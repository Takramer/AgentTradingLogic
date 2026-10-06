"""
kalshi_audit.py — why the KXBTC15M paper replay loses, and the pre-registered
variants that were allowed to try to lose less.

The published rules stay in rhengine/kalshi_v5_paper.json. This module does
not edit them. A variant is eligible only when its touch-mode cash beats the
touch baseline on both halves of the already-downloaded history:

  screen   2026-04-09T21:15:00Z  through  2026-07-08T21:15:00Z
  confirm  2026-07-08T21:15:00Z  through  2026-10-06T21:15:00Z

The confirm half is the 90-day row already printed in docs/KALSHI-PAPER.md.
The screen half was inside the 180-day row and was not printed alone.
Eligibility also requires at least 100 trades on each half. Fee rows and the
stop-at-0.40 fill are sensitivities. They cannot be eligible.

Nothing here is an rhengine.alpha.book result. Trade-level t-stats are not
reported. A day-block resample says whether one calendar day carries the
total. It is not a Sharpe.
"""

import argparse
import math
import random
from dataclasses import replace
from datetime import datetime, timezone

from .kalshi_backtest import (
    DEFAULT_CACHE,
    load_candles,
    load_manifest,
    parse_time,
    replay_book,
    replay_market,
)
from .kalshi_paper import DEFAULT_CONFIG_PATH, format_money, format_pnl, load_config

# Frozen to the replay already published. Later settlements stay out.
END_TS = parse_time("2026-10-06T21:15:00Z")
SCREEN_START = END_TS - 180 * 86400
CONFIRM_START = END_TS - 90 * 86400

# Logged before the run. Labels match the trial ledger.
VARIANTS = (
    {
        "label": "KXBTC15M hold to settlement, no TP no stop",
        "cfg": {"exit": 0.0, "take_profit_pts": 1.0},
    },
    {
        "label": "KXBTC15M drop the stop, keep take-profit",
        "cfg": {"exit": 0.0},
    },
    {
        "label": "KXBTC15M drop the take-profit, keep stop",
        "cfg": {"take_profit_pts": 1.0},
    },
    {
        "label": "KXBTC15M entry band 0.75-0.80 only",
        "cfg": {"entry_min": 0.75, "entry_max": 0.80},
    },
    {
        "label": "KXBTC15M entry band 0.80-0.85 only",
        "cfg": {"entry_min": 0.80, "entry_max": 0.85},
    },
    {
        "label": "KXBTC15M timeDelay 3 minutes",
        "cfg": {"time_delay": 3.0},
    },
    {
        "label": "KXBTC15M UP side only",
        "sides": ("UP",),
    },
    {
        "label": "KXBTC15M DOWN side only",
        "sides": ("DOWN",),
    },
)

MIN_TRADES = 100


def kalshi_quadratic_fee(price, contracts=1, multiplier=1):
    """General Kalshi taker fee, rounded up to the next cent.

    fees = round_up(multiplier * 0.07 * C * P * (1-P)).
    KXBTC15M reports fee_type quadratic and fee_multiplier 1.
    One contract at 0.50 is 0.0175, which rounds up to 0.02.
    One hundred contracts at 0.50 are 1.75 exactly.
    A non-positive price or a price at 1 pays nothing.
    """
    p = float(format(float(price), ".4f"))
    c = float(contracts)
    m = float(multiplier)
    if c <= 0 or p <= 0 or p >= 1:
        return 0.0
    raw = m * 0.07 * c * p * (1.0 - p)
    cents = math.ceil(raw * 100.0 - 1e-9)
    return cents / 100.0


def trade_fees(trade, contracts=1, maker_take_profit=False):
    """Entry is a taker. Settlement has no second fee.

    A quadratic series does not charge a resting order. maker_take_profit
    treats the take-profit as that resting order and the stop as a taker.
    """
    fee = kalshi_quadratic_fee(trade["entry"], contracts)
    if trade["reason"] == "settlement":
        return fee
    if maker_take_profit and trade["reason"] == "take_profit":
        return fee
    return fee + kalshi_quadratic_fee(trade["exit"], contracts)


def payoff(trades):
    """Win rate against the rate this payoff needs in order to break even."""
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    n = len(trades)
    if n == 0:
        return {
            "n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
            "win_rate": None, "avg_win": None, "avg_loss": None,
            "breakeven_win_rate": None, "gap": None, "expectancy": None,
        }
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / n
    denom = avg_win + abs(avg_loss)
    breakeven = (abs(avg_loss) / denom) if denom else None
    gap = None if breakeven is None else win_rate - breakeven
    pnl = sum(t["pnl"] for t in trades)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "pnl": pnl,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "breakeven_win_rate": breakeven,
        "gap": gap,
        "expectancy": pnl / n,
    }


def settlement_of(trades):
    """Same entries, held to the contract result. Skips a missing result."""
    held = []
    for trade in trades:
        if trade.get("settle_pnl") is None:
            continue
        held.append({"pnl": trade["settle_pnl"], "won": trade["settle"] == 1.0})
    if not held:
        return {"n": 0, "pnl": 0.0, "win_rate": None}
    wins = sum(1 for row in held if row["won"])
    return {
        "n": len(held),
        "pnl": sum(row["pnl"] for row in held),
        "win_rate": wins / len(held),
    }


def bucket_rows(trades, key):
    groups = {}
    for trade in trades:
        groups.setdefault(key(trade), []).append(trade)
    rows = []
    for name in sorted(groups, key=lambda item: str(item)):
        stats = payoff(groups[name])
        held = settlement_of(groups[name])
        stats["bucket"] = name
        stats["settle_pnl"] = held["pnl"]
        stats["settle_win_rate"] = held["win_rate"]
        rows.append(stats)
    return rows


def entry_bin(trade):
    price = trade["entry"]
    if price < 0.78:
        return "0.75-0.78"
    if price < 0.82:
        return "0.78-0.82"
    return "0.82-0.85"


def minute_bin(trade):
    mins = trade.get("minutes_left")
    if mins is None:
        return "n/a"
    if mins <= 2:
        return "1-2m"
    if mins <= 5:
        return "3-5m"
    return "6-8m"


def day_block_pnl(trades, draws=2000, seed=20261006):
    """Resample calendar days. The band is the resampled total, not a Sharpe."""
    by_day = {}
    for trade in trades:
        day = (trade.get("close_time") or "")[:10]
        by_day[day] = by_day.get(day, 0.0) + trade["pnl"]
    days = list(by_day.values())
    if not days:
        return {"days": 0, "pnl": 0.0, "lo": 0.0, "hi": 0.0}
    rng = random.Random(seed)
    n = len(days)
    totals = []
    for _ in range(int(draws)):
        total = 0.0
        for _i in range(n):
            total += days[rng.randrange(n)]
        totals.append(total)
    totals.sort()
    lo_i = int(0.025 * len(totals))
    hi_i = min(len(totals) - 1, int(0.975 * len(totals)))
    return {
        "days": n,
        "pnl": sum(days),
        "lo": totals[lo_i],
        "hi": totals[hi_i],
    }


def iter_trades(markets, candles_for, cfg, mode, sides=("UP", "DOWN"), stop_fill="path"):
    trades = []
    for market in sorted(markets, key=lambda row: (row["close_ts"], row.get("ticker") or "")):
        candles = candles_for(market["ticker"])
        if not candles:
            continue
        outcome = replay_market(
            market, candles, cfg, mode, sides=sides, stop_fill=stop_fill)
        trade = outcome.get("trade")
        if trade:
            trades.append(trade)
    return trades


def window_markets(markets, start_ts, end_ts, end_inclusive=True):
    chosen = []
    for market in markets:
        ts = market["close_ts"]
        if ts < start_ts or ts > END_TS:
            continue
        if ts < start_ts:
            continue
        if end_inclusive:
            if start_ts <= ts <= end_ts:
                chosen.append(market)
        elif start_ts <= ts < end_ts:
            chosen.append(market)
    return chosen


def eligible(screen_pnl, confirm_pnl, screen_n, confirm_n, screen_base, confirm_base):
    """Both halves must beat the touch baseline, with a real sample on each."""
    if screen_n < MIN_TRADES or confirm_n < MIN_TRADES:
        return False
    return screen_pnl > screen_base and confirm_pnl > confirm_base


def run_variants(markets, candles_for, cfg, mode="touch"):
    """Screen then confirm. Baseline is the published rules in this mode."""
    screen = window_markets(markets, SCREEN_START, CONFIRM_START, end_inclusive=False)
    confirm = window_markets(markets, CONFIRM_START, END_TS, end_inclusive=True)
    base_s = replay_book(screen, candles_for, cfg, mode, kill=False)
    base_c = replay_book(confirm, candles_for, cfg, mode, kill=False)
    rows = [{
        "label": "published rules",
        "screen_pnl": base_s["pnl"],
        "screen_trades": base_s["trades"],
        "confirm_pnl": base_c["pnl"],
        "confirm_trades": base_c["trades"],
        "eligible": False,
        "sensitivity": False,
    }]
    for spec in VARIANTS:
        variant = replace(cfg, **spec.get("cfg", {}))
        sides = spec.get("sides", ("UP", "DOWN"))
        stop_fill = spec.get("stop_fill", "path")
        got_s = replay_book(
            screen, candles_for, variant, mode, kill=False,
            sides=sides, stop_fill=stop_fill)
        got_c = replay_book(
            confirm, candles_for, variant, mode, kill=False,
            sides=sides, stop_fill=stop_fill)
        rows.append({
            "label": spec["label"],
            "screen_pnl": got_s["pnl"],
            "screen_trades": got_s["trades"],
            "confirm_pnl": got_c["pnl"],
            "confirm_trades": got_c["trades"],
            "eligible": eligible(
                got_s["pnl"], got_c["pnl"], got_s["trades"], got_c["trades"],
                base_s["pnl"], base_c["pnl"]),
            "sensitivity": False,
        })
    level_s = replay_book(screen, candles_for, cfg, mode, kill=False, stop_fill="level")
    level_c = replay_book(confirm, candles_for, cfg, mode, kill=False, stop_fill="level")
    rows.append({
        "label": "KXBTC15M stop filled at 0.40 unless the bar gaps through",
        "screen_pnl": level_s["pnl"],
        "screen_trades": level_s["trades"],
        "confirm_pnl": level_c["pnl"],
        "confirm_trades": level_c["trades"],
        "eligible": False,
        "sensitivity": True,
    })
    return rows


def fee_summary(trades, contracts=1):
    taker = sum(trade_fees(t, contracts, maker_take_profit=False) for t in trades)
    maker_tp = sum(trade_fees(t, contracts, maker_take_profit=True) for t in trades)
    gross = sum(t["pnl"] for t in trades)
    return {
        "gross": gross,
        "taker_fees": taker,
        "taker_net": gross - taker,
        "resting_tp_fees": maker_tp,
        "resting_tp_net": gross - maker_tp,
    }


def _pct(value):
    if value is None:
        return "n/a"
    return "{:.1f}%".format(100.0 * value)


def _num(value):
    if value is None:
        return "n/a"
    return "{:+.4f}".format(value)


def render_payoff(title, trades):
    stats = payoff(trades)
    held = settlement_of(trades)
    band = day_block_pnl(trades)
    lines = [
        title,
        "  trades {}  W {}  L {}  pnl {}  per trade {}".format(
            stats["n"], stats["wins"], stats["losses"],
            format_pnl(stats["pnl"]), _num(stats["expectancy"])),
        "  win rate {}  breakeven {}  gap {}".format(
            _pct(stats["win_rate"]), _pct(stats["breakeven_win_rate"]),
            _pct(stats["gap"])),
        "  avg win {}  avg loss {}".format(_num(stats["avg_win"]), _num(stats["avg_loss"])),
        "  same entries held to settlement: n {}  pnl {}  settle-win {}".format(
            held["n"], format_pnl(held["pnl"]), _pct(held["win_rate"])),
        "  day-block resample {} days  total {}  band {} to {}".format(
            band["days"], format_pnl(band["pnl"]),
            format_pnl(band["lo"]), format_pnl(band["hi"])),
    ]
    for label, key in (
        ("reason", lambda t: t["reason"]),
        ("side", lambda t: t["side"]),
        ("entry", entry_bin),
        ("minutes", minute_bin),
    ):
        lines.append("  by {}:".format(label))
        for row in bucket_rows(trades, key):
            lines.append(
                "    {bucket:<12} n {n:<6} pnl {pnl:>10}  win {wr:<6}  "
                "settle {spnl:>10}  settle-win {swr}".format(
                    bucket=str(row["bucket"]),
                    n=row["n"],
                    pnl=format_pnl(row["pnl"]),
                    wr=_pct(row["win_rate"]),
                    spnl=format_pnl(row["settle_pnl"]),
                    swr=_pct(row["settle_win_rate"]),
                ))
    return "\n".join(lines)


def render_variants(rows):
    lines = [
        "Touch-mode variants. Screen is Apr 9-Jul 8. Confirm is Jul 8-Oct 6.",
        "Eligible means both halves beat the published touch rules, with >= {} trades.".format(
            MIN_TRADES),
        "  {:<56} {:>8} {:>7} {:>8} {:>7}  {}".format(
            "variant", "screen", "n", "confirm", "n", "gate"),
    ]
    for row in rows:
        if row["sensitivity"]:
            gate = "sensitivity"
        elif row["label"] == "published rules":
            gate = "baseline"
        elif row["eligible"]:
            gate = "ELIGIBLE"
        else:
            gate = "rejected"
        lines.append("  {:<56} {:>8} {:>7} {:>8} {:>7}  {}".format(
            row["label"][:56],
            format_pnl(row["screen_pnl"]),
            row["screen_trades"],
            format_pnl(row["confirm_pnl"]),
            row["confirm_trades"],
            gate,
        ))
    return "\n".join(lines)


def load_joined(cache_dir):
    markets = [
        m for m in load_manifest(cache_dir)
        if SCREEN_START <= m["close_ts"] <= END_TS
    ]
    cache = {}
    for market in markets:
        cache[market["ticker"]] = load_candles(cache_dir, market["ticker"])

    def candles_for(ticker):
        return cache.get(ticker)

    return markets, candles_for


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explain the KXBTC15M paper-replay loss and score the pre-registered variants.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    args = parser.parse_args(argv)
    cfg, errors = load_config(args.config)
    if errors:
        for line in errors:
            print(line)
        return 1
    markets, candles_for = load_joined(args.cache)
    if not markets:
        print("No cached markets in the frozen 180-day window.")
        return 1
    full = window_markets(markets, SCREEN_START, END_TS, end_inclusive=True)
    lines = [
        "KXBTC15M audit. Fees shown separately. Not an rhengine.alpha.book result.",
        "Window frozen at {}.".format(
            datetime.fromtimestamp(END_TS, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
    ]
    books = {}
    for mode in ("touch", "close"):
        trades = iter_trades(full, candles_for, cfg, mode)
        books[mode] = trades
        lines.append(render_payoff("{} mode, 180d published rules".format(mode), trades))
        fees = fee_summary(trades, cfg.order_size)
        lines.append(
            "  fees: taker/taker {}  net {}  |  taker entry + resting take-profit {}  net {}".format(
                format_pnl(-fees["taker_fees"]), format_pnl(fees["taker_net"]),
                format_pnl(-fees["resting_tp_fees"]), format_pnl(fees["resting_tp_net"])))
    lines.append(render_variants(run_variants(markets, candles_for, cfg, mode="touch")))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
