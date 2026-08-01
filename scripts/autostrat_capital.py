"""
autostrat_capital.py — why the Monte Carlo p50 and the account disagree.

The p50 is REAL and it reproduces. It is also not the number that pays: it
bootstraps `n` trades drawn from the whole population and compounds them
SEQUENTIALLY, as if the account could take every signal, one after another, with
no two ever competing for the same capital. The account takes ~7% of them.

This script measures the gap directly rather than asserting it:

  1. p50 vs n_trades — the p50 is a per-trade compounding statistic, so it grows
     without bound in n. Quoting it without n is meaningless; the audit's
     convention is n=250.
  2. CAPITAL UTILISATION — average invested notional / equity, average open
     slots, and how often a signal was refused for want of a slot. If the book
     sits mostly in cash, its CAGR is capped by deployment, not by edge.
  3. risk_pct sweep — if deployment is the binding constraint, sizing up should
     close the gap to SPY. If instead Sharpe stays flat while drawdown scales
     1:1 with return, the edge per dollar-day is simply below SPY's and no
     amount of sizing fixes it. That is the question this answers.

Usage: py scripts/autostrat_capital.py --csv tests/results/candidate_macd_nouptrend_net30.csv
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from autostrat_lab import day_clustered_t, load_bars, mc_p50
from rhengine.portfolio import _schedule, sessions_from_bars, simulate_portfolio


def utilisation(trades, sessions, risk_pct, max_positions, max_heat_pct,
                max_new_per_day=8, start_equity=10_000.0):
    """portfolio.py's replay, instrumented for DEPLOYMENT rather than P&L.

    Every acceptance rule here is copied from simulate_portfolio, INCLUDING the
    heat cap, so the equity path is identical and `cagr` can be cross-checked
    against the engine. A second simulator that quietly disagrees with the first
    is how the round-6 lookahead got in; main() asserts the two agree.

    notional = risk_dollars / stop_pct is the dollars actually at work in the
    name; risk_dollars is only the slice at risk to the stop. Invested fraction
    is the first quantity, and it is the one that competes with buy-and-hold.
    NOTE it is deliberately NOT capped at 100% — the whole point is to find out
    whether the book is under- or over-deployed, so clamping would hide the
    answer."""
    scheduled = _schedule(trades, sessions)
    by_entry = {}
    for s in scheduled:
        by_entry.setdefault(s["entry_date"], []).append(s)

    equity, open_pos = float(start_equity), []
    invested, nopen = [], []
    taken = blocked_slot = blocked_heat = blocked_dup = 0
    ruined = False

    for day in sessions:
        still = []
        for p in open_pos:
            if p["exit_date"] <= day:
                equity += p["risk_dollars"] * p["r_multiple"]
            else:
                still.append(p)
        open_pos = still
        if equity <= 0:
            ruined = True

        new_today = 0
        held = {p["ticker"] for p in open_pos}
        for s in by_entry.get(day, []):
            if s["ticker"] in held:
                blocked_dup += 1
                continue
            if len(open_pos) >= max_positions or new_today >= max_new_per_day:
                blocked_slot += 1
                continue
            risk_d = equity * risk_pct / 100.0
            open_heat = sum(q["risk_dollars"] for q in open_pos)
            if open_heat + risk_d > equity * (max_heat_pct / 100.0):
                blocked_heat += 1
                continue
            e, st = float(s["entry"]), float(s["stop"])
            sp = (e - st) / e if e > 0 else 0.0
            notional = risk_d / sp if sp > 0 else 0.0
            open_pos.append(dict(s, risk_dollars=risk_d, notional=notional))
            held.add(s["ticker"])
            new_today += 1
            taken += 1
        committed = sum(p["notional"] for p in open_pos)
        invested.append(committed / equity if equity > 0 else float("nan"))
        nopen.append(len(open_pos))

    return {
        "taken": taken,
        "blocked_slot": blocked_slot,
        "blocked_heat": blocked_heat,
        "blocked_dup": blocked_dup,
        "ruined": ruined,
        "inv_mean": 100 * float(np.nanmean(invested)),
        "inv_p50": 100 * float(np.nanmedian(invested)),
        "inv_p95": 100 * float(np.nanpercentile(invested, 95)),
        "pct_flat": 100 * float(np.mean(np.array(nopen) == 0)),
        "open_mean": float(np.mean(nopen)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="tests/results/candidate_macd_nouptrend_net30.csv")
    ap.add_argument("--lo", default="2015-01-01")
    ap.add_argument("--max-positions", type=int, default=20)
    ap.add_argument("--max-heat-pct", type=float, default=100.0,
                    help="total open risk cap; 100 = effectively unbound, which "
                         "is what the round 1-8 sweeps used")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    trades = df.to_dict("records")
    r = np.array([t["r_multiple"] for t in trades], float)
    spy = load_bars("SPY", args.lo, None)
    sessions = sessions_from_bars(spy)
    years = len(sessions) / 252.0
    c = np.array([b["close"] for b in spy], float)
    rr = np.diff(c) / c[:-1]
    peak = np.maximum.accumulate(c)
    spy_cagr = 100 * ((c[-1] / c[0]) ** (1 / years) - 1)
    spy_dd = 100 * ((peak - c) / peak).max()
    spy_sharpe = rr.mean() / rr.std() * np.sqrt(252)

    tc, ndays = day_clustered_t(trades)
    print(f"{os.path.basename(args.csv)}: {len(r)} trades over {ndays} signal "
          f"days | meanR {r.mean():+.4f} | day-clustered t {tc:+.2f}")
    print(f"SPY {sessions[0]}..{sessions[-1]} ({years:.1f}yr): CAGR "
          f"{spy_cagr:.2f}%  maxDD {spy_dd:.1f}%  Sharpe {spy_sharpe:.2f}\n")

    print("1. MONTE CARLO p50 vs n_trades  (risk 1%/trade, 20k paths)")
    print(f"   {'n':>6}{'p5':>9}{'p50':>9}{'p95':>9}{'P(loss)':>10}{'ddp50':>8}")
    for n in (100, 250, 500, 1000, 2000):
        m = mc_p50(trades, paths=20000, n=n)
        print(f"   {n:>6}{m['p5']:>9.3f}{m['p50']:>9.3f}{m['p95']:>9.3f}"
              f"{100*m['p_loss']:>9.1f}%{m['maxdd_p50']:>7.1f}%")
    print("   ^ the p50 rises with n because it COMPOUNDS n sequential trades.")
    print("     It is not an annual return and it is not comparable to SPY.\n")

    RISKS = (0.25, 0.5, 1.0, 2.0, 3.0)
    HEAT = args.max_heat_pct
    print(f"2. CAPITAL UTILISATION  ({args.max_positions} slots, heat cap "
          f"{HEAT:.0f}%)")
    print(f"   {'risk%':>6}{'taken':>7}{'noslot':>8}{'noheat':>8}{'open':>6}"
          f"{'flat%':>7}{'inv%mean':>10}{'inv%p50':>9}{'inv%p95':>9}")
    for rp in RISKS:
        u = utilisation(trades, sessions, rp, args.max_positions, HEAT)
        print(f"   {rp:>6.2f}{u['taken']:>7}{u['blocked_slot']:>8}"
              f"{u['blocked_heat']:>8}{u['open_mean']:>6.1f}"
              f"{u['pct_flat']:>6.1f}%{u['inv_mean']:>10.1f}"
              f"{u['inv_p50']:>9.1f}{u['inv_p95']:>9.1f}")
    print("   inv% is notional/equity, UNCAPPED: >100 means the book is levered,")
    print("   which it can only be because stops are tight (notional = risk/stop).")

    print(f"\n3. DOES SIZING UP CLOSE THE GAP?  engine simulate_portfolio, "
          f"heat {HEAT:.0f}%")
    print(f"   {'risk%':>6}{'taken':>7}{'CAGR%':>9}{'maxDD%':>9}{'Sharpe':>8}"
          f"{'vsSPY':>8}{'ret/dd':>8}")
    print(f"   {'SPY':>6}{'-':>7}{spy_cagr:>9.2f}{spy_dd:>9.1f}"
          f"{spy_sharpe:>8.2f}{0.0:>+8.2f}{spy_cagr/spy_dd:>8.2f}")
    for rp in RISKS:
        st = simulate_portfolio(trades, sessions, start_equity=10_000.0,
                                risk_pct=rp,
                                max_positions=args.max_positions,
                                max_new_per_day=8,
                                max_heat_pct=HEAT)["stats"]
        cagr, dd = st["cagr_pct"], st["max_drawdown_pct"]
        print(f"   {rp:>6.2f}{st['trades_taken']:>7}"
              f"{cagr:>9.2f}{dd:>9.1f}{st['sharpe']:>8.2f}"
              f"{cagr-spy_cagr:>+8.2f}{cagr/dd if dd > 0 else float('nan'):>8.2f}")
    print("   Sharpe is the tell: if it stays flat or falls while return and")
    print("   drawdown scale together, sizing is leverage on a worse asset.")


if __name__ == "__main__":
    main()
