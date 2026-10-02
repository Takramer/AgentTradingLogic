# Video intake — 2026-07-31

Twenty-one videos supplied (one earlier, nineteen in a batch, one added after). This
records what each is, what cluster it belongs to, and — the part that matters —
**what evidentiary weight it can carry here.**

## The standing rule this is measured against

`docs/CLAUDE-RESEARCH-RULES.md` and CLAUDE.md rule 4: evidence comes from the
published record (`research_data/external/`, Ken French, OSAP/JKP) or from
`rhengine/alpha/book.py`. **A video is not evidence.** It can supply a
*hypothesis*, a *method*, or a *warning*. It cannot supply a result, and no
number stated in one may be repeated here as a finding.

That is not a dismissal of the genre. Two of these videos independently
describe tools this repo already built, which is real corroboration of method.
It is a statement about which column a claim may be written in.

---

## Cluster A — methodology (6 videos). The only cluster with transferable content.

| # | channel | title |
|---|---|---|
| 1 | Roman Paolucci | How to Quant Trade in 3 Minutes |
| 2 | neurotrader | How I Develop Trading Strategies \| **Permutation Tests** and Trading Strategy Development with Python |
| 3 | Unbiased Trading | The 4 backtesting techniques behind WINNING strategies |
| 4 | MemLabs | How to create a Mean Reversion strategy (by ex-HFT quant trader) |
| 5 | MemLabs | Introduction to Quantitative Trading |
| 6 | DeltaTrend Trading | Why the Same Setup Wins and Loses: **Contextualizing Features** \| The Quant Process Ep. 4 |

**#2 (permutation tests) — already built here, and that is the point.**
`rhengine/nulltest.py` runs random-entry permutation: it borrows each real
trade's stop/target geometry, re-rolls the entry, and asks whether the real
entry beats luck. An outside practitioner arriving at the same tool
independently is meaningful corroboration that the tool is the right one.

**The gap it exposes.** `nulltest.py` permutes *entries* against **real price
paths**, so market drift and volatility clustering survive the permutation —
which is exactly why the round-3 null mean was **+0.0552R rather than zero**.
The stronger form permutes *the data*: generate synthetic series with the same
return distribution but no serial structure, re-run the whole strategy
including its own signal generation, and ask whether the entire system
(entry + exit + drift harvesting) beats what it earns on structureless data.
This repo does not have that, and it is the one genuinely new method in the
batch. Logged as an open item, **not** built — see §"What was not built".

**#6 (contextualising features)** describes conditioning a setup on regime
rather than treating it as context-free. That is round 2 of
`docs/AUTOSTRAT-2026-07-31.md`, where the answer was counterintuitive: a MACD
long pays **most** in a SPY downtrend (+0.3281R) and least in an uptrend
(+0.0051R). Conditioning was worth doing and was not enough.

**#3 (four backtesting techniques)** — IS/OOS split, walk-forward, Monte Carlo
and permutation are all already present (`sanity.check_is_oos_split`,
`optimize.py`, `montecarlo.py`, `nulltest.py`). Nothing to add.

---

## Cluster A-bis — discretionary chart methodology (1 video, added after the batch)

| # | channel | title |
|---|---|---|
| 20 | Mulham Trading | Trading is hard until you apply these 2 concepts |

### What I could and could not establish

**I could not read this one.** YouTube renders client-side, so the transcript is
not reachable, and no third party has transcribed it. I have the title, the
channel, and the channel's published corpus — **not the two concepts
themselves.** Nothing below is a summary of the video's argument, because I
cannot see it.

What is established: the channel is an **ICT (Inner Circle Trader) / Smart Money
Concepts** educator — order blocks, fair value gaps, liquidity sweeps, market
maker models (a "Full ICT MMXM Strategy Course") — trading forex intraday, and
monetising through paid courses (Edge Skool, Gumroad). Video dated 2026-06-09;
the description sells a "1-minute strategy" eBook.

### Why this class is already closed here

`docs/AGENT-SPEC-VERDICT.md` §4 audited the codeable core of ICT on 2026-07-30
and rejected it. Quoting the finding rather than re-deriving it:

> Fair Value Gaps have **no peer-reviewed academic literature**. Every source
> found is a broker blog, a charting platform, or a Medium post. The recurring
> claim is a **70–85% fill rate** across liquid markets.

And why that number is not a strategy:

- a fill rate **with no holding period** is nearly vacuous — given enough time
  price revisits most levels, and sources concede higher-timeframe gaps can take
  "weeks or months";
- **no cost, no benchmark, and no accounting for the unfilled 15–30%** — which
  is exactly where the losses live, and whose size is never stated;
- it is **blocked on two spec gaps** (`GAP-3.10` minimum gap size and staleness,
  `GAP-3.11` how the bands are constructed). Building it means inventing both,
  then testing an invention against no prior.

The verdict table lists **FVG / ATR bands: "Zero academic literature. Retail
sources only. Do not build."** That was written before this video arrived and is
not adjusted by it.

### The nearest analogues this repo has already tested

Three ICT constructs do have codeable equivalents here, and all three are
already answered:

| ICT construct | nearest thing here | outcome |
|---|---|---|
| liquidity sweep / stop-run reversal | `STreversal` (OSAP) | screened 2026-07-31: **negative in every window** (d_t −1.03 / −0.57 / −0.84) |
| order block / prior consolidation | the `consolidation` strategy | **retired** — "COMP leg toxic" |
| fair value gap | — | never built; see above |

### The structural mismatch, independent of the evidence

Even granting the method worked, it does not fit this book: it is **forex
intraday**, this engine trades **US equities on daily bars and cannot short**.
And ICT constructs are not specified tightly enough to code without inventing
several free parameters each — which is precisely the multiple-comparisons
hazard recorded in Cluster B. An agent that can invent a parameterisation and
search it will find something; that finding will be selection.

### Verdict

**Nothing to build, and nothing to test.** Not a judgement about the video,
which I could not watch — a statement that its methodology's codeable core was
audited eight days before it was sent and found to rest on no evidence, and that
the two nearest analogues in this repo are respectively negative and retired.

If you have watched it and the two concepts are *not* ICT/SMC — e.g. if they are
risk-management or position-sizing ideas rather than chart patterns — tell me
what they are and I will assess them properly. That is a real possibility I
cannot rule out from the title.

---

## Cluster B — building strategies with an AI agent (3 videos)

| # | channel | title |
|---|---|---|
| 7 | Lewis Jackson | I Re-Created A Quant Trading Strategy With Claude Code |
| 8 | Lewis Jackson | I Re-Built A Quant Trading Strategy With Fable 5 |
| 9 | AI Pathways | How To Actually Use Claude Code for Trading Strategies (Like a Quant) |

Workflow and tooling content. No strategy evidence.

**The risk worth recording, because it is the risk this project is currently
running.** An agent that can generate and evaluate a hundred strategy variants
per hour is a *multiple-comparisons machine*. Nothing in the tooling corrects
for the number of hypotheses tried, and the more productive the agent, the
worse the selection bias in whatever it reports as best. The repo's existing
defences are exactly the right ones and should be treated as load-bearing:

- both local holdouts are **spent** (CLAUDE.md rule 4), so a new claim cannot be
  laundered through them;
- `external.py --long-only ... --vs` demands a candidate help in **every**
  window and states outright that `|d_t| < 0.5` after many screens "is selection,
  not evidence";
- `docs/HOLDOUT.md` requires a holdout look to be logged with a config hash
  **before** it is executed.

That last one is the only real protection against an agent quietly re-testing
until something passes. It should not be relaxed to make agent workflows
smoother.

---

## Cluster C — 0DTE and options (10 videos). Handle with the most care.

| # | channel | title |
|---|---|---|
| 10 | Volatility Vibes | This Option Strategy Turned $10k Into $1 Million In One Year |
| 11 | projectoption | 0DTE Options Trading Explained With Examples |
| 12 | EllyDTrades | How I Mastered Day Trading Options ($SPY, $QQQ, 0DTE Guide) |
| 13 | SMB Capital | The Top 3 0DTE Options Trading Strategies |
| 14 | TheOneLanceB | Watch Before Trading 0DTE Options |
| 15 | Matt Giannino | Small Account 0DTE SPY Option Strategy |
| 16 | SMB Capital | Simple 3-Indicator Setup for 0DTE Options |
| 17 | Simpler Trading | 0DTE: Choosing Your Strike Price (This Helped Me Turn 38k into 680k!) |
| 18 | Tom Sosnoff | 11 "Boring" Trading Strategies the top 1% of Retail Traders Use |
| 19 | Addy Khakoo | How I Trade SPX & ES 0DTE Options \| $200/Day Strategy |

### On the outcome claims

Two of these titles state specific multiples: **$10k → $1M in one year** (100×)
and **38k → 680k** (18×). No verifiable track record accompanies either, and
survivorship in this genre is total — the accounts that took the same trades and
were wiped out do not publish. **These numbers must never appear in this repo as
anything but a description of a video title.** Note also that the highest MC p50
this project has ever measured on real cost-charged trades is **1.772× over 250
trades**, and even that overstates what an account can realise (§9.1 of the
autostrat doc).

### What the repo already knows about options, which is a lot

`rhengine/market_data_options.py` is a deliberately measurement-only VRP kit,
and it already establishes the two facts that govern this whole cluster:

1. **The mechanism.** `docs/AGENT-SPEC-VERDICT.md` rejected every alpha claim in
   the tactical corpus *except* the options overlay, which is "the only module
   with a documented economic mechanism": the **volatility risk premium** — S&P
   500 implied vol exceeds subsequently realised vol ~85% of the time by 2–4 vol
   points. Of the ten videos, **#18 (Tom Sosnoff / tastytrade) is the only one
   pointed at this mechanism** — selling premium, high IV, defined risk. It is
   the one worth taking seriously, and what makes it serious is the mechanism,
   not the video.
2. **The prior to beat is bad.** CBOE **BXM** (the buy-write index) returned
   **~1/3 of the S&P over the last decade.** Harvesting the premium is not the
   same as beating the index, because the truncated upside costs more than the
   premium pays in a bull market. Any options overlay proposal starts from that.

### The blocker — RE-SPIKED 2026-07-31, and it was wrong

**Correction.** The 2026-07-30 spike recorded in `market_data_options.py` said
`implied_volatility`, delta, gamma, theta, vega and rho all return NULL. Tested
again the same day through the **Robinhood MCP server**, they are all
**populated**, along with `open_interest`, `volume`, and
`high/low_fill_rate_buy/sell_price` — an explicit fill model. Sample kept at
`research_data/options/spy_quotes_2026-07-31.json` and pinned by
`tests/test_options_costs.py::TestSnapshot::test_greeks_are_populated` so the
false claim cannot return to the docs.

What survives from the original spike:

- `get_option_historicals` bars still carry no greeks and no volume, so
  `market_data_options.implied_vol` is still needed for HISTORY.
- **Expired-contract discovery still has a weeks-long retention window.**
  2026-07-17 resolved; 2026-06-19 did not. Price history is retrievable given
  the instrument UUID, so UUIDs must be captured **prospectively** or a
  retrospective study becomes impossible to assemble.

### The cost model — BUILT 2026-07-31, and the naive conclusion was wrong

`rhengine/options_costs.py` + `tests/test_options_costs.py` (217 tests passing).
Measured on SPY at 746.81. **One cost number is not enough — three units, and
they disagree in direction:**

| exp | DTE | K | ty | prem bps | delta bps | theta bp/day | BE move 1d |
|---|---|---|---|---|---|---|---|
| 08-03 | 3 | 740 | C | 132 | **2** | 474 | 0.07% |
| 08-03 | 3 | 745 | C | 233 | **2** | 1262 | 0.11% |
| 08-03 | 3 | 750 | C | 274 | **1** | 3967 | 0.16% |
| 08-21 | 21 | 745 | C | 135 | 4 | 237 | 0.10% |
| 12-18 | 140 | 745 | C | 227 | 18 | 41 | 0.22% |

- **spread / premium** (132–274 bps): cost against capital deployed. The
  alarming number, and the wrong one for comparing against equities.
- **spread / (|delta| × spot)** (1–18 bps): cost per dollar of *underlying
  exposure*, the only unit comparable to `costs.round_trip_bps = 30`. The 100×
  contract multiplier cancels. **Short-dated SPY options are ~10× CHEAPER than
  the stock**, entirely because of leverage.
- **theta / premium** (41–3967 bps/day): equities have no analogue.

**I had this backwards in the first draft of this document**, which asserted
that option spreads made 0DTE prohibitively expensive. On the unit that matters
for comparison they are cheap. **The binding constraint is decay, not spread.**

`breakeven_move_pct` is the usable number: how far the underlying must move to
cover spread plus N days of theta. For the 3-DTE 745 call that is **0.11% in one
session** — genuinely low against SPY's ~0.7–1% daily range. That is why the
strategies in these videos can appear to work, and it is also why they are
fragile: the same leverage that makes the cost small makes the decay large.

**The weekend trap, which an unattended agent would walk straight into.** Theta
is per CALENDAR day. A Friday-close to Monday hold pays **three days of decay
for one trading day of movement**, roughly tripling the breakeven move (745C:
0.11% → 0.29%). Any autonomous short-dated options path needs an explicit
weekend rule.

### What is still missing before a 0DTE strategy could be evaluated

- **No historical option bars with greeks.** `get_option_historicals` has OHLC
  only, and expired-contract discovery expires in weeks, so there is no
  retrospective sample to backtest against. Live capture must run forward.
- **No realised-vs-implied measurement yet.** That is step 2 of the
  `market_data_options.py` sequence and it is now unblocked, but not done.
- **BXM remains the prior**, and it is bad.

## What was implemented from this intake

**One thing, and it comes from cluster A's theme rather than any single video:**
a `capital_rate` check wired into `rhengine/sanity.py` (now eleven checks, +5
unit tests, 206 passing). It operationalises the round-9 finding — R-multiples
are not returns — as a gate rather than a note:

```
hpr = r_multiple * stop_pct          # identity: notional = risk/stop_pct
rate = sum(hpr) / sum(sessions_held) # bp per dollar-day
PASS >= 4.10 bp/day (passive drift) | WARN if positive-but-below | FAIL if <= 0
```

**Its limitation, stated plainly: it would not have caught the macd candidate.**
That book scores +8.76 bp/day and PASSes, yet loses to SPY over its own chosen
windows (net −1.27 bp/day, §9.3). The battery deliberately does not fetch price
data, so it can only test against the *unconditional* drift — the easy hurdle.
A book that picks its days faces a harder one. The check catches the gross
failures (negative or drift-starved rates); the real test stays in
`scripts/autostrat_dollarday.py`, which needs prices.

## What was not built, and why

- **No 0DTE or options STRATEGY.** The cost model now exists and the greeks
  blocker is gone, but there is still no historical sample to test against
  (expired-contract discovery expires in weeks), no realised-vs-implied
  measurement, a bad documented prior (BXM), and CLAUDE.md rule 6 — autonomy is
  earned and G1 is 0 of 10. Building an unattended options path on video
  evidence would invert every standing rule at once. The measurement-first
  sequence in `market_data_options.py` (steps 3 and 4 not started) is the
  correct path and is unchanged.
- **No routing change.** `regime_routing` stays all-null, every sleeve stays
  `user_configurable`, nothing moves to `autonomous`.
- **No bar-permutation null yet.** It is the one real methodological gap found
  (§Cluster A #2) and it is worth building, but it is a new statistical tool and
  belongs in its own change with its own tests — not bolted on at the end of an
  intake.
