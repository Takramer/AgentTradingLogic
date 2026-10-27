# Intraday ideas — open list

**Started 2026-10-27.** Ideas for day-trading MES, what outside research says
about each, and a draft rule exact enough to log as a trial. Sections 2 and 3
have since been run: see [RESULTS.md](RESULTS.md). The rest are drafts to pick from, not settled rules, and
nothing from the swing-trading work in `rhengine/` is applied to them. Workflow:
[GUIDE.md](GUIDE.md).

| idea | what outside research says | draft rule |
|---|---|---|
| VIX / VVIX as a direction signal | no study found that predicts next-day or intraday S&P direction from them | none yet |
| VIX as a size / stop-width input | VIX forecasts how much the index moves, not which way | §1 |
| Intraday momentum (last 30 min follows the day) | two *Journal of Financial Economics* papers, 1974–2020, 60+ futures | §2 |
| Intraday momentum on high-VIX days | Gao et al. find it stronger on volatile days | §2 |
| Noise-area momentum (Zarattini et al.) | working paper by authors who sell the strategy; one informal re-test came out weaker | §3, after reading its costs |
| IFVG (inversion fair value gap) | no published research, no published test against random entries | §4 |
| Turtle Breakout (Revelio) | the same rules lost to random entries on ten futures in one costed outside test | §5 |

Add ideas from YouTube, X and NotebookLM here the same way: what the source
claims, what evidence exists, and one exact rule.

---

## 1. VIX and VVIX

**What they measure.** VIX is the S&P 500's option-implied volatility over the
next 30 days. VVIX is the same measure computed on VIX options: how much VIX
itself is expected to move.

**Direction.** The published VVIX results are about **option** returns, not the
index. Park (2015) finds a rise in VVIX lowers the returns of tail hedges (S&P
puts, VIX calls) over the next three to four weeks. Huang and Shaliastovich
find high VVIX predicts more negative returns on delta-hedged S&P and VIX
options. Neither tests next-day or intraday S&P direction. The "VVIX above
100" and "VVIX diverging from VIX" rules found online carry no sample and no
test. A direction rule built on either would be untested ground.

**Size and stop width.** VIX does forecast the *size* of moves. The rough daily
figure is VIX ÷ 16 (√252 ≈ 16): VIX 16 means about a 1% one-standard-deviation
day. Two cautions:

- VIX usually sits **above** the volatility that follows (the variance risk
  premium), so VIX ÷ 16 tends to overstate the day's move. Calibrate the ratio
  on MES's own realised range before using it to place stops.
- VIX's daily close prints at **16:15 ET**, after the MES RTH close. Using
  today's VIX close to trade today is look-ahead. Use the **prior** day's close.

A rule that sizes by VIX but never skips a day changes risk, not the trade
list. A rule that skips days by VIX is a new trial.

**Data.** Cboe's free daily files: `VIX_History.csv` (close from 1990, OHLC
from 1992) and `VVIX_History.csv` (close only, from 2007), at
`https://cdn.cboe.com/api/global/us_indices/daily_prices/`. Cboe moves these
files; check the link before building on it.

---

## 2. Intraday momentum

The intraday S&P pattern with the most peer-reviewed support found so far.

- **Gao, Han, Li and Zhou (2018, JFE).** On SPY, 1993–2013, the return from the
  prior close to 10:00 ET predicts the 15:30–16:00 return. Stronger on volatile,
  high-volume, recession and macro-news days. Present in ten other ETFs.
- **Baltussen, Da, Lammers and Martens (2021, JFE).** On 60+ futures,
  1974–2020, the return from the prior close to 15:30 predicts the last 30
  minutes, and reverses over the following days. Their explanation: option
  dealers and leveraged ETFs hedging short-gamma books trade *with* the move late
  in the day.

**Unknowns.** No source found tests 2014–2026 on its own, and one study finds no
effect during the 2020 crash. The edge per trade is small next to MES costs
($4.28 round trip, assumed IBKR). The harness answers both.

**Draft rule A** (fits the engine: signals fill at the next bar's open; flat at
session end):

```
r = close of the 15:29 bar − prior session's 15:59 close
at the close of the 15:29 bar: target = sign(r)   (0 if r == 0)
fills at the 15:30 open, held to the session's last bar
no stop, no target — one trade per session
```

No parameters, so no grid. The prior close comes from RTH bars only; the first
session in the data is skipped.

**Draft rule B.** Rule A, trading only when the prior day's VIX close is above
its trailing 252-day median. Fix the threshold before running; Gao et al.
predict this subset is stronger, so if it is not, that counts against both.

---

## 3. Noise-area momentum (Zarattini, Aziz, Barbon)

A working paper (SFI 24-97, 2024, revised 2025) that trades SPY when price
leaves a "noise area" built from the last 14 days' average move from the open,
checks every 30 minutes, trails a stop on the noise boundary and VWAP, and
sizes by volatility. It reports 19.6%/yr and Sharpe 1.33 net of costs,
2007–2024.

Before drafting a rule:

- Read the paper's cost section. The same authors' opening-range paper is
  reported to assume zero spread at the open; check whether this one does too.
- The authors sell the strategy; it is not peer-reviewed.
- One TradingView re-test (YuL) came out much weaker, with margin calls.
- It has four or more settings. Test the paper's published settings once
  rather than sweeping them, or the grid is charged to the ledger.

---

## 4. IFVG

**What it is.** A fair value gap is a three-candle gap: candle 3's low sits
above candle 1's high (bullish) or candle 3's high sits below candle 1's low
(bearish). An *inversion* is a gap that price later closes through the wrong
way; traders then treat the old gap as support or resistance in the new
direction.

**Evidence.** A search on 2026-10-27 for IFVG results on ES/NQ found playbooks,
indicators and one study site that keeps "the best variant by profit factor" on
the same data it reports — which picks the winner on the scoreboard. No one has
published a test against random entries, which is the check this harness runs.

**Draft rule.** No source pins the settings down, so every value below is a
choice. Fix them before running and do not tune after:

```
bars:      5-minute, built causally from 1-minute (a 5-min bar is known only
           at the close of its 5th minute)
gap:       3-candle FVG, at least 4 ticks (1.00 pt) tall, formed after 09:45
           in the same session; older gaps ignored
inversion: the first 5-minute close through the far side of a bearish gap
           -> long; through a bullish gap -> short. One trade per session
entry:     next 1-minute open
stop:      the other side of the gap
target:    2x the stop distance
exit:      stop, target, or session end
```

Trying 3-minute bars, other gap sizes or other targets after a result is in
counts as new trials.

---

## 5. Turtle Breakout (Revelio)

The [TradingView script](https://www.tradingview.com/script/Hd9ebcRm-Turtle-Breakout-Revelio)
draws these rules: long only; buy above the 20-day high; a fixed stop 2 ATR
(20-day, Wilder) under entry; trail on the 10-day low; exit at whichever stop is
higher. Days run 17:00–17:00 ET. An optional "day-trading mode" exits at 16:30
and re-enters at 18:00. The page makes no performance claim and sizes nothing.

**Outside test.** Algovibes (YouTube, 2026) coded these rules on futures,
2020–Oct 2026, with commission, a tick of slippage and roll costs. The book's
return matched buy-and-hold on the same markets at the same leverage
(33.5% vs 32.8%/yr); against 2,000 random entries with the same holding times
it beat luck on none of ten futures markets; day-trading mode did worse on all
ten.

**Fit.** The channel is built from *daily* bars, and the script's page says the
chart timeframe changes only the view. Positions last days, so it does not fit
this flat-every-session engine as written. Testing it here would need its own
multi-day engine.

---

## 6. SPY / SPX options on Robinhood

The agent account can buy single-leg SPY, SPX, SPXW and XSP options (level 2,
cash). Index options settle in cash with no forced sellout; SPY options are
closed by Robinhood 30 minutes before expiry if the account cannot cover
exercise. Published SPX/SPXW fees: about $0.50–$0.66 per contract plus $0.04
regulatory.

**Blocker: data.** An options day-trading backtest needs historical one-minute
option quotes (bid and ask, not just trades) — OPRA through ThetaData, Polygon
or Databento, all paid. Without the spread, a backtest on option mid-prices
would overstate every result. Nothing here tests options until that data is
bought.

---

## Sources

- Gao, Han, Li & Zhou, [*Market intraday momentum*](https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414), JFE 129 (2018); summary at [Alpha Architect](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/)
- Baltussen, Da, Lammers & Martens, [*Hedging demand and market intraday momentum*](https://econpapers.repec.org/RePEc:eee:jfinec:v:142:y:2021:i:1:p:377-403), JFE 142 (2021); [working paper](https://www3.nd.edu/~zda/intramom.pdf)
- Zarattini, Aziz & Barbon, [*Beat the Market*](https://ideas.repec.org/p/chf/rpseri/rp2497.html), SFI 24-97; informal re-test, [YuL on TradingView](https://kr.tradingview.com/script/gJeM3LZ5-Out-of-the-Noise-Intraday-Strategy-with-VWAP-YuL)
- Park, [*Volatility-of-volatility and tail risk hedging returns*](https://econpapers.repec.org/RePEc:eee:finmar:v:26:y:2015:i:c:p:38-63), J. Financial Markets 26 (2015); [Fed working paper](https://www.federalreserve.gov/pubs/feds/2013/201354/201354abs.html)
- CXO Advisory, [VVIX as a predictor](https://www.cxoadvisory.com/volatility-effects/expected-volatility-of-stock-market-volatility-as-a-predictor/) (Huang & Shaliastovich) and [VVIX/VIX as a return indicator](https://www.cxoadvisory.com/volatility-effects/vvix-vix-as-a-return-indicator/) (results paywalled)
- Rule of 16: [TD Ameritrade](https://tickertape.tdameritrade.com/amp/trading/volatility-rule-of-16-vix-18140), [VIX and More](https://vixandmore.blogspot.com/2010/05/vix-rule-of-16-and-realized-volatility.html)
- VIX/VVIX daily files: [Macroption, VVIX](https://macroption.com/vvix), [Macroption, VIX](https://www.macroption.com/vix-historical-data/)
- IFVG: [LuxAlgo definition](https://luxalgo.com/library/indicator/Inversion-Fair-Value-Gaps-IFVG), [TradeZella playbook](https://www.tradezella.com/strategies/ifvg-trading-model) — descriptions only, no results
- Revelio, [Turtle Breakout script](https://www.tradingview.com/script/Hd9ebcRm-Turtle-Breakout-Revelio) (read 2026-10-27)
- Broker fees: [IBKR futures commissions (SG site)](https://www.interactivebrokers.com.sg/en/index.php?f=49560), [BrokerChooser MES](https://brokerchooser.com/best-brokers/best-brokers-for-trading-micro-emini-sp500-futures-in-the-united-states), [Tradovate fees explained](https://damnpropfirms.com/trading-guides/tradovate-fees-explained-commissions-data-plans/), [Tradovate API requirements](https://danetrades.freshdesk.com/support/solutions/articles/202000104289-tradovate-api-requirements-and-subscription), [IBKR futures margin lesson](https://www.interactivebrokers.com/campus/trading-lessons/futures-margin/)
