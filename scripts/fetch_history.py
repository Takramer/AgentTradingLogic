"""Bulk Yahoo daily-bar downloader for the long-history research dataset.

Fetches full-history OHLCV + adjusted close for every ticker that was EVER an
S&P 500 member (per research_data/sp500_pit_raw.csv), including names that have
since been acquired or delisted. Yahoo retains many but not all of those; the
misses are recorded in _coverage.json so survivorship bias can be measured
rather than assumed away.

    py scripts/fetch_history.py            # fetch/refresh everything missing
    py scripts/fetch_history.py --force    # re-fetch even if cached
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import time
import urllib.error
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "research_data")
OUT = os.path.join(DATA, "yahoo_long")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

CHART = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    "?period1=0&period2=9999999999&interval=1d&events=div%2Csplit"
)


def yahoo_symbol(t: str) -> str:
    """S&P constituent notation -> Yahoo notation (BRK.B -> BRK-B)."""
    return t.replace(".", "-").strip().upper()


def fetch_one(sym: str, timeout: int = 30) -> pd.DataFrame | None:
    req = urllib.request.Request(CHART.format(sym=sym), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    res = payload.get("chart", {}).get("result")
    if not res:
        return None
    res = res[0]
    ts = res.get("timestamp")
    if not ts:
        return None
    q = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(ts, unit="s", utc=True).tz_convert("America/New_York").date,
            "open": q.get("open"),
            "high": q.get("high"),
            "low": q.get("low"),
            "close": q.get("close"),
            "volume": q.get("volume"),
            "adjclose": adj if adj is not None else q.get("close"),
        }
    )
    df = df.dropna(subset=["close", "adjclose"])
    return df if len(df) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--extra", default="SPY,^GSPC,IWM,QQQ,^VIX,IEF,TLT,GLD")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    members = pd.read_csv(os.path.join(DATA, "sp500_pit_raw.csv"))
    universe: set[str] = set()
    for row in members["tickers"]:
        universe.update(t.strip() for t in str(row).split(","))
    universe |= {t for t in args.extra.split(",") if t}
    todo = sorted(universe)

    cov_path = os.path.join(DATA, "_coverage.json")
    coverage = {}
    if os.path.exists(cov_path) and not args.force:
        coverage = json.load(open(cov_path))

    ok = miss = skip = 0
    for i, tk in enumerate(todo, 1):
        sym = yahoo_symbol(tk)
        path = os.path.join(OUT, f"{sym}.csv.gz")
        if os.path.exists(path) and not args.force:
            skip += 1
            continue
        try:
            df = fetch_one(sym)
        except urllib.error.HTTPError as e:
            if e.code in (429, 999):
                time.sleep(10 + random.random() * 10)
                try:
                    df = fetch_one(sym)
                except Exception:
                    df = None
            else:
                df = None
        except Exception:
            df = None

        if df is None or df.empty:
            coverage[tk] = {"symbol": sym, "status": "missing", "n": 0}
            miss += 1
        else:
            df.to_csv(path, index=False, compression="gzip")
            coverage[tk] = {
                "symbol": sym,
                "status": "ok",
                "n": int(len(df)),
                "first": str(df["date"].iloc[0]),
                "last": str(df["date"].iloc[-1]),
            }
            ok += 1
        if i % 25 == 0:
            json.dump(coverage, open(cov_path, "w"), indent=0)
            print(f"[{i}/{len(todo)}] ok={ok} miss={miss} skip={skip}", flush=True)
        time.sleep(args.sleep)

    json.dump(coverage, open(cov_path, "w"), indent=0)
    print(f"DONE ok={ok} miss={miss} skip={skip} total={len(todo)}")


if __name__ == "__main__":
    main()
