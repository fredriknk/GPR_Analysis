"""Reviewed plot polygons and conservative trace coverage summaries."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xarray as xr

from soil_sensing.profile import validate_profile


@dataclass(frozen=True)
class PlotGeometry:
    plot_id: str
    status: str
    crs: str | None
    polygon: tuple[tuple[float, float], ...] | None
    evidence: str | None


def _area(points: np.ndarray) -> float:
    return float(
        abs(
            np.dot(points[:, 0], np.roll(points[:, 1], -1))
            - np.dot(points[:, 1], np.roll(points[:, 0], -1))
        )
        / 2
    )


def _intersects(a, b, c, d) -> bool:
    def cross(x, y, z):
        u, v = y - x, z - x
        return u[0] * v[1] - u[1] * v[0]

    return bool(cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0)


def load_plots(path: Path) -> dict[str, PlotGeometry]:
    source = tomllib.loads(path.read_text(encoding="utf-8"))
    if source.get("schema_version") != "0.1" or set(source) - {
        "schema_version",
        "project_id",
        "plots",
    }:
        raise ValueError("Invalid plot configuration schema")
    plots = {}
    for row in source.get("plots", []):
        allowed = {
            "plot_id",
            "width_m",
            "length_m",
            "geometry_status",
            "crs",
            "polygon",
            "evidence",
        }
        if set(row) - allowed:
            raise ValueError("Unknown plot geometry field")
        identifier = row.get("plot_id")
        if not isinstance(identifier, str) or not identifier or identifier in plots:
            raise ValueError("Plot IDs must be unique, nonempty strings")
        if row.get("width_m", 0) <= 0 or row.get("length_m", 0) <= 0:
            raise ValueError(f"Invalid plot dimensions: {identifier}")
        status = row.get("geometry_status")
        if status not in ("not_supplied", "reviewed"):
            raise ValueError(f"Invalid geometry_status: {identifier}")
        polygon = row.get("polygon")
        if status == "reviewed":
            if not row.get("crs") or not row.get("evidence") or not polygon:
                raise ValueError("Reviewed geometry requires CRS, polygon and evidence")
            points = np.asarray(polygon, dtype=float)
            if (
                points.ndim != 2
                or points.shape[1] != 2
                or len(points) < 4
                or not np.isfinite(points).all()
            ):
                raise ValueError("Plot polygon requires at least four finite [x,y] vertices")
            if np.array_equal(points[0], points[-1]):
                points = points[:-1]
            if len(points) < 4 or _area(points) <= 0:
                raise ValueError("Degenerate plot polygon")
            from pyproj import CRS

            plot_crs = CRS.from_user_input(row["crs"])
            if not plot_crs.is_projected or plot_crs.axis_info[0].unit_name.lower() not in (
                "metre",
                "meter",
            ):
                raise ValueError("Plot CRS must be projected in metres")
            expected_area = row["width_m"] * row["length_m"]
            if not 0.8 * expected_area <= _area(points) <= 1.2 * expected_area:
                raise ValueError("Plot polygon area conflicts with declared dimensions")
            for i in range(len(points)):
                for j in range(i + 1, len(points)):
                    if j in (i, (i + 1) % len(points)) or i == (j + 1) % len(points):
                        continue
                    if _intersects(
                        points[i],
                        points[(i + 1) % len(points)],
                        points[j],
                        points[(j + 1) % len(points)],
                    ):
                        raise ValueError("Self-intersecting plot polygon")
            polygon = tuple(map(tuple, points.tolist()))
        elif polygon or row.get("crs"):
            raise ValueError("Unreviewed plot geometry cannot supply polygon or CRS")
        plots[identifier] = PlotGeometry(
            identifier, status, row.get("crs"), polygon, row.get("evidence")
        )
    if not plots:
        raise ValueError("No plots configured")
    return plots


def _inside(x: float, y: float, points: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    for i, (xa, ya) in enumerate(points):
        xb, yb = points[(i + 1) % len(points)]
        if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / (yb - ya) + xa:
            inside = not inside
    return inside


def coverage_report(profile: xr.Dataset, plots: dict[str, PlotGeometry]) -> dict:
    validate_profile(profile)
    identifier = profile.attrs["plot_id"]
    if not identifier:
        return {"status": "unassigned", "profile_id": profile.attrs["profile_id"]}
    if identifier not in plots:
        raise ValueError(f"Unknown assigned plot: {identifier}")
    plot = plots[identifier]
    if plot.status != "reviewed":
        return {"status": "geometry_missing", "plot_id": identifier}
    if profile.attrs["target_crs"] != plot.crs:
        raise ValueError("Profile and plot projected CRS must match exactly")
    x, y = profile.x_m.values, profile.y_m.values
    located = np.isfinite(x) & np.isfinite(y)
    inside = np.array(
        [
            _inside(a, b, plot.polygon) if valid else False
            for a, b, valid in zip(x, y, located, strict=True)
        ]
    )
    return {
        "status": "reviewed",
        "plot_id": identifier,
        "crs": plot.crs,
        "polygon_evidence": plot.evidence,
        "trace_count": profile.sizes["trace"],
        "located_traces": int(located.sum()),
        "inside_traces": int(inside.sum()),
        "outside_traces": int((located & ~inside).sum()),
        "unlocated_traces": int((~located).sum()),
    }
