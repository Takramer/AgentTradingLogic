"""
crypto.py — Kalshi's BTC/ETH complex: implied vol, term structure, barriers.

Why this and not the temperature module's approach
--------------------------------------------------
A temperature contract has an external fair value: a forecast estimates the
number the contract resolves on. Bitcoin does not work that way. Nobody
forecasts tomorrow's BTC price, and anyone claiming to is selling something.

What bitcoin contracts DO have is **one underlying settled at many horizons**:

    KXBTC15M     fifteen_min   up/down against the price 15 minutes prior
    KXBTCD       hourly        BRTI above K  (318 strikes, 3.68M contracts/24h)
    KXBTC        hourly        the range/bucket variant of the same ladder
    KXBTCMAXMON  monthly       one-touch:  "ever above B before month end"
    KXBTCMINMON  monthly       one-touch:  "ever below B"
    KXBTCMAXW/Q/Y               weekly, quarterly, annual maxima

Those are not independent products. They are the same distribution sliced
differently, so they can be checked against **each other** with no external
data and no forecast. Three checks live here, in increasing order of how much
they assume:

1. `ladder_arbs()`  -- monotonicity. "BTC > 80k" implies "BTC > 79k", so the
   YES price must be non-increasing in strike. A violation is a locked profit
   with ZERO resolution risk: one venue, one contract family, one settlement
   rule. Structurally the same nested hedge as `xvenue_relations.K_BROADER`,
   minus the cross-venue risk entirely.
2. `implied_vol()`  -- fit one lognormal sigma per expiry across the ladder,
   giving a term structure that should be roughly flat.
3. `one_touch_up()` -- price the monthly barriers off the vanilla ladder's own
   sigma. For a driftless walk P(touch) is very close to 2x P(finish above);
   if the barrier ladder disagreed with the vanilla ladder, that is an internal
   inconsistency rather than a forecast.

What was measured, 2026-08-22
-----------------------------
**Ladder arbs: zero.** 708 strikes across KXBTCD, KXETHD and KXBTC, using
executable prices (ask on one leg, bid on the other) net of the Kalshi taker
fee. The ladder is internally coherent. Wide "mids" near the money are
one-sided books, not dislocations -- `mid` is not a price on an empty book, and
filtering on a real two-sided spread is mandatory before fitting anything.

**Term structure, fit rmse 0.015-0.024:**  0.9h 61.0% | 14.9h 47.8% | 159h 52.3%

**One-touch ratio: 2.03-2.06** against the vanilla ladder at every barrier --
the reflection principle, reproduced out of live market prices. The monthly
barrier ladder is consistent with the hourly vanilla ladder plus a smile (the
far barriers trade a little above a single-sigma model, which is what a smile
looks like). No systematic mispricing found.

**Realized vol, and a correction.** 3,594 hourly Coinbase bars, 2026-03-25 to
2026-08-22: **39.6%** annualized, and flat at 37-40% whether sampled at 1h, 2h,
4h, 6h, 12h or 24h -- so it is not microstructure noise. Recent windows: 30d
35.8%, 14d 43.2%, 7d 57.1%, 36h 87.3%.

An earlier pass here estimated realized vol at 86% from 36 hours of BRTI prints
and concluded implied was cheap. That was backwards: 36 hours is not a vol
estimate, it was a volatile stretch. Implied of 48-61% sits **above** 30-day
realized of 35.8%, which is an ordinary variance risk premium, not an edge.
Before acting on that premium, read `docs/OPTIONS-SOURCES-2026-08-01.md` §6 --
this repo already measured premium-selling against the published CBOE record
and found BXM's alpha is t=+0.62 against total return.

The settlement index is free, from Kalshi
-----------------------------------------
Contracts settle on CF Benchmarks' BRTI, whose API does not serve it publicly
(404/400, checked). It does not matter: `brti_history()` reads
`expiration_value` off settled hourly markets and recovers the actual
settlement prints, exactly spaced one hour apart.

That gives a free calibration target for a proxy. **Coinbase's 1-minute close at
the settlement instant is within 2.0 bps of BRTI (sd 4.1 bps)** against an
hourly move of ~92 bps -- so Coinbase is a sound spot proxy and supplies
unlimited history that Kalshi's settled book does not. Note the alignment:
BRTI is a 60-second average ENDING at the hour. Comparing it to an hourly
candle close instead of the minute bar at that instant gives a basis sd of
94 bps and looks like a broken proxy. It is not; it is a timestamp bug.

The 15-minute up/down markets
-----------------------------
`updown_breakeven()` prices the only thing about them that matters. They sit at
about 50/50, the Kalshi fee peaks at 1.75 c/contract exactly there, so a 50 c
stake pays a 3.5% house edge and you must be right **51.75%** of the time to
break even. Over fifteen minutes at 40% annualized vol the underlying moves
about 0.21%, drift is nil, and there are 96 settlements a day to pay that edge
on. Nothing in this module will find you a 1.75-point directional edge on a
fifteen-minute bitcoin move.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import statistics as st
import sys
import time

import requests

from .market_data_kalshi import _kalshi_get
from .xvenue import kalshi_fee

COINBASE = "https://api.exchange.coinbase.com"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
EDT = dt.timezone(dt.timedelta(hours=-4))


def _ncdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ------------------------------------------------------------------ pricing ----

def digital(S, K, sigma, T):
    """P(S_T > K) under driftless GBM. T in years."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    return _ncdf((math.log(S / K) - 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T)))


def one_touch_up(S, B, sigma, T):
    """P(max_{t<=T} S_t >= B) for B > S, driftless GBM, reflection principle.

    Sign discipline matters here: both normal arguments must go strongly
    negative as the barrier moves away. Flipping the second one returns ~0.97
    for a barrier the spot will never reach, which is how the first version of
    this function claimed a 76-cent edge on an unreachable strike.
    """
    if T <= 0 or sigma <= 0:
        return 1.0 if S >= B else 0.0
    if B <= S:
        return 1.0
    mu = -0.5 * sigma * sigma
    v = sigma * math.sqrt(T)
    x = math.log(B / S)
    return min(1.0, _ncdf((-x + mu * T) / v)
               + math.exp(2.0 * mu * x / (sigma * sigma)) * _ncdf((-x - mu * T) / v))


def fit_sigma(points, S, T, lo=0.02, hi=5.0):
    """Least-squares lognormal sigma across a strike ladder.

    Grid then refine, on SQUARED error. Do not root-find on the signed sum: a
    digital above spot falls as vol rises while one below spot rises, so the
    sum is not monotone in sigma and bisection has no valid bracket. Doing that
    railed the fit to its bounds and produced a 500% implied vol.
    """
    if not points or T <= 0:
        return None

    def sse(s):
        return sum((digital(S, K, s, T) - p) ** 2 for K, p in points)

    n = 400
    best = min(((sse(lo + (hi - lo) * i / n), lo + (hi - lo) * i / n)
                for i in range(1, n + 1)))[1]
    for step in (0.005, 0.0005):
        best = min(((sse(best + d * step), best + d * step)
                    for d in range(-12, 13) if best + d * step > 0.001))[1]
    return best


def updown_breakeven(price=0.50, contracts=100):
    """What a 15-minute up/down actually costs you.

    Returns the hit rate required to break even. At the midpoint the Kalshi fee
    is at its maximum, which is precisely where these contracts trade.
    """
    fee = kalshi_fee(price, contracts) / contracts
    return {"price": price, "fee_per_contract": round(fee, 5),
            "fee_pct_of_stake": round(fee / price, 5) if price > 0 else None,
            "breakeven_hit_rate": round(price + fee, 5),
            "edge_needed_pts": round((price + fee - 0.5) * 100, 3)}


# ------------------------------------------------------------- market reads ----

def brti_history(series="KXBTCD", pages=6):
    """{datetime(EDT): BRTI print} from settled hourly markets.

    CF Benchmarks does not serve BRTI publicly. Kalshi does, for free, one hour
    at a time, in `expiration_value`.
    """
    out, cursor = {}, None
    for _ in range(pages):
        params = {"limit": 1000, "status": "settled", "series_ticker": series}
        if cursor:
            params["cursor"] = cursor
        body = _kalshi_get("/markets", params=params)
        markets = body.get("markets", [])
        for m in markets:
            g = re.match(r"[A-Z]+-(\d{2})([A-Z]{3})(\d{2})(\d{2})-", m.get("ticker", ""))
            v = m.get("expiration_value")
            if not g or g.group(2) not in MONTHS or v in (None, ""):
                continue
            try:
                out[dt.datetime(2000 + int(g.group(1)), MONTHS[g.group(2)],
                                int(g.group(3)), int(g.group(4)), tzinfo=EDT)] = float(v)
            except ValueError:
                pass
        cursor = body.get("cursor")
        if not cursor or not markets:
            break
    return out


def coinbase_hourly(hours=3000, product="BTC-USD", pause=0.25):
    """{unix_ts: close} hourly. Paged -- Coinbase serves 300 candles per call."""
    rows, end = {}, dt.datetime.now(dt.timezone.utc)
    for _ in range(max(1, hours // 300 + 1)):
        start = end - dt.timedelta(hours=300)
        r = requests.get(COINBASE + "/products/{}/candles".format(product),
                         params={"granularity": 3600, "start": start.isoformat(),
                                 "end": end.isoformat()}, timeout=30)
        if not r.ok:
            break
        data = r.json()
        if not data:
            break
        for c in data:
            rows[c[0]] = c[4]
        end = start
        time.sleep(pause)
    return rows


def spot(product="BTC-USD"):
    r = requests.get(COINBASE + "/products/{}/ticker".format(product), timeout=20)
    r.raise_for_status()
    return float(r.json()["price"])


def ladder(series, max_spread=0.05, min_p=0.03, max_p=0.97):
    """{close_time: [(strike, mid)]} for the live ladder, EXECUTABLE quotes only.

    A one-sided book has no mid. Half this ladder is untraded strikes quoted
    0.00/1.00, and averaging those produces a smooth-looking curve made of
    nothing. `max_spread` is the filter that makes every number downstream real.
    """
    body = _kalshi_get("/markets", params={"limit": 500, "status": "open",
                                           "series_ticker": series})
    out = {}
    for m in body.get("markets", []):
        g = re.search(r"above (\d+(?:\.\d+)?)", m.get("rules_primary") or "")
        if not g:
            continue
        bid = float(m.get("yes_bid_dollars") or 0)
        ask = float(m.get("yes_ask_dollars") or 0)
        if ask <= 0 or bid <= 0 or (ask - bid) > max_spread:
            continue
        p = (bid + ask) / 2.0
        if not (min_p < p < max_p):
            continue
        out.setdefault(m["close_time"], []).append((float(g.group(1)), p))
    for k in out:
        out[k].sort()
    return out


def ladder_arbs(series, contracts=100):
    """Monotonicity violations priced on executable quotes.

    "S > K_hi" implies "S > K_lo" for K_hi > K_lo, so p(K) must be
    non-increasing. When bid(K_hi) > ask(K_lo) the bound is broken: buy YES at
    K_lo and NO at K_hi and the worst case pays 1.

        S > K_hi : YES pays 1, NO pays 0  -> 1
        K_lo < S < K_hi : 1 + 1           -> 2
        S < K_lo : 0 + 1                  -> 1

    Guaranteed by logic AND by there being exactly one settlement rule -- the
    cross-venue version of this trade still has to survive two rulebooks.
    """
    body = _kalshi_get("/markets", params={"limit": 500, "status": "open",
                                           "series_ticker": series})
    byclose = {}
    for m in body.get("markets", []):
        g = re.search(r"above (\d+(?:\.\d+)?)", m.get("rules_primary") or "")
        if not g:
            continue
        byclose.setdefault(m["close_time"], []).append({
            "K": float(g.group(1)),
            "bid": float(m.get("yes_bid_dollars") or 0),
            "ask": float(m.get("yes_ask_dollars") or 0),
            "bsz": float(m.get("yes_bid_size_fp") or 0),
            "asz": float(m.get("yes_ask_size_fp") or 0)})
    found = []
    for ct, rows in byclose.items():
        rows.sort(key=lambda r: r["K"])
        for i, lo in enumerate(rows):
            if lo["ask"] <= 0:
                continue
            for hi in rows[i + 1:]:
                if hi["bid"] <= 0 or hi["bid"] <= lo["ask"]:
                    continue
                fee = (kalshi_fee(lo["ask"], contracts)
                       + kalshi_fee(1 - hi["bid"], contracts)) / contracts
                net = hi["bid"] - lo["ask"] - fee
                size = min(lo["asz"], hi["bsz"])
                if net > 0 and size > 0:
                    found.append({"close": ct, "buy_yes_at": lo["K"],
                                  "buy_no_at": hi["K"], "gross": round(hi["bid"] - lo["ask"], 4),
                                  "fee": round(fee, 4), "net": round(net, 4),
                                  "size": size})
    found.sort(key=lambda r: -r["net"])
    return found


def term_structure(series="KXBTCD", S=None):
    """[{close, hours, n, sigma, rmse}] -- one fitted vol per expiry."""
    S = S or spot()
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for ct, pts in sorted(ladder(series).items()):
        t = dt.datetime.fromisoformat(ct.replace("Z", "+00:00"))
        hours = (t - now).total_seconds() / 3600.0
        T = hours / (24 * 365)
        if len(pts) < 5 or T <= 0:
            continue
        sig = fit_sigma(pts, S, T)
        rmse = math.sqrt(sum((digital(S, K, sig, T) - p) ** 2 for K, p in pts) / len(pts))
        out.append({"close": ct, "hours": round(hours, 2), "n": len(pts),
                    "sigma": round(sig, 4), "sigma_over_T": round(sig * math.sqrt(T), 5),
                    "implied_move": round(S * sig * math.sqrt(T), 0),
                    "rmse": round(rmse, 4)})
    return out


def realized_vol(rows=None, windows=(168, 336, 720, None)):
    """Annualized realized vol from hourly closes, plus a sampling-horizon check.

    If the number moves a lot between 1h and 24h sampling, it is microstructure
    noise rather than volatility. Measured 2026-08-22 it did not: 37-40% across
    every horizon.
    """
    rows = rows if rows is not None else coinbase_hourly()
    ts = sorted(rows)
    lr = [math.log(rows[b] / rows[a]) for a, b in zip(ts, ts[1:])
          if rows[a] > 0 and rows[b] > 0]
    if len(lr) < 30:
        return {"error": "only {} returns".format(len(lr))}
    out = {"n": len(lr), "full": round(st.pstdev(lr) * math.sqrt(24 * 365), 4),
           "windows": {}, "by_sampling": {}}
    for w in windows:
        s = lr if w is None else lr[-w:]
        if len(s) >= 20:
            out["windows"]["{}h".format(w) if w else "full"] = \
                round(st.pstdev(s) * math.sqrt(24 * 365), 4)
    for h in (1, 2, 4, 6, 12, 24):
        agg = [math.log(rows[ts[i + h]] / rows[ts[i]])
               for i in range(0, len(ts) - h, h) if rows[ts[i]] > 0]
        if len(agg) > 20:
            out["by_sampling"]["{}h".format(h)] = \
                round(st.pstdev(agg) * math.sqrt(24 * 365 / h), 4)
    return out


def proxy_basis(pages=6, product="BTC-USD"):
    """BRTI settlement prints against the Coinbase minute close at that instant.

    Basis in bps. Compare against the ~92 bps hourly move to judge whether the
    proxy is usable.
    """
    brti = brti_history(pages=pages)
    if not brti:
        return {"error": "no settled prints"}
    ks = sorted(brti)
    r = requests.get(COINBASE + "/products/{}/candles".format(product),
                     params={"granularity": 60,
                             "start": (ks[-1] - dt.timedelta(hours=4)).isoformat(),
                             "end": ks[-1].isoformat()}, timeout=30)
    r.raise_for_status()
    cb = {dt.datetime.fromtimestamp(c[0], dt.timezone.utc): c[4] for c in r.json()}
    diffs = [(brti[t] - cb[t.astimezone(dt.timezone.utc)])
             / cb[t.astimezone(dt.timezone.utc)] * 1e4
             for t in ks if t.astimezone(dt.timezone.utc) in cb]
    if not diffs:
        return {"error": "no overlap; Coinbase minute candles cover ~5h"}
    return {"n": len(diffs), "mean_bps": round(st.mean(diffs), 2),
            "sd_bps": round(st.pstdev(diffs), 2),
            "max_abs_bps": round(max(abs(d) for d in diffs), 2)}


# -------------------------------------------------------------------- CLI ----

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--term", action="store_true", help="implied vol term structure")
    ap.add_argument("--realized", action="store_true", help="realized vol baseline")
    ap.add_argument("--arbs", action="store_true", help="ladder monotonicity scan")
    ap.add_argument("--touch", action="store_true", help="monthly one-touch vs the ladder")
    ap.add_argument("--basis", action="store_true", help="BRTI vs Coinbase proxy basis")
    ap.add_argument("--updown", action="store_true", help="15-minute up/down break-even")
    ap.add_argument("--series", default="KXBTCD")
    a = ap.parse_args(argv)
    did = False

    if a.updown:
        did = True
        print("15-minute up/down, Kalshi fee = ceil(0.07*C*P*(1-P)):")
        for px in (0.50, 0.45, 0.40, 0.30):
            r = updown_breakeven(px)
            print("  price {:.2f}  fee {:.4f} ({:.1%} of stake)  break-even hit rate "
                  "{:.4f}".format(px, r["fee_per_contract"], r["fee_pct_of_stake"],
                                  r["breakeven_hit_rate"]))
        print("  96 settlements a day. Nothing here finds a 1.75-point edge on a "
              "15-minute bitcoin move.")

    if a.basis:
        did = True
        print("BRTI vs Coinbase minute close at the settlement instant:", proxy_basis())

    if a.arbs:
        did = True
        for s in ("KXBTCD", "KXETHD", "KXBTC"):
            f = ladder_arbs(s)
            print("{:<10} {} monotonicity arbs net of fee".format(s, len(f)))
            for r in f[:5]:
                print("   {}  BUY YES {:,.0f} / NO {:,.0f}  net {:+.4f} size {:,.0f}".format(
                    r["close"][:16], r["buy_yes_at"], r["buy_no_at"], r["net"], r["size"]))

    if a.term:
        did = True
        S = spot()
        print("spot ${:,.0f}".format(S))
        print("  {:<18}{:>8}{:>5}{:>10}{:>12}{:>8}".format(
            "close", "hours", "n", "impl ann", "implied $", "rmse"))
        for r in term_structure(a.series, S):
            print("  {:<18}{:>8.1f}{:>5}{:>9.1f}%{:>12,.0f}{:>8.3f}".format(
                r["close"][:16], r["hours"], r["n"], r["sigma"] * 100,
                r["implied_move"], r["rmse"]))

    if a.realized:
        did = True
        rv = realized_vol()
        print("realized annualized vol (Coinbase hourly, n={}):".format(rv.get("n")))
        for k, v in rv.get("windows", {}).items():
            print("   last {:<6} {:5.1f}%".format(k, v * 100))
        print("  by sampling horizon (flat => not microstructure noise):")
        for k, v in rv.get("by_sampling", {}).items():
            print("   {:>4} returns {:5.1f}%".format(k, v * 100))

    if a.touch:
        did = True
        S = spot()
        ts = term_structure("KXBTCD", S)
        if not ts:
            print("no vanilla ladder to take sigma from")
            return 1
        sig = max(ts, key=lambda r: r["hours"])["sigma"]
        now = dt.datetime.now(dt.timezone.utc)
        body = _kalshi_get("/markets", params={"limit": 100, "status": "open",
                                               "series_ticker": "KXBTCMAXMON"})
        print("spot ${:,.0f}  sigma {:.0f}% from the {:.0f}h ladder".format(
            S, sig * 100, max(r["hours"] for r in ts)))
        print("  {:>10}{:>7}{:>7}{:>9}{:>10}{:>7}{:>8}".format(
            "barrier", "bid", "ask", "digital", "1-touch", "ratio", "edge"))
        rows = []
        for m in body.get("markets", []):
            g = re.search(r"[Aa]bove \$?([\d,]+(?:\.\d+)?)", m.get("yes_sub_title") or "")
            if not g:
                continue
            B = float(g.group(1).replace(",", ""))
            close = dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            T = max(0.0, (close - now).total_seconds() / (365 * 24 * 3600))
            if B <= S or T <= 0:
                continue
            bid = float(m.get("yes_bid_dollars") or 0)
            ask = float(m.get("yes_ask_dollars") or 0)
            dg, ot = digital(S, B, sig, T), one_touch_up(S, B, sig, T)
            rows.append((B, bid, ask, dg, ot))
        for B, bid, ask, dg, ot in sorted(rows):
            mid = (bid + ask) / 2
            print("  {:>10,.0f}{:>7.2f}{:>7.2f}{:>9.3f}{:>10.3f}{:>7}{:>+8.3f}".format(
                B, bid, ask, dg, ot,
                "{:.2f}".format(ot / dg) if dg > 1e-6 else "n/a", ot - mid))
        print("  ratio ~2.0 is the reflection principle, reproduced from live prices.")

    if not did:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
