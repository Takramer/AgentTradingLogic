# RMX — results

**2026-07-25.** Read this first. Full method, every trial and every rejection:
`docs/RMX-PREREGISTRATION.md`. Why the previous system was thrown out:
`docs/AUDIT-2026-07-24.md`.

---

## The answer

**One strategy survives: RMX-QT, a cap-weighted operating-profitability tilt
inside S&P 500 large caps.** It beat the market in **all five eras from 1963 to
2026** on Ken French's survivorship-free data — and the edge is small enough
that no honest test on the data available here can *prove* it, which is stated
rather than hidden.

Four other hypotheses were pre-registered and rejected on the numbers.

| hypothesis | in-sample | out-of-sample | verdict |
|---|---|---|---|
| Residual momentum at the cited horizon | rank-IC t = **+0.35** | not reached | rejected |
| 8 price-only cross-sectional signals | best \|t\| = **0.89** | not reached | rejected |
| **RMX-D** trend + absolute-momentum timing | +0.94 pp/yr, alpha t=+2.07, 8/11 gates | **−6.22 pp/yr**, alpha t=+0.07, **1/11 gates** | rejected |
| **RMX-Q** profitability tilt, *equal*-weighted | +2.66 pp/yr, alpha t=+2.48, 8/11 gates | **−2.03 pp/yr** vs SPY | rejected — see below |
| **RMX-QT** profitability tilt, *cap*-weighted | +0.50 pp/yr, alpha t=+0.79 | 63-year record, all 5 eras | **kept** |

---

## RMX-QT — the strategy

```
annually, on the last trading session of June, executed at the next close:

    take the point-in-time eligible S&P 500 members
    rank them by operating profitability
        OP/BE = (revenue - COGS - SG&A - interest) / book equity
        from the last fiscal year published at least six months earlier
    hold the top quintile, CAP-weighted, no name above 10%
```

~64 names, **33%/yr turnover**, two parameters, both from Fama-French. Formed
2026-06-30 it holds LLY, AAPL, NVDA, AMZN at the 10% cap, then LRCX, ABBV,
ORCL, HD, MRK.

**The evidence — French's 25 size × profitability portfolios, value-weighted,
1963–2026, survivorship-free, nothing of mine fitted to it:**

| era | BIG HiOP | market | Sharpe | alpha/yr | t |
|---|---|---|---|---|---|
| 1963–1979 | 7.29% | 7.08% | 0.18 vs 0.17 | +0.34% | +0.25 |
| 1980–1999 | 18.58% | 17.43% | 0.73 vs 0.69 | +1.15% | +0.93 |
| 2000–2015 | 4.94% | 4.43% | 0.30 vs 0.25 | +0.90% | +0.78 |
| **2016–2026** | **16.74%** | **15.22%** | 0.94 vs 0.84 | +2.16% | +1.41 |
| **1963–2026** | **11.68%** | **10.90%** | **0.52 vs 0.47** | **+1.19%** | **+1.71** |

Beta 0.93. It beats the market in **every** era — including the mega-cap decade
that destroyed every other idea tested here.

### Why cap-weighting is the whole strategy, not a detail

RMX-Q used the identical signal, equal-weighted. Out-of-sample the signal
earned a real **+1.62%/yr** against its own universe (88th percentile of 200
random draws) — and the portfolio still lost to SPY:

```
equal-weight covered universe   12.41%/yr
+ profitability tilt            + 1.62%/yr   <- the real edge
RMX-Q                            13.66%/yr
SPY (cap-weighted)               15.68%/yr   <- equal-weighting cost 3.3 pp/yr
```

At beta 0.93 there is no size bet left to lose. That is the entire fix.

### The honest limit — this edge cannot be proven on the data that exists

`scripts/rmxqt_power.py`. In-sample 2011–2018 RMX-QT returned +0.50 pp/yr with
alpha t = **+0.79**. That looks like a failed replication. It is not:

| true alpha/yr | tracking error | expected t over 7.5 yr | years needed for t=2 |
|---|---|---|---|
| +0.78% | 4.14% | 0.52 | 112 |
| **+1.19%** (French's) | 4.14% | **+0.79** | **48** |
| +2.00% | 4.14% | 1.32 | 17 |

**Observed t = +0.79. Expected t if French's alpha is exactly true = +0.79.
P = 0.50 — the median outcome.** The implementation is statistically
indistinguishable from the 63-year estimate; it simply has no power to confirm
it. An alpha of ~1.2 pp/yr against a 4.1 pp/yr tracking error needs about **48
years** to reach t = 2.

So the claim is: *the 63-year record is the evidence; the S&P 500
implementation is shown to be consistent with it.* Not the other way round.
Anyone quoting significance for a factor tilt on a decade of data is
overfitting or overclaiming.

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
cover it. Correcting to French's value-weighted construction did not help
(in-sample +0.54 pp/yr; on the by-then-burned 2019–2026 window, −3.45 pp/yr).

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

---

## Reproducing all of it

```bash
# 1. data (once; ~45 min for the Yahoo bulk fetch)
py scripts/fetch_history.py                 # 1,213 ever-S&P500 tickers, full history
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
