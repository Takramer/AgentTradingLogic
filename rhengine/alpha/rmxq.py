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

Superseded as the deliverable by `rmxb.py`: this sleeve alone carries an
information ratio of ~0.26, which needs ~58 years to reach t=2. RMX-B blends it
with momentum to get there in ~39. This module remains the quality leg.
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


def frozen() -> "RMXQConfig":
    """RMX-QT — the deliverable configuration, frozen 2026-07-25.

    Cap-weighted top **quintile** by operating profitability inside the
    point-in-time eligible S&P 500, rebalanced annually at the end of June,
    executed at the next session's close, no single name above 10%.

    This is the direct analogue of French's `BIG HiOP` portfolio - the largest
    size quintile sorted on operating profitability, value-weighted - which
    beat the market in **all five** eras from 1963 to 2026 (+0.78 pp/yr,
    alpha +1.19%/yr t=+1.71, Sharpe 0.52 vs 0.47, beta 0.93).

    Cap weighting is the whole point and is not a detail: RMX-Q's equal-weight
    predecessor earned a genuine +1.62%/yr from the same signal out-of-sample
    and still lost to SPY, because equal-weighting the S&P 500 cost 3.3 pp/yr
    from 2023-2025. At beta 0.93 there is no size bet left to lose.

    Expect a small edge slowly. `scripts/rmxqt_power.py` shows that an alpha of
    this size against a 4.5%/yr tracking error needs ~58 years to reach t=2. In
    this project's panel the sleeve returns +3.17 pp/yr vs SPY in-sample
    (alpha t=+1.74) and -1.99 pp/yr on the already-spent 2019-2026 window: two
    draws from a distribution with a ~1.6 pp/yr standard error, which is to say
    neither is evidence. Anyone promising significance here on a decade of data
    is selling something. `rmxb.py` blends this sleeve with momentum, which is
    what actually gets the combined t-stat above 2.
    """
    return RMXQConfig(signal=SIGNAL, top_fraction=0.20, rebalance_month=6,
                      weighting="value", max_weight=0.10)


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


_SHARES_CACHE: dict = {}

# A one-year share count this far from BOTH its neighbours is a filing units
# error, not a corporate action. Buybacks and issuance move share counts by tens
# of percent; nothing legitimate moves them 50x for one year and back.
UNITS_MIN_FACTOR = 50.0


def _repair_share_units(sh: pd.DataFrame) -> pd.DataFrame:
    """Repair filing units errors in the raw share counts. Ticker x year in/out.

    A separate failure from the split-basis problem `adjusted_shares` solves,
    and invisible to it: that function only ever chooses between `raw` and
    `raw * split_factor`, and a units error is a factor of 1,000. So these pass
    straight through into market cap.

    Measured damage before this fix, on the eligible S&P 500 universe:

        CLX  fy2012   130,962,056,000,000 shares  ->  $10.9 QUADRILLION cap
        CCL  fy2020       932,485,510,000 shares  ->  $24.6 trillion
        HBAN fy2010       863,338,744,000 shares  ->  $5.7 trillion
        WAT  fy2023                59,176 shares  ->  $17 million
        PEG, FTI, SPG, LEN, AIV, GRMN, KLAC, ANF, TER, DLR, BBBY  similar

    27 corrupted cap-years in all, 20 of them inside the 2010-2018 window used
    for every RMX-B / RMX-C implementation check. One $10.9-quadrillion name
    pins the position cap and squeezes every other weight in that year's book —
    the same class of error as the $1.5-trillion GE in `Panel.split_factor_after`,
    which is why the guard is explicit rather than a filter on the output.

    **The discriminator is a one-year SPIKE, not a level shift.** A reverse split
    changes every reported count from that year on, permanently, and is a real
    corporate action the split logic below handles. A units error affects one
    filing and its neighbours disagree in the same direction. So each year is
    compared against the geometric mean of its nearest populated neighbours on
    either side, never against the ticker's own average, and a year with only one
    neighbour is left alone rather than guessed at.

    A detected error is snapped to the nearest power of 1,000 when that lands
    within 50% of the neighbours, and dropped to NaN when it does not — a name
    with no usable share count is excluded from cap weighting that year, which
    is correct. Inventing a plausible-looking number would not be.
    """
    out = sh.copy()
    years = list(sh.columns)
    for tk in sh.index:
        row = sh.loc[tk]
        pos = [y for y in years if np.isfinite(row.get(y, np.nan)) and row[y] > 0]
        if len(pos) < 3:
            continue
        for i, y in enumerate(pos):
            if i == 0 or i == len(pos) - 1:
                continue                      # no neighbour on one side
            v = float(row[y])
            a, b = float(row[pos[i - 1]]), float(row[pos[i + 1]])
            ref = np.sqrt(a * b)
            if not np.isfinite(ref) or ref <= 0:
                continue
            # both neighbours must disagree in the SAME direction, or this is a
            # level shift (a real corporate action) and not a units error
            if not ((v / a > UNITS_MIN_FACTOR and v / b > UNITS_MIN_FACTOR)
                    or (a / v > UNITS_MIN_FACTOR and b / v > UNITS_MIN_FACTOR)):
                continue
            k = round(np.log(v / ref) / np.log(1000.0))
            fixed = v / (1000.0 ** k) if k != 0 else v
            out.loc[tk, y] = fixed if (k != 0 and 0.5 <= fixed / ref <= 2.0) \
                else np.nan
    return out


def adjusted_shares(panel: Panel, fund: Fundamentals) -> pd.DataFrame:
    """Share counts restated onto the price series' split basis, per fiscal year.

    Two things had to be reconciled and neither is optional:

    1. Yahoo's `close` is **split-adjusted**. An SEC share count is on the share
       basis of whatever filing reported it. Multiplying the two directly made
       GE a $1.5 trillion company in 2016 (1:8 reverse split) and hid Apple
       (4:1 split) entirely. 501 of 793 tickers here have at least one split.
    2. The SEC frames API returns the value from *whichever* filing covered that
       period, and filers **restate** prior-period share counts after a split.
       So some values are already split-adjusted and some are not, in the same
       column, with nothing to distinguish them.

    Neither rule alone is enough, so both are used:

    * **Continuity** decides most years. Share counts move a few percent a year
      through buybacks and issuance; a split-basis error moves them by an
      integer multiple, so walking backwards from a known-good year makes the
      choice unambiguous.
    * **Price-to-sales** decides the anchor when the most recent year is itself
      ambiguous - which happens (Mastercard's only share figures here are
      2009-2011, and the 2011 one is already post-split while 2009 and 2010 are
      not, so there is no clean year to walk back from).

    P/S is used only as the anchor and never on its own, because it misfires on
    genuinely expensive companies: NVIDIA really did trade at 39x sales in 2023,
    and a valuation prior would have thrown its share count out by 10x.
    """
    key = id(panel), id(fund)
    if key in _SHARES_CACHE:
        return _SHARES_CACHE[key]

    sh = _repair_share_units(fund.table["shares"].unstack("year"))
    rev = fund.table["revenue"].unstack("year")
    eq = fund.table["equity"].unstack("year")
    years = sorted(int(y) for y in sh.columns)
    out = pd.DataFrame(np.nan, index=sh.index, columns=years, dtype=float)

    # Typical large-cap multiples. The decision is between values an integer
    # factor apart, so it is insensitive to the exact prior.
    PS_PRIOR, PB_PRIOR = 2.5, 2.5

    # price at which each fiscal year's shares first get used (1 July of y+1)
    px_at = {}
    for y in years:
        idx = panel.close.index[panel.close.index >= pd.Timestamp(y + 1, 7, 1)]
        px_at[y] = panel.close.loc[idx[0] if len(idx) else panel.close.index[-1]]

    def factor(tk: str, y: int) -> float:
        return panel.split_factor_after(tk, pd.Timestamp(year=y, month=12, day=31))

    for tk in sh.index:
        row = sh.loc[tk]
        have = [y for y in years if np.isfinite(row.get(y, np.nan)) and row[y] > 0]
        if not have:
            continue

        nxt = None
        for y in reversed(have):
            raw = float(row[y])
            f = factor(tk, y)
            if f == 1.0:
                pick = raw
            elif nxt is not None:
                # continuity with the year already decided
                pick = min((raw, raw * f),
                           key=lambda v: abs(np.log(v / nxt)))
            else:
                # anchor year, and it is ambiguous: fall back to a valuation prior
                px = px_at[y].get(tk, np.nan)
                r = rev.at[tk, y] if y in rev.columns else np.nan
                b = eq.at[tk, y] if y in eq.columns else np.nan
                ref, prior = (r, PS_PRIOR) if (np.isfinite(r) and r > 0) else \
                             ((b, PB_PRIOR) if (np.isfinite(b) and b > 0) else (np.nan, np.nan))
                if not np.isfinite(px) or px <= 0 or not np.isfinite(ref):
                    pick = raw
                else:
                    pick = min((raw, raw * f),
                               key=lambda s: abs(np.log((s * px / ref) / prior)))
            out.loc[tk, y] = pick
            nxt = pick

    _SHARES_CACHE[key] = out
    return out


def market_cap(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
               names: pd.Index) -> pd.Series:
    """Split-consistent shares (point-in-time, lagged) x the price on `date`.

    The share count carries the same six-month publication lag as every other
    accounting item, so a buyback or issuance is only reflected once it was
    public. The price is current, which is correct: an index provider sets a
    weight from today's price and the last published share count.
    """
    fy = fund.latest_year_for(pd.Timestamp(date))
    if fy is None:
        return pd.Series(dtype=float)
    adj_sh = adjusted_shares(panel, fund)
    if fy not in adj_sh.columns:
        return pd.Series(dtype=float)
    sh = adj_sh[fy].reindex(names).dropna()
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
    ap.add_argument("--weighting", default="equal", choices=["value", "equal"])
    ap.add_argument("--frozen", action="store_true",
                    help="use the frozen RMX-QT configuration (see rmxq.frozen)")
    ap.add_argument("--trials", type=int, default=60)
    args = ap.parse_args()

    cfg = frozen() if args.frozen else RMXQConfig(
        signal=args.signal, top_fraction=args.top_fraction,
        weighting=args.weighting,
        rebalance_month=None if args.monthly else REBALANCE_MONTH)
    report(args.lo, args.hi, args.cost_bps, args.trials, cfg)


if __name__ == "__main__":
    main()
