"""
test_pit_universe.py — the universe must be point-in-time.

Guards docs/AUDIT-2026-07-24.md FC-4: `massive_universe()` historically drew its
ticker set from the most recent cached sessions and applied it to the entire
backtest window. That silently deleted every name which delisted before today
(measured: 1,859 of 10,095 common stocks alive on 2024-07-18 were gone by
2026-07-20; 575 of them passed the engine's own tradeability floors on day 1)
and screened historical bars on today's price and liquidity.

The invariant these tests pin:

    The universe as of date D must not change when data after D arrives.

These run against a synthetic cache written to a temp dir, so they need no
network and no real massive_cache. The `TICK_DIES` fixture ticker is liquid
early and absent later — exactly the shape survivorship bias erases.
"""

import gzip
import json
import os
import shutil
import tempfile
import unittest

from rhengine import data_local


# bar tuple layout in the massive cache: [open, high, low, close, volume, time_ms]
def _bar(close, volume):
    return [close, close * 1.01, close * 0.99, close, volume, 0]


def _write_cache(root, days, universe_by_day):
    os.makedirs(root, exist_ok=True)
    for d in days:
        path = os.path.join(root, d + ".json.gz")
        with gzip.open(path, "wt") as f:
            json.dump(universe_by_day[d], f)


class PitUniverseTest(unittest.TestCase):
    """A liquid name that later delists must remain in the early universe."""

    EARLY = ["2026-01-%02d" % i for i in range(1, 26)]   # 25 sessions
    LATE = ["2026-02-%02d" % i for i in range(1, 26)]    # 25 more

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pitcache_")
        by_day = {}
        for d in self.EARLY:
            by_day[d] = {
                "ALIVE": _bar(50.0, 100_000),   # liquid throughout
                "DIES": _bar(40.0, 100_000),    # liquid, then delists
            }
        for d in self.LATE:
            by_day[d] = {
                "ALIVE": _bar(55.0, 100_000),
                # DIES is gone — delisted/acquired
                "NEWCO": _bar(30.0, 100_000),   # IPO'd after the early window
            }
        _write_cache(self.tmp, self.EARLY + self.LATE, by_day)
        self._orig = data_local.massive_universe.__globals__.get("_MASSIVE_CACHE")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _universe(self, as_of=None):
        """Call massive_universe against the temp cache."""
        import rhengine.market_data as md
        orig = md._MASSIVE_CACHE
        md._MASSIVE_CACHE = self.tmp
        try:
            return set(data_local.massive_universe(
                lookback_sessions=20, min_price=1.0, min_dollar_vol=1000.0,
                plain_symbols_only=True, as_of=as_of, verbose=False))
        finally:
            md._MASSIVE_CACHE = orig

    def test_as_of_early_includes_the_name_that_later_dies(self):
        """THE core survivorship invariant."""
        early = self._universe(as_of=self.EARLY[-1])
        self.assertIn("DIES", early,
                      "a name liquid during the early window must be in the "
                      "as-of universe for that window")
        self.assertIn("ALIVE", early)
        self.assertNotIn("NEWCO", early,
                         "a name that had not started trading yet must not be "
                         "in a historical universe (look-ahead)")

    def test_as_of_late_drops_the_dead_name(self):
        late = self._universe(as_of=self.LATE[-1])
        self.assertNotIn("DIES", late)
        self.assertIn("ALIVE", late)
        self.assertIn("NEWCO", late)

    def test_universe_is_invariant_to_future_data(self):
        """The universe as of D must not change when data after D arrives.

        This is the test that FAILS against the legacy no-as_of behavior: the
        legacy call always reads the newest sessions, so appending February
        changes what January's universe looks like.
        """
        as_of = self.EARLY[-1]
        with_future = self._universe(as_of=as_of)

        # Rebuild a cache truncated at as_of — i.e. what was knowable then.
        truncated = tempfile.mkdtemp(prefix="pittrunc_")
        try:
            for f in sorted(os.listdir(self.tmp)):
                if f[:-8] <= as_of:
                    shutil.copy(os.path.join(self.tmp, f),
                                os.path.join(truncated, f))
            import rhengine.market_data as md
            orig = md._MASSIVE_CACHE
            md._MASSIVE_CACHE = truncated
            try:
                without_future = set(data_local.massive_universe(
                    lookback_sessions=20, min_price=1.0, min_dollar_vol=1000.0,
                    plain_symbols_only=True, as_of=as_of, verbose=False))
            finally:
                md._MASSIVE_CACHE = orig
        finally:
            shutil.rmtree(truncated, ignore_errors=True)

        self.assertEqual(with_future, without_future,
                         "universe as of {} changed when later data was added "
                         "— that is look-ahead".format(as_of))

    def test_legacy_call_without_as_of_is_survivorship_biased(self):
        """Documents the old behavior so nobody 'fixes' the guard away.

        Without as_of the universe is drawn from the newest sessions, so the
        delisted name is absent — which is precisely the bug.
        """
        legacy = self._universe(as_of=None)
        self.assertNotIn("DIES", legacy)
        self.assertIn("NEWCO", legacy)


class PitMembershipTest(unittest.TestCase):
    """The rebalancing membership schedule used by the backtest loop."""

    DAYS = ["2026-01-%02d" % i for i in range(1, 29)] + \
           ["2026-02-%02d" % i for i in range(1, 29)]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pitmem_")
        by_day = {}
        for d in self.DAYS:
            row = {"ALIVE": _bar(50.0, 100_000)}
            if d < "2026-02-01":
                row["DIES"] = _bar(40.0, 100_000)
            else:
                row["NEWCO"] = _bar(30.0, 100_000)
            by_day[d] = row
        _write_cache(self.tmp, self.DAYS, by_day)
        self.cfg = {"universe_filters": {
            "lookback_sessions": 20, "min_price": 1.0,
            "min_dollar_volume": 1000.0, "plain_symbols_only": True}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(self, rebalance=10):
        import rhengine.market_data as md
        orig = md._MASSIVE_CACHE
        md._MASSIVE_CACHE = self.tmp
        try:
            return data_local.pit_membership(
                self.cfg, rebalance_sessions=rebalance, verbose=False)
        finally:
            md._MASSIVE_CACHE = orig

    def test_membership_tracks_delisting(self):
        m = self._build()
        self.assertGreater(len(m), 1, "expected multiple rebalance snapshots")
        self.assertIn("DIES", m.union(),
                      "a delisted name must survive in the union so its bars "
                      "are fetched and its history is tradeable")
        # early snapshot has DIES, the last one does not
        self.assertTrue(m.contains("DIES", "2026-01-20"))
        self.assertFalse(m.contains("DIES", "2026-02-28"))

    def test_membership_before_first_snapshot_is_empty(self):
        m = self._build()
        self.assertFalse(m.contains("ALIVE", "2025-01-01"),
                         "no membership before the first screened date")

    def test_snapshots_are_monotonic_in_date(self):
        m = self._build()
        dates = [d for d, _ in m.sizes()]
        self.assertEqual(dates, sorted(dates))


class BacktestUniverseFilterTest(unittest.TestCase):
    """run_backtest_on must honor a membership object."""

    def test_universe_arg_filters_trades(self):
        from rhengine.backtest import run_backtest_on
        from rhengine.data_local import synthetic_universe

        bars_map, spy_bars = synthetic_universe(n_tickers=4, n_bars=200, seed=11)
        cfg = {"strategy": {"mode": "auto", "thresholds": {}},
               "exits": {"max_hold_sessions": 5},
               "universe_filters": {"min_price": 0.0}}

        base = run_backtest_on(cfg, bars_map, spy_bars, min_score=3)
        self.assertTrue(base, "fixture produced no trades to filter")

        class DenyAll:
            def contains(self, ticker, iso_date):
                return False

        filtered = run_backtest_on(cfg, bars_map, spy_bars, min_score=3,
                                   universe=DenyAll())
        self.assertEqual(filtered, [],
                         "a membership that admits nothing must yield no trades")

        kept = sorted({t["ticker"] for t in base})[:1]

        class OnlyFirst:
            def contains(self, ticker, iso_date):
                return ticker in kept

        one = run_backtest_on(cfg, bars_map, spy_bars, min_score=3,
                              universe=OnlyFirst())
        self.assertTrue(one)
        self.assertEqual({t["ticker"] for t in one}, set(kept))


if __name__ == "__main__":
    unittest.main()
