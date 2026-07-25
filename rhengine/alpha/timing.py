"""timing.py — the exposure sleeve (RMX-D).

Why this module and not another stock-ranking module: the IC screen in
docs/RMX-PREREGISTRATION.md Amendment B found that no price-only cross-sectional
signal ranks next month's return in a large-cap universe (best |t| = 0.89 over
131 monthly cross-sections, every top decile below its own universe mean). The
variable that was never tested is *how much to be invested*, and unlike stock
selection it can be tested on data with no survivorship bias at all: an index
level does not delist.

Three rules, four numbers, all from published work:

* **trend** - invested while the index closes above its 10-month moving average
  (Faber 2007, https://doi.org/10.2139/ssrn.962461). Checked monthly, not daily,
  which is the published form and also the low-turnover one.
* **volatility target** - scale exposure by `min(1, target / realised vol)`.
  De-risk only; never levered (Barroso & Santa-Clara 2015,
  https://doi.org/10.1016/j.jfineco.2014.11.003).
* **risk-off asset** - Treasuries instead of cash while defensive, but only when
  the bond series' own 12-month absolute momentum is positive (Antonacci 2014).
  Without that condition the defensive leg is a naked long-duration bet, which
  is how a 2008-calibrated rule loses money in 2022.

Everything is computed from data strictly before the decision date, and every
decision is executed at the NEXT session's close.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TimingConfig:
    trend_gate: bool = True       # L3 on/off (an explicit switch, not sma_months=1)
    sma_months: int = 10          # Faber's 10-month moving average
    vol_target: float | str | None = 0.12
    """Annualised target. A float fixes it; ``"index"`` sets it to the index's
    own realised volatility measured on an EXPANDING window of history strictly
    before each decision, which removes the parameter entirely - the overlay
    then only de-risks when the market is more volatile than it has been, and
    never imposes a permanent underweight."""
    vol_window: int = 60          # sessions
    abs_mom_months: int = 12      # for the risk-off asset's own trend test
    max_exposure: float = 1.0
    """Cap on gross equity exposure. Above 1.0 the book borrows, which
    `book.run_book` charges at rf + a retail margin spread. Moreira & Muir
    (2017, https://doi.org/10.1111/jofi.12513) show volatility-managed equity
    portfolios earn significant alpha and their construction scales above one;
    capping it is the conservative departure from that paper, not the
    aggressive one."""
    use_bonds: bool = True


def month_end_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(idx, index=idx)
    return pd.DatetimeIndex(s.groupby([idx.year, idx.month]).last().values)


def sma_trend(px: pd.Series, month_ends: pd.DatetimeIndex, months: int) -> pd.Series:
    """True while the month-end close is above the `months`-month average.

    The average is of month-end closes, which is Faber's construction; using a
    200-day daily average instead changes the answer by very little and adds a
    parameter that would then need its own justification.
    """
    m = px.reindex(month_ends).ffill()
    return (m > m.rolling(months).mean()).reindex(month_ends)


def abs_momentum(px: pd.Series, month_ends: pd.DatetimeIndex, months: int) -> pd.Series:
    m = px.reindex(month_ends).ffill()
    return (m / m.shift(months) - 1.0) > 0.0


def realised_vol(daily_ret: pd.Series, month_ends: pd.DatetimeIndex,
                 window: int) -> pd.Series:
    """Annualised trailing volatility as of each month end, causal by shift."""
    v = daily_ret.rolling(window).std() * np.sqrt(252.0)
    return v.reindex(month_ends, method="pad")


def build_schedule(risk_px: pd.Series, safe_px: pd.Series | None,
                   cfg: TimingConfig,
                   month_ends: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Per-rebalance decision table: equity weight, bond weight, cash weight.

    Returns a frame indexed by decision date. Nothing here reads a price on or
    after the decision date's close beyond that close itself, and `book.run_book`
    applies the row at the following session.
    """
    idx = risk_px.index
    me = month_end_index(idx) if month_ends is None else month_ends
    me = me[me.isin(idx)]

    ret = risk_px.pct_change()
    trend = (sma_trend(risk_px, me, cfg.sma_months) if cfg.trend_gate
             else pd.Series(True, index=me))

    if cfg.vol_target is None:
        scale = pd.Series(cfg.max_exposure, index=me)
    else:
        vol = realised_vol(ret, me, cfg.vol_window)
        if cfg.vol_target == "index":
            # expanding-window realised vol of the index itself, shifted one
            # month so the target never sees the period it is scaling
            long_run = (ret.expanding(min_periods=252).std() * np.sqrt(252.0))
            target = long_run.reindex(me, method="pad").shift(1)
            target = target.bfill()
        else:
            target = pd.Series(float(cfg.vol_target), index=me)
        scale = (target / vol).clip(upper=cfg.max_exposure).fillna(cfg.max_exposure)

    eq = np.where(trend.reindex(me).fillna(True), cfg.max_exposure, 0.0) * scale
    eq = pd.Series(eq, index=me).clip(0.0, cfg.max_exposure)

    # The risk-off allocation fills the gap up to 1.0 of capital, never up to
    # `max_exposure` - borrowing in order to hold more bonds is not what the
    # defensive leg is for.
    idle = (1.0 - eq).clip(lower=0.0)
    bond = pd.Series(0.0, index=me)
    if cfg.use_bonds and safe_px is not None:
        bond_ok = abs_momentum(safe_px, me, cfg.abs_mom_months).reindex(me).fillna(False)
        bond = pd.Series(np.where(bond_ok, 1.0, 0.0), index=me) * idle

    return pd.DataFrame({"equity": eq, "bond": bond,
                         "cash": (idle - bond).clip(lower=0.0),
                         "trend": trend.reindex(me).fillna(True),
                         "vol_scale": scale})


def apply_to_book(core_targets: dict[pd.Timestamp, pd.Series],
                  schedule: pd.DataFrame,
                  safe_ticker: str | None) -> dict[pd.Timestamp, pd.Series]:
    """Fold the timing schedule into the stock-level weight targets.

    The equity book keeps its own shape; the schedule decides what fraction of
    capital it gets, and hands the remainder to the safe asset or to cash.
    """
    out: dict[pd.Timestamp, pd.Series] = {}
    for d, w in core_targets.items():
        if d not in schedule.index:
            continue
        row = schedule.loc[d]
        s = w / w.sum() * float(row["equity"]) if w.sum() > 0 else w * 0.0
        if safe_ticker and row["bond"] > 0:
            s = pd.concat([s, pd.Series({safe_ticker: float(row["bond"])})])
        out[d] = s.groupby(level=0).sum()
    return out
