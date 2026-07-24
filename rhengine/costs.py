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
normalized by the ATR stop distance, cost in R is `2 * bps/1e4 / stop_pct`, and
the median stop distance is 4.7% of entry. Measured on that artifact:

    +0 bps   mean R +0.0572   sum R  +6,577
    +5 bps   mean R +0.0316   sum R  +3,638
    +10 bps  mean R +0.0061   sum R    +700
    +11.2 bps ................................ BREAKEVEN
    +20 bps  mean R -0.0450   sum R  -5,176

An 11 bps assumption is the difference between a profitable and an unprofitable
system. It must not live as a per-CLI default that anyone can forget to pass.

DEFAULT_ROUND_TRIP_BPS is deliberately set to 30, not 5. The universe floor is
`min_dollar_volume = 500_000` (config.json), i.e. names trading $500k/day, where
round-trip cost (half-spread x2 + impact) is routinely 30-100+ bps. 5 bps is a
mega-cap assumption applied to a microcap universe.

LIMITATION — this is still a scalar. The correct model is per-name and
liquidity-scaled; `estimate_round_trip_bps` below is a documented placeholder
implementing the Corwin-Schultz high-low spread estimator, which needs no data
this repo does not already have. It is NOT yet wired into the backtest loop.
Until it is, every reported number carries a flat-cost assumption. See
docs/AUDIT-2026-07-24.md sections O-2 and Phase 1.
"""

import json

from ._paths import app_path

# Round-trip (in + out) transaction cost in basis points of notional.
# Overridable via config.json -> {"costs": {"round_trip_bps": N}}.
DEFAULT_ROUND_TRIP_BPS = 30.0

_MISSING = object()


def default_cost_bps(cfg=_MISSING):
    """Resolve the round-trip cost assumption, in bps.

    `cfg` may be a loaded config dict, a path to a config JSON file, or omitted
    (in which case config.json is located via app_path). Order:
    cfg["costs"]["round_trip_bps"] -> DEFAULT_ROUND_TRIP_BPS. Never returns 0
    unless the config says so explicitly, which is then a documented choice.
    """
    if cfg is _MISSING or cfg is None or isinstance(cfg, str):
        path = cfg if isinstance(cfg, str) else app_path("config.json")
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
    """Round-trip cost expressed in R (risk units) for one trade.

    Mirrors backtest._cost_r. Kept here so the sanity battery, the ablation
    tools and the backtest cannot drift apart on the arithmetic.
    """
    if not cost_bps or entry <= 0 or entry <= stop:
        return 0.0
    stop_pct = (entry - stop) / entry
    if stop_pct <= 0:
        return 0.0
    return 2.0 * (cost_bps / 10000.0) / stop_pct


def breakeven_cost_bps(trades):
    """Round-trip bps at which a trade list's mean R reaches zero.

    trades: iterable of dicts with entry, stop, r_multiple. Returns None when
    the book has no positive edge to erode. This is the single most decision-
    relevant number for this strategy family and belongs in every report.
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
        return None
    mean_r = sum(rs) / len(rs)
    if mean_r <= 0:
        return None
    mean_inv = sum(inv) / len(inv)
    if mean_inv <= 0:
        return None
    return mean_r / (2.0 * mean_inv / 10000.0)


def estimate_round_trip_bps(bars, lookback=20):
    """PLACEHOLDER, NOT WIRED IN — Corwin-Schultz (2012) high-low spread estimate.

    Estimates the effective bid-ask spread from daily high/low prices alone, so
    it can run on the OHLC bars already in massive_cache with no new data feed.
    Reference: Corwin & Schultz, "A Simple Way to Estimate Bid-Ask Spreads from
    Daily High and Low Prices", Journal of Finance 67(2), 2012.
    https://doi.org/10.1111/j.1540-6261.2012.01729.x

    Returns an estimated ROUND-TRIP cost in bps (2 x half-spread), floored at 0.
    Returns None when there is not enough history.

    This does not yet include market impact, which for a $500k-ADV name at any
    meaningful size dominates the spread. Do not treat this as a complete cost
    model. It exists so the wiring in Phase 1 has a real estimator to call.
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
