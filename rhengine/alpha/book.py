"""book.py — the mark-to-market portfolio book.

This is the ONLY place performance numbers come from (audit FC-1: the old stack
summed R-multiples over 114,964 signals of which a real account could take 230).
Here a weight vector is a claim on capital: weights sum to at most 1, the
remainder earns the risk-free rate, and equity is marked every session against
that day's closes rather than only when a position exits (audit FC-13).

Conventions, all deliberately pessimistic:

* **Execution lag.** Weights decided from data through session `D` are applied
  from the close of `D+1`. The strategy never touches the bar it was computed on.
* **Cost.** `one_way_turnover * round_trip_bps`, charged on the execution day.
  One-way turnover is `0.5 * sum|w_new - w_drifted|`; a name rotated fully out
  and another fully in therefore pays one complete round trip between them.
  `round_trip_bps` may instead be a **per-name Series** (`costs.bps_for_caps`),
  in which case every name pays its own spread on its own traded notional and
  the scalar case is recovered exactly. A cap-weighted S&P 500 book is almost
  entirely mega-cap and costs a few bps, not the legacy scan universe's 30.
* **Delisting.** A held name whose price series ends while it is still an index
  member is booked at **-30%** on its last session and moved to cash (Shumway
  1997, https://doi.org/10.1111/j.1540-6261.1997.tb03818.x). Silently dropping
  it is the classic way a survivorship-limited dataset flatters a backtest.
* **Drift.** Between rebalances weights drift with prices (buy-and-hold), so
  turnover is the real turnover, not an artefact of forced daily re-weighting.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DELIST_RETURN = -0.30


@dataclass
class BookResult:
    daily_return: pd.Series      # net of cost, mark-to-market
    gross_return: pd.Series      # before cost
    equity: pd.Series
    exposure: pd.Series          # invested fraction, daily
    turnover: pd.Series          # one-way, on rebalance days
    cost_drag: pd.Series         # daily cost in return terms
    holdings: pd.DataFrame       # weights, daily
    n_positions: pd.Series

    @property
    def annual_turnover(self) -> float:
        yrs = len(self.daily_return) / 252.0
        return float(self.turnover.sum() / yrs) if yrs > 0 else float("nan")

    @property
    def total_cost_pct(self) -> float:
        return float(self.cost_drag.sum() * 100.0)

    @property
    def effective_bps(self) -> float:
        """Round-trip bps the book actually paid, turnover-weighted.

        Equals the scalar it was given when cost was a scalar. With a per-name
        cost vector it is the number to quote — and it is turnover-weighted,
        not weight-weighted, so a book that churns its cheap names and holds
        its dear ones reports honestly.
        """
        t = float(self.turnover.sum())
        return float(self.cost_drag.sum() / t * 1e4) if t > 0 else float("nan")


def _delist_map(adj: pd.DataFrame) -> pd.Series:
    """Last session on which each ticker has a price."""
    return adj.apply(lambda s: s.last_valid_index())


def run_book(adj: pd.DataFrame,
             targets: dict[pd.Timestamp, pd.Series],
             rf: pd.Series,
             round_trip_bps: float | pd.Series,
             start: pd.Timestamp | None = None,
             end: pd.Timestamp | None = None,
             exposure: dict[pd.Timestamp, float] | None = None,
             max_gross: float = 1.0,
             borrow_spread_bps: float = 200.0) -> BookResult:
    """Simulate a weight-target book with daily mark-to-market.

    `targets[D]` is the weight vector DECIDED at session D (indexed by ticker).
    It becomes effective at the close of the next session. `exposure[D]`
    optionally scales the whole book (the vol-target / trend-gate legs).

    Un-invested capital earns `rf`. Gross exposure above 1.0 is permitted only
    up to `max_gross`, and the borrowed portion is charged `rf +
    borrow_spread_bps` - the default 200 bps is a retail margin rate, not an
    institutional one, because a backtest that borrows at the T-bill rate is
    describing an account nobody has.
    """
    idx = adj.index
    if start is not None:
        idx = idx[idx >= start]
    if end is not None:
        idx = idx[idx <= end]
    if len(idx) < 2:
        raise ValueError("book window too short")

    cols = adj.columns
    px = adj.loc[idx]
    ret = px.pct_change()
    last_seen = _delist_map(adj)

    # A name that stops printing prices while held is a delisting, not a gap.
    is_last = pd.DataFrame(False, index=idx, columns=cols)
    for tk, d in last_seen.items():
        if d is not None and d in is_last.index and d < adj.index[-1]:
            is_last.loc[d, tk] = True

    # Effective date for each decision date: the NEXT session.
    pos = {d: i for i, d in enumerate(idx)}
    effective: dict[pd.Timestamp, pd.Series] = {}
    eff_exposure: dict[pd.Timestamp, float] = {}
    for d, w in targets.items():
        i = pos.get(d)
        if i is None or i + 1 >= len(idx):
            continue
        effective[idx[i + 1]] = w
        if exposure is not None:
            eff_exposure[idx[i + 1]] = float(exposure.get(d, 1.0))

    ret_np = ret.to_numpy(dtype=float)
    last_np = is_last.to_numpy()
    col_pos = {c: j for j, c in enumerate(cols)}

    # Cost may be a scalar (the historical behaviour, bit-for-bit unchanged) or
    # a per-name Series of round-trip bps — see costs.bps_for_caps. Per name is
    # the honest model: a cap-weighted S&P 500 book trades mega-caps at a few
    # bps, and charging it the legacy scan universe's flat 30 overstates cost by
    # an order of magnitude. A name missing from the Series is charged the
    # series' own MAXIMUM, never its mean: an unpriced name is an illiquid name
    # until shown otherwise, and the alternative rounds silently in the book's
    # favour.
    if isinstance(round_trip_bps, pd.Series):
        fill = float(round_trip_bps.max()) if len(round_trip_bps) else 0.0
        bps_np = round_trip_bps.reindex(cols).fillna(fill).to_numpy(dtype=float)
    else:
        bps_np = np.full(len(cols), float(round_trip_bps))
    rf_np = rf.reindex(idx).fillna(0.0).to_numpy(dtype=float)
    borrow_daily = borrow_spread_bps / 1e4 / 252.0

    n = len(idx)
    w = np.zeros(len(cols))
    gross = np.zeros(n)
    net = np.zeros(n)
    costs = np.zeros(n)
    turn = np.zeros(n)
    expo = np.zeros(n)
    npos = np.zeros(n, dtype=int)
    hold = np.zeros((n, len(cols)))

    for t in range(n):
        r = ret_np[t].copy()
        alive = np.isfinite(r)
        r[~alive] = 0.0
        # delisted-while-held: book the delisting return, then force to cash
        dead = last_np[t] & (w > 0)
        r[dead] = DELIST_RETURN

        invested = w.sum()
        # cash earns rf; borrowed capital pays rf + the retail margin spread
        cash_leg = (1.0 - invested) * rf_np[t]
        if invested > 1.0:
            cash_leg -= (invested - 1.0) * borrow_daily
        day = float(w @ r) + cash_leg
        gross[t] = day
        # drift weights with realised prices, then renormalise onto the new NAV
        nav = 1.0 + day
        w = (w * (1.0 + r)) / nav if nav > 0 else np.zeros_like(w)
        w[dead] = 0.0
        # a name with no price at all cannot be held
        stale = ~np.isfinite(px.to_numpy(dtype=float)[t])
        w[stale] = 0.0

        d = idx[t]
        if d in effective:
            tgt = np.zeros(len(cols))
            for tk, wt in effective[d].items():
                j = col_pos.get(tk)
                if j is not None and np.isfinite(wt):
                    tgt[j] = float(wt)
            s = tgt.sum()
            if s > max_gross:
                tgt = tgt / s * max_gross
            tgt *= eff_exposure.get(d, 1.0) if exposure is not None else 1.0
            s2 = tgt.sum()
            if s2 > max_gross:
                tgt = tgt / s2 * max_gross
            traded = np.abs(tgt - w)
            one_way = 0.5 * float(traded.sum())
            # Each name pays its OWN spread on its OWN traded notional. With a
            # scalar this is algebraically identical to one_way * bps / 1e4.
            c = 0.5 * float((traded * bps_np).sum()) / 1e4
            turn[t] = one_way
            costs[t] = c
            w = tgt
            gross[t] = day
            day = (1.0 + day) * (1.0 - c) - 1.0

        net[t] = day
        expo[t] = w.sum()
        npos[t] = int((w > 1e-9).sum())
        hold[t] = w

    daily = pd.Series(net, index=idx, name="net")
    return BookResult(
        daily_return=daily,
        gross_return=pd.Series(gross, index=idx, name="gross"),
        equity=(1.0 + daily).cumprod(),
        exposure=pd.Series(expo, index=idx, name="exposure"),
        turnover=pd.Series(turn, index=idx, name="turnover"),
        cost_drag=pd.Series(costs, index=idx, name="cost"),
        holdings=pd.DataFrame(hold, index=idx, columns=cols),
        n_positions=pd.Series(npos, index=idx, name="n_positions"),
    )


def buy_and_hold(series: pd.Series, start=None, end=None) -> pd.Series:
    """Daily return series of a single instrument, on the book's calendar."""
    s = series
    if start is not None:
        s = s[s.index >= start]
    if end is not None:
        s = s[s.index <= end]
    return s.pct_change().fillna(0.0)


def equal_weight_universe(adj: pd.DataFrame,
                          eligible_by_date: dict[pd.Timestamp, pd.Index],
                          rf: pd.Series, round_trip_bps: float,
                          start=None, end=None) -> BookResult:
    """The survivorship-neutral benchmark.

    Equal weight over exactly the names the strategy was allowed to choose
    from, rebalanced on the same dates. Because it inherits the identical
    missing-delisted-name bias, strategy-minus-this is the cleanest alpha
    estimate this dataset can produce (audit FC-4).
    """
    targets = {}
    for d, names in eligible_by_date.items():
        if len(names) == 0:
            continue
        targets[d] = pd.Series(1.0 / len(names), index=names)
    return run_book(adj, targets, rf, round_trip_bps, start=start, end=end)
