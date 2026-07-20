"""
market_data.py — Alpaca IEX bar fetcher
Port of nydex/src/api.js

Fetches OHLCV bars for one or many tickers via Alpaca's market data endpoint.
Credentials are read from environment variables — never hardcoded.
"""

import json
import os
import re
import requests
import time
from datetime import datetime, timedelta, timezone

ALPACA_BASE = "https://data.alpaca.markets/v2"

TF_MAP = {
    "1h": "1Hour",
    "4h": "4Hour",
    "1d": "1Day",
}


from ._paths import app_path as _app_path  # robust resolver (walks up to repo root)


def _get_credentials():
    """
    Read Alpaca keys from secrets.json in the same folder as this file.
    Fill in your keys there — never share or commit that file.

    secrets.json format:
        {
          "ALPACA_API_KEY":    "your_key_here",
          "ALPACA_API_SECRET": "your_secret_here"
        }

    Falls back to environment variables if secrets.json is missing.
    """
    import json

    secrets_path = _app_path("secrets.json")

    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            s = json.load(f)
        key    = s.get("ALPACA_API_KEY",    "").strip()
        secret = s.get("ALPACA_API_SECRET", "").strip()
    else:
        key    = os.environ.get("ALPACA_API_KEY",    "").strip()
        secret = os.environ.get("ALPACA_API_SECRET", "").strip()

    if not key or not secret or "paste_your" in key:
        raise EnvironmentError(
            "Alpaca credentials not set.\n"
            "Open RobinhoodAgent/secrets.json and replace the placeholder values "
            "with your actual Alpaca API key and secret."
        )
    return key, secret


def _safe_ticker(t):
    """
    Sanitize ticker symbol. Uppercase only, strips anything not A-Z or dot.
    Alpaca IEX uses BRK.B (not BRK-B or BRK/B).
    """
    clean = str(t).upper().strip()
    clean = re.sub(r"[-/]", ".", clean)  # BRK-B / BRK/B -> BRK.B (Alpaca format)
    clean = re.sub(r"[^A-Z.]", "", clean)
    if not clean:
        raise ValueError(f"Invalid ticker symbol: {t!r}")
    return clean


def fetch_bars(ticker, interval="1d", lookback_days=90):
    """
    Fetch OHLCV bars for a single ticker from Alpaca IEX.

    Args:
        ticker       : str  — e.g. "AAPL", "SPY"
        interval     : str  — "1h", "4h", or "1d"
        lookback_days: int  — how many calendar days back to fetch

    Returns:
        list of dicts: [{time, open, high, low, close, volume}, ...]
        time is Unix ms (same format as Nydex JS bars)

    Raises:
        EnvironmentError  — missing API keys
        PermissionError   — invalid API keys (403)
        ValueError        — symbol not found (422) or no data returned
        ConnectionError   — network/timeout issues
    """
    key, secret = _get_credentials()
    symbol = _safe_ticker(ticker)
    start  = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    tf     = TF_MAP.get(interval, "1Day")

    url    = f"{ALPACA_BASE}/stocks/{symbol}/bars"
    params = {
        "timeframe": tf,
        "start":     start,
        "limit":     1000,
        "feed":      "iex",
        "sort":      "asc",
    }
    headers = {
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
    }

    res = _alpaca_get(url, params=params, headers=headers, label=ticker)

    if res.status_code == 403:
        raise PermissionError("Invalid Alpaca API keys — check ALPACA_API_KEY and ALPACA_API_SECRET")
    if res.status_code == 422:
        raise ValueError(f"Symbol not found on Alpaca IEX: {ticker}")
    if not res.ok:
        raise ConnectionError(f"Alpaca HTTP {res.status_code} for {ticker}: {res.text[:200]}")

    body = res.json()
    raw  = body.get("bars", [])
    if not raw:
        raise ValueError(f"No bar data returned for {ticker}")
    if body.get("next_page_token"):
        # ponytail: no pagination — with sort=asc a truncated response silently
        # drops the NEWEST bars, so fail loud instead of trading on stale data.
        raise ConnectionError(
            f"Alpaca response for {ticker} was truncated (next_page_token set). "
            "Reduce lookback_days or add pagination before using this interval."
        )

    return [
        {
            "time":   int(datetime.fromisoformat(
                          b["t"].replace("Z", "+00:00")
                      ).timestamp() * 1000),
            "open":   b["o"],
            "high":   b["h"],
            "low":    b["l"],
            "close":  b["c"],
            "volume": b["v"],
        }
        for b in raw
    ]


def _alpaca_get(url, params, headers, label, max_retries=2):
    """
    GET wrapper with small 429 backoff.

    Alpaca's free market data plan allows 200 historical API calls/minute, but
    repeated local smoke tests can still trip the limiter. Prefer batch calls
    where possible; this only handles short-lived bursts.
    """
    for attempt in range(max_retries + 1):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=10)
        except requests.exceptions.Timeout:
            raise ConnectionError(f"Alpaca request timed out for {label}")
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Request failed for {label}: {e}")

        if res.status_code != 429 or attempt == max_retries:
            return res

        retry_after = res.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            wait_seconds = min(int(retry_after), 60)
        else:
            wait_seconds = 5 * (attempt + 1)
        time.sleep(wait_seconds)

    return res


def _data_source():
    """Read the configured data source ('alpaca' | 'massive'), default alpaca."""
    try:
        cfg_path = _app_path("config.json")
        with open(cfg_path) as f:
            return json.load(f).get("data_source", "alpaca")
    except Exception:
        return "alpaca"


def fetch_bars_batch(tickers, interval="1d", lookback_days=90, verbose=True, source=None):
    """Dispatch a batch fetch to the configured data source.

    'massive' = Polygon/Massive REST grouped-daily (real consolidated SIP volume,
    locally cached); 'alpaca' = Alpaca IEX. Massive only serves daily bars; any
    other interval falls back to Alpaca. Pass source= to force one."""
    src = source or _data_source()
    if src == "massive" and interval == "1d":
        return fetch_bars_batch_massive(tickers, lookback_days=lookback_days, verbose=verbose)
    return _fetch_bars_batch_alpaca(tickers, interval=interval,
                                    lookback_days=lookback_days, verbose=verbose)


def _fetch_bars_batch_alpaca(tickers, interval="1d", lookback_days=90, verbose=True):
    """
    Fetch bars for a list of tickers from Alpaca IEX. Skips failed tickers.

    Returns:
        {
          "bars":   {ticker: [bars]},   # successful fetches
          "errors": {ticker: str},      # failed tickers and why
        }
    """
    bars_map = {}
    errors   = {}
    requested_symbols = []
    display_by_symbol = {}

    for ticker in tickers:
        try:
            symbol = _safe_ticker(ticker)
            requested_symbols.append(symbol)
            display_by_symbol[symbol] = ticker
        except Exception as e:
            errors[ticker] = str(e)

    if not requested_symbols:
        return {"bars": bars_map, "errors": errors}

    if verbose:
        print(f"  Fetching {len(requested_symbols)} symbols in one Alpaca request...", end=" ", flush=True)

    key, secret = _get_credentials()
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    tf = TF_MAP.get(interval, "1Day")
    url = f"{ALPACA_BASE}/stocks/bars"
    params = {
        "symbols": ",".join(requested_symbols),
        "timeframe": tf,
        "start": start,
        "limit": 10000,
        "feed": "iex",
        "sort": "asc",
    }
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }

    res = _alpaca_get(url, params=params, headers=headers, label="batch")

    if res.status_code == 403:
        raise PermissionError("Invalid Alpaca API keys — check ALPACA_API_KEY and ALPACA_API_SECRET")
    if res.status_code == 422:
        raise ValueError(f"Alpaca rejected the batch request: {res.text[:200]}")
    if res.status_code == 429:
        raise ConnectionError(
            "Alpaca HTTP 429: too many requests. Wait about a minute, then retry. "
            "If this keeps happening, another script or app is using the same Alpaca key."
        )
    if not res.ok:
        raise ConnectionError(f"Alpaca HTTP {res.status_code} for batch: {res.text[:200]}")

    payload = res.json()
    if payload.get("next_page_token"):
        # ponytail: shared 10k-bar cap across all symbols; with sort=asc the
        # NEWEST bars are the ones dropped — fail loud, don't trade truncated.
        raise ConnectionError(
            "Alpaca batch response was truncated (next_page_token set). "
            "Fetch fewer symbols per batch / smaller lookback, or add pagination."
        )
    raw_by_symbol = payload.get("bars", {})

    for symbol in requested_symbols:
        ticker = display_by_symbol[symbol]
        raw = raw_by_symbol.get(symbol, [])
        if not raw:
            errors[ticker] = f"No bar data returned for {ticker}"
            continue

        bars_map[ticker] = [
            {
                "time": int(datetime.fromisoformat(
                    b["t"].replace("Z", "+00:00")
                ).timestamp() * 1000),
                "open": b["o"],
                "high": b["h"],
                "low": b["l"],
                "close": b["c"],
                "volume": b["v"],
            }
            for b in raw
        ]

    if verbose:
        print(f"{len(bars_map)} ok, {len(errors)} skipped")

    if verbose and errors:
        print(f"\n  Skipped {len(errors)}: {', '.join(errors.keys())}")

    return {"bars": bars_map, "errors": errors}


# ─────────────────────────────────────────────
# Massive / Polygon data source — REST grouped-daily with a local cache.
# Real consolidated SIP volume (vs Alpaca IEX's ~2-3%). Free tier is 5 calls/min,
# so each trading day's whole-market snapshot is cached to disk and only fetched
# once; after the initial backfill a daily run needs a single new call.
# ─────────────────────────────────────────────

import gzip
from zoneinfo import ZoneInfo

_MASSIVE_URL = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks"
_MASSIVE_CACHE = _app_path("massive_cache")
_ET = ZoneInfo("America/New_York")


def _massive_key():
    secrets_path = _app_path("secrets.json")
    import json as _json
    with open(secrets_path) as f:
        key = _json.load(f).get("MASSIVE_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("MASSIVE_API_KEY missing from secrets.json")
    return key


def _massive_fetch_day(date_iso, key, max_wait=30):
    """Fetch one trading day's whole-market grouped bars → {ticker: bar-tuple}.
    Returns {} for non-trading days. Backs off briefly on 429 (5 calls/min free
    tier) then raises so the caller can skip this day and move on."""
    waited = 0
    while True:
        r = requests.get(f"{_MASSIVE_URL}/{date_iso}",
                         params={"apiKey": key, "adjusted": "true"}, timeout=30)
        if r.status_code == 429:
            if waited >= max_wait:
                raise ConnectionError("Massive rate limit")
            time.sleep(13)
            waited += 13
            continue
        if not r.ok:
            raise ConnectionError(f"Massive HTTP {r.status_code}: {r.text[:120]}")
        out = {}
        for row in r.json().get("results", []):
            out[row["T"]] = [row["o"], row["h"], row["l"], row["c"], row["v"], row["t"]]
        return out


def _massive_day(date_iso, key, allow_fetch=True):
    """Cached whole-market bars for a date. Non-trading days cache as {} so they
    are never re-fetched. Only past dates are cached (today may be incomplete).

    ponytail: days are fetched adjusted-as-of-fetch-date and cached forever, so a
    later stock split leaves a silent price/volume discontinuity mid-series (fake
    PEAD events / breakouts / bad ATR for that ticker). Ceiling accepted for now;
    upgrade path: delete the cached window for a ticker's split date range (or
    `rm -r massive_cache` to rebuild) when a watchlist name splits."""
    path = os.path.join(_MASSIVE_CACHE, f"{date_iso}.json.gz")
    if os.path.exists(path):
        try:
            with gzip.open(path, "rt") as f:
                return json.load(f)
        except (OSError, ValueError):
            os.remove(path)  # corrupt/partial cache file — refetch below
    if not allow_fetch:
        return None
    day = _massive_fetch_day(date_iso, key)
    os.makedirs(_MASSIVE_CACHE, exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt") as f:
        json.dump(day, f)
    os.replace(tmp, path)  # atomic — never leave a partial cache file
    return day


def fetch_bars_batch_massive(tickers, lookback_days=130, verbose=True):
    """Assemble per-ticker daily bars from cached whole-market grouped files.
    Same return shape as fetch_bars_batch. First call over a new window is slow
    (5 req/min); subsequent calls hit the cache."""
    key = _massive_key()
    today_et = datetime.now(timezone.utc).astimezone(_ET).date()
    start = today_et - timedelta(days=lookback_days)

    # Weekday dates in the window (skip weekends to avoid wasting rate-limited
    # calls; holidays return {} and cache cheaply).
    dates = []
    d = start
    while d < today_et:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)

    wanted = set(tickers)
    series = {t: [] for t in tickers}
    to_fetch = [d for d in dates if not os.path.exists(os.path.join(_MASSIVE_CACHE, f"{d}.json.gz"))]
    if verbose:
        print(f"  Massive: {len(dates)} days in window, {len(to_fetch)} uncached "
              f"(~{max(0, len(to_fetch))//5}min to backfill at 5/min); using cache...", flush=True)

    # Fetch most-recent-first so, if the free-tier rate limit cuts a run short, the
    # bars indicators care about most (the latest) are the ones we secured. A day
    # that can't be fetched is skipped, not fatal — partial history still scores.
    skipped = 0
    for date_iso in sorted(dates, reverse=True):
        try:
            day = _massive_day(date_iso, key)
        except Exception:
            skipped += 1
            continue
        for t in wanted:
            bar = day.get(t)
            if bar:
                o, h, l, c, v, ts = bar
                series[t].append({"time": int(ts), "open": o, "high": h,
                                  "low": l, "close": c, "volume": v})
    if verbose and skipped:
        print(f"    ({skipped} days skipped this run — rate limit; re-run to backfill)", flush=True)

    bars_map, errors = {}, {}
    for t in tickers:
        if series[t]:
            series[t].sort(key=lambda b: b["time"])
            bars_map[t] = series[t]
        else:
            errors[t] = f"No Massive data for {t}"
    if verbose:
        print(f"  Massive: {len(bars_map)} ok, {len(errors)} empty")
    return {"bars": bars_map, "errors": errors}


def backfill_massive(lookback_days=230, verbose=True):
    """Paced backfill of the whole-market daily cache for a contiguous window.
    Unlike the live fetch (which skips days on rate limit), this WAITS for the
    5/min budget so it reliably fills every uncached trading day. Oldest-first so
    a contiguous block builds toward the present. Fully resumable — re-run to
    continue; cached days are skipped instantly."""
    key = _massive_key()
    today_et = datetime.now(timezone.utc).astimezone(_ET).date()
    start = today_et - timedelta(days=lookback_days)
    dates = []
    d = start
    while d < today_et:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    uncached = [d for d in dates
                if not os.path.exists(os.path.join(_MASSIVE_CACHE, f"{d}.json.gz"))]
    if verbose:
        print(f"backfill: {len(dates)} weekdays in window, {len(uncached)} to fetch "
              f"(~{len(uncached)//5 + 1} min at 5/min)", flush=True)
    os.makedirs(_MASSIVE_CACHE, exist_ok=True)
    done = 0
    for i in range(0, len(uncached), 5):
        batch = uncached[i:i + 5]
        for date_iso in batch:
            try:
                day = _massive_fetch_day(date_iso, key, max_wait=0)  # first try; paced
            except ConnectionError:
                time.sleep(62)  # rate limited despite pacing — wait a full minute
                day = _massive_fetch_day(date_iso, key, max_wait=60)
            tmp = os.path.join(_MASSIVE_CACHE, f"{date_iso}.json.gz.tmp")
            with gzip.open(tmp, "wt") as f:
                json.dump(day, f)
            os.replace(tmp, os.path.join(_MASSIVE_CACHE, f"{date_iso}.json.gz"))
            done += 1
        if verbose:
            print(f"  {done}/{len(uncached)} fetched", flush=True)
        if i + 5 < len(uncached):
            time.sleep(62)  # wait for the next minute's 5-call budget
    if verbose:
        print(f"backfill done: {done} new days cached", flush=True)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 230
        backfill_massive(lookback_days=days)
        raise SystemExit

    # Quick smoke test — run: python market_data.py
    print("Testing Alpaca connection...")
    result = fetch_bars_batch(["SPY", "AAPL", "FAKEXYZ"], interval="1d", lookback_days=10)
    for ticker, bars in result["bars"].items():
        last = bars[-1]
        print(f"  {ticker}: {len(bars)} bars, last close ${last['close']:.2f}")
    if result["errors"]:
        print(f"  Errors (expected for FAKEXYZ): {result['errors']}")
