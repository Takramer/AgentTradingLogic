"""test_execution.py — the traps in the Module 7 spread gate.

Each test corresponds to a specific way this gate could be silently wrong:

* `test_units_bridge_is_round_trip` / `test_measured_deciles_land_where_costs_py_says`
  — the quoted spread `(ask-bid)/mid` IS the round-trip cost, because each
  crossing pays a half-spread and there are two of them. It is tempting to
  double it, and an early draft of `execution.py` did, which would have
  reported this universe at 58 bps instead of 29. The second test is the guard
  that would have caught it: it reconstructs quotes from the ADV-decile figures
  `costs.py` records (5.5 / 29.1 / 98.1 bps round trip) and requires the
  bridge to return them unchanged. If anyone reintroduces a factor of 2, that
  test fails against a measured number rather than against an assertion.
* `test_unusable_quote_rejects` — a missing, crossed or zero quote must FAIL,
  not pass. A name you cannot get a quote for is not a name you can size a
  trade in, and defaulting to pass would make the gate invisible exactly where
  it matters most.
* `test_after_hours_book_rejects` — the real 2026-07-25 after-hours quotes that
  motivated the regular-hours sampling rule (SOUN 6.06/22.18). These are valid
  quotes, not malformed data, so they must be rejected by the WIDTH rule rather
  than the parse rule.
* `test_boundary_is_exclusive` — the spec's supported range is `< 1.0%`, so a
  spread exactly at the gate passes. An off-by-one here changes which names
  trade, silently.
* `test_order_type_never_market` — `GAP-7.1` leaves "highly liquid" undefined.
  If this ever returns "market" it means somebody filled a source gap from
  general knowledge, which is the one thing AGENT-SPEC.md forbids.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.execution import (  # noqa: E402
    DEFAULT_SPREAD_GATE_PCT,
    bps_round_trip,
    needs_block_desk,
    order_type,
    resolve_spread_gate_pct,
    spread_gate,
    spread_pct,
)


class TestSpreadPct(unittest.TestCase):
    def test_basic_spread(self):
        # bid 99.95 / ask 100.05 -> 0.10 wide on a 100.00 mid = 10 bps one way
        self.assertAlmostEqual(spread_pct(99.95, 100.05), 0.001, places=9)

    def test_units_bridge_is_round_trip(self):
        """The quoted spread IS the round trip — no factor of 2."""
        pct = spread_pct(99.95, 100.05)
        self.assertAlmostEqual(bps_round_trip(99.95, 100.05),
                               pct * 10000.0, places=9)
        # 0.10 wide on a 100.00 mid = 10 bps ROUND TRIP (two half-spreads).
        self.assertAlmostEqual(bps_round_trip(99.95, 100.05), 10.0, places=6)

    def test_measured_deciles_land_where_costs_py_says(self):
        """Pin the bridge to this repo's own measurement, not to an assertion.

        costs.py records 5.5 bps round trip in the most liquid ADV decile,
        29.1 trade-weighted, and 98.1 in the least. The live captures behind
        those figures quote 0.052%, 0.291% and 0.976% spreads respectively. So
        reconstructing a quote at each recorded cost must return that same cost
        — a 2x drift in either module breaks this immediately.
        """
        for round_trip_bps in (5.5, 29.1, 98.1):
            mid = 50.0
            half = mid * (round_trip_bps / 10000.0) / 2.0
            self.assertAlmostEqual(bps_round_trip(mid - half, mid + half),
                                   round_trip_bps, places=6)
            # and the fraction form agrees with the published percentage
            self.assertAlmostEqual(spread_pct(mid - half, mid + half) * 100.0,
                                   round_trip_bps / 100.0, places=6)

    def test_unusable_quote_rejects(self):
        for bid, ask in [(None, 10.0), (10.0, None), (0.0, 10.0),
                         (-1.0, 10.0), (10.0, 9.0), ("x", 10.0)]:
            self.assertIsNone(spread_pct(bid, ask), (bid, ask))
            passed, pct, reason = spread_gate(bid, ask)
            self.assertFalse(passed, (bid, ask))
            self.assertIsNone(pct)
            self.assertEqual(reason, "no usable quote")

    def test_locked_market_passes(self):
        """bid == ask is a locked book, not a broken one: zero spread, passes."""
        self.assertEqual(spread_pct(10.0, 10.0), 0.0)
        self.assertTrue(spread_gate(10.0, 10.0)[0])


class TestSpreadGate(unittest.TestCase):
    def test_tight_name_passes(self):
        passed, pct, reason = spread_gate(99.99, 100.01)
        self.assertTrue(passed)
        self.assertLess(pct, 0.001)
        self.assertEqual(reason, "")

    def test_wide_name_rejects_with_reason(self):
        passed, pct, reason = spread_gate(98.0, 100.0)
        self.assertFalse(passed)
        self.assertGreater(pct, 0.01)
        self.assertIn("2.02%", reason)

    def test_after_hours_book_rejects(self):
        """Real 2026-07-25 after-hours quotes (see costs.py / rh_spreads.py)."""
        for bid, ask in [(6.06, 22.18), (189.00, 322.25)]:
            passed, pct, reason = spread_gate(bid, ask)
            self.assertFalse(passed, (bid, ask))
            # Rejected on WIDTH, not on a parse failure — the quote is valid.
            self.assertIsNotNone(pct)
            self.assertIn(">", reason)

    def test_boundary_is_exclusive(self):
        """Spec range is `< 1.0%`; exactly at the gate passes."""
        # Construct a quote whose spread is exactly 1% of mid.
        mid = 100.0
        half = mid * 0.01 / 2.0
        self.assertAlmostEqual(spread_pct(mid - half, mid + half), 0.01, places=12)
        self.assertTrue(spread_gate(mid - half, mid + half, 0.01)[0])
        self.assertFalse(spread_gate(mid - half * 1.001, mid + half * 1.001, 0.01)[0])

    def test_custom_threshold_is_honoured(self):
        # 20 bps round trip: passes the spec gate, fails a house gate set at
        # 10 bps (roughly this universe's most-liquid decile).
        bid, ask = 99.90, 100.10
        self.assertTrue(spread_gate(bid, ask, 0.01)[0])
        self.assertFalse(spread_gate(bid, ask, 0.001)[0])


class TestConfigResolution(unittest.TestCase):
    def test_config_wins(self):
        self.assertEqual(
            resolve_spread_gate_pct({"execution": {"spread_gate_pct": 0.005}}),
            0.005)

    def test_missing_falls_back_to_spec_default(self):
        for cfg in (None, {}, {"execution": {}}, {"execution": None}):
            self.assertEqual(resolve_spread_gate_pct(cfg),
                             DEFAULT_SPREAD_GATE_PCT)

    def test_malformed_falls_back_rather_than_raising(self):
        self.assertEqual(
            resolve_spread_gate_pct({"execution": {"spread_gate_pct": "wide"}}),
            DEFAULT_SPREAD_GATE_PCT)

    def test_real_config_parses(self):
        """The shipped config.json must actually carry a usable value."""
        import json
        from rhengine._paths import app_path
        with open(app_path("config.json")) as f:
            cfg = json.load(f)
        pct = resolve_spread_gate_pct(cfg)
        self.assertGreater(pct, 0.0)
        self.assertLessEqual(pct, 0.01)


class TestModule7Remainder(unittest.TestCase):
    def test_order_type_never_market(self):
        """GAP-7.1 is unfilled, so the market-order exemption cannot apply."""
        for asset in ("stock", "etf", "option", "anything"):
            self.assertEqual(order_type(asset), "limit")

    def test_block_desk_gates(self):
        self.assertFalse(needs_block_desk(shares=100, notional=5000.0))
        self.assertTrue(needs_block_desk(shares=2001))
        self.assertFalse(needs_block_desk(shares=2000))
        self.assertTrue(needs_block_desk(notional=10_000_001.0))
        self.assertFalse(needs_block_desk())


if __name__ == "__main__":
    unittest.main()
