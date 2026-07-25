# RMX — results

**2026-07-25.** Read this first. Full method, every trial and every rejection:
`docs/RMX-PREREGISTRATION.md`. Why the previous system was thrown out:
`docs/AUDIT-2026-07-24.md`.

---

## The answer

**No strategy tested here beats the S&P 500 out-of-sample.** Four hypotheses
were pre-registered and measured; the two that survived in-sample both failed
their single holdout look.

| hypothesis | in-sample | out-of-sample | verdict |
|---|---|---|---|
| Residual momentum at the cited horizon | rank-IC t = **+0.35** | not reached | rejected |
| 8 price-only cross-sectional signals | best \|t\| = **0.89** | not reached | rejected |
| **RMX-D** trend + absolute-momentum timing | +0.94 pp/yr vs S&P, alpha t=+2.07, **8/11 gates** | **−6.22 pp/yr**, alpha t=+0.07, **1/11 gates** | rejected |
| **RMX-Q** operating-profitability tilt | +2.66 pp/yr vs SPY, alpha t=+2.48, **8/11 gates** | **−2.03 pp/yr**, alpha t=−0.48 | rejected vs SPY |

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

## What would actually be needed

1. **A cap-weighted tilt instead of a top-decile cut.** The signal is real and
   small; a construction that holds beta ≈ 1 and cap-weighted structure while
   tilting toward profitability never pays the size drag. **This has not been
   tested and must not be tested on 2019–2026, which is now spent.** It needs a
   fresh forward window.
2. **Fundamentals before 2009.** SEC XBRL starts in 2009, so the whole
   fundamental test covers one bull market and one real bear. Compustat/CRSP
   back to 1963 would settle it; free sources will not.
3. **Breadth past 500 large caps.** Every documented factor is stronger in small
   and mid caps — exactly where this dataset has neither coverage nor credible
   cost assumptions.

## What must not happen

Tuning RMX-D or RMX-Q against the results above and re-reporting them. Both
holdouts are spent. The next credible evaluation is forward, in real time, on a
configuration frozen before the sessions occur.

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
```
