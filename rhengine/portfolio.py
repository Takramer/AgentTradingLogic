"""
portfolio.py — account-level equity simulation on top of the per-trade backtest.

backtest.py answers "does the signal have edge?" in R-multiples per trade.
This module answers the question that actually matters for an account:
"what does the equity curve look like when trades compete for capital?"

It replays the backtest's trade list chronologically with fixed-fractional
position sizing and realistic capacity constraints:

  - risk a fixed % of CURRENT equity per trade (dollar P&L = risk $ x R)
  - at most --max-positions open at once
  - at most --max-new-per-day entries per session
  - one open position per ticker at a time
  - when signals compete for a slot, higher (score, weighted_score) wins

Equity is marked on a realized (cash) basis: it changes on the exit session.
That is exact for final equity and conservative for intraday drawdown.

Usage:
    python -m rhengine.portfolio                     # backtest then simulate
    python -m rhengine.portfolio --risk-pct 1 --max-positions 5
    python -m rhengine.portfolio --equity-csv curve.csv
"""

import argparse
import json
import math
from bisect import bisect_right


def sessions_from_bars(spy_bars):
    """Trading calendar (sorted ISO dates) from SPY bars."""
    from .backtest import _iso_date
    return [_iso_date(b["time"]) for b in spy_bars]


def _schedule(trades, sessions):
    """Attach entry/exit session dates to each trade.
    Entry = first session after signal_date; exit = entry + (sessions_held - 1)."""
    idx = {d: i for i, d in enumerate(sessions)}
    scheduled = []
    for t in trades:
        si = idx.get(t["signal_date"])
        if si is None:
            # signal date not in SPY calendar (data gap) — find next session
            si = bisect_right(sessions, t["signal_date"]) - 1
            if si < 0:
                continue
        ei = si + 1                      # fill on next session's open
        xi = si + int(t["sessions_held"])  # exit bar index relative to signal
        if ei >= len(sessions):
            continue
        xi = min(xi, len(sessions) - 1)
        s = dict(t)
        s["entry_date"] = sessions[ei]
        s["exit_date"] = sessions[xi]
        scheduled.append(s)
    scheduled.sort(key=lambda s: (s["entry_date"], -s["score"],
                                  -s.get("weighted_score", s["score"])))
    return scheduled


def simulate_portfolio(trades, sessions, start_equity=10_000.0, risk_pct=1.0,
                       max_positions=5, max_new_per_day=2, max_heat_pct=10.0,
                       size_from_hwm=False):
    """Replay trades chronologically under capital constraints.

    Returns dict with:
      equity_curve: [(iso_date, equity), ...] one point per session
      taken / skipped_slots / skipped_dup: trade lists
      stats: final equity, return, CAGR, max drawdown, Sharpe, exposure
    """
    scheduled = _schedule(trades, sessions)
    by_entry = {}
    for s in scheduled:
        by_entry.setdefault(s["entry_date"], []).append(s)

    equity = float(start_equity)
    hwm = equity           # high-watermark sizing base (findings Part 6):
                           # sizing off depleted equity in a drawdown creates
                           # volatility drag; hwm mode keeps risk dollars level
    open_pos = []          # each: scheduled trade + risk_dollars
    taken, skipped_slots, skipped_dup = [], [], []
    curve = []
    busy_sessions = 0

    for day in sessions:
        # 1. close positions exiting today (realize P&L)
        still = []
        for p in open_pos:
            if p["exit_date"] <= day:
                equity += p["risk_dollars"] * p["r_multiple"]
            else:
                still.append(p)
        open_pos = still

        # 2. open new positions entering today
        new_today = 0
        held_tickers = {p["ticker"] for p in open_pos}
        for s in by_entry.get(day, []):
            if s["ticker"] in held_tickers:
                skipped_dup.append(s)
                continue
            if len(open_pos) >= max_positions or new_today >= max_new_per_day:
                skipped_slots.append(s)
                continue
            # Portfolio heat cap: total open risk (capital lost if every stop
            # hits at once) may not exceed max_heat_pct of current equity.
            hwm = max(hwm, equity)
            risk_dollars = (hwm if size_from_hwm else equity) * (risk_pct / 100.0)
            open_heat = sum(q["risk_dollars"] for q in open_pos)
            if open_heat + risk_dollars > equity * (max_heat_pct / 100.0):
                skipped_slots.append(s)
                continue
            p = dict(s)
            p["risk_dollars"] = risk_dollars
            open_pos.append(p)
            held_tickers.add(p["ticker"])
            taken.append(p)
            new_today += 1

        if open_pos:
            busy_sessions += 1
        curve.append((day, round(equity, 2)))

    # force-close anything still open at the end (its exit was clipped to last bar)
    for p in open_pos:
        equity += p["risk_dollars"] * p["r_multiple"]
    if curve:
        curve[-1] = (curve[-1][0], round(equity, 2))

    stats = _curve_stats(curve, start_equity, len(sessions), busy_sessions,
                         taken, skipped_slots, skipped_dup)
    return {"equity_curve": curve, "taken": taken,
            "skipped_slots": skipped_slots, "skipped_dup": skipped_dup,
            "stats": stats}


def _curve_stats(curve, start_equity, n_sessions, busy_sessions,
                 taken, skipped_slots, skipped_dup):
    if not curve:
        return None
    final = curve[-1][1]
    total_ret = final / start_equity - 1.0

    peak, max_dd = start_equity, 0.0
    rets = []
    prev = start_equity
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, 1.0 - eq / peak)
        if prev > 0:
            rets.append(eq / prev - 1.0)
        prev = eq

    years = n_sessions / 252.0 if n_sessions else 0
    cagr = (final / start_equity) ** (1 / years) - 1.0 if years > 0 and final > 0 else 0.0
    mean = sum(rets) / len(rets) if rets else 0.0
    var = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    sharpe = (mean / math.sqrt(var) * math.sqrt(252)) if var > 0 else 0.0

    wins = [t for t in taken if t["r_multiple"] > 0]
    return {
        "start_equity": start_equity,
        "final_equity": round(final, 2),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 2),
        "sessions": n_sessions,
        "exposure_pct": round(100.0 * busy_sessions / n_sessions, 1) if n_sessions else 0.0,
        "trades_taken": len(taken),
        "win_rate_pct": round(100.0 * len(wins) / len(taken), 1) if taken else 0.0,
        "skipped_slots": len(skipped_slots),
        "skipped_dup_ticker": len(skipped_dup),
    }


def print_report(result):
    st = result["stats"]
    if not st:
        print("No sessions simulated.")
        return
    print("\n" + "=" * 52)
    print("PORTFOLIO SIMULATION")
    print("=" * 52)
    print("Start equity : ${:,.0f}".format(st["start_equity"]))
    print("Final equity : ${:,.2f}  ({:+.2f}%)".format(
        st["final_equity"], st["total_return_pct"]))
    print("CAGR         : {:+.2f}%   ({} sessions ~ {:.1f} yr)".format(
        st["cagr_pct"], st["sessions"], st["sessions"] / 252.0))
    print("Max drawdown : {:.2f}%".format(st["max_drawdown_pct"]))
    print("Sharpe (dly) : {:.2f}".format(st["sharpe"]))
    print("Exposure     : {:.1f}% of sessions with an open position".format(
        st["exposure_pct"]))
    print("Trades taken : {}  (win rate {:.1f}%)".format(
        st["trades_taken"], st["win_rate_pct"]))
    print("Skipped      : {} no slot, {} duplicate ticker".format(
        st["skipped_slots"], st["skipped_dup_ticker"]))
    if result["equity_curve"]:
        from .charts import equity_report
        print(equity_report(result["equity_curve"]))


def main():
    ap = argparse.ArgumentParser(
        description="Account-level equity simulation over the walk-forward backtest.")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--cost-bps", type=float, default=0.0)
    ap.add_argument("--start-equity", type=float, default=10_000)
    ap.add_argument("--risk-pct", type=float, default=1.0,
                    help="percent of current equity risked per trade (default 1)")
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--max-new-per-day", type=int, default=2)
    ap.add_argument("--size-from-hwm", action="store_true",
                    help="size risk off high-watermark equity instead of current "
                         "equity (findings Part 6: avoids drawdown volatility drag)")
    ap.add_argument("--max-heat-pct", type=float, default=10.0,
                    help="cap on total open risk as %% of equity (default 10)")
    ap.add_argument("--equity-csv", default=None, help="write equity curve CSV")
    ap.add_argument("--trades-csv", default=None, help="write taken trades CSV")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    from .backtest import run_backtest_on, summarize
    from .data_local import load_bars_any
    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
    trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=args.min_score,
                             forced_strategy=args.strategy, cost_bps=args.cost_bps)
    summarize(trades)

    sessions = sessions_from_bars(spy_bars)
    result = simulate_portfolio(trades, sessions,
                                start_equity=args.start_equity,
                                risk_pct=args.risk_pct,
                                max_positions=args.max_positions,
                                max_new_per_day=args.max_new_per_day,
                                size_from_hwm=args.size_from_hwm,
                                max_heat_pct=args.max_heat_pct)
    print_report(result)

    if args.equity_csv:
        import csv as _csv
        with open(args.equity_csv, "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["date", "equity"])
            w.writerows(result["equity_curve"])
        print("Wrote equity curve to {}".format(args.equity_csv))
    if args.trades_csv and result["taken"]:
        import csv as _csv
        rows = result["taken"]
        with open(args.trades_csv, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("Wrote {} taken trades to {}".format(len(rows), args.trades_csv))


if __name__ == "__main__":
    main()
