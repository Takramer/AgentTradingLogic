# Intraday — how to test a day-trading rule

**Started 2026-10-27.** Code: `intraday/`. Lab: `intraday/notebooks/lab.ipynb`.
Tests: `intraday/tests/`. Ideas: [IDEAS.md](IDEAS.md).

Day trading is a separate problem from the swing-trading research in
`rhengine/`, and this folder treats it that way. **No day-trading rule has been
chosen yet.** Nothing decided in the swing work carries over: not its rejected
signals, its verdicts, its holdouts or its trial count. What does carry over is
the method — the checks below catch a backtest lying, whatever the holding
period.

The harness **backtests only**. There is no order code in the package, a test
keeps it that way, and a rule that clears here has earned a paper-trading run,
nothing more.

---

## Workflow

1. **Get data.** SPY one-minute bars come from
   `python intraday/scripts/fetch_alpaca_bars.py SPY 2016-01-01 <end>` (Alpaca
   SIP feed, needs the Alpaca keys already in secrets.json). For real MES bars,
   put a Databento `ohlcv-1m` export (parent symbol `MES.FUT`) at
   `intraday/data/MES_1m.csv`; it loads as-is. Both are gitignored.
   Do not scrape OpenMarket or TradingView: both sites' terms forbid it.
2. **Log the idea first:** `python -m intraday log --label "<the idea>"`.
   This writes to intraday's own ledger, `intraday/data/trials.jsonl`.
3. **Pick a holdout start** and never develop past it. `evaluate()` and
   `sweep()` refuse bars at or after `holdout_start`.
4. **Write the rule** as a `Strategy` subclass (see `strategies.py`), then
   `engine.causality_check(strategy, bars)`.
5. **`validate.evaluate(...)`** with the `grid` you are choosing from. Read the
   report: every FAIL is a reason, not an obstacle.
6. **Only if nothing FAILs:** run `validate.holdout(...)` once (it logs the look
   to `intraday/data/holdout.jsonl` before it runs) and pass its result back to
   `evaluate`.
7. **Record the outcome**, win or lose:
   `python -m intraday log --label outcome --resolves "<the idea>" --outcome rejected`

---

## What the engine assumes (`engine.py`)

| rule | why |
|---|---|
| signal on bar t's close fills at bar t+1's **open** | filling on the bar that produced the signal is look-ahead |
| if a bar touches both stop and target, **the stop fills** | a bar does not record which came first |
| a bar that opens through a stop fills at the open | stops are not guaranteed prices |
| after a stop or target, flat until the target changes | otherwise the rule re-enters on the next bar |
| **flat every session**; last-bar signals discarded | no overnight risk, no overnight margin, no roll gaps booked as PnL |
| every trade pays `instrument.trade_cost(qty, entry, exit)` | MES: $4.28, assumed IBKR; SPY_RH: SEC fee on the sale + 1 cent per side |
| `notional=` sizes each trade in dollars instead of contracts | how a cash account trades fractional SPY |
| `long_only` instruments clip short targets to flat | a cash account cannot short |
| `settled_cash_only` caps entries per session at capital / notional | Robinhood cash: sale proceeds settle T+1 |
| contracts x intraday margin must fit in capital | IBKR would refuse the order |
| daily PnL covers every session, 0 when idle | dropping idle days inflates the t-stat |
| returns on a fixed account size | no compounding of early luck |

## The broker it assumes (`instruments.py`)

**Interactive Brokers, IBKR Pro fixed**, chosen 2026-10-27 because a program can
run it end to end: free Python API (TWS / IB Gateway), a paper account on the
same API, no API fee.

| item | MES, per contract | source |
|---|---|---|
| commission | $0.25 per side | IBKR rate card (as published on its Singapore site) |
| CME exchange + clearing | ~$0.62 per side | broker-reported; another source says ~$0.35, the higher is used |
| NFA | $0.02 per side | broker-reported |
| slippage | 1 tick ($1.25) per side | assumed |
| **round trip** | **$4.28** | 2x stress: $6.78 |
| intraday margin | $2,500 assumed | one report says ~$1,611; check in TWS |

Rules that matter for a day trader at IBKR: the pattern-day-trader rule does not
apply to futures; there is no margin call, IBKR liquidates on its own when equity
falls below maintenance; a position still open at the contract's intraday end
time moves to overnight margin; futures need a margin account.

**Robinhood SPY (`SPY_RH`), added 2026-10-27.** The agent account is a cash
account with options level 2. No futures, no shorting, settled cash only (one
full-size round trip a day), $0 commission. The pattern-day-trader $25k rule
was removed in 2026. Per dollar traded, SPY here costs about a quarter of MES at
IBKR, which is why the only surviving candidate (T5) runs on it.

`MES_ON_SPY` prices MES costs onto SPY bars (qty 50 = one MES) until real MES
bars are bought.

Considered and not chosen: Tradovate (lower $50 day margin, but a paid API
add-on and possibly a CME data licence for API data), NinjaTrader (automation
only through its desktop scripting), Robinhood (cheaper with Gold, no futures
for the agent account).

Every figure is assumed until executed fills say otherwise. Raise a cost
freely; lower one only with fill evidence.

---

## The checks (`validate.evaluate`)

| check | passes when | catches |
|---|---|---|
| trial logged | the label is in intraday's ledger | an unrecorded search |
| look-ahead | signals unchanged at 20 random truncation points | `shift(-1)`, centred windows, the session's high used before the close |
| sample size | ≥ 250 sessions and ≥ 100 trades | anecdotes |
| multiple testing | Newey-West daily t ≥ the Šidák bar for every intraday trial | the best of N tries |
| deflated Sharpe | P(true Sharpe > 0) ≥ 0.95 at the logged trial count | same, priced differently |
| random entries | p < 0.05 against the same trades at random times | exits + drift mistaken for an entry edge |
| cost stress | still net positive at 2× slippage | an edge thinner than one tick |
| sub-periods | ≥ 3 of 4 quarters of the sample net positive | one lucky stretch |
| survivorship | `continuous_futures`, `single_instrument` or `pit_universe` | symbol lists chosen today (`static_list` fails) |
| walk-forward | pooled out-of-sample net > 0 and t ≥ 1.65 | a setting picked on the data it is judged on |
| plateau | ≥ 2/3 of the best setting's grid neighbours made money | a spike, not a ridge |
| holdout | first look ever logged, and net positive | the final, once-only test |

Every statistic is computed on **daily** net PnL, never per trade: trades on the
same day are not independent. The report also prints the Sharpe with its 95% CI
and the long-every-session baseline (`SessionDrift`) with its daily correlation
to the rule: a long-biased rule that tracks it is holding the market, not timing
it.

`sweep()` charges the ledger for the grid automatically (one row,
n = combos − 1), once per grid and data span, so re-running a cell is free and
widening the grid is not. The trial count starts at zero for day trading; it
does not inherit the swing work's count.

Verdicts: **CLEARS** (every check passed, holdout included), **INCOMPLETE**
(nothing failed, something did not run — always the case before the holdout),
**DOES NOT CLEAR**.

---

## Outside research

YouTube strategies, X posts and NotebookLM notebooks are sources of ideas. The
route for each:

1. Pull out one rule with exact entry, exit, stop and session times. A claim
   with no testable rule ("read the tape") is filed and skipped.
2. Log it as one trial, citing where it came from.
3. Write it as a `Strategy`, run `evaluate`, record the outcome.

Each idea tested is one more trial, and the bar every later idea must clear
rises with it. That is what stops a long search from producing a "winner" by
luck.

---

## Limits

- One instrument at a time, one position, fixed size. No scaling in or out.
- Fills are bar prices plus assumed slippage. Queue position and the opening
  spread are not modelled.
- The random-entry null approximates the engine's exits (stops over the
  trade's holding window, else a time exit).
- `causality_check` samples 20 cuts; it can miss a leak that bites only at
  rare bars.
- Fees and margin are assumed, not taken from fills or the broker's live
  margin screen.
