"""rmxq_report.py — the full standard report for RMX-Q.

Same structure and the same gates as `rmxd_report.py`, so the two strategies
are judged by identical standards and the one that failed cannot be quietly
graded on a curve.

Two benchmarks are carried throughout, because they answer different questions:

* **SPY** is the user's objective, and it carries this dataset's survivorship
  gap (measured at +0.86 pp/yr by `scripts/rmx_bias.py`, reported as a haircut).
* **Equal-weight covered universe** is the same names on the same dates, so it
  removes both the survivorship gap and the equal-weighting effect. Alpha
  against it is the signal on its own.

    py scripts/rmxq_report.py --lo 2010-07-01 --hi 2018-12-31
    py scripts/rmxq_report.py --lo 2019-01-01 --hi 2026-07-24 --oos
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.fundamentals import Fundamentals                     # noqa: E402
from rhengine.alpha.rmx import load_panel                                # noqa: E402
from rhengine.alpha.rmxq import RMXQConfig, run                          # noqa: E402
from rhengine.alpha.stats import (Perf, alpha_beta, deflated_sharpe,     # noqa: E402
                                  monthly, perf)
from rhengine.alpha.validation import (bear_episodes, block_bootstrap,   # noqa: E402
                                       calendar_years, drop_best,
                                       random_selection_null, walk_forward)

COSTS = (0.0, 5.0, 10.0, 25.0, 50.0, 100.0)
SURVIVORSHIP_HAIRCUT_PP = 0.86      # measured in scripts/rmx_bias.py vs RSP


def hdr(t: str) -> None:
    print("\n" + t)
    print("-" * len(t))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="2010-07-01")
    ap.add_argument("--hi", default="2018-12-31")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--trials", type=int, default=60)
    ap.add_argument("--paths", type=int, default=1000)
    ap.add_argument("--null-paths", type=int, default=200)
    ap.add_argument("--top-fraction", type=float, default=0.30)
    ap.add_argument("--weighting", default="value", choices=["value", "equal"])
    ap.add_argument("--max-weight", type=float, default=0.10)
    ap.add_argument("--oos", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    panel, fund = load_panel(), Fundamentals.load()
    cfg = RMXQConfig(top_fraction=args.top_fraction, weighting=args.weighting,
                     max_weight=args.max_weight)
    res = run(panel, fund, cfg, args.lo, args.hi, args.cost_bps)
    bk, diag = res["book"], res["diag"]
    r = bk.daily_return
    spy = res["benchmarks"]["SPY (cap-weight)"]
    neutral_label = next(k for k in res["benchmarks"]
                         if k.endswith("covered universe") and not k.startswith("EW "))         if any(k.startswith("CW ") for k in res["benchmarks"]) else "EW covered universe"
    ew = res["benchmarks"][neutral_label]

    print("=" * 78)
    print(f"RMX-Q  {'OUT-OF-SAMPLE' if args.oos else 'in-sample'}   "
          f"{r.index[0].date()} .. {r.index[-1].date()}")
    print(f"top {cfg.top_fraction:.0%} by OP/BE | annual June rebalance | "
          f"{cfg.weighting} weight (cap {cfg.max_weight:.0%}) | "
          f"{args.cost_bps:.0f} bps round trip")
    print("=" * 78)

    ps = perf(r, panel.rf, "RMX-Q")
    pspy = perf(spy, panel.rf, "SPY (cap-weight)")
    pew = perf(ew, panel.rf, neutral_label)
    ab_spy = alpha_beta(monthly(r), monthly(spy), monthly(panel.rf))
    ab_ew = alpha_beta(monthly(r), monthly(ew), monthly(panel.rf))

    hdr("1. performance")
    print(Perf.header())
    for x in (ps, pspy, pew):
        print(x.row())
    print(f"\n    vs SPY : alpha {ab_spy['alpha_ann'] * 100:+.2f}%/yr  "
          f"t={ab_spy['alpha_t']:+.2f}  beta={ab_spy['beta']:.2f}   "
          f"excess CAGR {ps.cagr_pct - pspy.cagr_pct:+.2f} pp/yr")
    print(f"    vs {neutral_label[:2]}  : alpha {ab_ew['alpha_ann'] * 100:+.2f}%/yr  "
          f"t={ab_ew['alpha_t']:+.2f}  beta={ab_ew['beta']:.2f}   "
          f"excess CAGR {ps.cagr_pct - pew.cagr_pct:+.2f} pp/yr")
    print(f"    survivorship haircut on the SPY line: -{SURVIVORSHIP_HAIRCUT_PP:.2f} pp/yr "
          f"-> adjusted excess {ps.cagr_pct - pspy.cagr_pct - SURVIVORSHIP_HAIRCUT_PP:+.2f} pp/yr")
    print(f"    {len(res['targets'])} rebalances | {diag['picked'].mean():.0f} names | "
          f"fundamental coverage {diag['coverage'].mean() * 100:.0f}% | "
          f"turnover {bk.annual_turnover * 100:.0f}%/yr | "
          f"cost drag {bk.total_cost_pct:.2f}% total")

    m = monthly(r)
    ex_m = m - monthly(spy)
    dsr_abs = deflated_sharpe(m.mean() / m.std(), len(m), args.trials,
                              float(m.skew()), float(m.kurtosis()) + 3.0)
    dsr_ex = deflated_sharpe(ex_m.mean() / ex_m.std(), len(ex_m), args.trials,
                             float(ex_m.skew()), float(ex_m.kurtosis()) + 3.0)
    print(f"    deflated Sharpe @ {args.trials} trials: absolute {dsr_abs:.3f}   "
          f"on excess-over-SPY {dsr_ex:.3f}")

    hdr("2. cost sensitivity")
    print("    {:>6} {:>9} {:>10} {:>8} {:>9}".format(
        "bps", "CAGR", "vs SPY", "Sharpe", "turnover"))
    for c in COSTS:
        rc = run(panel, fund, cfg, args.lo, args.hi, c)
        pc = perf(rc["book"].daily_return, panel.rf, "x")
        print("    {:>6.0f} {:>+8.2f}% {:>+9.2f}pp {:>8.2f} {:>8.0f}%".format(
            c, pc.cagr_pct, pc.cagr_pct - pspy.cagr_pct, pc.sharpe,
            rc["book"].annual_turnover * 100))

    hdr("3. contiguous folds vs SPY")
    wf = walk_forward(r, spy, folds=min(6, max(2, len(res["targets"]))))
    print(wf.round(2).to_string(index=False))
    print(f"    folds beating SPY: {int(wf['beat'].sum())}/{len(wf)}  "
          f"worst excess {wf['excess_pct'].min():+.1f}%")

    hdr("4. calendar years")
    cy = calendar_years(r, spy)
    print(cy.round(1).to_string(index=False))
    beat = int(cy["excess_pct"].gt(0).sum())
    print(f"    beat SPY in {beat}/{len(cy)} years ({beat / len(cy) * 100:.0f}%)")

    hdr("5. bear episodes (SPY drawdowns worse than -20%)")
    ep = bear_episodes(r, spy)
    print(ep.round(1).to_string(index=False) if len(ep) else "    none in this window")

    hdr("6. concentration - delete the best months")
    db, dbb = drop_best(r), drop_best(spy)
    for k in (0, 1, 3, 6):
        print(f"    drop best {k}: RMX-Q {db[f'drop_best_{k}']:+9.1f}%   "
              f"SPY {dbb[f'drop_best_{k}']:+9.1f}%")

    hdr("7. random-selection null - is it the SIGNAL or just holding 78 names?")
    rn = random_selection_null(panel, res["covered"], int(diag["picked"].mean()),
                               panel.rf, args.cost_bps, r,
                               paths=args.null_paths,
                               start=r.index[0], end=r.index[-1])
    print(f"    actual {rn['actual_pct']:+.1f}%   random p5/p50/p95: "
          f"{rn['random_pct']['p5']:+.1f}% / {rn['random_pct']['p50']:+.1f}% / "
          f"{rn['random_pct']['p95']:+.1f}%")
    print(f"    RMX-Q sits at the {rn['percentile']:.0f}th percentile of "
          f"{rn['paths']} random draws from the same covered universe "
          f"(gate: >= 80th)")

    hdr("8. stationary block bootstrap (21-day blocks, paired with SPY)")
    mc = block_bootstrap(r, spy, paths=args.paths, mean_block=21)
    print(f"    terminal p5/p50/p95 : {mc['terminal_pct']['p5']:+.0f}% / "
          f"{mc['terminal_pct']['p50']:+.0f}% / {mc['terminal_pct']['p95']:+.0f}%")
    print(f"    SPY     p5/p50/p95  : {mc['spy_terminal_pct']['p5']:+.0f}% / "
          f"{mc['spy_terminal_pct']['p50']:+.0f}% / {mc['spy_terminal_pct']['p95']:+.0f}%")
    print(f"    maxDD  p50/p95/p99  : {mc['max_dd_mag_pct']['p50']:.1f}% / "
          f"{mc['max_dd_mag_pct']['p95']:.1f}% / {mc['max_dd_mag_pct']['p99']:.1f}%  "
          f"(realised {mc['realised_max_dd_mag_pct']:.1f}%)")
    print(f"    P(loss) {mc['p_loss']:.3f}    "
          f"P(underperform SPY) {mc['p_underperform_spy']:.3f}   (gate: <= 0.30)")

    hdr("9. gates")
    r50 = run(panel, fund, cfg, args.lo, args.hi, 50.0)
    p50 = perf(r50["book"].daily_return, panel.rf, "x")
    gates = [
        ("beats SPY on total return", ps.total_return_pct > pspy.total_return_pct),
        ("beats SPY on CAGR after survivorship haircut",
         ps.cagr_pct - pspy.cagr_pct > SURVIVORSHIP_HAIRCUT_PP),
        ("alpha t vs SPY >= 2.0", ab_spy["alpha_t"] >= 2.0),
        (f"alpha t vs {neutral_label} >= 2.0", ab_ew["alpha_t"] >= 2.0),
        ("Sharpe > SPY", ps.sharpe > pspy.sharpe),
        ("max drawdown <= SPY", ps.max_dd_pct >= pspy.max_dd_pct),
        ("still beats SPY at 50 bps", p50.cagr_pct > pspy.cagr_pct),
        (">= 70% of folds beat SPY", wf["beat"].mean() >= 0.70),
        ("selection >= 80th pct of random", rn["percentile"] >= 80.0),
        ("P(underperform SPY) <= 0.30", mc["p_underperform_spy"] <= 0.30),
        ("deflated Sharpe (excess) > 0.95", dsr_ex > 0.95),
    ]
    npass = sum(bool(v) for _, v in gates)
    for name, ok in gates:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n    {npass}/{len(gates)} gates met")

    if args.json_out:
        json.dump({
            "window": [str(r.index[0].date()), str(r.index[-1].date())],
            "oos": args.oos, "cost_bps": args.cost_bps,
            "rmxq": {"cagr": ps.cagr_pct, "sharpe": ps.sharpe,
                     "max_dd": ps.max_dd_pct, "total": ps.total_return_pct},
            "spy": {"cagr": pspy.cagr_pct, "sharpe": pspy.sharpe,
                    "max_dd": pspy.max_dd_pct, "total": pspy.total_return_pct},
            "ew": {"cagr": pew.cagr_pct, "sharpe": pew.sharpe},
            "alpha_vs_spy": ab_spy, "alpha_vs_ew": ab_ew,
            "dsr_excess": dsr_ex, "mc": mc, "null": rn,
            "turnover_ann": bk.annual_turnover,
            "gates": {n: bool(v) for n, v in gates}, "gates_passed": npass,
        }, open(args.json_out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
