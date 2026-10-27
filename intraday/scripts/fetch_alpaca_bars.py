"""Download one-minute bars from Alpaca's market-data API (SIP feed) to CSV.

    python intraday/scripts/fetch_alpaca_bars.py SPY 2016-01-01 2026-10-26

Writes intraday/data/<SYMBOL>_1m.csv (gitignored) with columns
ts (UTC bar open), open, high, low, close, volume. Split-adjusted, extended
hours included; `data.rth()` cuts to the session. Free Alpaca plans allow SIP
history older than 15 minutes. Keys come from secrets.json via the repo's own
loader and are never printed.

Lives outside the package on purpose: the `intraday` package itself makes no
network calls (a test bans `requests` there).
"""
import csv
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from rhengine.market_data import _get_credentials  # noqa: E402

URL = "https://data.alpaca.markets/v2/stocks/bars"


def iter_bars(symbol: str, start: str, end: str):
    """Yield (ts, open, high, low, close, volume) rows, oldest first."""
    key, secret = _get_credentials()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    params = {"symbols": symbol, "timeframe": "1Min", "start": start, "end": end,
              "feed": "sip", "adjustment": "split", "limit": 10000, "sort": "asc"}
    while True:
        for attempt in range(5):
            try:
                r = requests.get(URL, params=params, headers=headers, timeout=60)
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            break
        if not r.ok:
            raise SystemExit(f"HTTP {r.status_code}: {r.text[:300]}")
        js = r.json()
        for b in js.get("bars", {}).get(symbol, []):
            yield b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]
        tok = js.get("next_page_token")
        if not tok:
            return
        params["page_token"] = tok


def main(symbol: str, start: str, end: str) -> None:
    out = os.path.join(ROOT, "intraday", "data", f"{symbol}_1m.csv")
    n = 0
    with open(out + ".part", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for row in iter_bars(symbol, start, end):
            w.writerow(row)
            n += 1
            if n % 200_000 == 0:
                print(f"{n:,} bars, last {row[0]}", flush=True)
    os.replace(out + ".part", out)
    print(f"wrote {n:,} bars -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
