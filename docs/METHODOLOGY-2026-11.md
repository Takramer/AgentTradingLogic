# Methodology for RobinhoodAgent — what can beat the S&P 500, 2026-11-12

**Question asked:** research quantitative and qualitative methods, clean up the
repo, and produce a method that beats the S&P 500 and can run in the Robinhood
agentic account (stocks, options, swing, intraday, earnings, LEAPS — anything).

**Answer, up front:** one rule clears every test this repo applies to a claim:
**LRS-2x** — hold a 2x S&P 500 fund (SSO) while SPY closes above its 200-day
average, hold T-bills (SGOV) when it closes below. Published in 2016, replicated
here on 100 years of data, and it kept beating the market in the ten years
*after* publication. Built as `rhengine/alpha/lrs.py`, status `manual`.

It beats the index **in raw return, by taking more market risk when the trend
is up** — not by picking stocks. Its after-publication Sharpe (0.75) equals the
market's (0.74). If "beat the S&P" means risk-adjusted skill, nothing here does
it reliably; the closest is RMX-B (+1.8%/yr, t=+2.61 over 63 years), which
needs ~96 names and trails the index 4 years in 10.

---

## 1. LRS-2x — the rule

```
each session at 15:15 ET:
    price  = SPY last trade, from the Robinhood MCP (get_equity_quotes)
    signal = price > mean of (price + the prior 199 adjusted closes)
    target = SSO (2x daily S&P 500)  if signal  else  SGOV (0-3 month T-bills)
    trade now, market orders, sell the held fund before buying the other
one fund held at a time; ~5 switches a year
fallback if the 15:15 run fails: decide on the close, trade at the NEXT close
```

Source: Gayed & Bilello, *Leverage for the Long Run* (2016, Charles H. Dow
Award). Parameters are the paper's; nothing was tuned here.

## 2. The evidence

`scripts/lrs_check.py --lag 2`. Ken French daily market (CRSP value-weighted,
a close proxy for the S&P 500 total return), 1926-07 to 2026-09. Costs: the
borrowed half pays T-bill + 0.5%/yr, the 2x fund pays 0.90%/yr, each switch
pays 10 bps. Trade at the close **after** the signal (lag 2, this repo's
convention; the pre-registered lag 1 reads better and is in §2c).

### 2a. Full sample and after publication

| | CAGR | vol | Sharpe | max DD | alpha/yr | alpha t | beta |
|---|---|---|---|---|---|---|---|
| **1927–2026 (103.7 yr)** | | | | | | | |
| market | 9.78% | 17.1% | 0.45 | −84.1% | | | 1.00 |
| 2x, always held | 11.93% | 34.3% | 0.41 | −98.4% | −1.22% | −5.27 | 2.02 |
| LRS 1x | 9.59% | 11.4% | 0.60 | −41.1% | 3.31% | 3.37 | 0.47 |
| **LRS 2x** | **14.50%** | 22.8% | 0.58 | −68.4% | 6.32% | 3.28 | 0.93 |
| **2016-07 → 2026-09, after publication (10.2 yr)** | | | | | | | |
| market | 15.35% | 18.4% | 0.74 | −34.2% | | | 1.00 |
| 2x, always held | 23.77% | 36.8% | 0.70 | −59.7% | −2.53% | −3.99 | 2.04 |
| LRS 1x | 11.75% | 12.2% | 0.77 | −23.0% | 2.24% | 0.87 | 0.55 |
| **LRS 2x** | **19.35%** | 24.5% | 0.75 | **−42.1%** | 3.71% | 0.74 | 1.09 |

By era (CAGR, LRS-2x vs market): 1928–49 11.5% vs 3.9% · 1950–69 20.7% vs
13.0% · 1970–89 13.7% vs 11.3% · 1990–2009 10.9% vs 8.4% · 2010–26 15.1% vs
14.2%. **5 of 5.** The 1930s are not the whole story — CXO Advisory's critique
of the paper is that its 1928 start flatters any trend rule; every later era
still beats.

### 2b. Is the model real?

The 2x simulation, run on SPY, against the actual SSO fund, 2007–2026:
**15.15% vs 15.02% a year, daily correlation 0.996.** The financing and fee
assumptions are within 0.13 pp/yr of what the real fund delivered, including
2008.

### 2c. Pre-registration and the multiple-testing bill

Logged before the first run: family `index-leverage`, n=2 (1x and 2x), kill
criterion = beat the market's CAGR after publication AND full-sample max
drawdown no worse than the market's AND beat the market in ≥4/5 eras.

| | lag 1 (pre-registered) | lag 2 (next close) |
|---|---|---|
| full CAGR vs market | 16.30% vs 9.77% | 14.50% vs 9.78% |
| after-publication CAGR | 18.78% vs 15.35% | 19.35% vs 15.35% |
| full max DD | −60.1% | −68.4% |
| kill criterion | PASS 3/3 | PASS 3/3 |

Alpha t = +3.28 (lag 2): family-wise p = 0.003 against its family's 3 trials,
**p = 0.087 against all 88 trials in the repo — DOES NOT CLEAR the repo-wide
bar of 3.44.** After publication, alpha t = 0.74. Read the alpha as "it is
mostly beta, timed", which is the honest description of the rule.

### 2d. Deciding at 15:15 on the live price

The Robinhood MCP gives the 15:15 run a real-time SPY quote, so the rule can
act today instead of tomorrow. `scripts/lrs_1515_check.py`, pre-registered
(family `index-leverage`, now n=3) with the kill criterion "CAGR and max DD no
worse than next-close". SPY one-minute bars, 2016-07 → 2026-09, same costs:

| | CAGR | vol | Sharpe | max DD |
|---|---|---|---|---|
| market (SPY) | 15.30% | 17.8% | 0.75 | −33.7% |
| 2x, always held | 23.95% | 35.6% | 0.72 | −58.9% |
| next close (lag 2) | 18.01% | 23.9% | 0.71 | −43.1% |
| **15:15 live price** | **19.14%** | 24.1% | 0.75 | **−37.4%** |
| same-day close (lag 1, not executable) | 19.53% | 23.9% | 0.77 | −37.7% |

**PASS 2/2, adopted.** The 15:15 signal disagrees with the closing signal on
0.8% of days, so it recovers most of the gap between the lag-1 paper result
and the lag-2 one the repo can actually trade. SSO and SGOV both quoted a
1-cent spread on 2026-11-12 (1–1.5 bps); the test charges 10 bps per switch.

The first run of this test was invalid: `fetch_yahoo(adjust=False)` returned
cached adjusted bars because both shared one cache file. Fixed in
`rhengine/data_local.py` (`tests/test_data_local_cache.py`); every number
above comes from the rerun, where the one-minute 16:00 bar matches Yahoo's
close to a median 0.008%.

A related rule in the separate intraday ledger (T17: a daily 200-day filter on
a 15-minute SSO swing rule) made +78% against SSO buy-and-hold's +173% over
2016-06 → 2022-06. Same lesson as §3.3: since 2016, plain 2x has beaten the
filtered version on return; the filter buys a smaller drawdown.

## 3. What breaks it — say these before arming

1. **It is leverage, and the filter cannot dodge a one-day crash.** Under
   next-close execution the rule was invested on 19 Oct 1987: **−34.9% in one
   session.** The pre-registered lag-1 version stepped out one day earlier by
   luck. Other invested days in the record: −18%, −17%, −15%, −13%, −13%.
2. **After publication its drawdown was worse than the market's**: −42% vs
   −34% at next close, −37.4% vs −33.7% at 15:15, Nov 2021 → Mar 2023. SPY chopped around its 200-day line and the rule
   switched 18 times in 15 months; every re-entry at 2x lost.
3. **It trails the market in 17% of 10-year windows and 25% of 5-year
   windows.** 2010–2026 it was only 0.8 pp/yr ahead; plain 2x would have won.
4. **Taxes.** ~5 switches a year, all short-term gains. In a taxable account
   that costs real return; an IRA does not pay it — but the agent cannot reach
   the Roth IRA (only the agentic account is exposed to the MCP).
5. **The agentic account is a cash account.** That is why the leverage comes
   from SSO and not margin. Sales settle T+1: buying with unsettled proceeds
   and selling that buy before the proceeds settle is a good-faith violation.
   A flip and an immediate flip back is the case to watch; the executor checks
   settled cash before it buys.
6. **LRS 1x does not beat the market** (9.59% vs 9.78%). The filter alone buys a
   smoother ride, not more money. The excess comes from the leverage it makes
   survivable.
7. **Do not swap in 3x (UPRO/SPXL).** Not tested, not pre-registered, and
   volatility decay grows with the square of leverage.

## 4. Everything else, judged

What this repo has measured, plus what the published record says about the
qualitative methods it had not touched.

| method | verdict | evidence |
|---|---|---|
| Six legacy swing setups (vwap, bollinger, macd, consolidation, residual momentum, pead) | **retired** | stock selection at the 53rd percentile of random (AUDIT-2026-07-24); pead's edge died June 2026 |
| Quality + 12-1 momentum, cap-weighted (RMX-B) | **kept, manual** | +1.81%/yr alpha, t=+2.61, 4/4 eras, French 1963–2026 (rebuilt today); trails 42% of calendar years, 9% of 10-yr windows. Needs ~96 names |
| Trend timing of the stock book (RMX-D) | rejected | −6.22 pp/yr out of sample |
| Covered calls / wheel / premium selling | closed | BXM alpha t=+0.62 against total return (OPTIONS-SOURCES §6) |
| LEAPS stock replacement | same bet as LRS, worse vehicle at this size | one deep-ITM SPY or XSP LEAP controls ~$76,000 of index and costs over $10,000, plus option spreads; SSO is fractional from $1 |
| Day trading (`intraday/`) | nothing cleared | T10 paper-trading since 2026-10-28 |
| Prediction markets, BTC hourly, weather | closed; code removed today | not tradable through the Robinhood MCP |
| Congress trade copying | no | abnormal returns fell after the 2012 STOCK Act; buying after disclosure earned negative alpha in a House-purchase study (Gothenburg) |
| Insider purchases (Form 4) | untested here | 82 bps/month for "opportunistic" buys (Cohen, Malloy & Pomorski 2012), concentrated in small, poorly governed firms; needs a Form 4 feed and a trial |
| LLM news sentiment | untested here | next-day predictability (Lopez-Lira & Tang, v6 2025) is an underreaction story that needs real-time news and fast fills; the MCP has no news feed |
| Shorting, futures | unavailable | the MCP has no short or futures orders (ROBINHOOD_MCP.md) |

## 5. What the Robinhood MCP adds

| tool | use here | verdict |
|---|---|---|
| `get_equity_quotes` (real time) | the 15:15 SPY price | **adopted** (§2d) |
| `get_equity_tradability` | confirm SSO and SGOV trade, fractional, on the agentic account | Stage 2 runs it once before the first order; not run here (it needs the account number from the owner) |
| `review_equity_order` | preview each order before placing it | Stage 2, every order |
| `get_sec_filing_facts` | as-filed fundamentals | could feed RMX-B's OP/BE quality leg without the PIT panel; untested |
| `get_politician_trades` | congress copying | no (§4) |
| `get_equity_analyst_ratings` | current ratings only, no history | cannot be backtested, so not evidence |
| `get_earnings_calendar` / `_results` | earnings drift | pead retired; its edge died June 2026 |
| `get_option_chains` / `_quotes` | LEAPS, covered calls | §4; the agentic account is option level 2 |
| `run_scan`, technical indicators | swing setups | the six legacy setups picked stocks at the 53rd percentile of random |

The MCP has no short or futures orders, and the agentic account is cash, so
shorting is out either way.

## 6. Putting it in RobinhoodAgent

Built here, not wired there:

```bash
.venv/bin/python -m rhengine.alpha.lrs                       # dry-run target
.venv/bin/python -m rhengine.alpha.lrs --positions pos.json  # + deltas
.venv/bin/python -m rhengine.alpha.lrs --live-price 761.08    # 15:15 rule
```

The payload has the same shape as `live.emit` and the same arming rule:
`orders_permitted` is true only when `books.lrs2x.status` is exactly
`"autonomous"` (`tests/test_lrs.py`). On 2026-11-12 it reads SPY 759.81 vs
200-day 728.65 → **hold SSO**.

Owner decisions and fixes, in order:

1. **Stage 2 cannot log in.** Every run since 2026-11-06 fails with "OAuth
   session expired". Run `claude` once on the Mac to re-authenticate.
2. **The agentic account shows $0** since 2026-11-02 (decision_log). Fund it or
   point the agent at the right account.
3. **Un-ban SSO and SGOV.** `banned_instruments` / `bear_regime_only` block
   leveraged funds (commit 849eb1f, written for the swing scanner's downtrend
   holds — the opposite of how LRS uses SSO).
4. **Bump the engine submodule.** RobinhoodAgent pins AgentTradingLogic at
   `aa4e336` (2026-07-20), 27 commits back and older than the retirement of the
   six legacy sleeves. Its selector can still route to them.
5. **Wire the 15:15 run to `emit_lrs`**: Stage 2 reads SPY with
   `get_equity_quotes`, runs `lrs --live-price <last> --positions <held>`,
   then places the deltas: sell the held fund, buy the other with a
   `dollar_amount` market order. If that run fails, Stage 1 runs it without
   `--live-price` and the trade waits for the next close. The current
   caps (10% per position, 20% cash floor, 5 positions) do not fit a one-fund
   book; it needs its own sizing path, not a loosened swing validator.
6. **Arm it**: `"status": "autonomous"` under `books.lrs2x`. Nothing else.
