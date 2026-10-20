"""Versioned selection, capability and scientific cube contracts."""

import hashlib
import json
from enum import IntFlag

import numpy as np


class Reason(IntFlag):
    OUTSIDE_DOMAIN = 1
    INPUT_QUALITY = 2
    GEOMETRIC_SUPPORT = 4
    TOO_FEW_LINES = 8
    CALIBRATION = 16
    INTERPRETATION = 32


ATTRIBUTES = ("signed_amplitude", "envelope", "rms")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def line_id(profile):
    return json.dumps([profile.attrs["source_id"], profile.attrs["profile_id"]])


def freeze_selection(
    partition_id, profiles, hashes, *, position_policy, direction_policy, geometry
):
    identities = sorted((p.attrs["source_id"], p.attrs["profile_id"]) for p in profiles)
    if not partition_id or not identities or len(set(identities)) != len(identities):
        raise ValueError("Selection needs a partition and unique exact source/profile identities")
    if position_policy not in ("measured", "accepted"):
        raise ValueError("position_policy must be measured or accepted")
    if direction_policy not in ("separate", "combine_reviewed"):
        raise ValueError("Explicit direction policy is required")
    value = dict(
        schema_version="1.0",
        partition_id=partition_id,
        lines=[list(v) for v in identities],
        input_hashes=hashes,
        position_policy=position_policy,
        direction_policy=direction_policy,
        geometry=geometry,
    )
    return {**value, "selection_hash": digest(value)}


def validate_selection(selection):
    value = {k: v for k, v in selection.items() if k != "selection_hash"}
    if selection.get("schema_version") != "1.0" or digest(value) != selection.get("selection_hash"):
        raise ValueError("Invalid or altered reconstruction selection")
    lines = selection.get("lines", [])
    if not lines or lines != sorted(lines) or len(set(map(tuple, lines))) != len(lines):
        raise ValueError("Selection identities must be sorted and unique")


def capabilities(metadata):
    geographic = metadata.get("coordinate_mode") == "projected" and bool(metadata.get("crs"))
    geometry = geographic and bool(metadata.get("geometry_evidence"))
    model = metadata.get("calibration") or {}
    cal = model.get("calibration", {})
    depth = bool(
        cal.get("calibration_id") and cal.get("evidence") and metadata.get("time_zero_evidence")
    )
    return {
        "local_time_volume": {"allowed": True, "missing": []},
        "geographic_export": {
            "allowed": geometry,
            "missing": [] if geometry else ["reviewed datum/metric CRS, identity and geometry"],
        },
        "depth_and_thickness": {
            "allowed": depth,
            "missing": []
            if depth
            else ["physical time zero and applicable velocity/antenna calibration"],
        },
        "absolute_elevation": {
            "allowed": False,
            "missing": ["surveyed ground surface and vertical datum adapter"],
        },
    }


def validate_volume(cube, support=None):
    attrs = cube.attrs
    required = {
        "schema_version",
        "coordinate_mode",
        "crs",
        "plot_id",
        "survey_id",
        "origin_xy_m",
        "vertical_axis",
        "vertical_unit",
        "selection_hash",
        "partition_id",
        "calibration",
        "source_profiles",
        "attribute_unit",
        "domain_geojson",
        "grid_options",
    }
    if required - attrs.keys() or attrs.get("schema_version") != "1.0":
        raise ValueError("Incomplete radar volume schema 1.0")
    mode = attrs["coordinate_mode"]
    if mode not in ("raw_gps_relative", "projected", "synthetic_local"):
        raise ValueError("Unknown volume coordinate mode")
    if mode != "projected" and (attrs["crs"] is not None or attrs["plot_id"] is not None):
        raise ValueError("Local volumes must have null CRS and production plot ID")
    if mode == "synthetic_local" and attrs.get("synthetic") is not True:
        raise ValueError("Synthetic local frame requires an explicit fixture flag")
    if mode == "raw_gps_relative":
        anchor = attrs.get("coordinate_anchor_latlon")
        if (
            anchor is None
            or len(anchor) != 2
            or not np.isfinite(anchor).all()
            or attrs.get("diagnostic") is not True
        ):
            raise ValueError("Raw-GPS volume requires its approximate diagnostic anchor")
    origin = attrs["origin_xy_m"]
    if len(origin) != 2 or not np.isfinite(origin).all():
        raise ValueError("Volume origin requires two finite coordinates")
    if mode == "projected":
        from pyproj import CRS

        crs = CRS.from_user_input(attrs["crs"])
        if not crs.is_projected or any(
            a.unit_name.lower() not in ("metre", "meter") for a in crs.axis_info[:2]
        ):
            raise ValueError("Geographic volumes require a projected metric CRS")
        if not attrs.get("geometry_evidence") or not attrs["survey_id"] or not attrs["plot_id"]:
            raise ValueError("Geographic volumes require reviewed identities/geometry")
    axis = attrs["vertical_axis"]
    if axis not in ("recording_time", "corrected_time", "depth"):
        raise ValueError("Unknown vertical axis")
    if attrs["vertical_unit"] != ("m" if axis == "depth" else "ns"):
        raise ValueError("Vertical basis and unit disagree")
    if axis != "recording_time" and not attrs.get("time_zero_evidence"):
        raise ValueError("Corrected/depth volume requires time-zero evidence")
    if axis == "depth" and not capabilities(attrs)["depth_and_thickness"]["allowed"]:
        raise ValueError("Depth calibration missing")
    for name in ("x", "y", "vertical"):
        values = cube[name].values
        if (
            cube[name].dims != (name,)
            or not len(values)
            or not np.isfinite(values).all()
            or np.any(np.diff(values) <= 0)
        ):
            raise ValueError("Cube axes must be finite, strictly increasing and nonempty")
    for name in ("vertical_lower", "vertical_upper", "native_sample"):
        if name not in cube or cube[name].dims != ("vertical",):
            raise ValueError("Native vertical identity and explicit bounds are required")
    if np.any(cube.vertical_lower.values >= cube.vertical.values) or np.any(
        cube.vertical_upper.values <= cube.vertical.values
    ):
        raise ValueError("Vertical bounds must contain their sample centres")
    for name in ATTRIBUTES:
        for suffix in ("", "_valid", "_reason", "_nearest"):
            if name + suffix not in cube or cube[name + suffix].dims != ("vertical", "y", "x"):
                raise ValueError("Missing cube variable or invalid dimensions")
        mask = cube[name + "_valid"].values
        if mask.dtype != bool or not np.array_equal(mask, np.isfinite(cube[name].values)):
            raise ValueError("Per-variable validity must equal finite values")
        if not np.array_equal(mask, np.isfinite(cube[name + "_nearest"].values)):
            raise ValueError("Nearest baseline support differs")
        reason = cube[name + "_reason"].values
        if (
            np.any((reason == 0) != mask)
            or cube[name].attrs.get("units") != attrs["attribute_unit"]
        ):
            raise ValueError("Reason mask or attribute units disagree")
        if support is not None:
            if not np.array_equal(support.vertical.values, cube.vertical.values):
                raise ValueError("Support and cube vertical axes differ")
            ids = support[name + "_bin"].values
            weights = support[name + "_weight"].values
            if ids.shape != (*mask.shape, 3) or weights.shape != ids.shape:
                raise ValueError("Invalid stencil dimensions")
            if np.any(ids[~mask] != -1) or np.any(weights[~mask] != 0):
                raise ValueError("Unsupported voxels cannot reference contributors")
            if (
                not np.isfinite(weights).all()
                or np.any(weights < -1e-8)
                or not np.allclose(weights[mask].sum(axis=-1), 1)
            ):
                raise ValueError("Supported interpolation weights must sum to one")
            if np.any((ids < 0) & (weights != 0)):
                raise ValueError("Missing stencil vertices cannot carry weights")
            if np.any(support[name + "_line_count"].values[mask] < attrs["minimum_lines"]):
                raise ValueError("Insufficient independent lines")
    if support is not None and support.attrs.get("selection_hash") != attrs["selection_hash"]:
        raise ValueError("Support selection does not match cube")
