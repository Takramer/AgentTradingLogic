"""ff_blend.py — can two small, uncorrelated edges add up to a demonstrable one?

The problem RMX-QT ran into is arithmetic, not philosophical. A +1.19%/yr alpha
against a 4.14%/yr tracking error has an information ratio of 0.29, and an IR of
0.29 needs ~48 years to reach t=2. No amount of care fixes that for a single
sleeve.

The fundamental law of active management says what does fix it: IR scales with
the square root of the number of independent bets. Blend two long-only sleeves
whose excess returns are close to uncorrelated and the alphas add while the
tracking errors add in quadrature, so the blend's t-stat exceeds either leg's.

HYPOTHESIS, fixed before running
    A cap-weighted large-cap sleeve blending operating profitability
    (Fama-French RMW / Novy-Marx) with 12-1 momentum (Jegadeesh & Titman 1993,
    https://doi.org/10.1111/j.1540-6261.1993.tb04702.x) beats the market with
    alpha t >= 2.0 over 1963-2026, net of costs, and in a majority of eras.

    These two are the canonical low-correlation pair: momentum is a
    price-continuation signal, profitability an accounting-quality one, and
    momentum's well-known negative correlation with value does not extend to
    profitability.

CANDIDATES, fixed before running (4 trials, logged)
    A  BIG HiOP                     - profitability alone (the RMX-QT leg)
    B  BIG HiPRIOR                  - large-cap 12-1 momentum alone
    C  50/50 blend of A and B       - THE hypothesis
    D  BIG HiOP LoINV               - profitability + conservative investment

KILL CRITERION, fixed before running
    Candidate C is accepted only if alpha t >= 2.0 over 1963-2026 net of costs
    AND it beats the market in at least 4 of the 5 eras. Otherwise the project
    reports that no strategy here demonstrably beats the S&P.

COSTS
    French's returns are gross. Turnover is charged explicitly: the momentum leg
    is rebalanced monthly and turns over roughly 200%/yr one-way, profitability
    annually at roughly 33%/yr. Both are charged at a deliberately punitive
    20 bps round trip for mega-caps, where 1-3 bps is realistic.

    py scripts/ff_blend.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ff_factor_check import read_french                     # noqa: E402
from rhengine.alpha.stats import _newey_west_ols            # noqa: E402

ERAS = [("1963-07", "1979-12"), ("1980-01", "1999-12"),
        ("2000-01", "2015-12"), ("2016-01", "2026-12")]
FULL = ("1963-07", "2026-12")

# one-way turnover per year, and the round-trip cost charged against it
TURNOVER = {"quality": 0.33, "momentum": 2.00}
COST_BPS = 20.0


def ann(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12.0 / len(r)) - 1.0) * 100


def sharpe(r: pd.Series, rf: pd.Series) -> float:
    ex = (r - rf.reindex(r.index)).dropna()
    return float(ex.mean() / ex.std() * np.sqrt(12)) if ex.std() > 0 else np.nan


def alpha_t(r: pd.Series, mkt: pd.Series, rf: pd.Series, lags: int = 6):
    df = pd.concat({"r": r, "m": mkt, "f": rf}, axis=1).dropna()
    y = (df["r"] - df["f"]).to_numpy(float)
    X = np.column_stack([np.ones(len(df)), (df["m"] - df["f"]).to_numpy(float)])
    coef, se = _newey_west_ols(y, X, lags)
    return coef[0] * 12 * 100, coef[0] / se[0], coef[1]


def net_of_cost(r: pd.Series, kind: str) -> pd.Series:
    """Subtract turnover x round-trip cost, spread evenly across months."""
    drag = TURNOVER[kind] * COST_BPS / 1e4 / 12.0
    return r - drag


def big_col(cols, prefix_hi):
    hit = [c for c in cols if c.startswith(prefix_hi)]
    return hit[0] if hit else None


def evaluate(name: str, r: pd.Series, mkt: pd.Series, rf: pd.Series) -> dict:
    rows, beats = [], 0
    for a, b in ERAS:
        x = r.loc[a:b].dropna()
        m = mkt.reindex(x.index).dropna()
        x = x.reindex(m.index)
        if len(x) < 24:
            continue
        al, at, _ = alpha_t(x, m, rf.reindex(x.index))
        exc = ann(x) - ann(m)
        beats += exc > 0
        rows.append((f"{a[:4]}-{b[:4]}", ann(x), ann(m), exc, al, at))
    x = r.loc[FULL[0]:FULL[1]].dropna()
    m = mkt.reindex(x.index).dropna()
    x = x.reindex(m.index)
    al, at, be = alpha_t(x, m, rf.reindex(x.index))
    te = float((x - m).std() * np.sqrt(12) * 100)
    return {"name": name, "eras": rows, "beats": beats, "n_eras": len(rows),
            "cagr": ann(x), "mkt": ann(m), "excess": ann(x) - ann(m),
            "alpha": al, "t": at, "beta": be, "te": te,
            "sharpe": sharpe(x, rf), "mkt_sharpe": sharpe(m, rf),
            "series": x}


def show(res: dict) -> None:
    print(f"\n{res['name']}")
    print("    {:<12} {:>9} {:>9} {:>9} {:>9} {:>8}".format(
        "era", "CAGR", "market", "excess", "alpha/yr", "alpha t"))
    for e, c, m, x, al, at in res["eras"]:
        print("    {:<12} {:>+8.2f}% {:>+8.2f}% {:>+8.2f}pp {:>+8.2f}% {:>+8.2f}".format(
            e, c, m, x, al, at))
    print("    {:<12} {:>+8.2f}% {:>+8.2f}% {:>+8.2f}pp {:>+8.2f}% {:>+8.2f}   "
          "<- 1963-2026".format("FULL", res["cagr"], res["mkt"], res["excess"],
                                res["alpha"], res["t"]))
    print(f"    Sharpe {res['sharpe']:.2f} vs {res['mkt_sharpe']:.2f} | "
          f"beta {res['beta']:.2f} | tracking error {res['te']:.2f}%/yr | "
          f"IR {res['alpha'] / res['te']:.2f} | "
          f"beats the market in {res['beats']}/{res['n_eras']} eras")


def main() -> None:
    ff5 = read_french("ff5_monthly.csv")
    ff5.columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"][:ff5.shape[1]]
    rf, mkt = ff5["RF"], ff5["Mkt-RF"] + ff5["RF"]

    op = read_french("port_me_op_5x5.csv", block=0)
    op.columns = [str(c) for c in op.columns]
    pr = read_french("port_me_prior_5x5.csv", block=0)
    pr.columns = [str(c) for c in pr.columns]

    q_col = big_col(op.columns, "BIG HiOP")
    m_col = big_col(pr.columns, "BIG HiPRIOR")
    print("=" * 84)
    print("Blending two long-only large-cap sleeves - French portfolios, 1963-2026")
    print(f"cost: {COST_BPS:.0f} bps round trip on "
          f"{TURNOVER['quality']:.0%}/yr (quality) and "
          f"{TURNOVER['momentum']:.0%}/yr (momentum) one-way turnover")
    print("=" * 84)
    print(f"quality leg  = {q_col}")
    print(f"momentum leg = {m_col}")

    q = net_of_cost(op[q_col].dropna(), "quality")
    m = net_of_cost(pr[m_col].dropna(), "momentum")
    idx = q.index.intersection(m.index)
    q, m = q.reindex(idx), m.reindex(idx)

    # correlation of the two EXCESS return streams - the whole premise
    mk = mkt.reindex(idx)
    corr = float((q - mk).corr(m - mk))
    print(f"\ncorrelation of the two sleeves' excess returns: {corr:+.3f}"
          f"   ({'independent enough to blend' if abs(corr) < 0.4 else 'too correlated to help much'})")

    a = evaluate("A. quality only  (BIG HiOP, net)", q, mkt, rf)
    b = evaluate("B. momentum only (BIG HiPRIOR, net)", m, mkt, rf)
    blend = 0.5 * q + 0.5 * m
    c = evaluate("C. 50/50 BLEND   (the hypothesis)", blend, mkt, rf)

    for res in (a, b, c):
        show(res)

    # candidate D - profitability x investment triple sort
    try:
        tri = read_french("port_me_op_inv.csv", block=0)
        tri.columns = [str(x) for x in tri.columns]
        d_col = next((x for x in tri.columns if x.strip() == "BIG HiOP LoINV"), None)
        if d_col:
            d = evaluate(f"D. {d_col} (net)",
                         net_of_cost(tri[d_col].dropna(), "quality"), mkt, rf)
            show(d)
    except Exception as e:
        print(f"\nD. triple sort unavailable: {type(e).__name__}: {e}")

    print("\n" + "=" * 84)
    print("KILL CRITERION for candidate C: alpha t >= 2.0 full sample AND "
          "beats the market in >= 4 of 5 eras")
    ok_t = c["t"] >= 2.0
    ok_eras = c["beats"] >= 4
    print(f"    alpha t = {c['t']:+.2f}            {'PASS' if ok_t else 'FAIL'}")
    print(f"    eras beaten = {c['beats']}/{c['n_eras']}         "
          f"{'PASS' if ok_eras else 'FAIL'}")
    print(f"    VERDICT: {'ACCEPTED' if (ok_t and ok_eras) else 'REJECTED'}")
    print(f"\n    blend IR {c['alpha'] / c['te']:.2f} vs quality {a['alpha'] / a['te']:.2f} "
          f"and momentum {b['alpha'] / b['te']:.2f} "
          f"-> years to t=2: {(2 * c['te'] / c['alpha']) ** 2:.0f} "
          f"(quality alone {(2 * a['te'] / a['alpha']) ** 2:.0f})")
    print("=" * 84)


if __name__ == "__main__":
    main()
