"""lrs_crosscheck.py — recompute the LRS numbers with third-party libraries.

lrs_check.perf is hand-written. This recomputes the same figures another way
and exits 1 if any disagree beyond tolerance:

    max drawdown, compounding, Sharpe   quantstats
    alpha, its Newey-West t, beta       statsmodels OLS, HAC with 3 lags

Years are calendar years in both (lrs_check.years). quantstats' own CAGR
divides rows by 252, which is wrong on French daily data before 1952
(Saturday sessions), so it is not used for that.

    .venv/bin/python scripts/lrs_crosscheck.py
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd
import quantstats as qs
import statsmodels.api as sm

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrs_1515_check as M                              # noqa: E402
import lrs_check as L                                   # noqa: E402

TOL = {"cagr": 1e-4, "maxdd": 1e-6, "sharpe": 5e-3, "alpha_t": 0.15, "beta": 1e-3}


def independent(r: pd.Series, rf: pd.Series, mkt: pd.Series) -> dict:
    yrs = L.years(r.index)
    m = lambda s: (1 + s).resample("ME").prod() - 1     # noqa: E731
    y = m(r) - m(rf)
    X = sm.add_constant(m(mkt) - m(rf))
    fit = sm.OLS(y, X, missing="drop").fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    return {"cagr": (1 + qs.stats.comp(r)) ** (1 / yrs) - 1,
            "maxdd": qs.stats.max_drawdown(r),
            "sharpe": qs.stats.sharpe(r - rf, periods=len(r) / yrs),
            "alpha_t": float(fit.tvalues.iloc[0]),
            "beta": float(fit.params.iloc[1])}


def compare(name: str, frame: pd.DataFrame, cols) -> list[str]:
    bad = []
    for c in cols:
        a = L.perf(frame[c], frame.rf, frame.market)
        b = independent(frame[c], frame.rf, frame.market)
        row = []
        for k, tol in TOL.items():
            d = abs(a[k] - b[k])
            row.append(f"{k} {a[k]:+.4f}/{b[k]:+.4f}")
            if not np.isfinite(d) or d > tol:
                bad.append(f"{name} {c} {k}: repo {a[k]:.6f} vs independent {b[k]:.6f}")
        print(f"  {name:14}{c:9}" + "  ".join(row))
    return bad


def main() -> None:
    df = L.load()
    bad = []
    print("repo / independent")
    for lag in (1, 2):
        s = L.strategies(df, lag)
        bad += compare(f"lag{lag} full", s, ("lrs1x", "lrs2x", "bh2x"))
        bad += compare(f"lag{lag} 2016+", s.loc["2016-07-01":"2026-09-30"], ("lrs2x",))
    s = M.run(M.daily())
    bad += compare("15:15 SPY", s, ("lrs_lag2", "lrs_1515"))
    if bad:
        print("\nMISMATCH:\n  " + "\n  ".join(bad))
        sys.exit(1)
    print("\nAll figures agree within tolerance.")


if __name__ == "__main__":
    main()
