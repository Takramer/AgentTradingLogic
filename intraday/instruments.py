"""instruments.py — contract specs, the cost of one round trip, and the
broker's margin rule, for the broker this harness assumes.

**Assumed broker: Interactive Brokers (IBKR Pro, fixed).** Chosen 2026-10-27 as
the one retail futures broker an unattended program can run end to end: a free
Python API (TWS / IB Gateway) for orders and data, a paper account on the same
API, no separate API fee. Tradovate's day margins are lower ($50 MES), but its
API is a paid add-on and real-time API data may need a CME licence; NinjaTrader
automates only through its own desktop scripting; Robinhood's commission is
lower with Gold, but its agent account cannot trade futures.

Cost per side = IBKR commission + CME exchange/clearing + NFA, plus slippage in
ticks against the bar price the engine fills at. **Every number is ASSUMED**
from IBKR's published rate (as reported by its regional site and vendor
summaries) and broker-reported CME fees, not from executed fills. Where sources
disagreed, the higher figure was used. Do not lower a number without fill
evidence; raising one is always allowed.

MES at these settings: 2 x (0.89 + 1 tick x $1.25) = **$4.28 per round trip**,
or 0.856 index points. Every rule must earn more than that per trade, on
average, before it earns anything.

IBKR rules the backtest respects:

* **Intraday margin.** IBKR needs the intraday initial margin free to open a
  position and moves to the higher overnight margin if a position is still open
  at the contract's intraday end time. `intraday_margin` x contracts must fit
  inside the backtest's capital, or `backtest()` refuses to run. Being flat every
  session (the engine's rule) keeps overnight margin out of it.
* **No pattern-day-trader rule** — that applies to securities, not futures.
* **No margin call.** IBKR liquidates automatically, at its choice of price,
  when equity falls below maintenance. Size so a stop-out never gets near it.
* Futures need a margin account; cash and IRA accounts get no intraday discount.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Instrument:
    symbol: str
    tick_size: float
    #: USD per 1.0 price point, per contract (or per share).
    point_value: float
    #: USD per contract per side: commission + exchange + regulatory.
    fee_per_side: float
    #: Ticks lost per side versus the bar price used as the fill.
    slippage_ticks: float
    cost_note: str = ""
    #: USD the broker requires free per contract to open a position intraday.
    intraday_margin: float = 0.0
    #: Fees charged as a fraction of notional, averaged per side (SEC fee on sales).
    fee_rate: float = 0.0
    #: Cash account: buys must use settled cash (see engine.backtest max_entries).
    settled_cash_only: bool = False
    #: No short selling (a cash account cannot borrow shares).
    long_only: bool = False

    def side_cost(self, qty: float = 1.0) -> float:
        return abs(qty) * (self.fee_per_side
                           + self.slippage_ticks * self.tick_size * self.point_value)

    def round_trip(self, qty: float = 1.0) -> float:
        return 2.0 * self.side_cost(qty)

    def trade_cost(self, qty: float, entry_px: float, exit_px: float) -> float:
        """Round-trip cost of one trade: per-unit fees and slippage both sides,
        plus notional-based fees on both legs' value."""
        return (self.round_trip(qty)
                + self.fee_rate * abs(qty) * self.point_value * (entry_px + exit_px))

    def stressed(self, slippage_mult: float = 2.0) -> "Instrument":
        return replace(self, slippage_ticks=self.slippage_ticks * slippage_mult)


#: Micro E-mini S&P 500. Tick 0.25 = $1.25. ASSUMED 2026-10-27, IBKR Pro fixed.
MES = Instrument("MES", 0.25, 5.0, 0.89, 1.0,
                 "ASSUMED 2026-10-27: IBKR 0.25 + CME exch/clearing ~0.62 + NFA 0.02 per side",
                 intraday_margin=2_500.0)  # reported ~$1,611 intraday; check in TWS

#: E-mini S&P 500. Tick 0.25 = $12.50. ASSUMED as above.
ES = Instrument("ES", 0.25, 50.0, 2.25, 1.0,
                "ASSUMED 2026-10-27: IBKR 0.85 + CME exch/clearing ~1.38 + NFA 0.02 per side",
                intraday_margin=25_000.0)  # ~10x MES; check in TWS


#: SPY shares in a Robinhood cash account (the agent account, 2026-10-27).
#: $0 commission. SEC fee $20.60 per $1M of sales (FY2026, from 2026-04-04) =
#: 0.103 bp per side averaged; FINRA TAF ~$0.000166/share on sales, rounded up
#: to $0.0001/share per side. Slippage: 1 cent per share per side against the
#: bar price (SPY's spread is usually 1 cent). ASSUMED, not from fills.
SPY_RH = Instrument("SPY", 0.01, 1.0, 0.0001, 1.0,
                    "ASSUMED 2026-10-27: Robinhood $0 commission, SEC 20.60/M on sales, "
                    "TAF, 1c slippage per side",
                    fee_rate=20.60e-6 / 2, settled_cash_only=True, long_only=True)

#: MES priced on SPY bars, for testing a futures rule without MES data.
#: 1 MES = 5 x S&P 500 ~= 50 SPY shares, so trade qty=50 and every cost is the
#: MES cost spread over 50 share-equivalents: $0.89 fee -> $0.0178, one 0.25-pt
#: tick ($1.25) -> 2.5 SPY cents, $2,500 margin -> $50. Long and short, no
#: entry cap. Same assumptions as MES above.
MES_ON_SPY = Instrument("MES~SPY", 0.01, 1.0, 0.89 / 50, 2.5,
                        "MES costs per SPY share-equivalent; trade qty=50 for one contract",
                        intraday_margin=2_500.0 / 50)


def equity(symbol: str, slippage_cents: float = 1.0, fee_per_share: float = 0.0) -> Instrument:
    """A share-quantity instrument (qty = shares). One cent of slippage per side
    is optimistic for anything less liquid than SPY/QQQ."""
    return Instrument(symbol, 0.01, 1.0, fee_per_share, slippage_cents,
                      f"ASSUMED: {slippage_cents} cent(s) slippage per side")
