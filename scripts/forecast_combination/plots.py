"""Draw the registered comparisons from saved, verified scores."""
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.monthly_origins.figures import COLOURS, MARKERS, cell_ink, heat_grid

OUT = ROOT / "outputs/forecast_combination"
FIG = ROOT / "figures/forecast_combination"
COLORS = {**COLOURS, "Combo": "#202124"}
SYMBOLS = {**MARKERS, "Combo": "*"}
LINESTYLES = ["--", ":", "-.", (0, (4, 2, 1, 2)), (0, (1, 2, 3, 2)), "-"]


def save(fig, name):
    FIG.mkdir(exist_ok=True, parents=True)
    fig.savefig(FIG / f"{name}.png", dpi=300, facecolor="white")
    svg = FIG / f"{name}.svg"
    fig.savefig(svg, dpi=300, facecolor="white", metadata={"Date": None})
    svg.write_bytes(("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n").encode())
    plt.close(fig)


def time_plot(data, config, name, title, ylabel, signed=False):
    origins = config["populations"]["P2"]
    months = pd.date_range(origins[0], origins[-1], freq="MS")
    frame = data.reindex(origins).copy()
    frame.index = pd.to_datetime(frame.index)
    frame = frame.reindex(months)
    fig, ax = plt.subplots(figsize=(11, 4.8))
    fig.subplots_adjust(left=.11, right=.98, bottom=.22, top=.81)
    ax.set_title(title, loc="left", size=15, weight="bold", pad=32)
    for method, style in zip([*config["methods"], "Combo"], LINESTYLES):
        ax.plot(months, frame[method], label=method, color=COLORS[method], linestyle=style,
                marker=SYMBOLS[method], markersize=7.8 if method == "Combo" else 4.2,
                markeredgecolor="#252525", markeredgewidth=.4, linewidth=2.4 if method == "Combo" else 1.3)
    for origin in config["idea_formed_at"]:
        date = pd.Timestamp(origin)
        ax.axvline(date, color="#767676", lw=.9, ls="--", zorder=0)
        ax.text(date, 1.02, "idea formed here", transform=ax.get_xaxis_transform(),
                ha="right" if origin == config["idea_formed_at"][0] else "center", fontsize=8, color="#444")
    if signed:
        ax.axhline(0, color="black", lw=1.2, zorder=0)
    else:
        ax.set_ylim(bottom=0)
    ax.set(xlabel="Planning origin", ylabel=ylabel)
    ticks = months[::2]
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -.25), ncol=6, frameon=False, fontsize=9)
    ax.grid(axis="y", alpha=.22)
    save(fig, name)


def risk_plot(endpoints, methods):
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6), sharey=True)
    fig.subplots_adjust(left=.15, right=.97, top=.78, bottom=.18, wspace=.13)
    fig.suptitle("How costly were the difficult windows?", x=.15, ha="left", weight="bold", size=15)
    fig.text(.15, .87, "Primary test  ·  each panel has its own cost scale", size=10)
    ends = endpoints[(endpoints.population == "P1") & (endpoints.penalty == 5000) & endpoints.method.isin(methods)]
    for ax, plan in zip(axes, ["LP", "Protected"]):
        g = ends[ends.plan == plan].set_index("method").loc[methods]
        upper = g.worst_excess.max() / 1e6
        for y, (method, row) in enumerate(g.iterrows()):
            q, worst = row.p90_excess / 1e6, row.worst_excess / 1e6
            ax.plot([q, worst], [y, y], color=COLORS[method], lw=2)
            for x, filled in [(q, False), (worst, True)]:
                ax.plot(x, y, marker=SYMBOLS[method], markersize=10, markeredgewidth=1.2,
                        markerfacecolor=COLORS[method] if filled else "white", markeredgecolor="#303030")
        ax.set(xlim=(0, upper * 1.08), ylim=(len(methods)-.5, -.5), yticks=np.arange(len(methods)),
               yticklabels=methods, xlabel="Excess cost (millions)")
        ax.set_title(plan, size=12, loc="left", weight="bold")
        ax.grid(axis="x", alpha=.22)
    handles = [Line2D([], [], linestyle="", marker="o", markeredgecolor="#303030", markerfacecolor=color, label=label)
               for color, label in [("white", "90th percentile"), ("#303030", "Worst origin")]]
    axes[1].legend(handles=handles, loc="upper right", fontsize=9, frameon=False, bbox_to_anchor=(1, 1.18), ncol=2)
    save(fig, "worst_case")


def heatmap(costs, config, methods):
    origins = config["populations"]["P3_all"]
    table = costs[costs.origin.isin(origins)].copy()
    table["method"] = table.method.replace({"Combo (4 methods)": "Combo"})
    table = table.pivot(index="method", columns="origin", values="excess").loc[methods, origins] / 1e6
    cmap = matplotlib.colormaps["YlOrRd"].with_extremes(bad="#dedede")
    norm = Normalize(vmin=0, vmax=table.max().max())
    fig, ax = plt.subplots(figsize=(12, 4.6))
    fig.subplots_adjust(left=.14, right=.98, top=.75, bottom=.12)
    im = ax.imshow(table, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
    fig.suptitle("Protected cost above hindsight by planning origin", x=.14, ha="left", weight="bold", size=15)
    fig.text(.14, .865, "Millions of model units  ·  darker cells cost more", size=10)
    heat_grid(ax, pd.to_datetime(origins).strftime("%b\n%Y"), methods)
    for i, method in enumerate(methods):
        ax.get_yticklabels()[i].set_color(COLORS[method])
        ax.get_yticklabels()[i].set_fontweight("bold")
        for j, origin in enumerate(origins):
            v = table.iloc[i, j]
            label, ink = ("N/A", "#454545") if pd.isna(v) else (f"{v:.2f}", cell_ink(cmap, norm, v))
            if method == "Combo" and origin in config["populations"]["P3_partial"]:
                label += "\n4 methods"
            ax.text(j, i, label, ha="center", va="center", color=ink, fontsize=8.8)
    fig.colorbar(im, ax=ax, location="bottom", shrink=.62, aspect=55, pad=.055, label="Protected excess cost (millions)")
    save(fig, "summary_heatmap")


def main():
    if "All Part B checks passed." not in (OUT / "logic_review.md").read_text(encoding="utf-8"):
        raise ValueError("Complete the results sanity review before publication.")
    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none", "svg.hashsalt": "combined-forecasts",
                         "axes.spines.right": False, "axes.spines.top": False})
    read = lambda name: pd.read_csv(OUT / name, float_precision="round_trip")
    config = json.loads((OUT / "config.json").read_text(encoding="utf-8"))
    methods = [*config["methods"], "Combo"]
    costs = read("costs.csv").query("plan == 'Protected' and penalty == 5000")
    time_plot(costs.pivot(index="origin", columns="method", values="excess") / 1e6, config, "excess_by_origin",
              "What did protection cost above hindsight?", "Protected excess cost (millions)")
    metrics = read("origin_metrics.csv")
    time_plot(metrics.pivot(index="origin", columns="method", values="signed_error"), config, "signed_error_by_origin",
              "Which forecasts ran too low or too high?", "Mean actual minus forecast gap (beds)", signed=True)
    risk_plot(read("endpoints.csv"), methods)
    heatmap(costs, config, methods)


if __name__ == "__main__":
    main()
