"""
options_costs.py — what an option trade actually costs, in three units.

WHY THIS EXISTS. `docs/VIDEO-INTAKE-2026-07-31.md` recorded that this repo had
**no options cost model at all**, which made every options proposal unfalsifiable
here. It also recorded — wrongly, from the 2026-07-30 spike — that Robinhood
returns NULL for IV and greeks. Re-spiked 2026-07-31 through the MCP server:
`implied_volatility`, delta, gamma, theta, vega, rho, `open_interest` and
`volume` are all **populated on quotes**. This module is the arithmetic that
became possible as a result.

It contains NO strategy, NO structure selection and NO entry rule — same
discipline as `market_data_options.py`, and for the same reason: a module that
only measures cannot produce a false positive the way a backtest can.

THE THREE UNITS, AND WHY ONE NUMBER IS NOT ENOUGH

  1. `spread_bps_premium` = spread / mark.
     Cost against the CAPITAL DEPLOYED. This is the number that looks alarming
     (SPY ATM weeklies ~230 bps vs 30 bps for equities) and it is the right
     one when comparing option premium against option premium.

  2. `spread_bps_delta` = spread / (|delta| * spot).
     Cost per dollar of UNDERLYING EXPOSURE — the like-for-like comparison
     against `costs.round_trip_bps`, because a share of SPY is one delta.
     Measured on the captured snapshot, short-dated SPY options cost **1-4 bps**
     here against the equity benchmark of 30 — an order of magnitude CHEAPER,
     which is the opposite of the naive conclusion and is entirely down to
     leverage. Long-dated is the reverse (Dec 745C: 18 bps, and rising).

  3. `theta_bps_per_day` = |theta| / mark.
     The cost of TIME, which equities do not have at all. This is what kills
     short-dated options: a 1-DTE ATM SPY call bleeds ~12%/day of premium.
     Any spread-only cost comparison is meaningless without it.

THE HONEST SUMMARY IS #3, NOT #2. An option position financed at a competitive
cost per delta-dollar still has to out-run theta, and `breakeven_move_pct` below
is how far the underlying must travel in one session just to break even on
spread plus decay. Compare that against the daily move the strategy claims to
predict before believing anything.

Usage:
    py -m rhengine.options_costs                      # the captured snapshot
    py -m rhengine.options_costs --json path.json
"""

from __future__ import annotations

import argparse
import json
import math
import os

SNAPSHOT = os.path.join("research_data", "options", "spy_quotes_2026-07-31.json")

# The equity benchmark this is measured against (config.json costs.round_trip_bps).
EQUITY_ROUND_TRIP_BPS = 30.0


def spread_bps_premium(bid, ask, mark=None):
    """Round-trip cost as bps of the PREMIUM. Crossing the spread once costs
    half of it from mid, so a full in-and-out costs the whole spread."""
    mark = mark if mark else (bid + ask) / 2.0
    if mark <= 0 or ask < bid:
        return None
    return 1e4 * (ask - bid) / mark


def spread_bps_delta(bid, ask, delta, spot):
    """Round-trip cost as bps of UNDERLYING EXPOSURE.

    One contract on 100 shares carries |delta| * 100 shares of exposure, and the
    100s cancel: cost/exposure = spread / (|delta| * spot). This is the only one
    of the three directly comparable to costs.round_trip_bps, because that is
    also quoted against notional."""
    if spot <= 0 or ask < bid:
        return None
    d = abs(delta)
    if d <= 1e-9:
        return None
    return 1e4 * (ask - bid) / (d * spot)


def theta_bps_per_day(theta, mark):
    """Daily decay as bps of premium. Positive number = premium lost per day."""
    if mark <= 0:
        return None
    return 1e4 * abs(theta) / mark


def breakeven_move_pct(bid, ask, theta, delta, spot, days=1.0):
    """How far the UNDERLYING must move, in percent, to cover a round-trip
    spread plus `days` of decay.

    This is the number that decides whether an option expresses a view more
    cheaply than the stock. Solving delta * (spot * move) = spread + days*|theta|
    for move gives spread and theta both in premium dollars, delta converting
    underlying dollars into premium dollars."""
    if spot <= 0 or ask < bid:
        return None
    d = abs(delta)
    if d <= 1e-9:
        return None
    premium_needed = (ask - bid) + days * abs(theta)
    return 100.0 * premium_needed / (d * spot)


def implied_forward(call_mark, put_mark, strike, r, t_years):
    """Forward implied by put-call parity: F = (C - P) * e^(rT) + K.

    A CONSISTENCY CHECK, not a cost. If this lands far from spot, the call and
    put quotes are not contemporaneous (or the rate/dividend assumption is off)
    and any IV comparison between them is measuring staleness, not skew."""
    return (call_mark - put_mark) * math.exp(r * t_years) + strike


def analyse(contract, spot, days_ahead=1.0):
    """One contract -> the three cost units plus the breakeven move."""
    bid, ask = float(contract["bid"]), float(contract["ask"])
    mark = float(contract.get("mark") or (bid + ask) / 2.0)
    delta, theta = float(contract["delta"]), float(contract["theta"])
    return {
        "exp": contract["exp"], "strike": float(contract["strike"]),
        "type": contract["type"], "mark": mark, "iv": float(contract["iv"]),
        "delta": delta, "volume": contract.get("volume"),
        "open_interest": contract.get("open_interest"),
        "prem_bps": spread_bps_premium(bid, ask, mark),
        "delta_bps": spread_bps_delta(bid, ask, delta, spot),
        "theta_bps": theta_bps_per_day(theta, mark),
        "be_move_pct": breakeven_move_pct(bid, ask, theta, delta, spot,
                                          days_ahead),
    }


def _dte(exp_iso, asof_iso):
    from datetime import date
    y, m, d = (int(x) for x in exp_iso.split("-"))
    ay, am, ad = (int(x) for x in asof_iso[:10].split("-"))
    return (date(y, m, d) - date(ay, am, ad)).days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=SNAPSHOT)
    ap.add_argument("--days", type=float, default=1.0,
                    help="decay days included in the breakeven move")
    args = ap.parse_args()

    snap = json.load(open(args.json))
    spot = float(snap["spot"]["last_trade_price"])
    asof = snap["captured_at"]
    rows = [analyse(c, spot, args.days) for c in snap["contracts"]]

    print(f"{snap['underlying']} spot {spot:.2f} | captured {asof}")
    print(f"equity benchmark: {EQUITY_ROUND_TRIP_BPS:.0f} bps round trip "
          f"(config.json costs.round_trip_bps)\n")
    print(f"{'exp':<12}{'DTE':>4}{'strike':>8}{'ty':>5}{'mark':>8}{'IV':>7}"
          f"{'delta':>7}{'prem bps':>10}{'delta bps':>11}{'theta bp/d':>12}"
          f"{'BE move%':>10}{'volume':>9}")
    for r in rows:
        print(f"{r['exp']:<12}{_dte(r['exp'], asof):>4}{r['strike']:>8.0f}"
              f"{r['type'][:1].upper():>5}{r['mark']:>8.2f}{r['iv']:>7.3f}"
              f"{r['delta']:>+7.3f}{r['prem_bps']:>10.0f}{r['delta_bps']:>11.0f}"
              f"{r['theta_bps']:>12.0f}{r['be_move_pct']:>10.2f}"
              f"{r['volume']:>9}")

    print("\n  prem bps  = round-trip spread / premium  (capital deployed)")
    print(f"  delta bps = round-trip spread / (|delta|*spot)  <- compare to "
          f"{EQUITY_ROUND_TRIP_BPS:.0f}")
    print("  theta bp/d= daily decay / premium  (equities have no analogue)")
    print(f"  BE move%  = underlying move needed to cover spread + "
          f"{args.days:g}d decay")

    # Parity consistency where a call and put share expiry+strike.
    by_key = {}
    for c in snap["contracts"]:
        by_key.setdefault((c["exp"], c["strike"]), {})[c["type"]] = c
    print("\nput-call parity check (r=4%, no dividend) — implied forward vs spot:")
    for (exp, k), pair in sorted(by_key.items()):
        if "call" not in pair or "put" not in pair:
            continue
        t = max(_dte(exp, asof), 0) / 365.0
        f = implied_forward(float(pair["call"]["mark"]),
                            float(pair["put"]["mark"]), float(k), 0.04, t)
        print(f"  {exp} {k:.0f}: forward {f:8.2f}  spot {spot:.2f}  "
              f"diff {f - spot:+.2f}")
    print("  A large diff means the quotes are not contemporaneous (or the rate/")
    print("  dividend assumption is wrong) — not that an arbitrage exists.")


if __name__ == "__main__":
    main()
