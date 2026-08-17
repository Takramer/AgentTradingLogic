# Does going down the cap ladder help? — measured, and rejected

**2026-08-01.** Reproduce: `py scripts/ff_size_ladder.py`
(raw rows: `tests/results/ff_size_ladder.csv`)

## Why this was asked

"Go down the cap ladder" was proposed as the one remaining lever with a 2–3×
prior, on the strength of the decay table in
[EXTERNAL-RESEARCH-2026-07.md](EXTERNAL-RESEARCH-2026-07.md): published
anomalies pay a median **7 bp/month in the top 90% of market cap post-2005**
against **19 bp/month across all stocks**. Read as a signal-strength gradient,
that is 2.7× for leaving the large-cap pond.

It then hit the practical objection that killed it as a *build*: small- and
mid-cap price history since 2005 is neither free nor easy to obtain, and a
universe assembled from *today's* mid-caps is survivorship-poisoned in the one
size bucket where survivorship bias is largest.

That objection stops the build. It does not stop the measurement, because
French publishes 5×5 size × signal portfolios — value-weighted, monthly,
1963–2026, survivorship-free, CRSP-based — and this repo already has them on
disk. Same evidence base RMX-B's 63-year record comes from.

## The control that decides the answer

Every number below benchmarks a size bucket's signal portfolio against **that
same bucket's own cap-weighted return**, not against the total market.

This is the whole methodology. Comparing a small-cap book to the market
measures the **size premium plus the signal**, and the size premium is not an
edge — it is a risk factor anyone can buy for free, and it has been negative
for long stretches. `ff_largecap_blend.py::size_control` already found this
exact trap on the modern era, where adding SMB collapsed the blend's alpha.

Bucket weights are French's own `n_firms × avg_size`, lagged one month so the
weight applied in month *t* is knowable at the end of *t−1*.

## Result 1 — there is no size gradient. The prior was wrong.

Blend of the three legs (high OP, high prior return, low investment), excess
over its own size bucket, %/yr:

| era | ME1 tiny | ME2 | ME3 mid | ME4 | ME5 BIG |
|---|---|---|---|---|---|
| **1963–2026** | +3.01 | +2.66 | **+3.05** | +2.02 | +2.24 |
| 1963–2004 | +4.53 | +3.78 | +3.75 | +2.97 | +3.10 |
| 2005–2026 | +0.26 | +0.58 | +1.75 | +0.24 | +0.58 |
| 2015–2026 | −0.61 | +0.68 | +2.45 | +0.92 | −0.42 |

Over the full 63 years the excess is **flat across the ladder** — 2.0% to 3.1%,
with BIG (+2.24) beating ME4 (+2.02) and within half a point of ME2. All five
buckets carry t between +3.8 and +5.7, so this is not a power problem; the
gradient simply is not there.

**So the 7-vs-19 bp gap is not a signal-strength gradient.** Once the size
premium is netted out by benchmarking within-bucket, it largely disappears. The
extra 12 bp/month in "all stocks" is mostly the size factor itself plus
microcap effects, and neither is an edge you can claim.

## Result 2 — post-2005 the decay is universal, and the one survivor is a look

Every bucket collapses after 2005, exactly as the decay literature predicts.
The only cell that stands out is **ME3 at +1.75%/yr (t = +1.98)**, and 2015+
at +2.45 (t = +2.12).

Do not act on it. That is one cell among **5 buckets × 4 eras for the blend
alone**, before counting the three individual legs. The expected maximum |t|
among five correlated draws under the null is already around 1.5–2.0, so t =
1.98 as the best of twenty is what noise looks like — this repo's own rule on
agent-scale search, and both local holdouts are spent, so there is nothing left
to confirm it on.

## Result 3 — the one real gradient is momentum's, and it has decayed to nothing

Momentum excess over its own bucket, %/yr:

| era | ME1 tiny | ME2 | ME3 mid | ME4 | ME5 BIG |
|---|---|---|---|---|---|
| 1963–2004 | **+10.20** | +7.04 | +6.71 | +5.59 | +4.37 |
| 2005–2026 | +1.07 | +0.74 | +2.01 | +0.80 | **+0.04** |

Pre-2005 this is the textbook finding — momentum is far stronger in small caps,
monotone down the ladder, +10.2%/yr in the tiniest bucket. **Post-2005 the
gradient is gone**: +1.07 at the bottom, +0.04 at the top, no ordering.

And the historical version was never implementable anyway. It is the leg that
drives RMX-C's 176%/yr turnover, and 176%/yr turnover in ME1 — roughly a
thousand firms averaging tens of millions of dollars — is a cost structure, not
a strategy.

## Result 4 — post-2005, quality works *best* in the universe already traded

Quality excess over its own bucket, 2005–2026: **ME1 −1.46, ME2 +0.53, ME3
+0.54, ME4 +0.46, ME5 BIG +1.19 (t = +1.83)**.

The gradient runs *backwards*. The largest bucket is the best one, and the
smallest is negative. For RMX-B's quality leg specifically, the S&P 500 is not
a compromise forced by data availability — it is the bucket where the signal
has actually kept working.

## Cost — even taking ME3 at face value

BLEND, 2005–2026, at RMX-C's measured 176%/yr one-way turnover. Cells are %/yr
advantage over BIG *net of that bucket's cost*, with BIG held at its measured
30 bps:

| size | gross | vs BIG | 30bp | 50bp | 75bp | 100bp | 150bp |
|---|---|---|---|---|---|---|---|
| ME1 tiny | +0.26 | −0.33 | −0.33 | −0.68 | −1.12 | −1.56 | −2.44 |
| ME2 | +0.58 | −0.00 | −0.00 | −0.36 | −0.80 | −1.24 | −2.12 |
| **ME3 mid** | +1.75 | +1.16 | +1.16 | +0.81 | +0.37 | −0.07 | −0.95 |
| ME4 | +0.24 | −0.34 | −0.34 | −0.70 | −1.14 | −1.58 | −2.46 |
| ME5 BIG | +0.58 | +0.00 | +0.00 | −0.35 | −0.79 | −1.23 | −2.11 |

This repo's own stratified quote captures put the least liquid decile it
already trades at **98.1 bps**. At 100 bps ME3's advantage is **−0.07%/yr** —
gone. In the plausible 50–75 bps mid-cap band it is +0.4 to +0.8%/yr, and that
is the *cherry-picked* bucket.

## Verdict

**The kill criterion fired.** It was fixed before running: *if the within-bucket
blend excess does not rise as size falls, or the smallest tradeable bucket's
advantage over BIG is smaller than its extra cost, the cap ladder is not worth
the data problem.* Both clauses fired.

- No size gradient in the blend over 63 years.
- Post-2005 decay is universal, not large-cap-specific.
- The one gradient that ever existed (momentum) is gone and was never tradeable
  at the small end.
- Quality now works **best** in the largest bucket.
- The single standout cell is one look among twenty and dies at 100 bps.

**Do not buy mid/small-cap history.** The 7-vs-19 bp prior does not survive the
within-bucket control, so the project it was going to fund was going to buy the
size premium and call it alpha.

**What this does settle, positively:** the S&P 500 universe is not the
constraint. This was the last hypothesis that blamed the universe for the small
measured edge, and it is now rejected on 63 years of survivorship-free data. The
remaining levers are cost measurement and breadth — see
[CLAUDE-RESEARCH-RULES.md](CLAUDE-RESEARCH-RULES.md) §6 and §6b.

## Limitations, stated

- Each leg is benchmarked against the population *it* sorts, so the OP/INV
  bucket returns differ from the PRIOR bucket returns by up to ~1 pp/yr over
  the same era (French's profitability and investment sorts require book
  equity; the prior-return sort does not). That is correct — a benchmark must
  see the same population as the book, audit FC-4 — but it means bucket
  returns are not comparable *across legs*, only within one.
- French quintiles are NYSE breakpoints on the whole CRSP universe. "ME5 BIG"
  is a superset of the S&P 500, not the S&P 500.
- The turnover assumption (176%/yr) is RMX-C's measured figure applied
  uniformly. Real small-cap momentum turnover would be higher, which makes the
  cost table optimistic for the small buckets.
- Nothing here is net of cost inside the bucket returns themselves; French
  portfolios are gross. The sweep applies cost to the strategy leg only, which
  again flatters the small buckets.
