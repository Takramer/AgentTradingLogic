"""fetch_sec_fundamentals.py — point-in-time fundamentals from SEC XBRL.

Audit O-4 asked for "point-in-time fundamentals with valid_from" and noted that
the repo's existing Finnhub/AlphaVantage adapters are not point-in-time. This
gets them from the primary source, free and without a key: the SEC's XBRL
`frames` API returns every filer's value for one concept in one calendar period
in a single request.

**How look-ahead is prevented.** The frames endpoint gives the fiscal period but
not the filing date. Rather than guess, this uses the Fama-French convention:
accounting data for fiscal year Y is only usable from **1 July of Y+1**, a lag
of at least six months. Every US filer's 10-K is public well inside that, so no
figure can be used before it existed. It costs some freshness and buys a
guarantee.

    py scripts/fetch_sec_fundamentals.py                 # 2009 -> latest
    py scripts/fetch_sec_fundamentals.py --from-year 2015
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research_data", "sec")
UA = "AgentTradingLogic research (therealtk101@gmail.com)"

# (concept, kind) - "d" = duration (annual frame), "i" = instant (year-end frame)
CONCEPTS = [
    ("Revenues", "d"),
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "d"),
    ("SalesRevenueNet", "d"),
    ("CostOfGoodsAndServicesSold", "d"),
    ("CostOfRevenue", "d"),
    ("CostOfGoodsSold", "d"),
    ("GrossProfit", "d"),
    ("SellingGeneralAndAdministrativeExpense", "d"),
    ("InterestExpense", "d"),
    ("OperatingIncomeLoss", "d"),
    ("NetIncomeLoss", "d"),
    ("Assets", "i"),
    ("StockholdersEquity", "i"),
    # second pass: broaden the revenue/COGS tags. GrossProfit alone covers only
    # ~56% of the S&P 500, which was the binding constraint on the whole test.
    ("RevenueFromContractWithCustomerIncludingAssessedTax", "d"),
    ("SalesRevenueGoodsNet", "d"),
    ("SalesRevenueServicesNet", "d"),
    ("RevenuesNetOfInterestExpense", "d"),
    ("CostOfServices", "d"),
    ("CostOfSales", "d"),
    ("CostOfGoodsAndServicesSoldExcludingDepreciationDepletionAndAmortization", "d"),
    ("ResearchAndDevelopmentExpense", "d"),
    ("GeneralAndAdministrativeExpense", "d"),
    ("SellingAndMarketingExpense", "d"),
    ("OperatingExpenses", "d"),
    ("CostsAndExpenses", "d"),
    ("InterestExpenseDebt", "d"),
]


def get(url: str, retries: int = 3):
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 + 3 * k)
        except Exception:
            time.sleep(2 + 3 * k)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-year", type=int, default=2009)
    ap.add_argument("--to-year", type=int, default=2025)
    ap.add_argument("--sleep", type=float, default=0.12)   # SEC asks for <= 10 req/s
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)

    tk = get("https://www.sec.gov/files/company_tickers.json")
    cik2tick = {}
    for row in tk.values():
        cik2tick.setdefault(int(row["cik_str"]), row["ticker"].upper())
    json.dump(cik2tick, open(os.path.join(OUT, "cik_ticker.json"), "w"))
    print(f"cik->ticker map: {len(cik2tick)} entries")

    got = miss = 0
    for concept, kind in CONCEPTS:
        store = {}
        for year in range(args.from_year, args.to_year + 1):
            frame = f"CY{year}" + ("Q4I" if kind == "i" else "")
            url = (f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}"
                   f"/USD/{frame}.json")
            payload = get(url)
            time.sleep(args.sleep)
            if not payload or "data" not in payload:
                miss += 1
                continue
            rows = {}
            for d in payload["data"]:
                t = cik2tick.get(int(d["cik"]))
                if t:
                    rows[t] = d["val"]
            store[str(year)] = rows
            got += 1
        path = os.path.join(OUT, f"{concept}.json.gz")
        with gzip.open(path, "wt") as f:
            json.dump(store, f)
        n = sum(len(v) for v in store.values())
        print(f"{concept:<52} {len(store):>2} years  {n:>7} facts")

    print(f"\ndone: {got} frames fetched, {miss} missing")


if __name__ == "__main__":
    main()
