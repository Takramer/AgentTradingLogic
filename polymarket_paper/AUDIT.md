# Audit of the BTC 15-minute paper rule — 2026-10-06

The shipped replay lost **525.1575 USDC** over six months (paid balance
474.8425 from 1000). This file says why that shape loses, then scores ten
follow-ups that were logged before they were run. Research claims stay in
`rhengine/alpha/book.py`. Nothing here edits `config.json`.

## Why a 68.5% win rate lost money

The shipped path, already logged in `REPLAY.md`: 16,877 trades, 11,566 wins,
average win +0.7534 USDC, average loss -1.7395 USDC.

The payoff was visible before that run. Take-profit is +0.10. On 5 contracts
that is +0.50 when the exit is exactly one tick through the target, and the
measured average win was +0.75 because some exits overshoot. A stop from a
0.70 entry down to 0.40 is -1.50. A hold from 0.70 to 0 is -3.50. The loss is
about 2.3 times the win, so the win count has to clear about 70%. It cleared
68.5%.

That is the same shape as a public BTC 15-minute write-up that reported a 92%
win rate and a negative expectancy (Jimmy6929, `Polymarket-BTC-15-Minute-Trading-Bot`,
`REPORT.md`). The win rate is not the result. This audit does not use that
repo's number.

## What the follow-ups are allowed to change

Each row changes one knob on the shipped rule (fingerprint
`a0a4df1c15e64390`) and rescores the same 17,280 windows
(2026-04-09 21:15 UTC through the window starting 2026-10-06 21:00 UTC).

| Trial | Change |
|---|---|
| same entries, hold to Gamma | stop off, take-profit off |
| stop 0.40, no take-profit | winners are held |
| take-profit +0.10, no stop | losers are held |
| timeDelay 5 | enter only in the last 5 minutes |
| timeDelay 2 | enter only in the last 2 minutes |
| entry [0.65, 0.75] | narrower band |
| entry [0.55, 0.65] | cheaper contracts |
| stop 0.55 | tighter stop |
| take-profit +0.20 | wider take-profit |
| shipped rule with crypto taker fee 0.07 | same exits, fees on |

Keep a variant only if its **net** pnl is positive on the full path, on the
earliest 8,640 windows, and on the latest 8,640 windows. Net subtracts the
taker fee below. A keep is an in-sample candidate. It does not become the
shipped rule.

The fee is the schedule printed on these markets (`feeSchedule.exponent` 1,
`rate` 0.07, taker only), which is the crypto row of
`https://docs.polymarket.com/trading/fees` checked 2026-10-06:

`fee = shares × 0.07 × price × (1 − price)`

Rounded half-up to 5 decimals. A raw fee below 0.00001 is 0. Charged on the
entry and on a stop or take-profit exit. A hold pays the entry fee only.
Eight markets spread across the cache, plus three spot checks, all carried
this schedule. The paper fill is still the midpoint. The spread is not charged.

Day-block bootstrap of the shipped sum: block 96 windows (one day), 1,000
draws, seed 20261006, 2.5% and 97.5% percentiles. That interval describes the
path already logged. It is not an eleventh trial.

Methods looked at and not ported: apex-dao's order-book replay (this cache is
minute mids) and LuciferForge's Binance late-snipe (a different rule; Binance
returned HTTP 451 from this environment).

## Results

Filled after `python3 -m polymarket_paper.audit`. The command refuses to run
until the ten labels are in `research_data/trials.jsonl`.
