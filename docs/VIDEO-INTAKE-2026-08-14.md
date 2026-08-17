# Video intake — 2026-08-14

Two batches on the same day. **Batch 1: seven videos** (§Clusters D–G below).
**Batch 2: twenty-six more videos plus nine playlists, 361 unique videos in
total** (§Clusters H–I). Same structure and the same standing rule as
[docs/VIDEO-INTAKE-2026-07-31.md](VIDEO-INTAKE-2026-07-31.md), which triaged
twenty-one and should be read first if this is your entry point.

The full batch-2 extraction is machine-readable at
`research_data/video_corpus_2026-08-14.tsv` (361 rows: id, channel, title,
source playlist, prior-triage marker), regenerable with
`py scripts/fetch_video_titles.py`.

## The standing rule, restated because it is the whole point of the exercise

`docs/CLAUDE-RESEARCH-RULES.md` and CLAUDE.md rule 4: evidence comes from the
published record (`research_data/external/`, Ken French, OSAP/JKP) or from
`rhengine/alpha/book.py`. **A video is not evidence.** It can supply a
*hypothesis*, a *method*, or a *warning*. It cannot supply a result.

**What I could and could not read.** YouTube renders client-side and there is no
transcript endpoint, so for all seven I have the **title and channel only** —
fetched through the oEmbed API, which is the one thing that does resolve. Nothing
below claims to summarise an argument I have not heard. Where I make a claim
about content, it is sourced to something outside the video and cited.

### Batch 1

| # | channel | title |
|---|---|---|
| 1 | Matt Pocock | mattpocock/skills: A complete AI Coding workflow, end-to-end |
| 2 | Algo-trading with Saleh | Kimi K3 + MCP = King of Algo Trading! |
| 3 | Ross Cameron — Warrior Trading | How to Grow a Small Account with ZERO Experience (Full Training) |
| 4 | Trading with DaviddTech | I Built an INSANELY Profitable AI Hedge Trading Bot (Full Guide) |
| 5 | AI Pathways | Claude Tested Over 25,000 ICT Strategies (Here's What Works) |
| 6 | Jay E \| RoboNuggets | This NEW Claude Prompting Technique is blowing people's minds (gauntlet-loop) |
| 7 | Chase AI | Use These 17 Claude Plugins, It Will Make You 10x Better |

Four of seven are agent-tooling content, two are LLM-builds-a-bot content, one is
retail day-trading education. **None of the seven is a source of trading
evidence.** One of them — #5 — is nonetheless the most useful video this project
has been sent, for reasons that have nothing to do with whether its strategies
work.

---

## Cluster G — #5, and why it is the one that mattered

> **AI Pathways — "Claude Tested Over 25,000 ICT Strategies (Here's What Works)"**

Same channel as #9 in the previous intake ("How To Actually Use Claude Code for
Trading Strategies"). The subject matter was closed here on 2026-07-30 and
nothing in this video reopens it — but the *number in the title* is the finding.

### The subject matter: already closed, twice

`docs/AGENT-SPEC-VERDICT.md` §4 audited the codeable core of ICT / Smart Money
Concepts and rejected it: fair value gaps have **no peer-reviewed literature**,
every source is a broker blog or a Medium post, and the recurring "70–85% fill
rate" claim carries no holding period, no cost, no benchmark, and no accounting
for the unfilled 15–30% where the losses live. The previous intake's Cluster
A-bis re-ran that verdict against a second ICT video and added the two nearest
analogues this repo has actually measured:

| ICT construct | nearest thing here | outcome |
|---|---|---|
| liquidity sweep / stop-run reversal | `STreversal` (OSAP) | **negative in every window** — d_t −1.03 / −0.57 / −0.84 |
| order block / prior consolidation | the `consolidation` strategy | **retired** — "COMP leg toxic" |
| fair value gap | — | never built; blocked on `GAP-3.10` and `GAP-3.11` |

Both are now rows in the ledger described below, so the cost of having tried them
is counted rather than forgotten.

### The number in the title is the finding

**25,000 backtests is not a search for an edge. It is a draw from the maximum of
25,000 nulls, and that draw has a known distribution.**

`rhengine/trials.py` computes it. Under the null — every one of those 25,000
variants worthless — the *expected best* t-statistic is:

```
py -m rhengine.trials expected-max 25000
trials              : 25000
E[max t] under null : 4.08
required t @ 0.05   : 4.75
a t of 2.00 has family-wise p = 1.000
```

So a strategy emerging from a 25,000-variant sweep with **t = 4.0** is *below
average luck*. A t of 2.0 — the number most retail backtesting treats as
conclusive — has a family-wise p of **1.000**: among 25,000 coin flips it is
certain that something scores that well. No claim is being made here about what
that video found, because I could not watch it. The claim is about what a
finding from 25,000 trials is *worth* before you look at it, and the answer does
not depend on the strategies.

### Why that indicts this repo and not just the video

This project is also an agent that can try a hundred variants an hour. The
previous intake recorded the hazard in Cluster B and named the three existing
defences (spent holdouts, the every-window rule, `docs/HOLDOUT.md`
pre-logging). It concluded they were load-bearing and should not be relaxed.
They still are. But there was a fourth defence that the 2026-07-24 audit
prescribed and nobody built:

> **FC-14 remediation, §8 Phase 0:** "Create append-only experiment ledger;
> backfill the ≥12 known trials" → `validation/ledger.py`, `experiments.jsonl`.

`docs/AUTOSTRAT-2026-07-31.md` §G1-9 states the consequence in one line: the
Deflated Sharpe Ratio is *"not computed, and the trial count for this project is
not tracked."* `alpha/stats.py` has had `deflated_sharpe(sr, n, trials, ...)`
since the audit, and every caller passes a **hand-set constant** — `trials=1` in
`alpha/buywrite.py`, `trials=50` in `alpha/rmxd.py`, `trials=60` in
`alpha/rmxq.py`. Those are guesses. The machinery to price the search existed;
the input was invented.

**That is what got built from this intake.** See §What was implemented.

---

## Cluster D — agent tooling and prompting (#1, #6, #7)

| # | what it is | verdict |
|---|---|---|
| 1 | Matt Pocock's public skills pack — ~25 skills covering spec-writing, TDD, triage, domain modelling, code review, handoff | **not installed** |
| 6 | a prompting technique the title calls "gauntlet-loop" | **not adopted**; one hazard recorded |
| 7 | "17 Claude plugins" | **out of scope** — general workflow tooling, explicitly not wanted here |

### #1 — the pack is real, and it is general-purpose

I could read this one, because the repo it demonstrates is public. It contains
`to-spec`, `implement`, `tdd`, `diagnosing-bugs`, `code-review`, `wayfinder`,
`research`, `handoff` and about seventeen more, aimed at four stated failure
modes: developer/agent misalignment, verbosity, non-functional code, and
architectural degradation.

Good work, wrong shape for this repo. The instruction standing over this intake
is *only add skills that are beneficial to this project, not general ones for
expanded workflows*, and every skill in that pack is a general software-
engineering skill. This repo's two domain skills (`strat-refine`, `strat-data`)
plus `i-have-adhd` already cover the loop that matters here.

**One idea in it is worth noting as corroboration rather than as an import.**
Its `to-spec` step — write the specification down *before* implementing, so the
agent cannot quietly redefine success — is structurally the same move as
`docs/RMX-PREREGISTRATION.md` and `docs/HOLDOUT.md`, which this repo built for
statistical rather than engineering reasons. An outside practitioner arriving at
pre-commitment independently is the same kind of corroboration the previous
intake found in the permutation-test video. **No new skill; the existing
`strat-refine` skill gained the missing pre-commitment step instead** (see
§What was implemented).

### #6 — the hazard worth writing down

I do not know what the "gauntlet-loop" technique is; the title is all I have, and
the name suggests running an output through repeated adversarial passes until it
survives. **If that is what it is, applying it to strategy selection is the
failure mode in Cluster G with better branding.** Iterating a candidate against a
gauntlet until it passes *is* multiple testing, and each pass is a trial whether
or not anyone logs it. The technique may be excellent for prose or code, where
there is a ground truth to converge on. Backtests have no ground truth to
converge on inside the sample — only noise to fit.

If you have watched it and it is not that, say so and I will assess it properly.

---

## Cluster E — "the LLM built me a profitable bot" (#2, #4)

| # | title |
|---|---|
| 2 | Kimi K3 + MCP = King of Algo Trading! |
| 4 | I Built an INSANELY Profitable AI Hedge Trading Bot (Full Guide) |

Kimi K3 is real — Moonshot AI's 2.8T-parameter open-weights MoE model, weights
released 2026-07-27 — so #2 is not a fabricated premise. That establishes the
model exists. It establishes nothing about trading.

Both videos are the genre the previous intake's Cluster B described, with the
model swapped. The structural objection is unchanged and is not about which model
is used: **a more capable model generating and screening strategy variants makes
the selection problem worse, not better,** because the trial count rises and
nothing in the tooling counts it. #4's "INSANELY Profitable" is an outcome claim
with no verifiable track record, in a genre where survivorship is total — the
bots that lost are not published. Per the previous intake's rule, that phrase may
appear in this repo only as a description of a video title, which is what it is
doing here.

**Nothing to build.** MCP-driven data access is already how this repo reaches
Robinhood options data (`docs/OPTIONS-ACCUMULATOR.md`), and the constraint there
was never the transport.

---

## Cluster F — retail day-trading education (#3)

> **Ross Cameron / Warrior Trading — "How to Grow a Small Account with ZERO
> Experience (Full Training)"**

This one needs a provenance note that has nothing to do with the trading content,
because the content is inseparable from a documented regulatory finding.

**In April 2022 the FTC brought an action against Warrior Trading and Ross
Cameron and obtained a $3,000,000 settlement**, alleging that the company's
advertising showcased Cameron's own trading results in a way that implied
customers would achieve similar success, in violation of the FTC Act and the
Telemarketing Sales Rule. The complaint alleged that **the vast majority of
customers lost money trading**, on top of what they paid for the training. The
settlement bars baseless earnings claims; Warrior Trading and Cameron settled
without admitting or denying the allegations. The FTC returned **$2.9 million to
consumers** in January 2023.
([FTC press release](https://www.ftc.gov/news-events/news/press-releases/2022/04/federal-trade-commission-cracks-down-warrior-trading-misleading-consumers-false-investment-promises) ·
[FTC case page](https://www.ftc.gov/legal-library/browse/cases-proceedings/2023198-warrior-trading-inc-ftc-v) ·
[FTC redress announcement](https://www.ftc.gov/news-events/news/press-releases/2023/01/ftc-returns-more-29-million-consumers-harmed-warrior-trading))

That is a fact about the source, not an argument about the method, and it is
recorded because "grow a small account with zero experience" is precisely the
claim class the settlement covers.

**The method is also structurally inapplicable here.** It is discretionary
intraday momentum trading of low-float small caps — attended, sub-$20 stocks,
many trades per session. This engine trades **US equities on daily bars**, is
long-only, screens the **point-in-time S&P 500**, and its shipped book turns over
~190%/yr with a monthly re-form. There is no shared surface.

**One thing in the topic is real and already answered.** The small-account
question is a live constraint here — `docs/OPTIONS-SOURCES-2026-08-01.md`
establishes that the wheel needs ~$70k per cycle and is impossible on cash-
settled SPX/XSP/NDX, which is why the options path is disarmed pending capital.
That answer came from the capital table and the published CBOE record, and it
does not change.

---

---

# Batch 2 — 26 videos and 9 playlists (361 unique)

## Cluster H — the sweep-count series (26 loose videos)

**Five were already triaged** and are duplicates, not new information:
`ZVMTeDBmSrI` and `Z-hU97WO30I` (Lewis Jackson, Cluster B #7/#8 of the
2026-07-31 intake), `EUSXhJNwRqI` (AI Pathways, #9), `mkzcntzznMc` (MemLabs,
Cluster A #5), `NLBXgSmRBgU` (neurotrader permutation tests, Cluster A #2 — the
video that supplied the one genuine methodological gap, still unbuilt).

Of the twenty-one that are new, **eighteen are the "an LLM built me a trading
strategy" genre** across seven channels (AI Pathways ×5, Algo-trading with Saleh
×4, Trading with DaviddTech ×2, plus Miles Deutscher, EllyDTrades, Unbiased
Trading, Mind Math Money, SMB Capital, Samin Yasar, Michael Automates). Three are
education: two freeCodeCamp full courses, one "become a quant in 31 minutes",
one "I made an AI learn stock market patterns".

### The finding: a search-size series is now on the record

Batch 1's Cluster G was built around one video claiming 25,000 backtests. Batch 2
supplies two more explicit counts from two different channels:

| video | claimed sweep |
|---|---|
| AI Pathways — "Claude Tested Over 25,000 ICT Strategies" (batch 1) | 25,000 |
| EllyDTrades — "Claude Backtested 10,000 Trading Strategies. Here's What Actually Works" | 10,000 |
| Trading with DaviddTech — "I Let Claude Fable 5 Test 143,000 Trading Strategies (HERE ARE THE BEST)" | 143,000 |

These are not competing claims about markets. They are three samples of the same
procedure — sweep, rank, publish the top — at three scales, and the scale is the
only number in them that can be evaluated without watching anything.

```
py -m rhengine.trials expected-max 143000
py -m rhengine.trials expected-max 143000 --rho 0.9
```

| sweep | E[max t], ρ=0 | required t, ρ=0 | E[max t], ρ=0.9 | effective trials | required t |
|---|---|---|---|---|---|
| 10,000 | 3.86 | 4.56 | 1.22 | 6 | 2.63 |
| 25,000 | 4.08 | 4.75 | 1.29 | 6 | 2.63 |
| 143,000 | 4.47 | 5.09 | 1.41 | 8 | 2.73 |

**Read both columns, because the honest answer needs both and they point the
same way.**

- **Taken as independent tests (ρ=0)**, the winner of a 143,000-variant sweep
  has to reach **t = 5.09** to mean anything, and gets **4.47 for free**. No
  retail backtest reports a t-stat at all, let alone one above 5.
- **Taken as what they really are (ρ≈0.9 — a parameter grid over one idea)**,
  143,000 variants are worth about **8 independent tries**. The sweep is far
  less damning than the number sounds. But then the sweep also *bought* almost
  nothing: eight tries is a morning's work, and the 143,000 was theatre.

Either way the conclusion is the same and it is not about ICT, scalping, or
which model was used: **a ranked winner reported without its trial count and its
correlation assumption is uninterpretable.** That is not a criticism these videos
uniquely deserve — it is the standard this repo now holds itself to, and
[§What was implemented](#what-was-implemented-batch-2) records that RMX-B's own
headline fails the α=0.05 version of it.

### The outcome claims, recorded as titles and nothing more

"Sonnet 5 + Claude Code strategy makes 369%", "This Claude Fable 5-Minute
Scalping Bot is SO GOOD It's Almost Illegal (Yours FREE)". Per the 2026-07-31
rule these phrases may appear here only as descriptions of video titles, which is
what they are doing. The highest MC p50 this project has ever measured on real
cost-charged trades is **1.772× over 250 trades**, and even that overstates what
an account realises.

### One video that touches a live rule

**Michael Automates — "Finally FULL Portfolio Trade Automation with AI — Claude
MCP Routines (IT WORKS)".** Unattended order routing is the exact surface
CLAUDE.md rule 6 and [docs/GOLIVE-RUNBOOK.md](GOLIVE-RUNBOOK.md) govern. Nothing
here changes: `alpha/live.py::emit` stamps `orders_permitted: false` on any book
whose `strategies.json` status is not exactly `"autonomous"`, **no book is**, and
arming one writes the override it runs against into every payload. A video
demonstrating that the plumbing is easy is not evidence that the permission is
earned; the permission is the whole control.

---

## Cluster I — nine playlists, 335 unique videos. Curriculum, not evidence.

| playlist | owner | n | what it is |
|---|---|---|---|
| Introductory Lectures in Quantitative Trading (QT101) | HangukQuant | 25 | engineering course: yfinance, OOP alpha units, vectorization, profiling, **position sizing and volatility targeting** |
| Quantitative trading strategies | Liu Peng | 34 | academic lecture course: market microstructure, futures pricing, **pairs trading / cointegration**, Bayesian optimization, ML pairs |
| Full Algorithmic Trading Course | TradeOptionsWithMe | 19 | QuantConnect tutorial series (universes, backtesting, forex, options, crypto) |
| Quant Trading Accelerator | MemLabs | 9 | short course: vectorization, time series, statistical edge, **cross-validation**, bot |
| For Beginners in Quantitative Trading | Quantra / QuantInsti | 35 | vendor course-marketing shorts |
| Introduction to Market Making | QuantIntuition | 13 | market making, order books, hedging, MM risk |
| 100 Days of Hell with Python Algo Trading? | Kuldeep Singh Rathore | 90 | ~40 videos of Python/NumPy/pandas basics, then descriptive stats, Natenberg options theory, freqtrade crypto |
| Quantitative Trading | (curated) | 92 | a link-dump of 2015–2017 conference talks: Quantopian, Chat With Traders, PyData, Ernie Chan, Jim Simons' TED talk |
| Advanced Quantitative Trading Strategies | QuantInsti | 21 | webinars: Ernie Chan mean reversion, momentum, IV, regime change |

Three videos appear in two playlists each; the corpus TSV keeps first occurrence
only, hence 335 unique from 338 rows.

### The verdict, and it is short

**Nothing to build, and nothing to test.** These are courses and conference
talks. Not one of them is a source of evidence under the standing rule, and none
of them claims to be — that is the difference between this cluster and Cluster H,
and it is to this cluster's credit.

**Provenance note on the 92-video list.** It is largely a snapshot of the
2015–2017 Quantopian era; Quantopian shut its platform down in 2020. Several
entries are tooling walkthroughs for a service that no longer exists. Useful as
history, not as a current toolchain recommendation.

### What the curriculum corroborates, which is worth naming

Three threads in these playlists describe tools this repo already built, which
is method corroboration of the same kind the 2026-07-31 intake found in the
permutation-test video:

| thread | where in the playlists | what this repo already has |
|---|---|---|
| **cross-validation of strategies** | MemLabs Accelerator Part 7 | `rhengine/crossval.py` — blocked CV with purge/embargo, `--by-regime` |
| **volatility targeting / position sizing** | HangukQuant QT101 parts 11–12 (instrument-level and strategy-level vol targeting) | `sleeves.scale_by_own_vol`, `blend.py --vol-scale` (on for momentum), `rmxb.py --vol-scale-momentum` (off) |
| **mean reversion as a strategy class** | QuantInsti "Advanced" ×2 Ernie Chan videos, plus 10-categories webinar | already screened and **negative**: `STreversal` d_t −1.03/−0.57/−0.84 in every window, RSI-2 rejected, `consolidation` retired |

**And one that is the exact counterweight to Cluster H.** Playlist 8 contains
Robert Carver's QuantCon 2017 talk **"Trading Strategies that are Designed, Not
Fitted."** That title is this repo's whole thesis, and it sits in the same intake
as three videos advertising 10,000-, 25,000- and 143,000-variant fits. Recording
the pair is the most useful thing in this cluster.

### What is structurally inapplicable, so nobody re-reads it later

A large share of these 335 videos cannot apply here regardless of quality,
because of what this engine is — **US equities, daily bars, long-only, monthly to
annual re-formation, ~190%/yr turnover**:

- **market making and order-book strategies** (13 videos) — requires quoting
  both sides and inventory risk management;
- **HFT / microsecond latency / C++** (several in playlist 8) — wrong timescale
  by nine orders of magnitude;
- **crypto and forex bots** (freqtrade block, ~7 videos; several more) — wrong
  asset class, and this book cannot short;
- **intraday scalping and day trading** — the repo's daily-bar decision-to-close
  execution model cannot represent it.

That is not a dismissal of the content. It is a statement that the intersection
between this corpus and this engine's surface is small, and it is in the
engineering videos (vectorization, portfolio construction, vol targeting), not
the strategy ones.

---

## What was implemented (batch 2)

**Two things, both small, both motivated by Cluster H.**

### 1. Trial correlation — `expected_max_t(n, rho)` and `effective_trials(n, rho)`

The batch-1 module treated every trial as independent. A 143,000-variant grid
sweep over one idea is nothing like 143,000 independent tests, and pretending
otherwise sets a bar so absurd it invites being ignored. For equicorrelated
normals the correction is exact rather than a fudge — writing
`X_i = √ρ·Z + √(1−ρ)·ε_i`, the common factor has mean zero and drops out:

```
E[max | ρ] = √(1 − ρ) · E[max | iid]
```

`effective_trials` inverts that to "how many independent tries was this worth",
resolving **upward** to the conservative integer. `verdict()` now reports the
assumed ρ and both trial counts, because a burden quoted without its ρ is not
interpretable. CLI: `--rho` on both `report` and `expected-max`.

**The obvious abuse, stated in the docstring so it cannot be quiet:** raising ρ
makes any search look cheap — at ρ=0.95, 143,000 variants price like 4 tries. The
rule is to **quote the ρ=0 figure alongside whatever ρ you assumed** and justify
it, and if the trial returns exist, cluster them rather than guessing.
`tests/test_trials.py` is now 23 tests, including one asserting the √(1−ρ)
identity to 10 decimal places and one that the effective count is bracketed
correctly.

### 2. `scripts/fetch_video_titles.py` — the intake tool

Batch 2 could not be triaged by hand: nine playlist URLs, 338 rows. The script
takes any mix of video and playlist URLs and emits the TSV.

Two things worth recording because they cost time to find:

- **`yt-dlp` is not installed here**, and the obvious fallback broke: YouTube
  moved playlist items from `playlistVideoRenderer` to **`lockupViewModel`**, so
  every scraper written against the old shape returns zero rows against a page
  that plainly has videos on it. The script parses the new shape and follows
  continuation tokens through the innertube browse endpoint for lists past one
  page (two of these had 90+).
- **oEmbed remains the only per-video endpoint that resolves**, and it returns
  title and channel only. **Transcripts are still unreachable.** That is the
  permanent constraint on every intake doc: judge by title, channel and the
  channel's outside record, and say so.

Output for this batch: `research_data/video_corpus_2026-08-14.tsv`, 361 unique
rows with a `prior_triage` column marking the five already-seen videos.

---

## What was implemented (batch 1)

**`rhengine/trials.py` + `research_data/trials.jsonl` + `tests/test_trials.py`
(18 tests) — the append-only experiment ledger the audit ordered on 2026-07-24
and nobody built, plus the arithmetic that makes it bite.**

### The module

```bash
py -m rhengine.trials report                 # counts + required t, per family
py -m rhengine.trials report --family rmx --t 2.54
py -m rhengine.trials list --family sleeves
py -m rhengine.trials expected-max 25000     # what luck alone scores
py -m rhengine.trials log --family sleeves --label "..." --kind structural \
    --outcome rejected --source docs/RMX-C.md
```

Four functions carry it: `expected_max_t(N)` (the Bailey & López de Prado
approximation to E[max] of N standard normals — the same estimator
`alpha/stats.deflated_sharpe` already uses internally, now callable on its own),
`sidak_t(N, α)` (the bar a result must clear), `bonferroni_t(N, α)` (kept because
the audit quotes it), and `family_wise_p(t, N)` (how surprising is this, after N
tries).

**The implementation is checked against a document written before it existed.**
Audit FC-14 asserted, with no code to support it, that "with ≥12 trials, a
Bonferroni-style threshold is t ≈ 2.9–3.1". The module returns **2.87** at 12
trials, and `test_matches_audit_fc14` pins it there. Two independent derivations
agreeing is the only reason to trust either.

### The ledger

Thirty-four rows, **54 trials**, every one backfilled from a document already in
this repo and every one carrying its citation. `test_every_backfilled_row_cites_
a_source` fails if a row is ever added without one.

| family | trials | E[max t] | required t @ 0.05 |
|---|---|---|---|
| rmx | 18 | 1.85 | 2.98 |
| legacy-engine | 14 | 1.74 | 2.91 |
| sleeves | 14 | 1.74 | 2.91 |
| external-screen | 7 | 1.39 | 2.68 |
| options | 1 | 0.00 | 1.96 |
| **all** | **54** | **2.31** | **3.31** |

Some rows carry `n > 1` because the search was documented only in aggregate —
"8 price-only cross-sectional signals, best |t| = 0.89" is eight trials whose
individual names were never written down, and counting that row as one would
understate the burden by seven. The count sums `n`, not rows.

**Trials are counted per family, and that is a deliberate limit.** Bonferroni
over everything would charge RMX-B's legs for the legacy engine's ETF exclusion,
which shares no data, no universe and no null with it. `--family all` exists and
is the right number only when the question is "did this repo, searching
everywhere, find anything?"

### What the ledger says about the shipped strategy, stated plainly

RMX-B's headline is **alpha t = +2.54** on Ken French 1963–2026.
`docs/RMX-PREREGISTRATION.md` §4 declared a budget of **6 trials** ("1 baseline +
4 single-leg + 1 combined"). At 6 trials the two-sided Šidák bar is **2.63**:

```
observed t          = +2.54
luck alone delivers = 1.30
required t @ 0.05   = 2.63
family-wise p       = 0.065
DOES NOT CLEAR its own trial burden
```

**RMX-B's headline misses its own pre-registered multiple-testing bar at α=0.05
by 0.09, and clears it at α=0.10.** That is not a reason to drop the strategy —
the pre-registration was honest, the trial budget was small and declared in
advance, the record is external and survivorship-free, and 4/4 eras held. It is
a sharpening of the boundary CLAUDE.md already states: +1.78%/yr against 5.5%/yr
tracking error needs ~39 years to reach t=2, and the 63-year French record gets
to 2.54, which is *almost* enough once the six declared trials are paid for.
`test_rmxb_headline_t_does_not_clear_its_preregistered_budget` exists so nobody
re-rounds this away.

Charging RMX-B the full `rmx` family count of 18 (required t = 2.98) would be
the wrong test — most of those 18 were run on the local panel, not on the French
portfolios — and the module does not do it automatically. The 6 is the defensible
number and it is the one quoted here.

### The skill change

`.claude/skills/strat-refine/SKILL.md` gained the two steps that make the ledger
load-bearing rather than decorative:

- **step 0 — log the trial before running it**, whether or not you expect it to
  work. A trial logged only when it wins is worse than no ledger.
- **step 8 — pay for the search**: `trials report --family <f> --t <t>` after the
  Monte Carlo, and a survivor that reads DOES NOT CLEAR is the maximum of N
  draws, not a finding.

"Done" now requires the trial to have been logged before it ran and the reported
t to clear the Šidák bar at the family's count.

**No new skill was created.** The instruction was to add only skills beneficial
to this project, and the beneficial change was a missing step in a skill that
already existed.

---

## What was not built, and why

- **No ICT / SMC anything.** Closed 2026-07-30, re-closed 2026-07-31, unchanged
  by a third video. Both nearest analogues are now ledger rows (`STreversal`
  rejected, `consolidation` retired) so the cost of having tried them is counted.
- **No `trials=` rewiring of `rmxd.py` / `rmxq.py` / `buywrite.py`.** Their
  hand-set 50 / 60 / 1 are now visibly wrong and the ledger can supply the real
  numbers, but changing what those modules report changes published figures in
  `docs/RMX-RESULTS.md`, and that belongs in its own change with its own
  re-verification — not appended to an intake. **Logged as the open item.**
- **No bar-permutation null.** Still the one real methodological gap, carried
  forward unchanged from the 2026-07-31 intake §"What was not built".
- **No routing change, no promotion, no book armed.** `regime_routing` stays
  all-null; no book moves to `autonomous`. Batch 2's automation video does not
  change this — see §Cluster H.
- **No skills installed from #1 or #7.**
- **Nothing built from the 335 playlist videos.** They are courses; the three
  threads that overlap this engine (cross-validation, vol targeting, mean
  reversion) are respectively already built, already built, and already
  rejected. See §Cluster I.
- **No measured ρ.** `effective_trials` takes ρ as an assumption. Estimating it
  properly means clustering the trial return series, which this repo does not
  retain per trial. Logged as the open item behind the ρ=0 reporting rule.
- ~~**Still no bar-permutation null.**~~ **BUILT 2026-08-14** as
  `rhengine/barperm.py` (+15 tests) after the corpus was triaged — see
  §"The one thing 389 videos actually produced" below.

---

# The one thing 389 videos actually produced

Asked directly, after three intakes: **did any video help?** The honest tally
across `VIDEO-INTAKE-2026-07-31.md` (21) and this document (368) is **zero
signals, zero strategies, zero usable numbers.** Two things came out.

**The trials ledger is the weaker of the two, and its credit belongs elsewhere.**
Cluster G frames `rhengine/trials.py` as coming from the 25,000-strategy video,
and that is true about the *urgency* but not the *information*: the 2026-07-24
audit had already specified it (FC-14 remediation, §8 Phase 0, naming
`experiments.jsonl` and the ">=12 known trials" backfill). The video supplied a
vivid reason to stop deferring it. The one genuinely new piece — the trial
correlation correction — was derived here, not taken from any video.

**The other one is real, and it is a single video: neurotrader's permutation-test
video, Cluster A #2 of the 2026-07-31 intake.** Deferred there, deferred again in
both 2026-08-14 batches, built now.

## `rhengine/barperm.py` — the bar-permutation null (16 tests)

The third member of the null-test family. What each can and cannot ask:

| module | permutes | can conclude |
|---|---|---|
| `montecarlo.py` | the strategy's own trade R-multiples | outcome variance **given** the edge; its docstring says outright it "cannot say whether the edge exists" |
| `nulltest.py` | the ENTRY, against **real price paths** | whether the entry beats luck — but drift and vol clustering survive, which is why its round-3 null mean was +0.0552R rather than zero |
| **`barperm.py`** | **the DATA** | whether the whole system, signal generation included, beats a series with the same returns and no memory |

Per-bar log-relatives (`gap`, `h`, `l`, `c`) are shuffled as a unit and prices
rebuilt, so bar geometry survives and ATR-based stops stay realistic.

**The permutation is synchronized across every ticker and SPY** — one shuffled
ordering of the master calendar drives all series. Independent per-ticker
shuffling would destroy the cross-sectional correlation matrix and with it the
market factor, and a long-only book beats a market-less null trivially. That is
the same class of error as the drift leak in `nulltest.py`, and avoiding it is
the design decision the module rests on. SPY is permuted too, or the regime
router reads real structure the stock bars no longer contain.

### The property that is easy to get backwards, and I did

The first draft of this module printed *"drift is destroyed here."* **It is not.**
A sum is invariant to ordering, so permuting log-returns leaves each ticker's
total window return **exactly unchanged**. This null removes the *sequencing* of
the drift, never the drift. Consequences:

- a **positive null mean is expected, not a defect** — it is what buy-and-hold
  drift plus the exit engine plus the sizing earn with no signal at all;
- and that makes it a *harder* hurdle than `nulltest.py`'s, not a softer one,
  because the real strategy must beat the drift it was previously being
  credited for.

`test_total_drift_is_PRESERVED_not_destroyed` pins it so the docs cannot slide
back.

## First measured result: `macd` fails it

200 paths, 150-ticker cap, 30 bps resolved cost, 400-day window:

```
real avg_r          -0.0253   (687 trades)
null mean          +0.0154
null p50 / p95     +0.0101 / +0.1459
null p99 / max     +0.1911 / +0.2795
null median trades 654  (real 687)

p = 0.7313   FAIL — indistinguishable from shuffled bars
```

**73% of memoryless-bar paths beat the real strategy.** Stronger than that: the
real book is *negative* while the same exits on structureless data are
*positive*. Running `macd`'s signal over these 400 days did not merely add
nothing — it underperformed feeding the identical exit engine bars in random
order.

This is consistent with, and sharper than, the existing finding that `macd`
beats unconditional SPY on rate but loses to SPY over its own chosen windows.
That comparison needed prices and a benchmark; this one needs neither. Logged in
the ledger as a `legacy-engine` structural trial, outcome rejected.

**Two boundaries on the result, stated with it.** It is one strategy, one
400-day window, one 150-ticker slice — the cap is a runtime choice, not a
sample-size claim. And the real trade count moved 693 -> 687 between two runs
hours apart because the `massive` cache backfilled in between; the underlying
bars are live, so exact reproduction needs a pinned cache.

## What this does not resolve

`barperm.py` tests the **legacy engine** — daily bars, entry signals, R-space.
The research stack's cross-sectional monthly books (RMX-B/C) are not covered:
permuting bars under a monthly re-formed cap-weighted book asks a different
question, and building that is a separate change. The legacy engine is where
`nulltest.py` already lives and where the entry-signal question was open, which
is why it went there first.

---

## All five user-configurable strategies fail the bar-permutation null

200 paths each, 150-ticker cap, 400-day window, 30 bps resolved cost. Every one
pre-logged in the ledger before it ran, per CLAUDE.md rule 8.

### First, what could not be tested, and why that is good news

**The shipped regime-auto book emits zero trades.** `config.json`
`regime_routing` is `null` and every entry in `strategies.json` is
`user_configurable` — **none is `autonomous`** — so `select_strategy_for_regime`
returns `None`, and None means trade nothing. Zero trades at a 150-ticker cap
and still zero at 600. There is no auto book to null-test, which is rule 6
holding exactly as designed, not a defect. Logged `inconclusive`.

So the surface worth testing is the five strategies **a Nydex user can switch
on**.

### The results

| strategy | trades | real avg_r | null mean | null p50 | null p95 | p | verdict |
|---|---|---|---|---|---|---|---|
| bollinger | 1,000 | **+0.0143** | +0.0108 | **+0.0136** | +0.1294 | 0.4876 | FAIL |
| vwap | 409 | **+0.0084** | −0.0004 | −0.0059 | +0.1491 | 0.4279 | FAIL |
| residualmomentum | 6,679 | −0.0079 | −0.0051 | −0.0039 | +0.0827 | 0.5323 | FAIL |
| macd | 687 | −0.0253 | +0.0154 | +0.0101 | +0.1459 | 0.7313 | FAIL |
| pead | 368 | −0.1131 | −0.0020 | +0.0142 | +0.1812 | 0.7960 | FAIL |

**Not one of the five is distinguishable from its own exit engine run on
memoryless bars.**

### The three findings inside that table

**1. `bollinger`'s apparent edge is the exit engine.** Real +0.0143 against a
null median of **+0.0136** — a gap of seven ten-thousandths of an R. The
strategy that looks best of the five on raw avg R is reproduced almost exactly
by feeding the same exits bars in random order. This is the finding the module
was built to be able to make, and neither `nulltest.py` nor `montecarlo.py`
could have made it.

**2. `macd` and `pead` do worse than no signal at all.** `macd` is −0.0253 real
against a **+0.0154** null mean; `pead` is −0.1131 against a null median of
+0.0142, with 80% of shuffled paths beating it. `pead` also fires 368 trades
where the null median is 528, so real earnings structure both *reduces* its
trade count and *worsens* its per-trade result. These are not "no edge" results;
they are negative-signal results.

**3. `vwap` is the only one whose real result sits clearly above its own null
median** (+0.0084 vs −0.0059) and it still fails at p=0.4279 — because the null
distribution is wide. Which is the caveat below.

### The honest caveat: FAIL here means "not distinguishable", not "worthless"

Look at the null p95 column: a *random-ordering* path can score +0.08 to +0.18
avg R. That spread is large because per-path avg R over a few hundred trades is
noisy and right-skewed — stops cap the downside, winners do not cap the upside.
Only a large real edge clears a null that wide.

**More paths will not fix this.** The null p95 is a property of the statistic's
distribution, not of how many times it is sampled; 2,000 paths would estimate
the same wide distribution more precisely. Power comes from a bigger per-path
sample — more tickers or a longer window. The ranking confirms it:
`residualmomentum`, with 6,679 trades per path, has by far the tightest null
(p95 +0.0827), and still returns p=0.5323.

So the defensible reading is: **at this sample size, no strategy in the shipped
set shows an edge this test can detect, and two show signs of being actively
negative.** Widening the universe is the way to sharpen it, and is not done here.

### A flaw in the ledger that this run exposed

Rule 8 says log the trial *before* running it, so the outcome arrives later.
The batch-1 design told you to append a second row with the outcome — which
would have **counted every pre-logged trial twice**, inflating exactly the
number the module exists to keep honest. Fixed: `Trial.resolves` names the
earlier row, and a resolution row contributes **0** to every count while the
ledger stays append-only. `test_resolution_rows_do_not_double_count` pins it.
The five outcomes above are recorded that way, and `legacy-engine` still counts
20 trials, not 25.
