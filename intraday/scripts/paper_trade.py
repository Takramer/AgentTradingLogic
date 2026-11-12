"""Paper-trade T10 forward: the noise-area rule with a VWAP trailing stop, long
only, $10,000 of SPY in the Robinhood cash account. Places no orders.

    .venv/bin/python intraday/scripts/paper_trade.py          # record new sessions
    .venv/bin/python intraday/scripts/paper_trade.py status   # the record so far

T10 replaced T5 on 2026-10-27, before the first paper session, under the rule
pre-registered in RESULTS.md round 3 (it passes more development checks).

Fetches the last ~45 days of SPY one-minute bars from Alpaca, runs the frozen
rule through the research engine, and appends each completed session from
PAPER_START on to intraday/data/paper_t10.jsonl, once. Missed days (Mac asleep)
are filled in on the next run from the same bars, which gives the same answer
the live run would have: the rule passes the engine's look-ahead check.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.chdir(ROOT)

import json  # noqa: E402

import pandas as pd  # noqa: E402

from intraday import data, engine, instruments as I, paper, strategies as St  # noqa: E402

# Frozen 2026-10-27, before the first paper session. Changing any of these
# starts a new rule hash; it does not continue this record.
PAPER_START = date(2026, 10, 28)
CAPITAL = 10_000.0
RULE = St.NoiseArea(exit_rule="vwap")
INST = I.SPY_RH
RECORD = os.path.join(ROOT, "intraday", "data", "paper_t10.jsonl")
# Development-window backtest (RESULTS.md, T10): what the record is compared to.
BACKTEST = {"mean_day": 4013.35 / 2264, "trades_per_day": 749 / 2264}


def fetch(days: int = 45) -> pd.DataFrame:
    from fetch_alpaca_bars import iter_bars
    now = datetime.now(timezone.utc)
    rows = list(iter_bars("SPY", (now - timedelta(days=days)).strftime("%Y-%m-%d"),
                          (now - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True).dt.tz_convert(data.TZ))
    data.validate(df)
    return data.rth(df)


def last_complete_session() -> date:
    ny = pd.Timestamp.now(tz=data.TZ)
    return ny.date() if ny.hour * 60 + ny.minute >= 16 * 60 + 20 else ny.date() - timedelta(days=1)


def record() -> None:
    bars = fetch()
    res = engine.backtest(RULE, bars, INST, capital=CAPITAL, notional=CAPITAL)
    rhash = paper.rule_hash(RULE, INST, CAPITAL)
    done = {r["date"] for r in paper.load(RECORD)}
    rows = paper.new_rows(res, done, PAPER_START, last_complete_session(), rhash,
                          complete=paper.complete_sessions(bars))
    with open(RECORD, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"{stamp} recorded {len(rows)} session(s): "
          + ", ".join(f"{r['date']} {len(r['trades'])} trade(s) ${r['net_usd']:+.2f}"
                      for r in rows))


def status() -> None:
    s = paper.summary(paper.load(RECORD))
    if not s["sessions"]:
        print(f"no paper sessions yet (record starts {PAPER_START})")
        return
    print(f"T10 paper record {s['first']} -> {s['last']}: {s['sessions']} sessions, "
          f"{s['trades']} trades, net ${s['net_usd']:+,.2f} "
          f"(${s['mean_day']:+.2f}/session; backtest ${BACKTEST['mean_day']:+.2f})")
    print(f"  t (Newey-West) {s['t_nw']:+.2f}" if s["t_nw"] == s["t_nw"]
          else "  t needs 20+ sessions")
    if len(s["rules"]) > 1:
        print(f"  WARNING: {len(s['rules'])} rule hashes in one record: {s['rules']}")


if __name__ == "__main__":
    status() if sys.argv[1:] == ["status"] else record()
