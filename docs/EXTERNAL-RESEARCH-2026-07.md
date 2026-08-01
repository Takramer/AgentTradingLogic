# External research — what actually still pays, 2026-07-25

> **BUILT — see `docs/RMX-C.md` for results.** Sections 1-4 are the research.
> Section 5 was the plan; it has since been implemented and partly **corrected
> by the data**:
>
> - The published panel is on disk (389 series, 1926-2026) —
>   `scripts/fetch_external_factors.py`, `rhengine/alpha/external.py`.
> - The Chen & Welch collapse reproduces on our copy: median **36.5 → 14.4 →
>   13.8 bp/month** (pre-2005 / post-2005 / post-2015).
> - The **seasonality sleeve proposed in §4 was rejected before being built**:
>   `osap:MomSeason` is −8.7 bp/month, t = −0.19 since 2015. Issuance
>   (`ShareIss5Y`) replaced it.
> - The **reversal recast in §5.3 was mis-specified** as a weekly 5-day signal;
>   the record it was screened on is monthly one-month reversal. Corrected, and
>   then dropped from the default blend anyway — it retains only 0.37 of its
>   return ex-micro and does not cover its turnover.
> - Vol-scaling (§2.2) is implemented and verified on French's 1927-2026
>   momentum factor, but is **off by default**.

Deliberately independent of this repo. Nothing below was chosen because it
resembles RMX-B, `indicators.py`, or anything in `strategies.json`. The last
section is the only part that touches this project.

---

## 0. The one finding that reframes the whole search

**Chen & Welch, "What Useful Alphas?" (arXiv 2607.06502, July 2026)** —
~200 published long-short equity anomalies, re-measured by sample and by size:

| sample | median monthly long-short return |
|---|---|
| all stocks, through 2005 | **48 bp** |
| top-90% market cap, through 2005 | 26 bp |
| all stocks, post-2005 | 19 bp |
| **top-90% market cap, post-2005** | **7 bp** (median t = 0.45, 67% positive) |

Their conclusion, verbatim: *"published academic anomalies have been useless to
non-micro-cap portfolio managers in the 21st century."*

**Chen & Velikov (JFQA 2023), "Zeroing in on the Expected Returns of
Anomalies"** — 204 anomalies net of effective spreads and post-publication
decay: post-2005 mean net long-short return ≈ **−1 bp/month** as originally
implemented, ≈ **+4 bp/month** under cost-minimising execution. Gross
combination strategies worth 250–380 bp/month through 2005 fall to **0–20 bp
net** afterwards.

### What this means for the search

1. Do **not** go looking for a new cross-sectional signal inside the S&P 500.
   The category is measured, and its median is ~1%/yr gross before costs with a
   t-stat under 0.5. Any single new price-or-accounting signal found on
   large caps is, prior to evidence, a coin flip.
2. The surviving alpha is in **micro/small caps** (where costs eat it) and in
   **combinations** (where correlation, not signal strength, is the lever).
3. The three levers with a real evidence base are therefore **breadth, blending,
   and risk scaling** — not signal discovery.

That is a hard result and it is the correct starting point for anything below.

---

## 1. Ground-truth datasets — screen ideas here before writing code

These replace "run my own backtest and hope" with "check against a published,
survivorship-free, peer-reviewed panel". All free.

| source | what you get | access |
|---|---|---|
| **Open Source Asset Pricing** (Chen & Zimmermann, *Critical Finance Review* 2022) | **212 published predictors**, portfolio returns + long-short returns, Oct-2025 release; 319 characteristics total; plus ~29,000 data-mined accounting signals for null distributions | `pip install openassetpricing` — [OpenSourceAP/CrossSection](https://github.com/OpenSourceAP/CrossSection), [openassetpricing.com](https://www.openassetpricing.com/data/), [mk0417/open-asset-pricing-download](https://github.com/mk0417/open-asset-pricing-download) |
| **Global Factor Data** (Jensen, Kelly & Pedersen, *JF* 2023) | **153 factors × 13 themes × 93 countries**, monthly and daily, capped-value / value / equal weighted, size splits from All-Stocks to Nano-Cap, updated through Dec 2025 | free CSV downloads (CC BY-NC 4.0) at [jkpfactors.com](https://jkpfactors.com/) — stock-level needs WRDS |
| **AQR Data Sets** | QMJ quality factor + **10 quality-sorted long-only portfolios** (US from 1956), BAB, TSMOM, HML-Devil; monthly, updated | [aqr.com/library/data-sets](https://www.aqr.com/library/data-sets) |
| **Ken French** | already in use here | — |

Why this matters more than any strategy below: OSAP's ~29k data-mined signals
give you a **published null distribution** for "how good does a random
accounting ratio look on this data". That is the same job `nulltest.py` does
with random draws, except externally computed and impossible to fit.

The **13-theme clustering** in JKP is the direct tool for the blending lever:
pick one factor per theme and you are picking near-uncorrelated sleeves by
construction, rather than discovering the +0.18 correlation by accident.

---

## 2. Strategies with a real, out-of-sample record — ranked

Sharpes below are from the paper-backtest catalogue in
[paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading)
(40+ strategies, ordered by Sharpe, each with source paper and Python
implementation). Treat them as **gross, in-paper** numbers — they carry every
bias Chen & Welch document. Ranking below is mine, weighted by *evidence
quality × implementability in a cash equity account*, not by headline Sharpe.

### 2.1 Cross-asset trend following / time-series momentum — best evidence per unit of crowding
- TSMOM across bonds/commodities/FX/equities: paper Sharpe **0.576**, monthly.
  Asset-class trend-following **0.502**.
- Evidence spans the Depression, multiple regimes, 100+ years in the long
  reconstructions; it is the most replicated result in the literature outside
  equity momentum.
- **The honest caveat:** managed futures performance since ~2012 has been poor.
  This is a real drawdown-in-the-edge, not a data artifact.
- Production-grade open source: **[pysystemtrade](https://github.com/robcarver17/pysystemtrade)**
  (Rob Carver; now under the `pst-group` org since Jan 2026, maintained by Andy
  Geach) — full trend + carry forecast stack, vol targeting, forecast
  diversification multipliers, live IB execution. This is the single best-written
  systematic codebase to read for **position sizing and forecast combination**,
  independent of whether you ever trade futures.

### 2.2 Volatility-scaled momentum — the highest-confidence *free* Sharpe upgrade
**Barroso & Santa-Clara, "Momentum has its moments" (JFE 2015):** scale the
momentum portfolio by the inverse of its own trailing 6-month realised
volatility.

| | raw momentum | vol-scaled |
|---|---|---|
| Sharpe | 0.53 | **0.97** |
| excess kurtosis | 18.24 | 2.68 |
| skew | −2.47 | −0.42 |
| worst month | −78.96% | **−28.40%** |

Out-of-sample R² for predicting momentum's *own* volatility is 47% — vol is far
more forecastable than returns, which is why this survives where return-timing
does not.

**Do not confuse this with general volatility management.** Moreira & Muir's
market-level vol timing is contested: Cederburg et al. find no systematic
Sharpe gain across 103 strategies out of sample, and both they and Liu et al.
show the original spanning regressions use an unconditional variance that a
real-time investor does not have. DeMiguel et al. (*JF* 2024) give the
multifactor version. My read: **scaling a single high-kurtosis factor by its own
realised vol replicates; timing the market by vol does not.**

### 2.3 Cross-sectional short-horizon reversal — the one thing daily bars are actually good for
- Short-term reversal: paper Sharpe **0.816**, weekly rebalance.
- Reversal *around* earnings announcements: **0.785**, daily.
- Both are cross-sectional ranks over a universe, not per-ticker threshold
  triggers, and both are liquidity-provision premia — they pay in exactly the
  choppy regime a mean-reversion sleeve is meant to cover.
- Cost sensitivity is severe at weekly turnover; this only works with
  cost-minimising execution (Chen & Velikov's +4 bp vs −1 bp gap is the same
  point).

### 2.4 Low-turnover accounting tilts — asset growth and issuance
- Asset growth effect: paper Sharpe **0.835**, *yearly* rebalance — the highest
  Sharpe-per-unit-of-turnover in the catalogue.
- Net share issuance / buybacks: repurchasers outperform issuers; international
  evidence is broad, **but** Fama & French (2016) find the repurchase effect
  disappears under the five-factor model, and the whole family is subject to the
  post-2005 large-cap decay above.
- Practical appeal here is not the Sharpe, it is that `Assets` and
  `CommonStockSharesOutstanding` are near-universal XBRL tags — coverage far
  above the 66% that `op_be` reaches.

### 2.5 Return seasonality — genuinely orthogonal, price-data-only
**Heston & Sadka (JFE 2008):** stocks with high returns in a given calendar
month tend to repeat it, at annual lags 12, 24, 36 … out to **20 annual lags**.
Survives controls for size, industry, factor exposure, earnings announcements,
dividends and fiscal year, one at a time. Keloharju/Linnainmaa/Nyberg found
common factors behind it; international replications exist.

Cheap to compute from data already cached here, and correlated with nothing
else in this repo.

### 2.6 Machine-learned composites — real, but the least honest to claim
**Gu, Kelly & Xiu (RFS 2020):** 900+ predictors, neural nets, **value-weighted**
long-short decile OOS Sharpe **1.35** vs 0.61–0.83 for OLS benchmarks; S&P 500
timing Sharpe 0.77 vs 0.51 buy-and-hold. Stock-level R² only 0.33–0.40% — the
gains come from aggregating many tiny, weak edges, which is the same blending
law RMX-B found, taken to 900 legs.

The catch: it needs CRSP+Compustat breadth, and the value-weighted result is the
only one relevant to a large-cap account. Replications exist
([Tidy Finance](https://www.tidy-finance.org/blog/gu-kelly-xiu-replication/),
[duongtran14 partial replication](https://github.com/duongtran14/Partial-replication-of-Gu-Kelly-Xiu-2020-Empirical-Asset-Pricing-via-Machine-Learning.)).

---

## 3. GitHub repos worth actual time — ranked, with what to take from each

1. **[OpenSourceAP/CrossSection](https://github.com/OpenSourceAP/CrossSection)**
   + `pip install openassetpricing`. Take: 212 predictors' returns as an
   external screening panel, and the data-mined-signal null. Highest value per
   hour of anything on this list.
2. **[pysystemtrade](https://github.com/robcarver17/pysystemtrade)** (`pst-group`).
   Take: forecast scaling/capping, forecast diversification multiplier,
   instrument-level vol targeting, and how a production system separates
   forecast → position → order. Read `docs/`, not the futures plumbing.
3. **[microsoft/qlib](https://github.com/microsoft/qlib)** +
   **[microsoft/RD-Agent](https://github.com/microsoft/rd-agent)**. Take: the
   Alpha158/Alpha360 factor definitions as a ready-made feature set, and Qlib's
   point-in-time data layer + rolling-retrain harness. RD-Agent (NeurIPS 2025)
   reports ~2× the annualised return of Alpha158/360 with 70% fewer factors at
   <$10/optimisation cycle — but the benchmarks are CSI300/CSI500 A-shares, so
   treat it as an *automation* pattern, not a transferable result.
4. **[stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading)**
   (3rd ed., end-to-end workflow, 27 chapters) plus his maintained forks
   `zipline-reloaded` / `alphalens-reloaded` / `pyfolio-reloaded`. Take:
   **alphalens-reloaded** for factor tear-sheets — quantile IC, turnover, and
   decay by holding period, which is exactly what `ic.py` does by hand.
5. **[paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading)**
   — the strategy catalogue with per-strategy Python in `static/strategies/`.
   Take: the ranked shortlist and the paper citations, not the backtests.

Teaching material — technique, not evidence — is catalogued separately in
[REFERENCE-LIBRARIES.md](REFERENCE-LIBRARIES.md) (ML4T chapter map,
awesome-quant, cantaro86, Grzelak, Quant-Guild), with the per-repo licence
terms. Item 4 above is expanded there.

Runners-up worth knowing: [tidy-finance/py-tidyfinance](https://github.com/tidy-finance/py-tidyfinance)
(clean reference implementations of portfolio sorts, beta estimation, FF
regressions — the textbook version of what `rhengine/alpha/` does);
[mk0417/anomalies-replication-python](https://github.com/mk0417/anomalies-replication-python)
(Chen-Zimmermann in Python, 96 portfolio-construction variants — useful for
testing whether a result is construction-robust);
[dgunning/edgartools](https://github.com/dgunning/edgartools) (parses SEC
`companyfacts` with exact filing dates and dimension filtering — this directly
fixes the tag-coverage and filing-date complaints in RMX-RESULTS §"What would
make RMX-QT better");
[RL-MLDM/alphagen](https://github.com/RL-MLDM/alphagen) (KDD 2023 RL formula
search — read the *critiques*: both GP and RL variants over-optimise historical
IC and produce unmotivated, overfitted factors).

---

## 4. Candidate new indicators, ranked by evidence ÷ effort

1. **Own-volatility scaling of a momentum sleeve** — weight ∝ 1/σ̂(6-month
   realised vol of the sleeve's own returns). Barroso-Santa-Clara. Not a signal;
   a sizing rule. Documented Sharpe 0.53→0.97 and worst month −79%→−28%.
2. **Same-calendar-month seasonality rank** — mean return in month *m* over
   annual lags 1..20, cross-sectionally ranked. Heston-Sadka. Price data only,
   orthogonal to everything else.
3. **Asset growth** — YoY Δ total assets, ranked ascending (low growth wins),
   annual. One XBRL tag, near-universal coverage, yearly turnover.
4. **Beta / idiosyncratic-vol tilt (long-only BAB)** — rank by trailing beta,
   hold low-beta names. Validate against AQR's BAB and QMJ 10-portfolio series
   back to 1956 before building anything.
5. **Composite rank ensemble** — average the *z-scored ranks* of N weak sleeves
   rather than picking a winner. This is the Gu-Kelly-Xiu mechanism at N=5
   instead of N=900, and it is the only construction on this page with a
   published reason to expect the combination to beat every leg.

Explicitly *not* recommended: any new per-ticker threshold indicator (MACD
variants, band tags, VWAP anchors, ADX gates). There is no evidence base for
that family at all — it does not appear in OSAP, JKP, or the strategy catalogue,
because nobody has been able to publish a survivorship-free cross-sectional
result from it.

---

## 5. Now the part that touches this repo

### 5.1 The structural verdict

Every survivor in sections 1–4 shares a shape: **rank a universe on a slow
signal, hold cap-weighted for a fixed period, size by volatility.** Nothing that
survived is a per-ticker threshold trigger with ATR stops.

`indicators.py` (1,282 lines) implements the second shape. `rhengine/alpha/`
(~2,100 lines across 12 modules) implements the first. The recommendation is
therefore not "improve indicators.py" — it is **freeze it** (the golden vectors
are the point) and route all new work through `rhengine/alpha/`.

Two specific reasons the old shape suppresses returns rather than just failing
to add:

- **ATR stops truncate the right tail of a momentum sleeve.** Momentum's return
  distribution is negatively skewed with fat positive-carry periods; a 1.5×ATR
  stop converts holding-through-noise into realised whipsaw. Barroso's fix is
  *sizing*, not stopping.
- **Profit factor is the wrong objective for a rank-and-hold book.** PF is
  trade-based; a monthly-rebalanced sleeve has no trades in the PF sense. The
  objective that maps to money is alpha t-stat and information ratio against the
  *cap-weighted* universe — which is already what `alpha/stats.py` computes.
  If PF must go up, it goes up by holding longer and sizing smaller, not by
  adding legs.

### 5.2 Delete

| sleeve | why | action |
|---|---|---|
| `vwap` | never validated, zero trades in the 2025-09..2026-07 book, no evidence base for anchored-VWAP in any replicated panel | remove from `strategies.json`, delete scorer |
| `macd` | MACD-cross has no cross-sectional evidence base; the "OOS PF 1.37" is one window on a sleeve that never traded | remove |
| `consolidation` | already retired; COMP leg measured toxic (median PF 0.69 on vs 1.01 off) | delete scorer, keep only a repro tag |
| `pead` (price-proxy) | edge lived in excluded biotech (−0.05R / PF 0.89 on the clean universe); PEAD in large caps is among the most decayed post-2005 anomalies | remove — do not "upgrade to real SUE", the upgrade target is also dead in this size bucket |
| `residualmomentum` (trade-level) | failed its predeclared kill criterion at every horizon (t = −0.44 / −0.02 at 30 bps) | remove from scan; keep the *cross-sectional* residual idea as an `alpha/` rank experiment only |

That is 5 of 6 legacy sleeves. This is not vandalism — `regime_routing` is
already `{uptrend: null, downtrend: null, choppy: null}`, so none of them trade.
Deleting them removes the surface area where a future routing edit can silently
re-enable an unvalidated sleeve.

### 5.3 Alter

**`bollinger` is the one worth keeping, and it should change shape.** It is the
only legacy sleeve with a day-clustered t > 2 (+3.38, on 52 sessions), and
short-term reversal is one of the few families with a genuine replicated record
(§2.3). But the current form is a per-ticker band tag + RSI + volume + ADX gate
on 52 observations — four thresholds fitted to a sample too small to support one.

Recast it as **`alpha/reversal.py`**: rank the point-in-time universe on
5-day return (or 5-day return residual to SPY), hold the bottom quintile
cap-weighted for 5 days, no stops, weekly rebalance. Same economic claim,
measurable with the machinery in `alpha/stats.py`, and directly comparable to
OSAP's published short-term-reversal series. If it does not beat that published
series, it was the four thresholds, not the idea.

**`RMX-B` gains one line, not one leg:** vol-scale the momentum sleeve's weight
by its own trailing 6-month realised vol, renormalising monthly against the
quality sleeve. This is the §2.2 result. It changes no signal, adds no
parameter that isn't in the paper, and the thing it is documented to fix
(kurtosis 18 → 2.7, worst month −79% → −28%) is precisely what a 190%-turnover
momentum leg exposes you to.

### 5.4 Evolve — the actual path to a better machine

The correlation law RMX-B found (+0.18 between sleeves → blend t exceeds both
legs) is the only lever with an evidence base *and* headroom. Extend it
deliberately, using JKP's 13 themes to pick sleeves that cannot be correlated by
construction:

1. quality / profitability — **have it**
2. momentum, vol-scaled — **have it, add the scaling**
3. short-horizon reversal — the recast `bollinger` (§5.3)
4. ~~seasonality — Heston-Sadka~~ → **rejected on the decay screen**, replaced by
   **issuance** (5-year split-adjusted share-count growth, `osap:ShareIss5Y`:
   47.1 bp/month, t = 2.26 ex-micro since 2015, and the share counts are already
   in the fundamentals table)
5. investment / asset growth — annual, near-zero turnover

Five sleeves at pairwise correlation ~0.2 give roughly √5/√(1+4×0.2) ≈ **1.7×**
the information ratio of one sleeve at the same per-sleeve alpha. That is a
larger, better-evidenced improvement than any single new signal in section 2 —
and it is reachable with signals whose published records you can check *before*
spending a holdout.

**Measured outcome** (`docs/RMX-C.md`): on the published ex-micro records the
five legs came in at mean pairwise correlation **+0.004** — far better than the
0.2 assumed here — giving a realised t-multiple of **2.47×** against a
theoretical 2.54×. Over 2015-2024 the mean leg t is 1.89 and the blend's is
**4.67**. On the local panel the four-sleeve version (reversal dropped) runs at
192%/yr turnover with leg excess-return correlation +0.105.

What it does **not** do is fix breadth. Every factor is stronger in mid/small
caps, and RMX-RESULTS already names this as limit #3. JKP's size splits (down to
Nano-Cap) let you measure how much alpha you are leaving in the size buckets you
cannot cost — before deciding whether to fund the data to reach them.

### 5.5 What not to do, restated in the language of the new evidence

Do not screen 8 more price signals on the S&P 500 panel. Chen & Welch have
already measured the population that search draws from: median 7 bp/month,
median t 0.45, post-2005, large caps. The prior is a coin flip, the holdouts are
spent, and the correct response to a category with a 0.45 median t-stat is to
stop sampling from it.

---

## Sources

- [Chen & Welch, "What Useful Alphas?" (arXiv 2607.06502, 2026)](https://arxiv.org/abs/2607.06502)
- [Chen & Velikov, "Zeroing In on the Expected Returns of Anomalies" (JFQA 2023)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3073681)
- [Jensen, Kelly & Pedersen, "Is There a Replication Crisis in Finance?" (JF 2023)](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249) · [Global Factor Data](https://jkpfactors.com/)
- [Chen & Zimmermann, "Open Source Cross-Sectional Asset Pricing"](https://www.openassetpricing.com/) · [github](https://github.com/OpenSourceAP/CrossSection) · [python downloader](https://github.com/mk0417/open-asset-pricing-download)
- [AQR Data Sets](https://www.aqr.com/library/data-sets) · [QMJ monthly](https://www.aqr.com/library/data-sets/quality-minus-junk-factors-monthly) · [BAB monthly](https://www.aqr.com/Insights/Datasets/Betting-Against-Beta-Equity-Factors-Monthly)
- [Barroso & Santa-Clara, "Momentum Has Its Moments" (JFE 2015)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2041429)
- [Moreira & Muir, "Volatility Managed Portfolios" (NBER w22208)](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf) · [Cederburg et al., "On the performance of volatility-managed portfolios" (JFE)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X) · [DeMiguel et al., "A Multifactor Perspective" (JF 2024)](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13395)
- [Gu, Kelly & Xiu, "Empirical Asset Pricing via Machine Learning" (RFS 2020)](https://academic.oup.com/rfs/article/33/5/2223/5758276)
- [Heston & Sadka, "Seasonality in the cross-section of stock returns" (JFE 2008)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X0700195X) · [Keloharju et al., "Common Factors in Return Seasonalities"](https://www.aqr.com/-/media/AQR/Documents/News/2015/AllSeasons20141219pdf.pdf)
- [awesome-systematic-trading (strategy catalogue)](https://github.com/paperswithbacktest/awesome-systematic-trading)
- [pysystemtrade](https://github.com/robcarver17/pysystemtrade) · [qlib](https://github.com/microsoft/qlib) · [RD-Agent](https://github.com/microsoft/rd-agent) · [ML for Trading 3rd ed.](https://github.com/stefan-jansen/machine-learning-for-trading) · [py-tidyfinance](https://github.com/tidy-finance/py-tidyfinance) · [edgartools](https://github.com/dgunning/edgartools) · [alphagen](https://github.com/RL-MLDM/alphagen) · [anomalies-replication-python](https://github.com/mk0417/anomalies-replication-python)
