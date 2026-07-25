"""Tests for the post-audit alpha stack.

Each test is one of the "proof tests" the audit (docs/AUDIT-2026-07-24.md)
specified as a precondition for trusting any number this repo produces. They
run on synthetic data so they neither need the research dataset nor a network.
"""
import unittest

import numpy as np
import pandas as pd

from rhengine.alpha.book import DELIST_RETURN, run_book
from rhengine.alpha.signals import RMXConfig, composite_score, trend_ok, vol_scale, zscore
from rhengine.alpha.stats import alpha_beta, perf
from rhengine.alpha.validation import block_bootstrap, drop_best


def _cal(n=500, start="2010-01-04"):
    return pd.bdate_range(start, periods=n)


def _flat_panel(cal, tickers, drift=0.0005, seed=0):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.01, size=(len(cal), len(tickers)))
    return pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=cal, columns=tickers)


class TestBook(unittest.TestCase):
    """FC-1 / FC-13: the book is capital-constrained and marked to market."""

    def setUp(self):
        self.cal = _cal()
        self.tk = [f"T{i}" for i in range(10)]
        self.adj = _flat_panel(self.cal, self.tk)
        self.rf = pd.Series(0.0, index=self.cal)

    def test_weights_never_exceed_capital(self):
        tgt = {self.cal[20]: pd.Series(1.0, index=self.tk)}  # sums to 10
        bk = run_book(self.adj, tgt, self.rf, 0.0)
        self.assertLessEqual(bk.exposure.max(), 1.0 + 1e-9)

    def test_execution_is_lagged_one_session(self):
        d = self.cal[20]
        tgt = {d: pd.Series(1.0, index=["T0"])}
        bk = run_book(self.adj, tgt, self.rf, 0.0)
        # nothing is held on the decision day itself
        self.assertEqual(bk.n_positions.loc[d], 0)
        self.assertEqual(bk.n_positions.loc[self.cal[21]], 1)

    def test_cost_scales_with_turnover(self):
        tgt = {self.cal[i]: pd.Series(1.0, index=[self.tk[i % 10]])
               for i in range(20, 200, 5)}
        cheap = run_book(self.adj, tgt, self.rf, 10.0)
        dear = run_book(self.adj, tgt, self.rf, 100.0)
        self.assertGreater(dear.cost_drag.sum(), 8 * cheap.cost_drag.sum())
        self.assertLess(dear.equity.iloc[-1], cheap.equity.iloc[-1])

    def test_mtm_marks_every_session(self):
        """Equity must move on days with no rebalance - the FC-13 failure."""
        tgt = {self.cal[20]: pd.Series(1.0, index=["T0"])}
        bk = run_book(self.adj, tgt, self.rf, 0.0)
        moved = (bk.daily_return.iloc[25:200].abs() > 0).sum()
        self.assertGreater(moved, 150)

    def test_delisting_return_is_charged(self):
        adj = self.adj.copy()
        adj.loc[adj.index[300]:, "T0"] = np.nan   # T0 stops printing
        tgt = {self.cal[20]: pd.Series(1.0, index=["T0"])}
        bk = run_book(adj, tgt, self.rf, 0.0)
        hit = bk.daily_return.loc[adj.index[299]]
        self.assertLess(hit, DELIST_RETURN + 0.05)
        self.assertAlmostEqual(bk.exposure.iloc[-1], 0.0, places=6)

    def test_cash_earns_the_risk_free_rate(self):
        rf = pd.Series(0.0002, index=self.cal)
        bk = run_book(self.adj, {}, rf, 0.0)
        self.assertAlmostEqual(bk.daily_return.mean(), 0.0002, places=6)


class TestStats(unittest.TestCase):
    """FC-3 / FC-6: significance cannot be manufactured by duplication."""

    def test_alpha_t_does_not_rise_with_duplicated_observations(self):
        rng = np.random.default_rng(3)
        idx = pd.date_range("2000-01-31", periods=180, freq="ME")
        b = pd.Series(rng.normal(0.006, 0.04, 180), index=idx)
        r = 0.9 * b + pd.Series(rng.normal(0.002, 0.02, 180), index=idx)
        base = alpha_beta(r, b)
        # the FC-3 failure mode: same information, more "observations"
        idx2 = pd.date_range("2000-01-31", periods=360, freq="ME")
        r2 = pd.Series(np.repeat(r.to_numpy(), 2), index=idx2)
        b2 = pd.Series(np.repeat(b.to_numpy(), 2), index=idx2)
        dup = alpha_beta(r2, b2)
        self.assertLess(abs(dup["alpha_t"]), abs(base["alpha_t"]) * 1.6)

    def test_beta_recovered(self):
        rng = np.random.default_rng(5)
        idx = pd.date_range("2000-01-31", periods=240, freq="ME")
        b = pd.Series(rng.normal(0.006, 0.04, 240), index=idx)
        r = 0.5 * b + pd.Series(rng.normal(0.0, 0.005, 240), index=idx)
        self.assertAlmostEqual(alpha_beta(r, b)["beta"], 0.5, places=1)

    def test_perf_drawdown_is_negative_and_bounded(self):
        cal = _cal(400)
        rng = np.random.default_rng(1)
        r = pd.Series(rng.normal(0.0003, 0.012, 400), index=cal)
        p = perf(r, 0.0, "x")
        self.assertLess(p.max_dd_pct, 0.0)
        self.assertGreater(p.max_dd_pct, -100.0)


class TestSignals(unittest.TestCase):
    def test_zscore_is_winsorised(self):
        s = pd.Series([1.0] * 50 + [1000.0])
        z = zscore(s)
        self.assertLessEqual(z.max(), 3.0 + 1e-9)

    def test_trend_gate_blocks_only_on_both_conditions(self):
        cal = _cal(400)
        up = pd.Series(np.linspace(100, 200, 400), index=cal)
        down = pd.Series(np.linspace(200, 100, 400), index=cal)
        self.assertTrue(trend_ok(up, cal[-1]))
        self.assertFalse(trend_ok(down, cal[-1]))

    def test_vol_target_never_levers(self):
        cal = _cal(200)
        calm = pd.Series(np.full(200, 0.0001), index=cal)
        self.assertLessEqual(vol_scale(calm, cal[-1], 0.12, 60), 1.0)
        rng = np.random.default_rng(2)
        wild = pd.Series(rng.normal(0, 0.05, 200), index=cal)
        self.assertLess(vol_scale(wild, cal[-1], 0.12, 60), 0.5)

    def test_vol_scale_uses_only_prior_returns(self):
        cal = _cal(200)
        r = pd.Series(np.full(200, 0.0), index=cal)
        r.iloc[-1] = -0.5          # a crash ON the decision day
        self.assertEqual(vol_scale(r, cal[-1], 0.12, 60), 1.0)

    def test_composite_blends_enabled_legs_only(self):
        # resvol deliberately does NOT rank the same way as resmom, otherwise
        # the two legs are the same signal and the test proves nothing.
        st = pd.DataFrame({"resmom": [1.0, 2.0, 3.0], "resvol": [0.1, 0.4, 0.3]},
                          index=["A", "B", "C"])
        last = pd.Series([0.1, 0.0, -0.1], index=["A", "B", "C"])
        only = composite_score(st, last, RMXConfig(legs=("resmom",)))
        both = composite_score(st, last, RMXConfig(legs=("resmom", "lowvol")))
        self.assertFalse(np.allclose(only.to_numpy(), both.to_numpy()))
        self.assertEqual(only.idxmax(), "C")
        rev = composite_score(st, last, RMXConfig(legs=("reversal",)))
        self.assertEqual(rev.idxmax(), "C")   # worst last month -> best reversal


class TestValidation(unittest.TestCase):
    """FC-10: the bootstrap must not erase the clustering that makes drawdowns."""

    def test_iid_bootstrap_understates_drawdown_vs_blocks(self):
        """The FC-10 regression guard.

        `montecarlo.py` drew trades IID, which cannot reproduce the simultaneous
        losses that make real drawdowns. Blocks must produce a materially worse
        drawdown tail on the same series; if this ever stops holding, the block
        sampler has quietly become an IID sampler again.
        """
        cal = _cal(1000)
        rng = np.random.default_rng(4)
        r = pd.Series(rng.normal(0.0004, 0.01, 1000), index=cal)
        r.iloc[400:440] -= 0.01           # a clustered 40-day loss episode
        s = pd.Series(rng.normal(0.0003, 0.009, 1000), index=cal)
        blocks = block_bootstrap(r, s, paths=400, mean_block=21, seed=1)
        iid = block_bootstrap(r, s, paths=400, mean_block=1, seed=1)
        self.assertGreater(blocks["max_dd_mag_pct"]["p95"],
                           iid["max_dd_mag_pct"]["p95"] * 1.2)
        self.assertTrue(0.0 <= blocks["p_underperform_spy"] <= 1.0)

    def test_drop_best_months_exposes_concentration(self):
        idx = pd.bdate_range("2015-01-01", periods=756)
        r = pd.Series(0.0, index=idx)
        r.iloc[10] = 0.60                 # one month carries everything
        out = drop_best(r)
        self.assertGreater(out["drop_best_0"], 50.0)
        self.assertLess(abs(out["drop_best_1"]), 1.0)


if __name__ == "__main__":
    unittest.main()
