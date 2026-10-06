"""Public Polymarket reads for the paper runner.

Gamma discovers the current up/down contract. The CLOB midpoint is the
contract price. The crypto-price endpoint supplies the session open.
Chainlink's latest print comes from Polymarket's public live socket; that
socket sends one snapshot and then stays quiet, so the feed resubscribes.
Coinbase spot, then Binance spot, fill in when the socket print is older
than feedMaxAgeMs. No API key. No order endpoint.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from polymarket_paper.settings import (
    BINANCE_SYMBOL,
    CHAINLINK_SYMBOL,
    COINBASE_PAIR,
    SLUG_PREFIX,
    TIMEFRAMES,
)

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
CLOB_MIDPOINT = "https://clob.polymarket.com/midpoint"
CRYPTO_PRICE = "https://polymarket.com/api/crypto/crypto-price"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price"
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/{pair}/spot"
LIVE_WS_HOST = "ws-live-data.polymarket.com"
EASTERN = ZoneInfo("America/New_York")

MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Cache-Control": "no-cache, no-store",
    "Pragma": "no-cache",
}


def window_bounds(now: float, seconds: int) -> tuple[int, int]:
    start = int(now // seconds) * seconds
    return start, start + seconds


def market_slug(asset: str, timeframe: str, start_sec: int) -> str:
    prefix = SLUG_PREFIX[timeframe][asset]
    if timeframe != "1H":
        return f"{prefix}-{int(start_sec)}"
    when = datetime.fromtimestamp(int(start_sec), EASTERN)
    hour24 = when.hour
    period = "pm" if hour24 >= 12 else "am"
    hour12 = hour24 % 12 or 12
    return (
        f"{prefix}-{MONTHS[when.month - 1]}-{when.day}-{when.year}-"
        f"{hour12}{period}-et"
    )


def iso_utc(sec: int) -> str:
    return datetime.fromtimestamp(int(sec), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def crypto_price_url(asset: str, variant: str, start_sec: int, end_sec: int) -> str:
    query = (
        f"symbol={quote(asset, safe='')}"
        f"&eventStartTime={quote(iso_utc(start_sec), safe='')}"
        f"&variant={quote(variant, safe='')}"
        f"&endDate={quote(iso_utc(end_sec), safe='')}"
    )
    return f"{CRYPTO_PRICE}?{query}"


def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_gamma_market(body) -> dict | None:
    """First market on a Gamma event, with Up/Down token ids as strings."""
    if isinstance(body, dict):
        events = [body]
    elif isinstance(body, list):
        events = body
    else:
        return None
    if not events or not isinstance(events[0], dict):
        return None
    markets = events[0].get("markets") or []
    if not markets or not isinstance(markets[0], dict):
        return None
    market = markets[0]
    names = [str(name) for name in _as_list(market.get("outcomes"))]
    token_ids = [str(token) for token in _as_list(market.get("clobTokenIds"))]
    up = down = None
    for name, token_id in zip(names, token_ids):
        if name.lower() == "up":
            up = token_id
        elif name.lower() == "down":
            down = token_id
    if (up is None or down is None) and len(token_ids) >= 2:
        up = up or token_ids[0]
        down = down or token_ids[1]
    if not up or not down:
        return None
    return {
        "up_token_id": up,
        "down_token_id": down,
        "condition_id": str(market.get("conditionId") or ""),
        "slug": str(market.get("slug") or events[0].get("slug") or ""),
    }


def parse_mid(body) -> float | None:
    if not isinstance(body, dict) or body.get("mid") in (None, ""):
        return None
    try:
        price = float(body["mid"])
    except (TypeError, ValueError):
        return None
    if 0 <= price <= 1:
        return price
    return None


def parse_open_price(body) -> float | None:
    if not isinstance(body, dict) or body.get("openPrice") in (None, ""):
        return None
    try:
        price = float(body["openPrice"])
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def parse_spot_price(body) -> float | None:
    """Binance ticker or Coinbase spot payload."""
    if not isinstance(body, dict):
        return None
    raw = body.get("price")
    if raw in (None, "") and isinstance(body.get("data"), dict):
        raw = body["data"].get("amount")
    if raw in (None, ""):
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def prices_from_message(message: dict) -> list[tuple[str, float]]:
    """Chainlink/Binance prints inside one live-socket JSON object."""
    if not isinstance(message, dict):
        return []
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return []
    symbol = str(payload.get("symbol") or "")
    points = payload.get("data")
    if isinstance(points, list) and points:
        last = points[-1]
        if isinstance(last, dict) and last.get("value") not in (None, "") and symbol:
            try:
                return [(symbol, float(last["value"]))]
            except (TypeError, ValueError):
                return []
    if symbol and payload.get("value") not in (None, ""):
        try:
            return [(symbol, float(payload["value"]))]
        except (TypeError, ValueError):
            return []
    return []


class WsBuffer:
    """Reassemble websocket frames, including payloads split across reads."""

    def __init__(self):
        self.buf = b""
        self._frag = b""
        self._frag_op = None

    def feed(self, data: bytes) -> list[tuple[str, bytes]]:
        self.buf += data
        out = []
        while True:
            frame = self._pop()
            if frame is None:
                break
            opcode, payload, fin = frame
            if opcode == 0x0:
                self._frag += payload
                if fin and self._frag_op == 0x1:
                    out.append(("text", self._frag))
                    self._frag = b""
                    self._frag_op = None
                continue
            if opcode == 0x1:
                if fin:
                    out.append(("text", payload))
                else:
                    self._frag_op = opcode
                    self._frag = payload
                continue
            if opcode == 0x8:
                out.append(("close", payload))
            elif opcode == 0x9:
                out.append(("ping", payload))
            elif opcode == 0xA:
                out.append(("pong", payload))
        return out

    def _pop(self):
        buf = self.buf
        if len(buf) < 2:
            return None
        first, second = buf[0], buf[1]
        opcode = first & 0x0F
        fin = bool(first & 0x80)
        masked = bool(second & 0x80)
        length = second & 0x7F
        index = 2
        if length == 126:
            if len(buf) < 4:
                return None
            length = struct.unpack("!H", buf[2:4])[0]
            index = 4
        elif length == 127:
            if len(buf) < 10:
                return None
            length = struct.unpack("!Q", buf[2:10])[0]
            index = 10
        mask_len = 4 if masked else 0
        if len(buf) < index + mask_len + length:
            return None
        mask = None
        if masked:
            mask = buf[index:index + 4]
            index += 4
        payload = buf[index:index + length]
        self.buf = buf[index + length:]
        if mask is not None:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload, fin


def encode_client_text(text: str) -> bytes:
    payload = text.encode()
    mask = os.urandom(4)
    length = len(payload)
    header = bytearray([0x81])
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return bytes(header) + masked


def default_http(url: str, timeout: float):
    request = urllib.request.Request(url, headers=_HTTP_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


@dataclass
class Snapshot:
    up: float
    down: float
    minutes_left: float
    start_sec: int
    end_sec: int
    market_ticker: str
    up_token_id: str
    down_token_id: str
    condition_id: str

    @property
    def session_key(self) -> str:
        return self.market_ticker or str(self.start_sec)


@dataclass
class _CachedMarket:
    slug: str
    start_sec: int
    end_sec: int
    up_token_id: str
    down_token_id: str
    condition_id: str
    up: float
    down: float
    fetched_mono: float

    def snapshot(self, now: float) -> Snapshot:
        return Snapshot(
            up=self.up,
            down=self.down,
            minutes_left=(self.end_sec - now) / 60.0,
            start_sec=self.start_sec,
            end_sec=self.end_sec,
            market_ticker=self.slug,
            up_token_id=self.up_token_id,
            down_token_id=self.down_token_id,
            condition_id=self.condition_id,
        )


class MarketFeed:
    """Gamma + CLOB + open price, plus a background Chainlink cache."""

    def __init__(self, settings, http=None, mono=None):
        self.settings = settings
        self.http = http or default_http
        self.mono = mono or time.monotonic
        self.timeout_s = max(0.2, settings.http_timeout_ms / 1000.0)
        self.meta: dict[str, _CachedMarket] = {}
        self.prices: dict[str, tuple[float, float, str]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_socket, name="polymarket-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def note_price(self, symbol: str, value: float, source: str) -> None:
        with self._lock:
            self.prices[symbol] = (float(value), self.mono(), source)

    def snapshot(self, asset: str, now: float) -> Snapshot | None:
        seconds = TIMEFRAMES[self.settings.timeframe]["seconds"]
        start, end = window_bounds(now, seconds)
        if (end - now) / 60.0 < 0.5:
            self.meta.pop(asset, None)
            return None
        slug = market_slug(asset, self.settings.timeframe, start)
        cached = self.meta.get(asset)
        age_ms = None if cached is None else (self.mono() - cached.fetched_mono) * 1000
        if cached is not None and cached.slug == slug and age_ms is not None and age_ms < self.settings.market_refresh_ms:
            if self._refresh_mids(cached):
                return cached.snapshot(now)
            return None
        fresh = self._load_market(asset, slug, start, end)
        if fresh is not None:
            return fresh.snapshot(now)
        if cached is not None and cached.slug == slug and self._refresh_mids(cached):
            return cached.snapshot(now)
        return None

    def open_price(self, asset: str, snap: Snapshot) -> float | None:
        url = crypto_price_url(
            asset, self.settings.price_variant, snap.start_sec, snap.end_sec,
        )
        return parse_open_price(self.http(url, self.timeout_s))

    def live_price(self, asset: str) -> tuple[float, str] | None:
        """(price, source) or None. Socket prints win while they are fresh."""
        max_age = self.settings.feed_max_age_ms / 1000.0
        now_mono = self.mono()
        for symbol in self._preferred_symbols(asset):
            hit = self._cached(symbol, now_mono, max_age)
            if hit is not None:
                return hit
        recent = self._cached(COINBASE_PAIR[asset], now_mono, 5)
        if recent is not None:
            return recent
        spot = self._http_spot(asset)
        if spot is not None:
            return spot
        return None

    def _preferred_symbols(self, asset: str) -> list[str]:
        chainlink = CHAINLINK_SYMBOL[asset]
        binance = BINANCE_SYMBOL[asset]
        if self.settings.timeframe == "1H":
            return [binance, chainlink]
        return [chainlink, binance]

    def _cached(self, symbol: str, now_mono: float, max_age: float):
        with self._lock:
            row = self.prices.get(symbol)
        if row is None:
            return None
        value, stamped, source = row
        if now_mono - stamped <= max_age:
            return value, source
        return None

    def _http_spot(self, asset: str):
        if self.settings.timeframe == "1H":
            binance = self._binance(asset)
            if binance is not None:
                return binance
        coinbase = self._coinbase(asset)
        if coinbase is not None:
            return coinbase
        if self.settings.timeframe != "1H":
            return self._binance(asset)
        return None

    def _binance(self, asset: str):
        symbol = BINANCE_SYMBOL[asset]
        body = self.http(f"{BINANCE_TICKER}?symbol={symbol.upper()}", self.timeout_s)
        price = parse_spot_price(body)
        if price is None:
            return None
        self.note_price(symbol, price, "binance")
        return price, "binance"

    def _coinbase(self, asset: str):
        pair = COINBASE_PAIR[asset]
        body = self.http(COINBASE_SPOT.format(pair=pair), self.timeout_s)
        price = parse_spot_price(body)
        if price is None:
            return None
        self.note_price(COINBASE_PAIR[asset], price, "coinbase")
        return price, "coinbase"

    def _load_market(self, asset: str, slug: str, start: int, end: int):
        url = f"{GAMMA_EVENTS}?slug={quote(slug, safe='')}&_={int(self.mono() * 1000)}"
        parsed = parse_gamma_market(self.http(url, self.timeout_s))
        if parsed is None:
            return None
        cached = _CachedMarket(
            slug=parsed["slug"] or slug,
            start_sec=start,
            end_sec=end,
            up_token_id=parsed["up_token_id"],
            down_token_id=parsed["down_token_id"],
            condition_id=parsed["condition_id"],
            up=0.0,
            down=0.0,
            fetched_mono=self.mono(),
        )
        if not self._refresh_mids(cached):
            return None
        self.meta[asset] = cached
        return cached

    def _refresh_mids(self, cached: _CachedMarket) -> bool:
        up = parse_mid(self.http(f"{CLOB_MIDPOINT}?token_id={cached.up_token_id}", self.timeout_s))
        down = parse_mid(self.http(f"{CLOB_MIDPOINT}?token_id={cached.down_token_id}", self.timeout_s))
        if up is None or down is None:
            return False
        cached.up = up
        cached.down = down
        return True

    def _subscribe_payload(self) -> str:
        subscriptions = []
        for asset in self.settings.assets:
            subscriptions.append({
                "topic": "crypto_prices_chainlink",
                "type": "*",
                "filters": json.dumps({"symbol": CHAINLINK_SYMBOL[asset]}),
            })
            subscriptions.append({
                "topic": "crypto_prices",
                "type": "*",
                "filters": json.dumps({"symbol": BINANCE_SYMBOL[asset]}),
            })
        return json.dumps({"action": "subscribe", "subscriptions": subscriptions})

    def _run_socket(self) -> None:
        while not self._stop.is_set():
            try:
                self._session()
            except OSError:
                pass
            if self._stop.wait(2):
                return

    def _session(self) -> None:
        raw = socket.create_connection((LIVE_WS_HOST, 443), timeout=10)
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw, server_hostname=LIVE_WS_HOST)
        self._sock = sock
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            handshake = (
                f"GET / HTTP/1.1\r\nHost: {LIVE_WS_HOST}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(handshake.encode())
            prelude = b""
            while b"\r\n\r\n" not in prelude:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                prelude += chunk
            if b" 101 " not in prelude.split(b"\r\n", 1)[0]:
                return
            _, pending = prelude.split(b"\r\n\r\n", 1)
            sock.settimeout(1.0)
            buffer = WsBuffer()
            buffer.buf = pending
            payload = self._subscribe_payload()
            sock.sendall(encode_client_text(payload))
            last_sub = time.monotonic()
            while not self._stop.is_set():
                if time.monotonic() - last_sub >= 3:
                    sock.sendall(encode_client_text(payload))
                    last_sub = time.monotonic()
                try:
                    chunk = sock.recv(1 << 20)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                for kind, frame in buffer.feed(chunk):
                    if kind == "ping":
                        sock.sendall(_pong_frame(frame))
                    elif kind == "close":
                        return
                    elif kind == "text" and frame:
                        self._ingest_text(frame)
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def _ingest_text(self, frame: bytes) -> None:
        try:
            message = json.loads(frame.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        for symbol, value in prices_from_message(message):
            source = "chainlink" if "/" in symbol else "binance"
            self.note_price(symbol, value, source)


def _pong_frame(payload: bytes) -> bytes:
    mask = os.urandom(4)
    header = bytearray([0x8A, 0x80 | len(payload)])
    header.extend(mask)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return bytes(header) + masked
