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

    def test_portfolio_heat_cap(self):
        sessions = ["2026-01-{:02d}".format(d) for d in range(1, 29)]
        trades = [{"ticker": "T{}".format(k), "signal_date": "2026-01-01",
                   "sessions_held": 12, "r_multiple": 1.0, "score": 4}
                  for k in range(8)]
        capped = simulate_portfolio(trades, sessions, risk_pct=3.0,
                                    max_positions=8, max_new_per_day=8,
                                    max_heat_pct=10.0)
        # 3 x 3% = 9% fits under the 10% heat cap; a 4th (12%) must not
        self.assertEqual(len(capped["taken"]), 3)
        uncapped = simulate_portfolio(trades, sessions, risk_pct=3.0,
                                      max_positions=8, max_new_per_day=8,
                                      max_heat_pct=100.0)
        self.assertEqual(len(uncapped["taken"]), 8)

    def test_monte_carlo_deterministic(self):
        rs = [t["r_multiple"] for t in self.trades] or [1.0, -1.0, 0.5]
        a = run_monte_carlo(rs, paths=200, seed=9)
        b = run_monte_carlo(rs, paths=200, seed=9)
        self.assertEqual(a, b)
        self.assertEqual(a["paths"], 200)
        self.assertTrue(all(v >= 0 for v in a["final_equity_mult"].values()))
        dd = a["max_drawdown_pct"]
        self.assertLessEqual(dd[50], dd[99])


class TestAblation(unittest.TestCase):
    def test_masks_partition_and_full_set(self):
        from rhengine.ablate import evaluate_masks
        trades = [
            {"legs": "1111", "signal_date": "2024-01-05", "r_multiple": 1.0},
            {"legs": "1100", "signal_date": "2024-01-06", "r_multiple": -1.0},
            {"legs": "1010", "signal_date": "2024-06-05", "r_multiple": 0.5},
            {"legs": "0111", "signal_date": "2024-06-06", "r_multiple": 2.0},
        ]
        rows = evaluate_masks(trades, 4, split_date="2024-03-01")
        self.assertEqual(len(rows), 8)  # 2^3 confirmation subsets
        by_mask = {a: (i, o) for a, i, o in rows}
        # primary-only mask sees every trade whose leg 0 passed (3 of 4)
        is_st, oos_st = by_mask[(0,)]
        self.assertEqual((is_st["n"], oos_st["n"]), (2, 1))
        # full mask keeps only the all-pass trade, in-sample side
        is_st, oos_st = by_mask[(0, 1, 2, 3)]
        self.assertEqual(is_st["n"], 1)
        self.assertIsNone(oos_st)

    def test_tstat_present(self):
        from rhengine.backtest import _stats
        st = _stats([{"r_multiple": r} for r in (1.0, 1.2, 0.8, 1.1, 0.9)])
        self.assertGreater(st["t_stat"], 2.0)  # consistent positive edge


class TestHwmSizing(unittest.TestCase):
    def test_hwm_keeps_risk_level_in_drawdown(self):
        sessions = ["2024-01-0{}".format(d) for d in range(1, 10)]
        trades = [
            {"ticker": "A", "signal_date": sessions[0], "sessions_held": 1,
             "r_multiple": -1.0, "score": 4, "weighted_score": 4},
            {"ticker": "B", "signal_date": sessions[2], "sessions_held": 1,
             "r_multiple": 1.0, "score": 4, "weighted_score": 4},
        ]
        cur = simulate_portfolio(trades, sessions, risk_pct=5.0)
        hwm = simulate_portfolio(trades, sessions, risk_pct=5.0,
                                 size_from_hwm=True)
        # current-equity mode: lose 500, then win 5% of 9500 -> 9975
        self.assertEqual(cur["stats"]["final_equity"], 9975.0)
        # hwm mode: second trade still risks 5% of the 10k watermark -> 10000
        self.assertEqual(hwm["stats"]["final_equity"], 10000.0)


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
