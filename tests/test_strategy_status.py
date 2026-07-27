"""
test_strategy_status.py — autonomous vs manual deployment tiers.

The unattended Robinhood agent auto-routes by SPY regime. Nydex personal
configuration and the research CLIs select a strategy by hand. Those are
different risk surfaces and must not share a permission model: a sleeve that is
fine to experiment with by hand is not thereby cleared to trade unattended.

Invariants pinned here:

  1. status defaults to "manual" — autonomy is earned, never inherited
  2. auto-routing NEVER returns a non-autonomous strategy, even when
     regime_routing names one (demoting to "manual" is sufficient to pull a
     sleeve out of unattended trading)
  3. no cleared strategy for a regime => None => the agent stands flat, rather
     than falling back to an arbitrary sleeve
  4. forcing a strategy explicitly bypasses the restriction (manual path)
  5. the shipped spec is internally consistent

Audit: docs/AUDIT-2026-07-24.md FC-20 (routing table was hardcoded in
indicators.py while strategies.json regime_routing sat unread — two sources of
truth able to disagree silently).
"""

import unittest

from rhengine.backtest import run_backtest_on
from rhengine.data_local import synthetic_universe
from rhengine.indicators import (
    VALID_STATUSES,
    autonomous_strategies,
    load_spec,
    select_strategy_for_regime,
    strategy_cost_profile,
    strategy_status,
    user_configurable_strategies,
)


def _spec(routing, statuses):
    return {"regime_routing": routing,
            "strategies": {k: dict(v) for k, v in statuses.items()}}


class StatusDefaultTest(unittest.TestCase):

    def test_unmarked_strategy_defaults_to_manual(self):
        spec = _spec({}, {"foo": {}})
        self.assertEqual(strategy_status("foo", spec), "manual")

    def test_retired_flag_wins(self):
        spec = _spec({}, {"foo": {"retired": True, "status": "autonomous"}})
        self.assertEqual(strategy_status("foo", spec), "retired",
                         "a retired strategy must never read as autonomous")

    def test_unknown_strategy_is_manual_not_crash(self):
        self.assertEqual(strategy_status("does_not_exist", _spec({}, {})), "manual")

    def test_autonomous_list(self):
        spec = _spec({}, {"a": {"status": "autonomous"},
                          "b": {"status": "manual"},
                          "c": {"retired": True, "status": "autonomous"}})
        self.assertEqual(autonomous_strategies(spec), ["a"])


class RoutingGuardTest(unittest.TestCase):

    def test_routes_to_an_autonomous_strategy(self):
        spec = _spec({"uptrend": "a"}, {"a": {"status": "autonomous"}})
        self.assertEqual(select_strategy_for_regime("uptrend", spec), "a")

    def test_refuses_to_route_to_a_manual_strategy(self):
        """THE guard. Demoting to manual must remove it from auto-routing even
        though regime_routing still names it."""
        spec = _spec({"uptrend": "a"}, {"a": {"status": "manual"}})
        self.assertIsNone(select_strategy_for_regime("uptrend", spec))

    def test_refuses_to_route_to_a_retired_strategy(self):
        spec = _spec({"uptrend": "a"}, {"a": {"retired": True}})
        self.assertIsNone(select_strategy_for_regime("uptrend", spec))

    def test_unrouted_regime_is_none_not_a_fallback(self):
        """No silent fallback sleeve. The old code returned 'vwap' for any
        unknown regime, which auto-traded a never-validated strategy."""
        spec = _spec({"uptrend": "a"}, {"a": {"status": "autonomous"}})
        self.assertIsNone(select_strategy_for_regime("choppy", spec))
        self.assertIsNone(select_strategy_for_regime("banana", spec))

    def test_empty_autonomous_set_routes_nothing(self):
        spec = _spec({"uptrend": "a", "downtrend": "a", "choppy": "b"},
                     {"a": {"status": "manual"}, "b": {"status": "manual"}})
        for regime in ("uptrend", "downtrend", "choppy"):
            self.assertIsNone(select_strategy_for_regime(regime, spec))


class BacktestStandsFlatTest(unittest.TestCase):

    def test_auto_mode_emits_nothing_when_nothing_is_cleared(self):
        """A backtest in auto mode with an empty autonomous set must produce no
        trades — the agent's book has to include the periods it must do nothing.
        """
        bars_map, spy_bars = synthetic_universe(n_tickers=4, n_bars=200, seed=5)
        cfg = {"strategy": {"mode": "auto", "thresholds": {}},
               "exits": {"max_hold_sessions": 5},
               "universe_filters": {"min_price": 0.0}}

        import rhengine.indicators as ind
        real = ind.select_strategy_for_regime
        ind.select_strategy_for_regime = lambda regime, spec=None: None
        try:
            import rhengine.backtest as bt
            real_bt = bt.select_strategy_for_regime
            bt.select_strategy_for_regime = lambda regime, spec=None: None
            try:
                trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=3)
            finally:
                bt.select_strategy_for_regime = real_bt
        finally:
            ind.select_strategy_for_regime = real
        self.assertEqual(trades, [])

    def test_forced_strategy_still_works_when_nothing_is_autonomous(self):
        """The manual / Nydex path must be unaffected by the autonomy guard."""
        bars_map, spy_bars = synthetic_universe(n_tickers=4, n_bars=200, seed=5)
        cfg = {"strategy": {"mode": "auto", "thresholds": {}},
               "exits": {"max_hold_sessions": 5},
               "universe_filters": {"min_price": 0.0}}
        trades = run_backtest_on(cfg, bars_map, spy_bars, min_score=3,
                                 forced_strategy="bollinger")
        self.assertIsInstance(trades, list)   # runs; may be empty on fixture data


class ShippedSpecTest(unittest.TestCase):
    """The spec actually shipped must be internally consistent."""

    def test_routing_targets_exist(self):
        spec = load_spec()
        for regime, name in (spec.get("regime_routing") or {}).items():
            if regime.startswith("_") or not isinstance(name, str):
                continue
            self.assertIn(name, spec["strategies"],
                          "regime_routing[{}] names unknown strategy {}".format(
                              regime, name))

    def test_every_strategy_has_an_explicit_status(self):
        spec = load_spec()
        for name, s in spec["strategies"].items():
            self.assertTrue("status" in s or s.get("retired"),
                            "{} has no explicit status — it would silently "
                            "default to manual".format(name))

    def test_routed_strategies_are_autonomous(self):
        """If a regime routes somewhere, that target must be cleared; otherwise
        the routing entry is dead weight that reads as if it were live."""
        spec = load_spec()
        for regime, name in (spec.get("regime_routing") or {}).items():
            if regime.startswith("_") or not isinstance(name, str):
                continue
            self.assertEqual(
                strategy_status(name, spec), "autonomous",
                "regime_routing[{}] -> {} but its status is {}".format(
                    regime, name, strategy_status(name, spec)))

    def test_every_status_is_a_known_tier(self):
        """A typo'd status fails safe (never autonomous) but would silently
        misrepresent the sleeve to Nydex, which renders by tier."""
        spec = load_spec()
        for name, s in spec["strategies"].items():
            self.assertIn(strategy_status(name, spec), VALID_STATUSES,
                          "{} carries unknown status {!r}".format(
                              name, s.get("status")))


class UserConfigurableTierTest(unittest.TestCase):
    """`user_configurable` — shipped for a Nydex user to run themselves.

    Added 2026-07-25. `manual` conflated two different risk surfaces: attended
    research selection, and a user configuring a sleeve and leaving it running
    unattended. Only the first was ever evaluated. The tier confers NO agent
    permission — that separation is what these tests pin.
    """

    def test_never_auto_routes(self):
        """THE guard for this tier. A user running it themselves is their
        decision; the agent choosing it on their behalf is not."""
        spec = _spec({"uptrend": "a"}, {"a": {"status": "user_configurable"}})
        self.assertIsNone(select_strategy_for_regime("uptrend", spec))
        self.assertEqual(autonomous_strategies(spec), [])

    def test_listed_separately_from_autonomous(self):
        spec = _spec({}, {"a": {"status": "autonomous"},
                          "b": {"status": "user_configurable"},
                          "c": {"status": "manual"},
                          "d": {"retired": True, "status": "user_configurable"}})
        self.assertEqual(user_configurable_strategies(spec), ["b"])
        self.assertEqual(autonomous_strategies(spec), ["a"])

    def test_tiers_are_disjoint_in_shipped_spec(self):
        spec = load_spec()
        self.assertEqual(
            set(user_configurable_strategies(spec)) & set(autonomous_strategies(spec)),
            set())

    def test_shipped_sleeves_are_user_configurable(self):
        spec = load_spec()
        self.assertEqual(
            user_configurable_strategies(spec),
            ["bollinger", "macd", "pead", "residualmomentum", "vwap"])

    def test_each_publishes_a_cost_profile(self):
        """The tier's price of admission: a user cannot judge a strategy whose
        measured breakeven is hidden from them.

        Cost is MEASURED from live quotes (2026-07-27), not assumed — every
        field here is in ROUND-TRIP bps, the unit the measurement is in. Do not
        reintroduce a per-side field without renaming it; mixing the two units
        is the bug that made the engine charge 2x (backtest._cost_r).
        """
        spec = load_spec()
        required = ("basis", "measured_cost_bps_round_trip",
                    "breakeven_bps_round_trip", "mean_r_at_measured_cost",
                    "t_day_clustered", "verdict", "measured", "caveat", "source")
        for name in user_configurable_strategies(spec):
            profile = strategy_cost_profile(name, spec)
            self.assertIsNotNone(profile, "{} publishes no cost_profile".format(name))
            for key in required:
                self.assertIn(key, profile, "{} cost_profile lacks {}".format(name, key))
            for key in ("measured_cost_bps_round_trip", "breakeven_bps_round_trip",
                        "mean_r_at_measured_cost", "t_day_clustered"):
                self.assertIsInstance(profile[key], (int, float))

    def test_cost_profile_is_self_consistent(self):
        """Breakeven above the measured cost must mean positive net R, and below
        it negative. Catches a profile edited in one field but not the other."""
        spec = load_spec()
        for name in user_configurable_strategies(spec):
            p = strategy_cost_profile(name, spec)
            if p["breakeven_bps_round_trip"] < p["measured_cost_bps_round_trip"]:
                self.assertLess(p["mean_r_at_measured_cost"], 0, name)
            else:
                self.assertGreater(p["mean_r_at_measured_cost"], 0, name)

    def test_no_sleeve_claims_a_statistically_real_edge(self):
        """The sign of mean R is not the finding — the t-stat is.

        At the measured cost every sleeve sits inside the noise (day-clustered
        |t| < 1.5). If a future edit reports a real edge here it must come with
        a t-stat that survives day clustering, because per-trade t is inflated
        several-fold by same-day trades (audit FC-3): macd reads +1.44
        per-trade and +0.45 day-clustered on the same data.
        """
        spec = load_spec()
        for name in user_configurable_strategies(spec):
            p = strategy_cost_profile(name, spec)
            self.assertLess(
                abs(p["t_day_clustered"]), 1.5,
                "{} now claims a day-clustered t of {:+.2f}. If that is real, "
                "re-run G1 and move the tier deliberately — do not let a "
                "cost_profile edit silently promote a sleeve."
                .format(name, p["t_day_clustered"]))


if __name__ == "__main__":
    unittest.main()
