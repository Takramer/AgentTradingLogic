# Code map — which module owns what

Two stacks share the `rhengine` package. Keep them straight.

---

## A. Research stack — `rhengine/alpha/` (post-audit, authoritative)

The **only** place performance numbers may come from.

| Module | Owns |
|---|---|
| `panel.py` | Point-in-time universe. Daily S&P 500 membership 1996→2026 from component-change history, joined to Yahoo bars + French factors. `eligible(D)` is provably invariant to appending future data (audit FC-4) |
| `book.py` | **The only performance source.** Weight targets → daily mark-to-market equity. One-session execution lag, turnover cost, −30% delisting return, margin charged above 1.0× (FC-1, FC-13) |
| `stats.py` | Newey-West alpha/beta on non-overlapping monthly returns; Deflated Sharpe at the true trial count (FC-3, FC-6, FC-14) |
| `validation.py` | Stationary block bootstrap, random-selection + random-timing nulls, purged folds, bear-episode table, drop-best-months (FC-5, FC-7, FC-10) |
| `fundamentals.py` | Point-in-time SEC XBRL with the Fama-French 6-month publication lag. `_pick_shares` resolves share-count tag priority (see pitfalls) |
| `signals.py` | Residual momentum at the horizon its citation uses — months, not days (FC-8) |
| `external.py` | The published-record panel. `--catalog --decay --micro --long-only --blend`. **Screen here before spending panel time** |
| `sleeves.py` | The RMX-C blocks: quality, momentum, investment, issuance, payout. All identical in shape — rank PIT universe, top quintile cap-weighted, 10% name cap, next-close execution, no stops. Also `reversal_fast` and `value_ebm` (both rejected, kept for reproducibility) and `scale_by_own_vol` |
| `blend.py` | Combines sleeves. `--ablate` (leave-one-out), `--band`, `--momentum-calendar`, `--turnover-sweep` |
| `rmxb.py` | **RMX-B — the shipped strategy.** 50/50 quality + momentum. `--holdings` prints today's book |
| `rmxq.py` | The quality leg standalone. Also `adjusted_shares` and `_repair_share_units` |
| `rmxd.py` | RMX-D, frozen. **Rejected out-of-sample, 1/11 gates.** Do not tune |
| `timing.py` | RMX-D's 10-month trend gate + absolute-momentum risk-off leg |
| `rmx.py` | Shared research plumbing |

**Rejected code stays in the tree, out of `DEFAULT_SLEEVES`**, so the rejection
is reproducible.

## B. Legacy engine — `rhengine/*.py`

Shipped to RobinhoodAgent and Nydex. Its headline numbers were withdrawn by the
audit and have **not** been re-established.

| Module | Owns |
|---|---|
| `indicators.py` | **Do not edit without an explicit ask.** Pure scoring: `calc_*`/`score_*` (vwap, bollinger, macd, consolidation, residualmomentum, pead), regime/VIX classifiers, `score_ticker`/`score_all_tickers`, `resolve_thresholds`, `strategy_status`, `autonomous_strategies`, `select_strategy_for_regime` |
| `strategies.json` | **Canonical spec.** Per-strategy legs, threshold default/min/max/step, exits, `regime_routing`, `status`, measured cost blocks. Read by Python *and* the Nydex UI |
| `scan.py` | Live scan. Auto-routing returns only `status: autonomous` strategies; no cleared strategy ⇒ stands flat. `--strategy` is the manual bypass; `--all` covers every non-retired one |
| `costs.py` | Single source of truth for transaction costs — resolves `config.json` `costs.round_trip_bps`, and holds `CAP_TIERS` / `bps_for_caps` / `book_effective_bps`, the **per-name** cap-tiered model. The flat 30 is the LEGACY scan universe's number; a cap-weighted S&P 500 book pays ~2.8 bps. See research rules §6a |
| `alpha/book.py` | `run_book`'s `round_trip_bps` takes a float **or** a per-name Series; each name then pays its own spread on its own traded notional. `BookResult.effective_bps` reports what was actually paid, turnover-weighted |
| `execution.py` | Module 7 execution gates: spread gate, order type, block routing. **Live path only** — daily bars have no bid/ask, so it changes no backtest number |
| `market_data_options.py` | VRP measurement kit: Black-Scholes inversion for implied vol, realised vol, `vrp()`. Arithmetic only — no strategy, no data provider (IV is not fetchable; it is derived) |
| `backtest.py` | Walk-forward signal backtest → trade list with R-multiples |
| `portfolio.py` | Account-level sim: fixed-fractional sizing, position caps, per-day entry limits → equity curve, CAGR, maxDD, Sharpe |
| `montecarlo.py` / `mc_refined.py` | Bootstrap over trade R-multiples → outcome/drawdown distributions, P(loss), P(ruin) |
| `mc_books.py` | The **research-stack** Monte Carlo — RMX-B/RMX-C daily book returns, stationary-block bootstrap over SESSIONS, SPY resampled on the same indices → P(underperform SPY). Different unit from `mc_refined.py`; do not mix the two on one axis. Feeds §7–9 of `notebooks/montecarlo_refined.ipynb` |
| `optimize.py` | Threshold sweeps (OAT/grid), IS/OOS split, expanding-window walk-forward |
| `ablate.py` / `ablate_vbt.py` | Leg ablation; VectorBT full-matrix version ranked by profit factor |
| `ic.py` | Cross-sectional rank-IC + overlap-adjusted t, IC IR, score-tier monotonicity |
| `crossval.py` | Blocked CV with purge/embargo; `--by-regime` for the macro-defense test |
| `sanity.py` | Ten mechanical lie-detectors on a trade list. Nonzero exit code gates CI |
| `nulltest.py` | Random-entry permutation test — does the ENTRY beat luck? Permutes entries against REAL price paths, so drift and vol clustering survive |
| `barperm.py` | Bar-permutation null — does the WHOLE SYSTEM beat structureless data? Permutes the DATA (synchronized across tickers and SPY, so the market factor survives), re-runs signal generation and all. The only test of the three that can catch an "edge" that is really drift or vol clustering |
| `trials.py` | Append-only experiment ledger (`research_data/trials.jsonl`) + the multiple-testing bar. `expected_max_t` / `sidak_t` / `family_wise_p` — does the RESULT beat the search that found it? |
| `gates.py` | External-gate seam: annotates trades with `sue`/`sue_pct`/`macro_risk_on`, or hard-filters in live-representative mode |
| `backfill.py` | Resumable AlphaVantage earnings backfill, most-liquid-first, budget-aware |
| `universe_hygiene.py` | ETF/ADR/biotech exclusion. **The single biggest scan-quality win** — ETF blocklist from the Nasdaq directory |
| `charts.py` / `viz.py` | ASCII charts (dependency-free) and the shared matplotlib palette the notebooks draw through |

### Data adapters

| Module | Source | Note |
|---|---|---|
| `market_data.py` | Alpaca IEX + Massive/Polygon grouped-daily | Real SIP volume, disk-cached |
| `data_local.py` | Yahoo daily bars, CSV loader, synthetic universe | Keyless; the offline path |
| `market_data_av.py` | AlphaVantage earnings surprise | 25 req/**day** free tier — the SUE gate's long pole |
| `market_data_finnhub.py` | Finnhub earnings | 60 req/**min**, last ~45 days + forward; the store grows daily into backtestable history |
| `market_data_fred.py` | FRED macro (T10Y2Y, HY OAS, VIX) | `macro_flags` overlay |
| `market_data_kalshi.py` | Kalshi implied-probability bars | Perps order routing stubbed |
| `market_data_polymarket.py` | Polymarket Gamma/Data/CLOB | Bars have `volume=0` — cut VOL legs via `legMask` |

## C. Scripts — `scripts/`

| Script | Answers |
|---|---|
| `fetch_history.py` | 1,213 ever-S&P500 tickers, full Yahoo history (~45 min) |
| `fetch_sec_fundamentals.py` | SEC XBRL 2009–2025, point-in-time |
| `fetch_external_factors.py` | The 389-series published panel (134 MB, ~1 min) |
| `fetch_splits.py` | Split history → `research_data/splits.json.gz` |
| `rmx_bias.py` | Survivorship inflation, measured against RSP |
| `rmx_ic.py` / `rmxq_ic.py` | The screens that rejected 8 price and 5 fundamental signals |
| `rmxd_report.py` / `rmxq_report.py` | Full 11-gate reports |
| `ff_factor_check.py` | French 1963–2026 |
| `ff_largecap_quality.py` | The 63-year quality-leg evidence |
| `ff_blend.py` | The blend: alpha t=+2.54, 4/4 eras |
| `ff_largecap_blend.py` | Does any construction of this family beat the market inside the largest size quintile? EW vs VW inside the bucket |
| `cap_cost_profile.py` | Labels a book's holdings by market-cap tier and re-costs it per name. Prints the tier weights, the effective bps, and CAGR/alpha under flat vs `tier-lo/point/hi`. Run before quoting any cost-sensitive number for a book |
| `ff_size_ladder.py` | The same question down the WHOLE size ladder, each bucket benchmarked against its own cap-weighted return so the size premium is netted out. **Answered and rejected 2026-08-01** — no gradient over 63 years, momentum's gradient decayed to nothing, quality now best in BIG. See [SIZE-LADDER-2026-08-01.md](SIZE-LADDER-2026-08-01.md); do not re-litigate without reading it |
| `rmxqt_power.py` | Why no 8-year test can prove it |
| `verify_vol_scaling.py` | Barroso & Santa-Clara replicated on French momentum |
| `rh_spreads.py` | Measured Robinhood quoted spreads → real cost in bps |
| `measure_pit_cost.py` / `cost_crossover.py` | Where cost overtakes the edge |
| `strategy_scorecard.py` | Cross-strategy summary |
| `fetch_video_titles.py` | Video/course intake. Takes any mix of video and playlist URLs, emits a TSV of id/channel/title. **Titles and channels only — transcripts are unreachable and no `yt-dlp` here.** Playlist items are `lockupViewModel` now, not `playlistVideoRenderer`; continuations are followed |
| `register_backfill_task.ps1` | Windows Scheduled Task for the daily AV backfill |

## D. Data on disk

**Caches (gitignored, rebuildable):** `yahoo_cache/`, `massive_cache/`,
`av_cache/`, `finnhub_cache/`, `fred_cache/`, `kalshi_cache/`,
`polymarket_cache/`.

**`research_data/`** — `external/` (published factors; CSVs ignored but
`_manifest.json` files **are tracked**, because they record which vintage a
result came from), `sec/`, `yahoo_long/`, `_panel_cache.pkl`, `sp500_pit_raw.csv`,
French `ff*.csv` and `port_*.csv`, and the frozen `rmxd_*/rmxq_*` JSON results.

**Tracked reference data:** `etf_blocklist.json` (Nasdaq directory),
`sector_map.json`, `fixtures/` (golden vectors), `config.json`.

**Artifacts:** `tests/results/` — trades CSVs and measurement captures the
notebooks read.

## E. Notebooks — `notebooks/`

`ablation`, `walkforward`, `ic_tstats`, `crossval_sanity`, `montecarlo_refined`.
ASCII-only so headless execution is safe. Each caches its trades CSV under
`tests/results/` and recomputes only when its `REFRESH` flag is `True`. All draw
through `rhengine/viz.py`.

## F. Polymarket paper — `polymarket_paper/`

Separate from both stacks. Paper account for the public Nightshark crypto
up/down rule (`config.json` holds the video defaults). Orders stay unsent.
A balance in `state.json` is a paper mark, and research numbers still come
from `book.py`. Tests: `tests/test_polymarket_paper.py`.

## G. Tests — `tests/`

`test_adapters`, `test_alpha_pit` (incl. `test_universe_is_point_in_time`),
`test_alpha_stack`, `test_backfill`, `test_engine`, `test_finnhub`, `test_gates`,
`test_pit_universe`, `test_residmom_horizon`, `test_sim`, `test_sleeves` (29),
`test_strategy_status`, `test_polymarket_paper`.

If golden vectors fail, the engine drifted. Regenerate with `py gen_fixtures.py`
**only** after a deliberate change, and review the fixture diff.
