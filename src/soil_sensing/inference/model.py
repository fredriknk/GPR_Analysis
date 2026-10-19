"""Constrained fraction baselines with isolated preprocessing and calibration."""

import hashlib
import math
from dataclasses import asdict, dataclass

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from soil_sensing.inference.dataset import TARGETS, purged_training


@dataclass(frozen=True)
class ModelOptions:
    block_size_m: float
    buffer_m: float
    location_tolerance_m: float
    ridge_alphas: tuple = (0.1, 1.0, 10.0)
    inner_folds: int = 3
    interval_level: float = 0.9
    calibration_fraction: float = 0.3
    seed: int = 42
    minimum_training_groups: int = 3

    def __post_init__(self):
        for name in (
            "block_size_m",
            "buffer_m",
            "location_tolerance_m",
            "interval_level",
            "calibration_fraction",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not np.isfinite(value)
            ):
                raise ValueError(f"{name} must be finite and numeric")
        if self.block_size_m <= 0 or self.buffer_m < 0 or self.location_tolerance_m < 0:
            raise ValueError("Invalid spatial grouping/buffer limits")
        if not 0 < self.interval_level < 1 or not 0 < self.calibration_fraction < 0.5:
            raise ValueError("Invalid interval level or calibration fraction")
        for name in ("inner_folds", "minimum_training_groups", "seed"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if self.inner_folds < 2 or self.minimum_training_groups < 2 or self.seed < 0:
            raise ValueError(
                "At least two independent groups/folds and a nonnegative seed are required"
            )
        if not self.ridge_alphas or any(
            isinstance(a, bool) or not isinstance(a, (int, float)) or not np.isfinite(a) or a <= 0
            for a in self.ridge_alphas
        ):
            raise ValueError("ridge_alphas must contain positive finite values")


def project_composition(values):
    """Euclidean projection onto nonnegative fractions summing to exactly one.

    Targets are never renormalized. The model's output constraint is explicit and
    supports measured zeros without introducing a log-ratio pseudocount.
    """
    values = np.asarray(values, dtype=float).reshape(-1, 3)
    ordered = np.sort(values, axis=1)[:, ::-1]
    cumulative = ordered.cumsum(axis=1) - 1
    rank = np.sum(ordered - cumulative / np.arange(1, 4) > 0, axis=1) - 1
    threshold = cumulative[np.arange(len(values)), rank] / (rank + 1)
    return np.maximum(values - threshold[:, None], 0)


def _weights(groups):
    _, inverse, counts = np.unique(groups, return_inverse=True, return_counts=True)
    return 1 / counts[inverse]


def fit_estimator(dataset, ids, alpha):
    """Fit every feature selection/imputation/scaling step on these IDs only."""
    ids = np.asarray(ids, dtype=int)
    x, y, groups = dataset.x[ids], dataset.y[ids], dataset.groups[ids]
    active = np.isfinite(x).any(axis=0)
    if not active.any():
        raise ValueError("Training fold has no measured selected feature")
    pipeline = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=alpha))
    pipeline.fit(x[:, active], y, ridge__sample_weight=_weights(groups))
    imputer, scaler, ridge = pipeline.steps[0][1], pipeline.steps[1][1], pipeline.steps[2][1]
    model = {
        "schema_version": "0.1",
        "algorithm": "median_scale_ridge_simplex_v1",
        "target_names": list(TARGETS),
        "unit": "fraction",
        "alpha": float(alpha),
        "feature_names": dataset.feature_names,
        "feature_definitions": dataset.definitions,
        "active_features": active.tolist(),
        "imputer_medians": imputer.statistics_.tolist(),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coefficients": ridge.coef_.tolist(),
        "intercept": ridge.intercept_.tolist(),
        "domain_min": np.nanmin(x[:, active], axis=0).tolist(),
        "domain_max": np.nanmax(x[:, active], axis=0).tolist(),
        "mean_baseline": project_composition(np.average(y, axis=0, weights=_weights(groups)))[
            0
        ].tolist(),
        "proper_training_ids": [ids_to_key(dataset, i) for i in ids],
        "proper_training_groups": sorted(set(groups)),
        "target_definition": {
            k: dataset.rows[ids[0]][k]
            for k in ("method", "sand_silt_cutoff_um", "silt_clay_cutoff_um")
        },
        "interval": {"radius_fraction": None, "status": "not_calibrated"},
    }
    return model


def validate_model(model):
    if (
        model.get("schema_version") != "0.1"
        or model.get("algorithm") != "median_scale_ridge_simplex_v1"
    ):
        raise ValueError("Unsupported texture model schema/algorithm")
    names = model.get("feature_names", [])
    active = np.asarray(model.get("active_features", []))
    if (
        not names
        or len(names) != len(set(names))
        or active.shape != (len(names),)
        or active.dtype != bool
    ):
        raise ValueError("Invalid model feature definition")
    n = int(active.sum())
    shapes = {
        "imputer_medians": (n,),
        "scaler_mean": (n,),
        "scaler_scale": (n,),
        "coefficients": (3, n),
        "intercept": (3,),
        "domain_min": (n,),
        "domain_max": (n,),
        "mean_baseline": (3,),
    }
    for key, shape in shapes.items():
        value = np.asarray(model.get(key, []), dtype=float)
        if value.shape != shape or not np.isfinite(value).all():
            raise ValueError(f"Invalid model numerical field: {key}")
    if not n or np.any(np.array(model["scaler_scale"]) <= 0):
        raise ValueError("Invalid model preprocessing scale")
    if np.any(np.array(model["domain_min"]) > model["domain_max"]):
        raise ValueError("Invalid model training domain")
    if model.get("unit") != "fraction" or model.get("target_names") != list(TARGETS):
        raise ValueError("Invalid model target definition")
    interval = model.get("interval", {})
    radius = interval.get("radius_fraction")
    if radius is not None and (
        not isinstance(radius, (int, float)) or not np.isfinite(radius) or radius < 0
    ):
        raise ValueError("Invalid model interval calibration")


def predict_estimator(model, x):
    validate_model(model)
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(model["feature_names"]) or np.isinf(x).any():
        raise ValueError("Prediction feature shape/value mismatch")
    selected = x[:, np.asarray(model["active_features"])]
    observed = np.isfinite(selected)
    outside = observed & ((selected < model["domain_min"]) | (selected > model["domain_max"]))
    valid = observed.any(axis=1) & ~outside.any(axis=1)
    imputed = np.where(observed, selected, model["imputer_medians"])
    standardized = (imputed - model["scaler_mean"]) / model["scaler_scale"]
    values = project_composition(
        standardized @ np.asarray(model["coefficients"]).T + model["intercept"]
    )
    values[~valid] = np.nan
    excursion = (
        np.maximum(
            np.maximum(np.asarray(model["domain_min"]) - imputed, imputed - model["domain_max"]), 0
        )
        / model["scaler_scale"]
    )
    return values, valid, np.linalg.norm(excursion, axis=1), ~observed.all(axis=1)


def ids_to_key(dataset, i):
    row = dataset.rows[i]
    return [row["survey_id"], row["sample_id"]]


def _calibration_partition(dataset, ids, options):
    groups = sorted(
        set(dataset.groups[ids]),
        key=lambda g: hashlib.sha256(f"{options.seed}:{g}".encode()).hexdigest(),
    )
    count = max(1, int(np.ceil(len(groups) * options.calibration_fraction)))
    calibration = np.array([i for i in ids if dataset.groups[i] in groups[:count]], dtype=int)
    proper = purged_training(dataset, ids, calibration, options.buffer_m)
    if len(set(dataset.groups[proper])) < options.minimum_training_groups:
        raise ValueError(
            "Too few independent proper-training groups after calibration/buffer exclusions"
        )
    return proper, calibration


def fit_calibrated_model(dataset, ids, options):
    """Calibration groups are held aside before inner tuning and preprocessing."""
    ids = np.asarray(ids, dtype=int)
    proper, calibration = _calibration_partition(dataset, ids, options)
    groups = dataset.groups[proper]
    splits = min(options.inner_folds, len(set(groups)))
    tuning = []
    for alpha in options.ridge_alphas:
        errors = []
        for train_relative, test_relative in GroupKFold(splits).split(proper, groups=groups):
            test = proper[test_relative]
            train = purged_training(dataset, proper[train_relative], test, options.buffer_m)
            if len(set(dataset.groups[train])) < 2:
                continue
            model = fit_estimator(dataset, train, alpha)
            predicted, valid, _, _ = predict_estimator(model, dataset.x[test])
            for group in set(dataset.groups[test][valid]):
                chosen = valid & (dataset.groups[test] == group)
                errors.append(float(np.mean((predicted[chosen] - dataset.y[test][chosen]) ** 2)))
        tuning.append(
            {
                "alpha": float(alpha),
                "group_mse": float(np.mean(errors)) if errors else None,
                "scored_groups": len(errors),
            }
        )
    scored = [r for r in tuning if r["group_mse"] is not None]
    if not scored:
        raise ValueError(
            "No supported inner spatial-validation groups; revise study support or collect data"
        )
    selected = min(scored, key=lambda r: (r["group_mse"], r["alpha"]))["alpha"]
    model = fit_estimator(dataset, proper, selected)
    predictions, valid, _, _ = predict_estimator(model, dataset.x[calibration])
    scores = []
    for group in set(dataset.groups[calibration][valid]):
        chosen = valid & (dataset.groups[calibration] == group)
        scores.append(float(np.max(np.abs(predictions[chosen] - dataset.y[calibration][chosen]))))
    rank = math.ceil((len(scores) + 1) * options.interval_level)
    radius = float(sorted(scores)[rank - 1]) if scores and rank <= len(scores) else None
    model.update(
        options=asdict(options),
        tuning=tuning,
        calibration_ids=[ids_to_key(dataset, i) for i in calibration],
        calibration_groups=sorted(set(dataset.groups[calibration])),
    )
    model["interval"] = {
        "method": "group_split_conformal_max_component_residual",
        "level": options.interval_level,
        "radius_fraction": radius,
        "supported_calibration_groups": len(scores),
        "calibration_rank": rank,
        "status": "calibrated" if radius is not None else "insufficient_calibration_groups",
        "limitation": (
            "Nominal coverage requires exchangeable independent groups; "
            "spatial/campaign shift can invalidate it"
        ),
    }
    return model


def evaluate_model(dataset, options):
    plots = sorted({r["plot_id"] for r in dataset.rows})
    ids = np.arange(len(dataset.rows))
    if len(plots) < 2:
        raise ValueError(
            "Transfer evaluation requires at least two independent plots; "
            "no accuracy claim is emitted"
        )
    predictions, folds = [], []
    for plot in plots:
        test = ids[[r["plot_id"] == plot for r in dataset.rows]]
        train = purged_training(dataset, ids, test, options.buffer_m)
        fold = {
            "plot_id": plot,
            "held_out_count": len(test),
            "training_count": len(train),
            "held_out_groups": len(set(dataset.groups[test])),
            "status": "insufficient_support",
        }
        try:
            model = fit_calibrated_model(dataset, train, options)
        except ValueError as exc:
            fold["reason"] = str(exc)
            folds.append(fold)
            continue
        estimated, valid, distance, missing = predict_estimator(model, dataset.x[test])
        fold.update(
            status="evaluated",
            supported_count=int(valid.sum()),
            supported_fraction=float(valid.mean()),
            alpha=model["alpha"],
            proper_training_groups=model["proper_training_groups"],
            calibration_groups=model["calibration_groups"],
            held_out_group_ids=sorted(set(dataset.groups[test])),
            interval=model["interval"],
        )
        metrics = {}
        for name, values in (
            ("ridge_simplex", estimated),
            ("training_group_mean", np.tile(model["mean_baseline"], (len(test), 1))),
        ):
            error = (values[valid] - dataset.y[test][valid]) * 100
            metrics[name] = {
                t: {
                    "mae": float(np.mean(np.abs(error[:, j]))) if len(error) else None,
                    "rmse": float(np.sqrt(np.mean(error[:, j] ** 2))) if len(error) else None,
                    "bias": float(np.mean(error[:, j])) if len(error) else None,
                }
                for j, t in enumerate(TARGETS)
            }
        fold["metrics_percentage_points"] = metrics
        q = model["interval"]["radius_fraction"]
        covered = (
            np.max(np.abs(estimated[valid] - dataset.y[test][valid]), axis=1) <= q
            if q is not None
            else None
        )
        group_coverage = []
        if covered is not None:
            for group in set(dataset.groups[test][valid]):
                group_coverage.append(bool(covered[dataset.groups[test][valid] == group].all()))
        fold["empirical_joint_interval_coverage"] = (
            float(np.mean(group_coverage)) if group_coverage else None
        )
        fold["supported_independent_groups"] = len(set(dataset.groups[test][valid]))
        for local, i in enumerate(test):
            row = dataset.rows[i]
            prediction = {
                k: row[k]
                for k in (
                    "plot_id",
                    "survey_id",
                    "sample_id",
                    "x_m",
                    "y_m",
                    "crs",
                    "depth_top_m",
                    "depth_bottom_m",
                    "acquired_at_utc",
                )
            }
            prediction.update(
                valid_support=bool(valid[local]),
                feature_domain_distance=float(distance[local]),
                missing_features=bool(missing[local]),
                spatial_group=dataset.groups[i],
                interval_level=options.interval_level,
                interval_method=model["interval"]["method"],
                unit="fraction",
            )
            for j, t in enumerate(TARGETS):
                value = float(estimated[local, j]) if valid[local] else None
                prediction[t] = value
                prediction[t + "_observed"] = float(dataset.y[i, j])
                prediction[t + "_lower"] = (
                    max(0, value - q) if value is not None and q is not None else None
                )
                prediction[t + "_upper"] = (
                    min(1, value + q) if value is not None and q is not None else None
                )
            predictions.append(prediction)
        folds.append(fold)
    return folds, predictions
