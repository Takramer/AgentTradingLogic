# Video intake — 2026-08-17

Five videos. Same standing rule as the two previous intake documents
([2026-07-31](VIDEO-INTAKE-2026-07-31.md), 21 videos;
[2026-08-14](VIDEO-INTAKE-2026-08-14.md), 368): **a video is not evidence.**
Title and channel are all that is fetchable (`py scripts/fetch_video_titles.py`);
transcripts are unreachable and nothing below summarises an argument I could not
hear.

| # | channel | title | status |
|---|---|---|---|
| 1 | Roman Paolucci | Quant Explains Algorithmic Market-Making | closed — see below |
| 2 | DeltaTrend Trading | "Build Your Own Trading Strategy" — Ivy League Quant | nothing new |
| 3 | freeCodeCamp.org | Algorithmic Trading – Machine Learning & Quant Strategies Course with Python | **duplicate** — already row 362 of `research_data/video_corpus_2026-08-14.tsv` |
| 4 | Roman Paolucci | **Stop Using the Sharpe Ratio Until You Watch This** | **the one that mattered** |
| 5 | MemLabs | How to create a Mean Reversion strategy (by ex HFT quant trader) | **duplicate** — 2026-07-31 Cluster A #4 |

Two of five are exact duplicates. Both remaining channels have appeared before:
Roman Paolucci was Cluster A #1 ("How to Quant Trade in 3 Minutes"), DeltaTrend
was Cluster A #6 (contextualising features, already answered by autostrat round
2). Running corpus total: **394 videos, 392 unique.**

## #1 — market making, already closed

The 2026-08-14 intake triaged a 13-video market-making playlist
(QuantIntuition) and closed the class on structure rather than quality: market
making requires quoting both sides and managing inventory risk. **This engine
trades US equities on daily bars, long-only, and cannot short.** There is no
intersection. Unchanged by a fourteenth video.

## #2 — nothing new

Generic strategy-construction content from a channel whose one codeable idea
(conditioning a setup on regime instead of treating it as context-free) was
already tested: `docs/AUTOSTRAT-2026-07-31.md` round 2 found a MACD long pays
**most** in a SPY downtrend (+0.3281R) and least in an uptrend (+0.0051R).
Conditioning was worth doing and was not enough.

---

# #4 — "Stop Using the Sharpe Ratio". It found two real gaps.

Same pattern as the trials ledger in the last intake, and I will state it the
same way: **the video did not supply the method, it supplied the topic.** The
fixes below come from Lo (2002), *The Statistics of Sharpe Ratios*, Financial
Analysts Journal — not from anything anyone said on YouTube. What the video is
owed is that this repo had been printing Sharpe ratios for three weeks without
noticing either problem.

## Gap 1 — every Sharpe in this repo was a point estimate with no error bar

`alpha/stats.perf()` reported `sharpe` and nothing about its precision.
`alpha/stats.deflated_sharpe()` had been computing the Lo/Mertens standard error
**inline since the audit** and throwing it away after use. It is now exposed as
`sharpe_se()`, with `deflated_sharpe` calling it — a pure refactor, pinned by
`test_refactor_did_not_move_the_deflated_sharpe`, so no published DSR moves.

Measured on this repo's own book returns — `tests/results/book_returns_tier-point.csv`,
**4,025 sessions / 193 months, 2010-07 → 2026-07**, tier-point costs:

| series | Sharpe (daily √252) | Sharpe (monthly √12) | SE | **95% CI, annualised** |
|---|---|---|---|---|
| RMX-B | 0.836 | 0.974 | 0.075 | **0.46 .. 1.48** |
| RMX-C | 0.854 | 0.983 | 0.078 | **0.46 .. 1.51** |
| **SPY** | **0.895** | **1.039** | 0.078 | **0.51 .. 1.57** |
| CW universe | 0.873 | 1.013 | 0.077 | 0.49 .. 1.54 |

**The 95% confidence interval on an eight-year Sharpe is roughly ±0.5.** Every
Sharpe this repo has quoted — including the `Sharpe 0.86` that appears in
`docs/RMX-C.md` — sits inside an interval a full unit wide. The four series
above are **not distinguishable from each other**, and SPY has the highest point
estimate of the four.

That is not a new failure; it is the existing boundary made numeric. CLAUDE.md
already says the 8-year S&P 500 panel "can check the implementation for
*consistency*, never for *significance*", and that RMX-B's evidence is the
63-year Ken French record (Sharpe 0.55 vs the market's 0.47), not this panel.
The CI is what "never for significance" looks like when you write it down.
**Report Sharpe with this interval from now on.**

## Gap 2 — √252 assumes iid returns, and nobody checked

The universal annualisation `σ_daily × √252` is the **iid special case**. Lo
(2002) gives the general factor:

```
eta(q) = q / sqrt( q + 2 * sum_{k=1..q-1} (q-k) * rho_k )
```

Positive autocorrelation ⇒ `eta(q) < sqrt(q)` ⇒ the conventional Sharpe is
**overstated**. This is how smoothed or trending return series flatter
themselves, and `sqrt(252)` is hard-coded in `alpha/stats.py` and in six
`scripts/autostrat_*.py` files. Implemented as `lo_eta()`, applied to **monthly**
returns with q=12 rather than daily with q=252 — 251 sample autocorrelations off
daily data are mostly noise, and the repo's own FC-3 rule already says statistics
come off a non-overlapping monthly series.

### And then the measurement said: you cannot use this here

| series | AC(1) | eta(12) | vs √12 = 3.464 |
|---|---|---|---|
| RMX-B | −0.15 | **5.30** | +53% |
| RMX-C | −0.17 | 5.12 | +48% |
| CW universe | −0.16 | 4.96 | +43% |
| SPY | −0.14 | **4.74** | +37% |

Monthly returns are *negatively* autocorrelated over this window, so the naive
√12 **understates** every Sharpe by 37–53%. A uniform 50% uplift to a headline
Sharpe is exactly the kind of surprisingly-good number `docs/CLAUDE-PITFALLS.md`
says to distrust, so it was checked against the null.

**On pure white noise at n=193, `eta(12)` has a 95% range of 2.82 to 4.78** —
a ±38% band around a true value of 3.464. The estimator needs 11 autocorrelations
and the `(q−k)` weights amplify all eleven errors.

Against that band: RMX-B (5.30), RMX-C (5.12) and CW (4.96) fall outside;
**SPY (4.74) falls inside.** Same market, same window, same sign, same
magnitude — and the estimator calls the adjustment measurable for three series
and unmeasurable for the fourth. That is not a finding about RMX-B. It is a
demonstration that `eta(12)` cannot be leaned on at 193 observations.

**So the guard was built into the code rather than into a footnote.**
`lo_eta_null_band(n, q)` simulates the white-noise distribution, and `perf()`
returns `sharpe_lo2002` as **NaN whenever eta lands inside it**, while still
reporting the raw `lo_eta` and the band so the NaN is interpretable rather than
mysterious. Refusing to print a number you cannot measure is the correct
behaviour, and `test_perf_suppresses_an_unmeasurable_adjustment` pins it.

## What shipped

`rhengine/alpha/stats.py` — `sharpe_se`, `sharpe_ci`, `autocorrelations`,
`lo_eta`, `lo_eta_null_band`; `Perf` gains six defaulted fields
(`sharpe_se`, `sharpe_ci_lo/hi`, `ac1`, `sharpe_monthly`, `sharpe_lo2002`,
plus the raw `lo_eta` and its band) so dicts written before today still
construct; `print_report` prints a "Sharpe, honestly" block.
`tests/test_stats_sharpe.py`, 14 tests. Suite: **404 passing.**

## What did NOT change

- **No published number was altered.** The refactor is value-identical and
  proven so by test; the new columns are additive. `docs/RMX-C.md`'s
  `Sharpe 0.86` is still 0.854→0.86 — it simply now travels with `0.46 .. 1.51`.
- **`sqrt(252)` was not ripped out of the six `scripts/autostrat_*.py` files.**
  They compute Sharpe inline and those are R-space diagnostics, not published
  book statistics. Changing them is a separate, larger change.
- **No strategy, signal, or routing change.** Nothing from these five videos.

## Running tally across 394 videos

| method | source | status |
|---|---|---|
| bar-permutation null | neurotrader, 2026-07-31 | built 2026-08-14 (`barperm.py`) |
| Sharpe SE / CI, Lo (2002) eta | Roman Paolucci, 2026-08-17 | built today — and eta measured as **unusable at this n** |
| trials ledger | the sweep-count videos | built 2026-08-14, but the audit had already ordered it |

**Three methods from 394 videos. Zero signals, zero strategies, zero numbers.**
All three are statistical guard rails, and two of the three concluded by making
an existing claim *weaker* rather than supporting a new one.
