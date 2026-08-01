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

FOUR SUBCOMMANDS. `build` and `report` are the original measurement; `gate` and
`compare` were added 2026-07-30 alongside the Module 7 spread gate.

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

  3. gate     (offline, runs on captures already on disk)
       py scripts/rh_spreads.py gate
     What the Module 7 spread gate would actually reject, per ADV decile and
     trade-weighted. Answers whether universe_filters.min_dollar_volume already
     implies a tight enough book. It does not — see rhengine/execution.py.

  4. compare  (needs BOTH an open capture and a mid-session capture)
       py scripts/rh_spreads.py compare open.json mid.json
     The open-vs-mid multiplier, paired on the same symbols. This is the one
     number that decides whether the 30 bps assumption is conservative.

Sampling mid-session matters: the first ~30 minutes and last ~10 quote wide for
reasons that have nothing to do with what this strategy would pay, since it
enters on the next session's open... which is itself the widest moment of the
day. That is a known gap between this measurement and the fill model, and it
biases the answer OPTIMISTIC. Noted rather than hidden.

TO CLOSE THAT GAP you have to capture at the open, and it is a manual step:
09:30-09:35 ET on a trading day, same symbol list, save as
tests/results/rh_quotes_open_<date>.json, then run `compare` against a
mid-session file. Nothing offline can substitute for it — the rejected
Corwin-Schultz estimator (costs.estimate_round_trip_bps) was the attempt.
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


def _load_quotes(path):
    """Parse a get_equity_quotes capture into {SYMBOL: {bid, ask, mid, spread_bps}}.

    spread_bps is `(ask-bid)/mid * 10000` and that is a ROUND-TRIP number, not
    a per-side one: a market buy pays a half-spread above mid and the exit
    sale pays a half-spread below it, so in-and-out is exactly one quoted
    spread. Directly comparable to config.json costs.round_trip_bps. See
    rhengine/execution.py for the same arithmetic and the guard test.
    """
    quotes = json.loads(Path(path).read_text(encoding="utf-8"))
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
    return q


def gate(args):
    """Does universe_filters.min_dollar_volume already imply a sub-1% spread?

    It does not, and this is the measurement that says so. The Module 7 spread
    gate (docs/AGENT-SPEC.md, adopted 2026-07-30 as one of only two survivors
    of the evidence audit) rejects a name when the quoted spread exceeds a
    fraction of mid. Whether that gate is decoration or a real filter depends
    entirely on where this universe's ADV floor sits relative to the threshold,
    which is answerable from captures already on disk.

    Pass one or more quote files; each is reported separately, because a single
    day's tape does not pin the answer (the two mid-session captures already on
    disk differ by ~15% at the same clock time).
    """
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    thresh_bps = args.threshold_pct * 10000.0

    for path in args.quotes:
        q = _load_quotes(path)
        print(f"\n=== {Path(path).name} — {len(q)} usable quotes, "
              f"gate at {args.threshold_pct:.2%} ({thresh_bps:,.0f} bps round trip) ===")
        print(f"{'dec':>3} {'ADV lo':>13} {'ADV median':>13} {'n':>4} "
              f"{'median':>9} {'p90':>9} {'worst':>9} {'rejected':>10} {'wt':>6}")
        rej_w = kept_w = 0.0
        for s in spec["strata"]:
            v = np.array([q[t]["spread_bps"] for t in s["sample"] if t in q])
            if not len(v):
                continue
            n_rej = int((v > thresh_bps).sum())
            frac = n_rej / len(v)
            rej_w += s["weight"] * frac
            kept_w += s["weight"]
            print(f"{s['decile']:>3} {s['adv_lo']:>13,.0f} {s['adv_median']:>13,.0f} "
                  f"{len(v):>4} {np.median(v):>8.1f}b {np.percentile(v, 90):>8.1f}b "
                  f"{v.max():>8.1f}b {n_rej:>4}/{len(v):<5} {s['weight']:>6.3f}")
        print("-" * 88)
        print(f"  Trade-weighted share of the book this gate would REJECT: "
              f"{rej_w / kept_w:.1%}")
        print(f"  (weights are each stratum's share of the real "
              f"{spec['total_trades']:,}-trade book)")

    print("\n  Reading it: the gate is NOT decoration. It bites hardest exactly "
          "where\n  min_dollar_volume lets names in, and it is inert above "
          "roughly $6M/day.\n  Raising min_dollar_volume and lowering "
          "spread_gate_pct are the same lever\n  pulled from two ends — but only "
          "the gate sees the actual quote, which is\n  why a volume floor cannot "
          "substitute for it.")


def compare(args):
    """Open-session vs mid-session spread, per ADV decile.

    THE GAP THIS CLOSES. Every cost number in this repo is measured mid-session
    while the engine fills at the OPEN, which is the widest moment of the day.
    costs.py flags that as one of three reasons the 29.1 bps figure is an
    OPTIMISTIC bound, and docs/AGENT-SPEC-VERDICT.md §3 names the opening
    spread as still unmeasured. This subcommand is the arithmetic once both
    captures exist; capturing them is manual and has to happen on a trading day
    (see the module docstring).

    Why it is worth the trouble: docs/RMX-C.md shows the RMX-C turnover band is
    a wash at 30 bps and turns favourable somewhere above ~40. If the open runs
    materially wider than mid, that decision flips — which makes this
    measurement worth more than either turnover control.
    """
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    qo, qm = _load_quotes(args.open_quotes), _load_quotes(args.mid_quotes)
    print(f"open capture: {len(qo)} usable | mid capture: {len(qm)} usable\n")
    print(f"{'dec':>3} {'ADV median':>13} {'n':>4} {'open':>9} {'mid':>9} "
          f"{'ratio':>7}")
    rows = []
    for s in spec["strata"]:
        both = [t for t in s["sample"] if t in qo and t in qm]
        if not both:
            continue
        # Paired on the SAME names: a decile median over different survivor
        # sets would confound the time-of-day effect with composition.
        o = float(np.median([qo[t]["spread_bps"] for t in both]))
        m = float(np.median([qm[t]["spread_bps"] for t in both]))
        rows.append({"weight": s["weight"], "open": o, "mid": m})
        print(f"{s['decile']:>3} {s['adv_median']:>13,.0f} {len(both):>4} "
              f"{o:>8.1f}b {m:>8.1f}b {(o / m if m else float('nan')):>7.2f}x")
    if not rows:
        raise SystemExit("no symbol appears in both captures — nothing to pair")
    df = pd.DataFrame(rows)
    w = df.weight / df.weight.sum()
    to, tm = float(np.average(df.open, weights=w)), float(np.average(df.mid, weights=w))
    print("=" * 56)
    print(f"TRADE-WEIGHTED ROUND TRIP   open {to:>7.1f} bps | mid {tm:>7.1f} bps"
          f"  ->  {to / tm:.2f}x")
    cfg_bps = json.load(open(ROOT / "config.json"))["costs"]["round_trip_bps"]
    print(f"\n  config.json assumes {cfg_bps} bps. The engine fills at the OPEN, "
          f"so {to:.1f} is\n  the number it should be judged against"
          f" — {'CONSERVATIVE' if cfg_bps >= to else 'OPTIMISTIC'} as configured.")
    if to > 40.0:
        print("  Above 40 bps: re-run the RMX-C turnover sweep. docs/RMX-C.md "
              "shows the\n  --band 0.40 wash tips in the band's favour around "
              "there.\n    py -m rhengine.alpha.blend --turnover-sweep "
              f"--cost-bps {to:.0f}")
    print("\n  Still not the fill cost: this is a QUOTED spread with no market "
          "impact,\n  on one day, and PFOF price improvement cuts the other way. "
          "It is a bound,\n  not a measurement of what you would actually pay.")


def report(args):
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    q = _load_quotes(args.quotes)

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
    cfg_bps = json.load(open(ROOT / "config.json"))["costs"]["round_trip_bps"]
    print(f"\n  config.json assumes {cfg_bps} bps round trip "
          f"({'conservative' if cfg_bps >= tw else 'OPTIMISTIC'} vs this measurement)")
    print("  Prior readings: 29.1 bps (2026-07-27 13:45 ET), 24.6 (2026-07-28 13:46 ET)\n"
          "  — both mid-session. A ~15% day-to-day swing at the same clock time means\n"
          "  no single capture pins this to better than a few bps. The OPENING spread,\n"
          "  which is what the engine actually fills at, is still unmeasured.")

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

    g = sub.add_parser("gate", help="what the Module 7 spread gate would reject "
                                    "(offline; runs on captures already on disk)")
    g.add_argument("quotes", nargs="*", help="one or more quote JSONs "
                                             "(default: every capture in tests/results)")
    g.add_argument("--threshold-pct", type=float, default=None,
                   help="gate as a fraction of mid (default: config.json "
                        "execution.spread_gate_pct)")

    c = sub.add_parser("compare", help="open-session vs mid-session spread")
    c.add_argument("open_quotes", help="quotes captured 09:30-09:35 ET")
    c.add_argument("mid_quotes", help="quotes captured 10:00-15:30 ET")

    a = ap.parse_args()
    if a.cmd == "gate":
        if not a.quotes:
            a.quotes = sorted(str(p) for p in RESULTS.glob("rh_quotes_*.json"))
            if not a.quotes:
                raise SystemExit("no rh_quotes_*.json in tests/results — capture "
                                 "some first (see the module docstring)")
        if a.threshold_pct is None:
            sys.path.insert(0, str(ROOT))
            from rhengine.execution import resolve_spread_gate_pct
            a.threshold_pct = resolve_spread_gate_pct(
                json.load(open(ROOT / "config.json", encoding="utf-8")))
    {"build": build, "report": report, "gate": gate, "compare": compare}[a.cmd](a)
