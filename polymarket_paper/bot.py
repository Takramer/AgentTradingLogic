"""One paper account, one position per asset.

The step order matches the published script. A new session settles the old
contract before it is allowed to buy. A stop or a take-profit spends the
rest of that session flat. The first look at a session may buy on that same
tick; a later session change waits until the next tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from polymarket_paper.rules import (
    Account,
    HaltTrading,
    Position,
    cash,
    entry_decision,
    exit_decision,
    fmt_delta,
    fmt_pnl,
    fmt_px,
    fmt_size,
    in_entry_window,
    position_session_ended,
    resolution_price,
)

HEARTBEAT_SEC = 15


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class AssetState:
    phase: str = "WAIT_WINDOW"
    session_key: str | None = None
    open_price: float | None = None
    position: Position | None = None
    heartbeat_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "session_key": self.session_key,
            "open_price": self.open_price,
            "position": None if self.position is None else self.position.to_dict(),
            "heartbeat_at": self.heartbeat_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "AssetState":
        state = cls(
            phase=raw.get("phase") or "WAIT_WINDOW",
            session_key=raw.get("session_key"),
            open_price=None if raw.get("open_price") in (None, "") else float(raw["open_price"]),
            heartbeat_at=float(raw.get("heartbeat_at") or 0),
        )
        if raw.get("position"):
            state.position = Position.from_dict(raw["position"])
        return state


class PaperBot:
    def __init__(self, settings, feed, account: Account | None = None,
                 books: dict[str, AssetState] | None = None):
        self.settings = settings
        self.feed = feed
        self.account = account or Account(settings.initial_balance, settings.initial_balance)
        self.books = books or {asset: AssetState() for asset in settings.assets}
        for asset in settings.assets:
            self.books.setdefault(asset, AssetState())
        self.once: dict[str, bool] = {}
        self._account_at = -1e18
        self.saw_snapshot = False

    def step(self, now: float) -> list[str]:
        logs: list[str] = []
        self.saw_snapshot = False
        for asset in self.settings.assets:
            self._step_asset(asset, now, logs)
        self._status(now, logs, force=False)
        return logs

    def export_state(self) -> dict:
        return {
            "version": 1,
            "fingerprint": self.settings.fingerprint(),
            "account": self.account.to_dict(),
            "books": {asset: book.to_dict() for asset, book in self.books.items()},
        }

    @classmethod
    def from_state(cls, settings, feed, payload: dict) -> "PaperBot":
        if payload.get("version") != 1:
            raise ValueError("paper state version is not 1")
        books = {
            asset: AssetState.from_dict(raw)
            for asset, raw in (payload.get("books") or {}).items()
        }
        return cls(settings, feed, Account.from_dict(payload["account"]), books)

    def _step_asset(self, asset: str, now: float, logs: list[str]) -> None:
        book = self.books[asset]
        snap = self.feed.snapshot(asset, now)
        if snap is None:
            if book.position is not None and position_session_ended(book.position, now):
                self._settle(asset, book, now, logs)
            window = int(now // self.settings.timeframe_seconds)
            self._once(
                asset, f"NO_DATA_{window}",
                f"{asset} Waiting for Polymarket market data", now, logs,
            )
            return

        self.saw_snapshot = True
        if book.position is not None and position_session_ended(
            book.position, now, ticker=snap.market_ticker, session_key=snap.session_key,
        ):
            self._settle(asset, book, now, logs)

        if book.session_key is None:
            if not self._begin_session(asset, book, snap, now, logs, rolled=False):
                return
        elif book.session_key != snap.session_key:
            self._begin_session(asset, book, snap, now, logs, rolled=True)
            return
        elif book.open_price is None:
            if self._load_open(asset, book, snap) is None:
                self._once(asset, "WAIT_OPEN_PRICE", f"{asset} Waiting for {asset} OPEN", now, logs)
                return
            self._say(now, f"{asset} OPEN : {book.open_price:.2f}", logs)

        if book.position is not None:
            self._manage(asset, book, snap, now, logs)
            return

        if book.phase in ("STOPPED_OUT", "TOOK_PROFIT", "BUY_FAILED"):
            self._once(asset, book.phase, self._phase_message(asset, book.phase), now, logs)
            return

        if not in_entry_window(snap.minutes_left, self.settings.time_delay):
            book.phase = "WAIT_WINDOW"
            if snap.minutes_left <= 0:
                self._once(
                    asset, "SESSION_ENDED",
                    f"{asset} End of session, waiting for new Polymarket market", now, logs,
                )
            else:
                delay = f"{self.settings.time_delay:g}"
                self._once(
                    asset, "WAIT_WINDOW",
                    f"{asset} Waiting for last {delay} minutes", now, logs,
                )
            self._heartbeat(asset, book, snap, now, logs, delta=None, source=None)
            return

        if book.phase != "MONITORING":
            book.phase = "MONITORING"
            self._say(
                now,
                f"{asset} Monitoring Polymarket entry range "
                f"[{fmt_px(self.settings.entry_min)}, {fmt_px(self.settings.entry_max)}] "
                f"| Delta >= {self.settings.delta:g}",
                logs,
            )

        live_delta, source = self._live_delta(asset, book)
        self._heartbeat(asset, book, snap, now, logs, delta=live_delta, source=source)
        chosen, reason = entry_decision(
            snap.up, snap.down, self.settings.entry_min, self.settings.entry_max,
            live_delta, self.settings.delta,
        )
        if reason == "PRICE_OUT_OF_RANGE":
            self._once(
                asset, reason,
                f"{asset} UP {fmt_px(snap.up)} / DOWN {fmt_px(snap.down)} outside entry range "
                f"[{fmt_px(self.settings.entry_min)}, {fmt_px(self.settings.entry_max)}]",
                now, logs,
            )
            return
        if reason == "DELTA_LOW":
            self._once(
                asset, reason,
                f"{asset} LiveDelta {fmt_delta(live_delta)} < Delta {self.settings.delta:g}, skipping entry",
                now, logs,
            )
            return
        side, price = chosen
        self._buy(asset, book, snap, side, price, now, logs)

    def _begin_session(self, asset, book, snap, now, logs, rolled: bool) -> bool:
        book.session_key = snap.session_key
        book.phase = "WAIT_WINDOW"
        book.open_price = None
        book.heartbeat_at = 0
        self._reset_once(asset)
        opened = self._load_open(asset, book, snap)
        label = "New Polymarket session" if rolled else "Polymarket session"
        if opened is None:
            self._say(
                now,
                f"{asset} {label} ({snap.market_ticker}) | waiting for {asset} OPEN",
                logs,
            )
            return False
        self._say(
            now,
            f"{asset} {label} ({snap.market_ticker}) | {asset} OPEN : {opened:.2f}",
            logs,
        )
        return True

    def _load_open(self, asset, book, snap):
        price = self.feed.open_price(asset, snap)
        if price is None:
            return None
        book.open_price = float(price)
        return book.open_price

    def _live_delta(self, asset, book):
        if book.open_price is None:
            return None, None
        live = self.feed.live_price(asset)
        if live is None:
            return None, None
        price, source = live
        return abs(book.open_price - float(price)), source

    def _manage(self, asset, book, snap, now, logs) -> None:
        pos = book.position
        current = snap.up if pos.side == "UP" else snap.down
        pos.last_mark = current
        book.phase = "IN_POSITION"
        decision = exit_decision(current, self.settings.exit, pos.take_profit_target)
        live_delta, source = self._live_delta(asset, book)
        self._heartbeat(asset, book, snap, now, logs, delta=live_delta, source=source)
        if decision is None:
            return
        if decision == "stop":
            self._say(
                now,
                f"{asset} Stop-loss triggered @ {fmt_px(current)} < {fmt_px(self.settings.exit)}",
                logs,
            )
            reason = f"exit_below_{fmt_px(self.settings.exit)}"
            phase = "STOPPED_OUT"
        else:
            self._say(
                now,
                f"{asset} Take-profit triggered @ {fmt_px(current)} >= "
                f"{fmt_px(pos.take_profit_target)} "
                f"(fill {fmt_px(pos.entry)} + {fmt_px(self.settings.take_profit_pts)})",
                logs,
            )
            reason = f"take_profit_at_{fmt_px(pos.take_profit_target)}"
            phase = "TOOK_PROFIT"
        self._close(asset, pos, current, reason, now, logs)
        book.position = None
        book.phase = phase
        self._say(now, f"{asset} Position closed", logs)
        self._once(asset, phase, self._phase_message(asset, phase), now, logs)

    def _buy(self, asset, book, snap, side, price, now, logs) -> None:
        size = self.settings.order_size
        self._say(now, f"{asset} {side} triggered @ {fmt_px(price)} - placing paper order", logs)
        ok, cost = self.account.buy(price, size)
        if not ok:
            self._say(
                now,
                f"{asset} Paper buy rejected: cost {cost:.2f} exceeds balance {self.account.balance:.2f}",
                logs,
            )
            self._say(now, "Not enough balance left to place trades. Stopping script.", logs)
            raise HaltTrading(logs)
        self._say(
            now,
            f"{asset} Paper {side} filled @ {fmt_px(price)} | size {fmt_size(size)} | "
            f"cost {cost:.2f} | balance {self.account.balance:.2f}",
            logs,
        )
        target = cash(price + self.settings.take_profit_pts)
        token_id = snap.up_token_id if side == "UP" else snap.down_token_id
        book.position = Position(
            side=side,
            entry=float(price),
            take_profit_target=target,
            ticker=snap.market_ticker,
            token_id=token_id,
            session_key=snap.session_key,
            start_sec=snap.start_sec,
            end_sec=snap.end_sec,
            size=size,
            last_mark=float(price),
        )
        book.phase = "IN_POSITION"
        self._say(
            now,
            f"{asset} Paper position confirmed | fill: {fmt_px(price)} | size: {fmt_size(size)}",
            logs,
        )
        if target > 1:
            self._say(
                now,
                f"{asset} TP target {fmt_px(target)} > 1; no TP exit, hold for resolution",
                logs,
            )
        else:
            self._say(
                now,
                f"{asset} Monitoring stop-loss < {fmt_px(self.settings.exit)} | "
                f"take-profit >= {fmt_px(target)} (fill + {fmt_px(self.settings.take_profit_pts)})",
                logs,
            )
        self._status(now, logs, force=True)

    def _close(self, asset, pos, exit_price, reason, now, logs) -> None:
        pnl = self.account.close(
            asset, pos.side, pos.entry, exit_price, pos.size, reason, now,
        )
        self._say(
            now,
            f"{asset} Paper {pos.side} closed @ {fmt_px(exit_price)} | entry {fmt_px(pos.entry)} | "
            f"size {fmt_size(pos.size)} | PnL {fmt_pnl(pnl)} | balance {self.account.balance:.2f} | {reason}",
            logs,
        )
        self._status(now, logs, force=True)

    def _settle(self, asset, book, now, logs) -> None:
        pos = book.position
        if pos is None:
            return
        last = pos.last_mark
        settle = resolution_price(last)
        self._say(
            now,
            f"{asset} {pos.side} held to resolution | settled at {settle:g} (last price {fmt_px(last)})",
            logs,
        )
        self._close(asset, pos, settle, "held_to_resolution", now, logs)
        book.position = None
        book.phase = "WAIT_WINDOW"

    def _heartbeat(self, asset, book, snap, now, logs, delta, source) -> None:
        if book.heartbeat_at and now - book.heartbeat_at < HEARTBEAT_SEC:
            return
        book.heartbeat_at = now
        live = self.feed.live_price(asset)
        live_txt = "n/a" if live is None else f"{live[0]:.2f} {live[1]}"
        open_txt = "n/a" if book.open_price is None else f"{book.open_price:.2f}"
        line = (
            f"{asset} UP {fmt_px(snap.up)} | DOWN {fmt_px(snap.down)} | "
            f"OPEN {open_txt} | LIVE {live_txt} | DELTA {fmt_delta(delta)} | "
            f"{snap.minutes_left:.1f}m left | {book.phase}"
        )
        if book.position is not None:
            pos = book.position
            line += (
                f" | {pos.side} mark {fmt_px(pos.last_mark)} entry {fmt_px(pos.entry)}"
            )
        self._say(now, line, logs)

    def _status(self, now, logs, force: bool) -> None:
        if not force and now - self._account_at < HEARTBEAT_SEC:
            return
        self._account_at = now
        equity = self.account.equity(book.position for book in self.books.values())
        self._say(now, self.account.status_line(equity), logs)

    def _phase_message(self, asset: str, phase: str) -> str:
        if phase == "STOPPED_OUT":
            return f"{asset} Stopped out, waiting for next session"
        if phase == "TOOK_PROFIT":
            return f"{asset} Take profit completed, waiting for next session"
        return f"{asset} Paper buy failed, waiting for next session"

    def _once(self, asset, reason, message, now, logs) -> None:
        key = f"{asset}|{reason}"
        if key in self.once:
            return
        self.once[key] = True
        self._say(now, message, logs)

    def _reset_once(self, asset: str) -> None:
        prefix = asset + "|"
        for key in list(self.once):
            if key.startswith(prefix):
                del self.once[key]

    def _say(self, now, message, logs) -> None:
        logs.append(f"{_stamp(now)}Z  {message}")
