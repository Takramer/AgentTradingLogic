"""insider_check.py — do officers' and directors' open-market purchases predict returns?

Pre-registered in the trial ledger (family insider) before any return was
computed. Rule and kill criterion are in the ledger row; in short: buy at the
close of the session after a Form 4 purchase (>= $25k, officer or director) is
filed, hold 21 sessions, equal weight, 30 bps round trip.

Yahoo only prices companies still listed today, so every survivor looks good.
The control portfolio holds every priced issuer in the event set all the time;
the test is the excess over that control, not over SPY.

    .venv/bin/python scripts/fetch_form345.py      # once
    .venv/bin/python scripts/insider_check.py
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import statsmodels.api as sm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]
import lrs_check as L                                   # noqa: E402
from fetch_form345 import EVENTS, OUT                   # noqa: E402
from rhengine.alpha.stats import alpha_beta             # noqa: E402
from rhengine.data_local import fetch_yahoo             # noqa: E402

MIN_VALUE, HOLD, MIN_PRICE, MIN_DVOL, COST_SIDE = 25_000, 21, 5.0, 1e6, 0.0015
LO, HI = "2006-01-01", "2026-09-30"


def events() -> tuple[pd.DataFrame, dict]:
    ev = pd.read_csv(EVENTS, parse_dates=["FILING_DATE"], dtype={"ISSUERCIK": str})
    ev = ev[(ev.DOCUMENT_TYPE == "4")
            & ev.RELATIONSHIP.fillna("").str.contains("director|officer", case=False)]
    ev = ev.assign(value=ev.TRANS_SHARES * ev.TRANS_PRICEPERSHARE)
    f = ev.groupby("ACCESSION_NUMBER").agg(cik=("ISSUERCIK", "first"),
                                          filed=("FILING_DATE", "first"), value=("value", "sum"))
    f = f[(f.value >= MIN_VALUE) & f.filed.between(LO, HI)]
    f["cik"] = f.cik.astype(int)
    f = f.drop_duplicates(["cik", "filed"])
    tick = {}
    for row in json.load(open(os.path.join(OUT, "company_tickers.json"))).values():
        tick.setdefault(int(row["cik_str"]), row["ticker"].replace(".", "-"))
    f["ticker"] = f.cik.map(tick)
    return f.reset_index(drop=True), tick


def prices(tickers) -> tuple[pd.DataFrame, pd.DataFrame]:
    def one(t):
        try:
            b = fetch_yahoo(t, lookback_days=7800, adjust=True)
        except Exception:                               # noqa: BLE001
            return t, None
        idx = pd.to_datetime([x["time"] for x in b], unit="ms").tz_localize("UTC") \
            .tz_convert("America/New_York").tz_localize(None).normalize()
        return t, pd.DataFrame({"c": [x["close"] for x in b],
                                "v": [x["volume"] for x in b]}, index=idx)
    with ThreadPoolExecutor(4) as ex:
        got = {t: d for t, d in ex.map(one, sorted(tickers)) if d is not None and len(d)}
    close = pd.DataFrame({t: d.c[~d.index.duplicated()] for t, d in got.items()})
    dvol = pd.DataFrame({t: (d.c * d.v)[~d.index.duplicated()] for t, d in got.items()})
    return close.sort_index(), dvol.sort_index()


def hac_t(x: pd.Series) -> float:
    fit = sm.OLS(x.values, np.ones(len(x))).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    return float(fit.tvalues[0])


def main() -> None:
    ev, _ = events()
    print(f"qualifying events {len(ev)}; with a current ticker {ev.ticker.notna().sum()}")
    close, dvol = prices(set(ev.ticker.dropna()))
    close = close.loc["2005-06-01":]
    dvol = dvol.reindex(close.index)
    ret = close.pct_change(fill_method=None)
    ok = (close >= MIN_PRICE) & (dvol.rolling(20, min_periods=10).median() >= MIN_DVOL)

    dates, cols = close.index, {t: j for j, t in enumerate(close.columns)}
    active = np.zeros(close.shape, dtype=bool)
    priced = tradable = 0
    for e in ev.itertuples():
        j = cols.get(e.ticker)
        i = dates.searchsorted(e.filed, side="right")   # first session after filing
        if j is None or i >= len(dates) or np.isnan(close.iat[i, j]):
            continue
        priced += 1
        if not ok.iat[i, j]:
            continue
        tradable += 1
        active[i + 1:i + 1 + HOLD, j] = True
    act = pd.DataFrame(active, index=dates, columns=close.columns)
    n = act.sum(axis=1)
    held = act & ret.notna()
    gross = (ret.where(held)).mean(axis=1)
    entering = (act & ~act.shift(1, fill_value=False)).sum(axis=1)
    leaving = (~act & act.shift(1, fill_value=False)).sum(axis=1)
    cost = COST_SIDE * (entering / n.replace(0, np.nan)).fillna(0) \
        + COST_SIDE * (leaving / n.shift(1).replace(0, np.nan)).fillna(0)
    rf = L.load().rf.reindex(dates).ffill()
    port = gross.fillna(rf) - cost
    control = ret.where(ok.shift(1, fill_value=False)).mean(axis=1)
    spy_r = prices(["SPY"])[0]["SPY"].reindex(dates).pct_change(fill_method=None)

    gross_ex = (gross.fillna(rf) - control).loc["2006-02-01":HI].dropna()
    gm = (1 + gross.fillna(rf)).resample("ME").prod() - (1 + control).resample("ME").prod()
    print(f"before costs: excess over control {gross_ex.mean() * 252:+.2%}/yr, "
          f"HAC t {hac_t(gm.loc['2006-02':'2026-09'].dropna()):.2f}; costs "
          f"{cost.loc['2006-02-01':HI].mean() * 252:.2%}/yr")
    s = pd.DataFrame({"insider": port, "control": control, "spy": spy_r, "rf": rf,
                      "n": n}).loc["2006-02-01":HI].dropna(subset=["insider", "control", "spy", "rf"])
    print(f"priced {priced} ({priced / len(ev):.0%} of qualifying), tradable {tradable}; "
          f"avg open positions {s.n.mean():.0f}, days with none {(s.n == 0).mean():.0%}")

    m = (1 + s[["insider", "control", "spy", "rf"]]).resample("ME").prod() - 1
    yrs = L.years(s.index)
    cagr = {k: (1 + s[k]).prod() ** (1 / yrs) - 1 for k in ("insider", "control", "spy")}
    dd = {k: ((1 + s[k]).cumprod() / (1 + s[k]).cumprod().cummax() - 1).min()
          for k in ("insider", "control", "spy")}
    ex = m.insider - m.control
    t_ex = hac_t(ex)
    ab = alpha_beta(m.insider, m.spy, m.rf)
    print(f"\n{s.index[0].date()} .. {s.index[-1].date()} ({yrs:.1f} yr)")
    for k in ("insider", "control", "spy"):
        print(f"  {k:8} CAGR {cagr[k]:7.2%}  maxDD {dd[k]:7.1%}")
    print(f"  excess over control {ex.mean() * 12:+.2%}/yr, HAC t {t_ex:.2f}")
    print(f"  alpha vs SPY {ab['alpha_ann']:+.2%}/yr, t {ab['alpha_t']:.2f}, beta {ab['beta']:.2f}")
    halves = [ex.loc["2006":"2015"].mean(), ex.loc["2016":].mean()]
    print(f"  excess 2006-2015 {halves[0] * 12:+.2%}/yr, 2016-2026 {halves[1] * 12:+.2%}/yr")

    gate = priced / len(ev) >= 0.70
    k = [t_ex >= 1.96, cagr["insider"] > cagr["spy"], all(h > 0 for h in halves)]
    print("\nKILL CRITERION (pre-registered, family insider)")
    print(f"  excess over control t >= 1.96 {'PASS' if k[0] else 'FAIL'} ({t_ex:.2f}; repo bar 3.46)")
    print(f"  CAGR > SPY                   {'PASS' if k[1] else 'FAIL'}")
    print(f"  excess > 0 in both halves    {'PASS' if k[2] else 'FAIL'}")
    print(f"  >= 70% of events priced      {'PASS' if gate else 'FAIL'} ({priced / len(ev):.0%})")
    print(f"  VERDICT: {'INCONCLUSIVE (survivorship)' if not gate else 'ACCEPTED' if all(k) else 'REJECTED'}")


if __name__ == "__main__":
    main()
