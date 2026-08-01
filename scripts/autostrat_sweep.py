"""
autostrat_sweep.py — evaluate exit configs in BOTH spaces at once.

The point of this script is that R-space and capitalised space disagree, and
only the second one is money. A config can post a strong mean R, profit factor
and Monte Carlo p50 while LOSING to SPY once the account constraint (finite
position slots, concurrent holds, fixed-fractional sizing) is applied — because
R-multiples ignore capital efficiency and concurrency. See CLAUDE.md rule 2:
R-sums are not returns.

Long holds buy R-edge and pay for it in slot turnover. This sweep finds where
that trade-off actually lands.

Usage: py scripts/autostrat_sweep.py --max-tickers 300 --ticker-offset 120
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from autostrat_lab import (apply_cost, day_clustered_t, load_bars, mc_p50,
                           run_parallel, universe)
from rhengine.portfolio import simulate_portfolio, sessions_from_bars

CONFIGS = [
    # label,                 target, hold
    ("base   tgt2.5 hold10", 2.5, 10),
    ("       tgt5.0 hold10", 5.0, 10),
    ("       tgt5.0 hold20", 5.0, 20),
    ("       tgt8.0 hold20", 8.0, 20),
    ("       tgt8.0 hold30", 8.0, 30),
    ("cand   tgt8.0 hold60", 8.0, 60),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="macd")
    ap.add_argument("--lo", default="2015-01-01")
    ap.add_argument("--max-tickers", type=int, default=300)
    ap.add_argument("--ticker-offset", type=int, default=120)
    ap.add_argument("--regime", default="downtrend,choppy")
    ap.add_argument("--bps", type=float, default=30.0)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    ap.add_argument("--max-positions", type=int, default=20)
    args = ap.parse_args()

    cfg0 = json.load(open("config.json"))
    spy = load_bars("SPY", args.lo, None)
    sessions = sessions_from_bars(spy)
    years = len(sessions) / 252.0
    spy_tot = 100.0 * (spy[-1]["close"] / spy[0]["close"] - 1.0)
    spy_cagr = 100.0 * ((1 + spy_tot / 100) ** (1 / years) - 1)
    c = np.array([b["close"] for b in spy], float)
    rr = np.diff(c) / c[:-1]
    spy_sharpe = rr.mean() / rr.std() * np.sqrt(252)
    peak = np.maximum.accumulate(c)
    spy_dd = 100 * ((peak - c) / peak).max()

    syms = sorted(universe(args.lo, None, args.max_tickers, 0.0,
                           args.ticker_offset))
    regime = args.regime.split(",") if args.regime else None
    print(f"universe {len(syms)} | {sessions[0]}..{sessions[-1]} ({years:.1f} yr) "
          f"| regime={args.regime or 'all'} | cost {args.bps:.0f} bps")
    print(f"SPY buy-and-hold: total {spy_tot:+.1f}%  CAGR {spy_cagr:.2f}%  "
          f"maxDD {spy_dd:.1f}%  Sharpe {spy_sharpe:.2f}")
    print(f"account: risk {args.risk_pct}%/trade, max {args.max_positions} "
          f"concurrent positions\n")
    print(f"{'config':<22}{'n':>7}{'meanR':>9}{'dayT':>7}{'MCp50':>7} | "
          f"{'taken':>6}{'CAGR%':>8}{'maxDD%':>8}{'Sharpe':>8}{'vsSPY':>8}")

    for label, tgt, hold in CONFIGS:
        cfg = json.loads(json.dumps(cfg0))
        cfg.setdefault("strategy", {}).setdefault("thresholds", {}) \
           .setdefault(args.strategy, {}).update({"stopAtrMult": 1.5,
                                                  "targetAtrMult": tgt})
        cfg.setdefault("exits", {})["max_hold_sessions"] = hold
        tr0 = run_parallel(cfg, syms, spy, args.lo, None, args.strategy,
                           3, None, regime)
        if not tr0:
            print(f"{label:<22} NO TRADES")
            continue
        tr = apply_cost(tr0, args.bps)
        r = np.array([t["r_multiple"] for t in tr], float)
        tc, _ = day_clustered_t(tr)
        mc = mc_p50(tr)
        st = simulate_portfolio(tr, sessions, start_equity=10_000.0,
                                risk_pct=args.risk_pct,
                                max_positions=args.max_positions,
                                max_new_per_day=8, max_heat_pct=100.0)["stats"]
        cagr = 100 * ((1 + st["total_return_pct"] / 100) ** (1 / years) - 1)
        print(f"{label:<22}{len(tr):>7}{r.mean():>+9.4f}{tc:>+7.2f}"
              f"{mc.get('p50', float('nan')):>7.3f} | {st['trades_taken']:>6}"
              f"{cagr:>8.2f}{st['max_drawdown_pct']:>8.1f}{st['sharpe']:>8.2f}"
              f"{cagr - spy_cagr:>+8.2f}")


if __name__ == "__main__":
    main()
