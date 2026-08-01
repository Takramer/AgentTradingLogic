# Options accumulator — operating guide

**Built 2026-07-31.** `rhengine/options_registry.py`, 11 tests in
`tests/test_options_registry.py` (228 passing overall). Seeded with 10 SPY
contracts captured 2026-07-31T20:15Z.

## Why this has to run before anything else

`get_option_instruments state='expired'` has a retention window of **weeks**.
2026-07-17 resolved; 2026-06-19 did not. Once a contract falls out of that
listing you can no longer *discover* it by expiration date — but its full price
history stays retrievable forever **if you already hold the instrument UUID**.

So the thing that expires is not the data. It is the ability to ask for the
data. Every day this does not run is a permanently unrecoverable slice of
sample. Live quotes also carry IV and greeks while historical bars do not, so
the IV at each capture is likewise available only in the moment.

That is the entire justification. It is not a strategy and it cannot become one:
the module has no entry rule, no structure selection, and nothing in it can
produce a trading decision.

## The loop

The engine has no MCP access, so capture is a two-step handoff — the same
pattern `scripts/rh_spreads.py report quotes.json` already uses.

```bash
# 1. what to capture (offline)
py -m rhengine.options_registry plan --symbol SPY --spot 746.81

# 2. an AGENT runs the MCP calls the plan prints, and saves one file:
#    {captured_at, underlying, spot, instruments:[...], quotes:[...]}
#    instruments/quotes are the `data` blocks verbatim.

# 3. fold it in (offline, idempotent)
py -m rhengine.options_registry ingest capture.json

# 4. where things stand
py -m rhengine.options_registry status
py -m rhengine.options_registry due        # expired => measurable
```

Step 3 is **idempotent**: re-running a capture stores nothing and reports the
rows as duplicates, so a retried or double-fired cron is harmless.

## Cadence

**Once per trading day, mid-session (10:00–15:30 ET).** Not at the open or
close: `scripts/rh_spreads.py` established that the first ~30 and last ~10
minutes quote materially wider, and an accumulator that samples only at wide
moments builds a biased cost sample.

Missing a day costs one observation. Missing a *contract's whole life* costs
that contract permanently — so breadth of coverage matters more than never
missing a day.

## What gets stored

| file | what | why this shape |
|---|---|---|
| `research_data/options/contracts.json` | `{uuid: {symbol, exp, strike, type, first_seen, last_seen, captures}}` | **The artifact that expires.** Written atomically via `.tmp` + `os.replace` — a truncated registry loses the ability to reconstruct the sample at all. |
| `research_data/options/quotes.jsonl` | one row per (uuid, captured_at): bid/ask/mark, IV, all five greeks, OI, volume, fill-rate prices | JSONL so an interrupted append costs **one line, not the file**. `load_quotes` skips torn lines. |

## What is rejected, and why rejection matters more than storage

`validate()` drops a row rather than storing it when IV is NULL, the book is
crossed, there is no two-sided market, IV is outside (0, 5), or the contract is
unidentified. A quote with no instrument metadata is dropped as an *orphan* —
without strike and expiry it can never be matched to an underlying path, so
storing it is worse than losing it.

This is the failure mode an accumulator has: it never crashes. A bad row stored
as though it were good drags every later VRP average toward nothing, and the
damage only surfaces months later when the window has closed.

## Sampling is deliberately unconditional

`plan_strikes` emits a **symmetric mechanical ladder** on the strike grid, and
`plan_expirations` picks the expirations nearest 7 / 30 / 60 DTE. Neither
consults any signal. Selecting strikes by a signal would make the accumulated
sample conditional on that signal, and a conditional sample cannot answer an
unconditional question like *"is there a premium"*. Spanning tenors matters
because VRP is a term-structure phenomenon: a sample of only weeklies cannot
distinguish "a premium exists" from "a premium exists at one tenor".

## What this unblocks, and what it does not

Once contracts expire, `due` lists them and step 2 of the
`market_data_options.py` sequence becomes runnable: IV at entry versus realised
vol over the contract's **actual life**, net of the measured spread from
`options_costs.py`.

It does **not** make an options strategy possible yet. Still outstanding:

- **No retrospective sample.** This accumulates forward only. Nothing can be
  backtested until contracts captured here have lived and died.
- **The prior is still bad.** CBOE BXM returned ~1/3 of the S&P over the last
  decade; harvesting the premium is not the same as beating the index.
- **G1 is 0 of 10** and autonomy is earned (CLAUDE.md rule 6). Nothing here
  moves any strategy toward `autonomous`.

Do not add an IV-Rank function to this module or to `market_data_options.py` —
that gate is the wrong quantity, and the reasoning is in that module's docstring.
