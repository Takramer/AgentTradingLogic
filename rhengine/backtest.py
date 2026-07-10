"""
backtest.py — walk-forward historical backtest for the Nydex strategy engine.

Purpose: validate strategy/threshold/exit changes OUT-OF-SAMPLE before they touch
the live account. The system has no closed live trades yet, so this is the only
honest way to judge whether a tuning helps or just overfits.

Method (no lookahead):
  For each ticker and each historical day t (after a warmup):
    1. Classify the SPY regime using SPY bars up to and including day t.
    2. Select the regime's strategy (or a forced --strategy) and score the ticker
       using ONLY bars[:t+1] — the score sees exactly what production would have
       seen at that day's close.
    3. If a qualifying LONG setup exists (score >= --min-score), simulate the
       trade: fill at the NEXT bar's open, then walk forward bar-by-bar applying
       the same stop / target / time-stop rules the live system uses.
    4. Record the outcome in R-multiples (R = risk = entry - stop).

Only long setups are simulated — Robinhood MCP cannot short, so that matches live.
Within a bar, if both stop and target are touched, the stop is assumed first
(pessimistic), so reported results are conservative.

Usage:
    python3 backtest.py                       # auto regime, all watchlist, 400d
    python3 backtest.py --strategy macd       # force one strategy
    python3 backtest.py --lookback-days 500 --min-score 3
    python3 backtest.py --csv backtest_trades.csv   # dump per-trade rows

Aggregates win rate / avg R / expectancy / profit factor by score tier and by
weighted-score tier, so you can see whether 4/4 really beats 3/4 and whether the
weighted primary-leg score separates winners better than the flat count.
"""

import argparse
import json
from collections import defaultdict

from .market_data import fetch_bars, fetch_bars_batch_massive, _data_source
from .indicators import (
    score_ticker,
    classify_spy_regime,
    estimate_vix_regime,
    select_strategy_for_regime,
    resolve_thresholds,
)

WARMUP_BARS = 60  # strategies need ~55-60 bars of history before they score


def _max_hold(strategy, ticker, cfg):
    exits = cfg.get("exits", {})
    if ticker in set(cfg.get("leveraged_tickers", [])):
        return int(exits.get("leveraged_max_hold_sessions", 3))
    return int(exits.get("max_hold_sessions", 10))


def simulate_long(bars, t, stop, target, max_hold, atr=None, exits_cfg=None):
    """
    Fill at bars[t+1] open; walk forward up to max_hold sessions.
    Returns (reason, exit_price, r_multiple, sessions_held) or None if no fill bar.
    R = (exit - entry) / (entry - stop).

    Mirrors the live exit engine (order_plan.py): hard stop, target,
    breakeven trail once close >= entry + trail_trigger_mult*ATR, and a
    ratchet-only chandelier stop at highest-high - chandelier_atr_mult*ATR.
    Trail levels are updated from a bar only AFTER that bar's exit checks,
    so a bar's own high can't move a stop it then hits.
    """
    if t + 1 >= len(bars):
        return None  # no next bar to fill on
    entry = bars[t + 1]["open"]
    risk = entry - stop
    if risk <= 0:
        return None  # degenerate stop (at/above entry)

    exits_cfg = exits_cfg or {}
    trail_at = chand_dist = None
    if atr and atr > 0:
        trail_at   = entry + float(exits_cfg.get("trail_trigger_mult", 1.5)) * atr
        chand_dist = float(exits_cfg.get("chandelier_atr_mult", 3.0)) * atr

    cur_stop = stop
    hi_mark  = entry
    last = min(t + max_hold, len(bars) - 1)
    for k in range(t + 1, last + 1):
        b = bars[k]
        held = k - t
        stop_label = "Trail" if cur_stop > stop else "SL"
        # Gap-aware fills: an open beyond the level fills at the open.
        # Pessimistic ordering: check stop before target within the same bar.
        if b["open"] <= cur_stop:
            return (stop_label, b["open"], (b["open"] - entry) / risk, held)
        if b["open"] >= target:
            return ("TP", b["open"], (b["open"] - entry) / risk, held)
        if b["low"] <= cur_stop:
            return (stop_label, cur_stop, (cur_stop - entry) / risk, held)
        if b["high"] >= target:
            return ("TP", target, (target - entry) / risk, held)
        # Update trailing levels for subsequent bars.
        hi_mark = max(hi_mark, b["high"])
        if trail_at is not None and b["close"] >= trail_at:
            cur_stop = max(cur_stop, entry)  # breakeven trail
        if chand_dist is not None:
            cur_stop = max(cur_stop, hi_mark - chand_dist)  # chandelier, ratchet-only
    # Time stop at the close of the last held bar.
    exit_price = bars[last]["close"]
    return ("Time", exit_price, (exit_price - entry) / risk, last - t)


def load_bars(cfg, lookback_days=400, verbose=True):
    """Fetch daily bars once; returns (bars_map, spy_bars). Per-symbol because the
    batch endpoint shares a single 10k-bar cap that truncates long windows."""
    watchlist = cfg["watchlist"]
    spy_ticker = cfg.get("spy_ticker", "SPY")
    interval = cfg["strategy"].get("interval", "1d")

    fetch = list(dict.fromkeys(watchlist + [spy_ticker]))
    if verbose:
        print(f"Fetching {len(fetch)} symbols, {lookback_days}d of history "
              f"(source={_data_source()})...")
    if _data_source() == "massive":
        # One cached whole-market pass covers every ticker — no per-symbol cap or
        # rate-limit fan-out. Cache depth bounds how far back the backtest can go.
        bars_map = fetch_bars_batch_massive(fetch, lookback_days=lookback_days,
                                            verbose=verbose)["bars"]
    else:
        bars_map = {}
        skipped = []
        for sym in fetch:
            try:
                bars_map[sym] = fetch_bars(sym, interval=interval, lookback_days=lookback_days)
            except Exception as e:
                skipped.append(f"{sym} ({type(e).__name__})")
        if verbose and skipped:
            print(f"  Skipped {len(skipped)}: {', '.join(skipped)}")

    spy_bars = bars_map.pop(spy_ticker, None)
    if not spy_bars:
        raise RuntimeError(f"{spy_ticker} bars required for regime classification")
    return bars_map, spy_bars


def _cost_r(entry, stop, cost_bps):
    """Round-trip transaction cost expressed in R (risk units). A market order in
    and out costs ~2 * cost_bps of notional; dividing by the stop distance converts
    that to R. At a +0.1R edge, a few bps each way is not negligible — modeling it
    keeps the backtest honest about NET performance."""
    if not cost_bps or entry <= 0:
        return 0.0
    stop_pct = (entry - stop) / entry
    if stop_pct <= 0:
        return 0.0
    return 2.0 * (cost_bps / 10000.0) / stop_pct


def run_backtest_on(cfg, bars_map, spy_bars, min_score=3, forced_strategy=None,
                    date_lo=None, date_hi=None, regime_only=None, cost_bps=0.0):
    """Score + simulate over pre-fetched bars. Pure/in-memory so a sweep can call
    it many times without re-hitting the API. `date_lo`/`date_hi` (ISO strings)
    restrict signals to [date_lo, date_hi) for in-sample / out-of-sample splits.
    `regime_only` (e.g. 'uptrend') restricts to signals fired in that SPY regime —
    used to evaluate which strategy a given regime slot should route to."""
    mode = cfg["strategy"].get("mode", "auto")
    bear_only = set(cfg.get("bear_regime_only", []))
    regime_set = ({regime_only} if isinstance(regime_only, str)
                  else set(regime_only) if regime_only else None)

    trades = []
    for ticker, bars in bars_map.items():
        n = len(bars)
        if n < WARMUP_BARS + 2:
            continue
        spy_time_to_idx = {b["time"]: idx for idx, b in enumerate(spy_bars)}

        for t in range(WARMUP_BARS, n - 1):
            sig_date = _iso_date(bars[t]["time"])
            if date_lo is not None and sig_date < date_lo:
                continue
            if date_hi is not None and sig_date >= date_hi:
                continue

            spy_idx = spy_time_to_idx.get(bars[t]["time"])
            if spy_idx is None or spy_idx < WARMUP_BARS:
                continue
            spy_hist = spy_bars[: spy_idx + 1]
            regime = classify_spy_regime(spy_hist)
            if regime_set is not None and regime not in regime_set:
                continue

            if forced_strategy:
                strategy = forced_strategy
            else:
                strategy = (select_strategy_for_regime(regime)
                            if mode == "auto" else mode)

            if ticker in bear_only and regime != "downtrend":
                continue

            thresholds = resolve_thresholds(cfg, strategy)
            hist = bars[: t + 1]
            setups = score_ticker(ticker, hist, strategy, thresholds, spy_hist)
            if not setups:
                continue

            for s in setups:
                if s["direction"] != "bull":
                    continue  # live system is long-only
                if s["score"] < min_score:
                    continue
                sim = simulate_long(bars, t, s["stop"], s["target"],
                                    _max_hold(strategy, ticker, cfg),
                                    atr=s.get("atr"),
                                    exits_cfg=cfg.get("exits", {}))
                if sim is None:
                    continue
                reason, exit_price, r, held = sim
                r_net = r - _cost_r(bars[t + 1]["open"], s["stop"], cost_bps)
                trades.append({
                    "ticker": ticker,
                    "strategy": strategy,
                    "regime": regime,
                    "vix_regime": estimate_vix_regime(spy_hist),
                    "signal_date": sig_date,
                    "score": s["score"],
                    "weighted_score": s.get("weighted_score", s["score"]),
                    "entry": round(bars[t + 1]["open"], 4),
                    "stop": s["stop"],
                    "target": s["target"],
                    "exit_reason": reason,
                    "exit_price": round(exit_price, 4),
                    "r_multiple": round(r_net, 4),
                    "sessions_held": held,
                })

    return trades


def run_backtest(cfg, lookback_days=400, min_score=3, forced_strategy=None,
                 verbose=True, cost_bps=0.0):
    """Convenience wrapper: fetch then run over the full window."""
    bars_map, spy_bars = load_bars(cfg, lookback_days=lookback_days, verbose=verbose)
    return run_backtest_on(cfg, bars_map, spy_bars, min_score=min_score,
                           forced_strategy=forced_strategy, cost_bps=cost_bps)


def _iso_date(ms):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .astimezone(ZoneInfo("America/New_York")).date().isoformat())


def _stats(trades):
    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t["r_multiple"] > 0]
    gross_win = sum(t["r_multiple"] for t in wins)
    gross_loss = -sum(t["r_multiple"] for t in trades if t["r_multiple"] <= 0)
    avg_r = sum(t["r_multiple"] for t in trades) / n
    return {
        "n": n,
        "win_rate": len(wins) / n,
        "avg_r": avg_r,
        "expectancy_r": avg_r,  # per-trade expectancy in R
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
    }


def _print_group(title, groups):
    print(f"\n{title}")
    print(f"  {'key':<16} {'n':>5} {'win%':>7} {'avgR':>7} {'PF':>7}")
    for key in sorted(groups):
        st = _stats(groups[key])
        if not st:
            continue
        pf = "inf" if st["profit_factor"] == float("inf") else f"{st['profit_factor']:.2f}"
        print(f"  {str(key):<16} {st['n']:>5} {st['win_rate']*100:>6.1f}% "
              f"{st['avg_r']:>+7.2f} {pf:>7}")


def summarize(trades):
    overall = _stats(trades)
    if not overall:
        print("\nNo trades generated on this configuration/window.")
        return
    print("\n" + "=" * 52)
    print("BACKTEST SUMMARY")
    print("=" * 52)
    pf = "inf" if overall["profit_factor"] == float("inf") else f"{overall['profit_factor']:.2f}"
    print(f"Trades: {overall['n']}  |  Win rate: {overall['win_rate']*100:.1f}%  "
          f"|  Avg R: {overall['avg_r']:+.2f}  |  Profit factor: {pf}")
    print("(R = risk per trade; +1.0 avg R would mean each trade nets one unit of risk)")

    by_score = defaultdict(list)
    by_wscore = defaultdict(list)
    by_strategy = defaultdict(list)
    by_reason = defaultdict(list)
    for t in trades:
        by_score[f"{t['score']}/4"].append(t)
        by_wscore[f"w={t['weighted_score']}"].append(t)
        by_strategy[t["strategy"]].append(t)
        by_reason[t["exit_reason"]].append(t)

    _print_group("By score tier (is 4/4 really better than 3/4?):", by_score)
    _print_group("By weighted score (does primary-leg weighting separate winners?):", by_wscore)
    _print_group("By strategy:", by_strategy)
    _print_group("By exit reason:", by_reason)
    print()


def main():
    ap = argparse.ArgumentParser(description="Walk-forward backtest of the Nydex engine.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=3,
                    help="minimum passing legs to take a trade (default 3)")
    ap.add_argument("--strategy", default=None,
                    help="force a single strategy instead of regime-auto")
    ap.add_argument("--csv", default=None, help="write per-trade rows to this CSV")
    ap.add_argument("--cost-bps", type=float, default=0.0,
                    help="round-trip transaction cost in basis points (e.g. 5)")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    trades = run_backtest(cfg, lookback_days=args.lookback_days,
                          min_score=args.min_score,
                          forced_strategy=args.strategy,
                          cost_bps=args.cost_bps)
    summarize(trades)

    if args.csv and trades:
        import csv
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
            w.writeheader()
            w.writerows(trades)
        print(f"Wrote {len(trades)} trades to {args.csv}")


if __name__ == "__main__":
    main()
