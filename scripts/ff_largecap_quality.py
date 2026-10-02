"""ff_largecap_quality.py — the one construction RMX-Q never tested.

RMX-Q's failure was diagnosed mechanically, not guessed: the operating-
profitability signal added +2.58%/yr in-sample and +1.62%/yr out-of-sample
against its own universe, while the equal-weight construction it sat on lost
3.3 pp/yr to cap-weighted SPY. The fix that follows from that decomposition is
to apply the same signal on **cap weights inside large caps**, so there is no
size drag to overcome.

That construction cannot be honestly tested on my S&P 500 panel any more - the
2019-2026 window has been opened. It CAN be tested on data I have never
looked at: Ken French's 25 size x operating-profitability portfolios,
value-weighted, monthly, 1963-2026, survivorship-free, no cost assumption of
mine and nothing I fitted.

QUESTION, fixed before running
    Within the LARGEST size quintile, does a value-weighted high-operating-
    profitability portfolio beat the market?

KILL CRITERION, fixed before running
    If BIG HiOP fails to beat the market in the full 1963-2026 sample OR in
    the most recent era, the cap-weighted large-cap quality tilt is rejected
    and the project's negative verdict stands unchanged.

    py scripts/ff_largecap_quality.py
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

ERAS = [("1963-07", "1979-12"), ("1980-01", "1999-12"),
        ("2000-01", "2015-12"), ("2016-01", "2026-12"),
        ("1963-07", "2026-12")]


def ann(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12.0 / len(r)) - 1.0) * 100


def sharpe(r: pd.Series, rf: pd.Series) -> float:
    ex = (r - rf.reindex(r.index)).dropna()
    return float(ex.mean() / ex.std() * np.sqrt(12)) if ex.std() > 0 else np.nan


def alpha_t(r: pd.Series, mkt: pd.Series, rf: pd.Series, lags: int = 6):
    df = pd.concat({"r": r, "m": mkt, "f": rf}, axis=1).dropna()
    y = (df["r"] - df["f"]).to_numpy(float)
    X = np.column_stack([np.ones(len(df)), (df["m"] - df["f"]).to_numpy(float)])
    coef, se = _newey_west_ols(y, X, lags)
    return coef[0] * 12 * 100, coef[0] / se[0], coef[1]


def main() -> None:
    ff5 = read_french("ff5_monthly.csv")
    ff5.columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"][:ff5.shape[1]]
    rf, mkt = ff5["RF"], ff5["Mkt-RF"] + ff5["RF"]

    p = read_french("port_me_op_5x5.csv", block=0)     # value-weighted monthly
    cols = [str(c) for c in p.columns]
    p.columns = cols

    big = [c for c in cols if c.startswith("BIG") or c.startswith("ME5")]
    if len(big) != 5:
        print("unexpected BIG columns:", big)
        return
    lo, hi = big[0], big[-1]
    mid_hi = [c for c in big[-2:]]

    print("=" * 82)
    print("Cap-weighted quality tilt INSIDE LARGE CAPS - French 25 ME x OP, 1963-2026")
    print("=" * 82)
    print(f"largest size quintile columns: {big}")
    print(f"  low-profitability leg : {lo}")
    print(f"  high-profitability leg: {hi}\n")

    print("{:<12} {:>10} {:>10} {:>9} {:>9} {:>10} {:>9} {:>8}".format(
        "era", "HiOP CAGR", "mkt CAGR", "HiOP Shp", "mkt Shp", "alpha/yr", "alpha t", "beta"))
    verdict = {}
    for a, b in ERAS:
        r = p[hi].loc[a:b].dropna()
        m = mkt.reindex(r.index).dropna()
        r = r.reindex(m.index)
        if len(r) < 24:
            continue
        al, at, be = alpha_t(r, m, rf.reindex(r.index))
        print("{:<12} {:>+9.2f}% {:>+9.2f}% {:>9.2f} {:>9.2f} {:>+9.2f}% {:>+9.2f} {:>8.2f}".format(
            f"{a[:4]}-{b[:4]}", ann(r), ann(m), sharpe(r, rf), sharpe(m, rf), al, at, be))
        verdict[(a, b)] = (ann(r) - ann(m), at)

    print("\nhigh minus low profitability, WITHIN large caps "
          "(is the sort doing anything at all?)")
    print("{:<12} {:>12} {:>10} {:>10}".format("era", "HiOP-LoOP", "t", "LoOP CAGR"))
    for a, b in ERAS:
        h = p[hi].loc[a:b].dropna()
        l = p[lo].loc[a:b].dropna()
        idx = h.index.intersection(l.index)
        if len(idx) < 24:
            continue
        d = (h.reindex(idx) - l.reindex(idx))
        t = float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))
        print("{:<12} {:>+11.2f}% {:>+10.2f} {:>+9.2f}%".format(
            f"{a[:4]}-{b[:4]}", ann(h.reindex(idx)) - ann(l.reindex(idx)), t,
            ann(l.reindex(idx))))

    print("\nmilder tilt: top TWO profitability quintiles inside large caps, "
          "equal blend")
    print("{:<12} {:>10} {:>10} {:>9} {:>9} {:>10} {:>9}".format(
        "era", "tilt CAGR", "mkt CAGR", "tilt Shp", "mkt Shp", "alpha/yr", "alpha t"))
    blend = p[mid_hi].mean(axis=1)
    for a, b in ERAS:
        r = blend.loc[a:b].dropna()
        m = mkt.reindex(r.index).dropna()
        r = r.reindex(m.index)
        if len(r) < 24:
            continue
        al, at, _ = alpha_t(r, m, rf.reindex(r.index))
        print("{:<12} {:>+9.2f}% {:>+9.2f}% {:>9.2f} {:>9.2f} {:>+9.2f}% {:>+9.2f}".format(
            f"{a[:4]}-{b[:4]}", ann(r), ann(m), sharpe(r, rf), sharpe(m, rf), al, at))

    print("\n" + "=" * 82)
    full = verdict.get(("1963-07", "2026-12"))
    recent = verdict.get(("2016-01", "2026-12"))
    ok_full = full is not None and full[0] > 0
    ok_recent = recent is not None and recent[0] > 0
    print(f"KILL CRITERION: beat the market in the full sample AND the recent era")
    print(f"  full 1963-2026 : {'PASS' if ok_full else 'FAIL'}  "
          f"({full[0]:+.2f} pp/yr, alpha t={full[1]:+.2f})" if full else "  n/a")
    print(f"  recent 2016-   : {'PASS' if ok_recent else 'FAIL'}  "
          f"({recent[0]:+.2f} pp/yr, alpha t={recent[1]:+.2f})" if recent else "  n/a")
    print(f"  VERDICT: {'PROCEED' if (ok_full and ok_recent) else 'REJECTED'}")
    print("=" * 82)


if __name__ == "__main__":
    main()
