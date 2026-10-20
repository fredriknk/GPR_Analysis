"""Supported boundaries, time intervals and calibrated vertical thickness."""

import json
from dataclasses import replace

import numpy as np
import xarray as xr
from shapely.geometry import shape

from soil_sensing.spatial.gridding import GridOptions, aggregate_support
from soil_sensing.spatial.interpolation import interpolate_support
from soil_sensing.volumes.contracts import Reason


def finite_offset_depth(time, model):
    from soil_sensing.processing.depth import VelocityCalibration

    VelocityCalibration(**model["calibration"])
    velocity = model["calibration"]["velocity_m_per_ns"]
    offset = model["antenna_separation_m"]
    if not np.isfinite(offset) or offset < 0:
        raise ValueError("Antenna separation must be known and nonnegative")
    if model.get("method") != "homogeneous_horizontal_reflector_finite_offset":
        raise ValueError("Unsupported depth model; layered paths need a validated adapter")
    time = np.asarray(time, float)
    squared = (velocity * time / 2) ** 2 - (offset / 2) ** 2
    return np.sqrt(np.where((time > 0) & (squared > 0), squared, np.nan))


def shared_thickness_uncertainty(
    top_time, bottom_time, top_sigma, bottom_sigma, model, pick_correlation=0.0
):
    """First-order independent pick errors plus SHARED velocity/time-zero errors.

    Spatial interpolation, pick covariance and model inadequacy remain unknown;
    output is explicitly partial, never a complete confidence interval.
    """
    top, bottom = finite_offset_depth(top_time, model), finite_offset_depth(bottom_time, model)
    v = model["calibration"]["velocity_m_per_ns"]
    with np.errstate(divide="ignore", invalid="ignore"):
        dv_top, dv_bottom = v * top_time**2 / (4 * top), v * bottom_time**2 / (4 * bottom)
        dt_top, dt_bottom = v**2 * top_time / (4 * top), v**2 * bottom_time / (4 * bottom)
        variance = (dt_top * top_sigma) ** 2 + (dt_bottom * bottom_sigma) ** 2
        variance -= 2 * dt_top * dt_bottom * top_sigma * bottom_sigma * pick_correlation
        variance += (
            (dv_bottom - dv_top) * model["calibration"]["standard_uncertainty_m_per_ns"]
        ) ** 2
        variance += (
            (dt_bottom - dt_top) * model["calibration"]["time_zero_standard_uncertainty_ns"]
        ) ** 2
    return np.sqrt(variance)


def build_surfaces(cube, document, rows, layers, *, preview_proposed=False, uncertainty=None):
    if uncertainty is not None:
        if (
            set(uncertainty) != {"pick_error_model", "between_boundary_correlation", "evidence"}
            or uncertainty["pick_error_model"] != "independent_across_traces"
            or not uncertainty["evidence"]
            or not -1 <= uncertainty["between_boundary_correlation"] <= 1
        ):
            raise ValueError(
                "Uncertainty requires reviewed trace independence/correlation evidence"
            )
    if len({p["time_basis"] for p in document["picks"]}) > 1:
        raise ValueError("Surfaces cannot mix recording and corrected pick bases")
    time_basis = document["picks"][0]["time_basis"] if document["picks"] else "recording"
    lookups = {(r["source_id"], r["profile_id"], r["trace_id"]): r for r in rows}
    picks = [
        p
        for p in document["picks"]
        if p["status"] == "accepted" or preview_proposed and p["status"] == "proposed"
    ]
    horizons = sorted({p["horizon_id"] for p in document["picks"]})
    if not horizons:
        raise ValueError("No horizon picks supplied")
    xx, yy = np.meshgrid(cube.x.values, cube.y.values)
    query = np.c_[xx.ravel(), yy.ravel()]
    domain = shape(cube.attrs["domain_geojson"])
    options = GridOptions(**cube.attrs["grid_options"])
    times, sigmas, reasons, support_ids, support_weights, contributors, pick_sigmas = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for horizon in horizons:
        observations = []
        for pick in picks:
            if pick["horizon_id"] != horizon:
                continue
            row = lookups[(pick["source_id"], pick["profile_id"], pick["trace_id"])]
            if row["located"]:
                if (
                    row.get("sample_valid") is not None
                    and not row["sample_valid"][pick["native_sample"]]
                ):
                    continue
                observations.append(
                    {
                        **row,
                        "value": pick["two_way_time_ns"],
                        "trace_id": json.dumps(
                            [row["source_id"], row["profile_id"], row["trace_id"]]
                        ),
                        "sigma": pick["standard_uncertainty_ns"],
                    }
                )
        locations = {}
        for row in observations:
            locations.setdefault(tuple(row["xy"]), []).append(row["value"])
        contradictory = {xy for xy, values in locations.items() if np.ptp(values) > 1e-8}
        observations = [row for row in observations if tuple(row["xy"]) not in contradictory]
        bins = aggregate_support(observations, options.bin_m, [0, 0])
        result = interpolate_support(bins, query, domain, replace(options, method="linear"))
        valid = result["valid_support"]
        for i in np.flatnonzero(valid):
            valid[i] = (
                len(
                    {
                        line
                        for j in result["support_index"][i]
                        if j >= 0
                        for line in bins[j]["line_ids"]
                    }
                )
                >= cube.attrs["minimum_lines"]
            )
        ids = result["support_index"].copy()
        weights = np.zeros(ids.shape)
        selected = np.flatnonzero(valid)
        if len(selected):
            vertices = np.asarray([b["xy"] for b in bins])[ids[selected]]
            matrix = np.concatenate(
                [vertices.transpose(0, 2, 1), np.ones((len(selected), 1, 3))], axis=1
            )
            weights[selected] = np.linalg.solve(
                matrix, np.c_[query[selected], np.ones(len(selected))][..., None]
            )[..., 0]
        if options.method == "nearest":
            ids[:] = -1
            ids[selected, 0] = result["nearest_support_index"][selected]
            weights[:] = 0
            weights[selected, 0] = 1
        ids[~valid] = -1
        base = len(contributors)
        support_ids.append(np.where(ids >= 0, ids + base, -1).reshape(*xx.shape, 3))
        support_weights.append(weights.reshape(*xx.shape, 3))
        contributors.extend(
            {**b, "horizon_id": horizon, "bin_id": base + i} for i, b in enumerate(bins)
        )
        lookup_sigma = {r["trace_id"]: r["sigma"] for r in observations}
        partial = np.full(len(query), np.nan)
        if uncertainty is not None:
            bin_variance = np.array(
                [
                    sum(lookup_sigma[t] ** 2 for t in b["trace_ids"]) / len(b["trace_ids"]) ** 2
                    if all(lookup_sigma[t] is not None for t in b["trace_ids"])
                    else np.nan
                    for b in bins
                ]
            )
            if len(selected):
                partial[selected] = np.sqrt(
                    np.sum(
                        np.where(
                            weights[selected] > 0,
                            bin_variance[np.maximum(ids[selected], 0)] * weights[selected] ** 2,
                            0,
                        ),
                        axis=1,
                    )
                )
        pick_sigmas.append(partial.reshape(xx.shape))
        values = np.where(
            valid,
            result["nearest_value"] if options.method == "nearest" else result["value"],
            np.nan,
        )
        times.append(values.reshape(xx.shape))
        # Interpolated pick uncertainty needs spatial covariance; do not invent it.
        sigmas.append(np.full(xx.shape, np.nan))
        reasons.append(
            np.where(valid.reshape(xx.shape), 0, int(Reason.INTERPRETATION)).astype(np.uint16)
        )
    time_values = np.array(times)
    variables = dict(
        horizon_time_ns=(("horizon", "y", "x"), time_values),
        horizon_reason=(("horizon", "y", "x"), np.array(reasons)),
        horizon_standard_uncertainty_ns=(("horizon", "y", "x"), np.array(sigmas)),
        horizon_pick_component_standard_uncertainty_ns=(
            ("horizon", "y", "x"),
            np.array(pick_sigmas),
        ),
        horizon_support_index=(("horizon", "y", "x", "vertex"), np.array(support_ids)),
        horizon_support_weight=(("horizon", "y", "x", "vertex"), np.array(support_weights)),
    )
    calibrated = bool(cube.attrs.get("calibration") and cube.attrs.get("time_zero_evidence"))
    model = cube.attrs.get("calibration")
    if calibrated:
        t0 = (
            0
            if time_basis == "corrected"
            else float(
                model.get("reviewed_time_zero_ns", cube.attrs.get("reviewed_time_zero_ns", np.nan))
            )
        )
        if not np.isfinite(t0):
            raise ValueError("Recording picks require explicit physical time zero for depth")
        depth = finite_offset_depth(time_values - t0, model)
        variables["horizon_depth_m"] = (("horizon", "y", "x"), depth)
    layer_values, layer_reasons, layer_sigma = [], [], []
    seen = set()
    for layer in layers:
        if (
            set(layer) - {"layer_id", "top", "bottom", "minimum_interval_ns", "pinchout_evidence"}
            or not layer.get("layer_id")
            or layer["layer_id"] in seen
        ):
            raise ValueError("Invalid or duplicate layer definition")
        seen.add(layer["layer_id"])
        if (
            layer.get("top") not in horizons
            or layer.get("bottom") not in horizons
            or layer["top"] == layer["bottom"]
        ):
            raise ValueError("Layers require two distinct named boundaries")
        a, b = horizons.index(layer["top"]), horizons.index(layer["bottom"])
        interval = time_values[b] - time_values[a]
        valid = (
            np.isfinite(interval)
            & (interval >= layer.get("minimum_interval_ns", 0.0))
            & (interval > 0 if not layer.get("pinchout_evidence") else interval >= 0)
        )
        value = depth[b] - depth[a] if calibrated else interval
        valid &= np.isfinite(value) & (value >= 0)
        layer_values.append(np.where(valid, value, np.nan))
        sigma = np.full(value.shape, np.nan)
        if uncertainty is not None:
            if calibrated:
                sigma = shared_thickness_uncertainty(
                    time_values[a] - t0,
                    time_values[b] - t0,
                    np.array(pick_sigmas[a]),
                    np.array(pick_sigmas[b]),
                    model,
                    uncertainty["between_boundary_correlation"],
                )
            else:
                sigma = np.sqrt(
                    np.maximum(
                        0.0,
                        np.array(pick_sigmas[a]) ** 2
                        + np.array(pick_sigmas[b]) ** 2
                        - 2
                        * uncertainty["between_boundary_correlation"]
                        * np.array(pick_sigmas[a])
                        * np.array(pick_sigmas[b]),
                    )
                )
        layer_sigma.append(np.where(valid, sigma, np.nan))
        layer_reasons.append(np.where(valid, 0, int(Reason.INTERPRETATION)).astype(np.uint16))
    if layers:
        variables["layer_thickness_m" if calibrated else "layer_interval_twt_ns"] = (
            ("layer", "y", "x"),
            np.array(layer_values),
        )
        variables["layer_reason"] = (("layer", "y", "x"), np.array(layer_reasons))
        variables[
            "layer_partial_standard_uncertainty_m"
            if calibrated
            else "layer_pick_component_standard_uncertainty_ns"
        ] = (("layer", "y", "x"), np.array(layer_sigma))
    result = xr.Dataset(
        variables,
        coords=dict(
            horizon=horizons,
            layer=[layer["layer_id"] for layer in layers],
            x=cube.x.values,
            y=cube.y.values,
            vertex=[0, 1, 2],
        ),
        attrs={
            **cube.attrs,
            "product_kind": "interpreted_boundaries",
            "interpretation_hash": document["interpretation_hash"],
            "review_status": "proposed_preview" if preview_proposed else "accepted_only",
            "time_basis": time_basis,
            "layers": layers,
            "uncertainty_model": uncertainty,
            "surface_contributors": contributors,
            "uncertainty_status": "unknown spatial/model uncertainty; no invented raster precision",
            "thickness_direction": "vertical_positive_down_not_normal_to_dip",
        },
    )
    for name in result.data_vars:
        if "reason" not in name and "support" not in name:
            result[name].attrs["units"] = "m" if name.endswith("_m") else "ns"
    return result
