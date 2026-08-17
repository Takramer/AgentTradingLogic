"""Tests for the Sharpe honesty columns in rhengine/alpha/stats.py (2026-08-17).

Every Sharpe this repo printed before today was a point estimate with no
standard error and a hard-coded sqrt(252). These pin the three things that
changed:

1. `sharpe_se` is the same estimator `deflated_sharpe` was already using
   inline — refactoring it out must not move any DSR value;
2. `lo_eta` reproduces sqrt(q) exactly on uncorrelated data and moves the right
   way when autocorrelation is present;
3. **`lo_eta` is too noisy to use at this repo's sample size**, and `perf()`
   refuses to report it when it falls inside the white-noise band.
"""
from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from rhengine.alpha import stats as S


class TestSharpeSE(unittest.TestCase):
    def test_reduces_to_the_normal_case(self):
        # Normal returns: SE = sqrt((1 + SR^2/2)/(n-1)).
        sr, n = 0.25, 200
        want = math.sqrt((1 + sr ** 2 / 2) / (n - 1))
        self.assertAlmostEqual(S.sharpe_se(sr, n, 0.0, 3.0), want, places=12)

    def test_negative_skew_and_fat_tails_widen_it(self):
        base = S.sharpe_se(0.25, 200, 0.0, 3.0)
        self.assertGreater(S.sharpe_se(0.25, 200, -1.0, 3.0), base)   # left tail
        self.assertGreater(S.sharpe_se(0.25, 200, 0.0, 8.0), base)    # fat tails
        self.assertLess(S.sharpe_se(0.25, 400, 0.0, 3.0), base)       # more data

    def test_refactor_did_not_move_the_deflated_sharpe(self):
        """deflated_sharpe used to inline this formula. Same inputs, same answer."""
        sr, n, skew, kurt = 0.22, 180, -0.4, 4.2
        se = S.sharpe_se(sr, n, skew, kurt)
        euler = 0.5772156649015329
        from scipy import stats as sps
        for trials in (1, 12, 60):
            if trials <= 1:
                sr0 = 0.0
            else:
                z1 = sps.norm.ppf(1.0 - 1.0 / trials)
                z2 = sps.norm.ppf(1.0 - 1.0 / (trials * np.e))
                sr0 = se * ((1 - euler) * z1 + euler * z2)
            want = float(sps.norm.cdf((sr - sr0) / se))
            self.assertAlmostEqual(
                S.deflated_sharpe(sr, n, trials, skew, kurt), want, places=12)

    def test_ci_brackets_the_estimate_and_annualises(self):
        lo, hi = S.sharpe_ci(0.25, 200, 0.0, 3.0, conf=0.95)
        mid = 0.25 * math.sqrt(12)
        self.assertLess(lo, mid)
        self.assertGreater(hi, mid)
        self.assertAlmostEqual((lo + hi) / 2, mid, places=10)


class TestAutocorrelationAndEta(unittest.TestCase):
    def test_eta_is_sqrt_q_when_uncorrelated(self):
        self.assertAlmostEqual(S.lo_eta(np.zeros(11), 12), math.sqrt(12), places=12)
        self.assertAlmostEqual(S.lo_eta(np.zeros(3), 4), 2.0, places=12)
        self.assertEqual(S.lo_eta(np.zeros(0), 1), 1.0)

    def test_positive_autocorrelation_lowers_eta(self):
        """Smoothed/trending returns => naive sqrt(q) OVERSTATES the Sharpe."""
        self.assertLess(S.lo_eta(np.full(11, 0.2), 12), math.sqrt(12))

    def test_negative_autocorrelation_raises_eta(self):
        self.assertGreater(S.lo_eta(np.full(11, -0.02), 12), math.sqrt(12))

    def test_autocorrelations_recover_a_known_ar1(self):
        rng = np.random.default_rng(11)
        phi, n = 0.6, 20000
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = phi * x[t - 1] + rng.standard_normal()
        r = S.autocorrelations(x, 3)
        self.assertAlmostEqual(r[0], phi, delta=0.03)
        self.assertAlmostEqual(r[1], phi ** 2, delta=0.04)

    def test_autocorrelations_are_safe_on_degenerate_input(self):
        self.assertEqual(len(S.autocorrelations(np.array([1.0, 1.0, 1.0]), 2)), 2)
        self.assertTrue(np.all(S.autocorrelations(np.zeros(10), 3) == 0.0))
        self.assertEqual(len(S.autocorrelations(np.array([1.0]), 3)), 3)


class TestNullBandGuard(unittest.TestCase):
    def test_white_noise_band_straddles_sqrt_12_and_is_wide(self):
        lo, hi = S.lo_eta_null_band(193, 12)
        self.assertLess(lo, math.sqrt(12))
        self.assertGreater(hi, math.sqrt(12))
        # The point of the guard: +-35% or so at this sample size.
        self.assertGreater(hi / math.sqrt(12), 1.25)

    def test_band_tightens_as_the_sample_grows(self):
        narrow = S.lo_eta_null_band(2000, 12)
        wide = S.lo_eta_null_band(193, 12)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_perf_suppresses_an_unmeasurable_adjustment(self):
        """Pure white-noise daily returns: eta must land in the band -> NaN."""
        rng = np.random.default_rng(5)
        idx = pd.bdate_range("2010-01-01", periods=2600)
        r = pd.Series(rng.standard_normal(len(idx)) * 0.01, index=idx)
        p = S.perf(r, label="noise")
        self.assertTrue(math.isnan(p.sharpe_lo2002),
                        f"eta {p.lo_eta} vs band {p.lo_eta_null_lo}-{p.lo_eta_null_hi}")
        # ...but the raw eta and the band are still reported, so the NaN is
        # interpretable rather than mysterious.
        self.assertTrue(math.isfinite(p.lo_eta))
        self.assertTrue(math.isfinite(p.lo_eta_null_lo))

    def test_perf_reports_the_honesty_columns(self):
        rng = np.random.default_rng(9)
        idx = pd.bdate_range("2010-01-01", periods=2600)
        r = pd.Series(rng.standard_normal(len(idx)) * 0.01 + 0.0004, index=idx)
        p = S.perf(r, label="x")
        self.assertTrue(math.isfinite(p.sharpe_se))
        self.assertLess(p.sharpe_ci_lo, p.sharpe_monthly)
        self.assertGreater(p.sharpe_ci_hi, p.sharpe_monthly)
        self.assertTrue(math.isfinite(p.ac1))

    def test_old_perf_dicts_still_construct(self):
        """The new fields are defaulted, so pre-2026-08-17 dicts still load."""
        old = {
            "label": "old", "start": "2010-01-01", "end": "2018-01-01",
            "n_days": 2000, "n_months": 96, "total_return_pct": 50.0,
            "cagr_pct": 5.0, "vol_ann_pct": 15.0, "sharpe": 0.4, "sortino": 0.6,
            "max_dd_pct": -20.0, "calmar": 0.25, "ulcer_pct": 5.0, "skew": -0.3,
            "kurtosis": 1.2, "worst_month_pct": -8.0, "best_month_pct": 9.0,
            "hit_months_pct": 55.0,
        }
        p = S.Perf(**old)
        self.assertEqual(p.sharpe, 0.4)
        self.assertTrue(math.isnan(p.sharpe_se))


if __name__ == "__main__":
    unittest.main()
