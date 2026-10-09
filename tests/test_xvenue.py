"""Pure-function tests for the cross-venue scanner. No network."""
import unittest
from datetime import datetime, timedelta, timezone

from rhengine.xvenue import (
    _best, annualized, breakeven_divergence, kalshi_fee, relation_edge, report,
)
from rhengine.xvenue_relations import EQUIVALENT


def edge(kq, pq, **kw):
    """Price a pair as EQUIVALENT. A test-local convenience -- production code
    must name the relation, because assuming EQUIVALENT is the bug that
    reported +24c on 'will X run' against 'will X win'."""
    return relation_edge(kq, pq, EQUIVALENT, **kw)


class TestKalshiFee(unittest.TestCase):
    def test_peaks_at_the_midpoint(self):
        mid = kalshi_fee(0.50, 1000)
        self.assertGreater(mid, kalshi_fee(0.10, 1000))
        self.assertGreater(mid, kalshi_fee(0.90, 1000))

    def test_theoretical_rate_at_size(self):
        # 0.07 * 1000 * 0.5 * 0.5 = 17.50 exactly, no rounding slack.
        self.assertAlmostEqual(kalshi_fee(0.50, 1000), 17.50, places=6)
        self.assertAlmostEqual(kalshi_fee(0.50, 1000) / 1000, 0.0175, places=6)

    def test_ceiling_punishes_small_orders(self):
        # One contract pays 2c on a 1.75c theoretical fee -- a 14% overpay that
        # only disappears at size. This is why --contracts exists.
        self.assertAlmostEqual(kalshi_fee(0.50, 1), 0.02, places=6)
        self.assertGreater(kalshi_fee(0.50, 1) / 1, kalshi_fee(0.50, 1000) / 1000)

    def test_degenerate_inputs(self):
        self.assertEqual(kalshi_fee(None, 100), 0.0)
        self.assertEqual(kalshi_fee(0.5, 0), 0.0)
        self.assertEqual(kalshi_fee(1.7, 100), 0.0)


class TestBreakeven(unittest.TestCase):
    def test_equals_the_net_edge(self):
        for e in (0.005, 0.03, 0.12):
            self.assertAlmostEqual(breakeven_divergence(e), e, places=9)

    def test_identity_holds_on_a_real_row(self):
        # cost + net_edge == 1 by construction, which is what makes q* == edge.
        row = edge({"yes_ask": 0.42, "yes_ask_size": 500, "no_ask": 0.60,
                    "no_ask_size": 500, "close_time": None},
                   {"yes_bid": 0.47, "yes_bid_size": 500, "yes_ask": 0.49,
                    "yes_ask_size": 500, "no_ask": 0.53, "no_ask_size": 500},
                   contracts=1000)
        self.assertAlmostEqual(row["cost"] + row["net_edge"], 1.0, places=4)
        self.assertAlmostEqual(row["breakeven_divergence"], row["net_edge"], places=4)


class TestAnnualized(unittest.TestCase):
    def test_one_year_hold_is_the_raw_return(self):
        self.assertAlmostEqual(annualized(0.03, 0.97, 365), 0.03 / 0.97, places=6)

    def test_short_hold_beats_long_hold(self):
        self.assertGreater(annualized(0.03, 0.97, 30), annualized(0.03, 0.97, 270))

    def test_unusable_inputs(self):
        self.assertIsNone(annualized(0.03, 0.97, 0))
        self.assertIsNone(annualized(0.03, 0.0, 30))
        self.assertIsNone(annualized(0.03, 0.97, None))


class TestBestOfBook(unittest.TestCase):
    def test_ignores_api_ordering(self):
        lv = [{"price": "0.30", "size": "10"}, {"price": "0.45", "size": "7"},
              {"price": "0.12", "size": "99"}]
        self.assertEqual(_best(lv, "bid"), (0.45, 7.0))
        self.assertEqual(_best(lv, "ask"), (0.12, 99.0))

    def test_empty_book(self):
        self.assertEqual(_best([], "bid"), (None, 0.0))
        self.assertEqual(_best(None, "ask"), (None, 0.0))


class TestEdge(unittest.TestCase):
    def _pair(self, k_yes_ask, k_no_ask, p_yes_bid, p_yes_ask, days=90):
        close = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        # Polymarket runs a separate book per outcome token, so the NO side is
        # its own quote. merge_poly() supplies it in the live path; the
        # complement is only a fallback, so fixtures carry both sides.
        return ({"yes_ask": k_yes_ask, "yes_ask_size": 400, "no_ask": k_no_ask,
                 "no_ask_size": 400, "close_time": close},
                {"yes_bid": p_yes_bid, "yes_bid_size": 250,
                 "yes_ask": p_yes_ask, "yes_ask_size": 250,
                 "no_ask": round(1.0 - p_yes_bid, 4), "no_ask_size": 250,
                 "no_bid": round(1.0 - p_yes_ask, 4)})

    def test_fee_can_flip_a_positive_gross_edge_negative(self):
        # 1c gross gap at P=0.5 is eaten whole by a 1.75c taker fee.
        k, p = self._pair(0.50, 0.51, 0.51, 0.52)
        row = edge(k, p, contracts=1000)
        self.assertGreater(row["gross_edge"], 0)
        self.assertLess(row["net_edge"], 0)

    def test_picks_the_better_direction(self):
        k, p = self._pair(0.42, 0.90, 0.47, 0.99)
        row = edge(k, p, contracts=1000)
        self.assertEqual(row["direction"], "kalshi_yes/poly_no")
        self.assertAlmostEqual(row["gross_edge"], 1.0 - (0.42 + 0.53), places=6)

    def test_size_is_the_binding_leg(self):
        k, p = self._pair(0.42, 0.60, 0.47, 0.49)
        self.assertEqual(edge(k, p, contracts=1000)["max_contracts"], 250.0)

    def test_annualized_collapses_on_a_long_hold(self):
        # The same net edge is worth an order of magnitude less when it is
        # locked up for nine months than for one. This is the column that
        # kills most rows, so assert the gap directly.
        k, p = self._pair(0.40, 0.62, 0.47, 0.49, days=270)
        slow = edge(k, p, contracts=1000)
        k, p = self._pair(0.40, 0.62, 0.47, 0.49, days=30)
        fast = edge(k, p, contracts=1000)
        self.assertGreater(slow["net_edge"], 0)
        self.assertAlmostEqual(slow["net_edge"], fast["net_edge"], places=6)
        self.assertLess(slow["ann_return"], 0.10)       # T-bill territory
        self.assertGreater(fast["ann_return"], 5 * slow["ann_return"])

    def test_poly_fee_is_charged(self):
        k, p = self._pair(0.42, 0.60, 0.47, 0.49)
        free = edge(k, p, contracts=1000, poly_fee_bps=0.0)
        paid = edge(k, p, contracts=1000, poly_fee_bps=100.0)
        self.assertLess(paid["net_edge"], free["net_edge"])

    def test_one_sided_or_missing_book_returns_none(self):
        k, p = self._pair(0.42, 0.60, 0.47, 0.49)
        self.assertIsNone(edge(None, p))
        self.assertIsNone(edge(k, None))
        self.assertIsNone(edge({"yes_ask": None, "no_ask": None,
                                "close_time": None},
                               {"yes_bid": None, "yes_ask": None,
                                "no_ask": None}))


class TestReport(unittest.TestCase):
    def test_missing_log_is_empty_not_an_error(self):
        self.assertEqual(report("/nonexistent/xvenue_scan.jsonl"), {})

    def test_hit_rate_and_persistence(self):
        import json, os, tempfile
        rows = [
            {"id": "A", "verified": True, "net_edge": 0.02, "ann_return": 0.1,
             "max_contracts": 100},
            {"id": "A", "verified": True, "net_edge": -0.01, "ann_return": None,
             "max_contracts": 100},
            {"id": "A", "verified": True, "error": "no poly quote"},
            {"id": "B", "verified": False, "net_edge": -0.03, "ann_return": None,
             "max_contracts": 5},
        ]
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        try:
            agg = report(path)
            self.assertEqual(agg["A"]["cycles"], 3)
            self.assertEqual(agg["A"]["quoted"], 2)   # the errored cycle is not a quote
            self.assertEqual(agg["A"]["hits"], 1)
            self.assertAlmostEqual(agg["A"]["hit_rate"], 0.5, places=6)
            self.assertAlmostEqual(agg["A"]["best_net"], 0.02, places=6)
            self.assertEqual(agg["B"]["hits"], 0)
            self.assertFalse(agg["B"]["verified"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
