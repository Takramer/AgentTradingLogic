"""test_options_costs.py — the ways an options cost number can be quietly wrong.

Every test here corresponds to a unit error that would produce a plausible
figure rather than a crash, which is the only kind of bug that matters in a
measurement module.

* `test_spread_is_round_trip_not_half` — the repo has already shipped a 2x cost
  double-count once (commit e753c64, and again in three analysis scripts found
  2026-07-31). The spread IS the round trip; halving or doubling it here would
  reproduce that class of bug in a new module.
* `test_delta_bps_is_comparable_to_equity_bps` — the 100-share multiplier must
  cancel. If it does not, this number is off by 100x and would read as either
  free or ruinous.
* `test_delta_and_premium_units_disagree_by_design` — the two must NOT be equal;
  a change that made them agree would mean one of them lost its denominator.
* `test_breakeven_scales_with_decay_days` — weekend risk. A Friday-to-Monday
  hold pays three calendar days of theta for one trading day of movement.
* `test_parity_recovers_a_known_forward` — validates the consistency check
  itself against an exactly-constructed pair.
"""
from __future__ import annotations

import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.options_costs import (  # noqa: E402
    EQUITY_ROUND_TRIP_BPS,
    analyse,
    breakeven_move_pct,
    implied_forward,
    spread_bps_delta,
    spread_bps_premium,
    theta_bps_per_day,
)


class TestSpreadUnits(unittest.TestCase):
    def test_spread_is_round_trip_not_half(self):
        """bid 4.00 / ask 4.20 on a 4.10 mark is 0.20/4.10 = 487.8 bps."""
        self.assertAlmostEqual(spread_bps_premium(4.00, 4.20, 4.10),
                               1e4 * 0.20 / 4.10, places=9)

    def test_rejects_crossed_and_degenerate_quotes(self):
        self.assertIsNone(spread_bps_premium(4.20, 4.00, 4.10))   # crossed
        self.assertIsNone(spread_bps_premium(0.0, 0.0, 0.0))      # no mark
        self.assertIsNone(spread_bps_delta(4.0, 4.2, 0.0, 700.0))  # zero delta
        self.assertIsNone(spread_bps_delta(4.0, 4.2, 0.5, 0.0))    # zero spot

    def test_delta_bps_is_comparable_to_equity_bps(self):
        """The 100-share multiplier must cancel, and the result must be small.

        One contract on 100 shares at delta 0.50 carries 50 shares of exposure.
        A 0.10 spread costs $10 round trip against 50 * 700 = $35,000 of
        exposure = 2.86 bps — an ORDER OF MAGNITUDE below the 30 bps equity
        benchmark. That is the surprising half of this module's finding, so it
        is asserted rather than described.
        """
        got = spread_bps_delta(3.95, 4.05, 0.50, 700.0)
        self.assertAlmostEqual(got, 1e4 * (10.0 / (50 * 700.0)), places=9)
        self.assertLess(got, EQUITY_ROUND_TRIP_BPS / 5.0)

    def test_delta_and_premium_units_disagree_by_design(self):
        """They answer different questions and must not collapse into one."""
        prem = spread_bps_premium(3.95, 4.05, 4.00)
        dlt = spread_bps_delta(3.95, 4.05, 0.50, 700.0)
        self.assertGreater(prem, 10 * dlt)

    def test_delta_sign_does_not_change_cost(self):
        """A put's negative delta must not flip the cost negative."""
        self.assertAlmostEqual(spread_bps_delta(1.35, 1.37, -0.358, 746.81),
                               spread_bps_delta(1.35, 1.37, 0.358, 746.81),
                               places=12)


class TestDecay(unittest.TestCase):
    def test_theta_reported_as_positive_cost(self):
        """theta arrives negative; the reported figure is a cost, so positive."""
        self.assertGreater(theta_bps_per_day(-0.434, 3.44), 0)
        self.assertAlmostEqual(theta_bps_per_day(-0.434, 3.44),
                               1e4 * 0.434 / 3.44, places=9)

    def test_breakeven_scales_with_decay_days(self):
        """Weekend risk: 3 calendar days of theta, 1 trading day of movement.

        Friday-close to Monday-close on a short-dated contract pays three days
        of decay. The breakeven move must grow accordingly, and by more than the
        spread-only component.
        """
        kw = dict(bid=3.40, ask=3.48, theta=-0.434071, delta=0.639246,
                  spot=746.81)
        one = breakeven_move_pct(days=1.0, **kw)
        three = breakeven_move_pct(days=3.0, **kw)
        self.assertGreater(three, one)
        # the extra is exactly two more days of theta, converted to underlying %
        extra = 100.0 * (2 * 0.434071) / (0.639246 * 746.81)
        self.assertAlmostEqual(three - one, extra, places=9)

    def test_zero_decay_leaves_only_the_spread(self):
        be = breakeven_move_pct(3.40, 3.48, -0.434071, 0.639246, 746.81, days=0)
        self.assertAlmostEqual(be, 100.0 * 0.08 / (0.639246 * 746.81), places=9)


class TestParity(unittest.TestCase):
    def test_parity_recovers_a_known_forward(self):
        """Construct C - P from an exact forward, then invert it."""
        F, K, r, t = 760.0, 745.0, 0.04, 0.25
        diff = math.exp(-r * t) * (F - K)          # C - P by parity
        self.assertAlmostEqual(implied_forward(diff, 0.0, K, r, t), F, places=9)


class TestSnapshot(unittest.TestCase):
    """The captured file is evidence; guard the facts that were surprising."""

    @classmethod
    def setUpClass(cls):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "research_data", "options", "spy_quotes_2026-07-31.json")
        cls.snap = json.load(open(p))

    def test_greeks_are_populated(self):
        """The 2026-07-30 spike said IV and greeks were NULL. They are not.

        This test exists so that claim cannot silently return to the docs.
        """
        for c in self.snap["contracts"]:
            for field in ("iv", "delta", "gamma", "theta", "vega"):
                self.assertIsNotNone(c.get(field), (c["exp"], c["strike"], field))
            self.assertGreater(c["iv"], 0.0)
            self.assertIsNotNone(c.get("volume"))
            self.assertIsNotNone(c.get("open_interest"))

    def test_short_dated_is_cheap_per_delta_and_ruinous_per_day(self):
        """The finding worth pinning: on a per-delta-dollar basis a 3-DTE ATM
        SPY call is CHEAPER than the 30 bps equity benchmark, while its daily
        decay is an order of magnitude larger than any equity cost. Both halves
        must hold, or the module is telling only one side of the story."""
        spot = float(self.snap["spot"]["last_trade_price"])
        near = [analyse(c, spot) for c in self.snap["contracts"]
                if c["exp"] == "2026-08-03"]
        self.assertTrue(near)
        for r in near:
            self.assertLess(r["delta_bps"], EQUITY_ROUND_TRIP_BPS)
            # measured range on this snapshot is 474 (deep ITM) to 3967 (OTM)
            # bps/day; the deep-ITM contract sets the floor because its premium
            # is mostly intrinsic and therefore does not decay
            self.assertGreater(r["theta_bps"], 10 * EQUITY_ROUND_TRIP_BPS)


if __name__ == "__main__":
    unittest.main()
