"""
o1_horizon_test.py — O-1: does residual momentum work at its CITED horizon?

The sleeve carrying 94% of the book measures a 12-DAY residual while citing
Blitz, Huij & Martens (2011), which specifies 12 MONTHS (audit FC-8). At 12 days
the documented US equity effect is short-term reversal, not momentum. This
script tests both horizons head to head.

    SHORT  regBars 36,  formBars 12,  skipBars 1   daily signals, 10-day hold
    LONG   regBars 756, formBars 252, skipBars 21  monthly signals, 21-day hold

Both arms run on the SAME signal window and the SAME universe so the comparison
is like-for-like. The long arm cannot score until 1,030 bars of history exist,
so the window starts there and the short arm is restricted to match.

DATA: yahoo_cache deep history (~2,960 SPY bars, 2014-10 -> 2026-07), which is
the only source here reaching a 36-month window; the massive cache has 526
sessions. That universe is CURRENT S&P500+NDX membership and is therefore
survivorship-biased — absolute levels are optimistic. Both arms carry the
identical bias, so the HORIZON COMPARISON is valid; the absolute numbers are not.

Usage:  py scripts/o1_horizon_test.py [--max-tickers N]
"""

import argparse
import gzip
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from rhengine.backtest import _iso_date, run_backtest_on
from rhengine.portfolio import simulate_portfolio, sessions_from_bars

CACHE = "tests/yahoo_cache"
LONG_WINDOW = 756 + 252 + 21 + 1          # 1030 bars before the long arm scores
COSTS = [0.0, 5.0, 30.0]

# SELECTIVITY MATCHING — minResidualRA is compared against mean(residuals) /
# std(residuals) over the formation window. That statistic's dispersion scales
# as 1/sqrt(formBars), so a threshold tuned for 12 observations is ~5x too
# strict at 252. Measured on this cache: std 0.3315 at formBars=12 vs 0.0650 at
# formBars=252 (ratio 5.10; sqrt(252/12) = 4.58). At 0.15 the short arm admits
# 52.8% of observations and the long arm 1.0% — comparing them at the same
# nominal threshold compares selectivity, not horizon. The long arms use the
# scale-equivalent 0.03 so both admit a similar fraction.
ARMS = {
    "SHORT 36/12/1   daily,   ATR exits, hold 10": {
        "th": {"regBars": 36, "formBars": 12, "skipBars": 1,
               "legMask": 1, "minResidualRA": 0.15},
        "max_hold": 10, "monthly": False,
    },
    "LONG  756/252/21 monthly, ATR exits, hold 21": {
        "th": {"regBars": 756, "formBars": 252, "skipBars": 21,
               "legMask": 1, "minResidualRA": 0.03},
        "max_hold": 21, "monthly": True,
    },
    "LONG  756/252/21 monthly, WIDE stops, hold 21": {
        # Wide stop/target so the TIME stop dominates — approximates the
        # literature's "hold the selection until the next rebalance" rule.
        # The previous arm showed avg hold 6.8 sessions despite max_hold=21:
        # the ATR exit engine, not the signal horizon, was setting turnover.
        "th": {"regBars": 756, "formBars": 252, "skipBars": 21,
               "legMask": 1, "minResidualRA": 0.03,
               "stopAtrMult": 6.0, "targetAtrMult": 12.0},
        "max_hold": 21, "monthly": True,
    },
}


def _read(sym):
    try:
        return json.load(gzip.open(os.path.join(CACHE, sym + ".json.gz"), "rt"))["bars"]
    except Exception:
        return None


def load_cache(min_bars, max_tickers=0):
    """SPY is loaded unconditionally — it is the regression anchor, and an
    alphabetical --max-tickers cut would otherwise drop it."""
    bars_map = {}
    spy = _read("SPY")
    if spy:
        bars_map["SPY"] = spy
    for fn in sorted(os.listdir(CACHE)):
        if not fn.endswith(".json.gz"):
            continue
        sym = fn[:-8]
        if sym == "SPY":
            continue
        bars = _read(sym)
        if bars and len(bars) >= min_bars:
            bars_map[sym] = bars
        if max_tickers and len(bars_map) > max_tickers:
            break
    return bars_map


def day_clustered_t(trades):
    by = {}
    for t in trades:
        by.setdefault(t["signal_date"], []).append(t["r_multiple"])
    if len(by) < 3:
        return float("nan")
    d = np.array([np.mean(v) for v in by.values()])
    return float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tickers", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()

    base = json.load(open("config.json"))
    bars_map = load_cache(min_bars=1200, max_tickers=args.max_tickers)
    spy_bars = bars_map.pop("SPY", None)
    if not spy_bars:
        raise SystemExit("SPY missing from yahoo_cache")

    sessions = sessions_from_bars(spy_bars)
    start_i = LONG_WINDOW
    date_lo = sessions[start_i]
    win = sessions[start_i:]
    spy_ret = 100.0 * (spy_bars[-1]["close"] / spy_bars[start_i]["close"] - 1.0)
    years = len(win) / 252.0

    # monthly rebalance calendar: first session of each calendar month
    monthly, seen = set(), set()
    for d in win:
        if d[:7] not in seen:
            seen.add(d[:7])
            monthly.add(d)

    print("universe {} tickers | signal window {} .. {} ({} sessions, {:.1f} yr)"
          .format(len(bars_map), date_lo, sessions[-1], len(win), years))
    print("SPY buy-and-hold over that window: {:+.1f}%".format(spy_ret))
    print("monthly rebalance dates: {}".format(len(monthly)))
    print("NOTE: current-membership universe => survivorship-biased. The "
          "horizon COMPARISON is valid; absolute levels are optimistic.\n")

    for label, arm in ARMS.items():
        cfg = json.loads(json.dumps(base))
        cfg["strategy"]["thresholds"]["residualmomentum"] = arm["th"]
        cfg["exits"]["max_hold_sessions"] = arm["max_hold"]
        t1 = time.time()
        trades = run_backtest_on(
            cfg, bars_map, spy_bars, min_score=1,
            forced_strategy="residualmomentum", cost_bps=0.0, date_lo=date_lo,
            signal_dates=(monthly if arm["monthly"] else None))
        if not trades:
            print("{}\n   NO TRADES\n".format(label))
            continue
        entry = np.array([t["entry"] for t in trades], float)
        stop = np.array([t["stop"] for t in trades], float)
        r0 = np.array([t["r_multiple"] for t in trades], float)
        sp = np.where(entry > 0, (entry - stop) / np.maximum(entry, 1e-9), np.nan)
        ok = np.isfinite(sp) & (sp > 0)
        hold = np.mean([t["sessions_held"] for t in trades])

        print("{}   [{} trades, {:.0f}s]".format(label, len(trades), time.time() - t1))
        print("   avg hold {:.1f} sessions | signals/yr {:.0f}".format(
            hold, len(trades) / years))
        print("   {:>5}  {:>9}  {:>7}  {:>10}  {:>10}  {:>8}  {:>7}  {:>7}".format(
            "bps", "meanR", "PF", "dayclust t", "portfolio", "maxDD", "Sharpe", "taken"))
        for bps in COSTS:
            cost = np.zeros_like(r0)
            # ROUND TRIP, no 2x — see backtest._cost_r. Missed by commit e753c64.
            cost[ok] = (bps / 1e4) / sp[ok]
            r = r0 - cost
            gl = -r[r <= 0].sum()
            pf = (r[r > 0].sum() / gl) if gl > 0 else float("inf")
            tr = [dict(t, r_multiple=float(x)) for t, x in zip(trades, r)]
            st = simulate_portfolio(tr, win, start_equity=10_000.0, risk_pct=1.0,
                                    max_positions=10, max_new_per_day=4,
                                    max_heat_pct=10.0)["stats"]
            print("   {:>5.0f}  {:>+9.4f}  {:>7.3f}  {:>+10.2f}  {:>+9.2f}%  "
                  "{:>7.2f}%  {:>7.2f}  {:>7}".format(
                      bps, r.mean(), pf, day_clustered_t(tr),
                      st["total_return_pct"], st["max_drawdown_pct"],
                      st["sharpe"], st["trades_taken"]))
        print()

    print("total {:.0f}s".format(time.time() - t0))


if __name__ == "__main__":
    main()
