"""lrs.py — the LRS-2x book: 2x S&P 500 above its 200-day average, T-bills below.

Evidence and its limits: docs/METHODOLOGY-2026-11.md, scripts/lrs_check.py,
`strategies.json` -> books.lrs2x.

Emits the same payload shape as `live.emit` so one executor can consume both,
and inherits its arming rule unchanged: orders are permitted only when the
book's status is exactly "autonomous". This module never places an order.

    .venv/bin/python -m rhengine.alpha.lrs                          # target only
    .venv/bin/python -m rhengine.alpha.lrs --positions pos.json     # + deltas
    .venv/bin/python -m rhengine.alpha.lrs --live-price 761.08      # 15:15 ET rule

--live-price is the SPY last trade the executor read from the Robinhood MCP at
15:15 ET. It stands in for today's close, and the trade happens then, not at
tomorrow's close (scripts/lrs_1515_check.py: 19.14% CAGR, -37.4% max DD vs
18.01%, -43.1% for next-close, 2016-07..2026-09).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd

from . import live


def signal(adj: pd.Series, sma: int = 200) -> bool | None:
    """True when the last close is above its `sma`-session mean; None if too short."""
    adj = adj.dropna()
    if len(adj) < sma:
        return None
    return bool(adj.iloc[-1] > adj.iloc[-sma:].mean())


def emit_lrs(adj: pd.Series, positions: dict | None = None, asof: str | None = None,
             spec: dict | None = None, name: str = "lrs2x",
             live_price: float | None = None) -> dict:
    """Target book from SPY's adjusted closes (DatetimeIndex, New York dates).

    live_price: today's 15:15 ET SPY price. Appended as today's value, so the
    signal and the trade both happen today. `adj` must then end before today.
    """
    spec = spec or live._load(live._SPEC)
    bs = live.book_spec(name, spec)
    status = live.book_status(name, spec)
    sig = bs["signal"]
    adj = adj.dropna().sort_index()
    if live_price is not None:
        now = pd.Timestamp(asof or live._today_et()).normalize()
        if adj.index[-1].normalize() >= now:
            raise ValueError("live_price given but adj already has a bar for today")
        if not live_price > 0:
            raise ValueError(f"live_price must be positive, got {live_price}")
        adj = pd.concat([adj, pd.Series([float(live_price)], index=[now])])
    on = signal(adj, int(sig["sma_sessions"]))
    if on is None:
        raise live.StaleBook(f"{name}: fewer than {sig['sma_sessions']} closes")
    last = adj.index[-1]
    today = pd.Timestamp(asof) if asof else pd.Timestamp(live._today_et())
    age = (today.normalize() - last.normalize()).days
    limit = int(bs.get("kill_switches", {}).get("max_staleness_days",
                                                 live.MAX_STALENESS_DAYS))
    if age > limit:
        raise live.StaleBook(f"{name}: last {sig['ticker']} close is {last.date()}, "
                             f"{age} days before {today.date()} (limit {limit})")

    hold = bs["vehicles"]["risk_on" if on else "risk_off"]
    w = pd.Series({hold: 1.0})
    armed = status == "autonomous"
    payload = {
        "payload_version": live.PAYLOAD_VERSION,
        "book": name,
        "emitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config_hash": live.config_hash(),
        "status": status,
        "mode": "armed" if armed else "dry_run",
        "orders_permitted": armed,
        "decided_on": str(last.date()),
        "panel_last_bar": str(last.date()),
        "panel_age_days": int(age),
        "signal": {"ticker": sig["ticker"], "close": round(float(adj.iloc[-1]), 4),
                   "sma": round(float(adj.iloc[-int(sig["sma_sessions"]):].mean()), 4),
                   "risk_on": on},
        "execute_after": str(last.date()),
        "execution_rule": (
            "Execute NOW, at 15:15 ET on decided_on, with market orders "
            "(scripts/lrs_1515_check.py)." if live_price is not None else
            "Execute at the CLOSE of the first session that opens strictly "
            "after decided_on (the tested lag-2 rule)."),
        "constraints": {"long_only": True, "leverage_permitted": True,
                        "instruments": sorted(bs["vehicles"].values())},
        "executor_must": [
            "Refuse the whole payload if orders_permitted is false.",
            "Sell any holding not in weights before buying the target.",
            "Leave cash in cash if the target cannot be bought; never substitute "
            "another fund.",
        ],
        "evidence": bs.get("evidence"),
        "kill_switches": bs.get("kill_switches"),
        "overrides": ([f"ARMED. {bs['evidence']['_bound']}"] if armed else []),
        "n_names": 1,
        "weights": {hold: 1.0},
    }
    if positions is not None:
        pos, notes = live.clean_positions(positions, set(bs["vehicles"].values()))
        payload["positions"] = {k: round(v, 6) for k, v in pos.items()}
        payload["position_notes"] = notes
        payload["deltas"] = live._deltas(w, pos)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", help="JSON {ticker: weight of equity}")
    ap.add_argument("--live-price", type=float,
                    help="SPY last trade at 15:15 ET from the Robinhood MCP")
    ap.add_argument("--out")
    a = ap.parse_args()
    from ..data_local import fetch_yahoo
    bars = fetch_yahoo("SPY", lookback_days=400)
    adj = pd.Series([b["close"] for b in bars],
                    index=pd.to_datetime([b["time"] for b in bars], unit="ms")
                    .tz_localize("UTC").tz_convert("America/New_York")
                    .tz_localize(None).normalize())
    # Yahoo's last bar is LIVE while the session is open. A signal read from it
    # can flip back by the close, so today's bar counts only after 16:15 ET.
    now = pd.Timestamp.now(tz="America/New_York")
    if a.live_price is not None or now.hour * 60 + now.minute < 16 * 60 + 15:
        adj = adj[adj.index < now.tz_localize(None).normalize()]
    pos = json.load(open(a.positions)) if a.positions else None
    p = emit_lrs(adj, pos, live_price=a.live_price)
    text = json.dumps(p, indent=2)
    if a.out:
        open(a.out, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
