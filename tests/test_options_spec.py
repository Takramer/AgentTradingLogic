"""test_options_spec.py — the ways a source corpus turns into a fake strategy.

`options_spec.py` holds the only structured record of seven retail sources. The
danger it carries is not a crash: it is that a table of plausible numbers with
no evidence behind it gets read, later and by something with less context, as a
specification. Each test below pins one guard against that reading.

* `test_status_is_blocked` — the module must never import as permission.
* `test_every_param_names_its_sources` — an unattributed number is the exact
  object this file exists to prevent.
* `test_conflicts_are_not_silently_resolved` — where sources disagree the
  disagreement is the finding; averaging it away would manufacture consensus.
* `test_wheel_is_impossible_on_cash_settled` — the corpus' three videos are
  wheel videos and the two instruments asked about are cash settled. That is
  arithmetic, not judgement, and it must not drift.
* `test_sizing_rule_refuses_to_cover_the_wheel` — returning a plausible number
  where the rule does not apply would be an invention (CONFLICT OC-3).
* `test_strike_for_delta_inverts` — the delta bands feed the accumulator; if
  this is wrong the sample is quietly mis-targeted for months.
* `test_weekend_charges_three_days_for_one` — the trap docs/VIDEO-INTAKE
  flagged as the one an unattended agent walks straight into.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.options_spec import (  # noqa: E402
    BLOCKERS, CONFLICTS, EXECUTION_LIMITS, INSTRUMENTS, LATE_CLOSE_SURVEY,
    PARAMS, SOURCES, STATUS, atm_premium_estimate, collateral, decay_days,
    feasibility, min_account_for, session_close, strike_for_delta,
    wheel_collateral,
)


class TestStatus(unittest.TestCase):
    def test_status_is_blocked(self):
        """Nothing in this repo may read the spec as a green light. If this
        test is ever changed, the four BLOCKERS must have been cleared with
        evidence, not with a decision."""
        self.assertEqual(STATUS, "blocked")
        self.assertGreaterEqual(len(BLOCKERS), 4)

    def test_module_exposes_no_entry_rule(self):
        """Same discipline as market_data_options / options_costs / registry: a
        module that only measures cannot produce a false positive."""
        import rhengine.options_spec as spec
        banned = ("signal", "entry", "should_trade", "trade", "order")
        for name in dir(spec):
            if name.startswith("_"):
                continue
            self.assertFalse(
                any(b in name.lower() for b in banned),
                f"{name} looks like a trading decision, which this module "
                f"must never contain")


class TestProvenance(unittest.TestCase):
    def test_every_param_names_its_sources(self):
        for p in PARAMS:
            self.assertTrue(p["sources"], f"{p['name']} has no source")
            for s in p["sources"]:
                self.assertIn(s, SOURCES, f"{p['name']} cites unknown {s}")

    def test_no_parameter_is_attributed_to_a_video(self):
        """The three videos' transcripts are not fetchable, so nothing was read
        out of them. Attributing a number to one would be fabrication —
        docs/VIDEO-INTAKE-2026-07-31.md made the same call for the same reason."""
        videos = {k for k, v in SOURCES.items() if v["kind"] == "video"}
        for p in PARAMS:
            self.assertFalse(
                videos & set(p["sources"]),
                f"{p['name']} claims a video as a source; transcripts were "
                f"never readable")

    def test_conflicts_are_not_silently_resolved(self):
        """Each conflict states what it bears on HERE. A conflict recorded
        without that is a note; with it, it is a decision the repo has made."""
        self.assertGreaterEqual(len(CONFLICTS), 3)
        for c in CONFLICTS:
            self.assertTrue(c["detail"] and c["bearing"])

    def test_single_source_params_are_visible_as_such(self):
        """The whole point of counting sources: `vix_floor` rests on one author,
        and this repo has already rejected a VIX gate as an in-sample mirage."""
        vix = next(p for p in PARAMS if p["name"] == "vix_floor")
        self.assertEqual(len(vix["sources"]), 1)
        self.assertIn("REJECTED", vix["note"].upper())


class TestCapital(unittest.TestCase):
    def test_wheel_is_impossible_on_cash_settled(self):
        for sym in ("SPX", "XSP", "NDX"):
            self.assertIsNone(wheel_collateral(sym),
                              f"{sym} settles cash; there is nothing to assign")
        for sym in ("SPY", "QQQ"):
            self.assertIsNotNone(wheel_collateral(sym))

    def test_wheel_collateral_is_a_hundred_shares(self):
        self.assertAlmostEqual(wheel_collateral("SPY"),
                               INSTRUMENTS["SPY"]["level"] * 100, places=2)

    def test_sizing_rule_refuses_to_cover_the_wheel(self):
        """CONFLICT OC-3. MX and TA wrote 1-2% for defined-risk day trades.
        Applied to a cash-secured put it implies a ~$3.7M account, which nobody
        means — so the function returns None rather than that number."""
        self.assertIsNone(min_account_for("cash_secured_put", 74_431))
        self.assertIsNone(min_account_for("covered_call", 74_431))
        self.assertAlmostEqual(min_account_for("credit_spread", 210, 0.02),
                               10_500)

    def test_collateral_separates_buying_power_from_worst_case(self):
        """A cash-secured put holds the full strike but cannot lose all of it;
        a long call holds and can lose exactly the premium. Conflating the two
        is how a small account gets surprised."""
        col, loss = collateral("cash_secured_put", strike=745.0, credit=5.0)
        self.assertEqual(col, 74_500)
        self.assertEqual(loss, 74_000)
        col, loss = collateral("long_call", premium=3.01)
        self.assertEqual(col, loss)

    def test_small_account_can_hold_xsp_but_not_spx(self):
        """The concrete answer to 'maybe some SPX and QQQ 0DTEs': at 0DTE ATM,
        SPX is ~10x too big and XSP is the same exposure at 1/10 the ticket."""
        rows = {(r["symbol"], r["structure"]): r
                for r in feasibility(2000, iv=0.16, dte_days=1.0)}
        self.assertFalse(rows[("SPX", "long ATM (0DTE)")]["ok"])
        self.assertTrue(rows[("XSP", "long ATM (0DTE)")]["ok"])
        self.assertTrue(rows[("QQQ", "long ATM (0DTE)")]["ok"])

    def test_premium_estimate_is_only_an_estimate(self):
        """Pinned loosely on purpose. If this ever becomes precise enough to
        price a trade it will be used to price a trade, which it must not be."""
        p = atm_premium_estimate(746.81, 0.16, 1.0)
        self.assertTrue(2.0 < p < 5.0, p)


class TestStrikeInversion(unittest.TestCase):
    def test_strike_for_delta_inverts(self):
        """Round-trip against the forward Black-Scholes delta. If this drifts,
        the accumulator captures the wrong contracts and nothing errors."""
        from statistics import NormalDist
        s, iv, t_days, r = 746.81, 0.16, 30.0, 0.04
        for target in (0.10, 0.15, 0.30, 0.50):
            k = strike_for_delta(s, iv, t_days, target, "call", r=r)
            t = t_days / 365.0
            d1 = ((math.log(s / k) + (r + iv * iv / 2) * t)
                  / (iv * math.sqrt(t)))
            self.assertAlmostEqual(NormalDist().cdf(d1), target, places=6)

    def test_low_delta_calls_are_above_spot_and_puts_below(self):
        s = 746.81
        c = strike_for_delta(s, 0.16, 30, 0.10, "call")
        p = strike_for_delta(s, 0.16, 30, 0.10, "put")
        self.assertGreater(c, s)
        self.assertLess(p, s)

    def test_delta_bands_miss_the_dollar_ladder_at_long_tenor(self):
        """The gap this whole change exists to close. A +/-$4 SPY ladder and the
        60-DTE 0.10-delta strikes are DISJOINT — so before 2026-08-01 no short
        strike any premium-selling source trades was ever captured, and waiting
        longer would not have produced one."""
        from rhengine.options_registry import plan_strikes
        ladder = set(plan_strikes(746.81, 4, 1.0))
        bands = {strike_for_delta(746.81, 0.16, 60, d, ty, step=1.0)
                 for d in (0.10, 0.15) for ty in ("call", "put")}
        self.assertFalse(ladder & bands)

    def test_expired_contract_has_no_delta_to_invert(self):
        self.assertIsNone(strike_for_delta(746.81, 0.16, 0, 0.10, "call"))

    def test_rejects_a_delta_outside_zero_one(self):
        for bad in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                strike_for_delta(746.81, 0.16, 30, bad, "call")


class TestSessionClose(unittest.TestCase):
    """Surveyed 2026-08-01. The late close is real but the rule is not the
    obvious one, and an agent that infers it will be wrong on live money."""

    def test_late_close_is_not_a_size_or_index_rule(self):
        """XLF and XLE (sector SPDRs) have the 16:15 close; TQQQ and GDX do
        not, despite tracking indices and trading heavily. Any code that
        derives the flag from size, issuer or index-tracking is wrong."""
        self.assertTrue(LATE_CLOSE_SURVEY["XLF"][0])
        self.assertTrue(LATE_CLOSE_SURVEY["XLE"][0])
        self.assertFalse(LATE_CLOSE_SURVEY["TQQQ"][0])
        self.assertFalse(LATE_CLOSE_SURVEY["GDX"][0])

    def test_unsurveyed_symbol_raises_rather_than_guessing(self):
        with self.assertRaises(KeyError):
            session_close("NVDA")

    def test_index_chains_have_no_forced_sellout(self):
        """The structural argument for XSP over SPY in a small account: the
        equity chains force-close 30 min before expiry when the account cannot
        cover assignment, and an account that cannot post $74.7k never can."""
        for sym in ("SPY", "QQQ", "IWM"):
            self.assertEqual(LATE_CLOSE_SURVEY[sym][2], 1800)
            self.assertIsNotNone(session_close(sym)["forced_exit"])
        for sym in ("SPX", "XSP"):
            self.assertEqual(LATE_CLOSE_SURVEY[sym][2], 0)
            self.assertIsNone(session_close(sym)["forced_exit"])

    def test_multileg_is_recorded_as_unexecutable(self):
        """The corpus' entire premium-selling method is multi-leg, and this
        interface is single-leg only. Losing that fact would let a spread
        strategy be designed against an interface that cannot place it."""
        self.assertTrue(any("SINGLE-LEG ONLY" in e for e in EXECUTION_LIMITS))


class TestWeekendTrap(unittest.TestCase):
    def test_weekend_charges_three_days_for_one(self):
        """2026-07-31 is a Friday. Holding to Monday pays 3 calendar days of
        theta for 1 trading day of movement — the measured effect on the 3-DTE
        745C was a breakeven move of 0.11% -> 0.29%."""
        cal, trading, ratio = decay_days("2026-07-31", "2026-08-03")
        self.assertEqual((cal, trading), (3, 1))
        self.assertAlmostEqual(ratio, 3.0)

    def test_intraweek_hold_is_clean(self):
        cal, trading, ratio = decay_days("2026-08-03", "2026-08-04")
        self.assertEqual((cal, trading), (1, 1))
        self.assertAlmostEqual(ratio, 1.0)

    def test_same_day_hold_pays_no_overnight_decay(self):
        """0DTE's one structural advantage, and the reason the corpus prefers
        it: the position never crosses a calendar boundary."""
        self.assertEqual(decay_days("2026-08-03", "2026-08-03"), (0, 1, 0.0))

    def test_exit_before_entry_is_an_error_not_a_negative(self):
        with self.assertRaises(ValueError):
            decay_days("2026-08-03", "2026-07-31")


if __name__ == "__main__":
    unittest.main()
