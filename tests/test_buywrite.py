"""test_buywrite.py — the ways a premium-selling result gets overstated.

`alpha/buywrite.py` is the only OPTIONS evidence this repo has, and it is
evidence because it comes from a published, mechanical, survivorship-free
record rather than from a video. The failure modes are all silent:

* `test_price_index_manufactures_significance` — the single most important
  test in this file. BXM's alpha is +1.84%/yr t=+2.05 against the S&P PRICE
  index and +0.58%/yr t=+0.62 against the TOTAL RETURN index. The entire
  "covered calls generate alpha" case is a benchmark error, and it is
  reproducible on demand.
* `test_dead_period_is_nan_not_zero` — pandas' `prod` skips NaN, so a series
  with no data compounds to a perfectly plausible 0.00%/yr. PUT printed that
  for the 1980s.
* `test_rf_is_not_zero` — PUT earns the bill rate on collateral by
  construction; rf=0 credits the put writer with the whole T-bill return.

Data is gitignored (research_data/). These skip rather than fail when it has
not been fetched — `py scripts/fetch_cboe_benchmarks.py`.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha import buywrite  # noqa: E402
from rhengine.alpha.stats import alpha_beta, monthly  # noqa: E402

HAVE = all(os.path.exists(os.path.join(buywrite.CBOE, f"{n}.csv"))
           for n in ("BXM", "PUT", "SP500TR", "GSPC"))
WHY = "run `py scripts/fetch_cboe_benchmarks.py` first (research_data is gitignored)"


class TestCagrArithmetic(unittest.TestCase):
    def test_dead_period_is_nan_not_zero(self):
        """The bug that shipped in the first draft: reindexing onto a wider
        calendar fills the dead period with NaN, `prod` skips NaN, and a series
        that does not exist yet reports a clean 0.00%/yr."""
        r = pd.Series([np.nan] * 300)
        self.assertTrue(np.isnan(buywrite._cagr(r)))

    def test_short_window_is_nan(self):
        self.assertTrue(np.isnan(buywrite._cagr(pd.Series([0.001] * 30))))

    def test_cagr_is_right_on_a_known_series(self):
        r = pd.Series([0.0004] * 252)          # ~10.6%/yr
        self.assertAlmostEqual(buywrite._cagr(r), 10.62, places=1)


@unittest.skipUnless(HAVE, WHY)
class TestPublishedRecord(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.px = {k: buywrite.load_series(k)
                  for k in ("BXM", "PUT", "SP500TR", "GSPC")}
        cls.r = {k: buywrite.daily_returns(v) for k, v in cls.px.items()}

    def _aligned(self, leg):
        idx = self.r[leg].index
        for b in ("SP500TR", "GSPC"):
            idx = idx.intersection(self.r[b].index)
        return idx

    def test_price_index_manufactures_significance(self):
        """THE FINDING. Against the price index the covered-call writer looks
        like it has alpha at t > 2. Against the total-return index — the one it
        actually holds, dividends and all — it has none. Same series, same
        days, benchmark alone flips the verdict.

        If this test fails because the numbers moved, the finding is unchanged;
        re-derive the thresholds. If it fails because the SIGN flipped, stop and
        read the data."""
        idx = self._aligned("BXM")
        r = monthly(self.r["BXM"].reindex(idx))
        rf = monthly(buywrite.load_rf_daily(idx))
        vs_tr = alpha_beta(r, monthly(self.r["SP500TR"].reindex(idx)), rf)
        vs_px = alpha_beta(r, monthly(self.r["GSPC"].reindex(idx)), rf)
        self.assertGreater(vs_px["alpha_t"], 1.9, "price-index alpha t")
        self.assertLess(vs_tr["alpha_t"], 1.5, "total-return alpha t")
        self.assertGreater(vs_px["alpha_ann"], vs_tr["alpha_ann"])

    def test_neither_leg_beats_the_index_on_wealth(self):
        for leg in ("BXM", "PUT"):
            idx = self._aligned(leg)
            g_leg = float((1 + self.r[leg].reindex(idx)).prod())
            g_tr = float((1 + self.r["SP500TR"].reindex(idx)).prod())
            self.assertLess(g_leg, g_tr, f"{leg} vs SP500TR")

    def test_both_legs_run_at_roughly_beta_six(self):
        """Which is why the beta-matched cash blend is the benchmark that
        decides anything: a 0.6-beta payoff is buyable without options."""
        for leg in ("BXM", "PUT"):
            idx = self._aligned(leg)
            b = float(np.polyfit(self.r["SP500TR"].reindex(idx).to_numpy(float),
                                 self.r[leg].reindex(idx).to_numpy(float), 1)[0])
            self.assertTrue(0.45 < b < 0.75, f"{leg} beta {b:.2f}")

    def test_rf_is_not_zero(self):
        idx = self._aligned("PUT")
        rf = buywrite.load_rf_daily(idx)
        self.assertEqual(len(rf), len(idx))
        self.assertGreater(float(rf.sum()), 0.10,
                           "risk-free is flat zero — the French parse failed, "
                           "and PUT earns the bill rate by construction")
        self.assertLess(float(rf.max()), 0.01, "a daily rf above 1% is a "
                                               "monthly rate not divided down")

    def test_series_cover_the_claimed_history(self):
        self.assertLess(self.px["BXM"].index[0].year, 1990)
        self.assertLess(self.px["PUT"].index[0].year, 1998)
        self.assertGreater(len(self.px["BXM"]), 9000)


if __name__ == "__main__":
    unittest.main()
