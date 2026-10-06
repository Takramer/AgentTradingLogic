"""Replay the shipped paper rule on public Polymarket history.

This is not rhengine and not book.py. It walks CLOB UP prints for each
completed window, prices DOWN as 1 - UP, and calls the same entry and exit
rules as the live paper bot.

Two settlements are reported:

- bot: the video rule. Stop and take-profit fill at the sampled price.
  A hold pays 1 when the last mark with more than 30 seconds left is
  at least 0.50, otherwise 0.
- paid: stop and take-profit fill at that same sampled price. A hold pays
  the Gamma outcome (outcomePrices on the Up token).

Prints inside the last 30 seconds are ignored. The live runner drops those
snapshots, so it cannot stop, take profit, or enter there. Minute prints
miss moves between samples. Delta is treated as passed, which matches the
shipped Delta of 0 and does not replay the underlying. No taker fee is charged.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from polymarket_paper.rules import (
    Account,
    cash,
    entry_decision,
    exit_decision,
    fmt_pnl,
    in_entry_window,
    resolution_price,
)
from polymarket_paper.settings import (
    SLUG_PREFIX,
    TIMEFRAMES,
    default_config_path,
    load_raw,
    settings_from,
)

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
CLOB_HISTORY = "https://clob.polymarket.com/prices-history"
BATCH = 40
# The live snapshot is refused when less than 0.5 minutes remain.
CUTOFF_SEC = 30
HORIZONS = ((30, "1 month"), (90, "3 months"), (180, "6 months"))

_HTTP = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}
_THREAD = threading.local()


def _thread_session() -> requests.Session:
    sess = getattr(_THREAD, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update(_HTTP)
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4)
        sess.mount("https://", adapter)
        _THREAD.session = sess
    return sess


@dataclass
class WindowReplay:
    status: str
    points: int
    start: int | None = None
    side: str | None = None
    entry: float | None = None
    entry_ts: int | None = None
    exit_ts: int | None = None
    bot_exit: float | None = None
    paid_exit: float | None = None
    reason: str | None = None
    bot_pnl: float | None = None
    paid_pnl: float | None = None


def completed_starts(now: float, n: int, seconds: int) -> list[int]:
    """Start times of the last n windows whose end is at or before `now`."""
    if n < 1:
        raise ValueError("n must be >= 1")
    if seconds < 1:
        raise ValueError("seconds must be >= 1")
    last = int(now // seconds) * seconds - seconds
    first = last - (n - 1) * seconds
    return list(range(first, last + seconds, seconds))


def horizons_for(days: int) -> list[tuple[int, str]]:
    chosen = [(d, label) for d, label in HORIZONS if d <= days]
    if days not in {d for d, _ in chosen}:
        chosen.append((days, f"{days} days"))
    return chosen


def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_history(body) -> list[tuple[int, float]]:
    points = body.get("history", []) if isinstance(body, dict) else body
    out: list[tuple[int, float]] = []
    if not isinstance(points, list):
        return out
    for pt in points:
        if not isinstance(pt, dict):
            continue
        raw_t, raw_p = pt.get("t"), pt.get("p")
        if raw_t is None or raw_p is None:
            continue
        try:
            ts = int(raw_t)
            price = float(raw_p)
        except (TypeError, ValueError):
            continue
        if ts > 10**12:
            ts //= 1000
        out.append((ts, price))
    out.sort()
    return out


def parse_gamma_batch(body) -> dict[str, dict]:
    rows = body if isinstance(body, list) else []
    found: dict[str, dict] = {}
    for market in rows:
        if not isinstance(market, dict):
            continue
        slug = str(market.get("slug") or "")
        if not slug:
            continue
        names = [str(name) for name in _as_list(market.get("outcomes"))]
        tokens = [str(token) for token in _as_list(market.get("clobTokenIds"))]
        prices = _as_list(market.get("outcomePrices"))
        up_i = 0
        for i, name in enumerate(names):
            if name.lower() == "up":
                up_i = i
                break
        if up_i >= len(tokens) or not tokens[up_i]:
            continue
        up_outcome = None
        up_won = None
        if up_i < len(prices):
            try:
                up_outcome = float(prices[up_i])
                up_won = up_outcome >= 0.5
            except (TypeError, ValueError):
                up_outcome = None
                up_won = None
        found[slug] = {
            "up_token": tokens[up_i],
            "up_won": up_won,
            "up_outcome": up_outcome,
            "closed": bool(market.get("closed")),
        }
    return found


def _paid_hold(side: str, up_won) -> float | None:
    if up_won is None:
        return None
    won = (side == "UP") == bool(up_won)
    return 1.0 if won else 0.0


def _finish(status, points, start, side, entry, entry_ts, exit_ts, bot_exit, paid_exit, reason, size):
    bot_pnl = None if bot_exit is None else cash((bot_exit - entry) * size)
    paid_pnl = None if paid_exit is None else cash((paid_exit - entry) * size)
    return WindowReplay(
        status=status,
        points=points,
        start=start,
        side=side,
        entry=entry,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        bot_exit=bot_exit,
        paid_exit=paid_exit,
        reason=reason,
        bot_pnl=bot_pnl,
        paid_pnl=paid_pnl,
    )


def replay_window(points, *, start: int, end: int, up_won, settings, live_delta: float = 0.0,
                  stop_price: float | None = None, take_profit_pts: float | None = None) -> WindowReplay:
    """One window. `points` are (unix_sec, UP price). Empty input is missing.

    `stop_price` and `take_profit_pts` override the shipped knobs for a
    pre-registered ablation. The default path uses `settings` unchanged.
    A stop_price below 0 never fires, because contract prices sit in [0, 1].
    """
    stop = settings.exit if stop_price is None else float(stop_price)
    tp_pts = settings.take_profit_pts if take_profit_pts is None else float(take_profit_pts)
    raw = []
    for ts, price in points or []:
        try:
            ts_i = int(ts)
            px = float(price)
        except (TypeError, ValueError):
            continue
        raw.append((ts_i, px))
    raw.sort()
    if not raw:
        return WindowReplay(status="missing", points=0, start=start)

    usable = [
        (ts, px)
        for ts, px in raw
        if start <= ts <= end - CUTOFF_SEC and 0.0 <= px <= 1.0
    ]
    if not usable:
        return WindowReplay(status="flat", points=len(raw), start=start)

    side = None
    entry = None
    target = None
    entry_ts = None
    last_mark = None
    for ts, up in usable:
        down = 1.0 - up
        if side is None:
            minutes_left = (end - ts) / 60.0
            if not in_entry_window(minutes_left, settings.time_delay):
                continue
            chosen, _reason = entry_decision(
                up, down, settings.entry_min, settings.entry_max,
                live_delta, settings.delta,
            )
            if chosen is None:
                continue
            side, entry = chosen
            target = cash(entry + tp_pts)
            entry_ts = ts
            last_mark = entry
            continue
        current = up if side == "UP" else down
        last_mark = current
        decision = exit_decision(current, stop, target)
        if decision is None:
            continue
        return _finish(
            "trade", len(raw), start, side, entry, entry_ts, ts,
            current, current, "stop" if decision == "stop" else "take_profit",
            settings.order_size,
        )

    if side is None:
        return WindowReplay(status="flat", points=len(raw), start=start)
    bot_exit = resolution_price(last_mark)
    paid_exit = _paid_hold(side, up_won)
    return _finish(
        "trade", len(raw), start, side, entry, entry_ts, None,
        bot_exit, paid_exit, "held", settings.order_size,
    )


def naive_t(pnls: list[float]) -> float | None:
    """mean / (sample sd / sqrt(n)). Trades are not independent; this is not a research t."""
    n = len(pnls)
    if n < 2:
        return None
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    if var <= 0:
        return None
    return mean / (math.sqrt(var) / math.sqrt(n))


def max_drop(pnls: list[float], initial: float) -> float:
    equity = float(initial)
    peak = equity
    worst = 0.0
    for pnl in pnls:
        equity = cash(equity + pnl)
        if equity > peak:
            peak = equity
        drop = cash(peak - equity)
        if drop > worst:
            worst = drop
    return worst


def _walk_account(trades: list[WindowReplay], initial: float, size: float, attr: str):
    account = Account(initial, initial)
    halted = False
    for trade in trades:
        exit_price = getattr(trade, attr)
        if exit_price is None:
            continue
        ok, _cost = account.buy(trade.entry, size)
        if not ok:
            halted = True
            break
        account.close(
            "BTC", trade.side, trade.entry, exit_price, size,
            trade.reason or "", float(trade.entry_ts or trade.start or 0),
        )
    return account, halted


def _column(trades: list[WindowReplay], initial: float, size: float, attr: str, pnl_attr: str) -> dict:
    pnls = [getattr(t, pnl_attr) for t in trades if getattr(t, pnl_attr) is not None]
    unknown = sum(1 for t in trades if getattr(t, pnl_attr) is None)
    account, halted = _walk_account(trades, initial, size, attr)
    pnl_sum = cash(sum(pnls)) if pnls else 0.0
    return {
        "pnl_sum": pnl_sum,
        "final_balance": account.balance,
        "halted": halted,
        "filled": account.trades,
        "unknown": unknown,
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p <= 0),
        "avg_pnl": None if not pnls else cash(pnl_sum / len(pnls)),
        "max_drop": max_drop(pnls, initial),
        "naive_t": naive_t(pnls),
    }


def summarize_horizon(windows: list[dict], *, settings, days: int, label: str) -> dict:
    ordered = sorted(windows, key=lambda row: row["start"])
    trades = [
        row["replay"]
        for row in ordered
        if row.get("replay") is not None and row["replay"].status == "trade"
    ]
    path_points = [row["points"] for row in ordered if not row.get("missing")]
    exits = {"stop": 0, "take_profit": 0, "held": 0}
    hold_disagree = 0
    for trade in trades:
        if trade.reason in exits:
            exits[trade.reason] += 1
        if (
            trade.reason == "held"
            and trade.paid_exit is not None
            and trade.bot_exit != trade.paid_exit
        ):
            hold_disagree += 1
    months: dict[str, dict] = {}
    for row in ordered:
        key = datetime.fromtimestamp(row["start"], timezone.utc).strftime("%Y-%m")
        bucket = months.setdefault(key, {
            "windows": 0, "missing": 0, "trades": 0, "bot_pnl": 0.0, "paid_pnl": 0.0,
        })
        bucket["windows"] += 1
        if row.get("missing"):
            bucket["missing"] += 1
        replay = row.get("replay")
        if replay is not None and replay.status == "trade":
            bucket["trades"] += 1
            bucket["bot_pnl"] = cash(bucket["bot_pnl"] + (replay.bot_pnl or 0.0))
            if replay.paid_pnl is not None:
                bucket["paid_pnl"] = cash(bucket["paid_pnl"] + replay.paid_pnl)
    missing = sum(1 for row in ordered if row.get("missing"))
    flat = sum(
        1 for row in ordered
        if not row.get("missing") and (row.get("replay") is None or row["replay"].status != "trade")
    )
    span = None
    if ordered:
        span = {
            "first": datetime.fromtimestamp(ordered[0]["start"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "last": datetime.fromtimestamp(ordered[-1]["start"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
    return {
        "days": days,
        "label": label,
        "windows": len(ordered),
        "with_path": len(ordered) - missing,
        "missing": missing,
        "flat": flat,
        "trades": len(trades),
        "median_points": None if not path_points else statistics.median(path_points),
        "exits": exits,
        "hold_disagree": hold_disagree,
        "bot": _column(trades, settings.initial_balance, settings.order_size, "bot_exit", "bot_pnl"),
        "paid": _column(trades, settings.initial_balance, settings.order_size, "paid_exit", "paid_pnl"),
        "months": [{"month": key, **months[key]} for key in sorted(months)],
        "span": span,
    }


def _fmt_t(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_report(payload: dict) -> str:
    lines = [
        (
            f"{payload['asset']} {payload['timeframe']}"
            f"  entry [{payload['entry_min']:.2f}, {payload['entry_max']:.2f}]"
            f"  stop {payload['exit']:.2f}"
            f"  tp +{payload['take_profit']:.2f}"
            f"  size {payload['order_size']:g}"
            f"  start {payload['initial_balance']:.2f}"
        ),
        (
            "Paper replay of public CLOB prints. Not a book.py result. "
            "DOWN = 1 - UP. Delta treated as passed. No taker fee. "
            "Last 30s ignored. naive t treats each trade as independent."
        ),
    ]
    if payload.get("fetch_errors"):
        lines.append(f"Fetch errors: {payload['fetch_errors']} windows left out.")
    for horizon in payload["horizons"]:
        bot = horizon["bot"]
        paid = horizon["paid"]
        exits = horizon["exits"]
        span = horizon.get("span") or {}
        lines.append(
            f"{horizon['label']} ({horizon['days']}d)"
            f"  {span.get('first', '')} -> {span.get('last', '')}".rstrip()
        )
        med = horizon["median_points"]
        med_txt = "n/a" if med is None else f"{med:g}"
        lines.append(
            f"  windows {horizon['windows']}"
            f"  path {horizon['with_path']}"
            f"  missing {horizon['missing']}"
            f"  flat {horizon['flat']}"
            f"  trades {horizon['trades']}"
            f"  median prints {med_txt}"
        )
        lines.append(
            f"  exits stop {exits['stop']}"
            f"  tp {exits['take_profit']}"
            f"  hold {exits['held']}"
            f"  hold disagree {horizon['hold_disagree']}"
        )
        for name, col in (("bot", bot), ("paid", paid)):
            avg = "n/a" if col["avg_pnl"] is None else fmt_pnl(col["avg_pnl"])
            halted = "  HALTED" if col["halted"] else ""
            lines.append(
                f"  {name:4} pnl {fmt_pnl(col['pnl_sum'])}"
                f"  balance {col['final_balance']:.2f}"
                f"  wins {col['wins']}/{col['wins'] + col['losses']}"
                f"  avg {avg}"
                f"  max drop {col['max_drop']:.2f}"
                f"  naive t {_fmt_t(col['naive_t'])}"
                f"{halted}"
            )
        if horizon["days"] == payload["horizons"][-1]["days"] and horizon["months"]:
            lines.append("  months (bot pnl / paid pnl)")
            for month in horizon["months"]:
                lines.append(
                    f"    {month['month']}"
                    f"  trades {month['trades']}"
                    f"  missing {month['missing']}/{month['windows']}"
                    f"  bot {fmt_pnl(month['bot_pnl'])}"
                    f"  paid {fmt_pnl(month['paid_pnl'])}"
                )
    return "\n".join(lines)


def read_cache(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    if "start" not in data or "missing" not in data:
        return None
    return data


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _request_json(session: requests.Session, url: str, params) -> object:
    last = None
    for attempt in range(5):
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.4 * (2 ** attempt))
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            last = response.status_code
            time.sleep(0.4 * (2 ** attempt))
            continue
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code} for {url}")
        return response.json()
    raise RuntimeError(f"GET failed {url}: {last}")


def _cache_path(cache_dir: Path, asset: str, timeframe: str, start: int) -> Path:
    return cache_dir / f"{asset.lower()}-{timeframe.lower()}" / f"{start}.json"


def fill_cache(starts: list[int], *, asset: str, timeframe: str, cache_dir: Path,
               workers: int, offline: bool) -> int:
    """Fetch any window that is not already cached. Returns the error count."""
    prefix = SLUG_PREFIX[timeframe][asset]
    seconds = TIMEFRAMES[timeframe]["seconds"]
    needed = [
        start for start in starts
        if read_cache(_cache_path(cache_dir, asset, timeframe, start)) is None
    ]
    print(
        f"{asset} {timeframe}: {len(starts) - len(needed)} cached, {len(needed)} to fetch",
        file=sys.stderr, flush=True,
    )
    if not needed:
        return 0
    if offline:
        raise SystemExit(f"{len(needed)} windows are not in the cache")

    errors = 0
    total = len(needed)
    done = 0

    def _one(job):
        start, slug, row, path = job
        end = start + seconds
        body = _request_json(_thread_session(), CLOB_HISTORY, {
            "market": row["up_token"],
            "startTs": start,
            "endTs": end,
            "fidelity": 1,
        })
        hist = parse_history(body)
        _atomic_write(path, {
            "v": 1,
            "start": start,
            "slug": slug,
            "closed": True,
            "up_token": row["up_token"],
            "up_won": row["up_won"],
            "up_outcome": row["up_outcome"],
            "history": [[ts, price] for ts, price in hist],
            "missing": not hist,
            "reason": "" if hist else "no_history",
        })

    workers_n = max(1, workers)
    with ThreadPoolExecutor(max_workers=workers_n) as pool:
        for offset in range(0, total, BATCH):
            batch = needed[offset:offset + BATCH]
            slugs = [f"{prefix}-{start}" for start in batch]
            params = [("closed", "true"), ("limit", str(len(slugs)))]
            params += [("slug", slug) for slug in slugs]
            found = parse_gamma_batch(_request_json(_thread_session(), GAMMA_MARKETS, params))
            if len(batch) >= 10 and len(found) < len(batch) // 2:
                raise RuntimeError(
                    f"Gamma returned {len(found)} of {len(batch)} slugs. "
                    "Refusing to mark the rest as missing."
                )
            jobs = []
            for start, slug in zip(batch, slugs):
                path = _cache_path(cache_dir, asset, timeframe, start)
                row = found.get(slug)
                if row is None:
                    _atomic_write(path, {
                        "v": 1, "start": start, "slug": slug, "missing": True,
                        "reason": "no_market", "history": [],
                    })
                    continue
                if not row["closed"]:
                    _atomic_write(path, {
                        "v": 1, "start": start, "slug": slug, "missing": True,
                        "reason": "not_closed", "closed": False, "history": [],
                        "up_token": row["up_token"], "up_won": row["up_won"],
                    })
                    continue
                jobs.append((start, slug, row, path))
            futures = {pool.submit(_one, job): job[0] for job in jobs}
            for future in as_completed(futures):
                start = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    errors += 1
                    print(f"history {prefix}-{start} failed: {exc}", file=sys.stderr, flush=True)
            done += len(batch)
            print(f"fetched {done}/{total}", file=sys.stderr, flush=True)
    return errors


def windows_from_cache(starts: list[int], *, asset: str, timeframe: str,
                       cache_dir: Path, settings) -> tuple[list[dict], int]:
    seconds = TIMEFRAMES[timeframe]["seconds"]
    rows = []
    errors = 0
    for start in starts:
        cached = read_cache(_cache_path(cache_dir, asset, timeframe, start))
        if cached is None:
            errors += 1
            rows.append({
                "start": start, "missing": True, "points": 0, "replay": None,
                "reason": "fetch_error",
            })
            continue
        if cached.get("missing") or not cached.get("history"):
            rows.append({
                "start": start, "missing": True, "points": len(cached.get("history") or []),
                "replay": None, "reason": cached.get("reason") or "missing",
            })
            continue
        replay = replay_window(
            cached["history"],
            start=start,
            end=start + seconds,
            up_won=cached.get("up_won"),
            settings=settings,
        )
        missing = replay.status == "missing"
        rows.append({
            "start": start,
            "missing": missing,
            "points": replay.points,
            "replay": None if missing else replay,
            "reason": cached.get("reason") or "",
        })
    return rows, errors


def build_report(windows: list[dict], *, settings, asset: str, days: int,
                 fetch_errors: int, now: float) -> dict:
    seconds = settings.timeframe_seconds
    per_day = 86400 // seconds
    horizons = []
    for horizon_days, label in horizons_for(days):
        n = horizon_days * per_day
        horizons.append(summarize_horizon(
            windows[-n:], settings=settings, days=horizon_days, label=label,
        ))
    return {
        "asset": asset,
        "timeframe": settings.timeframe,
        "fingerprint": settings.fingerprint(),
        "now": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "entry_min": settings.entry_min,
        "entry_max": settings.entry_max,
        "exit": settings.exit,
        "take_profit": settings.take_profit_pts,
        "order_size": settings.order_size,
        "initial_balance": settings.initial_balance,
        "delta": settings.delta,
        "fetch_errors": fetch_errors,
        "assumptions": [
            "DOWN price is 1 - UP",
            "Delta is treated as passed",
            "no taker fee",
            "prints with less than 30 seconds left are ignored",
            "bot settlement uses the last eligible mark",
            "paid settlement uses the Gamma Up outcome on holds",
            "stop and take-profit fill at the sampled price in both columns",
            "30/90/180 day slices are one trial, not three",
        ],
        "horizons": horizons,
    }


def default_cache_dir() -> Path:
    return Path(__file__).resolve().parent / "cache"


def run(settings, *, days: int, workers: int, cache_dir: Path, offline: bool, now: float | None = None) -> dict:
    if settings.timeframe == "1H":
        raise SystemExit("Replay covers 5M and 15M unix windows. 1H uses a different slug.")
    if settings.delta != 0:
        raise SystemExit("Replay assumes Delta is 0. Historical underlying prints are not on this path.")
    if days < 1:
        raise SystemExit("days must be >= 1")
    now = time.time() if now is None else now
    seconds = settings.timeframe_seconds
    n = days * (86400 // seconds)
    starts = completed_starts(now, n, seconds)
    fetch_errors = 0
    per_asset = []
    for asset in settings.assets:
        if asset not in SLUG_PREFIX[settings.timeframe]:
            raise SystemExit(f"No slug for {asset} {settings.timeframe}")
        fill_cache(
            starts, asset=asset, timeframe=settings.timeframe, cache_dir=cache_dir,
            workers=workers, offline=offline,
        )
        windows, load_errors = windows_from_cache(
            starts, asset=asset, timeframe=settings.timeframe,
            cache_dir=cache_dir, settings=settings,
        )
        report = build_report(
            windows, settings=settings, asset=asset, days=days,
            fetch_errors=load_errors, now=now,
        )
        fetch_errors += report["fetch_errors"]
        per_asset.append(report)
    if len(per_asset) == 1:
        return per_asset[0]
    return {"assets": per_asset, "fetch_errors": fetch_errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Replay the paper up/down rule on public CLOB history.")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    raw = load_raw(args.config or default_config_path())
    settings = settings_from(raw)
    cache_dir = args.cache or default_cache_dir()
    payload = run(
        settings, days=args.days, workers=args.workers,
        cache_dir=cache_dir, offline=args.offline,
    )
    reports = payload["assets"] if "assets" in payload else [payload]
    for report in reports:
        print(format_report(report))
        print()
    out = cache_dir / "replay-summary.json"
    _atomic_write(out, payload if "assets" in payload else reports[0])
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
