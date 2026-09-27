"""Revised NHS monthly forecast-to-optimization evaluation.

This module preserves the legacy experiment and writes a separate, traceable
revision. Selection of models, bias corrections, ensembles, and decision-focused
winners uses development data only. The September 2025-May 2026 period is used
once for locked evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import platform
import warnings
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pulp

from forecasting_evaluation import (
    FINAL_TEST_END,
    FINAL_TEST_START,
    TARGETS,
    EvaluationConfig,
    ModelSpec,
    add_calendar_features,
    build_model_registry,
    build_recursive_feature_row,
    build_target_only_features,
    ets_forecast,
    forecast_selected_models,
    load_nhs_targets,
    make_ml_estimator,
    seasonal_naive_forecast,
    validate_target_data,
)
from optimization_evaluation import (
    AVAILABLE_TARGET,
    OCCUPIED_TARGET,
    CostParameters,
    OptimizationEvaluationConfig,
    build_cqr_raw_forecast_cache,
    build_locked_point_forecast_cache,
    build_paired_development_residual_paths,
    build_reactive_forecast_cache,
    build_rolling_interval_cache,
    build_stochastic_value_summary,
    evaluate_implemented_decisions,
    forecast_dates_from_origin,
    load_locked_project_inputs,
    monthly_backtest_origins,
    solve_deterministic_capacity_plan,
    solve_stochastic_capacity_plan,
)
from uncertainty_evaluation import (
    UncertaintyConfig,
    build_selected_validation_predictions,
)


REVISION_OUTPUT_DIR = Path("outputs/final_optimization_revision")
LEGACY_OUTPUT_DIR = Path("outputs/final_optimization_original")
PLANNING_HORIZONS = (1, 3, 6, 9)
UPDATE_FREQUENCIES = (1, 3, 6)
HISTORY_WINDOWS: Tuple[Optional[int], ...] = (12, 24, 36, None)
CORE_POLICIES = (
    "last_value_naive",
    "seasonal_naive",
    "locked_point_forecast",
    "moderate_90",
    "conservative_95",
    "stochastic_scenarios",
    "perfect_information_oracle",
)
IMPLEMENTABLE_POLICIES = CORE_POLICIES[:-1]
DIRECT_GAP_MODELS = (
    "last_value",
    "seasonal_naive",
    "ETS",
    "Ridge",
    "RandomForestRegressor",
    "ExtraTreesRegressor",
)
if importlib.util.find_spec("xgboost") is not None:
    DIRECT_GAP_MODELS += ("XGBoost",)
RHO_VALUES = (0.85, 0.90, 0.92, 0.95)
DEVELOPMENT_END = FINAL_TEST_START - pd.offsets.MonthBegin(1)
SEED = 42
NUMERICAL_TOLERANCE = 1e-6


@dataclass(frozen=True)
class RevisionConfig:
    workbook_path: Path = Path("Beds-publication-Timeseries-March-2020-May-2026.xlsx")
    sheet_name: str = "Timeseries all acute trusts"
    deterministic_output_dir: Path = Path("outputs/final_evaluation")
    uncertainty_output_dir: Path = Path("outputs/final_uncertainty")
    legacy_output_dir: Path = LEGACY_OUTPUT_DIR
    output_dir: Path = REVISION_OUTPUT_DIR
    random_seed: int = SEED
    generate_plots: bool = True
    legacy_costs: CostParameters = field(default_factory=CostParameters)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _history_label(window: Optional[int]) -> str:
    return "all_available" if window is None else str(window)


def _month_distance(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def last_value_forecast(history: pd.Series, horizon: int) -> np.ndarray:
    """Repeat only the latest value observable at the forecast origin."""

    if history.empty or horizon < 1:
        raise ValueError("Last-value forecasting requires history and a positive horizon.")
    return np.repeat(float(history.iloc[-1]), horizon)


def build_last_value_forecast_cache(
    target_df: pd.DataFrame,
    origins: Sequence[pd.Timestamp],
    final_test_end: pd.Timestamp,
    horizon: int = 9,
) -> pd.DataFrame:
    """Create a target-specific persistence path at every monthly origin."""

    rows: List[Dict[str, object]] = []
    for origin in pd.DatetimeIndex(origins):
        dates = forecast_dates_from_origin(origin, final_test_end, horizon)
        history = target_df.loc[:origin]
        for target in TARGETS:
            values = last_value_forecast(history[target], len(dates))
            for horizon_step, (forecast_date, value) in enumerate(
                zip(dates, values), start=1
            ):
                rows.append(
                    {
                        "forecast_origin": origin,
                        "forecast_date": forecast_date,
                        "horizon_step": horizon_step,
                        "target": target,
                        "prediction": float(value),
                        "selected_model": "last_value_naive",
                        "feature_mode": "univariate",
                        "training_start": history.index.min(),
                        "training_end": history.index.max(),
                        "training_observations": len(history),
                        "history_window": "all_available",
                        "future_actual_used": False,
                    }
                )
    result = pd.DataFrame(rows)
    keys = ["forecast_origin", "forecast_date", "target"]
    if result.duplicated(keys).any():
        raise AssertionError("Last-value cache contains duplicate origin/date/target keys.")
    if not (pd.to_datetime(result["training_end"]) <= result["forecast_origin"]).all():
        raise AssertionError("Last-value cache reads beyond its origin.")
    return result.sort_values(keys).reset_index(drop=True)


def load_revision_inputs(config: RevisionConfig) -> Dict[str, object]:
    """Load locked selections and monthly targets without changing canonical winners."""

    legacy_config = OptimizationEvaluationConfig(
        workbook_path=config.workbook_path,
        sheet_name=config.sheet_name,
        deterministic_output_dir=config.deterministic_output_dir,
        uncertainty_output_dir=config.uncertainty_output_dir,
        output_dir=config.legacy_output_dir,
        generate_plots=False,
    )
    inputs = load_locked_project_inputs(legacy_config)
    target_df = inputs["target_df"]
    validate_target_data(target_df)
    if len(target_df) != 66:
        raise AssertionError(f"Expected 66 monthly observations, found {len(target_df)}.")
    evaluation_config = EvaluationConfig(
        workbook_path=config.workbook_path,
        sheet_name=config.sheet_name,
        output_dir=config.deterministic_output_dir,
        generate_plots=False,
        mark_uncertainty_stale=False,
    )
    inputs["specs"] = build_model_registry(evaluation_config)
    uncertainty_config = UncertaintyConfig(
        deterministic_output_dir=config.deterministic_output_dir,
        output_dir=config.uncertainty_output_dir,
        final_selection_path=(
            config.deterministic_output_dir / "final_model_selection_with_deep.csv"
        ),
        random_seed=config.random_seed,
        generate_plots=False,
    )
    inputs["selected_validation_points"] = build_selected_validation_predictions(
        inputs["selection"], uncertainty_config
    )
    return inputs


def build_base_caches(
    inputs: Mapping[str, object], config: RevisionConfig
) -> Dict[str, object]:
    """Build aligned point, interval, and centered paired-error inputs."""

    target_df = inputs["target_df"]
    selection = inputs["selection"]
    origins = monthly_backtest_origins(FINAL_TEST_START, FINAL_TEST_END)
    legacy_point_path = config.legacy_output_dir / "rolling_locked_point_forecasts.csv"
    if legacy_point_path.exists():
        locked = pd.read_csv(
            legacy_point_path,
            parse_dates=[
                "forecast_origin",
                "forecast_date",
                "training_start",
                "training_end",
            ],
        )
        locked = locked.loc[
            locked["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
        ].copy()
    else:
        rows: List[Dict[str, object]] = []
        for origin in origins:
            dates = forecast_dates_from_origin(origin, FINAL_TEST_END, 9)
            for target in (AVAILABLE_TARGET, OCCUPIED_TARGET):
                base_model = _selected_base_model(selection, target)
                values, metadata = _recursive_target_model_forecast(
                    target_df,
                    target,
                    base_model,
                    origin,
                    dates,
                    None,
                    config.random_seed,
                )
                selected_model = str(
                    selection.loc[
                        selection["target"].eq(target), "selected_model"
                    ].iloc[0]
                )
                for horizon_step, (date, value) in enumerate(
                    zip(dates, values), start=1
                ):
                    rows.append(
                        {
                            "forecast_origin": origin,
                            "forecast_date": date,
                            "horizon_step": horizon_step,
                            "target": target,
                            "prediction": float(value),
                            "selected_model": selected_model,
                            "feature_mode": "target_only_features",
                            "training_start": metadata["training_start"],
                            "training_end": metadata["training_end"],
                            "training_observations": metadata["training_observations"],
                            "history_window": "all_available",
                            "future_actual_used": False,
                        }
                    )
        locked = pd.DataFrame(rows)
    seasonal = build_reactive_forecast_cache(
        target_df, origins, FINAL_TEST_END, horizon=9
    )
    last_value = build_last_value_forecast_cache(
        target_df, origins, FINAL_TEST_END, horizon=9
    )

    legacy_interval_path = config.legacy_output_dir / "rolling_uncertainty_inputs.csv"
    if legacy_interval_path.exists():
        interval = pd.read_csv(
            legacy_interval_path,
            parse_dates=[
                "forecast_origin",
                "forecast_date",
                "calibration_cutoff",
                "maximum_calibration_outcome_date",
            ],
        )
    else:
        legacy_config = OptimizationEvaluationConfig(
            workbook_path=config.workbook_path,
            sheet_name=config.sheet_name,
            deterministic_output_dir=config.deterministic_output_dir,
            uncertainty_output_dir=config.uncertainty_output_dir,
            output_dir=config.legacy_output_dir,
            generate_plots=False,
        )
        cqr = build_cqr_raw_forecast_cache(
            target_df,
            origins,
            FINAL_TEST_END,
            AVAILABLE_TARGET,
            (0.90, 0.95),
            config.random_seed,
            9,
        )
        interval = build_rolling_interval_cache(
            target_df,
            locked,
            inputs["selected_validation_points"],
            inputs["uncertainty_selection"],
            inputs["cqr_validation"],
            cqr,
            legacy_config,
        )

    residuals = build_paired_development_residual_paths(
        inputs["selected_validation_points"]
    )
    for column in ("available_error", "occupied_error"):
        residuals[f"centered_{column}"] = residuals[column] - residuals.groupby(
            "horizon_step"
        )[column].transform("mean")
    centered_means = residuals.groupby("horizon_step")[[
        "centered_available_error",
        "centered_occupied_error",
    ]].mean()
    if not np.allclose(centered_means.to_numpy(), 0.0, atol=1e-10):
        raise AssertionError("Centered scenario errors do not average to zero by horizon.")

    return {
        "last_value_naive": last_value,
        "seasonal_naive": seasonal,
        "locked_point_forecast": locked,
        "interval": interval,
        "centered_residual_paths": residuals,
    }


def _point_path(
    cache: pd.DataFrame,
    origin: pd.Timestamp,
    horizon: int,
    targets: Sequence[str] = (AVAILABLE_TARGET, OCCUPIED_TARGET),
) -> pd.DataFrame:
    result = cache.loc[
        cache["forecast_origin"].eq(pd.Timestamp(origin))
        & cache["target"].isin(targets)
        & cache["horizon_step"].le(horizon)
    ].copy()
    expected_dates = forecast_dates_from_origin(origin, FINAL_TEST_END, horizon)
    if set(result["forecast_date"]) != set(expected_dates):
        raise AssertionError("Point path does not contain the exact requested dates.")
    if result.duplicated(["forecast_date", "target"]).any():
        raise AssertionError("Point path has duplicate target-date keys.")
    return result.sort_values(["forecast_date", "target"])


def build_centered_scenarios(
    locked_cache: pd.DataFrame,
    residual_paths: pd.DataFrame,
    origin: pd.Timestamp,
    horizon: int,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Create seven equally weighted centered paired paths around locked points."""

    point = _point_path(locked_cache, origin, horizon)
    wide = point.pivot(
        index="forecast_date", columns="target", values="prediction"
    ).sort_index()
    rows: List[Dict[str, object]] = []
    diagnostics: List[Dict[str, object]] = []
    folds = list(residual_paths["fold_id"].drop_duplicates())
    for fold_id in folds:
        errors = residual_paths.loc[
            residual_paths["fold_id"].eq(fold_id)
            & residual_paths["horizon_step"].le(horizon)
        ].sort_values("horizon_step")
        for horizon_step, (month, point_row) in enumerate(wide.iterrows(), start=1):
            error = errors.loc[errors["horizon_step"].eq(horizon_step)].iloc[0]
            available_preclip = float(point_row[AVAILABLE_TARGET]) + float(
                error["centered_available_error"]
            )
            occupied_preclip = float(point_row[OCCUPIED_TARGET]) + float(
                error["centered_occupied_error"]
            )
            rows.append(
                {
                    "scenario_id": f"PATH_{fold_id}",
                    "sampled_development_fold": fold_id,
                    "forecast_month": month,
                    "horizon_step": horizon_step,
                    "available_capacity": max(available_preclip, 0.0),
                    "occupied_demand": max(occupied_preclip, 0.0),
                    "available_preclip": available_preclip,
                    "occupied_preclip": occupied_preclip,
                    "point_available": float(point_row[AVAILABLE_TARGET]),
                    "point_occupied": float(point_row[OCCUPIED_TARGET]),
                    "probability": 1.0 / len(folds),
                    "preserves_cross_target_dependence": True,
                    "preserves_temporal_dependence": True,
                    "final_test_used_for_scenario_generation": False,
                }
            )
    scenarios = pd.DataFrame(rows)
    for horizon_step, group in scenarios.groupby("horizon_step"):
        weights = group["probability"].to_numpy(dtype=float)
        for target, scenario_column, point_column, preclip_column in (
            (AVAILABLE_TARGET, "available_capacity", "point_available", "available_preclip"),
            (OCCUPIED_TARGET, "occupied_demand", "point_occupied", "occupied_preclip"),
        ):
            point_value = float(group[point_column].iloc[0])
            preclip_mean = float(np.average(group[preclip_column], weights=weights))
            postclip_mean = float(np.average(group[scenario_column], weights=weights))
            if abs(preclip_mean - point_value) > 1e-8:
                raise AssertionError("Centered pre-clipping scenario mean changed the point forecast.")
            diagnostics.append(
                {
                    "forecast_origin": origin,
                    "horizon_step": horizon_step,
                    "target": target,
                    "point_forecast": point_value,
                    "preclip_scenario_mean": preclip_mean,
                    "postclip_scenario_mean": postclip_mean,
                    "clipping_mean_effect": postclip_mean - preclip_mean,
                    "scenario_count": len(folds),
                    "probability_sum": float(
                        group.groupby("scenario_id")["probability"].first().sum()
                    ),
                }
            )
    solver_frame = scenarios[[
        "scenario_id",
        "forecast_month",
        "horizon_step",
        "available_capacity",
        "occupied_demand",
        "probability",
        "sampled_development_fold",
    ]].copy()
    return solver_frame, pd.DataFrame(diagnostics)


def _interval_path(
    interval_cache: pd.DataFrame,
    origin: pd.Timestamp,
    horizon: int,
    level: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DatetimeIndex]:
    subset = interval_cache.loc[
        interval_cache["forecast_origin"].eq(pd.Timestamp(origin))
        & np.isclose(interval_cache["nominal_level"], level)
        & interval_cache["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
        & interval_cache["horizon_step"].le(horizon)
    ].copy()
    available = subset.loc[subset["target"].eq(AVAILABLE_TARGET)].sort_values(
        "horizon_step"
    )
    occupied = subset.loc[subset["target"].eq(OCCUPIED_TARGET)].sort_values(
        "horizon_step"
    )
    if not available["forecast_date"].reset_index(drop=True).equals(
        occupied["forecast_date"].reset_index(drop=True)
    ):
        raise AssertionError("Interval target-date paths do not align.")
    return (
        available["lower"].to_numpy(dtype=float),
        occupied["upper"].to_numpy(dtype=float),
        available["point_forecast"].to_numpy(dtype=float),
        occupied["point_forecast"].to_numpy(dtype=float),
        pd.DatetimeIndex(available["forecast_date"]),
    )


def run_revised_policy_backtest(
    policy: str,
    planning_horizon_months: int,
    reoptimization_frequency_months: int,
    rho_max: float,
    costs: CostParameters,
    target_df: pd.DataFrame,
    caches: Mapping[str, object],
    policy_cache_key: Optional[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run one fair receding-horizon policy with separate horizon and update controls."""

    if planning_horizon_months not in PLANNING_HORIZONS:
        raise ValueError("planning_horizon_months must be 1, 3, 6, or 9.")
    if reoptimization_frequency_months not in UPDATE_FREQUENCIES:
        raise ValueError("reoptimization_frequency_months must be 1, 3, or 6.")
    if reoptimization_frequency_months > planning_horizon_months:
        raise ValueError("Update frequency cannot exceed the planning horizon.")
    if not 0 < rho_max < 1:
        raise ValueError("rho_max must lie in (0, 1).")

    initial_capacity = 0.0
    origin = FINAL_TEST_START - pd.offsets.MonthBegin(1)
    decision_frames: List[pd.DataFrame] = []
    realized_frames: List[pd.DataFrame] = []
    timeline_rows: List[Dict[str, object]] = []
    scenario_rows: List[pd.DataFrame] = []
    update_index = 0
    while origin < FINAL_TEST_END:
        update_index += 1
        dates = forecast_dates_from_origin(
            origin, FINAL_TEST_END, planning_horizon_months
        )
        if dates.empty:
            break
        point_key = policy_cache_key or policy
        uses_future_actuals = policy == "perfect_information_oracle"
        stochastic = policy == "stochastic_scenarios"

        if policy in caches or policy_cache_key is not None:
            path = _point_path(caches[point_key], origin, len(dates))
            wide = path.pivot(
                index="forecast_date", columns="target", values="prediction"
            ).sort_index()
            available = wide[AVAILABLE_TARGET].to_numpy(dtype=float)
            occupied = wide[OCCUPIED_TARGET].to_numpy(dtype=float)
            point_available = available.copy()
            point_occupied = occupied.copy()
            input_source = point_key
        elif policy in {"moderate_90", "conservative_95"}:
            level = 0.90 if policy == "moderate_90" else 0.95
            available, occupied, point_available, point_occupied, interval_dates = (
                _interval_path(caches["interval"], origin, len(dates), level)
            )
            if not interval_dates.equals(dates):
                raise AssertionError("Interval dates differ from planning dates.")
            input_source = f"audited_{int(level * 100)}_percent_bounds"
        elif stochastic:
            path = _point_path(caches["locked_point_forecast"], origin, len(dates))
            wide = path.pivot(
                index="forecast_date", columns="target", values="prediction"
            ).sort_index()
            point_available = wide[AVAILABLE_TARGET].to_numpy(dtype=float)
            point_occupied = wide[OCCUPIED_TARGET].to_numpy(dtype=float)
            scenarios, scenario_diagnostics = build_centered_scenarios(
                caches["locked_point_forecast"],
                caches["centered_residual_paths"],
                origin,
                len(dates),
            )
            available = point_available.copy()
            occupied = point_occupied.copy()
            scenario_diagnostics["policy"] = policy
            scenario_rows.append(scenario_diagnostics)
            input_source = "seven_equal_centered_paired_development_paths"
        elif uses_future_actuals:
            available = target_df.loc[dates, AVAILABLE_TARGET].to_numpy(dtype=float)
            occupied = target_df.loc[dates, OCCUPIED_TARGET].to_numpy(dtype=float)
            point_available = available.copy()
            point_occupied = occupied.copy()
            input_source = "actual_future_values_oracle_only"
        else:
            raise KeyError(policy)

        if stochastic:
            plan, _, metadata = solve_stochastic_capacity_plan(
                dates,
                scenarios,
                rho_max,
                costs,
                initial_capacity=initial_capacity,
                cvar_weight=0.0,
                policy=policy,
            )
            plan["scenario_count"] = scenarios["scenario_id"].nunique()
        else:
            plan, metadata = solve_deterministic_capacity_plan(
                dates,
                available,
                occupied,
                rho_max,
                costs,
                initial_capacity=initial_capacity,
                policy=policy,
            )
        plan["point_available_forecast"] = point_available
        plan["point_occupied_forecast"] = point_occupied
        plan["input_source"] = input_source
        plan["uses_future_actuals"] = uses_future_actuals
        plan["forecast_origin"] = origin
        plan["decision_information_cutoff"] = origin
        plan["update_index"] = update_index
        plan["planning_horizon_months"] = planning_horizon_months
        plan["reoptimization_frequency_months"] = reoptimization_frequency_months
        plan["planned_horizon_months"] = len(plan)
        segment_length = min(reoptimization_frequency_months, len(plan))
        plan["implemented_segment_months"] = segment_length
        plan["implemented"] = plan["horizon_step"].le(segment_length)
        plan["oracle_benchmark_only"] = uses_future_actuals
        plan["cost_parameters_validated"] = costs.validated
        plan["los_added_to_occupied_census"] = False
        plan["cvar_enabled"] = False
        plan["run_id"] = (
            f"{policy}_h{planning_horizon_months}_f{reoptimization_frequency_months}_"
            f"rho{rho_max:.2f}_{costs.parameter_set_id}_{origin:%Y%m}"
        )
        implemented = plan.loc[plan["implemented"]].copy()
        if len(implemented) != segment_length:
            raise AssertionError("The requested fixed decision segment was not implemented.")
        realized = evaluate_implemented_decisions(implemented, target_df, costs)
        realized["planning_horizon_months"] = planning_horizon_months
        realized["reoptimization_frequency_months"] = reoptimization_frequency_months
        realized["shortage_bed_months"] = realized["realized_shortage_bed_months"]
        realized["unused_bed_months"] = realized["realized_unused_bed_months"]
        realized["operational_cost"] = (
            realized["active_extra_capacity_cost"]
            + realized["addition_cost"]
            + realized["removal_cost"]
            + realized["capacity_change_cost"]
        )
        decision_frames.append(plan)
        realized_frames.append(realized)

        next_origin = origin + pd.DateOffset(months=reoptimization_frequency_months)
        reveal_date = min(
            pd.Timestamp(next_origin), FINAL_TEST_END + pd.offsets.MonthBegin(1)
        )
        for row in implemented.itertuples():
            timeline_rows.append(
                {
                    "policy": policy,
                    "origin_date": origin,
                    "forecast_horizon": planning_horizon_months,
                    "update_frequency": reoptimization_frequency_months,
                    "decision_month": pd.Timestamp(row.forecast_month),
                    "information_cutoff": origin,
                    "implemented_before_next_update": True,
                    "actual_revealed_date": reveal_date,
                    "leakage_flag": uses_future_actuals,
                    "oracle_benchmark_only": uses_future_actuals,
                    "rho_max": rho_max,
                    "parameter_set_id": costs.parameter_set_id,
                }
            )
        initial_capacity = float(
            implemented.sort_values("forecast_month")[
                "additional_capacity_active"
            ].iloc[-1]
        )
        origin = pd.Timestamp(next_origin)

    decisions = pd.concat(decision_frames, ignore_index=True)
    realized = pd.concat(realized_frames, ignore_index=True)
    timeline = pd.DataFrame(timeline_rows)
    scenario_diagnostics = (
        pd.concat(scenario_rows, ignore_index=True)
        if scenario_rows
        else pd.DataFrame()
    )
    expected_months = pd.date_range(FINAL_TEST_START, FINAL_TEST_END, freq="MS")
    if not pd.DatetimeIndex(realized["forecast_month"]).equals(expected_months):
        raise AssertionError("A policy did not implement each sealed test month exactly once.")
    if realized["forecast_month"].duplicated().any():
        raise AssertionError("A policy implemented duplicate decisions for a test month.")
    if policy != "perfect_information_oracle" and decisions["uses_future_actuals"].any():
        raise AssertionError("An implementable policy used future outcomes.")
    return decisions, realized, timeline, scenario_diagnostics


def summarize_policy_run(realized: pd.DataFrame) -> Dict[str, object]:
    """Return cost, shortage, service, capacity, and volatility diagnostics."""

    total_required = float(realized["actual_required_capacity"].sum())
    shortage = float(realized["realized_shortage_bed_months"].sum())
    active = realized["additional_capacity_active"].to_numpy(dtype=float)
    previous = np.concatenate([[0.0], active[:-1]])
    volatility = float(np.abs(active - previous).sum())
    return {
        "policy": str(realized["policy"].iloc[0]),
        "planning_horizon_months": int(realized["planning_horizon_months"].iloc[0]),
        "reoptimization_frequency_months": int(
            realized["reoptimization_frequency_months"].iloc[0]
        ),
        "rho_max": float(realized["rho_max"].iloc[0]),
        "parameter_set_id": str(realized["parameter_set_id"].iloc[0]),
        "total_realized_cost": float(realized["total_realized_cost"].sum()),
        "operational_cost": float(realized["operational_cost"].sum()),
        "active_extra_capacity_cost": float(
            realized["active_extra_capacity_cost"].sum()
        ),
        "addition_cost": float(realized["addition_cost"].sum()),
        "removal_cost": float(realized["removal_cost"].sum()),
        "capacity_change_cost": float(realized["capacity_change_cost"].sum()),
        "shortage_cost": float(realized["shortage_cost"].sum()),
        "shortage_bed_months": shortage,
        "shortage_months": int(realized["shortage_month"].sum()),
        "extra_staffed_bed_months": float(
            realized["additional_capacity_active"].sum()
        ),
        "peak_additional_capacity": float(
            realized["additional_capacity_active"].max()
        ),
        "unused_bed_months": float(realized["realized_unused_bed_months"].sum()),
        "service_level": 1.0 - shortage / total_required,
        "decision_volatility": volatility,
        "cost_parameters_validated": bool(
            realized["cost_parameters_validated"].iloc[0]
        ),
    }


def attach_oracle_regret(summary: pd.DataFrame) -> pd.DataFrame:
    keys = [
        "planning_horizon_months",
        "reoptimization_frequency_months",
        "rho_max",
        "parameter_set_id",
    ]
    oracle = summary.loc[
        summary["policy"].eq("perfect_information_oracle"),
        keys + ["total_realized_cost"],
    ].rename(columns={"total_realized_cost": "oracle_cost"})
    result = summary.merge(oracle, on=keys, how="left", validate="many_to_one")
    result["regret"] = result["total_realized_cost"] - result["oracle_cost"]
    result.loc[result["regret"].abs() < 1e-5, "regret"] = 0.0
    if (result["regret"] < -1e-4).any():
        raise AssertionError("An implementable policy is cheaper than the identical oracle.")
    return result


def build_horizon_update_factorial(
    target_df: pd.DataFrame,
    caches: Mapping[str, object],
    costs: CostParameters,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate valid planning-horizon by update-frequency combinations."""

    summaries: List[Dict[str, object]] = []
    decisions: List[pd.DataFrame] = []
    realized_rows: List[pd.DataFrame] = []
    timelines: List[pd.DataFrame] = []
    for horizon in PLANNING_HORIZONS:
        for frequency in UPDATE_FREQUENCIES:
            if frequency > horizon:
                continue
            for policy in CORE_POLICIES:
                decision, realized, timeline, _ = run_revised_policy_backtest(
                    policy,
                    horizon,
                    frequency,
                    0.90,
                    costs,
                    target_df,
                    caches,
                )
                summaries.append(summarize_policy_run(realized))
                decisions.append(decision)
                realized_rows.append(realized)
                timelines.append(timeline)
    summary = attach_oracle_regret(pd.DataFrame(summaries))
    return (
        summary,
        pd.concat(decisions, ignore_index=True),
        pd.concat(realized_rows, ignore_index=True),
        pd.concat(timelines, ignore_index=True),
    )


def build_forecast_alignment_audit(
    target_df: pd.DataFrame,
    caches: Mapping[str, object],
) -> pd.DataFrame:
    """Create one fail-fast origin-target-date alignment table."""

    rows: List[Dict[str, object]] = []
    for method in ("last_value_naive", "seasonal_naive", "locked_point_forecast"):
        cache = caches[method]
        subset = cache.loc[cache["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])]
        keys = ["forecast_origin", "forecast_date", "target"]
        if subset.duplicated(keys).any():
            raise AssertionError(f"Duplicate point keys in {method}.")
        for row in subset.itertuples():
            rows.append(
                {
                    "input_type": "point_forecast",
                    "policy_or_method": method,
                    "forecast_date": row.forecast_date,
                    "forecast_origin": row.forecast_origin,
                    "training_cutoff": row.training_end,
                    "target": row.target,
                    "model": row.selected_model,
                    "feature_mode": row.feature_mode,
                    "prediction": row.prediction,
                    "actual": float(target_df.loc[row.forecast_date, row.target]),
                    "horizon_step": row.horizon_step,
                    "nominal_level": np.nan,
                    "lower": np.nan,
                    "upper": np.nan,
                    "scenario_id": "",
                    "future_actual_used": False,
                    "alignment_check": True,
                }
            )
    interval = caches["interval"].loc[
        caches["interval"]["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
    ].copy()
    interval_keys = ["forecast_origin", "forecast_date", "target", "nominal_level"]
    if interval.duplicated(interval_keys).any():
        raise AssertionError("Duplicate interval origin/date/target/level keys.")
    for row in interval.itertuples():
        rows.append(
            {
                "input_type": "uncertainty_interval",
                "policy_or_method": row.selected_uncertainty_method,
                "forecast_date": row.forecast_date,
                "forecast_origin": row.forecast_origin,
                "training_cutoff": row.calibration_cutoff,
                "target": row.target,
                "model": "locked_point_plus_selected_uncertainty",
                "feature_mode": "target_specific",
                "prediction": row.point_forecast,
                "actual": float(target_df.loc[row.forecast_date, row.target]),
                "horizon_step": row.horizon_step,
                "nominal_level": row.nominal_level,
                "lower": row.lower,
                "upper": row.upper,
                "scenario_id": "",
                "future_actual_used": False,
                "alignment_check": row.lower <= row.point_forecast <= row.upper,
            }
        )
    first_origin = FINAL_TEST_START - pd.offsets.MonthBegin(1)
    scenarios, _ = build_centered_scenarios(
        caches["locked_point_forecast"], caches["centered_residual_paths"], first_origin, 9
    )
    long = scenarios.melt(
        id_vars=["scenario_id", "forecast_month", "probability"],
        value_vars=["available_capacity", "occupied_demand"],
        var_name="scenario_target",
        value_name="prediction",
    )
    long["target"] = long["scenario_target"].map(
        {
            "available_capacity": AVAILABLE_TARGET,
            "occupied_demand": OCCUPIED_TARGET,
        }
    )
    for row in long.itertuples():
        horizon_step = _month_distance(first_origin, pd.Timestamp(row.forecast_month))
        rows.append(
            {
                "input_type": "stochastic_scenario",
                "policy_or_method": "stochastic_scenarios",
                "forecast_date": row.forecast_month,
                "forecast_origin": first_origin,
                "training_cutoff": first_origin,
                "target": row.target,
                "model": "locked_point_plus_centered_paired_error",
                "feature_mode": "paired_empirical_path",
                "prediction": row.prediction,
                "actual": float(target_df.loc[row.forecast_month, row.target]),
                "horizon_step": horizon_step,
                "nominal_level": np.nan,
                "lower": np.nan,
                "upper": np.nan,
                "scenario_id": row.scenario_id,
                "future_actual_used": False,
                "alignment_check": True,
            }
        )
    result = pd.DataFrame(rows)
    if not result["alignment_check"].all():
        raise AssertionError("Forecast alignment audit contains a failed row.")
    if not (pd.to_datetime(result["training_cutoff"]) <= result["forecast_origin"]).all():
        raise AssertionError("An aligned input trained after its origin.")
    return result.sort_values(
        ["input_type", "policy_or_method", "forecast_origin", "forecast_date", "target", "scenario_id"]
    ).reset_index(drop=True)


def build_realized_shortage_audit(realized: pd.DataFrame) -> pd.DataFrame:
    """Export monthly actual-outcome shortage calculations and equality checks."""

    result = pd.DataFrame(
        {
            "policy": realized["policy"],
            "origin": realized["forecast_origin"],
            "month": realized["forecast_month"],
            "actual_available": realized["actual_available_capacity"],
            "actual_occupied": realized["actual_occupied_demand"],
            "rho_max": realized["rho_max"],
            "required_capacity": realized["actual_required_capacity"],
            "implemented_extra_capacity": realized["additional_capacity_active"],
            "realized_shortage": realized["realized_shortage_bed_months"],
            "planning_horizon_months": realized["planning_horizon_months"],
            "update_frequency": realized["reoptimization_frequency_months"],
            "parameter_set_id": realized["parameter_set_id"],
        }
    )
    expected = np.maximum(
        result["actual_occupied"] / result["rho_max"]
        - result["actual_available"]
        - result["implemented_extra_capacity"],
        0.0,
    )
    expected = np.where(expected < 1e-3, 0.0, expected)
    result["formula_check"] = np.isclose(
        result["realized_shortage"], expected, atol=1e-5
    )
    if not result["formula_check"].all():
        raise AssertionError("Realized shortage differs from the documented actual formula.")
    return result


def build_cost_formula_audit(costs: CostParameters) -> pd.DataFrame:
    """Document exact cost units, multiplication, and non-duplication checks."""

    rows = [
        ("active_extra_capacity", costs.active_staffed_bed_cost, "GBP", "per active bed-equivalent-month", "active_extra_capacity * unit_cost", "every active month", True, False),
        ("capacity_addition", costs.capacity_addition_cost, "GBP", "per bed-equivalent added", "capacity_added * unit_cost", "once in addition month", True, False),
        ("capacity_removal", costs.capacity_removal_cost, "GBP", "per bed-equivalent removed", "capacity_removed * unit_cost", "once in removal month", True, False),
        ("capacity_change_penalty", costs.capacity_change_penalty, "GBP", "per bed-equivalent changed", "(capacity_added + capacity_removed) * unit_cost", "once in change month", True, False),
        ("shortage", costs.shortage_penalty, "GBP", "per unmet occupancy-adjusted bed-equivalent-month", "realized_shortage * unit_cost", "once per realized month", True, False),
        ("unused_capacity", 0.0, "GBP", "not charged", "0 * realized_unused", "not applicable", False, False),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "cost_term",
            "unit_cost",
            "currency",
            "unit",
            "formula",
            "application_frequency",
            "multiplied_by_capacity_units",
            "double_counted",
        ],
    ).assign(
        output_reporting_unit="GBP",
        parameter_set_id=costs.parameter_set_id,
        stakeholder_validated=costs.validated,
        audit_note="Arithmetic is internally consistent; economic values remain illustrative.",
    )


def build_baseline_comparison(factorial: pd.DataFrame) -> pd.DataFrame:
    """Return the primary nine-month-horizon monthly-update policy comparison."""

    return factorial.loc[
        factorial["planning_horizon_months"].eq(9)
        & factorial["reoptimization_frequency_months"].eq(1)
    ].sort_values(["total_realized_cost", "shortage_bed_months"]).reset_index(drop=True)


def _selected_base_model(selection: pd.DataFrame, target: str) -> str:
    model = str(
        selection.loc[selection["target"].eq(target), "selected_model"].iloc[0]
    )
    for base in ("RandomForestRegressor", "ExtraTreesRegressor", "Ridge"):
        if model.startswith(base):
            return base
    raise ValueError(f"History sensitivity does not support locked model {model}.")


def _recursive_target_model_forecast(
    target_df: pd.DataFrame,
    target: str,
    base_model: str,
    origin: pd.Timestamp,
    forecast_dates: pd.DatetimeIndex,
    history_window: Optional[int],
    seed: int,
) -> Tuple[np.ndarray, Dict[str, object]]:
    """Refit one canonical target-only model with a raw outcome-history window."""

    observed = target_df.loc[:origin, [target]].copy()
    features = build_target_only_features(observed[target]).dropna()
    eligible = features.index[features.index <= origin]
    if history_window is not None:
        eligible = eligible[-history_window:]
    if len(eligible) < 12:
        raise ValueError("At least 12 supervised monthly outcomes are required.")
    estimator = make_ml_estimator(base_model, seed)
    estimator.fit(features.loc[eligible], observed.loc[eligible, target])
    simulated = observed.copy()
    predictions: List[float] = []
    for forecast_date in forecast_dates:
        row = build_recursive_feature_row(
            target,
            simulated,
            forecast_date,
            "target_only_features",
        ).loc[:, features.columns]
        prediction = max(float(estimator.predict(row)[0]), 0.0)
        predictions.append(prediction)
        simulated.loc[forecast_date, target] = prediction
    return np.asarray(predictions, dtype=float), {
        "training_start": eligible.min(),
        "training_end": eligible.max(),
        "training_observations": len(eligible),
        "lag_source_start": observed.index.min(),
    }


def build_locked_history_cache(
    target_df: pd.DataFrame,
    selection: pd.DataFrame,
    base_locked_cache: pd.DataFrame,
    origins: Sequence[pd.Timestamp],
    history_window: Optional[int],
    seed: int,
    horizon: int = 9,
) -> pd.DataFrame:
    """Build occupied/available locked forecasts for one raw outcome window."""

    if history_window is None:
        return base_locked_cache.loc[
            base_locked_cache["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
        ].copy()
    rows: List[Dict[str, object]] = []
    for origin in pd.DatetimeIndex(origins):
        dates = forecast_dates_from_origin(origin, FINAL_TEST_END, horizon)
        for target in (AVAILABLE_TARGET, OCCUPIED_TARGET):
            base_model = _selected_base_model(selection, target)
            values, metadata = _recursive_target_model_forecast(
                target_df,
                target,
                base_model,
                origin,
                dates,
                history_window,
                seed,
            )
            selected_model = str(
                selection.loc[
                    selection["target"].eq(target), "selected_model"
                ].iloc[0]
            )
            for horizon_step, (month, value) in enumerate(
                zip(dates, values), start=1
            ):
                rows.append(
                    {
                        "forecast_origin": origin,
                        "forecast_date": month,
                        "horizon_step": horizon_step,
                        "target": target,
                        "prediction": float(value),
                        "selected_model": selected_model,
                        "feature_mode": "target_only_features",
                        "training_start": metadata["training_start"],
                        "training_end": metadata["training_end"],
                        "training_observations": metadata["training_observations"],
                        "history_window": str(history_window),
                        "future_actual_used": False,
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["forecast_origin", "forecast_date", "target"]
    ).reset_index(drop=True)


def build_history_window_decision_results(
    target_df: pd.DataFrame,
    selection: pd.DataFrame,
    caches: Mapping[str, object],
    costs: CostParameters,
    seed: int,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame], pd.DataFrame]:
    """Evaluate 24, 36, and all history; retain 12 months as exploratory."""

    origins = monthly_backtest_origins(FINAL_TEST_START, FINAL_TEST_END)
    history_caches: Dict[str, pd.DataFrame] = {}
    result_rows: List[Dict[str, object]] = []
    realized_frames: List[pd.DataFrame] = []
    for window in HISTORY_WINDOWS:
        label = _history_label(window)
        cache_key = f"locked_history_{label}"
        cache = build_locked_history_cache(
            target_df,
            selection,
            caches["locked_point_forecast"],
            origins,
            window,
            seed,
            horizon=9,
        )
        history_caches[cache_key] = cache
        run_caches = dict(caches)
        run_caches[cache_key] = cache
        _, realized, _, _ = run_revised_policy_backtest(
            cache_key,
            9,
            1,
            0.90,
            costs,
            target_df,
            run_caches,
        )
        realized_frames.append(realized)
        summary = summarize_policy_run(realized)
        implemented = realized.sort_values("forecast_month")
        predicted_gap = (
            implemented["point_occupied_forecast"] / 0.90
            - implemented["point_available_forecast"]
        )
        actual_gap = (
            implemented["actual_occupied_demand"] / 0.90
            - implemented["actual_available_capacity"]
        )
        for target, prediction_column, actual_column in (
            (
                AVAILABLE_TARGET,
                "point_available_forecast",
                "actual_available_capacity",
            ),
            (
                OCCUPIED_TARGET,
                "point_occupied_forecast",
                "actual_occupied_demand",
            ),
        ):
            error = (
                implemented[prediction_column] - implemented[actual_column]
            ).to_numpy(dtype=float)
            summary[f"{target}_MAE"] = float(np.mean(np.abs(error)))
            summary[f"{target}_bias_prediction_minus_actual"] = float(
                np.mean(error)
            )
        summary["capacity_gap_MAE"] = float(
            np.mean(np.abs(predicted_gap - actual_gap))
        )
        summary["history_window"] = label
        summary["exploratory_only"] = window == 12
        summary["selected_using_final_test"] = False
        summary["interpretation"] = (
            "Exploratory only: too few supervised outcomes for reliable ML inference."
            if window == 12
            else "Sealed-test sensitivity only; not used to select a history window."
        )
        result_rows.append(summary)
    result = pd.DataFrame(result_rows)
    eligible = ~result["exploratory_only"]
    result.loc[eligible, "policy_cost_rank"] = result.loc[eligible, "total_realized_cost"].rank(
        method="min"
    )
    return result, history_caches, pd.concat(realized_frames, ignore_index=True)


def capacity_gap_series(target_df: pd.DataFrame, rho_max: float) -> pd.Series:
    gap = target_df[OCCUPIED_TARGET] / rho_max - target_df[AVAILABLE_TARGET]
    gap.name = "occupancy_adjusted_capacity_gap"
    return gap.astype(float)


def forecast_direct_gap(
    gap: pd.Series,
    origin: pd.Timestamp,
    horizon: int,
    model: str,
    seed: int,
) -> np.ndarray:
    """Forecast the signed optimization target without clipping negative headroom."""

    history = gap.loc[:origin].astype(float)
    dates = pd.date_range(origin + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if model == "last_value":
            return last_value_forecast(history, horizon)
        if model == "seasonal_naive":
            return seasonal_naive_forecast(history, horizon)
        if model == "ETS":
            return np.asarray(ets_forecast(history, horizon), dtype=float)
        if model not in {
            "Ridge",
            "RandomForestRegressor",
            "ExtraTreesRegressor",
            "XGBoost",
        }:
            raise KeyError(model)
        features = build_target_only_features(history).dropna()
        estimator = make_ml_estimator(model, seed)
        estimator.fit(features, history.loc[features.index])
        simulated = history.to_frame(name=gap.name)
        predictions: List[float] = []
        for date in dates:
            row = build_recursive_feature_row(
                gap.name,
                simulated,
                date,
                "target_only_features",
            ).loc[:, features.columns]
            prediction = float(estimator.predict(row)[0])
            predictions.append(prediction)
            simulated.loc[date, gap.name] = prediction
        return np.asarray(predictions, dtype=float)


def _locked_development_gap(
    selected_validation_points: pd.DataFrame,
    rho_max: float,
) -> pd.DataFrame:
    subset = selected_validation_points.loc[
        selected_validation_points["target"].isin(
            [AVAILABLE_TARGET, OCCUPIED_TARGET]
        )
    ].copy()
    actual = subset.pivot(
        index=["fold_id", "forecast_origin", "forecast_date", "horizon_step"],
        columns="target",
        values="actual",
    )
    prediction = subset.pivot(
        index=["fold_id", "forecast_origin", "forecast_date", "horizon_step"],
        columns="target",
        values="prediction",
    )
    result = pd.DataFrame(index=actual.index).reset_index()
    result["actual_gap"] = (
        actual[OCCUPIED_TARGET].to_numpy(dtype=float) / rho_max
        - actual[AVAILABLE_TARGET].to_numpy(dtype=float)
    )
    result["prediction"] = (
        prediction[OCCUPIED_TARGET].to_numpy(dtype=float) / rho_max
        - prediction[AVAILABLE_TARGET].to_numpy(dtype=float)
    )
    result["method"] = "separate_locked_targets"
    result["rho_max"] = rho_max
    return result


def _direct_candidate_development_predictions(
    target_df: pd.DataFrame,
    locked_gap: pd.DataFrame,
    rho_max: float,
    seed: int,
) -> pd.DataFrame:
    gap = capacity_gap_series(target_df, rho_max)
    rows: List[Dict[str, object]] = []
    origins = locked_gap[["fold_id", "forecast_origin"]].drop_duplicates()
    for model in DIRECT_GAP_MODELS:
        for record in origins.itertuples():
            values = forecast_direct_gap(
                gap, pd.Timestamp(record.forecast_origin), 9, model, seed
            )
            dates = pd.date_range(
                pd.Timestamp(record.forecast_origin) + pd.offsets.MonthBegin(1),
                periods=9,
                freq="MS",
            )
            for horizon_step, (date, prediction) in enumerate(
                zip(dates, values), start=1
            ):
                rows.append(
                    {
                        "fold_id": record.fold_id,
                        "forecast_origin": record.forecast_origin,
                        "forecast_date": date,
                        "horizon_step": horizon_step,
                        "actual_gap": float(gap.loc[date]),
                        "prediction": float(prediction),
                        "method": f"direct_{model}",
                        "rho_max": rho_max,
                    }
                )
    return pd.DataFrame(rows)


def _observable_prior_gap_errors(
    locked_gap: pd.DataFrame,
    origin: pd.Timestamp,
) -> pd.DataFrame:
    prior = locked_gap.loc[
        (locked_gap["forecast_origin"] < origin)
        & (locked_gap["forecast_date"] <= origin)
    ].copy()
    prior["error"] = prior["actual_gap"] - prior["prediction"]
    return prior.sort_values(["forecast_date", "forecast_origin", "horizon_step"])


def build_nested_bias_corrections(locked_gap: pd.DataFrame) -> pd.DataFrame:
    """Apply only residual corrections observable at each development origin."""

    rows: List[Dict[str, object]] = []
    for row in locked_gap.itertuples():
        prior = _observable_prior_gap_errors(
            locked_gap, pd.Timestamp(row.forecast_origin)
        )
        global_correction = float(prior["error"].mean()) if len(prior) else 0.0
        same_horizon = prior.loc[prior["horizon_step"].eq(row.horizon_step)]
        horizon_correction = (
            float(same_horizon["error"].mean())
            if len(same_horizon)
            else global_correction
        )
        recent = prior.tail(18)
        recent_correction = float(recent["error"].mean()) if len(recent) else 0.0
        for method, correction in (
            ("bias_global", global_correction),
            ("bias_horizon_specific", horizon_correction),
            ("bias_recent_18", recent_correction),
        ):
            rows.append(
                {
                    "fold_id": row.fold_id,
                    "forecast_origin": row.forecast_origin,
                    "forecast_date": row.forecast_date,
                    "horizon_step": row.horizon_step,
                    "actual_gap": row.actual_gap,
                    "prediction": row.prediction + correction,
                    "uncorrected_prediction": row.prediction,
                    "correction": correction,
                    "calibration_records": len(prior),
                    "method": method,
                    "rho_max": row.rho_max,
                    "final_test_used_for_correction": False,
                }
            )
    return pd.DataFrame(rows)


def select_bias_method(
    locked_gap: pd.DataFrame, corrected: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    uncorrected_mae = float(
        np.mean(np.abs(locked_gap["actual_gap"] - locked_gap["prediction"]))
    )
    rows.append(
        {
            "method": "uncorrected_locked",
            "development_MAE": uncorrected_mae,
            "rho_max": float(locked_gap["rho_max"].iloc[0]),
        }
    )
    for method, group in corrected.groupby("method"):
        rows.append(
            {
                "method": method,
                "development_MAE": float(
                    np.mean(np.abs(group["actual_gap"] - group["prediction"]))
                ),
                "rho_max": float(group["rho_max"].iloc[0]),
            }
        )
    summary = pd.DataFrame(rows).sort_values(["development_MAE", "method"])
    minimum = float(summary["development_MAE"].min())
    tied = summary.loc[summary["development_MAE"] <= minimum * 1.01].copy()
    simplicity = {
        "uncorrected_locked": 1,
        "bias_global": 2,
        "bias_recent_18": 3,
        "bias_horizon_specific": 4,
    }
    selected = tied.assign(
        simplicity_rank=tied["method"].map(simplicity)
    ).sort_values(["simplicity_rank", "development_MAE", "method"]).iloc[0]
    summary["selected_bias_method"] = summary["method"].eq(selected["method"])
    summary["selection_rule"] = (
        "Minimum nested development MAE; within 1%, prefer the simpler correction."
    )
    summary["final_test_used_for_selection"] = False
    return summary


def build_validation_ensemble(
    locked_gap: pd.DataFrame,
    direct_predictions: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    seasonal = direct_predictions.loc[
        direct_predictions["method"].eq("direct_seasonal_naive")
    ][
        ["fold_id", "forecast_origin", "forecast_date", "horizon_step", "prediction"]
    ].rename(columns={"prediction": "seasonal_prediction"})
    paired = locked_gap.merge(
        seasonal,
        on=["fold_id", "forecast_origin", "forecast_date", "horizon_step"],
        how="inner",
        validate="one_to_one",
    )
    weight_rows = []
    for weight_locked in np.linspace(0.0, 1.0, 21):
        prediction = (
            weight_locked * paired["prediction"]
            + (1.0 - weight_locked) * paired["seasonal_prediction"]
        )
        weight_rows.append(
            {
                "rho_max": float(paired["rho_max"].iloc[0]),
                "weight_locked": weight_locked,
                "weight_seasonal": 1.0 - weight_locked,
                "development_MAE": float(
                    np.mean(np.abs(paired["actual_gap"] - prediction))
                ),
                "final_test_used_for_weight_selection": False,
            }
        )
    weights = pd.DataFrame(weight_rows)
    minimum = float(weights["development_MAE"].min())
    tied = weights.loc[weights["development_MAE"] <= minimum * 1.01].copy()
    tied["distance_from_simple_half"] = (tied["weight_locked"] - 0.5).abs()
    selected = tied.sort_values(
        ["distance_from_simple_half", "development_MAE", "weight_locked"]
    ).iloc[0]
    weights["selected_weight"] = np.isclose(
        weights["weight_locked"], selected["weight_locked"]
    )
    weights["selection_rule"] = (
        "Minimum development gap MAE; within 1%, choose the weight closest to 0.5."
    )
    weight = float(selected["weight_locked"])
    ensemble = paired.copy()
    ensemble["prediction"] = (
        weight * ensemble["prediction"]
        + (1.0 - weight) * ensemble["seasonal_prediction"]
    )
    ensemble["method"] = "ensemble_locked_seasonal"
    ensemble["weight_locked"] = weight
    return weights, ensemble[
        [
            "fold_id",
            "forecast_origin",
            "forecast_date",
            "horizon_step",
            "actual_gap",
            "prediction",
            "method",
            "rho_max",
            "weight_locked",
        ]
    ]


def select_direct_gap_model(direct_predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    simplicity = {
        "direct_last_value": 1,
        "direct_seasonal_naive": 1,
        "direct_ETS": 2,
        "direct_Ridge": 3,
        "direct_RandomForestRegressor": 4,
        "direct_ExtraTreesRegressor": 4,
        "direct_XGBoost": 5,
    }
    for method, group in direct_predictions.groupby("method"):
        error = group["actual_gap"] - group["prediction"]
        rows.append(
            {
                "rho_max": float(group["rho_max"].iloc[0]),
                "method": method,
                "development_MAE": float(np.mean(np.abs(error))),
                "development_RMSE": float(np.sqrt(np.mean(error**2))),
                "development_bias_actual_minus_prediction": float(np.mean(error)),
                "simplicity_rank": simplicity[method],
                "successful_predictions": len(group),
            }
        )
    summary = pd.DataFrame(rows)
    minimum = float(summary["development_MAE"].min())
    tied = summary.loc[summary["development_MAE"] <= minimum * 1.01]
    selected = tied.sort_values(
        ["simplicity_rank", "development_RMSE", "development_MAE", "method"]
    ).iloc[0]
    summary["selected_direct_gap_model"] = summary["method"].eq(selected["method"])
    summary["selection_rule"] = (
        "Minimum development gap MAE; within 1%, prefer simplicity then RMSE."
    )
    summary["final_test_used_for_selection"] = False
    return summary.sort_values(["development_MAE", "method"]).reset_index(drop=True)


def _gap_path_to_point_cache(
    gap_paths: pd.DataFrame,
    reference_available_cache: pd.DataFrame,
    rho_max: float,
    method: str,
) -> pd.DataFrame:
    """Represent a direct gap exactly in the common occupied/available solver API."""

    available = reference_available_cache.loc[
        reference_available_cache["target"].eq(AVAILABLE_TARGET)
    ][["forecast_origin", "forecast_date", "horizon_step", "prediction"]].rename(
        columns={"prediction": "reference_available"}
    )
    merged = gap_paths.merge(
        available,
        on=["forecast_origin", "forecast_date", "horizon_step"],
        how="inner",
        validate="one_to_one",
    )
    rows = []
    for row in merged.itertuples():
        occupied = rho_max * (float(row.reference_available) + float(row.prediction))
        if occupied < 0:
            raise ValueError("Direct gap implies negative occupied demand.")
        common = {
            "forecast_origin": row.forecast_origin,
            "forecast_date": row.forecast_date,
            "horizon_step": row.horizon_step,
            "selected_model": method,
            "feature_mode": "direct_capacity_gap",
            "training_start": pd.NaT,
            "training_end": row.forecast_origin,
            "training_observations": np.nan,
            "history_window": "all_available",
            "future_actual_used": False,
        }
        rows.append(
            {
                **common,
                "target": AVAILABLE_TARGET,
                "prediction": float(row.reference_available),
            }
        )
        rows.append(
            {
                **common,
                "target": OCCUPIED_TARGET,
                "prediction": occupied,
            }
        )
    return pd.DataFrame(rows)


def build_direct_gap_test_cache(
    target_df: pd.DataFrame,
    reference_available_cache: pd.DataFrame,
    rho_max: float,
    model_method: str,
    seed: int,
) -> pd.DataFrame:
    gap = capacity_gap_series(target_df, rho_max)
    model = model_method.removeprefix("direct_")
    rows = []
    for origin in monthly_backtest_origins(FINAL_TEST_START, FINAL_TEST_END):
        dates = forecast_dates_from_origin(origin, FINAL_TEST_END, 9)
        values = forecast_direct_gap(gap, origin, len(dates), model, seed)
        for horizon_step, (date, prediction) in enumerate(
            zip(dates, values), start=1
        ):
            rows.append(
                {
                    "forecast_origin": origin,
                    "forecast_date": date,
                    "horizon_step": horizon_step,
                    "prediction": float(prediction),
                }
            )
    return _gap_path_to_point_cache(
        pd.DataFrame(rows), reference_available_cache, rho_max, model_method
    )


def _full_development_correction(
    locked_gap: pd.DataFrame,
    method: str,
    horizon_step: int,
) -> float:
    errors = (locked_gap["actual_gap"] - locked_gap["prediction"]).rename("error")
    table = locked_gap.assign(error=errors).sort_values(
        ["forecast_date", "forecast_origin", "horizon_step"]
    )
    if method == "uncorrected_locked":
        return 0.0
    if method == "bias_global":
        return float(table["error"].mean())
    if method == "bias_horizon_specific":
        subset = table.loc[table["horizon_step"].eq(horizon_step), "error"]
        return float(subset.mean())
    if method == "bias_recent_18":
        return float(table.tail(18)["error"].mean())
    raise KeyError(method)


def build_bias_corrected_test_cache(
    locked_cache: pd.DataFrame,
    locked_gap: pd.DataFrame,
    rho_max: float,
    selected_method: str,
) -> pd.DataFrame:
    rows = []
    for (origin, date, horizon_step), group in locked_cache.loc[
        locked_cache["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
    ].groupby(["forecast_origin", "forecast_date", "horizon_step"]):
        wide = group.set_index("target")["prediction"]
        uncorrected_gap = (
            float(wide[OCCUPIED_TARGET]) / rho_max
            - float(wide[AVAILABLE_TARGET])
        )
        correction = _full_development_correction(
            locked_gap, selected_method, int(horizon_step)
        )
        rows.append(
            {
                "forecast_origin": origin,
                "forecast_date": date,
                "horizon_step": horizon_step,
                "prediction": uncorrected_gap + correction,
                "correction": correction,
            }
        )
    return _gap_path_to_point_cache(
        pd.DataFrame(rows), locked_cache, rho_max, selected_method
    )


def build_ensemble_test_cache(
    locked_cache: pd.DataFrame,
    seasonal_cache: pd.DataFrame,
    weight_locked: float,
) -> pd.DataFrame:
    keys = ["forecast_origin", "forecast_date", "horizon_step", "target"]
    locked = locked_cache.loc[
        locked_cache["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
    ][keys + ["prediction"]].rename(columns={"prediction": "locked_prediction"})
    seasonal = seasonal_cache.loc[
        seasonal_cache["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
    ][keys + ["prediction"]].rename(columns={"prediction": "seasonal_prediction"})
    merged = locked.merge(seasonal, on=keys, validate="one_to_one")
    merged["prediction"] = (
        weight_locked * merged["locked_prediction"]
        + (1.0 - weight_locked) * merged["seasonal_prediction"]
    )
    merged["selected_model"] = "validation_weighted_locked_seasonal_ensemble"
    merged["feature_mode"] = "ensemble"
    merged["training_start"] = pd.NaT
    merged["training_end"] = merged["forecast_origin"]
    merged["training_observations"] = np.nan
    merged["history_window"] = "all_available"
    merged["future_actual_used"] = False
    return merged[
        keys
        + [
            "prediction",
            "selected_model",
            "feature_mode",
            "training_start",
            "training_end",
            "training_observations",
            "history_window",
            "future_actual_used",
        ]
    ]


def _evaluate_gap_predictions_on_development(
    predictions: pd.DataFrame,
    rho_max: float,
    costs: CostParameters,
) -> Tuple[float, float]:
    costs_by_fold = []
    shortage_by_fold = []
    for _, group in predictions.groupby("fold_id"):
        group = group.sort_values("horizon_step")
        dates = pd.DatetimeIndex(group["forecast_date"])
        offset = 200_000.0
        available = np.repeat(offset, len(group))
        occupied = rho_max * (offset + group["prediction"].to_numpy(dtype=float))
        plan, _ = solve_deterministic_capacity_plan(
            dates,
            available,
            occupied,
            rho_max,
            costs,
            initial_capacity=0.0,
            policy="development_gap_evaluation",
        )
        active = plan["additional_capacity_active"].to_numpy(dtype=float)
        actual_gap = group["actual_gap"].to_numpy(dtype=float)
        shortage = np.maximum(actual_gap - active, 0.0)
        operational = (
            costs.active_staffed_bed_cost * plan["additional_capacity_active"]
            + costs.capacity_addition_cost * plan["capacity_added"]
            + costs.capacity_removal_cost * plan["capacity_removed"]
            + costs.capacity_change_penalty
            * (plan["capacity_added"] + plan["capacity_removed"])
        ).sum()
        costs_by_fold.append(float(operational + costs.shortage_penalty * shortage.sum()))
        shortage_by_fold.append(float(shortage.sum()))
    return float(np.mean(costs_by_fold)), float(np.mean(shortage_by_fold))


def summarize_gap_method_development(
    predictions: pd.DataFrame,
    costs: CostParameters,
) -> pd.DataFrame:
    rows = []
    for (rho_max, method), group in predictions.groupby(["rho_max", "method"]):
        error = group["actual_gap"] - group["prediction"]
        decision_cost, decision_shortage = _evaluate_gap_predictions_on_development(
            group, float(rho_max), costs
        )
        rows.append(
            {
                "evaluation_period": "development_rolling_validation",
                "rho_max": rho_max,
                "method": method,
                "MAE": float(np.mean(np.abs(error))),
                "RMSE": float(np.sqrt(np.mean(error**2))),
                "bias_actual_minus_prediction": float(np.mean(error)),
                "downstream_mean_fold_cost": decision_cost,
                "downstream_mean_fold_shortage": decision_shortage,
                "forecast_rows": len(group),
                "final_test_used_for_selection": False,
            }
        )
    return pd.DataFrame(rows)


def build_gap_experiments(
    target_df: pd.DataFrame,
    inputs: Mapping[str, object],
    caches: Mapping[str, object],
    costs: CostParameters,
    seed: int,
) -> Dict[str, object]:
    """Select and evaluate direct gap, bias correction, and ensemble methods."""

    development_frames: List[pd.DataFrame] = []
    direct_selection_frames: List[pd.DataFrame] = []
    bias_selection_frames: List[pd.DataFrame] = []
    ensemble_weight_frames: List[pd.DataFrame] = []
    test_summaries: List[Dict[str, object]] = []
    test_realized_frames: List[pd.DataFrame] = []
    decision_winner_test_rows: List[Dict[str, object]] = []
    method_caches_by_rho: Dict[float, Dict[str, pd.DataFrame]] = {}

    for rho_max in RHO_VALUES:
        locked_gap = _locked_development_gap(
            inputs["selected_validation_points"], rho_max
        )
        direct = _direct_candidate_development_predictions(
            target_df, locked_gap, rho_max, seed
        )
        direct_selection = select_direct_gap_model(direct)
        direct_selection_frames.append(direct_selection)
        selected_direct = str(
            direct_selection.loc[
                direct_selection["selected_direct_gap_model"], "method"
            ].iloc[0]
        )

        corrected = build_nested_bias_corrections(locked_gap)
        bias_selection = select_bias_method(locked_gap, corrected)
        bias_selection_frames.append(bias_selection)
        selected_bias = str(
            bias_selection.loc[bias_selection["selected_bias_method"], "method"].iloc[0]
        )

        weights, ensemble = build_validation_ensemble(locked_gap, direct)
        ensemble_weight_frames.append(weights)
        selected_weight = float(weights.loc[weights["selected_weight"], "weight_locked"].iloc[0])

        development = pd.concat(
            [
                locked_gap,
                direct,
                corrected,
                ensemble,
            ],
            ignore_index=True,
            sort=False,
        )
        development_frames.append(development)
        development_rho_summary = summarize_gap_method_development(
            development, costs
        )
        decision_winner_method = str(
            development_rho_summary.sort_values(
                [
                    "downstream_mean_fold_cost",
                    "downstream_mean_fold_shortage",
                    "method",
                ]
            )["method"].iloc[0]
        )

        direct_cache = build_direct_gap_test_cache(
            target_df,
            caches["locked_point_forecast"],
            rho_max,
            selected_direct,
            seed,
        )
        bias_cache = build_bias_corrected_test_cache(
            caches["locked_point_forecast"], locked_gap, rho_max, selected_bias
        )
        ensemble_cache = build_ensemble_test_cache(
            caches["locked_point_forecast"], caches["seasonal_naive"], selected_weight
        )

        direct_seasonal_cache = build_direct_gap_test_cache(
            target_df,
            caches["locked_point_forecast"],
            rho_max,
            "direct_seasonal_naive",
            seed,
        )
        direct_last_cache = build_direct_gap_test_cache(
            target_df,
            caches["locked_point_forecast"],
            rho_max,
            "direct_last_value",
            seed,
        )
        method_caches = {
            "separate_locked_targets": caches["locked_point_forecast"],
            "seasonal_naive_direct_gap": direct_seasonal_cache,
            "last_value_direct_gap": direct_last_cache,
            "selected_direct_gap_model": direct_cache,
            "selected_bias_corrected_locked_gap": bias_cache,
            "ensemble_locked_seasonal": ensemble_cache,
        }
        method_caches_by_rho[rho_max] = method_caches
        run_caches = dict(caches)
        run_caches.update(method_caches)
        for method in method_caches:
            _, realized, _, _ = run_revised_policy_backtest(
                method,
                9,
                1,
                rho_max,
                costs,
                target_df,
                run_caches,
            )
            test_realized_frames.append(realized)
            summary = summarize_policy_run(realized)
            actual_gap = (
                realized["actual_occupied_demand"] / rho_max
                - realized["actual_available_capacity"]
            )
            predicted_gap = (
                realized["point_occupied_forecast"] / rho_max
                - realized["point_available_forecast"]
            )
            error = actual_gap - predicted_gap
            summary.update(
                {
                    "evaluation_period": "sealed_test_descriptive",
                    "method": method,
                    "MAE": float(np.mean(np.abs(error))),
                    "RMSE": float(np.sqrt(np.mean(error**2))),
                    "bias_actual_minus_prediction": float(np.mean(error)),
                    "selected_direct_model": selected_direct,
                    "selected_bias_method": selected_bias,
                    "ensemble_weight_locked": selected_weight,
                    "final_test_used_for_selection": False,
                }
            )
            test_summaries.append(summary)
        _, stochastic_realized, _, _ = run_revised_policy_backtest(
            "stochastic_scenarios",
            9,
            1,
            rho_max,
            costs,
            target_df,
            run_caches,
        )
        stochastic_summary = summarize_policy_run(stochastic_realized)
        stochastic_summary.update(
            {
                "evaluation_period": "sealed_test_descriptive",
                "method": "stochastic_gap_scenarios",
                "MAE": np.nan,
                "RMSE": np.nan,
                "bias_actual_minus_prediction": np.nan,
                "selected_direct_model": selected_direct,
                "selected_bias_method": selected_bias,
                "ensemble_weight_locked": selected_weight,
                "final_test_used_for_selection": False,
            }
        )
        test_summaries.append(stochastic_summary)
        test_realized_frames.append(stochastic_realized)

        if decision_winner_method == "separate_locked_targets" or decision_winner_method == "uncorrected_locked":
            decision_cache = caches["locked_point_forecast"]
            decision_test_method = "separate_locked_targets"
        elif decision_winner_method.startswith("direct_"):
            decision_cache = build_direct_gap_test_cache(
                target_df,
                caches["locked_point_forecast"],
                rho_max,
                decision_winner_method,
                seed,
            )
            decision_test_method = decision_winner_method
        elif decision_winner_method.startswith("bias_"):
            decision_cache = build_bias_corrected_test_cache(
                caches["locked_point_forecast"],
                locked_gap,
                rho_max,
                decision_winner_method,
            )
            decision_test_method = decision_winner_method
        elif decision_winner_method == "ensemble_locked_seasonal":
            decision_cache = ensemble_cache
            decision_test_method = decision_winner_method
        else:
            raise KeyError(f"No test-cache builder for {decision_winner_method}.")
        decision_key = "locked_development_decision_winner"
        decision_run_caches = dict(caches)
        decision_run_caches[decision_key] = decision_cache
        _, decision_realized, _, _ = run_revised_policy_backtest(
            decision_key,
            9,
            1,
            rho_max,
            costs,
            target_df,
            decision_run_caches,
        )
        decision_summary = summarize_policy_run(decision_realized)
        actual_gap = (
            decision_realized["actual_occupied_demand"] / rho_max
            - decision_realized["actual_available_capacity"]
        )
        predicted_gap = (
            decision_realized["point_occupied_forecast"] / rho_max
            - decision_realized["point_available_forecast"]
        )
        decision_summary.update(
            {
                "rho_max": rho_max,
                "development_selected_method": decision_winner_method,
                "sealed_test_method_mapping": decision_test_method,
                "sealed_test_gap_MAE": float(
                    np.mean(np.abs(actual_gap - predicted_gap))
                ),
                "final_test_used_for_selection": False,
            }
        )
        decision_winner_test_rows.append(decision_summary)

    development_all = pd.concat(development_frames, ignore_index=True, sort=False)
    development_summary = summarize_gap_method_development(development_all, costs)
    comparison = pd.concat(
        [development_summary, pd.DataFrame(test_summaries)],
        ignore_index=True,
        sort=False,
    )
    return {
        "development_predictions": development_all,
        "development_summary": development_summary,
        "direct_selection": pd.concat(direct_selection_frames, ignore_index=True),
        "bias_selection": pd.concat(bias_selection_frames, ignore_index=True),
        "ensemble_weights": pd.concat(ensemble_weight_frames, ignore_index=True),
        "comparison": comparison,
        "test_realized": pd.concat(test_realized_frames, ignore_index=True),
        "decision_winner_test": pd.DataFrame(decision_winner_test_rows),
        "method_caches_by_rho": method_caches_by_rho,
    }


def _development_target_accuracy(
    target_df: pd.DataFrame,
    selected_validation_points: pd.DataFrame,
    ensemble_weights: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate separate-target accuracy where a method genuinely forecasts both targets."""

    locked = selected_validation_points.loc[
        selected_validation_points["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
    ][
        [
            "fold_id",
            "forecast_origin",
            "forecast_date",
            "horizon_step",
            "target",
            "actual",
            "prediction",
        ]
    ].copy()
    locked["method"] = "separate_locked_targets"
    baseline_rows = []
    origins = locked[["fold_id", "forecast_origin"]].drop_duplicates()
    for record in origins.itertuples():
        dates = pd.date_range(
            pd.Timestamp(record.forecast_origin) + pd.offsets.MonthBegin(1),
            periods=9,
            freq="MS",
        )
        for target in (AVAILABLE_TARGET, OCCUPIED_TARGET):
            history = target_df.loc[: record.forecast_origin, target]
            for method, values in (
                ("direct_last_value", last_value_forecast(history, 9)),
                ("direct_seasonal_naive", seasonal_naive_forecast(history, 9)),
            ):
                for horizon_step, (date, prediction) in enumerate(
                    zip(dates, values), start=1
                ):
                    baseline_rows.append(
                        {
                            "fold_id": record.fold_id,
                            "forecast_origin": record.forecast_origin,
                            "forecast_date": date,
                            "horizon_step": horizon_step,
                            "target": target,
                            "actual": float(target_df.loc[date, target]),
                            "prediction": float(prediction),
                            "method": method,
                        }
                    )
    baseline = pd.DataFrame(baseline_rows)
    rows = []
    common = pd.concat([locked, baseline], ignore_index=True)
    for method, group in common.groupby("method"):
        target_mae = group.assign(
            absolute_error=(group["actual"] - group["prediction"]).abs()
        ).groupby("target")["absolute_error"].mean()
        rows.append(
            {
                "rho_max": np.nan,
                "method": method,
                "available_MAE": float(target_mae[AVAILABLE_TARGET]),
                "occupied_MAE": float(target_mae[OCCUPIED_TARGET]),
                "forecast_accuracy_score": float(target_mae.mean()),
            }
        )
    base_summary = pd.DataFrame(rows)

    ensemble_rows = []
    key = ["fold_id", "forecast_origin", "forecast_date", "horizon_step", "target"]
    seasonal = baseline.loc[baseline["method"].eq("direct_seasonal_naive")]
    paired = locked.merge(
        seasonal[key + ["prediction"]].rename(
            columns={"prediction": "seasonal_prediction"}
        ),
        on=key,
        validate="one_to_one",
    )
    for rho_max, selected in ensemble_weights.loc[
        ensemble_weights["selected_weight"]
    ].groupby("rho_max"):
        weight = float(selected["weight_locked"].iloc[0])
        frame = paired.copy()
        frame["ensemble_prediction"] = (
            weight * frame["prediction"]
            + (1.0 - weight) * frame["seasonal_prediction"]
        )
        target_mae = frame.assign(
            absolute_error=(frame["actual"] - frame["ensemble_prediction"]).abs()
        ).groupby("target")["absolute_error"].mean()
        ensemble_rows.append(
            {
                "rho_max": rho_max,
                "method": "ensemble_locked_seasonal",
                "available_MAE": float(target_mae[AVAILABLE_TARGET]),
                "occupied_MAE": float(target_mae[OCCUPIED_TARGET]),
                "forecast_accuracy_score": float(target_mae.mean()),
            }
        )
    return pd.concat(
        [base_summary, pd.DataFrame(ensemble_rows)], ignore_index=True
    )


def build_decision_focused_ranking(
    target_df: pd.DataFrame,
    selected_validation_points: pd.DataFrame,
    gap_experiments: Mapping[str, object],
) -> pd.DataFrame:
    """Label development-only forecast, gap, and decision-cost winners separately."""

    development = gap_experiments["development_summary"].copy()
    target_accuracy = _development_target_accuracy(
        target_df,
        selected_validation_points,
        gap_experiments["ensemble_weights"],
    )
    rows = []
    for rho_max in RHO_VALUES:
        frame = development.loc[development["rho_max"].eq(rho_max)].copy()
        accuracy = target_accuracy.loc[
            target_accuracy["rho_max"].isna()
            | target_accuracy["rho_max"].eq(rho_max)
        ].copy()
        accuracy["rho_max"] = rho_max
        frame = frame.merge(
            accuracy[
                [
                    "rho_max",
                    "method",
                    "available_MAE",
                    "occupied_MAE",
                    "forecast_accuracy_score",
                ]
            ],
            on=["rho_max", "method"],
            how="left",
        )
        forecast_candidates = frame.loc[frame["forecast_accuracy_score"].notna()]
        forecast_winner = str(
            forecast_candidates.sort_values(
                ["forecast_accuracy_score", "method"]
            )["method"].iloc[0]
        )
        gap_winner = str(frame.sort_values(["MAE", "method"])["method"].iloc[0])
        decision_winner = str(
            frame.sort_values(
                ["downstream_mean_fold_cost", "downstream_mean_fold_shortage", "method"]
            )["method"].iloc[0]
        )
        frame["forecast_accuracy_winner"] = frame["method"].eq(forecast_winner)
        frame["capacity_gap_winner"] = frame["method"].eq(gap_winner)
        frame["decision_cost_winner"] = frame["method"].eq(decision_winner)
        frame["locked_decision_winner"] = decision_winner
        frame["selection_period"] = "development_only"
        frame["final_test_used_for_selection"] = False
        frame["forecast_rank"] = frame["forecast_accuracy_score"].rank(
            method="min", na_option="bottom"
        )
        frame["capacity_gap_rank"] = frame["MAE"].rank(method="min")
        frame["decision_cost_rank"] = frame["downstream_mean_fold_cost"].rank(
            method="min"
        )
        rows.append(frame)
    result = pd.concat(rows, ignore_index=True)

    winner_test = gap_experiments["decision_winner_test"].rename(
        columns={
            "development_selected_method": "method",
            "total_realized_cost": "sealed_test_total_cost",
            "shortage_bed_months": "sealed_test_shortage",
        }
    )[
        [
            "rho_max",
            "method",
            "sealed_test_method_mapping",
            "sealed_test_total_cost",
            "sealed_test_shortage",
            "sealed_test_gap_MAE",
        ]
    ]
    result = result.merge(winner_test, on=["rho_max", "method"], how="left")
    return result


def build_cost_scenarios(legacy_costs: CostParameters) -> pd.DataFrame:
    """Create dimensionless, NHS-anchored, and direct-penalty sensitivity specs."""

    rows: List[Dict[str, object]] = [
        {
            "parameter_set_id": "legacy_illustrative",
            "cost_family": "legacy_illustrative",
            "currency": "GBP",
            "active_cost": legacy_costs.active_staffed_bed_cost,
            "addition_cost": legacy_costs.capacity_addition_cost,
            "removal_cost": legacy_costs.capacity_removal_cost,
            "change_cost": legacy_costs.capacity_change_penalty,
            "shortage_cost": legacy_costs.shortage_penalty,
            "shortage_to_active_ratio": legacy_costs.shortage_penalty
            / legacy_costs.active_staffed_bed_cost,
            "active_cost_anchor": legacy_costs.active_staffed_bed_cost,
            "stakeholder_validated": False,
        }
    ]
    ratios = (5.0, 10.0, 13.0, 25.0, 40.0)
    for ratio in ratios:
        rows.append(
            {
                "parameter_set_id": f"relative_active1_shortage{ratio:g}",
                "cost_family": "relative_dimensionless",
                "currency": "dimensionless",
                "active_cost": 1.0,
                "addition_cost": 1.0,
                "removal_cost": 0.5,
                "change_cost": 0.1,
                "shortage_cost": ratio,
                "shortage_to_active_ratio": ratio,
                "active_cost_anchor": 1.0,
                "stakeholder_validated": False,
            }
        )
    for active in (6000.0, 12000.0, 18000.0, 24000.0):
        for ratio in ratios:
            rows.append(
                {
                    "parameter_set_id": f"nhs_anchor_{active:g}_ratio_{ratio:g}",
                    "cost_family": "NHS_anchored_sensitivity",
                    "currency": "GBP",
                    "active_cost": active,
                    "addition_cost": active,
                    "removal_cost": 0.5 * active,
                    "change_cost": 0.1 * active,
                    "shortage_cost": ratio * active,
                    "shortage_to_active_ratio": ratio,
                    "active_cost_anchor": active,
                    "stakeholder_validated": False,
                }
            )
    for shortage in (2500.0, 5000.0, 8000.0, 10000.0, 15000.0, 25000.0, 50000.0):
        rows.append(
            {
                "parameter_set_id": f"legacy_active_direct_shortage_{shortage:g}",
                "cost_family": "direct_shortage_penalty_sensitivity",
                "currency": "GBP",
                "active_cost": legacy_costs.active_staffed_bed_cost,
                "addition_cost": legacy_costs.capacity_addition_cost,
                "removal_cost": legacy_costs.capacity_removal_cost,
                "change_cost": legacy_costs.capacity_change_penalty,
                "shortage_cost": shortage,
                "shortage_to_active_ratio": shortage
                / legacy_costs.active_staffed_bed_cost,
                "active_cost_anchor": legacy_costs.active_staffed_bed_cost,
                "stakeholder_validated": False,
            }
        )
    return pd.DataFrame(rows)


def _costs_from_scenario(row: pd.Series, legacy_costs: CostParameters) -> CostParameters:
    return CostParameters(
        active_staffed_bed_cost=float(row["active_cost"]),
        capacity_addition_cost=float(row["addition_cost"]),
        capacity_removal_cost=float(row["removal_cost"]),
        shortage_penalty=float(row["shortage_cost"]),
        capacity_change_penalty=float(row["change_cost"]),
        maximum_additional_capacity=legacy_costs.maximum_additional_capacity,
        monthly_ramp_up_limit=legacy_costs.monthly_ramp_up_limit,
        monthly_ramp_down_limit=legacy_costs.monthly_ramp_down_limit,
        parameter_set_id=str(row["parameter_set_id"]),
        validated=False,
    )


def run_cost_sensitivity(
    target_df: pd.DataFrame,
    caches: Mapping[str, object],
    legacy_costs: CostParameters,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Reoptimize every policy under every cost scenario."""

    registry = build_cost_scenarios(legacy_costs)
    summaries: List[Dict[str, object]] = []
    for spec in registry.to_dict("records"):
        spec_series = pd.Series(spec)
        costs = _costs_from_scenario(spec_series, legacy_costs)
        for policy in CORE_POLICIES:
            _, realized, _, _ = run_revised_policy_backtest(
                policy,
                9,
                1,
                0.90,
                costs,
                target_df,
                caches,
            )
            summary = summarize_policy_run(realized)
            summary.update(
                {
                    key: spec[key]
                    for key in (
                        "cost_family",
                        "currency",
                        "active_cost",
                        "addition_cost",
                        "removal_cost",
                        "change_cost",
                        "shortage_cost",
                        "shortage_to_active_ratio",
                        "active_cost_anchor",
                        "stakeholder_validated",
                    )
                }
            )
            summaries.append(summary)
    summary = attach_oracle_regret(pd.DataFrame(summaries))
    return summary, registry


def _pareto_flags(frame: pd.DataFrame) -> np.ndarray:
    cost = frame["total_realized_cost"].to_numpy(dtype=float)
    shortage = frame["shortage_bed_months"].to_numpy(dtype=float)
    flags = []
    for index in range(len(frame)):
        other = np.arange(len(frame)) != index
        dominates = (
            (cost[other] <= cost[index] + 1e-6)
            & (shortage[other] <= shortage[index] + 1e-6)
            & (
                (cost[other] < cost[index] - 1e-6)
                | (shortage[other] < shortage[index] - 1e-6)
            )
        )
        flags.append(not bool(dominates.any()))
    return np.asarray(flags, dtype=bool)


def build_policy_cost_frontier(cost_results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    deployable = cost_results.loc[
        ~cost_results["policy"].eq("perfect_information_oracle")
    ].copy()
    for _, group in deployable.groupby("parameter_set_id", sort=False):
        group = group.copy()
        group["pareto_efficient_cost_shortage"] = _pareto_flags(group)
        rows.append(group)
    return pd.concat(rows, ignore_index=True)


def build_break_even_analysis(cost_results: pd.DataFrame) -> pd.DataFrame:
    """Report pairwise policy switches and zero-shortage preference thresholds."""

    comparisons = (
        ("stochastic_vs_seasonal", "seasonal_naive", "stochastic_scenarios"),
        ("stochastic_vs_last_value", "last_value_naive", "stochastic_scenarios"),
        ("point_vs_seasonal", "seasonal_naive", "locked_point_forecast"),
        ("conservative95_vs_point", "locked_point_forecast", "conservative_95"),
    )
    rows: List[Dict[str, object]] = []
    lookup = cost_results.set_index(["parameter_set_id", "policy"])
    for spec_id, spec_group in cost_results.groupby("parameter_set_id"):
        common = spec_group.iloc[0]
        for comparison, policy_a, policy_b in comparisons:
            a = lookup.loc[(spec_id, policy_a)]
            b = lookup.loc[(spec_id, policy_b)]
            rows.append(
                {
                    "row_type": "pairwise_scenario_result",
                    "comparison": comparison,
                    "policy_a": policy_a,
                    "policy_b": policy_b,
                    "parameter_set_id": spec_id,
                    "cost_family": common["cost_family"],
                    "currency": common["currency"],
                    "active_cost_anchor": common["active_cost_anchor"],
                    "shortage_to_active_ratio": common["shortage_to_active_ratio"],
                    "shortage_penalty": common["shortage_cost"],
                    "policy_b_minus_a_cost": float(
                        b["total_realized_cost"] - a["total_realized_cost"]
                    ),
                    "policy_b_minus_a_shortage": float(
                        b["shortage_bed_months"] - a["shortage_bed_months"]
                    ),
                    "did_policy_b_cost_less": bool(
                        b["total_realized_cost"] < a["total_realized_cost"] - 1e-6
                    ),
                    "did_policy_b_reduce_shortage": bool(
                        b["shortage_bed_months"] < a["shortage_bed_months"] - 1e-6
                    ),
                    "switch_threshold_ratio": np.nan,
                    "note": "Each scenario was reoptimized; this is not post-hoc rescoring.",
                }
            )
        deployable = spec_group.loc[
            ~spec_group["policy"].eq("perfect_information_oracle")
        ]
        zero = deployable.loc[deployable["shortage_bed_months"].le(1e-6)]
        nonzero = deployable.loc[deployable["shortage_bed_months"].gt(1e-6)]
        if not zero.empty and not nonzero.empty:
            best_zero = zero.sort_values("total_realized_cost").iloc[0]
            best_nonzero = nonzero.sort_values("total_realized_cost").iloc[0]
            rows.append(
                {
                    "row_type": "zero_shortage_preference",
                    "comparison": "best_zero_shortage_vs_best_nonzero_shortage",
                    "policy_a": best_nonzero["policy"],
                    "policy_b": best_zero["policy"],
                    "parameter_set_id": spec_id,
                    "cost_family": common["cost_family"],
                    "currency": common["currency"],
                    "active_cost_anchor": common["active_cost_anchor"],
                    "shortage_to_active_ratio": common["shortage_to_active_ratio"],
                    "shortage_penalty": common["shortage_cost"],
                    "policy_b_minus_a_cost": float(
                        best_zero["total_realized_cost"]
                        - best_nonzero["total_realized_cost"]
                    ),
                    "policy_b_minus_a_shortage": float(
                        best_zero["shortage_bed_months"]
                        - best_nonzero["shortage_bed_months"]
                    ),
                    "did_policy_b_cost_less": bool(
                        best_zero["total_realized_cost"]
                        < best_nonzero["total_realized_cost"] - 1e-6
                    ),
                    "did_policy_b_reduce_shortage": True,
                    "switch_threshold_ratio": np.nan,
                    "note": "Best realized zero-shortage policy compared with cheapest nonzero-shortage policy.",
                }
            )
    result = pd.DataFrame(rows)
    threshold_rows = []
    pairwise = result.loc[result["row_type"].eq("pairwise_scenario_result")]
    for keys, group in pairwise.groupby(
        ["comparison", "cost_family", "active_cost_anchor"], dropna=False
    ):
        winning = group.loc[group["did_policy_b_cost_less"]].sort_values(
            "shortage_to_active_ratio"
        )
        threshold_rows.append(
            {
                "row_type": "estimated_switch_threshold",
                "comparison": keys[0],
                "policy_a": group["policy_a"].iloc[0],
                "policy_b": group["policy_b"].iloc[0],
                "parameter_set_id": "",
                "cost_family": keys[1],
                "currency": group["currency"].iloc[0],
                "active_cost_anchor": keys[2],
                "shortage_to_active_ratio": np.nan,
                "shortage_penalty": np.nan,
                "policy_b_minus_a_cost": np.nan,
                "policy_b_minus_a_shortage": np.nan,
                "did_policy_b_cost_less": not winning.empty,
                "did_policy_b_reduce_shortage": np.nan,
                "switch_threshold_ratio": (
                    float(winning["shortage_to_active_ratio"].iloc[0])
                    if not winning.empty
                    else np.nan
                ),
                "note": "Smallest tested ratio where policy B costs less; grid threshold, not a continuous causal estimate.",
            }
        )
    return pd.concat([result, pd.DataFrame(threshold_rows)], ignore_index=True)


def solve_service_constrained_plan(
    dates: Sequence[pd.Timestamp],
    available: Sequence[float],
    occupied: Sequence[float],
    rho_max: float,
    costs: CostParameters,
    initial_capacity: float,
    service_target: Optional[float] = None,
    lexicographic: bool = False,
    policy: str = "service_constrained",
) -> pd.DataFrame:
    """Solve epsilon-service or lexicographic shortage-first capacity planning."""

    dates = pd.DatetimeIndex(dates)
    available_values = np.asarray(available, dtype=float)
    occupied_values = np.asarray(occupied, dtype=float)
    n = len(dates)
    problem = pulp.LpProblem(f"revision_{policy}", pulp.LpMinimize)
    x = {t: pulp.LpVariable(f"x_{t}", 0, costs.maximum_additional_capacity) for t in range(n)}
    added = {t: pulp.LpVariable(f"add_{t}", 0, costs.monthly_ramp_up_limit) for t in range(n)}
    removed = {t: pulp.LpVariable(f"remove_{t}", 0, costs.monthly_ramp_down_limit) for t in range(n)}
    shortage = {t: pulp.LpVariable(f"shortage_{t}", lowBound=0) for t in range(n)}
    unused = {t: pulp.LpVariable(f"unused_{t}", lowBound=0) for t in range(n)}
    for t in range(n):
        previous = initial_capacity if t == 0 else x[t - 1]
        problem += x[t] == previous + added[t] - removed[t]
        required = occupied_values[t] / rho_max
        problem += available_values[t] + x[t] + shortage[t] == required + unused[t]
    total_shortage = pulp.lpSum(shortage.values())
    operational = pulp.lpSum(
        costs.active_staffed_bed_cost * x[t]
        + costs.capacity_addition_cost * added[t]
        + costs.capacity_removal_cost * removed[t]
        + costs.capacity_change_penalty * (added[t] + removed[t])
        for t in range(n)
    )
    if service_target is not None:
        if not 0 < service_target <= 1:
            raise ValueError("service_target must lie in (0, 1].")
        total_required = float(np.sum(occupied_values / rho_max))
        problem += total_shortage <= (1.0 - service_target) * total_required
    solver = pulp.PULP_CBC_CMD(msg=False, threads=1)
    if lexicographic:
        problem.setObjective(total_shortage)
        status = pulp.LpStatus[problem.solve(solver)]
        if status != "Optimal":
            raise RuntimeError(f"Lexicographic shortage phase failed: {status}")
        minimum_shortage = float(pulp.value(total_shortage))
        problem += total_shortage <= minimum_shortage + 1e-5
        problem.setObjective(operational)
    else:
        problem.setObjective(operational + costs.shortage_penalty * total_shortage)
    status = pulp.LpStatus[problem.solve(solver)]
    if status != "Optimal":
        raise RuntimeError(f"Service-constrained plan failed: {status}")
    rows = []
    previous = float(initial_capacity)
    for t, month in enumerate(dates):
        x_value = float(pulp.value(x[t]))
        add_value = float(pulp.value(added[t]))
        remove_value = float(pulp.value(removed[t]))
        shortage_value = float(pulp.value(shortage[t]))
        unused_value = float(pulp.value(unused[t]))
        rows.append(
            {
                "forecast_month": month,
                "horizon_step": t + 1,
                "policy": policy,
                "rho_max": rho_max,
                "available_capacity_input": available_values[t],
                "occupied_demand_input": occupied_values[t],
                "required_capacity_input": occupied_values[t] / rho_max,
                "initial_capacity_for_month": previous,
                "additional_capacity_active": x_value,
                "capacity_added": add_value,
                "capacity_removed": remove_value,
                "planned_shortage": shortage_value,
                "planned_unused_capacity": unused_value,
                "shortage_penalty_multiplier": 1.0,
                "scenario_count": 1,
                "solver_status": status,
                "parameter_set_id": costs.parameter_set_id,
                "maximum_capacity_limit": costs.maximum_additional_capacity,
                "ramp_up_limit": costs.monthly_ramp_up_limit,
                "ramp_down_limit": costs.monthly_ramp_down_limit,
            }
        )
        previous = x_value
    return pd.DataFrame(rows)


def run_service_formulation(
    target_df: pd.DataFrame,
    locked_cache: pd.DataFrame,
    costs: CostParameters,
    service_target: Optional[float],
    lexicographic: bool,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    label = "lexicographic_shortage_then_cost" if lexicographic else f"epsilon_service_{service_target:g}"
    initial_capacity = 0.0
    decision_rows = []
    realized_rows = []
    for origin in monthly_backtest_origins(FINAL_TEST_START, FINAL_TEST_END):
        dates = forecast_dates_from_origin(origin, FINAL_TEST_END, 9)
        path = _point_path(locked_cache, origin, len(dates))
        wide = path.pivot(index="forecast_date", columns="target", values="prediction").sort_index()
        plan = solve_service_constrained_plan(
            dates,
            wide[AVAILABLE_TARGET],
            wide[OCCUPIED_TARGET],
            0.90,
            costs,
            initial_capacity,
            service_target=service_target,
            lexicographic=lexicographic,
            policy=label,
        )
        first = plan.iloc[[0]].copy()
        first["point_available_forecast"] = float(wide[AVAILABLE_TARGET].iloc[0])
        first["point_occupied_forecast"] = float(wide[OCCUPIED_TARGET].iloc[0])
        first["forecast_origin"] = origin
        first["decision_information_cutoff"] = origin
        first["uses_future_actuals"] = False
        first["cost_parameters_validated"] = False
        first["planning_horizon_months"] = 9
        first["reoptimization_frequency_months"] = 1
        first["implemented"] = True
        first["input_source"] = "locked_point_forecast"
        first["service_formulation"] = "lexicographic" if lexicographic else "epsilon_constraint"
        first["planned_service_target"] = service_target if service_target is not None else 1.0
        realized = evaluate_implemented_decisions(first, target_df, costs)
        realized["planning_horizon_months"] = 9
        realized["reoptimization_frequency_months"] = 1
        realized["operational_cost"] = (
            realized["active_extra_capacity_cost"]
            + realized["addition_cost"]
            + realized["removal_cost"]
            + realized["capacity_change_cost"]
        )
        realized["service_formulation"] = first["service_formulation"].iloc[0]
        realized["planned_service_target"] = first["planned_service_target"].iloc[0]
        decision_rows.append(first)
        realized_rows.append(realized)
        initial_capacity = float(first["additional_capacity_active"].iloc[0])
    return pd.concat(decision_rows, ignore_index=True), pd.concat(realized_rows, ignore_index=True)


def build_cost_service_frontier(
    target_df: pd.DataFrame,
    caches: Mapping[str, object],
    costs: CostParameters,
    baseline_comparison: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    summaries = []
    realized_frames = []
    for target in (0.95, 0.975, 0.99, 1.0):
        _, realized = run_service_formulation(
            target_df,
            caches["locked_point_forecast"],
            costs,
            service_target=target,
            lexicographic=False,
        )
        summary = summarize_policy_run(realized)
        summary["formulation"] = "epsilon_constraint"
        summary["planned_service_target"] = target
        summaries.append(summary)
        realized_frames.append(realized)
    _, lex_realized = run_service_formulation(
        target_df,
        caches["locked_point_forecast"],
        costs,
        service_target=None,
        lexicographic=True,
    )
    lex_summary = summarize_policy_run(lex_realized)
    lex_summary["formulation"] = "lexicographic_shortage_then_cost"
    lex_summary["planned_service_target"] = 1.0
    summaries.append(lex_summary)
    realized_frames.append(lex_realized)

    service = pd.DataFrame(summaries)
    baseline = baseline_comparison.copy()
    baseline["formulation"] = "penalty_objective"
    baseline["planned_service_target"] = np.nan
    columns = sorted(set(service.columns) | set(baseline.columns))
    frontier = pd.concat(
        [baseline.reindex(columns=columns), service.reindex(columns=columns)],
        ignore_index=True,
    )
    frontier["oracle_benchmark_only"] = frontier["policy"].eq(
        "perfect_information_oracle"
    )
    frontier["pareto_efficient_including_oracle"] = _pareto_flags(frontier)
    frontier["pareto_efficient_cost_shortage"] = False
    implementable = ~frontier["oracle_benchmark_only"]
    frontier.loc[implementable, "pareto_efficient_cost_shortage"] = _pareto_flags(
        frontier.loc[implementable]
    )
    frontier["service_targets_are_sensitivity_settings"] = True
    return frontier, pd.concat(realized_frames, ignore_index=True)


def build_metric_sign_audit(
    baseline: pd.DataFrame,
    factorial: pd.DataFrame,
    stochastic_value: pd.DataFrame,
) -> pd.DataFrame:
    """Document sign conventions and assert expected stochastic inequalities."""

    rows: List[Dict[str, object]] = []
    lookup = baseline.set_index("policy")
    seasonal_cost = float(lookup.loc["seasonal_naive", "total_realized_cost"])
    point_cost = float(lookup.loc["locked_point_forecast", "total_realized_cost"])
    for policy in IMPLEMENTABLE_POLICIES:
        policy_cost = float(lookup.loc[policy, "total_realized_cost"])
        rows.append(
            {
                "metric": "ForecastValue",
                "policy": policy,
                "formula": "seasonal_naive_cost - policy_cost",
                "value": seasonal_cost - policy_cost,
                "positive_interpretation": "policy costs less than seasonal naive",
                "inequality_expected": False,
                "inequality_passed": True,
            }
        )
    stochastic_cost = float(
        lookup.loc["stochastic_scenarios", "total_realized_cost"]
    )
    rows.append(
        {
            "metric": "UncertaintyValue",
            "policy": "stochastic_scenarios",
            "formula": "locked_point_cost - stochastic_cost",
            "value": point_cost - stochastic_cost,
            "positive_interpretation": "stochastic policy costs less than locked point",
            "inequality_expected": False,
            "inequality_passed": True,
        }
    )
    h9 = factorial.loc[factorial["planning_horizon_months"].eq(9)]
    for policy, group in h9.groupby("policy"):
        monthly = float(
            group.loc[
                group["reoptimization_frequency_months"].eq(1),
                "total_realized_cost",
            ].iloc[0]
        )
        for row in group.itertuples():
            rows.append(
                {
                    "metric": "freshness_value",
                    "policy": policy,
                    "formula": "less_frequent_cost - monthly_cost",
                    "value": row.total_realized_cost - monthly,
                    "positive_interpretation": "monthly updating costs less",
                    "inequality_expected": False,
                    "inequality_passed": True,
                }
            )
    for row in baseline.itertuples():
        rows.append(
            {
                "metric": "regret",
                "policy": row.policy,
                "formula": "policy_cost - perfect_information_oracle_cost",
                "value": row.regret,
                "positive_interpretation": "cost of imperfect information",
                "inequality_expected": True,
                "inequality_passed": row.regret >= -1e-5,
            }
        )
    sv = stochastic_value.iloc[0]
    ws = float(sv["wait_and_see_cost"])
    rp = float(sv["stochastic_recourse_solution_cost"])
    eev = float(sv["expected_result_of_expected_value_solution"])
    evpi = float(sv["expected_value_of_perfect_information"])
    vss = float(sv["value_of_stochastic_solution"])
    rows.extend(
        [
            {
                "metric": "EVPI",
                "policy": "first_origin_scenario_problem",
                "formula": "stochastic_recourse - wait_and_see",
                "value": evpi,
                "positive_interpretation": "perfect information has nonnegative value",
                "inequality_expected": True,
                "inequality_passed": ws <= rp + 1e-5 and evpi >= -1e-5,
            },
            {
                "metric": "VSS",
                "policy": "first_origin_scenario_problem",
                "formula": "EEV - stochastic_recourse",
                "value": vss,
                "positive_interpretation": "stochastic solution has nonnegative value",
                "inequality_expected": True,
                "inequality_passed": rp <= eev + 1e-5 and vss >= -1e-5,
            },
        ]
    )
    result = pd.DataFrame(rows)
    expected = result.loc[result["inequality_expected"]]
    if not expected["inequality_passed"].all():
        raise AssertionError("An expected oracle, EVPI, or VSS inequality failed.")
    return result


def _bias_results_table(gap_experiments: Mapping[str, object]) -> pd.DataFrame:
    development = gap_experiments["development_summary"]
    bias_methods = {
        "separate_locked_targets",
        "bias_global",
        "bias_horizon_specific",
        "bias_recent_18",
    }
    dev = development.loc[development["method"].isin(bias_methods)].copy()
    dev["result_stage"] = "development_nested_validation"
    test = gap_experiments["comparison"].loc[
        gap_experiments["comparison"]["evaluation_period"].eq(
            "sealed_test_descriptive"
        )
        & gap_experiments["comparison"]["method"].isin(
            ["separate_locked_targets", "selected_bias_corrected_locked_gap"]
        )
    ].copy()
    test["result_stage"] = "sealed_test_after_lock"
    selection = gap_experiments["bias_selection"]
    dev = dev.merge(
        selection[["rho_max", "method", "selected_bias_method"]],
        on=["rho_max", "method"],
        how="left",
    )
    return pd.concat([dev, test], ignore_index=True, sort=False)


def _ensemble_results_table(gap_experiments: Mapping[str, object]) -> pd.DataFrame:
    weights = gap_experiments["ensemble_weights"].copy()
    weights["result_stage"] = "development_weight_selection"
    development = gap_experiments["development_summary"].loc[
        gap_experiments["development_summary"]["method"].isin(
            ["separate_locked_targets", "direct_seasonal_naive", "ensemble_locked_seasonal"]
        )
    ].copy()
    development["result_stage"] = "development_comparison"
    test = gap_experiments["comparison"].loc[
        gap_experiments["comparison"]["evaluation_period"].eq(
            "sealed_test_descriptive"
        )
        & gap_experiments["comparison"]["method"].isin(
            ["separate_locked_targets", "seasonal_naive_direct_gap", "ensemble_locked_seasonal"]
        )
    ].copy()
    test["result_stage"] = "sealed_test_after_weight_lock"
    return pd.concat([weights, development, test], ignore_index=True, sort=False)


def _plot_actual_forecast_paths(
    realized: pd.DataFrame,
    output_dir: Path,
) -> None:
    central = realized.loc[
        realized["planning_horizon_months"].eq(9)
        & realized["reoptimization_frequency_months"].eq(1)
        & realized["policy"].isin(
            [
                "last_value_naive",
                "seasonal_naive",
                "locked_point_forecast",
                "stochastic_scenarios",
            ]
        )
    ].copy()
    central["forecast_month"] = pd.to_datetime(central["forecast_month"])
    for target_label, actual_column, prediction_column, filename, title in (
        (
            "available",
            "actual_available_capacity",
            "point_available_forecast",
            "actual_vs_forecast_available.png",
            "Actual versus forecast available beds",
        ),
        (
            "occupied",
            "actual_occupied_demand",
            "point_occupied_forecast",
            "actual_vs_forecast_occupied.png",
            "Actual versus forecast occupied beds",
        ),
    ):
        fig, ax = plt.subplots(figsize=(10, 5))
        actual = central.drop_duplicates("forecast_month").sort_values("forecast_month")
        ax.plot(actual["forecast_month"], actual[actual_column], color="black", marker="o", linewidth=2, label="Actual")
        for policy, group in central.groupby("policy"):
            group = group.sort_values("forecast_month")
            ax.plot(group["forecast_month"], group[prediction_column], marker=".", label=policy)
        ax.set_title(title)
        ax.set_ylabel("National monthly bed-equivalents")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, ncol=2)
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=160)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    actual = central.drop_duplicates("forecast_month").sort_values("forecast_month")
    actual_gap = actual["actual_occupied_demand"] / 0.90 - actual["actual_available_capacity"]
    ax.plot(actual["forecast_month"], actual_gap, color="black", marker="o", linewidth=2, label="Actual gap")
    for policy, group in central.groupby("policy"):
        group = group.sort_values("forecast_month")
        predicted_gap = group["point_occupied_forecast"] / 0.90 - group["point_available_forecast"]
        ax.plot(group["forecast_month"], predicted_gap, marker=".", label=policy)
    ax.axhline(0, color="gray", linewidth=1)
    ax.set_title("Actual versus forecast occupancy-adjusted capacity gap")
    ax.set_ylabel("Required additional bed-equivalents")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(output_dir / "actual_vs_forecast_capacity_gap.png", dpi=160)
    plt.close(fig)

    for value_column, filename, title, ylabel in (
        ("additional_capacity_active", "monthly_implemented_extra_capacity.png", "Monthly implemented extra capacity", "Additional bed-equivalents"),
        ("realized_shortage_bed_months", "monthly_realized_shortage.png", "Monthly realized occupancy-headroom deficit", "Bed-month equivalents"),
    ):
        fig, ax = plt.subplots(figsize=(10, 5))
        for policy, group in central.groupby("policy"):
            group = group.sort_values("forecast_month")
            ax.plot(group["forecast_month"], group[value_column], marker="o", label=policy)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, ncol=2)
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=160)
        plt.close(fig)


def generate_revision_plots(
    output_dir: Path,
    factorial: pd.DataFrame,
    factorial_realized: pd.DataFrame,
    history: pd.DataFrame,
    gap_experiments: Mapping[str, object],
    cost_results: pd.DataFrame,
    service_frontier: pd.DataFrame,
    ranking: pd.DataFrame,
    scenarios: pd.DataFrame,
) -> pd.DataFrame:
    """Create the 15 required supervisor-readable figures."""

    output_dir.mkdir(parents=True, exist_ok=True)
    _plot_actual_forecast_paths(factorial_realized, output_dir)

    baseline = factorial.loc[
        factorial["planning_horizon_months"].eq(9)
        & factorial["reoptimization_frequency_months"].eq(1)
        & ~factorial["policy"].eq("perfect_information_oracle")
    ].copy()
    components = baseline.set_index("policy")[[
        "active_extra_capacity_cost",
        "addition_cost",
        "removal_cost",
        "capacity_change_cost",
        "shortage_cost",
    ]]
    fig, ax = plt.subplots(figsize=(11, 6))
    components.plot(kind="bar", stacked=True, ax=ax)
    ax.set_title("Realized cost decomposition by policy")
    ax.set_ylabel("Illustrative GBP")
    ax.set_xlabel("")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / "cost_decomposition_by_policy.png", dpi=160)
    plt.close(fig)

    monthly = factorial.loc[
        factorial["reoptimization_frequency_months"].eq(1)
        & ~factorial["policy"].eq("perfect_information_oracle")
    ]
    for column, filename, title, ylabel in (
        ("total_realized_cost", "forecast_horizon_vs_cost.png", "Planning horizon versus realized cost with monthly updating", "Illustrative GBP"),
        ("shortage_bed_months", "forecast_horizon_vs_shortage.png", "Planning horizon versus modeled deficit with monthly updating", "Bed-month equivalents"),
    ):
        fig, ax = plt.subplots(figsize=(9, 5))
        for policy, group in monthly.groupby("policy"):
            group = group.sort_values("planning_horizon_months")
            ax.plot(group["planning_horizon_months"], group[column], marker="o", label=policy)
        ax.set_xticks(PLANNING_HORIZONS)
        ax.set_xlabel("Planning horizon (months)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, ncol=2)
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=160)
        plt.close(fig)

    fig, ax1 = plt.subplots(figsize=(9, 5))
    ordered = history.assign(order=history["history_window"].map({"12": 0, "24": 1, "36": 2, "all_available": 3})).sort_values("order")
    ax1.plot(ordered["history_window"], ordered["total_realized_cost"], marker="o", color="#2a6f97", label="Cost")
    ax1.set_ylabel("Illustrative GBP", color="#2a6f97")
    ax2 = ax1.twinx()
    ax2.plot(ordered["history_window"], ordered["shortage_bed_months"], marker="s", color="#a13d2d", label="Deficit")
    ax2.set_ylabel("Bed-month equivalents", color="#a13d2d")
    ax1.set_title("History-window sensitivity")
    ax1.set_xlabel("Training outcome history")
    ax1.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "history_window_vs_cost.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 6))
    formulation_markers = {
        "penalty_objective": "o",
        "epsilon_constraint": "s",
        "lexicographic_shortage_then_cost": "D",
    }
    for formulation, group in service_frontier.groupby("formulation"):
        ax.scatter(
            group["shortage_bed_months"],
            group["total_realized_cost"],
            s=58,
            marker=formulation_markers.get(str(formulation), "o"),
            alpha=0.85,
            label=str(formulation).replace("_", " "),
        )
    # Several service formulations can produce the same realized decision. Label
    # the distinct core policy points only; the formulation legend and CSV retain
    # the complete detail without stacking unreadable text at one coordinate.
    core_points = service_frontier.loc[
        service_frontier["formulation"].eq("penalty_objective")
    ]
    for index, row in enumerate(core_points.itertuples()):
        offset = (5, 5 if index % 2 == 0 else -11)
        ax.annotate(
            str(row.policy),
            (row.shortage_bed_months, row.total_realized_cost),
            xytext=offset,
            textcoords="offset points",
            fontsize=7,
        )
    ax.set_title("Cost-service frontier")
    ax.set_xlabel("Modeled deficit bed-months")
    ax.set_ylabel("Illustrative GBP")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    fig.savefig(output_dir / "cost_service_frontier.png", dpi=160)
    plt.close(fig)

    direct = cost_results.loc[
        cost_results["cost_family"].eq("direct_shortage_penalty_sensitivity")
        & ~cost_results["policy"].eq("perfect_information_oracle")
    ]
    winners = direct.loc[direct.groupby("shortage_cost")["total_realized_cost"].idxmin()].sort_values("shortage_cost")
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.scatter(winners["shortage_cost"], range(len(winners)), c=range(len(winners)), cmap="viridis", s=70)
    ax.set_yticks(range(len(winners)), winners["policy"])
    ax.set_xscale("log")
    ax.set_xlabel("Shortage penalty (GBP per deficit bed-month)")
    ax.set_title("Lowest-cost policy by shortage penalty")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "shortage_penalty_vs_winning_policy.png", dpi=160)
    plt.close(fig)

    anchored = cost_results.loc[
        cost_results["cost_family"].eq("NHS_anchored_sensitivity")
        & np.isclose(cost_results["shortage_to_active_ratio"], 13.0)
        & ~cost_results["policy"].eq("perfect_information_oracle")
    ]
    anchor_winners = anchored.loc[anchored.groupby("active_cost_anchor")["total_realized_cost"].idxmin()].sort_values("active_cost_anchor")
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.scatter(anchor_winners["active_cost_anchor"], range(len(anchor_winners)), s=70, color="#2a9d8f")
    ax.set_yticks(range(len(anchor_winners)), anchor_winners["policy"])
    ax.set_xlabel("Active staffed bed-month sensitivity anchor (GBP)")
    ax.set_title("Lowest-cost policy by active-capacity anchor at ratio 13")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "active_capacity_cost_vs_winning_policy.png", dpi=160)
    plt.close(fig)

    central_rank = ranking.loc[ranking["rho_max"].eq(0.90)]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(central_rank["capacity_gap_rank"], central_rank["decision_cost_rank"], s=60)
    for row in central_rank.itertuples():
        ax.annotate(row.method, (row.capacity_gap_rank, row.decision_cost_rank), fontsize=7)
    ax.set_xlabel("Development capacity-gap rank")
    ax.set_ylabel("Development decision-cost rank")
    ax.set_title("Forecast rank versus decision rank")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "forecast_rank_vs_decision_rank.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    for scenario_id, group in scenarios.groupby("scenario_id"):
        group = group.sort_values("horizon_step")
        axes[0].plot(group["horizon_step"], group["occupied_demand"], alpha=0.65)
        axes[1].plot(group["horizon_step"], group["available_capacity"], alpha=0.65)
        axes[2].plot(group["horizon_step"], group["occupied_demand"] / 0.90 - group["available_capacity"], alpha=0.65)
    axes[0].set_ylabel("Occupied")
    axes[1].set_ylabel("Available")
    axes[2].set_ylabel("Gap")
    axes[2].set_xlabel("Horizon step")
    axes[0].set_title("Seven centered paired scenario paths by horizon")
    for ax in axes:
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_dir / "scenario_paths_by_horizon.png", dpi=160)
    plt.close(fig)

    test_realized = gap_experiments["test_realized"]
    central_gap = test_realized.loc[
        test_realized["rho_max"].eq(0.90)
        & test_realized["policy"].isin(
            ["separate_locked_targets", "selected_bias_corrected_locked_gap"]
        )
    ].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    actual = central_gap.drop_duplicates("forecast_month").sort_values("forecast_month")
    ax.plot(actual["forecast_month"], actual["actual_occupied_demand"] / 0.90 - actual["actual_available_capacity"], color="black", marker="o", label="Actual gap")
    for policy, group in central_gap.groupby("policy"):
        group = group.sort_values("forecast_month")
        prediction = group["point_occupied_forecast"] / 0.90 - group["point_available_forecast"]
        ax.plot(group["forecast_month"], prediction, marker=".", label=policy)
    ax.set_title("Capacity-gap bias before and after development-only correction")
    ax.set_ylabel("Required additional bed-equivalents")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_dir / "bias_before_after_correction.png", dpi=160)
    plt.close(fig)

    names = [
        "actual_vs_forecast_available.png",
        "actual_vs_forecast_occupied.png",
        "actual_vs_forecast_capacity_gap.png",
        "monthly_implemented_extra_capacity.png",
        "monthly_realized_shortage.png",
        "cost_decomposition_by_policy.png",
        "forecast_horizon_vs_cost.png",
        "forecast_horizon_vs_shortage.png",
        "history_window_vs_cost.png",
        "cost_service_frontier.png",
        "shortage_penalty_vs_winning_policy.png",
        "active_capacity_cost_vs_winning_policy.png",
        "forecast_rank_vs_decision_rank.png",
        "scenario_paths_by_horizon.png",
        "bias_before_after_correction.png",
    ]
    return pd.DataFrame(
        {"plot": names, "path": [str(output_dir / name) for name in names]}
    )


def _format_money(value: float, currency: str = "GBP") -> str:
    prefix = "GBP " if currency == "GBP" else ""
    return f"{prefix}{value:,.0f}"


def build_revised_summary(
    baseline: pd.DataFrame,
    factorial: pd.DataFrame,
    history: pd.DataFrame,
    gap_experiments: Mapping[str, object],
    ranking: pd.DataFrame,
    cost_results: pd.DataFrame,
    break_even: pd.DataFrame,
    service_frontier: pd.DataFrame,
) -> str:
    """Write a candid supervisor-facing interpretation of the revised evidence."""

    central = baseline.set_index("policy")
    last = central.loc["last_value_naive"]
    seasonal = central.loc["seasonal_naive"]
    point = central.loc["locked_point_forecast"]
    stochastic = central.loc["stochastic_scenarios"]
    monthly = factorial.loc[
        factorial["reoptimization_frequency_months"].eq(1)
        & ~factorial["policy"].eq("perfect_information_oracle")
    ]
    horizon_winners = monthly.loc[
        monthly.groupby("planning_horizon_months")["total_realized_cost"].idxmin()
    ].sort_values("planning_horizon_months")
    history_best = history.loc[
        ~history["exploratory_only"]
    ].sort_values("total_realized_cost").iloc[0]
    gap_test = gap_experiments["comparison"].loc[
        gap_experiments["comparison"]["evaluation_period"].eq("sealed_test_descriptive")
        & gap_experiments["comparison"]["rho_max"].eq(0.90)
    ].sort_values("total_realized_cost")
    best_gap = gap_test.iloc[0]
    rank90 = ranking.loc[ranking["rho_max"].eq(0.90)]
    forecast_winner = str(rank90.loc[rank90["forecast_accuracy_winner"], "method"].iloc[0])
    gap_winner = str(rank90.loc[rank90["capacity_gap_winner"], "method"].iloc[0])
    decision_winner = str(rank90["locked_decision_winner"].iloc[0])
    direct_family = cost_results.loc[
        cost_results["cost_family"].eq("direct_shortage_penalty_sensitivity")
        & ~cost_results["policy"].eq("perfect_information_oracle")
    ]
    winner_counts = direct_family.loc[
        direct_family.groupby("parameter_set_id")["total_realized_cost"].idxmin()
    ]["policy"].value_counts()
    pareto = service_frontier.loc[service_frontier["pareto_efficient_cost_shortage"]]
    oracle = service_frontier.loc[
        service_frontier["policy"].eq("perfect_information_oracle")
    ].iloc[0]

    horizon_lines = "\n".join(
        f"- {int(row.planning_horizon_months)} month(s): `{row.policy}`, "
        f"cost {_format_money(row.total_realized_cost)}, deficit {row.shortage_bed_months:,.1f}."
        for row in horizon_winners.itertuples()
    )
    cost_winner_lines = "\n".join(
        f"- `{policy}` was cheapest in {count} of {len(direct_family['parameter_set_id'].unique())} direct-penalty scenarios."
        for policy, count in winner_counts.items()
    )
    pareto_lines = "\n".join(
        f"- `{row.policy}` ({getattr(row, 'formulation', '')}): cost {_format_money(row.total_realized_cost)}, deficit {row.shortage_bed_months:,.1f}."
        for row in pareto.itertuples()
    )
    return f"""# Revised NHS forecast-to-optimization findings

## What changed

The revision preserves the original results, corrects the seasonal-naive terminology,
adds true last-value persistence, separates planning horizon from update frequency,
uses centered equally weighted paired scenarios, and selects direct-gap, bias,
ensemble, and decision-focused alternatives using development data only.

## Primary legacy-cost comparison

At `rho_max=0.90`, nine-month lookahead, monthly updating, and the unvalidated
`legacy_illustrative` costs:

- Last-value naive: cost {_format_money(last.total_realized_cost)}, deficit {last.shortage_bed_months:,.1f} bed-months.
- Seasonal naive: cost {_format_money(seasonal.total_realized_cost)}, deficit {seasonal.shortage_bed_months:,.1f} bed-months.
- Locked point forecast: cost {_format_money(point.total_realized_cost)}, deficit {point.shortage_bed_months:,.1f} bed-months.
- Centered stochastic scenarios: cost {_format_money(stochastic.total_realized_cost)}, deficit {stochastic.shortage_bed_months:,.1f} bed-months.

Seasonal naive remains a strong legitimate forecast baseline. The selected target-MAE
winners are not assumed to win the decision problem.

## Horizon with monthly updating

{horizon_lines}

These results separate lookahead from how long decisions are held fixed.

## History sensitivity

Among the non-exploratory 24-, 36-, and all-history test sensitivities, the lowest
realized cost was `{history_best.history_window}` history at
{_format_money(history_best.total_realized_cost)} with
{history_best.shortage_bed_months:,.1f} deficit bed-months. This is evidence consistent
with possible regime sensitivity, not proof of structural drift. The sealed test did
not select a new canonical history window.

## Direct gap, correction, and ensemble

At `rho_max=0.90`, the lowest sealed-test cost among the gap-input alternatives was
`{best_gap.method}` at {_format_money(best_gap.total_realized_cost)} with
{best_gap.shortage_bed_months:,.1f} deficit bed-months. This method was locked from
development evidence where selection was required.

The development-only labels differ as follows:

- Forecast-accuracy winner: `{forecast_winner}`.
- Capacity-gap winner: `{gap_winner}`.
- Decision-cost winner: `{decision_winner}`.

This distinction is central: the best separate-target forecast need not create the
best optimization input.

## Cost sensitivity

{cost_winner_lines}

Every cost point was reoptimized. Dimensionless experiments are not GBP, and the
GBP 6,000-24,000 active-capacity anchors are sensitivities rather than validated NHS
marginal costs. No combination is presented as the correct NHS value.

## Cost-service frontier

The central nondominated **implementable** points are:

{pareto_lines}

The perfect-information oracle is reported separately at
{_format_money(oracle.total_realized_cost)} and {oracle.shortage_bed_months:,.1f}
deficit bed-months. It is not implementable and therefore does not erase the
operational Pareto frontier.

Service constraints are sensitivity settings, not NHS targets. In this run, the
deterministic epsilon and lexicographic formulations produced the same decisions as
the locked point policy because planned shortage was already zero; they could not
protect against forecast error. The stochastic policy supplied the distinct
zero-realized-shortage implementable point.

## Final interpretation

The original negative point-policy result was not caused by realized-shortage or cost
formula defects. It reflected a strong seasonal baseline, harmful joint directional
error in independently forecast occupied and available beds, a decision target that
was not directly selected, and highly influential illustrative costs. The revision
also fixes reporting/design defects: baseline naming, scenario weighting/centering,
missing information timelines, and conflated horizon/update comparisons.

Evidence remains limited by 66 national monthly observations, nine sealed test months,
seven complete residual paths, overlapping development folds, and no validated NHS
cost or operational feasibility parameters. Stakeholder approval is still required for
costs, occupancy thresholds, ramp limits, maximum deployable capacity, and service
targets before any operational use.
"""


def run_revision(config: RevisionConfig = RevisionConfig()) -> Dict[str, object]:
    """Run the complete revised experiment and save all required artifacts."""

    audit_path = config.output_dir / "optimization_forensic_audit.md"
    if not audit_path.exists():
        raise FileNotFoundError(
            "The forensic audit must be completed before running revision code."
        )
    if not config.legacy_output_dir.exists():
        raise FileNotFoundError("Legacy outputs were not preserved.")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    plot_dir = config.output_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    legacy_costs = replace(
        config.legacy_costs, parameter_set_id="legacy_illustrative", validated=False
    )

    inputs = load_revision_inputs(config)
    target_df = inputs["target_df"]
    caches = build_base_caches(inputs, config)
    alignment = build_forecast_alignment_audit(target_df, caches)

    factorial, factorial_decisions, factorial_realized, timeline = (
        build_horizon_update_factorial(target_df, caches, legacy_costs)
    )
    baseline = build_baseline_comparison(factorial)
    shortage_audit = build_realized_shortage_audit(factorial_realized)
    cost_audit = build_cost_formula_audit(legacy_costs)

    history, history_caches, history_realized = build_history_window_decision_results(
        target_df,
        inputs["selection"],
        caches,
        legacy_costs,
        config.random_seed,
    )
    gap_experiments = build_gap_experiments(
        target_df, inputs, caches, legacy_costs, config.random_seed
    )
    bias_results = _bias_results_table(gap_experiments)
    ensemble_results = _ensemble_results_table(gap_experiments)
    ranking = build_decision_focused_ranking(
        target_df, inputs["selected_validation_points"], gap_experiments
    )

    cost_results, cost_registry = run_cost_sensitivity(
        target_df, caches, legacy_costs
    )
    break_even = build_break_even_analysis(cost_results)
    cost_frontier = build_policy_cost_frontier(cost_results)
    service_frontier, service_realized = build_cost_service_frontier(
        target_df, caches, legacy_costs, baseline
    )

    first_origin = FINAL_TEST_START - pd.offsets.MonthBegin(1)
    first_scenarios, scenario_mean_diagnostics = build_centered_scenarios(
        caches["locked_point_forecast"],
        caches["centered_residual_paths"],
        first_origin,
        9,
    )
    stochastic_value = build_stochastic_value_summary(
        first_scenarios, 0.90, legacy_costs
    )
    metric_sign_audit = build_metric_sign_audit(
        baseline, factorial, stochastic_value
    )

    plot_manifest = pd.DataFrame()
    if config.generate_plots:
        plot_manifest = generate_revision_plots(
            plot_dir,
            factorial,
            factorial_realized,
            history,
            gap_experiments,
            cost_results,
            service_frontier,
            ranking,
            first_scenarios,
        )
    markdown = build_revised_summary(
        baseline,
        factorial,
        history,
        gap_experiments,
        ranking,
        cost_results,
        break_even,
        service_frontier,
    )

    outputs: Dict[str, object] = {
        "forecast_input_alignment_audit": alignment,
        "policy_information_timeline": timeline,
        "realized_shortage_audit": shortage_audit,
        "cost_formula_audit": cost_audit,
        "baseline_comparison": baseline,
        "horizon_update_factorial_results": factorial,
        "history_window_decision_results": history,
        "capacity_gap_forecast_comparison": gap_experiments["comparison"],
        "bias_correction_results": bias_results,
        "ensemble_results": ensemble_results,
        "cost_sensitivity_results": cost_results,
        "policy_break_even_analysis": break_even,
        "policy_cost_frontier": cost_frontier,
        "cost_service_frontier": service_frontier,
        "decision_focused_model_ranking": ranking,
        "direct_gap_model_selection": gap_experiments["direct_selection"],
        "bias_method_selection": gap_experiments["bias_selection"],
        "ensemble_weight_selection": gap_experiments["ensemble_weights"],
        "development_gap_predictions": gap_experiments["development_predictions"],
        "locked_decision_winner_test_results": gap_experiments["decision_winner_test"],
        "factorial_implemented_decisions": factorial_decisions.loc[
            factorial_decisions["implemented"]
        ].copy(),
        "factorial_realized_monthly": factorial_realized,
        "history_window_realized_monthly": history_realized,
        "gap_method_realized_monthly": gap_experiments["test_realized"],
        "service_formulation_realized_monthly": service_realized,
        "centered_paired_scenarios": first_scenarios,
        "scenario_mean_diagnostics": scenario_mean_diagnostics,
        "metric_sign_audit": metric_sign_audit,
        "stochastic_value_summary": stochastic_value,
        "cost_scenario_registry": cost_registry,
        "plot_manifest": plot_manifest,
    }
    for name, value in outputs.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(config.output_dir / f"{name}.csv", index=False)
    (config.output_dir / "revised_optimization_summary.md").write_text(
        markdown, encoding="utf-8"
    )

    manifest = {
        "status": "complete",
        "run_date": pd.Timestamp.now().isoformat(),
        "data_frequency": "monthly",
        "data_scope": "national all-acute-trust aggregate",
        "observation_count": len(target_df),
        "sealed_test_start": str(FINAL_TEST_START.date()),
        "sealed_test_end": str(FINAL_TEST_END.date()),
        "legacy_outputs_preserved": True,
        "legacy_output_dir": str(config.legacy_output_dir),
        "canonical_forecast_winners_changed": False,
        "planning_horizons": list(PLANNING_HORIZONS),
        "update_frequencies": list(UPDATE_FREQUENCIES),
        "scenario_support": 7,
        "scenario_weights": "equal 1/7 after target-horizon centering",
        "cost_scenario_count": len(cost_registry),
        "costs_validated": False,
        "workbook_sha256": _sha256(config.workbook_path),
        "python_version": platform.python_version(),
        "pulp_version": pulp.__version__,
        "outputs": sorted(
            [f"{name}.csv" for name, value in outputs.items() if isinstance(value, pd.DataFrame)]
            + ["optimization_forensic_audit.md", "revised_optimization_summary.md"]
        ),
    }
    (config.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    outputs["revised_optimization_summary"] = markdown
    outputs["run_manifest"] = manifest
    return outputs


def load_revision_outputs(output_dir: Path = REVISION_OUTPUT_DIR) -> Dict[str, object]:
    """Load cached revised artifacts for the presentation notebook."""

    manifest_path = output_dir / "run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No completed revision exists in {output_dir}. Run optimization_revision.py."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts: Dict[str, object] = {"run_manifest": manifest}
    for filename in manifest["outputs"]:
        path = output_dir / filename
        if filename.endswith(".csv"):
            artifacts[filename.removesuffix(".csv")] = pd.read_csv(path)
    artifacts["optimization_forensic_audit"] = (
        output_dir / "optimization_forensic_audit.md"
    ).read_text(encoding="utf-8")
    artifacts["revised_optimization_summary"] = (
        output_dir / "revised_optimization_summary.md"
    ).read_text(encoding="utf-8")
    return artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("Beds-publication-Timeseries-March-2020-May-2026.xlsx"),
    )
    parser.add_argument("--output-dir", type=Path, default=REVISION_OUTPUT_DIR)
    parser.add_argument("--no-plots", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    artifacts = run_revision(
        RevisionConfig(
            workbook_path=args.workbook,
            output_dir=args.output_dir,
            generate_plots=not args.no_plots,
        )
    )
    print("\nPrimary revised baseline comparison:")
    print(
        artifacts["baseline_comparison"][[
            "policy",
            "total_realized_cost",
            "shortage_bed_months",
            "service_level",
            "regret",
        ]].to_string(index=False)
    )


if __name__ == "__main__":
    main()
