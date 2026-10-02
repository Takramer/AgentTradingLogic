# Go-live runbook — RobinhoodAgent target books

**Written 2026-08-01** for a first live session on **Monday 2026-08-03**.
Code: `rhengine/alpha/live.py`, 23 tests in `tests/test_live.py`.
Spec: `rhengine/strategies.json` → `books`.

---

## State right now, in one block

| | |
|---|---|
| `books.rmxb.status` | **`manual`** — emits, but `orders_permitted: false` |
| `books.rmxc.status` | `user_configurable` — Nydex only, never agent-routed |
| `regime_routing` | unrouted; the legacy scan path still stands flat |
| G1 | **0 of 10** |
| W3 holdout | ~8 sessions of the 125 the gate requires |

**Nothing trades unattended until a human edits one string.** That is
deliberate and it is the whole design of `live.emit()`.

## The one edit that arms it

```json
"books": { "rmxb": { "status": "autonomous" } }
```

Nothing else changes. No code, no flag, no environment variable. On the next
`emit()` the payload flips to `mode: "armed"`, `orders_permitted: true`, and
**picks up an `overrides` block that says in plain text that it is trading
against 0-of-10 gates**. That text travels into the executor's logs on every
single run, by design — an override that is only recorded in a document nobody
opens is not a control.

`tests/test_live.py::ArmingRule` pins this: six near-miss strings
(`"Autonomous"`, `"autonomous "`, `"true"`, …) all fail closed, and
`test_shipped_default_is_not_armed` fails the build if the committed default is
ever `autonomous`.

## What arming actually overrides

State these to yourself before flipping it, because they do not go away:

1. **G1-2** — net alpha t ≥ 2.0 vs SPY is not established, and on this panel it
   is not *establishable*: +1.78%/yr against 5.5%/yr tracking error needs ~39
   years. The evidence is French's 63 years, not anything measured here.
2. **The holdout gate** — 125 W3 sessions under a config frozen before
   2026-07-24. There are about 8.
3. **The trailing year is not a reason.** RMX-B returned +25.3% against SPY's
   +18.2%, at **beta 1.39 vs its own covered universe and −0.69%/yr alpha
   against it**. That year's excess was risk, not skill.
4. **Expect to trail the index roughly 4 years in 10.** If that is not
   acceptable, hold SPY — that is not a figure of speech, it is the documented
   recommendation in `docs/RMX-RESULTS.md`.

## Monday procedure

Run in this order. Any step that refuses, stop — do not work around it.

```bash
# 1. refresh the panel. The book is formed from a CACHE and the cache does not
#    know what day it is. --force IS REQUIRED — see the warning below.
py scripts/fetch_history.py --force   # ~30-45 min, 1,213 tickers
py -m rhengine.alpha.panel            # coverage + PIT universe check

# 2. tests. 269 of them. A red suite is a stop, not a warning.
py -m unittest discover tests

# 2b. the panel cache does NOT notice the refresh on its own
py -c "from rhengine.alpha.rmx import load_panel; load_panel(refresh=True)"

# 3. emit the book. REFUSES on a stale panel — see below.
py -m rhengine.alpha.live --book rmxb --out book.json

# 4. with the account's current weights, to get deltas and turnover
py -m rhengine.alpha.live --book rmxb --positions positions.json --out book.json
```

`positions.json` is `{"AAPL": 0.05, "LLY": 0.10, ...}` — fractions of account
equity, supplied by the executor. The engine has no account access and never
will.

### `fetch_history.py` without `--force` refreshes NOTHING

Found 2026-08-01 while preparing this runbook, and it is the single most
dangerous thing in the go-live path:

```
py scripts/fetch_history.py       ->  DONE ok=2 miss=428 skip=783
```

`skip=783`. The loop is `if os.path.exists(path) and not args.force: continue`
— it back-fills tickers that are **missing** and never updates one that is
present. It does not append; there is no incremental path. So the command
written in `docs/RMX-RESULTS.md` §Reproducing, and in the first draft of this
runbook, is a **no-op for every ticker you already have**, and it exits `0`
with a cheerful `DONE`.

Without `alpha/live.py`'s staleness refusal, Monday would have looked like
this: run the documented refresh, see `DONE`, emit a book, and trade a
**momentum leg formed from data ending 2026-07-24** — last month's winners,
executed with this month's confidence, silently.

`fetch_history.py` is deliberately **not** patched to fix this. It is the data
path, and G6 makes any change to it invalidate every prior result in the
repository. Use `--force`; the guard in `live.py` is what catches you if you
forget.

### Timing

The book is formed at a **month-end rebalance** and executed at the close of
the **first session that opens after** `decided_on`. 2026-08-03 is the first
trading day of August, so a monthly momentum re-form is due. Emit on Monday,
execute at Tuesday's close — or emit over the weekend and execute Monday's
close. Executing *on* `decided_on` is the lookahead that manufactured
+1.93 pp/yr of fake alpha in round 6 of `docs/AUTOSTRAT-2026-07-31.md`.

## Why it will refuse, and what each refusal means

Every refusal exits non-zero with a single `REFUSED [Type]: …` line — never a
traceback, because in a scheduled job's log a traceback reads as "the tool is
broken" rather than "the tool stopped you for a reason".

| `REFUSED [...]` | Meaning | Fix |
|---|---|---|
| `StaleBook: panel's last bar is …, N days before …` | The cache is stale. A monthly momentum leg served from a two-week-old cache is not a stale opinion, it is the *wrong book* — last month's winners with this month's confidence. | `py scripts/fetch_history.py --force` |
| `StaleBook: price bars … are newer than the panel cache` | The refresh worked but `load_panel()` is still serving the old pickle | `py -c "from rhengine.alpha.rmx import load_panel; load_panel(refresh=True)"` |
| `DegenerateBook: weights sum to 0.5…` | A leg produced nothing. **Not** renormalised — that would trade a 100% quality book under the name of a 50/50 blend | `py -m rhengine.alpha.panel` for coverage |
| `DegenerateBook: only N names` | Same cause, caught by the name floor (20) | as above |
| `BadPositions: … above 1.0 … FRACTIONS not percentages` | You passed `5.0` meaning 5% | divide by 100 |
| `BadPositions: … is short` | The account holds a short. These books are long-only | close or exclude the short leg |
| `BadPositions: non-numeric weight` | A `null` came back from the broker API | fix upstream |
| `orders_permitted: false` | Status is not `autonomous`. Working as designed. | The one edit above, if you mean it |

Staleness has **no override flag**, and adding one would be a mistake: this is
the failure mode that does not announce itself. Every other refusal is loud.

### The symbol trap, which is silent and costs real money

Robinhood returns class shares in dotted notation (`BRK.B`); the panel is keyed
on the fetch script's transform (`BRK-B`). Left unmapped, the reconciliation
does not error — it emits a **full-size sell of a name you are keeping and a
full-size buy of the name you already hold**. A wash trade that pays the spread
twice, on roughly ten S&P 500 names at any time.

`normalize_symbol()` applies the identical transform to both sides, reports
every remap in `position_notes`, and merges the exposure if you supply both
notations. Separately, anything you hold that the panel has never priced lands
in `unknown_to_panel` — a full exit is normal, a full exit of something
unrecognised is the signature of a symbol that failed to map, and it is printed
with a `!` so it is looked at before it is sold.

## The payload contract

What `emit()` returns, and the boundary it holds.

**It returns:** `weights` (ticker → fraction of equity), `deltas` against
supplied positions, `one_way_turnover`, `decided_on` / `execute_after`,
`constraints` (10% name cap, 30 bps, 1.0% spread gate, limit orders, long-only,
no leverage), `executor_must` (four obligations), `evidence`, `gates`,
`kill_switches`, `overrides`, and `config_hash`.

**It never returns** share counts, limit prices, order objects, routing or a
schedule. CLAUDE.md: *engine-only, no order planning, no orchestration*. Those
need live quotes and an account balance, neither of which exists in this
process. `tests/test_live.py::test_no_order_planning_fields_leak_in` fails the
build if a `shares` or `quantity` key ever appears.

### Four obligations the executor carries

The payload states them; they are not optional.

1. **Re-check every name against a live regular-hours quote** with
   `execution.spread_gate(bid, ask)`. The engine has no quote access, so the
   payload cannot pre-filter. The 1% gate rejects ~8.5% of the real book, and
   about half of the least-liquid ADV decile.
2. **No usable quote is a REJECT**, not a pass.
3. **Skip what you cannot fill, and leave the weight in cash.** Do not
   redistribute — redistribution changes the book that was measured.
4. **Refuse the whole payload if `orders_permitted` is false.**

## Kill switches — wire these before the first fill, not after

From `docs/AUDIT-2026-07-24.md` §10 G4, carried in every payload under
`kill_switches`:

| Condition | Action |
|---|---|
| Trailing 60-day net return below the MC **p5** path | halve size |
| Below the **p1** path | flat, revalidate |
| Realized round-trip cost > **1.5× modelled** for 20 consecutive days | flat immediately |
| Panel staleness > 5 days | refuse to emit (automatic) |

The cost switch is the one that matters most and the one most likely to fire:
modelled cost is a **flat 30 bps scalar** across a universe measured at 5.5 to
98.1 bps. If real fills come in wide, that switch is the only thing standing
between the book and a cost drag larger than the entire documented edge.

## What is still not built

- **G2 evidence.** ≥3 months and ≥100 paper trades, realized slippage within
  1.5× modelled, signal-time logs reconciling to simulated entries on ≥95% of
  trades. None of it exists yet, and it is the only thing that can turn the
  arming decision from a preference into a result.
- **A cost ledger.** The 1.5× kill switch needs realized cost per fill recorded
  and compared to 30 bps. Nothing records it today.
- **RMX-C in Nydex.** The spec entry and `cost_profile` are in place; the UI
  work is not in this repo.
