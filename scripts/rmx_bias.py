"""rmx_bias.py — measure the survivorship inflation in the research dataset.

The dataset prices 781 of the 1,213 names that were ever S&P 500 members; the
missing 432 are exactly the population that failed, was acquired, or whose
ticker was reused. Rather than wave at that, this script measures it:

    EW(priced PIT members)  vs  RSP (the real equal-weight S&P 500 ETF)

The gap, in percentage points per year, is the haircut that must be applied to
any "beats the S&P" claim made on this dataset.

    py scripts/rmx_bias.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.book import buy_and_hold, equal_weight_universe   # noqa: E402
from rhengine.alpha.panel import Panel, coverage_by_year               # noqa: E402
from rhengine.alpha.rmx import load_panel                              # noqa: E402
from rhengine.alpha.stats import Perf, perf                            # noqa: E402

RSP_START = "2003-06-01"        # RSP's own inception is 2003-05-01


def main() -> None:
    p: Panel = load_panel()
    print(p.summary())
    print()

    me = p.month_ends(RSP_START, "2026-07-24")
    eligible = {d: p.eligible(d) for d in me}
    eligible = {d: n for d, n in eligible.items() if len(n) >= 30}
    dates = sorted(eligible)
    lo, hi = dates[0], p.adj.index[-1]

    ew = equal_weight_universe(p.adj, eligible, p.rf, 10.0, start=lo, end=hi)

    rsp_px = pd.read_csv(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "research_data", "yahoo_long", "RSP.csv.gz"),
        parse_dates=["date"]).set_index("date")["adjclose"]
    rsp = buy_and_hold(rsp_px, lo, hi).reindex(ew.daily_return.index).fillna(0.0)
    spy = buy_and_hold(p.spy, lo, hi).reindex(ew.daily_return.index).fillna(0.0)

    rows = [perf(ew.daily_return, p.rf, "EW priced PIT members"),
            perf(rsp, p.rf, "RSP (real equal-weight S&P)"),
            perf(spy, p.rf, "SPY (cap-weight S&P)")]
    print(Perf.header())
    for r in rows:
        print(r.row())

    gap = rows[0].cagr_pct - rows[1].cagr_pct
    print()
    print(f"SURVIVORSHIP INFLATION = EW(priced subset) - RSP = "
          f"{gap:+.2f} pp/yr  over {rows[0].start} .. {rows[0].end}")
    print("Every 'vs SPY' number produced on this dataset should be read with")
    print(f"roughly {gap:+.2f} pp/yr of that gap treated as an artefact, not edge.")

    print()
    print("mean eligible universe size per rebalance: "
          f"{sum(len(v) for v in eligible.values()) / len(eligible):.0f}")
    print()
    print("priced-member coverage by year:")
    print(coverage_by_year(p).round(3).to_string())


if __name__ == "__main__":
    main()
