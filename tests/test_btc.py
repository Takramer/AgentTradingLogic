"""BTC feature construction and scoring. No network."""
import math
import unittest

from rhengine.btc import (
    DIGITAL_PRICE, FEATURES, HALF_SPREAD, _rank, _rsi, build, net_hit_rate,
    spearman,
)


def panel(closes, start=1_700_000_000):
    """[(ts, low, high, open, close, volume)] with a flat 1h grid."""
    return [(start + i * 3600, c * 0.99, c * 1.01, c, c, 100.0 + i)
            for i, c in enumerate(closes)]


class TestRank(unittest.TestCase):
    def test_ties_get_the_average_rank(self):
        self.assertEqual(_rank([5, 5, 9]), [1.5, 1.5, 3.0])

    def test_plain_order(self):
        self.assertEqual(_rank([3, 1, 2]), [3.0, 1.0, 2.0])


class TestRsi(unittest.TestCase):
    def test_all_gains_is_one_hundred(self):
        self.assertEqual(_rsi(list(range(1, 30)), 14), 100.0)

    def test_needs_enough_history(self):
        self.assertIsNone(_rsi([1, 2, 3], 14))

    def test_bounded(self):
        v = _rsi([10, 11, 9, 12, 8, 13, 7, 14, 6, 15, 5, 16, 4, 17, 3], 14)
        self.assertGreaterEqual(v, 0.0)
        self.assertLessEqual(v, 100.0)


class TestSpearman(unittest.TestCase):
    def test_perfect_monotone(self):
        ic, _ = spearman(list(range(50)), list(range(50)))
        self.assertAlmostEqual(ic, 1.0, places=9)

    def test_sign_and_t_agree(self):
        # Imperfect on purpose: a perfect |IC| = 1 leaves t undefined, which
        # spearman() reports as None rather than dividing by zero.
        y = list(range(60, 0, -1))
        y[0], y[5] = y[5], y[0]
        ic, t = spearman(list(range(60)), y)
        self.assertLess(ic, 0)
        self.assertLess(t, 0)

    def test_perfect_correlation_has_no_t(self):
        ic, t = spearman(list(range(60)), list(range(60)))
        self.assertAlmostEqual(ic, 1.0, places=9)
        self.assertIsNone(t)

    def test_too_few_points(self):
        self.assertEqual(spearman([1, 2, 3], [1, 2, 3]), (None, None))

    def test_constant_input(self):
        self.assertEqual(spearman([1] * 50, list(range(50))), (None, None))


class TestBuildNoLookahead(unittest.TestCase):
    def test_target_is_the_next_bar_only(self):
        closes = [100.0 * (1.001 ** i) for i in range(200)]
        rows = build(panel(closes))
        self.assertTrue(rows)
        ts0, _, y0 = rows[0]
        # first scored bar is index 168; target is 168 -> 169
        self.assertAlmostEqual(y0, math.log(closes[169] / closes[168]), places=12)

    def test_features_use_only_past_and_present(self):
        # A spike placed AFTER the scored bar must not move its features.
        # The series must actually vary: a flat panel has no 24h range and no
        # realised vol, so every row is dropped for having None features.
        base = [100.0 + (i % 11) * 0.7 for i in range(200)]
        a = build(panel(base))
        spiked = list(base)
        spiked[190] = 500.0
        b = build(panel(spiked))
        self.assertTrue(a)
        self.assertEqual(a[0][1], b[0][1])

    def test_every_declared_feature_is_present(self):
        rows = build(panel([100.0 + i * 0.3 for i in range(200)]))
        self.assertEqual(set(rows[0][1]), set(FEATURES))

    def test_short_panel_yields_nothing(self):
        self.assertEqual(build(panel([100.0] * 50)), [])


class TestNetHitRate(unittest.TestCase):
    def test_bar_is_fee_plus_half_spread(self):
        r = net_hit_rate([float(i) for i in range(400)],
                         [float(i) for i in range(400)], contracts=1000)
        self.assertAlmostEqual(r["bar"], DIGITAL_PRICE + 0.0175 + HALF_SPREAD,
                               places=4)

    def test_sign_flips_the_trade(self):
        # A perfectly negative feature scored long-top reports the LOSING side.
        # This is the bug that showed five signals at 0.46 instead of 0.54.
        # Targets must straddle zero AND be perfectly negatively ordered. An
        # all-negative target makes both orientations score 0.5 and proves
        # nothing. No exact zeros: a 0 target is neither a win nor a loss.
        x = [float(i) for i in range(400)]
        y = [-(float(i) - 199.5) for i in range(400)]
        wrong = net_hit_rate(x, y, sign=1)
        right = net_hit_rate(x, y, sign=-1)
        self.assertAlmostEqual(wrong["hit_rate"] + right["hit_rate"], 1.0, places=9)
        self.assertGreater(right["hit_rate"], wrong["hit_rate"])

    def test_edge_is_measured_against_the_bar_not_a_coin(self):
        x = [float(i) for i in range(400)]
        r = net_hit_rate(x, x, sign=1)
        self.assertAlmostEqual(r["edge_pts"], (r["hit_rate"] - r["bar"]) * 100,
                               places=6)
        self.assertGreater(r["bar"], 0.52)

    def test_no_signal_lands_near_the_coin_and_below_the_bar(self):
        x = [float(i % 7) for i in range(1000)]
        y = [1.0 if i % 2 else -1.0 for i in range(1000)]
        r = net_hit_rate(x, y, sign=1)
        self.assertLess(r["hit_rate"], r["bar"])


class TestCandleAlignment(unittest.TestCase):
    """Regression for the bug that manufactured a 47-62% six-day ROI.

    Coinbase stamps a candle with its bucket START, so closes[T] is the price at
    T+1h. Reading closes[settle-3600] as "spot an hour before settlement"
    actually reads the settlement price -- measured at -0.4 bps (sd 2.7) from
    the BRTI print. The strike was then chosen knowing the answer.
    """

    def test_close_belongs_to_the_end_of_its_bucket(self):
        closes = [100.0, 101.0, 102.0, 103.0]
        p = panel(closes)
        ts0 = p[0][0]
        # the bar stamped ts0 covers [ts0, ts0+3600]; its close is the price at
        # ts0+3600, NOT at ts0.
        by_ts = {r[0]: r[4] for r in p}
        self.assertEqual(by_ts[ts0], closes[0])
        self.assertEqual(by_ts[ts0 + 3600], closes[1])

    def test_decision_key_is_two_hours_before_settlement(self):
        # For a settlement at S the tradable information is as of S-1h, which
        # is the close of the bar STARTING at S-2h.
        settle = 1_700_010_000
        self.assertEqual(settle - 2 * 3600, settle - 7200)

    def test_build_target_is_one_bar_forward_of_its_features(self):
        closes = [100.0 + (i % 13) * 0.9 for i in range(200)]
        rows = build(panel(closes))
        ts, _, y = rows[0]
        i = 168
        self.assertAlmostEqual(y, math.log(closes[i + 1] / closes[i]), places=12)


class TestPreRegistration(unittest.TestCase):
    def test_eleven_features_exactly(self):
        # Adding a twelfth means adding a twelfth ledger line first.
        self.assertEqual(len(FEATURES), 11)
        self.assertEqual(len(set(FEATURES)), 11)


if __name__ == "__main__":
    unittest.main()
