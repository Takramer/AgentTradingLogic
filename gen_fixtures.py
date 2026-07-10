"""Regenerate golden-vector fixtures from the Python reference engine.

Deterministic synthetic bar series (seeded) are scored by every strategy with
the canonical spec defaults; the (input, output) pairs are the drift guard for
any other engine implementation (Nydex's indicators.js tests against the same
files).

Usage: python3 gen_fixtures.py   (from the repo root; rewrites fixtures/golden.json)
"""

import json
import os
import random

from rhengine.indicators import (
    score_ticker, classify_spy_regime, estimate_vix_regime, spec_defaults,
)

DAY_MS = 86_400_000
T0 = 1_700_000_000_000  # fixed epoch anchor — fixtures must be time-independent


def _bars(closes, vol_spikes=None, seed=1):
    """Bars from a close series: OHLC derived deterministically, volume 1M
    with optional {index: mult} spikes."""
    rng = random.Random(seed)
    vol_spikes = vol_spikes or {}
    bars = []
    for i, c in enumerate(closes):
        spread = abs(c) * 0.01 * (0.5 + rng.random())
        o = closes[i - 1] if i else c
        bars.append({
            "time": T0 + i * DAY_MS,
            "open": round(o, 4),
            "high": round(max(o, c) + spread, 4),
            "low": round(min(o, c) - spread, 4),
            "close": round(c, 4),
            "volume": int(1_000_000 * vol_spikes.get(i, 1.0)),
        })
    return bars


def _walk(n, start, drift, wobble, seed):
    rng = random.Random(seed)
    closes, c = [], start
    for _ in range(n):
        c *= 1 + drift + wobble * (rng.random() - 0.5)
        closes.append(c)
    return closes


def build_cases():
    cases = {}

    # Steady uptrend with a volume-confirmed final leg — feeds trend strategies.
    up = _walk(80, 100.0, 0.004, 0.01, seed=7)
    cases["uptrend"] = _bars(up, vol_spikes={78: 1.6, 79: 1.8}, seed=7)

    # Downtrend mirror.
    down = _walk(80, 100.0, -0.004, 0.01, seed=11)
    cases["downtrend"] = _bars(down, vol_spikes={79: 1.7}, seed=11)

    # Choppy range around 100 — bollinger territory; final bars tag and reclaim.
    rng = random.Random(13)
    chop = [100 + 4 * (rng.random() - 0.5) for _ in range(78)] + [94.5, 97.5]
    cases["choppy_reversal"] = _bars(chop, vol_spikes={79: 1.9}, seed=13)

    # Tight squeeze then breakout on volume — consolidation.
    squeeze = _walk(70, 100.0, 0.0, 0.004, seed=17) + [100.2, 100.1, 100.3, 100.2,
                                                        100.4, 100.3, 100.2, 100.3, 106.0]
    cases["squeeze_breakout"] = _bars(squeeze, vol_spikes={78: 2.2}, seed=17)

    # Earnings-like gap 5 sessions ago, drifting up — PEAD.
    pre = _walk(70, 50.0, 0.001, 0.008, seed=19)
    gap = pre[-1] * 1.09
    post = [gap * (1 + 0.005 * k) for k in range(6)]
    cases["earnings_gap_drift"] = _bars(pre + [gap] + post, vol_spikes={70: 3.5}, seed=19)

    # SPY proxy series for regime/VIX vectors and residual momentum.
    cases["spy_uptrend"] = _bars(_walk(80, 400.0, 0.002, 0.006, seed=23), seed=23)
    return cases


STRATEGIES = ["vwap", "bollinger", "macd", "consolidation", "residualmomentum", "pead"]


def main():
    cases = build_cases()
    spy = cases["spy_uptrend"]
    golden = {"_note": "Golden vectors: score_ticker(bars, strategy, spec defaults). "
                       "Regenerate ONLY with gen_fixtures.py after a deliberate engine change.",
              "cases": {}}
    for name, bars in cases.items():
        out = {"regime": classify_spy_regime(bars), "vix_regime": estimate_vix_regime(bars),
               "strategies": {}}
        for strat in STRATEGIES:
            setups = score_ticker("TEST", bars, strat, spec_defaults(strat), spy_bars=spy)
            out["strategies"][strat] = setups
        golden["cases"][name] = out

    os.makedirs("fixtures", exist_ok=True)
    with open("fixtures/bars.json", "w") as f:
        json.dump(cases, f)
    with open("fixtures/golden.json", "w") as f:
        json.dump(golden, f, indent=1)
    n_setups = sum(1 for c in golden["cases"].values()
                   for s in c["strategies"].values() if s)
    print(f"Wrote fixtures/bars.json + fixtures/golden.json "
          f"({len(cases)} bar series, {n_setups} non-empty strategy outputs)")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
