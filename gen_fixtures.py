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


def build_adapter_cases():
    """Golden vectors for the Kalshi/Polymarket adapter parsers.

    The raw payloads are hand-frozen API-shaped samples (docs.kalshi.com /
    docs.polymarket.com, 2026-07). If either venue changes schema, updating
    a raw here is a DELIBERATE act reviewed via the fixture diff — same
    contract as the engine golden vectors."""
    from rhengine.market_data_kalshi import (
        parse_exchange_status, parse_candlesticks, score_prob_shift,
    )
    from rhengine.market_data_polymarket import (
        parse_gamma_market, parse_prices_history, score_implied_prob,
    )

    t0 = 1_700_000_000  # seconds — adapter payloads use epoch seconds

    status_open = {"exchange_active": True, "trading_active": True,
                   "intra_exchange_transfers_active": True,
                   "exchange_estimated_resume_time": None}
    status_halted = {"exchange_active": True, "trading_active": False,
                     "intra_exchange_transfers_active": False,
                     "exchange_estimated_resume_time": "2026-07-20T08:00:00Z"}

    kalshi_candles = {"candlesticks": [
        # traded day, LIVE schema (verified 2026-07-20): *_dollars keys
        {"end_period_ts": t0,
         "price": {"open_dollars": "0.550000", "high_dollars": "0.610000",
                   "low_dollars": "0.540000", "close_dollars": "0.600000"},
         "yes_bid": {"close_dollars": "0.590000"},
         "yes_ask": {"close_dollars": "0.610000"},
         "volume_fp": "1250.00", "open_interest_fp": "2020.47"},
        # quote-only day, LIVE schema: no trade OHLC, only previous_dollars +
        # full bid/ask OHLC -> bid/ask midpoint bar with a real range
        {"end_period_ts": t0 + 86400, "price": {"previous_dollars": "0.6000"},
         "yes_bid": {"open_dollars": "0.5900", "high_dollars": "0.6100",
                     "low_dollars": "0.5800", "close_dollars": "0.6000"},
         "yes_ask": {"open_dollars": "0.6100", "high_dollars": "0.6500",
                     "low_dollars": "0.6000", "close_dollars": "0.6400"},
         "volume_fp": "0.00"},
        # dead day: previous trade price only -> flat carried-forward bar
        {"end_period_ts": t0 + 86400 * 2, "price": {"previous_dollars": "0.6200"},
         "yes_bid": {}, "yes_ask": {}, "volume_fp": "0.00"},
        # docs/legacy schema: bare keys, integer cents
        {"end_period_ts": t0 + 3 * 86400,
         "price": {"open": 62, "high": 68, "low": 61, "close": 67},
         "volume": 800},
    ]}

    gamma_market = {"id": 501234, "question": "Will the Fed cut rates in September?",
                    "slug": "fed-cut-september-2026",
                    "outcomePrices": "[\"0.65\", \"0.35\"]",
                    "volumeNum": 150000.5, "liquidityNum": 50000,
                    "clobTokenIds": "[\"11111111\", \"22222222\"]",
                    "endDate": "2026-09-18T00:00:00Z",
                    "active": True, "closed": False}

    pm_history = {"history": [
        {"t": t0 + 3600, "p": 0.40}, {"t": t0 + 40000, "p": 0.45},
        {"t": t0 + 80000, "p": 0.43},
        {"t": t0 + 86400 + 3600, "p": 0.47}, {"t": t0 + 86400 + 50000, "p": 0.52},
        {"t": t0 + 2 * 86400 + 3600, "p": 0.55},
    ]}

    kalshi_bars = parse_candlesticks(kalshi_candles)
    pm_bars = parse_prices_history(pm_history)
    status_exp_open = parse_exchange_status(status_open)
    status_exp_halted = parse_exchange_status(status_halted)
    for s in (status_exp_open, status_exp_halted):
        s.pop("checked_at")  # wall-clock — excluded from the golden contract

    from rhengine.market_data_fred import parse_observations, macro_flags
    from rhengine.market_data_av import parse_earnings, sue_gate

    fred_obs = {"observations": [
        {"date": "2026-07-15", "value": "0.42"},
        {"date": "2026-07-16", "value": "."},        # FRED missing-data marker
        {"date": "2026-07-17", "value": "-0.10"},
    ]}
    fred_parsed = parse_observations(fred_obs)
    fred_snap = {"T10Y2Y": {"time": 0, "value": -0.10},
                 "BAMLH0A0HYM2": {"time": 0, "value": 3.2},
                 "VIXCLS": {"time": 0, "value": 31.0}}

    av_earnings = {"symbol": "TEST", "quarterlyEarnings": [
        {"fiscalDateEnding": "2026-03-31", "reportedDate": "2026-04-30",
         "reportedEPS": "2.01", "estimatedEPS": "1.94",
         "surprise": "0.07", "surprisePercentage": "3.6082"},
        {"fiscalDateEnding": "2025-12-31", "reportedDate": "2026-01-28",
         "reportedEPS": "1.50", "estimatedEPS": "1.72",
         "surprise": "-0.22", "surprisePercentage": "-12.7907"},
        {"fiscalDateEnding": "2025-09-30", "reportedDate": None,  # dropped
         "reportedEPS": "1.10", "estimatedEPS": "None"},
    ]}
    av_events = parse_earnings(av_earnings)
    # now = 5 days after the 2026-01-28 miss — inside the 1-10d drift window
    av_now_ms = av_events[0]["time"] + 5 * 86_400_000

    return {
        "_note": "Adapter golden vectors: raw API-shaped payloads + expected "
                 "parser/leg output. Regenerate ONLY with gen_fixtures.py "
                 "after a deliberate adapter change.",
        "fred_observations": {"raw": fred_obs, "expected": fred_parsed},
        "fred_flags": {"input_snap": fred_snap,
                       "expected": macro_flags(fred_snap)},
        "av_earnings": {"raw": av_earnings, "expected_events": av_events},
        "av_sue_gate": {"input_events": av_events, "now_ms": av_now_ms,
                        "expected": sue_gate(av_events, av_now_ms,
                                             min_surprise_pct=5.0),
                        "min_surprise_pct": 5.0},
        "kalshi_status": {"raw_open": status_open, "expected_open": status_exp_open,
                          "raw_halted": status_halted, "expected_halted": status_exp_halted},
        "kalshi_candlesticks": {"raw": kalshi_candles, "expected_bars": kalshi_bars},
        "kalshi_prob_shift": {
            "input_bars": kalshi_bars,
            "expected": score_prob_shift(kalshi_bars, window=2, min_shift=0.05),
            "window": 2, "min_shift": 0.05},
        "polymarket_gamma": {"raw": gamma_market,
                             "expected": parse_gamma_market(gamma_market)},
        "polymarket_history": {"raw": pm_history, "expected_bars": pm_bars},
        "polymarket_implied_prob": {
            "input_bars": pm_bars,
            "expected": score_implied_prob(pm_bars, min_prob=0.1, max_prob=0.9,
                                           window=2, min_shift=0.05),
            "min_prob": 0.1, "max_prob": 0.9, "window": 2, "min_shift": 0.05},
    }


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
    with open("fixtures/adapters.json", "w") as f:
        json.dump(build_adapter_cases(), f, indent=1)
    n_setups = sum(1 for c in golden["cases"].values()
                   for s in c["strategies"].values() if s)
    print(f"Wrote fixtures/bars.json + fixtures/golden.json "
          f"({len(cases)} bar series, {n_setups} non-empty strategy outputs)")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
