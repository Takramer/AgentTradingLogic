# Pitfalls — the bugs that changed published numbers

Every entry here was caught *after* it had already poisoned a result. When
something looks surprisingly good, or a market cap / price / share count looks
wrong, check this list before believing it.

---

## Data integrity

### 1. Split-adjusted prices × unadjusted share counts

Yahoo's `close` is split-adjusted; an SEC share count is not. Multiplying them
made **GE** (1:8 reverse split) a **$1.5 trillion** company in 2016 that pinned
the 10% position cap every year, and hid **Apple** (4:1 split) entirely. 501 of
793 tickers have at least one split.

Worse: the SEC frames API returns whichever filing covered a period, and filers
*restate* prior-year share counts after a split — so some values in the same
column are already adjusted and some are not.

Fixed in `rmxq.adjusted_shares`, which disambiguates by continuity, anchored on a
price-to-sales prior only where the most recent year is itself ambiguous. Every
checked name now lands within 0.89–1.10× of its true cap, against 10× errors
before. **The bug was suppressing the result, not flattering it, and it was still
only caught by checking computed caps against known ones.**

### 2. Share-count units errors (factor of 1,000)

`fundamentals.shares` carries **285 filing units errors** — one fiscal year off
by 1,000× — which `adjusted_shares` could not see, because it only chooses
between `raw` and `raw × split_factor`, and 1,000 is not a split factor.

27 corrupted market caps on the eligible universe, 20 inside the 2010–2018
implementation window:

| ticker | FY | reported shares | implied cap |
|---|---|---|---|
| CLX | 2012 | 130,962,056,000,000 | **$10.9 quadrillion** |
| CCL | 2020 | 932,485,510,000 | $24.6 trillion |
| HBAN | 2010 | 863,338,744,000 | $5.7 trillion |
| WAT | 2023 | 59,176 | $17 million |

Fixed in `rmxq._repair_share_units`. The discriminator is a one-year **spike**,
not a level shift: a reverse split changes every count from that year on, while a
units error makes both neighbours disagree in the same direction. Errors snap to
the nearest power of 1,000 when that lands within 50% of the neighbours; NaN
otherwise.

### 3. Share-count tag priority (the bug underneath)

Fixing spikes left 20 cap-years still wrong — *persistently*, so no neighbour
test could see them. Cause: `SHARES` is coalesced first-present-wins, and the
higher-priority tag routinely carries a placeholder while a lower-priority one
carries the real number.

| ticker | year | tag that won | value | right answer |
|---|---|---|---|---|
| SPG | 2011-12 | CommonStockSharesOutstanding | **8,000** | WeightedAvgDiluted 293,573,472 |
| PCG | 2009 | EntityCommonStockSharesOutstanding | **12,345** | CommonStock 371,272,457 |
| FTI | 2015-16 | both share tags | **1** | 118,900,000 |
| PEG | 2016 | CommonStockSharesOutstanding | **505** | 505,282,421 |

Simon Property, a $47B REIT, was priced at a **$1 million** cap for two years and
got ~zero weight in every cap-weighted book.

Fixed in `fundamentals._pick_shares`: resolve each (ticker, year) against the
ticker's **own** share-count scale — the median plausible value across all three
tags and all years — taking the highest-priority candidate within 4× of it,
snapping by a power of 1,000 when nothing does, NaN when that fails.

Net: 27 corrupted cap-years → **9**. RMX-B's local check moved +0.47%/yr t=+0.35
→ +0.44%/yr t=+0.33 against a benchmark that also moved. RMX-B's headline
(French 1963–2026, t=+2.54) is untouched — no SEC share counts involved.

**The 9 residuals, and why they stay:** `BBBY` (3 yr) has its whole row wrong —
revenue $0.9B vs a real $7.8B, a CIK/ticker mismatch. `KLAC` (2) implies a $5.60
price against a traded ~$52, so the *price series* is suspect. `AIV` (4) split
into AIV + AIRC in Dec 2020 and Yahoo divides earlier closes by the spinoff
ratio, so price and share count are on different bases. All nine err **small**,
so they take ~zero weight rather than dominating.

**The general lesson:** validate computed market caps against known ones. Three
independent bugs all produced caps, all passed every unit test, and all were
found by eyeballing a number that was obviously wrong.

## Statistics

### 4. IID statistics on overlapping trades

The old stack's headline `t = +15.69` became `t = +2.87` once clustered by day.
Use `alpha/stats.py` — non-overlapping monthly returns, Newey-West.

### 5. Uncapitalised R-sums presented as performance

The `+6178R` headline was a zero-cost R-sum with no capital base. Only
`alpha/book.py` produces returns.

### 6. Zero costs by default

Eleven CLIs carried their own `--cost-bps` defaults (0.0 in
backtest/portfolio/optimize/nulltest/montecarlo/ic, 5.0 elsewhere), and
`tests/results/current_full.csv` was forensically confirmed generated at **0
bps**. Now all resolve through `rhengine/costs.py` → `config.json`
`costs.round_trip_bps = 30`.

### 7. Double-counted transaction cost

`cost_in_r`, `backtest._cost_r` and `breakeven_cost_bps` all treated
`cost_bps` as **per side** and doubled it, while their docstrings and every
caller said round trip. A config saying 30 charged 60, and every reported
breakeven was half the true figure. Fixed 2026-07-27 / commit `e753c64`. If you
see a note claiming "the engine doubles it," it is stale.

### 7b. Corwin-Schultz spread estimation does not work here

`estimate_round_trip_bps` returns `None` **always**, deliberately. Validated
2026-07-25 against names with known 1–6 bp spreads: it read SPY at 39.1 bps
against a true ~0.7, median **37× too high**. Two causes — the implementation
discarded negative per-pair estimates instead of zeroing them (so it returned
the median of the upper noise tail), and the method has no power on modern
spreads regardless: a 0.5 bp spread inside a 1% daily range is 1/200th of the
signal being measured. Spread has to come from live bid/ask during **regular**
hours (after-hours books quote ~10× wide). `_corwin_schultz_unvalidated` is kept
only so the validation table regenerates.

### 7c. "No edge" that is really a malformed input

`breakeven_cost_bps` used to return `None` both for a book with no edge and for
input where no row carried usable `entry`/`stop`/`r_multiple`. Handed the
`mc_refined` harvests (which record `r_multiple` but not entry/stop) it reported
"no edge" for every sleeve, including ones at +0.14 gross mean R. It now raises
`ValueError` on the second case. Do not read a finding into a broken input.

### 8. Survivorship-biased universe chosen by looking at the metric

Measured, not asserted: equal-weight over priced PIT members beat real RSP by
**+0.86 pp/yr** (2003–2026). Apply that haircut to any "vs SPY" figure.

### 9. Two sources of truth able to disagree silently

The regime routing table was hardcoded in `indicators.py` while
`strategies.json` `regime_routing` sat unread (audit FC-20). Pinned by
`tests/test_strategy_status.py`. When you add a spec field, make exactly one
side own it.

## Construction

### 10. Screening long-short, building long-only

Overstates by 2–20×. It is how the `EBM` sleeve got built and shipped before
being caught. Always `--long-only`.

### 11. Screening one signal, building another

The reversal sleeve was built as a 5-day/weekly formation while screened against
OSAP's monthly `STreversal`. Result: 1,311%/yr turnover, 11.1% cost drag, a
negative book. Preserved as `sleeves.reversal_fast`, research only.

### 12. Reading a non-monotone table as an effect

`--band 0.40` looked like +0.68%/yr; bands 0.25 and 0.30 were *worse* than
baseline. A cost mechanism cannot be non-monotone, so the differences were which
names happened to be held. The arithmetic confirmed a wash.

### 13. Testing crash insurance on a sample with no crash

Vol-scaling changed nothing on 2010–2018 — because that window contains no
momentum crash. It was verified on French's momentum factor instead
(`scripts/verify_vol_scaling.py`): Sharpe 0.46→0.65, excess kurtosis 27.9→11.7
over 1927–2026. Off by default anyway, so the docs keep describing what runs.

## Environment

### 14. `python` is not on PATH

Use `py`. `tzdata` is required (installed). Stooq is blocked; Yahoo works.

### 15. Notebook kernel staleness

After editing `rhengine/`, **Restart Kernel** — Run All alone keeps the old
module and silently reproduces the previous result.

### 16. Unicode in headless notebook runs

`$env:PYTHONUTF8='1'` before `py -m jupyter execute`. Notebooks are ASCII-only to
keep this path safe; keep them that way.

### 17. ETFs in an equity scan

The single biggest scan-quality win was excluding US-listed ETFs via the Nasdaq
directory blocklist (`etf_blocklist.json`, `universe_hygiene.py`, config
`exclude_etfs: true`). Fixture-safe. Also available: `exclude_adr`,
`exclude_biotech`.

### 18. Polymarket bars have `volume = 0`

Cut the volume legs via `legMask` or every volume-confirmed strategy silently
scores zero.

### 19. FMP free tier

Bulk lists return 402. Spot-checks only. Nasdaq directory is the real hygiene
source; Dolt is the earnings SQL API.

### 20. `tests/results/refined_trades_*bps.csv` predate the 2x cost fix

Those files were written **2026-07-27 15:10**; the transaction-cost
double-count fix (`e753c64`) landed at **19:34 the same day**. So they were
generated while the engine charged `cost_bps` twice: the file named
`refined_trades_30bps.csv` reflects a **60 bps** round trip, not 30. The
filename lies.

They are gitignored artifacts, so nothing in the repo pins them. Two live
consequences:

- Anything read off them is charged at double its label. Regenerate before
  quoting a number from one.
- `scripts/rh_spreads.py report` extrapolates sleeve net-R across a cost grid
  and evaluates it at `tw / 2.0`, with the comment "the grid is in per-side
  units". That halving is *correct against these stale files* and would become
  a 2x error the moment they are regenerated post-fix. The block is currently
  unreachable (the 5/11/20 bps grid files do not exist, so it hits
  `FileNotFoundError` and returns), which is why nobody caught it. Fix the
  `/2.0` and the CSVs in the same change or neither.

### 21. Spread units: the quoted spread IS the round trip

`(ask - bid) / mid` is already a round-trip cost — a market buy pays a
half-spread above mid, the exit sale pays a half-spread below it, and two
halves make one spread. Do **not** multiply by 2 to "annualise it to a round
trip". Doing so reports this universe at 58 bps against a measured 29.1.

Pinned by `tests/test_execution.py`, which reconstructs quotes from the ADV
deciles `costs.py` records (5.5 / 29.1 / 98.1 bps) and requires the bridge to
return them unchanged — a measured check, not an asserted one.

### 22. Deep-ITM and far-OTM options carry no recoverable implied vol

All of an option's volatility information lives in `price - intrinsic`. Two
regimes destroy it, and both return a plausible number if unguarded:

- **Far OTM, short dated** — the price underflows to exactly `0.0` in float,
  because `math.erf` saturates past |x|≈6 and both normal CDFs return a hard
  zero.
- **Deep ITM, short dated** — a 100/70 call with two weeks left is worth ~30.04
  of which the time value at 35% vol is ~1e-12, roughly 300 ULPs of 30.
  Inverting that returns 0.3488 against a true 0.35.

`market_data_options.has_vol_information` is the single predicate for both, and
`implied_vol` returns `None` rather than a fabricated vol. A VRP study that
fills unresolvable contracts with a default is measuring its own default.

Related: converge the inverter on the **vol bracket**, never on an absolute
price residual. A 1e-8 price tolerance leaves a cheap contract's vol off by
0.0017 — a twentieth of the entire published 2–4 point VRP.

## Rejected — do not re-propose without new evidence

| Thing | Why it died |
|---|---|
| RSI-2 mean reversion | Rejected in the 2026-07 diagnosis |
| VIX term-structure gate | In-sample mirage (`vix_utils`) |
| RMX-D trend/absolute-momentum timing | 1/11 gates OOS, 4th percentile of 200 permutations |
| RMX-Q equal-weighted | −3.3 pp/yr size drag swamps a +1.6 pp signal |
| Residual momentum at the cited horizon | rank-IC t = +0.35 |
| 8 price-only cross-sectional signals | best \|t\| = 0.89 |
| Seasonality (Heston-Sadka) | −8.7 bp/mo, t = −0.19 since 2015 |
| EBM value sleeve | Long-only screen negative in all three windows |
| Net payout yield | +0.85 correlated with `issuance` |
| Reversal sleeve | 0.37 ex-micro retention; the premium is micro-cap |
| Turnover bands / quarterly momentum | Cost saved ≈ gross given up; a wash |
| New single S&P 500 signals generally | 7 bp/mo post-2005, median \|t\| 0.45 |
| Weinstein stages / `stageFilter` retest on longer history | Zakamulin: 155 years, proper OOS, nothing. The null is not a power problem |
| ORB (opening-range breakout) | Published edge assumes **zero spread**, at the widest moment of the day |
| Fair Value Gaps, ATR bands, Donchian, Fibonacci | No academic literature; blocked on spec gaps deliberately left unfilled |
| IV Rank / IV-vs-HV options gates | Neither is the researched quantity — the VRP is IV vs *subsequently* realised vol |
