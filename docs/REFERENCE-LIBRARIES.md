# Reference libraries — everything this repo leans on from outside itself

Three unrelated things share the word "library". All three are here:

| Part | Covers | Standing |
|---|---|---|
| [1 — external repos](#part-1--five-external-repos-and-what-each-is-actually-good-for) | five teaching/lookup GitHub repos | **not evidence.** Technique and vocabulary only |
| [2 — the published record](#part-2--the-published-record) | `research_data/external/` — French, OSAP, JKP, AQR | **the only admissible evidence** (hard rule 4) |
| [3 — Python dependencies](#part-3--the-python-dependencies) | the core / sim / analysis tier split | packaging; contains one known gap |

Parts 2 and 3 added 2026-07-31.

---

## Part 1 — five external repos, and what each is actually good for

Metadata verified against the GitHub API 2026-07-31.

**Read this first:** none of these five is an evidence source. Four are teaching
material and one is a link index. Under the standing verdict in
[EXTERNAL-RESEARCH-2026-07.md](EXTERNAL-RESEARCH-2026-07.md) §0 — published
large-cap anomalies run ~7 bp/month post-2005 at median t = 0.45 — a textbook
demonstrating a method is *not* evidence the method pays here. The evidence
sources remain `research_data/external/`, Ken French, and OSAP/JKP. Nothing
below may be cited to support a performance number; hard rule #2 is unchanged.

What they *are* good for: implementation technique, vocabulary, and lookup.
That is a real use, and it is the one claimed here.

---

## The five at a glance

| repo | stars | license | last push | one-line verdict |
|---|---|---|---|---|
| [stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading) | 20.2k | **MIT** | 2026-07-31 | **The only one with direct bearing on this repo.** Ch. 16/17/18 map onto open questions here |
| [wilsonfreitas/awesome-quant](https://github.com/wilsonfreitas/awesome-quant) | 28.3k | *none* | 2026-07-31 | A lookup table for "does a library exist for X". Never read end-to-end |
| [cantaro86/Financial-Models-Numerical-Methods](https://github.com/cantaro86/Financial-Models-Numerical-Methods) | 7.2k | **AGPL-3.0** | 2024-10-22 | Derivatives pricing. §5 (Kalman) and §6 (OU) are the only sections that touch anything here. **Do not vendor** |
| [LechGrzelak/Computational-Finance-Course](https://github.com/LechGrzelak/Computational-Finance-Course) | 806 | BSD-3-Clause | 2024-03-01 | 14 lectures on option pricing. Zero cross-sectional equity content. Park it |
| [romanmichaelpaolucci/Quant-Guild-Library](https://github.com/romanmichaelpaolucci/Quant-Guild-Library) | 1.3k | *none* | 2026-07-29 | YouTube-lecture companion notebooks. Lowest evidence weight of the five. **Do not vendor** |

### Licensing — the actionable constraint

This matters before you copy a single function, not after.

- **MIT** (ML4T) — safe to adapt into `rhengine/` with attribution in the
  docstring.
- **BSD-3-Clause** (Grzelak) — same, plus the copyright notice.
- **AGPL-3.0** (cantaro86) — copying code from it would put the AGPL's network
  copyleft over this repo, which ships to RobinhoodAgent and Nydex. **Read for
  method, reimplement from the maths.** Never paste.
- **No license file** (Quant-Guild-Library, awesome-quant) — default copyright,
  all rights reserved. You may read; you may not copy. awesome-quant is a list
  of links, so the practical exposure is nil, but note that *appearing on that
  list licenses nothing to you either* — each linked project has its own terms.

---

## 1. ML for Trading, 3rd ed. — the one worth opening

MIT, 27 chapters, 20.2k stars, actively pushed. Already cited in
[EXTERNAL-RESEARCH-2026-07.md](EXTERNAL-RESEARCH-2026-07.md) §3 item 4 for
`alphalens-reloaded`; this is the fuller map. Every chapter directory ships both
`.ipynb` and a paired `.py`, so you can read it without a kernel.

### Chapters that touch an open question in this repo

**`18_transaction_costs/`** — bears directly on hard rule #3 (30 bps from
`config.json` → `costs.round_trip_bps`).

| notebook | why it matters here |
|---|---|
| `01_cost_taxonomy` | what the 30 bps is supposed to decompose into (spread / impact / fees / delay) |
| `02_spread_estimation` | estimating effective spread from OHLC — the cheap route to replacing an assumed 30 bps with a measured one, which rule #3 requires before it may be lowered |
| `09_frequency_tradeoff` | RMX-C runs ~190%/yr one-way turnover. This is that exact question |
| `10_gross_vs_net_performance` | the framing `alpha/book.py` already applies; useful as a cross-check on presentation, not on numbers |
| `05_almgren_chriss_optimal_execution` | out of scope — engine-only, no order planning |

Note the direction of travel: rule #3 says do not lower 30 bps without
**executed-fill evidence**. A spread estimator is not executed fills. It can
tell you the assumption is too *low*; it cannot license lowering it.

**`17_portfolio_construction/`** — RMX-C equal-weights five blocks by fiat.
These are the documented alternatives: `05_factor_allocation_evidence`,
`06_hierarchical_risk_parity`, `09_allocator_comparison`, `04_kelly_criterion`.
**The boundary:** picking among allocators on this project's 8-year S&P 500
panel is selection on a sample that cannot separate them — the same arithmetic
that puts RMX-B's +1.78%/yr at ~39 years to t=2. Read these to understand what
equal-weighting gives up; do not run a bake-off and adopt the winner.

**`16_strategy_simulation/`** — this repo has two engines whose numbers were
never reconciled after the 2026-07-24 audit. `01_backtest_first_principles`,
`06_framework_parity` and `07_engine_divergence_anatomy` are literally about
two engines disagreeing on the same signal. If the legacy engine's numbers are
ever re-established, that is the reading list.

**`06_strategy_definition/02_cv_foundations`** — purged / embargoed CV for
overlapping-label financial data. Relevant to the blocked cross-validation in
the `strat-refine` skill.

**`19_risk_management/02_exit_strategies`, `03_position_sizing_mae_mfe`** —
read with the §5.1 finding in hand: **ATR stops truncate the right tail of a
momentum sleeve**, and the fix for momentum's kurtosis is *sizing*, not
stopping. These notebooks are not a licence to re-add stops to a rank-and-hold
book.

### The maintained forks, which are the real deliverable

Jansen maintains `alphalens-reloaded`, `zipline-reloaded`, `pyfolio-reloaded`.
**`alphalens-reloaded`** is the highest-value item in this entire document:
quantile IC, turnover, and signal decay by holding period — which is what
[rhengine/ic.py](../rhengine/ic.py) computes by hand. Using it as a *second
implementation* to cross-check `ic.py` output is a legitimate, non-circular use.

### What it is not

A textbook. Its case studies are demonstrations chosen to teach a method, run
on data assembled for that purpose. None of the nine case studies is a
survivorship-free published record, and none of them screens as evidence under
[CLAUDE-RESEARCH-RULES.md](CLAUDE-RESEARCH-RULES.md). Chapters 21–24
(RL, RAG, knowledge graphs, autonomous agents) are outside the engine's charter
— engine-only means no orchestration and no ML.

---

## 2. awesome-quant — a lookup table, not a reading list

28.3k stars, 19 sections, updated continuously. Use it the way you'd use a
package index: you have a specific gap, you check whether something exists.

Sections that map onto real gaps here:

- **Market Data & Data Sources** — the standing data-hygiene problem. Cross-check
  against what's already been tried and rejected: Nasdaq directory is the real
  hygiene source, FMP free tier is spot-check only (bulk lists 402), Stooq is
  blocked, Yahoo works.
- **Calendars & Market Hours** — RMX-B executes "at the close of the session
  *after* the decision". That sentence has an exchange calendar hiding in it;
  `exchange_calendars` is the library that owns it.
- **Factor Analysis** — where `alphalens` and friends are indexed.
- **Portfolio Optimization & Risk Analysis** — `PyPortfolioOpt`, `Riskfolio-Lib`,
  `pyfolio`.
- **Prediction Markets** — the `strat-data` skill already names Kalshi and
  Polymarket; this is where the client libraries are indexed.
- **Time Series Analysis** — `arch` for the volatility work in RMX-C's
  (off-by-default) vol-scaling.

**The boundary, stated plainly:** inclusion on an awesome-list is a popularity
signal, not a validation. Several categories on that list — technical
indicators most of all — index tooling for a family this repo has already
audited and rejected (see [AGENT-SPEC-VERDICT.md](AGENT-SPEC-VERDICT.md): build
nothing from Modules 1–5, and §4 of the external research: no evidence base
exists for per-ticker threshold indicators in any survivorship-free panel).
A better MACD library does not make MACD work.

---

## 3. Financial-Models-Numerical-Methods — read §5 and §6, ignore the rest

7.2k stars, AGPL-3.0, 21 notebooks + appendices, last pushed 2024-10-22.
Graduate-level derivatives pricing, and the best-organised free treatment of it.

The catalogue:

| section | contents |
|---|---|
| 1.x | Black-Scholes numerics, SDE simulation, Fourier methods, Heston, Lévy processes |
| 2.x | BS PDE and sparse matrices, exotic options, American options |
| 3.x | Merton jump-diffusion, Variance Gamma, NIG — all PIDE |
| 4.x | pricing with transaction costs, volatility smile calibration |
| **5.x** | **Kalman filter linear regression, AR(1) autocorrelation tracking, volatility tracking** |
| **6.1** | **Ornstein-Uhlenbeck process and applications** |
| 7.1 | classical mean-variance optimisation |

Sections 1–4 price options. This engine does not trade options, and the options
overlay in the agent spec is one of the things
[AGENT-SPEC-VERDICT.md](AGENT-SPEC-VERDICT.md) says to build nothing from.
Skip them.

**§5.1–5.3 and §6.1 are the exception.** They are the cleanest free write-ups of
state-space volatility estimation and mean-reversion fitting, and both connect
to live questions:

- `5.3 Volatility tracking` — the vol-scaling in `alpha/blend.py` uses trailing
  6-month realised vol per Barroso–Santa-Clara. This is the more careful family
  of estimators. **But note the evidence asymmetry:** Barroso's specific
  trailing-realised rule is what replicates; a fancier filter is a *new*
  specification with no published record, and swapping it in would be exactly
  the kind of unbacked refinement the research rules forbid.
- `6.1 Ornstein-Uhlenbeck` — the correct model for the mean-reversion claim
  behind the recast reversal sleeve, which was measured and then **dropped**
  from the default blend (retains only 0.37 of its return ex-micro, doesn't
  cover its turnover). Read it to understand what was dropped, not to revive it.

**AGPL-3.0 is a hard blocker on copying.** Reimplement from the mathematics or
from a permissively-licensed source; do not paste.

---

## 4. Computational-Finance-Course — park it

806 stars, BSD-3-Clause, 14 lectures plus a Q&A directory, last pushed
2024-03-01. Companion code to Oosterlee & Grzelak, *Mathematical Modeling and
Computation in Finance* (World Scientific, 2019), with YouTube lectures.

Lectures 01–14: asset classes → stocks/options/stochastics → simulation →
implied vol → jump processes → affine jump diffusion → stochastic vol → Fourier
pricing → Monte Carlo → Heston MC → hedging and MC sensitivities → forward-start
and Bates → exotics → summary.

**Verdict: nothing here maps to a cross-sectional equity book.** It is a better
*course* than cantaro86's notebooks (structured, exercises, permissive licence)
and a worse *reference* (less breadth, no Kalman/OU sections). Its entire
subject is the derivatives pricing this engine does not do.

Recorded here rather than dropped for two reasons: (a) BSD-3 means if an
options overlay is ever built despite the verdict, this is the licence-clean
source to build from; (b) Lecture 11 (hedging and Monte Carlo sensitivities) is
the reference if Greeks ever become relevant. Neither is a current need.

---

## 5. Quant-Guild-Library — read for intuition, cite for nothing

1.3k stars, ~192 commits, `2025 Video Lectures/` and `2026 Video Lectures/`,
last pushed 2026-07-29. Companion notebooks to a YouTube lecture series by Roman
Paolucci (~136 lectures; most recent at time of writing: "How Volatility Drag
Destroys (and Creates) Wealth"). Topics: stochastic calculus, options pricing,
Black-Scholes, portfolio optimisation, volatility trading, algorithmic trading,
Monte Carlo, Markov chains, time series, IB integration.

**Evidence weight: lowest of the five, and it is not close.** It is a
video-lecture companion — not peer-reviewed, not a replication archive, not a
published-record panel. It is the exact category of source the research rules
exist to keep out of a performance claim. Under Chen & Welch, an
attractive-looking strategy presented without a survivorship-free out-of-sample
record is prior-odds a coin flip regardless of how well it is explained.

Where it is genuinely useful: **volatility drag / geometric-vs-arithmetic return
intuition**, which is the mechanism behind why vol-scaling helps a fat-tailed
sleeve, and which this repo currently asserts by citation rather than derives.
Good for building the intuition; useless as a source.

**No license file — default all-rights-reserved.** Read only; do not copy code.

---

## What none of these change

Stated explicitly, because a pile of 57k combined stars creates pressure to act:

1. **Both local holdouts are still spent.** No notebook in any of these repos
   creates new out-of-sample sample.
2. **Hard rule #2 stands.** No performance number may come from anywhere but
   `rhengine/alpha/book.py`. A number printed in a textbook notebook is a
   teaching output.
3. **Hard rule #1 stands.** None of this is a reason to touch
   `rhengine/indicators.py`. The golden vectors guard the Nydex JS port, and
   §5.1 of the external research already recommends freezing it.
4. **The category verdict stands.** Do not screen new price signals on the
   S&P 500 panel because a repo made it easy to. The population median is
   7 bp/month at t = 0.45; ease of implementation does not move the prior.

The one place these repos *legitimately* unblock work is the cost side —
`18_transaction_costs/02_spread_estimation` is a route to putting a measured
number where an assumed 30 bps sits, and rule #3 explicitly invites evidence
there. That is the highest-value single thread in this document.

---

## Part 2 — the published record

Where Part 1 is technique, this is evidence. Under
[CLAUDE.md](../CLAUDE.md) hard rule 4 these four archives are the **only**
admissible support for a new claim, because both local holdouts are spent.

`research_data/external/` is **gitignored**. Rebuild the whole thing with:

```bash
py scripts/fetch_external_factors.py
```

Each directory carries a `_manifest.json` recording source URL, byte count,
fetch timestamp and a truncated SHA-256 per file. That manifest is the
provenance record — a number that cannot be traced to a file in one of them is
not evidence. All four were fetched **2026-07-25**.

Query them through `rhengine/alpha/external.py`, never by reading the archives
directly:

```bash
py -m rhengine.alpha.external --catalog --min-t 2
py -m rhengine.alpha.external --decay --top 30        # is this signal dead in my era?
py -m rhengine.alpha.external --corr osap:Mom12m jkp:qmj aqr:BAB_US
py -m rhengine.alpha.external --long-only X --vs GP Mom12m AssetGrowth ShareIss5Y
```

### The four archives

| Dir | Source | Contents | Licence |
|---|---|---|---|
| `french/` | [Ken French data library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html) | 5-factor 2x3, momentum, ST/LT reversal, 25 OP×INV portfolios (6 files) | public |
| `osap/` | [openassetpricing.com](https://www.openassetpricing.com/) 2.0.0 (2025-10) | 212 published predictors: `SignalDoc.csv`, monthly long-short record, VW quintiles, ex-micro alt ports (5 files) | CC-BY — **cite Chen & Zimmermann** |
| `jkp/` | [jkpfactors.com](https://jkpfactors.com/) (through 2025-12) | 153 factors in 13 themes, VW-cap and EW, plus NYSE size cutoffs (5 files) | **CC BY-NC 4.0 — non-commercial only** |
| `aqr/` | [AQR data sets](https://www.aqr.com/Insights/Datasets) | QMJ, BAB, TSMOM, HML-Devil monthly; US series start 1926–1957 by factor (5 files) | see AQR terms |

Papers behind them: Fama & French; Chen & Zimmermann, *Critical Finance Review*
2022 (OSAP); Jensen, Kelly & Pedersen, *Journal of Finance* 2023 (JKP); Asness,
Frazzini & Pedersen (QMJ), Frazzini & Pedersen (BAB), Moskowitz, Ooi & Pedersen
(TSMOM).

**The JKP licence is a real constraint, not boilerplate.** CC BY-NC forbids
commercial use, and this engine ships to two applications. A JKP-derived number
reaching a shipped surface is a licence problem — use JKP for research
decisions, and re-establish anything customer-facing from French or OSAP. Note
this is a *stricter* constraint than Part 1's AGPL problem: that one blocks
copying code, this one blocks shipping a number.

### What each archive can and cannot settle

The part that decides whether a question is answerable at all.

| Question | Ask | Do **not** ask |
|---|---|---|
| Is the RMX-B blend real over the long run? | `french/` — 1963–2026, survivorship-free, the source of the +1.78%/yr, t=+2.54 headline | anything local; 8 years cannot see a 39-year t=2 |
| Is this signal dead in my era? | `osap/` `--decay` — 212 predictors with pre/post-publication splits | `french/`, which has only the canonical factors |
| Would this sleeve duplicate one I have? | `jkp/` themes — the 13 themes are the correlation map behind "no two sleeves share a theme" | in-sample ablation, which may confirm but never select |
| Does a candidate leg survive long-only? | `osap/` `--long-only` — the gap to long-short is 2–20× | the long-short headline, ever (hard rule 5) |
| Is the premium a micro-cap artefact? | `osap/` `PredictorAltPorts_ex_micro` — this is what killed the reversal sleeve | the full-sample port |

**The standing prior, from `--decay`:** published large-cap anomalies earn about
**7 bp/month post-2005, median |t| 0.45**. That is the number behind Part 1's
closing warning — ease of implementation does not move a prior that low.

### Derived artifacts (also gitignored)

Rebuilt from the above, sitting alongside it: `research_data/*.csv` (French
portfolio extracts), `_panel_cache.pkl`, `_coverage.json`, `splits.json.gz`, and
the `rmx*_is/oos.json` result files. Regenerate via `py -m rhengine.alpha.panel`
and the `scripts/fetch_*.py` set.

---

## Part 3 — the Python dependencies

Three tiers in [pyproject.toml](../pyproject.toml). The split exists because
`rhengine` is **shipped to RobinhoodAgent and Nydex**, and neither should
inherit a scientific stack in order to score six strategies.

| Tier | Install | Adds | For |
|---|---|---|---|
| core | `pip install -e .` | `requests` only | the shipped engine — scoring, regime, market-data adapters |
| sim | `pip install -e .[sim]` | numpy, pandas, matplotlib | backtest, montecarlo, ic, crossval, sanity, viz |
| analysis | `pip install -e .[analysis]` | + scipy, vectorbt, nbformat, nbclient, ipykernel | `ablate_vbt`, `mc_refined`, the notebooks |

### Which tier a module actually needs

| Package | Imported by |
|---|---|
| `requests` | every `market_data_*.py`, `data_local`, `universe_hygiene`, `fetch_external_factors` |
| `numpy` / `pandas` | all of `alpha/`, plus `ic`, `mc_refined`, `ablate_vbt`, most of `scripts/` |
| `scipy` | `alpha/stats.py` only (+ three `scripts/`) |
| `vectorbt` | `ablate_vbt.py`, `mc_refined.py` |
| `matplotlib` | `viz.py` |

Two modules added 2026-07-30 are deliberately **stdlib-only** and sit in the
core tier: `execution.py` (the spread gate) and `market_data_options.py`, whose
Black-Scholes inversion uses `math.erf` rather than `scipy.stats.norm` for
exactly this reason.

### Known gap — the `sim` tier cannot run the alpha stack

`rhengine/alpha/stats.py` line 24 does an unguarded
`from scipy import stats as sps`, and it is imported by `blend`, `external`,
`rmx`, `rmxb`, `rmxd`, `rmxq` and `validation` — essentially every research CLI
in `alpha/`. **scipy is declared only in the `analysis` extra.** So a clean
`pip install -e .[sim]` produces an environment where

```bash
py -m rhengine.alpha.rmxb --lo 2010-07-01 --hi 2018-12-31
```

fails on ImportError. The `sim` extra's own comment lists "backtest, montecarlo,
ic, crossval, sanity, viz" and predates the `alpha/` stack, which was never
added to any tier.

It has gone unnoticed because the working environment here has the full set
installed. Two ways to close it, **neither chosen**:

1. move `scipy` into `sim` — one line, and defensible, since the alpha stack is
   the only place performance claims may come from; or
2. add a fourth `research` extra and document `alpha/` as needing it.

Nothing is changed here on that: it affects what the two consuming apps install,
and that decision belongs to whoever owns those installs.

### Environment notes

Live in [CLAUDE.md](../CLAUDE.md) and are not repeated beyond the two that bite
most often: Python is `py` (never `python`/`python3`), and after editing
`rhengine/` a notebook needs **Restart Kernel** — Run All keeps the old module.

---

## Sources

**Part 2 — the published record.** Provenance for every file is in the
per-directory `_manifest.json` (source URL, bytes, SHA-256, fetch timestamp);
these are the project pages.

- [Ken French data library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)
- [Open Source Asset Pricing](https://www.openassetpricing.com/) — Chen & Zimmermann, *Critical Finance Review* 2022
- [JKP factors](https://jkpfactors.com/) — Jensen, Kelly & Pedersen, *Journal of Finance* 2023 · CC BY-NC 4.0
- [AQR data sets](https://www.aqr.com/Insights/Datasets)

**Part 1 — external repos.**

- [stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading) — MIT (code), © 2026 Stefan Jansen (book) · [alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded) · [zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) · [pyfolio-reloaded](https://github.com/stefan-jansen/pyfolio-reloaded)
- [wilsonfreitas/awesome-quant](https://github.com/wilsonfreitas/awesome-quant)
- [cantaro86/Financial-Models-Numerical-Methods](https://github.com/cantaro86/Financial-Models-Numerical-Methods) — AGPL-3.0
- [LechGrzelak/Computational-Finance-Course](https://github.com/LechGrzelak/Computational-Finance-Course) — BSD-3-Clause · Oosterlee & Grzelak, *Mathematical Modeling and Computation in Finance* (World Scientific, 2019)
- [romanmichaelpaolucci/Quant-Guild-Library](https://github.com/romanmichaelpaolucci/Quant-Guild-Library)
