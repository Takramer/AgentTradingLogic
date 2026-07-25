# AgentTradingLogic — shared trading engine

> ### Status, 2026-07-25 — read `docs/RMX-RESULTS.md` before trusting any number below
>
> An adversarial audit (`docs/AUDIT-2026-07-24.md`) found the legacy sleeve
> engine's headline result to be an artifact of zero costs, an uncapitalised
> R-sum, IID statistics on overlapping trades, and a survivorship-biased
> universe chosen by looking at the metric being reported. **None of the numbers
> in the "Simulation & backtesting" sections below have been re-established.**
>
> `rhengine/alpha/` is the rebuilt research stack: point-in-time universe,
> mark-to-market book, real cost curves, Newey-West statistics, benchmark alpha,
> block bootstrap, and pre-registered holdouts. Five hypotheses were tested
> against it and four were rejected.
>
> **The survivor is RMX-B** (`rhengine/alpha/rmxb.py`) — a 50/50 blend of two
> cap-weighted large-cap sleeves: operating profitability and 12-1 momentum. On
> Ken French's survivorship-free portfolios, 1963–2026, net of a punitive cost
> charge, it returns **12.55%/yr against the market's 10.90%**, alpha
> **+1.78%/yr with t=+2.54**, Sharpe 0.55 vs 0.47, beta 0.97, and beats the
> market in **all four eras**. The blend's t-stat exceeds both of its legs
> (+1.62 and +2.10) because their excess returns correlate only +0.18.
>
> **The boundary, stated up front:** a +1.78%/yr alpha against a 5.5%/yr
> tracking error needs ~39 years to reach t=2, so this project's own 8-year S&P
> 500 implementation can only be checked for consistency, never for
> significance. The demonstration is French's 63 years. Expect to trail the
> index in roughly 4 years out of 10.

Single source of truth for the 6-strategy signal engine shared by
**RobinhoodAgent** (autonomous Python app) and **Nydex** (client-side React app).
Engine-only: no order planning, no autonomous orchestration, no meta-labeling ML —
those stay app-side.

## The post-audit research stack (`rhengine/alpha/`)

| Path | What |
|---|---|
| `rhengine/alpha/panel.py` | **Point-in-time** panel: daily S&P 500 membership 1996→2026 reconstructed from component-change history, joined on date to Yahoo bars and Ken French factors. `eligible(D)` is provably invariant to appending future data (audit FC-4) |
| `rhengine/alpha/book.py` | The **only** source of performance numbers: weight targets → daily mark-to-market equity, one-session execution lag, turnover-based cost, −30% delisting return, charged margin above 1.0× (fixes FC-1, FC-13) |
| `rhengine/alpha/stats.py` | Newey-West alpha/beta on non-overlapping monthly returns, Deflated Sharpe at the true trial count (fixes FC-3, FC-6, FC-14) |
| `rhengine/alpha/validation.py` | Stationary block bootstrap, random-selection and random-timing nulls, purged folds, bear-episode table, drop-best-months (fixes FC-5, FC-7, FC-10) |
| `rhengine/alpha/signals.py` | Residual momentum at the horizon its citation actually uses — months, not days (fixes FC-8) |
| `rhengine/alpha/timing.py` | RMX-D: 10-month trend gate + absolute-momentum risk-off leg |
| `rhengine/alpha/rmxd.py` | RMX-D, frozen. **Rejected out-of-sample (1/11 gates)** |
| `rhengine/alpha/fundamentals.py` | Point-in-time SEC XBRL accounting data with the Fama-French 6-month publication lag (audit O-4) |
| `rhengine/alpha/rmxq.py` | The **quality leg**: operating-profitability tilt, cap- or equal-weighted. Also `adjusted_shares`, which reconciles split-adjusted prices against un-adjusted SEC share counts |
| `rhengine/alpha/rmxb.py` | **RMX-B** — the deliverable: 50/50 quality + 12-1 momentum, cap-weighted, alpha t=+2.54 over 1963-2026 |
| `docs/RMX-PREREGISTRATION.md` | Every hypothesis, parameter and kill criterion, written before its run; the trial ledger the audit demanded |
| `docs/RMX-RESULTS.md` | **The answer**, with the numbers that decided each verdict |

```bash
py scripts/fetch_history.py            # 1,213 ever-S&P500 tickers, full history
py scripts/fetch_sec_fundamentals.py   # SEC XBRL fundamentals, point-in-time
py -m rhengine.alpha.panel             # coverage by year + PIT universe check
py scripts/rmx_bias.py                 # survivorship inflation, measured vs RSP
py scripts/rmxq_report.py --lo 2010-07-01 --hi 2018-12-31   # full gate report
py scripts/ff_largecap_quality.py      # the 63-year quality-leg evidence
py scripts/ff_blend.py                 # the blend: alpha t=+2.54, 4/4 eras
py -m rhengine.alpha.rmxb              # RMX-B backtest
py -m rhengine.alpha.rmxb --holdings   # the book as of today
py scripts/rmxqt_power.py              # why no 8-year test can prove it
```

## Contents

| Path | What |
|---|---|
| `rhengine/indicators.py` | Pure engine: `calc_*`, `score_*` (vwap, bollinger, macd, consolidation, residualmomentum, pead), regime/VIX classifiers, `score_ticker` / `score_all_tickers` |
| `rhengine/market_data.py` | Data adapters: Alpaca IEX + Massive/Polygon grouped-daily (real SIP volume, disk-cached). Resolves `secrets.json` / `config.json` / `massive_cache` from the consuming app's cwd |
| `rhengine/backtest.py` | Walk-forward backtest engine (`python3 -m rhengine.backtest` from the app dir) |
| `rhengine/data_local.py` | **No-key data layer**: Yahoo daily bars (disk-cached in `yahoo_cache/`), OHLCV CSV loader, seeded synthetic universe for offline tests |
| `rhengine/market_data_kalshi.py` | Kalshi adapter: public exchange-status gate (`kalshi_is_tradable`), market discovery, daily implied-probability bars from candlesticks (cached in `kalshi_cache/`), `score_prob_shift` external leg. `KalshiPerpsClient` wraps the separate `/margin/*` surface (order routing stubbed — no funding/liquidation model yet) |
| `rhengine/market_data_polymarket.py` | Polymarket adapter, split per Polymarket's own APIs: Gamma (discovery), Data API (positions/trades/holders), CLOB prices-history → daily probability bars (cached in `polymarket_cache/`), `score_implied_prob` external leg. CLOB trading + perps are gated stubs |
| `rhengine/market_data_fred.py` | FRED macro adapter: T10Y2Y / HY OAS / real VIX close, cached in `fred_cache/`; `macro_flags` portfolio-heat overlay (app-side, conventional thresholds) |
| `rhengine/market_data_av.py` | AlphaVantage earnings-surprise adapter (real SUE feed — the PEAD upgrade path): cached in `av_cache/`, budget-aware batch (free tier = 25 req/day), `sue_gate` external leg |
| `rhengine/gates.py` | External-gate seam: `GateBook` annotates every backtest trade with `sue`/`sue_pct`/`macro_risk_on` columns (slice post-hoc, ablation-style) or hard-filters in live-representative mode; `py -m rhengine.gates backfill` tops up the AV earnings cache inside the daily budget |
| `rhengine/portfolio.py` | Account-level equity simulation: replays backtest trades with fixed-fractional sizing, position caps, per-day entry limits → equity curve, CAGR, max DD, Sharpe |
| `rhengine/montecarlo.py` | Bootstrap Monte Carlo over trade R-multiples → outcome/drawdown distributions, P(loss), P(ruin) |
| `rhengine/optimize.py` | Threshold sweeps (OAT / grid) with in-sample/out-of-sample splits, plus expanding-window walk-forward fold evaluation |
| `rhengine/ablate_vbt.py` | VectorBT full-matrix rule ablation: every combination of each strategy's legs re-derived as vectorized signals and backtested, ranked by profit factor (`py -m rhengine.ablate_vbt`) |
| `rhengine/ic.py` | Cross-sectional **Information Coefficient** + t-stats: does a higher score predict a better outcome among each day's candidates? Rank-IC per horizon (SPY-excess & raw), overlap-adjusted t, IC IR, score-tier monotonicity (`py -m rhengine.ic`) |
| `rhengine/crossval.py` | Blocked time-series **cross-validation** with purge/embargo: per-block fold stability + leave-one-block-out selection stability. Answers "is the edge everywhere or one lucky quarter?" (`py -m rhengine.crossval`) |
| `rhengine/sanity.py` | Ten-check **sanity battery** on a trade list (sample size, t-stat, outlier/cost/concentration/degenerate-R/monotonicity/exit-mix). PASS/WARN/FAIL with a nonzero exit code for CI/agent gating (`py -m rhengine.sanity --trades-csv …`) |
| `rhengine/nulltest.py` | Random-entry permutation test: does the ENTRY signal beat luck, or is the edge just exit engine + drift? (`py -m rhengine.nulltest`) |
| `rhengine/mc_refined.py` | Trade harvest + vectorized Monte Carlo for the ablation-refined strategy set (full path matrix for fan charts) |
| `rhengine/charts.py` | Dependency-free ASCII charts (equity curve, drawdown, histograms) — auto-shown by portfolio/montecarlo, or standalone: `py -m rhengine.charts --equity-csv equity_curve.csv --trades-csv backtest_trades.csv` |
| `rhengine/viz.py` | Matplotlib chart helpers (fan/hist/IC series/signed & paired bars/sweep/IS-OOS scatter/heatmap/equity-drawdown/check-table) shared by the notebooks — one validated palette so every lab reads as one system |
| `notebooks/` | Five validation labs (below): open in Jupyter to *see* what the CLIs print — distributions, fans, per-fold bars |
| `rhengine/strategies.json` | **Canonical spec** — per-strategy legs, threshold defaults/ranges, exits, regime routing. Threshold defaults are the walk-forward-tuned values |
| `fixtures/` | Golden vectors: deterministic bar series + expected `score_ticker` output. The cross-runtime drift guard |
| `gen_fixtures.py` | Regenerates the golden vectors (only after a deliberate engine change) |

## Consuming

Python (RobinhoodAgent): git submodule at `./engine`, then `pip install -e ./engine`;
`from rhengine.indicators import score_ticker`. Thresholds resolve as spec defaults
overlaid with app-config overrides (`resolve_thresholds`).

JS (Nydex): import the spec — `import spec from '../engine/rhengine/strategies.json'` —
and test `indicators.js` against `fixtures/` with vitest (sync pass deferred).

## Simulation & backtesting (standalone, no API keys)

The repo ships its own `config.json` with `data_source: "massive"` and
`watchlist: "all"` so every tool below runs from this directory against the
**entire US market** (~7k tradeable tickers after `universe_filters` floors;
zero the floors to scan literally everything) served instantly from the local
whole-market cache. Build/extend the cache once with
`py -m rhengine.market_data backfill 730` (Polygon free tier: 5 req/min,
~2 years max history). Alternatives: `yahoo` (keyless, per-symbol — use an
explicit watchlist, not "all") or `alpaca` (instant but IEX-only volume,
~2-3% of consolidated — weakens the volume legs).

```bash
# 1. Signal-level backtest (R-multiples, by score tier / strategy / exit)
py -m rhengine.backtest --lookback-days 400 --cost-bps 5 --csv backtest_trades.csv

# 2. Account-level simulation (sizing + capacity constraints -> equity curve)
py -m rhengine.portfolio --risk-pct 1 --max-positions 5 --equity-csv equity_curve.csv

# 3. Risk distribution (bootstrap the trade list)
py -m rhengine.montecarlo --trades-csv backtest_trades.csv --risk-pct 1

# 4. Threshold tuning with overfit guards
py -m rhengine.optimize --strategy residualmomentum                  # OAT, IS/OOS split
py -m rhengine.optimize --strategy bollinger --params volMult,adxMax --grid
py -m rhengine.optimize --strategy residualmomentum --walk-forward 4 # honest OOS
```

Offline paths for experiments without any network: `data_local.synthetic_universe()`
(seeded random walks) and `data_local.load_csv_dir()` (your own OHLCV CSVs) both
produce the exact bar shape `run_backtest_on` consumes.

### Deeper validation — is the edge real, or overfit / luck?

Five more tools stress a configuration before it touches the live account. Each
prints a verdict line and (via the notebooks) charts the distribution behind it.
Always `--cost-bps 5`; cap the universe with `--max-tickers` while iterating.

```bash
# Ablation — which legs carry the edge, which are drag (cut candidates)?
py -m rhengine.ablate --strategy residualmomentum --cost-bps 5
py -m rhengine.ablate --strategy bollinger --regime choppy   # routed → ablate in-slot

# Information Coefficient + t-stats — does a higher score rank a better outcome?
py -m rhengine.ic --strategy residualmomentum --max-tickers 300

# Blocked cross-validation — edge everywhere, or one lucky quarter?
py -m rhengine.crossval --strategy bollinger --folds 5
py -m rhengine.crossval --strategy bollinger --by-regime   # macro-defense: which regime carries it?
py -m rhengine.crossval --strategy bollinger --select --params volMult --folds 4

# Sanity battery — ten mechanical lie-detectors (nonzero exit code gates CI)
py -m rhengine.sanity --trades-csv backtest_trades.csv --strict

# Random-entry null — does the ENTRY beat luck, or is it exit engine + drift?
py -m rhengine.nulltest --strategy bollinger --max-tickers 300
```

**Notebooks** (`notebooks/`, ASCII-only so headless execute is safe): `ablation`,
`walkforward` (IS/OOS + forward-walk progressive tuning), `ic_tstats`,
`crossval_sanity`, `montecarlo_refined`. Each caches its trades CSV under
`tests/results/` and only recomputes when its `REFRESH` flag is `True`; all draw
through `rhengine/viz.py`. Run one headless (UTF-8 guard per the Windows notes):

```powershell
$env:PYTHONUTF8='1'; py -m jupyter execute --inplace notebooks/ablation.ipynb
```

**Agent skills** (`.claude/skills/`): `strat-refine` drives the token-frugal
cut/add/alter refinement loop with the promotion bars above; `strat-data` plugs
new data (source/universe/CSV) into the pipeline and regenerates the artifacts
the notebooks consume.

### External gates (macro + real earnings surprise)

`config.json`'s `external_gates` block (see the shipped config) turns on
annotation columns for every trade `backtest.py` records — no separate run
needed, ablate post-hoc:

```bash
# top up the AlphaVantage earnings cache for the watchlist (25 req/day free tier)
py -m rhengine.gates backfill --max-requests 20

py -m rhengine.backtest --strategy pead --csv pead_trades.csv
# pead_trades.csv now has sue / sue_pct / macro_risk_on columns —
# slice with pandas/ablate.py to compare sue=pass vs fail vs na before
# flipping either gate's "mode" to "filter" (which drops non-passing trades
# at record time — the live-representative setting).
```

`sue` is `"na"` for any ticker without a cached AlphaVantage report — backfill
before trusting a `pass`/`fail` split. `macro_risk_on` is `""` when a FRED
series is missing or its last observation is >14 days stale at signal time;
treat that as unknown, not as risk-on.

#### Backfilling the earnings cache (the SUE gate's long pole)

AlphaVantage's free tier is **25 requests/day**, and PEAD fires uniformly
across the market (~4 earnings gaps/yr/ticker), so there is no Pareto
shortcut — the cache fills over weeks, not minutes. `rhengine/backfill.py` is
a resumable runner that targets the **tickers PEAD actually trades**
(auto-discovered from a trade CSV), ranked **most-liquid-first** so partial
coverage always covers the names you'd trade:

```bash
py -m rhengine.backfill --once --max-requests 20   # spend today's budget, exit
py -m rhengine.backfill --loop                     # always-on: run daily forever
py -m rhengine.backfill --from-csv tests/results/refined_trades.csv --strategy pead
py -m rhengine.backfill --liquid-universe --target-size 300   # ignore CSV, pure liquidity
```

Coverage/ETA is written to `av_cache/_backfill_state.json` each run. ETFs and
other no-earnings symbols are **negative-cached** (never re-fetched); a
throttle response stops the run cleanly (budget spent, resume tomorrow).

**Unattended (recommended):** a Windows Scheduled Task runs it daily —
```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_backfill_task.ps1
```
registers `AgentTradingLogic-AVBackfill` (daily 06:00, logs to
`av_cache\backfill.log`). The default 500-name target fills in ~25 days at
20/day; check progress with `type av_cache\_backfill_state.json`.

#### Two earnings sources (set `external_gates.av_earnings_sue.source`)

| source | history | rate | role |
|---|---|---|---|
| `av` (default) | ~30 yr retroactive, report-dated | 25/**day** | deep backtests **today** — the slow backfill above |
| `finnhub` | last ~45 days + **forward** | 60/**min** | live gate + a store that **grows daily** into a backtestable report-dated history |

Finnhub's free tier only serves a near-real-time window, so it can't fill
history retroactively — but one bulk call captures the **whole market's**
newly-reported surprises with no per-ticker budget. The daily scheduled task
runs `py -m rhengine.market_data_finnhub snapshot`, appending to
`finnhub_cache/earnings_store.json.gz`; over weeks this becomes a complete,
report-dated SUE dataset. Switch the gate to it with `"source": "finnhub"`
once enough window has accumulated (or for the live path immediately).

## Tests

```
pip install -e . && python3 -m unittest discover tests
```

If golden vectors fail: the engine drifted. Regenerate with `python3 gen_fixtures.py`
only when the change was deliberate, and review the fixture diff.
