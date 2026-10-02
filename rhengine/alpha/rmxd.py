"""rmxd.py — RMX-D, the frozen strategy.

Specification (docs/RMX-PREREGISTRATION.md §9, frozen 2026-07-24):

    monthly, on the last trading session, executed at the NEXT session's close:

      if  index month-end close > 10-month MA of its month-end closes:
            100% equity
      else if the safe asset's trailing 12-month total return > 0:
            100% safe asset
      else:
            100% cash at the risk-free rate

Two instruments, two windows, no leverage, no volatility target, no stock
selection. Every one of those absences is a rejected hypothesis, not an
oversight - see Amendments B and D of the pre-registration for the numbers that
killed each one.

Why this ended up being the whole strategy: an IC screen of eight price-only
cross-sectional signals over 131 monthly cross-sections of the S&P 500 could
not produce a single |t| above 1.05, so there is no evidence here for picking
stocks. What survived measurement was the decision of how much to be invested -
and that decision can be tested on data with no survivorship bias, because an
index level never delists.

    py -m rhengine.alpha.rmxd --hi 2015-12-31          # in-sample
    py -m rhengine.alpha.rmxd --hi 2026-07-24 --oos    # the one OOS look
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from .book import buy_and_hold, run_book
from .stats import Perf, alpha_beta, compare, monthly, perf, print_report
from .timing import month_end_index

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BARS = os.path.join(ROOT, "research_data", "yahoo_long")

TREND_MONTHS = 10        # Faber 2007
ABSMOM_MONTHS = 12       # Antonacci 2014
DEFAULT_COST_BPS = 10.0


def series(sym: str) -> pd.Series:
    f = os.path.join(BARS, f"{sym.replace('^', '_')}.csv.gz")
    df = pd.read_csv(f, parse_dates=["date"]).drop_duplicates("date")
    return df.set_index("date")["adjclose"].sort_index()


def risk_free(cal: pd.DatetimeIndex) -> pd.Series:
    from .panel import Panel
    rf, _ = Panel._french(cal)
    return rf.ffill().bfill().fillna(0.0)


def schedule(equity_px: pd.Series, safe_px: pd.Series | None,
             trend_months: int = TREND_MONTHS,
             absmom_months: int = ABSMOM_MONTHS) -> pd.DataFrame:
    """The monthly decision table. Uses only closes at or before each date."""
    me = month_end_index(equity_px.index)
    eq_m = equity_px.reindex(me).ffill()
    risk_on = eq_m > eq_m.rolling(trend_months).mean()

    if safe_px is not None:
        safe_m = safe_px.reindex(me).ffill()
        safe_ok = (safe_m / safe_m.shift(absmom_months) - 1.0) > 0.0
    else:
        safe_ok = pd.Series(False, index=me)

    out = pd.DataFrame({"risk_on": risk_on, "safe_ok": safe_ok.reindex(me).fillna(False)})
    out["equity"] = np.where(out["risk_on"].fillna(False), 1.0, 0.0)
    out["safe"] = np.where(~out["risk_on"].fillna(False) & out["safe_ok"], 1.0, 0.0)
    out["cash"] = 1.0 - out["equity"] - out["safe"]
    warm = max(trend_months, absmom_months) + 1
    return out.iloc[warm:]


def build(equity_px: pd.Series, safe_px: pd.Series | None, lo: str, hi: str,
          cost_bps: float = DEFAULT_COST_BPS,
          trend_months: int = TREND_MONTHS, absmom_months: int = ABSMOM_MONTHS,
          book_lo: str | None = None):
    """Run RMX-D and its benchmark over [lo, hi]. Returns (book, bh, rf, sched).

    `book_lo` starts the *book* later than the data: the signal needs 12 months
    of warmup, so an out-of-sample window beginning on a given date must be fed
    history from before it without letting that history into the result.
    """
    cols = {"EQ": equity_px}
    if safe_px is not None:
        cols["SAFE"] = safe_px
    adj = pd.DataFrame(cols).sort_index()
    adj = adj.loc[(adj.index >= pd.Timestamp(lo)) & (adj.index <= pd.Timestamp(hi))]
    adj = adj.dropna(subset=["EQ"])
    if len(adj) < 400:
        raise ValueError(f"window too short: {len(adj)} sessions")

    sch = schedule(adj["EQ"], adj["SAFE"] if safe_px is not None else None,
                   trend_months, absmom_months)
    sch = sch[sch.index.isin(adj.index)]
    if book_lo is not None:
        sch = sch[sch.index >= pd.Timestamp(book_lo)]
    if sch.empty:
        raise ValueError("no rebalance dates inside the window")

    targets = {}
    for d, row in sch.iterrows():
        w = {}
        if row["equity"] > 0:
            w["EQ"] = 1.0
        elif row["safe"] > 0 and safe_px is not None:
            w["SAFE"] = 1.0
        targets[d] = pd.Series(w, dtype=float)

    rf = risk_free(adj.index)
    start = sch.index[0]
    bk = run_book(adj, targets, rf, cost_bps, start=start, end=adj.index[-1])
    bh = buy_and_hold(adj["EQ"], start, adj.index[-1]).reindex(
        bk.daily_return.index).fillna(0.0)
    return bk, bh, rf, sch


def report(equity_sym: str = "VFINX", safe_sym: str | None = "VFITX",
           lo: str = "1970-01-01", hi: str = "2015-12-31",
           cost_bps: float = DEFAULT_COST_BPS, trials: int = 50,
           label: str = "RMX-D") -> dict:
    eq = series(equity_sym)
    safe = series(safe_sym) if safe_sym else None
    bk, bh, rf, sch = build(eq, safe, lo, hi, cost_bps)
    rep = compare(bk.daily_return, {f"{equity_sym} buy & hold": bh}, rf=rf,
                  trials=trials, label=label)
    rep.update({
        "cost_bps": cost_bps, "turnover_ann": bk.annual_turnover,
        "cost_drag_total_pct": bk.total_cost_pct,
        "n_rebalances": len(sch),
        "pct_in_equity": float((sch["equity"] > 0).mean() * 100),
        "pct_in_safe": float((sch["safe"] > 0).mean() * 100),
        "pct_in_cash": float((sch["cash"] > 0.999).mean() * 100),
        "switches_per_year": float((sch["equity"].diff().abs() > 0).sum()
                                   / (len(sch) / 12.0)),
    })
    return rep | {"_book": bk, "_bh": bh, "_rf": rf, "_sched": sch}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", default="VFINX")
    ap.add_argument("--safe", default="VFITX")
    ap.add_argument("--lo", default="1970-01-01")
    ap.add_argument("--hi", default="2015-12-31")
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    ap.add_argument("--trials", type=int, default=50)
    args = ap.parse_args()

    rep = report(args.equity, args.safe or None, args.lo, args.hi,
                 args.cost_bps, args.trials)
    print(f"RMX-D  {args.equity} / {args.safe}  {args.lo} .. {args.hi}  "
          f"@ {args.cost_bps:.0f} bps")
    print(f"{rep['n_rebalances']} monthly decisions | "
          f"{rep['pct_in_equity']:.0f}% equity / {rep['pct_in_safe']:.0f}% bonds / "
          f"{rep['pct_in_cash']:.0f}% cash | "
          f"{rep['switches_per_year']:.1f} switches/yr | "
          f"turnover {rep['turnover_ann'] * 100:.0f}%/yr | "
          f"cost drag {rep['cost_drag_total_pct']:.1f}% total\n")
    print_report({k: v for k, v in rep.items() if not k.startswith("_")})


if __name__ == "__main__":
    main()
