"""Validation-selected uncertainty and future forecasts for NHS capacity.

This module consumes deterministic winners selected before the sealed final
test.  Interval methods are compared only with development-period rolling-origin
errors that would have been observable at each forecast origin.  The selected
method is then locked, evaluated once on September 2025-May 2026, and refitted
for genuine forecasts after May 2026.

Conformal methods are used as pragmatic calibration tools.  The small,
dependent monthly sample does not justify an unqualified exchangeable-data
finite-sample coverage claim.
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from deep_forecasting_evaluation import (
    DeepEvaluationConfig,
    forecast_deep_seed_ensemble,
)
from forecasting_evaluation import (
    EvaluationConfig,
    RATE_TARGETS,
    TARGETS,
    apply_domain_bounds,
    build_feature_frame,
    build_model_registry,
    build_recursive_feature_row,
    calculate_metrics,
    forecast_selected_models,
    load_nhs_targets,
    seasonal_naive_forecast,
    split_development_and_final_test,
    usable_evaluation_frame,
)


INTERVAL_LEVELS = (0.80, 0.90, 0.95)
PRIMARY_PROTOCOL = "fixed_origin_9_month"
PRESSURE_TARGETS = set(TARGETS) - {"g_and_a_beds_available"}
METHOD_RESIDUAL_BOOTSTRAP = "residual_bootstrap"
METHOD_SPLIT_CONFORMAL = "split_conformal_residual"
METHOD_SPLIT_HORIZON = "split_conformal_horizon_specific"
METHOD_ROLLING_CONFORMAL = "rolling_conformal_residual"
METHOD_CQR = "CQR_quantile_boosting"
METHOD_ENBPI_STYLE = "EnbPI_style"
METHOD_ACI = "ACI_batch_approximation"
METHOD_NATIVE_SARIMA = "native_SARIMA"
METHOD_NATIVE_ETS = "native_ETS_unavailable"
METHOD_NATIVE_DEEP = "native_deep_quantiles_not_applicable"


@dataclass(frozen=True)
class UncertaintyConfig:
    """Store interval-calibration, future-horizon, and planning settings."""

    deterministic_output_dir: Path = Path("outputs/final_evaluation")
    output_dir: Path = Path("outputs/final_uncertainty")
    final_selection_path: Path = Path(
        "outputs/final_evaluation/final_model_selection_with_deep.csv"
    )
    levels: Tuple[float, ...] = INTERVAL_LEVELS
    minimum_calibration_errors: int = 9
    rolling_calibration_window: int = 18
    enbpi_block_length: int = 3
    enbpi_bootstrap_repetitions: int = 500
    residual_bootstrap_repetitions: int = 2000
    aci_gamma: float = 0.05
    random_seed: int = 42
    future_horizon: int = 9
    rho_max: float = 0.90
    rho_max_note: str = (
        "Illustrative configurable planning scenario only; NHS stakeholders must validate rho_max."
    )
    generate_plots: bool = True


def validate_uncertainty_config(config: UncertaintyConfig) -> None:
    """Reject invalid interval levels, horizons, or planning parameters."""

    if not config.levels or any(level <= 0 or level >= 1 for level in config.levels):
        raise ValueError("Interval levels must lie strictly between zero and one.")
    if sorted(config.levels) != list(config.levels):
        raise ValueError("Interval levels must be increasing.")
    if config.minimum_calibration_errors < 3:
        raise ValueError("At least three historical forecast errors are required.")
    if config.future_horizon != 9:
        raise ValueError("The final workflow currently requires a nine-month planning horizon.")
    if config.rho_max <= 0 or config.rho_max >= 1:
        raise ValueError("rho_max must be a proportion strictly between zero and one.")


def finite_sample_quantile(values: Sequence[float], level: float) -> float:
    """Return the conservative split-conformal quantile with finite-n correction."""

    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if len(array) == 0:
        raise ValueError("Cannot calculate a conformal quantile from no values.")
    corrected = min(1.0, math.ceil((len(array) + 1) * level) / len(array))
    try:
        return float(np.quantile(array, corrected, method="higher"))
    except TypeError:  # pragma: no cover - NumPy compatibility
        return float(np.quantile(array, corrected, interpolation="higher"))


def interval_score_values(
    actual: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
    level: float,
) -> np.ndarray:
    """Calculate the Winkler interval score for each observation."""

    y = np.asarray(actual, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    alpha = 1 - level
    score = hi - lo
    score += (2 / alpha) * (lo - y) * (y < lo)
    score += (2 / alpha) * (y - hi) * (y > hi)
    return score


def enforce_interval_bounds(
    target: str,
    point: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Order interval bounds, include the point, and apply target-domain checks."""

    point = np.asarray(point, dtype=float)
    low = np.minimum(np.asarray(lower, dtype=float), np.asarray(upper, dtype=float))
    high = np.maximum(np.asarray(lower, dtype=float), np.asarray(upper, dtype=float))
    low = np.minimum(low, point)
    high = np.maximum(high, point)
    if target in RATE_TARGETS:
        low = np.clip(low, 0.0, 1.2)
        high = np.clip(high, 0.0, 1.2)
    else:
        low = np.maximum(low, 0.0)
        high = np.maximum(high, 0.0)
    return low, high


def load_locked_point_artifacts(
    config: UncertaintyConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load validation-selected winners and their validation and test predictions."""

    selection = pd.read_csv(config.final_selection_path)
    if not selection["selection_was_locked_before_test"].astype(str).str.lower().eq("true").all():
        raise AssertionError("Point models must be locked before uncertainty evaluation.")
    validation = build_selected_validation_predictions(selection, config)
    final_test = pd.read_csv(
        config.deterministic_output_dir / "final_test_predictions_with_deep.csv",
        parse_dates=["forecast_origin", "forecast_date"],
    )
    if not final_test["selection_was_locked_before_test"].astype(str).str.lower().eq("true").all():
        raise AssertionError("Final-test point predictions are not marked as locked.")
    return selection, validation, final_test


def build_selected_validation_predictions(
    selection: pd.DataFrame,
    config: UncertaintyConfig,
) -> pd.DataFrame:
    """Recover only validation predictions for each newly locked point winner."""

    deterministic = pd.read_csv(
        config.deterministic_output_dir / "rolling_origin_predictions.csv",
        parse_dates=["forecast_origin", "forecast_date"],
    )
    deep = pd.read_csv(
        config.deterministic_output_dir / "deep_fixed_origin_predictions.csv",
        parse_dates=["forecast_origin", "forecast_date"],
    )
    rows = []
    for target in TARGETS:
        selected = selection.loc[selection["target"].eq(target)].iloc[0]
        model = str(selected["selected_model"])
        if selected["deep_or_non_deep"] == "deep":
            subset = deep.loc[
                deep["target"].eq(target)
                & deep["model"].eq(model)
                & deep["stage"].eq("stage2_seed_ensemble")
            ].copy()
        else:
            subset = deterministic.loc[
                deterministic["target"].eq(target)
                & deterministic["model"].eq(model)
                & deterministic["protocol"].eq(PRIMARY_PROTOCOL)
            ].copy()
        if len(subset) != 63:
            raise AssertionError(
                f"Expected 63 selected validation predictions for {target}; found {len(subset)}."
            )
        subset["selected_model"] = model
        subset["selection_was_validation_only"] = True
        rows.append(
            subset[
                [
                    "target",
                    "selected_model",
                    "feature_mode",
                    "protocol",
                    "fold_id",
                    "forecast_origin",
                    "forecast_date",
                    "horizon_step",
                    "actual",
                    "prediction",
                    "error",
                    "absolute_error",
                    "selection_was_validation_only",
                ]
            ]
        )
    result = pd.concat(rows, ignore_index=True)
    result = result.sort_values(["target", "forecast_origin", "horizon_step"]).reset_index(drop=True)
    return result


def available_calibration_records(
    target_predictions: pd.DataFrame,
    forecast_origin: pd.Timestamp,
) -> pd.DataFrame:
    """Return prior forecast errors whose outcomes were known by an origin."""

    origin = pd.Timestamp(forecast_origin)
    available = target_predictions.loc[
        (target_predictions["forecast_origin"] < origin)
        & (target_predictions["forecast_date"] <= origin)
    ].copy()
    if not available.empty and available["forecast_date"].max() > origin:
        raise AssertionError("Calibration used an outcome not observable at the forecast origin.")
    return available.sort_values(["forecast_date", "forecast_origin", "horizon_step"])


def _quantile_pipeline(quantile: float, seed: int) -> Pipeline:
    """Build the fixed fold-local quantile boosting pipeline used by CQR."""

    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                GradientBoostingRegressor(
                    loss="quantile",
                    alpha=quantile,
                    n_estimators=160,
                    learning_rate=0.03,
                    max_depth=2,
                    min_samples_leaf=2,
                    random_state=seed,
                ),
            ),
        ]
    )


def recursive_quantile_forecast(
    target: str,
    target_df: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
    quantile: float,
    seed: int,
) -> np.ndarray:
    """Forecast one quantile recursively without reading horizon actuals."""

    training_dates = pd.DatetimeIndex(training_dates)
    forecast_dates = pd.DatetimeIndex(forecast_dates)
    history = target_df.loc[: training_dates.max(), TARGETS].copy()
    feature_frame = build_feature_frame(target, history, "target_only_features")
    x_train = feature_frame.loc[training_dates]
    y_train = target_df.loc[training_dates, target]
    model = _quantile_pipeline(quantile, seed)
    model.fit(x_train, y_train)
    simulated = history.copy()
    predictions = []
    for forecast_date in forecast_dates:
        row = build_recursive_feature_row(
            target,
            simulated,
            pd.Timestamp(forecast_date),
            "target_only_features",
        )
        value = float(model.predict(row)[0])
        value = float(apply_domain_bounds(target, [value])[0])
        new_row = pd.DataFrame(np.nan, index=[forecast_date], columns=TARGETS)
        new_row.loc[forecast_date, target] = value
        simulated = pd.concat([simulated, new_row])
        predictions.append(value)
    values = np.asarray(predictions, dtype=float)
    if len(values) != len(forecast_dates) or not np.isfinite(values).all():
        raise AssertionError("Recursive quantile forecast is incomplete or non-finite.")
    return values


def build_raw_cqr_validation_predictions(
    target_df: pd.DataFrame,
    point_validation: pd.DataFrame,
    config: UncertaintyConfig,
) -> pd.DataFrame:
    """Fit fold-local raw quantile paths before any conformal correction."""

    rows: List[Dict[str, object]] = []
    for target in TARGETS:
        target_points = point_validation.loc[point_validation["target"].eq(target)]
        for fold_id, fold_points in target_points.groupby("fold_id", sort=True):
            fold_points = fold_points.sort_values("horizon_step")
            origin = pd.Timestamp(fold_points["forecast_origin"].iloc[0])
            training_dates = usable_evaluation_frame(target_df).index[
                usable_evaluation_frame(target_df).index <= origin
            ]
            forecast_dates = pd.DatetimeIndex(fold_points["forecast_date"])
            for level in config.levels:
                alpha = 1 - level
                try:
                    lower = recursive_quantile_forecast(
                        target,
                        target_df,
                        training_dates,
                        forecast_dates,
                        alpha / 2,
                        config.random_seed,
                    )
                    upper = recursive_quantile_forecast(
                        target,
                        target_df,
                        training_dates,
                        forecast_dates,
                        1 - alpha / 2,
                        config.random_seed,
                    )
                    raw_lower = np.minimum(lower, upper)
                    raw_upper = np.maximum(lower, upper)
                    status = "ok"
                    failure = ""
                except Exception as exc:
                    raw_lower = np.full(len(forecast_dates), np.nan)
                    raw_upper = np.full(len(forecast_dates), np.nan)
                    status = "failed"
                    failure = f"{type(exc).__name__}: {exc}"
                for row, lo, hi in zip(fold_points.itertuples(), raw_lower, raw_upper):
                    score = max(float(lo) - row.actual, row.actual - float(hi), 0.0) if status == "ok" else np.nan
                    rows.append(
                        {
                            "target": target,
                            "fold_id": fold_id,
                            "forecast_origin": origin,
                            "forecast_date": row.forecast_date,
                            "horizon_step": row.horizon_step,
                            "level": level,
                            "actual": row.actual,
                            "point_prediction": row.prediction,
                            "raw_lower": lo,
                            "raw_upper": hi,
                            "nonconformity_score": score,
                            "status": status,
                            "failure_reason": failure,
                        }
                    )
    return pd.DataFrame(rows)


def _block_bootstrap_radius(
    absolute_errors: Sequence[float],
    level: float,
    block_length: int,
    repetitions: int,
    rng: np.random.Generator,
) -> float:
    """Return a block-bootstrap residual radius for the EnbPI-style benchmark."""

    errors = np.asarray(absolute_errors, dtype=float)
    n = len(errors)
    if n < block_length:
        return finite_sample_quantile(errors, level)
    starts = np.arange(0, n - block_length + 1)
    radii = []
    blocks_needed = math.ceil(n / block_length)
    for _ in range(repetitions):
        selected = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([errors[start : start + block_length] for start in selected])[:n]
        radii.append(finite_sample_quantile(sample, level))
    return float(np.median(radii))


def aci_adjusted_alpha(
    absolute_errors: Sequence[float],
    nominal_alpha: float,
    gamma: float,
) -> float:
    """Apply the ACI alpha recursion to an already observed residual stream.

    The resulting alpha is frozen when a complete fixed-origin nine-month path
    is issued.  This is therefore a batch approximation to online ACI, not the
    complete procedure in which alpha is updated after every newly observed
    outcome.
    """

    errors = np.asarray(absolute_errors, dtype=float)
    alpha_t = float(nominal_alpha)
    for index in range(3, len(errors)):
        radius = finite_sample_quantile(errors[:index], 1 - alpha_t)
        miss = int(errors[index] > radius)
        alpha_t = float(np.clip(alpha_t + gamma * (nominal_alpha - miss), 0.001, 0.50))
    return alpha_t


def _native_sarima_interval(
    history: pd.Series,
    forecast_dates: pd.DatetimeIndex,
    level: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return correctly supported statsmodels SARIMA prediction bounds."""

    from statsmodels.tsa.statespace.sarimax import SARIMAX

    model = SARIMAX(
        history,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, 12),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fit = model.fit(disp=False, maxiter=200, warn_convergence=False)
    interval = fit.get_forecast(steps=len(forecast_dates)).conf_int(alpha=1 - level)
    return interval.iloc[:, 0].to_numpy(dtype=float), interval.iloc[:, 1].to_numpy(dtype=float)


def _interval_from_method(
    method: str,
    target: str,
    level: float,
    points: np.ndarray,
    calibration: pd.DataFrame,
    rng: np.random.Generator,
    config: UncertaintyConfig,
    cqr_raw_lower: Optional[np.ndarray] = None,
    cqr_raw_upper: Optional[np.ndarray] = None,
    cqr_scores: Optional[Sequence[float]] = None,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, object]]:
    """Construct one interval path from origin-available calibration errors."""

    signed = calibration["actual"].to_numpy(dtype=float) - calibration["prediction"].to_numpy(dtype=float)
    absolute = np.abs(signed)
    alpha = 1 - level
    details: Dict[str, object] = {
        "calibration_count": len(calibration),
        "aci_gamma": np.nan,
        "aci_final_alpha": np.nan,
    }
    if method == METHOD_RESIDUAL_BOOTSTRAP:
        sampled = rng.choice(signed, size=config.residual_bootstrap_repetitions, replace=True)
        lo_error = float(np.quantile(sampled, alpha / 2))
        hi_error = float(np.quantile(sampled, 1 - alpha / 2))
        lower = points + lo_error
        upper = points + hi_error
    elif method == METHOD_SPLIT_CONFORMAL:
        radius = finite_sample_quantile(absolute, level)
        lower, upper = points - radius, points + radius
    elif method == METHOD_ROLLING_CONFORMAL:
        recent = absolute[-config.rolling_calibration_window :]
        radius = finite_sample_quantile(recent, level)
        details["calibration_count"] = len(recent)
        lower, upper = points - radius, points + radius
    elif method == METHOD_ENBPI_STYLE:
        radius = _block_bootstrap_radius(
            absolute,
            level,
            config.enbpi_block_length,
            config.enbpi_bootstrap_repetitions,
            rng,
        )
        lower, upper = points - radius, points + radius
    elif method == METHOD_ACI:
        adjusted_alpha = aci_adjusted_alpha(absolute, alpha, config.aci_gamma)
        radius = finite_sample_quantile(absolute, 1 - adjusted_alpha)
        details["aci_gamma"] = config.aci_gamma
        details["aci_final_alpha"] = adjusted_alpha
        lower, upper = points - radius, points + radius
    elif method == METHOD_CQR:
        if cqr_raw_lower is None or cqr_raw_upper is None or cqr_scores is None:
            raise ValueError("CQR requires raw quantile bounds and prior nonconformity scores.")
        correction = finite_sample_quantile(cqr_scores, level)
        details["calibration_count"] = len(cqr_scores)
        lower = np.asarray(cqr_raw_lower) - correction
        upper = np.asarray(cqr_raw_upper) + correction
    else:
        raise KeyError(method)
    lower, upper = enforce_interval_bounds(target, points, lower, upper)
    return lower, upper, details


def build_validation_intervals(
    target_df: pd.DataFrame,
    selection: pd.DataFrame,
    point_validation: pd.DataFrame,
    raw_cqr: pd.DataFrame,
    config: UncertaintyConfig,
) -> pd.DataFrame:
    """Create nested-development intervals using only origin-available errors."""

    rows: List[Dict[str, object]] = []
    standard_methods = [
        METHOD_RESIDUAL_BOOTSTRAP,
        METHOD_SPLIT_CONFORMAL,
        METHOD_ROLLING_CONFORMAL,
        METHOD_CQR,
        METHOD_ENBPI_STYLE,
        METHOD_ACI,
    ]
    for target_index, target in enumerate(TARGETS):
        target_points = point_validation.loc[point_validation["target"].eq(target)].copy()
        selected_model = selection.loc[selection["target"].eq(target), "selected_model"].iloc[0]
        for fold_index, (fold_id, fold_points) in enumerate(
            target_points.groupby("fold_id", sort=True)
        ):
            fold_points = fold_points.sort_values("horizon_step")
            origin = pd.Timestamp(fold_points["forecast_origin"].iloc[0])
            calibration = available_calibration_records(target_points, origin)
            points = fold_points["prediction"].to_numpy(dtype=float)
            forecast_dates = pd.DatetimeIndex(fold_points["forecast_date"])

            for level_index, level in enumerate(config.levels):
                common = {
                    "target": target,
                    "selected_point_model": selected_model,
                    "protocol": PRIMARY_PROTOCOL,
                    "fold_id": fold_id,
                    "forecast_origin": origin,
                    "level": level,
                    "nominal_coverage": level,
                    "calibration_scope": "pooled_horizon_prior_observable_errors",
                    "selection_period": "development_only",
                }
                if len(calibration) < config.minimum_calibration_errors:
                    rows.append(
                        {
                            **common,
                            "method": "all_methods",
                            "forecast_date": pd.NaT,
                            "horizon_step": np.nan,
                            "actual": np.nan,
                            "point_prediction": np.nan,
                            "lower": np.nan,
                            "upper": np.nan,
                            "calibration_count": len(calibration),
                            "status": "insufficient_calibration",
                            "failure_reason": (
                                f"Only {len(calibration)} observable prior errors; "
                                f"minimum is {config.minimum_calibration_errors}."
                            ),
                            "eligible_for_selection": False,
                        }
                    )
                    continue

                cqr_current = raw_cqr.loc[
                    raw_cqr["target"].eq(target)
                    & raw_cqr["fold_id"].eq(fold_id)
                    & np.isclose(raw_cqr["level"], level)
                    & raw_cqr["status"].eq("ok")
                ].sort_values("horizon_step")
                cqr_history = raw_cqr.loc[
                    raw_cqr["target"].eq(target)
                    & np.isclose(raw_cqr["level"], level)
                    & raw_cqr["status"].eq("ok")
                    & (raw_cqr["forecast_origin"] < origin)
                    & (raw_cqr["forecast_date"] <= origin)
                ].sort_values(["forecast_date", "forecast_origin", "horizon_step"])

                for method_index, method in enumerate(standard_methods):
                    seed = (
                        config.random_seed
                        + target_index * 10000
                        + fold_index * 100
                        + level_index * 10
                        + method_index
                    )
                    rng = np.random.default_rng(seed)
                    try:
                        if method == METHOD_CQR:
                            if len(cqr_current) != len(fold_points):
                                raise ValueError("Raw CQR path is incomplete for this fold.")
                            if len(cqr_history) < config.minimum_calibration_errors:
                                raise ValueError("Too few origin-observable CQR calibration scores.")
                            lower, upper, details = _interval_from_method(
                                method,
                                target,
                                level,
                                points,
                                calibration,
                                rng,
                                config,
                                cqr_raw_lower=cqr_current["raw_lower"].to_numpy(dtype=float),
                                cqr_raw_upper=cqr_current["raw_upper"].to_numpy(dtype=float),
                                cqr_scores=cqr_history["nonconformity_score"].to_numpy(dtype=float),
                            )
                        else:
                            lower, upper, details = _interval_from_method(
                                method,
                                target,
                                level,
                                points,
                                calibration,
                                rng,
                                config,
                            )
                        status = "ok"
                        failure = ""
                    except Exception as exc:
                        lower = upper = np.full(len(fold_points), np.nan)
                        details = {"calibration_count": len(calibration), "aci_gamma": np.nan, "aci_final_alpha": np.nan}
                        status = "failed"
                        failure = f"{type(exc).__name__}: {exc}"
                    for point_row, lo, hi in zip(fold_points.itertuples(), lower, upper):
                        rows.append(
                            {
                                **common,
                                "method": method,
                                "forecast_date": point_row.forecast_date,
                                "horizon_step": point_row.horizon_step,
                                "actual": point_row.actual,
                                "point_prediction": point_row.prediction,
                                "lower": lo,
                                "upper": hi,
                                **details,
                                "status": status,
                                "failure_reason": failure,
                                "eligible_for_selection": status == "ok",
                            }
                        )

                # Horizon-specific conformal is reported as exploratory because
                # each horizon has only a handful of origin-observable errors.
                lower_values = []
                upper_values = []
                counts = []
                for point_row in fold_points.itertuples():
                    horizon_calibration = calibration.loc[
                        calibration["horizon_step"].eq(point_row.horizon_step)
                    ]
                    counts.append(len(horizon_calibration))
                    if len(horizon_calibration) >= 3:
                        radius = finite_sample_quantile(
                            horizon_calibration["absolute_error"], level
                        )
                        lower_values.append(point_row.prediction - radius)
                        upper_values.append(point_row.prediction + radius)
                    else:
                        lower_values.append(np.nan)
                        upper_values.append(np.nan)
                lower_values, upper_values = enforce_interval_bounds(
                    target,
                    points,
                    np.asarray(lower_values),
                    np.asarray(upper_values),
                )
                for point_row, lo, hi, count in zip(
                    fold_points.itertuples(), lower_values, upper_values, counts
                ):
                    rows.append(
                        {
                            **common,
                            "method": METHOD_SPLIT_HORIZON,
                            "forecast_date": point_row.forecast_date,
                            "horizon_step": point_row.horizon_step,
                            "actual": point_row.actual,
                            "point_prediction": point_row.prediction,
                            "lower": lo,
                            "upper": hi,
                            "calibration_count": count,
                            "aci_gamma": np.nan,
                            "aci_final_alpha": np.nan,
                            "status": "exploratory" if count >= 3 else "insufficient_calibration",
                            "failure_reason": (
                                "Horizon-specific 95% calibration is unstable with at most six prior fold errors."
                            ),
                            "eligible_for_selection": False,
                        }
                    )

                if selected_model == "SARIMA":
                    training_dates = usable_evaluation_frame(target_df).index[
                        usable_evaluation_frame(target_df).index <= origin
                    ]
                    try:
                        native_lower, native_upper = _native_sarima_interval(
                            target_df.loc[training_dates, target], forecast_dates, level
                        )
                        native_lower, native_upper = enforce_interval_bounds(
                            target, points, native_lower, native_upper
                        )
                        native_status, native_failure = "ok", ""
                    except Exception as exc:
                        native_lower = native_upper = np.full(len(fold_points), np.nan)
                        native_status, native_failure = "failed", f"{type(exc).__name__}: {exc}"
                    for point_row, lo, hi in zip(fold_points.itertuples(), native_lower, native_upper):
                        rows.append(
                            {
                                **common,
                                "method": METHOD_NATIVE_SARIMA,
                                "forecast_date": point_row.forecast_date,
                                "horizon_step": point_row.horizon_step,
                                "actual": point_row.actual,
                                "point_prediction": point_row.prediction,
                                "lower": lo,
                                "upper": hi,
                                "calibration_count": len(calibration),
                                "status": native_status,
                                "failure_reason": native_failure,
                                "eligible_for_selection": native_status == "ok",
                            }
                        )
    result = pd.DataFrame(rows)
    for column in ["forecast_origin", "forecast_date"]:
        result[column] = pd.to_datetime(result[column])
    return result


def summarize_interval_predictions(
    predictions: pd.DataFrame,
    target_df: pd.DataFrame,
    evaluation_stage: str,
) -> pd.DataFrame:
    """Calculate coverage, tail misses, widths, score, and safety diagnostics."""

    rows: List[Dict[str, object]] = []
    group_columns = ["target", "method", "level", "calibration_scope"]
    ok_predictions = predictions.loc[predictions["status"].eq("ok")].copy()
    for keys, group in ok_predictions.groupby(group_columns, sort=True, dropna=False):
        target, method, level, calibration_scope = keys
        group = group.dropna(subset=["actual", "lower", "upper"])
        if group.empty:
            continue
        lower_miss = group["actual"] < group["lower"]
        upper_miss = group["actual"] > group["upper"]
        covered = ~(lower_miss | upper_miss)
        width = group["upper"] - group["lower"]
        scores = interval_score_values(group["actual"], group["lower"], group["upper"], level)
        target_scale = float(target_df[target].mean())
        if target == "g_and_a_beds_available":
            safety_tail_misses = int(lower_miss.sum())
        else:
            safety_tail_misses = int(upper_miss.sum())
        coverage = float(covered.mean())
        coverage_shortfall = max(float(level) - coverage, 0.0)
        safety_miss_rate = safety_tail_misses / len(group)
        normalized_interval_score = float(np.mean(scores) / max(abs(target_scale), 1e-9))
        normalized_width = float(width.mean() / max(abs(target_scale), 1e-9))
        safety_score = (
            1000 * coverage_shortfall
            + 100 * safety_miss_rate
            + normalized_interval_score
            + 0.1 * normalized_width
        )
        rows.append(
            {
                "target": target,
                "method": method,
                "level": level,
                "nominal_coverage": level,
                "calibration_scope": calibration_scope,
                "evaluation_stage": evaluation_stage,
                "evaluation_points": len(group),
                "empirical_coverage": coverage,
                "coverage_deviation": coverage - float(level),
                "coverage_shortfall": coverage_shortfall,
                "lower_tail_misses": int(lower_miss.sum()),
                "upper_tail_misses": int(upper_miss.sum()),
                "demand_underprediction_count": int(upper_miss.sum()) if target in PRESSURE_TARGETS else 0,
                "available_capacity_overprediction_count": int(lower_miss.sum()) if target == "g_and_a_beds_available" else 0,
                "optimization_safety_tail_misses": safety_tail_misses,
                "mean_interval_width": float(width.mean()),
                "median_interval_width": float(width.median()),
                "winkler_interval_score": float(np.mean(scores)),
                "normalized_interval_score": normalized_interval_score,
                "normalized_interval_width": normalized_width,
                "optimization_safety_score": safety_score,
                "mean_calibration_count": group["calibration_count"].mean(),
                "status": "ok",
            }
        )
    return pd.DataFrame(rows)


def select_uncertainty_methods(validation_results: pd.DataFrame) -> pd.DataFrame:
    """Lock one uncertainty method per target using validation results only."""

    candidates = validation_results.loc[
        validation_results["status"].eq("ok")
        & np.isclose(validation_results["level"], 0.95)
        & validation_results["calibration_scope"].eq(
            "pooled_horizon_prior_observable_errors"
        )
    ].copy()
    rows = []
    for target in TARGETS:
        target_candidates = candidates.loc[candidates["target"].eq(target)].copy()
        if target_candidates.empty:
            raise RuntimeError(f"No eligible validation uncertainty method for {target}.")
        target_candidates = target_candidates.sort_values(
            [
                "coverage_shortfall",
                "optimization_safety_tail_misses",
                "normalized_interval_score",
                "normalized_interval_width",
                "method",
            ]
        )
        selected = target_candidates.iloc[0]
        rows.append(
            {
                "target": target,
                "selected_uncertainty_method": selected["method"],
                "selection_level": 0.95,
                "validation_coverage": selected["empirical_coverage"],
                "validation_coverage_shortfall": selected["coverage_shortfall"],
                "validation_safety_tail_misses": selected[
                    "optimization_safety_tail_misses"
                ],
                "validation_mean_width": selected["mean_interval_width"],
                "validation_interval_score": selected["winkler_interval_score"],
                "validation_optimization_safety_score": selected[
                    "optimization_safety_score"
                ],
                "selection_was_validation_only": True,
                "final_test_used_for_selection": False,
                "selection_reason": (
                    "Selected at 95% from development-only nested rolling-origin calibration: "
                    "coverage shortfall first, then the operationally dangerous tail misses, "
                    "then normalized interval score and width."
                ),
            }
        )
    return pd.DataFrame(rows)


def _raw_cqr_for_period(
    target: str,
    target_df: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
    levels: Sequence[float],
    seed: int,
) -> Dict[float, Tuple[np.ndarray, np.ndarray]]:
    """Fit raw lower and upper recursive quantile paths for one forecast period."""

    result = {}
    for level in levels:
        alpha = 1 - level
        lower = recursive_quantile_forecast(
            target, target_df, training_dates, forecast_dates, alpha / 2, seed
        )
        upper = recursive_quantile_forecast(
            target, target_df, training_dates, forecast_dates, 1 - alpha / 2, seed
        )
        result[level] = (np.minimum(lower, upper), np.maximum(lower, upper))
    return result


def build_locked_final_test_intervals(
    target_df: pd.DataFrame,
    development_df: pd.DataFrame,
    final_test_df: pd.DataFrame,
    point_validation: pd.DataFrame,
    raw_cqr_validation: pd.DataFrame,
    final_point_predictions: pd.DataFrame,
    uncertainty_selection: pd.DataFrame,
    config: UncertaintyConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Apply locked interval methods once to the sealed final test period."""

    interval_rows: List[Dict[str, object]] = []
    cqr_test_rows: List[Dict[str, object]] = []
    for target_index, target in enumerate(TARGETS):
        method = uncertainty_selection.loc[
            uncertainty_selection["target"].eq(target), "selected_uncertainty_method"
        ].iloc[0]
        target_points = point_validation.loc[point_validation["target"].eq(target)].copy()
        calibration = target_points.loc[
            target_points["forecast_date"] <= development_df.index.max()
        ].sort_values(["forecast_date", "forecast_origin", "horizon_step"])
        final_points = final_point_predictions.loc[
            final_point_predictions["target"].eq(target)
        ].sort_values("horizon_step")
        points = final_points["prediction"].to_numpy(dtype=float)
        forecast_dates = pd.DatetimeIndex(final_points["forecast_date"])
        cqr_paths = None
        if method == METHOD_CQR:
            cqr_paths = _raw_cqr_for_period(
                target,
                target_df,
                development_df.index,
                forecast_dates,
                config.levels,
                config.random_seed,
            )

        for level_index, level in enumerate(config.levels):
            rng = np.random.default_rng(
                config.random_seed + target_index * 1000 + level_index * 10
            )
            if method == METHOD_CQR:
                cqr_history = raw_cqr_validation.loc[
                    raw_cqr_validation["target"].eq(target)
                    & np.isclose(raw_cqr_validation["level"], level)
                    & raw_cqr_validation["status"].eq("ok")
                    & (raw_cqr_validation["forecast_date"] <= development_df.index.max())
                ]
                raw_lower, raw_upper = cqr_paths[level]
                lower, upper, details = _interval_from_method(
                    method,
                    target,
                    level,
                    points,
                    calibration,
                    rng,
                    config,
                    cqr_raw_lower=raw_lower,
                    cqr_raw_upper=raw_upper,
                    cqr_scores=cqr_history["nonconformity_score"].to_numpy(dtype=float),
                )
                for point_row, raw_lo, raw_hi in zip(final_points.itertuples(), raw_lower, raw_upper):
                    score = max(raw_lo - point_row.actual, point_row.actual - raw_hi, 0.0)
                    cqr_test_rows.append(
                        {
                            "target": target,
                            "level": level,
                            "forecast_origin": development_df.index.max(),
                            "forecast_date": point_row.forecast_date,
                            "horizon_step": point_row.horizon_step,
                            "actual": point_row.actual,
                            "raw_lower": raw_lo,
                            "raw_upper": raw_hi,
                            "nonconformity_score": score,
                        }
                    )
            elif method == METHOD_NATIVE_SARIMA:
                lower, upper = _native_sarima_interval(
                    development_df[target], forecast_dates, level
                )
                lower, upper = enforce_interval_bounds(target, points, lower, upper)
                details = {"calibration_count": len(calibration), "aci_gamma": np.nan, "aci_final_alpha": np.nan}
            else:
                lower, upper, details = _interval_from_method(
                    method,
                    target,
                    level,
                    points,
                    calibration,
                    rng,
                    config,
                )
            for point_row, lo, hi in zip(final_points.itertuples(), lower, upper):
                interval_rows.append(
                    {
                        "target": target,
                        "selected_point_model": point_row.selected_model,
                        "selected_uncertainty_method": method,
                        "level": level,
                        "nominal_coverage": level,
                        "calibration_scope": "pooled_horizon_all_development_errors",
                        "forecast_origin": development_df.index.max(),
                        "forecast_date": point_row.forecast_date,
                        "horizon_step": point_row.horizon_step,
                        "actual": point_row.actual,
                        "point_prediction": point_row.prediction,
                        "lower": lo,
                        "upper": hi,
                        **details,
                        "status": "ok",
                        "uncertainty_selection_locked_before_final_test": True,
                    }
                )
    return pd.DataFrame(interval_rows), pd.DataFrame(cqr_test_rows)


def forecast_locked_models_after_cutoff(
    target_df: pd.DataFrame,
    point_selection: pd.DataFrame,
    config: UncertaintyConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Refit locked point winners through May 2026 and forecast nine future months."""

    evaluation_df = usable_evaluation_frame(target_df)
    training_dates = evaluation_df.index
    cutoff = pd.Timestamp(training_dates.max())
    forecast_dates = pd.date_range(
        cutoff + pd.offsets.MonthBegin(1), periods=config.future_horizon, freq="MS"
    )
    if forecast_dates.min() <= pd.Timestamp("2026-05-01"):
        raise AssertionError("Genuine future forecasts must begin after May 2026.")

    non_deep_selection = point_selection[["target", "selected_model", "feature_mode"]].copy()
    deep_targets = point_selection.loc[
        point_selection["deep_or_non_deep"].eq("deep"), "target"
    ].tolist()
    non_deep_selection.loc[
        non_deep_selection["target"].isin(deep_targets), ["selected_model", "feature_mode"]
    ] = ["seasonal_naive", "univariate"]
    non_deep_forecast = forecast_selected_models(
        target_df,
        training_dates,
        forecast_dates,
        non_deep_selection,
        build_model_registry(EvaluationConfig(generate_plots=False)),
        config.random_seed,
    )

    diagnostic_rows = []
    deep_config = DeepEvaluationConfig(
        deterministic_config=EvaluationConfig(generate_plots=False),
        output_dir=config.deterministic_output_dir,
        max_epochs=30,
    )
    for target in deep_targets:
        selected_model = point_selection.loc[
            point_selection["target"].eq(target), "selected_model"
        ].iloc[0]
        base_model = str(selected_model).removesuffix("_seed_ensemble")
        values, diagnostics = forecast_deep_seed_ensemble(
            base_model,
            target,
            target_df,
            training_dates,
            forecast_dates,
            deep_config,
        )
        non_deep_forecast[target] = values
        diagnostic_rows.extend(diagnostics.to_dict("records"))

    rows = []
    for target in TARGETS:
        selected = point_selection.loc[point_selection["target"].eq(target)].iloc[0]
        for step, forecast_date in enumerate(forecast_dates, start=1):
            rows.append(
                {
                    "forecast_month": forecast_date,
                    "target": target,
                    "selected_point_model": selected["selected_model"],
                    "point_forecast": float(non_deep_forecast.loc[forecast_date, target]),
                    "forecast_horizon_step": step,
                    "data_cutoff_date": cutoff,
                    "is_genuine_future_forecast": True,
                }
            )
    diagnostics = pd.DataFrame(diagnostic_rows)
    return pd.DataFrame(rows), diagnostics


def build_future_intervals(
    target_df: pd.DataFrame,
    point_validation: pd.DataFrame,
    final_point_predictions: pd.DataFrame,
    raw_cqr_validation: pd.DataFrame,
    raw_cqr_test: pd.DataFrame,
    future_points: pd.DataFrame,
    uncertainty_selection: pd.DataFrame,
    config: UncertaintyConfig,
) -> pd.DataFrame:
    """Apply locked interval methods after refitting through the May 2026 cutoff."""

    rows: List[Dict[str, object]] = []
    all_training_dates = usable_evaluation_frame(target_df).index
    cutoff = all_training_dates.max()
    for target_index, target in enumerate(TARGETS):
        method = uncertainty_selection.loc[
            uncertainty_selection["target"].eq(target), "selected_uncertainty_method"
        ].iloc[0]
        validation_calibration = point_validation.loc[point_validation["target"].eq(target)][
            ["actual", "prediction", "forecast_date", "forecast_origin", "horizon_step"]
        ].copy()
        test_calibration = final_point_predictions.loc[
            final_point_predictions["target"].eq(target)
        ][["actual", "prediction", "forecast_date", "forecast_origin", "horizon_step"]].copy()
        calibration = pd.concat([validation_calibration, test_calibration], ignore_index=True)
        calibration = calibration.loc[calibration["forecast_date"] <= cutoff].sort_values(
            ["forecast_date", "forecast_origin", "horizon_step"]
        )
        target_future = future_points.loc[future_points["target"].eq(target)].sort_values(
            "forecast_horizon_step"
        )
        points = target_future["point_forecast"].to_numpy(dtype=float)
        forecast_dates = pd.DatetimeIndex(target_future["forecast_month"])
        cqr_paths = None
        if method == METHOD_CQR:
            cqr_paths = _raw_cqr_for_period(
                target,
                target_df,
                all_training_dates,
                forecast_dates,
                config.levels,
                config.random_seed,
            )

        target_rows: List[Dict[str, object]] = []
        for level_index, level in enumerate(config.levels):
            rng = np.random.default_rng(
                config.random_seed + 50000 + target_index * 1000 + level_index * 10
            )
            if method == METHOD_CQR:
                cqr_scores = pd.concat(
                    [
                        raw_cqr_validation.loc[
                            raw_cqr_validation["target"].eq(target)
                            & np.isclose(raw_cqr_validation["level"], level)
                            & raw_cqr_validation["status"].eq("ok"),
                            ["nonconformity_score"],
                        ],
                        raw_cqr_test.loc[
                            raw_cqr_test["target"].eq(target)
                            & np.isclose(raw_cqr_test["level"], level),
                            ["nonconformity_score"],
                        ],
                    ],
                    ignore_index=True,
                )["nonconformity_score"].dropna()
                raw_lower, raw_upper = cqr_paths[level]
                lower, upper, details = _interval_from_method(
                    method,
                    target,
                    level,
                    points,
                    calibration,
                    rng,
                    config,
                    cqr_raw_lower=raw_lower,
                    cqr_raw_upper=raw_upper,
                    cqr_scores=cqr_scores,
                )
            elif method == METHOD_NATIVE_SARIMA:
                lower, upper = _native_sarima_interval(
                    target_df.loc[all_training_dates, target], forecast_dates, level
                )
                lower, upper = enforce_interval_bounds(target, points, lower, upper)
                details = {"calibration_count": len(calibration), "aci_gamma": np.nan, "aci_final_alpha": np.nan}
            else:
                lower, upper, details = _interval_from_method(
                    method,
                    target,
                    level,
                    points,
                    calibration,
                    rng,
                    config,
                )
            for point_row, lo, hi in zip(target_future.itertuples(), lower, upper):
                target_rows.append(
                    {
                        "forecast_month": point_row.forecast_month,
                        "target": target,
                        "selected_point_model": point_row.selected_point_model,
                        "selected_uncertainty_method": method,
                        "point_forecast": point_row.point_forecast,
                        "level": level,
                        "lower": lo,
                        "upper": hi,
                        "forecast_horizon_step": point_row.forecast_horizon_step,
                        "data_cutoff_date": point_row.data_cutoff_date,
                        **details,
                        "interval_method_locked_before_final_test": True,
                        "interval_warning": (
                            "Exploratory: dependent monthly errors and a very small calibration history, "
                            "especially for 95% intervals."
                        ),
                    }
                )
        rows.extend(target_rows)
    long = pd.DataFrame(rows)
    wide_rows = []
    for (target, forecast_month), group in long.groupby(["target", "forecast_month"], sort=True):
        group = group.set_index("level")
        point = float(group["point_forecast"].iloc[0])
        lower80 = float(group.loc[0.80, "lower"])
        upper80 = float(group.loc[0.80, "upper"])
        lower90 = min(float(group.loc[0.90, "lower"]), lower80)
        upper90 = max(float(group.loc[0.90, "upper"]), upper80)
        lower95 = min(float(group.loc[0.95, "lower"]), lower90)
        upper95 = max(float(group.loc[0.95, "upper"]), upper90)
        first = group.iloc[0]
        wide_rows.append(
            {
                "forecast_month": forecast_month,
                "target": target,
                "selected_point_model": first["selected_point_model"],
                "selected_uncertainty_method": first["selected_uncertainty_method"],
                "point_forecast": point,
                "lower_80": lower80,
                "upper_80": upper80,
                "lower_90": lower90,
                "upper_90": upper90,
                "lower_95": lower95,
                "upper_95": upper95,
                "forecast_horizon_step": int(first["forecast_horizon_step"]),
                "data_cutoff_date": first["data_cutoff_date"],
                "interval_warning": first["interval_warning"],
            }
        )
    return pd.DataFrame(wide_rows)


def build_optimization_inputs(
    future_forecasts: pd.DataFrame,
    config: UncertaintyConfig,
) -> pd.DataFrame:
    """Reshape genuine forecasts into month-matched conservative planning inputs."""

    rows: List[Dict[str, object]] = []
    expected_months = sorted(pd.to_datetime(future_forecasts["forecast_month"].unique()))
    for forecast_month in expected_months:
        month = future_forecasts.loc[
            pd.to_datetime(future_forecasts["forecast_month"]).eq(forecast_month)
        ].set_index("target")
        if set(month.index) != set(TARGETS):
            raise AssertionError(f"Optimization month {forecast_month} does not contain all targets.")
        for level in config.levels:
            suffix = int(level * 100)
            available = month.loc["g_and_a_beds_available"]
            occupied = month.loc["g_and_a_beds_occupied"]
            expected_headroom = float(available["point_forecast"] - occupied["point_forecast"])
            conservative_headroom = float(
                available[f"lower_{suffix}"] - occupied[f"upper_{suffix}"]
            )
            required_capacity = float(occupied[f"upper_{suffix}"] / config.rho_max)
            additional_requirement = max(
                required_capacity - float(available[f"lower_{suffix}"]), 0.0
            )
            row: Dict[str, object] = {
                "forecast_month": forecast_month,
                "confidence_level": level,
                "rho_max": config.rho_max,
                "rho_max_note": config.rho_max_note,
                "available_beds_point": available["point_forecast"],
                "available_beds_lower": available[f"lower_{suffix}"],
                "available_beds_upper": available[f"upper_{suffix}"],
                "occupied_beds_point": occupied["point_forecast"],
                "occupied_beds_lower": occupied[f"lower_{suffix}"],
                "occupied_beds_upper": occupied[f"upper_{suffix}"],
                "occupancy_rate_point": month.loc["g_and_a_occupancy_rate", "point_forecast"],
                "occupancy_rate_lower": month.loc[
                    "g_and_a_occupancy_rate", f"lower_{suffix}"
                ],
                "occupancy_rate_upper": month.loc[
                    "g_and_a_occupancy_rate", f"upper_{suffix}"
                ],
                "expected_headroom": expected_headroom,
                "conservative_headroom": conservative_headroom,
                "direct_shortage_indicator": bool(conservative_headroom < 0),
                "required_capacity_at_rho_max": required_capacity,
                "required_additional_capacity": additional_requirement,
                "occupancy_adjusted_shortage_indicator": bool(additional_requirement > 0),
                "shortage_indicator": bool(additional_requirement > 0),
                "available_uncertainty_method": available[
                    "selected_uncertainty_method"
                ],
                "occupied_uncertainty_method": occupied[
                    "selected_uncertainty_method"
                ],
                "forecast_cutoff_date": available["data_cutoff_date"],
                "bed_count_unit": "national monthly average G&A bed-equivalent count",
                "rate_unit": "proportion",
            }
            for target in [
                "los_count_7_or_more_days",
                "los_count_14_or_more_days",
                "los_count_21_or_more_days",
                "los_pct_7_or_more_days",
                "los_pct_14_or_more_days",
                "los_pct_21_or_more_days",
            ]:
                short = target.replace("_or_more_days", "_plus")
                row[f"{short}_point"] = month.loc[target, "point_forecast"]
                row[f"{short}_lower"] = month.loc[target, f"lower_{suffix}"]
                row[f"{short}_upper"] = month.loc[target, f"upper_{suffix}"]
                row[f"{short}_uncertainty_method"] = month.loc[
                    target, "selected_uncertainty_method"
                ]
            rows.append(row)
    result = pd.DataFrame(rows).sort_values(["forecast_month", "confidence_level"])
    if len(result) != config.future_horizon * len(config.levels):
        raise AssertionError("Optimization input table has an incorrect month-level shape.")
    return result.reset_index(drop=True)


def uncertainty_method_explanations() -> pd.DataFrame:
    """Document implementation status, formula, and dependent-data limitations."""

    return pd.DataFrame(
        [
            {
                "method": METHOD_RESIDUAL_BOOTSTRAP,
                "implementation_status": "implemented",
                "plain_english_explanation": (
                    "Resample signed historical forecast errors and add their lower and upper quantiles "
                    "to the locked point forecast."
                ),
                "formula": "[point + Q_(alpha/2)(error), point + Q_(1-alpha/2)(error)]",
                "settings": "2000 residual draws; development errors observable by each origin",
                "limitations": "Residual stationarity is doubtful and overlapping fold errors are dependent.",
            },
            {
                "method": METHOD_SPLIT_CONFORMAL,
                "implementation_status": "implemented",
                "plain_english_explanation": (
                    "Use a conservative finite-sample quantile of absolute calibration residuals as a "
                    "symmetric radius around the point forecast."
                ),
                "formula": "[point - Q_(1-alpha)(|error|), point + Q_(1-alpha)(|error|)]",
                "settings": "pooled horizons; finite-n higher quantile",
                "limitations": "Classical exchangeability is not credible for dependent monthly errors.",
            },
            {
                "method": METHOD_ROLLING_CONFORMAL,
                "implementation_status": "implemented",
                "plain_english_explanation": (
                    "Move a chronological calibration window and use only the most recent observable "
                    "absolute forecast errors."
                ),
                "formula": "point +/- Q_(1-alpha)(recent |errors|)",
                "settings": "last 18 origin-observable errors",
                "limitations": "Window choice is fixed and the sample remains very small.",
            },
            {
                "method": METHOD_CQR,
                "implementation_status": "implemented",
                "plain_english_explanation": (
                    "Fit recursive lower and upper quantile-boosting paths, then widen them by a prior "
                    "nonconformity-score quantile."
                ),
                "formula": "[q_low(x)-Q(score), q_high(x)+Q(score)]",
                "settings": "fold-local GradientBoosting quantiles; recursive target-only lags",
                "limitations": "Quantile models are data-hungry; formal CQR coverage assumes exchangeability.",
            },
            {
                "method": METHOD_ENBPI_STYLE,
                "implementation_status": "implemented_as_style_benchmark_not_full_EnbPI",
                "plain_english_explanation": (
                    "Use block-bootstrap resampling of prior absolute forecast errors to estimate a "
                    "sequential interval radius around the locked point model."
                ),
                "formula": "point +/- median_b Q_(1-alpha)(block-bootstrap errors_b)",
                "settings": "block length 3; 500 bootstrap repetitions",
                "limitations": (
                    "This is EnbPI_style, not Xu and Xie's full bootstrap-estimator/OOB algorithm; "
                    "it must never be labelled full EnbPI."
                ),
            },
            {
                "method": METHOD_ACI,
                "implementation_status": "implemented_as_batch_approximation_not_full_online_ACI",
                "plain_english_explanation": (
                    "Apply the ACI alpha recursion to chronologically available residuals, then freeze "
                    "that alpha while issuing the complete fixed-origin forecast path."
                ),
                "formula": "alpha_(t+1)=clip(alpha_t+gamma*(alpha-miss_t))",
                "settings": "gamma=0.05; alpha clipped to [0.001, 0.50]",
                "limitations": (
                    "This is not full online ACI: the nine future outcomes are unavailable when the path "
                    "is issued, so no within-path feedback update is made."
                ),
            },
            {
                "method": METHOD_NATIVE_SARIMA,
                "implementation_status": "implemented_when_locked_point_model_is_SARIMA",
                "plain_english_explanation": "Use statsmodels state-space forecast confidence intervals.",
                "formula": "model-based state-space forecast interval",
                "settings": "same fixed SARIMA order as deterministic evaluation",
                "limitations": "Applicable only when SARIMA is the locked point model.",
            },
            {
                "method": METHOD_NATIVE_ETS,
                "implementation_status": "not_used",
                "plain_english_explanation": "The current statsmodels ETS wrapper does not expose a verified native interval path.",
                "formula": "not applicable",
                "settings": "residual/conformal alternatives used",
                "limitations": "Do not describe an approximate residual interval as native ETS.",
            },
            {
                "method": METHOD_NATIVE_DEEP,
                "implementation_status": "not_applicable_unless_stable_TiDE_or_TFT_wins",
                "plain_english_explanation": (
                    "Native quantile deep forecasts are only eligible when a stable TiDE or TFT point "
                    "model wins validation."
                ),
                "formula": "model quantiles, optionally conformalized",
                "settings": "not trained when no deep model is selected",
                "limitations": "Raw neural quantiles should not be assumed calibrated.",
            },
        ]
    )


def plot_locked_test_intervals(
    final_intervals: pd.DataFrame,
    output_dir: Path,
) -> List[Path]:
    """Plot nested locked-test intervals for the six operational level targets."""

    targets = [
        "g_and_a_beds_available",
        "g_and_a_beds_occupied",
        "g_and_a_occupancy_rate",
        "los_count_7_or_more_days",
        "los_count_14_or_more_days",
        "los_count_21_or_more_days",
    ]
    plot_dir = output_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for target in targets:
        target_frame = final_intervals.loc[final_intervals["target"].eq(target)].copy()
        wide = target_frame.pivot_table(
            index=["forecast_date", "actual", "point_prediction"],
            columns="level",
            values=["lower", "upper"],
        ).reset_index()
        dates = pd.to_datetime(wide["forecast_date"])
        fig, ax = plt.subplots(figsize=(11, 5.5))
        ax.fill_between(
            dates,
            wide[("lower", 0.95)],
            wide[("upper", 0.95)],
            color="#d7e8f5",
            label="95% interval",
        )
        ax.fill_between(
            dates,
            wide[("lower", 0.90)],
            wide[("upper", 0.90)],
            color="#9fc8e3",
            label="90% interval",
        )
        ax.fill_between(
            dates,
            wide[("lower", 0.80)],
            wide[("upper", 0.80)],
            color="#5fa3cf",
            alpha=0.75,
            label="80% interval",
        )
        ax.plot(dates, wide[("point_prediction", "")], color="#d95f02", marker="o", label="Point forecast")
        ax.plot(dates, wide[("actual", "")], color="#1b1b1b", marker="o", label="Actual")
        ax.set_title(f"{target}: locked point and uncertainty test")
        ax.set_xlabel("Month")
        ax.set_ylabel("Rate" if target in RATE_TARGETS else "Published monthly national count")
        ax.grid(alpha=0.25)
        ax.legend(ncol=3)
        fig.autofmt_xdate()
        fig.tight_layout()
        path = plot_dir / f"{target}_locked_test_intervals.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(path)
    return paths


def run_uncertainty_evaluation(
    config: Optional[UncertaintyConfig] = None,
) -> Dict[str, pd.DataFrame]:
    """Run validation selection, locked test intervals, future forecasts, and inputs."""

    config = config or UncertaintyConfig()
    validate_uncertainty_config(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    target_df = load_nhs_targets(EvaluationConfig(generate_plots=False))
    development_df, final_test_df = split_development_and_final_test(
        target_df, EvaluationConfig(generate_plots=False)
    )
    selection, point_validation, final_point_predictions = load_locked_point_artifacts(config)

    raw_cqr_validation = build_raw_cqr_validation_predictions(
        target_df, point_validation, config
    )
    validation_predictions = build_validation_intervals(
        target_df,
        selection,
        point_validation,
        raw_cqr_validation,
        config,
    )
    validation_results = summarize_interval_predictions(
        validation_predictions,
        target_df,
        "development_nested_calibration",
    )
    uncertainty_selection = select_uncertainty_methods(validation_results)

    final_test_intervals, raw_cqr_test = build_locked_final_test_intervals(
        target_df,
        development_df,
        final_test_df,
        point_validation,
        raw_cqr_validation,
        final_point_predictions,
        uncertainty_selection,
        config,
    )
    final_test_metrics = summarize_interval_predictions(
        final_test_intervals.rename(columns={"selected_uncertainty_method": "method"}),
        target_df,
        "locked_final_test_once",
    )
    final_test_metrics["uncertainty_selection_locked_before_final_test"] = True

    future_points, future_deep_diagnostics = forecast_locked_models_after_cutoff(
        target_df,
        selection,
        config,
    )
    future_forecasts = build_future_intervals(
        target_df,
        point_validation,
        final_point_predictions,
        raw_cqr_validation,
        raw_cqr_test,
        future_points,
        uncertainty_selection,
        config,
    )
    optimization_inputs = build_optimization_inputs(future_forecasts, config)
    explanations = uncertainty_method_explanations()

    frames = {
        "validation_results": validation_results,
        "method_selection": uncertainty_selection,
        "final_test_intervals": final_test_intervals,
        "final_test_metrics": final_test_metrics,
        "method_explanations": explanations,
        "validation_predictions": validation_predictions,
        "raw_cqr_validation": raw_cqr_validation,
        "raw_cqr_test": raw_cqr_test,
        "future_points": future_points,
        "future_forecasts": future_forecasts,
        "optimization_inputs": optimization_inputs,
        "future_deep_diagnostics": future_deep_diagnostics,
    }
    paths = {
        "validation_results": config.output_dir / "uncertainty_validation_results.csv",
        "method_selection": config.output_dir / "uncertainty_method_selection.csv",
        "final_test_intervals": config.output_dir / "final_test_intervals.csv",
        "final_test_metrics": config.output_dir / "final_test_interval_metrics.csv",
        "method_explanations": config.output_dir / "uncertainty_method_explanations.csv",
        "validation_predictions": config.output_dir / "uncertainty_validation_predictions.csv",
        "raw_cqr_validation": config.output_dir / "cqr_raw_validation_predictions.csv",
        "raw_cqr_test": config.output_dir / "cqr_raw_final_test_predictions.csv",
        "future_points": config.output_dir / "future_point_forecasts.csv",
        "future_forecasts": Path("outputs/final_future_forecasts.csv"),
        "optimization_inputs": Path("outputs/final_optimization_inputs.csv"),
        "future_deep_diagnostics": config.output_dir / "future_deep_diagnostics.csv",
    }
    for key, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = frames[key]
        if frame.empty and len(frame.columns) == 0:
            frame = pd.DataFrame(columns=["status", "note"])
            frames[key] = frame
        frame.to_csv(path, index=False)
    if config.generate_plots:
        plot_locked_test_intervals(final_test_intervals, config.output_dir)
    return frames


def load_uncertainty_outputs(
    output_dir: Path = Path("outputs/final_uncertainty"),
) -> Dict[str, pd.DataFrame]:
    """Load cached final uncertainty artifacts for fast notebook presentation."""

    files = {
        "validation_results": output_dir / "uncertainty_validation_results.csv",
        "method_selection": output_dir / "uncertainty_method_selection.csv",
        "final_test_intervals": output_dir / "final_test_intervals.csv",
        "final_test_metrics": output_dir / "final_test_interval_metrics.csv",
        "method_explanations": output_dir / "uncertainty_method_explanations.csv",
        "future_forecasts": Path("outputs/final_future_forecasts.csv"),
        "optimization_inputs": Path("outputs/final_optimization_inputs.csv"),
    }
    missing = [str(path) for path in files.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing cached uncertainty outputs: {missing}")
    return {key: pd.read_csv(path) for key, path in files.items()}


def main() -> None:
    """Run the complete validation-selected uncertainty workflow."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rho-max",
        type=float,
        default=0.90,
        help="Illustrative configurable planning occupancy ceiling; requires stakeholder validation.",
    )
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    config = UncertaintyConfig(rho_max=args.rho_max, generate_plots=not args.no_plots)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        outputs = run_uncertainty_evaluation(config)
    print(outputs["method_selection"].to_string(index=False))
    print("Future period:", outputs["future_forecasts"]["forecast_month"].min(), "to", outputs["future_forecasts"]["forecast_month"].max())


if __name__ == "__main__":
    main()
