# Audit of the BTC 15-minute paper rule — 2026-10-06

The shipped replay lost **525.1575 USDC** over six months (paid balance
474.8425 from 1000). The loss is the exit rule on a contract that is close
to fairly priced, and the published taker fee is larger than the gross hold
edge. All ten follow-ups failed the keep rule. `config.json` stays on the
shipped rule.

Research claims stay in `rhengine/alpha/book.py`.

## Why a 68.5% win rate lost money

The shipped path, already logged in `REPLAY.md`: 16,877 trades, 11,566 wins,
average win +0.7534 USDC, average loss -1.7395 USDC.

Take-profit is +0.10. On 5 contracts the measured average win was +0.75.
A stop from a 0.70 entry to 0.40 is about -1.50. The loss is about 2.3 times
the win, so the win count has to clear about 70%. It cleared 68.5%.

Holding those same entries to the Gamma outcome, with the stop and the
take-profit turned off, made **+508.5525** before fees. The exits are the
difference:

`exit drag = -525.1575 − 508.5525 = -1033.71`

| What the exit did | Trades | Strategy paid | If held | Strategy minus held |
|---|---:|---:|---:|---:|
| Stop, and the side lost | 3,556 | -6280.025 | -11685.725 | +5405.70 |
| Stop, and the side won | 1,704 | -2784.825 | +2928.525 | -5713.35 |
| Take-profit, and the side won | 9,507 | +7201.3625 | +15771.2225 | -8569.86 |
| Take-profit, and the side lost | 1,977 | +1393.40 | -6450.40 | +7843.80 |
| Held | 133 | -55.07 | -55.07 | 0 |

Stops saved 5,405.70 on losers and gave back 5,713.35 on winners that
recovered. Net stop effect -307.65. Take-profits collected 7,201.36 on
winners that were going to pay about 15,771, and saved 7,843.80 on winners
that were going to go to 0. Net take-profit effect -726.06. Those two nets
are the -1,033.71.

A public BTC 15-minute write-up reported the same shape: a 92% win rate and
a negative expectancy (Jimmy6929, `Polymarket-BTC-15-Minute-Trading-Bot`).
That repo's number is not a result here. It is the reason a win rate was
not treated as the score.

## The entries are close to the outcome

Win rate below is the Gamma win rate of the side that was bought. Hold edge
per share is the held pnl divided by trades and by 5 contracts.

| Entry | Trades | Mean entry | Win rate | Held pnl | Edge / share |
|---|---:|---:|---:|---:|---:|
| 0.60–0.65 | 8,384 | 0.623 | 0.631 | +309.6825 | +0.0074 |
| 0.65–0.70 | 4,698 | 0.673 | 0.671 | -46.525 | -0.0020 |
| 0.70–0.75 | 2,542 | 0.722 | 0.744 | +272.295 | +0.0214 |
| 0.75–0.80 | 1,253 | 0.772 | 0.768 | -26.90 | -0.0043 |

Across all 16,877 entries the mean price is 0.663 and the hold edge is
**+0.0060 per share**. The taker fee at 0.663 is 0.0156 per share
(`0.07 × 0.663 × 0.337`). The fee is larger than the edge.

The 0.70–0.75 band is the only bucket whose hold edge per share (0.0214)
sits above the entry fee at its own mean price (about 0.014 per share at
0.722). It was not one of the ten trials. The tested band [0.65, 0.75]
kept the shipped exits and lost. A rule that buys only 0.70–0.75 and holds
is untested, and this same six-month path cannot confirm it.

15,069 of the 16,877 entries happened with more than 10 minutes left.
Those early entries paid -533.8525 with the exits and +466.375 if held.
The first in-band print is usually early, because `timeDelay` is 15.

## The fee

Eight markets spread across the cache, plus three spot checks, all reported
`feeSchedule` exponent 1, rate 0.07, taker only. That is the crypto row of
`https://docs.polymarket.com/trading/fees` checked 2026-10-06:

`fee = shares × 0.07 × price × (1 − price)`

Rounded half-up to 5 decimals. A raw fee below 0.00001 is 0. Charged on the
entry and on a stop or take-profit exit. A hold pays the entry fee only.
The fill is still the midpoint. The spread is not in this number.

On the shipped exits the fee total is **2280.73539**. Paid pnl after the
fee is **-2805.89289**.

On the hold path the fee is entry-only, **1305.28498**. Gross hold
+508.5525 becomes net **-796.73248**. The hold was positive in both halves
before the fee (+291.0725 and +217.48) and negative in both halves after it
(-362.17298 and -434.5595).

A day-block bootstrap of the shipped paid sum (block of 96 windows, 1,000
draws, seed 20261006) runs from **-824.78 to -205.05**. The same draw after
the fee runs from **-3111.33 to -2484.21**. The blocks are single days. A
six-month regime is still one path. Inside that limit, reshuffling days
does not pull the shipped sum up through zero.

## Follow-ups

Logged before the run. Each row changes one knob. Same 17,280 windows.
Keep only if **net** pnl is positive on the full path and on each half of
8,640 windows. None cleared. `config.json` was not edited.

| Trial | Trades | Paid, no fee | Net | Net, first half | Net, second half |
|---|---:|---:|---:|---:|---:|
| Hold to Gamma | 16,877 | +508.5525 | -796.73248 | -362.17298 | -434.5595 |
| Stop 0.40, no take-profit | 16,877 | +89.5225 | -1750.30065 | -945.46734 | -804.83331 |
| Take-profit +0.10, no stop | 16,877 | -309.1625 | -2275.64859 | -1140.00011 | -1135.64848 |
| timeDelay 5 | 7,934 | +127.1525 | -720.90636 | -276.37964 | -444.52672 |
| timeDelay 2 | 3,199 | +33.0775 | -239.73808 | -130.29397 | -109.44411 |
| Entry [0.65, 0.75] | 15,128 | -471.2675 | -2377.32316 | -1106.23024 | -1271.09292 |
| Entry [0.55, 0.65] | 15,611 | -587.2425 | -2966.97842 | -1657.12307 | -1309.85535 |
| Stop 0.55 | 16,877 | -389.155 | -2808.21911 | -1508.27739 | -1299.94172 |
| Take-profit +0.20 | 16,877 | -508.4225 | -2593.3515 | -1331.47243 | -1261.87907 |
| Shipped exits plus the fee | 16,877 | -525.1575 | -2805.89289 | -1433.81435 | -1372.07854 |

`timeDelay 2` is the only row whose no-fee pnl is positive on both halves
(+8.235 and +24.8425). After the fee it is negative on both. `timeDelay 5`
is positive overall before the fee (+127.1525) and negative in the second
half before the fee (-16.595).

Methods looked at and not ported: apex-dao's order-book replay (this cache
is minute mids) and LuciferForge's Binance late-snipe (a different rule;
Binance returned HTTP 451 from this environment).

## Reproduce

```bash
python3 -m polymarket_paper.audit
```

The cache from `python3 -m polymarket_paper.backtest` has to be present.
The command checks that the cache is still the recorded 17,280 windows.
