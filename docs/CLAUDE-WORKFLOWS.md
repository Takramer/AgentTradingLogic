# Workflows — runbooks

Every command runs from the repo root with `py`. Rules that govern these
runbooks: [CLAUDE-RESEARCH-RULES.md](CLAUDE-RESEARCH-RULES.md).

---

## 1. Add or drop a sleeve (research stack)

Run the cheapest gate that could kill it first. Stop the moment one rejects.

1. **Decay screen** — is this signal dead in my era?
   ```bash
   py -m rhengine.alpha.external --decay --top 25
   ```
   Dead since 2015 ⇒ drop it before writing any code. (`MomSeason` died here:
   −8.7 bp/mo, t=−0.19.)
2. **Size screen** — does it survive in an investable universe?
   ```bash
   py -m rhengine.alpha.external --micro --top 20
   ```
   Median ex-micro retention is 0.72. Anything near 0.37 (reversal) is a
   micro-cap premium this book cannot collect.
3. **Long-only screen — the one that matters.** Never `--blend`.
   ```bash
   py -m rhengine.alpha.external --long-only CANDIDATE \
          --vs GP Mom12m AssetGrowth ShareIss5Y
   ```
   Bar: positive `d_t` in **all three** windows (1970-2024, 2010-2018,
   2015-2024). `d_t` of +0.4 is at the edge of what selection alone produces —
   ship on consistency plus a cited paper, not on one number.
4. **Correlation check.** A candidate above ~+0.5 with an existing leg is the
   same theme; it adds turnover, not diversification. (`NetPayoutYield` was
   dropped at +0.64 with `issuance`.)
5. **Implement** in `alpha/sleeves.py`, matching the construction you screened —
   same formation window, same rebalance calendar, same weighting. Add unit
   tests to `tests/test_sleeves.py`.
6. **Local implementation check** — turnover, cost, name counts, correlations.
   Not significance.
   ```bash
   py -m rhengine.alpha.blend --lo 2010-07-01 --hi 2018-12-31
   py -m rhengine.alpha.blend --ablate --lo 2010-07-01 --hi 2018-12-31
   py -m pytest tests/test_sleeves.py -q
   ```
7. **If the local window disagrees with the published record, the record
   governs.** Document the disagreement rather than resolving it silently.

## 2. Refine a legacy-engine strategy

Full loop in `.claude/skills/strat-refine/SKILL.md`. Summary:

1. `py -m rhengine.sanity --trades-csv <csv> --strict` — FAIL means fix, not tune.
2. `py -m rhengine.ablate --strategy <s>` — is the leg edge or drag?
3. `py -m rhengine.ic --strategy <s> --max-tickers 300` — does the score rank?
   Flat IC + positive avg R ⇒ the edge is in the exit engine, not the entry.
4. `py -m rhengine.optimize --strategy <s> --params <p>` then `--walk-forward 4`.
   **Promotion bar: beat baseline on the POOLED OUT-OF-SAMPLE line with a stable
   per-fold winner.** IS win + OOS loss = noise = stays off.
5. `py -m rhengine.crossval --strategy <s> --folds 5` (`--by-regime` for the
   macro-defense test).
6. `py -m rhengine.nulltest --strategy <s> --max-tickers 300` — p ≤ 0.05 or the
   entry is luck.
7. `py -m rhengine.montecarlo --trades-csv <csv> --risk-pct 1 --max-dd-tolerance-pct 40`.

**Where changes go:** leg cuts → `config.json` `strategy.thresholds.<s>.legMask`
(bitmask over confirmation legs; leg 0 is always required). New tunables → also
register default/min/max/step in `rhengine/strategies.json` so `optimize.py` can
sweep it and Nydex can read it. **New gates default OFF.** Macro/regime defenses
are scan-level overlays (`bear_regime_only`, `universe_filters`, FRED /
prediction-market gates), never inside `indicators.py`.

Then `py -m unittest discover tests`. Golden-vector failure means you changed
scoring math.

Sample floors: ~300 trades minimum; Monte Carlo ≥20k paths;
`--max-tickers 150` while iterating, full universe to confirm.

## 3. Plug in new data

Full loop in `.claude/skills/strat-data/SKILL.md`. Everything routes off
`config.json`:

- `data_source`: `yahoo` (keyless, per-symbol — explicit watchlist, not `"all"`)
  | `massive` (whole-market SIP cache, needs backfill) | `alpaca` (IEX volume
  only) | `kalshi` / `polymarket` (implied-probability bars).
- `watchlist`: `"all"` or an explicit list.
- `universe_filters`: `min_price`, `min_dollar_volume`, `plain_symbols_only`,
  `lookback_sessions`, `exclude_etfs` / `exclude_adr` / `exclude_biotech`.
- `--lookback-days N` is a **CLI flag, not config**.

Own CSVs, no network: `data_local.load_csv_dir(<dir>)` and
`data_local.synthetic_universe()` produce the exact bar shape `run_backtest_on`
consumes.

## 4. Reproduce the headline results

```bash
# data (once; ~45 min for the Yahoo bulk fetch)
py scripts/fetch_history.py
py scripts/fetch_sec_fundamentals.py
py scripts/fetch_external_factors.py

# integrity
py -m rhengine.alpha.panel
py scripts/rmx_bias.py
py tests/test_alpha_pit.py && py tests/test_alpha_stack.py

# the screens that rejected things
py scripts/rmx_ic.py  --lo 2005-01-01 --hi 2015-12-31
py scripts/rmxq_ic.py --lo 2010-07-01 --hi 2018-12-31
py scripts/ff_factor_check.py

# RMX-B, the survivor
py scripts/ff_largecap_quality.py      # the 63-year quality evidence
py scripts/ff_blend.py                 # alpha t=+2.54, 4/4 eras
py -m rhengine.alpha.rmxb
py scripts/rmxqt_power.py              # why 8 years cannot prove it
```

## 5. Today's book

```bash
py -m rhengine.alpha.rmxb --holdings
```

## 6. Measure real transaction cost

```bash
py scripts/rh_spreads.py report
```
Prints trade-weighted round trip against `config.json`'s assumption and flags it
`conservative` or `OPTIMISTIC`. Readings so far are mid-session only (29.1 and
24.6 bps on consecutive days). **The opening spread — what the engine actually
fills at — is still unmeasured after two failed attempts.** Getting it is worth
more than any turnover-reduction tweak.

## 7. Run a notebook headless

```powershell
$env:PYTHONUTF8='1'; py -m jupyter execute --inplace notebooks/ablation.ipynb
```
Notebooks are ASCII-only so this path is safe. Interactively, **Restart Kernel**
after any edit to `rhengine/` — Run All keeps the stale module.

## 8. Top up the earnings cache

```bash
py -m rhengine.backfill --once --max-requests 20     # spend today's budget, exit
py -m rhengine.backfill --loop                       # daily, forever
py -m rhengine.market_data_finnhub snapshot          # whole market, one call
```
AlphaVantage is 25 req/day, so this fills over weeks. Coverage/ETA lands in
`av_cache/_backfill_state.json`. `sue` is `"na"` for uncached tickers — backfill
before trusting a pass/fail split. Unattended:
`powershell -ExecutionPolicy Bypass -File scripts\register_backfill_task.ps1`.

## 9. Before touching a holdout

1. Write the entry in `docs/HOLDOUT.md` — window, spec, config hash, declared
   kill criterion — **and commit it**.
2. Run once.
3. Record the result, pass or fail, and change nothing in response.

RMX-D is the worked example: failed 1/11 gates, no parameter adjusted,
Amendment E written.

## 10. Ship a documentation change

Results go in `docs/RMX-RESULTS.md`; method, every trial and every kill
criterion in `docs/RMX-PREREGISTRATION.md` (write the hypothesis **before** its
run); current sleeve work in `docs/RMX-C.md`. When a published number changes,
withdraw and restate it explicitly with an amendment — do not edit it in place.
