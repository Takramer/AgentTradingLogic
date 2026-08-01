"""Tests for the target-book handoff.

Two things are being guarded here, and only one of them is ordinary code
correctness.

The first is arithmetic: deltas, turnover, staleness.

The second is the ARMING RULE. `live.emit` is the single place in this
repository where research output becomes something an unattended process is
allowed to act on. Every test below that asserts on `orders_permitted` exists
so that permission cannot be granted by accident — by a typo in a status
string, by a new tier name, by a refactor that defaults the field to True. A
regression there does not raise; it trades.

The panel is not loaded. `_targets` is patched out in every test that needs
weights, because these tests are about the contract, not the construction, and
a test that takes ninety seconds to load a price panel is a test nobody runs.
"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from rhengine.alpha import live


def _book(n=30):
    """A well-formed book: n names, fully invested, nothing above the cap."""
    return {f"S{i:03d}": 1.0 / n for i in range(n)}


def _fake_targets(last_bar="2026-07-31", formed="2026-07-31",
                  weights=None, cap=0.10):
    """Stand in for the real construction: a panel calendar and one book."""
    weights = _book() if weights is None else weights
    cal = pd.bdate_range(end=pd.Timestamp(last_bar), periods=30)
    panel = SimpleNamespace(
        adj=pd.DataFrame(index=cal, columns=list(weights)))
    targets = {pd.Timestamp(formed): pd.Series(weights)}
    return panel, targets, cap


class DeltaMath(unittest.TestCase):
    def test_exit_appears_as_a_full_negative_delta(self):
        """A name held but not targeted must appear, or the book never sells."""
        target = pd.Series({"AAA": 0.5, "BBB": 0.5})
        d = live._deltas(target, {"AAA": 0.4, "ZZZ": 0.2})
        self.assertAlmostEqual(d["ZZZ"], -0.2)
        self.assertIn("BBB", d)

    def test_unchanged_name_is_omitted(self):
        target = pd.Series({"AAA": 0.5})
        self.assertEqual(live._deltas(target, {"AAA": 0.5}), {})

    def test_turnover_is_half_the_absolute_delta_sum(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", positions=_book(), asof="2026-07-31")
        self.assertEqual(p["one_way_turnover"], 0.0)

        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", positions={"CCC": 1.0}, asof="2026-07-31")
        # sell 100% of CCC, buy the whole book -> 200% two-way, 100% one-way.
        # places=4 because deltas are published rounded to 6dp and 30 of them
        # accumulate ~5e-6; that precision is the payload's, not an error.
        self.assertAlmostEqual(p["one_way_turnover"], 1.0, places=4)


class Staleness(unittest.TestCase):
    def test_stale_panel_is_refused_not_warned(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets("2026-07-24")):
            with self.assertRaises(live.StaleBook) as cm:
                live.emit("rmxb", asof="2026-08-01")
        self.assertIn("2026-07-24", str(cm.exception))

    def test_fresh_panel_passes(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets("2026-07-31")):
            p = live.emit("rmxb", asof="2026-08-01")
        self.assertEqual(p["panel_age_days"], 1)

    def test_boundary_is_inclusive(self):
        """Exactly at the limit passes; one day past does not."""
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets("2026-07-27")):
            live.emit("rmxb", asof="2026-08-01")          # 5 days, allowed
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets("2026-07-26")):
            with self.assertRaises(live.StaleBook):
                live.emit("rmxb", asof="2026-08-01")      # 6 days


class PanelCacheFreshness(unittest.TestCase):
    """The refresh-is-invisible trap: bars update, load_panel() serves the old
    pickle, and the operator concludes the guard is broken."""

    def test_refuses_when_bars_are_newer_than_the_cache(self):
        with mock.patch.object(live.os.path, "exists", return_value=True), \
             mock.patch.object(live.os.path, "getmtime", return_value=100.0), \
             mock.patch.object(live.os, "listdir", return_value=["A.csv.gz"]):
            # cache at 100, bar at 100 -> equal, allowed
            live.check_panel_cache()

        def _mtime(p):
            return 100.0 if p.endswith(".pkl") else 200.0

        with mock.patch.object(live.os.path, "exists", return_value=True), \
             mock.patch.object(live.os.path, "getmtime", side_effect=_mtime), \
             mock.patch.object(live.os, "listdir", return_value=["A.csv.gz"]):
            with self.assertRaises(live.StaleBook) as cm:
                live.check_panel_cache()
        self.assertIn("refresh=True", str(cm.exception))

    def test_no_cache_is_not_an_error(self):
        with mock.patch.object(live.os.path, "exists", return_value=False):
            live.check_panel_cache()


class ArmingRule(unittest.TestCase):
    """The permission boundary. A failure here is a live-trading incident."""

    def _emit(self, status):
        spec = json.loads(live._SPEC.read_text(encoding="utf-8"))
        spec["books"]["rmxb"]["status"] = status
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            return live.emit("rmxb", asof="2026-07-31", spec=spec)

    def test_manual_is_not_executable(self):
        p = self._emit("manual")
        self.assertFalse(p["orders_permitted"])
        self.assertEqual(p["mode"], "dry_run")

    def test_user_configurable_confers_no_agent_permission(self):
        p = self._emit("user_configurable")
        self.assertFalse(p["orders_permitted"])

    def test_retired_is_not_executable(self):
        self.assertFalse(self._emit("retired")["orders_permitted"])

    def test_only_the_exact_string_autonomous_arms_it(self):
        for near_miss in ("Autonomous", "AUTONOMOUS", "autonomous ", "auto",
                          "", "true"):
            with self.subTest(status=near_miss):
                self.assertFalse(self._emit(near_miss)["orders_permitted"])
        self.assertTrue(self._emit("autonomous")["orders_permitted"])

    def test_arming_forces_the_override_into_the_payload(self):
        """Trading against 0-of-10 gates must be stated where it is logged."""
        p = self._emit("autonomous")
        self.assertTrue(any("G1" in o for o in p["overrides"]))
        self.assertTrue(any("HOLDOUT" in o for o in p["overrides"]))

    def test_shipped_default_is_not_armed(self):
        """Whatever is committed right now must not trade unattended."""
        self.assertNotEqual(live.book_status("rmxb"), "autonomous")

    def test_missing_status_defaults_to_manual(self):
        spec = json.loads(live._SPEC.read_text(encoding="utf-8"))
        del spec["books"]["rmxb"]["status"]
        self.assertEqual(live.book_status("rmxb", spec), "manual")


class PayloadContract(unittest.TestCase):
    def test_no_order_planning_fields_leak_in(self):
        """CLAUDE.md: engine-only, no order planning. Guard the boundary."""
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", positions={"CCC": 1.0}, asof="2026-07-31")
        blob = json.dumps(p).lower()
        for banned in ("shares", "limit_price", "order_id", "quantity",
                       "notional", "qty"):
            with self.subTest(field=banned):
                self.assertNotIn(f'"{banned}"', blob)

    def test_constraints_come_from_the_real_config(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", asof="2026-07-31")
        c = p["constraints"]
        self.assertEqual(c["cost_bps_round_trip"], 30.0)
        self.assertEqual(c["order_type"], "limit")
        self.assertTrue(c["long_only"])
        self.assertFalse(c["leverage_permitted"])

    def test_executor_is_told_to_recheck_spreads_itself(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", asof="2026-07-31")
        self.assertTrue(any("spread_gate" in m for m in p["executor_must"]))

    def test_execution_lag_rule_is_present_and_names_the_lookahead(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", asof="2026-07-31")
        self.assertIn("after decided_on", p["execution_rule"])

    def test_weights_pass_through_unchanged(self):
        """emit() must not be able to change what the strategy holds."""
        w = _book(40)
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets(weights=w)):
            p = live.emit("rmxb", asof="2026-07-31")
        for k, v in w.items():
            self.assertAlmostEqual(p["weights"][k], round(v, 6))

    def test_config_hash_is_stable_and_short(self):
        h = live.config_hash()
        self.assertEqual(len(h), 16)
        self.assertEqual(h, live.config_hash())


class SymbolMismatch(unittest.TestCase):
    """The wash-trade bug: BRK.B held, BRK-B targeted, both sides full size."""

    def test_case_and_whitespace_only_without_a_universe(self):
        """With no `known` set there is nothing to resolve against, so it must
        not guess a direction — guessing is what broke this the first time."""
        self.assertEqual(live.normalize_symbol(" brk.b "), "BRK.B")
        self.assertEqual(live.normalize_symbol("aapl"), "AAPL")

    def test_resolves_toward_whichever_spelling_the_panel_uses(self):
        self.assertEqual(live.normalize_symbol("BRK-B", {"BRK.B"}), "BRK.B")
        self.assertEqual(live.normalize_symbol("BRK.B", {"BRK-B"}), "BRK-B")
        # an exact hit is never rewritten
        self.assertEqual(live.normalize_symbol("BRK.B", {"BRK.B", "BRK-B"}),
                         "BRK.B")

    def test_unresolvable_symbol_is_left_alone_for_the_flag_to_catch(self):
        self.assertEqual(live.normalize_symbol("ZZZQ", {"AAPL"}), "ZZZQ")

    def test_a_held_class_share_does_not_become_a_wash_trade(self):
        w = {k: 1.0 / 30 for k in list(_book(29)) + ["BRK.B"]}
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets(weights=w)):
            p = live.emit("rmxb", positions={"BRK-B": 1.0 / 30},
                          asof="2026-07-31")
        self.assertNotIn("BRK.B", p["deltas"])     # matched, no wash trade
        self.assertNotIn("BRK-B", p["deltas"])
        self.assertIn("BRK-B -> BRK.B", p["position_notes"])

    def test_both_notations_supplied_are_merged_not_double_counted(self):
        pos, notes = live.clean_positions({"BRK.B": 0.02, "BRK-B": 0.03},
                                          known={"BRK.B"})
        self.assertAlmostEqual(pos["BRK.B"], 0.05)
        self.assertTrue(any("merged" in n for n in notes))


class PanelNamingConvention(unittest.TestCase):
    """Pins the fact the first version of normalize_symbol got wrong.

    `panel.py` reads `yahoo_long/BRK-B.csv.gz` but stores the series under the
    membership ticker `BRK.B`. If that ever flips, every class-share position
    silently becomes a wash trade, so assert it against the real panel rather
    than trusting the comment.
    """

    def test_panel_columns_use_the_dotted_membership_ticker(self):
        try:
            from rhengine.alpha.rmx import load_panel
            cols = set(load_panel().adj.columns)
        except Exception as e:                      # no cached panel on CI
            self.skipTest(f"panel unavailable: {e}")
        if not any("." in c or "-" in c for c in cols):
            self.skipTest("no class shares in this panel")
        self.assertIn("BRK.B", cols)
        self.assertNotIn("BRK-B", cols)


class PositionValidation(unittest.TestCase):
    def test_percentages_are_rejected_with_the_fix_in_the_message(self):
        with self.assertRaises(live.BadPositions) as cm:
            live.clean_positions({"AAPL": 5.0, "MSFT": 4.0})
        self.assertIn("FRACTIONS", str(cm.exception))

    def test_a_book_totalling_one_hundred_is_rejected(self):
        with self.assertRaises(live.BadPositions) as cm:
            live.clean_positions({f"S{i}": 1.0 for i in range(100)})
        self.assertIn("percentages", str(cm.exception))

    def test_shorts_are_refused_not_netted(self):
        with self.assertRaises(live.BadPositions) as cm:
            live.clean_positions({"AAPL": 0.1, "TSLA": -0.05})
        self.assertIn("long-only", str(cm.exception))

    def test_null_weight_from_a_broker_api_is_a_clear_error(self):
        with self.assertRaises(live.BadPositions) as cm:
            live.clean_positions({"AAPL": None})
        self.assertIn("non-numeric", str(cm.exception))

    def test_nan_is_rejected(self):
        with self.assertRaises(live.BadPositions):
            live.clean_positions({"AAPL": float("nan")})

    def test_a_normal_book_passes_and_cash_is_reported(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", positions={"AAPL": 0.4}, asof="2026-07-31")
        self.assertAlmostEqual(p["cash_weight"], 0.6)

    def test_unmapped_holding_is_flagged_before_it_is_sold(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", positions={"WEIRD": 0.1}, asof="2026-07-31")
        self.assertIn("WEIRD", p["unknown_to_panel"])


class DegenerateBookGuard(unittest.TestCase):
    def test_a_book_that_lost_a_leg_is_refused_not_renormalised(self):
        half = {k: v / 2 for k, v in _book(30).items()}   # sums to 0.5
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets(weights=half)):
            with self.assertRaises(live.DegenerateBook) as cm:
                live.emit("rmxb", asof="2026-07-31")
        self.assertIn("NOT renormalising", str(cm.exception))

    def test_a_book_with_too_few_names_is_refused(self):
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets(weights=_book(3))):
            with self.assertRaises(live.DegenerateBook):
                live.emit("rmxb", asof="2026-07-31")


class ConsoleSafety(unittest.TestCase):
    def test_payload_strings_are_ascii(self):
        """cp1252 is the default console encoding on this box; non-ASCII in a
        printed or logged string comes out as replacement characters."""
        with mock.patch.object(live, "_targets",
                               return_value=_fake_targets()):
            p = live.emit("rmxb", asof="2026-07-31")
        for field in ("execution_rule", "overrides", "executor_must"):
            blob = json.dumps(p[field])
            with self.subTest(field=field):
                blob.encode("ascii")   # raises if a stray em dash returns


class SpecLookup(unittest.TestCase):
    def test_unknown_book_raises(self):
        with self.assertRaises(live.UnknownBook):
            live.book_spec("nope")

    def test_comment_keys_are_not_books(self):
        with self.assertRaises(live.UnknownBook):
            live.book_spec("_note")

    def test_both_shipped_books_declare_kill_switches(self):
        for name in ("rmxb", "rmxc"):
            with self.subTest(book=name):
                ks = live.book_spec(name)["kill_switches"]
                self.assertIn("flat_immediately_if", ks)
                self.assertEqual(ks["max_staleness_days"],
                                 live.MAX_STALENESS_DAYS)

    def test_both_shipped_books_state_their_evidence_bound(self):
        """A book may not ship a headline without the boundary next to it."""
        for name in ("rmxb", "rmxc"):
            with self.subTest(book=name):
                self.assertIn("_bound", live.book_spec(name)["evidence"])


if __name__ == "__main__":
    unittest.main()
