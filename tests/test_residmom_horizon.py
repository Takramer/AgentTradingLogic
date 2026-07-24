"""
test_residmom_horizon.py — residual momentum: date alignment + horizon params.

Guards two audit findings (docs/AUDIT-2026-07-24.md):

FC-9  score_residual_momentum sliced bars[-N:] and spy_bars[-N:] and paired them
      BY POSITION. When the two series do not cover identical sessions, each
      stock return gets paired with a LATER spy return — a forward-looking
      pairing in the beta estimate. 18.3% of cached tickers have at least one
      missing session. Caught by fixtures/bars.json itself, where
      squeeze_breakout (79 bars) ends one session before spy_uptrend (80):
      beta flipped +0.09 -> -0.13 when the pairing was corrected.

FC-8  the horizon was hardcoded to 36/12/1 TRADING DAYS while the docstring
      cited Blitz/Huij/Martens (2011), which specifies 36/12/1 MONTHS. The
      horizon is now config-driven; these tests pin the defaults so the legacy
      behavior cannot drift, and prove the long horizon is reachable.
"""

import math
import unittest

from rhengine.indicators import score_residual_momentum


def _series(n, drift, start=100.0, t0=1_600_000_000_000, step=86_400_000):
    """Deterministic bars on a regular daily grid."""
    bars, px = [], start
    for i in range(n):
        px *= math.exp(drift + 0.004 * math.sin(i * 0.7))
        bars.append({"time": t0 + i * step, "open": px, "high": px * 1.01,
                     "low": px * 0.99, "close": px, "volume": 1_000_000})
    return bars


def _ra(result):
    """Risk-adjusted residual (leg 0 'val' string) or None."""
    if not result:
        return None
    return result[0]["legs"][0]["val"]


class DateAlignmentTest(unittest.TestCase):

    def test_noop_when_series_are_aligned(self):
        """The fix must not move results for clean, fully-aligned data.

        Verified against the real fixtures too: uptrend / downtrend /
        choppy_reversal (all 80 bars, perfectly aligned) are byte-identical
        before and after; only the two short series changed.
        """
        stock = _series(80, 0.002)
        spy = _series(80, 0.001, start=400.0)
        legacy = score_residual_momentum("T", stock, {"alignByDate": 0}, spy)
        fixed = score_residual_momentum("T", stock, {"alignByDate": 1}, spy)
        self.assertEqual(_ra(legacy), _ra(fixed))

    def test_misaligned_series_change_and_legacy_is_reproducible(self):
        """Dropping interior sessions from the stock must change the result.

        Under positional pairing the stock's return on date d is matched to
        spy's return on a LATER date. Correcting that must move beta; the
        legacy value must still be reachable via alignByDate=0 so the two can
        be A/B compared.
        """
        spy = _series(80, 0.001, start=400.0)
        stock_full = _series(80, 0.002)
        # remove three interior sessions — a halted / thinly traded name
        gapped = [b for i, b in enumerate(stock_full) if i not in (20, 40, 60)]

        legacy = _ra(score_residual_momentum("T", gapped, {"alignByDate": 0}, spy))
        fixed = _ra(score_residual_momentum("T", gapped, {"alignByDate": 1}, spy))
        self.assertIsNotNone(legacy)
        self.assertIsNotNone(fixed)
        self.assertNotEqual(legacy, fixed,
                            "positional pairing on a gapped series must differ "
                            "from date-aligned pairing")

    def test_aligned_result_ignores_spy_bars_the_stock_does_not_share(self):
        """THE no-look-ahead invariant.

        Appending future spy sessions the stock does not have must not change
        a date-aligned score. Under the legacy positional slice it does — that
        is the forward-looking pairing FC-9 describes.
        """
        stock = _series(80, 0.002)
        spy = _series(80, 0.001, start=400.0)
        spy_extra = spy + _series(3, 0.001, start=spy[-1]["close"],
                                  t0=spy[-1]["time"] + 86_400_000)

        aligned_a = _ra(score_residual_momentum("T", stock, {"alignByDate": 1}, spy))
        aligned_b = _ra(score_residual_momentum("T", stock, {"alignByDate": 1}, spy_extra))
        self.assertEqual(aligned_a, aligned_b,
                         "date-aligned score changed when spy gained sessions "
                         "the stock never had — that is look-ahead")

        legacy_a = _ra(score_residual_momentum("T", stock, {"alignByDate": 0}, spy))
        legacy_b = _ra(score_residual_momentum("T", stock, {"alignByDate": 0}, spy_extra))
        self.assertNotEqual(legacy_a, legacy_b,
                            "legacy positional pairing is expected to leak here; "
                            "if this stops being true the guard is redundant")


class HorizonParamTest(unittest.TestCase):

    def test_defaults_are_the_legacy_36_12_1(self):
        stock = _series(80, 0.002)
        spy = _series(80, 0.001, start=400.0)
        implicit = _ra(score_residual_momentum("T", stock, {}, spy))
        explicit = _ra(score_residual_momentum(
            "T", stock, {"regBars": 36, "formBars": 12, "skipBars": 1}, spy))
        self.assertEqual(implicit, explicit)

    def test_window_is_reg_plus_form_plus_skip_plus_one(self):
        """Derived window must reproduce the original hardcoded 50 bars.

        With defaults that is 36 + 12 + 1 + 1 = 50, so estimation and formation
        windows stay adjacent and non-overlapping at every horizon.
        """
        spy = _series(200, 0.001, start=400.0)
        stock = _series(200, 0.002)
        # only the last 50 bars can matter at default settings
        trimmed = _ra(score_residual_momentum("T", stock[-50:], {}, spy[-50:]))
        full = _ra(score_residual_momentum("T", stock, {}, spy))
        self.assertEqual(trimmed, full)

    def test_long_horizon_is_reachable_and_differs(self):
        """The literature horizon must produce a different, valid signal."""
        n = 1100
        stock = _series(n, 0.0008)
        spy = _series(n, 0.0004, start=400.0)
        lit = {"regBars": 756, "formBars": 252, "skipBars": 21}
        short = _ra(score_residual_momentum("T", stock, {}, spy))
        long_ = _ra(score_residual_momentum("T", stock, lit, spy))
        self.assertIsNotNone(long_, "literature horizon returned no score")
        self.assertNotEqual(short, long_)

    def test_long_horizon_refuses_insufficient_history(self):
        """A 1030-bar window must not silently degrade to a short-horizon signal."""
        stock = _series(300, 0.002)
        spy = _series(300, 0.001, start=400.0)
        lit = {"regBars": 756, "formBars": 252, "skipBars": 21}
        self.assertIsNone(score_residual_momentum("T", stock, lit, spy),
                          "must return None rather than score a truncated window")


if __name__ == "__main__":
    unittest.main()
