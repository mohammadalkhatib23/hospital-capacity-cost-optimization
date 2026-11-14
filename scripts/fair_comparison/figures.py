"""Figures for the fair-comparison README section. Reads only outputs/fair_comparison/; nothing is refitted or re-solved.

One figure per origin: realised cost of the Point plan (no protection) and the Protected plan (SAA) for each method,
operating cost solid, shortfall cost hatched, hindsight as a line. Writes 300 dpi PNG and SVG files to
figures/fair_comparison/.
"""

from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "outputs/fair_comparison"
FIG = REPO / "figures/fair_comparison"
ORIGINS = {"O1_sep2025": ("o1", "Origin 1, September 2025"), "O2_mar2026": ("o2", "Origin 2, March 2026")}
METHODS = ("Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24")
COLOURS = {"Seasonal naive": "#E69F00", "SARIMA-24": "#0072B2", "ExtraTrees-24": "#009E73", "Mean-24": "#CC79A7", "Persistence": "#D55E00"}
BASE = 5000.0


def tint(colour, share=0.55):
    rgb = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)]) / 255
    return tuple(rgb + (1 - rgb) * share)


def point_vs_protected(key, plt):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    stem, label = ORIGINS[key]
    totals = pd.read_csv(DATA / key / "totals.csv", float_precision="round_trip")
    base = totals[np.isclose(totals.scored_at, BASE)]
    pick = lambda method, kind: base[(base.method == method) & (base.kind == kind)].iloc[0]
    hindsight = pick("Hindsight", "hindsight").total / 1e6
    fig, ax = plt.subplots(figsize=(11, 5.6))
    positions = [(name, kind, i + off) for i, name in enumerate(METHODS) for kind, off in (("point", -0.2), ("protected", 0.2))]
    top = max(pick(name, kind).total for name, kind, _ in positions) / 1e6
    for name, kind, x in positions:
        r = pick(name, kind)
        face = COLOURS[name] if kind == "point" else tint(COLOURS[name])
        ax.bar(x, r.operating / 1e6, 0.38, color=face, edgecolor=COLOURS[name], lw=0.8, zorder=2)
        if r.shortfall_cost > 0:
            ax.bar(x, r.shortfall_cost / 1e6, 0.38, bottom=r.operating / 1e6, color="white", edgecolor=COLOURS[name], hatch="////", lw=0.8, zorder=2)
        ax.text(x, r.total / 1e6 + 0.012 * top, f"{r.total / 1e6:.2f}", ha="center", va="bottom", fontsize=8.5)
    ax.axhline(hindsight, color="black", lw=1.6, zorder=3)
    ax.set_xticks([x for _, _, x in positions], ["Point" if kind == "point" else "Protected" for _, kind, _ in positions], fontsize=8.5)
    ax.tick_params(axis="x", length=0)
    for i, name in enumerate(METHODS):
        ax.text(i, -0.085, name, transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=10, fontweight="semibold")
    ax.set_ylabel("Realised cost (million model units)")
    ax.set_ylim(0, top * 1.2)
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    handles = [Patch(facecolor="#5b5b5b", label="Point plan: operating cost (solid)"), Patch(facecolor="#c9c9c9", edgecolor="#5b5b5b", label="Protected plan: operating cost (lighter)"),
               Patch(facecolor="white", edgecolor="#5b5b5b", hatch="////", label="Shortfall cost (hatched)"),
               Line2D([0], [0], color="black", lw=1.6, label=f"Hindsight cost ({hindsight:.2f})")]
    ax.legend(handles=handles, loc="upper right", frameon=False, ncol=2, fontsize=9)
    ax.set_title(f"Point plan vs Protected plan, by forecast ({label})", loc="left", fontweight="semibold")
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.14)
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / f"{stem}_point_vs_protected.png", dpi=300, facecolor="white")
    fig.savefig(FIG / f"{stem}_point_vs_protected.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "fair-comparison", "hatch.linewidth": 0.6})
    for key in ORIGINS:
        point_vs_protected(key, plt)
    print("wrote", len(list(FIG.glob("*.png"))), "PNG and", len(list(FIG.glob("*.svg"))), "SVG files to", FIG)


if __name__ == "__main__":
    main()
