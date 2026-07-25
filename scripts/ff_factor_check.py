"""ff_factor_check.py — is any equity factor still worth implementing?

Before spending effort building a point-in-time fundamentals pipeline (audit
O-4), ask the cheap question first: over the longest record that exists, do the
published factor premia survive, and are they still alive in the last two
decades? Ken French's own factor series answer that in one download, with no
survivorship bias, no cost assumption of mine, and no chance of me having fit
anything.

The question that matters for this project is narrower than "is HML real". It
is: **would a long-only tilt toward this factor have beaten the market**, which
is what a retail account can actually hold. So the test is run on the long-only
decile portfolios, not just the long-short factor.

    py scripts/ff_factor_check.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "research_data")


def read_french(name: str, block: int = 0) -> pd.DataFrame:
    """Parse block `block` of a French CSV.

    These files stack several tables under one another - value-weighted
    returns, equal-weighted returns, number of firms, average size - separated
    by blank lines and each preceded by its own column header. Reading them as
    one table silently mixes 'number of firms' (values around 400) into a
    return series, which compounds to nonsense. Blocks are therefore split
    explicitly and only the requested one is returned; block 0 is the
    value-weighted monthly returns.
    """
    lines = open(os.path.join(DATA, name), encoding="latin-1").read().splitlines()
    blocks, cur, header, headers = [], [], None, []
    for ln in lines:
        parts = [p.strip() for p in ln.split(",")]
        is_data = len(parts) >= 2 and len(parts[0]) == 6 and parts[0].isdigit()
        if is_data:
            try:
                cur.append([parts[0]] + [float(x) for x in parts[1:]])
            except ValueError:
                pass
        else:
            if cur:
                blocks.append((header, cur))
                headers.append(header)
                cur = []
            if len(parts) > 2 and parts[0] == "":
                header = parts[1:]
    if cur:
        blocks.append((header, cur))
    if not blocks:
        raise ValueError(f"no numeric block found in {name}")
    if block >= len(blocks):
        raise ValueError(f"{name} has {len(blocks)} blocks, asked for {block}")

    head, rows = blocks[block]
    width = max(len(r) for r in rows)
    rows = [r for r in rows if len(r) == width]
    df = pd.DataFrame(rows)
    df[0] = pd.to_datetime(df[0], format="%Y%m") + pd.offsets.MonthEnd(0)
    df = df.set_index(0).sort_index()
    df = df[~df.index.duplicated(keep="first")] / 100.0
    if head and len(head) >= df.shape[1]:
        df.columns = head[:df.shape[1]]
    # French marks missing data with -99.99 / -999, which become -0.9999 here
    return df.mask(df <= -0.99)


def ann(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12.0 / len(r)) - 1.0) * 100


def stats(r: pd.Series, rf: pd.Series) -> tuple[float, float, float]:
    ex = (r - rf).dropna()
    sh = float(ex.mean() / ex.std() * np.sqrt(12)) if ex.std() > 0 else np.nan
    t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex))))
    return ann(r), sh, t


def main() -> None:
    ff5 = read_french("ff5_monthly.csv")
    ff5.columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"][:ff5.shape[1]]
    rf = ff5["RF"]
    mkt = ff5["Mkt-RF"] + rf

    print("=" * 74)
    print("Are the published factors still paying? (Ken French monthly series)")
    print("=" * 74)
    print("\nLong-SHORT factor premia, annualised, by era")
    print("{:<8} {:>16} {:>16} {:>16}".format(
        "factor", "1963-2000", "2001-2015", "2016-2026"))
    eras = [("1963-07", "2000-12"), ("2001-01", "2015-12"), ("2016-01", "2026-12")]
    for f in ("Mkt-RF", "SMB", "HML", "RMW", "CMA"):
        cells = []
        for a, b in eras:
            s = ff5[f].loc[a:b].dropna()
            cells.append(f"{ann(s):+6.2f}% t={s.mean() / (s.std(ddof=1) / np.sqrt(len(s))):+5.2f}"
                         if len(s) > 24 else "      n/a")
        print("{:<8} {:>16} {:>16} {:>16}".format(f, *cells))

    # ---- the question a long-only account actually faces --------------------
    for fname, label in (("port_op.csv", "operating profitability"),
                         ("port_be.csv", "book-to-market")):
        try:
            p = read_french(fname, block=0)
        except Exception as e:
            print(f"\n{label}: could not parse ({e})")
            continue
        top = next((c for c in p.columns if str(c) in ("Hi 30", "Hi 20", "Hi 10")), None)
        if top is None:
            print(f"\n{label}: unexpected columns {list(p.columns)[:8]}")
            continue

        print(f"\nLONG-ONLY top portfolio on {label} ({top}) vs the market")
        print("{:<12} {:>10} {:>10} {:>9} {:>9} {:>10}".format(
            "era", "top CAGR", "mkt CAGR", "top Shp", "mkt Shp", "excess t"))
        for a, b in [("1963-07", "2000-12"), ("2001-01", "2015-12"),
                     ("2016-01", "2026-12"), ("1963-07", "2026-12")]:
            t_r = p[top].loc[a:b].dropna()
            m_r = mkt.reindex(t_r.index).dropna()
            t_r = t_r.reindex(m_r.index)
            if len(t_r) < 24:
                continue
            d = (t_r - m_r)
            tc, ts, _ = stats(t_r, rf.reindex(t_r.index))
            mc, ms, _ = stats(m_r, rf.reindex(m_r.index))
            tt = float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))
            print("{:<12} {:>+9.2f}% {:>+9.2f}% {:>9.2f} {:>9.2f} {:>+10.2f}".format(
                f"{a[:4]}-{b[:4]}", tc, mc, ts, ms, tt))

    print("\nRead: a long-only tilt only helps if 'top CAGR' beats 'mkt CAGR' with")
    print("an excess t that is not noise, in the RECENT era as well as the full one.")


if __name__ == "__main__":
    main()
