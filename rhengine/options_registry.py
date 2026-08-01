"""
options_registry.py — the forward accumulator for option contract data.

THE PROBLEM THIS SOLVES, AND WHY IT CANNOT WAIT

`get_option_instruments state='expired'` has a retention window of WEEKS.
2026-07-17 resolved; 2026-06-19 did not. Once a contract falls out of that
listing you can no longer DISCOVER it by expiration date — but its full price
history stays retrievable forever if you already hold the instrument UUID.

So the thing that expires is not the data. It is the ability to ask for the
data. That makes the UUID registry the one artifact that must start
accumulating immediately, and it makes every day of delay a permanently
unrecoverable slice of sample. Live quotes also carry IV and greeks
(re-spiked 2026-07-31) while historical bars do not, so the IV at each capture
is likewise available only in the moment.

WHAT THIS MODULE IS NOT. It contains no strategy, no structure selection and no
entry rule — same discipline as `market_data_options.py` and `options_costs.py`.
It accumulates and it reports coverage. Nothing here can produce a trading
decision.

ARCHITECTURE: the engine has no MCP access, so capture is a two-step handoff,
exactly the pattern `scripts/rh_spreads.py report quotes.json` already uses.

    1. `plan`    (offline)  -> what to capture today, as a strike ladder
    2. an AGENT calls the Robinhood MCP tools and saves the raw payloads
    3. `ingest`  (offline)  -> validate, dedupe, append to the registry
    4. `status`  (offline)  -> coverage, gaps, and what is about to expire

Storage is two files under research_data/options/, both append-only in effect:

    contracts.json   {instrument_id: {symbol, exp, strike, type, first_seen,
                     last_seen, captures}}  — THE artifact that expires. Losing
                     this loses the ability to ever reconstruct the sample.
    quotes.jsonl     one JSON object per (instrument_id, captured_at): the
                     quote and greeks as served. JSONL because an interrupted
                     append costs one line, not the file.

Usage:
    py -m rhengine.options_registry plan --symbol SPY --spot 746.81
    py -m rhengine.options_registry ingest capture.json
    py -m rhengine.options_registry status
    py -m rhengine.options_registry due          # ripe for a VRP measurement
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

DATA_DIR = os.path.join("research_data", "options")
CONTRACTS = os.path.join(DATA_DIR, "contracts.json")
QUOTES = os.path.join(DATA_DIR, "quotes.jsonl")

# Fields copied verbatim from a get_option_quotes result. Anything not in this
# list is dropped rather than stored half-understood.
QUOTE_FIELDS = ("bid_price", "ask_price", "mark_price", "adjusted_mark_price",
                "implied_volatility", "delta", "gamma", "theta", "vega", "rho",
                "open_interest", "volume", "previous_close_price",
                "high_fill_rate_buy_price", "low_fill_rate_buy_price",
                "high_fill_rate_sell_price", "low_fill_rate_sell_price")

# A capture is useless for VRP without these three.
REQUIRED = ("bid_price", "ask_price", "implied_volatility")


# ------------------------------------------------------------------ planning

def plan_strikes(spot, n_each_side=4, step=5.0):
    """Strike ladder around spot, on the `step` grid.

    Deliberately symmetric and mechanical: choosing strikes by any signal would
    make the accumulated sample conditional on that signal, and a conditional
    sample cannot answer an unconditional question like "is there a premium".
    """
    if spot <= 0 or step <= 0 or n_each_side < 0:
        raise ValueError("spot and step must be positive")
    atm = round(spot / step) * step
    return [round(atm + i * step, 4)
            for i in range(-n_each_side, n_each_side + 1)]


def plan_expirations(available, asof, targets=(7, 30, 60)):
    """Pick the expiration nearest each target DTE.

    Spanning tenors matters: VRP is a term-structure phenomenon, and a sample of
    only weeklies cannot distinguish "premium exists" from "premium exists at
    one tenor". Returns [] when `available` is empty rather than inventing dates.
    """
    a = date.fromisoformat(str(asof)[:10])
    dated = sorted((date.fromisoformat(e), e) for e in available)
    dated = [(d, e) for d, e in dated if (d - a).days >= 0]
    out = []
    for t in targets:
        if not dated:
            break
        best = min(dated, key=lambda de: abs((de[0] - a).days - t))
        if best[1] not in out:
            out.append(best[1])
    return out


# ------------------------------------------------------------------- storage

def _load_contracts(path=CONTRACTS):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_contracts(reg, path=CONTRACTS):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=1, sort_keys=True)
    os.replace(tmp, path)          # atomic: a crash never truncates the registry


def load_quotes(path=QUOTES):
    """All quote rows. Tolerates a torn final line from an interrupted append —
    that is the whole reason the store is JSONL rather than one JSON array."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ------------------------------------------------------------------- ingest

def normalise(capture):
    """MCP payload -> flat rows. Pure; does no I/O.

    `capture` is {"captured_at","underlying","spot", "instruments":[...],
    "quotes":[...]} where instruments/quotes are the `data` blocks from
    get_option_instruments / get_option_quotes verbatim. The two are joined on
    instrument id, because a quote alone does not say what contract it is.
    """
    meta = {}
    for i in capture.get("instruments", []):
        meta[i["id"]] = {
            "symbol": i.get("chain_symbol"),
            "exp": i.get("expiration_date"),
            "strike": float(i["strike_price"]) if i.get("strike_price") else None,
            "type": i.get("type"),
        }
    asof = capture["captured_at"]
    spot = float(capture["spot"]) if capture.get("spot") is not None else None
    rows, orphans = [], []
    for q in capture.get("quotes", []):
        iid = q.get("instrument_id")
        if iid not in meta:
            # A quote we cannot identify is not storable: without strike and
            # expiry it can never be matched to an underlying path.
            orphans.append(iid)
            continue
        row = {"instrument_id": iid, "captured_at": asof, "spot": spot}
        row.update(meta[iid])
        for k in QUOTE_FIELDS:
            v = q.get(k)
            if v is None or v == "":
                row[k] = None
            elif k in ("open_interest", "volume"):
                row[k] = int(v)
            else:
                row[k] = float(v)
        rows.append(row)
    return rows, orphans


def validate(row):
    """Reasons this row cannot support a VRP measurement. Empty list = usable."""
    bad = []
    for k in REQUIRED:
        if row.get(k) is None:
            bad.append(f"missing {k}")
    b, a = row.get("bid_price"), row.get("ask_price")
    if b is not None and a is not None:
        if a < b:
            bad.append("crossed quote")
        if b <= 0 and a <= 0:
            bad.append("no two-sided market")
    iv = row.get("implied_volatility")
    if iv is not None and not (0.0 < iv < 5.0):
        bad.append(f"implausible IV {iv}")
    if not row.get("exp") or row.get("strike") is None:
        bad.append("unidentified contract")
    return bad


def ingest(capture, contracts_path=CONTRACTS, quotes_path=QUOTES):
    """Validate, dedupe on (instrument_id, captured_at), append. Idempotent:
    re-ingesting the same capture adds nothing, so a retried cron is harmless."""
    rows, orphans = normalise(capture)
    reg = _load_contracts(contracts_path)
    seen = {(q["instrument_id"], q["captured_at"]) for q in load_quotes(quotes_path)}

    kept, dup, rejected = [], 0, []
    for r in rows:
        problems = validate(r)
        if problems:
            rejected.append((r["instrument_id"], "; ".join(problems)))
            continue
        if (r["instrument_id"], r["captured_at"]) in seen:
            dup += 1
            continue
        kept.append(r)
        seen.add((r["instrument_id"], r["captured_at"]))

    if kept:
        os.makedirs(os.path.dirname(quotes_path) or ".", exist_ok=True)
        with open(quotes_path, "a", encoding="utf-8") as f:
            for r in kept:
                f.write(json.dumps(r, sort_keys=True) + "\n")

    for r in kept:
        e = reg.setdefault(r["instrument_id"], {
            "symbol": r["symbol"], "exp": r["exp"], "strike": r["strike"],
            "type": r["type"], "first_seen": r["captured_at"], "captures": 0})
        e["last_seen"] = r["captured_at"]
        e["captures"] = e.get("captures", 0) + 1
    if kept:
        _save_contracts(reg, contracts_path)

    return {"stored": len(kept), "duplicates": dup, "rejected": rejected,
            "orphan_quotes": orphans, "contracts_tracked": len(reg)}


# ------------------------------------------------------------------ coverage

def coverage(contracts_path=CONTRACTS, quotes_path=QUOTES):
    reg = _load_contracts(contracts_path)
    rows = load_quotes(quotes_path)
    by_day = defaultdict(int)
    for r in rows:
        by_day[str(r["captured_at"])[:10]] += 1
    return {"contracts": len(reg), "quotes": len(rows),
            "days": sorted(by_day), "per_day": dict(by_day)}


def due_for_vrp(asof, contracts_path=CONTRACTS):
    """Contracts that have EXPIRED and were captured at least once.

    These are the only ones a realised-vs-implied comparison can be run on,
    because RV must be measured over the contract's ACTUAL life — which is not
    known until it ends. See market_data_options.vrp.
    """
    a = date.fromisoformat(str(asof)[:10])
    out = []
    for iid, e in _load_contracts(contracts_path).items():
        if not e.get("exp"):
            continue
        if date.fromisoformat(e["exp"]) <= a and e.get("captures", 0) > 0:
            out.append(dict(e, instrument_id=iid))
    return sorted(out, key=lambda e: (e["exp"], e["strike"] or 0))


def at_risk(asof, window_days=21, contracts_path=CONTRACTS):
    """Tracked contracts whose expiry is old enough that DISCOVERY may already
    have lapsed. Purely informational — we hold the UUIDs, so these are safe.
    It exists to show what would have been lost without the registry."""
    a = date.fromisoformat(str(asof)[:10])
    cut = a - timedelta(days=window_days)
    return [dict(e, instrument_id=i)
            for i, e in _load_contracts(contracts_path).items()
            if e.get("exp") and date.fromisoformat(e["exp"]) < cut]


# ----------------------------------------------------------------------- cli

def _cmd_plan(args):
    strikes = plan_strikes(args.spot, args.strikes, args.step)
    print(f"CAPTURE PLAN — {args.symbol} @ {args.spot:.2f}\n")
    print("1. get_option_chains(underlying_symbol='%s')" % args.symbol)
    print("2. pick expirations nearest %s DTE from chain.expiration_dates"
          % ",".join(str(t) for t in args.targets))
    print("3. get_option_instruments(chain_id=..., expiration_dates=<those>,")
    print("     type='call'|'put') and keep ONLY these strikes:")
    print("     " + ", ".join(f"{s:g}" for s in strikes))
    print("4. get_option_quotes(instrument_ids=[...])  (<=20 per call)")
    print("5. save {captured_at, underlying, spot, instruments:[...], "
          "quotes:[...]} and run:")
    print("     py -m rhengine.options_registry ingest capture.json\n")
    print("Strikes are a symmetric mechanical ladder ON PURPOSE: selecting them")
    print("by any signal would make the sample conditional on that signal.")


def _cmd_ingest(args):
    capture = json.load(open(args.file, encoding="utf-8"))
    res = ingest(capture)
    print(f"stored {res['stored']} | duplicates {res['duplicates']} | "
          f"rejected {len(res['rejected'])} | "
          f"contracts tracked {res['contracts_tracked']}")
    for iid, why in res["rejected"][:20]:
        print(f"  REJECT {iid}: {why}")
    if res["orphan_quotes"]:
        print(f"  {len(res['orphan_quotes'])} quotes had no matching instrument "
              f"metadata and were dropped")


def _cmd_status(args):
    c = coverage()
    print(f"contracts tracked : {c['contracts']}")
    print(f"quote rows        : {c['quotes']}")
    print(f"capture days      : {len(c['days'])}"
          + (f"  ({c['days'][0]} .. {c['days'][-1]})" if c['days'] else ""))
    for d in c["days"][-10:]:
        print(f"  {d}  {c['per_day'][d]} quotes")
    if not c["days"]:
        print("\nNothing captured yet. Run `plan`, capture, then `ingest`.")
        return
    asof = args.asof or datetime.utcnow().date().isoformat()
    due, risk = due_for_vrp(asof), at_risk(asof)
    print(f"\nexpired and measurable : {len(due)}")
    print(f"past the ~21d discovery window (held by UUID anyway): {len(risk)}")


def _cmd_due(args):
    asof = args.asof or datetime.utcnow().date().isoformat()
    rows = due_for_vrp(asof)
    if not rows:
        print("nothing expired yet")
        return
    print(f"{'expiration':<12}{'sym':>6}{'strike':>9}{'ty':>5}{'captures':>9}"
          f"  instrument_id")
    for e in rows:
        print(f"{e['exp']:<12}{str(e['symbol']):>6}{e['strike']:>9.0f}"
              f"{str(e['type'])[:1].upper():>5}{e['captures']:>9}  "
              f"{e['instrument_id']}")
    print("\nNext: get_option_historicals on these UUIDs for the realised path,")
    print("then market_data_options.vrp(iv_at_entry, realised_vol_over_life).")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--spot", type=float, required=True)
    p.add_argument("--strikes", type=int, default=4,
                   help="strikes each side of ATM")
    p.add_argument("--step", type=float, default=5.0)
    p.add_argument("--targets", type=int, nargs="+", default=[7, 30, 60])
    p.set_defaults(fn=_cmd_plan)

    p = sub.add_parser("ingest")
    p.add_argument("file")
    p.set_defaults(fn=_cmd_ingest)

    p = sub.add_parser("status")
    p.add_argument("--asof", default=None)
    p.set_defaults(fn=_cmd_status)

    p = sub.add_parser("due")
    p.add_argument("--asof", default=None)
    p.set_defaults(fn=_cmd_due)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
