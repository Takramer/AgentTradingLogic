"""Point-in-time and construction tests for the alpha stack.

`test_universe_is_point_in_time` is the audit's headline proof test
(docs/AUDIT-2026-07-24.md FC-4): "assert the universe for date D is identical
whether the cache ends at D or at D+250 sessions. This test **fails today**."
It fails for `data_local.massive_universe()`, which builds its ticker list from
the most recent 20 cached sessions and then applies that list to all of history.
It passes for `rhengine.alpha.panel.Panel.eligible`, which is the point.

These run on synthetic panels, so they need neither the research dataset nor a
network.
"""
import unittest

import numpy as np
import pandas as pd

from rhengine.alpha.book import run_book
from rhengine.alpha.fundamentals import Fundamentals
from rhengine.alpha.panel import MIN_DOLLAR_VOL, MIN_HISTORY_DAYS, MIN_PRICE, Panel
from rhengine.alpha.rmxq import RMXQConfig, _weights
from rhengine.alpha.timing import TimingConfig, build_schedule, month_end_index


def _panel(n_days=1400, tickers=("A", "B", "C", "D"), seed=0) -> Panel:
    cal = pd.bdate_range("2015-01-05", periods=n_days)
    rng = np.random.default_rng(seed)
    px = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0.0004, 0.012, (n_days, len(tickers))), axis=0),
        index=cal, columns=list(tickers))
    dv = pd.DataFrame(MIN_DOLLAR_VOL * 10, index=cal, columns=list(tickers), dtype=float)
    member = pd.DataFrame(True, index=cal, columns=list(tickers))
    rf = pd.Series(0.0, index=cal)
    return Panel(adj=px, close=px.copy(), dollar_vol=dv, member=member,
                 spy=px["A"], rf=rf, mkt=rf.copy(),
                 coverage=pd.Series(1.0, index=cal),
                 n_members=pd.Series(len(tickers), index=cal))


class TestPointInTime(unittest.TestCase):
    def test_universe_is_point_in_time(self):
        """The audit's FC-4 proof test. Truncating the future must change nothing."""
        full = _panel(1400)
        d = full.adj.index[1000]
        cut = full.adj.index[1010]

        truncated = Panel(
            adj=full.adj.loc[:cut], close=full.close.loc[:cut],
            dollar_vol=full.dollar_vol.loc[:cut], member=full.member.loc[:cut],
            spy=full.spy.loc[:cut], rf=full.rf.loc[:cut], mkt=full.mkt.loc[:cut],
            coverage=full.coverage.loc[:cut], n_members=full.n_members.loc[:cut])

        self.assertEqual(list(full.eligible(d)), list(truncated.eligible(d)))

    def test_future_delisting_does_not_remove_a_past_member(self):
        """A name that dies later was still tradeable before it died."""
        p = _panel(1400)
        d = p.adj.index[1000]
        before = set(p.eligible(d))
        p.adj.loc[p.adj.index[1200]:, "B"] = np.nan
        p.close.loc[p.close.index[1200]:, "B"] = np.nan
        p._liq_cache.clear()
        self.assertEqual(before, set(p.eligible(d)))

    def test_liquidity_and_price_floors_bind(self):
        p = _panel(1200)
        d = p.adj.index[1000]
        self.assertIn("C", p.eligible(d))
        p.dollar_vol.loc[:, "C"] = MIN_DOLLAR_VOL / 100.0
        p.close.loc[:, "D"] = MIN_PRICE / 2.0
        p._liq_cache.clear()
        elig = set(p.eligible(d))
        self.assertNotIn("C", elig)
        self.assertNotIn("D", elig)

    def test_short_history_is_excluded(self):
        p = _panel(1200)
        early = p.adj.index[MIN_HISTORY_DAYS // 2]
        self.assertEqual(len(p.eligible(early)), 0)


class TestTiming(unittest.TestCase):
    def test_schedule_never_allocates_more_than_capital(self):
        cal = pd.bdate_range("2000-01-03", periods=3000)
        px = pd.Series(np.linspace(100, 300, 3000), index=cal)
        safe = pd.Series(np.linspace(100, 150, 3000), index=cal)
        s = build_schedule(px, safe, TimingConfig())
        total = s["equity"] + s["bond"] + s["cash"]
        self.assertTrue(((total <= 1.0 + 1e-9) | (s["equity"] > 1.0)).all())
        self.assertTrue((s["bond"] >= 0).all())

    def test_downtrend_moves_out_of_equity(self):
        cal = pd.bdate_range("2000-01-03", periods=1500)
        px = pd.Series(np.linspace(300, 100, 1500), index=cal)
        s = build_schedule(px, None, TimingConfig(vol_target=None))
        self.assertLess(s["equity"].iloc[-12:].mean(), 0.2)

    def test_bond_leg_requires_the_bond_to_be_trending(self):
        cal = pd.bdate_range("2000-01-03", periods=1500)
        eq = pd.Series(np.linspace(300, 100, 1500), index=cal)     # equity falling
        bad_bond = pd.Series(np.linspace(200, 100, 1500), index=cal)  # bond falling too
        s = build_schedule(eq, bad_bond, TimingConfig(vol_target=None))
        self.assertAlmostEqual(float(s["bond"].iloc[-12:].sum()), 0.0, places=6)
        good_bond = pd.Series(np.linspace(100, 200, 1500), index=cal)
        s2 = build_schedule(eq, good_bond, TimingConfig(vol_target=None))
        self.assertGreater(float(s2["bond"].iloc[-12:].mean()), 0.9)

    def test_month_end_index_is_last_session_of_each_month(self):
        cal = pd.bdate_range("2020-01-01", "2020-06-30")
        me = month_end_index(cal)
        self.assertEqual(len(me), 6)
        self.assertEqual(str(me[0].date()), "2020-01-31")


class TestWeighting(unittest.TestCase):
    def test_value_weights_sum_to_one_and_respect_the_cap(self):
        cap = pd.Series({f"T{i}": 10.0 for i in range(30)})
        cap["T0"] = 100000.0
        w = _weights(cap, RMXQConfig(weighting="value", max_weight=0.10))
        self.assertAlmostEqual(float(w.sum()), 1.0, places=9)
        self.assertLessEqual(float(w.max()), 0.10 + 1e-9)

    def test_cap_below_one_over_n_degrades_to_equal_weight(self):
        """A 10% cap on 4 names is arithmetically impossible, not an error."""
        cap = pd.Series({"A": 1000.0, "B": 10.0, "C": 5.0, "D": 5.0})
        w = _weights(cap, RMXQConfig(weighting="value", max_weight=0.10))
        self.assertAlmostEqual(float(w.sum()), 1.0, places=9)
        self.assertLessEqual(float(w.max()), 0.25 + 1e-9)

    def test_value_weighting_still_orders_by_size(self):
        cap = pd.Series({"A": 100.0, "B": 50.0, "C": 25.0})
        w = _weights(cap, RMXQConfig(weighting="value", max_weight=1.0))
        self.assertGreater(w["A"], w["B"])
        self.assertGreater(w["B"], w["C"])
        self.assertAlmostEqual(float(w.sum()), 1.0, places=9)

    def test_equal_weighting_ignores_size(self):
        cap = pd.Series({"A": 100.0, "B": 1.0})
        w = _weights(cap, RMXQConfig(weighting="equal"))
        self.assertAlmostEqual(float(w["A"]), float(w["B"]))


class TestFundamentalsLag(unittest.TestCase):
    """No accounting number may be used before it was public (Fama-French lag)."""

    def _fake(self) -> Fundamentals:
        idx = pd.MultiIndex.from_product([["A", "B"], [2018, 2019, 2020]],
                                         names=["ticker", "year"])
        table = pd.DataFrame({"op_be": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]}, index=idx)
        vf = pd.Series({y: pd.Timestamp(year=y + 1, month=7, day=1)
                        for y in (2018, 2019, 2020)})
        return Fundamentals(table=table, valid_from=vf)

    def test_fiscal_year_is_unusable_before_july_of_the_next_year(self):
        f = self._fake()
        self.assertEqual(f.latest_year_for(pd.Timestamp("2020-06-30")), 2018)
        self.assertEqual(f.latest_year_for(pd.Timestamp("2020-07-01")), 2019)
        self.assertIsNone(f.latest_year_for(pd.Timestamp("2019-01-01")))

    def test_as_of_never_returns_a_future_fiscal_year(self):
        f = self._fake()
        s = f.as_of(pd.Timestamp("2020-06-30"), "op_be")
        self.assertAlmostEqual(float(s["A"]), 0.1)   # FY2018, not FY2019/2020


class TestBookLeverage(unittest.TestCase):
    def test_borrowing_is_charged_above_full_investment(self):
        cal = pd.bdate_range("2010-01-04", periods=500)
        adj = pd.DataFrame({"X": np.full(500, 100.0)}, index=cal)   # flat price
        rf = pd.Series(0.0, index=cal)
        tgt = {cal[10]: pd.Series({"X": 1.5})}
        lev = run_book(adj, tgt, rf, 0.0, max_gross=1.5, borrow_spread_bps=200.0)
        flat = run_book(adj, {cal[10]: pd.Series({"X": 1.0})}, rf, 0.0)
        # a flat asset held on margin must LOSE the borrow cost
        self.assertLess(lev.equity.iloc[-1], flat.equity.iloc[-1])
        self.assertLess(lev.daily_return.iloc[-1], 0.0)

    def test_max_gross_is_enforced_at_rebalance(self):
        """The cap binds when the book is SET.

        Between rebalances the ratio drifts - a static levered position gets
        more levered as its equity falls, which is real and is left visible
        rather than smoothed away by a fictional daily re-margining.
        """
        cal = pd.bdate_range("2010-01-04", periods=300)
        adj = pd.DataFrame({"X": np.full(300, 100.0), "Y": np.full(300, 50.0)},
                           index=cal)
        rf = pd.Series(0.0, index=cal)
        tgt = {cal[10]: pd.Series({"X": 3.0, "Y": 3.0})}
        bk = run_book(adj, tgt, rf, 0.0, max_gross=1.25)
        self.assertLessEqual(float(bk.exposure.loc[cal[11]]), 1.25 + 1e-9)
        self.assertGreater(float(bk.exposure.loc[cal[11]]), 1.2)


if __name__ == "__main__":
    unittest.main()
