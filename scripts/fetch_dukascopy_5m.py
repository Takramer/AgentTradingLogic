"""Download Dukascopy USA500 (S&P 500 index CFD) ticks and store 5-minute mid bars.

The Triple HTF Bollinger script is a 5-minute chart strategy. Robinhood's
5-minute SPY history is real only from about April 2026; everything older comes
back interpolated at a constant price. Dukascopy's USA500IDXUSD feed is the
24-hour index this session filter was written for, and it reaches 2016.

Month in the URL is zero-based. Prices are integers with point 0.001.
Bid and ask are both in each tick; the stored close is the mid, and `spread`
is the last ask-bid of the bar in index points.

    py scripts/fetch_dukascopy_5m.py
"""
from __future__ import annotations

import argparse
import json
import lzma
import struct
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

POINT = 1000.0
URL = "https://www.dukascopy.com/datafeed/USA500IDXUSD/{y}/{m0:02d}/{d:02d}/{h:02d}h_ticks.bi5"
OUT = Path("/tmp/usa500_5m")
REC = struct.Struct(">IIIff")


def hours(start: datetime, end: datetime):
    t = start
    while t <= end:
        # Index is closed Saturday and most of Sunday. Sunday reopen is ~22:00 UTC.
        if t.weekday() == 5 or (t.weekday() == 6 and t.hour < 22):
            t += timedelta(hours=1)
            continue
        yield t
        t += timedelta(hours=1)


_local = threading.local()
_backoff_until = 0.0
_backoff_lock = threading.Lock()


def _opener():
    """One connection pool per worker. The first version opened a socket per
    hour and leaked CLOSE_WAIT until every worker sat in poll."""
    op = getattr(_local, "opener", None)
    if op is None:
        op = urllib.request.build_opener()
        _local.opener = op
    return op


def fetch_hour(hour: datetime, retries: int = 3):
    """Return (hour, rows, ok). `ok` is False only when every attempt timed out,
    so the caller can leave that hour pending instead of punching a hole."""
    global _backoff_until
    url = URL.format(y=hour.year, m0=hour.month - 1, d=hour.day, h=hour.hour)
    last = None
    for attempt in range(retries):
        wait = _backoff_until - time.time()
        if wait > 0:
            time.sleep(min(wait, 20))
        time.sleep(0.25)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                       "Connection": "close"})
            with _opener().open(req, timeout=15) as r:
                raw = r.read()
            if not raw:
                return hour, [], True
            data = lzma.decompress(raw)
            return hour, bars_from_ticks(hour, data), True
        except urllib.error.HTTPError as e:
            # 404 is a closed hour. 503 is the rate limit; marking it done
            # punches a hole in a week that actually traded.
            if e.code == 404:
                return hour, [], True
            if e.code == 503:
                with _backoff_lock:
                    _backoff_until = time.time() + 15
            last = e
        except Exception as e:  # noqa: BLE001 — network is the failure mode
            last = e
        time.sleep(0.3 * (attempt + 1))
    print(f"miss {hour:%Y-%m-%dT%H} {last}", flush=True)
    return hour, [], False


def bars_from_ticks(hour: datetime, data: bytes):
    buckets: dict[datetime, list] = {}
    for i in range(0, len(data) - len(data) % 20, 20):
        ms, ask_i, bid_i, _av, _bv = REC.unpack_from(data, i)
        if ms > 3_700_000:
            continue
        bid = bid_i / POINT
        ask = ask_i / POINT
        mid = (bid + ask) / 2.0
        ts = hour + timedelta(milliseconds=int(ms))
        key = ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
        b = buckets.get(key)
        if b is None:
            buckets[key] = [mid, mid, mid, mid, ask - bid, 1]
        else:
            if mid > b[1]:
                b[1] = mid
            if mid < b[2]:
                b[2] = mid
            b[3] = mid
            b[4] = ask - bid
            b[5] += 1
    rows = []
    for ts, (o, h, l, c, spr, n) in buckets.items():
        rows.append((ts, o, h, l, c, spr, n))
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2016-10-01")
    p.add_argument("--end", default="2026-10-06T23:00:00")
    p.add_argument("--workers", type=int, default=16)
    args = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    done_path = OUT / "done_hours.json"
    done = set(json.loads(done_path.read_text())) if done_path.exists() else set()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    pending = [h for h in hours(start, end) if h.strftime("%Y-%m-%dT%H") not in done]
    # Recent years first. A sustained pull gets throttled, and the windows
    # that end in 2026 are the ones the backtest cannot start without.
    pending.reverse()
    print(f"pending hours {len(pending)}  already done {len(done)}", flush=True)
    bars_path = OUT / "bars.parquet"
    frames = []
    if bars_path.exists():
        frames.append(pd.read_parquet(bars_path))
    batch = []
    t0 = time.time()
    n = 0
    # Submit in blocks. One 60k-future pile left sockets in CLOSE_WAIT and
    # the pool stopped completing.
    block = max(args.workers * 40, 200)
    for i0 in range(0, len(pending), block):
        chunk = pending[i0:i0 + block]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(fetch_hour, h) for h in chunk]
            for fut in as_completed(futs):
                hour, rows, ok = fut.result()
                if ok:
                    done.add(hour.strftime("%Y-%m-%dT%H"))
                if rows:
                    batch.extend(rows)
                n += 1
        if batch:
            df = pd.DataFrame(batch, columns=["ts", "open", "high", "low", "close", "spread", "n"])
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            frames.append(df)
            batch = []
            allb = pd.concat(frames, ignore_index=True)
            allb = allb.drop_duplicates("ts").sort_values("ts")
            frames = [allb]
            allb.to_parquet(bars_path, index=False)
        done_path.write_text(json.dumps(sorted(done)))
        elapsed = time.time() - t0
        rate = n / elapsed if elapsed else 0
        left = (len(pending) - n) / rate if rate else 0
        print(f"{n}/{len(pending)}  {rate:.1f} hr/s  eta {left/60:.1f} min  "
              f"bars {len(frames[0]) if frames else 0}", flush=True)
    print("wrote", bars_path, flush=True)


if __name__ == "__main__":
    main()
