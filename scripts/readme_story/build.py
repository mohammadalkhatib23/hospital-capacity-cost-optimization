"""Plot mean realised excess from the frozen, comparable monthly plans."""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "outputs/forecast_combination"
COLOURS = {
    "Seasonal naive": "#E69F00",
    "Combo": "#000000",
    "ExtraTrees-24": "#009E73",
    "SARIMA-24": "#0072B2",
    "Mean-24": "#CC79A7",
    "Persistence": "#D55E00",
}


def comparable_means():
    config = json.loads((DATA / "config.json").read_text())
    origins = config["populations"]["P3"]
    expected = pd.period_range("2025-04", "2026-04", freq="M").astype(str)
    assert sorted(origins) == sorted(set(expected) - {"2025-10", "2026-01"})
    scores = pd.read_csv(DATA / "costs.csv")
    scores = scores[scores.origin.isin(origins) & scores.method.isin(COLOURS)
                    & scores.plan.isin(["LP", "Protected"]) & scores.penalty.eq(config["costs"]["shortage"])]
    matrix = scores.pivot(index="origin", columns=["method", "plan"], values="excess")
    assert matrix.shape == (11, 12) and matrix.notna().all().all()
    hindsight = scores.groupby("origin").hindsight
    assert hindsight.nunique().eq(1).all()
    band = (0.01 * hindsight.first()).mean() / 1e6
    return matrix.mean().unstack().loc[list(COLOURS)] / 1e6, band


def main():
    means, band = comparable_means()
    low = means.Protected.min()
    plt.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "approach-lp-protected"})
    fig, ax = plt.subplots(figsize=(10, 5.6), layout="constrained")
    x = np.arange(len(means))
    ax.axhspan(low, low + band, color="#a8acb4", alpha=.65, zorder=1)
    for pos, (method, row) in enumerate(means.iterrows()):
        for shift, plan in [(-.19, "LP"), (.19, "Protected")]:
            bar = ax.bar(pos + shift, row[plan], width=.36, color=COLOURS[method],
                         edgecolor="black", linewidth=.8, zorder=2)
            if plan == "Protected":
                ink = "white" if method in ("Combo", "SARIMA-24") else "black"
                ax.bar(pos + shift, row[plan], width=.36, facecolor="none",
                       edgecolor=ink, linewidth=0, hatch="////", zorder=3)
            ax.bar_label(bar, fmt="%.2f", padding=4, fontsize=10)
    ax.set_xticks(x, ["Average of five" if m == "Combo" else m for m in means.index])
    ax.set_ylabel("Mean cost above Hindsight per six-month plan (million model units)")
    ax.set_title("Cost of LP and Protected plans across 11 comparable origins", loc="left", pad=42)
    ax.legend(handles=[Patch(facecolor="#777777", label="LP"),
                       Patch(facecolor="white", edgecolor="black", hatch="////", label="Protected"),
                       Patch(facecolor="#c6c8ce", label=f"Tie band above lowest Protected mean ({band:.3f}m)")],
              loc="lower left", bbox_to_anchor=(0, 1), ncol=3, frameon=False)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#dfdfdf", linewidth=.6)
    ax.spines[["right", "top"]].set_visible(False)
    ax.margins(y=.10)
    out = ROOT / "figures/readme_story"
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(out / f"approach_lp_vs_protected.{ext}", dpi=300,
                    metadata={"Date": None} if ext == "svg" else None)
    plt.close(fig)
    print(means.round(4).to_string(), f"\nMean tie band: {band:.9f}m")


if __name__ == "__main__":
    main()
