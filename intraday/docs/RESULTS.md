# Intraday results — SPY one-minute bars, 2016–2026

## Pre-registration (written 2026-10-27, before any full-sample run)

**Data.** SPY one-minute bars, Alpaca SIP feed, split-adjusted, 2016-01-04 to
2026-10-26, cut to 09:30–16:00 ET: 2,719 sessions, 1,059,298 bars
(`intraday/scripts/fetch_alpaca_bars.py`). Daily VIX closes from FRED
(`VIXCLS`). MES has no data here; SPY stands in for it (below).

**Windows, fixed now.**

| window | dates | use |
|---|---|---|
| development | 2016-01-04 → 2024-12-31 | every check in `validate.evaluate` |
| holdout | 2025-01-02 → 2026-10-26 | **one look in total**, for the single best rule that passes every development check. If none passes, the holdout stays unused |

**Venues tested.**

| venue | instrument | rules the engine enforces |
|---|---|---|
| Robinhood agent account (cash, options level 2) | `SPY_RH`: $10,000 notional per trade, fractional shares | long only (no shorting in a cash account); one entry per session (settled cash); $0 commission, SEC fee 20.60/M on sales, 1 cent slippage per side |
| IBKR futures | `MES_ON_SPY`, qty 50 = one MES | long and short; MES costs ($4.28 round trip) spread over 50 SPY share-equivalents; $2,500 margin |

**Trials, in order.** Each is logged in `intraday/data/trials.jsonl` before it
runs. Settings are the published ones; there is no grid, so no parameter is
tuned on this data.

| # | rule | venue | source |
|---|---|---|---|
| T1 | intraday momentum: side of prior close → 15:30, held 15:30 → close | MES~SPY | Baltussen et al. 2021 |
| T2 | T1's rule | SPY_RH | same |
| T3 | intraday momentum: side of prior close → 10:00, held 15:30 → close | MES~SPY | Gao et al. 2018 |
| T4 | noise area, 14-day sigma, HH:00/HH:30 decisions, band+VWAP stop | MES~SPY | Zarattini, Aziz & Barbon 2024 |
| T5 | T4's rule | SPY_RH | same |
| T6 | T1's rule, only when the prior day's VIX close > its 252-day median | MES~SPY | Gao et al.: stronger on volatile days |

Anything tested after these is added to this table, and logged, before it runs.

---

## Results

### Round 1 (run 2026-10-27, development window only)

Net is after costs, on a $10,000 account, over 2,264 development sessions. t is
Newey-West on daily net PnL. The bar at 6 trials is t ≥ 2.63 (Šidák).

| # | trades | net | t | Sharpe [95% CI] | deflated Sharpe | random-entry p | failed checks | outcome |
|---|---|---|---|---|---|---|---|---|
| T1 | 2,250 | −$6,544 | −2.64 | −0.86 [−1.52, −0.20] | 0.00 | 0.038 | multiple testing, DSR, cost stress, sub-periods | rejected |
| T2 | 1,242 | −$664 | −0.60 | −0.21 [−0.89, 0.46] | 0.03 | 0.391 | + random entries | rejected |
| T3 | 2,259 | −$11,582 | −4.40 | −1.52 [−2.21, −0.84] | 0.00 | 0.872 | + random entries | rejected |
| T4 | 2,058 | +$3,890 | +1.21 | 0.37 [−0.28, 1.02] | 0.43 | 0.002 | multiple testing, DSR, cost stress, sub-periods | rejected |
| **T5** | 749 | **+$2,317** | **+2.39** | 0.71 [0.10, 1.32] | 0.84 | 0.012 | multiple testing, DSR | open |
| T6 | 999 | −$934 | −0.44 | −0.14 [−0.80, 0.52] | 0.04 | 0.002 | multiple testing, DSR, cost stress, sub-periods | rejected |

What this says:

- **Late-day momentum does not survive MES costs on SPY 2016–2024.** T1 made
  $3,086 gross and paid $9,630 in costs. Gao's 10:00 version (T3) lost before
  costs too. Both papers' samples end before or early in this window; the effect
  may have faded, or it may never have been big enough for one MES a day.
- **The noise area has an entry edge on both venues** (random-entry p = 0.002
  and 0.012), but on MES the costs ($8,808 of $12,698 gross) leave a t of 1.21
  that fails the 2x-slippage stress.
- **T5 is the only rule left standing.** On Robinhood SPY long-only it passes
  cost stress (+$1,852 at 2x slippage), all four quarters net positive except
  the first (−$53, $458, $1,156, $756), and beats random entries. It fails only
  the two checks that charge for the search: t 2.39 against a bar of 2.63
  (family-wise p 0.097), deflated Sharpe 0.84 against 0.95. Its daily PnL
  correlates +0.28 with holding SPY all day, so part of it is long exposure.
- **The VIX filter did not help** (T6 vs T1), against Gao et al.'s prediction.

### Round 2 — pre-registered before running

Each raises the bar for every trial, T5 included.

| # | rule | venue | why |
|---|---|---|---|
| T7 | T5's rule, only when the prior day's VIX close > its 252-day median | SPY_RH | paper's Fig. 8: the noise-area Sharpe rises with VIX; Gao et al. |
| T8 | T4's rule, long only | MES~SPY | does the futures venue work without the short side? |
| T9 | T5's rule, judged with a 3x3 grid: lookback {10, 14, 20} x band multiplier {0.8, 1.0, 1.2} | SPY_RH | plateau and walk-forward: is the published setting a ridge or a spike? Charges 8 grid trials. The published (14, 1.0) stays the rule; the grid is not used to pick a new one |

The bar after round 2 is about t ≥ 2.97 (17 trials).

### Round 2 (run 2026-10-27, development window only)

| # | trades | net | t | Sharpe [95% CI] | random-entry p | failed checks | outcome |
|---|---|---|---|---|---|---|---|
| T7 | 352 | +$962 | +1.18 | 0.35 [−0.27, 0.97] | 0.060 | multiple testing, DSR, random entries | rejected |
| T8 | 1,067 | +$2,914 | +1.46 | 0.45 [−0.19, 1.09] | 0.002 | multiple testing, DSR, sub-periods | rejected |
| T9 | 749 | +$2,317 | +2.39 | 0.71 [0.10, 1.32] | 0.012 | multiple testing, DSR | open (= T5) |

T9's grid, every cell after costs on SPY_RH:

| lookback \ band multiplier | 0.8 | 1.0 | 1.2 |
|---|---|---|---|
| 10 | +$3,140 (t 2.97) | +$2,563 (2.74) | +$1,955 (2.24) |
| 14 | +$2,713 (2.49) | **+$2,317 (2.39)** | +$2,520 (2.76) |
| 20 | +$3,132 (2.94) | +$2,684 (2.73) | +$2,235 (2.43) |

What this says:

- **The VIX filter halves the trades and keeps less than half the profit**
  (T7). The paper's Fig. 8 does not repeat here at a 252-day-median cut.
- **MES long-only works before costs but not reliably after them** (T8): the
  first half of the sample lost money.
- **T5 is a ridge, not a spike.** All nine neighbouring settings made money, t
  from 2.24 to 2.97, and the published setting sits mid-pack. Walk-forward —
  re-picking the best cell every 60 sessions on the prior 250 — banked $2,534
  out of sample, t +2.71, over 34 folds. It passes every check except the two
  that charge for the search.
- **At 17 trials the bar is t ≥ 2.97, and T5's 2.39 has family-wise p 0.25.**
  More searching on 2016–2024 can only raise that bar. The one test left that
  can raise confidence is the untouched holdout, 2025-01-02 → 2026-10-26.

**Status after round 2: T5 is the candidate. Development evidence is consistent
but not significant after the search. Holdout not spent.** (Superseded by round 3.)

---

### Round 3 — exits, pre-registered 2026-10-27 before running

Outside research (2026-10-27):

- **Maróy (2025)**, "Improvements to Intraday Momentum Strategies Using
  Parameter Optimization and Different Exit Strategies": the best exits on the
  noise-area rule were VWAP, VWAP & Ladder and Ladder, with Sharpe "over 3.0".
  Every parameter was optimised and only the abstract is public — that Sharpe
  is the maximum of a search, not evidence. Taken here as a list of exits to
  try, nothing more.
- **Concretum (2026), "How to manage an intraday trend trade"**: four exits on a
  noise-band rule for SOXX, 2012–2026, gross of costs. VWAP had the highest
  Sharpe (1.16 vs 0.92–1.12), but the yearly ranking rotated and the authors
  conclude no exit is clearly better.
- No independent out-of-sample result for the noise-area rule after April
  2024 was found.
- Not taken: the paper's volatility-target sizing (needs leverage a cash
  account does not have; capped at 1x it almost never changes the size), its
  band multiplier of 1.5 and its NR4 / day-of-week tables (all picked on
  2007–2024 data that overlaps this window).

| # | rule | venue | why |
|---|---|---|---|
| T10 | T5 with the long stop on VWAP alone instead of max(band, VWAP) | SPY_RH | the exit both sources rank first |
| T11 | T5 with the band+VWAP stop checked every minute instead of every 30 | SPY_RH | an agent watching live can exit inside the half hour; does it help or just add whipsaw? |

The bar after round 3 is about t ≥ 3.0 (19 trials). A variant replaces T5 in
the paper record only if it passes more checks than T5 or the same checks with
a higher t; otherwise T5 stays frozen.

Added after T10/T11 ran, before running it:

| # | rule | venue | why |
|---|---|---|---|
| T12 | T10's rule judged with T9's grid (lookback {10,14,20} x band {0.8,1.0,1.2}) | SPY_RH | T10 is the best of two exits; is it a ridge like T5, or one lucky cell? Charges 8 more trials (27 total, bar ~3.13) |

### Round 3 results (run 2026-10-27; figures after the code-review fixes below)

| # | trades | net | t | Sharpe [95% CI] | random-entry p | failed checks | outcome |
|---|---|---|---|---|---|---|---|
| T10 | 749 | +$4,013 | **+3.73** | 1.08 [0.48, 1.69] | 0.008 | deflated Sharpe (0.927) | **open — now the candidate** |
| T11 | 749 | +$2,166 | +2.86 | 0.91 [0.36, 1.46] | 0.002* | multiple testing, DSR | rejected |
| T12 | (= T10) | | | | | deflated Sharpe | grid below |

\* T11 ran before the null fix; not re-run, since it is rejected either way.

T12's grid, T10's exit, every cell after costs:

| lookback \ band multiplier | 0.8 | 1.0 | 1.2 |
|---|---|---|---|
| 10 | +$4,471 (t 3.83) | +$3,833 (3.62) | +$3,146 (3.18) |
| 14 | +$4,427 (3.76) | **+$4,013 (3.73)** | +$3,589 (3.56) |
| 20 | +$4,564 (3.94) | +$4,383 (4.10) | +$3,680 (3.71) |

Walk-forward (re-pick the best cell every 60 sessions on the prior 250): $3,881
out of sample, t +3.78, 34 folds. Plateau: every neighbour made money.

**T10 passes every development check but one.** t 3.73 against a bar of 3.12
at 28 trials, family-wise p 0.005. The deflated Sharpe is 0.927 against 0.95.
The entries are T5's, unchanged; letting winners run to VWAP instead of cutting
them at the band is the whole difference (median hold 150 min vs 60; win rate
49% vs 42%).

**Stress tests on T10** (diagnostics on the fixed rule, not trials — nothing
was picked from them):

| test | T5 | T10 |
|---|---|---|
| as run | +$2,317, t 2.39 | +$4,013, t 3.73 |
| signal acted on 1 minute late | +$2,004, t 2.08 | +$3,674, t 3.42 |
| 5 minutes late | +$1,218, t 1.26 | +$2,726, t 2.52 |
| slippage 3 cents a side | +$1,387, t 1.43 | +$3,083, t 2.86 |
| slippage 5 cents a side | +$457, t 0.47 | +$2,153, t 2.00 |
| without the best 10 days | +$499, t 0.58 | +$2,162, t 2.18 |
| without the best 20 days | −$593 | +$977, t 0.98 |
| alpha vs SPY open→close (NW) | $0.85/day, t 2.20, beta 0.08 | $1.53/day, t 3.64, beta 0.11 |
| losing years (2016–2024) | 2017 (−$64) | 2017 (−$37) |

What the stress tests say:

- T10 survives late fills and 3–5x slippage that would kill T5.
- **It lives on trend days.** Removing the best 20 of 2,264 sessions takes it
  to t 0.98. That is the shape of any trend-following rule, and it means a
  paper record of a few months can easily show nothing even if the edge is
  real.
- Beta to the day's SPY move is small (0.11); the return is not just being
  long. Daily correlation with holding SPY all session is +0.36.

**Code review, 2026-10-27** (`/code-review high` on `intraday/`): 11 findings,
all fixed, 40 tests pass. Two changed numbers:

1. The random-entry null cut random trades short at the close, so random books
   held less drift than the real one. Fixed to fit each random trade's full
   holding time inside its session. The null mean rose and p weakened (T5
   0.012 → 0.038, T10 0.002 → 0.008); both still pass.
2. `sweep()` built every grid cell from class defaults, ignoring the settings
   not on the grid. Caught **before** T12 ran (T12 would have re-tested T5's
   exit). T9 was unaffected (T5 is all defaults).

Others: the paper record now skips sessions whose bars stop before the close;
`holdout()` refuses bars that end before the holdout start (it would have
spent the one look on nothing); a NaN band no longer forces an exit under the
VWAP stop; network errors in the bar fetch are retried; `results.jsonl` lines
are time-stamped.

**Status: T10 is the candidate. It clears the search-adjusted t-bar but not the
deflated Sharpe. Holdout not spent. Paper record starts 2026-10-28.**

---

## Paper record (from 2026-10-28)

Decision 2026-10-27: **keep the holdout unspent and paper-trade first.** The
holdout is one look because a second look, after changing anything in
response to the first, turns it into development data. Paper sessions are new
data no decision has touched, so they add evidence without spending it.

The rule was switched from T5 to T10 the same day, before the first paper
session, under the replacement rule written before round 3 ran.

- Script: `intraday/scripts/paper_trade.py` (`... paper_trade.py status`).
  Weekdays 16:45 ET via `intraday/scripts/paper_trade.plist`, installed as
  `com.thomaskramer.intraday-paper`. No orders.
- Rule frozen: `NoiseArea(exit_rule="vwap")` — lookback 14, band 1.0,
  HH:00/HH:30 checks, VWAP trailing stop — long only, $10,000 per entry, one
  entry a session, `SPY_RH` costs. Each row of `intraday/data/paper_t10.jsonl`
  carries the rule's hash; rows are appended once and never rewritten.
- Fills are modelled (next-bar open + 1 cent), not broker fills.
- What to expect if the backtest holds: about $1.77 a session, one trade every
  three sessions, and most of the money from a few trend days. A t-stat on the
  paper record needs a year or more to mean anything. Before that, read it for
  gross failure: trade counts far off 0.33/day, or a loss larger than the
  backtest's worst stretch.

**Numbers to judge it by (dev backtest 2016–2024, $10k, computed 2026-10-27):**
mean $1.77 a session, sd $26.03. Worst drawdown −$309 (2020-03-17 → 03-25).
Worst rolling loss: −$207 over 21 sessions, −$247 over 60, −$264 over 120;
the 5th percentile of any 60-session window is −$106. Proving it with paper
alone, at t = 2, takes ~650 sessions (~2.6 years) if the backtest is right.

- **Stop line:** a paper drawdown past −$309 (worse than anything in nine
  years of backtest) means the rule is not behaving as tested. Stop and
  investigate before trusting any further paper days.
- **Trade-count check:** after 60 sessions, fewer than ~10 or more than ~30
  trades (expected ~20) means the live data or the code differs from the
  backtest.

**Pre-flight 2026-10-27.** 40 intraday tests pass. The launchd job is loaded.
A dry run of `record()`'s exact path (Alpaca fetch → engine → `new_rows`,
written nowhere) ran on 2026-09-14 → 2026-10-27 bars: it marked 10-27 as
incomplete, gave rule hash `0e229f5a8b71`, and produced 9 trades over the 21
sessions 2026-09-28 → 10-26, net −$43.21.

> **Those 21 sessions are holdout data, and they have now been seen** (they
> fall inside 2025-01-02 → 2026-10-26). Nothing was changed in response; the
> rule was frozen before the dry run. Any later holdout look must end on
> 2026-09-25 to stay clean, or must report this look. The result is within
> noise: the backtest's 5th percentile of a 21-session window is about −$100.
