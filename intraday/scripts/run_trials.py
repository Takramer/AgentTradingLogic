"""Run the pre-registered trials in intraday/docs/RESULTS.md on development data.

    python intraday/scripts/run_trials.py T1 T2 ...

Each trial must already be logged (validate refuses otherwise). Prints the full
report per trial and appends a one-line JSON summary to
intraday/data/results.jsonl. Never touches the holdout window.
"""
import json
import os
import sys
import time
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import pandas as pd  # noqa: E402

from intraday import data, instruments as I, strategies as St, validate as V  # noqa: E402

HOLDOUT_START = "2025-01-01"
CAPITAL = 10_000.0

bars = pd.read_pickle("intraday/data/SPY_rth.pkl")
dev = bars[bars.index < pd.Timestamp(HOLDOUT_START, tz=data.TZ)]
sessions = pd.Index(sorted(set(data.session_dates(dev.index))))
vix = data.load_daily("intraday/data/VIX_daily.csv")
high_vix = St.vix_above_median(vix, sessions)

MES = dict(instrument=I.MES_ON_SPY, qty=50.0)
RH = dict(instrument=I.SPY_RH, notional=CAPITAL)
MES_LONG = dict(instrument=replace(I.MES_ON_SPY, long_only=True), qty=50.0)
T9_GRID = {"lookback": [10, 14, 20], "vm": [0.8, 1.0, 1.2]}

TRIALS = {
    "T1": ("T1 intraday momentum ROD 15:30 -> close, MES~SPY", St.IntradayMomentum(), MES),
    "T2": ("T2 intraday momentum ROD 15:30 -> close, SPY_RH long-only", St.IntradayMomentum(), RH),
    "T3": ("T3 intraday momentum Gao 10:00 ref, 15:30 -> close, MES~SPY",
           St.IntradayMomentum(ref="10:00"), MES),
    "T4": ("T4 noise area (Zarattini 2024 published), MES~SPY", St.NoiseArea(), MES),
    "T5": ("T5 noise area (Zarattini 2024 published), SPY_RH long-only", St.NoiseArea(), RH),
    "T6": ("T6 T1 rule on high-VIX days (prior VIX > 252d median), MES~SPY",
           St.DayFilter(St.IntradayMomentum(), high_vix, "prior VIX > 252d median"), MES),
    "T7": ("T7 noise area + prior VIX > 252d median, SPY_RH long-only",
           St.DayFilter(St.NoiseArea(), high_vix, "prior VIX > 252d median"), RH),
    "T8": ("T8 noise area long-only, MES~SPY", St.NoiseArea(), MES_LONG),
    "T9": ("T9 noise area plateau grid lookback x vm, SPY_RH long-only", St.NoiseArea(),
           dict(RH, grid=T9_GRID)),
    "T10": ("T10 noise area, VWAP-only trailing stop, SPY_RH long-only",
            St.NoiseArea(exit_rule="vwap"), RH),
    "T11": ("T11 noise area, band+VWAP stop checked every minute, SPY_RH long-only",
            St.NoiseArea(stop_every=1), RH),
    "T12": ("T12 T10 plateau grid lookback x vm, SPY_RH long-only",
            St.NoiseArea(exit_rule="vwap"), dict(RH, grid=T9_GRID)),
}


def run(key: str) -> None:
    label, strat, venue = TRIALS[key]
    t0 = time.time()
    kw = dict(venue)
    inst = kw.pop("instrument")
    rep = V.evaluate(strat, dev, inst, trial=label, survivorship="single_instrument",
                     capital=CAPITAL, holdout_start=HOLDOUT_START, n_paths=500, **kw)
    print("=" * 100)
    V.print_report(rep)
    st = rep["stats"]
    line = {"run_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key, "trial": label, "verdict": rep["verdict"],
            "net_usd": rep["summary"]["net_usd"], "trades": rep["summary"]["trades"],
            "t_nw": st["t_nw"], "sharpe": st["sharpe"], "sharpe_ci": st["sharpe_ci"],
            "dsr": st["deflated_sharpe"], "null_p": rep["null"]["p"],
            "drift_corr": rep["drift"]["corr"],
            "fails": [c["check"] for c in rep["checks"] if c["status"] == "FAIL"],
            "n_trials": V.T.count(V.FAMILY, path=V.LEDGER),
            "secs": round(time.time() - t0, 1)}
    with open("intraday/data/results.jsonl", "a") as fh:
        fh.write(json.dumps(line, default=float) + "\n")
    print(f"  ({line['secs']}s)", flush=True)


if __name__ == "__main__":
    for k in sys.argv[1:] or list(TRIALS):
        run(k)
