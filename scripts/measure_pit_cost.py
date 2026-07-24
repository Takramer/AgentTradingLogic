"""
measure_pit_cost.py — isolate the effect of the two Phase-1 integrity fixes.

Runs the SAME window and the SAME engine three ways so each change is
attributable:

  A  static universe, 0 bps     — reproduces the legacy headline artifact
  B  static universe, 30 bps    — cost fix only        (audit FC-2)
  C  point-in-time universe, 30 bps — cost + survivorship fix (FC-2 + FC-4)

Reports per-trade stats, day-clustered t (the honest one, FC-3), breakeven cost,
and the capital-constrained portfolio result vs SPY buy-and-hold.

Usage:  py scripts/measure_pit_cost.py
"""

import json
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from rhengine.backtest import run_backtest_on, _iso_date
from rhengine.costs import breakeven_cost_bps
from rhengine.data_local import PitMembership, pit_membership, resolve_watchlist
from rhengine.market_data import fetch_bars_batch_massive
from rhengine.portfolio import simulate_portfolio, sessions_from_bars

LOOKBACK = 400
COST = 30.0


def day_clustered_t(trades):
    """t-stat of the equal-weight daily basket. FC-3: the per-trade t-stat in
    backtest._stats treats ~540 same-day overlapping signals as independent."""
    by_day = {}
    for t in trades:
        by_day.setdefault(t["signal_date"], []).append(t["r_multiple"])
    if len(by_day) < 3:
        return float("nan"), 0
    daily = np.array([np.mean(v) for v in by_day.values()])
    return float(daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))), len(daily)


def describe(label, trades, sessions, spy_ret):
    if not trades:
        print("  {:<34} NO TRADES".format(label))
        return
    r = np.array([t["r_multiple"] for t in trades])
    gw = r[r > 0].sum()
    gl = -r[r <= 0].sum()
    pf = gw / gl if gl > 0 else float("inf")
    tc, ndays = day_clustered_t(trades)
    naive_t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
    be = breakeven_cost_bps(trades)
    port = simulate_portfolio(trades, sessions, start_equity=10_000.0,
                              risk_pct=1.0, max_positions=10,
                              max_new_per_day=4, max_heat_pct=10.0)["stats"]
    print("  {:<34}".format(label))
    print("     trades {:>7}  sessions {:>4}  meanR {:+.4f}  PF {:.3f}".format(
        len(r), ndays, r.mean(), pf))
    print("     t naive {:+.2f}   t DAY-CLUSTERED {:+.2f}   breakeven {}".format(
        naive_t, tc, "n/a" if be is None else "{:.1f} bps".format(be)))
    print("     portfolio(10pos/4day/1%): {:+.2f}%  maxDD {:.2f}%  Sharpe {:.2f}"
          "  taken {}/{}".format(
              port["total_return_pct"], port["max_drawdown_pct"],
              port["sharpe"], port["trades_taken"], len(r)))
    print("     vs SPY buy-and-hold {:+.2f}% over the same window".format(spy_ret))
    print()


def main():
    cfg = json.load(open("config.json"))
    t0 = time.time()

    print("Building point-in-time membership...")
    pkl = "tests/results/_pit_membership.pkl"
    if os.path.exists(pkl):
        blob = pickle.load(open(pkl, "rb"))
        mem = PitMembership(list(zip(blob["dates"], blob["sets"])), 21)
        print("  loaded cached membership: {} snapshots".format(len(mem)))
    else:
        mem = pit_membership(cfg, rebalance_sessions=21, verbose=True)

    static = resolve_watchlist(cfg, verbose=False)
    union = sorted(set(mem.union()) | set(static))
    print("  static universe {} | PIT union {} | fetching {} symbols".format(
        len(static), len(mem.union()), len(union)))

    spy_t = cfg.get("spy_ticker", "SPY")
    bars_map = fetch_bars_batch_massive(union + [spy_t],
                                        lookback_days=LOOKBACK,
                                        verbose=False)["bars"]
    spy_bars = bars_map.pop(spy_t)
    sessions = sessions_from_bars(spy_bars)
    spy_ret = 100.0 * (spy_bars[-1]["close"] / spy_bars[0]["close"] - 1.0)
    print("  bars loaded for {} tickers, {} sessions ({}..{}), {:.0f}s\n".format(
        len(bars_map), len(sessions), sessions[0], sessions[-1], time.time() - t0))

    static_set = set(static)
    static_bars = {k: v for k, v in bars_map.items() if k in static_set}

    print("=" * 72)
    print("SAME WINDOW, SAME ENGINE — each fix isolated")
    print("=" * 72)

    a = run_backtest_on(cfg, static_bars, spy_bars, min_score=3, cost_bps=0.0)
    describe("A  static universe,  0 bps  (legacy)", a, sessions, spy_ret)

    b = run_backtest_on(cfg, static_bars, spy_bars, min_score=3, cost_bps=COST)
    describe("B  static universe, 30 bps  (FC-2)", b, sessions, spy_ret)

    c = run_backtest_on(cfg, bars_map, spy_bars, min_score=3, cost_bps=COST,
                        universe=mem)
    describe("C  POINT-IN-TIME univ, 30 bps (FC-2+FC-4)", c, sessions, spy_ret)

    print("total {:.0f}s".format(time.time() - t0))


if __name__ == "__main__":
    main()
