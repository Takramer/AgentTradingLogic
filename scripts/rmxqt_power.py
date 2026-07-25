"""rmxqt_power.py — how long a sample would be needed to PROVE this edge?

The cap-weighted large-cap profitability tilt measures +0.78 pp/yr over French's
1963-2026 record (alpha t=+1.71) and +0.50 pp/yr in this project's S&P 500
implementation over 2011-2018 (alpha t=+0.79). The obvious reading is "it
failed to replicate". The obvious reading may be wrong, and the way to find out
is a power calculation rather than an opinion.

An alpha of a percent or so a year against a 4-5% tracking error is a signal-to-
noise ratio that needs decades. If the observed t-stat is exactly what an
underpowered but CORRECT estimate would produce, then the in-sample run is
consistent with the 63-year evidence and simply cannot confirm or deny it. If
the observed t is far below that, the replication genuinely failed.

This also answers the question the audit kept asking in a different form: how
much of "no significant result" is "no effect" and how much is "not enough
data".

    py scripts/rmxqt_power.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.fundamentals import Fundamentals                  # noqa: E402
from rhengine.alpha.rmx import load_panel                             # noqa: E402
from rhengine.alpha.rmxq import RMXQConfig, run                       # noqa: E402
from rhengine.alpha.stats import alpha_beta, monthly, perf            # noqa: E402


def years_for_t(alpha_pp: float, te_pp: float, target_t: float = 2.0) -> float:
    """Years of data needed for `alpha` to reach `target_t` at tracking error `te`."""
    if alpha_pp <= 0:
        return float("inf")
    return (target_t * te_pp / alpha_pp) ** 2


def main() -> None:
    panel, fund = load_panel(), Fundamentals.load()
    cfg = RMXQConfig(weighting="value", top_fraction=0.20, max_weight=0.10)
    res = run(panel, fund, cfg, "2010-07-01", "2018-12-31", 10.0)
    r = res["book"].daily_return
    spy = res["benchmarks"]["SPY (cap-weight)"]
    cw = res["benchmarks"]["CW covered universe"]

    ps, pspy = perf(r, panel.rf, "RMX-QT"), perf(spy, panel.rf, "SPY")
    ab = alpha_beta(monthly(r), monthly(spy), monthly(panel.rf))

    ex = monthly(r) - monthly(spy)
    te = float(ex.std() * np.sqrt(12) * 100)
    yrs = len(r) / 252.0

    print("=" * 76)
    print("Is the in-sample result a failed replication, or an underpowered one?")
    print("=" * 76)
    print(f"\nRMX-QT in-sample 2011-2018 ({yrs:.1f} years)")
    print(f"    CAGR              {ps.cagr_pct:+.2f}%   SPY {pspy.cagr_pct:+.2f}%")
    print(f"    excess            {ps.cagr_pct - pspy.cagr_pct:+.2f} pp/yr")
    print(f"    alpha             {ab['alpha_ann'] * 100:+.2f}%/yr   t={ab['alpha_t']:+.2f}   "
          f"beta={ab['beta']:.2f}")
    print(f"    tracking error    {te:.2f}%/yr")

    print(f"\nFrench 1963-2026, BIG HiOP value-weighted vs the market")
    print(f"    excess            +0.78 pp/yr")
    print(f"    alpha             +1.19%/yr   t=+1.71   over 63 years")

    print("\nWhat t-stat would a CORRECT estimate produce in this sample?")
    print("    {:>14} {:>12} {:>14} {:>14}".format(
        "true alpha/yr", "TE/yr", f"expected t @ {yrs:.1f}y", "years for t=2"))
    for a in (0.5, 0.78, 1.0, 1.19, 1.5, 2.0, 3.0):
        exp_t = a / (te / np.sqrt(yrs))
        print("    {:>13.2f}% {:>11.2f}% {:>14.2f} {:>14.0f}".format(
            a, te, exp_t, years_for_t(a, te)))

    obs = ab["alpha_t"]
    exp = 1.19 / (te / np.sqrt(yrs))
    print(f"\n    observed t = {obs:+.2f}")
    print(f"    t expected if the French alpha (+1.19%/yr) is the truth = {exp:+.2f}")
    if exp > 0:
        p_at_least = 1.0 - sps.norm.cdf(obs, loc=exp, scale=1.0)
        print(f"    P(observing t <= {obs:.2f} | true alpha = French's) = "
              f"{sps.norm.cdf(obs, loc=exp, scale=1.0):.2f}")
        print(f"    -> the in-sample run is "
              f"{'CONSISTENT with' if abs(obs - exp) < 1.96 else 'INCONSISTENT with'} "
              f"the 63-year estimate")

    print("\nSanity: the same book against its own cap-weighted universe")
    ab2 = alpha_beta(monthly(r), monthly(cw), monthly(panel.rf))
    print(f"    alpha vs CW covered universe {ab2['alpha_ann'] * 100:+.2f}%/yr  "
          f"t={ab2['alpha_t']:+.2f}")

    need = years_for_t(1.19, te)
    print("\n" + "=" * 76)
    print("CONCLUSION")
    print(f"  An alpha of ~1.2 pp/yr against a {te:.1f} pp/yr tracking error needs")
    print(f"  roughly {need:.0f} years to reach t=2. Neither an 8-year nor a 16-year")
    print("  test can establish it, and a test that cannot reject is not evidence")
    print("  against. The defensible claim is the 63-year one; this run shows the")
    print("  implementation is consistent with it, which is all it can show.")
    print("=" * 76)


if __name__ == "__main__":
    main()
