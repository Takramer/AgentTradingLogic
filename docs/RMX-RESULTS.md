# RMX — results

**2026-07-25.** Read this first. Full method, every trial and every rejection:
`docs/RMX-PREREGISTRATION.md`. Why the previous system was thrown out:
`docs/AUDIT-2026-07-24.md`.

---

## The answer

**One construction clears alpha t ≥ 2.0 on a sample long enough to mean
anything: RMX-B, a 50/50 blend of two cap-weighted large-cap sleeves — operating
profitability and 12-1 momentum.**

On Ken French's own portfolios, 1963–2026, survivorship-free, net of a
deliberately punitive cost charge:

| | CAGR | market | alpha/yr | **alpha t** | IR | eras beaten |
|---|---|---|---|---|---|---|
| quality only | 11.61% | 10.90% | +1.13% | +1.62 | 0.22 | 4/4 |
| momentum only | 13.24% | 10.90% | +2.43% | +2.10 | 0.27 | 3/4 |
| **50/50 BLEND** | **12.55%** | **10.90%** | **+1.78%** | **+2.54** | **0.32** | **4/4** |

**+1.64 pp/yr over the market for 63 years, Sharpe 0.55 vs 0.47, beta 0.97,
positive in every era.** The blend's t-stat exceeds *both* of its legs because
their excess returns correlate only **+0.18** — alphas add, tracking errors add
in quadrature. That is the fundamental law of active management doing the work
that no single signal in this project could.

The honest boundary, stated up front: **this project's own 8-year S&P 500
implementation cannot confirm it.** A +1.78%/yr alpha against a 5.5%/yr tracking
error needs ~39 years to reach t=2. The demonstration is French's 63 years; the
implementation is checked for consistency, not significance.

The same French portfolios clear the random-sample bar the old score failed.
Through 2026-07, the 50/50 blend sits at the **100th percentile** of 200 random
BIG-quintile draws (quality 97.5th, momentum 99.5th). Detail and the rejected
panic-cut are in the 2026-10-04 section below. At 22 logged `rmx` trials,
t = +2.54 has family-wise p = 0.215 and does not clear the Šidák bar of 3.04.

Four other hypotheses were pre-registered and rejected on the numbers.

| hypothesis | in-sample | out-of-sample | verdict |
|---|---|---|---|
| Residual momentum at the cited horizon | rank-IC t = **+0.35** | not reached | rejected |
| 8 price-only cross-sectional signals | best \|t\| = **0.89** | not reached | rejected |
| **RMX-D** trend + absolute-momentum timing | +0.94 pp/yr, alpha t=+2.07, 8/11 gates | **−6.22 pp/yr**, alpha t=+0.07, **1/11 gates** | rejected |
| **RMX-Q** profitability tilt, *equal*-weighted | +2.66 pp/yr, alpha t=+2.48, 8/11 gates | **−2.03 pp/yr** vs SPY | rejected — see below |
| **RMX-B** quality + momentum, *cap*-weighted | +1.02 pp/yr, alpha t=+0.42 (underpowered) | 63-year record, **t=+2.54**, 4/4 eras | **kept** |

---

## RMX-B — the strategy

```
QUALITY LEG  (50% of capital, re-formed every June, held 12 months)
    point-in-time eligible S&P 500 members
    rank by operating profitability
        OP/BE = (revenue - COGS - SG&A - interest) / book equity
        from the last fiscal year published at least six months earlier
    hold the top quintile, cap-weighted, no name above 10%

MOMENTUM LEG (50% of capital, re-formed every month)
    same universe
    rank by return over months -12..-2  (the last month is skipped:
        it carries short-term reversal, the opposite sign)
    hold the top quintile, cap-weighted, no name above 10%

executed at the close of the session AFTER the decision
```

~96 names, ~190%/yr one-way turnover (the momentum leg drives it), cost drag
1.6% over 8 years at 10 bps. Two sleeves, two parameters each, all from
Jegadeesh-Titman and Fama-French. Typically only ~11 names sit in both legs —
the overlap is small, which is why the blend diversifies.

**The evidence — French's portfolios, 1963–2026, survivorship-free, nothing of
mine fitted to it.** The quality leg alone (`BIG HiOP`, the largest size
quintile sorted on operating profitability) beats the market in **all five**
eras: 1963–79 +0.14pp, 1980–99 +1.08pp, 2000–15 +0.44pp, 2016–26 +1.44pp,
full sample +0.71pp with alpha t=+1.62. The momentum leg adds +2.34pp full
sample (t=+2.10) but was flat in 2016–26. Blended 50/50 they reach **t=+2.54
and 4/4 eras** — neither leg does that alone.

### The honest limit — this edge cannot be proven on the data that exists

`scripts/rmxqt_power.py`. The quality leg in this project's S&P 500 panel,
in-sample 2011–2018: **+3.17 pp/yr vs SPY, alpha t=+1.74**; against its own
cap-weighted universe, +1.23%/yr with **t=+0.80**. On the (already spent)
2019–2026 window it is −1.99 pp/yr vs SPY.

Those look contradictory. They are not — they are two draws from a distribution
that is far too wide to read:

| true alpha/yr | tracking error | expected t over 7.5 yr | years needed for t=2 |
|---|---|---|---|
| +1.00% | 4.53% | 0.60 | 82 |
| **+1.19%** (French's) | 4.53% | **+0.72** | **58** |
| +2.00% | 4.53% | 1.21 | 21 |

Measuring a ~1.2 pp/yr effect with a 4.5 pp/yr tracking error over 7–8 years
gives a standard error of about 1.6 pp/yr. Getting +1.2%/yr in one window and
−1.7%/yr in the next is exactly what that looks like. **Neither window is
evidence for or against.**

So the claim is bounded deliberately: *French's 63 years is the demonstration;
this project's implementation is checked for consistency with it.* Anyone
quoting significance for a factor tilt on a decade of data is overclaiming.

### A bug worth reporting, because it changed numbers I had already published

Yahoo's `close` is split-adjusted; an SEC share count is not. Multiplying them
made GE (1:8 reverse split) a $1.5 trillion company in 2016 that pinned the
position cap every year, and hid Apple (4:1 split) entirely. 501 of 793 tickers
have at least one split. Worse, the SEC frames API returns whichever filing
covered a period and filers *restate* prior-year share counts after a split, so
some values in the same column are already adjusted and some are not.

`rmxq.adjusted_shares` now disambiguates them by continuity, anchored on a
price-to-sales prior only where the most recent year is itself ambiguous. Every
checked name now lands within 0.89–1.10× of its true market cap, against errors
of 10× before. Numbers withdrawn and restated are listed in Amendment H of the
pre-registration. The bug was *suppressing* the result rather than flattering
it, and it was still only caught by checking computed caps against known ones.

## What *is* real, and is the useful part

**Operating profitability ranks S&P 500 stocks.** It is the first signal in this
repository that has ever beaten its own null:

| | in-sample 2010–2018 | out-of-sample 2019–2026 |
|---|---|---|
| percentile of 200 random draws of the same size from the same universe | **100th** | **88th** |
| alpha vs an equal-weight version of its own universe | **+2.58%/yr, t=+2.76** | **+1.62%/yr, t=+1.36** |

For contrast, the audit measured the previous system's stock-selection rule at
the **53rd percentile of random** — i.e. a coin flip.

**So why did it still lose to SPY?** Arithmetic, not signal decay:

```
    equal-weight covered universe   12.41%/yr
    profitability tilt on top       + 1.62%/yr   <- the real edge
    ------------------------------------------
    RMX-Q                            13.66%/yr
    SPY (cap-weighted)               15.68%/yr
    ------------------------------------------
    shortfall                       - 2.03%/yr
```

The construction paid a **−3.3 pp/yr size drag** — equal-weighting the S&P 500
lost badly to cap-weighting from 2023 to 2025 — and a +1.6 pp signal cannot
cover it. That diagnosis is what produced RMX-B: cap-weight the legs so there is
no size bet left to lose, and add a second, near-uncorrelated sleeve so the
combined information ratio is large enough to be worth holding.

**The defensive result is also real, and also not a way to beat the index.**
RMX-D over 1981–2026: 10.46%/yr against the S&P's 11.15%, worst drawdown
**−33% against −55%**, Sharpe 0.54 against 0.47. It halves drawdown and costs
about 0.7 pp/yr. Worth having if drawdown is the binding constraint; it is not
alpha.

## Numbers you can hold this work to

- **Survivorship bias is measured, not asserted**: equal-weight over the priced
  point-in-time members beat the real equal-weight S&P 500 ETF (RSP) by
  **+0.86 pp/yr** over 2003–2026. Every "vs SPY" figure above carries that
  haircut explicitly.
- **Costs cannot rescue or kill RMX-Q**: 33%/yr one-way turnover means the
  entire curve from 0 to 100 bps round trip moves the result by 0.37 pp/yr.
- **Concentration**: deleting RMX-Q's best 6 months in-sample still leaves
  +75.6% against SPY's +46.4%. The old book turned negative after deleting its
  best 2% of trades.
- **All statistics are computed on non-overlapping monthly returns with
  Newey-West standard errors**, never on trade counts. The old stack's headline
  `t = +15.69` was `t = +2.87` once clustered by day.

## What would make RMX-QT better

1. **Fundamentals before 2009.** SEC XBRL starts in 2009, so the whole
   implementation check covers one bull market and one real bear. Compustat/CRSP
   back to 1963 would let the S&P 500 implementation be validated on the same
   span as the evidence; free sources will not.
2. **Better tag coverage.** `op_be` reaches 66% of the eligible universe — the
   gap is mostly financials and REITs, which Fama-French also treat separately,
   but the coalescing of XBRL tags is noisier than Compustat's standardised
   fields. SEC `companyfacts` per company would give exact filing dates and
   better coverage at the cost of a much larger download.
3. **Breadth past 500 large caps.** Every documented factor is stronger in small
   and mid caps — exactly where this dataset has neither coverage nor credible
   cost assumptions.

## What must not happen

Tuning RMX-D or RMX-Q against their out-of-sample results and re-reporting
them. Both holdouts are spent. RMX-QT's evidence deliberately does **not** come
from those windows — it comes from French's 1963–2026 record, which no decision
in this project influenced.

Nor should anyone expect RMX-QT to visibly beat SPY over any particular year or
even decade. A +1.2 pp/yr alpha with 4.1 pp/yr tracking error will trail the
index in roughly 4 years out of 10. If that is not acceptable, hold SPY.

Item 3 above (go to small and mid caps) was measured and **rejected** on
2026-08-01. See `docs/SIZE-LADDER-2026-08-01.md`. Do not fund that data.

## 2026-10-02 — three pre-registered looks, nothing adopted

Logged in `research_data/trials.jsonl` before any return was read. Details and
the August print are in `docs/HOLDOUT.md`.

Fresh Ken French vintage (CRSP **202608**, through 2026-08). The frozen 50/50
blend is CAGR 12.51% vs the market's 10.89%, alpha **+1.75%/yr, t = +2.53**,
4/4 eras. Sharpe 0.55, 95% CI **+0.29 to +0.81**; the market is 0.47, CI
**+0.21 to +0.72**. The intervals overlap. Against the `rmx` ledger (20 trials)
the family-wise p on t = +2.53 is **0.205**. Required t is 3.02. It already
missed the 6-trial Šidák bar of 2.63 (p = 0.067). It does not clear.

| look | gate | result |
|---|---|---|
| Months after 2026-07 | report only; do not tune | **2026-08 only.** Blend +2.04%, market +2.85%, excess **−0.81 pp**. No change. |
| Vol-scale the momentum leg | t ≥ 2 and a less-severe worst month and 2016–2026 excess within 1 pp of unscaled | t = +2.57, worst month **−24.00% vs −23.73%**. **Left off.** |
| Add Accruals, Beta, Leverage, or RD | long-only `d_t` > 0 in 1970–2024, 2010–2018, and 2015–2024 | **None did.** Not built. |

`BIG HiOP LoINV` is still the higher t-stat on this file (t = +2.81) and is
still not the strategy. Promoting it after seeing the table was forbidden when
it was first scored.

## 2026-10-04 — the rank beats a random BIG quintile; the panic-cut stays off

Logged in `research_data/trials.jsonl` before any return was read. Script:
`scripts/ff_random_null.py`. Sample **1963-07 through 2026-07** (757 months).
The August 2026 month stayed outside this look. Costs are the same 20 bps
round trip on 33%/yr and 200%/yr one-way turnover. The ledger rows carry
config hash `fd0e5831891d86ed`, the file as logged. A later edit added the
Sharpe point and the window label to the printout. The second run reproduced
the same verdict.

### Random quintile

The feasible form of audit FC-7 on these files. The name-level null in
`alpha/validation.py` needs the point-in-time panel, which is not in this
checkout. Here the eligible set is the five value-weight quintiles inside the
BIG size bucket. The ranked book always holds the top quintile. Each random
path draws one of the five uniformly: quality once per July–June cohort,
momentum every month, 200 paths, seed 11. The same cost drag is charged on
the ranked book and on every random book. Random books pay no extra switch
cost, which favors the null. The bar is the audit's: terminal wealth at or
above the **80th percentile**.

| book | percentile | CAGR | random median CAGR |
|---|---|---|---|
| quality, BIG HiOP | **97.5th** | 11.56% | 10.04% |
| momentum, BIG HiPRIOR | **99.5th** | 13.17% | 9.51% |
| **50/50 blend** | **100th** | **12.49%** | **9.90%** |

All three clear. The blend finished above every one of the 200 random paths.
The pre-registered rule was to report a leg under the bar and leave its weight
alone. None was under the bar, and no weight moved.

This is the gap between the retired technical score and this book. The old
score-ranked portfolio sat at the **53rd percentile** of random names
(`docs/AUDIT-2026-07-24.md`, FC-7). The quintile rank is the rule that clears
the same kind of question on the published large-cap portfolios.

Boundary: a quintile draw is coarser than drawing names. The spent 2019–2026
panel null stays a separate, already-logged test (profitability was 100th
in-sample and 88th on that window). This section leaves those two numbers in
their original logs.

### Panic-cut, left off

Daniel & Moskowitz, *Journal of Financial Economics* 2016, forecast momentum
crashes in panic states: a trailing 24-month market decline, and high market
variance. Their dynamic weight is fit to the long-short WML portfolio, with
GJR-GARCH coefficients estimated on the same sample they score. That fit is
not portable onto a long-only monthly series, and re-estimating it here would
be another in-sample search.

The pre-registered translation uses no estimated coefficient. Momentum weight
goes to 0, and quality to 1, only when both of these are known from returns
before the month:

- the prior 24-month market total return is negative, and
- the prior 6-month market variance (population, ddof=0) is above the median
  of earlier variance readings, with at least 60 of those readings.

Otherwise the weight stays 50/50. A change in the momentum weight pays 20 bps
on the one-way turnover. The rule fired in **82** of 757 months.

| | unscaled | panic-cut | gate |
|---|---|---|---|
| alpha t | **+2.54** | +2.49 | t ≥ 2 passed; t above the unscaled book **failed** |
| alpha/yr | +1.77% | +1.73% | |
| worst month | −23.73% | −23.73% | a less severe worst month **failed** |
| excess, 2016-01 → 2026-07 | +0.79 pp | +0.88 pp | within 1 pp, passed |
| Sharpe, 95% CI | 0.55, **+0.29 to +0.80** | 0.54, **+0.29 to +0.80** | |
| family-wise p, 22 `rmx` trials | **0.215** | 0.249 | p ≤ 0.05 **failed** |

Market Sharpe on the same window is 0.47, CI **+0.21 to +0.72**. The intervals
overlap. Required t at 22 trials is 3.04. E[max t] under the null is 1.94.
**VERDICT: LEAVE OFF.** `rmxb.py` is unchanged.

The published crash sits in the short leg: past losers rebound after a decline
when volatility is high. This book is long the winners only. Cutting those
winners in panic months gave back alpha and left the worst month at −23.73%.

### Rules that stay closed

Searched before this run. Each one already has a measured fate, so none of
them was opened as a fresh trial.

| proposed change | why it stays closed |
|---|---|
| Add value beside gross profitability | Novy-Marx, "The Quality Dimension of Value Investing," finds a long-only gain on the full sample. This repo's long-only screen already found the classic value ratios dead from 2010 onward. The modern window governs. |
| Replace 12-1 momentum with months 7–12 only | Asness, Moskowitz & Pedersen: the skip-month 12-1 is the standard, and averaging reasonable measures is the guard against mining. Goyal & Wahal did not replicate the 7–12 version internationally. |
| Constant-vol scaling of the momentum leg | Barroso & Santa-Clara. Run here on 2026-10-02. Alpha t rose to +2.57 and the worst month went from −23.73% to −24.00%. Left off. |
| Promote BIG HiOP LoINV | Higher t (+2.81 on the August vintage). Choosing it after seeing the table was forbidden in the pre-registration. |

The rule that beats a random BIG quintile is the one already shipped: the top
operating-profitability quintile, the top 12-1 momentum quintile, 50/50,
cap-weighted, large cap, next close.

---

## Reproducing all of it

```bash
# 1. data (once; ~45 min for the Yahoo bulk fetch)
py scripts/fetch_history.py                 # 1,213 ever-S&P500 tickers, full history
# NB (2026-08-01): without --force this SKIPS every ticker already on disk and
# refreshes nothing (measured: ok=2 miss=428 skip=783, exit 0). It back-fills
# missing names only. To bring an existing cache up to date you need
# `--force`. See docs/GOLIVE-RUNBOOK.md.
py scripts/fetch_sec_fundamentals.py        # SEC XBRL, 2009-2025, point-in-time

# 2. integrity checks
py -m rhengine.alpha.panel                  # coverage by year, PIT universe
py scripts/rmx_bias.py                      # survivorship inflation vs RSP
py tests/test_alpha_pit.py                  # incl. test_universe_is_point_in_time
py tests/test_alpha_stack.py

# 3. the screens that rejected things
py scripts/rmx_ic.py     --lo 2005-01-01 --hi 2015-12-31    # 8 price signals
py scripts/rmxq_ic.py    --lo 2010-07-01 --hi 2018-12-31    # 5 fundamental signals
py scripts/ff_factor_check.py                               # French 1963-2026

# 4. the two strategies, full gate reports
py scripts/rmxd_report.py --hi 2015-12-31
py scripts/rmxd_report.py --lo 2014-01-01 --hi 2026-07-24 --book-lo 2016-01-01 --oos
py scripts/rmxq_report.py --lo 2010-07-01 --hi 2018-12-31 --weighting equal
py scripts/rmxq_report.py --lo 2019-01-01 --hi 2026-07-25 --weighting equal --oos

# 5. RMX-QT, the surviving strategy
py scripts/ff_largecap_quality.py                            # the 63-year evidence
py -m rhengine.alpha.rmxq --frozen --lo 2010-07-01 --hi 2018-12-31
py scripts/rmxqt_power.py                                    # why 8 years cannot prove it
```
