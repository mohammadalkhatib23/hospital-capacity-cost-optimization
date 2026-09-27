"""Development-only paired paths for the separate frozen uncertainty experiment.

No fitting, solving, or filesystem writes occur on import. Authoritative feature,
estimator, recursion-row and solver implementations remain in their existing modules.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from joblib import parallel_backend

import forecasting_evaluation as fe
import optimization_evaluation as oe

A, O = oe.AVAILABLE_TARGET, oe.OCCUPIED_TARGET
RHO = 0.90
CUTOFF = pd.Timestamp("2025-08-01")
ORIGINS = pd.DatetimeIndex(["2025-08-01", "2025-11-01", "2026-02-01"])
MODELS = {A: "RandomForestRegressor", O: "ExtraTreesRegressor"}
MODE = "target_only_features"
P, B, C = "frozen_persistence", "frozen_locked_ml", "frozen_ml_uncertainty"
POLICIES = (P, B, C)


def eligible_inventory(data, cutoff=CUTOFF, minimum=None):
    """Inventory every development origin before observing evaluation costs."""
    minimum = fe.EvaluationConfig().minimum_training_observations if minimum is None else minimum
    history = data.loc[:cutoff, [A, O]].copy()
    rows = []
    for origin in history.index:
        usable = [fe.build_target_only_features(history.loc[:origin, t]).dropna().index for t in (A, O)]
        dates = pd.date_range(origin + pd.offsets.MonthBegin(), periods=3, freq="MS")
        complete = dates.isin(history.index).all() and dates.max() <= cutoff
        row_count = min(map(len, usable))
        rows.append({"forecast_origin": origin, "training_start": usable[0].min(),
                     "training_end": origin, "training_rows": row_count,
                     "forecast_start": dates[0], "forecast_end": dates[-1],
                     "minimum_training_rows": minimum, "complete_before_cutoff": bool(complete),
                     "eligible": bool(complete and row_count >= minimum),
                     "reason": "eligible" if complete and row_count >= minimum else
                     "too_few_training_rows" if row_count < minimum else "outcomes_after_cutoff"})
    return pd.DataFrame(rows)


def frozen_target_forecast(data, target, origin, minimum=None):
    """Exactly one expanding-history fit followed by three recursive predictions."""
    minimum = fe.EvaluationConfig().minimum_training_observations if minimum is None else minimum
    history = data.loc[:origin, [target]].copy()
    if history.empty or history.index.max() != origin or history.isna().any().any():
        raise ValueError("History must be complete and end at the requested origin.")
    features = fe.build_feature_frame(target, history, MODE).dropna()
    if len(features) < minimum:
        raise ValueError("Too few usable training rows.")
    model = fe.make_ml_estimator(MODELS[target], 42)
    simulated = history.copy()
    records = []
    with parallel_backend("sequential"):
        model.fit(features, history.loc[features.index, target])
        for h, date in enumerate(pd.date_range(origin + pd.offsets.MonthBegin(), periods=3, freq="MS"), 1):
            row = fe.build_recursive_feature_row(target, simulated, date, MODE)
            raw_prediction = float(model.predict(row)[0])
            prediction = float(fe.apply_domain_bounds(target, [raw_prediction])[0])
            records.append({"forecast_origin": origin, "forecast_date": date, "horizon_step": h,
                            "target": target, "prediction": prediction, "raw_prediction": raw_prediction,
                            "prediction_domain_adjustment": prediction - raw_prediction,
                            "training_start": features.index.min(), "training_end": features.index.max(),
                            "training_rows": len(features), "history_start": history.index.min(),
                            "history_end": history.index.max(), "feature_count": len(row.columns),
                            "imputer_fit_end": model.named_steps["imputer"].fit_index_end_,
                            "lag_1": row.iloc[0].target_lag_1, "expected_lag_1": simulated[target].iloc[-1],
                            "lag_2": row.iloc[0].target_lag_2, "prior_prediction_count": h - 1,
                            "model": MODELS[target], "feature_mode": MODE})
            simulated.loc[date, target] = prediction
    return pd.DataFrame(records)


def build_residual_bank(data, cutoff=CUTOFF):
    """Fit development origins only; attach actuals after each frozen path exists."""
    development = data.loc[:cutoff, [A, O]].copy()
    inventory = eligible_inventory(development, cutoff)
    audit = []
    for origin in inventory.loc[inventory.eligible, "forecast_origin"]:
        for target in (A, O):
            audit.append(frozen_target_forecast(development, target, origin))
    if not audit:
        raise ValueError("No complete eligible development paths.")
    audit = pd.concat(audit, ignore_index=True)
    keys = ["forecast_origin", "forecast_date", "horizon_step"]
    bank = audit.pivot(index=keys, columns="target", values="prediction").reset_index()
    bank = bank.rename(columns={A: "available_prediction", O: "occupied_prediction"})
    metadata = audit[audit.target == A][keys + ["training_start", "training_end", "training_rows"]]
    bank = bank.merge(metadata, on=keys, validate="one_to_one")
    for name, target in (("available", A), ("occupied", O)):
        bank[name + "_actual"] = development.loc[bank.forecast_date, target].to_numpy()
        bank[name + "_error"] = bank[name + "_actual"] - bank[name + "_prediction"]
    bank["actual_gap"] = bank.occupied_actual / RHO - bank.available_actual
    bank["predicted_gap"] = bank.occupied_prediction / RHO - bank.available_prediction
    bank["gap_error"] = bank.occupied_error / RHO - bank.available_error
    bank["residual_cutoff"] = cutoff
    return bank.sort_values(keys).reset_index(drop=True), audit, inventory


def center_bank(bank, cutoff=CUTOFF):
    """Center both components at each horizon without breaking path membership."""
    result = bank.copy(deep=True)
    if result.empty or result.duplicated(["forecast_origin", "horizon_step"]).any():
        raise ValueError("Empty or duplicate residual paths.")
    if (pd.to_datetime(result.forecast_date) > cutoff).any():
        raise ValueError("Residual bank contains outcomes after the cutoff.")
    for origin, group in result.groupby("forecast_origin"):
        ordered = group.sort_values("horizon_step")
        expected_dates = pd.date_range(pd.Timestamp(origin) + pd.offsets.MonthBegin(), periods=3, freq="MS")
        if ordered.horizon_step.tolist() != [1, 2, 3] or not pd.DatetimeIndex(ordered.forecast_date).equals(expected_dates):
            raise ValueError("Each residual origin needs one aligned complete three-step path.")
    for component in ("available", "occupied"):
        error = result[component + "_error"].to_numpy(dtype=float)
        if not np.isfinite(error).all():
            raise ValueError("Nonfinite residuals.")
        means = result.groupby("horizon_step")[component + "_error"].transform("mean")
        result[component + "_error_mean"] = means
        result["centered_" + component + "_error"] = error - means
    result["centered_gap_error"] = result.centered_occupied_error / RHO - result.centered_available_error
    return result


def centered_scenarios(point, bank):
    """One equally weighted complete development path per scenario; no clipping."""
    point = point.sort_index()
    if len(point) != 3 or point.index.has_duplicates or not point.index.equals(pd.date_range(point.index[0], periods=3, freq="MS")):
        raise ValueError("Point path must have three consecutive months.")
    bank = center_bank(bank)
    origins = sorted(bank.forecast_origin.unique())
    rows = []
    for source_origin in origins:
        path = bank[bank.forecast_origin == source_origin].sort_values("horizon_step")
        for h, date in enumerate(point.index, 1):
            error = path.iloc[h-1]
            available = float(point.loc[date, A]) + error.centered_available_error
            occupied = float(point.loc[date, O]) + error.centered_occupied_error
            if not np.isfinite([available, occupied]).all() or min(available, occupied) < 0 or occupied > available:
                raise ValueError("Scenario violates component domain; stop rather than silently clip or recenter.")
            rows.append({"scenario_id": f"DEV_{pd.Timestamp(source_origin):%Y_%m}",
                         "source_origin": source_origin, "source_month": error.forecast_date,
                         "forecast_month": date, "horizon_step": h, "available_capacity": available,
                         "occupied_demand": occupied, "scenario_gap": occupied / RHO - available,
                         "point_available": point.loc[date, A], "point_occupied": point.loc[date, O],
                         "point_gap": point.loc[date, O] / RHO - point.loc[date, A],
                         "centered_available_error": error.centered_available_error,
                         "centered_occupied_error": error.centered_occupied_error,
                         "centered_gap_error": error.centered_gap_error, "probability": 1 / len(origins),
                         "transformation": "none", "residual_cutoff": CUTOFF})
    scenarios = pd.DataFrame(rows)
    for _, g in scenarios.groupby("horizon_step"):
        for value, expected in (("available_capacity", "point_available"), ("occupied_demand", "point_occupied"), ("scenario_gap", "point_gap")):
            if not np.isclose(np.average(g[value], weights=g.probability), g[expected].iloc[0], atol=1e-8, rtol=0):
                raise AssertionError("Scenario mean differs from point forecast.")
    return scenarios


def solve_path(point, policy, initial_state, costs, scenarios=None):
    """Planning boundary: accepts point/scenario paths, never actual outcomes."""
    dates = pd.DatetimeIndex(point.index)
    if policy == C:
        if scenarios is None:
            raise ValueError("Uncertainty policy requires scenarios.")
        plan, accounting, metadata = oe.solve_stochastic_capacity_plan(
            dates, scenarios, RHO, costs, initial_capacity=initial_state,
            cvar_weight=0.0, policy=policy)
    elif policy in (P, B):
        plan, metadata = oe.solve_deterministic_capacity_plan(
            dates, point[A], point[O], RHO, costs, initial_capacity=initial_state, policy=policy)
        accounting = pd.DataFrame()
    else:
        raise ValueError("Unknown policy.")
    plan["forecast_origin"] = dates[0] - pd.offsets.MonthBegin()
    plan["uses_future_actuals"] = False
    plan["implemented"] = True
    plan["planning_horizon_months"] = 3
    plan["reoptimization_frequency_months"] = 3
    return plan, accounting, metadata


def evaluate_plan(plan, actuals, costs):
    result = oe.evaluate_implemented_decisions(plan, actuals, costs)
    result["operational_cost"] = result[["active_extra_capacity_cost", "addition_cost", "removal_cost", "capacity_change_cost"]].sum(axis=1)
    result["actual_gap"] = result.actual_required_capacity - result.actual_available_capacity
    result["predicted_gap"] = result.required_capacity_input - result.available_capacity_input
    return result


def summarize(rows):
    ordered = rows.sort_values("forecast_month")
    return {"operational_cost": rows.operational_cost.sum(), "shortage_cost": rows.shortage_cost.sum(),
            "total_modeled_cost": rows.total_realized_cost.sum(),
            "deficit_bed_months": rows.realized_shortage_bed_months.sum(),
            "unused_bed_months": rows.realized_unused_bed_months.sum(),
            "capacity_coverage": 1 - rows.realized_shortage_bed_months.sum() / rows.actual_required_capacity.sum(),
            "initial_state": ordered.initial_capacity_for_month.iloc[0],
            "ending_state": ordered.additional_capacity_active.iloc[-1]}


def cost_comparison(summary, label):
    indexed = summary.set_index("policy")
    a, b, c = [float(indexed.loc[p, "total_modeled_cost"]) for p in POLICIES]
    return {"period": label, "persistence_cost": a, "ML_point_cost": b, "ML_uncertainty_cost": c,
            "uncertainty_cost_reduction": b-c,
            "percentage_reduction": 100*(b-c)/b if b != 0 else np.nan}
