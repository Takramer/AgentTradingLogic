"""
xvenue.py — Kalshi <-> Polymarket cross-venue quote scanner. READ ONLY.

This places no orders and imports nothing that can. It answers one question,
by measurement rather than by argument: **does an executable gap exist between
the two venues, and for how long does it survive?**

Why it is a scanner and not a strategy
--------------------------------------
The gap is not the edge. Four things stand between a quoted spread and money,
and this module prices three of them into every row so the log cannot flatter
itself:

1. **Kalshi charges a taker fee** `ceil(0.07 * C * P * (1-P))`, peaking at
   1.75 c/contract at P=0.50. That is larger than the spread between the two
   venues on anything liquid. `kalshi_fee()` below; `--contracts` sets C
   because the ceiling is per *order*, so the rounding only amortizes at size.
2. **Capital is locked until resolution and cannot be netted across venues.**
   A 3 c edge held nine months is worse than T-bills, so every row carries
   `days_to_close` and `ann_return` and you should read the annualized column
   first.
3. **Divergent resolution loses the whole stake, not the edge.** Kalshi's
   `rules_primary` and Polymarket's UMA oracle can disagree on sources,
   cutoffs and timezones. The arithmetic is unusually clean: cost + edge = 1
   by construction, so the break-even probability of adverse divergence
   *equals the net edge in dollars*. A 3 c edge tolerates a 3% chance of the
   two venues resolving differently, and nothing more. `breakeven_divergence()`
   is that identity, and it is the number that kills most rows.

The fourth, leg risk, cannot be measured from quotes and is not modelled here.

What the log is for
-------------------
`scan()` appends one JSONL row per pair per cycle to
`research_data/xvenue_scan.jsonl`. Run it for a few weeks and the file answers:
how often is net edge > 0, at what size, on which pairs, and does an edge
persist across cycles or vanish in one? A gap seen in a single snapshot is a
stale quote. A gap that survives twenty cycles is a real barrier -- usually a
legal or capital-mobility barrier, which means the spread is the *price of that
barrier*, not free money.

Pair mapping is the hard part and is deliberately manual
--------------------------------------------------------
`--discover` matches Kalshi titles against Polymarket questions on token
overlap. It is a dumb string matcher and it emits **candidates only**, written
with `verified: false`. Nothing marked unverified is a tradable pair; it is a
prompt to read both rules texts side by side (`--audit` prints them) and set
the flag by hand. Every row in the log carries the pair's `verified` flag so
an unverified gap can never be mistaken for a finding.

Not a trial (yet)
-----------------
`trials.py` counts *decision points resolved by looking at a number*. Building
an instrument is not one. Reading its log and concluding "there is / is not an
exploitable gap" IS one -- log it to family `xvenue` at that point, not now.

Usage
-----
    python3 -m rhengine.xvenue --discover --limit 400   # propose candidate pairs
    python3 -m rhengine.xvenue --audit                  # rules text, side by side
    python3 -m rhengine.xvenue --once                   # one cycle to stdout + log
    python3 -m rhengine.xvenue --interval 300           # poll every 5 min
    python3 -m rhengine.xvenue --report                 # summarize the log so far

(This repo's CLAUDE.md says `py`; that is the Windows box. Use `python3` here.)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

from ._paths import app_path
from .market_data_kalshi import KALSHI_HOSTS, _kalshi_get
from .market_data_polymarket import CLOB_BASE, gamma_markets
from .xvenue_relations import (
    DISJOINT, EQUIVALENT, K_BROADER, K_NARROWER, UNKNOWN, classify,
)

PAIRS_NAME = os.path.join("research_data", "xvenue_pairs.json")
LOG_NAME = os.path.join("research_data", "xvenue_scan.jsonl")

#: A hedged pair that agrees on resolution cannot show a large edge -- the two
#: venues would have to be quoting the same event 20 points apart and nobody
#: closing it. So a break-even divergence probability above this is a tell that
#: the two legs are DIFFERENT CONTRACTS, not a tell that money is lying around.
#: Rows above it are flagged MISMATCH? and excluded from the report's hit rate.
MISMATCH_Q = 0.10

#: Kalshi general trading fee: ceil(0.07 * C * P * (1-P)), rounded up to the
#: next cent, charged on the taker side. Maker fees exist on some series and
#: are NOT modelled -- an arb crosses, so taker is the honest assumption.
KALSHI_FEE_COEF = 0.07


# ------------------------------------------------------------------ fees ----

def kalshi_fee(price, contracts=1, coef=KALSHI_FEE_COEF):
    """Kalshi taker fee in dollars for `contracts` at `price`.

    The ceiling is applied to the whole order, not per contract, which is why
    size matters: one contract at P=0.5 pays 1 c on a 1.75 c theoretical fee
    (a 43% overpay), while 1000 contracts pay the theoretical rate almost
    exactly. Returns total dollars; divide by contracts for the per-unit cost.
    """
    if price is None or not (0.0 <= price <= 1.0) or contracts <= 0:
        return 0.0
    return math.ceil(coef * contracts * price * (1.0 - price) * 100.0) / 100.0


def breakeven_divergence(edge):
    """Probability of adverse divergent resolution that zeroes the EV.

    Cost + edge = 1 because the hedged pair pays exactly 1 on agreement. If the
    venues disagree adversely both legs lose and the whole cost is gone:

        EV = (1-q)*edge - q*cost = 0  ->  q = edge / (edge + cost) = edge

    So the tolerance for a rules mismatch or a contested UMA resolution is, in
    probability, numerically identical to the net edge in dollars. Two-sided
    divergence (both legs winning) is ignored on purpose: when divergence is
    driven by someone profiting from the oracle, it is adverse by construction.
    """
    return max(0.0, float(edge))


def annualized(edge, cost, days):
    """Compound the per-resolution return to a year. None if days is unusable."""
    if not days or days <= 0 or cost is None or cost <= 0:
        return None
    try:
        return (1.0 + edge / cost) ** (365.0 / days) - 1.0
    except (OverflowError, ValueError):
        return None


# ------------------------------------------------------------- venue reads ----

def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def kalshi_quotes(tickers, chunk=50):
    """{ticker: quote} via batched GET /markets?tickers=. One call per chunk.

    Kalshi's NO book is the exact complement of the YES book (verified against
    the live payload: yes_bid 0.06 / yes_ask 0.07 -> no_bid 0.93 / no_ask 0.94),
    so NO ask size is the YES bid size and vice versa. Sizes are contracts.
    """
    out = {}
    tickers = list(tickers)
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        body = _kalshi_get("/markets", params={"tickers": ",".join(batch)})
        for m in body.get("markets", []):
            out[m["ticker"]] = {
                "yes_bid": _f(m.get("yes_bid_dollars")),
                "yes_ask": _f(m.get("yes_ask_dollars")),
                "no_bid": _f(m.get("no_bid_dollars")),
                "no_ask": _f(m.get("no_ask_dollars")),
                # NO ask lifts the YES bid, so it inherits the YES bid's size.
                "yes_ask_size": _f(m.get("yes_ask_size_fp")) or 0.0,
                "no_ask_size": _f(m.get("yes_bid_size_fp")) or 0.0,
                "close_time": m.get("close_time"),
                "status": m.get("status"),
                "title": m.get("title"),
                "rules": m.get("rules_primary") or "",
                "rules_secondary": m.get("rules_secondary") or "",
            }
    return out


def _best(levels, side):
    """Best price/size off one side of a Polymarket book.

    The API returns bids ascending and asks descending, but that ordering is
    not contractual, so take the extremum rather than trusting the index.
    """
    best_p, best_sz = None, 0.0
    for lv in levels or []:
        p, sz = _f(lv.get("price")), _f(lv.get("size")) or 0.0
        if p is None:
            continue
        if best_p is None or (p > best_p if side == "bid" else p < best_p):
            best_p, best_sz = p, sz
    return best_p, best_sz


def poly_quotes(token_ids, timeout=25, chunk=100):
    """{token_id: quote} via batched POST /books, chunked."""
    token_ids = [str(t) for t in token_ids]
    out = {}
    for i in range(0, len(token_ids), chunk):
        out.update(_poly_books(token_ids[i:i + chunk], timeout))
    return out


def _poly_books(token_ids, timeout=25):
    if not token_ids:
        return {}
    r = requests.post(CLOB_BASE + "/books",
                      json=[{"token_id": t} for t in token_ids],
                      timeout=timeout)
    r.raise_for_status()
    out = {}
    for b in r.json() or []:
        bid, bid_sz = _best(b.get("bids"), "bid")
        ask, ask_sz = _best(b.get("asks"), "ask")
        out[str(b.get("asset_id"))] = {
            "yes_bid": bid, "yes_ask": ask,
            "yes_bid_size": bid_sz, "yes_ask_size": ask_sz,
            "tick_size": _f(b.get("tick_size")),
            "neg_risk": bool(b.get("neg_risk", False)),
        }
    return out


def merge_poly(yes_q, no_q):
    """One two-sided Polymarket quote from the YES and NO token books.

    Polymarket runs a separate book per outcome token, so the real cost of the
    NO side is the NO token's own ask -- not 1 - yes_bid. The two differ
    whenever the books are not perfectly complementary, which is most of the
    time on a thin market. Fall back to the complement only when the NO token
    is missing, and say so in `no_source` so a row built on the approximation
    is never mistaken for one built on a real quote.
    """
    if not yes_q:
        return None
    out = dict(yes_q)
    if no_q and no_q.get("yes_ask") is not None:
        out["no_ask"] = no_q["yes_ask"]
        out["no_bid"] = no_q.get("yes_bid")
        out["no_ask_size"] = no_q.get("yes_ask_size") or 0.0
        out["no_source"] = "no_token"
    else:
        yb = yes_q.get("yes_bid")
        out["no_ask"] = None if yb is None else round(1.0 - yb, 6)
        ya = yes_q.get("yes_ask")
        out["no_bid"] = None if ya is None else round(1.0 - ya, 6)
        out["no_ask_size"] = yes_q.get("yes_bid_size") or 0.0
        out["no_source"] = "complement_of_yes"
    return out


# ------------------------------------------------------------------- edge ----

#: Which hedge each relation admits, as (kalshi_side, poly_side). Only these
#: constructions have a guaranteed minimum payoff of 1. The mirror direction of
#: a nested relation pays ZERO in the middle state and is not a trade -- it is
#: exactly the false positive the first version of this scanner reported as a
#: 24 c edge on "will X run" against "will X win".
HEDGES = {
    EQUIVALENT: [("yes", "no"), ("no", "yes")],
    K_BROADER:  [("yes", "no")],   # P => K: long the broader leg, short the narrower
    K_NARROWER: [("no", "yes")],   # K => P
    DISJOINT:   [("no", "no")],    # at most one can pay, so both NOs cannot both lose
    UNKNOWN:    [],
}

#: EQUIVALENT is guaranteed only if the two venues resolve the same way.
#: The nested and disjoint hedges are guaranteed by the logical relation itself,
#: which survives the venues disagreeing about sources, cutoffs and timezones.
GUARANTEE = {EQUIVALENT: "rules", K_BROADER: "logic",
             K_NARROWER: "logic", DISJOINT: "logic", UNKNOWN: "none"}


def _days_to_close(close_time, now=None):
    if not close_time:
        return None
    try:
        t = datetime.fromisoformat(str(close_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (t - now).total_seconds() / 86400.0)


def _leg(quote, side):
    """(ask price, ask size) for buying `side` of one venue's contract."""
    if side == "yes":
        return quote.get("yes_ask"), quote.get("yes_ask_size") or 0.0
    return quote.get("no_ask"), quote.get("no_ask_size") or 0.0


def _mid(q):
    b, a = q.get("yes_bid"), q.get("yes_ask")
    return None if b is None or a is None else (b + a) / 2.0


def _spread(q):
    b, a = q.get("yes_bid"), q.get("yes_ask")
    return None if b is None or a is None else max(0.0, a - b)


def unwind(kq, pq, contracts=100):
    """Economics of the OTHER trade: enter the dislocation, exit on convergence,
    never hold to resolution.

    This is what makes a rules mismatch survivable -- you are gone before either
    venue resolves anything. It replaces one risk with another. The hold-to-
    resolution arb has a bounded worst case; this does not. If the gap is
    structural (running is not winning) it never converges and the exit is at
    whatever the market gives you.

    `convergence_needed` is how far the two mids must close before the round
    trip breaks even: two Kalshi taker fees plus both spreads crossed twice.
    """
    km, pm = _mid(kq), _mid(pq)
    ks, ps = _spread(kq), _spread(pq)
    if km is None or pm is None or ks is None or ps is None:
        return None
    gap = abs(km - pm)
    fee = 2.0 * kalshi_fee(km, contracts) / contracts
    roundtrip = fee + ks + ps
    return {"gap": round(gap, 4),
            "roundtrip_cost": round(roundtrip, 4),
            "convergence_needed": round(roundtrip, 4),
            "unwind_net_if_full_convergence": round(gap - roundtrip, 4),
            "unwind_risk": "unbounded - convergence is not guaranteed"}


def relation_edge(kq, pq, relation, contracts=100, poly_fee_bps=0.0, now=None):
    """Best admissible hedge for one pair, given what the two contracts mean.

    Returns None when the relation admits no trade (UNKNOWN) or a leg is not
    quoted. Every returned row states which guarantee it rests on.
    """
    if not kq or not pq:
        return None
    best = None
    for k_side, p_side in HEDGES.get(relation, []):
        k_px, k_sz = _leg(kq, k_side)
        p_px, p_sz = _leg(pq, p_side)
        if k_px is None or p_px is None or k_px <= 0 or p_px <= 0:
            continue
        k_fee = kalshi_fee(k_px, contracts) / contracts
        p_fee = p_px * poly_fee_bps / 10_000.0
        cost = k_px + p_px + k_fee + p_fee
        net = 1.0 - cost                      # guaranteed minimum, not expected
        size = min(k_sz or 0.0, p_sz or 0.0)
        days = _days_to_close(kq.get("close_time"), now)
        guarantee = GUARANTEE.get(relation, "none")
        row = {
            "relation": relation,
            "guarantee": guarantee,
            "direction": "kalshi_{}/poly_{}".format(k_side, p_side),
            "kalshi_px": round(k_px, 4), "poly_px": round(p_px, 4),
            "gross_edge": round(1.0 - (k_px + p_px), 4),
            "kalshi_fee_per_contract": round(k_fee, 4),
            "poly_fee_per_contract": round(p_fee, 4),
            "cost": round(cost, 4),
            "net_edge": round(net, 4),
            # A nested or disjoint hedge pays 2 in its middle state; an
            # equivalent one never does. Minimum is what you underwrite.
            "max_payoff": 1.0 if relation == EQUIVALENT else 2.0,
            "max_contracts": round(size, 2),
            "net_dollars_at_size": round(net * size, 2),
            "days_to_close": None if days is None else round(days, 2),
            "ann_return": annualized(net, cost, days),
        }
        if row["ann_return"] is not None:
            row["ann_return"] = round(row["ann_return"], 4)
        # q* is only meaningful where the guarantee rests on the two venues
        # resolving alike. A logic-guaranteed hedge does not have this failure
        # mode; its residual risk is that the IMPLICATION breaks, which is a
        # different and much narrower question for --audit.
        if guarantee == "rules":
            row["breakeven_divergence"] = round(breakeven_divergence(net), 4)
            row["mismatch_suspect"] = breakeven_divergence(net) > MISMATCH_Q
        else:
            row["breakeven_divergence"] = None
            row["mismatch_suspect"] = False
        if best is None or row["net_edge"] > best["net_edge"]:
            best = row
    return best


# ------------------------------------------------------------------ pairs ----

def load_pairs(path=None):
    path = path or app_path(PAIRS_NAME)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("pairs", [])


def save_pairs(pairs, path=None):
    path = path or app_path(PAIRS_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(),
                   "pairs": pairs}, f, indent=1)
    os.replace(tmp, path)


_STOP = {"the", "a", "an", "will", "be", "in", "on", "at", "to", "of", "by",
         "for", "and", "or", "is", "are", "before", "after", "than", "this",
         "that", "it", "with", "any", "who", "what", "which", "how", "many"}


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if w not in _STOP and len(w) > 1}


def kalshi_open_markets(pages=6, per_page=200):
    """Real open markets via GET /events?with_nested_markets=true.

    NOT via GET /markets. That listing is swamped by KXMVECROSSCATEGORY combo
    shards -- auto-generated cross-category parlays which have no Polymarket
    analogue. Verified 2026-08-21: the first 1000 rows of /markets?status=open
    are 1000/1000 shards, and neither `exchange_index=0` nor a client-side
    prefix filter recovers a single real market from them. The events endpoint
    returns none of them.

    Yields flat dicts with a `match_text` built from the event title plus the
    market's yes-side subtitle, because a multi-outcome event ("Who will the
    next Pope be?") carries the actual candidate name only in the subtitle.
    """
    out, cursor = [], None
    for _ in range(pages):
        params = {"limit": per_page, "status": "open",
                  "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        body = _kalshi_get("/events", params=params)
        events = body.get("events", [])
        for ev in events:
            title = ev.get("title") or ""
            for m in ev.get("markets") or []:
                sub = m.get("yes_sub_title") or m.get("subtitle") or ""
                out.append({
                    "ticker": m.get("ticker"),
                    "event_ticker": ev.get("event_ticker"),
                    "subtitle": sub,
                    "title": "{} {}".format(title, sub).strip(),
                    "match_text": "{} {}".format(title, sub).strip(),
                })
        cursor = body.get("cursor")
        if not cursor or not events:
            break
    return out


def poly_open_markets(limit=500, page=100):
    """Top-volume open Polymarket markets. Gamma caps `limit` at 100 whatever
    you ask for (verified 2026-08-21: limit=500 returns 100), so page on
    `offset` instead."""
    out = []
    for off in range(0, limit, page):
        got = gamma_markets(limit=page, offset=off, closed=False,
                            order="volumeNum", ascending=False)
        out.extend(p for p in got if p.get("clob_token_ids"))
        if len(got) < page:
            break
    return out


def discover(limit=500, min_overlap=0.45, kalshi_pages=6,
             subtitle_cover=0.6):
    """Propose candidate pairs by Jaccard overlap on title tokens.

    Deliberately dumb. This finds *names that look alike*, which is not the
    same as *contracts that resolve alike* -- the whole point of the manual
    verification step. Everything it returns is written verified=false.
    """
    kms = kalshi_open_markets(pages=kalshi_pages)
    pms = poly_open_markets(limit=limit)

    ktok = [(m, _tokens(m["match_text"]), _tokens(m.get("subtitle"))) for m in kms]
    out = []
    for p in pms:
        pt = _tokens(p.get("question"))
        if len(pt) < 3:
            continue
        for m, kt, st in ktok:
            if len(kt) < 3:
                continue
            # Subtitle gate. On a multi-outcome event every market shares the
            # same title ("Who will run for the 2028 Democratic nomination?"),
            # so the shared frame alone scores 0.4+ Jaccard and the matcher
            # cheerfully pairs Gavin Newsom with Hillary Clinton. The subtitle
            # holds the only distinguishing text there is; require most of it
            # to appear on the Polymarket side before the pair is a candidate.
            if st and len(st & pt) / float(len(st)) < subtitle_cover:
                continue
            j = len(kt & pt) / float(len(kt | pt))
            if j >= min_overlap:
                out.append({
                    "id": "{}__{}".format(m["ticker"], p["slug"])[:120],
                    "kalshi_ticker": m["ticker"],
                    "kalshi_title": m["title"],
                    "poly_slug": p["slug"],
                    "poly_question": p.get("question"),
                    "poly_token_yes": p["clob_token_ids"][0],
                    "poly_token_no": (p["clob_token_ids"][1]
                                      if len(p["clob_token_ids"]) > 1 else None),
                    "similarity": round(j, 3),
                    "poly_liquidity": p.get("liquidity"),
                    "verified": False,
                    "note": "UNVERIFIED candidate - read both rules texts "
                            "(--audit) before trusting any edge on this pair",
                })
    out.sort(key=lambda r: -r["similarity"])
    return out, len(kms), len(pms)


# ------------------------------------------------------------------- scan ----

def scan(pairs, contracts=100, poly_fee_bps=0.0):
    """One cycle. Two Kalshi calls plus one Polymarket call per 100 tokens.

    A pair with no `relation` recorded is classified on the fly, but the result
    is treated as a proposal: it lands in the row so the log shows what was
    assumed, and `--classify` is where a human makes it stick.
    """
    if not pairs:
        return []
    kq = kalshi_quotes([p["kalshi_ticker"] for p in pairs])
    tokens = []
    for p in pairs:
        tokens.append(str(p["poly_token_yes"]))
        if p.get("poly_token_no"):
            tokens.append(str(p["poly_token_no"]))
    pq = poly_quotes(tokens)
    ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for p in pairs:
        k = kq.get(p["kalshi_ticker"])
        v = merge_poly(pq.get(str(p["poly_token_yes"])),
                       pq.get(str(p.get("poly_token_no"))))
        relation = p.get("relation")
        if not relation:
            relation, conf, why = classify(p.get("kalshi_title"),
                                           p.get("poly_question"))
        else:
            conf, why = p.get("relation_confidence", 1.0), "recorded in pairs file"
        row = {"ts": ts, "id": p["id"], "verified": bool(p.get("verified")),
               "kalshi_ticker": p["kalshi_ticker"], "poly_slug": p.get("poly_slug"),
               "relation": relation, "relation_confidence": conf,
               "relation_reason": why}
        if relation == UNKNOWN:
            row["error"] = "no admissible hedge (relation UNKNOWN)"
            rows.append(row)
            continue
        e = relation_edge(k, v, relation, contracts=contracts,
                          poly_fee_bps=poly_fee_bps)
        if e is None:
            row["error"] = ("no kalshi quote" if not k else
                            "no poly quote" if not v else
                            "relation admits no hedge / one-sided book")
        else:
            row.update(e)
            row["poly_no_source"] = v.get("no_source")
            u = unwind(k, v, contracts=contracts)
            if u:
                row.update(u)
        rows.append(row)
    return rows


def append_log(rows, path=None):
    path = path or app_path(LOG_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return path


def report(path=None, min_edge=0.0):
    """Summarize the log: per pair, how often net edge cleared and by how much.

    Persistence is the column that matters. A pair that shows an edge in 1 of
    40 cycles was a stale quote; one that shows it in 35 of 40 is a barrier.
    """
    path = path or app_path(LOG_NAME)
    if not os.path.exists(path):
        return {}
    agg = {}
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        a = agg.setdefault(r["id"], {
            "cycles": 0, "quoted": 0, "hits": 0, "best_net": None,
            "best_ann": None, "verified": r.get("verified", False),
            "sum_net": 0.0, "max_size": 0.0, "mismatch": 0})
        a["cycles"] += 1
        if "net_edge" not in r:
            continue
        a["quoted"] += 1
        a["sum_net"] += r["net_edge"]
        if r.get("mismatch_suspect"):
            a["mismatch"] += 1
            continue
        if r["net_edge"] > min_edge:
            a["hits"] += 1
            a["max_size"] = max(a["max_size"], r.get("max_contracts") or 0.0)
        if a["best_net"] is None or r["net_edge"] > a["best_net"]:
            a["best_net"] = r["net_edge"]
            a["best_ann"] = r.get("ann_return")
    for a in agg.values():
        a["mean_net"] = round(a["sum_net"] / a["quoted"], 4) if a["quoted"] else None
        a["hit_rate"] = round(a["hits"] / a["quoted"], 3) if a["quoted"] else None
        del a["sum_net"]
    return agg


# -------------------------------------------------------------------- CLI ----

def _fmt(row):
    if "error" in row:
        return "  {:<40} {}".format(row["id"][:40], row["error"])
    ann, q = row.get("ann_return"), row.get("breakeven_divergence")
    return ("  {:<40} {:<11} net {:+.4f}  size {:>7.0f}  {:>5}d  ann {:>8}  "
            "{}  [{}]{}{}").format(
        row["id"][:40], row["relation"], row["net_edge"], row["max_contracts"],
        "n/a" if row["days_to_close"] is None else "{:.0f}".format(row["days_to_close"]),
        "n/a" if ann is None else "{:+.1%}".format(ann),
        "q* {:.1%}".format(q) if q is not None else "guar:logic",
        row["direction"],
        "" if row["verified"] else "  UNVERIFIED",
        "  MISMATCH?" if row.get("mismatch_suspect") else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--discover", action="store_true",
                    help="propose candidate pairs by title overlap (writes verified=false)")
    ap.add_argument("--audit", action="store_true",
                    help="print both venues' rules text per pair for manual verification")
    ap.add_argument("--once", action="store_true", help="one scan cycle")
    ap.add_argument("--interval", type=float, default=None,
                    help="poll forever every N seconds")
    ap.add_argument("--report", action="store_true", help="summarize the log")
    ap.add_argument("--classify", action="store_true",
                    help="propose a logical relation for every pair and write it "
                         "into the pairs file (proposals, not verdicts)")
    ap.add_argument("--relation", default=None,
                    help="scan only pairs with this relation "
                         "(EQUIVALENT/K_BROADER/K_NARROWER/DISJOINT/UNKNOWN)")
    ap.add_argument("--contracts", type=int, default=100,
                    help="order size used to amortize the Kalshi fee ceiling (default 100)")
    ap.add_argument("--poly-fee-bps", type=float, default=0.0,
                    help="Polymarket fee in bps; 0 on most markets, VERIFY per market")
    ap.add_argument("--min-edge", type=float, default=0.0,
                    help="only print rows with net edge above this (default 0)")
    ap.add_argument("--limit", type=int, default=400, help="--discover: Polymarket markets to scan")
    ap.add_argument("--min-overlap", type=float, default=0.45, help="--discover: Jaccard floor")
    ap.add_argument("--subtitle-cover", type=float, default=0.6,
                    help="--discover: fraction of the Kalshi subtitle that must "
                         "appear in the Polymarket question (default 0.6)")
    ap.add_argument("--top", type=int, default=None,
                    help="scan only the N pairs with the deepest Polymarket liquidity")
    ap.add_argument("--verified-only", action="store_true",
                    help="scan only pairs a human has marked verified")
    ap.add_argument("--pairs", default=None, help="override pairs file path")
    ap.add_argument("--out", default=None, help="override log path")
    a = ap.parse_args(argv)

    if a.discover:
        cands, nk, np_ = discover(limit=a.limit, min_overlap=a.min_overlap,
                                  subtitle_cover=a.subtitle_cover)
        existing = {p["id"]: p for p in load_pairs(a.pairs)}
        added = 0
        for c in cands:
            if c["id"] not in existing:
                existing[c["id"]] = c
                added += 1
        save_pairs(list(existing.values()), a.pairs)
        print("scanned {} Kalshi open markets x {} Polymarket markets".format(nk, np_))
        print("{} candidates at overlap >= {}, {} new".format(len(cands), a.min_overlap, added))
        for c in cands[:20]:
            print("  {:.2f}  K: {}".format(c["similarity"], (c["kalshi_title"] or "")[:70]))
            print("        P: {}".format((c["poly_question"] or "")[:70]))
        if cands:
            print("\nNone of these are tradable pairs yet. Run --audit and set "
                  "verified=true by hand where the rules genuinely match.")
        return 0

    pairs = load_pairs(a.pairs)

    if a.classify:
        counts, changed = {}, 0
        for p in pairs:
            rel, conf, why = classify(p.get("kalshi_title"), p.get("poly_question"))
            if p.get("relation") != rel:
                changed += 1
            p["relation"], p["relation_confidence"] = rel, conf
            p["relation_reason"] = why
            counts[rel] = counts.get(rel, 0) + 1
        save_pairs(pairs, a.pairs)
        print("classified {} pairs ({} changed)".format(len(pairs), changed))
        for rel in sorted(counts, key=lambda r: -counts[r]):
            print("  {:<12} {}".format(rel, counts[rel]))
        print("\nRelations are PROPOSALS from text. K_BROADER / K_NARROWER / "
              "DISJOINT hedges are guaranteed by logic rather than by the two "
              "venues resolving alike -- but only if the implication itself "
              "survives both rule sets. Run --audit before setting verified.")
        return 0

    if a.relation:
        pairs = [p for p in pairs if (p.get("relation") or UNKNOWN) == a.relation]
    if a.verified_only:
        pairs = [p for p in pairs if p.get("verified")]
    if a.top:
        pairs = sorted(pairs, key=lambda p: -(p.get("poly_liquidity") or 0))[:a.top]
    if not pairs:
        print("no pairs in {} -- run --discover first".format(a.pairs or app_path(PAIRS_NAME)))
        return 1

    if a.audit:
        kq = kalshi_quotes([p["kalshi_ticker"] for p in pairs])
        for p in pairs:
            k = kq.get(p["kalshi_ticker"], {})
            print("=" * 78)
            print("{}   verified={}".format(p["id"], p.get("verified", False)))
            print("-- KALSHI {} --".format(p["kalshi_ticker"]))
            print("   title: {}".format(k.get("title")))
            print("   close: {}".format(k.get("close_time")))
            print("   rules: {}".format(k.get("rules", "")[:600]))
            if k.get("rules_secondary"):
                print("   also : {}".format(k["rules_secondary"][:300]))
            print("-- POLYMARKET {} --".format(p.get("poly_slug")))
            print("   question: {}".format(p.get("poly_question")))
            print("   rules   : https://polymarket.com/event/{}".format(p.get("poly_slug")))
            print("   (UMA resolution criteria live on the market page; Gamma does "
                  "not expose full rules text -- read it there.)")
        return 0

    if a.report:
        agg = report(a.out, min_edge=a.min_edge)
        if not agg:
            print("empty log")
            return 1
        print("{:<44} {:>6} {:>6} {:>8} {:>9} {:>9}".format(
            "pair", "cycles", "quoted", "hit_rate", "mean_net", "best_net"))
        for pid, v in sorted(agg.items(), key=lambda kv: -(kv[1]["best_net"] or -9)):
            print("{:<44} {:>6} {:>6} {:>8} {:>9} {:>9}{}".format(
                pid[:44], v["cycles"], v["quoted"],
                "n/a" if v["hit_rate"] is None else "{:.0%}".format(v["hit_rate"]),
                "n/a" if v["mean_net"] is None else "{:+.4f}".format(v["mean_net"]),
                "n/a" if v["best_net"] is None else "{:+.4f}".format(v["best_net"]),
                "" if v["verified"] else "  UNVERIFIED"))
        print("\nhit_rate is the column that matters: a one-cycle gap is a stale "
              "quote, a persistent gap is a barrier you must name before trading it.")
        return 0

    def cycle():
        rows = scan(pairs, contracts=a.contracts, poly_fee_bps=a.poly_fee_bps)
        path = append_log(rows, a.out)
        shown = [r for r in rows if "error" in r or r["net_edge"] >= a.min_edge]
        print("{}  {} pairs, {} rows >= {:+.4f}  -> {}".format(
            datetime.now(timezone.utc).strftime("%H:%M:%S"),
            len(rows), len([r for r in shown if "error" not in r]), a.min_edge, path))
        for r in sorted(shown, key=lambda r: -(r.get("net_edge", -9))):
            print(_fmt(r))

    if a.interval:
        print("polling every {}s; ctrl-C to stop".format(a.interval))
        try:
            while True:
                try:
                    cycle()
                except Exception as e:  # a venue hiccup must not end the run
                    print("  cycle failed: {}: {}".format(type(e).__name__, e))
                time.sleep(a.interval)
        except KeyboardInterrupt:
            print("\nstopped")
        return 0

    cycle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
