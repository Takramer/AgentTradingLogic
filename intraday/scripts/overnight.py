"""overnight.py — T18: hold SPY only overnight (close -> next open) vs only by day.

Pre-registered in intraday/data/trials.jsonl before running. Development
window only (2016-01-04 .. 2024-12-31); the holdout stays unspent. A standalone
sim because the position crosses sessions (engine.py is intraday-only).

Costs are SPY_RH's: 1 cent slippage per side, SEC 20.60/M on sales, $0
commission, $10,000 every night. The bars are split-adjusted only, so the
dividend is added back on each ex-date to whoever held through the prior close
(overnight and buy-and-hold); the day-only book never holds it.

    .venv/bin/python intraday/scripts/overnight.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from intraday import validate as V  # noqa: E402
from rhengine import trials as T  # noqa: E402

LO, HI, CAP, SLIP, SEC = "2016-01-04", "2024-12-31", 10_000.0, 0.01, 20.60e-6
BARS = os.path.join(ROOT, "intraday", "data", "SPY_rth.pkl")


def dividends() -> pd.Series:
    """SPY cash dividends by ex-date (Yahoo chart events)."""
    r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/SPY"
                     "?period1=1420070400&period2=1767225600&interval=1d&events=div",
                     headers={"User-Agent": "Mozilla/5.0 (research script)"}, timeout=30)
    r.raise_for_status()
    ev = r.json()["chart"]["result"][0]["events"]["dividends"].values()
    s = pd.Series({pd.Timestamp(e["date"], unit="s", tz="UTC").tz_convert("America/New_York")
                   .normalize().tz_localize(None): e["amount"] for e in ev})
    return s.sort_index()


def sessions() -> pd.DataFrame:
    b = pd.read_pickle(BARS)
    d = b.index.tz_localize(None).normalize()
    s = b.groupby(d).agg(open=("open", "first"), close=("close", "last"))
    return s.loc[LO:HI]


def books(s: pd.DataFrame, div: pd.Series, costs: bool = True) -> pd.DataFrame:
    slip, sec = (SLIP, SEC) if costs else (0.0, 0.0)
    dv = div.reindex(s.index).fillna(0.0)
    prev = s.close.shift(1)
    sh_n = CAP / (prev + slip)
    night = sh_n * ((s.open - slip) - (prev + slip) + dv) - sec * sh_n * (s.open - slip)
    sh_d = CAP / (s.open + slip)
    day = sh_d * ((s.close - slip) - (s.open + slip)) - sec * sh_d * (s.close - slip)
    hold = CAP * (s.close + dv - prev) / prev
    return pd.DataFrame({"overnight": night, "day": day, "hold": hold}).iloc[1:]


def maxdd_usd(x: pd.Series) -> float:
    c = x.cumsum()
    return float((c - c.cummax()).min())


def main() -> None:
    label = "T18 overnight-only SPY: buy the last RTH bar close, sell the next session's first RTH open, SPY_RH costs"
    V.require_logged(label)
    n = max(T.count(V.FAMILY, path=V.LEDGER), 1)
    need = T.sidak_t(n)
    s, div = sessions(), dividends()
    net, gross = books(s, div), books(s, div, costs=False)
    print(f"{len(net)} nights {net.index[0].date()} .. {net.index[-1].date()}; "
          f"{(div.reindex(s.index).fillna(0) > 0).sum()} ex-dividend dates; {n} trials logged")
    for k in ("overnight", "day", "hold"):
        st = V.day_stats(net[k], trials=n)
        lo, hi = st["sharpe_ci"]
        print(f"  {k:<9} net ${st['net_usd']:>8,.0f} (gross ${gross[k].sum():>8,.0f})  "
              f"t {st['t_nw']:+.2f}  Sharpe {st['sharpe']:.2f} [{lo:.2f}, {hi:.2f}]  "
              f"DSR {st['deflated_sharpe']:.2f}  maxDD ${maxdd_usd(net[k]):,.0f}  "
              f"2016-20 ${net[k].loc[:'2020'].sum():,.0f}  2021-24 ${net[k].loc['2021':].sum():,.0f}")
    print(f"  corr(overnight, day) {net.overnight.corr(net.day):+.2f}; "
          f"overnight sd ${net.overnight.std():.0f}/night vs day ${net.day.std():.0f}")
    st, hd = V.day_stats(net.overnight, n), V.day_stats(net.hold, n)
    req = need
    ok = [st["t_nw"] >= req, st["sharpe"] > hd["sharpe"],
          net.overnight.loc[:"2020"].sum() > 0 and net.overnight.loc["2021":].sum() > 0]
    print(f"family-wise p {T.family_wise_p(st['t_nw'], n):.3f}")
    print(f"KILL: t >= required ({req:.2f}) {ok[0]}; Sharpe > hold {ok[1]}; both halves > 0 {ok[2]}")


if __name__ == "__main__":
    main()
