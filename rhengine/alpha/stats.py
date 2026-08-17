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

from dataclasses import dataclass, asdict, field
from functools import lru_cache

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


def sharpe_se(sr: float, n: int, skew: float, kurt: float) -> float:
    """Asymptotic standard error of a Sharpe ratio estimate.

    Lo (2002) / Mertens (2002), the non-normal form:

        SE(SR) = sqrt( (1 - skew*SR + (kurt-1)/4 * SR^2) / (n-1) )

    `kurt` is **non-excess** kurtosis (normal = 3), which reduces to the
    familiar `sqrt((1 + SR^2/2)/n)` under normality. This was already computed
    inline inside `deflated_sharpe`; it is exposed here because **a Sharpe ratio
    quoted without its standard error is not a measurement**, and every Sharpe
    in this repo was quoted that way until 2026-08-17.
    """
    if n < 3 or not np.isfinite(sr):
        return float("nan")
    v = (1.0 - skew * sr + 0.25 * (kurt - 1.0) * sr ** 2) / (n - 1)
    return float(np.sqrt(v)) if v > 0 else float("nan")


def sharpe_ci(sr: float, n: int, skew: float, kurt: float,
              conf: float = 0.95, periods_per_year: float = 12.0
              ) -> tuple[float, float]:
    """Annualised confidence interval for a per-period Sharpe. Wide. Always."""
    se = sharpe_se(sr, n, skew, kurt)
    if not np.isfinite(se):
        return (float("nan"), float("nan"))
    z = sps.norm.ppf(0.5 + conf / 2.0)
    ann = np.sqrt(periods_per_year)
    return (float((sr - z * se) * ann), float((sr + z * se) * ann))


def autocorrelations(x: np.ndarray, lags: int) -> np.ndarray:
    """Sample autocorrelations rho_1..rho_`lags` of a return series."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = len(a)
    if n < 3 or lags < 1:
        return np.zeros(max(lags, 0))
    a = a - a.mean()
    denom = float(a @ a)
    if denom <= 0:
        return np.zeros(lags)
    return np.array([float(a[k:] @ a[:-k]) / denom
                     for k in range(1, min(lags, n - 1) + 1)])


def lo_eta(rhos: np.ndarray, q: int) -> float:
    """Lo (2002) time-aggregation factor for the Sharpe ratio.

        eta(q) = q / sqrt( q + 2 * sum_{k=1}^{q-1} (q-k) * rho_k )

    The universal `sqrt(q)` scaling is the **iid special case** (all rho = 0).
    Positive autocorrelation makes eta(q) < sqrt(q), so the conventional
    annualised Sharpe is **overstated** — the classic way a smoothed or
    trending return series flatters itself. Negative autocorrelation does the
    reverse.

    Applied here to MONTHLY returns with q=12 rather than daily with q=252, for
    the same reason `alpha_beta` regresses monthly: 251 sample autocorrelations
    estimated off daily data are mostly noise, and the repo's own FC-3 rule
    already says statistics come off a non-overlapping monthly series.
    """
    q = int(q)
    if q <= 1:
        return 1.0
    r = np.asarray(rhos, dtype=float)[: q - 1]
    ks = np.arange(1, len(r) + 1)
    denom = q + 2.0 * float(((q - ks) * r).sum())
    if denom <= 0:
        return float("nan")
    return float(q / np.sqrt(denom))


@lru_cache(maxsize=64)
def lo_eta_null_band(n: int, q: int = 12, conf: float = 0.95,
                     paths: int = 2000, seed: int = 1337) -> tuple[float, float]:
    """The range `lo_eta` returns on data that has NO autocorrelation at all.

    **This is the guard rail, and it is not optional.** `lo_eta(q=12)` needs 11
    sample autocorrelations; off ~193 monthly observations each one is mostly
    noise, and the weights `(q-k)` amplify them. Simulated on pure white noise
    at n=193 the estimator ranges (95%) from **2.82 to 4.78** around a true
    value of sqrt(12)=3.464 — a +-38% band. An eta inside that band carries no
    information, and multiplying a Sharpe by it manufactures precision.

    `perf()` therefore reports `sharpe_lo2002` **only when eta falls outside
    this band**, and NaN otherwise. Refusing to print a number is the correct
    behaviour when the number cannot be measured.

    Measured on this repo's own 193-month book returns, the four series land at
    eta = 5.30 (RMX-B), 5.12 (RMX-C), 4.96 (CW universe) and **4.74 (SPY, which
    is INSIDE the band)**. Same market, same window, same sign of effect — and
    the estimator calls it measurable for three of them and not the fourth.
    Treat that as the demonstration of why this correction should not be leaned
    on at this sample size, not as evidence that RMX-B differs from SPY.
    """
    rng = np.random.default_rng(seed)
    etas = np.array([lo_eta(autocorrelations(rng.standard_normal(n), q - 1), q)
                     for _ in range(paths)])
    etas = etas[np.isfinite(etas)]
    if len(etas) < 10:
        return (float("nan"), float("nan"))
    tail = (1.0 - conf) / 2.0 * 100.0
    return (float(np.percentile(etas, tail)),
            float(np.percentile(etas, 100.0 - tail)))


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
    se_sr = sharpe_se(sr, n, skew, kurt)
    if not np.isfinite(se_sr) or se_sr <= 0:
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
    # --- added 2026-08-17. Defaulted so dicts written before that date still
    # construct via Perf(**d). A Sharpe with no standard error is a point
    # estimate pretending to be a measurement; these are the honesty columns.
    #: Standard error of the MONTHLY Sharpe (Lo 2002 / Mertens non-normal form).
    sharpe_se: float = float("nan")
    #: 95% CI on the ANNUALISED Sharpe. Expect it to be embarrassingly wide.
    sharpe_ci_lo: float = float("nan")
    sharpe_ci_hi: float = float("nan")
    #: Lag-1 autocorrelation of monthly returns. Positive => sqrt(12) overstates.
    ac1: float = float("nan")
    #: Annualised Sharpe from monthly returns using the naive sqrt(12)...
    sharpe_monthly: float = float("nan")
    #: ...and using Lo (2002) eta(12), which prices the autocorrelation.
    #: **NaN when the adjustment is inside the white-noise band** — see
    #: `lo_eta_null_band`. An unmeasurable correction is not printed.
    sharpe_lo2002: float = float("nan")
    #: The raw eta(12) and the band white noise produces at this n, always
    #: reported together so the NaN above is interpretable.
    lo_eta: float = float("nan")
    lo_eta_null_lo: float = float("nan")
    lo_eta_null_hi: float = float("nan")

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

    # Monthly excess series — the non-overlapping basis FC-3 requires. Every
    # honesty column below is computed off this, never off daily returns.
    rf_m = (rf.reindex(r.index).fillna(0.0).resample("ME")
            .apply(lambda s: (1 + s).prod() - 1.0)) if isinstance(rf, pd.Series) \
        else pd.Series(0.0, index=m.index)
    ex_m = (m - rf_m.reindex(m.index).fillna(0.0)).dropna()
    n_m, sk_m, ku_m = len(ex_m), float(m.skew()) if len(m) > 3 else 0.0, \
        (float(m.kurtosis()) + 3.0) if len(m) > 3 else 3.0
    sr_m = float(ex_m.mean() / ex_m.std()) if n_m > 2 and ex_m.std() > 0 else float("nan")
    se_m = sharpe_se(sr_m, n_m, sk_m, ku_m)
    ci_lo, ci_hi = sharpe_ci(sr_m, n_m, sk_m, ku_m, periods_per_year=12.0)
    rhos = autocorrelations(ex_m.to_numpy(float), 11)
    eta = lo_eta(rhos, 12)
    # Only report the autocorrelation adjustment when it is distinguishable
    # from what white noise produces at this sample size. See lo_eta_null_band.
    band = lo_eta_null_band(n_m, 12) if n_m >= 24 else (float("nan"), float("nan"))
    eta_is_real = (np.isfinite(eta) and np.isfinite(band[0])
                   and not (band[0] <= eta <= band[1]))

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
        sharpe_se=se_m,
        sharpe_ci_lo=ci_lo,
        sharpe_ci_hi=ci_hi,
        ac1=float(rhos[0]) if len(rhos) else float("nan"),
        sharpe_monthly=sr_m * np.sqrt(12.0),
        sharpe_lo2002=(sr_m * eta) if eta_is_real else float("nan"),
        lo_eta=eta,
        lo_eta_null_lo=band[0],
        lo_eta_null_hi=band[1],
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
    print("{:<26} {:>8} {:>8} {:>20} {:>7}".format(
        "Sharpe, honestly", "sqrt(12)", "Lo2002", "95% CI (ann)", "AC(1)"))
    for name, d in rep["perf"].items():
        p = Perf(**d)
        print("{:<26} {:>8.2f} {:>8.2f} {:>9.2f} .. {:<7.2f} {:>+7.2f}".format(
            name[:26], p.sharpe_monthly, p.sharpe_lo2002,
            p.sharpe_ci_lo, p.sharpe_ci_hi, p.ac1))
    print()
    print("{:<26} {:>10} {:>9} {:>7} {:>10} {:>10}".format(
        "vs benchmark", "alpha/yr", "alpha t", "beta", "excess tot", "excess CAGR"))
    for name, d in rep["vs"].items():
        print("{:<26} {:>9.2f}% {:>+9.2f} {:>7.2f} {:>+9.1f}% {:>+9.2f}%".format(
            name[:26], d["alpha_ann"] * 100, d["alpha_t"], d["beta"],
            d["excess_total_pct"], d["excess_cagr_pct"]))
    print(f"\nDeflated Sharpe (P[SR>0] after {rep['trials']} trials): {rep['dsr']:.3f}")
