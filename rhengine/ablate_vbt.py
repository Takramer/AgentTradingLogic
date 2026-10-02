"""
ablate_vbt.py — full-matrix rule ablation with VectorBT.

Where ablate.py re-slices ONE engine backtest's trade list by leg pass-masks
(cheap, but limited to trades the engine emitted at score >= 2), this tool
re-derives every strategy rule as a vectorized boolean matrix (bars x tickers)
and backtests EVERY subset of the rules as its own configuration with
vectorbt.Portfolio.from_signals — including subsets without the primary
trigger leg, which the mask approach cannot reach. Entry = AND of the active
rules; the empty subset is the "no rules" market baseline.

Rules per strategy = the 4 scored legs from indicators.py / strategies.json.
MACD's configurable second leg is swept as 3 states (off | sub_zero |
hist_rising). Hard gates that are NOT legs stay always-on to match
production: bollinger's ADX trend gate, pead's event-in-drift-window gate,
the warmup floor, and the degenerate-risk (min_stop_pct) fill guard.

Trade mechanics mirror backtest.simulate_long: signal at bar t close, fill at
bar t+1 open, stop = close[t] - stopAtrMult*ATR[t], target = close[t] +
targetAtrMult*ATR[t] (bollinger targets the mid band when above price), time
stop after max_hold_sessions, costs as fees per side.

Known deviations from the event-loop engine (constant across combos, so the
RANKING is what to trust): Wilder RSI/ATR/ADX use ewm smoothing instead of
SMA-seeded recursion; no breakeven/chandelier trailing stop; time exit fills
at the next open rather than the 10th close; vectorbt resolves same-bar
stop+target by its own ordering; one position per ticker (overlapping signals
re-enter after exit instead of stacking independent trades); residualmomentum
rrgMode and consolidation squeezeBars sweeps are not implemented (defaults 0).

Output: a pandas DataFrame — one row per rule combination — sorted by profit
factor, with IS/OOS split columns and a per-rule marginal summary. Long-only,
like the live system.

Usage:
    py -m rhengine.ablate_vbt                               # all strategies
    py -m rhengine.ablate_vbt --strategy bollinger --cost-bps 5
    py -m rhengine.ablate_vbt --lookback-days 500 --max-tickers 1000
"""

import argparse
import itertools
import json

import numpy as np
import pandas as pd

from .costs import resolve_cost_bps

from .backtest import WARMUP_BARS
from .indicators import load_spec, resolve_thresholds

STRATEGIES = ("vwap", "bollinger", "macd", "consolidation",
              "residualmomentum", "pead")


# ------------------------------------------------------------- indicators ----
# Vectorized (bars x tickers) ports of the scalar calcs in indicators.py.

def _ema(close, span):
    return close.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(close, period=14):
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - 100 / (1 + ag / al)   # al == 0 -> rs inf -> 100, like the engine


def _true_range(high, low, close):
    pc = close.shift(1)
    return pd.DataFrame(
        np.maximum(high.values - low.values,
                   np.maximum(np.abs(high.values - pc.values),
                              np.abs(low.values - pc.values))),
        index=close.index, columns=close.columns)


def _atr(high, low, close, period=14):
    return _true_range(high, low, close).ewm(
        alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(high, low, close, period=14):
    up, dn = high.diff(), -low.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = _true_range(high, low, close)
    w = dict(alpha=1 / period, adjust=False, min_periods=period)
    atr = tr.ewm(**w).mean()
    pdi = 100 * plus_dm.ewm(**w).mean() / atr
    mdi = 100 * minus_dm.ewm(**w).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(**w).mean()


def _bbands(close, period=20, mult=2):
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)   # engine uses population std
    return mid, mid + mult * sd, mid - mult * sd


def _avwap_reclaim(high, low, close, volume, lookback):
    """AVWAP Reclaim leg: anchor at the most recent swing low within the last
    `lookback` bars (window includes the current bar); pass when yesterday
    closed below the anchored VWAP and today closed above it. Cumsum trick:
    AVWAP(a, t) = (cpv[t] - cpv[a-1]) / (cv[t] - cv[a-1])."""
    from numpy.lib.stride_tricks import sliding_window_view
    tp = (high.values + low.values + close.values) / 3
    pv = np.nan_to_num(tp * volume.values)
    v = np.nan_to_num(volume.values)
    cpv, cv = np.cumsum(pv, axis=0), np.cumsum(v, axis=0)
    lo, cl = low.values, close.values
    T, N = cl.shape
    out = np.zeros((T, N), dtype=bool)
    if T < lookback:
        return pd.DataFrame(out, index=close.index, columns=close.columns)
    t_idx = np.arange(lookback - 1, T)
    for ci in range(N):
        win = sliding_window_view(lo[:, ci], lookback)          # rows end at t
        anchors = t_idx - (lookback - 1) + np.where(
            np.isnan(win), np.inf, win).argmin(axis=1)
        base_pv = np.where(anchors > 0, cpv[np.maximum(anchors - 1, 0), ci], 0.0)
        base_v = np.where(anchors > 0, cv[np.maximum(anchors - 1, 0), ci], 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            aw_t = (cpv[t_idx, ci] - base_pv) / (cv[t_idx, ci] - base_v)
            aw_p = (cpv[t_idx - 1, ci] - base_pv) / (cv[t_idx - 1, ci] - base_v)
            ok = ((anchors < t_idx)                       # engine: avwap[prev] exists
                  & (cl[t_idx - 1, ci] < aw_p) & (cl[t_idx, ci] > aw_t))
        out[t_idx, ci] = np.where(np.isnan(ok.astype(float)), False, ok)
    return pd.DataFrame(out, index=close.index, columns=close.columns)


def _pead_event(close, volume, atr, avg_vol_prev, th):
    """Most recent outsized move (>= moveMult*ATR[t]) on outsized volume within
    the scan window, per bar t. Returns (gate, up, event_close): gate = event
    exists AND days_since within [driftMin, driftMax] (hard gate, as in the
    engine); up = event direction; event_close = close of the event bar."""
    from numpy.lib.stride_tricks import sliding_window_view
    W = int(th.get("peadWindow", 40))
    d_lo, d_hi = int(th.get("driftMin", 1)), int(th.get("driftMax", 10))
    cl, atr_v = close.values, atr.values
    move = np.abs(np.diff(cl, axis=0, prepend=np.full((1, cl.shape[1]), np.nan)))
    up_move = np.diff(cl, axis=0, prepend=np.full((1, cl.shape[1]), np.nan)) > 0
    vol_ok = (volume.values >= th.get("volMult", 2.0) * avg_vol_prev.values)
    T, N = cl.shape
    gate = np.zeros((T, N), dtype=bool)
    up = np.zeros((T, N), dtype=bool)
    ev_close = np.full((T, N), np.nan)
    if T <= W:
        z = pd.DataFrame(gate, index=close.index, columns=close.columns)
        return z, z.copy(), pd.DataFrame(ev_close, index=close.index, columns=close.columns)
    t_idx = np.arange(W, T)                       # windows cover j in [t-W, t-1]
    for ci in range(N):
        mw = sliding_window_view(move[:, ci], W)[:-1]       # row r -> t = r + W
        vw = sliding_window_view(vol_ok[:, ci], W)[:-1]
        with np.errstate(invalid="ignore"):
            qual = (mw >= th.get("moveMult", 2.0) * atr_v[t_idx, ci][:, None]) & vw
        has = qual.any(axis=1)
        pos = W - 1 - qual[:, ::-1].argmax(axis=1)          # last qualifying j
        j = t_idx - W + pos
        days = t_idx - j
        g = has & (days >= d_lo) & (days <= d_hi)
        gate[t_idx, ci] = g
        up[t_idx, ci] = g & up_move[j, ci]
        ev_close[t_idx, ci] = np.where(g, cl[j, ci], np.nan)
    idx, cols = close.index, close.columns
    return (pd.DataFrame(gate, index=idx, columns=cols),
            pd.DataFrame(up, index=idx, columns=cols),
            pd.DataFrame(ev_close, index=idx, columns=cols))


# ------------------------------------------------------------ market frame ----

class Market:
    """Wide OHLCV frames (bars x tickers) + shared indicators, computed once."""

    def __init__(self, bars_map, spy_bars):
        def _idx(bars):
            return (pd.to_datetime([b["time"] for b in bars], unit="ms", utc=True)
                    .tz_convert("America/New_York").normalize().tz_localize(None))

        cols = {}
        for f in ("open", "high", "low", "close", "volume"):
            cols[f] = {t: pd.Series([b[f] for b in bars], index=_idx(bars))
                       for t, bars in bars_map.items() if len(bars) > WARMUP_BARS}
        self.open = pd.concat(cols["open"], axis=1).sort_index()
        self.high = pd.concat(cols["high"], axis=1).reindex(self.open.index)
        self.low = pd.concat(cols["low"], axis=1).reindex(self.open.index)
        self.close = pd.concat(cols["close"], axis=1).reindex(self.open.index)
        self.volume = pd.concat(cols["volume"], axis=1).reindex(self.open.index)
        self.spy_close = (pd.Series([b["close"] for b in spy_bars], index=_idx(spy_bars))
                          .reindex(self.open.index))

        self.atr = _atr(self.high, self.low, self.close)
        self.rsi = _rsi(self.close)
        self.adx = _adx(self.high, self.low, self.close)
        self.ema = {p: _ema(self.close, p) for p in (9, 20, 21, 50)}
        self.bb_mid, self.bb_up, self.bb_lo = _bbands(self.close)
        # engine: mean of the PREVIOUS <=20 bars' volume, excluding current
        self.avg_vol = self.volume.rolling(20).mean().shift(1)
        # per-ticker bar count so warmup matches the engine's per-ticker t >= 60
        self.valid = self.close.notna().cumsum() > WARMUP_BARS

    def vol_spike(self, mult):
        return self.volume > self.avg_vol * mult


# ------------------------------------------------------------ rule builders ----
# Each returns (rules, gate, target_level):
#   rules  = [(label, [(state_name, bool DataFrame), ...]), ...]  (bull side)
#   gate   = always-on bool DataFrame or None
#   target_level = price-level DataFrame or None (None -> close + tgt*ATR)

def _single(label, frame):
    return (label, [("", frame)])


def rules_vwap(m, th):
    e9, e21, e50 = m.ema[9], m.ema[21], m.ema[50]
    rsi_lo, rsi_hi = th.get("rsiLow", 50), th.get("rsiHigh", 70)
    rules = [
        _single("AVWAP", _avwap_reclaim(m.high, m.low, m.close, m.volume,
                                        int(th.get("vwapAnchorLookback", 30)))),
        _single("EMA-STACK", (e9 > e21) & (e21 > e50) & (m.close > e9)),
        _single("VOL", m.vol_spike(th.get("volMult", 1.15))),
        _single("RSI-RESET", (m.rsi.shift(1) < rsi_lo)
                & (m.rsi > rsi_lo) & (m.rsi < rsi_hi)),
    ]
    return rules, None, None


def rules_bollinger(m, th):
    gate = ~(m.adx > th.get("adxMax", 35))       # hard gate; NaN ADX passes
    rules = [
        _single("BB-TAG", m.close.shift(1) <= m.bb_lo.shift(1)),
        _single("CLOSE-IN", m.close > m.bb_lo),
        _single("RSI-OS", m.rsi.shift(1) < th.get("rsiLow", 35)),
        _single("VOL", m.vol_spike(th.get("volMult", 1.1))),
    ]
    # mean-reversion target = mid band when above price (fallback handled later)
    target = m.bb_mid.where(m.bb_mid > m.close,
                            m.close + th.get("targetAtrMult", 1.5) * m.atr)
    return rules, gate, target


def rules_macd(m, th):
    macd = _ema(m.close, int(th.get("macdFast", 12))) - _ema(m.close, int(th.get("macdSlow", 26)))
    sig = macd.ewm(span=int(th.get("macdSignal", 9)), adjust=False).mean()
    hist = macd - sig
    lb = int(th.get("crossLookback", 1))
    crossed = ((macd > sig) & (macd.shift(1) < sig.shift(1))) \
        .rolling(lb, min_periods=1).max().astype(bool) & (macd > sig)
    rules = [
        _single("CROSS", crossed),
        # the engine's configurable second leg, swept as two alternative states
        ("STAGE", [("sub0", (macd < 0) & (sig < 0)),
                   ("hist-rise", hist > hist.shift(1))]),
        _single("EMA50", m.close > m.ema[50]),
        _single("RSI-BAND", (m.rsi > th.get("rsiLow", 45)) & (m.rsi < th.get("rsiHigh", 75))),
    ]
    return rules, None, None


def rules_consolidation(m, th):
    bb_width = (m.bb_up - m.bb_lo) / m.bb_mid
    rng_hi = m.high.rolling(20).max().shift(1)   # engine: bars[i-20:i]
    rng_lo = m.low.rolling(20).min().shift(1)
    rng_width = (rng_hi - rng_lo) / m.close
    breakout = (m.close > rng_hi) & (m.close.shift(1) <= rng_hi)
    rules = [
        _single("COMP", (bb_width < th.get("bbWidth", 0.06))
                & (rng_width < th.get("rangeWidth", 0.08))),
        _single("ADX-LOW", m.adx < th.get("adxMax", 25)),
        _single("BO+VOL", breakout & m.vol_spike(th.get("volMult", 1.3))),
        _single("HELD", breakout & (m.low >= rng_hi - 0.25 * m.atr)),
    ]
    return rules, None, None


def rules_residualmomentum(m, th):
    # Engine geometry over the last 50 bars: OLS beta on the oldest 36 of 49
    # log returns (a 36-bar window lagged 13), formation = last 12 returns
    # skipping the most recent (lag 1); prev formation lag 2, same alpha/beta.
    rets = np.log(m.close / m.close.shift(1))
    spy_r = np.log(m.spy_close / m.spy_close.shift(1))

    w = 36
    mx = spy_r.rolling(w).mean()
    my = rets.rolling(w).mean()
    cov = rets.mul(spy_r, axis=0).rolling(w).mean() - my.mul(mx, axis=0)
    var_x = (spy_r ** 2).rolling(w).mean() - mx ** 2
    beta = cov.div(var_x, axis=0).shift(13)
    alpha = my.shift(13) - beta.mul(mx.shift(13), axis=0)

    f = 12
    scale = f / (f - 1)                          # population -> ddof=1
    var_s = rets.rolling(f).var(ddof=0) * scale
    var_xf = (spy_r.rolling(f).var(ddof=0) * scale)
    cov_f = (rets.mul(spy_r, axis=0).rolling(f).mean()
             - rets.rolling(f).mean().mul(spy_r.rolling(f).mean(), axis=0)) * scale

    def _ra(lag):
        mean_res = (rets.rolling(f).mean().shift(lag) - alpha
                    - beta.mul(spy_r.rolling(f).mean().shift(lag), axis=0))
        var_res = (var_s.shift(lag) - 2 * beta * cov_f.shift(lag)
                   + (beta ** 2).mul(var_xf.shift(lag), axis=0))
        return mean_res / np.sqrt(var_res.where(var_res > 0))

    min_ra = th.get("minResidualRA", 0.15)
    ra, ra_prev = _ra(1), _ra(2)
    rules = [
        _single("RA-RES", (ra > min_ra) & (ra_prev > min_ra)),
        _single("EMA21", m.close > m.ema[21]),
        _single("EMA-X", m.ema[21] > m.ema[50]),
        _single("RSI-BAND", (m.rsi > 50) & (m.rsi < 75)),   # hardcoded in engine
    ]
    return rules, None, None


def rules_pead(m, th):
    gate, up, ev_close = _pead_event(m.close, m.volume, m.atr, m.avg_vol, th)
    rules = [
        _single("EVENT-UP", up),
        _single("DRIFT", up & (m.close >= ev_close)),
        _single("EMA20", up & (m.close > m.ema[20])),
        _single("RSI-BAND", up & (m.rsi > 45) & (m.rsi < 80)),
    ]
    return rules, gate, None


RULE_BUILDERS = {
    "vwap": rules_vwap, "bollinger": rules_bollinger, "macd": rules_macd,
    "consolidation": rules_consolidation,
    "residualmomentum": rules_residualmomentum, "pead": rules_pead,
}


# ------------------------------------------------------------------ sweep ----

def _profit_factor(pnl):
    if len(pnl) == 0:
        return np.nan
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl <= 0].sum()
    return np.inf if losses == 0 else wins / losses


def sweep_strategy(strategy, m, th, exits_cfg, cost_bps, split_ts, verbose=True):
    """Backtest every combination of the strategy's rules. Returns result rows."""
    import vectorbt as vbt

    rules, gate, target_level = RULE_BUILDERS[strategy](m, th)
    stop_mult = float(th.get("stopAtrMult", 1.0))
    target_mult = float(th.get("targetAtrMult", 2.0))
    max_hold = int(exits_cfg.get("max_hold_sessions", 10))
    min_stop_pct = float(exits_cfg.get("min_stop_pct", 0.003))

    # Exit geometry, anchored at the signal close (shifted to the entry bar).
    stop_level = (m.close - stop_mult * m.atr).shift(1)
    if target_level is None:
        target_level = m.close + target_mult * m.atr
    target_level = target_level.shift(1)
    sl_frac = ((m.open - stop_level) / m.open).clip(lower=1e-4)
    tp_frac = ((target_level - m.open) / m.open).clip(lower=1e-4)
    # degenerate-risk fill guard, same as simulate_long
    fillable = ((m.open - stop_level) > m.open * min_stop_pct) & m.open.notna()

    base = m.valid if gate is None else (m.valid & gate.fillna(False))
    # every combination of rule states (off + each state), off = rule removed
    state_axes = [[None] + [(lbl, name, frame) for name, frame in states]
                  for lbl, states in rules]
    rows = []
    for combo in itertools.product(*state_axes):
        active = [c for c in combo if c is not None]
        label = "+".join(lbl + ("=" + name if name else "")
                         for lbl, name, _ in active) or "(none — baseline)"
        sig = base.copy()
        for _, _, frame in active:
            sig &= frame.fillna(False)

        entries = sig.shift(1, fill_value=False) & fillable
        time_exits = sig.shift(1 + max_hold, fill_value=False)
        pf = vbt.Portfolio.from_signals(
            close=m.close, entries=entries, exits=time_exits,
            price=m.open, open=m.open, high=m.high, low=m.low,
            sl_stop=sl_frac.values, tp_stop=tp_frac.values,
            stop_entry_price="fillprice", direction="longonly",
            # vectorbt charges `fees` on EVERY order — entry and exit both — so
            # a round-trip figure must be halved here or it is billed twice.
            # (2026-07-27: it was not, mirroring the same units bug in
            # backtest._cost_r. `cost_bps` is ROUND TRIP; see costs.py.)
            fees=cost_bps / 2.0 / 1e4, init_cash=10_000.0,
            size=100.0, size_type="value", freq="1D")

        recs = pf.trades.records
        if not isinstance(recs, pd.DataFrame):
            recs = pd.DataFrame(recs)
        pnl = recs["pnl"].values if len(recs) else np.array([])
        entry_dates = (m.close.index.values[recs["entry_idx"].values.astype(int)]
                       if len(recs) else np.array([], dtype="datetime64[ns]"))
        # drop trades with unvaluable PnL (open at window end on a NaN-tailed,
        # e.g. delisted, ticker) so counts and sums stay meaningful
        valued = ~np.isnan(pnl)
        recs, pnl, entry_dates = recs[valued], pnl[valued], entry_dates[valued]
        is_mask = entry_dates < np.datetime64(split_ts)
        ret_col = "return" if "return" in recs.columns else None
        rows.append({
            "strategy": strategy,
            "rules": label,
            "n_rules": len(active),
            "trades": len(pnl),
            "win_rate": float((pnl > 0).mean()) if len(pnl) else np.nan,
            "avg_trade_pct": (float(recs[ret_col].mean()) * 100
                              if ret_col and len(recs) else np.nan),
            "profit_factor": _profit_factor(pnl),
            "is_pf": _profit_factor(pnl[is_mask]),
            "oos_pf": _profit_factor(pnl[~is_mask]),
            "oos_trades": int((~is_mask).sum()),
            "total_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        })
        if verbose:
            r = rows[-1]
            print("  {:<44} n={:>6}  PF={:>6}".format(
                label[:44], r["trades"],
                "inf" if r["profit_factor"] == np.inf else
                ("-" if np.isnan(r["profit_factor"]) else f"{r['profit_factor']:.2f}")))
    return rows


def marginal_summary(df):
    """Per rule: median PF of combos with the rule on vs off. A negative delta
    means the median configuration does BETTER without the rule — dead weight."""
    out = []
    for strat, g in df.groupby("strategy"):
        pf = g["profit_factor"].replace(np.inf, np.nan)
        labels = set()
        for r in g["rules"]:
            if not r.startswith("("):
                labels.update(x.split("=")[0] for x in r.split("+"))
        for lbl in sorted(labels):
            has = g["rules"].str.contains(lbl, regex=False)
            on, off = pf[has].median(), pf[~has].median()
            out.append({"strategy": strat, "rule": lbl,
                        "median_pf_on": on, "median_pf_off": off,
                        "delta": on - off,
                        "verdict": "edge" if on > off else "dead weight"})
    return pd.DataFrame(out)


# ------------------------------------------------------------------- main ----

def main():
    ap = argparse.ArgumentParser(
        description="VectorBT full-matrix rule ablation, ranked by profit factor.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--strategy", default="all",
                    help="one of {} or 'all'".format("|".join(STRATEGIES)))
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--cost-bps", type=float, default=None,
                    help="cost per side in basis points (default 5)")
    ap.add_argument("--split", type=float, default=0.7,
                    help="IS/OOS date split fraction (default 0.7)")
    ap.add_argument("--max-tickers", type=int, default=0,
                    help="cap the universe for a faster run (0 = all)")
    ap.add_argument("--min-trades", type=int, default=300,
                    help="flag rows under this many trades")
    ap.add_argument("--csv", default="ablation_vbt_results.csv")
    args = ap.parse_args()
    # Single source of truth for the cost assumption (costs.py / config.json
    # "costs.round_trip_bps"). Audit FC-2: these defaults used to be 0.0 or 5.0
    # per-CLI and the headline artifact was generated at 0 bps.
    args.cost_bps = resolve_cost_bps(args.cost_bps,
                                     getattr(args, "config", None))

    strategies = STRATEGIES if args.strategy == "all" else (args.strategy,)
    for s in strategies:
        if s not in RULE_BUILDERS:
            raise SystemExit("Unknown strategy {!r}".format(s))

    with open(args.config) as f:
        cfg = json.load(f)
    from .data_local import load_bars_any
    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
    if args.max_tickers:
        bars_map = dict(list(bars_map.items())[:args.max_tickers])
    print("Building matrix over {} tickers...".format(len(bars_map)))
    m = Market(bars_map, spy_bars)
    split_ts = m.close.index[int(len(m.close.index) * args.split)]
    print("{} bars {} → {} | IS/OOS split at {}".format(
        len(m.close.index), m.close.index[0].date(), m.close.index[-1].date(),
        split_ts.date()))

    all_rows = []
    for strat in strategies:
        th = resolve_thresholds(cfg, strat)
        print("\n=== {} — sweeping rule matrix ===".format(strat))
        all_rows += sweep_strategy(strat, m, th, cfg.get("exits", {}),
                                   args.cost_bps, split_ts)

    df = (pd.DataFrame(all_rows)
          .sort_values("profit_factor", ascending=False, na_position="last")
          .reset_index(drop=True))
    df["low_n"] = df["trades"] < args.min_trades

    pd.set_option("display.width", 200)
    print("\n" + "=" * 100)
    print("RULE-COMBINATION MATRIX — ranked by Profit Factor "
          "(low_n = under {} trades, treat with suspicion)".format(args.min_trades))
    print("=" * 100)
    print(df.round(3).to_string(index=True))

    print("\nPer-rule marginal contribution (median PF with rule on vs off):")
    print(marginal_summary(df).round(3).to_string(index=False))

    if args.csv:
        df.to_csv(args.csv, index=False)
        print("\nWrote {} rows to {}".format(len(df), args.csv))
    return df


if __name__ == "__main__":
    main()
