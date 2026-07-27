"""Measure the REAL round-trip cost of this universe from live Robinhood quotes.

Why this exists: cost is the single number that decides whether any sleeve in
this repo works (breakeven is ~16 bps per side against a 30 bps assumption), and
the project has never measured it. Two prior routes both failed:

  * The published broker fee schedule is exact but nearly zero — 0.396 bps round
    trip trade-weighted (costs.robinhood_explicit_bps). Robinhood charges no
    commission; you pay the SPREAD, which is not on any fee schedule.
  * costs.estimate_round_trip_bps (Corwin-Schultz from daily high/low) was
    validated 2026-07-25 and rejected — ~37x too high, SPY at 39 bps against a
    true sub-1 bp spread. See its docstring.

So the spread has to come from live bid/ask during REGULAR trading hours.
After-hours books are near-empty and quote roughly 10x wide (2026-07-25 close:
SOUN 6.06/22.18, LSTR 189.00/322.25) — sampling then is worse than not sampling.

TWO STEPS.

  1. build   (offline, any time)
       py scripts/rh_spreads.py build
     Stratifies the 3,161 traded tickers into ADV deciles — dollar volume is
     what drives spread — and draws a sample from each, recording the stratum's
     true share of the 120,406-trade book so the estimate can be reweighted back
     to what was actually traded. Writes tests/results/rh_spread_sample.json.

  2. report  (needs quotes captured 10:00-15:30 ET on a trading day)
       py scripts/rh_spreads.py report quotes.json
     quotes.json is [{"symbol","bid_price","ask_price"}, ...] as returned by the
     Robinhood get_equity_quotes tool. Produces the trade-weighted round-trip
     cost and what it does to each sleeve's net edge.

Sampling mid-session matters: the first ~30 minutes and last ~10 quote wide for
reasons that have nothing to do with what this strategy would pay, since it
enters on the next session's open... which is itself the widest moment of the
day. That is a known gap between this measurement and the fill model, and it
biases the answer OPTIMISTIC. Noted rather than hidden.
"""
import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rhengine.costs import robinhood_explicit_bps  # noqa: E402
from rhengine.data_local import load_bars_any      # noqa: E402

RESULTS = ROOT / "tests" / "results"
SAMPLE = RESULTS / "rh_spread_sample.json"
TRADES = RESULTS / "refined_trades_30bps.csv"
DECILES = 10
PER_DECILE = 30
SEED = 1337


def build(args):
    cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))
    bars_map, _ = load_bars_any(cfg, lookback_days=730, verbose=False)
    trades = pd.read_csv(TRADES)
    counts = trades.ticker.value_counts()

    rows = []
    for tk, n in counts.items():
        bars = bars_map.get(tk)
        if not bars or len(bars) < 60:
            continue
        tail = bars[-60:]
        adv = float(np.median([b["close"] * b["volume"] for b in tail]))
        price = float(np.median([b["close"] for b in tail]))
        if adv <= 0 or price <= 0:
            continue
        rows.append({"ticker": tk, "trades": int(n), "adv": adv, "price": price})
    df = pd.DataFrame(rows)
    df["decile"] = pd.qcut(df.adv, DECILES, labels=False, duplicates="drop")

    rng = random.Random(SEED)
    out = {"_note": "stratified by 60d median dollar volume; weight = stratum "
                    "share of real trades, so the sample reweights to the book",
           "total_trades": int(df.trades.sum()), "strata": []}
    for d, g in df.groupby("decile"):
        picks = g.ticker.tolist()
        if len(picks) > PER_DECILE:
            picks = rng.sample(picks, PER_DECILE)
        out["strata"].append({
            "decile": int(d),
            "adv_lo": float(g.adv.min()), "adv_hi": float(g.adv.max()),
            "adv_median": float(g.adv.median()),
            "price_median": float(g.price.median()),
            "tickers_in_stratum": int(len(g)),
            "trades_in_stratum": int(g.trades.sum()),
            "weight": float(g.trades.sum() / df.trades.sum()),
            "sample": sorted(picks),
        })
    SAMPLE.write_text(json.dumps(out, indent=1), encoding="utf-8")

    n_sym = sum(len(s["sample"]) for s in out["strata"])
    print(f"wrote {SAMPLE.relative_to(ROOT)}  —  {n_sym} symbols across "
          f"{len(out['strata'])} ADV deciles, covering {out['total_trades']:,} trades\n")
    print(f"{'dec':>3} {'ADV median':>14} {'px med':>8} {'names':>6} "
          f"{'trades':>8} {'weight':>7} {'sampled':>8}")
    for s in out["strata"]:
        print(f"{s['decile']:>3} {s['adv_median']:>14,.0f} {s['price_median']:>8.2f} "
              f"{s['tickers_in_stratum']:>6} {s['trades_in_stratum']:>8,} "
              f"{s['weight']:>7.3f} {len(s['sample']):>8}")
    print("\nNext: capture quotes for these symbols 10:00-15:30 ET on a trading "
          "day, save as JSON, then:\n  py scripts/rh_spreads.py report quotes.json")


def report(args):
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    quotes = json.loads(Path(args.quotes).read_text(encoding="utf-8"))
    if isinstance(quotes, dict):
        quotes = quotes.get("results") or quotes.get("quotes") or []
    q = {}
    for r in quotes:
        r = r.get("quote", r)
        try:
            bid, ask = float(r["bid_price"]), float(r["ask_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid <= 0 or ask <= 0 or ask < bid:
            continue          # empty book — drop rather than invent a spread
        mid = (bid + ask) / 2.0
        q[r["symbol"].upper()] = {"bid": bid, "ask": ask, "mid": mid,
                                  "spread_bps": (ask - bid) / mid * 10000.0}

    strata, missing = [], 0
    for s in spec["strata"]:
        got = [q[t] for t in s["sample"] if t in q]
        missing += len(s["sample"]) - len(got)
        if not got:
            continue
        spreads = sorted(x["spread_bps"] for x in got)
        med = float(np.median(spreads))
        explicit = float(np.mean([robinhood_explicit_bps(x["mid"]) for x in got]))
        strata.append({**{k: s[k] for k in
                          ("decile", "adv_median", "price_median", "weight")},
                       "n": len(got), "spread_bps": med,
                       "explicit_bps": explicit, "total_bps": med + explicit})
    if not strata:
        raise SystemExit("no usable quotes — was the market open?")

    df = pd.DataFrame(strata)
    df["weight"] = df.weight / df.weight.sum()      # renormalise over covered strata
    tw = float(np.average(df.total_bps, weights=df.weight))
    sp = float(np.average(df.spread_bps, weights=df.weight))
    ex = float(np.average(df.explicit_bps, weights=df.weight))

    print(f"{len(q)} usable quotes, {missing} missing/empty-book\n")
    print(f"{'dec':>3} {'ADV median':>14} {'n':>4} {'spread':>9} {'fees':>7} "
          f"{'total':>8} {'weight':>7}")
    for r in df.itertuples():
        print(f"{r.decile:>3} {r.adv_median:>14,.0f} {r.n:>4} {r.spread_bps:>8.1f} "
              f"{r.explicit_bps:>7.2f} {r.total_bps:>8.1f} {r.weight:>7.3f}")
    print("=" * 62)
    print(f"TRADE-WEIGHTED ROUND TRIP        spread {sp:>7.1f} bps")
    print(f"                                   fees {ex:>7.2f} bps")
    print(f"                                  TOTAL {tw:>7.1f} bps")
    print(f"\n  config.json assumes {json.load(open(ROOT/'config.json'))['costs']['round_trip_bps']} bps"
          f" (engine doubles it — see backtest._cost_r)")

    # what it does to the sleeves, using the measured cost grid
    grid = [0.0, 5.0, 11.0, 20.0, 30.0]
    try:
        f = {c: pd.read_csv(RESULTS / f"refined_trades_{c:g}bps.csv") for c in grid}
    except FileNotFoundError:
        return
    print("\n  extrapolated net mean R per sleeve at the measured cost:")
    print("  (linear in bps; cost enters R linearly so this is exact, not fitted)")
    for s in list(f[0.0].strategy.unique()) + ["POOLED"]:
        ys = [(f[c].r_multiple.mean() if s == "POOLED"
               else f[c].loc[f[c].strategy == s, "r_multiple"].mean()) for c in grid]
        slope, icept = np.polyfit(grid, ys, 1)
        # measured total is a ROUND-TRIP number; the grid is in per-side units
        net = icept + slope * (tw / 2.0)
        print(f"    {s:18} {net:>+8.4f} R   ({'still positive' if net > 0 else 'LOSS'})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="build the stratified sampling frame (offline)")
    r = sub.add_parser("report", help="analyse captured live quotes")
    r.add_argument("quotes", help="JSON from get_equity_quotes")
    a = ap.parse_args()
    (build if a.cmd == "build" else report)(a)
