"""
viz.py — matplotlib chart helpers shared by the notebooks/ validation labs.

Every lab notebook draws through these functions so the charts read as one
system: fixed categorical hue order (never cycled), a single-hue sequential
ramp for magnitude, a blue<->red diverging ramp (gray midpoint) for polarity,
recessive grid/axes, thin marks, direct labels where they matter. Functions
take plain data (lists / numpy / pandas), return (fig, ax) so notebooks can
compose or save them.

Nothing here talks to the engine — pair with:
    backtest / optimize / ablate / crossval / ic / sanity / mc_refined
"""

import numpy as np

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# ---------------------------------------------------------------- palette --
# Validated light-mode palette (dataviz reference instance). Categorical hue
# order is a CVD-safety mechanism — assign in order, never cycle or shuffle.
CAT = ["#2a78d6", "#008300", "#e87ba4", "#eda100",
       "#1baf7a", "#eb6834", "#4a3aa7", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
            "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281",
            "#0d366b"]
DIV_NEG, DIV_MID, DIV_POS = "#e34948", "#f0efec", "#2a78d6"  # red<->gray<->blue
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
GOOD, CRITICAL, WARNING = "#0ca30c", "#d03b3b", "#fab219"  # status — never series

SEQ_CMAP = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
DIV_CMAP = LinearSegmentedColormap.from_list("div_rb", [DIV_NEG, DIV_MID, DIV_POS])


def use_style():
    """Apply the lab style globally (call once per notebook)."""
    matplotlib.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "Arial", "DejaVu Sans"],
        "text.color": INK, "axes.labelcolor": INK2,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "xtick.labelsize": 9, "ytick.labelsize": 9,
        "axes.edgecolor": BASELINE, "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.axisbelow": True,
        "axes.titlecolor": INK, "axes.titlesize": 11, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "axes.labelsize": 9.5,
        "legend.frameon": False, "legend.fontsize": 9,
        "figure.figsize": (9, 4.2), "figure.dpi": 100,
        "lines.linewidth": 2.0,
    })


def _ax(ax, figsize=None):
    if ax is not None:
        return ax.figure, ax
    return plt.subplots(figsize=figsize or matplotlib.rcParams["figure.figsize"])


# ----------------------------------------------------------------- charts --

def fan_chart(equity, percentiles=(5, 25, 50, 75, 95), title=None,
              xlabel="trade #", ax=None):
    """Percentile fan of a Monte Carlo equity-path matrix (paths x steps).
    Bands are steps of the sequential ramp (magnitude, one hue); the median is
    the only full-strength line."""
    fig, ax = _ax(ax)
    eq = np.asarray(equity, dtype=float)
    x = np.arange(1, eq.shape[1] + 1)
    qs = {p: np.percentile(eq, p, axis=0) for p in percentiles}
    ps = sorted(percentiles)
    pairs = [(ps[i], ps[-1 - i]) for i in range(len(ps) // 2)]
    for depth, (lo, hi) in enumerate(pairs):
        ax.fill_between(x, qs[lo], qs[hi], color=SEQ_BLUE[2 + 2 * depth],
                        alpha=0.55, linewidth=0, label=f"p{lo}-p{hi}")
    med = ps[len(ps) // 2] if len(ps) % 2 else 50
    ax.plot(x, np.percentile(eq, med, axis=0), color=SEQ_BLUE[8],
            label=f"median (p{med})")
    ax.axhline(1.0, color=BASELINE, linewidth=0.8)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("equity multiple")
    if title:
        ax.set_title(title)
    ax.legend(loc="upper left")
    ax.margins(x=0)
    return fig, ax


def dist_hist(vals, bins=60, pct_marks=(5, 50, 95), title=None, xlabel=None,
              x_fmt="{:.2f}", ref=None, ref_label=None, ax=None):
    """Distribution histogram (single series -> single hue, no legend box).
    Percentile markers are labeled directly; `ref` draws one reference line
    (e.g. the observed backtest value) in ink."""
    fig, ax = _ax(ax)
    vals = np.asarray(vals, dtype=float)
    ax.hist(vals, bins=bins, color=CAT[0], edgecolor=SURFACE, linewidth=0.4)
    ymax = ax.get_ylim()[1]
    for p in pct_marks:
        v = np.percentile(vals, p)
        ax.axvline(v, color=INK2, linewidth=1.0, linestyle=":")
        ax.annotate(f"p{p} {x_fmt.format(v)}", (v, ymax * 0.97),
                    ha="center", va="top", fontsize=8.5, color=INK2,
                    rotation=90)
    if ref is not None:
        ax.axvline(ref, color=INK, linewidth=1.4)
        ax.annotate(ref_label or x_fmt.format(ref), (ref, ymax * 0.55),
                    ha="right", va="center", fontsize=9, color=INK, rotation=90)
    ax.set_ylabel("paths")
    if xlabel:
        ax.set_xlabel(xlabel)
    if title:
        ax.set_title(title)
    return fig, ax


def ic_timeseries(daily_ic, roll=21, title=None, ax=None):
    """Daily cross-sectional IC (faint dots) + rolling mean (line) + zero
    baseline. `daily_ic` is a pandas Series indexed by date."""
    fig, ax = _ax(ax)
    ax.plot(daily_ic.index, daily_ic.values, ".", color=SEQ_BLUE[3],
            markersize=3.5, alpha=0.7, label="daily IC")
    rm = daily_ic.rolling(roll, min_periods=max(3, roll // 3)).mean()
    ax.plot(rm.index, rm.values, color=CAT[0], label=f"{roll}d rolling mean")
    ax.axhline(0, color=BASELINE, linewidth=0.8)
    mu = float(daily_ic.mean())
    ax.axhline(mu, color=INK2, linewidth=1.0, linestyle=":")
    ax.annotate(f"mean {mu:+.3f}", (daily_ic.index[-1], mu),
                ha="right", va="bottom", fontsize=8.5, color=INK2)
    ax.set_ylabel("rank IC")
    if title:
        ax.set_title(title)
    ax.legend(loc="upper left")
    return fig, ax


def bars_signed(labels, values, title=None, xlabel=None, fmt="{:+.3f}",
                ax=None, highlight=None):
    """Horizontal bars for a signed metric (avg R, IC, PF-1): polarity uses
    the diverging poles (blue positive / red negative). `highlight` marks one
    row (e.g. the production config) with an ink outline."""
    fig, ax = _ax(ax, figsize=(9, max(2.2, 0.42 * len(labels) + 1)))
    y = np.arange(len(labels))[::-1]
    vals = np.asarray(values, dtype=float)
    colors = [DIV_POS if v >= 0 else DIV_NEG for v in vals]
    bars = ax.barh(y, vals, height=0.62, color=colors)
    if highlight is not None:
        for i, lbl in enumerate(labels):
            if lbl == highlight:
                bars[i].set_edgecolor(INK)
                bars[i].set_linewidth(1.4)
    span = max(abs(vals.max()), abs(vals.min()), 1e-9)
    for yi, v in zip(y, vals):
        ax.annotate(fmt.format(v), (v, yi),
                    xytext=(4 if v >= 0 else -4, 0), textcoords="offset points",
                    va="center", ha="left" if v >= 0 else "right",
                    fontsize=8.5, color=INK2)
    ax.set_yticks(y, labels)
    ax.axvline(0, color=BASELINE, linewidth=0.8)
    ax.set_xlim(-span * 1.25, span * 1.25)
    if xlabel:
        ax.set_xlabel(xlabel)
    if title:
        ax.set_title(title)
    ax.grid(axis="y", visible=False)
    return fig, ax


def paired_bars(group_labels, series_a, series_b, label_a="IS", label_b="OOS",
                title=None, ylabel=None, fmt="{:+.2f}", ax=None):
    """Two series side-by-side per group (e.g. IS vs OOS per fold).
    Fixed hue order: series A slot 1 (blue), series B slot 2 (green)."""
    fig, ax = _ax(ax)
    x = np.arange(len(group_labels))
    w = 0.36
    a = np.asarray(series_a, dtype=float)
    b = np.asarray(series_b, dtype=float)
    ax.bar(x - w / 2, a, width=w - 0.04, color=CAT[0], label=label_a)
    ax.bar(x + w / 2, b, width=w - 0.04, color=CAT[1], label=label_b)
    for xi, v in list(zip(x - w / 2, a)) + list(zip(x + w / 2, b)):
        ax.annotate(fmt.format(v), (xi, v), xytext=(0, 3 if v >= 0 else -10),
                    textcoords="offset points", ha="center", fontsize=8,
                    color=INK2)
    ax.axhline(0, color=BASELINE, linewidth=0.8)
    ax.set_xticks(x, group_labels)
    if ylabel:
        ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    ax.legend(loc="best")
    ax.grid(axis="x", visible=False)
    return fig, ax


def sweep_lines(x_vals, is_vals, oos_vals, param, default=None, title=None,
                ylabel="avg R", ax=None):
    """One threshold's OAT sweep: IS and OOS curves over the candidate values.
    A value that only wins in-sample is noise — the OOS line is the verdict."""
    fig, ax = _ax(ax)
    ax.plot(x_vals, is_vals, color=CAT[0], marker="o", markersize=5, label="IS")
    ax.plot(x_vals, oos_vals, color=CAT[1], marker="o", markersize=5, label="OOS")
    ax.axhline(0, color=BASELINE, linewidth=0.8)
    if default is not None:
        ax.axvline(default, color=INK2, linewidth=1.0, linestyle=":")
        ax.annotate("default", (default, ax.get_ylim()[1]), ha="center",
                    va="top", fontsize=8.5, color=INK2)
    ax.set_xlabel(param)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    ax.legend(loc="best")
    return fig, ax


def is_oos_scatter(is_vals, oos_vals, labels=None, title=None,
                   xlabel="IS", ylabel="OOS", ax=None):
    """IS vs OOS scatter with y=x guide. Points in the lower-right wedge
    (IS win, OOS loss) are the overfits."""
    fig, ax = _ax(ax, figsize=(5.6, 5.2))
    is_v = np.asarray(is_vals, dtype=float)
    oos_v = np.asarray(oos_vals, dtype=float)
    lim = max(np.abs(np.concatenate([is_v, oos_v])).max() * 1.15, 1e-6)
    ax.axhspan(-lim, 0, xmin=0.5, color=DIV_NEG, alpha=0.06, linewidth=0)
    ax.plot([-lim, lim], [-lim, lim], color=BASELINE, linewidth=0.8)
    ax.axhline(0, color=BASELINE, linewidth=0.8)
    ax.axvline(0, color=BASELINE, linewidth=0.8)
    ax.scatter(is_v, oos_v, s=42, color=CAT[0], edgecolor=SURFACE,
               linewidth=0.8, zorder=3)
    if labels is not None:
        for x, y, l in zip(is_v, oos_v, labels):
            ax.annotate(str(l), (x, y), xytext=(5, 4),
                        textcoords="offset points", fontsize=8, color=INK2)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    return fig, ax


def heatmap(grid_df, fmt="{:+.2f}", diverging=True, center=0.0, title=None,
            cbar_label=None, ax=None):
    """Annotated heatmap of a pivoted DataFrame (e.g. grid sweep: param1 x
    param2 -> OOS avg R). Diverging (polarity) by default; set
    diverging=False for pure magnitude."""
    fig, ax = _ax(ax, figsize=(1.1 * len(grid_df.columns) + 2.4,
                               0.55 * len(grid_df.index) + 1.6))
    vals = grid_df.values.astype(float)
    if diverging:
        span = max(abs(np.nanmax(vals) - center), abs(np.nanmin(vals) - center),
                   1e-9)
        im = ax.imshow(vals, cmap=DIV_CMAP, vmin=center - span,
                       vmax=center + span, aspect="auto")
    else:
        im = ax.imshow(vals, cmap=SEQ_CMAP, aspect="auto")
    ax.set_xticks(range(len(grid_df.columns)), [str(c) for c in grid_df.columns])
    ax.set_yticks(range(len(grid_df.index)), [str(i) for i in grid_df.index])
    ax.set_xlabel(grid_df.columns.name or "")
    ax.set_ylabel(grid_df.index.name or "")
    ax.grid(visible=False)
    lo, hi = im.get_clim()
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            v = vals[i, j]
            if np.isnan(v):
                continue
            frac = (v - lo) / (hi - lo) if hi > lo else 0.5
            ax.annotate(fmt.format(v), (j, i), ha="center", va="center",
                        fontsize=8.5,
                        color=SURFACE if abs(frac - 0.5) > 0.32 else INK)
    cb = fig.colorbar(im, ax=ax, shrink=0.85)
    cb.outline.set_visible(False)
    if cbar_label:
        cb.set_label(cbar_label, color=INK2, fontsize=9)
    if title:
        ax.set_title(title)
    return fig, ax


def equity_drawdown(equity, dates=None, title=None, axes=None):
    """Stacked equity curve + drawdown panels (one y-scale each — never a
    dual-axis chart). `equity` is a 1-D array of equity values."""
    if axes is None:
        fig, axes = plt.subplots(2, 1, sharex=True, figsize=(9, 5.4),
                                 height_ratios=[2.2, 1],
                                 gridspec_kw={"hspace": 0.08})
    else:
        fig = axes[0].figure
    eq = np.asarray(equity, dtype=float)
    x = np.arange(len(eq)) if dates is None else dates
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1.0) * 100
    axes[0].plot(x, eq, color=CAT[0])
    axes[0].set_ylabel("equity")
    if title:
        axes[0].set_title(title)
    axes[1].fill_between(x, dd, 0, color=DIV_NEG, alpha=0.35, linewidth=0)
    axes[1].plot(x, dd, color=DIV_NEG, linewidth=1.2)
    axes[1].set_ylabel("drawdown %")
    mn = dd.min()
    axes[1].annotate(f"max {mn:.1f}%", (x[int(np.argmin(dd))], mn),
                     xytext=(0, -2), textcoords="offset points",
                     ha="center", va="top", fontsize=8.5, color=INK2)
    for a in axes:
        a.margins(x=0)
    return fig, axes


def check_table(rows, title=None, ax=None):
    """Render sanity-battery rows [(name, status, value, note)] as a table
    figure. Status uses the reserved status palette WITH a symbol per row —
    never color alone."""
    marks = {"PASS": ("#0ca30c", "ok  PASS"), "WARN": ("#b97b00", "!   WARN"),
             "FAIL": ("#d03b3b", "x   FAIL"), "SKIP": (MUTED, "-   SKIP")}
    fig, ax = _ax(ax, figsize=(9.5, 0.42 * len(rows) + 1.2))
    ax.set_axis_off()
    if title:
        ax.set_title(title, pad=18)
    for i, (name, status, value, note) in enumerate(rows):
        y = 1 - (i + 0.5) / len(rows)
        color, mark = marks.get(status, (MUTED, status))
        ax.annotate(mark, (0.00, y), xycoords="axes fraction", fontsize=9.5,
                    color=color, fontweight="bold", family="monospace",
                    va="center")
        ax.annotate(name, (0.12, y), xycoords="axes fraction", fontsize=9.5,
                    color=INK, va="center")
        ax.annotate(str(value), (0.40, y), xycoords="axes fraction",
                    fontsize=9, color=INK2, va="center")
        ax.annotate(str(note), (0.62, y), xycoords="axes fraction",
                    fontsize=8.5, color=MUTED, va="center")
    return fig, ax
