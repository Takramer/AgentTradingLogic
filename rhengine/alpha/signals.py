"""signals.py — the RMX signal set, frozen in docs/RMX-PREREGISTRATION.md.

Core: residual momentum at the **horizon its citation actually uses**.

The repo's existing `residualmomentum` sleeve cites Blitz, Huij & Martens
(2011) but implements a 36-**day** beta window and a 12-**day** formation
window with a 1-**day** skip (audit FC-8). At 12 days the documented US equity
effect is short-term *reversal*, so the sleeve's economic story did not apply
to the horizon it traded. Here the horizon is months, as published:

    beta        36 monthly observations ending at the rebalance month
    formation   months M-11 .. M-1   (11 months)
    skip        month M              (the 1-month reversal month)
    ranking     mean(residual) / std(residual) over the formation window

Blitz, Huij & Martens, *Residual Momentum*, Journal of Empirical Finance 18(3),
2011. https://doi.org/10.1016/j.jempfin.2011.01.003

The optional legs are pulled from the other sleeves in this repo (short-term
reversal from `bollinger`'s oversold leg, the SMA/trend test from
`classify_spy_regime` and `macd`) plus the two defences the audit itself
recommended in O-4. Each has a published prior; none was chosen by looking at
this sample.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BETA_MONTHS = 36
FORM_MONTHS = 11        # months M-11 .. M-1
SKIP_MONTHS = 1
MIN_BETA_OBS = 30


# ------------------------------------------------------------------ primitives
def zscore(s: pd.Series, clip: float = 3.0) -> pd.Series:
    """Cross-sectional z-score, winsorised so one name cannot set the scale."""
    s = s.astype(float)
    mu, sd = s.mean(), s.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).clip(-clip, clip)


@dataclass
class MonthlyFactors:
    """Monthly excess returns for every name plus the market, on one grid."""

    month_ends: pd.DatetimeIndex
    stock_ex: pd.DataFrame       # months x tickers, excess of rf
    mkt_ex: pd.Series            # months, market excess return
    raw: pd.DataFrame            # months x tickers, simple total return


def build_monthly(panel, lo: str | None = None, hi: str | None = None) -> MonthlyFactors:
    me = panel.month_ends(lo, hi)
    raw = panel.adj.reindex(me).pct_change()

    # Compound the daily rf / market series onto the same month-end grid: each
    # session belongs to the first month-end at or after it.
    days = panel.adj.index
    # Only sessions inside (me[0], me[-1]] belong to a complete month bucket;
    # everything before me[0] would otherwise be swept into the first month and
    # report a multi-year "monthly" return.
    inside = (days > me[0]) & (days <= me[-1])
    bucket = me[np.searchsorted(me.values, days[inside].values, side="left")]
    key = pd.Series(bucket, index=days[inside])
    rf_m = panel.rf[inside].groupby(key).apply(lambda s: (1 + s).prod() - 1.0)
    # compound the market TOTAL return, then subtract the compounded cash leg;
    # compounding an excess-return series directly is not the same thing.
    mkt_tot = (panel.mkt + panel.rf)[inside]
    mk_m = mkt_tot.groupby(key).apply(lambda s: (1 + s).prod() - 1.0)
    rf_m = rf_m.reindex(me)          # me[0] has no bucket -> NaN, as it should
    mk_m = mk_m.reindex(me) - rf_m
    rf_m = rf_m.fillna(0.0)
    stock_ex = raw.sub(rf_m, axis=0)
    return MonthlyFactors(month_ends=me, stock_ex=stock_ex,
                          mkt_ex=mk_m.astype(float), raw=raw)


# ------------------------------------------------------------- residual engine
def residual_stats(mf: MonthlyFactors, i: int, names: pd.Index) -> pd.DataFrame:
    """Beta/alpha/residual statistics for `names` as of month index `i`.

    Uses months (i-35 .. i) inclusive, i.e. only data available at that
    rebalance. Returns a frame with resmom, resvol, beta, nobs.
    """
    lo = i - BETA_MONTHS + 1
    if lo < 0:
        return pd.DataFrame()
    Y = mf.stock_ex.iloc[lo:i + 1][names].to_numpy(dtype=float)   # 36 x N
    m = mf.mkt_ex.iloc[lo:i + 1].to_numpy(dtype=float)            # 36

    ok = np.isfinite(Y) & np.isfinite(m)[:, None]
    nobs = ok.sum(axis=0)
    Yc = np.where(ok, Y, 0.0)
    mc = np.where(ok, m[:, None], 0.0)

    n = np.maximum(nobs, 1)
    ybar = Yc.sum(axis=0) / n
    mbar = mc.sum(axis=0) / n
    cov = (np.where(ok, (Y - ybar) * (m[:, None] - mbar), 0.0)).sum(axis=0) / n
    var = (np.where(ok, (m[:, None] - mbar) ** 2, 0.0)).sum(axis=0) / n
    beta = np.divide(cov, var, out=np.zeros_like(cov), where=var > 0)
    alpha = ybar - beta * mbar
    resid = Y - alpha - beta * m[:, None]
    resid = np.where(ok, resid, np.nan)

    resvol = np.nanstd(resid, axis=0)

    # formation window: months M-11 .. M-1  (drop the last SKIP_MONTHS rows)
    f = resid[-(FORM_MONTHS + SKIP_MONTHS):-SKIP_MONTHS or None]
    fmean = np.nanmean(f, axis=0)
    fstd = np.nanstd(f, axis=0)
    resmom = np.divide(fmean, fstd, out=np.full_like(fmean, np.nan), where=fstd > 0)

    return pd.DataFrame(
        {"resmom": resmom, "resvol": resvol, "beta": beta, "nobs": nobs,
         "form_obs": np.isfinite(f).sum(axis=0)},
        index=names,
    )


# ------------------------------------------------------------------- the legs
@dataclass
class RMXConfig:
    """Frozen strategy parameters. Changing one of these is a logged trial."""

    n_positions: int = 50
    legs: tuple[str, ...] = ("resmom",)     # + "reversal", "lowvol"
    trend_gate: bool = False                # L3
    vol_target: float | None = None         # L4, annualised (e.g. 0.12)
    vol_window: int = 60
    max_exposure: float = 1.0
    weights: dict[str, float] = field(default_factory=dict)


def composite_score(stats: pd.DataFrame, last_month_ret: pd.Series,
                    cfg: RMXConfig) -> pd.Series:
    """Equal-weight blend of the enabled legs' cross-sectional z-scores."""
    parts = {}
    if "resmom" in cfg.legs:
        parts["resmom"] = zscore(stats["resmom"])
    if "reversal" in cfg.legs:
        # L1 - short-term reversal (Jegadeesh 1990): the skipped month is a
        # signal in its own right, with the opposite sign to momentum.
        parts["reversal"] = zscore(-last_month_ret.reindex(stats.index))
    if "lowvol" in cfg.legs:
        # L2 - low idiosyncratic volatility (Ang, Hodrick, Xing & Zhang 2006).
        parts["lowvol"] = zscore(-stats["resvol"])
    if not parts:
        raise ValueError("no legs enabled")
    df = pd.DataFrame(parts)
    if cfg.weights:
        w = pd.Series({k: cfg.weights.get(k, 1.0) for k in df.columns})
        return (df * w).sum(axis=1) / w.sum()
    return df.mean(axis=1)


def trend_ok(spy: pd.Series, date: pd.Timestamp) -> bool:
    """L3 - absolute-momentum / trend gate (Faber 2007, SSRN 962461).

    Risk-on unless SPY is BOTH below its 200-day average AND down over the
    trailing 12 months. Requiring both is deliberately slow: single-condition
    gates whipsaw, and the audit's whole complaint about the old stack was
    decisions that looked good on one 20-day episode.
    """
    s = spy[spy.index <= date]
    if len(s) < 252:
        return True
    sma200 = s.iloc[-200:].mean()
    twelve = s.iloc[-1] / s.iloc[-252] - 1.0
    return not (s.iloc[-1] < sma200 and twelve < 0)


def vol_scale(daily_ret: pd.Series, date: pd.Timestamp, target: float,
              window: int, cap: float = 1.0) -> float:
    """L4 - volatility targeting (Barroso & Santa-Clara 2015).

    De-risk only: the multiplier is capped at `cap` (1.0 by default) so the
    book is never levered. Uses only returns strictly before `date`.
    """
    r = daily_ret[daily_ret.index < date]
    if len(r) < window:
        return cap
    realised = r.iloc[-window:].std() * np.sqrt(252.0)
    if not np.isfinite(realised) or realised <= 0:
        return cap
    return float(min(cap, target / realised))
