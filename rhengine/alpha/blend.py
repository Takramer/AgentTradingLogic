"""blend.py -- RMX-C, the multi-sleeve blend, and the ablation harness for it.

RMX-B blended two sleeves and reached alpha t = +2.54 on French's 63 years
because their excess returns correlated only +0.18. This generalises that to N
sleeves and makes the leave-one-out test a single command, so "does this leg
earn its place" is answered with a number instead of an opinion.

    py -m rhengine.alpha.blend --lo 2010-07-01 --hi 2018-12-31
    py -m rhengine.alpha.blend --sleeves quality momentum --vol-scale momentum
    py -m rhengine.alpha.blend --ablate --lo 2010-07-01 --hi 2018-12-31

Why one book and not N books added together. Overlapping names net out before
they are traded: if the quality sleeve is buying AAPL while the reversal sleeve
sells it, a real account trades the difference. Summing separate books charges
both trades and overstates cost. So sleeve weights are combined into a single
target vector per rebalance date and `run_book` sees one portfolio.

    ---------------------------------------------------------------------
    WHAT THIS CANNOT DO. It cannot establish that any of this is real. The
    published record (docs/EXTERNAL-RESEARCH-2026-07.md) is the evidence; this
    is the implementation check. The 2019-2026 holdout is SPENT — RMX-D and
    RMX-Q were both measured on it. Ablation numbers from this module are
    diagnostics for construction choices, not new evidence of edge, and a
    sleeve should be added or dropped on its PUBLISHED record first.
    ---------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .book import buy_and_hold, run_book
from .fundamentals import Fundamentals
from .panel import Panel
from .rmx import load_panel
from .rmxq import _weights, cost_vector, market_cap
from .sleeves import (CALENDARS, DEFAULT_SLEEVES, REGISTRY, Sleeve, _Cfg,
                      build_sleeve, scale_by_own_vol)
from .stats import Perf, alpha_beta, monthly, perf

DEFAULT_COST_BPS = 10.0


@dataclass
class BlendConfig:
    sleeves: tuple[str, ...] = DEFAULT_SLEEVES
    weights: dict[str, float] | None = None      # None -> equal
    vol_scale: tuple[str, ...] = ("momentum",)   # Barroso, on the momentum leg
    vol_cap: float = 1.5
    max_weight: float = 0.10                     # cap on the COMBINED name weight
    min_names: int = 40
    # Turnover controls. Both are OFF by default: they change the shipped
    # strategy, and the published analogues are a strict quintile on the
    # signal's own calendar. See `--band` / `--momentum-calendar`.
    exit_fraction: float | None = None           # no-trade band, e.g. 0.30
    calendars: dict[str, str] | None = None      # per-sleeve calendar override

    def sleeve_weight(self, name: str) -> float:
        if self.weights:
            return float(self.weights.get(name, 0.0))
        return 1.0 / len(self.sleeves)

    def resolve(self, name: str) -> Sleeve:
        """The registry sleeve with this config's turnover overrides applied."""
        s = REGISTRY[name]
        changes = {}
        if self.exit_fraction is not None \
                and self.exit_fraction > s.top_fraction:
            changes["exit_fraction"] = float(self.exit_fraction)
        if self.calendars and name in self.calendars:
            cal = self.calendars[name]
            if cal not in CALENDARS:
                raise KeyError(f"unknown calendar {cal!r} for sleeve {name!r}; "
                               f"have {sorted(CALENDARS)}")
            changes["calendar"] = cal
        return replace(s, **changes) if changes else s


# ------------------------------------------------------------------- machinery
def _sleeve_daily(panel: Panel, targets: dict, cost_bps: float,
                  lo: pd.Timestamp, hi: pd.Timestamp) -> pd.Series:
    """A sleeve's own daily return series, needed before it can be vol-scaled."""
    if not targets:
        return pd.Series(dtype=float)
    bk = run_book(panel.adj, targets, panel.rf, cost_bps,
                  start=min(targets), end=hi)
    return bk.daily_return


def _as_of(targets: dict[pd.Timestamp, pd.Series],
           date: pd.Timestamp) -> pd.Series | None:
    """The sleeve's most recent target on or before `date`.

    An annual sleeve keeps its June weights while a weekly sleeve re-forms, so
    the blend must carry each leg forward rather than dropping it.
    """
    keys = [d for d in targets if d <= date]
    return targets[max(keys)] if keys else None


def build(panel: Panel, fund: Fundamentals, cfg: BlendConfig, lo: str, hi: str,
         cost_bps: float = DEFAULT_COST_BPS) -> dict:
    """Per-sleeve targets, the vol-scaling series, and the combined targets."""
    per_sleeve, per_covered, per_diag = {}, {}, {}
    for name in cfg.sleeves:
        sleeve: Sleeve = cfg.resolve(name)
        t, c, d = build_sleeve(panel, fund, sleeve, lo, hi, cfg.min_names)
        if not t:
            print(f"  sleeve {name}: no rebalances produced -- skipped")
            continue
        per_sleeve[name], per_covered[name], per_diag[name] = t, c, d

    if not per_sleeve:
        raise ValueError("no sleeve produced any rebalance")

    hi_ts = pd.Timestamp(hi)
    all_dates = pd.DatetimeIndex(
        sorted({d for t in per_sleeve.values() for d in t}))

    # ---- pass 1: each sleeve's own return series, for the vol scaling --------
    scale: dict[str, pd.Series] = {}
    sleeve_daily: dict[str, pd.Series] = {}
    for name, t in per_sleeve.items():
        r = _sleeve_daily(panel, t, cost_bps, pd.Timestamp(lo), hi_ts)
        sleeve_daily[name] = r
        if name in cfg.vol_scale and len(r) > 200:
            scale[name] = scale_by_own_vol(r, all_dates, cap=cfg.vol_cap)
        else:
            scale[name] = pd.Series(1.0, index=all_dates)

    # ---- pass 2: one combined target vector per rebalance date --------------
    cap_cfg = _Cfg(cfg.max_weight)
    combined: dict[pd.Timestamp, pd.Series] = {}
    covered_union: dict[pd.Timestamp, pd.Index] = {}
    rows = []
    for d in all_dates:
        acc = pd.Series(dtype=float)
        used = {}
        for name, t in per_sleeve.items():
            w = _as_of(t, d)
            if w is None:
                continue
            # a name that has left the panel cannot keep carrying weight from an
            # older formation date
            w = w[w.index.isin(panel.adj.columns)]
            live = panel.adj.loc[d, w.index].notna()
            w = w[live.to_numpy()]
            if w.empty or w.sum() <= 0:
                continue
            w = w / w.sum()
            k = cfg.sleeve_weight(name) * float(scale[name].get(d, 1.0))
            used[name] = k
            acc = acc.add(w * k, fill_value=0.0)
        if acc.empty or acc.sum() <= 0:
            continue
        # Scaling is relative between sleeves, not a leverage decision: the book
        # is renormalised to fully invested so a vol-scaled leg shifts the MIX
        # rather than the gross exposure. Chasing gross exposure with a retail
        # margin rate is a different strategy with a different cost.
        acc = acc / acc.sum()
        if acc.max() > cfg.max_weight:
            acc = _weights(acc, cap_cfg)
        combined[d] = acc
        uni = pd.Index([])
        for name in used:
            c = _as_of_index(per_covered[name], d)
            if c is not None:
                uni = uni.union(c)
        covered_union[d] = uni
        rows.append({"date": str(d.date()), "held": int((acc > 1e-6).sum()),
                     "max_weight": float(acc.max()),
                     **{f"k_{k}": round(v, 3) for k, v in used.items()}})

    return {"per_sleeve": per_sleeve, "per_covered": per_covered,
            "per_diag": per_diag, "sleeve_daily": sleeve_daily,
            "scale": scale, "targets": combined, "covered": covered_union,
            "diag": pd.DataFrame(rows)}


def _as_of_index(covered: dict[pd.Timestamp, pd.Index],
                 date: pd.Timestamp) -> pd.Index | None:
    keys = [d for d in covered if d <= date]
    return covered[max(keys)] if keys else None


def run(panel: Panel, fund: Fundamentals, cfg: BlendConfig, lo: str, hi: str,
        cost_bps: float = DEFAULT_COST_BPS) -> dict:
    built = build(panel, fund, cfg, lo, hi, cost_bps)
    targets = built["targets"]
    start, end = min(targets), pd.Timestamp(hi)
    bk = run_book(panel.adj, targets, panel.rf, cost_bps, start=start, end=end)

    # The cap-weighted version of exactly the same investable set, no signal:
    # survivorship- and size-neutral, so the difference is the ranking only.
    neutral = {}
    for d, names in built["covered"].items():
        if len(names) == 0:
            continue
        cap = market_cap(panel, fund, d, names)
        if len(cap):
            neutral[d] = _weights(cap, _Cfg(cfg.max_weight))
    nb = run_book(panel.adj, neutral, panel.rf, cost_bps, start=start, end=end)
    spy = buy_and_hold(panel.spy, start, end).reindex(
        bk.daily_return.index).fillna(0.0)
    return built | {"book": bk,
                    "benchmarks": {"SPY (cap-weight)": spy,
                                   "CW covered universe": nb.daily_return}}


# ----------------------------------------------------------------- reporting
def _line(label: str, r: pd.Series, bench: pd.Series, rf: pd.Series) -> str:
    ab = alpha_beta(monthly(r), monthly(bench), monthly(rf))
    p = perf(r, rf, label)
    return ("{:<22} CAGR {:>6.2f}%  vol {:>5.2f}%  SR {:>5.2f}  maxDD {:>7.2f}%  "
            "alpha {:>+6.2f}%/yr  t={:>+5.2f}  beta {:.2f}").format(
        label[:22], p.cagr_pct, p.vol_ann_pct, p.sharpe, p.max_dd_pct,
        ab["alpha_ann"] * 100, ab["alpha_t"], ab["beta"])


def report(cfg: BlendConfig, lo: str, hi: str, cost_bps: float,
           panel: Panel | None = None, fund: Fundamentals | None = None,
           quiet: bool = False) -> dict:
    panel = panel or load_panel()
    fund = fund or Fundamentals.load()
    res = run(panel, fund, cfg, lo, hi, cost_bps)
    bk = res["book"]
    r = bk.daily_return
    spy = res["benchmarks"]["SPY (cap-weight)"]
    cw = res["benchmarks"]["CW covered universe"]

    out = {"sleeves": list(res["per_sleeve"]), "cost_bps": (float(cost_bps) if not isinstance(cost_bps, pd.Series)
                        else "per-name tier"),
           # The data stamp. A result is only reproducible against the panel it
           # was computed on, and the panel is a mutable 150 MB cache — see
           # Panel.fingerprint. Quote this next to any number taken from here.
           "panel_fp": panel.fingerprint(lo, hi),
           "start": str(r.index[0].date()), "end": str(r.index[-1].date()),
           "n_rebalances": len(res["targets"]),
           "mean_held": float(res["diag"]["held"].mean()),
           "turnover_ann": bk.annual_turnover,
           "cost_drag_total_pct": bk.total_cost_pct,
           "vs": {}}
    for name, b in (("SPY", spy), ("CW universe", cw)):
        ab = alpha_beta(monthly(r), monthly(b), monthly(panel.rf))
        out["vs"][name] = ab
    p = perf(r, panel.rf, "RMX-C")
    out["perf"] = {"cagr_pct": p.cagr_pct, "vol_ann_pct": p.vol_ann_pct,
                   "sharpe": p.sharpe, "max_dd_pct": p.max_dd_pct,
                   "worst_month_pct": p.worst_month_pct,
                   "skew": p.skew, "kurtosis": p.kurtosis}

    if not quiet:
        label = (f"{cost_bps:.0f} bps" if not isinstance(cost_bps, pd.Series)
                 else f"per-name tier ({bk.effective_bps:.2f} bps eff)")
        print(f"\n=== RMX-C  {out['start']} .. {out['end']}  @ {label}"
              f"  panel {out['panel_fp']} ===")
        print(f"sleeves: {', '.join(res['per_sleeve'])}"
              + (f"  | vol-scaled: {', '.join(cfg.vol_scale)}"
                 if cfg.vol_scale else ""))
        print(f"{out['n_rebalances']} rebalances | {out['mean_held']:.0f} names held"
              f" | one-way turnover {bk.annual_turnover * 100:.0f}%/yr"
              f" | cost drag {bk.total_cost_pct:.2f}% total\n")
        print(Perf.header())
        print(perf(r, panel.rf, "RMX-C blend").row())
        for name, d in res["sleeve_daily"].items():
            if len(d) > 20:
                print(perf(d.reindex(r.index).dropna(), panel.rf,
                           f"  leg: {name}").row())
        print(perf(spy, panel.rf, "SPY").row())
        print(perf(cw, panel.rf, "CW covered universe").row())
        print()
        for name, b in (("SPY", spy), ("CW universe", cw)):
            ab = out["vs"][name]
            print(f"  vs {name:<14} alpha {ab['alpha_ann'] * 100:+.2f}%/yr  "
                  f"t={ab['alpha_t']:+.2f}  beta={ab['beta']:.2f}  n={ab['n']}mo")
        # The number that decides whether blending was worth doing: correlation
        # of the legs' EXCESS returns over the cap-weighted universe. Total
        # returns of two long-only equity sleeves correlate ~0.85 whatever the
        # signals are — that is shared market beta, not shared information, and
        # reading it as the latter would make every blend look pointless. RMX-B's
        # published +0.18 is an excess-return correlation.
        cw_m = monthly(cw)
        legs = pd.DataFrame(
            {k: monthly(v).sub(cw_m, fill_value=np.nan)
             for k, v in res["sleeve_daily"].items() if len(v) > 20}).dropna()
        if legs.shape[1] > 1:
            c = legs.corr()
            k = c.shape[0]
            rho = (c.to_numpy().sum() - k) / (k * (k - 1))
            out["mean_pairwise_excess_corr"] = float(rho)
            print(f"\n  leg EXCESS-return correlation vs CW universe "
                  f"(mean pairwise {rho:+.3f}):")
            print("  " + c.round(2).to_string().replace("\n", "\n  "))
            print(f"  theoretical IR multiple over the mean leg: "
                  f"{np.sqrt(k / (1 + (k - 1) * rho)):.2f}x")
    return out | {"_res": res, "_panel": panel, "_fund": fund}


def ablate(cfg: BlendConfig, lo: str, hi: str, cost_bps: float) -> pd.DataFrame:
    """Leave-one-out: what does the blend lose when each sleeve is removed?

    A sleeve that RAISES alpha t when removed is not paying rent. Note the
    warning at the top of this module: with ~100 monthly observations the
    standard error on these t-stats is large enough that only big differences
    mean anything. Treat a swing below ~0.5 in t as noise.
    """
    panel, fund = load_panel(), Fundamentals.load()
    rows = []
    full = report(cfg, lo, hi, cost_bps, panel, fund, quiet=True)
    rows.append({"config": "ALL", "n_sleeves": len(full["sleeves"]),
                 "cagr_pct": full["perf"]["cagr_pct"],
                 "sharpe": full["perf"]["sharpe"],
                 "max_dd_pct": full["perf"]["max_dd_pct"],
                 "alpha_spy_t": full["vs"]["SPY"]["alpha_t"],
                 "alpha_cw_t": full["vs"]["CW universe"]["alpha_t"],
                 "alpha_cw_pct": full["vs"]["CW universe"]["alpha_ann"] * 100,
                 "turnover_pct": full["turnover_ann"] * 100})
    for drop in cfg.sleeves:
        keep = tuple(s for s in cfg.sleeves if s != drop)
        if not keep:
            continue
        sub = BlendConfig(sleeves=keep,
                          vol_scale=tuple(v for v in cfg.vol_scale if v in keep),
                          vol_cap=cfg.vol_cap, max_weight=cfg.max_weight,
                          min_names=cfg.min_names)
        try:
            r = report(sub, lo, hi, cost_bps, panel, fund, quiet=True)
        except Exception as exc:                      # noqa: BLE001
            rows.append({"config": f"-{drop}", "error": str(exc)[:60]})
            continue
        rows.append({"config": f"-{drop}", "n_sleeves": len(r["sleeves"]),
                     "cagr_pct": r["perf"]["cagr_pct"],
                     "sharpe": r["perf"]["sharpe"],
                     "max_dd_pct": r["perf"]["max_dd_pct"],
                     "alpha_spy_t": r["vs"]["SPY"]["alpha_t"],
                     "alpha_cw_t": r["vs"]["CW universe"]["alpha_t"],
                     "alpha_cw_pct": r["vs"]["CW universe"]["alpha_ann"] * 100,
                     "turnover_pct": r["turnover_ann"] * 100})
    df = pd.DataFrame(rows).set_index("config")
    base = df.loc["ALL", "alpha_cw_t"]
    df["d_alpha_t"] = base - df["alpha_cw_t"]      # positive = the leg helps
    return df


def turnover_sweep(base: BlendConfig, lo: str, hi: str) -> pd.DataFrame:
    """Grid the two turnover controls against the cost that actually applies.

    Turnover only matters through cost, so the sweep is run at BOTH 10 bps (the
    research default) and 30 bps (config.json's modelled cost for this
    universe). A control that helps at 10 and hurts at 30, or the reverse, is
    the whole point of running both — a single cost assumption cannot tell you
    whether a turnover reduction paid for the gross return it gave up.

    Both controls are construction changes measured on a SPENT window, so this
    is a cost/turnover diagnostic and not evidence of edge. What it can settle
    is the mechanical question: does the band or the calendar buy back more in
    cost than it loses in signal?
    """
    panel, fund = load_panel(), Fundamentals.load()
    variants = [
        ("baseline (strict quintile, monthly mom)", None, None),
        ("band 0.25", 0.25, None),
        ("band 0.30", 0.30, None),
        ("band 0.40", 0.40, None),
        ("quarterly momentum", None, "quarterly"),
        ("band 0.30 + quarterly momentum", 0.30, "quarterly"),
    ]
    rows = []
    for label, band, cal in variants:
        cfg = BlendConfig(sleeves=base.sleeves, vol_scale=base.vol_scale,
                          vol_cap=base.vol_cap, max_weight=base.max_weight,
                          min_names=base.min_names, exit_fraction=band,
                          calendars={"momentum": cal} if cal else None)
        row = {"config": label}
        for bps in (10.0, 30.0):
            try:
                r = report(cfg, lo, hi, bps, panel, fund, quiet=True)
            except Exception as exc:                  # noqa: BLE001
                row[f"err@{bps:.0f}"] = str(exc)[:40]
                continue
            row[f"turn@{bps:.0f}"] = r["turnover_ann"] * 100
            row[f"cagr@{bps:.0f}"] = r["perf"]["cagr_pct"]
            row[f"alpha@{bps:.0f}"] = r["vs"]["CW universe"]["alpha_ann"] * 100
            row[f"t@{bps:.0f}"] = r["vs"]["CW universe"]["alpha_t"]
            row[f"held@{bps:.0f}"] = r["mean_held"]
        rows.append(row)
    df = pd.DataFrame(rows).set_index("config")
    print(f"\n=== turnover controls  {lo} .. {hi}  vs the CW covered universe ===")
    print(df.to_string(float_format=lambda v: f"{v:,.2f}"))
    print("\nturn = one-way turnover %/yr | alpha = %/yr vs the CW universe")
    print("A control earns its place only if it raises alpha AT 30 BPS, the cost")
    print("this universe is actually modelled at. Improvements at 10 bps alone")
    print("are an artefact of understating cost.")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="2010-07-01")
    # see rmxb.main: --holdings wants today, a backtest wants the in-sample
    # window, and sharing one default breaks --holdings silently.
    ap.add_argument("--hi", default=None)
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    ap.add_argument("--sleeves", nargs="*", default=list(DEFAULT_SLEEVES),
                    choices=sorted(REGISTRY))
    ap.add_argument("--vol-scale", nargs="*", default=["momentum"],
                    help="sleeves to scale by their own realised vol (Barroso). "
                         "DEFAULTS TO THE MOMENTUM LEG -- unlike rmxb.py, where "
                         "vol-scaling is off. Every RMX-C number in docs/RMX-C.md "
                         "was produced with this on. Pass a bare --vol-scale to "
                         "turn it off.")
    ap.add_argument("--vol-cap", type=float, default=1.5)
    ap.add_argument("--cost-model", default="flat",
                    choices=("flat", "tier-lo", "tier-point", "tier-hi"),
                    help="'flat' charges --cost-bps to every name. 'tier-*' "
                         "charges each name its market-cap tier's spread "
                         "(costs.CAP_TIERS); run lo and hi as a bracket. This "
                         "book is ~92%% mega-cap, so flat 30 overstates it "
                         "~10x -- see scripts/cap_cost_profile.py")
    ap.add_argument("--band", type=float, default=None, metavar="EXIT_FRACTION",
                    help="no-trade band: enter on the top 20%%, sell only on "
                         "falling out of the top EXIT_FRACTION (e.g. 0.30). "
                         "Cuts churn without changing the signal.")
    ap.add_argument("--momentum-calendar", default=None,
                    choices=sorted(CALENDARS),
                    help="rebalance the momentum leg on this calendar instead "
                         "of monthly. DEVIATES from Mom12m's published record.")
    ap.add_argument("--turnover-sweep", action="store_true",
                    help="grid the two turnover controls at 10 and 30 bps")
    ap.add_argument("--ablate", action="store_true",
                    help="leave-one-out over the chosen sleeves")
    ap.add_argument("--holdings", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.hi is None:
        args.hi = (dt.date.today().isoformat() if args.holdings
                   else "2018-12-31")

    cfg = BlendConfig(sleeves=tuple(args.sleeves),
                      vol_scale=tuple(args.vol_scale), vol_cap=args.vol_cap,
                      exit_fraction=args.band,
                      calendars=({"momentum": args.momentum_calendar}
                                 if args.momentum_calendar else None))

    if args.turnover_sweep:
        turnover_sweep(cfg, args.lo, args.hi)
        return

    if args.holdings:
        panel, fund = load_panel(), Fundamentals.load()
        built = build(panel, fund, cfg, "2023-01-01", args.hi, args.cost_bps)
        if not built["targets"]:
            raise SystemExit(
                f"no rebalance formed in 2023-01-01..{args.hi} -- the panel "
                f"probably does not reach that far. Check `py -m "
                f"rhengine.alpha.panel`.")
        last = max(built["targets"])
        w = built["targets"][last]
        w = w[w > 1e-6].sort_values(ascending=False)
        print(f"RMX-C book formed {last.date()} -- {len(w)} names")
        for k, v in w.head(30).items():
            print(f"    {k:<8} {v * 100:5.2f}%")
        return

    if args.ablate:
        df = ablate(cfg, args.lo, args.hi, args.cost_bps)
        print(f"\n=== RMX-C leave-one-out  {args.lo} .. {args.hi} "
              f"@ {args.cost_bps:.0f} bps ===")
        print(df.to_string(float_format=lambda v: f"{v:,.2f}"))
        print("\nd_alpha_t > 0 means the dropped sleeve was ADDING alpha t.")
        print("Swings below ~0.5 t are noise on this sample length.")
        return

    cost = args.cost_bps
    if args.cost_model != "flat":
        # Targets must exist before their caps can price them, so this is a
        # cheap extra `build` rather than a restructure of report().
        panel, fund = load_panel(), Fundamentals.load()
        built = build(panel, fund, cfg, args.lo, args.hi, args.cost_bps)
        cost = cost_vector(panel, fund, built["targets"],
                           mode=args.cost_model.split("-", 1)[1])

    out = report(cfg, args.lo, args.hi, cost)
    if args.json:
        print(json.dumps({k: v for k, v in out.items()
                          if not k.startswith("_")}, indent=2, default=float))


if __name__ == "__main__":
    main()
