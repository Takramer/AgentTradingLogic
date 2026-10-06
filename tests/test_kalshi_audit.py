"""Fee math and the ablation gate. No network."""

import unittest

from rhengine.kalshi_audit import (
    day_block_pnl,
    eligible,
    kalshi_quadratic_fee,
    payoff,
    render_variants,
    trade_fees,
)


class TestFee(unittest.TestCase):
    def test_general_table_rounds_up_to_the_next_cent(self):
        # One contract. 0.07 * 0.50 * 0.50 = 0.0175 -> 0.02.
        self.assertEqual(kalshi_quadratic_fee(0.50), 0.02)
        self.assertEqual(kalshi_quadratic_fee(0.01), 0.01)
        self.assertEqual(kalshi_quadratic_fee(0.80), 0.02)
        self.assertEqual(kalshi_quadratic_fee(0.85), 0.01)
        self.assertEqual(kalshi_quadratic_fee(0.75), 0.02)
        # One hundred contracts. 0.50 is exact. 0.75 is 1.3125 -> 1.32.
        self.assertEqual(kalshi_quadratic_fee(0.50, contracts=100), 1.75)
        self.assertEqual(kalshi_quadratic_fee(0.80, contracts=100), 1.12)
        self.assertEqual(kalshi_quadratic_fee(0.75, contracts=100), 1.32)
        self.assertEqual(kalshi_quadratic_fee(0.0), 0.0)
        self.assertEqual(kalshi_quadratic_fee(1.0), 0.0)

    def test_settlement_charges_the_entry_only_and_a_resting_take_profit_is_free(self):
        held = {"entry": 0.80, "exit": 1.0, "reason": "settlement"}
        stopped = {"entry": 0.80, "exit": 0.25, "reason": "stop"}
        won = {"entry": 0.80, "exit": 0.90, "reason": "take_profit"}
        self.assertEqual(trade_fees(held), 0.02)
        self.assertEqual(trade_fees(stopped), 0.04)
        self.assertEqual(trade_fees(won), 0.03)
        self.assertEqual(trade_fees(won, maker_take_profit=True), 0.02)


class TestPayoff(unittest.TestCase):
    def test_eighty_percent_winners_still_lose_when_the_loss_is_five_times_the_win(self):
        trades = [{"pnl": 0.10}] * 80 + [{"pnl": -0.55}] * 20
        stats = payoff(trades)
        self.assertAlmostEqual(stats["expectancy"], 0.08 - 0.11)
        self.assertAlmostEqual(stats["breakeven_win_rate"], 0.55 / 0.65)
        self.assertLess(stats["gap"], 0)
        self.assertAlmostEqual(stats["pnl"], 80 * 0.10 + 20 * -0.55)

    def test_a_loss_that_beats_the_baseline_is_not_labeled_as_a_promotion(self):
        text = render_variants([{
            "label": "published rules",
            "screen_pnl": -10, "screen_trades": 200,
            "confirm_pnl": -10, "confirm_trades": 200,
            "eligible": False, "sensitivity": False,
        }, {
            "label": "lower band",
            "screen_pnl": -4, "screen_trades": 200,
            "confirm_pnl": -3, "confirm_trades": 200,
            "eligible": True, "sensitivity": False,
        }])
        self.assertIn("beats baseline, still a loss", text)
        self.assertNotIn("ELIGIBLE", text)

    def test_a_variant_must_beat_both_halves_and_have_a_hundred_trades(self):
        self.assertTrue(eligible(-10, -20, 200, 200, -30, -40))
        self.assertFalse(eligible(-10, -50, 200, 200, -30, -40))
        self.assertFalse(eligible(1, 1, 50, 200, 0, 0))

    def test_day_block_of_equal_days_stays_on_the_total(self):
        trades = [
            {"close_time": "2026-04-{:02d}T00:00:00Z".format(day), "pnl": 1.0}
            for day in range(1, 11)
        ]
        band = day_block_pnl(trades, draws=200, seed=1)
        self.assertEqual(band["days"], 10)
        self.assertEqual(band["pnl"], 10)
        self.assertEqual(band["lo"], 10)
        self.assertEqual(band["hi"], 10)
        again = day_block_pnl(
            [{"close_time": "2026-04-01T00:00:00Z", "pnl": 5.0},
             {"close_time": "2026-04-02T00:00:00Z", "pnl": -1.0}],
            draws=50, seed=7)
        self.assertEqual(again, day_block_pnl(
            [{"close_time": "2026-04-01T00:00:00Z", "pnl": 5.0},
             {"close_time": "2026-04-02T00:00:00Z", "pnl": -1.0}],
            draws=50, seed=7))


if __name__ == "__main__":
    unittest.main()
