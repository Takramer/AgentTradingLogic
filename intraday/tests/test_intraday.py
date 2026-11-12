"""Tests for intraday/ — the day-trading backtest harness.

Each test pins one rule a day-trading backtest most often breaks: fills on the
signal bar, holding overnight, optimistic stop/target ordering, dropping idle
days, signals that read the future, uncharged parameter sweeps, repeat holdout
looks, fake PnL at futures rolls, and naive timestamps. The last test keeps
order-placing code out of the package.
"""
from __future__ import annotations

import glob
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from rhengine import trials as T
from intraday import data, engine, instruments as I, strategies as St
from intraday import paper, validate as V

TZ = data.TZ


def _bars(rows, day="2024-03-04"):
    idx = pd.date_range(f"{day} 09:30", periods=len(rows), freq="min", tz=TZ)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    return df.assign(volume=100.0)


class Fixed(engine.Strategy):
    name = "fixed"

    def __init__(self, target, stop=None, take=None):
        super().__init__(target=list(target), stop=stop, take=take)

    def signals(self, bars):
        n = len(bars)
        out = pd.DataFrame({"target": self.params["target"][:n]}, index=bars.index,
                           dtype=float)
        if self.params["stop"] is not None:
            out["stop"] = self.params["stop"]
        if self.params["take"] is not None:
            out["take"] = self.params["take"]
        return out


class RandomDaily(engine.Strategy):
    """One random 30-bar trade per session, seeded by the date: no edge, and
    causal by construction."""
    name = "random_daily"

    def __init__(self, seed=0):
        super().__init__(seed=seed)

    def signals(self, bars):
        day = data.session_dates(bars.index)
        k = bars.groupby(day).cumcount().to_numpy()
        target = np.zeros(len(bars))
        for d in pd.unique(day):
            rng = np.random.default_rng(self.params["seed"] + d.toordinal())
            start, side = int(rng.integers(5, 300)), float(rng.choice([-1, 1]))
            target[(day == d) & (k >= start) & (k < start + 30)] = side
        return pd.DataFrame({"target": target}, index=bars.index)


class PeekAhead(engine.Strategy):
    """Knows where price is 30 bars from now. Makes money; must never clear."""
    name = "peek"

    def signals(self, bars):
        fwd = bars["close"].shift(-30) - bars["close"]
        return pd.DataFrame({"target": np.sign(fwd).fillna(0.0)}, index=bars.index)


class DayHighLeak(engine.Strategy):
    """The classic intraday leak: the session's high, known only at the close."""
    name = "day_high_leak"

    def signals(self, bars):
        day = data.session_dates(bars.index)
        hi = bars["high"].groupby(day).transform("max")
        return pd.DataFrame({"target": (bars["close"] < hi - 5).astype(float)},
                            index=bars.index)


class TestCosts(unittest.TestCase):
    def test_mes_round_trip(self):
        self.assertAlmostEqual(I.MES.round_trip(1), 4.28)
        self.assertAlmostEqual(I.MES.round_trip(2), 8.56)
        self.assertAlmostEqual(I.MES.stressed(2.0).round_trip(1), 6.78)

    def test_spy_robinhood_costs(self):
        # 100 shares, 500 -> 501: 2 x 1c slippage + 2 x $0.0001 TAF per share,
        # plus SEC 20.60/M split across both legs' notional.
        c = I.SPY_RH.trade_cost(100, 500.0, 501.0)
        self.assertAlmostEqual(c, 100 * 0.0202 + 10.30e-6 * 100 * 1001.0)

    def test_notional_sizing_and_cash_account_cap(self):
        b = data.synthetic_bars(days=3, price=500.0)
        r = engine.backtest(St.SessionDrift(), b, I.SPY_RH, capital=10_000, notional=10_000)
        self.assertEqual(len(r.trades), 3)
        q = r.trades["qty"].to_numpy()
        self.assertTrue(abs(q[0] * r.trades["entry_px"].iat[0] - 10_000) < 1e-6)

        class Flip(engine.Strategy):          # wants a new position every 30 bars
            name = "flip"
            def signals(self, bars):
                k = np.arange(len(bars)) // 30
                return pd.DataFrame({"target": np.where(k % 2, 1.0, -1.0)}, index=bars.index)

        r = engine.backtest(Flip(), b, I.SPY_RH, capital=10_000, notional=10_000)
        self.assertEqual(r.trades.groupby("day").size().max(), 1)   # settled cash: once a day
        r = engine.backtest(Flip(), b, I.SPY_RH, capital=10_000, notional=5_000)
        self.assertEqual(r.trades.groupby("day").size().max(), 2)
        r = engine.backtest(Flip(), b, I.MES, capital=10_000)
        self.assertGreater(r.trades.groupby("day").size().max(), 2)  # futures: no cap

    def test_margin_refuses_oversized_position(self):
        b = data.synthetic_bars(days=2)
        with self.assertRaises(ValueError):
            engine.backtest(St.SessionDrift(), b, I.MES, qty=5, capital=10_000)
        engine.backtest(St.SessionDrift(), b, I.MES, qty=4, capital=10_000)


class TestEngine(unittest.TestCase):
    ROWS = [(100, 101, 99, 100), (101, 102, 100, 101), (102, 103, 101, 102),
            (103, 104, 102, 103), (104, 106, 103, 105)]

    def test_fills_at_next_open_and_closes_at_session_end(self):
        r = engine.backtest(Fixed([1] * 5), _bars(self.ROWS), I.MES)
        t = r.trades.iloc[0]
        self.assertEqual(len(r.trades), 1)
        self.assertEqual(t.entry_i, 1)                 # signal on bar 0, fill bar 1
        self.assertEqual(t.entry_px, 101)              # bar 1 OPEN, not bar 0 close
        self.assertEqual((t.reason, t.exit_px), ("eod", 105))
        self.assertAlmostEqual(t.net, (105 - 101) * 5 - 4.28)

    def test_flat_every_night(self):
        b = pd.concat([_bars(self.ROWS, "2024-03-04"), _bars(self.ROWS, "2024-03-05")])
        r = engine.backtest(Fixed([1] * 10), b, I.MES)
        self.assertEqual(len(r.trades), 2)
        self.assertTrue((r.trades.entry_ts.dt.date == r.trades.exit_ts.dt.date).all())
        self.assertEqual(len(r.daily), 2)

    def test_stop_assumed_first_when_bar_hits_both(self):
        rows = [(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 110, 90, 100),
                (100, 100, 100, 100)]
        r = engine.backtest(Fixed([1] * 4, stop=95.0, take=105.0), _bars(rows), I.MES)
        t = r.trades.iloc[0]
        self.assertEqual((t.reason, t.exit_px), ("stop", 95.0))

    def test_gap_through_stop_fills_at_open(self):
        rows = [(100, 100, 100, 100), (100, 100.5, 99.5, 100), (90, 91, 89, 90),
                (90, 90, 90, 90)]
        r = engine.backtest(Fixed([1] * 4, stop=95.0), _bars(rows), I.MES)
        self.assertEqual(r.trades.iloc[0].exit_px, 90)

    def test_no_reentry_until_target_changes(self):
        rows = [(100, 100, 100, 100), (100, 100, 94, 95), (95, 96, 94, 95),
                (95, 96, 94, 95), (95, 96, 94, 95)]
        r = engine.backtest(Fixed([1] * 5, stop=95.0), _bars(rows), I.MES)
        self.assertEqual(len(r.trades), 1)

    def test_daily_pnl_keeps_idle_sessions(self):
        b = pd.concat([_bars(self.ROWS, d) for d in ("2024-03-04", "2024-03-05", "2024-03-06")])
        tgt = [0] * 5 + [1] * 5 + [0] * 5
        r = engine.backtest(Fixed(tgt), b, I.MES)
        self.assertEqual(len(r.daily), 3)
        self.assertEqual((r.daily == 0).sum(), 2)


class TestLookAhead(unittest.TestCase):
    def setUp(self):
        self.bars = data.synthetic_bars(10, seed=1)

    def test_clean_strategies_pass(self):
        for s in (St.OpeningRangeBreakout(15, take_r=1.0), St.SessionDrift(), RandomDaily()):
            self.assertGreater(engine.causality_check(s, self.bars), 0)

    def test_shift_minus_one_is_caught(self):
        with self.assertRaises(engine.LookAheadError):
            engine.causality_check(PeekAhead(), self.bars)

    def test_session_high_leak_is_caught(self):
        with self.assertRaises(engine.LookAheadError):
            engine.causality_check(DayHighLeak(), self.bars)


class TestLedgerAndHoldout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.tmp.name, "trials.jsonl")
        self.looks = os.path.join(self.tmp.name, "holdout.jsonl")
        self.bars = data.synthetic_bars(30, seed=2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unlogged_trial_refused(self):
        with self.assertRaises(V.TrialNotLogged):
            V.require_logged("never logged", ledger=self.ledger)
        T.append(T.Trial(family=V.FAMILY, label="logged"), self.ledger)
        V.require_logged("logged", ledger=self.ledger)

    def test_sweep_charged_once_per_grid(self):
        T.append(T.Trial(family=V.FAMILY, label="orb"), self.ledger)
        grid = {"or_minutes": [5, 15, 30]}
        for _ in range(2):
            V.sweep(St.OpeningRangeBreakout, grid, self.bars, I.MES, trial="orb",
                    ledger=self.ledger)
        self.assertEqual(T.count(V.FAMILY, path=self.ledger), 3)
        V.sweep(St.OpeningRangeBreakout, {"or_minutes": [5, 15, 30, 60]}, self.bars,
                I.MES, trial="orb", ledger=self.ledger)
        self.assertEqual(T.count(V.FAMILY, path=self.ledger), 6)

    def test_second_holdout_look_is_not_clean(self):
        T.append(T.Trial(family=V.FAMILY, label="orb"), self.ledger)
        kw = dict(trial="orb", holdout_start="2023-02-01", ledger=self.ledger,
                  look_log=self.looks)
        first = V.holdout(St.OpeningRangeBreakout(), self.bars, I.MES, **kw)
        second = V.holdout(St.OpeningRangeBreakout(30), self.bars, I.MES, **kw)
        self.assertTrue(first["clean"])
        self.assertFalse(second["clean"])
        self.assertEqual(second["looks"], 2)

    def test_holdout_refuses_bars_that_end_before_it(self):
        T.append(T.Trial(family=V.FAMILY, label="orb"), self.ledger)
        with self.assertRaises(ValueError):
            V.holdout(St.OpeningRangeBreakout(), self.bars, I.MES, trial="orb",
                      holdout_start="2030-01-01", ledger=self.ledger, look_log=self.looks)
        self.assertFalse(os.path.exists(self.looks))      # no look spent

    def test_sweep_keeps_the_strategy_settings_it_does_not_vary(self):
        T.append(T.Trial(family=V.FAMILY, label="na"), self.ledger)
        base = St.NoiseArea(lookback=5, min_days=3, exit_rule="vwap").params
        sw = V.sweep(St.NoiseArea, {"lookback": [5]}, self.bars, I.MES, trial="na",
                     ledger=self.ledger, base=base)
        direct = engine.backtest(St.NoiseArea(lookback=5, min_days=3, exit_rule="vwap"),
                                 self.bars, I.MES)
        self.assertAlmostEqual(sw.table["net_usd"].iloc[0], direct.daily.sum(), places=6)
        with self.assertRaises(ValueError):          # wrappers cannot be rebuilt
            V.sweep(St.DayFilter, {"filter": ["x"]}, self.bars, I.MES, trial="na",
                    ledger=self.ledger,
                    base=St.DayFilter(St.SessionDrift(), pd.Series(dtype=bool), "x").params)

    def test_development_data_may_not_reach_the_holdout(self):
        with self.assertRaises(V.HoldoutBreach):
            V.guard_holdout(self.bars, "2023-02-01")
        V.guard_holdout(self.bars, "2030-01-01")


class TestVerdict(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.tmp.name, "trials.jsonl")
        self.bars = data.synthetic_bars(300, seed=4)

    def tearDown(self):
        self.tmp.cleanup()

    def _eval(self, strategy, label, **kw):
        T.append(T.Trial(family=V.FAMILY, label=label), self.ledger)
        return V.evaluate(strategy, self.bars, I.MES, trial=label,
                          survivorship="continuous_futures", ledger=self.ledger,
                          n_paths=50, **kw)

    def test_random_rule_on_random_walk_does_not_clear(self):
        rep = self._eval(RandomDaily(), "random")
        self.assertNotEqual(rep["verdict"], "CLEARS")

    def test_leaky_rule_fails_on_look_ahead_however_good_it_looks(self):
        rep = self._eval(PeekAhead(), "peek")
        status = {c["check"]: c["status"] for c in rep["checks"]}
        self.assertEqual(status["look-ahead"], "FAIL")
        self.assertEqual(rep["verdict"], "DOES NOT CLEAR")

    def test_static_symbol_list_fails_survivorship(self):
        T.append(T.Trial(family=V.FAMILY, label="drift"), self.ledger)
        rep = V.evaluate(St.SessionDrift(), self.bars, I.MES, trial="drift",
                         survivorship="static_list", ledger=self.ledger, n_paths=20)
        status = {c["check"]: c["status"] for c in rep["checks"]}
        self.assertEqual(status["survivorship"], "FAIL")

    def test_grid_is_charged_before_the_burden_is_computed(self):
        rep = self._eval(St.OpeningRangeBreakout(15), "orb",
                         grid={"or_minutes": [5, 15, 30], "take_r": [None, 1.0]})
        self.assertEqual(rep["stats"]["trials"], 6)
        self.assertIsNotNone(rep["walk_forward"])


class TestData(unittest.TestCase):
    def test_continuous_back_adjusts_the_roll(self):
        rows = [(100, 101, 99, 100)] * 3
        frames = []
        for i, d in enumerate(("2024-03-11", "2024-03-12", "2024-03-13", "2024-03-14")):
            a = _bars(rows, d).assign(contract="MESH4", volume=1000.0 if i < 2 else 10.0)
            b = _bars([tuple(x + 10 for x in r) for r in rows], d).assign(
                contract="MESM4", volume=10.0 if i < 2 else 1000.0)
            frames += [a, b]
        raw = pd.concat(frames).sort_index(kind="stable")
        out = data.continuous(raw)
        self.assertEqual(len(out.attrs["rolls"]), 1)
        self.assertAlmostEqual(out.attrs["rolls"][0]["gap"], 10.0)
        self.assertFalse(out.index.has_duplicates)
        self.assertTrue(np.allclose(out["close"], 110.0))   # no jump at the roll
        self.assertEqual(list(out["contract"].unique()), ["MESH4", "MESM4"])

    def test_naive_timestamps_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bars.csv")
            pd.DataFrame({"ts": ["2024-03-04 14:30:00"], "open": [1.0], "high": [1.0],
                          "low": [1.0], "close": [1.0], "volume": [1]}).to_csv(p, index=False)
            with self.assertRaises(ValueError):
                data.load_bars(p)
            b = data.load_bars(p, naive_tz="UTC")
            self.assertEqual(str(b.index[0]), "2024-03-04 09:30:00-05:00")

    def test_databento_columns_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mes.csv")
            pd.DataFrame({"ts_event": ["2024-03-04T14:30:00Z", "2024-03-04T14:31:00Z"],
                          "rtype": [33, 33], "open": [1.0, 1.0], "high": [2.0, 2.0],
                          "low": [0.5, 0.5], "close": [1.5, 1.5], "volume": [9, 9],
                          "symbol": ["MESH4", "MESH4"]}).to_csv(p, index=False)
            b = data.load_bars(p)
            self.assertIn("contract", b.columns)
            self.assertEqual(len(data.rth(b)), 2)

    def test_bad_bars_rejected(self):
        b = _bars([(100, 99, 98, 100)])                      # high below open
        with self.assertRaises(ValueError):
            data.validate(b)


class TestNoExecution(unittest.TestCase):
    def test_package_contains_no_order_code(self):
        here = os.path.dirname(os.path.abspath(data.__file__))
        banned = ("rhengine.execution", "from ..execution", "place_equity_order",
                  "place_order", "submit_order", "import requests")
        for path in glob.glob(os.path.join(here, "*.py")):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for word in banned:
                self.assertNotIn(word, src, f"{os.path.basename(path)} contains {word!r}")

    def test_scripts_contain_no_order_code(self):
        # The scripts may fetch data (requests) but never route an order.
        here = os.path.join(os.path.dirname(os.path.abspath(data.__file__)), "scripts")
        banned = ("rhengine.execution", "place_equity_order", "place_order",
                  "submit_order", "/v2/orders")
        for path in glob.glob(os.path.join(here, "*.py")):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for word in banned:
                self.assertNotIn(word, src, f"{os.path.basename(path)} contains {word!r}")


class TestPaper(unittest.TestCase):
    def test_records_each_completed_session_once(self):
        b = data.synthetic_bars(days=6, seed=1)
        r = engine.backtest(St.SessionDrift(), b, I.SPY_RH, capital=10_000, notional=10_000)
        days = [d.date() for d in r.daily.index]
        rows = paper.new_rows(r, {days[3].isoformat()}, days[2], days[4], "h")
        self.assertEqual([x["date"] for x in rows], [days[2].isoformat(), days[4].isoformat()])
        self.assertAlmostEqual(rows[0]["net_usd"], r.daily.iloc[2], places=3)
        self.assertEqual(len(rows[0]["trades"]), 1)
        s = paper.summary(rows)
        self.assertEqual((s["sessions"], s["trades"], s["rules"]), (2, 2, ["h"]))
        self.assertEqual(paper.summary([])["sessions"], 0)

    def test_incomplete_sessions_are_not_recorded(self):
        b = data.synthetic_bars(days=3, seed=1)
        days = sorted(set(data.session_dates(b.index)))
        cut = b[~((data.session_dates(b.index) == days[1]) & (b.index.hour >= 14))]
        self.assertEqual(paper.complete_sessions(cut), {days[0], days[2]})
        r = engine.backtest(St.SessionDrift(), cut, I.SPY_RH, capital=10_000, notional=10_000)
        rows = paper.new_rows(r, set(), days[0], days[2], "h",
                              complete=paper.complete_sessions(cut))
        self.assertEqual([x["date"] for x in rows], [days[0].isoformat(), days[2].isoformat()])

    def test_rule_hash_changes_with_the_rule(self):
        a = paper.rule_hash(St.NoiseArea(), I.SPY_RH, 10_000)
        self.assertEqual(a, paper.rule_hash(St.NoiseArea(), I.SPY_RH, 10_000))
        self.assertNotEqual(a, paper.rule_hash(St.NoiseArea(vm=1.2), I.SPY_RH, 10_000))


class TestPublishedRules(unittest.TestCase):
    def test_intraday_momentum_side_and_timing(self):
        b = data.synthetic_bars(days=3, seed=4)
        sig = St.IntradayMomentum().signals(b)
        day = data.session_dates(b.index)
        hhmm = b.index.strftime("%H:%M")
        first = day == day[0]
        self.assertTrue((sig["target"][first] == 0).all())          # no prior close
        for d in sorted(set(day))[1:]:
            on = day == d
            prev_close = b["close"][day == sorted(set(day))[sorted(set(day)).index(d) - 1]].iloc[-1]
            r = b["close"][on & (hhmm == "15:29")].iloc[0] - prev_close
            t = sig["target"][on]
            self.assertTrue((t[hhmm[on] < "15:29"] == 0).all())
            self.assertTrue((t[hhmm[on] >= "15:29"] == np.sign(r)).all())
        r = engine.backtest(St.IntradayMomentum(), b, I.MES)
        self.assertTrue((r.trades["entry_ts"].dt.strftime("%H:%M") == "15:30").all())

    def test_new_rules_are_causal(self):
        b = data.synthetic_bars(days=25, seed=5)
        for s in (St.IntradayMomentum(), St.IntradayMomentum(ref="10:00"),
                  St.NoiseArea(lookback=5, min_days=3)):
            engine.causality_check(s, b, n_checks=10)

    def test_noise_area_trades_only_on_half_hours(self):
        b = data.synthetic_bars(days=25, seed=6)
        r = engine.backtest(St.NoiseArea(lookback=5, min_days=3), b, I.MES)
        self.assertGreater(len(r.trades), 0)
        mins = r.trades["entry_ts"].dt.minute.to_numpy()
        self.assertTrue(set(mins) <= {0, 30})

    def test_noise_area_exit_options(self):
        b = data.synthetic_bars(days=25, seed=6)
        kw = dict(lookback=5, min_days=3)
        for strat in (St.NoiseArea(exit_rule="vwap", **kw), St.NoiseArea(stop_every=1, **kw)):
            self.assertGreater(engine.causality_check(strat, b), 0)
            r = engine.backtest(strat, b, I.MES)
            self.assertTrue(set(r.trades["entry_ts"].dt.minute) <= {0, 30})
        base = engine.backtest(St.NoiseArea(**kw), b, I.MES).trades
        fine = engine.backtest(St.NoiseArea(stop_every=1, **kw), b, I.MES).trades
        # Minute-by-minute stops can only cut a trade sooner, never later.
        self.assertLessEqual(fine["hold"].mean(), base["hold"].mean())
        with self.assertRaises(ValueError):
            St.NoiseArea(exit_rule="ladder")

    def test_prior_day_is_strictly_before(self):
        s = pd.Series([10.0, 20.0, 30.0], index=pd.to_datetime(
            ["2024-01-02", "2024-01-03", "2024-01-04"]).date)
        got = data.prior_day(s, pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-05"]).date)
        self.assertTrue(np.isnan(got[0]))
        self.assertEqual(list(got[1:]), [10.0, 30.0])

    def test_day_filter_and_long_only(self):
        b = data.synthetic_bars(days=4, seed=7)
        days = sorted(set(data.session_dates(b.index)))
        allowed = pd.Series([False, True, False, True], index=days)
        f = St.DayFilter(St.SessionDrift(), allowed, "test")
        r = engine.backtest(f, b, I.MES)
        self.assertEqual(sorted(r.trades["day"]), [days[1], days[3]])

        class Short(engine.Strategy):
            name = "short"
            def signals(self, bars):
                return pd.DataFrame({"target": -1.0}, index=bars.index)
        self.assertEqual(len(engine.backtest(Short(), b, I.SPY_RH, notional=10_000).trades), 0)


class TestSilo(unittest.TestCase):
    """intraday is its own project: it may borrow rhengine's statistics, never
    its rules, data or ledger."""

    def test_only_shared_stats_imported(self):
        import re
        here = os.path.dirname(os.path.abspath(data.__file__))
        allowed = {"rhengine.trials", "rhengine.alpha.stats"}
        for path in glob.glob(os.path.join(here, "*.py")):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r"^\s*from (rhengine[\w.]*) import (\w+)", src, re.M):
                mod = m.group(1) if m.group(1) != "rhengine" else f"rhengine.{m.group(2)}"
                if m.group(1) == "rhengine.alpha":
                    mod = f"rhengine.alpha.{m.group(2)}"
                self.assertIn(mod, allowed, f"{os.path.basename(path)} imports {mod}")

    def test_own_ledger(self):
        self.assertNotEqual(os.path.abspath(V.LEDGER), os.path.abspath(T.ledger_path()))
        self.assertIn(os.path.join("intraday", "data"), V.LEDGER)


if __name__ == "__main__":
    unittest.main()
