"""
nulltest.py — random-entry permutation test: does the ENTRY signal beat luck?

montecarlo.py resamples the strategy's own trades, so it measures outcome
variance GIVEN the edge — it cannot say whether the edge exists. This module
tests exactly that. It runs the real backtest once, then builds a null
distribution by re-running the same number of trades with RANDOM entries while
keeping everything else identical: the same exit engine (stop/target/breakeven
trail/chandelier/time stop), the same per-trade stop/target geometry (each null
trade borrows a real trade's stop%% and target%% distances), the same costs,
the same date window, the same universe.

If random entries produce avg R / profit factor as good as the real strategy,
the entries add nothing — the performance is exit engine + market drift.

  p-value = fraction of null paths whose stat >= the real stat
            (with the +1 correction: (1 + n_ge) / (paths + 1))

Modes (what gets randomized):
  both     random ticker AND random date       — total entry skill vs drift (default)
  tickers  keep each trade's date, random ticker — cross-sectional selection skill
  dates    keep each trade's ticker, random date — timing skill

Usage:
    py -m rhengine.nulltest                          # auto regime, 400d, 1000 paths
    py -m rhengine.nulltest --strategy bollinger --paths 2000
    py -m rhengine.nulltest --mode tickers --cost-bps 5
"""

import argparse
import json
import random

from .backtest import (
    load_bars, run_backtest_on, simulate_long, _cost_r, _stats,
    _iso_date, _max_hold, WARMUP_BARS,
)


def _atr_series(bars, period=14):
    """Simple ATR (mean true range) per index; None until enough history."""
    n = len(bars)
    atr = [None] * n
    trs = [0.0] * n
    for i in range(n):
        b = bars[i]
        if i == 0:
            trs[i] = b["high"] - b["low"]
        else:
            pc = bars[i - 1]["close"]
            trs[i] = max(b["high"] - b["low"], abs(b["high"] - pc),
                         abs(b["low"] - pc))
    run = 0.0
    for i in range(n):
        run += trs[i]
        if i >= period:
            run -= trs[i - period]
            atr[i] = run / period
        elif i == period - 1:
            atr[i] = run / period
    return atr


def build_candidates(bars_map, date_lo, date_hi):
    """Valid random entry points inside the real signal window.
    Returns (pooled [(ticker, t)], by_date {date: [(ticker, t)]},
             by_ticker {ticker: [t]}, dates {(ticker, t): date})."""
    pooled, by_date, by_ticker, dates = [], {}, {}, {}
    for ticker, bars in bars_map.items():
        n = len(bars)
        for t in range(WARMUP_BARS, n - 1):
            d = _iso_date(bars[t]["time"])
            if d < date_lo or d > date_hi:
                continue
            pooled.append((ticker, t))
            by_date.setdefault(d, []).append((ticker, t))
            by_ticker.setdefault(ticker, []).append(t)
            dates[(ticker, t)] = d
    return pooled, by_date, by_ticker, dates


def run_null_paths(cfg, bars_map, real_trades, paths=1000, mode="both",
                   cost_bps=0.0, seed=1337, verbose=True):
    """Null distribution of (avg_r, profit_factor, win_rate) over `paths`
    random-entry replays of the real trade list."""
    date_lo = min(t["signal_date"] for t in real_trades)
    date_hi = max(t["signal_date"] for t in real_trades)
    pooled, by_date, by_ticker, _ = build_candidates(bars_map, date_lo, date_hi)
    if not pooled:
        raise RuntimeError("no candidate entry bars in the real signal window")

    # Per-trade geometry borrowed from the real trades (stop/target distances
    # as a fraction of price, plus strategy for the max-hold rule).
    geoms = []
    for tr in real_trades:
        e = tr["entry"]
        if e <= 0:
            continue
        geoms.append({
            "stop_pct": (e - tr["stop"]) / e,
            "target_pct": (tr["target"] - e) / e,
            "strategy": tr["strategy"],
            "date": tr["signal_date"],
            "ticker": tr["ticker"],
        })

    atr_cache = {}

    def atr_at(ticker, t):
        if ticker not in atr_cache:
            atr_cache[ticker] = _atr_series(bars_map[ticker])
        return atr_cache[ticker][t]

    exits_cfg = cfg.get("exits", {})
    rng = random.Random(seed)
    results = []
    for p in range(paths):
        rs = []
        for g in geoms:
            sim = None
            for _try in range(10):  # resample degenerate fills
                if mode == "tickers":
                    cands = by_date.get(g["date"])
                    if not cands:
                        break
                    ticker, t = rng.choice(cands)
                elif mode == "dates":
                    ts = by_ticker.get(g["ticker"])
                    if not ts:
                        break
                    ticker, t = g["ticker"], rng.choice(ts)
                else:
                    ticker, t = rng.choice(pooled)
                bars = bars_map[ticker]
                ref = bars[t]["close"]
                stop = ref * (1.0 - g["stop_pct"])
                target = ref * (1.0 + g["target_pct"])
                sim = simulate_long(bars, t, stop, target,
                                    _max_hold(g["strategy"], ticker, cfg),
                                    atr=atr_at(ticker, t),
                                    exits_cfg=exits_cfg)
                if sim is not None:
                    entry = bars[t + 1]["open"]
                    rs.append(sim[2] - _cost_r(entry, stop, cost_bps))
                    break
        st = _stats([{"r_multiple": r} for r in rs])
        if st:
            results.append(st)
        if verbose and (p + 1) % 100 == 0:
            print(f"  null path {p + 1}/{paths}")
    return results


def _pctile(sorted_vals, q):
    if not sorted_vals:
        return float("nan")
    i = min(len(sorted_vals) - 1, max(0, int(q * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def summarize_null(real_st, null_stats, paths):
    def dist(key):
        vals = sorted(s[key] for s in null_stats
                      if s[key] != float("inf"))
        return vals

    print("\n" + "=" * 60)
    print("RANDOM-ENTRY NULL TEST")
    print("=" * 60)
    print(f"Real:  n={real_st['n']}  win%={real_st['win_rate']*100:.1f}  "
          f"avgR={real_st['avg_r']:+.3f}  PF={real_st['profit_factor']:.2f}")
    print(f"Null paths: {len(null_stats)}")
    print(f"\n  {'stat':<8} {'null p5':>9} {'null p50':>9} {'null p95':>9} "
          f"{'real':>9} {'p-value':>9}")
    out = {}
    for key, real_v in (("avg_r", real_st["avg_r"]),
                        ("profit_factor", real_st["profit_factor"]),
                        ("win_rate", real_st["win_rate"])):
        vals = dist(key)
        n_ge = sum(1 for v in vals if v >= real_v)
        pval = (1 + n_ge) / (len(null_stats) + 1)
        out[key] = pval
        print(f"  {key:<8.8} {_pctile(vals, 0.05):>9.3f} "
              f"{_pctile(vals, 0.50):>9.3f} {_pctile(vals, 0.95):>9.3f} "
              f"{real_v:>9.3f} {pval:>9.4f}")

    p = out["avg_r"]
    if p <= 0.01:
        verdict = "STRONG: entries beat random at p<=0.01 — real entry edge."
    elif p <= 0.05:
        verdict = "PASS: entries beat random at p<=0.05."
    elif p <= 0.20:
        verdict = ("WEAK: entries only marginally beat random — much of the "
                   "PF is exit engine + drift.")
    else:
        verdict = ("FAIL: random entries do as well — the entry signal adds "
                   "no measurable edge over the exit engine + market drift.")
    print(f"\nVerdict (on avg R): {verdict}\n")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Random-entry permutation test of the Nydex entry signals.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--strategy", default=None,
                    help="force a single strategy instead of regime-auto")
    ap.add_argument("--paths", type=int, default=1000)
    ap.add_argument("--mode", choices=("both", "tickers", "dates"),
                    default="both")
    ap.add_argument("--cost-bps", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--max-tickers", type=int, default=0,
                    help="cap the universe for a faster run (0 = all); the "
                         "event-loop backtest is slow at full-market scale")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    bars_map, spy_bars = load_bars(cfg, lookback_days=args.lookback_days)
    if args.max_tickers:
        bars_map = dict(list(bars_map.items())[:args.max_tickers])
        print(f"Universe capped at {len(bars_map)} tickers")
    print("Running real backtest...")
    real = run_backtest_on(cfg, bars_map, spy_bars, min_score=args.min_score,
                           forced_strategy=args.strategy,
                           cost_bps=args.cost_bps)
    real_st = _stats(real)
    if not real_st:
        print("No real trades — nothing to test.")
        return
    print(f"Real trades: {real_st['n']}  avgR={real_st['avg_r']:+.3f}  "
          f"PF={real_st['profit_factor']:.2f}")
    print(f"Simulating {args.paths} random-entry paths (mode={args.mode})...")
    null_stats = run_null_paths(cfg, bars_map, real, paths=args.paths,
                                mode=args.mode, cost_bps=args.cost_bps,
                                seed=args.seed)
    summarize_null(real_st, null_stats, args.paths)


if __name__ == "__main__":
    main()
