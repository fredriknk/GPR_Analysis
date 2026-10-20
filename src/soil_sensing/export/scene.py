"""Immutable-input scene runs, lossless grids, provenance and offline HTML export."""

import hashlib
import json
import platform
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import xarray as xr

from soil_sensing import __version__
from soil_sensing.ingest import code_hash, tree_hash, write_json
from soil_sensing.inventory import sha256_file
from soil_sensing.io.storage import load_profile
from soil_sensing.spatial.gridding import GridOptions
from soil_sensing.spatial.interpolation import build_grid, evaluate_lines, validate_grid
from soil_sensing.spatial.plots import load_plots
from soil_sensing.visualization.plotly_viewer import write_viewer
from soil_sensing.visualization.scene import build_scene, validate_gates
from soil_sensing.visualization.synthetic import synthetic_plots


@dataclass(frozen=True)
class RenderOptions:
    trace_step: int = 2
    sample_step: int = 2
    max_track_gap_m: float = 6.0
    max_curtain_vertices: int = 150_000
    max_measured_points: int = 100_000
    max_input_samples: int = 10_000_000
    max_payload_mb: float = 30.0

    def __post_init__(self):
        for name in (
            "trace_step",
            "sample_step",
            "max_curtain_vertices",
            "max_measured_points",
            "max_input_samples",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("max_track_gap_m", "max_payload_mb"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


def load_scene_config(path: Path):
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version",
        "synthetic",
        "diagnostic",
        "diagnostic_layout",
        "diagnostic_cluster_distance_m",
        "diagnostic_partitions",
        "profiles",
        "plots_file",
        "vertical_axis",
        "gates",
        "grid",
        "render",
        "exclusions",
        "regions",
    }
    if data.get("schema_version") != "0.1" or set(data) - allowed:
        raise ValueError("Invalid visualization schema or unknown fields")
    for name in ("synthetic", "diagnostic"):
        if type(data.get(name, False)) is not bool:
            raise ValueError(f"{name} must be Boolean")
    synthetic, diagnostic = data.get("synthetic", False), data.get("diagnostic", False)
    diagnostic_layout = data.get("diagnostic_layout", "odometer")
    if diagnostic_layout not in ("odometer", "gps"):
        raise ValueError("diagnostic_layout must be odometer or gps")
    if diagnostic_layout != "odometer" and not diagnostic:
        raise ValueError("diagnostic_layout requires diagnostic=true")
    cluster_distance = data.get("diagnostic_cluster_distance_m", 1000.0)
    if (
        isinstance(cluster_distance, bool)
        or not isinstance(cluster_distance, (int, float))
        or not np.isfinite(cluster_distance)
        or cluster_distance <= 0
    ):
        raise ValueError("diagnostic_cluster_distance_m must be finite and positive")
    if "plots_file" in data and (not isinstance(data["plots_file"], str) or not data["plots_file"]):
        raise ValueError("plots_file must be a nonempty path string")
    if synthetic and (
        diagnostic
        or data.get("profiles")
        or data.get("plots_file")
        or data.get("exclusions")
        or data.get("regions")
    ):
        raise ValueError("Synthetic mode cannot reference field inputs or geometry")
    stores = data.get("profiles", [])
    if not isinstance(stores, list) or any(not isinstance(v, str) or not v for v in stores):
        raise ValueError("profiles must list Zarr store paths")
    if not synthetic and not stores:
        raise ValueError("Supply profiles or explicitly enable synthetic mode")
    if not synthetic and not diagnostic and not data.get("plots_file"):
        raise ValueError("Plot scenes require plots_file with reviewed geometry")
    if diagnostic and (data.get("plots_file") or data.get("exclusions") or data.get("regions")):
        raise ValueError("Diagnostic scenes cannot use geographic plot geometry")
    partitions = data.get("diagnostic_partitions", [])
    if not isinstance(partitions, list):
        raise ValueError("diagnostic_partitions must be a list of tables")
    if partitions and (not diagnostic or diagnostic_layout != "gps"):
        raise ValueError("diagnostic_partitions require diagnostic GPS layout")
    partition_ids, partition_lines = set(), set()
    for partition in partitions:
        if not isinstance(partition, dict) or set(partition) != {
            "partition_id",
            "label",
            "default_visible",
            "lines",
        }:
            raise ValueError("Each diagnostic partition requires id, label, visibility and lines")
        if (
            not isinstance(partition["partition_id"], str)
            or not partition["partition_id"]
            or partition["partition_id"] in partition_ids
            or not isinstance(partition["label"], str)
            or not partition["label"]
            or type(partition["default_visible"]) is not bool
            or not isinstance(partition["lines"], list)
            or not partition["lines"]
        ):
            raise ValueError("Invalid diagnostic partition metadata")
        partition_ids.add(partition["partition_id"])
        for line in partition["lines"]:
            if (
                not isinstance(line, list)
                or len(line) != 2
                or any(not isinstance(value, str) or not value for value in line)
                or tuple(line) in partition_lines
            ):
                raise ValueError("Diagnostic partition lines must be unique source/profile pairs")
            partition_lines.add(tuple(line))
    if data.get("vertical_axis", "recording_time") not in (
        "recording_time",
        "corrected_time",
        "depth",
    ):
        raise ValueError("Invalid vertical_axis")
    validate_gates(data.get("gates"))
    for key, cls in (("grid", GridOptions), ("render", RenderOptions)):
        values = data.get(key, {})
        if not isinstance(values, dict) or set(values) - cls.__dataclass_fields__.keys():
            raise ValueError(f"Unknown {key} option")
    for key in ("exclusions", "regions"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"{key} must be a list of polygon tables")
        for shape in data.get(key, []):
            if (
                not isinstance(shape, dict)
                or set(shape) != {"plot_id", "polygon", "evidence"}
                or not isinstance(shape["plot_id"], str)
                or not shape["plot_id"]
                or not isinstance(shape["evidence"], str)
                or not shape["evidence"]
            ):
                raise ValueError(f"{key} require plot_id, polygon and evidence")
    resolved = [(path.parent / p).resolve() for p in stores]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate input stores")
    data["profiles"] = resolved
    data["diagnostic_layout"] = diagnostic_layout
    data["diagnostic_cluster_distance_m"] = float(cluster_distance)
    data["diagnostic_partitions"] = partitions
    if data.get("plots_file"):
        data["plots_file"] = (path.parent / data["plots_file"]).resolve()
    return data, GridOptions(**data.get("grid", {})), RenderOptions(**data.get("render", {}))


def _gps_profile_center(profile):
    if "latitude_deg" not in profile or "longitude_deg" not in profile:
        raise ValueError("GPS-layout diagnostics require parsed latitude/longitude")
    valid = np.isfinite(profile.latitude_deg.values) & np.isfinite(profile.longitude_deg.values)
    valid &= np.isin(profile.position_status.values, ["measured", "interpolated"])
    if not valid.any():
        raise ValueError("Every GPS-layout line requires an accepted position")
    return np.array(
        [
            np.nanmedian(profile.latitude_deg.values[valid]),
            np.nanmedian(profile.longitude_deg.values[valid]),
        ]
    )


def _gps_distance_m(left, right):
    latitude = np.deg2rad((left[0] + right[0]) / 2)
    return float(
        np.hypot(
            (left[0] - right[0]) * 111_320.0,
            (left[1] - right[1]) * 111_320.0 * np.cos(latitude),
        )
    )


def suggest_diagnostic_partitions(
    lines, *, max_line_length_m=15.0, center_distance_m=20.0, min_lines=4
):
    """Suggest short, dense line groups for diagnostic display, never plot identity."""
    if (
        not np.isfinite(max_line_length_m)
        or max_line_length_m <= 0
        or not np.isfinite(center_distance_m)
        or center_distance_m <= 0
        or type(min_lines) is not int
        or min_lines < 2
    ):
        raise ValueError("Diagnostic partition limits must be finite and positive")
    candidates = [
        index
        for index, line in enumerate(lines)
        if Path(line["profile_id"]).name.upper().startswith(("XLINE", "YLINE"))
        and line["path_length_m"] <= max_line_length_m
    ]
    unseen, components = set(candidates), []
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        indices, frontier = {seed}, [seed]
        while frontier:
            left = frontier.pop()
            left_center = (
                lines[left]["center_latitude_deg"],
                lines[left]["center_longitude_deg"],
            )
            neighbours = set()
            for right in unseen:
                right_center = (
                    lines[right]["center_latitude_deg"],
                    lines[right]["center_longitude_deg"],
                )
                if _gps_distance_m(left_center, right_center) <= center_distance_m:
                    neighbours.add(right)
            unseen -= neighbours
            indices |= neighbours
            frontier.extend(neighbours)
        if len(indices) >= min_lines:
            components.append(sorted(indices))
    components.sort(key=lambda group: (-len(group), group[0]))
    partitions, assigned = [], set()
    for number, group in enumerate(components, 1):
        assigned.update(group)
        partitions.append(
            {
                "partition_id": f"grid-{number:02d}",
                "label": f"Grid partition {number}",
                "default_visible": True,
                "lines": [
                    [lines[index]["source_id"], lines[index]["profile_id"]] for index in group
                ],
            }
        )
    other = [
        [line["source_id"], line["profile_id"]]
        for index, line in enumerate(lines)
        if index not in assigned
    ]
    if other:
        partitions.append(
            {
                "partition_id": "other-lines",
                "label": "Other / traverse lines",
                "default_visible": False,
                "lines": other,
            }
        )
    return partitions


def _gps_diagnostic_groups(profiles, cluster_distance_m):
    """Connected components of profile centres for display-only site separation."""
    centres = [_gps_profile_center(profile) for profile in profiles]
    unseen = set(range(len(profiles)))
    groups = []
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        indices, frontier = {seed}, [seed]
        while frontier:
            left = frontier.pop()
            neighbours = {
                right
                for right in unseen
                if _gps_distance_m(centres[left], centres[right]) <= cluster_distance_m
            }
            unseen -= neighbours
            indices |= neighbours
            frontier.extend(neighbours)
        groups.append([profiles[index] for index in sorted(indices)])
    return sorted(groups, key=len, reverse=True)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _scene_payload(scene, grid):
    metadata = {
        **scene.grid_metadata(),
        "diagnostic": scene.diagnostic,
        "boundary": scene.boundary,
        "domain_geojson": scene.domain.__geo_interface__,
        "quality": scene.quality,
        "curtains": scene.curtains,
        "observations": scene.observations,
        "ground_plane": "assumed flat relative ground at zero; no elevation correction",
        "scale_bar_m": min(
            20, max(p[0] for p in scene.boundary) - min(p[0] for p in scene.boundary)
        ),
        "grid": None,
    }
    if grid is not None:
        valid = grid.valid_support.values
        metadata["grid"] = {
            "x": grid.x.values.tolist(),
            "y": grid.y.values.tolist(),
            "value": grid.value.values.tolist(),
            "distance": np.where(valid, grid.nearest_distance_m.values, np.nan).tolist(),
            "count": grid.contributor_count.values.tolist(),
            "coverage": valid.mean(axis=(1, 2)).tolist(),
            "support_status": grid.attrs["support_status"],
        }
    return metadata


def visualize_run(config_path: Path, output: Path, *, progress=None):
    """Build reviewed plot scenes or explicit synthetic/odometer diagnostics."""
    started = time.perf_counter()
    config_path, output = config_path.resolve(), output.resolve()
    config, grid_options, render = load_scene_config(config_path)
    inputs = [config_path, *config["profiles"]]
    if config.get("plots_file"):
        inputs.append(config["plots_file"])
    if output.exists() or "gpr_raw_data" in (p.casefold() for p in output.parts):
        raise ValueError("Visualization output must be a new path outside GPR_raw_data")
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in inputs):
        raise ValueError("Output must be separate from all input stores and configurations")
    checksums = {str(p): tree_hash(p) if p.is_dir() else sha256_file(p) for p in inputs}
    output.mkdir(parents=True)
    manifest = {
        "schema_version": "0.1",
        "operation": "visualize",
        "status": "running",
        "started_utc": datetime.now(UTC).isoformat(),
        "tool_version": __version__,
        "code_sha256": code_hash(),
        "inputs": checksums,
        "configuration": _json_safe(
            {
                **config,
                "profiles": [str(p) for p in config["profiles"]],
                **({"plots_file": str(config["plots_file"])} if config.get("plots_file") else {}),
                "grid": asdict(grid_options),
                "render": asdict(render),
            }
        ),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            **{
                p: version(p)
                for p in ("numpy", "scipy", "xarray", "zarr", "pyproj", "plotly", "shapely")
            },
        },
        "artifacts": {},
    }
    write_json(output / "run.json", manifest)
    try:
        if config.get("synthetic"):
            profiles, plots, exclusions = synthetic_plots()
        else:
            # Inspect metadata before loading amplitudes so an oversized batch
            # cannot exhaust memory before its display budgets are checked.
            sizes = []
            for path in config["profiles"]:
                with xr.open_zarr(
                    path, consolidated=True, chunks=None, mask_and_scale=False
                ) as stored:
                    sizes.append((stored.sizes.get("trace", 0), stored.sizes.get("sample", 0)))
            if sum(n * m for n, m in sizes) > render.max_input_samples:
                raise ValueError("Inputs exceed max_input_samples; select fewer lines per run")
            if sum(n for n, _ in sizes) * len(config["gates"]) > render.max_measured_points:
                raise ValueError("Inputs exceed max_measured_points; select fewer lines/gates")
            if (
                sum(
                    int(np.ceil(n / render.trace_step)) * int(np.ceil(m / render.sample_step))
                    for n, m in sizes
                )
                > render.max_curtain_vertices
            ):
                raise ValueError("Curtains exceed max_curtain_vertices; increase display steps")
            profiles = [load_profile(p) for p in config["profiles"]]
            plots = load_plots(config["plots_file"]) if config.get("plots_file") else {}
            exclusions = {}
        groups, identities = {}, set()
        for profile in profiles:
            identity = (profile.attrs["source_id"], profile.attrs["profile_id"])
            if identity in identities:
                raise ValueError("Duplicate source profile across scene inputs")
            identities.add(identity)
            if not config.get("diagnostic"):
                key = (profile.attrs["plot_id"], profile.attrs["survey_id"])
                groups.setdefault(key, []).append(profile)
        configured_partition_lines = {
            tuple(line)
            for partition in config["diagnostic_partitions"]
            for line in partition["lines"]
        }
        if configured_partition_lines - identities:
            raise ValueError("Diagnostic partitions reference an unselected source profile")
        if config.get("diagnostic"):
            diagnostic_groups = (
                _gps_diagnostic_groups(profiles, config["diagnostic_cluster_distance_m"])
                if config["diagnostic_layout"] == "gps"
                else [[profile] for profile in profiles]
            )
            groups = {(index, None): group for index, group in enumerate(diagnostic_groups)}
        scene_limit = 100 if config.get("diagnostic") else 4
        if not 1 <= len(groups) <= scene_limit:
            raise ValueError(
                f"Select between one and {scene_limit} independent "
                + ("diagnostic lines" if config.get("diagnostic") else "plot/session scenes")
            )
        for key in ("exclusions", "regions"):
            if {s["plot_id"] for s in config.get(key, [])} - {p[0] for p in groups}:
                raise ValueError(f"{key} reference unselected or unknown plots")
        # Reject global allocation and display overload before building the grids.
        estimated_vertices = sum(
            int(np.ceil(p.sizes["trace"] / render.trace_step))
            * int(np.ceil(p.sizes["sample"] / render.sample_step))
            for p in profiles
        )
        if estimated_vertices > render.max_curtain_vertices:
            raise ValueError(
                "Curtains exceed max_curtain_vertices; increase display steps or select fewer lines"
            )
        if (
            sum(p.sizes["trace"] for p in profiles) * len(config["gates"])
            > render.max_measured_points
        ):
            raise ValueError("Measurements exceed max_measured_points; select fewer lines/gates")
        payloads, reports, signatures = [], [], set()
        total_cells, rendered_vertices = 0, 0
        for index, ((plot_id, _), group) in enumerate(groups.items()):
            if progress:
                progress(f"Building scene {index + 1}/{len(groups)}")
            scene = build_scene(
                group,
                plots.get(plot_id),
                config["gates"],
                vertical_axis=config.get("vertical_axis", "recording_time"),
                exclusions=exclusions.get(plot_id, [])
                + [r["polygon"] for r in config.get("exclusions", []) if r["plot_id"] == plot_id],
                regions=[
                    r["polygon"] for r in config.get("regions", []) if r["plot_id"] == plot_id
                ],
                trace_step=render.trace_step,
                sample_step=render.sample_step,
                max_track_gap_m=render.max_track_gap_m,
                synthetic=config.get("synthetic", False),
                diagnostic=config.get("diagnostic", False),
                gps_diagnostic=config.get("diagnostic_layout") == "gps",
                diagnostic_partitions=config["diagnostic_partitions"],
            )
            actual_vertices = sum(len(c["xy"]) * len(c["vertical"]) for c in scene.curtains)
            rendered_vertices += actual_vertices
            if rendered_vertices > render.max_curtain_vertices:
                raise ValueError(
                    "Split curtains exceed max_curtain_vertices; increase display steps"
                )
            if not config.get("synthetic"):
                for source_row, profile in zip(scene.sources, group, strict=True):
                    source_row["input_store"] = next(
                        {"path": str(path), "sha256": checksums[str(path)]}
                        for path, candidate in zip(config["profiles"], profiles, strict=True)
                        if candidate is profile
                    )
            signatures.add((scene.processing_signature, scene.attribute_unit))
            if len(signatures) > 1:
                raise ValueError(
                    "Shared-scale scenes require compatible units and processing settings"
                )
            directory = output / f"scene-{index + 1:02d}"
            directory.mkdir()
            grid = None
            if not scene.diagnostic:
                grid, support = build_grid(
                    scene.observations,
                    scene.boundary,
                    scene.domain,
                    grid_options,
                    {**scene.grid_metadata(), "run_input_sha256": checksums},
                )
                total_cells += grid.value.size
                if total_cells > grid_options.max_cells:
                    raise ValueError(
                        "Combined grids exceed max_cells; increase spacing_m or reduce gates"
                    )
                store = directory / "grid.zarr"
                grid.to_zarr(
                    store,
                    mode="w-",
                    consolidated=True,
                    zarr_format=2,
                    encoding={v: {"_FillValue": None} for v in grid.variables},
                )
                with xr.open_zarr(
                    store, consolidated=True, chunks=None, mask_and_scale=False
                ) as saved:
                    restored = saved.load()
                validate_grid(restored)
                xr.testing.assert_identical(grid, restored)
                write_json(directory / "support.json", {"schema_version": "0.1", "gates": support})
                evaluation = evaluate_lines(scene.observations, scene.domain, grid_options, [0, 0])
                reports.append(
                    {
                        "plot_id": scene.plot_id,
                        "survey_id": scene.survey_id,
                        "unit": scene.attribute_unit,
                        "folds": evaluation,
                    }
                )
                manifest["artifacts"][f"scene-{index + 1:02d}/grid.zarr"] = tree_hash(store)
            payloads.append(_scene_payload(scene, grid))
        amplitude = [
            abs(v)
            for s in payloads
            for c in s["curtains"]
            for row in c["amplitude"]
            for v in row
            if np.isfinite(v)
        ]
        rms = [r["value"] for s in payloads for gate in s["observations"] for r in gate]
        source = json.dumps(checksums, sort_keys=True) + manifest["code_sha256"]
        payload = _json_safe(
            {
                "schema_version": "0.1",
                "scene_id": hashlib.sha256(source.encode()).hexdigest(),
                "scenes": payloads,
                "synthetic": config.get("synthetic", False),
                "vertical_axis": scene.vertical_axis,
                "gate_unit": scene.gate_unit,
                "gates": config["gates"],
                "attribute_unit": scene.attribute_unit,
                "amplitude_limit": max(float(np.percentile(amplitude, 98)), 1) if amplitude else 1,
                "rms_limit": max(rms, default=1) or 1,
                "method": grid_options.method,
                "max_distance_m": grid_options.max_distance_m,
                "view_defaults": {
                    "camera": "oblique",
                    "gate": next(
                        (
                            i
                            for i, (a, b) in enumerate(config["gates"])
                            if config.get("synthetic") and a <= 38 < b
                        ),
                        0,
                    ),
                    "vertical_display_factor": 1,
                },
            }
        )
        serialized = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        from plotly.offline import get_plotlyjs

        payload_bytes = len(serialized.encode()) + len(get_plotlyjs().encode())
        if payload_bytes > render.max_payload_mb * 1024**2:
            raise ValueError(
                "Offline payload exceeds max_payload_mb; increase display steps or grid spacing"
            )
        write_viewer(output, serialized)
        # Dense scene arrays live once in HTML; manifest retains metadata and references.
        scene_manifest = {k: v for k, v in payload.items() if k != "scenes"}
        scene_manifest["scenes"] = [
            {k: v for k, v in s.items() if k not in ("curtains", "observations", "grid")}
            | {
                "grid_store": None if s["diagnostic"] else f"scene-{i + 1:02d}/grid.zarr",
                "support_file": None if s["diagnostic"] else f"scene-{i + 1:02d}/support.json",
            }
            for i, s in enumerate(payloads)
        ]
        write_json(output / "scene.json", _json_safe(scene_manifest))
        write_json(
            output / "evaluation.json",
            {
                "schema_version": "0.1",
                "split": "leave_one_whole_line_out",
                "aggregation_refitted_per_fold": True,
                "plots": reports,
                "limitation": (
                    "Spatial support diagnostics and radar-attribute errors; "
                    "no soil-property validation"
                ),
            },
        )
        if checksums != {str(p): tree_hash(p) if p.is_dir() else sha256_file(p) for p in inputs}:
            raise ValueError("Inputs changed during visualization")
        for path in sorted(output.rglob("*")):
            if path.is_file() and path.name != "run.json" and ".zarr" not in path.as_posix():
                manifest["artifacts"][path.relative_to(output).as_posix()] = sha256_file(path)
        manifest.update(
            status="complete",
            scene_count=len(groups),
            grid_cells=total_cells,
            estimated_curtain_vertices=estimated_vertices,
            rendered_curtain_vertices=rendered_vertices,
            payload_bytes=payload_bytes,
        )
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        manifest.update(
            finished_utc=datetime.now(UTC).isoformat(),
            elapsed_seconds=time.perf_counter() - started,
        )
        write_json(output / "run.json", manifest)
    return manifest
