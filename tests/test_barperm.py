"""Tests for rhengine/barperm.py — the bar-permutation null.

These are offline: they exercise the permutation itself, not the backtest that
consumes it, because the backtest needs network data. What has to hold for the
null to mean anything:

1. the permuted series has the SAME return distribution (a null that changes the
   distribution is testing two things at once);
2. OHLC stays valid, so ATR and range-based stops behave (H >= max(O,C),
   L <= min(O,C));
3. the calendar is untouched — only returns move;
4. **the permutation is synchronized across tickers**, which is the design
   decision the whole test rests on. If it were not, the market factor would be
   destroyed and a long-only book would beat the null trivially.
"""
from __future__ import annotations

import math
import random
import unittest

from rhengine import barperm as BP


def mkbars(seed: int, n: int = 60, start: float = 100.0, drift: float = 0.0):
    """A synthetic OHLC series on a shared integer calendar (times 0..n-1)."""
    rng = random.Random(seed)
    bars, c = [], start
    for t in range(n):
        o = c * math.exp(rng.gauss(drift, 0.01))
        c = o * math.exp(rng.gauss(drift, 0.01))
        h = max(o, c) * math.exp(abs(rng.gauss(0, 0.004)))
        l = min(o, c) * math.exp(-abs(rng.gauss(0, 0.004)))
        bars.append({"time": t, "open": o, "high": h, "low": l, "close": c,
                     "volume": 1000 + t})
    return bars


def logrets(bars):
    return [round(math.log(bars[i]["close"] / bars[i - 1]["close"]), 9)
            for i in range(1, len(bars))]


class TestDecomposeRebuild(unittest.TestCase):
    def test_roundtrip_is_the_identity(self):
        bars = mkbars(1)
        anchor, comps = BP.decompose(bars)
        back = BP.rebuild(anchor, comps, [k["time"] for k in comps])
        self.assertEqual(len(back), len(bars))
        for a, b in zip(bars, back):
            self.assertEqual(a["time"], b["time"])
            for k in BP.OHLC:
                self.assertAlmostEqual(a[k], b[k], places=8)

    def test_nonpositive_prices_are_skipped_not_nan(self):
        bars = mkbars(2, n=20)
        bars[7]["low"] = 0.0
        anchor, comps = BP.decompose(bars)
        self.assertEqual(len(comps), 18)          # 19 gaps minus the bad bar
        for k in comps:
            for f in ("gap", "h", "l", "c"):
                self.assertTrue(math.isfinite(k[f]))

    def test_volume_travels_with_its_bar(self):
        bars = mkbars(3, n=25)
        _, comps = BP.decompose(bars)
        self.assertEqual([k["volume"] for k in comps],
                         [b["volume"] for b in bars[1:]])


class TestPermutation(unittest.TestCase):
    def setUp(self):
        self.spy = mkbars(10, n=80, drift=0.0004)
        self.map = {"AAA": mkbars(11, n=80), "BBB": mkbars(12, n=80),
                    "CCC": mkbars(13, n=80)}

    def test_return_distribution_is_preserved_exactly(self):
        pm, ps = BP.permuted_map(self.map, self.spy, random.Random(7))
        for tk, bars in self.map.items():
            self.assertEqual(sorted(logrets(bars)), sorted(logrets(pm[tk])))
        self.assertEqual(sorted(logrets(self.spy)), sorted(logrets(ps)))

    def test_ordering_actually_changes(self):
        pm, _ = BP.permuted_map(self.map, self.spy, random.Random(7))
        self.assertNotEqual(logrets(self.map["AAA"]), logrets(pm["AAA"]))

    def test_ohlc_stays_valid(self):
        pm, ps = BP.permuted_map(self.map, self.spy, random.Random(3))
        for bars in list(pm.values()) + [ps]:
            for b in bars:
                self.assertGreaterEqual(b["high"], max(b["open"], b["close"]) - 1e-9)
                self.assertLessEqual(b["low"], min(b["open"], b["close"]) + 1e-9)
                self.assertGreater(b["low"], 0.0)

    def test_calendar_is_untouched(self):
        pm, ps = BP.permuted_map(self.map, self.spy, random.Random(5))
        for tk, bars in self.map.items():
            self.assertEqual([b["time"] for b in bars],
                             [b["time"] for b in pm[tk]])
        self.assertEqual([b["time"] for b in self.spy], [b["time"] for b in ps])

    def test_permutation_is_synchronized_across_tickers(self):
        """The design decision the whole module rests on.

        Every series must receive the SAME reordering of calendar slots, so the
        cross-section moves together and the market factor survives. Verified by
        checking that the permuted position of each source bar is identical for
        every ticker.
        """
        rng = random.Random(99)
        pm, ps = BP.permuted_map(self.map, self.spy, rng)

        def order(orig, perm):
            """Where each original log-return ended up after permuting."""
            src = logrets(orig)
            got = logrets(perm)
            pos = {}
            for i, v in enumerate(got):
                pos.setdefault(round(v, 9), []).append(i)
            return [pos[round(v, 9)].pop(0) for v in src]

        ref = order(self.spy, ps)
        for tk in self.map:
            self.assertEqual(order(self.map[tk], pm[tk]), ref,
                             f"{tk} was not permuted in lockstep with SPY")

    def test_independent_shuffling_would_fail_that_test(self):
        """Guard the guard: the synchronization test must be able to fail."""
        rng = random.Random(1)
        a = BP.permuted_map({"AAA": self.map["AAA"]}, self.spy, rng)[0]["AAA"]
        b = BP.permuted_map({"AAA": self.map["AAA"]}, self.spy, rng)[0]["AAA"]
        self.assertNotEqual(logrets(a), logrets(b))

    def test_ticker_with_missing_dates_still_synchronizes(self):
        """A short/gappy ticker takes its dates in the master's permuted order."""
        short = [b for b in mkbars(21, n=80) if b["time"] % 3 != 0]
        pm, _ = BP.permuted_map({"AAA": self.map["AAA"], "SHORT": short},
                                self.spy, random.Random(4))
        self.assertEqual([b["time"] for b in short],
                         [b["time"] for b in pm["SHORT"]])
        self.assertEqual(sorted(logrets(short)), sorted(logrets(pm["SHORT"])))

    def test_total_drift_is_PRESERVED_not_destroyed(self):
        """The easy-to-get-wrong property, pinned so the docs cannot drift back.

        A sum is invariant to ordering, so permuting log-returns leaves each
        ticker's total window return exactly unchanged. This null removes the
        SEQUENCING of the drift, never the drift. It is why a positive null mean
        is expected rather than a bug.
        """
        pm, ps = BP.permuted_map(self.map, self.spy, random.Random(31))
        for tk, bars in self.map.items():
            self.assertAlmostEqual(sum(logrets(bars)), sum(logrets(pm[tk])),
                                   places=8)
        self.assertAlmostEqual(sum(logrets(self.spy)), sum(logrets(ps)), places=8)
        # ... and therefore the last close is (near) identical too.
        self.assertAlmostEqual(self.spy[-1]["close"], ps[-1]["close"], places=6)

    def test_seeded_runs_reproduce(self):
        p1, _ = BP.permuted_map(self.map, self.spy, random.Random(42))
        p2, _ = BP.permuted_map(self.map, self.spy, random.Random(42))
        self.assertEqual(logrets(p1["AAA"]), logrets(p2["AAA"]))

    def test_serial_structure_is_destroyed(self):
        """A trending series should lose its autocorrelation of |returns|.

        Volatility clustering is the property an ATR stop feeds on, so this is
        the substantive check that the null is doing its job.
        """
        # Build a series with strong vol clustering: alternating calm/wild runs.
        rng = random.Random(77)
        bars, c = [], 100.0
        for t in range(400):
            sd = 0.002 if (t // 40) % 2 == 0 else 0.03
            o = c * math.exp(rng.gauss(0, sd))
            c = o * math.exp(rng.gauss(0, sd))
            bars.append({"time": t, "open": o, "high": max(o, c),
                         "low": min(o, c), "close": c, "volume": 1})

        def ac1_abs(bs):
            r = [abs(x) for x in logrets(bs)]
            m = sum(r) / len(r)
            num = sum((r[i] - m) * (r[i - 1] - m) for i in range(1, len(r)))
            den = sum((x - m) ** 2 for x in r)
            return num / den

        pm, _ = BP.permuted_map({"X": bars}, bars, random.Random(8))
        self.assertGreater(ac1_abs(bars), 0.3)          # real: clustered
        self.assertLess(abs(ac1_abs(pm["X"])), 0.15)    # permuted: memoryless


class TestStatistics(unittest.TestCase):
    def test_p_value_has_the_plus_one_correction(self):
        # No null reaches the real stat -> p must be 1/(n+1), never 0.
        self.assertAlmostEqual(BP.p_value(10.0, [0.0] * 99), 1.0 / 100.0)
        # Every null reaches it -> p = 1.
        self.assertAlmostEqual(BP.p_value(0.0, [1.0] * 99), 1.0)
        self.assertGreater(BP.p_value(0.5, [0.0] * 19 + [1.0]), 0.0)

    def test_summarize_reports_percentiles_and_p(self):
        real = {"avg_r": 0.40, "n": 120, "profit_factor": 1.5,
                "win_rate": 0.5, "t_stat": 2.0}
        nulls = [{"avg_r": i / 100.0, "n": 100, "profit_factor": 1.0,
                  "win_rate": 0.4, "t_stat": 0.1} for i in range(100)]
        rep = BP.summarize(real, nulls, "avg_r")
        self.assertEqual(rep["real"], 0.40)
        self.assertEqual(rep["paths"], 100)
        self.assertAlmostEqual(rep["null_p50"], 0.50, places=2)
        # 59 of 100 nulls are >= 0.40 -> p is large; the edge is not real.
        self.assertGreater(rep["p_value"], 0.5)

    def test_summarize_handles_a_real_edge(self):
        real = {"avg_r": 0.99, "n": 120, "profit_factor": 2.0,
                "win_rate": 0.6, "t_stat": 3.0}
        nulls = [{"avg_r": i / 1000.0, "n": 100, "profit_factor": 1.0,
                  "win_rate": 0.4, "t_stat": 0.1} for i in range(200)]
        rep = BP.summarize(real, nulls, "avg_r")
        self.assertLessEqual(rep["p_value"], 0.05)


if __name__ == "__main__":
    unittest.main()
