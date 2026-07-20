"""
market_data_polymarket.py — Polymarket data adapter.

Same layering as market_data.py / market_data_kalshi.py: fetch + cache venue
data, emit bars in the engine's shape. indicators.py never imports this file.

Split mirrors Polymarket's own API split (docs.polymarket.com):

  gamma_markets() / gamma_events() — Gamma API (gamma-api.polymarket.com),
      PUBLIC. Market/event discovery: questions, slugs, outcome prices,
      volume/liquidity, clobTokenIds.
  data_positions() / data_trades() / data_holders() — Data API
      (data-api.polymarket.com), PUBLIC. Positions, trades, holders — the
      crowd/whale-flow surface.
  fetch_prob_bars() — CLOB prices-history (clob.polymarket.com), PUBLIC read.
      Probability time series for one outcome token → daily bars, disk-cached
      in polymarket_cache/ for offline backtests.
  clob_orderbook() — CLOB book, PUBLIC read. Execution-time depth/slippage
      check ONLY — books are point-in-time and unreplayable, so nothing in
      the signal path may depend on one.
  clob_client() / perps_client() — GATED stubs. Order placement needs L2
      wallet-derived auth; perps is referral-code early access. Both raise
      with instructions instead of half-working.

CLOB prices are probabilities in [0, 1].

ponytail: prices-history serves resolved/closed markets only at >=12h
granularity, so a backtest across dead markets is daily-fidelity at best —
fine for the daily engine, but don't expect hourly research on old markets.

ponytail: prob bars carry volume=0.0 (the endpoint returns trade prices
only). Volume legs (VOL) are meaningless on Polymarket bars — cut them via
legMask, or gate with the external legs which don't use volume.
"""

import gzip
import json
import os
import time
from datetime import datetime, timezone

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

DAY_MS = 86_400_000
_CACHE_DIR = "polymarket_cache"


def _get(url, params=None, timeout=20, _retries=1):
    last_err = None
    for attempt in range(_retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
        except requests.HTTPError as e:
            raise ConnectionError("Polymarket GET {} -> HTTP {}".format(
                url, r.status_code)) from e
    raise ConnectionError("Polymarket GET {} failed: {}".format(url, last_err))


def _json_list(v):
    """Gamma stringifies list fields (outcomePrices, clobTokenIds) as JSON
    text; older payloads used comma-separated strings. Normalize to a list."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    s = str(v).strip()
    try:
        out = json.loads(s)
        return out if isinstance(out, list) else [out]
    except ValueError:
        return [p.strip() for p in s.split(",") if p.strip()]


# ------------------------------------------------------------- Gamma API ----

def parse_gamma_market(m):
    """Pure parser: one Gamma market dict → engine-friendly dict (fixture-
    tested). Keeps ids as strings, converts prices/volume to floats."""
    prices = [float(p) for p in _json_list(m.get("outcomePrices"))]
    return {
        "id": str(m.get("id", "")),
        "question": m.get("question"),
        "slug": m.get("slug"),
        "outcome_prices": prices,
        "implied_prob": prices[0] if prices else None,  # YES side
        "volume": float(m.get("volumeNum") or m.get("volume") or 0),
        "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
        "clob_token_ids": [str(t) for t in _json_list(m.get("clobTokenIds"))],
        "end_date": m.get("endDate"),
        "active": bool(m.get("active", False)),
        "closed": bool(m.get("closed", False)),
    }


def gamma_markets(slug=None, tag_id=None, closed=False, limit=100, offset=0,
                  order="volumeNum", ascending=False):
    """Market discovery via the Gamma API. PUBLIC — signal-safe.

    slug may be a string or list of slugs (exact match); otherwise returns
    markets sorted by volume. Returns parsed dicts (parse_gamma_market)."""
    params = {"limit": limit, "offset": offset,
              "order": order, "ascending": str(ascending).lower()}
    if closed is not None:  # None = don't filter (Gamma 422s on closed=none)
        params["closed"] = str(closed).lower()
    if slug:
        params["slug"] = slug if isinstance(slug, (list, tuple)) else [slug]
    if tag_id is not None:
        params["tag_id"] = tag_id
    body = _get(GAMMA_BASE + "/markets", params=params)
    return [parse_gamma_market(m) for m in (body if isinstance(body, list) else [])]


def gamma_events(slug=None, limit=50, closed=False):
    """Event discovery (an event groups related markets). PUBLIC. Raw dicts."""
    params = {"limit": limit, "closed": str(closed).lower()}
    if slug:
        params["slug"] = slug if isinstance(slug, (list, tuple)) else [slug]
    body = _get(GAMMA_BASE + "/events", params=params)
    return body if isinstance(body, list) else []


# -------------------------------------------------------------- Data API ----

def data_positions(user):
    """Open positions for a wallet address. PUBLIC — whale-tracking surface."""
    return _get(DATA_BASE + "/positions", params={"user": user})


def data_trades(market=None, user=None, limit=100):
    """Recent trades, filterable by market conditionId or wallet. PUBLIC."""
    params = {"limit": limit}
    if market:
        params["market"] = market
    if user:
        params["user"] = user
    return _get(DATA_BASE + "/trades", params=params)


def data_holders(market, limit=20):
    """Top holders of a market's outcome tokens. PUBLIC — concentration is a
    crowding signal (a book held by two wallets reprices violently)."""
    return _get(DATA_BASE + "/holders", params={"market": market, "limit": limit})


# ------------------------------------------------------- probability bars ----

def parse_prices_history(body, interval="1d"):
    """Pure parser: prices-history payload {"history":[{"t": sec, "p": prob}]}
    → engine bars, bucketed per UTC day (fixture-tested).

    OHLC is derived from the points inside each bucket (open=first, close=
    last, high/low=extremes). volume is 0.0 — see module ponytail."""
    points = body.get("history", []) if isinstance(body, dict) else body
    buckets = {}
    for pt in points:
        t, p = pt.get("t"), pt.get("p")
        if t is None or p is None:
            continue
        day = (int(t) * 1000) // DAY_MS * DAY_MS
        buckets.setdefault(day, []).append((int(t), float(p)))
    bars = []
    for day in sorted(buckets):
        pts = sorted(buckets[day])
        ps = [p for _, p in pts]
        bars.append({
            "time": day,
            "open": ps[0], "high": max(ps), "low": min(ps), "close": ps[-1],
            "volume": 0.0,
        })
    return bars


def _cache_path(token_id):
    return os.path.join(_CACHE_DIR, str(token_id)[:64] + ".json.gz")


def fetch_prob_bars(clob_token_id, lookback_days=90, fidelity=720, offline=False):
    """Daily probability bars for one outcome token via CLOB prices-history.
    PUBLIC. Disk-cached in polymarket_cache/ (fetched-today stamp); offline=
    True replays any cached snapshot with no network, and a network failure
    falls back to a stale snapshot (data_local pattern).

    fidelity is in minutes (720 = 2 points/day — enough for daily bars
    while staying inside the resolved-market granularity floor).

    ponytail: the live endpoint (verified 2026-07-20) rejects bare
    startTs/endTs with HTTP 400 despite docs suggesting otherwise; it wants
    a relative interval ("1h"/"6h"/"1d"/"1w"/"1m"/"max"), so lookback_days
    is mapped to the smallest interval that covers it."""
    path = _cache_path(clob_token_id)
    blob = None
    if os.path.exists(path):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                blob = json.load(f)
        except Exception:
            blob = None
    today = datetime.now(timezone.utc).date().isoformat()
    # Coverage rule: a cached shorter window never silently serves a longer
    # request (data_local period1 rule).
    covers = blob and blob.get("lookback_days", 0) >= lookback_days
    if blob and (offline or (covers and blob.get("fetched") == today)):
        return blob["bars"]

    interval = ("1d" if lookback_days <= 1 else
                "1w" if lookback_days <= 7 else
                "1m" if lookback_days <= 31 else "max")
    try:
        body = _get(CLOB_BASE + "/prices-history",
                    params={"market": clob_token_id,
                            "interval": interval, "fidelity": fidelity})
    except ConnectionError:
        if blob:
            return blob["bars"]
        raise
    bars = parse_prices_history(body)
    os.makedirs(_CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"fetched": today, "lookback_days": lookback_days,
                   "bars": bars}, f, separators=(",", ":"))
    os.replace(tmp, path)
    return bars


def resolve_slug_to_token(slug):
    """Market slug → (YES clob_token_id, parsed market). The YES token is
    clob_token_ids[0] by Polymarket convention."""
    mkts = gamma_markets(slug=slug, closed=None if slug else False, limit=1)
    if not mkts or not mkts[0]["clob_token_ids"]:
        raise ValueError("Polymarket: no market/token for slug {!r}".format(slug))
    return mkts[0]["clob_token_ids"][0], mkts[0]


# ---------------------------------------------------------- external leg ----

def score_implied_prob(prob_bars, min_prob=0.10, max_prob=0.90,
                       window=5, min_shift=0.0):
    """polymarket_implied_prob external leg (spec: strategies.json
    external_legs). Pure function, evaluated OUTSIDE score_ticker.

    Passes when the market's implied probability sits in the tradable band
    (min_prob..max_prob — near 0/1 there is no repricing left to harvest)
    and, if min_shift > 0, has moved at least that much over `window` bars.
    Returns {"pass", "prob", "shift", "direction"}."""
    if not prob_bars:
        return {"pass": False, "prob": None, "shift": 0.0, "direction": None}
    prob = prob_bars[-1]["close"]
    shift = (prob - prob_bars[-1 - window]["close"]
             if len(prob_bars) > window else 0.0)
    direction = "bull" if shift > 0 else ("bear" if shift < 0 else None)
    ok = (min_prob <= prob <= max_prob) and abs(shift) >= min_shift
    return {"pass": ok, "prob": round(prob, 6), "shift": round(shift, 6),
            "direction": direction}


# ----------------------------------------------------- sim-loader plumbing ----

def load_bars_polymarket(cfg, lookback_days=400, verbose=True):
    """(bars_map, spy_bars) for data_local.load_bars_any dispatch.

    Watchlist entries are market slugs (resolved to YES tokens via Gamma).
    SPY bars still come from Yahoo — regime stays equity-anchored."""
    from .data_local import fetch_yahoo
    entries = cfg.get("watchlist", [])
    if entries == "all":
        raise ValueError('watchlist "all" is a Massive-cache feature; '
                         'polymarket needs explicit market slugs')
    bars_map, errors = {}, {}
    for slug in entries:
        try:
            token, _ = resolve_slug_to_token(slug)
            bars = fetch_prob_bars(token, lookback_days=lookback_days)
            if bars:
                bars_map[slug] = bars
            else:
                errors[slug] = "no price history returned"
        except Exception as e:
            errors[slug] = "{}: {}".format(type(e).__name__, e)
    if verbose:
        print("  Polymarket: {} ok, {} failed{}".format(
            len(bars_map), len(errors),
            " ({})".format(", ".join(errors)) if errors else ""))
    spy_bars = fetch_yahoo(cfg.get("spy_ticker", "SPY"), lookback_days=lookback_days)
    return bars_map, spy_bars


# ---------------------------------------------------------- gated surfaces ----

def clob_orderbook(token_id):
    """Order book snapshot for one token. PUBLIC read, but execution-time
    only: use it to size entries against real depth, never for signals —
    a book can't be replayed, so any signal built on it can't be backtested."""
    return _get(CLOB_BASE + "/book", params={"token_id": token_id})


def clob_client():
    """GATED: authenticated CLOB trading client (order placement/cancel).

    Requires L2 auth — an API key/secret/passphrase derived from a wallet
    signature (Polymarket's py-clob-client handles the derivation). Keys
    would live in secrets.json as POLYMARKET_API_KEY / _SECRET /
    _PASSPHRASE + POLYMARKET_PROXY_ADDRESS. Execution is app-side
    (RobinhoodAgent), not engine-side — same boundary as Alpaca orders."""
    raise NotImplementedError(
        "Polymarket CLOB trading is app-side. Install py-clob-client and "
        "build the client in the consuming app; this engine only reads "
        "public data. See docs.polymarket.com CLOB authentication.")


def perps_client():
    """GATED / EARLY ACCESS: Polymarket perps stub.

    Perps currently requires a referral link/code before the API will
    authenticate at all, and carries funding payments + mark-price
    liquidation — mechanics the engine's exit model does not represent
    (same reasoning as KalshiPerpsClient). Revisit once access is granted
    AND funding/liquidation modeling exists."""
    raise NotImplementedError(
        "Polymarket perps is referral-gated early access and the engine "
        "has no funding/liquidation model. See docs.polymarket.com/perps/"
        "overview.")


if __name__ == "__main__":
    # Smoke test — run: py -m rhengine.market_data_polymarket
    mkts = gamma_markets(limit=3)
    for m in mkts:
        print("{}  prob={}  vol={:,.0f}".format(
            (m["question"] or m["slug"] or "")[:60], m["implied_prob"], m["volume"]))
    if mkts and mkts[0]["clob_token_ids"]:
        bars = fetch_prob_bars(mkts[0]["clob_token_ids"][0], lookback_days=30)
        print("{} daily prob bars; leg: {}".format(
            len(bars), score_implied_prob(bars)))
