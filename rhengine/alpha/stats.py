"""stats.py — honest performance statistics.

Fixes the three statistical findings of the audit:

* **FC-3** — no statistic here is computed with `n = trade count`. Every t-stat
  is computed on a **non-overlapping return series** (monthly by default) with
  Newey-West HAC standard errors, so duplicating positions cannot raise it.
* **FC-6** — nothing reports a return without also reporting alpha and beta
  against a benchmark, with the alpha's own standard error.
* **FC-14** — the Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014,
  https://doi.org/10.3905/jpm.2014.40.5.094) takes the number of trials that
  were actually run, so the multiple-testing burden is priced in rather than
  ignored.

Implemented on numpy alone: statsmodels is not installed in this environment
and the estimators involved are three lines each.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from scipy import stats as sps

TRADING_DAYS = 252.0


# --------------------------------------------------------------------- OLS/HAC
def _newey_west_ols(y: np.ndarray, X: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """OLS coefficients and Newey-West (Bartlett kernel) standard errors."""
    n, k = X.shape
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    resid = y - X @ beta
    S = (X * resid[:, None]).T @ (X * resid[:, None])
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1.0)
        G = (X[L:] * resid[L:, None]).T @ (X[:-L] * resid[:-L, None])
        S += w * (G + G.T)
    cov = xtx_inv @ S @ xtx_inv * (n / max(n - k, 1))
    return beta, np.sqrt(np.maximum(np.diag(cov), 0.0))


def alpha_beta(ret: pd.Series, bench: pd.Series, rf: pd.Series | float = 0.0,
               lags: int = 3, periods_per_year: float = 12.0) -> dict:
    """Regress excess strategy return on excess benchmark return.

    `ret`, `bench` and `rf` must share an index of NON-OVERLAPPING periods
    (monthly). Returns annualised alpha plus the HAC t-stat of the intercept —
    the number audit FC-6 asked for and the repo never produced.
    """
    df = pd.concat({"r": ret, "b": bench}, axis=1).dropna()
    if isinstance(rf, pd.Series):
        df["rf"] = rf.reindex(df.index).fillna(0.0)
    else:
        df["rf"] = float(rf)
    if len(df) < 12:
        return {"alpha_ann": np.nan, "alpha_t": np.nan, "beta": np.nan, "n": len(df)}
    y = (df["r"] - df["rf"]).to_numpy(float)
    x = (df["b"] - df["rf"]).to_numpy(float)
    X = np.column_stack([np.ones(len(x)), x])
    coef, se = _newey_west_ols(y, X, lags)
    return {
        "alpha_ann": float(coef[0] * periods_per_year),
        "alpha_t": float(coef[0] / se[0]) if se[0] > 0 else np.nan,
        "beta": float(coef[1]),
        "beta_t": float(coef[1] / se[1]) if se[1] > 0 else np.nan,
        "n": int(len(df)),
    }


def deflated_sharpe(sr: float, n: int, trials: int, skew: float, kurt: float,
                    periods_per_year: float = 12.0) -> float:
    """Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio, as a probability.

    `sr` is the observed Sharpe per period (not annualised). Returns P(true SR
    > 0) after deducting the selection bias implied by `trials` independent
    attempts. Values below ~0.95 mean the result is not distinguishable from
    the best of that many random tries.
    """
    if n < 12 or trials < 1 or not np.isfinite(sr):
        return float("nan")
    # Asymptotic SE of the Sharpe estimator under non-normal returns
    # (Lo 2002 / Mertens 2002), which is also the dispersion the expected
    # maximum of `trials` draws is scaled by.
    se_sr = np.sqrt((1 - skew * sr + 0.25 * (kurt - 1) * sr ** 2) / (n - 1))
    if se_sr <= 0:
        return float("nan")
    if trials <= 1:
        sr0 = 0.0
    else:
        euler = 0.5772156649015329
        z1 = sps.norm.ppf(1.0 - 1.0 / trials)
        z2 = sps.norm.ppf(1.0 - 1.0 / (trials * np.e))
        sr0 = se_sr * ((1 - euler) * z1 + euler * z2)
    return float(sps.norm.cdf((sr - sr0) / se_sr))


# ------------------------------------------------------------------ core stats
@dataclass
class Perf:
    label: str
    start: str
    end: str
    n_days: int
    n_months: int
    total_return_pct: float
    cagr_pct: float
    vol_ann_pct: float
    sharpe: float
    sortino: float
    max_dd_pct: float
    calmar: float
    ulcer_pct: float
    skew: float
    kurtosis: float
    worst_month_pct: float
    best_month_pct: float
    hit_months_pct: float

    def row(self) -> str:
        return ("{:<26} {:>9.1f}% {:>7.2f}% {:>7.2f}% {:>7.2f} {:>7.2f} "
                "{:>7.2f}% {:>6.2f}").format(
            self.label[:26], self.total_return_pct, self.cagr_pct,
            self.vol_ann_pct, self.sharpe, self.sortino,
            self.max_dd_pct, self.calmar)

    @staticmethod
    def header() -> str:
        return ("{:<26} {:>10} {:>8} {:>8} {:>7} {:>7} {:>8} {:>6}".format(
            "book", "total", "CAGR", "vol", "Sharpe", "Sortino", "maxDD", "Calmar"))


def drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def perf(daily_ret: pd.Series, rf: pd.Series | float = 0.0, label: str = "") -> Perf:
    """Full performance record from a DAILY return series (mark-to-market)."""
    r = daily_ret.dropna()
    if len(r) < 20:
        raise ValueError(f"{label}: too few observations ({len(r)})")
    eq = (1.0 + r).cumprod()
    dd = drawdown(eq)
    years = len(r) / TRADING_DAYS
    rf_d = rf.reindex(r.index).fillna(0.0) if isinstance(rf, pd.Series) else pd.Series(rf, index=r.index)
    ex = r - rf_d
    down = ex[ex < 0]
    m = r.resample("ME").apply(lambda s: (1 + s).prod() - 1.0)
    return Perf(
        label=label,
        start=str(r.index[0].date()), end=str(r.index[-1].date()),
        n_days=len(r), n_months=len(m),
        total_return_pct=float(eq.iloc[-1] - 1.0) * 100.0,
        cagr_pct=float(eq.iloc[-1] ** (1.0 / years) - 1.0) * 100.0,
        vol_ann_pct=float(r.std() * np.sqrt(TRADING_DAYS)) * 100.0,
        sharpe=float(ex.mean() / ex.std() * np.sqrt(TRADING_DAYS)) if ex.std() > 0 else np.nan,
        sortino=float(ex.mean() / down.std() * np.sqrt(TRADING_DAYS)) if len(down) > 2 and down.std() > 0 else np.nan,
        max_dd_pct=float(dd.min()) * 100.0,
        calmar=float((eq.iloc[-1] ** (1.0 / years) - 1.0) / abs(dd.min())) if dd.min() < 0 else np.nan,
        ulcer_pct=float(np.sqrt((dd ** 2).mean())) * 100.0,
        skew=float(m.skew()) if len(m) > 3 else np.nan,
        kurtosis=float(m.kurtosis()) if len(m) > 3 else np.nan,
        worst_month_pct=float(m.min()) * 100.0,
        best_month_pct=float(m.max()) * 100.0,
        hit_months_pct=float((m > 0).mean()) * 100.0,
    )


def monthly(daily_ret: pd.Series) -> pd.Series:
    return daily_ret.resample("ME").apply(lambda s: (1 + s).prod() - 1.0)


def compare(strategy: pd.Series, benchmarks: dict[str, pd.Series],
            rf: pd.Series | float = 0.0, trials: int = 1,
            label: str = "strategy") -> dict:
    """One report: strategy vs every benchmark, with alpha t-stats and DSR."""
    out = {"perf": {}, "vs": {}}
    p_s = perf(strategy, rf, label)
    out["perf"][label] = asdict(p_s)
    ms = monthly(strategy)
    rf_m = monthly(rf) if isinstance(rf, pd.Series) else rf
    for name, b in benchmarks.items():
        b = b.reindex(strategy.index).dropna()
        out["perf"][name] = asdict(perf(b, rf, name))
        ab = alpha_beta(ms, monthly(b), rf_m)
        ab["excess_total_pct"] = (
            out["perf"][label]["total_return_pct"] - out["perf"][name]["total_return_pct"])
        ab["excess_cagr_pct"] = (
            out["perf"][label]["cagr_pct"] - out["perf"][name]["cagr_pct"])
        out["vs"][name] = ab
    ex_m = ms - (rf_m.reindex(ms.index).fillna(0.0) if isinstance(rf_m, pd.Series) else rf_m)
    sr_m = ex_m.mean() / ex_m.std() if ex_m.std() > 0 else np.nan
    out["dsr"] = deflated_sharpe(sr_m, len(ms), trials,
                                 float(ms.skew()), float(ms.kurtosis()) + 3.0)
    out["trials"] = trials
    return out


def print_report(rep: dict) -> None:
    print(Perf.header())
    for name, d in rep["perf"].items():
        print(Perf(**d).row())
    print()
    print("{:<26} {:>10} {:>9} {:>7} {:>10} {:>10}".format(
        "vs benchmark", "alpha/yr", "alpha t", "beta", "excess tot", "excess CAGR"))
    for name, d in rep["vs"].items():
        print("{:<26} {:>9.2f}% {:>+9.2f} {:>7.2f} {:>+9.1f}% {:>+9.2f}%".format(
            name[:26], d["alpha_ann"] * 100, d["alpha_t"], d["beta"],
            d["excess_total_pct"], d["excess_cagr_pct"]))
    print(f"\nDeflated Sharpe (P[SR>0] after {rep['trials']} trials): {rep['dsr']:.3f}")
