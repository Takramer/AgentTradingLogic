# AgentTradingLogic — shared trading engine

Single source of truth for the 6-strategy signal engine shared by
**RobinhoodAgent** (autonomous Python app) and **Nydex** (client-side React app).
Engine-only: no order planning, no autonomous orchestration, no meta-labeling ML —
those stay app-side.

## Contents

| Path | What |
|---|---|
| `rhengine/indicators.py` | Pure engine: `calc_*`, `score_*` (vwap, bollinger, macd, consolidation, residualmomentum, pead), regime/VIX classifiers, `score_ticker` / `score_all_tickers` |
| `rhengine/market_data.py` | Data adapters: Alpaca IEX + Massive/Polygon grouped-daily (real SIP volume, disk-cached). Resolves `secrets.json` / `config.json` / `massive_cache` from the consuming app's cwd |
| `rhengine/backtest.py` | Walk-forward backtest engine (`python3 -m rhengine.backtest` from the app dir) |
| `rhengine/data_local.py` | **No-key data layer**: Yahoo daily bars (disk-cached in `yahoo_cache/`), OHLCV CSV loader, seeded synthetic universe for offline tests |
| `rhengine/portfolio.py` | Account-level equity simulation: replays backtest trades with fixed-fractional sizing, position caps, per-day entry limits → equity curve, CAGR, max DD, Sharpe |
| `rhengine/montecarlo.py` | Bootstrap Monte Carlo over trade R-multiples → outcome/drawdown distributions, P(loss), P(ruin) |
| `rhengine/optimize.py` | Threshold sweeps (OAT / grid) with in-sample/out-of-sample splits, plus expanding-window walk-forward fold evaluation |
| `rhengine/ablate_vbt.py` | VectorBT full-matrix rule ablation: every combination of each strategy's legs re-derived as vectorized signals and backtested, ranked by profit factor (`py -m rhengine.ablate_vbt`) |
| `rhengine/charts.py` | Dependency-free ASCII charts (equity curve, drawdown, histograms) — auto-shown by portfolio/montecarlo, or standalone: `py -m rhengine.charts --equity-csv equity_curve.csv --trades-csv backtest_trades.csv` |
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

The repo ships its own `config.json` (`data_source: "yahoo"`) so every tool
below runs from this directory. The shipped config uses `data_source:
"massive"` with `watchlist: "all"` — the **entire US market** (~7k tradeable
tickers after `universe_filters` floors; zero the floors to scan literally
everything) served instantly from the local whole-market cache. Build/extend
the cache once with `py -m rhengine.market_data backfill 730` (Polygon free
tier: 5 req/min, ~2 years max history). Alternatives: `yahoo` (keyless,
per-symbol — use an explicit watchlist, not "all") or `alpaca` (instant but
IEX-only volume, ~2-3% of consolidated — weakens the volume legs).

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

## Tests

```
pip install -e . && python3 -m unittest discover tests
```

If golden vectors fail: the engine drifted. Regenerate with `python3 gen_fixtures.py`
only when the change was deliberate, and review the fixture diff.
