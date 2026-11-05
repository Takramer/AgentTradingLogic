# BTC 15-minute paper replay — 2026-10-06

One rule, logged before the run (`polymarket-updown`, n=1, fingerprint
`a0a4df1c15e64390`). The 1-month, 3-month, and 6-month windows are nested
slices of that same path. They are one trial.

Shipped rule: BTC, 15-minute up/down, entry band [0.60, 0.80], stop 0.40,
take-profit +0.10, 5 contracts, paper start 1000. Delta is 0 and is treated
as passed. The paper account charges no taker fee.

The gate for this trial is the **6-month paid sum**. It is **-525.1575 USDC**.
The paid account ends at **474.8425**. The trial resolves **rejected**.

| Slice | Windows | Path | Trades | Bot PnL | Paid PnL | Paid balance | Paid max drop |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 month (2026-09-06 21:15 → 2026-10-06 21:00 UTC) | 2880 | 2879 | 2839 | +64.8075 | +44.8075 | 1044.8075 | 83.45 |
| 3 months (2026-07-08 21:15 → 2026-10-06 21:00 UTC) | 8640 | 8630 | 8429 | -168.4525 | -228.4525 | 771.5475 | 298.985 |
| 6 months (2026-04-09 21:15 → 2026-10-06 21:00 UTC) | 17280 | 17254 | 16877 | -395.1575 | -525.1575 | 474.8425 | 594.04 |

The 1-month paid sum is +44.8075. It is the last 30 days of the same replay.
The account did not halt. One entry costs at most 4.00.

Research claims stay in `rhengine/alpha/book.py`. Every number above is this
paper replay.

## How a trade was scored

Public data only. Gamma `closed=true` for the slug `btc-updown-15m-{start}`.
CLOB `prices-history` on the Up token, `startTs`/`endTs`, fidelity 1.
Median 15 prints per window. 26 windows had no path (25 empty history, 1
missing market). Fetch errors: 0.

DOWN is priced as `1 - UP`. Six windows were checked against the DOWN token.
The nearest DOWN print within 30 seconds differed from `1 - UP` by as much
as 0.09. A gap that size can move a price across the entry band.

Prints with less than 30 seconds left are ignored, matching the live runner.
Stop and take-profit fill at the sampled price in both columns. A hold uses
the video rule on the bot column (last eligible mark ≥ 0.50 pays 1) and the
Gamma Up outcome on the paid column.

On the 6-month path the exits were 11,484 take-profits, 5,260 stops, and
133 holds. 38 holds disagreed with the Gamma outcome. Each disagreement
moves the paid column by 5.00 USDC relative to the bot column. The net of
those flips is 26 against the video rule, which is the entire 130.00 gap
between -395.1575 and -525.1575.

Sides: 8,402 UP and 8,475 DOWN.

Paid trades: 11,566 wins, 5,311 losses (68.5%). Average win +0.7534 USDC.
Average loss -1.7395 USDC. The loss is about 2.3 times the win, so the win
count has to clear about 70% for the sum to be positive. It cleared 68.5%.

A naive t on the paid trade list, treating each 15-minute trade as
independent, is -3.3590 at 6 months, -2.0951 at 3 months, and +0.7329 at
1 month. The bot column is -2.5401, -1.5523, and +1.0656. The trades share
one market and are dependent, so this t is a description of the sample.
The rejection is the paid sum, not that t.

## Months on the 6-month path

April starts 2026-04-09 and October ends 2026-10-06 21:00 UTC. Those two
months are partial.

| Month | Windows | Missing | Trades | Bot PnL | Paid PnL |
|---|---:|---:|---:|---:|---:|
| 2026-04 | 2027 | 4 | 1974 | -55.45 | -80.45 |
| 2026-05 | 2976 | 3 | 2919 | -101.18 | -126.18 |
| 2026-06 | 2880 | 7 | 2812 | -87.175 | -102.175 |
| 2026-07 | 2976 | 8 | 2920 | -122.935 | -137.935 |
| 2026-08 | 2976 | 0 | 2880 | -57.975 | -72.975 |
| 2026-09 | 2880 | 4 | 2818 | +10.45 | -19.55 |
| 2026-10 | 565 | 0 | 554 | +19.1075 | +14.1075 |

## What the replay leaves out

Minute prints miss moves between samples. A window can jump from 0.54 to
0.84 and never enter the band. 377 windows on the 6-month path had a price
path and no trade.

The live BTC 15-minute market charges a taker fee (`crypto_fees_v2`). This
account charges none. A fee would remove cash from a sum that is already
negative.

Gamma `outcomePrices` is the paid column for a hold. The market's own
resolution source is the Chainlink BTC/USD 60-second TWAP. This replay does
not rebuild that TWAP.

A 2-day fetch checked the client before the 180-day run. Those windows sit
inside this path and were not scored as their own trial.

## Reproduce

From the repo root:

```bash
python3 -m polymarket_paper.backtest
```

The first run downloads about 17,280 windows (about 10 minutes at 20
workers). Cache lands in `polymarket_paper/cache/` and is gitignored.
`python3 -m polymarket_paper.backtest --offline` replays a warm cache.
