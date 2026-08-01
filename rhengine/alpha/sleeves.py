"""sleeves.py — long-only cap-weighted quintile sleeves, one per published theme.

Every sleeve here has the same shape, because that shape is the only one with a
survivorship-free published record (see docs/EXTERNAL-RESEARCH-2026-07.md):

    rank the point-in-time eligible universe on a slow signal
    hold the top quintile, cap-weighted, no name above 10%
    rebalance on a fixed calendar, execute at the NEXT close
    no stops, no targets, no regime gate

Five sleeves (`DEFAULT_SLEEVES`), chosen so that no two share a JKP theme.

MEASURE THEM THE WAY THEY ARE BUILT. Every number below is the published
top-quintile VALUE-WEIGHTED LONG-ONLY portfolio's return **in excess of the
market** (`py -m rhengine.alpha.external --long-only ...`). That is what this
module holds. Long-short spreads are two to twenty times larger, and quoting
them here would describe a portfolio nobody is running — the gap on these five:

                            long-short (ex-micro)    long-only, ex-market
    1970-2024                  55 bp/mo t=7.7          20 bp/mo t=5.2
    2015-2024                  61 bp/mo t=4.1         2.6 bp/mo t=0.35

Per sleeve, long-only excess over the market, bp/month:

    sleeve      OSAP          1970-2024      2010-2018     2015-2024   theme
    quality     GP           14.3 / 1.90    8.0 / 0.53   26.2 / 1.47   profitability
    momentum    Mom12m       22.3 / 2.09  -14.5 /-0.81  -11.3 /-0.60   momentum
    investment  AssetGrowth  21.5 / 2.26  -15.7 /-0.96  -10.5 /-0.47   investment
    issuance    ShareIss5Y   22.5 / 3.84   -0.7 /-0.11    6.0 / 0.74   issuance
    payout      PayoutYield  16.7 / 1.89    9.5 / 0.90   19.0 / 1.08   payout

    equal-weight blend        20.1 / 5.20   -5.7 /-1.01    2.6 / 0.35
    mean pairwise corr             -0.02        -0.10         -0.06

Read that table honestly. Over 55 years the blend beats the market by 2.4%/yr
with t = 5.2, and it does so on near-zero correlations — the blend's t is 2.7x
the mean leg's, which is the whole reason for blending. Since 2015 it beats the
market by 0.3%/yr, t = 0.35: **nothing.** Not because the sleeves stopped
ranking (the top quintile still beat the middle quintile by 0.6%/yr) but because
a decade in which SMB and HML were both about -2%/yr punished every tilt away
from mega-cap growth. The 55-year number is what to expect from a normal era;
the 10-year number is what the last decade actually paid.

Correlation, not signal strength, is the lever — consistent with Chen & Welch
(2026) on the published anomaly population. But blending near-zero-alpha legs
produces a near-zero-alpha blend, and no amount of diversification fixes that.

Sleeves that are NOT in `DEFAULT_SLEEVES`, and why:

* `value` (EBM) — built 2026-07-25 and rejected the same day. Chosen on a
  long-short screen, then failed the long-only screen in all three windows. The
  cautionary case; read its docstring before adding anything.
* `reversal` — dropped 2026-07-25. It replaced the `bollinger` strategy and had
  the right economic claim, but keeps only 0.37 of its published return
  ex-micro and cost the blend 0.83 in alpha t at 382%/yr turnover. Kept for
  reproducibility. **Read the cost warning in its docstring.**
* `reversal_fast` — research only, the 5-day/weekly mis-specification.

Volatility scaling (`scale_by_own_vol`) implements Barroso & Santa-Clara,
"Momentum has its moments" (JFE 2015, https://doi.org/10.1016/j.jfineco.2015.02.002).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .fundamentals import Fundamentals
from .panel import Panel
from .rmxq import _weights, adjusted_shares, market_cap

# ------------------------------------------------------------------ conventions
TOP_FRACTION = 0.20
MAX_WEIGHT = 0.10
MIN_NAMES = 40
QUALITY_MONTH = 6            # Fama-French form profitability sorts end of June
VOL_WINDOW = 126             # ~6 months, Barroso & Santa-Clara's window
ISSUANCE_YEARS = 5           # Daniel & Titman / Pontiff & Woodgate horizon


# ==================================================================== calendars
def week_ends(panel: Panel, lo: str | None = None,
              hi: str | None = None) -> pd.DatetimeIndex:
    """Last trading session of each ISO week in [lo, hi]."""
    idx = panel.adj.index
    if lo is not None:
        idx = idx[idx >= pd.Timestamp(lo)]
    if hi is not None:
        idx = idx[idx <= pd.Timestamp(hi)]
    s = pd.Series(idx, index=idx)
    iso = idx.isocalendar()
    return pd.DatetimeIndex(
        s.groupby([iso["year"].to_numpy(), iso["week"].to_numpy()]).last().values)


def june_ends(panel: Panel, lo: str | None = None,
              hi: str | None = None) -> pd.DatetimeIndex:
    me = panel.month_ends(lo, hi)
    return pd.DatetimeIndex([d for d in me if d.month == QUALITY_MONTH])


def quarter_ends(panel: Panel, lo: str | None = None,
                 hi: str | None = None) -> pd.DatetimeIndex:
    """Last trading session of March, June, September and December.

    A turnover control, not a signal change. OSAP's `Mom12m` record is a MONTHLY
    rebalance, so a sleeve run on this calendar has deviated from the record it
    was screened on and the deviation has to be measured, not assumed harmless:
    momentum decays inside a quarter, so gross return falls. Whether NET return
    rises is the whole question — see `--momentum-calendar` in blend.py.
    """
    me = panel.month_ends(lo, hi)
    return pd.DatetimeIndex([d for d in me if d.month in (3, 6, 9, 12)])


CALENDARS: dict[str, Callable[..., pd.DatetimeIndex]] = {
    "weekly": week_ends,
    "monthly": lambda p, lo, hi: p.month_ends(lo, hi),
    "quarterly": quarter_ends,
    "annual": june_ends,
}


# ====================================================================== signals
# Every signal returns "higher is better" so the sleeve builder can always take
# nlargest. Predictors whose published sign is negative (asset growth, share
# issuance) are negated here, once, at the source.

def quality_op_be(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
                  names: pd.Index) -> pd.Series:
    """Operating profitability, (rev - COGS - SG&A - interest) / book equity.

    Fama & French 2015. This is RMX-B's quality leg, unchanged.
    """
    return fund.as_of(date, "op_be", names)


def momentum_12_1(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
                  names: pd.Index) -> pd.Series:
    """Return over months D-12..D-2. Jegadeesh & Titman 1993.

    The skipped month carries short-term reversal, which is the opposite sign —
    and which the `reversal` sleeve harvests deliberately instead.
    """
    me = panel.month_ends(None, str(date.date()))
    if len(me) < 14:
        return pd.Series(dtype=float)
    px = panel.adj.reindex([me[-13], me[-2]])[names]
    mom = px.iloc[1] / px.iloc[0] - 1.0
    return mom[np.isfinite(mom)]


def _minus_trailing_return(panel: Panel, date: pd.Timestamp, names: pd.Index,
                           sessions: int, residual: bool) -> pd.Series:
    idx = panel.adj.index
    i = idx.get_indexer([date], method="pad")[0]
    if i < sessions:
        return pd.Series(dtype=float)
    px = panel.adj.iloc[[i - sessions, i]][names]
    r = px.iloc[1] / px.iloc[0] - 1.0
    if residual:
        spy = panel.spy.iloc[[i - sessions, i]]
        r = r - (spy.iloc[1] / spy.iloc[0] - 1.0)
    return -r[np.isfinite(r)]


def reversal_1m(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
                names: pd.Index, residual: bool = True) -> pd.Series:
    """Minus the prior month's return — short-term reversal, Jegadeesh 1990.

    This is deliberately the SAME construction as OSAP's `STreversal`, the
    series the sleeve was screened on: one-month formation, monthly rebalance.
    An earlier version of this sleeve used a 5-day formation on a weekly
    calendar, which is a different signal from the one the evidence covers and
    turned over ~5,000%/yr; `reversal_fast` keeps it for research only.

    `residual=True` subtracts SPY's own return over the same window, so the
    sleeve is not quietly long beta after a down month. That is not cosmetic: an
    unresidualised reversal sleeve loads up on market exposure at exactly the
    moment the index fell, which is how a "mean reversion" book becomes a
    leveraged market bet.

    THE COST AND SIZE WARNING. This is the sleeve most likely to disappoint:

    * Micro-cap dependence. On OSAP's published record `STreversal` earns 281
      bp/month across all stocks but only **105 bp ex-micro (ratio 0.37)** — the
      worst retention of any signal in the top 20 by t-stat. Most of the
      textbook reversal premium sits in names this project cannot trade.
    * Turnover. Even monthly, a fully rotating quintile is ~1,200%/yr one-way.
      Check `--cost-bps 30` before believing any result here.

    It earns its place mainly through correlation: on the published series its
    excess return is -0.56 correlated with momentum's, which is worth more to
    the blend than its own mean.
    """
    return _minus_trailing_return(panel, date, names, 21, residual)


def reversal_fast(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
                  names: pd.Index, residual: bool = True) -> pd.Series:
    """Minus the last 5 sessions' return, for a weekly calendar. RESEARCH ONLY.

    Measured in this panel over 2010-2018 it drove the blend's one-way turnover
    to 1,311%/yr and 11.1% of total cost drag, turning a positive blend into
    -0.97%/yr against its own cap-weighted universe. It is kept only so that
    result stays reproducible.
    """
    return _minus_trailing_return(panel, date, names, 5, residual)


def investment_asset_growth(panel: Panel, fund: Fundamentals,
                            date: pd.Timestamp, names: pd.Index) -> pd.Series:
    """Minus year-on-year total-asset growth. Cooper, Gulen & Schill 2008.

    Low-investment firms outperform. One XBRL tag (`Assets`), annual turnover,
    and the highest published Sharpe per unit of turnover in the catalogue.
    """
    g = fund.as_of(date, "asset_growth", names)
    return -g[np.isfinite(g)]


def issuance_5y(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
                names: pd.Index) -> pd.Series:
    """Minus 5-year growth in split-adjusted share count.

    Pontiff & Woodgate 2008 / Daniel & Titman 2006: firms that shrink their
    share count outperform firms that issue. Net repurchasers beat net issuers.

    **Splits must be removed first.** A 4:1 split quadruples the reported share
    count without issuing a single share; not adjusting would rank every
    splitting company as a massive issuer. This is the same trap that produced
    the $1.5-trillion GE in the market-cap bug (see `Panel.split_factor_after`),
    and it is handled the same way: both share counts are pushed onto today's
    split basis before the ratio is taken.
    """
    y_now = fund.latest_year_for(date)
    if y_now is None:
        return pd.Series(dtype=float)
    y_then = y_now - ISSUANCE_YEARS
    try:
        now = fund.table["shares"].xs(y_now, level="year")
        then = fund.table["shares"].xs(y_then, level="year")
    except KeyError:
        return pd.Series(dtype=float)

    out = {}
    for tk in names:
        a, b = now.get(tk, np.nan), then.get(tk, np.nan)
        if not (np.isfinite(a) and np.isfinite(b)) or a <= 0 or b <= 0:
            continue
        # each fiscal year's count is on its own split basis; both are moved to
        # the current basis by the splits that happened after that year ended
        fa = panel.split_factor_after(tk, pd.Timestamp(year=y_now, month=12, day=31))
        fb = panel.split_factor_after(tk, pd.Timestamp(year=y_then, month=12, day=31))
        growth = (a * fa) / (b * fb) - 1.0
        if np.isfinite(growth):
            out[tk] = -growth
    return pd.Series(out, dtype=float)


def market_cap_at(panel: Panel, fund: Fundamentals, fy: int,
                  price_date: pd.Timestamp, names: pd.Index) -> pd.Series:
    """Market cap from fiscal year `fy`'s shares at `price_date`'s close.

    `rmxq.market_cap` always prices the CURRENT close, which is what an index
    provider does when it sets a weight. A valuation ratio needs the opposite:
    both sides measured at the same moment, or six months of price drift leaks
    into the signal and the sleeve becomes part reversal. See `value_ebm`.
    """
    adj_sh = adjusted_shares(panel, fund)
    if fy not in adj_sh.columns:
        return pd.Series(dtype=float)
    sh = adj_sh[fy].reindex(names).dropna()
    i = panel.close.index.get_indexer([pd.Timestamp(price_date)], method="pad")[0]
    if i < 0 or sh.empty:
        return pd.Series(dtype=float)
    cap = (sh * panel.close.iloc[i].reindex(sh.index)).dropna()
    return cap[cap > 0]


def value_ebm(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
              names: pd.Index) -> pd.Series:
    """Enterprise book-to-market. Penman, Richardson & Tuna (JAR 2007).

        EBM = (book equity + net cash) / (market equity + net cash)

    where net cash is cash and short-term investments minus all debt. This is
    OSAP's `EBM` formula written out — the *enterprise* component of B/M, with
    the leverage component stripped out. Penman et al. show that plain B/M mixes
    two effects with opposite signs: the enterprise ratio predicts returns
    positively while the leverage component predicts them negatively, so B/M
    dilutes its own signal. Removing the leverage part is the whole point.

    NOT IN `DEFAULT_SLEEVES` — THIS SLEEVE WAS REJECTED. It is kept because the
    rejection is worth being able to reproduce.

    EBM was selected as a 5th sleeve on OSAP's ex-micro LONG-SHORT record, where
    it adds +0.51 blend t over 1970-2024 and +0.54 over 2015-2024 with the
    highest ex-micro retention of any valuation signal (0.91). Measured instead
    on the construction this sleeve actually has — long-only, cap-weighted top
    quintile, excess over the market (`external.long_only`) — it earns:

        1970-2024   +11.8 bp/month  t=+1.10     blend d_t  -0.06
        2010-2018    -7.5 bp/month  t=-0.34     blend d_t  -0.20
        2015-2024    -2.6 bp/month  t=-0.08     blend d_t  -0.12

    It fails in all three windows. A long-short spread can be wide because the
    short leg falls, and this account cannot collect that; EBM's premium is
    substantially a short-side premium. The local 2010-2018 ablation agreed
    independently (d_alpha_t -1.28, the worst of any sleeve tried), so the
    published record and the panel both reject it.

    `DivYieldST` was the pick before EBM and was rejected earlier, on reading
    its definition: it is Litzenberger & Ramaswamy's *predicted dividend next
    month*, discretized to four levels — a monthly dividend-capture timing
    signal, not a slow value tilt. Plain `BM` is worse than EBM on every window
    since 2010 (-52.3 bp/month over 2010-2018). The lesson, which cost two
    build cycles: **screen on `long_only`, never on the long-short.**

    THE RATIO TRAP. When net cash is more negative than market equity, the
    denominator goes negative; if book equity is also below net debt, BOTH terms
    are negative and the ratio comes out large and positive. That would rank the
    most distressed, most levered names as the deepest value. Only strictly
    positive denominators and positive book equity are kept, which is also why
    this sleeve cannot hold a highly levered firm no matter how cheap it looks.

    TIMING. Market equity is measured at the FISCAL YEAR END, not at the June
    rebalance — OSAP's `mve_c` is calendar-aligned to the accounting data, and
    Fama-French match December book to December market for the same reason.
    Using the June price instead would put six months of price change in the
    numerator of a ratio whose denominator is six months older, which turns a
    slow value sort into a partial 6-month reversal bet. That is the horizon of
    the `momentum` sleeve with the sign flipped, so it would raise this sleeve's
    correlation with momentum — destroying the only thing it was added for.
    """
    fy = fund.latest_year_for(pd.Timestamp(date))
    if fy is None:
        return pd.Series(dtype=float)
    eq = fund.as_of(date, "equity", names)
    nc = fund.as_of(date, "net_cash", names)
    cap = market_cap_at(panel, fund, fy,
                        pd.Timestamp(year=fy, month=12, day=31), names)
    idx = eq.index.intersection(nc.index).intersection(cap.index)
    if len(idx) == 0:
        return pd.Series(dtype=float)
    eq, nc, cap = eq[idx], nc[idx], cap[idx]
    num = eq + nc
    den = cap + nc
    ok = (den > 0) & (eq > 0) & np.isfinite(num) & np.isfinite(den)
    return (num[ok] / den[ok])


def payout_yield(panel: Panel, fund: Fundamentals, date: pd.Timestamp,
                 names: pd.Index) -> pd.Series:
    """Total payout yield. Boudoukh, Michaely, Richardson & Roberts (JF 2007).

        (common dividends + share repurchases) / market equity lagged 6 months

    Firms handing cash back outperform firms that do not. Boudoukh et al.'s point
    is that dividend yield alone became the wrong measure once repurchases
    overtook dividends as the main channel in the 1980s: a company returning all
    its cash through buybacks scores zero on dividend yield. Total payout fixes
    that, which is why this and not `DivYieldAnn`.

    WHY THIS IS THE 5TH SLEEVE. It is the only candidate that beat the market as
    a long-only cap-weighted top quintile in every window tested — the screen
    that matters, run via `external.long_only`, not the long-short:

        window       solo bp/mo     t      blend d_t   blend d_SR
        1970-2024        +16.7    +1.89       +0.38       +0.03
        2010-2018         +9.5    +0.90       +0.44       +0.16
        2015-2024        +19.0    +1.08       +0.37       +0.12

    Nineteen candidates were screened this way and only this one and `Tax` were
    positive in all three; `Tax` adds almost nothing in two of them. Note the
    honest size of the effect: no window reaches t = 2 standalone, and after
    screening nineteen signals a blend d_t of +0.4 is at the edge of what
    selection alone produces. This sleeve is here on the strength of its
    consistency and a 2,000-citation paper, not on a decisive number.

    OVERLAP WITH THE ISSUANCE SLEEVE IS REAL. Buybacks both raise payout yield
    and shrink the share count, so this correlates +0.45 (1970-2024) and +0.48
    (2015-2024) with `issuance` — by far the highest pair in the blend. It still
    earns its place: 0.45 leaves most of the variance independent, and the
    blend's mean pairwise correlation stays near zero. But this is the one pair
    to watch if a future sleeve is added on the same theme. `NetPayoutYield`
    (payout minus issuance) was the alternative and correlates +0.64/+0.66 with
    `issuance` — too much, and it fails the 2010-2018 window.

    DELIBERATE DEVIATIONS from the published construction, both documented
    because they change what the signal is:

    * Financial firms are NOT excluded. OSAP drops SIC 6000-6999; this project
      has no SIC codes on disk. Banks are large payers, so this sleeve holds
      more of them than the published record does.
    * Market equity is lagged 6 months from the FISCAL YEAR END, per the paper,
      which for a June rebalance means the June close of the *previous* year.
      See the timing note in `value_ebm` for why a valuation ratio must not be
      priced at the rebalance date.
    * Payout <= 0 is excluded, per the paper: a firm returning nothing has no
      payout yield, and keeping it as a zero would make the bottom of the sort a
      different population from the top.
    """
    fy = fund.latest_year_for(pd.Timestamp(date))
    if fy is None:
        return pd.Series(dtype=float)
    pay = fund.as_of(date, "payout", names)
    eq = fund.as_of(date, "equity", names)
    # "lagged 6 months" from a 31 December fiscal year end is the June close
    cap = market_cap_at(panel, fund, fy,
                        pd.Timestamp(year=fy, month=6, day=30), names)
    idx = pay.index.intersection(eq.index).intersection(cap.index)
    if len(idx) == 0:
        return pd.Series(dtype=float)
    pay, eq, cap = pay[idx], eq[idx], cap[idx]
    y = pay / cap
    ok = (pay > 0) & (eq > 0) & np.isfinite(y)
    return y[ok]


# ====================================================================== sleeves
@dataclass(frozen=True)
class Sleeve:
    name: str
    signal: Callable[..., pd.Series]
    calendar: str                      # weekly | monthly | annual
    theme: str                         # JKP theme, for the no-two-alike rule
    osap: str                          # the published analogue in OSAP
    top_fraction: float = TOP_FRACTION
    max_weight: float = MAX_WEIGHT
    hold_between_rebalances: bool = True
    # No-trade band. A name enters on reaching the top `top_fraction` and is only
    # sold once it falls out of the top `exit_fraction`. Equal to top_fraction
    # means no band, which is the strict-quintile default.
    exit_fraction: float = TOP_FRACTION

    def __post_init__(self):
        if self.exit_fraction < self.top_fraction:
            raise ValueError(
                f"{self.name}: exit_fraction {self.exit_fraction} is tighter "
                f"than top_fraction {self.top_fraction}, which would sell names "
                f"that still qualify to enter and churn every rebalance")


REGISTRY: dict[str, Sleeve] = {
    "quality": Sleeve("quality", quality_op_be, "annual", "profitability", "GP"),
    "momentum": Sleeve("momentum", momentum_12_1, "monthly", "momentum", "Mom12m"),
    "reversal": Sleeve("reversal", reversal_1m, "monthly", "short_term_reversal",
                       "STreversal"),
    "reversal_fast": Sleeve("reversal_fast", reversal_fast, "weekly",
                            "short_term_reversal", "STreversal"),
    "investment": Sleeve("investment", investment_asset_growth, "annual",
                         "investment", "AssetGrowth"),
    "issuance": Sleeve("issuance", issuance_5y, "annual", "debt_issuance",
                       "ShareIss5Y"),
    "value": Sleeve("value", value_ebm, "annual", "value", "EBM"),
    "payout": Sleeve("payout", payout_yield, "annual", "payout", "PayoutYield"),
}

# The five blocks that are actually run. Two changes on 2026-07-25:
#   - `reversal` OUT: retains only 0.37 of its published return ex-micro and cost
#     the blend 0.83 in alpha t at 382%/yr turnover (docs/RMX-C.md).
#   - `payout` IN: the only candidate of nineteen that beat the market as a
#     LONG-ONLY top quintile in all three test windows. `value` (EBM) was picked
#     first, built, and rejected when screened on the right construction.
# Changing this tuple changes the shipped strategy. The no-two-alike theme rule
# is enforced by tests/test_sleeves.py.
DEFAULT_SLEEVES = ("quality", "momentum", "investment", "issuance", "payout")


class _Cfg:
    """Minimal shim so `rmxq._weights` can be reused unchanged."""

    def __init__(self, max_weight: float, weighting: str = "value"):
        self.max_weight = max_weight
        self.weighting = weighting


def build_sleeve(panel: Panel, fund: Fundamentals, sleeve: Sleeve,
                 lo: str, hi: str, min_names: int = MIN_NAMES
                 ) -> tuple[dict[pd.Timestamp, pd.Series],
                            dict[pd.Timestamp, pd.Index], pd.DataFrame]:
    """Weight targets and covered universe for one sleeve.

    The covered universe is the set of names that *had a usable signal value*,
    not merely the eligible set — the benchmark has to see the same population
    as the book or the comparison silently measures coverage instead of skill
    (audit FC-4).

    **This function is stateful across rebalance dates** when the sleeve has a
    no-trade band (`exit_fraction > top_fraction`): what it holds depends on what
    it held last time. That is the point — a strict quintile sells a name the
    moment it slips to rank 21% and buys it back when it returns, which is pure
    churn against a signal that has barely moved. With a band, a name enters on
    reaching the top `top_fraction` and leaves only on falling out of the top
    `exit_fraction`, so the held count floats between the two.

    The band does NOT change the signal or the covered universe, so the
    benchmark and the published analogue both stay valid. It only changes when a
    decision is acted on.
    """
    cfg = _Cfg(sleeve.max_weight)
    dates = CALENDARS[sleeve.calendar](panel, lo, hi)
    targets: dict[pd.Timestamp, pd.Series] = {}
    covered: dict[pd.Timestamp, pd.Index] = {}
    banded = sleeve.exit_fraction > sleeve.top_fraction
    held: pd.Index = pd.Index([])
    diag = []
    for d in dates:
        names = panel.eligible(d)
        if len(names) < min_names:
            continue
        cap_all = market_cap(panel, fund, d, names)
        if len(cap_all) < min_names:
            continue
        v = sleeve.signal(panel, fund, d, cap_all.index)
        v = v[np.isfinite(v)]
        if len(v) < min_names:
            continue
        k = max(int(len(v) * sleeve.top_fraction), 10)
        pick = v.nlargest(k).index
        n_new = len(pick)
        if banded and len(held):
            k_exit = max(int(len(v) * sleeve.exit_fraction), k)
            # a name already held survives while it is still inside the wider
            # band AND still has a usable signal this period
            survivor = v.nlargest(k_exit).index.intersection(held)
            pick = pick.union(survivor)
        w = _weights(cap_all.reindex(pick).dropna(), cfg)
        if w.empty or w.sum() <= 0:
            continue
        targets[d] = w / w.sum()
        covered[d] = v.index
        held = w.index
        diag.append({"date": str(d.date()), "eligible": len(names),
                     "covered": len(v), "picked": len(pick),
                     "fresh": n_new, "held_over": len(pick) - n_new,
                     "max_weight": float(targets[d].max())})
    return targets, covered, pd.DataFrame(diag)


# ============================================================== vol scaling
def realised_vol(daily: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    """Annualised trailing realised volatility, shifted so it is knowable.

    `shift(1)` matters: the value stamped on session D uses returns through
    D-1 only, so a rebalance decided on D never sees D's own move.
    """
    return daily.rolling(window, min_periods=window // 2).std().shift(1) \
        * np.sqrt(252.0)


def scale_by_own_vol(daily: pd.Series, dates: pd.DatetimeIndex,
                     target_vol: float | None = None,
                     window: int = VOL_WINDOW,
                     cap: float = 1.5) -> pd.Series:
    """Barroso & Santa-Clara scaling factor for one sleeve, on `dates`.

    The sleeve's weight is multiplied by `target_vol / vol_hat`, where `vol_hat`
    is its own trailing realised volatility. Momentum's volatility is far more
    forecastable than its return (out-of-sample R^2 ~47% in the paper), which is
    why this survives out of sample where market-level volatility timing does
    not — see Cederburg et al. on the Moreira-Muir critique.

    Reported effect on the raw momentum factor, 1927-2011: Sharpe 0.53 -> 0.97,
    excess kurtosis 18.24 -> 2.68, worst month -78.96% -> -28.40%.

    `target_vol` defaults to the sleeve's full-sample realised vol, which makes
    the scaling mean-one and therefore leverage-neutral on average. `cap` bounds
    the multiplier so a quiet stretch cannot lever the sleeve arbitrarily; 1.5 is
    the retail-account answer, not the paper's.
    """
    vol = realised_vol(daily, window)
    if target_vol is None:
        target_vol = float(daily.std() * np.sqrt(252.0))
    k = (target_vol / vol).clip(upper=cap)
    k = k.reindex(daily.index).ffill()
    return k.reindex(dates, method="pad").fillna(1.0)
