# Autonomous strategy rebuild — 2026-07-31

**Question asked:** fix the autonomous strategy, make it as profitable as
possible through entries/exits/setup criteria, verify by simulation, iterate up
to four times.

**Answer, up front:** the exit engine was badly mis-specified and fixing it is a
large, real, null-beating improvement in R-space. It is still **not enough to
beat SPY once the book is capitalised**, so nothing is promoted and
`regime_routing` stays unrouted. Four iterations were spent; the result is a
rejection with a much better-understood sleeve behind it.

Harness: `scripts/autostrat_lab.py` (parallel), `scripts/autostrat_sweep.py`
(both-spaces comparison). Data: `research_data/yahoo_long`, 793 ever-S&P500
tickers, split/dividend-adjusted, 2015-01-01 .. 2026-07-24 (11.5 yr).

---

## Starting state

`regime_routing` is `{uptrend: null, downtrend: null, choppy: null}` — the agent
stands flat, by the deliberate determination in AUDIT-2026-07-24 §12f. Pooled
book was +0.0175 net R at 29.1 bps with day-clustered t = −0.24. G1: 0 of 10.

Baseline reproduced here (macd, 120 tickers, 11.5 yr, 30 bps):
**+0.0096 mean R, PF 1.019, day-clustered t = −1.24, MC p50 1.004, P(loss) 49%.**

## Bugs found and fixed

1. **The 2x transaction-cost double-count survived in three analysis scripts.**
   Commit `e753c64` fixed the four engine paths but missed
   `scripts/strategy_scorecard.py:120`, `scripts/cost_crossover.py:84` and
   `scripts/o1_horizon_test.py:172`, all of which still computed
   `2.0 * (bps/1e4) / stop_pct`. Every "30 bps" row those three printed was
   really 60 bps. Fixed; see pitfalls #7.
2. **`config.json` `exits.stop_atr_mult` / `exits.target_atr_mult` are dead
   keys.** Nothing reads them. The binding knobs are `stopAtrMult` /
   `targetAtrMult` in `strategies.json` thresholds, overridable through
   `config.json strategy.thresholds.<strategy>`. An exit sweep driven through
   the documented config path produces *byte-identical output* and reads as
   "exits don't matter". Annotated in place.
3. **Harness date bug (mine, caught before it mattered).** pandas 2.x infers
   `datetime64[s]` from date-only strings, so `astype("int64") // 10**6` landed
   every bar in 1970 and collapsed 6,739 trades into 5 signal-day clusters,
   producing a fake day-clustered t of +2.75. Pin the unit.

## Iteration 1 — the exit engine was shaped wrong for the signal

The shipped exit is a 2.5 ATR target with a 10-session time cap. On a trend
signal that amputates the right tail: 32% of trades exited at the time cap
averaging **+0.41R** while still alive, and the fixed target capped winners at
+1.63R. Widening the target and the hold lets the ratcheting chandelier trail
become the real exit.

macd, 120 tickers, 30 bps:

| exits | meanR | PF | day-clustered t | MC p50 | P(loss) |
|---|---|---|---|---|---|
| stop1.5 tgt2.5 hold10 (shipped) | +0.0096 | 1.019 | **−1.24** | 1.004 | 49.2% |
| stop1.5 tgt5.0 hold40 | +0.1072 | 1.181 | +1.63 | 1.254 | 19.7% |
| **stop1.5 tgt8.0 hold60** | **+0.1349** | 1.227 | **+2.30** | 1.335 | 16.1% |

Monotone in target, and mechanistically explained (right tail preserved) rather
than a non-monotone noise pattern of the `--band 0.40` kind.

## Iteration 2 — the regime gate, and it points the opposite way to intuition

Per-era the sleeve was flat in 2015-2017 chop and **negative in the 2022 bear**,
which is what `regime_routing` exists for. Gating by SPY regime, 300 held-out
tickers, 30 bps:

| regime | trades/yr | meanR | PF | day-clustered t | MC p50 |
|---|---|---|---|---|---|
| uptrend | 782 | **+0.0051** | 1.008 | +0.95 | 0.962 |
| choppy | 363 | +0.1611 | 1.282 | +2.80 | 1.423 |
| **downtrend** | 307 | **+0.3281** | 1.587 | +2.41 | 2.144 |

**A MACD long pays most in a SPY downtrend and least in an uptrend** — it is
buying washout reversals, and in an uptrend the crosses fire late and extended.
Routing to `uptrend`, the intuitive choice, would have been the worst one. The
downtrend arm is positive in **all five eras** (+0.06 / +0.57 / +0.69 / +0.11 /
+0.60), so it is not a single-episode COVID artifact.

Candidate = macd, stop 1.5 / target 8.0 ATR, hold 60, traded only when SPY is
**not** in an uptrend.

## Iteration 3 — verification, all of which passed

641 unseen tickers (parameters chosen on names [0:120], these are [120:]),
30 bps: **+0.2532 mean R, PF 1.448, day-clustered t = +4.45, MC p50 1.772,
P(loss) 2.6%**, all five eras positive including 2022 (+0.0433).

- **Null test** (random entry, identical exits, 140 paths): real +0.2377 against
  a null mean of +0.0552, sd 0.0357 — **100th percentile, p = 0.0071.** The
  entry beats luck by 5.1 sd. Note the null is *not* zero: with these exits
  random entry still earns +0.0552, so ~23% of the gated result is exit engine
  plus market drift (it was ~47% before the regime gate).
- **Sanity battery**: 8 PASS, 1 WARN, 0 FAIL. The WARN is a −13.2R downside gap
  (`SW`, 2020-03-10) through a tight stop — correct behaviour, since
  `winsor_r_cap` caps upside only so the loss tail stays honest.
- **IS/OOS split**: OOS +0.4368 against IS +0.2794 — out-of-sample better.
- **Outlier dependence**: +0.3267 → +0.2464 after cutting the best 1%.
- **Monte Carlo** (repo CLI, cost-charged CSV, 20k paths, 250 trades, 1% risk):
  p50 x1.778, p5 x1.085, P(end below start) 2.6%, worst-5% drawdown 27.2%.
- **201 unit tests pass.**

## Iteration 4 — capitalisation, which reverses the verdict

Every number above is in **R-space, and R-multiples are not returns**
(CLAUDE.md rule 2). R ignores capital efficiency and concurrency. Running the
same trades through `portfolio.py` — finite slots, concurrent holds,
fixed-fractional sizing — at 30 bps, 0.5% risk, 20 positions, 300 held-out
tickers:

| config | meanR | day-clustered t | MC p50 | **CAGR** | **maxDD** | **Sharpe** | vs SPY |
|---|---|---|---|---|---|---|---|
| base tgt2.5 hold10 | +0.0760 | +0.89 | 1.186 | 1.39% | 40.6% | 0.17 | −12.22 |
| tgt5.0 hold20 | +0.1822 | +3.04 | 1.517 | 3.65% | 37.6% | 0.31 | −9.96 |
| **tgt8.0 hold30** | +0.2342 | +4.07 | 1.707 | **8.23%** | **32.2%** | **0.55** | **−5.38** |
| tgt8.0 hold60 | +0.2377 | +3.69 | 1.709 | 6.50% | 38.2% | 0.48 | −7.11 |
| **SPY buy-and-hold** | — | — | — | **13.61%** | **33.7%** | **0.81** | — |

**Every configuration loses to SPY.** The best trails by 5.38 pp/yr at a lower
Sharpe. The R-space metrics and the capitalised metrics rank the configs the
same way, but only the second one is money, and it never clears the passive
alternative sitting in the same account.

The mechanism is capacity. The candidate fires 1,415 signals/yr; with 60-session
holds and 10 slots the account takes **1,216 of 16,318** — 93% are never taken,
and the Monte Carlo p50 of 1.772 implicitly assumed all of them could be, taken
sequentially and independently. That gap between the MC and the account sim is
the whole finding.

### The blend does not rescue it

The repo's documented lever is diversification across near-uncorrelated legs.
It does not apply here — same window, 30 bps, 0.5% risk, 20 slots:

| sleeve | n | meanR | day-clustered t | CAGR | maxDD | Sharpe |
|---|---|---|---|---|---|---|
| macd | 7,728 | +0.2342 | +4.07 | 8.23% | 32.2% | 0.55 |
| bollinger | 17,504 | +0.0545 | +2.60 | 0.35% | 56.1% | 0.11 |
| vwap | 3,428 | +0.1213 | +1.49 | 4.63% | 52.8% | 0.37 |
| residualmomentum | 59,229 | +0.0858 | +3.56 | −14.52% | 89.9% | −0.66 |
| **POOLED** | 87,889 | +0.0940 | +4.09 | **−10.87%** | 82.0% | −0.46 |

Pairwise daily-R correlation is **+0.31 to +0.57, mean ≈ 0.42** — not the
~0.00 the alpha stack's blocks achieve. These are four long-only price-momentum
variants of one theme, so alphas do not add while tracking errors do.
`residualmomentum` additionally floods the pool with 67% of its trades at low
edge, crowding macd's better ones out of the slots. Pooling makes it worse.

## Verdict

**No promotion.** `regime_routing` stays unrouted, all sleeves stay
`user_configurable`, G1 is unchanged. The specific criteria this fails:

- **G1-2** (net alpha t ≥ 2.0 vs SPY): not met. The +4.45 is a day-clustered t
  on R-multiples, not alpha against SPY in return space. Capitalised, the book
  trails SPY.
- **G1-6** (parameter plateau ±20%): not tested.
- **G1-8** (MC P(underperform SPY) ≤ 30%): fails outright — it underperforms SPY
  in the point estimate.
- **G1-9** (DSR at true trial count): not computed, and the trial count for this
  session is ~25 configurations.

What is genuinely established and worth keeping: the shipped exit is
mis-specified for a trend signal, and the correctly-shaped exit moves the sleeve
from −22.8% to +117.8% total return at zero cost and from a t of −1.24 to +4.07.
That is a real finding about the exit engine. It is not an edge over SPY.

## Caveats that bound all of the above

- **Missing bankruptcies.** `FTR`, `WLL`, `HTZ`, `MNK`, `JCP` — S&P 500 names
  that went bankrupt — are **absent** from `research_data/yahoo_long`. A
  dip-buying strategy is exactly what that bias flatters. Of 300 holdout names
  only 7 end early and all 7 are acquisitions (premium exits), so the *observed*
  delisting effect runs the other way, but the survivorship hole is real and
  unquantified here.
- **Ticker recycling.** `BBBY` and `SHLD` show bars through 2026-07; both symbols
  were reassigned to different companies. Those series splice two businesses.
- Flat 30 bps for every name, per costs.py — the measured range is 5.5 to 98.1
  bps across ADV deciles.
- The 2015-2026 window is one long bull market with two real drawdowns. SPY's
  13.61% CAGR is an unusually high bar and the comparison would look different
  in another era — which cuts both ways and is not an argument for deploying.

## Reproducing

```bash
py scripts/autostrat_lab.py --strategy macd --lo 2015-01-01 --max-tickers 120
py scripts/autostrat_lab.py --strategy macd --lo 2015-01-01 --ticker-offset 120 \
       --exits "stop=1.5,target=8.0,hold=60" --regime downtrend,choppy --by-era
py scripts/autostrat_lab.py --strategy macd --lo 2015-01-01 --ticker-offset 120 \
       --max-tickers 300 --exits "stop=1.5,target=8.0,hold=60" \
       --regime downtrend,choppy --null 140 --null-cost 30
py scripts/autostrat_sweep.py --max-tickers 300 --ticker-offset 120
py -m rhengine.sanity --trades-csv tests/results/candidate_macd_nouptrend.csv
py -m unittest discover tests
```

---

# Rounds 5–8 — the ambitious extensions, and what they cost

Four further rounds were run under an explicit brief to take bigger liberties.
All four are negative, and one of them was negative only because a bug in my own
harness was caught. Recorded in full because the rejections are the content.

## Round 5 — SPY overlay: capital sharing (apparent success)

The standalone sleeve loses to SPY largely because it stands **flat ~87% of the
time** while SPY compounds. Measuring SPY's own returns by its own EMA regime
showed the two are complementary:

| regime | %days | SPY ann.ret | SPY Sharpe | macd sleeve |
|---|---|---|---|---|
| uptrend | 61.1% | 6.45% | 0.55 | +0.0051 R |
| choppy | 25.8% | 45.10% | 2.14 | +0.1611 R |
| downtrend | 13.1% | 7.00% (34% vol) | 0.20 | +0.3281 R |

SPY is weakest exactly where the sleeve is strongest. `autostrat_combined.py`
shares one pot between stock positions and an SPY cash overlay — every dollar in
a position is a dollar out of SPY, which is the constraint that stops an overlay
from being a free lunch. Headline: **down-entries + park idle in SPY when not in
a downtrend → CAGR 15.53%, maxDD 31.2%, Sharpe 0.95, +1.93 pp/yr over SPY.**

## Round 6 — that result was a lookahead bug in my own simulator

Decomposing it gave "TIMING ONLY: SPY ex-downtrend, no stocks at all" =
**26.31% CAGR at 12.1% maxDD** — roughly double buy-and-hold off a plain EMA
filter. Not credible, and the "surprisingly good" signature pitfalls.md exists
to catch.

**The bug:** the overlay used `regime[day]` to decide whether to earn `day`'s
return. `classify_spy_regime` reads closes *through* day `day`, so the book was
sidestepping declines it could not have known about. Allocation must be decided
from the **prior** session's regime. Fixed in `simulate_combined` with the guard
comment in place.

Corrected, the entire result evaporates:

| component | CAGR | maxDD | Sharpe | vs SPY |
|---|---|---|---|---|
| SPY buy-and-hold | 13.61% | 33.7% | 0.81 | — |
| timing only (SPY ex-downtrend) | 13.46% | 21.4% | 1.07 | −0.15 |
| stocks only (down-entries) | 5.45% | 27.1% | 0.48 | −8.16 |
| **full combined** | **13.34%** | **36.3%** | 0.83 | **−0.27** |
| down+choppy entries + park ex-down | 11.74% | 33.7% | 0.69 | −1.86 |
| down+choppy entries + park always | 10.34% | 45.3% | 0.58 | −3.27 |

The stock overlay **adds** drawdown and **subtracts** return against pure
timing. Every variant loses to SPY.

Worth noting as a harness check: the corrected "timing only" row — same return,
~⅔ the drawdown, Sharpe 1.07 vs 0.81 — independently reproduces **RMX-D's**
documented profile ("halves drawdown, costs about 0.7 pp/yr; worth having if
drawdown is the binding constraint, it is not alpha"). Arriving at a known
result by a different route is evidence the harness is sound. It is also a
known-**rejected** path (1/11 gates OOS, Zakamulin 155 years), so it is not
built on.

## Round 7 — slot allocation: the capacity hypothesis, killed

The sleeve fires ~1,400 signals/yr into ~20 slots, so ~90% are discarded in
arrival order. Pool mean R is **+0.2562** but the *taken* slice is **+0.1599** —
a real capacity tax. If the taken slice could be made the best slice rather than
an arbitrary one, that is free return with no change to the entry rule.

`autostrat_rank.py` orders each day's candidates by a causal feature before
handing out slots. Ranked against a 40-seed random-ordering control:

| ranker | CAGR | z vs random | percentile |
|---|---|---|---|
| arrival (status quo) | 10.48 | +1.78 | 95.0th |
| dollarvol | 10.09 | +1.50 | 90.0th |
| mom12_1 | 8.56 | +0.36 | 72.5th |
| rev1m | 7.54 | −0.41 | 35.0th |
| atrpct | 6.70 | −1.03 | 12.5th |
| **SPY buy-and-hold** | **13.61** | **+4.11** | **above all 40** |

Random ordering alone has **sd 1.34 pp/yr and spans 5.90–10.98**. Every ranker
sits inside that band, including 12-1 momentum — the one ranker with a 63-year
published record. The apparent "arrival beats random by 4 pp/yr" seen on a
single seed was one draw against that spread.

**SPY sits 4.11 sd above the entire distribution of possible slot allocations.**
So capacity was never the binding constraint: no allocation scheme, optimal or
otherwise, closes a gap that wide. The signal is not good enough, and that is a
different problem from the one the capacity finding suggested.

## Standing verdict after 8 rounds

Unchanged and now much better supported. `regime_routing` stays unrouted, every
sleeve stays `user_configurable`, G1 stays 0 of 10, 201 tests pass.

Routes tried and closed: exit reshaping (real, large, insufficient), regime
gating (real, insufficient), sleeve blending (correlations ~0.42, makes it
worse), SPY capital overlay (negative once lookahead is removed), market timing
(rediscovers RMX-D, already rejected), and slot ranking (indistinguishable from
random). The one honest positive is that the shipped exit is mis-specified for a
trend signal — worth fixing on its own terms, not an edge over SPY.

**The bugs found across all 8 rounds are the durable deliverable:** the 2x cost
double-count in three analysis scripts, the two dead exit keys in `config.json`,
and a lookahead in a regime overlay that read as +1.93 pp/yr of alpha until it
was decomposed.

---

# Round 9 — the metric that should have been round 1

Prompted by "we had a 1.6x p50 earlier and now nothing is that good." Both
halves of that turned out to be worth answering: the p50 never regressed, and
the reason it looked like progress is that it cannot measure progress.

## 9.1 The p50 is intact, and it is not a return

`mc_p50` bootstraps `n` trades from the population and compounds them
**sequentially**, as if the account could take every signal one after another
with no two competing for capital. On the unchanged 16,318-trade candidate
(`tests/results/candidate_macd_nouptrend_net30.csv`, already cost-charged):

| n | p5 | p50 | p95 | P(loss) | ddp50 |
|---|---|---|---|---|---|
| 100 | 0.924 | 1.252 | 1.745 | 11.7% | 11.4% |
| 250 | 1.093 | **1.772** | 2.998 | 2.6% | 15.7% |
| 500 | 1.583 | 3.171 | 6.575 | 0.3% | 19.2% |
| 1000 | 3.770 | 10.200 | 28.514 | 0.0% | 22.7% |
| 2000 | 25.325 | 105.721 | 443.050 | 0.0% | 26.2% |

The p50 rises without bound in `n`. **A p50 quoted without its `n` is not a fact
about the strategy** — the audit's convention is n=250, which is where 1.772
comes from. It is a per-trade compounding statistic, it assumes concurrency
away, and it is not comparable to an index return. Nothing got worse; the
number was never measuring what it was being read as measuring.

## 9.2 Deployment: the book is over-invested, not starved

The hypothesis was that the sleeve loses because it sits in cash. Measured
directly, with every acceptance rule copied from `portfolio.py` and the equity
path cross-checked against `simulate_portfolio` (both take 2,416 trades):

| risk% | taken | no-slot | open | flat% | inv% mean | inv% p95 |
|---|---|---|---|---|---|---|
| 0.25 | 2416 | 13789 | 11.8 | 6.6% | 74.1 | 157.8 |
| 0.50 | 2416 | 13789 | 11.8 | 6.6% | **148.6** | 321.4 |
| 1.00 | 2416 | 13789 | 11.8 | 6.6% | 299.9 | 671.2 |
| 2.00 | 2416 | 13789 | 11.8 | 6.6% | 622.5 | 1509.7 |

`inv%` is notional/equity, deliberately uncapped. At the standard 0.5% risk the
book runs at **~1.5x leverage**, not in cash — because `notional = risk/stop`
and the stops are tight. The hypothesis was wrong in the opposite direction from
the one expected. Sizing up accordingly destroys it (engine `simulate_portfolio`,
SPY CAGR 13.61% / Sharpe 0.81):

| risk% | CAGR% | maxDD% | Sharpe | vs SPY |
|---|---|---|---|---|
| 0.25 | 3.23 | 22.2 | 0.41 | −10.38 |
| 0.50 | 4.38 | 42.1 | 0.34 | −9.23 |
| 1.00 | 1.36 | 71.8 | 0.21 | −12.25 |
| 2.00 | −19.74 | 96.9 | 0.08 | −33.35 |

Sharpe falls monotonically as size rises — leverage on a worse asset, plus
volatility drag. Deployment is closed.

## 9.3 The metric: net rate against the matched index

A trade's return on the capital it ties up is an **identity**, not a modelling
choice. `portfolio.py` sizes `notional = risk_dollars / stop_pct`, so

```
P&L / notional = (risk * R) / (risk / stop_pct) = R * stop_pct
```

This is a property of the trade alone — no slot count, no `risk_pct`, no heat
cap, no leverage assumption. It is the one quantity that survives the
R-space/capitalised-space translation intact. Divide the book's total by its
total dollar-days, and benchmark against SPY over **exactly the same
entry..exit windows**:

```
book, dollar-day weighted     +9.96 bp/day
SPY over the SAME windows    +11.22 bp/day   <- the like-for-like hurdle
SPY unconditional             +5.68 bp/day   <- NOT the hurdle
net                           -1.27 bp/day   ~ -3.2 pp/yr on deployed capital
mean excess per trade        -0.225%  (day-clustered t -1.59)
trades beating SPY over their own window: 35.1%
```

**Benchmark against the matched window, never unconditional SPY.** The sleeve
chooses its days, so the unconditional average is the wrong hurdle and flatters
it by a factor of two.

Two weightings are reported because they can disagree in sign: the rate is
dollar-day weighted (long holds dominate), per-trade excess is equal-weighted
(fast stop-outs dominate). Here they agree — both negative.

**Why this ends the search.** A sleeve with net ≤ 0 is a worse place to park a
dollar than the index *on the very days it chooses to be invested*. Ranking,
sizing, slot count and blending change *which* trades are taken and *how much*
capital each gets, but none of them can change a trade's `R × stop_pct` — so
none can lift a negative net. Net ≤ 0 closes all of those at once, and this one
number would have ended most of rounds 1–8 in an afternoon.

Exits are the exception and are treated separately in §9.6: they change hold
length, hence *which windows* the book is invested in, so net can move with
them.

## 9.4 Timing, not selection — what the sleeve actually knows

The decomposition explains all eight prior rounds in one line. The book's raw
rate is **1.75x unconditional SPY** (9.96 vs 5.68 bp/day). Every bit of that is
window selection: it enters in downtrend/choppy regimes, and those windows
contain the sharp rebounds. Within the windows it picks, its names lose to
simply holding the index.

**The sleeve is a market-timing signal wearing a stock picker's clothes.**

- Round 5's SPY overlay looked strong because the *windows* are good; the value
  was never in the names.
- Round 7's rankers did nothing because within-window name selection is the
  broken part, and reordering names cannot fix a negative net.
- The round-3 regime finding (macd pays most in downtrends) is the timing
  component showing through.

And the tradeable content of "when" is RMX-D — already measured and rejected
(halves drawdown, costs ~0.7 pp/yr, not alpha). There is nothing left to build
here, and this is now the third route by which that conclusion has been reached.

## 9.5 Ex-ante vs ex-post cuts

Every cut decidable **at the signal bar** is negative or noise:

| cut | best bucket | net bp/day | dayT |
|---|---|---|---|
| entry regime | choppy | −0.75 | −0.25 |
| VIX regime | calm (n=432) | +2.59 | +0.73 |
| setup score | only score 4 fires | −1.27 | −1.59 |
| leg mask | 1100 | −0.11 | +0.20 |
| stop width | widest 20% | +0.50 | −0.15 |
| era | 2018-2019 | +0.08 | −1.06 |

Nothing survives. The two cuts that look spectacular are **ex-post and
untradeable** — holding length and exit reason are *outcomes*:

| ex-post cut | n | excess% | dayT |
|---|---|---|---|
| 40–61 sessions | 2002 | **+5.57** | +11.37 |
| TP exit | 1182 | **+19.61** | +25.44 |
| SL exit | 6297 | −4.81 | −36.86 |

A trade is in the "40–61 sessions" or "TP" bucket *because it kept working*.
Selecting on either is conditioning on the future. They diagnose the shape of
the loss; they are not filters. The scripts label these sections explicitly so
the tables cannot be lifted out of context.

## Tooling added

| script | what it answers |
|---|---|
| `scripts/autostrat_capital.py` | p50 vs n; deployment; does sizing close the gap |
| `scripts/autostrat_dollarday.py` | net rate for one book, ex-ante and ex-post cuts |
| `scripts/autostrat_screen.py` | net rate for every strategy, one line each |

## 9.6 Every non-retired strategy, screened on net rate

300 tickers (offset 120, the cross-sectional holdout set), 2015-01-02..2026-07-24,
30 bps, `regime=all`, stop 1.5 / target 8.0 / hold 30 — the exit config rounds
1–4 selected for macd, applied uniformly.

| strategy | n | meanR | dayT | held | bp/day | SPY bp/d | **net** | netT | ann% |
|---|---|---|---|---|---|---|---|---|---|
| vwap | 9,779 | +0.0777 | +1.37 | 12.4 | 4.69 | 4.82 | **−0.13** | −1.78 | −0.32 |
| macd | 16,250 | +0.0945 | +2.59 | 13.1 | 5.60 | 7.42 | **−1.81** | −2.00 | −4.47 |
| pead | 13,613 | +0.0263 | −4.47 | 14.1 | 2.44 | 4.56 | **−2.12** | −7.69 | −5.19 |
| bollinger | 25,175 | +0.0298 | +0.50 | 8.6 | 5.36 | 7.50 | **−2.14** | −1.99 | −5.26 |
| residualmomentum | 171,705 | −0.0004 | +1.00 | 12.3 | 1.80 | 4.22 | **−2.42** | −8.86 | −5.92 |

**Nothing is positive.** The closest is `vwap` at −0.13 bp/day (−0.32%/yr on
deployed capital), and its netT is −1.78 — negative, not merely indistinguishable
from zero.

The table is also the R-space mirage shown across the whole strategy family at
once: **four of five have a positive mean R, and macd's day-clustered t is
+2.59, yet every single net is negative.** A positive mean R with a tight stop
buys a small dollar return over a long hold; the index earns more over the same
days.

### What this closes, and what it does not

Net ≤ 0 rules out **sizing, slot count, ranking, and blending** outright — those
change *which* trades are taken and *how much* capital each gets, but they cannot
change any trade's `R × stop_pct`, so they cannot lift a negative net.

It does **not** close exits by itself, and the earlier phrasing in §9.3 that
"exits only redistribute dollar-days" was an overreach. Exits change hold length,
which changes *which windows* the book is invested in, so net can move with them.
The bound here is empirical rather than structural: rounds 1–4 already swept the
exit surface for macd and this table uses the winner of that sweep. A wider
per-strategy exit sweep is unexhausted — it is just a poor bet, since the sweep
that was run moved macd's R-space t from −1.24 to +4.07 without moving its net
above zero.

Caveats: one exit config applied to all five, `regime=all`, and a single
300-ticker slice. `DEC` was skipped in every run (`ZeroDivisionError`, isolated
by the per-ticker fallback).

---

# Round 10 — intake: freeCodeCamp "Algorithmic Trading Using Python" (Nick McCullum)

Video supplied mid-hunt (`youtu.be/xfzGZB4HhEE`). Three projects: an equal-weight
S&P 500 fund, a quantitative momentum strategy, and a quantitative value
strategy.

## 10.1 What it is, and what it cannot be

**It is not evidence, and the repo's rules do not permit treating it as such.**

- **No backtest of any kind.** It produces forward-looking position sizes into
  an Excel file. There is no equity curve, no cost model, no null test.
- **IEX Cloud sandbox data** — deliberately randomised. Numbers printed in the
  tutorial are not real prices.
- **Current S&P 500 membership from a static CSV** — survivorship bias, the
  exact hazard `rhengine/alpha/panel.py` exists to eliminate.
- No transaction costs, so nothing is comparable to the 30 bps standard.

It is a well-made lesson in `requests`, batched API calls and pandas. That is
all it claims to be, and it should not be graded as more.

## 10.2 Where it is nonetheless useful: it points at the right half of the repo

Every strategy in it is **cross-sectional rank → equal weight → hold**. There
are no stops, no ATR targets, no trailing exits, no per-trade R-multiples — none
of the legacy-engine machinery that rounds 1–9 failed to rescue. Its shape is
the research stack (`rhengine/alpha/`), which is where the only surviving
strategy in this project lives (RMX-B, alpha t=+2.54).

That is weak but real corroboration of a direction this repo already took, and
it is worth saying plainly: an outside introductory course, reasoning from
scratch, builds the thing that works here and does not build the thing that
doesn't.

## 10.3 Idea 1 — "HQM" multi-horizon momentum. REJECTED.

The video's one genuine refinement over plain momentum: instead of a single
12-month return, percentile-rank the 1, 3, 6 and 12-month returns and average
them, preferring *consistent* momentum. RMX-B's leg is single-horizon 12-1.

Screened long-only against the published record (OSAP, `--vs GP Mom12m`), as an
added block on the existing 4-block blend:

| candidate | solo bp/t (full) | d_t 1970-2024 | d_t 2010-2018 | d_t 2015-2024 |
|---|---|---|---|---|
| Mom6m | 11.82 / 1.09 | −0.49 | −0.47 | +0.33 |
| IndMom | −0.83 / −0.08 | −1.00 | −0.76 | −0.83 |
| STreversal | −4.14 / −0.29 | −1.03 | −0.57 | −0.84 |

**Nothing helps in even two of three windows, let alone all three.** Adding
Mom6m *lowers* the blend t from 2.80 to 2.31. Single-horizon 12-1 is better than
the multi-horizon average.

**There is also a sign error in the video's construction.** It signs the
1-month return as positive momentum — higher recent return, higher HQM score.
The published record names that horizon `STreversal` precisely because the
profitable direction is the opposite. Even taken with the *correct* reversal
sign it subtracts in all three windows (d_t −1.03 / −0.57 / −0.84), so the fix
does not rescue it either.

## 10.4 Idea 2 — "Robust Value" multi-ratio composite. REJECTED, and instructive.

The video composites P/E, P/B, P/S, EV/EBITDA and EV/GP by percentile rank. Same
screen, using OSAP's `EP`, `BM`, `SP`, `CF`, `EBM`:

| candidate | solo bp/t (full) | rho | d_t 1970-2024 | d_t 2010-2018 | d_t 2015-2024 |
|---|---|---|---|---|---|
| CF | 24.92 / 2.29 | −0.24 | **+2.70** | −0.07 | −0.43 |
| EP | 19.49 / 1.64 | −0.24 | **+2.08** | −0.65 | +0.34 |
| BM | 24.46 / 1.91 | −0.16 | +1.49 | −1.55 | −1.04 |
| SP | 25.55 / 1.98 | −0.15 | +1.45 | +0.11 | −0.58 |
| EBM | 11.79 / 1.10 | −0.23 | +1.39 | −0.31 | +0.04 |

On the full 1970–2024 sample this is the **strongest set of blend candidates
this project has ever screened** — CF lifts the blend t from 2.80 to 5.50. They
are negatively correlated with every block already held (rho −0.15 to −0.24),
which is exactly the property that makes a block worth adding.

**And all of it is pre-2010.** In 2010-2018 the best is SP at d_t +0.11; in
2015-2024 the best is EP at +0.34. Both are inside the ±0.5 band the screen
itself calls selection rather than evidence. BM, the classic value factor, is
*negative* in both modern windows.

This is the cleanest illustration yet of why the video's methodology is
dangerous rather than merely incomplete: a full-sample or current-snapshot screen
would rank CF as the best signal available, and it has added nothing for fifteen
years. It independently reproduces the standing bound (published large-cap
anomalies ≈ 7 bp/month post-2005, median t 0.45).

## 10.5 Verdict

Nothing to build. Both of the video's ideas were tested against the published
record — not the spent local holdouts — and both fail the standing gate that a
candidate must help in every window. The equal-weight S&P 500 project is a
worse-constructed version of what `alpha/book.py` already does correctly.

The value screen is retained as a **new negative result**: the classic value
ratios are strongly additive to the RMX-C blend on long history and dead in the
modern era. That question had not been asked here before, and now it does not
need asking again.
