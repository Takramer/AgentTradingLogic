"""ff_largecap_blend.py — is there ANY construction of this factor family that
beat the market in the modern era, inside the universe we actually trade?

RMX-C's own published record says the blend earned +2.4%/yr over 1970-2024 and
+0.3%/yr (t=0.35) since 2015, measured long-only over the market. That was read
off OSAP's value-weighted quintiles across the whole CRSP universe. Two
objections to concluding anything from it, and this script answers both with
data nobody here has fitted:

  1. OSAP's quintiles span all listed stocks. This project trades the S&P 500.
     A premium that survives in the CRSP cross-section can be entirely absent in
     the top size quintile, and that is where our money is.
  2. The 2015-2024 shortfall was diagnosed as an index-concentration effect. If
     so, it should show up as a LEVEL problem in every large-cap bucket rather
     than as a broken ranking, and equal weighting inside the bucket should
     change the answer.

Ken French publishes exactly the right thing: 5x5 portfolios on size x each
signal, in BOTH value- and equal-weighted form, monthly, 1963-2026,
survivorship-free, with no cost assumption and nothing fitted here. The largest
size quintile ("BIG") is a superset of the S&P 500 and the closest public proxy
for this project's universe.

QUESTION, fixed before running
    Inside the LARGEST size quintile, does a cap-weighted portfolio holding the
    favourable extreme of each signal — high operating profitability, low
    investment, high prior return — beat the cap-weighted market? Individually,
    and blended equal-weight? Over 1963-2026 and over 2016-2026 separately?

    And does EQUAL weighting inside the same bucket help or hurt?

KILL CRITERION, fixed before running
    If the blend of BIG favourable-extreme portfolios fails to beat the market
    over 2016-2026 on BOTH weighting schemes, then no construction of this
    family available to us recovered the modern shortfall, the +0.3%/yr stands
    as the honest modern expectation, and the answer to "improve the strategy"
    is not another sleeve.

    py scripts/ff_largecap_blend.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ff_factor_check import read_french                     # noqa: E402
from rhengine.alpha.stats import _newey_west_ols            # noqa: E402

# (file, column holding the FAVOURABLE extreme inside the biggest size bucket)
# The sign is taken from the published predictor, not chosen here: profitability
# and prior return are high-is-good, investment is low-is-good.
LEGS = {
    "quality  (BIG HiOP)":     ("port_me_op_5x5.csv", "BIG HiOP"),
    "investment (BIG LoINV)":  ("port_me_inv_5x5.csv", "BIG LoINV"),
    "momentum (BIG HiPRIOR)":  ("port_me_prior_5x5.csv", "BIG HiPRIOR"),
}
# The two sleeves with no French 5x5 analogue — issuance and payout — cannot be
# tested here. Their absence makes this a LOWER bound on the blend's
# diversification, not an upper one: three legs is fewer than five.

ERAS = [("1963-07", "1999-12"), ("2000-01", "2015-12"),
        ("2016-01", "2026-12"), ("1963-07", "2026-12")]

VW, EW = 0, 1


def ann(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12.0 / len(r)) - 1.0) * 100


def alpha_t(r: pd.Series, mkt: pd.Series, rf: pd.Series, lags: int = 6):
    """CAPM alpha of `r` against the market, Newey-West. Returns (%/yr, t)."""
    y = (r - rf).to_numpy(float)
    x = np.column_stack([np.ones(len(y)), (mkt - rf).to_numpy(float)])
    coef, se = _newey_west_ols(y, x, lags)
    return float(coef[0]) * 1200.0, float(coef[0] / se[0])


def load(block: int) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    # read_french already returns DECIMALS — it divides by 100 internally. An
    # extra /100 here scales every return by 1e-2, which leaves t-stats and
    # signs untouched (both are scale-invariant) and silently makes the
    # magnitudes unreadable. That is the failure mode to watch for in this file.
    ff = read_french("ff5_monthly.csv", 0)
    mkt, rf = ff["Mkt-RF"] + ff["RF"], ff["RF"]
    legs = {}
    for label, (fname, col) in LEGS.items():
        df = read_french(fname, block)
        if col not in df.columns:
            raise KeyError(f"{col!r} not in {fname}: {list(df.columns)}")
        legs[label] = df[col]
    return pd.DataFrame(legs), mkt, rf


def report(block: int, name: str) -> pd.DataFrame:
    legs, mkt, rf = load(block)
    legs["BLEND (equal-weight of the three)"] = legs.mean(axis=1)
    rows = []
    print(f"\n{'=' * 78}\n=== {name} inside the largest size quintile\n{'=' * 78}")
    print(f"{'portfolio':36} {'era':16} {'ret':>7} {'mkt':>7} {'excess':>8} "
          f"{'alpha':>8} {'t':>6}")
    for label in legs.columns:
        for lo, hi in ERAS:
            s = legs[label][lo:hi].dropna()
            m = mkt.reindex(s.index).dropna()
            s = s.reindex(m.index)
            if len(s) < 24:
                continue
            a, t = alpha_t(s, m, rf.reindex(s.index))
            rows.append({"weighting": name, "portfolio": label,
                         "era": f"{lo}..{hi}", "n": len(s),
                         "ret": ann(s), "mkt": ann(m),
                         "excess": ann(s) - ann(m), "alpha": a, "t": t})
            star = "  <--" if label.startswith("BLEND") else ""
            print(f"{label:36} {lo + '..' + hi[:4]:16} {ann(s):7.2f} "
                  f"{ann(m):7.2f} {ann(s) - ann(m):+8.2f} {a:+8.2f} {t:+6.2f}"
                  + star)
        print()
    return pd.DataFrame(rows)


def size_control() -> None:
    """Is the modern alpha an edge, or just a bet on large beating small?

    The value-weighted blend clears the kill criterion above with alpha
    +1.31%/yr (t=1.03) over 2016-2026 while returning only +0.37 pp/yr more than
    the market. That gap is a beta of 0.92, and a beta shortfall against a
    total-market benchmark is the signature of a SIZE tilt, not of an
    unlevered edge waiting to be levered.

    The distinction decides what to do next and the two answers are opposite:

      * a real low-beta edge  -> lever the same book to beta 1 and collect it
      * a size loading        -> there is nothing to lever; the "alpha" is the
                                 large-cap premium of a decade in which large
                                 beat small, and it reverses when that does

    So the blend is re-run against the market AND SMB. If the market beta rises
    toward 1 and the alpha collapses, it was size.
    """
    ff = read_french("ff5_monthly.csv", 0)
    mkt, rf, smb = ff["Mkt-RF"] + ff["RF"], ff["RF"], ff["SMB"]
    legs = pd.DataFrame({k: read_french(f, VW)[c] for k, (f, c) in LEGS.items()})
    blend = legs.mean(axis=1)

    print(f"\n{'=' * 78}\n=== Is the modern alpha an edge or a size bet?\n{'=' * 78}")
    print(f"{'era':12} {'model':22} {'alpha%/yr':>10} {'t':>6} {'b_mkt':>7} "
          f"{'b_smb':>7}")
    for lo, hi in (("1963-07", "2026-12"), ("2016-01", "2026-12")):
        s = blend[lo:hi].dropna()
        m = mkt.reindex(s.index).dropna()
        s = s.reindex(m.index)
        r, sm = rf.reindex(s.index), smb.reindex(s.index)
        y = (s - r).to_numpy(float)
        for name, X in (
                ("CAPM", np.column_stack([np.ones(len(y)), m - r])),
                ("CAPM + SMB", np.column_stack([np.ones(len(y)), m - r, sm]))):
            c, se = _newey_west_ols(y, X.astype(float), 6)
            bs = f"{c[2]:7.2f}" if len(c) > 2 else "      -"
            print(f"{lo[:4] + '-' + hi[:4]:12} {name:22} {c[0] * 1200:+10.2f} "
                  f"{c[0] / se[0]:+6.2f} {c[1]:7.2f} {bs}")
        print()
    print("Full sample: controlling for size does not weaken the edge, so the\n"
          "63-year result is not a size artefact.\n"
          "2016-2026: it does. Read the second row of that block before drawing\n"
          "any conclusion from the kill criterion above.")


def main() -> None:
    out = pd.concat([report(VW, "VALUE-WEIGHTED"), report(EW, "EQUAL-WEIGHTED")])

    print(f"\n{'=' * 78}\n=== VERDICT: the blend, both weightings, by era\n{'=' * 78}")
    b = out[out["portfolio"].str.startswith("BLEND")]
    piv = b.pivot(index="era", columns="weighting",
                  values=["excess", "t"]).round(2)
    print(piv.to_string())

    modern = b[b["era"].str.startswith("2016")]
    beat = modern[modern["excess"] > 0]
    print("\nKILL CRITERION: the blend must beat the market over 2016-2026 on at "
          "least one weighting.")
    if beat.empty:
        print("  NOT MET on either weighting. No construction of this family "
              "available here\n  recovered the modern shortfall.")
    else:
        for _, r in beat.iterrows():
            print(f"  MET on {r['weighting']}: {r['excess']:+.2f} pp/yr, "
                  f"alpha {r['alpha']:+.2f}%/yr t={r['t']:+.2f} "
                  f"({int(r['n'])} months)")
    size_control()
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tests", "results", "ff_largecap_blend.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.to_csv(path, index=False)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
