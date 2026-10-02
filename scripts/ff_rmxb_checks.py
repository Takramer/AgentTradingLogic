"""ff_rmxb_checks.py — two pre-registered looks at the frozen French blend.

Logged 2026-10-02 in research_data/trials.jsonl BEFORE either number was read.
Neither look is allowed to change a shipped default on its own.

1. Months strictly after 2026-07. Print them. Do not tune.
2. Vol-scale the momentum leg (6-month vol, lag 1, cap 1.5, renormalise
   against quality, charge the extra turnover at 20 bps). ADOPT only if
   full-sample alpha t stays >= 2.0 AND the worst month is less severe than
   the unscaled blend AND 2016-2026 excess vs the market is within 1.0 pp/yr
   of the unscaled blend. Otherwise leave it off.

    py scripts/ff_rmxb_checks.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ff_blend import (  # noqa: E402
    COST_BPS, ann, big_col, evaluate, net_of_cost, read_french,
)
from rhengine.alpha.stats import sharpe_ci  # noqa: E402


def _ci(r: pd.Series, rf: pd.Series) -> tuple[float, float]:
    df = pd.concat({"r": r, "f": rf}, axis=1).dropna()
    ex = df["r"] - df["f"]
    sr = float(ex.mean() / ex.std())
    # pandas kurtosis is excess; sharpe_ci wants non-excess (normal = 3)
    return sharpe_ci(sr, len(ex), float(ex.skew()), float(ex.kurtosis()) + 3.0)


def main() -> None:
    ff5 = read_french("ff5_monthly.csv")
    ff5.columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"][:ff5.shape[1]]
    rf, mkt = ff5["RF"], ff5["Mkt-RF"] + ff5["RF"]
    op = read_french("port_me_op_5x5.csv", block=0)
    op.columns = [str(c) for c in op.columns]
    pr = read_french("port_me_prior_5x5.csv", block=0)
    pr.columns = [str(c) for c in pr.columns]
    q = net_of_cost(op[big_col(op.columns, "BIG HiOP")].dropna(), "quality")
    m = net_of_cost(pr[big_col(pr.columns, "BIG HiPRIOR")].dropna(), "momentum")
    idx = q.index.intersection(m.index)
    q, m = q.reindex(idx), m.reindex(idx)
    base = 0.5 * q + 0.5 * m

    vol = m.rolling(6).std().shift(1)
    k = (float(m.std()) / vol).clip(upper=1.5)
    w_m = ((0.5 * k) / (0.5 * k + 0.5)).fillna(0.5)
    scaled = (1.0 - w_m) * q + w_m * m
    scaled = scaled - w_m.diff().abs().fillna(0.0) * (COST_BPS / 1e4)

    base_res = evaluate("unscaled", base, mkt, rf)
    sc_res = evaluate("vol-scaled", scaled.dropna(), mkt, rf)

    def excess(r: pd.Series) -> float:
        x = r.loc["2016-01":"2026-12"].dropna()
        mk = mkt.reindex(x.index).dropna()
        x = x.reindex(mk.index)
        return ann(x) - ann(mk)

    worst_b, worst_s = float(base.min() * 100), float(scaled.dropna().min() * 100)
    ex_b, ex_s = excess(base), excess(scaled)
    t_ok = sc_res["t"] >= 2.0
    worst_ok = worst_s > worst_b
    era_ok = abs(ex_s - ex_b) <= 1.0
    adopt = t_ok and worst_ok and era_ok

    print("VOL-SCALE  (adopt only if all three gates pass)")
    print(f"  alpha t     unscaled {base_res['t']:+.2f}   scaled {sc_res['t']:+.2f}"
          f"   >= 2.0? {t_ok}")
    print(f"  CAGR        unscaled {base_res['cagr']:.2f}%   scaled {sc_res['cagr']:.2f}%"
          f"   market {base_res['mkt']:.2f}%")
    print(f"  worst month unscaled {worst_b:+.2f}%   scaled {worst_s:+.2f}%"
          f"   improved? {worst_ok}")
    print(f"  2016-2026 excess unscaled {ex_b:+.2f}pp   scaled {ex_s:+.2f}pp"
          f"   within 1pp? {era_ok}")
    print(f"  VERDICT: {'ADOPT' if adopt else 'LEAVE OFF'}")

    print("\nSHARPE 95% CI, annualised, excess over RF")
    for name, series in (("unscaled blend", base),
                         ("market", mkt.reindex(base.index)),
                         ("vol-scaled", scaled.dropna())):
        lo, hi = _ci(series, rf)
        print(f"  {name:16} {lo:+.2f} .. {hi:+.2f}")

    fwd = base.loc["2026-08-01":]
    mk = mkt.reindex(fwd.index)
    print("\nFORWARD  months after 2026-07 — not an input to the verdict above")
    print(f"  last month on file {base.index.max().date()}   n={len(fwd)}")
    for dt, r in fwd.items():
        mm = float(mk.loc[dt])
        print(f"  {dt.date()}  blend {r * 100:+.2f}%  market {mm * 100:+.2f}%"
              f"  excess {(r - mm) * 100:+.2f}pp")


if __name__ == "__main__":
    main()
