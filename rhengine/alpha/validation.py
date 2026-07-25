"""validation.py — the tests that decide whether RMX is allowed to exist.

Each function here maps to a gate in docs/RMX-PREREGISTRATION.md §5, which in
turn maps to audit §10 G1. They are deliberately harsh:

* `random_selection_null` is the audit's FC-7 finding turned into a gate. The
  old scoring rule sat at the **53rd percentile** of random selection from its
  own candidate pool; a ranking that cannot beat a coin flip is not a ranking.
* `block_bootstrap` resamples **days in blocks from the book's own return
  series**, not trades IID from an untradeable signal population (FC-10). IID
  resampling destroys the loss clustering that produces real drawdowns.
* `walk_forward` reports each fold against SPY over the same fold, because a
  fold that "made money" in 2013 tells you nothing on its own.
* `cost_curve` exists because an 11 bps assumption was the difference between
  a profitable and an unprofitable system last time (FC-2).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .book import buy_and_hold, run_book
from .stats import alpha_beta, drawdown, monthly, perf


# ------------------------------------------------------------------ cost curve
def cost_curve(panel, targets, exposure, spy_daily, bps_list=(0, 5, 10, 20, 50, 100),
               start=None, end=None) -> pd.DataFrame:
    """Net return vs SPY across a grid of round-trip cost assumptions."""
    rows = []
    spy_tot = float((1 + spy_daily).prod() - 1.0) * 100.0
    for bps in bps_list:
        bk = run_book(panel.adj, targets, panel.rf, float(bps),
                      start=start, end=end, exposure=exposure)
        p = perf(bk.daily_return, panel.rf, f"{bps}bps")
        rows.append({
            "bps": bps,
            "total_pct": p.total_return_pct,
            "cagr_pct": p.cagr_pct,
            "sharpe": p.sharpe,
            "max_dd_pct": p.max_dd_pct,
            "vs_spy_pct": p.total_return_pct - spy_tot,
            "cost_drag_pct": bk.total_cost_pct,
        })
    return pd.DataFrame(rows)


def breakeven_bps(curve: pd.DataFrame) -> float:
    """Round-trip bps at which the book stops beating SPY (linear interp)."""
    c = curve.sort_values("bps")
    v = c["vs_spy_pct"].to_numpy(float)
    b = c["bps"].to_numpy(float)
    for i in range(1, len(v)):
        if v[i - 1] > 0 >= v[i]:
            return float(b[i - 1] + (b[i] - b[i - 1]) * v[i - 1] / (v[i - 1] - v[i]))
    return float("inf") if v[-1] > 0 else float("-inf")


# ------------------------------------------------------------- walk-forward
def walk_forward(book_ret: pd.Series, spy_ret: pd.Series, folds: int = 5) -> pd.DataFrame:
    """Contiguous time folds, each scored against SPY over the same dates.

    No purge/embargo is needed here for the reason the audit's FC-12 named:
    there is no fitting inside a fold. The parameters come from published
    literature and are identical in every fold, so a fold is a pure
    out-of-period observation, not a train/test split.
    """
    idx = book_ret.index
    edges = np.linspace(0, len(idx), folds + 1).astype(int)
    rows = []
    for k in range(folds):
        sl = slice(edges[k], edges[k + 1])
        b, s = book_ret.iloc[sl], spy_ret.iloc[sl]
        if len(b) < 40:
            continue
        tb = float((1 + b).prod() - 1.0) * 100
        ts = float((1 + s).prod() - 1.0) * 100
        rows.append({
            "fold": k + 1,
            "start": str(idx[edges[k]].date()),
            "end": str(idx[edges[k + 1] - 1].date()),
            "strategy_pct": tb, "spy_pct": ts, "excess_pct": tb - ts,
            "max_dd_pct": float(drawdown((1 + b).cumprod()).min()) * 100,
            "spy_dd_pct": float(drawdown((1 + s).cumprod()).min()) * 100,
            "beat": tb > ts,
        })
    return pd.DataFrame(rows)


def calendar_years(book_ret: pd.Series, spy_ret: pd.Series) -> pd.DataFrame:
    """Year-by-year table. The single most falsifiable view of a long book."""
    rows = []
    for y, b in book_ret.groupby(book_ret.index.year):
        s = spy_ret[spy_ret.index.year == y]
        tb = float((1 + b).prod() - 1.0) * 100
        ts = float((1 + s).prod() - 1.0) * 100
        rows.append({"year": int(y), "strategy_pct": tb, "spy_pct": ts,
                     "excess_pct": tb - ts,
                     "max_dd_pct": float(drawdown((1 + b).cumprod()).min()) * 100})
    return pd.DataFrame(rows)


# ------------------------------------------------------------ block bootstrap
def block_bootstrap(book_ret: pd.Series, spy_ret: pd.Series, paths: int = 2000,
                    mean_block: int = 21, seed: int = 7) -> dict:
    """Stationary block bootstrap of the PAIRED (strategy, SPY) daily series.

    Blocks are geometric with mean length `mean_block` (Politis & Romano 1994,
    https://doi.org/10.1080/01621459.1994.10476870). Strategy and benchmark are
    resampled with the SAME block indices, so P(underperform SPY) is measured
    against a market path that actually co-moves with the strategy path -
    resampling them independently would compare the book to a market that never
    happened.
    """
    rng = np.random.default_rng(seed)
    b = book_ret.to_numpy(float)
    s = spy_ret.reindex(book_ret.index).fillna(0.0).to_numpy(float)
    n = len(b)
    p = 1.0 / mean_block

    fin_b = np.empty(paths)
    fin_s = np.empty(paths)
    dd = np.empty(paths)
    for k in range(paths):
        idx = np.empty(n, dtype=np.int64)
        i = 0
        while i < n:
            start = rng.integers(0, n)
            L = min(rng.geometric(p), n - i)
            take = (start + np.arange(L)) % n
            idx[i:i + L] = take
            i += L
        pb, ps = b[idx], s[idx]
        eq = np.cumprod(1.0 + pb)
        fin_b[k] = eq[-1] - 1.0
        fin_s[k] = np.prod(1.0 + ps) - 1.0
        dd[k] = (eq / np.maximum.accumulate(eq) - 1.0).min()

    q = [5, 25, 50, 75, 95]
    dd_mag = -dd * 100.0          # reported as POSITIVE magnitudes: bigger = worse
    return {
        "paths": paths, "mean_block": mean_block,
        "terminal_pct": {f"p{x}": float(np.percentile(fin_b, x) * 100) for x in q},
        "spy_terminal_pct": {f"p{x}": float(np.percentile(fin_s, x) * 100) for x in q},
        "max_dd_mag_pct": {f"p{x}": float(np.percentile(dd_mag, x)) for x in (50, 75, 95, 99)},
        "p_loss": float((fin_b < 0).mean()),
        "p_underperform_spy": float((fin_b < fin_s).mean()),
        "realised_max_dd_mag_pct": float(-drawdown((1 + book_ret).cumprod()).min() * 100),
    }


# --------------------------------------------------------- random-entry null
def random_selection_null(panel, eligible: dict, n_positions: int, rf, cost_bps: float,
                          book_ret: pd.Series, exposure=None, paths: int = 200,
                          seed: int = 11, start=None, end=None) -> dict:
    """Does the RANKING add anything over drawing N names from the same pool?

    This is audit FC-7 as a gate. The old system's score-ranked portfolio landed
    at the 53rd percentile of 200 random draws - i.e. the ranking was noise and
    the entire result was which names happened to be picked.
    """
    rng = np.random.default_rng(seed)
    finals = np.empty(paths)
    for k in range(paths):
        targets = {}
        for d, names in eligible.items():
            if len(names) < n_positions:
                continue
            pick = rng.choice(np.asarray(names), size=n_positions, replace=False)
            targets[d] = pd.Series(1.0 / n_positions, index=pd.Index(pick))
        bk = run_book(panel.adj, targets, rf, cost_bps, start=start, end=end,
                      exposure=exposure)
        finals[k] = float((1 + bk.daily_return).prod() - 1.0)
    actual = float((1 + book_ret).prod() - 1.0)
    return {
        "paths": paths,
        "actual_pct": actual * 100,
        "percentile": float((finals < actual).mean() * 100),
        "random_pct": {f"p{x}": float(np.percentile(finals, x) * 100)
                       for x in (5, 25, 50, 75, 95)},
    }


# -------------------------------------------------------- random-timing null
def random_timing_null(adj: pd.DataFrame, sched: pd.DataFrame, rf: pd.Series,
                       cost_bps: float, actual_ret: pd.Series, safe_col: str | None,
                       paths: int = 500, seed: int = 23) -> dict:
    """Does the trend rule beat a coin flip that is invested equally often?

    The timing analogue of audit FC-7. The old system's stock ranking sat at the
    53rd percentile of random selection; the same question has to be asked of a
    market-timing rule, because "invested 75% of months during a 45-year bull
    market" is by itself most of the return. Each path permutes the monthly
    risk-on flags, preserving how many months are invested and destroying only
    *when* - so the null holds exposure constant and tests timing alone.
    """
    rng = np.random.default_rng(seed)
    flags = (sched["equity"] > 0).to_numpy()
    safe_flags = (sched["safe"] > 0).to_numpy() if "safe" in sched else np.zeros_like(flags)
    start, end = sched.index[0], adj.index[-1]

    finals = np.empty(paths)
    for k in range(paths):
        perm = rng.permutation(len(flags))
        eq, sf = flags[perm], safe_flags[perm]
        targets = {}
        for j, d in enumerate(sched.index):
            if eq[j]:
                targets[d] = pd.Series({"EQ": 1.0})
            elif sf[j] and safe_col:
                targets[d] = pd.Series({safe_col: 1.0})
            else:
                targets[d] = pd.Series(dtype=float)
        bk = run_book(adj, targets, rf, cost_bps, start=start, end=end)
        finals[k] = float((1 + bk.daily_return).prod() - 1.0)

    actual = float((1 + actual_ret).prod() - 1.0)
    return {
        "paths": paths,
        "actual_pct": actual * 100,
        "percentile": float((finals < actual).mean() * 100),
        "random_pct": {f"p{x}": float(np.percentile(finals, x) * 100)
                       for x in (5, 25, 50, 75, 95)},
    }


def bear_episodes(book_ret: pd.Series, bench_ret: pd.Series,
                  threshold: float = -0.20) -> pd.DataFrame:
    """Benchmark drawdown episodes deeper than `threshold`, scored side by side.

    Regime claims in this repo have previously rested on a single 20-day
    episode. Counting episodes - not days - is the audit's §5 requirement.
    """
    eq = (1 + bench_ret).cumprod()
    dd = eq / eq.cummax() - 1.0
    rows, in_ep, lo_i = [], False, None
    peak_i = 0
    for i in range(len(dd)):
        if dd.iloc[i] == 0:
            if in_ep:
                rows.append((peak_i, i))
                in_ep = False
            peak_i = i
        elif dd.iloc[i] <= threshold:
            in_ep = True
    if in_ep:
        rows.append((peak_i, len(dd) - 1))

    out = []
    for a, b in rows:
        s, m = book_ret.iloc[a:b + 1], bench_ret.iloc[a:b + 1]
        out.append({
            "start": str(book_ret.index[a].date()),
            "end": str(book_ret.index[b].date()),
            "months": round((b - a) / 21.0),
            "benchmark_pct": float((1 + m).prod() - 1.0) * 100,
            "strategy_pct": float((1 + s).prod() - 1.0) * 100,
            "bench_dd_pct": float(drawdown((1 + m).cumprod()).min()) * 100,
            "strat_dd_pct": float(drawdown((1 + s).cumprod()).min()) * 100,
        })
    return pd.DataFrame(out)


# ------------------------------------------------------------ concentration
def drop_best(book_ret: pd.Series, months_dropped: int = 3) -> dict:
    """Audit FC-5: does the edge survive deleting its best months?

    The old book turned negative after removing its top 2% of trades, and ten
    sessions carried 107% of it. A strategy whose result is a handful of months
    is a story about those months.
    """
    m = monthly(book_ret)
    out = {"n_months": len(m)}
    for k in (0, 1, 3, 6):
        keep = m.sort_values(ascending=False).iloc[k:]
        out[f"drop_best_{k}"] = float((1 + keep).prod() - 1.0) * 100
    out["neg_months_pct"] = float((m < 0).mean() * 100)
    return out
