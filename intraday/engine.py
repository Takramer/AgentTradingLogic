"""engine.py — the intraday backtest loop. Fixed rules, no options to loosen them.

1. A strategy returns, per bar, a `target` position in {-1, 0, +1} (and
   optionally absolute `stop` / `take` prices) using data up to that bar's
   CLOSE. The engine acts on it at the **next bar's OPEN**. Never the same bar.
2. Stops and targets are checked against later bars' high/low. If one bar
   touches both, **the stop is assumed to fill first** — the bar does not say
   which came first, so the pessimistic answer is the only safe one. A bar that
   opens through the level fills at the open, not at the level.
3. After a stop or target exit the engine stays flat until the strategy's
   target changes. Otherwise a rule would re-enter on the very next bar.
4. **Flat every session.** Signals on a session's last bar are discarded and an
   open position closes at that bar's close. No position, no pending order and
   no stop survives overnight.
5. Every trade pays `instrument.trade_cost(qty, entry, exit)`: per-unit fees
   and slippage both sides, plus any notional-based fee (SEC fee on SPY).
6. Daily PnL covers **every** session, with 0 on days the rule did not trade.
   Dropping idle days inflates any t-stat computed from them.

`causality_check` re-runs a strategy on truncated history and raises if its
past signals change — the cheap, reliable catch for `shift(-1)`, centred
windows, whole-sample scaling, and the classic intraday leak of using the
session's full high/low before the session is over.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import time as dtime

import numpy as np
import pandas as pd

from .data import session_dates
from .instruments import Instrument


class LookAheadError(AssertionError):
    """A strategy's signal for bar t changed when bars after t were removed."""


class Strategy:
    """Subclass, set `name`, and implement `signals(bars) -> DataFrame` with a
    `target` column (and optional `stop`, `take`). Constructor kwargs are the
    parameters a sweep varies."""

    name = "strategy"

    def __init__(self, **params):
        self.params = params

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover
        raise NotImplementedError

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.params.items())
        return f"{type(self).__name__}({args})"


@dataclass
class Result:
    strategy: str
    params: dict
    instrument: Instrument
    qty: float
    capital: float
    trades: pd.DataFrame
    #: Net USD per session, every session in the data, 0 when flat all day.
    daily: pd.Series
    trial: str | None = None
    config_hash: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        """Daily return on a FIXED account size — no compounding, so one lucky
        early month cannot scale every later trade."""
        return self.daily / self.capital

    def summary(self) -> dict:
        t = self.trades
        n = len(t)
        return {
            "strategy": repr_params(self.strategy, self.params),
            "sessions": int(len(self.daily)),
            "active_sessions": int((self.daily != 0).sum()),
            "trades": n,
            "gross_usd": float(t["gross"].sum()) if n else 0.0,
            "costs_usd": float(t["cost"].sum()) if n else 0.0,
            "net_usd": float(t["net"].sum()) if n else 0.0,
            "avg_trade_usd": float(t["net"].mean()) if n else float("nan"),
            "win_rate": float((t["net"] > 0).mean()) if n else float("nan"),
            "trial": self.trial,
        }


def repr_params(name: str, params: dict) -> str:
    return name + "(" + ", ".join(f"{k}={v!r}" for k, v in params.items()) + ")"


def short_hash(blob) -> str:
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str)
                          .encode()).hexdigest()[:12]


def config_hash(strategy: Strategy, instrument: Instrument, bars: pd.DataFrame,
                **extra) -> str:
    return short_hash({"strategy": strategy.name, "params": strategy.params,
                       "instrument": asdict(instrument),
                       "span": [str(bars.index[0]), str(bars.index[-1]), len(bars)],
                       **extra})


def _session_bounds(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list]:
    days = session_dates(bars.index)
    codes, uniq = pd.factorize(days, sort=False)
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[starts[1:] - 1, len(bars) - 1]
    return starts, ends, list(uniq)


def _signals(strategy: Strategy, bars: pd.DataFrame) -> pd.DataFrame:
    sig = strategy.signals(bars)
    if not isinstance(sig, pd.DataFrame) or "target" not in sig.columns:
        raise TypeError("signals() must return a DataFrame with a `target` column")
    if not sig.index.equals(bars.index):
        raise ValueError("signals() must return one row per input bar, same index")
    tgt = sig["target"].fillna(0.0)
    if not tgt.isin([-1.0, 0.0, 1.0]).all():
        raise ValueError("target must be -1, 0 or +1")
    return sig


def backtest(strategy: Strategy, bars: pd.DataFrame, instrument: Instrument,
             qty: float = 1.0, capital: float = 10_000.0, trial: str | None = None,
             flat_by: str | None = None, notional: float | None = None,
             max_entries: int | None = None) -> Result:
    """Run one strategy over single-instrument bars (use `continuous()` first
    for futures). `flat_by` ("15:55") forces the exit at that bar's open and
    blocks entries after it; default is the session's last close.

    Sizing: a fixed `qty` (contracts), or `notional` USD per trade, in which
    case each trade buys notional / entry price units (fractional shares) and
    `qty` is ignored. `max_entries` caps entries per session; on an instrument
    with `settled_cash_only` (a cash account) it defaults to
    capital // notional, because buying again with same-day sale proceeds and
    selling before they settle is a good-faith violation."""
    if bars.index.has_duplicates:
        raise ValueError("duplicate timestamps — build a continuous series first")
    if notional is not None and notional > capital:
        raise ValueError(f"notional ${notional:,.0f} exceeds capital ${capital:,.0f}")
    if notional is not None and max_entries is None and instrument.settled_cash_only:
        max_entries = max(int(capital // notional), 1)
    need = 0.0 if notional is not None else abs(qty) * instrument.intraday_margin
    if need > capital:
        raise ValueError(f"{qty:g} {instrument.symbol} needs ${need:,.0f} intraday margin; "
                         f"capital is ${capital:,.0f}. The broker would refuse the order.")
    sig = _signals(strategy, bars)
    o, h, l, c = (bars[k].to_numpy(float).tolist() for k in ("open", "high", "low", "close"))
    tgt = sig["target"].fillna(0.0)
    if instrument.long_only:
        tgt = tgt.clip(lower=0.0)
    tgt = tgt.to_numpy(float).tolist()
    nan = [float("nan")] * len(bars)
    stp = sig["stop"].to_numpy(float).tolist() if "stop" in sig else nan
    tkp = sig["take"].to_numpy(float).tolist() if "take" in sig else nan
    cutoff = dtime.fromisoformat(flat_by) if flat_by else None
    clock = bars.index.time if cutoff else None
    starts, ends, days = _session_bounds(bars)
    rows = []

    def close(day, e_i, x_i, side, e_px, x_px, st, tk, why):
        q = notional / e_px if notional is not None else qty
        gross = side * (x_px - e_px) * instrument.point_value * q
        cost = instrument.trade_cost(q, e_px, x_px)
        rows.append((day, e_i, x_i, side, e_px, x_px,
                     abs(e_px - st) if st == st else np.nan,
                     abs(tk - e_px) if tk == tk else np.nan,
                     gross, cost, gross - cost, why, q))

    for s, e, day in zip(starts.tolist(), ends.tolist(), days):
        pos, blocked, entries = 0.0, None, 0
        e_i = e_px = st = tk = None
        for i in range(s + 1, e + 1):
            if cutoff is not None and clock[i] >= cutoff:
                if pos:
                    close(day, e_i, i, pos, e_px, o[i], st, tk, "flat_by")
                    pos = 0.0
                break
            want = tgt[i - 1]
            if max_entries is not None and entries >= max_entries and not pos:
                want = 0.0
            if blocked is not None:
                if want == blocked:
                    want = 0.0
                else:
                    blocked = None
            if want != pos:
                if pos:
                    close(day, e_i, i, pos, e_px, o[i], st, tk, "signal")
                if want and max_entries is not None and entries >= max_entries:
                    want = 0.0
                if want:
                    e_i, e_px, st, tk = i, o[i], stp[i - 1], tkp[i - 1]
                    entries += 1
                pos = want
            if not pos:
                continue
            if pos > 0:
                hit_s = st == st and l[i] <= st
                hit_t = tk == tk and h[i] >= tk
                px_s, px_t = min(o[i], st) if hit_s else 0, max(o[i], tk) if hit_t else 0
            else:
                hit_s = st == st and h[i] >= st
                hit_t = tk == tk and l[i] <= tk
                px_s, px_t = max(o[i], st) if hit_s else 0, min(o[i], tk) if hit_t else 0
            if hit_s or hit_t:
                close(day, e_i, i, pos, e_px, px_s if hit_s else px_t, st, tk,
                      "stop" if hit_s else "take")
                blocked, pos = pos, 0.0
        if pos:
            close(day, e_i, e, pos, e_px, c[e], st, tk, "eod")

    cols = ["day", "entry_i", "exit_i", "side", "entry_px", "exit_px",
            "stop_dist", "take_dist", "gross", "cost", "net", "reason", "qty"]
    trades = pd.DataFrame(rows, columns=cols)
    trades["entry_ts"] = bars.index[trades["entry_i"].to_numpy(int)] if len(trades) else []
    trades["exit_ts"] = bars.index[trades["exit_i"].to_numpy(int)] if len(trades) else []
    trades["hold"] = trades["exit_i"] - trades["entry_i"]

    day_index = pd.DatetimeIndex(pd.to_datetime(days), name="day")
    daily = pd.Series(0.0, index=day_index, name="net_usd")
    if len(trades):
        per_day = trades.groupby(pd.to_datetime(trades["day"]))["net"].sum()
        daily.loc[per_day.index] = per_day.to_numpy()

    return Result(strategy=strategy.name, params=dict(strategy.params),
                  instrument=instrument, qty=qty, capital=capital, trades=trades,
                  daily=daily, trial=trial,
                  config_hash=config_hash(strategy, instrument, bars, qty=qty,
                                          capital=capital, flat_by=flat_by,
                                          notional=notional, max_entries=max_entries),
                  meta={"notional": notional, "max_entries": max_entries,
                        "flat_by": flat_by})


def causality_check(strategy: Strategy, bars: pd.DataFrame, n_checks: int = 20,
                    seed: int = 0, min_bars: int = 400) -> int:
    """Raise LookAheadError if any signal for bar t depends on bars after t.

    Recomputes signals on `bars[:cut]` at random cut points and compares them
    with the full run's first `cut` rows. Returns the number of cuts checked.
    It cannot prove a strategy is clean — a leak that only bites at an unchecked
    cut slips through — but every common leak changes many bars, so 20 random
    cuts catch them.
    """
    full = _signals(strategy, bars)
    num = [col for col in full.columns if np.issubdtype(full[col].dtype, np.number)]
    lo = min(min_bars, len(bars) // 2)
    rng = np.random.default_rng(seed)
    cuts = sorted(set(rng.integers(lo, len(bars), size=n_checks).tolist()))
    for cut in cuts:
        part = strategy.signals(bars.iloc[:cut])
        for col in num:
            a = full[col].to_numpy(float)[:cut]
            b = part[col].to_numpy(float)
            bad = ~np.isclose(a, b, equal_nan=True)
            if bad.any():
                k = int(np.flatnonzero(bad)[0])
                raise LookAheadError(
                    f"{strategy!r}: column {col!r} at {bars.index[k]} is {a[k]} "
                    f"with the full history but {b[k]} when history stops at "
                    f"{bars.index[cut - 1]} — the signal reads the future")
    return len(cuts)
