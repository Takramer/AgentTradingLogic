"""
sanity.py — mechanical sanity battery for a backtest's trade list.

Ten cheap checks that catch the classic ways a backtest lies BEFORE anyone
spends hours on walk-forwards. Runs on a trades CSV (seconds, no data fetch)
or on a fresh backtest. Each check returns PASS / WARN / FAIL / SKIP with the
number that triggered it; the process exit code is 1 if anything FAILs
(--strict: WARNs fail too), so agents and CI can gate on it.

Checks:
  sample_size       n >= 300 (the sources' minimum statistical confidence)
  t_stat            mean R distinguishable from zero (>= 2)
  outlier_dependence edge survives deleting the top 1% best trades
  cost_stress       edge survives +5 / +10 bps extra round-trip cost
  is_oos_split      edge exists in the last 30% of the window, not just the first 70%
  ticker_concentration  no single ticker carries the P&L
  month_concentration   no single month carries the P&L
  degenerate_r      no absurd R-multiples (stop-distance pathology)
  score_monotonicity    higher score tiers earn more (else the legs don't rank)
  exit_mix          stops/targets actually fire (time-stop-dominated = geometry mismatch)

Usage:
    py -m rhengine.sanity --trades-csv backtest_trades.csv
    py -m rhengine.sanity --trades-csv trades.csv --strict --json
    py -m rhengine.sanity --lookback-days 400 --cost-bps 5   # fresh backtest
"""

import argparse
import csv
import json
import math
from collections import defaultdict

from .costs import resolve_cost_bps


def _avg(rs):
    return sum(rs) / len(rs) if rs else 0.0


def _t_stat(rs):
    n = len(rs)
    if n < 2:
        return 0.0
    m = _avg(rs)
    var = sum((r - m) ** 2 for r in rs) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else 0.0


def _extra_cost_r(trade, extra_bps):
    """Additional cost in R for extra_bps more ROUND-TRIP cost, from the
    trade's own entry/stop geometry.

    Must stay identical to costs.cost_in_r / backtest._cost_r — this had the
    same 2x double-count they did and was fixed with them on 2026-07-27. It
    made the battery's cost-stress check twice as punitive as advertised.
    """
    entry, stop = trade.get("entry"), trade.get("stop")
    if entry is None or stop is None or entry <= 0 or entry <= stop:
        return None
    return (extra_bps / 10000.0) / ((entry - stop) / entry)


# ----------------------------------------------------------------- checks --
# Each check: (name, status, value, note). SKIP when the CSV lacks a column.

def check_sample_size(trades):
    n = len(trades)
    status = "PASS" if n >= 300 else "WARN" if n >= 100 else "FAIL"
    return ("sample_size", status, f"n={n}",
            "sources: ~300 trades = minimum confidence")


def check_t_stat(trades):
    rs = [t["r_multiple"] for t in trades]
    t = _t_stat(rs)
    status = "PASS" if t >= 2 else "WARN" if t >= 1 else "FAIL"
    return ("t_stat", status, f"t={t:+.2f} avgR={_avg(rs):+.4f}",
            "t>=2: edge distinguishable from noise")


def check_outlier_dependence(trades):
    rs = sorted((t["r_multiple"] for t in trades), reverse=True)
    if len(rs) < 20:
        return ("outlier_dependence", "SKIP", "n<20", "")
    k = max(1, len(rs) // 100)  # top 1%
    full, trimmed = _avg(rs), _avg(rs[k:])
    if full <= 0:
        return ("outlier_dependence", "SKIP", f"avgR {full:+.4f} <= 0",
                "no positive edge to stress")
    status = ("FAIL" if trimmed <= 0
              else "WARN" if trimmed < full * 0.4 else "PASS")
    return ("outlier_dependence", status,
            f"avgR {full:+.4f} -> {trimmed:+.4f} (top {k} cut)",
            "edge should not live in a handful of home runs")


def check_cost_stress(trades):
    if any(t.get("entry") is None or t.get("stop") is None for t in trades):
        return ("cost_stress", "SKIP", "no entry/stop columns", "")
    def stressed(extra):
        out = []
        for t in trades:
            c = _extra_cost_r(t, extra)
            if c is not None:
                out.append(t["r_multiple"] - c)
        return _avg(out)
    base, p5, p10 = _avg([t["r_multiple"] for t in trades]), stressed(5), stressed(10)
    if base <= 0:
        return ("cost_stress", "SKIP", f"avgR {base:+.4f} <= 0",
                "no positive edge to stress")
    status = "FAIL" if p5 <= 0 else "WARN" if p10 <= 0 else "PASS"
    return ("cost_stress", status,
            f"avgR +0bps {base:+.4f} | +5 {p5:+.4f} | +10 {p10:+.4f}",
            "a real edge survives being charged more")


def check_is_oos_split(trades, split=0.7):
    dates = sorted(t["signal_date"] for t in trades if t.get("signal_date"))
    if not dates:
        return ("is_oos_split", "SKIP", "no signal_date column", "")
    cut = dates[int(len(dates) * split)]
    is_rs = [t["r_multiple"] for t in trades if t["signal_date"] < cut]
    oos_rs = [t["r_multiple"] for t in trades if t["signal_date"] >= cut]
    if len(oos_rs) < 20:
        return ("is_oos_split", "SKIP", f"OOS n={len(oos_rs)}", "")
    is_a, oos_a = _avg(is_rs), _avg(oos_rs)
    status = ("PASS" if oos_a > 0
              else "WARN" if oos_a > -0.01 else "FAIL")
    return ("is_oos_split", status,
            f"IS {is_a:+.4f} (n={len(is_rs)}) | OOS {oos_a:+.4f} (n={len(oos_rs)})",
            f"split at {cut}; IS-only wins = curve fit")


def check_ticker_concentration(trades):
    if any("ticker" not in t for t in trades):
        return ("ticker_concentration", "SKIP", "no ticker column", "")
    gross = defaultdict(float)
    total = 0.0
    for t in trades:
        if t["r_multiple"] > 0:
            gross[t["ticker"]] += t["r_multiple"]
            total += t["r_multiple"]
    if total <= 0:
        return ("ticker_concentration", "SKIP", "no gross profit", "")
    top, share = max(gross.items(), key=lambda kv: kv[1])
    pct = share / total * 100
    status = "PASS" if pct <= 15 else "WARN" if pct <= 30 else "FAIL"
    return ("ticker_concentration", status, f"{top} = {pct:.1f}% of gross wins",
            "one ticker carrying the edge = idiosyncratic luck")


def check_month_concentration(trades):
    if any(not t.get("signal_date") for t in trades):
        return ("month_concentration", "SKIP", "no signal_date column", "")
    gross = defaultdict(float)
    total = 0.0
    for t in trades:
        if t["r_multiple"] > 0:
            gross[t["signal_date"][:7]] += t["r_multiple"]
            total += t["r_multiple"]
    if total <= 0:
        return ("month_concentration", "SKIP", "no gross profit", "")
    top, share = max(gross.items(), key=lambda kv: kv[1])
    pct = share / total * 100
    status = "PASS" if pct <= 40 else "WARN" if pct <= 60 else "FAIL"
    return ("month_concentration", status, f"{top} = {pct:.1f}% of gross wins",
            "one month carrying the edge = one regime event")


def check_degenerate_r(trades):
    worst = max(abs(t["r_multiple"]) for t in trades)
    status = "PASS" if worst <= 10 else "WARN" if worst <= 20 else "FAIL"
    return ("degenerate_r", status, f"max |R| = {worst:.1f}",
            "R>20 usually means a near-zero stop distance slipped through")


def check_score_monotonicity(trades):
    tiers = defaultdict(list)
    for t in trades:
        if t.get("score") is not None:
            tiers[int(t["score"])].append(t["r_multiple"])
    tiers = {k: v for k, v in tiers.items() if len(v) >= 20}
    if len(tiers) < 2:
        return ("score_monotonicity", "SKIP", "one usable score tier", "")
    keys = sorted(tiers)
    means = [_avg(tiers[k]) for k in keys]
    label = " | ".join(f"{k}: {m:+.3f} (n={len(tiers[k])})"
                       for k, m in zip(keys, means))
    status = "PASS" if all(b >= a - 0.005 for a, b in zip(means, means[1:])) \
        else "WARN"
    return ("score_monotonicity", status, label,
            "higher tiers should earn more, else extra legs don't rank")


def check_exit_mix(trades):
    if any("exit_reason" not in t for t in trades):
        return ("exit_mix", "SKIP", "no exit_reason column", "")
    counts = defaultdict(int)
    for t in trades:
        counts[t["exit_reason"]] += 1
    n = len(trades)
    time_pct = counts.get("Time", 0) / n * 100
    label = " | ".join(f"{k} {v / n * 100:.0f}%" for k, v in sorted(counts.items()))
    status = "PASS" if time_pct <= 60 else "WARN"
    return ("exit_mix", status, label,
            "time-stop-dominated = stop/target geometry rarely reached")


CHECKS = [check_sample_size, check_t_stat, check_outlier_dependence,
          check_cost_stress, check_is_oos_split, check_ticker_concentration,
          check_month_concentration, check_degenerate_r,
          check_score_monotonicity, check_exit_mix]


def run_battery(trades):
    """All checks over a trade list (dicts with at least r_multiple).
    Returns [(name, status, value, note)]."""
    if not trades:
        return [("sample_size", "FAIL", "n=0", "no trades at all")]
    return [c(trades) for c in CHECKS]


# ---------------------------------------------------------------- loading --

def load_trades_csv(path):
    """Trades CSV -> list of dicts with numeric fields coerced. Tolerates
    missing columns (checks SKIP on absence)."""
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("r_multiple"):
                continue
            t = {"r_multiple": float(row["r_multiple"])}
            for k in ("entry", "stop", "target", "exit_price"):
                t[k] = float(row[k]) if row.get(k) else None
            for k in ("ticker", "strategy", "regime", "exit_reason"):
                if k in row:
                    t[k] = row[k]
            t["signal_date"] = (row.get("signal_date") or
                                (row.get("entry_date") or "")[:10] or None)
            t["score"] = int(float(row["score"])) if row.get("score") else None
            out.append(t)
    return out


def print_battery(rows):
    order = {"FAIL": 0, "WARN": 1, "PASS": 2, "SKIP": 3}
    print("\n" + "=" * 68)
    print("SANITY BATTERY")
    print("=" * 68)
    for name, status, value, note in rows:
        print(f"  [{status:<4}] {name:<22} {value}")
        if note and status in ("WARN", "FAIL"):
            print(f"         {'':<22} ^ {note}")
    counts = defaultdict(int)
    for _, status, _, _ in rows:
        counts[status] += 1
    print("\n  " + "  ".join(f"{s}:{counts[s]}"
                             for s in ("PASS", "WARN", "FAIL", "SKIP")))
    worst = min((r[1] for r in rows), key=lambda s: order[s])
    verdict = {"FAIL": "REJECT — fix the failing checks before any tuning work.",
               "WARN": "CAUTION — investigate warnings before trusting metrics.",
               }.get(worst, "CLEAN — proceed to walk-forward / Monte Carlo.")
    print(f"  VERDICT: {verdict}")
    return counts


def main():
    ap = argparse.ArgumentParser(description="Sanity battery for a trade list.")
    ap.add_argument("--trades-csv", default=None,
                    help="run on an existing trades CSV (fast path)")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--cost-bps", type=float, default=None)
    ap.add_argument("--max-tickers", type=int, default=0)
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable results on stdout")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on WARN as well as FAIL")
    args = ap.parse_args()
    # Single source of truth for the cost assumption (costs.py / config.json
    # "costs.round_trip_bps"). Audit FC-2: these defaults used to be 0.0 or 5.0
    # per-CLI and the headline artifact was generated at 0 bps.
    args.cost_bps = resolve_cost_bps(args.cost_bps,
                                     getattr(args, "config", None))

    if args.trades_csv:
        trades = load_trades_csv(args.trades_csv)
    else:
        with open(args.config) as f:
            cfg = json.load(f)
        from .data_local import load_bars_any
        from .backtest import run_backtest_on
        bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
        if args.max_tickers:
            bars_map = dict(list(bars_map.items())[:args.max_tickers])
        trades = run_backtest_on(cfg, bars_map, spy_bars,
                                 min_score=args.min_score,
                                 forced_strategy=args.strategy,
                                 cost_bps=args.cost_bps)

    rows = run_battery(trades)
    if args.json:
        print(json.dumps([{"check": n, "status": s, "value": v, "note": note}
                          for n, s, v, note in rows], indent=2))
    else:
        print_battery(rows)

    statuses = {r[1] for r in rows}
    if "FAIL" in statuses or (args.strict and "WARN" in statuses):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
