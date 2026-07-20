"""
ic.py — cross-sectional Information Coefficient (IC) and t-stats.

The backtest asks "did the trades make money?"; IC asks the sharper question
"does a HIGHER score predict a BETTER outcome among the candidates available
that day?" — the classic Grinold/Kahn signal-quality measure. A strategy can
have positive avg R while its score adds no ranking information (all edge in
the exit engine), or negative avg R while the score ranks well (edge eaten by
costs/geometry). IC separates those cases, so it tells you WHERE to work.

Method:
  1. Harvest signals at the engine's emission floor (score >= 2) with
     `run_backtest_on` — each signal keeps its score, weighted score, the
     exit-engine R-multiple, and forward returns at fixed horizons
     (next-open -> close h bars later), both raw and SPY-excess.
  2. For each signal date with a wide-enough cross-section, Spearman
     rank-correlate the signal against the outcome -> one IC per day.
  3. Aggregate: mean IC, t-stat of the daily series, IC IR, %positive days.

Reading the numbers (daily-horizon equity conventions):
  |mean IC| ~0.02 weak, ~0.05 useful, >0.10 suspicious (check for leakage).
  t-stat >= 2 on the daily IC series = ranking skill distinguishable from
  noise. For horizons > 1 day the observations overlap, inflating t by about
  sqrt(h) — the report shows the overlap-adjusted t alongside the raw one;
  trust the adjusted column.

Usage:
    py -m rhengine.ic                                # auto regime, all horizons
    py -m rhengine.ic --strategy residualmomentum --max-tickers 300
    py -m rhengine.ic --signal weighted_score --csv ic_panel.csv
"""

import argparse
import json
import math

import numpy as np
import pandas as pd

from .backtest import run_backtest_on, _iso_date

HORIZONS = (1, 5, 10, 20)


# ---------------------------------------------------------------- harvest --

def harvest_signals(cfg, bars_map, spy_bars, min_score=2, forced_strategy=None,
                    cost_bps=0.0, horizons=HORIZONS, verbose=True):
    """One backtest pass at the emission floor, then attach forward returns.

    Returns a DataFrame with one row per emitted signal:
      ticker, strategy, regime, signal_date, score, weighted_score,
      r_multiple, fwd_{h} (raw next-open->close return, decimal),
      exc_{h} (fwd minus SPY same-window return).
    Horizons that would run off the end of a ticker's history are NaN.
    """
    trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=min_score,
                             forced_strategy=forced_strategy, cost_bps=cost_bps)
    if verbose:
        print(f"Harvested {len(trades)} signals at score>={min_score}")
    if not trades:
        return pd.DataFrame()

    # Per-ticker ISO-date -> bar-index maps for forward-return lookup.
    idx_of = {}
    for ticker, bars in bars_map.items():
        idx_of[ticker] = {_iso_date(b["time"]): i for i, b in enumerate(bars)}
    spy_idx = {_iso_date(b["time"]): i for i, b in enumerate(spy_bars)}

    rows = []
    for tr in trades:
        bars = bars_map.get(tr["ticker"])
        t = idx_of.get(tr["ticker"], {}).get(tr["signal_date"])
        s = spy_idx.get(tr["signal_date"])
        if bars is None or t is None or t + 1 >= len(bars):
            continue
        entry = bars[t + 1]["open"]
        if entry <= 0:
            continue
        row = {k: tr[k] for k in ("ticker", "strategy", "regime", "signal_date",
                                  "score", "weighted_score", "r_multiple")}
        for h in horizons:
            fwd = exc = float("nan")
            if t + 1 + h < len(bars):
                fwd = bars[t + 1 + h]["close"] / entry - 1.0
                if s is not None and s + 1 + h < len(spy_bars):
                    spy_entry = spy_bars[s + 1]["open"]
                    if spy_entry > 0:
                        exc = fwd - (spy_bars[s + 1 + h]["close"] / spy_entry - 1.0)
            row[f"fwd_{h}"] = fwd
            row[f"exc_{h}"] = exc
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- IC --

def _spearman(x, y):
    """Rank correlation with average-rank tie handling (no scipy needed).
    Discrete scores tie constantly — average ranks keep the estimate sane."""
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def daily_ic(panel, signal="weighted_score", target="exc_5", min_names=8):
    """Per-date cross-sectional Spearman IC. Days with fewer than `min_names`
    scored candidates (or a constant signal) are skipped — a 3-name rank
    correlation is noise, not information.

    Returns a pandas Series (index = signal_date as datetime, values = IC)."""
    out = {}
    df = panel.dropna(subset=[signal, target])
    for date, g in df.groupby("signal_date"):
        if len(g) < min_names:
            continue
        ic = _spearman(g[signal].to_numpy(), g[target].to_numpy())
        if not math.isnan(ic):
            out[date] = ic
    s = pd.Series(out, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def ic_summary(ic_series, horizon=1):
    """Aggregate a daily IC series. `horizon` drives the overlap adjustment:
    h-day forward returns make consecutive daily ICs overlap ~h-fold, so the
    naive t-stat is inflated by about sqrt(h)."""
    n = len(ic_series)
    if n < 3:
        return None
    mean = float(ic_series.mean())
    std = float(ic_series.std(ddof=1))
    t = mean / (std / math.sqrt(n)) if std > 0 else float("nan")
    return {
        "n_days": n,
        "mean_ic": mean,
        "std_ic": std,
        "t_stat": t,
        "t_stat_adj": t / math.sqrt(max(1, horizon)),
        "ic_ir": mean / std if std > 0 else float("nan"),
        "pct_positive": float((ic_series > 0).mean() * 100),
    }


def ic_table(panel, signal="weighted_score", horizons=HORIZONS, min_names=8,
             kind="exc"):
    """IC summary per horizon (kind = 'exc' SPY-excess | 'fwd' raw) plus the
    exit-engine target (r_multiple, horizon 1 for adjustment purposes).
    Returns a DataFrame indexed by target name."""
    rows = {}
    for h in horizons:
        s = daily_ic(panel, signal=signal, target=f"{kind}_{h}",
                     min_names=min_names)
        st = ic_summary(s, horizon=h)
        if st:
            rows[f"{kind}_{h}d"] = st
    s = daily_ic(panel, signal=signal, target="r_multiple", min_names=min_names)
    st = ic_summary(s, horizon=1)
    if st:
        rows["r_multiple"] = st
    return pd.DataFrame(rows).T


def score_tier_returns(panel, horizons=HORIZONS, kind="exc"):
    """Mean outcome by integer score tier — the monotonicity view. If 4/4
    doesn't beat 3/4 here, the extra leg isn't ranking anything."""
    cols = [f"{kind}_{h}" for h in horizons] + ["r_multiple"]
    g = panel.groupby("score")[cols].agg(["mean", "count"])
    return g


# -------------------------------------------------------------------- CLI --

def _print_table(df, title):
    print(f"\n{title}")
    if df is None or df.empty:
        print("  (not enough cross-sectional data)")
        return
    with pd.option_context("display.float_format", "{:+.4f}".format,
                           "display.width", 120):
        print(df.to_string())


def main():
    ap = argparse.ArgumentParser(
        description="Cross-sectional IC / t-stats of the engine's scores.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=2,
                    help="emission floor for the harvest (default 2 — you "
                         "want the full scored cross-section, not just trades)")
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--signal", default="weighted_score",
                    choices=("weighted_score", "score"))
    ap.add_argument("--horizons", default="1,5,10,20")
    ap.add_argument("--min-names", type=int, default=8,
                    help="minimum candidates per day for a valid daily IC")
    ap.add_argument("--cost-bps", type=float, default=0.0)
    ap.add_argument("--max-tickers", type=int, default=0,
                    help="cap the universe (0 = all); the event-loop harvest "
                         "is slow at full-market scale")
    ap.add_argument("--csv", default=None,
                    help="write the per-signal panel (with forward returns) "
                         "to this CSV for notebook reuse")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)
    horizons = tuple(int(h) for h in args.horizons.split(","))

    from .data_local import load_bars_any
    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
    if args.max_tickers:
        bars_map = dict(list(bars_map.items())[:args.max_tickers])
        print(f"Universe capped at {len(bars_map)} tickers")

    panel = harvest_signals(cfg, bars_map, spy_bars, min_score=args.min_score,
                            forced_strategy=args.strategy,
                            cost_bps=args.cost_bps, horizons=horizons)
    if panel.empty:
        print("No signals harvested.")
        return
    if args.csv:
        panel.to_csv(args.csv, index=False)
        print(f"Wrote {len(panel)} signal rows to {args.csv}")

    print("\n" + "=" * 64)
    print(f"INFORMATION COEFFICIENT — signal: {args.signal}  "
          f"(t_stat_adj corrects horizon overlap; that's the honest column)")
    print("=" * 64)
    _print_table(ic_table(panel, signal=args.signal, horizons=horizons,
                          min_names=args.min_names, kind="exc"),
                 "IC vs SPY-EXCESS forward returns:")
    _print_table(ic_table(panel, signal=args.signal, horizons=horizons,
                          min_names=args.min_names, kind="fwd"),
                 "IC vs RAW forward returns:")

    for strat, g in panel.groupby("strategy"):
        _print_table(ic_table(g, signal=args.signal, horizons=horizons,
                              min_names=args.min_names, kind="exc"),
                     f"IC by strategy — {strat} ({len(g)} signals):")

    _print_table(score_tier_returns(panel, horizons=horizons, kind="exc"),
                 "Mean SPY-excess return by score tier (monotone = score ranks):")


if __name__ == "__main__":
    main()
