"""Leakage-safe deterministic forecasting evaluation for the NHS capacity project.

The module keeps model selection entirely inside the pre-September-2025
development period.  It supports two deliberately separate protocols:

* ``fixed_origin_9_month`` recursively forecasts a complete planning horizon.
* ``rolling_one_step`` reveals one observation at a time and refits each month.

The notebook imports this module so that the evaluation logic can also be tested
without executing every notebook cell.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.linear_model import ElasticNet, Lasso, Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR


TARGETS = [
    "g_and_a_beds_available",
    "g_and_a_beds_occupied",
    "g_and_a_occupancy_rate",
    "los_count_7_or_more_days",
    "los_count_14_or_more_days",
    "los_count_21_or_more_days",
    "los_pct_7_or_more_days",
    "los_pct_14_or_more_days",
    "los_pct_21_or_more_days",
]

RATE_TARGETS = {
    "g_and_a_occupancy_rate",
    "los_pct_7_or_more_days",
    "los_pct_14_or_more_days",
    "los_pct_21_or_more_days",
}

RELATED_LAG_INPUT_COLUMNS = [
    "g_and_a_beds_available",
    "g_and_a_beds_occupied",
    "los_count_7_or_more_days",
    "los_count_14_or_more_days",
    "los_count_21_or_more_days",
    "los_pct_7_or_more_days",
    "los_pct_14_or_more_days",
    "los_pct_21_or_more_days",
]

VAR_COLUMNS = [
    "g_and_a_beds_available",
    "g_and_a_beds_occupied",
    "los_count_7_or_more_days",
    "los_count_14_or_more_days",
    "los_count_21_or_more_days",
]

LAGS = (1, 2, 3, 6, 12)
ROLLING_WINDOWS = (3, 6, 12)
FINAL_TEST_START = pd.Timestamp("2025-09-01")
FINAL_TEST_END = pd.Timestamp("2026-05-01")
SEASONALITY = 12


@dataclass(frozen=True)
class EvaluationConfig:
    """Store reproducible paths, fold settings, and execution options."""

    workbook_path: Path = Path("Beds-publication-Timeseries-March-2020-May-2026.xlsx")
    sheet_name: str = "Timeseries all acute trusts"
    output_dir: Path = Path("outputs/final_evaluation")
    final_test_start: pd.Timestamp = FINAL_TEST_START
    final_test_end: pd.Timestamp = FINAL_TEST_END
    minimum_training_observations: int = 30
    fixed_horizon: int = 9
    fold_step: int = 1
    random_seed: int = 42
    practical_tie_fraction: float = 0.01
    minimum_complex_model_skill: float = 0.01
    bootstrap_repetitions: int = 2000
    generate_plots: bool = True
    mark_uncertainty_stale: bool = True
    model_allowlist: Optional[Tuple[str, ...]] = field(default=None)


@dataclass(frozen=True)
class RollingOriginFold:
    """Describe one expanding-window training and validation split."""

    protocol: str
    fold_id: str
    training_start: pd.Timestamp
    training_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp
    training_observations: int
    validation_observations: int


@dataclass(frozen=True)
class ModelSpec:
    """Describe a model, feature mode, family, and fixed hyperparameters."""

    model: str
    base_model: str
    feature_mode: str
    family: str
    hyperparameters: Mapping[str, object]
    enabled: bool = True
    exclusion_reason: str = ""


class DataFrameMedianImputer(BaseEstimator, TransformerMixin):
    """Median-impute while preserving dates for fit-range auditing."""

    def fit(self, X: pd.DataFrame, y=None):
        frame = _ensure_frame(X)
        self.medians_ = frame.median(numeric_only=True)
        self.fit_index_start_ = frame.index.min()
        self.fit_index_end_ = frame.index.max()
        self.n_samples_seen_ = len(frame)
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        frame = _ensure_frame(X)
        return frame.fillna(self.medians_)


class AuditedStandardScaler(StandardScaler):
    """StandardScaler that records the dates used by ``fit``."""

    def fit(self, X, y=None, sample_weight=None):
        frame = _ensure_frame(X)
        self.fit_index_start_ = frame.index.min()
        self.fit_index_end_ = frame.index.max()
        return super().fit(frame, y=y, sample_weight=sample_weight)


def _ensure_frame(X) -> pd.DataFrame:
    """Return a DataFrame without discarding an existing DatetimeIndex."""

    if isinstance(X, pd.DataFrame):
        return X.copy()
    return pd.DataFrame(X)


def combine_multirow_headers(
    raw_sheet: pd.DataFrame,
    group_row: int,
    header_row: int,
) -> List[str]:
    """Combine the NHS group row and main header row into unique names."""

    group_labels = raw_sheet.iloc[group_row].ffill()
    main_labels = raw_sheet.iloc[header_row]
    combined: List[str] = []
    for position, (group, main) in enumerate(zip(group_labels, main_labels)):
        if pd.isna(main):
            combined.append(f"unnamed_{position}")
            continue
        main_text = str(main).strip()
        group_text = "" if pd.isna(group) else str(group).strip()
        if main_text in {"7 or more days", "14 or more days", "21 or more days"}:
            if group_text.lower().startswith("number of"):
                combined.append(f"los_count {main_text}")
            elif group_text.startswith("%"):
                combined.append(f"los_pct {main_text}")
            else:
                combined.append(f"{group_text} {main_text}".strip())
        else:
            combined.append(main_text)
    return combined


def normalize_column_name(name: str) -> str:
    """Convert a workbook heading to a stable snake-case name."""

    name = name.lower().strip()
    name = name.replace("g&a", "g_and_a")
    name = name.replace("&", "and").replace("%", "pct").replace("+", "plus")
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_")


def clean_numeric_value(value):
    """Coerce workbook numbers, comma strings, percentages, and missing markers."""

    if pd.isna(value):
        return np.nan
    if isinstance(value, str):
        text = value.strip()
        if text in {"", "-", "--", "\u2013", "\u2014", "..", "..."}:
            return np.nan
        text = text.replace(",", "")
        if text.endswith("%"):
            return pd.to_numeric(text[:-1], errors="coerce") / 100
        return pd.to_numeric(text, errors="coerce")
    return pd.to_numeric(value, errors="coerce")


def load_nhs_targets(config: EvaluationConfig) -> pd.DataFrame:
    """Load, clean, and return the nine monthly all-acute-trust targets."""

    raw = pd.read_excel(config.workbook_path, sheet_name=config.sheet_name, header=None)
    header_rows = raw.index[
        raw.apply(lambda row: row.astype(str).str.strip().eq("Month").any(), axis=1)
    ].tolist()
    if len(header_rows) != 1:
        raise ValueError(f"Expected one Month header row, found {header_rows}")
    header_row = header_rows[0]
    columns = combine_multirow_headers(raw, header_row - 1, header_row)
    clean = raw.iloc[header_row + 1 :].copy()
    clean.columns = columns
    clean = clean.drop(columns=[c for c in clean.columns if c.startswith("unnamed_")])
    clean["Month"] = pd.to_datetime(clean["Month"], format="%B %Y", errors="coerce")
    clean = clean.loc[clean["Month"].notna()].copy()
    clean = clean.rename(columns={c: normalize_column_name(c) for c in clean.columns})
    for column in [c for c in clean.columns if c != "month"]:
        clean[column] = clean[column].map(clean_numeric_value)
    for column in [c for c in clean.columns if "rate" in c or c.startswith("los_pct")]:
        if clean[column].dropna().gt(1.5).any():
            clean[column] = clean[column] / 100
    monthly = clean.sort_values("month").set_index("month").asfreq("MS")
    missing_targets = [target for target in TARGETS if target not in monthly.columns]
    if missing_targets:
        raise KeyError(f"Missing targets: {missing_targets}")
    return monthly[TARGETS].copy()


def validate_target_data(target_df: pd.DataFrame) -> None:
    """Fail early when dates, frequency, targets, or values are unsuitable."""

    if not target_df.index.is_monotonic_increasing:
        raise AssertionError("Monthly dates must be strictly increasing.")
    if target_df.index.has_duplicates:
        raise AssertionError("Monthly dates must not contain duplicates.")
    if list(target_df.columns) != TARGETS:
        raise AssertionError("The target table must contain the nine required targets in order.")
    expected = pd.date_range(target_df.index.min(), target_df.index.max(), freq="MS")
    if not target_df.index.equals(expected):
        raise AssertionError("The target index must be a complete monthly-start sequence.")
    if target_df[TARGETS].isna().any().any():
        raise AssertionError("The final target table contains missing values.")


def add_calendar_features(features: pd.DataFrame) -> pd.DataFrame:
    """Add only calendar values genuinely known before forecast time."""

    result = features.copy()
    result["month_number"] = result.index.month
    result["quarter"] = result.index.quarter
    result["year"] = result.index.year
    result["month_sin"] = np.sin(2 * np.pi * result.index.month / 12)
    result["month_cos"] = np.cos(2 * np.pi * result.index.month / 12)
    return result


def build_target_only_features(series: pd.Series) -> pd.DataFrame:
    """Build target lags and shifted rolling statistics with no current value."""

    features = pd.DataFrame(index=series.index)
    shifted = series.shift(1)
    for lag in LAGS:
        features[f"target_lag_{lag}"] = series.shift(lag)
    for window in ROLLING_WINDOWS:
        features[f"target_roll_mean_{window}"] = shifted.rolling(window).mean()
        features[f"target_roll_std_{window}"] = shifted.rolling(window).std()
    return add_calendar_features(features)


def build_multivariate_lag_features(
    target_name: str,
    target_df: pd.DataFrame,
) -> pd.DataFrame:
    """Add lagged related outcomes while excluding all same-month outcomes."""

    features = build_target_only_features(target_df[target_name])
    for column in RELATED_LAG_INPUT_COLUMNS:
        for lag in LAGS:
            features[f"{column}_lag_{lag}"] = target_df[column].shift(lag)
    return features


def build_feature_frame(
    target_name: str,
    target_df: pd.DataFrame,
    feature_mode: str,
) -> pd.DataFrame:
    """Dispatch to the requested leakage-safe historical feature builder."""

    if feature_mode == "target_only_features":
        return build_target_only_features(target_df[target_name])
    if feature_mode == "multivariate_lag_features":
        return build_multivariate_lag_features(target_name, target_df)
    raise ValueError(f"Unknown feature mode: {feature_mode}")


def build_recursive_feature_row(
    target_name: str,
    simulated_history: pd.DataFrame,
    forecast_date: pd.Timestamp,
    feature_mode: str,
) -> pd.DataFrame:
    """Build one future row using observed history and prior predictions only."""

    if forecast_date <= simulated_history.index.max():
        raise AssertionError("Recursive feature date must be after the simulated history.")
    expected_date = simulated_history.index.max() + pd.offsets.MonthBegin(1)
    if forecast_date != expected_date:
        raise AssertionError("Recursive forecasts must advance exactly one month at a time.")

    values: Dict[str, float] = {}
    target_series = simulated_history[target_name]
    for lag in LAGS:
        source_date = forecast_date - pd.DateOffset(months=lag)
        values[f"target_lag_{lag}"] = float(target_series.loc[source_date])
    for window in ROLLING_WINDOWS:
        prior = target_series.iloc[-window:]
        values[f"target_roll_mean_{window}"] = float(prior.mean())
        values[f"target_roll_std_{window}"] = float(prior.std())

    values["month_number"] = forecast_date.month
    values["quarter"] = forecast_date.quarter
    values["year"] = forecast_date.year
    values["month_sin"] = math.sin(2 * math.pi * forecast_date.month / 12)
    values["month_cos"] = math.cos(2 * math.pi * forecast_date.month / 12)

    # Historical multivariate frames append related-variable lags after the
    # complete target-only frame, including calendar columns. Match that order.
    if feature_mode == "multivariate_lag_features":
        for column in RELATED_LAG_INPUT_COLUMNS:
            for lag in LAGS:
                source_date = forecast_date - pd.DateOffset(months=lag)
                values[f"{column}_lag_{lag}"] = float(simulated_history.loc[source_date, column])
    elif feature_mode != "target_only_features":
        raise ValueError(f"Unknown feature mode: {feature_mode}")

    return pd.DataFrame([values], index=pd.DatetimeIndex([forecast_date], name="month"))


def usable_evaluation_frame(target_df: pd.DataFrame) -> pd.DataFrame:
    """Return rows where the longest target-only lag/rolling features exist."""

    feature_index = build_target_only_features(target_df[TARGETS[0]]).dropna().index
    return target_df.loc[feature_index, TARGETS].copy()


def split_development_and_final_test(
    target_df: pd.DataFrame,
    config: EvaluationConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Reserve September 2025-May 2026 before any model comparison."""

    evaluation_df = usable_evaluation_frame(target_df)
    development = evaluation_df.loc[evaluation_df.index < config.final_test_start].copy()
    final_test = evaluation_df.loc[
        (evaluation_df.index >= config.final_test_start)
        & (evaluation_df.index <= config.final_test_end)
    ].copy()
    if len(final_test) != config.fixed_horizon:
        raise AssertionError(
            f"Expected {config.fixed_horizon} final-test months, found {len(final_test)}."
        )
    if development.index.max() >= final_test.index.min():
        raise AssertionError("Development and final test periods overlap.")
    return development, final_test


def generate_rolling_origin_folds(
    development_index: pd.DatetimeIndex,
    protocol: str,
    minimum_training_observations: int = 30,
    validation_horizon: int = 9,
    step: int = 1,
) -> List[RollingOriginFold]:
    """Generate common expanding-window folds for every target and model."""

    if protocol not in {"fixed_origin_9_month", "rolling_one_step"}:
        raise ValueError(f"Unknown protocol: {protocol}")
    folds: List[RollingOriginFold] = []
    train_size = minimum_training_observations
    fold_number = 1
    prefix = "F" if protocol == "fixed_origin_9_month" else "R"
    while train_size + validation_horizon <= len(development_index):
        train_dates = development_index[:train_size]
        validation_dates = development_index[train_size : train_size + validation_horizon]
        folds.append(
            RollingOriginFold(
                protocol=protocol,
                fold_id=f"{prefix}{fold_number:02d}",
                training_start=train_dates.min(),
                training_end=train_dates.max(),
                validation_start=validation_dates.min(),
                validation_end=validation_dates.max(),
                training_observations=len(train_dates),
                validation_observations=len(validation_dates),
            )
        )
        train_size += step
        fold_number += 1
    return folds


def folds_to_frame(folds: Sequence[RollingOriginFold]) -> pd.DataFrame:
    """Convert fold objects to the documented CSV schema."""

    return pd.DataFrame(
        [
            {
                "protocol": fold.protocol,
                "fold_id": fold.fold_id,
                "training_start": fold.training_start,
                "training_end": fold.training_end,
                "validation_start": fold.validation_start,
                "validation_end": fold.validation_end,
                "training_observations": fold.training_observations,
                "validation_observations": fold.validation_observations,
            }
            for fold in folds
        ]
    )


def assert_fold_integrity(
    folds: Sequence[RollingOriginFold],
    development_index: pd.DatetimeIndex,
    final_test_index: pd.DatetimeIndex,
) -> None:
    """Prove folds are chronological and never touch the final test."""

    development_dates = set(development_index)
    test_dates = set(final_test_index)
    for fold in folds:
        training_dates = set(
            development_index[
                (development_index >= fold.training_start)
                & (development_index <= fold.training_end)
            ]
        )
        validation_dates = set(
            development_index[
                (development_index >= fold.validation_start)
                & (development_index <= fold.validation_end)
            ]
        )
        if training_dates & validation_dates:
            raise AssertionError(f"Training/validation overlap in {fold.fold_id}")
        if (training_dates | validation_dates) & test_dates:
            raise AssertionError(f"Final-test date entered {fold.fold_id}")
        if not training_dates | validation_dates <= development_dates:
            raise AssertionError(f"Non-development date entered {fold.fold_id}")
        if max(training_dates) >= min(validation_dates):
            raise AssertionError(f"Validation is not after training in {fold.fold_id}")
        if len(training_dates) != fold.training_observations:
            raise AssertionError(f"Training count mismatch in {fold.fold_id}")
        if len(validation_dates) != fold.validation_observations:
            raise AssertionError(f"Validation count mismatch in {fold.fold_id}")


def rmse(y_true, y_pred) -> float:
    """Calculate root mean squared error."""

    error = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    return float(np.sqrt(np.mean(np.square(error))))


def mape(y_true, y_pred) -> float:
    """Calculate MAPE while ignoring zero actual values."""

    actual = np.asarray(y_true, dtype=float)
    prediction = np.asarray(y_pred, dtype=float)
    valid = actual != 0
    if not valid.any():
        return np.nan
    return float(np.mean(np.abs((actual[valid] - prediction[valid]) / actual[valid])))


def smape(y_true, y_pred) -> float:
    """Calculate symmetric mean absolute percentage error."""

    actual = np.asarray(y_true, dtype=float)
    prediction = np.asarray(y_pred, dtype=float)
    denominator = np.abs(actual) + np.abs(prediction)
    values = np.where(denominator == 0, 0.0, 2 * np.abs(prediction - actual) / denominator)
    return float(np.mean(values))


def mase(y_true, y_pred, training_values, seasonality: int = SEASONALITY) -> float:
    """Scale MAE by the in-fold seasonal-naive training error."""

    training = np.asarray(training_values, dtype=float)
    if len(training) <= seasonality:
        scale = np.mean(np.abs(np.diff(training)))
    else:
        scale = np.mean(np.abs(training[seasonality:] - training[:-seasonality]))
    if not np.isfinite(scale) or scale == 0:
        return np.nan
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))) / scale)


def calculate_metrics(y_true, y_pred, training_values) -> Dict[str, float]:
    """Calculate all point-forecast metrics from one fold or test forecast."""

    actual = np.asarray(y_true, dtype=float)
    prediction = np.asarray(y_pred, dtype=float)
    error = actual - prediction
    return {
        "MAE": float(np.mean(np.abs(error))),
        "RMSE": rmse(actual, prediction),
        "MAPE": mape(actual, prediction),
        "sMAPE": smape(actual, prediction),
        "MASE": mase(actual, prediction, training_values),
        "bias": float(np.mean(error)),
    }


def apply_domain_bounds(target_name: str, values) -> np.ndarray:
    """Apply fixed physical bounds without consulting validation or test errors."""

    prediction = np.asarray(values, dtype=float)
    if target_name in RATE_TARGETS:
        return np.clip(prediction, 0.0, 1.0)
    return np.clip(prediction, 0.0, None)


def naive_forecast(history: pd.Series, horizon: int) -> np.ndarray:
    """Repeat the latest observed value for every future month."""

    return np.repeat(float(history.iloc[-1]), horizon)


def seasonal_naive_forecast(history: pd.Series, horizon: int) -> np.ndarray:
    """Repeat the last observed 12-month seasonal pattern."""

    if len(history) < SEASONALITY:
        return naive_forecast(history, horizon)
    return np.resize(history.iloc[-SEASONALITY:].to_numpy(dtype=float), horizon)


def ets_forecast(history: pd.Series, horizon: int) -> np.ndarray:
    """Fit additive ETS on the fold training history and forecast directly."""

    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    seasonal = "add" if len(history) >= 24 else None
    model = ExponentialSmoothing(
        history,
        trend="add",
        seasonal=seasonal,
        seasonal_periods=SEASONALITY if seasonal else None,
        initialization_method="estimated",
    )
    return model.fit(optimized=True).forecast(horizon).to_numpy(dtype=float)


def sarima_forecast(history: pd.Series, horizon: int) -> np.ndarray:
    """Fit the fixed seasonal SARIMA specification inside the current fold."""

    from statsmodels.tsa.statespace.sarimax import SARIMAX

    model = SARIMAX(
        history,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, SEASONALITY),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fit = model.fit(disp=False, maxiter=200, warn_convergence=False)
    return fit.forecast(horizon).to_numpy(dtype=float)


def build_model_registry(config: EvaluationConfig) -> List[ModelSpec]:
    """Create the fixed model registry without looking at validation or test scores."""

    specs = [
        ModelSpec("naive", "naive", "univariate", "statistical", {}),
        ModelSpec("seasonal_naive", "seasonal_naive", "univariate", "statistical", {"seasonality": 12}),
        ModelSpec(
            "ETS",
            "ETS",
            "univariate",
            "statistical",
            {"trend": "add", "seasonal": "add_if_24_rows", "seasonal_periods": 12},
        ),
        ModelSpec(
            "SARIMA",
            "SARIMA",
            "univariate",
            "statistical",
            {"order": [1, 1, 1], "seasonal_order": [1, 1, 1, 12]},
        ),
        ModelSpec(
            "SARIMAX_multivariate_lag_features",
            "SARIMAX",
            "multivariate_lag_features",
            "statistical",
            {"order": [1, 1, 1], "seasonal_order": [1, 1, 1, 12], "max_exog": 8},
        ),
    ]

    ml_parameters: Dict[str, Dict[str, object]] = {
        "Ridge": {"alpha": 1.0},
        "Lasso": {"alpha": 0.001, "max_iter": 10000, "random_state": config.random_seed},
        "ElasticNet": {
            "alpha": 0.001,
            "l1_ratio": 0.5,
            "max_iter": 10000,
            "random_state": config.random_seed,
        },
        "RandomForestRegressor": {
            "n_estimators": 300,
            "min_samples_leaf": 2,
            "random_state": config.random_seed,
        },
        "ExtraTreesRegressor": {
            "n_estimators": 300,
            "min_samples_leaf": 2,
            "random_state": config.random_seed,
        },
        "SVR": {"C": 10.0, "epsilon": 0.01},
        "MLPRegressor": {
            "hidden_layer_sizes": [32],
            "activation": "relu",
            "alpha": 0.001,
            "learning_rate_init": 0.001,
            "max_iter": 1000,
            "early_stopping": False,
            "random_state": config.random_seed,
        },
        "XGBoost": {
            "n_estimators": 250,
            "learning_rate": 0.03,
            "max_depth": 3,
            "objective": "reg:squarederror",
            "random_state": config.random_seed,
        },
        "LightGBM": {
            "n_estimators": 250,
            "learning_rate": 0.03,
            "max_depth": 3,
            "num_leaves": 7,
            "min_child_samples": 2,
            "min_data_in_bin": 1,
            "random_state": config.random_seed,
        },
    }
    for base_model, parameters in ml_parameters.items():
        for mode in ("target_only_features", "multivariate_lag_features"):
            specs.append(
                ModelSpec(
                    f"{base_model}_{mode}",
                    base_model,
                    mode,
                    "machine_learning" if base_model != "MLPRegressor" else "neural_network",
                    parameters,
                )
            )

    try:
        import catboost  # noqa: F401

        catboost_enabled = True
        catboost_reason = ""
    except Exception as exc:
        catboost_enabled = False
        catboost_reason = f"CatBoost dependency unavailable: {exc}"
    for mode in ("target_only_features", "multivariate_lag_features"):
        specs.append(
            ModelSpec(
                f"CatBoost_{mode}",
                "CatBoost",
                mode,
                "machine_learning",
                {
                    "iterations": 250,
                    "learning_rate": 0.03,
                    "depth": 3,
                    "random_seed": config.random_seed,
                },
                enabled=catboost_enabled,
                exclusion_reason=catboost_reason,
            )
        )

    deep_reason = (
        "Not run inside the deterministic engine. The finalized deep_forecasting_evaluation.py "
        "benchmarks direct nine-step Darts models on the same seven primary folds and records "
        "three-seed stability; deep secondary one-step folds are intentionally omitted."
    )
    for model in ("TiDE_univariate", "NBEATS", "NHITS", "TFT"):
        specs.append(
            ModelSpec(
                model,
                model,
                "target_only_features",
                "deep_learning",
                {"input_chunk_length": 12, "output_chunk_length": 1, "n_epochs": 50, "random_state": config.random_seed},
                enabled=False,
                exclusion_reason=deep_reason,
            )
        )
    specs.append(
        ModelSpec(
            "TiDE_with_past_covariates",
            "TiDE_with_past_covariates",
            "multivariate_lag_features",
            "deep_learning",
            {"input_chunk_length": 12, "output_chunk_length": 1, "n_epochs": 50, "random_state": config.random_seed},
            enabled=False,
            exclusion_reason=(
                "The endogenous-past-covariate TiDE variant remains excluded because no leakage-safe "
                "future endogenous generator is implemented. Target-only and known-calendar TiDE "
                "are evaluated in deep_forecasting_evaluation.py."
            ),
        )
    )
    if config.model_allowlist is not None:
        allow = set(config.model_allowlist)
        specs = [spec for spec in specs if spec.model in allow]
    return specs


def make_ml_estimator(base_model: str, seed: int) -> Pipeline:
    """Construct one fixed, seeded sklearn-compatible estimator pipeline."""

    scale_models = {"Ridge", "Lasso", "ElasticNet", "SVR", "MLPRegressor"}
    if base_model == "Ridge":
        estimator = Ridge(alpha=1.0)
    elif base_model == "Lasso":
        estimator = Lasso(alpha=0.001, max_iter=10000, random_state=seed)
    elif base_model == "ElasticNet":
        estimator = ElasticNet(alpha=0.001, l1_ratio=0.5, max_iter=10000, random_state=seed)
    elif base_model == "RandomForestRegressor":
        estimator = RandomForestRegressor(
            n_estimators=300,
            min_samples_leaf=2,
            random_state=seed,
            n_jobs=-1,
        )
    elif base_model == "ExtraTreesRegressor":
        estimator = ExtraTreesRegressor(
            n_estimators=300,
            min_samples_leaf=2,
            random_state=seed,
            n_jobs=-1,
        )
    elif base_model == "SVR":
        estimator = SVR(C=10.0, epsilon=0.01)
    elif base_model == "MLPRegressor":
        estimator = MLPRegressor(
            hidden_layer_sizes=(32,),
            activation="relu",
            alpha=0.001,
            learning_rate_init=0.001,
            max_iter=1000,
            early_stopping=False,
            random_state=seed,
        )
    elif base_model == "XGBoost":
        from xgboost import XGBRegressor

        estimator = XGBRegressor(
            n_estimators=250,
            learning_rate=0.03,
            max_depth=3,
            objective="reg:squarederror",
            random_state=seed,
            n_jobs=1,
            verbosity=0,
        )
    elif base_model == "LightGBM":
        from lightgbm import LGBMRegressor

        estimator = LGBMRegressor(
            n_estimators=250,
            learning_rate=0.03,
            max_depth=3,
            num_leaves=7,
            min_child_samples=2,
            min_data_in_bin=1,
            random_state=seed,
            n_jobs=1,
            deterministic=True,
            force_col_wise=True,
            verbose=-1,
        )
    elif base_model == "CatBoost":
        from catboost import CatBoostRegressor

        estimator = CatBoostRegressor(
            iterations=250,
            learning_rate=0.03,
            depth=3,
            loss_function="RMSE",
            random_seed=seed,
            verbose=False,
        )
    else:
        raise ValueError(f"Unsupported ML model: {base_model}")

    steps: List[Tuple[str, object]] = [("imputer", DataFrameMedianImputer())]
    if base_model in scale_models:
        steps.append(("scaler", AuditedStandardScaler()))
    steps.append(("model", estimator))
    return Pipeline(steps)


def fit_ml_group(
    spec: ModelSpec,
    observed_history: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    seed: int,
) -> Dict[str, Pipeline]:
    """Fit one target model per outcome using only fold-training rows."""

    if observed_history.index.max() != training_dates.max():
        raise AssertionError("Observed history must end at the fold forecast origin.")
    models: Dict[str, Pipeline] = {}
    for target in TARGETS:
        features = build_feature_frame(target, observed_history, spec.feature_mode)
        X_train = features.loc[training_dates]
        y_train = observed_history.loc[training_dates, target]
        if X_train.isna().any().any():
            raise ValueError(f"Incomplete training features for {target} and {spec.model}")
        if X_train.index.max() > training_dates.max():
            raise AssertionError("Preprocessing received a post-origin date.")
        model = make_ml_estimator(spec.base_model, seed)
        model.fit(X_train, y_train)
        imputer = model.named_steps["imputer"]
        if imputer.fit_index_end_ != training_dates.max():
            raise AssertionError("Imputer fit range crossed the forecast origin.")
        if "scaler" in model.named_steps:
            scaler = model.named_steps["scaler"]
            if scaler.fit_index_end_ != training_dates.max():
                raise AssertionError("Scaler fit range crossed the forecast origin.")
        models[target] = model
    return models


def recursive_ml_forecast(
    spec: ModelSpec,
    observed_history: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
    seed: int,
) -> pd.DataFrame:
    """Forecast synchronously, appending predictions rather than future actuals."""

    models = fit_ml_group(spec, observed_history, training_dates, seed)
    simulated = observed_history.copy()
    rows = []
    for forecast_date in forecast_dates:
        month_predictions: Dict[str, float] = {}
        for target in TARGETS:
            X_next = build_recursive_feature_row(
                target,
                simulated,
                forecast_date,
                spec.feature_mode,
            )
            prediction = models[target].predict(X_next)
            month_predictions[target] = float(apply_domain_bounds(target, prediction)[0])
        # Append only after every target has been predicted from the same state.
        next_row = pd.DataFrame(
            [month_predictions],
            index=pd.DatetimeIndex([forecast_date], name=simulated.index.name),
        )
        simulated = pd.concat([simulated, next_row])
        rows.append(next_row)
    result = pd.concat(rows)
    if not result.index.equals(forecast_dates):
        raise AssertionError("Recursive ML forecast dates do not match the requested horizon.")
    return result[TARGETS]


def select_sarimax_columns(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    maximum_features: int = 8,
) -> List[str]:
    """Select exogenous columns using fold-training correlations only."""

    if X_train.index.max() != y_train.index.max():
        raise AssertionError("SARIMAX feature selection ranges do not match.")
    correlations = X_train.apply(lambda column: column.corr(y_train)).abs()
    correlations = correlations.replace([np.inf, -np.inf], np.nan).dropna()
    return correlations.sort_values(ascending=False).head(maximum_features).index.tolist()


def recursive_sarimax_forecast(
    observed_history: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Forecast SARIMAX targets synchronously with recursively generated lags."""

    from statsmodels.tsa.statespace.sarimax import SARIMAX

    results = {}
    selected_columns: Dict[str, List[str]] = {}
    for target in TARGETS:
        features = build_multivariate_lag_features(target, observed_history)
        X_train = features.loc[training_dates]
        y_train = observed_history.loc[training_dates, target]
        columns = select_sarimax_columns(X_train, y_train, maximum_features=8)
        if not columns:
            raise ValueError(f"No SARIMAX exogenous columns for {target}")
        selected_columns[target] = columns
        model = SARIMAX(
            y_train,
            exog=X_train[columns],
            order=(1, 1, 1),
            seasonal_order=(1, 1, 1, SEASONALITY),
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        results[target] = model.fit(disp=False, maxiter=200, warn_convergence=False)

    simulated = observed_history.copy()
    rows = []
    for forecast_date in forecast_dates:
        predictions: Dict[str, float] = {}
        next_exog: Dict[str, pd.DataFrame] = {}
        for target in TARGETS:
            feature_row = build_recursive_feature_row(
                target,
                simulated,
                forecast_date,
                "multivariate_lag_features",
            )
            exog = feature_row[selected_columns[target]]
            next_exog[target] = exog
            prediction = results[target].get_forecast(steps=1, exog=exog).predicted_mean
            predictions[target] = float(apply_domain_bounds(target, prediction)[0])
        next_row = pd.DataFrame(
            [predictions],
            index=pd.DatetimeIndex([forecast_date], name=simulated.index.name),
        )
        simulated = pd.concat([simulated, next_row])
        rows.append(next_row)
        for target in TARGETS:
            endog = pd.Series([predictions[target]], index=[forecast_date], name=target)
            results[target] = results[target].append(
                endog=endog,
                exog=next_exog[target],
                refit=False,
            )
    return pd.concat(rows)[TARGETS]


def var_forecast(
    observed_history: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
) -> Tuple[pd.DataFrame, int]:
    """Fit a small fold-local VAR and derive occupancy from its forecasts."""

    from statsmodels.tsa.api import VAR

    train = observed_history.loc[training_dates, VAR_COLUMNS]
    maximum_lag = min(3, max(1, len(train) // 8))
    model = VAR(train)
    selection = model.select_order(maxlags=maximum_lag)
    selected_lag = selection.selected_orders.get("aic")
    if selected_lag is None or selected_lag < 1:
        selected_lag = selection.selected_orders.get("bic")
    selected_lag = int(selected_lag) if selected_lag and selected_lag >= 1 else 1
    selected_lag = min(selected_lag, maximum_lag)
    fit = model.fit(selected_lag)
    values = fit.forecast(train.to_numpy()[-selected_lag:], steps=len(forecast_dates))
    forecast = pd.DataFrame(values, index=forecast_dates, columns=VAR_COLUMNS)
    forecast["g_and_a_occupancy_rate"] = (
        forecast["g_and_a_beds_occupied"] / forecast["g_and_a_beds_available"]
    ).clip(0, 1)
    return forecast, selected_lag


def _fold_dates(
    development_index: pd.DatetimeIndex,
    fold: RollingOriginFold,
) -> Tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Recover exact train and validation date vectors for a fold."""

    training = development_index[
        (development_index >= fold.training_start) & (development_index <= fold.training_end)
    ]
    validation = development_index[
        (development_index >= fold.validation_start) & (development_index <= fold.validation_end)
    ]
    return training, validation


def _empty_metric_row(
    target: str,
    spec: ModelSpec,
    fold: RollingOriginFold,
    status: str,
    reason: str,
) -> Dict[str, object]:
    """Create a transparent failed or excluded fold record."""

    return {
        "target": target,
        "model": spec.model,
        "feature_mode": spec.feature_mode,
        "model_family": spec.family,
        "protocol": fold.protocol,
        "fold_id": fold.fold_id,
        "forecast_origin": fold.training_end,
        "MAE": np.nan,
        "RMSE": np.nan,
        "MAPE": np.nan,
        "sMAPE": np.nan,
        "MASE": np.nan,
        "bias": np.nan,
        "status": status,
        "failure_reason": reason,
        "hyperparameters": json.dumps(dict(spec.hyperparameters), sort_keys=True),
    }


def _record_success(
    target: str,
    spec: ModelSpec,
    fold: RollingOriginFold,
    training_values: pd.Series,
    actual: pd.Series,
    prediction: pd.Series,
    metric_rows: List[Dict[str, object]],
    prediction_rows: List[Dict[str, object]],
) -> None:
    """Validate and append one successful fold's metrics and predictions."""

    if len(prediction) != fold.validation_observations:
        raise AssertionError("Forecast length does not match the fold horizon.")
    if not np.isfinite(prediction.to_numpy(dtype=float)).all():
        raise AssertionError("Forecast contains NaN or infinite values.")
    metrics = calculate_metrics(actual, prediction, training_values)
    metric_rows.append(
        {
            "target": target,
            "model": spec.model,
            "feature_mode": spec.feature_mode,
            "model_family": spec.family,
            "protocol": fold.protocol,
            "fold_id": fold.fold_id,
            "forecast_origin": fold.training_end,
            **metrics,
            "status": "ok",
            "failure_reason": "",
            "hyperparameters": json.dumps(dict(spec.hyperparameters), sort_keys=True),
        }
    )
    for step, forecast_date in enumerate(actual.index, start=1):
        actual_value = float(actual.loc[forecast_date])
        predicted_value = float(prediction.loc[forecast_date])
        error = actual_value - predicted_value
        prediction_rows.append(
            {
                "target": target,
                "model": spec.model,
                "feature_mode": spec.feature_mode,
                "protocol": fold.protocol,
                "fold_id": fold.fold_id,
                "forecast_origin": fold.training_end,
                "forecast_date": forecast_date,
                "horizon_step": step,
                "actual": actual_value,
                "prediction": predicted_value,
                "error": error,
                "absolute_error": abs(error),
                "squared_error": error**2,
            }
        )


def evaluate_rolling_origin(
    target_df: pd.DataFrame,
    development: pd.DataFrame,
    folds: Sequence[RollingOriginFold],
    specs: Sequence[ModelSpec],
    seed: int,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate every registered model on common leakage-safe rolling folds."""

    metric_rows: List[Dict[str, object]] = []
    prediction_rows: List[Dict[str, object]] = []
    statistical_specs = [s for s in specs if s.family == "statistical" and s.base_model in {"naive", "seasonal_naive", "ETS", "SARIMA"}]
    sarimax_specs = [s for s in specs if s.base_model == "SARIMAX"]
    ml_specs = [s for s in specs if s.family in {"machine_learning", "neural_network"}]
    disabled_specs = [s for s in specs if not s.enabled]
    enabled_ml_specs = [s for s in ml_specs if s.enabled]

    for fold in folds:
        training_dates, validation_dates = _fold_dates(development.index, fold)
        origin = training_dates.max()
        observed_history = target_df.loc[:origin, TARGETS].copy()

        for spec in statistical_specs:
            if not spec.enabled:
                continue
            for target in TARGETS:
                try:
                    history = development.loc[training_dates, target]
                    if spec.base_model == "naive":
                        values = naive_forecast(history, len(validation_dates))
                    elif spec.base_model == "seasonal_naive":
                        values = seasonal_naive_forecast(history, len(validation_dates))
                    elif spec.base_model == "ETS":
                        values = ets_forecast(history, len(validation_dates))
                    else:
                        values = sarima_forecast(history, len(validation_dates))
                    values = apply_domain_bounds(target, values)
                    prediction = pd.Series(values, index=validation_dates, name=spec.model)
                    _record_success(
                        target,
                        spec,
                        fold,
                        history,
                        development.loc[validation_dates, target],
                        prediction,
                        metric_rows,
                        prediction_rows,
                    )
                except Exception as exc:
                    metric_rows.append(_empty_metric_row(target, spec, fold, "failed", str(exc)))

        for spec in enabled_ml_specs:
            try:
                group_prediction = recursive_ml_forecast(
                    spec,
                    observed_history,
                    training_dates,
                    validation_dates,
                    seed,
                )
                for target in TARGETS:
                    _record_success(
                        target,
                        spec,
                        fold,
                        development.loc[training_dates, target],
                        development.loc[validation_dates, target],
                        group_prediction[target],
                        metric_rows,
                        prediction_rows,
                    )
            except Exception as exc:
                for target in TARGETS:
                    metric_rows.append(_empty_metric_row(target, spec, fold, "failed", str(exc)))

        for spec in sarimax_specs:
            if not spec.enabled:
                continue
            try:
                group_prediction = recursive_sarimax_forecast(
                    observed_history,
                    training_dates,
                    validation_dates,
                )
                for target in TARGETS:
                    _record_success(
                        target,
                        spec,
                        fold,
                        development.loc[training_dates, target],
                        development.loc[validation_dates, target],
                        group_prediction[target],
                        metric_rows,
                        prediction_rows,
                    )
            except Exception as exc:
                for target in TARGETS:
                    metric_rows.append(_empty_metric_row(target, spec, fold, "failed", str(exc)))

        var_spec = ModelSpec("VAR", "VAR", "multivariate", "statistical", {"maximum_lag": 3})
        var_implied_spec = ModelSpec("VAR_implied", "VAR", "multivariate", "statistical", {"maximum_lag": 3})
        if config_allows_model(specs, "VAR"):
            try:
                prediction, selected_lag = var_forecast(observed_history, training_dates, validation_dates)
                for target in VAR_COLUMNS:
                    spec_with_lag = ModelSpec(
                        "VAR", "VAR", "multivariate", "statistical", {"selected_lag": selected_lag}
                    )
                    _record_success(
                        target,
                        spec_with_lag,
                        fold,
                        development.loc[training_dates, target],
                        development.loc[validation_dates, target],
                        prediction[target],
                        metric_rows,
                        prediction_rows,
                    )
                _record_success(
                    "g_and_a_occupancy_rate",
                    ModelSpec("VAR_implied", "VAR", "multivariate", "statistical", {"selected_lag": selected_lag}),
                    fold,
                    development.loc[training_dates, "g_and_a_occupancy_rate"],
                    development.loc[validation_dates, "g_and_a_occupancy_rate"],
                    prediction["g_and_a_occupancy_rate"],
                    metric_rows,
                    prediction_rows,
                )
                for target in [t for t in TARGETS if t not in VAR_COLUMNS and t != "g_and_a_occupancy_rate"]:
                    metric_rows.append(
                        _empty_metric_row(
                            target,
                            var_spec,
                            fold,
                            "excluded",
                            "VAR does not directly model LOS percentage targets.",
                        )
                    )
            except Exception as exc:
                for target in TARGETS:
                    spec = var_implied_spec if target == "g_and_a_occupancy_rate" else var_spec
                    metric_rows.append(_empty_metric_row(target, spec, fold, "failed", str(exc)))

        for spec in disabled_specs:
            for target in TARGETS:
                metric_rows.append(
                    _empty_metric_row(
                        target,
                        spec,
                        fold,
                        "excluded",
                        spec.exclusion_reason,
                    )
                )

    return pd.DataFrame(metric_rows), pd.DataFrame(prediction_rows)


def config_allows_model(specs: Sequence[ModelSpec], model: str) -> bool:
    """Respect a lightweight allowlist for special group models such as VAR."""

    listed_models = {spec.model for spec in specs}
    # Full registries do not need an explicit VAR placeholder.
    has_allowlist_shape = len(specs) < 20
    return model in listed_models or not has_allowlist_shape


def summarize_rolling_origin_metrics(fold_metrics: pd.DataFrame) -> pd.DataFrame:
    """Summarize accuracy, stability, ranks, wins, and baseline skill."""

    metrics = fold_metrics.copy()
    successful = metrics.loc[metrics["status"].eq("ok")].copy()
    successful["fold_rank"] = successful.groupby(
        ["target", "protocol", "fold_id"]
    )["MAE"].rank(method="min")
    seasonal = successful.loc[successful["model"].eq("seasonal_naive"), [
        "target", "protocol", "fold_id", "MAE"
    ]].rename(columns={"MAE": "seasonal_fold_MAE"})
    successful = successful.merge(
        seasonal,
        on=["target", "protocol", "fold_id"],
        how="left",
    )
    successful["beat_seasonal"] = successful["MAE"] < successful["seasonal_fold_MAE"]

    rows = []
    group_columns = ["target", "model", "feature_mode", "model_family", "protocol"]
    for keys, all_group in metrics.groupby(group_columns, sort=True, dropna=False):
        target, model, feature_mode, family, protocol = keys
        ok_group = successful.loc[
            (successful["target"] == target)
            & (successful["model"] == model)
            & (successful["feature_mode"] == feature_mode)
            & (successful["protocol"] == protocol)
        ]
        seasonal_mean = successful.loc[
            (successful["target"] == target)
            & (successful["protocol"] == protocol)
            & (successful["model"] == "seasonal_naive"),
            "MAE",
        ].mean()
        mean_mae = ok_group["MAE"].mean()
        skill = (
            1 - mean_mae / seasonal_mean
            if pd.notna(mean_mae) and pd.notna(seasonal_mean) and seasonal_mean != 0
            else np.nan
        )
        reasons = sorted(set(all_group.loc[all_group["status"].ne("ok"), "failure_reason"].dropna()))
        rows.append(
            {
                "target": target,
                "model": model,
                "feature_mode": feature_mode,
                "model_family": family,
                "protocol": protocol,
                "successful_folds": int(all_group["status"].eq("ok").sum()),
                "failed_folds": int(all_group["status"].ne("ok").sum()),
                "excluded_folds": int(all_group["status"].eq("excluded").sum()),
                "mean_MAE": mean_mae,
                "median_MAE": ok_group["MAE"].median(),
                "std_MAE": ok_group["MAE"].std(ddof=1),
                "min_MAE": ok_group["MAE"].min(),
                "max_MAE": ok_group["MAE"].max(),
                "mean_RMSE": ok_group["RMSE"].mean(),
                "mean_sMAPE": ok_group["sMAPE"].mean(),
                "mean_MASE": ok_group["MASE"].mean(),
                "mean_bias": ok_group["bias"].mean(),
                "mean_rank": ok_group["fold_rank"].mean(),
                "fold_win_count": int(ok_group["fold_rank"].eq(1).sum()),
                "seasonal_naive_skill_MAE": skill,
                "percentage_folds_beating_seasonal_naive": (
                    float(ok_group["beat_seasonal"].mean() * 100) if len(ok_group) else np.nan
                ),
                "hyperparameters": all_group["hyperparameters"].iloc[0],
                "failure_reasons": " | ".join(reasons),
            }
        )
    return pd.DataFrame(rows)


def model_simplicity_rank(model: str) -> int:
    """Return the documented preference used only inside a 1% practical tie."""

    if model in {"naive", "seasonal_naive"}:
        return 1
    if model == "ETS":
        return 2
    if model in {"SARIMA", "SARIMAX_multivariate_lag_features", "VAR", "VAR_implied"}:
        return 3
    if model.startswith(("Ridge_", "Lasso_", "ElasticNet_")):
        return 4
    if model.startswith(("MLPRegressor_", "TiDE", "NBEATS", "NHITS", "TFT")):
        return 6
    return 5


def select_validation_models(
    model_summary: pd.DataFrame,
    total_primary_folds: int,
    practical_tie_fraction: float = 0.01,
    minimum_complex_model_skill: float = 0.01,
) -> pd.DataFrame:
    """Select models from fixed-origin validation only; no test input is accepted."""

    primary = model_summary.loc[
        model_summary["protocol"].eq("fixed_origin_9_month")
        & model_summary["successful_folds"].eq(total_primary_folds)
        & model_summary["mean_MAE"].notna()
    ].copy()
    rows = []
    for target in TARGETS:
        candidates = primary.loc[primary["target"].eq(target)].copy()
        if candidates.empty:
            raise RuntimeError(f"No fully successful primary-validation model for {target}")
        candidates = candidates.sort_values(
            ["mean_MAE", "mean_MASE", "mean_RMSE", "std_MAE", "model"],
            na_position="last",
        )
        raw = candidates.iloc[0]
        tie_limit = float(raw["mean_MAE"]) * (1 + practical_tie_fraction)
        tied = candidates.loc[candidates["mean_MAE"] <= tie_limit].copy()
        tied["simplicity_rank"] = tied["model"].map(model_simplicity_rank)
        selected = tied.sort_values(
            ["simplicity_rank", "mean_MASE", "mean_RMSE", "std_MAE", "mean_MAE", "model"],
            na_position="last",
        ).iloc[0]
        practical_tie_applied = len(tied) > 1 and selected["model"] != raw["model"]
        tie_reason = (
            f"{len(tied)} models were within {practical_tie_fraction:.0%} of the raw MAE winner; "
            f"selected the simpler {selected['model']}."
            if practical_tie_applied
            else "No simpler fully eligible model displaced the raw MAE winner within the 1% tie band."
        )

        seasonal = candidates.loc[candidates["model"].eq("seasonal_naive")]
        seasonal_mae = float(seasonal.iloc[0]["mean_MAE"])
        selected_skill = 1 - float(selected["mean_MAE"]) / seasonal_mae
        baseline_guard_applied = False
        if model_simplicity_rank(str(selected["model"])) > 1 and selected_skill <= minimum_complex_model_skill:
            selected = seasonal.iloc[0]
            selected_skill = 0.0
            baseline_guard_applied = True
            tie_reason += (
                f" Seasonal naive was retained because the complex candidate improved MAE by no more "
                f"than {minimum_complex_model_skill:.0%}."
            )

        rows.append(
            {
                "target": target,
                "raw_best_model": raw["model"],
                "raw_best_feature_mode": raw["feature_mode"],
                "selected_model": selected["model"],
                "feature_mode": selected["feature_mode"],
                "primary_validation_MAE": selected["mean_MAE"],
                "primary_validation_MASE": selected["mean_MASE"],
                "primary_validation_RMSE": selected["mean_RMSE"],
                "MAE_standard_deviation": selected["std_MAE"],
                "seasonal_naive_MAE": seasonal_mae,
                "seasonal_naive_skill": selected_skill,
                "practical_tie_applied": practical_tie_applied,
                "baseline_guard_applied": baseline_guard_applied,
                "tie_break_reason": tie_reason,
                "selection_reason": (
                    "Selected before final-test evaluation using mean fixed-origin rolling-validation MAE, "
                    "the 1% practical-tie simplicity rule, and the seasonal-naive skill guard."
                ),
                "hyperparameters": selected["hyperparameters"],
                "number_of_successful_folds": int(selected["successful_folds"]),
            }
        )
    return pd.DataFrame(rows)


def _lookup_spec(specs: Sequence[ModelSpec], model: str) -> ModelSpec:
    """Recover a selected model specification, including special VAR labels."""

    for spec in specs:
        if spec.model == model:
            return spec
    if model in {"VAR", "VAR_implied"}:
        return ModelSpec(model, "VAR", "multivariate", "statistical", {"maximum_lag": 3})
    raise KeyError(model)


def forecast_selected_models(
    target_df: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
    selection: pd.DataFrame,
    specs: Sequence[ModelSpec],
    seed: int,
) -> pd.DataFrame:
    """Forecast locked target-specific models from one common origin."""

    origin = training_dates.max()
    observed_history = target_df.loc[:origin, TARGETS].copy()
    predictions = pd.DataFrame(index=forecast_dates, columns=TARGETS, dtype=float)
    cache: Dict[str, pd.DataFrame] = {}
    for target in TARGETS:
        model_name = selection.loc[selection["target"].eq(target), "selected_model"].iloc[0]
        spec = _lookup_spec(specs, model_name)
        if model_name in cache:
            predictions[target] = cache[model_name][target]
            continue
        if spec.base_model in {"naive", "seasonal_naive", "ETS", "SARIMA"}:
            history = target_df.loc[training_dates, target]
            if spec.base_model == "naive":
                values = naive_forecast(history, len(forecast_dates))
            elif spec.base_model == "seasonal_naive":
                values = seasonal_naive_forecast(history, len(forecast_dates))
            elif spec.base_model == "ETS":
                values = ets_forecast(history, len(forecast_dates))
            else:
                values = sarima_forecast(history, len(forecast_dates))
            predictions[target] = apply_domain_bounds(target, values)
        elif spec.base_model == "SARIMAX":
            cache[model_name] = recursive_sarimax_forecast(
                observed_history,
                training_dates,
                forecast_dates,
            )
            predictions[target] = cache[model_name][target]
        elif spec.base_model == "VAR":
            var_values, _ = var_forecast(observed_history, training_dates, forecast_dates)
            cache[model_name] = var_values
            predictions[target] = var_values[target]
        else:
            cache[model_name] = recursive_ml_forecast(
                spec,
                observed_history,
                training_dates,
                forecast_dates,
                seed,
            )
            predictions[target] = cache[model_name][target]
    if predictions.isna().any().any() or not np.isfinite(predictions.to_numpy()).all():
        raise AssertionError("Locked-model forecast contains invalid values.")
    return predictions


def evaluate_locked_final_test(
    target_df: pd.DataFrame,
    development: pd.DataFrame,
    final_test: pd.DataFrame,
    selection: pd.DataFrame,
    specs: Sequence[ModelSpec],
    seed: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate locked models once at fixed origin and secondarily one step ahead."""

    prediction_rows = []
    metric_rows = []
    baseline_rows = []

    protocols: Dict[str, pd.DataFrame] = {}
    protocols["fixed_origin_9_month"] = forecast_selected_models(
        target_df,
        development.index,
        final_test.index,
        selection,
        specs,
        seed,
    )

    rolling = pd.DataFrame(index=final_test.index, columns=TARGETS, dtype=float)
    for forecast_date in final_test.index:
        training_dates = usable_evaluation_frame(target_df).index[
            usable_evaluation_frame(target_df).index < forecast_date
        ]
        one_step = forecast_selected_models(
            target_df,
            training_dates,
            pd.DatetimeIndex([forecast_date]),
            selection,
            specs,
            seed,
        )
        rolling.loc[forecast_date] = one_step.loc[forecast_date]
    protocols["rolling_one_step"] = rolling

    for protocol, forecast in protocols.items():
        for target in TARGETS:
            selected_row = selection.loc[selection["target"].eq(target)].iloc[0]
            model_name = selected_row["selected_model"]
            feature_mode = selected_row["feature_mode"]
            if protocol == "fixed_origin_9_month":
                baseline_values = seasonal_naive_forecast(
                    development[target],
                    len(final_test),
                )
            else:
                baseline_values = []
                evaluation = usable_evaluation_frame(target_df)
                for forecast_date in final_test.index:
                    history = evaluation.loc[evaluation.index < forecast_date, target]
                    baseline_values.append(seasonal_naive_forecast(history, 1)[0])
                baseline_values = np.asarray(baseline_values)
            baseline = pd.Series(
                apply_domain_bounds(target, baseline_values),
                index=final_test.index,
            )
            actual = final_test[target]
            prediction = forecast[target]
            training_values = development[target]
            selected_metrics = calculate_metrics(actual, prediction, training_values)
            baseline_metrics = calculate_metrics(actual, baseline, training_values)
            metric_rows.append(
                {
                    "target": target,
                    "selected_model": model_name,
                    "feature_mode": feature_mode,
                    "protocol": protocol,
                    **selected_metrics,
                    "seasonal_naive_MAE": baseline_metrics["MAE"],
                    "seasonal_naive_skill": 1 - selected_metrics["MAE"] / baseline_metrics["MAE"],
                    "selection_was_locked_before_test": True,
                }
            )
            for step, forecast_date in enumerate(final_test.index, start=1):
                actual_value = float(actual.loc[forecast_date])
                predicted_value = float(prediction.loc[forecast_date])
                error = actual_value - predicted_value
                prediction_rows.append(
                    {
                        "target": target,
                        "selected_model": model_name,
                        "feature_mode": feature_mode,
                        "protocol": protocol,
                        "forecast_origin": (
                            development.index.max()
                            if protocol == "fixed_origin_9_month"
                            else forecast_date - pd.offsets.MonthBegin(1)
                        ),
                        "forecast_date": forecast_date,
                        "horizon_step": step if protocol == "fixed_origin_9_month" else 1,
                        "actual": actual_value,
                        "prediction": predicted_value,
                        "error": error,
                        "absolute_error": abs(error),
                    }
                )
                baseline_rows.append(
                    {
                        "target": target,
                        "protocol": protocol,
                        "forecast_date": forecast_date,
                        "actual": actual_value,
                        "seasonal_naive_prediction": float(baseline.loc[forecast_date]),
                    }
                )
    return pd.DataFrame(prediction_rows), pd.DataFrame(metric_rows), pd.DataFrame(baseline_rows)


def paired_bootstrap_error_comparison(
    validation_predictions: pd.DataFrame,
    selection: pd.DataFrame,
    repetitions: int,
    seed: int,
) -> pd.DataFrame:
    """Bootstrap paired MAE differences with an explicit small-sample warning."""

    rng = np.random.default_rng(seed)
    rows = []
    primary = validation_predictions.loc[
        validation_predictions["protocol"].eq("fixed_origin_9_month")
    ]
    for target in TARGETS:
        selected_model = selection.loc[selection["target"].eq(target), "selected_model"].iloc[0]
        selected = primary.loc[
            (primary["target"] == target) & (primary["model"] == selected_model),
            ["fold_id", "forecast_date", "absolute_error"],
        ].rename(columns={"absolute_error": "selected_absolute_error"})
        baseline = primary.loc[
            (primary["target"] == target) & (primary["model"] == "seasonal_naive"),
            ["fold_id", "forecast_date", "absolute_error"],
        ].rename(columns={"absolute_error": "baseline_absolute_error"})
        paired = selected.merge(baseline, on=["fold_id", "forecast_date"], how="inner")
        difference = paired["selected_absolute_error"] - paired["baseline_absolute_error"]
        if len(difference):
            draws = rng.choice(difference.to_numpy(), size=(repetitions, len(difference)), replace=True)
            means = draws.mean(axis=1)
            lower, upper = np.quantile(means, [0.025, 0.975])
            mean_difference = float(difference.mean())
        else:
            lower = upper = mean_difference = np.nan
        rows.append(
            {
                "target": target,
                "selected_model": selected_model,
                "protocol": "fixed_origin_9_month",
                "paired_forecasts": len(difference),
                "mean_MAE_difference_selected_minus_seasonal": mean_difference,
                "bootstrap_95pct_lower": lower,
                "bootstrap_95pct_upper": upper,
                "selected_better_on_average": bool(pd.notna(mean_difference) and mean_difference < 0),
                "warning": (
                    "Exploratory only: overlapping rolling-origin horizons make paired errors dependent, "
                    "and the small sample is underpowered for strong significance claims."
                ),
            }
        )
    return pd.DataFrame(rows)


def build_canonical_selection(
    validation_selection: pd.DataFrame,
    final_test_metrics: pd.DataFrame,
    previous_selection_path: Path,
) -> pd.DataFrame:
    """Merge locked validation choices with separately labelled test outcomes."""

    fixed = final_test_metrics.loc[
        final_test_metrics["protocol"].eq("fixed_origin_9_month")
    ].rename(
        columns={
            "MAE": "final_test_MAE",
            "RMSE": "final_test_RMSE",
            "MAPE": "final_test_MAPE",
            "sMAPE": "final_test_sMAPE",
            "MASE": "final_test_MASE",
            "bias": "final_test_bias",
            "seasonal_naive_MAE": "final_test_seasonal_naive_MAE",
            "seasonal_naive_skill": "final_test_seasonal_naive_skill",
        }
    )
    rolling = final_test_metrics.loc[
        final_test_metrics["protocol"].eq("rolling_one_step")
    ].rename(
        columns={
            "MAE": "rolling_one_step_test_MAE",
            "RMSE": "rolling_one_step_test_RMSE",
            "sMAPE": "rolling_one_step_test_sMAPE",
            "MASE": "rolling_one_step_test_MASE",
            "bias": "rolling_one_step_test_bias",
        }
    )
    keep_fixed = [
        "target",
        "final_test_MAE",
        "final_test_RMSE",
        "final_test_MAPE",
        "final_test_sMAPE",
        "final_test_MASE",
        "final_test_bias",
        "final_test_seasonal_naive_MAE",
        "final_test_seasonal_naive_skill",
        "selection_was_locked_before_test",
    ]
    keep_rolling = [
        "target",
        "rolling_one_step_test_MAE",
        "rolling_one_step_test_RMSE",
        "rolling_one_step_test_sMAPE",
        "rolling_one_step_test_MASE",
        "rolling_one_step_test_bias",
    ]
    canonical = validation_selection.merge(fixed[keep_fixed], on="target", how="left")
    canonical = canonical.merge(rolling[keep_rolling], on="target", how="left")
    previous = {}
    if previous_selection_path.exists():
        old = pd.read_csv(previous_selection_path)
        model_column = "best_overall_model" if "best_overall_model" in old.columns else "selected_for_final_forecast"
        if model_column in old.columns:
            previous = dict(zip(old["target"], old[model_column]))
    canonical["previous_test_selected_model"] = canonical["target"].map(previous).fillna("not available")
    canonical["changed_from_previous_test_selected_model"] = (
        canonical["selected_model"] != canonical["previous_test_selected_model"]
    )
    canonical["selection_basis"] = "pre-test fixed-origin rolling-validation mean MAE with documented tie/baseline guards"
    return canonical


def generate_evaluation_plots(
    model_summary: pd.DataFrame,
    fold_metrics: pd.DataFrame,
    final_predictions: pd.DataFrame,
    baseline_predictions: pd.DataFrame,
    plot_dir: Path,
) -> None:
    """Save five readable diagnostics per target using top models plus baselines."""

    plot_dir.mkdir(parents=True, exist_ok=True)
    primary_summary = model_summary.loc[
        model_summary["protocol"].eq("fixed_origin_9_month")
        & model_summary["mean_MAE"].notna()
    ]
    primary_folds = fold_metrics.loc[
        fold_metrics["protocol"].eq("fixed_origin_9_month")
        & fold_metrics["status"].eq("ok")
    ]
    fixed_test = final_predictions.loc[
        final_predictions["protocol"].eq("fixed_origin_9_month")
    ]
    fixed_baseline = baseline_predictions.loc[
        baseline_predictions["protocol"].eq("fixed_origin_9_month")
    ]

    for target in TARGETS:
        target_summary = primary_summary.loc[primary_summary["target"].eq(target)].nsmallest(6, "mean_MAE")
        baseline_names = {"naive", "seasonal_naive"}
        selected_names = list(dict.fromkeys(target_summary["model"].tolist() + list(baseline_names)))
        target_summary = primary_summary.loc[
            primary_summary["target"].eq(target) & primary_summary["model"].isin(selected_names)
        ].sort_values("mean_MAE")

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.barh(target_summary["model"], target_summary["mean_MAE"], color="#2b6f77")
        ax.invert_yaxis()
        ax.set_title(f"{target}: rolling-origin mean MAE")
        ax.set_xlabel("Mean validation MAE")
        fig.tight_layout()
        fig.savefig(plot_dir / f"{target}_validation_mean_mae.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

        fold_subset = primary_folds.loc[
            primary_folds["target"].eq(target) & primary_folds["model"].isin(selected_names)
        ]
        labels = []
        values = []
        for model, group in fold_subset.groupby("model"):
            labels.append(model)
            values.append(group["MAE"].to_numpy())
        fig, ax = plt.subplots(figsize=(10, 5))
        if values:
            ax.boxplot(values, tick_labels=labels, vert=False)
        ax.set_title(f"{target}: fold-level MAE stability")
        ax.set_xlabel("Fold MAE")
        fig.tight_layout()
        fig.savefig(plot_dir / f"{target}_fold_mae_stability.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

        test = fixed_test.loc[fixed_test["target"].eq(target)].sort_values("forecast_date")
        baseline = fixed_baseline.loc[fixed_baseline["target"].eq(target)].sort_values("forecast_date")
        dates = pd.to_datetime(test["forecast_date"])
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(dates, test["actual"], marker="o", label="Actual")
        ax.plot(dates, test["prediction"], marker="o", label="Selected forecast")
        ax.set_title(f"{target}: untouched fixed-origin test")
        ax.legend()
        fig.tight_layout()
        fig.savefig(plot_dir / f"{target}_final_test_actual_vs_forecast.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.axhline(0, color="black", linewidth=1)
        ax.plot(dates, test["error"], marker="o", color="#a13d2d")
        ax.set_title(f"{target}: final-test residuals (actual - forecast)")
        ax.set_ylabel("Residual")
        fig.tight_layout()
        fig.savefig(plot_dir / f"{target}_final_test_residuals.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(dates, test["actual"], marker="o", label="Actual")
        ax.plot(dates, test["prediction"], marker="o", label="Selected model")
        ax.plot(
            pd.to_datetime(baseline["forecast_date"]),
            baseline["seasonal_naive_prediction"],
            marker="o",
            linestyle="--",
            label="Seasonal naive",
        )
        ax.set_title(f"{target}: selected model versus seasonal naive")
        ax.legend()
        fig.tight_layout()
        fig.savefig(plot_dir / f"{target}_selected_vs_seasonal_naive.png", dpi=150, bbox_inches="tight")
        plt.close(fig)


def dataframe_to_markdown(frame: pd.DataFrame) -> str:
    """Render a small DataFrame as Markdown without an optional dependency."""

    display = frame.copy()
    for column in display.columns:
        if pd.api.types.is_datetime64_any_dtype(display[column]):
            display[column] = display[column].dt.strftime("%Y-%m-%d")
    headers = [str(column) for column in display.columns]
    rows = [headers, ["---"] * len(headers)]
    for values in display.itertuples(index=False, name=None):
        rows.append(["" if pd.isna(value) else str(value) for value in values])
    return "\n".join("| " + " | ".join(row) + " |" for row in rows)


def write_protocol_summary(
    path: Path,
    config: EvaluationConfig,
    development: pd.DataFrame,
    final_test: pd.DataFrame,
    folds: pd.DataFrame,
    canonical_selection: pd.DataFrame,
    model_summary: pd.DataFrame,
) -> None:
    """Write a supervisor-friendly account of the final evaluation design."""

    fixed_folds = folds.loc[folds["protocol"].eq("fixed_origin_9_month")]
    one_step_folds = folds.loc[folds["protocol"].eq("rolling_one_step")]
    excluded = model_summary.loc[
        model_summary["protocol"].eq("fixed_origin_9_month")
        & model_summary["successful_folds"].eq(0),
        ["model", "failure_reasons"],
    ].drop_duplicates()
    lines = [
        "# Final deterministic evaluation protocol",
        "",
        "## Data periods",
        "",
        f"- Development data: {development.index.min().date()} to {development.index.max().date()} ({len(development)} usable months).",
        f"- Untouched final test: {final_test.index.min().date()} to {final_test.index.max().date()} ({len(final_test)} months).",
        "- The source is monthly NHS England all-acute-trust aggregate data, not raw daily or patient-level data.",
        "",
        "## Rolling-origin construction",
        "",
        f"The primary protocol uses {len(fixed_folds)} expanding-window folds, a minimum of {config.minimum_training_observations} usable months, a nine-month horizon, and a one-month step. The secondary protocol uses {len(one_step_folds)} expanding one-step folds.",
        "",
        dataframe_to_markdown(fixed_folds),
        "",
        "## Forecasting protocols",
        "",
        "- `fixed_origin_9_month` is the primary planning evaluation. It forecasts all nine months without revealing outcomes inside the horizon.",
        "- `rolling_one_step` is a secondary monitoring evaluation. It reveals one actual month, refits, and advances to the next origin.",
        "- Lag-based ML forecasts are recursive. Predictions, never hidden actuals, are appended during a fixed horizon.",
        "- Multivariate ML and SARIMAX predict every target from one shared history state, then append the full predicted vector synchronously.",
        "",
        "## Leakage protections",
        "",
        "All folds end before September 2025. Feature construction uses shifted lags and rolling statistics. Imputers, scalers, correlation selectors, and models are fitted independently inside each fold. Known calendar fields are the only future covariates.",
        "",
        "## Selection rule",
        "",
        "Mean fixed-origin validation MAE is primary. Models must complete every primary fold. Models within 1% of the raw MAE winner are treated as practically tied and the simpler model is preferred. A complex model must improve mean MAE over seasonal naive by more than 1%.",
        "",
        "## Locked final test",
        "",
        "The target-specific model, feature mode, and fixed hyperparameters are selected before the final test is evaluated. Models are then refitted through August 2025 and forecast September 2025-May 2026 once from a fixed origin. Test performance never changes selection.",
        "",
        "## Final validation-selected models",
        "",
        dataframe_to_markdown(canonical_selection[["target", "selected_model", "feature_mode", "primary_validation_MAE", "final_test_MAE"]]),
        "",
        "## Failed or excluded models",
        "",
        dataframe_to_markdown(excluded) if not excluded.empty else "No model families were excluded.",
        "",
        "## Stability and significance",
        "",
        "Fold counts, dispersion, ranks, wins, and seasonal-naive comparisons are reported. The paired bootstrap is exploratory because validation horizons overlap and the sample is small; no strong statistical-significance claim is made.",
        "",
        "## Limitations",
        "",
        "Only 66 monthly observations exist, with 45 usable pre-test rows after lag construction. Seven fixed-horizon folds overlap heavily. Deep models are evaluated separately on those same primary folds because their secondary one-step cost is not decision-relevant. Results are national aggregates and cannot directly optimize trust-level bed allocation.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_final_evaluation(config: EvaluationConfig) -> Dict[str, pd.DataFrame]:
    """Run the complete corrected evaluation and write canonical outputs."""

    warnings.filterwarnings("ignore")
    np.random.seed(config.random_seed)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    target_df = load_nhs_targets(config)
    validate_target_data(target_df)
    development, final_test = split_development_and_final_test(target_df, config)

    fixed_folds = generate_rolling_origin_folds(
        development.index,
        "fixed_origin_9_month",
        config.minimum_training_observations,
        config.fixed_horizon,
        config.fold_step,
    )
    one_step_folds = generate_rolling_origin_folds(
        development.index,
        "rolling_one_step",
        config.minimum_training_observations,
        1,
        config.fold_step,
    )
    all_folds = fixed_folds + one_step_folds
    assert_fold_integrity(all_folds, development.index, final_test.index)
    folds_frame = folds_to_frame(all_folds)
    folds_frame.to_csv(config.output_dir / "rolling_origin_folds.csv", index=False)

    specs = build_model_registry(config)
    fold_metrics_parts = []
    prediction_parts = []
    for protocol_folds in (fixed_folds, one_step_folds):
        metrics, predictions = evaluate_rolling_origin(
            target_df,
            development,
            protocol_folds,
            specs,
            config.random_seed,
        )
        fold_metrics_parts.append(metrics)
        prediction_parts.append(predictions)
    fold_metrics = pd.concat(fold_metrics_parts, ignore_index=True)
    rolling_predictions = pd.concat(prediction_parts, ignore_index=True)
    fold_metrics.to_csv(config.output_dir / "rolling_origin_fold_metrics.csv", index=False)
    rolling_predictions.to_csv(config.output_dir / "rolling_origin_predictions.csv", index=False)

    model_summary = summarize_rolling_origin_metrics(fold_metrics)
    model_summary.to_csv(config.output_dir / "rolling_origin_model_summary.csv", index=False)
    validation_selection = select_validation_models(
        model_summary,
        total_primary_folds=len(fixed_folds),
        practical_tie_fraction=config.practical_tie_fraction,
        minimum_complex_model_skill=config.minimum_complex_model_skill,
    )
    validation_selection.to_csv(config.output_dir / "validation_model_selection.csv", index=False)

    error_comparison = paired_bootstrap_error_comparison(
        rolling_predictions,
        validation_selection,
        config.bootstrap_repetitions,
        config.random_seed,
    )
    error_comparison.to_csv(config.output_dir / "paired_error_comparison.csv", index=False)

    final_predictions, final_metrics, baseline_predictions = evaluate_locked_final_test(
        target_df,
        development,
        final_test,
        validation_selection,
        specs,
        config.random_seed,
    )
    final_predictions.to_csv(config.output_dir / "final_test_predictions.csv", index=False)
    final_metrics.to_csv(config.output_dir / "final_test_metrics.csv", index=False)
    baseline_predictions.to_csv(
        config.output_dir / "final_test_seasonal_naive_predictions.csv",
        index=False,
    )

    canonical = build_canonical_selection(
        validation_selection,
        final_metrics,
        Path("outputs/final_overall_model_selection.csv"),
    )
    canonical.to_csv(config.output_dir / "final_model_selection.csv", index=False)

    if config.generate_plots:
        generate_evaluation_plots(
            model_summary,
            fold_metrics,
            final_predictions,
            baseline_predictions,
            config.output_dir / "plots",
        )
    write_protocol_summary(
        config.output_dir / "evaluation_protocol_summary.md",
        config,
        development,
        final_test,
        folds_frame,
        canonical,
        model_summary,
    )

    if config.mark_uncertainty_stale:
        stale_path = Path("outputs/UNCERTAINTY_RESULTS_STALE.md")
        stale_path.write_text(
            "# Uncertainty outputs are stale\n\n"
            "The existing uncertainty CSVs and plots were fitted around models selected using the old test-based workflow. "
            "They must not be used for optimization until uncertainty is refitted around "
            "`outputs/final_evaluation/final_model_selection.csv`. The corrected deterministic selection was locked using "
            "pre-test rolling-origin validation.\n",
            encoding="utf-8",
        )

    return {
        "targets": target_df,
        "development": development,
        "final_test": final_test,
        "folds": folds_frame,
        "fold_metrics": fold_metrics,
        "rolling_predictions": rolling_predictions,
        "model_summary": model_summary,
        "validation_selection": validation_selection,
        "final_test_predictions": final_predictions,
        "final_test_metrics": final_metrics,
        "final_model_selection": canonical,
        "paired_error_comparison": error_comparison,
    }


def parse_args() -> argparse.Namespace:
    """Parse full or lightweight command-line execution options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lightweight", action="store_true", help="Run baselines, ETS, and Ridge only.")
    parser.add_argument("--no-plots", action="store_true", help="Skip plot generation.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/final_evaluation"))
    return parser.parse_args()


def main() -> None:
    """Execute the corrected evaluation from the repository root."""

    args = parse_args()
    allowlist = None
    mark_stale = True
    if args.lightweight:
        allowlist = (
            "naive",
            "seasonal_naive",
            "ETS",
            "Ridge_target_only_features",
            "Ridge_multivariate_lag_features",
        )
        mark_stale = False
    config = EvaluationConfig(
        output_dir=args.output_dir,
        generate_plots=not args.no_plots,
        mark_uncertainty_stale=mark_stale,
        model_allowlist=allowlist,
    )
    artifacts = run_final_evaluation(config)
    print("\nExact rolling-origin folds:")
    print(artifacts["folds"].to_string(index=False))
    print("\nValidation-selected models and untouched fixed-origin test metrics:")
    print(
        artifacts["final_model_selection"][[
            "target",
            "raw_best_model",
            "selected_model",
            "primary_validation_MAE",
            "final_test_MAE",
            "final_test_seasonal_naive_skill",
            "changed_from_previous_test_selected_model",
        ]].to_string(index=False)
    )


if __name__ == "__main__":
    main()
