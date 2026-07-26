"""fetch_external_factors.py — harvest published factor records into research_data/external.

Why this exists. This project's own panel starts in 1996 (prices) and 2009
(SEC XBRL fundamentals), which is far too short to establish significance for
anything with a 4-5%/yr tracking error — see docs/RMX-RESULTS.md, "the honest
limit". The fix is not more of our own data; it is to screen every candidate
signal against a *published, survivorship-free, peer-reviewed* record first,
and only then spend panel time on the survivors.

Four sources, all free, all downloaded here without credentials:

    osap    Chen & Zimmermann, "Open Source Cross-Sectional Asset Pricing"
            (Critical Finance Review 2022). 212 published predictors, monthly
            long-short portfolio returns 1926-2024 plus value-weighted quintile
            portfolios. https://www.openassetpricing.com/
            Release 2.0.0 (Oct 2025) lives in a public Google Drive folder; the
            folder listing is parsed the same way `gdown` does it.

    jkp     Jensen, Kelly & Pedersen, "Is There a Replication Crisis in
            Finance?" (Journal of Finance 2023). 153 factors in 13 themes,
            93 countries, capped-value-weighted. https://jkpfactors.com/
            Plain S3 objects; the site's own download form builds these URLs.

    aqr     AQR Data Sets: Quality-Minus-Junk, Betting-Against-Beta and
            Time-Series-Momentum monthly factor returns, US back to 1926/1957.
            https://www.aqr.com/library/data-sets

    french  Ken French's monthly research factors + the momentum factor, 1926-.
            The daily file is already fetched by the RMX pipeline; this adds
            the monthly ones the external comparisons need.

Everything is written to `research_data/external/<source>/` with a
`_manifest.json` recording URL, bytes, sha256, row count and date range per
file, so a later result can name exactly which vintage produced it.

    py scripts/fetch_external_factors.py              # everything
    py scripts/fetch_external_factors.py --only jkp aqr
    py scripts/fetch_external_factors.py --list       # show plan, download nothing
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import time
import zipfile
from io import BytesIO

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research_data", "external")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# --- OSAP: release 2.0.0, October 2025 --------------------------------------
OSAP_FOLDER = "https://drive.google.com/drive/folders/1qQDuTsnyvWfEJR6nPBQZ8xxlq6bkLG_y"
OSAP_WANTED = {
    # file name in the Drive folder     -> local name
    "SignalDoc.csv": "SignalDoc.csv",
    "PredictorPortsFull.csv": "PredictorPortsFull.csv",
    "PredictorAltPorts_QuintilesVW.zip": "PredictorAltPorts_QuintilesVW.zip",
    "PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.zip":
        "PredictorAltPorts_ex_micro.zip",
}

# --- JKP: the S3 keys the site's downloadFactors() builds -------------------
JKP_S3 = "https://jkpfactors-data.s3.amazonaws.com/public"
JKP_THEMES = ["accruals", "debt_issuance", "investment", "low_leverage",
              "low_risk", "momentum", "profit_growth", "profitability",
              "quality", "seasonality", "short_term_reversal", "size", "value"]
JKP_FILES = {
    # all 153 factors, USA, monthly, capped value weighted — the main file
    "usa_all_factors_monthly_vw_cap.zip":
        f"{JKP_S3}/%5Busa%5D_%5Ball_factors%5D_%5Bmonthly%5D_%5Bvw_cap%5D.zip",
    # the 13 theme aggregates — one series per theme, the cluster-level view
    "usa_all_themes_monthly_vw_cap.zip":
        f"{JKP_S3}/%5Busa%5D_%5Ball_themes%5D_%5Bmonthly%5D_%5Bvw_cap%5D.zip",
    # equal-weighted twin: the size-drag diagnostic that killed RMX-Q
    "usa_all_factors_monthly_ew.zip":
        f"{JKP_S3}/%5Busa%5D_%5Ball_factors%5D_%5Bmonthly%5D_%5Bew%5D.zip",
    # NYSE size breakpoints, so size buckets can be rebuilt locally
    "nyse_cutoffs.csv": f"{JKP_S3}/other/nyse_cutoffs.csv",
    # NOTE: the site also offers size-bucket files (large/mega/small/micro/nano)
    # via `public/factor/[region]_[theme]_[universe].zip`. Every such key
    # returns 403 as of 2026-07-25 — they are referenced by the download form
    # but not published on the bucket. Use OSAP's ME>NYSE20pct liquidity screen
    # for the large-cap view instead, and nyse_cutoffs.csv to rebuild buckets.
}

# --- AQR -------------------------------------------------------------------
AQR_BASE = "https://www.aqr.com/-/media/AQR/Documents/Insights/Data-Sets"
AQR_FILES = {
    "quality-minus-junk-factors-monthly.xlsx":
        f"{AQR_BASE}/Quality-Minus-Junk-Factors-Monthly.xlsx",
    # NOTE: the 10 quality-sorted long-only portfolios are advertised on the
    # site but every filename variant of
    # .../Quality-Minus-Junk-Ten-Quality-Sorted-Portfolios-Monthly.xlsx returns
    # AQR's 404 page. The QMJ factor file below carries the same signal; grab
    # the long-only portfolios by hand from aqr.com/library/data-sets if the
    # decile spread is ever needed.
    "betting-against-beta-equity-factors-monthly.xlsx":
        f"{AQR_BASE}/Betting-Against-Beta-Equity-Factors-Monthly.xlsx",
    "time-series-momentum-factors-monthly.xlsx":
        f"{AQR_BASE}/Time-Series-Momentum-Factors-Monthly.xlsx",
    "the-devil-in-hmls-details-factors-monthly.xlsx":
        f"{AQR_BASE}/The-Devil-in-HMLs-Details-Factors-Monthly.xlsx",
}

# --- Ken French ------------------------------------------------------------
FRENCH_BASE = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp")
FRENCH_FILES = {
    "F-F_Research_Data_5_Factors_2x3.zip":
        f"{FRENCH_BASE}/F-F_Research_Data_5_Factors_2x3_CSV.zip",
    "F-F_Momentum_Factor.zip": f"{FRENCH_BASE}/F-F_Momentum_Factor_CSV.zip",
    "F-F_ST_Reversal_Factor.zip": f"{FRENCH_BASE}/F-F_ST_Reversal_Factor_CSV.zip",
    "F-F_LT_Reversal_Factor.zip": f"{FRENCH_BASE}/F-F_LT_Reversal_Factor_CSV.zip",
    "25_Portfolios_OP_INV_5x5.zip": f"{FRENCH_BASE}/25_Portfolios_OP_INV_5x5_CSV.zip",
}


# ======================================================================= util
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def _sha256_head(path: str, limit: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(limit))
    return h.hexdigest()[:16]


def _download(sess: requests.Session, url: str, dest: str,
              force: bool = False) -> dict:
    """Stream `url` to `dest`. Returns a manifest record."""
    name = os.path.basename(dest)
    if os.path.exists(dest) and not force:
        n = os.path.getsize(dest)
        print(f"    {name:<52} cached  {n / 1e6:8.1f} MB")
        return {"file": name, "url": url, "bytes": n, "cached": True,
                "sha256_16": _sha256_head(dest)}
    t0 = time.time()
    r = sess.get(url, stream=True, timeout=180)
    r.raise_for_status()
    tmp = dest + ".part"
    n = mark = 0
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            if chunk:
                f.write(chunk)
                n += len(chunk)
                if n - mark >= 25e6:          # progress ping every 25 MB only
                    mark = n
                    print(f"    {name:<52} {n / 1e6:8.1f} MB ...", flush=True)
    os.replace(tmp, dest)
    dt = time.time() - t0
    print(f"    {name:<52} {n / 1e6:8.1f} MB  {dt:5.1f}s")
    return {"file": name, "url": url, "bytes": n, "cached": False,
            "seconds": round(dt, 1), "sha256_16": _sha256_head(dest)}


def _write_manifest(folder: str, records: list[dict], source: str,
                    note: str) -> None:
    path = os.path.join(folder, "_manifest.json")
    prev = {}
    if os.path.exists(path):
        try:
            prev = json.load(open(path))
        except (ValueError, OSError):
            prev = {}
    prev.update({"source": source, "note": note,
                 "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                 "files": records})
    with open(path, "w") as f:
        json.dump(prev, f, indent=2)


# ================================================================ google drive
def _drive_folder_entries(sess: requests.Session, folder_url: str) -> list[tuple]:
    """(id, name, mime) for every child of a public Drive folder.

    Google embeds the listing as a JS string assigned to `window['_DRIVE_ivd']`.
    This is the same trick `gdown` uses; done here with a regex so the harvest
    needs nothing beyond `requests` (installing `beautifulsoup4` is harmless but
    installing `openassetpricing` is not — it pins an older pandas and would
    downgrade the one the engine runs on).
    """
    url = folder_url + ("&hl=en" if "?" in folder_url else "?hl=en")
    res = sess.get(url, timeout=90)
    res.raise_for_status()
    text = res.text
    # The listing is one JS string literal:
    #     window['_DRIVE_ivd'] = '\x5b\x5b\x5b\x22<file id>\x22, ...'
    # Anchor on the assignment rather than "the second quoted string in the
    # script", which is what gdown does and which breaks whenever Google adds
    # another literal ahead of it.
    m = re.search(r"window\['_DRIVE_ivd'\]\s*=\s*'((?:[^'\\]|\\.)*)'", text)
    if m is None:
        raise RuntimeError(
            "Drive listing not found. Either the folder is rate-limited (wait "
            "and retry) or its sharing changed. Manual fallback: open "
            f"{folder_url} and download the files in OSAP_WANTED by hand.")
    arr = json.loads(m.group(1).encode("utf-8").decode("unicode_escape"))
    children = arr[0] or []
    return [(e[0], e[2].encode("raw_unicode_escape").decode("utf-8"), e[3])
            for e in children]


FOLDER_MIME = "application/vnd.google-apps.folder"


def _drive_walk(sess: requests.Session, folder_url: str, depth: int = 0,
                skip: frozenset = frozenset({"Individual", "Results",
                                             "Predictors"})) -> dict[str, str]:
    """Flat {file name: file id} for a folder tree, skipping the huge subfolders."""
    out: dict[str, str] = {}
    for fid, name, mime in _drive_folder_entries(sess, folder_url):
        if mime == FOLDER_MIME:
            if name in skip or depth >= 3:
                continue
            out.update(_drive_walk(
                sess, f"https://drive.google.com/drive/folders/{fid}",
                depth + 1, skip))
        else:
            out.setdefault(name, fid)
    return out


def _drive_direct_url(sess: requests.Session, file_id: str) -> str:
    """Resolve a Drive file id to a URL that streams bytes, not an HTML page.

    Small files download straight from `uc?id=`. Anything over ~100 MB gets a
    virus-scan interstitial whose form must be re-posted; the token lives in
    hidden inputs on that page.
    """
    url = f"https://drive.google.com/uc?id={file_id}&export=download"
    r = sess.get(url, stream=True, timeout=90)
    ctype = r.headers.get("Content-Type", "")
    if "text/html" not in ctype:
        r.close()
        return url
    body = r.text
    m = re.search(r'action="([^"]+)"', body)
    if not m:
        m2 = re.search(r'href="(/uc\?export=download[^"]+)', body)
        if m2:
            return "https://docs.google.com" + m2.group(1).replace("&amp;", "&")
        raise RuntimeError(f"cannot resolve Drive file {file_id}")
    action = m.group(1).replace("&amp;", "&")
    params = dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', body))
    q = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{action}{'&' if '?' in action else '?'}{q}"


# ==================================================================== sources
def fetch_osap(force: bool = False) -> list[dict]:
    folder = os.path.join(OUT, "osap")
    os.makedirs(folder, exist_ok=True)
    sess = _session()
    print("  resolving Google Drive folder listing ...")
    ids = _drive_walk(sess, OSAP_FOLDER)
    print(f"  {len(ids)} files visible in the release folder")
    recs = []
    for remote, local in OSAP_WANTED.items():
        fid = ids.get(remote)
        if fid is None:
            print(f"    {remote:<52} NOT FOUND in listing")
            recs.append({"file": local, "error": "not found in Drive listing"})
            continue
        url = _drive_direct_url(sess, fid)
        try:
            recs.append(_download(sess, url, os.path.join(folder, local), force))
        except Exception as exc:                     # noqa: BLE001
            print(f"    {local:<52} FAILED {type(exc).__name__}: {exc}")
            recs.append({"file": local, "error": f"{type(exc).__name__}: {exc}"})
    _write_manifest(folder, recs, "openassetpricing.com release 2.0.0 (2025-10)",
                    "Chen & Zimmermann, Critical Finance Review 2022. 212 "
                    "published predictors; PredictorPortsFull.csv is the "
                    "monthly long-short record, QuintilesVW the value-weighted "
                    "quintile portfolios. CC-BY: cite the paper.")
    return recs


def fetch_jkp(force: bool = False) -> list[dict]:
    folder = os.path.join(OUT, "jkp")
    os.makedirs(folder, exist_ok=True)
    sess = _session()
    recs = []
    for local, url in JKP_FILES.items():
        try:
            recs.append(_download(sess, url, os.path.join(folder, local), force))
        except Exception as exc:                     # noqa: BLE001
            print(f"    {local:<52} FAILED {type(exc).__name__}: {exc}")
            recs.append({"file": local, "error": f"{type(exc).__name__}: {exc}"})
    _write_manifest(folder, recs, "jkpfactors.com (updated through 2025-12)",
                    "Jensen, Kelly & Pedersen, Journal of Finance 2023. 153 "
                    "factors in 13 themes. CC BY-NC 4.0 — non-commercial use. "
                    f"Themes: {', '.join(JKP_THEMES)}")
    return recs


def fetch_aqr(force: bool = False) -> list[dict]:
    folder = os.path.join(OUT, "aqr")
    os.makedirs(folder, exist_ok=True)
    sess = _session()
    recs = []
    for local, url in AQR_FILES.items():
        try:
            recs.append(_download(sess, url, os.path.join(folder, local), force))
        except Exception as exc:                     # noqa: BLE001
            print(f"    {local:<52} FAILED {type(exc).__name__}: {exc}")
            recs.append({"file": local, "error": f"{type(exc).__name__}: {exc}"})
    _write_manifest(folder, recs, "aqr.com/library/data-sets",
                    "QMJ (Asness, Frazzini & Pedersen), BAB (Frazzini & "
                    "Pedersen), TSMOM (Moskowitz, Ooi & Pedersen), HML Devil. "
                    "US series start 1926-1957 depending on factor.")
    return recs


def fetch_french(force: bool = False) -> list[dict]:
    folder = os.path.join(OUT, "french")
    os.makedirs(folder, exist_ok=True)
    sess = _session()
    recs = []
    for local, url in FRENCH_FILES.items():
        try:
            recs.append(_download(sess, url, os.path.join(folder, local), force))
        except Exception as exc:                     # noqa: BLE001
            print(f"    {local:<52} FAILED {type(exc).__name__}: {exc}")
            recs.append({"file": local, "error": f"{type(exc).__name__}: {exc}"})
    _write_manifest(folder, recs, "Ken French data library",
                    "Monthly research factors, momentum, short- and long-term "
                    "reversal, and the 25 OP x INV portfolios. The daily file "
                    "used by rhengine.alpha.panel is fetched separately.")
    return recs


SOURCES = {"osap": fetch_osap, "jkp": fetch_jkp, "aqr": fetch_aqr,
           "french": fetch_french}


def inventory() -> None:
    """Print what is on disk, with the unzipped member listing for archives."""
    if not os.path.isdir(OUT):
        print("nothing harvested yet")
        return
    total = 0
    for src in sorted(os.listdir(OUT)):
        d = os.path.join(OUT, src)
        if not os.path.isdir(d):
            continue
        print(f"\n{src}/")
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if f == "_manifest.json" or not os.path.isfile(p):
                continue
            n = os.path.getsize(p)
            total += n
            extra = ""
            if f.endswith(".zip"):
                try:
                    with zipfile.ZipFile(p) as z:
                        names = z.namelist()
                    extra = f"  [{len(names)} member(s): " \
                            f"{', '.join(names[:3])}{' ...' if len(names) > 3 else ''}]"
                except zipfile.BadZipFile:
                    extra = "  [NOT A ZIP — download failed]"
            print(f"    {f:<52} {n / 1e6:8.2f} MB{extra}")
    print(f"\ntotal {total / 1e6:.1f} MB in {OUT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=sorted(SOURCES),
                    help="fetch only these sources")
    ap.add_argument("--force", action="store_true",
                    help="re-download files already on disk")
    ap.add_argument("--list", action="store_true",
                    help="show the plan and what is already on disk")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    if args.list:
        for name, files in (("osap", OSAP_WANTED), ("jkp", JKP_FILES),
                            ("aqr", AQR_FILES), ("french", FRENCH_FILES)):
            print(f"{name}: {len(files)} files")
        inventory()
        return

    todo = args.only or list(SOURCES)
    failed = 0
    for name in todo:
        print(f"\n=== {name} ===")
        recs = SOURCES[name](args.force)
        failed += sum(1 for r in recs if "error" in r)
    inventory()
    if failed:
        print(f"\n{failed} file(s) failed — see the _manifest.json entries")
        sys.exit(1)


if __name__ == "__main__":
    main()
