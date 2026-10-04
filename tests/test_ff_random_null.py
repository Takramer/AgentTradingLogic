"""The French random-quintile null and the panic-cut weight.

These pin the rules that were logged before the French file was scored.
They do not read a portfolio CSV.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))

from ff_random_null import (  # noqa: E402
    adopt_panic, held_returns, july_june_cohort, momentum_weight, panic_state,
    percentile, quintile_picks, terminal_wealth,
)


def _month_end(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="ME")


class TestCohortAndNull(unittest.TestCase):
    def test_july_through_june_is_one_cohort(self):
        dates = pd.DatetimeIndex([
            "1963-07-31", "1964-06-30", "1964-07-31", "1965-06-30",
        ])
        got = july_june_cohort(dates)
        self.assertEqual(got[0], got[1])
        self.assertEqual(got[2], got[3])
        self.assertNotEqual(got[1], got[2])

    def test_dominant_quintile_clears_every_random_path(self):
        dates = _month_end("1963-07-31", 120)
        cols = [0.0, 0.0, 0.0, 0.0, 0.02]
        ports = pd.DataFrame({f"c{i}": cols[i] for i in range(5)}, index=dates)
        picks = quintile_picks(dates, "month", 5, 200, 11)
        finals = terminal_wealth(held_returns(ports, picks))
        actual = float(terminal_wealth(ports["c4"].to_numpy(float)))
        self.assertEqual(percentile(actual, finals), 100.0)
        # a cohort redraw is the same fact on a slower clock
        picks_c = quintile_picks(dates, "july_june", 5, 200, 11)
        finals_c = terminal_wealth(held_returns(ports, picks_c))
        self.assertEqual(percentile(actual, finals_c), 100.0)

    def test_worst_quintile_beats_no_random_path(self):
        dates = _month_end("1963-07-31", 80)
        ports = pd.DataFrame({f"c{i}": (0.02 if i else 0.0) for i in range(5)},
                             index=dates)
        picks = quintile_picks(dates, "month", 5, 200, 11)
        finals = terminal_wealth(held_returns(ports, picks))
        actual = float(terminal_wealth(ports["c0"].to_numpy(float)))
        self.assertEqual(percentile(actual, finals), 0.0)

    def test_percentile_ignores_ties(self):
        self.assertAlmostEqual(percentile(1.0, np.array([1.0, 1.0, 0.5])), 100.0 / 3.0)


class TestPanicCut(unittest.TestCase):
    def test_changing_this_month_does_not_change_this_months_flag(self):
        rng = np.random.default_rng(0)
        idx = _month_end("1963-07-31", 200)
        r = pd.Series(rng.normal(0.005, 0.04, len(idx)), index=idx)
        flag = panic_state(r)
        bumped = r.copy()
        bumped.iloc[150] += 0.50
        flag2 = panic_state(bumped)
        self.assertTrue(flag.iloc[:151].equals(flag2.iloc[:151]))

    def test_a_flat_bear_does_not_fire(self):
        # Constant negative returns are a bear market with zero variance.
        idx = _month_end("1963-07-31", 100)
        r = pd.Series(-0.01, index=idx)
        self.assertFalse(bool(panic_state(r).any()))

    def test_a_variance_spike_in_a_bear_fires_after_the_spike(self):
        idx = _month_end("1963-07-31", 120)
        r = pd.Series(0.0, index=idx)
        r.iloc[90:96] = [0.15, -0.15, 0.15, -0.15, 0.15, -0.15]
        flag = panic_state(r)
        self.assertFalse(bool(flag.iloc[:90].any()))
        self.assertTrue(bool(flag.iloc[96:].any()))
        w = momentum_weight(flag)
        self.assertTrue(set(np.unique(w.to_numpy())).issubset({0.0, 0.5}))
        self.assertEqual(float(w.loc[flag].max()), 0.0)
        self.assertEqual(float(w.loc[~flag].min()), 0.5)

    def test_adopt_requires_every_gate(self):
        self.assertTrue(adopt_panic(3.2, 2.5, -20.0, -24.0, 1.0, 1.2, 0.04))
        self.assertFalse(adopt_panic(3.2, 2.5, -24.0, -20.0, 1.0, 1.2, 0.04))
        self.assertFalse(adopt_panic(2.4, 2.5, -20.0, -24.0, 1.0, 1.2, 0.04))
        self.assertFalse(adopt_panic(3.2, 2.5, -20.0, -24.0, 3.0, 1.0, 0.04))
        self.assertFalse(adopt_panic(3.2, 2.5, -20.0, -24.0, 1.0, 1.2, 0.20))


if __name__ == "__main__":
    unittest.main()
