"""rmxd_report.py — the full standard report for RMX-D.

One command, every gate in docs/RMX-PREREGISTRATION.md §5, printed with the
number that decides it. Written so the failing case is as visible as the
passing one: audit §9 asked for a single report per experiment with explicit
machine-checkable PASS/FAIL, and this is it.

    py scripts/rmxd_report.py --hi 2015-12-31                # in-sample
    py scripts/rmxd_report.py --lo 2016-01-01 --hi 2026-07-24 --oos
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.book import buy_and_hold, run_book                      # noqa: E402
from rhengine.alpha.rmxd import (ABSMOM_MONTHS, TREND_MONTHS, build,        # noqa: E402
                                 risk_free, schedule, series)
from rhengine.alpha.stats import (Perf, alpha_beta, deflated_sharpe,        # noqa: E402
                                  monthly, perf)
from rhengine.alpha.validation import (bear_episodes, block_bootstrap,      # noqa: E402
                                       calendar_years, drop_best,
                                       random_timing_null, walk_forward)

COSTS = (0.0, 5.0, 10.0, 20.0, 50.0, 100.0)


def hdr(t: str) -> None:
    print("\n" + t)
    print("-" * len(t))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", default="VFINX")
    ap.add_argument("--safe", default="VFITX")
    ap.add_argument("--lo", default="1970-01-01")
    ap.add_argument("--hi", default="2015-12-31")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--paths", type=int, default=1000)
    ap.add_argument("--book-lo", default=None,
                    help="start the book here; earlier data is signal warmup only")
    ap.add_argument("--oos", action="store_true",
                    help="label the run as the single out-of-sample look")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    eq_px, safe_px = series(args.equity), series(args.safe) if args.safe else None
    bk, bh, rf, sch = build(eq_px, safe_px, args.lo, args.hi, args.cost_bps,
                            book_lo=args.book_lo)
    r, b = bk.daily_return, bh

    print("=" * 78)
    print(f"RMX-D  {'OUT-OF-SAMPLE' if args.oos else 'in-sample'}   "
          f"{args.equity} / {args.safe}   {r.index[0].date()} .. {r.index[-1].date()}")
    print(f"trend {TREND_MONTHS}m SMA | absolute momentum {ABSMOM_MONTHS}m | "
          f"no leverage | no vol target | {args.cost_bps:.0f} bps round trip")
    print("=" * 78)

    ps, pb = perf(r, rf, "RMX-D"), perf(b, rf, f"{args.equity} buy & hold")
    ab = alpha_beta(monthly(r), monthly(b), monthly(rf))

    hdr("1. performance")
    print(Perf.header())
    print(ps.row())
    print(pb.row())
    print(f"\n    alpha {ab['alpha_ann'] * 100:+.2f}%/yr  t={ab['alpha_t']:+.2f}  "
          f"beta={ab['beta']:.2f}  over {ab['n']} months")
    print(f"    excess CAGR {ps.cagr_pct - pb.cagr_pct:+.2f} pp/yr   "
          f"ulcer {ps.ulcer_pct:.2f}% vs {pb.ulcer_pct:.2f}%   "
          f"worst month {ps.worst_month_pct:+.1f}% vs {pb.worst_month_pct:+.1f}%")
    print(f"    exposure: {(sch['equity'] > 0).mean() * 100:.0f}% equity / "
          f"{(sch['safe'] > 0).mean() * 100:.0f}% bonds / "
          f"{(sch['cash'] > 0.999).mean() * 100:.0f}% cash   "
          f"turnover {bk.annual_turnover * 100:.0f}%/yr   "
          f"cost drag {bk.total_cost_pct:.1f}% total")

    ex_m = monthly(r) - monthly(b)
    dsr_abs = deflated_sharpe(monthly(r).mean() / monthly(r).std(), len(monthly(r)),
                              args.trials, float(monthly(r).skew()),
                              float(monthly(r).kurtosis()) + 3.0)
    dsr_ex = deflated_sharpe(ex_m.mean() / ex_m.std(), len(ex_m), args.trials,
                             float(ex_m.skew()), float(ex_m.kurtosis()) + 3.0)
    print(f"    deflated Sharpe @ {args.trials} trials: absolute {dsr_abs:.3f}   "
          f"on excess-over-benchmark {dsr_ex:.3f}")

    hdr("2. cost sensitivity  (audit FC-2: an 11 bps assumption once decided everything)")
    print("    {:>6} {:>9} {:>9} {:>8} {:>9}".format(
        "bps", "CAGR", "vs B&H", "Sharpe", "maxDD"))
    for c in COSTS:
        bkc, bhc, rfc, _ = build(eq_px, safe_px, args.lo, args.hi, c,
                                 book_lo=args.book_lo)
        pc = perf(bkc.daily_return, rfc, "x")
        print("    {:>6.0f} {:>+8.2f}% {:>+8.2f}pp {:>8.2f} {:>8.2f}%".format(
            c, pc.cagr_pct, pc.cagr_pct - pb.cagr_pct, pc.sharpe, pc.max_dd_pct))

    hdr("3. contiguous folds vs the index")
    wf = walk_forward(r, b, folds=6)
    print(wf.round(2).to_string(index=False))
    print(f"    folds beating the index: {int(wf['beat'].sum())}/{len(wf)}  "
          f"worst excess {wf['excess_pct'].min():+.1f}%")

    hdr("4. bear episodes (benchmark drawdowns worse than -20%)")
    ep = bear_episodes(r, b)
    if len(ep):
        print(ep.round(1).to_string(index=False))
        print(f"    independent episodes: {len(ep)}  "
              f"(audit G1.3 wants >= 3)")
    else:
        print("    none in this window")

    hdr("5. calendar years")
    cy = calendar_years(r, b)
    print(cy.round(1).to_string(index=False))
    beat = int(cy["excess_pct"].gt(0).sum())
    print(f"    beat the index in {beat}/{len(cy)} years ({beat / len(cy) * 100:.0f}%)  "
          f"- a defensive rule is SUPPOSED to lose small often and win big rarely")

    hdr("6. concentration - delete the best months")
    db = drop_best(r)
    dbb = drop_best(b)
    for k in (0, 1, 3, 6):
        print(f"    drop best {k}: strategy {db[f'drop_best_{k}']:+10.1f}%   "
              f"index {dbb[f'drop_best_{k}']:+10.1f}%")
    print(f"    negative months: strategy {db['neg_months_pct']:.0f}%  "
          f"index {dbb['neg_months_pct']:.0f}%")

    hdr("7. random-timing null - does the SIGNAL beat a coin flip invested as often?")
    adj = pd.DataFrame({"EQ": eq_px, "SAFE": safe_px}).sort_index()
    adj = adj.loc[(adj.index >= r.index[0]) & (adj.index <= r.index[-1])].dropna(subset=["EQ"])
    rn = random_timing_null(adj, sch[sch.index.isin(adj.index)], rf, args.cost_bps,
                            r, "SAFE", paths=200)
    print(f"    actual {rn['actual_pct']:+.1f}%   random p5/p50/p95: "
          f"{rn['random_pct']['p5']:+.1f}% / {rn['random_pct']['p50']:+.1f}% / "
          f"{rn['random_pct']['p95']:+.1f}%")
    print(f"    the rule sits at the {rn['percentile']:.0f}th percentile of "
          f"{rn['paths']} permutations   (gate: >= 80th)")

    hdr("8. stationary block bootstrap (21-day blocks, paired with the index)")
    mc = block_bootstrap(r, b, paths=args.paths, mean_block=21)
    print(f"    terminal p5/p50/p95 : {mc['terminal_pct']['p5']:+.0f}% / "
          f"{mc['terminal_pct']['p50']:+.0f}% / {mc['terminal_pct']['p95']:+.0f}%")
    print(f"    index   p5/p50/p95  : {mc['spy_terminal_pct']['p5']:+.0f}% / "
          f"{mc['spy_terminal_pct']['p50']:+.0f}% / {mc['spy_terminal_pct']['p95']:+.0f}%")
    print(f"    maxDD  p50/p95/p99  : {mc['max_dd_mag_pct']['p50']:.1f}% / "
          f"{mc['max_dd_mag_pct']['p95']:.1f}% / {mc['max_dd_mag_pct']['p99']:.1f}%  "
          f"(realised {mc['realised_max_dd_mag_pct']:.1f}%)")
    print(f"    P(loss) {mc['p_loss']:.3f}    "
          f"P(underperform index) {mc['p_underperform_spy']:.3f}   (gate: <= 0.30)")

    hdr("9. gates")
    gates = [
        ("beats index on total return", ps.total_return_pct > pb.total_return_pct),
        ("beats index on CAGR", ps.cagr_pct > pb.cagr_pct),
        ("alpha t >= 2.0", ab["alpha_t"] >= 2.0),
        ("max drawdown <= index", ps.max_dd_pct >= pb.max_dd_pct),
        ("Sharpe > index", ps.sharpe > pb.sharpe),
        ("still beats index at 50 bps", None),
        (">= 70% of folds beat index", wf["beat"].mean() >= 0.70),
        (">= 3 independent bear episodes", len(ep) >= 3),
        ("timing >= 80th pct of random", rn["percentile"] >= 80.0),
        ("P(underperform index) <= 0.30", mc["p_underperform_spy"] <= 0.30),
        ("deflated Sharpe (excess) > 0.95", dsr_ex > 0.95),
    ]
    bk50, _, rf50, _ = build(eq_px, safe_px, args.lo, args.hi, 50.0,
                             book_lo=args.book_lo)
    gates[5] = ("still beats index at 50 bps",
                perf(bk50.daily_return, rf50, "x").cagr_pct > pb.cagr_pct)
    npass = 0
    for name, ok in gates:
        npass += bool(ok)
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n    {npass}/{len(gates)} gates met")

    if args.json_out:
        out = {
            "window": [str(r.index[0].date()), str(r.index[-1].date())],
            "oos": args.oos, "cost_bps": args.cost_bps,
            "strategy": {"cagr": ps.cagr_pct, "sharpe": ps.sharpe,
                         "max_dd": ps.max_dd_pct, "total": ps.total_return_pct},
            "benchmark": {"cagr": pb.cagr_pct, "sharpe": pb.sharpe,
                          "max_dd": pb.max_dd_pct, "total": pb.total_return_pct},
            "alpha": ab, "dsr_excess": dsr_ex, "mc": mc, "null": rn,
            "gates": {n: bool(v) for n, v in gates}, "gates_passed": npass,
        }
        json.dump(out, open(args.json_out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
