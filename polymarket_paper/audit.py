"""Explain the shipped BTC 15m paper loss and score a fixed ablation list.

The list and the keep rule are the source of the trial-ledger rows. Run
``python3 -m polymarket_paper.audit --log-trials`` before
``python3 -m polymarket_paper.audit``. The second command refuses to score
the cache until those labels are in the ledger.

A variant is kept only when its fee-adjusted paid pnl is positive on the
full 180-day path and on each 90-day half. A keep does not edit
``config.json``. Research claims stay in ``rhengine.alpha.book``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from polymarket_paper.backtest import (
    default_cache_dir,
    read_cache,
    replay_window,
)
from polymarket_paper.rules import cash
from polymarket_paper.settings import TIMEFRAMES, default_config_path, load_raw, settings_from

# docs.polymarket.com/trading/fees crypto category, and the feeSchedule on
# the BTC 15m markets in this cache: exponent 1, rate 0.07, taker only.
FEE_RATE = 0.07
FEE_EXPONENT = 1
FEE_LABEL = "BTC 15m shipped rule with crypto taker fee 0.07"
BOOT_BLOCK = 96
BOOT_DRAWS = 1000
BOOT_SEED = 20261006
HALF = 8640

VARIANTS = (
    {
        "label": "BTC 15m ablation: same entries, hold to Gamma",
        "kind": "structural",
        "stop_price": -1.0,
        "take_profit_pts": 1.0,
    },
    {
        "label": "BTC 15m ablation: stop 0.40, no take-profit",
        "kind": "structural",
        "take_profit_pts": 1.0,
    },
    {
        "label": "BTC 15m ablation: take-profit +0.10, no stop",
        "kind": "structural",
        "stop_price": -1.0,
    },
    {
        "label": "BTC 15m ablation: timeDelay 5",
        "kind": "timing",
        "time_delay": 5,
    },
    {
        "label": "BTC 15m ablation: timeDelay 2",
        "kind": "timing",
        "time_delay": 2,
    },
    {
        "label": "BTC 15m ablation: entry [0.65, 0.75]",
        "kind": "threshold",
        "entry_min": 0.65,
        "entry_max": 0.75,
    },
    {
        "label": "BTC 15m ablation: entry [0.55, 0.65]",
        "kind": "threshold",
        "entry_min": 0.55,
        "entry_max": 0.65,
    },
    {
        "label": "BTC 15m ablation: stop 0.55",
        "kind": "threshold",
        "exit": 0.55,
    },
    {
        "label": "BTC 15m ablation: take-profit +0.20",
        "kind": "threshold",
        "take_profit_pts": 0.20,
    },
)

NOTE = (
    "Logged BEFORE the run. One knob off the shipped BTC 15m rule "
    "(fingerprint {fp}). Same 17280 windows as polymarket_paper/REPLAY.md. "
    "Paid pnl uses Gamma settlement. Net pnl subtracts taker fee "
    "C*0.07*p*(1-p) on the entry and on a stop or take-profit exit, "
    "rounded half-up to 5 decimals, minimum charge 0.00001 else 0. "
    "Holds pay the entry fee only. First half is the earliest 8640 windows; "
    "second half is the latest 8640. Keep only if net pnl > 0 on the full "
    "path and on both halves. A keep does not edit config.json and is not "
    "a book.py result. Otherwise rejected."
)


def trial_labels() -> list[str]:
    return [row["label"] for row in VARIANTS] + [FEE_LABEL]


def taker_fee(shares: float, price: float, rate: float = FEE_RATE, exponent: int = FEE_EXPONENT) -> float:
    """USDC taker fee. Matches the crypto table: 100 shares at 0.70 cost 1.47."""
    p = round(float(price), 6)
    if p <= 0 or p >= 1:
        return 0.0
    raw = (Decimal(str(shares)) * Decimal(str(rate)) * (Decimal(str(p)) * (1 - Decimal(str(p)))) ** exponent)
    if raw < Decimal("0.00001"):
        return 0.0
    rounded = raw.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    return float(rounded)


def trade_fee(trade, size: float) -> float:
    fee = taker_fee(size, trade.entry)
    if trade.reason != "held":
        fee += taker_fee(size, trade.paid_exit)
    return fee


def net_paid(trade, size: float) -> float:
    return cash(trade.paid_pnl - trade_fee(trade, size))


def entry_bucket(price: float) -> str:
    p = float(price)
    edges = (0.60, 0.65, 0.70, 0.75, 0.80)
    for i, lo in enumerate(edges[:-1]):
        hi = edges[i + 1]
        last = i == len(edges) - 2
        if lo <= p < hi or (last and p == hi):
            return f"{lo:.2f}-{hi:.2f}"
    return "outside"


def time_bucket(minutes_left: float) -> str:
    if minutes_left > 10:
        return ">10m"
    if minutes_left > 5:
        return "5-10m"
    return "<=5m"


def classify_exit(base, hold) -> str:
    won = hold.paid_exit == 1
    if base.reason == "stop":
        return "stop_whipsaw" if won else "stop_helped"
    if base.reason == "take_profit":
        return "tp_capped" if won else "tp_saved"
    return "held"


def pair_trades(base, hold) -> list[tuple]:
    hold_by = {trade.start: trade for trade in hold}
    pairs = []
    for trade in base:
        other = hold_by.get(trade.start)
        if other is None or other.side != trade.side or other.entry != trade.entry:
            raise ValueError(f"hold path does not match the shipped entry at {trade.start}")
        pairs.append((trade, other))
    if len(pairs) != len(hold):
        raise ValueError("hold path traded a window the shipped rule skipped")
    return pairs


def _blank_cell() -> dict:
    return {"n": 0, "paid": 0.0, "hold": 0.0}


def summarize_overlay(pairs, *, window_seconds: int, size: float) -> dict:
    cells = {name: _blank_cell() for name in (
        "stop_helped", "stop_whipsaw", "tp_capped", "tp_saved", "held",
    )}
    by_price: dict[str, dict] = {}
    by_time: dict[str, dict] = {}
    paid = 0.0
    hold_sum = 0.0
    for base, other in pairs:
        kind = classify_exit(base, other)
        cell = cells[kind]
        cell["n"] += 1
        cell["paid"] = cash(cell["paid"] + base.paid_pnl)
        cell["hold"] = cash(cell["hold"] + other.paid_pnl)
        paid = cash(paid + base.paid_pnl)
        hold_sum = cash(hold_sum + other.paid_pnl)
        minutes_left = (base.start + window_seconds - base.entry_ts) / 60.0
        for key, table in (
            (entry_bucket(base.entry), by_price),
            (time_bucket(minutes_left), by_time),
        ):
            bucket = table.setdefault(key, {"n": 0, "wins": 0, "entry": 0.0, "paid": 0.0, "hold": 0.0})
            bucket["n"] += 1
            bucket["wins"] += 1 if other.paid_exit == 1 else 0
            bucket["entry"] = cash(bucket["entry"] + base.entry)
            bucket["paid"] = cash(bucket["paid"] + base.paid_pnl)
            bucket["hold"] = cash(bucket["hold"] + other.paid_pnl)
    def _rows(table):
        out = []
        for key in sorted(table):
            bucket = table[key]
            n = bucket["n"]
            out.append({
                "bucket": key,
                "n": n,
                "win_rate": None if n == 0 else bucket["wins"] / n,
                "mean_entry": None if n == 0 else bucket["entry"] / n,
                "paid": bucket["paid"],
                "hold": bucket["hold"],
                "hold_edge_per_share": None if n == 0 else (bucket["hold"] / n) / size,
            })
        return out
    return {
        "trades": len(pairs),
        "paid": paid,
        "hold": hold_sum,
        "exit_drag": cash(paid - hold_sum),
        "cells": cells,
        "by_price": _rows(by_price),
        "by_time": _rows(by_time),
    }


def block_bootstrap_interval(values: list[float], block: int = BOOT_BLOCK,
                             draws: int = BOOT_DRAWS, seed: int = BOOT_SEED) -> dict:
    """Percentile interval of the sum, resampling whole blocks with replacement."""
    if block < 1 or len(values) % block != 0:
        raise ValueError("block must divide the series")
    nblocks = len(values) // block
    rng = random.Random(seed)
    sums = []
    for _ in range(draws):
        total = 0.0
        for _block in range(nblocks):
            start = rng.randrange(nblocks) * block
            total += sum(values[start:start + block])
        sums.append(total)
    sums.sort()
    return {
        "block": block,
        "draws": draws,
        "seed": seed,
        "lo": sums[int(0.025 * (draws - 1))],
        "hi": sums[int(0.975 * (draws - 1))],
        "observed": sum(values),
    }


def _settings_for(base, spec: dict):
    fields = {}
    for name in ("time_delay", "entry_min", "entry_max", "exit", "take_profit_pts"):
        if name in spec:
            fields[name] = spec[name]
    return replace(base, **fields) if fields else base


def replay_row(row: dict, settings, *, window_seconds: int, stop_price=None, take_profit_pts=None):
    if row.get("missing") or not row.get("history"):
        return None
    return replay_window(
        row["history"],
        start=int(row["start"]),
        end=int(row["start"]) + window_seconds,
        up_won=row.get("up_won"),
        settings=settings,
        stop_price=stop_price,
        take_profit_pts=take_profit_pts,
    )


def collect(rows: list[dict], settings, *, window_seconds: int, stop_price=None, take_profit_pts=None):
    """One slot per window. None means that window did not trade."""
    slots = []
    for row in rows:
        replay = replay_row(
            row, settings, window_seconds=window_seconds,
            stop_price=stop_price, take_profit_pts=take_profit_pts,
        )
        if replay is None or replay.status != "trade":
            slots.append(None)
        else:
            slots.append(replay)
    return slots


def score_slots(slots, *, size: float, half: int) -> dict:
    trades = [slot for slot in slots if slot is not None]
    paid_values = [0.0 if slot is None else slot.paid_pnl for slot in slots]
    net_values = [0.0 if slot is None else net_paid(slot, size) for slot in slots]
    paid = cash(sum(paid_values))
    net = cash(sum(net_values))
    net_h1 = cash(sum(net_values[:half]))
    net_h2 = cash(sum(net_values[half:]))
    return {
        "trades": len(trades),
        "paid": paid,
        "net": net,
        "gross_h1": cash(sum(paid_values[:half])),
        "gross_h2": cash(sum(paid_values[half:])),
        "net_h1": net_h1,
        "net_h2": net_h2,
        "kept": net > 0 and net_h1 > 0 and net_h2 > 0,
        "fees": cash(paid - net),
    }


def load_rows(cache_dir: Path, asset: str, timeframe: str) -> list[dict]:
    folder = cache_dir / f"{asset.lower()}-{timeframe.lower()}"
    paths = sorted(folder.glob("*.json"), key=lambda path: int(path.stem))
    rows = []
    for path in paths:
        cached = read_cache(path)
        if cached is None:
            continue
        rows.append(cached)
    return rows


def assert_recorded_window(rows: list[dict]) -> None:
    if len(rows) != HALF * 2:
        raise SystemExit(f"cache has {len(rows)} windows; the recorded replay used {HALF * 2}")
    first = datetime.fromtimestamp(int(rows[0]["start"]), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last = datetime.fromtimestamp(int(rows[-1]["start"]), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if first != "2026-04-09 21:15 UTC" or last != "2026-10-06 21:00 UTC":
        raise SystemExit(f"cache span is {first} -> {last}, not the recorded replay")


def ledger_path() -> Path:
    return Path(__file__).resolve().parents[1] / "research_data" / "trials.jsonl"


def logged_labels(path: Path | None = None) -> set[str]:
    path = path or ledger_path()
    found = set()
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        found.add(json.loads(line)["label"])
    return found


def log_trials(settings, path: Path | None = None) -> list[str]:
    path = path or ledger_path()
    existing = logged_labels(path)
    wrote = []
    note = NOTE.format(fp=settings.fingerprint())
    rows = []
    for spec in VARIANTS:
        rows.append({
            "family": "polymarket-updown",
            "label": spec["label"],
            "kind": spec["kind"],
            "outcome": "inconclusive",
            "source": "polymarket_paper/AUDIT.md",
            "date": "2026-10-06",
            "config_hash": settings.fingerprint(),
            "note": note,
            "resolves": "",
            "n": 1,
        })
    rows.append({
        "family": "polymarket-updown",
        "label": FEE_LABEL,
        "kind": "cost",
        "outcome": "inconclusive",
        "source": "polymarket_paper/AUDIT.md",
        "date": "2026-10-06",
        "config_hash": settings.fingerprint(),
        "note": (
            "Logged BEFORE the fee number is read. Shipped exits, same 17280 "
            "windows. Fee is the crypto schedule on the markets themselves: "
            "C*0.07*p*(1-p), exponent 1, taker only, half-up to 5 decimals, "
            "minimum 0.00001 else 0. Charged on entry and on a stop or "
            "take-profit exit. This row measures the drag. It is not a new "
            "entry rule. Not a book.py result."
        ),
        "resolves": "",
        "n": 1,
    })
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            if row["label"] in existing:
                continue
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            wrote.append(row["label"])
    return wrote


def run_audit(settings, rows: list[dict]) -> dict:
    seconds = TIMEFRAMES[settings.timeframe]["seconds"]
    base_slots = collect(rows, settings, window_seconds=seconds)
    hold_spec = VARIANTS[0]
    hold_slots = collect(
        rows, _settings_for(settings, hold_spec), window_seconds=seconds,
        stop_price=hold_spec.get("stop_price"),
        take_profit_pts=hold_spec.get("take_profit_pts"),
    )
    overlay = summarize_overlay(
        pair_trades(
            [slot for slot in base_slots if slot is not None],
            [slot for slot in hold_slots if slot is not None],
        ),
        window_seconds=seconds,
        size=settings.order_size,
    )
    scored = []
    for spec in VARIANTS:
        slots = collect(
            rows, _settings_for(settings, spec), window_seconds=seconds,
            stop_price=spec.get("stop_price"),
            take_profit_pts=spec.get("take_profit_pts"),
        )
        row = score_slots(slots, size=settings.order_size, half=HALF)
        row["label"] = spec["label"]
        row["kind"] = spec["kind"]
        scored.append(row)
    shipped_net = score_slots(base_slots, size=settings.order_size, half=HALF)
    shipped_net["label"] = FEE_LABEL
    shipped_net["kind"] = "cost"
    shipped_net["kept"] = False
    return {
        "fingerprint": settings.fingerprint(),
        "windows": len(rows),
        "order_size": settings.order_size,
        "overlay": overlay,
        "bootstrap_gross": block_bootstrap_interval(
            [0.0 if slot is None else slot.paid_pnl for slot in base_slots]
        ),
        "bootstrap_net": block_bootstrap_interval(
            [0.0 if slot is None else net_paid(slot, settings.order_size) for slot in base_slots]
        ),
        "variants": scored,
        "shipped_net": shipped_net,
        "kept": [row["label"] for row in scored if row["kept"]],
    }


def format_report(payload: dict) -> str:
    overlay = payload["overlay"]
    lines = [
        "Shipped path versus holding the same entries to the Gamma outcome.",
        (
            f"  trades {overlay['trades']}  paid {overlay['paid']}  "
            f"hold {overlay['hold']}  exit drag {overlay['exit_drag']}"
        ),
    ]
    for name, cell in overlay["cells"].items():
        lines.append(f"  {name:14} n {cell['n']:5}  paid {cell['paid']}  hold {cell['hold']}")
    lines.append("Entry price (win rate is the Gamma win rate of that side)")
    for row in overlay["by_price"]:
        rate = "n/a" if row["win_rate"] is None else f"{row['win_rate']:.3f}"
        mean = "n/a" if row["mean_entry"] is None else f"{row['mean_entry']:.3f}"
        edge = "n/a" if row["hold_edge_per_share"] is None else f"{row['hold_edge_per_share']:.4f}"
        lines.append(
            f"  {row['bucket']:9} n {row['n']:5}  entry {mean}  win {rate}  "
            f"paid {row['paid']}  hold {row['hold']}  hold edge/share {edge}"
        )
    lines.append("Minutes left at entry")
    for row in overlay["by_time"]:
        rate = "n/a" if row["win_rate"] is None else f"{row['win_rate']:.3f}"
        lines.append(
            f"  {row['bucket']:6} n {row['n']:5}  win {rate}  paid {row['paid']}  hold {row['hold']}"
        )
    gross = payload["bootstrap_gross"]
    net = payload["bootstrap_net"]
    lines.append(
        f"Day-block bootstrap of the shipped paid sum: {gross['lo']:.2f} to {gross['hi']:.2f} "
        f"(observed {gross['observed']:.4f}, block {gross['block']}, draws {gross['draws']}, seed {gross['seed']})"
    )
    lines.append(
        f"Same bootstrap after the taker fee: {net['lo']:.2f} to {net['hi']:.2f} "
        f"(observed {net['observed']:.4f})"
    )
    lines.append("Ablations. Kept only if net > 0 on the full path and both halves.")
    for row in payload["variants"] + [payload["shipped_net"]]:
        flag = "KEEP" if row["kept"] else "reject"
        lines.append(
            f"  {flag:6} {row['label']}"
            f"  trades {row['trades']}"
            f"  paid {row['paid']}"
            f"  net {row['net']}"
            f"  net h1 {row['net_h1']}"
            f"  net h2 {row['net_h2']}"
            f"  fees {row['fees']}"
        )
    if payload["kept"]:
        lines.append("In-sample keeps, not a new default: " + "; ".join(payload["kept"]))
    else:
        lines.append("No variant cleared the keep rule. config.json stays on the shipped rule.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit the shipped paper rule and score the pre-registered ablations.")
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--log-trials", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    raw = load_raw(args.config or default_config_path())
    settings = settings_from(raw)
    if settings.timeframe != "15M" or settings.assets != ("BTC",):
        raise SystemExit("This audit is the recorded BTC 15m replay.")
    if args.log_trials:
        wrote = log_trials(settings)
        print(f"logged {len(wrote)} new trial rows")
        return 0
    missing = [label for label in trial_labels() if label not in logged_labels()]
    if missing:
        raise SystemExit("Log the trials first: python3 -m polymarket_paper.audit --log-trials")
    cache_dir = args.cache or default_cache_dir()
    rows = load_rows(cache_dir, "BTC", "15M")
    assert_recorded_window(rows)
    payload = run_audit(settings, rows)
    print(format_report(payload))
    out = args.json_out or (cache_dir / "audit-summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(out)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
