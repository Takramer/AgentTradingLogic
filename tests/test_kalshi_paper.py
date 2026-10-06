"""Paper rules for the Kalshi v5 15-minute bot. No network."""

import contextlib
import inspect
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone

import rhengine.kalshi_paper as paper
from rhengine.kalshi_paper import (
    KalshiClient,
    PaperConfig,
    PaperEngine,
    apply_orderbook,
    best_bid,
    choose_side,
    compose_price,
    load_config,
    minutes_remaining_in_quarter,
    parse_config,
    pick_market,
    quarter_index,
    run,
)

NOW = datetime(2026, 10, 6, 20, 10, tzinfo=timezone.utc)  # 5 min before 20:15
EARLY = datetime(2026, 10, 6, 20, 2, tzinfo=timezone.utc)  # 13 min before 20:15
CLOSE = "2026-10-06T20:15:00Z"


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


def snap(**overrides):
    base = {
        "up": 0.80,
        "down": 0.22,
        "close_time": CLOSE,
        "market_ticker": "KXBTC15M-T",
        "floor_strike": 85000.0,
        "minutes_left": None,
    }
    base.update(overrides)
    return base


def px(live=85100.0, open15m=85000.0):
    return {"price": live, "open15m": open15m, "delta": abs(open15m - live),
            "open_source": "kalshi_floor_strike", "spot_source": "test"}


def msgs(events):
    return [e["msg"] for e in events]


class TestConfig(unittest.TestCase):
    def test_shipped_file_matches_the_pasted_script(self):
        loaded, errors = load_config(paper.DEFAULT_CONFIG_PATH)
        self.assertEqual(errors, [])
        self.assertEqual(loaded.initial_balance, 1000)
        self.assertEqual(loaded.time_delay, 8)
        self.assertEqual((loaded.entry_min, loaded.entry_max), (0.75, 0.85))
        self.assertEqual(loaded.exit, 0.40)
        self.assertEqual(loaded.take_profit_pts, 0.10)
        self.assertEqual(loaded.delta, 0)
        self.assertEqual(loaded.assets, ("BTC",))
        self.assertEqual(loaded.order_size, 1)
        self.assertEqual(loaded.pings, 1)
        self.assertEqual(loaded.daily_profit_target, 400)
        self.assertEqual(loaded.daily_loss_target, -600)

    def test_rejects_a_reversed_entry_range(self):
        with open(paper.DEFAULT_CONFIG_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
        raw["EntryRange"] = [0.85, 0.75]
        loaded, errors = parse_config(raw)
        self.assertIsNone(loaded)
        self.assertTrue(any("EntryRange" in line for line in errors))

    def test_rejects_an_unknown_asset_and_a_fractional_ping(self):
        with open(paper.DEFAULT_CONFIG_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
        raw["assets"] = ["AAPL"]
        raw["Pings"] = "1.5"
        _loaded, errors = parse_config(raw)
        self.assertTrue(any("assets" in line for line in errors))
        self.assertTrue(any("Pings" in line for line in errors))


class TestClockAndBook(unittest.TestCase):
    def test_quarter_clock(self):
        moment = datetime(2026, 10, 6, 20, 7, 30, tzinfo=timezone.utc)
        self.assertAlmostEqual(minutes_remaining_in_quarter(moment), 7.5)
        self.assertEqual(quarter_index(moment), (20 * 60 + 7) // 15)
        self.assertEqual(quarter_index(datetime(2026, 10, 6, 20, 15, tzinfo=timezone.utc)),
                         quarter_index(moment) + 1)

    def test_orderbook_implied_asks(self):
        body = {"orderbook_fp": {
            "yes_dollars": [["0.4400", "10"]],
            "no_dollars": [["0.5500", "10"]],
        }}
        self.assertAlmostEqual(best_bid(body, "yes_dollars"), 0.44)
        self.assertAlmostEqual(best_bid(body, "no_dollars"), 0.55)
        market = snap()
        self.assertTrue(apply_orderbook(market, body))
        self.assertAlmostEqual(market["up"], round(1 - 0.55, 4))
        self.assertAlmostEqual(market["down"], round(1 - 0.44, 4))

    def test_legacy_cents_book(self):
        body = {"orderbook": {"yes": [[44, 10]], "no": [[55, 10]]}}
        self.assertAlmostEqual(best_bid(body, "yes_dollars"), 0.44)
        self.assertAlmostEqual(best_bid(body, "no_dollars"), 0.55)

    def test_pick_market_skips_the_last_30_seconds(self):
        now = NOW
        stale = {"ticker": "OLD", "close_time": "2026-10-06T20:10:20Z"}
        fresh = {"ticker": "NEW", "close_time": CLOSE}
        self.assertEqual(pick_market([stale, fresh], now)["ticker"], "NEW")
        self.assertIsNone(pick_market([stale], now))

    def test_compose_price_prefers_the_contract_strike(self):
        view = compose_price(
            {"floor_strike": 85618},
            {"price": 85592, "exchange_open": 85610, "source": "coinbase"})
        self.assertEqual(view["open15m"], 85618)
        self.assertEqual(view["open_source"], "kalshi_floor_strike")
        self.assertAlmostEqual(view["delta"], 26)
        self.assertEqual(view["spot_source"], "coinbase")

    def test_missing_spot_has_no_delta(self):
        view = compose_price({"floor_strike": 85618}, None)
        self.assertIsNone(view["delta"])

    def test_side_choice(self):
        self.assertEqual(choose_side(0.84, 0.80, 0.75, 0.85, 10, 0)[:2], ("UP", 0.84))
        self.assertEqual(choose_side(0.76, 0.84, 0.75, 0.85, 10, 0)[:2], ("DOWN", 0.84))
        self.assertEqual(choose_side(0.75, 0.10, 0.75, 0.85, 0, 0)[0], "UP")
        self.assertEqual(choose_side(0.85, 0.10, 0.75, 0.85, 0, 0)[0], "UP")
        self.assertEqual(choose_side(0.74, 0.86, 0.75, 0.85, 10, 0)[2], "out_of_range")
        self.assertEqual(choose_side(0.80, 0.20, 0.75, 0.85, None, 0)[2], "delta_low")
        self.assertEqual(choose_side(0.80, 0.20, 0.75, 0.85, 5, 10)[2], "delta_low")
        self.assertEqual(choose_side(None, 0.80, 0.75, 0.85, 1, 0)[0], "DOWN")


class TestEngine(unittest.TestCase):
    def test_first_poll_announces_the_session_and_does_not_buy(self):
        engine = PaperEngine(cfg())
        events = engine.tick("BTC", snap(), px(), NOW)
        self.assertIn("New Kalshi paper session", msgs(events)[0])
        self.assertEqual(engine.positions, {})
        self.assertEqual(engine.balance, 1000)

    def test_second_poll_inside_the_window_buys_up(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        events = engine.tick("BTC", snap(), px(), NOW)
        self.assertEqual(engine.positions["BTC"]["side"], "UP")
        self.assertAlmostEqual(engine.positions["BTC"]["entry"], 0.80)
        self.assertAlmostEqual(engine.balance, 999.20)
        self.assertTrue(any("filled @ 0.80" in line for line in msgs(events)))
        self.assertTrue(any("take-profit >= 0.90" in line for line in msgs(events)))

    def test_outside_the_window_does_not_buy(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), EARLY)
        events = engine.tick("BTC", snap(), px(), EARLY)
        self.assertEqual(engine.positions, {})
        self.assertTrue(any("Waiting for last 8 minutes" in line for line in msgs(events)))

    def test_out_of_range_and_missing_delta_do_not_repeat(self):
        engine = PaperEngine(cfg(delta=50))
        engine.tick("BTC", snap(up=0.50, down=0.50), px(), NOW)
        first = engine.tick("BTC", snap(up=0.50, down=0.50), px(), NOW)
        second = engine.tick("BTC", snap(up=0.50, down=0.50), px(), NOW)
        self.assertTrue(any("outside entry range" in line for line in msgs(first)))
        self.assertEqual(second, [])

        engine = PaperEngine(cfg(delta=50))
        engine.tick("BTC", snap(), px(live=85010, open15m=85000), NOW)
        low = engine.tick("BTC", snap(), px(live=85010, open15m=85000), NOW)
        self.assertTrue(any("skipping entry" in line for line in msgs(low)))
        self.assertEqual(engine.positions, {})

    def test_stop_confirms_on_a_ping_and_then_waits(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        sleeps = []

        def refetch(_asset):
            return snap(up=0.25, down=0.70)

        events = engine.tick("BTC", snap(up=0.30, down=0.70), px(), NOW,
                             refetch=refetch, sleep=lambda seconds: sleeps.append(seconds))
        self.assertEqual(sleeps, [1])
        self.assertNotIn("BTC", engine.positions)
        self.assertEqual(engine.phase["BTC"], "STOPPED_OUT")
        self.assertAlmostEqual(engine.balance, 999.45)
        self.assertEqual(engine.losses, 1)
        self.assertTrue(any("Stop-loss confirmed @ 0.25" in line for line in msgs(events)))
        later = engine.tick("BTC", snap(), px(), NOW)
        self.assertEqual(later, [])
        self.assertEqual(engine.positions, {})

    def test_stop_ping_rebound_keeps_the_position(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)

        def refetch(_asset):
            return snap(up=0.55)

        engine.tick("BTC", snap(up=0.30), px(), NOW, refetch=refetch, sleep=lambda _s: None)
        self.assertEqual(engine.positions["BTC"]["side"], "UP")
        self.assertAlmostEqual(engine.positions["BTC"]["last_mark"], 0.55)
        self.assertEqual(engine.trade_count, 0)

    def test_zero_pings_exits_without_a_refetch(self):
        engine = PaperEngine(cfg(pings=0))
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)

        def refetch(_asset):
            raise AssertionError("ping refetch")

        engine.tick("BTC", snap(up=0.30), px(), NOW, refetch=refetch, sleep=lambda _s: None)
        self.assertEqual(engine.phase["BTC"], "STOPPED_OUT")
        self.assertAlmostEqual(engine.trades[0]["exit"], 0.30)
        self.assertAlmostEqual(engine.trades[0]["pnl"], -0.50)

    def test_price_sitting_on_the_exit_does_not_stop(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(up=0.40), px(), NOW, refetch=lambda _a: (_ for _ in ()).throw(AssertionError("ping")))
        self.assertIn("BTC", engine.positions)

    def test_take_profit(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        events = engine.tick("BTC", snap(up=0.90), px(), NOW)
        self.assertEqual(engine.phase["BTC"], "TOOK_PROFIT")
        self.assertAlmostEqual(engine.trades[0]["pnl"], 0.10)
        self.assertAlmostEqual(engine.balance, 1000.10)
        self.assertEqual(engine.wins, 1)
        self.assertTrue(any("Take-profit triggered @ 0.90" in line for line in msgs(events)))

    def test_target_above_one_holds(self):
        engine = PaperEngine(cfg(take_profit_pts=0.30))
        engine.tick("BTC", snap(), px(), NOW)
        opened = engine.tick("BTC", snap(), px(), NOW)
        self.assertTrue(any("no TP exit" in line for line in msgs(opened)))
        engine.tick("BTC", snap(up=0.99), px(), NOW)
        self.assertIn("BTC", engine.positions)
        self.assertEqual(engine.trade_count, 0)

    def test_blank_side_price_does_not_stop_at_zero(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        events = engine.tick("BTC", snap(up=None), px(), NOW)
        self.assertIn("BTC", engine.positions)
        self.assertEqual(engine.trade_count, 0)
        self.assertTrue(any("not treating it as 0" in line for line in msgs(events)))

    def test_insufficient_cash_stops_the_process(self):
        engine = PaperEngine(cfg(initial_balance=0.50))
        engine.tick("BTC", snap(), px(), NOW)
        events = engine.tick("BTC", snap(), px(), NOW)
        self.assertTrue(engine.halted)
        self.assertEqual(engine.halt_reason, "insufficient_balance")
        self.assertEqual(engine.positions, {})
        self.assertAlmostEqual(engine.balance, 0.50)
        self.assertTrue(any("Stopping script." in line for line in msgs(events)))

    def test_ticker_roll_holds_for_settlement_and_the_next_window_can_enter(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        rolled = engine.tick("BTC", snap(market_ticker="KXBTC15M-NEXT"), px(), NOW)
        self.assertNotIn("BTC", engine.positions)
        self.assertIn("KXBTC15M-T", engine.pending)
        self.assertTrue(any("held to resolution" in line for line in msgs(rolled)))
        engine.tick("BTC", snap(market_ticker="KXBTC15M-NEXT"), px(), NOW)
        engine.tick("BTC", snap(market_ticker="KXBTC15M-NEXT"), px(), NOW)
        self.assertEqual(engine.positions["BTC"]["ticker"], "KXBTC15M-NEXT")
        self.assertFalse(engine.check_daily([]))

    def test_quarter_change_also_holds(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        later = datetime(2026, 10, 6, 20, 16, tzinfo=timezone.utc)
        engine.tick("BTC", snap(), px(), later)
        self.assertIn("KXBTC15M-T", engine.pending)

    def test_settlement_yes_and_no_for_each_side(self):
        cases = [("UP", "yes", 1.0), ("UP", "no", 0.0), ("DOWN", "yes", 0.0), ("DOWN", "no", 1.0)]
        for side, result, settle in cases:
            engine = PaperEngine(cfg())
            up, down = (0.80, 0.20) if side == "UP" else (0.20, 0.80)
            market = snap(up=up, down=down)
            engine.tick("BTC", market, px(), NOW)
            engine.tick("BTC", market, px(), NOW)
            self.assertEqual(engine.positions["BTC"]["side"], side)
            engine.tick("BTC", snap(market_ticker="NEXT"), px(), NOW)
            events = engine.process_settlements(lambda _ticker: result, NOW, mono=0)
            self.assertEqual(engine.pending, {})
            self.assertAlmostEqual(engine.trades[-1]["exit"], settle)
            self.assertAlmostEqual(engine.trades[-1]["pnl"], settle - 0.80)
            self.assertTrue(any("settlement confirmed" in line for line in msgs(events)))

    def test_settlement_is_throttled_for_two_seconds(self):
        engine = PaperEngine(cfg())
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(market_ticker="NEXT"), px(), NOW)
        calls = []

        def fetch(_ticker):
            calls.append(_ticker)
            return ""

        engine.process_settlements(fetch, NOW, mono=10)
        engine.process_settlements(fetch, NOW, mono=11)
        engine.process_settlements(fetch, NOW, mono=12)
        self.assertEqual(calls, ["KXBTC15M-T", "KXBTC15M-T"])
        self.assertIn("KXBTC15M-T", engine.pending)

    def test_daily_targets_fire_only_when_flat(self):
        engine = PaperEngine(cfg(daily_profit_target=0.19))
        engine.positions["BTC"] = {
            "side": "UP", "entry": 0.50, "size": 1, "last_mark": 1.0, "ticker": "T",
            "quarter_index": quarter_index(NOW),
        }
        self.assertFalse(engine.check_daily([]))
        self.assertFalse(engine.halted)

        engine = PaperEngine(cfg(daily_profit_target=0.19))
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(market_ticker="NEXT"), px(), NOW)
        events = engine.process_settlements(lambda _t: "yes", NOW, mono=0)
        self.assertTrue(engine.halted)
        self.assertEqual(engine.halt_reason, "daily_profit")
        self.assertTrue(any("Daily profit target reached" in line for line in msgs(events)))

        engine = PaperEngine(cfg(daily_loss_target=-0.79))
        down = snap(up=0.20, down=0.80)
        engine.tick("BTC", down, px(), NOW)
        engine.tick("BTC", down, px(), NOW)
        engine.tick("BTC", snap(market_ticker="NEXT", up=0.20, down=0.80), px(), NOW)
        events = engine.process_settlements(lambda _t: "yes", NOW, mono=0)
        self.assertEqual(engine.halt_reason, "daily_loss")
        self.assertTrue(any("Daily loss target reached" in line for line in msgs(events)))

    def test_daily_target_uses_a_strict_comparison(self):
        engine = PaperEngine(cfg(daily_profit_target=0.20))
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(market_ticker="NEXT"), px(), NOW)
        engine.process_settlements(lambda _t: "yes", NOW, mono=0)
        self.assertFalse(engine.halted)
        self.assertAlmostEqual(engine.live_pnl(), 0.20)

    def test_zero_pnl_counts_as_a_loss(self):
        engine = PaperEngine(cfg())
        engine._record("BTC", "UP", 0.50, 0.50, 1, "flat", NOW, [])
        self.assertEqual(engine.wins, 0)
        self.assertEqual(engine.losses, 1)

    def test_assets_do_not_share_a_position(self):
        engine = PaperEngine(cfg(assets=("BTC", "ETH")))
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("BTC", snap(), px(), NOW)
        engine.tick("ETH", snap(market_ticker="KXETH15M-T"), px(), NOW)
        self.assertIn("BTC", engine.positions)
        self.assertNotIn("ETH", engine.positions)


class TestClientCache(unittest.TestCase):
    def _client(self, getter, sleeper=None):
        client = KalshiClient(5000, 1500, sleeper=sleeper or (lambda _s: None))
        client._get = getter
        return client

    def test_market_list_is_cached_and_the_book_is_not(self):
        market = {
            "ticker": "KXBTC15M-T",
            "yes_ask_dollars": "0.8000",
            "no_ask_dollars": "0.2100",
            "close_time": CLOSE,
            "floor_strike": 85000,
        }
        book = {"orderbook_fp": {"yes_dollars": [["0.2000", "1"]], "no_dollars": [["0.1900", "1"]]}}
        calls = []

        def getter(path, params=None):
            calls.append(path)
            if path.endswith("/orderbook"):
                return book
            return {"markets": [market]}

        client = self._client(getter)
        first = client.snapshot("BTC", NOW)
        second = client.snapshot("BTC", NOW)
        self.assertAlmostEqual(first["up"], round(1 - 0.19, 4))
        self.assertAlmostEqual(second["down"], round(1 - 0.20, 4))
        self.assertEqual(calls, [
            "/markets",
            "/markets/KXBTC15M-T/orderbook",
            "/markets/KXBTC15M-T/orderbook",
        ])

    def test_a_failed_book_on_a_fresh_cache_does_not_reuse_quotes(self):
        market = {"ticker": "KXBTC15M-T", "yes_ask_dollars": "0.8000",
                  "no_ask_dollars": "0.2100", "close_time": CLOSE, "floor_strike": 85000}
        book = {"orderbook_fp": {"yes_dollars": [["0.2000", "1"]], "no_dollars": [["0.1900", "1"]]}}
        state = {"book": book}

        def getter(path, params=None):
            if path.endswith("/orderbook"):
                return state["book"]
            return {"markets": [market]}

        client = self._client(getter)
        self.assertIsNotNone(client.snapshot("BTC", NOW))
        state["book"] = None
        self.assertIsNone(client.snapshot("BTC", NOW))

    def test_expiring_cache_is_replaced(self):
        fresh = {"ticker": "NEW", "yes_ask_dollars": "0.8000", "no_ask_dollars": "0.2000",
                 "close_time": CLOSE, "floor_strike": 85000}
        calls = []

        def getter(path, params=None):
            calls.append(path)
            if path.endswith("/orderbook"):
                return {"orderbook_fp": {"yes_dollars": [["0.2000", "1"]], "no_dollars": [["0.1900", "1"]]}}
            return {"markets": [fresh]}

        client = self._client(getter)
        client._meta["BTC"] = {
            "up": 0.5, "down": 0.5, "close_time": "2026-10-06T20:09:00Z",
            "market_ticker": "OLD", "floor_strike": 1, "minutes_left": -1,
        }
        client._meta_at["BTC"] = time.monotonic()
        got = client.snapshot("BTC", NOW)
        self.assertEqual(got["market_ticker"], "NEW")
        self.assertIn("/markets", calls)

    def test_discover_retries_a_dying_market(self):
        stale = {"ticker": "OLD", "close_time": "2026-10-06T20:10:10Z",
                 "yes_ask_dollars": "0.8000", "no_ask_dollars": "0.2000"}
        fresh = {"ticker": "NEW", "close_time": CLOSE,
                 "yes_ask_dollars": "0.8000", "no_ask_dollars": "0.2000", "floor_strike": 85000}
        state = {"n": 0}
        sleeps = []

        def getter(path, params=None):
            if path.endswith("/orderbook"):
                return {"orderbook_fp": {"yes_dollars": [["0.2000", "1"]], "no_dollars": [["0.1900", "1"]]}}
            state["n"] += 1
            return {"markets": [stale]} if state["n"] < 3 else {"markets": [fresh]}

        client = self._client(getter, sleeper=lambda seconds: sleeps.append(seconds))
        got = client.snapshot("BTC", NOW)
        self.assertEqual(got["market_ticker"], "NEW")
        self.assertEqual(sleeps, [0.2, 0.2])


class TestRunner(unittest.TestCase):
    def test_module_does_not_define_an_order_call(self):
        src = inspect.getsource(paper)
        self.assertNotIn("requests.post", src)
        self.assertNotIn("requests.put", src)
        self.assertNotIn("create_order", src)
        self.assertNotIn("/portfolio/orders", src)

    def test_once_writes_a_log_and_does_not_need_a_host(self):
        class Fake:
            def __init__(self):
                self._meta = {}
                self._spot = {}

            def snapshot(self, asset, now=None):
                market = snap()
                self._meta[asset] = market
                return market

            def spot(self, asset, now_ts=None):
                self._spot[asset] = {"price": 85100, "exchange_open": 85000, "source": "fake"}
                return self._spot[asset]

            def result(self, ticker):
                raise AssertionError("no settlement on an empty book")

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                code = run(
                    cfg(), once=True, client=Fake(),
                    log_path=os.path.join(tmp, "events.jsonl"),
                    status_path=os.path.join(tmp, "status.json"),
                    sleep=lambda _s: None,
                    now_fn=lambda: EARLY,
                )
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "events.jsonl"), encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("No Nightshark host", text)
            self.assertIn("no orders", text)
            with open(os.path.join(tmp, "status.json"), encoding="utf-8") as handle:
                status = json.load(handle)
            self.assertIn("Waiting for last 8 minutes", text)
            self.assertEqual(status["trades"], 0)
            self.assertAlmostEqual(status["balance"], 1000)

    def test_once_inside_the_window_can_buy(self):
        class Fake:
            def __init__(self):
                self._meta = {}
                self._spot = {}

            def snapshot(self, asset, now=None):
                market = snap()
                self._meta[asset] = market
                return market

            def spot(self, asset, now_ts=None):
                self._spot[asset] = {"price": 85100, "exchange_open": 85000, "source": "fake"}
                return self._spot[asset]

            def result(self, ticker):
                return ""

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                code = run(
                    cfg(), once=True, client=Fake(),
                    log_path=os.path.join(tmp, "events.jsonl"),
                    status_path=os.path.join(tmp, "status.json"),
                    sleep=lambda _s: (_ for _ in ()).throw(AssertionError("sleep")),
                    now_fn=lambda: NOW,
                )
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "status.json"), encoding="utf-8") as handle:
                status = json.load(handle)
            self.assertEqual(status["trades"], 0)
            self.assertEqual(status["assets"]["BTC"]["phase"], "IN_POSITION")
            self.assertAlmostEqual(status["balance"], 999.20)


if __name__ == "__main__":
    unittest.main()
