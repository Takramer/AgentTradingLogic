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
| `rhengine/strategies.json` | **Canonical spec** — per-strategy legs, threshold defaults/ranges, exits, regime routing. Threshold defaults are the walk-forward-tuned values |
| `fixtures/` | Golden vectors: deterministic bar series + expected `score_ticker` output. The cross-runtime drift guard |
| `gen_fixtures.py` | Regenerates the golden vectors (only after a deliberate engine change) |

## Consuming

Python (RobinhoodAgent): git submodule at `./engine`, then `pip install -e ./engine`;
`from rhengine.indicators import score_ticker`. Thresholds resolve as spec defaults
overlaid with app-config overrides (`resolve_thresholds`).

JS (Nydex): import the spec — `import spec from '../engine/rhengine/strategies.json'` —
and test `indicators.js` against `fixtures/` with vitest (sync pass deferred).

## Tests

```
pip install -e . && python3 -m unittest discover tests
```

If golden vectors fail: the engine drifted. Regenerate with `python3 gen_fixtures.py`
only when the change was deliberate, and review the fixture diff.
