"""Evidence-gated training/prediction CLIs over audited GPR/laboratory inputs."""

import csv
import hashlib
import json
import tomllib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from pyproj import CRS

from soil_sensing.fusion.matching import match_texture_to_gpr
from soil_sensing.fusion.observations import read_manual_texture_csv
from soil_sensing.fusion.workflow import _csv
from soil_sensing.inference.dataset import TARGETS, prepare_dataset
from soil_sensing.inference.model import (
    ModelOptions,
    evaluate_model,
    fit_calibrated_model,
    predict_estimator,
    validate_model,
)
from soil_sensing.ingest import write_json
from soil_sensing.inventory import sha256_file
from soil_sensing.runs import begin_run, finish_run


def load_model_config(path):
    config = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    options = set(ModelOptions.__dataclass_fields__)
    allowed = {
        "schema_version",
        "feature_names",
        "radius_m",
        "max_lag_hours",
        "minimum_depth_overlap",
        "synthetic",
        "calibration_independence_evidence",
        *options,
    }
    required = {
        "schema_version",
        "feature_names",
        "radius_m",
        "max_lag_hours",
        "block_size_m",
        "buffer_m",
        "location_tolerance_m",
        "calibration_independence_evidence",
    }
    if config.get("schema_version") != "0.1" or set(config) - allowed or required - config.keys():
        raise ValueError("Invalid texture-model config schema or fields")
    if type(config.get("synthetic", False)) is not bool:
        raise ValueError("synthetic must be Boolean")
    evidence = config["calibration_independence_evidence"]
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("Independent velocity/feature calibration evidence is required")
    features = config["feature_names"]
    if (
        not isinstance(features, list)
        or not features
        or any(not isinstance(v, str) for v in features)
    ):
        raise ValueError("feature_names must be a nonempty list of quantitative gate names")
    for name in ("radius_m", "max_lag_hours", "minimum_depth_overlap"):
        value = config.get(name, 0.25)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError(f"{name} must be finite and numeric")
    model_options = ModelOptions(**{k: v for k, v in config.items() if k in options})
    return config, model_options


def train_run(texture, features, config_path, output):
    config, options = load_model_config(config_path)
    output, manifest = begin_run(
        "train_texture",
        output,
        [texture, features, config_path],
        configuration={**config, "model_options": asdict(options)},
    )
    try:
        observations = read_manual_texture_csv(Path(texture))
        with Path(features).open(newline="", encoding="utf-8") as stream:
            feature_rows = list(csv.DictReader(stream))
        rows, audit = match_texture_to_gpr(
            observations,
            feature_rows,
            radius_m=config["radius_m"],
            max_lag_hours=config["max_lag_hours"],
            minimum_depth_overlap=config.get("minimum_depth_overlap", 0.25),
        )
        dataset, exclusions = prepare_dataset(
            rows,
            config["feature_names"],
            block_size_m=options.block_size_m,
            location_tolerance_m=options.location_tolerance_m,
        )
        folds, predictions = evaluate_model(dataset, options)
        model = fit_calibrated_model(dataset, np.arange(len(dataset.rows)), options)
        model.update(
            synthetic=config.get("synthetic", False),
            source_hashes=manifest["inputs"],
            calibration_independence_evidence=config["calibration_independence_evidence"],
            crs=sorted({r["crs"] for r in dataset.rows}),
            depth_range_m=[
                min(r["depth_top_m"] for r in dataset.rows),
                max(r["depth_bottom_m"] for r in dataset.rows),
            ],
            acquisition_period_utc=[
                min(r["acquired_at_utc"] for r in dataset.rows),
                max(r["acquired_at_utc"] for r in dataset.rows),
            ],
        )
        model["model_id"] = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
        write_json(output / "model.json", model)
        write_json(
            output / "evaluation.json",
            {
                "schema_version": "0.1",
                "split": "leave_one_plot_out_with_shared_support_purging_and_buffer",
                "folds": folds,
                "sample_exclusions": exclusions,
                "independent_group_count": len(set(dataset.groups)),
                "sample_count": len(dataset.rows),
                "available_sensor_ablations": ["training_group_mean", "gpr_ridge_simplex"],
                "synthetic": config.get("synthetic", False),
                "limitation": (
                    "Descriptive spatial validation; "
                    "no field accuracy or cross-campaign validity is assumed"
                ),
            },
        )
        _csv(output / "matched_samples.csv", rows)
        _csv(output / "join_audit.csv", audit)
        if predictions:
            _csv(output / "held_out_predictions.csv", predictions)
        header = [
            "support_id",
            "plot_id",
            "survey_id",
            "x_m",
            "y_m",
            "crs",
            "acquired_at_utc",
            "depth_top_m",
            "depth_bottom_m",
        ]
        for f in dataset.feature_names:
            header.extend([f, f + "_unit", f + "_processing_signature", f + "_contributors"])
        with (output / "prediction_template.csv").open("x", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerow(header)
        manifest.update(
            model_id=model["model_id"],
            sample_count=len(dataset.rows),
            independent_groups=len(set(dataset.groups)),
            synthetic=config.get("synthetic", False),
            evaluated_plots=sum(f["status"] == "evaluated" for f in folds),
            interval_status=model["interval"]["status"],
        )
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return finish_run(output, manifest)


def prediction_table(model, rows):
    validate_model(model)
    parsed, matrix, flags = [], [], []
    seen = set()
    for row in rows:
        required = (
            "support_id",
            "plot_id",
            "survey_id",
            "x_m",
            "y_m",
            "crs",
            "acquired_at_utc",
            "depth_top_m",
            "depth_bottom_m",
        )
        if any(row.get(k) in (None, "") for k in required):
            raise ValueError(
                "Prediction rows require explicit identity, coordinates, UTC and depth support"
            )
        identity = (row["survey_id"], row["support_id"])
        if identity in seen:
            raise ValueError("Duplicate prediction support identity")
        seen.add(identity)
        values = {k: row[k] for k in required}
        for key in ("x_m", "y_m", "depth_top_m", "depth_bottom_m"):
            values[key] = float(row[key])
            if not np.isfinite(values[key]):
                raise ValueError("Prediction coordinates/depths must be finite")
        if not 0 <= values["depth_top_m"] < values["depth_bottom_m"]:
            raise ValueError("Invalid prediction depth interval")
        crs = CRS.from_user_input(row["crs"])
        if not crs.is_projected or any(
            a.unit_name.lower() not in ("metre", "meter") for a in crs.axis_info[:2]
        ):
            raise ValueError("Prediction CRS must be projected in metres")
        values["crs"] = crs.to_string()
        date = datetime.fromisoformat(row["acquired_at_utc"].replace("Z", "+00:00"))
        if date.tzinfo is None or date.utcoffset().total_seconds() != 0:
            raise ValueError("Prediction acquisition time must explicitly be UTC")
        feature_values = []
        for f in model["feature_names"]:
            value = row.get(f)
            if value in (None, ""):
                feature_values.append(np.nan)
                continue
            definition = model["feature_definitions"][f]
            if (
                row.get(f + "_unit") != definition["unit"]
                or row.get(f + "_processing_signature") != definition["processing_signature"]
            ):
                raise ValueError(
                    "Prediction feature unit or processing/calibration signature mismatch"
                )
            count = float(row.get(f + "_contributors", 0))
            value = float(value)
            if (
                not np.isfinite(value)
                or not np.isfinite(count)
                or count < 1
                or not count.is_integer()
            ):
                raise ValueError(
                    "Quantitative prediction features require finite values "
                    "and measured contributors"
                )
            feature_values.append(value)
        reasons = []
        if values["crs"] not in model["crs"]:
            reasons.append("outside_trained_crs")
        if (
            values["depth_top_m"] < model["depth_range_m"][0]
            or values["depth_bottom_m"] > model["depth_range_m"][1]
        ):
            reasons.append("outside_training_depth")
        period = [
            datetime.fromisoformat(v.replace("Z", "+00:00"))
            for v in model["acquisition_period_utc"]
        ]
        if not period[0] <= date <= period[1]:
            reasons.append("acquisition_period_shift")
        parsed.append(values)
        matrix.append(feature_values)
        flags.append(reasons)
    if not parsed:
        raise ValueError("No prediction supports supplied")
    predicted, valid, distance, missing = predict_estimator(model, matrix)
    result = []
    q = model["interval"]["radius_fraction"]
    for i, row in enumerate(parsed):
        # Campaign shifts are flagged; spatial/depth/feature extrapolation is masked.
        supported = bool(valid[i]) and not any(v.startswith("outside_") for v in flags[i])
        if not valid[i]:
            flags[i].append("outside_feature_domain_or_all_features_missing")
        row.update(
            valid_support=supported,
            unit="fraction",
            model_id=model["model_id"],
            synthetic=model.get("synthetic", False),
            quality_flags="|".join(flags[i]),
            feature_domain_distance=float(distance[i]),
            missing_features=bool(missing[i]),
            interval_level=model["interval"]["level"],
            interval_method=model["interval"]["method"],
            interval_status=model["interval"]["status"],
        )
        for j, t in enumerate(TARGETS):
            value = float(predicted[i, j]) if supported else None
            row[t] = value
            row[t + "_lower"] = max(0, value - q) if value is not None and q is not None else None
            row[t + "_upper"] = min(1, value + q) if value is not None and q is not None else None
        result.append(row)
    return result


def predict_run(model_path, input_csv, output):
    output, manifest = begin_run("predict_texture", output, [model_path, input_csv])
    try:
        model = json.loads(Path(model_path).read_text(encoding="utf-8"))
        with Path(input_csv).open(newline="", encoding="utf-8") as stream:
            rows = prediction_table(model, list(csv.DictReader(stream)))
        _csv(output / "texture_estimates.csv", rows)
        write_json(
            output / "model_reference.json",
            {
                "model_id": model["model_id"],
                "model_sha256": sha256_file(Path(model_path)),
                "target_definition": model["target_definition"],
                "interval": model["interval"],
                "processing_definitions": model["feature_definitions"],
                "synthetic": model.get("synthetic", False),
            },
        )
        manifest.update(
            model_id=model["model_id"],
            support_count=len(rows),
            valid_support_count=sum(r["valid_support"] for r in rows),
            synthetic=model.get("synthetic", False),
        )
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return finish_run(output, manifest)
