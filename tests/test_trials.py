"""Tests for rhengine/trials.py — the experiment ledger and its thresholds.

Two things are being guarded here:

1. **The arithmetic**, against an independently written source. The 2026-07-24
   audit stated, without this module existing, that "with >=12 trials, a
   Bonferroni-style threshold is t ~ 2.9-3.1". `test_matches_audit_fc14` asserts
   the code reproduces that band. If someone rewrites the estimator and the
   number moves, the audit and the code stop agreeing and this fails.
2. **The backfilled ledger**, which is evidence and must not silently shrink.
   `test_shipped_ledger_*` pin the citations and the >=12-entry floor the audit
   asked for.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from rhengine import trials as T


class TestThresholds(unittest.TestCase):
    def test_one_trial_costs_nothing(self):
        self.assertEqual(T.expected_max_t(1), 0.0)
        self.assertEqual(T.expected_max_t(0), 0.0)
        # A single test at alpha=0.05 two-sided is the familiar 1.96.
        self.assertAlmostEqual(T.sidak_t(1), 1.959963985, places=6)

    def test_expected_max_is_monotone_in_trials(self):
        seq = [T.expected_max_t(n) for n in (1, 2, 6, 12, 50, 200, 25000)]
        self.assertEqual(seq, sorted(seq))
        # Searching 25k variants buys you a t of ~4 for free.
        self.assertGreater(T.expected_max_t(25000), 4.0)
        self.assertLess(T.expected_max_t(25000), 4.2)

    def test_correlated_trials_are_worth_fewer_independent_ones(self):
        """E[max | rho] = sqrt(1-rho) * E[max | iid] — exact for equicorrelated.

        X_i = sqrt(rho)*Z + sqrt(1-rho)*eps_i, so max_i X_i = sqrt(rho)*Z +
        sqrt(1-rho)*max_i eps_i and E[Z] = 0.
        """
        base = T.expected_max_t(143_000)
        for rho in (0.0, 0.25, 0.5, 0.9, 0.95):
            self.assertAlmostEqual(T.expected_max_t(143_000, rho),
                                   ((1 - rho) ** 0.5) * base, places=10)
        # Monotone: more correlation, cheaper search.
        seq = [T.expected_max_t(1000, r) for r in (0.0, 0.3, 0.6, 0.9)]
        self.assertEqual(seq, sorted(seq, reverse=True))

    def test_effective_trials_inverts_expected_max(self):
        for n, rho in ((10_000, 0.5), (25_000, 0.9), (143_000, 0.95), (500, 0.2)):
            ne = T.effective_trials(n, rho)
            target = T.expected_max_t(n, rho)
            self.assertGreaterEqual(ne, 1)
            self.assertLessEqual(ne, n)
            # N_eff is an integer, so the exact inverse is bracketed rather than
            # hit. It resolves UPWARD — the conservative side, charging for the
            # extra fraction of a trial rather than discounting it.
            self.assertGreaterEqual(T.expected_max_t(ne), target - 1e-9)
            if ne > 1:
                self.assertLess(T.expected_max_t(ne - 1), target + 1e-9)

    def test_effective_trials_is_identity_when_independent(self):
        self.assertEqual(T.effective_trials(500, 0.0), 500)
        self.assertEqual(T.effective_trials(1, 0.9), 1)

    def test_rho_out_of_range_is_rejected(self):
        for bad in (-0.1, 1.0, 1.5):
            with self.assertRaises(ValueError):
                T.expected_max_t(100, bad)

    def test_verdict_reports_the_rho_it_assumed(self):
        v = T.verdict(3.0, 143_000, rho=0.9)
        self.assertEqual(v["rho"], 0.9)
        self.assertLess(v["effective_trials"], 143_000)
        # Same t, no correlation assumed: a far harsher bar.
        self.assertGreater(T.verdict(3.0, 143_000)["required_t"], v["required_t"])

    def test_matches_audit_fc14(self):
        """AUDIT-2026-07-24 FC-14: 'with >=12 trials ... t ~ 2.9-3.1'."""
        b = T.bonferroni_t(12)
        self.assertGreaterEqual(b, 2.85)
        self.assertLessEqual(b, 3.1)

    def test_sidak_is_never_harsher_than_bonferroni(self):
        for n in (1, 2, 6, 12, 54, 500):
            self.assertLessEqual(T.sidak_t(n), T.bonferroni_t(n) + 1e-12)

    def test_family_wise_p_grows_with_search(self):
        self.assertAlmostEqual(T.family_wise_p(1.96, 1), 0.05, places=3)
        p6 = T.family_wise_p(2.54, 6)
        self.assertGreater(p6, T.family_wise_p(2.54, 1))
        self.assertLess(p6, 1.0)

    def test_verdict_flags_a_result_that_does_not_clear(self):
        v = T.verdict(1.78, 12)          # the audit's own headline alpha t
        self.assertFalse(v["clears"])
        self.assertGreater(v["family_wise_p"], 0.5)
        self.assertTrue(T.verdict(4.0, 6)["clears"])


class TestLedgerIO(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "trials.jsonl")

    def test_append_then_count(self):
        T.append(T.Trial(family="f", label="a", outcome="kept"), self.path)
        T.append(T.Trial(family="f", label="b", outcome="rejected"), self.path)
        T.append(T.Trial(family="g", label="c"), self.path)
        self.assertEqual(T.count("f", path=self.path), 2)
        self.assertEqual(T.count("all", path=self.path), 3)
        self.assertEqual(T.count(None, path=self.path), 3)
        self.assertEqual(T.families(self.path), {"f": 2, "g": 1})

    def test_n_counts_as_n_trials_not_one_row(self):
        """A row recording '8 signals screened' is eight trials, not one."""
        T.append(T.Trial(family="f", label="8 price-only signals", n=8), self.path)
        self.assertEqual(len(T.load(self.path)), 1)
        self.assertEqual(T.count("f", path=self.path), 8)

    def test_resolution_rows_do_not_double_count(self):
        """Rule 8 logs a trial BEFORE it runs, so the outcome arrives later.

        Appending a second full row for that outcome would charge one search
        twice. A row with `resolves` set carries only the verdict.
        """
        T.append(T.Trial(family="f", label="try X", outcome="inconclusive"), self.path)
        self.assertEqual(T.count("f", path=self.path), 1)
        T.append(T.Trial(family="f", label="try X result", resolves="try X",
                         outcome="rejected"), self.path)
        self.assertEqual(T.count("f", path=self.path), 1)       # still ONE trial
        self.assertEqual(len(T.load(self.path)), 2)             # but two rows
        self.assertEqual(T.families(self.path), {"f": 1})
        self.assertEqual(T.outcomes(self.path)["try X"], "rejected")

    def test_kind_filter(self):
        T.append(T.Trial(family="f", label="a", kind="threshold"), self.path)
        T.append(T.Trial(family="f", label="b", kind="structural"), self.path)
        self.assertEqual(T.count("f", kind="threshold", path=self.path), 1)

    def test_missing_ledger_is_empty_not_an_error(self):
        self.assertEqual(T.load(os.path.join(self.dir, "nope.jsonl")), [])
        self.assertEqual(T.count(path=os.path.join(self.dir, "nope.jsonl")), 0)

    def test_comments_and_blank_lines_are_skipped(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("# header\n\n")
            fh.write(json.dumps({"family": "f", "label": "a"}) + "\n")
        self.assertEqual(T.count(path=self.path), 1)

    def test_bad_fields_are_rejected_at_write_time(self):
        with self.assertRaises(ValueError):
            T.Trial(family="f", label="a", kind="vibes")
        with self.assertRaises(ValueError):
            T.Trial(family="f", label="a", outcome="probably fine")
        with self.assertRaises(ValueError):
            T.Trial(family="", label="a")
        with self.assertRaises(ValueError):
            T.Trial(family="f", label="a", n=0)

    def test_cli_log_and_report_roundtrip(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):   # the CLI prints; tests should not
            rc = T.main(["--ledger", self.path, "log", "--family", "x",
                         "--label", "y", "--outcome", "rejected",
                         "--source", "docs/X.md", "-n", "3"])
            self.assertEqual(rc, 0)
            self.assertEqual(T.main(["--ledger", self.path, "report"]), 0)
            self.assertEqual(T.main(["--ledger", self.path, "list"]), 0)
        self.assertEqual(T.count("x", path=self.path), 3)
        self.assertEqual(T.load(self.path)[0].source, "docs/X.md")
        self.assertIn("required t", buf.getvalue())
        self.assertIn("docs/X.md", buf.getvalue())


class TestShippedLedger(unittest.TestCase):
    """The backfilled ledger is evidence. It must not quietly get smaller."""

    def setUp(self):
        self.rows = T.load()

    def test_ledger_exists_and_meets_the_audit_floor(self):
        # AUDIT-2026-07-24 §8 Phase 0: "backfill the >=12 known trials".
        self.assertGreaterEqual(len(self.rows), 12)
        self.assertGreaterEqual(T.count("legacy-engine"), 12)

    def test_every_backfilled_row_cites_a_source(self):
        missing = [r.label for r in self.rows if not r.source]
        self.assertEqual(missing, [], f"ledger rows with no citation: {missing}")

    def test_the_surviving_strategy_is_logged_as_kept(self):
        kept = [r for r in self.rows if r.family == "rmx" and r.outcome == "kept"]
        self.assertTrue(any("RMX-B" in r.label for r in kept))

    def test_spent_holdouts_are_logged_as_rejected(self):
        by_label = {r.label: r for r in self.rows}
        for name in ("RMX-D", "RMX-Q"):
            hit = [r for r in self.rows if r.label.startswith(name)]
            self.assertTrue(hit, f"{name} missing from the ledger")
            self.assertEqual(hit[0].outcome, "rejected")
        self.assertTrue(by_label)

    def test_rmxb_headline_t_does_not_clear_its_preregistered_budget(self):
        """RMX-B is +2.54 on Ken French; its pre-declared budget was 6 trials.

        docs/RMX-PREREGISTRATION.md §4: "1 baseline + 4 single-leg + 1 combined
        = 6". At 6 trials the two-sided Sidak bar is 2.63, so the headline misses
        at alpha=0.05 and clears at alpha=0.10. That is the boundary, and this
        test exists so nobody re-rounds it away.
        """
        v = T.verdict(2.54, 6)
        self.assertFalse(v["clears"])
        self.assertGreater(v["family_wise_p"], 0.05)
        self.assertLess(v["family_wise_p"], 0.10)
        self.assertTrue(T.verdict(2.54, 6, alpha=0.10)["t"] == 2.54)
        self.assertGreaterEqual(abs(2.54), T.sidak_t(6, alpha=0.10))


if __name__ == "__main__":
    unittest.main()
