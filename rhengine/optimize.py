"""
optimize.py — threshold sweeps with in-sample / out-of-sample honesty checks.

Tuning thresholds on the whole history and reporting the whole-history result
is how backtests lie. Every evaluation here is split-aware:

  OAT sweep (default): vary ONE threshold at a time across its spec range
      (others at defaults), reporting in-sample (train) and out-of-sample
      (test) stats side by side. A value that only wins in-sample is noise.

  --grid: full cartesian sweep over --params (capped by --max-combos).

  --walk-forward: expanding-window fold evaluation. Each fold picks the best
      candidate on data BEFORE the boundary, then banks the winner's trades on
      the unseen segment after it. The pooled out-of-sample trades are the
      honest estimate of what tuning would actually have delivered; the
      per-fold winning params show whether the "best" setting is stable or
      random.

Candidate ranges come from strategies.json (min/max/step per threshold), so
the sweep space always matches what the Nydex UI sliders allow.

Usage:
    python -m rhengine.optimize --strategy residualmomentum
    python -m rhengine.optimize --strategy bollinger --params volMult,adxMax --grid
    python -m rhengine.optimize --strategy residualmomentum --walk-forward 4
"""

import argparse
import itertools
import json

from .backtest import run_backtest_on, _stats, WARMUP_BARS, _iso_date
from .indicators import load_spec, spec_defaults


def _spec_values(strategy, param, max_values=9):
    """Candidate values for one threshold from its spec range (min..max by
    step), thinned evenly to at most max_values."""
    spec = load_spec()["strategies"][strategy]["thresholds"][param]
    lo, hi, step = spec["min"], spec["max"], spec["step"]
    vals = []
    v = lo
    while v <= hi + 1e-9:
        vals.append(round(v, 10))
        v += step
    if len(vals) > max_values:
        idxs = [round(i * (len(vals) - 1) / (max_values - 1)) for i in range(max_values)]
        vals = [vals[i] for i in sorted(set(idxs))]
    # keep the spec default in the candidate set
    d = spec["default"]
    if d not in vals:
        vals.append(d)
        vals.sort()
    return vals


def _cfg_with(cfg, strategy, overrides):
    """Deep-copied cfg with nested per-strategy threshold overrides."""
    cfg2 = json.loads(json.dumps(cfg))
    strat_cfg = cfg2.setdefault("strategy", {})
    thr = strat_cfg.get("thresholds")
    if not isinstance(thr, dict) or any(not isinstance(v, dict) for v in thr.values()):
        thr = {}  # discard flat-shape thresholds; we tune one strategy explicitly
    merged = dict(thr.get(strategy, {}))
    merged.update(overrides)
    thr[strategy] = merged
    strat_cfg["thresholds"] = thr
    return cfg2


def _eligible_dates(spy_bars):
    """ISO dates on which signals can fire (post-warmup)."""
    return [_iso_date(b["time"]) for b in spy_bars[WARMUP_BARS:]]


def _evaluate(cfg, bars_map, spy_bars, strategy, overrides, min_score,
              cost_bps, date_lo=None, date_hi=None):
    cfg2 = _cfg_with(cfg, strategy, overrides)
    trades = run_backtest_on(cfg2, bars_map, spy_bars, min_score=min_score,
                             forced_strategy=strategy,
                             date_lo=date_lo, date_hi=date_hi, cost_bps=cost_bps)
    return trades, _stats(trades)


def _fmt(st):
    if not st:
        return "{:>4} {:>7} {:>7} {:>7}".format(0, "-", "-", "-")
    pf = "inf" if st["profit_factor"] == float("inf") else "{:.2f}".format(st["profit_factor"])
    return "{:>4} {:>6.1f}% {:>+7.3f} {:>7}".format(
        st["n"], st["win_rate"] * 100, st["avg_r"], pf)


_HDR = "{:>4} {:>7} {:>7} {:>7}".format("n", "win%", "avgR", "PF")


def _rank_key(st, min_trades):
    """Sort key: enough trades first, then avg R, then sample size."""
    if not st:
        return (0, float("-inf"), 0)
    return (1 if st["n"] >= min_trades else 0, st["avg_r"], st["n"])


def sweep_oat(cfg, bars_map, spy_bars, strategy, params, min_score, cost_bps,
              split, min_trades, max_values):
    dates = _eligible_dates(spy_bars)
    split_date = dates[int(len(dates) * split)]
    defaults = spec_defaults(strategy)
    print("\nOAT sweep — {} | split {} (train < {} <= test)".format(
        strategy, split, split_date))
    print("candidates per param from strategies.json ranges\n")
    print("  {:<22} {:>8}   IS: {}   OOS: {}".format("param", "value", _HDR, _HDR))

    all_rows = []
    for param in params:
        vals = _spec_values(strategy, param, max_values=max_values)
        rows = []
        for v in vals:
            _, is_st = _evaluate(cfg, bars_map, spy_bars, strategy, {param: v},
                                 min_score, cost_bps, date_hi=split_date)
            _, oos_st = _evaluate(cfg, bars_map, spy_bars, strategy, {param: v},
                                  min_score, cost_bps, date_lo=split_date)
            rows.append((v, is_st, oos_st))
            all_rows.append({"param": param, "value": v,
                             "is_default": v == defaults.get(param),
                             "is": is_st, "oos": oos_st})
        for v, is_st, oos_st in rows:
            mark = " *" if v == defaults.get(param) else "  "
            print("  {:<22} {:>8}{}  IS: {}   OOS: {}".format(
                param, v, mark, _fmt(is_st), _fmt(oos_st)))
        best = max(rows, key=lambda r: _rank_key(r[1], min_trades))
        print("  {:<22} best-IS value {} (check its OOS line before trusting)\n"
              .format("", best[0]))
    return all_rows


def sweep_grid(cfg, bars_map, spy_bars, strategy, params, min_score, cost_bps,
               split, min_trades, max_values, max_combos):
    dates = _eligible_dates(spy_bars)
    split_date = dates[int(len(dates) * split)]
    grids = [(p, _spec_values(strategy, p, max_values=max_values)) for p in params]
    combos = list(itertools.product(*[vs for _, vs in grids]))
    if len(combos) > max_combos:
        raise SystemExit("Grid has {} combos (> --max-combos {}). "
                         "Trim --params or lower --max-values.".format(
                             len(combos), max_combos))
    print("\nGrid sweep — {} | {} combos over {} | split {} ({})".format(
        strategy, len(combos), ",".join(params), split, split_date))
    results = []
    for combo in combos:
        overrides = dict(zip([p for p, _ in grids], combo))
        _, is_st = _evaluate(cfg, bars_map, spy_bars, strategy, overrides,
                             min_score, cost_bps, date_hi=split_date)
        _, oos_st = _evaluate(cfg, bars_map, spy_bars, strategy, overrides,
                              min_score, cost_bps, date_lo=split_date)
        results.append((overrides, is_st, oos_st))
    results.sort(key=lambda r: _rank_key(r[1], min_trades), reverse=True)
    print("\n  {:<44}   IS: {}   OOS: {}".format("params (top 15 by IS avgR)", _HDR, _HDR))
    for overrides, is_st, oos_st in results[:15]:
        label = ",".join("{}={}".format(k, v) for k, v in overrides.items())
        print("  {:<44}   IS: {}   OOS: {}".format(label[:44], _fmt(is_st), _fmt(oos_st)))
    return results


def walk_forward(cfg, bars_map, spy_bars, strategy, params, min_score, cost_bps,
                 folds, min_trades, max_values, grid=False, max_combos=400):
    """Expanding-window: tune on everything before each boundary, bank the
    winner's trades on the next unseen segment."""
    dates = _eligible_dates(spy_bars)
    bounds = [dates[int(len(dates) * i / folds)] for i in range(1, folds)] + [None]

    if grid:
        grids = [(p, _spec_values(strategy, p, max_values=max_values)) for p in params]
        combos = list(itertools.product(*[vs for _, vs in grids]))
        if len(combos) > max_combos:
            raise SystemExit("Grid too large for walk-forward; trim --params.")
        candidates = [dict(zip([p for p, _ in grids], c)) for c in combos]
    else:
        candidates = [{}]  # defaults
        for p in params:
            for v in _spec_values(strategy, p, max_values=max_values):
                candidates.append({p: v})

    pooled = []
    fold_log = []
    print("\nWalk-forward — {} | {} folds | {} candidates".format(
        strategy, folds, len(candidates)))
    for i in range(len(bounds) - 1):
        train_hi = bounds[i]
        test_lo, test_hi = bounds[i], bounds[i + 1]
        best, best_st = None, None
        for cand in candidates:
            _, st = _evaluate(cfg, bars_map, spy_bars, strategy, cand,
                              min_score, cost_bps, date_hi=train_hi)
            if best is None or _rank_key(st, min_trades) > _rank_key(best_st, min_trades):
                best, best_st = cand, st
        test_trades, test_st = _evaluate(cfg, bars_map, spy_bars, strategy, best,
                                         min_score, cost_bps,
                                         date_lo=test_lo, date_hi=test_hi)
        pooled.extend(test_trades)
        label = ",".join("{}={}".format(k, v) for k, v in best.items()) or "(defaults)"
        fold_log.append({"fold": i + 1, "train_hi": train_hi,
                         "test_lo": test_lo, "test_hi": test_hi,
                         "chosen": label, "train": best_st, "test": test_st})
        print("  fold {}: tuned<{}  chose {:<36} train[{}]  test[{}]".format(
            i + 1, train_hi, label[:36], _fmt(best_st), _fmt(test_st)))

    st = _stats(pooled)
    print("\n  POOLED OUT-OF-SAMPLE ({} trades): {}".format(
        len(pooled), _fmt(st) if st else "none"))
    print("  ^ this line is the honest answer; whole-history numbers overfit.")
    return pooled, fold_log


def main():
    ap = argparse.ArgumentParser(
        description="Threshold sweeps with IS/OOS splits and walk-forward folds.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--strategy", required=True,
                    help="strategy to tune (vwap|bollinger|macd|consolidation|"
                         "residualmomentum|pead)")
    ap.add_argument("--params", default=None,
                    help="comma list of thresholds to sweep (default: all in spec)")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--cost-bps", type=float, default=0.0)
    ap.add_argument("--split", type=float, default=0.7,
                    help="train fraction for IS/OOS split (default 0.7)")
    ap.add_argument("--min-trades", type=int, default=15,
                    help="candidates below this many IS trades rank last")
    ap.add_argument("--max-values", type=int, default=9,
                    help="max candidate values per param (default 9)")
    ap.add_argument("--grid", action="store_true", help="full cartesian sweep")
    ap.add_argument("--max-combos", type=int, default=400)
    ap.add_argument("--walk-forward", type=int, default=0, metavar="FOLDS",
                    help="expanding-window fold evaluation (e.g. 4)")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    spec = load_spec()["strategies"]
    if args.strategy not in spec:
        raise SystemExit("Unknown strategy {!r}; choose from {}".format(
            args.strategy, ", ".join(spec)))
    all_params = [p for p in spec[args.strategy]["thresholds"]]
    params = args.params.split(",") if args.params else all_params
    bad = [p for p in params if p not in all_params]
    if bad:
        raise SystemExit("Unknown params {} for {}; spec has {}".format(
            bad, args.strategy, all_params))

    from .data_local import load_bars_any
    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)

    if args.walk_forward:
        walk_forward(cfg, bars_map, spy_bars, args.strategy, params,
                     args.min_score, args.cost_bps, args.walk_forward,
                     args.min_trades, args.max_values, grid=args.grid,
                     max_combos=args.max_combos)
    elif args.grid:
        sweep_grid(cfg, bars_map, spy_bars, args.strategy, params,
                   args.min_score, args.cost_bps, args.split,
                   args.min_trades, args.max_values, args.max_combos)
    else:
        sweep_oat(cfg, bars_map, spy_bars, args.strategy, params,
                  args.min_score, args.cost_bps, args.split,
                  args.min_trades, args.max_values)


if __name__ == "__main__":
    main()
