"""
autostrat_rank.py — does it matter WHICH signals get the scarce slots?

The capacity finding (docs/AUTOSTRAT-2026-07-31.md): the sleeve fires ~1,400
signals/yr but the account can hold ~20 concurrent positions, so ~90% of signals
are never taken — and portfolio.py takes them in whatever order they arrive,
which is effectively arbitrary. If the taken slice were the BEST slice instead
of an arbitrary one, that is free return with no change to the entry rule.

Rankers tested (all computed from bars at or before the SIGNAL bar — a ranker
that peeks at the entry bar or later is lookahead and would manufacture the
result):

  mom12_1   12-1 momentum, months -12..-2, skipping the last month.
            The one cross-sectional ranker in this repo with a 63-year
            published record (RMX-B's momentum leg, alpha t=+2.10) — so it is
            the defensible prior, not a fitted choice.
  rev1m     LAST month's return, ascending (short-term reversal).
  atrpct    ATR as a % of price, descending. Mechanical, not a signal:
            cost_in_R = bps / stop_pct, so wider stops are cheaper per R.
  dollarvol median dollar volume, descending — real spread falls hard with ADV
            (5.5 bps top decile vs 98.1 bottom, costs.py).
  arrival   the status quo: whatever order trades arrive in.
  random    shuffled, as a control — the spread between `arrival` and `random`
            is how much of any ranker's apparent effect is just ordering noise.

Usage: py scripts/autostrat_rank.py --max-tickers 300 --ticker-offset 120
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from autostrat_lab import apply_cost, day_clustered_t, load_bars, run_parallel, universe
from rhengine.backtest import _iso_date
from rhengine.portfolio import _schedule, sessions_from_bars

RANKERS = ["arrival", "random", "mom12_1", "rev1m", "atrpct", "dollarvol"]


def enrich(trades, lo):
    """Attach causal ranking features, computed strictly from bars at or before
    the signal bar. Returns only trades whose features could be formed."""
    by_ticker = {}
    for t in trades:
        by_ticker.setdefault(t["ticker"], []).append(t)
    out = []
    for tk, rows in by_ticker.items():
        bars = load_bars(tk, lo, None)
        if not bars:
            continue
        idx = {_iso_date(b["time"]): i for i, b in enumerate(bars)}
        close = np.array([b["close"] for b in bars], float)
        high = np.array([b["high"] for b in bars], float)
        low = np.array([b["low"] for b in bars], float)
        vol = np.array([b["volume"] for b in bars], float)
        pc = np.concatenate([[close[0]], close[:-1]])
        tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
        atr = np.convolve(tr, np.ones(14) / 14, mode="full")[:len(tr)]
        dv = close * vol
        for t in rows:
            i = idx.get(t["signal_date"])
            if i is None or i < 252:
                continue
            c0 = close[i]
            if c0 <= 0 or close[i - 252] <= 0 or close[i - 21] <= 0:
                continue
            t = dict(t)
            # months -12..-2: skip the most recent month (it carries reversal)
            t["mom12_1"] = float(close[i - 21] / close[i - 252] - 1.0)
            t["rev1m"] = -float(c0 / close[i - 21] - 1.0)   # ascending -> negate
            t["atrpct"] = float(atr[i] / c0) if atr[i] > 0 else 0.0
            t["dollarvol"] = float(np.median(dv[max(0, i - 20):i + 1]))
            out.append(t)
    return out


def simulate_ranked(trades, sessions, ranker, start_equity=10_000.0,
                    risk_pct=0.5, max_positions=20, max_new_per_day=8,
                    seed=0):
    """portfolio.py's constraint replay, but same-day candidates are ordered by
    `ranker` (descending) before slots are handed out."""
    scheduled = _schedule(trades, sessions)
    by_entry = {}
    for s in scheduled:
        by_entry.setdefault(s["entry_date"], []).append(s)
    rng = random.Random(seed)
    for day, rows in by_entry.items():
        if ranker == "arrival":
            pass
        elif ranker == "random":
            rng.shuffle(rows)
        else:
            rows.sort(key=lambda s: s.get(ranker, 0.0), reverse=True)

    equity, open_pos, curve, taken = float(start_equity), [], [], []
    for day in sessions:
        still = []
        for p in open_pos:
            if p["exit_date"] <= day:
                equity += p["risk_dollars"] * p["r_multiple"]
            else:
                still.append(p)
        open_pos = still
        new_today = 0
        held = {p["ticker"] for p in open_pos}
        for s in by_entry.get(day, []):
            if s["ticker"] in held:
                continue
            if len(open_pos) >= max_positions or new_today >= max_new_per_day:
                continue
            e, st = float(s["entry"]), float(s["stop"])
            sp = (e - st) / e if e > 0 else 0.0
            if sp <= 0:
                continue
            risk_d = equity * risk_pct / 100.0
            open_pos.append(dict(s, risk_dollars=risk_d))
            held.add(s["ticker"])
            new_today += 1
            taken.append(s)
        curve.append(equity)

    eq = np.array(curve, float)
    years = len(sessions) / 252.0
    rets = np.diff(eq) / eq[:-1]
    peak = np.maximum.accumulate(eq)
    tr = np.array([t["r_multiple"] for t in taken], float)
    return {"cagr": 100 * ((eq[-1] / start_equity) ** (1 / years) - 1),
            "maxdd": 100 * ((peak - eq) / peak).max(),
            "sharpe": (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0,
            "taken": len(taken),
            "meanR_taken": tr.mean() if len(tr) else float("nan"),
            "dayT_taken": day_clustered_t(taken)[0] if len(tr) else float("nan")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="macd")
    ap.add_argument("--lo", default="2015-01-01")
    ap.add_argument("--max-tickers", type=int, default=300)
    ap.add_argument("--ticker-offset", type=int, default=120)
    ap.add_argument("--regime", default="downtrend,choppy")
    ap.add_argument("--bps", type=float, default=30.0)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    ap.add_argument("--max-positions", type=int, default=20)
    ap.add_argument("--target", type=float, default=8.0)
    ap.add_argument("--hold", type=int, default=30)
    args = ap.parse_args()

    cfg = json.load(open("config.json"))
    cfg.setdefault("strategy", {}).setdefault("thresholds", {}) \
       .setdefault(args.strategy, {}).update({"stopAtrMult": 1.5,
                                              "targetAtrMult": args.target})
    cfg.setdefault("exits", {})["max_hold_sessions"] = args.hold

    spy = load_bars("SPY", args.lo, None)
    sessions = sessions_from_bars(spy)
    years = len(sessions) / 252.0
    c = np.array([b["close"] for b in spy], float)
    spy_cagr = 100 * ((c[-1] / c[0]) ** (1 / years) - 1)
    syms = sorted(universe(args.lo, None, args.max_tickers, 0.0, args.ticker_offset))
    regs = args.regime.split(",") if args.regime else None

    raw = run_parallel(cfg, syms, spy, args.lo, None, args.strategy, 3, None, regs)
    trades = apply_cost(enrich(raw, args.lo), args.bps)
    allR = np.array([t["r_multiple"] for t in trades], float)
    print(f"universe {len(syms)} | {len(trades)} rankable signals "
          f"(pool meanR {allR.mean():+.4f}) | {args.bps:.0f} bps "
          f"| {args.max_positions} slots | SPY CAGR {spy_cagr:.2f}%\n")
    print(f"{'ranker':<12}{'taken':>7}{'meanR taken':>13}{'dayT':>8}"
          f"{'CAGR%':>9}{'maxDD%':>9}{'Sharpe':>8}{'vsSPY':>8}")
    for rk in RANKERS:
        s = simulate_ranked(trades, sessions, rk, risk_pct=args.risk_pct,
                            max_positions=args.max_positions)
        print(f"{rk:<12}{s['taken']:>7}{s['meanR_taken']:>+13.4f}"
              f"{s['dayT_taken']:>+8.2f}{s['cagr']:>9.2f}{s['maxdd']:>9.1f}"
              f"{s['sharpe']:>8.2f}{s['cagr']-spy_cagr:>+8.2f}")


if __name__ == "__main__":
    main()
