"""
buywrite.py — does selling premium beat owning the index? The published answer.

WHAT THIS SETTLES, AND WHY IT CAN BE SETTLED AT ALL

docs/OPTIONS-SOURCES-2026-08-01.md triaged seven sources. Three of them — all
the videos — teach the WHEEL: sell a cash-secured put, take assignment, write a
covered call, repeat. Every other strategy in that corpus is blocked behind
months of forward option capture. The wheel is not, because CBOE has published
both of its halves as mechanical indices for decades:

    BXM  long the S&P 500, short a 1-month ATM call, rolled monthly
         => the COVERED-CALL half
    PUT  fully collateralised short 1-month ATM put
         => the CASH-SECURED-PUT half

Rules-based, survivorship-free, no selection, no forward wait. This is the class
of evidence CLAUDE.md rule 4 asks for, and it is available today.

THE BENCHMARK IS THE ANALYSIS. BXM and PUT are TOTAL-RETURN indices — the
covered-call writer owns the S&P and receives its dividends; the put writer
earns interest on collateral. Benchmarking either against the S&P PRICE index
(^GSPC) credits them with a dividend stream they already hold, worth ~1.8%/yr.
Both benchmarks are reported below, side by side, so the size of that error is
visible rather than argued about. Any premium-selling claim that does not name
its benchmark should be assumed to have used the price index.

WHAT THIS MODULE DOES NOT DO. It does not backtest a wheel, choose strikes,
choose a tenor, or produce a trading decision. BXM and PUT are monthly ATM and
mechanical; a discretionary wheel at a chosen delta on a chosen name is a
different object, and nothing here bounds it. What these indices bound is the
STRUCTURAL return of writing S&P premium, which is the part every wheel shares.

Usage:
    py -m rhengine.alpha.buywrite                  # full report
    py -m rhengine.alpha.buywrite --decades        # era table only
    py -m rhengine.alpha.buywrite --since 2016-01-01
"""

from __future__ import annotations

import argparse
import os
import zipfile

import numpy as np
import pandas as pd

from rhengine.alpha.stats import compare, perf, print_report

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CBOE = os.path.join(ROOT, "research_data", "external", "cboe")
FRENCH = os.path.join(ROOT, "research_data", "external", "french",
                      "F-F_Research_Data_5_Factors_2x3.zip")


def load_series(name: str) -> pd.Series:
    path = os.path.join(CBOE, f"{name}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} missing — run `py scripts/fetch_cboe_benchmarks.py` first")
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date")["close"].sort_index()


def daily_returns(px: pd.Series) -> pd.Series:
    return px.pct_change().dropna()


def load_rf_daily(index: pd.DatetimeIndex) -> pd.Series:
    """Daily risk-free rate from Ken French's monthly 1-month T-bill.

    Sharpe is the one statistic here that moves with rf, and over a window
    containing both 5% and 0% short rates a zero-rf Sharpe is not comparable
    across eras. PUT in particular earns the bill rate on its collateral by
    construction, so setting rf = 0 would credit the put-writer with the
    entire T-bill return as though it were premium.

    French publishes monthly; the rate is spread evenly across that month's
    trading days. After the file ends the last observation is held flat — which
    is stated rather than silently extrapolated.
    """
    if not os.path.exists(FRENCH):
        return pd.Series(0.0, index=index)
    with zipfile.ZipFile(FRENCH) as z:
        raw = z.read(z.namelist()[0]).decode("latin-1").splitlines()
    rows = []
    for line in raw:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 7 and parts[0].isdigit() and len(parts[0]) == 6:
            rows.append((parts[0], float(parts[6])))
    if not rows:
        return pd.Series(0.0, index=index)
    m = pd.Series({pd.Period(k, freq="M"): v / 100.0 for k, v in rows})
    per = index.to_period("M")
    monthly_rate = pd.Series(per.map(m).astype(float), index=index)
    monthly_rate = monthly_rate.ffill().fillna(0.0)
    # days per month in THIS index, so a short month is not over-charged
    n = pd.Series(1, index=index).groupby(per).transform("size")
    return monthly_rate / n


def _cagr(r: pd.Series) -> float:
    """CAGR of a daily return series, NaN when the series is not live.

    The dropna is load-bearing. `reindex` onto a wider calendar fills the dead
    period with NaN, and pandas' `prod` SKIPS NaN — so a series with no data at
    all returns a product of 1.0, which compounds to a perfectly plausible
    0.00%/yr instead of an obvious blank. PUT does not exist before 1996-08 and
    printed 0.00 for the 1980s until this was fixed.
    """
    r = r.dropna()
    if len(r) < 60:
        return float("nan")
    return float((1 + r).prod() ** (252.0 / len(r)) - 1.0) * 100.0


def decade_table(series: dict[str, pd.Series], bench_name: str = "SP500TR"):
    """CAGR per calendar decade, and the premium-seller's share of the index.

    The 'share of the index' column is the one the repo has been quoting from
    memory as '~1/3 over the last decade'. It is computed here rather than
    recalled, and it is extremely sensitive to the window — which is the point.
    """
    idx = series[bench_name].index
    out = []
    for lo in range(1980, 2030, 10):
        hi = lo + 10
        mask = (idx.year >= lo) & (idx.year < hi)
        if mask.sum() < 250:
            continue
        row = {"era": f"{lo}s"}
        full = int(mask.sum())
        for k, s in series.items():
            cell = s.reindex(idx)[mask].dropna()
            row[k] = _cagr(cell)
            # A decade a series only half-lived through is not a decade. Flagged
            # rather than dropped, because the partial cells are still readable.
            row[k + "_partial"] = len(cell) < 0.9 * full
        out.append(row)
    return out


def _share(x, b):
    """Ratio of TOTAL GROWTH, not of CAGR. 'One third of the S&P' is a claim
    about wealth, and CAGRs do not divide that way."""
    if not np.isfinite(x) or not np.isfinite(b) or b <= -100:
        return float("nan")
    return (x / 100.0) / (b / 100.0) if b != 0 else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decades", action="store_true")
    ap.add_argument("--since", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    px = {k: load_series(k) for k in ("BXM", "PUT", "SP500TR", "GSPC")}
    rets = {k: daily_returns(v) for k, v in px.items()}
    if args.since:
        rets = {k: v[v.index >= args.since] for k, v in rets.items()}

    print("CBOE PREMIUM-SELLING INDICES vs THE INDEX ITSELF")
    print("source: research_data/external/cboe (Yahoo ^BXM ^PUT ^SP500TR ^GSPC)")
    for k, v in rets.items():
        print(f"  {k:<9} {len(v):>6} days  "
              f"{v.index[0].date()} .. {v.index[-1].date()}")
    print("\nBXM/PUT are TOTAL RETURN. SP500TR is the correct benchmark;")
    print("GSPC is the price index, shown only to size the dividend error.\n")

    if args.decades:
        _print_decades(rets)
        return

    for leg, why in (("BXM", "covered-call half of the wheel"),
                     ("PUT", "cash-secured-put half of the wheel")):
        # Every series restricted to the leg's own live window, so the
        # comparison is over identical days and not over different histories.
        r = rets[leg]
        common = r.index
        for b in ("SP500TR", "GSPC"):
            common = common.intersection(rets[b].index)
        r = r.reindex(common)
        bench = {b: rets[b].reindex(common) for b in ("SP500TR", "GSPC")}
        rf = load_rf_daily(common)

        # THE TEST THAT ACTUALLY DECIDES IT. Both indices run at beta ~0.6 to
        # the S&P, so the honest question is not "does premium-selling beat the
        # index" — it obviously will not, it holds less of it — but "does it
        # beat the same beta bought with no options at all". A static
        # beta*SP500TR + (1-beta)*cash sleeve needs no assignment, no
        # collateral, no chain, no execution and no $74.7k. If the writer
        # cannot beat THAT, the premium is not paying for the machinery.
        beta = float(np.polyfit(bench["SP500TR"].to_numpy(float),
                                r.to_numpy(float), 1)[0])
        bench[f"{beta:.2f}xTR+cash"] = (beta * bench["SP500TR"]
                                        + (1.0 - beta) * rf)

        print("=" * 78)
        print(f"{leg} — {why}   ({common[0].date()} .. {common[-1].date()})")
        print("=" * 78)
        rep = compare(r, bench, rf=rf, trials=1, label=leg)
        print_report(rep)
        tot = rep["perf"][leg]["total_return_pct"]
        for b in ("SP500TR", "GSPC"):
            s = _share(tot, rep["perf"][b]["total_return_pct"])
            print(f"  growth of $1 vs {b:<9}: {1 + tot / 100:>8.2f}x  vs "
                  f"{1 + rep['perf'][b]['total_return_pct'] / 100:>8.2f}x   "
                  f"= {s:.2f} of the index")
        print(f"\n  THE ONE THAT MATTERS: alpha vs the beta-matched cash blend")
        bm = next(k for k in rep["vs"] if k.endswith("xTR+cash"))
        d = rep["vs"][bm]
        print(f"    {leg} vs {bm}: {d['alpha_ann'] * 100:+.2f}%/yr, "
              f"t = {d['alpha_t']:+.2f}")
        print(f"    That blend needs no options, no assignment, no collateral")
        print(f"    and no chain. It is what the premium has to beat to be worth")
        print(f"    the machinery.")
        print()

    _print_decades(rets)

    print("\nREADING THIS. A lower CAGR at a much lower vol is not a failure —")
    print("check Sharpe and maxDD before concluding. The wheel's problem in a")
    print("bull market is truncated upside, and the decade table is where that")
    print("shows up. Note also that both indices roll MONTHLY AT THE MONEY:")
    print("they bound the STRUCTURAL return of writing S&P premium, not a")
    print("discretionary wheel at a chosen delta on a chosen name.")


def _print_decades(rets):
    print("CAGR BY DECADE (%/yr)   * = series not live for the whole decade")
    print(f"{'era':<8}{'BXM':>10}{'PUT':>10}{'SP500TR':>10}{'GSPC':>10}"
          f"{'BXM-TR':>9}{'PUT-TR':>9}")
    for row in decade_table(rets):
        def f(k):
            v = row.get(k, float("nan"))
            if not np.isfinite(v):
                return f"{'-':>10}"
            return f"{v:>9.2f}" + ("*" if row.get(k + "_partial") else " ")

        def d(k):
            v, b = row.get(k, float("nan")), row.get("SP500TR", float("nan"))
            return (f"{v - b:>+9.2f}" if np.isfinite(v) and np.isfinite(b)
                    else f"{'-':>9}")
        print(f"{row['era']:<8}{f('BXM')}{f('PUT')}{f('SP500TR')}{f('GSPC')}"
              f"{d('BXM')}{d('PUT')}")
    print("\nThe sign flips with the era, and that IS the mechanism: writing")
    print("premium wins when the index goes nowhere (the 2000s) and loses when")
    print("it runs (the 2010s, the 2020s). Nothing here is a timing signal —")
    print("knowing which era you are in beforehand is the whole problem.")


if __name__ == "__main__":
    main()
