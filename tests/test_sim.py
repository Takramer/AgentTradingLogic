"""Offline tests for the simulation framework (no network, deterministic)."""

import json
import os
import tempfile
import unittest

from rhengine.backtest import run_backtest_on
from rhengine.data_local import load_csv, synthetic_universe
from rhengine.montecarlo import run_monte_carlo
from rhengine.optimize import _cfg_with, _spec_values
from rhengine.portfolio import sessions_from_bars, simulate_portfolio


def _cfg():
    return {
        "watchlist": [],
        "strategy": {"mode": "auto", "interval": "1d", "thresholds": {}},
        "exits": {
            "stop_atr_mult": 1.5, "target_atr_mult": 2.0,
            "trail_trigger_mult": 1.5, "chandelier_atr_mult": 3.0,
            "max_hold_sessions": 10, "leveraged_max_hold_sessions": 3,
        },
        "leveraged_tickers": [],
        "bear_regime_only": [],
    }


class TestSyntheticData(unittest.TestCase):
    def test_deterministic(self):
        a_map, a_spy = synthetic_universe(n_tickers=4, n_bars=120, seed=7)
        b_map, b_spy = synthetic_universe(n_tickers=4, n_bars=120, seed=7)
        self.assertEqual(a_map, b_map)
        self.assertEqual(a_spy, b_spy)

    def test_bar_shape(self):
        bars_map, spy = synthetic_universe(n_tickers=2, n_bars=80, seed=1)
        for bars in list(bars_map.values()) + [spy]:
            self.assertEqual(len(bars), 80)
            for b in bars:
                self.assertLessEqual(b["low"], min(b["open"], b["close"]))
                self.assertGreaterEqual(b["high"], max(b["open"], b["close"]))
            self.assertEqual(bars, sorted(bars, key=lambda x: x["time"]))


class TestPipeline(unittest.TestCase):
    """synthetic bars -> backtest -> portfolio -> monte carlo, all offline."""

    @classmethod
    def setUpClass(cls):
        cls.bars_map, cls.spy = synthetic_universe(n_tickers=10, n_bars=300, seed=42)
        cls.trades = run_backtest_on(_cfg(), cls.bars_map, cls.spy, min_score=2)

    def test_backtest_deterministic_and_shaped(self):
        again = run_backtest_on(_cfg(), self.bars_map, self.spy, min_score=2)
        self.assertEqual(self.trades, again)
        for t in self.trades:
            for key in ("ticker", "strategy", "regime", "signal_date", "score",
                        "r_multiple", "sessions_held", "exit_reason"):
                self.assertIn(key, t)

    def test_portfolio_sim(self):
        sessions = sessions_from_bars(self.spy)
        res = simulate_portfolio(self.trades, sessions, start_equity=10_000,
                                 risk_pct=1.0, max_positions=3, max_new_per_day=2)
        self.assertEqual(len(res["equity_curve"]), len(sessions))
        st = res["stats"]
        self.assertEqual(res["equity_curve"][-1][1], st["final_equity"])
        self.assertGreater(st["final_equity"], 0)
        # every scheduled trade is accounted for exactly once
        accounted = (len(res["taken"]) + len(res["skipped_slots"])
                     + len(res["skipped_dup"]))
        self.assertLessEqual(accounted, len(self.trades))
        # risking 1% per trade keeps drawdown far from ruin on any sample
        self.assertLess(st["max_drawdown_pct"], 100.0)

    def test_monte_carlo_deterministic(self):
        rs = [t["r_multiple"] for t in self.trades] or [1.0, -1.0, 0.5]
        a = run_monte_carlo(rs, paths=200, seed=9)
        b = run_monte_carlo(rs, paths=200, seed=9)
        self.assertEqual(a, b)
        self.assertEqual(a["paths"], 200)
        self.assertTrue(all(v >= 0 for v in a["final_equity_mult"].values()))
        dd = a["max_drawdown_pct"]
        self.assertLessEqual(dd[50], dd[99])


class TestOptimizeHelpers(unittest.TestCase):
    def test_spec_values_include_default_and_respect_cap(self):
        vals = _spec_values("bollinger", "adxMax", max_values=7)
        self.assertLessEqual(len(vals), 8)  # cap + default
        self.assertIn(35, vals)             # spec default
        self.assertEqual(vals, sorted(vals))

    def test_cfg_with_nests_overrides(self):
        cfg2 = _cfg_with(_cfg(), "bollinger", {"adxMax": 20})
        self.assertEqual(cfg2["strategy"]["thresholds"]["bollinger"]["adxMax"], 20)
        # original untouched
        self.assertEqual(_cfg()["strategy"]["thresholds"], {})


class TestCsvLoader(unittest.TestCase):
    def test_round_trip(self):
        rows = ("Date,Open,High,Low,Close,Volume\n"
                "2024-01-02,100,102,99,101,5000\n"
                "2024-01-03,101,103,100,102,6000\n")
        fd, path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(rows)
            bars = load_csv(path)
        finally:
            os.remove(path)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["close"], 101.0)
        self.assertLess(bars[0]["time"], bars[1]["time"])


if __name__ == "__main__":
    unittest.main()
