"""rmx_timing.py — test the RMX-D exposure sleeve on survivorship-free history.

The stock-selection work in this project has always been limited by which
tickers a free vendor still serves. This test is not: an index level and a
total-return mutual fund have no delisting bias, so `^GSPC` (1970 ->) and
`VFINX` (1980 ->) give 56 and 46 uninterrupted years covering nine bear markets.

Everything here runs through `rhengine.alpha.book`, so the numbers are
mark-to-market, cost-charged and benchmark-compared like every other result in
the post-audit stack.

    py scripts/rmx_timing.py --hi 2015-12-31            # in-sample only
    py scripts/rmx_timing.py --hi 2015-12-31 --sweep    # parameter plateau
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.book import buy_and_hold, run_book                    # noqa: E402
from rhengine.alpha.stats import Perf, alpha_beta, monthly, perf          # noqa: E402
from rhengine.alpha.timing import TimingConfig, build_schedule, month_end_index  # noqa: E402
from rhengine.alpha.validation import (block_bootstrap, calendar_years,   # noqa: E402
                                       drop_best, walk_forward)

BARS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "research_data", "yahoo_long")


def series(sym: str) -> pd.Series:
    f = os.path.join(BARS, f"{sym.replace('^', '_')}.csv.gz")
    df = pd.read_csv(f, parse_dates=["date"]).drop_duplicates("date")
    return df.set_index("date")["adjclose"].sort_index()


def load_rf(cal: pd.DatetimeIndex) -> pd.Series:
    from rhengine.alpha.panel import Panel
    rf, _ = Panel._french(cal)
    return rf.ffill().fillna(0.0)


def run_one(risk: pd.Series, safe: pd.Series | None, cfg: TimingConfig,
            cost_bps: float, lo: str, hi: str, label: str,
            borrow_bps: float = 200.0):
    """Build a two-asset (equity / bond / cash) book from the timing schedule."""
    cols = {"RISK": risk}
    if safe is not None:
        cols["SAFE"] = safe
    adj = pd.DataFrame(cols).dropna(how="all")
    adj = adj.loc[(adj.index >= pd.Timestamp(lo)) & (adj.index <= pd.Timestamp(hi))]
    adj = adj.dropna(subset=["RISK"])
    rf = load_rf(adj.index)

    sched = build_schedule(adj["RISK"], adj["SAFE"] if safe is not None else None, cfg)
    warm = max(cfg.sma_months, cfg.abs_mom_months) + 1
    sched = sched.iloc[warm:]
    if sched.empty:
        raise ValueError("not enough history for the warmup")

    targets = {}
    for d, row in sched.iterrows():
        w = {"RISK": float(row["equity"])}
        if safe is not None and row["bond"] > 0:
            w["SAFE"] = float(row["bond"])
        targets[d] = pd.Series(w)

    start = sched.index[0]
    bk = run_book(adj, targets, rf, cost_bps, start=start, end=adj.index[-1],
                  max_gross=max(1.0, cfg.max_exposure), borrow_spread_bps=borrow_bps)
    bh = buy_and_hold(adj["RISK"], start, adj.index[-1]).reindex(
        bk.daily_return.index).fillna(0.0)
    return bk, bh, rf, sched


def show(bk, bh, rf, label, cost_bps):
    p = perf(bk.daily_return, rf, label)
    b = perf(bh, rf, "buy & hold")
    ab = alpha_beta(monthly(bk.daily_return), monthly(bh), monthly(rf))
    print(Perf.header())
    print(p.row())
    print(b.row())
    print(f"\nalpha {ab['alpha_ann'] * 100:+.2f}%/yr  t={ab['alpha_t']:+.2f}  "
          f"beta={ab['beta']:.2f}  n={ab['n']} months  |  "
          f"excess CAGR {p.cagr_pct - b.cagr_pct:+.2f} pp/yr  |  "
          f"turnover {bk.annual_turnover * 100:.0f}%/yr @ {cost_bps:.0f} bps  |  "
          f"mean exposure {bk.exposure.mean() * 100:.0f}%")
    return p, b, ab


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="1970-01-01")
    ap.add_argument("--hi", default="2015-12-31")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--mc", action="store_true")
    args = ap.parse_args()

    gspc, vfinx = series("^GSPC"), series("VFINX")
    vustx, vfitx = series("VUSTX"), series("VFITX")

    print("=" * 78)
    print(f"RMX-D exposure sleeve   {args.lo} .. {args.hi}   "
          f"{args.cost_bps:.0f} bps round trip")
    print("=" * 78)

    # ---- A. price-only index, the longest clean sample available -----------
    print("\n[A] S&P 500 price index (^GSPC), no dividends, cash at RF, no bonds")
    cfg = TimingConfig(use_bonds=False)
    bk, bh, rf, sched = run_one(gspc, None, cfg, args.cost_bps, args.lo, args.hi, "RMX-D")
    pa, ba, _ = show(bk, bh, rf, "RMX-D (trend+voltarget)", args.cost_bps)

    print("\n  split-half stability (the declared kill criterion is Calmar in BOTH):")
    mid = bk.daily_return.index[len(bk.daily_return) // 2]
    for name, sl in (("first half", slice(None, mid)), ("second half", slice(mid, None))):
        s, h = bk.daily_return.loc[sl], bh.loc[sl]
        ps, ph = perf(s, rf, name), perf(h, rf, name)
        flag = "PASS" if ps.calmar > ph.calmar else "FAIL"
        print(f"    {name:<12} {ps.start}..{ps.end}  "
              f"CAGR {ps.cagr_pct:+6.2f}% vs {ph.cagr_pct:+6.2f}%   "
              f"maxDD {ps.max_dd_pct:7.2f}% vs {ph.max_dd_pct:7.2f}%   "
              f"Calmar {ps.calmar:5.2f} vs {ph.calmar:5.2f}   [{flag}]")

    # ---- B. total return, with the Treasury risk-off leg -------------------
    print("\n[B] S&P 500 TOTAL return (VFINX) + long-Treasury risk-off (VUSTX)")
    cfg2 = TimingConfig(use_bonds=True)
    bk2, bh2, rf2, sched2 = run_one(vfinx, vustx, cfg2, args.cost_bps,
                                    args.lo, args.hi, "RMX-D")
    show(bk2, bh2, rf2, "RMX-D + bonds", args.cost_bps)

    print("\n[C] same, intermediate Treasuries (VFITX) instead of long")
    bk3, bh3, rf3, _ = run_one(vfinx, vfitx, cfg2, args.cost_bps, args.lo, args.hi, "RMX-D")
    show(bk3, bh3, rf3, "RMX-D + IT bonds", args.cost_bps)

    print("\n[D] cash-only risk-off (no bond bet at all)")
    cfg3 = TimingConfig(use_bonds=False)
    bk4, bh4, rf4, _ = run_one(vfinx, None, cfg3, args.cost_bps, args.lo, args.hi, "RMX-D")
    show(bk4, bh4, rf4, "RMX-D cash-only", args.cost_bps)

    # ---- decomposition: which rule does the work? --------------------------
    print("\n[E] rule decomposition on VFINX total return (cash risk-off)")
    for lbl, c in (("trend only", TimingConfig(vol_target=None, use_bonds=False)),
                   ("voltarget only .12", TimingConfig(trend_gate=False, use_bonds=False)),
                   ("voltarget only idx", TimingConfig(trend_gate=False, vol_target="index",
                                                       use_bonds=False)),
                   ("trend + vt .12", TimingConfig(use_bonds=False)),
                   ("trend + vt index", TimingConfig(vol_target="index", use_bonds=False))):
        try:
            b, h, r, _ = run_one(vfinx, None, c, args.cost_bps, args.lo, args.hi, lbl)
            pp, hh = perf(b.daily_return, r, lbl), perf(h, r, "bh")
            print(f"    {lbl:<18} CAGR {pp.cagr_pct:+6.2f}%  maxDD {pp.max_dd_pct:7.2f}%  "
                  f"Sharpe {pp.sharpe:5.2f}  Calmar {pp.calmar:5.2f}  "
                  f"(B&H {hh.cagr_pct:+.2f}% / {hh.max_dd_pct:.2f}% / {hh.sharpe:.2f})")
        except Exception as e:
            print(f"    {lbl:<18} ERROR {type(e).__name__}: {e}")

    # ---- year table --------------------------------------------------------
    print("\n[F] calendar years, ^GSPC book (strategy vs index)")
    cy = calendar_years(bk.daily_return, bh)
    print(cy.round(1).to_string(index=False))
    beat = int(cy["excess_pct"].gt(0).sum())
    print(f"    beat the index in {beat}/{len(cy)} years "
          f"({beat / len(cy) * 100:.0f}%)")

    print("\n[G] contiguous folds, ^GSPC book")
    print(walk_forward(bk.daily_return, bh, folds=6).round(2).to_string(index=False))

    print("\n[H] concentration - does the result survive deleting its best months?")
    print("   ", drop_best(bk.daily_return))

    if args.sweep:
        print("\n[I] parameter plateau on VFINX (cash risk-off) - "
              "declared kill criterion: advantage must survive 8 and 12 months")
        print("    {:>4} {:>7} {:>8} {:>8} {:>8} {:>8}".format(
            "sma", "voltgt", "CAGR", "maxDD", "Sharpe", "vs B&H"))
        print("    {:>4} {:>7} {:>5} {:>8} {:>8} {:>8} {:>8}".format(
            "sma", "voltgt", "cap", "CAGR", "maxDD", "Sharpe", "vs B&H"))
        for sma in (8, 10, 12):
            for vt in (None, 0.12, "index"):
                for cap in (1.0, 1.25, 1.5, 2.0):
                    c = TimingConfig(sma_months=sma, vol_target=vt,
                                     max_exposure=cap, use_bonds=False)
                    b, h, r, _ = run_one(vfinx, None, c, args.cost_bps,
                                         args.lo, args.hi, "x")
                    pp, hh = perf(b.daily_return, r, "x"), perf(h, r, "bh")
                    print("    {:>4} {:>7} {:>5.2f} {:>+8.2f} {:>8.2f} {:>8.2f} "
                          "{:>+8.2f}".format(
                              sma, "off" if vt is None else str(vt), cap,
                              pp.cagr_pct, pp.max_dd_pct, pp.sharpe,
                              pp.cagr_pct - hh.cagr_pct))

    if args.mc:
        print("\n[J] stationary block bootstrap, ^GSPC book (2000 paths, 21-day blocks)")
        out = block_bootstrap(bk.daily_return, bh, paths=2000, mean_block=21)
        print(f"    terminal p5/p50/p95 : {out['terminal_pct']['p5']:+.0f}% / "
              f"{out['terminal_pct']['p50']:+.0f}% / {out['terminal_pct']['p95']:+.0f}%")
        print(f"    index  p5/p50/p95   : {out['spy_terminal_pct']['p5']:+.0f}% / "
              f"{out['spy_terminal_pct']['p50']:+.0f}% / {out['spy_terminal_pct']['p95']:+.0f}%")
        print(f"    maxDD p50/p95/p99   : {out['max_dd_mag_pct']['p50']:.1f}% / "
              f"{out['max_dd_mag_pct']['p95']:.1f}% / {out['max_dd_mag_pct']['p99']:.1f}%  "
              f"(realised {out['realised_max_dd_mag_pct']:.1f}%)")
        print(f"    P(loss) {out['p_loss']:.3f}   "
              f"P(underperform index) {out['p_underperform_spy']:.3f}")


if __name__ == "__main__":
    main()
