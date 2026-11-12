"""lrs_vt_extra_check.py — two follow-ups to LRS-VT, pre-registered in the ledger
(family index-leverage) before this script first ran.

    A. LRS-VT decided at 15:15 ET on the live SPY price (2016-2026, one-minute bars)
    B. LRS-VT holding 10-year Treasuries instead of T-bills when the trend is off
       (1963-2026, bond rebuilt from Yahoo ^TNX yields, checked against IEF)

    .venv/bin/python scripts/lrs_vt_extra_check.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrs_1515_check as M                              # noqa: E402
import lrs_check as L                                   # noqa: E402
import lrs_variants_check as V                          # noqa: E402
from rhengine.data_local import fetch_yahoo             # noqa: E402

BOND_ER = 0.0015
PERIODS = [("1963", "1981"), ("1982", "1999"), ("2000", "2019"), ("2020", "2026-09-30")]


def weights(e: pd.Series) -> pd.DataFrame:
    spy = e.clip(upper=1) - (e - 1).clip(lower=0)
    sso = (e - 1).clip(lower=0)
    return pd.DataFrame({"spy": spy, "sso": sso, "cash": 1 - spy - sso})


def cost(w: pd.DataFrame) -> pd.Series:
    return w.diff().abs().sum(axis=1).fillna(0) / 2 * L.SWITCH_BPS / 1e4


def report(s: pd.DataFrame, cols, title: str) -> dict:
    print(f"\n{title} ({s.index[0].date()} .. {s.index[-1].date()}, {L.years(s.index):.1f} yr)")
    print(f"  {'':14}{'CAGR':>8}{'maxDD':>8}{'Calmar':>8}{'Sharpe':>8}{'alpha t':>9}")
    res = {}
    for c in cols:
        p = L.perf(s[c], s.rf, s.market)
        res[c] = p
        print(f"  {c:14}{p['cagr']:>8.2%}{p['maxdd']:>8.1%}{p['cagr'] / -p['maxdd']:>8.2f}"
              f"{p['sharpe']:>8.2f}{p['alpha_t']:>9.2f}")
    return res


# ── A. 15:15 ─────────────────────────────────────────────────────────────────
def part_a() -> None:
    d = M.daily()
    r = d.adj.pct_change()
    live = d.p1515 * (d.adj / d.raw).shift(1)
    on_1515 = (live > (d.adj.shift(1).rolling(199).sum() + live) / 200).astype(float)
    on_close = (d.adj > d.adj.rolling(200).mean()).astype(float)
    vol = r.rolling(V.VOL_WIN).std() * np.sqrt(252)
    cap = lambda v: (V.VOL_TARGET / v).clip(upper=2.0)          # noqa: E731

    b = d.adj / live - 1                                         # 15:15 -> close
    a = (1 + r) / (1 + b) - 1                                    # prior close -> 15:15
    lev_a = 2 * a
    lev_b = (1 + 2 * r) / (1 + lev_a) - 1                        # SSO resets at the close
    fin = d.rf + L.SPREAD / 252 + L.ER[2] / 252
    spy_fee = L.ER[1] / 252

    def at_1515(e):
        P, N = weights(e.shift(1)), weights(e)
        leg1 = P.spy * a + P.sso * lev_a + P.cash * d.rf * 0.9
        leg2 = N.spy * b + N.sso * lev_b + N.cash * d.rf * 0.1
        return (1 + leg1) * (1 + leg2) - 1 - P.spy * spy_fee - P.sso * fin - cost(weights(e))

    def at_close(e):                                             # e already lagged
        w = weights(e)
        return w.spy * (r - spy_fee) + w.sso * (2 * r - fin) + w.cash * d.rf - cost(w)

    s = pd.DataFrame({"market": r, "rf": d.rf})
    s["lrs_vt_1515"] = at_1515(V.banded(on_1515 * cap(vol.shift(1))))
    s["lrs_vt_lag2"] = at_close(V.banded(on_close * cap(vol)).shift(2))
    ref = M.run(d)
    chk = pd.DataFrame({"x": at_1515(2 * on_1515), "y": at_close((2 * on_close).shift(2))}).loc[ref.index]
    gap = max((chk.x - ref.lrs_1515).abs().max(), (chk.y - ref.lrs_lag2).abs().max())
    assert gap < 1e-12, f"exposure path does not reproduce lrs_check/lrs_1515 (gap {gap})"
    s = s.loc[ref.index]
    s["lrs2x_1515"], s["lrs2x_lag2"] = ref.lrs_1515, ref.lrs_lag2
    res = report(s, ["market", "lrs2x_lag2", "lrs2x_1515", "lrs_vt_lag2", "lrs_vt_1515"],
                 "A. LRS-VT at 15:15 vs next close (SPY)")
    k1 = res["lrs_vt_1515"]["cagr"] >= res["lrs_vt_lag2"]["cagr"]
    k2 = res["lrs_vt_1515"]["maxdd"] >= res["lrs_vt_lag2"]["maxdd"]
    print(f"  KILL: CAGR >= lag 2 {'PASS' if k1 else 'FAIL'} | maxDD no worse "
          f"{'PASS' if k2 else 'FAIL'} -> {'ACCEPTED' if k1 and k2 else 'REJECTED'}")


# ── B. Treasuries when the trend is off ──────────────────────────────────────
def yahoo(t: str, days: int) -> pd.Series:
    b = fetch_yahoo(t, lookback_days=days, adjust=True)
    return pd.Series([x["close"] for x in b], index=pd.to_datetime(
        [x["time"] for x in b], unit="ms").tz_localize("UTC")
        .tz_convert("America/New_York").tz_localize(None).normalize())


def bond_returns(y: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    """Constant-maturity 10-year par bond: buy at par with coupon y(t-1), accrue
    the coupon, reprice at y(t). Ignores roll-down."""
    y = (y / 100).reindex(idx, method="ffill")
    c, dt = y.shift(1), idx.to_series().diff().dt.days / 365.25
    v = (1 + y / 2) ** -20
    price = c / y * (1 - v) + v
    return price - 1 + c * dt - BOND_ER * dt


def part_b() -> None:
    df = L.load()
    tnx = yahoo("^TNX", 24000)
    bond = bond_returns(tnx, df.index)

    ief = yahoo("IEF", 9000).pct_change()
    both = pd.DataFrame({"ief": ief, "model": bond_returns(tnx, ief.index)}).dropna()
    yrs = L.years(both.index)
    print(f"\nB. bond check vs IEF {both.index[0].date()}..{both.index[-1].date()}: "
          f"daily corr {both.ief.corr(both.model):.3f}, CAGR IEF "
          f"{(1 + both.ief).prod() ** (1 / yrs) - 1:.2%} vs model "
          f"{(1 + both.model).prod() ** (1 / yrs) - 1:.2%}, vol "
          f"{both.ief.std() * np.sqrt(252):.1%} vs {both.model.std() * np.sqrt(252):.1%}")

    idx = (1 + df.mkt).cumprod()
    on = (idx > idx.rolling(L.SMA).mean()).astype(float)
    vol = df.mkt.rolling(V.VOL_WIN).std() * np.sqrt(252)
    e = V.banded(on * (V.VOL_TARGET / vol).clip(upper=2.0)).shift(V.LAG)
    dt = df.index.to_series().diff().dt.days.fillna(1) / 365.25
    sso_r = 2 * df.mkt - (df.rf + L.SPREAD * dt) - L.ER[2] * dt
    spy_r = df.mkt - L.ER[1] * dt

    def run(off_in_bonds: bool) -> pd.Series:
        w = weights(e)
        w["bond"] = 0.0
        if off_in_bonds:
            off = e == 0
            w.loc[off, "bond"], w.loc[off, "cash"] = 1.0, 0.0
        return w.spy * spy_r + w.sso * sso_r + w.cash * df.rf + w.bond * bond - cost(w)

    s = pd.DataFrame({"market": df.mkt, "rf": df.rf, "bond": bond,
                      "lrs_vt": run(False), "lrs_vt_bonds": run(True)})
    gap = (s.lrs_vt - V.build(df).lrs_vt.reindex(s.index)).loc["1963":].abs().max()
    assert gap < 1e-12, f"T-bill path does not reproduce lrs_variants_check (gap {gap})"
    s = s.loc["1963":"2026-09-30"]
    s["lrs2x"] = L.strategies(df, V.LAG).lrs2x.reindex(s.index)
    cols = ["market", "bond", "lrs2x", "lrs_vt", "lrs_vt_bonds"]
    full = report(s, cols, "B. Treasuries instead of T-bills when the trend is off")
    oos = report(s.loc[V.OOS[0]:V.OOS[1]], cols, "B. same, 2016+")
    print("  periods, CAGR:")
    wins = 0
    for lo, hi in PERIODS:
        w = s.loc[lo:hi]
        c = {k: (1 + w[k]).prod() ** (1 / L.years(w.index)) - 1 for k in cols}
        wins += c["lrs_vt_bonds"] > c["lrs_vt"]
        print(f"    {lo}-{hi[:4]}  " + "  ".join(f"{k} {c[k]:6.2%}" for k in cols))
    print("  worst drawdowns, lrs_vt_bonds:")
    for dd, pk, tr, rec in V.drawdowns(s.lrs_vt_bonds):
        print(f"    {dd:7.1%}  {pk}  {tr}  {rec or 'not yet'}")
    k = [full["lrs_vt_bonds"]["cagr"] > full["lrs_vt"]["cagr"],
         full["lrs_vt_bonds"]["maxdd"] >= full["lrs_vt"]["maxdd"],
         oos["lrs_vt_bonds"]["maxdd"] >= oos["lrs_vt"]["maxdd"],
         wins >= 3]
    print(f"  KILL: CAGR {'PASS' if k[0] else 'FAIL'} | maxDD full {'PASS' if k[1] else 'FAIL'} "
          f"2016+ {'PASS' if k[2] else 'FAIL'} | periods {wins}/4 -> "
          f"{'ACCEPTED' if all(k) else 'REJECTED'}")


if __name__ == "__main__":
    part_a()
    part_b()
