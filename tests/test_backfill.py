"""Backfill runner + AV negative-cache tests — no network, no real disk."""

import gzip
import json
import os
import tempfile
import time
import unittest

from rhengine import backfill
from rhengine import market_data_av as av


class _TmpCwd:
    """Run a test body in a throwaway cwd with an av_cache/ dir."""
    def __enter__(self):
        self._old = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        os.makedirs("av_cache")
        return self

    def __exit__(self, *a):
        os.chdir(self._old)
        self._tmp.cleanup()

    def write_cache(self, sym, blob):
        with gzip.open(os.path.join("av_cache", sym.upper() + ".json.gz"),
                       "wt", encoding="utf-8") as f:
            json.dump(blob, f)


class TestCoverage(unittest.TestCase):
    def test_split_fresh_stale_missing_and_negative_cache(self):
        with _TmpCwd() as tc:
            now = time.time()
            tc.write_cache("FRESH", {"fetched_ts": now, "events": [{"x": 1}]})
            tc.write_cache("STALE", {"fetched_ts": now - 40 * 86400, "events": []})
            tc.write_cache("ETF", {"fetched_ts": now - 999 * 86400,
                                   "no_earnings": True, "events": []})
            fresh, stale, missing = backfill.coverage(
                ["FRESH", "STALE", "ETF", "GONE"], ttl_days=7)
            self.assertEqual(fresh, ["FRESH", "ETF"])  # ETF negative-cache = fresh
            self.assertEqual(stale, ["STALE"])
            self.assertEqual(missing, ["GONE"])


class TestRankAndCsv(unittest.TestCase):
    def test_rank_by_liquidity_stable_tail(self):
        dvol = {"A": 10.0, "B": 100.0, "C": 50.0}
        ranked = backfill.rank_by_liquidity(["A", "B", "C", "Z", "Y"], dvol=dvol)
        self.assertEqual(ranked[:3], ["B", "C", "A"])   # liquid, desc
        self.assertEqual(ranked[3:], ["Z", "Y"])        # unknown, original order

    def test_tickers_from_csv_strategy_filter(self):
        with _TmpCwd():
            with open("t.csv", "w", newline="") as f:
                f.write("ticker,strategy\nAAA,pead\nBBB,macd\nAAA,pead\nCCC,pead\n")
            self.assertEqual(backfill.tickers_from_csvs(["t.csv"], strategy="pead"),
                             ["AAA", "CCC"])
            self.assertEqual(backfill.tickers_from_csvs(["t.csv"]),
                             ["AAA", "BBB", "CCC"])


class TestRunnerBudget(unittest.TestCase):
    def test_run_once_stops_on_throttle_and_respects_budget(self):
        with _TmpCwd():
            calls = []

            def fake_fetch(sym, ttl_days=7):
                calls.append(sym)
                if sym == "T2":            # second name throttles
                    raise av.RateLimited("throttled")
                # persist a fresh entry so coverage() sees progress
                av._cache_write(sym, {"fetched_ts": time.time(), "events": []})

            orig_fetch = backfill.fetch_earnings
            orig_pace = backfill._PACE_SECONDS
            backfill.fetch_earnings = fake_fetch
            backfill._PACE_SECONDS = 0  # no real sleeping in the test
            try:
                state = backfill.run_once(["T1", "T2", "T3", "T4"],
                                          max_requests=10, verbose=False)
            finally:
                backfill.fetch_earnings = orig_fetch
                backfill._PACE_SECONDS = orig_pace
            # T1 ok, T2 throttles -> stop before T3/T4
            self.assertEqual(calls, ["T1", "T2"])
            self.assertEqual(state["cached_fresh"], 1)
            self.assertEqual(state["remaining"], 3)
            self.assertTrue(os.path.exists(os.path.join("av_cache",
                                                        "_backfill_state.json")))

    def test_run_once_caps_at_max_requests(self):
        with _TmpCwd():
            def fake_fetch(sym, ttl_days=7):
                av._cache_write(sym, {"fetched_ts": time.time(), "events": []})

            orig_fetch, orig_pace = backfill.fetch_earnings, backfill._PACE_SECONDS
            backfill.fetch_earnings = fake_fetch
            backfill._PACE_SECONDS = 0
            try:
                state = backfill.run_once(["A", "B", "C", "D", "E"],
                                          max_requests=2, verbose=False)
            finally:
                backfill.fetch_earnings, backfill._PACE_SECONDS = orig_fetch, orig_pace
            self.assertEqual(state["cached_fresh"], 2)
            self.assertEqual(state["remaining"], 3)


class TestAvNegativeCache(unittest.TestCase):
    """Patch av.requests / av._av_key in place and restore — never reload the
    module, or av.RateLimited's class identity would drift from the one
    backfill.py bound at import (breaking its `except RateLimited`)."""

    def setUp(self):
        self._real_req, self._real_key = av.requests, av._av_key
        av._av_key = lambda: "k"

    def tearDown(self):
        av.requests, av._av_key = self._real_req, self._real_key

    def test_empty_body_negative_caches(self):
        with _TmpCwd():
            av.requests = _FakeRequests({})       # ETF: clean empty body
            self.assertEqual(av.fetch_earnings("SPY"), [])
            self.assertTrue(av._cache_read("SPY")["no_earnings"])
            # second call must NOT hit network (would raise if it did)
            av.requests = _FakeRequests(_boom=True)
            self.assertEqual(av.fetch_earnings("SPY"), [])

    def test_information_notice_raises_ratelimited(self):
        with _TmpCwd():
            av.requests = _FakeRequests({"Information": "slow down"})
            with self.assertRaises(av.RateLimited):
                av.fetch_earnings("QQQ")
            self.assertIsNone(av._cache_read("QQQ"))  # not cached


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _FakeRequests:
    RequestException = Exception

    def __init__(self, body=None, _boom=False):
        self._body = body
        self._boom = _boom

    def get(self, *a, **k):
        if self._boom:
            raise AssertionError("network hit when cache should have served")
        return _FakeResp(self._body)


if __name__ == "__main__":
    unittest.main()
