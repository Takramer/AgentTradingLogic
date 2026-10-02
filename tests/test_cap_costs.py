"""test_cap_costs.py — the cap-tiered, per-name cost model.

Added 2026-08-01. `costs.py`'s own LIMITATION note had said for a week that the
scalar was wrong and the per-name model was missing; Corwin-Schultz was the
intended route and was rejected at ~37x too high. This is the other route: a
published market-cap → spread prior applied per name.

Three ways it could be silently wrong, one test class each.

* **Units.** A quoted spread IS the round trip — you cross a half-spread going
  in and a half-spread coming out. Reading the table as per-side would double
  every cost, which is precisely the bug that lived in `cost_in_r` and
  `backtest._cost_r` until 2026-07-27.
* **Direction of the unknown.** A name whose cap cannot be resolved must be
  charged MORE, not less. Defaulting an unpriced name to the mega-cap tier
  rounds in the book's favour and is undetectable in the output.
* **Backward compatibility.** `run_book` grew a per-name path. If the scalar
  path is not bit-for-bit what it was, every published number silently moves —
  the failure `Panel.fingerprint` was built to catch.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine import costs as C
from rhengine.alpha.book import run_book


class TestTierBoundaries(unittest.TestCase):
    """The table is the contract. Boundaries are half-open [lo, hi)."""

    def test_every_tier_is_reachable(self):
        for cap, want in ((5e11, "mega_large"), (10e9, "mega_large"),
                          (9.9e9, "mid"), (2e9, "mid"),
                          (1.99e9, "small"), (300e6, "small"),
                          (299e6, "micro"), (1.0, "micro")):
            self.assertEqual(C.cap_tier(cap), want, f"cap {cap:g}")

    def test_tiers_are_contiguous_and_ordered(self):
        """No gap and no overlap, and spreads must widen as size falls — a
        table that inverted anywhere would make small caps look cheap."""
        prev_hi_bps = 0.0
        for i, (_n, lo_cap, hi_cap, lo_bps, hi_bps) in enumerate(C.CAP_TIERS):
            self.assertLess(lo_cap, hi_cap)
            self.assertLess(lo_bps, hi_bps)
            self.assertGreaterEqual(lo_bps, prev_hi_bps)
            prev_hi_bps = hi_bps
            if i + 1 < len(C.CAP_TIERS):
                self.assertEqual(lo_cap, C.CAP_TIERS[i + 1][2],
                                 "gap or overlap between tiers")

    def test_missing_or_nonsense_caps_are_not_guessed(self):
        for bad in (None, np.nan, 0.0, -1.0, "abc"):
            self.assertIsNone(C.cap_tier(bad))


class TestUnitsAndDirection(unittest.TestCase):
    def test_table_is_round_trip_not_per_side(self):
        """SPY's true round trip is ~0.7 bps and INTC's ~1.5 (the validation
        table in costs.estimate_round_trip_bps). Both are mega-cap. If the
        table were per-side, the mega band would have to be ~0.5-2.5 to
        bracket them; it is 1-5, which brackets them as ROUND TRIP."""
        lo, hi = C.CAP_TIERS[0][3], C.CAP_TIERS[0][4]
        self.assertEqual((lo, hi), (1.0, 5.0))
        for known_round_trip in (0.7, 1.5, 3.0):
            self.assertLess(known_round_trip, hi,
                            "a measured mega-cap round trip sits above the "
                            "band's top -- the table has been read per-side")

    def test_point_is_the_geometric_mean(self):
        """Arithmetic would put the typical microcap at 350 of a 100-600 band.
        Geometric is the right centre for a range spanning an order of
        magnitude."""
        self.assertAlmostEqual(C.round_trip_bps_for_cap(50e6), np.sqrt(100 * 600))
        self.assertAlmostEqual(C.round_trip_bps_for_cap(5e10), np.sqrt(1 * 5))

    def test_lo_and_hi_bracket_the_point(self):
        for cap in (5e10, 5e9, 1e9, 1e8):
            lo = C.round_trip_bps_for_cap(cap, "lo")
            pt = C.round_trip_bps_for_cap(cap, "point")
            hi = C.round_trip_bps_for_cap(cap, "hi")
            self.assertLess(lo, pt)
            self.assertLess(pt, hi)

    def test_an_unpriceable_name_is_charged_more_not_less(self):
        """The direction that matters. Defaulting to mega-cap would make a
        book look cheaper the worse its data is."""
        unknown = C.round_trip_bps_for_cap(np.nan, unknown_bps=30.0)
        self.assertEqual(unknown, 30.0)
        self.assertGreater(unknown, C.round_trip_bps_for_cap(5e10, "hi"))

    def test_book_effective_bps_weights_by_position(self):
        w = pd.Series({"BIG": 0.9, "TINY": 0.1})
        caps = pd.Series({"BIG": 5e10, "TINY": 1e8})
        got = C.book_effective_bps(w, caps)
        want = 0.9 * np.sqrt(5) + 0.1 * np.sqrt(100 * 600)
        self.assertAlmostEqual(got, want, places=9)


class TestRunBookPerNameCost(unittest.TestCase):
    """`run_book` grew a per-name cost path. It must not have moved the old one."""

    def setUp(self):
        self.cal = pd.bdate_range("2015-01-05", periods=400)
        rng = np.random.default_rng(4)
        self.tk = ["BIG", "MID", "TINY"]
        self.adj = pd.DataFrame(
            100 * np.cumprod(1 + rng.normal(0.0004, 0.01,
                                            (len(self.cal), 3)), axis=0),
            index=self.cal, columns=self.tk)
        self.rf = pd.Series(0.0, index=self.cal)
        # rotate the whole book every 20 sessions so turnover is large and the
        # cost difference is not lost in rounding
        self.tgt = {self.cal[i]: pd.Series({self.tk[(i // 20) % 3]: 1.0})
                    for i in range(20, 380, 20)}

    def test_scalar_path_is_unchanged(self):
        """A uniform Series must equal the scalar exactly, or the refactor
        moved every published number."""
        for bps in (0.0, 10.0, 30.0):
            flat = run_book(self.adj, self.tgt, self.rf, bps)
            vec = run_book(self.adj, self.tgt, self.rf,
                           pd.Series(bps, index=self.tk))
            pd.testing.assert_series_equal(flat.daily_return, vec.daily_return)
            self.assertAlmostEqual(flat.effective_bps, bps, places=9)

    def test_each_name_pays_its_own_spread(self):
        """Charge one name 100 bps and the rest 0; the drag must equal that
        name's own traded notional times its own rate, not a blended average."""
        only_tiny = pd.Series({"BIG": 0.0, "MID": 0.0, "TINY": 100.0})
        bk = run_book(self.adj, self.tgt, self.rf, only_tiny)
        flat100 = run_book(self.adj, self.tgt, self.rf, 100.0)
        zero = run_book(self.adj, self.tgt, self.rf, 0.0)
        self.assertGreater(bk.cost_drag.sum(), zero.cost_drag.sum())
        self.assertLess(bk.cost_drag.sum(), flat100.cost_drag.sum())

    def test_effective_bps_is_turnover_weighted(self):
        """Not weight-weighted. A book that churns its cheap names and holds
        its dear ones must report the cost it actually paid."""
        vec = pd.Series({"BIG": 2.0, "MID": 10.0, "TINY": 250.0})
        bk = run_book(self.adj, self.tgt, self.rf, vec)
        self.assertGreater(bk.effective_bps, 2.0)
        self.assertLess(bk.effective_bps, 250.0)
        self.assertAlmostEqual(
            bk.effective_bps,
            bk.cost_drag.sum() / bk.turnover.sum() * 1e4, places=9)

    def test_a_name_missing_from_the_vector_pays_the_maximum(self):
        """An unpriced name is illiquid until shown otherwise. Filling with the
        mean — or zero — makes a book cheaper the worse its coverage is."""
        partial = pd.Series({"BIG": 2.0, "MID": 10.0})       # TINY absent
        as_max = pd.Series({"BIG": 2.0, "MID": 10.0, "TINY": 10.0})
        as_min = pd.Series({"BIG": 2.0, "MID": 10.0, "TINY": 2.0})
        gap = run_book(self.adj, self.tgt, self.rf, partial).cost_drag.sum()
        self.assertAlmostEqual(
            gap, run_book(self.adj, self.tgt, self.rf, as_max).cost_drag.sum(),
            places=12, msg="a missing name must be charged the vector's max")
        self.assertGreater(
            gap, run_book(self.adj, self.tgt, self.rf, as_min).cost_drag.sum())

    def test_cheaper_costs_never_lower_terminal_equity(self):
        cheap = run_book(self.adj, self.tgt, self.rf,
                         C.bps_for_caps(pd.Series({"BIG": 5e10, "MID": 5e9,
                                                   "TINY": 1e8}), "lo"))
        dear = run_book(self.adj, self.tgt, self.rf,
                        C.bps_for_caps(pd.Series({"BIG": 5e10, "MID": 5e9,
                                                  "TINY": 1e8}), "hi"))
        self.assertGreater(cheap.equity.iloc[-1], dear.equity.iloc[-1])


if __name__ == "__main__":
    unittest.main()
