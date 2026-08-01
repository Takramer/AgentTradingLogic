# CLAUDE.md — operating guide for this repo

Quant research + signal engine. Two systems live here, and they are **not** the
same code path:

| | legacy engine | research stack |
|---|---|---|
| code | `rhengine/*.py` | `rhengine/alpha/*.py` |
| unit | per-trade R-multiples | daily mark-to-market equity |
| universe | live/whole-market scan | point-in-time S&P 500 |
| status | shipped to two apps, **numbers not re-established after the 2026-07-24 audit** | the only place performance claims may come from |

The engine is shared by **RobinhoodAgent** (autonomous Python) and **Nydex**
(React). Engine-only: no order planning, no orchestration, no ML.

**Start here:** [docs/RMX-RESULTS.md](docs/RMX-RESULTS.md) (the answer) →
[docs/RMX-C.md](docs/RMX-C.md) (current work) →
[docs/AUDIT-2026-07-24.md](docs/AUDIT-2026-07-24.md) (why everything older was thrown out).

## Supporting guides

| File | Read it when |
|---|---|
| [docs/CLAUDE-RESEARCH-RULES.md](docs/CLAUDE-RESEARCH-RULES.md) | You are about to report a number, add a signal, or claim an edge |
| [docs/CLAUDE-CODEMAP.md](docs/CLAUDE-CODEMAP.md) | You need to find which module owns a thing |
| [docs/CLAUDE-WORKFLOWS.md](docs/CLAUDE-WORKFLOWS.md) | You are running a refinement, adding a sleeve, or plugging in data |
| [docs/CLAUDE-PITFALLS.md](docs/CLAUDE-PITFALLS.md) | Anything looks surprisingly good, or a cap/price/count looks wrong |
| [docs/GOLIVE-RUNBOOK.md](docs/GOLIVE-RUNBOOK.md) | **Anything touching real money.** The Monday procedure, the one edit that arms a book, what that edit overrides, the four executor obligations, and the kill switches |
| [docs/AGENT-SPEC-VERDICT.md](docs/AGENT-SPEC-VERDICT.md) | **Before** building any technical/tactical rule — the external evidence audit. Short version: build nothing from Modules 1–5 |
| [docs/AGENT-SPEC.md](docs/AGENT-SPEC.md) | You need the exact parameter for a technical/tactical rule (stages, RRG, breakouts, ATR stops, options overlay, execution gates), its range, and whether the sources actually specify it |
| [docs/OPTIONS-ACCUMULATOR.md](docs/OPTIONS-ACCUMULATOR.md) | You are running or resuming the daily options capture, or about to touch `options_registry.py` / `options_costs.py` |
| [docs/VIDEO-INTAKE-2026-07-31.md](docs/VIDEO-INTAKE-2026-07-31.md) | Someone sends a video/course. 20 already triaged; **read §Cluster C before any options or 0DTE work** — the data blockers and the BXM prior are there |
| [docs/REFERENCE-LIBRARIES.md](docs/REFERENCE-LIBRARIES.md) | Anything the repo leans on from outside itself. Part 1: public quant repos for technique + licence terms before you copy (**not evidence**). Part 2: `research_data/external/` — which archive settles which question, and which cannot. Part 3: the core/sim/analysis dependency tiers |

## Environment

- Python is `py`, never `python` or `python3`. Run from the repo root.
- Windows. Notebooks headless need `$env:PYTHONUTF8='1'`.
- `tzdata` is required and installed. Stooq is blocked; Yahoo works.
- Tests: `py -m unittest discover tests` (228 passing, 2026-07-31) or `py -m pytest tests/test_sleeves.py -q`.
- After editing `rhengine/`, **Restart Kernel** in a notebook — Run All keeps the old module.

## Hard rules

1. **Do not touch `rhengine/indicators.py`** unless explicitly asked. `fixtures/`
   golden vectors guard cross-runtime drift against the Nydex JS port. Refinements
   go in `config.json` (legMask, exits, universe) and `rhengine/strategies.json`
   (threshold default/min/max/step) — never in the scoring math.
2. **Never quote a performance number that did not come out of
   `rhengine/alpha/book.py`.** R-sums are not returns.
3. **Costs resolve from `config.json` → `costs.round_trip_bps` (30 bps)** via
   `rhengine/costs.py`. That is the single source of truth; `--cost-bps` overrides.
   Do not lower it without executed-fill evidence.
4. **Both local holdouts are spent** (2019–2026 on RMX-D and RMX-Q). No new
   claim may rest on them. Evidence comes from the published record
   (`research_data/external/`, Ken French, OSAP/JKP) — see the research rules.
5. **Screen sleeve candidates long-only** (`--long-only`), never long-short. The
   book cannot short; long-short records overstate by 2–20×.
6. **Autonomy is earned.** `strategies.json` `status` has four tiers and defaults
   to `manual`:
   | tier | who runs it |
   |---|---|
   | `autonomous` | the unattended RobinhoodAgent auto-routes to it |
   | `user_configurable` | Nydex ships it for a *user* to configure and leave running — **confers no agent-routing permission** |
   | `manual` | `--strategy` in the research CLIs, attended only |
   | `retired` | kept so old results reproduce; hidden from `scan --all` |

   `select_strategy_for_regime` returns `None` when no cleared strategy exists,
   and **None means trade nothing** — never fall back to another sleeve.
   Demoting to `manual` is sufficient to pull a strategy out of unattended
   trading. Explicit `--strategy` bypasses all of this (the manual path).

   The same four tiers govern the research-stack **books** (`strategies.json`
   → `books`, added 2026-08-01), enforced in `alpha/live.py::emit` rather than
   in `indicators.py`: a book that is not exactly `"autonomous"` emits with
   `orders_permitted: false`. Arming one writes the override it is running
   against into every payload. See the runbook.
7. Log any W2/W3 holdout look in [docs/HOLDOUT.md](docs/HOLDOUT.md) **before**
   executing it, with the config hash.

## The shipped strategy, in one block

**RMX-B** (`rhengine/alpha/rmxb.py`) — 50/50 cap-weighted blend, ~96 names,
~190%/yr one-way turnover:

- **quality leg** — top OP/BE quintile of the PIT S&P 500, re-formed each June, held 12 months
- **momentum leg** — top 12-1 return quintile, re-formed monthly
- 10% name cap, executed at the close of the session *after* the decision

Evidence: Ken French portfolios 1963–2026, survivorship-free — 12.55%/yr vs the
market's 10.90%, alpha +1.78%/yr **t = +2.54**, Sharpe 0.55 vs 0.47, beta 0.97,
4/4 eras. The legs correlate only +0.18, which is why the blend beats both.

**The boundary, always state it:** +1.78%/yr against 5.5%/yr tracking error needs
~39 years to reach t=2. This project's 8-year S&P 500 panel can check the
implementation for *consistency*, never for *significance*. Expect to trail the
index ~4 years in 10.

**RMX-C** (`alpha/sleeves.py` + `alpha/blend.py`) is the live extension: 5 blocks
(quality, momentum, investment, issuance, payout). `--band` and
`--momentum-calendar quarterly` are implemented, measured, and **off by default**.

**Vol-scaling is off in RMX-B and ON in RMX-C** — the two modules differ, and
this line used to claim otherwise. `rmxb.py --vol-scale-momentum` is a flag you
must pass; `blend.py --vol-scale` defaults to `["momentum"]`. Every RMX-C figure
in `docs/RMX-C.md` was produced with it on (verified 2026-08-01: the documented
13.15% / Sharpe 0.86 / −21.19% row reproduces exactly with it, and gives 13.03%
without). Pass a bare `--vol-scale` to turn it off.

## Common commands

```bash
# research stack
py -m rhengine.alpha.panel                       # PIT coverage + universe check
py -m rhengine.alpha.rmxb --lo 2010-07-01 --hi 2018-12-31
py -m rhengine.alpha.rmxb --holdings             # today's book

# the live handoff — target weights + deltas for an executor. Refuses on a
# stale panel; refuses to mark a book executable unless status == autonomous.
py -m rhengine.alpha.live --book rmxb --out book.json
py -m rhengine.alpha.live --book rmxb --positions positions.json --out book.json
py -m rhengine.alpha.blend --ablate              # leave-one-out (confirm only, never select)
py -m rhengine.alpha.external --decay            # is this signal dead in my era?
py -m rhengine.alpha.external --long-only X --vs GP Mom12m AssetGrowth ShareIss5Y

# legacy engine (always with resolved costs; cap the universe while iterating)
py -m rhengine.backtest --lookback-days 400 --csv backtest_trades.csv
py -m rhengine.sanity --trades-csv backtest_trades.csv --strict
py -m rhengine.ic --strategy residualmomentum --max-tickers 300

# options — measurement only, no strategy. Run the capture DAILY, mid-session.
py -m rhengine.options_registry plan --symbol SPY --spot <spot>
py -m rhengine.options_registry ingest capture.json   # idempotent
py -m rhengine.options_registry status
py -m rhengine.options_costs                          # spread/premium, spread/delta, theta
```

## Skills

`.claude/skills/` — `strat-refine` (cut/add/alter a rule through the validation
gates), `strat-data` (swap data source / universe / CSVs and regenerate
artifacts), `i-have-adhd` (output shaping — applies to every reply).

## Writing style for this repo

Docs here record **rejections as carefully as results**, keep the reasoning
error visible rather than deleting it, and state the boundary of a claim in the
same breath as the claim. Match that. Never present an in-sample number without
its out-of-sample fate, and never round a t-stat up.
