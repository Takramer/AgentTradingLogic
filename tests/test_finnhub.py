"""Finnhub earnings adapter tests — pure parse/merge, no network."""

import unittest

from rhengine import market_data_finnhub as fh
from rhengine.market_data_av import sue_gate

DAY_MS = 86_400_000


def _rows():
    return [
        {"symbol": "AAA", "date": "2026-06-01", "epsActual": 1.10,
         "epsEstimate": 1.00, "quarter": 2, "year": 2026},   # +10% beat
        {"symbol": "AAA", "date": "2026-03-01", "epsActual": 0.80,
         "epsEstimate": 1.00, "quarter": 1, "year": 2026},   # -20% miss
        {"symbol": "BBB", "date": "2026-06-02", "epsActual": 2.00,
         "epsEstimate": 2.00, "quarter": 2, "year": 2026},   # 0% (kept, sp=0)
        {"symbol": "CCC", "date": "2026-06-03", "epsActual": None,
         "epsEstimate": 0.50, "quarter": 2, "year": 2026},   # future → dropped
        {"symbol": "DDD", "date": "2026-06-04", "epsActual": 1.0,
         "epsEstimate": 0.0, "quarter": 2, "year": 2026},    # est 0 → sp None → dropped
    ]


class TestParse(unittest.TestCase):
    def test_parse_calendar_rows(self):
        parsed = fh.parse_calendar_rows(_rows())
        self.assertEqual(set(parsed), {"AAA", "BBB"})       # CCC/DDD dropped
        self.assertEqual(len(parsed["AAA"]), 2)
        beat = [e for e in parsed["AAA"] if e["fiscal_date"] == "2026-06-01"][0]
        self.assertAlmostEqual(beat["surprise_pct"], 10.0, places=3)
        miss = [e for e in parsed["AAA"] if e["fiscal_date"] == "2026-03-01"][0]
        self.assertAlmostEqual(miss["surprise_pct"], -20.0, places=3)

    def test_surprise_pct_guards_zero_estimate(self):
        self.assertIsNone(fh._surprise_pct(1.0, 0))
        self.assertIsNone(fh._surprise_pct(None, 1.0))
        self.assertAlmostEqual(fh._surprise_pct(1.1, 1.0), 10.0, places=3)


class TestMerge(unittest.TestCase):
    def test_merge_dedups_and_sorts(self):
        store = {"symbols": {}}
        added1 = fh.merge_events(store, fh.parse_calendar_rows(_rows()))
        self.assertEqual(added1, 3)  # AAA x2 + BBB
        # re-merging the same window adds nothing (dedup by report date)
        added2 = fh.merge_events(store, fh.parse_calendar_rows(_rows()))
        self.assertEqual(added2, 0)
        aaa = store["symbols"]["AAA"]
        self.assertEqual([e["fiscal_date"] for e in aaa],
                         ["2026-03-01", "2026-06-01"])  # ascending

    def test_merge_appends_new_window(self):
        store = {"symbols": {}}
        fh.merge_events(store, fh.parse_calendar_rows(_rows()))
        newer = [{"symbol": "AAA", "date": "2026-09-01", "epsActual": 1.5,
                  "epsEstimate": 1.2, "quarter": 3, "year": 2026}]
        added = fh.merge_events(store, fh.parse_calendar_rows(newer))
        self.assertEqual(added, 1)
        self.assertEqual(len(store["symbols"]["AAA"]), 3)


class TestGateInteropShape(unittest.TestCase):
    def test_events_feed_sue_gate(self):
        # Finnhub events must be consumable by the shared av.sue_gate.
        store = {"symbols": {}}
        fh.merge_events(store, fh.parse_calendar_rows(_rows()))
        events = store["symbols"]["AAA"]
        now_ms = events[-1]["time"] + 5 * DAY_MS  # 5d after the +10% beat
        g = sue_gate(events, now_ms, drift_min=1, drift_max=10,
                     min_surprise_pct=5.0)
        self.assertTrue(g["pass"])
        self.assertEqual(g["direction"], "bull")


if __name__ == "__main__":
    unittest.main()
