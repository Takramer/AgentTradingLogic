"""ff_random_null.py — does the frozen rank beat a random BIG quintile?

Two looks, both logged in research_data/trials.jsonl before main() was run.
Sample is 1963-07 through 2026-07. August 2026 is excluded and is not an input.

1. Random-quintile null (audit FC-7 on the published portfolios). Always holding
   BIG HiOP / BIG HiPRIOR / the 50/50 blend, versus 200 uniform draws of one of
   the five value-weight quintiles inside BIG. Quality redraws once per
   July–June cohort. Momentum redraws every month. Seed 11, separate stream
   per book. The same 20 bps turnover drag is charged on the ranked book and
   on every random book. Random books do not pay an extra switch cost, which
   favors the null. This look does not change a weight. A leg under the 80th
   percentile is reported and kept.

2. Panic-cut. Long-only translation of Daniel & Moskowitz (2016): no Table 5
   coefficients, no GARCH. Momentum weight goes to 0 only when the prior
   24-month market total return is negative AND the prior 6-month market
   variance exceeds the median of earlier variance readings (60 readings
   required). Otherwise the weight stays 50/50. Extra turnover is charged at
   20 bps. ADOPT only if every gate in `adopt_panic` passes.

    py scripts/ff_random_null.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ff_blend import COST_BPS, TURNOVER, alpha_t, ann, net_of_cost, read_french, sharpe  # noqa: E402
from rhengine.alpha.stats import sharpe_ci  # noqa: E402
from rhengine.trials import count, family_wise_p  # noqa: E402

SAMPLE_END = pd.Timestamp("2026-07-31")
PATHS = 200
SEED = 11
BAR = 80.0
RECENT = ("2016-01", "2026-07")

OP_BIG = ["BIG LoOP", "ME5 OP2", "ME5 OP3", "ME5 OP4", "BIG HiOP"]
PRIOR_BIG = ["BIG LoPRIOR", "ME5 PRIOR2", "ME5 PRIOR3", "ME5 PRIOR4", "BIG HiPRIOR"]
HI = 4


def july_june_cohort(dates: pd.DatetimeIndex) -> np.ndarray:
    """July through the following June share one quality draw."""
    year = dates.year.to_numpy()
    return np.where(dates.month >= 7, year, year - 1)


def quintile_picks(dates: pd.DatetimeIndex, mode: str, n: int, paths: int,
                   seed: int) -> np.ndarray:
    """Integer column picks, shape (paths, n_dates)."""
    rng = np.random.default_rng(seed)
    out = np.empty((paths, len(dates)), dtype=int)
    if mode == "month":
        for k in range(paths):
            out[k] = rng.integers(0, n, size=len(dates))
        return out
    if mode == "july_june":
        cohort = july_june_cohort(dates)
        order = pd.unique(cohort)
        for k in range(paths):
            drawn = {c: int(rng.integers(0, n)) for c in order}
            out[k] = [drawn[c] for c in cohort]
        return out
    raise ValueError(mode)


def held_returns(ports: pd.DataFrame, picks: np.ndarray) -> np.ndarray:
    arr = ports.to_numpy(float)
    t = np.arange(arr.shape[0])
    return arr[t, picks]


def terminal_wealth(r: np.ndarray) -> np.ndarray:
    return np.prod(1.0 + r, axis=-1) - 1.0


def percentile(actual: float, finals: np.ndarray) -> float:
    """Share of random terminal wealths strictly below the ranked book."""
    return float((finals < actual).mean() * 100.0)


def panic_state(mkt: pd.Series, var_window: int = 6, bear_window: int = 24,
                min_history: int = 60) -> pd.Series:
    """True only when both the bear and the high-variance conditions are known.

    Both inputs use returns strictly before the month they apply to. The
    variance threshold is the median of variance readings strictly before the
    current one, and it stays inactive until `min_history` of those exist.
    """
    log_r = np.log1p(mkt.astype(float))
    bear = np.exp(log_r.rolling(bear_window).sum().shift(1)) < 1.0
    var = mkt.astype(float).rolling(var_window).var(ddof=0).shift(1)
    thresh = var.shift(1).expanding(min_periods=min_history).median()
    return (bear & (var > thresh)).fillna(False)


def momentum_weight(panic: pd.Series) -> pd.Series:
    return pd.Series(np.where(panic.to_numpy(), 0.0, 0.5), index=panic.index,
                     dtype=float)


def apply_weight(q: pd.Series, m: pd.Series, w_m: pd.Series) -> pd.Series:
    """Blend, then charge |Δw_m| at the French round-trip cost."""
    w = w_m.reindex(q.index)
    blended = (1.0 - w) * q + w * m
    turn = w.diff().abs().fillna(0.0) * (COST_BPS / 1e4)
    return blended - turn


def _ci(r: pd.Series, rf: pd.Series) -> tuple[float, float]:
    df = pd.concat({"r": r, "f": rf}, axis=1).dropna()
    ex = df["r"] - df["f"]
    sr = float(ex.mean() / ex.std())
    return sharpe_ci(sr, len(ex), float(ex.skew()), float(ex.kurtosis()) + 3.0)


def book_null(ports: pd.DataFrame, hi: int, mode: str, drag: float,
              paths: int = PATHS, seed: int = SEED) -> dict:
    ranked = ports.iloc[:, hi] - drag
    picks = quintile_picks(ports.index, mode, ports.shape[1], paths, seed)
    paths_r = held_returns(ports, picks) - drag
    actual = float(terminal_wealth(ranked.to_numpy(float)))
    finals = terminal_wealth(paths_r)
    t = len(ranked)
    def _cagr(w):
        return float((1.0 + w) ** (12.0 / t) - 1.0) * 100.0
    return {
        "percentile": percentile(actual, finals),
        "actual_cagr": _cagr(actual),
        "random_cagr_p50": _cagr(float(np.median(finals))),
        "n": t,
    }


def adopt_panic(scaled_t: float, base_t: float, worst_s: float, worst_b: float,
                ex_s: float, ex_b: float, fw_p: float) -> bool:
    return bool(
        scaled_t >= 2.0
        and scaled_t > base_t
        and worst_s > worst_b
        and abs(ex_s - ex_b) <= 1.0
        and fw_p <= 0.05
    )


def _excess(r: pd.Series, mkt: pd.Series) -> float:
    x = r.loc[RECENT[0]:RECENT[1]].dropna()
    mk = mkt.reindex(x.index).dropna()
    x = x.reindex(mk.index)
    return ann(x) - ann(mk)


def main() -> None:
    ff5 = read_french("ff5_monthly.csv")
    ff5.columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"][:ff5.shape[1]]
    rf = ff5["RF"].loc[:SAMPLE_END]
    mkt = (ff5["Mkt-RF"] + ff5["RF"]).loc[:SAMPLE_END]
    op = read_french("port_me_op_5x5.csv", block=0).loc[:SAMPLE_END]
    pr = read_french("port_me_prior_5x5.csv", block=0).loc[:SAMPLE_END]
    op_big = op[OP_BIG].dropna(how="any")
    pr_big = pr[PRIOR_BIG].dropna(how="any")
    idx = op_big.index.intersection(pr_big.index).intersection(mkt.dropna().index)
    idx = idx[idx <= SAMPLE_END]
    op_big, pr_big = op_big.reindex(idx), pr_big.reindex(idx)
    mkt, rf = mkt.reindex(idx), rf.reindex(idx)
    if idx.max() > SAMPLE_END:
        raise RuntimeError("sample leaked past 2026-07")

    q = net_of_cost(op_big["BIG HiOP"], "quality")
    m = net_of_cost(pr_big["BIG HiPRIOR"], "momentum")
    base = 0.5 * q + 0.5 * m
    panic = panic_state(mkt)
    w_m = momentum_weight(panic)
    scaled = apply_weight(q, m, w_m).dropna()

    print(f"SAMPLE  {idx.min().date()} → {idx.max().date()}   n={len(idx)}"
          f"   panic months {int(panic.sum())}")

    drag_q = TURNOVER["quality"] * COST_BPS / 1e4 / 12.0
    drag_m = TURNOVER["momentum"] * COST_BPS / 1e4 / 12.0
    q_null = book_null(op_big, HI, "july_june", drag_q)
    m_null = book_null(pr_big, HI, "month", drag_m)

    # Blend null: independent streams, same seeds as the single-book nulls,
    # so the quality draws match `q_null` and the momentum draws match `m_null`.
    q_picks = quintile_picks(idx, "july_june", 5, PATHS, SEED)
    m_picks = quintile_picks(idx, "month", 5, PATHS, SEED)
    blend_paths = 0.5 * (held_returns(op_big, q_picks) - drag_q) + 0.5 * (
        held_returns(pr_big, m_picks) - drag_m)
    blend_finals = terminal_wealth(blend_paths)
    blend_actual = float(terminal_wealth(base.to_numpy(float)))
    blend_pct = percentile(blend_actual, blend_finals)
    t_len = len(base)
    def _cagr(w):
        return float((1.0 + w) ** (12.0 / t_len) - 1.0) * 100.0

    print("\nRANDOM QUINTILE  (report only; weights stay 50/50)")
    print(f"  bar: terminal wealth >= {BAR:.0f}th percentile of {PATHS} draws, seed {SEED}")
    for name, res in (("quality  BIG HiOP", q_null), ("momentum BIG HiPRIOR", m_null)):
        flag = "CLEARS" if res["percentile"] >= BAR else "BELOW BAR"
        print(f"  {name:22} {res['percentile']:6.1f}th   "
              f"CAGR {res['actual_cagr']:.2f}%   "
              f"random median {res['random_cagr_p50']:.2f}%   {flag}")
    flag = "CLEARS" if blend_pct >= BAR else "BELOW BAR"
    print(f"  {'50/50 blend':22} {blend_pct:6.1f}th   "
          f"CAGR {_cagr(blend_actual):.2f}%   "
          f"random median {_cagr(float(np.median(blend_finals))):.2f}%   {flag}")

    base_al, base_t, _ = alpha_t(base, mkt, rf)
    sc_al, sc_t, _ = alpha_t(scaled, mkt.reindex(scaled.index), rf.reindex(scaled.index))
    worst_b, worst_s = float(base.min() * 100), float(scaled.min() * 100)
    ex_b, ex_s = _excess(base, mkt), _excess(scaled, mkt)
    n_trials = count("rmx")
    fw = family_wise_p(sc_t, n_trials)
    gates = {
        "t>=2": sc_t >= 2.0,
        "t>unscaled": sc_t > base_t,
        "worst month improved": worst_s > worst_b,
        "2016-2026 excess within 1pp": abs(ex_s - ex_b) <= 1.0,
        "family-wise p<=0.05": fw <= 0.05,
    }
    adopt = adopt_panic(sc_t, base_t, worst_s, worst_b, ex_s, ex_b, fw)
    print("\nPANIC-CUT  (adopt only if every gate passes)")
    print(f"  alpha t     unscaled {base_t:+.2f}   panic-cut {sc_t:+.2f}"
          f"   alpha/yr {base_al:+.2f}% → {sc_al:+.2f}%")
    print(f"  worst month unscaled {worst_b:+.2f}%   panic-cut {worst_s:+.2f}%")
    print(f"  2016-01..2026-07 excess unscaled {ex_b:+.2f}pp   panic-cut {ex_s:+.2f}pp")
    base_fw = family_wise_p(base_t, n_trials)
    print(f"  family-wise p, {n_trials} rmx trials: "
          f"unscaled {base_fw:.3f}   panic-cut {fw:.3f}")
    for name, ok in gates.items():
        print(f"    {name:32} {'pass' if ok else 'fail'}")
    print(f"  VERDICT: {'ADOPT' if adopt else 'LEAVE OFF'}")

    print("\nSHARPE 95% CI, annualised, excess over RF, through 2026-07")
    for name, series in (("unscaled blend", base),
                         ("panic-cut", scaled),
                         ("market", mkt)):
        lo, hi = _ci(series, rf)
        print(f"  {name:16} {sharpe(series, rf):.2f}   CI {lo:+.2f} .. {hi:+.2f}")


if __name__ == "__main__":
    main()
