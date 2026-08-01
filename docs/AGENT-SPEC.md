# Agent parameter specification — technical / tactical layer

> ### ⚠ Read [AGENT-SPEC-VERDICT.md](AGENT-SPEC-VERDICT.md) before building any of this
>
> An external-evidence audit (2026-07-30) says **build nothing from Modules 1–5**.
> Weinstein stage analysis has no peer-reviewed support and its nearest testable
> relative fails over 155 years; Fair Value Gaps have no academic literature at
> all; the ORB result assumes zero bid-ask spread, which is the one cost this
> repo has measured. Two things survive: the Module 7 **spread gate**, and a
> **volatility-risk-premium measurement** underneath Module 6.
>
> This file remains accurate as a record of what the sources say. It is no longer
> a build queue.

> ### Provenance and standing, 2026-07-30
>
> Two corpora, kept separable in the `Source` column:
>
> | Tag | Corpus | How it was read |
> |---|---|---|
> | `NB p.n` | **NotebookLM synthesis over 75 sources** (successor to the 69-source pass in [notebooklm-findings.md](notebooklm-findings.md)) | Indirect. Passage IDs are the only provenance — **the 75 underlying documents were not read** |
> | `R1` | `Research/Algorithmic Execution Framework_ Systematic Breakout and Swing Trading Integration.md` | Read in full |
> | `R2` | `Research/Comprehensive Briefing_ Quantitative Trading Strategies and Volatility Analysis.md` | Read in full |
> | `R3` | `Research/Volatility-Driven Architecture_ Integrating ATR and Implied Volatility…md` | Read in full |
>
> An `NB p.n` citation is a pointer to a claim, not a verified citation. An
> `R1`/`R2`/`R3` citation is a direct quote from a file in this repo. Where the
> two corpora disagree, both values appear in [CONFLICTS](#conflicts) — the
> R-corpus does **not** automatically win just because it was read directly.
>
> **This is a specification, not evidence.** Nothing here has been run through
> `rhengine/alpha/book.py`. No number in this file is a performance claim, and
> none of it may be quoted as one — see
> [CLAUDE-RESEARCH-RULES.md](CLAUDE-RESEARCH-RULES.md) §1. Where a parameter here
> has already been implemented *and tested* in this repo, the verdict was usually
> "no out-of-sample edge"; that record lives in
> [notebooklm-findings.md](notebooklm-findings.md) Part 1 and is summarised in
> [§ Status in this repo](#status-in-this-repo) at the bottom of this file.
>
> Purpose of the file: give a coding agent exact, unambiguous parameters so that
> **what gets built is what the sources actually said**, and so that any later
> rejection is a rejection of the published rule rather than of a paraphrase.

Modules 1–8 are source-only. Everything the sources fail to specify is listed in
[COVERAGE GAPS](#coverage-gaps), and every place two sources disagree is labelled
`CONFLICT` with both values shown. Gaps are **not** filled from general
knowledge — an unspecified parameter stays unspecified.

---

## Module 1 — Market regime / stage classification

Stan Weinstein's four-stage lifecycle, used as a regime gate.

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `ma_period_weekly` | 30 weeks | 30–40 weeks | `SMA_30W = mean(weekly_close[-30:])` | NB p.805, 856 |
| `ma_period_daily` | 150 days | 150–200 days | `SMA_150D = mean(daily_close[-150:])`; daily equivalent of the 30-week MA | NB p.805, 856 |
| `ma_period_alt` | 200 days / 40 weeks | fixed | Institutional-benchmark alternative to the 30-week MA (see `CONFLICT-1`) | NB p.799, 856 |
| `secondary_ma_weekly` | 10 weeks | fixed | 10-week SMA (= 50-day). Stage 2 pullback-add level and Stage 3 exit trigger | NB p.857, 882 |
| `stage2_slope` | `> 0` | `> 0` | `d/dt SMA_30W > 0` | NB p.788, 857 |
| `stage4_slope` | `< 0` | `< 0` | `d/dt SMA_30W < 0` | NB p.788, 857 |
| `stage1_slope` / `stage3_slope` | flat | flat | `d/dt SMA_30W ≈ 0` — no numeric tolerance given (see `GAP-1.1`) | NB p.788, 857 |
| `stage2_price` | `close > SMA_30W` | — | Weekly close above a rising 30-week MA | NB p.788, 857 |
| `stage4_price` | `close < SMA_30W` | — | Weekly close below a falling 30-week MA | NB p.788, 857 |
| `stage1_price` / `stage3_price` | oscillating | — | Price crosses above and below the flat MA repeatedly (Stage 1 narrow-range, Stage 3 wide/erratic) | NB p.788, 857 |
| `breakout_volume_mult` | 2.0× | 1.5×–3.0× | `breakout_volume >= mult * mean(volume[-20:])` (see `CONFLICT-2`) | NB p.786, 857 |
| `stage3_volume_test` | down-week dominant | — | `sum(volume on down weeks) > sum(volume on up weeks)` = distribution | NB p.857 |
| `stage4_volume_test` | not required | — | Breakdowns may expand volume, but absence of buying volume alone sustains markdown | NB p.857 |
| `rsd_formula` | — | — | `RSD = (price_asset / price_index) * 100` (Dorsey relative strength) | NB p.532, 858 |
| `rsm_period` | 52 weeks | fixed | `RSM = (RSD / SMA(RSD, 52) - 1) * 100` (Mansfield relative strength) | NB p.532, 858 |
| `stage2_rsm` | `> 0` | `> 0` | Mansfield RS above the zero line and rising | NB p.857, 858 |
| `stage4_rsm` | `< 0` | `< 0` | Mansfield RS below the zero line | NB p.857, 858 |
| `stage1_rsm` | `<= 0`, stabilising | — | Below zero but no longer deteriorating (flat) | NB p.857, 858 |
| `stage3_rsm` | `RSM < SMA(RSM,52)` | — | RS rolls under its own 52-week average | NB p.857, 858 |
| `stage1_action` | no position | — | Observe only; record support/resistance bounds | NB p.857 |
| `stage4_action` | flat / defensive | — | No long exposure; cash, aggregate bonds, or systematic short | NB p.857, 867 |

### Implementation Notes

```
# All inputs weekly unless noted. Requires >= 160 bars of history for a slope test.

slope        = d_dt(SMA_30W)
avg_vol_20   = mean(weekly_volume[-20:])
RSD          = (price_asset / price_index) * 100
RSM          = (RSD / SMA(RSD, 52) - 1) * 100

is_stage_1 = (slope == FLAT)
             AND price_oscillates_across(SMA_30W)
             AND (weekly_volume < avg_vol_20)
             AND (RSM <= 0) AND rsm_is_flat

is_stage_2 = (slope > 0)
             AND (weekly_close > SMA_30W)
             AND (breakout_volume >= 2.0 * avg_vol_20)
             AND (RSM > 0)

is_stage_3 = (slope == FLAT)
             AND price_whipsaws_across(SMA_30W)
             AND (down_week_volume > up_week_volume)
             AND (RSM < SMA(RSM, 52))

is_stage_4 = (slope < 0)
             AND (weekly_close < SMA_30W)
             AND (RSM < 0)

# Gating
allow_new_long  = is_stage_2
allow_add       = is_stage_2 AND pullback_holds(SMA_10W)
force_flat      = is_stage_4
no_new_entries  = is_stage_1 OR is_stage_3 OR is_stage_4
```

---

## Module 2 — Sector rotation & relative strength

Relative Rotation Graph quadrants plus Antonacci-style dual momentum.

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `rrg_origin` | `(100, 100)` | fixed | Benchmark-neutral centre of the RS-Ratio / RS-Momentum plane | NB p.106, 860 |
| `rrg_x_axis` | JdK RS-Ratio | — | Horizontal axis; normalised relative strength vs benchmark (normalisation formula not given — `GAP-2.1`) | NB p.106, 860 |
| `rrg_y_axis` | JdK RS-Momentum | — | Vertical axis; rate of change of RS-Ratio (formula not given — `GAP-2.1`) | NB p.106, 860 |
| `quadrant_leading` | — | — | `RS_Ratio > 100 AND RS_Momentum > 100` | NB p.106, 861 |
| `quadrant_weakening` | — | — | `RS_Ratio > 100 AND RS_Momentum < 100` | NB p.106, 861 |
| `quadrant_lagging` | — | — | `RS_Ratio < 100 AND RS_Momentum < 100` | NB p.106, 861 |
| `quadrant_improving` | — | — | `RS_Ratio < 100 AND RS_Momentum > 100` | NB p.106, 861 |
| `rotation_direction` | clockwise | fixed | Leading → Weakening → Lagging → Improving → Leading | NB p.861 |
| `entry_heading_deg` | 45° | 0°–90° | Tail vector heading north-east: `0 <= theta <= 90` | NB p.113–115, 861 |
| `entry_distance_min` | 2.0 units | `> 2.0` | `sqrt((RS_Ratio-100)^2 + (RS_Momentum-100)^2) > 2.0` | NB p.116, 117 |
| `entry_quadrants` | Lagging, Improving | — | Buy candidates rotating out of Lagging / through Improving on an NE heading | NB p.113–115, 861 |
| `noise_filter_formula` | differential return | — | `ret_diff(t) = ret_sector(t) - ret_benchmark(t)` (removes common-mode market noise) | NB p.81, 864, 865 |
| `noise_filter_ema` | 125 days | fixed | Smooth `ret_diff` with EMA, time constant 125 days; weight decay `exp(-t/125)` | NB p.865 |
| `dm_lookback` | 12 months | 3 / 6 / 12 months | Absolute and relative momentum measured over the lookback (see `CONFLICT-6`) | NB p.206, 227, 866 |
| `dm_rebalance` | monthly | monthly | Evaluate and reallocate on the month-end close | NB p.227, 866 |
| `dm_universe` | 11 Select Sector SPDR ETFs | — | Cross-sectional ranking set | NB p.866 |
| `dm_top_n` | 4 | 1–4 | Hold the top N by 12-month relative return | NB p.866 |
| `dm_weight_per_sector` | 25% | `1 / top_n` | Equal weight across the selected sectors | NB p.866 |
| `dm_absolute_gate_index` | `> 0%` | `> 0%` | Offensive regime requires S&P 500 12-month return `> 0` | NB p.227, 867 |
| `dm_absolute_gate_sector` | `> 0%` | `> 0%` | An individual sector is held only if its own absolute momentum is positive (vs T-bills) | NB p.866 |
| `dm_defensive_asset` | aggregate bonds | BND / AGG / BIL / cash | On a failed absolute gate, rotate 100% out of equity | NB p.227, 867 |

### Implementation Notes

```
# --- RRG entry gate ---
distance = sqrt( (rs_ratio - 100)^2 + (rs_momentum - 100)^2 )

in_entry_quadrant = (quadrant == LAGGING) OR (quadrant == IMPROVING)
is_ne_heading     = (tail_heading_deg >= 0) AND (tail_heading_deg <= 90)
passes_distance   = (distance > 2.0)

rrg_entry_signal  = in_entry_quadrant AND is_ne_heading AND passes_distance

# --- Dual momentum allocation, evaluated month-end only ---
IF (sp500_return_12m > 0):                       # absolute gate, index level
    ranked = sort_desc(sector_etfs, key = return_12m)
    held   = [s FOR s IN ranked[0:4] IF s.return_12m > 0]     # per-sector absolute gate
    FOR s IN held:  allocate(s, 0.25)
    allocate(DEFENSIVE, 0.25 * (4 - len(held)))  # unfilled slots go defensive
ELSE:
    allocate(DEFENSIVE, 1.00)                    # BND / AGG / BIL / cash
```

---

## Module 3 — Entry signals

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `bb_period` | 20 | fixed | `SMA(close, 20)` centreline | NB p.873 |
| `bb_stdev_mult` | 2.0 | fixed | `SMA(close,20) ± 2.0 * stdev(close,20)` | NB p.873 |
| `keltner_period` | 20 (EMA) | fixed | `EMA(close, 20)` centreline | NB p.873 |
| `keltner_atr_mult` | 1.5 | fixed | `EMA(close,20) ± 1.5 * ATR_14` — specified for the TTM squeeze only (`GAP-3.2`) | NB p.873 |
| `squeeze_on_condition` | — | — | `bb_upper < kc_upper AND bb_lower > kc_lower` (Bollinger inside Keltner) | NB p.873 |
| `squeeze_release_condition` | — | — | Bands expand back outside the channel; entry is on the **release**, not the squeeze | NB p.873 |
| `squeeze_confirmation` | volume + momentum | — | Volume expansion, plus MACD or RSI directional alignment | NB p.873 |
| `orb_window_min` | 5 minutes | 5–30 minutes | `H_orb`, `L_orb` = high/low of 09:30–09:35 ET (see `CONFLICT-3`) | NB p.400, 859 |
| `orb_long_trigger` | `price > H_orb` | — | Buy ATM call on break of the opening-range high | NB p.400 |
| `orb_short_trigger` | `price < L_orb` | — | Buy ATM put on break of the opening-range low | NB p.400 |
| `orb_session_filter` | Mon / Wed / Fri | fixed | Execute only on those weekday sessions | NB p.400 |
| `base_duration_weeks` | 12 weeks | 12–24 weeks | Stage 1 consolidation length required before a breakout is tradable | NB p.914 |
| `base_breakout_trigger` | weekly close | — | Weekly close above the Stage 1 horizontal resistance | NB p.857, 914 |
| `base_breakout_volume` | 2.0× | 1.5×–3.0× | `breakout_volume >= mult * mean(volume[-20:])` (see `CONFLICT-2`) | NB p.786, 857 |
| `base_breakout_regime` | Stage 2 | — | Requires rising `SMA_30W`, `price > SMA_30W`, `RSM > 0` | NB p.788, 857 |
| `pullback_level` | broken resistance | — | Retrace to the level that was resistance before the breakout; it must hold as support | NB p.784, 788 |
| `pullback_volume` | below average | — | Retracement volume must contract vs its own baseline (no multiplier given — `GAP-3.4`) | NB p.784, 788 |
| `pullback_confirmation` | bullish reversal candle | — | Rebound candle printed at or above the flipped level | NB p.784, 788 |
| `pullback_min_rrr` | 1:2 | — | Reject the setup if reward:risk to the next target is under 1:2 | NB p.437, 447 |
| `vwap_price_gate` | — | — | `price > VWAP AND price > EMA_9` (intraday) | NB p.415, 418 |
| `vwap_entry` | pullback to VWAP | — | Enter on the rebound off VWAP, not on the initial cross | NB p.417, 418 |
| `vwap_rsi_filter` | 50.0 | `> 50` | `RSI_14 > 50` must hold throughout the pullback | NB p.417 |
| `donchian_periods` | 20 | 10 / 20 / 55 sessions | Turtle: buy the break of the upper Donchian extreme; exit on consolidation or mean-reversion toward the lower channel | R1 §4, R2 §2 |
| `fib_retrace_entry` | 38.2% | fixed | Enter the corrective wave at the 38.2% retracement of the prior impulse, in the direction of the larger trend | R1 §4, R2 §2 |
| `n_day_breakout` | 20 days | fixed | Buy a new 20-day price high (or sell a new 20-day low); holding period measured over the following 20 days | R2 §1, R3 §6 |
| `atr_band_levels` | ATR High / ATR Low | — | ATR-derived dynamic support/resistance. Long requires price to touch or cross the ATR Low **without a full close below**; short is the mirror | R1 §3 |
| `fvg_requirement` | required | — | Fair Value Gap must be present. Bullish FVG = middle candle bullish (bearish for shorts). **R1 prefers FVG + ATR interaction over volume confirmation** | R1 §3 |
| `fvg_entry_level` | nearest gap edge | — | Entry triggers at the nearest edge of the Fair Value Gap | R1 §3 |
| `fvg_stop_level` | gap candle low | — | Stop anchored at the gap candle low (long) / high (short) | R1 §3 |
| `volume_filter_alt` | 10% above 50-day avg | — | `volume > 1.10 * mean(volume[-50:])` — a third, much weaker volume gate (see `CONFLICT-2`) | R1 §3, R2 §1 |
| `volume_standing` | de-emphasised | — | R1 explicitly downgrades volume: "an inconsistent endorser", "statistical significance frequently diluted by noise" | R1 §3 |
| `atr_breakout_confirm` | ATR surge | — | A measurable rise in ATR after a low-volatility period validates the breakout; flat ATR = noise | R2 §3, R3 §7 |
| `rsi_divergence_exit` | — | — | Reversal/exhaustion signal: price makes a new high, RSI fails to confirm | R2 §2, R3 §6 |
| `entry_efficiency_gate` | `IV < HV` | — | Debit/long-vega entries preferred when IV < HV; credit structures when IV > HV (see `CONFLICT-9`) | R1 §1, R2 §4, R3 §4 |
| `pre_event_entry` | before the catalyst | — | Enter ahead of a scheduled catalyst to capture the IV run-up; exit **immediately before** the release | R1 §4, R3 §4 |
| `invalidate_reentry` | — | — | Close back inside the consolidation range or below the trigger level = false breakout | NB p.784, 788 |
| `invalidate_gap` | — | — | Gap down that covers the breakout bar's low (throwback failure) | NB p.784 |
| `invalidate_option_premium` | −30% | −30% to −50% | Close if option premium decays to `<= 0.70 * entry_premium` (see `CONFLICT-4`) | NB p.504, 506, 873 |

### Implementation Notes

```
squeeze_on      = (bb_upper < kc_upper) AND (bb_lower > kc_lower)
squeeze_release = squeeze_on[-1] AND NOT squeeze_on

entry_squeeze   = squeeze_release
                  AND (volume > mean(volume[-20:]))
                  AND (macd_rising OR rsi_rising)

entry_orb_long  = (weekday IN [MON, WED, FRI])
                  AND (price > orb_5min_high)

entry_base      = is_stage_2                              # Module 1
                  AND (weekly_close > stage1_resistance)
                  AND (base_length_weeks >= 12)
                  AND (weekly_volume >= 2.0 * mean(weekly_volume[-20:]))
                  AND (RSM > 0)

entry_pullback  = broke_out_previously
                  AND touched(prior_resistance)
                  AND (price >= prior_resistance)         # support flip held
                  AND (pullback_volume < avg_volume)
                  AND bullish_reversal_candle
                  AND (reward_risk >= 2.0)

entry_vwap      = (price > vwap) AND (price > ema_9) AND (rsi_14 > 50)
                  AND rebounded_off(vwap)

entry_turtle    = (close > max(high[-20:]))            # or 10 / 55 session variant
exit_turtle     = (close < min(low[-20:])) OR consolidating

entry_fib       = trend_is_up
                  AND retraced_to(0.382 * prior_impulse_range)
                  AND resumption_confirmed

entry_20d       = (close > max(high[-20:]))            # long
                  # short variant: (close < min(low[-20:]))

# R1's validation stack — note it REPLACES volume, it does not add to it
entry_fvg_long  = touched(atr_low_band) AND NOT (close < atr_low_band)
                  AND bullish_fvg_present
                  # entry  = nearest_edge(fvg)
                  # stop   = fvg_candle_low

atr_confirms    = (atr_14 > atr_14[-1]) AND was_low_volatility_regime

# Volatility-regime gate on structure choice (Modules 3 <-> 6 handshake)
prefer_debit    = (iv < hv)
prefer_credit   = (iv > hv)

exhaustion      = price_made_new_high AND NOT rsi_made_new_high   # bearish divergence

# Invalidation — evaluated every bar while the position is open
invalidated = (close < breakout_trigger_level)
              OR (gapped_down AND low < breakout_bar_low)
              OR (is_option AND option_price <= 0.70 * option_entry_price)
```

---

## Module 4 — Volatility & position sizing

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `atr_period` | 14 | 2–50 | Wilder smoothing: `ATR_t = (ATR_{t-1} * (n-1) + TR_t) / n`. 14 is the stated industry default | NB p.424, 876; R3 §2 |
| `atr_period_short` | 2–10 | 2–10 | Short-horizon calibration: high sensitivity to immediate price shocks and noise | R3 §2 |
| `atr_period_long` | 20–50 | 20–50 | Long-horizon calibration: smoothed macro-volatility for trend identification | R3 §2 |
| `true_range` | — | — | `TR_t = max(H_t - L_t, abs(H_t - C_{t-1}), abs(L_t - C_{t-1}))` | NB p.424, 876; R2 §3; R3 §2 |
| `atr_stop_mult` | 2.0× | 1.5×–3.0× | `stop_distance = ATR_n * mult`. 1.5 = tight control in low-noise regimes; 3.0 = breathing room in high-volatility regimes | NB p.876; R1 §5; R3 §5 |
| `risk_per_trade_pct` | 1.0% | 1.0% (hard cap) or 1.0%–2.0% | `risk_dollars = equity * risk_pct`. R1/R2/R3 state a hard **1% rule**; NB gives a 1–2% band (see `CONFLICT-10`) | R1 §5; R2 §6; R3 §7; NB p.437, 473, 880 |
| `position_size_shares` | — | — | **Formula A:** `shares = floor((equity * risk_pct) / (ATR_n * atr_stop_mult))` | NB p.876 |
| `position_size_alt` | — | — | **Formula B:** `size = risk_amount / (ATR * asset_price)` — as written in R1/R3 (see `CONFLICT-11`; the expression is dimensionally inconsistent) | R1 §5; R3 §5 |
| `sizing_objective` | risk parity | — | Equalise dollar-at-risk across volatility regimes; size moves inversely to volatility | R1 §5; R2 §3; R3 §5 |
| `min_rrr` | 1:2 | `>= 1:2` | Mandated minimum — every entry must target reward `>= 2 ×` initial risk | R1 §5; R2 §6 |
| `portfolio_heat_cap` | 10.0% | max 10.0% | `sum_i(shares_i * stop_distance_i) <= 0.10 * equity` | NB p.570, 880, 883 |
| `kelly_formula` | — | 0.0–1.0 | `f* = (b*p - q) / b`, where `p` = win rate, `q = 1-p`, `b` = win/loss payout ratio | NB p.567, 883 |
| `kelly_fraction` | 0.25 (quarter) | 0.25–0.50 | Size at `kelly_fraction * f*`; full Kelly rejected for drawdown/volatility drag | NB p.883 |
| `expected_move_1sd` | 1 SD | — | `EM = S * IV * sqrt(DTE / 365)`; `S` = spot, `IV` = annualised implied vol | NB p.825, 870 |
| `sd_1_probability` | 68.2% | — | ≈16% chance ITM / ≈84% OTM at the 1 SD strike | NB p.825, 870 |
| `sd_2_probability` | 95.4% | — | `2 * EM`; ≈2.5% ITM / ≈97.5% OTM | NB p.825, 870 |
| `sd_3_probability` | 99.7% | — | `3 * EM` | NB p.825, 870 |
| `heat_approach_behaviour` | scale down | — | As heat nears the cap, reduce new position size before blocking outright | NB p.570 |

### Implementation Notes

```
tr           = max(high - low, abs(high - prev_close), abs(low - prev_close))
atr_14       = (prev_atr_14 * 13 + tr) / 14                  # Wilder, not SMA

stop_distance  = atr_14 * atr_stop_mult                       # mult IN [1.5, 3.0]
risk_dollars   = equity * risk_pct                            # 0.01 hard cap per R1/R2/R3
shares_raw     = floor(risk_dollars / stop_distance)          # Formula A — use this one

# Formula B, as literally written in R1 §5 / R3 §5:
#     size = risk_amount / (ATR * asset_price)
# It is dimensionally inconsistent (dividing dollars by dollars-squared) and
# does not reduce to Formula A. Recorded, NOT implemented. See CONFLICT-11.

# Reject before sizing if the setup cannot pay 2:1
IF (target_distance / stop_distance < 2.0): reject_entry("min RRR")

# Kelly cap
f_star         = (payout_ratio * win_rate - (1 - win_rate)) / payout_ratio
kelly_dollars  = equity * (0.25 * f_star)
shares_kelly   = floor(kelly_dollars / stop_distance)
shares         = min(shares_raw, shares_kelly)

# Portfolio heat gate — evaluated before every entry
open_heat      = sum(pos.shares * pos.stop_distance FOR pos IN open_positions)
new_heat       = shares * stop_distance
heat_budget    = 0.10 * equity

IF (open_heat + new_heat > heat_budget):
    shares = floor(max(0, heat_budget - open_heat) / stop_distance)   # scale, then block
IF (shares == 0): reject_entry("portfolio heat cap")
```

---

## Module 5 — Exit logic

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `initial_stop_atr` | 2.0× ATR | 1.5×–3.0× ATR | `stop = entry - (ATR_14 * mult)` | NB p.876 |
| `initial_stop_structural` | pivot / base low | — | Placed beneath the breakout gap low, pivot low, or base support floor | NB p.437, 857 |
| `option_premium_stop` | −30% | −30% to −50% | Close if `option_price <= 0.70 * entry_premium` (see `CONFLICT-4`) | NB p.504, 506, 873 |
| `trailing_stop_atr` | 3.0× ATR | 1.5×–3.0× ATR | Chandelier: `stop = highest_high_since_entry - (ATR_14 * mult)` | NB p.437, 876 |
| `step_trail_breakeven` | +1.0R | fixed | At `+1.0R` unrealised, move the stop to entry (`0.0R`) | NB p.506 |
| `step_trail_lock` | +2.0R → +1.0R | fixed | At `+2.0R`, trail the stop to `+1.0R`; continue in `+1.0R` increments | NB p.506 |
| `profit_target_rrr` | 1:2 | 1:1 – 1:4 | `target = entry + (rrr * initial_risk_R)` (see `CONFLICT-5`) | NB p.437, 447 |
| `time_stop_0dte` | 15:30 ET | fixed | Force-close 0DTE options at 15:30 ET if neither stop nor target hit | NB p.505, 506 |
| `time_stop_swing` | max-hold limit | unspecified | Swing setups carry a max-hold limit; no bar/day count given (`GAP-5.2`) | NB p.505 |
| `stage_exit_ma_flatten` | — | — | Exit when `d/dt SMA_30W` loses its positive slope (Stage 3 onset) | NB p.857, 882 |
| `stage_exit_volume` | — | — | Exit on heavy down-week volume exceeding up-week volume | NB p.857 |
| `stage_exit_ma_break` | `close < SMA_10W` | — | Weekly close below the 10-week (50-day) MA in Stage 3 | NB p.857, 882 |
| `stage4_liquidation` | full | — | Stage 4 confirmed = no long exposure retained | NB p.857, 867 |

### Implementation Notes

```
initial_stop = max( entry - (atr_14 * atr_stop_mult),      # volatility floor
                    structural_pivot_low )                  # whichever is tighter/structural
risk_R       = entry - initial_stop
profit_R     = (price - entry) / risk_R

# --- Stop ladder, checked every bar, most-recent wins ---
IF (profit_R >= 2.0):   stop = entry + (1.0 * risk_R)       # lock +1R
ELIF (profit_R >= 1.0): stop = entry                        # breakeven
stop = max(stop, highest_high_since_entry - (atr_14 * trail_mult))   # chandelier, ratchet only

# --- Exit precedence ---
IF (price <= stop):                                  exit("stop")
ELIF (is_option AND opt_price <= 0.70 * opt_entry):  exit("premium stop")
ELIF (price >= entry + (rrr * risk_R)):              exit("target")
ELIF (is_0dte AND now >= "15:30 ET"):                exit("time stop")
ELIF (is_stage_3 OR is_stage_4 OR weekly_close < sma_10w): exit("stage exit")
```

---

## Module 6 — Options overlay

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `covered_call_ratio` | 1 contract / 100 shares | fixed | Sell 1 OTM call per 100 long shares | NB p.874, 875 |
| `cc_return_static` | — | — | `premium / (stock_price - premium)` — return if unassigned | NB p.874 |
| `cc_return_assigned` | — | — | `((strike - stock_price) + premium) / (stock_price - premium)` | NB p.874 |
| `cc_return_annualised` | annualised | — | R2 defines Static Return and If-Called Return as **annualised**; NB's formulas are period returns (see `CONFLICT-12`) | R2 §5 |
| `cc_breakeven` | — | — | `breakeven = stock_purchase_price - premium_received` | R2 §5 |
| `cc_stance` | neutral → mildly bullish | — | Buy-write: long 100 shares + 1 short call, share-for-share | R2 §5 |
| `iv_rank_formula` | — | 0–100 | `IVR = (IV_now - IV_52w_low) / (IV_52w_high - IV_52w_low) * 100` | NB p.872 |
| `iv_rank_sell_gate` | `> 70` | `> 70` | Sell premium: covered calls, credit spreads, iron condors, strangles | NB p.872, 873 |
| `iv_rank_buy_gate` | `< 30` | `< 30` | Buy premium: long calls/puts, debit spreads | NB p.872, 873 |
| `buy_gate_alternative` | squeeze release | — | A TTM squeeze release also qualifies for buying premium (IV compressed) | NB p.873 |
| `short_call_delta` | 0.20 | 0.20–0.30 | Strike selection aligned to chart resistance; ≈20–30% assignment probability | NB p.875 |
| `long_option_delta` | 0.50 | 0.50–0.70 | ATM, or one strike ITM for directional conviction | NB p.506, 873 |
| `short_premium_dte` | 45 | 30–45 | Theta-decay acceleration window for sold premium | NB p.875 |
| `long_momentum_dte` | 0–5 | 0DTE – weeklies | Short-dated only with disciplined same-day exits | NB p.504, 506 |
| `roll_trigger` | strike approach | unspecified | Underlying approaches the short strike near expiry (no numeric trigger — `GAP-6.2`) | NB p.875 |
| `roll_standard` | net credit | — | Buy back the short call; sell a farther-month, higher-strike call for a net credit | NB p.875 |
| `roll_hard` | profit harvest | — | Close the profitable leg; redeploy part of the gain into a cheaper, lower-delta OTM strike | NB p.506, 876 |
| `roll_directions` | Up / Down / Out | — | Rolling is preferred over accepting assignment: it preserves the theta edge and the underlying position, and defers the tax and capital consequences | R1 §5 |
| `earnings_rule` | do not buy premium | — | Post-event IV crush loses even when direction is right; exit before the news | NB p.871, 877; R3 §4 |
| `iv_vs_hv_gate` | — | — | `IV > HV` → premium is expensive → **sell** (short vega: covered calls, cash-secured puts, credit spreads). `IV < HV` → **buy** (long vega: long calls/puts, debit spreads). An alternative to the IV-Rank gate — see `CONFLICT-9` | R1 §1; R2 §4; R3 §4 |
| `hv_window` | 30 days | — | Historical volatility measured over a specified past window; 30 days is the example given | R2 §4 |
| `iv_definition` | 1 SD annualised | — | IV is quoted as one standard deviation, annualised, assuming a log-normal price distribution; 68.27% of outcomes fall inside `S ± S*IV` over one year | R3 §3 |
| `vega_definition` | — | — | Dollar sensitivity of option premium to a **1% change in implied volatility** | R3 §3 |
| `bsm_inputs` | 6 | fixed | Spot, strike, days to expiry, risk-free rate, expected dividend yield, implied volatility | R3 §3 |
| `assignment_trigger_1` | ITM at expiry | — | Assignment most likely when the option is in-the-money at expiration | R2 §5 |
| `assignment_trigger_2` | time value depleted | — | Assignment risk rises as extrinsic value approaches zero | R2 §5 |
| `assignment_trigger_3` | ex-dividend date | — | Early assignment concentrates immediately before the ex-dividend date | R2 §5 |
| `early_assignment_prior` | rare | — | Early assignment is rare while substantial time value remains | R2 §5 |
| `assignment_response` | roll | — | Deep-ITM short call → roll Up/Down/Out rather than allow assignment | R1 §5 |

### Implementation Notes

```
iv_rank = (iv_now - iv_52w_low) / (iv_52w_high - iv_52w_low) * 100

IF (iv_rank > 70):
    structure = COVERED_CALL or CREDIT_SPREAD or IRON_CONDOR
    short_delta = 0.20 .. 0.30
    dte         = 30 .. 45
    strike      = nearest_strike_at(short_delta) aligned_to chart_resistance
ELIF (iv_rank < 30) OR squeeze_release:
    structure = LONG_CALL or LONG_PUT or DEBIT_SPREAD
    delta     = 0.50 .. 0.70
    dte       = 0 .. 5                       # requires same-day exit discipline

# Alternative regime gate (R-corpus) — evaluated on IV vs HV, not on IV Rank.
# The two gates can disagree; see CONFLICT-9. Pick ONE and record which.
IF (iv > hv):   vega_stance = SHORT      # sell: covered call, CSP, credit spread
ELIF (iv < hv): vega_stance = LONG       # buy:  long call/put, debit spread

# Hard blocks
IF earnings_event_before_expiry AND structure IS long_premium:
    reject("IV crush")

# Catalyst play: capture the IV run-up, never hold through the print
IF holding_into(scheduled_catalyst):
    exit_before(event_datetime)          # R1 §4, R3 §4

# --- Assignment management (short call) ---
assignment_risk = is_itm
                  AND (extrinsic_value ~= 0)
                  AND (near_expiry OR before_ex_dividend)

IF assignment_risk:
    roll(direction = UP or DOWN or OUT)  # preferred: preserves theta edge,
                                         # keeps the underlying, defers tax
ELIF underlying_price approaches short_strike AND near_expiry:
    buy_to_close(short_call)
    sell_to_open(higher_strike, later_expiry)   # must be a net credit
```

---

## Module 7 — Execution & liquidity

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `order_type_etf` | limit | limit only | Mandatory limit orders on all ETF executions | NB p.880 |
| `order_type_option` | limit | limit only | Mandatory limit orders on all options executions | NB p.880 |
| `order_type_stock` | limit | market or limit | Market permitted **only** for highly liquid single stocks in the regular session | NB p.880 |
| `market_order_prohibition` | — | — | Never market: illiquid names, volatile conditions, premarket, after-hours | NB p.880 |
| `spread_gate_pct` | 1.0% | `< 1.0%` | Reject if `(ask - bid) / mid > 0.01` | NB p.199, 878 |
| `stock_liquidity_test` | ADV + book depth | — | Secondary-market average daily volume and bid/ask depth; carries single-name gap risk | NB p.877, 879 |
| `etf_liquidity_test` | underlying basket | — | Driven by creation/redemption via Authorized Participants, **not** ETF screen volume | NB p.199, 878, 879 |
| `block_gate_shares` | 2,000 shares | `> 2,000` | Route above this size to an ETF capital-markets or block desk | NB p.199, 758, 878 |
| `block_gate_notional` | $10,000,000 | `> $10M` | Same routing gate on notional | NB p.199, 758, 878 |
| `stop_order_conversion` | — | — | Stop orders become market/limit on trigger; budget for gap-down slippage (`GAP-7.5`) | NB p.880 |

### Implementation Notes

```
spread_pct = (ask - bid) / mid

IF (spread_pct > 0.01):                 reject_execution("spread gate")

IF (asset_type IN [ETF, OPTION]):       order_type = LIMIT          # mandatory
ELIF (asset_type == STOCK
      AND is_highly_liquid(adv, depth)
      AND is_regular_session()):        order_type = MARKET or LIMIT
ELSE:                                   order_type = LIMIT

IF (asset_type == ETF):
    liquidity = assess_underlying_basket(holdings)     # NOT etf screen volume
ELSE:
    liquidity = assess_adv_and_book_depth(symbol)

IF (order_shares > 2000) OR (order_notional > 10_000_000):
    route(CAPITAL_MARKETS_DESK)
ELSE:
    route(STANDARD)
```

---

## Module 8 — Validation protocol

| Parameter | Default Value | Supported Range | Formula / Condition | Source |
|---|---|---|---|---|
| `is_oos_split` | ~60 / 40 | — | Example given: 2016–2021 in-sample, 2022–2026 out-of-sample (rule not stated — `GAP-8.1`) | NB p.98, 381, 400 |
| `blind_holdout_years` | 1–2 years | 1–2 years | Most recent 1–2 years held back, touched once, at the end | NB p.98, 381 |
| `fwpt_refit_interval` | 6 months | fixed | Forward-walk progressive tuning: re-fit parameters every 6 months of walked data | NB p.98, 99, 100 |
| `mc_runs` | 20,000 | `>= 20,000` | Minimum randomised simulation paths | NB p.110, 883 |
| `mc_signals_per_run` | 10 | fixed | Bootstrap-resample 10 trade signals per run to build a compounded equity curve | NB p.110 |
| `mc_report_band` | 20th / 80th pct | — | Extract the 20th and 80th percentile return bounds | NB p.110 |
| `pass_profit_factor` | `> 1.25` | `> 1.25` | `gross_profit / gross_loss` | NB p.420 |
| `pass_sharpe` | `> 1.00` | `> 1.00` | `(annual_return - rf) / annual_volatility` | NB p.177, 181, 564 |
| `pass_mar` | `> 1.00` | `> 1.00` | `CAGR / max_drawdown` | NB p.381 |
| `pass_expectancy` | `> 0` | `> 0` | `(win_rate * avg_win) - (loss_rate * avg_loss) - costs > 0`, net of friction | NB p.382 |
| `pass_p_value` | `< 0.05` | `< 0.05` | Equivalently `t > 2.0` against a zero-mean null | NB p.378, 555 |
| `pass_max_drawdown` | `< 20%` | `< 20%` or `< 30%` | Peak-to-trough equity decline (see `CONFLICT-7`) | NB p.420, 461 |
| `pass_mc_worst_5pct_dd` | `< 25%` | `< 25%` | Worst-5% simulated max drawdown must clear the tolerance | NB p.563 |
| `min_trades` | 300 | `>= 300` | Below ~300 trades no statistical confidence; ~9 trades is no conclusion at all | NB p.400 |
| `ic_requirement` | positive & stable | `> 0` | Rolling-window information correlation between signal and forward return (no threshold value — `GAP-8.6`) | NB p.377, 378, 141 |
| `ranking_metric` | profit factor | — | Rank configurations by profit factor, **not** total return | NB p.420 |
| `cost_requirement` | non-zero | — | Zero-cost backtests are fiction; costs must be charged | NB p.184 (see repo note below) |
| `survivorship_requirement` | dead tickers included | — | Current-membership universes are optimistic by construction | NB p.119, 199, 878 |

### Implementation Notes

```
passes_is  = (is_profit_factor  > 1.25) AND (is_sharpe  > 1.00) AND (is_trade_count >= 300)
passes_oos = (oos_profit_factor > 1.25) AND (oos_sharpe > 1.00) AND (oos_p_value < 0.05)
passes_mc  = (mc_runs >= 20000) AND (mc_worst_5pct_drawdown < 0.25) AND (mc_mean_return > 0)

passes_risk = (max_drawdown < 0.20) AND (mar_ratio > 1.00) AND (expectancy_net_of_costs > 0)

approve_for_live = passes_is AND passes_oos AND passes_mc AND passes_risk
# An in-sample pass with an out-of-sample fail is a REJECT, never a re-tune.
```

---

## COVERAGE GAPS

Parameters requested in the specification brief that the 75 sources **do not
specify**. Each is left unimplemented rather than filled from general knowledge.
Any value supplied for these later is a house assumption and must be labelled as
one.

### Module 1 — regime / stage

- `GAP-1.1` **Flat-slope tolerance.** Stage 1 and Stage 3 both require a "flat"
  30-week MA. No numeric band (degrees, %/week, or slope-vs-ATR) is given, so
  the Stage 1 / Stage 3 / transition boundary is not machine-decidable.
- `GAP-1.2` **Stage 1 minimum duration.** Stage 3 and Stage 4 carry no minimum
  base or top length; only Stage 2 entry references a 12–24 week base.
- `GAP-1.3` **"Volume dries up" threshold.** Stage 1 requires below-average
  volume, with no multiplier — unlike the breakout side, which has 2×–3×.
- `GAP-1.4` **Slope measurement window.** No lookback is given for computing
  `d/dt SMA_30W` (bars back, regression length, or smoothing).
- `GAP-1.5` **Stage 3 "wide and erratic" definition.** No range/ATR expansion
  threshold that separates Stage 3 chop from Stage 1 chop.

### Module 2 — rotation / relative strength

- `GAP-2.1` **JdK RS-Ratio and RS-Momentum formulas.** The sources give the
  quadrant rules, the heading rule and the distance rule but **not** the
  normalisation that produces the two axes. The metric is proprietary and cannot
  be reproduced from these sources. Only the Dorsey RSD and Mansfield RSM
  formulas are supplied.
  **Deliberately left unfilled, 2026-07-30.** The evidence audit found no
  literature on RRG quadrants at all, so the module is abandoned and there is
  nothing to invent a formula *for*. An unfilled gap on a dead module costs
  nothing; a house-invented parameter on an unevidenced construct costs a
  research cycle. See [AGENT-SPEC-VERDICT.md](AGENT-SPEC-VERDICT.md) §2.
- `GAP-2.2` **RRG tail length.** Number of periods drawn in the rotation tail,
  which determines the heading calculation, is unspecified.
- `GAP-2.3` **Heading calculation window.** How many tail points define
  `theta` is not stated.
- `GAP-2.4` **RRG timeframe.** Whether the RRG runs on daily or weekly bars is
  not stated (Mansfield RS is specified weekly; the RRG is not).
- `GAP-2.5` **Benchmark selection rule.** S&P 500 / SPY is used by example; no
  rule for choosing a benchmark per universe.
- `GAP-2.6` **Rebalance tolerance band.** No drift threshold that suppresses a
  monthly reallocation; the sources rebalance unconditionally.
- `GAP-2.7` **Dual-momentum lookback weighting.** When the multi-lookback
  variant (3/6/12) is used, the weights for combining the three are not given.
- `GAP-2.8` **T-bill proxy.** The absolute-momentum gate references T-bills
  without naming an instrument or tenor.

### Module 3 — entry signals

- `GAP-3.1` **Anchored VWAP.** Anchor selection (event, date, session) and
  timeframe are absent. Only intraday session VWAP is covered.
- `GAP-3.2` **Keltner parameters outside the TTM squeeze.** `EMA 20 / 1.5 ATR`
  is given for the squeeze only; no general-purpose Keltner setting.
- `GAP-3.3` **PEAD parameters.** No earnings-surprise magnitude, gap size, or
  post-earnings holding window. The sources' only earnings guidance is
  prohibitive (do not hold long premium through the event).
- `GAP-3.4` **Pullback volume multiplier.** "Below average" with no factor,
  unlike the 2×–3× on the breakout side.
- `GAP-3.5` **Base tightness / depth.** No maximum base depth (%) or
  volatility-contraction test qualifying a Stage 1 base.
- `GAP-3.6` **ORB stop placement.** The opposite side of the opening range is
  the obvious candidate but is never stated.
- `GAP-3.7` **Confirmation count.** How many confirmations must agree before an
  entry fires (any one, or all) is not specified for any setup.
- `GAP-3.8` **MACD parameters.** MACD is named as a squeeze confirmation with
  no periods given.
- `GAP-3.9` **RSI period outside the VWAP setup.** `RSI > 50` is specified for
  the VWAP reclaim; the period (14 assumed) is stated nowhere. The RSI-divergence
  exhaustion signal (R2 §2) likewise carries no period and no divergence
  lookback.
- `GAP-3.10` **Fair Value Gap definition.** R1 requires an FVG and specifies the
  middle-candle direction, the entry edge and the stop anchor, but gives **no
  minimum gap size**, no maximum age before the gap is stale, and no rule for
  which of several open gaps to use.
  **Deliberately left unfilled, 2026-07-30.** FVG has *zero* academic
  literature; the only quantitative claim anywhere is a 70–85% "fill rate" with
  no holding period, no cost, no benchmark and no accounting for the unfilled
  15–30% where the losses live. Filling this gap means inventing a parameter and
  then testing the invention against no prior. See
  [AGENT-SPEC-VERDICT.md](AGENT-SPEC-VERDICT.md) §4.
- `GAP-3.11` **ATR High/Low band construction.** R1 uses "ATR High" and "ATR Low"
  as dynamic support/resistance without stating what they are computed from
  (which anchor, which multiple, which period).
  **Deliberately left unfilled, 2026-07-30** — same reasoning as `GAP-3.10`; the
  bands exist only to serve the FVG entry.
- `GAP-3.12` **Donchian variant selection.** 10, 20 and 55 sessions are all
  given; no rule selects among them, and the exit ("consolidates or mean-reverts
  toward the lower channel") has no numeric trigger.
- `GAP-3.13` **Fibonacci setup completeness.** Only the 38.2% level is given —
  no swing-identification rule, no invalidation level, no alternative ratios.
- `GAP-3.14` **ATR-surge confirmation threshold.** "A measurable surge in ATR"
  confirms a breakout; no multiple or percentage defines *measurable*.
- `GAP-3.15` **20-day breakout exit.** The 20-day high/low entry is measured over
  "the following 20 days" in backtests, but no source states whether 20 days is
  the intended holding rule or merely the measurement window.

### Module 4 — volatility / sizing

- ~~`GAP-4.1` **Risk-per-trade default.**~~ **CLOSED by R1/R2/R3** — the 1% rule
  is stated as a hard cap ("no single trade may risk >1% of total portfolio
  value"). NB's 1%–2% band is now a conflict, not a gap (`CONFLICT-10`).
- `GAP-4.2` **ATR multiple selection rule.** *Narrowed.* R3 gives the direction
  (1.5× for low-noise regimes, 3.0× for high-volatility regimes) but no
  threshold that maps a measured volatility level to a multiplier.
- `GAP-4.8` **ATR period selection rule.** R3 gives horizon bands (2–10 short,
  14 default, 20–50 long) but no rule mapping a holding period to a value.
- `GAP-4.9` **Position-sizing formula.** Two incompatible formulas are given and
  one of them is dimensionally invalid (`CONFLICT-11`). No source reconciles them.
- `GAP-4.3` **Kelly input estimation.** No lookback window or minimum sample for
  estimating `p` and `b`, and no rule for re-estimating them.
- `GAP-4.4` **Maximum concurrent positions.** Not specified; only aggregate heat
  is capped.
- `GAP-4.5` **Sector / correlation exposure caps.** Sector caps and
  correlation-based size scaling are described directionally with **no**
  threshold, correlation cut-off, or scaling coefficient.
- `GAP-4.6` **Drawdown de-risking schedule.** "Reduce size after extended
  drawdowns" carries no trigger level or reduction factor.
- `GAP-4.7` **Trading-days convention for expected move.** `sqrt(DTE/365)` uses
  calendar days; no guidance on a 252-day variant.

### Module 5 — exits

- `GAP-5.1` **Profit-target default.** *Narrowed.* R1/R2 mandate **1:2 as a
  floor**, which removes the 1:1 rung (`CONFLICT-13`). Above the floor, 1:2, 1:3
  and 1:4 all still appear with no rule selecting among them (`CONFLICT-5`).
- `GAP-5.2` **Swing time-stop length.** "Max-hold session limits" are asserted
  with no bar or day count.
- `GAP-5.3` **Partial exits / scale-out.** No fraction, level, or ladder for
  taking partial profit.
- `GAP-5.4` **Trailing-stop activation point.** When the chandelier trail takes
  over from the initial stop is not stated.
- `GAP-5.5` **Stop precedence.** When the ATR stop and the structural pivot stop
  disagree, no rule says which governs.
- `GAP-5.6` **Gap-through-stop handling.** No rule for a stop gapped through at
  the open beyond "budget for slippage".

### Module 6 — options

- `GAP-6.1` **IV percentile.** *Narrowed, still open.* R3 names IV Percentile
  alongside IV Rank as a relative-value gauge, but **defines only IV Rank** and
  attaches **no percentile threshold**. The buy/sell levels (70 / 30) belong to
  IV Rank alone.
- `GAP-6.2` **Roll trigger.** "Approaches the short strike" / "deep in-the-money"
  — no delta, moneyness %, or DTE trigger in any source.
- `GAP-6.3` **Assignment handling.** *Narrowed.* R2 gives the three conditions
  that raise assignment probability (ITM at expiry, time value depleted,
  pre-ex-dividend) and R1 gives the response (roll Up/Down/Out). Still missing:
  when to *accept* assignment, and any post-assignment re-entry rule.
- `GAP-6.9` **IV vs HV threshold.** The `IV > HV` / `IV < HV` gate has no
  buffer — no gap size at which the comparison becomes actionable rather than
  noise. HV window is exemplified as 30 days but not fixed.
- `GAP-6.4` **Long-option DTE for non-momentum setups.** Only 0–5 DTE (momentum)
  and 30–45 DTE (short premium) are given.
- `GAP-6.5` **Covered-call position sizing.** How many contracts to write
  against a position larger than 100 shares, and whether to write against the
  whole holding, is not specified.
- `GAP-6.6` **Spread widths.** Credit/debit spread and iron-condor wing widths
  are never given.
- `GAP-6.7` **Profit-taking on short premium.** No "close at X% of max profit"
  rule (the common 50% convention appears nowhere in these sources).
- `GAP-6.8` **Options liquidity gates.** No minimum open interest, volume, or
  options-specific spread limit.

### Module 7 — execution

- `GAP-7.1` **"Highly liquid" definition.** The market-order exemption depends
  on it; no ADV, dollar-volume, or depth threshold is given.
- `GAP-7.2` **Limit-order offset.** No tick or bps offset from mid/bid/ask, and
  no repricing or timeout policy.
- `GAP-7.3` **ETF-specific screens.** No minimum AUM, and no premium/discount to
  NAV tolerance.
- `GAP-7.4` **Participation rate.** No cap on order size as a fraction of ADV.
- `GAP-7.5` **Slippage buffer.** Gap-down slippage must be budgeted; no size is
  given for the buffer.
- `GAP-7.6` **Index-rebalance / removal handling.** No rule for a position whose
  name leaves the index while held.

### Module 8 — validation

- `GAP-8.1` **IS/OOS split rule.** Only worked examples; no ratio rule.
- `GAP-8.2` **Monte Carlo resampling method.** Trade resampling vs block
  bootstrap is not distinguished, so serial dependence handling is undefined.
- `GAP-8.3` **Walk-forward fold count.** The refit *interval* (6 months) is
  given; the number of folds and the window type (rolling vs expanding) are not.
- `GAP-8.4` **Multiple-testing correction.** No deflated Sharpe, White's reality
  check, or trial-count adjustment appears in these sources.
- `GAP-8.5` **Purged / embargoed cross-validation.** Not covered; no fold count,
  purge length, or embargo period.
- `GAP-8.6` **IC threshold.** "Positive and stable" with no numeric floor and no
  rolling-window length.
- `GAP-8.7` **Minimum out-of-sample trade count.** The 300-trade floor is not
  stated to apply per-split.
- `GAP-8.8` **Triple-barrier / meta-labeling.** No barrier widths, vertical
  barrier horizon, or probability threshold.
- `GAP-8.9` **ML hyperparameters.** "Hundreds of trees" is the only value;
  `max_depth`, `min_samples_split`, `min_samples_leaf` are all absent.
- `GAP-8.10` **Cost level.** Costs are required but no bps figure is given by
  these sources.

---

## CONFLICTS

Where two sources give different values. Both are shown; neither is silently
preferred.

| ID | Parameter | Value A | Value B | Notes |
|---|---|---|---|---|
| `CONFLICT-1` | Stage-analysis moving average | 30-week / 150-day (NB p.805, 856) | 40-week / 200-day (NB p.799, 856) | A is the Weinstein original; B is the institutional-benchmark adaptation. Not reconciled. |
| `CONFLICT-2` | Breakout volume multiplier | 2×–3× the 20-period average (NB p.857) | 40–50% above average, i.e. 1.4×–1.5× (NB p.786) | **Three-way.** Value C: **10% above the 50-day average**, i.e. 1.10× (R1 §3, R2 §1) — a different multiplier *and* a different baseline window. A is 20× stricter than C. R1 additionally argues volume should be **de-emphasised entirely** in favour of FVG + ATR interaction, calling it "an inconsistent endorser". |
| `CONFLICT-3` | Opening-range window | 5 minutes (NB p.400, 859) | 15–30 minutes (NB p.402, 859) | Changes both the range width and the trade count materially. |
| `CONFLICT-4` | Option premium stop | −30% of entry premium (NB p.504) | −50% of entry premium (NB p.506, 873) | Same source cluster, two levels. |
| `CONFLICT-5` | Profit-target RRR | 1:1, 1:2, 1:3 (NB p.437) | 1:2 to 1:4 (NB p.447) | Overlapping but non-identical ladders; no selection rule (`GAP-5.1`). |
| `CONFLICT-6` | Dual-momentum lookback | 12-month single lookback (NB p.227, 866) | Average of 3, 6 and 12 month (NB p.206) | Different signals, not a range. Weighting for B unspecified (`GAP-2.7`). |
| `CONFLICT-7` | Maximum drawdown pass level | `< 20%` (NB p.420) | `< 30%` (NB p.461) | A 10-point spread in the deployment gate. |
| `CONFLICT-8` | Long-option delta | 0.50 ATM (NB p.873) | 0.50–0.70, slightly ITM (NB p.506) | B is a superset; A is stated as the default. |
| `CONFLICT-9` | Options regime gate | **IV Rank** > 70 sell / < 30 buy (NB p.872, 873) | **IV vs HV**: `IV > HV` sell, `IV < HV` buy (R1 §1, R2 §4, R3 §4) | Two different measurements, not two levels of one. IV Rank is IV against its own 52-week range; IV-vs-HV is IV against realised volatility. They routinely disagree — a name can sit at IV Rank 20 with IV still above HV. No source reconciles them or ranks one above the other. |
| `CONFLICT-10` | Risk per trade | **1% hard cap** — "no single trade may risk >1%" (R1 §5, R2 §6, R3 §7) | 1.0%–2.0% band (NB p.437, 473, 880) | A forbids what B's upper half permits. Three of four directly-read sources say 1%. |
| `CONFLICT-11` | Position-size formula | `shares = (equity × risk%) / (ATR × multiplier)` (NB p.876) | `size = risk_amount / (ATR × asset_price)` (R1 §5, R3 §5) | Not two parameterisations of one formula — they disagree structurally. **B is dimensionally invalid**: dividing dollars by (dollars × dollars) yields 1/dollars, not a share count. B is recorded because the sources say it; it is not implementable as written. |
| `CONFLICT-12` | Covered-call return metrics | Period return: `premium / (stock_price - premium)` (NB p.874) | **Annualised** static and if-called return (R2 §5) | Same metric names, different time normalisation. A 45-DTE covered call reports ~8× apart between the two. |
| `CONFLICT-13` | Minimum risk-reward | 1:2 **mandated minimum** on every entry (R1 §5, R2 §6) | 1:1 permitted (NB p.437) | A forbids the 1:1 rung of `CONFLICT-5`'s ladder. |

### `CONFLICT-9` is resolved by rejecting both sides, 2026-07-30

Not a house pick between A and B. **The researched quantity is neither.** The
documented economic mechanism is the volatility risk premium — implied vol
against **subsequently realised** vol, which on S&P 500 options exceeds it
roughly 85% of the time by 2–4 vol points.

- **B (IV vs HV)** compares IV to *past* realised vol. It is a proxy for the VRP
  only to the extent volatility is persistent.
- **A (IV Rank)** is purely self-referential — IV against its own 52-week range —
  and has no mechanism attached to it at all. The 70/30 thresholds have no
  evidentiary support found anywhere.

**If an options gate is ever built, compute the VRP directly**: implied vol at
entry against realised vol over the contract's actual life, on the names this
book actually holds. Do not adopt either source construct. See
[AGENT-SPEC-VERDICT.md](AGENT-SPEC-VERDICT.md) §5.

---

## UNVERIFIED PERFORMANCE CLAIMS — do not quote

The `Research/` briefings carry backtest figures. They are recorded here so the
spec is complete, and fenced here so they never leak into a result.

**None of these came from `rhengine/alpha/book.py`. None may be quoted as a
performance number, cited in a README, or used to justify promoting a default.**
See [CLAUDE-RESEARCH-RULES.md](CLAUDE-RESEARCH-RULES.md) §1 — this material sits
*below* the "legacy trade lists" tier, because unlike those it cannot even be
re-run.

| Claim | As stated | Source |
|---|---|---|
| GLD, 20-day high breakout | +0.86% average gain over the following 20 days | R1 §6, R2 §1, R3 §6 |
| GLD, same setup | 53% win ratio; average gain 4.1% vs average loss 2.9% | R1 §6, R3 §6 |
| GLD, + volume ≥ 1.10× 50-day average | average gain improves to 1.25% | R1 §3 |
| GLD, gain composition | "a significant portion" is an overnight edge (close → next open), attributed to a "monetary inflation tailwind" | R3 §6 |
| SPY, 20-day high breakout | +1.15% average gain | R1 §6 |
| SPY, 20-day **low** breakout | +1.45% — the downside outperforms the upside | R1 §6 |
| TSLA, 2024-07-23 | IV run-up into Q2 earnings, exit band $240–270, followed by a 12% next-session plunge | R3 §4 |
| WMT | breakout above $55 from a multi-year $37–51 consolidation, ran to ~$105 before RSI exhaustion | R3 §6 |
| META | 38.2% retracement near $500 offered the entry after a large surge | R3 §6 |

### Why none of this is usable as evidence

1. **No cost assumption.** Not one figure states a commission, spread, or
   slippage charge. This repo charges 30 bps round trip.
2. **No sample size, no date range, no universe.** Two tickers, unstated
   history, unstated number of trades. The spec's own floor is
   `min_trades >= 300` (Module 8) and nothing here demonstrates it.
3. **No in-sample / out-of-sample split**, no walk-forward, no null model —
   all of which the same corpus demands in Module 8. The claims violate the
   validation protocol they ship with.
4. **Two-asset selection.** GLD and SPY were reported; the universe they were
   selected from is not, so the survivorship of the *claim* is unknown.
5. **Case studies are not evidence.** WMT, META and TSLA are three named
   winners with hindsight-chosen levels.

### The arithmetic, on their own numbers

The GLD figures are at least internally consistent:
`0.53 × 4.1% − 0.47 × 2.9% = +0.81%`, against the stated +0.86%. That implies a
gross profit factor of ~**1.59** — clearing the spec's own `> 1.25` bar.

Charge this repo's 30 bps round trip against each trade and the same numbers give
~**1.34**. Charge the illiquid-decile 98 bps and it goes below 1.0. The edge, as
described, is roughly the size of its own transaction costs. That is the single
most important thing to know before building any of it, and no source states it.

---

## Status in this repo

*Not source content — this section maps the spec onto code that exists here, and
onto results already recorded. Added so nobody re-implements a rule this project
has already tested and shelved.*

| Module | Where it lives | Standing |
|---|---|---|
| 1 — stage classification | `stageFilter` in `rhengine/indicators.py` | **Implemented, default OFF.** Slope proxy `mean(closes[-150:]) > mean(closes[-160:-10])` (a house answer to `GAP-1.4`). residualmomentum OOS +0.026 vs +0.044 baseline; bollinger pooled OOS −0.057R. **Was "underpowered, not disproven"; that reading is withdrawn 2026-07-30.** Zakamulin ran the nearest testable relative (MA market timing) over 155 years with rolling and expanding OOS windows and found no significant outperformance in the second half — and showed the best-known positive result in that literature was look-ahead bias. Stop attributing the null to sample size. **Do not retest on longer history** |
| 2 — RRG / rotation | `rrgMode` in `rhengine/indicators.py` | **Implemented as a proxy, default OFF.** `RS-Ratio = 100 * RS / SMA(RS,60)` on daily bars — a substitute for the unavailable JdK formula (`GAP-2.1`). OOS +0.031 vs +0.044 baseline. A weekly, z-scored variant is untested |
| 2 — dual momentum | not built | Sector-ETF dual momentum has no implementation here. The research stack's own timing work (`alpha/timing.py`, RMX-D) is a **rejected** 10-month trend gate — see `docs/RMX-RESULTS.md` |
| 3 — TTM squeeze | `squeezeBars` + `calc_keltner_squeeze()` | **Implemented, default OFF.** Never won a training fold; squeeze events are rare in 400d |
| 3 — breakout volume | `volMult` (spec max 3.0) | **Implemented.** Walk-forward flip-flopped 3.0 → 1.1 across folds; pooled OOS PF 1.00. No stable value |
| 3 — VWAP | `score_vwap()` | **Already stricter than the spec** (anchored-VWAP reclaim + 9/21/50 EMA stack + RSI reset). Nothing to build |
| 3 — ORB | not built | Intraday; this engine is daily-bar |
| 3 — Donchian / Turtle | not built | 20-day high/low channel breakout. Closest existing code is `score_consolidation()`, which is a different construction |
| 3 — Fair Value Gap | **not built** | R1's preferred validation layer, and the one piece of genuinely new machinery in the `Research/` corpus. Needs a 3-candle gap detector; nothing here does it |
| 3 — ATR High/Low bands | not built | Blocked on `GAP-3.11` — the sources never say how the bands are constructed |
| 3 — Fibonacci 38.2% | not built | Blocked on `GAP-3.13` — no swing-identification rule |
| 4 — 1% risk rule | `--risk-pct` in `rhengine/portfolio.py` | **Already supported.** Set it to 1, not 2, to match `CONFLICT-10`'s A-side |
| 4 — ATR sizing + heat | `rhengine/portfolio.py`, `--max-heat-pct 10` | **Implemented and kept on.** This is a risk control, not an edge claim — it stays regardless of backtest outcome. Current defaults (1.5 stop / 3.0 chandelier) sit inside the source range |
| 4 — Kelly | not built | Fractional Kelly has no implementation |
| 5 — exits | `rhengine/indicators.py` exits, `config.json` | Stops/trails implemented; stage-based exits ride on `stageFilter` |
| 6 — options overlay | **not built** | The only module with a documented mechanism (the volatility risk premium), and the work is a **measurement, not a strategy**. **IV cannot be fetched from this broker at all** (spiked 2026-07-30: `get_option_quotes` returns `implied_volatility: null` and null greeks; `get_option_historicals` is OHLC only), so it is **derived** instead — `rhengine/market_data_options.py` inverts Black-Scholes for IV and computes realised vol and VRP, 17 tests. Option price history *is* retroactive, including for expired contracts; the only perishable asset is the ability to enumerate old contracts, so the standing job is a contract-UUID registry, not an IV snapshot (**not built**). Order: provider → measure VRP on RMX-B's ~96 holdings net of the options spread → only then model upside truncation against RMX-B's +1.78%/yr alpha (BXM's last decade, ~⅓ of the S&P, is the prior to beat). Do **not** build an IV-Rank gate; see the `CONFLICT-9` resolution above and [AGENT-SPEC-VERDICT.md](AGENT-SPEC-VERDICT.md) §5. Build order in [notebooklm-findings.md](notebooklm-findings.md) §3.4 |
| 7 — execution / liquidity | `rhengine/execution.py`, `config.json` `execution.spread_gate_pct` / `min_dollar_volume` / `exclude_etfs` | **Spread gate implemented 2026-07-30** at the spec default 1.0%, plus the block-routing gates. LIVE PATH ONLY — daily bars carry no bid/ask, so no recorded backtest changes. Measured: it rejects **8.3–8.9% of the book by trade count** across both quote captures, concentrated in the least-liquid ADV decile (14/29 names both days) and inert above ~$6M/day — so `min_dollar_volume` = $500k does **not** imply a sub-1% spread. `order_type()` returns limit always (`GAP-7.1` unfilled, so the market-order exemption cannot be evaluated). Reproduce: `py scripts/rh_spreads.py gate` |
| 8 — validation | `rhengine/optimize.py`, `montecarlo.py`, `alpha/validation.py`, `alpha/stats.py` | Implemented and **stricter than this spec** — see the conflicts below |

### Where this repo's rules override the spec

These are not conflicts between sources. They are places where the repo has
evidence and the spec does not, and **the repo governs**:

1. **Costs.** Spec says only "costs must be non-zero" (`GAP-8.10`). This repo
   charges **30 bps round trip** from `config.json` → `costs.round_trip_bps`,
   measured from 296 live quotes. Do not lower it. See
   [CLAUDE-RESEARCH-RULES.md](CLAUDE-RESEARCH-RULES.md) §6.
2. **Statistics.** Spec's `t > 2.0` is computed on trades. This repo requires
   **non-overlapping monthly returns with Newey-West standard errors** plus a
   **deflated Sharpe at the true trial count** — a trade-level `t = +15.69` here
   became `t = +2.87` once clustered by day. Spec's `min_trades >= 300` is
   necessary, not sufficient.
3. **Multiple testing.** Absent from the spec (`GAP-8.4`), mandatory here.
4. **Null models.** Spec compares against zero. This repo requires beating
   random-selection, random-timing and block-bootstrap nulls
   (`alpha/validation.py`).
5. **Ablation.** Spec ranks leg subsets by profit factor to *select*. Here,
   in-sample ablation **may confirm, never select** — sleeve membership is
   decided on the published record first (research rules §7).
6. **Long-only screening.** Any sleeve candidate is screened `--long-only`.
   Long-short records overstate this book by 2–20×.
7. **Sizing units.** R-multiples are not returns. Any performance figure comes
   from `rhengine/alpha/book.py` and nowhere else.
8. **Backtest figures in the sources.** The `Research/` briefings report GLD and
   SPY breakout returns. Those are quarantined in
   [UNVERIFIED PERFORMANCE CLAIMS](#unverified-performance-claims--do-not-quote)
   and are not evidence for anything here.

### If you implement from this file

1. Every rule lands as a **threshold-gated leg or filter, default OFF**.
   Engine output at spec defaults must stay bit-identical — `fixtures/` golden
   vectors are the guard.
2. Register every tunable in `rhengine/strategies.json`
   (`default` / `min` / `max` / `step`).
3. Never touch the scoring math in `rhengine/indicators.py` without being asked.
4. Promotion bar: beat baseline on the **pooled out-of-sample** line with a
   stable per-fold winner. In-sample wins with OOS losses stay off.
5. Volume legs need consolidated volume — Yahoo or Massive/SIP. Alpaca IEX
   volume is ~2–3% of consolidated and invalidates them.
