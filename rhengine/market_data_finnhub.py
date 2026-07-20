"""
market_data_finnhub.py — Finnhub earnings-surprise adapter (live + forward store).

Complements market_data_av.py rather than replacing it. Verified against the
Finnhub free tier (2026-07-20):

  * /calendar/earnings (BULK, no symbol filter) serves the whole market's
    report-dated surprises for a RECENT window (~last 30-45 days + forward)
    in a single call — and the per-symbol filter is broken on free tier, so
    bulk is the only reliable path. Historical windows (>~60d back) return
    empty: free tier is a near-real-time feed, NOT retroactive history.
  * /stock/earnings is capped at the last 4 quarters and gives the fiscal
    PERIOD end, not the report date — unusable for lookahead-free backtests.

So the free tier's real value is:
  1. LIVE gate — 60 req/min refreshes the whole watchlist's recent earnings
     in seconds (vs AlphaVantage's 25/DAY wall). Use for the production path.
  2. FORWARD-ACCUMULATING store — one bulk call/day appends the market's
     newly-reported surprises to finnhub_cache/earnings_store.json.gz, keyed
     by report date. With zero per-ticker budget this grows into a correct,
     report-dated, full-market SUE history that IS backtestable over the
     accumulated window. AV remains the source for retroactive deep history.

Event shape matches market_data_av (time = report-date ms, surprise_pct,
eps_actual, eps_estimate, fiscal_date) so market_data_av.sue_gate and
gates.GateBook consume Finnhub events with no changes. _cache_read(symbol)
mirrors the AV interface (reads the shared store) so the gate source is a
config switch.
"""

import gzip
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

FINNHUB_BASE = "https://finnhub.io/api/v1"
_CACHE_DIR = "finnhub_cache"
_STORE = os.path.join(_CACHE_DIR, "earnings_store.json.gz")
DAY_MS = 86_400_000


def _app_path(name):
    p = os.path.join(os.getcwd(), name)
    return p if os.path.exists(p) else os.path.join(os.path.dirname(__file__), name)


def _finnhub_key():
    with open(_app_path("secrets.json")) as f:
        key = json.load(f).get("FINNHUB_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("FINNHUB_API_KEY missing from secrets.json")
    return key


class RateLimited(ConnectionError):
    """Finnhub returned HTTP 429 (60 req/min free tier). Transient."""


def _surprise_pct(actual, estimate):
    """Percent surprise, guarding a zero/None estimate (undefined → None)."""
    if actual is None or estimate is None or estimate == 0:
        return None
    return round(100.0 * (actual - estimate) / abs(estimate), 4)


# ------------------------------------------------------------ bulk calendar ----

def fetch_calendar_window(from_date, to_date, key=None):
    """Bulk earnings calendar for [from_date, to_date] (ISO strings). PUBLIC
    to the whole market — no symbol filter. Returns the raw row list."""
    key = key or _finnhub_key()
    r = requests.get(FINNHUB_BASE + "/calendar/earnings",
                     params={"from": from_date, "to": to_date, "token": key},
                     timeout=25)
    if r.status_code == 429:
        raise RateLimited("Finnhub 429 (60/min)")
    r.raise_for_status()
    body = r.json()
    return body.get("earningsCalendar", []) if isinstance(body, dict) else []


def parse_calendar_rows(rows):
    """Pure parser: bulk-calendar rows → {symbol: [event, ...]} (fixture-
    tested). Only rows with a report date AND a computable surprise are kept
    (a scheduled/estimate-only future row has epsActual=None → dropped)."""
    out = {}
    for row in rows:
        sym = (row.get("symbol") or "").strip().upper()
        rd = row.get("date")
        actual, est = row.get("epsActual"), row.get("epsEstimate")
        sp = _surprise_pct(actual, est)
        if not sym or not rd or sp is None:
            continue
        try:
            d = datetime.fromisoformat(rd).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.setdefault(sym, []).append({
            "time": int(d.timestamp() * 1000),
            "fiscal_date": rd,
            "eps_actual": float(actual),
            "eps_estimate": float(est),
            "surprise_pct": sp,
        })
    return out


# --------------------------------------------------------------- store ----

def _load_store():
    if not os.path.exists(_STORE):
        return {"updated": None, "symbols": {}}
    try:
        with gzip.open(_STORE, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated": None, "symbols": {}}


def _save_store(store):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    tmp = _STORE + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(store, f, separators=(",", ":"))
    os.replace(tmp, _STORE)


def merge_events(store, parsed):
    """Merge {symbol: [events]} into the store, dedup by (symbol, report
    date). Pure on the passed dict (mutates + returns it) — fixture-tested."""
    symbols = store.setdefault("symbols", {})
    added = 0
    for sym, events in parsed.items():
        existing = symbols.setdefault(sym, [])
        seen = {e["fiscal_date"] for e in existing}
        for e in events:
            if e["fiscal_date"] not in seen:
                existing.append(e)
                seen.add(e["fiscal_date"])
                added += 1
        existing.sort(key=lambda e: e["time"])
    return added


def snapshot_recent(days_back=45, key=None, verbose=True):
    """Fetch the recent bulk calendar and append new surprises to the store.
    Budget-free (one call covers the whole market). Run daily to accumulate a
    growing, report-dated, full-market SUE history. Returns (added, total)."""
    key = key or _finnhub_key()
    today = datetime.now(timezone.utc).date()
    rows = fetch_calendar_window((today - timedelta(days=days_back)).isoformat(),
                                 today.isoformat(), key=key)
    parsed = parse_calendar_rows(rows)
    store = _load_store()
    added = merge_events(store, parsed)
    store["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_store(store)
    total = sum(len(v) for v in store["symbols"].values())
    if verbose:
        print("  Finnhub snapshot: {} rows, {} symbols with surprises; "
              "+{} new events (store: {} symbols, {} events total)".format(
                  len(rows), len(parsed), added,
                  len(store["symbols"]), total))
    return added, total


# ---------------------------------------------- AV-compatible read surface ----

def events_for(symbol):
    """Report-dated surprise events for a symbol from the accumulated store,
    ascending. Same shape as market_data_av.parse_earnings output."""
    return _load_store()["symbols"].get(symbol.upper(), [])


def _cache_read(symbol):
    """AV-interface shim so gates.GateBook can read Finnhub events via the
    same call. Returns {"events": [...], "fetched_ts": ...} or None."""
    events = events_for(symbol)
    if not events:
        return None
    ts = os.path.getmtime(_STORE) if os.path.exists(_STORE) else 0
    return {"events": events, "fetched_ts": ts}


def fetch_earnings(symbol, ttl_days=7, offline=False):
    """AV-compatible entrypoint. Finnhub earnings come from the shared store
    (populated by snapshot_recent), so this just reads it — the store, not a
    per-symbol call, is how Finnhub data lands. Signature matches
    market_data_av.fetch_earnings so callers are source-agnostic."""
    return events_for(symbol)


# ---------------------------------------------------------- live refresh ----

def live_surprise(symbol, key=None):
    """Most recent reported surprise for one symbol via /stock/earnings
    (last 4 quarters, 60/min). PERIOD-dated (no report date) — for a live
    'did this name just beat/miss?' read, not for backtests. Returns the
    latest event dict or None."""
    key = key or _finnhub_key()
    r = requests.get(FINNHUB_BASE + "/stock/earnings",
                     params={"symbol": symbol.upper(), "token": key}, timeout=20)
    if r.status_code == 429:
        raise RateLimited("Finnhub 429 (60/min)")
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return None
    rows.sort(key=lambda x: x.get("period", ""))
    latest = rows[-1]
    return {
        "fiscal_date": latest.get("period"),
        "eps_actual": latest.get("actual"),
        "eps_estimate": latest.get("estimate"),
        "surprise_pct": latest.get("surprisePercent"),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "snapshot":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 45
        snapshot_recent(days_back=days)
    else:
        # Smoke test — run: py -m rhengine.market_data_finnhub
        added, total = snapshot_recent(days_back=45)
        print("live AAPL:", live_surprise("AAPL"))
        from .market_data_av import sue_gate
        ev = events_for("AAPL")
        print("AAPL store events:", len(ev),
              "| gate:", sue_gate(ev, int(time.time() * 1000)) if ev else "none yet")
