---
name: strat-data
description: >-
  Plug new data into the rhengine backtest/validation pipeline and re-run the
  full sweep — switch or refresh a data source (yahoo/massive/alpaca/kalshi/
  polymarket/FRED/AlphaVantage), change the universe or lookback window, load
  your own OHLCV CSVs, then regenerate the trades and all validation artifacts
  the notebooks consume. Use when the user says "backtest on X data", "add these
  tickers", "refresh the cache", "run it on 2 years", or "use my CSVs". Frugal:
  edit config, run the harvest CLIs, hand back the cached CSV paths.
---

# Plug in data + re-run the validation sweep

Goal: get fresh data into `run_backtest_on` (the pure in-memory core every tool
reuses) and regenerate the CSVs the notebooks and gates read. One edit to
`config.json`, then a fixed sequence of harvest commands.

## 1. Point the pipeline at the data

Everything routes off `config.json`. Edit these, nothing else:

- **`data_source`**: `yahoo` (keyless, per-symbol — use an explicit watchlist,
  not `"all"`), `massive` (whole-market SIP cache; needs backfill), `alpaca`
  (IEX volume only — weakens volume legs), `kalshi` / `polymarket` (implied-
  probability bars; watchlist entries become market tickers/slugs, and
  Polymarket bars have volume=0 so cut VOL legs via `legMask`).
- **`watchlist`**: `"all"` (massive whole-market via `universe_filters` floors)
  or an explicit list of symbols.
- **`universe_filters`**: `min_price`, `min_dollar_volume`, `plain_symbols_only`,
  `lookback_sessions` — the tradeability floors for `"all"`. Zero them to scan
  literally everything.
- **`lookback_days`** is a CLI flag, not config — pass `--lookback-days N` (the
  massive cache depth caps how far back you can go).

**Your own CSVs (no network):** `rhengine.data_local.load_csv_dir(<dir>)` and
`synthetic_universe()` both produce the exact bar shape `run_backtest_on`
consumes. For a one-off, load them in a notebook rather than editing config.

**Refresh / backfill the massive cache:**
`py -m rhengine.market_data backfill 730` (Polygon free tier: 5 req/min,
~2 years max). Kalshi/Polymarket/FRED/AV each cache under their own `*_cache/`
dir with their own TTL; AlphaVantage free tier is 25 req/**day** so its batch
helper is budget-aware — don't loop it.

## 2. Regenerate the artifacts (in dependency order)

Run from repo root, always `--cost-bps 5`. Cap the universe while checking the
plumbing (`--max-tickers 150`), then drop the cap for the real run.

```powershell
# a. Signal-level trades — the base artifact most tools/notebooks read
py -m rhengine.backtest --lookback-days 400 --cost-bps 5 --csv tests/results/cv_trades.csv

# b. Refined-set trades (VBT path) — Monte Carlo notebook input
py -m rhengine.mc_refined --lookback-days 730 --cost-bps 5 --csv tests/results/refined_trades.csv

# c. IC signal panel (scores + forward returns)
py -m rhengine.ic --max-tickers 300 --csv tests/results/ic_panel_auto.csv

# d. Full-matrix ablation (whole market, VBT)
py -m rhengine.ablate_vbt        # writes ablation_vbt_results.csv
```

Then the notebooks (`notebooks/*.ipynb`) pick these CSVs up automatically —
they check for the cached file and only recompute when `REFRESH=True`.

## 3. Confirm the data is sane before trusting anything

New data is the most common source of a fake edge (survivorship, bad volume,
split glitches). Gate it:

```powershell
py -m rhengine.sanity --trades-csv tests/results/cv_trades.csv --strict
```

Watch specifically for: `degenerate_r` (bad split/tiny-stop rows),
`ticker_concentration` / `month_concentration` (a thin universe leaning on one
name), and `cost_stress` (edge that only exists at zero cost). A FAIL means the
data or config is wrong — fix before running the refinement loop.

## 4. Hand off

Report the cached CSV paths you regenerated and the one-line sanity verdict.
If the user then wants to *change rules*, that's the **strat-refine** skill —
this skill only gets fresh, trustworthy data into the pipeline.

## Guardrails

- Don't edit `rhengine/indicators.py` or the golden `fixtures/` to make data
  fit — the engine is the constant; data plugs in around it.
- Switching `data_source` changes which legs are valid (IEX/Polymarket volume).
  Note it when the volume legs are in play.
- `secrets.json` holds live keys (ALPACA/MASSIVE/FRED/ALPHAVANTAGE). It's
  gitignored — never print it, commit it, or echo its contents.
