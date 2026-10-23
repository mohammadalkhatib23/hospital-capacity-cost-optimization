"""Forecast vs decision method at the September 2025 origin, with every forecast trained on the same 24 months.

Run with --project-root pointing to the full NHS workspace. W is the 24 published months ending at the Sep 2025
cut-off (v6 rule: outcomes up to and including the origin month). Five forecasts (Mean-24, Persistence, Seasonal
naive, SARIMA-24, ExtraTrees-24) each feed a Rule plan (open the forecast gap, within cap and ramps) and an LP plan
(the README point program). The saved full-history SARIMA forecast from v6 is a reference LP. Every plan is scored on
Oct 2025 - Mar 2026 actuals from the September 2026 workbook (v5b loader, SHA-256 checked). Writes
figures/sep2025_forecasts_vs_actual.{png,svg} and figures/sep2025_realised_cost_by_plan.{png,svg} and prints the
README table.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import warnings

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd
import pulp

ORIGIN = pd.Timestamp("2025-09-01")
A, O = "g_and_a_beds_available", "g_and_a_beds_occupied"
TAU, C, A_PLUS, A_MINUS, P, X_MAX, RAMP = 0.90, 500.0, 550.0, 300.0, 5000.0, 15000.0, 5000.0
FR = "outputs/formulation_robustness"
SOURCES = ("Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24")
DATES = pd.date_range(ORIGIN + pd.DateOffset(months=1), periods=6, freq="MS")


def module(root, name):
    spec = importlib.util.spec_from_file_location(f"_sep2025_{name}", root / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def solve(gap):
    """The README point program for one gap path."""
    T = len(gap)
    lp = pulp.LpProblem("readme_point_plan", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x_{t}", 0, X_MAX) for t in range(T)]
    a = [pulp.LpVariable(f"a_{t}", 0, RAMP) for t in range(T)]
    r = [pulp.LpVariable(f"r_{t}", 0, RAMP) for t in range(T)]
    s = [pulp.LpVariable(f"s_{t}", 0) for t in range(T)]
    u = [pulp.LpVariable(f"u_{t}", 0) for t in range(T)]
    for t in range(T):
        lp += x[t] == (x[t - 1] if t else 0) + a[t] - r[t]
        lp += x[t] + s[t] - u[t] == float(gap[t])
    lp += pulp.lpSum(C * x[t] + A_PLUS * a[t] + A_MINUS * r[t] + P * s[t] for t in range(T))
    status = pulp.LpStatus[lp.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))]
    assert status == "Optimal", status
    return np.array([v.value() for v in x])


def rule(gap):
    """Open max(0, gap) each month; if the cap or a ramp binds, move as far toward it as allowed."""
    x, prev = [], 0.0
    for g in gap:
        target = min(X_MAX, max(0.0, float(g)))
        prev = min(prev + RAMP, max(prev - RAMP, target))
        x.append(prev)
    return np.array(x)


def flows(x):
    step = np.diff(np.concatenate([[0.0], x]))
    return np.maximum(step, 0), np.maximum(-step, 0)


def feasible(x):
    a, r = flows(x)
    return bool((x >= -1e-9).all() and (x <= X_MAX + 1e-9).all() and (a <= RAMP + 1e-9).all() and (r <= RAMP + 1e-9).all())


def score(x, actual_gap):
    a, r = flows(x)
    operating = float(np.sum(C * x + A_PLUS * a + A_MINUS * r))
    shortfall = float(np.maximum(actual_gap - x, 0).sum())  # no deadband
    return dict(operating=operating, shortfall_bed_months=shortfall, shortfall_cost=P * shortfall, total=operating + P * shortfall)


def sarima_24(w, target):
    """Existing fixed SARIMA specification (formulation_robustness_helpers.SARIMA_SPEC / sarima_fit), fitted on W only."""
    from sklearn.preprocessing import StandardScaler
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    y = w[target]
    ys = StandardScaler().fit(y.to_numpy().reshape(-1, 1))
    yz = pd.Series(ys.transform(y.to_numpy().reshape(-1, 1)).ravel(), index=y.index, name=target)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fitted = SARIMAX(yz, exog=None, order=(1, 0, 0), seasonal_order=(0, 1, 0, 12), trend="n", enforce_stationarity=True,
                         enforce_invertibility=True, simple_differencing=False, missing="raise").fit(
            method="lbfgs", maxiter=400, disp=False, cov_type="none")
    converged = bool(fitted.mle_retvals.get("converged", False))
    if not converged:
        raise SystemExit(f"SARIMA-24 did not converge for {target}; stopping")
    raw = ys.inverse_transform(np.asarray(fitted.forecast(6)).reshape(-1, 1)).ravel()
    return np.maximum(0.0, raw), dict(target=target, converged=converged, training_rows=len(y),
                                      warnings=sorted({f"{type(m.message).__name__}: {m.message}" for m in caught}))


def extra_trees_24(fe, w, target):
    """ExtraTreesRegressor_target_only_features (forecasting_evaluation.make_ml_estimator, seed 42), recursive six steps, W only."""
    x = fe.build_target_only_features(w[target]).dropna()  # rows needing data before W are dropped, not filled
    assert (x.index - pd.DateOffset(months=12) >= w.index[0]).all() and x.index[-1] == ORIGIN
    if len(x) < 6:
        raise SystemExit(f"ExtraTrees-24 has {len(x)} training rows for {target}; stopping")
    estimator = fe.make_ml_estimator("ExtraTreesRegressor", 42)
    estimator.fit(x, w.loc[x.index, target])
    assert estimator.named_steps["imputer"].fit_index_end_ == ORIGIN
    simulated, values = w[[target]].copy(), []
    for date in DATES:
        assert min(date - pd.DateOffset(months=lag) for lag in fe.LAGS) >= w.index[0]
        assert simulated.index[-max(fe.ROLLING_WINDOWS)] >= w.index[0]
        row = fe.build_recursive_feature_row(target, simulated, date, "target_only_features").reindex(columns=x.columns)
        value = float(fe.apply_domain_bounds(target, [float(estimator.predict(row)[0])])[0])
        values.append(value)
        simulated.loc[date, target] = value
    return np.array(values), len(x)


def forecasts(root, data):
    fe = module(root, "forecasting_evaluation")
    w = data.loc[:ORIGIN].iloc[-24:]
    assert len(w) == 24 and w.index[0] == pd.Timestamp("2023-10-01") and w.index[-1] == ORIGIN
    assert w.index.equals(pd.date_range("2023-10-01", ORIGIN, freq="MS"))
    out, notes = {}, dict(sarima=[], extra_trees_rows={})
    for target in (A, O):
        prior_year = DATES - pd.DateOffset(years=1)
        assert prior_year.isin(w.index).all()
        out[("Mean-24", target)] = np.repeat(w[target].mean(), 6)
        out[("Persistence", target)] = np.repeat(w[target].iloc[-1], 6)
        out[("Seasonal naive", target)] = w.loc[prior_year, target].to_numpy(float)
        out[("SARIMA-24", target)], info = sarima_24(w, target)
        notes["sarima"].append(info)
        out[("ExtraTrees-24", target)], rows = extra_trees_24(fe, w, target)
        # Same seed, same trees; n_jobs=-1 (existing spec) can change float summation order by ~1e-11 beds.
        again, _ = extra_trees_24(fe, w, target)
        diff = float(np.max(np.abs(again - out[("ExtraTrees-24", target)])))
        assert diff <= 1e-6, f"Extra Trees refit is not reproducible ({diff})"
        notes["extra_trees_rows"][target] = rows
        notes.setdefault("extra_trees_refit_max_diff", {})[target] = diff
    gaps = {name: out[(name, O)] / TAU - out[(name, A)] for name in SOURCES}
    saved = pd.read_csv(root / f"{FR}/tables/forecast_inputs_v6.csv", parse_dates=["origin", "forecast_month"], float_precision="round_trip")
    saved = saved[saved.origin.eq(ORIGIN)].sort_values("horizon")
    assert saved.forecast_month.tolist() == list(DATES)
    gaps["SARIMA-full"] = saved.occupied_prediction.to_numpy() / TAU - saved.available_prediction.to_numpy()
    return gaps, notes


def figure_a(gaps, actual, out_dir, plt):
    styles = {"Mean-24": dict(ls=(0, (1, 1.5)), marker="s"), "Persistence": dict(ls=(0, (5, 2)), marker="^"),
              "Seasonal naive": dict(ls="-.", marker="D"), "SARIMA-24": dict(ls="-", marker="o"),
              "ExtraTrees-24": dict(ls=(0, (3, 1, 1, 1, 1, 1)), marker="v")}
    fig, ax = plt.subplots(figsize=(10, 5.4))
    for name, st in styles.items():
        ax.plot(DATES, gaps[name], color="#555555", lw=1.4, ms=6, mfc="white", mew=1.2, label=name, zorder=3, **st)
    ax.plot(DATES, gaps["SARIMA-full"], color="#888888", lw=0.9, ls="--", label="SARIMA-full (reference)", zorder=2)
    ax.plot(DATES, actual, color="black", lw=3, marker="o", ms=6, label="Actual", zorder=4)
    ax.set_xticks(DATES, [f"{d:%b %Y}" for d in DATES])
    ax.set_ylabel("Planning gap (beds)")
    ax.set_title("September 2025 origin: forecast gaps vs actual (24-month training)", loc="left", fontweight="semibold")
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=4, frameon=False, fontsize=9)
    fig.tight_layout()
    save(fig, out_dir, "sep2025_forecasts_vs_actual", plt)


def figure_b(table, hindsight, out_dir, plt):
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    tones = {"Rule": "#5b5b5b", "LP": "#b4b4b4"}
    fig, ax = plt.subplots(figsize=(11, 5.6))
    width, positions, centers = 0.38, [], []
    for i, name in enumerate(SOURCES):
        positions += [(name, "Rule", i - 0.2), (name, "LP", i + 0.2)]
        centers.append((i, name))
    ref_x = len(SOURCES) + 0.4
    positions.append(("SARIMA-full", "LP", ref_x))
    centers.append((ref_x, "SARIMA-full\n(reference)"))
    for name, dec, xpos in positions:
        r = table[(table.Forecast == name) & (table.Decision == dec)].iloc[0]
        ax.bar(xpos, r.operating / 1e6, width, color=tones[dec], edgecolor="white", linewidth=0.8, zorder=2)
        ax.bar(xpos, r.shortfall_cost / 1e6, width, bottom=r.operating / 1e6, color="white", edgecolor=tones[dec], hatch="////", linewidth=1.0, zorder=2)
        ax.text(xpos, r.total / 1e6 + 0.3, f"{r.total / 1e6:.2f}", ha="center", va="bottom", fontsize=8.5)
        ax.text(xpos, 0.3, dec, ha="center", va="bottom", fontsize=8, color="white" if dec == "Rule" else "black")
    ax.axvline(len(SOURCES) - 0.15, color="#bdbdbd", lw=0.8, ls=":")
    ax.axhline(hindsight / 1e6, color="black", lw=1.6, zorder=3)
    ax.set_xticks([c for c, _ in centers], [n for _, n in centers])
    ax.set_ylabel("Realised cost (million model units)")
    ax.set_title("September 2025 origin: realised cost by method", loc="left", fontweight="semibold")
    ax.yaxis.grid(True, color="#e3e3e3", lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_ylim(0, table.total.max() / 1e6 * 1.18)
    handles = [Patch(facecolor=tones["Rule"], label="Rule: operating cost"), Patch(facecolor=tones["LP"], label="LP: operating cost"),
               Patch(facecolor="white", edgecolor="#5b5b5b", hatch="////", label="Shortfall cost (hatched)"),
               Line2D([0], [0], color="black", lw=1.6, label=f"Hindsight cost ({hindsight / 1e6:.2f})")]
    ax.legend(handles=handles, loc="upper right", frameon=False, ncol=2, fontsize=9)
    fig.tight_layout()
    save(fig, out_dir, "sep2025_realised_cost_by_plan", plt)


def save(fig, out_dir, stem, plt):
    fig.savefig(out_dir / f"{stem}.png", dpi=300, facecolor="white")
    fig.savefig(out_dir / f"{stem}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "figures")
    args = parser.parse_args()
    root = args.project_root.resolve()
    source = json.loads((root / "data/raw_v5/SOURCE.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((root / "data/raw_v5" / source["file"]).read_bytes()).hexdigest() == source["sha256"]
    helpers = module(root, "formulation_robustness_helpers")
    data = helpers.v5b_load("2026-09-01")
    # Forecasts see only the cut-off prefix; actuals are read separately for scoring.
    gaps, notes = forecasts(root, data.loc[:ORIGIN].copy())
    actual = data.loc[DATES, O].to_numpy(float) / TAU - data.loc[DATES, A].to_numpy(float)

    plans = []
    for name in SOURCES:
        plans += [(name, "24 months", "Rule", rule(gaps[name])), (name, "24 months", "LP", solve(gaps[name]))]
    plans += [("SARIMA-full", "full history (reference)", "LP", solve(gaps["SARIMA-full"])), ("Hindsight", "-", "LP", solve(actual))]
    assert all(feasible(x) for *_, x in plans)
    identical = [name for name in SOURCES if np.max(np.abs(plans[2 * SOURCES.index(name)][3] - plans[2 * SOURCES.index(name) + 1][3])) <= 1e-3]

    rows = []
    for name, training, dec, x in plans:
        res = score(x, actual)
        err = actual - gaps[name] if name in gaps else None
        rows.append(dict(Forecast=name, Training=training, Decision=dec, mae=np.abs(err).mean() if err is not None else np.nan,
                         bias=err.mean() if err is not None else np.nan, **res, x=x))
    table = pd.DataFrame(rows)
    hindsight = float(table.loc[table.Forecast.eq("Hindsight"), "total"].iloc[0])
    table["excess"] = table.total - hindsight
    assert (table.total >= hindsight * (1 - 1e-9)).all(), "hindsight is not the lowest cost"

    saved_plans = pd.read_csv(root / f"{FR}/plans_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    saved_scores = pd.read_csv(root / f"{FR}/tables/scores_v6.csv", parse_dates=["origin"], float_precision="round_trip")
    pick = lambda f, policy: f[f.origin.eq(ORIGIN) & f.environment.eq("E0") & f.policy.eq(policy)].iloc[0]
    ref = table[table.Forecast.eq("SARIMA-full")].iloc[0]
    saved_x = pick(saved_plans, "SARIMA point")[[f"x{k}" for k in range(1, 7)]].to_numpy(float)
    saved_cost = float(pick(saved_scores, "SARIMA point").cost)
    checks = dict(sarima_full_plan_diff=float(np.max(np.abs(ref.x - saved_x))),
                  sarima_full_cost_rel_diff=abs(ref.total - saved_cost) / saved_cost,
                  hindsight_cost_rel_diff=abs(hindsight - float(pick(saved_scores, "Hindsight").cost)) / hindsight)
    assert checks["sarima_full_plan_diff"] <= 1e-3 and checks["sarima_full_cost_rel_diff"] <= 1e-6, checks

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "sep2025-methods", "hatch.color": "#3a3a3a", "hatch.linewidth": 0.6})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    figure_a(gaps, actual, args.output_dir, plt)
    figure_b(table[table.Forecast.ne("Hindsight")], hindsight, args.output_dir, plt)

    print(json.dumps(dict(checks=checks, identical_rule_lp=identical, **notes), indent=1, default=str))
    print("actual gap", np.round(actual, 1))
    for name, g in gaps.items():
        print(f"{name:15s}", np.round(g, 1))
    for r in table.itertuples():
        print(f"{r.Forecast:15s} {r.Decision:5s}", np.round(r.x, 1), f"shortfall {r.shortfall_bed_months:.1f}")
    print("\n| Forecast | Training data | Decision | Gap MAE (beds) | Mean signed gap error (beds) | Operating cost | Shortfall cost | Total realised cost | Excess over hindsight |")
    print("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in table.itertuples():
        mae, bias = ("-", "-") if r.Forecast == "Hindsight" else (f"{r.mae:,.0f}", f"{r.bias:,.0f}")
        print(f"| {r.Forecast} | {r.Training} | {r.Decision} | {mae} | {bias} | {r.operating / 1e6:.2f} | {r.shortfall_cost / 1e6:.2f} | "
              f"{r.total / 1e6:.2f} | {r.excess / 1e6:.2f} |")


if __name__ == "__main__":
    main()
