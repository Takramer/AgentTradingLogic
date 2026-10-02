"""external.py — published factor records as a queryable panel.

`scripts/fetch_external_factors.py` puts four published datasets on disk. This
module turns them into one thing: a monthly wide frame of decimal returns,
indexed by month-end, with columns named `source:series`.

    from rhengine.alpha.external import External
    ext = External.load()
    ext.wide[["osap:Mom12m", "jkp:ret_12_1", "aqr:QMJ_US"]].corr()

What it is for. Every claim in docs/RMX-RESULTS.md that survived came from an
external record (French 1963-2026), and every claim that died came from this
project's own 8-year panel. That is not a coincidence — a 4-5%/yr tracking error
needs decades. So the rule this module exists to enforce:

    screen a candidate signal against the published record FIRST; only spend
    panel time, and only spend holdout, on the ones that survive that screen.

Four screens are built in, each answering a question the local panel cannot:

    catalog()      does this series have a real full-sample record at all?
    decay()        does it still work in MY era and MY size bucket?
                   (Chen & Welch 2026 measured the median published anomaly at
                   7 bp/month post-2005 in the top 90% of market cap. This
                   reproduces that cut on any subset.)
    correlations() would it actually diversify what I already hold?
                   (the +0.18 that made RMX-B work is the whole edge)
    long_only()    would it work in a portfolio that CANNOT SHORT?

**Read that last one before using any of the others.** Every sleeve in this
project is long-only, cap-weighted, top quintile. A long-short spread can be
large because the short leg falls, and this account cannot collect that. The gap
is not a detail — measured on the four sleeves RMX-C actually runs:

                          long-short (ex-micro)     long-only top quintile
    1970-2024                    55 bp/mo t=7.7          20 bp/mo t=5.2
    2015-2024                    61 bp/mo t=4.1           2.6 bp/mo t=0.35

Selecting a sleeve on the long-short number is how a candidate whose entire
premium sits in the short leg gets shipped. That happened here: `EBM` was chosen
as a 5th sleeve on a long-short screen (+0.51 blend t) and its long-only quintile
turns out to be -2.6 bp/month since 2015. See docs/RMX-C.md.

Units are normalised on load: OSAP ships percent, JKP and AQR ship decimals,
French ships percent. Everything here is **decimal monthly**.

    py -m rhengine.alpha.external --catalog --min-t 2
    py -m rhengine.alpha.external --decay --top 30
    py -m rhengine.alpha.external --corr osap:Mom12m jkp:qmj aqr:BAB_US
"""
from __future__ import annotations

import argparse
import functools
import os
import zipfile
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .stats import _newey_west_ols

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXT = os.path.join(ROOT, "research_data", "external")

# The break Chen & Welch (arXiv 2607.06502) use: modern trading technology and
# post-publication decay both land in the early 2000s.
MODERN_BREAK = "2005-01-01"


def _require(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} missing — run `py scripts/fetch_external_factors.py`")
    return path


def _zip_csv(path: str, **kw) -> pd.DataFrame:
    """Read the single CSV member of a zip (member names contain brackets)."""
    with zipfile.ZipFile(_require(path)) as z:
        member = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        with z.open(member) as f:
            return pd.read_csv(f, **kw)


def nw_tstat(x: pd.Series, lags: int = 3) -> float:
    """Newey-West t-stat of the mean of a monthly series."""
    y = x.dropna().to_numpy(float)
    if len(y) < 24:
        return float("nan")
    coef, se = _newey_west_ols(y, np.ones((len(y), 1)), lags)
    return float(coef[0] / se[0]) if se[0] > 0 else float("nan")


# ============================================================== source loaders
def load_osap_long_short() -> pd.DataFrame:
    """OSAP: monthly long-short return per predictor, month-end index.

    `PredictorPortsFull.csv` holds every portfolio leg; `port == 'LS'` is the
    long-short. Returns are in percent in the file and decimal here.
    """
    path = _require(os.path.join(EXT, "osap", "PredictorPortsFull.csv"))
    df = pd.read_csv(path, usecols=["signalname", "port", "date", "ret"],
                     dtype={"port": "string"})
    df = df[df["port"] == "LS"]
    df["date"] = pd.to_datetime(df["date"])
    w = df.pivot_table(index="date", columns="signalname", values="ret")
    return (w / 100.0).sort_index()


def load_osap_quintiles_vw(signal: str | None = None) -> pd.DataFrame:
    """OSAP value-weighted quintile portfolios — the long-only view.

    This is the construction that matters here: RMX-B holds a cap-weighted top
    quintile long-only, so `port == '05'` (or '01' where the sign is negative)
    is its published analogue, not the long-short.
    """
    df = _zip_csv(os.path.join(EXT, "osap", "PredictorAltPorts_QuintilesVW.zip"),
                  usecols=["signalname", "port", "date", "ret"],
                  dtype={"port": "string"})
    if signal is not None:
        df = df[df["signalname"] == signal]
    df["date"] = pd.to_datetime(df["date"])
    df["ret"] = df["ret"] / 100.0
    return df.sort_values(["signalname", "port", "date"])


def load_osap_ex_micro() -> pd.DataFrame:
    """OSAP long-short with the ME > NYSE 20th percentile screen applied.

    The single most important cut for this project: it removes the micro caps
    where most published anomaly return lives and none of it is tradeable.
    Comparing this against `load_osap_long_short()` measures directly how much
    of a signal's record survives into an investable universe.
    """
    df = _zip_csv(os.path.join(EXT, "osap", "PredictorAltPorts_ex_micro.zip"),
                  usecols=["signalname", "port", "date", "ret"],
                  dtype={"port": "string"})
    df = df[df["port"] == "LS"]
    df["date"] = pd.to_datetime(df["date"])
    w = df.pivot_table(index="date", columns="signalname", values="ret")
    return (w / 100.0).sort_index()


def load_signal_doc() -> pd.DataFrame:
    """OSAP SignalDoc: paper, journal, original sample, sign, replication grade."""
    path = _require(os.path.join(EXT, "osap", "SignalDoc.csv"))
    doc = pd.read_csv(path, encoding="latin-1", low_memory=False)
    keep = {"Acronym": "signal", "Authors": "authors", "Year": "year",
            "Journal": "journal", "Cat.Economic": "theme",
            "Cat.Data": "data", "Cat.Signal": "kind",
            "Signal Rep Quality": "rep_quality", "Sign": "sign",
            "SampleStartYear": "sample_start", "SampleEndYear": "sample_end",
            "LongDescription": "description"}
    have = {k: v for k, v in keep.items() if k in doc.columns}
    return doc[list(have)].rename(columns=have).set_index("signal")


def load_jkp_factors(weighting: str = "vw_cap") -> pd.DataFrame:
    """JKP: 153 USA factors, monthly, already decimal and sign-aligned."""
    name = ("usa_all_factors_monthly_vw_cap.zip" if weighting == "vw_cap"
            else "usa_all_factors_monthly_ew.zip")
    df = _zip_csv(os.path.join(EXT, "jkp", name),
                  usecols=["name", "date", "ret"])
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index="date", columns="name", values="ret").sort_index()


def load_jkp_themes() -> pd.DataFrame:
    """JKP: the 13 theme aggregates — one series per cluster.

    These are the right menu for sleeve selection. Picking one factor from each
    of two different themes is a near-guarantee of low correlation; picking two
    factors from the same theme is a near-guarantee of the opposite.
    """
    df = _zip_csv(os.path.join(EXT, "jkp", "usa_all_themes_monthly_vw_cap.zip"),
                  usecols=["name", "date", "ret"])
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index="date", columns="name", values="ret").sort_index()


def _aqr_sheet(fname: str, sheet: str, header_probe: int = 40) -> pd.DataFrame:
    """Read one AQR sheet, finding the header row that starts the date column."""
    path = _require(os.path.join(EXT, "aqr", fname))
    raw = pd.read_excel(path, sheet_name=sheet, header=None, nrows=header_probe)
    hdr = None
    for i in range(len(raw)):
        first = str(raw.iloc[i, 0]).strip().upper()
        if first in {"DATE", "DATES", "MONTH"}:
            hdr = i
            break
        # AQR's TSMOM sheet leaves the date column unlabelled: the header row is
        # the one with several labels and a blank first cell, immediately above
        # the first parseable date.
        row = raw.iloc[i]
        if first in {"NAN", ""} and row.iloc[1:].notna().sum() >= 2 \
                and i + 1 < len(raw) \
                and pd.notna(pd.to_datetime(raw.iloc[i + 1, 0], errors="coerce")):
            hdr = i
            break
    if hdr is None:
        raise ValueError(f"{fname}:{sheet} — no header row in first "
                         f"{header_probe} rows")
    df = pd.read_excel(path, sheet_name=sheet, skiprows=hdr)
    df = df.rename(columns={df.columns[0]: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"]).set_index("date").apply(
        pd.to_numeric, errors="coerce")


AQR_SERIES = {
    # local name  -> (file, sheet, column)
    "QMJ_US": ("quality-minus-junk-factors-monthly.xlsx", "QMJ Factors", "USA"),
    "BAB_US": ("betting-against-beta-equity-factors-monthly.xlsx",
               "BAB Factors", "USA"),
    "HMLdevil_US": ("the-devil-in-hmls-details-factors-monthly.xlsx",
                    "HML Devil", "USA"),
    "TSMOM": ("time-series-momentum-factors-monthly.xlsx", "TSMOM Factors",
              "TSMOM"),
}


def load_aqr() -> pd.DataFrame:
    """AQR: QMJ, BAB, HML-Devil (US) and TSMOM, monthly decimal."""
    out = {}
    for label, (fname, sheet, col) in AQR_SERIES.items():
        try:
            df = _aqr_sheet(fname, sheet)
        except Exception as exc:                     # noqa: BLE001
            print(f"    aqr:{label} unavailable ({type(exc).__name__}: {exc})")
            continue
        hit = [c for c in df.columns if str(c).strip().upper() == col.upper()]
        if not hit:
            # AQR relabels sheets between vintages; fall back to a contains match
            hit = [c for c in df.columns if col.upper() in str(c).upper()]
        if hit:
            out[label] = df[hit[0]].dropna()
        else:
            print(f"    aqr:{label} column {col!r} not in {list(df.columns)[:8]}")
    return pd.DataFrame(out).sort_index()


def load_french_monthly() -> pd.DataFrame:
    """French: FF5 + momentum + short/long-term reversal, monthly decimal."""
    files = {"F-F_Research_Data_5_Factors_2x3.zip": None,
             "F-F_Momentum_Factor.zip": None,
             "F-F_ST_Reversal_Factor.zip": None,
             "F-F_LT_Reversal_Factor.zip": None}
    frames = []
    for fname in files:
        path = os.path.join(EXT, "french", fname)
        if not os.path.exists(path):
            continue
        with zipfile.ZipFile(path) as z:
            member = z.namelist()[0]
            text = z.read(member).decode("latin-1").splitlines()
        recs = []
        header = None
        prev_line = ""
        for ln in text:
            parts = [p.strip() for p in ln.split(",")]
            # monthly rows are YYYYMM; the annual block that follows is YYYY
            is_data = (len(parts) >= 2 and len(parts[0]) == 6
                       and parts[0].isdigit())
            if not is_data:
                if recs:                     # monthly block finished
                    break
                prev_line = ln               # remember the last non-data line
                continue
            try:
                row = [pd.Period(parts[0], freq="M").to_timestamp("M")] \
                    + [float(x) for x in parts[1:]]
            except ValueError:
                continue
            if not recs:
                # The header is the line IMMEDIATELY above the first data row.
                # Searching the whole preamble for "a line with the right number
                # of commas" picks up French's prose — that is how the momentum
                # factor once got named "constructed from six value-weight
                # portfolios formed".
                hp = [p.strip() for p in prev_line.split(",")]
                if len(hp) == len(row) and hp[0] == "":
                    header = ["date"] + hp[1:]
                elif len(hp) == len(row) - 1:
                    header = ["date"] + hp
            recs.append(row)
        if not recs:
            continue
        if header is None or any(not h for h in header):
            stem = fname.replace(".zip", "").replace("F-F_", "")
            header = ["date"] + [f"{stem}_{i}" for i in range(len(recs[0]) - 1)]
        f = pd.DataFrame(recs, columns=header).set_index("date")
        frames.append(f / 100.0)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    return out.loc[:, ~out.columns.duplicated()].sort_index()


# ===================================================================== the panel
@dataclass
class External:
    wide: pd.DataFrame                  # month-end x "source:series", decimal
    doc: pd.DataFrame                   # OSAP metadata, indexed by signal
    ex_micro: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    @classmethod
    @functools.lru_cache(maxsize=1)
    def load(cls) -> "External":
        parts = {}
        loaders = {"osap": load_osap_long_short, "jkp": load_jkp_factors,
                   "aqr": load_aqr, "french": load_french_monthly}
        for src, fn in loaders.items():
            try:
                df = fn()
            except FileNotFoundError as exc:
                print(f"  {src}: {exc}")
                continue
            if df.empty:
                continue
            df.columns = [f"{src}:{c}" for c in df.columns]
            df.index = pd.DatetimeIndex(df.index).to_period("M").to_timestamp("M")
            parts[src] = df[~df.index.duplicated(keep="last")]
        if not parts:
            raise FileNotFoundError(
                "no external data on disk — run scripts/fetch_external_factors.py")
        try:
            themes = load_jkp_themes()
            themes.columns = [f"jkptheme:{c}" for c in themes.columns]
            themes.index = pd.DatetimeIndex(
                themes.index).to_period("M").to_timestamp("M")
            parts["jkptheme"] = themes
        except FileNotFoundError:
            pass
        wide = pd.concat(parts.values(), axis=1).sort_index()
        try:
            doc = load_signal_doc()
        except FileNotFoundError:
            doc = pd.DataFrame()
        try:
            xm = load_osap_ex_micro()
        except FileNotFoundError:
            xm = pd.DataFrame()
        return cls(wide=wide, doc=doc, ex_micro=xm)

    # -------------------------------------------------------------- screens
    def window(self, lo: str | None = None, hi: str | None = None) -> pd.DataFrame:
        w = self.wide
        if lo:
            w = w[w.index >= pd.Timestamp(lo)]
        if hi:
            w = w[w.index <= pd.Timestamp(hi)]
        return w

    def catalog(self, lo: str | None = None, hi: str | None = None,
                min_months: int = 60) -> pd.DataFrame:
        """One row per series: span, annualised mean/vol, Sharpe, NW t-stat."""
        w = self.window(lo, hi)
        rows = []
        for col in w.columns:
            s = w[col].dropna()
            if len(s) < min_months:
                continue
            src, name = col.split(":", 1)
            mu, sd = s.mean(), s.std()
            rows.append({
                "series": col, "source": src, "name": name,
                "start": s.index[0].date(), "end": s.index[-1].date(),
                "months": len(s),
                "ret_ann_pct": mu * 12 * 100,
                "vol_ann_pct": sd * np.sqrt(12) * 100,
                "sharpe": (mu / sd * np.sqrt(12)) if sd > 0 else np.nan,
                "t_nw": nw_tstat(s),
                "bp_month": mu * 1e4,
            })
        cat = pd.DataFrame(rows).set_index("series")
        if not self.doc.empty:
            meta = self.doc.reindex(cat["name"])
            for c in ("authors", "year", "journal", "theme", "rep_quality"):
                if c in meta.columns:
                    cat[c] = meta[c].to_numpy()
        return cat.sort_values("t_nw", ascending=False)

    def decay(self, break_date: str = MODERN_BREAK, recent: str = "2015-01-01",
              min_months: int = 60) -> pd.DataFrame:
        """The Chen & Welch cut, per series, in basis points per month.

        Columns `bp_pre` / `bp_post` / `bp_recent` are the same series' mean
        monthly return before the modern break, after it, and over the recent
        window. `survives` is the honest filter: still positive and t > 1 in the
        recent window, having also been positive before.
        """
        rows = []
        for col in self.wide.columns:
            s = self.wide[col].dropna()
            if len(s) < min_months:
                continue
            pre = s[s.index < pd.Timestamp(break_date)]
            post = s[s.index >= pd.Timestamp(break_date)]
            rec = s[s.index >= pd.Timestamp(recent)]
            rows.append({
                "series": col,
                "bp_pre": pre.mean() * 1e4 if len(pre) >= 24 else np.nan,
                "bp_post": post.mean() * 1e4 if len(post) >= 24 else np.nan,
                "bp_recent": rec.mean() * 1e4 if len(rec) >= 24 else np.nan,
                "t_pre": nw_tstat(pre), "t_post": nw_tstat(post),
                "t_recent": nw_tstat(rec),
                "n_pre": len(pre), "n_post": len(post), "n_recent": len(rec),
            })
        d = pd.DataFrame(rows).set_index("series")
        d["retention"] = d["bp_post"] / d["bp_pre"].where(d["bp_pre"] > 0)
        d["survives"] = ((d["bp_pre"] > 0) & (d["bp_recent"] > 0)
                         & (d["t_recent"] > 1.0))
        return d.sort_values("t_recent", ascending=False)

    def micro_dependence(self, min_months: int = 60) -> pd.DataFrame:
        """How much of each OSAP signal's record survives excluding micro caps.

        `ratio` below ~0.5 means the published number is a micro-cap number and
        this project — large-cap only, no credible micro-cap cost model — cannot
        have it at any price.
        """
        if self.ex_micro.empty:
            raise FileNotFoundError("OSAP ex-micro file not harvested")
        full = load_osap_long_short()
        rows = []
        for name in self.ex_micro.columns.intersection(full.columns):
            a = full[name].dropna()
            b = self.ex_micro[name].dropna()
            idx = a.index.intersection(b.index)
            if len(idx) < min_months:
                continue
            ma, mb = a.loc[idx].mean(), b.loc[idx].mean()
            rows.append({"signal": name, "bp_all": ma * 1e4,
                         "bp_ex_micro": mb * 1e4,
                         "ratio": mb / ma if ma > 0 else np.nan,
                         "t_all": nw_tstat(a.loc[idx]),
                         "t_ex_micro": nw_tstat(b.loc[idx]),
                         "months": len(idx)})
        return pd.DataFrame(rows).set_index("signal").sort_values(
            "t_ex_micro", ascending=False)

    # ------------------------------------------------- the long-only screen
    @functools.cached_property
    def _quintiles(self) -> dict[str, pd.DataFrame]:
        """Per signal, a month-end x port frame of VW quintile returns."""
        q = load_osap_quintiles_vw()
        q = q[q["port"] != "LS"].copy()
        q["date"] = pd.DatetimeIndex(q["date"]).to_period("M").to_timestamp("M")
        return {s: g.pivot_table(index="date", columns="port", values="ret")
                for s, g in q.groupby("signalname")}

    @functools.cached_property
    def market(self) -> pd.Series:
        """French's value-weighted market TOTAL return, monthly decimal."""
        return (self.wide["french:Mkt-RF"] + self.wide["french:RF"]).dropna()

    def long_only(self, signal: str, excess: bool = True,
                  min_months: int = 120) -> pd.Series:
        """One signal's top-quintile long-only leg, as excess over the market.

        This is the analogue of what the sleeves actually hold — see the module
        docstring for why using the long-short instead is a live foot-gun.

        Two conventions had to be established from the data rather than assumed:

        1. **Port 05 is always the long side**, for every signal, regardless of
           the `Sign` column in SignalDoc. Verified on the sign = -1 signals:
           Size, Accruals, AssetGrowth and ShareIss5Y all come out monotone
           INCREASING in port number, i.e. the predicted winners sit in port 05.
           A `sign`-conditional lookup would silently return the SHORT leg for
           every negative-sign predictor.
        2. **Excess over the market, not the raw return.** A long-only sleeve has
           to beat the index, not merely be positive; every quintile of every
           signal was positive over 1970-2024.

        The ex-micro file is deliberately not used here: its port labels are not
        consistent across signals (for GP, Mom12m and AssetGrowth simultaneously,
        neither p10-p01 nor p01-p10 reproduces the `LS` series in that same
        file), so any long-only leg read out of it would be a guess. Value
        weighting inside each quintile already keeps micro caps to a small share.

        Returns an empty series when the signal has no five-port record, when it
        is too short, or when its quintile means are not monotone increasing —
        the monotonicity check is the guard that catches a convention change
        rather than silently inverting a portfolio.
        """
        w = self._quintiles.get(signal)
        if w is None or w.shape[1] != 5:
            return pd.Series(dtype=float)
        full = w.dropna()
        if len(full) < min_months:
            return pd.Series(dtype=float)
        means = full.mean().to_numpy()
        ranks = pd.Series(means).rank().to_numpy()
        rho = float(np.corrcoef(np.arange(5), ranks)[0, 1])
        if not np.isfinite(rho) or rho < 0.5:
            return pd.Series(dtype=float)
        leg = w[sorted(w.columns)[-1]].dropna()
        if not excess:
            return leg
        return (leg - self.market.reindex(leg.index)).dropna()

    def long_only_blend(self, signals: list[str], lo: str | None = None,
                        hi: str | None = None) -> dict:
        """`blend_gain`, but on long-only top-quintile excess returns.

        Use this — not `blend_gain` — to decide whether to add a sleeve.
        """
        legs = {s: self.long_only(s) for s in signals}
        missing = [s for s, v in legs.items() if v.empty]
        if missing:
            raise KeyError(f"no usable long-only quintile leg for {missing}")
        f = pd.DataFrame(legs).dropna()
        if lo:
            f = f[f.index >= pd.Timestamp(lo)]
        if hi:
            f = f[f.index <= pd.Timestamp(hi)]
        if len(f) < 24:
            raise ValueError(f"only {len(f)} overlapping months")
        b = f.mean(axis=1)
        k = f.shape[1]
        c = f.corr().to_numpy()
        return {
            "months": len(f), "start": str(f.index[0].date()),
            "end": str(f.index[-1].date()),
            "legs": {s: {"bp_month": f[s].mean() * 1e4, "t_nw": nw_tstat(f[s])}
                     for s in f.columns},
            "mean_leg_t": float(np.mean([nw_tstat(f[s]) for s in f.columns])),
            "mean_pairwise_corr": float((c.sum() - k) / (k * (k - 1)))
            if k > 1 else 1.0,
            "blend_bp_month": float(b.mean() * 1e4),
            "blend_t_nw": nw_tstat(b),
            "blend_sharpe": float(b.mean() / b.std() * np.sqrt(12)),
        }

    def correlations(self, series: list[str], lo: str | None = None,
                     hi: str | None = None) -> pd.DataFrame:
        w = self.window(lo, hi)
        missing = [s for s in series if s not in w.columns]
        if missing:
            raise KeyError(f"unknown series {missing}; try --catalog to list")
        return w[series].dropna().corr()

    def blend_gain(self, series: list[str], lo: str | None = None,
                   hi: str | None = None, ex_micro: bool = False) -> dict:
        """What an equal-weight blend of `series` buys over its average leg.

        This is the arithmetic that made RMX-B work, made explicit: alphas
        average, tracking errors add in quadrature, so the blend's t-stat beats
        the mean leg t by roughly sqrt(k / (1 + (k-1) * rho_bar)).

        `ex_micro=True` re-runs it on OSAP's ME > NYSE-20th-percentile series
        instead. Always look at both: the all-stocks number is the published
        headline and the ex-micro number is the one an S&P 500 account could
        conceivably have. Neither is net of cost.
        """
        if ex_micro:
            if self.ex_micro.empty:
                raise FileNotFoundError("OSAP ex-micro file not harvested")
            names = [s.split(":", 1)[1] for s in series]
            unknown = [n for n in names if n not in self.ex_micro.columns]
            if unknown:
                raise KeyError(f"no ex-micro series for {unknown}")
            frame = self.ex_micro[names]
            frame.columns = series
            w = frame
            if lo:
                w = w[w.index >= pd.Timestamp(lo)]
            if hi:
                w = w[w.index <= pd.Timestamp(hi)]
            w = w.dropna()
        else:
            w = self.window(lo, hi)[series].dropna()
        if len(w) < 24:
            raise ValueError(f"only {len(w)} overlapping months")
        legs = {c: {"bp_month": w[c].mean() * 1e4, "t_nw": nw_tstat(w[c]),
                    "sharpe": w[c].mean() / w[c].std() * np.sqrt(12)}
                for c in series}
        b = w.mean(axis=1)
        k = len(series)
        rho = w.corr().to_numpy()
        rho_bar = float((rho.sum() - k) / (k * (k - 1))) if k > 1 else 1.0
        return {
            "months": len(w),
            "start": str(w.index[0].date()), "end": str(w.index[-1].date()),
            "legs": legs,
            "mean_leg_t": float(np.mean([v["t_nw"] for v in legs.values()])),
            "mean_pairwise_corr": rho_bar,
            "blend_bp_month": float(b.mean() * 1e4),
            "blend_t_nw": nw_tstat(b),
            "blend_sharpe": float(b.mean() / b.std() * np.sqrt(12)),
            "theoretical_t_multiple": float(
                np.sqrt(k / (1 + (k - 1) * rho_bar))) if k > 1 else 1.0,
        }


# ========================================================================= CLI
def _fmt(df: pd.DataFrame, cols: list[str], n: int) -> str:
    show = [c for c in cols if c in df.columns]
    return df[show].head(n).to_string(float_format=lambda v: f"{v:,.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--decay", action="store_true")
    ap.add_argument("--micro", action="store_true",
                    help="how much of each OSAP signal needs micro caps")
    ap.add_argument("--corr", nargs="*", help="series names to correlate")
    ap.add_argument("--blend", nargs="*", help="series to equal-weight blend")
    ap.add_argument("--ex-micro", action="store_true",
                    help="run --blend on the ME > NYSE 20th pct screen instead")
    ap.add_argument("--long-only", nargs="*", metavar="SIGNAL",
                    help="OSAP signal names (no source prefix) to screen as "
                         "LONG-ONLY top-quintile legs — the construction the "
                         "sleeves actually have. Use this to add a sleeve.")
    ap.add_argument("--vs", nargs="*", default=None, metavar="SIGNAL",
                    help="with --long-only: treat these as the sleeves already "
                         "held and report what each candidate ADDS")
    ap.add_argument("--source", help="filter to one source prefix")
    ap.add_argument("--lo"), ap.add_argument("--hi")
    ap.add_argument("--min-t", type=float, default=None)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    ext = External.load()
    print(f"external panel: {ext.wide.shape[1]} series, "
          f"{ext.wide.index[0].date()} .. {ext.wide.index[-1].date()}")
    by_src = pd.Series([c.split(":")[0] for c in ext.wide.columns]).value_counts()
    print("  " + "  ".join(f"{k}={v}" for k, v in by_src.items()))

    if args.catalog:
        cat = ext.catalog(args.lo, args.hi)
        if args.source:
            cat = cat[cat["source"] == args.source]
        if args.min_t is not None:
            cat = cat[cat["t_nw"] >= args.min_t]
        print(f"\n=== catalog ({len(cat)} series) "
              f"{args.lo or 'start'} .. {args.hi or 'end'} ===")
        print(_fmt(cat, ["months", "ret_ann_pct", "vol_ann_pct", "sharpe",
                         "t_nw", "bp_month", "authors", "year", "theme"],
                   args.top))

    if args.decay:
        d = ext.decay()
        print(f"\n=== decay: pre-2005 vs post-2005 vs post-2015, bp/month ===")
        surv = d[d["survives"]]
        print(f"{len(surv)} of {len(d)} series still positive with t>1 since 2015")
        print(_fmt(d, ["bp_pre", "bp_post", "bp_recent", "t_pre", "t_post",
                       "t_recent", "retention"], args.top))
        print("\nmedian bp/month across all series:")
        print(d[["bp_pre", "bp_post", "bp_recent"]].median().round(2).to_string())

    if args.micro:
        m = ext.micro_dependence()
        print(f"\n=== micro-cap dependence ({len(m)} OSAP signals) ===")
        print(_fmt(m, ["bp_all", "bp_ex_micro", "ratio", "t_all", "t_ex_micro",
                       "months"], args.top))
        print(f"\nmedian ratio ex-micro/all: {m['ratio'].median():.2f}")

    if args.corr:
        print("\n=== correlations ===")
        print(ext.correlations(args.corr, args.lo, args.hi).round(3).to_string())

    if args.long_only:
        windows = (("1970-01-01", "2024-12-31"), ("2010-07-01", "2018-12-31"),
                   ("2015-01-01", "2024-12-31"))
        held = list(args.vs or [])
        print("\n=== long-only VW top quintile, EXCESS OVER MARKET, bp/mo ===")
        print(f"{'signal':22} " + "  ".join(
            f"{a[:4]}-{b[:4]:>10}" for a, b in windows))
        usable = []
        for s in held + [c for c in args.long_only if c not in held]:
            x = ext.long_only(s)
            if x.empty:
                print(f"{s:22} no usable five-port record / not monotone")
                continue
            usable.append(s)
            cells = []
            for a, b in windows:
                y = x[(x.index >= a) & (x.index <= b)]
                cells.append(f"{y.mean() * 1e4:7.1f}/{nw_tstat(y):5.2f}"
                             if len(y) >= 24 else "")
            print(f"{s:22} " + "  ".join(f"{c:>15}" for c in cells)
                  + ("  <-- held" if s in held else ""))

        if held:
            cands = [c for c in usable if c not in held]
            for a, b in windows:
                try:
                    base = ext.long_only_blend(held, a, b)
                except (KeyError, ValueError) as exc:
                    print(f"\n{a}..{b}: {exc}")
                    continue
                print(f"\n--- add a 5th block, {a[:7]}..{b[:7]} — base "
                      f"{base['blend_bp_month']:.1f}bp t={base['blend_t_nw']:.2f} "
                      f"SR={base['blend_sharpe']:.2f} "
                      f"rho={base['mean_pairwise_corr']:+.3f} n={base['months']}")
                rows = []
                for c in cands:
                    try:
                        g = ext.long_only_blend(held + [c], a, b)
                    except (KeyError, ValueError):
                        continue
                    rows.append({"cand": c,
                                 "solo_bp": g["legs"][c]["bp_month"],
                                 "solo_t": g["legs"][c]["t_nw"],
                                 "blend_t": g["blend_t_nw"],
                                 "d_t": g["blend_t_nw"] - base["blend_t_nw"],
                                 "d_SR": g["blend_sharpe"] - base["blend_sharpe"],
                                 "rho": g["mean_pairwise_corr"]})
                if rows:
                    print(pd.DataFrame(rows).set_index("cand")
                          .sort_values("d_t", ascending=False)
                          .to_string(float_format=lambda v: f"{v:,.2f}"))
            print("\nA candidate must help in EVERY window to be worth building."
                  "\nAfter screening this many signals, |d_t| below ~0.5 is "
                  "selection, not evidence.")

    if args.blend:
        import json
        print("\n=== blend gain ===")
        print(json.dumps(ext.blend_gain(args.blend, args.lo, args.hi,
                                        ex_micro=args.ex_micro),
                         indent=2, default=float))


if __name__ == "__main__":
    main()
