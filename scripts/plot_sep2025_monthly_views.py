"""Monthly views of the September 2025 forecast vs decision method comparison.

Run with --project-root pointing to the full NHS workspace. Imports scripts/compare_methods_sep2025.py and recomputes
every forecast and plan through its existing functions (unchanged). Before anything is written, recomputed totals are
checked against the README table (0.01m), the v6-saved plans (1e-6 relative) and the earlier Extra Trees forecasts
(1e-6 beds). Writes outputs/sep2025_comparison/{forecasts,actuals,plans,totals}.csv at full precision and builds
figures/sep2025_forecasts_three_panel.{png,svg} and figures/sep2025_monthly_cost_heatmap.{png,svg} from those CSVs.
"""

import argparse
import importlib.util
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
FR = "outputs/formulation_robustness"
MODELS = ("Seasonal naive", "SARIMA-24", "ExtraTrees-24", "Mean-24", "Persistence", "SARIMA-full")
COLOURS = {"Seasonal naive": "#E69F00", "SARIMA-24": "#0072B2", "ExtraTrees-24": "#009E73", "Mean-24": "#CC79A7",
           "Persistence": "#D55E00", "SARIMA-full": "#56B4E9", "Hindsight": "#000000"}
MARKERS = {"Seasonal naive": "D", "SARIMA-24": "o", "ExtraTrees-24": "v", "Mean-24": "s", "Persistence": "^", "SARIMA-full": "x"}
SHORT = {"Seasonal naive": "SN", "SARIMA-24": "SARIMA-24", "ExtraTrees-24": "ET-24", "Mean-24": "Mean-24",
         "Persistence": "Persistence", "SARIMA-full": "SARIMA-full"}
HEAT_ROWS = ("Seasonal naive LP", "SARIMA-24 LP", "ExtraTrees-24 LP", "Mean-24 LP", "Persistence LP", "SARIMA-full LP", "Hindsight")
# Extra Trees forecasts printed by the earlier run (compare_methods_sep2025.py, commit e25eade).
ET_PREVIOUS = {"g_and_a_beds_available": [101316.24811111, 101778.03444444, 102157.765, 103190.78111111, 103182.65944444, 102921.585],
               "g_and_a_beds_occupied": [94391.87222222, 94657.80444444, 94710.09166667, 96561.77444444, 96634.855, 95969.54055556]}


def load_compare():
    spec = importlib.util.spec_from_file_location("_compare_methods_sep2025", REPO / "scripts/compare_methods_sep2025.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def readme_totals():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    section = text[text.index("## Forecast vs decision method: September 2025 origin"):]
    out = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 9 and re.fullmatch(r"-?\d+\.\d\d", cells[7]):
            name = "Hindsight" if cells[0] == "Hindsight" else f"{cells[0]} {cells[2]}"
            out[name] = dict(total=float(cells[7]), mae=None if cells[3] == "-" else int(cells[3].replace(",", "")))
    assert len(out) == 12, out
    return out


def recompute(root, out_dir):
    m = load_compare()
    helpers = m.module(root, "formulation_robustness_helpers")
    fe = m.module(root, "forecasting_evaluation")
    data = helpers.v5b_load("2026-09-01")
    pre = data.loc[:m.ORIGIN].copy()
    w = pre.iloc[-24:]
    A, O, dates = m.A, m.O, m.DATES
    fc = {}
    for t in (A, O):
        prior_year = dates - pd.DateOffset(years=1)
        fc[("Mean-24", t)] = np.repeat(w[t].mean(), 6)
        fc[("Persistence", t)] = np.repeat(w[t].iloc[-1], 6)
        fc[("Seasonal naive", t)] = w.loc[prior_year, t].to_numpy(float)
        fc[("SARIMA-24", t)], _ = m.sarima_24(w, t)
        fc[("ExtraTrees-24", t)], _ = m.extra_trees_24(fe, w, t)
        np.testing.assert_allclose(fc[("ExtraTrees-24", t)], ET_PREVIOUS[t], rtol=0, atol=1e-6)
    saved = pd.read_csv(root / f"{FR}/tables/forecast_inputs_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    saved = saved[saved.origin.eq(m.ORIGIN)].sort_values("horizon")
    fc[("SARIMA-full", A)], fc[("SARIMA-full", O)] = saved.available_prediction.to_numpy(float), saved.occupied_prediction.to_numpy(float)
    gaps, _ = m.forecasts(root, pre)  # the script's own gap path for every source
    for name in MODELS:
        np.testing.assert_allclose(fc[(name, O)] / m.TAU - fc[(name, A)], gaps[name], rtol=0, atol=1e-6)
    actual = pd.DataFrame(dict(month=dates, occupied=data.loc[dates, O].to_numpy(float), available=data.loc[dates, A].to_numpy(float)))
    actual["gap"] = actual.occupied / m.TAU - actual.available
    d = actual.gap.to_numpy()

    plans = []
    for name in m.SOURCES:
        plans += [(f"{name} Rule", m.rule(gaps[name])), (f"{name} LP", m.solve(gaps[name]))]
    plans += [("SARIMA-full LP", m.solve(gaps["SARIMA-full"])), ("Hindsight", m.solve(d))]
    plan_rows, total_rows = [], []
    for plan, x in plans:
        a, r = m.flows(x)
        short, spare = np.maximum(d - x, 0), np.maximum(x - d, 0)
        cost = m.C * x + m.A_PLUS * a + m.A_MINUS * r + m.P * short
        plan_rows += [dict(plan=plan, month=dates[i], x=x[i], a=a[i], r=r[i], shortfall=short[i], spare=spare[i], monthly_cost=cost[i]) for i in range(6)]
        total_rows.append(dict(plan=plan, running=m.C * x.sum(), opening=m.A_PLUS * a.sum(), closing=m.A_MINUS * r.sum(),
                               shortfall_cost=m.P * short.sum(), total=m.score(x, d)["total"]))
    plans_df, totals = pd.DataFrame(plan_rows), pd.DataFrame(total_rows)

    readme = readme_totals()
    for r in totals.itertuples():
        assert abs(round(r.total / 1e6, 2) - readme[r.plan]["total"]) < 0.01 + 1e-9, (r.plan, r.total, readme[r.plan])
        assert abs(plans_df[plans_df.plan.eq(r.plan)].monthly_cost.sum() - r.total) <= 1e-6 * r.total
    saved_plans = pd.read_csv(root / f"{FR}/plans_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    saved_hind = pd.read_csv(root / f"{FR}/tables/hindsight_plans_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    saved_scores = pd.read_csv(root / f"{FR}/tables/scores_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    e0 = lambda f, policy: f[f.origin.eq(m.ORIGIN) & f.environment.eq("E0") & f.policy.eq(policy)].iloc[0]
    worst = 0.0
    for plan, policy, plan_file in (("SARIMA-full LP", "SARIMA point", saved_plans), ("Seasonal naive LP", "Seasonal naive", saved_plans),
                                    ("Persistence LP", "Persistence", saved_plans), ("Hindsight", "Hindsight", saved_hind)):
        x = plans_df[plans_df.plan.eq(plan)].x.to_numpy()
        ref = e0(plan_file, policy)[[f"x{k}" for k in range(1, 7)]].to_numpy(float)
        cost_ref = float(e0(saved_scores, policy).cost)
        total = float(totals.loc[totals.plan.eq(plan), "total"].iloc[0])
        worst = max(worst, float(np.max(np.abs(x - ref) / np.abs(ref))), abs(total - cost_ref) / cost_ref)
    assert worst <= 1e-6, worst

    forecasts = pd.DataFrame([dict(source=name, month=dates[i], forecast_occupied=fc[(name, O)][i], forecast_available=fc[(name, A)][i],
                                   forecast_gap=gaps[name][i]) for name in MODELS for i in range(6)])
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("forecasts", forecasts), ("actuals", actual), ("plans", plans_df), ("totals", totals)):
        frame.to_csv(out_dir / f"{name}.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    return readme, worst


def figure_forecasts(out_dir, fig_dir, readme, plt):
    fc = pd.read_csv(out_dir / "forecasts.csv", parse_dates=["month"], float_precision="round_trip")
    act = pd.read_csv(out_dir / "actuals.csv", parse_dates=["month"], float_precision="round_trip")
    panels = (("forecast_occupied", "occupied", "Occupied beds", "Occupied beds"),
              ("forecast_available", "available", "Available beds", "Available beds"),
              ("forecast_gap", "gap", "Planning gap (O/0.90 - A)", "Planning gap (beds)"))
    fig, axes = plt.subplots(3, 1, figsize=(10, 11.5), sharex=True)
    for ax, (col, acol, title, ylabel) in zip(axes, panels):
        maes, values = [], [act[acol].to_numpy()]
        for name in MODELS:
            g = fc[fc.source.eq(name)].sort_values("month")
            ref = name == "SARIMA-full"
            ax.plot(g.month, g[col], color=COLOURS[name], ls="--" if ref else "-", lw=1.3 if ref else 1.6, marker=MARKERS[name], ms=5,
                    label="SARIMA-full (reference)" if ref else name, zorder=3)
            mae = float(np.abs(act[acol].to_numpy() - g[col].to_numpy()).mean())
            if col == "forecast_gap":
                key = f"{name} LP"
                assert round(mae) == readme[key]["mae"] or f"{mae:,.0f}" == f"{readme[key]['mae']:,}", (name, mae, readme[key])
            maes.append(f"{SHORT[name]} {mae:,.0f}")
            values.append(g[col].to_numpy())
        ax.plot(act.month, act[acol], color="black", lw=3, marker="o", ms=6, label="Actual", zorder=4)
        allv = np.concatenate(values)
        pad = (allv.max() - allv.min()) * 0.08
        ax.set_ylim(allv.min() - pad, allv.max() + pad)
        ax.set_title(title, loc="left", fontsize=11, fontweight="semibold", pad=16)
        ax.text(1.0, 1.01, "MAE: " + " | ".join(maes), transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color="#444444")
        ax.set_ylabel(ylabel)
        ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
        ax.set_axisbelow(True)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    months = act.month.tolist()
    axes[-1].set_xticks(months, [f"{d:%b %Y}" for d in months])
    handles, labels = axes[0].get_legend_handles_labels()
    fig.suptitle("September 2025 origin: monthly forecasts vs actual (24-month training)", x=0.02, ha="left", fontsize=13, fontweight="semibold", y=0.995)
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.97), ncol=7, frameon=False, fontsize=8.5, handlelength=2.2)
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    save(fig, fig_dir, "sep2025_forecasts_three_panel", plt)


def figure_heatmap(out_dir, fig_dir, plt):
    from matplotlib.colors import Normalize
    plans = pd.read_csv(out_dir / "plans.csv", parse_dates=["month"], float_precision="round_trip")
    totals = pd.read_csv(out_dir / "totals.csv", float_precision="round_trip").set_index("plan")
    months = sorted(plans.month.unique())
    grid = np.array([[plans[(plans.plan == p) & (plans.month == mo)].monthly_cost.iloc[0] / 1e6 for mo in months] for p in HEAT_ROWS])
    tot = np.array([totals.loc[p, "total"] / 1e6 for p in HEAT_ROWS])
    for p, t, row in zip(HEAT_ROWS, tot, grid):
        assert abs(row.sum() - t) <= 1e-9 * t, p
    cmap = plt.get_cmap("YlOrRd")
    norm_m, norm_t = Normalize(grid.min(), grid.max()), Normalize(tot.min(), tot.max())
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ncol = len(months) + 1
    for i, p in enumerate(HEAT_ROWS):
        for j in range(ncol):
            if j < len(months):
                v, rgba = grid[i, j], cmap(norm_m(grid[i, j]))
                g = plans[(plans.plan == p) & (plans.month == months[j])].iloc[0]
                sub = f"short {g.shortfall:,.0f} beds" if g.shortfall > 0.5 else f"spare {g.spare:,.0f} beds" if g.spare > 0.5 else ""
            else:
                v, rgba, sub = tot[i], cmap(norm_t(tot[i])), ""
            xj = j + (0.25 if j == len(months) else 0)
            ax.add_patch(plt.Rectangle((xj, i), 1, 1, facecolor=rgba, edgecolor="white", lw=1.5))
            lum = 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]
            ink = "white" if lum < 0.5 else "black"
            ax.text(xj + 0.5, i + (0.42 if sub else 0.5), f"{v:.2f}", ha="center", va="center", fontsize=10, color=ink,
                    fontweight="semibold" if j == len(months) else "normal")
            if sub:
                ax.text(xj + 0.5, i + 0.72, sub, ha="center", va="center", fontsize=7, color=ink)
    for k in (5, 6):
        ax.plot([0, ncol + 0.25], [k, k], color="black", lw=1.2)
    ax.set_xlim(0, ncol + 0.25)
    ax.set_ylim(len(HEAT_ROWS), 0)
    ax.set_xticks([j + 0.5 for j in range(len(months))] + [len(months) + 0.75], [f"{pd.Timestamp(mo):%b %Y}" for mo in months] + ["Total"])
    ax.xaxis.tick_top()
    labels = [p if p != "SARIMA-full LP" else "SARIMA-full LP (reference)" for p in HEAT_ROWS]
    ax.set_yticks([i + 0.5 for i in range(len(HEAT_ROWS))], labels)
    for tick, p in zip(ax.get_yticklabels(), HEAT_ROWS):
        tick.set_color(COLOURS[p.replace(" LP", "")])
        tick.set_fontweight("semibold")
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    sm = plt.cm.ScalarMappable(norm=norm_m, cmap=cmap)
    cb = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("Monthly realised cost (million model units)")
    cb.outline.set_visible(False)
    ax.set_title("September 2025 origin: what each plan cost, month by month", loc="left", fontsize=13, fontweight="semibold", pad=30)
    fig.text(0.99, 0.01, "Total column uses its own scale of the same colours.", ha="right", fontsize=7.5, color="#555555")
    fig.tight_layout()
    save(fig, fig_dir, "sep2025_monthly_cost_heatmap", plt)


def save(fig, fig_dir, stem, plt):
    fig.savefig(fig_dir / f"{stem}.png", dpi=300, facecolor="white")
    fig.savefig(fig_dir / f"{stem}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    out_dir, fig_dir = REPO / "outputs/sep2025_comparison", REPO / "figures"
    readme, worst = recompute(args.project_root.resolve(), out_dir)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "sep2025-monthly"})
    figure_forecasts(out_dir, fig_dir, readme, plt)
    figure_heatmap(out_dir, fig_dir, plt)
    print(f"all checks passed; worst v6 relative difference {worst:.3g}")


if __name__ == "__main__":
    main()
