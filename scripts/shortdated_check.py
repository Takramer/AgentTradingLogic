"""shortdated_check.py — does selling WEEKLY S&P options beat the same beta with no options?

Pre-registered in the trial ledger (family options_shortdated) before any
return was computed. Same test that judged the monthly BXM/PUT on 2026-08-01
(rhengine/alpha/buywrite.py): alpha against a static beta*SP500TR + (1-beta)*cash
blend, because a premium seller runs at beta ~0.5 and "loses to the index" is
the wrong question.

    WPUT  short a 1-week ATM SPX put, rolled every Friday, T-bill collateral
    BXMW  long the S&P, short 1-week ATM calls (multi-week ladder)

    .venv/bin/python scripts/shortdated_check.py   # needs the CSVs below

Data: Cboe's own CSVs (cdn.cboe.com .../daily_prices/{WPUT,BXMW}_History.csv;
Yahoo serves both as a quote with no history), Yahoo ^PUT ^SP500TR, French
1-month T-bill. Saved under research_data/external/cboe/.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rhengine.alpha.buywrite import daily_returns, load_rf_daily, load_series  # noqa: E402
from rhengine.alpha.stats import compare  # noqa: E402


def blend(r: pd.Series, tr: pd.Series, rf: pd.Series) -> tuple[pd.Series, float]:
    beta = float(np.polyfit(tr.to_numpy(float), r.to_numpy(float), 1)[0])
    return beta * tr + (1.0 - beta) * rf, beta


def maxdd(r: pd.Series) -> float:
    w = (1 + r).cumprod()
    return float((w / w.cummax() - 1).min())


def judge(name: str, rets: dict) -> bool:
    r = rets[name]
    days = r.index.intersection(rets["SP500TR"].index)
    r, tr = r.reindex(days), rets["SP500TR"].reindex(days)
    rf = load_rf_daily(days)
    bl, beta = blend(r, tr, rf)
    key = f"{beta:.2f}xTR+cash"
    rep = compare(r, {"SP500TR": tr, key: bl}, rf=rf, trials=2, label=name)
    p, v = rep["perf"], rep["vs"][key]
    print(f"\n{name}  {days[0].date()} .. {days[-1].date()}  beta {beta:.2f}")
    for k in (name, "SP500TR", key):
        x = p[k]
        print(f"  {k:<14} CAGR {x['cagr_pct']:6.2f}%  Sharpe {x['sharpe']:5.2f}  "
              f"maxDD {maxdd({name: r, 'SP500TR': tr, key: bl}[k]):7.1%}")
    print(f"  alpha vs blend {v['alpha_ann'] * 100:+.2f}%/yr, t {v['alpha_t']:+.2f}")
    halves = []
    for lo, hi in ((None, "2015-12-31"), ("2016-01-01", None)):
        sl = slice(lo, hi)
        if len(r.loc[sl]) < 500:
            halves.append(None)
            continue
        h = compare(r.loc[sl], {key: bl.loc[sl]}, rf=rf.loc[sl], trials=2, label=name)
        halves.append(h["vs"][key]["alpha_ann"])
    print("  alpha vs blend by half: " + ", ".join(
        "n/a" if a is None else f"{a * 100:+.2f}%/yr" for a in halves))
    ok = [v["alpha_t"] >= 1.96, p[name]["sharpe"] > p["SP500TR"]["sharpe"],
          all(a is not None and a > 0 for a in halves)]
    print(f"  kill: t>=1.96 {'PASS' if ok[0] else 'FAIL'}; Sharpe>TR "
          f"{'PASS' if ok[1] else 'FAIL'}; both halves>0 {'PASS' if ok[2] else 'FAIL'}"
          f"  -> {'CANDIDATE' if all(ok) else 'REJECTED'}")
    return all(ok)


def main() -> None:
    rets = {k: daily_returns(load_series(k)) for k in ("WPUT", "BXMW", "PUT", "SP500TR")}
    for k in ("WPUT", "BXMW"):
        judge(k, rets)
    days = rets["WPUT"].index.intersection(rets["PUT"].index)
    w, m = rets["WPUT"].reindex(days), rets["PUT"].reindex(days)
    yrs = len(days) / 252
    print(f"\nWPUT vs PUT, same {len(days)} days: CAGR {(1 + w).prod() ** (1 / yrs) - 1:.2%} vs "
          f"{(1 + m).prod() ** (1 / yrs) - 1:.2%}; maxDD {maxdd(w):.1%} vs {maxdd(m):.1%}")
    print("\nBy calendar year (%): WPUT, PUT, SP500TR")
    tab = pd.DataFrame({k: (1 + rets[k]).groupby(rets[k].index.year).prod() - 1
                        for k in ("WPUT", "PUT", "SP500TR")}).loc[2006:] * 100
    print(tab.round(1).to_string())


if __name__ == "__main__":
    main()
