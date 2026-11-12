"""strategies.py — a baseline, a worked example, and the published rules under
test. No rule has cleared; the trial record is intraday/docs/RESULTS.md.

* `SessionDrift` — long from the open to the close every session. The intraday
  version of buy-and-hold, and the bar every rule is compared against: a
  long-biased rule that "works" may just be holding the session's drift.
* `OpeningRangeBreakout` — kept as a worked example that exercises every part
  of the harness (stops, targets, one entry per session). It is here to show
  how to write a rule, not because it was picked.

Writing a new one: subclass `Strategy`, compute everything with groupby/cum*
operations **within the session** so bar t never sees bar t+1, and run
`engine.causality_check` before you trust a single number. The common leak is
`groupby(day).transform("max")` — the session's high, known only at the close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import prior_day, session_dates
from .engine import Strategy


class SessionDrift(Strategy):
    name = "session_drift"

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"target": 1.0}, index=bars.index)


class OpeningRangeBreakout(Strategy):
    """First close outside the first `or_minutes` bars' range sets the side for
    the rest of the session. Stop at the far side of the range; optional target
    at `take_r` times the range."""

    name = "orb"

    def __init__(self, or_minutes: int = 15, take_r: float | None = None):
        super().__init__(or_minutes=or_minutes, take_r=take_r)

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        n = int(self.params["or_minutes"])
        day = session_dates(bars.index)
        k = bars.groupby(day).cumcount().to_numpy()
        # Range high/low become known at the CLOSE of bar n-1, and not before.
        hi = bars["high"].groupby(day).cummax().where(k == n - 1).groupby(day).ffill()
        lo = bars["low"].groupby(day).cummin().where(k == n - 1).groupby(day).ffill()

        side = pd.Series(np.where(bars["close"] > hi, 1.0,
                                  np.where(bars["close"] < lo, -1.0, np.nan)),
                         index=bars.index).where(k >= n)
        nth = side.notna().astype(int).groupby(day).cumsum()
        first = side.where(nth == 1).groupby(day).ffill()
        target = first.fillna(0.0)

        stop = pd.Series(np.where(target > 0, lo, np.where(target < 0, hi, np.nan)),
                         index=bars.index)
        out = pd.DataFrame({"target": target, "stop": stop}, index=bars.index)
        r = self.params["take_r"]
        if r:
            width = hi - lo
            out["take"] = np.where(target > 0, hi + r * width,
                                   np.where(target < 0, lo - r * width, np.nan))
        return out


# --------------------------------------------------------------- shared pieces
def _minute(bars: pd.DataFrame) -> np.ndarray:
    """Minutes since 09:30 for each bar's OPEN time (09:30 bar = 0)."""
    return (bars.index.hour * 60 + bars.index.minute - 570).to_numpy()


def _prev_session_close(bars: pd.DataFrame, day) -> pd.Series:
    """Each bar's previous session's last close (NaN on the first session)."""
    last = bars["close"].groupby(day).last()
    return pd.Series(pd.Series(last.shift(1).to_numpy(), index=last.index)
                     .reindex(day).to_numpy(), index=bars.index)


def _hhmm(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m) - 570


# -------------------------------------------------------- intraday momentum
class IntradayMomentum(Strategy):
    """Market intraday momentum. At `entry` (default 15:30), take the side of
    the return from the previous session's close to `ref`, and hold to the
    close. One trade per session, no stop, no target.

    * ref="15:30": Baltussen, Da, Lammers & Martens (JFE 2021) — the rest of
      the day predicts the last 30 minutes.
    * ref="10:00": Gao, Han, Li & Zhou (JFE 2018) — the first half hour
      (prior close to 10:00) predicts the last half hour.

    A time "HH:MM" is a price AT that minute: the close of the bar stamped one
    minute earlier. Entry fills at the open of the `entry` bar.
    """

    name = "intraday_momentum"

    def __init__(self, ref: str = "15:30", entry: str = "15:30"):
        super().__init__(ref=ref, entry=entry)

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        day = session_dates(bars.index)
        m = _minute(bars)
        ref_bar, sig_bar = _hhmm(self.params["ref"]) - 1, _hhmm(self.params["entry"]) - 1
        if ref_bar > sig_bar:
            raise ValueError("ref must be at or before entry")
        r = (bars["close"] - _prev_session_close(bars, day)).where(m == ref_bar)
        r = r.groupby(day).ffill()
        side = np.sign(r).where(m >= sig_bar).fillna(0.0)
        return pd.DataFrame({"target": side}, index=bars.index)


# ------------------------------------------------------------- noise area
class NoiseArea(Strategy):
    """Zarattini, Aziz & Barbon, "Beat the Market" (SFI 24-97, 2024), as
    published: the base model with the current-band + VWAP trailing stop.

    sigma(t, m) = mean over the previous `lookback` sessions of
                  |close at minute m / that session's open - 1|
    UB = max(open_t, prev_close) * (1 + sigma);  LB = min(open_t, prev_close) * (1 - sigma)

    Decisions only every `every` minutes (HH:00 and HH:30), on the price at that
    minute: flat -> long above UB, short below LB. Long stays while price is
    above max(UB, VWAP), else exits (reversing short if below LB); short
    mirror-image. Flat at the close. Sizing is the engine's (fixed notional),
    not the paper's volatility targeting.

    Sigma needs `min_days` of the lookback present for that minute (half-day
    sessions leave late minutes empty). `vm` scales the band width (the paper's
    "volatility multiplier", section 4.4); the published base model uses 1.0.

    Exit options (defaults reproduce the paper): `exit_rule="vwap"` trails VWAP
    alone instead of max(band, VWAP); `stop_every` checks the trailing stop
    every that many minutes (entries still only every `every` minutes).
    """

    name = "noise_area"

    def __init__(self, lookback: int = 14, every: int = 30, vwap_stop: bool = True,
                 min_days: int = 10, vm: float = 1.0, exit_rule: str = "band_vwap",
                 stop_every: int | None = None):
        if exit_rule not in ("band_vwap", "vwap"):
            raise ValueError("exit_rule must be 'band_vwap' or 'vwap'")
        super().__init__(lookback=lookback, every=every, vwap_stop=vwap_stop,
                         min_days=min_days, vm=vm, exit_rule=exit_rule,
                         stop_every=stop_every or every)

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        day = session_dates(bars.index)
        m = _minute(bars)
        o = bars["open"].groupby(day).transform("first")          # known at 09:30
        move = (bars["close"] / o - 1.0).abs()
        grid = pd.DataFrame({"day": day, "m": m, "v": move.to_numpy()}) \
                 .pivot_table(index="day", columns="m", values="v", aggfunc="first")
        sigma_tbl = grid.rolling(p["lookback"], min_periods=p["min_days"]).mean().shift(1)
        stacked = sigma_tbl.stack(future_stack=True)
        sigma = p["vm"] * stacked.reindex(pd.MultiIndex.from_arrays([day, m])).to_numpy()

        pc = _prev_session_close(bars, day).fillna(o)
        ub = (np.maximum(o, pc) * (1.0 + sigma)).to_numpy()
        lb = (np.minimum(o, pc) * (1.0 - sigma)).to_numpy()
        tp = (bars["high"] + bars["low"] + bars["close"]) / 3.0
        vol = bars["volume"].clip(lower=0.0)
        cv = vol.groupby(day).cumsum()
        vwap = ((tp * vol).groupby(day).cumsum() / cv.where(cv > 0)).to_numpy()
        close = bars["close"].to_numpy()

        target = np.zeros(len(bars))
        mm = np.asarray(m) + 1
        is_entry = mm % p["every"] == 0
        check = np.flatnonzero(is_entry | (mm % p["stop_every"] == 0))
        codes = pd.factorize(day)[0]
        starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
        ends = np.r_[starts[1:], len(bars)]
        vw_only = p["exit_rule"] == "vwap"
        ci = 0
        for s, e in zip(starts, ends):
            pos = 0.0
            last = s
            while ci < len(check) and check[ci] < e:
                i = check[ci]
                ci += 1
                c, u, l_, w = close[i], ub[i], lb[i], vwap[i]
                if not is_entry[i] and not pos:
                    continue                     # stop-only check, nothing held
                target[last:i] = pos
                last = i
                band_ok = u == u and l_ == l_
                use_w = p["vwap_stop"] and w == w
                if pos > 0:
                    stop = w if vw_only and use_w else (max(u, w) if use_w and band_ok
                                                       else u)
                    rev = band_ok and is_entry[i] and c < l_
                    new = 1.0 if stop == stop and c > stop else (-1.0 if rev else 0.0)
                elif pos < 0:
                    stop = w if vw_only and use_w else (min(l_, w) if use_w and band_ok
                                                       else l_)
                    rev = band_ok and is_entry[i] and c > u
                    new = -1.0 if stop == stop and c < stop else (1.0 if rev else 0.0)
                elif band_ok:
                    new = 1.0 if c > u else (-1.0 if c < l_ else 0.0)
                else:
                    new = 0.0
                pos = new
            target[last:e] = pos
        return pd.DataFrame({"target": target}, index=bars.index)


# ---------------------------------------------------------------- day filter
class DayFilter(Strategy):
    """Trade `inner` only on sessions where `allowed[date]` is True. Build
    `allowed` from information dated BEFORE the session (`data.prior_day`)."""

    name = "day_filter"

    def __init__(self, inner: Strategy, allowed: pd.Series, label: str):
        super().__init__(inner=repr(inner), filter=label)
        self.inner, self.allowed = inner, allowed

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        sig = self.inner.signals(bars).copy()
        ok = self.allowed.reindex(session_dates(bars.index)).fillna(False).to_numpy(bool)
        sig["target"] = sig["target"].where(ok, 0.0)
        return sig


def vix_above_median(vix: pd.Series, sessions, window: int = 252) -> pd.Series:
    """True for a session when the PRIOR day's VIX close was above the median
    of the `window` VIX closes up to and including that prior day."""
    flag = (vix > vix.rolling(window, min_periods=window).median()).astype(float)
    flag[vix.rolling(window, min_periods=window).median().isna()] = np.nan
    vals = prior_day(flag, sessions)
    return pd.Series(vals == 1.0, index=pd.Index(sessions))
