"""
charts.py — dependency-free ASCII charts for terminal output.

Pure ASCII (no Unicode blocks) so it renders identically in PowerShell,
cmd.exe, and any log file. Used by the portfolio and Monte Carlo CLIs, and
runnable standalone against the CSVs the other tools write:

    py -m rhengine.charts --equity-csv equity_curve.csv
    py -m rhengine.charts --trades-csv backtest_trades.csv
"""

import argparse


def line_chart(values, width=68, height=14, title=None, y_fmt="{:,.2f}",
               x_labels=None):
    """Render a series as an ASCII line chart string.

    values: list of numbers. Downsampled to `width` columns (last value in
    each bucket, so an equity curve keeps its true endpoint).
    x_labels: optional (left, right) strings printed under the axis.
    """
    if not values:
        return "(no data)"
    n = len(values)
    cols = min(width, n)
    # bucket -> representative value (last of bucket)
    pts = [values[min(n - 1, ((i + 1) * n) // cols - 1)] for i in range(cols)]
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or abs(hi) or 1.0

    # row index (0=top) for each column
    rows_for = [int(round((hi - p) / span * (height - 1))) for p in pts]
    grid = [[" "] * cols for _ in range(height)]
    for x, r in enumerate(rows_for):
        if x > 0:
            # vertical fill between consecutive points so steep moves stay connected
            r0 = rows_for[x - 1]
            step = 1 if r > r0 else -1
            for rr in range(r0 + step, r, step):
                grid[rr][x] = "|" if abs(r - r0) > 1 else "*"
        grid[r][x] = "*"

    gutter = max(len(y_fmt.format(hi)), len(y_fmt.format(lo)))
    out = []
    if title:
        out.append(title)
    for r in range(height):
        if r == 0:
            label = y_fmt.format(hi)
        elif r == height - 1:
            label = y_fmt.format(lo)
        elif r == height // 2:
            label = y_fmt.format(lo + (hi - lo) * (1 - r / (height - 1)))
        else:
            label = ""
        out.append("{:>{g}} |{}".format(label, "".join(grid[r]), g=gutter))
    out.append("{:>{g}} +{}".format("", "-" * cols, g=gutter))
    if x_labels:
        left, right = x_labels
        pad = max(0, cols - len(left) - len(right))
        out.append("{:>{g}}  {}{}{}".format("", left, " " * pad, right, g=gutter))
    return "\n".join(out)


def histogram(values, bins=24, width=50, title=None, x_fmt="{:+.2f}"):
    """Horizontal-bar histogram string."""
    if not values:
        return "(no data)"
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for v in values:
        i = min(bins - 1, int((v - lo) / span * bins))
        counts[i] += 1
    peak = max(counts)
    out = []
    if title:
        out.append(title)
    for i, c in enumerate(counts):
        edge = lo + span * i / bins
        bar = "#" * int(round(c / peak * width)) if peak else ""
        out.append("  {:>8} | {:<{w}} {}".format(
            x_fmt.format(edge), bar, c if c else "", w=width))
    return "\n".join(out)


def drawdown_series(equity_values):
    """Percent drawdown-from-peak series (negative numbers, 0 at peaks)."""
    out, peak = [], float("-inf")
    for eq in equity_values:
        peak = max(peak, eq)
        out.append((eq / peak - 1.0) * 100 if peak > 0 else 0.0)
    return out


def equity_report(curve, width=68, height=12):
    """Equity + drawdown charts from a [(date, equity), ...] curve."""
    dates = [d for d, _ in curve]
    eq = [v for _, v in curve]
    parts = [
        line_chart(eq, width=width, height=height, y_fmt="{:,.0f}",
                   title="\nEquity curve ($)",
                   x_labels=(dates[0], dates[-1]) if dates else None),
        line_chart(drawdown_series(eq), width=width, height=max(6, height // 2),
                   y_fmt="{:.1f}%", title="\nDrawdown from peak (%)",
                   x_labels=(dates[0], dates[-1]) if dates else None),
    ]
    return "\n".join(parts)


def _read_csv(path):
    import csv
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description="ASCII charts from sim CSVs.")
    ap.add_argument("--equity-csv", default=None,
                    help="equity curve CSV from rhengine.portfolio")
    ap.add_argument("--trades-csv", default=None,
                    help="trades CSV from rhengine.backtest")
    ap.add_argument("--width", type=int, default=68)
    ap.add_argument("--height", type=int, default=14)
    args = ap.parse_args()

    if not (args.equity_csv or args.trades_csv):
        ap.error("pass --equity-csv and/or --trades-csv")

    if args.equity_csv:
        rows = _read_csv(args.equity_csv)
        curve = [(r["date"], float(r["equity"])) for r in rows]
        print(equity_report(curve, width=args.width, height=args.height))

    if args.trades_csv:
        rows = _read_csv(args.trades_csv)
        rs = [float(r["r_multiple"]) for r in rows if r.get("r_multiple")]
        print(histogram(rs, title="\nTrade R-multiple distribution "
                                  "({} trades)".format(len(rs))))
        cum, total = [], 0.0
        for r in rs:
            total += r
            cum.append(total)
        print(line_chart(cum, width=args.width, height=args.height,
                         y_fmt="{:+,.1f}", title="\nCumulative R over trade sequence",
                         x_labels=("trade 1", "trade {}".format(len(cum)))))


if __name__ == "__main__":
    main()
