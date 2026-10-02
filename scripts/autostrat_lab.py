"""
autostrat_lab.py — parallel research harness for the legacy engine.

Why this exists: run_backtest_on is pure and in-memory, but scoring is O(bars^2)
per ticker (score_ticker re-derives indicators over bars[:t+1] every bar). One
sleeve over 793 tickers x 2,900 bars is ~2 hours single-threaded. Ticker shards
are independent, so this fans them across cores and reconstitutes the trade list.

DATA: research_data/yahoo_long — 793 EVER-S&P500 tickers, full history, so the
universe is not current-membership and is far less survivorship-biased than
yahoo_cache. Prices are ratio-adjusted by adjclose/close so splits cannot open
fake gaps (see docs/CLAUDE-PITFALLS.md #1).

COST: round trip, resolved through rhengine.costs from config.json. Never
doubled here — see backtest._cost_r and commit e753c64.

Usage:
    py scripts/autostrat_lab.py --strategy macd --lo 2015-01-01
    py scripts/autostrat_lab.py --strategy macd --exits stop=2.5,target=100,hold=40
"""

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

LONG_DIR = "research_data/yahoo_long"

# ---------------------------------------------------------------- data


def load_bars(sym, lo, hi=None):
    """Split/dividend-adjusted OHLCV in the dict shape run_backtest_on consumes."""
    p = os.path.join(LONG_DIR, sym + ".csv.gz")
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    df = df[df.date >= lo]
    if hi:
        df = df[df.date < hi]
    if len(df) < 300:
        return None
    df = df[np.isfinite(df.close) & (df.close > 0)]
    ratio = (df.adjclose / df.close).values
    # MUST pin the unit: pandas 2.x infers datetime64[s] from date-only strings,
    # so a bare astype("int64") yields SECONDS and any //10**6 lands in 1970 —
    # which silently collapses every signal_date into one cluster and makes the
    # day-clustered t-stat (FC-3) nonsense. Cast to ms explicitly.
    t = df.date.values.astype("datetime64[ms]").astype("int64")
    # bars are timestamped at the 09:30 ET open, matching yahoo_cache
    t = t + 13 * 3600 * 1000
    o, h, l, c = (df[k].values * ratio for k in ("open", "high", "low", "close"))
    v = df.volume.values / np.maximum(ratio, 1e-9)
    return [{"time": int(t[i]), "open": float(o[i]), "high": float(h[i]),
             "low": float(l[i]), "close": float(c[i]), "volume": float(v[i])}
            for i in range(len(df))]


def universe(lo, hi, max_tickers=None, min_dollar_volume=0.0, offset=0):
    """Tickers with enough history, optionally filtered on median dollar volume.
    `offset` skips the first N qualifying names so a disjoint ticker set can be
    used as a CROSS-SECTIONAL holdout — parameters chosen on names [0:120] have
    never seen names [120:], which is an unspent split even though the calendar
    window is shared."""
    syms = sorted(f[:-7] for f in os.listdir(LONG_DIR) if f.endswith(".csv.gz"))
    syms = [s for s in syms if s != "SPY"]
    out, seen = {}, 0
    for s in syms:
        b = load_bars(s, lo, hi)
        if not b:
            continue
        if min_dollar_volume > 0:
            dv = np.median([x["close"] * x["volume"] for x in b[-250:]])
            if dv < min_dollar_volume:
                continue
        seen += 1
        if seen <= offset:
            continue
        out[s] = b
        if max_tickers and len(out) >= max_tickers:
            break
    return out


# ---------------------------------------------------------------- shard worker

_G = {}


def _init(cfg, spy_bars):
    _G["cfg"], _G["spy"] = cfg, spy_bars


def _run_shard(args):
    syms, lo, hi, strategy, min_score, regime = args
    from rhengine.backtest import run_backtest_on
    bars = {s: load_bars(s, lo, hi) for s in syms}
    bars = {k: v for k, v in bars.items() if v}
    if not bars:
        return [], []
    kw = dict(min_score=min_score, forced_strategy=strategy, cost_bps=0.0,
              regime_only=regime)
    try:
        return run_backtest_on(_G["cfg"], bars, _G["spy"], **kw), []
    except Exception:
        # Fast path is one call over the whole shard; a single malformed ticker
        # (e.g. a zero-range bar -> ZeroDivisionError in the scorer) would
        # otherwise discard every trade in the shard. Fall back to per-ticker so
        # the bad name is isolated and NAMED rather than silently swallowed.
        trades, bad = [], []
        for s, b in bars.items():
            try:
                trades.extend(run_backtest_on(_G["cfg"], {s: b}, _G["spy"], **kw))
            except Exception as e:
                bad.append(f"{s}:{type(e).__name__}")
        return trades, bad


def run_parallel(cfg, syms, spy_bars, lo, hi, strategy, min_score=3, workers=None,
                 regime=None):
    """Gross (cost_bps=0) trades; cost is applied analytically downstream so one
    run serves a whole cost grid."""
    workers = workers or max(1, (os.cpu_count() or 4) - 2)
    shards = [list(syms[i::workers]) for i in range(workers)]
    shards = [s for s in shards if s]
    trades, bad = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                             initargs=(cfg, spy_bars)) as ex:
        for part, b in ex.map(_run_shard,
                              [(s, lo, hi, strategy, min_score, regime)
                               for s in shards]):
            trades.extend(part)
            bad.extend(b)
    if bad:
        print(f"  WARNING skipped {len(bad)} malformed tickers: "
              f"{', '.join(sorted(bad)[:12])}")
    trades.sort(key=lambda t: (t["signal_date"], t["ticker"]))
    return trades


# ---------------------------------------------------------------- null test

_NG = {}


def _init_null(cfg, syms, lo, hi, spy):
    bars = {s: load_bars(s, lo, hi) for s in syms}
    _NG["cfg"] = cfg
    _NG["bars"] = {k: v for k, v in bars.items() if v}
    _NG["spy"] = spy


def _null_shard(args):
    paths, real_trades, mode, cost_bps, seed = args
    from rhengine.nulltest import run_null_paths
    return run_null_paths(_NG["cfg"], _NG["bars"], real_trades, paths=paths,
                          mode=mode, cost_bps=cost_bps, seed=seed, verbose=False)


def run_null(cfg, syms, spy, lo, hi, real_trades, paths=200, mode="both",
             cost_bps=30.0, workers=None):
    """Random-entry permutation. Answers the question the Monte Carlo cannot:
    is the edge in the ENTRY, or is it the exit engine harvesting market drift?
    Long holds in a bull market inflate R without any selection skill, so a
    let-winners-run exit MUST be checked against this."""
    workers = workers or max(1, (os.cpu_count() or 4) - 2)
    per = max(1, paths // workers)
    jobs = [(per, real_trades, mode, cost_bps, 1337 + i) for i in range(workers)]
    out = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_null,
                             initargs=(cfg, syms, lo, hi, spy)) as ex:
        for part in ex.map(_null_shard, jobs):
            out.extend(part)
    return out


# ---------------------------------------------------------------- metrics


def day_clustered_t(trades):
    """FC-3: cluster by signal date. Overlapping same-day trades are one draw."""
    by = {}
    for t in trades:
        by.setdefault(t["signal_date"], []).append(t["r_multiple"])
    if len(by) < 3:
        return float("nan"), len(by)
    d = np.array([np.mean(v) for v in by.values()])
    sd = d.std(ddof=1)
    if sd <= 0:
        return float("nan"), len(by)
    return float(d.mean() / (sd / np.sqrt(len(d)))), len(by)


def apply_cost(trades, bps):
    """cost_in_R = round_trip_bps / stop_pct. NO factor of 2."""
    out = []
    for t in trades:
        e, s = float(t["entry"]), float(t["stop"])
        sp = (e - s) / e if e > 0 else 0.0
        c = (bps / 1e4) / sp if sp > 0 else 0.0
        out.append(dict(t, r_multiple=float(t["r_multiple"]) - c))
    return out


def mc_p50(trades, paths=20000, n=250, risk_pct=1.0, seed=7):
    """Bootstrap terminal-equity quantiles over R-multiples, compounding at
    risk_pct per trade. This is the p50 the goal asks about."""
    r = np.array([t["r_multiple"] for t in trades], float)
    if len(r) < 30:
        return {}
    rng = np.random.default_rng(seed)
    draw = rng.choice(r, size=(paths, n), replace=True)
    eq = np.cumprod(1.0 + (risk_pct / 100.0) * draw, axis=1)
    term = eq[:, -1]
    peak = np.maximum.accumulate(eq, axis=1)
    dd = ((peak - eq) / peak).max(axis=1)
    return {"p5": float(np.percentile(term, 5)),
            "p50": float(np.percentile(term, 50)),
            "p95": float(np.percentile(term, 95)),
            "p_loss": float((term < 1.0).mean()),
            "maxdd_p50": float(np.percentile(dd, 50) * 100),
            "maxdd_p95": float(np.percentile(dd, 95) * 100)}


def report(label, trades, costs=(0.0, 10.0, 20.0, 30.0), years=None):
    if not trades:
        print(f"{label}: NO TRADES")
        return None
    r0 = np.array([t["r_multiple"] for t in trades], float)
    _, ndays = day_clustered_t(trades)
    print(f"\n=== {label} ===")
    print(f"  {len(trades)} trades over {ndays} signal days"
          + (f" | {len(trades)/years:.0f}/yr" if years else ""))
    hdr = f"  {'bps':>4} {'meanR':>9} {'win%':>6} {'PF':>7} {'dayT':>7} " \
          f"{'MC p50':>8} {'p5':>7} {'P(loss)':>8} {'ddp50':>7}"
    print(hdr)
    rows = {}
    for bps in costs:
        tr = apply_cost(trades, bps)
        r = np.array([t["r_multiple"] for t in tr], float)
        gl = -r[r <= 0].sum()
        pf = (r[r > 0].sum() / gl) if gl > 0 else float("inf")
        tc, _ = day_clustered_t(tr)
        mc = mc_p50(tr)
        rows[bps] = {"meanR": r.mean(), "pf": pf, "t": tc, **mc}
        print(f"  {bps:>4.0f} {r.mean():>+9.4f} {100*(r>0).mean():>5.1f}% "
              f"{pf:>7.3f} {tc:>+7.2f} {mc.get('p50',float('nan')):>8.3f} "
              f"{mc.get('p5',float('nan')):>7.3f} "
              f"{100*mc.get('p_loss',float('nan')):>7.1f}% "
              f"{mc.get('maxdd_p50',float('nan')):>6.1f}%")
    ex = {}
    for t in trades:
        k = t.get("exit_reason", "?")
        ex.setdefault(k, []).append(t["r_multiple"])
    print("  exits: " + "  ".join(
        f"{k}={len(v)}({np.mean(v):+.2f}R)" for k, v in sorted(ex.items())))
    return rows


def by_era(trades, bps=30.0, eras=(("2015-2017", "2015-01-01", "2018-01-01"),
                                   ("2018-2019", "2018-01-01", "2020-01-01"),
                                   ("2020-2021", "2020-01-01", "2022-01-01"),
                                   ("2022-2023", "2022-01-01", "2024-01-01"),
                                   ("2024-2026", "2024-01-01", "2027-01-01"))):
    """Regime robustness. A let-winners-run exit on a long-only book harvests
    market drift, so the era that matters most is the bear one (2022)."""
    tr = apply_cost(trades, bps)
    print(f"\n  by era @ {bps:.0f} bps:")
    print(f"    {'era':<11}{'n':>7}{'meanR':>10}{'win%':>7}{'PF':>8}{'dayT':>8}")
    for name, lo, hi in eras:
        sub = [t for t in tr if lo <= t["signal_date"] < hi]
        if len(sub) < 30:
            print(f"    {name:<11}{len(sub):>7}   (too few)")
            continue
        r = np.array([t["r_multiple"] for t in sub], float)
        gl = -r[r <= 0].sum()
        pf = (r[r > 0].sum() / gl) if gl > 0 else float("inf")
        tc, _ = day_clustered_t(sub)
        print(f"    {name:<11}{len(sub):>7}{r.mean():>+10.4f}"
              f"{100*(r>0).mean():>6.1f}%{pf:>8.3f}{tc:>+8.2f}")


# ---------------------------------------------------------------- cli


# Exit knobs live in TWO places and it matters which.
#   stop/target ATR multiples come from the SETUP, i.e. strategies.json
#   thresholds (camelCase stopAtrMult/targetAtrMult), overridable through
#   config.json strategy.thresholds.<strategy> via indicators.resolve_thresholds.
#   hold/trail/chandelier/min_stop come from config.json `exits`.
# config.json `exits.stop_atr_mult` / `exits.target_atr_mult` are DEAD KEYS —
# nothing reads them (only tests/test_sim.py mentions them). Setting them looks
# like it retunes the exit and silently does nothing.
_EXIT_CFG = {"trail": "trail_trigger_mult", "chand": "chandelier_atr_mult",
             "hold": "max_hold_sessions", "minstop": "min_stop_pct"}
_EXIT_TH = {"stop": "stopAtrMult", "target": "targetAtrMult"}


def parse_exits(s):
    """-> (config-exits dict, per-strategy threshold dict)."""
    cfg_out, th_out = {}, {}
    for kv in s.split(","):
        if not kv.strip():
            continue
        k, v = kv.split("=")
        k = k.strip()
        if k in _EXIT_CFG:
            cfg_out[_EXIT_CFG[k]] = float(v)
        elif k in _EXIT_TH:
            th_out[_EXIT_TH[k]] = float(v)
        else:
            raise SystemExit(f"unknown exit knob {k!r}")
    return cfg_out, th_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="macd")
    ap.add_argument("--lo", default="2015-01-01")
    ap.add_argument("--hi", default=None)
    ap.add_argument("--max-tickers", type=int, default=None)
    ap.add_argument("--ticker-offset", type=int, default=0,
                    help="skip N qualifying names — cross-sectional holdout")
    ap.add_argument("--by-era", action="store_true")
    ap.add_argument("--regime", default=None,
                    help="restrict signals to SPY regimes, comma-separated: "
                         "uptrend|downtrend|choppy (run_backtest_on accepts a set)")
    ap.add_argument("--min-dollar-volume", type=float, default=0.0)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--exits", default=None,
                    help="stop=,target=,trail=,chand=,hold=,minstop=")
    ap.add_argument("--thresholds", default=None,
                    help="raw per-strategy threshold overrides, k=v,k=v")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--null", type=int, default=0,
                    help="random-entry permutation paths (0 = skip)")
    ap.add_argument("--null-mode", default="both",
                    choices=["both", "tickers", "dates"])
    ap.add_argument("--null-cost", type=float, default=30.0)
    args = ap.parse_args()

    cfg = json.load(open(args.config))
    if args.exits:
        ecfg, eth = parse_exits(args.exits)
        cfg.setdefault("exits", {}).update(ecfg)
        if eth:
            cfg.setdefault("strategy", {}).setdefault("thresholds", {}) \
               .setdefault(args.strategy, {}).update(eth)
    if args.thresholds:
        cfg.setdefault("strategy", {}).setdefault("thresholds", {}) \
           .setdefault(args.strategy, {}).update(
               {k: float(v) for k, v in
                (p.split("=") for p in args.thresholds.split(",") if p.strip())})
    # trail/chandelier are opt-out via a sentinel of 0
    for k in ("trail_trigger_mult", "chandelier_atr_mult"):
        if cfg.get("exits", {}).get(k) == 0:
            cfg["exits"][k] = None

    spy = load_bars("SPY", args.lo, args.hi)
    if not spy:
        sys.exit("SPY history required")
    uni = universe(args.lo, args.hi, args.max_tickers, args.min_dollar_volume,
                   args.ticker_offset)
    syms = sorted(uni)
    del uni  # shards reload; keeps the parent process small
    years = len(spy) / 252.0
    print(f"universe {len(syms)} tickers | {args.lo}..{args.hi or 'end'} "
          f"| {len(spy)} sessions ({years:.1f} yr) | strategy={args.strategy}")
    print(f"exits: {json.dumps(cfg.get('exits', {}), default=str)}")

    trades = run_parallel(cfg, syms, spy, args.lo, args.hi, args.strategy,
                          args.min_score, args.workers,
                          args.regime.split(",") if args.regime else None)
    report(args.strategy, trades, years=years)
    if trades and args.by_era:
        by_era(trades)

    if args.null and trades:
        bps = args.null_cost
        real = apply_cost(trades, bps)
        real_r = float(np.mean([t["r_multiple"] for t in real]))
        print(f"\n--- NULL TEST: random entry, same exits, {args.null} paths "
              f"@ {bps:.0f} bps, mode={args.null_mode} ---")
        res = run_null(cfg, syms, spy, args.lo, args.hi, trades,
                       paths=args.null, mode=args.null_mode, cost_bps=bps,
                       workers=args.workers)
        na = np.array([r["avg_r"] for r in res], float)
        n_ge = int((na >= real_r).sum())
        p = (1 + n_ge) / (len(na) + 1)
        pct = 100.0 * (na < real_r).mean()
        print(f"  real avg R          {real_r:+.4f}")
        print(f"  null avg R  mean    {na.mean():+.4f}   sd {na.std(ddof=1):.4f}")
        print(f"  null p5/p50/p95     {np.percentile(na,5):+.4f} / "
              f"{np.percentile(na,50):+.4f} / {np.percentile(na,95):+.4f}")
        print(f"  real percentile     {pct:.1f}th of {len(na)} null paths")
        print(f"  p-value             {p:.4f}   "
              f"{'ENTRY BEATS LUCK' if p <= 0.05 else 'ENTRY IS NOT DISTINGUISHABLE FROM LUCK'}")

    if args.csv and trades:
        pd.DataFrame(trades).to_csv(args.csv, index=False)
        print(f"\nwrote {len(trades)} trades -> {args.csv}")


if __name__ == "__main__":
    main()
