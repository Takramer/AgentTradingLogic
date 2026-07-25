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
    autonomous_strategies,
    load_spec,
    select_strategy_for_regime,
    strategy_status,
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


if __name__ == "__main__":
    unittest.main()
