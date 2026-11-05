"""Paper rules for the Polymarket up/down runner.

Network stays out of these tests. The live APIs are exercised by
`python3 -m polymarket_paper --once`, not by the suite.
"""

import json
import struct
import unittest
from datetime import datetime, timezone

from polymarket_paper.backtest import (
    completed_starts,
    format_report,
    naive_t,
    parse_gamma_batch,
    parse_history,
    replay_window,
    summarize_horizon,
)
from polymarket_paper.bot import PaperBot
from polymarket_paper.market import (
    MarketFeed,
    Snapshot,
    WsBuffer,
    encode_client_text,
    market_slug,
    parse_gamma_market,
    parse_mid,
    parse_open_price,
    parse_spot_price,
    prices_from_message,
    window_bounds,
)
from polymarket_paper.rules import (
    HaltTrading,
    entry_decision,
    exit_decision,
    resolution_price,
)
from polymarket_paper.settings import default_config_path, load_raw, settings_from, validate


def _settings(**overrides):
    raw = {
        "initialBalance": 1000,
        "assets": ["BTC"],
        "TimeFrame": "15M",
        "timeDelay": 15,
        "EntryRange": [0.60, 0.80],
        "Exit": 0.40,
        "TakeProfitPts": 0.10,
        "Delta": 0,
        "orderSize": 5,
    }
    raw.update(overrides)
    return settings_from(raw)


class ScriptedFeed:
    def __init__(self, snap=None, open_price=100.0, live=(130.0, "script")):
        self.snap = snap
        self.open = open_price
        self.live = live

    def snapshot(self, asset, now):
        return self.snap

    def open_price(self, asset, snap):
        return self.open

    def live_price(self, asset):
        return self.live


def _snap(up, down, now, start, end, ticker):
    return Snapshot(
        up=up, down=down, minutes_left=(end - now) / 60.0,
        start_sec=start, end_sec=end, market_ticker=ticker,
        up_token_id="up-token", down_token_id="down-token", condition_id="0xabc",
    )


class RuleTests(unittest.TestCase):
    def test_entry_prefers_the_higher_side_inside_the_range(self):
        chosen, reason = entry_decision(0.70, 0.65, 0.60, 0.80, 30, 0)
        self.assertEqual(chosen, ("UP", 0.70))
        self.assertIsNone(reason)
        chosen, reason = entry_decision(0.62, 0.70, 0.60, 0.80, 30, 0)
        self.assertEqual(chosen[0], "DOWN")
        self.assertIsNone(reason)

    def test_entry_takes_the_only_side_inside_the_range(self):
        chosen, _ = entry_decision(0.70, 0.90, 0.60, 0.80, 0, 0)
        self.assertEqual(chosen[0], "UP")
        chosen, _ = entry_decision(0.40, 0.70, 0.60, 0.80, 0, 0)
        self.assertEqual(chosen[0], "DOWN")

    def test_missing_live_price_skips_even_when_delta_is_zero(self):
        chosen, reason = entry_decision(0.70, 0.30, 0.60, 0.80, None, 0)
        self.assertIsNone(chosen)
        self.assertEqual(reason, "DELTA_LOW")

    def test_delta_below_the_minimum_skips(self):
        _, reason = entry_decision(0.70, 0.30, 0.60, 0.80, 10, 50)
        self.assertEqual(reason, "DELTA_LOW")

    def test_stop_is_strict_and_take_profit_above_one_does_not_fire(self):
        self.assertIsNone(exit_decision(0.40, 0.40, 0.85))
        self.assertEqual(exit_decision(0.39, 0.40, 0.85), "stop")
        self.assertEqual(exit_decision(0.85, 0.40, 0.85), "take_profit")
        self.assertIsNone(exit_decision(0.99, 0.40, 1.05))

    def test_resolution_uses_the_last_mark(self):
        self.assertEqual(resolution_price(0.50), 1)
        self.assertEqual(resolution_price(0.49), 0)


class SettingsTests(unittest.TestCase):
    def test_shipped_config_matches_the_video_defaults(self):
        raw = load_raw(default_config_path())
        self.assertEqual(validate(raw), [])
        settings = settings_from(raw)
        self.assertEqual(settings.assets, ("BTC",))
        self.assertEqual(settings.timeframe, "15M")
        self.assertEqual(settings.entry_min, 0.60)
        self.assertEqual(settings.entry_max, 0.80)
        self.assertEqual(settings.exit, 0.40)
        self.assertEqual(settings.take_profit_pts, 0.10)
        self.assertEqual(settings.delta, 0)
        self.assertEqual(settings.order_size, 5)
        self.assertEqual(settings.initial_balance, 1000)
        self.assertIn("TimeFrame: 15M", settings.banner())
        self.assertIn("InitialBalance: 1000.00", settings.banner())

    def test_order_below_five_is_rejected(self):
        errors = validate({
            "initialBalance": 1000, "assets": ["BTC"], "TimeFrame": "15M",
            "timeDelay": 15, "EntryRange": [0.60, 0.80], "Exit": 0.40,
            "TakeProfitPts": 0.10, "Delta": 0, "orderSize": 4,
        })
        self.assertTrue(any("at least 5" in line for line in errors))

    def test_delay_cannot_exceed_the_window(self):
        errors = validate({
            "initialBalance": 1000, "assets": ["btc"], "TimeFrame": "15m",
            "timeDelay": 20, "EntryRange": [0.80, 0.60], "Exit": 0,
            "TakeProfitPts": 0, "Delta": -1, "orderSize": "5.5",
        })
        blob = "\n".join(errors)
        self.assertIn("timeDelay", blob)
        self.assertIn("greater than", blob)
        self.assertIn("Exit", blob)
        self.assertIn("TakeProfitPts", blob)
        self.assertIn("Delta", blob)


class SlugTests(unittest.TestCase):
    def test_fifteen_minute_slug_is_the_window_start(self):
        start, end = window_bounds(1_791_316_860, 900)
        self.assertEqual(end - start, 900)
        self.assertEqual(market_slug("BTC", "15M", start), f"btc-updown-15m-{start}")

    def test_hourly_slug_uses_eastern_time(self):
        cases = (
            (datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc),
             "bitcoin-up-or-down-october-6-2026-4pm-et"),
            (datetime(2026, 1, 15, 21, 0, tzinfo=timezone.utc),
             "bitcoin-up-or-down-january-15-2026-4pm-et"),
            (datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc),
             "bitcoin-up-or-down-january-15-2026-12pm-et"),
            (datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc),
             "bitcoin-up-or-down-january-15-2026-12am-et"),
        )
        for moment, slug in cases:
            with self.subTest(slug=slug):
                self.assertEqual(market_slug("BTC", "1H", int(moment.timestamp())), slug)


class FeedParseTests(unittest.TestCase):
    def test_gamma_string_fields_map_up_and_down(self):
        parsed = parse_gamma_market([{
            "slug": "btc-updown-15m-1",
            "markets": [{
                "outcomes": "[\"Up\", \"Down\"]",
                "clobTokenIds": "[\"111\", \"222\"]",
                "conditionId": "0xabc",
                "slug": "btc-updown-15m-1",
            }],
        }])
        self.assertEqual(parsed["up_token_id"], "111")
        self.assertEqual(parsed["down_token_id"], "222")

    def test_open_and_mid_parsers(self):
        self.assertEqual(parse_open_price({"openPrice": 85609.01, "closePrice": None}), 85609.01)
        self.assertIsNone(parse_open_price({"openPrice": None}))
        self.assertEqual(parse_mid({"mid": "0.625"}), 0.625)
        self.assertEqual(parse_spot_price({"data": {"amount": "85500.5"}}), 85500.5)
        self.assertEqual(parse_spot_price({"price": "85501"}), 85501)

    def test_chainlink_snapshot_uses_the_last_point(self):
        message = {
            "topic": "crypto_prices_chainlink",
            "type": "subscribe",
            "payload": {
                "symbol": "btc/usd",
                "data": [{"timestamp": 1, "value": 10}, {"timestamp": 2, "value": 12.5}],
            },
        }
        self.assertEqual(prices_from_message(message), [("btc/usd", 12.5)])
        update = {"payload": {"symbol": "btcusdt", "value": "99.5", "timestamp": 3}}
        self.assertEqual(prices_from_message(update), [("btcusdt", 99.5)])

    def test_websocket_buffer_rejoins_split_and_masked_frames(self):
        buffer = WsBuffer()
        payload = b"hello"
        frame = bytes([0x81, len(payload)]) + payload
        self.assertEqual(buffer.feed(frame[:3]), [])
        self.assertEqual(buffer.feed(frame[3:]), [("text", b"hello")])

        buffer = WsBuffer()
        part = bytes([0x01, 3]) + b"hel"
        rest = bytes([0x80, 2]) + b"lo"
        self.assertEqual(buffer.feed(part + rest), [("text", b"hello")])

        buffer = WsBuffer()
        self.assertEqual(buffer.feed(encode_client_text('{"ok":1}')), [("text", b'{"ok":1}')])

    def test_snapshot_from_fake_http_and_expired_window_skips_the_network(self):
        calls = []

        def http(url, timeout):
            calls.append(url)
            if "gamma-api" in url:
                return [{
                    "markets": [{
                        "outcomes": ["Up", "Down"],
                        "clobTokenIds": ["111", "222"],
                        "conditionId": "0xabc",
                        "slug": "btc-updown-15m-1000",
                    }],
                }]
            if "token_id=111" in url:
                return {"mid": "0.70"}
            if "token_id=222" in url:
                return {"mid": "0.30"}
            if "crypto-price" in url:
                return {"openPrice": 85000}
            if "coinbase" in url:
                return {"data": {"amount": "85100"}}
            return None

        settings = _settings()
        feed = MarketFeed(settings, http=http, mono=lambda: 10)
        start, end = window_bounds(1_000, settings.timeframe_seconds)
        snap = feed.snapshot("BTC", start + 60)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.up, 0.70)
        self.assertEqual(snap.down, 0.30)
        self.assertEqual(feed.open_price("BTC", snap), 85000)
        feed.note_price("btc/usd", 85300, "chainlink")
        self.assertEqual(feed.live_price("BTC"), (85300, "chainlink"))

        def explode(url, timeout):
            raise AssertionError(url)

        quiet = MarketFeed(settings, http=explode, mono=lambda: 10)
        self.assertIsNone(quiet.snapshot("BTC", end - 10))


class BotTests(unittest.TestCase):
    def _window(self, now, minutes_left=10):
        end = now + minutes_left * 60
        start = end - 900
        return start, end

    def test_first_tick_buys_and_take_profit_closes(self):
        now = 1_700_000_000.0
        start, end = self._window(now)
        feed = ScriptedFeed(snap=_snap(0.75, 0.25, now, start, end, "m1"))
        bot = PaperBot(_settings(), feed)
        logs = "\n".join(bot.step(now))
        self.assertIn("Paper position confirmed", logs)
        self.assertEqual(bot.books["BTC"].position.side, "UP")
        self.assertAlmostEqual(bot.account.balance, 996.25, places=4)

        feed.snap = _snap(0.90, 0.10, now + 1, start, end, "m1")
        logs = "\n".join(bot.step(now + 1))
        self.assertIn("Take-profit triggered", logs)
        self.assertIsNone(bot.books["BTC"].position)
        self.assertEqual(bot.books["BTC"].phase, "TOOK_PROFIT")
        self.assertAlmostEqual(bot.account.balance, 1000.75, places=4)
        bot.step(now + 2)
        self.assertIsNone(bot.books["BTC"].position)

    def test_stop_blocks_the_rest_of_the_session(self):
        now = 1_700_000_000.0
        start, end = self._window(now)
        feed = ScriptedFeed(snap=_snap(0.75, 0.25, now, start, end, "m1"))
        bot = PaperBot(_settings(), feed)
        bot.step(now)
        feed.snap = _snap(0.25, 0.75, now + 1, start, end, "m1")
        logs = "\n".join(bot.step(now + 1))
        self.assertIn("Stop-loss triggered", logs)
        self.assertEqual(bot.books["BTC"].phase, "STOPPED_OUT")
        feed.snap = _snap(0.75, 0.25, now + 2, start, end, "m1")
        bot.step(now + 2)
        self.assertIsNone(bot.books["BTC"].position)

    def test_session_change_settles_on_the_previous_mark_and_waits(self):
        now = 1_700_000_000.0
        start, end = self._window(now)
        feed = ScriptedFeed(snap=_snap(0.75, 0.25, now, start, end, "m1"))
        bot = PaperBot(_settings(), feed)
        bot.step(now)
        new_end = (now + 2) + 10 * 60
        new_start = new_end - 900
        feed.snap = _snap(0.10, 0.90, now + 1, new_start, new_end, "m2")
        logs = "\n".join(bot.step(now + 1))
        self.assertIn("settled at 1", logs)
        self.assertIn("last price 0.75", logs)
        self.assertIsNone(bot.books["BTC"].position)
        self.assertAlmostEqual(bot.account.balance, 1001.25, places=4)
        feed.snap = _snap(0.75, 0.25, now + 2, new_start, new_end, "m2")
        bot.step(now + 2)
        self.assertIsNotNone(bot.books["BTC"].position)

    def test_low_delta_and_a_price_outside_the_range_do_not_buy(self):
        now = 1_700_000_000.0
        start, end = self._window(now)
        feed = ScriptedFeed(
            snap=_snap(0.70, 0.30, now, start, end, "m1"),
            live=(110.0, "script"),
        )
        bot = PaperBot(_settings(Delta=50), feed)
        logs = "\n".join(bot.step(now))
        self.assertIn("skipping entry", logs)
        self.assertIsNone(bot.books["BTC"].position)

        feed = ScriptedFeed(snap=_snap(0.40, 0.55, now, start, end, "m1"))
        bot = PaperBot(_settings(), feed)
        logs = "\n".join(bot.step(now))
        self.assertIn("outside entry range", logs)
        self.assertIsNone(bot.books["BTC"].position)

    def test_outside_the_activation_window_waits(self):
        now = 1_700_000_000.0
        start, end = self._window(now, minutes_left=10)
        feed = ScriptedFeed(snap=_snap(0.75, 0.25, now, start, end, "m1"))
        bot = PaperBot(_settings(timeDelay=5), feed)
        logs = "\n".join(bot.step(now))
        self.assertIn("Waiting for last 5 minutes", logs)
        self.assertIsNone(bot.books["BTC"].position)

    def test_take_profit_of_one_holds_through_a_high_mark(self):
        now = 1_700_000_000.0
        start, end = self._window(now)
        feed = ScriptedFeed(snap=_snap(0.75, 0.25, now, start, end, "m1"))
        bot = PaperBot(_settings(TakeProfitPts=1), feed)
        logs = "\n".join(bot.step(now))
        self.assertIn("hold for resolution", logs)
        feed.snap = _snap(0.99, 0.01, now + 1, start, end, "m1")
        bot.step(now + 1)
        self.assertIsNotNone(bot.books["BTC"].position)

    def test_empty_balance_stops_without_opening(self):
        now = 1_700_000_000.0
        start, end = self._window(now)
        feed = ScriptedFeed(snap=_snap(0.75, 0.25, now, start, end, "m1"))
        bot = PaperBot(_settings(initialBalance=3), feed)
        with self.assertRaises(HaltTrading) as caught:
            bot.step(now)
        self.assertIn("Not enough balance", "\n".join(caught.exception.logs))
        self.assertEqual(bot.account.balance, 3)
        self.assertIsNone(bot.books["BTC"].position)

    def test_state_roundtrip_keeps_the_open_position(self):
        now = 1_700_000_000.0
        start, end = self._window(now)
        settings = _settings()
        feed = ScriptedFeed(snap=_snap(0.75, 0.25, now, start, end, "m1"))
        bot = PaperBot(settings, feed)
        bot.step(now)
        restored = PaperBot.from_state(settings, feed, json.loads(json.dumps(bot.export_state())))
        self.assertEqual(restored.books["BTC"].position.side, "UP")
        self.assertAlmostEqual(restored.account.balance, bot.account.balance, places=6)


class ReplayTests(unittest.TestCase):
    def test_take_profit_fills_at_the_sample(self):
        result = replay_window(
            [(60, 0.50), (120, 0.70), (180, 0.85)],
            start=0, end=900, up_won=False, settings=_settings(),
        )
        self.assertEqual(result.side, "UP")
        self.assertEqual(result.entry, 0.70)
        self.assertEqual(result.reason, "take_profit")
        self.assertEqual(result.bot_exit, 0.85)
        self.assertEqual(result.paid_exit, 0.85)
        self.assertEqual(result.bot_pnl, 0.75)

    def test_stop_fills_at_the_sample(self):
        result = replay_window(
            [(120, 0.70), (180, 0.30)],
            start=0, end=900, up_won=True, settings=_settings(),
        )
        self.assertEqual(result.reason, "stop")
        self.assertEqual(result.bot_exit, 0.30)
        self.assertEqual(result.paid_exit, 0.30)
        self.assertEqual(result.bot_pnl, -2.0)

    def test_down_side_stops_when_up_rises(self):
        result = replay_window(
            [(120, 0.30), (180, 0.75)],
            start=0, end=900, up_won=True, settings=_settings(),
        )
        self.assertEqual(result.side, "DOWN")
        self.assertEqual(result.entry, 0.70)
        self.assertEqual(result.reason, "stop")
        self.assertEqual(result.bot_exit, 0.25)

    def test_flat_when_neither_side_enters_the_band(self):
        result = replay_window(
            [(120, 0.50), (200, 0.55), (400, 0.45)],
            start=0, end=900, up_won=True, settings=_settings(),
        )
        self.assertEqual(result.status, "flat")
        self.assertIsNone(result.side)

    def test_hold_bot_and_official_can_disagree(self):
        agree = replay_window(
            [(120, 0.70), (200, 0.65)],
            start=0, end=900, up_won=True, settings=_settings(),
        )
        disagree = replay_window(
            [(120, 0.70), (200, 0.65)],
            start=0, end=900, up_won=False, settings=_settings(),
        )
        self.assertEqual(agree.reason, "held")
        self.assertEqual(agree.bot_exit, 1.0)
        self.assertEqual(agree.paid_exit, 1.0)
        self.assertEqual(agree.bot_pnl, 1.5)
        self.assertEqual(disagree.bot_exit, 1.0)
        self.assertEqual(disagree.paid_exit, 0.0)
        self.assertEqual(disagree.paid_pnl, -3.5)

    def test_no_entry_in_the_last_30_seconds(self):
        result = replay_window(
            [(880, 0.70)],
            start=0, end=900, up_won=True, settings=_settings(),
        )
        self.assertEqual(result.status, "flat")
        self.assertIsNone(result.side)

    def test_stop_inside_the_last_30_seconds_is_ignored(self):
        result = replay_window(
            [(120, 0.70), (880, 0.10)],
            start=0, end=900, up_won=False, settings=_settings(),
        )
        self.assertEqual(result.reason, "held")
        self.assertEqual(result.bot_exit, 1.0)
        self.assertEqual(result.paid_exit, 0.0)

    def test_time_delay_skips_early_prints(self):
        result = replay_window(
            [(60, 0.70), (700, 0.72)],
            start=0, end=900, up_won=True, settings=_settings(timeDelay=5),
        )
        self.assertEqual(result.entry_ts, 700)
        self.assertEqual(result.entry, 0.72)

    def test_stop_is_strict_at_the_exit_price(self):
        result = replay_window(
            [(120, 0.70), (180, 0.40)],
            start=0, end=900, up_won=True, settings=_settings(),
        )
        self.assertEqual(result.reason, "held")
        self.assertEqual(result.bot_exit, 0.0)
        self.assertEqual(result.paid_exit, 1.0)

    def test_completed_starts_drop_the_open_window(self):
        self.assertEqual(completed_starts(1800, 2, 900), [0, 900])
        self.assertEqual(completed_starts(1800.5, 2, 900), [0, 900])
        self.assertEqual(completed_starts(1799, 2, 900), [-900, 0])

    def test_parsers_read_gamma_strings_and_history(self):
        body = [{
            "slug": "btc-updown-15m-1",
            "closed": True,
            "outcomes": "[\"Up\", \"Down\"]",
            "clobTokenIds": "[\"111\", \"222\"]",
            "outcomePrices": "[\"0\", \"1\"]",
        }]
        rows = parse_gamma_batch(body)
        self.assertEqual(rows["btc-updown-15m-1"]["up_token"], "111")
        self.assertIs(rows["btc-updown-15m-1"]["up_won"], False)
        points = parse_history({
            "history": [{"t": 100, "p": "0.7"}, {"t": 1_600_000_000_000, "p": 0.2}],
        })
        self.assertEqual(points[0], (100, 0.7))
        self.assertEqual(points[1][0], 1_600_000_000)

    def test_naive_t_uses_the_sample_standard_deviation(self):
        self.assertAlmostEqual(naive_t([1, 1, 1, 3]), 3.0)
        self.assertIsNone(naive_t([1, 1, 1, 1]))

    def test_summary_splits_bot_and_paid_and_halts(self):
        settings = _settings()
        took = replay_window(
            [(120, 0.70), (180, 0.85)],
            start=0, end=900, up_won=True, settings=settings,
        )
        held = replay_window(
            [(120, 0.70), (200, 0.65)],
            start=0, end=900, up_won=False, settings=settings,
        )
        held.start = 900
        summary = summarize_horizon(
            [
                {"start": 0, "missing": False, "points": 2, "replay": took},
                {"start": 900, "missing": False, "points": 2, "replay": held},
                {"start": 1800, "missing": True, "points": 0, "replay": None},
            ],
            settings=settings, days=1, label="test",
        )
        self.assertEqual(summary["trades"], 2)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["flat"], 0)
        self.assertEqual(summary["hold_disagree"], 1)
        self.assertAlmostEqual(summary["bot"]["pnl_sum"], 2.25)
        self.assertAlmostEqual(summary["paid"]["pnl_sum"], -2.75)
        self.assertFalse(summary["bot"]["halted"])
        text = format_report({
            "asset": "BTC",
            "timeframe": "15M",
            "entry_min": 0.60,
            "entry_max": 0.80,
            "exit": 0.40,
            "take_profit": 0.10,
            "order_size": 5,
            "initial_balance": 1000,
            "fetch_errors": 0,
            "horizons": [summary],
        })
        self.assertIn("Not a book.py result", text)

        broke = _settings(initialBalance=1)
        halted = summarize_horizon(
            [{"start": 0, "missing": False, "points": 2, "replay": took}],
            settings=broke, days=1, label="halt",
        )
        self.assertTrue(halted["bot"]["halted"])
        self.assertEqual(halted["bot"]["filled"], 0)
        self.assertEqual(halted["bot"]["final_balance"], 1)
        self.assertEqual(halted["bot"]["pnl_sum"], 0.75)


class FrameLengthTests(unittest.TestCase):
    def test_sixteen_bit_length(self):
        payload = b"x" * 200
        frame = bytes([0x81, 126]) + struct.pack("!H", 200) + payload
        buffer = WsBuffer()
        self.assertEqual(buffer.feed(frame), [("text", payload)])


if __name__ == "__main__":
    unittest.main()
