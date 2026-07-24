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
from bisect import bisect_right
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


# ------------------------------------------------------- full universe ----

def massive_universe(lookback_sessions=20, min_price=1.0, min_dollar_vol=500_000.0,
                     plain_symbols_only=True, exclude_etfs=False,
                     exclude_adr=False, exclude_biotech=False,
                     biotech_smallcap_max_mktcap=10000.0,
                     adr_smallcap_max_mktcap=10000.0, as_of=None, verbose=True):
    """Tickers passing tradeability floors over the `lookback_sessions` cached
    days ending at `as_of` (ISO date; default = the newest cached session).

    `as_of` is the point-in-time control. Leaving it None reproduces the legacy
    behavior — a universe drawn from the newest sessions and applied to all of
    history — which is survivorship-biased by construction (measured: 1,859 of
    10,095 common stocks alive on 2024-07-18 are absent by 2026-07-20, of which
    575 passed these very floors on day 1). Pass `as_of` per rebalance date, or
    use `pit_membership()`, to screen with only the information available then.

    NOT YET POINT-IN-TIME even with `as_of`: exclude_etfs / exclude_adr /
    exclude_biotech consult present-day snapshots (etf_blocklist.json,
    sector_map.json with *current* market cap). Those need dated vintages to be
    honest; until then they remain a look-ahead channel. See AUDIT FC-4.

    Filters:

      min_price      — median close floor (default $1: drops sub-dollar names
                       whose spreads/halts make fills fictional)
      min_dollar_vol — median close*volume floor (default $500k/day: a signal
                       you can't fill isn't a signal)
      plain_symbols_only — drop warrants/units/preferreds (symbols with . or /)
      exclude_etfs   — drop US-listed ETFs (universe_hygiene blocklist). These
                       are ~a third of the raw universe and lose heavily: an
                       equity engine sets pennies-wide stops on tiny-ATR bond/
                       credit ETFs and gets stopped out for -1R repeatedly.
                       `plain_symbols_only` can't catch them (LQD/AGG are plain
                       alphabetic tickers). Needs the blocklist cache — run
                       `py -m rhengine.universe_hygiene refresh` first.
      exclude_adr    — drop ADRs / foreign-domiciled names (country != US).
      exclude_biotech— drop Biotechnology (any cap) and small-cap clinical
                       Pharma/Health names (< biotech_smallcap_max_mktcap, $M):
                       binary trial/FDA events = unpredictable gap risk. Both use
                       the Finnhub sector_map cache — run
                       `py -m rhengine.universe_hygiene sectors` first (unknown
                       names pass through until classified).

    Set the floors to 0 in config to truly scan everything. Requires cached
    Massive data (run `py -m rhengine.market_data backfill` first)."""
    from .market_data import _MASSIVE_CACHE
    import statistics
    etf_block = set()
    if exclude_etfs:
        from .universe_hygiene import etf_blocklist
        etf_block = etf_blocklist(verbose=verbose)
    sector_block = set()
    if exclude_adr or exclude_biotech:
        from .universe_hygiene import biotech_adr_blocklist
        sector_block = biotech_adr_blocklist(
            exclude_adr=exclude_adr, exclude_biotech=exclude_biotech,
            biotech_smallcap_max_mktcap=biotech_smallcap_max_mktcap,
            adr_smallcap_max_mktcap=adr_smallcap_max_mktcap, verbose=verbose)
    days = sorted(f for f in os.listdir(_MASSIVE_CACHE) if f.endswith(".json.gz"))
    if as_of is not None:
        # POINT-IN-TIME: consider only sessions at or before as_of. Without this
        # the universe is always drawn from the most recent cached sessions and
        # applied retroactively to the whole backtest, which (a) silently drops
        # every name that delisted before today and (b) screens history on
        # today's price/liquidity. See docs/AUDIT-2026-07-24.md FC-4.
        days = [f for f in days if f[:-8] <= as_of]
    if not days:
        raise RuntimeError("massive_cache is empty — run "
                           "`py -m rhengine.market_data backfill` first")
    closes, dollars = {}, {}
    used = 0
    for fname in reversed(days):
        if used >= lookback_sessions:
            break
        with gzip.open(os.path.join(_MASSIVE_CACHE, fname), "rt") as f:
            day = json.load(f)
        if not day:
            continue  # holiday
        used += 1
        for t, bar in day.items():
            c, v = bar[3], bar[4]
            closes.setdefault(t, []).append(c)
            dollars.setdefault(t, []).append(c * v)
    out = []
    n_etf = 0
    n_sector = 0
    for t, cs in closes.items():
        # plain_symbols_only drops non-common-stock share classes. Polygon/Massive
        # encodes preferreds/warrants/units with LOWERCASE suffixes (BACpB, XYZw,
        # XYZu) and other classes with '.'/'/'; requiring pure-UPPERCASE-alpha drops
        # them all. Preferreds especially are bond-like (near-zero ATR) and, like the
        # bond ETFs, get noise-stopped for -1R — 121 of them lost -1,035 R in the book.
        if plain_symbols_only and not (t.isalpha() and t.isupper()):
            continue
        if exclude_etfs and t in etf_block:
            n_etf += 1
            continue
        if sector_block and t in sector_block:
            n_sector += 1
            continue
        if statistics.median(cs) < min_price:
            continue
        if statistics.median(dollars[t]) < min_dollar_vol:
            continue
        out.append(t)
    out.sort()
    if verbose:
        extra = ""
        if exclude_etfs:
            extra += ", excluded {} ETFs".format(n_etf)
        if sector_block:
            extra += ", excluded {} ADR/biotech".format(n_sector)
        print("  Universe: {} of {} cached tickers pass filters "
              "(min_price={}, min_dollar_vol={:,.0f}{})".format(
                  len(out), len(closes), min_price, min_dollar_vol, extra))
    return out


class PitMembership:
    """Point-in-time universe membership: which tickers were investable when.

    Built by `pit_membership()`. Holds a schedule of (rebalance_date, frozenset)
    snapshots; membership on any date is the most recent snapshot at or before
    it — exactly what a live system would have known.
    """

    def __init__(self, grid, rebalance_sessions):
        self._dates = [d for d, _ in grid]
        self._sets = [s for _, s in grid]
        self.rebalance_sessions = rebalance_sessions

    def contains(self, ticker, iso_date):
        i = bisect_right(self._dates, iso_date) - 1
        if i < 0:
            return False          # before the first snapshot: not yet screened
        return ticker in self._sets[i]

    def at(self, iso_date):
        i = bisect_right(self._dates, iso_date) - 1
        return self._sets[i] if i >= 0 else frozenset()

    def union(self):
        """Every ticker that was ever a member — the set of bars to fetch."""
        out = set()
        for s in self._sets:
            out |= s
        return sorted(out)

    def sizes(self):
        return [(d, len(s)) for d, s in zip(self._dates, self._sets)]

    def __len__(self):
        return len(self._dates)


def pit_membership(cfg, rebalance_sessions=21, verbose=True):
    """Build a PitMembership over the whole Massive cache in ONE pass.

    At every `rebalance_sessions`-th cached session, snapshot the set of tickers
    that pass the config's universe_filters using only the trailing
    `lookback_sessions` of data available at that date. Defaults to a ~monthly
    grid (21 trading sessions).

    This is the survivorship fix (AUDIT FC-4): a name that was liquid in 2024
    and delisted in 2025 is a member of the 2024 snapshots and absent from the
    2025 ones, instead of being erased from all of history.

    Cost: one read of every cached day. ~500 files for a 2-year cache.
    """
    from .market_data import _MASSIVE_CACHE
    import statistics
    from collections import deque

    uf = cfg.get("universe_filters", {}) or {}
    lookback = int(uf.get("lookback_sessions", 20))
    min_price = float(uf.get("min_price", 1.0))
    min_dollar_vol = float(uf.get("min_dollar_volume", 500_000.0))
    plain_only = bool(uf.get("plain_symbols_only", True))

    block = set()
    if bool(uf.get("exclude_etfs", False)):
        from .universe_hygiene import etf_blocklist
        block |= etf_blocklist(verbose=verbose)
    if bool(uf.get("exclude_adr", False)) or bool(uf.get("exclude_biotech", False)):
        from .universe_hygiene import biotech_adr_blocklist
        block |= biotech_adr_blocklist(
            exclude_adr=bool(uf.get("exclude_adr", False)),
            exclude_biotech=bool(uf.get("exclude_biotech", False)),
            biotech_smallcap_max_mktcap=float(uf.get("biotech_smallcap_max_mktcap", 10000.0)),
            adr_smallcap_max_mktcap=float(uf.get("adr_smallcap_max_mktcap", 10000.0)),
            verbose=verbose)

    days = sorted(f for f in os.listdir(_MASSIVE_CACHE) if f.endswith(".json.gz"))
    if not days:
        raise RuntimeError("massive_cache is empty — run "
                           "`py -m rhengine.market_data backfill` first")

    window = deque(maxlen=lookback)   # each entry: {ticker: (close, dollar_vol)}
    grid, used = [], 0
    if verbose:
        print("  Building point-in-time universe over {} cached sessions "
              "(rebalance every {})...".format(len(days), rebalance_sessions))
    for fname in days:
        with gzip.open(os.path.join(_MASSIVE_CACHE, fname), "rt") as f:
            day = json.load(f)
        if not day:
            continue  # holiday
        window.append({t: (bar[3], bar[3] * bar[4]) for t, bar in day.items()})
        used += 1
        if used < lookback or used % rebalance_sessions:
            continue
        closes, dollars = {}, {}
        for snap in window:
            for t, (c, dv) in snap.items():
                closes.setdefault(t, []).append(c)
                dollars.setdefault(t, []).append(dv)
        members = set()
        for t, cs in closes.items():
            if plain_only and not (t.isalpha() and t.isupper()):
                continue
            if t in block:
                continue
            if statistics.median(cs) < min_price:
                continue
            if statistics.median(dollars[t]) < min_dollar_vol:
                continue
            members.add(t)
        grid.append((fname[:-8], frozenset(members)))

    if not grid:
        raise RuntimeError(
            "no rebalance snapshots built — cache has {} sessions but "
            "lookback_sessions={}".format(used, lookback))
    m = PitMembership(grid, rebalance_sessions)
    if verbose:
        sizes = [n for _, n in m.sizes()]
        print("  PIT universe: {} snapshots, {}..{} names each, "
              "{} distinct names ever a member".format(
                  len(m), min(sizes), max(sizes), len(m.union())))
    return m


def resolve_watchlist(cfg, verbose=True):
    """cfg watchlist, or the whole cached market when watchlist is \"all\".
    Universe floors come from cfg[\"universe_filters\"] (optional)."""
    wl = cfg.get("watchlist", [])
    if wl != "all":
        return wl
    uf = cfg.get("universe_filters", {})
    return massive_universe(
        lookback_sessions=int(uf.get("lookback_sessions", 20)),
        min_price=float(uf.get("min_price", 1.0)),
        min_dollar_vol=float(uf.get("min_dollar_volume", 500_000.0)),
        plain_symbols_only=bool(uf.get("plain_symbols_only", True)),
        exclude_etfs=bool(uf.get("exclude_etfs", False)),
        exclude_adr=bool(uf.get("exclude_adr", False)),
        exclude_biotech=bool(uf.get("exclude_biotech", False)),
        biotech_smallcap_max_mktcap=float(uf.get("biotech_smallcap_max_mktcap", 10000.0)),
        adr_smallcap_max_mktcap=float(uf.get("adr_smallcap_max_mktcap", 10000.0)),
        verbose=verbose)


# ----------------------------------------------------------- dispatcher ----

def load_bars_any(cfg, lookback_days=400, verbose=True):
    """Fetch (bars_map, spy_bars) for the config's watchlist using whatever
    data_source the config names: "yahoo" (default, no key), or "alpaca"/
    "massive" via the production loader."""
    source = cfg.get("data_source", "yahoo")
    if source in ("alpaca", "massive"):
        from .backtest import load_bars
        return load_bars(cfg, lookback_days=lookback_days, verbose=verbose)
    if source == "kalshi":
        # Watchlist entries are "SERIES_TICKER/MARKET_TICKER"; bars are daily
        # implied probabilities [0,1]. SPY regime anchor still comes from Yahoo.
        from .market_data_kalshi import load_bars_kalshi
        return load_bars_kalshi(cfg, lookback_days=lookback_days, verbose=verbose)
    if source == "polymarket":
        # Watchlist entries are market slugs; volume=0 on these bars, so cut
        # VOL legs via legMask (see market_data_polymarket module note).
        from .market_data_polymarket import load_bars_polymarket
        return load_bars_polymarket(cfg, lookback_days=lookback_days, verbose=verbose)

    if cfg.get("watchlist") == "all":
        raise ValueError('watchlist "all" requires data_source "massive" — '
                         'Yahoo fetches one symbol at a time and would take hours')
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
