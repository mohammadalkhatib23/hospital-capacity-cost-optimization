"""Leakage-safe Darts benchmarks for the NHS monthly forecasting project.

The deterministic engine remains responsible for the classical and standard
machine-learning comparison.  This module adds direct nine-month TiDE,
N-BEATS, N-HiTS, and TFT forecasts on the same seven development folds.  It
never supplies future NHS outcomes as covariates and never uses the sealed
September-2025 to May-2026 test period for model selection.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import random
import tempfile
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from forecasting_evaluation import (
    EvaluationConfig,
    RATE_TARGETS,
    TARGETS,
    apply_domain_bounds,
    build_model_registry,
    calculate_metrics,
    generate_rolling_origin_folds,
    load_nhs_targets,
    seasonal_naive_forecast,
    select_validation_models,
    split_development_and_final_test,
    summarize_rolling_origin_metrics,
)


try:
    import torch
    from darts import TimeSeries
    from darts.models import NBEATSModel, NHiTSModel, TFTModel, TiDEModel
    from pytorch_lightning.callbacks import EarlyStopping

    DARTS_AVAILABLE = True
    DARTS_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - exercised only without optional dependencies
    torch = None
    TimeSeries = None
    NBEATSModel = NHiTSModel = TFTModel = TiDEModel = None
    EarlyStopping = None
    DARTS_AVAILABLE = False
    DARTS_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


PRIMARY_PROTOCOL = "fixed_origin_9_month"
STAGE1_SEED = 42
STAGE2_SEEDS = (17, 42, 73)
CALENDAR_COLUMNS = (
    "month_sin",
    "month_cos",
    "month_number",
    "quarter",
    "year",
)


@dataclass(frozen=True)
class DeepEvaluationConfig:
    """Store the deliberately small deep-model architecture and run settings."""

    deterministic_config: EvaluationConfig = field(
        default_factory=lambda: EvaluationConfig(generate_plots=False)
    )
    output_dir: Path = Path("outputs/final_evaluation")
    input_chunk_length: int = 12
    output_chunk_length: int = 9
    internal_validation_horizon: int = 9
    max_epochs: int = 30
    early_stopping_patience: int = 5
    early_stopping_min_delta: float = 1e-4
    batch_size: int = 8
    stage1_seed: int = STAGE1_SEED
    stage2_seeds: Tuple[int, ...] = STAGE2_SEEDS
    practical_tie_fraction: float = 0.01
    minimum_complex_model_skill: float = 0.01
    accelerator: str = "cpu"


@dataclass(frozen=True)
class DeepModelSpec:
    """Describe one direct Darts architecture and its permitted covariates."""

    model: str
    feature_mode: str
    uses_future_calendar: bool
    hyperparameters: Mapping[str, object]


class AuditedSeriesScaler:
    """Scale one target while recording the exact dates used for fitting."""

    def __init__(self) -> None:
        self.scaler = StandardScaler()
        self.fit_start_: Optional[pd.Timestamp] = None
        self.fit_end_: Optional[pd.Timestamp] = None

    def fit(self, series: pd.Series) -> "AuditedSeriesScaler":
        """Fit only on the supplied chronological training series."""

        if series.empty:
            raise ValueError("Cannot fit a target scaler on an empty series.")
        self.fit_start_ = pd.Timestamp(series.index.min())
        self.fit_end_ = pd.Timestamp(series.index.max())
        self.scaler.fit(series.to_numpy(dtype=float).reshape(-1, 1))
        return self

    def transform(self, series: pd.Series) -> pd.Series:
        """Transform a series without changing its dates or name."""

        values = self.scaler.transform(series.to_numpy(dtype=float).reshape(-1, 1)).reshape(-1)
        return pd.Series(values, index=series.index, name=series.name)

    def inverse_array(self, values: np.ndarray) -> np.ndarray:
        """Return scaled predictions to the original target units."""

        return self.scaler.inverse_transform(np.asarray(values).reshape(-1, 1)).reshape(-1)


class AuditedFrameScaler:
    """Scale calendar covariates and retain their training-only fit range."""

    def __init__(self) -> None:
        self.scaler = StandardScaler()
        self.fit_start_: Optional[pd.Timestamp] = None
        self.fit_end_: Optional[pd.Timestamp] = None
        self.columns_: Tuple[str, ...] = ()

    def fit(self, frame: pd.DataFrame) -> "AuditedFrameScaler":
        """Fit on calendar rows from the internal deep-training segment only."""

        if frame.empty:
            raise ValueError("Cannot fit a calendar scaler on an empty frame.")
        self.fit_start_ = pd.Timestamp(frame.index.min())
        self.fit_end_ = pd.Timestamp(frame.index.max())
        self.columns_ = tuple(frame.columns)
        self.scaler.fit(frame.to_numpy(dtype=float))
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Transform calendar values while preserving the complete future index."""

        if tuple(frame.columns) != self.columns_:
            raise AssertionError("Calendar feature order changed after scaler fitting.")
        values = self.scaler.transform(frame.to_numpy(dtype=float))
        return pd.DataFrame(values, index=frame.index, columns=frame.columns)


def build_deep_model_registry(config: DeepEvaluationConfig) -> List[DeepModelSpec]:
    """Return the fixed small architectures used in the two-stage benchmark."""

    common = {
        "input_chunk_length": config.input_chunk_length,
        "output_chunk_length": config.output_chunk_length,
        "n_epochs": config.max_epochs,
        "batch_size": config.batch_size,
        "early_stopping_patience": config.early_stopping_patience,
    }
    return [
        DeepModelSpec(
            "TiDE_target_only",
            "target_only_deep",
            False,
            {
                **common,
                "hidden_size": 32,
                "num_encoder_layers": 1,
                "num_decoder_layers": 1,
                "dropout": 0.1,
            },
        ),
        DeepModelSpec(
            "TiDE_calendar",
            "known_future_calendar",
            True,
            {
                **common,
                "hidden_size": 32,
                "num_encoder_layers": 1,
                "num_decoder_layers": 1,
                "dropout": 0.1,
            },
        ),
        DeepModelSpec(
            "NBEATS",
            "target_only_deep",
            False,
            {
                **common,
                "num_stacks": 2,
                "num_blocks": 1,
                "num_layers": 2,
                "layer_widths": 32,
                "dropout": 0.1,
            },
        ),
        DeepModelSpec(
            "NHITS",
            "target_only_deep",
            False,
            {
                **common,
                "num_stacks": 2,
                "num_blocks": 1,
                "num_layers": 2,
                "layer_widths": 32,
                "dropout": 0.1,
            },
        ),
        DeepModelSpec(
            "TFT_calendar",
            "known_future_calendar",
            True,
            {
                **common,
                "hidden_size": 8,
                "lstm_layers": 1,
                "num_attention_heads": 1,
                "hidden_continuous_size": 4,
                "dropout": 0.1,
            },
        ),
    ]


def set_deep_seed(seed: int) -> None:
    """Set Python, NumPy, and PyTorch seeds without assuming a GPU exists."""

    random.seed(seed)
    np.random.seed(seed)
    if torch is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        try:
            torch.use_deterministic_algorithms(True, warn_only=True)
        except TypeError:  # pragma: no cover - compatibility for older PyTorch
            torch.use_deterministic_algorithms(True)


def build_known_future_calendar(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Create calendar-only covariates containing no NHS target information."""

    index = pd.DatetimeIndex(index, name="month")
    frame = pd.DataFrame(index=index)
    frame["month_sin"] = np.sin(2 * np.pi * index.month / 12)
    frame["month_cos"] = np.cos(2 * np.pi * index.month / 12)
    frame["month_number"] = index.month.astype(float)
    frame["quarter"] = index.quarter.astype(float)
    frame["year"] = index.year.astype(float)
    if tuple(frame.columns) != CALENDAR_COLUMNS:
        raise AssertionError("Unexpected future-calendar schema.")
    if set(frame.columns).intersection(TARGETS):
        raise AssertionError("Future calendar covariates contain a target column.")
    return frame


def _to_timeseries(series: pd.Series) -> "TimeSeries":
    """Convert a pandas series to a one-component Darts TimeSeries."""

    return TimeSeries.from_times_and_values(
        pd.DatetimeIndex(series.index),
        series.to_numpy(dtype=float).reshape(-1, 1),
        columns=[str(series.name or "target")],
    )


def _frame_to_timeseries(frame: pd.DataFrame) -> "TimeSeries":
    """Convert known calendar values to a multicomponent Darts TimeSeries."""

    return TimeSeries.from_times_and_values(
        pd.DatetimeIndex(frame.index),
        frame.to_numpy(dtype=float),
        columns=list(frame.columns),
    )


def _make_darts_model(
    spec: DeepModelSpec,
    config: DeepEvaluationConfig,
    seed: int,
    callback: "EarlyStopping",
    model_name: str,
    work_dir: str,
):
    """Instantiate one CPU-compatible Darts model with common training controls."""

    trainer_kwargs = {
        "accelerator": config.accelerator,
        "devices": 1,
        "enable_progress_bar": False,
        "enable_model_summary": False,
        "logger": False,
        "deterministic": True,
        "callbacks": [callback],
    }
    common = {
        "input_chunk_length": config.input_chunk_length,
        "output_chunk_length": config.output_chunk_length,
        "n_epochs": config.max_epochs,
        "batch_size": config.batch_size,
        "random_state": seed,
        "save_checkpoints": True,
        "force_reset": True,
        "model_name": model_name,
        "work_dir": work_dir,
        "pl_trainer_kwargs": trainer_kwargs,
    }
    if spec.model.startswith("TiDE"):
        return TiDEModel(
            hidden_size=32,
            num_encoder_layers=1,
            num_decoder_layers=1,
            dropout=0.1,
            **common,
        )
    if spec.model == "NBEATS":
        return NBEATSModel(
            num_stacks=2,
            num_blocks=1,
            num_layers=2,
            layer_widths=32,
            dropout=0.1,
            **common,
        )
    if spec.model == "NHITS":
        return NHiTSModel(
            num_stacks=2,
            num_blocks=1,
            num_layers=2,
            layer_widths=32,
            dropout=0.1,
            **common,
        )
    if spec.model == "TFT_calendar":
        return TFTModel(
            hidden_size=8,
            lstm_layers=1,
            num_attention_heads=1,
            hidden_continuous_size=4,
            dropout=0.1,
            add_relative_index=False,
            **common,
        )
    raise KeyError(spec.model)


def _best_checkpoint_epoch(model, callback: "EarlyStopping") -> int:
    """Recover the restored checkpoint epoch without relying on filename format."""

    try:
        checkpoint_path = model.trainer.checkpoint_callback.best_model_path
        if checkpoint_path:
            checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
            return int(checkpoint.get("epoch", 0)) + 1
    except Exception:
        pass
    stopped = int(getattr(callback, "stopped_epoch", 0))
    return max(1, stopped + 1)


def fit_predict_deep_fold(
    spec: DeepModelSpec,
    target: str,
    target_df: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
    seed: int,
    config: DeepEvaluationConfig,
) -> Tuple[np.ndarray, Dict[str, object]]:
    """Fit one deep model inside one fold and return a direct nine-step path.

    The last nine origin-available training months are used only as the internal
    early-stopping target.  Their preceding 12 months provide validation context.
    The restored checkpoint then receives the complete origin-available history
    as input context.  No validation-horizon or test-horizon target is read.
    """

    if not DARTS_AVAILABLE:
        raise RuntimeError(f"Darts deep dependencies unavailable: {DARTS_IMPORT_ERROR}")
    training_dates = pd.DatetimeIndex(training_dates)
    forecast_dates = pd.DatetimeIndex(forecast_dates)
    if len(forecast_dates) != config.output_chunk_length:
        raise AssertionError("Deep direct forecasts must contain exactly nine months.")
    if training_dates.max() >= forecast_dates.min():
        raise AssertionError("Deep forecast dates must be strictly after training dates.")
    if forecast_dates[0] != training_dates[-1] + pd.offsets.MonthBegin(1):
        raise AssertionError("Deep forecast dates must begin one month after the origin.")

    full_training = target_df.loc[training_dates, target].astype(float)
    internal_horizon = config.internal_validation_horizon
    minimum = config.input_chunk_length + config.output_chunk_length + internal_horizon
    if len(full_training) < minimum:
        raise ValueError(
            f"Deep training needs at least {minimum} origin-available months; found {len(full_training)}."
        )
    fit_series = full_training.iloc[:-internal_horizon]
    validation_context = full_training.iloc[-(config.input_chunk_length + internal_horizon) :]
    if validation_context.index.max() > training_dates.max():
        raise AssertionError("Internal validation escaped the fold training period.")

    target_scaler = AuditedSeriesScaler().fit(fit_series)
    fit_scaled = target_scaler.transform(fit_series)
    validation_scaled = target_scaler.transform(validation_context)
    full_training_scaled = target_scaler.transform(full_training)

    calendar_ts = None
    calendar_scaler = None
    if spec.uses_future_calendar:
        calendar_index = pd.date_range(
            training_dates.min(),
            forecast_dates.max(),
            freq="MS",
            name="month",
        )
        calendar = build_known_future_calendar(calendar_index)
        calendar_scaler = AuditedFrameScaler().fit(calendar.loc[fit_series.index])
        calendar_ts = _frame_to_timeseries(calendar_scaler.transform(calendar))

    set_deep_seed(seed)
    callback = EarlyStopping(
        monitor="val_loss",
        patience=config.early_stopping_patience,
        min_delta=config.early_stopping_min_delta,
        mode="min",
    )
    runtime_start = time.perf_counter()
    captured = io.StringIO()
    warning_messages: List[str] = []
    safe_name = f"{target}_{spec.model}_{training_dates.max():%Y%m}_{seed}"
    with tempfile.TemporaryDirectory(prefix="nhs_deep_") as temp_dir:
        model = _make_darts_model(spec, config, seed, callback, safe_name, temp_dir)
        with warnings.catch_warnings(record=True) as caught, contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            warnings.simplefilter("always")
            fit_kwargs = {
                "series": _to_timeseries(fit_scaled),
                "val_series": _to_timeseries(validation_scaled),
                "verbose": False,
                "load_best": True,
            }
            if spec.uses_future_calendar:
                fit_kwargs["future_covariates"] = calendar_ts
                fit_kwargs["val_future_covariates"] = calendar_ts
            model.fit(**fit_kwargs)

            predict_kwargs = {
                "n": config.output_chunk_length,
                "series": _to_timeseries(full_training_scaled),
                "verbose": False,
                "show_warnings": False,
            }
            if spec.uses_future_calendar:
                predict_kwargs["future_covariates"] = calendar_ts
            forecast_scaled = model.predict(**predict_kwargs)
            warning_messages = [str(item.message) for item in caught]
        best_epoch = _best_checkpoint_epoch(model, callback)
        best_score = float(callback.best_score) if callback.best_score is not None else np.nan

    values = forecast_scaled.values(copy=False).reshape(-1)
    predictions = target_scaler.inverse_array(values)
    predictions = apply_domain_bounds(target, predictions)
    if len(predictions) != config.output_chunk_length:
        raise AssertionError("Darts returned an incorrect direct forecast length.")
    if not np.isfinite(predictions).all():
        raise AssertionError("Darts returned NaN or infinite predictions.")

    captured_lines = [line.strip() for line in captured.getvalue().splitlines() if line.strip()]
    diagnostics = {
        "seed": seed,
        "runtime_seconds": time.perf_counter() - runtime_start,
        "best_epoch": best_epoch,
        "best_internal_validation_loss": best_score,
        "target_scaler_fit_start": target_scaler.fit_start_,
        "target_scaler_fit_end": target_scaler.fit_end_,
        "calendar_scaler_fit_start": calendar_scaler.fit_start_ if calendar_scaler else pd.NaT,
        "calendar_scaler_fit_end": calendar_scaler.fit_end_ if calendar_scaler else pd.NaT,
        "deep_fit_start": fit_series.index.min(),
        "deep_fit_end": fit_series.index.max(),
        "internal_validation_start": validation_context.index[-internal_horizon],
        "internal_validation_end": validation_context.index.max(),
        "warnings": " | ".join(sorted(set(warning_messages + captured_lines[-3:]))),
    }
    return np.asarray(predictions, dtype=float), diagnostics


def _fold_dates(fold) -> Tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Expand a fold descriptor into exact monthly training and forecast dates."""

    training_dates = pd.date_range(fold.training_start, fold.training_end, freq="MS")
    forecast_dates = pd.date_range(fold.validation_start, fold.validation_end, freq="MS")
    return training_dates, forecast_dates


def evaluate_deep_model_on_folds(
    spec: DeepModelSpec,
    targets: Sequence[str],
    target_df: pd.DataFrame,
    development_df: pd.DataFrame,
    folds: Sequence[object],
    seed: int,
    stage: str,
    config: DeepEvaluationConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate one architecture and seed on the requested fixed-origin folds."""

    prediction_rows: List[Dict[str, object]] = []
    metric_rows: List[Dict[str, object]] = []
    hyperparameters = json.dumps(dict(spec.hyperparameters), sort_keys=True)
    for target in targets:
        for fold in folds:
            training_dates, forecast_dates = _fold_dates(fold)
            common = {
                "target": target,
                "model": spec.model,
                "feature_mode": spec.feature_mode,
                "model_family": "deep_learning",
                "protocol": PRIMARY_PROTOCOL,
                "fold_id": fold.fold_id,
                "stage": stage,
                "seed": seed,
                "forecast_origin": training_dates.max(),
                "training_start": training_dates.min(),
                "training_end": training_dates.max(),
                "hyperparameters": hyperparameters,
            }
            try:
                predictions, diagnostics = fit_predict_deep_fold(
                    spec,
                    target,
                    target_df,
                    training_dates,
                    forecast_dates,
                    seed,
                    config,
                )
                actual = development_df.loc[forecast_dates, target]
                metrics = calculate_metrics(
                    actual,
                    pd.Series(predictions, index=forecast_dates),
                    development_df.loc[training_dates, target],
                )
                metric_rows.append(
                    {
                        **common,
                        **metrics,
                        **diagnostics,
                        "status": "ok",
                        "failure_reason": "",
                    }
                )
                for horizon_step, (forecast_date, actual_value, prediction) in enumerate(
                    zip(forecast_dates, actual, predictions),
                    start=1,
                ):
                    error = float(actual_value - prediction)
                    prediction_rows.append(
                        {
                            **common,
                            "forecast_date": forecast_date,
                            "horizon_step": horizon_step,
                            "actual": float(actual_value),
                            "prediction": float(prediction),
                            "error": error,
                            "absolute_error": abs(error),
                            "squared_error": error**2,
                            **diagnostics,
                            "evaluation_split": "development_validation",
                        }
                    )
            except Exception as exc:
                metric_rows.append(
                    {
                        **common,
                        "MAE": np.nan,
                        "RMSE": np.nan,
                        "MAPE": np.nan,
                        "sMAPE": np.nan,
                        "MASE": np.nan,
                        "bias": np.nan,
                        "runtime_seconds": np.nan,
                        "best_epoch": np.nan,
                        "best_internal_validation_loss": np.nan,
                        "warnings": "",
                        "status": "failed",
                        "failure_reason": f"{type(exc).__name__}: {exc}",
                    }
                )
    return pd.DataFrame(prediction_rows), pd.DataFrame(metric_rows)


def summarize_deep_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    """Summarize deep accuracy, fold stability, seed variability, and failures."""

    rows: List[Dict[str, object]] = []
    group_columns = ["target", "model", "feature_mode", "stage", "seed"]
    for keys, group in metrics.groupby(group_columns, dropna=False, sort=True):
        target, model, feature_mode, stage, seed = keys
        ok = group.loc[group["status"].eq("ok")]
        runtime = (
            pd.to_numeric(ok["runtime_seconds"], errors="coerce")
            if "runtime_seconds" in ok
            else pd.Series(dtype=float)
        )
        best_epochs = (
            pd.to_numeric(ok["best_epoch"], errors="coerce")
            if "best_epoch" in ok
            else pd.Series(dtype=float)
        )
        reasons = sorted(set(group.loc[group["status"].ne("ok"), "failure_reason"].dropna()))
        rows.append(
            {
                "target": target,
                "model": model,
                "feature_mode": feature_mode,
                "model_family": "deep_learning",
                "protocol": PRIMARY_PROTOCOL,
                "summary_stage": stage,
                "seed": seed,
                "successful_folds": int(len(ok)),
                "failed_folds": int(len(group) - len(ok)),
                "mean_MAE": ok["MAE"].mean(),
                "median_MAE": ok["MAE"].median(),
                "std_MAE": ok["MAE"].std(ddof=1),
                "min_MAE": ok["MAE"].min(),
                "max_MAE": ok["MAE"].max(),
                "mean_RMSE": ok["RMSE"].mean(),
                "mean_sMAPE": ok["sMAPE"].mean(),
                "mean_MASE": ok["MASE"].mean(),
                "mean_bias": ok["bias"].mean(),
                "mean_runtime_seconds": runtime.mean(),
                "total_runtime_seconds": runtime.sum(min_count=1),
                "mean_best_epoch": best_epochs.mean(),
                "hyperparameters": group["hyperparameters"].iloc[0],
                "failure_reasons": " | ".join(reasons),
            }
        )
    return pd.DataFrame(rows)


def choose_stage1_best_deep(stage1_summary: pd.DataFrame, required_folds: int) -> pd.DataFrame:
    """Select one architecture per target using only common-seed fold MAE."""

    eligible = stage1_summary.loc[
        stage1_summary["successful_folds"].eq(required_folds)
        & stage1_summary["mean_MAE"].notna()
    ].copy()
    rows = []
    for target in TARGETS:
        candidates = eligible.loc[eligible["target"].eq(target)].sort_values(
            ["mean_MAE", "mean_MASE", "mean_RMSE", "std_MAE", "model"]
        )
        if candidates.empty:
            rows.append(
                {
                    "target": target,
                    "best_stage1_deep_model": "none",
                    "feature_mode": "none",
                    "stage1_mean_MAE": np.nan,
                    "status": "failed",
                    "failure_reason": "No deep model completed all seven primary folds.",
                }
            )
        else:
            best = candidates.iloc[0]
            rows.append(
                {
                    "target": target,
                    "best_stage1_deep_model": best["model"],
                    "feature_mode": best["feature_mode"],
                    "stage1_mean_MAE": best["mean_MAE"],
                    "status": "ok",
                    "failure_reason": "",
                }
            )
    return pd.DataFrame(rows)


def build_seed_ensemble(
    stage2_predictions: pd.DataFrame,
    development_df: pd.DataFrame,
    config: DeepEvaluationConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Average the three prespecified seeds and calculate fold-level metrics."""

    expected_seeds = set(config.stage2_seeds)
    prediction_rows: List[Dict[str, object]] = []
    metric_rows: List[Dict[str, object]] = []
    seed_stability_rows: List[Dict[str, object]] = []

    seed_metric = (
        stage2_predictions.groupby(["target", "model", "seed", "fold_id"], as_index=False)
        .agg(MAE=("absolute_error", "mean"))
    )
    for (target, model), group in seed_metric.groupby(["target", "model"]):
        per_seed = group.groupby("seed")["MAE"].mean()
        seed_stability_rows.append(
            {
                "target": target,
                "base_deep_model": model,
                "seeds": ",".join(str(seed) for seed in sorted(expected_seeds)),
                "seed_count": int(per_seed.index.nunique()),
                "mean_seed_validation_MAE": per_seed.mean(),
                "seed_validation_MAE_standard_deviation": per_seed.std(ddof=1),
                "minimum_seed_validation_MAE": per_seed.min(),
                "maximum_seed_validation_MAE": per_seed.max(),
            }
        )

    keys = ["target", "model", "feature_mode", "fold_id", "forecast_origin", "forecast_date", "horizon_step"]
    for key_values, group in stage2_predictions.groupby(keys, sort=True):
        target, model, feature_mode, fold_id, origin, forecast_date, horizon_step = key_values
        if set(group["seed"].astype(int)) != expected_seeds:
            continue
        prediction = float(group["prediction"].mean())
        actual = float(group["actual"].iloc[0])
        error = actual - prediction
        prediction_rows.append(
            {
                "target": target,
                "model": f"{model}_seed_ensemble",
                "base_deep_model": model,
                "feature_mode": feature_mode,
                "model_family": "deep_learning",
                "protocol": PRIMARY_PROTOCOL,
                "fold_id": fold_id,
                "stage": "stage2_seed_ensemble",
                "seed": "ensemble_17_42_73",
                "forecast_origin": origin,
                "forecast_date": forecast_date,
                "horizon_step": horizon_step,
                "actual": actual,
                "prediction": prediction,
                "error": error,
                "absolute_error": abs(error),
                "squared_error": error**2,
                "evaluation_split": "development_validation",
            }
        )

    ensemble_predictions = pd.DataFrame(prediction_rows)
    for (target, model, feature_mode, fold_id), group in ensemble_predictions.groupby(
        ["target", "model", "feature_mode", "fold_id"], sort=True
    ):
        origin = pd.Timestamp(group["forecast_origin"].iloc[0])
        training = development_df.loc[development_df.index <= origin, target]
        metrics = calculate_metrics(group["actual"], group["prediction"], training)
        metric_rows.append(
            {
                "target": target,
                "model": model,
                "feature_mode": feature_mode,
                "model_family": "deep_learning",
                "protocol": PRIMARY_PROTOCOL,
                "fold_id": fold_id,
                "stage": "stage2_seed_ensemble",
                "seed": "ensemble_17_42_73",
                **metrics,
                "hyperparameters": json.dumps(
                    {
                        "base_model": group["base_deep_model"].iloc[0],
                        "seeds": list(config.stage2_seeds),
                        "aggregation": "arithmetic_mean",
                    },
                    sort_keys=True,
                ),
                "status": "ok",
                "failure_reason": "",
            }
        )
    return ensemble_predictions, pd.DataFrame(metric_rows), pd.DataFrame(seed_stability_rows)


def _deep_summary_for_selection(
    ensemble_metrics: pd.DataFrame,
    deterministic_fold_metrics: pd.DataFrame,
) -> pd.DataFrame:
    """Recalculate ranks and skills with only stable deep ensembles added."""

    required_columns = deterministic_fold_metrics.columns
    deep = ensemble_metrics.copy()
    for column in required_columns:
        if column not in deep:
            deep[column] = np.nan
    combined_metrics = pd.concat(
        [deterministic_fold_metrics, deep[required_columns]],
        ignore_index=True,
    )
    return summarize_rolling_origin_metrics(combined_metrics)


def build_deep_vs_classical_table(
    deep_ensemble_summary: pd.DataFrame,
    seed_stability: pd.DataFrame,
    deterministic_selection: pd.DataFrame,
    final_selection: pd.DataFrame,
) -> pd.DataFrame:
    """Compare each stable deep candidate with the locked non-deep candidate."""

    rows = []
    for target in TARGETS:
        deep = deep_ensemble_summary.loc[deep_ensemble_summary["target"].eq(target)].iloc[0]
        classical = deterministic_selection.loc[deterministic_selection["target"].eq(target)].iloc[0]
        stable = seed_stability.loc[seed_stability["target"].eq(target)].iloc[0]
        selected = final_selection.loc[final_selection["target"].eq(target)].iloc[0]
        difference = float(deep["mean_MAE"] - classical["primary_validation_MAE"])
        percent = 100 * difference / float(classical["primary_validation_MAE"])
        deep_skill = 1 - float(deep["mean_MAE"]) / float(classical["seasonal_naive_MAE"])
        rows.append(
            {
                "target": target,
                "best_deep_model": deep["model"],
                "best_deep_feature_mode": deep["feature_mode"],
                "best_deep_validation_MAE": deep["mean_MAE"],
                "best_deep_validation_MASE": deep["mean_MASE"],
                "best_deep_fold_MAE_standard_deviation": deep["std_MAE"],
                "seed_validation_MAE_standard_deviation": stable[
                    "seed_validation_MAE_standard_deviation"
                ],
                "best_non_deep_model": classical["selected_model"],
                "best_non_deep_feature_mode": classical["feature_mode"],
                "best_non_deep_validation_MAE": classical["primary_validation_MAE"],
                "seasonal_naive_validation_MAE": classical["seasonal_naive_MAE"],
                "deep_skill_relative_to_seasonal_naive": deep_skill,
                "deep_minus_non_deep_MAE": difference,
                "deep_minus_non_deep_percent": percent,
                "overall_validation_winner": selected["selected_model"],
                "did_deep_win": str(selected["selected_model"]).endswith("_seed_ensemble"),
                "practically_meaningful_difference": abs(percent) > 1.0,
                "plain_english_result": (
                    "The three-seed deep ensemble won after the common validation and tie rules."
                    if str(selected["selected_model"]).endswith("_seed_ensemble")
                    else "The best validated classical or standard-ML model remained preferable."
                ),
            }
        )
    return pd.DataFrame(rows)


def _spec_lookup(registry: Sequence[DeepModelSpec]) -> Dict[str, DeepModelSpec]:
    """Map deep model labels to their fixed specifications."""

    return {spec.model: spec for spec in registry}


def forecast_deep_seed_ensemble(
    base_model: str,
    target: str,
    target_df: pd.DataFrame,
    training_dates: pd.DatetimeIndex,
    forecast_dates: pd.DatetimeIndex,
    config: DeepEvaluationConfig,
    registry: Optional[Sequence[DeepModelSpec]] = None,
) -> Tuple[np.ndarray, pd.DataFrame]:
    """Forecast one target by averaging the three prespecified deep seeds."""

    registry = list(registry or build_deep_model_registry(config))
    spec = _spec_lookup(registry)[base_model]
    predictions = []
    diagnostics = []
    for seed in config.stage2_seeds:
        values, info = fit_predict_deep_fold(
            spec,
            target,
            target_df,
            training_dates,
            forecast_dates,
            seed,
            config,
        )
        predictions.append(values)
        diagnostics.append({"target": target, "base_deep_model": base_model, **info})
    return np.mean(np.vstack(predictions), axis=0), pd.DataFrame(diagnostics)


def evaluate_locked_selection_with_deep(
    target_df: pd.DataFrame,
    development_df: pd.DataFrame,
    final_test_df: pd.DataFrame,
    final_selection: pd.DataFrame,
    stage1_best: pd.DataFrame,
    config: DeepEvaluationConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate the validation-locked overall winners on the untouched test once."""

    old_predictions = pd.read_csv(config.output_dir / "final_test_predictions.csv", parse_dates=["forecast_date"])
    old_fixed = old_predictions.loc[old_predictions["protocol"].eq(PRIMARY_PROTOCOL)].copy()
    prediction_rows: List[Dict[str, object]] = []
    metric_rows: List[Dict[str, object]] = []
    diagnostic_rows: List[Dict[str, object]] = []
    registry = build_deep_model_registry(config)

    for target in TARGETS:
        selected = final_selection.loc[final_selection["target"].eq(target)].iloc[0]
        selected_model = str(selected["selected_model"])
        if selected_model.endswith("_seed_ensemble"):
            base_model = stage1_best.loc[stage1_best["target"].eq(target), "best_stage1_deep_model"].iloc[0]
            predictions, diagnostics = forecast_deep_seed_ensemble(
                base_model,
                target,
                target_df,
                development_df.index,
                final_test_df.index,
                config,
                registry,
            )
            diagnostic_rows.extend(diagnostics.to_dict("records"))
        else:
            previous = old_fixed.loc[old_fixed["target"].eq(target)].sort_values("forecast_date")
            if previous.empty or previous["selected_model"].iloc[0] != selected_model:
                raise AssertionError(
                    "A non-deep model changed after deep comparison; deterministic test predictions cannot be reused."
                )
            predictions = previous["prediction"].to_numpy(dtype=float)

        actual = final_test_df[target]
        prediction_series = pd.Series(predictions, index=final_test_df.index)
        metrics = calculate_metrics(actual, prediction_series, development_df[target])
        baseline = seasonal_naive_forecast(development_df[target], len(final_test_df))
        baseline_metrics = calculate_metrics(actual, baseline, development_df[target])
        metric_rows.append(
            {
                "target": target,
                "selected_model": selected_model,
                "feature_mode": selected["feature_mode"],
                "model_family": "deep_learning" if selected_model.endswith("_seed_ensemble") else "non_deep",
                "protocol": PRIMARY_PROTOCOL,
                **metrics,
                "seasonal_naive_MAE": baseline_metrics["MAE"],
                "seasonal_naive_skill": 1 - metrics["MAE"] / baseline_metrics["MAE"],
                "selection_was_locked_before_test": True,
            }
        )
        for step, forecast_date in enumerate(final_test_df.index, start=1):
            prediction = float(prediction_series.loc[forecast_date])
            actual_value = float(actual.loc[forecast_date])
            error = actual_value - prediction
            prediction_rows.append(
                {
                    "target": target,
                    "selected_model": selected_model,
                    "feature_mode": selected["feature_mode"],
                    "model_family": "deep_learning" if selected_model.endswith("_seed_ensemble") else "non_deep",
                    "protocol": PRIMARY_PROTOCOL,
                    "forecast_origin": development_df.index.max(),
                    "forecast_date": forecast_date,
                    "horizon_step": step,
                    "actual": actual_value,
                    "prediction": prediction,
                    "error": error,
                    "absolute_error": abs(error),
                    "selection_was_locked_before_test": True,
                }
            )
    diagnostics = pd.DataFrame(diagnostic_rows)
    if diagnostics.empty:
        diagnostics = pd.DataFrame(
            columns=[
                "target",
                "base_deep_model",
                "seed",
                "runtime_seconds",
                "best_epoch",
                "best_internal_validation_loss",
                "target_scaler_fit_start",
                "target_scaler_fit_end",
                "calendar_scaler_fit_start",
                "calendar_scaler_fit_end",
                "deep_fit_start",
                "deep_fit_end",
                "internal_validation_start",
                "internal_validation_end",
                "warnings",
            ]
        )
    return pd.DataFrame(prediction_rows), pd.DataFrame(metric_rows), diagnostics


def build_final_selection_with_deep(
    final_selection: pd.DataFrame,
    combined_summary: pd.DataFrame,
    final_test_metrics: pd.DataFrame,
) -> pd.DataFrame:
    """Create the canonical point-model table with validation and test separated."""

    primary = combined_summary.loc[combined_summary["protocol"].eq(PRIMARY_PROTOCOL)]
    family_lookup = primary.set_index(["target", "model"])["model_family"].to_dict()
    result = final_selection.copy()
    result["model_family"] = [
        family_lookup.get((row.target, row.selected_model), "unknown")
        for row in result.itertuples()
    ]
    result["deep_or_non_deep"] = np.where(
        result["model_family"].eq("deep_learning"), "deep", "non_deep"
    )
    test = final_test_metrics.rename(
        columns={
            "MAE": "final_locked_test_MAE",
            "RMSE": "final_locked_test_RMSE",
            "MAPE": "final_locked_test_MAPE",
            "sMAPE": "final_locked_test_sMAPE",
            "MASE": "final_locked_test_MASE",
            "bias": "final_locked_test_bias",
        }
    )
    result = result.merge(
        test[
            [
                "target",
                "final_locked_test_MAE",
                "final_locked_test_RMSE",
                "final_locked_test_MAPE",
                "final_locked_test_sMAPE",
                "final_locked_test_MASE",
                "final_locked_test_bias",
                "selection_was_locked_before_test",
            ]
        ],
        on="target",
        how="left",
        validate="one_to_one",
    )
    result["selection_basis"] = (
        "Mean fixed-origin nine-month development-validation MAE, 1% practical-tie simplicity rule, "
        "and seasonal-naive skill guard; final test not used for selection."
    )
    return result


def run_deep_evaluation(config: Optional[DeepEvaluationConfig] = None) -> Dict[str, pd.DataFrame]:
    """Run both deep stages, combine validation selection, and test locked winners."""

    config = config or DeepEvaluationConfig()
    if not DARTS_AVAILABLE:
        raise RuntimeError(f"Cannot run deep evaluation: {DARTS_IMPORT_ERROR}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    logging.getLogger("pytorch_lightning").setLevel(logging.ERROR)

    target_df = load_nhs_targets(config.deterministic_config)
    development_df, final_test_df = split_development_and_final_test(
        target_df, config.deterministic_config
    )
    folds = generate_rolling_origin_folds(
        development_df.index,
        protocol=PRIMARY_PROTOCOL,
        minimum_training_observations=config.deterministic_config.minimum_training_observations,
        validation_horizon=config.output_chunk_length,
        step=config.deterministic_config.fold_step,
    )
    registry = build_deep_model_registry(config)

    all_predictions: List[pd.DataFrame] = []
    all_metrics: List[pd.DataFrame] = []
    for spec in registry:
        predictions, metrics = evaluate_deep_model_on_folds(
            spec,
            TARGETS,
            target_df,
            development_df,
            folds,
            config.stage1_seed,
            "stage1_screen",
            config,
        )
        all_predictions.append(predictions)
        all_metrics.append(metrics)

    stage1_predictions = pd.concat(all_predictions, ignore_index=True)
    stage1_metrics = pd.concat(all_metrics, ignore_index=True)
    stage1_summary = summarize_deep_metrics(stage1_metrics)
    stage1_best = choose_stage1_best_deep(stage1_summary, len(folds))

    stage2_predictions_parts: List[pd.DataFrame] = []
    stage2_metrics_parts: List[pd.DataFrame] = []
    for target in TARGETS:
        best_row = stage1_best.loc[stage1_best["target"].eq(target)].iloc[0]
        if best_row["status"] != "ok":
            continue
        spec = _spec_lookup(registry)[best_row["best_stage1_deep_model"]]
        seed42 = stage1_predictions.loc[
            stage1_predictions["target"].eq(target)
            & stage1_predictions["model"].eq(spec.model)
        ].copy()
        seed42["stage"] = "stage2_stability"
        metric42 = stage1_metrics.loc[
            stage1_metrics["target"].eq(target)
            & stage1_metrics["model"].eq(spec.model)
        ].copy()
        metric42["stage"] = "stage2_stability"
        stage2_predictions_parts.append(seed42)
        stage2_metrics_parts.append(metric42)
        for seed in config.stage2_seeds:
            if seed == config.stage1_seed:
                continue
            predictions, metrics = evaluate_deep_model_on_folds(
                spec,
                [target],
                target_df,
                development_df,
                folds,
                seed,
                "stage2_stability",
                config,
            )
            stage2_predictions_parts.append(predictions)
            stage2_metrics_parts.append(metrics)

    stage2_predictions = pd.concat(stage2_predictions_parts, ignore_index=True)
    stage2_metrics = pd.concat(stage2_metrics_parts, ignore_index=True)
    ensemble_predictions, ensemble_metrics, seed_stability = build_seed_ensemble(
        stage2_predictions,
        development_df,
        config,
    )
    stage2_summary = summarize_deep_metrics(stage2_metrics)
    ensemble_summary_raw = summarize_deep_metrics(ensemble_metrics)

    deterministic_fold_metrics = pd.read_csv(config.output_dir / "rolling_origin_fold_metrics.csv")
    combined_summary = _deep_summary_for_selection(ensemble_metrics, deterministic_fold_metrics)
    final_selection = select_validation_models(
        combined_summary,
        total_primary_folds=len(folds),
        practical_tie_fraction=config.practical_tie_fraction,
        minimum_complex_model_skill=config.minimum_complex_model_skill,
    )
    deterministic_selection = pd.read_csv(config.output_dir / "validation_model_selection.csv")
    deep_ensemble_summary = combined_summary.loc[
        combined_summary["model_family"].eq("deep_learning")
        & combined_summary["model"].str.endswith("_seed_ensemble")
    ].copy()
    deep_vs_classical = build_deep_vs_classical_table(
        deep_ensemble_summary,
        seed_stability,
        deterministic_selection,
        final_selection,
    )

    final_test_predictions, final_test_metrics, final_deep_diagnostics = (
        evaluate_locked_selection_with_deep(
            target_df,
            development_df,
            final_test_df,
            final_selection,
            stage1_best,
            config,
        )
    )
    final_model_selection = build_final_selection_with_deep(
        final_selection,
        combined_summary,
        final_test_metrics,
    )

    all_deep_predictions = pd.concat(
        [stage1_predictions, stage2_predictions, ensemble_predictions],
        ignore_index=True,
        sort=False,
    )
    all_deep_metrics = pd.concat(
        [stage1_metrics, stage2_metrics, ensemble_metrics],
        ignore_index=True,
        sort=False,
    )
    all_deep_summary = pd.concat(
        [stage1_summary, stage2_summary, ensemble_summary_raw],
        ignore_index=True,
        sort=False,
    )

    paths = {
        "deep_predictions": config.output_dir / "deep_fixed_origin_predictions.csv",
        "deep_metrics": config.output_dir / "deep_fold_metrics.csv",
        "deep_summary": config.output_dir / "deep_model_summary.csv",
        "deep_vs_classical": config.output_dir / "deep_vs_classical_final.csv",
        "stage1_best": config.output_dir / "deep_stage1_best_by_target.csv",
        "seed_stability": config.output_dir / "deep_seed_stability.csv",
        "combined_summary": config.output_dir / "model_summary_with_deep.csv",
        "final_selection": config.output_dir / "final_model_selection_with_deep.csv",
        "final_predictions": config.output_dir / "final_test_predictions_with_deep.csv",
        "final_metrics": config.output_dir / "final_test_metrics_with_deep.csv",
        "final_deep_diagnostics": config.output_dir / "final_deep_test_diagnostics.csv",
    }
    frames = {
        "deep_predictions": all_deep_predictions,
        "deep_metrics": all_deep_metrics,
        "deep_summary": all_deep_summary,
        "deep_vs_classical": deep_vs_classical,
        "stage1_best": stage1_best,
        "seed_stability": seed_stability,
        "combined_summary": combined_summary,
        "final_selection": final_model_selection,
        "final_predictions": final_test_predictions,
        "final_metrics": final_test_metrics,
        "final_deep_diagnostics": final_deep_diagnostics,
    }
    for key, path in paths.items():
        frames[key].to_csv(path, index=False)
    return frames


def load_deep_outputs(output_dir: Path = Path("outputs/final_evaluation")) -> Dict[str, pd.DataFrame]:
    """Load cached deep and combined-selection artifacts for presentation mode."""

    filenames = {
        "deep_predictions": "deep_fixed_origin_predictions.csv",
        "deep_metrics": "deep_fold_metrics.csv",
        "deep_summary": "deep_model_summary.csv",
        "deep_vs_classical": "deep_vs_classical_final.csv",
        "stage1_best": "deep_stage1_best_by_target.csv",
        "seed_stability": "deep_seed_stability.csv",
        "combined_summary": "model_summary_with_deep.csv",
        "final_selection": "final_model_selection_with_deep.csv",
        "final_predictions": "final_test_predictions_with_deep.csv",
        "final_metrics": "final_test_metrics_with_deep.csv",
        "final_deep_diagnostics": "final_deep_test_diagnostics.csv",
    }
    missing = [name for name in filenames.values() if not (output_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing cached deep outputs: {missing}")
    loaded = {}
    for key, name in filenames.items():
        try:
            loaded[key] = pd.read_csv(output_dir / name)
        except pd.errors.EmptyDataError:
            loaded[key] = pd.DataFrame()
    return loaded


def main() -> None:
    """Run the complete required deep benchmark from the command line."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/final_evaluation"))
    args = parser.parse_args()
    config = DeepEvaluationConfig(output_dir=args.output_dir, max_epochs=args.epochs)
    print("Torch version:", torch.__version__ if torch is not None else "unavailable")
    print("CUDA available:", bool(torch is not None and torch.cuda.is_available()))
    if torch is not None and torch.cuda.is_available():
        print("GPU:", torch.cuda.get_device_name(0))
    started = time.perf_counter()
    outputs = run_deep_evaluation(config)
    print(outputs["deep_vs_classical"].to_string(index=False))
    print(f"Deep evaluation completed in {time.perf_counter() - started:.1f} seconds.")


if __name__ == "__main__":
    main()
