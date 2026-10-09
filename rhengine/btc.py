"""
btc.py — does anything predict the next hour of bitcoin?

Aimed squarely at `KXBTCD`, Kalshi's hourly BTC digital: 3.68M contracts/24h,
318 strikes, settles on a 60-second BRTI average at the top of each hour. The
forecast horizon here is one hour because that is the horizon that trades.

Three rules this module exists to enforce, learned from the four books already
measured and closed in this repo:

1. **Score against the price, not against reality.** A signal that predicts
   bitcoin is worthless if the ladder already prices it. The fee makes that
   concrete: at the 50/50 point where an hourly digital trades, the Kalshi taker
   fee peaks at 1.75 c, so a directional signal must be right **51.75%** of the
   time before it pays for itself. `net_hit_rate()` reports the gap to that bar,
   not the raw hit rate.
2. **Pre-register, then log.** The eleven features below were fixed and written
   to the trial ledger (family `btc`) BEFORE the first IC was computed. At
   eleven trials the family-wise bar is t = 2.83 and luck alone scores
   E[max t] = 1.62. A feature reporting t = 2.1 is noise wearing a result's hat.
3. **Catch the error you expect, not everything.** A bare `except Exception`
   swallowed a NameError 150 times in the weather book and reported it as
   missing data.

Data
----
Coinbase hourly closes, validated against the settlement index itself: BRTI
prints recovered from settled Kalshi markets sit **2.0 bps** from the Coinbase
minute close at the settlement instant (sd 4.1 bps) against a ~92 bps hourly
move. See `crypto.proxy_basis()`. 13,490 bars, 2025-02-05 to 2026-08-22, two
non-hourly gaps.

The panel is cached at `research_data/crypto/btc_hourly.json` so a re-run does
not re-page 45 Coinbase requests.

What is deliberately NOT here
-----------------------------
No sweep. Eleven features, one horizon, one target. The 2026-08-14 video intake
recorded what a result drawn from 25,000 backtests is worth; this module is the
opposite of that by construction, and adding a twelfth feature means adding a
twelfth ledger line first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics as st
import sys
import time

import requests

from ._paths import app_path
from .market_data_kalshi import _kalshi_get, fetch_prob_bars
from .xvenue import kalshi_fee

COINBASE = "https://api.exchange.coinbase.com"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
PANEL = os.path.join("research_data", "crypto", "btc_hourly.json")
PANEL_BFX = os.path.join("research_data", "crypto", "btc_hourly_bitfinex.json")
BITFINEX = "https://api-pub.bitfinex.com/v2/candles/trade:1h:tBTCUSD/hist"

#: Pre-registered. Eleven, logged to the ledger before the first result.
FEATURES = ("ret_1h", "ret_4h", "ret_24h", "ret_168h", "rsi_14", "rv_24h",
            "rv_ratio_24_168", "vol_z_168", "range_pos_24h", "hour_of_day",
            "day_of_week")

#: The hourly digital trades near 50/50, where the Kalshi fee is at its maximum.
DIGITAL_PRICE = 0.50

#: Half-spread paid on entry. The tight KXBTCD strikes quote 1 c wide (measured
#: 2026-08-22 over 708 strikes), and a taker crosses half of it. Charging only
#: the fee understates the bar by a full point and turns two losing signals into
#: winners.
HALF_SPREAD = 0.005


# -------------------------------------------------------------------- data ----

def fetch_panel(hours=13500, product="BTC-USD", pause=0.22, path=None):
    """Hourly OHLCV from Coinbase, paged 300 at a time, cached to disk."""
    path = path or app_path(PANEL)
    rows, end = {}, dt.datetime.now(dt.timezone.utc)
    for _ in range(hours // 300 + 1):
        start = end - dt.timedelta(hours=300)
        r = requests.get(COINBASE + "/products/{}/candles".format(product),
                         params={"granularity": 3600, "start": start.isoformat(),
                                 "end": end.isoformat()}, timeout=30)
        if not r.ok:
            break
        data = r.json()
        if not data:
            break
        for c in data:
            rows[c[0]] = c            # [ts, low, high, open, close, volume]
        end = start
        time.sleep(pause)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({str(k): rows[k] for k in sorted(rows)}, f)
    return rows


def fetch_bitfinex(start_year=2015, pause=2.5, max_requests=20, path=None):
    """Deep hourly history, keyless, 10,000 candles per request.

    Coinbase serves 300 bars a call and this serves 10,000, so 11.6 years
    arrives in eleven requests instead of four hundred. Measured 2026-08-22:
    101,810 bars from 2015-01-01, 18 non-hourly gaps (0.02%). Data exists back
    to 2013-03-31.

    **Not a settlement proxy.** Bitfinex sits +8.4 bps from Coinbase with an sd
    of 11.2 bps, against Coinbase's 2.0 bps (sd 4.1) from the BRTI prints Kalshi
    actually settles on. Level noise of 11 bps means hourly-return noise near
    16 bps against a ~92 bps move, which attenuates any IC measured on it. Use
    Coinbase for anything touching a settlement and this for long-run history.
    """
    path = path or app_path(PANEL_BFX)
    rows = {}
    cur = int(dt.datetime(start_year, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    now_ms = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    for _ in range(max_requests):
        r = requests.get(BITFINEX, timeout=40, headers={"User-Agent": "rhengine"},
                         params={"limit": 10000, "start": cur, "sort": 1})
        if not r.ok:
            break
        data = r.json()
        if not data:
            break
        for c in data:                       # [ts, open, close, high, low, volume]
            rows[c[0] // 1000] = c
        nxt = data[-1][0] + 3600000
        if nxt <= cur or nxt > now_ms:
            break
        cur = nxt
        time.sleep(pause)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({str(k): rows[k] for k in sorted(rows)}, f)
    return rows


def load_bitfinex(path=None, refresh=False):
    """Bitfinex rows mapped into the panel shape (ts, low, high, open, close, vol)."""
    path = path or app_path(PANEL_BFX)
    if refresh or not os.path.exists(path):
        fetch_bitfinex(path=path)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for k, c in sorted(raw.items(), key=lambda kv: int(kv[0])):
        # bitfinex order is [ts, open, close, high, low, volume]
        out.append((int(k), c[4], c[3], c[1], c[2], c[5]))
    return out


def by_year(rows, feature="range_pos_24h", quantile=0.2):
    """IC and execution-adjusted edge for one feature, per calendar year.

    Part 1 measured the IC halving over eighteen months and could not say
    whether that was decay or noise. Eleven years answers it.
    """
    ic_all, _ = spearman([f[feature] for _, f, _ in rows], [t for _, _, t in rows])
    sign = -1 if (ic_all or 0) < 0 else 1
    buckets = {}
    for ts, f, y in rows:
        buckets.setdefault(dt.datetime.fromtimestamp(ts, dt.timezone.utc).year,
                           []).append((f[feature], y))
    out = []
    for year in sorted(buckets):
        xs = [a for a, _ in buckets[year]]
        ys = [b for _, b in buckets[year]]
        if len(xs) < 500:
            continue
        ic, t = spearman(xs, ys)
        hit = net_hit_rate(xs, ys, sign=sign, quantile=quantile)
        out.append({"year": year, "n": len(xs),
                    "ic": None if ic is None else round(ic, 5),
                    "t": None if t is None else round(t, 2),
                    "hit": hit["hit_rate"], "edge_pts": hit["edge_pts"]})
    return sign, out


def load_panel(path=None, refresh=False):
    """[(ts, low, high, open, close, volume)] ascending."""
    path = path or app_path(PANEL)
    if refresh or not os.path.exists(path):
        fetch_panel(path=path)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [tuple([int(k)] + list(v[1:])) for k, v in
            sorted(raw.items(), key=lambda kv: int(kv[0]))]


# ---------------------------------------------------------------- features ----

def _rsi(closes, n=14):
    """Wilder RSI over a closing series. None until n+1 points exist."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def build(panel, max_lb=168):
    """[(ts, {feature: value}, forward_1h_log_return)] — no lookahead.

    Every feature at index i uses bars <= i; the target is the log return from
    close[i] to close[i+1]. Bars without a full 168-hour history are dropped
    rather than padded.
    """
    closes = [r[4] for r in panel]
    highs = [r[2] for r in panel]
    lows = [r[1] for r in panel]
    vols = [r[5] for r in panel]
    lr = [0.0] + [math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 else 0.0
                  for i in range(1, len(closes))]
    out = []
    for i in range(max_lb, len(panel) - 1):
        c = closes[i]
        if c <= 0 or closes[i + 1] <= 0:
            continue
        w24, w168 = lr[i - 23:i + 1], lr[i - 167:i + 1]
        rv24 = st.pstdev(w24) if len(w24) > 2 else 0.0
        rv168 = st.pstdev(w168) if len(w168) > 2 else 0.0
        v168 = vols[i - 167:i + 1]
        vmean, vsd = st.mean(v168), st.pstdev(v168)
        hi24, lo24 = max(highs[i - 23:i + 1]), min(lows[i - 23:i + 1])
        when = dt.datetime.fromtimestamp(panel[i][0], dt.timezone.utc)
        f = {
            "ret_1h": lr[i],
            "ret_4h": math.log(c / closes[i - 4]),
            "ret_24h": math.log(c / closes[i - 24]),
            "ret_168h": math.log(c / closes[i - 168]),
            "rsi_14": _rsi(closes[:i + 1], 14),
            "rv_24h": rv24,
            "rv_ratio_24_168": (rv24 / rv168) if rv168 > 0 else None,
            "vol_z_168": ((vols[i] - vmean) / vsd) if vsd > 0 else None,
            "range_pos_24h": ((c - lo24) / (hi24 - lo24)) if hi24 > lo24 else None,
            "hour_of_day": float(when.hour),
            "day_of_week": float(when.weekday()),
        }
        if any(v is None for v in f.values()):
            continue
        out.append((panel[i][0], f, math.log(closes[i + 1] / c)))
    return out


# ----------------------------------------------------------------- scoring ----

def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):                 # average ties
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    """Rank correlation, and the t-stat of its difference from zero."""
    n = len(xs)
    if n < 30:
        return None, None
    rx, ry = _rank(xs), _rank(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if den == 0:
        return None, None
    ic = num / den
    if abs(ic) >= 1.0:
        return ic, None
    return ic, ic * math.sqrt((n - 2) / (1.0 - ic * ic))


def net_hit_rate(feature_vals, targets, quantile=0.2, price=DIGITAL_PRICE,
                 contracts=100, sign=1, half_spread=HALF_SPREAD):
    """Directional hit rate of the feature's tails, against the fee bar.

    `sign` orients the trade. A feature with NEGATIVE IC is traded backwards —
    long its bottom quantile, short its top — so scoring it long-top always
    reports the losing side. The first run of this module did exactly that and
    showed five features "clearing" with hit rates of 0.46, which is a 0.54
    signal read upside down. Callers pass sign=+1/-1 from the IC.

    `bar` is the rate at which a digital at `price` breaks even after the Kalshi
    taker fee — 51.76% at the midpoint, which is where an hourly BTC digital
    actually trades.
    """
    n = len(feature_vals)
    k = max(30, int(n * quantile))
    order = sorted(range(n), key=lambda i: feature_vals[i])
    lo, hi = order[:k], order[-k:]
    if sign < 0:
        lo, hi = hi, lo
    wins = sum(1 for i in hi if targets[i] > 0) + sum(1 for i in lo if targets[i] < 0)
    total = len(hi) + len(lo)
    rate = wins / total if total else None
    bar = price + kalshi_fee(price, contracts) / contracts + half_spread
    se = math.sqrt(0.25 / total) if total else None
    return {"hit_rate": None if rate is None else round(rate, 4),
            "n": total, "bar": round(bar, 4),
            "sign": sign,
            "edge_pts": None if rate is None else round((rate - bar) * 100, 3),
            "t_vs_bar": None if rate is None else round((rate - bar) / se, 3)}


def split_score(rows, features=FEATURES, train_frac=0.6):
    """Fit the sign in-sample, then score the SAME orientation out-of-sample.

    A chronological split, not random: shuffling hourly bars leaks the future
    into the past through overlapping lookbacks. The out-of-sample hit rate is
    the only column worth reading — the in-sample one chose its own direction.
    """
    cut = int(len(rows) * train_frac)
    tr, te = rows[:cut], rows[cut:]
    y_tr = [t for _, _, t in tr]
    y_te = [t for _, _, t in te]
    out = []
    for name in features:
        ic_tr, t_tr = spearman([f[name] for _, f, _ in tr], y_tr)
        sign = -1 if (ic_tr or 0) < 0 else 1
        ic_te, t_te = spearman([f[name] for _, f, _ in te], y_te)
        hit_te = net_hit_rate([f[name] for _, f, _ in te], y_te, sign=sign)
        out.append({"feature": name, "sign": sign,
                    "ic_train": None if ic_tr is None else round(ic_tr, 5),
                    "t_train": None if t_tr is None else round(t_tr, 2),
                    "ic_test": None if ic_te is None else round(ic_te, 5),
                    "t_test": None if t_te is None else round(t_te, 2),
                    "same_sign": (ic_te or 0) * (ic_tr or 0) > 0,
                    "n_train": len(tr), "n_test": len(te), **hit_te})
    out.sort(key=lambda r: -(r["edge_pts"] or -99))
    return out


def score(rows, features=FEATURES):
    """[{feature, ic, t, hit_rate, edge_pts, t_vs_bar}] sorted by |t|."""
    targets = [t for _, _, t in rows]
    out = []
    for name in features:
        vals = [f[name] for _, f, _ in rows]
        ic, t = spearman(vals, targets)
        hit = net_hit_rate(vals, targets, sign=(-1 if (ic or 0) < 0 else 1))
        out.append({"feature": name, "n": len(rows),
                    "ic": None if ic is None else round(ic, 5),
                    "t": None if t is None else round(t, 3), **hit})
    out.sort(key=lambda r: -abs(r["t"] or 0))
    return out


def stability(rows, feature, periods=6, quantile=0.2):
    """Out-of-fee edge for one feature, chopped into equal chronological blocks.

    An edge carried by one stretch is a stretch, not an edge. This is the check
    that separates the two, and it is cheap.
    """
    ic_all, _ = spearman([f[feature] for _, f, _ in rows], [t for _, _, t in rows])
    sign = -1 if (ic_all or 0) < 0 else 1
    size = len(rows) // periods
    out = []
    for p in range(periods):
        blk = rows[p * size:(p + 1) * size] if p < periods - 1 else rows[p * size:]
        if len(blk) < 200:
            continue
        y = [t for _, _, t in blk]
        ic, t = spearman([f[feature] for _, f, _ in blk], y)
        hit = net_hit_rate([f[feature] for _, f, _ in blk], y, sign=sign,
                           quantile=quantile)
        out.append({"block": p + 1, "n": len(blk),
                    "from": dt.datetime.fromtimestamp(blk[0][0], dt.timezone.utc).date().isoformat(),
                    "ic": None if ic is None else round(ic, 5),
                    "t": None if t is None else round(t, 2),
                    "hit": hit["hit_rate"], "edge_pts": hit["edge_pts"]})
    return sign, out


def conditional(rows, feature="ret_4h", by="rv_24h", buckets=3, quantile=0.2):
    """Is the edge a constant, or a regime?

    `ret_4h`'s execution-adjusted edge sits entirely in the last third of the
    sample, which is also the highest-volatility stretch. Mean reversion is
    expected to be stronger in high-vol chop, so the honest question is whether
    the edge is conditional on volatility rather than on the calendar. Splitting
    by `by` answers it, and a conditional edge is a different (and much more
    fragile) product from a constant one.
    """
    ic_all, _ = spearman([f[feature] for _, f, _ in rows], [t for _, _, t in rows])
    sign = -1 if (ic_all or 0) < 0 else 1
    order = sorted(rows, key=lambda r: r[1][by])
    size = len(order) // buckets
    out = []
    for b in range(buckets):
        blk = order[b * size:(b + 1) * size] if b < buckets - 1 else order[b * size:]
        y = [t for _, _, t in blk]
        ic, t = spearman([f[feature] for _, f, _ in blk], y)
        hit = net_hit_rate([f[feature] for _, f, _ in blk], y, sign=sign,
                           quantile=quantile)
        vals = [r[1][by] for r in blk]
        out.append({"bucket": b + 1, "n": len(blk),
                    "by_lo": round(min(vals), 6), "by_hi": round(max(vals), 6),
                    "ic": None if ic is None else round(ic, 5),
                    "t": None if t is None else round(t, 2),
                    "hit": hit["hit_rate"], "edge_pts": hit["edge_pts"]})
    return sign, out


# ------------------------------------------------- scoring against the price ----
#
# Everything above predicts the SIGN of the next hourly return. That is not the
# same claim as "the ladder is mispriced", and the weather book died on exactly
# that distinction. This section runs the harder test.

LADDER_LOG = os.path.join("research_data", "crypto", "ladder_scored.jsonl")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_EDT = dt.timezone(dt.timedelta(hours=-4))


def settled_hours(series="KXBTCD", pages=25, pause=0.1):
    """{settle_datetime: {strike: (result, expiration_value, ticker)}}.

    Only the upper-tail `-T<strike>` markets. Kalshi retains roughly six days of
    settled hourly markets, which is the binding constraint on this whole test:
    135 hours gives a standard error of 4.3 points on a hit rate, against an
    edge of about 1.6. Underpowered by an order of magnitude, and the accumulator
    below exists for that reason.
    """
    import re as _re
    out, cursor = {}, None
    for _ in range(pages):
        params = {"limit": 1000, "status": "settled", "series_ticker": series}
        if cursor:
            params["cursor"] = cursor
        body = _kalshi_get("/markets", params=params)
        markets = body.get("markets", [])
        for m in markets:
            g = _re.match(r"[A-Z]+-(\d{2})([A-Z]{3})(\d{2})(\d{2})-T([\d.]+)$",
                          m.get("ticker", ""))
            v = m.get("expiration_value")
            if not g or g.group(2) not in _MONTHS or v in (None, ""):
                continue
            try:
                when = dt.datetime(2000 + int(g.group(1)), _MONTHS[g.group(2)],
                                   int(g.group(3)), int(g.group(4)), tzinfo=_EDT)
                out.setdefault(when, {})[float(g.group(5))] = (
                    m.get("result"), float(v), m["ticker"])
            except (ValueError, KeyError):
                continue
        cursor = body.get("cursor")
        if not cursor or not markets:
            break
        time.sleep(pause)
    return out


def settled_quote(series, ticker, settle_ts, skip=2, take=5, max_spread=0.03,
                  timeout=30):
    """A robust early quote: median mid over the first few TIGHT minutes.

    The very first print of a just-opened hourly market is not a price. Measured
    on one market: 09:01 quoted 0.43/0.44 and 09:02 quoted 0.51/0.52, a nine
    point jump in sixty seconds. Scoring the market against that first print
    produced a calibration table where its "under 20%" bucket realised 44%,
    which is a measurement artifact and not a market failure.

    Skips the first `skip` valid minutes, then takes the median mid of up to
    `take` minutes whose spread is at most `max_spread`. Returns
    (mid, median_spread, minutes_used, first_ts).
    """
    r = requests.get(
        "{}/series/{}/markets/{}/candlesticks".format(KALSHI_BASE, series, ticker),
        params={"start_ts": settle_ts - 2 * 3600, "end_ts": settle_ts + 300,
                "period_interval": 1}, timeout=timeout)
    if not r.ok:
        return None, None, 0, None
    mids, spreads, first = [], [], None
    seen = 0
    for c in r.json().get("candlesticks", []):
        b = (c.get("yes_bid") or {}).get("close_dollars")
        a = (c.get("yes_ask") or {}).get("close_dollars")
        if b is None or a is None:
            continue
        bid, ask = float(b), float(a)
        if not (0.0 < bid < 1.0 and 0.0 < ask < 1.0 and ask >= bid):
            continue
        seen += 1
        if seen <= skip or (ask - bid) > max_spread:
            continue
        if first is None:
            first = c["end_period_ts"]
        mids.append((bid + ask) / 2.0)
        spreads.append(ask - bid)
        if len(mids) >= take:
            break
    if not mids:
        return None, None, 0, None
    return (round(st.median(mids), 4), round(st.median(spreads), 4),
            len(mids), first)


def opening_quote(series, ticker, settle_ts, timeout=30):
    """(yes_bid, yes_ask) at the moment the hourly market opens.

    These markets exist for ONE hour: the market for a 10:00 settlement opens
    around 09:01 and is gone by 10:01. So hourly candlesticks give two bars,
    neither of them before the decision time, and there is no "quote an hour
    earlier" to read -- the opening print IS the tradable decision point,
    roughly 59 minutes before settlement.

    Minute granularity needs a narrow window; `period_interval=1` over ten days
    is rejected with HTTP 400, and 5 is not a valid interval at all (only 1, 60
    and 1440).
    """
    r = requests.get(
        "{}/series/{}/markets/{}/candlesticks".format(KALSHI_BASE, series, ticker),
        params={"start_ts": settle_ts - 2 * 3600, "end_ts": settle_ts + 300,
                "period_interval": 1}, timeout=timeout)
    if not r.ok:
        return None, None, None
    for c in r.json().get("candlesticks", []):
        b = (c.get("yes_bid") or {}).get("close_dollars")
        a = (c.get("yes_ask") or {}).get("close_dollars")
        if b is None or a is None:
            continue
        bid, ask = float(b), float(a)
        if 0.0 < bid < 1.0 and 0.0 < ask < 1.0 and ask >= bid:
            return bid, ask, c["end_period_ts"]
    return None, None, None


def _brier(probs, outcomes):
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def ladder_test(series="KXBTCD", feature="range_pos_24h", pause=0.2, panel=None):
    """Does the signal add anything the ladder was not already quoting?

    For each settled hour: take the strike nearest spot at the decision time,
    read the market's own quote for it from hourly candlesticks, and compare
    three predictors against what actually settled --

        market      the ladder's implied P(BRTI > K)
        signal      the feature, mapped to a direction
        residual    does the signal predict (outcome - market_p)?

    The residual correlation is the one that matters. A signal that predicts the
    outcome but not the residual is a signal the market already has.
    """
    panel = panel if panel is not None else load_panel()
    rows = build(panel)
    feat = {ts: f[feature] for ts, f, _ in rows}
    closes = {r[0]: r[4] for r in panel}
    hours = settled_hours(series)
    recs = []
    for when in sorted(hours):
        # ALIGNMENT. Coinbase stamps a candle with its bucket START, so
        # closes[T] is the price at T+1h. Using closes[settle-3600] as "spot at
        # the decision time" therefore reads the SETTLEMENT price -- measured at
        # -0.4 bps (sd 2.7) from the BRTI print. The first version of this
        # function did exactly that: it chose the strike knowing the answer and
        # computed the feature through it, which manufactured a +0.28 partial
        # correlation and a 47-62% six-day ROI.
        #
        # The market for settlement S opens near S-59min, so the decision
        # information must be as of S-1h, which is closes[S - 2*3600]. The
        # feature keyed at ts uses data through ts+1h, so it needs the same key.
        key = int(when.timestamp()) - 2 * 3600
        if key not in feat or key not in closes:
            continue
        spot = closes[key]
        strikes = hours[when]
        K = min(strikes, key=lambda k: abs(k - spot))
        if abs(K - spot) / spot > 0.005:          # no strike near the money
            continue
        result, expval, ticker = strikes[K]
        try:
            mid, spread, nmin, qts = settled_quote(series, ticker,
                                                   int(when.timestamp()))
            time.sleep(pause)
        except (ConnectionError, requests.RequestException, OSError):
            continue
        if mid is None or nmin < 3:
            continue
        recs.append({"hour": when.isoformat(), "K": K, "spot": spot,
                     "offset_bps": round((K - spot) / spot * 1e4, 2),
                     "market_p": mid, "spread": spread, "minutes": nmin,
                     "quote_ts": qts, "feature": feat[key],
                     "outcome": 1 if expval > K else 0, "expval": expval})
    if len(recs) < 20:
        return {"error": "only {} usable hours".format(len(recs))}

    n = len(recs)
    mp = [r["market_p"] for r in recs]
    oc = [r["outcome"] for r in recs]
    fv = [r["feature"] for r in recs]
    resid = [o - p for o, p in zip(oc, mp)]
    # The feature has NEGATIVE IC on returns, so a high range position predicts
    # a lower chance of finishing above. Signal probability is the mirror.
    sig_p = [1.0 - v for v in fv]
    ic_out, t_out = spearman(fv, oc)
    ic_res, t_res = spearman(fv, resid)
    # Strike offset dominates the residual mechanically (IC -0.67 measured):
    # K sits within ~14 bps of spot, so whether you START above the strike
    # tilts the outcome far more than any signal. Any claim about the feature
    # must survive removing it.
    off = [r["offset_bps"] for r in recs]
    ic_fo, _ = spearman(fv, off)
    ic_ro, _ = spearman(off, resid)
    denom = math.sqrt(max(1e-12, (1 - (ic_fo or 0) ** 2) * (1 - (ic_ro or 0) ** 2)))
    partial = ((ic_res or 0) - (ic_fo or 0) * (ic_ro or 0)) / denom
    t_partial = (partial * math.sqrt(max(1, n - 3) / max(1e-12, 1 - partial ** 2))
                 if abs(partial) < 1 else None)
    return {"n": n, "span": "{}..{}".format(recs[0]["hour"][:16], recs[-1]["hour"][:16]),
            "market_brier": round(_brier(mp, oc), 5),
            "signal_brier": round(_brier(sig_p, oc), 5),
            "always_half_brier": round(_brier([0.5] * n, oc), 5),
            "market_hit": round(sum(1 for p, o in zip(mp, oc)
                                    if (p > 0.5) == (o == 1)) / n, 4),
            "signal_hit": round(sum(1 for p, o in zip(sig_p, oc)
                                    if (p > 0.5) == (o == 1)) / n, 4),
            "hit_se": round(math.sqrt(0.25 / n), 4),
            "ic_vs_outcome": None if ic_out is None else round(ic_out, 4),
            "t_vs_outcome": None if t_out is None else round(t_out, 2),
            "ic_vs_residual": None if ic_res is None else round(ic_res, 4),
            "t_vs_residual": None if t_res is None else round(t_res, 2),
            "ic_offset_residual": None if ic_ro is None else round(ic_ro, 4),
            "partial_ic": round(partial, 4),
            "t_partial": None if t_partial is None else round(t_partial, 2),
            "records": recs}


def append_ladder_log(recs, path=None):
    """Accumulate scored hours so the test gains power. Kalshi retains only six
    days of settled markets; at ~24 usable hours a day, three months of this log
    is what it takes to see a 1.6-point edge."""
    path = path or app_path(LADDER_LOG)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["hour"])
                except (ValueError, KeyError):
                    pass
    added = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in recs:
            if r["hour"] in seen:
                continue
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
            added += 1
    return added, path


def backtest_ladder(path=None, thresholds=(0.05, 0.10, 0.15), contracts=100,
                    sign=-1):
    """Actual P&L on the banked ladder quotes. Not IC — dollars.

    Per scored hour the signal gives P(finish above K); `sign=-1` is the
    mean-reversion mapping (high range position -> lower chance of finishing
    above). Trade only when the signal disagrees with the market by more than
    `threshold`, and cross to do it:

        buy YES  cost = ask + fee(ask)        pays 1 if outcome
        buy NO   cost = (1 - bid) + fee(1-bid) pays 1 if not outcome

    `bid`/`ask` are reconstructed from the recorded mid and spread. Reported per
    contract, so multiply by size. The three thresholds are pre-registered in
    the ledger; adding a fourth means adding a fourth ledger line first.
    """
    path = path or app_path(LADDER_LOG)
    if not os.path.exists(path):
        return {"error": "no ladder log"}
    recs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
    recs.sort(key=lambda r: r["hour"])
    out = []
    for thr in thresholds:
        pnl, wins, trades, staked = [], 0, 0, 0.0
        for r in recs:
            sig_up = (1.0 - r["feature"]) if sign < 0 else r["feature"]
            mid, half = r["market_p"], r.get("spread", 0.01) / 2.0
            bid, ask = max(0.01, mid - half), min(0.99, mid + half)
            if sig_up - ask > thr:
                cost = ask + kalshi_fee(ask, contracts) / contracts
                got = 1.0 if r["outcome"] == 1 else 0.0
            elif bid - sig_up > thr:
                px = 1.0 - bid
                cost = px + kalshi_fee(px, contracts) / contracts
                got = 1.0 if r["outcome"] == 0 else 0.0
            else:
                continue
            trades += 1
            staked += cost
            wins += 1 if got > 0 else 0
            pnl.append(got - cost)
        if trades < 5:
            out.append({"threshold": thr, "trades": trades, "note": "too few"})
            continue
        tot = sum(pnl)
        sd = st.pstdev(pnl) if len(pnl) > 1 else 0.0
        out.append({"threshold": thr, "trades": trades,
                    "hit": round(wins / trades, 4),
                    "pnl_per_contract": round(tot / trades, 5),
                    "total_pnl": round(tot, 3),
                    "roi_on_stake": round(tot / staked, 4) if staked else None,
                    "t": round((tot / trades) / (sd / math.sqrt(trades)), 2)
                         if sd > 0 else None})
    return {"n_hours": len(recs), "rows": out,
            "span": "{}..{}".format(recs[0]["hour"][:16], recs[-1]["hour"][:16])}


# -------------------------------------------------------------------- CLI ----

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--refresh", action="store_true", help="re-page Coinbase")
    ap.add_argument("--horizon", type=int, default=1, help="forward hours (default 1)")
    ap.add_argument("--source", default="coinbase", choices=["coinbase", "bitfinex"],
                    help="bitfinex gives 11.6y but is a noisier settlement proxy")
    ap.add_argument("--by-year", metavar="FEATURE",
                    help="IC and edge per calendar year — is the edge decaying?")
    ap.add_argument("--backtest", action="store_true",
                    help="P&L on the banked ladder quotes, in dollars per contract")
    ap.add_argument("--flip", action="store_true",
                    help="--backtest: trade the momentum mapping instead")
    ap.add_argument("--ladder", action="store_true",
                    help="score the signal against the ladder's own implied probability")
    ap.add_argument("--feature", default="range_pos_24h", help="--ladder: which feature")
    ap.add_argument("--conditional", metavar="FEATURE",
                    help="split one feature's edge by volatility regime")
    ap.add_argument("--by", default="rv_24h", help="--conditional: regime variable")
    ap.add_argument("--stability", metavar="FEATURE",
                    help="chop one feature's edge into 6 chronological blocks")
    ap.add_argument("--split", action="store_true",
                    help="chronological 60/40: pick the sign in-sample, score out")
    ap.add_argument("--required-t", type=float, default=2.83,
                    help="family-wise bar from `trials report --family btc`")
    a = ap.parse_args(argv)

    panel = (load_bitfinex(refresh=a.refresh) if a.source == "bitfinex"
             else load_panel(refresh=a.refresh))
    rows = build(panel)
    if a.horizon > 1:                     # re-target without rebuilding features
        closes = {r[0]: r[4] for r in panel}
        ts_sorted = [r[0] for r in panel]
        idx = {t: i for i, t in enumerate(ts_sorted)}
        rebased = []
        for ts, f, _ in rows:
            i = idx[ts]
            if i + a.horizon >= len(ts_sorted):
                continue
            c0, c1 = closes[ts_sorted[i]], closes[ts_sorted[i + a.horizon]]
            if c0 > 0 and c1 > 0:
                rebased.append((ts, f, math.log(c1 / c0)))
        rows = rebased
    span = (dt.datetime.fromtimestamp(rows[0][0], dt.timezone.utc).date(),
            dt.datetime.fromtimestamp(rows[-1][0], dt.timezone.utc).date())
    print("{} bars, {} scored, {} -> {}, forward {}h".format(
        len(panel), len(rows), span[0], span[1], a.horizon))
    if a.backtest:
        r = backtest_ladder(sign=(1 if a.flip else -1))
        if "error" in r:
            print(r["error"])
            return 1
        print("{} banked hours, {}   mapping: {}".format(
            r["n_hours"], r["span"], "momentum" if a.flip else "mean-reversion"))
        print("  {:>10}{:>9}{:>9}{:>16}{:>12}{:>9}".format(
            "threshold", "trades", "hit", "pnl/contract", "ROI", "t"))
        for row in r["rows"]:
            if "note" in row:
                print("  {:>10.2f}{:>9}{:>9}{:>16}{:>12}{:>9}".format(
                    row["threshold"], row["trades"], "-", row["note"], "-", "-"))
                continue
            print("  {:>10.2f}{:>9}{:>9.4f}{:>16.5f}{:>11.2%}{:>9}".format(
                row["threshold"], row["trades"], row["hit"],
                row["pnl_per_contract"], row["roi_on_stake"],
                "n/a" if row["t"] is None else "{:+.2f}".format(row["t"])))
        print("\nDollars per contract, fee and full crossed spread charged. "
              "At ~140 hours this cannot establish profitability — read the t "
              "against the family-wise bar of 3.03, not against 2.")
        return 0

    if a.by_year:
        sign, yrs = by_year(rows, a.by_year)
        print("{}  sign {:+d}  source {}".format(a.by_year, sign, a.source))
        print("  {:>6}{:>8}{:>11}{:>8}{:>9}{:>10}".format(
            "year", "n", "IC", "t", "hit", "edge_pts"))
        pos = 0
        for y in yrs:
            pos += 1 if (y["edge_pts"] or 0) > 0 else 0
            print("  {:>6}{:>8}{:>11}{:>8}{:>9}{:>10}".format(
                y["year"], y["n"],
                "n/a" if y["ic"] is None else "{:+.5f}".format(y["ic"]),
                "n/a" if y["t"] is None else "{:+.2f}".format(y["t"]),
                "n/a" if y["hit"] is None else "{:.4f}".format(y["hit"]),
                "n/a" if y["edge_pts"] is None else "{:+.2f}".format(y["edge_pts"])))
        print("\n{}/{} years positive after execution costs.".format(pos, len(yrs)))
        return 0

    if a.ladder:
        r = ladder_test(feature=a.feature)
        if "error" in r:
            print(r["error"])
            return 1
        recs = r.pop("records")
        added, path = append_ladder_log(recs)
        print("{} settled hours, {}".format(r["n"], r["span"]))
        print("  Brier   market {:.5f} | signal {:.5f} | always-0.5 {:.5f}".format(
            r["market_brier"], r["signal_brier"], r["always_half_brier"]))
        print("  hit     market {:.4f} | signal {:.4f}  (SE {:.4f})".format(
            r["market_hit"], r["signal_hit"], r["hit_se"]))
        print("  signal vs outcome    IC {:+.4f}  t {:+.2f}".format(
            r["ic_vs_outcome"], r["t_vs_outcome"]))
        print("  signal vs RESIDUAL   IC {:+.4f}  t {:+.2f}".format(
            r["ic_vs_residual"], r["t_vs_residual"]))
        print("  strike-offset vs RESIDUAL  IC {:+.4f}  (mechanical)".format(
            r["ic_offset_residual"]))
        print("  PARTIAL, offset removed    IC {:+.4f}  t {}   <-- the one that "
              "matters".format(r["partial_ic"],
                               "n/a" if r["t_partial"] is None
                               else "{:+.2f}".format(r["t_partial"])))
        print("\n+{} new hours -> {}".format(added, path))
        print("A signal that predicts the outcome but not the residual is a "
              "signal the market already has.")
        return 0

    if a.conditional:
        sign, bks = conditional(rows, a.conditional, by=a.by)
        print("{} split by {}  sign {:+d}".format(a.conditional, a.by, sign))
        print("  {:>7}{:>7}{:>22}{:>10}{:>8}{:>10}{:>10}".format(
            "bucket", "n", a.by + " range", "IC", "t", "hit", "edge_pts"))
        for b in bks:
            print("  {:>7}{:>7}{:>22}{:>10}{:>8}{:>10}{:>10}".format(
                b["bucket"], b["n"], "{:.5f}..{:.5f}".format(b["by_lo"], b["by_hi"]),
                "n/a" if b["ic"] is None else "{:+.5f}".format(b["ic"]),
                "n/a" if b["t"] is None else "{:+.2f}".format(b["t"]),
                "n/a" if b["hit"] is None else "{:.4f}".format(b["hit"]),
                "n/a" if b["edge_pts"] is None else "{:+.2f}".format(b["edge_pts"])))
        print("\nAn edge present only in the top bucket is conditional on regime, "
              "not a constant, and has to be traded with a live vol gate.")
        return 0

    if a.stability:
        sign, blocks = stability(rows, a.stability)
        print("{}  sign {:+d}".format(a.stability, sign))
        print("  {:>5}{:>7}{:>12}{:>10}{:>8}{:>10}{:>10}".format(
            "block", "n", "from", "IC", "t", "hit", "edge_pts"))
        pos = 0
        for b in blocks:
            pos += 1 if (b["edge_pts"] or 0) > 0 else 0
            print("  {:>5}{:>7}{:>12}{:>10}{:>8}{:>10}{:>10}".format(
                b["block"], b["n"], b["from"],
                "n/a" if b["ic"] is None else "{:+.5f}".format(b["ic"]),
                "n/a" if b["t"] is None else "{:+.2f}".format(b["t"]),
                "n/a" if b["hit"] is None else "{:.4f}".format(b["hit"]),
                "n/a" if b["edge_pts"] is None else "{:+.2f}".format(b["edge_pts"])))
        print("\n{}/{} blocks positive after execution costs.".format(pos, len(blocks)))
        return 0

    if a.split:
        print("chronological 60/40 split; sign chosen on train, scored on test")
        print("{:<18}{:>5}{:>10}{:>9}{:>10}{:>9}{:>11}{:>10}".format(
            "feature", "sign", "IC_train", "t_train", "IC_test", "t_test",
            "hit_test", "edge_pts"))
        for r in split_score(rows):
            print("{:<18}{:>5}{:>10}{:>9}{:>10}{:>9}{:>11}{:>10}{}".format(
                r["feature"], r["sign"],
                "n/a" if r["ic_train"] is None else "{:+.5f}".format(r["ic_train"]),
                "n/a" if r["t_train"] is None else "{:+.2f}".format(r["t_train"]),
                "n/a" if r["ic_test"] is None else "{:+.5f}".format(r["ic_test"]),
                "n/a" if r["t_test"] is None else "{:+.2f}".format(r["t_test"]),
                "n/a" if r["hit_rate"] is None else "{:.4f}".format(r["hit_rate"]),
                "n/a" if r["edge_pts"] is None else "{:+.2f}".format(r["edge_pts"]),
                "" if r["same_sign"] else "  SIGN FLIPPED"))
        print("\nedge_pts is the out-of-sample hit rate minus the fee bar. "
              "A flipped sign means the in-sample direction did not survive.")
        return 0

    print("{:<18}{:>9}{:>8}{:>11}{:>8}{:>10}{:>9}".format(
        "feature", "IC", "t", "hit_rate", "bar", "edge_pts", "t_vs_bar"))
    results = score(rows)
    for r in results:
        flag = "  <-- clears" if r["t"] is not None and abs(r["t"]) >= a.required_t else ""
        print("{:<18}{:>9}{:>8}{:>11}{:>8}{:>10}{:>9}{}".format(
            r["feature"],
            "n/a" if r["ic"] is None else "{:+.5f}".format(r["ic"]),
            "n/a" if r["t"] is None else "{:+.2f}".format(r["t"]),
            "n/a" if r["hit_rate"] is None else "{:.4f}".format(r["hit_rate"]),
            "{:.4f}".format(r["bar"]),
            "n/a" if r["edge_pts"] is None else "{:+.2f}".format(r["edge_pts"]),
            "n/a" if r["t_vs_bar"] is None else "{:+.2f}".format(r["t_vs_bar"]),
            flag))
    clears = [r for r in results if r["t"] is not None and abs(r["t"]) >= a.required_t]
    pays = [r for r in results if (r["edge_pts"] or -9) > 0]
    print("\n{} of {} clear the family-wise bar t={:.2f} (11 pre-registered "
          "trials; luck alone scores E[max t]=1.62).".format(
              len(clears), len(results), a.required_t))
    print("{} beat the execution bar of {:.4f} (fee + half-spread) — a "
          "statistically real IC that does not clear execution is not a "
          "trade.".format(len(pays), results[0]["bar"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
