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
* `payout`  dividends + share repurchases, in dollars. Scaled by market cap in
            `sleeves.payout_yield`, this is the one candidate 5th sleeve that
            beat the market as a LONG-ONLY top quintile in every window tested.
            Boudoukh, Michaely, Richardson & Roberts 2007, https://doi.org/10.1111/j.1540-6261.2007.01241.x
* `net_cash` cash + short-term investments - all debt. Not a signal by itself:
            it is the enterprise adjustment in `sleeves.value_ebm`, which needs
            market cap and therefore lives in sleeves.py rather than here.
            Penman, Richardson & Tuna 2007, https://doi.org/10.1111/j.1475-679X.2007.00240.x

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
# Compustat's `che` (cash AND short-term investments) and `dltt + dlc` (all
# interest-bearing debt) have no single XBRL tag. Each is summed from the cash
# line plus whichever short-term-investment tag the filer chose, and from the
# long-term plus current portions of debt. `DebtCurrent` is the total; filers
# that omit it split the same amount across the two tags below it, so the two
# routes are alternatives, never added together.
CASH = ["CashAndCashEquivalentsAtCarryingValue"]
SHORT_INVEST = ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
DEBT_LT = ["LongTermDebtNoncurrent", "LongTermDebt"]
DEBT_CUR = ["DebtCurrent"]
DEBT_CUR_PARTS = ["LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"]
# Cash returned to shareholders, for the payout sleeve. Compustat's `prstkc`
# covers common AND preferred repurchases; `dvc` is common dividends only.
REPURCHASE = ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"]
REPURCHASE_PFD = ["PaymentsForRepurchaseOfPreferredStockAndPreferenceStock"]
DIVIDENDS = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]

PUBLISH_MONTH = 7      # fiscal year Y becomes usable on 1 July of Y+1
PUBLISH_DAY = 1


# A listed US company has more than 100,000 shares outstanding. Below that the
# value is a placeholder, not a count: the SEC frames carry SPG = 8,000,
# FTI = 1, PCG = 12,345 and EntityCommonStockSharesOutstanding = 0.
MIN_PLAUSIBLE_SHARES = 1e5
# How far from the ticker's own share-count scale a value may sit before it is
# rejected. Buybacks and issuance stay well inside 4x over a decade; the errors
# being caught are factors of 1,000.
SHARE_SCALE_TOL = 4.0


def _pick_shares(df: pd.DataFrame) -> pd.Series:
    """Share count per (ticker, year), chosen for consistency not tag priority.

    A plain first-present coalesce over `SHARES` is wrong, and measurably so. The
    failure is not that a tag is missing — it is that a HIGHER-priority tag
    carries a placeholder or a thousands-scaled figure while a lower-priority one
    carries the real number:

        SPG   CommonStockSharesOutstanding 2011-2012 = 8,000
              (WeightedAverageNumberOfDiluted = 293,573,472, correct)
        PCG   EntityCommonStockSharesOutstanding 2009 = 12,345
              (CommonStockSharesOutstanding = 371,272,457, correct)
        FTI   both share tags = 1 for 2015-2016   (real: 118,900,000)
        GRMN  CommonStockSharesOutstanding 2013-2016 reported in THOUSANDS
        PEG   CommonStockSharesOutstanding 2016 = 505   (real: 505,282,421)
        LEN   WeightedAverageNumberOfDiluted 2009 = 170,537 (thousands)

    Those produced market caps of $1M for a $47B REIT, which then received ~zero
    weight in every cap-weighted book. `rmxq._repair_share_units` cannot help:
    it compares a year against its neighbours, and several of these are wrong in
    every year the bad tag is present.

    So each (ticker, year) is resolved against the ticker's OWN scale — the
    median of every plausible value across all three tags and all years, which
    is robust as long as most observations are correctly scaled. A candidate
    within `SHARE_SCALE_TOL` of that scale is taken in tag-priority order; if
    none qualifies, the best candidate is snapped by a power of 1,000 when that
    lands it in range; if that also fails, the year is NaN. A name with no usable
    share count is excluded from cap weighting, which is correct — inventing a
    plausible-looking number would not be.
    """
    cols = [c for c in SHARES if c in df]
    if not cols:
        return pd.Series(np.nan, index=df.index, dtype=float)
    stacked = pd.concat([df[c] for c in cols])
    scale = stacked[stacked >= MIN_PLAUSIBLE_SHARES].groupby(
        level="ticker").median()

    cand = df[cols].to_numpy(dtype=float)          # rows in tag priority order
    ref = scale.reindex(
        df.index.get_level_values("ticker")).to_numpy(dtype=float)
    out = np.full(len(df), np.nan)

    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(cand) & (cand >= MIN_PLAUSIBLE_SHARES)
        in_range = ok & np.isfinite(ref)[:, None] & \
            (cand <= ref[:, None] * SHARE_SCALE_TOL) & \
            (cand >= ref[:, None] / SHARE_SCALE_TOL)
        # first candidate in tag-priority order that sits at the right scale
        first = np.argmax(in_range, axis=1)
        hit = in_range.any(axis=1)
        out[hit] = cand[hit, first[hit]]

        # nothing at the right scale: try snapping a power of 1,000
        todo = (~hit) & np.isfinite(ref) & np.isfinite(cand).any(axis=1)
        if todo.any():
            best = np.where(np.isfinite(cand), cand, np.nan)
            idx = np.nanargmax(np.where(np.isfinite(best), best, -np.inf),
                               axis=1)
            v = best[np.arange(len(df)), idx]
            k = np.round(np.log(v / ref) / np.log(1000.0))
            snapped = v / (1000.0 ** k)
            good = todo & np.isfinite(snapped) & (k != 0) & \
                (snapped <= ref * SHARE_SCALE_TOL) & \
                (snapped >= ref / SHARE_SCALE_TOL)
            out[good] = snapped[good]

    return pd.Series(out, index=df.index, dtype=float)


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
               CASH + SHORT_INVEST + DEBT_LT + DEBT_CUR + DEBT_CUR_PARTS +
               REPURCHASE + REPURCHASE_PFD + DIVIDENDS +
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

        out["shares"] = _pick_shares(df)

        # ---- balance sheet, for the EBM value sleeve -------------------------
        # A firm with no short-term investments does not tag the line at all, so
        # a missing STI means zero, not unknown. A missing CASH line, by
        # contrast, means the filer did not report it and `che` is genuinely
        # unknown — hence fillna(0) on one and not the other.
        cash = coalesce(CASH)
        out["cash_sti"] = cash + coalesce(SHORT_INVEST).fillna(0.0)
        lt = coalesce(DEBT_LT)
        cur = coalesce(DEBT_CUR)
        parts = sum((df.get(n).fillna(0.0) for n in DEBT_CUR_PARTS
                     if n in df), start=pd.Series(0.0, index=df.index))
        cur = cur.fillna(parts.where(parts > 0))
        # Debt-free firms tag neither line. Treating that as unknown would drop
        # the whole cash-rich end of the universe, which is exactly where the
        # enterprise adjustment matters most, so an absent debt line is zero
        # provided the balance sheet itself parsed (assets present).
        reported = lt.notna() | cur.notna()
        out["debt"] = (lt.fillna(0.0) + cur.fillna(0.0)) \
            .where(reported, 0.0).where(reported | assets.notna())
        # OSAP's EBM adjustment: che - dltt - dlc (net financial ASSETS). The
        # remaining Compustat terms in the published formula (dc convertible
        # debt, dvpa preferred dividends in arrears, tstkp treasury preferred)
        # have no reliable XBRL frame and are immaterial for S&P 500 filers.
        out["net_cash"] = out["cash_sti"] - out["debt"]

        # ---- cash returned to shareholders, for the payout sleeve ------------
        # A firm that paid no dividend and bought back nothing does not tag the
        # line. That is a real zero, not missing data — and it matters that the
        # distinction is right, because OSAP's PayoutYield EXCLUDES firms with
        # payout <= 0, so treating "no tag" as unknown versus as zero changes
        # who is in the universe rather than just their score. Either line
        # present is taken as evidence the cash-flow statement parsed.
        rep = coalesce(REPURCHASE)
        rep_p = coalesce(REPURCHASE_PFD)
        div = coalesce(DIVIDENDS)
        seen = rep.notna() | div.notna() | rep_p.notna()
        out["payout"] = (rep.fillna(0.0) + rep_p.fillna(0.0)
                         + div.fillna(0.0)).where(seen)

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
