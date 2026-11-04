"""T13-T17 — TrendSpider's shared "SSO Opus 5.5 Swing 15m" strategy, tested and refined.

    .venv/bin/python intraday/scripts/trendspider_sso.py            # every trial, both eras
    .venv/bin/python intraday/scripts/trendspider_sso.py selftest   # causality + sim checks

Source: https://share.trendspider.com/strategies/79a84053fa45a1b7f292091e
(scraped 2026-11-04). TrendSpider's own figures for Jul 2022 -> Nov 2026: +153%
strategy, +111% buy & hold, 40% winners, 0.96% average trade, -12.50% drawdown.

Entry (all, at bar close; fill at the NEXT bar's open — "trade by: open"):
  SMA(50) > SMA(200);  ADX(10) > 20;  and either
  A: close > highest high of the 14 bars ending one bar ago, volume >= 2x SMA20(volume)
  B: VWAP (day-anchored) crosses above close, RSI(14) > 50, volume >= 1.5x SMA20(volume)
Exits (checked at bar close, fill at next open): loss > 3.5%; gain > 15%;
  25 bars held and PnL < 0; 60 bars held and PnL < 1%; close 7% below peak close.

Variants (pre-registered in docs/RESULTS.md before they ran):
  T13  as published.  Setup B means price drops BELOW VWAP (odd for a long rule).
  T13b Setup B read as close crossing above VWAP.
  T15  random-entry null for T13: same trend+ADX filter, random entry bars.
  T16  volume vs the same 15-minute slot's mean over the prior 20 sessions. A 20-bar
       SMA spans the open and close auctions, so 56% of T13's signals fired on the
       09:30 or 15:45 bar purely because those bars always carry heavy volume.
  T17  Gayed & Bilello (2016) "Leverage for the Long Run": hold SSO while its daily
       close is above its 200-day SMA, else cash. Decided at the 15:45 bar close,
       filled at the next open.

Stats: `validate.day_stats` on daily RETURNS x fixed notional (compounding dollar
PnL weights late years more — the first version of this script got that wrong).
Costs: Robinhood $0 commission, 1 cent per share per side, SEC fee on sells.
Bars: Alpaca SIP 15m, split-adjusted, cut to RTH (09:30-15:45 bar opens).
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from intraday import validate as V  # noqa: E402
from rhengine import trials as T  # noqa: E402

CSV = os.path.join(ROOT, "intraday", "data", "SSO_15m.csv")
CAPITAL, SLIP, SEC_FEE = 10_000.0, 0.01, 27.8e-6
ERAS = {"unseen 2016-06..2022-06": ("2016-06-01", "2022-07-01"),
        "seen   2022-07..2026-11": ("2022-07-01", "2026-11-04")}


def load(path: str = CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.index = pd.to_datetime(df.pop("ts"), utc=True).dt.tz_convert("America/New_York")
    t = df.index.hour * 60 + df.index.minute
    return df[(t >= 570) & (t <= 945) & (df.index.day_of_week < 5)]


def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()


def regime(b: pd.DataFrame) -> pd.Series:
    """SMA50 > SMA200 and ADX(10) > 20 — the filter every setup shares."""
    c, h, lo = b.close, b.high, b.low
    up, dn = h.diff(), -lo.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), b.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), b.index)
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr, 10)
    pdi, mdi = 100 * wilder(pdm, 10) / atr, 100 * wilder(mdm, 10) / atr
    adx = wilder(100 * (pdi - mdi).abs() / (pdi + mdi), 10)
    return ((c.rolling(50).mean() > c.rolling(200).mean()) & (adx > 20)).fillna(False)


def signals(b: pd.DataFrame, vwap_up: bool = False, slot_volume: bool = False) -> pd.Series:
    c, h, lo, v = b.close, b.high, b.low, b.volume
    if slot_volume:
        slot = b.index.strftime("%H:%M")
        base = v.groupby(slot).transform(lambda s: s.shift(1).rolling(20).mean())
    else:
        base = v.rolling(20).mean()
    setup_a = (c > h.rolling(14).max().shift(1)) & (v >= 2.0 * base)
    day = b.index.date
    tp = (h + lo + c) / 3
    vwap = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum()
    above = (c > vwap) if vwap_up else (vwap > c)
    cross = above & ~above.shift(1, fill_value=True)
    rsi = 100 - 100 / (1 + wilder(c.diff().clip(lower=0), 14) / wilder(-c.diff().clip(upper=0), 14))
    setup_b = cross & (rsi > 50) & (v >= 1.5 * base)
    return (regime(b) & (setup_a | setup_b)).fillna(False)


def daily_sma_target(b: pd.DataFrame, n: int = 200) -> pd.Series:
    """T17: True on the day's last bar when the daily close is above its n-day SMA;
    NaN on other bars, so the position only changes at the close.

    Keyed to the 15:45 bar by clock, not "last bar in the file": the latter makes
    a 10:15 bar look like the close when the data stop mid-day (caught by the
    causality check). Half-days (last bar 12:45) carry no decision."""
    last = b[b.index.strftime("%H:%M") == "15:45"].close
    above = (last > last.rolling(n).mean()).astype(float)
    return above.reindex(b.index)


def causality_check(b: pd.DataFrame, fn, cuts: int = 40) -> None:
    """A value at bar k must not change when bars after k are deleted."""
    full = fn(b)
    for k in np.random.default_rng(0).integers(5_000, len(b), cuts):
        part, ref = fn(b.iloc[: k + 1]).iloc[-1], full.iloc[k]
        assert (pd.isna(part) and pd.isna(ref)) or part == ref, f"lookahead at {b.index[k]}"


def run(b, entries, lo, hi, exits=True, target=None):
    """Event sim, all-in, one position. `entries` = signal at bar close (fill next
    open). `exits=True` applies the published exit stack; with `target` the
    position simply follows the target (T17)."""
    o, c, idx = b.open.to_numpy(), b.close.to_numpy(), b.index
    s = entries.to_numpy() if entries is not None else None
    tg = target.to_numpy() if target is not None else None
    i0 = idx.searchsorted(pd.Timestamp(lo, tz=idx.tz))
    i1 = idx.searchsorted(pd.Timestamp(hi, tz=idx.tz))
    cash, sh, entry, peak, held, pending, t_in = CAPITAL, 0, 0.0, 0.0, 0, None, None
    trades, eq = [], np.empty(i1 - i0)
    for j, i in enumerate(range(i0, i1)):
        if pending == "buy":
            entry = o[i] + SLIP
            sh = int(cash // entry)
            cash -= sh * entry
            peak, held, t_in = c[i], 0, idx[i]
        elif pending == "sell":
            px = o[i] - SLIP
            cash += sh * px * (1 - SEC_FEE)
            trades.append((t_in, idx[i], px / entry - 1, held))
            sh = 0
        pending = None
        last = i + 1 >= i1
        if sh:
            held += 1
            peak = max(peak, c[i])
        if tg is not None:
            if not np.isnan(tg[i]) and not last:
                if sh and not tg[i]:
                    pending = "sell"
                elif not sh and tg[i]:
                    pending = "buy"
        elif sh:
            pnl = c[i] / entry - 1
            if exits and (pnl < -0.035 or pnl > 0.15 or (held >= 25 and pnl < 0)
                          or (held >= 60 and pnl < 0.01) or c[i] < peak * 0.93):
                pending = "sell"
        elif s[i] and not last:
            pending = "buy"
        eq[j] = cash + sh * c[i]
    if sh:  # mark the open trade at the window's last close
        trades.append((t_in, idx[i1 - 1], c[i1 - 1] / entry - 1, held))
    return pd.Series(eq, idx[i0:i1]), pd.DataFrame(trades, columns=["entry", "exit", "ret", "bars"])


def summarize(eq: pd.Series, tr: pd.DataFrame, n_trials: int) -> dict:
    d = eq.groupby(eq.index.date).last()
    r = d.pct_change().fillna(d.iloc[0] / CAPITAL - 1)
    st = V.day_stats(r * CAPITAL, trials=n_trials)
    return {"total": eq.iloc[-1] / CAPITAL - 1, "dd": (eq / eq.cummax() - 1).min(),
            "trades": len(tr), "win": (tr.ret > 0).mean() if len(tr) else np.nan,
            "in_mkt": tr.bars.sum() / len(eq) if len(tr) else 0.0, **st}


def random_null(b, entries, lo, hi, n_paths=500, seed=0):
    """T15: as many candidate entry bars as the real signal has, drawn at random
    from bars where the shared regime filter holds. Returns each path's total."""
    win = (b.index >= pd.Timestamp(lo, tz=b.index.tz)) & (b.index < pd.Timestamp(hi, tz=b.index.tz))
    pool = np.flatnonzero(regime(b).to_numpy() & win)
    k = int((entries & win).sum())
    rng, out = np.random.default_rng(seed), []
    for _ in range(n_paths):
        fake = np.zeros(len(b), bool)
        fake[rng.choice(pool, k, replace=False)] = True
        eq, _ = run(b, pd.Series(fake, b.index), lo, hi)
        out.append(eq.iloc[-1] / CAPITAL - 1)
    return np.array(out)


def line(name, m):
    lo_ci, hi_ci = m["sharpe_ci"]
    return (f"  {name:<34} total {m['total']:+7.1%}  DD {m['dd']:6.1%}  trades {m['trades']:4d}  "
            f"win {m['win']:4.0%}  in-mkt {m['in_mkt']:4.0%}  Sharpe {m['sharpe']:5.2f} "
            f"[{lo_ci:5.2f},{hi_ci:5.2f}]  t {m['t_nw']:+5.2f}")


def main():
    b = load()
    n = T.count(V.FAMILY, path=V.LEDGER)
    sigs = {"T13 as published": signals(b),
            "T13b VWAP read as close-above": signals(b, vwap_up=True),
            "T16 slot-relative volume": signals(b, slot_volume=True)}
    tgt = daily_sma_target(b)
    for era, (lo, hi) in ERAS.items():
        print(f"\n{era}   (ledger: {n} trials)")
        w = b[(b.index >= lo) & (b.index < hi)]
        bh_eq = CAPITAL * w.close / w.close.iloc[0]
        bh_tr = pd.DataFrame({"ret": [w.close.iloc[-1] / w.close.iloc[0] - 1], "bars": [len(w)]})
        print(line("SSO buy & hold", summarize(bh_eq, bh_tr, n)))
        for name, s in sigs.items():
            print(line(name, summarize(*run(b, s, lo, hi), n)))
        print(line("T17 daily 200-SMA (Gayed-Bilello)",
                   summarize(*run(b, None, lo, hi, target=tgt), n)))
        real = summarize(*run(b, sigs["T13 as published"], lo, hi), n)["total"]
        null = random_null(b, sigs["T13 as published"], lo, hi)
        print(f"  T15 random-entry null for T13: median {np.median(null):+.1%}, "
              f"p(random >= T13) = {(null >= real).mean():.3f}")


def selftest():
    b = load()
    for fn in (signals, lambda x: signals(x, slot_volume=True), daily_sma_target):
        causality_check(b, fn)
    # never-exit sim on an always-true signal must equal buy & hold within costs
    lo, hi = "2023-01-03", "2023-12-30"
    eq, tr = run(b, pd.Series(True, b.index), lo, hi, exits=False)
    w = b[(b.index >= lo) & (b.index < hi)]
    bh = w.close.iloc[-1] / w.open.iloc[1] - 1
    assert abs((eq.iloc[-1] / CAPITAL - 1) - bh) < 0.01, (eq.iloc[-1], bh)
    assert len(tr) == 1
    print("selftest ok: no lookahead in signals/slot volume/daily SMA; sim matches buy & hold")


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["selftest"] else main()
