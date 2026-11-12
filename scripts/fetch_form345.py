"""fetch_form345.py — SEC insider-transaction data sets (Forms 3/4/5), 2006 onward.

Downloads the quarterly zips into research_data/sec/form345/ (gitignored) and
extracts every open-market purchase (transaction code P) into
research_data/sec/form345_purchases.csv.gz. Also saves the SEC's current
CIK -> ticker map, used later to price the issuers.

    .venv/bin/python scripts/fetch_form345.py              # download + extract
    .venv/bin/python scripts/fetch_form345.py --extract    # extract only
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
import zipfile

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_sec_fundamentals import OUT, UA              # noqa: E402

DIR = os.path.join(OUT, "form345")
LISTING = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
EVENTS = os.path.join(OUT, "form345_purchases.csv.gz")


def get(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    return r.content


def download() -> None:
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(OUT, "company_tickers.json"), "wb") as f:
        f.write(get(TICKERS))
    links = sorted(set(re.findall(r'href="([^"]+_form345\.zip)"', get(LISTING).decode())))
    for link in links:
        path = os.path.join(DIR, link.rsplit("/", 1)[1])
        if os.path.exists(path):
            continue
        data = get("https://www.sec.gov" + link)
        with open(path, "wb") as f:
            f.write(data)
        print(f"{os.path.basename(path)}: {len(data) / 1e6:.1f} MB", flush=True)
        time.sleep(0.2)                                 # SEC asks for <= 10 req/s


def extract() -> pd.DataFrame:
    keep = []
    for name in sorted(os.listdir(DIR)):
        with zipfile.ZipFile(os.path.join(DIR, name)) as z:
            rd = lambda f, cols: pd.read_csv(io.BytesIO(z.read(f)), sep="\t",  # noqa: E731
                                            usecols=cols, dtype=str, on_bad_lines="skip")
            sub = rd("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                                       "ISSUERCIK", "ISSUERTRADINGSYMBOL"])
            own = rd("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK",
                                           "RPTOWNER_RELATIONSHIP"])
            tr = rd("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE",
                                          "TRANS_SHARES", "TRANS_PRICEPERSHARE",
                                          "TRANS_ACQUIRED_DISP_CD"])
        tr = tr[(tr.TRANS_CODE == "P") & (tr.TRANS_ACQUIRED_DISP_CD == "A")]
        own = own.groupby("ACCESSION_NUMBER").agg(
            RPTOWNERCIK=("RPTOWNERCIK", "first"),
            RELATIONSHIP=("RPTOWNER_RELATIONSHIP", lambda s: ",".join(s.dropna())))
        df = tr.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER")
        keep.append(df[df.DOCUMENT_TYPE.isin(["4", "4/A"])])
        print(f"{name}: {len(keep[-1])} purchases", flush=True)
    ev = pd.concat(keep, ignore_index=True)
    for c in ("TRANS_SHARES", "TRANS_PRICEPERSHARE"):
        ev[c] = pd.to_numeric(ev[c], errors="coerce")
    ev["FILING_DATE"] = pd.to_datetime(ev.FILING_DATE, format="%d-%b-%Y", errors="coerce")
    ev["TRANS_DATE"] = pd.to_datetime(ev.TRANS_DATE, format="%d-%b-%Y", errors="coerce")
    ev.to_csv(EVENTS, index=False)
    print(f"{len(ev)} purchases -> {EVENTS}")
    return ev


if __name__ == "__main__":
    if "--extract" not in sys.argv:
        download()
    extract()
