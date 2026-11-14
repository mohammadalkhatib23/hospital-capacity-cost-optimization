"""Figures for the clean checkpoint README. Reads only outputs/clean_checkpoint/; nothing is refitted or re-solved.

Per origin: three forecast figures (occupied, available, gap: Actual, Seasonal naive and SARIMA-24), the cost bars
(Rule and LP side by side, operating solid, shortfall hatched, hindsight line) and the monthly cost heatmap. Across
origins: excess over hindsight per plan. Writes 300 dpi PNG and SVG files to figures/clean_checkpoint/.
"""

from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "outputs/clean_checkpoint"
FIG = REPO / "figures/clean_checkpoint"
ORIGINS = {"O1_sep2025": ("o1", "Origin 1, September 2025"), "O2_mar2026": ("o2", "Origin 2, March 2026")}
METHODS = ("Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24")
COLOURS = {"Seasonal naive": "#E69F00", "SARIMA-24": "#0072B2", "ExtraTrees-24": "#009E73", "Mean-24": "#CC79A7", "Persistence": "#D55E00"}
SHOWN = {"Seasonal naive": dict(marker="D", ls=(0, (5, 2)), tag="SN"), "SARIMA-24": dict(marker="s", ls="-", tag="S24")}
SERIES = (("occupied", "forecast_occupied", "Occupied beds"), ("available", "forecast_available", "Available beds"),
          ("gap", "forecast_gap", "Planning gap (beds)"))
TITLES = {"occupied": "Occupied beds", "available": "Available beds", "gap": "Planning gap"}


def read(path):
    return pd.read_csv(path, parse_dates=["month"], float_precision="round_trip") if "month" in pd.read_csv(path, nrows=0).columns \
        else pd.read_csv(path, float_precision="round_trip")


def tint(colour, share=0.55):
    """The colour mixed with white, for the LP bars."""
    rgb = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)]) / 255
    return tuple(rgb + (1 - rgb) * share)


def style(ax):
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def save(fig, stem, plt):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / f"{stem}.png", dpi=300, facecolor="white")
    fig.savefig(FIG / f"{stem}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def forecast_figure(key, series, col, ylabel, plt):
    from matplotlib.transforms import blended_transform_factory
    stem, label = ORIGINS[key]
    fc, act = read(DATA / key / "forecasts.csv"), read(DATA / key / "actuals.csv").sort_values("month").reset_index(drop=True)
    paths = {name: fc[fc.method == name].sort_values("month").reset_index(drop=True) for name in SHOWN}
    months = act.month
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for name, st in SHOWN.items():
        ax.plot(months, paths[name][col], color=COLOURS[name], marker=st["marker"], ls=st["ls"], ms=6, lw=1.8, label=name, zorder=3)
    ax.plot(months, act[series], color="black", lw=3, marker="o", ms=7, label="Actual", zorder=4)
    values = np.concatenate([act[series].to_numpy()] + [paths[name][col].to_numpy() for name in SHOWN])
    span = values.max() - values.min()
    ax.set_ylim(values.min() - 0.12 * span, values.max() + 0.18 * span)
    points_per_bed = 217 / (1.3 * span)  # the axes are about 217 points tall
    for i, (mo, v) in enumerate(zip(months, act[series])):
        near = [paths[name][col].iloc[i] - v for name in SHOWN]  # label on the side with more room from the forecasts
        room = {True: min([d for d in near if d > 0], default=np.inf), False: min([-d for d in near if d <= 0], default=np.inf)}
        above = room[True] > room[False]
        if max(room.values()) * points_per_bed < 5:  # both forecasts sit on the actual marker: label away from the neighbouring months
            above = np.mean([act[series].iloc[j] for j in (i - 1, i + 1) if 0 <= j < len(act)]) < v
        blocked = {side: any(5 < abs(d) * points_per_bed < 22 for d in near if (d > 0) == side) for side in (True, False)}
        if blocked[above] and not blocked[not above]:  # a forecast marker sits where the label would go
            above = not above
        ax.annotate(f"{v:,.0f}", (mo, v), xytext=(0, 9 if above else -10), textcoords="offset points", ha="center",
                    va="bottom" if above else "top", fontsize=8, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85), zorder=5)
    ax.set_xticks(months, [f"{mo:%b %Y}" for mo in months])
    trans = blended_transform_factory(ax.transData, ax.transAxes)
    err = {name: np.abs(act[series].to_numpy() - paths[name][col].to_numpy()) for name in SHOWN}
    for mo, sn, s24 in zip(months, err["Seasonal naive"], err["SARIMA-24"]):
        name = "Seasonal naive" if sn < s24 else "SARIMA-24"
        ax.text(mo, -0.115, SHOWN[name]["tag"], transform=trans, ha="center", va="top", fontsize=8.5, fontweight="semibold", color=COLOURS[name])
    ax.text(-0.01, -0.115, "Closer:", transform=ax.transAxes, ha="right", va="top", fontsize=8, color="#555555")
    ax.set_xlabel("Month", labelpad=20)
    ax.set_ylabel(ylabel)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    style(ax)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=9, handlelength=3, borderaxespad=0.2)
    ax.set_title(f"{TITLES[series]}: forecast vs actual ({label})", loc="left", fontweight="semibold", pad=28)
    fig.tight_layout()
    save(fig, f"{stem}_forecast_{series}", plt)


def bar_axis(ax, positions):
    """Rule / LP under each bar pair and the method name under each group."""
    ax.set_xticks([x for _, _, x in positions], [dec for _, dec, _ in positions], fontsize=8.5)
    ax.tick_params(axis="x", length=0)
    for i, name in enumerate(METHODS):
        ax.text(i, -0.085, name, transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=10, fontweight="semibold")


def cost_bars(key, plt):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    stem, label = ORIGINS[key]
    totals = read(DATA / key / "totals.csv").set_index("plan")
    hindsight = totals.loc["Hindsight", "total"] / 1e6
    fig, ax = plt.subplots(figsize=(11, 5.6))
    positions = [(name, dec, i + off) for i, name in enumerate(METHODS) for dec, off in (("Rule", -0.2), ("LP", 0.2))]
    top = totals.drop("Hindsight").total.max() / 1e6
    for name, dec, x in positions:
        r = totals.loc[f"{name} {dec}"]
        face = COLOURS[name] if dec == "Rule" else tint(COLOURS[name])
        ax.bar(x, r.operating / 1e6, 0.38, color=face, edgecolor=COLOURS[name], lw=0.8, zorder=2)
        if r.shortfall_cost > 0:
            ax.bar(x, r.shortfall_cost / 1e6, 0.38, bottom=r.operating / 1e6, color="white", edgecolor=COLOURS[name], hatch="////", lw=0.8, zorder=2)
        ax.text(x, r.total / 1e6 + 0.012 * top, f"{r.total / 1e6:.2f}", ha="center", va="bottom", fontsize=8.5)
    ax.axhline(hindsight, color="black", lw=1.6, zorder=3)
    bar_axis(ax, positions)
    ax.set_ylabel("Realised cost (million model units)")
    ax.set_ylim(0, top * 1.2)
    style(ax)
    handles = [Patch(facecolor="#5b5b5b", label="Rule: operating cost (solid)"), Patch(facecolor="#c9c9c9", edgecolor="#5b5b5b", label="LP: operating cost (lighter)"),
               Patch(facecolor="white", edgecolor="#5b5b5b", hatch="////", label="Shortfall cost (hatched)"),
               Line2D([0], [0], color="black", lw=1.6, label=f"Hindsight cost ({hindsight:.2f})")]
    ax.legend(handles=handles, loc="upper right", frameon=False, ncol=2, fontsize=9)
    ax.set_title(f"Realised cost by plan ({label})", loc="left", fontweight="semibold")
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.14)
    save(fig, f"{stem}_cost_bars", plt)


def cost_heatmap(key, plt):
    from matplotlib.colors import Normalize
    stem, label = ORIGINS[key]
    plans, totals = read(DATA / key / "plans.csv"), read(DATA / key / "totals.csv").set_index("plan")
    rows = [f"{name} {dec}" for name in METHODS for dec in ("Rule", "LP")] + ["Hindsight"]
    months = sorted(plans.month.unique())
    cell = {(r.plan, r.month): r for r in plans.itertuples()}
    grid = np.array([[cell[(p, mo)].monthly_cost / 1e6 for mo in months] for p in rows])
    tot = np.array([totals.loc[p, "total"] / 1e6 for p in rows])
    assert np.allclose(grid.sum(axis=1), tot, rtol=1e-9)
    cmap = plt.get_cmap("YlOrRd")
    norm_m, norm_t = Normalize(grid.min(), grid.max()), Normalize(tot.min(), tot.max())
    fig, ax = plt.subplots(figsize=(11, 7.6))
    ncol = len(months) + 1
    for i, p in enumerate(rows):
        for j in range(ncol):
            if j < len(months):
                g = cell[(p, months[j])]
                v, rgba = grid[i, j], cmap(norm_m(grid[i, j]))
                sub = f"short {g.shortfall:,.0f}" if g.shortfall > 0.5 else f"spare {g.spare:,.0f}" if g.spare > 0.5 else ""
            else:
                v, rgba, sub = tot[i], cmap(norm_t(tot[i])), ""
            xj = j + (0.25 if j == len(months) else 0)
            ax.add_patch(plt.Rectangle((xj, i), 1, 1, facecolor=rgba, edgecolor="white", lw=1.5))
            ink = "white" if 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2] < 0.5 else "black"
            ax.text(xj + 0.5, i + (0.4 if sub else 0.5), f"{v:.2f}", ha="center", va="center", fontsize=10, color=ink,
                    fontweight="semibold" if j == len(months) else "normal")
            if sub:
                ax.text(xj + 0.5, i + 0.72, sub, ha="center", va="center", fontsize=7.5, color=ink)
    for k in range(2, len(rows), 2):
        ax.plot([0, ncol + 0.25], [k, k], color="black", lw=1.2 if k == len(rows) - 1 else 0.6)
    ax.set_xlim(0, ncol + 0.25)
    ax.set_ylim(len(rows), 0)
    ax.set_xticks([j + 0.5 for j in range(len(months))] + [len(months) + 0.75], [f"{pd.Timestamp(mo):%b %Y}" for mo in months] + ["Total"])
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.5 for i in range(len(rows))], rows)
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm_m, cmap=cmap), ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("Monthly realised cost (million model units)")
    cb.outline.set_visible(False)
    ax.set_title(f"What each plan cost, month by month ({label})", loc="left", fontsize=13, fontweight="semibold", pad=30)
    fig.text(0.99, 0.012, "'short' / 'spare': beds below / above the actual gap. The Total column uses its own scale of the same colours.",
             ha="right", fontsize=7.5, color="#555555")
    fig.tight_layout(rect=(0, 0.035, 1, 1))
    save(fig, f"{stem}_cost_heatmap", plt)


def excess_bars(plt):
    from matplotlib.patches import Patch
    combined = read(DATA / "combined.csv").set_index("plan")
    fig, ax = plt.subplots(figsize=(12.5, 5.6))
    positions = [(name, dec, i + off) for i, name in enumerate(METHODS) for dec, off in (("Rule", -0.21), ("LP", 0.21))]
    top = max(combined[f"{key}_excess"].max() for key in ORIGINS) / 1e6
    for name, dec, x in positions:
        r = combined.loc[f"{name} {dec}"]
        for key, off, kw in (("O1_sep2025", -0.095, dict(color=COLOURS[name])),
                             ("O2_mar2026", 0.095, dict(color="white", hatch="////"))):
            v = r[f"{key}_excess"] / 1e6
            ax.bar(x + off, v, 0.18, edgecolor=COLOURS[name], lw=0.8, zorder=2, **kw)
            ax.text(x + off, v + 0.012 * top, f"{v:.1f}", ha="center", va="bottom", fontsize=7.5)
    bar_axis(ax, positions)
    ax.set_ylabel("Excess over hindsight (million model units)")
    ax.set_ylim(0, top * 1.15)
    style(ax)
    ax.legend(handles=[Patch(facecolor="#5b5b5b", label="Origin 1: September 2025 (solid)"),
                       Patch(facecolor="white", edgecolor="#5b5b5b", hatch="////", label="Origin 2: March 2026 (hatched)")],
              loc="upper right", frameon=False, fontsize=9)
    ax.set_title("Excess over hindsight by plan, both origins", loc="left", fontweight="semibold")
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.14)
    save(fig, "both_excess_over_hindsight", plt)


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "clean-checkpoint", "hatch.linewidth": 0.6})
    for key in ORIGINS:
        for series, col, ylabel in SERIES:
            forecast_figure(key, series, col, ylabel, plt)
        cost_bars(key, plt)
        cost_heatmap(key, plt)
    excess_bars(plt)
    print("wrote", len(list(FIG.glob("*.png"))), "PNG and", len(list(FIG.glob("*.svg"))), "SVG files to", FIG)


if __name__ == "__main__":
    main()
