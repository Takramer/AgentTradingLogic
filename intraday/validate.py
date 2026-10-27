"""validate.py — what a day-trading rule must survive before anyone believes it.

Each check targets a known way a backtest lies about a trading rule:

| check            | catches                                                    |
|------------------|------------------------------------------------------------|
| trial logged     | an unrecorded search; the ledger makes `trials=` a count   |
| look-ahead       | signals that read bars after t (engine.causality_check)    |
| sample size      | < 250 sessions or < 100 trades: nothing can be concluded   |
| multiple testing | t-stat below the Sidak bar for every trial in `intraday`   |
| deflated Sharpe  | the best of N tries mistaken for skill                     |
| random entries   | an "edge" that is exit rules + drift; same trades, random times |
| cost stress      | an edge smaller than one more tick of slippage             |
| sub-periods      | one lucky stretch carrying the whole sample                |
| survivorship     | symbol lists chosen today; futures rolls booked as PnL     |
| walk-forward     | a parameter picked on the same data it is judged on        |
| plateau          | a best setting whose neighbours lose — a spike, not a ridge |
| holdout          | the final once-only look, logged before it runs            |

Every statistic uses **daily** net PnL with Newey-West errors, never trade
count — overlapping trades are not independent.

Verdicts: `CLEARS` only when every check passes; `INCOMPLETE` when none fails
but some did not run (the holdout always runs last, alone); otherwise
`DOES NOT CLEAR`. **CLEARS is permission to paper-trade, not to trade.**
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

# Shared statistics only: the trial-ledger maths and Sharpe/Newey-West helpers.
# No swing-trading rule, ledger or result is read from rhengine.
from rhengine import trials as T
from rhengine.alpha import stats as S
from .engine import (LookAheadError, Result, Strategy, _session_bounds, backtest,
                     causality_check)
from .instruments import Instrument
from .strategies import SessionDrift

FAMILY = "intraday"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LEDGER = os.path.join(DATA_DIR, "trials.jsonl")      # intraday's own; never rhengine's
HOLDOUT_LOG = os.path.join(DATA_DIR, "holdout.jsonl")
MIN_SESSIONS, MIN_TRADES = 250, 100
NW_LAGS = 5
SESSIONS_PER_YEAR = 252.0

SURVIVORSHIP = {
    "continuous_futures": (True, "n/a: one continuous series built from every expiry"),
    "single_instrument": (True, "n/a: one instrument listed for the whole window"),
    "pit_universe": (True, "symbols drawn from dated point-in-time membership"),
    "static_list": (False, "UNCHECKED: a list chosen today holds only survivors"),
}


class TrialNotLogged(RuntimeError):
    pass


class HoldoutBreach(RuntimeError):
    pass


# ------------------------------------------------------------ ledger plumbing
def is_logged(label: str | None, family: str = FAMILY, ledger: str = LEDGER) -> bool:
    return bool(label) and any(t.label == label and t.family == family and not t.resolves
                               for t in T.load(ledger))


def require_logged(label: str | None, family: str = FAMILY, ledger: str = LEDGER) -> None:
    if not is_logged(label, family, ledger):
        raise TrialNotLogged(
            f"trial {label!r} is not in the ledger. Log it BEFORE running "
            f"(intraday/CLAUDE.md):\n  python -m intraday log --label \"{label}\"")


def log_once(label: str, n: int, kind: str, chash: str, family: str = FAMILY,
             ledger: str = LEDGER, note: str = "") -> bool:
    """Append unless this exact label + config hash is already there, so
    re-running a notebook cell does not charge the same search twice."""
    if any(t.label == label and t.config_hash == chash for t in T.load(ledger)):
        return False
    T.append(T.Trial(family=family, label=label, kind=kind, n=n, config_hash=chash,
                     source="intraday/docs/GUIDE.md", note=note), ledger)
    return True


def guard_holdout(bars: pd.DataFrame, holdout_start) -> None:
    if holdout_start is None:
        return
    cut = pd.Timestamp(holdout_start)
    cut = cut.tz_localize(bars.index.tz) if cut.tzinfo is None else cut
    if bars.index.max() >= cut:
        raise HoldoutBreach(f"bars run to {bars.index.max()}, past the holdout "
                            f"start {cut}. Develop on bars before it only.")


def holdout_log_path(path: str | None = None) -> str:
    return path or HOLDOUT_LOG


# ------------------------------------------------------------------ statistics
def day_stats(daily: pd.Series, trials: int = 1) -> dict:
    """t-stat (Newey-West, 5 lags), annualised Sharpe with its 95% CI, and the
    deflated Sharpe at `trials`, all on daily net PnL."""
    x = daily.to_numpy(float)
    n = len(x)
    out = {"sessions": n, "net_usd": float(x.sum()), "mean_usd": float(x.mean()) if n else np.nan,
           "t_nw": np.nan, "sharpe": np.nan, "sharpe_ci": (np.nan, np.nan),
           "deflated_sharpe": np.nan, "trials": int(trials)}
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    if n < 20 or sd == 0.0:
        return out
    beta, se = S._newey_west_ols(x, np.ones((n, 1)), NW_LAGS)
    sr = x.mean() / sd
    sk = float(pd.Series(x).skew())
    ku = float(pd.Series(x).kurt()) + 3.0
    out.update(t_nw=float(beta[0] / se[0]) if se[0] > 0 else np.nan,
               sharpe=float(sr * np.sqrt(SESSIONS_PER_YEAR)),
               sharpe_ci=S.sharpe_ci(sr, n, sk, ku, periods_per_year=SESSIONS_PER_YEAR),
               deflated_sharpe=S.deflated_sharpe(sr, n, max(int(trials), 1), sk, ku,
                                                 periods_per_year=SESSIONS_PER_YEAR))
    return out


def subperiods(daily: pd.Series, k: int = 4) -> list[float]:
    """Net PnL of `k` consecutive, equal-length chunks of the sample."""
    return [float(c.sum()) for c in np.array_split(daily, k) if len(c)]


# ------------------------------------------------------------ the random null
def random_entry_null(result: Result, bars: pd.DataFrame, n_paths: int = 500,
                      mode: str = "any_day", seed: int = 0) -> dict:
    """Does the ENTRY beat luck? Re-run the same trades — same count, sides,
    holding times, stop/target distances and costs — at random entry bars, and
    see how often the random book earns as much as the real one.

    `any_day` randomises the session too (tests day selection and timing);
    `same_day` keeps each trade's session (tests intraday timing only).
    p = (1 + #null >= real) / (paths + 1). Null exits approximate the engine's:
    stops checked over the trade's holding window, else a time exit.
    """
    tr = result.trades
    if not len(tr):
        return {"p": np.nan, "real_usd": 0.0, "paths": 0, "mode": mode}
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    starts, ends, days = _session_bounds(bars)
    day_pos = {d: i for i, d in enumerate(days)}
    t_day = np.array([day_pos[d] for d in tr["day"]])
    side = tr["side"].to_numpy(float)
    hold = np.maximum(tr["hold"].to_numpy(int), 1)
    sd = tr["stop_dist"].to_numpy(float)
    td = tr["take_dist"].to_numpy(float)
    inst = result.instrument
    qs = tr["qty"].to_numpy(float) if "qty" in tr else np.full(len(tr), result.qty)
    ep = tr["entry_px"].to_numpy(float)
    sized = bool(result.meta.get("notional"))
    rng = np.random.default_rng(seed)
    real = float(tr["net"].sum())

    totals = np.empty(n_paths)
    for p in range(n_paths):
        k = t_day if mode == "same_day" else rng.integers(0, len(starts), len(tr))
        s, e = starts[k], ends[k]
        # Fit the whole holding time inside the session where it can fit, so a
        # random trade is not cut short at the close (real ones often ride to it).
        room = np.maximum(e - hold - s, 1)
        ent = s + 1 + np.minimum((rng.random(len(tr)) * room).astype(int), e - s - 1)
        pnl = 0.0
        for j in range(len(tr)):
            i0, last, sg = ent[j], e[j], side[j]
            px0 = o[i0]
            stop = px0 - sg * sd[j] if sd[j] == sd[j] else np.nan
            take = px0 + sg * td[j] if td[j] == td[j] else np.nan
            x_t = i0 + hold[j]
            hi = min(x_t - 1, last)
            exit_px = o[x_t] if x_t <= last else c[last]
            if stop == stop or take == take:
                w = slice(i0, hi + 1)
                if sg > 0:
                    js = np.flatnonzero(l[w] <= stop) if stop == stop else []
                    jt = np.flatnonzero(h[w] >= take) if take == take else []
                else:
                    js = np.flatnonzero(h[w] >= stop) if stop == stop else []
                    jt = np.flatnonzero(l[w] <= take) if take == take else []
                fs = js[0] if len(js) else 10**9
                ft = jt[0] if len(jt) else 10**9
                if fs <= ft and fs < 10**9:
                    b = i0 + fs
                    exit_px = min(o[b], stop) if sg > 0 else max(o[b], stop)
                elif ft < 10**9:
                    b = i0 + ft
                    exit_px = max(o[b], take) if sg > 0 else min(o[b], take)
            q = qs[j] * ep[j] / px0 if sized else qs[j]
            pnl += sg * (exit_px - px0) * inst.point_value * q - inst.trade_cost(q, px0, exit_px)
        totals[p] = pnl
    return {"real_usd": real, "null_mean_usd": float(totals.mean()),
            "null_p95_usd": float(np.percentile(totals, 95)),
            "p": float((1 + (totals >= real).sum()) / (n_paths + 1)),
            "paths": n_paths, "mode": mode}


# --------------------------------------------------- parameter search, honestly
@dataclass
class Sweep:
    keys: list
    grid: dict
    table: pd.DataFrame          # one row per combo: params, net, trades, t
    daily: pd.DataFrame          # sessions x combo-number, net USD
    pos: list                    # each combo's index into each grid list


def sweep(strategy_cls, grid: dict, bars: pd.DataFrame, instrument: Instrument, *,
          trial: str, ledger: str = LEDGER, qty: float = 1.0,
          capital: float = 10_000.0, holdout_start=None,
          notional: float | None = None, base: dict | None = None) -> Sweep:
    """Backtest every combination in `grid` and **charge the ledger for it**:
    one row of n = (combos - 1) under '<trial> :: grid', on top of the trial's
    own row. Re-running the same grid on the same data charges nothing new;
    widening it charges the difference as a new row.

    `base` holds the strategy's other settings; each cell is `base` with the
    grid's values on top. Without it every cell falls back to class defaults."""
    require_logged(trial, ledger=ledger)
    guard_holdout(bars, holdout_start)
    try:
        strategy_cls(**(base or {}))
    except TypeError as exc:
        raise ValueError(f"{strategy_cls.__name__} cannot be rebuilt from its params, "
                         f"so it cannot be swept ({exc}). Sweep the inner rule.") from exc
    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    if len(combos) > 1:
        chash = _grid_hash(strategy_cls, grid, bars, base)
        log_once(f"{trial} :: grid", len(combos) - 1, "threshold", chash, ledger=ledger,
                 note=f"{len(combos)} combos of {keys}")
    rows, cols = [], {}
    for n, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        res = backtest(strategy_cls(**{**(base or {}), **params}), bars, instrument, qty, capital, trial,
                       notional=notional)
        st = day_stats(res.daily)
        rows.append({**params, "net_usd": st["net_usd"], "trades": len(res.trades),
                     "t_nw": st["t_nw"]})
        cols[n] = res.daily
    pos = list(itertools.product(*(range(len(grid[k])) for k in keys)))
    return Sweep(keys, grid, pd.DataFrame(rows), pd.DataFrame(cols), pos)


def _grid_hash(strategy_cls, grid: dict, bars: pd.DataFrame,
               base: dict | None = None) -> str:
    d = {"s": getattr(strategy_cls, "name", str(strategy_cls)),
         "g": grid, "span": [str(bars.index[0]), str(bars.index[-1])]}
    try:
        defaults = strategy_cls().params
    except TypeError:
        defaults = None
    rest = {k: v for k, v in (base or {}).items() if k not in grid}
    if rest and (defaults is None or rest != {k: v for k, v in defaults.items()
                                              if k not in grid}):
        d["base"] = rest                   # default-built grids keep their old hash
    blob = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def plateau(sw: Sweep) -> float | None:
    """Share of the best combo's grid neighbours (one step on one parameter)
    that also made money. None when the grid has no neighbours."""
    if len(sw.table) < 2:
        return None
    pos = sw.pos
    best = int(sw.table["net_usd"].idxmax())
    nbrs = [i for i, p in enumerate(pos)
            if sum(abs(a - b) for a, b in zip(p, pos[best])) == 1]
    if not nbrs:
        return None
    return float((sw.table.loc[nbrs, "net_usd"] > 0).mean())


@dataclass
class WalkForward:
    folds: pd.DataFrame
    oos: pd.Series               # pooled out-of-sample daily net USD
    stability: float             # share of folds that picked the modal combo


def walk_forward(sw: Sweep, train: int = 250, test: int = 60) -> WalkForward:
    """Rolling: pick the best combo (by daily t) on `train` sessions, bank its
    next `test` sessions, slide forward by `test`. Only the pooled `oos` line
    is evidence; the in-sample winners are not."""
    D = sw.daily
    folds, oos = [], []
    i = train
    while i < len(D):
        tr, te = D.iloc[i - train:i], D.iloc[i:i + test]
        score = tr.mean() / (tr.std(ddof=1) / np.sqrt(len(tr))).replace(0, np.nan)
        best = int(score.fillna(-np.inf).idxmax())
        folds.append({"train_end": tr.index[-1].date(), "test_start": te.index[0].date(),
                      "test_end": te.index[-1].date(), "combo": best,
                      **sw.table.loc[best, sw.keys].to_dict(),
                      "train_t": float(score[best]), "test_net_usd": float(te[best].sum())})
        oos.append(te[best])
        i += test
    if not folds:
        raise ValueError(f"need more than {train} sessions for one fold; have {len(D)}")
    f = pd.DataFrame(folds)
    return WalkForward(f, pd.concat(oos), float(f["combo"].value_counts(normalize=True).iloc[0]))


# ------------------------------------------------------------------- holdout
def holdout(strategy: Strategy, bars: pd.DataFrame, instrument: Instrument, *,
            trial: str, holdout_start, ledger: str = LEDGER,
            look_log: str | None = None, qty: float = 1.0,
            capital: float = 10_000.0, notional: float | None = None) -> dict:
    """The one look at data the rule was never developed on. The look is
    written to `intraday/data/holdout.jsonl` BEFORE it runs, and the result is
    clean only if it is the first look ever logged there."""
    require_logged(trial, ledger=ledger)
    path = holdout_log_path(look_log)
    cut = pd.Timestamp(holdout_start)
    cut_tz = cut.tz_localize(bars.index.tz) if cut.tzinfo is None else cut
    if bars.index.max() < cut_tz:
        raise ValueError(f"bars end {bars.index.max()}, before the holdout start {cut_tz}: "
                         "nothing to look at, and the one look would be spent")
    res = backtest(strategy, bars, instrument, qty, capital, trial, notional=notional)
    look = {"date": date.today().isoformat(), "trial": trial,
            "config_hash": res.config_hash, "holdout_start": str(cut.date())}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(look, sort_keys=True) + "\n")
    with open(path, encoding="utf-8") as fh:
        looks = [json.loads(x) for x in fh if x.strip()]
    daily = res.daily[res.daily.index >= cut.tz_localize(None)]
    st = day_stats(daily, T.count(FAMILY, path=ledger))
    return {"stats": st, "daily": daily, "looks": len(looks),
            "clean": len(looks) == 1, "net_usd": st["net_usd"]}


# -------------------------------------------------------------------- verdict
def evaluate(strategy: Strategy, bars: pd.DataFrame, instrument: Instrument, *,
             trial: str | None, survivorship: str, grid: dict | None = None,
             ledger: str = LEDGER, qty: float = 1.0, capital: float = 10_000.0,
             n_paths: int = 500, seed: int = 0, holdout_start=None,
             holdout_result: dict | None = None, wf_train: int = 250,
             wf_test: int = 60, rho: float = 0.0,
             notional: float | None = None) -> dict:
    """Run every check on development data and return the report dict.

    `grid` (the sweep the parameters came from) enables walk-forward and the
    plateau check. `holdout_result` is the dict `holdout()` returned; without
    it the verdict can be INCOMPLETE at best — by design.
    """
    if survivorship not in SURVIVORSHIP:
        raise ValueError(f"survivorship must be one of {list(SURVIVORSHIP)}")
    guard_holdout(bars, holdout_start)
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "status": ("NOT RUN" if ok is None else
                                                 "PASS" if ok else "FAIL"),
                       "detail": detail})

    logged = is_logged(trial, ledger=ledger)
    # The sweep runs first so its grid is charged BEFORE the burden is computed.
    sw = None
    if grid and logged:
        sw = sweep(type(strategy), grid, bars, instrument, trial=trial, ledger=ledger,
                   qty=qty, capital=capital, holdout_start=holdout_start,
                   notional=notional, base=dict(strategy.params))
    n_trials = max(T.count(FAMILY, path=ledger), 1)
    add("trial logged", logged,
        f"{trial!r} in family {FAMILY!r} ({n_trials} trials)" if logged
        else f"{trial!r} not logged — every number below is uncharged")

    try:
        cuts = causality_check(strategy, bars, seed=seed)
        add("look-ahead", True, f"signals unchanged at {cuts} truncation points")
    except LookAheadError as exc:
        add("look-ahead", False, str(exc))

    res = backtest(strategy, bars, instrument, qty, capital, trial, notional=notional)
    st = day_stats(res.daily, n_trials)
    n_tr = len(res.trades)
    add("sample size", st["sessions"] >= MIN_SESSIONS and n_tr >= MIN_TRADES,
        f"{st['sessions']} sessions, {n_tr} trades (need {MIN_SESSIONS} / {MIN_TRADES})")

    v = T.verdict(st["t_nw"] if np.isfinite(st["t_nw"]) else 0.0, n_trials, rho=rho)
    add("multiple testing", bool(v["clears"] and st["t_nw"] > 0),
        f"t = {st['t_nw']:+.2f} vs required {v['required_t']:.2f} at {n_trials} "
        f"trials (rho {rho}); family-wise p = {v['family_wise_p']:.3f}")

    dsr = st["deflated_sharpe"]
    add("deflated Sharpe", bool(np.isfinite(dsr) and dsr >= 0.95),
        f"P(true Sharpe > 0) = {dsr:.3f} after {n_trials} trials (need 0.95)")

    nl = random_entry_null(res, bars, n_paths=n_paths, seed=seed)
    add("random entries", bool(np.isfinite(nl["p"]) and nl["p"] < 0.05),
        f"real ${nl['real_usd']:,.0f} vs random mean ${nl.get('null_mean_usd', 0):,.0f}"
        f", p = {nl['p']:.3f} over {nl['paths']} paths")

    hard = backtest(strategy, bars, instrument.stressed(2.0), qty, capital, trial,
                    notional=notional)
    hard_net = float(hard.daily.sum())
    add("cost stress", hard_net > 0, f"net ${hard_net:,.0f} at 2x slippage")

    parts = subperiods(res.daily)
    add("sub-periods", sum(p > 0 for p in parts) >= len(parts) - 1,
        "quarter nets: " + ", ".join(f"${p:,.0f}" for p in parts))

    ok, why = SURVIVORSHIP[survivorship]
    if survivorship == "continuous_futures" and "rolls" not in bars.attrs:
        why += ("; not built by data.continuous, so multi-day lookbacks may see raw "
                "roll gaps (flat overnight, so the gaps themselves book no PnL)")
    add("survivorship", ok, why)

    wf = None
    if sw is not None:
        try:
            wf = walk_forward(sw, wf_train, wf_test)
            ws = day_stats(wf.oos)
            add("walk-forward", bool(ws["net_usd"] > 0 and ws["t_nw"] >= 1.65),
                f"pooled OOS ${ws['net_usd']:,.0f}, t = {ws['t_nw']:+.2f} (need > 0 and "
                f">= 1.65) over {len(wf.folds)} folds; modal pick {wf.stability:.0%}")
        except ValueError as exc:
            add("walk-forward", None, str(exc))
        pl = plateau(sw)
        add("plateau", None if pl is None else pl >= 2 / 3,
            "no grid neighbours" if pl is None
            else f"{pl:.0%} of the best setting's neighbours made money (need 67%)")
    else:
        why = "no grid given" if not grid else "log the trial first"
        add("walk-forward", None, why)
        add("plateau", None, why)

    if holdout_result is None:
        add("holdout", None, "run validate.holdout() once, after everything above passes")
    else:
        h = holdout_result
        add("holdout", bool(h["clean"] and h["net_usd"] > 0),
            f"net ${h['net_usd']:,.0f}, t = {h['stats']['t_nw']:+.2f}; "
            f"{h['looks']} look(s) logged" + ("" if h["clean"] else " — NOT CLEAN"))

    drift = backtest(SessionDrift(), bars, instrument, qty, capital, notional=notional)
    corr = float(res.daily.corr(drift.daily)) if res.daily.std() > 0 else np.nan

    status = [c["status"] for c in checks]
    verdict = ("DOES NOT CLEAR" if "FAIL" in status else
               "INCOMPLETE" if "NOT RUN" in status else "CLEARS")
    return {"strategy": repr(strategy), "trial": trial, "verdict": verdict,
            "checks": checks, "summary": res.summary(), "stats": st, "null": nl,
            "drift": {"net_usd": float(drift.daily.sum()), "corr": corr},
            "result": res, "sweep": sw, "walk_forward": wf}


def print_report(rep: dict) -> None:
    s, st = rep["summary"], rep["stats"]
    print(f"{rep['strategy']}   trial: {rep['trial']}")
    print(f"  {s['trades']} trades over {s['sessions']} sessions | net ${s['net_usd']:,.0f}"
          f" (gross ${s['gross_usd']:,.0f}, costs ${s['costs_usd']:,.0f})")
    lo, hi = st["sharpe_ci"]
    print(f"  Sharpe {st['sharpe']:+.2f} [95% CI {lo:+.2f} to {hi:+.2f}] | "
          f"session-drift baseline net ${rep['drift']['net_usd']:,.0f}, "
          f"daily corr {rep['drift']['corr']:+.2f}\n")
    for c in rep["checks"]:
        print(f"  {c['status']:<8} {c['check']:<17} {c['detail']}")
    print(f"\n  VERDICT: {rep['verdict']}")
    if rep["verdict"] == "CLEARS":
        print("  CLEARS = permission to paper-trade, not to trade.")
