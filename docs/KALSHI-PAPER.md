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
