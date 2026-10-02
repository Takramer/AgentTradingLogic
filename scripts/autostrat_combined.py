"""
autostrat_combined.py — SPY base + stock-picking overlay, sharing one pot.

The finding this is built on (docs/AUTOSTRAT-2026-07-31.md): the macd sleeve and
SPY are complementary across SPY's own EMA regime.

    regime      %days   SPY ann.ret  SPY Sharpe   macd sleeve
    uptrend     61.1%      6.45%        0.55        +0.0051 R
    choppy      25.8%     45.10%        2.14        +0.1611 R
    downtrend   13.1%      7.00%        0.20        +0.3281 R

SPY is weakest exactly where the sleeve is strongest. The standalone sleeve
loses to SPY mainly because it stands FLAT ~87% of the time while SPY compounds;
this parks the idle capital instead.

CAPITAL IS SHARED AND THAT IS THE POINT. Every dollar in a stock position is a
dollar not in SPY, so the overlay only pays if the position beats SPY over the
same days. Reporting an overlay against SPY without that constraint is the
classic way to manufacture a fake edge.

P&L convention matches rhengine/portfolio.py: stock P&L is realized on the exit
date as risk_dollars * r_multiple, so the two are directly comparable. Idle cash
earns the SPY daily return when the overlay says to be invested.

Usage: py scripts/autostrat_combined.py --max-tickers 300 --ticker-offset 120
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from autostrat_lab import apply_cost, load_bars, run_parallel, universe
from rhengine.backtest import _iso_date
from rhengine.indicators import classify_spy_regime
from rhengine.portfolio import _schedule, sessions_from_bars


def spy_regime_series(spy):
    """Causal: classify_spy_regime reads only the trailing window."""
    return {_iso_date(spy[t]["time"]):
            (classify_spy_regime(spy[:t + 1]) if t >= 55 else "choppy")
            for t in range(len(spy))}


def simulate_combined(trades, sessions, spy, regime, park_when=None,
                      start_equity=10_000.0, risk_pct=0.5, max_positions=20,
                      max_new_per_day=8, max_heat_pct=100.0):
    """Replay `trades` under capital constraints; idle capital sits in SPY on
    days whose regime is in `park_when` (None = never park, i.e. the pure
    sleeve). Returns (equity_curve, stats)."""
    spy_close = {_iso_date(b["time"]): b["close"] for b in spy}
    scheduled = _schedule(trades, sessions)
    by_entry = {}
    for s in scheduled:
        by_entry.setdefault(s["entry_date"], []).append(s)

    equity = float(start_equity)
    open_pos, curve = [], []
    prev_close, prev_regime = None, None
    taken = 0

    for day in sessions:
        # 1. realize exits
        still = []
        for p in open_pos:
            if p["exit_date"] <= day:
                equity += p["risk_dollars"] * p["r_multiple"]
            else:
                still.append(p)
        open_pos = still

        # 2. SPY return on idle capital (committed notional is NOT in SPY).
        # LOOKAHEAD GUARD: the allocation for `day` must be decided from the
        # PREVIOUS session's regime. regime[day] is computed from closes through
        # day itself, so using it to earn day's return lets the book sidestep a
        # decline it could not have known about. That bug read 26.31% CAGR at
        # 12.1% maxDD off a plain EMA filter — roughly double buy-and-hold — and
        # is exactly the "surprisingly good" signature in docs/CLAUDE-PITFALLS.md.
        px = spy_close.get(day)
        if prev_close and px and park_when and prev_regime in park_when:
            committed = sum(p["notional"] for p in open_pos)
            idle = max(0.0, equity - committed)
            equity += idle * (px / prev_close - 1.0)
        if px:
            prev_close = px
        prev_regime = regime.get(day, prev_regime)

        # 3. open new positions
        new_today = 0
        held = {p["ticker"] for p in open_pos}
        for s in by_entry.get(day, []):
            if s["ticker"] in held:
                continue
            if len(open_pos) >= max_positions or new_today >= max_new_per_day:
                continue
            e, st = float(s["entry"]), float(s["stop"])
            stop_pct = (e - st) / e if e > 0 else 0.0
            if stop_pct <= 0:
                continue
            risk_d = equity * risk_pct / 100.0
            open_risk = sum(p["risk_dollars"] for p in open_pos)
            if open_risk + risk_d > equity * max_heat_pct / 100.0:
                continue
            notional = min(risk_d / stop_pct, equity)  # no leverage per name
            open_pos.append(dict(s, risk_dollars=risk_d, notional=notional))
            held.add(s["ticker"])
            new_today += 1
            taken += 1
        curve.append((day, equity))

    eq = np.array([c[1] for c in curve], float)
    years = len(sessions) / 252.0
    rets = np.diff(eq) / eq[:-1]
    peak = np.maximum.accumulate(eq)
    stats = {
        "final": eq[-1],
        "total_pct": 100.0 * (eq[-1] / start_equity - 1.0),
        "cagr": 100.0 * ((eq[-1] / start_equity) ** (1 / years) - 1),
        "maxdd": 100.0 * ((peak - eq) / peak).max(),
        "sharpe": (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0,
        "taken": taken,
    }
    return curve, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="macd")
    ap.add_argument("--lo", default="2015-01-01")
    ap.add_argument("--max-tickers", type=int, default=300)
    ap.add_argument("--ticker-offset", type=int, default=120)
    ap.add_argument("--bps", type=float, default=30.0)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    ap.add_argument("--max-positions", type=int, default=20)
    ap.add_argument("--target", type=float, default=8.0)
    ap.add_argument("--hold", type=int, default=30)
    args = ap.parse_args()

    cfg = json.load(open("config.json"))
    cfg.setdefault("strategy", {}).setdefault("thresholds", {}) \
       .setdefault(args.strategy, {}).update({"stopAtrMult": 1.5,
                                              "targetAtrMult": args.target})
    cfg.setdefault("exits", {})["max_hold_sessions"] = args.hold

    spy = load_bars("SPY", args.lo, None)
    sessions = sessions_from_bars(spy)
    regime = spy_regime_series(spy)
    years = len(sessions) / 252.0
    syms = sorted(universe(args.lo, None, args.max_tickers, 0.0,
                           args.ticker_offset))

    c = np.array([b["close"] for b in spy], float)
    rr = np.diff(c) / c[:-1]
    peak = np.maximum.accumulate(c)
    spy_stats = {"cagr": 100 * ((c[-1] / c[0]) ** (1 / years) - 1),
                 "maxdd": 100 * ((peak - c) / peak).max(),
                 "sharpe": rr.mean() / rr.std() * np.sqrt(252)}
    print(f"universe {len(syms)} | {sessions[0]}..{sessions[-1]} ({years:.1f} yr)"
          f" | {args.bps:.0f} bps | risk {args.risk_pct}% | {args.max_positions} slots")
    print(f"BENCHMARK SPY buy-and-hold: CAGR {spy_stats['cagr']:.2f}%  "
          f"maxDD {spy_stats['maxdd']:.1f}%  Sharpe {spy_stats['sharpe']:.2f}\n")

    # entry-regime variants of the sleeve
    books = {}
    for label, regs in [("entries: down+choppy", ["downtrend", "choppy"]),
                        ("entries: downtrend", ["downtrend"])]:
        books[label] = apply_cost(
            run_parallel(cfg, syms, spy, args.lo, None, args.strategy, 3, None, regs),
            args.bps)

    ALL = {"uptrend", "choppy", "downtrend"}
    variants = [
        ("sleeve alone (no park)",        "entries: down+choppy", None),
        ("+ park idle in SPY always",     "entries: down+choppy", ALL),
        ("+ park idle, skip downtrend",   "entries: down+choppy", {"uptrend", "choppy"}),
        ("down-entries + park always",    "entries: downtrend",   ALL),
        ("down-entries + park ex-down",   "entries: downtrend",   {"uptrend", "choppy"}),
    ]
    print(f"{'variant':<32}{'taken':>7}{'CAGR%':>9}{'maxDD%':>9}"
          f"{'Sharpe':>8}{'vsSPY':>8}")
    for name, book, park in variants:
        _, st = simulate_combined(books[book], sessions, spy, regime, park,
                                  risk_pct=args.risk_pct,
                                  max_positions=args.max_positions)
        print(f"{name:<32}{st['taken']:>7}{st['cagr']:>9.2f}{st['maxdd']:>9.1f}"
              f"{st['sharpe']:>8.2f}{st['cagr']-spy_stats['cagr']:>+8.2f}")


if __name__ == "__main__":
    main()
