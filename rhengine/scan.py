"""
Live watchlist scanner — score today's setups across the config watchlist.

    py -m rhengine.scan                     # auto: regime-routed strategy
    py -m rhengine.scan --strategy pead     # force one strategy
    py -m rhengine.scan --all               # every non-retired strategy
    py -m rhengine.scan --min-score 5 --top 20 --csv scan_results.csv

Uses whatever data_source config.json names (yahoo needs no keys).
"""
import argparse
import csv
import json
import sys

from .data_local import load_bars_any
from .indicators import (
    classify_spy_regime,
    resolve_thresholds,
    score_all_tickers,
    select_strategy_for_regime,
    strategy_status,
    autonomous_strategies,
    load_spec,
)


def main():
    ap = argparse.ArgumentParser(description="Scan the watchlist for setups")
    ap.add_argument("--strategy", help="force one strategy key")
    ap.add_argument("--all", action="store_true",
                    help="scan every non-retired strategy")
    ap.add_argument("--min-score", type=int, default=4)
    ap.add_argument("--top", type=int, default=25, help="rows to print")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--csv", help="write full results to CSV")
    args = ap.parse_args()

    with open("config.json") as f:
        cfg = json.load(f)

    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
    regime = classify_spy_regime(spy_bars)

    if args.all:
        spec = load_spec()
        strategies = [k for k in spec["strategies"]
                      if strategy_status(k, spec) != "retired"]
        if not strategies:
            print("\nNo non-retired strategy. The legacy sleeves are retired; "
                  "pass --strategy <name> to reproduce one.")
            return
    elif args.strategy:
        # Explicit --strategy is the reproduction path. A retired sleeve still
        # scores here so the rejection can be re-run. Auto-routing is the only
        # path that requires status == autonomous.
        strategies = [args.strategy]
    else:
        auto = select_strategy_for_regime(regime)
        if auto is None:
            print("\nNo strategy is cleared for autonomous trading in the "
                  "'{}' regime — nothing to scan.".format(regime))
            print("Autonomous set: {}".format(
                ", ".join(autonomous_strategies()) or "(empty)"))
            print("Pick one explicitly with --strategy to scan manually, "
                  "or --all for every non-retired strategy.")
            return
        strategies = [auto]

    results = []
    for strat in strategies:
        thresholds = resolve_thresholds(cfg, strat)
        results += score_all_tickers(
            bars_map, strat, thresholds, min_score=args.min_score,
            spy_bars=spy_bars, bear_regime_only=cfg.get("bear_regime_only"),
            spy_regime=regime)
    results.sort(key=lambda s: (s["score"], s.get("weighted_score", s["score"]),
                                s.get("strength", 0.0)), reverse=True)

    print("\nSPY regime: {} | strategies: {} | {} tickers scanned | {} setups >= score {}"
          .format(regime, ",".join(strategies), len(bars_map), len(results),
                  args.min_score))
    fmt = "{:<7} {:<17} {:>5} {:>8} {:>9} {:>10}"
    print(fmt.format("TICKER", "STRATEGY", "SCORE", "WEIGHTED", "STRENGTH", "CLOSE"))
    for s in results[:args.top]:
        print(fmt.format(s["ticker"], s.get("strategy", strategies[0]), s["score"],
                         "{:.2f}".format(s.get("weighted_score", s["score"])),
                         "{:.3f}".format(s.get("strength", 0.0)),
                         "{:.2f}".format(s.get("close", s.get("entry", 0.0)))))

    if args.csv and results:
        keys = sorted({k for s in results for k in s if not isinstance(s[k], (list, dict))})
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
        print("\nwrote {} rows -> {}".format(len(results), args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
