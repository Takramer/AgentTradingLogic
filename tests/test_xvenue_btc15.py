"""Pure-function tests for the 15-minute BTC cross-venue logger. No network."""
import unittest

from rhengine.xvenue_btc15 import (
    GATE_MIN_WINDOWS, cross_edges, evaluate, kalshi_outcome, kalshi_quote,
    pending_windows, pick_kalshi_market, poly_fee, poly_internal, poly_outcome,
    poly_slug, poly_tokens, snapshot_row, window_bounds,
)

# 2026-10-09 21:30:00 UTC, a window boundary.
T0 = 1791581400


def kq(up=0.50, down=0.51, up_sz=500, down_sz=500):
    return {"ticker": "KXBTC15M-X", "up_ask": up, "down_ask": down,
            "up_ask_size": up_sz, "down_ask_size": down_sz}


def pq(up=0.50, down=0.51, up_sz=500, down_sz=500):
    return {"slug": "s", "up_ask": up, "down_ask": down,
            "up_ask_size": up_sz, "down_ask_size": down_sz}


class TestGrid(unittest.TestCase):
    def test_bounds_snap_to_quarter_hour(self):
        self.assertEqual(window_bounds(T0 + 899), (T0, T0 + 900))
        self.assertEqual(window_bounds(T0 + 900), (T0 + 900, T0 + 1800))

    def test_slug_is_window_start(self):
        self.assertEqual(poly_slug(T0), f"btc-updown-15m-{T0}")

    def test_kalshi_market_matched_on_close_time(self):
        ms = [{"ticker": "a", "close_time": "2026-10-09T21:30:00Z"},
              {"ticker": "b", "close_time": "2026-10-09T21:45:00Z"}]
        self.assertEqual(pick_kalshi_market(ms, T0 + 900)["ticker"], "b")
        self.assertIsNone(pick_kalshi_market(ms, T0 + 1800))


class TestParsers(unittest.TestCase):
    def test_kalshi_no_ask_inherits_yes_bid_size(self):
        q = kalshi_quote({"ticker": "t", "yes_ask_dollars": "0.52",
                          "no_ask_dollars": "0.49", "yes_ask_size_fp": "10",
                          "yes_bid_size_fp": "30", "floor_strike": 62000})
        self.assertEqual((q["up_ask"], q["down_ask"]), (0.52, 0.49))
        self.assertEqual((q["up_ask_size"], q["down_ask_size"]), (10.0, 30.0))

    def test_poly_tokens_follow_outcome_labels_not_order(self):
        raw = {"outcomes": '["Down", "Up"]', "clobTokenIds": '["d1", "u1"]'}
        self.assertEqual(poly_tokens(raw), {"Up": "u1", "Down": "d1"})

    def test_poly_tokens_refuse_other_markets(self):
        self.assertIsNone(poly_tokens({"outcomes": '["Yes","No"]',
                                       "clobTokenIds": '["a","b"]'}))

    def test_outcomes(self):
        self.assertEqual(kalshi_outcome({"result": "yes"}), "up")
        self.assertEqual(kalshi_outcome({"result": "no"}), "down")
        self.assertIsNone(kalshi_outcome({"result": ""}))
        raw = {"closed": True, "outcomes": '["Up","Down"]',
               "outcomePrices": '["0","1"]'}
        self.assertEqual(poly_outcome(raw), "down")
        self.assertIsNone(poly_outcome({**raw, "closed": False}))
        self.assertIsNone(poly_outcome({**raw, "outcomePrices": '["0.5","0.5"]'}))


class TestFeesAndEdge(unittest.TestCase):
    def test_poly_fee_peaks_at_175c_per_100(self):
        self.assertAlmostEqual(poly_fee(0.50, 100), 1.75, places=9)
        self.assertLess(poly_fee(0.10, 100), poly_fee(0.50, 100))

    def test_coin_flip_pair_needs_3_5c_of_room(self):
        # 0.48 + 0.48 = 0.96 leaves 4c gross, about 3.5c of fees: barely positive.
        e = cross_edges(kq(up=0.48), pq(down=0.48))["A"]
        self.assertGreater(e["net_edge"], 0)
        self.assertLess(e["net_edge"], 0.006)
        # 0.49 + 0.49 leaves 2c gross: negative after fees.
        self.assertLess(cross_edges(kq(up=0.49), pq(down=0.49))["A"]["net_edge"], 0)

    def test_depth_is_thinner_leg(self):
        e = cross_edges(kq(up=0.40, up_sz=20), pq(down=0.40, down_sz=900))["A"]
        self.assertEqual(e["size"], 20)
        self.assertAlmostEqual(e["depth_usd"], 16.0)

    def test_missing_or_dead_quote_is_none(self):
        self.assertIsNone(cross_edges(kq(up=None), pq())["A"])
        self.assertIsNone(cross_edges(kq(up=1.0), pq())["A"])

    def test_poly_internal(self):
        self.assertLess(poly_internal(pq(up=0.50, down=0.51)), 0)

    def test_snapshot_picks_best_construction(self):
        row = snapshot_row(kq(up=0.60, down=0.45), pq(up=0.45, down=0.60),
                           {"start": T0, "end": T0 + 900, "ts": T0 + 10,
                            "secs_to_close": 890, "skew_s": 0.2})
        self.assertEqual(row["best"], "B")


def quote(start, edge_a, ts_off=10, skew=0.1, depth=500):
    cost = 1 - edge_a
    return {"start": start, "ts": start + ts_off, "skew_s": skew,
            "kalshi": {"ticker": f"K{start}"},
            "edges": {"A": {"net_edge": edge_a, "cost": cost, "depth_usd": depth},
                      "B": None}}


def outcome(start, k, p):
    return {"start": start, "kalshi": k, "poly": p}


class TestEvaluate(unittest.TestCase):
    def test_disagreement_turns_a_positive_edge_into_a_loss(self):
        # 2c edge every window, but the venues disagree adversely 1 time in 10:
        # Kalshi says DOWN (our UP leg loses) and Polymarket says UP (our DOWN
        # leg loses). The paper P&L must show the loss the quotes cannot.
        qs, os_ = [], []
        for i in range(10):
            s = T0 + 900 * i
            qs.append(quote(s, 0.02))
            os_.append(outcome(s, "down", "up") if i == 0 else outcome(s, "up", "up"))
        r = evaluate(qs, os_)
        self.assertAlmostEqual(r["disagree_rate"], 0.10)
        self.assertAlmostEqual(r["paper_pnl_per_pair_total"], 9 * 0.02 - 0.98, places=6)
        self.assertFalse(r["gates"]["median_edge>disagree"])
        self.assertEqual(r["verdict"], "DOES NOT CLEAR")

    def test_skewed_or_thin_quotes_do_not_count(self):
        qs = [quote(T0, 0.05, skew=5.0), quote(T0 + 900, 0.05, depth=10)]
        r = evaluate(qs, [outcome(T0, "up", "up"), outcome(T0 + 900, "up", "up")])
        self.assertEqual(r["windows_with_edge"], 0)

    def test_first_usable_quote_is_the_paper_trade(self):
        qs = [quote(T0, 0.01, ts_off=5), quote(T0, 0.04, ts_off=50)]
        r = evaluate(qs, [outcome(T0, "up", "up")])
        self.assertAlmostEqual(r["paper_pnl_per_pair_total"], 0.01, places=6)
        self.assertAlmostEqual(r["median_best_edge"], 0.04)

    def test_small_sample_never_clears(self):
        qs = [quote(T0 + 900 * i, 0.05) for i in range(20)]
        os_ = [outcome(T0 + 900 * i, "up", "up") for i in range(20)]
        r = evaluate(qs, os_)
        self.assertFalse(r["gates"][f"windows>={GATE_MIN_WINDOWS}"])
        self.assertEqual(r["verdict"], "DOES NOT CLEAR")

    def test_pending_windows_wait_for_grace(self):
        qs = [quote(T0, 0.0), quote(T0 + 900, 0.0)]
        p = pending_windows(qs, [outcome(T0, "up", "up")], now=T0 + 1800 + 301)
        self.assertEqual(p, {T0 + 900: f"K{T0 + 900}"})
        self.assertEqual(pending_windows(qs, [], now=T0 + 1000), {})

    def test_incomplete_outcome_row_is_not_retried_or_scored(self):
        qs = [quote(T0, 0.05)]
        stuck = [{"start": T0, "kalshi": "up", "poly": None}]
        self.assertEqual(pending_windows(qs, stuck, now=T0 + 99999), {})
        self.assertEqual(evaluate(qs, stuck)["windows"], 0)


if __name__ == "__main__":
    unittest.main()
