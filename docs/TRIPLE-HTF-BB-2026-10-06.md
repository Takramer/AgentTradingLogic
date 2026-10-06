# Triple HTF Bollinger — 2026-10-06

Pine source, unmodified defaults:
[HermanTrading/Triple-HTF-Bollinger-Bands-Strategy-5m-15m-1h](https://github.com/HermanTrading/Triple-HTF-Bollinger-Bands-Strategy-5m-15m-1h/blob/main/Triple_HTF_Bollinger_Bands_Strategy_5m_15m_1h.pine).

**Rejected as something to run.** On the two windows with continuous bars it
trailed the S&P, sat in cash most of the session, and its gross R is a coin
flip. The 1-year alpha t does not clear this family's bar. The 5-year and
10-year rows of this rule are unmeasured.

Runner: `scripts/triple_htf_bb.py`. Sharpes from `rhengine.alpha.stats.perf`.
Three trials were logged before the run, family `legacy-engine` (25 trials,
E[max |t|] = 2.00, required t at 0.05 = 3.08). This note is their source.
Nothing was added to `strategies.json`. The six legacy sleeves stay retired.

---

## What was measured

5-minute chart. Bollinger 20, 2.0, population standard deviation, on the
previous completed 5m, 15m and 60m bar (`[1]` with `lookahead_on`, the
non-repainting form). Long when the close is above at least two upper bands
and above the 5m basis by one tick. Short is the mirror. Stop is the 5m basis
fixed at entry. Target is 1R from the fill. Stop is checked before the target.
A gap through either fills at the open. Entries only from 08:00 inclusive to
12:00 exclusive America/New_York. Cooldown is `bar_index - exit_bar > 10`.
One position. Fills at the signal close.

The Pine `strategy()` call buys 1 share against $1,000,000. That quantity is
not a return. This run is fully invested while a trade is open and in cash
otherwise, marked to market every bar.

Windows end 2026-10-06. Starts: 6m 2026-04-06, 1y 2025-10-06, 5y 2021-10-06,
10y 2016-10-06.

| instrument | what it is | cost that is the headline |
|---|---|---|
| USA500IDXUSD | Dukascopy S&P 500 index CFD, 24-hour, the chart the session filter was written for | quoted bid-ask, charged in full on a mid fill (~0.7–0.8 bps round trip here) |
| SPY | Robinhood extended-hours 5-minute, real bars only | flat 5 bps round trip. The opening spread is unmeasured. 5 bps is the top of the mega-cap tier |

Robinhood 5-minute history before 2026-04-01 came back interpolated at one
price and was dropped. The last real SPY bar in the file is 2026-10-05.

Flat 5 bps and flat 30 bps on the index are brackets on the same trades. They
are not extra trials. 30 bps is the legacy-universe cost and the wrong tier
for this instrument.

Long-only is the same entries with shorts dropped. This book cannot short.

---

## USA500, quoted spread

Compare **total return**. The index series has Sunday-evening sessions, so a
6-month window is 158 index days and 127 SPY sessions. `perf` annualizes with
`n_days / 252`, which makes the CAGRs disagree even when the totals agree.
The index is a price series. SPY total return includes dividends. A flat book
does not collect them.

### Long and short

| | total | vol | Sharpe (monthly) | 95% CI | maxDD | days | months |
|---|---|---|---|---|---|---|---|
| **HTF 6m** | **−3.9%** | 4.7% | −1.24 | −4.89 .. +2.42 | −7.5% | 158 | 7 |
| index hold | +18.6% | 10.9% | +2.22 | +0.19 .. +4.26 | −4.2% | 158 | 7 |
| SPY price | +18.1% | 12.5% | +2.23 | +0.18 .. +4.28 | −4.5% | 127 | 7 |
| SPY total | +18.8% | 12.5% | +2.34 | +0.35 .. +4.32 | −4.5% | 127 | 7 |
| **HTF 1y** | **−3.8%** | 4.8% | −0.65 | −2.73 .. +1.43 | −7.5% | 313 | 13 |
| index hold | +15.7% | 12.4% | +1.12 | −0.56 .. +2.80 | −9.1% | 313 | 13 |
| SPY price | +15.8% | 13.0% | +1.12 | −0.60 .. +2.83 | −9.1% | 251 | 13 |
| SPY total | +17.1% | 13.1% | +1.21 | −0.48 .. +2.90 | −8.9% | 251 | 13 |

6-month alpha t is not computed (7 months; the test needs 12).

1-year alpha versus the index: **−6.09%/yr, t = −1.15, beta 0.18**, 13 months.
Versus SPY price: −6.84%/yr, t = −1.40, beta 0.14. Versus SPY total:
−6.96%/yr, t = −1.41, beta 0.14.

Family-wise, t = −1.15 against 25 logged `legacy-engine` trials has p = 0.999.
Required t = 3.08. **DOES NOT CLEAR.**

| | 6m | 1y |
|---|---|---|
| trades | 237 | 462 |
| win rate | 51% | 50% |
| avg R, gross / net | +0.03 / −0.02 | +0.01 / −0.04 |
| median hold | 30 min | 35 min |
| time in market | 13.0% of bars | 14.1% of bars |
| round trips | 378/yr | 372/yr |
| one-way turnover | 37,800%/yr | 37,196%/yr |
| avg round-trip cost | 0.7 bps | 0.8 bps |

One-way turnover counts each round trip as a full deployment of the book.
RMX-B's published one-way turnover is about 190%/yr.

### Long only

| | total | Sharpe | 95% CI | maxDD | trades | gross R | net R | time in market |
|---|---|---|---|---|---|---|---|---|
| 6m | −1.0% | −0.42 | −3.32 .. +2.49 | −4.3% | 140 | +0.07 | +0.02 | 7.5% |
| 1y | −3.2% | −0.73 | −2.90 .. +1.43 | −5.1% | 266 | +0.02 | −0.04 | 7.7% |

1-year alpha versus the index: **−6.18%/yr, t = −2.32, beta 0.23**.
Family-wise p = 0.402. Required t = 3.08. **DOES NOT CLEAR.**
Versus SPY price: −5.18%/yr, t = −1.88, beta 0.22. Versus SPY total:
−5.41%/yr, t = −1.92, beta 0.22. Round trips 223/yr (6m) and 214/yr (1y).

Dropping the shorts removes a slice of the loss and leaves the book behind
the index. Beta stays near 0.2.

### Cost brackets, same long-and-short trades

| charge | 6m total | 1y total | 1y net R | 1y alpha vs index |
|---|---|---|---|---|
| quoted spread (~0.8 bps) | −3.9% | −3.8% | −0.04 | −6.09%/yr, t = −1.15 |
| flat 5 bps | −13.2% | −20.9% | −0.33 | −23.87%/yr, t = −4.26 |
| flat 30 bps | −52.0% | −75.1% | −2.07 | −123.21%/yr, t = −12.86 |

t = −4.26 at the 5 bp bracket has family-wise p = 0.001 and clears 3.08.
Gross R on those trades is +0.01. The t-stat is the turnover: a 1R stop is
often about 10 bp of price, so 5 bp is a large fraction of 1R, and the book
does ~370 round trips a year. The headline cost remains the quoted spread,
where the same trades do not clear.

---

## SPY, flat 5 bps, 6 months only

Real 5-minute bars run from 2026-04-01. The 1-year, 5-year and 10-year
windows have no real history back to their starts, and the runner skips them.

| | total | vol | Sharpe | 95% CI | maxDD | days |
|---|---|---|---|---|---|---|
| long and short | −13.4% | 5.4% | −4.20 | −6.75 .. −1.64 | −13.3% | 127 |
| long only | −7.2% | 3.8% | −3.53 | −6.87 .. −0.19 | −7.8% | 127 |
| SPY price, same window | +18.1% | 12.5% | +2.23 | +0.18 .. +4.28 | −4.5% | 127 |
| SPY total | +18.8% | 12.5% | +2.34 | +0.35 .. +4.32 | −4.5% | 127 |

203 trades long and short, win 49%, gross R −0.02, net R −0.34, median hold
40 min, 18.9% of bars, 403 round trips/yr. Long only: 121 trades, gross R
+0.06, net R −0.27, 10.8% of bars.

The long-and-short Sharpe interval excludes zero. The window is 7 months, so
alpha t is not computed. A 7-month Sharpe interval is not the family-wise test.

---

## 5-year and 10-year of this rule

Unmeasured.

The USA500 file spans 2016-10-03 through 2026-10-06 and contains one hole
longer than a weekend: **2021-02-18 02:55 UTC → 2025-08-07 18:00 UTC**
(1,631 days). Years present, in bar counts: 2016–2020 are populated, 2021
stops in February, 2022–2024 are absent, 2025 resumes on August 7.

The 10-year window contains that hole, so the runner skips it. The 5-year
window starts on 2021-10-06, which is inside the hole. Filtering to the
window would hide the gap, label ~14 months as five years, and let the
buy-and-hold return on 2025-08-07 include the jump from the February 2021
close. An earlier print of that slice showed an index hold of +98% with
52.5% vol on 364 days. That print is discarded. The runner now skips a window
whose first bar is more than 10 days after the requested start.

Dukascopy was rate-limiting when the pull stopped. A single hour request on
2026-10-06 timed out at 20 seconds. The hole stays empty until that host
answers again. Weekend gaps of about 3 days are exchange closures and are
left in.

---

## RMX-B on the same calendar, Ken French

Vintage of the files in this run: monthly data through **2026-08-31**.
Construction is the published one: 50/50 `BIG HiOP` + `BIG HiPRIOR`, net of
20 bps on 33%/yr quality turnover and 200%/yr momentum turnover. The market
is French's value-weighted market, gross, with dividends.

These window slices are a description. They are not a replacement for the
frozen claim in `docs/RMX-RESULTS.md`: **+1.78%/yr, t = +2.54**, 1963–2026.
On this later vintage the full sample (1963-07 .. 2026-08, 758 months) reads
CAGR 12.51% vs 10.89%, alpha **+1.88%/yr, t = +2.72**. The claim in
`RMX-RESULTS.md` stays the one that was frozen.

| window | months in the file | RMX-B total | market | alpha/yr | t |
|---|---|---|---|---|---|
| 6m 2026-03 .. 2026-08 | 6 | +16.2% | +12.3% | n/a | n/a |
| 1y 2025-09 .. 2026-08 | 12 | +23.0% | +19.8% | −3.37% | −0.59 |
| 5y 2021-09 .. 2026-08 | 60 | +75.9% | +74.3% | +0.45% | +0.25 |
| 10y 2016-09 .. 2026-08 | 120 | +350.5% | +311.6% | +1.60% | +0.99 |

The French 6-month window is March–August 2026, because the file ends in
August. The Pine 6-month window is April–October. They are neighboring
windows, not the same days.

None of the four recent windows has an alpha t near 2. The 10-year t of
+0.99 is what “+1.78%/yr against ~5.5%/yr tracking error needs ~39 years”
looks like from inside a decade. RMX-B's total return was ahead of the market
in all four windows. That is a description of the path, and the alpha t is
not a finding.

RMX-C was not re-run. The local point-in-time panel is not in this checkout,
and the panel figures in `docs/RMX-C.md` are an implementation check rather
than the evidence.

---

## How this rule differs from the book

1. **One instrument, five minutes, New York morning.** RMX-B is ~96 names,
   cap-weighted, quality held 12 months from June, momentum 12-1 reformed
   monthly, about 190%/yr one-way. This rule does ~370 round trips a year
   (about 37,000%/yr one-way) and is in the market 8–14% of the 5-minute bars.
2. **Beta near 0.2.** The gap versus the S&P in 2025–26 is mostly cash, plus
   the short side, during a rally. Long-only still trails.
3. **It buys the upper band.** The retired `bollinger` sleeve buys a lower-band
   tag in a choppy regime: daily mean reversion. Its deep-history scorecard is
   net R −0.0128 at 29.1 bps, day-clustered t +1.34
   (`docs/AUDIT-2026-07-24.md`, the cost-unit correction). Opposite sign, and
   that sleeve is retired.
4. **A 1:1 stop and target with gross R ≈ 0 and a win rate ≈ 50%.** That is
   the shape of a coin flip. The quoted ~0.8 bp spread barely moves R. Five
   basis points does, because the risk to the basis is small.
5. **Evidence length.** RMX-B's claim is a 63-year published record. This
   rule has 13 months of continuous index bars at the end of the file, and a
   6-month SPY cross-check. Thirteen months cannot resolve a −6%/yr alpha
   against the tracking error of a strategy that is flat most of the day, and
   the t that does exist misses 3.08.

---

## Verdict

| trial | outcome | why |
|---|---|---|
| USA500 long and short | rejected | Trailed the index. Gross R ≈ 0. 1y alpha t = −1.15, family-wise p = 0.999. 5y and 10y unmeasured. |
| USA500 long only | rejected | Still trailed. 1y alpha t = −2.32, family-wise p = 0.402. |
| SPY extended hours, 5 bps | rejected | 6m total −13.4% against SPY +18.1%. Seven months, so no alpha t. The Sharpe CI excludes zero under that 5 bp charge. |

The rejection is economic: there is nothing here to adopt, and the 5 bp
bracket shows the turnover cannot carry a mega-cap cost. It is not a
significant negative alpha at the quoted spread. The 5-year and 10-year rows
stay blank until the 2021-02-18 → 2025-08-07 hole is filled with real bars.
