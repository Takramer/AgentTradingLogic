"""
crossval.py — blocked time-series cross-validation with purge/embargo.

optimize.py's walk-forward is the honest promotion number (never trains on the
future). CV answers a different question: STABILITY. Splitting the history
into k contiguous blocks and evaluating each one shows whether the edge lives
everywhere or in one lucky quarter; rotating a held-out block through a
selection loop shows whether "the best threshold" is the same threshold in
every fold or a coin flip.

Two modes:

  fold evaluation (default): ONE backtest at the current config, trades
      bucketed into k contiguous date blocks -> per-block stats. Cheap
      (single pass). A strategy positive in 4/5 blocks is robust; one
      monster block carrying the mean is regime luck.

  --select --params p1,p2: leave-one-block-out selection. For each block:
      tune on the other blocks (each candidate re-backtested on the
      non-test dates), evaluate the winner on the held-out block, pool.
      NOTE: unlike walk-forward, training uses blocks AFTER the test block
      too — this measures selection stability with maximal data, it is NOT
      the deployment estimate. Use optimize --walk-forward for that.

Purge/embargo: trades held up to max_hold_sessions straddle block borders —
a signal fired just before the test block exits inside it (leakage). Both
modes drop signals within --embargo-days calendar days of a border on the
TRAIN side (fold evaluation trims block edges symmetrically so blocks stay
comparable).

Usage:
    py -m rhengine.crossval --folds 5
    py -m rhengine.crossval --strategy residualmomentum --folds 5
    py -m rhengine.crossval --strategy bollinger --select --params volMult --folds 4
"""

import argparse
import json
from datetime import date, timedelta

from .backtest import run_backtest_on, _stats, _iso_date, WARMUP_BARS
from .optimize import _spec_values, _evaluate, _fmt, _rank_key, _HDR


# ------------------------------------------------------------------ folds --

def fold_bounds(spy_bars, k):
    """k contiguous [lo, hi) ISO-date blocks over the post-warmup window.
    The last block's hi is None (open-ended)."""
    dates = [_iso_date(b["time"]) for b in spy_bars[WARMUP_BARS:]]
    bounds = []
    for i in range(k):
        lo = dates[int(len(dates) * i / k)]
        hi = dates[int(len(dates) * (i + 1) / k)] if i < k - 1 else None
        bounds.append((lo, hi))
    return bounds


def _shift(iso, days):
    if iso is None:
        return None
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def evaluate_folds(cfg, bars_map, spy_bars, k=5, min_score=3,
                   forced_strategy=None, cost_bps=5.0, embargo_days=0):
    """ONE backtest, trades bucketed per block. Returns
    (fold_rows, trades) where fold_rows = [{fold, lo, hi, stats}] and stats
    is the backtest._stats dict (or None for an empty block)."""
    trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=min_score,
                             forced_strategy=forced_strategy, cost_bps=cost_bps)
    rows = []
    for i, (lo, hi) in enumerate(fold_bounds(spy_bars, k)):
        # symmetric embargo trim keeps blocks non-overlapping in exit-time
        lo_t = _shift(lo, embargo_days) if i > 0 else lo
        block = [t for t in trades
                 if t["signal_date"] >= lo_t
                 and (hi is None or t["signal_date"] < hi)]
        rows.append({"fold": i + 1, "lo": lo, "hi": hi or "end",
                     "stats": _stats(block)})
    return rows, trades


def stability_summary(fold_rows):
    """Cross-fold robustness numbers: how many blocks are positive, the
    spread, and the worst block. A pooled mean hiding a terrible block is
    exactly what this exposes."""
    avgs = [r["stats"]["avg_r"] for r in fold_rows if r["stats"]]
    if not avgs:
        return None
    return {
        "folds_scored": len(avgs),
        "folds_positive": sum(1 for a in avgs if a > 0),
        "mean_of_folds": sum(avgs) / len(avgs),
        "worst_fold": min(avgs),
        "best_fold": max(avgs),
        "spread": max(avgs) - min(avgs),
    }


# -------------------------------------------------------------- selection --

def _eval_train(cfg, bars_map, spy_bars, strategy, overrides, min_score,
                cost_bps, test_lo, test_hi, embargo_days):
    """Backtest a candidate on everything OUTSIDE [test_lo, test_hi), with an
    embargo margin on both borders. Two segments (before / after the block)."""
    trades = []
    lo_edge = _shift(test_lo, -embargo_days)
    tr, _ = _evaluate(cfg, bars_map, spy_bars, strategy, overrides,
                      min_score, cost_bps, date_hi=lo_edge)
    trades += tr
    if test_hi is not None:
        hi_edge = _shift(test_hi, embargo_days)
        tr, _ = _evaluate(cfg, bars_map, spy_bars, strategy, overrides,
                          min_score, cost_bps, date_lo=hi_edge)
        trades += tr
    return trades, _stats(trades)


def cv_select(cfg, bars_map, spy_bars, strategy, params, k=4, min_score=3,
              cost_bps=5.0, embargo_days=15, min_trades=15, max_values=9,
              verbose=True):
    """Leave-one-block-out candidate selection (OAT candidate set: defaults
    plus each single-param override). Returns (pooled_oos_trades, fold_log)."""
    candidates = [{}]
    for p in params:
        for v in _spec_values(strategy, p, max_values=max_values):
            candidates.append({p: v})

    pooled, fold_log = [], []
    bounds = fold_bounds(spy_bars, k)
    if verbose:
        print(f"\nCV selection — {strategy} | {k} folds | "
              f"{len(candidates)} candidates | embargo {embargo_days}d")
    for i, (lo, hi) in enumerate(bounds):
        best, best_st = None, None
        for cand in candidates:
            _, st = _eval_train(cfg, bars_map, spy_bars, strategy, cand,
                                min_score, cost_bps, lo, hi, embargo_days)
            if best is None or _rank_key(st, min_trades) > _rank_key(best_st, min_trades):
                best, best_st = cand, st
        test_tr, test_st = _evaluate(cfg, bars_map, spy_bars, strategy, best,
                                     min_score, cost_bps,
                                     date_lo=lo, date_hi=hi)
        pooled.extend(test_tr)
        label = ",".join(f"{k2}={v}" for k2, v in best.items()) or "(defaults)"
        fold_log.append({"fold": i + 1, "lo": lo, "hi": hi or "end",
                         "chosen": label, "train": best_st, "test": test_st})
        if verbose:
            print(f"  fold {i+1} [{lo} .. {hi or 'end'}): chose {label:<34}"
                  f" train[{_fmt(best_st)}]  test[{_fmt(test_st)}]")

    st = _stats(pooled)
    if verbose:
        print(f"\n  POOLED HELD-OUT ({len(pooled)} trades): "
              f"{_fmt(st) if st else 'none'}")
        chosen = {f['chosen'] for f in fold_log}
        print(f"  distinct winners across folds: {len(chosen)} "
              f"({'stable' if len(chosen) <= 2 else 'UNSTABLE — the best value is noise'})")
        print("  (CV trains on future blocks; use optimize --walk-forward for "
              "the deployment estimate)")
    return pooled, fold_log


# -------------------------------------------------------------------- CLI --

def main():
    ap = argparse.ArgumentParser(
        description="Blocked time-series CV: fold stability + LOBO selection.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--strategy", default=None,
                    help="force one strategy (required for --select)")
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--embargo-days", type=int, default=15,
                    help="calendar-day purge margin at block borders "
                         "(~max_hold_sessions in trading days; default 15)")
    ap.add_argument("--select", action="store_true",
                    help="leave-one-block-out candidate selection")
    ap.add_argument("--params", default=None,
                    help="comma list of thresholds for --select")
    ap.add_argument("--min-trades", type=int, default=15)
    ap.add_argument("--max-values", type=int, default=9)
    ap.add_argument("--max-tickers", type=int, default=0,
                    help="cap the universe for a faster run (0 = all)")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)
    from .data_local import load_bars_any
    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
    if args.max_tickers:
        bars_map = dict(list(bars_map.items())[:args.max_tickers])
        print(f"Universe capped at {len(bars_map)} tickers")

    if args.select:
        if not args.strategy or not args.params:
            raise SystemExit("--select needs --strategy and --params")
        cv_select(cfg, bars_map, spy_bars, args.strategy,
                  args.params.split(","), k=args.folds,
                  min_score=args.min_score, cost_bps=args.cost_bps,
                  embargo_days=args.embargo_days, min_trades=args.min_trades,
                  max_values=args.max_values)
        return

    rows, trades = evaluate_folds(cfg, bars_map, spy_bars, k=args.folds,
                                  min_score=args.min_score,
                                  forced_strategy=args.strategy,
                                  cost_bps=args.cost_bps,
                                  embargo_days=args.embargo_days)
    print(f"\nBlocked fold evaluation — {args.folds} folds | "
          f"{len(trades)} trades | strategy: {args.strategy or 'auto-regime'}")
    print(f"  {'fold':<6} {'window':<26} {_HDR}")
    for r in rows:
        print(f"  {r['fold']:<6} {r['lo']} .. {r['hi']:<12} {_fmt(r['stats'])}")
    st = stability_summary(rows)
    if st:
        print(f"\n  {st['folds_positive']}/{st['folds_scored']} folds positive"
              f"  |  mean-of-folds avgR {st['mean_of_folds']:+.3f}"
              f"  |  worst {st['worst_fold']:+.3f}  best {st['best_fold']:+.3f}")
        if st["folds_positive"] * 2 <= st["folds_scored"]:
            print("  VERDICT: edge is NOT consistent across time blocks.")
        elif st["worst_fold"] < -0.05:
            print("  VERDICT: positive on average but one block is badly "
                  "negative — check what regime that was.")
        else:
            print("  VERDICT: edge is broadly consistent across blocks.")


if __name__ == "__main__":
    main()
