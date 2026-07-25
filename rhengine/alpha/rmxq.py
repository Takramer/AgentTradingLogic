"""rmxq.py — RMX-Q, the quality-tilted S&P 500 book.

Frozen specification (docs/RMX-PREREGISTRATION.md Amendment E):

    every year, on the last trading session of June, executed at the NEXT
    session's close:

        rank the point-in-time eligible S&P 500 members by operating
        profitability  OP/BE = (revenue - COGS - SG&A - interest) / book equity
        using the last fiscal year published at least six months earlier;

        hold the top 30%, equal-weighted, for twelve months.

No timing overlay, no leverage, no momentum, no volatility target. Each of
those was tested and rejected first, with the numbers recorded in the
pre-registration.

Why operating profitability and nothing else: an eight-signal price-only screen
produced no |t| above 1.05 in this universe, and of five fundamental signals
only OP/BE cleared the pre-declared bar (rank-IC t = +2.52 with Newey-West(12)
standard errors). The prior is not this sample - it is Ken French's own
1963-2026 portfolios, where the long-only top-30% OP portfolio beat the market
in every era with a full-sample excess t of +2.88, while value and investment
went flat after 2000.

    py -m rhengine.alpha.rmxq --lo 2010-07-01 --hi 2018-12-31
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .book import buy_and_hold, equal_weight_universe, run_book
from .fundamentals import Fundamentals
from .panel import Panel
from .rmx import load_panel
from .stats import compare, print_report

SIGNAL = "op_be"
TOP_FRACTION = 0.30
REBALANCE_MONTH = 6          # French forms portfolios at the end of June
DEFAULT_COST_BPS = 10.0


@dataclass
class RMXQConfig:
    signal: str = SIGNAL
    top_fraction: float = TOP_FRACTION
    rebalance_month: int | None = REBALANCE_MONTH   # None = every month
    min_names: int = 40
    weighting: str = "value"
    """"value" (French's published construction) or "equal".

    This is not a detail. Ken French's operating-profitability portfolios - the
    1963-2026 evidence this strategy rests on - are VALUE-weighted. An
    equal-weighted implementation silently adds a size exposure on top of the
    quality tilt, and over 2019-2026 that exposure cost 3.3 pp/yr as mega-caps
    ran away from the average S&P 500 name. See Amendment F of the
    pre-registration."""
    max_weight: float = 0.10
    """Cap on any single position. French applies no cap; 10% here stops the
    book from becoming three stocks when market caps get as concentrated as
    they did by 2025. Set to 1.0 to reproduce the uncapped construction."""


def rebalance_dates(panel: Panel, cfg: RMXQConfig, lo: str, hi: str) -> pd.DatetimeIndex:
    me = panel.month_ends(lo, hi)
    if cfg.rebalance_month is None:
        return me
    return pd.DatetimeIndex([d for d in me if d.month == cfg.rebalance_month])


def market_cap(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
               names: pd.Index) -> pd.Series:
    """Shares outstanding (point-in-time, lagged) x the price on `date`.

    The share count carries the same six-month publication lag as every other
    accounting item, so a buyback or issuance is only reflected once it was
    public. Prices are current, which is correct: a weight is set from today's
    price and yesterday's share count, exactly as an index provider does it.
    """
    sh = fund.as_of(date, "shares", names)
    px = panel.close.loc[date].reindex(sh.index)
    cap = (sh * px).dropna()
    return cap[cap > 0]


def _weights(cap: pd.Series, cfg: RMXQConfig) -> pd.Series:
    if cfg.weighting == "equal" or cap.empty:
        return pd.Series(1.0 / max(len(cap), 1), index=cap.index)
    w = cap / cap.sum()
    # With n names a cap below 1/n is arithmetically impossible; equal weight is
    # the most diversified portfolio available, so that is the binding limit.
    cap_w = max(cfg.max_weight, 1.0 / len(cap))
    if cap_w < 1.0:
        # iteratively cap and redistribute, so the cap does not silently
        # leave the weights summing to less than one
        for _ in range(50):
            over = w > cap_w + 1e-12
            if not over.any():
                break
            excess = float((w[over] - cap_w).sum())
            w[over] = cap_w
            room = ~over
            if not room.any() or w[room].sum() <= 0:
                break
            w[room] += excess * w[room] / w[room].sum()
    return w / w.sum()


def build_targets(panel: Panel, fund: Fundamentals, cfg: RMXQConfig,
                  lo: str, hi: str):
    """Weight targets, the covered universe, and per-rebalance diagnostics."""
    targets: dict[pd.Timestamp, pd.Series] = {}
    covered: dict[pd.Timestamp, pd.Index] = {}
    diag = []
    for d in rebalance_dates(panel, cfg, lo, hi):
        names = panel.eligible(d)
        if len(names) < cfg.min_names:
            continue
        v = fund.as_of(d, cfg.signal, names)
        v = v[np.isfinite(v)]
        if cfg.weighting == "value":
            # a name without a share count cannot be value-weighted, so it is
            # not investable here and must leave the covered universe too -
            # otherwise the benchmark and the book see different populations
            cap = market_cap(panel, fund, d, v.index)
            v = v.reindex(cap.index).dropna()
        if len(v) < cfg.min_names:
            continue
        k = max(int(len(v) * cfg.top_fraction), 10)
        pick = v.nlargest(k).index
        if cfg.weighting == "value":
            w = _weights(market_cap(panel, fund, d, pick), cfg)
        else:
            w = pd.Series(1.0 / len(pick), index=pick)
        targets[d] = w
        covered[d] = v.index
        diag.append({"date": str(d.date()), "eligible": len(names),
                     "covered": len(v), "coverage": len(v) / len(names),
                     "picked": len(pick), "max_weight": float(w.max()),
                     "fiscal_year": fund.latest_year_for(d),
                     "median_op_be": float(v.median()),
                     "picked_min_op_be": float(v.loc[pick].min())})
    return targets, covered, pd.DataFrame(diag)


def run(panel: Panel, fund: Fundamentals, cfg: RMXQConfig, lo: str, hi: str,
        cost_bps: float = DEFAULT_COST_BPS):
    targets, covered, diag = build_targets(panel, fund, cfg, lo, hi)
    if not targets:
        raise ValueError("no rebalances produced")
    dates = sorted(targets)
    start, end = dates[0], pd.Timestamp(hi)

    bk = run_book(panel.adj, targets, panel.rf, cost_bps, start=start, end=end)

    # Same names, same dates, same weighting scheme, but NO signal. This is the
    # benchmark that isolates the signal: it carries the identical survivorship
    # gap and the identical size exposure, so the difference is the ranking and
    # nothing else.
    neutral_targets = {}
    for d, names in covered.items():
        if len(names) == 0:
            continue
        if cfg.weighting == "value":
            neutral_targets[d] = _weights(market_cap(panel, fund, d, names), cfg)
        else:
            neutral_targets[d] = pd.Series(1.0 / len(names), index=names)
    neutral = run_book(panel.adj, neutral_targets, panel.rf, cost_bps,
                       start=start, end=end)
    ew = equal_weight_universe(panel.adj, covered, panel.rf, cost_bps,
                               start=start, end=end)
    spy = buy_and_hold(panel.spy, start, end).reindex(
        bk.daily_return.index).fillna(0.0)
    label = "CW covered universe" if cfg.weighting == "value" else "EW covered universe"
    return {"book": bk, "targets": targets, "covered": covered, "diag": diag,
            "neutral": neutral,
            "benchmarks": {"SPY (cap-weight)": spy,
                           label: neutral.daily_return,
                           "EW covered universe": ew.daily_return}}


def report(lo: str = "2010-07-01", hi: str = "2018-12-31",
           cost_bps: float = DEFAULT_COST_BPS, trials: int = 60,
           cfg: RMXQConfig | None = None, quiet: bool = False) -> dict:
    panel, fund = load_panel(), Fundamentals.load()
    cfg = cfg or RMXQConfig()
    res = run(panel, fund, cfg, lo, hi, cost_bps)
    bk = res["book"]
    rep = compare(bk.daily_return, res["benchmarks"], rf=panel.rf,
                  trials=trials, label="RMX-Q")
    rep.update({
        "cost_bps": cost_bps,
        "turnover_ann": bk.annual_turnover,
        "cost_drag_total_pct": bk.total_cost_pct,
        "n_rebalances": len(res["targets"]),
        "mean_positions": float(res["diag"]["picked"].mean()),
        "mean_coverage": float(res["diag"]["coverage"].mean()),
    })
    if not quiet:
        print(f"\n=== RMX-Q  {lo} .. {hi}  @ {cost_bps:.0f} bps ===")
        print(f"{rep['n_rebalances']} annual rebalances | "
              f"{rep['mean_positions']:.0f} names held | "
              f"fundamental coverage {rep['mean_coverage'] * 100:.0f}% of the "
              f"eligible universe | one-way turnover "
              f"{rep['turnover_ann'] * 100:.0f}%/yr | "
              f"cost drag {rep['cost_drag_total_pct']:.1f}% total\n")
        print_report(rep)
    return rep | {"_res": res, "_panel": panel, "_fund": fund}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="2010-07-01")
    ap.add_argument("--hi", default="2018-12-31")
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    ap.add_argument("--top-fraction", type=float, default=TOP_FRACTION)
    ap.add_argument("--signal", default=SIGNAL)
    ap.add_argument("--monthly", action="store_true",
                    help="rebalance every month instead of annually in June")
    ap.add_argument("--trials", type=int, default=60)
    args = ap.parse_args()

    cfg = RMXQConfig(signal=args.signal, top_fraction=args.top_fraction,
                     rebalance_month=None if args.monthly else REBALANCE_MONTH)
    report(args.lo, args.hi, args.cost_bps, args.trials, cfg)


if __name__ == "__main__":
    main()
