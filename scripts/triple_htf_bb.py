"""Backtest of HermanTrading's Triple HTF Bollinger Bands strategy.

Source, unmodified defaults:
https://github.com/HermanTrading/Triple-HTF-Bollinger-Bands-Strategy-5m-15m-1h

The Pine script is a 5-minute chart. On each confirmed bar it buys when the
close is above at least two of the three upper bands (5m, 15m, 1h) and above
the 5m basis, and shorts the mirror image. Bands are Bollinger 20, 2 standard
deviations, on the previous completed higher-timeframe bar (the script's
`[1]` plus `lookahead_on`, which is the non-repainting form). Stop is the 5m
basis fixed at entry. Target is 1R. New entries only from 08:00 to 12:00
America/New_York. Ten chart bars of cooldown after an exit. One position.

The script's `strategy()` call buys 1 share against $1,000,000. That is not a
return. This runner is fully invested while a trade is open and in cash
otherwise, marked to market every bar, so the numbers below are returns.
`rhengine.alpha.stats.perf` is the only path that turns those returns into a
Sharpe.

    py scripts/triple_htf_bb.py --self-test
    py scripts/triple_htf_bb.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhengine.alpha.stats import Perf, alpha_beta, monthly, perf  # noqa: E402

LENGTH = 20
MULT = 2.0
RR = 1.0
COOLDOWN = 10
MINTICK = 0.01
NY = "America/New_York"

WINDOWS = (
    ("6m", "2026-04-06"),
    ("1y", "2025-10-06"),
    ("5y", "2021-10-06"),
    ("10y", "2016-10-06"),
)


def population_bb(close: pd.Series, length: int = LENGTH, mult: float = MULT):
    """Pine `ta.sma` / `ta.stdev`: population standard deviation, divide by N."""
    mean = close.rolling(length, min_periods=length).mean()
    sd = close.rolling(length, min_periods=length).std(ddof=0)
    return mean, mean + mult * sd, mean - mult * sd


def _map_htf(ts: pd.Series, close: pd.Series, freq: str):
    """Previous completed HTF bar's bands, carried onto each 5-minute bar.

    `request.security(..., gaps_off, lookahead_on)` of `expr[1]` shows the
    last finished higher-timeframe bar on every chart bar of the current one.
    Clock alignment matches Pine intraday bars: UTC and New York differ by a
    whole hour, so 15-minute and 60-minute buckets are the same in both.
    """
    bucket = ts.dt.floor(freq)
    htf_close = close.groupby(bucket, sort=True).last()
    basis, upper, lower = population_bb(htf_close)
    confirmed = pd.DataFrame({
        "basis": basis.shift(1),
        "upper": upper.shift(1),
        "lower": lower.shift(1),
    })
    return confirmed.reindex(bucket.to_numpy())


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if out["ts"].dt.tz is None:
        out["ts"] = out["ts"].dt.tz_localize("UTC")
    close_ts = out["ts"] + pd.Timedelta(minutes=5)
    out["close_ny"] = close_ts.dt.tz_convert(NY)
    b5 = _map_htf(out["ts"], out["close"], "5min")
    b15 = _map_htf(out["ts"], out["close"], "15min")
    b60 = _map_htf(out["ts"], out["close"], "60min")
    out["basis"] = b5["basis"].to_numpy()
    out["upper5"] = b5["upper"].to_numpy()
    out["lower5"] = b5["lower"].to_numpy()
    out["upper15"] = b15["upper"].to_numpy()
    out["lower15"] = b15["lower"].to_numpy()
    out["upper60"] = b60["upper"].to_numpy()
    out["lower60"] = b60["lower"].to_numpy()
    minutes = out["close_ny"].dt.hour * 60 + out["close_ny"].dt.minute
    out["in_session"] = ((minutes >= 8 * 60) & (minutes < 12 * 60)).to_numpy()
    if "spread" not in out.columns:
        out["spread"] = np.nan
    return out


@dataclass
class RunResult:
    daily: pd.Series
    equity: pd.Series
    trades: pd.DataFrame
    time_in_market: float
    one_way_turnover: float
    avg_spread_bps: float


def simulate(df: pd.DataFrame, entry_start: pd.Timestamp, entry_end: pd.Timestamp,
             long_only: bool = False, round_trip_bps: float | None = None,
             rr: float = RR, cooldown: int = COOLDOWN) -> RunResult:
    """One position, enter at the signal close, stop checked before target.

    `round_trip_bps is None` charges the quoted ask-bid spread (full spread
    per round trip, because fills are at the mid). A number charges that many
    basis points per round trip, half on entry and half on exit.
    """
    n = len(df)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    basis = df["basis"].to_numpy(float)
    spread = df["spread"].to_numpy(float)
    session = df["in_session"].to_numpy(bool)
    close_ny = df["close_ny"]
    start_ok = (close_ny >= entry_start) & (close_ny <= entry_end)
    allow = session & start_ok.to_numpy()

    uppers = np.vstack([
        df["upper5"].to_numpy(float),
        df["upper15"].to_numpy(float),
        df["upper60"].to_numpy(float),
    ])
    lowers = np.vstack([
        df["lower5"].to_numpy(float),
        df["lower15"].to_numpy(float),
        df["lower60"].to_numpy(float),
    ])
    upper_count = (c > uppers).sum(axis=0)
    lower_count = (c < lowers).sum(axis=0)
    # NaN comparisons are False, matching Pine's `not na(upper) and close > upper`.
    long_sig = allow & (upper_count >= 2) & (c > basis + MINTICK)
    short_sig = allow & (lower_count >= 2) & (c < basis - MINTICK)
    if long_only:
        short_sig = np.zeros(n, dtype=bool)

    inside = start_ok.to_numpy()
    if not inside.any():
        raise ValueError("window contains no bars")
    last = int(np.flatnonzero(inside)[-1])

    equity = np.ones(n)
    eq = 1.0
    side = 0
    stop = target = entry = 0.0
    prev = 0.0
    risk = 0.0
    entry_i = -1
    entry_cost = 0.0
    last_exit = -10_000
    trades = []
    in_mkt = 0
    spread_paid = []

    def half_cost(i: int, price: float) -> float:
        if round_trip_bps is not None:
            return round_trip_bps / 2.0 / 1e4
        spr = spread[i]
        if not np.isfinite(spr) or price <= 0:
            return 0.0
        return (spr / price) / 2.0

    for i in range(last + 1):
        if side != 0 and inside[i]:
            in_mkt += 1
            fill = None
            reason = ""
            if side > 0:
                if l[i] <= stop:
                    fill = o[i] if o[i] < stop else stop
                    reason = "stop"
                elif h[i] >= target:
                    fill = o[i] if o[i] > target else target
                    reason = "target"
            else:
                if h[i] >= stop:
                    fill = o[i] if o[i] > stop else stop
                    reason = "stop"
                elif l[i] <= target:
                    fill = o[i] if o[i] < target else target
                    reason = "target"
            if fill is not None:
                gross = side * (fill / prev - 1.0)
                xc = half_cost(i, fill)
                eq *= (1.0 + gross) * (1.0 - xc)
                net = (1.0 - entry_cost) * (1.0 + side * (fill / entry - 1.0)) * (1.0 - xc) - 1.0
                r_gross = side * (fill - entry) / risk
                trades.append({
                    "entry": close_ny.iloc[entry_i],
                    "exit": close_ny.iloc[i],
                    "side": side,
                    "entry_px": entry,
                    "exit_px": fill,
                    "stop": stop,
                    "r_gross": r_gross,
                    "r_net": net / (risk / entry),
                    "hold_bars": i - entry_i,
                    "reason": reason,
                })
                spread_paid.append(entry_cost + xc)
                side = 0
                last_exit = i
            else:
                gross = side * (c[i] / prev - 1.0)
                eq *= (1.0 + gross)
                prev = c[i]
        if side == 0 and i - last_exit > cooldown:
            want = 1 if long_sig[i] else (-1 if short_sig[i] else 0)
            if want != 0 and np.isfinite(basis[i]):
                risk_px = want * (c[i] - basis[i])
                if risk_px > MINTICK:
                    ec = half_cost(i, c[i])
                    eq *= (1.0 - ec)
                    side = want
                    entry = c[i]
                    stop = basis[i]
                    risk = risk_px
                    target = entry + want * risk * rr
                    prev = entry
                    entry_i = i
                    entry_cost = ec
        equity[i] = eq

    # An open trade stays in the equity curve. It is not a closed R.
    if side != 0:
        trades.append({
            "entry": close_ny.iloc[entry_i],
            "exit": pd.NaT,
            "side": side,
            "entry_px": entry,
            "exit_px": c[-1],
            "stop": stop,
            "r_gross": side * (c[-1] - entry) / risk,
            "r_net": np.nan,
            "hold_bars": n - 1 - entry_i,
            "reason": "open",
        })

    ny_date = close_ny.dt.tz_localize(None).dt.normalize()
    # Keep marks only inside the reporting window. Warmup bars stay flat at 1.
    eq_s = pd.Series(equity, index=ny_date)
    eq_s = eq_s.iloc[:last + 1][inside[:last + 1]]
    daily_eq = eq_s.groupby(level=0).last()
    daily = daily_eq.pct_change()
    if len(daily):
        daily.iloc[0] = daily_eq.iloc[0] / 1.0 - 1.0
    daily = daily.dropna()
    daily.index = pd.DatetimeIndex(daily.index)
    years = max(len(daily) / 252.0, 1e-9)
    closed = [t for t in trades if t["reason"] != "open"]
    inside_n = int(inside[:last + 1].sum())
    return RunResult(
        daily=daily,
        equity=daily_eq,
        trades=pd.DataFrame(trades),
        time_in_market=in_mkt / inside_n if inside_n else 0.0,
        one_way_turnover=(len(closed) / years),
        avg_spread_bps=(float(np.mean(spread_paid)) * 1e4) if spread_paid else 0.0,
    )


def buy_and_hold(df: pd.DataFrame, entry_start, entry_end) -> pd.Series:
    """Close-to-close of the same bars. The first day uses the prior close."""
    ny_date = df["close_ny"].dt.tz_localize(None).dt.normalize()
    px = df["close"].groupby(ny_date).last()
    r = px.pct_change()
    start = entry_start.tz_localize(None).normalize()
    end = entry_end.tz_localize(None).normalize()
    r = r[(r.index >= start) & (r.index <= end)]
    r.index = pd.DatetimeIndex(r.index)
    return r.dropna()


def load_usa500(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return prepare(df)


def load_spy(directory: str) -> pd.DataFrame:
    rows = []
    for p in sorted(Path(directory).glob("*.json")):
        rows.extend(json.loads(p.read_text()))
    df = pd.DataFrame(rows).rename(columns={"t": "ts", "o": "open", "h": "high",
                                            "l": "low", "c": "close"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return prepare(df)


def load_daily_px(path: str) -> pd.Series:
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    s = df.set_index("ts")["close"].sort_index()
    s.index = s.index.tz_localize(None).normalize()
    s = s[~s.index.duplicated(keep="last")]
    return s.pct_change().dropna()


def _line(name: str, p: Perf, extra: str = "") -> str:
    ci = f"{p.sharpe_ci_lo:+.2f} .. {p.sharpe_ci_hi:+.2f}"
    return (f"{name:<22} {p.total_return_pct:>8.1f}% {p.cagr_pct:>7.2f}% "
            f"{p.vol_ann_pct:>6.1f}% {p.sharpe_monthly:>6.2f} {ci:>18} "
            f"{p.max_dd_pct:>7.1f}% {p.n_months:>4}{extra}")


def report_window(label: str, result: RunResult, benches: dict[str, pd.Series]) -> dict:
    """Performance through stats.perf. Alpha t only when a year of months exists."""
    out = {"label": label, "perf": perf(result.daily, label=label)}
    ms = monthly(result.daily)
    closed = result.trades[result.trades["reason"] != "open"] if len(result.trades) else result.trades
    out["n_trades"] = int(len(closed))
    out["win"] = float((closed["r_gross"] > 0).mean()) if len(closed) else float("nan")
    out["avg_r"] = float(closed["r_gross"].mean()) if len(closed) else float("nan")
    out["avg_r_net"] = float(closed["r_net"].mean()) if len(closed) else float("nan")
    out["hold_min"] = float(closed["hold_bars"].median() * 5) if len(closed) else float("nan")
    out["time"] = result.time_in_market
    out["turn"] = result.one_way_turnover
    out["cost_bps"] = result.avg_spread_bps
    out["vs"] = {}
    for name, b in benches.items():
        aligned = b.reindex(result.daily.index).dropna()
        common = result.daily.reindex(aligned.index).dropna()
        aligned = aligned.reindex(common.index)
        if len(common) < 20:
            continue
        out["vs"][name] = {
            "perf": perf(aligned, label=name),
            "alpha": alpha_beta(monthly(common), monthly(aligned)),
        }
    return out


def print_block(rows: list[dict]) -> None:
    print(f"{'book':<22} {'total':>9} {'CAGR':>8} {'vol':>7} {'Sh(m)':>6} "
          f"{'Sharpe 95% CI':>18} {'maxDD':>8} {'mo':>4}")
    for row in rows:
        print(_line(row["label"], row["perf"]))
        for name, vs in row["vs"].items():
            print(_line("  " + name, vs["perf"]))
            a = vs["alpha"]
            if np.isfinite(a["alpha_t"]):
                print(f"{'':<22} alpha/yr {a['alpha_ann']*100:+.2f}%  "
                      f"t {a['alpha_t']:+.2f}  beta {a['beta']:.2f}  "
                      f"months {a['n']}")
            else:
                print(f"{'':<22} alpha t not computed ({a['n']} months; needs 12)")
        win = row["win"] * 100 if np.isfinite(row["win"]) else float("nan")
        print(f"{'':<22} trades {row['n_trades']}  win {win:.0f}%  "
              f"avg R {row['avg_r']:+.2f} gross / {row['avg_r_net']:+.2f} net  "
              f"median hold {row['hold_min']:.0f} min")
        print(f"{'':<22} time in market {row['time']*100:.1f}% of bars  "
              f"one-way turnover {row['turn']*100:.0f}%/yr  "
              f"avg round-trip cost {row['cost_bps']:.1f} bps")
        print()


def french_blend() -> pd.DataFrame | None:
    """RMX-B's published construction: 50/50 BIG HiOP and BIG HiPRIOR, net.

    Same cost the French measurement uses (20 bps round trip on 33%/yr and
    200%/yr one-way). Returns monthly simple returns, or None if the files
    are not on disk.
    """
    root = Path(__file__).resolve().parents[1] / "research_data"
    names = ("ff5_monthly.csv", "port_me_op_5x5.csv", "port_me_prior_5x5.csv")
    if not all((root / n).exists() for n in names):
        return None
    sys.path.insert(0, str(root.parent / "scripts"))
    from ff_factor_check import read_french

    ff5 = read_french("ff5_monthly.csv")
    ff5.columns = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"][:ff5.shape[1]]
    mkt = ff5["Mkt-RF"] + ff5["RF"]
    op = read_french("port_me_op_5x5.csv")
    pr = read_french("port_me_prior_5x5.csv")
    q = op["BIG HiOP"] - (0.33 * 20.0 / 1e4 / 12.0)
    m = pr["BIG HiPRIOR"] - (2.00 * 20.0 / 1e4 / 12.0)
    idx = q.dropna().index.intersection(m.dropna().index).intersection(mkt.dropna().index)
    blend = 0.5 * q.reindex(idx) + 0.5 * m.reindex(idx)
    return pd.DataFrame({"rmxb": blend, "mkt": mkt.reindex(idx)}).dropna()


def french_window_table(monthly_px: pd.DataFrame) -> None:
    print("RMX-B on Ken French, same calendar windows, monthly.")
    print("50/50 BIG HiOP + BIG HiPRIOR, net of 20 bps on 33%/yr and 200%/yr.")
    print("Market is French's value-weighted market, gross, with dividends.")
    print(f"{'window':<22} {'RMX-B':>8} {'market':>8} {'excess':>8} {'alpha/yr':>9} {'t':>7} {'mo':>4}")
    cuts = [("6m", 6), ("1y", 12), ("5y", 60), ("10y", 120),
            ("1963-2026", len(monthly_px))]
    for name, k in cuts:
        sub = monthly_px.tail(k) if name != "1963-2026" else monthly_px
        # tail(k) is the last k months, which is the window ending at the file's end
        r, m = sub["rmxb"], sub["mkt"]
        tot_r = float((1 + r).prod() - 1)
        tot_m = float((1 + m).prod() - 1)
        years = len(sub) / 12.0
        cagr_r = float((1 + r).prod() ** (1 / years) - 1)
        cagr_m = float((1 + m).prod() ** (1 / years) - 1)
        ab = alpha_beta(r, m)
        t = f"{ab['alpha_t']:+.2f}" if np.isfinite(ab["alpha_t"]) else "n/a"
        al = f"{ab['alpha_ann']*100:+.2f}%" if np.isfinite(ab["alpha_ann"]) else "n/a"
        span = f"{sub.index[0]:%Y-%m}..{sub.index[-1]:%Y-%m}"
        print(f"{name + ' ' + span:<32} {tot_r*100:>7.1f}% {tot_m*100:>7.1f}% "
              f"{(tot_r-tot_m)*100:>+7.1f}% {al:>9} {t:>7} {len(sub):>4}"
              f"   cagr {cagr_r*100:.2f}% vs {cagr_m*100:.2f}%")


def self_test() -> None:
    # Population stdev, not the sample one. [1..5] -> sqrt(2).
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    # rolling needs the window; check the formula directly
    sd = float(s.std(ddof=0))
    assert abs(sd - 2 ** 0.5) < 1e-12, sd

    start = pd.Timestamp("2024-06-03 11:00", tz="UTC")  # 07:00 ET
    ts = pd.date_range(start, periods=500, freq="5min", tz="UTC")
    close = np.full(len(ts), 100.0)
    # A bar whose close is 10:00 ET sits inside the NY AM session.
    close_ny = (ts + pd.Timedelta(minutes=5)).tz_convert(NY)
    session_idx = np.where((close_ny.hour >= 9) & (close_ny.hour < 11)
                           & (np.arange(len(ts)) > 250))[0]
    assert len(session_idx) > 5
    spike = int(session_idx[0])
    close[spike] = 110.0
    high = close.copy()
    low = close.copy()
    open_ = close.copy()
    # Next bar runs the target. 1R on a 10-point risk is 120.
    # Stay above the stop. A low equal to the basis would fill the stop first.
    open_[spike + 1] = 110.0
    low[spike + 1] = 108.0
    high[spike + 1] = 120.0
    close[spike + 1] = 115.0
    df = prepare(pd.DataFrame({
        "ts": ts, "open": open_, "high": high, "low": low, "close": close,
        "spread": np.full(len(ts), 0.05),
    }))
    # The signal bar must be using the PRE-spike bands. Flat history -> basis 100.
    assert abs(df["basis"].iloc[spike] - 100.0) < 1e-9, df["basis"].iloc[spike]
    assert df["upper5"].iloc[spike] <= 100.0 + 1e-9
    end = df["close_ny"].iloc[-1] + pd.Timedelta(minutes=1)
    begin = df["close_ny"].iloc[0]
    res = simulate(df, begin, end, round_trip_bps=0.0)
    closed = res.trades[res.trades["reason"] != "open"]
    assert len(closed) == 1, res.trades
    tr = closed.iloc[0]
    assert tr["side"] == 1
    assert tr["reason"] == "target"
    assert abs(tr["r_gross"] - 1.0) < 1e-9, tr["r_gross"]
    assert abs(tr["exit_px"] - 120.0) < 1e-9

    # Same bar trades through the stop and the target: stop fills, not the target.
    high2 = close.copy()
    low2 = close.copy()
    open2 = close.copy()
    high2[spike + 1] = 120.0
    low2[spike + 1] = 90.0
    open2[spike + 1] = 100.0
    df2 = prepare(pd.DataFrame({
        "ts": ts, "open": open2, "high": high2, "low": low2, "close": close,
        "spread": 0.0,
    }))
    res2 = simulate(df2, begin, end, round_trip_bps=0.0)
    tr2 = res2.trades[res2.trades["reason"] != "open"].iloc[0]
    assert tr2["reason"] == "stop", tr2["reason"]
    assert abs(tr2["r_gross"] + 1.0) < 1e-9

    # A gap through the stop fills at the open.
    open3 = close.copy()
    low3 = close.copy()
    high3 = close.copy()
    open3[spike + 1] = 95.0
    low3[spike + 1] = 94.0
    high3[spike + 1] = 96.0
    close3 = close.copy()
    close3[spike + 1] = 95.5
    df3 = prepare(pd.DataFrame({
        "ts": ts, "open": open3, "high": high3, "low": low3, "close": close3,
        "spread": 0.0,
    }))
    res3 = simulate(df3, begin, end, round_trip_bps=0.0)
    tr3 = res3.trades[res3.trades["reason"] != "open"].iloc[0]
    assert abs(tr3["exit_px"] - 95.0) < 1e-9, tr3["exit_px"]

    # 13:00 ET is outside the session even if price is through every band.
    afternoon = np.where((close_ny.hour == 13) & (np.arange(len(ts)) > 300))[0]
    assert len(afternoon)
    j = int(afternoon[0])
    close4 = np.full(len(ts), 100.0)
    close4[j] = 110.0
    df4 = prepare(pd.DataFrame({
        "ts": ts, "open": close4, "high": close4, "low": close4, "close": close4,
        "spread": 0.0,
    }))
    res4 = simulate(df4, begin, end, round_trip_bps=0.0)
    assert len(res4.trades) == 0, res4.trades

    # Cooldown: a second spike 5 bars after the exit must not fill.
    close5 = np.full(len(ts), 100.0)
    high5 = close5.copy()
    low5 = close5.copy()
    close5[spike] = 110.0
    high5[spike + 1] = 120.0
    close5[spike + 5] = 110.0
    # and a legal one 15 bars after the exit bar (exit is spike+1, need index > exit+10)
    # Exit bar is spike+1. Pine allows the next entry only once
    # bar_index - exit_bar > 10, and the bar must still be inside NY AM.
    later = None
    for k in range(spike + 1 + 11, len(ts) - 2):
        if 8 <= close_ny[k].hour < 12:
            later = k
            break
    assert later is not None
    close5[later] = 110.0
    high5[later + 1] = 120.0
    df5 = prepare(pd.DataFrame({
        "ts": ts, "open": close5, "high": high5, "low": low5, "close": close5,
        "spread": 0.0,
    }))
    res5 = simulate(df5, begin, end, round_trip_bps=0.0)
    closed5 = res5.trades[res5.trades["reason"] != "open"]
    assert len(closed5) == 2, closed5

    print("self-test passed")


def run_instrument(df: pd.DataFrame, name: str, benches_extra: dict[str, pd.Series],
                   round_trip_bps: float | None, long_only: bool, end: pd.Timestamp) -> list[dict]:
    rows = []
    for label, start_s in WINDOWS:
        start = pd.Timestamp(start_s, tz=NY)
        if df["close_ny"].min() > start + pd.Timedelta(days=10):
            print(f"{name} {label}: no real 5-minute history back to {start_s}. Skipped.")
            continue
        if df["close_ny"].max() < start:
            print(f"{name} {label}: data ends before the window. Skipped.")
            continue
        res = simulate(df, start, end, long_only=long_only, round_trip_bps=round_trip_bps)
        benches = {"index hold": buy_and_hold(df, start, end)}
        for bname, series in benches_extra.items():
            benches[bname] = series
        row = report_window(f"{name} {label}", res, benches)
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--usa", default="/tmp/usa500_5m/bars.parquet")
    ap.add_argument("--spy", default="/tmp/spy5m_real")
    ap.add_argument("--spy-total", default="/tmp/spy_daily_all.parquet")
    ap.add_argument("--spy-price", default="/tmp/spy_daily_split.parquet")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return

    end = pd.Timestamp("2026-10-06 23:59", tz=NY)
    usa = load_usa500(args.usa)
    print(f"USA500 5-minute bars {len(usa):,}  "
          f"{usa['close_ny'].iloc[0]} -> {usa['close_ny'].iloc[-1]}")
    spy_total = load_daily_px(args.spy_total) if Path(args.spy_total).exists() else None
    spy_price = load_daily_px(args.spy_price) if Path(args.spy_price).exists() else None
    extra = {}
    if spy_price is not None:
        extra["SPY price"] = spy_price
    if spy_total is not None:
        extra["SPY total"] = spy_total

    print("\n=== USA500 index, long and short, quoted Dukascopy spread ===")
    print("Fully invested while in a trade, cash otherwise. One position.\n")
    rows = run_instrument(usa, "HTF both", extra, None, False, end)
    print_block(rows)

    print("=== USA500 index, long only, quoted spread ===")
    print("The book cannot short. Same entries, shorts dropped.\n")
    rows_l = run_instrument(usa, "HTF long", extra, None, True, end)
    print_block(rows_l)

    print("=== USA500 index, long and short, flat 5 bps and flat 30 bps ===")
    for bps in (5.0, 30.0):
        print(f"\n-- {bps:.0f} bps round trip --")
        print_block(run_instrument(usa, f"HTF {bps:.0f}bp", {}, bps, False, end))

    if Path(args.spy).exists():
        spy = load_spy(args.spy)
        print(f"\nSPY extended-hours 5-minute bars {len(spy):,}  "
              f"{spy['close_ny'].iloc[0]} -> {spy['close_ny'].iloc[-1]}")
        print("Real bars only. Older Robinhood 5-minute history came back "
              "interpolated at one price and was dropped.")
        print("Cost is 5 bps round trip (top of the mega-cap tier; the open "
              "spread is still unmeasured). SPY has no quote in this file.\n")
        print_block(run_instrument(spy, "SPY both", extra, 5.0, False, end))
        print("=== SPY long only, 5 bps ===\n")
        print_block(run_instrument(spy, "SPY long", extra, 5.0, True, end))

    blend = french_blend()
    if blend is not None:
        print()
        french_window_table(blend)
        print(f"\nFrench file ends {blend.index[-1]:%Y-%m-%d}. "
              "A 6-month or 1-year alpha t is not a measurement on this book: "
              "+1.78%/yr against ~5.5%/yr tracking error needs ~39 years to reach t=2.")


if __name__ == "__main__":
    main()
