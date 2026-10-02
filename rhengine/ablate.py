"""
ablate.py — leg-ablation testing: which legs carry the edge, which are drag?

(docs/notebooklm-findings.md Part 4.1.) Every strategy scores 4 named legs.
This tool re-evaluates every subset of the confirmation legs (the primary
trigger leg — leg 1 — is always required) over a SINGLE backtest's trade list,
ranking configurations by profit factor with the same IS/OOS split optimize.py
uses. The sources' expected finding: complex filters often hurt, and the
stripped-down version frequently has the highest PF. A leg whose removal
RAISES out-of-sample PF is a deletion candidate.

Method: the backtest runs once at the engine's emission floor (score >= 2) and
records each trade's leg pass-mask; a mask subset then "qualifies" a trade iff
every active leg passed. No re-scoring, so 8 leg subsets cost one backtest.

Caveat: trades the engine never emitted can't be resurrected here, so the
all-confirmations-off row is floored at whatever gate produced the population.
What that gate is depends on the strategy's legMask (config.json thresholds):

  legMask = 0  emission needs >= 2 genuinely passing legs, so the primary-only
               row is an approximation — it can only see trades that had a
               second leg passing too.
  legMask = m  every leg in m must genuinely pass and the rest are bypassed
               into the score, so the population is exactly "the masked legs
               passed" and subsets over it are properly nested. Leg state is
               still recorded truthfully (indicators.py writes raw_pass), so
               the subsets discriminate either way.

Degeneracy guard: evaluate_masks() raises AblationDegenerateError when every
subset returns identical statistics. That is never a finding — it means the
leg bitstrings carry no information — and it silently produced a chart of 8
identical bars for the whole of 2026-07-18..25 (see indicators.py legMask).

Usage:
    py -m rhengine.ablate --strategy bollinger
    py -m rhengine.ablate --strategy residualmomentum --lookback-days 730 --cost-bps 5
"""

import argparse
import itertools
import json
from collections import Counter

from .costs import resolve_cost_bps

from .backtest import run_backtest_on, _stats, _iso_date, WARMUP_BARS
from .indicators import load_spec


class AblationDegenerateError(RuntimeError):
    """Every leg subset scored identically — the ablation measured nothing."""


def leg_state_counts(trades):
    """Distribution of recorded leg bitstrings, most common first."""
    return Counter(t["legs"] for t in trades)


def _signature(st):
    """The part of a stats dict a caller would actually report."""
    if not st:
        return None
    return (st["n"], round(st["avg_r"], 9), round(st["win_rate"], 9))


def evaluate_masks(trades, n_legs, split_date, strict=True):
    """All confirmation-leg subsets over one trade list.

    Returns [(active_idx_tuple, is_stats, oos_stats)], every mask including
    leg 0. Raises AblationDegenerateError if the subsets cannot be told apart
    (`strict=False` to get the rows back anyway, e.g. to inspect them).
    """
    confirm = list(range(1, n_legs))
    rows = []
    for r in range(len(confirm) + 1):
        for subset in itertools.combinations(confirm, r):
            active = (0,) + subset
            qual = [t for t in trades
                    if all(i < len(t["legs"]) and t["legs"][i] == "1"
                           for i in active)]
            is_tr = [t for t in qual if t["signal_date"] < split_date]
            oos_tr = [t for t in qual if t["signal_date"] >= split_date]
            rows.append((active, _stats(is_tr), _stats(oos_tr)))

    if strict and trades and len(rows) > 1:
        sigs = {(_signature(s), _signature(o)) for _, s, o in rows}
        if len(sigs) == 1:
            states = leg_state_counts(trades)
            raise AblationDegenerateError(
                "all {} leg subsets returned identical statistics over {:,} "
                "trades — this is a blinded run, not a null result.\n"
                "  recorded leg states: {}\n"
                "  A single state means every trade was written with the same "
                "pass-mask, so no subset can exclude anything. Usual cause: a "
                "legMask that rewrites leg pass-state before backtest.py "
                "serialises it (indicators.py). Check that legs carry "
                "'raw_pass' and that backtest.py reads it."
                .format(len(rows), len(trades),
                        ", ".join("{}={:,}".format(k, v)
                                  for k, v in states.most_common(6))))
    return rows


def _pf(st):
    if not st:
        return 0.0
    return 1e9 if st["profit_factor"] == float("inf") else st["profit_factor"]


def _fmt(st):
    if not st:
        return "{:>5} {:>6} {:>7} {:>6} {:>6}".format(0, "-", "-", "-", "-")
    pf = "inf" if st["profit_factor"] == float("inf") else "{:.2f}".format(st["profit_factor"])
    return "{:>5} {:>5.1f}% {:>+7.3f} {:>6} {:>+6.1f}".format(
        st["n"], st["win_rate"] * 100, st["avg_r"], pf, st["t_stat"])


def main():
    ap = argparse.ArgumentParser(description="Leg-ablation testing (PF-ranked).")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--cost-bps", type=float, default=None)
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--regime", default=None,
                    help="restrict to signals fired in this SPY regime (e.g. "
                         "choppy) — ablate a strategy only where routing uses it")
    ap.add_argument("--min-trades", type=int, default=300,
                    help="flag configs under this many total trades (sources: "
                         "~300 = minimum statistical confidence)")
    args = ap.parse_args()
    # Single source of truth for the cost assumption (costs.py / config.json
    # "costs.round_trip_bps"). Audit FC-2: these defaults used to be 0.0 or 5.0
    # per-CLI and the headline artifact was generated at 0 bps.
    args.cost_bps = resolve_cost_bps(args.cost_bps,
                                     getattr(args, "config", None))

    with open(args.config) as f:
        cfg = json.load(f)
    spec = load_spec()["strategies"]
    if args.strategy not in spec:
        raise SystemExit("Unknown strategy {!r}".format(args.strategy))
    leg_names = [l["abbr"] for l in spec[args.strategy]["legs"]]

    from .data_local import load_bars_any
    bars_map, spy_bars = load_bars_any(cfg, lookback_days=args.lookback_days)
    dates = [_iso_date(b["time"]) for b in spy_bars[WARMUP_BARS:]]
    split_date = dates[int(len(dates) * args.split)]

    trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=2,
                             forced_strategy=args.strategy,
                             cost_bps=args.cost_bps,
                             regime_only=args.regime)
    mask = int((cfg.get("strategy", {}).get("thresholds", {})
                .get(args.strategy, {}) or {}).get("legMask", 0))
    print("\nAblation — {} | legs: {} | {} emitted trades | split {} ({}) | "
          "{} bps | legMask={}{}"
          .format(args.strategy, ", ".join(leg_names), len(trades),
                  args.split, split_date, args.cost_bps, mask,
                  " | regime={}".format(args.regime) if args.regime else ""))
    if mask:
        print("  legMask {} in effect: {} must genuinely pass, {} bypassed into "
              "the score.\n  Leg state is still recorded truthfully, so the "
              "subsets below are real."
              .format(mask,
                      "+".join(n for i, n in enumerate(leg_names)
                               if (mask >> i) & 1) or "(none)",
                      "+".join(n for i, n in enumerate(leg_names)
                               if not (mask >> i) & 1) or "(none)"))
    if trades:
        states = leg_state_counts(trades)
        print("  leg states: {}{}".format(
            "  ".join("{} {:.1f}%".format(k, v / len(trades) * 100)
                      for k, v in states.most_common(6)),
            "  (+{} more)".format(len(states) - 6) if len(states) > 6 else ""))

    rows = evaluate_masks(trades, len(leg_names), split_date)
    rows.sort(key=lambda r: _pf(r[2]), reverse=True)  # rank by OOS PF

    hdr = "{:>5} {:>6} {:>7} {:>6} {:>6}".format("n", "win%", "avgR", "PF", "t")
    print("\n  {:<28}  IS: {}   OOS: {}".format("active legs (ranked by OOS PF)",
                                                hdr, hdr))
    full_mask = tuple(range(len(leg_names)))
    for active, is_st, oos_st in rows:
        label = "+".join(leg_names[i] for i in active)
        total = (is_st["n"] if is_st else 0) + (oos_st["n"] if oos_st else 0)
        flag = "  <300n" if total < args.min_trades else ""
        mark = " *" if active == full_mask else "  "
        print("  {:<28}{}  IS: {}   OOS: {}{}".format(
            label[:28], mark, _fmt(is_st), _fmt(oos_st), flag))
    print("\n  * = full leg set (production). A subset above the full set with a"
          "\n  healthy sample means the missing legs are drag, not edge.")


if __name__ == "__main__":
    main()
