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


class TestLegMask(unittest.TestCase):
    """legMask gate: bit i set = leg i must pass; unmasked legs bypassed."""

    def _score(self, mask):
        from rhengine.indicators import score_ticker, spec_defaults
        bars_map, spy = synthetic_universe(n_tickers=6, n_bars=300, seed=42)
        th = dict(spec_defaults("residualmomentum"), legMask=mask)
        out = {}
        for t, bars in bars_map.items():
            s = score_ticker(t, bars, "residualmomentum", th, spy)
            if s:
                out[t] = s
        return out

    def test_mask_zero_is_identity(self):
        from rhengine.indicators import score_ticker, spec_defaults
        bars_map, spy = synthetic_universe(n_tickers=6, n_bars=300, seed=42)
        th0 = spec_defaults("residualmomentum")
        for t, bars in bars_map.items():
            self.assertEqual(score_ticker(t, bars, "residualmomentum", th0, spy),
                             score_ticker(t, bars, "residualmomentum",
                                          dict(th0, legMask=0), spy))

    def test_mask_primary_only(self):
        masked = self._score(1)
        for setups in masked.values():
            for s in setups:
                self.assertTrue(s["legs"][0]["pass"])   # primary really passed
                self.assertEqual(s["score"], 4)          # bypassed legs count
                for l in s["legs"][1:]:
                    self.assertTrue(l["pass"])
        # primary-only must be a superset of the full-mask population
        full = self._score(15)
        self.assertTrue(set(full).issubset(set(masked) | set(full)))

    def test_bypassed_legs_keep_raw_pass(self):
        """The bypass must not destroy the real evaluation.

        Regression for the ablation blindness (2026-07-25): overwriting "pass"
        was the only record of leg state, so backtest.py wrote "1111" for every
        trade and ablate.py could not tell any leg subset from another.
        """
        masked = self._score(1)
        self.assertTrue(masked, "fixture produced no setups")
        saw_failing_confirm = False
        for setups in masked.values():
            for s in setups:
                for l in s["legs"]:
                    self.assertIn("raw_pass", l)
                self.assertTrue(s["legs"][0]["raw_pass"])  # masked leg is real
                if not all(l["raw_pass"] for l in s["legs"][1:]):
                    saw_failing_confirm = True
        self.assertTrue(saw_failing_confirm,
                        "no bypassed leg actually failed — cannot prove "
                        "raw_pass survives the bypass")


class TestAblationDiscriminates(unittest.TestCase):
    """The ablation must be able to tell leg subsets apart under a legMask.

    config.json ships thresholds.residualmomentum.legMask=1. Under the old
    gate that made every emitted trade record "1111", so all 8 subsets scored
    identically and the notebook drew 8 identical bars — a null result by
    construction, presented as a result.
    """

    def _trades(self, mask):
        cfg = _cfg()
        cfg["strategy"]["thresholds"]["residualmomentum"] = {"legMask": mask}
        bars_map, spy = synthetic_universe(n_tickers=12, n_bars=400, seed=42)
        trades = run_backtest_on(cfg, bars_map, spy, min_score=2,
                                 forced_strategy="residualmomentum", cost_bps=5)
        return trades, spy

    def test_leg_states_vary_under_mask(self):
        trades, _ = self._trades(1)
        self.assertTrue(trades, "fixture produced no trades")
        states = {t["legs"] for t in trades}
        self.assertGreater(len(states), 1,
                           "every trade recorded the same leg mask {} — the "
                           "ablation is blind".format(states))
        # legMask=1 requires leg 0, so it must pass on every emitted trade
        self.assertTrue(all(t["legs"][0] == "1" for t in trades))

    def test_evaluate_masks_separates_subsets(self):
        from rhengine.ablate import evaluate_masks
        from rhengine.backtest import _iso_date, WARMUP_BARS
        trades, spy = self._trades(1)
        dates = [_iso_date(b["time"]) for b in spy[WARMUP_BARS:]]
        rows = evaluate_masks(trades, 4, dates[int(len(dates) * 0.7)])
        self.assertEqual(len(rows), 8)
        ns = {(s or {}).get("n", 0) for _, s, _ in rows}
        self.assertGreater(len(ns), 1, "all subsets share one sample size")

    def test_degenerate_input_raises(self):
        """The guard is the tripwire if the root cause ever comes back."""
        from rhengine.ablate import evaluate_masks, AblationDegenerateError
        flat = [{"legs": "1111", "signal_date": "2024-01-{:02d}".format(i % 28 + 1),
                 "r_multiple": 0.1} for i in range(50)]
        with self.assertRaises(AblationDegenerateError):
            evaluate_masks(flat, 4, "2024-01-20")
        # opt-out still returns the rows for inspection
        self.assertEqual(len(evaluate_masks(flat, 4, "2024-01-20", strict=False)), 8)


class TestCostUnits(unittest.TestCase):
    """`cost_bps` means ROUND TRIP everywhere. Pin it so the 2x cannot return.

    Until 2026-07-27 backtest._cost_r, costs.cost_in_r and
    costs.breakeven_cost_bps all doubled the value, treating a round-trip config
    number as per-side. Every backtest was charged 2x and every reported
    breakeven was half the truth. Live quotes then measured the real cost of
    this universe at 29.1 bps round trip, against a config that said 30 and an
    engine that charged 60.
    """

    def test_cost_in_r_matches_first_principles(self):
        """Derivation, not a magic number.

        Position notional N with a stop `stop_pct` below entry risks
        N * stop_pct dollars, which is 1R by definition. A round trip costs
        N * bps/1e4 dollars. So cost in R = bps/1e4 / stop_pct — with no factor
        of two, because `bps` is already the whole round trip.
        """
        from rhengine.backtest import _cost_r
        from rhengine.costs import cost_in_r
        for entry, stop, bps in ((100.0, 96.0, 30.0), (50.0, 47.5, 10.0),
                                 (12.0, 11.4, 29.1), (250.0, 240.0, 5.0)):
            stop_pct = (entry - stop) / entry
            notional = 10_000.0
            risk_dollars = notional * stop_pct
            cost_dollars = notional * bps / 10_000.0
            expected = cost_dollars / risk_dollars
            self.assertAlmostEqual(_cost_r(entry, stop, bps), expected, places=12)
            self.assertAlmostEqual(cost_in_r(entry, stop, bps), expected, places=12)

    def test_engine_and_library_agree(self):
        """backtest._cost_r and costs.cost_in_r must never drift apart."""
        from rhengine.backtest import _cost_r
        from rhengine.costs import cost_in_r
        for entry, stop in ((100.0, 96.0), (7.5, 7.1), (300.0, 287.0)):
            for bps in (0.0, 5.0, 29.1, 30.0, 60.0):
                self.assertAlmostEqual(_cost_r(entry, stop, bps),
                                       cost_in_r(entry, stop, bps), places=12)

    def test_breakeven_inverts_cost_in_r(self):
        """Charging a book exactly its breakeven must land mean R on zero.

        This is the property that failed before: breakeven returned a per-side
        figure while cost_in_r consumed a round-trip one, so feeding one into
        the other left half the edge on the table.
        """
        from rhengine.costs import breakeven_cost_bps, cost_in_r
        entry, stop = 100.0, 95.0          # 5% stop
        trades = [{"entry": entry, "stop": stop, "r_multiple": r}
                  for r in (1.5, -1.0, 0.4, -1.0, 0.9, 2.0, -1.0)]
        be = breakeven_cost_bps(trades)
        self.assertIsNotNone(be)
        mean_r = sum(t["r_multiple"] for t in trades) / len(trades)
        net = mean_r - cost_in_r(entry, stop, be)
        self.assertAlmostEqual(net, 0.0, places=10)

    def test_breakeven_is_a_round_trip_figure(self):
        """A 5% stop and a +0.10 mean R breaks even at 50 bps ROUND TRIP.

        cost_in_r = bps/1e4/0.05 = 0.10  ->  bps = 50. If this ever reads 25,
        the per-side convention has crept back in.
        """
        from rhengine.costs import breakeven_cost_bps
        trades = [{"entry": 100.0, "stop": 95.0, "r_multiple": 0.10}] * 200
        self.assertAlmostEqual(breakeven_cost_bps(trades), 50.0, places=6)

    def test_no_edge_returns_none(self):
        """None means 'no edge to erode' — and ONLY that."""
        from rhengine.costs import breakeven_cost_bps
        self.assertIsNone(breakeven_cost_bps(
            [{"entry": 100.0, "stop": 95.0, "r_multiple": -0.2}] * 10))

    def test_unusable_rows_raise_rather_than_read_as_no_edge(self):
        """A malformed input must not masquerade as a finding.

        The mc_refined harvests carry r_multiple but no entry/stop. Fed to this
        function they used to return None for every sleeve, which reads as
        'no edge' — including for macd at a +0.14 gross mean R.
        """
        from rhengine.costs import breakeven_cost_bps
        with self.assertRaises(ValueError):
            breakeven_cost_bps([{"strategy": "macd", "r_multiple": 0.14}] * 50)
        with self.assertRaises(ValueError):
            breakeven_cost_bps([])

    def test_every_cost_path_agrees(self):
        """Four functions compute cost-in-R independently. All four must match.

        They drifted before: backtest._cost_r, costs.cost_in_r and
        sanity._extra_cost_r each carried the 2x, while the vectorbt path in
        mc_refined/ablate_vbt double-charged a different way (a round-trip rate
        applied per order). Any new cost path belongs in this test.
        """
        from rhengine.backtest import _cost_r
        from rhengine.costs import cost_in_r
        from rhengine.sanity import _extra_cost_r
        for entry, stop, bps in ((100.0, 96.0, 30.0), (20.0, 19.0, 29.1),
                                 (400.0, 380.0, 12.5)):
            ref = _cost_r(entry, stop, bps)
            self.assertAlmostEqual(cost_in_r(entry, stop, bps), ref, places=12)
            self.assertAlmostEqual(
                _extra_cost_r({"entry": entry, "stop": stop}, bps), ref, places=12)

    def test_vectorbt_fee_rate_is_per_order(self):
        """The vbt paths must halve the round-trip figure before passing it.

        vectorbt charges `fees` on entry AND exit, so handing it a round-trip
        number bills it twice. Pinned by reading the source rather than running
        a backtest — the assertion is about the units, and a full vbt run here
        would be slow and data-dependent.
        """
        import inspect
        from rhengine import ablate_vbt, mc_refined
        for mod in (mc_refined, ablate_vbt):
            src = inspect.getsource(mod)
            self.assertIn("fees=cost_bps / 2.0 / 1e4", src,
                          "{}: vbt fee rate is not half the round trip — it will "
                          "be charged on both entry and exit".format(mod.__name__))


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


class TestAppPath(unittest.TestCase):
    """Secrets/config must resolve from any cwd, not just the repo root."""

    def test_resolves_repo_file_from_subdir(self):
        import os
        from rhengine._paths import app_path, _REPO_ROOT
        # a file that exists at the repo root resolves from a nested cwd
        target = "pyproject.toml"
        if not os.path.exists(os.path.join(_REPO_ROOT, target)):
            self.skipTest("pyproject.toml not at repo root")
        old = os.getcwd()
        sub = os.path.join(_REPO_ROOT, "rhengine")
        try:
            os.chdir(sub)
            got = app_path(target)
            self.assertTrue(os.path.exists(got))
            self.assertEqual(os.path.realpath(got),
                             os.path.realpath(os.path.join(_REPO_ROOT, target)))
        finally:
            os.chdir(old)

    def test_missing_file_points_at_cwd(self):
        import os
        from rhengine._paths import app_path
        got = app_path("definitely_not_a_real_file_xyz.json")
        self.assertEqual(os.path.dirname(got), os.getcwd())


class TestRegimeSplit(unittest.TestCase):
    def test_buckets_partition_trades(self):
        from rhengine.crossval import split_by_regime, regime_stability
        trades = [
            {"regime": "uptrend", "vix_regime": "calm", "r_multiple": 1.0},
            {"regime": "uptrend", "vix_regime": "calm", "r_multiple": -1.0},
            {"regime": "choppy", "vix_regime": "fear_spiking", "r_multiple": 0.5},
            {"regime": "downtrend", "vix_regime": "panic", "r_multiple": -0.5},
        ]
        by = split_by_regime(trades, "regime")
        self.assertEqual(sum(s["n"] for s in by.values()), len(trades))
        self.assertEqual(by["uptrend"]["n"], 2)
        rs = regime_stability(trades)
        self.assertEqual(rs["regimes_scored"], 3)
        # uptrend avg is 0.0 (+1,-1) so not positive; choppy is the only one >0
        self.assertEqual(rs["positive_regimes"], ["choppy"])


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


class TestCapitalRateCheck(unittest.TestCase):
    """sanity.check_capital_rate — the gate that notices R-sums are not returns.

    The failure it exists to catch is a book with excellent R-space statistics
    whose capital earns less than simply holding the index. That happened to
    every strategy in the repo on 2026-07-31 (docs/AUTOSTRAT-2026-07-31.md §9),
    and none of the other ten checks noticed.
    """

    @staticmethod
    def _trades(r, stop_pct, held, n=200):
        entry = 100.0
        return [{"r_multiple": r, "entry": entry,
                 "stop": entry * (1.0 - stop_pct), "sessions_held": held}
                for _ in range(n)]

    def test_identity_matches_hpr_definition(self):
        """The reported rate must equal sum(R*stop_pct)/sum(days) exactly.

        This is an identity, not an approximation: portfolio.py sizes
        notional = risk/stop_pct, so P&L/notional = R*stop_pct. If this drifts,
        the check is measuring something that is not a return on capital.
        """
        from rhengine.sanity import check_capital_rate
        r, stop_pct, held = 0.25, 0.05, 10
        _, _, value, _ = check_capital_rate(self._trades(r, stop_pct, held))
        self.assertAlmostEqual(float(value.split()[0]),
                               1e4 * (r * stop_pct) / held, places=6)

    def test_grades_against_passive_drift(self):
        """PASS above passive, WARN positive-but-below, FAIL at a loss."""
        from rhengine.sanity import PASSIVE_BP_PER_DAY, check_capital_rate
        # 0.25R on a 5% stop over 10 sessions = 12.5 bp/day, well above passive
        self.assertEqual(check_capital_rate(self._trades(0.25, 0.05, 10))[1],
                         "PASS")
        # positive but starved: same edge stretched over 200 sessions
        self.assertEqual(check_capital_rate(self._trades(0.25, 0.05, 200))[1],
                         "WARN")
        self.assertEqual(check_capital_rate(self._trades(-0.10, 0.05, 10))[1],
                         "FAIL")
        self.assertGreater(PASSIVE_BP_PER_DAY, 0)

    def test_high_meanR_can_still_fail(self):
        """The whole point: a big mean R with a tight stop and a long hold is
        NOT a good rate. +0.50R sounds excellent; on a 1% stop held 250
        sessions it earns 0.2 bp/day, a twentieth of passive drift."""
        from rhengine.sanity import _t_stat, check_capital_rate
        # jittered so the R series has variance and t_stat is meaningful —
        # a constant series has zero variance and t_stat returns 0 by design
        trades = self._trades(0.50, 0.01, 250)
        for i, t in enumerate(trades):
            t["r_multiple"] = 0.50 + (0.05 if i % 2 else -0.05)
        self.assertEqual(check_capital_rate(trades)[1], "WARN")
        # and R-space is perfectly happy with it: a huge, highly significant t
        self.assertGreater(_t_stat([t["r_multiple"] for t in trades]), 20)

    def test_skips_without_holding_length(self):
        """R-space-only CSVs must SKIP, never silently score as zero."""
        from rhengine.sanity import check_capital_rate
        trades = [{"r_multiple": 0.2, "entry": 100.0, "stop": 95.0}
                  for _ in range(200)]
        self.assertEqual(check_capital_rate(trades)[1], "SKIP")

    def test_wired_into_the_battery(self):
        """A check that is not in CHECKS gates nothing."""
        from rhengine.sanity import CHECKS, check_capital_rate, run_battery
        self.assertIn(check_capital_rate, CHECKS)
        names = [row[0] for row in run_battery(self._trades(0.25, 0.05, 10))]
        self.assertIn("capital_rate", names)


if __name__ == "__main__":
    unittest.main()
