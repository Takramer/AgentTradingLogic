"""
xvenue_btc15.py — Kalshi KXBTC15M vs Polymarket btc-updown-15m, paired-quote
logger. READ ONLY: places no orders and imports nothing that can.

The question
------------
Both venues list "will BTC be up over this 15-minute window?" on the same
:00/:15/:30/:45 ET grid. If Kalshi UP + Polymarket DOWN (or the mirror) can be
bought for less than $1 after both taker fees, the pair pays $1 whatever BTC
does -- **provided both venues call the same outcome.**

They settle on different prices. Kalshi uses CF Benchmarks' BRTI; Polymarket's
rules name Chainlink's BTC/USD data stream, and each venue sets its own
starting line from its own feed. When BTC finishes between the two lines, one
venue says UP and the other DOWN and the bought pair pays $0 (or $2). The
identity from `xvenue.breakeven_divergence` applies unchanged: **a net edge of
X dollars tolerates an X probability of adverse disagreement and nothing more.**

So this module measures the two things that decide it, together:

1. the quoted gap, after both fees, at executable size, every few seconds;
2. how often the two venues actually resolved differently.

And then the only number that matters: what the pair would have *paid* on the
realized outcomes, disagreements included (`report()` -> `paper`).

Fees, as published 2026-10-09
-----------------------------
* Kalshi taker: ceil(0.07 * C * P * (1-P)) per order (`xvenue.kalshi_fee`).
* Polymarket crypto taker: C * 0.07 * P * (1-P), no rounding
  (docs.polymarket.com, maker-rebates-program). `--poly-fee-coef` overrides.
  The Polymarket US app (CFTC venue, 15-min BTC since 2026-09-22) has its own
  books and may have its own schedule; this logger reads the international
  CLOB, which is what the public API serves. Check the US app before trusting
  a row for money.

Pre-registered gate (written before the first look; see trial ledger, family
`xvenue`). Tradeable only if ALL hold over >= 1,000 settled windows:

* >= 10% of windows show net edge > 0 with >= $50 of depth on both legs;
* the median of those windows' best net edge exceeds the measured disagreement
  rate;
* the paper P&L of taking the first such quote each window, on realized
  outcomes, is positive.

Usage (on the Mac -- the cloud sandbox cannot reach either venue)
-----
    python3 -m rhengine.xvenue_btc15 --once      # one snapshot to stdout
    python3 -m rhengine.xvenue_btc15 --run       # log every 15 s, settle as it goes
    python3 -m rhengine.xvenue_btc15 --settle    # resolve finished windows only
    python3 -m rhengine.xvenue_btc15 --report    # the gate, from the logs
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

from ._paths import app_path
from .market_data_kalshi import _kalshi_get
from .market_data_polymarket import _json_list
from .xvenue import kalshi_fee, poly_quotes

SERIES = "KXBTC15M"
WINDOW = 900  # seconds
POLY_SLUG = "btc-updown-15m-{start}"
POLY_FEE_COEF = 0.07

LOG_DIR = os.path.join("research_data", "xvenue_btc15")
QUOTES = "quotes.jsonl"
OUTCOMES = "outcomes.jsonl"

#: The two venue reads are sequential. A pair quoted further apart than this
#: is not a simultaneous quote and is excluded from the gate.
MAX_SKEW_S = 2.0
#: Gate thresholds -- pre-registered, do not tune after looking.
GATE_MIN_WINDOWS = 1000
GATE_MIN_DEPTH_USD = 50.0
GATE_MIN_HIT = 0.10
#: Stop retrying a window's settlement after this long.
GIVE_UP_S = 6 * 3600


# ------------------------------------------------------------- the grid ----

def window_bounds(ts):
    """(start, end) unix seconds of the 15-minute window containing ts."""
    start = int(ts) // WINDOW * WINDOW
    return start, start + WINDOW


def poly_slug(start):
    return POLY_SLUG.format(start=int(start))


def _iso_ts(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def pick_kalshi_market(markets, end):
    """The KXBTC15M market whose close_time is this window's end, else None."""
    for m in markets:
        if _iso_ts(m.get("close_time")) == int(end):
            return m
    return None


# ------------------------------------------------------------------ fees ----

def poly_fee(price, contracts=1, coef=POLY_FEE_COEF):
    """Polymarket crypto taker fee in dollars: C * coef * P * (1-P)."""
    if price is None or not (0.0 <= price <= 1.0) or contracts <= 0:
        return 0.0
    return contracts * coef * price * (1.0 - price)


# ----------------------------------------------------------- venue reads ----

def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def kalshi_quote(m):
    """Executable two-sided quote off one Kalshi market dict. YES = UP.

    The NO ask lifts the YES bid, so it inherits the YES bid's size.
    """
    return {
        "ticker": m.get("ticker"),
        "up_ask": _f(m.get("yes_ask_dollars")),
        "down_ask": _f(m.get("no_ask_dollars")),
        "up_ask_size": _f(m.get("yes_ask_size_fp")) or 0.0,
        "down_ask_size": _f(m.get("yes_bid_size_fp")) or 0.0,
        "strike": _f(m.get("floor_strike")),
        "status": m.get("status"),
    }


def poly_tokens(market):
    """{'Up': token_id, 'Down': token_id} from a parsed Gamma market + raw."""
    outcomes = [str(o).strip().lower() for o in _json_list(market.get("outcomes"))]
    ids = market.get("clob_token_ids") or [
        str(t) for t in _json_list(market.get("clobTokenIds"))]
    if len(outcomes) != 2 or len(ids) != 2 or set(outcomes) != {"up", "down"}:
        return None
    return {("Up" if o == "up" else "Down"): t for o, t in zip(outcomes, ids)}


def _gamma_raw(slug):
    """Raw Gamma market for one slug (outcomes, eventMetadata are not parsed
    by market_data_polymarket.parse_gamma_market)."""
    from .market_data_polymarket import GAMMA_BASE, _get
    body = _get(GAMMA_BASE + "/markets", params={"slug": [slug]})
    return body[0] if isinstance(body, list) and body else None


def fetch_pair(now=None, contracts=100):
    """One simultaneous-as-possible read of both venues for the live window.

    Returns (kalshi_quote, poly_quote, meta) or raises ConnectionError with a
    reason a person can act on.
    """
    now = time.time() if now is None else now
    start, end = window_bounds(now)

    t0 = time.time()
    body = _kalshi_get("/markets", params={"series_ticker": SERIES,
                                           "status": "open", "limit": 50})
    km = pick_kalshi_market(body.get("markets", []), end)
    t1 = time.time()
    if km is None:
        raise ConnectionError(f"no open {SERIES} market closing at {end}")

    raw = _gamma_raw(poly_slug(start))
    if raw is None:
        raise ConnectionError(f"no Polymarket market {poly_slug(start)}")
    toks = poly_tokens(raw)
    if toks is None:
        raise ConnectionError(f"unexpected outcomes on {poly_slug(start)}")
    books = poly_quotes([toks["Up"], toks["Down"]])
    t2 = time.time()

    up, down = books.get(str(toks["Up"]), {}), books.get(str(toks["Down"]), {})
    pq = {
        "slug": poly_slug(start),
        "up_ask": up.get("yes_ask"), "up_ask_size": up.get("yes_ask_size") or 0.0,
        "down_ask": down.get("yes_ask"),
        "down_ask_size": down.get("yes_ask_size") or 0.0,
    }
    meta = {"start": start, "end": end, "ts": round(t2, 3),
            "secs_to_close": round(end - t2, 1),
            # Kalshi read finished at t1, Polymarket books at t2.
            "skew_s": round(t2 - t1, 3), "kalshi_rtt_s": round(t1 - t0, 3)}
    return kalshi_quote(km), pq, meta


# ------------------------------------------------------------------ edge ----

def cross_edges(kq, pq, contracts=100, poly_coef=POLY_FEE_COEF):
    """Both cross-venue constructions, net of both taker fees.

    A: Kalshi UP + Polymarket DOWN.   B: Kalshi DOWN + Polymarket UP.
    Each pays $1 per pair when the venues agree. Edge is per pair, in dollars;
    depth is the dollar cost of the size available on the thinner leg.
    """
    out = {}
    for name, k_side, p_side in (("A", "up", "down"), ("B", "down", "up")):
        ka, pa = kq.get(f"{k_side}_ask"), pq.get(f"{p_side}_ask")
        if ka is None or pa is None or not (0 < ka < 1) or not (0 < pa < 1):
            out[name] = None
            continue
        size = min(kq.get(f"{k_side}_ask_size") or 0.0,
                   pq.get(f"{p_side}_ask_size") or 0.0)
        cost = (ka + pa + kalshi_fee(ka, contracts) / contracts
                + poly_fee(pa, contracts, poly_coef) / contracts)
        out[name] = {"k_ask": ka, "p_ask": pa, "cost": round(cost, 5),
                     "net_edge": round(1.0 - cost, 5), "size": size,
                     "depth_usd": round(size * (ka + pa), 2)}
    return out


def poly_internal(pq, contracts=100, poly_coef=POLY_FEE_COEF):
    """Polymarket UP + DOWN on one venue: one oracle, no disagreement risk."""
    u, d = pq.get("up_ask"), pq.get("down_ask")
    if u is None or d is None:
        return None
    cost = u + d + (poly_fee(u, contracts, poly_coef)
                    + poly_fee(d, contracts, poly_coef)) / contracts
    return round(1.0 - cost, 5)


def snapshot_row(kq, pq, meta, contracts=100, poly_coef=POLY_FEE_COEF):
    edges = cross_edges(kq, pq, contracts, poly_coef)
    best = max((e for e in edges.values() if e), key=lambda e: e["net_edge"],
               default=None)
    best_name = next((n for n, e in edges.items() if e is best), None)
    return {**meta, "kalshi": kq, "poly": pq, "edges": edges,
            "best": best_name, "best_net_edge": best and best["net_edge"],
            "poly_internal_edge": poly_internal(pq, contracts, poly_coef),
            "contracts": contracts, "poly_fee_coef": poly_coef}


# ------------------------------------------------------------ settlement ----

def kalshi_outcome(m):
    """'up' / 'down' / None from a settled Kalshi market dict."""
    r = (m.get("result") or "").lower()
    return {"yes": "up", "no": "down"}.get(r)


def poly_outcome(raw):
    """'up' / 'down' / None from a raw Gamma market (closed, outcomePrices)."""
    if not raw or not raw.get("closed"):
        return None
    toks = [str(o).strip().lower() for o in _json_list(raw.get("outcomes"))]
    px = [_f(p) for p in _json_list(raw.get("outcomePrices"))]
    if len(toks) != 2 or len(px) != 2 or None in px:
        return None
    if max(px) < 0.99:  # not cleanly resolved yet
        return None
    return toks[px.index(max(px))]


def settle_window(start, ticker):
    end = start + WINDOW
    k = _kalshi_get(f"/markets/{ticker}").get("market", {})
    raw = _gamma_raw(poly_slug(start))
    meta = (raw or {}).get("eventMetadata") or {}
    return {"start": start, "end": end, "ticker": ticker, "slug": poly_slug(start),
            "kalshi": kalshi_outcome(k), "poly": poly_outcome(raw),
            "kalshi_strike": _f(k.get("floor_strike")),
            "kalshi_value": _f(k.get("expiration_value")),
            "poly_meta": meta if isinstance(meta, dict) else {}}


# --------------------------------------------------------------- logging ----

def _path(name):
    d = app_path(LOG_DIR)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _append(name, row):
    with open(_path(name), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _read(name):
    p = _path(name)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # a half-written last line after a kill
    return rows


def pending_windows(quotes, outcomes, now, grace=300):
    """{start: kalshi_ticker} for logged windows that ended > grace s ago and
    have no outcome row yet. A row is written once both venues resolve, or
    after GIVE_UP_S with whatever resolved, so one stuck market cannot block
    the queue; `evaluate` ignores incomplete rows."""
    done = {o["start"] for o in outcomes}
    out = {}
    for q in quotes:
        s, t = q.get("start"), (q.get("kalshi") or {}).get("ticker")
        if s is None or not t or s in done or s + WINDOW + grace > now:
            continue
        out[s] = t
    return out


def settle_pending(now=None, limit=20):
    now = time.time() if now is None else now
    todo = pending_windows(_read(QUOTES), _read(OUTCOMES), now)
    n = 0
    for start, ticker in sorted(todo.items())[:limit]:
        try:
            row = settle_window(start, ticker)
        except ConnectionError as e:
            print(f"settle {ticker}: {e}", file=sys.stderr)
            continue
        if (row["kalshi"] and row["poly"]) or now - row["end"] > GIVE_UP_S:
            _append(OUTCOMES, row)
            n += 1
    return n


# ---------------------------------------------------------------- report ----

def _usable(e):
    return e and e["net_edge"] > 0 and e["depth_usd"] >= GATE_MIN_DEPTH_USD


def evaluate(quotes, outcomes):
    """The pre-registered gate, from the two logs. Pure."""
    res = {}
    for o in outcomes:
        if o.get("kalshi") and o.get("poly"):
            res[o["start"]] = o
    by_win = {}
    for q in quotes:
        if q.get("start") in res and (q.get("skew_s") or 0) <= MAX_SKEW_S:
            by_win.setdefault(q["start"], []).append(q)

    n = len(res)
    disagree = sum(1 for o in res.values() if o["kalshi"] != o["poly"])
    hit_edges, pnl, trades = [], 0.0, 0
    for start, qs in by_win.items():
        o = res[start]
        best = max((e["net_edge"] for q in qs for e in q["edges"].values()
                    if _usable(e)), default=None)
        if best is None:
            continue
        hit_edges.append(best)
        # Paper trade: first usable quote of the window, one pair.
        q, name, e = next((q, nm, e) for q in sorted(qs, key=lambda r: r["ts"])
                          for nm, e in q["edges"].items() if _usable(e))
        k_side = "up" if name == "A" else "down"
        p_side = "down" if name == "A" else "up"
        payoff = (o["kalshi"] == k_side) + (o["poly"] == p_side)
        pnl += payoff - e["cost"]
        trades += 1

    dis_rate = disagree / n if n else None
    hit = len(hit_edges) / n if n else None
    med = statistics.median(hit_edges) if hit_edges else None
    gates = {
        "windows>=1000": n >= GATE_MIN_WINDOWS,
        "hit>=10%": bool(hit is not None and hit >= GATE_MIN_HIT),
        "median_edge>disagree": bool(med is not None and dis_rate is not None
                                     and med > dis_rate),
        "paper_pnl>0": trades > 0 and pnl > 0,
    }
    return {"windows": n, "disagreements": disagree, "disagree_rate": dis_rate,
            "windows_with_edge": len(hit_edges), "hit_rate": hit,
            "median_best_edge": med, "paper_trades": trades,
            "paper_pnl_per_pair_total": round(pnl, 4),
            "gates": gates, "verdict": "CLEARS" if all(gates.values())
            else "DOES NOT CLEAR"}


def report():
    r = evaluate(_read(QUOTES), _read(OUTCOMES))
    def pct(x):
        return "n/a" if x is None else f"{100 * x:.2f}%"
    print(f"settled windows        {r['windows']}  (gate needs {GATE_MIN_WINDOWS})")
    print(f"venues disagreed       {r['disagreements']}  = {pct(r['disagree_rate'])}")
    print(f"windows with edge>0    {r['windows_with_edge']}  = {pct(r['hit_rate'])}"
          f"  (>= ${GATE_MIN_DEPTH_USD:.0f} depth both legs)")
    med = r["median_best_edge"]
    print(f"median best net edge   {'n/a' if med is None else f'{100 * med:.2f} c'}")
    print(f"paper P&L              {r['paper_pnl_per_pair_total']:+.4f} $ over "
          f"{r['paper_trades']} one-pair trades, disagreements included")
    for g, ok in r["gates"].items():
        print(f"  [{'x' if ok else ' '}] {g}")
    print(r["verdict"])
    return r


# ------------------------------------------------------------------ main ----

def _line(row):
    b = row["edges"].get(row["best"]) if row["best"] else None
    k, p = row["kalshi"], row["poly"]
    return (f"{datetime.fromtimestamp(row['ts'], timezone.utc):%H:%M:%S}Z "
            f"t-{row['secs_to_close']:>5.0f}s  K up/dn {k['up_ask']}/{k['down_ask']}  "
            f"P up/dn {p['up_ask']}/{p['down_ask']}  best {row['best'] or '-'} "
            f"net {('%+.2fc' % (100 * b['net_edge'])) if b else 'n/a'} "
            f"depth ${b['depth_usd'] if b else 0:.0f}  skew {row['skew_s']}s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--settle", action="store_true")
    g.add_argument("--report", action="store_true")
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--contracts", type=int, default=100)
    ap.add_argument("--poly-fee-coef", type=float, default=POLY_FEE_COEF)
    a = ap.parse_args(argv)

    if a.report:
        report()
        return 0
    if a.settle:
        print(f"settled {settle_pending()} windows")
        return 0

    last_settle = 0.0
    while True:
        try:
            kq, pq, meta = fetch_pair(contracts=a.contracts)
            row = snapshot_row(kq, pq, meta, a.contracts, a.poly_fee_coef)
            print(_line(row), flush=True)
            if a.run:
                _append(QUOTES, row)
        except (ConnectionError, OSError) as e:
            print(f"skip: {e}", file=sys.stderr, flush=True)
        if a.once:
            return 0
        if time.time() - last_settle > 120:
            try:
                settle_pending()
            except (ConnectionError, OSError) as e:
                print(f"settle skipped: {e}", file=sys.stderr)
            last_settle = time.time()
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
