"""
kalshi_paper.py — paper verification of the Kalshi v5 15-minute bot.

The AutoHotkey script this ports is the paper script from
https://code.processoverprofit.blog/docs/youtube-code/video-14-kalshi-v5/
It is written for Nightshark, a paid Windows host, build July 27, 2026
or newer. Log, LogStream, HttpGet, GetKalshiPrice and stopCode are
Nightshark runtime calls. They are not required.

The same page ships a live script with a different entry range
(0.80–0.93) and a -200 balance stop. This module does not place orders
and does not implement that script.

This module runs the same rules against Kalshi's public market-data API
and a public spot print (Coinbase, Kraken if Coinbase fails). It places no
orders and reads no API key.

Paper PnL from this process is a local cash account. It is not a
rhengine.alpha.book result and it is not an edge claim. The script's own
accounting is used: no exchange fees.

Differences from the Nightshark script, on purpose:
  * A blank side price is missing, not zero. An empty quote cannot stop
    a position out at 0.
  * A market with under 30 seconds left is never selected. The script
    skips those on retries 1-3 and can still take one on retry 4.
  * open15m is the contract's floor_strike (Kalshi's target price for the
    window). GetKalshiPrice is a host function and was not in the script.
    The live print is Coinbase (Kraken fallback), so Delta mixes that
    print with Kalshi's strike. Delta 0 only checks that both numbers exist.
"""

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .market_data_kalshi import KALSHI_HOSTS

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_PKG_DIR)
DEFAULT_CONFIG_PATH = os.path.join(_PKG_DIR, "kalshi_v5_paper.json")
DEFAULT_LOG_DIR = os.path.join(_REPO_ROOT, "research_data", "kalshi_paper")

# Series tickers from the Nightshark script. Public GET, no auth.
ASSET_SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
    "DOGE": "KXDOGE15M",
    "HYPE": "KXHYPE15M",
    "BNB": "KXBNB15M",
}

_COINBASE = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "DOGE": "DOGE-USD",
    "BNB": "BNB-USD",
    "HYPE": "HYPE-USD",
}
_KRAKEN = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
    "DOGE": "XDGUSD",
    "BNB": "BNBUSD",
}

_UA = {"User-Agent": "rhengine-kalshi-paper"}
_CLOSE_KEYS = (
    "close_time",
    "expected_expiration_time",
    "expiration_time",
    "settlement_time",
)


@dataclass(frozen=True)
class PaperConfig:
    initial_balance: float
    time_delay: float
    entry_min: float
    entry_max: float
    exit: float
    take_profit_pts: float
    delta: float
    assets: tuple
    order_size: float
    pings: int
    daily_profit_target: float
    daily_loss_target: float
    poll_interval_ms: int
    poll_jitter_max_ms: int
    market_refresh_ms: int
    price_refresh_ms: int


def _is_number(value):
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    text = str(value).strip()
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def _as_float(value):
    return float(str(value).strip())


def parse_config(raw):
    """Validate a settings dict. Returns (PaperConfig or None, error strings).

    Field names match the Nightshark script. Keys starting with '_' are notes.
    """
    if not isinstance(raw, dict):
        return None, ["ERROR: config must be a JSON object."]
    errors = []

    def need(name):
        if name not in raw:
            errors.append("ERROR: {} is missing.".format(name))
            return None
        return raw[name]

    initial = need("initialBalance")
    initial_ok = _is_number(initial) and _as_float(initial) > 0
    if initial is not None and not initial_ok:
        errors.append(
            "ERROR: initialBalance is '{}'. It needs to be a number greater "
            "than zero, for example 1000.".format(initial))

    entry = need("EntryRange")
    entry_min = entry_max = None
    if entry is not None and not (isinstance(entry, (list, tuple)) and len(entry) == 2):
        errors.append(
            "ERROR: EntryRange must be [Min, Max], for example [0.60, 0.70]. "
            "Both values need to be decimals between 0 and 1.")
    elif isinstance(entry, (list, tuple)) and len(entry) == 2:
        lo, hi = entry[0], entry[1]
        lo_ok = _is_number(lo) and 0 < _as_float(lo) < 1
        hi_ok = _is_number(hi) and 0 < _as_float(hi) < 1
        if not lo_ok:
            errors.append(
                "ERROR: EntryRange Min is '{}'. It needs to be a decimal "
                "between 0 and 1, for example 0.60.".format(lo))
        else:
            entry_min = _as_float(lo)
        if not hi_ok:
            errors.append(
                "ERROR: EntryRange Max is '{}'. It needs to be a decimal "
                "between 0 and 1, for example 0.70.".format(hi))
        else:
            entry_max = _as_float(hi)
        if lo_ok and hi_ok and entry_min > entry_max:
            errors.append(
                "ERROR: EntryRange Min {} is greater than Max {}. Put the "
                "smaller decimal first, for example [0.60, 0.70].".format(
                    entry_min, entry_max))

    exit_px = need("Exit")
    exit_ok = _is_number(exit_px) and 0 < _as_float(exit_px) < 1
    if exit_px is not None and not exit_ok:
        errors.append(
            "ERROR: Exit is '{}'. It needs to be a decimal between 0 and 1, "
            "for example 0.40.".format(exit_px))

    tp = need("TakeProfitPts")
    tp_ok = _is_number(tp) and 0 < _as_float(tp) <= 1
    if tp is not None and not tp_ok:
        errors.append(
            "ERROR: TakeProfitPts is '{}'. It needs to be greater than 0 and "
            "no more than 1, for example 0.10 or 1.".format(tp))

    delay = need("timeDelay")
    delay_ok = _is_number(delay) and 0 < _as_float(delay) <= 15
    if delay is not None and not delay_ok:
        errors.append(
            "ERROR: timeDelay is '{}'. It needs to be greater than zero and "
            "no more than 15 minutes.".format(delay))

    size = need("orderSize")
    size_ok = False
    if size is not None:
        size_text = str(size).strip()
        size_ok = bool(re.fullmatch(r"\d+(\.\d{1,2})?", size_text)) and _as_float(size_text) > 0
        if not size_ok:
            errors.append(
                "ERROR: orderSize is '{}'. Set it to a positive contract count "
                "with no more than two decimal places, for example 1 or 5.50.".format(size))

    profit = need("Daily_Profit_Target")
    profit_ok = _is_number(profit) and _as_float(profit) >= 0
    if profit is not None and not profit_ok:
        errors.append(
            "ERROR: Daily_Profit_Target is '{}'. Use a positive dollar amount, "
            "or 0 to disable it.".format(profit))

    loss = need("Daily_Loss_Target")
    loss_ok = _is_number(loss) and _as_float(loss) <= 0
    if loss is not None and not loss_ok:
        errors.append(
            "ERROR: Daily_Loss_Target is '{}'. Use a negative dollar amount, "
            "or 0 to disable it.".format(loss))

    pings = need("Pings")
    pings_ok = False
    if pings is not None:
        pings_text = str(pings).strip()
        pings_ok = pings_text.isdigit()
        if not pings_ok:
            errors.append(
                "ERROR: Pings is '{}'. Use a whole number of confirmation "
                "checks, for example 0, 1, or 2.".format(pings))

    delta = raw.get("Delta", 0)
    delta_ok = _is_number(delta)
    if not delta_ok:
        errors.append(
            "ERROR: Delta is '{}'. It needs to be a number, for example 0.".format(delta))

    assets = need("assets")
    asset_list = ()
    if assets is not None:
        if not isinstance(assets, (list, tuple)) or not assets:
            errors.append(
                "ERROR: assets must be a non-empty list, for example [\"BTC\"].")
        else:
            unknown = [a for a in assets if a not in ASSET_SERIES]
            if unknown:
                errors.append(
                    "ERROR: assets has {} which is not in the series map {}.".format(
                        unknown, ", ".join(sorted(ASSET_SERIES))))
            if len(assets) != len(set(assets)):
                errors.append("ERROR: assets lists the same name twice.")
            if not unknown and len(assets) == len(set(assets)):
                asset_list = tuple(assets)

    def optional_int(name, default, minimum):
        if name not in raw:
            return default
        value = raw[name]
        if isinstance(value, bool) or not _is_number(value):
            errors.append("ERROR: {} is '{}'. It needs to be a whole number.".format(
                name, value))
            return default
        number = int(_as_float(value))
        if number < minimum or abs(_as_float(value) - number) > 1e-9:
            errors.append("ERROR: {} is '{}'. It needs to be a whole number >= {}.".format(
                name, value, minimum))
            return default
        return number

    poll_ms = optional_int("pollIntervalMs", 700, 0)
    jitter_ms = optional_int("pollJitterMaxMs", 250, 0)
    market_ms = optional_int("marketRefreshMs", 5000, 0)
    price_ms = optional_int("priceRefreshMs", 1500, 0)

    if errors:
        return None, errors

    cfg = PaperConfig(
        initial_balance=_as_float(initial),
        time_delay=_as_float(delay),
        entry_min=entry_min,
        entry_max=entry_max,
        exit=_as_float(exit_px),
        take_profit_pts=_as_float(tp),
        delta=_as_float(delta),
        assets=asset_list,
        order_size=_as_float(size),
        pings=int(str(pings).strip()),
        daily_profit_target=_as_float(profit),
        daily_loss_target=_as_float(loss),
        poll_interval_ms=poll_ms,
        poll_jitter_max_ms=jitter_ms,
        market_refresh_ms=market_ms,
        price_refresh_ms=price_ms,
    )
    return cfg, []


def load_config(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    return parse_config(raw)


def parse_iso(iso):
    if not iso or not isinstance(iso, str):
        return None
    text = iso.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def minutes_until(iso, now):
    ts = parse_iso(iso)
    if ts is None or now is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (ts - now.astimezone(timezone.utc)).total_seconds() / 60.0


def minutes_remaining_in_quarter(now):
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    return 15 - (now.minute % 15) - (now.second + now.microsecond / 1e6) / 60.0


def quarter_index(now):
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    return (now.hour * 60 + now.minute) // 15


def minutes_left(snapshot, now):
    if snapshot:
        close = snapshot.get("close_time") or ""
        if close:
            mins = minutes_until(close, now)
            if mins is not None:
                return mins
        stored = snapshot.get("minutes_left")
        if stored is not None and stored != "":
            return float(stored)
    return minutes_remaining_in_quarter(now)


def market_clock(market, now):
    """Return (minutes_left or None, iso close string) from a market payload."""
    if not isinstance(market, dict):
        return None, ""
    for key in _CLOSE_KEYS:
        iso = market.get(key) or ""
        if not iso:
            continue
        mins = minutes_until(iso, now)
        if mins is not None:
            return mins, iso
    return None, ""


def dollar_price(market, dollar_field, cents_field=""):
    if not isinstance(market, dict):
        return None
    val = market.get(dollar_field)
    if val not in (None, ""):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    if cents_field:
        val = market.get(cents_field)
        if val not in (None, ""):
            try:
                return float(val) / 100.0
            except (TypeError, ValueError):
                return None
    return None


def _levels(body, field):
    if not isinstance(body, dict):
        return []
    containers = [body]
    for key in ("orderbook_fp", "orderbook"):
        nested = body.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        levels = container.get(field)
        if levels:
            return levels
    return []


def best_bid(body, dollar_field):
    """First level of a depth=1 book. Dollar strings stay dollars; a number
    above 1.5 is legacy cents."""
    levels = _levels(body, dollar_field)
    cents_field = "yes" if str(dollar_field).startswith("yes") else "no"
    if not levels:
        levels = _levels(body, cents_field)
        if not levels:
            return None
        try:
            raw = float(levels[0][0] if isinstance(levels[0], (list, tuple)) else levels[0])
        except (TypeError, ValueError, IndexError):
            return None
        return raw / 100.0 if raw > 1.5 else raw
    try:
        raw = float(levels[0][0] if isinstance(levels[0], (list, tuple)) else levels[0])
    except (TypeError, ValueError, IndexError):
        return None
    return raw / 100.0 if raw > 1.5 else raw


def apply_orderbook(snapshot, body):
    """UP ask = 1 - best NO bid. DOWN ask = 1 - best YES bid. Same as the script."""
    if not isinstance(snapshot, dict) or not snapshot.get("market_ticker"):
        return False
    no_bid = best_bid(body, "no_dollars")
    yes_bid = best_bid(body, "yes_dollars")
    updated = False
    if no_bid is not None:
        snapshot["up"] = round(1 - no_bid, 4)
        updated = True
    if yes_bid is not None:
        snapshot["down"] = round(1 - yes_bid, 4)
        updated = True
    return updated


def snapshot_from_market(market, now):
    mins, iso = market_clock(market, now)
    floor = market.get("floor_strike")
    try:
        floor = float(floor) if floor not in (None, "") else None
    except (TypeError, ValueError):
        floor = None
    return {
        "up": dollar_price(market, "yes_ask_dollars", "yes_ask"),
        "down": dollar_price(market, "no_ask_dollars", "no_ask"),
        "minutes_left": mins,
        "close_time": iso,
        "market_ticker": market.get("ticker") or "",
        "floor_strike": floor,
    }


def pick_market(markets, now, cutoff_minutes=0.5):
    """Soonest open market that is not inside the last `cutoff_minutes`.

    Returns the market dict, or None when every row is missing or too close
    to expiry. The script's retry loop can still accept a sub-30s market on
    the fourth try; this function does not.
    """
    viable = []
    for market in markets or []:
        ticker = (market.get("ticker") or "").strip() if isinstance(market, dict) else ""
        if not ticker:
            continue
        mins, _iso = market_clock(market, now)
        if mins is not None and mins < cutoff_minutes:
            continue
        sort_key = mins if mins is not None else 1e9
        viable.append((sort_key, market))
    if not viable:
        return None
    viable.sort(key=lambda item: item[0])
    return viable[0][1]


def compose_price(snapshot, spot):
    """Live print plus the 15-minute reference used by Delta.

    open15m prefers Kalshi floor_strike (the contract's target). The exchange
    15-minute candle open is only a fallback when the market has no strike.
    """
    live = None
    exchange_open = None
    spot_source = None
    if isinstance(spot, dict):
        try:
            if spot.get("price") not in (None, ""):
                live = float(spot["price"])
        except (TypeError, ValueError):
            live = None
        try:
            if spot.get("exchange_open") not in (None, ""):
                exchange_open = float(spot["exchange_open"])
        except (TypeError, ValueError):
            exchange_open = None
        spot_source = spot.get("source")
    if live is not None and live <= 0:
        live = None
    if exchange_open is not None and exchange_open <= 0:
        exchange_open = None

    open15m = None
    open_source = None
    floor = None
    if isinstance(snapshot, dict):
        floor = snapshot.get("floor_strike")
    try:
        if floor not in (None, "") and float(floor) > 0:
            open15m = float(floor)
            open_source = "kalshi_floor_strike"
    except (TypeError, ValueError):
        open15m = None
    if open15m is None and exchange_open is not None:
        open15m = exchange_open
        open_source = (spot_source or "exchange") + "_15m_open"

    delta = None
    if live is not None and open15m is not None:
        delta = abs(open15m - live)
    return {
        "price": live,
        "open15m": open15m,
        "open_source": open_source,
        "exchange_open": exchange_open,
        "spot_source": spot_source,
        "delta": delta,
    }


def choose_side(up, down, entry_min, entry_max, delta, min_delta):
    """Return (side, price, reason). reason is None when a side is chosen."""
    def in_range(px):
        return isinstance(px, (int, float)) and not isinstance(px, bool) and entry_min <= float(px) <= entry_max

    up_in = in_range(up)
    down_in = in_range(down)
    if not (up_in or down_in):
        return None, None, "out_of_range"
    if delta is None or float(delta) < float(min_delta):
        return None, None, "delta_low"
    if up_in and (not down_in or float(up) >= float(down)):
        return "UP", float(up), None
    return "DOWN", float(down), None


def format_px(price):
    return "{:.2f}".format(round(float(price) + 0.0, 2))


def format_money(value):
    return "{:.2f}".format(round(float(value) + 0.0, 2))


def format_pnl(value):
    value = round(float(value) + 0.0, 2)
    if value > 0:
        return "+" + "{:.2f}".format(value)
    return "{:.2f}".format(value)


def format_size(size):
    value = round(float(size) * 100) / 100.0
    text = "{:.2f}".format(value)
    while "." in text and text.endswith("0"):
        text = text[:-1]
    if text.endswith("."):
        text = text[:-1]
    return text


def format_or_na(value):
    if value is None or value == "":
        return "n/a"
    return format_px(value)


def format_delta(value):
    if value is None or value == "":
        return "n/a"
    value = float(value)
    if abs(value) >= 1:
        return "{:.2f}".format(round(value, 2))
    return "{:.4f}".format(round(value, 4))


class PaperEngine:
    """The script's state machine. Network stays outside; pass snapshots in."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.balance = float(cfg.initial_balance)
        self.wins = 0
        self.losses = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self.trade_count = 0
        self.trades = []
        self.positions = {}
        self.pending = {}
        self.phase = {}
        self.session_key = {}
        self.log_once = {}
        self.settlement_checked_at = {}
        self.halted = False
        self.halt_reason = None

    def startup_lines(self):
        c = self.cfg
        lines = [(
            "Kalshi PAPER TRADING Started | EntryRange: [{}, {}] | Contracts: {} "
            "| Exit: {} | TakeProfitPts: {} | Pings: {} | Daily targets: {}/{} "
            "| Delta: {} | Window: last {}m | InitialBalance: {}".format(
                format_px(c.entry_min), format_px(c.entry_max), format_size(c.order_size),
                format_px(c.exit), format_px(c.take_profit_pts), c.pings,
                format_money(c.daily_profit_target), format_money(c.daily_loss_target),
                c.delta, format_size(c.time_delay), format_money(c.initial_balance))
        )]
        if float(c.take_profit_pts) == 1:
            lines.append("TakeProfitPts = 1, winners will be held to resolution")
        lines.append(
            "Paper only. No Nightshark host, no Kalshi API key, no orders. "
            "Spot is a public exchange print. 15m open is the contract floor strike.")
        return lines

    def equity(self):
        total = float(self.balance)
        for book in (self.positions, self.pending):
            for pos in book.values():
                if not isinstance(pos, dict):
                    continue
                mark = pos["last_mark"] if "last_mark" in pos else pos["entry"]
                total += float(mark) * float(pos["size"])
        return total

    def live_pnl(self):
        return self.equity() - float(self.cfg.initial_balance)

    def open_exposure(self):
        return any(isinstance(p, dict) for p in self.positions.values()) or any(
            isinstance(p, dict) for p in self.pending.values())

    def account_line(self):
        avg_profit = format_money(self.gross_profit / self.wins) if self.wins else "n/a"
        avg_loss = format_money(self.gross_loss / self.losses) if self.losses else "n/a"
        return (
            "pnl {} | equity {} | cash {} | wins {}/{} | avg profit {} | avg loss {}".format(
                format_pnl(self.live_pnl()), format_money(self.equity()),
                format_money(self.balance), self.wins, self.trade_count,
                avg_profit, avg_loss)
        )

    def status(self, now=None):
        view = {
            "balance": self.balance,
            "equity": self.equity(),
            "pnl": self.live_pnl(),
            "wins": self.wins,
            "losses": self.losses,
            "trades": self.trade_count,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "pending": [p.get("ticker") for p in self.pending.values() if isinstance(p, dict)],
            "assets": {},
        }
        for asset in self.cfg.assets:
            pos = self.positions.get(asset)
            view["assets"][asset] = {
                "phase": self.phase.get(asset, "WAIT_WINDOW"),
                "session": self.session_key.get(asset),
                "position": None if not isinstance(pos, dict) else {
                    "side": pos["side"],
                    "entry": pos["entry"],
                    "size": pos["size"],
                    "last_mark": pos.get("last_mark"),
                    "ticker": pos.get("ticker"),
                    "take_profit_target": pos.get("take_profit_target"),
                },
            }
        if now is not None:
            view["ts"] = now.astimezone(timezone.utc).isoformat()
        return view

    def _emit(self, events, msg, **extra):
        event = {"msg": msg}
        event.update(extra)
        events.append(event)

    def _log_once(self, asset, reason, msg, events):
        key = asset + "|" + reason
        if key in self.log_once:
            return
        self.log_once[key] = True
        self._emit(events, msg)

    def _reset_log_once(self, asset):
        prefix = asset + "|"
        self.log_once = {k: v for k, v in self.log_once.items() if not k.startswith(prefix)}

    def _side_price(self, snapshot, side):
        if not isinstance(snapshot, dict):
            return None
        raw = snapshot.get("up") if side == "UP" else snapshot.get("down")
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def position_session_ended(self, asset, snapshot, now):
        pos = self.positions.get(asset)
        if not isinstance(pos, dict):
            return False
        ticker = ""
        if isinstance(snapshot, dict):
            ticker = snapshot.get("market_ticker") or ""
        if ticker and pos.get("ticker") and ticker != pos["ticker"]:
            return True
        if "quarter_index" in pos and quarter_index(now) != pos["quarter_index"]:
            return True
        return False

    def mark_held(self, asset, events):
        pos = self.positions.get(asset)
        if not isinstance(pos, dict):
            return True
        pos = self.positions.pop(asset)
        pos["asset"] = asset
        key = pos.get("ticker") or "{}|{}".format(asset, time.time_ns())
        self.pending[key] = pos
        self._emit(events, "{} {} held to resolution | settlement pending".format(
            asset, pos["side"]))
        self.phase[asset] = "WAIT_WINDOW"
        return True

    def _record(self, asset, side, entry, exit_price, size, reason, now, events):
        proceeds = float(exit_price) * float(size)
        pnl = (float(exit_price) - float(entry)) * float(size)
        self.balance += proceeds
        self.trade_count += 1
        if pnl > 0:
            self.wins += 1
            self.gross_profit += pnl
        else:
            self.losses += 1
            self.gross_loss += abs(pnl)
        trade = {
            "ts": now.astimezone(timezone.utc).isoformat(),
            "asset": asset,
            "side": side,
            "entry": float(entry),
            "exit": float(exit_price),
            "size": float(size),
            "pnl": pnl,
            "reason": reason,
            "balance": self.balance,
        }
        self.trades.append(trade)
        self._emit(
            events,
            "{} Paper {} closed @ {} | entry {} | size {} | PnL {} | cash {} | {}".format(
                asset, side, format_px(exit_price), format_px(entry), format_size(size),
                format_pnl(pnl), format_money(self.balance), reason),
            trade=trade,
        )

    def check_daily(self, events):
        if self.open_exposure():
            return False
        # Dollar targets are judged on the cents the log prints, so a PnL
        # that displays as +0.20 does not clear a 0.20 target.
        pnl = round(self.live_pnl(), 2)
        target = round(float(self.cfg.daily_profit_target), 2)
        if target > 0 and pnl > target:
            self._emit(events, "Daily profit target reached | PnL {} > {} | stopping bot".format(
                format_pnl(pnl), format_pnl(target)))
            self.halted = True
            self.halt_reason = "daily_profit"
            return True
        stop = round(float(self.cfg.daily_loss_target), 2)
        if stop < 0 and pnl < stop:
            self._emit(events, "Daily loss target reached | PnL {} < {} | stopping bot".format(
                format_pnl(pnl), format_pnl(stop)))
            self.halted = True
            self.halt_reason = "daily_loss"
            return True
        return False

    def _buy(self, asset, side, price, events):
        cost = float(price) * float(self.cfg.order_size)
        if cost > self.balance + 1e-12:
            self._emit(events, "{} Paper buy rejected: cost {} exceeds cash balance {}".format(
                asset, format_money(cost), format_money(self.balance)))
            self._emit(events, "Not enough paper balance left to place trades. Stopping script.")
            self.halted = True
            self.halt_reason = "insufficient_balance"
            return False
        self.balance -= cost
        self._emit(events, "{} Paper {} filled @ {} | size {} | cost {} | cash {}".format(
            asset, side, format_px(price), format_size(self.cfg.order_size),
            format_money(cost), format_money(self.balance)))
        return True

    def _confirm_stop(self, asset, pos, price, refetch, sleep, events):
        pings = int(self.cfg.pings)
        if pings <= 0:
            return True, float(price)
        confirmed = float(price)
        self._emit(events, "{} Stop-loss candidate @ {} | waiting for {} confirmation ping(s)".format(
            asset, format_px(confirmed), pings))
        for index in range(1, pings + 1):
            if sleep is not None:
                sleep(1)
            fresh = refetch(asset) if refetch is not None else None
            fresh_ticker = (fresh.get("market_ticker") or "") if isinstance(fresh, dict) else ""
            if not isinstance(fresh, dict) or (
                    pos.get("ticker") and fresh_ticker != pos.get("ticker")):
                self._emit(events, "{} Stop-loss confirmation reset: fresh market price unavailable".format(asset))
                return False, confirmed
            ping = self._side_price(fresh, pos["side"])
            if ping is None:
                self._emit(events, "{} Stop-loss confirmation reset: fresh side price unavailable".format(asset))
                return False, confirmed
            if isinstance(self.positions.get(asset), dict):
                self.positions[asset]["last_mark"] = ping
            confirmed = ping
            if confirmed >= float(self.cfg.exit):
                self._emit(events, "PING {} : {} Stop-loss reset: price rebounded to {}".format(
                    index, asset, format_px(confirmed)))
                return False, confirmed
            self._emit(events, "PING {} : {} Stop-loss remained below Exit @ {}".format(
                index, asset, format_px(confirmed)))
        return True, confirmed

    def tick(self, asset, snapshot, price, now, refetch=None, sleep=None):
        events = []
        if self.halted:
            return events
        if not isinstance(snapshot, dict):
            if self.position_session_ended(asset, None, now):
                self.mark_held(asset, events)
                return events
            self._log_once(
                asset, "NO_DATA_{}".format(quarter_index(now)),
                "{} Waiting for Kalshi market data".format(asset), events)
            return events

        if self.position_session_ended(asset, snapshot, now):
            self.mark_held(asset, events)
            return events

        key = snapshot.get("market_ticker") or snapshot.get("close_time") or str(quarter_index(now))
        if self.session_key.get(asset) != key:
            self.session_key[asset] = key
            self.phase[asset] = "WAIT_WINDOW"
            self._reset_log_once(asset)
            self._emit(events, "{} New Kalshi paper session ({})".format(
                asset, snapshot.get("market_ticker") or ""))
            return events

        pos = self.positions.get(asset)
        if isinstance(pos, dict):
            current = self._side_price(snapshot, pos["side"])
            self.phase[asset] = "IN_POSITION"
            if current is None:
                self._log_once(asset, "MARK_MISSING",
                               "{} Held side price missing; not treating it as 0".format(asset),
                               events)
                return events
            pos["last_mark"] = current
            target = float(pos["take_profit_target"]) if "take_profit_target" in pos else (
                float(pos["entry"]) + float(self.cfg.take_profit_pts))
            exit_reason = ""
            exit_phase = ""
            exit_msg = ""
            exit_price = current
            if current < float(self.cfg.exit):
                ok, confirmed = self._confirm_stop(asset, pos, current, refetch, sleep, events)
                if ok:
                    exit_price = confirmed
                    exit_reason = "exit_below_{}".format(self.cfg.exit)
                    exit_phase = "STOPPED_OUT"
                    exit_msg = "{} Stopped out, waiting for next session".format(asset)
                    self._emit(events, "{} Stop-loss confirmed @ {} < {}".format(
                        asset, format_px(exit_price), format_px(self.cfg.exit)))
            elif target <= 1 and current >= target:
                exit_reason = "take_profit_at_{}".format(target)
                exit_phase = "TOOK_PROFIT"
                exit_msg = "{} Take profit completed, waiting for next session".format(asset)
                self._emit(events, "{} Take-profit triggered @ {} >= {} (fill {} + {})".format(
                    asset, format_px(current), format_px(target),
                    format_px(pos["entry"]), format_px(self.cfg.take_profit_pts)))
            if exit_reason:
                self.positions.pop(asset, None)
                self._record(asset, pos["side"], pos["entry"], exit_price, pos["size"],
                             exit_reason, now, events)
                self._emit(events, "{} Paper position closed".format(asset))
                self.phase[asset] = exit_phase
                self._log_once(asset, exit_phase, exit_msg, events)
                self.check_daily(events)
            return events

        phase = self.phase.get(asset)
        if phase == "STOPPED_OUT":
            self._log_once(asset, "STOPPED_OUT",
                           "{} Stopped out, waiting for next session".format(asset), events)
            return events
        if phase == "TOOK_PROFIT":
            self._log_once(asset, "TOOK_PROFIT",
                           "{} Take profit completed, waiting for next session".format(asset), events)
            return events
        if phase == "BUY_FAILED":
            self._log_once(asset, "BUY_FAILED",
                           "{} Paper buy failed earlier, waiting for next session".format(asset), events)
            return events

        mins = minutes_left(snapshot, now)
        if not (mins > 0 and mins <= float(self.cfg.time_delay)):
            if mins <= 0:
                self._log_once(asset, "SESSION_ENDED",
                               "{} End of session, waiting for new Kalshi market".format(asset), events)
            else:
                self._log_once(asset, "WAIT_WINDOW",
                               "{} Waiting for last {} minutes".format(asset, format_size(self.cfg.time_delay)),
                               events)
            self.phase[asset] = "WAIT_WINDOW"
            return events

        if self.phase.get(asset) != "MONITORING":
            self.phase[asset] = "MONITORING"
            self._emit(events, "{} Monitoring Kalshi paper entry range [{}, {}] | Delta >= {}".format(
                asset, format_px(self.cfg.entry_min), format_px(self.cfg.entry_max), self.cfg.delta))

        view = price if isinstance(price, dict) else {}
        delta = view.get("delta")
        side, entry, reason = choose_side(
            snapshot.get("up"), snapshot.get("down"),
            self.cfg.entry_min, self.cfg.entry_max, delta, self.cfg.delta)
        if reason == "delta_low":
            self._log_once(asset, "DELTA_LOW",
                           "{} LiveDelta {} < Delta {}, skipping entry".format(
                               asset, format_delta(delta), self.cfg.delta), events)
            return events
        if reason == "out_of_range":
            self._log_once(asset, "PRICE_OUT_OF_RANGE",
                           "{} UP {} / DOWN {} outside entry range [{}, {}]".format(
                               asset, format_or_na(snapshot.get("up")), format_or_na(snapshot.get("down")),
                               format_px(self.cfg.entry_min), format_px(self.cfg.entry_max)), events)
            return events

        self._emit(events, "{} {} triggered @ {} — placing paper order".format(
            asset, side, format_px(entry)))
        if not self._buy(asset, side, entry, events):
            self.phase[asset] = "BUY_FAILED"
            self._log_once(asset, "BUY_FAILED",
                           "{} Paper buy failed, waiting for next session".format(asset), events)
            return events

        target = float(entry) + float(self.cfg.take_profit_pts)
        self.positions[asset] = {
            "side": side,
            "entry": float(entry),
            "take_profit_target": target,
            "ticker": snapshot.get("market_ticker") or "",
            "session_key": key,
            "quarter_index": quarter_index(now),
            "size": float(self.cfg.order_size),
            "last_mark": float(entry),
            "asset": asset,
        }
        self.phase[asset] = "IN_POSITION"
        self._emit(events, "{} Paper position confirmed | fill: {} | size: {}".format(
            asset, format_px(entry), format_size(self.cfg.order_size)))
        if target > 1:
            self._emit(events, "{} TP target {} > 1; no TP exit—hold for resolution".format(
                asset, format_px(target)))
        else:
            self._emit(events, "{} Monitoring stop-loss < {} | take-profit >= {} (fill + {})".format(
                asset, format_px(self.cfg.exit), format_px(target), format_px(self.cfg.take_profit_pts)))
        return events

    def process_settlements(self, fetch_result, now, mono):
        events = []
        if self.halted:
            return events
        resolved = []
        for key, pos in list(self.pending.items()):
            if not isinstance(pos, dict):
                continue
            last = self.settlement_checked_at.get(key)
            if last is not None and (mono - last) < 2.0:
                continue
            self.settlement_checked_at[key] = mono
            ticker = pos.get("ticker") or ""
            if not ticker:
                continue
            try:
                result = fetch_result(ticker)
            except Exception:
                result = ""
            result = (result or "").strip().lower()
            if result == "yes":
                yes_settle = 1.0
            elif result == "no":
                yes_settle = 0.0
            else:
                continue
            self.settlement_checked_at.pop(key, None)
            settle = yes_settle if pos.get("side") == "UP" else 1.0 - yes_settle
            asset = pos.get("asset") or "Kalshi"
            last_mark = pos["last_mark"] if "last_mark" in pos else pos["entry"]
            self._emit(events, "{} {} settlement confirmed @ {} (last price {})".format(
                asset, pos.get("side"), format_px(settle), format_px(last_mark)))
            self._record(asset, pos.get("side"), pos["entry"], settle, pos["size"],
                         "held_to_resolution", now, events)
            resolved.append(key)
        for key in resolved:
            self.pending.pop(key, None)
        if resolved:
            self.check_daily(events)
        return events


def _http_json(url, params=None, timeout=8):
    try:
        response = requests.get(url, params=params, timeout=timeout, headers=_UA)
    except requests.RequestException:
        return None
    if response.status_code == 429 or response.status_code >= 500:
        return None
    if not response.ok:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _coinbase_spot(product, now_ts):
    ticker = _http_json("https://api.exchange.coinbase.com/products/{}/ticker".format(product))
    if not isinstance(ticker, dict) or not ticker.get("price"):
        return None
    price = float(ticker["price"])
    if price <= 0:
        return None
    candles = _http_json(
        "https://api.exchange.coinbase.com/products/{}/candles".format(product),
        params={"granularity": 900})
    exchange_open = None
    if isinstance(candles, list):
        best_start = None
        for row in candles:
            if not isinstance(row, (list, tuple)) or len(row) < 4:
                continue
            start = int(row[0])
            if start <= now_ts and (best_start is None or start > best_start[0]):
                best_start = (start, float(row[3]))
            if start <= now_ts < start + 900:
                exchange_open = float(row[3])
                break
        if exchange_open is None and best_start is not None:
            exchange_open = best_start[1]
    return {"price": price, "exchange_open": exchange_open, "source": "coinbase"}


def _kraken_spot(pair, now_ts):
    ticker = _http_json("https://api.kraken.com/0/public/Ticker", params={"pair": pair})
    if not isinstance(ticker, dict) or ticker.get("error"):
        return None
    result = ticker.get("result") or {}
    price = None
    for payload in result.values():
        if isinstance(payload, dict) and payload.get("c"):
            price = float(payload["c"][0])
            break
    if price is None or price <= 0:
        return None
    ohlc = _http_json("https://api.kraken.com/0/public/OHLC", params={"pair": pair, "interval": 15})
    exchange_open = None
    rows = []
    if isinstance(ohlc, dict):
        for value in (ohlc.get("result") or {}).values():
            if isinstance(value, list):
                rows = value
                break
    best_start = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        start = int(row[0])
        opened = float(row[1])
        if start <= now_ts and (best_start is None or start > best_start[0]):
            best_start = (start, opened)
        if start <= now_ts < start + 900:
            exchange_open = opened
            break
    if exchange_open is None and best_start is not None:
        exchange_open = best_start[1]
    return {"price": price, "exchange_open": exchange_open, "source": "kraken"}


def fetch_public_spot(asset, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    product = _COINBASE.get(asset)
    if product:
        spot = _coinbase_spot(product, now_ts)
        if spot:
            return spot
    pair = _KRAKEN.get(asset)
    if pair:
        return _kraken_spot(pair, now_ts)
    return None


class KalshiClient:
    """Public GETs only. Market metadata is cached; the orderbook is not."""

    def __init__(self, market_refresh_ms=5000, price_refresh_ms=1500, sleeper=None):
        self.market_refresh_ms = market_refresh_ms
        self.price_refresh_ms = price_refresh_ms
        self._sleep = sleeper or time.sleep
        self._meta = {}
        self._meta_at = {}
        self._spot = {}
        self._spot_at = {}

    def _get(self, path, params=None):
        for base in KALSHI_HOSTS:
            body = _http_json(base + path, params=params)
            if body is not None:
                return body
        return None

    def _refresh_book(self, snapshot):
        ticker = snapshot.get("market_ticker") or ""
        if not ticker:
            return False
        body = self._get("/markets/{}/orderbook".format(ticker), {"depth": 1})
        if not body:
            return False
        return apply_orderbook(snapshot, body)

    def _discover(self, asset, now):
        series = ASSET_SERIES[asset]
        chosen = None
        for attempt in range(4):
            body = self._get("/markets", {
                "series_ticker": series,
                "status": "open",
                "limit": 5,
            })
            markets = (body or {}).get("markets") if isinstance(body, dict) else None
            chosen = pick_market(markets, now)
            if chosen is not None:
                break
            if attempt < 3:
                self._sleep(0.2)
        if chosen is None:
            return None
        snap = snapshot_from_market(chosen, now)
        self._meta[asset] = snap
        self._meta_at[asset] = time.monotonic()
        if self._refresh_book(snap) or (snap["up"] is not None and snap["down"] is not None):
            return snap
        return None

    def snapshot(self, asset, now=None):
        if asset not in ASSET_SERIES:
            return None
        now = now or datetime.now(timezone.utc)
        cached = self._meta.get(asset)
        age_ms = (time.monotonic() - self._meta_at.get(asset, 0)) * 1000
        if isinstance(cached, dict) and age_ms < self.market_refresh_ms:
            mins = minutes_left(cached, now)
            if mins is not None and mins < 0.5:
                self._meta.pop(asset, None)
                self._meta_at.pop(asset, None)
            else:
                if self._refresh_book(cached):
                    return cached
                return None
        snap = self._discover(asset, now)
        if snap is not None:
            return snap
        cached = self._meta.get(asset)
        if isinstance(cached, dict):
            mins = minutes_left(cached, now)
            if mins is not None and mins < 0.5:
                self._meta.pop(asset, None)
                self._meta_at.pop(asset, None)
                return None
            if self._refresh_book(cached):
                return cached
        return None

    def spot(self, asset, now_ts=None):
        now_m = time.monotonic()
        cached = self._spot.get(asset)
        age_ms = (now_m - self._spot_at.get(asset, 0)) * 1000
        if cached and age_ms < self.price_refresh_ms:
            return cached
        fresh = fetch_public_spot(asset, now_ts if now_ts is not None else time.time())
        self._spot_at[asset] = now_m
        if isinstance(fresh, dict) and fresh.get("price"):
            self._spot[asset] = fresh
            return fresh
        return cached

    def result(self, ticker):
        body = self._get("/markets/{}".format(ticker))
        if not isinstance(body, dict):
            return ""
        market = body.get("market") if isinstance(body.get("market"), dict) else body
        return market.get("result") or ""


def heartbeat(asset, engine, snapshot, price, now):
    phase = engine.phase.get(asset, "WAIT_WINDOW")
    if not isinstance(snapshot, dict):
        return "{} {} | no market | {} | pending {}".format(
            asset, phase, engine.account_line(), len(engine.pending))
    mins = minutes_left(snapshot, now)
    view = price if isinstance(price, dict) else {}
    pos = engine.positions.get(asset)
    held = ""
    if isinstance(pos, dict):
        held = " | held {} @ {}".format(pos["side"], format_px(pos["entry"]))
    return (
        "{} {} {} | {:.1f}m left | UP {} DOWN {} | spot {} open {} ({}) delta {}{} | {} | pending {}".format(
            asset, phase, snapshot.get("market_ticker") or "",
            mins, format_or_na(snapshot.get("up")), format_or_na(snapshot.get("down")),
            format_or_na(view.get("price")), format_or_na(view.get("open15m")),
            view.get("open_source") or "n/a", format_delta(view.get("delta")),
            held, engine.account_line(), len(engine.pending))
    )


def run(cfg, once=False, log_path=None, status_path=None, sleep=None, client=None, now_fn=None):
    """Poll until a daily target, a broken paper account, or Ctrl-C.

    Returns a process exit code. 0 means the loop ended on purpose.
    """
    sleep = sleep or time.sleep
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    client = client or KalshiClient(cfg.market_refresh_ms, cfg.price_refresh_ms, sleeper=sleep)
    engine = PaperEngine(cfg)
    log_path = log_path or os.path.join(DEFAULT_LOG_DIR, "events.jsonl")
    status_path = status_path or os.path.join(DEFAULT_LOG_DIR, "status.json")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logged_spot = set()
    last_beat = 0.0

    def emit(event):
        msg = event["msg"] if isinstance(event, dict) else str(event)
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print("{} {}".format(stamp, msg), flush=True)
        row = {"ts": datetime.now(timezone.utc).isoformat(), "session": session_id, "msg": msg}
        if isinstance(event, dict) and "trade" in event:
            row["trade"] = event["trade"]
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def write_status(now):
        payload = engine.status(now)
        payload["session"] = session_id
        payload["config"] = {
            "entry": [cfg.entry_min, cfg.entry_max],
            "exit": cfg.exit,
            "take_profit_pts": cfg.take_profit_pts,
            "delta": cfg.delta,
            "time_delay": cfg.time_delay,
            "order_size": cfg.order_size,
            "pings": cfg.pings,
            "assets": list(cfg.assets),
        }
        tmp = status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, status_path)

    emit({"msg": "session {} | log {}".format(session_id, log_path)})
    for line in engine.startup_lines():
        emit({"msg": line})

    def refetch(asset):
        return client.snapshot(asset, now_fn())

    def poll():
        now = now_fn()
        for asset in cfg.assets:
            snap = client.snapshot(asset, now)
            spot = client.spot(asset, now.timestamp())
            price = compose_price(snap, spot)
            source = price.get("spot_source")
            if source and asset not in logged_spot:
                logged_spot.add(asset)
                emit({"msg": "{} spot source {} | 15m open source {}".format(
                    asset, source, price.get("open_source") or "n/a")})
            events = engine.tick(asset, snap, price, now, refetch=refetch, sleep=sleep)
            for event in events:
                emit(event)
            if engine.halted:
                break
        if not engine.halted:
            for event in engine.process_settlements(client.result, now_fn(), time.monotonic()):
                emit(event)

    def beat(now):
        for asset in cfg.assets:
            snap = client._meta.get(asset)
            spot = client._spot.get(asset)
            emit({"msg": heartbeat(asset, engine, snap, compose_price(snap, spot), now)})

    try:
        while not engine.halted:
            poll()
            # The script's first sighting of a market only announces the
            # session. A one-shot run also takes the next poll, which is
            # the one that can enter, instead of stopping on the announcement.
            if once and not engine.halted:
                poll()
            now = now_fn()
            beat_due = once or (time.monotonic() - last_beat) >= 5
            if beat_due:
                beat(now)
                last_beat = time.monotonic()
            write_status(now)
            if once or engine.halted:
                break
            delay_ms = cfg.poll_interval_ms + random.randint(0, cfg.poll_jitter_max_ms)
            if delay_ms > 0:
                sleep(delay_ms / 1000.0)
    except KeyboardInterrupt:
        emit({"msg": "Stopped"})
        write_status(now_fn())
        return 0

    if engine.halted and engine.pending:
        emit({"msg": "Stopping with settlement still pending. Equity still marks those contracts; cash does not include the settlement yet."})
        write_status(now_fn())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Paper-verify the Kalshi v5 15-minute bot. Places no orders.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help="Settings JSON (default: rhengine/kalshi_v5_paper.json)")
    parser.add_argument("--once", action="store_true",
                        help="Announce the session and print the next poll's decision, then exit")
    parser.add_argument("--log", default=None, help="Append-only jsonl path")
    parser.add_argument("--status", default=None, help="Overwrite-each-loop status JSON path")
    args = parser.parse_args(argv)
    try:
        cfg, errors = load_config(args.config)
    except OSError as exc:
        print("ERROR: could not read config {}: {}".format(args.config, exc))
        return 2
    except json.JSONDecodeError as exc:
        print("ERROR: config is not valid JSON: {}".format(exc))
        return 2
    if errors:
        for line in errors:
            print(line)
        print("ERROR: Fix the settings listed above and restart the script. Stopping now.")
        return 2
    return run(cfg, once=args.once, log_path=args.log, status_path=args.status)


if __name__ == "__main__":
    sys.exit(main())
