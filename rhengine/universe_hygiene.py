"""
universe_hygiene.py — keep non-equities out of the equity scan universe.

The scorers in indicators.py are EQUITY strategies (residual momentum, Bollinger
mean reversion, MACD). Bond/credit/covered-call ETFs have near-zero ATR, so the
exit engine sets pennies-wide stops and ordinary noise executes them for a full
-1R over and over. In the whole-market Massive backtest these instruments were
~33% of all trades and lost ~-6,551 R — turning a +6,178 R clean-equity book into
a -1,712 R loser. `plain_symbols_only` can't catch them: an ETF like LQD or AGG
is a plain alphabetic ticker.

This module maintains a cached blocklist of US-listed ETFs (and a snapshot of the
full listed-symbol directory) sourced from the free Nasdaq Trader symbol
directory, and a loader the universe filter consults. It is a SCAN-LEVEL overlay
— it never touches indicators.py or the golden fixtures.

Refresh the cache (do this periodically; new ETFs list all the time):
    py -m rhengine.universe_hygiene refresh

The filter is applied by data_local.massive_universe when config.json
universe_filters.exclude_etfs is true.
"""

import json
import os
import time as _time

import requests

from ._paths import app_path

_CACHE_NAME = "etf_blocklist.json"
_SECTOR_CACHE = "sector_map.json"
_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt"
_FINNHUB_PROFILE = "https://finnhub.io/api/v1/stock/profile2"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) rhengine-hygiene"}

# Finnhub industry buckets for the "no biotech" hard rule. Biotechnology is
# excluded at any size (binary trial/FDA events = unpredictable gap risk). The
# broader clinical-stage buckets are excluded only under a market-cap floor so
# large stable names (JNJ, PFE, UNH) survive while small drug developers don't.
_BIOTECH_INDUSTRIES = {"Biotechnology"}
_SMALLCAP_HEALTH_INDUSTRIES = {"Pharmaceuticals", "Health Care",
                               "Life Sciences Tools & Services"}

_cache = None         # in-process memo: {"etfs": set, "listed": set, "refreshed": str}
_sector_cache = None  # in-process memo: {ticker: {"industry","country","mktcap"}}


def refresh_etf_blocklist(verbose=True):
    """Fetch the Nasdaq Trader symbol directory and cache the ETF set + the full
    listed-symbol set. Returns (n_etfs, n_listed). Whole-market, free, no key."""
    r = requests.get(_NASDAQ_URL, headers=_UA, timeout=60)
    r.raise_for_status()
    lines = r.text.splitlines()
    if not lines or not lines[0].startswith("Nasdaq Traded"):
        raise RuntimeError("Unexpected Nasdaq directory format (header missing)")
    header = lines[0].split("|")
    try:
        i_sym, i_etf = header.index("Symbol"), header.index("ETF")
    except ValueError:
        raise RuntimeError("Nasdaq directory missing Symbol/ETF columns: %r" % header)

    etfs, listed = set(), set()
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):  # footer
            continue
        cols = ln.split("|")
        if len(cols) <= max(i_sym, i_etf):
            continue
        sym = cols[i_sym].strip()
        if not sym:
            continue
        listed.add(sym)
        if cols[i_etf].strip() == "Y":
            etfs.add(sym)

    if not etfs:
        raise RuntimeError("No ETFs parsed — refusing to write an empty blocklist")

    payload = {"refreshed": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
               "source": _NASDAQ_URL,
               "etfs": sorted(etfs), "listed": sorted(listed)}
    path = app_path(_CACHE_NAME)
    with open(path, "w") as f:
        json.dump(payload, f)
    global _cache
    _cache = {"etfs": etfs, "listed": listed, "refreshed": payload["refreshed"]}
    if verbose:
        print("Wrote {} ({} ETFs, {} listed symbols) — refreshed {}".format(
            path, len(etfs), len(listed), payload["refreshed"]))
    return len(etfs), len(listed)


def _load(verbose=True):
    global _cache
    if _cache is not None:
        return _cache
    path = app_path(_CACHE_NAME)
    if not os.path.exists(path):
        if verbose:
            print("  universe_hygiene: no {} cache — run "
                  "`py -m rhengine.universe_hygiene refresh` (exclusion is a no-op "
                  "until then)".format(_CACHE_NAME))
        _cache = {"etfs": set(), "listed": set(), "refreshed": None}
        return _cache
    with open(path) as f:
        d = json.load(f)
    _cache = {"etfs": set(d.get("etfs", [])), "listed": set(d.get("listed", [])),
              "refreshed": d.get("refreshed")}
    return _cache


def etf_blocklist(verbose=True):
    """Set of US-listed ETF tickers to exclude from the equity scan (empty if the
    cache is missing — the filter then safely no-ops)."""
    return _load(verbose)["etfs"]


def listed_symbols(verbose=True):
    """Set of all currently US-listed tickers (for an optional require-listed
    filter that drops delisted/foreign names). Empty if the cache is missing."""
    return _load(verbose)["listed"]


# ---------------------------------------------------------- sector / ADR ----
# Finnhub profile2 gives industry (biotech) AND country (ADR = country != US) in
# one call. Free tier is 60 req/min, so classifying the whole scan universe is a
# slow one-time job — cached incrementally to sector_map.json and refreshable.

def _finnhub_key():
    with open(app_path("secrets.json")) as f:
        k = json.load(f).get("FINNHUB_API_KEY", "").strip()
    if not k:
        raise EnvironmentError("FINNHUB_API_KEY missing from secrets.json")
    return k


def refresh_sector_map(tickers, verbose=True, sleep=1.05, save_every=50):
    """Classify `tickers` via Finnhub profile2 (industry/country/marketCap in $M).
    Incremental: reads the existing cache and only fetches names not yet known,
    saving progress periodically so a long run can resume. Returns the full map."""
    path = app_path(_SECTOR_CACHE)
    data = json.load(open(path)) if os.path.exists(path) else {}
    key = _finnhub_key()
    todo = [t for t in tickers if t not in data]
    if verbose:
        print("sector_map: {} cached, {} to classify (~{:.0f} min at 60/min)".format(
            len(data), len(todo), len(todo) / 60.0))

    def _save():
        with open(path, "w") as f:
            json.dump(data, f)

    for i, t in enumerate(todo):
        try:
            r = requests.get(_FINNHUB_PROFILE, params={"symbol": t, "token": key},
                             timeout=20)
            if r.status_code == 429:          # rate limited — back off and retry once
                _time.sleep(5.0)
                r = requests.get(_FINNHUB_PROFILE, params={"symbol": t, "token": key},
                                 timeout=20)
            j = r.json() if r.status_code == 200 else {}
            data[t] = {"industry": j.get("finnhubIndustry"),
                       "country": j.get("country"),
                       "mktcap": j.get("marketCapitalization")}
        except Exception:
            data[t] = {"industry": None, "country": None, "mktcap": None}
        _time.sleep(sleep)
        if (i + 1) % save_every == 0:
            _save()
            if verbose:
                print("  classified {}/{}".format(i + 1, len(todo)), flush=True)
    _save()
    global _sector_cache
    _sector_cache = data
    if verbose:
        print("sector_map: {} names cached at {}".format(len(data), path))
    return data


def _load_sector_map(verbose=True):
    global _sector_cache
    if _sector_cache is not None:
        return _sector_cache
    path = app_path(_SECTOR_CACHE)
    if not os.path.exists(path):
        if verbose:
            print("  universe_hygiene: no {} — run `py -m rhengine.universe_hygiene "
                  "sectors` (ADR/biotech exclusion no-ops until then)".format(_SECTOR_CACHE))
        _sector_cache = {}
        return _sector_cache
    _sector_cache = json.load(open(path))
    return _sector_cache


def biotech_adr_blocklist(exclude_adr=True, exclude_biotech=True,
                          biotech_smallcap_max_mktcap=10000.0,
                          adr_smallcap_max_mktcap=10000.0, verbose=True):
    """Set of tickers to exclude as ADRs (foreign-domiciled) and/or biotech.

      exclude_adr — country != US. Gated by adr_smallcap_max_mktcap ($M): only
                    foreign names UNDER the cap are dropped, so liquid large-cap
                    foreign-domiciled names (ACN, ACGL, AEM, ALC) survive — they
                    carry little of the illiquid-ADR risk. Set the cap huge to
                    exclude all foreign, or 0 to keep the ADR rule off.
      exclude_biotech — Biotechnology at any size, plus small-cap clinical
                    Pharma/Health under biotech_smallcap_max_mktcap.

    Only classifies names present in sector_map.json; unknown names pass through
    (never over-excludes on missing data)."""
    data = _load_sector_map(verbose)
    out = set()
    for t, info in data.items():
        country = (info.get("country") or "").upper()
        industry = info.get("industry") or ""
        mktcap = info.get("mktcap") or 0
        if (exclude_adr and country not in ("US", "USA", "")
                and 0 < mktcap < adr_smallcap_max_mktcap):
            out.add(t); continue
        if exclude_biotech:
            if industry in _BIOTECH_INDUSTRIES:
                out.add(t); continue
            if industry in _SMALLCAP_HEALTH_INDUSTRIES and 0 < mktcap < biotech_smallcap_max_mktcap:
                out.add(t); continue
    return out


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if cmd == "refresh":
        refresh_etf_blocklist()
    elif cmd == "sectors":
        # classify a ticker universe: names from a CSV column, or all listed equities
        src = sys.argv[2] if len(sys.argv) > 2 else None
        if src:
            import csv as _csv
            with open(src) as f:
                syms = sorted({row["ticker"] for row in _csv.DictReader(f)})
        else:
            syms = sorted(listed_symbols())
        refresh_sector_map(syms)
    elif cmd == "status":
        c = _load()
        sm = _load_sector_map(verbose=False)
        block = biotech_adr_blocklist(verbose=False) if sm else set()
        print("etfs={} listed={} refreshed={} | sector_map={} | adr+biotech_block={}".format(
            len(c["etfs"]), len(c["listed"]), c["refreshed"], len(sm), len(block)))
    else:
        raise SystemExit("usage: py -m rhengine.universe_hygiene [refresh|sectors [csv]|status]")
