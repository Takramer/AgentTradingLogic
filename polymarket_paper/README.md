# Polymarket paper trader

Paper-trades the crypto up/down rule from the public Nightshark notes:
<https://code.processoverprofit.blog/docs/youtube-code/video-13-polymarket-crypto-bot/>

This folder is its own space. It is walled off from `rhengine`. A balance here
is a paper mark for that rule. Research numbers still come from
`rhengine/alpha/book.py`.

## Run it

From the repo root:

1. `python3 -m polymarket_paper --once`
2. Confirm the log shows a BTC open price plus UP and DOWN.
3. Leave it on with `python3 -m polymarket_paper`

On Windows, from this repo, use `py` in place of `python3`.

A working `--once` looks like this. This read was 2026-10-06 20:27 UTC.
Both contract prices were outside the entry band, so the account stayed at 1000:

```text
Polymarket PAPER TRADING Started | TimeFrame: 15M | EntryRange: [0.60, 0.80] | Contracts: 5 | Exit: 0.40 | TakeProfitPts: 0.10 | Delta: 0 | Window: last 15m | InitialBalance: 1000.00
BTC Polymarket session (btc-updown-15m-1791317700) | BTC OPEN : 85621.10
BTC Monitoring Polymarket entry range [0.60, 0.80] | Delta >= 0
BTC UP 0.55 | DOWN 0.46 | OPEN 85621.10 | LIVE 85617.15 chainlink | DELTA 3.95 | 2.5m left | MONITORING
BTC UP 0.55 / DOWN 0.46 outside entry range [0.60, 0.80]
CASH 1000.00 | EQUITY 1000.00 | PNL 0.00 | WINS 0/0 | AVG WIN n/a | AVG LOSS n/a
```

Ctrl+C stops it. The account is saved in `polymarket_paper/state.json`.
The log is `polymarket_paper/paper.log`.

Change a knob in `config.json`, then start again. If the strategy fields
changed, the old account is kept aside until you run:

`python3 -m polymarket_paper --reset`

## Nightshark

You can paper-trade this rule without paying Nightshark.

Their app (nightshark.io, pricing page checked 2026-10-06) is what the
AutoHotkey script plugs into. The video page says "Latest Nightshark Version
Required" because the script calls their log window, their price helper, and,
for live orders, their signer.

| Plan | Price | What you get |
|---|---|---|
| Free | $0, no card | 1 hour of runtime per day, core features |
| Monthly | $30/month | Unlimited runtime, plus their AI / multi-agent extras |
| Annual | $300/year | Same unlimited runtime, $60 less than twelve months |

One free hour cannot sit on every 15-minute window in a day. This runner has
no runtime cap. Polymarket's market-data APIs used here are public, so a
paper account does not need a Polymarket login.

## The rule in `config.json`

| Knob | Shipped value | Meaning |
|---|---|---|
| `assets` | `["BTC"]` | Also ETH, SOL, XRP, DOGE, HYPE, BNB |
| `TimeFrame` | `15M` | `5M`, `15M`, or `1H` |
| `timeDelay` | `15` | Trade only in the last N minutes of the window |
| `EntryRange` | `[0.60, 0.80]` | Buy the side whose price sits in this band |
| `Exit` | `0.40` | Sell when that side's price is below this |
| `TakeProfitPts` | `0.10` | Sell at fill + this. `1` holds to the paper resolution |
| `Delta` | `0` | Also require \|session open − live price\| at least this |
| `orderSize` | `5` | Contracts per entry. Polymarket's live minimum is 5 |

With `timeDelay` equal to the whole window, the bot can enter any time after
the open price prints, except the last 30 seconds. One position per asset.
A stop or a take-profit spends the rest of that window flat.

## How a fill is counted

The fill price is the CLOB midpoint. The paper account charges no taker fee.
The live BTC 15-minute market reports fees enabled (`crypto_fees_v2`), so a
real fill would be worse than this log.

At the end of the window the script settles the contract at 1 when the last
midpoint was at least 0.50, and at 0 otherwise. The market itself resolves on
a Chainlink BTC/USD 60-second TWAP. Those two can disagree.

`EQUITY` is cash plus the open contract at the latest midpoint. `PNL` is
equity minus the starting balance.

## Prices

- Contract prices: `gamma-api.polymarket.com` for the current slug, then
  `clob.polymarket.com/midpoint`.
- Session open: `polymarket.com/api/crypto/crypto-price`.
- Live underlying: Polymarket's public Chainlink socket
  (`wss://ws-live-data.polymarket.com`). That socket sends a snapshot and
  then stays quiet, so this runner resubscribes every few seconds.
  Authenticated live prices on `ws-live-v2.polymarket.com` need a CLOB API
  key; this runner does not use one.
- If that print is older than `feedMaxAgeMs` (60s), the runner uses Coinbase
  spot, then Binance spot. Binance is the video script's 1-hour source.
  Binance returned HTTP 451 from the environment this was written in, which
  is why Coinbase is in the chain.

`Delta` is in the underlying's own units (dollars for BTC), not a percent.
The shipped value is 0, so any live print is enough.

## Replay

`python3 -m polymarket_paper.backtest`

`python3 -m polymarket_paper.audit` explains that replay. The exits turned
a +508.5525 hold into the -525.1575 loss, and the taker fee is larger than
the hold edge. The ten follow-ups in `AUDIT.md` all failed the keep rule.

Replays the shipped rule on public CLOB history and prints the 1-month,
3-month, and 6-month slices together. Cache lands in `polymarket_paper/cache/`.
The 2026-10-06 record is `polymarket_paper/REPLAY.md`. Six-month paid PnL
was -525.1575 USDC from a 1000 start. The trial is rejected. Research
claims stay in `rhengine/alpha/book.py`.

## Tests

`python3 -m unittest tests.test_polymarket_paper`
