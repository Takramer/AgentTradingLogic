"""
execution.py — Module 7 execution / liquidity gates.

Adopted from docs/AGENT-SPEC.md Module 7 on 2026-07-30, and it is the ONLY
thing adopted from that spec's Modules 1-7. The evidence audit
(docs/AGENT-SPEC-VERDICT.md) rejected every alpha claim in the corpus; the
spread gate survived because it is not an alpha claim at all. It is arithmetic
hygiene: if the round-trip spread on a name exceeds the edge you are trying to
capture, the trade cannot pay, and no backtest is required to know that.

WHAT THIS MODULE CANNOT DO, AND WHY THAT MATTERS
------------------------------------------------
The engine is a DAILY-BAR backtest. Daily bars contain no bid and no ask, so
this gate CANNOT be applied retrospectively and none of the recorded results
change by one basis point when it is switched on. It binds on the live path
only, where a real quote exists.

That limitation is not routed around. The obvious route — synthesise a spread
from daily high/low — is `costs.estimate_round_trip_bps` (Corwin-Schultz), and
it was validated on 2026-07-25 and REJECTED at ~37x too high (SPY at 39 bps
against a true sub-1 bp spread). It returns None. Do not reintroduce a
synthetic spread here to make the gate "work" in the backtest; that would put
a fabricated number in the cost path, which is the exact failure the
2026-07-24 audit exists to prevent.

So this module is deliberately small and pure. The functions take a bid and an
ask that somebody else obtained from a live quote.

CALIBRATION AGAINST WHAT THIS REPO MEASURED
-------------------------------------------
The spec's 1.0% default is a round number from the source corpus, not a
measurement. This repo's own measurement (296 live Robinhood quotes,
mid-session 2026-07-27, reweighted to the real 120,406-trade book) is 29.1 bps
round trip, ranging 5.5 bps in the most liquid ADV decile to 98.1 bps in the
least. See `costs.py`.

Read those units carefully, because the obvious reading is wrong:

    spread_pct     = (ask - bid) / mid       <- the QUOTED spread as a fraction
    round-trip bps = spread_pct * 10000      <- NO factor of 2

There is no doubling, and the reason is worth stating because getting it wrong
in either direction is a 2x error in the number that decides everything. A
market buy pays the ask, which is a HALF-spread above mid; a market sell hits
the bid, another half-spread. In and out is therefore two halves — exactly one
quoted spread. `spread_pct` is already a round-trip quantity.

Verified against this repo's own measurement rather than asserted: the least
liquid ADV decile quotes a 0.976% median spread and `costs.py` records that
decile at 98.1 bps round trip; the most liquid quotes 0.052% against a recorded
5.5 bps. Both tie at 1x. (An earlier draft of this module doubled it and would
have reported the whole universe at 58 bps.)

So a 1.0% spread gate admits names costing ~100 bps round trip in spread alone,
against a gross edge of roughly 0.08 R and a measured breakeven near 33 bps —
about 3x looser than the point where the edge is already gone. It is kept as
the DEFAULT because that is what the source says and this repo does not
silently retune source parameters. `spread_gate_pct` is configurable precisely
so the house number can be lower, and `bps_round_trip()` exists so the
comparison is always made in the units the cost model uses.

WHAT THE GATE ACTUALLY REJECTS (measured 2026-07-30)
----------------------------------------------------
It is not decoration. Run against the two stratified quote captures, the 1%
gate rejects roughly half of the least-liquid ADV decile on both days
(14/29 and 14/29), plus part of deciles 1 and 2, and essentially nothing above
~$6M/day. `universe_filters.min_dollar_volume` of $500k does NOT imply a sub-1%
spread. See `scripts/rh_spreads.py gate`.

MODULE 7 PARTS DELIBERATELY NOT IMPLEMENTED
-------------------------------------------
* Market orders for "highly liquid" single stocks. `GAP-7.1` — no source
  defines "highly liquid". Rather than invent a threshold, `order_type()`
  returns LIMIT always, which is the conservative reading of the same rule.
* ETF underlying-basket liquidity assessment (spec: judge an ETF by its
  creation/redemption basket, not its screen volume). This repo excludes ETFs
  from the equity scan outright (`universe_filters.exclude_etfs`), so the rule
  has nothing to act on.
* Slippage buffer on stop-order conversion (`GAP-7.5` — no size given).
"""

from .costs import default_cost_bps  # noqa: F401  (re-exported for callers)

# Spec default, docs/AGENT-SPEC.md Module 7 `spread_gate_pct` (NB p.199, 878).
# Expressed as a FRACTION of mid, for ONE crossing of the spread.
DEFAULT_SPREAD_GATE_PCT = 0.01

# Spec block-routing gates (NB p.199, 758, 878). Recorded for completeness;
# at this account size neither has ever bound.
BLOCK_GATE_SHARES = 2000
BLOCK_GATE_NOTIONAL = 10_000_000.0


def spread_pct(bid, ask):
    """Quoted spread as a fraction of mid — already a ROUND-TRIP cost.

    Returns None when the quote is unusable — a missing side, a non-positive
    price, or a crossed book (bid > ask). None means "no opinion", and callers
    must treat it as a REJECT rather than a pass: a name you cannot get a
    quote for is not a name you can size a trade in.

    After-hours books routinely produce garbage here (2026-07-25 close: SOUN
    quoted 6.06/22.18, LSTR 189.00/322.25 — spreads of 100%+). Those are real
    quotes, not bad data, and the gate correctly rejects them. Sample and gate
    during regular hours.
    """
    if bid is None or ask is None:
        return None
    try:
        bid = float(bid)
        ask = float(ask)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid


def bps_round_trip(bid, ask):
    """The spread expressed the way every cost number in this repo is: ROUND
    TRIP basis points, directly comparable to `costs.round_trip_bps`.

    The conversion is a scale change and nothing else — no factor of 2. See the
    module docstring for why, and for the measurement that pins it. Do not
    "fix" this by doubling: that is the same class of error as the 2x
    double-count removed from `backtest._cost_r` on 2026-07-27, and
    `tests/test_execution.py` checks it against `costs.py`'s recorded deciles.

    Returns None on an unusable quote.
    """
    pct = spread_pct(bid, ask)
    if pct is None:
        return None
    return pct * 10000.0


def resolve_spread_gate_pct(cfg=None):
    """Read `execution.spread_gate_pct` out of a loaded config dict.

    Mirrors `costs.default_cost_bps`: config wins, spec default otherwise, and
    a malformed value falls back rather than raising. Kept deliberately dumb —
    it takes an already-loaded dict, because the only caller that needs config
    loading is the CLI.
    """
    try:
        block = (cfg or {}).get("execution") or {}
        return float(block.get("spread_gate_pct", DEFAULT_SPREAD_GATE_PCT))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_SPREAD_GATE_PCT


def spread_gate(bid, ask, max_pct=DEFAULT_SPREAD_GATE_PCT):
    """Module 7's gate: reject when `(ask - bid) / mid > max_pct`.

    Returns (passed, spread_pct, reason):
        (True,  0.0021, "")                     tradeable
        (False, 0.0180, "spread 1.80% > 1.00%") too wide
        (False, None,   "no usable quote")      missing / crossed / stale book

    The boundary is exclusive, matching the spec's `< 1.0%` supported range: a
    spread exactly at the gate passes.
    """
    pct = spread_pct(bid, ask)
    if pct is None:
        return False, None, "no usable quote"
    if pct > max_pct:
        return False, pct, "spread {:.2%} > {:.2%}".format(pct, max_pct)
    return True, pct, ""


def needs_block_desk(shares=None, notional=None):
    """True when an order trips either spec block-routing gate.

    Recorded from the spec (>2,000 shares OR >$10M notional route to an ETF
    capital-markets or block desk) and implemented because it is trivial, not
    because it binds. At this repo's position sizing it never has. Kept so
    that if the account ever grows into it, the rule is already here rather
    than being rediscovered.
    """
    if shares is not None and shares > BLOCK_GATE_SHARES:
        return True
    if notional is not None and notional > BLOCK_GATE_NOTIONAL:
        return True
    return False


def order_type(asset_type="stock"):
    """Always "limit".

    The spec permits market orders for "highly liquid single stocks in the
    regular session" and mandates limit for ETFs and options. `GAP-7.1` leaves
    "highly liquid" undefined, and this repo does not fill source gaps from
    general knowledge, so the exemption cannot be evaluated and the mandatory
    branch governs everything.

    The parameter is accepted so call sites read explicitly and so a future
    definition of `GAP-7.1` has an obvious place to land.
    """
    return "limit"
