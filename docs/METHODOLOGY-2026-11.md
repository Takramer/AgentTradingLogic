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
| **1927–2026 (99.6 yr)** | | | | | | | |
| market | 10.20% | 17.5% | 0.46 | −84.1% | | | 1.00 |
| 2x, always held | 12.52% | 35.0% | 0.42 | −98.4% | −1.16% | −5.00 | 2.02 |
| LRS 1x | 10.01% | 11.6% | 0.61 | −41.1% | 3.31% | 3.38 | 0.47 |
| **LRS 2x** | **15.19%** | 23.2% | 0.59 | −68.3% | 6.36% | 3.30 | 0.93 |
| **2016-07 → 2026-09, after publication (10.2 yr)** | | | | | | | |
| market | 15.31% | 18.4% | 0.74 | −34.2% | | | 1.00 |
| 2x, always held | 23.70% | 36.7% | 0.70 | −59.7% | −2.54% | −4.00 | 2.04 |
| LRS 1x | 11.72% | 12.2% | 0.77 | −23.0% | 2.24% | 0.87 | 0.55 |
| **LRS 2x** | **19.30%** | 24.5% | 0.75 | **−42.1%** | 3.71% | 0.74 | 1.09 |

By era (CAGR, LRS-2x vs market): 1928–49 13.8% vs 4.6% · 1950–69 20.9% vs
13.1% · 1970–89 13.8% vs 11.3% · 1990–2009 10.9% vs 8.4% · 2010–26 15.0% vs
14.2%. **5 of 5.** The 1930s are not the whole story — CXO Advisory's critique
of the paper is that its 1928 start flatters any trend rule; every later era
still beats.

### 2b. Is the model real?

The 2x simulation, run on SPY, against the actual SSO fund, 2007–2026:
**15.15% vs 15.02% a year, daily correlation 0.996.** The financing and fee
assumptions are within 0.13 pp/yr of what the real fund delivered, including
2008.

**Checked twice, corrected once (2026-11-12).** The first version of these
tables counted years as rows ÷ 252 and charged annual costs per row. Ken
French's daily file has ~290 rows a year before 1952, when the NYSE traded on
Saturdays, so every full-sample and pre-1952 CAGR read too low (market 9.78%,
LRS-2x 14.50%). `lrs_check.py` now uses calendar years and accrues costs by
days elapsed; no verdict changed. `scripts/lrs_crosscheck.py` recomputes
every figure in §2 with quantstats (drawdown, compounding, Sharpe) and
statsmodels (Newey-West alpha) and fails on any disagreement; all agree.

### 2c. Pre-registration and the multiple-testing bill

Logged before the first run: family `index-leverage`, n=2 (1x and 2x), kill
criterion = beat the market's CAGR after publication AND full-sample max
drawdown no worse than the market's AND beat the market in ≥4/5 eras.

| | lag 1 (pre-registered) | lag 2 (next close) |
|---|---|---|
| full CAGR vs market | 17.08% vs 10.20% | 15.19% vs 10.20% |
| after-publication CAGR | 18.72% vs 15.31% | 19.30% vs 15.31% |
| full max DD | −60.0% | −68.3% |
| kill criterion | PASS 3/3 | PASS 3/3 |

Alpha t = +3.30 (lag 2): family-wise p = 0.003 against its family's 3 trials,
**p = 0.082 against all 88 trials in the repo — DOES NOT CLEAR the repo-wide
bar of 3.44.** After publication, alpha t = 0.74. Read the alpha as "it is
mostly beta, timed", which is the honest description of the rule.

### 2d. Deciding at 15:15 on the live price

The Robinhood MCP gives the 15:15 run a real-time SPY quote, so the rule can
act today instead of tomorrow. `scripts/lrs_1515_check.py`, pre-registered
(family `index-leverage`, now n=3) with the kill criterion "CAGR and max DD no
worse than next-close". SPY one-minute bars, 2016-07 → 2026-09, same costs:

| | CAGR | vol | Sharpe | max DD |
|---|---|---|---|---|
| market (SPY) | 15.26% | 17.8% | 0.75 | −33.7% |
| 2x, always held | 23.88% | 35.5% | 0.71 | −58.9% |
| next close (lag 2) | 17.96% | 23.9% | 0.71 | −43.1% |
| **15:15 live price** | **19.09%** | 24.0% | 0.75 | **−37.4%** |
| same-day close (lag 1, not executable) | 19.48% | 23.8% | 0.77 | −37.7% |

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

### 2e. Cutting the drawdown: LRS-VT

LRS-2x's worst loss, −68.3%, came in 1933–35: whipsaw at 2x after the 1932
bottom. Its 1987 loss was −54.1%. Three variants were pre-registered (family
`index-leverage`, now 6 trials; repo 91) and run by `scripts/lrs_variants_check.py`,
lag 2, same costs. Exposure is held as SPY + T-bills up to 1x, SPY + SSO above.

| 1927–2026 | CAGR | maxDD | Calmar | Sharpe | alpha t | avg exposure | trails 10-yr |
|---|---|---|---|---|---|---|---|
| market | 10.20% | −84.1% | 0.12 | 0.46 | — | 1.00 | — |
| LRS-2x | 15.19% | −68.3% | 0.22 | 0.59 | 3.30 | 1.45 | 17% |
| **LRS-VT** | **14.57%** | **−57.9%** | **0.25** | **0.63** | **3.68** | 1.37 | 19% |
| LRS-1.5x | 12.76% | −56.2% | 0.23 | 0.60 | 3.31 | 1.09 | 31% |
| VT alone | 13.30% | −92.1% | 0.14 | 0.49 | 1.52 | 1.79 | 27% |

| 2016-07 – 2026-09 | CAGR | maxDD |
|---|---|---|
| market | 15.31% | −34.2% |
| LRS-2x | 19.30% | −42.1% |
| LRS-VT | 16.87% | −35.0% |
| LRS-1.5x | 15.67% | −33.1% |

**LRS-VT**: in the trend, exposure = min(2, 0.30 ÷ 21-day realized vol);
rebalance only when the target moves more than 0.25x. It passed all five
pre-registered checks. Alpha t 3.68 clears the repo-wide bar (3.45; p 0.021
across 91 trials, 0.0014 within the family) — the first result here that does.
It costs 0.6 points a year against LRS-2x over a century and 2.4 points since
2016, for 10 points less drawdown, and trades about 8 times a year instead of 5.

VT alone (no trend) lost 92% in 1929–32: the trend filter dodges slow crashes,
the vol cap trims fast ones. LRS-1.5x passes but trails the market in 31% of
decades. None of these fixes the lag: any 200-day rule trails in V-shaped years.

**At 15:15 on the live price** (`scripts/lrs_vt_extra_check.py`, pre-registered,
SPY one-minute bars 2016-07 – 2026-09): LRS-VT made 17.41% a year with a worst
loss of −31.6%, against SPY's 15.26% / −33.7% and LRS-VT at the next close
16.29% / −36.6%. Accepted. It beat SPY on both return and drawdown here, but ten
years alone prove little (alpha t 0.47); the century-long test carries the weight.

**Treasuries instead of T-bills when the trend is off**: rejected. The bond is
rebuilt from 10-year yields since 1962 (daily correlation 0.947 with IEF; the
model earns 0.7 points a year less than IEF). It helped from 1963 to 2026
(13.79% vs 12.84%), all of it while rates fell. In 2022 stocks and bonds fell
together and its drawdown reached −43.4%, against −35.0% with T-bills.

After these two trials the family holds 8 and the repo 93; LRS-VT's alpha t 3.68
still clears the bar (p 0.021 across 93). Not wired into RobinhoodAgent.

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
6. **LRS 1x does not beat the market** (10.01% vs 10.20%). The filter alone buys a
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
| Weekly premium selling (Cboe WPUT, BXMW) | **failed** | `scripts/shortdated_check.py`, pre-registered 2026-11-12: WPUT 2006–2026 4.75%/yr, Sharpe 0.31, −2.02%/yr (t −1.57) against the same beta held passively; BXMW −1.19%/yr (t −0.87). Weekly is worse than monthly: WPUT 4.60% vs PUT 7.52% on the same days |
| 0DTE options | not tested here; published record is poor | Vilkov, *0DTE Trading Rules* (SSRN 4641356, rev. 2026-03, 2016-09..2026-02 real quotes): the same-day premium is "small and difficult to monetize after realistic frictions", P&L is driven by tails; only some 10:00 ET conditional rules made money out of sample. Retail 0DTE trades lose ~4.6% each (Anatomy of Retail Option Trading, 2025). The agent account (level 2, cash) can only buy options, the losing side. Testing needs paid one-minute OPRA quotes; the free accumulator stopped on 2026-08-01 with 10 quotes |
| Overnight-only SPY (close → next open) | **failed** | `intraday/scripts/overnight.py`, T18: dev 2016–2024 $6,931 on $10k, t +2.06 against a bar of 3.18, Sharpe 0.66 vs 0.85 for holding all day |
| LEAPS stock replacement | same bet as LRS, worse vehicle at this size | one deep-ITM SPY or XSP LEAP controls ~$76,000 of index and costs over $10,000, plus option spreads; SSO is fractional from $1 |
| Day trading (`intraday/`) | **failed** | T10, the last candidate, lost $617 on its one holdout look (2025-01 → 2026-09, t −1.51, drawdown −$727 vs a −$309 development worst). Holdout spent (`intraday/docs/RESULTS.md`) |
| Prediction markets, BTC hourly, weather | closed; code removed today | not tradable through the Robinhood MCP |
| Congress trade copying | no | abnormal returns fell after the 2012 STOCK Act; buying after disclosure earned negative alpha in a House-purchase study (Gothenburg) |
| Insider purchases (Form 4) | **failed** | `scripts/insider_check.py`, pre-registered: officers' and directors' buys ≥ $25k, held a month, 2006–2026. 6.78%/yr vs SPY 11.06%; −1.42%/yr against a survivor control after 30 bps costs (t −1.12). Before costs +2.43%/yr (t 1.71); monthly turnover eats 3.56%/yr. Only 54% of events could be priced (Yahoo lacks delisted stocks), which flatters it |
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

RobinhoodAgent runs **LRS-VT** (`books.lrs_vt`), not LRS-2x. Wired on
RobinhoodAgent branch `feature/lrs2x`, **unarmed**. `books.lrs2x` stays as the
research reference. The engine side runs on its own:

```bash
.venv/bin/python -m rhengine.alpha.lrs --vt                     # dry-run target
.venv/bin/python -m rhengine.alpha.lrs --vt --live-price 761.08 # 15:15 rule
.venv/bin/python scripts/lrs_variants_check.py                  # the evidence
```

On 2026-11-12: SPY above its 200-day average, 21-day vol 22.1% → target
exposure 1.36x = **64% SPY, 36% SSO, 0% SGOV**.

How the closing run uses it:

1. Stage 2 adds SPY to its quotes and writes `spy_last` into `state.json`.
2. `order_plan._build_lrs_actions` imports `lrs.book("lrs_vt")`,
   `closes_before`, `vt_exposure`, `held_exposure`, `split` and `needs_trade`
   (stdlib only; the runtime Python has no pandas). Target exposure is 0 when
   `spy_last` is not above the 200-day average, else 0.30 / the 21-day
   realized vol of closes through yesterday, capped at 2x.
3. The book owns the whole account: cash plus SPY, SSO and SGOV. It trades
   only on entry, exit, or when held exposure is more than 0.25 from the
   target (~8 trades a year). Then it sells down what is over weight (in full
   when a fund's target is under 2% of the book, else a partial quantity),
   and buys what is under weight with cash plus the sale proceeds, sells
   first. Inside the band it only parks idle cash in SGOV. Legs under 2% of
   the book are skipped.
4. It skips LRS, and only LRS, before 15:00 ET, when `spy_last` is missing or
   more than 30 minutes old, when Yahoo fails, when the last close is stale, or
   when the engine's vehicles differ from SPY/SSO/SGOV. A skipped day is not
   retried; the next closing run decides again. Under the kill switch it still
   sells SPY and SSO toward the target but buys nothing and sells no SGOV.
5. `validate_plan._validate_lrs` fails the plan if it trades anything but
   SPY/SSO/SGOV, if the plan's weights do not sum to 1 or exceed 2x exposure,
   if it buys a fund whose target weight is 0, trades one fund twice, lists a
   buy before a sell, buys more than the account holds, or buys under the
   kill switch. It reads the book's status from the engine itself.
6. Swing exits and trailing stops ignore SPY/SSO/SGOV while LRS is on.

Two keys arm it, and both must turn:

| Key | Where | Shipped |
|---|---|---|
| `lrs.enabled` | RobinhoodAgent `config.json` | `false` |
| `books.lrs_vt.status == "autonomous"` | engine `rhengine/strategies.json` | `"manual"` |

With only the first, the plan carries `dry_run_orders` that Stage 2 never
places. None of the three funds is banned; `banned_instruments` holds only
inverse funds and VXX.

The engine bump (pin `aa4e336` → this repo) needed three helpers that
`signals_pipeline.py` imported and this repo deleted. They now live in
`signals_pipeline.py`. On the new engine the swing scanner selects no strategy
on a cold start, because all six sleeves are retired.

Owner steps, in order:

1. ~~Re-authenticate Stage 2~~ — done 2026-11-12; the login holds until
   2026-12-11.
2. **Fund the agentic account** (••2927 shows $0 since 2026-11-02). At $50,
   a 36% SSO leg is $18; the 2% floor skips legs under $1.
3. **Choose the book.** Set `entries_enabled: false` and `lrs.enabled: true`;
   the validator refuses both on at once.
4. **Arm the engine**: `"status": "autonomous"` under `books.lrs_vt`.
5. **Push AgentTradingLogic first, then RobinhoodAgent**, and merge
   `feature/lrs2x` into `main`. The runtime clone pulls `main` the next
   morning; the first trade is that day's 15:15 run.

## 7. From a $5,000 start — backtest, out of sample, forward (2026-11-12)

Pre-registered in the ledger (family index-leverage) before it ran. Rules
frozen; nothing re-tuned. `scripts/lrs_5k_check.py`, `scripts/lrs_paper.py`.

| $5,000 in | SPY | LRS-2x | LRS-VT |
|---|---|---|---|
| 1950 → 2026-09, next close | $22.6M, 11.59%/yr, −54.6% | $241M, 15.08%, −54.1% | $202M, 14.82%, **−49.2%** |
| 2000 → 2026-09, next close | $44,067, 8.48%, −54.6% | $62,760, 9.92%, −47.6% | $63,452, 9.97%, **−37.4%** |
| 2016-07 → 2026-09, 15:15 (the live rule) | $21,357, 15.22%, −33.7% | $29,966, 19.09%, −37.4% | $25,911, 17.41%, −31.6% |

Read it like this:

- **Out of sample, LRS still beat the index.** LRS-2x was published in 2016 on
  older data; since then it made 18.52%/yr against 14.92% (alpha t +0.76, too
  short a window to be significant).
- **The parameters are not fitted.** Re-picking vol target and band every five
  years on the prior twenty gives Sharpe 0.60 against the fixed rule's 0.61, and
  all 15 grid cells beat the market's Sharpe (0.58–0.62 against 0.54).
- **But the edge is small next to its noise.** LRS-VT's alpha t is 1.91 on
  1950–2026, under the family bar of 3.04; the 3.68 from 1927 rests on the
  1929–32 crash. It beats SPY by ~4%/yr with 14% tracking error, so a live
  record needs ~48 years to prove itself at t = 2. Over the live rule's ten
  years its Sharpe (0.74) equals SPY's (0.75).
- **Worst case is a crash while levered.** LRS-VT's third-worst drawdown is
  −49% (Aug 1987 → Aug 1988): it held ~2x into October 19, 1987.
- **Bootstrap, 10 years from $5,000** (5,000 paths, 63-day blocks, 1953–2026):
  median $18,765 vs SPY $14,853; ahead of SPY in 72% of paths; behind $5,000 in
  3%; a drawdown past −40% in 34% of paths vs 21% for SPY. One year out, a
  loss is about as likely as for SPY (26% vs 23%).
- **Rolling windows since 1950:** ahead of SPY in 57% of 1-year, 74% of 5-year
  and 79% of 10-year windows.
- **Costs:** 30 bps a switch takes 1950–2026 CAGR from 14.82% to 13.40%; 50 bps
  and +1% financing to 11.22%, still below SPY's 11.59%.

**Forward paper record** (`research_data/paper/lrs_vt_5k.jsonl`): $5,000 from
2026-10-01, the first session after the design data, at real 15:15 SPY/SSO/SGOV
prices, through the same engine functions RobinhoodAgent calls. Re-run
`.venv/bin/python scripts/lrs_paper.py record` any day; the whole path is
recomputed from bars and stored rows must match it. First 31 sessions: −4.07%
(max drawdown −12.2%) against SPY −0.96%: held 2x through a choppy October.
Six weeks says nothing; judge it after 250 sessions.
