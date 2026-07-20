---
name: strat-refine
description: >-
  Refine a trading strategy in this repo (rhengine) by cutting, adding, or
  altering rules with the quant validation suite — ablation, IS/OOS +
  walk-forward tuning, IC/t-stats, blocked cross-validation, sanity battery,
  and Monte Carlo. Use when the user wants to test whether a rule/leg/threshold
  helps, promote or drop a filter, add a regime/macro defense, or judge if an
  edge is real vs overfit. Token-frugal loop: edit config, run one CLI, read
  the verdict line, decide, repeat.
---

# Strategy refinement loop

This repo is a signal engine (`rhengine/`) plus a validation suite. Your job in
this skill is to make one honest, reversible change at a time and let the tools
say whether it helped. **Do not touch `rhengine/indicators.py`** unless the user
explicitly asks — golden fixtures in `fixtures/` guard cross-runtime drift with
the Nydex JS port. Refinements live in `config.json` (leg masks, exits,
universe) and `strategies.json` (threshold default/min/max/step), never in the
scoring math.

## Environment (Windows) — read first

- Invoke Python as `py -m rhengine.<module>` from the repo root. `python` is not
  on PATH. `tzdata` is required (already installed).
- Data source is set in `config.json` (`data_source`). Full-market `massive`
  cache exists; `yahoo` is the keyless per-symbol fallback.
- **Always pass `--cost-bps 5`.** Never report or trust whole-history numbers.
- Event-loop tools (`backtest`, `ic`, `crossval`, `sanity` fresh, `nulltest`)
  are slow at full-market scale — add `--max-tickers 150` (or lower) while
  iterating, then confirm the finalist on the full universe.
- `~300` trades is the minimum sample for confidence; Monte Carlo uses `>=20k`
  paths.

## The token-frugal loop

For each candidate change, run the *cheapest gate that could kill it first*, and
stop as soon as a gate rejects. Read only the verdict/summary line, not the
whole table, unless a number is surprising.

1. **Sanity first (seconds, no fetch).** If a trades CSV exists:
   `py -m rhengine.sanity --trades-csv <csv>` — or `--strict` in a gate.
   FAIL here means stop and fix; don't tune a broken backtest.
2. **Ablation — is the rule drag or edge?**
   `py -m rhengine.ablate --strategy <s> --cost-bps 5` (add `--regime choppy`
   for a routed strategy like bollinger). A leg subset above the `*` production
   row with a healthy OOS sample = the missing legs are drag → cut them via
   `config.json` `legMask`.
3. **IC / t-stats — does the score rank at all?**
   `py -m rhengine.ic --strategy <s> --max-tickers 300`. Positive, adj-t>=2 IC
   with monotone score tiers = the entry ranks (refine by *adding* filters).
   Flat IC but positive avg R = edge is in the exit engine (refine geometry).
4. **Add/alter a threshold — IS/OOS then walk-forward.**
   `py -m rhengine.optimize --strategy <s> --params <p>` (OAT, IS/OOS split),
   then `--walk-forward 4`. **Promotion bar: beat baseline on the POOLED
   OUT-OF-SAMPLE line with a stable per-fold winner.** IS win + OOS loss = noise
   = stays off.
5. **Cross-validation — is the edge everywhere or one lucky quarter?**
   `py -m rhengine.crossval --strategy <s> --folds 5`. Want most blocks
   positive and no single badly-negative block. `--select --params <p>` checks
   whether the best value is stable across folds.
6. **Random-entry null — does the entry beat luck?**
   `py -m rhengine.nulltest --strategy <s> --max-tickers 300`. p<=0.05 on avg R
   = real entry edge; FAIL = the performance is exit engine + drift.
7. **Monte Carlo — stress the survivor.**
   `py -m rhengine.montecarlo --trades-csv <csv> --risk-pct 1
   --max-dd-tolerance-pct 40` (exit-code gate). Read p5 final equity and p95/p99
   max drawdown; if the worst 5% is catastrophic, the weakness is sizing/stops.

## Making a change

- **Cut a leg:** set `config.json` → `strategy.thresholds.<strategy>.legMask`.
  The mask is a bitmask over confirmation legs (leg 0 / primary is always
  required). Re-run steps 2→5.
- **Add/alter a threshold:** edit the value in `config.json`
  `strategy.thresholds.<strategy>.<param>`; if it's a new tunable, also register
  default/min/max/step in `rhengine/strategies.json` so `optimize.py` can sweep
  it and the Nydex UI can read it. New gates default OFF.
- **Add a macro/regime defense:** these are scan-level overlays
  (`bear_regime_only`, `universe_filters`, prediction-market / FRED gates in
  config), never inside `indicators.py`.
- After any engine-spec change, run `py -m unittest discover tests` — if golden
  vectors fail you changed scoring math; that needs `py gen_fixtures.py` and a
  deliberate fixture-diff review, not a silent regen.

## Charts / deeper dives

Every methodology has a notebook in `notebooks/` (ablation, walkforward,
ic_tstats, crossval_sanity, montecarlo_refined) that charts what the CLI prints.
Point the user there when they want to *see* the distribution, fan, or
per-fold bars rather than a verdict line. To run one headless:
`$env:PYTHONUTF8='1'; py -m jupyter execute --inplace notebooks/<nb>.ipynb`
(notebooks are ASCII-only precisely so this path is safe).

## What "done" looks like

A promoted change: clean sanity battery, ablation keeps the leg (or the cut
wins OOS), pooled walk-forward OOS beats baseline with a stable winner, CV
broadly positive, null p<=0.05, and Monte Carlo max-DD within tolerance at the
intended risk. Anything less: report the failing gate and stop — don't stack
more tuning on an unproven change.
