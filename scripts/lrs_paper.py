"""lrs_paper.py — forward paper record of LRS-VT from $5,000, at real 15:15 prices.

Starts 2026-10-01, the first session after the data LRS-VT was designed on
(which ended 2026-09-30). Pre-registered in the ledger (family index-leverage,
2026-11-12). No orders, ever: this only reads bars and writes a file.

Each session it calls the same engine functions RobinhoodAgent's planner calls
(rhengine.alpha.lrs: closes_before, vt_exposure, split, held_exposure,
needs_trade) on SPY's 15:15 ET price, fills at the 15:15 one-minute closes of
SPY / SSO / SGOV plus 1 cent a share, charges the SEC fee on sales, credits
dividends on ex-dates, and marks the book at the 16:00 close. Inside the band
it only parks idle cash in SGOV, as the planner does.

The whole path is recomputed from bars on every run. Rows already in the file
must match the recomputation (same rule hash, same equity to the cent), or the
run stops: the record cannot be quietly rewritten. New sessions are appended.

    .venv/bin/python scripts/lrs_paper.py record     # fill up to the last full session
    .venv/bin/python scripts/lrs_paper.py status
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "intraday", "scripts")]
from rhengine.alpha import lrs  # noqa: E402

START, CAPITAL, SLIP, SEC = "2026-10-01", 5_000.0, 0.01, 20.60e-6
OUT = os.path.join(ROOT, "research_data", "paper", "lrs_vt_5k.jsonl")
NY = "America/New_York"


def rule_hash(b: dict) -> str:
    blob = json.dumps({"signal": b["signal"], "vehicles": b["vehicles"], "start": START,
                       "capital": CAPITAL, "slip": SLIP, "sec": SEC, "floor": "max(1, 2%)"},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def minute_bars(sym: str, start: str, end: str) -> pd.DataFrame:
    from fetch_alpaca_bars import iter_bars
    rows = list(iter_bars(sym, f"{start}T13:00:00Z", end))
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"])
    t = pd.to_datetime(df.ts, utc=True).dt.tz_convert(NY)
    hm = t.dt.hour * 60 + t.dt.minute
    df = df.assign(day=t.dt.tz_localize(None).dt.normalize(), hm=hm)
    return df[(hm >= 570) & (hm < 960)]


def dividends(sym: str) -> pd.Series:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                     "?range=2y&interval=1d&events=div",
                     headers={"User-Agent": "Mozilla/5.0 (research script)"}, timeout=30)
    r.raise_for_status()
    ev = (r.json()["chart"]["result"][0].get("events") or {}).get("dividends", {}).values()
    return pd.Series({pd.Timestamp(e["date"], unit="s", tz="UTC").tz_convert(NY).normalize()
                      .tz_localize(None): e["amount"] for e in ev}, dtype=float).sort_index()


def simulate(end: str) -> list[dict]:
    b = lrs.book("lrs_vt")
    veh = b["vehicles"]
    funds = [veh["index"], veh["levered"], veh["cash"]]
    bars = {f: minute_bars(f, START, end) for f in funds}
    full = [d for d, g in bars[veh["index"]].groupby("day") if g.hm.max() >= 959]
    at = {f: bars[f][bars[f].hm <= 915].groupby("day").c.last() for f in funds}
    close = {f: bars[f].groupby("day").c.last() for f in funds}
    div = {f: dividends(f) for f in funds}
    h, band = rule_hash(b), float(b["signal"]["band"])
    sh, cash, rows = {f: 0.0 for f in funds}, CAPITAL, []
    for d in full:
        for f in funds:                                   # ex-date: paid on prior-close shares
            cash += sh[f] * float(div[f].get(d, 0.0))
        px = {f: float(at[f][d]) for f in funds}
        closes, last = lrs.closes_before(d.date())
        e = lrs.vt_exposure(closes, px[veh["index"]], b)
        vals = {f: sh[f] * px[f] for f in funds}
        total = cash + sum(vals.values())
        held = lrs.held_exposure(vals, total, veh)
        floor = max(1.0, 0.02 * total)
        trades = []
        if e is not None and lrs.needs_trade(e, held, band):
            w = lrs.split(e, veh)
            delta = {f: w[f] * total - vals[f] for f in funds}
            for f in sorted(funds, key=lambda f: delta[f]):            # sells first
                dd = delta[f]
                if dd < 0 and (abs(dd) >= floor or w[f] * total < floor) and sh[f] > 0:
                    q = sh[f] if w[f] * total < floor else min(sh[f], -dd / px[f])
                    cash += q * (px[f] - SLIP) * (1 - SEC)
                    sh[f] -= q
                    trades.append([f, -round(q, 6)])
                elif dd > 0 and dd >= floor:
                    spend = min(dd, cash)
                    q = spend / (px[f] + SLIP)
                    cash -= spend
                    sh[f] += q
                    trades.append([f, round(q, 6)])
        elif cash >= floor:                                # inside the band: park idle cash
            q = cash / (px[veh["cash"]] + SLIP)
            sh[veh["cash"]] += q
            cash = 0.0
            trades.append([veh["cash"], round(q, 6)])
        eq = cash + sum(sh[f] * float(close[f][d]) for f in funds)
        rows.append({"date": str(d.date()), "rule_hash": h, "spy_1515": px[veh["index"]],
                     "last_close": str(last), "target": None if e is None else round(e, 4),
                     "held_before": round(held, 4), "trades": trades,
                     "shares": {f: round(sh[f], 6) for f in funds}, "cash": round(cash, 2),
                     "equity": round(eq, 2),
                     "spy_close": float(close[veh["index"]][d])})
    return rows


def record() -> None:
    # the free SIP feed only serves bars older than 15 minutes
    end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = simulate(end)
    old = [json.loads(x) for x in open(OUT)] if os.path.exists(OUT) else []
    for a, b in zip(old, rows):
        if (a["date"], a["rule_hash"]) != (b["date"], b["rule_hash"]) or abs(a["equity"] - b["equity"]) > 0.01:
            raise SystemExit(f"record diverges at {a['date']}: stored {a['equity']} / {a['rule_hash']}, "
                             f"recomputed {b['equity']} / {b['rule_hash']}. Investigate; do not rewrite.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a") as fh:
        for r in rows[len(old):]:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    print(f"{len(rows) - len(old)} new sessions appended ({len(rows)} total)")
    status()


def status() -> None:
    rows = [json.loads(x) for x in open(OUT)]
    eq = pd.Series({r["date"]: r["equity"] for r in rows})
    spy0 = rows[0]["spy_1515"] + SLIP
    spy = pd.Series({r["date"]: CAPITAL / spy0 * r["spy_close"] for r in rows})
    dd = (eq / eq.cummax() - 1).min()
    print(f"LRS-VT paper {rows[0]['date']} -> {rows[-1]['date']}: {len(rows)} sessions, "
          f"${CAPITAL:,.0f} -> ${eq.iloc[-1]:,.2f} ({eq.iloc[-1] / CAPITAL - 1:+.2%}), "
          f"max DD {dd:.1%}; SPY bought at the first 15:15 -> ${spy.iloc[-1]:,.2f} "
          f"({spy.iloc[-1] / CAPITAL - 1:+.2%}) [SPY ex-dividends]")
    print(f"  trades {sum(bool(r['trades']) for r in rows)}, last target {rows[-1]['target']}, "
          f"holding {rows[-1]['shares']}, cash ${rows[-1]['cash']}. Judge after 250+ sessions.")


if __name__ == "__main__":
    {"record": record, "status": status}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
