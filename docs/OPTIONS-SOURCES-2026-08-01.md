# Options source intake — 2026-08-01

Seven sources supplied: three YouTube videos and four articles, with the brief
"include these in the options strat for the RobinhoodAgent, don't start options
live yet — though maybe some SPX and QQQ 0DTEs."

This records what each source is, what was built from it, and the part that
governs everything else: **what the account can afford.** For a small account
the binding constraint on this corpus is not edge, spread or decay. It is the
contract multiplier, and it does not divide.

Measured against the same standing rule as
[VIDEO-INTAKE-2026-07-31.md](VIDEO-INTAKE-2026-07-31.md): a video or a blog is
not evidence. It supplies a hypothesis, a method or a warning. It cannot supply
a result, and no number stated in one appears here as a finding.

---

## 1. The first finding: three of the seven are about a different strategy

The three videos were resolved by title (oEmbed; transcripts remain unfetchable
because YouTube renders client-side — same limitation as the previous intake).

| # | channel | title | topic |
|---|---|---|---|
| V1 | InTheMoney | How to Trade the Wheel Strategy | **wheel** |
| V2 | Invest with Henry | The Wheel Strategy For Beginners 2026 MASTERCLASS (2+ Hours) | **wheel** |
| V3 | The Cashflow Academy | The "Wheel" Options Strategy I Will Use For Life | **wheel** |
| MX | marketxls.com | How to trade 0DTE SPY options | 0DTE |
| TA | tradealgo.com | 0DTE SPY options strategy | 0DTE |
| TB | tradingblock.com | 0DTE options strategies | 0DTE |
| TP | tradeproacademy.com | How to day trade SPY options | intraday SPY |

**All three videos are wheel content — cash-secured puts rolling into covered
calls. None is about 0DTE.** The corpus is therefore two unrelated strategies,
and they have opposite capital profiles: 0DTE is the cheapest thing in options,
the wheel is among the most expensive. That split, not any parameter, is what
determines what is available here.

Nothing below is attributed to a video. The titles are established; the content
is not, and `tests/test_options_spec.py::test_no_parameter_is_attributed_to_a_video`
pins that so a later reader cannot mistake genre convention for something that
was read.

---

## 2. The capital verdict, which answers the brief directly

Levels are the 2026-07-31 close, read from the Robinhood chain and quote
endpoints on 2026-08-01. Reproduce with
`py -m rhengine.options_spec --capital <n>`.

### The wheel is not available, and not because of money

| underlying | settles | wheel |
|---|---|---|
| SPY | shares | **$74,681** collateral per cycle |
| QQQ | shares | **$68,792** collateral per cycle |
| SPX / XSP / NDX | **cash** | **impossible at any account size** |

SPX, XSP and NDX settle in cash. Assignment delivers a debit, not stock, so
there is nothing to write a covered call against — **the wheel's second leg does
not exist on them.** It is not that the strategy degrades on an index; it is
that half of it is undefined. The two instruments named in the brief for 0DTE
are exactly the two the videos' strategy cannot use.

On SPY and QQQ the wheel is defined and costs ~$70k a cycle. There is no
fractional contract.

### 0DTE is affordable, and XSP is the instrument nobody in the corpus mentions

At 0DTE ATM, ~16% IV (VIX closed at 15.99 — reference only, see §4):

| underlying | 0DTE listed | one ATM contract | $3-wide credit spread |
|---|---|---|---|
| SPY | yes (M–F) | ~$250 | $210 collateral |
| QQQ | yes (M–F) | ~$230 | $210 |
| **XSP** | yes (M–F) | **~$251** | $210 |
| SPX | yes (SPXW, M–F) | **~$2,509** | $1,050 (15-wide) |
| NDX | no dailies | ~$9,472 | $2,100 |

Premiums are `atm_premium_estimate()` — the `0.4·S·σ·√T` approximation, **for
sizing only, never for pricing a trade.** They exist so this table could be
written before any 0DTE quote has ever been captured. Replace them with captured
marks once the accumulator has run.

**SPX is ~10× too large a ticket for a small account; XSP is the same index at
1/10 the size and is not mentioned by any of the seven sources.** Two structural
facts also favour the cash-settled route, and no source mentions these either:

- SPX/XSP carry `sellout_time_to_expiration: 0`. SPY and QQQ carry **1800** —
  the broker force-closes an equity 0DTE position 30 minutes before expiry if
  the account cannot cover assignment. For an account that cannot cover 100
  shares of SPY, that is not an edge case, it is the default exit.
- Cash settlement means no assignment risk at all, which is the failure mode
  that actually destroys small options accounts.

Against that: the corpus' own sizing rule (MX and TA agree on 1–2% per trade)
implies **~$12,500** to buy one SPY 0DTE ATM call and **~$10,500** for one
$3-wide credit spread. MX independently states a $5k–$10k minimum. The sources
that describe these trades do not describe them for an account below ~$10k.

---

## 3. What was built

Three things. None of them can produce a trading decision, and that is
deliberate — the same discipline as `market_data_options.py` and
`options_costs.py`.

### 3a. `rhengine/options_spec.py` — the parameter corpus with provenance

Every numeric rule any source states, recorded with **which sources state it**.
`py -m rhengine.options_spec`.

Provenance is the point. "Stop at 2× the credit received" appears in two
independently written sources; "only trade if VIX is above 14" appears in one.
Those are different objects, and storing them identically would erase the only
quality signal available. The column counts sources, **not truth** — four retail
articles agreeing is four retail articles.

The corpus agrees on more than expected:

| parameter | value | sources |
|---|---|---|
| short strike delta (selling) | 0.10 – 0.15 | MX, TA, TB |
| long strike delta (buying) | 0.25 – 0.50 / ATM | TA, TB, TP |
| entry window ET | 09:35 – 12:00 | MX, TA, TB |
| no new positions after ET | 14:00 | MX, TA, TB |
| stop on a credit spread | 2× credit | MX, TA |
| risk per trade | 1 – 2% of account | MX, TA |
| event blackout | CPI, NFP, FOMC | MX, TB |

The entry window is the one item with **corroboration of mechanism rather than
of opinion**: `scripts/rh_spreads.py` independently found quotes tightest in the
same window, and `docs/OPTIONS-ACCUMULATOR.md` already sets the capture cadence
to 10:00–15:30 ET for that reason. Three sources arriving at the same window is
consistent with liquidity, which is measurable, rather than with an edge, which
is not.

### 3b. Three recorded conflicts

Where sources disagree, the disagreement is the finding. Averaging it away would
manufacture a consensus the corpus does not have.

- **OC-1 — whether to trade 0DTE at all.** TP, on a page titled *how to day
  trade SPY options*, says avoid same-day expiry, prefer the next expiry, cap at
  7 DTE. MX, TA and TB build their entire method on 0DTE. **Within one
  seven-source corpus the headline question is unresolved.** `options_costs.py`
  has already measured the thing they are arguing about: the 3-DTE 745C needs a
  0.11% move in one session to cover spread plus decay, against SPY's ~0.7–1%
  daily range. That number is measured here; theirs are asserted there.
- **OC-2 — stops.** MX and TA specify hard stops. TB says never use stop orders
  on options, because they trigger market orders into a wide book. Both can be
  right — a *price* rule with a *limit* execution — but the corpus never
  separates the rule from the order type, and an unattended executor must.
- **OC-3 — the 1–2% rule against the wheel.** The sizing rule is written for
  defined-risk day trades. Applied to a SPY cash-secured put it implies a ~$3.7M
  account, which nobody means. `min_account_for()` returns `None` for the wheel
  legs rather than a plausible-looking invention.

### 3c. The accumulator now captures what these strategies trade

This is the only change with a consequence beyond documentation, and it closes a
gap that was silently costing sample every day.

`rhengine/options_registry.py`:

| change | why |
|---|---|
| default tenors `(7, 30, 60)` → **`(0, 7, 30, 60)`** | the old default captured **no same-day contract at all**. Four of seven sources are about 0DTE, and that question was unanswerable from this registry no matter how long the job ran. SPY, QQQ and SPXW all list M–F expirations. |
| `STRIKE_STEP` per underlying | the old flat `step=5.0` asks for strikes that do not exist on a $1-grid name. It does not error — it just returns fewer contracts. |
| **`plan_delta_strikes()`** at 0.10 / 0.15 / 0.30 | a ±$4 SPY dollar ladder sits between roughly 0.35 and 0.65 delta at 60 DTE. **The 0.10–0.15 delta short strikes every premium-selling source in this corpus trades were never being captured, at any tenor.** The two strike sets are provably disjoint — pinned by `test_delta_bands_reach_where_the_dollar_ladder_cannot`. |

**Why the delta bands do not break the sampling discipline.**
`docs/OPTIONS-ACCUMULATOR.md` states that sampling is deliberately
unconditional, because a signal-selected sample cannot answer an unconditional
question. Delta here is inverted Black–Scholes moneyness — a function of spot,
IV and time only. It consults no price history, no indicator and no signal. It
changes **which contracts are seen**, never **which days are sampled**, and
calls and puts are always taken symmetrically so the sample does not become a
skew sample.

**The cost of the change, stated rather than buried:** the registry's tenor
composition differs before and after 2026-08-01. Any term-structure comparison
drawn from it must condition on capture date, or it will read a change in
sampling as a change in the market.

New capture command:

```bash
py -m rhengine.options_registry plan --symbol SPY --spot 746.81 --iv 0.16
```

Without `--iv` the delta bands are skipped and the plan says so. An invented IV
would put the bands at invented strikes and the registry would record them as
though they had been chosen.

---

## 4. What was rejected, and by what

Two parameters in the corpus reproduce gates this repo has already tested and
thrown out. They are recorded in `PARAMS` with the rejection attached, so that
re-proposing them requires reading the rejection first.

- **`vix_floor = 14` (TA only).** A VIX-level gate. `vix_utils`' term-structure
  gate was tested here and **rejected as an in-sample mirage**. A level
  threshold is the same class of object with one fewer moving part. Single
  source, already-rejected form: do not build.
- **`iv_ceiling = 0.30` (TP only).** An IV-level gate.
  `market_data_options.py` argues at length (CONFLICT-9) that IV level is the
  wrong quantity — the researched quantity is IV at entry versus realised vol
  over the contract's **actual life**, which is the one with a mechanism
  attached. The module's docstring already forbids adding an IV-Rank function
  for this reason.

**Every indicator rule in the corpus is blocked on data, not on evidence.** TP's
20-EMA on 5-minute bars and volume-profile ledges, MX's put/call volume ratio,
TB's volume-versus-open-interest — all need intraday bars. This research stack
runs on **daily** bars. Nothing here is a judgement on those rules; they simply
cannot be evaluated with what the engine has.

---

## 5. What was not built, and why

**No options strategy, and nothing armed.** `options_spec.STATUS` is
`"blocked"`, pinned by test. Four blockers, unchanged from the previous intake
except where noted:

1. **No retrospective sample.** Expired-contract discovery lapses in weeks, so
   the registry accumulates forward only and nothing captured has yet lived and
   died. §3c widens *what* is captured; it does not create history.
2. **No realised-vs-implied measurement.** `market_data_options.py` step 2 is
   unblocked and still not run.
3. **The published prior is bad** — and for the wheel this is now sharper than
   it was, see §6.
4. **CLAUDE.md rule 6.** No options path has any track record here at all.

**No routing change, no book entry, no `strategies.json` edit.** Nothing moved
toward `autonomous`, and per the brief, nothing goes live.

---

## 6. The wheel, settled from the published record — RUN 2026-08-01

The wheel did not need a forward sample. CBOE publishes both of its halves as
mechanical indices, and they are now fetched, measured and pinned by tests.

```bash
py scripts/fetch_cboe_benchmarks.py     # ^BXM ^PUT ^SP500TR ^GSPC -> external/cboe
py -m rhengine.alpha.buywrite           # the report below
```

- **BXM** — long the S&P, short a 1-month ATM call, rolled monthly. The
  **covered-call half**. 9,603 daily closes, **1988-06 → 2026-07**.
- **PUT** — fully collateralised short 1-month ATM put. The **cash-secured-put
  half**. 7,539 closes, **1996-08 → 2026-07**.

Rules-based, survivorship-free, no selection, 38 and 30 years. This is the
class of evidence CLAUDE.md rule 4 asks for, and the videos cannot supply it.

### 6a. The benchmark error, which is the finding

BXM and PUT are **total-return** indices — the covered-call writer owns the S&P
and receives its dividends. Benchmark matters more than anything else here:

| BXM 1988–2026 | alpha/yr | alpha t | verdict |
|---|---|---|---|
| vs **^GSPC** (S&P **price** index) | +1.84% | **+2.05** | "covered calls generate alpha" |
| vs **^SP500TR** (S&P **total return**) | +0.58% | **+0.62** | nothing |

**Same series, same days. The benchmark alone flips the verdict.** The entire
retail case for premium selling is reproducible as a dividend-accounting error
worth ~1.8%/yr. PUT does the same thing: +2.57%/yr t=+2.20 against the price
index, +1.53%/yr t=+1.26 against total return.

Pinned by `tests/test_buywrite.py::test_price_index_manufactures_significance`
so it cannot quietly come back. **Any premium-selling claim that does not name
its benchmark should be assumed to have used the price index.**

### 6b. The full record, against the correct benchmark

| | CAGR | vol | Sharpe | maxDD | growth of $1 |
|---|---|---|---|---|---|
| **BXM** 1988–2026 | 8.78% | 12.98% | **0.49** | −40.1% | 24.66× |
| ^SP500TR, same days | 11.42% | 17.82% | **0.53** | −55.3% | 61.59× |
| **PUT** 1996–2026 | 8.50% | 15.24% | **0.46** | −37.1% | 11.49× |
| ^SP500TR, same days | 10.16% | 19.09% | **0.49** | −55.3% | 18.07× |

Both legs give up return **and** Sharpe. They buy a genuinely better drawdown
(−40% and −37% against −55%), and that is the real product.

**The repo's standing "~1/3 of the S&P over the last decade" figure is
confirmed, and it was accurate.** Measured over 2016-08 → 2026-07: BXM turns \$1
into **2.10×** against the index's **4.06×** — **0.36 of the index**. Over the
full 38 years it is 0.39. Last decade BXM's alpha is *negative*: −2.03%/yr,
t = −0.89.

### 6c. The test that actually decides it

Both indices run at **beta ≈ 0.6**. So "does premium-selling beat the index" is
the wrong question — it holds less of the index, of course it does not. The
right question is whether it beats **the same beta bought with no options at
all**: a static `0.64 × SP500TR + 0.36 × T-bills` sleeve, which needs no chain,
no assignment, no collateral and no execution.

| | CAGR | Sharpe | maxDD | alpha vs blend | t |
|---|---|---|---|---|---|
| **BXM** | 8.78% | 0.49 | −40.1% | +0.45%/yr | **+0.48** |
| 0.64×TR + cash | 8.72% | **0.53** | **−38.5%** | — | — |
| **PUT** | 8.50% | 0.46 | −37.1% | +1.39%/yr | **+1.13** |
| 0.64×TR + cash | 7.72% | **0.49** | −38.4% | — | — |

**Over 38 years the covered-call index delivers +0.05%/yr more than a passive
index/cash blend, at worse Sharpe and worse drawdown.** Put-writing is
genuinely better than buy-writing — every era, and +0.78%/yr over the blend —
but at **t = +1.13** it does not clear any bar this repo uses. Compare RMX-B's
alpha **t = +2.54**, which is what a shipped result looks like here.

### 6d. Era dependence, which is the mechanism

| era | BXM | PUT | ^SP500TR | BXM − TR | PUT − TR |
|---|---|---|---|---|---|
| 1990s | 15.88 | 22.03\* | 18.15 | −2.27 | +3.88 |
| 2000s | 2.60 | 4.80 | −0.95 | **+3.55** | **+5.76** |
| 2010s | 7.06 | 7.34 | 13.58 | −6.52 | −6.24 |
| 2020s | 7.77 | 9.39 | 15.39 | −7.62 | −6.00 |

\* partial — PUT begins 1996-08.

Writing premium **wins when the index goes nowhere and loses when it runs**,
which is exactly the truncated-upside mechanism, now measured rather than
asserted. It is not a timing signal: knowing the era in advance is the whole
problem, and this repo has already rejected the regime-timing route (RMX-D).

### 6e. Verdict

**The wheel closes on evidence, not on the capital constraint.** Thirty-eight
years of published, survivorship-free data say S&P covered-call writing is
indistinguishable from — and on Sharpe and drawdown slightly worse than — a
passive index/cash blend requiring no options at all. Put-writing is the better
of the two and still does not clear t = 2.

That is a *better* outcome than the capital blocker, because it does not go away
when the account grows.

**The boundary, in the same breath:** BXM and PUT are **S&P 500**, **monthly**,
**at the money**, **mechanical**. They bound the *structural* return of writing
index premium — the part every wheel shares. They do **not** settle whether a
discretionary wheel at a chosen delta on a chosen name does better, and nothing
available here settles that. What they do establish is that the *starting
prior* for any such proposal is "no edge over cash-plus-beta", and the burden
sits with the proposal.

**What is still open:** the VRP measurement (`market_data_options.py` step 2)
asks a different question — whether *short-dated* implied exceeds subsequently
realised vol — and BXM/PUT do not answer it, because they are monthly. The
accumulator remains the right job and its 0DTE tenor is now being captured.

---

## 7. Robinhood's session rules — surveyed 2026-08-01

Yes, the late close is real. The rule is **not** the one it looks like, and an
agent that infers it will be wrong with live money. Surveyed across 14 chains
via `get_option_chains`; encoded in `options_spec.LATE_CLOSE_SURVEY`.

| flag | meaning |
|---|---|
| `late_close_state: enabled` | the **16:15 ET** close |
| `extended_hours_state: enabled` | the **CURB** session — index chains only, and *longer* than the late close: the broker accepts `market_hours='regular_curb_hours'` and `'regular_curb_overnight_hours'` |
| `sellout_time_to_expiration` | seconds before expiry at which the broker **force-closes** a position the account cannot cover |

| symbol | kind | close | forced exit |
|---|---|---|---|
| SPY, QQQ, IWM, DIA | broad ETF | **16:15** | 30 min before expiry |
| **XLF, XLE, GLD, EEM** | sector / commodity ETF | **16:15** | 30 min before expiry |
| **TQQQ, GDX, ARKK** | leveraged / active ETF | 16:00 | 30 min before expiry |
| AAPL | single stock | 16:00 | 30 min before expiry |
| **SPX (SPXW), XSP** | index | **CURB, incl. overnight** | **none** |

**It is not a size rule and not an index-tracking rule.** XLF and XLE have the
late close; TQQQ — leveraged Nasdaq-100, enormous volume — does not. GDX does
not. It is an exchange-designated list, so `session_close()` raises on an
unsurveyed symbol rather than guessing. Read the flag per chain.

**Two consequences that matter more than the extra 15 minutes:**

1. **Every equity/ETF chain force-closes 30 minutes before expiry** if the
   account cannot cover assignment. For an account that cannot post \$74,681 for
   100 SPY, that is not an edge case — it is the default exit on every SPY or
   QQQ 0DTE. **SPX and XSP have `sellout = 0`**: cash-settled, no assignment,
   no forced exit, plus a session that runs past 16:15 and overnight. That is
   the strongest argument yet for XSP over SPY/QQQ in a small account, and it
   is independent of the strategy.
2. **The agent interface is SINGLE-LEG ONLY** — multi-leg spreads are
   unsupported even on an `option_level_3` account
   (`options_spec.EXECUTION_LIMITS`). So **every credit spread and iron condor
   in the source corpus is unexecutable here.** What remains placeable is long
   calls/puts, covered calls and cash-secured puts — Level 2, i.e. the wheel
   (§6, now closed on evidence) and naked directional buying (which the corpus
   itself warns about). The premium-selling method the sources actually teach
   cannot be run through this path at all.

Also resolves **OC-2** (the stops conflict) in the broker's terms:
`stop_market` is sell-to-close only, GFD, regular hours only, stop below the
current ask — and is **blocked in every extended-hours session**, which accept
limit-immediate orders only. TB's warning about stop orders becoming market
orders is the broker's own position.

## 8. Running it

```bash
py -m rhengine.options_spec                       # parameters + provenance
py -m rhengine.options_spec --conflicts           # only where sources disagree
py -m rhengine.options_spec --capital 2000        # what an account can hold
py -m rhengine.options_spec --strikes --spot 746.81 --iv 0.16 --dte 1

# the daily capture, now including 0DTE and the delta bands
py -m rhengine.options_registry plan --symbol SPY --spot <spot> --iv <atm iv>
py -m rhengine.options_registry ingest capture.json
py -m rhengine.options_registry status

# the published record for premium selling
py scripts/fetch_cboe_benchmarks.py               # ^BXM ^PUT ^SP500TR ^GSPC
py -m rhengine.alpha.buywrite                     # full report
py -m rhengine.alpha.buywrite --decades
py -m rhengine.alpha.buywrite --since 2016-08-01  # the "last decade" claim
```

Tests: 53 added (`test_options_spec.py` 25, `test_options_registry.py` 7,
`test_buywrite.py` 8 + 3 arithmetic), **350 passing** 2026-08-01. The buywrite
tests skip when `research_data/external/cboe` has not been fetched.
