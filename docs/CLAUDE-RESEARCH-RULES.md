# Research rules — the evidence contract

Read before reporting any number, adding any signal, or claiming any edge.
These are not style preferences. Each one exists because it was violated and
cost a result. Authority: `docs/AUDIT-2026-07-24.md`, `docs/RMX-PREREGISTRATION.md`.

---

## 1. Where evidence is allowed to come from

**Ranked, best first:**

1. **Published records** — `research_data/external/` (389 monthly series,
   1926–2026: OSAP, JKP, AQR, Ken French). Nothing in this project was fitted to
   them. This is where every surviving claim came from.
2. **Ken French portfolios** — survivorship-free, 1963–2026. RMX-B's t=+2.54 is
   here and nowhere else.
3. **The local PIT panel** (`alpha/panel.py` + `alpha/book.py`) — 8 years, S&P
   500. Good for *implementation checks*: turnover, cost drag, name counts,
   correlations, "does the code do what the spec says." **Never for
   significance.**
4. **Legacy `rhengine/` trade lists** — diagnostic only. Every headline from this
   path was withdrawn by the audit.

**Every claim that survived came from an external record; every one that died
came from the local 8-year panel.** Screen the record *before* spending panel
time or holdout.

## 2. The statistics that count

- Compute on **non-overlapping monthly returns with Newey-West standard errors**
  (`alpha/stats.py`). Never on trade counts. The old stack's `t = +15.69` was
  `t = +2.87` once clustered by day.
- **Deflated Sharpe at the true trial count.** Count every trial, including the
  ones you abandoned.
- **R-multiples are not returns.** A zero-cost uncapitalised R-sum (the old
  `+6178R` headline) means nothing. Performance comes from `alpha/book.py`:
  daily mark-to-market, one-session execution lag, turnover cost, −30% delisting
  return, margin charged above 1.0×.
- **Beat your own null**, not just SPY. `alpha/validation.py` gives random
  selection, random timing, stationary block bootstrap, purged folds,
  drop-best-months. The old stock-selection rule sat at the **53rd percentile of
  random**; profitability sits at the 100th in-sample and 88th out.
- **Survivorship is measured, not asserted.** Equal-weight over priced PIT
  members beat real RSP by **+0.86 pp/yr** (2003–2026). Every "vs SPY" figure
  carries that haircut explicitly.

## 3. Power — know what the sample can resolve before you run it

`scripts/rmxqt_power.py`. A ~1.2 pp/yr alpha against a 4.5 pp/yr tracking error
over 7–8 years has a standard error of ~1.6 pp/yr. So `+1.2%` in one window and
`−1.7%` in the next are **the same draw**, not a contradiction, and neither is
evidence.

| true alpha/yr | TE | expected t over 7.5 yr | years for t=2 |
|---|---|---|---|
| +1.00% | 4.53% | 0.60 | 82 |
| +1.19% | 4.53% | +0.72 | 58 |
| +2.00% | 4.53% | 1.21 | 21 |

102 months against a ~4%/yr TE resolves nothing. Say so, in the same paragraph
as the number, every time.

## 4. Screen the construction you are actually building

The sleeves are **long-only** and cannot short. Measuring them against
long-short published records overstates by 2–20×:

| window | long-short (ex-micro) | long-only, ex-market |
|---|---|---|
| 1970–2024 | 55 bp/mo, t=7.7 | 20.1 bp/mo, t=5.2 |
| 2010–2018 | 53 bp/mo, t=3.4 | −5.7 bp/mo, t=−1.01 |
| 2015–2024 | 61 bp/mo, t=4.1 | 2.6 bp/mo, t=0.35 |

That error is load-bearing: it is how the `EBM` sleeve got selected, built with
seven unit tests, and shipped before being caught. Use
`py -m rhengine.alpha.external --long-only`, never `--blend`.

Same class of error, mechanical version: the reversal sleeve was first built as
a **5-day formation on a weekly calendar** while screened against OSAP's
`STreversal`, which is a *monthly* Jegadeesh (1990) signal. It drove turnover to
1,311%/yr and 11.1% cost drag. **Match the construction to the record you
screened it on, or the screen was about a different strategy.**

## 5. Anomaly decay — the prior you start from

Reproduced on our own copy (`--decay`, median across 389 series):
pre-2005 **36.5 bp/mo** → post-2005 **14.4** → post-2015 **13.8**. Chen & Welch
report 48 → 19 on ~200 OSAP long-shorts. Same collapse.

Published large-cap anomalies pay ~7 bp/mo post-2005 with median |t| 0.45.
**Stop screening new single S&P 500 signals.** The lever that works is
diversification: five near-uncorrelated legs reach t=4.67 while no leg reaches
2.5, and the realised t-multiple (2.47×) matches theory
√(k/(1+(k−1)ρ̄)) = 2.54×. Alphas add; tracking errors add in quadrature.

Also check the size cut (`--micro`): median ex-micro retention is **0.72**. The
textbook reversal premium retains only 0.37 — it is a micro-cap premium and this
book cannot collect it.

## 6. Costs

- `config.json` → `costs.round_trip_bps = 30`, resolved through
  `rhengine/costs.py`. Every CLI declares `--cost-bps default=None` and routes
  through `resolve_cost_bps`, so exactly one place decides what "unspecified
  cost" means.
- **Everything is ROUND TRIP** — in and out, total. If you need a per-side
  number, name it `per_side_bps` and convert at the call site. `cost_in_r`,
  `backtest._cost_r` and `breakeven_cost_bps` all silently doubled the value
  until 2026-07-27; a config saying 30 charged 60.
- 30, not 5: `min_dollar_volume` is $500k/day, where round-trip half-spread ×2
  plus impact is routinely 30–100+ bps. 5 bps is a mega-cap number.
- Cost matters more here than the bps suggest, because R is normalised by the
  ATR stop. At a ~4.2% median stop, **1 bp ≈ 0.0024 R against a ~0.08 R gross
  edge** — about **33 bps round trip erases it completely**.
- Measured breakeven on `current_full.csv` is **11.2 bps incremental**.
- The 30 is measured, not assumed: 296 live Robinhood quotes stratified across
  ADV deciles, reweighted to the real 120,406-trade book, gave **29.1 bps**
  (28.7 spread + 0.4 fees) — but it ranges **5.5 bps in the most liquid decile
  to 98.1 in the least**, and this book trades all of them at roughly equal
  frequency. Explicit broker fees are negligible (0.396 bps round trip).
- **It is still a scalar.** The per-name liquidity-scaled route
  (`estimate_round_trip_bps`, Corwin-Schultz) was validated and rejected —
  it reads ~37× too high. Every reported number carries a flat-cost assumption;
  say so.
- Actual quoted spread measured mid-session: 29.1 bps (2026-07-27), 24.6
  (2026-07-28) — a ~15% swing at the same clock time. Headline stays at the
  conservative 29.1. **The opening spread, which is what the engine actually
  fills at and is the widest of the day, is still unmeasured.**
- Measuring real fill cost is worth more than any turnover-reduction tweak
  (see §7).

### 6a. The flat 30 bps is the LEGACY universe's number. Do not apply it to a book.

Added 2026-08-01, and it corrects a claim this file's own §6 invites.

30 bps is calibrated on `universe_filters.min_dollar_volume = $500k/day` — the
legacy engine's scan universe, which is mostly small and micro caps. The
research-stack books are a different population: **RMX-C's weight is 92.3%
mega/large-cap, median holding $18.6bn**, measured by
`scripts/cap_cost_profile.py`. Charging it 30 bps is not conservatism, it is
the wrong tier's number, and it is wrong by about an order of magnitude.

Per-name cap-tiered spreads (`costs.CAP_TIERS`, applied through
`costs.bps_for_caps` and `book.run_book`'s per-name path):

| tier | cap | round-trip bps | RMX-C weight |
|---|---|---|---|
| mega/large | $10bn+ | 1–5 | **92.3%** |
| mid | $2–10bn | 5–20 | 5.7% |
| small | $0.3–2bn | 20–100 | 0.1% |
| micro | <$300m | 100–600 | 0.0% |

RMX-C's effective cost, turnover-weighted: **2.76 bps** (bracket 1.27–6.03).

| cost model | eff bps | CAGR | alpha vs CW | t |
|---|---|---|---|---|
| flat 30 bps | 30.00 | 12.79% | **−0.09%** | −0.11 |
| flat 10 bps (published) | 10.00 | 13.18% | +0.21% | +0.26 |
| **tier point** | **2.76** | **13.33%** | **+0.32%** | +0.39 |

So the flat 30 was charging the book **0.54 pp/yr it does not pay** — more than
twice its whole measured alpha — and was the single largest reason RMX looked
marginal. **Cost is not the binding constraint on the research books.** It
remains the binding constraint on the legacy engine, where 30 bps is the right
tier and ~33 bps erases the gross edge entirely.

Rules:
- Books get `--cost-model tier-point`, bracketed with `tier-lo` / `tier-hi`.
  A conclusion that needs `tier-lo` has not survived.
- The benchmark must be re-costed under the **same** model. Charging the
  strategy a new cost and leaving the cap-weighted universe on the old one
  manufactures alpha out of bookkeeping, and since the benchmark trades far
  less it is the direction that flatters.
- `CAP_TIERS` is a published **prior**, not your fills. It agrees with what
  this repo has measured (SPY 0.7, INTC 1.5, BABA 3.0 bps round trip; 5.5 bps
  in the most liquid ADV decile), but the **opening** spread — what the engine
  actually fills at, and the widest of the day — is still unmeasured.

## 5b. The universe is not the constraint — settled 2026-08-01

The 7 bp/month post-2005 figure in §5 is for the **top 90% of market cap**,
against 19 bp for all stocks, and that gap reads like an argument for trading
smaller names. It is not.

Benchmark each size quintile's signal portfolio against **that bucket's own
cap-weighted return** — French 5×5 size × signal, survivorship-free, 1963–2026
— and the blend excess is flat across the whole ladder: ME1 +3.01, ME2 +2.66,
ME3 +3.05, ME4 +2.02, **ME5 BIG +2.24** %/yr. The 7-vs-19 gap is the size
premium plus microcap effects, not signal strength, and the size premium is a
free risk factor rather than an edge.

Post-2005 every bucket decays together; momentum's famous small-cap gradient
(+10.20%/yr in ME1 pre-2005) is **gone**; and quality's gradient now runs
*backwards*, working best in BIG. Full account and cost sweep in
[SIZE-LADDER-2026-08-01.md](SIZE-LADDER-2026-08-01.md).

**Consequence:** "the edge is small because we only trade the S&P 500" is a
rejected hypothesis. Do not fund mid/small-cap data acquisition on it, and do
not benchmark a small-cap book against the total market — that measures SMB.

## 6b. Data provenance — stamp the panel, not just the cost

A number is defined by three things: the **window**, the **cost**, and the
**data**. Sections 3 and 6 govern the first two. The third went unstamped until
2026-08-01, and it cost a published result.

- `research_data/_panel_cache.pkl` is 150 MB, gitignored, mutable and rebuilt
  **in place**. There is no history and no snapshot. A refresh silently
  invalidates every figure computed against the old cache.
- That is not hypothetical. docs/RMX-C.md published RMX-C at 13.15% CAGR and
  later asserted it "reproduces exactly" — while admitting turnover came out
  175% against the table's 170%, which cannot both be true. The current panel
  gives 13.18%. The RMX-C code path is byte-identical across the whole period,
  so the drift is data; the panel was rebuilt on 2026-08-01 00:37 and **the
  old one no longer exists, so it can never be attributed further.**
- **Quote `panel_fp` with any number you report.** `Panel.fingerprint(lo, hi)`
  is a window-scoped digest of the `adj` and `close` matrices the backtest can
  see. `rmxb`, `blend` and `mc_books` print it; `live.emit()` carries it in the
  payload; `mc_books` writes it to a `.meta.json` beside every returns CSV.
- It is **window-scoped on purpose**: the ordinary weekly append must not move
  the stamp on a 2010-2018 result, or readers learn to ignore it. A changed
  stamp always means a changed backtest.
- A table without a stamp predates the fix. It is **historical, not
  reproducible** — re-measure before quoting it, and say which panel you got.
- `panel_last_bar` is not a substitute. It says how *fresh* the data is; the
  fingerprint says *which* data it is, and a refetch can revise history
  without moving the last bar.

## 7. In-sample ablation may confirm, never select

`py -m rhengine.alpha.blend --ablate` exists to check *construction* choices —
turnover, cost, whether a leg double-counts another — against an already-spent
window. A sleeve is added or dropped on its **published record first**; the
ablation may only agree.

The rule is real precisely because it cuts against convenience:

- Dropping `reversal` raised alpha t by 0.83 and halved turnover — and that was
  already implied by two external facts (0.37 ex-micro retention, ~1,200%/yr
  turnover). Acting on it was legitimate *only* because the record agreed.
- The local window prefers **4 blocks**; the published record prefers **5**
  (with `payout`). They disagree and **the published record governs.**

Worked example of a local result refuting itself: the `--band 0.40` turnover
control appears to add +0.68%/yr at 30 bps, but bands 0.25 and 0.30 are *worse*
than baseline. A cost mechanism cannot be non-monotone. The arithmetic confirms
it — 15% of the book in the band gives up ~0.25 pp/yr gross to save ~0.20 pp/yr
of cost. **A wash at 30 bps, a net loser at 10.**

## 8. Holdouts

`docs/HOLDOUT.md` is append-only. Never edit a past entry.

- **W1 development** 2024-07-18 → 2025-12-31 — unrestricted.
- **W2 quarantined** 2026-01-01 → 2026-07-20 — already viewed. Use once as a
  consistency check. A pass is weak evidence; a failure is strong evidence.
  Never quote as out-of-sample.
- **W3 true holdout** 2026-07-21 onward — grows one session per day. Gate: no
  promotion to paper trading until ≥125 sessions under a config frozen before
  2026-07-24.
- RMX-D and RMX-Q carry their own splits; **both are spent.** RMX-D failed
  1/11 gates out-of-sample (−6.22 pp/yr, alpha t=+0.07, 4th percentile of 200
  permutations) and no parameter was changed in response. That is the correct
  handling — copy it.
- A config change after a W2/W3 look resets that window's value to **zero**.
  No partial credit.

## 9. What must not happen

- Tuning RMX-D or RMX-Q against their out-of-sample results and re-reporting them.
- Selecting sleeve membership on `--ablate` output and calling it edge.
- Quoting significance for a factor tilt on a decade of data.
- Reporting an in-sample number without its out-of-sample fate in the same table.
- Silently changing the shipped default so the docs describe something that is
  no longer running (this is why vol-scaling, `--band` and quarterly momentum
  are all implemented, verified, and **off**).

## 10. Report a rejection as fully as a result

Five hypotheses were pre-registered; four were rejected, and the rejections are
the most valuable content in `docs/RMX-RESULTS.md`. When something dies, record
the numbers that killed it, keep the code out of the defaults, and leave the
reasoning error visible — `docs/RMX-C.md` keeps its own long-short mistake in
the document with a correction block on top, because the error is more useful
visible than deleted.
