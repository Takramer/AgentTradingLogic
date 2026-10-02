"""
autostrat_screen.py — screen every strategy on NET RATE vs the matched index.

The metric is from autostrat_dollarday.py: a trade's return on the capital it
ties up is r_multiple * stop_pct, which is a property of the trade alone. Divide
the book's total by its total dollar-days and compare against SPY's return over
exactly the same entry..exit windows:

    net = book bp/day - SPY bp/day over the SAME windows

This is the right screen, and it is cheap, because it needs no sizing, no slot
count and no portfolio replay — the things that made rounds 1-8 slow and let
R-space results survive far longer than they deserved. A sleeve with net <= 0 is
a worse place to park a dollar than the index on the very days it chooses to be
invested, so it cannot be rescued by exits, ranking, sizing or blending; all of
those redistribute dollar-days without making them more productive.

Compare against SPY UNCONDITIONAL only to see how much of a sleeve's raw rate is
market timing rather than name selection. A sleeve whose raw rate beats
unconditional SPY but whose net is negative is a timing signal wearing a stock
picker's clothes — its information is in WHEN, not WHICH.

Usage: py scripts/autostrat_screen.py --max-tickers 300 --ticker-offset 120
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from autostrat_dollarday import attach, dayt
from autostrat_lab import apply_cost, day_clustered_t, load_bars, run_parallel, universe
from rhengine.portfolio import sessions_from_bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default=None,
                    help="comma-separated; default = every non-retired strategy")
    ap.add_argument("--lo", default="2015-01-01")
    ap.add_argument("--max-tickers", type=int, default=300)
    ap.add_argument("--ticker-offset", type=int, default=120)
    ap.add_argument("--regime", default=None)
    ap.add_argument("--bps", type=float, default=30.0)
    ap.add_argument("--target", type=float, default=8.0)
    ap.add_argument("--stop", type=float, default=1.5)
    ap.add_argument("--hold", type=int, default=30)
    args = ap.parse_args()

    sj = json.load(open("rhengine/strategies.json"))
    sj = sj.get("strategies", sj)
    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",") if s.strip()]
    else:
        names = [k for k, v in sj.items()
                 if not (isinstance(v, dict) and v.get("status") == "retired")]

    cfg0 = json.load(open("config.json"))
    spy = load_bars("SPY", args.lo, None)
    sessions = sessions_from_bars(spy)
    years = len(sessions) / 252.0
    c = np.array([b["close"] for b in spy], float)
    spy_daily = float(np.mean(np.diff(c) / c[:-1]))
    syms = sorted(universe(args.lo, None, args.max_tickers, 0.0,
                           args.ticker_offset))
    regs = args.regime.split(",") if args.regime else None

    print(f"universe {len(syms)} | {sessions[0]}..{sessions[-1]} ({years:.1f} yr)"
          f" | {args.bps:.0f} bps | regime={args.regime or 'all'}"
          f" | stop {args.stop} tgt {args.target} hold {args.hold}")
    print(f"SPY unconditional {1e4*spy_daily:+.2f} bp/day\n")
    print(f"{'strategy':<18}{'n':>7}{'meanR':>9}{'dayT':>7}{'held':>7}"
          f"{'bp/day':>9}{'SPYbp/d':>9}{'net':>8}{'netT':>7}{'ann%':>8}")

    for name in names:
        cfg = json.loads(json.dumps(cfg0))
        cfg.setdefault("strategy", {}).setdefault("thresholds", {}) \
           .setdefault(name, {}).update({"stopAtrMult": args.stop,
                                         "targetAtrMult": args.target})
        cfg.setdefault("exits", {})["max_hold_sessions"] = args.hold
        try:
            raw = run_parallel(cfg, syms, spy, args.lo, None, name, 3, None, regs)
        except Exception as e:
            print(f"{name:<18}  FAILED {type(e).__name__}: {e}")
            continue
        if not raw:
            print(f"{name:<18}  NO TRADES")
            continue
        tr = apply_cost(raw, args.bps)
        rows = attach(tr, sessions, spy)
        if len(rows) < 30:
            print(f"{name:<18}{len(rows):>7}   (too few with matched windows)")
            continue
        r = np.array([t["r_multiple"] for t in tr], float)
        hpr = np.array([x["hpr"] for x in rows], float)
        bench = np.array([x["bench"] for x in rows], float)
        held = np.array([x["held"] for x in rows], float)
        rate = hpr.sum() / held.sum()
        brate = bench.sum() / held.sum()
        net = rate - brate
        tc, _ = day_clustered_t(tr)
        print(f"{name:<18}{len(rows):>7}{r.mean():>+9.4f}{tc:>+7.2f}"
              f"{held.mean():>7.1f}{1e4*rate:>9.2f}{1e4*brate:>9.2f}"
              f"{1e4*net:>+8.2f}{dayt(rows,'excess'):>+7.2f}"
              f"{100*((1+net)**252-1):>+8.2f}")

    print("\nnet is the whole verdict: bp/day the book earned minus bp/day the")
    print("index earned over the SAME windows. ann% annualises it on DEPLOYED")
    print("capital. netT is the day-clustered t on per-trade excess (FC-3).")


if __name__ == "__main__":
    main()
