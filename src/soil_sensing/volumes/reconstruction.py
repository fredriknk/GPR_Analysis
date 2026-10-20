"""Bounded vertical blocks with reusable horizontal, validity-specific stencils."""

import json
from dataclasses import asdict, dataclass, replace

import numpy as np
import xarray as xr
from shapely.geometry import shape

from soil_sensing.spatial.gridding import (
    GridOptions,
    aggregate_support,
    grid_coordinates,
    inside_domain,
)
from soil_sensing.spatial.interpolation import interpolate_support
from soil_sensing.volumes.contracts import ATTRIBUTES, Reason, validate_volume


@dataclass(frozen=True)
class VolumeOptions:
    minimum_lines: int = 2
    block_samples: int = 16
    max_memory_mb: float = 512.0
    max_stencil_patterns: int = 128

    def __post_init__(self):
        for name in ("minimum_lines", "block_samples", "max_stencil_patterns"):
            if type(getattr(self, name)) is not int or getattr(self, name) < (
                2 if name == "minimum_lines" else 1
            ):
                raise ValueError(f"Invalid {name}")
        if (
            isinstance(self.max_memory_mb, bool)
            or not np.isfinite(self.max_memory_mb)
            or self.max_memory_mb <= 0
        ):
            raise ValueError("max_memory_mb must be positive and finite")


class Reconstruction:
    """Coordinates/attributes are full native data; previews never enter this class."""

    def __init__(
        self,
        rows,
        attributes,
        vertical,
        native_samples,
        boundary,
        domain,
        metadata,
        grid=None,
        options=None,
    ):
        grid, options = grid or GridOptions(), options or VolumeOptions()
        if (
            not len(vertical)
            or set(attributes) != set(ATTRIBUTES)
            or any(np.asarray(v).shape != (len(rows), len(vertical)) for v in attributes.values())
        ):
            raise ValueError(
                "Attributes must be native (trace,vertical) arrays with a nonempty axis"
            )
        self.rows, self.attributes = rows, attributes
        self.vertical, self.native_samples = np.asarray(vertical), np.asarray(native_samples)
        self.domain, self.metadata, self.grid, self.options = domain, metadata, grid, options
        self.x, self.y = grid_coordinates(boundary, grid, len(vertical))
        # Arrays, stencils, both serialization copies and cached query plans.
        n, b = len(self.x) * len(self.y), min(options.block_samples, len(vertical))
        native_bytes = sum(v.nbytes for v in attributes.values())
        self.estimated_memory_bytes = int(
            native_bytes + n * b * 3 * 130 * 2 + n * options.max_stencil_patterns * 100
        )
        if self.estimated_memory_bytes > options.max_memory_mb * 1_000_000:
            raise ValueError(
                "Volume exceeds max_memory_mb before allocation; "
                "reduce block_samples/grid size/stencil cache"
            )
        xx, yy = np.meshgrid(self.x, self.y)
        self.query = np.c_[xx.ravel(), yy.ravel()]
        self.inside = inside_domain(domain, self.query)
        self.table, self.patterns = [], {}

    def _stencil(self, valid):
        key = np.packbits(valid).tobytes()
        if key in self.patterns:
            return self.patterns[key]
        if len(self.patterns) >= self.options.max_stencil_patterns:
            raise ValueError("Too many validity stencils; raise the explicit cache/memory budget")
        observations = [
            {**self.rows[i], "value": 0.0, "trace_id": str(i)} for i in np.flatnonzero(valid)
        ]
        bins = aggregate_support(observations, self.grid.bin_m, [0, 0])
        bins = [b for b in bins if inside_domain(self.domain, [b["xy"]])[0]]
        result = interpolate_support(
            bins, self.query, self.domain, replace(self.grid, method="linear")
        )
        ids = result["support_index"].copy()
        weights = np.zeros(ids.shape, dtype=float)
        line_count = np.zeros(len(ids), dtype=np.int16)
        direction_count = np.zeros(len(ids), dtype=np.int16)
        interpolated_fraction = np.full(len(ids), np.nan)
        supported = result["valid_support"].copy()
        xy = np.array([b["xy"] for b in bins])
        selected = np.flatnonzero(supported)
        if len(selected):
            if self.grid.method != "nearest":
                triangles = xy[ids[selected]]
                matrix = np.concatenate(
                    [triangles.transpose(0, 2, 1), np.ones((len(selected), 1, 3))], axis=1
                )
                weights[selected] = np.linalg.solve(
                    matrix, np.c_[self.query[selected], np.ones(len(selected))][..., None]
                )[..., 0]
            for index in selected:
                chosen = [bins[j] for j in ids[index] if j >= 0]
                members = {int(i) for b in chosen for i in b["trace_ids"]}
                line_count[index] = len({self.rows[i]["line_id"] for i in members})
                direction_count[index] = len({self.rows[i]["direction_group"] for i in members})
                interpolated_fraction[index] = np.mean(
                    [self.rows[i]["position_status"] == "interpolated" for i in members]
                )
        if self.grid.method == "nearest":
            ids[:] = -1
            ids[selected, 0] = result["nearest_support_index"][selected]
            weights[selected, 0] = 1
        supported &= line_count >= self.options.minimum_lines
        reason = np.full(len(ids), int(Reason.GEOMETRIC_SUPPORT), dtype=np.uint16)
        reason[~self.inside] = int(Reason.OUTSIDE_DOMAIN)
        reason[result["valid_support"] & ~supported] = int(Reason.TOO_FEW_LINES)
        reason[supported] = 0
        ids[~supported], weights[~supported] = -1, 0
        line_count[~supported], direction_count[~supported] = 0, 0
        interpolated_fraction[~supported] = np.nan
        base = len(self.table)
        for b in bins:
            members = [int(i) for i in b["trace_ids"]]
            self.table.append(
                {
                    "bin_id": len(self.table),
                    "xy": b["xy"],
                    "line_ids": b["line_ids"],
                    "members": [
                        {
                            **{
                                k: self.rows[i][k]
                                for k in (
                                    "source_id",
                                    "profile_id",
                                    "trace_id",
                                    "position_status",
                                    "quality_flags",
                                    "time_utc",
                                )
                            },
                            "weight": 1 / len(members),
                        }
                        for i in members
                    ],
                }
            )
        global_ids = np.where(ids >= 0, ids + base, -1)
        stencil = dict(
            bins=bins,
            ids=global_ids,
            local_ids=ids,
            weights=weights,
            valid=supported,
            reason=reason,
            nearest=result["nearest_support_index"],
            distance=result["nearest_distance_m"],
            line_count=line_count,
            direction_count=direction_count,
            interpolated_fraction=interpolated_fraction,
        )
        if len(self.patterns) < self.options.max_stencil_patterns:
            self.patterns[key] = stencil
        return stencil

    def blocks(self):
        for start in range(0, len(self.vertical), self.options.block_samples):
            stop = min(start + self.options.block_samples, len(self.vertical))
            values, supports = {}, {}
            for name in ATTRIBUTES:
                planes, nearest_planes, reasons, stencils = [], [], [], []
                for sample in range(start, stop):
                    data = self.attributes[name][:, sample]
                    valid = np.isfinite(data) & np.array(
                        [r["located"] for r in self.rows], dtype=bool
                    )
                    plan = self._stencil(valid)
                    means = np.array(
                        [np.mean(data[np.asarray(b["trace_ids"], int)]) for b in plan["bins"]]
                    )
                    plane, nearest = (
                        np.full(len(self.query), np.nan),
                        np.full(len(self.query), np.nan),
                    )
                    mask = plan["valid"]
                    if mask.any():
                        plane[mask] = np.sum(
                            means[np.maximum(plan["local_ids"][mask], 0)] * plan["weights"][mask],
                            axis=1,
                        )
                        nearest[mask] = means[plan["nearest"][mask]]
                    reason = plan["reason"].copy()
                    if not valid.any():
                        reason[self.inside] = int(Reason.INPUT_QUALITY)
                    planes.append(plane)
                    nearest_planes.append(nearest)
                    reasons.append(reason)
                    stencils.append(plan)
                dims, shape_ = ("vertical", "y", "x"), (stop - start, len(self.y), len(self.x))
                values[name] = (dims, np.array(planes).reshape(shape_))
                values[name + "_nearest"] = (dims, np.array(nearest_planes).reshape(shape_))
                values[name + "_valid"] = (dims, np.isfinite(values[name][1]))
                values[name + "_reason"] = (dims, np.array(reasons, np.uint16).reshape(shape_))
                for suffix, key_ in (
                    ("bin", "ids"),
                    ("weight", "weights"),
                    ("line_count", "line_count"),
                    ("distance_m", "distance"),
                    ("direction_count", "direction_count"),
                    ("interpolated_position_fraction", "interpolated_fraction"),
                ):
                    extra = suffix in ("bin", "weight")
                    supports[name + "_" + suffix] = (
                        dims + (("vertex",) if extra else ()),
                        np.array([s[key_] for s in stencils]).reshape(
                            shape_ + ((3,) if extra else ())
                        ),
                    )
            coords = dict(
                x=self.x,
                y=self.y,
                vertical=self.vertical[start:stop],
                native_sample=("vertical", self.native_samples[start:stop]),
            )
            edges = (
                np.r_[
                    self.vertical[0] - (self.vertical[1] - self.vertical[0]) / 2,
                    (self.vertical[:-1] + self.vertical[1:]) / 2,
                    self.vertical[-1] + (self.vertical[-1] - self.vertical[-2]) / 2,
                ]
                if len(self.vertical) > 1
                else np.array([self.vertical[0] - 0.5, self.vertical[0] + 0.5])
            )
            coords["vertical_lower"] = ("vertical", edges[start:stop])
            coords["vertical_upper"] = ("vertical", edges[start + 1 : stop + 1])
            cube = xr.Dataset(
                values,
                coords=coords,
                attrs={
                    **self.metadata,
                    "schema_version": "1.0",
                    "grid_options": asdict(self.grid),
                    "minimum_lines": self.options.minimum_lines,
                    "domain_geojson": json.loads(json.dumps(self.domain.__geo_interface__)),
                    "vertical_direction": "positive_down",
                    "support_reference": "support.zarr",
                    "contributor_reference": "contributors.json",
                    "all_voxels_are_estimates": True,
                    "uncertainty_status": "not_estimated; support diagnostics are not confidence",
                },
            )
            for name in ATTRIBUTES:
                cube[name].attrs["units"] = cube[name + "_nearest"].attrs["units"] = self.metadata[
                    "attribute_unit"
                ]
            cube.vertical.attrs.update(units=self.metadata["vertical_unit"], positive="down")
            cube.x.attrs["units"] = cube.y.attrs["units"] = "m"
            support = xr.Dataset(
                supports,
                coords={**coords, "vertex": [0, 1, 2]},
                attrs={"schema_version": "1.0", "selection_hash": self.metadata["selection_hash"]},
            )
            validate_volume(cube, support)
            yield cube, support


def evaluate_volume(
    rows,
    attributes,
    vertical,
    native_samples,
    boundary,
    domain,
    metadata,
    grid,
    options,
    *,
    sample_step=16,
    buffer_m=0,
):
    """Whole-line holdouts; bins, masks and triangulations rebuilt on training only."""
    from soil_sensing.spatial.interpolation import interpolate_support

    reports = []
    chosen = np.arange(0, len(vertical), sample_step)
    for held_line in sorted({r["line_id"] for r in rows}):
        held_indices = np.array(
            [i for i, r in enumerate(rows) if r["line_id"] == held_line and r["located"]], int
        )
        train_indices = np.array(
            [i for i, r in enumerate(rows) if r["line_id"] != held_line and r["located"]], int
        )
        if buffer_m and len(held_indices) and len(train_indices):
            from scipy.spatial import cKDTree

            distances = cKDTree([rows[i]["xy"] for i in held_indices]).query(
                [rows[i]["xy"] for i in train_indices]
            )[0]
            train_indices = train_indices[distances > buffer_m]
        training_domain = domain
        if metadata.get("track_buffer_m"):
            from shapely import LineString, unary_union

            available = set(map(int, train_indices))
            segments = [
                LineString([rows[i - 1]["xy"], rows[i]["xy"]]).buffer(
                    metadata["track_buffer_m"], cap_style="flat"
                )
                for i in available
                if i - 1 in available
                and rows[i]["line_id"] == rows[i - 1]["line_id"]
                and np.linalg.norm(np.asarray(rows[i]["xy"]) - rows[i - 1]["xy"])
                <= metadata["track_gap_m"]
            ]
            training_domain = domain.intersection(unary_union(segments))
        for sample in chosen:
            data = attributes["envelope"][:, sample]
            train = [
                {**rows[i], "value": float(data[i])} for i in train_indices if np.isfinite(data[i])
            ]
            held = [i for i in held_indices if np.isfinite(data[i])]
            if not held:
                continue
            bins = aggregate_support(train, grid.bin_m, [0, 0])
            bins = [b for b in bins if inside_domain(training_domain, [b["xy"]])[0]]
            result = interpolate_support(
                bins, [rows[i]["xy"] for i in held], training_domain, replace(grid, method="linear")
            )
            valid = result["valid_support"]
            for j in np.flatnonzero(valid):
                ids = result["support_index"][j]
                valid[j] = (
                    len({line for index in ids if index >= 0 for line in bins[index]["line_ids"]})
                    >= options.minimum_lines
                )
            metrics = {}
            for label, key in (("candidate", "value"), ("nearest", "nearest_value")):
                if label == "candidate" and grid.method == "nearest":
                    key = "nearest_value"
                error = result[key][valid] - data[np.asarray(held)][valid]
                metrics[label] = {
                    "mae": float(np.abs(error).mean()) if len(error) else None,
                    "rmse": float(np.sqrt(np.mean(error**2))) if len(error) else None,
                }
            reports.append(
                dict(
                    held_out_line=held_line,
                    native_sample=int(native_samples[sample]),
                    held_out_count=len(held),
                    supported_count=int(valid.sum()),
                    supported_fraction=float(valid.mean()),
                    buffer_m=buffer_m,
                    metrics=metrics,
                )
            )
    return dict(
        schema_version="1.0",
        attribute="envelope",
        unit=metadata["attribute_unit"],
        fold_unit="whole_line",
        sample_step=sample_step,
        field_validation=False,
        folds=reports,
    )


def domain_from_metadata(metadata):
    return shape(metadata["domain_geojson"])
