"""test_options_vrp.py — the traps in the VRP measurement kit.

Each test corresponds to a way this could be silently wrong. A VRP study is a
measurement, so an error here does not show up as a crash — it shows up as a
plausible-looking premium that is not there.

* `test_put_call_parity` — the strongest available check on `bs_price` that
  does not require trusting a hand-copied reference value. Parity must hold to
  machine precision at every moneyness and both rate/dividend settings, or d1,
  d2, the discounting or the dividend adjustment is wrong somewhere.
* `test_round_trip_recovers_sigma` — price at a known sigma, invert, get it
  back. This is the test that would catch a sign error in d1 or a bracket bug.
* `test_unresolvable_returns_none` — a price at or below intrinsic carries NO
  volatility information. Returning a number there (0, or a clamped bound) is
  the dangerous failure: it looks like data and would drag any average VRP.
* `test_monotone_in_sigma` — bisection is only valid because BS price rises
  strictly with sigma. If that ever fails the inverter returns garbage rather
  than failing, so it is asserted directly.
* `test_vrp_sign_convention` — positive VRP must mean the SELLER was paid.
  A flipped sign would invert the conclusion of the entire study.
* `test_realised_vol_against_known_series` — a constant-growth series has zero
  variance and therefore zero vol, whatever its drift. Catches the classic bug
  of computing vol on prices instead of log returns.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.market_data_options import (  # noqa: E402
    bs_price,
    has_vol_information,
    implied_vol,
    realised_vol,
    vrp,
    year_fraction,
)


class TestBlackScholes(unittest.TestCase):
    def test_put_call_parity(self):
        """C - P = S*e^-qT - K*e^-rT, exactly, everywhere."""
        for S in (50.0, 100.0, 180.0):
            for K in (80.0, 100.0, 120.0):
                for T in (0.05, 0.5, 2.0):
                    for r, q in ((0.0, 0.0), (0.04, 0.0), (0.04, 0.02)):
                        c = bs_price(S, K, T, r, 0.28, "call", q)
                        p = bs_price(S, K, T, r, 0.28, "put", q)
                        self.assertAlmostEqual(
                            c - p,
                            S * math.exp(-q * T) - K * math.exp(-r * T),
                            places=10, msg=(S, K, T, r, q))

    def test_monotone_in_sigma(self):
        """Bisection is only sound because price strictly increases in sigma."""
        for kind in ("call", "put"):
            prev = -1.0
            for sigma in [0.01 * i for i in range(1, 200)]:
                px = bs_price(100.0, 110.0, 0.75, 0.03, sigma, kind, 0.01)
                self.assertGreater(px, prev, (kind, sigma))
                prev = px

    def test_zero_vol_is_discounted_intrinsic(self):
        c = bs_price(120.0, 100.0, 1.0, 0.05, 0.0, "call")
        self.assertAlmostEqual(c, 120.0 - 100.0 * math.exp(-0.05), places=10)
        p = bs_price(80.0, 100.0, 1.0, 0.05, 0.0, "put")
        self.assertAlmostEqual(p, 100.0 * math.exp(-0.05) - 80.0, places=10)

    def test_rejects_bad_kind(self):
        with self.assertRaises(ValueError):
            bs_price(100.0, 100.0, 1.0, 0.0, 0.2, "straddle")


class TestImpliedVol(unittest.TestCase):
    def test_round_trip_recovers_sigma(self):
        """Price at a known sigma, invert, get it back — wherever a price exists.

        The contract is two-sided, and both halves matter:

          * where the price carries time value, inversion must recover sigma;
          * where it does not, inversion must return None rather than a
            fabricated vol.

        Two distinct ways the second case arises, both present in this grid.
        A far-OTM short-dated option prices to EXACTLY 0.0 in float (math.erf
        saturates past |x|~6, so both normal CDFs return a hard zero). A deep
        ITM short-dated one prices to intrinsic plus ~1e-12 on a ~30 handle,
        which is float noise. `has_vol_information` is the single predicate
        for both, and the test asks it rather than restating a threshold — so
        the module and the test cannot drift apart on where the floor is.
        """
        checked = 0
        for S, K in ((100.0, 100.0), (100.0, 130.0), (100.0, 70.0)):
            for T in (0.02, 0.25, 1.5):
                for kind in ("call", "put"):
                    for sigma in (0.12, 0.35, 0.90):
                        case = (S, K, T, kind, sigma)
                        px = bs_price(S, K, T, 0.04, sigma, kind, 0.01)
                        got = implied_vol(px, S, K, T, 0.04, kind, 0.01)
                        intrinsic = bs_price(S, K, T, 0.04, 0.0, kind, 0.01)
                        if not has_vol_information(px, intrinsic):
                            self.assertIsNone(got, case)
                            continue
                        self.assertIsNotNone(got, case)
                        self.assertAlmostEqual(got, sigma, places=5, msg=case)
                        checked += 1
        # Guard the guard: most cases must be real inversions, or this test
        # could pass by short-circuiting everything into the None branch.
        self.assertGreater(checked, 40)

    def test_unresolvable_returns_none(self):
        S, K, T, r = 100.0, 100.0, 0.5, 0.04
        intrinsic = max(0.0, S - K * math.exp(-r * T))
        # At or below intrinsic: no volatility information exists.
        self.assertIsNone(implied_vol(intrinsic, S, K, T, r, "call"))
        self.assertIsNone(implied_vol(intrinsic * 0.5, S, K, T, r, "call"))
        # At or above the theoretical max.
        self.assertIsNone(implied_vol(S, S, K, T, r))
        self.assertIsNone(implied_vol(S * 2, S, K, T, r, "call"))
        # Degenerate inputs.
        self.assertIsNone(implied_vol(5.0, S, K, 0.0, r, "call"))
        self.assertIsNone(implied_vol(5.0, S, K, -1.0, r, "call"))
        self.assertIsNone(implied_vol(None, S, K, T, r, "call"))
        self.assertIsNone(implied_vol(-1.0, S, K, T, r, "call"))

    def test_cheap_option_inverts_precisely(self):
        """Regression: convergence must be on the vol bracket, not the price.

        S=100 K=130 T=0.02 sigma=0.35 prices to ~1e-5. With an absolute price
        tolerance of 1e-8 the inverter returned 0.3517 — off by 0.17 vol points,
        which is a twentieth of the entire published VRP. Cheap far-from-money
        contracts are precisely where a put-side VRP study lives, so this is
        not an academic corner.
        """
        S, K, T, sigma = 100.0, 130.0, 0.02, 0.35
        px = bs_price(S, K, T, 0.04, sigma, "call", 0.01)
        self.assertGreater(px, 0.0)
        self.assertLess(px, 0.01)             # genuinely a cheap contract
        got = implied_vol(px, S, K, T, 0.04, "call", 0.01)
        self.assertAlmostEqual(got, sigma, places=8)

    def test_never_clamps_to_a_bound(self):
        """A vol above the 500% bracket must be None, not 5.0.

        A clamped value is the dangerous outcome: it is a plausible number
        that was never measured.
        """
        S, K, T = 100.0, 100.0, 1.0
        px_at_hi = bs_price(S, K, T, 0.0, 6.0, "call")
        got = implied_vol(px_at_hi, S, K, T, 0.0, "call")
        self.assertIsNone(got)

    def test_real_contract_shape(self):
        """A plausible AAPL-like deep-ITM call inverts to a sane vol.

        Modelled on the spiked contract (250 strike, spot ~335, ~50 days).
        Deep ITM is where inversion is hardest — almost all the price is
        intrinsic — so this is the case most likely to return None wrongly.
        """
        iv = implied_vol(price=86.0, S=334.0, K=250.0, T=50 / 365.0,
                         r=0.04, kind="call", q=0.004)
        self.assertIsNotNone(iv)
        self.assertGreater(iv, 0.01)
        self.assertLess(iv, 3.0)


class TestRealisedVol(unittest.TestCase):
    def test_realised_vol_against_known_series(self):
        """Constant growth = zero variance = zero vol, regardless of drift."""
        closes = [100.0 * (1.01 ** i) for i in range(30)]
        self.assertAlmostEqual(realised_vol(closes), 0.0, places=10)

    def test_known_daily_sigma_annualises(self):
        """A series with a known log-return sd must annualise by sqrt(252).

        Note the Bessel correction: `realised_vol` uses the SAMPLE sd (ddof=1),
        which is the right choice for an estimate, so a perfectly alternating
        +-s series of n returns has sd = s*sqrt(n/(n-1)), not s. Writing the
        expectation without that factor is how you convince yourself the module
        is off by 0.4% when it is exact.
        """
        daily_sd, n = 0.02, 120
        rets = [daily_sd, -daily_sd] * (n // 2)
        closes = [100.0]
        for r in rets:
            closes.append(closes[-1] * math.exp(r))
        expected = daily_sd * math.sqrt(n / (n - 1.0)) * math.sqrt(252)
        self.assertAlmostEqual(realised_vol(closes), expected, places=10)

    def test_insufficient_or_bad_data_is_none(self):
        self.assertIsNone(realised_vol([]))
        self.assertIsNone(realised_vol([100.0]))
        self.assertIsNone(realised_vol([100.0, 101.0]))
        self.assertIsNone(realised_vol([100.0, 0.0, 101.0]))
        self.assertIsNone(realised_vol([100.0, None, 101.0]))
        self.assertIsNone(realised_vol(None))


class TestVrp(unittest.TestCase):
    def test_vrp_sign_convention(self):
        """Positive VRP = implied exceeded realised = the SELLER was paid."""
        self.assertAlmostEqual(vrp(0.30, 0.26), 0.04, places=12)
        self.assertLess(vrp(0.20, 0.35), 0.0)

    def test_none_propagates(self):
        self.assertIsNone(vrp(None, 0.2))
        self.assertIsNone(vrp(0.2, None))
        self.assertIsNone(vrp(None, None))

    def test_published_magnitude_is_in_range(self):
        """The literature quotes 2-4 vol points on SPX. Check the units.

        If vrp() ever returned percentages rather than decimals this would
        be 200-400 and the check would fail — which is the point.
        """
        v = vrp(0.185, 0.155)
        self.assertGreater(v * 100.0, 2.0)
        self.assertLess(v * 100.0, 4.0)


class TestYearFraction(unittest.TestCase):
    def test_calendar_basis(self):
        day = 86_400_000
        self.assertAlmostEqual(year_fraction(0, 365 * day), 1.0, places=12)
        self.assertAlmostEqual(year_fraction(0, 30 * day), 30 / 365.0, places=12)

    def test_non_positive_span_is_none(self):
        self.assertIsNone(year_fraction(1000, 1000))
        self.assertIsNone(year_fraction(2000, 1000))
        self.assertIsNone(year_fraction(None, 1000))


if __name__ == "__main__":
    unittest.main()
