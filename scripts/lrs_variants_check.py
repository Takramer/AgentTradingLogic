"""lrs_variants_check.py — can LRS-2x keep its edge with a smaller drawdown?

Three variants, pre-registered in the trial ledger (family index-leverage)
before this script first ran. Same data, costs and lag as lrs_check.py.

    lrs_vt    200-day trend, exposure capped at 0.30 / 21-day realized vol (max 2x)
    vt        the same vol cap with no trend filter
    lrs1.5x   200-day trend at a flat 1.5x

Exposure e is held the cheapest way: SPY + T-bills when e <= 1, SPY + SSO when
e > 1. lrs2x is rebuilt through the same path and must match lrs_check exactly.

    .venv/bin/python scripts/lrs_variants_check.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrs_check as L                                   # noqa: E402

LAG, VOL_TARGET, VOL_WIN, BAND = 2, 0.30, 21, 0.25
OOS = ("2016-07-01", "2026-09-30")


def exposure_returns(e: pd.Series, df: pd.DataFrame) -> pd.Series:
    """Daily return of holding exposure e (0..2) as SPY/SSO/T-bills, net of
    fees, financing and 10 bps per unit of one-way turnover."""
    dt = df.index.to_series().diff().dt.days.fillna(1) / 365.25
    spy = e.clip(upper=1) - (e - 1).clip(lower=0)       # 2 - e above 1x
    sso = (e - 1).clip(lower=0)
    cash = 1 - spy - sso
    r = (spy * (df.mkt - L.ER[1] * dt)
         + sso * (2 * df.mkt - (df.rf + L.SPREAD * dt) - L.ER[2] * dt)
         + cash * df.rf)
    # ponytail: weights reset daily (no intraday drift); fine at a 0.25 band
    turn = (spy.diff().abs() + sso.diff().abs() + cash.diff().abs()).fillna(0) / 2
    return r - turn * L.SWITCH_BPS / 1e4


def banded(target: pd.Series) -> pd.Series:
    """Trade only when the target moves more than BAND from what is held, or on
    entry/exit (target or holding at zero)."""
    held, out = 0.0, np.empty(len(target))
    for i, t in enumerate(target.to_numpy()):
        if np.isnan(t):
            t = 0.0
        if (t == 0) != (held == 0) or abs(t - held) > BAND:
            held = t
        out[i] = held
    return pd.Series(out, index=target.index)


def build(df: pd.DataFrame) -> pd.DataFrame:
    base = L.strategies(df, LAG)                        # market, lrs2x, on, rf
    idx = (1 + df.mkt).cumprod()
    on = (idx > idx.rolling(L.SMA).mean()).astype(float)
    vol = df.mkt.rolling(VOL_WIN).std() * np.sqrt(252)
    cap = (VOL_TARGET / vol).clip(upper=2.0)
    e = {"lrs2x_rebuilt": 2.0 * on,
         "lrs1.5x": 1.5 * on,
         "lrs_vt": banded(on * cap),
         "vt": banded(cap)}
    out = base[["market", "lrs2x", "on", "rf"]].copy()
    for k, v in e.items():
        out[k] = exposure_returns(v.shift(LAG), df).reindex(out.index)
        out["e_" + k] = v.shift(LAG).reindex(out.index)
    return out


def drawdowns(r: pd.Series, k: int = 5) -> list[tuple]:
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    rows, i, n = [], 0, len(dd)
    v = dd.to_numpy()
    while i < n:
        if v[i] < 0:
            j = i
            while j < n and v[j] < 0:
                j += 1
            seg = dd.iloc[i:j]
            rows.append((seg.min(), dd.index[i - 1].date() if i else dd.index[0].date(),
                         seg.idxmin().date(), dd.index[j].date() if j < n else None))
            i = j
        else:
            i += 1
    return sorted(rows)[:k]


def main() -> None:
    s = build(L.load())
    gap = (s.lrs2x - s.lrs2x_rebuilt).abs().max()
    assert gap < 1e-12, f"exposure path does not reproduce lrs2x (max gap {gap})"
    cols = ["market", "lrs2x", "lrs1.5x", "lrs_vt", "vt"]
    res = {}
    for name, lo, hi in (("FULL", "1927", "2026"), ("2016+", *OOS)):
        w = s.loc[lo:hi]
        print(f"\n{name} ({w.index[0].date()} .. {w.index[-1].date()})")
        print(f"  {'':9}{'CAGR':>8}{'maxDD':>8}{'Calmar':>8}{'Sharpe':>8}{'alpha t':>9}"
              f"{'avg e':>7}{'trail10y':>9}")
        for c in cols:
            p = L.perf(w[c], w.rf, w.market)
            p["calmar"] = p["cagr"] / -p["maxdd"]
            res[(name, c)] = p
            avg_e = w["e_" + c].mean() if "e_" + c in w else (2 * w.on.mean() if c == "lrs2x" else 1.0)
            print(f"  {c:9}{p['cagr']:>8.2%}{p['maxdd']:>8.1%}{p['calmar']:>8.2f}"
                  f"{p['sharpe']:>8.2f}{p['alpha_t']:>9.2f}{avg_e:>7.2f}"
                  f"{L.rolling_trail(w, c, 10) if c != 'market' else 0:>9.0%}")

    print("\nERAS — CAGR")
    beats = {c: 0 for c in cols[1:]}
    for lo, hi in L.ERAS:
        w = s.loc[lo:hi]
        c = {k: (1 + w[k]).prod() ** (1 / L.years(w.index)) - 1 for k in cols}
        for k in beats:
            beats[k] += c[k] > c["market"]
        print(f"  {lo}-{hi}  " + "  ".join(f"{k} {c[k]:6.2%}" for k in cols))

    print("\nWORST 5 DRAWDOWNS (depth, peak, trough, recovered)")
    for c in ("market", "lrs2x", "lrs_vt", "lrs1.5x"):
        print(f"  {c}")
        for d, pk, tr, rec in drawdowns(s.loc["1927":, c]):
            print(f"    {d:7.1%}  {pk}  {tr}  {rec or 'not yet'}")

    print("\nKILL CRITERION (pre-registered)")
    for c in ("lrs_vt", "vt", "lrs1.5x"):
        k = [res[("FULL", c)]["cagr"] > res[("FULL", "market")]["cagr"],
             res[("FULL", c)]["maxdd"] > res[("FULL", "lrs2x")]["maxdd"],
             res[("2016+", c)]["maxdd"] > res[("2016+", "lrs2x")]["maxdd"],
             res[("2016+", c)]["cagr"] > res[("2016+", "market")]["cagr"],
             beats[c] >= 4]
        print(f"  {c:8} full CAGR>mkt {'PASS' if k[0] else 'FAIL'} | DD<lrs2x full "
              f"{'PASS' if k[1] else 'FAIL'} 2016+ {'PASS' if k[2] else 'FAIL'} | 2016+ CAGR>mkt "
              f"{'PASS' if k[3] else 'FAIL'} | eras {beats[c]}/5 -> "
              f"{'ACCEPTED' if all(k) else 'REJECTED'}")


if __name__ == "__main__":
    main()
