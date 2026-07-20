"""GateBook annotation/filter tests — pure data, no network, no disk."""

import unittest

from rhengine.gates import GateBook, DAY_MS

T0 = 1_700_000_000_000


def _macro_series(curve=0.5, oas=3.0, vix=18.0, t=T0):
    return {
        "T10Y2Y": [{"time": t, "value": curve}],
        "BAMLH0A0HYM2": [{"time": t, "value": oas}],
        "VIXCLS": [{"time": t, "value": vix}],
    }


def _sue_events(surprise_pct=8.0, t=T0):
    return {"AAA": [{"time": t, "fiscal_date": "x", "eps_actual": 1.0,
                     "eps_estimate": 0.9, "surprise_pct": surprise_pct}]}


class TestGateBook(unittest.TestCase):
    def test_sue_annotation_pass_fail_na(self):
        gb = GateBook(sue_events=_sue_events(8.0), sue_cfg={"enabled": True})
        tr = {}
        gb.annotate(tr, "AAA", T0 + 3 * DAY_MS)  # 3d after report, in window
        self.assertEqual(tr["sue"], "pass")
        self.assertEqual(tr["sue_pct"], 8.0)

        tr = {}
        gb.annotate(tr, "AAA", T0 + 30 * DAY_MS)  # outside drift window
        self.assertEqual(tr["sue"], "fail")

        tr = {}
        gb.annotate(tr, "ZZZ", T0 + 3 * DAY_MS)  # no cached earnings
        self.assertEqual(tr["sue"], "na")

    def test_sue_negative_surprise_fails_long_only(self):
        gb = GateBook(sue_events=_sue_events(-12.0), sue_cfg={"enabled": True})
        tr = {}
        gb.annotate(tr, "AAA", T0 + 3 * DAY_MS)
        self.assertEqual(tr["sue"], "fail")  # bear report can't confirm a long

    def test_macro_annotation_and_staleness(self):
        gb = GateBook(macro_series=_macro_series(), macro_cfg={"enabled": True})
        tr = {}
        gb.annotate(tr, "AAA", T0 + DAY_MS)
        self.assertEqual(tr["macro_risk_on"], 1)

        gb2 = GateBook(macro_series=_macro_series(vix=31.0),
                       macro_cfg={"enabled": True})
        tr = {}
        gb2.annotate(tr, "AAA", T0 + DAY_MS)
        self.assertEqual(tr["macro_risk_on"], 0)

        tr = {}
        gb.annotate(tr, "AAA", T0 + 20 * DAY_MS)  # >14d stale = unknown
        self.assertEqual(tr["macro_risk_on"], "")

    def test_macro_incomplete_series_is_unknown(self):
        series = _macro_series()
        del series["VIXCLS"]
        gb = GateBook(macro_series=series, macro_cfg={"enabled": True})
        tr = {}
        gb.annotate(tr, "AAA", T0 + DAY_MS)
        self.assertEqual(tr["macro_risk_on"], "")

    def test_filter_mode(self):
        gb = GateBook(sue_events=_sue_events(8.0),
                      sue_cfg={"enabled": True, "mode": "filter"},
                      macro_series=_macro_series(),
                      macro_cfg={"enabled": True, "mode": "filter"})
        self.assertTrue(gb.keep({"sue": "pass", "macro_risk_on": 1}))
        self.assertFalse(gb.keep({"sue": "fail", "macro_risk_on": 1}))
        self.assertFalse(gb.keep({"sue": "na", "macro_risk_on": 1}))
        self.assertFalse(gb.keep({"sue": "pass", "macro_risk_on": ""}))

    def test_annotate_mode_keeps_everything(self):
        gb = GateBook(sue_events=_sue_events(8.0), sue_cfg={"enabled": True})
        self.assertTrue(gb.keep({"sue": "na"}))

    def test_from_config_disabled_returns_none(self):
        self.assertIsNone(GateBook.from_config({}, ["AAA"], verbose=False))


if __name__ == "__main__":
    unittest.main()
