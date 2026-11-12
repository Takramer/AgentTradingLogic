"""lrs_1515_check.py — LRS-2x decided on the live 15:15 ET price, traded at 15:15.

The Robinhood MCP gives Stage 2 a real-time SPY quote at its 15:15 run. That
lets the agent act on today's signal today instead of at tomorrow's close
(lrs_check.py --lag 2). Pre-registered in family index-leverage before the
first run. Window 2016-07-01..2026-09-30 (French T-bill file ends there).

Data: intraday/data/SPY_1m.csv (Alpaca one-minute bars, gitignored, from
intraday/scripts/fetch_alpaca_bars.py) for the 15:15 price; Yahoo adjusted
daily closes for everything else.

    .venv/bin/python scripts/lrs_1515_check.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]
import lrs_check as L                                   # noqa: E402
from rhengine.data_local import fetch_yahoo              # noqa: E402

LO, HI = "2016-07-01", "2026-09-30"


def daily() -> pd.DataFrame:
    def ser(adjust: bool) -> pd.Series:
        b = fetch_yahoo("SPY", lookback_days=4400, adjust=adjust)
        return pd.Series([x["close"] for x in b], index=pd.to_datetime(
            [x["time"] for x in b], unit="ms").tz_localize("UTC")
            .tz_convert("America/New_York").tz_localize(None).normalize())
    d = pd.DataFrame({"adj": ser(True), "raw": ser(False)}).dropna()
    m = pd.read_csv(os.path.join(ROOT, "intraday", "data", "SPY_1m.csv"),
                    usecols=["ts", "close"])
    t = pd.to_datetime(m.ts, utc=True).dt.tz_convert("America/New_York")
    m = m.assign(day=t.dt.tz_localize(None).dt.normalize(), hm=t.dt.hour * 60 + t.dt.minute)
    d["p1515"] = m[(m.hm >= 570) & (m.hm <= 915)].groupby("day").close.last()
    d["c1600"] = m[(m.hm >= 570) & (m.hm < 960)].groupby("day").close.last()
    rf = L.load().rf
    d["rf"] = rf.reindex(d.index).ffill()
    return d.dropna(subset=["adj", "raw"])


def run(d: pd.DataFrame) -> pd.DataFrame:
    r = d.adj.pct_change()
    factor = (d.adj / d.raw).shift(1)                    # yesterday's adj/raw
    live = d.p1515 * factor                              # today's 15:15, adjusted
    sma_live = (d.adj.shift(1).rolling(199).sum() + live) / 200
    on_1515 = (live > sma_live).astype(float)            # decided and traded 15:15 today
    on_close = (d.adj > d.adj.rolling(200).mean()).astype(float)
    b = (d.adj / live) - 1                               # 15:15 -> close
    a = (1 + r) / (1 + b) - 1                            # prior close -> 15:15
    fin = (d.rf + L.SPREAD / 252) + L.ER[2] / 252
    lev_a, lev_full = 2 * a, 2 * r                       # SSO resets at the close
    lev_b = (1 + lev_full) / (1 + lev_a) - 1
    rf_a, rf_b = d.rf * 0.9, d.rf * 0.1
    prev = on_1515.shift(1)
    leg1 = prev * lev_a + (1 - prev) * rf_a
    leg2 = on_1515 * lev_b + (1 - on_1515) * rf_b
    sw = (on_1515 != prev).astype(float) * L.SWITCH_BPS / 1e4
    out = pd.DataFrame({"market": r, "rf": d.rf, "on": on_1515})
    out["lrs_1515"] = (1 + leg1) * (1 + leg2) - 1 - prev * fin - sw
    lev = 2 * r - fin
    for lag in (1, 2):
        on = on_close.shift(lag)
        out[f"lrs_lag{lag}"] = on * lev + (1 - on) * d.rf - on.diff().abs() * L.SWITCH_BPS / 1e4
    out["bh2x"] = lev
    out["disagree"] = (on_1515 != on_close).astype(float)
    return out.loc[LO:HI].dropna(subset=["lrs_1515", "lrs_lag2", "market"])


def main() -> None:
    d = daily()
    gap = ((d.c1600 / d.raw) - 1).abs().loc[LO:HI].dropna()
    print(f"one-minute 16:00 close vs Yahoo close: median gap {gap.median():.4%}, "
          f"max {gap.max():.3%}, days missing 15:15 bar: "
          f"{d.p1515.loc[LO:HI].isna().sum()}")
    s = run(d)
    print(f"\n{s.index[0].date()} .. {s.index[-1].date()}  ({len(s) / 252:.1f} yr)")
    print(f"15:15 signal disagrees with the close signal on {s.disagree.mean():.1%} of days")
    print(f"  {'':10}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}")
    res = {}
    for c in ("market", "bh2x", "lrs_lag1", "lrs_lag2", "lrs_1515"):
        p = L.perf(s[c], s.rf, s.market)
        res[c] = p
        print(f"  {c:10}{p['cagr']:>8.2%}{p['vol']:>8.2%}{p['sharpe']:>8.2f}{p['maxdd']:>9.1%}")
    k1 = res["lrs_1515"]["cagr"] >= res["lrs_lag2"]["cagr"]
    k2 = res["lrs_1515"]["maxdd"] >= res["lrs_lag2"]["maxdd"]
    print("\nKILL CRITERION (pre-registered): 15:15 vs next close (lag 2)")
    print(f"  CAGR >= lag 2      {'PASS' if k1 else 'FAIL'}")
    print(f"  maxDD no worse     {'PASS' if k2 else 'FAIL'}")
    print(f"  VERDICT: {'ACCEPTED' if k1 and k2 else 'REJECTED'}")


if __name__ == "__main__":
    main()
