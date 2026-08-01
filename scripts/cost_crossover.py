"""
cost_crossover.py — at what transaction cost does the strategy stop working?

measure_pit_cost.py showed the book is significantly negative at 30 bps. The
decision-relevant question is not "is 30 the right number" but "what cost does
this strategy REQUIRE, and is that attainable in its own universe?"

Runs ONE point-in-time backtest at 0 bps, then applies cost analytically per
trade (cost_R = 2 * bps/1e4 / stop_pct) and re-runs the capital-constrained
portfolio at each level. Reports the two thresholds that matter:

    breakeven      — cost at which mean R hits zero
    SPY crossover  — cost at which the portfolio stops beating buy-and-hold

Usage:  py scripts/cost_crossover.py
"""

import json
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from rhengine.backtest import run_backtest_on
from rhengine.data_local import PitMembership, pit_membership
from rhengine.market_data import fetch_bars_batch_massive
from rhengine.portfolio import simulate_portfolio, sessions_from_bars

LOOKBACK = 400
LEVELS = [0, 5, 8, 10, 15, 20, 30, 50, 75, 100]


def day_clustered_t(dates, r):
    by = {}
    for d, x in zip(dates, r):
        by.setdefault(d, []).append(x)
    daily = np.array([np.mean(v) for v in by.values()])
    return float(daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))))


def main():
    cfg = json.load(open("config.json"))
    t0 = time.time()

    pkl = "tests/results/_pit_membership.pkl"
    if os.path.exists(pkl):
        blob = pickle.load(open(pkl, "rb"))
        mem = PitMembership(list(zip(blob["dates"], blob["sets"])), 21)
    else:
        mem = pit_membership(cfg, rebalance_sessions=21, verbose=True)

    spy_t = cfg.get("spy_ticker", "SPY")
    bars_map = fetch_bars_batch_massive(sorted(mem.union()) + [spy_t],
                                        lookback_days=LOOKBACK, verbose=False)["bars"]
    spy_bars = bars_map.pop(spy_t)
    sessions = sessions_from_bars(spy_bars)
    spy_ret = 100.0 * (spy_bars[-1]["close"] / spy_bars[0]["close"] - 1.0)

    print("Point-in-time backtest at 0 bps ({} tickers)...".format(len(bars_map)))
    trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=3,
                             cost_bps=0.0, universe=mem)
    print("  {} trades in {:.0f}s\n".format(len(trades), time.time() - t0))

    entry = np.array([t["entry"] for t in trades], dtype=float)
    stop = np.array([t["stop"] for t in trades], dtype=float)
    r0 = np.array([t["r_multiple"] for t in trades], dtype=float)
    dates = [t["signal_date"] for t in trades]
    stop_pct = np.where(entry > 0, (entry - stop) / np.maximum(entry, 1e-9), np.nan)
    ok = np.isfinite(stop_pct) & (stop_pct > 0)

    print("=" * 78)
    print("COST CROSSOVER — point-in-time universe, {} sessions, SPY {:+.2f}%".format(
        len(set(dates)), spy_ret))
    print("=" * 78)
    print("  {:>5}  {:>9}  {:>7}  {:>9}  {:>10}  {:>8}  {:>7}".format(
        "bps", "meanR", "PF", "dayclust t", "portfolio", "maxDD", "Sharpe"))

    for bps in LEVELS:
        cost = np.zeros_like(r0)
        # ROUND TRIP, no 2x — see backtest._cost_r. Missed by commit e753c64,
        # so every crossover level printed here was double its label.
        cost[ok] = (bps / 1e4) / stop_pct[ok]
        r = r0 - cost
        gw = r[r > 0].sum()
        gl = -r[r <= 0].sum()
        pf = gw / gl if gl > 0 else float("inf")
        tc = day_clustered_t(dates, r)
        tr = [dict(t, r_multiple=float(x)) for t, x in zip(trades, r)]
        st = simulate_portfolio(tr, sessions, start_equity=10_000.0, risk_pct=1.0,
                                max_positions=10, max_new_per_day=4,
                                max_heat_pct=10.0)["stats"]
        flag = ""
        if st["total_return_pct"] < spy_ret and not flag:
            flag = "  <- loses to SPY"
        if r.mean() <= 0:
            flag = "  <- NEGATIVE EDGE"
        print("  {:>5}  {:>+9.4f}  {:>7.3f}  {:>+9.2f}  {:>+9.2f}%  {:>7.2f}%  {:>7.2f}{}".format(
            bps, r.mean(), pf, tc, st["total_return_pct"],
            st["max_drawdown_pct"], st["sharpe"], flag))

    print()
    print("  SPY buy-and-hold over the same window: {:+.2f}%".format(spy_ret))
    print("  universe liquidity floor: ${:,.0f}/day median dollar volume".format(
        float(cfg.get("universe_filters", {}).get("min_dollar_volume", 0))))
    print("\ntotal {:.0f}s".format(time.time() - t0))


if __name__ == "__main__":
    main()
