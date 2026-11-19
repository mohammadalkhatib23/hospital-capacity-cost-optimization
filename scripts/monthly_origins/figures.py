"""Render the monthly-origin figures from the verified monthly scores and interpretation tables."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "monthly_origins"
FIG = ROOT / "figures" / "monthly_origins"
METHODS = ["Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24"]
COLOURS = dict(zip(METHODS, ["#CC79A7", "#D55E00", "#E69F00", "#0072B2", "#009E73"]))
MARKERS = dict(zip(METHODS, ["D", "s", "^", "o", "P"]))
PLANS = ["Rule", "LP", "Protected"]
ORIGINS = pd.period_range("2025-04", "2026-04", freq="M").astype(str).tolist()
MILLION = 1_000_000
GREY = "#e3e4e4"
INK = "#202325"
COST = matplotlib.colormaps["YlOrRd"].with_extremes(bad=GREY)
SIGNED = matplotlib.colormaps["RdBu_r"].with_extremes(bad=GREY)


def save(fig, stem):
    fig.savefig(FIG / f"{stem}.png", dpi=300, facecolor="white")
    svg = FIG / f"{stem}.svg"
    fig.savefig(svg, dpi=300, facecolor="white", metadata={"Date": None})
    # Matplotlib's SVG writer adds trailing whitespace to path definitions.
    lines = svg.read_text(encoding="utf-8").splitlines()
    svg.write_bytes(("\n".join(line.rstrip() for line in lines) + "\n").encode())
    plt.close(fig)


def origin_labels():
    return [pd.Timestamp(origin).strftime("%b\n%Y") for origin in ORIGINS]


def cell_ink(cmap, norm, value):
    """Pick the higher-contrast foreground using relative sRGB luminance."""
    rgb = np.array(cmap(norm(value))[:3])
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return "#ffffff" if linear @ [0.2126, 0.7152, 0.0722] < 0.179 else "#111111"


def heat_grid(ax, xlabels, ylabels):
    nr, nc = len(ylabels), len(xlabels)
    ax.set_xticks(range(nc), labels=xlabels)
    ax.set_yticks(range(nr), labels=ylabels)
    ax.set_xticks(np.arange(-0.5, nc, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, nr, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="both", length=0)
    ax.tick_params(axis="x", pad=6)
    ax.tick_params(axis="y", pad=8)
    ax.xaxis.tick_top()
    for spine in ax.spines.values():
        spine.set_visible(False)


def method_labels(ax, methods):
    for label, method in zip(ax.get_yticklabels(), methods):
        label.set_color(COLOURS.get(method, "black"))
        label.set_fontweight("semibold")


def title(fig, left, heading, subtitle, *, title_y=0.94, sub_y=0.84):
    fig.text(left, title_y, heading, ha="left", va="center", fontsize=17, weight="bold")
    fig.text(left, sub_y, subtitle, ha="left", va="center", fontsize=10.5, color="#474c50")


def origin_heatmap(origin, monthly):
    rows = [(m, p) for m in METHODS for p in PLANS] + [("Hindsight", "Hindsight")]
    dates = pd.date_range(pd.Timestamp(origin) + pd.DateOffset(months=1), periods=6, freq="MS")
    columns = dates.strftime("%b\n%Y")
    keyed = monthly.set_index(["method", "plan", "month"])
    keys = [(m, p, d) for m, p in rows for d in dates.strftime("%Y-%m-%d")]
    values = keyed.reindex(pd.MultiIndex.from_tuples(keys, names=keyed.index.names))
    costs, short, spare = (values[name].to_numpy().reshape(len(rows), len(dates))
                           for name in ["monthly_cost", "shortfall", "spare"])
    costs = costs / MILLION
    panel_data = [
        (costs, short, spare, columns, "Monthly cost (millions)"),
        (costs.sum(axis=1, keepdims=True), short.sum(axis=1, keepdims=True),
         spare.sum(axis=1, keepdims=True), ["Total"], "Total cost (millions)"),
    ]
    fig = plt.figure(figsize=(10.9, 9.0))
    left = 0.222
    grid = fig.add_gridspec(1, 2, left=left, right=0.978, bottom=0.127, top=0.867,
                            width_ratios=[6.0, 1.13], wspace=0.035)
    heading = pd.Timestamp(origin).strftime("%B %Y") + " origin"
    title(fig, left, heading, "Realised monthly cost and beds short or spare", title_y=0.968, sub_y=0.937)
    for j, (data, shortage, surplus, labels, bar_label) in enumerate(panel_data):
        ax = fig.add_subplot(grid[0, j])
        norm = Normalize(0, np.nanmax(data))
        image = ax.imshow(data, norm=norm, cmap=COST, aspect="auto", interpolation="nearest")
        names = [f"{m} / {p}" if m != "Hindsight" else m for m, p in rows]
        heat_grid(ax, labels, names if j == 0 else [""] * len(rows))
        if j == 0:
            method_labels(ax, [m for m, _ in rows])
        for row, col in np.ndindex(data.shape):
            cost = data[row, col]
            if np.isnan(cost):
                ax.text(col, row, "N/A", ha="center", va="center", color="#565a5d", fontsize=9)
                continue
            n, extra = shortage[row, col], surplus[row, col]
            note = f"short {n:,.0f}" if round(n) else f"spare {extra:,.0f}"
            ax.text(col, row, f"{cost:.2f}m\n{note}", color=cell_ink(COST, norm, cost),
                    ha="center", va="center", fontsize=8.8, linespacing=1.14)
        for boundary in np.arange(2.5, 15, 3):
            ax.axhline(boundary, color="#595f62", lw=1)
        box = ax.get_position()
        cax = fig.add_axes([box.x0, 0.086, box.width, 0.011])
        bar = fig.colorbar(image, cax=cax, orientation="horizontal")
        if j == 1:
            bar.set_ticks([0, np.nanmax(data)])
            bar.set_ticklabels(["0", f"{np.nanmax(data):.1f}"])
        bar.outline.set_visible(False)
        bar.ax.tick_params(length=2, labelsize=8)
        bar.set_label(bar_label, fontsize=9, labelpad=1)
    note = "Total shows summed shortfall bed-months, or spare bed-months if shortfall rounds to zero."
    if np.isnan(costs).any():
        note += "  N/A = SARIMA-24 unavailable."
    fig.text(left, 0.018, note, fontsize=8.9, color="#474c50")
    save(fig, f"origin_{origin}_heatmap")


def summary_heatmap(metrics, field, filename, heading, subtitle, bar_label, *, beds=False, diverging=False):
    matrix = metrics.pivot(index="method", columns="origin", values=field).reindex(index=METHODS, columns=ORIGINS)
    values = matrix.to_numpy() / (1 if beds else MILLION)
    limit = float(np.nanmax(np.abs(values)))
    cmap = SIGNED if diverging else COST
    norm = Normalize(-limit if diverging else 0, limit)
    fig = plt.figure(figsize=(13.7, 4.35))
    left = 0.139
    ax = fig.add_axes([left, 0.205, 0.846, 0.48])
    title(fig, left, heading, subtitle, title_y=0.937, sub_y=0.850)
    image = ax.imshow(values, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
    heat_grid(ax, origin_labels(), METHODS)
    method_labels(ax, METHODS)
    for row, col in np.ndindex(values.shape):
        v = values[row, col]
        if np.isnan(v):
            label, color = "N/A", "#565a5d"
        else:
            fmt = "+,.0f" if beds else ("+.2f" if diverging else ".2f")
            rounded = round(v, 0 if beds else 2)
            label = format(v if rounded else 0.0, fmt)
            color = cell_ink(cmap, norm, v)
        ax.text(col, row, label, ha="center", va="center", fontsize=10.8, color=color)
    cax = fig.add_axes([left, 0.118, 0.42, 0.024])
    bar = fig.colorbar(image, cax=cax, orientation="horizontal")
    bar.outline.set_visible(False)
    bar.ax.tick_params(length=2, labelsize=8)
    bar.set_label(bar_label, labelpad=1.5, fontsize=9)
    fig.text(0.985, 0.100, "N/A = SARIMA-24 unavailable", ha="right", color="#565a5d", fontsize=9)
    save(fig, filename)


def best_heatmap(winners):
    data = winners.set_index("origin").loc[ORIGINS]
    excess = data.best_excess.to_numpy() / MILLION
    norm = Normalize(0, np.nanmax(excess))
    fig = plt.figure(figsize=(13.7, 3.3))
    left = 0.018
    title(fig, left, "Cheapest plan at each fully comparable origin",
          "Each cell gives the method, plan and cost above that origin’s hindsight benchmark.", title_y=0.926, sub_y=0.794)
    ax = fig.add_axes([left, 0.202, 0.966, 0.338])
    ax.imshow(excess.reshape(1, -1), cmap=COST, norm=norm, aspect="auto", interpolation="nearest")
    heat_grid(ax, origin_labels(), [""])
    for col, (_, row) in enumerate(data.iterrows()):
        if not row.common:
            label, color = "Unranked\nSARIMA-24\nunavailable", "#41464a"
        else:
            method = row.best_method.replace("Seasonal naive", "Seasonal\nnaive")
            label = f"{method}\n{row.best_plan}\n+{excess[col]:.2f}m"
            color = cell_ink(COST, norm, excess[col])
        ax.text(col, 0, label, color=color, ha="center", va="center", fontsize=9.25, linespacing=1.18)
    note = f"Darker cells have higher excess cost. Rankings use the {data.common.sum()} origins with all five methods."
    if data.best_plan.eq("Rule/LP").any():
        note += "  Rule/LP indicates a tie within solver precision."
    fig.text(left, 0.075, note, fontsize=9.5, color="#474c50")
    save(fig, "summary_best_plan")


def protection_scatter(metrics):
    fig, ax = plt.subplots(figsize=(8.7, 5.4))
    fig.subplots_adjust(left=0.122, bottom=0.197, right=0.975, top=0.805)
    title(fig, 0.122, "Forecast error and the value of protection",
          "Each marker is one method at one origin. Above zero, protection reduced cost.", title_y=0.944, sub_y=0.863)
    ax.grid(color="#e3e5e7", lw=0.75)
    ax.axhline(0, color="#333333", lw=1.25)
    ax.axvline(0, color="#333333", lw=1.25)
    for method in METHODS:
        frame = metrics.loc[metrics.method.eq(method)]
        ax.scatter(frame.signed_error, frame.value_protection / MILLION, color=COLOURS[method],
                   marker=MARKERS[method], edgecolor="#262626", linewidth=0.85, s=48, alpha=0.85,
                   label=method, zorder=3)
    ax.set_xlabel("Mean signed gap error (beds)   ·   negative = forecast high, positive = forecast low", labelpad=6)
    ax.set_ylabel("LP cost minus Protected cost (millions)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.190), frameon=False, ncol=5,
              handletextpad=0.35, columnspacing=1.05, fontsize=9.3)
    save(fig, "protection_vs_error_direction")


def main():
    verification = (OUT / "verification.md").read_text(encoding="utf-8")
    if "All Phase 1 checks passed." not in verification or "**FAIL**" in verification:
        raise RuntimeError("Figures require all Phase 1 checks to pass.")
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                                "axes.labelcolor": INK, "svg.hashsalt": "monthly-origins", "svg.fonttype": "none"})
    monthly = pd.read_csv(OUT / "monthly_costs.csv", float_precision="round_trip")
    metrics = pd.read_csv(OUT / "method_origin.csv", float_precision="round_trip")
    winners = pd.read_csv(OUT / "winners.csv", float_precision="round_trip")
    FIG.mkdir(parents=True, exist_ok=True)
    for origin in ORIGINS:
        origin_heatmap(origin, monthly.loc[monthly.origin.eq(origin)])
    summary_heatmap(metrics, "signed_error", "summary_signed_gap_error", "Which forecasts ran high or low?",
                    "Mean actual gap minus forecast gap over the next six months.",
                    "Mean signed error (beds)   ·   cool = too high, warm = too low", beds=True, diverging=True)
    summary_heatmap(metrics, "lp_excess", "summary_lp_excess", "How close did each optimised plan come to hindsight?",
                    "Each cell subtracts the cheapest achievable cost with the six actual gaps known.",
                    "LP cost above hindsight (millions)")
    summary_heatmap(metrics, "value_optimisation", "summary_value_of_optimisation", "How much did optimisation save?",
                    "Rule cost minus LP cost for the same forecast. Larger numbers mean larger savings.",
                    "Cost saved by optimisation (millions)")
    summary_heatmap(metrics, "value_protection", "summary_value_of_protection", "When did protection pay off?",
                    "LP cost minus Protected cost for the same forecast. Positive numbers mean protection helped.",
                    "Value of protection (millions)   ·   cool = extra cost, warm = saving", diverging=True)
    best_heatmap(winners)
    protection_scatter(metrics)


if __name__ == "__main__":
    main()
