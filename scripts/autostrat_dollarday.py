"""
autostrat_dollarday.py — return per dollar-day, and the excess over SPY.

WHY THIS METRIC. A trade's return ON THE CAPITAL IT TIES UP is exactly

    hpr = r_multiple * stop_pct

because portfolio.py sizes notional = risk_dollars / stop_pct, so
P&L / notional = (risk * R) / (risk / stop_pct) = R * stop_pct. That identity
means the holding-period return is a property of the TRADE alone — no sizing,
no slot count, no heat cap, no leverage assumption. It is the one number that
survives the R-space/capitalised-space translation intact.

Divide by sessions_held and you get return per dollar-day, which is directly
comparable to SPY's ~0.05%/day. And because every dollar in this book is a
dollar not in the index, the number that actually decides the strategy is

    excess = hpr - (SPY return over the SAME entry..exit window)

If mean excess is <= 0, the sleeve is a worse place to park a dollar than the
index over precisely the days it chooses to be invested — and then no exit
retune, ranker, slot count or position size can rescue it, because all of those
only redistribute dollar-days they cannot make more productive.

A high mean R with a tight stop and a long hold is the trap: +0.25R sounds
strong, but 0.25 * 5% stop = 1.25% earned over 60 sessions = 0.02%/day, which
loses to the index by more than half.

Usage: py scripts/autostrat_dollarday.py --csv tests/results/candidate_macd_nouptrend_net30.csv
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from autostrat_lab import load_bars
from rhengine.backtest import _iso_date
from rhengine.portfolio import _schedule, sessions_from_bars


def attach(trades, sessions, spy):
    """Add hpr, the matched SPY window return, and the excess."""
    spy_px = {_iso_date(b["time"]): b["close"] for b in spy}
    sess_ix = {d: i for i, d in enumerate(sessions)}
    out = []
    for s in _schedule(trades, sessions):
        e, st = float(s["entry"]), float(s["stop"])
        sp = (e - st) / e if e > 0 else 0.0
        if sp <= 0:
            continue
        i, j = sess_ix.get(s["entry_date"]), sess_ix.get(s["exit_date"])
        if i is None or j is None or j <= i:
            continue
        p0, p1 = spy_px.get(sessions[i]), spy_px.get(sessions[j])
        if not p0 or not p1:
            continue
        held = j - i
        hpr = float(s["r_multiple"]) * sp
        bench = p1 / p0 - 1.0
        out.append(dict(s, stop_pct=sp, held=held, hpr=hpr, bench=bench,
                        excess=hpr - bench))
    return out


def dayt(rows, key):
    """FC-3 day clustering on an arbitrary field."""
    by = {}
    for r in rows:
        by.setdefault(r["signal_date"], []).append(r[key])
    if len(by) < 3:
        return float("nan")
    d = np.array([np.mean(v) for v in by.values()])
    sd = d.std(ddof=1)
    return float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else float("nan")


def block(label, rows, spy_daily):
    if len(rows) < 30:
        print(f"  {label:<22}{len(rows):>7}   (too few)")
        return
    hpr = np.array([r["hpr"] for r in rows], float)
    exc = np.array([r["excess"] for r in rows], float)
    held = np.array([r["held"] for r in rows], float)
    # DOLLAR-WEIGHTED rate: sum(return) / sum(days). The mean of per-trade
    # ratios is not this — a -5% loss closed in 2 days reads as -250 bp/day and
    # swamps the average, which describes the ratio's skew, not the book's rate.
    bench = np.array([r["bench"] for r in rows], float)
    perday = hpr.sum() / held.sum()
    bperday = bench.sum() / held.sum()
    print(f"  {label:<22}{len(rows):>7}{100*hpr.mean():>9.2f}"
          f"{held.mean():>8.1f}{1e4*perday:>10.2f}{1e4*bperday:>10.2f}"
          f"{1e4*(perday-bperday):>9.2f}"
          f"{100*exc.mean():>10.2f}{dayt(rows, 'excess'):>8.2f}"
          f"{100*(exc > 0).mean():>8.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="tests/results/candidate_macd_nouptrend_net30.csv")
    ap.add_argument("--lo", default="2015-01-01")
    args = ap.parse_args()

    trades = pd.read_csv(args.csv).to_dict("records")
    spy = load_bars("SPY", args.lo, None)
    sessions = sessions_from_bars(spy)
    rows = attach(trades, sessions, spy)

    c = np.array([b["close"] for b in spy], float)
    spy_daily = float(np.mean(np.diff(c) / c[:-1]))
    print(f"{os.path.basename(args.csv)}: {len(rows)} trades with a matched SPY "
          f"window (of {len(trades)})")
    print(f"SPY mean daily return {1e4*spy_daily:.2f} bp/day — the hurdle every "
          f"dollar-day in this book has to clear.\n")

    hpr = np.array([r["hpr"] for r in rows], float)
    held = np.array([r["held"] for r in rows], float)
    exc = np.array([r["excess"] for r in rows], float)
    print(f"HEADLINE")
    bench = np.array([r["bench"] for r in rows], float)
    rate = hpr.sum() / held.sum()
    brate = bench.sum() / held.sum()
    print(f"  mean holding-period return   {100*hpr.mean():+.3f}%  over "
          f"{held.mean():.1f} sessions")
    print(f"  book rate, dollar-day wtd    {1e4*rate:+.2f} bp/day")
    print(f"  SPY over the SAME windows    {1e4*brate:+.2f} bp/day"
          f"   <- the like-for-like hurdle")
    print(f"  SPY unconditional            {1e4*spy_daily:+.2f} bp/day"
          f"   (NOT the hurdle: the book picks its days)")
    print(f"  net rate                     {1e4*(rate-brate):+.2f} bp/day")
    print(f"  mean excess vs matched SPY   {100*exc.mean():+.3f}%  per trade,"
          f" equal-weighted (day-clustered t {dayt(rows,'excess'):+.2f})")
    print(f"  trades beating SPY over their own window: "
          f"{100*(exc>0).mean():.1f}%")
    print("  The two views weight differently: the rate is dollar-day weighted,")
    print("  so long holds dominate it; the per-trade excess is equal-weighted,")
    print("  so fast stop-outs dominate. Both are shown because they can and do")
    print("  disagree, and the rate is the one that competes with buy-and-hold.")

    hdr = (f"\n  {'cut':<22}{'n':>7}{'hpr%':>9}{'held':>8}{'bp/day':>10}"
           f"{'SPYbp/d':>10}{'net':>9}{'excess%':>10}{'dayT':>8}{'win%':>8}")

    print("\n" + "=" * 78)
    print("EX-ANTE CUTS — known at the signal bar, so they can actually be traded")
    print("=" * 78)

    print(hdr.replace("cut", "by entry regime"))
    for k in sorted({str(r.get("regime", "?")) for r in rows}):
        block(k, [r for r in rows if str(r.get("regime")) == k], spy_daily)

    print(hdr.replace("cut", "by VIX regime"))
    for k in sorted({str(r.get("vix_regime", "?")) for r in rows}):
        block(k, [r for r in rows if str(r.get("vix_regime")) == k], spy_daily)

    print(hdr.replace("cut", "by setup score"))
    for k in sorted({int(r["score"]) for r in rows}):
        block(f"score {k}", [r for r in rows if int(r["score"]) == k], spy_daily)

    print(hdr.replace("cut", "by leg mask"))
    for k in sorted({str(r.get("legs", "?")) for r in rows}):
        block(f"legs {k}", [r for r in rows if str(r.get("legs")) == k], spy_daily)

    print(hdr.replace("cut", "by stop width"))
    sp = np.array([r["stop_pct"] for r in rows], float)
    qs = np.percentile(sp, [20, 40, 60, 80])
    lab = ["tightest 20%", "20-40%", "40-60%", "60-80%", "widest 20%"]
    bins = np.digitize(sp, qs)
    for i, l in enumerate(lab):
        block(l, [r for r, b in zip(rows, bins) if b == i], spy_daily)

    print(hdr.replace("cut", "by era"))
    for name, lo, hi in (("2015-2017", "2015-01-01", "2018-01-01"),
                         ("2018-2019", "2018-01-01", "2020-01-01"),
                         ("2020-2021", "2020-01-01", "2022-01-01"),
                         ("2022-2023", "2022-01-01", "2024-01-01"),
                         ("2024-2026", "2024-01-01", "2027-01-01")):
        block(name, [r for r in rows if lo <= str(r["signal_date"]) < hi],
              spy_daily)

    print("\n" + "=" * 78)
    print("EX-POST CUTS — NOT TRADEABLE. Read these as diagnosis only.")
    print("  Holding length and exit reason are OUTCOMES of the trade: a name is")
    print("  only in the '40-61 sessions' or 'TP' bucket BECAUSE it kept working.")
    print("  Selecting on them is conditioning on the future. They explain the")
    print("  shape of the loss; they are not a filter you can apply at entry.")
    print("=" * 78)

    print(hdr.replace("cut", "by holding length"))
    for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 40), (40, 61)]:
        block(f"{lo}-{hi} sessions", [r for r in rows if lo < r["held"] <= hi],
              spy_daily)

    print(hdr.replace("cut", "by exit reason"))
    for k in sorted({str(r.get("exit_reason", "?")) for r in rows}):
        block(str(k), [r for r in rows if str(r.get("exit_reason")) == k],
              spy_daily)


if __name__ == "__main__":
    main()
