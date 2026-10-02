"""ff_size_ladder.py — does the RMX signal family pay more further down the
cap ladder, and does the extra gross survive the extra cost?

Written 2026-08-01, after "go down the cap ladder" was proposed as the only
remaining lever with a 2-3x prior and immediately hit the obvious objection:
small- and mid-cap price history since 2005 is neither free nor easy to get,
and a universe built from *today's* mid-caps is survivorship-poisoned in the
one size bucket where survivorship bias is largest.

That objection kills building the BOOK. It does not kill measuring the CLAIM,
because Ken French already publishes exactly the right thing and this repo
already has it on disk: 5x5 portfolios on size x signal, value-weighted,
monthly, survivorship-free, CRSP-based, nothing here fitted to them. Same
evidence base RMX-B's 63-year record comes from — see scripts/ff_largecap_blend.py,
which asks this question for the BIG bucket only. This is that script walked
down the whole ladder.

QUESTION, fixed before running
    Inside each size quintile, does holding the favourable extreme of each
    signal (high OP, high prior return, low investment) beat a cap-weighted
    portfolio of that SAME size bucket? Does the advantage grow as size falls?
    And at what round-trip cost does the advantage of the smaller buckets over
    BIG disappear?

THE CONTROL THAT MATTERS
    The benchmark is the size bucket's own cap-weighted return, not the total
    market. Comparing a small-cap book to the market measures the SIZE PREMIUM
    plus the signal, and the size premium is not an edge you can claim - it is
    a risk factor anyone can buy for free, and it has been negative for long
    stretches. ff_largecap_blend.py's `size_control` found exactly this trap
    on the modern era, where controlling for SMB collapsed the alpha. So every
    number here is signal-only, within-bucket, by construction.

    Bucket weights come from French's own firm-count and average-size blocks
    (n_firms x avg_size), lagged one month so the weight applied in month t
    uses last month's sizes and is knowable.

KILL CRITERION, fixed before running
    If the within-bucket blend excess does not rise as size falls, OR the
    smallest tradeable bucket's advantage over BIG is smaller than the extra
    round-trip cost that bucket plausibly carries, then the cap ladder is not
    worth the data problem, the answer is to stay in large caps, and this file
    records the rejection.

WHAT THIS CANNOT DO
    It cannot tell you your fill cost in a mid-cap name, so it does not claim
    a net number - it prints a cost SWEEP and the breakeven. It also cannot
    speak to capacity: ME1 is ~1,000 firms averaging tens of millions of
    dollars. Nobody trades that book. Read ME3/ME4, not ME1.

    py scripts/ff_size_ladder.py
    py scripts/ff_size_ladder.py --turnover 176
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ff_factor_check import read_french                     # noqa: E402
from rhengine.alpha.stats import _newey_west_ols            # noqa: E402

VW, EW = 0, 1
N_FIRMS, AVG_SIZE = 2, 3        # identified by magnitude; see _check_blocks

# file -> (signal token, is the FAVOURABLE extreme the high or the low bucket)
# Signs are the published ones, not chosen here: profitability and prior return
# are high-is-good, investment is low-is-good.
LEGS = {
    "quality":    ("port_me_op_5x5.csv", "OP", "Hi"),
    "momentum":   ("port_me_prior_5x5.csv", "PRIOR", "Hi"),
    "investment": ("port_me_inv_5x5.csv", "INV", "Lo"),
}

# French names the extreme columns "SMALL LoOP" / "BIG HiOP" and the interior
# ones "ME3 OP2", so the column for a bucket is not formulaic at the edges.
SIZE_LABEL = {1: "SMALL", 5: "BIG"}
ERAS = [("1963-07", "2026-12"), ("1963-07", "2004-12"),
        ("2005-01", "2026-12"), ("2015-01", "2026-12")]

# Turnover of each leg, one-way %/yr, taken from what this repo actually
# measures on its own book rather than assumed: RMX-C reports 176%/yr overall,
# and it is momentum that drives it (annual sorts barely trade).
DEFAULT_TURNOVER = 176.0


def _col(size: int, token: str, rank: int) -> str:
    """The French column name for (size quintile, signal quintile).

    Only the CORNERS get words. French writes "SMALL LoOP" and "BIG HiOP" for
    the four corners of the 5x5 grid and "ME3 OP2" everywhere else — including
    "ME2 OP5", which is *not* "ME2 HiOP". Getting this wrong raises rather than
    silently mis-reading a column, which is why the caller checks membership.
    """
    if size in SIZE_LABEL and rank in (1, 5):
        return f"{SIZE_LABEL[size]} {'Lo' if rank == 1 else 'Hi'}{token}"
    return f"ME{size} {token}{rank}"


def ann(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12.0 / len(r)) - 1.0) * 100


def alpha_t(r: pd.Series, bench: pd.Series, rf: pd.Series, lags: int = 6):
    """Alpha of `r` against `bench`, Newey-West. Returns (%/yr, t)."""
    y = (r - rf).to_numpy(float)
    x = np.column_stack([np.ones(len(y)), (bench - rf).to_numpy(float)])
    coef, se = _newey_west_ols(y, x, lags)
    return float(coef[0]) * 1200.0, float(coef[0] / se[0])


def bucket_frames(fname: str, token: str, extreme: str):
    """Per size quintile: the favourable-extreme return, and that bucket's own
    cap-weighted return across all five signal quintiles."""
    ret = read_french(fname, VW)
    nfirm = read_french(fname, N_FIRMS)
    size = read_french(fname, AVG_SIZE)
    legs, benches = {}, {}
    for s in range(1, 6):
        cols = [_col(s, token, k) for k in range(1, 6)]
        missing = [c for c in cols if c not in ret.columns]
        if missing:
            raise KeyError(f"{fname}: missing {missing}\nhave {list(ret.columns)}")
        # cap = firms x average size, lagged one month: the weight applied in
        # month t must be knowable at the end of t-1.
        cap = (nfirm[cols] * size[cols]).shift(1)
        w = cap.div(cap.sum(axis=1), axis=0)
        benches[s] = (ret[cols] * w).sum(axis=1, min_count=5)
        legs[s] = ret[_col(s, token, 5 if extreme == "Hi" else 1)]
    return pd.DataFrame(legs), pd.DataFrame(benches)


def build() -> dict:
    ff = read_french("ff5_monthly.csv", 0)
    rf = ff["RF"]
    out = {}
    for name, (fname, token, extreme) in LEGS.items():
        out[name] = bucket_frames(fname, token, extreme)
    # the blend: equal weight of the three legs inside each size bucket, on the
    # calendar all three share (OP and INV start 1963-07, PRIOR starts 1927)
    idx = None
    for legs, _ in out.values():
        idx = legs.dropna(how="all").index if idx is None \
            else idx.intersection(legs.dropna(how="all").index)
    blend_legs = sum(out[n][0].reindex(idx) for n in LEGS) / len(LEGS)
    blend_bench = sum(out[n][1].reindex(idx) for n in LEGS) / len(LEGS)
    out["BLEND"] = (blend_legs, blend_bench)
    return {"legs": out, "rf": rf}


def report(data: dict) -> pd.DataFrame:
    rf = data["rf"]
    rows = []
    for name, (legs, benches) in data["legs"].items():
        print(f"\n{'=' * 84}")
        print(f"=== {name}: favourable extreme vs its OWN size bucket "
              f"(cap-weighted, signal-only)")
        print(f"{'=' * 84}")
        print(f"{'era':18} {'size':7} {'leg':>7} {'bucket':>8} {'excess':>8} "
              f"{'alpha':>8} {'t':>6}")
        for lo, hi in ERAS:
            for s in range(1, 6):
                r = legs[s][lo:hi].dropna()
                b = benches[s].reindex(r.index).dropna()
                r = r.reindex(b.index)
                if len(r) < 24:
                    continue
                a, t = alpha_t(r, b, rf.reindex(r.index))
                lbl = {1: "ME1 tiny", 2: "ME2", 3: "ME3 mid", 4: "ME4",
                       5: "ME5 BIG"}[s]
                rows.append({"leg": name, "era": f"{lo}..{hi}", "size": lbl,
                             "n": len(r), "ret": ann(r), "bucket": ann(b),
                             "excess": ann(r) - ann(b), "alpha": a, "t": t})
                print(f"{lo + '..' + hi[:4]:18} {lbl:7} {ann(r):7.2f} "
                      f"{ann(b):8.2f} {ann(r) - ann(b):+8.2f} {a:+8.2f} "
                      f"{t:+6.2f}")
            print()
    return pd.DataFrame(rows)


def cost_sweep(df: pd.DataFrame, turnover: float, era: str) -> None:
    """What round-trip cost erases the smaller buckets' advantage over BIG?

    The comparison that decides the question is not "does ME3 beat its bucket"
    - it is "does ME3's advantage over ME5's advantage survive the extra cost
    of trading ME3". BIG is what this project already trades at a measured
    ~30 bps, so BIG is the incumbent and everything else has to pay its way in.
    """
    e = df[(df.leg == "BLEND") & (df.era == era)].set_index("size")
    if e.empty:
        return
    big = float(e.loc["ME5 BIG", "excess"])
    print(f"\n{'=' * 84}")
    print(f"=== BLEND, {era}: cost sweep against BIG as the incumbent")
    print(f"{'=' * 84}")
    print(f"one-way turnover assumed {turnover:.0f}%/yr on every bucket "
          f"(RMX-C's measured figure)")
    print(f"BIG excess over its own bucket: {big:+.2f}%/yr at 30 bps that is "
          f"{big - turnover / 100 * 30 / 100:+.2f}%/yr net\n")
    bps = [30, 50, 75, 100, 150, 200]
    print(f"{'size':9} {'gross':>7} {'vs BIG':>8} " +
          " ".join(f"{b:>6}bp" for b in bps))
    for s in ("ME1 tiny", "ME2", "ME3 mid", "ME4", "ME5 BIG"):
        if s not in e.index:
            continue
        g = float(e.loc[s, "excess"])
        # net advantage over the incumbent: this bucket's gross edge minus its
        # cost, less BIG's gross edge minus BIG's measured 30 bps
        big_net = big - turnover / 100 * 30 / 100
        cells = " ".join(f"{g - turnover / 100 * b / 100 - big_net:+8.2f}"
                         for b in bps)
        print(f"{s:9} {g:+7.2f} {g - big:+8.2f} {cells}")
    print("\nCells are %/yr ADVANTAGE OVER BIG net of that bucket's cost, with")
    print("BIG held at its measured 30 bps. Negative = not worth leaving large")
    print("caps at that cost. The repo's own quote captures put the least")
    print("liquid decile it trades at 98.1 bps, so read the 100 bp column as")
    print("the pessimistic case and 50-75 bp as the plausible mid-cap one.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turnover", type=float, default=DEFAULT_TURNOVER,
                    help="one-way %%/yr assumed for the blend (default: "
                         "RMX-C's measured 176)")
    ap.add_argument("--era", default="2005-01..2026-12",
                    help="era for the cost sweep")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    data = build()
    df = report(data)
    cost_sweep(df, args.turnover, args.era)
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nwrote {args.csv}")


if __name__ == "__main__":
    main()
