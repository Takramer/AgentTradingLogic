"""fetch_cboe_benchmarks.py — the published record for premium-selling strategies.

WHY THIS IS THE HIGHEST-VALUE OPTIONS FETCH IN THE REPO

Every options idea in docs/OPTIONS-SOURCES-2026-08-01.md is blocked behind
months of forward capture, because `options_registry.py` accumulates forward
only and expired-contract discovery lapses in weeks. Every one except the wheel.

The wheel is a cash-secured put that becomes a covered call on assignment, and
CBOE has published both halves as rules-based indices for decades:

    BXM   CBOE S&P 500 BuyWrite Index. Long the S&P 500, short a one-month
          at-the-money call, rolled monthly. THE COVERED-CALL HALF.
    PUT   CBOE S&P 500 PutWrite Index. Fully collateralised short one-month
          at-the-money put. THE CASH-SECURED-PUT HALF.

So the strategy the three videos in that intake teach does not need a forward
sample to be evaluated. It has a mechanical, survivorship-free, multi-decade
record, which is exactly the class of evidence CLAUDE.md rule 4 demands and the
videos themselves cannot supply.

THE BENCHMARK CHOICE IS THE WHOLE ANALYSIS, AND IT IS EASY TO GET WRONG.

BXM and PUT are TOTAL-RETURN strategy indices: the covered-call writer owns the
index and collects its dividends, and the put writer earns interest on the
collateral. Comparing either against ^GSPC — the S&P 500 PRICE index, which
excludes dividends — flatters them by roughly the whole dividend yield, ~1.8%/yr
over this window. The correct benchmark is ^SP500TR. Both are fetched here
precisely so the difference can be shown rather than assumed, because the
"BXM returned ~1/3 of the S&P" figure this repo has been carrying is exactly
the kind of claim whose benchmark was never stated.

DATA. Yahoo's chart API, same path as scripts/fetch_history.py. Verified
2026-08-01:

    ^BXM       9,603 daily closes  1988-06-01 .. 2026-07-31
    ^PUT       7,539 daily closes  1996-08-02 .. 2026-07-31
    ^SP500TR                       1988-01-04 ..            (matches BXM's start)
    ^GSPC                          1970-01-02 ..            (price only; the trap)

    ^BXMD and ^CLL return a single bar and are NOT available. Do not add them.
    Note ^BXM has no history at interval=1mo either — it must be pulled daily
    and resampled, which is why this script only ever requests 1d.

    py scripts/fetch_cboe_benchmarks.py
    py scripts/fetch_cboe_benchmarks.py --list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research_data", "external", "cboe")

CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1=0&period2=9999999999&interval=1d")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

SERIES = {
    "BXM": ("^BXM", "CBOE S&P 500 BuyWrite — the covered-call half of the wheel"),
    "PUT": ("^PUT", "CBOE S&P 500 PutWrite — the cash-secured-put half"),
    "SP500TR": ("^SP500TR", "S&P 500 TOTAL RETURN — the correct benchmark"),
    "GSPC": ("^GSPC", "S&P 500 PRICE index — kept only to show the ~1.8%/yr "
                      "dividend gap a price benchmark hides"),
}

# A series that silently arrives with a handful of rows is the failure mode
# here: it does not raise, it just produces a short, wrong-looking history.
MIN_ROWS = 2000


def fetch_one(sym: str, timeout: int = 30) -> pd.DataFrame:
    r = requests.get(CHART.format(sym=sym), headers={"User-Agent": UA},
                     timeout=timeout)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result")
    if not res:
        raise RuntimeError(f"{sym}: no chart result "
                           f"({(r.json().get('chart') or {}).get('error')})")
    node = res[0]
    ts = node.get("timestamp") or []
    q = node["indicators"]["quote"][0]
    df = pd.DataFrame({
        "date": pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize(),
        "close": q.get("close"),
    }).dropna()
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if len(df) < MIN_ROWS:
        raise RuntimeError(f"{sym}: only {len(df)} rows — Yahoo serves some of "
                           f"these indices as a quote with no history "
                           f"(^BXMD and ^CLL both do). Refusing to write a "
                           f"stub that would later read as a real sample.")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, (sym, why) in SERIES.items():
            print(f"{name:<9} {sym:<10} {why}")
        return

    os.makedirs(OUT, exist_ok=True)
    manifest = {}
    for name, (sym, why) in SERIES.items():
        df = fetch_one(sym)
        path = os.path.join(OUT, f"{name}.csv")
        df.to_csv(path, index=False)
        raw = open(path, "rb").read()
        manifest[name] = {
            "yahoo_symbol": sym, "what": why, "path": f"{name}.csv",
            "rows": len(df), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "start": str(df["date"].iloc[0].date()),
            "end": str(df["date"].iloc[-1].date()),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        print(f"{name:<9} {len(df):>6} rows  "
              f"{manifest[name]['start']} .. {manifest[name]['end']}")

    manifest["_note"] = (
        "BXM and PUT are TOTAL-RETURN strategy indices. Benchmark them against "
        "SP500TR, never GSPC — the price index omits ~1.8%/yr of dividends the "
        "covered-call writer actually receives.")
    with open(os.path.join(OUT, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    print(f"\nwrote {OUT}")
    print("next: py -m rhengine.alpha.buywrite")


if __name__ == "__main__":
    main()
