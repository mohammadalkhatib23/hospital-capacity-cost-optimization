"""Solve the README "Optimisation problem" (point and SAA) for five origins and score on actual outcomes.

Run with --project-root pointing to the full NHS workspace. Uses only saved inputs: SARIMA point forecasts
(2025 origins: six_month_sarima_forecasts.csv; 2026 origins: confirm_inputs_v5b.csv) and saved empirical scenario
banks (2025: centered_scenarios.csv; 2026: scenarios_v6.csv). No forecast is fitted and no scenario is generated.
Actuals come from the September 2026 workbook through the v5b loader (SHA-256 checked). Plans are verified against
the saved plans and costs against the saved scores. Writes figures/point_vs_saa_five_origins.{png,svg} and prints the
README table.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd
import pulp

ORIGINS = ("2025-02-01", "2025-06-01", "2025-08-01", "2026-02-01", "2026-03-01")
A, O = "g_and_a_beds_available", "g_and_a_beds_occupied"
TAU, C, A_PLUS, A_MINUS, P, X_MAX, RAMP = 0.90, 500.0, 550.0, 300.0, 5000.0, 15000.0, 5000.0
SIM = "outputs/sarima_uncertainty_generator_comparison"
METHOD = "outputs/frozen_six_month_method_window_saa_comparison"
FR = "outputs/formulation_robustness"


def months(origin):
    return pd.date_range(pd.Timestamp(origin) + pd.DateOffset(months=1), periods=6, freq="MS")


def solve(gaps, weights):
    """The README program: point planning when one path is given, SAA when N equally weighted paths are given."""
    gaps = np.atleast_2d(np.asarray(gaps, float))
    n, T = gaps.shape
    lp = pulp.LpProblem("readme_capacity_plan", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x_{t}", 0, X_MAX) for t in range(T)]
    a = [pulp.LpVariable(f"a_{t}", 0, RAMP) for t in range(T)]
    r = [pulp.LpVariable(f"r_{t}", 0, RAMP) for t in range(T)]
    s = [[pulp.LpVariable(f"s_{j}_{t}", 0) for t in range(T)] for j in range(n)]
    u = [[pulp.LpVariable(f"u_{j}_{t}", 0) for t in range(T)] for j in range(n)]
    for t in range(T):
        lp += x[t] == (x[t - 1] if t else 0) + a[t] - r[t]
        for j in range(n):
            lp += x[t] + s[j][t] - u[j][t] == float(gaps[j, t])
    lp += pulp.lpSum(C * x[t] + A_PLUS * a[t] + A_MINUS * r[t] for t in range(T)) + P * pulp.lpSum(
        float(weights[j]) * s[j][t] for j in range(n) for t in range(T))
    status = pulp.LpStatus[lp.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))]
    assert status == "Optimal", status
    return dict(x=np.array([v.value() for v in x]), a=np.array([v.value() for v in a]), r=np.array([v.value() for v in r]),
                objective=float(pulp.value(lp.objective)))


def score(plan, actual_gap):
    operating = float(np.sum(C * plan["x"] + A_PLUS * plan["a"] + A_MINUS * plan["r"]))
    shortfall = float(np.maximum(actual_gap - plan["x"], 0).sum())  # no deadband
    return dict(operating=operating, shortfall_bed_months=shortfall, shortfall_cost=P * shortfall, total=operating + P * shortfall)


def load(root):
    source = json.loads((root / "data/raw_v5/SOURCE.json").read_text(encoding="utf-8"))
    book = root / "data/raw_v5" / source["file"]
    assert hashlib.sha256(book.read_bytes()).hexdigest() == source["sha256"]
    spec = importlib.util.spec_from_file_location("_point_vs_saa_v5b", root / "formulation_robustness_helpers.py")
    helpers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helpers
    spec.loader.exec_module(helpers)
    actuals = helpers.v5b_load("2026-09-01")
    read = lambda rel: pd.read_csv(root / rel, float_precision="round_trip")
    old_point = read("outputs/six_month_sarima_frozen_planning_publication/six_month_sarima_forecasts.csv")
    new_point = read(f"{FR}/tables/confirm_inputs_v5b.csv")
    old_bank = read("outputs/frozen_six_month_sarima_saa_comparison/centered_scenarios.csv")
    new_bank = read(f"{FR}/tables/scenarios_v6.csv")
    inputs = {}
    for origin in ORIGINS:
        dates = months(origin)
        if origin < "2026":
            f = old_point[old_point.origin.eq(origin) & old_point.policy.eq("SARIMA")].pivot(index="horizon", columns="target", values="prediction")
            o_hat, a_hat = f[O].to_numpy(float), f[A].to_numpy(float)
            bank = old_bank[old_bank.origin.eq(origin)]
        else:
            f = new_point[new_point.origin.eq(origin)].sort_values("horizon")
            o_hat, a_hat = f.O_hat.to_numpy(float), f.A_hat.to_numpy(float)
            bank = new_bank[new_bank.origin.eq(origin)]
            # The v6 bank must be the confirmation bank: same quantiles as the saved v5b inputs.
            g = bank.assign(d=bank.occupied_demand / TAU - bank.available_capacity).pivot(index="scenario_id", columns="horizon", values="d")
            for q, col in ((0.1, "SAA_p10"), (0.5, "SAA_p50"), (0.9, "SAA_p90")):
                np.testing.assert_allclose(np.quantile(g.to_numpy(), q, axis=0), f[col].to_numpy(), rtol=0, atol=1e-6)
        occ = bank.pivot(index="scenario_id", columns="horizon", values="occupied_demand")
        av = bank.pivot(index="scenario_id", columns="horizon", values="available_capacity").loc[occ.index]
        w = bank.groupby("scenario_id").probability.first().loc[occ.index].to_numpy(float)
        assert np.allclose(w, 1 / len(w))
        np.testing.assert_allclose((w[:, None] * occ.to_numpy()).sum(0), o_hat, rtol=0, atol=1e-6)  # centred on the forecast
        np.testing.assert_allclose((w[:, None] * av.to_numpy()).sum(0), a_hat, rtol=0, atol=1e-6)
        inputs[origin] = dict(dates=dates, point_gap=o_hat / TAU - a_hat, scen_gaps=occ.to_numpy() / TAU - av.to_numpy(), weights=w,
                              actual_gap=actuals.loc[dates, O].to_numpy(float) / TAU - actuals.loc[dates, A].to_numpy(float))
    return inputs


def saved_reference(root):
    read = lambda rel: pd.read_csv(root / rel, float_precision="round_trip")
    plans, costs = {}, {}
    sim_plans, sim_costs = read(f"{SIM}/frozen_decisions.csv"), read(f"{SIM}/policy_cost_comparison.csv")
    hp, hc = read(f"{METHOD}/frozen_decisions.csv"), read(f"{METHOD}/realized_policy_summary.csv")
    conf_plans, conf_scores = read(f"{FR}/plans_confirm_v5b.csv"), read(f"{FR}/tables/confirm_scores_v5b.csv")
    for origin in ORIGINS:
        if origin < "2026":
            for name, policy in (("Point", "SARIMA point"), ("SAA", "Empirical-path SAA")):
                g = sim_plans[sim_plans.origin.eq(origin) & sim_plans.policy.eq(policy)].sort_values("horizon_step")
                plans[(origin, name)] = g.additional_capacity_active.to_numpy(float)
                costs[(origin, name)] = float(sim_costs[sim_costs.origin.eq(origin) & sim_costs.policy.eq(policy)].total_realized_cost.iloc[0])
            g = hp[hp.origin.eq(origin) & hp.config.eq("hindsight")].sort_values("horizon_step")
            plans[(origin, "Hindsight")] = g.additional_capacity_active.to_numpy(float)
            costs[(origin, "Hindsight")] = float(hc[hc.origin.eq(origin) & hc.config.eq("hindsight")].total_modeled_cost.iloc[0])
        else:
            for name, policy in (("Point", "SARIMA point"), ("SAA", "Empirical-path SAA")):
                g = conf_plans[conf_plans.origin.eq(origin) & conf_plans.environment.eq("E0") & conf_plans.policy.eq(policy)].iloc[0]
                plans[(origin, name)] = np.array([g[f"x{k}"] for k in range(1, 7)], float)
            for name, policy in (("Point", "SARIMA point"), ("SAA", "Empirical-path SAA"), ("Hindsight", "Hindsight")):
                costs[(origin, name)] = float(conf_scores[conf_scores.origin.eq(origin) & conf_scores.environment.eq("E0") & conf_scores.policy.eq(policy)].cost.iloc[0])
    return plans, costs


def figure(table, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "point-vs-saa", "hatch.color": "#3a3a3a", "hatch.linewidth": 0.6})
    tones = {"Point": "#5b5b5b", "SAA": "#b4b4b4"}
    fig, ax = plt.subplots(figsize=(10, 5.2))
    width, gap = 0.36, 0.04
    for i, r in enumerate(table.itertuples()):
        for k, name in enumerate(("Point", "SAA")):
            xpos = i + (k - 0.5) * (width + gap)
            op, sh = getattr(r, f"{name}_operating"), getattr(r, f"{name}_shortfall_cost")
            ax.bar(xpos, op, width, color=tones[name], edgecolor="white", linewidth=0.8, zorder=2)
            ax.bar(xpos, sh, width, bottom=op, color="white", edgecolor=tones[name], hatch="////", linewidth=1.0, zorder=2)
        top = max(r.Point_total, r.SAA_total)
        ax.plot([i - width - gap, i + width + gap], [r.Hindsight_total] * 2, color="black", lw=2.2, zorder=3)
        ax.text(i, top + 0.35, f"Point - SAA = {r.Point_minus_SAA:.2f}m", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(table)), [f"{pd.Timestamp(o):%b %Y}\n{m}" for o, m in zip(table.Origin, table.Forecast_months)])
    ax.set_ylabel("Realised cost (million model units)")
    ax.set_title("Point vs SAA: realised cost by origin", loc="left", fontweight="semibold")
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8, zorder=0)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_ylim(0, max(table.Point_total.max(), table.SAA_total.max()) * 1.15)
    handles = [Patch(facecolor=tones["Point"], label="Point: operating cost"), Patch(facecolor=tones["SAA"], label="SAA: operating cost"),
               Patch(facecolor="white", edgecolor="#5b5b5b", hatch="////", label="Shortfall cost (hatched)"),
               Line2D([0], [0], color="black", lw=2.2, label="Hindsight cost")]
    ax.legend(handles=handles, loc="upper left", frameon=False, ncol=2, fontsize=9)
    fig.tight_layout()
    fig.savefig(out_dir / "point_vs_saa_five_origins.png", dpi=300, facecolor="white")
    fig.savefig(out_dir / "point_vs_saa_five_origins.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "figures")
    args = parser.parse_args()
    root = args.project_root.resolve()
    inputs = load(root)
    saved_plans, saved_costs = saved_reference(root)
    rows, checks = [], dict(max_plan_diff=0.0, max_cost_rel_diff=0.0)
    for origin, d in inputs.items():
        point = solve(d["point_gap"], [1.0])
        saa = solve(d["scen_gaps"], d["weights"])
        hind = solve(d["actual_gap"], [1.0])
        equal = solve(np.tile(d["point_gap"], (len(d["weights"]), 1)), d["weights"])
        assert np.max(np.abs(equal["x"] - point["x"])) <= 1e-3 and abs(equal["objective"] - point["objective"]) <= 1e-6 * point["objective"], "equal-scenario SAA != point"
        res = {name: score(plan, d["actual_gap"]) for name, plan in (("Point", point), ("SAA", saa), ("Hindsight", hind))}
        for name, plan in (("Point", point), ("SAA", saa), ("Hindsight", hind)):
            if (origin, name) in saved_plans:
                checks["max_plan_diff"] = max(checks["max_plan_diff"], float(np.max(np.abs(plan["x"] - saved_plans[(origin, name)]))))
            checks["max_cost_rel_diff"] = max(checks["max_cost_rel_diff"], abs(res[name]["total"] - saved_costs[(origin, name)]) / saved_costs[(origin, name)])
        assert res["Hindsight"]["total"] <= min(res["Point"]["total"], res["SAA"]["total"]) * (1 + 1e-9), f"hindsight not lowest at {origin}"
        rows.append(dict(Origin=origin, Forecast_months=f"{d['dates'][0]:%b %Y}-{d['dates'][-1]:%b %Y}", N=len(d["weights"]),
                         **{f"{k}_{m}": v / 1e6 for k in ("Point", "SAA") for m, v in res[k].items() if m in ("operating", "shortfall_cost", "total")},
                         Point_shortfall_bed_months=res["Point"]["shortfall_bed_months"], SAA_shortfall_bed_months=res["SAA"]["shortfall_bed_months"],
                         Hindsight_total=res["Hindsight"]["total"] / 1e6, Hindsight_operating=res["Hindsight"]["operating"] / 1e6,
                         Hindsight_shortfall_bed_months=res["Hindsight"]["shortfall_bed_months"]))
    assert checks["max_plan_diff"] <= 1e-3, checks
    assert checks["max_cost_rel_diff"] <= 1e-6, checks
    table = pd.DataFrame(rows)
    table["Point_minus_SAA"] = table.Point_total - table.SAA_total
    args.output_dir.mkdir(parents=True, exist_ok=True)
    figure(table, args.output_dir)
    print(json.dumps(checks))
    print(table.to_string())
    print("\n| Origin | Forecast months | N | Point operating | Point shortfall cost | Point total | SAA operating | SAA shortfall cost | SAA total | Hindsight total | Point - SAA |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in table.itertuples():
        print(f"| {pd.Timestamp(r.Origin):%b %Y} | {r.Forecast_months} | {r.N} | {r.Point_operating:.2f} | {r.Point_shortfall_cost:.2f} | {r.Point_total:.2f} | "
              f"{r.SAA_operating:.2f} | {r.SAA_shortfall_cost:.2f} | {r.SAA_total:.2f} | {r.Hindsight_total:.2f} | {r.Point_minus_SAA:.2f} |")


if __name__ == "__main__":
    main()
