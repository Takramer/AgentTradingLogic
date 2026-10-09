"""
market_data_kalshi.py — Kalshi event-contract data adapter.

Same layering as market_data.py (Alpaca/Massive) and data_local.py (Yahoo):
this module fetches and caches venue data and emits bars in the exact shape
the pure scorer expects ({"time": ms, "open", "high", "low", "close",
"volume"}, ascending). indicators.py never imports this file.

Everything the signal path needs here is PUBLIC — no keys required:

  kalshi_is_tradable()      — GET /exchange/status gate for live/backtest loops
  fetch_markets()           — GET /markets discovery (open markets, by series)
  fetch_prob_bars()         — GET /series/{s}/markets/{t}/candlesticks →
                              daily probability OHLCV bars, disk-cached in
                              kalshi_cache/ so backtests replay offline
  score_prob_shift()        — pure external-leg scorer (spec: external_legs.
                              kalshi_prob_shift in strategies.json)

KalshiPerpsClient wraps the separate /margin/* (perps) namespace. Kalshi
documents perps as a distinct API surface rolled out member-by-member, and
warns API keys must NOT be shared between the event-contract and margin FIX
gateways — so it is a separate class with its own credential slots, never
reusing an event-contract client. Order routing is deliberately stubbed:
the engine has no funding/liquidation model yet (see class docstring).

Prices are probabilities in [0, 1] (Kalshi fixed-point dollar strings; the
legacy API used integer cents — both are normalized here).
"""

import gzip
import json
import os
import time
from datetime import datetime, timezone

import requests

# Primary host per docs.kalshi.com (2026-07); legacy host kept as fallback
# because Kalshi has migrated hosts before (trading-api → elections → external).
KALSHI_HOSTS = [
    "https://external-api.kalshi.com/trade-api/v2",
    "https://api.elections.kalshi.com/trade-api/v2",
]

DAY_MS = 86_400_000


from ._paths import app_path as _app_path  # robust resolver (walks up to repo root)


_CACHE_DIR = "kalshi_cache"


def _kalshi_get(path, params=None, timeout=15, _retries=1):
    """GET against the first Kalshi host that answers. Public endpoints only."""
    last_err = None
    for base in KALSHI_HOSTS:
        for attempt in range(_retries + 1):
            try:
                r = requests.get(base + path, params=params, timeout=timeout)
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code == 404 and base != KALSHI_HOSTS[-1]:
                    break  # wrong host — try the next one
                r.raise_for_status()
                return r.json()
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                break  # host unreachable — try the next one
            except requests.HTTPError as e:
                last_err = e
                break
    raise ConnectionError("Kalshi GET {} failed: {}".format(path, last_err))


# ------------------------------------------------------------ status gate ----

def kalshi_is_tradable():
    """Exchange-status gate for live/backtest loops. Public, no auth.

    Returns:
        {
          "tradable": bool,              # exchange_active AND trading_active
          "exchange_active": bool,
          "trading_active": bool,
          "estimated_resume_time": str|None,  # ISO ts during maintenance
          "checked_at": int,             # Unix ms
        }

    A loop should skip the cycle (not crash) when tradable is False; if
    estimated_resume_time is set, sleeping until then beats polling.
    On network failure this RAISES — callers decide whether no-answer
    means skip-cycle (live) or proceed-from-cache (backtest).
    """
    body = _kalshi_get("/exchange/status")
    return parse_exchange_status(body)


def parse_exchange_status(body):
    """Pure parser for the /exchange/status payload (fixture-tested)."""
    exchange_active = bool(body.get("exchange_active", False))
    trading_active = bool(body.get("trading_active", False))
    return {
        "tradable": exchange_active and trading_active,
        "exchange_active": exchange_active,
        "trading_active": trading_active,
        "estimated_resume_time": body.get("exchange_estimated_resume_time"),
        "checked_at": int(time.time() * 1000),
    }


# -------------------------------------------------------------- discovery ----

def fetch_markets(series_ticker=None, status="open", limit=200):
    """List markets (public). Filter by series_ticker to find the actively
    traded strikes of one series. Returns the raw market dicts (ticker,
    series_ticker, yes_bid/yes_ask, volume, open_interest, close_time...)."""
    params = {"limit": limit, "status": status}
    if series_ticker:
        params["series_ticker"] = series_ticker
    body = _kalshi_get("/markets", params=params)
    return body.get("markets", [])


# ------------------------------------------------------- probability bars ----

def _px(v):
    """Normalize a Kalshi price to a [0,1] probability float.
    Fixed-point dollar strings ("0.635000") pass through; legacy integer
    cents (63) are divided by 100. None stays None."""
    if v is None:
        return None
    f = float(v)
    return f / 100.0 if f > 1.5 else f


def _fld(d, name):
    """Live payloads suffix OHLC keys with _dollars (close_dollars); the docs
    and legacy payloads use bare keys (close). Accept both."""
    v = d.get(name + "_dollars")
    return d.get(name) if v is None else v


def _mid(bid, ask, name):
    a, b = _px(_fld(bid, name)), _px(_fld(ask, name))
    if a is None or b is None:
        return None
    return round((a + b) / 2.0, 6)


def parse_candlesticks(body):
    """Pure parser: candlesticks payload → engine bars (fixture-tested).

    Priority per period: traded price OHLC; else the yes_bid/yes_ask OHLC
    midpoints (quote-only days still get a real range); else the venue's
    carried-forward previous trade price (flat bar). Periods with no price
    information at all are dropped."""
    bars = []
    for c in body.get("candlesticks", []):
        ts = c.get("end_period_ts")
        if ts is None:
            continue
        price = c.get("price") or {}
        bid, ask = c.get("yes_bid") or {}, c.get("yes_ask") or {}
        o, h = _px(_fld(price, "open")), _px(_fld(price, "high"))
        lo, cl = _px(_fld(price, "low")), _px(_fld(price, "close"))
        if cl is None:
            o, cl = _mid(bid, ask, "open"), _mid(bid, ask, "close")
            h, lo = _mid(bid, ask, "high"), _mid(bid, ask, "low")
            if cl is None:
                prev = _px(_fld(price, "previous"))
                if prev is None:
                    continue
                o = h = lo = cl = prev
        o = cl if o is None else o
        h = max(v for v in (o, h, cl) if v is not None)
        lo = min(v for v in (o, lo, cl) if v is not None)
        vol = c.get("volume_fp", c.get("volume", 0))
        bars.append({
            "time": int(ts) * 1000,
            "open": o, "high": h, "low": lo, "close": cl,
            "volume": float(vol or 0),
        })
    bars.sort(key=lambda b: b["time"])
    return bars


def _cache_path(series_ticker, market_ticker):
    safe = "{}__{}".format(series_ticker, market_ticker).replace("/", "_")
    return os.path.join(_CACHE_DIR, safe + ".json.gz")


def fetch_prob_bars(series_ticker, market_ticker, lookback_days=90,
                    period_interval=1440, offline=False):
    """Daily (or hourly: period_interval=60) probability bars for one market.

    Disk-cached in kalshi_cache/ with a fetched-today stamp (data_local
    pattern). offline=True serves ANY cached snapshot without touching the
    network — the path backtests use; a network failure also falls back to
    a stale snapshot rather than dying mid-backtest."""
    path = _cache_path(series_ticker, market_ticker)
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

    now = int(time.time())
    try:
        body = _kalshi_get(
            "/series/{}/markets/{}/candlesticks".format(series_ticker, market_ticker),
            params={"start_ts": now - lookback_days * 86400, "end_ts": now,
                    "period_interval": period_interval})
    except ConnectionError:
        if blob:  # stale snapshot beats no data for research use
            return blob["bars"]
        raise
    bars = parse_candlesticks(body)
    os.makedirs(_CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"fetched": today, "lookback_days": lookback_days,
                   "bars": bars}, f, separators=(",", ":"))
    os.replace(tmp, path)
    return bars


# ---------------------------------------------------------- external leg ----

def score_prob_shift(prob_bars, window=5, min_shift=0.05):
    """kalshi_prob_shift external leg (spec: strategies.json external_legs).

    Pure function — evaluated OUTSIDE score_ticker so indicators.py stays an
    OHLCV-only scorer and the golden vectors never move. Apps apply it as a
    scan-level gate alongside a strategy's own legs.

    Measures the change in implied probability over the last `window` bars.
    Returns {"pass": bool, "shift": float, "direction": "bull"|"bear"|None}.
    A market repricing fast (|shift| >= min_shift) is information flow the
    equity tape may not have digested yet."""
    if not prob_bars or len(prob_bars) <= window:
        return {"pass": False, "shift": 0.0, "direction": None}
    shift = prob_bars[-1]["close"] - prob_bars[-1 - window]["close"]
    direction = "bull" if shift > 0 else ("bear" if shift < 0 else None)
    return {"pass": abs(shift) >= min_shift, "shift": round(shift, 6),
            "direction": direction}


# ----------------------------------------------------- sim-loader plumbing ----

def load_bars_kalshi(cfg, lookback_days=400, verbose=True):
    """(bars_map, spy_bars) for data_local.load_bars_any dispatch.

    Watchlist entries are "SERIES_TICKER/MARKET_TICKER" strings. SPY bars
    still come from Yahoo — regime classification stays anchored to the
    equity tape even when the scored universe is event contracts."""
    from .data_local import fetch_yahoo
    entries = cfg.get("watchlist", [])
    if entries == "all":
        raise ValueError('watchlist "all" is a Massive-cache feature; kalshi '
                         'needs explicit "SERIES/MARKET" entries')
    bars_map, errors = {}, {}
    for entry in entries:
        try:
            series, market = entry.split("/", 1)
            bars = fetch_prob_bars(series, market, lookback_days=lookback_days)
            if bars:
                bars_map[entry] = bars
            else:
                errors[entry] = "no candlesticks returned"
        except Exception as e:
            errors[entry] = "{}: {}".format(type(e).__name__, e)
    if verbose:
        print("  Kalshi: {} ok, {} failed{}".format(
            len(bars_map), len(errors),
            " ({})".format(", ".join(errors)) if errors else ""))
    spy_bars = fetch_yahoo(cfg.get("spy_ticker", "SPY"), lookback_days=lookback_days)
    return bars_map, spy_bars


# ------------------------------------------------------------ perps client ----

class KalshiPerpsClient:
    """Kalshi margin (perps) API — SEPARATE surface from event contracts.

    Kalshi documents /margin/* as its own namespace with member-by-member
    rollout and explicitly warns keys must not be shared between the
    event-contract and margin FIX gateways. So: own class, own credential
    slots (secrets.json KALSHI_MARGIN_KEY_ID / KALSHI_MARGIN_PRIVATE_KEY),
    never constructed from an event-contract client.

    Only the unauthenticated enablement probe is implemented. Order routing
    stays stubbed until the engine grows a funding/liquidation model —
    perps carry funding payments and a liquidation_mark_price, and neither
    exists in the exit engine (stop/target/chandelier assume the position
    can't be force-closed by the venue). Trading perps through the current
    exit model would understate risk. See docs.kalshi.com/margin.
    """

    def __init__(self, key_id=None, private_key_pem=None, demo=False):
        base = ("https://external-api.demo.kalshi.co/trade-api/v2" if demo
                else KALSHI_HOSTS[0])
        self.base = base + "/margin"
        self.key_id = key_id
        self.private_key_pem = private_key_pem

    def enabled(self):
        """True if margin trading is switched on for this environment/account.
        Endpoint requires no state change; safe to probe."""
        try:
            r = requests.get(self.base + "/enabled", timeout=10)
            if r.ok:
                return bool(r.json().get("enabled", False))
        except requests.RequestException:
            pass
        return False

    def _not_implemented(self, what):
        raise NotImplementedError(
            "KalshiPerpsClient.{}: perps order routing is intentionally "
            "stubbed — the engine has no funding/liquidation model. Model "
            "funding drag and liquidation_mark_price stops first.".format(what))

    def create_order(self, *a, **k):
        self._not_implemented("create_order")

    def cancel_order(self, *a, **k):
        self._not_implemented("cancel_order")

    def get_funding_rate(self, *a, **k):
        self._not_implemented("get_funding_rate")



# ------------------------------------------------------------ authenticated ----
#
# Kalshi issues no bearer token. Every authenticated request carries an
# RSA-PSS-SHA256 signature over `timestamp + METHOD + path`, signed with the
# private half of a key pair whose public half Kalshi holds. The private .pem
# downloads once at key creation and cannot be re-fetched.
#
# Credentials come from secrets.json (gitignored): KALSHI_API_KEY_ID and
# KALSHI_PRIVATE_KEY_PATH. The .pem itself lives OUTSIDE the repo.
#
# Read-only by design. This module signs GETs. Order placement is app-side and
# stays that way -- see the four executor obligations in docs/GOLIVE-RUNBOOK.md.

def _kalshi_creds():
    """(key_id, private_key) or (None, None) when not configured."""
    import json as _json
    path = _app_path("secrets.json")
    if not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        cfg = _json.load(f)
    key_id = (cfg.get("KALSHI_API_KEY_ID") or "").strip()
    pem_path = os.path.expanduser((cfg.get("KALSHI_PRIVATE_KEY_PATH") or "").strip())
    if not key_id or not pem_path or not os.path.exists(pem_path):
        return None, None
    from cryptography.hazmat.primitives import serialization
    with open(pem_path, "rb") as f:
        return key_id, serialization.load_pem_private_key(f.read(), password=None)


def kalshi_signed_headers(method, path, key_id=None, private_key=None):
    """Headers for one authenticated Kalshi request.

    `path` is the request path WITHOUT the query string -- signing the query
    too produces a valid-looking signature that Kalshi rejects.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    if private_key is None:
        key_id, private_key = _kalshi_creds()
    if not private_key:
        raise EnvironmentError(
            "Kalshi API not configured: set KALSHI_API_KEY_ID and "
            "KALSHI_PRIVATE_KEY_PATH in secrets.json")
    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode("utf-8")
    sig = private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size),
        hashes.SHA256())
    import base64
    return {"KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("ascii"),
            "Content-Type": "application/json"}


def kalshi_auth_get(path, params=None, timeout=20):
    """Authenticated GET. Read-only: this helper does not do POST or DELETE."""
    base = KALSHI_HOSTS[0]
    # The signed path is everything after the host, including /trade-api/v2,
    # and excludes the query string.
    from urllib.parse import urlparse
    signed_path = urlparse(base + path).path
    headers = kalshi_signed_headers("GET", signed_path)
    r = requests.get(base + path, params=params, headers=headers, timeout=timeout)
    if r.status_code == 401:
        raise PermissionError("Kalshi auth rejected: {}".format(r.text[:200]))
    r.raise_for_status()
    return r.json()


def kalshi_is_authenticated():
    """True if the configured key can read the account. Read-only probe."""
    try:
        kalshi_auth_get("/portfolio/balance")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # Smoke test — run: py -m rhengine.market_data_kalshi
    st = kalshi_is_tradable()
    print("exchange status:", st)
    mkts = fetch_markets(limit=5)
    print("sample open markets:", [m.get("ticker") for m in mkts])
    if mkts:
        m = mkts[0]
        series = m.get("series_ticker") or m.get("event_ticker", "").split("-")[0]
        bars = fetch_prob_bars(series, m["ticker"], lookback_days=30)
        print("{}: {} daily prob bars, last close {}".format(
            m["ticker"], len(bars), bars[-1]["close"] if bars else "n/a"))
        print("prob_shift:", score_prob_shift(bars))
    print("margin enabled (perps):", KalshiPerpsClient().enabled())
