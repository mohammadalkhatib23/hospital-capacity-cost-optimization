"""Single-series forecast figures for the September 2025 origin: Seasonal naive and SARIMA-24 against the actual values.

Reads only outputs/sep2025_comparison/forecasts.csv and actuals.csv (written by scripts/plot_sep2025_monthly_views.py);
nothing is refitted or re-solved. Prints the month counts (which of the two forecasts was closer each month), the MAE of
every 24-month method per series, and the ExtraTrees-24 gap-error sign count used in the README. Writes
figures/sep2025_forecast_{occupied,available,gap}.{png,svg}.
"""

from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "outputs/sep2025_comparison"
SHOWN = {"Seasonal naive": dict(colour="#E69F00", marker="D", tag="SN"), "SARIMA-24": dict(colour="#0072B2", marker="s", tag="S24")}
METHODS_24 = ("Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24")
SERIES = (("occupied", "forecast_occupied", "Occupied beds", "Occupied beds: forecast vs actual (Sep 2025 origin)"),
          ("available", "forecast_available", "Available beds", "Available beds: forecast vs actual (Sep 2025 origin)"),
          ("gap", "forecast_gap", "Planning gap (beds)", "Planning gap: forecast vs actual (Sep 2025 origin)"))


def load():
    fc = pd.read_csv(DATA / "forecasts.csv", parse_dates=["month"], float_precision="round_trip")
    act = pd.read_csv(DATA / "actuals.csv", parse_dates=["month"], float_precision="round_trip").sort_values("month").reset_index(drop=True)
    paths = {name: g.sort_values("month").reset_index(drop=True) for name, g in fc.groupby("source")}
    for g in paths.values():
        assert g.month.tolist() == act.month.tolist()
    return paths, act


def summary(paths, act):
    out = {}
    for key, col, _, _ in SERIES:
        y = act[key].to_numpy()
        err = {name: np.abs(y - paths[name][col].to_numpy()) for name in METHODS_24}
        sn_closer = err["Seasonal naive"] < err["SARIMA-24"]
        mae = {name: float(e.mean()) for name, e in err.items()}
        best = min(METHODS_24, key=mae.get)
        out[key] = dict(sn_closer=sn_closer, mae=mae, best=best,
                        sn_months=[f"{m:%b}" for m, c in zip(act.month, sn_closer) if c],
                        s24_months=[f"{m:%b}" for m, c in zip(act.month, sn_closer) if not c])
    et = paths["ExtraTrees-24"]
    over_a = et.forecast_available.to_numpy() - act.available.to_numpy()
    over_o = et.forecast_occupied.to_numpy() - act.occupied.to_numpy()
    out["et_gap_positive"] = int(((act.gap.to_numpy() - et.forecast_gap.to_numpy()) > 0).sum())
    out["et_available_exceeds"] = int((over_a > over_o / 0.90).sum())
    for name in ("Seasonal naive", "SARIMA-24"):
        g = paths[name]
        out[f"{name} over"] = dict(occupied=(g.forecast_occupied - act.occupied).to_numpy(), available=(g.forecast_available - act.available).to_numpy(),
                                   gap_error=(act.gap - g.forecast_gap).to_numpy())
    return out


def figure(key, col, ylabel, title, paths, act, closer, plt):
    from matplotlib.transforms import blended_transform_factory
    fig, ax = plt.subplots(figsize=(10, 4.5))
    months = act.month
    for name, st in SHOWN.items():
        ax.plot(months, paths[name][col], color=st["colour"], marker=st["marker"], ms=6, lw=1.8, label=name, zorder=3)
    ax.plot(months, act[key], color="black", lw=3, marker="o", ms=7, label="Actual", zorder=4)
    values = np.concatenate([act[key].to_numpy()] + [paths[name][col].to_numpy() for name in SHOWN])
    span = values.max() - values.min()
    ax.set_ylim(values.min() - 0.12 * span, values.max() + 0.18 * span)
    for i, (m, v) in enumerate(zip(months, act[key])):
        above = np.mean([paths[name][col].iloc[i] for name in SHOWN]) < v  # label on the side away from the forecasts
        ax.annotate(f"{v:,.0f}", (m, v), xytext=(0, 9 if above else -10), textcoords="offset points", ha="center",
                    va="bottom" if above else "top", fontsize=8, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85), zorder=5)
    ax.set_xticks(months, [f"{m:%b %Y}" for m in months])
    trans = blended_transform_factory(ax.transData, ax.transAxes)
    for m, sn in zip(months, closer):
        st = SHOWN["Seasonal naive" if sn else "SARIMA-24"]
        ax.text(m, -0.115, st["tag"], transform=trans, ha="center", va="top", fontsize=8.5, fontweight="semibold", color=st["colour"])
    ax.text(-0.01, -0.115, "Closer:", transform=ax.transAxes, ha="right", va="top", fontsize=8, color="#555555")
    ax.set_xlabel("Month", labelpad=20)
    ax.set_ylabel(ylabel)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.set_title(title, loc="left", fontweight="semibold")
    fig.tight_layout()
    stem = f"sep2025_forecast_{key}"
    fig.savefig(REPO / "figures" / f"{stem}.png", dpi=300, facecolor="white")
    fig.savefig(REPO / "figures" / f"{stem}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def main():
    paths, act = load()
    s = summary(paths, act)
    # Statements made in the README.
    assert int(s["occupied"]["sn_closer"].sum()) == 6 and int(s["available"]["sn_closer"].sum()) == 0
    assert s["gap"]["s24_months"] == ["Jan"]
    assert (s["occupied"]["best"], s["available"]["best"], s["gap"]["best"]) == ("ExtraTrees-24", "ExtraTrees-24", "Seasonal naive")
    assert s["et_gap_positive"] >= 4
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "sep2025-singles"})
    for key, col, ylabel, title in SERIES:
        figure(key, col, ylabel, title, paths, act, s[key]["sn_closer"], plt)
        r = s[key]
        print(f"{key}: SN closer {int(r['sn_closer'].sum())}/6 {r['sn_months']}; S24 closer {r['s24_months']}; best {r['best']} "
              + ", ".join(f"{k} {v:,.1f}" for k, v in r["mae"].items()))
    print("ExtraTrees-24 gap error > 0 in", s["et_gap_positive"], "of 6; available overestimate > occupied overestimate / 0.90 in", s["et_available_exceeds"], "of 6")
    for name in ("Seasonal naive", "SARIMA-24"):
        o = s[f"{name} over"]
        print(name, "occupied over", np.round(o["occupied"]), "available over", np.round(o["available"]), "gap error", np.round(o["gap_error"]))


if __name__ == "__main__":
    main()
