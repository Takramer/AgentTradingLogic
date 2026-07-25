"""rmx_ic.py — cross-sectional information coefficients for the candidate legs.

Before arguing about portfolio construction, establish whether the raw signals
rank next month's return at all. This is the question `rhengine/ic.py` asked of
the old sleeves, asked here on non-overlapping MONTHLY observations so the
t-stat means something (audit FC-3: the old IC t-stats pooled overlapping
daily signals and were inflated ~5.5x).

For each rebalance month and each candidate signal:
  rank-IC          Spearman(signal, next-month return) across the eligible names
  decile spread    top-decile mean return minus bottom-decile mean, next month
Both are averaged over months, with a t-stat on the monthly series (n = months,
which is the honest effective sample).

    py scripts/rmx_ic.py --lo 2005-01-01 --hi 2015-12-31
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.rmx import load_panel                                # noqa: E402
from rhengine.alpha.signals import build_monthly, residual_stats         # noqa: E402


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    if len(x) < 6 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="2005-01-01")
    ap.add_argument("--hi", default="2015-12-31")
    ap.add_argument("--decile", type=int, default=10)
    args = ap.parse_args()

    p = load_panel()
    warm = pd.Timestamp(args.lo) - pd.DateOffset(months=51)
    mf = build_monthly(p, str(warm.date()), args.hi)
    me = mf.month_ends
    first = int(np.searchsorted(me.values, np.datetime64(pd.Timestamp(args.lo))))

    recs = []
    for i in range(max(first, 48), len(me) - 1):
        d = me[i]
        names = p.eligible(d).intersection(mf.stock_ex.columns)
        if len(names) < 60:
            continue
        st = residual_stats(mf, i, names)
        if st.empty:
            continue
        st = st[(st["nobs"] >= 30) & (st["form_obs"] >= 9)].dropna(subset=["resmom"])
        if len(st) < 60:
            continue
        nxt = mf.raw.iloc[i + 1][st.index]
        past12 = (1.0 + mf.raw.iloc[i - 11:i][st.index]).prod() - 1.0
        vol12 = mf.raw.iloc[i - 11:i][st.index].std()
        # 52-week-high proximity (George & Hwang 2004,
        # https://doi.org/10.1111/j.1540-6261.2004.00695.x)
        px = p.adj.loc[:d, st.index]
        near_high = px.iloc[-1] / px.iloc[-252:].max()
        # long-term reversal, months -60..-13 (De Bondt & Thaler 1985,
        # https://doi.org/10.1111/j.1540-6261.1985.tb05004.x)
        lt = ((1.0 + mf.raw.iloc[max(i - 59, 0):i - 11][st.index]).prod() - 1.0
              if i >= 60 else pd.Series(np.nan, index=st.index))
        cand = {
            "resmom": st["resmom"],
            "mom12_1": past12,
            "riskadj_mom": past12 / vol12.replace(0.0, np.nan),
            "near_52w_high": near_high,
            "reversal": -mf.raw.iloc[i][st.index],
            "lt_reversal": -lt,
            "lowvol": -st["resvol"],
            "lowbeta": -st["beta"],
        }
        row = {"date": d, "n": len(st)}
        ok = nxt.notna()
        for k, v in cand.items():
            v = v.reindex(st.index)
            m = ok & v.notna()
            if m.sum() < 40:
                continue
            row[f"ic_{k}"] = float(sps.spearmanr(v[m], nxt[m]).statistic)
            q = args.decile
            cut = max(int(m.sum() / q), 5)
            order = v[m].sort_values()
            row[f"spread_{k}"] = float(nxt[order.index[-cut:]].mean()
                                      - nxt[order.index[:cut]].mean())
            row[f"top_{k}"] = float(nxt[order.index[-cut:]].mean())
        row["universe_mean"] = float(nxt[ok].mean())
        recs.append(row)

    df = pd.DataFrame(recs).set_index("date")
    print(f"{len(df)} monthly cross-sections, {args.lo} .. {args.hi}, "
          f"mean universe {df['n'].mean():.0f} names\n")
    print("{:<10} {:>8} {:>8} {:>10} {:>8} {:>11} {:>8}".format(
        "signal", "meanIC", "IC t", "IR", "spread%", "spread t", "top-uni%"))
    keys = ("resmom", "mom12_1", "riskadj_mom", "near_52w_high",
            "reversal", "lt_reversal", "lowvol", "lowbeta")
    for k in keys:
        ic, sp, top = df.get(f"ic_{k}"), df.get(f"spread_{k}"), df.get(f"top_{k}")
        if ic is None:
            continue
        ir = ic.mean() / ic.std(ddof=1) if ic.std(ddof=1) > 0 else np.nan
        excess = (top - df["universe_mean"]) * 100
        print("{:<10} {:>+8.4f} {:>+8.2f} {:>10.3f} {:>+8.3f} {:>+11.2f} "
              "{:>+8.3f}".format(k, ic.mean(), tstat(ic), ir, sp.mean() * 100,
                                 tstat(sp), excess.mean()))
    print("\nuniverse mean monthly return {:+.3f}%".format(df["universe_mean"].mean() * 100))
    print("\nIC by 3-year block (is the sign stable, or one lucky stretch?)")
    blocks = df.index.year // 3 * 3
    for k in keys:
        if f"ic_{k}" not in df:
            continue
        g = df[f"ic_{k}"].groupby(blocks).mean()
        print("{:<10} ".format(k) + "  ".join(
            f"{y}:{v:+.3f}" for y, v in g.items()))


if __name__ == "__main__":
    main()
