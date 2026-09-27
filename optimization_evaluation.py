"""Forecast-to-optimization evaluation for NHS monthly capacity planning.

The module consumes the already locked deterministic and uncertainty selections.
It does not re-select forecasting models.  It evaluates how point forecasts,
audited marginal bounds, paired residual scenarios, and forecast-update
frequency affect a small linear capacity-planning model.

All monetary values in the default configuration are illustrative sensitivity
values.  They are not NHS-validated marginal costs.  The sealed September 2025
to May 2026 period is used only for final out-of-sample decision evaluation.
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pulp
import seaborn as sns

from forecasting_evaluation import (
    FINAL_TEST_END,
    FINAL_TEST_START,
    TARGETS,
    EvaluationConfig,
    ModelSpec,
    apply_domain_bounds,
    build_model_registry,
    forecast_selected_models,
    load_nhs_targets,
    seasonal_naive_forecast,
    usable_evaluation_frame,
)
from uncertainty_evaluation import (
    METHOD_CQR,
    UncertaintyConfig,
    _interval_from_method,
    build_selected_validation_predictions,
    recursive_quantile_forecast,
)


AVAILABLE_TARGET = "g_and_a_beds_available"
OCCUPIED_TARGET = "g_and_a_beds_occupied"
OCCUPANCY_TARGET = "g_and_a_occupancy_rate"
LOS_TARGETS = [
    "los_count_7_or_more_days",
    "los_count_14_or_more_days",
    "los_count_21_or_more_days",
    "los_pct_7_or_more_days",
    "los_pct_14_or_more_days",
    "los_pct_21_or_more_days",
]
PRIMARY_LOS_SIGNAL = "los_count_21_or_more_days"

IMPLEMENTABLE_POLICIES = (
    "reactive_baseline",
    "point_forecast",
    "moderate_90",
    "conservative_95",
    "stochastic_scenarios",
)
ALL_POLICIES = IMPLEMENTABLE_POLICIES + ("perfect_information_oracle",)
UPDATE_FREQUENCIES = (1, 3, 6)
OCCUPANCY_THRESHOLDS = (0.85, 0.90, 0.92, 0.95)
HISTORY_WINDOWS = (12, 24, 36, None)
CENTRAL_PARAMETER_SET_ID = "illustrative_central_v1"

EXPECTED_LOCKED_WINNERS = {
    AVAILABLE_TARGET: "RandomForestRegressor_target_only_features",
    OCCUPIED_TARGET: "ExtraTreesRegressor_target_only_features",
    OCCUPANCY_TARGET: "seasonal_naive",
    "los_count_7_or_more_days": "ExtraTreesRegressor_target_only_features",
    "los_count_14_or_more_days": "ExtraTreesRegressor_target_only_features",
    "los_count_21_or_more_days": "seasonal_naive",
    "los_pct_7_or_more_days": "seasonal_naive",
    "los_pct_14_or_more_days": "XGBoost_target_only_features",
    "los_pct_21_or_more_days": "ExtraTreesRegressor_multivariate_lag_features",
}


@dataclass(frozen=True)
class CostParameters:
    """Illustrative central parameters shared by every compared policy."""

    active_staffed_bed_cost: float = 500.0
    capacity_addition_cost: float = 500.0
    capacity_removal_cost: float = 250.0
    shortage_penalty: float = 5000.0
    capacity_change_penalty: float = 50.0
    maximum_additional_capacity: float = 15000.0
    monthly_ramp_up_limit: float = 5000.0
    monthly_ramp_down_limit: float = 5000.0
    parameter_set_id: str = CENTRAL_PARAMETER_SET_ID
    validated: bool = False

    def validate(self) -> None:
        numeric = [
            self.active_staffed_bed_cost,
            self.capacity_addition_cost,
            self.capacity_removal_cost,
            self.shortage_penalty,
            self.capacity_change_penalty,
            self.maximum_additional_capacity,
            self.monthly_ramp_up_limit,
            self.monthly_ramp_down_limit,
        ]
        if any(value < 0 or not np.isfinite(value) for value in numeric):
            raise ValueError("Cost and operational sensitivity values must be finite and nonnegative.")
        if self.maximum_additional_capacity <= 0:
            raise ValueError("maximum_additional_capacity must be positive.")


@dataclass(frozen=True)
class OptimizationEvaluationConfig:
    """Paths and fixed evaluation settings for the optimization experiment."""

    workbook_path: Path = Path("Beds-publication-Timeseries-March-2020-May-2026.xlsx")
    sheet_name: str = "Timeseries all acute trusts"
    deterministic_output_dir: Path = Path("outputs/final_evaluation")
    uncertainty_output_dir: Path = Path("outputs/final_uncertainty")
    output_dir: Path = Path("outputs/final_optimization")
    final_test_start: pd.Timestamp = FINAL_TEST_START
    final_test_end: pd.Timestamp = FINAL_TEST_END
    planning_horizon: int = 9
    update_frequencies: Tuple[int, ...] = UPDATE_FREQUENCIES
    occupancy_thresholds: Tuple[float, ...] = OCCUPANCY_THRESHOLDS
    scenario_count: int = 200
    random_seed: int = 42
    cvar_weight: float = 0.0
    cvar_alpha: float = 0.90
    los_pressure_mode: bool = False
    primary_los_signal: str = PRIMARY_LOS_SIGNAL
    los_trigger_quantile: float = 0.90
    los_shortage_penalty_multiplier: float = 1.25
    generate_plots: bool = True
    costs: CostParameters = field(default_factory=CostParameters)

    def validate(self) -> None:
        self.costs.validate()
        if self.planning_horizon != 9:
            raise ValueError("The locked forecasting protocol uses a nine-month planning horizon.")
        if set(self.update_frequencies) != set(UPDATE_FREQUENCIES):
            raise ValueError("Update frequencies must be exactly 1, 3, and 6 months.")
        if any(rho <= 0 or rho >= 1 for rho in self.occupancy_thresholds):
            raise ValueError("Occupancy thresholds must lie strictly between zero and one.")
        if self.scenario_count < 7:
            raise ValueError("At least seven stochastic scenarios are required.")
        if self.cvar_weight < 0 or not 0 < self.cvar_alpha < 1:
            raise ValueError("Invalid CVaR configuration.")
        if self.primary_los_signal not in LOS_TARGETS:
            raise ValueError("primary_los_signal must be an LOS target.")
        if not 0 < self.los_trigger_quantile < 1:
            raise ValueError("los_trigger_quantile must lie strictly between zero and one.")
        if self.los_pressure_mode:
            warnings.warn(
                "LOS pressure mode is enabled with illustrative weights and requires stakeholder approval.",
                RuntimeWarning,
            )


def validate_locked_selection(selection: pd.DataFrame) -> None:
    """Verify that the canonical winners are complete and still test-locked."""

    required = {
        "target",
        "selected_model",
        "feature_mode",
        "selection_was_locked_before_test",
    }
    missing = required - set(selection.columns)
    if missing:
        raise KeyError(f"Canonical selection is missing columns: {sorted(missing)}")
    if set(selection["target"]) != set(TARGETS) or len(selection) != len(TARGETS):
        raise AssertionError("Canonical selection must contain exactly one row per target.")
    locked = selection["selection_was_locked_before_test"].astype(str).str.lower().eq("true")
    if not locked.all():
        raise AssertionError("Every forecasting winner must be locked before the final test.")
    actual = selection.set_index("target")["selected_model"].to_dict()
    if actual != EXPECTED_LOCKED_WINNERS:
        differences = {
            target: {"expected": EXPECTED_LOCKED_WINNERS[target], "found": actual.get(target)}
            for target in TARGETS
            if actual.get(target) != EXPECTED_LOCKED_WINNERS[target]
        }
        raise AssertionError(f"Canonical forecast winners changed unexpectedly: {differences}")


def load_locked_project_inputs(
    config: OptimizationEvaluationConfig,
) -> Dict[str, pd.DataFrame]:
    """Load all authoritative forecast and uncertainty artifacts."""

    evaluation_config = EvaluationConfig(
        workbook_path=config.workbook_path,
        sheet_name=config.sheet_name,
        output_dir=config.deterministic_output_dir,
        generate_plots=False,
        mark_uncertainty_stale=False,
    )
    target_df = load_nhs_targets(evaluation_config)
    selection = pd.read_csv(
        config.deterministic_output_dir / "final_model_selection_with_deep.csv"
    )
    validate_locked_selection(selection)

    frames = {
        "target_df": target_df,
        "selection": selection,
        "rolling_predictions": pd.read_csv(
            config.deterministic_output_dir / "rolling_origin_predictions.csv",
            parse_dates=["forecast_origin", "forecast_date"],
        ),
        "rolling_summary": pd.read_csv(
            config.deterministic_output_dir / "rolling_origin_model_summary.csv"
        ),
        "final_predictions": pd.read_csv(
            config.deterministic_output_dir / "final_test_predictions_with_deep.csv",
            parse_dates=["forecast_origin", "forecast_date"],
        ),
        "final_metrics": pd.read_csv(
            config.deterministic_output_dir / "final_test_metrics_with_deep.csv"
        ),
        "uncertainty_selection": pd.read_csv(
            config.uncertainty_output_dir / "uncertainty_selection_audited.csv"
        ),
        "uncertainty_validation": pd.read_csv(
            config.uncertainty_output_dir / "uncertainty_validation_predictions.csv",
            parse_dates=["forecast_origin", "forecast_date"],
        ),
        "audited_test_intervals": pd.read_csv(
            config.uncertainty_output_dir / "audited_final_test_intervals.csv",
            parse_dates=["forecast_date"],
        ),
        "cqr_validation": pd.read_csv(
            config.uncertainty_output_dir / "cqr_raw_validation_predictions.csv",
            parse_dates=["forecast_origin", "forecast_date"],
        ),
        "stress_residual_path": pd.read_csv(
            config.uncertainty_output_dir / "stress_residual_path.csv"
        ),
    }
    return frames


def build_optimization_parameter_registry(
    config: OptimizationEvaluationConfig,
) -> pd.DataFrame:
    """Create a source-audited registry without claiming validated NHS costs."""

    costs = config.costs
    issue_path = "Private original task brief (not distributed); see README.md"
    common_cost_note = (
        "Illustrative sensitivity value only. No local or authenticated GitLab evidence "
        "validated this as the marginal NHS cost of operational capacity."
    )
    rows = [
        {
            "parameter": "active_staffed_bed_cost",
            "symbol": "c_active_t",
            "value": costs.active_staffed_bed_cost,
            "low_value": 250.0,
            "central_value": costs.active_staffed_bed_cost,
            "high_value": 1000.0,
            "unit": "GBP per additional operational bed-equivalent-month",
            "currency": "GBP",
            "meaning": "Cost of staffed, funded, equipped and operational additional capacity active in month t",
            "source_type": "illustrative sensitivity value",
            "source_file_or_issue": issue_path,
            "source_location": "Cost audit note referring to an approximately GBP 500-like value",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": common_cost_note,
        },
        {
            "parameter": "capacity_addition_cost",
            "symbol": "c_add_t",
            "value": costs.capacity_addition_cost,
            "low_value": 250.0,
            "central_value": costs.capacity_addition_cost,
            "high_value": 1000.0,
            "unit": "GBP per bed-equivalent activated",
            "currency": "GBP",
            "meaning": "One-off activation, recruitment, equipment and estates sensitivity cost",
            "source_type": "project assumption",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "CostParameters.capacity_addition_cost",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": common_cost_note,
        },
        {
            "parameter": "capacity_removal_cost",
            "symbol": "c_remove_t",
            "value": costs.capacity_removal_cost,
            "low_value": 100.0,
            "central_value": costs.capacity_removal_cost,
            "high_value": 500.0,
            "unit": "GBP per bed-equivalent removed",
            "currency": "GBP",
            "meaning": "One-off safe reduction, redeployment or closure sensitivity cost",
            "source_type": "project assumption",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "CostParameters.capacity_removal_cost",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": common_cost_note,
        },
        {
            "parameter": "shortage_penalty",
            "symbol": "c_short_t",
            "value": costs.shortage_penalty,
            "low_value": 2500.0,
            "central_value": costs.shortage_penalty,
            "high_value": 10000.0,
            "unit": "GBP per unmet occupancy-adjusted bed-equivalent-month",
            "currency": "GBP",
            "meaning": "Illustrative operational and service penalty for unmet capacity",
            "source_type": "illustrative sensitivity value",
            "source_file_or_issue": issue_path,
            "source_location": "Cost audit note referring to an approximately GBP 5,000-like value",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": common_cost_note,
        },
        {
            "parameter": "capacity_change_penalty",
            "symbol": "c_change_t",
            "value": costs.capacity_change_penalty,
            "low_value": 0.0,
            "central_value": costs.capacity_change_penalty,
            "high_value": 100.0,
            "unit": "GBP per bed-equivalent changed",
            "currency": "GBP",
            "meaning": "Optional sensitivity penalty discouraging volatile additions and removals",
            "source_type": "project assumption",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "CostParameters.capacity_change_penalty",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": common_cost_note,
        },
        {
            "parameter": "maximum_additional_capacity",
            "symbol": "X_max_t",
            "value": costs.maximum_additional_capacity,
            "low_value": 10000.0,
            "central_value": costs.maximum_additional_capacity,
            "high_value": 20000.0,
            "unit": "operational bed-equivalents",
            "currency": "",
            "meaning": "Maximum additional staffed and deployable capacity",
            "source_type": "illustrative sensitivity value",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "CostParameters.maximum_additional_capacity",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": "Operational feasibility requires local workforce, estates and equipment validation.",
        },
        {
            "parameter": "monthly_ramp_up_limit",
            "symbol": "U_t",
            "value": costs.monthly_ramp_up_limit,
            "low_value": 2000.0,
            "central_value": costs.monthly_ramp_up_limit,
            "high_value": 8000.0,
            "unit": "operational bed-equivalents per month",
            "currency": "",
            "meaning": "Maximum increase in active additional capacity in one month",
            "source_type": "illustrative sensitivity value",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "CostParameters.monthly_ramp_up_limit",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": "Requires operations and workforce validation.",
        },
        {
            "parameter": "monthly_ramp_down_limit",
            "symbol": "V_t",
            "value": costs.monthly_ramp_down_limit,
            "low_value": 2000.0,
            "central_value": costs.monthly_ramp_down_limit,
            "high_value": 8000.0,
            "unit": "operational bed-equivalents per month",
            "currency": "",
            "meaning": "Maximum reduction in active additional capacity in one month",
            "source_type": "illustrative sensitivity value",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "CostParameters.monthly_ramp_down_limit",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": "Requires operations and workforce validation.",
        },
        {
            "parameter": "occupancy_threshold",
            "symbol": "rho_max",
            "value": 0.90,
            "low_value": 0.85,
            "central_value": 0.90,
            "high_value": 0.95,
            "unit": "proportion",
            "currency": "",
            "meaning": "Planning sensitivity threshold, evaluated at 0.85, 0.90, 0.92 and 0.95",
            "source_type": "project assumption",
            "source_file_or_issue": "uncertainty_optimization_audit.py",
            "source_location": "AuditConfig.rho_max_values",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": "No value is presented as a universal NHS occupancy target.",
        },
        {
            "parameter": "stochastic_scenario_count",
            "symbol": "|S|",
            "value": config.scenario_count,
            "low_value": 100,
            "central_value": config.scenario_count,
            "high_value": 500,
            "unit": "paired residual paths",
            "currency": "",
            "meaning": "Number of equally weighted paired available/occupied scenarios",
            "source_type": "project assumption",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "OptimizationEvaluationConfig.scenario_count",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": "Scenarios resample complete development paths and preserve cross-target and temporal dependence.",
        },
        {
            "parameter": "CVaR_weight",
            "symbol": "lambda_CVaR",
            "value": config.cvar_weight,
            "low_value": 0.0,
            "central_value": 0.0,
            "high_value": 1.0,
            "unit": "objective weight",
            "currency": "",
            "meaning": "Optional weight on CVaR of scenario shortage cost",
            "source_type": "project assumption",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "OptimizationEvaluationConfig.cvar_weight",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": "Disabled in the base evaluation.",
        },
        {
            "parameter": "primary_LOS_signal",
            "symbol": "L_primary",
            "value": config.primary_los_signal,
            "low_value": "",
            "central_value": config.primary_los_signal,
            "high_value": "",
            "unit": "target name",
            "currency": "",
            "meaning": "Optional severe-flow-pressure signal; LOS is never added to occupied census",
            "source_type": "project assumption",
            "source_file_or_issue": "optimization_evaluation.py",
            "source_location": "PRIMARY_LOS_SIGNAL",
            "validated": False,
            "stakeholder_validation_required": True,
            "notes": "LOS pressure mode is disabled by default.",
        },
    ]
    registry = pd.DataFrame(rows)
    required = [
        "parameter",
        "symbol",
        "value",
        "low_value",
        "central_value",
        "high_value",
        "unit",
        "currency",
        "meaning",
        "source_type",
        "source_file_or_issue",
        "source_location",
        "validated",
        "stakeholder_validation_required",
        "notes",
    ]
    return registry[required]


def _as_monthly_input(
    dates: Sequence[pd.Timestamp],
    available: Sequence[float],
    demand: Sequence[float],
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "forecast_month": pd.DatetimeIndex(dates),
            "available_capacity_input": np.asarray(available, dtype=float),
            "occupied_demand_input": np.asarray(demand, dtype=float),
        }
    )
    if frame.empty or frame["forecast_month"].duplicated().any():
        raise ValueError("Optimization inputs require unique monthly dates.")
    if not frame["forecast_month"].is_monotonic_increasing:
        raise ValueError("Optimization inputs must be chronological.")
    if not np.isfinite(frame[["available_capacity_input", "occupied_demand_input"]]).all().all():
        raise ValueError("Optimization inputs must be finite.")
    if (frame[["available_capacity_input", "occupied_demand_input"]] < 0).any().any():
        raise ValueError("Optimization inputs cannot be negative.")
    return frame


def solve_deterministic_capacity_plan(
    dates: Sequence[pd.Timestamp],
    available: Sequence[float],
    demand: Sequence[float],
    rho_max: float,
    costs: CostParameters,
    initial_capacity: float = 0.0,
    shortage_penalty_multipliers: Optional[Sequence[float]] = None,
    policy: str = "point_forecast",
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Solve the deterministic linear capacity plan with PuLP/CBC."""

    costs.validate()
    if not 0 < rho_max < 1:
        raise ValueError("rho_max must lie strictly between zero and one.")
    frame = _as_monthly_input(dates, available, demand)
    n_months = len(frame)
    multipliers = (
        np.ones(n_months, dtype=float)
        if shortage_penalty_multipliers is None
        else np.asarray(shortage_penalty_multipliers, dtype=float)
    )
    if len(multipliers) != n_months or (multipliers <= 0).any():
        raise ValueError("Shortage penalty multipliers must be positive and align with dates.")
    tolerance = 1e-5
    if (
        initial_capacity < -tolerance
        or initial_capacity > costs.maximum_additional_capacity + tolerance
    ):
        raise ValueError("initial_capacity violates the maximum-capacity bound.")
    initial_capacity = float(
        np.clip(initial_capacity, 0.0, costs.maximum_additional_capacity)
    )

    problem = pulp.LpProblem(f"nhs_capacity_{policy}", pulp.LpMinimize)
    x = {
        t: pulp.LpVariable(
            f"x_{t}", lowBound=0, upBound=costs.maximum_additional_capacity
        )
        for t in range(n_months)
    }
    added = {
        t: pulp.LpVariable(
            f"a_{t}", lowBound=0, upBound=costs.monthly_ramp_up_limit
        )
        for t in range(n_months)
    }
    removed = {
        t: pulp.LpVariable(
            f"r_{t}", lowBound=0, upBound=costs.monthly_ramp_down_limit
        )
        for t in range(n_months)
    }
    shortage = {t: pulp.LpVariable(f"s_{t}", lowBound=0) for t in range(n_months)}
    unused = {t: pulp.LpVariable(f"u_{t}", lowBound=0) for t in range(n_months)}

    for t, row in frame.iterrows():
        previous = initial_capacity if t == 0 else x[t - 1]
        problem += x[t] == previous + added[t] - removed[t], f"transition_{t}"
        required = float(row["occupied_demand_input"]) / rho_max
        problem += (
            float(row["available_capacity_input"]) + x[t] + shortage[t]
            == required + unused[t]
        ), f"capacity_balance_{t}"

    problem += pulp.lpSum(
        costs.active_staffed_bed_cost * x[t]
        + costs.capacity_addition_cost * added[t]
        + costs.capacity_removal_cost * removed[t]
        + costs.capacity_change_penalty * (added[t] + removed[t])
        + costs.shortage_penalty * float(multipliers[t]) * shortage[t]
        for t in range(n_months)
    )

    solver = pulp.PULP_CBC_CMD(msg=False, threads=1)
    status_code = problem.solve(solver)
    status = pulp.LpStatus[status_code]
    if status != "Optimal":
        raise RuntimeError(f"CBC did not find an optimal deterministic plan: {status}")

    rows: List[Dict[str, object]] = []
    previous = float(initial_capacity)
    for t, input_row in frame.iterrows():
        x_value = float(pulp.value(x[t]))
        add_value = float(pulp.value(added[t]))
        remove_value = float(pulp.value(removed[t]))
        shortage_value = float(pulp.value(shortage[t]))
        unused_value = float(pulp.value(unused[t]))
        required = float(input_row["occupied_demand_input"]) / rho_max
        balance_lhs = (
            float(input_row["available_capacity_input"]) + x_value + shortage_value
        )
        transition_residual = x_value - (previous + add_value - remove_value)
        rows.append(
            {
                "forecast_month": input_row["forecast_month"],
                "horizon_step": t + 1,
                "policy": policy,
                "rho_max": rho_max,
                "available_capacity_input": float(input_row["available_capacity_input"]),
                "occupied_demand_input": float(input_row["occupied_demand_input"]),
                "required_capacity_input": required,
                "initial_capacity_for_month": previous,
                "additional_capacity_active": x_value,
                "capacity_added": add_value,
                "capacity_removed": remove_value,
                "planned_shortage": shortage_value,
                "planned_unused_capacity": unused_value,
                "capacity_balance_residual": balance_lhs - (required + unused_value),
                "transition_residual": transition_residual,
                "maximum_capacity_limit": costs.maximum_additional_capacity,
                "ramp_up_limit": costs.monthly_ramp_up_limit,
                "ramp_down_limit": costs.monthly_ramp_down_limit,
                "shortage_penalty_multiplier": float(multipliers[t]),
                "scenario_count": 1,
                "solver_status": status,
                "planned_objective_value": float(pulp.value(problem.objective)),
                "parameter_set_id": costs.parameter_set_id,
            }
        )
        previous = x_value
    decisions = pd.DataFrame(rows)
    metadata = {
        "solver": "PuLP CBC",
        "solver_status": status,
        "objective_value": float(pulp.value(problem.objective)),
        "cvar_enabled": False,
        "scenario_count": 1,
    }
    return decisions, metadata


def _validate_scenario_frame(
    scenarios: pd.DataFrame,
    dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    required = {
        "scenario_id",
        "forecast_month",
        "available_capacity",
        "occupied_demand",
        "probability",
    }
    missing = required - set(scenarios.columns)
    if missing:
        raise KeyError(f"Scenario frame is missing columns: {sorted(missing)}")
    result = scenarios.copy()
    result["forecast_month"] = pd.to_datetime(result["forecast_month"])
    if set(result["forecast_month"]) != set(dates):
        raise AssertionError("Every scenario must align with the planning months.")
    counts = result.groupby("scenario_id")["forecast_month"].nunique()
    if not counts.eq(len(dates)).all():
        raise AssertionError("Every scenario must contain every planning month exactly once.")
    probabilities = result.groupby("scenario_id")["probability"].first()
    if not np.isclose(probabilities.sum(), 1.0):
        raise AssertionError("Scenario probabilities must sum to one.")
    if (result[["available_capacity", "occupied_demand", "probability"]] < 0).any().any():
        raise ValueError("Scenario values and probabilities must be nonnegative.")
    return result.sort_values(["scenario_id", "forecast_month"]).reset_index(drop=True)


def solve_stochastic_capacity_plan(
    dates: Sequence[pd.Timestamp],
    scenarios: pd.DataFrame,
    rho_max: float,
    costs: CostParameters,
    initial_capacity: float = 0.0,
    cvar_weight: float = 0.0,
    cvar_alpha: float = 0.90,
    policy: str = "stochastic_scenarios",
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, object]]:
    """Solve a shared-capacity stochastic LP with scenario recourse shortage."""

    costs.validate()
    if not 0 < rho_max < 1:
        raise ValueError("rho_max must lie strictly between zero and one.")
    if cvar_weight < 0 or not 0 < cvar_alpha < 1:
        raise ValueError("Invalid CVaR settings.")
    tolerance = 1e-5
    if (
        initial_capacity < -tolerance
        or initial_capacity > costs.maximum_additional_capacity + tolerance
    ):
        raise ValueError("initial_capacity violates the maximum-capacity bound.")
    initial_capacity = float(
        np.clip(initial_capacity, 0.0, costs.maximum_additional_capacity)
    )
    dates_index = pd.DatetimeIndex(dates)
    scenario_frame = _validate_scenario_frame(scenarios, dates_index)
    scenario_ids = list(scenario_frame["scenario_id"].drop_duplicates())
    probabilities = (
        scenario_frame.groupby("scenario_id")["probability"].first().to_dict()
    )
    lookup = scenario_frame.set_index(["scenario_id", "forecast_month"])
    n_months = len(dates_index)

    problem = pulp.LpProblem("nhs_capacity_stochastic", pulp.LpMinimize)
    x = {
        t: pulp.LpVariable(
            f"x_{t}", lowBound=0, upBound=costs.maximum_additional_capacity
        )
        for t in range(n_months)
    }
    added = {
        t: pulp.LpVariable(
            f"a_{t}", lowBound=0, upBound=costs.monthly_ramp_up_limit
        )
        for t in range(n_months)
    }
    removed = {
        t: pulp.LpVariable(
            f"r_{t}", lowBound=0, upBound=costs.monthly_ramp_down_limit
        )
        for t in range(n_months)
    }
    shortage = {
        (scenario_id, t): pulp.LpVariable(f"s_{scenario_id}_{t}", lowBound=0)
        for scenario_id in scenario_ids
        for t in range(n_months)
    }
    unused = {
        (scenario_id, t): pulp.LpVariable(f"u_{scenario_id}_{t}", lowBound=0)
        for scenario_id in scenario_ids
        for t in range(n_months)
    }

    for t in range(n_months):
        previous = initial_capacity if t == 0 else x[t - 1]
        problem += x[t] == previous + added[t] - removed[t], f"transition_{t}"
    for scenario_id in scenario_ids:
        for t, month in enumerate(dates_index):
            row = lookup.loc[(scenario_id, month)]
            required = float(row["occupied_demand"]) / rho_max
            problem += (
                float(row["available_capacity"])
                + x[t]
                + shortage[(scenario_id, t)]
                == required + unused[(scenario_id, t)]
            ), f"capacity_{scenario_id}_{t}"

    deterministic_cost = pulp.lpSum(
        costs.active_staffed_bed_cost * x[t]
        + costs.capacity_addition_cost * added[t]
        + costs.capacity_removal_cost * removed[t]
        + costs.capacity_change_penalty * (added[t] + removed[t])
        for t in range(n_months)
    )
    scenario_shortage_cost = {
        scenario_id: pulp.lpSum(
            costs.shortage_penalty * shortage[(scenario_id, t)]
            for t in range(n_months)
        )
        for scenario_id in scenario_ids
    }
    expected_shortage_cost = pulp.lpSum(
        float(probabilities[scenario_id]) * scenario_shortage_cost[scenario_id]
        for scenario_id in scenario_ids
    )
    objective = deterministic_cost + expected_shortage_cost

    cvar_enabled = cvar_weight > 0
    if cvar_enabled:
        eta = pulp.LpVariable("cvar_eta", lowBound=0)
        excess = {
            scenario_id: pulp.LpVariable(f"cvar_excess_{scenario_id}", lowBound=0)
            for scenario_id in scenario_ids
        }
        for scenario_id in scenario_ids:
            problem += (
                excess[scenario_id] >= scenario_shortage_cost[scenario_id] - eta
            ), f"cvar_excess_constraint_{scenario_id}"
        cvar = eta + (1.0 / (1.0 - cvar_alpha)) * pulp.lpSum(
            float(probabilities[scenario_id]) * excess[scenario_id]
            for scenario_id in scenario_ids
        )
        objective += cvar_weight * cvar
    problem += objective

    solver = pulp.PULP_CBC_CMD(msg=False, threads=1)
    status_code = problem.solve(solver)
    status = pulp.LpStatus[status_code]
    if status != "Optimal":
        raise RuntimeError(f"CBC did not find an optimal stochastic plan: {status}")

    scenario_rows: List[Dict[str, object]] = []
    for scenario_id in scenario_ids:
        probability = float(probabilities[scenario_id])
        for t, month in enumerate(dates_index):
            row = lookup.loc[(scenario_id, month)]
            x_value = float(pulp.value(x[t]))
            shortage_value = float(pulp.value(shortage[(scenario_id, t)]))
            unused_value = float(pulp.value(unused[(scenario_id, t)]))
            required = float(row["occupied_demand"]) / rho_max
            balance = (
                float(row["available_capacity"])
                + x_value
                + shortage_value
                - required
                - unused_value
            )
            scenario_rows.append(
                {
                    "scenario_id": scenario_id,
                    "probability": probability,
                    "forecast_month": month,
                    "horizon_step": t + 1,
                    "available_capacity": float(row["available_capacity"]),
                    "occupied_demand": float(row["occupied_demand"]),
                    "additional_capacity_active": x_value,
                    "scenario_shortage": shortage_value,
                    "scenario_unused_capacity": unused_value,
                    "capacity_balance_residual": balance,
                }
            )
    scenario_solution = pd.DataFrame(scenario_rows)

    rows: List[Dict[str, object]] = []
    previous = float(initial_capacity)
    for t, month in enumerate(dates_index):
        month_scenarios = scenario_solution.loc[
            scenario_solution["forecast_month"].eq(month)
        ]
        weights = month_scenarios["probability"].to_numpy(dtype=float)
        expected_available = float(
            np.average(month_scenarios["available_capacity"], weights=weights)
        )
        expected_demand = float(
            np.average(month_scenarios["occupied_demand"], weights=weights)
        )
        expected_shortage = float(
            np.average(month_scenarios["scenario_shortage"], weights=weights)
        )
        expected_unused = float(
            np.average(month_scenarios["scenario_unused_capacity"], weights=weights)
        )
        x_value = float(pulp.value(x[t]))
        add_value = float(pulp.value(added[t]))
        remove_value = float(pulp.value(removed[t]))
        rows.append(
            {
                "forecast_month": month,
                "horizon_step": t + 1,
                "policy": policy,
                "rho_max": rho_max,
                "available_capacity_input": expected_available,
                "occupied_demand_input": expected_demand,
                "required_capacity_input": expected_demand / rho_max,
                "initial_capacity_for_month": previous,
                "additional_capacity_active": x_value,
                "capacity_added": add_value,
                "capacity_removed": remove_value,
                "planned_shortage": expected_shortage,
                "planned_unused_capacity": expected_unused,
                "capacity_balance_residual": float(
                    month_scenarios["capacity_balance_residual"].abs().max()
                ),
                "transition_residual": x_value
                - (previous + add_value - remove_value),
                "maximum_capacity_limit": costs.maximum_additional_capacity,
                "ramp_up_limit": costs.monthly_ramp_up_limit,
                "ramp_down_limit": costs.monthly_ramp_down_limit,
                "shortage_penalty_multiplier": 1.0,
                "scenario_count": len(scenario_ids),
                "solver_status": status,
                "planned_objective_value": float(pulp.value(problem.objective)),
                "parameter_set_id": costs.parameter_set_id,
            }
        )
        previous = x_value
    decisions = pd.DataFrame(rows)
    metadata = {
        "solver": "PuLP CBC",
        "solver_status": status,
        "objective_value": float(pulp.value(problem.objective)),
        "cvar_enabled": cvar_enabled,
        "cvar_weight": cvar_weight,
        "cvar_alpha": cvar_alpha,
        "scenario_count": len(scenario_ids),
    }
    return decisions, scenario_solution, metadata


def monthly_backtest_origins(
    final_test_start: pd.Timestamp,
    final_test_end: pd.Timestamp,
) -> pd.DatetimeIndex:
    """Return every possible monthly forecast origin before the sealed outcomes."""

    first_origin = pd.Timestamp(final_test_start) - pd.offsets.MonthBegin(1)
    last_origin = pd.Timestamp(final_test_end) - pd.offsets.MonthBegin(1)
    return pd.date_range(first_origin, last_origin, freq="MS")


def forecast_dates_from_origin(
    origin: pd.Timestamp,
    final_test_end: pd.Timestamp,
    horizon: int = 9,
) -> pd.DatetimeIndex:
    """Create a nine-month path, transparently truncated at the backtest end."""

    dates = pd.date_range(
        pd.Timestamp(origin) + pd.offsets.MonthBegin(1),
        periods=horizon,
        freq="MS",
    )
    return dates[dates <= pd.Timestamp(final_test_end)]


def training_dates_for_origin(
    target_df: pd.DataFrame,
    origin: pd.Timestamp,
    history_window: Optional[int] = None,
) -> pd.DatetimeIndex:
    """Select chronological training targets while retaining lag-source history."""

    eligible = usable_evaluation_frame(target_df).index[
        usable_evaluation_frame(target_df).index <= pd.Timestamp(origin)
    ]
    if history_window is not None:
        if history_window not in {12, 24, 36}:
            raise ValueError("history_window must be 12, 24, 36, or None.")
        eligible = eligible[-history_window:]
    if len(eligible) < 12:
        raise ValueError("At least 12 monthly target observations are required.")
    return pd.DatetimeIndex(eligible)


def build_locked_point_forecast_cache(
    target_df: pd.DataFrame,
    selection: pd.DataFrame,
    specs: Sequence[ModelSpec],
    origins: Sequence[pd.Timestamp],
    final_test_end: pd.Timestamp,
    seed: int,
    horizon: int = 9,
    history_window: Optional[int] = None,
) -> pd.DataFrame:
    """Refit the locked winner at each origin without reading future outcomes."""

    validate_locked_selection(selection)
    rows: List[Dict[str, object]] = []
    for origin in pd.DatetimeIndex(origins):
        training_dates = training_dates_for_origin(target_df, origin, history_window)
        forecast_dates = forecast_dates_from_origin(origin, final_test_end, horizon)
        forecast = forecast_selected_models(
            target_df,
            training_dates,
            forecast_dates,
            selection,
            specs,
            seed,
        )
        for horizon_step, forecast_date in enumerate(forecast_dates, start=1):
            for target in TARGETS:
                selected = selection.loc[selection["target"].eq(target)].iloc[0]
                rows.append(
                    {
                        "forecast_origin": origin,
                        "forecast_date": forecast_date,
                        "horizon_step": horizon_step,
                        "target": target,
                        "prediction": float(forecast.loc[forecast_date, target]),
                        "selected_model": selected["selected_model"],
                        "feature_mode": selected["feature_mode"],
                        "training_start": training_dates.min(),
                        "training_end": training_dates.max(),
                        "training_observations": len(training_dates),
                        "history_window": (
                            "all_available" if history_window is None else str(history_window)
                        ),
                        "future_actual_used": False,
                    }
                )
    result = pd.DataFrame(rows)
    if result.empty or result["prediction"].isna().any():
        raise AssertionError("Locked point forecast cache is incomplete.")
    if not (
        pd.to_datetime(result["training_end"]) <= pd.to_datetime(result["forecast_origin"])
    ).all():
        raise AssertionError("A locked forecast trained beyond its origin.")
    return result.sort_values(
        ["forecast_origin", "forecast_date", "target"]
    ).reset_index(drop=True)


def build_reactive_forecast_cache(
    target_df: pd.DataFrame,
    origins: Sequence[pd.Timestamp],
    final_test_end: pd.Timestamp,
    horizon: int = 9,
) -> pd.DataFrame:
    """Build a seasonal-naive operational baseline at every update origin."""

    rows: List[Dict[str, object]] = []
    for origin in pd.DatetimeIndex(origins):
        forecast_dates = forecast_dates_from_origin(origin, final_test_end, horizon)
        history = target_df.loc[:origin]
        for target in TARGETS:
            values = seasonal_naive_forecast(history[target], len(forecast_dates))
            values = apply_domain_bounds(target, values)
            for horizon_step, (forecast_date, prediction) in enumerate(
                zip(forecast_dates, values), start=1
            ):
                rows.append(
                    {
                        "forecast_origin": origin,
                        "forecast_date": forecast_date,
                        "horizon_step": horizon_step,
                        "target": target,
                        "prediction": float(prediction),
                        "selected_model": "seasonal_naive",
                        "feature_mode": "univariate",
                        "training_start": history.index.min(),
                        "training_end": history.index.max(),
                        "training_observations": len(history),
                        "history_window": "all_available",
                        "future_actual_used": False,
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["forecast_origin", "forecast_date", "target"]
    ).reset_index(drop=True)


def build_cqr_raw_forecast_cache(
    target_df: pd.DataFrame,
    origins: Sequence[pd.Timestamp],
    final_test_end: pd.Timestamp,
    target: str,
    levels: Sequence[float],
    seed: int,
    horizon: int = 9,
) -> pd.DataFrame:
    """Create fold-local raw quantile forecasts needed by rolling CQR."""

    rows: List[Dict[str, object]] = []
    for origin_index, origin in enumerate(pd.DatetimeIndex(origins)):
        training_dates = training_dates_for_origin(target_df, origin)
        forecast_dates = forecast_dates_from_origin(origin, final_test_end, horizon)
        for level_index, level in enumerate(levels):
            alpha = 1.0 - float(level)
            local_seed = seed + origin_index * 100 + level_index * 10
            lower = recursive_quantile_forecast(
                target,
                target_df,
                training_dates,
                forecast_dates,
                alpha / 2.0,
                local_seed,
            )
            upper = recursive_quantile_forecast(
                target,
                target_df,
                training_dates,
                forecast_dates,
                1.0 - alpha / 2.0,
                local_seed,
            )
            raw_lower = np.minimum(lower, upper)
            raw_upper = np.maximum(lower, upper)
            for horizon_step, forecast_date in enumerate(forecast_dates, start=1):
                rows.append(
                    {
                        "target": target,
                        "forecast_origin": origin,
                        "forecast_date": forecast_date,
                        "horizon_step": horizon_step,
                        "level": float(level),
                        "raw_lower": float(raw_lower[horizon_step - 1]),
                        "raw_upper": float(raw_upper[horizon_step - 1]),
                        "training_end": training_dates.max(),
                        "future_actual_used": False,
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["forecast_origin", "level", "horizon_step"]
    ).reset_index(drop=True)


def _observable_point_calibration(
    target: str,
    origin: pd.Timestamp,
    development_points: pd.DataFrame,
    rolling_cache: pd.DataFrame,
    target_df: pd.DataFrame,
) -> pd.DataFrame:
    """Join actuals only after forecast events are known at an origin."""

    origin = pd.Timestamp(origin)
    development = development_points.loc[
        development_points["target"].eq(target)
        & (development_points["forecast_origin"] < origin)
        & (development_points["forecast_date"] <= origin)
    ][
        [
            "forecast_origin",
            "forecast_date",
            "horizon_step",
            "actual",
            "prediction",
        ]
    ].copy()
    rolling = rolling_cache.loc[
        rolling_cache["target"].eq(target)
        & (rolling_cache["forecast_origin"] < origin)
        & (rolling_cache["forecast_date"] <= origin)
    ][
        ["forecast_origin", "forecast_date", "horizon_step", "prediction"]
    ].copy()
    if not rolling.empty:
        rolling["actual"] = [
            float(target_df.loc[pd.Timestamp(date), target])
            for date in rolling["forecast_date"]
        ]
        rolling = rolling[
            [
                "forecast_origin",
                "forecast_date",
                "horizon_step",
                "actual",
                "prediction",
            ]
        ]
    calibration = pd.concat([development, rolling], ignore_index=True)
    calibration = calibration.sort_values(
        ["forecast_date", "forecast_origin", "horizon_step"]
    ).reset_index(drop=True)
    if calibration.empty:
        raise ValueError(f"No observable calibration records for {target} at {origin}.")
    if pd.to_datetime(calibration["forecast_date"]).max() > origin:
        raise AssertionError("Calibration contains an outcome after the forecast origin.")
    return calibration


def _observable_cqr_scores(
    target: str,
    level: float,
    origin: pd.Timestamp,
    development_cqr: pd.DataFrame,
    rolling_cqr: pd.DataFrame,
    target_df: pd.DataFrame,
) -> np.ndarray:
    """Return CQR scores whose outcomes were observable at the current origin."""

    origin = pd.Timestamp(origin)
    development = development_cqr.loc[
        development_cqr["target"].eq(target)
        & np.isclose(development_cqr["level"], level)
        & development_cqr["status"].eq("ok")
        & (development_cqr["forecast_origin"] < origin)
        & (development_cqr["forecast_date"] <= origin),
        "nonconformity_score",
    ].to_numpy(dtype=float)
    rolling = rolling_cqr.loc[
        rolling_cqr["target"].eq(target)
        & np.isclose(rolling_cqr["level"], level)
        & (rolling_cqr["forecast_origin"] < origin)
        & (rolling_cqr["forecast_date"] <= origin)
    ].copy()
    rolling_scores: List[float] = []
    for row in rolling.itertuples():
        actual = float(target_df.loc[pd.Timestamp(row.forecast_date), target])
        rolling_scores.append(
            max(float(row.raw_lower) - actual, actual - float(row.raw_upper), 0.0)
        )
    scores = np.concatenate(
        [development[np.isfinite(development)], np.asarray(rolling_scores, dtype=float)]
    )
    if len(scores) == 0:
        raise ValueError(f"No observable CQR scores for {target} at {origin}.")
    return scores


def build_rolling_interval_cache(
    target_df: pd.DataFrame,
    point_cache: pd.DataFrame,
    development_points: pd.DataFrame,
    uncertainty_selection: pd.DataFrame,
    development_cqr: pd.DataFrame,
    rolling_cqr: pd.DataFrame,
    config: OptimizationEvaluationConfig,
    targets: Sequence[str] = (AVAILABLE_TARGET, OCCUPIED_TARGET),
    levels: Sequence[float] = (0.90, 0.95),
) -> pd.DataFrame:
    """Rebuild locked intervals at each origin with only observable calibration."""

    uncertainty_config = UncertaintyConfig(
        deterministic_output_dir=config.deterministic_output_dir,
        output_dir=config.uncertainty_output_dir,
        final_selection_path=(
            config.deterministic_output_dir / "final_model_selection_with_deep.csv"
        ),
        random_seed=config.random_seed,
        generate_plots=False,
    )
    rows: List[Dict[str, object]] = []
    origins = pd.DatetimeIndex(point_cache["forecast_origin"].drop_duplicates())
    for origin_index, origin in enumerate(origins):
        for target_index, target in enumerate(targets):
            current_points = point_cache.loc[
                point_cache["forecast_origin"].eq(origin)
                & point_cache["target"].eq(target)
            ].sort_values("horizon_step")
            calibration = _observable_point_calibration(
                target,
                origin,
                development_points,
                point_cache,
                target_df,
            )
            for level_index, level in enumerate(levels):
                selected = uncertainty_selection.loc[
                    uncertainty_selection["target"].eq(target)
                    & np.isclose(uncertainty_selection["nominal_level"], level)
                ]
                if len(selected) != 1:
                    raise AssertionError(
                        f"Expected one audited uncertainty selection for {target} at {level}."
                    )
                selected_row = selected.iloc[0]
                method = str(selected_row["selected_uncertainty_method"])
                raw = rolling_cqr.loc[
                    rolling_cqr["target"].eq(target)
                    & rolling_cqr["forecast_origin"].eq(origin)
                    & np.isclose(rolling_cqr["level"], level)
                ].sort_values("horizon_step")
                cqr_scores = None
                raw_lower = None
                raw_upper = None
                if method == METHOD_CQR:
                    if len(raw) != len(current_points):
                        raise AssertionError("Current CQR path does not align with point forecasts.")
                    raw_lower = raw["raw_lower"].to_numpy(dtype=float)
                    raw_upper = raw["raw_upper"].to_numpy(dtype=float)
                    cqr_scores = _observable_cqr_scores(
                        target,
                        level,
                        origin,
                        development_cqr,
                        rolling_cqr,
                        target_df,
                    )
                rng = np.random.default_rng(
                    config.random_seed
                    + origin_index * 1000
                    + target_index * 100
                    + level_index * 10
                )
                lower, upper, details = _interval_from_method(
                    method=method,
                    target=target,
                    level=level,
                    points=current_points["prediction"].to_numpy(dtype=float),
                    calibration=calibration,
                    rng=rng,
                    config=uncertainty_config,
                    cqr_raw_lower=raw_lower,
                    cqr_raw_upper=raw_upper,
                    cqr_scores=cqr_scores,
                )
                for index, point_row in enumerate(current_points.itertuples()):
                    rows.append(
                        {
                            "forecast_origin": origin,
                            "forecast_date": point_row.forecast_date,
                            "horizon_step": point_row.horizon_step,
                            "target": target,
                            "nominal_level": float(level),
                            "point_forecast": float(point_row.prediction),
                            "lower": float(lower[index]),
                            "upper": float(upper[index]),
                            "selected_uncertainty_method": method,
                            "selection_status": selected_row["selection_status"],
                            "nominal_target_achieved": bool(
                                selected_row["nominal_target_achieved"]
                            ),
                            "calibration_count": int(details["calibration_count"]),
                            "calibration_cutoff": origin,
                            "maximum_calibration_outcome_date": pd.to_datetime(
                                calibration["forecast_date"]
                            ).max(),
                            "final_test_used_for_method_selection": False,
                            "future_actual_used": False,
                        }
                    )
    result = pd.DataFrame(rows)
    if not (
        pd.to_datetime(result["maximum_calibration_outcome_date"])
        <= pd.to_datetime(result["forecast_origin"])
    ).all():
        raise AssertionError("An interval used a calibration outcome after its origin.")
    return result.sort_values(
        ["forecast_origin", "forecast_date", "target", "nominal_level"]
    ).reset_index(drop=True)


def build_paired_development_residual_paths(
    selected_validation_points: pd.DataFrame,
) -> pd.DataFrame:
    """Build complete nine-month available/occupied residual paths."""

    subset = selected_validation_points.loc[
        selected_validation_points["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
    ].copy()
    pivot = subset.pivot_table(
        index=["fold_id", "horizon_step"],
        columns="target",
        values="error",
        aggfunc="first",
    ).reset_index()
    complete_folds = (
        pivot.groupby("fold_id")["horizon_step"].nunique().loc[lambda s: s.eq(9)].index
    )
    pivot = pivot.loc[pivot["fold_id"].isin(complete_folds)].copy()
    if len(complete_folds) != 7 or len(pivot) != 63:
        raise AssertionError("Expected seven complete paired development residual paths.")
    result = pivot.rename(
        columns={
            AVAILABLE_TARGET: "available_error",
            OCCUPIED_TARGET: "occupied_error",
        }
    )
    result["headroom_error"] = (
        result["available_error"] - result["occupied_error"]
    )
    result["source_period"] = "development_only"
    result["final_test_used"] = False
    return result.sort_values(["fold_id", "horizon_step"]).reset_index(drop=True)


def generate_paired_residual_scenarios(
    point_path: pd.DataFrame,
    residual_paths: pd.DataFrame,
    scenario_count: int,
    seed: int,
) -> pd.DataFrame:
    """Resample complete paired paths to preserve temporal and cross-target dependence."""

    required_targets = {AVAILABLE_TARGET, OCCUPIED_TARGET}
    if set(point_path["target"]) != required_targets:
        raise AssertionError("Point path must contain available and occupied targets only.")
    wide = point_path.pivot(
        index="forecast_date", columns="target", values="prediction"
    ).sort_index()
    horizon = len(wide)
    if horizon < 1 or horizon > 9:
        raise ValueError("Scenario horizon must lie between one and nine months.")
    folds = list(residual_paths["fold_id"].drop_duplicates())
    rng = np.random.default_rng(seed)
    sampled_folds = rng.choice(folds, size=scenario_count, replace=True)
    rows: List[Dict[str, object]] = []
    probability = 1.0 / scenario_count
    for scenario_index, fold_id in enumerate(sampled_folds):
        residual = residual_paths.loc[
            residual_paths["fold_id"].eq(fold_id)
        ].sort_values("horizon_step").head(horizon)
        for horizon_step, (forecast_date, point_row) in enumerate(
            wide.iterrows(), start=1
        ):
            residual_row = residual.iloc[horizon_step - 1]
            rows.append(
                {
                    "scenario_id": f"S{scenario_index + 1:03d}",
                    "forecast_month": forecast_date,
                    "horizon_step": horizon_step,
                    "available_capacity": max(
                        float(point_row[AVAILABLE_TARGET])
                        + float(residual_row["available_error"]),
                        0.0,
                    ),
                    "occupied_demand": max(
                        float(point_row[OCCUPIED_TARGET])
                        + float(residual_row["occupied_error"]),
                        0.0,
                    ),
                    "probability": probability,
                    "sampled_development_fold": fold_id,
                    "scenario_source": "complete_paired_development_residual_path",
                    "preserves_cross_target_dependence": True,
                    "preserves_within_path_temporal_dependence": True,
                    "final_test_used_for_scenario_generation": False,
                }
            )
    return pd.DataFrame(rows)


def _cache_path(
    cache: pd.DataFrame,
    origin: pd.Timestamp,
    targets: Sequence[str] = (AVAILABLE_TARGET, OCCUPIED_TARGET),
) -> pd.DataFrame:
    path = cache.loc[
        cache["forecast_origin"].eq(pd.Timestamp(origin))
        & cache["target"].isin(targets)
    ].copy()
    if path.empty:
        raise KeyError(f"No forecast path exists for origin {origin}.")
    expected = len(path["forecast_date"].drop_duplicates()) * len(targets)
    if len(path) != expected:
        raise AssertionError("Forecast cache path is incomplete.")
    return path.sort_values(["forecast_date", "target"]).reset_index(drop=True)


def _interval_policy_inputs(
    interval_cache: pd.DataFrame,
    origin: pd.Timestamp,
    level: float,
) -> pd.DataFrame:
    subset = interval_cache.loc[
        interval_cache["forecast_origin"].eq(pd.Timestamp(origin))
        & np.isclose(interval_cache["nominal_level"], level)
        & interval_cache["target"].isin([AVAILABLE_TARGET, OCCUPIED_TARGET])
    ].copy()
    if subset.empty:
        raise KeyError(f"No {level:.0%} interval path exists for origin {origin}.")
    available = subset.loc[subset["target"].eq(AVAILABLE_TARGET)].set_index(
        "forecast_date"
    )
    occupied = subset.loc[subset["target"].eq(OCCUPIED_TARGET)].set_index(
        "forecast_date"
    )
    if not available.index.equals(occupied.index):
        raise AssertionError("Available and occupied interval dates do not align.")
    status_values = sorted(set(subset["selection_status"].astype(str)))
    methods = sorted(set(subset["selected_uncertainty_method"].astype(str)))
    return pd.DataFrame(
        {
            "forecast_month": available.index,
            "available_capacity_input": available["lower"].to_numpy(dtype=float),
            "occupied_demand_input": occupied["upper"].to_numpy(dtype=float),
            "point_available_forecast": available["point_forecast"].to_numpy(dtype=float),
            "point_occupied_forecast": occupied["point_forecast"].to_numpy(dtype=float),
            "input_source": f"audited_{int(level * 100)}_percent_marginal_bounds",
            "coverage_status": " | ".join(status_values),
            "uncertainty_method": " | ".join(methods),
            "uses_future_actuals": False,
        }
    )


def build_policy_inputs(
    policy: str,
    origin: pd.Timestamp,
    point_cache: pd.DataFrame,
    reactive_cache: pd.DataFrame,
    interval_cache: pd.DataFrame,
    target_df: pd.DataFrame,
    final_test_end: pd.Timestamp,
    horizon: int,
) -> pd.DataFrame:
    """Return the exact available/demand path used by one policy."""

    origin = pd.Timestamp(origin)
    if policy in {"point_forecast", "stochastic_scenarios"}:
        path = _cache_path(point_cache, origin)
        wide = path.pivot(
            index="forecast_date", columns="target", values="prediction"
        ).sort_index()
        return pd.DataFrame(
            {
                "forecast_month": wide.index,
                "available_capacity_input": wide[AVAILABLE_TARGET].to_numpy(dtype=float),
                "occupied_demand_input": wide[OCCUPIED_TARGET].to_numpy(dtype=float),
                "point_available_forecast": wide[AVAILABLE_TARGET].to_numpy(dtype=float),
                "point_occupied_forecast": wide[OCCUPIED_TARGET].to_numpy(dtype=float),
                "input_source": (
                    "locked_validation_selected_point_forecasts"
                    if policy == "point_forecast"
                    else "paired_development_residual_scenarios_around_locked_points"
                ),
                "coverage_status": (
                    "not_applicable_point_scenario"
                    if policy == "point_forecast"
                    else "scenario_distribution_no_nominal_coverage_claim"
                ),
                "uncertainty_method": (
                    "point_forecast"
                    if policy == "point_forecast"
                    else "complete_paired_development_residual_paths"
                ),
                "uses_future_actuals": False,
            }
        )
    if policy == "reactive_baseline":
        path = _cache_path(reactive_cache, origin)
        wide = path.pivot(
            index="forecast_date", columns="target", values="prediction"
        ).sort_index()
        return pd.DataFrame(
            {
                "forecast_month": wide.index,
                "available_capacity_input": wide[AVAILABLE_TARGET].to_numpy(dtype=float),
                "occupied_demand_input": wide[OCCUPIED_TARGET].to_numpy(dtype=float),
                "point_available_forecast": wide[AVAILABLE_TARGET].to_numpy(dtype=float),
                "point_occupied_forecast": wide[OCCUPIED_TARGET].to_numpy(dtype=float),
                "input_source": "seasonal_naive_reactive_baseline",
                "coverage_status": "not_applicable_baseline",
                "uncertainty_method": "none",
                "uses_future_actuals": False,
            }
        )
    if policy == "moderate_90":
        return _interval_policy_inputs(interval_cache, origin, 0.90)
    if policy == "conservative_95":
        return _interval_policy_inputs(interval_cache, origin, 0.95)
    if policy == "perfect_information_oracle":
        dates = forecast_dates_from_origin(origin, final_test_end, horizon)
        return pd.DataFrame(
            {
                "forecast_month": dates,
                "available_capacity_input": target_df.loc[
                    dates, AVAILABLE_TARGET
                ].to_numpy(dtype=float),
                "occupied_demand_input": target_df.loc[
                    dates, OCCUPIED_TARGET
                ].to_numpy(dtype=float),
                "point_available_forecast": target_df.loc[
                    dates, AVAILABLE_TARGET
                ].to_numpy(dtype=float),
                "point_occupied_forecast": target_df.loc[
                    dates, OCCUPIED_TARGET
                ].to_numpy(dtype=float),
                "input_source": "actual_future_values_oracle_only",
                "coverage_status": "perfect_information_benchmark",
                "uncertainty_method": "not_applicable",
                "uses_future_actuals": True,
            }
        )
    raise KeyError(f"Unknown policy: {policy}")


def los_penalty_multipliers(
    origin: pd.Timestamp,
    forecast_dates: Sequence[pd.Timestamp],
    target_df: pd.DataFrame,
    point_cache: pd.DataFrame,
    config: OptimizationEvaluationConfig,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Return optional LOS trigger multipliers without changing occupied census."""

    dates = pd.DatetimeIndex(forecast_dates)
    threshold = float(
        target_df.loc[: pd.Timestamp(origin), config.primary_los_signal].quantile(
            config.los_trigger_quantile
        )
    )
    path = point_cache.loc[
        point_cache["forecast_origin"].eq(pd.Timestamp(origin))
        & point_cache["target"].eq(config.primary_los_signal)
    ].set_index("forecast_date")
    values = path.loc[dates, "prediction"].to_numpy(dtype=float)
    triggers = values > threshold
    if not config.los_pressure_mode:
        return np.ones(len(dates), dtype=float), triggers, threshold
    multipliers = np.where(
        triggers,
        config.los_shortage_penalty_multiplier,
        1.0,
    )
    return multipliers.astype(float), triggers, threshold


def evaluate_implemented_decisions(
    implemented_decisions: pd.DataFrame,
    target_df: pd.DataFrame,
    costs: CostParameters,
) -> pd.DataFrame:
    """Reveal actual outcomes and calculate realized costs after decisions."""

    rows: List[Dict[str, object]] = []
    for decision in implemented_decisions.itertuples():
        month = pd.Timestamp(decision.forecast_month)
        actual_available = float(target_df.loc[month, AVAILABLE_TARGET])
        actual_occupied = float(target_df.loc[month, OCCUPIED_TARGET])
        actual_required = actual_occupied / float(decision.rho_max)
        active = float(decision.additional_capacity_active)
        realized_shortage = max(actual_required - actual_available - active, 0.0)
        realized_unused = max(actual_available + active - actual_required, 0.0)
        if realized_shortage < 1e-3:
            realized_shortage = 0.0
        if realized_unused < 1e-3:
            realized_unused = 0.0
        active_cost = costs.active_staffed_bed_cost * active
        addition_cost = costs.capacity_addition_cost * float(decision.capacity_added)
        removal_cost = costs.capacity_removal_cost * float(decision.capacity_removed)
        change_cost = costs.capacity_change_penalty * (
            float(decision.capacity_added) + float(decision.capacity_removed)
        )
        shortage_cost = (
            costs.shortage_penalty
            * float(decision.shortage_penalty_multiplier)
            * realized_shortage
        )
        row = decision._asdict()
        row.update(
            {
                "actual_available_capacity": actual_available,
                "actual_occupied_demand": actual_occupied,
                "actual_required_capacity": actual_required,
                "realized_shortage_bed_months": realized_shortage,
                "realized_unused_bed_months": realized_unused,
                "active_extra_capacity_cost": active_cost,
                "addition_cost": addition_cost,
                "removal_cost": removal_cost,
                "capacity_change_cost": change_cost,
                "shortage_cost": shortage_cost,
                "total_realized_cost": active_cost
                + addition_cost
                + removal_cost
                + change_cost
                + shortage_cost,
                "available_input_error": float(decision.available_capacity_input)
                - actual_available,
                "occupied_input_error": float(decision.occupied_demand_input)
                - actual_occupied,
                "shortage_month": realized_shortage > 1e-7,
                "actual_outcome_revealed_after_decision": (
                    bool(decision.uses_future_actuals)
                    or pd.Timestamp(decision.forecast_origin) < month
                ),
                "realized_shortage_uses_actual_outcomes": True,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def run_policy_backtest(
    policy: str,
    reoptimization_frequency_months: int,
    rho_max: float,
    target_df: pd.DataFrame,
    point_cache: pd.DataFrame,
    reactive_cache: pd.DataFrame,
    interval_cache: pd.DataFrame,
    scenario_cache: Mapping[pd.Timestamp, pd.DataFrame],
    config: OptimizationEvaluationConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Run one chronological receding-horizon policy over the sealed test."""

    if policy not in ALL_POLICIES:
        raise KeyError(policy)
    if reoptimization_frequency_months not in UPDATE_FREQUENCIES:
        raise ValueError("reoptimization_frequency_months must be 1, 3, or 6.")
    if rho_max not in config.occupancy_thresholds:
        raise ValueError("rho_max is not in the configured sensitivity set.")

    all_origins = monthly_backtest_origins(
        config.final_test_start, config.final_test_end
    )
    origins = all_origins[::reoptimization_frequency_months]
    initial_capacity = 0.0
    decision_frames: List[pd.DataFrame] = []
    realized_frames: List[pd.DataFrame] = []

    for update_index, origin in enumerate(origins, start=1):
        inputs = build_policy_inputs(
            policy,
            origin,
            point_cache,
            reactive_cache,
            interval_cache,
            target_df,
            config.final_test_end,
            config.planning_horizon,
        ).sort_values("forecast_month")
        dates = pd.DatetimeIndex(inputs["forecast_month"])
        multipliers, los_triggers, los_threshold = los_penalty_multipliers(
            origin,
            dates,
            target_df,
            point_cache,
            config,
        )
        if policy == "stochastic_scenarios":
            generated_scenarios = scenario_cache[pd.Timestamp(origin)]
            scenarios = compress_duplicate_scenarios(generated_scenarios)
            decisions, _, metadata = solve_stochastic_capacity_plan(
                dates,
                scenarios,
                rho_max,
                config.costs,
                initial_capacity=initial_capacity,
                cvar_weight=config.cvar_weight,
                cvar_alpha=config.cvar_alpha,
                policy=policy,
            )
            decisions["generated_scenario_count"] = int(
                generated_scenarios["scenario_id"].nunique()
            )
            decisions["unique_scenario_paths"] = int(
                scenarios["scenario_id"].nunique()
            )
        else:
            decisions, metadata = solve_deterministic_capacity_plan(
                dates,
                inputs["available_capacity_input"],
                inputs["occupied_demand_input"],
                rho_max,
                config.costs,
                initial_capacity=initial_capacity,
                shortage_penalty_multipliers=multipliers,
                policy=policy,
            )
            decisions["generated_scenario_count"] = 1
            decisions["unique_scenario_paths"] = 1
        decisions = decisions.merge(
            inputs[
                [
                    "forecast_month",
                    "point_available_forecast",
                    "point_occupied_forecast",
                    "input_source",
                    "coverage_status",
                    "uncertainty_method",
                    "uses_future_actuals",
                ]
            ],
            on="forecast_month",
            how="left",
            validate="one_to_one",
        )
        segment_length = min(reoptimization_frequency_months, len(decisions))
        decisions["forecast_origin"] = origin
        decisions["decision_information_cutoff"] = origin
        decisions["update_index"] = update_index
        decisions["reoptimization_frequency_months"] = (
            reoptimization_frequency_months
        )
        decisions["planned_horizon_months"] = len(decisions)
        decisions["implemented_segment_months"] = segment_length
        decisions["implemented"] = decisions["horizon_step"] <= segment_length
        decisions["run_id"] = (
            f"{policy}_f{reoptimization_frequency_months}_"
            f"rho{rho_max:.2f}_{origin:%Y%m}"
        )
        decisions["final_test_used_for_policy_tuning"] = False
        decisions["cost_parameters_validated"] = config.costs.validated
        decisions["los_pressure_mode_enabled"] = config.los_pressure_mode
        decisions["primary_los_signal"] = config.primary_los_signal
        decisions["los_trigger_threshold"] = los_threshold
        decisions["los_stress_trigger"] = los_triggers
        decisions["los_added_to_occupied_census"] = False
        decisions["cvar_enabled"] = bool(metadata["cvar_enabled"])
        decisions["oracle_benchmark_only"] = policy == "perfect_information_oracle"

        implemented = decisions.loc[decisions["implemented"]].copy()
        if len(implemented) != segment_length:
            raise AssertionError("Implemented segment length is incorrect.")
        realized = evaluate_implemented_decisions(
            implemented,
            target_df,
            config.costs,
        )
        decision_frames.append(decisions)
        realized_frames.append(realized)
        initial_capacity = float(
            implemented.sort_values("forecast_month")[
                "additional_capacity_active"
            ].iloc[-1]
        )

    all_decisions = pd.concat(decision_frames, ignore_index=True)
    all_realized = pd.concat(realized_frames, ignore_index=True)
    if len(all_realized) != 9:
        raise AssertionError(
            f"Each policy/frequency/rho run must implement nine months, found {len(all_realized)}."
        )
    if all_realized["forecast_month"].duplicated().any():
        raise AssertionError("A policy implemented more than one decision for a month.")
    implementable = all_decisions["policy"].isin(IMPLEMENTABLE_POLICIES)
    if all_decisions.loc[implementable, "uses_future_actuals"].astype(bool).any():
        raise AssertionError("An implementable policy used future actual outcomes.")
    return all_decisions, all_realized


def run_all_policy_backtests(
    target_df: pd.DataFrame,
    point_cache: pd.DataFrame,
    reactive_cache: pd.DataFrame,
    interval_cache: pd.DataFrame,
    residual_paths: pd.DataFrame,
    config: OptimizationEvaluationConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[pd.Timestamp, pd.DataFrame]]:
    """Evaluate all policies, frequencies and occupancy sensitivities."""

    scenario_cache: Dict[pd.Timestamp, pd.DataFrame] = {}
    for origin_index, origin in enumerate(
        monthly_backtest_origins(config.final_test_start, config.final_test_end)
    ):
        point_path = _cache_path(point_cache, origin)
        scenario_cache[pd.Timestamp(origin)] = generate_paired_residual_scenarios(
            point_path,
            residual_paths,
            config.scenario_count,
            config.random_seed + origin_index * 1000,
        )

    decision_frames: List[pd.DataFrame] = []
    realized_frames: List[pd.DataFrame] = []
    for rho_max in config.occupancy_thresholds:
        for frequency in config.update_frequencies:
            for policy in ALL_POLICIES:
                decisions, realized = run_policy_backtest(
                    policy,
                    frequency,
                    rho_max,
                    target_df,
                    point_cache,
                    reactive_cache,
                    interval_cache,
                    scenario_cache,
                    config,
                )
                decision_frames.append(decisions)
                realized_frames.append(realized)
    return (
        pd.concat(decision_frames, ignore_index=True),
        pd.concat(realized_frames, ignore_index=True),
        scenario_cache,
    )


def summarize_optimization_policies(realized: pd.DataFrame) -> pd.DataFrame:
    """Aggregate realized operational and cost outcomes for every policy run."""

    group_columns = [
        "policy",
        "reoptimization_frequency_months",
        "rho_max",
        "parameter_set_id",
    ]
    rows: List[Dict[str, object]] = []
    for keys, group in realized.groupby(group_columns, sort=True):
        policy, frequency, rho_max, parameter_set_id = keys
        total_required = float(group["actual_required_capacity"].sum())
        shortage = float(group["realized_shortage_bed_months"].sum())
        changes = (
            group["capacity_added"].to_numpy(dtype=float)
            + group["capacity_removed"].to_numpy(dtype=float)
        )
        rows.append(
            {
                "policy": policy,
                "reoptimization_frequency_months": int(frequency),
                "rho_max": float(rho_max),
                "parameter_set_id": parameter_set_id,
                "total_realized_cost": float(group["total_realized_cost"].sum()),
                "active_extra_capacity_cost": float(
                    group["active_extra_capacity_cost"].sum()
                ),
                "addition_cost": float(group["addition_cost"].sum()),
                "removal_cost": float(group["removal_cost"].sum()),
                "capacity_change_cost": float(group["capacity_change_cost"].sum()),
                "shortage_cost": float(group["shortage_cost"].sum()),
                "shortage_bed_months": shortage,
                "unused_bed_months": float(
                    group["realized_unused_bed_months"].sum()
                ),
                "total_extra_staffed_bed_months": float(
                    group["additional_capacity_active"].sum()
                ),
                "peak_extra_capacity": float(
                    group["additional_capacity_active"].max()
                ),
                "number_of_capacity_changes": int((changes > 1e-7).sum()),
                "maximum_monthly_change": float(changes.max()),
                "service_level": (
                    1.0 - shortage / total_required if total_required > 0 else np.nan
                ),
                "shortage_months": int(group["shortage_month"].astype(bool).sum()),
                "cost_parameters_validated": bool(
                    group["cost_parameters_validated"].iloc[0]
                ),
                "oracle_benchmark_only": policy == "perfect_information_oracle",
            }
        )
    summary = pd.DataFrame(rows)
    oracle = summary.loc[
        summary["policy"].eq("perfect_information_oracle"),
        [
            "reoptimization_frequency_months",
            "rho_max",
            "parameter_set_id",
            "total_realized_cost",
        ],
    ].rename(columns={"total_realized_cost": "perfect_information_oracle_cost"})
    summary = summary.merge(
        oracle,
        on=["reoptimization_frequency_months", "rho_max", "parameter_set_id"],
        how="left",
        validate="many_to_one",
    )
    summary["regret_vs_perfect_information"] = (
        summary["total_realized_cost"] - summary["perfect_information_oracle_cost"]
    )
    summary.loc[
        summary["regret_vs_perfect_information"].abs() < 1e-5,
        "regret_vs_perfect_information",
    ] = 0.0
    return summary.sort_values(
        ["rho_max", "reoptimization_frequency_months", "policy"]
    ).reset_index(drop=True)


def build_update_frequency_summary(policy_summary: pd.DataFrame) -> pd.DataFrame:
    """Compare each update policy with monthly reoptimization."""

    monthly = policy_summary.loc[
        policy_summary["reoptimization_frequency_months"].eq(1),
        [
            "policy",
            "rho_max",
            "parameter_set_id",
            "total_realized_cost",
            "shortage_bed_months",
            "total_extra_staffed_bed_months",
        ],
    ].rename(
        columns={
            "total_realized_cost": "monthly_update_cost",
            "shortage_bed_months": "monthly_update_shortage_bed_months",
            "total_extra_staffed_bed_months": "monthly_update_extra_capacity_bed_months",
        }
    )
    result = policy_summary.merge(
        monthly,
        on=["policy", "rho_max", "parameter_set_id"],
        how="left",
        validate="many_to_one",
    )
    result["freshness_value"] = (
        result["total_realized_cost"] - result["monthly_update_cost"]
    )
    result["freshness_value_percent_of_monthly"] = np.where(
        result["monthly_update_cost"].abs() > 1e-9,
        100.0 * result["freshness_value"] / result["monthly_update_cost"],
        np.nan,
    )
    result["shortage_change_vs_monthly"] = (
        result["shortage_bed_months"]
        - result["monthly_update_shortage_bed_months"]
    )
    result["extra_capacity_change_vs_monthly"] = (
        result["total_extra_staffed_bed_months"]
        - result["monthly_update_extra_capacity_bed_months"]
    )
    return result.sort_values(
        ["rho_max", "policy", "reoptimization_frequency_months"]
    ).reset_index(drop=True)


def build_forecast_value_summary(policy_summary: pd.DataFrame) -> pd.DataFrame:
    """Calculate cost improvement relative to the reactive seasonal baseline."""

    reactive = policy_summary.loc[
        policy_summary["policy"].eq("reactive_baseline"),
        [
            "reoptimization_frequency_months",
            "rho_max",
            "parameter_set_id",
            "total_realized_cost",
            "shortage_bed_months",
            "service_level",
        ],
    ].rename(
        columns={
            "total_realized_cost": "reactive_cost",
            "shortage_bed_months": "reactive_shortage_bed_months",
            "service_level": "reactive_service_level",
        }
    )
    candidates = policy_summary.loc[
        policy_summary["policy"].isin(
            [
                "point_forecast",
                "moderate_90",
                "conservative_95",
                "stochastic_scenarios",
            ]
        )
    ].copy()
    result = candidates.merge(
        reactive,
        on=["reoptimization_frequency_months", "rho_max", "parameter_set_id"],
        how="left",
        validate="many_to_one",
    )
    result["forecast_value"] = (
        result["reactive_cost"] - result["total_realized_cost"]
    )
    result["forecast_value_percent"] = np.where(
        result["reactive_cost"].abs() > 1e-9,
        100.0 * result["forecast_value"] / result["reactive_cost"],
        np.nan,
    )
    result["shortage_reduction_vs_reactive"] = (
        result["reactive_shortage_bed_months"] - result["shortage_bed_months"]
    )
    result["service_level_improvement_vs_reactive"] = (
        result["service_level"] - result["reactive_service_level"]
    )
    result["positive_value_means_forecasting_helped"] = True
    return result.sort_values(
        ["rho_max", "reoptimization_frequency_months", "policy"]
    ).reset_index(drop=True)


def build_uncertainty_value_summary(policy_summary: pd.DataFrame) -> pd.DataFrame:
    """Compare uncertainty-aware policies with point-forecast optimization."""

    point = policy_summary.loc[
        policy_summary["policy"].eq("point_forecast"),
        [
            "reoptimization_frequency_months",
            "rho_max",
            "parameter_set_id",
            "total_realized_cost",
            "shortage_bed_months",
            "total_extra_staffed_bed_months",
            "service_level",
        ],
    ].rename(
        columns={
            "total_realized_cost": "point_cost",
            "shortage_bed_months": "point_shortage_bed_months",
            "total_extra_staffed_bed_months": "point_extra_capacity_bed_months",
            "service_level": "point_service_level",
        }
    )
    candidates = policy_summary.loc[
        policy_summary["policy"].isin(
            ["moderate_90", "conservative_95", "stochastic_scenarios"]
        )
    ].copy()
    result = candidates.merge(
        point,
        on=["reoptimization_frequency_months", "rho_max", "parameter_set_id"],
        how="left",
        validate="many_to_one",
    )
    result["uncertainty_value"] = (
        result["point_cost"] - result["total_realized_cost"]
    )
    result["uncertainty_value_percent"] = np.where(
        result["point_cost"].abs() > 1e-9,
        100.0 * result["uncertainty_value"] / result["point_cost"],
        np.nan,
    )
    result["shortage_reduction_vs_point"] = (
        result["point_shortage_bed_months"] - result["shortage_bed_months"]
    )
    result["added_capacity_cost_of_conservatism"] = (
        result["total_extra_staffed_bed_months"]
        - result["point_extra_capacity_bed_months"]
    )
    result["service_level_improvement_vs_point"] = (
        result["service_level"] - result["point_service_level"]
    )
    result["positive_value_means_uncertainty_helped"] = True
    return result.sort_values(
        ["rho_max", "reoptimization_frequency_months", "policy"]
    ).reset_index(drop=True)


def build_optimization_horizon_summary(
    decisions: pd.DataFrame,
    realized: pd.DataFrame,
) -> pd.DataFrame:
    """Summarize planned and, where implemented, realized outcomes by horizon."""

    group_columns = [
        "policy",
        "reoptimization_frequency_months",
        "rho_max",
        "horizon_step",
    ]
    planned = (
        decisions.groupby(group_columns, as_index=False)
        .agg(
            planned_rows=("forecast_month", "size"),
            mean_required_extra_capacity=("additional_capacity_active", "mean"),
            median_required_extra_capacity=("additional_capacity_active", "median"),
            mean_planned_shortage=("planned_shortage", "mean"),
            mean_planned_unused_capacity=("planned_unused_capacity", "mean"),
        )
        .sort_values(group_columns)
    )
    realized_summary = (
        realized.groupby(group_columns, as_index=False)
        .agg(
            implemented_months=("forecast_month", "size"),
            mean_realized_shortage=("realized_shortage_bed_months", "mean"),
            total_realized_shortage=("realized_shortage_bed_months", "sum"),
            mean_unused_capacity=("realized_unused_bed_months", "mean"),
            mean_realized_cost=("total_realized_cost", "mean"),
            shortage_month_probability=("shortage_month", "mean"),
        )
    )
    result = planned.merge(
        realized_summary,
        on=group_columns,
        how="left",
        validate="one_to_one",
    ).sort_values(group_columns).reset_index(drop=True)
    result["implemented_months"] = result["implemented_months"].fillna(0).astype(int)
    result["shortage_risk_interpretation"] = (
        "Empirical fraction of implemented sealed-test months with positive "
        "occupancy-adjusted shortage at this step from an update origin"
    )
    return result


def _csv_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().eq("true")


def build_uncertainty_horizon_analysis(
    uncertainty_validation: pd.DataFrame,
    uncertainty_selection: pd.DataFrame,
    selected_validation_points: pd.DataFrame,
) -> pd.DataFrame:
    """Measure selected interval behavior by horizon using development paths."""

    validation = uncertainty_validation.copy()
    validation["eligible_for_selection_bool"] = _csv_bool(
        validation["eligible_for_selection"]
    )
    rows: List[Dict[str, object]] = []
    for selected in uncertainty_selection.itertuples():
        subset = validation.loc[
            validation["target"].eq(selected.target)
            & validation["method"].eq(selected.selected_uncertainty_method)
            & np.isclose(validation["level"], selected.nominal_level)
            & validation["status"].eq("ok")
            & validation["eligible_for_selection_bool"]
        ].copy()
        if len(subset) != 27:
            raise AssertionError(
                f"Expected 27 selected interval predictions for {selected.target}, "
                f"{selected.nominal_level}, found {len(subset)}."
            )
        point_subset = selected_validation_points.loc[
            selected_validation_points["target"].eq(selected.target)
        ].copy()
        horizon_widths: Dict[int, float] = {}
        for horizon_step in range(1, 10):
            interval_h = subset.loc[subset["horizon_step"].eq(horizon_step)]
            point_h = point_subset.loc[
                point_subset["horizon_step"].eq(horizon_step)
            ]
            width = (
                interval_h["upper"].to_numpy(dtype=float)
                - interval_h["lower"].to_numpy(dtype=float)
            )
            actual = interval_h["actual"].to_numpy(dtype=float)
            lower = interval_h["lower"].to_numpy(dtype=float)
            upper = interval_h["upper"].to_numpy(dtype=float)
            horizon_widths[horizon_step] = float(np.mean(width))
            rows.append(
                {
                    "target": selected.target,
                    "nominal_level": float(selected.nominal_level),
                    "selected_uncertainty_method": selected.selected_uncertainty_method,
                    "selection_status": selected.selection_status,
                    "horizon_step": horizon_step,
                    "interval_evaluation_predictions": len(interval_h),
                    "point_evaluation_predictions": len(point_h),
                    "mean_interval_width": float(np.mean(width)),
                    "median_interval_width": float(np.median(width)),
                    "empirical_coverage": float(
                        np.mean((actual >= lower) & (actual <= upper))
                    ),
                    "lower_miss_count": int(np.sum(actual < lower)),
                    "upper_miss_count": int(np.sum(actual > upper)),
                    "point_forecast_MAE": float(
                        point_h["absolute_error"].mean()
                    ),
                    "scenario_standard_deviation": float(
                        point_h["error"].std(ddof=1)
                    ),
                    "analysis_period": "development_fixed_origin_paths",
                    "final_test_used_for_method_selection": False,
                }
            )
        width_series = pd.Series(horizon_widths).sort_index()
        if np.allclose(
            width_series.to_numpy(dtype=float),
            float(width_series.iloc[0]),
            rtol=1e-10,
            atol=1e-12,
        ):
            slope = 0.0
            spearman = np.nan
        else:
            slope = float(
                np.polyfit(
                    width_series.index.to_numpy(dtype=float),
                    width_series.to_numpy(dtype=float),
                    1,
                )[0]
            )
            spearman = float(
                width_series.corr(
                    pd.Series(width_series.index, index=width_series.index),
                    method="spearman",
                )
            )
        change = float(width_series.iloc[-1] - width_series.iloc[0])
        if abs(change) < 1e-9:
            change = 0.0
        for row in rows[-9:]:
            row["interval_width_slope_per_horizon"] = slope
            row["interval_width_spearman_with_horizon"] = spearman
            row["interval_width_h9_minus_h1"] = change
            row["uncertainty_widened_from_h1_to_h9"] = change > 1e-9
    return pd.DataFrame(rows)


def evaluate_fixed_plan_on_scenarios(
    decisions: pd.DataFrame,
    scenarios: pd.DataFrame,
    rho_max: float,
    costs: CostParameters,
) -> float:
    """Evaluate one fixed capacity path across a common scenario distribution."""

    dates = pd.DatetimeIndex(decisions["forecast_month"])
    scenario_frame = _validate_scenario_frame(scenarios, dates)
    deterministic_cost = float(
        (
            costs.active_staffed_bed_cost * decisions["additional_capacity_active"]
            + costs.capacity_addition_cost * decisions["capacity_added"]
            + costs.capacity_removal_cost * decisions["capacity_removed"]
            + costs.capacity_change_penalty
            * (decisions["capacity_added"] + decisions["capacity_removed"])
        ).sum()
    )
    x_lookup = decisions.set_index("forecast_month")[
        "additional_capacity_active"
    ].to_dict()
    expected_shortage_cost = 0.0
    for scenario_id, scenario in scenario_frame.groupby("scenario_id"):
        probability = float(scenario["probability"].iloc[0])
        shortage = 0.0
        for row in scenario.itertuples():
            shortage += max(
                float(row.occupied_demand) / rho_max
                - float(row.available_capacity)
                - float(x_lookup[pd.Timestamp(row.forecast_month)]),
                0.0,
            )
        expected_shortage_cost += probability * costs.shortage_penalty * shortage
    return deterministic_cost + expected_shortage_cost


def compress_duplicate_scenarios(scenarios: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate sampled development paths while retaining probabilities."""

    if "sampled_development_fold" not in scenarios:
        return scenarios.copy()
    scenario_fold = scenarios.groupby("scenario_id")[
        "sampled_development_fold"
    ].first()
    fold_probabilities = (
        scenario_fold.value_counts(normalize=True).sort_index().to_dict()
    )
    rows: List[pd.DataFrame] = []
    for fold_id, probability in fold_probabilities.items():
        scenario_id = scenario_fold.loc[scenario_fold.eq(fold_id)].index[0]
        path = scenarios.loc[scenarios["scenario_id"].eq(scenario_id)].copy()
        path["scenario_id"] = f"PATH_{fold_id}"
        path["probability"] = float(probability)
        rows.append(path)
    return pd.concat(rows, ignore_index=True)


def build_stochastic_value_summary(
    scenarios: pd.DataFrame,
    rho_max: float,
    costs: CostParameters,
) -> pd.DataFrame:
    """Calculate WS, EEV, recourse, EVPI and VSS with common assumptions."""

    compressed = compress_duplicate_scenarios(scenarios)
    dates = pd.DatetimeIndex(
        sorted(pd.to_datetime(compressed["forecast_month"]).unique())
    )
    expected = (
        compressed.assign(
            weighted_available=lambda frame: frame["available_capacity"]
            * frame["probability"],
            weighted_demand=lambda frame: frame["occupied_demand"]
            * frame["probability"],
        )
        .groupby("forecast_month", as_index=False)
        .agg(
            available_capacity=("weighted_available", "sum"),
            occupied_demand=("weighted_demand", "sum"),
        )
        .sort_values("forecast_month")
    )
    ev_plan, _ = solve_deterministic_capacity_plan(
        dates,
        expected["available_capacity"],
        expected["occupied_demand"],
        rho_max,
        costs,
        policy="expected_value_solution",
    )
    expected_result_of_ev = evaluate_fixed_plan_on_scenarios(
        ev_plan, compressed, rho_max, costs
    )
    stochastic_plan, _, stochastic_metadata = solve_stochastic_capacity_plan(
        dates,
        compressed,
        rho_max,
        costs,
        cvar_weight=0.0,
        policy="stochastic_recourse_solution",
    )
    stochastic_recourse_cost = float(stochastic_metadata["objective_value"])

    wait_and_see_cost = 0.0
    for scenario_id, scenario in compressed.groupby("scenario_id"):
        scenario = scenario.sort_values("forecast_month")
        probability = float(scenario["probability"].iloc[0])
        _, metadata = solve_deterministic_capacity_plan(
            dates,
            scenario["available_capacity"],
            scenario["occupied_demand"],
            rho_max,
            costs,
            policy=f"wait_and_see_{scenario_id}",
        )
        wait_and_see_cost += probability * float(metadata["objective_value"])

    evpi = stochastic_recourse_cost - wait_and_see_cost
    vss = expected_result_of_ev - stochastic_recourse_cost
    if abs(evpi) < 1e-5:
        evpi = 0.0
    if abs(vss) < 1e-5:
        vss = 0.0
    return pd.DataFrame(
        [
            {
                "rho_max": rho_max,
                "parameter_set_id": costs.parameter_set_id,
                "scenario_count_original": scenarios["scenario_id"].nunique(),
                "unique_development_paths": compressed["scenario_id"].nunique(),
                "wait_and_see_cost": wait_and_see_cost,
                "expected_value_solution_objective": float(
                    ev_plan["planned_objective_value"].iloc[0]
                ),
                "expected_result_of_expected_value_solution": expected_result_of_ev,
                "stochastic_recourse_solution_cost": stochastic_recourse_cost,
                "expected_value_of_perfect_information": evpi,
                "value_of_stochastic_solution": vss,
                "EVPI_sign_convention": "recourse cost minus wait-and-see cost; nonnegative is expected",
                "VSS_sign_convention": "EEV cost minus recourse cost; nonnegative is expected",
                "identical_costs_and_constraints": True,
                "CVaR_enabled": False,
            }
        ]
    )


def _realize_complete_plan(
    decisions: pd.DataFrame,
    target_df: pd.DataFrame,
    costs: CostParameters,
    origin: pd.Timestamp,
    input_source: str,
    uses_future_actuals: bool = False,
) -> pd.DataFrame:
    plan = decisions.copy()
    plan["implemented"] = True
    plan["forecast_origin"] = pd.Timestamp(origin)
    plan["uses_future_actuals"] = uses_future_actuals
    plan["input_source"] = input_source
    plan["coverage_status"] = "not_applicable"
    plan["uncertainty_method"] = "none"
    plan["reoptimization_frequency_months"] = len(plan)
    plan["cost_parameters_validated"] = costs.validated
    plan["oracle_benchmark_only"] = uses_future_actuals
    return evaluate_implemented_decisions(plan, target_df, costs)


def build_history_window_sensitivity(
    target_df: pd.DataFrame,
    selection: pd.DataFrame,
    specs: Sequence[ModelSpec],
    config: OptimizationEvaluationConfig,
    rho_max: float = 0.90,
) -> pd.DataFrame:
    """Evaluate 12/24/36/all-history estimation windows at the locked origin."""

    origin = pd.Timestamp(config.final_test_start) - pd.offsets.MonthBegin(1)
    forecast_dates = forecast_dates_from_origin(
        origin, config.final_test_end, config.planning_horizon
    )
    rows: List[Dict[str, object]] = []
    for history_window in HISTORY_WINDOWS:
        try:
            training_dates = training_dates_for_origin(
                target_df, origin, history_window
            )
            predictions = forecast_selected_models(
                target_df,
                training_dates,
                forecast_dates,
                selection,
                specs,
                config.random_seed,
            )
            plan, _ = solve_deterministic_capacity_plan(
                forecast_dates,
                predictions[AVAILABLE_TARGET],
                predictions[OCCUPIED_TARGET],
                rho_max,
                config.costs,
                policy=f"history_window_{history_window or 'all'}",
            )
            realized = _realize_complete_plan(
                plan,
                target_df,
                config.costs,
                origin,
                f"locked_models_history_window_{history_window or 'all_available'}",
            )
            rows.append(
                {
                    "history_window_months": (
                        "all_available" if history_window is None else str(history_window)
                    ),
                    "training_observations": len(training_dates),
                    "training_start": training_dates.min(),
                    "training_end": training_dates.max(),
                    "available_final_test_MAE": float(
                        np.mean(
                            np.abs(
                                target_df.loc[forecast_dates, AVAILABLE_TARGET].to_numpy(
                                    dtype=float
                                )
                                - predictions[AVAILABLE_TARGET].to_numpy(dtype=float)
                            )
                        )
                    ),
                    "occupied_final_test_MAE": float(
                        np.mean(
                            np.abs(
                                target_df.loc[forecast_dates, OCCUPIED_TARGET].to_numpy(
                                    dtype=float
                                )
                                - predictions[OCCUPIED_TARGET].to_numpy(dtype=float)
                            )
                        )
                    ),
                    "total_realized_cost": float(
                        realized["total_realized_cost"].sum()
                    ),
                    "shortage_bed_months": float(
                        realized["realized_shortage_bed_months"].sum()
                    ),
                    "extra_capacity_bed_months": float(
                        realized["additional_capacity_active"].sum()
                    ),
                    "status": "ok",
                    "failure_reason": "",
                    "uses_1_3_or_6_month_training_window": False,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "history_window_months": (
                        "all_available" if history_window is None else str(history_window)
                    ),
                    "training_observations": np.nan,
                    "training_start": pd.NaT,
                    "training_end": pd.NaT,
                    "available_final_test_MAE": np.nan,
                    "occupied_final_test_MAE": np.nan,
                    "total_realized_cost": np.nan,
                    "shortage_bed_months": np.nan,
                    "extra_capacity_bed_months": np.nan,
                    "status": "failed",
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                    "uses_1_3_or_6_month_training_window": False,
                }
            )
    return pd.DataFrame(rows)


def _eligible_candidate_models(
    target: str,
    selected_model: str,
    rolling_summary: pd.DataFrame,
    specs: Sequence[ModelSpec],
) -> List[str]:
    available_specs = {spec.model for spec in specs if spec.enabled}
    summary = rolling_summary.loc[
        rolling_summary["target"].eq(target)
        & rolling_summary["protocol"].eq("fixed_origin_9_month")
        & rolling_summary["mean_MAE"].notna()
        & rolling_summary["model"].isin(available_specs)
    ].sort_values(["mean_MAE", "model"])
    candidates = [selected_model]
    if "seasonal_naive" not in candidates:
        candidates.append("seasonal_naive")
    for model in summary["model"]:
        if model not in candidates:
            candidates.append(str(model))
        if len(candidates) == 4:
            break
    if len(candidates) < 4:
        raise AssertionError(f"Fewer than four eligible ranking candidates for {target}.")
    return candidates


def build_forecast_vs_decision_model_ranking(
    target_df: pd.DataFrame,
    selection: pd.DataFrame,
    rolling_summary: pd.DataFrame,
    specs: Sequence[ModelSpec],
    config: OptimizationEvaluationConfig,
    rho_max: float = 0.90,
) -> pd.DataFrame:
    """Compare forecast rank with downstream deterministic decision rank."""

    origin = pd.Timestamp(config.final_test_start) - pd.offsets.MonthBegin(1)
    forecast_dates = forecast_dates_from_origin(
        origin, config.final_test_end, config.planning_horizon
    )
    training_dates = training_dates_for_origin(target_df, origin)
    actual_plan, _ = solve_deterministic_capacity_plan(
        forecast_dates,
        target_df.loc[forecast_dates, AVAILABLE_TARGET],
        target_df.loc[forecast_dates, OCCUPIED_TARGET],
        rho_max,
        config.costs,
        policy="ranking_perfect_information",
    )
    actual_realized = _realize_complete_plan(
        actual_plan,
        target_df,
        config.costs,
        origin,
        "actual_future_values_oracle_only",
        uses_future_actuals=True,
    )
    oracle_cost = float(actual_realized["total_realized_cost"].sum())

    rows: List[Dict[str, object]] = []
    summary_lookup = rolling_summary.loc[
        rolling_summary["protocol"].eq("fixed_origin_9_month")
    ].set_index(["target", "model"])
    spec_lookup = {spec.model: spec for spec in specs}
    for target in [AVAILABLE_TARGET, OCCUPIED_TARGET]:
        canonical = str(
            selection.loc[selection["target"].eq(target), "selected_model"].iloc[0]
        )
        candidates = _eligible_candidate_models(
            target, canonical, rolling_summary, specs
        )
        for candidate in candidates:
            candidate_selection = selection.copy()
            candidate_selection.loc[
                candidate_selection["target"].eq(target), "selected_model"
            ] = candidate
            candidate_selection.loc[
                candidate_selection["target"].eq(target), "feature_mode"
            ] = spec_lookup[candidate].feature_mode
            predictions = forecast_selected_models(
                target_df,
                training_dates,
                forecast_dates,
                candidate_selection,
                specs,
                config.random_seed,
            )
            plan, _ = solve_deterministic_capacity_plan(
                forecast_dates,
                predictions[AVAILABLE_TARGET],
                predictions[OCCUPIED_TARGET],
                rho_max,
                config.costs,
                policy=f"ranking_{target}_{candidate}",
            )
            realized = _realize_complete_plan(
                plan,
                target_df,
                config.costs,
                origin,
                f"candidate_{target}_{candidate}",
            )
            validation_row = summary_lookup.loc[(target, candidate)]
            target_actual = target_df.loc[forecast_dates, target].to_numpy(dtype=float)
            target_prediction = predictions[target].to_numpy(dtype=float)
            rows.append(
                {
                    "target": target,
                    "model": candidate,
                    "feature_mode": spec_lookup[candidate].feature_mode,
                    "canonical_selected_model": canonical,
                    "is_canonical_selected_model": candidate == canonical,
                    "validation_MAE": float(validation_row["mean_MAE"]),
                    "final_test_MAE": float(
                        np.mean(np.abs(target_actual - target_prediction))
                    ),
                    "realized_optimization_cost": float(
                        realized["total_realized_cost"].sum()
                    ),
                    "shortage_bed_months": float(
                        realized["realized_shortage_bed_months"].sum()
                    ),
                    "extra_capacity_bed_months": float(
                        realized["additional_capacity_active"].sum()
                    ),
                    "regret": float(realized["total_realized_cost"].sum())
                    - oracle_cost,
                    "rho_max": rho_max,
                    "parameter_set_id": config.costs.parameter_set_id,
                    "identical_optimizer_and_costs": True,
                    "final_test_used_to_select_forecast_model": False,
                }
            )
    result = pd.DataFrame(rows)
    result["forecast_rank"] = (
        result.groupby("target")["validation_MAE"]
        .rank(method="min", ascending=True)
        .astype(int)
    )
    result["decision_rank"] = (
        result.groupby("target")["realized_optimization_cost"]
        .rank(method="min", ascending=True)
        .astype(int)
    )
    target_answers = {}
    for target, group in result.groupby("target"):
        forecast_winner = group.sort_values(
            ["validation_MAE", "model"]
        ).iloc[0]["model"]
        decision_winner = group.sort_values(
            ["realized_optimization_cost", "model"]
        ).iloc[0]["model"]
        target_answers[target] = forecast_winner == decision_winner
    result["did_lowest_MAE_also_produce_lowest_cost"] = result["target"].map(
        target_answers
    )
    result["ranking_interpretation"] = np.where(
        result["did_lowest_MAE_also_produce_lowest_cost"],
        "Lowest validation MAE also minimized realized cost among tested candidates.",
        "Decision winner differed because capacity constraints and asymmetric shortage cost weight forecast direction and timing, not only absolute error.",
    )
    return result.sort_values(["target", "forecast_rank", "model"]).reset_index(
        drop=True
    )


def build_los_pressure_diagnostics(
    target_df: pd.DataFrame,
    selection: pd.DataFrame,
    final_predictions: pd.DataFrame,
    audited_test_intervals: pd.DataFrame,
    realized: pd.DataFrame,
    config: OptimizationEvaluationConfig,
) -> pd.DataFrame:
    """Describe LOS pressure beside decisions without adding LOS to census."""

    origin = pd.Timestamp(config.final_test_start) - pd.offsets.MonthBegin(1)
    point_shortage = realized.loc[
        realized["policy"].eq("point_forecast")
        & realized["reoptimization_frequency_months"].eq(1)
        & np.isclose(realized["rho_max"], 0.90)
    ][["forecast_month", "realized_shortage_bed_months"]].copy()
    occupied_point = final_predictions.loc[
        final_predictions["target"].eq(OCCUPIED_TARGET)
    ][["forecast_date", "prediction"]].rename(
        columns={"prediction": "occupied_point_forecast"}
    )
    rows: List[pd.DataFrame] = []
    count_trigger_frames: List[pd.DataFrame] = []
    for target in LOS_TARGETS:
        point = final_predictions.loc[
            final_predictions["target"].eq(target)
        ][
            [
                "forecast_date",
                "prediction",
                "actual",
                "selected_model",
            ]
        ].rename(
            columns={
                "prediction": "los_point_forecast",
                "actual": "los_actual",
            }
        )
        interval = audited_test_intervals.loc[
            audited_test_intervals["target"].eq(target)
            & np.isclose(audited_test_intervals["nominal_level"], 0.95)
        ][
            [
                "forecast_date",
                "lower",
                "upper",
                "selection_status",
                "selected_uncertainty_method",
            ]
        ]
        diagnostic = (
            point.merge(interval, on="forecast_date", validate="one_to_one")
            .merge(occupied_point, on="forecast_date", validate="one_to_one")
            .merge(
                point_shortage,
                left_on="forecast_date",
                right_on="forecast_month",
                validate="one_to_one",
            )
        )
        threshold_80 = float(target_df.loc[:origin, target].quantile(0.80))
        threshold_90 = float(target_df.loc[:origin, target].quantile(0.90))
        diagnostic["target"] = target
        diagnostic["historical_80th_percentile"] = threshold_80
        diagnostic["historical_90th_percentile"] = threshold_90
        diagnostic["point_exceeds_80th_percentile"] = (
            diagnostic["los_point_forecast"] > threshold_80
        )
        diagnostic["point_exceeds_90th_percentile"] = (
            diagnostic["los_point_forecast"] > threshold_90
        )
        diagnostic["upper_bound_exceeds_90th_percentile"] = (
            diagnostic["upper"] > threshold_90
        )
        diagnostic["actual_occupancy_adjusted_pressure"] = np.maximum(
            target_df.loc[
                pd.DatetimeIndex(diagnostic["forecast_date"]), OCCUPIED_TARGET
            ].to_numpy(dtype=float)
            / 0.90
            - target_df.loc[
                pd.DatetimeIndex(diagnostic["forecast_date"]), AVAILABLE_TARGET
            ].to_numpy(dtype=float),
            0.0,
        )
        diagnostic["point_LOS_vs_occupied_forecast_correlation"] = diagnostic[
            "los_point_forecast"
        ].corr(diagnostic["occupied_point_forecast"])
        diagnostic["actual_LOS_vs_occupancy_pressure_correlation"] = diagnostic[
            "los_actual"
        ].corr(diagnostic["actual_occupancy_adjusted_pressure"])
        diagnostic["point_LOS_vs_realized_shortage_correlation"] = diagnostic[
            "los_point_forecast"
        ].corr(diagnostic["realized_shortage_bed_months"])
        diagnostic["correlation_interpretation"] = (
            "Descriptive association only; no causal claim."
        )
        diagnostic["primary_LOS_signal"] = config.primary_los_signal
        diagnostic["is_primary_LOS_signal"] = target == config.primary_los_signal
        diagnostic["LOS_pressure_mode_enabled"] = config.los_pressure_mode
        diagnostic["LOS_added_to_occupied_census"] = False
        diagnostic["trigger_requires_stakeholder_approval"] = True
        rows.append(diagnostic)
        if target.startswith("los_count_"):
            count_trigger_frames.append(
                diagnostic[
                    ["forecast_date", "point_exceeds_90th_percentile"]
                ].rename(
                    columns={
                        "point_exceeds_90th_percentile": f"{target}_trigger"
                    }
                )
            )

    result = pd.concat(rows, ignore_index=True)
    simultaneous = count_trigger_frames[0]
    for trigger_frame in count_trigger_frames[1:]:
        simultaneous = simultaneous.merge(
            trigger_frame, on="forecast_date", validate="one_to_one"
        )
    trigger_columns = [
        column for column in simultaneous if column.endswith("_trigger")
    ]
    simultaneous["simultaneously_elevated_count_signals"] = simultaneous[
        trigger_columns
    ].sum(axis=1)
    result = result.merge(
        simultaneous[
            ["forecast_date", "simultaneously_elevated_count_signals"]
        ],
        on="forecast_date",
        validate="many_to_one",
    )
    result["optional_multi_signal_stress_trigger"] = (
        result["simultaneously_elevated_count_signals"] >= 2
    )
    return result.sort_values(["target", "forecast_date"]).reset_index(drop=True)


def add_scenario_shortage_risk_by_horizon(
    horizon_summary: pd.DataFrame,
    decisions: pd.DataFrame,
    scenario_cache: Mapping[pd.Timestamp, pd.DataFrame],
) -> pd.DataFrame:
    """Add development-scenario shortage risk for the first nine-month plan."""

    first_origin = min(scenario_cache)
    scenarios = scenario_cache[first_origin]
    rows: List[Dict[str, object]] = []
    first_plans = decisions.loc[
        decisions["forecast_origin"].eq(first_origin)
    ].copy()
    for keys, plan in first_plans.groupby(
        ["policy", "reoptimization_frequency_months", "rho_max"], sort=True
    ):
        policy, frequency, rho_max = keys
        for decision in plan.itertuples():
            month_scenarios = scenarios.loc[
                scenarios["forecast_month"].eq(pd.Timestamp(decision.forecast_month))
            ]
            shortage_values = np.maximum(
                month_scenarios["occupied_demand"].to_numpy(dtype=float) / rho_max
                - month_scenarios["available_capacity"].to_numpy(dtype=float)
                - float(decision.additional_capacity_active),
                0.0,
            )
            probabilities = month_scenarios["probability"].to_numpy(dtype=float)
            rows.append(
                {
                    "policy": policy,
                    "reoptimization_frequency_months": int(frequency),
                    "rho_max": float(rho_max),
                    "horizon_step": int(decision.horizon_step),
                    "development_scenario_shortage_probability": float(
                        probabilities[shortage_values > 1e-7].sum()
                    ),
                    "development_scenario_expected_shortage": float(
                        np.sum(probabilities * shortage_values)
                    ),
                    "shortage_risk_scenario_source": (
                        "complete_paired_development_residual_paths"
                    ),
                }
            )
    risk = pd.DataFrame(rows)
    return horizon_summary.merge(
        risk,
        on=[
            "policy",
            "reoptimization_frequency_months",
            "rho_max",
            "horizon_step",
        ],
        how="left",
        validate="one_to_one",
    )


def _save_figure(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def generate_optimization_plots(
    uncertainty_horizon: pd.DataFrame,
    optimization_horizon: pd.DataFrame,
    uncertainty_value: pd.DataFrame,
    decisions: pd.DataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    """Create the five requested presentation plots and return a manifest."""

    plot_dir = output_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="notebook")
    manifest: List[Dict[str, str]] = []

    selected_95 = uncertainty_horizon.loc[
        np.isclose(uncertainty_horizon["nominal_level"], 0.95)
    ].copy()
    count_targets = [
        AVAILABLE_TARGET,
        OCCUPIED_TARGET,
        "los_count_7_or_more_days",
        "los_count_14_or_more_days",
        "los_count_21_or_more_days",
    ]
    rate_targets = [target for target in TARGETS if target not in count_targets]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for target in count_targets:
        subset = selected_95.loc[selected_95["target"].eq(target)]
        axes[0].plot(
            subset["horizon_step"],
            subset["mean_interval_width"],
            marker="o",
            label=target,
        )
    for target in rate_targets:
        subset = selected_95.loc[selected_95["target"].eq(target)]
        axes[1].plot(
            subset["horizon_step"],
            subset["mean_interval_width"],
            marker="o",
            label=target,
        )
    axes[0].set(title="95% interval width: counts", xlabel="Horizon step", ylabel="Mean width")
    axes[1].set(title="95% interval width: rates", xlabel="Horizon step", ylabel="Mean width")
    for axis in axes:
        axis.legend(fontsize=7)
    fig.suptitle("Selected uncertainty width by forecast horizon")
    path = plot_dir / "interval_width_vs_horizon.png"
    _save_figure(fig, path)
    manifest.append({"plot": "interval_width_vs_horizon", "path": str(path)})

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for target in count_targets:
        subset = selected_95.loc[selected_95["target"].eq(target)]
        axes[0].plot(
            subset["horizon_step"],
            subset["point_forecast_MAE"],
            marker="o",
            label=target,
        )
    for target in rate_targets:
        subset = selected_95.loc[selected_95["target"].eq(target)]
        axes[1].plot(
            subset["horizon_step"],
            subset["point_forecast_MAE"],
            marker="o",
            label=target,
        )
    axes[0].set(title="Forecast error: counts", xlabel="Horizon step", ylabel="Development MAE")
    axes[1].set(title="Forecast error: rates", xlabel="Horizon step", ylabel="Development MAE")
    for axis in axes:
        axis.legend(fontsize=7)
    fig.suptitle("Locked point-forecast error by horizon")
    path = plot_dir / "forecast_error_vs_horizon.png"
    _save_figure(fig, path)
    manifest.append({"plot": "forecast_error_vs_horizon", "path": str(path)})

    first_origin = pd.to_datetime(decisions["forecast_origin"]).min()
    plan = decisions.loc[
        decisions["forecast_origin"].eq(first_origin)
        & np.isclose(decisions["rho_max"], 0.90)
        & decisions["reoptimization_frequency_months"].eq(1)
        & decisions["policy"].isin(IMPLEMENTABLE_POLICIES)
    ].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    for policy, subset in plan.groupby("policy"):
        ax.plot(
            subset["horizon_step"],
            subset["additional_capacity_active"],
            marker="o",
            label=policy,
        )
    ax.set(
        title="First-origin additional capacity plan at rho_max = 0.90",
        xlabel="Horizon step",
        ylabel="Additional operational bed-equivalents",
    )
    ax.legend(fontsize=8)
    path = plot_dir / "required_extra_capacity_vs_horizon.png"
    _save_figure(fig, path)
    manifest.append({"plot": "required_extra_capacity_vs_horizon", "path": str(path)})

    cost_difference = uncertainty_value.loc[
        np.isclose(uncertainty_value["rho_max"], 0.90)
    ].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    sns.barplot(
        data=cost_difference,
        x="reoptimization_frequency_months",
        y="uncertainty_value",
        hue="policy",
        ax=ax,
    )
    ax.axhline(0.0, color="black", linewidth=1)
    ax.set(
        title="Cost difference: point minus uncertainty-aware policy",
        xlabel="Update frequency (months)",
        ylabel="Uncertainty value (illustrative GBP)",
    )
    path = plot_dir / "point_vs_uncertainty_cost_difference.png"
    _save_figure(fig, path)
    manifest.append(
        {"plot": "point_vs_uncertainty_cost_difference", "path": str(path)}
    )

    risk = optimization_horizon.loc[
        np.isclose(optimization_horizon["rho_max"], 0.90)
        & optimization_horizon["reoptimization_frequency_months"].eq(1)
        & optimization_horizon["policy"].isin(IMPLEMENTABLE_POLICIES)
    ].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    for policy, subset in risk.groupby("policy"):
        ax.plot(
            subset["horizon_step"],
            subset["development_scenario_shortage_probability"],
            marker="o",
            label=policy,
        )
    ax.set(
        title="Development-scenario shortage risk by horizon",
        xlabel="Horizon step",
        ylabel="Shortage probability",
        ylim=(-0.02, 1.02),
    )
    ax.legend(fontsize=8)
    path = plot_dir / "shortage_risk_vs_horizon.png"
    _save_figure(fig, path)
    manifest.append({"plot": "shortage_risk_vs_horizon", "path": str(path)})

    return pd.DataFrame(manifest)


def _markdown_table(frame: pd.DataFrame) -> str:
    try:
        return frame.to_markdown(index=False)
    except ImportError:
        return frame.to_csv(index=False)


def build_optimization_experiment_markdown(
    selection: pd.DataFrame,
    registry: pd.DataFrame,
    policy_summary: pd.DataFrame,
    update_summary: pd.DataFrame,
    forecast_value: pd.DataFrame,
    uncertainty_value: pd.DataFrame,
    uncertainty_horizon: pd.DataFrame,
    model_ranking: pd.DataFrame,
    los_diagnostics: pd.DataFrame,
    stochastic_value: pd.DataFrame,
    history_sensitivity: pd.DataFrame,
    config: OptimizationEvaluationConfig,
) -> str:
    """Write a concise findings report directly from evaluated outputs."""

    central = policy_summary.loc[
        np.isclose(policy_summary["rho_max"], 0.90)
    ].copy()
    presentation = central[
        [
            "policy",
            "reoptimization_frequency_months",
            "total_realized_cost",
            "shortage_bed_months",
            "total_extra_staffed_bed_months",
            "regret_vs_perfect_information",
        ]
    ].copy()
    presentation["total_realized_cost"] = presentation[
        "total_realized_cost"
    ].round(0)
    presentation["shortage_bed_months"] = presentation[
        "shortage_bed_months"
    ].round(1)
    presentation["total_extra_staffed_bed_months"] = presentation[
        "total_extra_staffed_bed_months"
    ].round(1)
    presentation["regret_vs_perfect_information"] = presentation[
        "regret_vs_perfect_information"
    ].round(0)

    implementable = central.loc[
        central["policy"].isin(IMPLEMENTABLE_POLICIES)
    ]
    best = implementable.sort_values("total_realized_cost").iloc[0]
    monthly_vs = update_summary.loc[
        np.isclose(update_summary["rho_max"], 0.90)
        & update_summary["policy"].eq("point_forecast")
    ][
        [
            "reoptimization_frequency_months",
            "total_realized_cost",
            "freshness_value",
            "shortage_bed_months",
        ]
    ].copy()
    width_95 = uncertainty_horizon.loc[
        np.isclose(uncertainty_horizon["nominal_level"], 0.95)
    ].drop_duplicates("target")
    widened_count = int(width_95["uncertainty_widened_from_h1_to_h9"].sum())

    ranking_answers = (
        model_ranking.groupby("target")[
            "did_lowest_MAE_also_produce_lowest_cost"
        ]
        .first()
        .to_dict()
    )
    fv_central = forecast_value.loc[
        np.isclose(forecast_value["rho_max"], 0.90)
        & forecast_value["reoptimization_frequency_months"].eq(1)
    ][["policy", "forecast_value", "forecast_value_percent"]].copy()
    uv_central = uncertainty_value.loc[
        np.isclose(uncertainty_value["rho_max"], 0.90)
        & uncertainty_value["reoptimization_frequency_months"].eq(1)
    ][
        [
            "policy",
            "uncertainty_value",
            "shortage_reduction_vs_point",
            "added_capacity_cost_of_conservatism",
        ]
    ].copy()
    los_trigger_months = int(
        los_diagnostics.loc[
            los_diagnostics["target"].eq(config.primary_los_signal),
            "optional_multi_signal_stress_trigger",
        ].sum()
    )
    stochastic = stochastic_value.iloc[0]
    failed_windows = history_sensitivity.loc[
        history_sensitivity["status"].ne("ok"), "history_window_months"
    ].tolist()

    return f"""# Forecast-to-Optimization Evaluation

## Scope and information discipline

The forecasting winners and uncertainty selections remain locked.  Forecasts are rebuilt at
each monthly origin using all observations available by that origin.  The final September
2025-May 2026 outcomes do not tune costs, forecast winners, uncertainty methods, or policy
settings.  They are revealed only after each implementable decision.  The perfect-information
oracle is explicitly non-implementable.

The data contain 66 monthly national all-acute-trust aggregate observations.  They are not raw
daily, patient-level, trust-level, ward-level, or hospital-level records.

## Cost and solver audit

PuLP {pulp.__version__} with CBC solves a continuous linear program.  No integer ward decision
was required by accessible local documentation.  `glab` was not installed, so no authenticated
GitLab issue could be inspected; the audit therefore uses local files and pasted issue text only.
The central GBP 500 active-capacity and GBP 5,000 shortage values are illustrative sensitivity
values mentioned only by scale in the current issue.  They are not NHS-validated costs.  All
{len(registry)} registry rows requiring operational or economic judgement remain unvalidated.

## Model

The monthly decision `x_t` is additional staffed, funded, equipped and operational bed-equivalent
capacity.  Capacity follows `x_t = x_(t-1) + a_t - r_t`, respects ramp and maximum limits, and
balances available capacity plus `x_t` and shortage against occupied demand divided by `rho_max`.
The stochastic policy shares `x_t`, `a_t`, and `r_t` across paired residual scenarios and permits
scenario-specific shortage recourse.  CVaR is implemented but disabled in the base run.

LOS forecasts are diagnostics only.  They are never added to occupied census because long-stay
patients are already included in occupied beds.  The optional LOS pressure trigger is disabled.

## Central rho_max = 0.90 results

{_markdown_table(presentation)}

The lowest-cost implementable central-sensitivity run was `{best['policy']}` with a
{int(best['reoptimization_frequency_months'])}-month update frequency and illustrative realized
cost GBP {best['total_realized_cost']:,.0f}.  This is a sensitivity result, not a budget forecast.

### Forecast freshness for the point policy

{_markdown_table(monthly_vs.round(2))}

Positive freshness value means less-frequent updating cost more than monthly updating; negative
values mean it cost less in this small sealed test.

## Value of forecasting

{_markdown_table(fv_central.round(3))}

`ForecastValue = Cost_reactive - Cost_policy`.  Negative values are retained and mean that the
forecast policy did not improve the illustrative realized decision cost.

## Value of uncertainty

{_markdown_table(uv_central.round(3))}

`UncertaintyValue = Cost_point - Cost_uncertainty-aware`.  A negative value means extra
conservatism cost more than the shortage reduction was worth under the illustrative parameters.
The 90% and 95% available-bed bounds remain `exploratory_undercovered`; they are not guaranteed
calibrated intervals.

## Horizon behavior

At the 95% level, interval width from horizon 1 to horizon 9 increased for {widened_count} of
{len(TARGETS)} targets.  The project does not impose monotonic widening; target-level slopes,
coverage, misses, point MAE, and residual scenario standard deviation are exported.

## Forecast rank versus decision rank

For available beds, lowest validation MAE also minimized tested decision cost:
`{ranking_answers.get(AVAILABLE_TARGET)}`.  For occupied beds:
`{ranking_answers.get(OCCUPIED_TARGET)}`.  A disagreement is possible because an asymmetric
shortage penalty responds to error direction and timing, while MAE does not.

## Stochastic-programming diagnostics

Using identical costs and constraints at the first origin:

- wait-and-see cost: GBP {stochastic['wait_and_see_cost']:,.0f}
- stochastic recourse cost: GBP {stochastic['stochastic_recourse_solution_cost']:,.0f}
- expected result of the expected-value solution: GBP {stochastic['expected_result_of_expected_value_solution']:,.0f}
- EVPI: GBP {stochastic['expected_value_of_perfect_information']:,.0f}
- VSS: GBP {stochastic['value_of_stochastic_solution']:,.0f}

For minimization, `EVPI = recourse - wait-and-see` and `VSS = EEV - recourse`.

## LOS and history-window diagnostics

The optional multi-indicator LOS trigger fired in {los_trigger_months} sealed-test months for the
configured primary signal `{config.primary_los_signal}`.  It remains disabled and all correlations
are descriptive, not causal.  History-window sensitivity used 12, 24, 36, and all available target
observations; it never used 1-, 3-, or 6-month training windows.  Failed windows: {failed_windows or 'none'}.

## Decision gate

The model is ready for stakeholder review and methodological sensitivity analysis.  It is not
ready for decision-grade budgeting or deployment until finance, workforce, operations, clinical,
commissioning, and estates stakeholders validate marginal costs, shortage valuation, occupancy
policy, ramp limits, maximum deployable capacity, LOS-trigger policy, and any integer ward-size
requirement.  National aggregate forecasts also cannot prescribe trust- or ward-level actions
without more granular data.
"""


def run_optimization_evaluation(
    config: Optional[OptimizationEvaluationConfig] = None,
) -> Dict[str, object]:
    """Run the full forecast-to-optimization evaluation and save artifacts."""

    config = config or OptimizationEvaluationConfig()
    config.validate()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "plots").mkdir(parents=True, exist_ok=True)

    inputs = load_locked_project_inputs(config)
    target_df = inputs["target_df"]
    selection = inputs["selection"]
    evaluation_config = EvaluationConfig(
        workbook_path=config.workbook_path,
        sheet_name=config.sheet_name,
        output_dir=config.deterministic_output_dir,
        generate_plots=False,
        mark_uncertainty_stale=False,
    )
    specs = build_model_registry(evaluation_config)
    uncertainty_config = UncertaintyConfig(
        deterministic_output_dir=config.deterministic_output_dir,
        output_dir=config.uncertainty_output_dir,
        final_selection_path=(
            config.deterministic_output_dir / "final_model_selection_with_deep.csv"
        ),
        random_seed=config.random_seed,
        generate_plots=False,
    )
    selected_validation_points = build_selected_validation_predictions(
        selection, uncertainty_config
    )

    origins = monthly_backtest_origins(config.final_test_start, config.final_test_end)
    point_cache = build_locked_point_forecast_cache(
        target_df,
        selection,
        specs,
        origins,
        config.final_test_end,
        config.random_seed,
        config.planning_horizon,
    )
    reactive_cache = build_reactive_forecast_cache(
        target_df,
        origins,
        config.final_test_end,
        config.planning_horizon,
    )
    cqr_cache = build_cqr_raw_forecast_cache(
        target_df,
        origins,
        config.final_test_end,
        AVAILABLE_TARGET,
        (0.90, 0.95),
        config.random_seed,
        config.planning_horizon,
    )
    interval_cache = build_rolling_interval_cache(
        target_df,
        point_cache,
        selected_validation_points,
        inputs["uncertainty_selection"],
        inputs["cqr_validation"],
        cqr_cache,
        config,
    )
    residual_paths = build_paired_development_residual_paths(
        selected_validation_points
    )
    decisions, realized, scenario_cache = run_all_policy_backtests(
        target_df,
        point_cache,
        reactive_cache,
        interval_cache,
        residual_paths,
        config,
    )

    policy_summary = summarize_optimization_policies(realized)
    update_summary = build_update_frequency_summary(policy_summary)
    forecast_value = build_forecast_value_summary(policy_summary)
    uncertainty_value = build_uncertainty_value_summary(policy_summary)
    optimization_horizon = build_optimization_horizon_summary(decisions, realized)
    optimization_horizon = add_scenario_shortage_risk_by_horizon(
        optimization_horizon, decisions, scenario_cache
    )
    uncertainty_horizon = build_uncertainty_horizon_analysis(
        inputs["uncertainty_validation"],
        inputs["uncertainty_selection"],
        selected_validation_points,
    )
    first_origin = origins[0]
    stochastic_value = build_stochastic_value_summary(
        scenario_cache[pd.Timestamp(first_origin)],
        0.90,
        config.costs,
    )
    model_ranking = build_forecast_vs_decision_model_ranking(
        target_df,
        selection,
        inputs["rolling_summary"],
        specs,
        config,
        rho_max=0.90,
    )
    history_sensitivity = build_history_window_sensitivity(
        target_df,
        selection,
        specs,
        config,
        rho_max=0.90,
    )
    los_diagnostics = build_los_pressure_diagnostics(
        target_df,
        selection,
        inputs["final_predictions"],
        inputs["audited_test_intervals"],
        realized,
        config,
    )
    parameter_registry = build_optimization_parameter_registry(config)
    occupancy_sensitivity = policy_summary.copy()

    plot_manifest = pd.DataFrame()
    if config.generate_plots:
        plot_manifest = generate_optimization_plots(
            uncertainty_horizon,
            optimization_horizon,
            uncertainty_value,
            decisions,
            config.output_dir,
        )
    markdown = build_optimization_experiment_markdown(
        selection,
        parameter_registry,
        policy_summary,
        update_summary,
        forecast_value,
        uncertainty_value,
        uncertainty_horizon,
        model_ranking,
        los_diagnostics,
        stochastic_value,
        history_sensitivity,
        config,
    )

    artifacts: Dict[str, object] = {
        "decisions": decisions,
        "realized": realized,
        "policy_summary": policy_summary,
        "update_frequency_summary": update_summary,
        "optimization_horizon_summary": optimization_horizon,
        "uncertainty_horizon_analysis": uncertainty_horizon,
        "forecast_value_summary": forecast_value,
        "uncertainty_value_summary": uncertainty_value,
        "model_ranking": model_ranking,
        "occupancy_sensitivity": occupancy_sensitivity,
        "los_diagnostics": los_diagnostics,
        "parameter_registry": parameter_registry,
        "stochastic_value_summary": stochastic_value,
        "history_window_sensitivity": history_sensitivity,
        "point_forecast_cache": point_cache,
        "interval_cache": interval_cache,
        "paired_residual_paths": residual_paths,
        "first_origin_scenarios": scenario_cache[pd.Timestamp(first_origin)],
        "plot_manifest": plot_manifest,
        "experiment_markdown": markdown,
    }
    filenames = {
        "decisions": "optimization_backtest_decisions.csv",
        "realized": "optimization_backtest_realized_results.csv",
        "policy_summary": "optimization_policy_summary.csv",
        "update_frequency_summary": "optimization_update_frequency_summary.csv",
        "optimization_horizon_summary": "optimization_horizon_summary.csv",
        "uncertainty_horizon_analysis": "uncertainty_horizon_analysis.csv",
        "forecast_value_summary": "forecast_value_summary.csv",
        "uncertainty_value_summary": "uncertainty_value_summary.csv",
        "model_ranking": "forecast_vs_decision_model_ranking.csv",
        "occupancy_sensitivity": "occupancy_threshold_sensitivity.csv",
        "los_diagnostics": "los_pressure_diagnostics.csv",
        "parameter_registry": "optimization_parameter_registry.csv",
        "stochastic_value_summary": "stochastic_value_summary.csv",
        "history_window_sensitivity": "history_window_sensitivity.csv",
        "point_forecast_cache": "rolling_locked_point_forecasts.csv",
        "interval_cache": "rolling_uncertainty_inputs.csv",
        "paired_residual_paths": "paired_development_residual_paths.csv",
        "first_origin_scenarios": "first_origin_stochastic_scenarios.csv",
        "plot_manifest": "plot_manifest.csv",
    }
    for key, filename in filenames.items():
        value = artifacts[key]
        if isinstance(value, pd.DataFrame):
            value.to_csv(config.output_dir / filename, index=False)
    report_path = config.output_dir / "optimization_experiment_summary.md"
    report_path.write_text(markdown, encoding="utf-8")

    run_manifest = {
        "status": "complete",
        "data_frequency": "monthly",
        "data_scope": "national all-acute-trust aggregate",
        "final_test_start": str(config.final_test_start.date()),
        "final_test_end": str(config.final_test_end.date()),
        "forecast_winners_changed": False,
        "uncertainty_selection_changed": False,
        "cost_parameter_set": asdict(config.costs),
        "update_frequencies": list(config.update_frequencies),
        "occupancy_thresholds": list(config.occupancy_thresholds),
        "scenario_count": config.scenario_count,
        "CVaR_enabled": config.cvar_weight > 0,
        "LOS_pressure_mode_enabled": config.los_pressure_mode,
        "GitLab_remote_inspected": False,
        "GitLab_remote_reason": "glab is not installed and no local Git repository metadata exists",
        "optimizer": f"PuLP {pulp.__version__} with CBC",
    }
    (config.output_dir / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2),
        encoding="utf-8",
    )
    artifacts["run_manifest"] = run_manifest
    return artifacts


def load_optimization_outputs(
    output_dir: Path = Path("outputs/final_optimization"),
) -> Dict[str, object]:
    """Load cached presentation outputs without rerunning forecasts or solvers."""

    filenames = {
        "decisions": "optimization_backtest_decisions.csv",
        "realized": "optimization_backtest_realized_results.csv",
        "policy_summary": "optimization_policy_summary.csv",
        "update_frequency_summary": "optimization_update_frequency_summary.csv",
        "optimization_horizon_summary": "optimization_horizon_summary.csv",
        "uncertainty_horizon_analysis": "uncertainty_horizon_analysis.csv",
        "forecast_value_summary": "forecast_value_summary.csv",
        "uncertainty_value_summary": "uncertainty_value_summary.csv",
        "model_ranking": "forecast_vs_decision_model_ranking.csv",
        "occupancy_sensitivity": "occupancy_threshold_sensitivity.csv",
        "los_diagnostics": "los_pressure_diagnostics.csv",
        "parameter_registry": "optimization_parameter_registry.csv",
        "stochastic_value_summary": "stochastic_value_summary.csv",
        "history_window_sensitivity": "history_window_sensitivity.csv",
        "point_forecast_cache": "rolling_locked_point_forecasts.csv",
        "interval_cache": "rolling_uncertainty_inputs.csv",
        "paired_residual_paths": "paired_development_residual_paths.csv",
        "first_origin_scenarios": "first_origin_stochastic_scenarios.csv",
        "plot_manifest": "plot_manifest.csv",
    }
    date_columns = {
        "decisions": ["forecast_month", "forecast_origin", "decision_information_cutoff"],
        "realized": ["forecast_month", "forecast_origin", "decision_information_cutoff"],
        "los_diagnostics": ["forecast_date", "forecast_month"],
        "point_forecast_cache": [
            "forecast_origin",
            "forecast_date",
            "training_start",
            "training_end",
        ],
        "interval_cache": [
            "forecast_origin",
            "forecast_date",
            "calibration_cutoff",
            "maximum_calibration_outcome_date",
        ],
        "first_origin_scenarios": ["forecast_month"],
        "history_window_sensitivity": ["training_start", "training_end"],
    }
    outputs: Dict[str, object] = {}
    for key, filename in filenames.items():
        path = output_dir / filename
        outputs[key] = pd.read_csv(path, parse_dates=date_columns.get(key, []))
    outputs["experiment_markdown"] = (
        output_dir / "optimization_experiment_summary.md"
    ).read_text(encoding="utf-8")
    outputs["run_manifest"] = json.loads(
        (output_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the NHS forecast-to-optimization evaluation."
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip plot generation.",
    )
    parser.add_argument(
        "--scenario-count",
        type=int,
        default=200,
        help="Number of paired residual scenarios.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = OptimizationEvaluationConfig(
        scenario_count=args.scenario_count,
        generate_plots=not args.no_plots,
    )
    outputs = run_optimization_evaluation(config)
    summary = outputs["policy_summary"]
    central = summary.loc[np.isclose(summary["rho_max"], 0.90)]
    print("Forecast winners changed: False")
    print("Costs validated:", config.costs.validated)
    print("Central rho_max = 0.90 policy summary:")
    print(
        central[
            [
                "policy",
                "reoptimization_frequency_months",
                "total_realized_cost",
                "shortage_bed_months",
                "total_extra_staffed_bed_months",
                "regret_vs_perfect_information",
            ]
        ].to_string(index=False)
    )
    print("Outputs:", config.output_dir.resolve())


if __name__ == "__main__":
    main()
