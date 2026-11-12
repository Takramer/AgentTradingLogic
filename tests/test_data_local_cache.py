"""Raw and adjusted Yahoo bars must not share a cache file (bug fixed 2026-11-12)."""

import tempfile
import unittest
from unittest import mock

from rhengine import data_local


class CacheKey(unittest.TestCase):
    def test_raw_read_never_returns_adjusted_bars(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(data_local, "CACHE_DIR", d):
            data_local._cache_save("SPY", 0, [{"close": 160.74}], adjust=True)
            self.assertIsNone(data_local._cache_load("SPY", 0, adjust=False))
            data_local._cache_save("SPY", 0, [{"close": 196.16}], adjust=False)
            self.assertEqual(data_local._cache_load("SPY", 0, adjust=True)[0]["close"], 160.74)
            self.assertEqual(data_local._cache_load("SPY", 0, adjust=False)[0]["close"], 196.16)


if __name__ == "__main__":
    unittest.main()
