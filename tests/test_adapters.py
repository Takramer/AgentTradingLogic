"""Drift guard for the Kalshi/Polymarket adapters — no network.

fixtures/adapters.json holds hand-frozen API-shaped payloads plus the
expected parser/leg output. If a parser changes behavior, these fail until
the fixtures are deliberately regenerated (gen_fixtures.py) and the diff
reviewed — same contract as the engine golden vectors.
"""

import gzip
import json
import os
import tempfile
import unittest

from rhengine.market_data_kalshi import (
    parse_exchange_status, parse_candlesticks, score_prob_shift,
    fetch_prob_bars as kalshi_fetch_prob_bars,
)
from rhengine.market_data_polymarket import (
    parse_gamma_market, parse_prices_history, score_implied_prob,
    fetch_prob_bars as pm_fetch_prob_bars,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "adapters.json")


def _load():
    with open(FIXTURES) as f:
        return json.load(f)


class TestKalshiAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = _load()

    def test_status_parse_open(self):
        case = self.fx["kalshi_status"]
        got = parse_exchange_status(case["raw_open"])
        got.pop("checked_at")
        self.assertEqual(got, case["expected_open"])
        self.assertTrue(got["tradable"])

    def test_status_parse_halted(self):
        case = self.fx["kalshi_status"]
        got = parse_exchange_status(case["raw_halted"])
        got.pop("checked_at")
        self.assertEqual(got, case["expected_halted"])
        self.assertFalse(got["tradable"])
        self.assertIsNotNone(got["estimated_resume_time"])

    def test_candlesticks_parse(self):
        case = self.fx["kalshi_candlesticks"]
        bars = parse_candlesticks(case["raw"])
        self.assertEqual(bars, case["expected_bars"])
        # engine bar-shape invariants
        for b in bars:
            self.assertLessEqual(b["low"], min(b["open"], b["close"]))
            self.assertGreaterEqual(b["high"], max(b["open"], b["close"]))
            self.assertTrue(0.0 <= b["close"] <= 1.0)
        self.assertEqual(bars, sorted(bars, key=lambda b: b["time"]))

    def test_prob_shift_leg(self):
        case = self.fx["kalshi_prob_shift"]
        got = score_prob_shift(case["input_bars"], window=case["window"],
                               min_shift=case["min_shift"])
        self.assertEqual(got, case["expected"])

    def test_prob_shift_short_series(self):
        got = score_prob_shift([{"close": 0.5}], window=5)
        self.assertFalse(got["pass"])

    def test_offline_cache_replay(self):
        # offline=True must serve a stale snapshot with zero network access
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                os.makedirs("kalshi_cache")
                snap = {"fetched": "2001-01-01",
                        "bars": [{"time": 0, "open": 0.5, "high": 0.5,
                                  "low": 0.5, "close": 0.5, "volume": 1.0}]}
                with gzip.open(os.path.join("kalshi_cache", "S__M.json.gz"),
                               "wt", encoding="utf-8") as f:
                    json.dump(snap, f)
                bars = kalshi_fetch_prob_bars("S", "M", offline=True)
                self.assertEqual(bars, snap["bars"])
            finally:
                os.chdir(cwd)


class TestPolymarketAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = _load()

    def test_gamma_parse(self):
        case = self.fx["polymarket_gamma"]
        got = parse_gamma_market(case["raw"])
        self.assertEqual(got, case["expected"])
        self.assertEqual(got["implied_prob"], 0.65)
        self.assertEqual(len(got["clob_token_ids"]), 2)

    def test_history_parse(self):
        case = self.fx["polymarket_history"]
        bars = parse_prices_history(case["raw"])
        self.assertEqual(bars, case["expected_bars"])
        for b in bars:
            self.assertLessEqual(b["low"], min(b["open"], b["close"]))
            self.assertGreaterEqual(b["high"], max(b["open"], b["close"]))
            self.assertEqual(b["volume"], 0.0)
        self.assertEqual(bars, sorted(bars, key=lambda b: b["time"]))

    def test_implied_prob_leg(self):
        case = self.fx["polymarket_implied_prob"]
        got = score_implied_prob(case["input_bars"], min_prob=case["min_prob"],
                                 max_prob=case["max_prob"], window=case["window"],
                                 min_shift=case["min_shift"])
        self.assertEqual(got, case["expected"])

    def test_implied_prob_band_rejects_extremes(self):
        bars = [{"close": 0.97}] * 6
        got = score_implied_prob(bars, min_prob=0.1, max_prob=0.9)
        self.assertFalse(got["pass"])

    def test_offline_cache_replay(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                os.makedirs("polymarket_cache")
                snap = {"fetched": "2001-01-01",
                        "bars": [{"time": 0, "open": 0.4, "high": 0.5,
                                  "low": 0.4, "close": 0.5, "volume": 0.0}]}
                with gzip.open(os.path.join("polymarket_cache", "tok123.json.gz"),
                               "wt", encoding="utf-8") as f:
                    json.dump(snap, f)
                bars = pm_fetch_prob_bars("tok123", offline=True)
                self.assertEqual(bars, snap["bars"])
            finally:
                os.chdir(cwd)


class TestFredAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = _load()

    def test_observations_parse(self):
        from rhengine.market_data_fred import parse_observations
        case = self.fx["fred_observations"]
        got = parse_observations(case["raw"])
        self.assertEqual(got, case["expected"])
        self.assertEqual(len(got), 2)  # "." missing-data row dropped

    def test_macro_flags(self):
        from rhengine.market_data_fred import macro_flags
        case = self.fx["fred_flags"]
        got = macro_flags(case["input_snap"])
        self.assertEqual(got, case["expected"])
        self.assertTrue(got["curve_inverted"])
        self.assertTrue(got["vix_elevated"])
        self.assertFalse(got["risk_on"])

    def test_flags_incomplete_snapshot_never_risk_on(self):
        from rhengine.market_data_fred import macro_flags
        got = macro_flags({"T10Y2Y": {"time": 0, "value": 0.5}})
        self.assertFalse(got["risk_on"])  # missing series = unknown, not safe


class TestAlphaVantageAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = _load()

    def test_earnings_parse(self):
        from rhengine.market_data_av import parse_earnings
        case = self.fx["av_earnings"]
        got = parse_earnings(case["raw"])
        self.assertEqual(got, case["expected_events"])
        self.assertEqual(len(got), 2)  # null reportedDate row dropped
        self.assertEqual(got, sorted(got, key=lambda e: e["time"]))

    def test_sue_gate(self):
        from rhengine.market_data_av import sue_gate
        case = self.fx["av_sue_gate"]
        got = sue_gate(case["input_events"], case["now_ms"],
                       min_surprise_pct=case["min_surprise_pct"])
        self.assertEqual(got, case["expected"])
        self.assertTrue(got["pass"])
        self.assertEqual(got["direction"], "bear")  # -12.8% miss

    def test_sue_gate_outside_window(self):
        from rhengine.market_data_av import sue_gate, DAY_MS
        case = self.fx["av_sue_gate"]
        got = sue_gate(case["input_events"],
                       case["now_ms"] + 30 * DAY_MS)  # 35d after — stale
        self.assertFalse(got["pass"])


if __name__ == "__main__":
    unittest.main()
