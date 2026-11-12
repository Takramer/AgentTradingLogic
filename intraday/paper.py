"""paper.py — forward paper record for a frozen rule. No orders, no network.

Each completed session from `start` on is backtested by the same engine as the
research and appended once to a JSONL record. Rows are never rewritten: a later
data revision or code change cannot alter what was recorded on the day. Every
row carries the rule's hash, so a changed rule shows up as a new hash rather
than as a silent continuation.

These are modelled fills (next-bar open plus the instrument's assumed slippage),
not broker fills. What paper data adds is time the rule has never seen.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date

import numpy as np
import pandas as pd

from .data import session_dates
from .engine import Result, short_hash
from .strategies import Strategy
from .validate import day_stats


def rule_hash(strategy: Strategy, instrument, notional: float | None) -> str:
    return short_hash({"strategy": strategy.name, "params": strategy.params,
                       "instrument": asdict(instrument), "notional": notional})


def complete_sessions(bars: pd.DataFrame, min_early_close_bars: int = 205) -> set:
    """Sessions whose bars run to the close: the last bar is 15:59, or 12:59 on
    an early-close day (which has ~210 bars). A feed that stalls mid-session
    leaves a session out until a later fetch has it whole."""
    day = pd.Series(session_dates(bars.index), index=bars.index)
    last = bars.index.to_series().groupby(day.to_numpy()).max().dt.strftime("%H:%M")
    n = day.groupby(day.to_numpy()).size()
    ok = (last == "15:59") | ((last == "12:59") & (n >= min_early_close_bars))
    return set(ok[ok].index)


def load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def new_rows(result: Result, recorded: set, start: date, last_complete: date,
             rhash: str, complete: set | None = None) -> list[dict]:
    """One row per session in [start, last_complete] not already recorded and,
    when `complete` is given, whose bars are whole (`complete_sessions`)."""
    rows = []
    tr = result.trades
    by_day = {d: g for d, g in tr.groupby(pd.to_datetime(tr["day"]).dt.date)} if len(tr) else {}
    for day, net in result.daily.items():
        d = day.date()
        if (d < start or d > last_complete or d.isoformat() in recorded
                or (complete is not None and d not in complete)):
            continue
        t = by_day.get(d, tr.iloc[:0])
        rows.append({
            "date": d.isoformat(), "rule": rhash, "net_usd": round(float(net), 4),
            "trades": [{"side": int(np.sign(r.side)), "qty": round(float(r.qty), 6),
                        "entry_ts": str(r.entry_ts), "entry_px": float(r.entry_px),
                        "exit_ts": str(r.exit_ts), "exit_px": float(r.exit_px),
                        "cost": round(float(r.cost), 4), "net": round(float(r.net), 4),
                        "reason": r.reason} for r in t.itertuples()]})
    return rows


def summary(rows: list[dict]) -> dict:
    if not rows:
        return {"sessions": 0}
    daily = pd.Series([r["net_usd"] for r in rows],
                      index=pd.to_datetime([r["date"] for r in rows])).sort_index()
    st = day_stats(daily)
    return {"sessions": len(daily), "first": str(daily.index[0].date()),
            "last": str(daily.index[-1].date()),
            "trades": sum(len(r["trades"]) for r in rows),
            "net_usd": float(daily.sum()), "mean_day": float(daily.mean()),
            "t_nw": st["t_nw"], "rules": sorted({r["rule"] for r in rows})}
