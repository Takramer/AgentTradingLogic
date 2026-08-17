"""test_options_registry.py — the ways a forward accumulator loses data.

An accumulator fails silently. There is no crash when a day is skipped, a
duplicate is double-counted, or a bad quote is stored as if it were good — the
damage only appears months later when the sample is analysed and is by then
unrecoverable, because the discovery window has closed. Each test below is one
of those silent failures.

* `test_ingest_is_idempotent` — a retried cron must not double-count.
* `test_rejects_unusable_quotes` — a NULL IV or crossed book stored as data
  would drag every later VRP average toward nothing.
* `test_orphan_quotes_are_dropped_not_guessed` — a quote with no instrument
  metadata cannot be matched to an underlying path, ever.
* `test_registry_survives_a_torn_line` — an interrupted append must cost one
  line, not the file.
* `test_atomic_contract_write` — the UUID registry is THE artifact that expires;
  a truncating write loses the ability to reconstruct the sample at all.
* `test_strike_ladder_is_unconditional` — a signal-selected sample cannot answer
  an unconditional question.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.options_registry import (  # noqa: E402
    STRIKE_STEP, coverage, due_for_vrp, ingest, load_quotes, normalise,
    plan_delta_strikes, plan_expirations, plan_strikes, validate,
)


def _capture(captured_at="2026-07-31T20:15:00Z", iv="0.0880", bid="3.40",
             ask="3.48", iid="abc-123"):
    return {
        "captured_at": captured_at, "underlying": "SPY", "spot": 746.81,
        "instruments": [{"id": iid, "chain_symbol": "SPY",
                         "expiration_date": "2026-08-03",
                         "strike_price": "745.0000", "type": "call"}],
        "quotes": [{"instrument_id": iid, "bid_price": bid, "ask_price": ask,
                    "mark_price": "3.44", "implied_volatility": iv,
                    "delta": "0.639246", "gamma": "0.065185",
                    "theta": "-0.434071", "vega": "0.244351", "rho": "0.036200",
                    "open_interest": 3357, "volume": 61234}],
    }


class TestPlanning(unittest.TestCase):
    def test_strike_ladder_is_unconditional(self):
        """Symmetric, mechanical, on the grid — never signal-selected."""
        s = plan_strikes(746.81, n_each_side=2, step=5.0)
        self.assertEqual(s, [735.0, 740.0, 745.0, 750.0, 755.0])
        self.assertEqual(len(s), 5)
        # symmetric about ATM
        atm = s[len(s) // 2]
        self.assertAlmostEqual(atm - s[0], s[-1] - atm, places=9)

    def test_strike_ladder_rejects_nonsense(self):
        for bad in ((0.0, 2, 5.0), (700.0, 2, 0.0), (700.0, -1, 5.0)):
            with self.assertRaises(ValueError):
                plan_strikes(bad[0], bad[1], bad[2])

    def test_expirations_span_tenors_and_skip_the_past(self):
        avail = ["2026-07-20", "2026-08-07", "2026-08-31", "2026-09-30",
                 "2026-12-18"]
        got = plan_expirations(avail, "2026-07-31", targets=(7, 30, 60))
        self.assertNotIn("2026-07-20", got)      # already expired
        self.assertEqual(len(set(got)), len(got))  # no duplicates
        self.assertIn("2026-08-07", got)
        self.assertEqual(plan_expirations([], "2026-07-31"), [])


class TestValidation(unittest.TestCase):
    def test_rejects_unusable_quotes(self):
        rows, _ = normalise(_capture())
        self.assertEqual(validate(rows[0]), [])
        for kwargs, expect in (({"iv": ""}, "missing implied_volatility"),
                               ({"bid": "3.60", "ask": "3.40"}, "crossed quote"),
                               ({"iv": "9.9"}, "implausible IV")):
            r, _ = normalise(_capture(**kwargs))
            self.assertTrue(any(expect in p for p in validate(r[0])),
                            f"{kwargs} -> {validate(r[0])}")

    def test_orphan_quotes_are_dropped_not_guessed(self):
        """No instrument metadata means no strike and no expiry — the row could
        never be matched to an underlying path, so storing it is worse than
        dropping it."""
        cap = _capture()
        cap["quotes"].append(dict(cap["quotes"][0], instrument_id="unknown-id"))
        rows, orphans = normalise(cap)
        self.assertEqual(len(rows), 1)
        self.assertEqual(orphans, ["unknown-id"])


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.c = os.path.join(self.dir, "contracts.json")
        self.q = os.path.join(self.dir, "quotes.jsonl")

    def test_ingest_is_idempotent(self):
        """A retried cron must add nothing the second time."""
        first = ingest(_capture(), self.c, self.q)
        self.assertEqual(first["stored"], 1)
        again = ingest(_capture(), self.c, self.q)
        self.assertEqual(again["stored"], 0)
        self.assertEqual(again["duplicates"], 1)
        self.assertEqual(len(load_quotes(self.q)), 1)
        self.assertEqual(json.load(open(self.c))["abc-123"]["captures"], 1)

    def test_second_day_appends_and_bumps_last_seen(self):
        ingest(_capture(captured_at="2026-07-31T20:15:00Z"), self.c, self.q)
        ingest(_capture(captured_at="2026-08-01T20:15:00Z"), self.c, self.q)
        self.assertEqual(len(load_quotes(self.q)), 2)
        e = json.load(open(self.c))["abc-123"]
        self.assertEqual(e["captures"], 2)
        self.assertEqual(e["first_seen"], "2026-07-31T20:15:00Z")
        self.assertEqual(e["last_seen"], "2026-08-01T20:15:00Z")

    def test_bad_rows_never_reach_the_store(self):
        res = ingest(_capture(iv=""), self.c, self.q)
        self.assertEqual(res["stored"], 0)
        self.assertEqual(len(res["rejected"]), 1)
        self.assertEqual(load_quotes(self.q), [])
        self.assertFalse(os.path.exists(self.c))

    def test_registry_survives_a_torn_line(self):
        """An interrupted append costs one line, not the file — the reason the
        quote store is JSONL rather than a single JSON array."""
        ingest(_capture(), self.c, self.q)
        with open(self.q, "a", encoding="utf-8") as f:
            f.write('{"instrument_id": "trunc", "captured')   # power cut
        self.assertEqual(len(load_quotes(self.q)), 1)
        res = ingest(_capture(captured_at="2026-08-01T20:15:00Z"), self.c, self.q)
        self.assertEqual(res["stored"], 1)

    def test_atomic_contract_write(self):
        """The UUID registry is the artifact that expires. It must never be left
        truncated, and no .tmp may survive a successful write."""
        ingest(_capture(), self.c, self.q)
        self.assertFalse(os.path.exists(self.c + ".tmp"))
        json.load(open(self.c))          # parses = not truncated

    def test_coverage_and_due(self):
        ingest(_capture(), self.c, self.q)
        cov = coverage(self.c, self.q)
        self.assertEqual(cov["contracts"], 1)
        self.assertEqual(cov["days"], ["2026-07-31"])
        # not yet expired
        self.assertEqual(due_for_vrp("2026-08-01", self.c), [])
        # expired: now measurable
        due = due_for_vrp("2026-08-10", self.c)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["instrument_id"], "abc-123")


class TestPlanCoversWhatIsTraded(unittest.TestCase):
    """The 2026-08-01 planning change, and the silent gaps it closed.

    None of these failures would ever raise. Each one produces a registry that
    looks healthy for months and then cannot answer the question it was built
    for, by which time the discovery window has shut.
    """

    def test_zero_dte_is_in_the_default_tenors(self):
        """Four of the seven sources in docs/OPTIONS-SOURCES-2026-08-01.md are
        about 0DTE. The old default (7, 30, 60) captured no same-day contract,
        so that question was unanswerable no matter how long the job ran."""
        exps = ["2026-08-03", "2026-08-10", "2026-09-02", "2026-10-02"]
        picked = plan_expirations(exps, "2026-08-03")
        self.assertIn("2026-08-03", picked)

    def test_today_is_kept_not_filtered_as_past(self):
        """A >= 0 boundary error here would drop 0DTE on every single run."""
        self.assertEqual(plan_expirations(["2026-08-03"], "2026-08-03"),
                         ["2026-08-03"])

    def test_strike_grid_matches_the_listed_grid(self):
        """A $5 ladder on a $1-grid name asks for strikes that do not exist;
        the request does not error, it just returns fewer contracts."""
        self.assertEqual(STRIKE_STEP["SPY"], 1.0)
        self.assertEqual(STRIKE_STEP["SPX"], 5.0)
        self.assertEqual(STRIKE_STEP["XSP"], 1.0)

    def test_delta_bands_reach_where_the_dollar_ladder_cannot(self):
        """At 60 DTE the +/-$4 SPY ladder sits inside ~0.35-0.65 delta. Every
        premium-selling source in the corpus sells 0.10-0.15. Before this
        change those contracts were never captured at any tenor."""
        ladder = set(plan_strikes(746.81, 4, 1.0))
        bands = set(plan_delta_strikes(746.81, 0.16, 60, (0.10, 0.15), 1.0))
        self.assertTrue(bands)
        self.assertFalse(ladder & bands)

    def test_delta_bands_are_symmetric_in_type(self):
        """Calls and puts both, so the sample can still answer an unconditional
        question. Asking only for puts would make it a skew sample."""
        ks = plan_delta_strikes(746.81, 0.16, 30, (0.10,), 1.0)
        self.assertEqual(len(ks), 2)
        self.assertLess(ks[0], 746.81)
        self.assertGreater(ks[1], 746.81)

    def test_unknown_iv_falls_back_rather_than_guessing(self):
        """An invented IV would put the delta bands at invented strikes, and
        the registry would record them as though they had been chosen."""
        self.assertEqual(plan_delta_strikes(746.81, None, 30), [])
        self.assertEqual(plan_delta_strikes(746.81, 0.0, 30), [])
        self.assertEqual(plan_delta_strikes(746.81, 0.16, 0), [])

    def test_strike_ladder_is_still_unconditional(self):
        """The delta bands must not have made the ladder signal-dependent: the
        same spot still yields the same strikes, with no other input."""
        self.assertEqual(plan_strikes(746.81, 4, 1.0),
                         plan_strikes(746.81, 4, 1.0))


if __name__ == "__main__":
    unittest.main()
