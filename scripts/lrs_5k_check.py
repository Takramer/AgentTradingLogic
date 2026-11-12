"""lrs_5k_check.py — LRS-VT and LRS-2x from a $5,000 start: backtest, out of
sample, walk-forward, bootstrap, rolling windows, drawdowns, cost stress.

Pre-registered in the ledger (family index-leverage, 2026-11-12) before it
first ran. The rules are frozen; nothing here re-tunes them. The forward paper
record is scripts/lrs_paper.py.

    .venv/bin/python scripts/lrs_5k_check.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lrs_check as L                                   # noqa: E402
import lrs_variants_check as V                          # noqa: E402
import lrs_vt_extra_check as X                          # noqa: E402

START = 5_000.0
GRID_VT, GRID_BAND = (0.20, 0.25, 0.30, 0.35, 0.40), (0.10, 0.25, 0.40)


def lrs_vt(df, vt=V.VOL_TARGET, band=V.BAND) -> pd.Series:
    idx = (1 + df.mkt).cumprod()
    on = (idx > idx.rolling(L.SMA).mean()).astype(float)
    vol = df.mkt.rolling(V.VOL_WIN).std() * np.sqrt(252)
    old, V.BAND = V.BAND, band                          # banded() reads the module value
    try:
        e = V.banded(on * (vt / vol).clip(upper=2.0))
    finally:
        V.BAND = old
    return V.exposure_returns(e.shift(V.LAG), df)


def book(df) -> pd.DataFrame:
    b = V.build(df)
    dt = df.index.to_series().diff().dt.days.fillna(1) / 365.25
    return pd.DataFrame({"spy": (df.mkt - L.ER[1] * dt).reindex(b.index), "lrs2x": b.lrs2x,
                         "lrs_vt": b.lrs_vt, "market": b.market, "rf": b.rf})


def underwater_yrs(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    peak = eq.cummax()
    longest, start = 0.0, None
    for d, under in (eq < peak).items():
        if under and start is None:
            start = d
        elif not under and start is not None:
            longest, start = max(longest, (d - start).days / 365.25), None
    if start is not None:
        longest = max(longest, (r.index[-1] - start).days / 365.25)
    return longest


def dollars(s: pd.DataFrame, cols, title: str) -> None:
    print(f"\n{title}  ({s.index[0].date()} .. {s.index[-1].date()}, {L.years(s.index):.1f} yr)")
    print(f"  {'':8}{'$5k ->':>14}{'CAGR':>8}{'maxDD':>8}{'worst yr':>9}{'underwater':>11}"
          f"{'Sharpe':>8}{'alpha t':>8}")
    for c in cols:
        p = L.perf(s[c], s.rf, s.market)
        yr = (1 + s[c]).groupby(s.index.year).prod() - 1
        end = START * (1 + s[c]).prod()
        print(f"  {c:8}{end:>14,.0f}{p['cagr']:>8.2%}{p['maxdd']:>8.1%}{yr.min():>9.1%}"
              f"{underwater_yrs(s[c]):>9.1f}yr{p['sharpe']:>8.2f}"
              f"{'-' if c == 'spy' else format(p['alpha_t'], '.2f'):>8}")


def sharpe(r, rf):
    ex = (r - rf).dropna()
    return ex.mean() / ex.std() * np.sqrt(len(ex) / L.years(ex.index))


def walk_forward(df, s: pd.DataFrame) -> None:
    cells = {(v, b): lrs_vt(df, v, b) for v in GRID_VT for b in GRID_BAND}
    lo = s.loc["1950":]
    beat = sum(sharpe(r.loc["1950":], lo.rf) > sharpe(lo.market, lo.rf) for r in cells.values())
    print(f"\nPLATEAU 1950-2026: {beat} of {len(cells)} cells beat the market's Sharpe "
          f"({sharpe(lo.market, lo.rf):.2f}) -> {'ok' if beat >= 12 else 'WARNING'}")
    print("  Sharpe by cell (rows vol target, cols band):")
    for v in GRID_VT:
        print(f"   {v:.2f} " + "".join(f"{sharpe(cells[(v, b)].loc['1950':], lo.rf):7.2f}"
                                       for b in GRID_BAND))
    chain, picks = [], []
    for y in range(1950, 2026, 5):
        tr = slice(f"{y - 20}", f"{y - 1}")
        best = max(cells, key=lambda k: sharpe(cells[k].loc[tr], s.rf.loc[tr]))
        picks.append(f"{y}:{best[0]:.2f}/{best[1]:.2f}")
        chain.append(cells[best].loc[f"{y}":f"{min(y + 4, 2026)}"])
    wf = pd.concat(chain)
    fixed = s.lrs_vt.loc[wf.index]
    a, b = sharpe(wf, s.rf.loc[wf.index]), sharpe(fixed, s.rf.loc[wf.index])
    yrs = L.years(wf.index)
    print(f"\nWALK-FORWARD (re-pick every 5 yr on the trailing 20): picks {' '.join(picks)}")
    print(f"  out-of-sample chain Sharpe {a:.2f}, CAGR {(1 + wf).prod() ** (1 / yrs) - 1:.2%}; "
          f"fixed 0.30/0.25 Sharpe {b:.2f}, CAGR {(1 + fixed).prod() ** (1 / yrs) - 1:.2%}"
          f" -> {'ok' if a >= 0.9 * b else 'WARNING: the fixed parameters look fitted'}")


def bootstrap(s: pd.DataFrame, n=5000, block=63, seed=7) -> None:
    x = s.loc["1953":, ["spy", "lrs2x", "lrs_vt"]].to_numpy()
    T, rng = len(x), np.random.default_rng(seed)
    print(f"\nBOOTSTRAP from $5,000 (stationary blocks, mean {block} days, {n:,} paths, "
          f"1953-2026 days, the three books resampled on the same dates)")
    print(f"  {'':16}{'5th':>9}{'median':>9}{'95th':>9}{'P(<$5k)':>9}{'P(>SPY)':>9}"
          f"{'med DD':>8}{'P(DD<-40%)':>11}")
    for years in (1, 5, 10):
        h = 252 * years
        i = rng.integers(0, T, n)
        eq = np.ones((n, 3))
        peak, dd = np.ones((n, 3)), np.zeros((n, 3))
        for _ in range(h):
            eq *= 1 + x[i]
            np.maximum(peak, eq, out=peak)
            np.minimum(dd, eq / peak - 1, out=dd)
            jump = rng.random(n) < 1 / block
            i = np.where(jump, rng.integers(0, T, n), (i + 1) % T)
        for j, k in enumerate(("spy", "lrs2x", "lrs_vt")):
            v = START * eq[:, j]
            q = np.percentile(v, [5, 50, 95])
            beat = "" if k == "spy" else f"{(eq[:, j] > eq[:, 0]).mean():>9.0%}"
            print(f"  {years:>2}y {k:<11}{q[0]:>9,.0f}{q[1]:>9,.0f}{q[2]:>9,.0f}"
                  f"{(v < START).mean():>9.0%}{beat or '':>9}{np.median(dd[:, j]):>8.0%}"
                  f"{(dd[:, j] < -0.40).mean():>11.0%}")


def main() -> None:
    df = L.load()
    s = book(df)
    dollars(s, ["spy", "lrs2x", "lrs_vt"], "A. $5,000 at the next close (lag 2), French daily index")
    dollars(s.loc["1950":], ["spy", "lrs2x", "lrs_vt"], "   same, from 1950")
    dollars(s.loc["2000":], ["spy", "lrs2x", "lrs_vt"], "   same, from 2000")

    with contextlib.redirect_stdout(io.StringIO()):
        live = X.part_a()                                # the 15:15 rule RobinhoodAgent runs
    live = live.rename(columns={"lrs_vt_1515": "lrs_vt", "lrs2x_1515": "lrs2x"})
    live["spy"] = live.market - L.ER[1] / 252
    dollars(live, ["spy", "lrs2x", "lrs_vt"], "B. $5,000 at 15:15 ET on SPY's live price (the live rule)")

    post = s.loc["2016":"2026-09"]
    p = L.perf(post.lrs2x, post.rf, post.market)
    print(f"\nC. OUT OF SAMPLE for LRS-2x (Gayed & Bilello, published 2016): 2016-01..2026-09 "
          f"alpha {p['alpha']:+.2%}/yr, t {p['alpha_t']:+.2f}, CAGR {p['cagr']:.2%} vs market "
          f"{L.perf(post.market, post.rf, post.market)['cagr']:.2%}, maxDD {p['maxdd']:.1%}"
          f" -> {'ok' if p['alpha'] > 0 else 'WARNING'}")

    walk_forward(df, s)

    lo = s.loc["1950":]
    print("\nROLLING WINDOWS 1950-2026 (monthly steps): share where LRS-VT ends ahead of SPY")
    print("  " + "  ".join(f"{y}y {1 - L.rolling_trail(lo.assign(market=lo.spy), 'lrs_vt', y):.0%}"
                           for y in (1, 3, 5, 10)))

    print("\nWORST DRAWDOWNS 1928-2026 (depth, peak, trough, recovered)")
    for k in ("spy", "lrs_vt"):
        print(f"  {k}: " + "; ".join(f"{d:.0%} {a}->{b}->{c or 'not yet'}"
                                     for d, a, b, c in V.drawdowns(s[k], 3)))

    print("\nCOST STRESS, LRS-VT 1950-2026 CAGR (base: 10 bps a switch, financing rf+0.5%)")
    base_sw, base_sp = L.SWITCH_BPS, L.SPREAD
    for sw, sp in ((10, 0.005), (30, 0.005), (50, 0.005), (10, 0.015), (50, 0.015)):
        L.SWITCH_BPS, L.SPREAD = sw, sp
        r = lrs_vt(df).loc["1950":]
        print(f"  {sw:>2} bps, financing rf+{sp:.1%}: {(1 + r).prod() ** (1 / L.years(r.index)) - 1:.2%}")
    L.SWITCH_BPS, L.SPREAD = base_sw, base_sp

    m = (1 + lo[["lrs_vt", "spy"]]).resample("ME").prod() - 1
    act = m.lrs_vt - m.spy
    te, al = act.std() * np.sqrt(12), act.mean() * 12
    pv = L.perf(lo.lrs_vt, lo.rf, lo.market)
    print(f"\nYEARS TO PROVE: LRS-VT beats SPY by {al:+.2%}/yr with {te:.1%} tracking error; "
          f"a live record needs ~{(2 * te / al) ** 2:.0f} years to show t = 2. "
          f"Alpha t 1950-2026 {pv['alpha_t']:.2f} against the family bar "
          f"(index-leverage, 22 trials) of 3.04.")

    bootstrap(s)


if __name__ == "__main__":
    main()
