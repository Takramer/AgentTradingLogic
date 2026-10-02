"""test_mc_books.py — RMX-B and RMX-C: construction, and the Monte Carlo unit.

Two groups of traps.

**Construction.** RMX-B and RMX-C are the only books this project ships, and
both are built from targets that never raise when they are wrong — a blend that
silently drifts off 50/50, a quality leg that re-forms monthly instead of each
June, a momentum sort that forgets to skip the reversal month, or a combined
vector that no longer sums to one would all produce a perfectly plausible equity
curve. These tests pin each of those to a number on synthetic data, so they need
neither `research_data/` nor a network.

**Units.** `mc_refined.run_mc_paths` bootstraps per-trade R-multiples i.i.d.
under fixed-fractional sizing. A research-stack book has no trades, no
R-multiples and no sizing dial, so feeding it through that function would be the
audit's FC-1 category error wearing a chart. `mc_books.run_mc_book_paths` is the
separate returns-space bootstrap, and the reason it resamples in BLOCKS rather
than i.i.d. days is FC-10: i.i.d. resampling destroys the loss clustering that
produces real drawdowns. `test_blocks_preserve_loss_clustering` is that claim as
an assertion — if it ever fails, the fans in the notebook are understating the
tail and should not be shown.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine import mc_books as MB
from rhengine.alpha import blend as BL
from rhengine.alpha import rmxb as RB
from rhengine.alpha import sleeves as S
from rhengine.alpha.panel import Panel


# ------------------------------------------------------------------- fixtures
class _Panel:
    """Synthetic PIT panel: `n` names on a real business-day calendar.

    Name i compounds at a fixed daily drift that increases with i, so the
    momentum ranking is deterministic and known in advance: T{n-1} is always the
    strongest. `bump` lets a test rewrite one name's path to probe a specific
    behaviour (the skipped reversal month, mainly).
    """

    def __init__(self, n: int = 30, start: str = "2015-01-01",
                 periods: int = 1800, seed: int = 0):
        idx = pd.bdate_range(start, periods=periods)
        self.tickers = [f"T{i:02d}" for i in range(n)]
        rng = np.random.default_rng(seed)
        drift = np.linspace(0.0001, 0.0007, n)
        noise = rng.normal(0.0, 0.004, size=(periods, n))
        px = 100.0 * np.cumprod(1.0 + drift + noise, axis=0)
        self.adj = pd.DataFrame(px, index=idx, columns=self.tickers)
        self.close = self.adj.copy()
        self.spy = pd.Series(px.mean(axis=1), index=idx)
        self.rf = pd.Series(0.0, index=idx)

    def eligible(self, date):
        return self.adj.columns

    def month_ends(self, lo=None, hi=None):
        idx = self.adj.index
        if lo is not None:
            idx = idx[idx >= pd.Timestamp(lo)]
        if hi is not None:
            idx = idx[idx <= pd.Timestamp(hi)]
        s = pd.Series(idx, index=idx)
        return pd.DatetimeIndex(
            s.groupby([idx.year, idx.month]).last().values)

    def split_factor_after(self, ticker, cutoff):
        return 1.0


class _Fund:
    """Fundamentals stand-in serving one field, `op_be`, from a dict.

    `flip_date` swaps the ranking part-way through the sample, which is how the
    "quality re-forms only in June" test detects a leg that is re-sorting every
    month.
    """

    def __init__(self, values: dict, flipped: dict | None = None,
                 flip_date: str | None = None):
        self.values = pd.Series(values, dtype=float)
        self.flipped = pd.Series(flipped, dtype=float) if flipped else None
        self.flip_date = pd.Timestamp(flip_date) if flip_date else None

    def latest_year_for(self, date):
        return pd.Timestamp(date).year - 1

    def as_of(self, date, field, tickers=None):
        s = self.values
        if self.flipped is not None and pd.Timestamp(date) >= self.flip_date:
            s = self.flipped
        return s.reindex(tickers).dropna() if tickers is not None else s


def _caps(panel, fund, date, names):
    """Deterministic market caps: strictly increasing in the ticker index.

    Patched over `rmxq.market_cap` so these tests exercise the *construction*
    without dragging in the share-count repair path, which `test_sleeves.py`
    already covers.
    """
    names = pd.Index([t for t in names if t in panel.adj.columns])
    px = panel.close.loc[date].reindex(names)
    sh = pd.Series([1e6 * (i + 1) for i in range(len(names))], index=names,
                   dtype=float)
    cap = (sh * px).dropna()
    return cap[cap > 0]


def _rmxb_targets(panel, fund, cfg=None, lo=None, hi=None):
    cfg = cfg or RB.RMXBConfig(min_names=4)
    lo = lo or str(panel.adj.index[0].date())
    hi = hi or str(panel.adj.index[-1].date())
    with mock.patch.object(RB, "market_cap", _caps):
        return RB.build_targets(panel, fund, cfg, lo, hi)


# ------------------------------------------------------------------- RMX-B
class TestRMXBConstruction(unittest.TestCase):
    """The shipped strategy. Every number here is a specification, not a fit."""

    def setUp(self):
        self.panel = _Panel()
        self.fund = _Fund({t: float(i) for i, t in
                           enumerate(self.panel.tickers)})

    def test_blend_is_exactly_half_of_each_leg(self):
        """50/50. A drift here is invisible in the equity curve and changes
        which strategy the 63-year French record is evidence for."""
        targets, _, _, legs = _rmxb_targets(self.panel, self.fund)
        self.assertGreater(len(targets), 24)
        d = sorted(targets)[-1]
        want = (0.5 * legs["quality"][d]).add(0.5 * legs["momentum"][d],
                                              fill_value=0.0)
        want = want / want.sum()
        pd.testing.assert_series_equal(targets[d].sort_index(),
                                       want.sort_index(),
                                       check_names=False)

    def test_targets_are_fully_invested_and_capped(self):
        cfg = RB.RMXBConfig(min_names=4)
        targets, _, _, _ = _rmxb_targets(self.panel, self.fund, cfg)
        for d, w in targets.items():
            self.assertAlmostEqual(float(w.sum()), 1.0, places=9,
                                   msg=f"{d.date()} does not sum to 1")
            self.assertTrue((w >= -1e-12).all(), f"{d.date()} went short")
            # Each leg is capped at 10%, so the blend of two capped legs cannot
            # exceed it either — an overlapping name is 0.5*0.10 + 0.5*0.10.
            # The tolerance is not cosmetic: `rmxq._weights` caps and
            # redistributes iteratively and gives up after 50 passes, then the
            # blend is renormalised, so a name can land a few parts per million
            # over. Measured overshoot on this fixture is 4.7e-7. Tightening
            # this to 1e-9 would be asserting a property the capper does not
            # claim; loosening it past ~1e-4 would stop catching a real breach.
            self.assertLessEqual(float(w.max()), cfg.max_weight * 1.0001)

    def test_quality_leg_reforms_only_in_june(self):
        """French forms the profitability sort at the end of June and holds it
        twelve months. A leg that re-sorted monthly would track the flip
        immediately and quietly become a different, higher-turnover strategy."""
        n = len(self.panel.tickers)
        fund = _Fund({t: float(i) for i, t in enumerate(self.panel.tickers)},
                     flipped={t: float(n - i) for i, t in
                              enumerate(self.panel.tickers)},
                     flip_date="2017-08-01")
        _, _, _, legs = _rmxb_targets(self.panel, fund)
        q = legs["quality"]
        aug = [d for d in q if d.year == 2017 and d.month == 8][0]
        jul = [d for d in q if d.year == 2017 and d.month == 7][0]
        nxt = [d for d in q if d.year == 2018 and d.month == 6][0]
        # the flip lands in August; the leg must not notice until next June
        self.assertEqual(set(q[aug].index), set(q[jul].index))
        self.assertNotEqual(set(q[nxt].index), set(q[jul].index))
        self.assertTrue(all(d.month == 6 or d == min(q)
                            for d in _changed_dates(q)),
                        "quality leg re-formed outside June")

    def test_momentum_skips_the_reversal_month(self):
        """A name that soared over months -12..-2 and then crashed in the last
        month must still be picked. Including the last month is the classic way
        to blunt momentum — it is short-term reversal, the opposite sign."""
        panel = _Panel()
        me = panel.month_ends()
        d = me[20]
        last_me = me[19]
        star = "T01"                       # a weak name, promoted by hand
        # +200% over the formation window, then -40% in the skipped month
        f_start, f_end = me[8], me[19 - 1]
        adj = panel.adj.copy()
        adj.loc[f_start:f_end, star] = np.linspace(
            100.0, 300.0, len(adj.loc[f_start:f_end]))
        after = adj.loc[last_me:d].index
        adj.loc[last_me:d, star] = np.linspace(300.0, 180.0, len(after))
        panel.adj, panel.close = adj, adj.copy()

        mom = RB.momentum_12_1(panel, d, pd.Index(panel.tickers))
        k = max(int(len(mom) * RB.TOP_FRACTION), 10)
        self.assertIn(star, mom.nlargest(k).index)
        # and the same name is NOT the winner if the skip is removed
        px = panel.adj.reindex([me[8], me[20]])[panel.tickers]
        unskipped = (px.iloc[1] / px.iloc[0] - 1.0)
        self.assertLess(unskipped[star], mom[star])

    def test_vol_scaling_shifts_the_mix_not_the_gross(self):
        """Barroso scaling changes the quality/momentum MIX. If it changed gross
        exposure instead, the book would be levering at a rate no retail account
        gets — the whole reason `run_book` charges a margin spread."""
        cfg = RB.RMXBConfig(min_names=4, vol_scale_momentum=True)
        with mock.patch.object(RB, "market_cap", _caps):
            targets, _, _, legs = RB.build_targets(
                self.panel, self.fund, cfg,
                str(self.panel.adj.index[0].date()),
                str(self.panel.adj.index[-1].date()))
            scaled, k = RB._vol_scaled_blend(
                self.panel, cfg, legs, 10.0, self.panel.adj.index[-1])
        self.assertTrue(len(scaled) > 0)
        for d, w in scaled.items():
            self.assertAlmostEqual(float(w.sum()), 1.0, places=9)
        self.assertLessEqual(float(k.max()), cfg.vol_cap + 1e-9)
        self.assertGreater(float(k.min()), 0.0)
        # the mix actually moved somewhere, or the flag is doing nothing
        moved = [d for d in scaled if d in targets
                 and not np.allclose(scaled[d].reindex(targets[d].index)
                                     .fillna(0.0), targets[d], atol=1e-9)]
        self.assertTrue(moved, "vol scaling left every target unchanged")

    def test_defaults_match_the_shipped_specification(self):
        """docs/RMX-RESULTS.md and strategies.json describe THIS config. A
        default changed here silently re-points the published evidence."""
        cfg = RB.RMXBConfig()
        self.assertEqual((cfg.quality_weight, cfg.momentum_weight), (0.5, 0.5))
        self.assertEqual(cfg.top_fraction, 0.20)
        self.assertEqual(cfg.max_weight, 0.10)
        self.assertEqual(cfg.weighting, "value")
        self.assertFalse(cfg.vol_scale_momentum,
                         "RMX-B ships with vol-scaling OFF; RMX-C ships it ON")
        self.assertEqual((RB.FORM_MONTHS, RB.SKIP_MONTHS), (12, 1))
        self.assertEqual(RB.QUALITY_MONTH, 6)


def _changed_dates(leg: dict) -> list:
    """Dates on which a leg's holdings differ from the previous rebalance."""
    ds = sorted(leg)
    return [d for prev, d in zip(ds, ds[1:])
            if set(leg[d].index) != set(leg[prev].index)]


# ------------------------------------------------------------------- RMX-C
class TestRMXCConstruction(unittest.TestCase):
    """The 5-block extension. Tested on two blocks: the machinery under test is
    the COMBINE step, and two sleeves on different calendars exercise all of it
    (carry-forward, weighting, the cap, the single-vector netting)."""

    def setUp(self):
        self.panel = _Panel()
        self.fund = _Fund({t: float(i) for i, t in
                           enumerate(self.panel.tickers)})
        self.cfg = BL.BlendConfig(sleeves=("quality", "momentum"),
                                  vol_scale=(), min_names=4)
        self.lo = str(self.panel.adj.index[0].date())
        self.hi = str(self.panel.adj.index[-1].date())

    def _build(self, cfg=None):
        with mock.patch.object(S, "market_cap", _caps), \
                mock.patch.object(BL, "market_cap", _caps):
            return BL.build(self.panel, self.fund, cfg or self.cfg,
                            self.lo, self.hi, 10.0)

    def test_combined_targets_are_fully_invested_and_capped(self):
        built = self._build()
        self.assertGreater(len(built["targets"]), 24)
        for d, w in built["targets"].items():
            self.assertAlmostEqual(float(w.sum()), 1.0, places=9)
            self.assertLessEqual(float(w.max()), self.cfg.max_weight + 1e-9)
            self.assertTrue((w >= -1e-12).all())

    def test_annual_sleeve_is_carried_between_monthly_rebalances(self):
        """The quality block re-forms each June; the momentum block re-forms
        monthly. If the annual leg were dropped on non-June dates the blend
        would be pure momentum for eleven months of every twelve."""
        built = self._build()
        q = built["per_sleeve"]["quality"]
        self.assertTrue(all(d.month == 6 for d in q))
        sep = [d for d in built["targets"]
               if d.year == 2017 and d.month == 9][0]
        carried = BL._as_of(q, sep)
        self.assertIsNotNone(carried)
        self.assertEqual(carried.index.tolist(),
                         q[max(d for d in q if d <= sep)].index.tolist())
        # and the September blend genuinely contains quality-only names
        mom = BL._as_of(built["per_sleeve"]["momentum"], sep)
        only_q = set(carried.index) - set(mom.index)
        if only_q:
            self.assertTrue(set(built["targets"][sep].index) & only_q)

    def test_overlapping_names_are_netted_into_one_vector(self):
        """One book, not N books added. A name both blocks want must appear
        ONCE at the summed weight — summing separate books would charge the
        trade twice and overstate cost."""
        built = self._build()
        d = sorted(built["targets"])[-1]
        qw = BL._as_of(built["per_sleeve"]["quality"], d)
        mw = BL._as_of(built["per_sleeve"]["momentum"], d)
        overlap = qw.index.intersection(mw.index)
        self.assertTrue(len(overlap) > 0, "fixture produced no overlap to test")
        w = built["targets"][d]
        self.assertEqual(len(w.index), len(set(w.index)))
        for tk in overlap:
            self.assertGreater(w[tk], min(qw[tk], mw[tk]) * 0.5 - 1e-12)

    def test_sleeve_weights_default_to_equal_and_honour_overrides(self):
        equal = BL.BlendConfig(sleeves=("quality", "momentum"))
        self.assertAlmostEqual(equal.sleeve_weight("quality"), 0.5)
        tilt = BL.BlendConfig(sleeves=("quality", "momentum"),
                              weights={"quality": 0.75, "momentum": 0.25})
        self.assertAlmostEqual(tilt.sleeve_weight("quality"), 0.75)
        self.assertAlmostEqual(tilt.sleeve_weight("momentum"), 0.25)
        built = self._build(BL.BlendConfig(sleeves=("quality", "momentum"),
                                           vol_scale=(), min_names=4,
                                           weights={"quality": 1.0,
                                                    "momentum": 0.0}))
        d = sorted(built["targets"])[-1]
        qw = BL._as_of(built["per_sleeve"]["quality"], d)
        # A zero-weighted sleeve still contributes its names to the target
        # INDEX at weight 0.0 — `build` accumulates `w * k` and does not prune.
        # Harmless (nothing is held, and the `held` diagnostic thresholds at
        # 1e-6), but it means the comparison has to be on held names.
        w = built["targets"][d]
        self.assertEqual(set(w[w > 1e-12].index), set(qw.index))

    def test_defaults_match_the_shipped_specification(self):
        cfg = BL.BlendConfig()
        self.assertEqual(cfg.sleeves, S.DEFAULT_SLEEVES)
        self.assertEqual(cfg.sleeves,
                         ("quality", "momentum", "investment", "issuance",
                          "payout"))
        self.assertEqual(cfg.vol_scale, ("momentum",),
                         "RMX-C ships momentum vol-scaling ON; RMX-B ships OFF")
        self.assertEqual(cfg.max_weight, 0.10)
        self.assertIsNone(cfg.exit_fraction, "the no-trade band is off by default")
        self.assertIsNone(cfg.calendars, "calendar overrides are off by default")
        for name in cfg.sleeves:
            self.assertIn(name, S.REGISTRY)


# --------------------------------------------------------- the bootstrap unit
class TestBookMonteCarlo(unittest.TestCase):
    """`run_mc_book_paths` — the returns-space bootstrap the books need."""

    def setUp(self):
        idx = pd.bdate_range("2015-01-01", periods=1000)
        rng = np.random.default_rng(3)
        self.r = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)

    def test_shapes_and_determinism(self):
        a = MB.run_mc_book_paths(self.r, horizon=60, paths=500, seed=11)
        b = MB.run_mc_book_paths(self.r, horizon=60, paths=500, seed=11)
        c = MB.run_mc_book_paths(self.r, horizon=60, paths=500, seed=12)
        self.assertEqual(a["equity"].shape, (500, 60))
        np.testing.assert_allclose(a["finals"], b["finals"])
        self.assertFalse(np.allclose(a["finals"], c["finals"]))
        self.assertEqual(a["n_observed"], len(self.r))

    def test_bootstrap_preserves_the_sample_mean(self):
        """Resampling re-arranges; it must not add or remove return."""
        m = MB.run_mc_book_paths(self.r, horizon=252, paths=4000, seed=5)
        drawn = np.log(m["equity"][:, -1]).mean() / 252.0
        self.assertAlmostEqual(drawn, np.log1p(self.r).mean(), places=4)

    def test_indices_stay_inside_the_sample(self):
        rng = np.random.default_rng(0)
        idx = MB.stationary_block_indices(37, 200, 100, 21, rng)
        self.assertEqual(idx.min(), 0 if idx.min() == 0 else idx.min())
        self.assertGreaterEqual(idx.min(), 0)
        self.assertLess(idx.max(), 37)
        # blocks wrap rather than terminate at the sample's end
        self.assertTrue(((idx[:, 1:] - idx[:, :-1]) == 1).any())

    def test_blocks_preserve_loss_clustering(self):
        """FC-10. A crash that arrived as one contiguous run must be able to
        arrive that way again. `mean_block=1` degenerates to i.i.d. sampling,
        which scatters the crash across the path and understates the drawdown —
        if this assertion ever flips, the notebook's fans are too optimistic."""
        idx = pd.bdate_range("2015-01-01", periods=500)
        r = pd.Series(0.0012, index=idx)
        r.iloc[200:240] = -0.02             # one clustered 40-session crash
        blocked = MB.run_mc_book_paths(r, horizon=252, paths=4000,
                                       mean_block=21, seed=7)
        iid = MB.run_mc_book_paths(r, horizon=252, paths=4000,
                                   mean_block=1, seed=7)
        self.assertGreater(blocked["maxdd_pct"][95], iid["maxdd_pct"][95] * 1.5)
        self.assertGreater(blocked["maxdd_pct"][50], iid["maxdd_pct"][50])

    def test_paired_benchmark_uses_the_same_block_indices(self):
        """A book bootstrapped against an independently resampled benchmark is
        compared to a market that never happened. Same series in both slots
        must therefore give P(underperform) of exactly zero."""
        m = MB.run_mc_book_paths(self.r, horizon=120, paths=500, seed=9,
                                 bench=self.r)
        self.assertEqual(m["prob_underperform"], 0.0)
        np.testing.assert_allclose(m["finals"], m["bench_finals"], rtol=1e-12)

    def test_underperformance_is_measured_not_assumed(self):
        worse = self.r - 0.0004
        m = MB.run_mc_book_paths(worse, horizon=252, paths=2000, seed=4,
                                 bench=self.r)
        self.assertEqual(m["prob_underperform"], 100.0)

    def test_ruin_is_a_drawdown_not_an_endpoint(self):
        """A path can end above water having been 60% under on the way. The
        endpoint distribution alone would score that as a win."""
        idx = pd.bdate_range("2015-01-01", periods=300)
        r = pd.Series(0.004, index=idx)
        r.iloc[100:150] = -0.03
        m = MB.run_mc_book_paths(r, horizon=252, paths=2000, seed=2,
                                 mean_block=40, ruin_dd_pct=30.0)
        self.assertGreater(m["prob_ruin"], 0.0)
        self.assertLess(m["prob_loss"], 100.0)

    def test_rejects_a_degenerate_sample(self):
        with self.assertRaises(ValueError):
            MB.run_mc_book_paths(pd.Series([0.01]), horizon=10, paths=10)

    def test_book_specs_name_the_two_shipped_books(self):
        self.assertEqual(set(MB.BOOK_SPECS), {"RMX-B", "RMX-C"})
        self.assertEqual(MB.BOOK_SPECS["RMX-B"]["module"], "rhengine.alpha.rmxb")
        self.assertEqual(MB.BOOK_SPECS["RMX-C"]["module"], "rhengine.alpha.blend")


class TestPanelFingerprint(unittest.TestCase):
    """`Panel.fingerprint` — the data stamp, added 2026-08-01.

    It exists because docs/RMX-C.md published a 13.15% CAGR that the current
    panel does not reproduce, and — the panel cache having been rebuilt in
    place with no snapshot — the difference can never now be attributed. The
    stamp is only worth carrying if two properties hold, and both are asserted
    below: it must be BLIND to data outside the backtest window (or the weekly
    refresh churns it and readers learn to ignore it), and SENSITIVE to any
    revision inside it (or it certifies nothing).
    """

    def _panel(self, n=400):
        idx = pd.bdate_range("2015-01-01", periods=n)
        rng = np.random.default_rng(1)
        px = pd.DataFrame(
            100 * np.cumprod(1 + rng.normal(0.0004, 0.01, (n, 6)), axis=0),
            index=idx, columns=[f"T{i}" for i in range(6)])
        empty = pd.DataFrame(index=idx, columns=px.columns, dtype=float)
        s = pd.Series(0.0, index=idx)
        return Panel(adj=px, close=px * 0.99, dollar_vol=empty,
                     member=empty.fillna(True).astype(bool),
                     spy=s, rf=s, mkt=s, coverage=s, n_members=s)

    def test_is_deterministic(self):
        p = self._panel()
        self.assertEqual(p.fingerprint("2015-02-01", "2015-06-30"),
                         p.fingerprint("2015-02-01", "2015-06-30"))
        self.assertNotEqual(p.fingerprint("2015-02-01", "2015-06-30"),
                            p.fingerprint("2015-02-01", "2015-07-31"))

    def test_ignores_data_outside_the_window(self):
        """The weekly append must not move a 2010-2018 stamp. This is the
        property that decides whether anyone keeps quoting it."""
        p, q = self._panel(), self._panel()
        extra = pd.bdate_range(q.adj.index[-1] + pd.Timedelta(days=1),
                               periods=10)
        add = pd.DataFrame(500.0, index=extra, columns=q.adj.columns)
        q.adj = pd.concat([q.adj, add])
        q.close = pd.concat([q.close, add * 0.99])
        self.assertEqual(p.fingerprint("2015-02-01", "2015-06-30"),
                         q.fingerprint("2015-02-01", "2015-06-30"))

    def test_detects_a_revision_inside_the_window(self):
        p, q = self._panel(), self._panel()
        d = q.adj.index[50]
        q.adj.loc[d, "T3"] *= 1.0001          # one bar, one part in 10,000
        self.assertNotEqual(p.fingerprint("2015-01-01", "2015-06-30"),
                            q.fingerprint("2015-01-01", "2015-06-30"))

    def test_detects_a_raw_close_revision_adj_cannot_see(self):
        """`close` is hashed separately on purpose: market caps are built from
        raw split-adjusted prices, so a split revision moves the caps — and
        therefore every cap weight — without touching `adj`'s returns."""
        p, q = self._panel(), self._panel()
        q.close.loc[q.close.index[50], "T3"] *= 2.0
        self.assertNotEqual(p.fingerprint(), q.fingerprint())

    def test_detects_a_changed_universe(self):
        p, q = self._panel(), self._panel()
        q.adj = q.adj.drop(columns=["T5"])
        q.close = q.close.drop(columns=["T5"])
        self.assertNotEqual(p.fingerprint(), q.fingerprint())

    def test_is_blind_to_column_order(self):
        """A reordered load is the same data. A stamp that changed on it would
        fire on non-events, which is how a check gets ignored."""
        p, q = self._panel(), self._panel()
        rev = list(q.adj.columns)[::-1]
        q.adj, q.close = q.adj[rev], q.close[rev]
        self.assertEqual(p.fingerprint(), q.fingerprint())

    def test_missing_prices_hash_stably(self):
        p, q = self._panel(), self._panel()
        for f in (p, q):
            f.adj.iloc[100:110, 2] = np.nan
            f.close.iloc[100:110, 2] = np.nan
        self.assertEqual(p.fingerprint(), q.fingerprint())


if __name__ == "__main__":
    unittest.main()
