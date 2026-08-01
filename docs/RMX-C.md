# RMX-C — the multi-sleeve blend, and what the published record says about it

**2026-07-25.** Built from `docs/EXTERNAL-RESEARCH-2026-07.md`. Code:
`rhengine/alpha/sleeves.py`, `rhengine/alpha/blend.py`,
`rhengine/alpha/external.py`. Data: `scripts/fetch_external_factors.py`.

> ## CORRECTION, same day — read this before the rest of the document
>
> Everything below the "five sleeves" heading measures the sleeves against
> **long-short** published records. The sleeves are **long-only** and cannot
> short. That overstates the evidence by a factor of two to twenty, and the
> error is load-bearing: it is how `EBM` got selected, built and shipped as a
> fifth sleeve before being caught.
>
> Measured correctly — top-quintile value-weighted long-only, in excess of the
> market, via the new `external.long_only` — the four sleeves earn:
>
> | window | long-short (ex-micro) | **long-only, ex-market** |
> |---|---|---|
> | 1970-2024 | 55 bp/mo, t=7.7 | **20.1 bp/mo, t=5.2** (+2.4%/yr) |
> | 2010-2018 | 53 bp/mo, t=3.4 | **-5.7 bp/mo, t=-1.01** |
> | 2015-2024 | 61 bp/mo, t=4.1 | **2.6 bp/mo, t=0.35** (+0.3%/yr) |
>
> The 55-year result is real and is still the reason to run this. The last
> decade paid **nothing**, and the honest reason is not that the rankings
> stopped working — the top quintile still beat the middle quintile by
> 0.6%/yr — but that SMB and HML were both about -2%/yr, so every tilt away
> from mega-cap growth was fighting the index.
>
> Three consequences, all applied:
> 1. `value` (EBM) is **out** of `DEFAULT_SLEEVES`; `payout` (Boudoukh et al.
>    PayoutYield) is **in**, the only one of nineteen candidates that beat the
>    market long-only in all three windows.
> 2. Sleeve selection now runs through `--long-only`, never `--blend`.
> 3. A separate share-count units bug was found while validating the new sleeve
>    and fixed — see "The units bug" at the end.
>
> The sections below are left as written, because the reasoning error is more
> useful visible than deleted. Treat their magnitudes as long-short numbers.

---

## What was added

**A published-record panel.** 389 monthly factor series, 1926-2026, on disk in
`research_data/external/` (134 MB), from four free sources:

| source | content | span |
|---|---|---|
| OSAP (Chen & Zimmermann) | 212 published predictors: long-short, VW quintiles, and a ME>NYSE-20th-pct screen | 1926-2024 |
| JKP (Jensen, Kelly & Pedersen) | 153 USA factors in 13 themes, capped-VW and EW | 1926-2025 |
| AQR | QMJ, BAB, HML-Devil (US), TSMOM | 1926/1957- |
| Ken French | FF5, momentum, ST/LT reversal, 25 OP×INV portfolios | 1926- |

**The rule this enforces:** screen a candidate against the published record
*before* spending panel time or holdout on it. Every claim in RMX-RESULTS that
survived came from an external record; every one that died came from the local
8-year panel.

```
py scripts/fetch_external_factors.py
py -m rhengine.alpha.external --catalog --min-t 2
py -m rhengine.alpha.external --decay          # my era?
py -m rhengine.alpha.external --micro          # my size bucket?
py -m rhengine.alpha.external --blend a b c --ex-micro
```

## Chen & Welch reproduced on our own copy of the data

`py -m rhengine.alpha.external --decay`, median across all 389 series:

| | bp/month |
|---|---|
| pre-2005 | **36.5** |
| post-2005 | **14.4** |
| post-2015 | **13.8** |

Chen & Welch (2026) report 48 → 19 bp on ~200 OSAP long-shorts. Same collapse,
independently computed here. **124 of 389 series are still positive with t > 1
since 2015.**

And the size cut, `--micro`, median ratio of ex-micro to all-stocks return
across 211 OSAP signals: **0.72**. Most published anomalies keep ~70% of their
return in an investable universe — but the worst offender is the one that
matters most below.

## The five sleeves, and which survived contact

`rhengine/alpha/sleeves.py`. All identical in shape: rank the PIT eligible
universe, hold the top quintile cap-weighted, 10% name cap, execute next close,
**no stops**. No two share a JKP theme.

Published record, OSAP long-short **ex-micro**, via `--blend --ex-micro`:

| sleeve | OSAP analogue | 1970-2024 bp/mo | t | 2015-2024 bp/mo | t |
|---|---|---|---|---|---|
| quality (`op_be`) | GP | 36.0 | 2.74 | 106.5 | 3.13 |
| momentum (12-1) | Mom12m | 105.5 | 4.17 | 76.7 | 1.57 |
| reversal (1-month) | STreversal | 49.5 | 2.46 | 47.2 | 1.24 |
| investment (asset growth) | AssetGrowth | 71.5 | 5.42 | 45.6 | 1.26 |
| issuance (5y share count) | ShareIss5Y | 47.6 | 4.83 | 47.1 | 2.26 |
| **equal-weight blend** | | **62.0** | **8.87** | **64.6** | **4.67** |
| mean pairwise correlation | | +0.004 | | −0.025 | |

**That table is the whole thesis.** Over 2015-2024 the mean leg t is 1.89 and
no leg reaches 2.5 — exactly the population Chen & Welch measured. The blend
reaches t = 4.67 because the correlations are ~zero. The realised t-multiple
(2.47×) matches the theoretical √(k/(1+(k−1)ρ̄)) = 2.54×.

### Two sleeves I proposed and the data rejected

- **Seasonality (Heston-Sadka same-calendar-month).** Strong full-sample
  record — but `osap:MomSeason` earns **−8.7 bp/month, t = −0.19 since 2015**.
  Dropped before it was built. This is what the decay screen is for.
- **Net payout yield.** Good record (64.7 bp, t 3.69 ex-micro) but **+0.85
  correlated with the issuance sleeve** — same theme, no diversification.
  Dropped in favour of issuance, which needs no new data.

## Implementation on the local panel, 2010-07 .. 2018-12

`py -m rhengine.alpha.blend`, 10 bps round trip, vs the cap-weighted covered
universe (survivorship- and size-neutral):

| config | CAGR | Sharpe | maxDD | turnover | alpha vs CW | t |
|---|---|---|---|---|---|---|
| SPY | 12.50% | 0.87 | −19.4% | — | — | — |
| CW covered universe | 12.71% | 0.87 | −19.9% | — | — | — |
| RMX-B (2 sleeves, shipped) | 13.52% | 0.87 | −23.6% | 189% | +0.47%/yr | +0.35 |
| RMX-C, 5 sleeves | 13.46% | 0.86 | −21.9% | 382% | −0.26%/yr | −0.22 |
| **RMX-C, 4 sleeves (no reversal)** | **13.60%** | **0.88** | **−21.6%** | **192%** | **+0.61%/yr** | **+0.62** |
| RMX-C, 4 sleeves @ 30 bps | 13.16% | 0.86 | −21.6% | 192% | +0.28%/yr | +0.29 |

Leg excess-return correlation vs the CW universe, mean pairwise **+0.105**,
theoretical IR multiple 1.74×.

**Nothing here is significant and nothing here was ever going to be.** 102
months against a ~4%/yr tracking error cannot resolve a 0.6%/yr alpha. These
numbers are an implementation check: does the code do what the specification
says, at what turnover and what cost. The evidence is the 1970-2024 table above.

### The reversal sleeve does not pay rent, and this was predictable

`py -m rhengine.alpha.blend --ablate` (leave-one-out, `d_alpha_t > 0` means the
dropped sleeve was helping):

| dropped | CAGR | Sharpe | alpha vs CW | t | turnover | d_alpha_t |
|---|---|---|---|---|---|---|
| — (all 5) | 13.46% | 0.86 | −0.26% | −0.22 | 382% | 0.00 |
| −quality | 12.89% | 0.80 | −1.40% | −0.82 | 476% | **+0.61** |
| −momentum | 13.17% | 0.85 | −0.54% | −0.39 | 435% | +0.18 |
| **−reversal** | 13.60% | 0.88 | +0.61% | +0.62 | **192%** | **−0.83** |
| −investment | 14.06% | 0.88 | +0.25% | +0.18 | 441% | −0.40 |
| −issuance | 13.40% | 0.85 | −0.29% | −0.23 | 399% | +0.02 |

Dropping reversal raises alpha t by 0.83 and halves turnover. Crucially, that
decision does **not** rest on this in-sample run — it was already implied by two
external facts:

1. `STreversal` retains only **0.37** of its return ex-micro, the worst of the
   top 20 signals by t-stat. The textbook reversal premium is a micro-cap premium.
2. A monthly-rotating quintile turns over ~1,200%/yr one-way. The ex-micro
   long-short premium is ~5.9 pp/yr gross; the long leg gets less than half of
   that, and cost takes a large bite of what remains.

The ablation agrees with the published record. That is the only configuration in
which an in-sample ablation result should be acted on.

### A specification error caught by the record

The reversal sleeve was first built as a **5-day formation on a weekly
calendar**. That is not the signal the evidence covers — OSAP's `STreversal` is
Jegadeesh (1990) one-month reversal, rebalanced monthly. The weekly version drove
the blend to **1,311%/yr turnover and 11.1% total cost drag**, turning the book
negative. It is preserved as `sleeves.reversal_fast`, research only. The lesson
is mechanical: **match the construction to the record you screened it on**, or
the screen was about a different strategy.

## Volatility scaling: implemented, verified, and off by default

`rmxb.py --vol-scale-momentum`, and `sleeves.scale_by_own_vol`. Barroso &
Santa-Clara (JFE 2015): weight momentum by the inverse of its own trailing
6-month realised volatility.

On the local 2010-2018 panel it changes almost nothing (alpha t +0.35 → +0.34,
maxDD −23.57% → −23.47%). That is not evidence against it — **the window
contains no momentum crash.** Testing crash insurance on a sample with no fire
proves nothing.

So it was verified on French's momentum factor instead,
`py scripts/verify_vol_scaling.py`:

| window | | Sharpe | excess kurtosis | worst month |
|---|---|---|---|---|
| 1927-2026 | raw | 0.46 | 27.85 | −52.6% |
| | vol-scaled | **0.65** | **11.71** | **−47.2%** |
| 1970-2026 | raw | 0.47 | 9.45 | −34.4% |
| | vol-scaled | **0.62** | **4.36** | **−25.0%** |
| 2005-2026 | raw | 0.12 | 15.48 | −34.4% |
| | vol-scaled | **0.29** | **3.09** | **−19.2%** |

The mechanism replicates, including post-2005. Magnitudes are smaller than the
paper's 0.53 → 0.97 for two deliberate reasons: monthly rather than daily
observations for the vol estimate, and the multiplier capped at 1.5 because a
retail account does not borrow at the T-bill rate.

**Default is OFF in `rmxb.py`.** RMX-B as documented in RMX-RESULTS is the 50/50
blend with the 63-year record; silently changing the shipped strategy would make
the docs describe something that is no longer running.

> **Correction, 2026-08-01.** That sentence was read for ten months as covering
> RMX-C too, and it does not. `blend.py` defaults `--vol-scale` to
> `["momentum"]` — **on**. CLAUDE.md asserted the opposite, so the doc and the
> code disagreed about what RMX-C *is*.
>
> Resolved in favour of the code, because the code is what produced the
> published numbers. At 10 bps over 2010-07..2018-12 the 5-block row reproduces
> **exactly** with vol-scaling on (13.15% CAGR, 15.39% vol, Sharpe 0.86,
> maxDD −21.19%) and gives **13.03%** with it off. Turning it "back" off would
> have made every table below unreproducible — the exact failure research rules
> §9 forbids.
>
> So: **RMX-C's momentum leg is vol-scaled by default; RMX-B's is not.** The
> `--vol-scale` help text now says so, and a bare `--vol-scale` disables it.
> (One residual mismatch, unexplained and small: the table below records 170%
> turnover where the current code reports 175% on with / 167% off.)

## Where the 2015 cutoff actually belongs

Harvesting stopped at nothing — the published files are full history at no extra
cost, and truncating them would throw away the only samples long enough to
measure anything. 2015-2026 is 138 months; against a 4-5%/yr tracking error that
resolves an alpha of about ±1.4%/yr at one standard error. It cannot establish
significance for a factor tilt and never will.

What 2015+ **is** good for, and what it is used for here:

- correlation between sleeves (converges in ~5 years — this is the number the
  whole blend depends on, and it is measurable)
- turnover, cost drag, capacity, name counts
- "is this signal dead in my era" — the `--decay` screen's `bp_recent` column

What it is **not** good for: a holdout. The 2019-2026 window is already spent on
RMX-D and RMX-Q. There is no unspent local holdout left, which is precisely why
the published record has to carry the evidential weight.

## What must not happen

Tuning sleeve membership on `--ablate` output and reporting the result as edge.
The ablation harness exists to check construction choices — turnover, cost,
whether a leg is double-counting another — against a spent window. A sleeve is
added or dropped on its **published** record first; the ablation may only
confirm.

## The corrected screen, and the fifth sleeve

`py -m rhengine.alpha.external --long-only <candidates> --vs GP Mom12m
AssetGrowth ShareIss5Y`. Nineteen candidates, three windows, long-only
top-quintile excess over the market. Change in the blend's t-stat from adding
each as a fifth block:

| candidate | 1970-2024 | 2010-2018 | 2015-2024 | verdict |
|---|---|---|---|---|
| **PayoutYield** | **+0.38** | **+0.44** | **+0.37** | **shipped** |
| Tax | +0.10 | +0.45 | +0.03 | positive everywhere, adds ~nothing twice |
| EarningsConsistency | −0.39 | +0.54 | +0.55 | best modern, negative long-run |
| CF | +0.81 | +0.13 | −0.41 | best long-run, fails recent |
| NetPayoutYield | +0.54 | −0.03 | +0.23 | +0.64 correlated with `issuance` |
| EBM | −0.06 | −0.20 | −0.12 | **rejected — see below** |
| BM | −0.17 | −1.16 | −0.80 | plain book-to-market is the worst of them |

`PayoutYield` (Boudoukh, Michaely, Richardson & Roberts, JF 2007) is the only
one positive in all three windows, and the only one whose standalone long-only
leg also beat the market in all three (+16.7 / +9.5 / +19.0 bp/month). Its one
flaw is real: buybacks raise payout yield *and* shrink share count, so it
correlates +0.45 with the `issuance` sleeve on the published record — the highest
pair in the blend. On the local panel that correlation comes out at only +0.14.

**Effect sizes here are small and the search was wide.** Nineteen candidates x
three windows; a `d_t` of +0.4 is at the edge of what selection alone produces.
This sleeve is in on consistency plus a well-cited paper, not a decisive number.

### The sleeve that was built and rejected

`EBM` (enterprise book-to-market, Penman, Richardson & Tuna 2007) was selected on
the ex-micro **long-short** screen, where it adds +0.51 blend t over 1970-2024
and +0.54 since 2015 with the best ex-micro retention of any valuation signal
(0.91). It was implemented in full (`sleeves.value_ebm`, seven unit tests
including the both-terms-negative ratio trap and the fiscal-year-end pricing
check) and then failed the long-only screen in all three windows: −0.06, −0.20,
−0.12, with a standalone leg of −2.6 bp/month since 2015. Its premium is
substantially a short-side premium and this account cannot collect it.

The local ablation agreed independently — `d_alpha_t` −1.28, the worst of any
sleeve tried — so the published record and the panel rejected it together. The
code is kept, out of `DEFAULT_SLEEVES`, because the rejection is worth being able
to reproduce. **The rule that failed was not "check the record", it was "check
the record for the construction you are actually building."**

## Turnover controls: both implemented, both a wash, both off by default

The blend turns over 175%/yr one-way. At the 30 bps this universe is modelled at
(`config.json`) that is ~0.53%/yr of cost against a size-adjusted modern edge of
+0.26%/yr — cost is about twice the edge. So reducing turnover looked like the
one lever with a certain payoff. It is not.

Two controls were built (`--band`, `--momentum-calendar`, `--turnover-sweep`):

| config | turnover | alpha @10bps | t | alpha @30bps | t |
|---|---|---|---|---|---|
| baseline (strict quintile, monthly mom) | 175% | +0.16% | +0.19 | −0.14% | −0.17 |
| band 0.25 | 144% | −0.08% | −0.10 | −0.31% | −0.41 |
| band 0.30 | 129% | +0.02% | +0.03 | −0.18% | −0.25 |
| **band 0.40** | **107%** | **+0.70%** | **+1.01** | **+0.54%** | **+0.79** |
| quarterly momentum | 101% | +0.03% | +0.04 | −0.13% | −0.15 |
| band 0.30 + quarterly momentum | 84% | −0.07% | −0.08 | −0.20% | −0.25 |

Band 0.40 appears to win by +0.68%/yr at 30 bps. **It does not, and the table
contains its own refutation:** the pattern is non-monotone — 0.25 and 0.30 are
both WORSE than baseline. A cost mechanism cannot do that. Trading less always
saves cost monotonically, so a middle that loses means the differences are
dominated by which names happened to be held in this window.

The arithmetic confirms it. Widening the hold band to the top 40% held 181 names
against 154 fresh, so 15% of the book sits in the band. On the published VW
quintiles the rank-4 bucket earns 13.9 bp/month less than rank-5 (1970-2024) and
12.8 bp/month less (2015-2024), consistently across all five sleeves. So:

    gross given up   15% x 13.9 bp/mo   =  -0.25 pp/yr   (-0.23 pp/yr since 2015)
    cost saved       68 pp turnover x 30 bps  =  +0.20 pp/yr   (+0.07 at 10 bps)

**The two are the same size.** The band is a wash at 30 bps and a net loser at
10. The local +0.68%/yr was noise, exactly as the non-monotone pattern implied.

Quarterly momentum is the same story with a bigger turnover cut (175% -> 101%,
saving ~0.22 pp/yr) and no measurable local effect. Its gross cost cannot be
measured on data held here — OSAP's `Mom12m` is a monthly rebalance and no
quarterly analogue is on disk — but Jegadeesh & Titman's own tables show the
12-1 premium falling materially at a three-month holding period, which is very
likely larger than 0.22 pp/yr. It is not adopted on that basis.

**Both stay OFF by default.** `--band 0.40` and `--momentum-calendar quarterly`
exist so the measurement is reproducible, and because the band is genuinely
attractive if real execution cost turns out to be well above 30 bps: the wash
tips in its favour at roughly 40 bps and above. Measuring actual fill cost is
therefore worth more than either control.

**What this rules out.** The signal decays at about the rate the cost is saved,
so the strategy is already near the efficient frontier of its own construction.
There is no free lunch in trading less, and construction tweaks cannot
manufacture a modern edge — this is the second consecutive negative result after
equal-weighting and beta-targeting (`scripts/ff_largecap_blend.py`).

## The units bug

Found while validating `payout_yield`: `WAT` priced out at a 445% payout yield.
The cause was not in the sleeve. `fundamentals.shares` carries **285 filing
units errors** — a share count off by a factor of 1,000 for one fiscal year —
and `adjusted_shares` could not see them, because it only ever chooses between
`raw` and `raw x split_factor` and 1,000 is not a split factor.

On the eligible S&P 500 universe that produced **27 corrupted market caps, 20 of
them inside the 2010-2018 window used for every implementation check in this
document**:

| ticker | fiscal year | reported shares | implied market cap |
|---|---|---|---|
| CLX | 2012 | 130,962,056,000,000 | **$10.9 quadrillion** |
| CCL | 2020 | 932,485,510,000 | $24.6 trillion |
| HBAN | 2010 | 863,338,744,000 | $5.7 trillion |
| WAT | 2023 | 59,176 | $17 million |
| DLR | 2024 | 336,637 | $59 million |

A $10.9-quadrillion name pins the 10% position cap and squeezes every other
weight in that year's book. Same class as the $1.5-trillion GE in
`Panel.split_factor_after`.

Fixed in `rmxq._repair_share_units`. The discriminator is a one-year **spike**,
not a level shift: a reverse split changes every count from that year on and is a
real corporate action the split logic already handles, while a units error makes
both neighbours disagree in the same direction. Detected errors snap to the
nearest power of 1,000 when that lands within 50% of the neighbours, and are
dropped to NaN when it does not.

### The second bug, underneath the first

Fixing the spikes left 20 cap-years still wrong, all *persistently* rather than
for one year — so the neighbour test could not see them. The cause was not units
at all but **tag priority**: `SHARES` is coalesced first-present-wins, and the
higher-priority tag routinely carries a placeholder while a lower-priority one
carries the real number.

| ticker | year | tag that won | value | tag that was right |
|---|---|---|---|---|
| SPG | 2011-12 | CommonStockSharesOutstanding | **8,000** | WeightedAvgDiluted = 293,573,472 |
| PCG | 2009 | EntityCommonStockSharesOutstanding | **12,345** | CommonStock = 371,272,457 |
| FTI | 2015-16 | both share tags | **1** | — (real: 118,900,000) |
| GRMN | 2013-16 | CommonStockSharesOutstanding | in **thousands** | EntityCommon, where present |
| PEG | 2016 | CommonStockSharesOutstanding | **505** | — (real: 505,282,421) |
| LEN | 2009 | WeightedAvgDiluted | **170,537** | — (thousands) |

Simon Property, a $47B REIT, was priced at a **$1 million** market cap for two
years and therefore received ~zero weight in every cap-weighted book.

Fixed in `fundamentals._pick_shares`: resolve each (ticker, year) against the
ticker's **own** share-count scale — the median of every plausible value across
all three tags and all years — taking the highest-priority candidate that sits
within 4x of it, snapping by a power of 1,000 when nothing does, and NaN when
that fails too. All six cases above now resolve to the correct figure and share
coverage is unchanged (2,078 names in 2010, 4,008 in 2022).

Net effect: **27 corrupted cap-years -> 9.** RMX-B's local check moves from
+0.47%/yr t=+0.35 to **+0.44%/yr t=+0.33** against a benchmark that also moved
(CW universe 12.71% -> 12.44%/yr). Neither is significant and neither ever was.
RMX-B's headline (French 1963-2026, alpha t=+2.54) is untouched — no SEC share
counts involved. All 156 tests pass.

**Residual 9, and why they stay:** `BBBY` (3 years) has its whole row wrong —
revenue $0.9B against a real $7.8B — which is a CIK/ticker mismatch, not a share
count. `KLAC` (2) has plausible shares and an implied price of $5.60 against a
traded ~$52, so the *price series* is suspect. `AIV` (4) is legitimate: it split
into AIV + AIRC in December 2020 and Yahoo's split-adjusted history divides the
earlier closes by the spinoff ratio, so price and share count are on different
bases and no share-count fix can reconcile them. All nine err SMALL, so they take
~zero weight rather than dominating. Resolving them needs a second share-count
source and a price-series cross-check.

## Reproducing

```bash
py scripts/fetch_external_factors.py                  # 134 MB, ~1 min
py scripts/fetch_sec_fundamentals.py --only PaymentsForRepurchaseOfCommonStock \
       PaymentsOfDividendsCommonStock PaymentsOfDividends \
       PaymentsForRepurchaseOfEquity \
       PaymentsForRepurchaseOfPreferredStockAndPreferenceStock
py -m rhengine.alpha.external --decay --top 25
py -m rhengine.alpha.external --micro --top 20

# THE screen for adding a sleeve — long-only, the construction actually built
py -m rhengine.alpha.external --long-only PayoutYield Tax EarningsConsistency \
       EBM BM CF NetPayoutYield --vs GP Mom12m AssetGrowth ShareIss5Y

py scripts/verify_vol_scaling.py
py -m rhengine.alpha.blend --lo 2010-07-01 --hi 2018-12-31   # 5 blocks, default
py -m rhengine.alpha.blend --ablate --lo 2010-07-01 --hi 2018-12-31
py -m rhengine.alpha.rmxb --lo 2010-07-01 --hi 2018-12-31 --vol-scale-momentum
py -m pytest tests/test_sleeves.py -q                        # 29 tests
```

## Local implementation check, 5 blocks with `payout`, 2010-07 .. 2018-12

All figures below are **after** both share-count fixes (see "The units bug").

| config | CAGR | Sharpe | maxDD | turnover | alpha vs CW | t |
|---|---|---|---|---|---|---|
| SPY | 12.50% | 0.87 | −19.4% | — | — | — |
| CW covered universe | 12.44% | 0.86 | −19.5% | — | — | — |
| RMX-B (2 sleeves, shipped) | 13.22% | 0.86 | −22.2% | 189% | +0.44%/yr | +0.33 |
| **RMX-C, 5 blocks (quality/momentum/investment/issuance/payout)** | **13.15%** | **0.86** | **−21.2%** | **170%** | **+0.16%/yr** | **+0.19** |
| RMX-C, 4 blocks (no payout) | 13.31% | 0.87 | −20.6% | 198% | +0.56%/yr | +0.59 |
| RMX-C, 5 blocks with `value` (EBM) instead | 12.85% | 0.83 | −21.8% | 168% | −0.58%/yr | −0.66 |

Leg excess-return correlation vs the CW universe, mean pairwise **+0.107** on 5
blocks against +0.157 on 4 — `payout` correlates +0.12 / +0.11 / −0.19 / +0.08
with quality / momentum / investment / issuance, the lowest of any leg, and it is
the only leg that lowers the blend's mean correlation. It also cuts turnover from
198% to 170%/yr, because it is an annual sort diluting two annual and one monthly.

Leave-one-out `d_alpha_t` (measured pre-fix, directions unchanged): quality
+0.95, issuance +0.06, investment −0.29, **payout −0.46**, momentum −0.51.

**The local window prefers 4 blocks and the published record prefers 5.** They
disagree, and the published record governs — that is the rule in "What must not
happen" below, and it is a real rule precisely because it cuts this way here.
102 months against a ~4%/yr tracking error resolves nothing: the entire spread
between the 4- and 5-block rows is 0.45%/yr with a standard error several times
that. If you would rather run 4 blocks, `--sleeves quality momentum investment
issuance` is defensible; what is not defensible is choosing between them on the
strength of these numbers.
