"""SARIMA-36 vs Seasonal naive across all 29 origins (12 selection origins and 17 earlier test origins).

Reads only saved CSVs: outputs/twelve_origin_forecast_test/{totals,accuracy}.csv and
outputs/backward_sarima36_test/{totals,accuracy}.csv. Nothing is refitted or re-solved. Applies the pre-registered tie
rules (cost: 1% of that origin's hindsight cost; gap MAE: 5 beds). Prints the combined table and per-origin figures used
in the README, and writes figures/all_origins_sarima36_minus_sn.{png,svg}.
"""

from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SOURCES = {"selection": REPO / "outputs/twelve_origin_forecast_test", "test": REPO / "outputs/backward_sarima36_test"}


def classify(advantage, band):
    return "win" if advantage > band else "loss" if advantage < -band else "tie"


def per_origin():
    frames = []
    for group, path in SOURCES.items():
        tot = pd.read_csv(path / "totals.csv", parse_dates=["origin"], float_precision="round_trip")
        acc = pd.read_csv(path / "accuracy.csv", parse_dates=["origin"], float_precision="round_trip")
        cost = tot.pivot(index="origin", columns="plan", values="total")
        gap = acc.pivot(index="origin", columns="model", values="gap_mae")
        f = pd.DataFrame(dict(sn_cost=cost["Seasonal naive LP"], s36_cost=cost["SARIMA-36 LP"], hindsight=cost["Hindsight"],
                              sn_gap=gap["Seasonal naive"], s36_gap=gap["SARIMA-36"]))
        f["group"] = group
        frames.append(f)
    f = pd.concat(frames).sort_index()
    assert len(f) == 29 and f.index.is_unique and not f.isna().any().any()
    f["diff"] = f.s36_cost - f.sn_cost
    f["cost_result"] = [classify(-d, 0.01 * h) for d, h in zip(f["diff"], f.hindsight)]
    f["gap_result"] = [classify(a - b, 5.0) for a, b in zip(f.sn_gap, f.s36_gap)]
    return f


def combined(f):
    rows = []
    for label, g in (("12 origins (Apr 2025 - Mar 2026, selection)", f[f.group == "selection"]),
                     ("17 earlier origins (Nov 2023 - Mar 2025, test)", f[f.group == "test"]), ("All 29", f)):
        wtl = lambda col: " / ".join(str(int((g[col] == k).sum())) for k in ("win", "tie", "loss"))
        rows.append(dict(population=label, n=len(g), cost=wtl("cost_result"), gap=wtl("gap_result"), total=g["diff"].sum() / 1e6, mean=g["diff"].mean() / 1e6))
    return pd.DataFrame(rows)


def figure(f):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "all-origins-s36", "hatch.linewidth": 0.6})
    x = np.arange(len(f))
    v = f["diff"].to_numpy() / 1e6
    fig, ax = plt.subplots(figsize=(13, 5))
    sel = np.where(f.group.to_numpy() == "selection")[0]
    ax.axvspan(sel.min() - 0.5, sel.max() + 0.5, color="#eeeeee", zorder=0)
    ax.text((sel.min() + sel.max()) / 2, 1.0, "selection origins", transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=9, color="#555555")
    neg = v < 0
    ax.bar(x[neg], v[neg], 0.72, color="#0072B2", zorder=2)
    ax.bar(x[~neg], v[~neg], 0.72, color="#E69F00", edgecolor="#8a5a00", hatch="////", lw=0.6, zorder=2)
    ax.axhline(0, color="black", lw=1)
    ax.set_xticks(x, [f"{o:%b}\n{o:%y}" for o in f.index], fontsize=8)
    ax.set_xlim(-0.6, len(f) - 0.4)
    ax.set_xlabel("Forecast origin")
    ax.set_ylabel("SARIMA-36 cost - Seasonal naive cost\n(million model units)")
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(handles=[Patch(color="#0072B2", label="SARIMA-36 cheaper"),
                       Patch(facecolor="#E69F00", edgecolor="#8a5a00", hatch="////", label="Seasonal naive cheaper")],
              loc="upper right", frameon=False, fontsize=9)
    ax.set_title("SARIMA-36 minus Seasonal naive LP cost, all 29 origins", loc="left", fontweight="semibold", pad=16)
    fig.tight_layout()
    fig.savefig(REPO / "figures/all_origins_sarima36_minus_sn.png", dpi=300, facecolor="white")
    fig.savefig(REPO / "figures/all_origins_sarima36_minus_sn.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def main():
    f = per_origin()
    figure(f)
    print(combined(f).to_string())
    sel = f[f.group == "selection"].sort_values("diff")
    print((sel[["diff", "cost_result"]].assign(diff=sel["diff"] / 1e6)).round(3).to_string())


if __name__ == "__main__":
    main()
