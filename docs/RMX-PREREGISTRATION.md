# RMX — pre-registration

**Written:** 2026-07-24, **before any result on the new dataset was observed.**
**Authority:** `docs/AUDIT-2026-07-24.md` (§7 O-1..O-4, §8 Phase 1–2, §10 G1).

This file exists so that the trial count is knowable and the parameters are
attributable to published literature rather than to this sample. It is
append-only. Every run against the OOS window must be logged in
`research_data/experiments.jsonl` **before** it executes.

---

## 0. Why a new strategy rather than more tuning

The audit killed the deployed system on evidence, not opinion:

- the headline book was gross of cost and un-capitalised (FC-1, FC-2);
- 94% of it was `residualmomentum`, whose day-clustered t is **+1.32** and whose
  implementation transposed the cited paper's **months into days** (FC-8);
- selection ranking sits at the **53rd percentile of random** (FC-7);
- the universe was survivorship-biased and chosen on full-sample P&L (FC-4);
- post-beta alpha t was **+1.78** against a multiple-testing bar of ~2.9 (FC-14).

Commit `45fb1f0` then showed that fixing the horizon (O-1) did **not** rescue it.
That result is not evidence the hypothesis is wrong — it is evidence the **data
was too short to test it**. The cache spans 523 sessions. A 36-month beta window
plus a 12-month formation window consumes 48 months before the first signal
fires. O-1 could not have produced a valid answer on 24 months of data.

So the first move is data, not code: 30 years of point-in-time history.

---

## 1. Dataset (new, built 2026-07-24)

| Item | Source | Span | Notes |
|---|---|---|---|
| PIT S&P 500 membership | `fja05680/sp500` daily component snapshots | 1996-01-02 → 2026-06-30 | 2,718 change-dates, 1,206 distinct tickers ever |
| Daily OHLCV + adj close | Yahoo chart API, full history per ticker | per-ticker max | 1,206 ever-members + SPY/^GSPC/^VIX |
| Daily factors + risk-free | Ken French data library | 1926 → 2026-05 | `RF` is the cash leg; `Mkt-RF` the market factor |
| Survivorship-free cross-check | repo `massive_cache` grouped-daily | 2024-07-18 → 2026-07-23 | whole-market snapshots, no survivor filter |

**Known bias, quantified not assumed:** Yahoo drops most bankrupt/delisted
symbols (verified: ENRNQ, LEHMQ, WCOM, ABK all return no data). Every rebalance
therefore records `coverage = priced members / PIT members`; the run aborts if
coverage falls below 0.85 in any year, and the coverage series is reported.
Mitigations:

1. The primary alpha test is against an **equal-weight PIT-universe benchmark
   built from the same priced names**, so the identical bias sits on both sides.
2. Names that vanish from the price file while still index members are booked at
   a **−30% delisting return** in their final month (Shumway 1997 convention),
   rather than silently dropped.
3. SPY comparison is reported but labelled as carrying the bias.

---

## 2. Strategy specification — frozen before first run

### 2.1 Universe, at each rebalance date `D`, using only data ≤ `D`

- member of the S&P 500 per the PIT file as of `D`;
- close ≥ $5;
- ≥ 756 trading days of price history (beta window);
- median 60-day dollar volume ≥ $5,000,000 (audit O-3: the old $500k floor
  admitted names where 30–100 bps round-trip is normal).

### 2.2 Core signal — residual momentum at the **cited** horizon (fixes FC-8)

Blitz, Huij & Martens, *Residual Momentum*, J. Empirical Finance 18(3) 2011,
https://doi.org/10.1016/j.jempfin.2011.01.003 — 36-month estimation, 12-month
formation, 1-month skip, ranked on residual return **scaled by residual
standard deviation**.

1. Monthly excess returns, 36 months ending `D`: regress `r_i − rf` on
   `mkt − rf` → `α_i, β_i, ε_i`. Series joined **on date**, not by position
   (fixes FC-9). Require ≥ 30 of 36 monthly observations.
2. Formation: residuals over months `D−12 … D−2` (1-month skip).
3. `RESMOM_i = mean(ε_i over the 11 formation months) / std(ε_i over the same)`.

### 2.3 Candidate legs — each is one logged trial, tested one at a time

| Leg | Definition | Source in this repo | Literature prior |
|---|---|---|---|
| **L1 · reversal** | z-score of −1 × last-month return | `bollinger` / RSI oversold leg | Jegadeesh 1990 https://doi.org/10.1111/j.1540-6261.1990.tb05110.x |
| **L2 · low residual vol** | z-score of −1 × std(ε) over 36m | audit O-4 diversifier | Ang, Hodrick, Xing & Zhang 2006 https://doi.org/10.1111/j.1540-6261.2006.00836.x |
| **L3 · trend gate** | exposure → 0 when SPY < 200d SMA **and** SPY 12m return < 0 | `classify_spy_regime`, `macd` SMA legs | Faber 2007 https://doi.org/10.2139/ssrn.962461 |
| **L4 · vol target** | scale exposure by `min(1, 12% / realised 60d strategy vol)`; de-risk only, never lever | new | Barroso & Santa-Clara 2015 https://doi.org/10.1016/j.jfineco.2014.11.003 |

Composite score = equal-weight average of the z-scores of the legs that are on.
Long the top `N = 50` names, equal weight. No shorts (the account is long-only).

### 2.4 Execution

- Rebalance on the **last trading day of each month**; signals use data through
  that close; fills at the **next session's close**. One full day of delay.
- Cost = `turnover × round_trip_bps`, charged on the rebalance day.
  Headline **10 bps** round trip (S&P 500 large caps with a $5M ADV floor);
  the full curve 0/5/10/20/50/100 bps and the breakeven are always reported.
- Cash (when L3/L4 de-risk) earns the Ken French daily `RF`.
- Equity is marked **daily, mark-to-market** (fixes FC-13).

---

## 3. Windows

### 3.1 Amendment A — 2026-07-24, made on a DATA measurement, before any
### strategy result was computed

The download finished at **781 of 1,213 ever-members priced (64%)**. Measured
priced-member coverage per §1 turned out far worse than the 0.85 floor assumed
when this file was written:

| year | 1996 | 2000 | 2005 | 2010 | 2015 | 2019 | 2022 | 2026 |
|---|---|---|---|---|---|---|---|---|
| coverage | 0.38 | 0.48 | 0.55 | 0.68 | 0.76 | 0.85 | 0.92 | 0.99 |

Two mechanisms, not one. Bankrupt and acquired names are simply gone (verified:
ENRNQ, LEHMQ, WCOM, ABK all return nothing). Separately, **tickers get reused** —
`AAL` was Alexander & Alexander in 1996 and is American Airlines now — so 92 of
the 1996 members resolve to a symbol whose modern price series does not reach
back that far. The second mechanism is the more dangerous one, because a reused
ticker whose modern owner *did* trade in 1996 would silently attribute the wrong
company's returns; it is contained here only because the history requirement
(756 sessions of the same series) drops those names.

Pre-2005 the priced subset is barely half the index. Numbers computed there are
not evidence about anything. The windows are therefore moved:

| Window | Span | Coverage | Use |
|---|---|---|---|
| **IS** | 2005-01-01 → 2015-12-31 | 0.55 → 0.76 | development, leg selection, all iteration |
| **OOS** | 2016-01-01 → 2026-07-24 | 0.79 → 0.99 | looked at **once**, after IS is frozen |
| DEEP | 1999-01-01 → 2004-12-31 | 0.46 → 0.55 | supplementary only, never a promotion input |

IS spans the 2008 crisis, the 2009 momentum crash, 2011 and 2015 — 3 independent
bear episodes, the audit's §10 G1.3 requirement that a 213-day sample could
never meet. OOS spans 2018Q4, the 2020 COVID crash and the 2022 bear, and has
the *better* data, which is the right way round: the claim should rest on the
cleaner half.

### 3.2 Survivorship bias is measured, not asserted

Two independent measurements, both reported with every result:

1. **EW(priced PIT members) − RSP.** RSP is the real equal-weight S&P 500 ETF
   (2003→). The gap between an equal-weight book over the *priced subset* and
   the actual equal-weight index **is** the survivorship inflation, in
   percentage points per year. It is subtracted from the SPY comparison as a
   haircut scenario.
2. **Strategy − EW(priced PIT members).** Both sides draw from the identical
   biased set, so this difference is the cleanest alpha estimate available here
   and is the **primary** number. The SPY comparison is secondary and labelled.

## 4. Trial budget

Pre-declared trials: **1 baseline + 4 single-leg + 1 combined = 6.** Any trial
beyond this must be appended here with its justification before running. The
multiple-testing bar applied to the final alpha t-stat is Bonferroni over the
*actual* logged trial count.

## 5. Acceptance gates (audit §10 G1, adapted to a monthly long-only book)

A strategy is reported as "beats the S&P" only if **all** hold:

1. IS **and** OOS net total return > SPY over the same window, at 10 bps.
2. Day/month-clustered alpha t ≥ 2.0 vs SPY, net of 10 bps, in **IS**.
3. Alpha t ≥ 2.0 vs the **equal-weight PIT universe** (survivorship-neutral).
4. Net return still positive at 50 bps round trip.
5. Purged walk-forward: ≥ 70% of folds beat SPY, worst fold > −5% vs SPY.
6. Parameter plateau: ±20% on N, formation length and vol target keeps the
   OOS-vs-SPY sign.
7. Block-bootstrap MC: P(underperform SPY) ≤ 30%.
8. Score-ranked selection ≥ 80th percentile of 200 random-N-from-universe runs.
9. Deflated Sharpe > 0 at the logged trial count.
10. Max drawdown ≤ SPY's over the same window.

A result that fails any of these is reported as a failure, with the number.

---

## 6. Amendment B — 2026-07-24 · the cross-sectional hypothesis is rejected

**Trials 1–9 are spent. Result: no cross-sectional stock-selection signal in
this universe has usable predictive power.**

### Screen S1 — eight price-only signals, IS window, 131 monthly cross-sections

Rank-IC against the *next* month's return, mean universe 315 names. t-stats on
the monthly IC series (n = 131 months — the honest effective sample, not 41,000
name-months):

| signal | mean IC | IC t | top-decile minus universe |
|---|---|---|---|
| risk-adjusted 12-1 momentum | +0.0145 | **+0.89** | −0.34%/mo |
| mom 12-1 (Jegadeesh & Titman) | +0.0127 | +0.72 | −0.27%/mo |
| **residual momentum (the RMX core)** | **+0.0046** | **+0.35** | −0.23%/mo |
| 52-week-high proximity | +0.0060 | +0.30 | −0.22%/mo |
| 1-month reversal | +0.0070 | +0.50 | −0.27%/mo |
| low residual volatility | +0.0017 | +0.09 | −0.09%/mo |
| low beta | +0.0013 | +0.06 | −0.42%/mo |
| long-term reversal | −0.0131 | −1.02 | −0.14%/mo |

Not one clears t = 1.0, let alone the t = 2.0 gate, and **every** signal's top
decile *underperformed* the universe mean. The IC sign is not even stable:
residual momentum runs −0.011 / −0.016 / +0.012 / +0.029 across the four 3-year
blocks — negative through 2009, positive after.

### Trial 1 — the frozen RMX baseline, run anyway

| | total | CAGR | vol | Sharpe | maxDD |
|---|---|---|---|---|---|
| RMX resmom, 50 names, 10 bps | +101.0% | 6.61% | 22.4% | 0.34 | −58.7% |
| SPY | +115.6% | 7.29% | 20.0% | 0.39 | −55.2% |
| EW universe (same names) | +159.4% | 9.13% | 22.3% | 0.45 | −58.9% |

Alpha vs SPY **−0.56%/yr (t = −0.27)**; vs the equal-weight universe
**−1.61%/yr (t = −0.88)**. Turnover 452%/yr one-way.

**Verdict: the residual-momentum sleeve is dead at the literature horizon too,
and the reason is not the implementation — the signal has no rank information
in a large-cap universe.** This is consistent with the published record:
momentum is concentrated in small and illiquid names, and the 2005–2015 window
contains the 2009 momentum crash. Fixing FC-8's units did not help because
FC-8 was not the binding constraint.

Per audit §8 Phase 1's kill condition ("if post-Phase-1 alpha t < 1.0, stop"),
cross-sectional selection is **abandoned**, not tuned. No further trials will
be spent on ranking stocks.

## 7. Amendment C — the surviving hypothesis, frozen before its first run

What the same measurements *did* show is that the money in this dataset is in
**exposure**, not in selection: the strategy's drawdown (−58.7%) and SPY's
(−55.2%) are the dominant facts, and the one component never yet tested is the
decision of *how much to be invested*.

That component also has the one thing the rest of this project has never had:
**a long, survivorship-free test set.** Index levels and total-return funds do
not suffer from delisting bias at all, so the timing rule can be tested on:

| series | span | what it gives |
|---|---|---|
| `^GSPC` | 1970 → 2026 | 56 years, ~9 bear markets, price-only |
| `VFINX` | 1980 → 2026 | 46 years of S&P 500 **total** return |
| `VUSTX` / `VFITX` | 1986 / 1991 → | Treasury total return for the risk-off leg |

### RMX-D specification — frozen now

1. **Core exposure** — the S&P 500 (SPY / VFINX total return).
2. **L3 trend gate** — monthly: risk-on while the index closes above its
   10-month moving average. Faber 2007, https://doi.org/10.2139/ssrn.962461.
3. **L4 volatility target** — scale by `min(1, 0.12 / realised 60-day vol)`;
   de-risk only, never lever. Barroso & Santa-Clara 2015.
4. **Risk-off asset** — intermediate Treasuries **only when their own 12-month
   absolute momentum is positive**, else cash at `RF`. Antonacci 2014; the
   momentum condition is what stops the bond leg from repeating 2022.

Parameters: 10-month SMA, 12% vol target, 60-day vol window, 12-month absolute
momentum. Four numbers, all published, none chosen from this sample.

### Kill criteria, declared before the run

- If RMX-D's **Calmar** does not exceed the index's in **both** halves of the
  1970–2015 record, it is rejected.
- If it needs more than the four parameters above to work, it is rejected.
- If its return advantage disappears when the trend rule is moved to 8 or 12
  months, it is rejected as a parameter artefact.

## 8. Amendment D — two of the four RMX-D rules are rejected, in-sample

### L4 volatility targeting — **REJECTED**

On VFINX total return, 1980–2015, cash risk-off, no leverage:

| config | CAGR | maxDD | Sharpe |
|---|---|---|---|
| trend gate only | 10.06% | −33.1% | **0.48** |
| + 12% vol target | 8.89% | −27.4% | 0.46 |
| + expanding-window "index vol" target | 9.43% | −33.1% | 0.45 |
| buy & hold | 10.07% | −55.3% | 0.39 |

The target buys a little drawdown for a lot of return and *lowers* Sharpe. It
is dropped. Barroso & Santa-Clara's result is for a long-short momentum factor
whose volatility is far more forecastable than a long-only index's; importing
it here was not justified and the data says so.

### Leverage — **REJECTED, and it is the one that would have been easy to fake**

Raising the exposure cap does raise CAGR. It does it by lowering Sharpe and
deepening drawdown past the benchmark's:

| cap | CAGR | maxDD | Sharpe | vs B&H |
|---|---|---|---|---|
| 1.00 | 10.06% | −33.1% | **0.48** | −0.02 pp |
| 1.25 | 10.91% | −41.3% | 0.46 | +0.83 pp |
| 1.50 | 11.66% | −49.4% | 0.44 | +1.58 pp |
| 2.00 | 12.86% | −65.1% | 0.43 | **+2.79 pp** |

Reporting the 2.0× row as "+2.79 pp/yr versus the S&P" would be a headline and
a lie: at 2× the book is riskier than the index it is beating, with a **worse
maximum drawdown than buy-and-hold**, and the Sharpe is *falling* the whole way
down the table because 200 bps of margin interest eats the difference. Leverage
is not alpha. Cap stays at 1.0.

### The SMA parameter is a plateau, not a point

CAGR vs buy-and-hold at cap 1.0, no vol target: 8-month +0.14 pp, 10-month
−0.02 pp, 12-month −0.05 pp; Sharpe 0.50 / 0.48 / 0.47 against 0.39. The result
does not hinge on the number.

## 9. FROZEN SPECIFICATION — RMX-D, 2026-07-24

Everything below is fixed before the out-of-sample window is opened.

**Instruments: SPY and IEF only.** Because cross-sectional selection was
rejected, the equity leg is the index itself — which means *the deployed
strategy has no survivorship bias at all*, the defect that invalidated every
previous result in this repository.

```
monthly, on the last trading session, executed at the NEXT session's close:

  if  SPY month-end close  >  10-month moving average of its month-end closes:
        100% SPY
  else if IEF trailing 12-month total return > 0:
        100% IEF
  else:
        100% cash at the risk-free rate
```

Long-history proxies for testing: VFINX (S&P 500 total return, 1980→) and
VFITX (intermediate Treasury, 1991→); ^GSPC (1970→) for the price-only check.

| parameter | value | source |
|---|---|---|
| trend window | 10 months | Faber 2007 |
| absolute-momentum window | 12 months | Antonacci 2014 |
| leverage | none | rejected above |
| vol target | none | rejected above |
| cost | 10 bps round trip headline, curve to 100 bps | audit FC-2 |

**In-sample result, 1980–2015 (36 years, survivorship-free):**

| | CAGR | vol | Sharpe | maxDD | Calmar |
|---|---|---|---|---|---|
| RMX-D (IT Treasuries) | **11.01%** | 13.3% | **0.54** | **−33.1%** | 0.33 |
| RMX-D (LT Treasuries) | 11.49% | 14.1% | 0.55 | −33.1% | 0.35 |
| trend-only, cash risk-off | 10.06% | 13.1% | 0.48 | −33.1% | 0.30 |
| S&P 500 buy & hold | 10.06% | 17.9% | 0.39 | −55.3% | 0.18 |

Alpha vs the S&P **+3.39%/yr, t = +2.07**, beta 0.54, turnover 90%/yr one-way.

### The one thing that could still be wrong

The bond leg's entire test history (VFITX from 1991, VUSTX from 1986) sits
inside the greatest bond bull market on record. The 12-month absolute-momentum
condition exists precisely to stop the defensive leg becoming a naked duration
bet, but **it has never been tested in a sustained rising-rate regime**. The
out-of-sample window contains 2022, the worst year for Treasuries in modern
history. That is the single most informative test available and it has not been
looked at yet.

## 10. Amendment E — RMX-D fails out-of-sample; the quality hypothesis replaces it

### The single pre-registered OOS look, 2016-01 → 2026-07, executed once

| | CAGR | vol | Sharpe | maxDD |
|---|---|---|---|---|
| RMX-D | 9.24% | 12.6% | 0.58 | **−26.8%** |
| S&P 500 buy & hold | **15.46%** | 17.9% | **0.76** | −33.8% |

Alpha **+0.22%/yr, t = +0.07**. **1 of 11 gates met** — only "max drawdown ≤
index". It lost 6.22 pp/yr and lost on Sharpe as well.

The most informative single number is the random-timing null: the trend rule
sat at the **4th percentile** of 200 permutations that held the same number of
invested months in a different order. In-sample it sat at the 98th. The signal
genuinely helped for 35 years and genuinely hurt for the last 10. 2022 is the
specific failure the pre-registration warned about: the bond leg's 12-month
absolute-momentum gate did **not** prevent −21.4% against the index's −18.2%.

Full period 1981–2026: CAGR 10.46% vs 11.15%, Sharpe 0.54 vs 0.47, maxDD −33%
vs −55%, alpha +2.59%/yr t=+1.78, **4/11 gates**. Deployable SPY/IEF form
2003–2026: 9.06% vs 11.20%, **3/11 gates**.

**RMX-D is rejected as a way to beat the S&P.** It is retained only as what it
demonstrably is: a defensive overlay that halves drawdown and costs return.
No parameter of it will be adjusted in response to this result — that would
convert the holdout into a training set, which is exactly the disease the audit
diagnosed.

### Why quality/profitability is the next hypothesis and not a fishing trip

Audit §7 O-4 named "cross-sectional quality/profitability (Novy-Marx 2013)" as
the highest-justified addition **before any of this work started**. What was
missing was point-in-time fundamentals. Two things changed:

1. **Independent evidence, from the canonical source, over 63 years.** Ken
   French's own long-only top-30%-operating-profitability portfolio versus the
   market — no cost assumption of mine, no survivorship bias, nothing I fitted:

   | era | top-30% OP CAGR | market CAGR | top Sharpe | mkt Sharpe | excess t |
   |---|---|---|---|---|---|
   | 1963–2000 | 13.13% | 11.91% | 0.47 | 0.41 | +2.10 |
   | 2001–2015 | 6.96% | 5.60% | 0.46 | 0.34 | +1.13 |
   | **2016–2026** | **17.35%** | **15.22%** | 0.97 | 0.84 | +1.73 |
   | **1963–2026** | **12.30%** | **10.90%** | 0.55 | 0.47 | **+2.88** |

   It beats the market in **every** era, on return *and* Sharpe, with beta ≈ 1
   — which is precisely what RMX-D could not do. For contrast, in the same
   files value is dead (HML +4.80% t=3.25 in 1963–2000, **−0.64% in 2016–2026**)
   and investment is dead (CMA +3.73% → **−0.79%**). Only RMW survives all
   three eras.

2. **The data now exists here.** SEC XBRL annual frames, 2009–2025, 13
   concepts, ~100k firm-years, with the Fama-French 6-month publication lag
   applied so a fiscal year is unusable until 1 July of the following year.

### Honest statement of what my own test can and cannot prove

I have now seen that 2016–2026 was an exceptional mega-cap bull and that
profitability worked through it. That knowledge cannot be unseen. So the
evidentiary weight sits where it belongs: **French's 63-year record is the
evidence; my S&P 500 run is a replication check** — can this be implemented in
a liquid large-cap universe, at realistic cost, with point-in-time data, and
still beat SPY? Every parameter comes from Novy-Marx or French, none from my
sample.

### RMX-Q specification — frozen before its first run

- **Universe:** the point-in-time eligible S&P 500 set already defined in §2.1.
- **Signal:** gross profitability `GP/A = (revenue − COGS) / assets`
  (Novy-Marx 2013), computed from SEC XBRL with the six-month lag.
- **Portfolio:** top 30% by GP/A, equal-weighted, **annual** rebalance at the
  end of June (French's formation convention).
- **No timing overlay, no leverage, no vol target, no stock-level momentum.**
- **Split:** IS 2010-07 → 2018-12; **OOS 2019-01 → 2026-07, one look.**
- **Kill criteria, declared now:** rank-IC t < 1.5 in-sample with Newey-West(12)
  standard errors, *or* the long-only portfolio fails to beat SPY in-sample net
  of 10 bps → the hypothesis is dropped and the project reports a negative
  result.

## 11. Amendment F — RMX-Q results, and the final verdict

### In-sample 2010-07 → 2018-12 (equal weight, as specified)

| | CAGR | vol | Sharpe | maxDD |
|---|---|---|---|---|
| **RMX-Q** | **13.80%** | 14.5% | **0.94** | −19.9% |
| SPY | 11.14% | 14.6% | 0.77 | −19.4% |
| EW covered universe | 11.43% | 14.9% | 0.78 | −20.6% |

Alpha vs SPY **+2.67%/yr t=+2.48**; vs the equal-weight covered universe
**+2.58%/yr t=+2.76**. Beat SPY in **7 of 8 years**. Turnover 33%/yr, so the
whole cost curve from 0 to 100 bps moves the result by 0.37 pp. Random-selection
null: **100th percentile** of 200 draws of the same number of names from the
same universe. **8/11 gates.**

### Out-of-sample 2019-07 → 2026-07, opened once

| | CAGR | Sharpe | maxDD |
|---|---|---|---|
| RMX-Q (equal weight) | 13.66% | 0.61 | −34.0% |
| **SPY** | **15.68%** | **0.69** | −33.7% |
| EW covered universe | 12.41% | 0.54 | −37.5% |

**The signal held. The portfolio lost.**

- alpha vs the equal-weight covered universe **+1.62%/yr, t=+1.36**, and the
  random-selection null still puts it at the **88th percentile** — so ranking by
  operating profitability really did pick better names than chance, in a window
  it was never fitted to;
- alpha vs SPY **−1.01%/yr, t=−0.48**; CAGR −2.03 pp/yr, −2.89 after the
  survivorship haircut.

The arithmetic is unambiguous: the equal-weight covered universe returned
12.41% against SPY's 15.68%. The construction carried a **−3.3 pp/yr size
drag** that a +1.6 pp/yr signal could not cover. It beat SPY in 2022 (−11.5%
vs −18.2%) and lost in 2023, 2024 and 2025 — the mega-cap years.

### The value-weighted correction, and why it does not rescue anything

French's published portfolios are value-weighted; specifying equal weight was
my deviation. Correcting it: in-sample +0.54 pp/yr vs SPY (alpha +1.30%/yr,
t=+1.06); on 2019–2026, **−3.45 pp/yr, alpha vs the cap-weighted covered
universe −1.41%/yr, 0/11 gates.**

**That 2019–2026 number is not out-of-sample evidence** — by the time it was run
the window had been opened twice. It is reported because it is decisive in the
*negative* direction, and a result that only counts when it helps is not a
result. The honest reading is that the signal's contribution is not stable
across weighting schemes, which is itself evidence that it is weak.

---

## 12. FINAL VERDICT

**No configuration tested beats the S&P 500 out-of-sample under honest
measurement.** Four hypotheses, all pre-registered, all measured:

| # | hypothesis | verdict | the number that decided it |
|---|---|---|---|
| 1 | Residual momentum at the cited horizon | **rejected** | rank-IC t = +0.35 over 131 cross-sections |
| 2 | Any of 8 price-only cross-sectional signals | **rejected** | best \|t\| = 0.89; every top decile below its universe |
| 3 | Trend + absolute-momentum exposure timing (RMX-D) | **rejected vs SPY** | OOS −6.2 pp/yr, alpha t = +0.07, 4th percentile of random timing |
| 4 | Operating-profitability tilt (RMX-Q) | **signal real, portfolio loses** | OOS +1.62%/yr vs its own universe (88th pct of random) but −2.03 pp/yr vs SPY |

### What is actually true, and is worth keeping

1. **Operating profitability ranks S&P 500 returns.** It beat random selection
   from its own universe at the 100th percentile in-sample and the 88th
   out-of-sample — two disjoint windows. Compare the old system's stock ranking,
   which the audit put at the **53rd** percentile. This is the first signal in
   this repository that has ever cleared its own null.
2. **It is worth about +1.5 to +2.6 pp/yr against its own universe** — real, and
   too small to overcome a −3.3 pp/yr weighting drag.
3. **Defensive timing halves drawdown and costs return.** Over 1981–2026 RMX-D
   returned 10.46% against 11.15% with a −33% worst drawdown against −55%.
   Useful if drawdown is the constraint; not a way to beat the index.

### What would be needed to actually beat the S&P, stated plainly

- **A cap-weighted tilt rather than a top-decile cut.** The signal is real but
  small; a construction that keeps beta ≈ 1 and cap-weighted structure while
  tilting toward profitability would not pay the −3.3 pp size drag. This is how
  quality ETFs are built. **It has not been tested here and must not be tested
  on 2019–2026, which is now burned.** It needs a fresh forward window.
- **Fundamentals before 2009.** SEC XBRL starts in 2009, so the entire
  fundamental test spans one uninterrupted bull market with a single real bear.
  Compustat/CRSP history back to 1963 would make this answerable; free sources
  will not.
- **Breadth beyond 500 large caps.** Every documented factor is stronger in
  small and mid caps, which is exactly where this dataset has neither coverage
  nor tradeable cost assumptions.

### What must not happen next

Adjusting any parameter of RMX-D or RMX-Q in response to the results above and
re-reporting them. The audit's central finding was that this project's numbers
came from decisions made by looking at the metric being reported. The holdout
for both strategies is now spent; the next credible evaluation is forward, in
real time, on a configuration frozen before the sessions occur.

## 14. Amendment H — a market-cap bug, and every number it invalidated

Found while diagnosing why the cap-weighted benchmark trailed SPY by 2.6 pp/yr:
**Yahoo's `close` is split-adjusted, and an SEC share count is not.**
Multiplying them made GE — after its 1:8 reverse split — a $1.5 trillion company
in 2016 that pinned the 10% position cap in every year of the backtest, while
Apple's 4:1 split hid the largest company in the index entirely. 501 of the 793
tickers in the panel have at least one split, so this was the normal case.

The fix needed two steps, because the first one alone was wrong: split events
were fetched (`scripts/fetch_splits.py`), and then the share counts had to be
disambiguated, since the SEC frames API returns whichever filing covered a
period and filers **restate** prior-year share counts after a split. Some values
in the same column are already split-adjusted and some are not. `adjusted_shares`
now walks each ticker backwards using continuity, anchoring on a price-to-sales
prior only where the most recent year is itself ambiguous. Validated against
known caps on four dates: every checked name now lands within 0.89–1.10× of its
actual market capitalisation, against errors of 10× before.

**Numbers previously reported that are now withdrawn and restated:**

| | was (buggy caps) | is (corrected) |
|---|---|---|
| RMX-QT in-sample vs SPY | +0.50 pp/yr, alpha t=+0.79 | **+3.17 pp/yr, alpha t=+1.74** |
| RMX-QT in-sample vs its own cap-weighted universe | not reported | +1.23%/yr, t=+0.80 |
| RMX-QT tracking error | 4.14%/yr | 4.53%/yr |
| years to reach t=2 | 48 | **58** |
| RMX-Q value-weighted on 2019-2026 | −3.45 pp/yr, 0/11 gates | **−1.99 pp/yr, 1/11 gates** |

The conclusions do not change, and that is worth stating plainly rather than
quietly: the corrected in-sample number is *better*, the corrected burned-window
number is *less bad*, and the strategy still cannot be shown to beat SPY on any
sample this project owns. A bug that flatters a result is the dangerous kind;
this one was suppressing a result and it was still caught by a sanity check
against known market caps, which is the only reason to run such checks.

## 15. Amendment I — RMX-B, and the only t ≥ 2 in this project

The single-sleeve problem is arithmetic: profitability is worth roughly
+1.2%/yr of alpha against a 4.5%/yr tracking error, an information ratio of
0.26, which needs ~58 years to reach t=2. No amount of care fixes that for one
sleeve. The fundamental law of active management says breadth does.

**Hypothesis, fixed before running:** a 50/50 blend of two long-only,
cap-weighted large-cap sleeves — operating profitability and 12-1 momentum —
beats the market with alpha t ≥ 2.0 over 1963–2026 and in a majority of eras.
Tested on French's 25 size × OP and 25 size × prior-return portfolios, net of a
deliberately punitive 20 bps round trip charged against 33%/yr (quality) and
200%/yr (momentum) one-way turnover.

Correlation of the two sleeves' excess returns: **+0.18.**

| | CAGR | market | alpha/yr | alpha t | IR | eras beaten |
|---|---|---|---|---|---|---|
| quality only | 11.61% | 10.90% | +1.13% | +1.62 | 0.22 | 4/4 |
| momentum only | 13.24% | 10.90% | +2.43% | +2.10 | 0.27 | 3/4 |
| **50/50 blend** | **12.55%** | **10.90%** | **+1.78%** | **+2.54** | **0.32** | **4/4** |

The blend's t-stat exceeds both of its legs, which is the mechanism working
rather than a lucky draw. Sharpe 0.55 vs 0.47, beta 0.97. **ACCEPTED** on the
pre-declared criterion (t ≥ 2.0 and ≥4 of 5 eras).

A secondary candidate, `BIG HiOP LoINV` (profitability × conservative
investment), scored higher still — +2.03 pp/yr, alpha t=+2.69, 4/4 eras — but
it was declared secondary and is *not* promoted, because choosing it after
seeing the table is exactly the selection this project exists to avoid.

**Implementation caveat, stated as loudly as the result:** RMX-B in this
project's own S&P 500 panel returns +1.02 pp/yr in-sample with alpha t=+0.42.
At a 39-year requirement for t=2, that is uninformative in both directions.

---

## Log

| # | Date | Trial | Window | Result |
|---|---|---|---|---|
| 0 | 2026-07-24 | Spec frozen; dataset built | — | 781/1213 names priced |
| S1 | 2026-07-24 | IC screen, 8 price-only signals | IS 2005-2015 | all \|t\| < 1.05 — **reject** |
| 1 | 2026-07-24 | RMX baseline, resmom, N=50, 10 bps | IS 2005-2015 | −1.61%/yr vs EW, t=−0.88 — **reject** |
| 2 | 2026-07-24 | RMX-D trend + 12% vol target, cash | 1970-2015 | Sharpe 0.46 < 0.48 — **reject L4** |
| 3 | 2026-07-24 | Leverage cap sweep 1.0/1.25/1.5/2.0 | 1980-2015 | Sharpe falls with cap — **reject** |
| 4 | 2026-07-24 | SMA plateau 8/10/12 x voltgt x cap (36 cells) | 1980-2015 | plateau confirmed |
| 5 | 2026-07-24 | T3 trend only, cash risk-off | 1980-2015 | alpha +2.34%/yr t=+1.64 |
| 6 | 2026-07-24 | T4 trend + LT Treasury risk-off | 1980-2015 | alpha +3.94%/yr t=+2.22 |
| 7 | 2026-07-24 | **T4b trend + IT Treasury — FROZEN AS RMX-D** | 1980-2015 | alpha +3.39%/yr t=+2.07 |
| 8 | 2026-07-24 | RMX-D robustness suite | 1980-2015 | 8/11 gates; timing at 98th pct of random |
| 9 | 2026-07-24 | **RMX-D single out-of-sample look** | **2016-2026** | **1/11 gates — REJECTED** |
| S2 | 2026-07-24 | Fundamental IC screen, 5 signals | IS 2010-2018 | only `op_be` clears (NW t=+2.52) |
| 10 | 2026-07-24 | RMX-Q equal weight | IS 2010-2018 | 8/11 gates; alpha vs SPY +2.67%/yr t=+2.48 |
| 11 | 2026-07-24 | **RMX-Q single out-of-sample look** | **2019-2026** | signal holds (88th pct) but **−2.03 pp/yr vs SPY** |
| 12 | 2026-07-25 | RMX-Q value weight (correction to French's construction) | IS 2010-2018 | +0.54 pp/yr vs SPY, alpha t=+1.06 |
| 13 | 2026-07-25 | RMX-Q value weight on the (now burned) 2019-2026 window | contaminated | **0/11 gates** — reported, not admissible |

| 14 | 2026-07-25 | **French 25 ME × OP — cap-weighted quality tilt inside large caps** | 1963-2026, never previously viewed | **PASS** — beats the market in all 5 eras, +0.78 pp/yr, alpha t=+1.71 |
| 15 | 2026-07-25 | **RMX-QT frozen** (cap-weight, top quintile, 10% cap) | IS 2011-2018 | +0.50 pp/yr, alpha t=+0.79 — **exactly** the t a correct estimate yields (P=0.50) |
| 16 | 2026-07-25 | Power analysis of the RMX-QT edge | — | ~48 years needed for t=2; no sample here can prove or disprove it |

**Trial count for the Deflated Sharpe Ratio: 70** (8 IC signals + 5 fundamental
signals + 1 portfolio baseline + 36 sweep cells + 5 timing configs + the RMX-Q
and RMX-QT variants). The sweep cells are counted in full even though they
explore one family, because counting them generously is the conservative
direction.

## 13. Amendment G — RMX-QT, and why it is not a fifth bite at the apple

Trial 14 is the only test in this project run on data that had **never** been
opened for any purpose: French's 25 size × operating-profitability portfolios.
The construction it tests was not chosen by trying variants until one worked —
it follows mechanically from RMX-Q's failure decomposition, which showed the
signal earning +1.62%/yr while the equal-weight construction underneath it lost
3.3 pp/yr. The question "does the same signal work on cap weights?" was written
down, with its kill criterion, before the file was parsed.

It passed: **BIG HiOP beat the market in all five eras**, 1963–2026, +0.78 pp/yr,
alpha t=+1.71, beta 0.93, Sharpe 0.52 vs 0.47.

Trial 15 then implemented it in this project's own point-in-time S&P 500 panel
and got +0.50 pp/yr, alpha t=+0.79 — which trial 16 shows is *precisely* the
t-statistic a correct +1.19%/yr alpha produces over 7.5 years at a 4.14%/yr
tracking error (P = 0.50, the median outcome). The implementation neither
confirms nor contradicts the 63-year record; at this sample size it cannot.

**What is claimed:** RMX-QT rests on 63 years of independent, survivorship-free
evidence, and this project's implementation of it is statistically consistent
with that evidence.

**What is not claimed:** that any backtest here demonstrates RMX-QT beats the
S&P. It does not, it cannot, and a strategy of this alpha-to-tracking-error
ratio would need roughly half a century of data before any backtest could.
