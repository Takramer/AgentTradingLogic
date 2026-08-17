"""cap_cost_profile.py — label the book by market-cap tier and re-cost it.

The cost assumption in `config.json` is a single scalar, 30 bps round trip. It
was calibrated on the LEGACY engine's scan universe, which floors at
$500k/day of dollar volume and is therefore mostly small and micro caps. The
research-stack books are a different animal: RMX-B and RMX-C hold a
cap-weighted slice of the S&P 500, where the same 30 bps is not conservative,
it is simply the wrong tier's number.

This script does two things and refuses to guess at a third:

  1. LABELS every name the book holds with its liquidity tier (mega/large,
     mid, small, micro) from its point-in-time market cap, and prints how the
     book's weight is distributed across them.
  2. RE-COSTS the book with each name paying its own tier's spread
     (`costs.bps_for_caps`), and reports the effect against the flat
     assumptions the published numbers were produced at.

  3. It does NOT claim this is your fill cost. The tier table is a published
     prior (see `costs.CAP_TIERS`); your actual fills are a measurement nobody
     has taken at the open, which is where this engine trades. Every figure
     here is printed as a lo/point/hi bracket for that reason. A conclusion
     that only holds at `lo` has not held.

    py scripts/cap_cost_profile.py
    py scripts/cap_cost_profile.py --book rmxc --lo 2010-07-01 --hi 2018-12-31
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.blend import BlendConfig                    # noqa: E402
from rhengine.alpha.blend import build as blend_build           # noqa: E402
from rhengine.alpha.book import run_book                        # noqa: E402
from rhengine.alpha.fundamentals import Fundamentals            # noqa: E402
from rhengine.alpha.rmx import load_panel                       # noqa: E402
from rhengine.alpha.rmxb import RMXBConfig, build_targets       # noqa: E402
from rhengine.alpha.rmxq import market_cap                      # noqa: E402
from rhengine.alpha.stats import alpha_beta, monthly, perf      # noqa: E402
from rhengine.costs import (CAP_TIERS, TIER_NAMES, bps_for_caps,  # noqa: E402
                            default_cost_bps, tier_of_caps)


def book_targets(name: str, panel, fund, lo: str, hi: str):
    if name == "rmxb":
        t, _, _, _ = build_targets(panel, fund, RMXBConfig(), lo, hi)
        return t
    built = blend_build(panel, fund, BlendConfig(), lo, hi, default_cost_bps())
    return built["targets"]


def cap_profile(panel, fund, targets: dict) -> pd.DataFrame:
    """Weight held in each tier, per rebalance date, then averaged.

    Weighted by POSITION, because that is what "what kind of company is this
    book made of" means. The cost effect is turnover-weighted and comes from
    `run_book` instead — the two differ whenever the book churns one tier and
    holds another.
    """
    rows = []
    for d, w in targets.items():
        w = w[w > 1e-9]
        caps = market_cap(panel, fund, d, w.index)
        tiers = tier_of_caps(caps.reindex(w.index))
        row = {"date": d}
        for t in TIER_NAMES + ("unknown",):
            row[t] = float(w[tiers.values == t].sum())
        row["median_cap_$bn"] = float(caps.median() / 1e9) if len(caps) else np.nan
        row["n"] = int(len(w))
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


def static_bps(panel, fund, targets: dict, mode: str) -> pd.Series:
    """One round-trip bps per ticker, from its MEDIAN cap over the window.

    An approximation, stated rather than hidden: caps move, and a name can
    cross a tier boundary mid-sample. It is defensible here because the S&P
    500 names this book holds sit deep inside the mega/large tier rather than
    near its edge — the profile table prints the median cap so you can see how
    close to a boundary the book actually is. A date-varying cost vector would
    need `run_book` to take a DataFrame, which is not worth it until a book
    holds names near $10bn in quantity.
    """
    caps = {}
    for d, w in targets.items():
        c = market_cap(panel, fund, d, w[w > 1e-9].index)
        for tk, v in c.items():
            caps.setdefault(tk, []).append(v)
    med = pd.Series({tk: float(np.median(v)) for tk, v in caps.items()})
    return bps_for_caps(med, mode=mode)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="rmxc", choices=("rmxb", "rmxc"))
    ap.add_argument("--lo", default="2010-07-01")
    ap.add_argument("--hi", default="2018-12-31")
    ap.add_argument("--flat", type=float, nargs="*", default=[10.0, 30.0],
                    help="flat bps assumptions to compare against")
    args = ap.parse_args()

    panel, fund = load_panel(), Fundamentals.load()
    fp = panel.fingerprint(args.lo, args.hi)
    targets = book_targets(args.book, panel, fund, args.lo, args.hi)
    start, end = min(targets), pd.Timestamp(args.hi)

    print(f"\n=== {args.book.upper()}  {args.lo} .. {args.hi}  panel {fp} ===\n")

    prof = cap_profile(panel, fund, targets)
    print("Book composition by market-cap tier (mean weight over "
          f"{len(prof)} rebalances):")
    print(f"{'tier':14} {'cap range':>18} {'spread bps':>12} {'mean weight':>13}")
    for name, lo_cap, hi_cap, lo_bps, hi_bps in CAP_TIERS:
        span = (f"${lo_cap / 1e9:g}bn+" if not np.isfinite(hi_cap)
                else f"<${hi_cap / 1e6:g}m" if hi_cap < 1e9
                else f"${lo_cap / 1e9:g}-{hi_cap / 1e9:g}bn")
        print(f"{name:14} {span:>18} {f'{lo_bps:g}-{hi_bps:g}':>12} "
              f"{prof[name].mean() * 100:12.2f}%")
    if prof["unknown"].mean() > 1e-6:
        print(f"{'unknown':14} {'-':>22} {'flat default':>14} "
              f"{prof['unknown'].mean() * 100:12.2f}%")
    print(f"\nmedian holding {prof['median_cap_$bn'].median():.1f} $bn  |  "
          f"{prof['n'].mean():.0f} names  |  weight in mega/large "
          f"{prof['mega_large'].mean() * 100:.1f}%")

    print("\nRe-costed. 'flat' rows are what the published numbers assumed; "
          "'tier' rows charge\neach name its own spread on its own traded "
          "notional.\n")
    # The cap-weighted covered universe, re-costed under the SAME model as the
    # book. Charging the strategy a new cost and leaving the benchmark on the
    # old one would manufacture alpha out of a bookkeeping change — and the
    # benchmark trades far less, so it is the direction that flatters.
    neutral = {}
    for d, w in targets.items():
        cap = market_cap(panel, fund, d, panel.eligible(d))
        if len(cap):
            neutral[d] = cap / cap.sum()

    def _run(cost):
        bk = run_book(panel.adj, targets, panel.rf, cost, start=start, end=end)
        nb = run_book(panel.adj, neutral, panel.rf, cost, start=start, end=end)
        ab = alpha_beta(monthly(bk.daily_return), monthly(nb.daily_return),
                        monthly(panel.rf))
        return bk, ab

    print(f"{'cost model':22} {'eff bps':>9} {'CAGR':>8} {'cost drag':>11} "
          f"{'alpha vs CW':>12} {'t':>6}")
    results = []
    for f in args.flat:
        results.append((f"flat {f:g} bps", *_run(float(f))))
    for mode in ("lo", "point", "hi"):
        vec = static_bps(panel, fund, targets, mode)
        results.append((f"tier {mode}", *_run(vec)))
    for label, bk, ab in results:
        c = perf(bk.daily_return, panel.rf, "x").cagr_pct
        print(f"{label:22} {bk.effective_bps:9.2f} {c:7.2f}% "
              f"{bk.total_cost_pct:10.2f}% {ab['alpha_ann'] * 100:+11.2f}% "
              f"{ab['alpha_t']:+6.2f}")

    print("\nThe tier rows are a PRIOR (costs.CAP_TIERS), not your fills. The "
          "bracket is\nlo..hi; a conclusion that needs 'lo' has not survived. "
          "Measuring the OPENING\nspread, which is what this engine fills at "
          "and the widest of the day, is still\nthe open item.")


if __name__ == "__main__":
    main()
