"""barperm.py — bar-permutation null: does the WHOLE system beat structureless data?

The third and strongest member of the null-test family, and the only genuinely
new method three video intakes have surfaced (`docs/VIDEO-INTAKE-2026-07-31.md`
Cluster A #2, deferred there and in both 2026-08-14 batches). What the existing
two can and cannot ask:

| module | what it permutes | what it can conclude |
|---|---|---|
| `montecarlo.py` | the strategy's own trade R-multiples | outcome variance **given** the edge. Its docstring says outright it "cannot say whether the edge exists" |
| `nulltest.py` | the ENTRY (ticker and/or date), against **real price paths** | whether the entry signal beats luck — but drift and vol clustering survive the permutation, which is why the round-3 null mean was **+0.0552R rather than zero** |
| **this module** | **the DATA** | whether the entire system — signal generation, entry, exit, sizing — beats what it earns on a series with the same return distribution and no serial structure |

That last row is the question neither of the others can reach. A strategy whose
"edge" is really trend-following the market's drift, or harvesting volatility
clustering through an ATR stop, scores the same here on shuffled data as on real
data, and the p-value says so.

## What is destroyed and what is kept

Log-relative components are computed per bar and shuffled **as a unit**:

    gap_i = log(O_i / C_{i-1})     h_i = log(H_i / O_i)
    l_i   = log(L_i / O_i)         c_i = log(C_i / O_i)

Then prices are rebuilt cumulatively from the first close. Consequences:

* **kept** — each ticker's return distribution exactly (same multiset of
  log-returns), bar geometry (H >= max(O,C) and L <= min(O,C) hold by
  construction, so ATR and range-based stops stay realistic), the calendar,
  and volume travelling with its bar.
* **kept, and easy to get wrong — DRIFT.** Permutation reorders the log-returns,
  and a sum is invariant to ordering, so **each ticker's total return over the
  window is exactly unchanged**. This null does not remove the market's rise; it
  removes the *sequencing* of it. That is the correct null for a signal question
  ("does the signal read sequence?") and it means a **positive null mean is
  expected, not a defect** — it is what buy-and-hold drift plus the exit engine
  plus the sizing earn with no signal at all. It is also the number the real
  result has to beat, which is exactly the hurdle `nulltest.py` cannot pose.
* **destroyed** — all serial structure: trends, moving-average crossovers,
  momentum, mean reversion, and volatility clustering.

## Synchronized across tickers, deliberately

**One permutation of the master calendar is applied to every ticker and to SPY.**
Shuffling each ticker independently would destroy the cross-sectional correlation
matrix and with it the market factor — and a long-only book beats a market-less
null trivially, which is the same class of error as the drift leak that made
`nulltest.py`'s null mean positive. Synchronized permutation keeps the market
there to be beaten. SPY is permuted with the same ordering so regime
classification reads structureless data too; leaving it real would let the
regime router see genuine structure that the stock bars no longer contain.

The null is therefore: *"same market, same volatility, same cross-section, no
memory."* Beating it is the claim that the signal reads sequence, not level.

## Cost

Every path re-runs the full event-loop backtest, so this is far heavier than
`nulltest.py`'s entry re-roll. Defaults are 200 paths and a 150-ticker cap,
which resolves p down to 1/201 with the +1 correction — enough for a 0.05 gate
and no finer than the decision needs.

Usage:

    py -m rhengine.barperm                                  # 200 paths, 150 tickers
    py -m rhengine.barperm --strategy macd --paths 200
    py -m rhengine.barperm --stat profit_factor --seed 7
"""
from __future__ import annotations

import argparse
import json
import math
import random

from .costs import resolve_cost_bps
from .backtest import load_bars, run_backtest_on, _stats

#: Bars are dicts; these are the price keys rebuilt by the permutation.
OHLC = ("open", "high", "low", "close")


# ------------------------------------------------------------------ decomposition
def decompose(bars: list[dict]) -> tuple[dict, list[dict]]:
    """Split a bar series into an anchor and per-bar log-relative components.

    Returns `(anchor, components)` where `anchor` is a copy of bar 0 and
    `components[i]` describes bar `i+1` relative to bar `i`'s close. Bars with a
    non-positive price are dropped from the tail of the decomposition rather
    than producing a NaN, because a single bad print would otherwise poison
    every permuted path.
    """
    comps: list[dict] = []
    if not bars:
        return {}, comps
    prev_close = float(bars[0]["close"])
    for b in bars[1:]:
        o, h, l, c = (float(b[k]) for k in OHLC)
        if min(o, h, l, c, prev_close) <= 0.0:
            prev_close = c if c > 0 else prev_close
            continue
        comps.append({
            "time": b["time"],
            "gap": math.log(o / prev_close),
            "h": math.log(h / o),
            "l": math.log(l / o),
            "c": math.log(c / o),
            "volume": b.get("volume", 0),
        })
        prev_close = c
    return dict(bars[0]), comps


def rebuild(anchor: dict, comps: list[dict], times: list) -> list[dict]:
    """Rebuild an OHLC series from an anchor bar and an ORDERED component list.

    `times` supplies the calendar — the same ascending dates as the original, so
    only the returns move, never the dates. `len(times)` must equal `len(comps)`.
    """
    out = [dict(anchor)]
    prev_close = float(anchor["close"])
    for t, k in zip(times, comps):
        o = prev_close * math.exp(k["gap"])
        bar = dict(anchor)
        bar["time"] = t
        bar["open"] = o
        bar["high"] = o * math.exp(k["h"])
        bar["low"] = o * math.exp(k["l"])
        bar["close"] = o * math.exp(k["c"])
        bar["volume"] = k["volume"]
        out.append(bar)
        prev_close = bar["close"]
    return out


# ------------------------------------------------------------------- permutation
def master_calendar(spy_bars: list[dict], bars_map: dict) -> list:
    """The ordering every ticker is permuted against.

    SPY drives it — `load_bars` already requires SPY for regime classification,
    and using one series as the master is what makes the permutation
    cross-sectionally synchronized. Ticker dates SPY does not have are appended
    so nothing is silently dropped.
    """
    times = [b["time"] for b in spy_bars]
    seen = set(times)
    extra = set()
    for bars in bars_map.values():
        for b in bars:
            if b["time"] not in seen:
                extra.add(b["time"])
    return sorted(seen | extra)


def permuted_map(bars_map: dict, spy_bars: list[dict], rng: random.Random
                 ) -> tuple[dict, list[dict]]:
    """One synchronized bar permutation of the whole universe, SPY included.

    Draws a single shuffled ordering of the master calendar and applies it to
    every series. A ticker holding only a subset of master dates takes those
    dates **in the master's permuted order**, so relative ordering between any
    two tickers is identical to the market's — that is what preserves the
    cross-sectional correlation structure.
    """
    master = master_calendar(spy_bars, bars_map)
    order = master[1:]                       # slot 0 anchors; nothing to reorder
    shuffled = list(order)
    rng.shuffle(shuffled)
    rank = {t: i for i, t in enumerate(shuffled)}

    def apply(bars: list[dict]) -> list[dict]:
        anchor, comps = decompose(bars)
        if not comps:
            return list(bars)
        # Sort this ticker's components into the master's permuted order, then
        # lay them back onto its own (unchanged, ascending) calendar.
        comps_sorted = sorted(comps, key=lambda k: rank.get(k["time"], -1))
        times = [k["time"] for k in comps]
        return rebuild(anchor, comps_sorted, times)

    return ({tk: apply(bars) for tk, bars in bars_map.items()}, apply(spy_bars))


# ------------------------------------------------------------------------ the test
def run_paths(cfg, bars_map, spy_bars, paths=200, min_score=3,
              forced_strategy=None, cost_bps=0.0, seed=1337, verbose=True
              ) -> list[dict]:
    """Re-run the whole backtest on `paths` synchronized bar permutations."""
    rng = random.Random(seed)
    out: list[dict] = []
    for i in range(paths):
        pm, ps = permuted_map(bars_map, spy_bars, rng)
        trades = run_backtest_on(cfg, pm, ps, min_score=min_score,
                                 forced_strategy=forced_strategy,
                                 cost_bps=cost_bps)
        st = _stats(trades)
        out.append(st or {"n": 0, "avg_r": 0.0, "profit_factor": 0.0,
                          "win_rate": 0.0, "t_stat": 0.0})
        if verbose and (i + 1) % 10 == 0:
            print(f"  {i + 1}/{paths} paths", flush=True)
    return out


def p_value(real: float, null: list[float]) -> float:
    """One-sided p with the +1 correction: (1 + #{null >= real}) / (n + 1).

    The correction is not cosmetic — without it a null distribution that never
    reaches the real statistic reports p = 0, which is a claim no finite
    simulation can support.
    """
    ge = sum(1 for x in null if x >= real)
    return (1.0 + ge) / (len(null) + 1.0)


def summarize(real_st: dict, nulls: list[dict], stat: str = "avg_r") -> dict:
    vals = sorted(n[stat] for n in nulls)
    counts = sorted(n["n"] for n in nulls)
    k = len(vals)

    def pct(p):
        return vals[min(int(p * k), k - 1)] if k else float("nan")

    return {
        "stat": stat,
        "real": real_st[stat],
        "real_n": real_st["n"],
        "paths": k,
        "null_mean": sum(vals) / k if k else float("nan"),
        "null_p50": pct(0.50),
        "null_p95": pct(0.95),
        "null_p99": pct(0.99),
        "null_max": vals[-1] if k else float("nan"),
        "null_n_p50": counts[k // 2] if k else 0,
        "p_value": p_value(real_st[stat], vals),
    }


def print_report(rep: dict) -> None:
    print()
    print(f"real {rep['stat']:<14} {rep['real']:+.4f}   ({rep['real_n']} trades)")
    print(f"null mean          {rep['null_mean']:+.4f}")
    print(f"null p50 / p95     {rep['null_p50']:+.4f} / {rep['null_p95']:+.4f}")
    print(f"null p99 / max     {rep['null_p99']:+.4f} / {rep['null_max']:+.4f}")
    print(f"null median trades {rep['null_n_p50']}  (real {rep['real_n']})")
    print(f"paths              {rep['paths']}")
    print()
    p = rep["p_value"]
    print(f"p = {p:.4f}   " + ("PASS — the system beats structureless data"
                               if p <= 0.05 else
                               "FAIL — indistinguishable from shuffled bars"))
    if rep["null_mean"] > 0:
        print(f"NOTE: the null mean is {rep['null_mean']:+.4f}, not zero — and "
              "that is EXPECTED, not a bug. Permutation reorders log-returns, so "
              "each ticker's total drift over the window is preserved exactly; "
              "only its SEQUENCING is destroyed. A long-biased book therefore "
              "still harvests the same average drift here. The null mean is what "
              "drift + the exit engine + sizing earn with no signal at all, and "
              "it is the number the real result has to beat.")
    if rep["null_n_p50"] and rep["real_n"]:
        ratio = rep["null_n_p50"] / rep["real_n"]
        if ratio < 0.5 or ratio > 2.0:
            print(f"NOTE: permuted data fires {ratio:.2f}x the real trade count. "
                  "The comparison is still valid (per-trade statistic) but the "
                  "signal is strongly sensitive to serial structure — which is "
                  "itself the finding.")


# ------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Bar-permutation null — does the whole system beat "
                    "structureless data?")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--strategy", default=None,
                    help="force a single strategy instead of regime-auto")
    ap.add_argument("--paths", type=int, default=200,
                    help="every path re-runs the full backtest; 200 resolves "
                         "p to 1/201, which is enough for a 0.05 gate")
    ap.add_argument("--stat", default="avg_r",
                    choices=("avg_r", "profit_factor", "win_rate", "t_stat"))
    ap.add_argument("--cost-bps", type=float, default=None)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--max-tickers", type=int, default=150,
                    help="0 = all; the event-loop backtest is slow at "
                         "full-market scale and this runs it once per path")
    a = ap.parse_args(argv)
    a.cost_bps = resolve_cost_bps(a.cost_bps, a.config)

    with open(a.config, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    bars_map, spy_bars = load_bars(cfg, lookback_days=a.lookback_days)
    if a.max_tickers:
        bars_map = dict(list(bars_map.items())[:a.max_tickers])
        print(f"Universe capped at {len(bars_map)} tickers")

    print("Running real backtest...")
    real = run_backtest_on(cfg, bars_map, spy_bars, min_score=a.min_score,
                           forced_strategy=a.strategy, cost_bps=a.cost_bps)
    real_st = _stats(real)
    if not real_st:
        print("No real trades — nothing to test.")
        return 1
    print(f"Real trades: {real_st['n']}  avgR={real_st['avg_r']:+.3f}  "
          f"PF={real_st['profit_factor']:.2f}")

    print(f"Simulating {a.paths} synchronized bar permutations "
          f"(cost={a.cost_bps} bps)...")
    nulls = run_paths(cfg, bars_map, spy_bars, paths=a.paths,
                      min_score=a.min_score, forced_strategy=a.strategy,
                      cost_bps=a.cost_bps, seed=a.seed)
    rep = summarize(real_st, nulls, a.stat)
    print_report(rep)
    return 0 if rep["p_value"] <= 0.05 else 2


if __name__ == "__main__":
    raise SystemExit(main())
