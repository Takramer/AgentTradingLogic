"""Crypto pricing math: digitals, barriers, vol fitting, fee arithmetic. No network."""
import math
import unittest

from rhengine.crypto import digital, fit_sigma, one_touch_up, updown_breakeven


class TestDigital(unittest.TestCase):
    def test_atm_is_near_a_half(self):
        self.assertAlmostEqual(digital(100, 100, 0.01, 1.0), 0.5, places=2)

    def test_monotone_decreasing_in_strike(self):
        ps = [digital(100, k, 0.5, 0.1) for k in range(80, 121, 5)]
        self.assertEqual(ps, sorted(ps, reverse=True))

    def test_zero_time_is_a_step(self):
        self.assertEqual(digital(100, 90, 0.5, 0.0), 1.0)
        self.assertEqual(digital(100, 110, 0.5, 0.0), 0.0)

    def test_vol_pushes_otm_up_and_itm_down(self):
        # The reason a signed error sum is not monotone in sigma.
        self.assertGreater(digital(100, 120, 0.8, 0.1), digital(100, 120, 0.3, 0.1))
        self.assertLess(digital(100, 80, 0.8, 0.1), digital(100, 80, 0.3, 0.1))


class TestOneTouch(unittest.TestCase):
    def test_roughly_twice_the_digital(self):
        # Reflection principle. Reproduced from live Kalshi prices at 2.03-2.06.
        for B in (105, 110, 120):
            r = one_touch_up(100, B, 0.6, 0.05) / digital(100, B, 0.6, 0.05)
            self.assertGreater(r, 1.8)
            self.assertLess(r, 2.2)

    def test_unreachable_barrier_is_near_zero(self):
        # The sign-flip regression: a flipped second term returned ~0.97 here
        # and manufactured a 76-cent edge on a barrier spot cannot reach.
        self.assertLess(one_touch_up(77000, 130000, 0.05, 0.027), 1e-6)

    def test_barrier_at_or_below_spot_is_certain(self):
        self.assertEqual(one_touch_up(100, 100, 0.5, 0.1), 1.0)
        self.assertEqual(one_touch_up(100, 90, 0.5, 0.1), 1.0)

    def test_monotone_in_barrier_and_in_vol(self):
        self.assertGreater(one_touch_up(100, 105, 0.5, 0.1),
                           one_touch_up(100, 115, 0.5, 0.1))
        self.assertGreater(one_touch_up(100, 110, 0.9, 0.1),
                           one_touch_up(100, 110, 0.3, 0.1))

    def test_never_exceeds_one(self):
        self.assertLessEqual(one_touch_up(100, 100.01, 3.0, 5.0), 1.0)


class TestFitSigma(unittest.TestCase):
    def _ladder(self, S, sigma, T, strikes):
        return [(k, digital(S, k, sigma, T)) for k in strikes]

    def test_recovers_a_known_sigma(self):
        S, sigma, T = 77000.0, 0.52, 159 / (24 * 365.0)
        pts = self._ladder(S, sigma, T, range(72000, 83001, 500))
        self.assertAlmostEqual(fit_sigma(pts, S, T), sigma, places=2)

    def test_recovers_across_horizons(self):
        # The bug this guards: root-finding on the signed error railed the fit
        # to its bound and reported 500% implied vol.
        S = 77000.0
        for hours, sigma in ((1, 0.61), (15, 0.48), (159, 0.52)):
            T = hours / (24 * 365.0)
            w = S * sigma * math.sqrt(T)
            strikes = [S + w * f for f in (-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2)]
            got = fit_sigma(self._ladder(S, sigma, T, strikes), S, T)
            self.assertLess(abs(got - sigma) / sigma, 0.05,
                            "{}h: got {} want {}".format(hours, got, sigma))

    def test_degenerate_inputs(self):
        self.assertIsNone(fit_sigma([], 100, 0.1))
        self.assertIsNone(fit_sigma([(100, 0.5)], 100, 0.0))


class TestUpDownFees(unittest.TestCase):
    def test_the_house_edge_at_the_midpoint(self):
        r = updown_breakeven(0.50, contracts=1000)
        self.assertAlmostEqual(r["fee_per_contract"], 0.0175, places=4)
        self.assertAlmostEqual(r["breakeven_hit_rate"], 0.5175, places=4)
        self.assertAlmostEqual(r["edge_needed_pts"], 1.75, places=2)

    def test_fee_share_of_stake_grows_as_price_falls(self):
        rich = updown_breakeven(0.50, 1000)["fee_pct_of_stake"]
        cheap = updown_breakeven(0.10, 1000)["fee_pct_of_stake"]
        self.assertGreater(cheap, rich)

    def test_breakeven_always_above_price(self):
        for px in (0.1, 0.25, 0.5, 0.75, 0.9):
            r = updown_breakeven(px, 1000)
            self.assertGreater(r["breakeven_hit_rate"], px)


class TestLadderArbLogic(unittest.TestCase):
    def test_the_nested_hedge_pays_at_least_one(self):
        # buy YES at K_lo, NO at K_hi (K_hi > K_lo). Worst case is 1 by logic.
        k_lo, k_hi = 76000, 78000
        for S in (75000, 77000, 79000):
            yes_lo = 1.0 if S > k_lo else 0.0
            no_hi = 0.0 if S > k_hi else 1.0
            self.assertGreaterEqual(yes_lo + no_hi, 1.0)

    def test_profit_is_the_bound_violation(self):
        bid_hi, ask_lo = 0.62, 0.55        # higher strike bid above lower strike ask
        cost = ask_lo + (1.0 - bid_hi)
        self.assertAlmostEqual(1.0 - cost, bid_hi - ask_lo, places=9)


if __name__ == "__main__":
    unittest.main()
