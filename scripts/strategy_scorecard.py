"""
strategy_scorecard.py — score every sleeve on the deepest available history.

Decides which strategies the autonomous Robinhood agent may trade and which are
manual-only (Nydex personal configuration). Every prior sleeve verdict in this
repo was formed on a 213-session, single-regime window; this runs the same
engine over 1,930 sessions / 7.7 years covering the 2018 selloff, COVID, the
2022 bear market and 2025-26.

DATA CAVEAT: yahoo_cache is current S&P500/NDX membership, so it is
survivorship-biased and every number below is OPTIMISTIC in absolute terms. It
is the only source here with enough depth. A sleeve that fails on this data
fails a fortiori on an honest universe.

Reports at 0 / 5 / 30 bps round trip:
  mean R, PF, day-clustered t (FC-3: NOT the per-trade t), breakeven cost,
  portfolio return / maxDD / Sharpe vs SPY buy-and-hold.

Usage:  py scripts/strategy_scorecard.py [--max-tickers N] [--strategies a,b]
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from rhengine.backtest import run_backtest_on
from rhengine.costs import breakeven_cost_bps
from rhengine.portfolio import simulate_portfolio, sessions_from_bars

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from o1_horizon_test import load_cache  # noqa: E402  (shared cache loader)

COSTS = [0.0, 5.0, 30.0]
# (label, forced strategy, regime restriction) — regime restriction mirrors the
# production routing in indicators.select_strategy_for_regime.
RUNS = [
    ("residualmomentum", "residualmomentum", None),
    ("bollinger (all regimes)", "bollinger", None),
    ("bollinger (choppy only)", "bollinger", "choppy"),
    ("macd", "macd", None),
    ("vwap", "vwap", None),
    ("consolidation", "consolidation", None),
    ("pead", "pead", None),
]


def day_clustered_t(trades):
    by = {}
    for t in trades:
        by.setdefault(t["signal_date"], []).append(t["r_multiple"])
    if len(by) < 3:
        return float("nan"), len(by)
    d = np.array([np.mean(v) for v in by.values()])
    return float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))), len(by)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tickers", type=int, default=120)
    ap.add_argument("--strategies", default=None)
    args = ap.parse_args()

    cfg = json.load(open("config.json"))
    bars_map = load_cache(min_bars=1200, max_tickers=args.max_tickers)
    spy_bars = bars_map.pop("SPY")
    sessions = sessions_from_bars(spy_bars)
    # start after the deepest warmup any sleeve needs (150d SMA stage filter)
    start = 200
    date_lo = sessions[start]
    win = sessions[start:]
    spy_ret = 100.0 * (spy_bars[-1]["close"] / spy_bars[start]["close"] - 1.0)
    years = len(win) / 252.0

    wanted = set(args.strategies.split(",")) if args.strategies else None

    print("universe {} tickers | {} .. {} ({} sessions, {:.1f} yr)".format(
        len(bars_map), date_lo, sessions[-1], len(win), years))
    print("SPY buy-and-hold: {:+.1f}%   |   costs tested: {} bps".format(
        spy_ret, "/".join(str(int(c)) for c in COSTS)))
    print("CAVEAT: current-membership universe => survivorship-biased, "
          "absolute levels OPTIMISTIC.\n")

    for label, strat, regime in RUNS:
        if wanted and strat not in wanted:
            continue
        t1 = time.time()
        try:
            trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=3,
                                     forced_strategy=strat, cost_bps=0.0,
                                     date_lo=date_lo, regime_only=regime)
        except Exception as e:
            print("{:<26} ERROR {}: {}\n".format(label, type(e).__name__, e))
            continue
        if not trades:
            print("{:<26} NO TRADES\n".format(label))
            continue

        entry = np.array([t["entry"] for t in trades], float)
        stop = np.array([t["stop"] for t in trades], float)
        r0 = np.array([t["r_multiple"] for t in trades], float)
        sp = np.where(entry > 0, (entry - stop) / np.maximum(entry, 1e-9), np.nan)
        ok = np.isfinite(sp) & (sp > 0)
        be = breakeven_cost_bps(trades)
        _, ndays = day_clustered_t(trades)

        print("{}   [{} trades over {} signal days, {:.0f}s]".format(
            label, len(trades), ndays, time.time() - t1))
        print("   breakeven {}   signals/yr {:.0f}".format(
            "n/a" if be is None else "{:.1f} bps".format(be), len(trades) / years))
        print("   {:>4} {:>9} {:>7} {:>11} {:>10} {:>8} {:>7}".format(
            "bps", "meanR", "PF", "dayclust t", "portfolio", "maxDD", "Sharpe"))
        for bps in COSTS:
            cost = np.zeros_like(r0)
            cost[ok] = 2.0 * (bps / 1e4) / sp[ok]
            r = r0 - cost
            gl = -r[r <= 0].sum()
            pf = (r[r > 0].sum() / gl) if gl > 0 else float("inf")
            tr = [dict(t, r_multiple=float(x)) for t, x in zip(trades, r)]
            tc, _ = day_clustered_t(tr)
            st = simulate_portfolio(tr, win, start_equity=10_000.0, risk_pct=1.0,
                                    max_positions=10, max_new_per_day=4,
                                    max_heat_pct=10.0)["stats"]
            print("   {:>4.0f} {:>+9.4f} {:>7.3f} {:>+11.2f} {:>+9.2f}% {:>7.2f}% "
                  "{:>7.2f}".format(bps, r.mean(), pf, tc,
                                    st["total_return_pct"],
                                    st["max_drawdown_pct"], st["sharpe"]))
        print()

    print("SPY buy-and-hold over the same window: {:+.1f}%".format(spy_ret))


if __name__ == "__main__":
    main()
