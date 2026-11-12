"""LRS-2x book: the signal, the vehicle choice, staleness, and the arming rule."""

import json
import subprocess
import sys
import unittest

import numpy as np
import pandas as pd

from rhengine.alpha import live, lrs


def _closes(last_move: float, n: int = 260) -> pd.Series:
    idx = pd.bdate_range(end="2026-11-10", periods=n)
    s = pd.Series(np.linspace(100, 120, n), index=idx)
    s.iloc[-1] = s.iloc[-201:-1].mean() * (1 + last_move)
    return s


def _spec(status: str) -> dict:
    spec = json.loads(live._SPEC.read_text(encoding="utf-8"))
    spec["books"]["lrs2x"]["status"] = status
    return spec


class LRS(unittest.TestCase):
    def test_signal(self):
        self.assertTrue(lrs.signal(_closes(+0.05)))
        self.assertFalse(lrs.signal(_closes(-0.05)))
        self.assertIsNone(lrs.signal(_closes(0.0, n=150)))

    def test_vehicle_follows_signal(self):
        on = lrs.emit_lrs(_closes(+0.05), asof="2026-11-11", spec=_spec("manual"))
        off = lrs.emit_lrs(_closes(-0.05), asof="2026-11-11", spec=_spec("manual"))
        self.assertEqual(on["weights"], {"SSO": 1.0})
        self.assertEqual(off["weights"], {"SGOV": 1.0})

    def test_flip_sells_the_old_fund(self):
        p = lrs.emit_lrs(_closes(-0.05), positions={"SSO": 0.98}, asof="2026-11-11",
                         spec=_spec("manual"))
        self.assertEqual(p["deltas"], {"SGOV": 1.0, "SSO": -0.98})

    def test_live_price_decides_today(self):
        adj = _closes(0.0).iloc[:-1]                     # history ends 2026-11-09
        sma = adj.iloc[-199:].mean()
        on = lrs.emit_lrs(adj, asof="2026-11-10", spec=_spec("manual"), live_price=sma * 1.05)
        off = lrs.emit_lrs(adj, asof="2026-11-10", spec=_spec("manual"), live_price=sma * 0.95)
        self.assertEqual((on["weights"], on["decided_on"]), ({"SSO": 1.0}, "2026-11-10"))
        self.assertEqual(off["weights"], {"SGOV": 1.0})
        self.assertIn("15:15", on["execution_rule"])
        with self.assertRaises(ValueError):              # today's bar already present
            lrs.emit_lrs(_closes(0.0), asof="2026-11-10", spec=_spec("manual"), live_price=100)

    def test_stdlib_path_needs_no_pandas(self):
        # RobinhoodAgent's runtime Python has no pandas; its planner imports this.
        code = ("import sys; sys.modules['pandas'] = None\n"
                "from rhengine.alpha import lrs\n"
                "assert lrs.book()['vehicles'] == {'risk_on': 'SSO', 'risk_off': 'SGOV'}\n"
                "assert lrs.decide([1.0] * 199, 2.0) is True\n"
                "assert lrs.decide([1.0] * 199, 0.5) is False\n"
                "assert lrs.decide([1.0] * 198, 2.0) is None\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=str(live._SPEC.parent.parent))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_decide_matches_signal(self):
        for move in (+0.05, -0.05, +0.001, -0.001):
            s = _closes(move)
            self.assertEqual(lrs.decide(s.tolist()), lrs.signal(s))

    def test_stale_refuses(self):
        with self.assertRaises(live.StaleBook):
            lrs.emit_lrs(_closes(+0.05), asof="2026-11-30", spec=_spec("manual"))

    def test_shipped_default_is_not_armed(self):
        self.assertEqual(live.book_status("lrs2x"), "manual")

    def test_only_the_exact_string_autonomous_arms_it(self):
        for s in ("manual", "user_configurable", "retired", "Autonomous",
                  "autonomous ", "true", ""):
            with self.subTest(status=s):
                p = lrs.emit_lrs(_closes(+0.05), asof="2026-11-11", spec=_spec(s))
                self.assertFalse(p["orders_permitted"])
                self.assertEqual(p["overrides"], [])
        p = lrs.emit_lrs(_closes(+0.05), asof="2026-11-11", spec=_spec("autonomous"))
        self.assertTrue(p["orders_permitted"])
        self.assertTrue(p["overrides"][0].startswith("ARMED"))


if __name__ == "__main__":
    unittest.main()
