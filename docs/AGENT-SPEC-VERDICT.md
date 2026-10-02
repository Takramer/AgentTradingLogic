# Build verdict on the tactical spec — external evidence audit, 2026-07-30

Companion to [AGENT-SPEC.md](AGENT-SPEC.md). The spec says *what the sources
claim*. This file says *what the published record says about those claims*, and
therefore **what to build**.

Method: eight web searches plus one page fetch, 2026-07-30, looking for
peer-reviewed or otherwise adversarial evidence on each module. Everything below
is second-hand — search summaries and abstracts, not primary reads of the
papers. Treat the numbers as directionally reliable, not as citations. One fetch
(CXO Advisory's ORB review) hit a paywall before the results section.

---

## The verdict, in one table

| Module | Evidence found | Verdict |
|---|---|---|
| 1 — Weinstein stages | **None peer-reviewed.** The adjacent, heavily-studied version (MA market timing) fails | **Do not build as alpha.** Keep the existing `stageFilter` off |
| 2 — RRG quadrants | None. The JdK metric is proprietary and unreproducible (`GAP-2.1`) | **Do not build.** Blocked on the formula anyway |
| 2 — dual momentum | Strong evidence for *diversified multi-asset* time-series momentum; a lost decade inside it | **Defer.** Real, but it is a different asset class from this book |
| 3 — ORB | Strong published claim, frictionless assumptions, catastrophic base rate | **Do not build.** Worst possible fit for this repo's cost structure |
| 3 — FVG / ATR bands | **Zero academic literature.** Retail sources only | **Do not build** |
| 3 — Donchian / Fibonacci | Nothing found beyond the same practitioner corpus | **Do not build** |
| 4 — ATR sizing, 1% rule, heat cap | Not an alpha claim — a variance control | **Keep.** Already implemented |
| 5 — exits | Not separately testable from entries | **Keep existing** |
| 6 — options overlay | **The only module with a documented economic mechanism** (volatility risk premium) | **The one candidate.** Data first, strategy code later |
| 7 — execution gates | Uncontroversial hygiene | **Adopt the spread gate.** Cheap, no evidence needed |
| 8 — validation | Spec is weaker than what this repo already runs | **Ignore.** Repo protocol governs |

**Net: build nothing from Modules 1–5. Adopt one execution gate. The only work
worth funding is measuring the volatility risk premium on the book this project
actually holds — and that is a measurement task, not a strategy.**

---

## 1. Weinstein stage analysis — no evidence, and the nearest testable relative fails

No peer-reviewed validation of Weinstein's 4-stage method exists. Search returns
are entirely trading blogs, indicator scripts and platform marketing. The method
is popular; it is not studied.

What *has* been studied to destruction is the closest testable relative:
moving-average market timing. Valeriy Zakamulin's work is the reference:

- He showed that Glabadanidis's widely-cited "extraordinarily good performance"
  of MA timing was an artefact of **look-ahead bias**. Simulated without it, the
  MA strategy was **only marginally better than buy-and-hold**.
- Across a **155-year** dataset, using both rolling and expanding out-of-sample
  windows, he found **no statistically significant evidence** that market-timing
  strategies outperformed the market in the second half of the sample.
- His conclusion is that even long-sample, economically-significant-looking
  trend-following results are often an artefact of data snooping.

**This corroborates a result this repo already has.** `stageFilter` scored OOS
+0.026 vs a +0.044 baseline on residualmomentum and −0.057R pooled on bollinger
(`notebooklm-findings.md` §1.1). That was written off as "underpowered — only
~1.5 regime cycles in 400 days." The external record says the underpowering is
not the interesting part: a 155-year sample with proper OOS methodology also
finds nothing. Stop attributing the null to sample size.

**Verdict: do not build. Do not promote `stageFilter`. Do not re-test it on
longer history expecting a different answer** — that test has been run by
someone with 155 years of data.

---

## 2. Dual momentum — real, but not this asset class, and not without a bad decade

This is the one place the retail doctrine touches something with genuine
academic standing. Hurst, Ooi and Pedersen's *A Century of Evidence on
Trend-Following Investing* builds an equal-weighted blend of 1-, 3- and
12-month time-series momentum across **67 markets in four asset classes,
1880–2016**. Moskowitz, Ooi and Pedersen's underlying *Time Series Momentum*
paper documents 1–12 month persistence with longer-horizon reversal, large alpha
against standard factors, and best performance in extreme markets.

Three caveats that matter more than the headline:

1. **It is a diversified futures portfolio**, across equity indices, currencies,
   commodities and bonds. It is not a moving-average filter on single equities,
   and it is not sector-ETF rotation. The spec's Module 2 is not the thing that
   was tested.
2. **The lost decade is real.** The SG Trend Index — the 10 largest
   trend-following CTAs — returned a **total of 3.7% over 2009–2018**, a CAGR of
   **0.4%**, with a **−21.8% max drawdown**. A decade of near-zero return with a
   fifth of capital drawn down is inside the distribution of this strategy.
3. Some research attributes the TSMOM result to the **volatility scaling** used
   in construction rather than to time-series momentum itself.

Note the alignment with this repo's own conclusion: `CLAUDE-RESEARCH-RULES.md`
§5 says the lever that works is diversification across near-uncorrelated legs,
not any single signal. The century-of-evidence result is the same lever, applied
across asset classes instead of across factors.

**Verdict: defer.** A sector-ETF dual-momentum sleeve is not the researched
construction, and building the researched construction means trading futures
across four asset classes — far outside this engine's scope. If it is ever
revisited, screen it on the published record first (research rules §1), and
budget for a decade of nothing.

---

## 3. ORB — the strongest published claim in the corpus, and the one to touch last

Zarattini, Barbon and Aziz's ORB papers are real, cited, and report a **5-minute
opening-range breakout on QQQ delivering ~33% annualised alpha net of
commissions**, with a leveraged TQQQ variant returning **1,484% over 2016–2023**
against 169% for QQQ.

The assumption list is the whole story:

| Assumption | Stated value |
|---|---|
| Bid-ask spread | **none** |
| Slippage / market impact | **none** |
| Commission | $0.0005/share |
| Leverage | up to **4×** |
| Execution | exact fills at 09:35 and 16:00 |
| Risk per trade | 1% of capital at the stop |

This repo's measured round-trip cost is **29.1 bps**, ranging **5.5 bps in the
most liquid decile to 98.1 bps in the least**, and the **opening spread — the
widest of the day, and precisely what an ORB strategy fills at — is still
unmeasured** (`CLAUDE-RESEARCH-RULES.md` §6). A strategy whose published edge is
computed at zero spread, entering at the open, is the single least transferable
result in the corpus.

The base rate is worse. Barber, Lee, Liu and Odean's complete Taiwan market
records (1992–2006) find **fewer than 1% of day traders predictably profitable
net of fees**, roughly 3% positive net. Chague, De-Losso and Giovannetti followed
**19,646 Brazilian day traders** (2013–2015): of the ~1,600 who persisted beyond
300 sessions, **97% lost money** and **1.1% out-earned the minimum wage**.

Both facts can be true — a real frictionless edge, and a population that loses
money net of frictions. That is what it looks like when an edge is smaller than
its transaction costs.

**Verdict: do not build.** Not because the paper is wrong, but because the one
number it assumes away is the number this repo has measured and knows to be
large.

---

## 4. FVG and ATR bands — no literature exists

Fair Value Gaps have **no peer-reviewed academic literature**. Every source
found is a broker blog, a charting platform, or a Medium post. The recurring
claim is a **70–85% fill rate** across liquid markets.

That statistic does not support a strategy:

- A fill rate with **no holding period** is nearly vacuous. Given enough time,
  price revisits most levels; sources themselves note higher-timeframe gaps may
  take "weeks or months" to fill.
- There is **no cost**, **no benchmark**, and **no accounting for the unfilled
  15–30%** — which is where the losses live, and whose size is never stated.
- The stated intellectual lineage (auction market theory, market profile, volume
  spread analysis) is itself not an evidence base.

R1 proposes FVG as a *replacement* for volume confirmation. Volume confirmation
is weakly evidenced, but it is at least measured. Swapping it for an unmeasured
construct is a downgrade.

Recall also that FVG is **blocked on two spec gaps** (`GAP-3.10` minimum gap
size and staleness; `GAP-3.11` how the ATR bands are even constructed). Building
it means inventing both, then testing an invention against no prior.

**Verdict: do not build.** This resolves the open question from the last pass —
the house decisions on `GAP-3.10`/`GAP-3.11` are not worth making.

---

## 5. Options overlay — the one module with a mechanism

This is where the evidence actually is, and it is not about IV Rank thresholds.

**The volatility risk premium is documented.** On S&P 500 options, implied
volatility exceeds subsequently-realised volatility roughly **85% of the time**,
with an average overstatement of **2–4 volatility points**. That is a structural
risk premium paid to option sellers, and it is the economic reason selling
premium can work. Nothing in Modules 1–5 has anything comparable.

**But harvesting it via covered calls has a poor recent record.** The CBOE
BuyWrite Index (BXM):

| Window | Result |
|---|---|
| 1988–2006 | ~11.77%/yr vs 11.67% for the S&P 500, at ~⅔ the risk |
| 1988–2011 | 9.1%/yr, ~⅔ the volatility |
| **Last ten years** | **~⅓ the return of the S&P 500** |

The mechanism is real; the standard vehicle truncates upside badly through a
bull market. A covered-call overlay on a book whose entire thesis is a **+1.78%/yr
equity alpha** (RMX-B) would be selling away the thing the book exists to
capture. That is the trade-off to model before writing any code.

### This resolves CONFLICT-9 — by rejecting both options

The spec's two competing gates are IV Rank (IV vs its own 52-week range) and
IV-vs-HV (IV vs *past* realised vol). **The researched quantity is neither.** The
VRP is IV minus **subsequently realised** volatility. IV-vs-HV is a proxy for it
only to the extent volatility is persistent; IV Rank is purely self-referential
and has no mechanism attached to it at all.

**Recommendation: compute the VRP directly** — implied vol at entry against
realised vol over the contract's actual life — and use *that* as the gate. Do
not adopt either source construct. The 70/30 IV-Rank thresholds have no
evidentiary support found anywhere in this search.

### What to actually do

A measurement, not a strategy, and in this order:

1. **Build the IV data provider only.** Chain IV history, cached like
   `yahoo_cache/`. This is the hard prerequisite and it is already the top item
   in `notebooklm-findings.md` §3.4.
2. **Measure the VRP on the names RMX-B actually holds** — ~96 large caps. Does
   IV exceed subsequent RV on this specific book, and by how much, after the
   options spread? The S&P 500 *index* VRP is not evidence about single-name
   large caps.
3. **Only if step 2 shows a premium net of costs**, model the upside truncation
   against RMX-B's alpha. BXM's last decade is the prior to beat.
4. Structure-selection code comes after all of that, or never.

Steps 1–2 are honest measurement and cannot produce a false positive the way a
backtest can. Step 3 is where the real decision is.

---

## 6. Execution — adopt the spread gate, ignore the rest

The 1%-of-mid bid-ask gate (`spread_gate_pct`, Module 7) needs no external
evidence: it is arithmetic hygiene, and this repo already knows its worst
liquidity decile runs 98.1 bps round trip. It is cheap to add and prevents the
book from trading names where the edge is structurally unrecoverable.

Everything else in Module 7 is either already covered (`min_dollar_volume`,
`exclude_etfs`) or blocked on `GAP-7.1` (nobody defines "highly liquid").

---

## 7. Validation — the spec is weaker than what already runs here

Module 8's bar (profit factor > 1.25, Sharpe > 1.00, p < 0.05, ≥300 trades) is
below this repo's standard on every axis that matters: it computes t-stats on
trades rather than non-overlapping monthly returns with Newey-West errors, has
**no multiple-testing correction** (`GAP-8.4`), and compares against zero rather
than against random-selection and block-bootstrap nulls.

Note the corpus's own inconsistency: it ships this protocol *and* reports GLD/SPY
figures that satisfy none of it (see `AGENT-SPEC.md` → UNVERIFIED PERFORMANCE
CLAIMS). A source that violates its own validation standard in the same document
is telling you how much the standard cost it.

**Verdict: ignore Module 8.** `alpha/stats.py`, `alpha/validation.py`,
`crossval.py` and `sanity.py` already exceed it.

---

## What this changes about the spec

Nothing in `AGENT-SPEC.md` gets deleted. It remains an accurate record of what
the sources say, and this repo keeps rejections as carefully as results.

What changes is its **standing**: it is now a *rejection record with two
survivors* — a spread gate worth adopting and a volatility-risk-premium
measurement worth funding — rather than a build queue.

The parameters this project still needs to invent (`GAP-3.10`, `GAP-3.11`,
`GAP-2.1`) are for constructs the evidence says not to build. **Leave them
open.** An unfilled gap on an abandoned module costs nothing; a house-invented
parameter on an unevidenced construct costs a research cycle.

---

## What was built from this verdict, 2026-07-30

Both survivors were acted on the same day. Nothing from Modules 1–5 or 8 was
touched.

### Survivor 1 — the spread gate: built, and it is not decoration

`rhengine/execution.py` (+ `config.json` `execution.spread_gate_pct`, 16 tests).
The spec default of 1.0% is kept, because this repo does not silently retune a
source parameter, and the value is configurable so a house number can be set
later.

**It binds harder than expected.** Run against both stratified quote captures:

| ADV decile | median spread | rejected at 1% |
|---|---|---|
| 0 (~$0.7M/day) | 97.6 / 88.9 bps | **14/29 both days** |
| 1 (~$1.8M/day) | 70.7 / 60.8 bps | 10/29, 6/29 |
| 2 (~$4.1M/day) | 43.5 / 20.9 bps | 3/30, 4/30 |
| 3+ (>$6M/day) | ≤18 bps | ~0 |

**Trade-weighted, the gate rejects 8.3–8.9% of the real 120,406-trade book**,
stable across two days. So `universe_filters.min_dollar_volume` of $500k does
**not** imply a tight book — a volume floor and a spread gate are the same lever
from two ends, but only the gate sees the actual quote. Reproduce with
`py scripts/rh_spreads.py gate`.

Honest limitation, stated in the module: daily bars carry no bid/ask, so this
gate is **live-path only** and changes no recorded backtest number. The
temptation to synthesise a spread is exactly the rejected Corwin-Schultz path
(`costs.estimate_round_trip_bps`, ~37× too high).

**A units correction fell out of building it.** The quoted spread `(ask-bid)/mid`
*is* the round-trip cost — each crossing pays a half-spread, twice makes one
spread. An early draft doubled it and would have reported this universe at 58
bps instead of 29. It is now pinned by a test against `costs.py`'s own recorded
deciles rather than by assertion.

### Survivor 2 — the VRP: the data question is answered, and it changes the plan

§5 above said "build the IV data provider only". The spike says there is no IV
provider to build:

| Robinhood call | IV? |
|---|---|
| `get_option_quotes` | has `implied_volatility` — returns **null**, as do all greeks |
| `get_option_historicals` | daily OHLC only |
| `get_option_instruments state='expired'` | resolves 2026-07-17, **not** 2026-06-19 |
| historicals on an expired contract | **full contract life**, given the UUID |

So IV must be **derived** by inverting Black-Scholes from the option price —
built and tested in `rhengine/market_data_options.py` (17 tests): `implied_vol`,
`realised_vol`, `vrp`, `bs_price`, `has_vol_information`.

The useful discovery is about sequencing. Option *price* history is fully
retroactive, so nothing about the measurement requires waiting. **The only
perishable asset is the ability to enumerate old contracts.** The standing job
is therefore a registry of contract UUIDs, not a daily IV snapshot — much
cheaper than `notebooklm-findings.md` §3.4 assumed, and it is the one piece
that has to start early. **It is not built**: it is a scheduled job and wants a
cadence decision first.

Two accuracy limits are recorded in the module rather than papered over,
because both bias toward *finding* a premium that is not there:

1. **These are American options; Black-Scholes is European.** For puts, and for
   calls across a dividend, inverting a real price through BS returns a vol
   that is too high — which inflates any VRP computed from it.
2. **The options spread is the whole question.** Equity option books run far
   wider than the 29.1 bps measured on the underlying, and a 2–4 vol point
   premium can sit entirely inside one. Any VRP must be reported as a band
   measured from bid and from ask, never as a midpoint.

Steps 3 and 4 of §5 (upside truncation against RMX-B's alpha, then structure
selection) remain unstarted, and step 3 is still where the real decision is.

---

## Sources

Second-hand via search summaries, 2026-07-30. Not primary reads.

- [Zakamulin, *Revisiting the Profitability of Market Timing with Moving Averages*](https://onlinelibrary.wiley.com/doi/abs/10.1111/irfi.12132) — and [*A Comprehensive Look at the Empirical Performance of Moving Average Trading Strategies*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2677212)
- [Alpha Architect, *The Moving Average Research King: Valeriy Zakamulin*](https://alphaarchitect.com/the-moving-average-research-king-valeriy-zakamulin/)
- [Hurst, Ooi & Pedersen, *A Century of Evidence on Trend-Following Investing*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026)
- [Moskowitz, Ooi & Pedersen, *Time Series Momentum*](https://www.sciencedirect.com/science/article/pii/S0304405X11002613)
- [Man Group, *A Trend Following Deep Dive: The Dynamics of Dispersion*](https://www.man.com/insights/deep-dive-trend-following) — SG Trend Index lost-decade figures
- [Zarattini, Barbon & Aziz, *A Profitable Day Trading Strategy For The U.S. Equity Market*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284)
- [CXO Advisory, *Day Trading with an Opening Range Breakout Strategy*](https://www.cxoadvisory.com/technical-trading/day-trading-with-an-opening-range-breakout-strategy/) — assumption list; results paywalled
- [Barber, Lee, Liu & Odean, *Do Individual Day Traders Make Money? Evidence from Taiwan*](https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trade%20040330.pdf)
- [Chague, De-Losso & Giovannetti — Brazilian day-trader cohort](https://www.tradicted.com/research/chagu-day-2020/)
- [StockTitan, *Implied vs Historical Volatility*](https://www.stocktitan.net/articles/implied-volatility-vs-historical-volatility) — VRP magnitude
- [Tastytrade, *Volatility Metrics (IVR, IV%, IVx, HV)*](https://support.tastytrade.com/support/s/solutions/articles/43000539059)
- [CBOE S&P 500 BuyWrite Index (BXM)](https://en.wikipedia.org/wiki/CBOE_S&P_500_BuyWrite_Index) and [Callan evaluation of the buy-write strategy](https://www.borntosell.com/covered-call-blog/evaluation-of-buy-write-strategy)
- [The Tactical Brief, *Mighty Gaps: The Fair Value Gap*](https://thetacticalbrief.com/wp-content/uploads/2023/12/Mighty-Gaps-1-FVG-Fair-Value-Gap.pdf) and [FluxCharts, *Fair Value Gaps Explained*](https://www.fluxcharts.com/articles/fair-value-gaps-fvg-explained) — the retail fill-rate claim, and the absence of anything better
