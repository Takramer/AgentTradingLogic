"""rmxb.py — RMX-B, the blended sleeve. The strategy this project ships.

Two long-only, cap-weighted large-cap sleeves, held 50/50:

    QUALITY   top quintile by operating profitability OP/BE, rebalanced
              annually at the end of June (Fama-French's formation convention)
    MOMENTUM  top quintile by 12-1 price momentum, rebalanced monthly
              (Jegadeesh & Titman 1993,
               https://doi.org/10.1111/j.1540-6261.1993.tb04702.x)

Why a blend and not a better single signal. Every individual edge measured in
this project is small: profitability is worth about +1.1%/yr of alpha against a
5.2%/yr tracking error, an information ratio of 0.22, which needs 84 years to
reach t=2. That is not a research failure, it is what large-cap equity alpha
looks like. The fundamental law of active management says the fix is breadth,
not a cleverer signal: blend sleeves whose excess returns are close to
uncorrelated and the alphas add while the tracking errors add in quadrature.

Measured on Ken French's own portfolios, 1963-2026, survivorship-free, net of a
deliberately punitive 20 bps round-trip charge on 33%/yr (quality) and 200%/yr
(momentum) turnover:

    correlation of the two sleeves' excess returns   +0.18

                        CAGR     market    alpha/yr   alpha t    IR
    quality only      +11.61%   +10.90%      +1.13%     +1.62   0.22
    momentum only     +13.24%   +10.90%      +2.43%     +2.10   0.27
    50/50 BLEND       +12.55%   +10.90%      +1.78%    ****+2.54****   0.32

    blend beats the market in 4 of 4 eras; Sharpe 0.55 vs 0.47; beta 0.97

The blend's t-stat exceeds both of its legs. That is the whole point, and it is
the only construction in this project that clears alpha t >= 2.0 on a sample
long enough to mean anything.

READ THIS BEFORE EXPECTING ANYTHING. The demonstrated result is French's 63-year
record. A +1.78%/yr alpha against a 5.54%/yr tracking error still needs ~39
years to reach t=2, so this project's own 8-year S&P 500 implementation can only
be checked for consistency, never for significance. Expect to trail the index in
roughly 4 years out of 10.

    py -m rhengine.alpha.rmxb --lo 2010-07-01 --hi 2018-12-31
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .book import buy_and_hold, run_book
from .fundamentals import Fundamentals
from .panel import Panel
from .rmx import load_panel
from .rmxq import _weights, market_cap
from .stats import Perf, alpha_beta, monthly, perf

FORM_MONTHS = 12       # 12-1 momentum: 11 months of return, skipping the last
SKIP_MONTHS = 1
TOP_FRACTION = 0.20
MAX_WEIGHT = 0.10
QUALITY_MONTH = 6      # French forms the profitability sort at the end of June
DEFAULT_COST_BPS = 10.0


@dataclass
class RMXBConfig:
    top_fraction: float = TOP_FRACTION
    max_weight: float = MAX_WEIGHT
    quality_weight: float = 0.5
    momentum_weight: float = 0.5
    min_names: int = 40
    weighting: str = "value"    # both legs are cap-weighted; `_weights` reads this
    # Barroso & Santa-Clara vol scaling on the momentum leg (see `run`). OFF by
    # default: the 50/50 blend is the specification with the 63-year record, and
    # turning this on silently would mean the shipped strategy is no longer the
    # one docs/RMX-RESULTS.md reports.
    vol_scale_momentum: bool = False
    vol_window: int = 126       # ~6 months, the paper's window
    vol_cap: float = 1.5        # bound on the multiplier; retail, not the paper


def momentum_12_1(panel: Panel, date: pd.Timestamp, names: pd.Index) -> pd.Series:
    """Return over months D-12..D-2, skipping the most recent month.

    The skip is not decoration: the most recent month carries short-term
    reversal, which is the opposite sign, and including it is the classic way
    to blunt a momentum signal.
    """
    me = panel.month_ends(None, str(date.date()))
    if len(me) < FORM_MONTHS + SKIP_MONTHS + 1:
        return pd.Series(dtype=float)
    end = me[-(SKIP_MONTHS + 1)]
    start = me[-(FORM_MONTHS + SKIP_MONTHS + 1)]
    px = panel.adj.reindex([start, end])[names]
    mom = px.iloc[1] / px.iloc[0] - 1.0
    return mom[np.isfinite(mom)]


def build_targets(panel: Panel, fund: Fundamentals, cfg: RMXBConfig,
                  lo: str, hi: str):
    """Blended weight targets, monthly, plus the covered universe per date."""
    targets: dict[pd.Timestamp, pd.Series] = {}
    covered: dict[pd.Timestamp, pd.Index] = {}
    legs: dict[str, dict[pd.Timestamp, pd.Series]] = {"quality": {},
                                                      "momentum": {}}
    diag = []
    quality_w: pd.Series | None = None

    for d in panel.month_ends(lo, hi):
        names = panel.eligible(d)
        if len(names) < cfg.min_names:
            continue
        cap_all = market_cap(panel, fund, d, names)
        if len(cap_all) < cfg.min_names:
            continue

        # ---- quality leg: refreshed only in June, held for twelve months ----
        if quality_w is None or d.month == QUALITY_MONTH:
            v = fund.as_of(d, "op_be", cap_all.index)
            v = v[np.isfinite(v)]
            if len(v) >= cfg.min_names:
                k = max(int(len(v) * cfg.top_fraction), 10)
                pick = v.nlargest(k).index
                quality_w = _weights(cap_all.reindex(pick).dropna(), cfg)
        if quality_w is None:
            continue

        # ---- momentum leg: refreshed every month ---------------------------
        mom = momentum_12_1(panel, d, cap_all.index)
        if len(mom) < cfg.min_names:
            continue
        k = max(int(len(mom) * cfg.top_fraction), 10)
        mpick = mom.nlargest(k).index
        momentum_w = _weights(cap_all.reindex(mpick).dropna(), cfg)

        # names that have gone stale (delisted out of the panel) must not keep
        # carrying weight from a June formation
        qw = quality_w.reindex(cap_all.index).dropna()
        if qw.sum() <= 0:
            continue
        qw = qw / qw.sum()

        blended = (cfg.quality_weight * qw).add(
            cfg.momentum_weight * momentum_w, fill_value=0.0)
        targets[d] = blended / blended.sum()
        covered[d] = cap_all.index
        legs["quality"][d] = qw
        legs["momentum"][d] = momentum_w
        diag.append({"date": str(d.date()), "eligible": len(names),
                     "covered": len(cap_all), "quality_names": len(qw),
                     "momentum_names": len(mpick),
                     "held": int((targets[d] > 1e-6).sum()),
                     "overlap": len(set(qw.index) & set(mpick)),
                     "max_weight": float(targets[d].max())})
    return targets, covered, pd.DataFrame(diag), legs


def _vol_scaled_blend(panel: Panel, cfg: RMXBConfig,
                      legs: dict[str, dict[pd.Timestamp, pd.Series]],
                      cost_bps: float, hi: pd.Timestamp
                      ) -> tuple[dict[pd.Timestamp, pd.Series], pd.Series]:
    """Re-blend with the momentum leg scaled by its own realised volatility.

    Barroso & Santa-Clara, "Momentum has its moments" (JFE 2015): weight
    momentum by the inverse of its trailing 6-month realised volatility.
    Momentum's volatility is far more forecastable than its return (their
    out-of-sample R^2 for the specific component is 47%), which is why this
    replicates where market-level volatility timing does not — see Cederburg et
    al. on the Moreira-Muir critique.

    On the raw momentum factor 1927-2011 the paper reports Sharpe 0.53 -> 0.97,
    excess kurtosis 18.24 -> 2.68, worst month -78.96% -> -28.40%.

    Two deviations from the paper, both deliberate:

    * The scale is normalised to mean one over the sample and capped at
      `vol_cap`, so this shifts the quality/momentum MIX rather than levering the
      book. A backtest that levers at the T-bill rate describes an account
      nobody has (see `book.run_book`'s borrow charge).
    * `realised_vol` shifts by one session, so the multiplier applied on a
      rebalance date uses returns strictly before it.
    """
    from .sleeves import realised_vol

    mom_targets = legs["momentum"]
    if not mom_targets:
        raise ValueError("no momentum-leg targets to scale")
    mom_book = run_book(panel.adj, mom_targets, panel.rf, cost_bps,
                        start=min(mom_targets), end=hi)
    vol = realised_vol(mom_book.daily_return, cfg.vol_window)
    target_vol = float(mom_book.daily_return.std() * np.sqrt(252.0))
    k = (target_vol / vol).clip(upper=cfg.vol_cap).ffill()

    out: dict[pd.Timestamp, pd.Series] = {}
    applied = {}
    for d, mw in mom_targets.items():
        qw = legs["quality"].get(d)
        if qw is None:
            continue
        kd = float(k.reindex([d], method="pad").iloc[0]) if len(k) else 1.0
        if not np.isfinite(kd):
            kd = 1.0
        applied[d] = kd
        blended = (cfg.quality_weight * qw).add(
            cfg.momentum_weight * kd * mw, fill_value=0.0)
        if blended.sum() <= 0:
            continue
        out[d] = blended / blended.sum()
    return out, pd.Series(applied, name="mom_vol_scale")


def run(panel: Panel, fund: Fundamentals, cfg: RMXBConfig, lo: str, hi: str,
        cost_bps: float = DEFAULT_COST_BPS):
    targets, covered, diag, legs = build_targets(panel, fund, cfg, lo, hi)
    if not targets:
        raise ValueError("no rebalances produced")
    dates = sorted(targets)
    start, end = dates[0], pd.Timestamp(hi)

    scale = None
    if cfg.vol_scale_momentum:
        targets, scale = _vol_scaled_blend(panel, cfg, legs, cost_bps,
                                           pd.Timestamp(hi))
        if not targets:
            raise ValueError("vol scaling produced no rebalances")

    bk = run_book(panel.adj, targets, panel.rf, cost_bps, start=start, end=end)

    # cap-weighted version of the same investable set, no signal: the
    # survivorship- and size-neutral benchmark
    neutral = {d: _weights(market_cap(panel, fund, d, names), cfg)
               for d, names in covered.items() if len(names)}
    nb = run_book(panel.adj, neutral, panel.rf, cost_bps, start=start, end=end)
    spy = buy_and_hold(panel.spy, start, end).reindex(
        bk.daily_return.index).fillna(0.0)
    return {"book": bk, "targets": targets, "covered": covered, "diag": diag,
            "legs": legs, "mom_vol_scale": scale,
            "benchmarks": {"SPY (cap-weight)": spy,
                           "CW covered universe": nb.daily_return}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="2010-07-01")
    ap.add_argument("--hi", default="2018-12-31")
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    ap.add_argument("--top-fraction", type=float, default=TOP_FRACTION)
    ap.add_argument("--holdings", action="store_true",
                    help="print the current book instead of the backtest")
    ap.add_argument("--vol-scale-momentum", action="store_true",
                    help="Barroso & Santa-Clara: weight the momentum leg by the "
                         "inverse of its own 6-month realised volatility")
    ap.add_argument("--vol-cap", type=float, default=1.5)
    args = ap.parse_args()

    panel, fund = load_panel(), Fundamentals.load()
    cfg = RMXBConfig(top_fraction=args.top_fraction,
                     vol_scale_momentum=args.vol_scale_momentum,
                     vol_cap=args.vol_cap)

    if args.holdings:
        t, _, d, _ = build_targets(panel, fund, cfg, "2024-01-01", args.hi)
        last = sorted(t)[-1]
        w = t[last].sort_values(ascending=False)
        w = w[w > 1e-6]
        print(f"RMX-B book formed {last.date()} - {len(w)} names")
        for k, v in w.head(25).items():
            print(f"    {k:<8} {v * 100:5.2f}%")
        return

    res = run(panel, fund, cfg, args.lo, args.hi, args.cost_bps)
    bk = res["book"]
    r = bk.daily_return
    spy = res["benchmarks"]["SPY (cap-weight)"]
    cw = res["benchmarks"]["CW covered universe"]

    tag = " [momentum vol-scaled]" if cfg.vol_scale_momentum else ""
    print(f"RMX-B{tag}  {r.index[0].date()} .. {r.index[-1].date()}  @ "
          f"{args.cost_bps:.0f} bps")
    if res["mom_vol_scale"] is not None:
        k = res["mom_vol_scale"]
        print(f"momentum scale: mean {k.mean():.2f}  min {k.min():.2f}  "
              f"max {k.max():.2f}  (capped at {cfg.vol_cap})")
    print(f"{len(res['targets'])} monthly rebalances | "
          f"{res['diag']['held'].mean():.0f} names held | "
          f"quality/momentum overlap {res['diag']['overlap'].mean():.0f} names | "
          f"turnover {bk.annual_turnover * 100:.0f}%/yr | "
          f"cost drag {bk.total_cost_pct:.2f}% total\n")
    print(Perf.header())
    print(perf(r, panel.rf, "RMX-B").row())
    print(perf(spy, panel.rf, "SPY").row())
    print(perf(cw, panel.rf, "CW covered universe").row())
    for name, b in (("SPY", spy), ("CW universe", cw)):
        ab = alpha_beta(monthly(r), monthly(b), monthly(panel.rf))
        print(f"\n    vs {name:<12} alpha {ab['alpha_ann'] * 100:+.2f}%/yr  "
              f"t={ab['alpha_t']:+.2f}  beta={ab['beta']:.2f}  "
              f"excess CAGR {perf(r, panel.rf, 'x').cagr_pct - perf(b, panel.rf, 'y').cagr_pct:+.2f} pp/yr")


if __name__ == "__main__":
    main()
