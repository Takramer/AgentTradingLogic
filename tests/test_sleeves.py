"""test_sleeves.py — the traps in the new sleeve/blend/external code.

Each test here corresponds to a specific way this code could be silently wrong:

* `test_issuance_removes_splits` — a 4:1 split quadruples the reported share
  count without issuing a share. Not adjusting would rank every splitting
  company as a huge issuer. Same class of bug as the $1.5-trillion GE in
  Amendment H of the pre-registration.
* `test_realised_vol_is_knowable` — the vol multiplier stamped on session D must
  use returns strictly before D, or the Barroso scaling is look-ahead.
* `test_reversal_sign` / `test_investment_sign` — every signal in the registry
  must be "higher is better", because the sleeve builder always takes nlargest.
  A sign flip would build the exact inverse of the intended portfolio and it
  would not raise.
* `test_no_two_sleeves_share_a_theme` — the blend's whole value is low
  correlation. Two sleeves from the same JKP theme break that silently.
* `test_reversal_residualises_market` — an unresidualised reversal sleeve is a
  leveraged market bet after a down month, not a mean-reversion sleeve.
* `test_ebm_rejects_double_negative` — when a firm's net debt exceeds both its
  book equity and its market cap, EBM's numerator and denominator are both
  negative and the ratio comes out large and POSITIVE. Unguarded, the value
  sleeve would fill up with the most distressed names in the index and the sort
  would silently invert. This is the single most dangerous line in `value_ebm`.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha import sleeves as S


class _FakePanel:
    """Minimal Panel stand-in: two names, a deterministic price path."""

    def __init__(self, n: int = 60, splits: dict | None = None):
        idx = pd.bdate_range("2020-01-01", periods=n)
        # AAA rises steadily; BBB fell hard over the last 5 sessions
        aaa = pd.Series(np.linspace(100.0, 160.0, n), index=idx)
        bbb = pd.Series(np.linspace(100.0, 140.0, n), index=idx)
        bbb.iloc[-5:] = np.linspace(140.0, 110.0, 5)
        self.adj = pd.DataFrame({"AAA": aaa, "BBB": bbb})
        self.close = self.adj.copy()      # no dividends in the synthetic path
        self.spy = pd.Series(np.linspace(400.0, 440.0, n), index=idx)
        self._splits = splits or {}

    def split_factor_after(self, ticker, cutoff):
        f = 1.0
        for d, ratio in self._splits.get(ticker, []):
            if d > cutoff:
                f *= ratio
        return f

    def eligible(self, date):
        """Every priced name. The universe floors are Panel's job, not a stub's."""
        return self.adj.columns

    def month_ends(self, lo=None, hi=None):
        idx = self.adj.index
        if hi is not None:
            idx = idx[idx <= pd.Timestamp(hi)]
        s = pd.Series(idx, index=idx)
        return pd.DatetimeIndex(
            s.groupby([idx.year, idx.month]).last().values)


class _FakeFund:
    """Minimal Fundamentals stand-in over a (ticker, year) MultiIndex."""

    def __init__(self, table: pd.DataFrame, year: int = 2024):
        self.table = table
        self._year = year

    def latest_year_for(self, date):
        return self._year

    def as_of(self, date, field, tickers=None):
        s = self.table[field].xs(self._year, level="year")
        s = s[np.isfinite(s)]
        return s.reindex(tickers).dropna() if tickers is not None else s


def _shares_table(rows: dict, years: tuple[int, ...]) -> pd.DataFrame:
    idx = pd.MultiIndex.from_product([sorted(rows), years],
                                     names=["ticker", "year"])
    s = pd.Series(np.nan, index=idx, dtype=float)
    for tk, per_year in rows.items():
        for y, v in per_year.items():
            s.loc[(tk, y)] = float(v)
    return pd.DataFrame({"shares": s})


class TestIssuance(unittest.TestCase):
    YEARS = (2019, 2024)

    def test_issuance_removes_splits(self):
        """A pure 4:1 split must score as ZERO issuance, not +300%."""
        # SPLT reports 100m shares in FY2019 and 400m in FY2024, but the entire
        # change is a 4:1 split that happened in 2021. Real issuance is zero.
        table = _shares_table({"SPLT": {2019: 100e6, 2024: 400e6}}, self.YEARS)
        panel = _FakePanel(splits={"SPLT": [(pd.Timestamp("2021-06-01"), 4.0)]})
        fund = _FakeFund(table, year=2024)
        got = S.issuance_5y(panel, fund, pd.Timestamp("2024-12-31"),
                            pd.Index(["SPLT"]))
        # FY2019's count is pre-split, so it is multiplied up by 4; FY2024's is
        # already post-split. 400/(100*4) - 1 = 0, and the signal is negated.
        self.assertAlmostEqual(got["SPLT"], 0.0, places=9)

    def test_issuance_detects_real_dilution(self):
        """Genuine issuance with no split must score negative (bad)."""
        table = _shares_table({"DILU": {2019: 100e6, 2024: 150e6}}, self.YEARS)
        fund = _FakeFund(table, year=2024)
        got = S.issuance_5y(_FakePanel(), fund, pd.Timestamp("2024-12-31"),
                            pd.Index(["DILU"]))
        self.assertAlmostEqual(got["DILU"], -0.5, places=9)

    def test_issuance_rewards_buybacks(self):
        """A net repurchaser must score positive (good)."""
        table = _shares_table({"BUYB": {2019: 100e6, 2024: 80e6}}, self.YEARS)
        fund = _FakeFund(table, year=2024)
        got = S.issuance_5y(_FakePanel(), fund, pd.Timestamp("2024-12-31"),
                            pd.Index(["BUYB"]))
        self.assertGreater(got["BUYB"], 0.0)
        self.assertAlmostEqual(got["BUYB"], 0.2, places=9)

    def test_issuance_skips_missing_or_nonpositive(self):
        table = _shares_table({"NONE": {2024: 100e6},
                               "ZERO": {2019: 0.0, 2024: 100e6}}, self.YEARS)
        fund = _FakeFund(table, year=2024)
        got = S.issuance_5y(_FakePanel(), fund, pd.Timestamp("2024-12-31"),
                            pd.Index(["NONE", "ZERO"]))
        self.assertEqual(len(got), 0)


def _fund_table(rows: dict, year: int = 2024) -> pd.DataFrame:
    """rows = {ticker: {field: value}} -> a (ticker, year) MultiIndex frame."""
    idx = pd.MultiIndex.from_product([sorted(rows), [year]],
                                     names=["ticker", "year"])
    fields = sorted({f for v in rows.values() for f in v})
    data = {f: [rows[tk].get(f, np.nan) for tk, _ in idx] for f in fields}
    return pd.DataFrame(data, index=idx, dtype=float)


class TestValueEBM(unittest.TestCase):
    """EBM = (equity + net_cash) / (market_cap + net_cash)."""

    def _ebm(self, rows: dict, caps: dict) -> pd.Series:
        fund = _FakeFund(_fund_table(rows), year=2024)
        names = pd.Index(sorted(rows))
        real = S.market_cap_at
        S.market_cap_at = lambda p, f, fy, pd_, n: pd.Series(
            caps, dtype=float).reindex(n).dropna()
        try:
            return S.value_ebm(_FakePanel(), fund, pd.Timestamp("2025-06-30"),
                               names)
        finally:
            S.market_cap_at = real

    def test_debt_free_firm_is_plain_book_to_market(self):
        got = self._ebm({"FLAT": {"equity": 100.0, "net_cash": 0.0}},
                        {"FLAT": 200.0})
        self.assertAlmostEqual(got["FLAT"], 0.5, places=12)

    def test_net_cash_and_net_debt_move_the_ratio_opposite_ways(self):
        rows = {"CASHY": {"equity": 100.0, "net_cash": 50.0},
                "LEVD": {"equity": 100.0, "net_cash": -50.0}}
        got = self._ebm(rows, {"CASHY": 200.0, "LEVD": 200.0})
        self.assertAlmostEqual(got["CASHY"], 150.0 / 250.0, places=12)
        self.assertAlmostEqual(got["LEVD"], 50.0 / 150.0, places=12)

    def test_cheaper_firm_ranks_higher(self):
        """Sign check: the sleeve takes nlargest, so cheap must score high."""
        rows = {"CHEAP": {"equity": 100.0, "net_cash": 0.0},
                "RICH": {"equity": 100.0, "net_cash": 0.0}}
        got = self._ebm(rows, {"CHEAP": 120.0, "RICH": 900.0})
        self.assertGreater(got["CHEAP"], got["RICH"])

    def test_ebm_rejects_double_negative(self):
        """Net debt above BOTH book equity and market cap must be dropped.

        num = 20 - 300 = -280, den = 100 - 300 = -200, ratio = +1.4 — which
        would outrank the 0.5 of a healthy firm and put the most distressed
        name in the index at the top of a VALUE sort.
        """
        rows = {"DISTRESS": {"equity": 20.0, "net_cash": -300.0},
                "FLAT": {"equity": 100.0, "net_cash": 0.0}}
        got = self._ebm(rows, {"DISTRESS": 100.0, "FLAT": 200.0})
        self.assertNotIn("DISTRESS", got.index)
        self.assertIn("FLAT", got.index)

    def test_negative_denominator_alone_is_dropped(self):
        rows = {"LEVERED": {"equity": 500.0, "net_cash": -300.0}}
        got = self._ebm(rows, {"LEVERED": 100.0})   # den = -200, num = +200
        self.assertEqual(len(got), 0)

    def test_negative_book_equity_is_dropped(self):
        rows = {"NEGBOOK": {"equity": -50.0, "net_cash": 100.0}}
        got = self._ebm(rows, {"NEGBOOK": 200.0})   # den = 300 > 0, but eq < 0
        self.assertEqual(len(got), 0)

    def test_missing_market_cap_is_skipped_not_guessed(self):
        rows = {"HAVE": {"equity": 100.0, "net_cash": 0.0},
                "NOCAP": {"equity": 100.0, "net_cash": 0.0}}
        got = self._ebm(rows, {"HAVE": 200.0})
        self.assertEqual(list(got.index), ["HAVE"])


class TestPayoutYield(unittest.TestCase):
    """(dividends + repurchases) / market equity lagged 6 months."""

    def _py(self, rows: dict, caps: dict) -> pd.Series:
        fund = _FakeFund(_fund_table(rows), year=2024)
        real = S.market_cap_at
        S.market_cap_at = lambda p, f, fy, pdate, n: pd.Series(
            caps, dtype=float).reindex(n).dropna()
        try:
            return S.payout_yield(_FakePanel(), fund,
                                  pd.Timestamp("2025-06-30"),
                                  pd.Index(sorted(rows)))
        finally:
            S.market_cap_at = real

    def test_yield_is_payout_over_cap(self):
        got = self._py({"PAYER": {"payout": 5e9, "equity": 40e9}},
                       {"PAYER": 100e9})
        self.assertAlmostEqual(got["PAYER"], 0.05, places=12)

    def test_bigger_payer_ranks_higher(self):
        """Sign check: the sleeve takes nlargest, so high payout must score high."""
        rows = {"BIG": {"payout": 8e9, "equity": 40e9},
                "SMALL": {"payout": 1e9, "equity": 40e9}}
        got = self._py(rows, {"BIG": 100e9, "SMALL": 100e9})
        self.assertGreater(got["BIG"], got["SMALL"])

    def test_zero_and_negative_payout_excluded(self):
        """Boudoukh et al. exclude payout <= 0 — it is a different population.

        Keeping a non-payer as a 0.0 would not change who lands in the top
        quintile, but it changes the covered universe the sleeve is benchmarked
        against, which is how a coverage effect gets measured as skill.
        """
        rows = {"NONE": {"payout": 0.0, "equity": 40e9},
                "NEG": {"payout": -1e9, "equity": 40e9},
                "PAYER": {"payout": 2e9, "equity": 40e9}}
        got = self._py(rows, {k: 100e9 for k in rows})
        self.assertEqual(list(got.index), ["PAYER"])

    def test_negative_book_equity_excluded(self):
        got = self._py({"NEGBOOK": {"payout": 5e9, "equity": -1e9}},
                       {"NEGBOOK": 100e9})
        self.assertEqual(len(got), 0)

    def test_missing_payout_is_not_treated_as_zero(self):
        rows = {"UNKNOWN": {"equity": 40e9}, "PAYER": {"payout": 2e9,
                                                       "equity": 40e9}}
        got = self._py(rows, {k: 100e9 for k in rows})
        self.assertEqual(list(got.index), ["PAYER"])

    def test_prices_at_june_of_the_fiscal_year(self):
        """"Lagged 6 months" from a 31 Dec year end is that year's June close."""
        seen = {}

        def spy(p, f, fy, pdate, n):
            seen["fy"], seen["date"] = fy, pd.Timestamp(pdate)
            return pd.Series({"ACME": 100e9})

        fund = _FakeFund(_fund_table({"ACME": {"payout": 5e9,
                                               "equity": 40e9}}), year=2024)
        real = S.market_cap_at
        S.market_cap_at = spy
        try:
            S.payout_yield(_FakePanel(), fund, pd.Timestamp("2025-06-30"),
                           pd.Index(["ACME"]))
        finally:
            S.market_cap_at = real
        self.assertEqual(seen["fy"], 2024)
        self.assertEqual(seen["date"], pd.Timestamp("2024-06-30"))


class TestEBMTiming(unittest.TestCase):
    """Market equity must be measured at the fiscal year end.

    Pricing the ratio at the June rebalance instead puts six months of price
    change in the numerator and none in the denominator, which makes the value
    sleeve part 6-month reversal — the horizon of the momentum sleeve with the
    sign flipped. That would raise its correlation with momentum and destroy the
    only reason the sleeve exists. The bug is invisible in aggregate results, so
    it has to be pinned here.
    """

    def setUp(self):
        # a full calendar year of sessions, price doubling across it
        idx = pd.bdate_range("2024-01-01", "2025-07-31")
        self.panel = _FakePanel(n=10)
        self.panel.close = pd.DataFrame(
            {"ACME": np.linspace(50.0, 150.0, len(idx))}, index=idx)
        self.panel.adj = self.panel.close.copy()
        self.fund = _FakeFund(
            _fund_table({"ACME": {"equity": 100e6, "net_cash": 0.0}}), year=2024)
        self._real = S.adjusted_shares
        S.adjusted_shares = lambda p, f: pd.DataFrame({2024: {"ACME": 1e6}})

    def tearDown(self):
        S.adjusted_shares = self._real

    def _price_on(self, date: str) -> float:
        i = self.panel.close.index.get_indexer([pd.Timestamp(date)],
                                               method="pad")[0]
        return float(self.panel.close.iloc[i, 0])

    def test_market_cap_at_uses_that_dates_close(self):
        cap = S.market_cap_at(self.panel, self.fund, 2024,
                              pd.Timestamp("2024-12-31"), pd.Index(["ACME"]))
        self.assertAlmostEqual(float(cap["ACME"]),
                               1e6 * self._price_on("2024-12-31"), places=4)

    def test_value_ebm_prices_at_fiscal_year_end_not_rebalance(self):
        got = S.value_ebm(self.panel, self.fund, pd.Timestamp("2025-06-30"),
                          pd.Index(["ACME"]))
        fye = 100e6 / (1e6 * self._price_on("2024-12-31"))
        june = 100e6 / (1e6 * self._price_on("2025-06-30"))
        self.assertAlmostEqual(float(got["ACME"]), fye, places=9)
        # and the two must actually differ, or this test proves nothing
        self.assertGreater(abs(fye - june), 0.05)


class TestSignalSigns(unittest.TestCase):
    def test_reversal_sign(self):
        """The name that FELL must rank higher — reversal buys losers."""
        panel = _FakePanel()
        fund = _FakeFund(_shares_table({}, (2024,)), year=2024)
        got = S.reversal_1m(panel, fund, panel.adj.index[-1],
                            pd.Index(["AAA", "BBB"]), residual=False)
        self.assertGreater(got["BBB"], got["AAA"])

    def test_reversal_residualises_market(self):
        """Residualising must change the score by exactly SPY's own return."""
        panel = _FakePanel()
        fund = _FakeFund(_shares_table({}, (2024,)), year=2024)
        d = panel.adj.index[-1]
        raw = S.reversal_1m(panel, fund, d, pd.Index(["AAA"]), residual=False)
        res = S.reversal_1m(panel, fund, d, pd.Index(["AAA"]), residual=True)
        i = panel.adj.index.get_indexer([d], method="pad")[0]
        spy_r = panel.spy.iloc[i] / panel.spy.iloc[i - 21] - 1.0
        # signal is negated, so residualising ADDS spy's return back
        self.assertAlmostEqual(res["AAA"] - raw["AAA"], spy_r, places=12)

    def test_investment_sign(self):
        """LOW asset growth must rank higher — Cooper, Gulen & Schill."""
        idx = pd.MultiIndex.from_product([["LOW", "HIGH"], [2024]],
                                         names=["ticker", "year"])
        table = pd.DataFrame({"asset_growth": pd.Series([0.02, 0.60], index=idx)})
        fund = _FakeFund(table, year=2024)
        got = S.investment_asset_growth(_FakePanel(), fund,
                                        pd.Timestamp("2024-12-31"),
                                        pd.Index(["LOW", "HIGH"]))
        self.assertGreater(got["LOW"], got["HIGH"])

    def test_momentum_skips_last_month(self):
        """12-1 momentum must not use the most recent month's return."""
        panel = _FakePanel(n=400)
        fund = _FakeFund(_shares_table({}, (2024,)), year=2024)
        d = panel.adj.index[-1]
        base = S.momentum_12_1(panel, fund, d, pd.Index(["AAA"]))
        # The formation window ends at the SECOND-to-last month end; a violent
        # move strictly after it must leave the signal untouched. Bump from the
        # session after that boundary rather than a fixed number of days back,
        # which can straddle it on a short synthetic calendar.
        me = panel.month_ends(None, str(d.date()))
        cut = panel.adj.index.get_indexer([me[-2]])[0] + 1
        spiked = _FakePanel(n=400)
        spiked.adj.iloc[cut:, spiked.adj.columns.get_loc("AAA")] *= 2.0
        after = S.momentum_12_1(spiked, fund, d, pd.Index(["AAA"]))
        self.assertAlmostEqual(float(base["AAA"]), float(after["AAA"]), places=9)
        # and a move BEFORE the boundary must change it, or the test is vacuous
        earlier = _FakePanel(n=400)
        earlier.adj.iloc[cut - 2:, earlier.adj.columns.get_loc("AAA")] *= 2.0
        self.assertNotAlmostEqual(
            float(base["AAA"]),
            float(S.momentum_12_1(earlier, fund, d, pd.Index(["AAA"]))["AAA"]),
            places=6)


class TestVolScaling(unittest.TestCase):
    def test_realised_vol_is_knowable(self):
        """The value on session D must not depend on D's own return."""
        idx = pd.bdate_range("2020-01-01", periods=300)
        rng = np.random.default_rng(0)
        r = pd.Series(rng.normal(0, 0.01, 300), index=idx)
        v1 = S.realised_vol(r, window=126)
        bumped = r.copy()
        bumped.iloc[-1] = 0.25          # a huge move on the LAST session
        v2 = S.realised_vol(bumped, window=126)
        self.assertAlmostEqual(float(v1.iloc[-1]), float(v2.iloc[-1]), places=12)

    def test_scale_is_inverse_in_vol_and_capped(self):
        idx = pd.bdate_range("2020-01-01", periods=400)
        rng = np.random.default_rng(1)
        # calm first half, turbulent second half
        r = pd.Series(np.concatenate([rng.normal(0, 0.004, 200),
                                      rng.normal(0, 0.02, 200)]), index=idx)
        k = S.scale_by_own_vol(r, idx, cap=1.5)
        self.assertLessEqual(float(k.max()), 1.5 + 1e-12)
        self.assertGreater(float(k.iloc[210]), float(k.iloc[-1]))
        self.assertTrue(np.isfinite(k).all())


class TestNoTradeBand(unittest.TestCase):
    """A held name must survive a slip past the entry rank but inside the band.

    Without the band a strict quintile sells a name the moment it slips to rank
    21% and buys it back when it returns, which is churn against a signal that
    barely moved. With it, `build_sleeve` becomes stateful across rebalances, so
    the invariants worth pinning are: the band never sells a name that still
    qualifies to ENTER, it does sell one that leaves the wider band, and a
    tighter exit than entry is rejected outright rather than silently churning.
    """

    def test_exit_tighter_than_entry_is_rejected(self):
        with self.assertRaises(ValueError):
            S.Sleeve("bad", S.momentum_12_1, "monthly", "momentum", "Mom12m",
                     top_fraction=0.20, exit_fraction=0.10)

    def test_default_registry_has_no_band(self):
        """The shipped sleeves are strict quintiles; the band is opt-in."""
        for name in S.DEFAULT_SLEEVES:
            s = S.REGISTRY[name]
            self.assertEqual(s.exit_fraction, s.top_fraction, name)

    def test_band_retains_a_slipping_name_and_drops_a_falling_one(self):
        """Rank 25 of 100 survives a 0.20/0.40 band; rank 55 does not."""
        n = 100
        names = pd.Index([f"T{i:03d}" for i in range(n)])

        # signal ranks T000 best .. T099 worst on the first date; on the second,
        # the incumbent T000 slips to rank 25 and T001 slips to rank 55
        seq = [list(names), None]
        after = [f"T{i:03d}" for i in range(2, n)]
        after.insert(24, "T000")            # -> rank 25 of 100, inside 0.40
        after.insert(54, "T001")            # -> rank 55 of 100, outside 0.40
        seq[1] = after

        calls = {"i": 0}

        def signal(panel, fund, date, universe):
            order = seq[min(calls["i"], 1)]
            calls["i"] += 1
            return pd.Series(np.linspace(1.0, 0.0, n),
                             index=pd.Index(order), dtype=float)

        sleeve = S.Sleeve("banded", signal, "monthly", "test", "None",
                          top_fraction=0.20, exit_fraction=0.40)
        panel = _FakePanel(n=60)
        panel.adj = pd.DataFrame(
            {t: np.linspace(100.0, 110.0, 60) for t in names},
            index=panel.adj.index)
        panel.close = panel.adj.copy()
        fund = _FakeFund(_fund_table({t: {"equity": 1.0} for t in names[:1]}))

        real = S.market_cap
        S.market_cap = lambda p, f, d, u: pd.Series(1e9, index=names)
        try:
            targets, _, diag = S.build_sleeve(panel, fund, sleeve,
                                              None, None, min_names=40)
        finally:
            S.market_cap = real

        self.assertGreaterEqual(len(targets), 2)
        second = targets[sorted(targets)[1]]
        self.assertIn("T000", second.index,
                      "rank 25 of 100 is inside the 0.40 band and was held")
        self.assertNotIn("T001", second.index,
                         "rank 55 of 100 is outside the 0.40 band and must go")
        # and the band must actually have held something over, or the fixture
        # is not exercising the code path at all
        self.assertGreater(int(diag.iloc[1]["held_over"]), 0)


class TestRegistry(unittest.TestCase):
    def test_no_two_default_sleeves_share_a_theme(self):
        themes = [S.REGISTRY[n].theme for n in S.DEFAULT_SLEEVES]
        self.assertEqual(len(themes), len(set(themes)),
                         f"duplicate themes in DEFAULT_SLEEVES: {themes}")

    def test_default_sleeves_all_registered(self):
        for n in S.DEFAULT_SLEEVES:
            self.assertIn(n, S.REGISTRY)

    def test_reversal_default_is_monthly(self):
        """The weekly variant turned over 1,311%/yr; it must not be the default."""
        self.assertEqual(S.REGISTRY["reversal"].calendar, "monthly")
        self.assertNotIn("reversal_fast", S.DEFAULT_SLEEVES)

    def test_calendars_are_ordered_and_distinct(self):
        panel = _FakePanel(n=300)
        for name in ("weekly", "monthly"):
            d = S.CALENDARS[name](panel, None, None)
            self.assertGreater(len(d), 0, name)
            self.assertTrue(pd.Index(d).is_monotonic_increasing, name)
            self.assertEqual(len(d), len(set(d)), name)
        weekly = S.CALENDARS["weekly"](panel, None, None)
        monthly = S.CALENDARS["monthly"](panel, None, None)
        self.assertGreater(len(weekly), len(monthly))


if __name__ == "__main__":
    unittest.main(verbosity=1)
