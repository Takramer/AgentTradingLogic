"""
montecarlo.py — bootstrap risk analysis of a backtest's trade distribution.

A single backtest produces ONE ordering of trades — one equity curve, one max
drawdown. That ordering is luck. This module resamples the trade R-multiples
(with replacement) thousands of times under the same fixed-fractional sizing
to answer distributional questions:

  - What range of outcomes should I expect over the next N trades?
  - How deep can drawdowns get before the strategy is "broken"?
  - What is the probability of losing money over a horizon?
  - Is my risk-per-trade too hot for the drawdowns I can stomach?

Usage:
    python -m rhengine.montecarlo                          # backtest then MC
    python -m rhengine.montecarlo --trades-csv backtest_trades.csv
    python -m rhengine.montecarlo --risk-pct 2 --n-trades 200 --paths 5000
"""

import argparse
import json
import random
import statistics

from .costs import resolve_cost_bps


def run_monte_carlo(r_multiples, n_trades=None, paths=20000, risk_pct=1.0,
                    start_equity=10_000.0, seed=1337, ruin_dd_pct=50.0):
    """Bootstrap `paths` sequences of `n_trades` R-multiples (sampled with
    replacement from the observed distribution) under fixed-fractional sizing.

    Returns percentile tables for final equity multiple and max drawdown, plus
    tail probabilities. Deterministic for a given seed."""
    rs = [float(r) for r in r_multiples]
    if not rs:
        return None
    n_trades = n_trades or len(rs)
    rng = random.Random(seed)
    f = risk_pct / 100.0

    finals, maxdds = [], []
    p_loss = p_ruin = 0
    for _ in range(paths):
        eq = 1.0
        peak = 1.0
        dd = 0.0
        for _ in range(n_trades):
            eq *= 1.0 + f * rng.choice(rs)
            if eq > peak:
                peak = eq
            elif peak > 0:
                dd = max(dd, 1.0 - eq / peak)
        finals.append(eq)
        maxdds.append(dd)
        if eq < 1.0:
            p_loss += 1
        if dd >= ruin_dd_pct / 100.0:
            p_ruin += 1

    finals.sort()
    maxdds.sort()

    def pct(sorted_vals, p):
        i = min(len(sorted_vals) - 1, max(0, int(round(p / 100.0 * (len(sorted_vals) - 1)))))
        return sorted_vals[i]

    return {
        "n_observed_trades": len(rs),
        "n_trades_per_path": n_trades,
        "paths": paths,
        "risk_pct": risk_pct,
        "observed_avg_r": round(statistics.fmean(rs), 4),
        "final_equity_mult": {p: round(pct(finals, p), 4)
                              for p in (5, 25, 50, 75, 95)},
        "max_drawdown_pct": {p: round(pct(maxdds, p) * 100, 2)
                             for p in (50, 75, 95, 99)},
        "prob_loss_pct": round(100.0 * p_loss / paths, 1),
        "prob_dd_over_{:.0f}pct".format(ruin_dd_pct): round(100.0 * p_ruin / paths, 1),
        "start_equity": start_equity,
        "_finals": finals,   # sorted; kept for charting
        "_maxdds": maxdds,
    }


def print_report(mc):
    if not mc:
        print("No trades to resample.")
        return
    print("\n" + "=" * 52)
    print("MONTE CARLO ({} paths x {} trades, {:.1f}% risk/trade)".format(
        mc["paths"], mc["n_trades_per_path"], mc["risk_pct"]))
    print("=" * 52)
    print("Observed trades: {}  |  avg R: {:+.3f}".format(
        mc["n_observed_trades"], mc["observed_avg_r"]))
    se = mc["start_equity"]
    fe = mc["final_equity_mult"]
    print("\nFinal equity (start ${:,.0f}):".format(se))
    for p in (5, 25, 50, 75, 95):
        print("  p{:<3} x{:<7} ${:,.0f}".format(p, fe[p], fe[p] * se))
    dd = mc["max_drawdown_pct"]
    print("\nMax drawdown:")
    for p in (50, 75, 95, 99):
        print("  p{:<3} {:.1f}%".format(p, dd[p]))
    ruin_key = [k for k in mc if k.startswith("prob_dd_over_")][0]
    print("\nP(end below start): {:.1f}%".format(mc["prob_loss_pct"]))
    print("P(drawdown > {}%): {:.1f}%".format(
        ruin_key.replace("prob_dd_over_", "").replace("pct", ""), mc[ruin_key]))
    if mc.get("_finals"):
        from .charts import histogram
        print(histogram(mc["_finals"], x_fmt="x{:.2f}",
                        title="\nFinal equity multiple across paths"))
        print(histogram([d * 100 for d in mc["_maxdds"]], x_fmt="{:.0f}%",
                        title="\nMax drawdown across paths"))


def _rs_from_csv(path):
    import csv
    with open(path, newline="") as f:
        return [float(row["r_multiple"]) for row in csv.DictReader(f)
                if row.get("r_multiple")]


def main():
    ap = argparse.ArgumentParser(
        description="Bootstrap Monte Carlo over backtest trade R-multiples.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--trades-csv", default=None,
                    help="reuse a trades CSV instead of re-running the backtest")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--cost-bps", type=float, default=None)
    ap.add_argument("--paths", type=int, default=20000)  # sources: >= 20k runs
    ap.add_argument("--n-trades", type=int, default=None,
                    help="trades per path (default: as many as observed)")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--start-equity", type=float, default=10_000)
    ap.add_argument("--ruin-dd-pct", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--max-dd-tolerance-pct", type=float, default=None,
                    help="acceptance gate (findings 4.2): exit 1 if the worst-5%% "
                         "bootstrap max drawdown exceeds this — lets CI/agents "
                         "reject a config mechanically")
    args = ap.parse_args()
    # Single source of truth for the cost assumption (costs.py / config.json
    # "costs.round_trip_bps"). Audit FC-2: these defaults used to be 0.0 or 5.0
    # per-CLI and the headline artifact was generated at 0 bps.
    args.cost_bps = resolve_cost_bps(args.cost_bps,
                                     getattr(args, "config", None))

    if args.trades_csv:
        rs = _rs_from_csv(args.trades_csv)
    else:
        with open(args.config) as f:
            cfg = json.load(f)
        from .backtest import run_backtest_on
        from .data_local import load_bars_any
        bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
        trades = run_backtest_on(cfg, bars_map, spy_bars,
                                 min_score=args.min_score,
                                 forced_strategy=args.strategy,
                                 cost_bps=args.cost_bps)
        rs = [t["r_multiple"] for t in trades]

    mc = run_monte_carlo(rs, n_trades=args.n_trades, paths=args.paths,
                         risk_pct=args.risk_pct, start_equity=args.start_equity,
                         seed=args.seed, ruin_dd_pct=args.ruin_dd_pct)
    print_report(mc)

    if mc and args.max_dd_tolerance_pct is not None:
        worst5 = mc["max_drawdown_pct"][95]
        if worst5 > args.max_dd_tolerance_pct:
            print("\nREJECT: worst-5% max drawdown {:.1f}% exceeds tolerance {:.1f}% "
                  "— cut --risk-pct or fix the strategy.".format(
                      worst5, args.max_dd_tolerance_pct))
            raise SystemExit(1)
        print("\nACCEPT: worst-5% max drawdown {:.1f}% within tolerance {:.1f}%."
              .format(worst5, args.max_dd_tolerance_pct))


if __name__ == "__main__":
    main()
