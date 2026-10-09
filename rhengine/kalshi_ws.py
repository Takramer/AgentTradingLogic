"""
kalshi_ws.py — authenticated Kalshi WebSocket: live order books. READ ONLY.

Everything else in this repo polls REST. That is fine for a temperature ladder
that resolves tomorrow and hopeless for an hourly BTC ladder or a 15-minute
up/down, where the book moves faster than a poll loop can see. This is the fix.

Places no orders and has no code path that could. The `cmd` values it sends are
`subscribe` and `unsubscribe`, nothing else.

Three things that are easy to get wrong, all verified against the live feed on
2026-08-22:

**1. The signed path excludes the query string, and it is the WS path.**
Authentication is the same RSA-PSS-SHA256 scheme as the REST API
(`market_data_kalshi.kalshi_signed_headers`), signed over
`timestamp + "GET" + "/trade-api/ws/v2"`.

**2. This Python's stdlib `ssl` has no CA bundle.** `requests` bundles certifi
so REST works, but `websockets` uses the stdlib context and fails with
CERTIFICATE_VERIFY_FAILED. An explicit certifi context is required, and its
absence looks exactly like a network outage.

**3. Field names are not what the docs say.** The live payloads are:

    orderbook_snapshot  msg.yes_dollars_fp / msg.no_dollars_fp
                        -> [[price_str, size_str], ...]
    orderbook_delta     msg.price_dollars, msg.delta_fp, msg.side, msg.ts_ms

Reading `msg.price` / `msg.delta` (the documented names) yields None on every
message and a book that silently never updates.

Sequence numbers, and the bug that eats order books
---------------------------------------------------
Every message carries `seq`, per subscription. Deltas are increments against
the snapshot, so ONE dropped message leaves a book that is wrong for as long as
it stays connected and gives no error while it does. `Book.apply()` returns
False on a sequence gap and `stream()` re-subscribes when that happens rather
than carrying on with a corrupted book.

Both sides are quoted in their own terms: a `no` level at 0.92 is a NO order at
92 cents, equivalent to a YES bid at 8 cents. `Book.best()` converts so the two
sides can be compared.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import ssl
import sys

from .market_data_kalshi import kalshi_signed_headers

WS_PATH = "/trade-api/ws/v2"
WS_HOSTS = ["wss://api.elections.kalshi.com" + WS_PATH,
            "wss://external-api.kalshi.com" + WS_PATH]

#: Confirmed against the live feed. `ticker_v2` and `trade` were rejected with
#: "Unknown channel name" (code 8) on this account, so they are not offered
#: here rather than being listed and failing at runtime.
CHANNELS = ("orderbook_delta",)


def _ssl_context():
    """certifi-backed context. The stdlib default has no roots on this install."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _header_kwarg():
    """websockets renamed extra_headers -> additional_headers in v13."""
    import websockets
    params = inspect.signature(websockets.connect).parameters
    return "additional_headers" if "additional_headers" in params else "extra_headers"


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


class Book:
    """One market's two-sided book, maintained from a snapshot plus deltas."""

    def __init__(self, ticker):
        self.ticker = ticker
        self.yes = {}          # price -> size, YES orders
        self.no = {}           # price -> size, NO orders
        self.seq = None
        self.ts_ms = None

    def snapshot(self, msg, seq=None):
        self.yes = {p: s for p, s in
                    ((_f(a), _f(b)) for a, b in (msg.get("yes_dollars_fp") or []))
                    if p is not None and s}
        self.no = {p: s for p, s in
                   ((_f(a), _f(b)) for a, b in (msg.get("no_dollars_fp") or []))
                   if p is not None and s}
        self.seq = seq
        self.ts_ms = msg.get("ts_ms")

    def apply(self, msg, seq=None):
        """Apply one delta. Returns False on a sequence gap — the book is now
        untrustworthy and the caller must re-subscribe."""
        if seq is not None and self.seq is not None and seq != self.seq + 1:
            return False
        price, delta = _f(msg.get("price_dollars")), _f(msg.get("delta_fp"))
        side = msg.get("side")
        book = self.yes if side == "yes" else self.no if side == "no" else None
        if book is None or price is None or delta is None:
            self.seq = seq if seq is not None else self.seq
            return True
        book[price] = book.get(price, 0.0) + delta
        if book[price] <= 0:
            book.pop(price, None)
        self.seq = seq if seq is not None else self.seq
        self.ts_ms = msg.get("ts_ms", self.ts_ms)
        return True

    def best(self):
        """{yes_bid, yes_ask, sizes} in YES terms.

        A NO order at price p is a YES order at 1-p on the other side, so the
        best YES ask is 1 minus the best NO bid. Comparing the raw sides
        without converting reads the spread backwards.
        """
        yb = max(self.yes) if self.yes else None
        nb = max(self.no) if self.no else None
        ya = None if nb is None else round(1.0 - nb, 4)
        return {"ticker": self.ticker,
                "yes_bid": yb, "yes_bid_size": self.yes.get(yb) if yb else None,
                "yes_ask": ya, "yes_ask_size": self.no.get(nb) if nb else None,
                "spread": None if (yb is None or ya is None) else round(ya - yb, 4),
                "seq": self.seq, "ts_ms": self.ts_ms}


async def stream(tickers, on_book=None, channels=CHANNELS, max_messages=None):
    """Maintain live books for `tickers`, calling `on_book(Book)` on each change.

    Re-subscribes on a sequence gap rather than serving a corrupted book.
    Returns the {ticker: Book} map when it stops.
    """
    import websockets
    books, kw, ctx = {t: Book(t) for t in tickers}, _header_kwarg(), _ssl_context()
    last_err = None
    for host in WS_HOSTS:
        try:
            headers = kalshi_signed_headers("GET", WS_PATH)
            async with websockets.connect(host, ssl=ctx, open_timeout=15,
                                          close_timeout=5, **{kw: headers}) as ws:
                sub_id = 1
                await ws.send(json.dumps({"id": sub_id, "cmd": "subscribe",
                                          "params": {"channels": list(channels),
                                                     "market_tickers": list(tickers)}}))
                seen = 0
                while max_messages is None or seen < max_messages:
                    raw = await ws.recv()
                    seen += 1
                    d = json.loads(raw)
                    kind, msg, seq = d.get("type"), d.get("msg") or {}, d.get("seq")
                    tk = msg.get("market_ticker")
                    if kind == "orderbook_snapshot" and tk in books:
                        books[tk].snapshot(msg, seq)
                    elif kind == "orderbook_delta" and tk in books:
                        if not books[tk].apply(msg, seq):
                            sub_id += 1
                            await ws.send(json.dumps(
                                {"id": sub_id, "cmd": "subscribe",
                                 "params": {"channels": list(channels),
                                            "market_tickers": [tk]}}))
                            continue
                    elif kind == "error":
                        last_err = msg
                        continue
                    else:
                        continue
                    if on_book:
                        on_book(books[tk])
                return books
        except Exception as e:                      # try the other host
            last_err = "{}: {}".format(type(e).__name__, e)
    raise ConnectionError("Kalshi WS failed: {}".format(last_err))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stream live Kalshi order books. Read only.")
    ap.add_argument("--series", default="KXBTCD", help="pick the top markets from this series")
    ap.add_argument("--tickers", help="comma-separated market tickers instead")
    ap.add_argument("--n", type=int, default=4, help="markets to watch from --series")
    ap.add_argument("--messages", type=int, default=30, help="stop after N messages")
    a = ap.parse_args(argv)

    if a.tickers:
        tickers = a.tickers.split(",")
    else:
        from .market_data_kalshi import _kalshi_get
        body = _kalshi_get("/markets", params={"limit": 300, "status": "open",
                                               "series_ticker": a.series})
        ranked = sorted(body.get("markets", []),
                        key=lambda m: -float(m.get("volume_24h_fp") or 0))
        tickers = [m["ticker"] for m in ranked[:a.n]]
    print("streaming {} markets, {} messages".format(len(tickers), a.messages))

    def show(book):
        b = book.best()
        print("  {:<30} bid {:>7} x{:<10} ask {:>7} x{:<10} spread {:<7} seq {}".format(
            b["ticker"][:30],
            "-" if b["yes_bid"] is None else "{:.2f}".format(b["yes_bid"]),
            "-" if b["yes_bid_size"] is None else "{:,.0f}".format(b["yes_bid_size"]),
            "-" if b["yes_ask"] is None else "{:.2f}".format(b["yes_ask"]),
            "-" if b["yes_ask_size"] is None else "{:,.0f}".format(b["yes_ask_size"]),
            "-" if b["spread"] is None else "{:.2f}".format(b["spread"]), b["seq"]))

    asyncio.run(stream(tickers, on_book=show, max_messages=a.messages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
