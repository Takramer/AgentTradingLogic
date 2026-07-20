"""
market_data_fred.py — FRED macro-series adapter (portfolio-level overlay).

Same layering as the other adapters: fetch + cache, pure parsers, nothing
imported by indicators.py. FRED is macro state, not a per-setup signal — the
intended consumer is a portfolio-heat scaler (size down when macro is
hostile), not a new strategy leg.

  fetch_series()    — one FRED series → [{"time": ms, "value": float}],
                      disk-cached in fred_cache/ (fetched-today stamp,
                      offline replay + stale fallback like the other caches)
  macro_snapshot()  — latest value of each default series in one dict
  macro_flags()     — pure snapshot → flags (curve_inverted, credit_stress,
                      vix_elevated, risk_on). Thresholds are CONVENTIONAL
                      defaults, not backtested — tune before trusting.

Default series:
  T10Y2Y        10y-2y Treasury spread (daily) — curve inversion
  BAMLH0A0HYM2  ICE BofA US High Yield OAS (daily) — credit stress
  VIXCLS        CBOE VIX close (daily) — REAL VIX, so apps can gate on the
                actual index instead of indicators.estimate_vix_regime's
                SPY-derived proxy (the engine function stays untouched)

Key: secrets.json FRED_API_KEY (free, api.stlouisfed.org). Generous rate
limits (~120 req/min) — no pacing needed at this footprint.
"""

import gzip
import json
import os
import time
from datetime import datetime, timezone

import requests

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_SERIES = ["T10Y2Y", "BAMLH0A0HYM2", "VIXCLS"]

_CACHE_DIR = "fred_cache"


from ._paths import app_path as _app_path  # robust resolver (walks up to repo root)


def _fred_key():
    with open(_app_path("secrets.json")) as f:
        key = json.load(f).get("FRED_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("FRED_API_KEY missing from secrets.json")
    return key


def parse_observations(body):
    """Pure parser: FRED observations payload → ascending [{"time": ms,
    "value": float}] (fixture-tested). FRED encodes missing data as value
    "." — those rows are dropped."""
    out = []
    for o in body.get("observations", []):
        v = o.get("value")
        if v in (None, "", "."):
            continue
        try:
            d = datetime.fromisoformat(o["date"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"time": int(d.timestamp() * 1000), "value": float(v)})
    out.sort(key=lambda x: x["time"])
    return out


def fetch_series(series_id, lookback_days=400, offline=False):
    """One FRED series, disk-cached (data_local pattern: fetched-today
    stamp; offline=True replays any snapshot; network failure falls back
    to a stale snapshot)."""
    path = os.path.join(_CACHE_DIR, series_id + ".json.gz")
    blob = None
    if os.path.exists(path):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                blob = json.load(f)
        except Exception:
            blob = None
    today = datetime.now(timezone.utc).date().isoformat()
    start = datetime.now(timezone.utc).date().toordinal() - lookback_days
    start_iso = datetime.fromordinal(start).date().isoformat()
    # A snapshot only satisfies a request it fully covers — a cached 120d
    # window must not silently serve a 460d backtest (data_local period1 rule).
    covers = blob and blob.get("start_iso", "9999") <= start_iso
    if blob and (offline or (covers and blob.get("fetched") == today)):
        return blob["obs"]
    try:
        r = requests.get(FRED_BASE, params={
            "series_id": series_id, "api_key": _fred_key(), "file_type": "json",
            "observation_start": start_iso, "sort_order": "asc"}, timeout=20)
        r.raise_for_status()
        obs = parse_observations(r.json())
    except (requests.RequestException, ValueError) as e:
        if blob:
            return blob["obs"]
        raise ConnectionError("FRED fetch failed for {}: {}".format(series_id, e))
    os.makedirs(_CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"fetched": today, "start_iso": start_iso, "obs": obs},
                  f, separators=(",", ":"))
    os.replace(tmp, path)
    return obs


def macro_snapshot(series_ids=None, lookback_days=120, offline=False):
    """Latest observation of each series: {sid: {"time": ms, "value": float}}.
    Series that fail to fetch are simply absent — callers must treat a
    missing series as unknown, not as safe."""
    snap = {}
    for sid in (series_ids or DEFAULT_SERIES):
        try:
            obs = fetch_series(sid, lookback_days=lookback_days, offline=offline)
        except ConnectionError:
            continue
        if obs:
            snap[sid] = obs[-1]
    return snap


def macro_flags(snap, curve_min=0.0, hy_oas_max=4.5, vix_max=25.0):
    """Pure snapshot → flags (fixture-tested). Conventional thresholds —
    curve inverted below 0, HY OAS stressed above ~4.5%, VIX elevated above
    25. risk_on = no flag tripped AND all three series present. Intended as
    a portfolio-heat input (e.g. halve risk_pct when risk_on is False),
    applied app-side; nothing in the engine consumes this automatically."""
    def val(sid):
        return snap.get(sid, {}).get("value")

    curve, oas, vix = val("T10Y2Y"), val("BAMLH0A0HYM2"), val("VIXCLS")
    flags = {
        "curve_inverted": curve is not None and curve < curve_min,
        "credit_stress": oas is not None and oas > hy_oas_max,
        "vix_elevated": vix is not None and vix > vix_max,
    }
    complete = None not in (curve, oas, vix)
    flags["risk_on"] = complete and not any(
        flags[k] for k in ("curve_inverted", "credit_stress", "vix_elevated"))
    return flags


if __name__ == "__main__":
    # Smoke test — run: py -m rhengine.market_data_fred
    snap = macro_snapshot()
    for sid, o in snap.items():
        print("{}: {} ({})".format(
            sid, o["value"],
            datetime.fromtimestamp(o["time"] / 1000, timezone.utc).date()))
    print("flags:", macro_flags(snap))
