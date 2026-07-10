"""
indicators.py — Nydex strategy signal scorer
Faithful Python port of nydex/src/indicators.js

Exposes one main entry point:
    score_ticker(ticker, bars, strategy, thresholds, spy_bars=None)
    → list of setup dicts, or None

Each setup dict contains:
    ticker, price, change, score (0-4), legs, direction ("bull"/"bear"),
    entry, target, stop, atr

Strategy keys (match Nydex exactly):
    "vwap" | "bollinger" | "macd" | "consolidation" | "residualmomentum"

Requires: no third-party libraries (stdlib math only)
"""

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# US equity session timezone. Alpaca stamps daily bars at 00:00 ET
# (04:00 UTC in EDT / 05:00 UTC in EST), so converting a bar's UTC timestamp
# to this zone yields its correct session date with no off-by-one.
_ET = ZoneInfo("America/New_York")


def _bar_et_date(bar):
    """Session (calendar) date of a bar in US/Eastern."""
    return datetime.fromtimestamp(bar["time"] / 1000, tz=timezone.utc).astimezone(_ET).date()


def drop_partial_bar(bars, now_utc=None):
    """
    Drop the trailing daily bar when it belongs to the current, still-in-progress
    ET session.

    Stage 1 runs mid-session (9:50 AM / 3:15 PM ET). During market hours Alpaca
    returns a partial bar for *today* — its volume is only a fraction of a full
    day and its close is unsettled. Scoring on it systematically fails every
    volume-spike leg and pollutes every close-based comparison (VWAP reclaim,
    BB re-entry, MACD cross, range breakout). Swing signals must be evaluated on
    the last COMPLETED session, so we strip today's bar before scoring.

    Returns a new list; does not mutate the input. Backtests are unaffected —
    historical windows never end on the current calendar date.
    """
    if not bars:
        return bars
    now = now_utc or datetime.now(timezone.utc)
    today_et = now.astimezone(_ET).date()
    if _bar_et_date(bars[-1]) >= today_et:
        return bars[:-1]
    return bars


# ─────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────

def _mean(arr):
    return sum(arr) / len(arr) if arr else 0.0


def calc_ema(closes, period):
    """Exponential Moving Average. Returns list same length as closes, None-padded."""
    if len(closes) < period:
        return [None] * len(closes)
    k   = 2 / (period + 1)
    ema = _mean(closes[:period])
    out = [None] * (period - 1)
    out.append(ema)
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1 - k)
        out.append(ema)
    return out


def calc_vwap(bars):
    """Cumulative VWAP from the first bar in the slice.

    NOTE: over a multi-month daily window this is just a volume-weighted average
    anchored to an arbitrary window edge — not a meaningful reference. Use
    calc_anchored_vwap for daily-bar swing signals instead.
    """
    pv, v = 0.0, 0.0
    result = []
    for b in bars:
        tp  = (b["high"] + b["low"] + b["close"]) / 3
        pv += tp * b["volume"]
        v  += b["volume"]
        result.append(pv / v if v else tp)
    return result


def _recent_extreme_idx(bars, lookback, kind):
    """Index of the most recent swing low ('low') or swing high ('high')
    within the last `lookback` bars. This is the AVWAP anchor."""
    window = bars[-lookback:] if lookback < len(bars) else bars
    start = len(bars) - len(window)
    if kind == "low":
        j = min(range(len(window)), key=lambda k: window[k]["low"])
    else:
        j = max(range(len(window)), key=lambda k: window[k]["high"])
    return start + j


def calc_anchored_vwap(bars, anchor_idx):
    """Anchored VWAP: cumulative volume-weighted typical price from anchor_idx
    forward. Returns a list the length of `bars`, None-padded before the anchor.

    On daily bars, anchoring to a meaningful pivot (a recent swing low/high)
    makes 'reclaim / reject VWAP' a real institutional-cost-basis signal, unlike
    a whole-window cumulative VWAP whose anchor slides every day."""
    out = [None] * anchor_idx
    pv, v = 0.0, 0.0
    for b in bars[anchor_idx:]:
        tp  = (b["high"] + b["low"] + b["close"]) / 3
        pv += tp * b["volume"]
        v  += b["volume"]
        out.append(pv / v if v else tp)
    return out


def calc_rsi(closes, period=14):
    """Wilder RSI. Returns list same length as closes, None-padded."""
    if len(closes) <= period:
        return [None] * len(closes)
    out = [None] * period
    g, l = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    ag, al = g / period, l / period
    out.append(100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al))
    for i in range(period + 1, len(closes)):
        d  = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + (d if d > 0 else 0)) / period
        al = (al * (period - 1) + (-d if d < 0 else 0)) / period
        out.append(100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al))
    return out


def calc_bb(closes, period=20, mult=2):
    """Bollinger Bands. Returns list of {mid, upper, lower} or None."""
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
            continue
        sl  = closes[i - period + 1 : i + 1]
        m   = _mean(sl)
        std = math.sqrt(_mean([(v - m) ** 2 for v in sl]))
        result.append({"mid": m, "upper": m + mult * std, "lower": m - mult * std})
    return result


def calc_macd(closes, fast=12, slow=26, sig=9):
    """MACD line, signal line, histogram. Returns {macd, signal, hist} each same length as closes."""
    ema_fast  = calc_ema(closes, fast)
    ema_slow  = calc_ema(closes, slow)
    macd_line = [
        ema_fast[i] - ema_slow[i]
        if ema_fast[i] is not None and ema_slow[i] is not None
        else None
        for i in range(len(closes))
    ]
    valid     = [v for v in macd_line if v is not None]
    sig_raw   = calc_ema(valid, sig)
    offset    = len(macd_line) - len(valid)
    sig_full  = [None] * offset + sig_raw
    hist      = [
        macd_line[i] - sig_full[i]
        if macd_line[i] is not None and sig_full[i] is not None
        else None
        for i in range(len(closes))
    ]
    return {"macd": macd_line, "signal": sig_full, "hist": hist}


def calc_atr(bars, period=14):
    """Average True Range over last `period` bars."""
    last = bars[-period:]
    trs  = []
    for j, b in enumerate(last):
        if j == 0:
            trs.append(b["high"] - b["low"])
        else:
            trs.append(max(
                b["high"] - b["low"],
                abs(b["high"] - last[j - 1]["close"]),
                abs(b["low"]  - last[j - 1]["close"]),
            ))
    return _mean(trs)


def calc_adx(bars, period=14):
    """Average Directional Index. Returns float or None if insufficient data."""
    if len(bars) < period * 2:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(bars)):
        h,  l  = bars[i]["high"],     bars[i]["low"]
        ph, pl = bars[i-1]["high"],   bars[i-1]["low"]
        pc     = bars[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        plus_dm.append( h - ph if (h - ph > pl - l) and (h - ph > 0) else 0)
        minus_dm.append(pl - l if (pl - l > h - ph) and (pl - l > 0) else 0)

    atr14  = sum(trs[:period])
    p_dm14 = sum(plus_dm[:period])
    m_dm14 = sum(minus_dm[:period])
    dx_arr = []

    for i in range(period, len(trs)):
        atr14  = atr14  - atr14  / period + trs[i]
        p_dm14 = p_dm14 - p_dm14 / period + plus_dm[i]
        m_dm14 = m_dm14 - m_dm14 / period + minus_dm[i]
        p_di   = (p_dm14 / atr14 * 100) if atr14 else 0
        m_di   = (m_dm14 / atr14 * 100) if atr14 else 0
        total  = p_di + m_di
        dx_arr.append(abs(p_di - m_di) / total * 100 if total else 0)

    if len(dx_arr) < period:
        return None
    return _mean(dx_arr[-period:])


def _calc_ols(x, y):
    """Simple OLS regression: y = alpha + beta*x. Returns {alpha, beta}."""
    n = len(x)
    if n < 10:
        return {"alpha": 0.0, "beta": 1.0}
    mx = _mean(x)
    my = _mean(y)
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    den = sum((x[i] - mx) ** 2 for i in range(n))
    beta = num / den if den else 1.0
    return {"alpha": my - beta * mx, "beta": beta}


# ─────────────────────────────────────────────
# Shared result builder
# ─────────────────────────────────────────────

def _exit_params(thresholds):
    """Per-strategy exit geometry from the strategy's threshold block.

    stopAtrMult   — stop distance in ATRs (default 1.0). Research favours
                    1.5–2.5× so the stop sits outside normal daily noise; a 1×
                    stop gets clipped by chop.
    targetAtrMult — fixed take-profit distance in ATRs (default 2.0). Trend
                    strategies pair this with a Chandelier trail applied to live
                    positions in order_plan.py.
    """
    return (
        float(thresholds.get("stopAtrMult", 1.0)),
        float(thresholds.get("targetAtrMult", 2.0)),
    )


def _weighted_score(legs, primary_leg_names, primary_weight=2):
    """Sum of passing-leg weights: the strategy's primary trigger counts
    `primary_weight`, each confirmation counts 1. Reported alongside the plain
    integer score so weighting can be evaluated by the backtest before it is ever
    used as a live gate."""
    return sum(
        (primary_weight if l["name"] in primary_leg_names else 1)
        for l in legs if l["pass"]
    )


def _build_results(ticker, price, change, atr, bull_legs, bear_legs,
                   stop_mult=1.0, target_mult=2.0,
                   bull_target=None, bear_target=None,
                   primary_leg_names=(),
                   bull_strength=0.0, bear_strength=0.0):
    """
    Assemble setup dicts from scored legs.
    Only includes a direction if it scores >= 2 legs (same threshold as Nydex).

    Exits are per-strategy:
      - stop     = entry ∓ atr*stop_mult
      - target   = an explicit price (bull_target/bear_target, e.g. the
                   Bollinger mid-band for mean reversion) when supplied and on
                   the correct side of entry; otherwise entry ± atr*target_mult.

    `score` (count of passing legs) remains the live qualification gate.
    `weighted_score` is additive/informational — the primary trigger leg is
    worth more — so it can be A/B'd in the backtest without changing live signals.
    """
    bull_score = sum(1 for l in bull_legs if l["pass"])
    bear_score = sum(1 for l in bear_legs if l["pass"])
    results = []
    if bull_score >= 2:
        target = bull_target if (bull_target is not None and bull_target > price) \
            else price + atr * target_mult
        results.append({
            "ticker":    ticker,
            "price":     price,
            "change":    change,
            "score":     bull_score,
            "weighted_score": _weighted_score(bull_legs, primary_leg_names),
            "strength":  round(bull_strength, 4),
            "legs":      bull_legs,
            "direction": "bull",
            "entry":     price,
            "target":    round(target,                 4),
            "stop":      round(price - atr * stop_mult, 4),
            "atr":       round(atr,                     4),
        })
    if bear_score >= 2:
        target = bear_target if (bear_target is not None and bear_target < price) \
            else price - atr * target_mult
        results.append({
            "ticker":    ticker,
            "price":     price,
            "change":    change,
            "score":     bear_score,
            "weighted_score": _weighted_score(bear_legs, primary_leg_names),
            "strength":  round(bear_strength, 4),
            "legs":      bear_legs,
            "direction": "bear",
            "entry":     price,
            "target":    round(target,                 4),
            "stop":      round(price + atr * stop_mult, 4),
            "atr":       round(atr,                     4),
        })
    return results if results else None


# ─────────────────────────────────────────────
# Strategy 1: VWAP Confluence
# ─────────────────────────────────────────────

def score_vwap(ticker, bars, thresholds):
    if len(bars) < 55:
        return None

    closes  = [b["close"]  for b in bars]
    volumes = [b["volume"] for b in bars]
    ema9    = calc_ema(closes, 9)
    ema21   = calc_ema(closes, 21)
    ema50   = calc_ema(closes, 50)
    rsi     = calc_rsi(closes)

    i, prev = len(bars) - 1, len(bars) - 2
    price   = closes[i]
    avg_vol = _mean(volumes[max(0, i - 20):i]) or 1
    atr     = calc_atr(bars)
    change  = (price - closes[prev]) / closes[prev] * 100

    # Anchored VWAP: bull anchors at the most recent swing LOW (price reclaiming
    # the down-leg buyers' average cost = momentum shift up); bear anchors at the
    # most recent swing HIGH. Meaningful on daily bars, unlike a whole-window VWAP
    # whose anchor slides forward every session.
    anchor_lb   = int(thresholds.get("vwapAnchorLookback", 30))
    low_anchor  = _recent_extreme_idx(bars, anchor_lb, "low")
    high_anchor = _recent_extreme_idx(bars, anchor_lb, "high")
    bull_avwap  = calc_anchored_vwap(bars, low_anchor)
    bear_avwap  = calc_anchored_vwap(bars, high_anchor)

    vol_mult = thresholds.get("volMult", 1.15)
    rsi_low  = thresholds.get("rsiLow",  50)
    rsi_high = thresholds.get("rsiHigh", 70)
    vol_pass = volumes[i] > avg_vol * vol_mult
    vol_val  = f"+{(volumes[i]/avg_vol - 1)*100:.0f}% vs avg"

    e9, e21, e50 = ema9[i], ema21[i], ema50[i]
    ri, rp       = rsi[i],  rsi[prev]

    bull_reclaim = (bull_avwap[prev] is not None and bull_avwap[i] is not None
                    and closes[prev] < bull_avwap[prev] and closes[i] > bull_avwap[i])
    bear_reject  = (bear_avwap[prev] is not None and bear_avwap[i] is not None
                    and closes[prev] > bear_avwap[prev] and closes[i] < bear_avwap[i])

    bull_legs = [
        {"name": "AVWAP Reclaim",  "pass": bull_reclaim,
         "val": f"AVWAP ${bull_avwap[i]:.2f}" if bull_avwap[i] is not None else "—"},
        {"name": "EMA Stack Bull", "pass": all(v is not None for v in [e9,e21,e50]) and e9>e21 and e21>e50 and price>e9,
         "val": f"9>{e9:.1f} 21>{e21:.1f}"},
        {"name": "Volume Spike",   "pass": vol_pass,  "val": vol_val},
        {"name": "RSI Reset ↑",    "pass": rp is not None and ri is not None and rp < rsi_low and ri > rsi_low and ri < rsi_high,
         "val": f"RSI {ri:.1f}" if ri else "—"},
    ]
    bear_legs = [
        {"name": "AVWAP Rejection", "pass": bear_reject,
         "val": f"AVWAP ${bear_avwap[i]:.2f}" if bear_avwap[i] is not None else "—"},
        {"name": "EMA Stack Bear", "pass": all(v is not None for v in [e9,e21,e50]) and e9<e21 and e21<e50 and price<e9,
         "val": f"9<{e9:.1f} 21<{e21:.1f}"},
        {"name": "Volume Spike",   "pass": vol_pass,  "val": vol_val},
        {"name": "RSI Rollover ↓", "pass": rp is not None and ri is not None and rp > rsi_low and ri < rsi_low and ri > 30,
         "val": f"RSI {ri:.1f}" if ri else "—"},
    ]
    stop_mult, target_mult = _exit_params(thresholds)
    vol_ratio = volumes[i] / avg_vol   # conviction: relative volume on the reclaim
    return _build_results(ticker, price, change, atr, bull_legs, bear_legs,
                          stop_mult=stop_mult, target_mult=target_mult,
                          primary_leg_names=("AVWAP Reclaim", "AVWAP Rejection"),
                          bull_strength=vol_ratio, bear_strength=vol_ratio)


# ─────────────────────────────────────────────
# Strategy 2: Bollinger Band Mean Reversion
# ─────────────────────────────────────────────

def score_bollinger(ticker, bars, thresholds):
    if len(bars) < 55:
        return None

    closes  = [b["close"]  for b in bars]
    volumes = [b["volume"] for b in bars]
    bb      = calc_bb(closes, 20, 2)
    rsi     = calc_rsi(closes)

    i, prev = len(bars) - 1, len(bars) - 2
    price   = closes[i]
    avg_vol = _mean(volumes[max(0, i - 20):i]) or 1
    atr     = calc_atr(bars)
    change  = (price - closes[prev]) / closes[prev] * 100

    cur, prv = bb[i], bb[prev]
    if not cur or not prv:
        return None

    # Hard ADX gate — band-walking in a real trend produces false Bollinger signals.
    # Price keeps tagging the band and "closing inside" while the trend just continues.
    # This is a hard NO, not a leg vote — if trending, skip mean reversion entirely.
    adx      = calc_adx(bars[-60:], 14)
    adx_gate = thresholds.get("adxMax", 30)
    if adx is not None and adx > adx_gate:
        return None

    vol_mult = thresholds.get("volMult", 1.1)
    rsi_ob   = thresholds.get("rsiHigh", 65)
    rsi_os   = thresholds.get("rsiLow",  35)
    vol_pass = volumes[i] > avg_vol * vol_mult
    vol_val  = f"+{(volumes[i]/avg_vol - 1)*100:.0f}% vs avg"
    ri, rp   = rsi[i], rsi[prev]

    bull_legs = [
        {"name": "BB Lower Tag",    "pass": closes[prev] <= prv["lower"], "val": f"Lower ${prv['lower']:.2f}"},
        {"name": "Close Inside BB", "pass": price > cur["lower"],         "val": f"Mid ${cur['mid']:.2f}"},
        {"name": "RSI Oversold",    "pass": rp is not None and rp < rsi_os, "val": f"RSI {ri:.1f}" if ri else "—"},
        {"name": "Volume Spike",    "pass": vol_pass,                     "val": vol_val},
    ]
    bear_legs = [
        {"name": "BB Upper Tag",    "pass": closes[prev] >= prv["upper"], "val": f"Upper ${prv['upper']:.2f}"},
        {"name": "Close Inside BB", "pass": price < cur["upper"],         "val": f"Mid ${cur['mid']:.2f}"},
        {"name": "RSI Overbought",  "pass": rp is not None and rp > rsi_ob, "val": f"RSI {ri:.1f}" if ri else "—"},
        {"name": "Volume Spike",    "pass": vol_pass,                     "val": vol_val},
    ]
    # Mean reversion targets the mean (mid band), not a fixed 2× ATR trend
    # target — the 20-SMA is hit far more often than the opposite band, which
    # lifts the win rate. Falls back to atr*target_mult if the mid band is on
    # the wrong side of price (guarded in _build_results).
    stop_mult, target_mult = _exit_params(thresholds)
    # Conviction: how stretched from the mean (RSI depth). More oversold = stronger
    # bull reversion; more overbought = stronger bear reversion.
    rp_val = rp if rp is not None else 50.0
    return _build_results(ticker, price, change, atr, bull_legs, bear_legs,
                          stop_mult=stop_mult, target_mult=target_mult,
                          bull_target=cur["mid"], bear_target=cur["mid"],
                          primary_leg_names=("BB Lower Tag", "BB Upper Tag"),
                          bull_strength=(50.0 - rp_val), bear_strength=(rp_val - 50.0))


# ─────────────────────────────────────────────
# Strategy 3: MACD Trend Continuation
# ─────────────────────────────────────────────

def _crossed_within(fast_line, slow_line, i, lookback, direction):
    """True if fast crossed slow in the given `direction` on any of the last
    `lookback` bars AND is still on that side at bar i. direction='up'/'down'."""
    if fast_line[i] is None or slow_line[i] is None:
        return False
    if direction == "up" and not (fast_line[i] > slow_line[i]):
        return False
    if direction == "down" and not (fast_line[i] < slow_line[i]):
        return False
    for k in range(i, max(0, i - lookback), -1):
        a, b = fast_line[k], slow_line[k]
        pa, pb = fast_line[k - 1], slow_line[k - 1]
        if None in (a, b, pa, pb):
            continue
        if direction == "up" and pa < pb and a > b:
            return True
        if direction == "down" and pa > pb and a < b:
            return True
    return False


def score_macd_trend(ticker, bars, thresholds):
    if len(bars) < 60:
        return None

    fast = int(thresholds.get("macdFast", 12))
    slow = int(thresholds.get("macdSlow", 26))
    sig  = int(thresholds.get("macdSignal", 9))
    cross_lb   = int(thresholds.get("crossLookback", 1))
    second_leg = thresholds.get("secondLeg", "sub_zero")  # "sub_zero" | "hist_rising"
    ema_filter = bool(thresholds.get("emaFilter", True))

    closes  = [b["close"]  for b in bars]
    volumes = [b["volume"] for b in bars]
    macd_d  = calc_macd(closes, fast, slow, sig)
    ema50   = calc_ema(closes, 50)
    rsi     = calc_rsi(closes)

    i, prev = len(bars) - 1, len(bars) - 2
    price   = closes[i]
    avg_vol = _mean(volumes[max(0, i - 20):i]) or 1
    atr     = calc_atr(bars)
    change  = (price - closes[prev]) / closes[prev] * 100

    ml, sl, hl = macd_d["macd"], macd_d["signal"], macd_d["hist"]
    if any(v is None for v in [ml[i], sl[i], hl[i], hl[prev]]):
        return None

    rsi_min  = thresholds.get("rsiLow",  45)
    rsi_max  = thresholds.get("rsiHigh", 75)
    ri       = rsi[i]
    e50      = ema50[i]

    bull_cross = _crossed_within(ml, sl, i, cross_lb, "up")
    bear_cross = _crossed_within(ml, sl, i, cross_lb, "down")

    # Second leg is configurable (swept in optimize.py):
    #   sub_zero    — cross while both lines are still below/above zero (early
    #                 reversal off a pullback; avoids chasing an extended move)
    #   hist_rising — MACD histogram expanding in the trade direction (momentum
    #                 confirmation; less restrictive than sub_zero)
    if second_leg == "hist_rising":
        second_name = "Hist Rising"
        bull_second = hl[i] > hl[prev]
        bear_second = hl[i] < hl[prev]
        bull_second_val = bear_second_val = f"Hist {hl[i]:+.3f} (prev {hl[prev]:+.3f})"
    else:
        second_name = "Early-Stage (Sub-0)"
        bull_second = ml[i] < 0 and sl[i] < 0
        bear_second = ml[i] > 0 and sl[i] > 0
        bull_second_val = bear_second_val = f"MACD {ml[i]:.3f} / Sig {sl[i]:.3f}"

    # EMA50 leg: real trend filter when enabled; a free pass when disabled so the
    # 4-leg / 4-of-4 structure is preserved for the sweep.
    bull_ema = (e50 is not None and price > e50) if ema_filter else True
    bear_ema = (e50 is not None and price < e50) if ema_filter else True
    ema_bull_val = (f"EMA50 ${e50:.2f}" if e50 else "—") if ema_filter else "filter off"
    ema_bear_val = ema_bull_val

    bull_legs = [
        {"name": "MACD Cross ↑",  "pass": bull_cross, "val": f"MACD {ml[i]:.3f}"},
        {"name": second_name,     "pass": bull_second, "val": bull_second_val},
        {"name": "Above EMA50",   "pass": bull_ema,   "val": ema_bull_val},
        {"name": "RSI Momentum",
         "pass": ri is not None and rsi_min < ri < rsi_max,
         "val":  f"RSI {ri:.1f}" if ri else "—"},
    ]
    bear_legs = [
        {"name": "MACD Cross ↓",  "pass": bear_cross, "val": f"MACD {ml[i]:.3f}"},
        {"name": second_name,     "pass": bear_second, "val": bear_second_val},
        {"name": "Below EMA50",   "pass": bear_ema,   "val": ema_bear_val},
        {"name": "RSI Momentum",
         "pass": ri is not None and 30 < ri < (100 - rsi_min),
         "val":  f"RSI {ri:.1f}" if ri else "—"},
    ]
    stop_mult, target_mult = _exit_params(thresholds)
    hist_str = (hl[i] / atr) if atr else 0.0   # momentum magnitude, ATR-normalized
    return _build_results(ticker, price, change, atr, bull_legs, bear_legs,
                          stop_mult=stop_mult, target_mult=target_mult,
                          primary_leg_names=("MACD Cross ↑", "MACD Cross ↓"),
                          bull_strength=hist_str, bear_strength=-hist_str)


# ─────────────────────────────────────────────
# Strategy 4: Consolidation Breakout
# ─────────────────────────────────────────────

def score_consolidation(ticker, bars, thresholds):
    if len(bars) < 60:
        return None

    closes  = [b["close"]  for b in bars]
    volumes = [b["volume"] for b in bars]

    i, prev = len(bars) - 1, len(bars) - 2
    price   = closes[i]
    atr     = calc_atr(bars)
    change  = (price - closes[prev]) / closes[prev] * 100

    bb  = calc_bb(closes, 20, 2)
    cur = bb[i]
    if not cur:
        return None

    bb_width       = (cur["upper"] - cur["lower"]) / cur["mid"]
    bb_width_thresh = thresholds.get("bbWidth",    0.06)
    adx_thresh      = thresholds.get("adxMax",     25)
    range_thresh    = thresholds.get("rangeWidth",  0.08)
    vol_mult        = thresholds.get("volMult",     1.3)

    adx    = calc_adx(bars[-60:], 14)
    recent = bars[i - 19 : i]
    if not recent:
        return None

    range_high  = max(b["high"]   for b in recent)
    range_low   = min(b["low"]    for b in recent)
    range_width = (range_high - range_low) / price
    avg_vol     = _mean([b["volume"] for b in recent]) or 1
    vol_pass    = volumes[i] > avg_vol * vol_mult
    vol_val     = f"+{(volumes[i]/avg_vol - 1)*100:.0f}% vs avg"

    is_tight   = bb_width    < bb_width_thresh
    is_ranging = (adx < adx_thresh) if adx is not None else True
    is_narrow  = range_width < range_thresh
    bull_break = closes[i] > range_high and closes[prev] <= range_high
    bear_break = closes[i] < range_low  and closes[prev] >= range_low

    # Clean Break (Held): bar's low (bull) must not wick back deep into the range.
    # A breakout bar that wicks back inside = weak conviction = likely fakeout.
    # Buffer = ATR * 0.25 tolerance below the range boundary.
    held_buffer = atr * 0.25
    bull_held   = bull_break and bars[i]["low"]  >= range_high - held_buffer
    bear_held   = bear_break and bars[i]["high"] <= range_low  + held_buffer

    adx_val = f"ADX {adx:.1f} < {adx_thresh}" if adx is not None else "ADX est."

    bull_legs = [
        {"name": "Volatility Compression",
         "pass": is_tight and is_narrow,
         "val":  f"BB {bb_width*100:.1f}% · Range {range_width*100:.1f}%"},
        {"name": "ADX Low (Ranging)",
         "pass": is_ranging, "val": adx_val},
        {"name": "Breakout + Volume",
         "pass": bull_break and vol_pass, "val": vol_val},
        {"name": "Clean Break (Held)",
         "pass": bull_held,
         "val":  f"Low ${bars[i]['low']:.2f} vs Range ${range_high:.2f}"},
    ]
    bear_legs = [
        {"name": "Volatility Compression",
         "pass": is_tight and is_narrow,
         "val":  f"BB {bb_width*100:.1f}% · Range {range_width*100:.1f}%"},
        {"name": "ADX Low (Ranging)",
         "pass": is_ranging, "val": adx_val},
        {"name": "Breakdown + Volume",
         "pass": bear_break and vol_pass, "val": vol_val},
        {"name": "Clean Break (Held)",
         "pass": bear_held,
         "val":  f"High ${bars[i]['high']:.2f} vs Range ${range_low:.2f}"},
    ]
    stop_mult, target_mult = _exit_params(thresholds)
    vol_ratio = volumes[i] / avg_vol   # conviction: volume surge on the break
    return _build_results(ticker, price, change, atr, bull_legs, bear_legs,
                          stop_mult=stop_mult, target_mult=target_mult,
                          primary_leg_names=("Breakout + Volume", "Breakdown + Volume"),
                          bull_strength=vol_ratio, bear_strength=vol_ratio)


# ─────────────────────────────────────────────
# Strategy 5: Residual Momentum
# Based on Kakushadze §3.7
# ─────────────────────────────────────────────

def score_residual_momentum(ticker, bars, thresholds, spy_bars=None):
    if not spy_bars or len(spy_bars) < 40 or len(bars) < 40:
        return None

    N           = min(len(bars), len(spy_bars), 50)
    stock_slice = bars[-N:]
    spy_slice   = spy_bars[-N:]

    stock_rets = [math.log(stock_slice[i]["close"] / stock_slice[i-1]["close"]) for i in range(1, N)]
    spy_rets   = [math.log(spy_slice[i]["close"]   / spy_slice[i-1]["close"])   for i in range(1, N)]
    L = min(len(stock_rets), len(spy_rets))
    if L < 20:
        return None

    reg_n = min(L, 36)
    ols   = _calc_ols(spy_rets[:reg_n], stock_rets[:reg_n])
    beta  = ols["beta"]

    SKIP, FORM = 1, 12
    form_end   = L - SKIP
    form_start = max(0, form_end - FORM)
    form_stock = stock_rets[form_start:form_end]
    form_spy   = spy_rets[form_start:form_end]
    if len(form_stock) < 6:
        return None

    residuals = [form_stock[j] - beta * form_spy[j] for j in range(len(form_stock))]
    mean_res  = _mean(residuals)
    n         = len(residuals)
    variance  = sum((r - mean_res) ** 2 for r in residuals) / (n - 1) if n > 1 else 0
    std_res   = math.sqrt(variance) if variance > 0 else 0
    risk_adj  = mean_res / std_res if std_res > 0 else 0.0

    # Two-bar hold: compute prior bar's formation window too.
    # A single bar crossing the threshold can be a noise spike;
    # requiring it to hold the prior bar means the alpha is persistent, not a flicker.
    prev_form_end   = form_end - 1
    prev_form_start = max(0, prev_form_end - FORM)
    prev_form_stock = stock_rets[prev_form_start:prev_form_end]
    prev_form_spy   = spy_rets[prev_form_start:prev_form_end]
    if len(prev_form_stock) >= 6:
        prev_residuals  = [prev_form_stock[j] - beta * prev_form_spy[j] for j in range(len(prev_form_stock))]
        prev_mean_res   = _mean(prev_residuals)
        prev_n          = len(prev_residuals)
        prev_var        = sum((r - prev_mean_res) ** 2 for r in prev_residuals) / (prev_n - 1) if prev_n > 1 else 0
        prev_std_res    = math.sqrt(prev_var) if prev_var > 0 else 0
        prev_risk_adj   = prev_mean_res / prev_std_res if prev_std_res > 0 else 0.0
    else:
        prev_risk_adj = 0.0

    closes  = [b["close"]  for b in bars]
    volumes = [b["volume"] for b in bars]
    i, prev = len(bars) - 1, len(bars) - 2
    price   = closes[i]
    atr     = calc_atr(bars)
    change  = (price - closes[prev]) / closes[prev] * 100
    ema21   = calc_ema(closes, 21)
    ema50   = calc_ema(closes, 50)
    rsi     = calc_rsi(closes)
    avg_vol = _mean(volumes[max(0, i-20):i]) or 1

    min_ra   = thresholds.get("minResidualRA", 0.15)
    vol_mult = thresholds.get("volMult", 1.1)
    vol_pass = volumes[i] > avg_vol * vol_mult
    vol_val  = f"+{(volumes[i]/avg_vol - 1)*100:.0f}% vs avg"
    ri       = rsi[i]
    e21, e50 = ema21[i], ema50[i]

    bull_legs = [
        {"name": "Residual Momentum (2-bar)",
         "pass": risk_adj > min_ra and prev_risk_adj > min_ra,
         "val":  f"RA-Res: {risk_adj:.3f} prev: {prev_risk_adj:.3f} (β={beta:.2f})"},
        {"name": "Above EMA21",
         "pass": e21 is not None and price > e21,
         "val":  f"EMA21 ${e21:.2f}" if e21 else "—"},
        {"name": "EMA21 > EMA50",
         "pass": e21 is not None and e50 is not None and e21 > e50,
         "val":  f"EMA50 ${e50:.2f}" if e50 else "—"},
        {"name": "RSI Momentum",
         "pass": ri is not None and 50 < ri < 75,
         "val":  f"RSI {ri:.1f}" if ri else "—"},
    ]
    bear_legs = [
        {"name": "Residual Momentum (2-bar)",
         "pass": risk_adj < -min_ra and prev_risk_adj < -min_ra,
         "val":  f"RA-Res: {risk_adj:.3f} prev: {prev_risk_adj:.3f} (β={beta:.2f})"},
        {"name": "Below EMA21",
         "pass": e21 is not None and price < e21,
         "val":  f"EMA21 ${e21:.2f}" if e21 else "—"},
        {"name": "EMA21 < EMA50",
         "pass": e21 is not None and e50 is not None and e21 < e50,
         "val":  f"EMA50 ${e50:.2f}" if e50 else "—"},
        {"name": "RSI Weak",
         "pass": ri is not None and 25 < ri < 50,
         "val":  f"RSI {ri:.1f}" if ri else "—"},
    ]
    stop_mult, target_mult = _exit_params(thresholds)
    # Conviction: the risk-adjusted residual itself — the ranking variable in the
    # residual-momentum literature (Blitz/Huij/Martens sort on exactly this).
    return _build_results(ticker, price, change, atr, bull_legs, bear_legs,
                          stop_mult=stop_mult, target_mult=target_mult,
                          primary_leg_names=("Residual Momentum (2-bar)",),
                          bull_strength=risk_adj, bear_strength=-risk_adj)


# ─────────────────────────────────────────────
# Strategy 6: Post-Earnings-Announcement Drift (PEAD)
# Bernard & Thomas (1989): stocks drift in the direction of an earnings surprise
# for weeks after the report. This is a price-based implementation — it detects the
# earnings event as the most recent volume-confirmed large move (a robust proxy for
# an earnings reaction, since that is nearly always a stock's biggest single-day
# move) and rides the drift. UPGRADE PATH: replace detect_earnings_event with a real
# earnings calendar + SUE (actual vs consensus EPS) from a fundamentals feed.
# ─────────────────────────────────────────────

def detect_earnings_event(bars, window=40, move_mult=2.0, vol_mult=2.0):
    """Most recent volume-confirmed large daily move in the last `window` bars
    (excluding the current bar). Returns event info or None. `ret_atr` is the move
    in ATR units = the surprise-magnitude proxy; sign = direction."""
    if len(bars) < 25:
        return None
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    atr = calc_atr(bars)
    if atr <= 0:
        return None
    i = len(bars) - 1
    lo = max(1, i - window)
    for j in range(i - 1, lo - 1, -1):  # most recent first, exclude current bar
        avg_vol = _mean(vols[max(0, j - 20):j]) or 1
        move = closes[j] - closes[j - 1]
        if abs(move) >= move_mult * atr and vols[j] >= vol_mult * avg_vol:
            return {"idx": j, "ret": move, "ret_atr": move / atr,
                    "days_since": i - j, "event_close": closes[j]}
    return None


def score_pead(ticker, bars, thresholds):
    if len(bars) < 40:
        return None

    closes = [b["close"] for b in bars]
    i, prev = len(bars) - 1, len(bars) - 2
    price = closes[i]
    atr = calc_atr(bars)
    change = (price - closes[prev]) / closes[prev] * 100

    window    = int(thresholds.get("peadWindow", 40))
    move_mult = thresholds.get("moveMult", 2.0)
    vol_mult  = thresholds.get("volMult", 2.0)
    drift_min = int(thresholds.get("driftMin", 1))
    drift_max = int(thresholds.get("driftMax", 10))

    ev = detect_earnings_event(bars, window, move_mult, vol_mult)
    if ev is None or not (drift_min <= ev["days_since"] <= drift_max):
        return None  # no recent earnings-like event → no PEAD setup

    ema20 = calc_ema(closes, 20)[i]
    ri    = calc_rsi(closes)[i]
    up    = ev["ret"] > 0
    down  = ev["ret"] < 0
    ev_val = f"{ev['ret_atr']:+.1f}ATR {ev['days_since']}d ago"

    # All legs are gated on the event direction so the opposite side can never
    # score off momentum alone.
    bull_legs = [
        {"name": "Earnings Surprise ↑", "pass": up, "val": ev_val},
        {"name": "Drift Intact",        "pass": up and price >= ev["event_close"],
         "val": f"vs event ${ev['event_close']:.2f}"},
        {"name": "Above EMA20",         "pass": up and ema20 is not None and price > ema20,
         "val": f"EMA20 ${ema20:.2f}" if ema20 else "—"},
        {"name": "RSI Momentum",        "pass": up and ri is not None and 45 < ri < 80,
         "val": f"RSI {ri:.1f}" if ri else "—"},
    ]
    bear_legs = [
        {"name": "Earnings Surprise ↓", "pass": down, "val": ev_val},
        {"name": "Drift Intact",        "pass": down and price <= ev["event_close"],
         "val": f"vs event ${ev['event_close']:.2f}"},
        {"name": "Below EMA20",         "pass": down and ema20 is not None and price < ema20,
         "val": f"EMA20 ${ema20:.2f}" if ema20 else "—"},
        {"name": "RSI Weak",            "pass": down and ri is not None and 20 < ri < 55,
         "val": f"RSI {ri:.1f}" if ri else "—"},
    ]
    stop_mult, target_mult = _exit_params(thresholds)
    return _build_results(ticker, price, change, atr, bull_legs, bear_legs,
                          stop_mult=stop_mult, target_mult=target_mult,
                          primary_leg_names=("Earnings Surprise ↑", "Earnings Surprise ↓"),
                          bull_strength=ev["ret_atr"], bear_strength=-ev["ret_atr"])


# ─────────────────────────────────────────────
# SPY regime classifier
# ─────────────────────────────────────────────

def estimate_vix_regime(spy_bars, period=14):
    """
    Estimate VIX volatility regime from SPY ATR as a percentage of price.
    No external VIX data needed — uses bars already fetched for SPY.

    ATR/price mapping (calibrated against historical VIX levels):
        < 0.8%   → calm          (VIX ≈ <15)
        0.8–1.2% → normal        (VIX ≈ 15–20)
        1.2–1.8% → fear_elevated (VIX ≈ 20–25)
        1.8–2.5% → fear_spiking  (VIX ≈ 25–30)
        > 2.5%   → panic         (VIX ≈ >30)

    Returns: "calm" | "normal" | "fear_elevated" | "fear_spiking" | "panic" | "unknown"
    """
    if not spy_bars or len(spy_bars) < period + 1:
        return "unknown"

    atr   = calc_atr(spy_bars, period)
    price = spy_bars[-1]["close"]
    if not price:
        return "unknown"

    atr_pct = atr / price

    if atr_pct < 0.008:
        return "calm"
    elif atr_pct < 0.012:
        return "normal"
    elif atr_pct < 0.018:
        return "fear_elevated"
    elif atr_pct < 0.025:
        return "fear_spiking"
    else:
        return "panic"


def compute_trend_filter(spy_bars, period=200):
    """Crash-avoidance overlay. Returns (risk_off: bool, sma: float|None).
    risk_off is True when SPY closes below its `period`-day SMA — a simple,
    evidenced regime filter that keeps new longs out of sustained downtrends
    (drawdown clusters are what wreck Sharpe). Needs >= period bars; if history
    is too short it returns (False, None) — i.e. it never blocks on missing data."""
    if not spy_bars or len(spy_bars) < period:
        return False, None
    closes = [b["close"] for b in spy_bars]
    sma = sum(closes[-period:]) / period
    return closes[-1] < sma, sma


def compute_realized_vol(spy_bars, lookback=20):
    """Annualized realized volatility of SPY daily log returns over `lookback`."""
    if not spy_bars or len(spy_bars) < lookback + 1:
        return None
    closes = [b["close"] for b in spy_bars][-(lookback + 1):]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    m = _mean(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    return math.sqrt(var) * math.sqrt(252)


def compute_vol_target_mult(spy_bars, target_annual_vol, bounds=(0.5, 1.5), lookback=20):
    """Volatility-targeting size multiplier = clip(target / realized, bounds).
    The most reliable Sharpe lever: lean into calm markets, cut exposure when
    realized vol spikes. Continuous complement to the discrete VIX bands.
    Returns (mult, realized_vol). mult defaults to 1.0 if vol can't be computed."""
    rv = compute_realized_vol(spy_bars, lookback)
    if not rv or rv <= 0 or not target_annual_vol:
        return 1.0, rv
    lo, hi = bounds
    return max(lo, min(hi, target_annual_vol / rv)), rv


def classify_spy_regime(spy_bars):
    """
    Classify SPY trend regime using EMA stack — same logic referenced in
    the Nydex instruction set.

    Returns: "uptrend" | "downtrend" | "choppy"
    """
    if len(spy_bars) < 55:
        return "choppy"

    closes = [b["close"] for b in spy_bars]
    ema21  = calc_ema(closes, 21)
    ema50  = calc_ema(closes, 50)
    price  = closes[-1]
    e21    = ema21[-1]
    e50    = ema50[-1]

    if e21 is None or e50 is None:
        return "choppy"

    if price > e21 and e21 > e50:
        return "uptrend"
    if price < e21 and e21 < e50:
        return "downtrend"
    return "choppy"


def select_strategy_for_regime(spy_regime):
    """
    Map SPY regime → recommended strategy key.

    Uptrend routes to residual momentum, not MACD. A walk-forward backtest
    (optimize.py) showed MACD has NO out-of-sample edge in uptrends under any
    parameterization (OOS avg R ≈ -0.14 to -0.22, profit factor < 1), while
    residual momentum is consistently positive in-sample AND out-of-sample
    (OOS avg R ≈ +0.10, PF ≈ 1.2) over a large trade sample. MACD remains
    available and fully parameterized for manual use, just not auto-routed.
    """
    return {
        "uptrend":   "residualmomentum",
        "downtrend": "residualmomentum",
        "choppy":    "bollinger",
    }.get(spy_regime, "vwap")


def load_spec():
    """Load the canonical strategy spec (strategies.json, shipped with the engine)."""
    import json, os
    with open(os.path.join(os.path.dirname(__file__), "strategies.json")) as f:
        return json.load(f)


def spec_defaults(strategy):
    """Default threshold values for a strategy, from the canonical spec."""
    entry = load_spec()["strategies"].get(strategy, {})
    return {k: v["default"] for k, v in entry.get("thresholds", {}).items()}


def resolve_thresholds(cfg, strategy):
    """
    Return thresholds for the selected strategy: canonical spec defaults
    (strategies.json) overlaid with any app-config overrides.

    Supports both the current nested config shape:
        {"thresholds": {"macd": {...}, "bollinger": {...}}}
    and the older flat shape:
        {"thresholds": {"volMult": 1.15, "rsiLow": 50}}
    """
    merged = spec_defaults(strategy)
    thresholds = cfg.get("strategy", {}).get("thresholds", {})
    strategy_thresholds = thresholds.get(strategy)
    merged.update(strategy_thresholds if isinstance(strategy_thresholds, dict)
                  else {k: v for k, v in thresholds.items() if not isinstance(v, dict)})
    return merged


# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────

def score_ticker(ticker, bars, strategy, thresholds, spy_bars=None):
    """
    Score a single ticker against the given strategy.
    Mirrors Nydex's scoreTicker() dispatcher.

    Args:
        ticker    : str
        bars      : list of bar dicts
        strategy  : "vwap"|"bollinger"|"macd"|"consolidation"|"residualmomentum"
        thresholds: dict of threshold overrides (see config.json)
        spy_bars  : list of SPY bar dicts (required for residualmomentum)

    Returns:
        list of setup dicts (may be empty), or None if bars too short
    """
    fn = {
        "vwap":              score_vwap,
        "bollinger":         score_bollinger,
        "macd":              score_macd_trend,
        "consolidation":     score_consolidation,
        "pead":              score_pead,
        "residualmomentum":  lambda t, b, th: score_residual_momentum(t, b, th, spy_bars),
    }.get(strategy, score_vwap)

    return fn(ticker, bars, thresholds)


def score_all_tickers(bars_map, strategy, thresholds, min_score=4,
                      spy_bars=None, bear_regime_only=None, spy_regime="uptrend"):
    """
    Run score_ticker across every ticker in bars_map.
    Filters to min_score and applies bear-regime-only rules for inverse ETFs.

    Args:
        bars_map        : {ticker: [bars]} from market_data.fetch_bars_batch
        strategy        : strategy key string
        thresholds      : dict
        min_score       : int — minimum legs that must pass (default 4)
        spy_bars        : list — SPY bars for residual momentum
        bear_regime_only: list — tickers only tradeable in downtrend (inverse ETFs)
        spy_regime      : str — current regime from classify_spy_regime()

    Returns:
        list of qualifying setup dicts, sorted by score descending
    """
    bear_only = set(bear_regime_only or [])
    results   = []

    for ticker, bars in bars_map.items():
        # Block inverse/bear ETFs when market is trending up
        if ticker in bear_only and spy_regime != "downtrend":
            continue

        setups = score_ticker(ticker, bars, strategy, thresholds, spy_bars)
        if not setups:
            continue

        for setup in setups:
            if setup["score"] >= min_score:
                setup["strategy"] = strategy  # provenance for merged sleeves
                results.append(setup)

    # Rank so the portfolio takes the STRONGEST setups, not watchlist order:
    # score first (the qualification tier), then the primary-leg-weighted score,
    # then the strategy's own conviction metric (strength). order_plan.py consumes
    # this order directly when filling the limited position slots.
    results.sort(
        key=lambda s: (s["score"], s.get("weighted_score", s["score"]), s.get("strength", 0.0)),
        reverse=True,
    )
    return results


