"""Audited, support-aware matching of manual texture assays to radar attributes."""

import json
import math
import statistics
from datetime import datetime

from soil_sensing.fusion.observations import Observation


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("Feature time must explicitly be UTC")
    return result


def match_texture_to_gpr(
    observations: list[Observation],
    features: list[dict],
    *,
    radius_m: float,
    max_lag_hours: float,
    minimum_depth_overlap: float = 0.25,
) -> tuple[list[dict], list[dict]]:
    """Aggregate nearby GPR attributes to independent sample supports.

    Returns one row per physical sample and an explicit contributor/exclusion audit.
    Uncalibrated time windows are excluded because they lack depth support.
    """
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("radius_m must be positive and finite")
    if not math.isfinite(max_lag_hours) or max_lag_hours < 0:
        raise ValueError("max_lag_hours must be nonnegative and finite")
    if not 0 < minimum_depth_overlap <= 1:
        raise ValueError("minimum_depth_overlap must be in (0, 1]")
    groups: dict[tuple[str, str], list[Observation]] = {}
    for item in observations:
        if item.sensor_type != "manual_texture":
            raise ValueError("Only manual texture observations are supported as targets")
        groups.setdefault((item.survey_id, item.sample_id), []).append(item)
    if not groups:
        raise ValueError("No texture observations")
    definitions = {
        (item.unit, item.method, item.sand_silt_cutoff_um, item.silt_clay_cutoff_um)
        for item in observations
    }
    if len(definitions) != 1:
        raise ValueError(
            "Different texture units/methods/cutoffs require an explicit harmonization"
        )
    if any(item.quality_flag for item in observations):
        raise ValueError("Flagged texture assays must be reviewed before matching")
    rows, audit = [], []
    for (survey_id, sample_id), group in groups.items():
        if len(group) != 3 or {o.property for o in group} != {
            "sand_fraction",
            "silt_fraction",
            "clay_fraction",
        }:
            raise ValueError("Each target must have a complete texture triplet")
        sample = group[0]
        row = {
            "sample_id": sample_id,
            "survey_id": survey_id,
            "plot_id": sample.plot_id,
            "unit": sample.unit,
            "depth_top_m": sample.depth_top_m,
            "depth_bottom_m": sample.depth_bottom_m,
            "x_m": sample.x_m,
            "y_m": sample.y_m,
            "crs": sample.crs,
            "acquired_at_utc": sample.acquired_at_utc.isoformat(),
            "method": sample.method,
            "sand_silt_cutoff_um": sample.sand_silt_cutoff_um,
            "silt_clay_cutoff_um": sample.silt_clay_cutoff_um,
            "closure_tolerance_percent": sample.closure_tolerance_percent,
            "target_source_sha256": sample.source_sha256,
            **{o.property: o.value for o in group},
        }
        accepted: dict[str, list[float]] = {}
        units_by_key: dict[str, str] = {}
        lineage = {}
        for feature in features:
            if feature.get("unit") not in ("ADC counts", "mV", "V"):
                raise ValueError("Unknown GPR amplitude feature unit")
            reason = None
            distance = None
            lag = None
            overlap = None
            if feature.get("branch") != "quantitative" or feature.get("value") in (None, ""):
                reason = "nonquantitative_or_invalid_signal"
            elif (
                feature.get("plot_id") != sample.plot_id or feature.get("target_crs") != sample.crs
            ):
                reason = "plot_or_crs_mismatch"
            elif (
                feature.get("position_status") not in ("measured", "interpolated")
                or feature.get("x_m") in (None, "")
                or feature.get("y_m") in (None, "")
            ):
                reason = "unlocated"
            else:
                x, y = float(feature["x_m"]), float(feature["y_m"])
                if not math.isfinite(x) or not math.isfinite(y):
                    reason = "unlocated"
                else:
                    distance = math.hypot(x - sample.x_m, y - sample.y_m)
                    if distance > radius_m:
                        reason = "outside_radius"
            if reason is None:
                if not feature.get("time_utc"):
                    reason = "missing_time"
                else:
                    lag = (
                        abs(
                            (
                                _utc(str(feature["time_utc"])) - sample.acquired_at_utc
                            ).total_seconds()
                        )
                        / 3600
                    )
                    if lag > max_lag_hours:
                        reason = "outside_time_tolerance"
            if reason is None:
                top, bottom = feature.get("depth_top_m"), feature.get("depth_bottom_m")
                if (
                    top in (None, "")
                    or bottom in (None, "")
                    or not feature.get("velocity_calibration_id")
                ):
                    reason = "uncalibrated_depth"
                else:
                    top, bottom = float(top), float(bottom)
                    if not math.isfinite(top) or not math.isfinite(bottom) or bottom <= top:
                        reason = "invalid_depth_support"
                    else:
                        shared = max(
                            0.0, min(bottom, sample.depth_bottom_m) - max(top, sample.depth_top_m)
                        )
                        overlap = shared / (sample.depth_bottom_m - sample.depth_top_m)
                        if overlap < minimum_depth_overlap:
                            reason = "insufficient_depth_overlap"
            feature_key = (
                f"{feature.get('feature_name')}_"
                f"{float(feature['window_start_ns']):g}_"
                f"{float(feature['window_stop_ns']):g}_ns"
            )
            if feature_key in units_by_key and units_by_key[feature_key] != feature["unit"]:
                raise ValueError("Same-named GPR features use incompatible units")
            units_by_key[feature_key] = feature["unit"]
            if reason is None:
                value = float(feature["value"])
                if not math.isfinite(value):
                    reason = "nonfinite_feature"
                else:
                    accepted.setdefault(feature_key, []).append(value)
                    lineage.setdefault(feature_key, []).append(
                        {
                            "trace_id": feature.get("trace_id"),
                            "source_id": feature.get("source_id"),
                            "profile_id": feature.get("profile_id"),
                            "processing_signature": feature.get("processing_signature"),
                            "time_basis": feature.get("time_basis"),
                            "velocity_calibration_id": feature.get("velocity_calibration_id"),
                            "velocity_calibration_evidence": feature.get(
                                "velocity_calibration_evidence"
                            ),
                        }
                    )
            audit.append(
                {
                    "sample_id": sample_id,
                    "sample_survey_id": survey_id,
                    "trace_id": feature.get("trace_id"),
                    "feature_key": feature_key,
                    "included": reason is None,
                    "exclusion_reason": reason,
                    "distance_m": distance,
                    "lag_hours": lag,
                    "depth_overlap_fraction": overlap,
                }
            )
        for key, values in accepted.items():
            row[key] = statistics.median(values)
            row[f"{key}_unit"] = units_by_key[key]
            row[f"{key}_contributors"] = len(values)
        row["lineage"] = json.dumps(lineage, sort_keys=True, separators=(",", ":"))
        rows.append(row)
    return rows, audit
