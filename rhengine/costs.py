"""
costs.py — one source of truth for transaction-cost assumptions.

Why this module exists (audit 2026-07-24, docs/AUDIT-2026-07-24.md FC-2):

The eleven CLIs in this package each carried their own `--cost-bps` default:
`0.0` in backtest/portfolio/optimize/nulltest/montecarlo/ic and `5.0` in
sanity/ablate/ablate_vbt/crossval/mc_refined. The headline artifact
(`tests/results/current_full.csv`) was forensically confirmed to have been
generated at **0 bps** — a regression of `r_multiple` on `1/stop_pct` over the
41,446 exact-at-stop fills returns intercept -1.0000, slope -0.000000 — despite
the project's own written rule "Always pass --cost-bps 5".

That is not a small bookkeeping detail for this strategy. Because R is
normalized by the ATR stop distance, cost in R is `bps/1e4 / stop_pct` for a
ROUND TRIP, and the median stop distance is ~4.2% of entry. So every 1 bp of
round-trip cost is ~0.0024 R, and the whole gross edge is ~0.08 R: about 33 bps
round trip erases it completely.

UNITS. Everything in this module, and `cost_bps` everywhere it is passed, means
ROUND TRIP — in and out, total. Until 2026-07-27 `cost_in_r`, `backtest._cost_r`
and `breakeven_cost_bps` all silently treated the value as PER SIDE and doubled
it, so a config saying 30 charged 60 and every reported breakeven was half the
true figure. See backtest._cost_r for the full account. If you need a per-side
number, name it per_side_bps and convert at the call site.

DEFAULT_ROUND_TRIP_BPS is 30, and that is now MEASURED rather than assumed. On
2026-07-27 (13:45 ET, mid-session) 296 live Robinhood quotes stratified across
ADV deciles and reweighted to the real 120,406-trade book gave 29.1 bps round
trip — 28.7 spread + 0.4 explicit fees. It ranges from 5.5 bps in the most
liquid decile to 98.1 bps in the least, and this book trades all of them at
roughly equal frequency. 30 is kept as a mildly conservative round number.

That measurement is an OPTIMISTIC bound on three counts: it is mid-session while
the engine fills at the open, it is quoted spread with no market impact, and it
is a single day's tape. PFOF price improvement is the one factor cutting the
other way. Re-measure with scripts/rh_spreads.py.

LIMITATION — this is still a scalar. The correct model is per-name and
liquidity-scaled, and the intended route to that (`estimate_round_trip_bps`,
Corwin-Schultz from daily high/low) was validated on 2026-07-25 and REJECTED:
it reads ~37x too high, putting SPY at 39 bps against a true sub-1 bp spread.
It now returns None. See its docstring for the measurement. Every reported
number therefore still carries a flat-cost assumption.

What IS known exactly is the explicit broker schedule — `robinhood_explicit_bps`
below — and it is negligible: 0.396 bps round trip trade-weighted over the real
120,406-trade book, about $0.10 on a $2,392 position. Robinhood charges no
commission. The cost that decides whether this system works is the bid-ask
spread, which no broker fee schedule contains and which cannot be recovered
from daily bars. It has to be sampled from live quotes during REGULAR trading
hours (after-hours books are near-empty and quote roughly 10x wide).

See docs/AUDIT-2026-07-24.md sections O-2 and Phase 1.
"""

import json
import os

import numpy as np
import pandas as pd

from ._paths import app_path

# Round-trip (in + out) transaction cost in basis points of notional.
# Overridable via config.json -> {"costs": {"round_trip_bps": N}}.
DEFAULT_ROUND_TRIP_BPS = 30.0

_MISSING = object()


def default_cost_bps(cfg=_MISSING):
    """Resolve the round-trip cost assumption, in bps.

    `cfg` may be a loaded config dict, a path to a config JSON file (str or
    os.PathLike — the notebooks pass a pathlib.Path), or omitted (in which case
    config.json is located via app_path). Order:
    cfg["costs"]["round_trip_bps"] -> DEFAULT_ROUND_TRIP_BPS. Never returns 0
    unless the config says so explicitly, which is then a documented choice.
    """
    if cfg is _MISSING or cfg is None or isinstance(cfg, (str, os.PathLike)):
        path = cfg if isinstance(cfg, (str, os.PathLike)) else app_path("config.json")
        try:
            with open(path) as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
    costs = (cfg or {}).get("costs") or {}
    try:
        return float(costs.get("round_trip_bps", DEFAULT_ROUND_TRIP_BPS))
    except (TypeError, ValueError):
        return DEFAULT_ROUND_TRIP_BPS


# ------------------------------------------------------- cap-tiered spreads
# The per-name model the LIMITATION note above says is missing. Corwin-Schultz
# was the intended route and was rejected (see estimate_round_trip_bps, ~37x
# too high); this is the other route — a published market-cap → spread prior,
# applied per name, with the repo's own measurements used as the cross-check.
#
# UNITS, and this is the one that costs 2x if you get it wrong. These are
# QUOTED SPREADS as a fraction of mid, in bps. A quoted spread IS the round
# trip: you cross the half-spread going in and the half-spread coming out.
# So the numbers below are ROUND TRIP bps directly — NO factor of 2. Same
# convention as config.json `execution._units_note`. `cost_in_r` and
# `backtest._cost_r` both silently doubled this until 2026-07-27.
#
# Source: literature ranges supplied 2026-08-01 (Oxford academic / Stern /
# CMC). They are ranges, not point estimates, because spread varies by float,
# volume and volatility inside every bucket. `point` is the GEOMETRIC mean of
# the range, which is the right centre for a band spanning an order of
# magnitude — the arithmetic mean of 100-600 is 350 and would put the typical
# microcap near the worst case.
#
# CROSS-CHECK against what this repo has measured itself, and the two agree:
#   * `estimate_round_trip_bps`'s validation table records true round trips of
#     SPY 0.7, INTC 1.5, BABA 3.0 bps — inside or below the 1-5 mega/large band.
#   * 296 live Robinhood quotes (2026-07-27) over the legacy engine's
#     min-$500k/day universe gave 5.5 bps in the most liquid ADV decile and
#     98.1 in the least — 5.5 sits in the large-cap band, 98.1 at the top of
#     the small-cap band. Trade-weighted the same capture gave 29.1 bps, BELOW
#     this table's small-cap point estimate of 44.7, which is expected:
#     Robinhood's tradeable set is more liquid than the full small-cap
#     population and PFOF buys some price improvement.
#
# So treat `point` as a mildly conservative central case and use `lo`/`hi` for
# the bracket. It is still a prior, not a measurement of YOUR fills.
CAP_TIERS = (
    # name,          min_cap,   max_cap,      lo,    hi
    ("mega_large",    10e9,     float("inf"),  1.0,    5.0),
    ("mid",            2e9,     10e9,          5.0,   20.0),
    ("small",        300e6,     2e9,          20.0,  100.0),
    ("micro",          0.0,     300e6,       100.0,  600.0),
)

TIER_NAMES = tuple(t[0] for t in CAP_TIERS)


def cap_tier(market_cap):
    """Label one market cap (in dollars) with its liquidity tier.

    Returns None for a missing or non-positive cap rather than guessing. A
    name with no cap must not silently be treated as mega-cap — that is the
    direction that flatters the book.
    """
    try:
        cap = float(market_cap)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(cap) or cap <= 0:
        return None
    for name, lo_cap, hi_cap, _lo, _hi in CAP_TIERS:
        if lo_cap <= cap < hi_cap:
            return name
    return None


def round_trip_bps_for_cap(market_cap, mode="point", unknown_bps=None):
    """Round-trip cost in bps for one name, from its market cap.

    `mode` is "point" (geometric mean of the tier's range), "lo" (optimistic
    edge) or "hi" (pessimistic edge). Run the two edges as a bracket; a result
    that only survives at `lo` has not survived.

    An unclassifiable cap falls back to `unknown_bps`, defaulting to the
    config's flat round trip — deliberately NOT to the mega-cap 1-5 bps, since
    "we could not price this name" must never make it look cheap.
    """
    tier = cap_tier(market_cap)
    if tier is None:
        return float(default_cost_bps() if unknown_bps is None else unknown_bps)
    for name, _lo_cap, _hi_cap, lo, hi in CAP_TIERS:
        if name == tier:
            if mode == "lo":
                return float(lo)
            if mode == "hi":
                return float(hi)
            return float(np.sqrt(lo * hi))
    raise KeyError(tier)                                   # unreachable


def bps_for_caps(caps, mode="point", unknown_bps=None):
    """Per-name round-trip bps for a Series of market caps (index: ticker)."""
    caps = pd.Series(caps, dtype=float)
    return caps.map(lambda c: round_trip_bps_for_cap(c, mode, unknown_bps))


def tier_of_caps(caps):
    """Per-name tier label for a Series of market caps. `None` -> 'unknown'."""
    caps = pd.Series(caps, dtype=float)
    return caps.map(lambda c: cap_tier(c) or "unknown")


def book_effective_bps(weights, caps, mode="point", unknown_bps=None):
    """The weight-average round-trip bps a book actually faces.

    This is the number to quote instead of a flat scalar. A cap-weighted S&P
    500 book is ~all mega-cap, so its effective cost is a few bps — applying
    the legacy engine's 30, calibrated on a min-$500k/day microcap scan
    universe, overstates it by an order of magnitude.

    NOTE this weights by POSITION size, which answers "what does holding this
    book cost per unit of turnover". It assumes turnover is spread across names
    in proportion to weight. Where it is not — a book that churns its small
    names and holds its large ones — the honest figure is turnover-weighted,
    which `alpha/book.py::run_book` computes exactly by charging each name's
    own bps against its own |Δw|.
    """
    w = pd.Series(weights, dtype=float)
    w = w[w > 0]
    if w.empty:
        return float("nan")
    b = bps_for_caps(pd.Series(caps, dtype=float).reindex(w.index),
                     mode, unknown_bps)
    return float((w * b).sum() / w.sum())


def resolve_cost_bps(arg_value, cfg=_MISSING):
    """For CLIs: use --cost-bps when the user passed one, else the config default.

    Every CLI in this package declares `--cost-bps` with `default=None` and
    routes through here, so there is exactly one place that decides what
    "unspecified cost" means. Pass the CLI's --config value as `cfg` so a
    non-default config's costs block is honored.
    """
    if arg_value is not None:
        return float(arg_value)
    return default_cost_bps(cfg)


def cost_in_r(entry, stop, cost_bps):
    """ROUND-TRIP cost expressed in R (risk units) for one trade.

    Mirrors backtest._cost_r. Kept here so the sanity battery, the ablation
    tools and the backtest cannot drift apart on the arithmetic — see that
    function for the 2026-07-27 units fix (this shared a 2x double-count with
    it, and both were corrected together).
    """
    if not cost_bps or entry <= 0 or entry <= stop:
        return 0.0
    stop_pct = (entry - stop) / entry
    if stop_pct <= 0:
        return 0.0
    return (cost_bps / 10000.0) / stop_pct


def breakeven_cost_bps(trades):
    """ROUND-TRIP bps at which a trade list's mean R reaches zero.

    trades: iterable of dicts with entry, stop, r_multiple.

    Returns None only when the book has a non-positive mean R — there is no
    edge for cost to erode. Raises ValueError when NOT ONE row carried usable
    entry/stop/r_multiple, because that is a broken input, not a finding. Those
    two used to share the None return, and it misled: handed the mc_refined
    harvests (which carry r_multiple but no entry/stop) it reported "no edge"
    for every sleeve, including ones with a +0.14 gross mean R.

    Units fixed 2026-07-27 alongside backtest._cost_r: this used to divide by
    `2.0 * mean_inv`, returning a PER-SIDE breakeven while its docstring and
    every caller said round trip. Reported breakevens were therefore half the
    real number.
    """
    inv = []
    rs = []
    for t in trades:
        entry, stop = t.get("entry"), t.get("stop")
        r = t.get("r_multiple")
        if entry is None or stop is None or r is None:
            continue
        if entry <= 0 or entry <= stop:
            continue
        inv.append(entry / (entry - stop))
        rs.append(float(r))
    if not rs:
        raise ValueError(
            "breakeven_cost_bps: no row carried usable entry/stop/r_multiple. "
            "This is a malformed input, not a book without an edge — the "
            "mc_refined harvests, for instance, record r_multiple but not "
            "entry/stop. Do not read 'no edge' into this.")
    mean_r = sum(rs) / len(rs)
    if mean_r <= 0:
        return None
    mean_inv = sum(inv) / len(inv)
    if mean_inv <= 0:
        return None
    return mean_r / (mean_inv / 10000.0)


# --- Robinhood explicit fee schedule, US listed equities --------------------
# These are EXACT and they are also nearly irrelevant: measured over the real
# 120,406-trade book they come to 0.396 bps round trip, about $0.10 on a $2,392
# position. Robinhood charges no commission; what costs money is the SPREAD,
# which is not a Robinhood fee at all — it is the market's price for immediacy.
#
# VERIFY BEFORE RELYING ON THESE. Both rates reset periodically (the SEC resets
# Section 31 annually to hit a revenue target and has ranged $5.10-$27.80 per
# $1M in recent years; FINRA last raised the TAF in Jan 2025). Last confirmed
# against the published schedules for 2025; re-check Robinhood's current fee
# schedule before quoting them.
SEC_FEE_PER_DOLLAR = 27.80 / 1e6      # Section 31, SELLS only
FINRA_TAF_PER_SHARE = 0.000166        # SELLS only
FINRA_TAF_CAP = 8.30                  # per trade


def robinhood_explicit_bps(price, shares=None):
    """Round-trip Robinhood fees in bps of notional for one trade at `price`.

    Commission is zero. Both regulatory fees apply on the SELL only, so the
    round trip pays each once. The TAF is per SHARE, so in bps it scales as
    1/price — a $1.50 stock pays ~1.1 bps and a $150 stock ~0.011 bps. That is
    the only part of the published schedule that varies enough to notice, and
    it argues mildly against the sub-$5 end of this universe.

    `shares` only matters for the $8.30 TAF cap, which needs ~50,000 shares to
    bind and never does at this account size.
    """
    if not price or price <= 0:
        return 0.0
    sec_bps = SEC_FEE_PER_DOLLAR * 10000.0
    taf_total = FINRA_TAF_PER_SHARE * shares if shares else None
    if taf_total is not None and taf_total > FINRA_TAF_CAP:
        taf_bps = FINRA_TAF_CAP / (price * shares) * 10000.0
    else:
        taf_bps = FINRA_TAF_PER_SHARE / price * 10000.0
    return sec_bps + taf_bps


def estimate_round_trip_bps(bars, lookback=20):
    """DO NOT USE — Corwin-Schultz (2012) high-low spread estimate. It does not
    work on this data and is kept only so the failure stays documented.

    Reference: Corwin & Schultz, "A Simple Way to Estimate Bid-Ask Spreads from
    Daily High and Low Prices", Journal of Finance 67(2), 2012.
    https://doi.org/10.1111/j.1540-6261.2012.01729.x

    VALIDATED 2026-07-25 AND REJECTED. Measured against names whose real
    round-trip spreads are known to be 1-6 bps, over the 730d massive cache:

        name   true~   this fn   zero-neg   +overnight-adj
        SPY      0.7      39.1       24.2             28.5
        INTC     1.5     153.5       89.9            103.7
        BABA     3.0      97.0       37.8             54.5
        AMC      6.0     196.1      138.0            147.2
        median ratio to truth:  37x        24x              26x

    Two separate problems, and fixing the first does not save it:

    1. THIS IMPLEMENTATION discards negative per-pair estimates instead of
       zeroing them, then takes the median of the survivors. CS estimates are
       noisy and roughly symmetric about the true spread, so for a tight name
       nearly every positive draw is noise — this returns the median of the
       upper tail. The paper says set negatives to zero and KEEP them.
    2. THE METHOD has no power here regardless. It separates spread from
       volatility using the fact that spread does not scale with time. A 0.5 bp
       spread inside a 1% daily range is 1/200th of the signal it is measuring.
       CS was built for an era of 50-200 bp spreads; it cannot resolve modern
       ones from daily bars.

    Returns None always. Get the spread from live bid/ask (Robinhood
    get_equity_price_book / get_equity_quotes during REGULAR hours — after-hours
    books are empty and quote ~10x wide), or from an intraday feed. See
    robinhood_explicit_bps for the part of cost that IS knowable offline.
    """
    return None


def _corwin_schultz_unvalidated(bars, lookback=20):
    """The original CS implementation, retained for reproducibility only.

    Kept so the 2026-07-25 validation table above can be regenerated. Its output
    is wrong by a factor of ~37; do not wire it into anything.
    """
    import math

    if not bars or len(bars) < lookback + 1:
        return None
    window = bars[-(lookback + 1):]
    spreads = []
    for i in range(1, len(window)):
        h1, l1 = window[i - 1]["high"], window[i - 1]["low"]
        h2, l2 = window[i]["high"], window[i]["low"]
        if min(h1, l1, h2, l2) <= 0:
            continue
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        hi2 = max(h1, h2)
        lo2 = min(l1, l2)
        gamma = math.log(hi2 / lo2) ** 2
        denom = 3.0 - 2.0 * math.sqrt(2.0)
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / denom \
            - math.sqrt(gamma / denom)
        s = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        if s > 0:
            spreads.append(s)
    if not spreads:
        return 0.0
    spreads.sort()
    median = spreads[len(spreads) // 2]
    return max(0.0, median * 10000.0)  # spread fraction -> round-trip bps
