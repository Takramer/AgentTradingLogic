"""Golden-vector drift guard: the engine must reproduce fixtures/golden.json
byte-for-byte (as JSON values). If this fails, either the engine drifted or a
deliberate change needs `python3 gen_fixtures.py` + a reviewed fixture diff."""

import json
import os
import unittest

from rhengine.indicators import (
    score_ticker, classify_spy_regime, estimate_vix_regime, spec_defaults,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRATEGIES = ["vwap", "bollinger", "macd", "consolidation", "residualmomentum", "pead"]


class TestGoldenVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "fixtures", "bars.json")) as f:
            cls.cases = json.load(f)
        with open(os.path.join(ROOT, "fixtures", "golden.json")) as f:
            cls.golden = json.load(f)["cases"]

    def test_vectors(self):
        spy = self.cases["spy_uptrend"]
        for name, bars in self.cases.items():
            exp = self.golden[name]
            self.assertEqual(classify_spy_regime(bars), exp["regime"], name)
            self.assertEqual(estimate_vix_regime(bars), exp["vix_regime"], name)
            for strat in STRATEGIES:
                got = score_ticker("TEST", bars, strat, spec_defaults(strat), spy_bars=spy)
                # JSON round-trip normalizes tuples/None the same way the fixture was stored
                self.assertEqual(json.loads(json.dumps(got)), exp["strategies"][strat],
                                 f"{name}/{strat}")

    def test_spec_loads(self):
        from rhengine.indicators import load_spec
        spec = load_spec()
        self.assertEqual(set(spec["strategies"]), set(STRATEGIES))
        for strat in STRATEGIES:
            self.assertTrue(spec_defaults(strat))


if __name__ == "__main__":
    unittest.main()
