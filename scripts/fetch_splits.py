"""fetch_splits.py — split events, so market caps stop being wrong.

Yahoo's chart API returns a `close` that is already **split-adjusted**, while a
share count taken from an SEC filing is on the share basis of its own fiscal
year. Multiplying the two is only correct for a company that never split.

Measured damage before this fix, on 2016-06-30:

    GE    10.06B shares x $150.87  =  $1,518B   (1:8 reverse split in 2021)
    AAPL   5.82B shares x  $23.90  =    $139B   (4:1 split in 2020)

GE's real cap was ~$280B and Apple's ~$530B. GE hit the 10% position cap in
every year of the backtest and Apple - the largest company in the index -
never appeared. Every cap-weighted number computed from that is meaningless.

The fix: fetch each ticker's split history once, build the cumulative factor
from each date to today, and restate historical share counts onto today's
basis so they multiply correctly against today's split-adjusted prices.

    py scripts/fetch_splits.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARS = os.path.join(ROOT, "research_data", "yahoo_long")
OUT = os.path.join(ROOT, "research_data", "splits.json.gz")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/120 Safari/537.36")
CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1=0&period2=9999999999&interval=1d&events=split")


def fetch_splits(sym: str) -> list[dict] | None:
    req = urllib.request.Request(CHART.format(sym=sym), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    res = payload.get("chart", {}).get("result")
    if not res:
        return None
    ev = (res[0].get("events") or {}).get("splits") or {}
    out = []
    for s in ev.values():
        num, den = s.get("numerator"), s.get("denominator")
        if num and den:
            out.append({"date": int(s["date"]), "ratio": float(num) / float(den)})
    return sorted(out, key=lambda x: x["date"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    syms = sorted(f[:-7] for f in os.listdir(BARS) if f.endswith(".csv.gz"))
    store = {}
    if os.path.exists(OUT):
        with gzip.open(OUT, "rt") as f:
            store = json.load(f)

    todo = [s for s in syms if s not in store]
    print(f"{len(syms)} tickers, {len(todo)} still to fetch")
    n_split = 0
    for i, sym in enumerate(todo, 1):
        try:
            store[sym] = fetch_splits(sym) or []
        except urllib.error.HTTPError as e:
            if e.code in (429, 999):
                time.sleep(15)
                try:
                    store[sym] = fetch_splits(sym) or []
                except Exception:
                    store[sym] = []
            else:
                store[sym] = []
        except Exception:
            store[sym] = []
        n_split += bool(store[sym])
        if i % 100 == 0:
            with gzip.open(OUT, "wt") as f:
                json.dump(store, f)
            print(f"  [{i}/{len(todo)}] {n_split} with splits", flush=True)
        time.sleep(args.sleep)

    with gzip.open(OUT, "wt") as f:
        json.dump(store, f)
    have = sum(1 for v in store.values() if v)
    print(f"done: {len(store)} tickers, {have} have at least one split")


if __name__ == "__main__":
    main()
