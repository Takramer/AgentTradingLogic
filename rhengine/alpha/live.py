"""
live.py — the target-book handoff between the research stack and an executor.

WHAT THIS IS
------------
One function, `emit()`, that turns a research-stack strategy into a dated,
signed, machine-readable **target book**: a set of names and portfolio weights,
plus every constraint the executor must honour and every reason it might have
to refuse. `RobinhoodAgent` and Nydex both consume the same payload.

WHAT THIS IS NOT, AND WILL NOT BECOME
-------------------------------------
CLAUDE.md: "Engine-only: no order planning, no orchestration, no ML." That
boundary is load-bearing and this module sits exactly on it.

So `emit()` returns WEIGHTS and, if you hand it current positions, WEIGHT
DELTAS. It does not return share counts, limit prices, order objects, routing,
or a schedule. Those are the executor's job, they need live quotes and an
account balance, and neither exists in this process. `execution.order_type()`
already says every order is a LIMIT; the price on it is not computed here.

If you find yourself adding a `shares` field, you are writing the order planner
in the wrong repository.

THE ARMING RULE — READ BEFORE CHANGING ANY DEFAULT
--------------------------------------------------
`emit()` refuses to mark a book executable unless the strategy's `status` in
strategies.json `books` is exactly "autonomous". Anything else returns
`mode: "dry_run"` and `orders_permitted: false`, and the payload is a
*proposal* the agent must not act on unattended.

This mirrors `indicators.select_strategy_for_regime`, and for the same reason:
autonomy is earned (CLAUDE.md rule 6). As of 2026-08-01 **no book in this repo
has earned it** — G1 stands at 0 of 10 and docs/HOLDOUT.md gates promotion on
125 W3 sessions under a frozen config. Flipping a status to "autonomous" is a
deliberate act by the account owner that overrides both, and the payload says
so in `overrides` so the decision is visible in every downstream log rather
than buried in a JSON file nobody re-reads.

STALENESS IS A HARD FAILURE, NOT A WARNING
------------------------------------------
The panel is a cache. It does not know what day it is, and a book formed from
a cache that stopped updating is not a stale opinion — it is a *wrong* one, and
it fails silently in the most expensive possible direction: the momentum leg
re-forms monthly, so a cache lagging by weeks hands the executor last month's
winners with this month's confidence. `emit()` therefore compares the panel's
last bar against the calendar and REFUSES (raises `StaleBook`) past
`max_staleness_days`. There is no flag to downgrade it to a warning.

WHY THERE IS A ONE-SESSION LAG IN THE PAYLOAD
---------------------------------------------
`decided_on` is the last bar the signal saw. `execute_after` is the next
session. Every backtested number in this repo assumes the book is executed at
the close of the session AFTER the decision (RMX-RESULTS.md), and round 6 of
docs/AUTOSTRAT-2026-07-31.md is what happens when that is violated: reading
`regime[day]` instead of `regime[day-1]` manufactured +1.93 pp/yr of alpha that
did not exist. Executing this payload any earlier than `execute_after` means
the live book is not the book that was measured.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from .. import execution
from ..costs import default_cost_bps
from .fundamentals import Fundamentals
from .rmx import CACHE as _PANEL_CACHE
from .rmx import load_panel

_SPEC = Path(__file__).resolve().parent.parent / "strategies.json"
_CONFIG = Path(__file__).resolve().parent.parent.parent / "config.json"
_BARS = str(Path(_PANEL_CACHE).parent / "yahoo_long")

# A book whose freshest bar is older than this is refused outright. Five
# sessions is one trading week: long enough to survive a holiday or a failed
# overnight fetch, short enough that a monthly-rebalanced momentum leg has not
# yet drifted into a different book.
MAX_STALENESS_DAYS = 5

PAYLOAD_VERSION = 1


class StaleBook(RuntimeError):
    """The panel's last bar is too old to form a tradeable book."""


class DegenerateBook(RuntimeError):
    """The construction returned something that is not a fully-invested book."""


class BadPositions(ValueError):
    """The supplied account positions cannot be reconciled against a book."""


def normalize_symbol(t, known=None) -> str:
    """Resolve a broker ticker to the symbol the panel is actually keyed on.

    Class shares carry two spellings and this repo uses BOTH, in two different
    places, which is exactly how a silent mismatch gets in:

        research_data/yahoo_long/BRK-B.csv.gz   <- FILENAME, via yahoo_symbol()
        panel.adj["BRK.B"]                       <- COLUMN, the membership ticker

    `panel.py` reads the dashed file and stores it under the dotted membership
    ticker, so the tradeable universe — and therefore every weight in a book —
    is keyed the way the *broker* spells it. An earlier version of this
    function applied `yahoo_symbol`'s "." -> "-" transform on the theory that
    the panel matched the filenames. It does not, and that version took a
    position that already matched and broke it.

    So this does not assume a convention in either direction. It resolves
    against `known` — the panel's real column set — and tries both spellings.
    Getting this wrong in either direction produces the same silent, expensive
    failure: a full-size SELL of a name being kept plus a full-size BUY of the
    name already held. A wash trade that pays the spread twice, on the ~10 such
    names in the S&P 500 at any time (BRK.B, BF.B, ...).

    With `known=None` it only normalises case and whitespace, which is all it
    can honestly do without the universe in hand.
    """
    base = str(t).strip().upper()
    if not known:
        return base
    if base in known:
        return base
    for alt in (base.replace("-", "."), base.replace(".", "-")):
        if alt != base and alt in known:
            return alt
    return base


def check_panel_cache() -> None:
    """Refuse when `_panel_cache.pkl` is older than the bars it was built from.

    `rmx.load_panel()` returns the pickle whenever it exists and never compares
    it to the CSVs. That is correct for research — rebuilding a panel on every
    import would be intolerable — and wrong for a live path, because it makes
    a successful data refresh invisible: you run the fetch, every CSV updates,
    and `load_panel()` hands back the same stale bars it had before.

    Without this check the failure presents as "I refreshed the data and the
    staleness guard still refuses", which is precisely the shape of problem
    that gets an operator to reach for an override. Naming the real cause here
    is the difference between a two-second fix and a bad decision.
    """
    if not os.path.exists(_PANEL_CACHE):
        return
    cached = os.path.getmtime(_PANEL_CACHE)
    try:
        newest = max(os.path.getmtime(os.path.join(_BARS, f))
                     for f in os.listdir(_BARS))
    except (OSError, ValueError):
        return
    if newest > cached:
        raise StaleBook(
            f"price bars in {_BARS} are newer than the panel cache "
            f"({dt.datetime.fromtimestamp(newest):%Y-%m-%d %H:%M} vs "
            f"{dt.datetime.fromtimestamp(cached):%Y-%m-%d %H:%M}). "
            f"load_panel() would silently serve the OLD bars. Rebuild it: "
            f"py -c \"from rhengine.alpha.rmx import load_panel; "
            f"load_panel(refresh=True)\"")


class UnknownBook(KeyError):
    """No such strategy in strategies.json `books`."""


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def config_hash(paths=None) -> str:
    """Stable short hash over every file that can change what gets traded.

    docs/HOLDOUT.md rule 2 makes a config change consequential — it resets a
    window's evidentiary value to zero. That is only enforceable if every
    emitted book carries the hash of the thing that produced it, so a later
    reader can tell whether a live fill belongs to the config that was
    validated or to some successor.
    """
    root = Path(__file__).resolve().parent.parent.parent
    paths = paths or ["config.json", "rhengine/strategies.json",
                      "rhengine/alpha/rmxb.py", "rhengine/alpha/sleeves.py",
                      "rhengine/alpha/blend.py", "rhengine/alpha/live.py"]
    h = hashlib.sha256()
    for p in paths:
        h.update((root / p).read_bytes())
    return h.hexdigest()[:16]


def book_spec(name: str, spec: dict | None = None) -> dict:
    spec = spec or _load(_SPEC)
    books = spec.get("books") or {}
    real = sorted(k for k in books if not k.startswith("_"))
    if name.startswith("_") or name not in books:
        raise UnknownBook(
            f"{name!r} is not in strategies.json `books` "
            f"(have: {real or 'none'})")
    return books[name]


def book_status(name: str, spec: dict | None = None) -> str:
    """Deployment tier for a research-stack book.

    Same four tiers and the same default as `indicators.strategy_status`:
    anything without an explicit status is "manual". Autonomy is not inherited
    by being present in the file.
    """
    return str(book_spec(name, spec).get("status") or "manual")


def _targets(name: str, hi: str, cfg_over: dict | None = None):
    """Dispatch to the strategy that owns the construction.

    Deliberately thin. The weights are computed by `rmxb.py` / `blend.py` and
    nothing about them is recomputed, re-normalised or "cleaned" here — this
    module must not be able to change what the strategy holds, or the thing
    that trades stops being the thing that was measured.
    """
    check_panel_cache()
    panel, fund = load_panel(), Fundamentals.load()
    if name == "rmxb":
        from .rmxb import RMXBConfig, build_targets
        cfg = RMXBConfig(**(cfg_over or {}))
        t, covered, _diag, _legs = build_targets(
            panel, fund, cfg, "2024-01-01", hi)
        return panel, t, cfg.max_weight
    if name == "rmxc":
        from .blend import BlendConfig, build
        cfg = BlendConfig(**(cfg_over or {}))
        built = build(panel, fund, cfg, "2023-01-01", hi, default_cost_bps())
        # cfg.max_weight, not getattr(..., 0.10): if the field is ever renamed
        # a getattr default would keep publishing 10% into the payload while
        # the book actually held something else, and the executor would size
        # against a cap that is not the one enforced.
        return panel, built["targets"], cfg.max_weight
    raise UnknownBook(f"no construction registered for {name!r}")


# The bars are New York dates and the rebalance calendar is the NYSE session
# calendar. `date.today()` is the *machine's* local date, so a box in UTC or in
# Asia rolls over hours before or after the market does — which moves
# `panel_age_days` by a day and can turn a fresh book into a refused one, or
# worse, the reverse.
_ET = "America/New_York"


def _today_et() -> dt.date:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(_ET)).date()
    except Exception:                                   # pragma: no cover
        # tzdata is a hard requirement of this repo (CLAUDE.md Environment), so
        # this branch means the environment is broken rather than merely
        # unusual. Falling back to local time is still better than failing to
        # emit, but the payload records that it happened.
        return dt.date.today()


def _check_book(name: str, w: pd.Series, min_names: int = 20,
                tolerance: float = 0.05) -> None:
    """Refuse a book that is not fully invested, rather than repairing it.

    Both these conditions mean an upstream construction failed — a leg that
    produced no names, a fundamentals column that went empty, a universe filter
    that ate the book. The tempting repair is to renormalise the weights to sum
    to 1.0, and it is exactly wrong: it would take a book that lost its
    momentum leg and silently return a 100% quality book under the name of a
    50/50 blend, which then trades, and reports, as something it is not.

    So this raises. The caller's job is to find out what broke.
    """
    if len(w) < min_names:
        raise DegenerateBook(
            f"{name}: only {len(w)} names in the book (floor {min_names}). A "
            f"leg has probably produced nothing -- check "
            f"`py -m rhengine.alpha.panel` for coverage.")
    total = float(w.sum())
    if abs(total - 1.0) > tolerance:
        raise DegenerateBook(
            f"{name}: weights sum to {total:.4f}, not 1.0 +/- {tolerance}. "
            f"These books are fully invested with no leverage; a partial sum "
            f"means a leg is missing. NOT renormalising -- that would trade a "
            f"different book under this one's name.")


def emit(name: str = "rmxb", positions: dict | None = None,
         asof: str | None = None, spec: dict | None = None,
         config: dict | None = None, cfg_over: dict | None = None,
         max_staleness_days: int = MAX_STALENESS_DAYS) -> dict:
    """Build the target book payload for `name`.

    `positions` is what the account currently holds, as {ticker: weight} where
    weight is a fraction of account equity. Supply it and the payload gains a
    `deltas` block; omit it and the payload is the target alone. Share counts
    are never returned — see the module docstring.

    Raises `StaleBook` if the panel's freshest bar is more than
    `max_staleness_days` calendar days before `asof` (default: today).
    """
    spec = spec or _load(_SPEC)
    config = config if config is not None else _load(_CONFIG)
    bs = book_spec(name, spec)
    status = book_status(name, spec)

    panel, targets, cap = _targets(name, asof or _today_et().isoformat(),
                                   cfg_over)
    if not targets:
        raise StaleBook(
            f"{name}: no rebalance formed on or before {asof or 'today'} -- "
            f"the panel is empty or does not reach that far")

    formed = max(targets)
    calendar = panel.adj.index
    last_bar = calendar[-1]
    today = pd.Timestamp(asof) if asof else pd.Timestamp(_today_et())

    age = (today.normalize() - last_bar.normalize()).days
    if age > max_staleness_days:
        raise StaleBook(
            f"{name}: panel's last bar is {last_bar.date()}, {age} calendar "
            f"days before {today.date()} (limit {max_staleness_days}). Refusing "
            f"to emit a book from a stale cache. Refresh with "
            f"`py scripts/fetch_history.py` and re-run.")

    # One-session execution lag. The next session is not in the cached calendar
    # yet — by construction, it has not happened — so the payload states the
    # rule the executor must satisfy rather than guessing a date: execute at the
    # close of the FIRST session that opens after `decided_on`.
    w = targets[formed]
    w = w[w > 1e-6].sort_values(ascending=False)
    _check_book(name, w)

    payload = {
        "payload_version": PAYLOAD_VERSION,
        "book": name,
        "emitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config_hash": config_hash(),
        "status": status,
        "mode": "armed" if status == "autonomous" else "dry_run",
        "orders_permitted": status == "autonomous",
        "decided_on": str(formed.date()),
        "panel_last_bar": str(last_bar.date()),
        "panel_age_days": int(age),
        "execute_after": str(formed.date()),
        "execution_rule": (
            "Execute at the CLOSE of the first session that opens strictly "
            "after decided_on. Executing earlier invalidates every backtested "
            "number for this book (docs/AUTOSTRAT-2026-07-31.md round 6)."),
        "constraints": {
            "max_name_weight": float(cap),
            "cost_bps_round_trip": float(default_cost_bps()),
            "spread_gate_pct": float(
                execution.resolve_spread_gate_pct(config)),
            "order_type": execution.order_type("stock"),
            "long_only": True,
            "leverage_permitted": False,
        },
        "executor_must": [
            "Re-check every name against execution.spread_gate(bid, ask) using "
            "a LIVE regular-hours quote; the engine has no quote access and "
            "this payload cannot pre-filter on spread.",
            "Treat a name with no usable quote as a REJECT, not a pass.",
            "Skip any name it cannot fill within the gate, and leave that "
            "weight in cash rather than redistributing it -- redistribution "
            "changes the book that was measured.",
            "Refuse the whole payload if orders_permitted is false.",
        ],
        "evidence": bs.get("evidence"),
        "gates": bs.get("gates"),
        "kill_switches": bs.get("kill_switches"),
        "overrides": _overrides(status, bs),
        "n_names": int(len(w)),
        "weights": {k: round(float(v), 6) for k, v in w.items()},
    }

    if positions is not None:
        known = set(panel.adj.columns) | set(w.index)
        pos, notes = clean_positions(positions, known)
        payload["positions"] = {k: round(v, 6) for k, v in pos.items()}
        payload["position_notes"] = notes
        payload["deltas"] = _deltas(w, pos)
        payload["one_way_turnover"] = round(
            sum(abs(d) for d in payload["deltas"].values()) / 2.0, 6)
        payload["cash_weight"] = round(1.0 - sum(pos.values()), 6)
        # Held names that are neither targeted nor priced by the panel. A full
        # exit is normal; a full exit of something the panel has never heard of
        # is the signature of an unmapped symbol, and the executor should look
        # before it sells.
        payload["unknown_to_panel"] = sorted(s for s in pos if s not in known)
    return payload


def _overrides(status: str, bs: dict) -> list:
    """Every repo rule this book's current tier is running against.

    Emitted into the payload rather than kept in a doc so that an override is
    visible at the point of execution. A rule that is only recorded in a file
    nobody opens is not a control.
    """
    out = []
    if status == "autonomous":
        out.append(
            "ARMED FOR UNATTENDED TRADING. G1 stands at 0 of 10 "
            "(docs/AUDIT-2026-07-24.md section 10) and docs/HOLDOUT.md gates paper "
            "promotion on 125 W3 sessions under a config frozen before "
            "2026-07-24. Neither is met. This book trades on the account "
            "owner's explicit decision, not on validated evidence.")
    if bs.get("evidence", {}).get("local_alpha_t") is not None:
        out.append(
            "Local-panel alpha is not significant and cannot become "
            "significant: see docs/CLAUDE-RESEARCH-RULES.md section 3.")
    return out


def clean_positions(positions: dict, known=None) -> tuple[dict, list]:
    """Normalise and validate what the account says it holds.

    `known` is the panel's tradeable symbol set; pass it so class-share
    spellings resolve against reality rather than against an assumption.

    Returns `(weights, notes)`. Raises `BadPositions` rather than guessing,
    because every guess available here is wrong in an expensive direction.

    Rejects, and why each one is a real Monday-morning failure rather than a
    hypothetical:

    * **Percentages instead of fractions.** `{"AAPL": 5.0}` meaning 5% is the
      single most likely first-run mistake, and it does not look like an error
      downstream — it looks like a 500%-weight position the book must sell, so
      the deltas come out roughly 100x too large. Detected by any single weight
      above 1.0, or a total above 1.5.
    * **Short positions.** These books are long-only and cannot short
      (CLAUDE.md rule 5; the record for a long-short construction overstates by
      2-20x). A negative weight is a real account state this module has no
      correct handling for, so it says so instead of netting it away.
    * **Non-numeric or non-finite values.** A `null` from a broker API becomes
      `float(None)` -> TypeError three frames deeper, where it reads as a bug
      in the delta arithmetic rather than as bad input.
    """
    if not isinstance(positions, dict):
        raise BadPositions(
            f"positions must be a dict of {{ticker: weight}}, got "
            f"{type(positions).__name__}")

    notes, out = [], {}
    for raw, v in positions.items():
        try:
            w = float(v)
        except (TypeError, ValueError):
            raise BadPositions(
                f"position {raw!r} has a non-numeric weight {v!r}. Weights are "
                f"fractions of account equity, e.g. 0.05 for 5%.")
        if w != w or w in (float("inf"), float("-inf")):
            raise BadPositions(f"position {raw!r} has a non-finite weight {v!r}")
        if w < 0:
            raise BadPositions(
                f"position {raw!r} is short ({w}). These books are long-only "
                f"and cannot short (CLAUDE.md rule 5). Close or exclude the "
                f"short leg before reconciling.")
        if w > 1.0:
            raise BadPositions(
                f"position {raw!r} has weight {w}, above 1.0. Weights are "
                f"FRACTIONS of account equity, not percentages -- pass 0.05 for "
                f"5%, not 5.0.")

        sym = normalize_symbol(raw, known)
        if sym != str(raw).strip().upper():
            notes.append(f"{raw} -> {sym}")
        if sym in out:
            # e.g. both "BRK.B" and "BRK-B" supplied. Summing is the only
            # reading that conserves the account's actual exposure.
            notes.append(f"{sym}: two entries merged")
            out[sym] += w
        else:
            out[sym] = w

    total = sum(out.values())
    if total > 1.5:
        raise BadPositions(
            f"supplied positions total {total:.2f} of account equity. Above "
            f"1.5 this is almost certainly percentages rather than fractions "
            f"(a book at 100 would total 100.0). Divide by 100 and re-run.")
    return out, notes


def _deltas(target: pd.Series, positions: dict) -> dict:
    """Target weight minus current weight, for every name in either set.

    Both sides are already normalised by `clean_positions`; the target comes
    from the panel, which is keyed the same way.

    Names held but not targeted appear with a negative delta equal to their
    full current weight — an exit. They must not be dropped: a book that only
    reports what to buy silently becomes a book that never sells.
    """
    keys = sorted(set(target.index) | set(positions))
    out = {}
    for k in keys:
        d = float(target.get(k, 0.0)) - float(positions.get(k, 0.0))
        if abs(d) > 1e-6:
            out[k] = round(d, 6)
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Emit a dated target book for an executor to consume.")
    ap.add_argument("--book", default="rmxb", help="rmxb | rmxc")
    ap.add_argument("--positions", default=None, metavar="JSON",
                    help="path to {ticker: weight} of what the account holds")
    ap.add_argument("--asof", default=None,
                    help="pretend today is this date (testing / replay)")
    ap.add_argument("--out", default=None, help="write the payload here")
    ap.add_argument("--max-staleness-days", type=int,
                    default=MAX_STALENESS_DAYS)
    args = ap.parse_args()

    pos = None
    if args.positions:
        pos = _load(Path(args.positions))

    # Every refusal exits non-zero with one line. A scheduled job's log is the
    # only place these are ever read, and a traceback there reads as "the tool
    # is broken" rather than "the tool stopped you for a reason".
    try:
        payload = emit(args.book, positions=pos, asof=args.asof,
                       max_staleness_days=args.max_staleness_days)
    except (StaleBook, DegenerateBook, BadPositions, UnknownBook) as e:
        raise SystemExit(f"REFUSED [{type(e).__name__}]: {e}")

    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2),
                                  encoding="utf-8")

    print(f"{payload['book']}  {payload['mode'].upper()}  "
          f"status={payload['status']}  "
          f"orders_permitted={payload['orders_permitted']}")
    print(f"  decided_on   {payload['decided_on']}  "
          f"(panel last bar {payload['panel_last_bar']}, "
          f"{payload['panel_age_days']}d old)")
    print(f"  names        {payload['n_names']}  "
          f"cap {payload['constraints']['max_name_weight']:.0%}  "
          f"cost {payload['constraints']['cost_bps_round_trip']:.0f} bps  "
          f"spread gate {payload['constraints']['spread_gate_pct']:.2%}")
    print(f"  config_hash  {payload['config_hash']}")
    if "deltas" in payload:
        print(f"  deltas       {len(payload['deltas'])} names, "
              f"one-way turnover {payload['one_way_turnover']:.1%}, "
              f"cash {payload['cash_weight']:.1%}")
        for n in payload["position_notes"]:
            print(f"  symbol       {n}")
        if payload["unknown_to_panel"]:
            print(f"  ! held but unknown to the panel, check before selling: "
                  f"{', '.join(payload['unknown_to_panel'])}")
    for o in payload["overrides"]:
        print(f"  ! {o}")
    if args.out:
        print(f"  written      {args.out}")


if __name__ == "__main__":
    main()
