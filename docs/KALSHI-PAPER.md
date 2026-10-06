# Kalshi v5 paper verification

Nightshark is paid. You do not need it to paper-trade this script.

The rules are the **paper** script on
[Video 14: Kalshi Trading Bot v5](https://code.processoverprofit.blog/docs/youtube-code/video-14-kalshi-v5/).
That page also ships a **live** script. This runner does not. The live
script places orders, and its settings are not the paper settings:

| | Paper (this runner) | Live (that page, not built here) |
|---|---|---|
| Entry range | 0.75–0.85 | 0.80–0.93 |
| Daily loss | -600, paper account | -200, Kalshi balance change |
| Orders | none | real Kalshi orders |

The original scripts need Nightshark dated **July 27, 2026** or newer.
An older Nightshark install will not run them. Re-download from the
Nightshark site if you want the AutoHotkey host. The live script also
needs an API key file and a private key file inside that host.

```bash
py -m rhengine.kalshi_paper --once
py -m rhengine.kalshi_paper
```

`--once` takes two polls with no wait. The script's first sighting of a
market only announces the session; the second poll is the one that can
enter. The long-running command does those about 700ms apart. Ctrl-C
stops it. Settings live in
`rhengine/kalshi_v5_paper.json`. The log is
`research_data/kalshi_paper/events.jsonl`. The latest book is
`research_data/kalshi_paper/status.json`.

## What Nightshark was doing

The script you have is AutoHotkey written for Nightshark (nightshark.io),
a paid Windows host. These calls are Nightshark's, not Python and not
Kalshi:

| Call | What this runner does instead |
|---|---|
| `HttpGet` | Public `GET` against the same Kalshi hosts as `market_data_kalshi.py` |
| `GetKalshiPrice` | Coinbase last trade, Kraken if Coinbase fails |
| `Log` / `LogStream` | Terminal line, plus the jsonl and `status.json` |
| `stopCode` | The process exits |

Market data and the order book are public. No Kalshi API key. The process
has no order call. A restart starts a new paper account at `initialBalance`.
The jsonl file is append-only, so a new run is a new `"session"` id in that
file, not a continuation of the old cash balance.

## The rules that are running

Shipped settings, same numbers as the script:

| Setting | Value | Meaning |
|---|---|---|
| `initialBalance` | 1000 | Paper cash, USD |
| `timeDelay` | 8 | Trade only in the last 8 minutes of the 15-minute contract |
| `EntryRange` | 0.75–0.85 | Buy the side whose ask is inside this range, inclusive |
| `Exit` | 0.40 | Stop when that side's price is below 0.40 |
| `TakeProfitPts` | 0.10 | Sell when price reaches fill + 0.10. A target above 1.00 is not a take-profit; the contract is held to resolution |
| `Pings` | 1 | One extra one-second check before a stop is allowed to fire. A rebound to 0.40 or above cancels the stop |
| `Delta` | 0 | `|spot − 15m open|` must be at least this. 0 still requires both numbers to exist |
| `orderSize` | 1 | Contracts, not dollars. A fill at 0.80 costs 0.80 |
| `Daily_Profit_Target` | 400 | Stop the process after flat PnL is above this. Compared in cents. 0 disables |
| `Daily_Loss_Target` | -600 | Stop the process after flat PnL is below this. Compared in cents. 0 disables |
| `assets` | BTC | Series `KXBTC15M`. ETH, SOL, XRP, DOGE, HYPE, BNB are in the map |

UP is the YES ask, priced as `1 − best NO bid`. DOWN is `1 − best YES bid`.
One contract pays 1 if that side wins and 0 if it loses. Cash drops by the
fill when the paper order is accepted and rises by the exit price when it
closes. A zero PnL is counted as a loss, matching the script.

The first poll of a new market only announces the session. The next poll
can enter. A contract sent to resolution does not block the next window,
and the daily target is not checked while any contract is still open or
waiting on a result.

## Differences from the Nightshark script

1. Spot is Coinbase (Kraken fallback). The 15-minute open used for `Delta`
   is the contract's `floor_strike`, which is Kalshi's target price for
   that window. `GetKalshiPrice` was a host function and was not in the
   script. With `Delta` at 0 this only checks that both numbers exist.
   If you raise `Delta`, a Coinbase print and Kalshi's strike can disagree
   by tens of dollars, and that gap counts.
2. A missing side price stays missing. The script would coerce a blank
   quote to 0 and could stop a position out at 0. This runner will not.
3. A market with under 30 seconds left is never selected. The script
   skips those on the first three tries and can still take one on the
   fourth.
4. Fees are not charged. The Nightshark script does not charge them
   either. Live Kalshi fees would make this account look worse.

## What a log is not

PnL in the terminal, in `status.json`, and in the jsonl file is this
paper account. It is not an `rhengine.alpha.book` result. Do not quote it
as an edge, a Sharpe, or a t-stat. There is no trial-ledger entry for
running it: the rules were supplied, not searched for.

`equity` marks open and unsettled contracts. `cash` does not, until the
result is in. Read equity while something is pending.

## Replay

```bash
py -m rhengine.kalshi_backtest
py -m rhengine.kalshi_backtest --offline
```

The first command downloads settled `KXBTC15M` minute bars into
`research_data/kalshi_paper/hist/` (not committed) and replays the paper
rules. `--offline` reruns the arithmetic on that cache. No orders.

The live settled list starts **2026-07-31**. Contracts older than the
**2026-08-07** historical cutoff are on `/historical/markets`. That
archive is the same "BTC price up in next 15 mins?" series and goes back
to **2025-12-10**. Paging only the live list makes 3 months and 6 months
look empty. They are not.

Windows are 30, 90, and 180 days, ending at the latest settled close in
the cache. Those are the 1-month, 3-month, and 6-month rows. Each window
is a fresh $1000 account. `kill on` is the script: the process stops once
flat PnL is above +400 or below -600, compared in cents. `kill off` is
the same rules with that stop removed, so one early halt does not hide
the rest of the window.

Minute bars are not the 700ms poll. Exits start the minute after entry.
The replay assumes the process was already watching when the window
opened.

| Mode | What it books |
|---|---|
| `close` | The minute-end ask. Enter only when that close is inside 0.75–0.85. Stop only when a later close is below 0.40. A take-profit minute is booked at that close, which can be past fill+0.10 |
| `touch` | Enter when the repaired high/low overlaps the band, at the highest price inside it. Take profit at the target. A stop needs the minute to finish below 0.40, filled at that close, so a crash is booked at the low close. A dip that recovers before the minute ends is kept |

Empty-book prints of 0 and 1 are repaired when the close is a real price.
A close that itself prints 0.00 or 1.00 is kept. `Delta` is 0. The check
passes when `floor_strike` is present. The Coinbase path is not rebuilt.
A contract with no strike does not enter. Fees are off, same as the script.

## Replay results

Run on 2026-10-06. Latest settled close in the cache: **2026-10-06T21:15:00Z**.
Each row starts at $1000, one contract, BTC only. The +400 / -600 stop
never fired, so the kill-switch account and the no-kill account are the
same numbers. Touch at 180 days finished at -536.07, still above -600.

| Window | Markets with bars | Mode | Trades | Wins | Losses | Stop | Take profit | Held to settlement | PnL | End cash |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30d, 2026-09-06 → 2026-10-06 | 2849 / 2849 | close | 1938 | 1596 | 342 | 342 | 1596 | 0 | +25.42 | 1025.42 |
| 30d, 2026-09-06 → 2026-10-06 | 2849 / 2849 | touch | 2546 | 2023 | 523 | 523 | 2023 | 0 | -87.80 | 912.20 |
| 90d, 2026-07-08 → 2026-10-06 | 8524 / 8530 | close | 5733 | 4660 | 1073 | 1073 | 4660 | 0 | +36.19 | 1036.19 |
| 90d, 2026-07-08 → 2026-10-06 | 8524 / 8530 | touch | 7605 | 6046 | 1559 | 1559 | 6046 | 0 | -263.39 | 736.61 |
| 180d, 2026-04-09 → 2026-10-06 | 17024 / 17032 | close | 11035 | 8952 | 2083 | 2083 | 8952 | 0 | +62.39 | 1062.39 |
| 180d, 2026-04-09 → 2026-10-06 | 17024 / 17032 | touch | 14915 | 11848 | 3067 | 3067 | 11848 | 0 | -536.07 | 463.93 |

No candle download missed. Five markets in the 90-day window and 25 in
the 180-day window had no `floor_strike`, so they could not enter. The
settled list has a weekly gap of about two hours, around 07:00–09:15 UTC.
The three windows are different market sets. 180 days is inside the
archive, which starts 2025-12-10.

No trade was held to settlement. After a fill inside 0.75–0.85, a later
minute went through the take-profit or through 0.40. On the 30-day book
the close-mode take-profit exited at a median 0.944 (entry median 0.80)
and the stop exited at a median 0.24. Touch-mode take-profits exited at
the 0.10 target (median PnL +0.10) and stops exited at a median 0.28.
That is why close mode's cash is up by tens of dollars and touch mode's
cash is down by hundreds: close mode is paid the minute-end price after
the target, and touch mode is paid the minute-end price after the crash.
The script sells at the first poll that reaches fill+0.10, and its stop
waits one second. Neither minute-bar fill is that poll.

This cash is the replay. It is not an `rhengine.alpha.book` result. No
Sharpe, no t-stat. The `kalshi-v5` trial logged before the run stays
inconclusive. The rules were supplied. The trial is the decision to
measure these three windows together, not a search.

## Why touch mode loses

```bash
py -m rhengine.kalshi_audit
```

Touch mode over the frozen 180 days, 2026-04-09T21:15:00Z through
2026-10-06T21:15:00Z, is the published-rules row: 14,915 trades, win rate
79.4%, average win +0.100, average loss −0.562, cash −536.07. That payoff
needs an 84.9% win rate to break even. It is short by 5.4 points, which
is −3.6 cents a trade.

The entry causes it. Touch mode buys the highest price inside 0.75–0.85.
The mean fill is 0.819, and 6,969 of the 14,915 fills are at the 0.85 cap.
Those same contracts settle in your favor 78.4% of the time. A contract
bought at 0.819 has to win 81.9% of the time to be worth what you paid.
Holding every one of those entries to $1 or $0 loses **$518.50**. The
take-profit and the stop together lose **$536.07**. The exit adds about
$18. It is not the source of the loss.

The cheap end of the band is the worst buy. The expensive end is where
most of the fills land, and it is still short of the price.

| Fill | Trades | Mean price | Settles as a win | Shortfall | Trade PnL |
|---|---:|---:|---:|---:|---:|
| 0.75–0.78 | 2646 | 0.758 | 68.7% | 7.1 cents | −195.85 |
| 0.78–0.82 | 3103 | 0.795 | 74.2% | 5.3 cents | −187.24 |
| 0.82–0.85 | 9166 | 0.845 | 82.7% | 1.8 cents | −152.98 |

Stops are 3,067 trades and −$1,723.70. Held to settlement those same
stops would have been −$1,765.21, and 23.4% of them would have won.
Take-profits are 11,848 trades and +$1,187.63. Held to settlement they
would have been +$1,246.71, and 92.7% of them would have won. The
10-cent cap gives up about $59 of settlement value. The stop, filled at
the minute close after the break, saves about $41 versus settling at 0.
Net, the exit is a small drag on a bad purchase.

The minute-close stop fill makes the dollar loss look larger than a
one-second ping would. Selling at 0.40 when the bar did not open already
through the stop loses $42.24 on the first half and $31.47 on the second
half, about $74 together. That row is a fill sensitivity. It is still a
loss. Resampling the 181 calendar days, the touch-mode −536.07 has a
band of −601.31 to −471.35. One bad day does not carry it.

Close mode is a different set of entries: it buys only when the minute
ends inside the band, at a mean price of 0.800. Those entries, held to
settlement, make +$51.68. The close-mode account makes +$62.39 because
the take-profit is booked at the minute close (average win +0.141, not
+0.100). Its day-block band is +2.39 to +121.70, which includes a result
next to zero. The win rate is 81.1% against an 80.3% breakeven.

`KXBTC15M` is fee type `quadratic` with multiplier 1. The general schedule
is `round up(0.07 × C × P × (1−P))` to the next cent. One contract at
0.80 is 0.0112, which rounds up to 0.02. There is no settlement fee.
Charging that taker fee on entry and on the exit takes the touch account
from −536.07 to **−922.37** (day-block band −988.46 to −854.97) and the
close account from +62.39 to **−247.02** (band −307.06 to −187.58). A
resting take-profit on this series is not charged, because the fee type
is `quadratic` and not `quadratic_with_maker_fees`. Leaving the
take-profit uncharged and still charging the entry and the stop leaves
touch at −803.89 and close at −162.30.

## What was tried

Eight rule changes were logged before they ran, plus the fee row and the
stop-at-0.40 row. The screen is 2026-04-09 to 2026-07-08. The confirm
half is 2026-07-08 to 2026-10-06, which is the 90-day touch row already
printed above (−263.39). A variant had to beat the published touch rules
on both halves, with at least 100 trades on each. Beating the baseline
was not enough to edit `kalshi_v5_paper.json`. That file is the published
script. Every variant that beat it was still a loss on both halves, so
none of them was installed.

| Variant | Apr 9–Jul 8 | Jul 8–Oct 6 | Result |
|---|---:|---:|---|
| Published touch rules | −272.68 / 7310 | −263.39 / 7605 | baseline, halves sum to −536.07 |
| Hold to settlement | −280.21 / 7310 | −238.29 / 7605 | rejected, screen was worse |
| Drop the stop, keep the take-profit | −294.45 / 7310 | −238.34 / 7605 | rejected, screen was worse |
| Drop the take-profit, keep the stop | −235.81 / 7310 | −249.10 / 7605 | loses less, still about −485, not installed |
| Band 0.75–0.80 only | −163.25 / 6783 | −155.78 / 7104 | −2.4 then −2.2 cents a trade, still a loss, not installed |
| Band 0.80–0.85 only | −173.83 / 7298 | −180.63 / 7580 | −2.4 cents a trade on both halves, not installed |
| Last 3 minutes only | −188.64 / 4065 | −236.14 / 4479 | −4.6 then −5.3 cents a trade, worse than the baseline's −3.7 / −3.5, not installed |
| UP only | −233.92 / 4600 | −222.30 / 4733 | fewer bets, worse per trade than the baseline, not installed |
| DOWN only | −174.30 / 4595 | −199.61 / 4796 | fewer bets, about the same per trade, not installed |
| Stop filled at 0.40 unless the bar gaps | −42.24 / 7310 | −31.47 / 7605 | sensitivity, still a loss |

Holding to settlement and dropping the stop both lost more on the screen
half than the published rules. The loss is already in the purchase.
Removing the 10-cent cap keeps the same trades and gives back a little of
the tail to $1. It does not make the purchase worth the price. Restricting
the band lowers the price touch mode is willing to pay, and the account
is still down on both halves. Cutting the clock to 3 minutes, or trading
one side, shrinks the dollar loss by placing fewer negative bets.

Third-party Kalshi backtesters (a price-range engine, and a binary
backtester with a fee model and a Brier score) want either a book
snapshot or a settlement path. This cache is one-minute bid/ask bars.
The two fills and the stop-at-0.40 row are the bracket those bars can
support. No outside project was imported. The fee numbers use Kalshi's
series record and the general quadratic schedule, checked in
`tests/test_kalshi_audit.py` against the published one-contract and
100-contract table.

The eight rule trials and the two sensitivities are resolved in
`research_data/trials.jsonl` as rejections or inconclusive measurements.
None is kept. No Sharpe, no t-stat. This remains a paper account.
