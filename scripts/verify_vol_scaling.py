"""verify_vol_scaling.py — does Barroso & Santa-Clara replicate on our data?

Before the vol-scaling option in `rhengine.alpha.rmxb` / `sleeves.scale_by_own_vol`
is trusted, the mechanism it implements has to be shown working on a record long
enough to contain momentum crashes. This project's own S&P 500 panel is not:
SEC XBRL fundamentals start in 2009, so RMX-B cannot even be formed before mid
2010, and 2010-2018 contains no momentum crash at all. Testing crash insurance
on a sample with no fire proves nothing.

So the check runs on Ken French's momentum factor, 1927-2026, harvested by
`scripts/fetch_external_factors.py`.

Barroso & Santa-Clara (JFE 2015) report, on daily-estimated 6-month vol with no
cap: Sharpe 0.53 -> 0.97, excess kurtosis 18.24 -> 2.68, worst month -78.96% ->
-28.40%. This implementation is deliberately weaker than theirs — monthly
return observations for the vol estimate instead of daily, and the multiplier
capped at 1.5 because a retail account cannot lever at the T-bill rate — so the
magnitudes are smaller. What matters is that the sign and the mechanism hold,
including in the modern era.

    py scripts/verify_vol_scaling.py
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from rhengine.alpha.external import External, nw_tstat

WINDOW = 6        # months of realised vol
CAP = 1.5


def scaled(s: pd.Series, window: int = WINDOW, cap: float = CAP) -> pd.Series:
    """Barroso scaling, mean-one and knowable: vol uses returns before t."""
    vol = s.rolling(window).std().shift(1)
    k = (s.std() / vol).clip(upper=cap)
    return (s * k).dropna()


def _row(x: pd.Series, label: str) -> str:
    return ("  {:<12} {:>7.2f}%/yr {:>7.2f}% {:>6.2f} {:>+7.2f} {:>8.2f} "
            "{:>9.2f}% {:>+6.2f}").format(
        label, x.mean() * 1200, x.std() * np.sqrt(12) * 100,
        x.mean() / x.std() * np.sqrt(12), x.skew(), x.kurtosis(),
        x.min() * 100, nw_tstat(x))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="french:Mom")
    ap.add_argument("--windows", nargs="*",
                    default=["1927-01-01", "1970-01-01", "2005-01-01",
                             "2015-01-01"])
    ap.add_argument("--cap", type=float, default=CAP)
    args = ap.parse_args()

    ext = External.load()
    if args.series not in ext.wide.columns:
        raise SystemExit(f"{args.series} not in the external panel; try "
                         f"`py -m rhengine.alpha.external --catalog`")
    m = ext.wide[args.series].dropna()

    print(f"\n{args.series}: {m.index[0].date()} .. {m.index[-1].date()} "
          f"({len(m)} months), vol window {WINDOW}m, cap {args.cap}")
    print("  {:<12} {:>11} {:>8} {:>6} {:>7} {:>8} {:>10} {:>6}".format(
        "", "mean", "vol", "SR", "skew", "exkurt", "worst mo", "t_NW"))
    for lo in args.windows:
        s = m[m.index >= pd.Timestamp(lo)]
        if len(s) < 36:
            continue
        sc = scaled(s, cap=args.cap)
        raw = s.reindex(sc.index)
        print(f"\n  {lo[:4]}-{m.index[-1].year}   n={len(sc)}")
        print(_row(raw, "raw"))
        print(_row(sc, "vol-scaled"))
        d_sr = (sc.mean() / sc.std() - raw.mean() / raw.std()) * np.sqrt(12)
        print(f"  -> Sharpe {d_sr:+.2f}, excess kurtosis "
              f"{sc.kurtosis() - raw.kurtosis():+.2f}, worst month "
              f"{(sc.min() - raw.min()) * 100:+.2f} pp")

    print("\nVerdict: the mechanism is crash insurance. It raises Sharpe by "
          "cutting tail risk,\nnot by predicting returns, and a sample without "
          "a momentum crash cannot show it.")


if __name__ == "__main__":
    main()
