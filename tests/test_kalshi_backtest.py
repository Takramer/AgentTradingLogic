"""Minute-bar replay of the Kalshi v5 paper rules. No network."""

import inspect
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

import rhengine.kalshi_backtest as bt
from rhengine.kalshi_paper import PaperConfig


CLOSE = 1_800_000_000


def cfg(**overrides):
    base = dict(
        initial_balance=1000.0,
        time_delay=8.0,
        entry_min=0.75,
        entry_max=0.85,
        exit=0.40,
        take_profit_pts=0.10,
        delta=0.0,
        assets=("BTC",),
        order_size=1.0,
        pings=1,
        daily_profit_target=400.0,
        daily_loss_target=-600.0,
        poll_interval_ms=700,
        poll_jitter_max_ms=250,
        market_refresh_ms=5000,
        price_refresh_ms=1500,
    )
    base.update(overrides)
    return PaperConfig(**base)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mkt(ticker="T", result="yes", floor=72000.0, close_ts=CLOSE, **extra):
    row = {
        "ticker": ticker,
        "close_time": iso(close_ts),
        "open_time": iso(close_ts - 900),
        "result": result,
        "floor_strike": floor,
        "close_ts": close_ts,
        "source": "live",
    }
    row.update(extra)
    return row


def block(ohlc):
    if ohlc is None:
        return {}
    out = {}
    for name, value in zip(("open", "high", "low", "close"), ohlc):
        if value is not None:
            out[name] = value
    return out


def candle(mins_left, ask, bid=None, close_ts=CLOSE):
    return {
        "end_period_ts": close_ts - int(mins_left * 60),
        "yes_ask": block(ask),
        "yes_bid": block(bid),
    }


def flat(px):
    return (px, px, px, px)


def one(bars, mode, result="yes", **cfg_kw):
    return bt.replay_market(mkt(result=result), bars, cfg(**cfg_kw), mode)


class TestRepair(unittest.TestCase):
    def test_empty_book_ask_high_is_replaced_and_a_real_open_of_one_stays(self):
        raw = {
            "yes_ask": {
                "open": "1.0000", "high": "1.0000",
                "low": "0.4700", "close": "0.6200",
            },
            "yes_bid": {
                "open": "0.0010", "high": "0.6100",
                "low": "0.0010", "close": "0.6100",
            },
        }
        up, down = bt.side_books(raw)
        self.assertEqual(up, (1.0, 0.62, 0.47, 0.62))
        self.assertEqual(down, (0.999, 0.39, 0.39, 0.39))

    def test_a_real_zero_close_is_kept(self):
        raw = {
            "yes_ask": {"open": "0", "high": "0", "low": "0", "close": "0"},
            "yes_bid": {"open": "0", "high": "0", "low": "0", "close": "0"},
        }
        up, down = bt.side_books(raw)
        self.assertEqual(up, (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(down[3], 1.0)
        self.assertEqual(down[1], 1.0)

    def test_a_real_one_close_is_kept(self):
        repaired = bt.repair_ohlc(1.0, 1.0, 1.0, 1.0)
        self.assertEqual(repaired, (1.0, 1.0, 1.0, 1.0))

    def test_legacy_cents_are_scaled(self):
        raw = {"yes_ask": {"open": 60, "high": 100, "low": 55, "close": 62}}
        up, _down = bt.side_books(raw)
        self.assertEqual(up[3], 0.62)
        self.assertEqual(up[1], 0.62)


class TestReplay(unittest.TestCase):
    def test_close_mode_enters_on_the_first_in_window_close_and_books_the_later_close(self):
        bars = [
            candle(9, flat(0.80)),
            candle(8, flat(0.76)),
            candle(3, flat(0.84)),
            candle(2, flat(0.99)),
        ]
        out = one(bars, "close")
        trade = out["trade"]
        self.assertEqual(trade["entry"], 0.76)
        self.assertEqual(trade["exit"], 0.99)
        self.assertEqual(trade["reason"], "take_profit")
        self.assertAlmostEqual(trade["pnl"], 0.23)

    def test_close_mode_stops_at_the_minute_close(self):
        bars = [candle(6, flat(0.80)), candle(5, (0.70, 0.70, 0.10, 0.25))]
        out = one(bars, "close")
        self.assertEqual(out["trade"]["reason"], "stop")
        self.assertEqual(out["trade"]["exit"], 0.25)
        opened_through = one(
            [candle(6, flat(0.80)), candle(5, (0.20, 0.30, 0.10, 0.25))],
            "close")
        self.assertEqual(opened_through["trade"]["exit"], 0.20)

    def test_close_mode_does_not_enter_outside_the_band(self):
        bars = [candle(5, flat(0.60)), candle(4, (0.70, 0.90, 0.70, 0.60))]
        out = one(bars, "close", result="yes")
        self.assertIsNone(out["trade"])
        self.assertTrue(out["covered"])

    def test_band_edges_are_inclusive_and_the_exit_price_is_not_a_stop(self):
        entered = one([candle(5, flat(0.75)), candle(0, flat(0.40))], "close")
        self.assertEqual(entered["trade"]["reason"], "settlement")
        missed = one([candle(5, flat(0.74))], "close")
        self.assertIsNone(missed["trade"])
        top = one([candle(5, flat(0.85)), candle(1, flat(0.40))], "close")
        self.assertEqual(top["trade"]["reason"], "settlement")

    def test_touch_does_not_exit_on_the_entry_bar(self):
        bars = [candle(5, (0.80, 0.95, 0.80, 0.80))]
        out = one(bars, "touch", result="yes")
        self.assertEqual(out["trade"]["reason"], "settlement")
        self.assertEqual(out["trade"]["entry"], 0.85)
        self.assertEqual(out["trade"]["exit"], 1.0)

    def test_touch_fills_at_the_top_of_the_band_and_takes_profit_at_the_target(self):
        bars = [
            candle(6, (0.72, 0.90, 0.70, 0.80)),
            candle(5, (0.80, 0.99, 0.80, 0.99)),
        ]
        out = one(bars, "touch")
        trade = out["trade"]
        self.assertEqual(trade["entry"], 0.85)
        self.assertEqual(trade["exit"], 0.95)
        self.assertEqual(trade["reason"], "take_profit")
        self.assertAlmostEqual(trade["pnl"], 0.10)

    def test_repaired_one_handle_does_not_enter(self):
        bars = [candle(5, (1.0, 1.0, 0.50, 0.55))]
        out = one(bars, "touch")
        self.assertIsNone(out["trade"])

    def test_a_wick_below_the_exit_that_closes_back_above_does_not_stop(self):
        bars = [
            candle(6, flat(0.80)),
            candle(5, (0.80, 0.80, 0.20, 0.70)),
        ]
        out = one(bars, "touch", result="yes")
        self.assertEqual(out["trade"]["reason"], "settlement")
        self.assertEqual(out["trade"]["exit"], 1.0)

    def test_a_crash_that_closes_below_the_exit_fills_at_the_close(self):
        bars = [
            candle(6, flat(0.80)),
            candle(5, (0.70, 0.70, 0.10, 0.15)),
        ]
        out = one(bars, "touch")
        self.assertEqual(out["trade"]["reason"], "stop")
        self.assertEqual(out["trade"]["exit"], 0.15)

    def test_a_bar_that_opens_through_the_stop_fills_at_the_open(self):
        bars = [
            candle(6, flat(0.80)),
            candle(5, (0.30, 0.30, 0.10, 0.15)),
        ]
        out = one(bars, "touch")
        self.assertEqual(out["trade"]["exit"], 0.30)

    def test_bearish_bar_that_hits_stop_and_target_takes_the_stop(self):
        bars = [
            candle(6, flat(0.80)),
            candle(5, (0.50, 0.95, 0.05, 0.30)),
        ]
        out = one(bars, "touch")
        self.assertEqual(out["trade"]["reason"], "stop")
        self.assertEqual(out["trade"]["exit"], 0.30)

    def test_a_bar_that_is_not_bearish_and_hits_both_takes_profit(self):
        bars = [
            candle(6, flat(0.80)),
            candle(5, (0.20, 0.95, 0.05, 0.30)),
        ]
        out = one(bars, "touch")
        self.assertEqual(out["trade"]["reason"], "take_profit")
        self.assertEqual(out["trade"]["exit"], 0.90)

    def test_missing_open_lets_the_stop_win(self):
        bars = [
            candle(6, flat(0.80)),
            candle(5, (None, 0.95, 0.05, 0.30)),
        ]
        out = one(bars, "touch")
        self.assertEqual(out["trade"]["reason"], "stop")

    def test_higher_close_wins_and_a_tie_buys_up(self):
        down_bid = flat(0.18)  # DOWN close = 0.82
        up_lower = one([candle(5, flat(0.78), down_bid)], "close", result="no")
        self.assertEqual(up_lower["trade"]["side"], "DOWN")
        self.assertEqual(up_lower["trade"]["entry"], 0.82)
        tie_bid = flat(0.20)  # DOWN close = 0.80
        tie = one([candle(5, flat(0.80), tie_bid)], "close", result="yes")
        self.assertEqual(tie["trade"]["side"], "UP")

    def test_down_settles_at_one_when_the_result_is_no(self):
        bid = flat(0.20)
        out = one([candle(5, flat(0.10), bid)], "close", result="no")
        self.assertEqual(out["trade"]["side"], "DOWN")
        self.assertEqual(out["trade"]["exit"], 1.0)
        self.assertEqual(out["trade"]["reason"], "settlement")

    def test_up_settles_at_zero_when_the_result_is_no(self):
        out = one([candle(5, flat(0.80))], "close", result="no")
        self.assertEqual(out["trade"]["exit"], 0.0)
        self.assertLess(out["trade"]["pnl"], 0)

    def test_target_above_one_holds_to_settlement(self):
        bars = [candle(5, flat(0.80)), candle(1, flat(0.99))]
        out = one(bars, "close", take_profit_pts=0.30, result="yes")
        self.assertEqual(out["trade"]["reason"], "settlement")
        self.assertEqual(out["trade"]["exit"], 1.0)

    def test_bar_after_the_close_and_the_bar_at_the_close_cannot_enter(self):
        late = one([candle(-1, flat(0.80)), candle(9, flat(0.10))], "close")
        self.assertIsNone(late["trade"])
        self.assertTrue(late["covered"])
        at_close = one([candle(0, flat(0.80))], "close")
        self.assertIsNone(at_close["trade"])

    def test_missing_floor_and_a_positive_delta_do_not_enter(self):
        bars = [candle(5, flat(0.80))]
        missing = bt.replay_market(mkt(floor=None), bars, cfg(), "close")
        self.assertIsNone(missing["trade"])
        positive = one(bars, "close", delta=5)
        self.assertIsNone(positive["trade"])

    def test_zero_pnl_is_a_loss(self):
        bars = [candle(5, flat(0.30)), candle(4, flat(0.30))]
        book = bt.replay_book(
            [mkt()],
            lambda _ticker: bars,
            cfg(entry_min=0.30, entry_max=0.50),
            "close",
            kill=False,
        )
        self.assertEqual(book["trades"], 1)
        self.assertEqual(book["wins"], 0)
        self.assertEqual(book["losses"], 1)
        self.assertEqual(book["pnl"], 0.0)

    def test_post_expiry_bar_is_dropped(self):
        only_after = [candle(-1, flat(0.80))]
        out = one(only_after, "close")
        self.assertFalse(out["covered"])
        self.assertIsNone(out["trade"])


class TestAccount(unittest.TestCase):
    def _bars(self, px=0.80):
        return [candle(5, flat(px))]

    def test_kill_switch_stops_the_next_market_and_a_tie_at_the_target_does_not(self):
        first = mkt("A", result="yes", close_ts=CLOSE)
        second = mkt("B", result="yes", close_ts=CLOSE + 900)
        candles = {
            "A": [candle(5, flat(0.80), close_ts=CLOSE)],
            "B": [candle(5, flat(0.80), close_ts=CLOSE + 900)],
        }
        stopped = bt.replay_book(
            [second, first], lambda t: candles[t],
            cfg(daily_profit_target=0.10), "close", kill=True)
        self.assertEqual(stopped["trades"], 1)
        self.assertEqual(stopped["halt"], "daily_profit")
        self.assertEqual(stopped["pnl"], 0.20)
        kept = bt.replay_book(
            [first, second], lambda t: candles[t],
            cfg(daily_profit_target=0.20), "close", kill=True)
        # +0.20 is not greater than a 0.20 target, so the next market still trades.
        # The second win takes the account to +0.40 and then the process stops.
        self.assertEqual(kept["trades"], 2)
        self.assertEqual(kept["halt"], "daily_profit")
        self.assertEqual(kept["pnl"], 0.40)
        running = bt.replay_book(
            [first, second], lambda t: candles[t],
            cfg(daily_profit_target=0.10), "close", kill=False)
        self.assertEqual(running["trades"], 2)
        self.assertIsNone(running["halt"])

    def test_loss_target_uses_a_strict_cent_comparison(self):
        market = mkt(result="no")
        book = bt.replay_book(
            [market], lambda _t: self._bars(0.80),
            cfg(daily_loss_target=-0.80), "close", kill=True)
        self.assertEqual(book["trades"], 1)
        self.assertIsNone(book["halt"])
        self.assertEqual(book["pnl"], -0.80)
        tripped = bt.replay_book(
            [market], lambda _t: self._bars(0.80),
            cfg(daily_loss_target=-0.50), "close", kill=True)
        self.assertEqual(tripped["halt"], "daily_loss")

    def test_insufficient_cash_halts_without_a_trade(self):
        book = bt.replay_book(
            [mkt()], lambda _t: self._bars(0.80),
            cfg(initial_balance=0.50), "close", kill=True)
        self.assertEqual(book["trades"], 0)
        self.assertEqual(book["halt"], "insufficient_balance")
        self.assertEqual(book["end_cash"], 0.50)

    def test_a_missing_candle_file_counts_as_missing_not_as_a_flat_market(self):
        book = bt.replay_book(
            [mkt("A"), mkt("B", close_ts=CLOSE + 900)],
            lambda ticker: self._bars() if ticker == "A" else None,
            cfg(), "close", kill=False)
        self.assertEqual(book["markets"], 2)
        self.assertEqual(book["missing"], 1)
        self.assertEqual(book["with_bars"], 1)
        self.assertEqual(book["trades"], 1)

    def test_windows_are_measured_back_from_the_latest_close(self):
        end = CLOSE
        markets = [
            mkt("near", close_ts=end - 10 * 86400),
            mkt("mid", close_ts=end - 40 * 86400),
            mkt("far", close_ts=end - 100 * 86400),
        ]
        d30, _start = bt.slice_window(markets, 30, end)
        d90, _start = bt.slice_window(markets, 90, end)
        d180, _start = bt.slice_window(markets, 180, end)
        self.assertEqual([m["ticker"] for m in d30], ["near"])
        self.assertEqual({m["ticker"] for m in d90}, {"near", "mid"})
        self.assertEqual({m["ticker"] for m in d180}, {"near", "mid", "far"})
        _chosen, start180 = bt.slice_window(markets, 180, end)
        self.assertFalse(bt.window_is_short(end - 200 * 86400, start180))
        self.assertTrue(bt.window_is_short(end - 100 * 86400, start180))


class TestListing(unittest.TestCase):
    def test_pages_stop_at_the_window_and_live_wins_a_duplicate(self):
        def fake(path, params):
            if path == "/markets":
                if not params.get("cursor"):
                    return {"markets": [{
                        "ticker": "KXBTC15M-NEW",
                        "close_time": iso(2000),
                        "open_time": iso(1000),
                        "result": "yes",
                        "floor_strike": 1,
                    }], "cursor": "c"}
                return {"markets": [{
                    "ticker": "KXBTC15M-OLD",
                    "close_time": iso(10),
                    "result": "yes",
                    "floor_strike": 1,
                }], "cursor": "d"}
            if path == "/historical/markets":
                return {"markets": [
                    {
                        "ticker": "KXBTC15M-NEW",
                        "close_time": iso(2000),
                        "result": "no",
                        "floor_strike": 9,
                    },
                    {
                        "ticker": "KXBTC15M-HIST",
                        "close_time": iso(1500),
                        "result": "yes",
                        "floor_strike": 3,
                    },
                    {
                        "ticker": "OTHER-1",
                        "close_time": iso(1800),
                        "result": "yes",
                    },
                ], "cursor": ""}
            raise AssertionError(path)

        rows = bt.list_settled(100, get=fake)
        self.assertEqual([r["ticker"] for r in rows], ["KXBTC15M-NEW", "KXBTC15M-HIST"])
        self.assertEqual(rows[0]["result"], "yes")
        self.assertEqual(rows[0]["source"], "live")
        self.assertEqual(rows[1]["source"], "historical")

    def test_empty_historical_response_falls_through_to_bars(self):
        market = mkt(source="historical")
        calls = []

        def fake(path, params):
            calls.append(path)
            if path.startswith("/historical/"):
                return {"candlesticks": []}
            return {"candlesticks": [{"end_period_ts": 1}]}

        bars = bt.fetch_candles(market, get=fake)
        self.assertEqual(len(bars), 1)
        self.assertTrue(calls[0].startswith("/historical/"))
        self.assertEqual(calls[1], "/series/KXBTC15M/markets/T/candlesticks")

    def test_both_missing_raises_and_does_not_return_an_empty_list(self):
        with self.assertRaises(ConnectionError):
            bt.fetch_candles(mkt(), get=lambda path, params: None)


class TestCommand(unittest.TestCase):
    def test_module_does_not_define_an_order_call(self):
        src = inspect.getsource(bt)
        self.assertNotIn("requests.post", src)
        self.assertNotIn("requests.put", src)
        self.assertNotIn("create_order", src)
        self.assertNotIn("/portfolio/orders", src)

    def test_offline_replays_a_cached_window(self):
        market = mkt("KXBTC15M-TEST")
        bars = [candle(5, flat(0.80))]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "_markets.json"), "w", encoding="utf-8") as handle:
                json.dump([market], handle)
            with open(os.path.join(tmp, market["ticker"] + ".json"), "w", encoding="utf-8") as handle:
                json.dump({"market": market, "candlesticks": bars}, handle)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = bt.main(["--offline", "--cache", tmp, "--months", "1"])
            text = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Not an rhengine.alpha.book result.", text)
        self.assertIn("pnl    +0.20", text)
        self.assertIn("kill on", text)
        self.assertIn("kill off", text)
        self.assertIn("close", text)
        self.assertIn("touch", text)


if __name__ == "__main__":
    unittest.main()
