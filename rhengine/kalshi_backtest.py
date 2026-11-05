"""
kalshi_backtest.py — replay the Kalshi v5 paper rules on 1-minute candles.

The rules are the paper script in rhengine/kalshi_v5_paper.json. This module
places no orders. It reads public candlesticks for KXBTC15M and books a
local cash account. That account is not an rhengine.alpha.book result.

Minute bars are not the script's 700ms poll, and they cannot do its
one-second stop ping. Two fills are always reported:

  close — one sample, the minute-end ask. Enter only if that close is
          inside the band. Stop only if the later close is below Exit.
          A take-profit minute is booked at that close, which can be
          past fill + TakeProfitPts.
  touch — enter if the repaired high/low overlaps the band, at the
          highest price inside the overlap. Take profit when the high
          reaches the target, filled at the target (or at the open if
          the bar opened through it). A stop needs the low and the close
          both below Exit, filled at the close, or at the open if the
          bar opened already below Exit. A crash is therefore booked at
          the low close. A dip that recovers before the minute ends is kept.

Exits start the minute after the entry minute. The replay assumes the
process was already watching when the 15-minute window opened, so the
script's announce-only first poll is not charged against that minute.

The live settled list starts 2026-07-31. Contracts older than the
2026-08-07 historical cutoff are on /historical/markets. That archive is
the same series and goes back to 2025-12-10. Paging only the live list
makes a 6-month window look empty.

Delta is 0 in the shipped settings. The check passes when floor_strike
is present. This replay does not rebuild the Coinbase spot path. A
positive Delta blocks every entry. Fees are not charged.
"""

import argparse
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from .kalshi_paper import (
    DEFAULT_CONFIG_PATH,
    choose_side,
    format_money,
    format_pnl,
    load_config,
)
from .market_data_kalshi import KALSHI_HOSTS

SERIES = "KXBTC15M"
# 1 / 3 / 6 month, counted as 30 / 90 / 180 days.
WINDOW_DAYS = {1: 30, 3: 90, 6: 180}
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_PKG_DIR)
DEFAULT_CACHE = os.path.join(_REPO_ROOT, "research_data", "kalshi_paper", "hist")
_UA = "rhengine-kalshi-backtest"
_TICKER_OK = re.compile(r"^[A-Z0-9._-]+$")
_LOCAL = threading.local()

NOTES = (
    "Minute bars are not the 700ms poll. Exits start the minute after entry.",
    "Close mode fills at the minute-end ask. A take-profit minute is booked at that close, which can be past fill+0.10.",
    "Touch mode enters at the highest price inside the band and sells a take-profit at the target. A stop needs the minute to finish below the exit, filled at that close, so a crash is booked at the low close. A dip that recovers before the minute ends is kept.",
    "Delta is 0 in the shipped settings. The replay passes that check when floor_strike is present. It does not rebuild the Coinbase path.",
    "Fees are not charged. This cash account is the replay. It is not an rhengine.alpha.book result.",
)


def _session():
    sess = getattr(_LOCAL, "s", None)
    if sess is None:
        sess = requests.Session()
        sess.headers["User-Agent"] = _UA
        _LOCAL.s = sess
    return sess


def kalshi_get(path, params=None, timeout=15, retries=3):
    """GET a public Kalshi path. None when every host returns 404."""
    last = None
    for attempt in range(retries):
        misses = 0
        for base in KALSHI_HOSTS:
            try:
                response = _session().get(
                    base + path, params=params, timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = exc
                continue
            if response.status_code == 429:
                last = "429"
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 404:
                misses += 1
                last = "404"
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                last = exc
                continue
            return response.json()
        if misses == len(KALSHI_HOSTS):
            return None
        time.sleep(0.5 * (attempt + 1))
    raise ConnectionError("GET {} failed: {}".format(path, last))


def parse_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _px(value):
    """Dollar string, dollar float, or legacy cents. None stays None."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1.5:
        number = number / 100.0
    return number


def _field(block, name):
    if not isinstance(block, dict):
        return None
    value = block.get(name + "_dollars")
    if value is None:
        value = block.get(name)
    return _px(value)


def repair_ohlc(open_, high, low, close):
    """Drop empty-book prints of 0 and 1 when the close is a real price.

    A close that itself prints 0.00 or 1.00 is kept. That is a real
    expiry print, not a missing quote.
    """
    if close is None:
        return open_, high, low, close
    if high is not None and high >= 0.999 and close < 0.999:
        others = [x for x in (open_, low, close) if x is not None and x < 0.999]
        if others:
            high = max(others)
    if low is not None and low <= 0.001 and close > 0.001:
        others = [x for x in (open_, high, close) if x is not None and x > 0.001]
        if others:
            low = min(others)
    return open_, high, low, close


def _round4(value):
    if value is None:
        return None
    return round(float(value), 4)


def _block_ohlc(block):
    return repair_ohlc(
        _field(block, "open"),
        _field(block, "high"),
        _field(block, "low"),
        _field(block, "close"),
    )


def side_books(candle):
    """Return (up, down) OHLC tuples from one candlestick.

    UP is the repaired yes_ask. DOWN is the repaired yes_bid flipped:
    the cheap DOWN print is 1 - yes_bid high.
    """
    candle = candle or {}
    up = tuple(_round4(x) for x in _block_ohlc(candle.get("yes_ask") or {}))
    bid_o, bid_h, bid_l, bid_c = _block_ohlc(candle.get("yes_bid") or {})
    down = (
        None if bid_o is None else _round4(1.0 - bid_o),
        None if bid_l is None else _round4(1.0 - bid_l),
        None if bid_h is None else _round4(1.0 - bid_h),
        None if bid_c is None else _round4(1.0 - bid_c),
    )
    return up, down


def has_floor(market):
    try:
        return float(market.get("floor_strike")) > 0
    except (TypeError, ValueError):
        return False


def delta_value(market, cfg):
    """0 when the shipped Delta check can pass. None otherwise.

    A positive Delta needs a spot path. This replay does not build one,
    so a positive Delta fails closed.
    """
    if not has_floor(market):
        return None
    if float(cfg.delta) > 0:
        return None
    return 0.0


def _touch_fill(ohlc, cfg):
    open_, high, low, close = ohlc
    if high is None:
        high = close
    if low is None:
        low = close
    if high is None or low is None:
        return None
    if low > high:
        low, high = high, low
    worst = min(high, float(cfg.entry_max))
    best = max(low, float(cfg.entry_min))
    if worst < best:
        return None
    return worst


def entry_quotes(up, down, cfg, mode):
    if mode == "close":
        return up[3], down[3]
    if mode != "touch":
        raise ValueError("mode must be close or touch, got {!r}".format(mode))
    return _touch_fill(up, cfg), _touch_fill(down, cfg)


def exit_signal(ohlc, entry, cfg, mode, stop_fill="path"):
    """Return (reason, price) or (None, None). Does not run on the entry bar.

    stop_fill "path" sells at the minute's own price. "level" sells at Exit
    when the bar did not open already through the stop. That second fill is
    a sensitivity for the one-second ping. It is not the published replay.
    """
    if stop_fill not in ("path", "level"):
        raise ValueError("stop_fill must be path or level, got {!r}".format(stop_fill))
    open_, high, low, close = ohlc
    target = round(float(entry) + float(cfg.take_profit_pts), 4)
    level = float(cfg.exit)

    def stop_px():
        if open_ is not None and open_ < level:
            return open_
        if stop_fill == "level":
            return level
        return close

    if mode == "close":
        stop_hit = close is not None and close < level
        tp_hit = target <= 1 and close is not None and close >= target

        def tp_px():
            return close
    elif mode == "touch":
        stop_hit = (
            low is not None and close is not None
            and low < level and close < level)
        tp_hit = target <= 1 and high is not None and high >= target

        def tp_px():
            if open_ is not None and open_ >= target:
                return open_
            return target
    else:
        raise ValueError("mode must be close or touch, got {!r}".format(mode))

    bearish = open_ is None or (close is not None and close < open_)
    order = ("stop", "tp") if bearish else ("tp", "stop")
    for name in order:
        if name == "stop" and stop_hit:
            price = stop_px()
            if price is None:
                continue
            return "stop", float(price)
        if name == "tp" and tp_hit:
            price = tp_px()
            if price is None:
                continue
            return "take_profit", float(price)
    return None, None


def settlement_price(side, result):
    text = (result or "").strip().lower()
    if text not in ("yes", "no"):
        return None
    won = (text == "yes" and side == "UP") or (text == "no" and side == "DOWN")
    return 1.0 if won else 0.0


def _trade(market, side, entry, exit_px, reason, size, minutes_left=None):
    pnl = (float(exit_px) - float(entry)) * float(size)
    trade = {
        "ticker": market.get("ticker"),
        "side": side,
        "entry": float(entry),
        "exit": float(exit_px),
        "pnl": pnl,
        "reason": reason,
        "close_time": market.get("close_time"),
    }
    if minutes_left is not None:
        trade["minutes_left"] = float(minutes_left)
    return trade


def _with_settlement(trade, market, entry, size):
    settle = settlement_price(trade["side"], market.get("result"))
    trade["settle"] = settle
    if settle is None:
        trade["settle_pnl"] = None
    else:
        trade["settle_pnl"] = (float(settle) - float(entry)) * float(size)
    return trade


def replay_market(market, candlesticks, cfg, mode, sides=("UP", "DOWN"), stop_fill="path"):
    """One 15-minute contract. covered is False when no usable bar survived."""
    close_ts = market.get("close_ts")
    if close_ts is None:
        close_ts = parse_time(market.get("close_time"))
    bars = []
    if close_ts is not None:
        for raw in candlesticks or []:
            end = raw.get("end_period_ts")
            if end is None:
                continue
            end = int(end)
            if end > close_ts:
                continue
            up, down = side_books(raw)
            bars.append((end, (close_ts - end) / 60.0, up, down))
    bars.sort(key=lambda row: row[0])
    covered = False
    for _end, _mins, up, down in bars:
        if any(value is not None for value in up + down):
            covered = True
            break
    out = {"covered": covered, "trade": None, "unresolved": False}
    if close_ts is None:
        return out
    delta = delta_value(market, cfg)
    allowed = set(sides)
    side = None
    entry = None
    entry_mins = None
    size = cfg.order_size
    for _end, mins, up, down in bars:
        if side is None:
            if not (mins > 0 and mins <= float(cfg.time_delay) + 1e-9):
                continue
            up_px, down_px = entry_quotes(up, down, cfg, mode)
            if "UP" not in allowed:
                up_px = None
            if "DOWN" not in allowed:
                down_px = None
            picked, price, _reason = choose_side(
                up_px, down_px, cfg.entry_min, cfg.entry_max, delta, cfg.delta)
            if picked is None:
                continue
            side, entry, entry_mins = picked, price, mins
            continue
        ohlc = up if side == "UP" else down
        reason, exit_px = exit_signal(ohlc, entry, cfg, mode, stop_fill=stop_fill)
        if reason is None:
            continue
        out["trade"] = _with_settlement(
            _trade(market, side, entry, exit_px, reason, size, entry_mins),
            market, entry, size)
        return out
    if side is not None:
        exit_px = settlement_price(side, market.get("result"))
        if exit_px is None:
            out["unresolved"] = True
            return out
        out["trade"] = _with_settlement(
            _trade(market, side, entry, exit_px, "settlement", size, entry_mins),
            market, entry, size)
    return out


def daily_halt(pnl, cfg):
    """Same cent comparison as PaperEngine.check_daily. None if still running."""
    pnl = round(float(pnl), 2)
    target = round(float(cfg.daily_profit_target), 2)
    if target > 0 and pnl > target:
        return "daily_profit"
    stop = round(float(cfg.daily_loss_target), 2)
    if stop < 0 and pnl < stop:
        return "daily_loss"
    return None


def replay_book(markets, candles_for, cfg, mode, kill, sides=("UP", "DOWN"), stop_fill="path"):
    """Fresh account. markets are slim dicts with close_ts. kill stops the process."""
    rows = sorted(markets, key=lambda m: (m["close_ts"], m.get("ticker") or ""))
    balance = float(cfg.initial_balance)
    wins = 0
    losses = 0
    reasons = {"stop": 0, "take_profit": 0, "settlement": 0}
    trades = 0
    with_bars = 0
    missing = 0
    no_floor = 0
    unresolved = 0
    halt = None
    size = float(cfg.order_size)
    prepared = []
    for market in rows:
        if not has_floor(market):
            no_floor += 1
        candles = candles_for(market["ticker"])
        if candles is None:
            missing += 1
            prepared.append(None)
            continue
        outcome = replay_market(
            market, candles, cfg, mode, sides=sides, stop_fill=stop_fill)
        if outcome["covered"]:
            with_bars += 1
        prepared.append(outcome)
    for outcome in prepared:
        if halt:
            break
        if not outcome or not outcome.get("trade"):
            if outcome and outcome.get("unresolved"):
                unresolved += 1
            continue
        trade = outcome["trade"]
        cost = float(trade["entry"]) * size
        if cost > balance + 1e-9:
            halt = "insufficient_balance"
            break
        balance -= cost
        balance += float(trade["exit"]) * size
        trades += 1
        if trade["pnl"] > 0:
            wins += 1
        else:
            losses += 1
        reasons[trade["reason"]] = reasons.get(trade["reason"], 0) + 1
        if kill:
            halt = daily_halt(balance - float(cfg.initial_balance), cfg)
            if halt:
                break
    return {
        "markets": len(rows),
        "with_bars": with_bars,
        "missing": missing,
        "no_floor": no_floor,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "pnl": round(balance - float(cfg.initial_balance), 2),
        "end_cash": round(balance, 2),
        "halt": halt,
        "reasons": reasons,
        "unresolved": unresolved,
        "first_close": rows[0]["close_time"] if rows else None,
        "last_close": rows[-1]["close_time"] if rows else None,
    }


def window_is_short(earliest_ts, start_ts):
    """True when the archive starts after the requested window."""
    return earliest_ts > start_ts + 15 * 60


def slice_window(markets, days, end_ts):
    start = end_ts - int(days) * 86400
    chosen = [m for m in markets if start <= m["close_ts"] <= end_ts]
    return chosen, start


def ticker_sig(markets):
    names = sorted(m["ticker"] for m in markets)
    digest = hashlib.sha1("\n".join(names).encode("utf-8")).hexdigest()[:12]
    return digest, len(names)


def slim_market(raw, source):
    ticker = raw.get("ticker") or ""
    close_ts = parse_time(raw.get("close_time"))
    floor = raw.get("floor_strike")
    try:
        if floor is not None and floor != "":
            floor = float(floor)
        else:
            floor = None
    except (TypeError, ValueError):
        floor = None
    return {
        "ticker": ticker,
        "close_time": raw.get("close_time"),
        "open_time": raw.get("open_time"),
        "result": (raw.get("result") or "").lower(),
        "floor_strike": floor,
        "close_ts": close_ts,
        "source": source,
    }


def page_markets(get, path, params, min_close_ts):
    """Newest-first pages. Stop once a page reaches before min_close_ts."""
    cursor = None
    out = []
    while True:
        query = dict(params)
        query["limit"] = 1000
        if cursor:
            query["cursor"] = cursor
        body = get(path, query)
        if not body:
            break
        markets = body.get("markets") or []
        if not markets:
            break
        oldest = None
        for raw in markets:
            ts = parse_time(raw.get("close_time"))
            if ts is None:
                continue
            if oldest is None or ts < oldest:
                oldest = ts
            if ts >= min_close_ts:
                out.append(raw)
        cursor = body.get("cursor") or ""
        if not cursor or oldest is None or oldest < min_close_ts:
            break
    return out


def list_settled(min_close_ts, get=None, series=SERIES):
    """Live settled markets, then the historical archive. Live wins a duplicate."""
    get = get or kalshi_get
    found = {}
    order = []

    def add(rows, source):
        for raw in rows:
            ticker = raw.get("ticker") or ""
            if not ticker.startswith(series):
                continue
            if not _TICKER_OK.match(ticker):
                continue
            slim = slim_market(raw, source)
            if slim["close_ts"] is None or slim["close_ts"] < min_close_ts:
                continue
            if ticker in found:
                continue
            found[ticker] = slim
            order.append(ticker)

    live = page_markets(
        get, "/markets",
        {"series_ticker": series, "status": "settled"},
        min_close_ts)
    add(live, "live")
    hist = page_markets(
        get, "/historical/markets",
        {"series_ticker": series},
        min_close_ts)
    add(hist, "historical")
    return [found[ticker] for ticker in order]


def candle_paths(market, series=SERIES):
    ticker = market["ticker"]
    series_path = "/series/{}/markets/{}/candlesticks".format(series, ticker)
    hist_path = "/historical/markets/{}/candlesticks".format(ticker)
    if market.get("source") == "historical":
        return [hist_path, series_path]
    return [series_path, hist_path]


def fetch_candles(market, get=None):
    """Minute bars for one contract. Empty list is a real empty response."""
    get = get or kalshi_get
    close_ts = market.get("close_ts") or parse_time(market.get("close_time"))
    open_ts = parse_time(market.get("open_time"))
    if close_ts is None:
        raise ValueError("missing close_time for {}".format(market.get("ticker")))
    if open_ts is None:
        open_ts = close_ts - 15 * 60
    params = {
        "start_ts": int(open_ts) - 60,
        "end_ts": int(close_ts) + 180,
        "period_interval": 1,
    }
    empty = None
    for path in candle_paths(market):
        body = get(path, params)
        if not body:
            continue
        bars = body.get("candlesticks")
        if bars:
            return bars
        if empty is None:
            empty = []
    if empty is None:
        raise ConnectionError(market.get("ticker") or "candles")
    return empty


def cache_file(cache_dir, ticker):
    if not _TICKER_OK.match(ticker):
        raise ValueError("unsafe ticker {!r}".format(ticker))
    return os.path.join(cache_dir, ticker + ".json")


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, separators=(",", ":"))
        handle.write("\n")
    os.replace(tmp, path)


def manifest_path(cache_dir):
    return os.path.join(cache_dir, "_markets.json")


def load_manifest(cache_dir):
    path = manifest_path(cache_dir)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    out = []
    for row in data:
        if row.get("close_ts") is None:
            row = dict(row)
            row["close_ts"] = parse_time(row.get("close_time"))
        if row.get("close_ts") is None or not row.get("ticker"):
            continue
        out.append(row)
    return out


def load_candles(cache_dir, ticker):
    """List of bars, [] when the file exists and is empty, None when missing."""
    path = cache_file(cache_dir, ticker)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        blob = json.load(handle)
    bars = blob.get("candlesticks")
    if bars is None:
        return None
    return bars


def download(cache_dir, min_close_ts, workers=8, get=None):
    """Write the manifest, then candle files. Existing candle files are kept."""
    os.makedirs(cache_dir, exist_ok=True)
    markets = list_settled(min_close_ts, get=get)
    if not markets:
        raise RuntimeError("no settled {} markets at or after {}".format(
            SERIES, iso(min_close_ts)))
    atomic_json(manifest_path(cache_dir), markets)
    todo = []
    for market in markets:
        if os.path.exists(cache_file(cache_dir, market["ticker"])):
            continue
        todo.append(market)
    print("markets {}  cached {}  to fetch {}".format(
        len(markets), len(markets) - len(todo), len(todo)), flush=True)
    errors = []
    if not todo:
        return markets

    def one(market):
        bars = fetch_candles(market, get=get)
        atomic_json(cache_file(cache_dir, market["ticker"]), {
            "market": market,
            "candlesticks": bars,
        })
        return len(bars)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = {pool.submit(one, market): market for market in todo}
        for future in as_completed(futures):
            market = futures[future]
            done += 1
            try:
                future.result()
            except Exception as exc:
                errors.append("{} {}".format(market["ticker"], exc))
            if done % 200 == 0 or done == len(todo):
                print("candles {}/{}  errors {}".format(
                    done, len(todo), len(errors)), flush=True)
    if errors:
        with open(os.path.join(cache_dir, "_errors.txt"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(errors))
            handle.write("\n")
        print("failed {} — rerun to retry the misses".format(len(errors)), flush=True)
    return markets


def run_windows(markets, candles_for, cfg, months, end_ts=None):
    if not markets:
        return []
    if end_ts is None:
        end_ts = max(m["close_ts"] for m in markets)
    earliest = min(m["close_ts"] for m in markets)
    rows = []
    for month in months:
        days = WINDOW_DAYS[month]
        chosen, start = slice_window(markets, days, end_ts)
        sig, count = ticker_sig(chosen)
        short = window_is_short(earliest, start) if chosen or markets else True
        for mode in ("close", "touch"):
            for kill in (True, False):
                result = replay_book(chosen, candles_for, cfg, mode, kill)
                result.update({
                    "month": month,
                    "days": days,
                    "mode": mode,
                    "kill": bool(kill),
                    "start": iso(start),
                    "end": iso(end_ts),
                    "history_short": short,
                    "sig": sig,
                    "sig_n": count,
                })
                rows.append(result)
    return rows


def render_report(rows):
    lines = [
        "KXBTC15M paper replay. Fees off. Not an rhengine.alpha.book result.",
    ]
    if not rows:
        lines.append("No settled markets in the cache.")
        return "\n".join(lines)
    lines.append("Windows end at {}.".format(rows[0]["end"]))
    seen = []
    last_key = None
    for row in rows:
        key = (row["month"], row["sig"])
        if key != last_key:
            label = "{} month / {}d  {} -> {}  markets {}  bars {}  missing {}  no_floor {}".format(
                row["month"], row["days"], row["first_close"], row["last_close"],
                row["markets"], row["with_bars"], row["missing"], row["no_floor"])
            if row.get("history_short"):
                label += "  SHORT (archive starts after {})".format(row["start"])
            else:
                label += "  full window"
            lines.append(label)
            if row["markets"] and row["with_bars"] < 0.95 * row["markets"]:
                lines.append("INCOMPLETE  bars cover {:.1%} of markets. Rerun to fill misses.".format(
                    row["with_bars"] / row["markets"]))
            last_key = key
            seen.append((row["month"], row["sig"], row["sig_n"]))
        halt = row["halt"] or "-"
        reasons = row["reasons"]
        lines.append(
            "  {mode:<5}  kill {kill:<3}  trades {trades:<5}  W {wins:<5}  L {losses:<5}  "
            "pnl {pnl:>8}  cash {cash:>8}  halt {halt:<20}  "
            "stop {stop:<5} tp {tp:<5} settle {settle:<5}".format(
                mode=row["mode"],
                kill="on" if row["kill"] else "off",
                trades=row["trades"],
                wins=row["wins"],
                losses=row["losses"],
                pnl=format_pnl(row["pnl"]),
                cash=format_money(row["end_cash"]),
                halt=halt,
                stop=reasons.get("stop", 0),
                tp=reasons.get("take_profit", 0),
                settle=reasons.get("settlement", 0),
            ))
    sigs = {}
    for month, sig, count in seen:
        sigs.setdefault(sig, []).append("{}mo".format(month))
    for sig, months in sigs.items():
        if len(months) > 1:
            lines.append("Same market set for {}.".format(", ".join(months)))
    lines.extend(NOTES)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay the Kalshi v5 paper rules on KXBTC15M minute bars. Places no orders.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--months", nargs="+", type=int, default=[1, 3, 6])
    parser.add_argument("--offline", action="store_true",
                        help="Replay the cache only. Do not call Kalshi.")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    unknown = [m for m in args.months if m not in WINDOW_DAYS]
    if unknown:
        parser.error("--months must be 1, 3, or 6, got {}".format(unknown))
    cfg, errors = load_config(args.config)
    if errors:
        for line in errors:
            print(line)
        return 1
    if "BTC" not in cfg.assets:
        print("Config assets do not include BTC. This replay is KXBTC15M only.")
        return 1
    if not args.offline:
        # Pad two days so a latest close a few hours behind `now` still
        # has its full longest window on disk.
        span = max(WINDOW_DAYS[m] for m in args.months) + 2
        min_close = int(time.time()) - span * 86400
        try:
            download(args.cache, min_close, workers=args.workers)
        except Exception as exc:
            print("download failed: {}".format(exc))
            return 1
    markets = load_manifest(args.cache)
    if not markets:
        print("No settled markets in {}.".format(args.cache))
        print("Run without --offline to fill the cache.")
        return 1

    def candles_for(ticker):
        return load_candles(args.cache, ticker)

    rows = run_windows(markets, candles_for, cfg, args.months)
    text = render_report(rows)
    print(text)
    atomic_json(os.path.join(args.cache, "_replay.json"), {
        "rows": rows,
        "notes": list(NOTES),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
