"""fundamentals.py — point-in-time accounting signals from the SEC XBRL store.

Built for audit O-4, which named cross-sectional quality/profitability as the
one addition with a real prior and required "point-in-time fundamentals with a
valid_from date". The `valid_from` here is deliberately crude and deliberately
safe: fiscal year Y is usable only from **1 July of Y+1**, the Fama-French
convention. Every 10-K is filed well inside six months, so no number can be
consumed before it was public.

Signals, each with its citation:

* `gp_at`   gross profitability, (revenue - COGS) / assets
            Novy-Marx 2013, https://doi.org/10.1016/j.jfineco.2013.01.003
* `op_be`   operating profitability, (rev - COGS - SG&A - interest) / book equity
            Fama & French 2015, https://doi.org/10.1016/j.jfineco.2014.10.010
* `roa`     operating income / assets
* `asset_growth`  investment; a NEGATIVE predictor
            Cooper, Gulen & Schill 2008, https://doi.org/10.1111/j.1540-6261.2008.01370.x

The independent evidence that made this worth building at all is in
`scripts/ff_factor_check.py`: on Ken French's own 1963-2026 portfolios the
long-only top-30% operating-profitability portfolio beat the market in every
era measured, full-sample excess t = +2.88, while value (HML) and investment
(CMA) are flat-to-negative since 2001.
"""
from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEC = os.path.join(ROOT, "research_data", "sec")

# Tag priority for each economic quantity - first present wins. Several tags
# per quantity is not belt-and-braces: `GrossProfit` alone is reported by only
# ~56% of S&P 500 filers, and a signal computed on a non-random half of the
# universe is a selection effect, not a factor.
REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
           "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
           "RevenuesNetOfInterestExpense"]
COGS = ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
        "CostOfServices"]
SGA = ["SellingGeneralAndAdministrativeExpense"]
INTEREST = ["InterestExpense", "InterestExpenseDebt"]
# Shares outstanding, for market-cap weighting. Fama-French's published
# profitability portfolios are VALUE-weighted; an equal-weighted implementation
# is a different portfolio riding a different (size) exposure.
SHARES = ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding",
          "WeightedAverageNumberOfDilutedSharesOutstanding"]

PUBLISH_MONTH = 7      # fiscal year Y becomes usable on 1 July of Y+1
PUBLISH_DAY = 1


def _load(concept: str) -> dict:
    path = os.path.join(SEC, f"{concept}.json.gz")
    if not os.path.exists(path):
        return {}
    with gzip.open(path, "rt") as f:
        return json.load(f)


@dataclass
class Fundamentals:
    """Wide frame: index (ticker, fiscal_year) -> accounting signals."""

    table: pd.DataFrame        # MultiIndex (ticker, year)
    valid_from: pd.Series      # per fiscal year, the date the data may be used

    @classmethod
    def load(cls) -> "Fundamentals":
        raw = {c: _load(c) for c in
               REVENUE + COGS + SGA + INTEREST + SHARES +
               ["GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
                "Assets", "StockholdersEquity", "ResearchAndDevelopmentExpense",
                "GeneralAndAdministrativeExpense", "SellingAndMarketingExpense",
                "OperatingExpenses", "CostsAndExpenses"]}

        years = sorted({int(y) for store in raw.values() for y in store})
        tickers = sorted({t for store in raw.values() for y in store for t in store[y]})
        idx = pd.MultiIndex.from_product([tickers, years], names=["ticker", "year"])
        cols = {}
        for concept, store in raw.items():
            s = pd.Series(np.nan, index=idx, dtype=float)
            flat = {(t, int(y)): v for y, rows in store.items() for t, v in rows.items()}
            if flat:
                s.update(pd.Series(flat))
            cols[concept] = s
        df = pd.DataFrame(cols)

        def coalesce(names):
            out = pd.Series(np.nan, index=df.index, dtype=float)
            for n in names:
                if n in df:
                    out = out.fillna(df[n])
            return out

        rev = coalesce(REVENUE)
        cogs = coalesce(COGS)
        gp = df.get("GrossProfit", pd.Series(np.nan, index=df.index)).copy()
        gp = gp.fillna(rev - cogs)
        # last resort: revenue minus total costs, plus back the operating
        # expenses that are not cost of sales
        alt = rev - df.get("CostsAndExpenses") + df.get("OperatingExpenses")
        gp = gp.fillna(alt)

        assets = df.get("Assets")
        equity = df.get("StockholdersEquity")
        sga = coalesce(SGA)
        # SG&A is often split into its components instead of tagged as one line
        sga_parts = (df.get("GeneralAndAdministrativeExpense").fillna(0.0)
                     + df.get("SellingAndMarketingExpense").fillna(0.0))
        sga = sga.fillna(sga_parts.where(sga_parts > 0))
        interest = coalesce(INTEREST)
        opinc = df.get("OperatingIncomeLoss")

        out = pd.DataFrame(index=df.index)
        out["assets"] = assets
        out["equity"] = equity
        out["revenue"] = rev
        out["gross_profit"] = gp
        # Novy-Marx: gross profits scaled by ASSETS, not by equity or sales
        out["gp_at"] = gp / assets.where(assets > 0)
        op = gp - sga.fillna(0.0) - interest.fillna(0.0)
        out["op_be"] = op / equity.where(equity > 0)
        # high-coverage proxy: operating income already nets SG&A off revenue,
        # so it reaches the ~25% of filers that never tag a gross-profit line
        out["op_be_proxy"] = ((opinc - interest.fillna(0.0))
                              / equity.where(equity > 0))
        out["op_be"] = out["op_be"].fillna(out["op_be_proxy"])
        out["roa"] = opinc / assets.where(assets > 0)
        out["net_margin"] = df.get("NetIncomeLoss") / rev.where(rev > 0)

        out["shares"] = coalesce(SHARES)

        prev = out["assets"].groupby(level="ticker").shift(1)
        out["asset_growth"] = out["assets"] / prev.where(prev > 0) - 1.0

        vf = pd.Series(
            {y: pd.Timestamp(year=y + 1, month=PUBLISH_MONTH, day=PUBLISH_DAY)
             for y in years})
        return cls(table=out, valid_from=vf)

    # ------------------------------------------------------------------ query
    def latest_year_for(self, date: pd.Timestamp) -> int | None:
        """The most recent fiscal year whose data was public on `date`."""
        ok = self.valid_from[self.valid_from <= date]
        return int(ok.index.max()) if len(ok) else None

    def as_of(self, date: pd.Timestamp, field: str,
              tickers: pd.Index | None = None) -> pd.Series:
        """Cross-section of `field` using only data published on or before `date`."""
        y = self.latest_year_for(pd.Timestamp(date))
        if y is None or field not in self.table:
            return pd.Series(dtype=float)
        s = self.table[field].xs(y, level="year")
        s = s[np.isfinite(s)]
        return s.reindex(tickers).dropna() if tickers is not None else s

    def coverage(self, tickers: pd.Index, date: pd.Timestamp,
                 field: str = "gp_at") -> float:
        got = self.as_of(date, field, tickers)
        return float(len(got) / max(len(tickers), 1))


if __name__ == "__main__":
    f = Fundamentals.load()
    print("fundamentals table:", f.table.shape)
    print("fiscal years:", int(f.table.index.get_level_values('year').min()),
          "->", int(f.table.index.get_level_values('year').max()))
    print("\nvalid_from (Fama-French 6-month lag):")
    print(f.valid_from.tail(4).to_string())
    for d in ("2012-06-29", "2016-06-30", "2020-06-30", "2026-06-30"):
        s = f.as_of(pd.Timestamp(d), "gp_at")
        print(f"\n{d}: fiscal year {f.latest_year_for(pd.Timestamp(d))}, "
              f"{len(s)} tickers with gp_at")
        print(f"   gp_at quantiles: " +
              "  ".join(f"p{q}={s.quantile(q / 100):.3f}" for q in (10, 50, 90)))
