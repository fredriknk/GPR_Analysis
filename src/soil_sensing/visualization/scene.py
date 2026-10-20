"""Validate quantitative profiles and build traceable, decimated review scenes."""

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np

from soil_sensing.processing.gpr import window_rms_features
from soil_sensing.profile import QualityFlag, validate_profile
from soil_sensing.spatial.gridding import inside_domain, polygon_domain


@dataclass
class Scene:
    plot_id: str
    survey_id: str | None
    crs: str | None
    origin_xy_m: list[float]
    boundary: list
    domain: object
    vertical_axis: str
    gates: list
    attribute_unit: str
    processing_signature: str
    calibration: dict | None
    geometry_evidence: str | None = None
    synthetic: bool = False
    diagnostic: bool = False
    coordinate_mode: str = "projected"
    coordinate_note: str | None = None
    coordinate_anchor_latlon: list[float] | None = None
    curtains: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    quality: dict = field(default_factory=dict)

    @property
    def gate_unit(self):
        return "m" if self.vertical_axis == "depth" else "ns"

    def grid_metadata(self):
        return {
            "plot_id": self.plot_id,
            "survey_id": self.survey_id,
            "crs": self.crs,
            "origin_xy_m": self.origin_xy_m,
            "gate_bounds": self.gates,
            "gate_unit": self.gate_unit,
            "vertical_axis": self.vertical_axis,
            "attribute": "gpr_rms",
            "attribute_unit": self.attribute_unit,
            "processing_signature": self.processing_signature,
            "calibration": self.calibration,
            "source_profiles": self.sources,
            "synthetic": self.synthetic,
            "geometry_evidence": self.geometry_evidence,
            "coordinate_mode": self.coordinate_mode,
            "coordinate_note": self.coordinate_note,
            "coordinate_anchor_latlon": self.coordinate_anchor_latlon,
        }


def validate_gates(gates):
    if not isinstance(gates, list) or not gates:
        raise ValueError("gates must contain [start, stop] pairs")
    previous_stop = -np.inf
    for gate in gates:
        if not isinstance(gate, (list, tuple)) or len(gate) != 2:
            raise ValueError("gates must contain [start, stop] pairs")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in gate):
            raise ValueError("Gate bounds must be numeric")
        if not np.isfinite(gate).all() or not 0 <= gate[0] < gate[1]:
            raise ValueError("Gate bounds must be finite, nonnegative and ordered")
        if gate[0] < previous_stop:
            raise ValueError("Gates must be ordered and nonoverlapping")
        previous_stop = gate[1]


def profile_signature(profile):
    specification = {
        key: profile.attrs.get(key)
        for key in (
            "processing_options",
            "amplitude_units",
            "antenna_frequency_mhz",
            "antenna_separation_m",
            "time_axis_convention",
            "depth_model",
            "channel_model",
        )
    }
    specification["transforms"] = [
        row
        for row in profile.attrs["history"]
        if row.get("operation") in ("process_gpr", "add_depth_axis")
    ]
    return hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()


def axis_values(profile, vertical_axis):
    if vertical_axis not in ("recording_time", "corrected_time", "depth"):
        raise ValueError("vertical_axis must be recording_time, corrected_time or depth")
    if vertical_axis == "recording_time":
        return profile.twt_ns.values
    if "corrected_twt_ns" not in profile or not profile.attrs.get("processing_options", {}).get(
        "time_zero_evidence"
    ):
        raise ValueError("Corrected time/depth requires evidenced physical time zero")
    if vertical_axis == "corrected_time":
        return profile.corrected_twt_ns.values
    if "depth_m" not in profile or not profile.attrs.get("depth_model"):
        raise ValueError("Depth mode requires an evidenced velocity calibration and depth axis")
    calibration = profile.attrs["depth_model"].get("calibration", {})
    if not calibration.get("calibration_id") or not calibration.get("evidence"):
        raise ValueError("Depth mode requires calibration identity and evidence")
    return profile.depth_m.values


def raw_gps_relative_xy(profile, anchor_latlon):
    """Approximate local offsets for diagnostic display; this is not a CRS transform."""
    if "latitude_deg" not in profile or "longitude_deg" not in profile:
        raise ValueError("GPS-layout diagnostics require parsed latitude/longitude")
    latitude = profile.latitude_deg.values.astype(float)
    longitude = profile.longitude_deg.values.astype(float)
    anchor_lat, anchor_lon = anchor_latlon
    north = (latitude - anchor_lat) * 111_320.0
    east = (longitude - anchor_lon) * 111_320.0 * np.cos(np.deg2rad(anchor_lat))
    return np.c_[east, north]


def _features(profile, gates, vertical_axis):
    """Reuse M3 quality rules for each selected gate; never grid display gain."""
    axis = axis_values(profile, vertical_axis)
    # M3's extractor prefers corrected time when present. Translate only the gate
    # sample selection to that coordinate, without resampling values or axes.
    feature_time = profile["corrected_twt_ns" if "corrected_twt_ns" in profile else "twt_ns"].values
    rows = []
    for start, stop in gates:
        selected = np.flatnonzero(np.isfinite(axis) & (axis >= start) & (axis < stop))
        if not len(selected):
            raise ValueError(f"Gate {start:g}–{stop:g} selects no samples")
        lower, upper = feature_time[selected[0]], feature_time[selected[-1]]
        if lower < 0:
            # The existing extractor deliberately forbids negative corrected gates.
            # Use a temporary recording coordinate to select the identical samples.
            gate_profile = profile.drop_vars("corrected_twt_ns")
            lower, upper = profile.twt_ns.values[selected[[0, -1]]]
        else:
            gate_profile = profile
        extracted = window_rms_features(
            gate_profile, [(float(lower), float(upper + profile.attrs["sample_interval_ns"]))]
        )
        rows.append(extracted)
    return rows


def build_scene(
    profiles,
    plot,
    gates,
    *,
    vertical_axis="recording_time",
    exclusions=(),
    regions=(),
    trace_step=2,
    sample_step=2,
    max_track_gap_m=6.0,
    synthetic=False,
    diagnostic=False,
    gps_diagnostic=False,
):
    validate_gates(gates)
    if not profiles:
        raise ValueError("A scene requires at least one profile")
    for profile in profiles:
        validate_profile(profile)
        if profile.attrs["processing_branch"] != "quantitative":
            raise ValueError("Scenes require quantitative profiles; display gain cannot be gridded")
    first = profiles[0]
    signature = profile_signature(first)
    axis = axis_values(first, vertical_axis)
    finite_axis = axis[np.isfinite(axis)]
    if not len(finite_axis) or np.any(np.diff(finite_axis) <= 0):
        raise ValueError("Vertical axis must have finite, strictly increasing samples")
    for profile in profiles:
        if profile_signature(profile) != signature:
            raise ValueError("Incompatible processing settings, calibration or amplitude units")
        if not np.array_equal(axis, axis_values(profile, vertical_axis), equal_nan=True):
            raise ValueError("Incompatible axes; explicit reviewed resampling is required")
        if profile.attrs["survey_id"] != first.attrs["survey_id"]:
            raise ValueError("Never merge unrelated surveys into a scene")
    if gps_diagnostic and not diagnostic:
        raise ValueError("GPS-layout diagnostics require diagnostic mode")
    coordinate_mode = "projected"
    coordinate_note = None
    coordinate_anchor = None
    if diagnostic:
        if not gps_diagnostic and len(profiles) != 1:
            raise ValueError("Diagnostic scenes contain exactly one line")
        if gps_diagnostic:
            accepted = []
            for profile in profiles:
                good = np.isfinite(profile.latitude_deg.values) & np.isfinite(
                    profile.longitude_deg.values
                )
                good &= np.isin(profile.position_status.values, ["measured", "interpolated"])
                if not good.any():
                    raise ValueError("Every GPS-layout line requires an accepted position")
                accepted.append(
                    np.c_[profile.latitude_deg.values[good], profile.longitude_deg.values[good]]
                )
            coordinates = np.concatenate(accepted)
            coordinate_anchor = np.nanmedian(coordinates, axis=0).astype(float).tolist()
            all_xy = np.concatenate(
                [raw_gps_relative_xy(profile, coordinate_anchor) for profile in profiles]
            )
            finite = np.isfinite(all_xy).all(axis=1)
            lower, upper = np.min(all_xy[finite], axis=0), np.max(all_xy[finite], axis=0)
            pad = max(2.0, 0.02 * float(np.max(upper - lower)))
            boundary = [
                [lower[0] - pad, lower[1] - pad],
                [upper[0] + pad, lower[1] - pad],
                [upper[0] + pad, upper[1] + pad],
                [lower[0] - pad, upper[1] + pad],
            ]
            coordinate_mode = "raw_gps_relative"
            coordinate_note = (
                "Approximate small-area offsets derived from raw NMEA latitude/longitude; "
                "datum, CRS and positional accuracy are unverified"
            )
            plot_id = (
                f"GPS diagnostic: {len(profiles)} lines near "
                f"{coordinate_anchor[0]:.5f}, {coordinate_anchor[1]:.5f}"
            )
        else:
            length = max(float(first.distance_m.values[-1]), 1.0)
            boundary = [[0, -1], [length, -1], [length, 1], [0, 1]]
            coordinate_mode = "odometer"
            coordinate_note = "Independent odometer line; no geographic placement"
            plot_id = f"Diagnostic: {first.attrs['source_id']}/{first.attrs['profile_id']}"
        origin, crs = [0.0, 0.0], None
    else:
        if plot is None or plot.status != "reviewed" or not plot.polygon or not plot.evidence:
            raise ValueError("Plot scenes require reviewed geometry, CRS and assignment evidence")
        from pyproj import CRS

        crs_object = CRS.from_user_input(plot.crs)
        if not crs_object.is_projected or any(
            a.unit_name.lower() not in ("metre", "meter") for a in crs_object.axis_info[:2]
        ):
            raise ValueError("Scene CRS must be projected in metres")
        for profile in profiles:
            assignment = profile.attrs.get("assignment") or {}
            if (
                profile.attrs["plot_id"] != plot.plot_id
                or not profile.attrs["survey_id"]
                or not assignment.get("assignment_evidence")
                or not assignment.get("reviewer")
                or assignment.get("include") is False
            ):
                raise ValueError("Plot scenes require reviewed explicit profile assignments")
            if profile.attrs["target_crs"] != plot.crs or not profile.attrs["source_crs"]:
                raise ValueError("Profile datum and plot projected CRS must match")
        origin = np.min(plot.polygon, axis=0).tolist()
        boundary = (np.asarray(plot.polygon) - origin).tolist()
        crs, plot_id = plot.crs, plot.plot_id

    def local(rings):
        return [(np.asarray(r) - origin).tolist() for r in rings]

    domain = polygon_domain(boundary, exclusions=local(exclusions), regions=local(regions))
    scene = Scene(
        plot_id,
        first.attrs["survey_id"],
        crs,
        origin,
        boundary,
        domain,
        vertical_axis,
        gates,
        first.attrs["amplitude_units"],
        signature,
        first.attrs.get("depth_model"),
        geometry_evidence=plot.evidence if not diagnostic else None,
        synthetic=synthetic,
        diagnostic=diagnostic,
        coordinate_mode=coordinate_mode,
        coordinate_note=coordinate_note,
        coordinate_anchor_latlon=coordinate_anchor,
        observations=[[] for _ in gates],
        quality={"total_traces": 0, "excluded_positions": 0, "excluded_windows": 0},
    )
    seen_lines, seen_traces = set(), set()
    for profile in profiles:
        line = json.dumps([profile.attrs["source_id"], profile.attrs["profile_id"]])
        trace_ids = profile.trace_id.values.astype(str)
        if line in seen_lines or seen_traces.intersection(trace_ids):
            raise ValueError("Duplicate source line or trace identity")
        seen_lines.add(line)
        seen_traces.update(trace_ids)
        scene.sources.append(
            {
                "source_id": profile.attrs["source_id"],
                "profile_id": profile.attrs["profile_id"],
                "checksums": profile.attrs["source_checksums"],
                "history": profile.attrs["history"],
                "assignment": profile.attrs.get("assignment"),
                "position_status_counts": {
                    k: int(np.sum(profile.position_status.values == k))
                    for k in ("measured", "interpolated", "missing", "rejected")
                },
            }
        )
        if gps_diagnostic:
            xy = raw_gps_relative_xy(profile, coordinate_anchor)
            located = np.isfinite(xy).all(axis=1) & np.isin(
                profile.position_status.values, ["measured", "interpolated"]
            )
        elif diagnostic:
            xy = np.c_[profile.distance_m.values, np.zeros(profile.sizes["trace"])]
            located = np.ones(len(xy), dtype=bool)
        else:
            xy = np.c_[profile.x_m.values, profile.y_m.values] - origin
            located = np.isfinite(xy).all(axis=1) & np.isin(
                profile.position_status.values, ["measured", "interpolated"]
            )
            located &= inside_domain(domain, np.where(np.isfinite(xy), xy, -1e30))
        scene.quality["total_traces"] += len(xy)
        scene.quality["excluded_positions"] += int((~located).sum())
        features = _features(profile, gates, vertical_axis)
        for gate, rows in enumerate(features):
            for i, row in enumerate(rows):
                if row["value"] is None:
                    scene.quality["excluded_windows"] += 1
                if not located[i] or row["value"] is None:
                    continue
                scene.observations[gate].append(
                    {
                        "xy": xy[i].tolist(),
                        "value": row["value"],
                        "line_id": line,
                        "trace_id": row["trace_id"],
                        "position_status": row["position_status"],
                        "time_utc": row["time_utc"],
                        "quality_flags": row["quality_flags"],
                        "horizontal_uncertainty_m": (
                            float(profile.horizontal_uncertainty_m.values[i])
                            if np.isfinite(profile.horizontal_uncertainty_m.values[i])
                            else None
                        ),
                    }
                )
        # Split in acquisition order BEFORE display decimation, preserving dropouts.
        breaks = np.r_[True, np.linalg.norm(np.diff(xy, axis=0), axis=1) > max_track_gap_m]
        breaks |= ~located | np.r_[False, ~located[:-1]]
        if not diagnostic:
            from shapely import LineString

            for i in np.flatnonzero(located[1:] & located[:-1]) + 1:
                if not domain.covers(LineString(xy[i - 1 : i + 1])):
                    breaks[i] = True
        groups = np.cumsum(breaks)
        sample_ids = np.arange(0, len(axis), sample_step)
        sample_ids = sample_ids[np.isfinite(axis[sample_ids]) & (axis[sample_ids] >= 0)]
        if not len(sample_ids):
            raise ValueError("No nonnegative vertical samples available for curtains")
        for group in np.unique(groups):
            ids = np.flatnonzero((groups == group) & located)[::trace_step]
            if not len(ids):
                continue
            amplitude = profile.amplitude.values[np.ix_(ids, sample_ids)].astype(float)
            bad = int(QualityFlag.ZERO_SIGNAL | QualityFlag.VENDOR_ZERO)
            invalid = (profile.quality_flags.values[ids] & bad) != 0
            amplitude[invalid] = np.nan
            if "adc_limit_mask" in profile:
                amplitude[profile.adc_limit_mask.values[np.ix_(ids, sample_ids)]] = np.nan
            if profile.attrs.get("clipping_can_spread_across_windows"):
                amplitude[(profile.quality_flags.values[ids] & int(QualityFlag.ADC_LIMIT)) != 0] = (
                    np.nan
                )
            scene.curtains.append(
                {
                    "line_id": line,
                    "xy": xy[ids].tolist(),
                    "vertical": axis[sample_ids].tolist(),
                    "amplitude": amplitude.T.tolist(),
                    "trace_ids": trace_ids[ids].tolist(),
                    "position_status": profile.position_status.values[ids].tolist(),
                    "quality_flags": profile.quality_flags.values[ids].astype(int).tolist(),
                    "depth_standard_uncertainty_m": (
                        profile.depth_standard_uncertainty_m.values[sample_ids].tolist()
                        if vertical_axis == "depth"
                        else None
                    ),
                }
            )
    return scene
