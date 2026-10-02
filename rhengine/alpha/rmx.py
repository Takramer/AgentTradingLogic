"""rmx.py — build and run the RMX strategy.

Ties `panel` (point-in-time universe) to `signals` (the frozen spec) to `book`
(mark-to-market simulation). Nothing here decides anything by looking at
results; the parameters live in `signals.RMXConfig` and every change to one is
a trial that must be logged.

    py -m rhengine.alpha.rmx --lo 1999-01-01 --hi 2013-12-31
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict

import numpy as np
import pandas as pd

from .book import buy_and_hold, equal_weight_universe, run_book
from .panel import Panel
from .signals import (RMXConfig, build_monthly, composite_score, residual_stats,
                      trend_ok, vol_scale)
from .stats import compare, print_report

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "research_data", "_panel_cache.pkl")


def load_panel(refresh: bool = False) -> Panel:
    if not refresh and os.path.exists(CACHE):
        return pd.read_pickle(CACHE)
    p = Panel.load()
    pd.to_pickle(p, CACHE)
    return p


def build_targets(panel: Panel, cfg: RMXConfig, lo: str, hi: str,
                  warmup_months: int = 48):
    """Weight targets + the eligible-universe map, for one config and window.

    `warmup_months` of history before `lo` are consumed by the 36-month beta
    window and the 12-month formation window; signals only start after that.
    """
    start = (pd.Timestamp(lo) - pd.DateOffset(months=warmup_months + 3))
    mf = build_monthly(panel, str(start.date()), hi)
    me = mf.month_ends

    targets: dict[pd.Timestamp, pd.Series] = {}
    eligible: dict[pd.Timestamp, pd.Index] = {}
    diag = []

    first = np.searchsorted(me.values, np.datetime64(pd.Timestamp(lo)))
    for i in range(max(first, warmup_months), len(me)):
        d = me[i]
        names = panel.eligible(d)
        names = names.intersection(mf.stock_ex.columns)
        if len(names) < cfg.n_positions * 2:
            continue
        st = residual_stats(mf, i, names)
        if st.empty:
            continue
        st = st[(st["nobs"] >= 30) & (st["form_obs"] >= 9)]
        st = st.dropna(subset=["resmom"])
        if len(st) < cfg.n_positions * 2:
            continue
        last_m = mf.raw.iloc[i][st.index]
        score = composite_score(st, last_m, cfg)
        pick = score.nlargest(cfg.n_positions).index
        targets[d] = pd.Series(1.0 / len(pick), index=pick)
        eligible[d] = st.index
        diag.append({"date": str(d.date()), "universe": len(st),
                     "picked": len(pick),
                     "mean_beta": float(st.loc[pick, "beta"].mean()),
                     "mean_resmom": float(st.loc[pick, "resmom"].mean())})
    return targets, eligible, pd.DataFrame(diag)


def run(panel: Panel, cfg: RMXConfig, lo: str, hi: str, cost_bps: float):
    """Full run: targets -> exposure overlays -> book -> benchmarks."""
    targets, eligible, diag = build_targets(panel, cfg, lo, hi)
    if not targets:
        raise ValueError("no rebalances produced - window too short for warmup")

    dates = sorted(targets)
    b_lo, b_hi = dates[0], pd.Timestamp(hi)

    exposure = None
    if cfg.trend_gate:
        exposure = {d: (cfg.max_exposure if trend_ok(panel.spy, d) else 0.0)
                    for d in dates}

    bk = run_book(panel.adj, targets, panel.rf, cost_bps,
                  start=b_lo, end=b_hi, exposure=exposure)

    if cfg.vol_target:
        # Two-pass: the vol used to size at date D is the realised vol of the
        # UNSCALED book strictly before D, so the overlay stays causal.
        base = bk.daily_return
        expo2 = {}
        for d in dates:
            g = cfg.max_exposure if (not cfg.trend_gate or trend_ok(panel.spy, d)) else 0.0
            expo2[d] = g * vol_scale(base, d, cfg.vol_target, cfg.vol_window,
                                     cap=1.0)
        bk = run_book(panel.adj, targets, panel.rf, cost_bps,
                      start=b_lo, end=b_hi, exposure=expo2)

    ew = equal_weight_universe(panel.adj, eligible, panel.rf, cost_bps,
                               start=b_lo, end=b_hi)
    spy = buy_and_hold(panel.spy, b_lo, b_hi).reindex(bk.daily_return.index).fillna(0.0)

    return {
        "book": bk,
        "benchmarks": {"SPY": spy, "EW universe (same names)": ew.daily_return},
        "eligible": eligible,
        "targets": targets,
        "diag": diag,
    }


def report(panel: Panel, cfg: RMXConfig, lo: str, hi: str, cost_bps: float,
           trials: int = 1, label: str = "RMX", quiet: bool = False) -> dict:
    res = run(panel, cfg, lo, hi, cost_bps)
    bk = res["book"]
    rep = compare(bk.daily_return, res["benchmarks"], rf=panel.rf,
                  trials=trials, label=label)
    rep["turnover_ann"] = bk.annual_turnover
    rep["cost_drag_total_pct"] = bk.total_cost_pct
    rep["cost_bps"] = cost_bps
    rep["n_rebalances"] = len(res["targets"])
    rep["mean_universe"] = float(res["diag"]["universe"].mean()) if len(res["diag"]) else np.nan
    rep["mean_exposure"] = float(bk.exposure.mean())
    rep["config"] = asdict(cfg) | {"legs": list(cfg.legs)}
    if not quiet:
        print(f"\n=== {label}  {lo} .. {hi}  @ {cost_bps:.0f} bps round trip ===")
        print(f"{rep['n_rebalances']} monthly rebalances | "
              f"mean eligible universe {rep['mean_universe']:.0f} | "
              f"one-way turnover {rep['turnover_ann'] * 100:.0f}%/yr | "
              f"cost drag {rep['cost_drag_total_pct']:.1f}% total | "
              f"mean exposure {rep['mean_exposure'] * 100:.0f}%")
        print()
        print_report(rep)
    return rep | {"_res": res}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="1999-01-01")
    ap.add_argument("--hi", default="2013-12-31")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--legs", default="resmom")
    ap.add_argument("--trend-gate", action="store_true")
    ap.add_argument("--vol-target", type=float, default=None)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    panel = load_panel(args.refresh)
    cfg = RMXConfig(n_positions=args.n, legs=tuple(args.legs.split(",")),
                    trend_gate=args.trend_gate, vol_target=args.vol_target)
    rep = report(panel, cfg, args.lo, args.hi, args.cost_bps, trials=args.trials,
                 label="RMX " + args.legs)
    if args.json_out:
        rep.pop("_res", None)
        json.dump(rep, open(args.json_out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
