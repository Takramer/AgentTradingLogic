"""rmxq_ic.py — does gross profitability rank returns inside the S&P 500?

The screen that decides whether RMX-Q is built. Fundamentals update once a
year, so the same signal value persists for twelve monthly cross-sections and
the monthly IC series is autocorrelated by construction. Two honest readings
are therefore printed:

  * monthly rank-IC with a **Newey-West(12)** t-stat, which prices that overlap
    in (the naive t would be inflated exactly the way audit FC-3 describes);
  * **annual** June-to-June holding-period IC, fully non-overlapping, where n
    is the number of formation years and no correction is needed.

    py scripts/rmxq_ic.py --lo 2010-07-01 --hi 2018-12-31
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.fundamentals import Fundamentals          # noqa: E402
from rhengine.alpha.rmx import load_panel                     # noqa: E402
from rhengine.alpha.stats import _newey_west_ols              # noqa: E402

SIGNALS = ["op_be", "gp_at", "op_be_proxy", "roa", "net_margin", "asset_growth"]
NEGATIVE = {"asset_growth"}      # investment is a negative predictor


def nw_tstat(x: pd.Series, lags: int = 12) -> float:
    x = x.dropna().to_numpy(float)
    if len(x) < 24:
        return float("nan")
    X = np.ones((len(x), 1))
    coef, se = _newey_west_ols(x, X, lags)
    return float(coef[0] / se[0]) if se[0] > 0 else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="2010-07-01")
    ap.add_argument("--hi", default="2018-12-31")
    ap.add_argument("--top-frac", type=float, default=0.30)
    args = ap.parse_args()

    p = load_panel()
    f = Fundamentals.load()
    me = p.month_ends(args.lo, args.hi)

    rows = []
    for i, d in enumerate(me[:-1]):
        names = p.eligible(d)
        if len(names) < 60:
            continue
        nxt = (p.adj.loc[me[i + 1]] / p.adj.loc[d] - 1.0).reindex(names)
        ok = nxt.notna()
        row = {"date": d, "n": int(ok.sum()),
               "universe_mean": float(nxt[ok].mean())}
        for sig in SIGNALS:
            v = f.as_of(d, sig, names)
            if NEGATIVE and sig in NEGATIVE:
                v = -v
            m = v.index.intersection(nxt[ok].index)
            if len(m) < 50:
                continue
            row[f"cov_{sig}"] = len(m) / int(ok.sum())
            row[f"ic_{sig}"] = float(sps.spearmanr(v[m], nxt[m]).statistic)
            k = max(int(len(m) * args.top_frac), 10)
            order = v[m].sort_values()
            row[f"top_{sig}"] = float(nxt[order.index[-k:]].mean())
            row[f"bot_{sig}"] = float(nxt[order.index[:k]].mean())
        rows.append(row)

    df = pd.DataFrame(rows).set_index("date")
    print(f"{len(df)} monthly cross-sections {args.lo} .. {args.hi}, "
          f"mean universe {df['n'].mean():.0f} names, "
          f"top {args.top_frac:.0%} portfolio\n")

    print("{:<14} {:>6} {:>9} {:>8} {:>9} {:>11} {:>10}".format(
        "signal", "cover", "meanIC", "naive t", "NW(12) t", "top-uni %/mo", "top-bot %/mo"))
    for sig in SIGNALS:
        if f"ic_{sig}" not in df:
            print(f"{sig:<14} insufficient coverage")
            continue
        ic = df[f"ic_{sig}"].dropna()
        exc = (df[f"top_{sig}"] - df["universe_mean"]).dropna() * 100
        spr = (df[f"top_{sig}"] - df[f"bot_{sig}"]).dropna() * 100
        naive = ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))
        print("{:<14} {:>5.0%} {:>+9.4f} {:>+8.2f} {:>+9.2f} {:>+11.3f} {:>+10.3f}".format(
            sig, df[f"cov_{sig}"].mean(), ic.mean(), naive, nw_tstat(ic),
            exc.mean(), spr.mean()))

    print("\nannual (June -> June) non-overlapping holding-period test")
    print("{:<14} {:>8} {:>12} {:>12} {:>10}".format(
        "signal", "years", "top CAGR-ish", "uni CAGR-ish", "excess t"))
    junes = [d for d in me if d.month == 6]
    for sig in SIGNALS:
        tops, unis = [], []
        for j, d in enumerate(junes[:-1]):
            names = p.eligible(d)
            nd = junes[j + 1]
            r = (p.adj.loc[nd] / p.adj.loc[d] - 1.0).reindex(names).dropna()
            v = f.as_of(d, sig, names)
            if NEGATIVE and sig in NEGATIVE:
                v = -v
            m = v.index.intersection(r.index)
            if len(m) < 50:
                continue
            k = max(int(len(m) * args.top_frac), 10)
            order = v[m].sort_values()
            tops.append(float(r[order.index[-k:]].mean()))
            unis.append(float(r.mean()))
        if len(tops) < 4:
            print(f"{sig:<14} too few formation years")
            continue
        t_, u_ = np.array(tops), np.array(unis)
        d_ = t_ - u_
        tt = d_.mean() / (d_.std(ddof=1) / np.sqrt(len(d_)))
        print("{:<14} {:>8d} {:>+11.2f}% {:>+11.2f}% {:>+10.2f}".format(
            sig, len(tops), (np.prod(1 + t_) ** (1 / len(t_)) - 1) * 100,
            (np.prod(1 + u_) ** (1 / len(u_)) - 1) * 100, tt))

    print("\nyear-by-year excess of the top-30% GP/A basket over its universe")
    if "top_gp_at" in df:
        g = ((df["top_gp_at"] - df["universe_mean"]) * 100).groupby(df.index.year)
        print("  " + "  ".join(f"{y}:{v.sum():+.1f}" for y, v in g))


if __name__ == "__main__":
    main()
