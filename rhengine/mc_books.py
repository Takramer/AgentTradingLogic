"""
mc_books.py — Monte Carlo for the RESEARCH-STACK books (RMX-B, RMX-C).

`mc_refined.py` bootstraps per-trade R-multiples from the legacy engine. The
research stack has no trades and no R-multiples: RMX-B and RMX-C are
cross-sectional rank-and-hold books whose only output is a **daily
mark-to-market return series** (`rhengine/alpha/book.py`). Resampling those in
R-space is not merely awkward, it is the exact category error the 2026-07-24
audit was called for — see docs/AUDIT-2026-07-24.md FC-1 and the "R-space vs
capitalised" trap. So this module is a separate bootstrap with different units:

    mc_refined.run_mc_paths        unit = one TRADE, resampled i.i.d.,
                                   sized at `risk_pct` of equity per trade
    mc_books.run_mc_book_paths     unit = one SESSION, resampled in BLOCKS,
                                   fully invested, no sizing dial at all

Two consequences worth stating before any chart is read:

* **There is no risk-per-trade knob.** A book's weights sum to one; its
  dispersion is whatever the holdings do. The risk sweep in the refined-set
  notebook has no analogue here, and inventing one (levering the book) would
  describe an account nobody has — `book.run_book` charges a retail margin
  spread for exactly that reason.
* **Blocks, not i.i.d. days.** Daily equity returns cluster: volatility arrives
  in runs, and i.i.d. resampling destroys precisely the structure that produces
  real drawdowns (audit FC-10). This uses the stationary block bootstrap of
  Politis & Romano 1994 (https://doi.org/10.1080/01621459.1994.10476870), the
  same scheme as `alpha/validation.block_bootstrap` — the difference is that
  this one keeps the **full path matrix** so a notebook can draw fan charts,
  where `validation` returns summary statistics only.

The benchmark is resampled with the SAME block indices as the book, so
P(underperform SPY) is measured against a market path that actually co-moves
with the strategy path. Resampling them independently compares the book to a
market that never happened.

WHAT THIS CANNOT DO. It is a re-arrangement of one realised sample. It cannot
turn eight years of S&P 500 panel into significance — RMX-B's +1.78%/yr against
5.5%/yr tracking error needs ~39 years to reach t=2, and no amount of
resampling shortens that. Read the fans as the *shape* of the risk, never as
evidence of edge.

Usage:
    py -m rhengine.mc_books --lo 2010-07-01 --hi 2018-12-31
    py -m rhengine.mc_books --csv tests/results/book_returns_10bps.csv
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

DEFAULT_HORIZON = 252        # one trading year
# 1 / 3 / 5 / 10 years. A one-year horizon is the wrong unit for a book whose
# whole claim is a small edge compounding: at one year the market's own noise
# is 20-50x the signal, and a coin-flip-ish win rate is the only possible
# answer. The edge only becomes visible against noise over horizons where
# alpha*T grows faster than TE*sqrt(T) - i.e. slowly, which is the point.
HORIZONS = (252, 756, 1260, 2520)
DEFAULT_MEAN_BLOCK = 21      # ~one month; the validation.py convention
DEFAULT_PATHS = 20_000
DEFAULT_RUIN_DD = 50.0

# The two research-stack books, and the flags each one's published figures were
# produced under. RMX-B ships vol-scaling OFF (the 63-year record is the
# unscaled 50/50); RMX-C ships it ON for the momentum block (`blend.BlendConfig`
# defaults to `vol_scale=("momentum",)`). The two modules genuinely differ —
# CLAUDE.md used to claim otherwise.
# The return columns `book_daily_returns` produces, in plotting order. `rf` is
# carried alongside them but is not a book and is never bootstrapped.
BOOK_SERIES = ("RMX-B", "RMX-C", "SPY", "CW universe")

BOOK_SPECS = {
    "RMX-B": {"module": "rhengine.alpha.rmxb",
              "note": "50/50 cap-weighted quality + 12-1 momentum, "
                      "vol-scaling OFF"},
    "RMX-C": {"module": "rhengine.alpha.blend",
              "note": "5 blocks (quality/momentum/investment/issuance/payout), "
                      "momentum vol-scaled"},
}


# ------------------------------------------------------------------ harvesting
def book_daily_returns(lo: str = "2010-07-01", hi: str = "2018-12-31",
                       cost_bps: float = 10.0,
                       vol_scale_momentum: bool = False,
                       cost_model: str = "flat",
                       panel=None, fund=None,
                       verbose: bool = True) -> pd.DataFrame:
    """Daily net return series for RMX-B, RMX-C and their shared benchmarks.

    Columns: RMX-B, RMX-C, SPY, CW universe, rf — all on the intersection of
    the two books' calendars, so every column covers identical sessions and the
    paired bootstrap below is comparing like with like. `rf` (Ken French's
    daily risk-free rate) rides along so a consumer of the CSV can compute a
    Sharpe ratio without reloading the panel; dropping it would leave a
    notebook quietly reporting excess-return statistics against zero.

    `cost_bps` is ROUND TRIP (see `rhengine/costs.py`). The research stack's
    published local figures are at 10 bps; `config.json`'s measured 30 is the
    live-execution assumption and is a legitimate alternative — pass it and
    every number here moves, which is the point of stamping it on the cache.
    """
    from .alpha.blend import BlendConfig
    from .alpha.blend import build as blend_build
    from .alpha.blend import run as blend_run
    from .alpha.fundamentals import Fundamentals
    from .alpha.rmx import load_panel
    from .alpha.rmxb import RMXBConfig, build_targets
    from .alpha.rmxb import run as rmxb_run
    from .alpha.rmxq import cost_vector

    panel = panel if panel is not None else load_panel()
    fund = fund if fund is not None else Fundamentals.load()
    bcfg = RMXBConfig(vol_scale_momentum=vol_scale_momentum)
    ccfg = BlendConfig()

    # `cost_model="tier-*"` charges every name its market-cap tier's spread
    # instead of one flat number. It matters more than it sounds: these books
    # are >92% mega-cap, so the legacy universe's flat 30 bps overstates their
    # cost ~10x and was on its own enough to flip RMX-C's measured alpha
    # negative. See research rules 6a and scripts/cap_cost_profile.py.
    b_cost = c_cost = cost_bps
    if cost_model != "flat":
        mode = cost_model.split("-", 1)[1]
        bt, _, _, _ = build_targets(panel, fund, bcfg, lo, hi)
        b_cost = cost_vector(panel, fund, bt, mode=mode)
        ct = blend_build(panel, fund, ccfg, lo, hi, cost_bps)["targets"]
        c_cost = cost_vector(panel, fund, ct, mode=mode)

    tag = cost_model if cost_model != "flat" else f"{cost_bps:g} bps"
    if verbose:
        print(f"  RMX-B  {lo} .. {hi} @ {tag} ...")
    b = rmxb_run(panel, fund, bcfg, lo, hi, b_cost)
    if verbose:
        print(f"  RMX-C  {lo} .. {hi} @ {tag} ...")
    c = blend_run(panel, fund, ccfg, lo, hi, c_cost)

    out = pd.DataFrame({
        "RMX-B": b["book"].daily_return,
        "RMX-C": c["book"].daily_return,
        "SPY": b["benchmarks"]["SPY (cap-weight)"],
        "CW universe": b["benchmarks"]["CW covered universe"],
        "rf": panel.rf,
    }).dropna()
    out.attrs["cost_bps"] = float(cost_bps)
    out.attrs["cost_model"] = cost_model
    out.attrs["effective_bps"] = {"RMX-B": b["book"].effective_bps,
                                  "RMX-C": c["book"].effective_bps}
    out.attrs["series"] = BOOK_SERIES
    out.attrs["panel_fp"] = panel.fingerprint(lo, hi)
    out.attrs["window"] = [lo, hi]
    out.attrs["turnover_ann"] = {"RMX-B": b["book"].annual_turnover,
                                 "RMX-C": c["book"].annual_turnover}
    return out


# ------------------------------------------------------------------- bootstrap
def stationary_block_indices(n: int, horizon: int, paths: int,
                             mean_block: int, rng) -> np.ndarray:
    """(paths x horizon) resampling indices, geometric blocks, wrapped.

    Each step either continues the previous block (index + 1, modulo n) or
    starts a new one at a uniformly drawn position, with restart probability
    1/`mean_block`. Expected block length is therefore `mean_block` sessions
    and the resampled series is stationary — no seam at the sample's edges.
    """
    if n < 2:
        raise ValueError("need at least two sessions to bootstrap")
    p = 1.0 / float(mean_block)
    idx = np.empty((paths, horizon), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, size=paths)
    for t in range(1, horizon):
        restart = rng.random(paths) < p
        cont = (idx[:, t - 1] + 1) % n
        idx[:, t] = np.where(restart, rng.integers(0, n, size=paths), cont)
    return idx


def run_mc_book_paths(daily: pd.Series, horizon: int = DEFAULT_HORIZON,
                      paths: int = DEFAULT_PATHS,
                      mean_block: int = DEFAULT_MEAN_BLOCK,
                      seed: int = 1337, ruin_dd_pct: float = DEFAULT_RUIN_DD,
                      bench: pd.Series | None = None) -> dict:
    """Stationary block bootstrap of a book's daily returns.

    Returns the same keys as `mc_refined.run_mc_paths` wherever the concept
    survives the change of unit (`equity`, `finals`, `maxdd`, `final_pct`,
    `maxdd_pct`, `prob_loss`, `prob_ruin`) so notebook plotting code is shared,
    plus the ones that only exist here: `cagr_pct` percentiles and, when
    `bench` is supplied, `prob_underperform` against the benchmark resampled on
    the identical block indices.

    Note what `prob_loss` means in this unit: the probability that a fully
    invested equity book is DOWN over `horizon` sessions. For a long-only large
    cap book that is a market-shaped number near 20-25% at a one-year horizon,
    not a statement about the strategy's edge. The number that speaks to the
    strategy is `prob_underperform`.
    """
    r = np.asarray(daily.dropna(), dtype=float)
    rng = np.random.default_rng(seed)
    idx = stationary_block_indices(len(r), horizon, paths, mean_block, rng)

    eq = np.cumprod(1.0 + r[idx], axis=1)
    peak = np.maximum.accumulate(np.maximum(eq, 1.0), axis=1)
    maxdd = (1.0 - eq / peak).max(axis=1)
    finals = eq[:, -1]
    yrs = horizon / 252.0
    cagr = finals ** (1.0 / yrs) - 1.0

    out = {
        "equity": eq, "finals": finals, "maxdd": maxdd,
        "n_observed": len(r), "horizon": horizon, "paths": paths,
        "mean_block": mean_block,
        "avg_daily_pct": float(r.mean() * 100),
        "realised_cagr_pct": float(((1.0 + r).prod() ** (252.0 / len(r)) - 1.0)
                                   * 100),
        "final_pct": {p: float(np.percentile(finals, p))
                      for p in (5, 25, 50, 75, 95)},
        "cagr_pct": {p: float(np.percentile(cagr, p) * 100)
                     for p in (5, 25, 50, 75, 95)},
        "maxdd_pct": {p: float(np.percentile(maxdd, p) * 100)
                      for p in (50, 75, 95, 99)},
        "prob_loss": float((finals < 1.0).mean() * 100),
        "prob_ruin": float((maxdd >= ruin_dd_pct / 100).mean() * 100),
        "prob_underperform": float("nan"),
    }
    if bench is not None:
        b = np.asarray(bench.reindex(daily.dropna().index).fillna(0.0),
                       dtype=float)
        bench_finals = np.prod(1.0 + b[idx], axis=1)
        out["bench_finals"] = bench_finals
        out["bench_final_pct"] = {p: float(np.percentile(bench_finals, p))
                                  for p in (5, 25, 50, 75, 95)}
        out["prob_underperform"] = float((finals < bench_finals).mean() * 100)
    return out


# ---------------------------------------------------------------------- CLI
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Harvest RMX-B / RMX-C daily book returns and block-"
                    "bootstrap them. Writes a returns CSV the Monte Carlo "
                    "notebook consumes.")
    ap.add_argument("--lo", default="2010-07-01")
    ap.add_argument("--hi", default="2018-12-31",
                    help="default is the IN-SAMPLE window; the 2019-2026 "
                         "holdout is SPENT (CLAUDE.md rule 4) and no new claim "
                         "may rest on it")
    ap.add_argument("--cost-bps", type=float, default=10.0,
                    help="ROUND TRIP. 10 = the research stack's published "
                         "local figures; 30 = config.json's measured live cost")
    ap.add_argument("--vol-scale-momentum", action="store_true",
                    help="RMX-B only; RMX-C vol-scales momentum by default")
    ap.add_argument("--cost-model", default="flat",
                    choices=("flat", "tier-lo", "tier-point", "tier-hi"),
                    help="'flat' charges --cost-bps to every name; 'tier-*' "
                         "charges each name its cap tier's spread. These books "
                         "are >92%% mega-cap, so flat 30 overstates them ~10x")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    ap.add_argument("--horizons", type=int, nargs="*", default=None,
                    metavar="SESSIONS",
                    help=f"sweep several holding periods instead of one; "
                         f"default sweep is {HORIZONS} (1/3/5/10 years)")
    ap.add_argument("--paths", type=int, default=DEFAULT_PATHS)
    ap.add_argument("--mean-block", type=int, default=DEFAULT_MEAN_BLOCK)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--csv", default=None,
                    help="default tests/results/book_returns_{bps}bps.csv")
    args = ap.parse_args()

    # The cache is stamped with the cost MODEL, not just the bps: a tier-point
    # harvest and a flat-10 harvest are different books and must never be
    # served to each other from the same file.
    stamp = (f"{args.cost_bps:g}bps" if args.cost_model == "flat"
             else args.cost_model)
    csv = args.csv or f"tests/results/book_returns_{stamp}.csv"
    print(f"Harvesting research-stack book returns ({args.lo} .. {args.hi}) ...")
    daily = book_daily_returns(args.lo, args.hi, args.cost_bps,
                               vol_scale_momentum=args.vol_scale_momentum,
                               cost_model=args.cost_model)
    daily.to_csv(csv)
    # Sidecar rather than a CSV comment line: the notebook reads the CSV with a
    # plain `read_csv`, and a provenance header that every consumer has to know
    # to skip is a provenance header that gets skipped.
    meta = {"panel_fp": daily.attrs["panel_fp"],
            "window": [args.lo, args.hi],
            "cost_model": args.cost_model,
            "cost_bps": float(args.cost_bps),
            "effective_bps": daily.attrs["effective_bps"],
            "vol_scale_momentum": bool(args.vol_scale_momentum),
            "sessions": int(len(daily)),
            "turnover_ann": daily.attrs["turnover_ann"],
            "harvested_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    meta_path = os.path.splitext(csv)[0] + ".meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {len(daily)} sessions x {daily.shape[1]} series to {csv}")
    print(f"  panel {meta['panel_fp']}  ->  {meta_path}\n")

    horizons = args.horizons if args.horizons is not None else [args.horizon]
    print(f"{args.paths:,} paths, mean block {args.mean_block}d, "
          f"sample {len(daily)} sessions\n")
    for h in horizons:
        yrs = h / 252.0
        print(f"--- horizon {h} sessions ({yrs:.0f}y) "
              f"{'-' * 46}")
        print("{:<14} {:>9} {:>9} {:>9} {:>9} {:>9} {:>10}".format(
            "book", "p5 x", "p50 x", "p95 x", "DD p50", "DD p95", "beats SPY"))
        for name in BOOK_SERIES:
            m = run_mc_book_paths(daily[name], horizon=h,
                                  paths=args.paths, mean_block=args.mean_block,
                                  seed=args.seed,
                                  bench=None if name == "SPY" else daily["SPY"])
            beat = ("-" if np.isnan(m["prob_underperform"])
                    else f"{100 - m['prob_underperform']:.1f}%")
            print("{:<14} {:>9.2f} {:>9.2f} {:>9.2f} {:>8.1f}% {:>8.1f}% "
                  "{:>10}".format(
                      name, m["final_pct"][5], m["final_pct"][50],
                      m["final_pct"][95], m["maxdd_pct"][50],
                      m["maxdd_pct"][95], beat))
        if h > len(daily) // 2:
            print(f"  NOTE: {h} sessions is over half the {len(daily)}-session "
                  f"sample, so paths overlap heavily\n  and this row's SPREAD "
                  f"is understated. The central tendency is fine.")
        print()
    print("Blocks preserve loss clustering; they do NOT create evidence. "
          "One realised sample, re-arranged.")


if __name__ == "__main__":
    main()
