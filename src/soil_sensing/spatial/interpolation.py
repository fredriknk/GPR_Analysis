"""Conservative per-gate Delaunay interpolation and whole-line evaluation."""

import json
from dataclasses import asdict

import numpy as np
import xarray as xr
from scipy.spatial import Delaunay, QhullError, cKDTree
from shapely import Polygon

from soil_sensing.spatial.gridding import (
    GridOptions,
    aggregate_support,
    grid_coordinates,
    inside_domain,
)


def interpolate_support(support: list[dict], query, domain, options: GridOptions):
    """Both methods share hull, complete-triangle, edge and contributor-distance masks.

    Nearest is a baseline within the same footprint, never a way to extrapolate.
    Requiring whole triangles inside the domain prevents bridges across holes and
    disconnected regions, even where the query itself lies on valid ground.
    """
    query = np.asarray(query, dtype=float).reshape(-1, 2)
    n = len(query)
    result = {
        "value": np.full(n, np.nan),
        "nearest_value": np.full(n, np.nan),
        "valid_support": np.zeros(n, dtype=bool),
        "nearest_distance_m": np.full(n, np.nan),
        "contributor_count": np.zeros(n, dtype=np.int32),
        "nearest_support_index": np.full(n, -1, dtype=np.int32),
        "support_index": np.full((n, 3), -1, dtype=np.int32),
        "status": "insufficient_noncollinear_support",
    }
    if not support:
        return result
    xy = np.array([r["xy"] for r in support])
    values = np.array([r["value"] for r in support])
    distance, nearest = cKDTree(xy).query(query)
    result["nearest_distance_m"] = distance
    if len(xy) < 3:
        return result
    try:
        triangulation = Delaunay(xy)
    except QhullError:
        return result
    vertices = xy[triangulation.simplices]
    edges = np.linalg.norm(vertices - np.roll(vertices, 1, axis=1), axis=2).max(axis=1)
    usable = edges <= options.max_edge_m
    for i in np.flatnonzero(usable):
        usable[i] = domain.covers(Polygon(vertices[i]))
    simplex = triangulation.find_simplex(query)
    valid = (simplex >= 0) & inside_domain(domain, query)
    valid &= usable[np.maximum(simplex, 0)]
    ids = triangulation.simplices[np.maximum(simplex, 0)]
    valid &= np.linalg.norm(xy[ids] - query[:, None, :], axis=2).max(axis=1) <= (
        options.max_distance_m
    )
    selected = np.flatnonzero(valid)
    transform = triangulation.transform[simplex[selected]]
    weights2 = np.einsum("nij,nj->ni", transform[:, :2], query[selected] - transform[:, 2])
    weights = np.c_[weights2, 1 - weights2.sum(axis=1)]
    linear = np.sum(weights * values[ids[selected]], axis=1)
    result["value"][selected] = linear if options.method == "linear" else values[nearest[selected]]
    result["nearest_value"][selected] = values[nearest[selected]]
    result["nearest_support_index"][selected] = nearest[selected]
    result["valid_support"] = valid
    result["support_index"][selected] = ids[selected]
    counts = np.array([r["count"] for r in support])
    result["contributor_count"][selected] = (
        counts[ids[selected]].sum(axis=1)
        if options.method == "linear"
        else counts[nearest[selected]]
    )
    if options.method == "nearest":
        result["support_index"][selected] = -1
        result["support_index"][selected, 0] = nearest[selected]
    result["status"] = "supported" if valid.any() else "no_supported_queries"
    return result


def build_grid(observations_by_gate, boundary, domain, options: GridOptions, metadata):
    x, y = grid_coordinates(boundary, options, len(observations_by_gate))
    xx, yy = np.meshgrid(x, y)
    query = np.c_[xx.ravel(), yy.ravel()]
    results, supports, statuses = [], [], []
    origin = np.min(boundary, axis=0)
    for observations in observations_by_gate:
        support = aggregate_support(observations, options.bin_m, origin)
        support = [s for s in support if inside_domain(domain, [s["xy"]])[0]]
        result = interpolate_support(support, query, domain, options)
        supports.append(support)
        statuses.append(result.pop("status"))
        results.append(result)
    variables = {}
    for key in results[0]:
        dims = ("gate", "y", "x") + (("vertex",) if key == "support_index" else ())
        shape = (len(results), len(y), len(x)) + ((3,) if key == "support_index" else ())
        variables[key] = (dims, np.stack([r[key] for r in results]).reshape(shape))
    bounds = np.asarray(metadata["gate_bounds"])
    grid = xr.Dataset(
        variables,
        coords={
            "gate": np.arange(len(results)),
            "x": x,
            "y": y,
            "vertex": np.arange(3),
            "gate_start": ("gate", bounds[:, 0]),
            "gate_stop": ("gate", bounds[:, 1]),
        },
        attrs={
            **metadata,
            "schema_version": "0.1",
            "method": options.method,
            "bin_aggregation": "per_line_spatial_bin_mean_then_exact_location_mean",
            "support_status": statuses,
            "support_reference": "support.json",
            "coverage_diagnostics_are_uncertainty": False,
            "grid_options": asdict(options),
            "domain_geojson": json.loads(json.dumps(domain.__geo_interface__)),
        },
    )
    grid.value.attrs["units"] = metadata["attribute_unit"]
    grid.nearest_value.attrs["units"] = metadata["attribute_unit"]
    grid.nearest_distance_m.attrs["units"] = "m"
    validate_grid(grid)
    return grid, supports


def validate_grid(grid):
    required = {
        "value",
        "nearest_value",
        "valid_support",
        "nearest_distance_m",
        "contributor_count",
        "nearest_support_index",
        "support_index",
        "gate_start",
        "gate_stop",
    }
    attrs = {
        "schema_version",
        "crs",
        "origin_xy_m",
        "plot_id",
        "survey_id",
        "gate_unit",
        "attribute_unit",
        "vertical_axis",
        "source_profiles",
        "processing_signature",
        "calibration",
        "method",
        "support_reference",
    }
    if required - set(grid.variables) or attrs - grid.attrs.keys():
        raise ValueError("Incomplete attribute grid contract")
    if grid.attrs["schema_version"] != "0.1" or grid.attrs["gate_unit"] not in ("ns", "m"):
        raise ValueError("Unsupported grid schema or gate unit")
    from pyproj import CRS

    crs = CRS.from_user_input(grid.attrs["crs"])
    if not crs.is_projected or any(
        a.unit_name.lower() not in ("metre", "meter") for a in crs.axis_info[:2]
    ):
        raise ValueError("Grids require a projected metric CRS")
    if grid.attrs["method"] not in ("nearest", "linear"):
        raise ValueError("Unknown gridding method")
    if grid.attrs["vertical_axis"] not in ("recording_time", "corrected_time", "depth"):
        raise ValueError("Unknown grid vertical axis")
    if (grid.attrs["vertical_axis"] == "depth") != (grid.attrs["gate_unit"] == "m"):
        raise ValueError("Grid vertical axis and gate units conflict")
    if grid.attrs["vertical_axis"] == "depth" and not grid.attrs["calibration"]:
        raise ValueError("Depth grids require calibration")
    if not grid.attrs["attribute_unit"] or any(
        grid[v].attrs.get("units") != grid.attrs["attribute_unit"]
        for v in ("value", "nearest_value")
    ):
        raise ValueError("Incompatible grid attribute units")
    if not np.isfinite(grid.attrs["origin_xy_m"]).all() or len(grid.attrs["origin_xy_m"]) != 2:
        raise ValueError("Grid origin must contain two finite coordinates")
    for name in ("x", "y", "gate_start", "gate_stop"):
        if not np.isfinite(grid[name].values).all():
            raise ValueError("Grid coordinates must be finite")
    if any(grid[name].dims != ("gate",) for name in ("gate_start", "gate_stop")):
        raise ValueError("Gate bounds must have dimensions (gate,)")
    for name in ("x", "y"):
        if grid[name].dims != (name,) or np.any(np.diff(grid[name].values) <= 0):
            raise ValueError("Grid horizontal axes must be strictly increasing")
    for name in required - {"support_index", "gate_start", "gate_stop"}:
        if grid[name].dims != ("gate", "y", "x"):
            raise ValueError(f"Invalid grid dimensions for {name}")
    if grid.support_index.dims != ("gate", "y", "x", "vertex"):
        raise ValueError("Invalid grid contributor dimensions")
    valid = grid.valid_support.values
    if valid.dtype != np.bool_ or not np.array_equal(np.isfinite(grid.value.values), valid):
        raise ValueError("Grid support mask must equal finite value mask")
    if not np.array_equal(np.isfinite(grid.nearest_value.values), valid):
        raise ValueError("Baseline and candidate must have identical support")
    if np.any(grid.contributor_count.values[valid] <= 0):
        raise ValueError("Supported cells require contributors")
    if np.any(grid.contributor_count.values[~valid] != 0):
        raise ValueError("Unsupported cells cannot have contributor counts")
    if np.any(grid.nearest_support_index.values[~valid] != -1):
        raise ValueError("Unsupported cells cannot reference nearest contributors")
    if np.any(grid.nearest_support_index.values[valid] < 0):
        raise ValueError("Supported cells require a nearest contributor")
    if np.any(~np.isfinite(grid.nearest_distance_m.values[valid])) or np.any(
        grid.nearest_distance_m.values[np.isfinite(grid.nearest_distance_m.values)] < 0
    ):
        raise ValueError("Support distances must be finite and nonnegative where supported")
    if np.any(grid.support_index.values[~valid] != -1):
        raise ValueError("Unsupported cells cannot reference contributors")
    expected = 3 if grid.attrs["method"] == "linear" else 1
    if np.any((grid.support_index.values[valid] >= 0).sum(axis=1) != expected):
        raise ValueError("Contributor references conflict with interpolation method")
    if np.any(grid.gate_start.values >= grid.gate_stop.values):
        raise ValueError("Grid gates must have ordered bounds")


def evaluate_lines(observations_by_gate, domain, options: GridOptions, origin):
    """Rebuild bins and triangulations per fold; no held-out trace enters fitting."""
    reports = []
    for gate, observations in enumerate(observations_by_gate):
        lines = sorted({r["line_id"] for r in observations})
        for line in lines:
            held = [r for r in observations if r["line_id"] == line]
            train = [r for r in observations if r["line_id"] != line]
            support = aggregate_support(train, options.bin_m, origin)
            support = [s for s in support if inside_domain(domain, [s["xy"]])[0]]
            result = interpolate_support(support, [r["xy"] for r in held], domain, options)
            valid = result["valid_support"]
            truth = np.array([r["value"] for r in held])[valid]
            metrics = {}
            for method, key in ((options.method, "value"), ("nearest", "nearest_value")):
                error = result[key][valid] - truth
                metrics[method] = {
                    "mae": float(np.mean(np.abs(error))) if len(error) else None,
                    "rmse": float(np.sqrt(np.mean(error**2))) if len(error) else None,
                    "bias": float(np.mean(error)) if len(error) else None,
                }
            reports.append(
                {
                    "gate": gate,
                    "held_out_line": line,
                    "training_lines": len(lines) - 1,
                    "held_out_count": len(held),
                    "supported_count": int(valid.sum()),
                    "supported_fraction": float(valid.mean()),
                    "metrics": metrics,
                }
            )
    return reports
