"""Metric support bins and polygon masks for quantitative radar attributes."""

from dataclasses import dataclass

import numpy as np
from shapely import Polygon, points, unary_union


@dataclass(frozen=True)
class GridOptions:
    spacing_m: float = 1.0
    bin_m: float = 2.0
    max_distance_m: float = 25.0
    max_edge_m: float = 28.0
    method: str = "linear"
    max_cells: int = 2_000_000

    def __post_init__(self):
        for name in ("spacing_m", "bin_m", "max_distance_m", "max_edge_m"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.method not in ("linear", "nearest"):
            raise ValueError("Interpolation method must be linear or nearest")
        if type(self.max_cells) is not int or self.max_cells <= 0:
            raise ValueError("max_cells must be a positive integer")


def polygon_domain(boundary, *, exclusions=(), regions=()):
    """Explicit survey regions intersect the boundary; exclusion rings make holes."""

    def polygon(ring):
        try:
            shape = Polygon(ring)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid support polygon") from exc
        if not shape.is_valid or shape.is_empty or shape.area <= 0:
            raise ValueError("Support polygons must be valid with positive area")
        if not np.isfinite(np.asarray(shape.exterior.coords)).all():
            raise ValueError("Support polygon coordinates must be finite")
        return shape

    domain = polygon(boundary)
    if regions:
        domain = domain.intersection(unary_union([polygon(r) for r in regions]))
    if exclusions:
        domain = domain.difference(unary_union([polygon(r) for r in exclusions]))
    return domain


def inside_domain(domain, xy):
    """Include boundary points, exclude points in holes or outside survey regions."""
    xy = np.asarray(xy, dtype=float)
    return np.asarray(domain.covers(points(xy)), dtype=bool)


def grid_coordinates(boundary, options: GridOptions, gates: int):
    """Cell centres; budget is checked before allocating a dense mesh."""
    polygon = Polygon(boundary)
    xmin, ymin, xmax, ymax = polygon.bounds
    nx, ny = (int(np.ceil(v / options.spacing_m)) for v in (xmax - xmin, ymax - ymin))
    if nx * ny * gates > options.max_cells:
        raise ValueError("Grid exceeds max_cells; increase spacing_m or reduce gates")
    if nx < 1 or ny < 1:
        raise ValueError("Empty grid extent")
    return (
        xmin + (np.arange(nx) + 0.5) * options.spacing_m,
        ymin + (np.arange(ny) + 0.5) * options.spacing_m,
    )


def aggregate_support(observations: list[dict], bin_m: float, origin):
    """Mean within each line/bin, then mean exact duplicate bin locations.

    Bin IDs are local to one gate. Contributors retain line and source trace IDs.
    Call separately on training lines: aggregation must never precede a holdout.
    """
    bins = {}
    for row in observations:
        key = (row["line_id"], *np.floor((np.array(row["xy"]) - origin) / bin_m).astype(int))
        bins.setdefault(key, []).append(row)
    locations = {}
    for rows in bins.values():
        xy = tuple(np.mean([r["xy"] for r in rows], axis=0))
        locations.setdefault(xy, []).extend(rows)
    result = []
    for xy, rows in sorted(locations.items()):
        result.append(
            {
                "xy": list(xy),
                "value": float(np.mean([r["value"] for r in rows])),
                "count": len(rows),
                "trace_ids": [r["trace_id"] for r in rows],
                "line_ids": sorted({r["line_id"] for r in rows}),
                "position_status": sorted({r["position_status"] for r in rows}),
            }
        )
    return result
