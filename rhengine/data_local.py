"""
data_local.py — no-key data layer for simulation and backtesting.

The engine's production adapters (market_data.py: Alpaca IEX, Massive/Polygon)
require API credentials. This module provides three credential-free paths that
produce bars in the exact shape the scorers expect
({"time": ms, "open", "high", "low", "close", "volume"}, ascending):

  1. fetch_yahoo / fetch_yahoo_batch — real daily history from Yahoo Finance's
     public chart API (consolidated volume, split+dividend adjusted via the
     adjclose ratio), disk-cached per ticker in yahoo_cache/.
  2. load_csv / load_csv_dir — offline OHLCV CSVs (Date,Open,High,Low,Close,Volume).
  3. synthetic_universe — seeded geometric random walks for deterministic tests
     and what-if simulations with zero network access.

load_bars_any(cfg, ...) is the dispatcher the sim CLIs use: it routes
data_source "yahoo" (default when unset) here and "alpaca"/"massive" to the
production loader in backtest.load_bars.
"""

import csv
import gzip
import json
import os
import random
import time as _time
from datetime import datetime, timezone

import requests

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) rhengine-backtest"}
CACHE_DIR = "yahoo_cache"

DAY_MS = 86_400_000


# ---------------------------------------------------------------- Yahoo ----

def _cache_path(ticker):
    return os.path.join(CACHE_DIR, ticker.upper() + ".json.gz")


def _cache_load(ticker, period1):
    """Return cached bars if fetched today (UTC) and covering period1, else None."""
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            blob = json.load(f)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    if blob.get("fetched") != today or blob.get("period1", 1 << 62) > period1:
        return None
    return blob.get("bars")


def _cache_save(ticker, period1, bars):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(ticker)
    tmp = path + ".tmp"
    blob = {
        "fetched": datetime.now(timezone.utc).date().isoformat(),
        "period1": period1,
        "bars": bars,
    }
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(blob, f, separators=(",", ":"))
    os.replace(tmp, path)


def fetch_yahoo(ticker, lookback_days=400, adjust=True, _retries=2):
    """Daily bars from Yahoo's chart API. No key needed. Cached for the day.

    adjust=True scales OHLC by adjclose/close so the series is split- and
    dividend-adjusted, matching the Massive/Polygon adjusted=true convention.
    Volume is left raw (the volume legs only use relative ratios).
    """
    now = int(_time.time())
    period1 = now - lookback_days * 86400
    cached = _cache_load(ticker, period1)
    if cached is not None:
        return [b for b in cached if b["time"] >= period1 * 1000]

    url = ("{}/{}?period1={}&period2={}&interval=1d"
           "&includeAdjustedClose=true".format(YAHOO_BASE, ticker, period1, now))
    last_err = None
    for attempt in range(_retries + 1):
        try:
            resp = requests.get(url, headers=_UA, timeout=20)
            if resp.status_code == 429:
                _time.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code == 404:
                raise ValueError("Yahoo: unknown symbol {}".format(ticker))
            resp.raise_for_status()
            payload = resp.json()
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            _time.sleep(1.0 * (attempt + 1))
    else:
        raise ConnectionError("Yahoo fetch failed for {}: {}".format(ticker, last_err))

    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = (payload.get("chart") or {}).get("error")
        raise ValueError("Yahoo: no data for {} ({})".format(ticker, err))
    r0 = result[0]
    ts = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((r0.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose")

    bars = []
    for i in range(len(ts)):
        o, h = quote["open"][i], quote["high"][i]
        lo, c = quote["low"][i], quote["close"][i]
        v = quote["volume"][i]
        if None in (o, h, lo, c) or v is None:
            continue  # Yahoo pads halts/holidays with nulls
        factor = 1.0
        if adjust and adj and adj[i] is not None and c:
            factor = adj[i] / c
        bars.append({
            "time": ts[i] * 1000,
            "open": round(o * factor, 6),
            "high": round(h * factor, 6),
            "low": round(lo * factor, 6),
            "close": round(c * factor, 6),
            "volume": v,
        })
    bars.sort(key=lambda b: b["time"])
    _cache_save(ticker, period1, bars)
    return bars


def fetch_yahoo_batch(tickers, lookback_days=400, verbose=True, pause=0.25):
    """Sequential fetch with polite pacing. Returns {"bars": {...}, "errors": {...}}
    — same shape as market_data's batch functions."""
    bars_map, errors = {}, {}
    for i, sym in enumerate(tickers):
        try:
            bars_map[sym] = fetch_yahoo(sym, lookback_days=lookback_days)
        except Exception as e:
            errors[sym] = "{}: {}".format(type(e).__name__, e)
        if pause and i + 1 < len(tickers):
            _time.sleep(pause)
    if verbose and errors:
        print("  Yahoo errors: " + ", ".join(sorted(errors)))
    return {"bars": bars_map, "errors": errors}


# ------------------------------------------------------------------ CSV ----

def load_csv(path):
    """Load one OHLCV CSV (Date,Open,High,Low,Close,Volume; extra columns ok,
    case-insensitive headers). Date may be ISO (YYYY-MM-DD) or epoch seconds/ms."""
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            low = {k.strip().lower(): v for k, v in row.items() if k}
            raw = (low.get("date") or low.get("time") or low.get("timestamp") or "").strip()
            if not raw:
                continue
            try:
                if "-" in raw or "/" in raw:
                    d = datetime.fromisoformat(raw.replace("/", "-")[:10])
                    ms = int(d.replace(tzinfo=timezone.utc).timestamp() * 1000)
                else:
                    num = float(raw)
                    ms = int(num if num > 1e12 else num * 1000)
                bars.append({
                    "time": ms,
                    "open": float(low["open"]),
                    "high": float(low["high"]),
                    "low": float(low["low"]),
                    "close": float(low["close"]),
                    "volume": float(low.get("volume") or 0),
                })
            except (KeyError, ValueError):
                continue
    bars.sort(key=lambda b: b["time"])
    return bars


def load_csv_dir(dirpath):
    """Load every *.csv in a directory as {TICKER: bars} (ticker = filename stem)."""
    out = {}
    for name in sorted(os.listdir(dirpath)):
        if name.lower().endswith(".csv"):
            out[os.path.splitext(name)[0].upper()] = load_csv(os.path.join(dirpath, name))
    return out


# ------------------------------------------------------------ synthetic ----

def _walk(n, start, drift, wobble, seed):
    rng = random.Random(seed)
    closes, px = [], start
    for _ in range(n):
        px *= 1.0 + drift + rng.uniform(-wobble, wobble)
        closes.append(px)
    return closes


def _bars_from_closes(closes, seed, base_vol=1_000_000, t0=1_700_000_000_000):
    rng = random.Random(seed + 1)
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev if i else c
        hi = max(o, c) * (1.0 + rng.uniform(0.0, 0.01))
        lo = min(o, c) * (1.0 - rng.uniform(0.0, 0.01))
        bars.append({
            "time": t0 + i * DAY_MS,
            "open": round(o, 4), "high": round(hi, 4),
            "low": round(lo, 4), "close": round(c, 4),
            "volume": int(base_vol * rng.uniform(0.6, 1.8)),
        })
        prev = c
    return bars


def synthetic_universe(n_tickers=8, n_bars=300, seed=42):
    """Deterministic bars_map + spy_bars for offline simulation. Tickers get a
    mix of drifts (trending up/down/flat) so every strategy has material."""
    rng = random.Random(seed)
    bars_map = {}
    for i in range(n_tickers):
        drift = rng.choice([0.0025, 0.0015, 0.0, -0.0015, -0.0025])
        wobble = rng.choice([0.008, 0.012, 0.02])
        closes = _walk(n_bars, rng.uniform(20, 300), drift, wobble, seed * 1000 + i)
        bars_map["SYN{:02d}".format(i)] = _bars_from_closes(closes, seed * 1000 + i)
    spy = _bars_from_closes(_walk(n_bars, 450, 0.0008, 0.007, seed * 7 + 3), seed * 7 + 3)
    return bars_map, spy


# ----------------------------------------------------------- dispatcher ----

def load_bars_any(cfg, lookback_days=400, verbose=True):
    """Fetch (bars_map, spy_bars) for the config's watchlist using whatever
    data_source the config names: "yahoo" (default, no key), or "alpaca"/
    "massive" via the production loader."""
    source = cfg.get("data_source", "yahoo")
    if source in ("alpaca", "massive"):
        from .backtest import load_bars
        return load_bars(cfg, lookback_days=lookback_days, verbose=verbose)

    watchlist = cfg["watchlist"]
    spy_ticker = cfg.get("spy_ticker", "SPY")
    fetch = list(dict.fromkeys(watchlist + [spy_ticker]))
    if verbose:
        print("Fetching {} symbols, {}d of history (source=yahoo)...".format(
            len(fetch), lookback_days))
    bars_map = fetch_yahoo_batch(fetch, lookback_days=lookback_days,
                                 verbose=verbose)["bars"]
    spy_bars = bars_map.pop(spy_ticker, None)
    if not spy_bars:
        raise RuntimeError("{} bars required for regime classification".format(spy_ticker))
    return bars_map, spy_bars
