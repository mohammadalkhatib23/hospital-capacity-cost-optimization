"""Training-window comparison at the September 2025 origin: SARIMA and ExtraTrees on 24 months, 36 months and full history.

Run with --project-root pointing to the full NHS workspace. Imports scripts/compare_methods_sep2025.py (forecast and LP
functions) and scripts/plot_sep2025_monthly_views.py (module loader), both unchanged. Specifications are those of the
Sep 2025 comparison; only the training window changes. Every window ends at the Sep 2025 cut-off and no fit or feature
uses data outside it. The W24 results are first checked against the saved Sep 2025 outputs. Writes
outputs/sep2025_window_comparison/{forecasts,plans,monthly_costs,totals,accuracy}.csv and
figures/sep2025_windows_{gap_mae,bed_mae,total_cost,gap_forecasts,monthly_cost_heatmap}.{png,svg}, and prints the
README table.
"""

import argparse
import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs/sep2025_window_comparison"
SAVED = REPO / "outputs/sep2025_comparison"
WINDOWS = (("24", 24), ("36", 36), ("full", None))
COLOURS = {"Seasonal naive": "#E69F00", "SARIMA": "#0072B2", "ExtraTrees": "#009E73", "Hindsight": "#000000"}
STYLES = {"24": "-", "36": "--", "full": ":"}
PLANS = ("Seasonal naive LP", "SARIMA-24 LP", "SARIMA-36 LP", "SARIMA-full LP", "ExtraTrees-24 LP", "ExtraTrees-36 LP",
         "ExtraTrees-full LP", "Hindsight")


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"_windows_{name}", REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def family(model):
    return "Hindsight" if model == "Hindsight" else model.split("-")[0].replace(" LP", "")


def compute(root):
    views = load_module("plot_sep2025_monthly_views")
    m = views.load_compare()
    helpers, fe = m.module(root, "formulation_robustness_helpers"), m.module(root, "forecasting_evaluation")
    data = helpers.v5b_load("2026-09-01")
    pre = data.loc[:m.ORIGIN].copy()
    A, O, dates = m.A, m.O, m.DATES
    act = pd.DataFrame(dict(month=dates, occupied=data.loc[dates, O].to_numpy(float), available=data.loc[dates, A].to_numpy(float)))
    act["gap"] = act.occupied / m.TAU - act.available

    fc, info = {}, []
    prior_year = dates - pd.DateOffset(years=1)
    fc["Seasonal naive"] = {t: pre.loc[prior_year, t].to_numpy(float) for t in (A, O)}
    info.append(dict(model="Seasonal naive", window="-", window_start=prior_year[0], window_end=prior_year[-1], rows_occupied=np.nan,
                     rows_available=np.nan, converged=""))
    for label, n in WINDOWS:
        w = pre.iloc[-n:] if n else pre
        assert w.index[-1] == m.ORIGIN and w.index.equals(pd.date_range(w.index[0], m.ORIGIN, freq="MS"))
        sar, et, rows, conv = {}, {}, {}, []
        for t in (A, O):
            try:
                sar[t], meta = m.sarima_24(w, t)  # existing SARIMA function; fits on every month of the window given
                conv.append(f"{'occupied' if t == O else 'available'}: converged ({meta['training_rows']} labels)")
            except SystemExit as exc:
                conv.append(f"{'occupied' if t == O else 'available'}: FAILED ({exc})")
            et[t], rows[t] = m.extra_trees_24(fe, w, t)  # asserts every feature date lies inside the window
        if len(sar) == 2:
            fc[f"SARIMA-{label}"] = sar
        fc[f"ExtraTrees-{label}"] = et
        info.append(dict(model=f"SARIMA-{label}", window=label, window_start=w.index[0], window_end=w.index[-1], rows_occupied=len(w),
                         rows_available=len(w), converged="; ".join(conv)))
        info.append(dict(model=f"ExtraTrees-{label}", window=label, window_start=w.index[0], window_end=w.index[-1], rows_occupied=rows[O],
                         rows_available=rows[A], converged=""))

    forecasts = pd.DataFrame([dict(model=k, month=dates[i], forecast_occupied=v[O][i], forecast_available=v[A][i],
                                   forecast_gap=v[O][i] / m.TAU - v[A][i]) for k, v in fc.items() for i in range(6)])
    saved_plans = pd.read_csv(SAVED / "plans.csv", parse_dates=["month"], float_precision="round_trip")
    hind_x = saved_plans[saved_plans.plan.eq("Hindsight")].sort_values("month").x.to_numpy()
    d = act.gap.to_numpy()
    plan_x = {f"{k} LP": m.solve(forecasts[forecasts.model.eq(k)].forecast_gap.to_numpy()) for k in fc}
    plan_x["Hindsight"] = hind_x
    plan_rows, cost_rows, totals = [], [], []
    for plan, x in plan_x.items():
        a, r = m.flows(x)
        short, spare = np.maximum(d - x, 0), np.maximum(x - d, 0)
        for i in range(6):
            plan_rows.append(dict(plan=plan, month=dates[i], x=x[i], a=a[i], r=r[i], shortfall=short[i], spare=spare[i]))
            parts = dict(running=m.C * x[i], opening=m.A_PLUS * a[i], closing=m.A_MINUS * r[i], shortfall_cost=m.P * short[i])
            cost_rows.append(dict(plan=plan, month=dates[i], **parts, monthly_cost=sum(parts.values())))
        sc = m.score(x, d)
        totals.append(dict(plan=plan, running=m.C * x.sum(), opening=m.A_PLUS * a.sum(), closing=m.A_MINUS * r.sum(), operating=sc["operating"],
                           shortfall_bed_months=sc["shortfall_bed_months"], shortfall_cost=sc["shortfall_cost"], total=sc["total"]))
    plans, costs, totals = pd.DataFrame(plan_rows), pd.DataFrame(cost_rows), pd.DataFrame(totals)
    hind = float(totals.loc[totals.plan.eq("Hindsight"), "total"].iloc[0])
    totals["excess_over_hindsight"] = totals.total - hind
    assert (totals.total >= hind * (1 - 1e-9)).all()
    for p in totals.plan:
        assert abs(costs[costs.plan.eq(p)].monthly_cost.sum() - totals.loc[totals.plan.eq(p), "total"].iloc[0]) <= 1e-6 * hind

    acc = []
    sn = forecasts[forecasts.model.eq("Seasonal naive")]
    for r in info:
        g = forecasts[forecasts.model.eq(r["model"])]
        row = dict(r)
        if len(g):
            for key, col in (("occupied", "forecast_occupied"), ("available", "forecast_available"), ("gap", "forecast_gap")):
                e, e_sn = np.abs(act[key].to_numpy() - g[col].to_numpy()), np.abs(act[key].to_numpy() - sn[col].to_numpy())
                row[f"{key}_mae"] = e.mean()
                row[f"{key}_months_closer_than_sn"] = np.nan if r["model"] == "Seasonal naive" else int((e < e_sn).sum())
            row["mean_signed_gap_error"] = (act.gap.to_numpy() - g.forecast_gap.to_numpy()).mean()
        acc.append(row)
    accuracy = pd.DataFrame(acc)
    reproduce(forecasts, totals, accuracy, m)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, frame in (("forecasts", forecasts), ("plans", plans), ("monthly_costs", costs), ("totals", totals), ("accuracy", accuracy)):
        frame.to_csv(OUT / f"{name}.csv", index=False, float_format="%.17g", date_format="%Y-%m-%d")
    return act


def reproduce(forecasts, totals, accuracy, m):
    """W24 must reproduce the published Sep 2025 run; stop otherwise."""
    saved_fc = pd.read_csv(SAVED / "forecasts.csv", float_precision="round_trip")
    saved_tot = pd.read_csv(SAVED / "totals.csv", float_precision="round_trip").set_index("plan").total
    for name, saved_name in (("Seasonal naive", "Seasonal naive"), ("SARIMA-24", "SARIMA-24"), ("ExtraTrees-24", "ExtraTrees-24")):
        new = forecasts[forecasts.model.eq(name)][["forecast_occupied", "forecast_available"]].to_numpy()
        old = saved_fc[saved_fc.source.eq(saved_name)][["forecast_occupied", "forecast_available"]].to_numpy()
        np.testing.assert_allclose(new, old, rtol=0, atol=1e-6)
        mine = float(totals.loc[totals.plan.eq(f"{name} LP"), "total"].iloc[0])
        assert abs(mine - saved_tot[f"{saved_name} LP"]) <= 1e-6 * mine, name
    expect = {"Seasonal naive": (16.137, 313), "SARIMA-24": (16.108, 346), "ExtraTrees-24": (23.659, 454)}
    for name, (cost, mae) in expect.items():
        assert round(float(totals.loc[totals.plan.eq(f"{name} LP"), "total"].iloc[0]) / 1e6, 3) == cost, name
        assert round(float(accuracy.loc[accuracy.model.eq(name), "gap_mae"].iloc[0])) == mae, name
    hind = float(totals.loc[totals.plan.eq("Hindsight"), "total"].iloc[0])
    assert abs(hind - saved_tot["Hindsight"]) <= 1e-9 * hind


def bar_layout(ax, values, sn_value, ylabel, fmt, plt, hatch_parts=None, hindsight=None):
    width = 0.36
    for k, fam in enumerate(("SARIMA", "ExtraTrees")):
        for i, (label, _) in enumerate(WINDOWS):
            xpos = i + (k - 0.5) * (width + 0.04)
            v = values.get(f"{fam}-{label}")
            if v is None or np.isnan(v):
                ax.text(xpos, 0, "fit failed", ha="center", va="bottom", fontsize=8, rotation=90)
                continue
            if hatch_parts:
                op, sh = hatch_parts[f"{fam}-{label}"]
                ax.bar(xpos, op, width, color=COLOURS[fam], edgecolor="white", lw=0.8, zorder=2)
                ax.bar(xpos, sh, width, bottom=op, color="white", edgecolor=COLOURS[fam], hatch="////", lw=1.0, zorder=2)
            else:
                ax.bar(xpos, v, width, color=COLOURS[fam], edgecolor="white", lw=0.8, zorder=2)
            ax.text(xpos, v, fmt(v), ha="center", va="bottom", fontsize=8.5, zorder=4)
    lines = [ax.axhline(sn_value, color=COLOURS["Seasonal naive"], ls="--", lw=1.8, zorder=3, label=f"Seasonal naive ({fmt(sn_value)})")]
    if hindsight is not None:
        lines.append(ax.axhline(hindsight, color="black", lw=1.6, zorder=3, label=f"Hindsight ({fmt(hindsight)})"))
    ax.set_xticks(range(len(WINDOWS)), ["24 months", "36 months", "Full history"])
    ax.set_xlim(-0.6, len(WINDOWS) - 0.4)
    ax.set_xlabel("Training window")
    ax.set_ylabel(ylabel)
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"))
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return lines


def family_legend(ax, extra=(), loc="upper left"):
    from matplotlib.patches import Patch
    handles = [Patch(color=COLOURS["SARIMA"], label="SARIMA"), Patch(color=COLOURS["ExtraTrees"], label="ExtraTrees")] + list(extra)
    ax.legend(handles=handles, loc=loc, frameon=False, fontsize=9)


def save(fig, stem, plt):
    fig.savefig(REPO / "figures" / f"{stem}.png", dpi=300, facecolor="white")
    fig.savefig(REPO / "figures" / f"{stem}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def figures(plt):
    acc = pd.read_csv(OUT / "accuracy.csv", float_precision="round_trip").set_index("model")
    tot = pd.read_csv(OUT / "totals.csv", float_precision="round_trip").set_index("plan")
    fc = pd.read_csv(OUT / "forecasts.csv", parse_dates=["month"], float_precision="round_trip")
    whole = lambda v: f"{v:,.0f}"
    mae = lambda key: {k: acc.loc[k, f"{key}_mae"] for k in acc.index if k != "Seasonal naive"}

    fig, ax = plt.subplots(figsize=(9, 4.8))
    vals = mae("gap")
    lines = bar_layout(ax, vals, acc.loc["Seasonal naive", "gap_mae"], "Gap MAE (beds)", whole, plt)
    ax.set_ylim(0, max(vals.values()) * 1.18)
    family_legend(ax, lines, loc="upper right")
    ax.set_title("Gap MAE by training window (Sep 2025 origin)", loc="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "sep2025_windows_gap_mae", plt)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, key, title in zip(axes, ("occupied", "available"), ("Occupied beds", "Available beds")):
        vals = mae(key)
        lines = bar_layout(ax, vals, acc.loc["Seasonal naive", f"{key}_mae"], "MAE (beds)", whole, plt)
        ax.set_ylim(0, max(max(vals.values()), acc.loc["Seasonal naive", f"{key}_mae"]) * 1.32)
        ax.set_title(title, loc="left", fontweight="semibold")
        family_legend(ax, lines, loc="upper right")
    fig.suptitle("Occupied and available bed MAE by training window (Sep 2025 origin)", x=0.01, ha="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "sep2025_windows_bed_mae", plt)

    from matplotlib.patches import Patch
    fig, ax = plt.subplots(figsize=(9, 5))
    keys = [k for k in acc.index if k != "Seasonal naive"]
    vals = {k: tot.loc[f"{k} LP", "total"] / 1e6 for k in keys}
    parts = {k: (tot.loc[f"{k} LP", "operating"] / 1e6, tot.loc[f"{k} LP", "shortfall_cost"] / 1e6) for k in keys}
    lines = bar_layout(ax, vals, tot.loc["Seasonal naive LP", "total"] / 1e6, "LP total realised cost (million model units)", lambda v: f"{v:.2f}", plt,
                       hatch_parts=parts, hindsight=tot.loc["Hindsight", "total"] / 1e6)
    ax.set_ylim(0, max(vals.values()) * 1.15)
    family_legend(ax, [Patch(facecolor="white", edgecolor="#555555", hatch="////", label="Shortfall cost (hatched)")] + lines, loc="upper right")
    ax.set_title("LP total realised cost by training window (Sep 2025 origin)", loc="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "sep2025_windows_total_cost", plt)

    act = pd.read_csv(SAVED / "actuals.csv", parse_dates=["month"], float_precision="round_trip")
    fig, ax = plt.subplots(figsize=(11, 5))
    order = ["Seasonal naive"] + [f"{f}-{w}" for f in ("SARIMA", "ExtraTrees") for w, _ in WINDOWS]
    for model in [k for k in order if k in set(fc.model)]:
        g = fc[fc.model.eq(model)]
        fam = family(model)
        ls = "-" if model == "Seasonal naive" else STYLES[model.split("-")[1]]
        ax.plot(g.month, g.forecast_gap, color=COLOURS[fam], ls=ls, lw=1.8, marker="o" if fam != "Seasonal naive" else "D", ms=4, label=model)
    ax.plot(act.month, act.gap, color="black", lw=3, marker="o", ms=6, label="Actual")
    ax.set_xticks(act.month, [f"{d:%b %Y}" for d in act.month])
    ax.set_ylabel("Planning gap (beds)")
    ax.set_xlabel("Month")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=9)
    ax.set_title("Planning gap by month: forecasts by training window vs actual (Sep 2025 origin)", loc="left", fontweight="semibold")
    fig.tight_layout()
    save(fig, "sep2025_windows_gap_forecasts", plt)

    heatmap(plt)


def heatmap(plt):
    from matplotlib.colors import Normalize
    plans = pd.read_csv(OUT / "plans.csv", parse_dates=["month"], float_precision="round_trip")
    costs = pd.read_csv(OUT / "monthly_costs.csv", parse_dates=["month"], float_precision="round_trip")
    tot = pd.read_csv(OUT / "totals.csv", float_precision="round_trip").set_index("plan")
    rows = [p for p in PLANS if p in tot.index]
    months = sorted(costs.month.unique())
    grid = np.array([[costs[(costs.plan == p) & (costs.month == mo)].monthly_cost.iloc[0] / 1e6 for mo in months] for p in rows])
    totals = np.array([tot.loc[p, "total"] / 1e6 for p in rows])
    cmap = plt.get_cmap("YlOrRd")
    norm_m, norm_t = Normalize(grid.min(), grid.max()), Normalize(totals.min(), totals.max())
    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    for i, p in enumerate(rows):
        for j in range(len(months) + 1):
            if j < len(months):
                v, rgba = grid[i, j], cmap(norm_m(grid[i, j]))
                g = plans[(plans.plan == p) & (plans.month == months[j])].iloc[0]
                sub = f"short {g.shortfall:,.0f} beds" if g.shortfall > 0.5 else f"spare {g.spare:,.0f} beds" if g.spare > 0.5 else ""
                xj = j
            else:
                v, rgba, sub, xj = totals[i], cmap(norm_t(totals[i])), "", j + 0.25
            ax.add_patch(plt.Rectangle((xj, i), 1, 1, facecolor=rgba, edgecolor="white", lw=1.5))
            ink = "white" if 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2] < 0.5 else "black"
            ax.text(xj + 0.5, i + (0.42 if sub else 0.5), f"{v:.2f}", ha="center", va="center", fontsize=10, color=ink,
                    fontweight="semibold" if j == len(months) else "normal")
            if sub:
                ax.text(xj + 0.5, i + 0.72, sub, ha="center", va="center", fontsize=7, color=ink)
    for k in (1, 4, 7):
        ax.plot([0, len(months) + 1.25], [k, k], color="black", lw=1.0)
    ax.set_xlim(0, len(months) + 1.25)
    ax.set_ylim(len(rows), 0)
    ax.set_xticks([j + 0.5 for j in range(len(months))] + [len(months) + 0.75], [f"{pd.Timestamp(mo):%b %Y}" for mo in months] + ["Total"])
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.5 for i in range(len(rows))], rows)
    for tick, p in zip(ax.get_yticklabels(), rows):
        tick.set_color(COLOURS[family(p)])
        tick.set_fontweight("semibold")
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm_m, cmap=cmap), ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("Monthly realised cost (million model units)")
    cb.outline.set_visible(False)
    ax.set_title("Monthly realised cost by training window (Sep 2025 origin)", loc="left", fontsize=13, fontweight="semibold", pad=30)
    fig.text(0.99, 0.01, "Total column uses its own scale of the same colours.", ha="right", fontsize=7.5, color="#555555")
    fig.tight_layout()
    save(fig, "sep2025_windows_monthly_cost_heatmap", plt)


def readme_table():
    acc = pd.read_csv(OUT / "accuracy.csv", float_precision="round_trip").set_index("model")
    tot = pd.read_csv(OUT / "totals.csv", float_precision="round_trip").set_index("plan")
    window = {"-": "same month one year earlier", "24": "Oct 2023 - Sep 2025", "36": "Oct 2022 - Sep 2025", "full": "Dec 2020 - Sep 2025"}
    lines = ["| Model | Training window | Training rows / labels | Occupied MAE | Available MAE | Gap MAE | Gap months closer than SN | LP total cost | Shortfall cost | Excess over hindsight |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for model in acc.index:
        r, t = acc.loc[model], tot.loc[f"{model} LP"]
        rows = "-" if model == "Seasonal naive" else (f"{int(r.rows_occupied)} labels" if model.startswith("SARIMA") else f"{int(r.rows_occupied)} rows")
        closer = "-" if model == "Seasonal naive" else f"{int(r.gap_months_closer_than_sn)} of 6"
        lines.append(f"| {model} | {window[str(r.window)]} | {rows} | {r.occupied_mae:,.0f} | {r.available_mae:,.0f} | {r.gap_mae:,.0f} | {closer} | "
                     f"{t.total / 1e6:.2f} | {t.shortfall_cost / 1e6:.2f} | {t.excess_over_hindsight / 1e6:.2f} |")
    h = tot.loc["Hindsight"]
    lines.append(f"| Hindsight | - | - | - | - | - | - | {h.total / 1e6:.2f} | {h.shortfall_cost / 1e6:.2f} | 0.00 |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    compute(args.project_root.resolve())
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "sep2025-windows", "hatch.linewidth": 0.6})
    figures(plt)
    acc = pd.read_csv(OUT / "accuracy.csv", float_precision="round_trip")
    print(acc.to_string())
    print(pd.read_csv(OUT / "totals.csv").to_string())
    print(readme_table())


if __name__ == "__main__":
    main()
