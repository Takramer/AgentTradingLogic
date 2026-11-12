"""lrs_check.py — Gayed & Bilello's "Leverage for the Long Run" on French daily data.

The only route to beating the S&P 500 this repo had not measured: take MORE
market exposure, and use a trend filter to step aside in the crashes that make
leverage ruinous. Published rule (Gayed & Bilello 2016, Dow Award): hold the
index at L x daily-reset while it closes above its 200-day average, T-bills
otherwise. Pre-registered in the trial ledger (family index-leverage, n=2)
before this script first ran; parameters are the paper's, nothing is tuned.

Data: Ken French daily Mkt-RF + RF, 1926-07 onward (CRSP value-weighted
market, a close proxy for the S&P 500 total return). Signal at close t, held
from t+1. Costs: borrowed leg pays RF + 0.5%/yr, 2x vehicle pays 0.90%/yr
(SSO's expense ratio), 1x pays 0.03%, every switch pays 10 bps.

    .venv/bin/python scripts/lrs_check.py            # pre-registered, lag 1
    .venv/bin/python scripts/lrs_check.py --lag 2    # trade at the NEXT close
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rhengine.alpha.stats import alpha_beta  # noqa: E402

SPREAD, SWITCH_BPS, SMA = 0.005, 10.0, 200
ER = {1: 0.0003, 2: 0.009}
ERAS = [("1928", "1949"), ("1950", "1969"), ("1970", "1989"),
        ("1990", "2009"), ("2010", "2026")]


def load() -> pd.DataFrame:
    rows = []
    for ln in open(os.path.join(ROOT, "research_data", "ff_daily.csv"),
                   encoding="latin-1"):
        p = [x.strip() for x in ln.split(",")]
        if len(p) >= 5 and len(p[0]) == 8 and p[0].isdigit():
            rows.append((pd.Timestamp(p[0]), float(p[1]) / 100, float(p[4]) / 100))
    df = pd.DataFrame(rows, columns=["date", "mkt_rf", "rf"]).set_index("date")
    df["mkt"] = df.mkt_rf + df.rf
    return df


def years(idx: pd.DatetimeIndex) -> float:
    """Calendar years spanned. Not rows / 252: before 1952 the NYSE traded on
    Saturdays and French's daily file has ~290 rows a year (fixed 2026-11-12;
    until then every pre-1952 CAGR here was understated)."""
    return (idx[-1] - idx[0]).days / 365.25


def strategies(df: pd.DataFrame, lag: int = 1) -> pd.DataFrame:
    idx = (1 + df.mkt).cumprod()
    # Annual costs accrue by calendar time elapsed since the previous row, so a
    # Saturday session does not pay a full trading day's fee.
    dt = df.index.to_series().diff().dt.days.fillna(1) / 365.25
    on = (idx > idx.rolling(SMA).mean()).astype(float).shift(lag)  # lag 1: trade at close t; lag 2: close t+1
    out = pd.DataFrame(index=df.index)
    out["market"] = df.mkt
    for L in (1, 2):
        lev = L * df.mkt - (L - 1) * (df.rf + SPREAD * dt) - ER[L] * dt
        out[f"bh{L}x"] = lev
        switch = on.diff().abs().fillna(0) * SWITCH_BPS / 1e4
        out[f"lrs{L}x"] = on * lev + (1 - on) * df.rf - switch
    out["on"] = on
    out["rf"] = df.rf
    return out.iloc[SMA + lag:]


def perf(r: pd.Series, rf: pd.Series, mkt: pd.Series) -> dict:
    yrs = years(r.index)
    ppy = len(r) / yrs                                        # rows per year
    eq = (1 + r).cumprod()
    m = lambda s: (1 + s).resample("ME").prod() - 1          # noqa: E731
    ab = alpha_beta(m(r), m(mkt), m(rf))
    ex = r - rf
    return {"cagr": eq.iloc[-1] ** (1 / yrs) - 1,
            "vol": r.std() * np.sqrt(ppy),
            "sharpe": ex.mean() / ex.std() * np.sqrt(ppy),
            "maxdd": (eq / eq.cummax() - 1).min(),
            "alpha": ab["alpha_ann"], "alpha_t": ab["alpha_t"], "beta": ab["beta"]}


def table(s: pd.DataFrame, lo: str, hi: str, title: str) -> dict:
    w = s.loc[lo:hi]
    print(f"\n{title}  ({w.index[0].date()} .. {w.index[-1].date()}, "
          f"{years(w.index):.1f} yr, in market {w.on.mean():.0%}, "
          f"{w.on.diff().abs().sum() / years(w.index):.1f} switches/yr)")
    print(f"  {'':8}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}"
          f"{'alpha':>8}{'t':>7}{'beta':>7}")
    res = {}
    for c in ("market", "bh1x", "lrs1x", "bh2x", "lrs2x"):
        p = perf(w[c], w.rf, w.market)
        res[c] = p
        print(f"  {c:8}{p['cagr']:>8.2%}{p['vol']:>8.2%}{p['sharpe']:>8.2f}"
              f"{p['maxdd']:>9.1%}{p['alpha']:>8.2%}{p['alpha_t']:>7.2f}{p['beta']:>7.2f}")
    return res


def rolling_trail(s: pd.DataFrame, col: str, years: int) -> float:
    """Share of rolling `years` windows (monthly steps) where `col` trails the market."""
    m = (1 + s[[col, "market"]]).resample("ME").prod()
    k = years * 12
    g = m.rolling(k).apply(np.prod, raw=True).dropna()
    return float((g[col] < g["market"]).mean())


def main() -> None:
    lag = int(sys.argv[sys.argv.index("--lag") + 1]) if "--lag" in sys.argv else 1
    print(f"execution lag {lag} (1 = pre-registered; 2 = repo convention, next close)")
    s = strategies(load(), lag)
    full = table(s, "1927", "2026", "FULL")
    table(s, "1928", "2015", "IN-SAMPLE: the paper's window")
    oos = table(s, "2016-07", "2026", "OUT-OF-SAMPLE: after publication")

    print("\nERAS — CAGR vs market")
    beats = 0
    for lo, hi in ERAS:
        w = s.loc[lo:hi]
        c = {k: (1 + w[k]).prod() ** (1 / years(w.index)) - 1 for k in ("market", "lrs1x", "lrs2x", "bh2x")}
        beats += c["lrs2x"] > c["market"]
        print(f"  {lo}-{hi}  market {c['market']:7.2%}  lrs1x {c['lrs1x']:7.2%}  "
              f"lrs2x {c['lrs2x']:7.2%}  bh2x {c['bh2x']:7.2%}")

    print("\nHOW OFTEN IT TRAILS THE MARKET (rolling windows, monthly steps)")
    for col in ("lrs1x", "lrs2x", "bh2x"):
        print(f"  {col:6} 5yr {rolling_trail(s, col, 5):5.0%}   "
              f"10yr {rolling_trail(s, col, 10):5.0%}   20yr {rolling_trail(s, col, 20):5.0%}")

    k1 = oos["lrs2x"]["cagr"] > oos["market"]["cagr"]
    k2 = full["lrs2x"]["maxdd"] >= full["market"]["maxdd"]
    k3 = beats >= 4
    print("\nKILL CRITERION (pre-registered, family index-leverage)")
    print(f"  OOS CAGR lrs2x > market        {'PASS' if k1 else 'FAIL'}")
    print(f"  full maxDD lrs2x <= market's   {'PASS' if k2 else 'FAIL'}")
    print(f"  beats market in >=4/5 eras     {'PASS' if k3 else 'FAIL'} ({beats}/5)")
    print(f"  VERDICT: {'ACCEPTED' if k1 and k2 and k3 else 'REJECTED'}")


if __name__ == "__main__":
    main()
