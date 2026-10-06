"""Pure paper rules from the published video script.

No network and no clock. The runner applies these in the same order as the
script: settle a dead session before managing the open contract, refuse a
second entry in a session that already stopped or took profit, and treat a
missing live price as a skip even when Delta is 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class HaltTrading(Exception):
    """The paper account cannot pay for another entry. The script stops."""

    def __init__(self, logs: list[str]):
        super().__init__("paper balance exhausted")
        self.logs = logs


def in_entry_window(minutes_left: float, time_delay: float) -> bool:
    return minutes_left > 0 and minutes_left <= time_delay


def entry_decision(up, down, entry_min, entry_max, live_delta, delta_min):
    """Pick UP or DOWN, or a skip reason.

    Both sides inside the range: take UP when up >= down, otherwise DOWN.
    LiveDelta must be a number and at least Delta. An empty live price skips,
    including when Delta is 0.
    """
    up_in = entry_min <= up <= entry_max
    down_in = entry_min <= down <= entry_max
    if not up_in and not down_in:
        return None, "PRICE_OUT_OF_RANGE"
    if live_delta is None or live_delta < delta_min:
        return None, "DELTA_LOW"
    if up_in and (not down_in or up >= down):
        return ("UP", float(up)), None
    return ("DOWN", float(down)), None


def exit_decision(current, stop, take_profit_target):
    """Stop is strict (<). Take-profit is inclusive, and only when the target is <= 1."""
    if current < stop:
        return "stop"
    if take_profit_target <= 1 and current >= take_profit_target:
        return "take_profit"
    return None


def resolution_price(last_mark: float) -> float:
    """The script's paper settlement: last mark >= 0.50 pays 1, otherwise 0.

    This is the video rule. It is not the Chainlink 60-second TWAP the
    market uses to resolve.
    """
    return 1.0 if last_mark >= 0.5 else 0.0


def position_session_ended(position, now: float, ticker: str | None = None,
                           session_key: str | None = None) -> bool:
    if ticker and position.ticker and ticker != position.ticker:
        return True
    if now >= position.end_sec:
        return True
    if session_key and position.session_key and session_key != position.session_key:
        return True
    return False


def fmt_px(value) -> str:
    return f"{float(value):.2f}"


def fmt_pnl(value) -> str:
    value = round(float(value) + 0.0, 2)
    if value > 0:
        return f"+{value:.2f}"
    return f"{value:.2f}"


def fmt_delta(value) -> str:
    if value is None:
        return "n/a"
    value = float(value)
    if abs(value) >= 1:
        return f"{value:.2f}"
    return f"{value:.4f}"


def cash(value) -> float:
    """USDC bookkeeping. Six decimals keeps binary dust out of the balance."""
    return round(float(value), 6)


def fmt_size(size) -> str:
    value = round(float(size) * 1_000_000) / 1_000_000
    text = f"{value:.6f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


@dataclass
class Position:
    side: str
    entry: float
    take_profit_target: float
    ticker: str
    token_id: str
    session_key: str
    start_sec: int
    end_sec: int
    size: float
    last_mark: float

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "entry": self.entry,
            "take_profit_target": self.take_profit_target,
            "ticker": self.ticker,
            "token_id": self.token_id,
            "session_key": self.session_key,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "size": self.size,
            "last_mark": self.last_mark,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Position":
        return cls(
            side=raw["side"],
            entry=float(raw["entry"]),
            take_profit_target=float(raw["take_profit_target"]),
            ticker=raw["ticker"],
            token_id=str(raw["token_id"]),
            session_key=raw["session_key"],
            start_sec=int(raw["start_sec"]),
            end_sec=int(raw["end_sec"]),
            size=float(raw["size"]),
            last_mark=float(raw["last_mark"]),
        )


@dataclass
class ClosedTrade:
    asset: str
    side: str
    entry: float
    exit: float
    size: float
    pnl: float
    reason: str
    ts: float

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "side": self.side,
            "entry": self.entry,
            "exit": self.exit,
            "size": self.size,
            "pnl": self.pnl,
            "reason": self.reason,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ClosedTrade":
        return cls(
            asset=raw["asset"],
            side=raw["side"],
            entry=float(raw["entry"]),
            exit=float(raw["exit"]),
            size=float(raw["size"]),
            pnl=float(raw["pnl"]),
            reason=raw["reason"],
            ts=float(raw["ts"]),
        )


@dataclass
class Account:
    initial: float
    balance: float
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    trades: int = 0
    closed: list[ClosedTrade] = field(default_factory=list)

    def buy(self, price: float, size: float) -> tuple[bool, float]:
        cost = cash(float(price) * float(size))
        if cost > self.balance:
            return False, cost
        self.balance = cash(self.balance - cost)
        return True, cost

    def close(self, asset: str, side: str, entry: float, exit_price: float,
              size: float, reason: str, ts: float) -> float:
        proceeds = cash(float(exit_price) * float(size))
        pnl = cash((float(exit_price) - float(entry)) * float(size))
        self.balance = cash(self.balance + proceeds)
        self.trades += 1
        if pnl > 0:
            self.wins += 1
            self.gross_profit = cash(self.gross_profit + pnl)
        else:
            self.losses += 1
            self.gross_loss = cash(self.gross_loss + abs(pnl))
        self.closed.append(ClosedTrade(
            asset=asset, side=side, entry=float(entry), exit=float(exit_price),
            size=float(size), pnl=pnl, reason=reason, ts=ts,
        ))
        if len(self.closed) > 200:
            self.closed = self.closed[-200:]
        return pnl

    def equity(self, positions) -> float:
        total = self.balance
        for pos in positions:
            if pos is None:
                continue
            total += pos.last_mark * pos.size
        return total

    def status_line(self, equity: float) -> str:
        pnl = equity - self.initial
        avg_win = f"{self.gross_profit / self.wins:.2f}" if self.wins else "n/a"
        avg_loss = f"{self.gross_loss / self.losses:.2f}" if self.losses else "n/a"
        return (
            f"CASH {self.balance:.2f} | EQUITY {equity:.2f} | PNL {fmt_pnl(pnl)} | "
            f"WINS {self.wins}/{self.trades} | AVG WIN {avg_win} | AVG LOSS {avg_loss}"
        )

    def to_dict(self) -> dict:
        return {
            "initial": self.initial,
            "balance": self.balance,
            "wins": self.wins,
            "losses": self.losses,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "trades": self.trades,
            "closed": [trade.to_dict() for trade in self.closed],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Account":
        account = cls(
            initial=float(raw["initial"]),
            balance=float(raw["balance"]),
            wins=int(raw.get("wins", 0)),
            losses=int(raw.get("losses", 0)),
            gross_profit=float(raw.get("gross_profit", 0)),
            gross_loss=float(raw.get("gross_loss", 0)),
            trades=int(raw.get("trades", 0)),
        )
        account.closed = [ClosedTrade.from_dict(item) for item in raw.get("closed", [])]
        return account
