# NotebookLM Research Findings — Rule Sets, Tests & Build Backlog

Source: NotebookLM notebook "Backtesting" (69 sources), queried 2026-07-18.
Audience: an LLM (or human) implementing these rigorously in Python against this repo's engine.

Read `README.md` first for the engine layout. Ground rules for any implementation here:

1. **Every new rule is a threshold-gated leg or filter, default OFF.** Engine output at
   spec defaults must stay bit-identical (golden vectors in `fixtures/` are the guard —
   `python3 tests/test_engine.py` must pass untouched). Defaults are only promoted after
   walk-forward validation wins.
2. **Register every tunable in `rhengine/strategies.json`** (default/min/max/step) —
   that is what `optimize.py` sweeps and what the Nydex UI reads.
3. **Validation protocol** (never report whole-history numbers):
   ```bash
   python3 -m rhengine.optimize --strategy X --params newParam            # OAT, IS/OOS split
   python3 -m rhengine.optimize --strategy X --params newParam --walk-forward 4
   ```
   Promotion bar: the gate must beat baseline on the POOLED OUT-OF-SAMPLE line with a
   stable per-fold winner. In-sample wins with OOS losses = noise = stays off.
4. Volume-based legs need real consolidated volume — Yahoo (default) or Massive/SIP.
   Alpaca IEX volume is ~2–3% of consolidated and invalidates them.

---

## Part 1 — Equity rule sets (IMPLEMENTED, validated 2026-07-18, defaults OFF)

These four are already coded and sweepable. First validation on 400d × 518 tickers
(S&P 500 + NDX union, 5 bps costs) showed **no OOS edge for any of them** — details
per rule below. Re-run the same commands on longer SIP history before promoting.

### 1.1 Stage Analysis regime gate (Weinstein) — `stageFilter`, all strategies
- Rule: bull setups only when `close > SMA150` and SMA150 rising (Stage 2);
  bear setups only when `close < SMA150` and SMA150 falling (Stage 4).
  Slope proxy in code: `mean(closes[-150:]) > mean(closes[-160:-10])`. Skip if <160 bars.
- Source guidance: 30-week (150d) SMA primary filter; 40-week (200d) alternative.
  Stage-2 entry = base breakout + rising 30-wk SMA + 2–3× volume. Exit when the
  30-wk SMA flattens and price breaks the 10-wk (50d) MA. Pullbacks to the 50d MA
  in Stage 2 are add points.
- Where: `score_ticker()` post-filter in `rhengine/indicators.py`.
- Validation so far: residualmomentum IS +0.019 vs +0.012 but OOS +0.026 vs +0.044
  (overfit shape); bollinger pooled OOS −0.057R. 400d only sees ~1.5 regime cycles —
  weak power; longest-history retest is the priority.

### 1.2 TTM Squeeze release — `squeezeBars`, consolidation
- Rule: Bollinger(20, 2σ) fully inside Keltner(EMA20 ± 1.5×ATR14) = squeeze ON.
  Breakout leg additionally requires a squeeze-ON bar within the last `squeezeBars`
  bars (entry = squeeze *release*). 0 disables.
- Source guidance: BB 20-period SMA / 2σ exact; Keltner params unspecified in sources
  (1.5×ATR is the convention); enter when bands expand back outside the channels.
- Where: `calc_keltner_squeeze()` + `score_consolidation()` in `indicators.py`.
- Validation so far: never won a training fold. Squeeze events are rare in 400d —
  low sample; retest on longer history.

### 1.3 Breakout volume expansion — `volMult` up to 3.0, consolidation
- Rule: breakout bar volume ≥ 2–3× the 20-bar average (institutional confirmation);
  otherwise treat as false breakout.
- Where: already a leg; spec max raised 2.5 → 3.0 so sweeps cover the source range.
- Validation so far: walk-forward flip-flopped (3.0 → 1.1 across folds), pooled OOS
  flat (PF 1.00). No stable value.

### 1.4 RRG rotation filter — `rrgMode`, residualmomentum
- Rule (encoded proxy): RS = close/SPY-close series; RS-Ratio = 100 × RS / SMA(RS, 60).
  Bull requires RS-Ratio < 100 (Lagging/Improving quadrant), rising vs 5 bars ago
  (NE heading, ≡ RS-Momentum > 100), and |RS-Ratio − 100| > 2 (distance floor).
  Bear symmetric. Needs ≥75 aligned bars.
- Source guidance: buy Lagging/Improving quadrant (JdK RS-Ratio < 100) on NE heading
  (0°–90°), vector distance from (100,100) origin > 2; Mansfield RS smoothed with a
  52-week SMA on weeklies (daily proxy used here).
- Validation so far: OOS +0.031 vs +0.044 baseline — slightly worse. Likely because the
  existing primary leg already demands 2-bar residual persistence (an "improving" test).
  A truer JdK normalization (z-scored ratio, weekly bars) is an untested variant.

### 1.5 VWAP confirmation — no work needed
Sources want: price above VWAP and 9-EMA; pullback entries only with RSI > 50.
`score_vwap()` already has anchored-VWAP reclaim + 9/21/50 EMA stack + RSI-reset legs,
which is stricter. Nothing to build.

### 1.6 PEAD — negative finding
Sources contain **no** supporting parameters for PEAD and warn against holding through
earnings: gaps bypass stops; they prefer "buy before the catalyst, exit before the news."
Action for a builder: walk-forward the existing `pead` sleeve before ever sizing it, and
never pair PEAD entries with long options (see 3.4).

---

## Part 2 — Risk & portfolio rule sets

### 2.1 ATR position sizing + portfolio heat (IMPLEMENTED)
- Sizing: `position_size = (equity × risk%) / (ATR × multiplier)` — the engine's
  R-multiple framework in `portfolio.py` is equivalent (risk_dollars = equity × risk%).
- Stop multiplier: keep within 1.5–3.0 (current defaults 1.5 stop / 3.0 chandelier comply).
- **Heat cap: total open risk ≤ 10% of equity** — implemented as `max_heat_pct=10`
  in `simulate_portfolio()` (`--max-heat-pct`). This is a risk control, not an edge
  claim — use it regardless of backtest outcome.

### 2.2 Simulation/test suite to run on any change
```bash
python3 -m rhengine.backtest  --lookback-days 400 --cost-bps 5 --csv backtest_trades.csv
python3 -m rhengine.portfolio --risk-pct 1 --max-positions 5 --equity-csv equity_curve.csv
python3 -m rhengine.montecarlo --trades-csv backtest_trades.csv --risk-pct 1   # P(loss), P(ruin), DD dist
python3 tests/test_engine.py && PYTHONPATH=. python3 tests/test_sim.py
```
Monte Carlo acceptance idea (not yet enforced): reject a config whose bootstrap
P(ruin) > 0 or 5th-percentile max-DD breaches the heat cap's implied floor.

---

## Part 3 — Options extension (NOT BUILT — the main backlog item)

No options code exists in this repo. Build as a new module (e.g. `rhengine/options_plan.py`)
that consumes setup dicts from `score_all_tickers()` and emits an options order plan.
The engine stays signal-only; structure selection is a mapping layer.

### 3.1 Signal → structure mapping
| Signal | Structure | Why |
|---|---|---|
| Consolidation / TTM squeeze breakout | Long call (bull) / long put (bear), ~0.50Δ ATM | IV is compressed in the squeeze; buy cheap vega, paid on direction + vol expansion |
| Bollinger mean-reversion **and IV Rank > 70** | Credit spread / iron condor / strangle, short legs 0.20–0.30Δ at 1–2σ | Inflated premium; ~84%/97.5% expire-worthless probabilities at 1σ/2σ |
| MACD / momentum continuation | Long ATM or slightly ITM (~0.50Δ) | Liquidity, tight spreads, responsiveness |
| Stage 1/3 sideways holds (max-hold limited) | Covered call at resistance, 0.20–0.30Δ | Premium income while the equity chops |
| PEAD / earnings events | **Do not buy long options** | Post-earnings IV crush loses even when direction is right |

### 3.2 Strike / expiry rules
- Directional buys: 0.50Δ (ATM or one strike ITM). Short-dated (weeklies/0DTE) only
  with disciplined same-day exits — theta dominates.
- Premium selling: short strike 0.20–0.30Δ; **30–45 DTE** (theta-curve sweet spot).

### 3.3 Options risk management rules
- **Premium-based stop**: close at −30% to −50% of the option's entry value —
  never manage on the underlying's price alone (protects against theta/vol crush).
- **Hard roll**: on a large directional win, close the position, redeploy a fraction of
  the profit into a cheaper further-OTM strike — keeps exposure, banks the gain.
- **IV Rank gate**: required input. IVR = (current IV − 52wk low) / (52wk high − low).
  Sell premium only when IVR > 70; prefer buying when IVR is low. Needs an options
  data feed (chain IV history) — not derivable from the current bar data.

### 3.4 Build order for the options module
1. IV Rank provider (chain snapshots or an IV history feed; cache like `yahoo_cache/`).
2. Mapping layer: setup dict → structure/strike/expiry per 3.1–3.2 (pure function, unit-testable).
3. Options backtest: reuse `backtest.py` trade timing; approximate P&L with delta
   exposure + theta decay first, full repricing later. Validate the premium-stop rule
   against the equity-stop rule on the same signals.
4. Wire into `portfolio.py` risk framework: defined-risk structures' max loss = the
   heat-cap contribution.

---

## Part 4 — Open research questions (for the next notebook/web pass)
- Keltner parameters and a true JdK RS-Ratio/RS-Momentum formula (sources only gave the
  BB side and quadrant/heading/distance rules).
- Whether any source quantifies expected edge per filter — needed to prioritize retests.
- Earnings-calendar feed to replace `detect_earnings_event()` price-proxy (upgrade path
  noted in `indicators.py`) and to implement "exit before the event" per 1.6.
