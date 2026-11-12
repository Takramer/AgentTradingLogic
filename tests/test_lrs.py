"""LRS books: the signal, the vehicle choice, staleness, the arming rule, and
LRS-VT's exposure, split and band (what RobinhoodAgent's planner calls)."""

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
                "assert lrs.decide([1.0] * 198, 2.0) is None\n"
                "b = lrs.book('lrs_vt')\n"
                "assert b['vehicles'] == {'index': 'SPY', 'levered': 'SSO', 'cash': 'SGOV'}\n"
                "xs = [100 * 1.001 ** i for i in range(260)]\n"
                "assert lrs.vt_exposure(xs, None, b) is not None\n"
                "assert lrs.needs_trade(0.0, 1.2, 0.25)\n")
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


VEH = {"index": "SPY", "levered": "SSO", "cash": "SGOV"}


def _vt_book(**kw) -> dict:
    b = lrs.book("lrs_vt")
    b["signal"] = {**b["signal"], **kw}
    return b


class LRSVT(unittest.TestCase):
    def test_realized_vol_matches_pandas(self):
        rng = np.random.default_rng(7)
        s = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.012, 300)))
        want = s.pct_change().rolling(21).std().iloc[-1] * np.sqrt(252)
        self.assertAlmostEqual(lrs.realized_vol(s.tolist()), want, places=12)
        self.assertIsNone(lrs.realized_vol([1.0] * 21))

    def test_exposure_off_capped_and_scaled(self):
        rng = np.random.default_rng(3)
        calm = list(100 * np.cumprod(1 + 0.001 + rng.normal(0, 0.002, 260)))   # vol ~3%
        wild = list(100 * np.cumprod(1 + 0.001 + rng.normal(0, 0.03, 260)))    # vol ~37%
        b = lrs.book("lrs_vt")
        self.assertEqual(lrs.vt_exposure(calm, b=b), 2.0)                      # capped
        self.assertTrue(lrs.decide(wild))                                       # seed 3: trend on
        e = lrs.vt_exposure(wild, b=b)
        self.assertAlmostEqual(e, 0.30 / lrs.realized_vol(wild), places=12)
        self.assertLess(e, 1.0)
        sma = sum(calm[-199:]) / 199
        self.assertEqual(lrs.vt_exposure(calm, live_price=sma * 0.5, b=b), 0.0)  # trend off
        self.assertIsNone(lrs.vt_exposure(calm[:150], b=b))
        self.assertEqual(lrs.vt_exposure(calm, b=_vt_book(max_exposure=1.5)), 1.5)

    def test_vol_ignores_the_live_price(self):
        # the tested 15:15 rule: trend on the live price, vol through yesterday's close
        xs = [100 * 1.001 ** i for i in range(260)]
        xs[-5] *= 1.01
        a = lrs.vt_exposure(xs, live_price=xs[-1] * 1.0001)
        b = lrs.vt_exposure(xs, live_price=xs[-1] * 1.03)
        self.assertEqual(a, b)

    def test_split(self):
        for e, want in ((0.0, (0, 0, 1)), (0.5, (0.5, 0, 0.5)), (1.0, (1, 0, 0)),
                        (1.36, (0.64, 0.36, 0)), (2.0, (0, 1, 0))):
            w = lrs.split(e, VEH)
            for k, x in zip(("SPY", "SSO", "SGOV"), want):
                self.assertAlmostEqual(w[k], x, places=12, msg=f"e={e} {k}")
            self.assertAlmostEqual(lrs.held_exposure(w, 1.0, VEH), e, places=12)

    def test_held_exposure(self):
        self.assertAlmostEqual(lrs.held_exposure({"SPY": 600, "SSO": 400}, 1000, VEH), 1.4)
        self.assertEqual(lrs.held_exposure({"SGOV": 1000}, 1000, VEH), 0.0)
        self.assertEqual(lrs.held_exposure({}, 0, VEH), 0.0)

    def test_needs_trade(self):
        self.assertTrue(lrs.needs_trade(1.2, 0.0, 0.25))     # entry
        self.assertTrue(lrs.needs_trade(0.0, 1.9, 0.25))     # exit
        self.assertTrue(lrs.needs_trade(0.0, 0.05, 0.25))    # exit a small remainder
        self.assertFalse(lrs.needs_trade(0.0, 0.01, 0.25))   # dust counts as flat
        self.assertFalse(lrs.needs_trade(1.4, 1.2, 0.25))    # inside the band
        self.assertTrue(lrs.needs_trade(1.6, 1.3, 0.25))     # outside it
        self.assertTrue(lrs.needs_trade(0.9, 1.2, 0.25))

    def test_shipped_default_is_not_armed(self):
        self.assertEqual(live.book_status("lrs_vt"), "manual")
        s = lrs.book("lrs_vt")["signal"]
        self.assertEqual((s["sma_sessions"], s["vol_sessions"], s["vol_target"],
                          s["max_exposure"], s["band"]), (200, 21, 0.30, 2.0, 0.25))


if __name__ == "__main__":
    unittest.main()
