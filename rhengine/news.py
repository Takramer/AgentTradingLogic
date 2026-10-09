"""
news.py — real-time news breadth, and whether an event contract has priced it.

This is the DIRECTIONAL tool, not the arbitrage one. `xvenue.py` finds trades
whose worst case is bounded by logic. Nothing here is bounded by anything. A
news-driven trade on an event contract is a bet that the market has not yet
repriced, and it can be wrong in the full amount. Say that out loud before
sizing anything from this module.

Sources, and what each is actually good for
-------------------------------------------
  Alpaca /v1beta1/news   Benzinga feed, real-time (seconds to a minute),
                         symbol-tagged. Keyed, and ALPACA_API_KEY is already in
                         secrets.json. Best latency available here. Equity and
                         crypto only -- it will not cover a Israeli election or
                         a papal conclave.
  Google News RSS        No key, arbitrary query, ~100 items per query, broad
                         topical coverage including politics and geopolitics.
                         This is the breadth source. One trap, found by
                         measurement 2026-08-21: the feed is RELEVANCE-ranked,
                         not recency-ranked. A bare query for "Nikki Haley"
                         returns items spanning 2024 to last week, so filtering
                         it by timestamp afterwards throws away the sample
                         instead of windowing it. The recency window has to go
                         INTO the query as `when:Nd`, which is what
                         `google_news()` does.
  GDELT DOC 2.0          NOT wired. Global and keyless, but throttled to HTTP
                         429 from this host at any pace (verified 2026-08-21,
                         one request per 25 s still refused). A retry loop that
                         never succeeds is worse than this sentence.

Not wired, and why: Finnhub and Alpha Vantage both have news endpoints and both
already have adapters in this repo, but neither key is set in secrets.json
(checked 2026-08-21 -- only Alpaca and Massive are). Add the key and they slot
in beside Alpaca; the interface below is the same shape.

The signal
----------
Sentiment scoring is skipped on purpose. Free-tier feeds do not agree on it, and
a headline classifier is a research project. What survives contact with reality
is **article volume against its own baseline**: a story breaking produces a rate
of coverage several times its trailing norm, and that is measurable with a
timestamp and arithmetic.

`divergence()` then joins that to the contract's own price tape (reusing
`market_data_kalshi.fetch_prob_bars`, which already caches) and puts each
contract in one of four boxes:

    news spike + price moved   PRICED       the market saw it. No trade.
    news spike + price flat    UNPRICED     the candidate. Also the box where
                                            you are most likely to be the last
                                            to know -- a flat price after real
                                            news often means the news is not
                                            news to anyone who trades it.
    no spike   + price moved   SILENT_MOVE  informed flow, or a thin book being
                                            pushed. Fading it is a bet against
                                            someone who may know more.
    no spike   + price flat    QUIET        nothing.

UNPRICED is a place to look, not a trade. The module reports the box and the
numbers behind it and stops there.
"""
from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from ._paths import app_path
from .market_data_kalshi import fetch_prob_bars

ALPACA_NEWS = "https://data.alpaca.markets/v1beta1/news"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
UA = {"User-Agent": "Mozilla/5.0 (rhengine research)"}

LOG_NAME = os.path.join("research_data", "news_scan.jsonl")


def _utc(dt):
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now():
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------- sources ----

def alpaca_news(symbols=None, since_hours=24, limit=50):
    """Benzinga feed via Alpaca. Returns [{ts, title, source, symbols, url}].

    Raises EnvironmentError if the Alpaca key is not configured -- callers that
    want breadth without keys should use google_news() instead.
    """
    from .market_data import _get_credentials
    key, secret = _get_credentials()
    params = {"limit": min(limit, 50), "sort": "desc",
              "start": (_now() - timedelta(hours=since_hours)).isoformat()}
    if symbols:
        params["symbols"] = ",".join(symbols) if not isinstance(symbols, str) else symbols
    r = requests.get(ALPACA_NEWS, params=params, timeout=30,
                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    r.raise_for_status()
    out = []
    for a in r.json().get("news", []):
        out.append({"ts": a.get("created_at"), "title": a.get("headline"),
                    "source": a.get("source"), "symbols": a.get("symbols") or [],
                    "url": a.get("url"), "feed": "alpaca"})
    return out


def parse_rss(text):
    """Pure parser: Google News RSS -> [{ts, title, source, url}] (fixture-tested).

    Regex rather than an XML parser on purpose: the feed carries unescaped
    ampersands and stray HTML in titles that ElementTree rejects outright, and
    a dropped feed is worse than a slightly rough title.
    """
    out = []
    for it in re.findall(r"<item>(.*?)</item>", text or "", re.S):
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        u = re.search(r"<link>(.*?)</link>", it, re.S)
        s = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
        if not t or not d:
            continue
        try:
            ts = _utc(email.utils.parsedate_to_datetime(d.group(1).strip()))
        except (TypeError, ValueError):
            continue
        out.append({"ts": ts.isoformat(), "title": re.sub(r"<[^>]+>", "", t.group(1)).strip(),
                    "source": s.group(1).strip() if s else None,
                    "url": u.group(1).strip() if u else None, "feed": "google"})
    return out


#: Google News returns at most ~100 items per query. A topic saturating the cap
#: makes the measured rate a floor, not a rate, so it is reported rather than
#: silently smoothed over.
RSS_CAP = 100


def google_news(query, when="2d", timeout=30):
    """Keyless news breadth, windowed AT THE SOURCE.

    `when` is appended to the query as Google's `when:Nd` operator rather than
    applied as a post-filter, because the feed is relevance-ranked: a bare query
    mixes last year's coverage with this morning's, and dropping the old rows
    leaves an arbitrary remnant rather than a time window.
    """
    q = "{} when:{}".format(query, when) if when else query
    r = requests.get(GOOGLE_NEWS_RSS, headers=UA, timeout=timeout,
                     params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    r.raise_for_status()
    return parse_rss(r.text)


def _when_days(when):
    m = re.match(r"(\d+)\s*([hd])", str(when))
    if not m:
        return 1.0
    n = float(m.group(1))
    return n / 24.0 if m.group(2) == "h" else n


def news_rate(query, hot="1d", base="7d", pause=0.5, subject_terms=None):
    """Coverage rate in a hot window against a longer baseline window.

    Two queries, not one filtered list -- see google_news(). Returns
    {hot_n, base_n, hot_rate, base_rate, ratio, breaking, saturated, items}
    where the rates are articles per day and `ratio` is None when the baseline
    is empty (one article about something nobody covers is not a 10x spike, it
    is a sample of one).
    """
    hot_items = google_news(query, when=hot)
    time.sleep(pause)                      # be a good citizen on a free feed
    base_items = google_news(query, when=base)
    hot_items, gated = on_subject(hot_items, subject_terms)
    base_items, _ = on_subject(base_items, subject_terms)
    hd, bd = _when_days(hot), _when_days(base)
    hn, bn = len(hot_items), len(base_items)
    saturated = hn >= RSS_CAP or bn >= RSS_CAP
    hot_rate = hn / max(hd, 1e-9)
    # The baseline window contains the hot window, so net them out rather than
    # letting a spike inflate its own baseline.
    base_rate = max(0.0, bn - hn) / max(bd - hd, 1e-9)
    ratio = None if base_rate <= 0 else round(hot_rate / base_rate, 2)
    return {"hot_n": hn, "base_n": bn,
            "hot_rate": round(hot_rate, 2), "base_rate": round(base_rate, 2),
            "ratio": ratio, "breaking": base_rate <= 0 and hn > 0,
            "saturated": saturated, "subject_gated": gated, "items": hot_items}


# ------------------------------------------------------------ query building ----

_BOILER = re.compile(
    r"\b(will|who|what|which|the|a|an|be|is|are|in|on|at|to|of|by|for|and|or|"
    r"before|after|than|this|that|it|with|any|how|many|market|resolves?|yes|no)\b",
    re.I)


#: Lowercase particles that belong INSIDE a name. Without these, "Ursula von
#: der Leyen" parses as the name "Ursula" plus loose words, the query becomes
#: generic NATO coverage, and the spike detector reports a 6x signal built
#: entirely from Ukraine war headlines. Measured on live data, 2026-08-21.
_PARTICLES = r"(?:von|van|der|den|de|del|della|di|da|du|la|le|bin|al|ibn|of|st)"

#: A name token may itself be hyphenated ("Ocasio-Cortez") or apostrophised
#: ("O'Rourke"); without that, the surname splits off and searches as a common
#: word.
#: One name token: "Beto", "Ocasio-Cortez", "O'Rourke".
_TOK = r"[A-Z][a-z]*(?:['\u2019][A-Z]?[a-z]+)?(?:-[A-Z]?[a-z]+)*"

_NAME = re.compile(
    r"\b({t}(?:\s+(?:{p}\s+)*{t})+)\b".format(t=_TOK, p=_PARTICLES))

#: Capitalised question scaffolding that a greedy name regex swallows as a
#: first name ("Will Beto O'Rourke" -> "Will Beto").
_NOT_A_NAME = {"will", "who", "what", "which", "when", "where", "the", "a",
               "an", "is", "are", "does", "do", "can", "should"}


def extract_names(title):
    """Multi-word proper names, keeping particles, hyphens and apostrophes."""
    out = []
    for raw in _NAME.findall(re.sub(r"[?？]", " ", title or "")):
        parts = raw.split()
        while parts and parts[0].lower() in _NOT_A_NAME:
            parts.pop(0)
        name = " ".join(parts)
        if len(parts) >= 2 and len(name) > 3:
            out.append(name)
    return list(dict.fromkeys(out))


def query_for_contract(title, max_terms=6):
    """Contract title -> a news query.

    Keeps proper nouns and numbers, drops the question scaffolding, and quotes
    each full name so it searches as a phrase.
    """
    t = re.sub(r"[?？]", " ", title or "")
    names = extract_names(t)
    used = " ".join(names).lower()
    rest = [w for w in re.findall(r"[A-Za-z0-9']+", _BOILER.sub(" ", t))
            if len(w) > 2 and w.lower() not in used]
    terms = ['"{}"'.format(n) for n in names][:3]
    terms += list(dict.fromkeys(rest))[:max(0, max_terms - len(terms))]
    return " ".join(terms) if terms else (title or "").strip()


def on_subject(items, subject_terms):
    """Keep only headlines that actually mention the distinguishing subject.

    The counterpart of the pair matcher's subtitle gate, and the same lesson: a
    query built from a shared frame ("next NATO Secretary General") retrieves
    the frame's whole news cycle, and counting that as coverage OF ONE CANDIDATE
    manufactures a spike out of unrelated stories. With no subject to gate on,
    everything passes and the caller is told so via `subject_gated=False`.
    """
    if not subject_terms:
        return list(items), False
    low = [t.lower() for t in subject_terms]
    keep = [i for i in items
            if any(t in (i.get("title") or "").lower() for t in low)]
    return keep, True


# ------------------------------------------------------------------- signal ----

def price_move(series_ticker, market_ticker, window=3, lookback_days=30):
    """Absolute probability move over the last `window` daily bars, or None."""
    try:
        bars = fetch_prob_bars(series_ticker, market_ticker,
                               lookback_days=lookback_days)
    except Exception:
        return None
    if not bars or len(bars) <= window:
        return None
    return {"shift": round(bars[-1]["close"] - bars[-1 - window]["close"], 4),
            "last": bars[-1]["close"], "bars": len(bars)}


def classify_divergence(sp, mv, spike_ratio=2.5, move_threshold=0.03):
    """(box, why) for one contract. See the module docstring for the four boxes."""
    hot = bool(sp and (sp["breaking"] or
                       (sp["ratio"] is not None and sp["ratio"] >= spike_ratio)))
    moved = bool(mv and abs(mv["shift"]) >= move_threshold)
    if hot and moved:
        return "PRICED", "coverage spiked and the contract repriced"
    if hot and not moved:
        return "UNPRICED", ("coverage spiked and the contract has not moved -- "
                            "look, do not assume")
    if moved and not hot:
        return "SILENT_MOVE", "contract repriced with no coverage spike"
    return "QUIET", "no coverage spike, no repricing"


def watch(markets, hot="1d", base="7d", window=3, verbose=True):
    """[{ticker, box, spike, move, query}] for a list of Kalshi market dicts.

    `markets` items need `ticker`, `series_ticker` (or `event_ticker`) and
    `title`. One Google News request per market, so keep the list short.
    """
    out = []
    for m in markets:
        title = m.get("title") or m.get("ticker")
        q = query_for_contract(title)
        subj = extract_names(title)
        try:
            sp = news_rate(q, hot=hot, base=base, subject_terms=subj)
        except requests.RequestException as e:
            out.append({"ticker": m.get("ticker"), "query": q,
                        "error": "{}: {}".format(type(e).__name__, e)})
            continue
        items = sp.pop("items")
        series = m.get("series_ticker") or str(m.get("event_ticker", "")).split("-")[0]
        mv = price_move(series, m.get("ticker"), window=window)
        box, why = classify_divergence(sp, mv)
        row = {"ts": _now().isoformat(), "ticker": m.get("ticker"), "title": title,
               "query": q, "subject": subj, "box": box, "why": why,
               "spike": sp, "move": mv,
               "headline": items[0]["title"] if items else None}
        out.append(row)
        if verbose:
            print("  {:<34} {:<12} {}".format(
                str(m.get("ticker"))[:34], box,
                "hot={} base={} ratio={} move={}".format(
                    sp["hot_n"], sp["base_n"], sp["ratio"],
                    None if not mv else "{:+.3f}".format(mv["shift"]))))
    return out


def append_log(rows, path=None):
    path = path or app_path(LOG_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return path


# -------------------------------------------------------------------- CLI ----

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="News breadth against event-contract prices. DIRECTIONAL: "
                    "nothing here has a bounded worst case.")
    ap.add_argument("--query", help="one-off news query, print what comes back")
    ap.add_argument("--subject", help="--query: comma-separated terms a headline "
                                      "must contain to count (the subject gate)")
    ap.add_argument("--series", help="watch every open market in a Kalshi series")
    ap.add_argument("--tickers", help="comma-separated Kalshi market tickers to watch")
    ap.add_argument("--alpaca", action="store_true", help="dump the Alpaca real-time feed")
    ap.add_argument("--symbols", help="--alpaca: comma-separated tickers")
    ap.add_argument("--hot", default="1d", help="hot window, e.g. 6h or 1d")
    ap.add_argument("--base", default="7d", help="baseline window, e.g. 7d")
    ap.add_argument("--since-hours", type=float, default=72.0,
                    help="--alpaca only")
    ap.add_argument("--window", type=int, default=3, help="price-move lookback in bars")
    ap.add_argument("--limit", type=int, default=15, help="max markets to watch")
    ap.add_argument("--box", help="only show this box (UNPRICED/PRICED/SILENT_MOVE/QUIET)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    if a.alpaca:
        syms = a.symbols.split(",") if a.symbols else None
        for n in alpaca_news(symbols=syms, since_hours=a.since_hours):
            print("  {}  {:<10} {:<28} {}".format(
                n["ts"][:19], n["source"] or "", ",".join(n["symbols"])[:28],
                (n["title"] or "")[:70]))
        return 0

    if a.query:
        sp = news_rate(a.query, hot=a.hot, base=a.base,
                       subject_terms=a.subject.split(",") if a.subject else None)
        items = sp.pop("items")
        print("query: {}\n{}".format(a.query, sp))
        for i in items[:12]:
            print("  {}  {:<22} {}".format(i["ts"][:19], (i["source"] or "")[:22],
                                           i["title"][:66]))
        return 0

    from .market_data_kalshi import fetch_markets
    from .xvenue import kalshi_open_markets
    if a.tickers:
        from .xvenue import kalshi_quotes
        kq = kalshi_quotes(a.tickers.split(","))
        markets = [{"ticker": t, "title": q.get("title"),
                    "series_ticker": t.split("-")[0]} for t, q in kq.items()]
    elif a.series:
        markets = [{"ticker": m["ticker"], "title": m.get("title"),
                    "series_ticker": a.series}
                   for m in fetch_markets(series_ticker=a.series)][:a.limit]
    else:
        markets = [{"ticker": m["ticker"], "title": m["title"],
                    "series_ticker": str(m["event_ticker"]).split("-")[0]}
                   for m in kalshi_open_markets(pages=1)][:a.limit]

    print("watching {} markets".format(len(markets)))
    rows = watch(markets, hot=a.hot, base=a.base, window=a.window)
    path = append_log(rows, a.out)
    shown = [r for r in rows if not a.box or r.get("box") == a.box]
    print("\n-> {}".format(path))
    for r in shown:
        if r.get("box") in ("UNPRICED", "SILENT_MOVE"):
            print("\n{}  {}".format(r["box"], r["title"]))
            print("  query   : {}".format(r["query"]))
            print("  spike   : {}".format(r["spike"]))
            print("  move    : {}".format(r["move"]))
            print("  headline: {}".format(r["headline"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
