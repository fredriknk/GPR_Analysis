"""Canonical radar-profile validation and quality flags."""

from enum import IntFlag

import numpy as np
import xarray as xr


class QualityFlag(IntFlag):
    POSITION_MISSING = 1
    ZERO_SIGNAL = 2
    ADC_LIMIT = 4
    VENDOR_ZERO = 8
    GPS_INVALID = 16
    GPS_DUPLICATE = 32
    GPS_POSITION_MISMATCH = 64
    GPS_SPEED_OUTLIER = 128
    GPS_INTERPOLATED = 256


def validate_profile(profile: xr.Dataset) -> None:
    """Validate the stored raw profile contract; no spatial calibration is implied."""
    required = {
        "amplitude",
        "trace_id",
        "sample",
        "twt_ns",
        "distance_m",
        "x_m",
        "y_m",
        "elevation_m",
        "time_utc",
        "position_status",
        "quality_flags",
        "horizontal_uncertainty_m",
        "trace_header_bytes",
        "recorded_position_m",
    }
    missing = required - set(profile.variables)
    if missing:
        raise ValueError(f"Radar profile missing variables: {sorted(missing)}")
    attrs = {
        "schema_version",
        "profile_id",
        "source_id",
        "survey_id",
        "plot_id",
        "source_crs",
        "target_crs",
        "vertical_datum",
        "source_checksums",
        "sample_interval_ns",
        "time_axis_convention",
        "processing_branch",
        "history",
    }
    if attrs - profile.attrs.keys():
        raise ValueError(
            f"Radar profile missing attributes: {sorted(attrs - profile.attrs.keys())}"
        )
    if profile.attrs["schema_version"] != "0.2":
        raise ValueError("Unsupported RadarProfile schema")
    if profile.amplitude.dims != ("trace", "sample") or profile.amplitude.dtype != np.float32:
        raise ValueError("amplitude must be float32 with dimensions (trace, sample)")
    if not all(profile.sizes.get(d, 0) > 0 for d in ("trace", "sample")):
        raise ValueError("Empty radar profile")
    if not np.isfinite(profile.amplitude.values).all():
        raise ValueError("Raw amplitudes must be finite")
    if profile.trace_header_bytes.dims != ("trace", "header_byte"):
        raise ValueError("Trace headers must have dimensions (trace, header_byte)")
    if profile.sizes["header_byte"] != 128 or profile.trace_header_bytes.dtype != np.uint8:
        raise ValueError("Trace headers must preserve exactly 128 bytes per trace")
    trace_variables = required - {"amplitude", "sample", "twt_ns", "trace_header_bytes"}
    if any(profile[v].dims != ("trace",) for v in trace_variables):
        raise ValueError("Trace metadata dimensions must be (trace,)")
    if len(np.unique(profile.trace_id.values)) != profile.sizes["trace"]:
        raise ValueError("trace_id must be unique within a profile")
    if not np.array_equal(profile["sample"], np.arange(profile.sizes["sample"])):
        raise ValueError("sample must be a zero-based consecutive index")
    interval = profile.attrs["sample_interval_ns"]
    if not np.isfinite(interval) or interval <= 0:
        raise ValueError("Invalid sampling interval")
    if profile.twt_ns.dims != ("sample",) or not np.allclose(
        profile.twt_ns, np.arange(profile.sizes["sample"]) * interval, rtol=1e-12, atol=1e-12
    ):
        raise ValueError("Raw time coordinates do not match sample_interval_ns")
    if not np.isfinite(profile.distance_m).all() or np.any(np.diff(profile.distance_m) < 0):
        raise ValueError("distance_m must be finite and nondecreasing")
    if profile.quality_flags.dtype != np.uint32:
        raise ValueError("quality_flags must be uint32")
    statuses = profile.position_status.values
    if not np.isin(statuses, ("measured", "interpolated", "missing", "rejected")).all():
        raise ValueError("Invalid position_status")
    if np.any(np.isfinite(profile.x_m.values) != np.isfinite(profile.y_m.values)):
        raise ValueError("Projected x/y coordinates must be present together")
    if not profile.attrs["target_crs"] and (
        np.isfinite(profile.x_m).any() or np.isfinite(profile.y_m).any()
    ):
        raise ValueError("Projected positions require a target CRS")
    if ("longitude_deg" in profile) != ("latitude_deg" in profile):
        raise ValueError("Geographic longitude and latitude must be stored together")
    if "longitude_deg" in profile:
        for name, limit in (("longitude_deg", 180), ("latitude_deg", 90)):
            values = profile[name]
            if values.dims != ("trace",) or np.any(
                np.abs(values.values[np.isfinite(values)]) > limit
            ):
                raise ValueError(f"Invalid {name}")
        located = np.isfinite(profile.longitude_deg.values) & np.isfinite(
            profile.latitude_deg.values
        )
        if np.any(located != np.isin(statuses, ("measured", "interpolated"))):
            raise ValueError("Geographic coordinates conflict with position_status")
    if "adc_limit_mask" in profile and (
        profile.adc_limit_mask.dims != ("trace", "sample")
        or profile.adc_limit_mask.dtype != np.bool_
    ):
        raise ValueError("adc_limit_mask must be Boolean on (trace, sample)")
    if "corrected_twt_ns" in profile:
        if (
            profile.corrected_twt_ns.dims != ("sample",)
            or not np.isfinite(profile.corrected_twt_ns.values).all()
        ):
            raise ValueError("Invalid corrected time axis")
    if "depth_m" in profile:
        if "depth_standard_uncertainty_m" not in profile:
            raise ValueError("Depth uncertainty is required")
        for name in ("depth_m", "depth_standard_uncertainty_m"):
            values = profile[name]
            if values.dims != ("sample",) or np.any(values.values[np.isfinite(values)] < 0):
                raise ValueError(f"Invalid {name}")
