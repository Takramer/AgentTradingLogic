"""
backfill.py — resumable, liquidity-ranked AlphaVantage earnings backfill.

The SUE gate (market_data_av.sue_gate) needs a cached earnings history per
ticker, but the free AlphaVantage tier is 25 requests/DAY. The PEAD signal
is uniform across the market (~4 earnings gaps/yr/ticker), so there is no
Pareto shortcut: covering all ~6k PEAD names would take ~300 days. The
tractable play is a FIXED target universe, backfilled MOST-LIQUID-FIRST so
that partial progress always covers the names you would actually trade, and
resumable so a daily scheduled task fills it unattended over a few weeks.

  target_universe()  — top-N tickers by median dollar volume from the
                       Massive cache (the completable default; ~N/20 days)
  tickers_from_csvs()— union of tickers in trade CSV(s), optional strategy
                       filter (e.g. only names PEAD actually traded)
  rank_by_liquidity()— order any ticker set by Massive-cache dollar volume
  run_once()         — spend up to max_requests on missing/stale names, in
                       liquidity order, pacing the per-minute limiter
  coverage()         — (fresh, stale, missing) split + a state manifest

CLI (see __main__): --once for Task Scheduler, --loop for an always-on
session. State/manifest: av_cache/_backfill_state.json.

Deliberately does NOT tune gate thresholds or wire gates into the
validation tools (ic/crossval/sanity) — that waits for the multiple-testing
templates so the gate config can be chosen under proper OOS/multiplicity
controls. This module only makes sure the DATA is ready when they land.
"""

import json
import os
import statistics
import time
from datetime import datetime, timezone

from .market_data_av import _cache_read, fetch_earnings, RateLimited, _CACHE_DIR

_STATE = os.path.join(_CACHE_DIR, "_backfill_state.json")
_PACE_SECONDS = 13  # free tier is 5 req/min; 13s spacing stays safely under


# ------------------------------------------------------- universe sources ----

def _massive_dollar_vol(lookback_sessions=20):
    """{ticker: median daily dollar volume} over the most recent cached
    Massive sessions. {} if the cache is absent."""
    from .market_data import _MASSIVE_CACHE
    import gzip
    if not os.path.isdir(_MASSIVE_CACHE):
        return {}
    days = sorted(f for f in os.listdir(_MASSIVE_CACHE) if f.endswith(".json.gz"))
    dollars, used = {}, 0
    for fname in reversed(days):
        if used >= lookback_sessions:
            break
        with gzip.open(os.path.join(_MASSIVE_CACHE, fname), "rt") as f:
            day = json.load(f)
        if not day:
            continue  # holiday
        used += 1
        for t, bar in day.items():
            dollars.setdefault(t, []).append(bar[3] * bar[4])  # close * volume
    return {t: statistics.median(v) for t, v in dollars.items() if v}


def rank_by_liquidity(tickers, dvol=None):
    """Order `tickers` by descending Massive-cache dollar volume. Names with
    no cache entry sort last, preserving their given order (stable)."""
    dvol = _massive_dollar_vol() if dvol is None else dvol
    ranked = sorted((t for t in tickers if t in dvol),
                    key=lambda t: dvol[t], reverse=True)
    tail = [t for t in tickers if t not in dvol]
    return ranked + tail


def target_universe(size=500, min_price=5.0, min_dollar_vol=1_000_000.0):
    """Top-`size` most-liquid plain-symbol tickers from the Massive cache —
    the completable default target (~size/20 days at 20 req/day)."""
    dvol = _massive_dollar_vol()
    if not dvol:
        raise RuntimeError(
            "Massive cache empty — can't rank by liquidity. Use --from-csv or "
            "--from-config, or run `py -m rhengine.market_data backfill` first.")
    from .market_data import _MASSIVE_CACHE  # noqa: F401  (cache existence proven above)
    elig = [t for t, d in dvol.items()
            if t.isalpha() and d >= min_dollar_vol]
    elig.sort(key=lambda t: dvol[t], reverse=True)
    return elig[:size], dvol


def tickers_from_csvs(paths, strategy=None):
    """Union of the `ticker` column across trade CSV(s), optionally filtered
    to one strategy. Preserves first-seen order."""
    import csv
    seen, out = set(), []
    for path in paths:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if strategy and row.get("strategy") != strategy:
                    continue
                t = (row.get("ticker") or "").strip().upper()
                if t and t not in seen:
                    seen.add(t)
                    out.append(t)
    return out


# ------------------------------------------------------------- coverage ----

def coverage(tickers, ttl_days=7):
    """Split `tickers` into (fresh, stale, missing) by reading av_cache.
    A negative-cached no-earnings symbol (ETF) counts as fresh forever."""
    now = time.time()
    fresh, stale, missing = [], [], []
    for t in tickers:
        blob = _cache_read(t)
        if not blob:
            missing.append(t)
        elif blob.get("no_earnings") or now - blob.get("fetched_ts", 0) < ttl_days * 86400:
            fresh.append(t)
        else:
            stale.append(t)
    return fresh, stale, missing


def _write_state(target, fresh, stale, missing, spent, max_requests):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    done = len(fresh)
    todo = len(stale) + len(missing)
    eta_days = (todo + max_requests - 1) // max(1, max_requests) if todo else 0
    state = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_size": len(target),
        "cached_fresh": done,
        "remaining": todo,
        "coverage_pct": round(100 * done / len(target), 1) if target else 0.0,
        "requests_spent_last_run": spent,
        "eta_days_at_budget": eta_days,
    }
    tmp = _STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, _STATE)
    return state


# --------------------------------------------------------------- runner ----

def run_once(target, max_requests=20, ttl_days=7, verbose=True):
    """Spend up to max_requests network calls on missing/stale target names,
    most-liquid-first (target must already be liquidity-ranked). Fresh names
    cost nothing. Returns the state dict. Resumable — re-run to continue."""
    fresh, stale, missing = coverage(target, ttl_days=ttl_days)
    queue = missing + stale  # missing first, then refresh stale; both in target order
    spent = 0
    for sym in queue:
        if spent >= max_requests:
            break
        try:
            if spent:
                time.sleep(_PACE_SECONDS)  # respect 5/min free tier
            fetch_earnings(sym, ttl_days=ttl_days)  # offline=False → network
            spent += 1
        except RateLimited:
            # Daily/per-minute budget gone — stop; today's calls are spent.
            if verbose:
                print("  AV throttled after {} calls — stopping this run "
                      "(budget exhausted; resume tomorrow).".format(spent))
            spent += 1  # the throttled call still counted against the budget
            break
        except Exception as e:
            if verbose:
                print("  {} failed: {}".format(sym, e))
    fresh, stale, missing = coverage(target, ttl_days=ttl_days)  # recount
    state = _write_state(target, fresh, stale, missing, spent, max_requests)
    if verbose:
        print("  Backfill: {}/{} cached ({}%), {} remaining, spent {} calls"
              "{}".format(state["cached_fresh"], state["target_size"],
                          state["coverage_pct"], state["remaining"], spent,
                          "" if state["remaining"] == 0
                          else ", ~{} days to full at {}/day".format(
                              state["eta_days_at_budget"], max_requests)))
    return state


# Trade CSVs the auto-default scans for PEAD signalers (first match wins).
_PEAD_CSV_CANDIDATES = [
    os.path.join("tests", "results", "refined_trades.csv"),
    os.path.join("tests", "results", "fullmarket_trades.csv"),
    os.path.join("tests", "results", "backtest_trades.csv"),
]


def _auto_pead_target(size):
    """Smart default: the tickers PEAD actually trades (from a discovered
    trade CSV), ranked by liquidity, capped to `size`. Every backfilled name
    is one the SUE gate can act on — no budget wasted on names PEAD never
    trades. Falls back to the pure liquid universe if no CSV is found."""
    for path in _PEAD_CSV_CANDIDATES:
        if os.path.exists(path):
            pead = tickers_from_csvs([path], strategy="pead")
            if pead:
                ranked = rank_by_liquidity(pead)
                print("  Default target: {} PEAD signalers from {} "
                      "(liquidity-ranked, capped to {}).".format(
                          len(pead), path, size))
                return ranked[:size]
    print("  No PEAD trade CSV found — falling back to the liquid universe.")
    target, _ = target_universe(size=size)
    return target


def _resolve_target(args):
    """Turn CLI args into a liquidity-ranked target ticker list."""
    if args.tickers:
        base = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        return rank_by_liquidity(base)
    if args.from_csv:
        base = tickers_from_csvs(args.from_csv.split(","),
                                 strategy=args.strategy)
        return rank_by_liquidity(base)
    if args.from_config:
        with open(args.config) as f:
            cfg = json.load(f)
        from .data_local import resolve_watchlist
        return rank_by_liquidity(resolve_watchlist(cfg, verbose=False))
    if args.liquid_universe:
        target, _ = target_universe(size=args.target_size)
        return target
    return _auto_pead_target(args.target_size)  # smart default


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Resumable, liquidity-ranked AlphaVantage earnings backfill.")
    src = ap.add_argument_group("target universe (pick one; default = Massive top-N)")
    src.add_argument("--target-size", type=int, default=500,
                     help="cap the target to N tickers (default 500 ~ 26 days)")
    src.add_argument("--liquid-universe", action="store_true",
                     help="target the top-N liquid Massive names instead of the "
                          "default (PEAD signalers from a discovered trade CSV)")
    src.add_argument("--from-csv", default=None,
                     help="comma-separated trade CSV(s); backfill their tickers")
    src.add_argument("--from-config", action="store_true",
                     help="backfill the config watchlist (resolve_watchlist)")
    src.add_argument("--tickers", default=None, help="explicit comma-separated list")
    src.add_argument("--strategy", default=None,
                     help="with --from-csv, only tickers this strategy traded (e.g. pead)")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--max-requests", type=int, default=20,
                    help="network calls per run (default 20; free tier is 25/day)")
    ap.add_argument("--ttl-days", type=int, default=7,
                    help="treat a cache entry older than this as stale (default 7)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="spend today's budget and exit (default; for Task Scheduler)")
    mode.add_argument("--loop", action="store_true",
                      help="run daily forever (sleep ~24h between runs)")
    args = ap.parse_args()

    target = _resolve_target(args)
    print("Target: {} tickers (liquidity-ranked). av_cache/_backfill_state.json "
          "tracks coverage.".format(len(target)))

    while True:
        state = run_once(target, max_requests=args.max_requests,
                         ttl_days=args.ttl_days)
        if not args.loop or state["remaining"] == 0:
            if state["remaining"] == 0:
                print("Backfill complete — target fully cached.")
            break
        # Sleep to the next daily budget. 24h + a minute of slack.
        print("Sleeping ~24h for the next daily budget (Ctrl-C to stop)...")
        time.sleep(24 * 3600 + 60)


if __name__ == "__main__":
    main()
