"""panel.py — the point-in-time research panel.

Loads the long-history dataset built by scripts/fetch_history.py into aligned
date x ticker matrices, plus the daily S&P 500 membership mask reconstructed
from the component-change file.

Everything downstream joins **on date**. Nothing is aligned by position; that
was audit finding FC-9, which corrupted beta for 18% of the old universe.

    from rhengine.alpha.panel import Panel
    p = Panel.load()
    p.eligible(pd.Timestamp("2010-06-30"))   # tradeable names as of that close
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "research_data")
BARS = os.path.join(DATA, "yahoo_long")

# Universe floors, frozen in docs/RMX-PREREGISTRATION.md 2.1.
MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 5_000_000.0
MIN_HISTORY_DAYS = 756          # 36 months, the beta estimation window
LIQ_WINDOW = 60                 # sessions used for the median-dollar-volume test


def yahoo_symbol(t: str) -> str:
    return t.replace(".", "-").strip().upper()


@dataclass
class Panel:
    """Aligned price/volume matrices + the point-in-time membership mask."""

    adj: pd.DataFrame          # split/dividend-adjusted close  (total-return basis)
    close: pd.DataFrame        # raw close, for the $5 price floor
    dollar_vol: pd.DataFrame   # close * volume
    member: pd.DataFrame       # bool, S&P 500 membership as of that session
    spy: pd.Series             # SPY adjusted close
    rf: pd.Series              # Ken French daily risk-free rate (decimal)
    mkt: pd.Series             # Ken French daily Mkt-RF (decimal)
    coverage: pd.Series        # priced members / PIT members, per session
    n_members: pd.Series       # PIT member count, per session
    _liq_cache: dict = field(default_factory=dict, repr=False)

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, start: str = "1996-01-01") -> "Panel":
        members_raw = pd.read_csv(os.path.join(DATA, "sp500_pit_raw.csv"))
        members_raw["date"] = pd.to_datetime(members_raw["date"])
        members_raw = members_raw.sort_values("date")

        ever = sorted({t.strip() for row in members_raw["tickers"]
                       for t in str(row).split(",")})

        adj, close, dvol = {}, {}, {}
        for tk in ever:
            path = os.path.join(BARS, f"{yahoo_symbol(tk)}.csv.gz")
            if not os.path.exists(path):
                continue
            df = pd.read_csv(path, parse_dates=["date"])
            df = df[df["date"] >= start]
            if df.empty:
                continue
            df = df.drop_duplicates("date").set_index("date").sort_index()
            adj[tk] = df["adjclose"]
            close[tk] = df["close"]
            dvol[tk] = df["close"] * df["volume"]

        adj = pd.DataFrame(adj).sort_index()
        close = pd.DataFrame(close).reindex_like(adj)
        dollar_vol = pd.DataFrame(dvol).reindex_like(adj)

        spy_df = pd.read_csv(os.path.join(BARS, "SPY.csv.gz"), parse_dates=["date"])
        spy_df = spy_df[spy_df["date"] >= start].drop_duplicates("date")
        spy = spy_df.set_index("date")["adjclose"].sort_index()

        # The trading calendar is SPY's, restricted to sessions the panel has.
        cal = adj.index.intersection(spy.index)
        adj, close, dollar_vol = adj.loc[cal], close.loc[cal], dollar_vol.loc[cal]
        spy = spy.loc[cal]

        # Coverage is measured against EVERY name the index ever held, not just
        # the ones Yahoo still serves - otherwise the survivorship gap is
        # invisible by construction (audit FC-4).
        member_full = cls._membership_mask(members_raw, cal, pd.Index(ever))
        member = member_full[adj.columns]
        rf, mkt = cls._french(cal)
        # French's series ends a couple of months before Yahoo's. Extending the
        # market factor by forward-filling one day's return would be nonsense;
        # SPY's own excess return is the right stand-in for the tail.
        gap = mkt.isna()
        if gap.any():
            mkt = mkt.fillna(spy.pct_change() - rf)

        priced = adj.notna() & member
        coverage = priced.sum(axis=1) / member_full.sum(axis=1).replace(0, np.nan)

        return cls(adj=adj, close=close, dollar_vol=dollar_vol, member=member,
                   spy=spy, rf=rf, mkt=mkt, coverage=coverage,
                   n_members=member_full.sum(axis=1))

    @staticmethod
    def _membership_mask(members_raw: pd.DataFrame, cal: pd.DatetimeIndex,
                         cols: pd.Index) -> pd.DataFrame:
        """Daily bool mask: was `ticker` in the index at that session's close?

        The source file records a full component list on each change-date; the
        list is therefore forward-filled to every session until the next change.
        """
        idx = {t: i for i, t in enumerate(cols)}
        rows, dates = [], []
        for d, tks in zip(members_raw["date"], members_raw["tickers"]):
            row = np.zeros(len(cols), dtype=bool)
            for t in str(tks).split(","):
                j = idx.get(t.strip())
                if j is not None:
                    row[j] = True
            rows.append(row)
            dates.append(d)
        snap = pd.DataFrame(rows, index=pd.DatetimeIndex(dates), columns=cols)
        snap = snap[~snap.index.duplicated(keep="last")]
        return snap.reindex(snap.index.union(cal)).ffill().reindex(cal).fillna(False)

    @staticmethod
    def _french(cal: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
        path = os.path.join(DATA, "ff_daily.csv")
        raw = open(path, encoding="latin-1").read().splitlines()
        recs = []
        for ln in raw:
            # blank lines appear between records depending on how the file was
            # written; only the YYYYMMDD rows are data, everything else is prose
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) < 5 or len(parts[0]) != 8 or not parts[0].isdigit():
                continue
            try:
                recs.append((pd.Timestamp(parts[0]), float(parts[1]) / 100.0,
                             float(parts[4]) / 100.0))
            except ValueError:
                continue
        if not recs:
            raise ValueError(f"no factor rows parsed from {path}")
        ff = pd.DataFrame(recs, columns=["date", "mkt_rf", "rf"]).set_index("date")
        ff = ff.reindex(cal)
        # Hold the last observed short rate past the end of French's file rather
        # than silently paying zero on cash; the market factor is NOT held (a
        # repeated daily return would be fiction) and is patched by the caller.
        return ff["rf"].ffill(), ff["mkt_rf"]

    # -------------------------------------------------------------- selection
    def _liquidity_ok(self, iloc: int) -> np.ndarray:
        hit = self._liq_cache.get(iloc)
        if hit is None:
            lo = max(0, iloc - LIQ_WINDOW + 1)
            med = self.dollar_vol.iloc[lo:iloc + 1].median()
            hit = (med.fillna(0.0).to_numpy(dtype=float) >= MIN_DOLLAR_VOL)
            self._liq_cache[iloc] = hit
        return hit

    def eligible(self, date: pd.Timestamp) -> pd.Index:
        """Tradeable names as of `date`'s close, using only data <= date.

        Point-in-time by construction: every input is sliced at `date`, so the
        answer cannot change when more history is appended to the cache. That
        is audit test `test_universe_is_point_in_time`, which the old
        `massive_universe()` failed.
        """
        iloc = self.adj.index.get_indexer([date], method="pad")[0]
        if iloc < 0:
            return pd.Index([])
        d = self.adj.index[iloc]

        ok = self.member.loc[d].to_numpy(dtype=bool).copy()
        ok &= self._liquidity_ok(int(iloc))
        ok &= (self.close.loc[d].fillna(0.0).values >= MIN_PRICE)

        hist = self.adj.iloc[max(0, iloc - MIN_HISTORY_DAYS + 1):iloc + 1]
        ok &= (hist.notna().sum().values >= MIN_HISTORY_DAYS * 0.9)
        ok &= self.adj.loc[d].notna().values
        return self.adj.columns[ok]

    def month_ends(self, lo: str | None = None, hi: str | None = None) -> pd.DatetimeIndex:
        """Last trading session of each month within [lo, hi]."""
        idx = self.adj.index
        if lo is not None:
            idx = idx[idx >= pd.Timestamp(lo)]
        if hi is not None:
            idx = idx[idx <= pd.Timestamp(hi)]
        s = pd.Series(idx, index=idx)
        return pd.DatetimeIndex(s.groupby([idx.year, idx.month]).last().values)

    def monthly_returns(self, month_ends: pd.DatetimeIndex) -> pd.DataFrame:
        """Simple monthly total returns on the month-end grid."""
        px = self.adj.reindex(month_ends)
        return px.pct_change()

    def summary(self) -> str:
        first, last = self.adj.index[0].date(), self.adj.index[-1].date()
        return (
            f"panel {first} .. {last}  {len(self.adj):,} sessions  "
            f"{self.adj.shape[1]:,} tickers priced\n"
            f"priced-member coverage: mean {self.coverage.mean():.3f}  "
            f"min {self.coverage.min():.3f} on {self.coverage.idxmin().date()}  "
            f"(index size {self.n_members.iloc[-1]:.0f})"
        )


def coverage_by_year(p: Panel) -> pd.Series:
    return p.coverage.groupby(p.coverage.index.year).mean()


if __name__ == "__main__":
    p = Panel.load()
    print(p.summary())
    print()
    print("priced-member coverage by year (audit: abort below 0.85)")
    cov = coverage_by_year(p)
    print(cov.round(3).to_string())
    bad = cov[cov < 0.85]
    if len(bad):
        print(f"\nWARNING: {len(bad)} year(s) below the 0.85 coverage floor:")
        print(bad.round(3).to_string())
    print()
    d = pd.Timestamp("2010-06-30")
    print(f"eligible on {d.date()}: {len(p.eligible(d))} names")
    print(json.dumps({"sessions": len(p.adj), "tickers": int(p.adj.shape[1])}))
