"""data.py — one-minute bars in, validated, cut to the session, and (for futures)
stitched across contract rolls without a fake price jump at each roll.

Conventions, because each one is a place a backtest silently lies:

* **Timestamps are bar OPEN times** (Databento's `ts_event` convention). A bar
  stamped 09:30 covers 09:30:00-09:30:59.
* **Everything is America/New_York.** Timezone-aware input is converted. Naive
  input is refused unless you say what zone it is in — a naive New York file
  read as UTC shifts every session by four or five hours and nothing downstream
  would notice.
* **Futures need a continuous series.** Trading the raw front contract across a
  roll books the price gap between expiries as profit or loss. `continuous()`
  back-adjusts by that gap, which is the futures form of survivorship bias: the
  series must be built from *every* contract that traded, chosen by what was
  actually front at the time.

Data sources: a paid vendor export (e.g. Databento `ohlcv-1m`, parent symbol
`MES.FUT`). Do not scrape OpenMarket or TradingView — both sites' terms
forbid it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "America/New_York"
PRICE_COLS = ("open", "high", "low", "close")
_TS_NAMES = ("ts", "ts_event", "timestamp", "datetime", "time", "date")


def load_bars(path: str, naive_tz: str | None = None) -> pd.DataFrame:
    """Read a CSV or parquet of one-minute OHLCV bars.

    Accepts Databento columns as-is (`ts_event`, `symbol`). A `symbol` or
    `contract` column is kept as `contract` for `continuous()`.
    """
    df = pd.read_parquet(path) if str(path).endswith(".parquet") else pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    ts_col = next((c for c in _TS_NAMES if c in df.columns), None)
    if ts_col is None:
        raise ValueError(f"no timestamp column; expected one of {_TS_NAMES}")
    if "symbol" in df.columns and "contract" not in df.columns:
        df = df.rename(columns={"symbol": "contract"})

    ts = pd.to_datetime(df[ts_col])
    if ts.dt.tz is None:
        if not naive_tz:
            raise ValueError(
                "timestamps carry no timezone. Pass naive_tz='UTC' or "
                f"naive_tz='{TZ}' — guessing shifts every session.")
        ts = ts.dt.tz_localize(naive_tz)
    keep = [c for c in (*PRICE_COLS, "volume", "contract") if c in df.columns]
    out = df[keep].copy()
    out.index = pd.DatetimeIndex(ts.dt.tz_convert(TZ), name="ts")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out = out.sort_index(kind="stable")
    validate(out)
    return out


def validate(bars: pd.DataFrame) -> None:
    """Raise ValueError listing every structural problem found."""
    problems = []
    missing = [c for c in PRICE_COLS if c not in bars.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        problems.append("index must be a timezone-aware DatetimeIndex")
    if not bars.index.is_monotonic_increasing:
        problems.append("timestamps not sorted")
    key = [bars.index, bars["contract"]] if "contract" in bars.columns else [bars.index]
    dups = pd.MultiIndex.from_arrays(key).duplicated().sum()
    if dups:
        problems.append(f"{dups} duplicate bars")
    px = bars[list(PRICE_COLS)]
    if px.isna().any().any():
        problems.append(f"{int(px.isna().sum().sum())} missing prices")
    if (bars["high"] < bars[["open", "close"]].max(axis=1)).any():
        problems.append("high below open/close")
    if (bars["low"] > bars[["open", "close"]].min(axis=1)).any():
        problems.append("low above open/close")
    if "volume" in bars.columns and (bars["volume"] < 0).any():
        problems.append("negative volume")
    if problems:
        raise ValueError("bad bars: " + "; ".join(problems))


def rth(bars: pd.DataFrame, start: str = "09:30", end: str = "16:00") -> pd.DataFrame:
    """Regular trading hours only: bars that OPEN in [start, end)."""
    return bars.between_time(start, end, inclusive="left")


def session_dates(index: pd.DatetimeIndex) -> np.ndarray:
    """New York calendar date of each bar. Callers pass RTH bars, so a session
    never crosses midnight."""
    return index.tz_convert(TZ).date


def continuous(bars: pd.DataFrame) -> pd.DataFrame:
    """Back-adjusted continuous futures series from per-contract bars.

    Each session trades the contract with the most volume that day, never
    stepping back to an earlier expiry. At each roll every earlier price is
    shifted by the gap (new - old close at the last bar both traded), so the
    latest contract is unadjusted and a roll books no PnL. Calendar spreads
    (symbols containing '-') are dropped. Roll dates and gaps are kept in
    `.attrs["rolls"]`.
    """
    if "contract" not in bars.columns:
        raise ValueError("continuous() needs a `contract` column")
    b = bars[~bars["contract"].astype(str).str.contains("-", regex=False)]
    day = pd.Index(session_dates(b.index), name="day")
    vol = b.groupby([day, b["contract"]])["volume"].sum().unstack(fill_value=0.0)
    last_bar = pd.Series(b.index, index=b.index).groupby(b["contract"]).max()
    expiry_rank = last_bar.rank(method="first").astype(int)
    by_rank = {r: c for c, r in expiry_rank.items()}
    front = vol.idxmax(axis=1).map(expiry_rank).cummax().map(by_rank)

    rolls, adj_after = [], {}
    days = list(front.index)
    for prev, cur in zip(days[:-1], days[1:]):
        old, new = front[prev], front[cur]
        if old == new:
            continue
        on_prev = b[day == prev]
        a = on_prev[on_prev["contract"] == old]["close"]
        n = on_prev[on_prev["contract"] == new]["close"]
        both = a.index.intersection(n.index)
        if len(both):
            gap = float(n.loc[both[-1]] - a.loc[both[-1]])
        elif len(a) and len(n):
            gap = float(n.iloc[-1] - a.iloc[-1])
        else:
            gap = float("nan")
        rolls.append({"day": cur, "from": old, "to": new, "gap": gap})
        adj_after[cur] = gap
    if any(not np.isfinite(r["gap"]) for r in rolls):
        bad = [str(r["day"]) for r in rolls if not np.isfinite(r["gap"])]
        raise ValueError(f"cannot measure the roll gap on {bad}: "
                         "the new contract has no bars on the prior session")

    # Shift for a session = sum of every roll gap that happens AFTER it.
    shift = pd.Series(0.0, index=front.index)
    for d, gap in adj_after.items():
        shift[shift.index < d] += gap

    pick = b["contract"].to_numpy() == front.reindex(day).to_numpy()
    out = b[pick].copy()
    s = shift.reindex(pd.Index(session_dates(out.index))).to_numpy()
    for c in PRICE_COLS:
        out[c] = out[c] + s
    out["adj"] = s
    out.attrs["rolls"] = rolls
    out.attrs["continuous"] = "back-adjusted by roll gap, front = max daily volume"
    return out


def load_daily(path: str, column: str | None = None) -> pd.Series:
    """A daily series (e.g. FRED's VIXCLS CSV) as float values indexed by date.
    The value for date d is the CLOSE of d: for a VIX close (16:15 ET) it is
    only usable from session d+1 on — use `prior_day()`."""
    df = pd.read_csv(path)
    col = column or df.columns[-1]
    s = pd.to_numeric(df[col], errors="coerce")
    s.index = pd.to_datetime(df[df.columns[0]]).dt.date
    return s.dropna().sort_index()


def prior_day(series: pd.Series, days) -> np.ndarray:
    """For each session date in `days`, the latest value dated strictly BEFORE
    it. Never the same day: a daily close is known only after the session."""
    idx = pd.to_datetime(pd.Index(series.index))
    pos = idx.searchsorted(pd.to_datetime(pd.Index(days)), side="left") - 1
    vals = series.to_numpy(float)
    return np.where(pos >= 0, vals[np.clip(pos, 0, None)], np.nan)


def synthetic_bars(days: int = 300, seed: int = 0, start: str = "2023-01-03",
                   price: float = 5000.0, tick: float = 0.25,
                   sigma_ticks: float = 1.5, drift_ticks: float = 0.0,
                   minutes: int = 390) -> pd.DataFrame:
    """A random walk on the tick grid, RTH only. **Has no edge by construction**:
    any rule that 'works' on it is measuring luck or a leak. Used by the tests
    and as the notebook's fallback when no real data file exists."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=days)
    n = days * minutes
    r = np.round(rng.normal(drift_ticks, sigma_ticks, n)) * tick
    g = np.zeros(n)
    g[::minutes] = np.round(rng.normal(0.0, 8.0, days)) * tick
    close = price + np.cumsum(r + g)
    open_ = close - r
    wick = lambda: np.abs(np.round(rng.normal(0.0, 0.7, n))) * tick  # noqa: E731
    high = np.maximum(open_, close) + wick()
    low = np.minimum(open_, close) - wick()
    stamps = (np.repeat(dates.values, minutes)
              + np.tile(pd.to_timedelta(570 + np.arange(minutes), unit="min").values, days))
    idx = pd.DatetimeIndex(stamps, name="ts").tz_localize(TZ)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(50, 500, n).astype(float)}, index=idx)
