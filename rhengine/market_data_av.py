"""
market_data_av.py — AlphaVantage earnings-surprise adapter (real SUE feed).

The PEAD spec (strategies.json) names its own upgrade path: "replace the
price-proxy event detector with a real earnings/SUE feed". This adapter is
that feed. Academic PEAD sorts on the earnings surprise itself, not on gap
size — the price-proxy EVENT leg stays in indicators.py untouched; the real
feed is applied as the external gating leg av_earnings_sue (strategies.json
external_legs), scored here by sue_gate().

  fetch_earnings()       — EARNINGS endpoint for one symbol → ascending
                           event list, disk-cached in av_cache/
  fetch_earnings_batch() — cache-first batch that spends at most
                           max_requests network calls per run
  sue_gate()             — pure external-leg scorer: a real report inside
                           the drift window with |surprise| >= threshold

ponytail: AlphaVantage free tier is 25 requests/DAY (not per minute). A
500-ticker watchlist takes ~3 weeks to cold-fill at that budget, so the
cache TTL is 7 days (earnings are quarterly; daily refresh is waste) and
the batch helper refreshes stalest-first within its budget. For a broad
universe either upgrade the key or keep this leg on a focused watchlist.
"""

import gzip
import json
import os
import time
from datetime import datetime, timezone

import requests

AV_BASE = "https://www.alphavantage.co/query"
_CACHE_DIR = "av_cache"
DAY_MS = 86_400_000


def _app_path(name):
    p = os.path.join(os.getcwd(), name)
    return p if os.path.exists(p) else os.path.join(os.path.dirname(__file__), name)


def _av_key():
    with open(_app_path("secrets.json")) as f:
        key = json.load(f).get("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("ALPHAVANTAGE_API_KEY missing from secrets.json")
    return key


def _flt(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_earnings(body):
    """Pure parser: EARNINGS payload → ascending events (fixture-tested).

    [{"time": ms of reportedDate, "fiscal_date": str, "eps_actual": float,
      "eps_estimate": float|None, "surprise_pct": float|None}]
    Rows without a reportedDate are dropped; missing estimates stay None
    (surprise can't be computed — sue_gate treats that as no event)."""
    events = []
    for q in body.get("quarterlyEarnings", []):
        rd = q.get("reportedDate")
        if not rd:
            continue
        try:
            d = datetime.fromisoformat(rd).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        events.append({
            "time": int(d.timestamp() * 1000),
            "fiscal_date": q.get("fiscalDateEnding"),
            "eps_actual": _flt(q.get("reportedEPS")),
            "eps_estimate": _flt(q.get("estimatedEPS")),
            "surprise_pct": _flt(q.get("surprisePercentage")),
        })
    events.sort(key=lambda e: e["time"])
    return events


def _cache_path(symbol):
    return os.path.join(_CACHE_DIR, symbol.upper() + ".json.gz")


def _cache_read(symbol):
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fetch_earnings(symbol, ttl_days=7, offline=False):
    """Earnings-surprise history for one symbol, cached for ttl_days (the
    25-req/day free tier makes daily refresh waste — reports are quarterly).
    offline=True replays any snapshot; failures fall back to stale."""
    blob = _cache_read(symbol)
    now = time.time()
    if blob and (offline or now - blob.get("fetched_ts", 0) < ttl_days * 86400):
        return blob["events"]
    try:
        r = requests.get(AV_BASE, params={"function": "EARNINGS",
                                          "symbol": symbol.upper(),
                                          "apikey": _av_key()}, timeout=20)
        r.raise_for_status()
        body = r.json()
        # AV signals budget exhaustion inside a 200 body, not via status code
        if "quarterlyEarnings" not in body:
            raise ConnectionError("AlphaVantage non-data response for {}: {}".format(
                symbol, str(body)[:120]))
        events = parse_earnings(body)
    except (requests.RequestException, ValueError, ConnectionError) as e:
        if blob:
            return blob["events"]
        raise ConnectionError("AlphaVantage fetch failed for {}: {}".format(symbol, e))
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_path(symbol)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"fetched_ts": now, "events": events}, f, separators=(",", ":"))
    os.replace(tmp, path)
    return events


def fetch_earnings_batch(symbols, max_requests=20, ttl_days=7, verbose=True):
    """Cache-first batch inside the daily budget: fresh caches are served
    free; at most max_requests symbols hit the network, stalest cache
    first (never-fetched counts as stalest). Returns {"events": {sym: [...]},
    "errors": {sym: str}} — same envelope as the bar batch functions."""
    def staleness(sym):
        blob = _cache_read(sym)
        return blob.get("fetched_ts", 0) if blob else 0

    events_map, errors = {}, {}
    spent = 0
    for sym in sorted(symbols, key=staleness):
        try:
            offline = spent >= max_requests
            blob = _cache_read(sym)
            fresh = blob and time.time() - blob.get("fetched_ts", 0) < ttl_days * 86400
            if not fresh and not offline:
                if spent:  # pace the free tier's per-minute limiter
                    time.sleep(13)
                spent += 1
            events_map[sym] = fetch_earnings(sym, ttl_days=ttl_days, offline=offline)
        except Exception as e:
            errors[sym] = "{}: {}".format(type(e).__name__, e)
    if verbose:
        print("  AV earnings: {} ok ({} network calls), {} failed".format(
            len(events_map), spent, len(errors)))
    return {"events": events_map, "errors": errors}


# ---------------------------------------------------------- external leg ----

def sue_gate(events, now_ms, drift_min=1, drift_max=10, min_surprise_pct=5.0):
    """av_earnings_sue external leg (spec: strategies.json external_legs).
    Pure function, evaluated OUTSIDE score_ticker — the PEAD price-proxy
    EVENT leg is untouched; this gate confirms the proxy event with a REAL
    report: one inside the drift window with |surprise| >= threshold.

    Returns {"pass", "surprise_pct", "days_since", "direction"} where
    direction is bull for a positive surprise, bear for a miss."""
    best = None
    for e in reversed(events or []):
        days = (now_ms - e["time"]) / DAY_MS
        if days < drift_min:
            continue
        if days > drift_max:
            break
        if e["surprise_pct"] is None:
            continue
        best = (e, days)
        break
    if not best or abs(best[0]["surprise_pct"]) < min_surprise_pct:
        return {"pass": False, "surprise_pct": None, "days_since": None,
                "direction": None}
    e, days = best
    return {"pass": True, "surprise_pct": e["surprise_pct"],
            "days_since": round(days, 2),
            "direction": "bull" if e["surprise_pct"] > 0 else "bear"}


if __name__ == "__main__":
    # Smoke test — run: py -m rhengine.market_data_av   (costs 1 of 25 daily calls)
    events = fetch_earnings("AAPL")
    print("AAPL: {} quarterly reports cached; latest: {}".format(
        len(events), events[-1] if events else None))
    print("sue_gate now:", sue_gate(events, int(time.time() * 1000)))
