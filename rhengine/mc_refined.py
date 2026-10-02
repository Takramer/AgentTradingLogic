"""
mc_refined.py — trade harvest + Monte Carlo for the REFINED strategy set.

The 2026-07-18 full-matrix ablations (ablate.py + ablate_vbt.py) refined each
strategy down to the rule subset with real OOS edge:

  residualmomentum  RA-RES only            (all 3 confirmation legs are drag)
  macd              CROSS + STAGE=sub0     (EMA50/RSI dead weight)
  bollinger         full 4-leg set, CHOPPY REGIME ONLY (its routing slot;
                    all-regime ablation is misleading for a routed strategy)
  vwap              AVWAP + VOL + RSI-RESET (EMA-STACK mildly dead)
  pead              event gate only        (no-rules baseline beats every combo)
  consolidation     RETIRED (COMP leg toxic) — excluded

This module re-derives those exact configurations with the vectorized VBT
backtest from ablate_vbt (same fills, exits, costs, guards), extracts
per-trade R-multiples (trade return / stop-distance fraction at entry), and
bootstraps equity paths per strategy and for the pooled set. Unlike
montecarlo.run_monte_carlo it keeps the FULL path matrix so a notebook can
draw percentile fan charts, not just endpoint tables.

Usage:
    py -m rhengine.mc_refined --csv tests/results/refined_trades.csv
    py -m rhengine.mc_refined --lookback-days 730 --cost-bps 5
"""

import argparse
import json

import numpy as np
import pandas as pd

from .costs import resolve_cost_bps

from .ablate_vbt import RULE_BUILDERS, Market
from .indicators import classify_spy_regime, resolve_thresholds

# strategy -> (rule spec, regime restriction or None)
# rule spec entries are "LABEL" or "LABEL=state" matching ablate_vbt rule labels
REFINED_SET = {
    "residualmomentum": (["RA-RES"], None),
    "macd": (["CROSS", "STAGE=sub0"], None),
    "bollinger": (["BB-TAG", "CLOSE-IN", "RSI-OS", "VOL"], "choppy"),
    "vwap": (["AVWAP", "VOL", "RSI-RESET"], None),
    "pead": ([], None),          # hard event gate stays on; no scored legs
}


def spy_regime_series(spy_bars, index):
    """Per-date SPY regime (uptrend|downtrend|choppy), classified with only the
    bars available at each date — same prefix logic as the event-loop engine."""
    dates = (pd.to_datetime([b["time"] for b in spy_bars], unit="ms", utc=True)
             .tz_convert("America/New_York").normalize().tz_localize(None))
    regimes = [classify_spy_regime(spy_bars[: i + 1]) for i in range(len(spy_bars))]
    return (pd.Series(regimes, index=dates)
            .reindex(index).ffill().fillna("choppy"))


def _select_rules(rules, spec):
    """Pick the (label, state) frames named in `spec` from a builder's rules."""
    frames = []
    by_label = {lbl: dict(states) for lbl, states in rules}
    for item in spec:
        lbl, _, state = item.partition("=")
        frames.append((item, by_label[lbl][state]))
    return frames


def harvest_refined_trades(cfg, bars_map, spy_bars, cost_bps=5.0, verbose=True):
    """Backtest each refined configuration; return one DataFrame of trades with
    per-trade R-multiples (columns: strategy, rules, ticker, entry_date,
    exit_date, ret_pct, pnl, r_multiple)."""
    import vectorbt as vbt

    m = Market(bars_map, spy_bars)
    regime = spy_regime_series(spy_bars, m.close.index)
    exits_cfg = cfg.get("exits", {})
    max_hold = int(exits_cfg.get("max_hold_sessions", 10))
    min_stop_pct = float(exits_cfg.get("min_stop_pct", 0.003))

    out = []
    for strategy, (spec, regime_only) in REFINED_SET.items():
        th = resolve_thresholds(cfg, strategy)
        rules, gate, target_level = RULE_BUILDERS[strategy](m, th)
        stop_mult = float(th.get("stopAtrMult", 1.0))
        target_mult = float(th.get("targetAtrMult", 2.0))

        stop_level = (m.close - stop_mult * m.atr).shift(1)
        if target_level is None:
            target_level = m.close + target_mult * m.atr
        target_level = target_level.shift(1)
        sl_frac = ((m.open - stop_level) / m.open).clip(lower=1e-4)
        tp_frac = ((target_level - m.open) / m.open).clip(lower=1e-4)
        fillable = ((m.open - stop_level) > m.open * min_stop_pct) & m.open.notna()

        # .copy(): in-place &= below must never mutate the shared m.valid
        sig = (m.valid if gate is None else (m.valid & gate.fillna(False))).copy()
        if regime_only:
            sig = sig & (regime == regime_only).to_numpy()[:, None]
        for _, frame in _select_rules(rules, spec):
            sig &= frame.fillna(False)

        entries = sig.shift(1, fill_value=False) & fillable
        time_exits = sig.shift(1 + max_hold, fill_value=False)
        pf = vbt.Portfolio.from_signals(
            close=m.close, entries=entries, exits=time_exits,
            price=m.open, open=m.open, high=m.high, low=m.low,
            sl_stop=sl_frac.values, tp_stop=tp_frac.values,
            stop_entry_price="fillprice", direction="longonly",
            # vectorbt charges `fees` on EVERY order — entry and exit both — so
            # a round-trip figure must be halved here or it is billed twice.
            # (2026-07-27: it was not, mirroring the same units bug in
            # backtest._cost_r. `cost_bps` is ROUND TRIP; see costs.py.)
            fees=cost_bps / 2.0 / 1e4, init_cash=10_000.0,
            size=100.0, size_type="value", freq="1D")

        recs = pf.trades.records
        if not isinstance(recs, pd.DataFrame):
            recs = pd.DataFrame(recs)
        recs = recs[~recs["pnl"].isna()]          # drop unvaluable open trades
        ei = recs["entry_idx"].values.astype(int)
        ci = recs["col"].values.astype(int)
        risk_frac = sl_frac.values[ei, ci]        # stop distance as entry frac
        label = "+".join(spec) or "(event gate only)"
        out.append(pd.DataFrame({
            "strategy": strategy,
            "rules": label,
            "ticker": m.close.columns[ci],
            "entry_date": m.close.index.values[ei],
            "exit_date": m.close.index.values[recs["exit_idx"].values.astype(int)],
            "ret_pct": recs["return"].values * 100,
            "pnl": recs["pnl"].values,
            "r_multiple": recs["return"].values / risk_frac,
        }))
        if verbose:
            t = out[-1]
            print("  {:<18} {:<28} n={:>6}  avgR={:+.3f}".format(
                strategy, label, len(t), t["r_multiple"].mean()))
    return pd.concat(out, ignore_index=True)


def run_mc_paths(r_multiples, n_trades=250, paths=20000, risk_pct=1.0,
                 seed=1337, ruin_dd_pct=50.0):
    """Vectorized bootstrap: sample (paths x n_trades) R-multiples with
    replacement, compound under fixed-fractional sizing. Returns the full
    equity-path matrix plus endpoint/drawdown distributions."""
    rs = np.asarray(r_multiples, dtype=float)
    rng = np.random.default_rng(seed)
    f = risk_pct / 100.0
    eq = np.cumprod(1.0 + f * rng.choice(rs, size=(paths, n_trades)), axis=1)
    peak = np.maximum.accumulate(np.maximum(eq, 1.0), axis=1)
    maxdd = (1.0 - eq / peak).max(axis=1)
    finals = eq[:, -1]
    return {
        "equity": eq, "finals": finals, "maxdd": maxdd,
        "n_observed": len(rs), "n_trades": n_trades, "paths": paths,
        "risk_pct": risk_pct, "avg_r": float(rs.mean()),
        "final_pct": {p: float(np.percentile(finals, p))
                      for p in (5, 25, 50, 75, 95)},
        "maxdd_pct": {p: float(np.percentile(maxdd, p) * 100)
                      for p in (50, 75, 95, 99)},
        "prob_loss": float((finals < 1.0).mean() * 100),
        "prob_ruin": float((maxdd >= ruin_dd_pct / 100).mean() * 100),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Harvest refined-set trades (VBT) and write a trades CSV "
                    "for Monte Carlo analysis.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=730)
    ap.add_argument("--cost-bps", type=float, default=None)
    ap.add_argument("--max-tickers", type=int, default=0)
    ap.add_argument("--csv", default="tests/results/refined_trades.csv")
    args = ap.parse_args()
    # Single source of truth for the cost assumption (costs.py / config.json
    # "costs.round_trip_bps"). Audit FC-2: these defaults used to be 0.0 or 5.0
    # per-CLI and the headline artifact was generated at 0 bps.
    args.cost_bps = resolve_cost_bps(args.cost_bps,
                                     getattr(args, "config", None))

    with open(args.config) as f:
        cfg = json.load(f)
    from .data_local import load_bars_any
    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
    if args.max_tickers:
        bars_map = dict(list(bars_map.items())[:args.max_tickers])
    print("Harvesting refined-set trades over {} tickers...".format(len(bars_map)))
    trades = harvest_refined_trades(cfg, bars_map, spy_bars,
                                    cost_bps=args.cost_bps)
    trades.to_csv(args.csv, index=False)
    print("Wrote {} trades to {}".format(len(trades), args.csv))


if __name__ == "__main__":
    main()
