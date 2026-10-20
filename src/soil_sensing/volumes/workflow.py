"""Immutable, evidence-gated volume runs over exact partition memberships."""

import json
import time
import tomllib
from dataclasses import asdict
from pathlib import Path

import numpy as np
import xarray as xr
from shapely import LineString, unary_union

from soil_sensing.export.report import verify_run
from soil_sensing.ingest import write_json
from soil_sensing.io.storage import load_profile, save_profile
from soil_sensing.processing.attributes import AttributeOptions, native_attributes
from soil_sensing.runs import begin_run, finish_run
from soil_sensing.spatial.gridding import GridOptions, polygon_domain
from soil_sensing.spatial.plots import load_plots
from soil_sensing.visualization.scene import axis_values, profile_signature, raw_gps_relative_xy
from soil_sensing.volumes.audit import track_direction
from soil_sensing.volumes.contracts import (
    ATTRIBUTES,
    capabilities,
    digest,
    freeze_selection,
    line_id,
    validate_selection,
    validate_volume,
)
from soil_sensing.volumes.reconstruction import Reconstruction, VolumeOptions, evaluate_volume


def load_volume_config(path):
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version",
        "synthetic",
        "synthetic_depth",
        "coordinate_mode",
        "profiles",
        "partitions",
        "plots_file",
        "vertical_axis",
        "position_policy",
        "direction_policy",
        "combination_evidence",
        "grid",
        "volume",
        "attributes",
        "track_gap_m",
        "track_buffer_m",
        "evaluation_sample_step",
        "evaluation_attribute",
        "evaluation_buffer_m",
        "preview",
        "detection",
    }
    if data.get("schema_version") != "1.0" or set(data) - allowed:
        raise ValueError("Unknown volume config field/schema")
    if "detection" in data:
        from soil_sensing.volumes.detection import detect_anomalies

        detection = data["detection"]
        if not isinstance(detection, dict) or set(detection) - {
            "threshold",
            "minimum_cells",
            "minimum_slice",
        }:
            raise ValueError("Unknown detection option")
        detect_anomalies(np.zeros((2, 2, 2)), np.ones((1, 1), bool), **detection)
    for name in ("synthetic", "synthetic_depth"):
        if type(data.get(name, False)) is not bool:
            raise ValueError(f"{name} must be Boolean")
    if data.get("synthetic_depth") and not data.get("synthetic"):
        raise ValueError("synthetic_depth requires the explicit synthetic fixture")
    mode = data.get("coordinate_mode", "projected")
    if mode not in ("projected", "raw_gps_relative", "synthetic_local"):
        raise ValueError("Unknown coordinate_mode")
    if mode == "synthetic_local" and not data.get("synthetic"):
        raise ValueError("synthetic_local requires synthetic=true")
    if data.get("synthetic") and (
        data.get("profiles") or data.get("partitions") or data.get("plots_file")
    ):
        raise ValueError("Synthetic mode cannot consume field inputs")
    data["coordinate_mode"] = mode
    for name in ("track_gap_m", "track_buffer_m", "evaluation_buffer_m"):
        value = data.get(
            name, {"track_gap_m": 6.0, "track_buffer_m": 7.0, "evaluation_buffer_m": 0.0}[name]
        )
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not np.isfinite(value)
            or value < (0 if name == "evaluation_buffer_m" else 1e-12)
        ):
            raise ValueError(f"Invalid {name}")
        data[name] = value
    step = data.get("evaluation_sample_step", 16)
    if type(step) is not int or step <= 0:
        raise ValueError("evaluation_sample_step must be a positive integer")
    data["evaluation_sample_step"] = step
    if data.get("evaluation_attribute", "envelope") not in ATTRIBUTES:
        raise ValueError("Unknown evaluation_attribute")
    stores = data.get("profiles", [])
    if not isinstance(stores, list) or any(not isinstance(v, str) or not v for v in stores):
        raise ValueError("profiles must contain paths")
    data["profiles"] = [(path.parent / p).resolve() for p in stores]
    if len(set(data["profiles"])) != len(stores):
        raise ValueError("Duplicate profile stores")
    if data.get("plots_file"):
        data["plots_file"] = (path.parent / data["plots_file"]).resolve()
    if not data.get("synthetic") and (not stores or not data.get("partitions")):
        raise ValueError("Supply full quantitative stores and frozen exact partition memberships")
    partitions = data.get("partitions", [])
    if not isinstance(partitions, list) or any(
        not isinstance(p, dict)
        or not isinstance(p.get("lines"), list)
        or not p["lines"]
        or any(
            not isinstance(line, list)
            or len(line) != 2
            or any(not isinstance(v, str) or not v for v in line)
            for line in p["lines"]
        )
        for p in partitions
    ):
        raise ValueError("Partitions must contain explicit nonempty source/profile pairs")
    if mode == "raw_gps_relative" and data.get("plots_file"):
        raise ValueError("Raw-GPS diagnostics cannot use geographic plot geometry")
    if data.get("position_policy", "accepted") not in ("accepted", "measured") or data.get(
        "direction_policy", "separate"
    ) not in ("separate", "combine_reviewed"):
        raise ValueError("Unknown position/direction policy")
    if data.get("direction_policy", "separate") == "combine_reviewed" and not data.get(
        "combination_evidence"
    ):
        raise ValueError("Combining direction groups requires combination_evidence")
    return (
        data,
        GridOptions(**data.get("grid", {})),
        VolumeOptions(**data.get("volume", {})),
        AttributeOptions(**data.get("attributes", {})),
    )


def _synthetic(depth):
    from soil_sensing.visualization.synthetic import synthetic_plots

    profiles, plots, exclusions = synthetic_plots()
    profiles = [p for p in profiles if p.attrs["plot_id"] in list(plots)[:2]]
    # Two independent dipping boundaries plus a diffraction, without sharing truth
    # with the native-profile detector or tracker.
    for p in profiles:
        t = p.twt_ns.values[None, :]
        x = p.distance_m.values[:, None]
        for centre, strength in ((18 + 0.04 * x, 300), (55 + 0.06 * x, 450)):
            a = (t - centre) / 2
            p.amplitude.values[:] += (strength * (1 - 2 * a**2) * np.exp(-(a**2))).astype(
                np.float32
            )
        event = np.sqrt(30**2 + ((x - 24) / 0.1) ** 2)
        a = (t - event) / 1.5
        p.amplitude.values[:] += (1000 * (1 - 2 * a**2) * np.exp(-(a**2))).astype(np.float32)
        if depth:
            from soil_sensing.processing.depth import VelocityCalibration, add_depth_axis

            p.attrs["antenna_separation_m"] = 0.4
            p.attrs["processing_options"].update(
                reviewed_time_zero_ns=0.0, time_zero_evidence="synthetic generator reference"
            )
            p.coords["corrected_twt_ns"] = ("sample", p.twt_ns.values.copy())
            calibrated = add_depth_axis(
                p,
                VelocityCalibration(
                    "synthetic-v1", "explicit simulated homogeneous fixture", 0.1, 0.002, 0.2
                ),
            )
            p.update(calibrated)
            p.attrs = calibrated.attrs
    partitions = [
        dict(
            partition_id=f"grid-{i + 1:02d}",
            lines=[
                [p.attrs["source_id"], p.attrs["profile_id"]]
                for p in profiles
                if p.attrs["plot_id"] == plot
            ],
        )
        for i, plot in enumerate(list(plots)[:2])
    ]
    return profiles, plots, partitions, exclusions


def _prepare(profiles, partition, plots, data, grid, attributes, exclusions):
    identities = [line_id(p) for p in profiles]
    if len(set(identities)) != len(identities):
        raise ValueError("Repeated source/profile identity")
    reference = profiles[0]
    basis = data.get("vertical_axis", "recording_time")
    axes = [axis_values(p, basis) for p in profiles]
    if any(not np.array_equal(a, axes[0], equal_nan=True) for a in axes[1:]):
        raise ValueError("Incompatible native vertical axes; explicit reviewed resampling required")
    if len({profile_signature(p) for p in profiles}) != 1:
        raise ValueError("Incompatible processing/settings; do not normalize or stack")
    if len({p.attrs["survey_id"] for p in profiles}) > 1:
        raise ValueError("A partition cannot merge acquisition sessions/surveys")
    finite_axis = np.isfinite(axes[0])
    if not finite_axis.any():
        raise ValueError("No supported native vertical samples")
    mode = data["coordinate_mode"]
    anchor, origin, crs, plot_id, evidence = None, [0.0, 0.0], None, None, None
    if mode == "raw_gps_relative":
        if basis != "recording_time":
            raise ValueError("Raw-GPS diagnostic volumes currently support recording time only")
        accepted = []
        for p in profiles:
            if "latitude_deg" not in p:
                raise ValueError("Raw-GPS volumes require located profiles with NMEA coordinates")
            good = np.isin(p.position_status.values, ["measured", "interpolated"])
            accepted.extend(
                np.c_[p.latitude_deg.values[good], p.longitude_deg.values[good]].tolist()
            )
        if not accepted:
            raise ValueError("Partition has no accepted GPS positions")
        anchor = np.median(accepted, axis=0).tolist()
        positions = [raw_gps_relative_xy(p, anchor) for p in profiles]
    else:
        plot_ids = {p.attrs["plot_id"] for p in profiles}
        if len(plot_ids) != 1:
            raise ValueError("A geographic partition requires one plot")
        plot = plots.get(reference.attrs["plot_id"])
        if plot is None or plot.status != "reviewed" or not plot.evidence:
            raise ValueError("Reviewed geometry is required")
        origin = np.min(plot.polygon, axis=0).tolist()
        positions = [np.c_[p.x_m.values, p.y_m.values] - origin for p in profiles]
        if mode == "projected":
            for p in profiles:
                assignment = p.attrs.get("assignment") or {}
                if (
                    p.attrs["target_crs"] != plot.crs
                    or not p.attrs["source_crs"]
                    or not assignment.get("assignment_evidence")
                    or not assignment.get("reviewer")
                    or assignment.get("include") is False
                    or not p.attrs["survey_id"]
                ):
                    raise ValueError(
                        "Geographic volume requires reviewed datum/CRS and assignments"
                    )
            crs, plot_id, evidence = plot.crs, plot.plot_id, plot.evidence
    rows, arrays, track_segments, audit, offset = [], {n: [] for n in ATTRIBUTES}, [], [], 0
    allowed = (
        ["measured"]
        if data.get("position_policy", "accepted") == "measured"
        else ["measured", "interpolated"]
    )
    for p, xy in zip(profiles, positions, strict=True):
        computed = native_attributes(p, attributes)
        for name in ATTRIBUTES:
            arrays[name].append(computed[name][:, finite_axis])
        located = np.isfinite(xy).all(axis=1) & np.isin(p.position_status.values, allowed)
        valid_steps = located[1:] & located[:-1]
        diffs = np.diff(xy, axis=0)
        valid_steps &= np.linalg.norm(diffs, axis=1) <= data["track_gap_m"]
        direction = track_direction(xy, valid_steps)
        dates = sorted({str(t)[:10] for t in p.time_utc.values if not np.isnat(t)})
        audit.append(
            dict(
                line_id=line_id(p),
                direction_group=direction,
                direction_method="principal axis of accepted gap-limited track endpoints",
                acquisition_dates=dates,
                missing_positions=int((~located).sum()),
                broken_segments=int((~valid_steps).sum()),
                duplicate_locations=int(located.sum() - len(np.unique(xy[located], axis=0))),
                orientation=p.attrs.get("antenna_orientation"),
                precision_m=None,
            )
        )
        for i in np.flatnonzero(valid_steps):
            if np.linalg.norm(diffs[i]) > 0:
                track_segments.append(
                    LineString(xy[i : i + 2]).buffer(data["track_buffer_m"], cap_style="flat")
                )
        for i in range(p.sizes["trace"]):
            stamp = p.time_utc.values[i]
            rows.append(
                dict(
                    xy=xy[i].tolist(),
                    located=bool(located[i]),
                    line_id=line_id(p),
                    source_id=p.attrs["source_id"],
                    profile_id=p.attrs["profile_id"],
                    trace_id=str(p.trace_id.values[i]),
                    position_status=str(p.position_status.values[i]),
                    direction_group=direction,
                    quality_flags=int(p.quality_flags.values[i]),
                    time_utc=None if np.isnat(stamp) else str(stamp),
                )
            )
        offset += len(xy)
    groups = {r["direction_group"] for r in rows if r["located"]}
    dates = {d for a in audit for d in a["acquisition_dates"]}
    if len(groups) > 1 and data.get("direction_policy", "separate") != "combine_reviewed":
        raise ValueError(
            "Mixed direction groups: split partition or provide combine_reviewed policy/evidence"
        )
    if len(dates) > 1:
        raise ValueError("Partition spans acquisition dates; split into compatible sessions")
    all_xy = np.array([r["xy"] for r in rows if r["located"]])
    if not len(all_xy):
        raise ValueError("No accepted positions in partition")
    lo, hi = np.min(all_xy, axis=0), np.max(all_xy, axis=0)
    if mode == "projected" or mode == "synthetic_local":
        boundary = (np.asarray(plot.polygon) - origin).tolist()
    else:
        boundary = [
            [lo[0] - 1, lo[1] - 1],
            [hi[0] + 1, lo[1] - 1],
            [hi[0] + 1, hi[1] + 1],
            [lo[0] - 1, hi[1] + 1],
        ]
    rings = partition.get("exclusions", exclusions.get(reference.attrs["plot_id"], []))
    if mode in ("projected", "synthetic_local"):
        rings = [(np.asarray(r) - origin).tolist() for r in rings]
    regions = partition.get("regions", [])
    if mode in ("projected", "synthetic_local"):
        regions = [(np.asarray(r) - origin).tolist() for r in regions]
    domain = polygon_domain(boundary, exclusions=rings, regions=regions)
    footprint = unary_union(track_segments)
    domain = domain.intersection(footprint)
    metadata = dict(
        coordinate_mode=mode,
        crs=crs,
        plot_id=plot_id,
        survey_id=reference.attrs["survey_id"],
        origin_xy_m=origin,
        coordinate_anchor_latlon=anchor,
        coordinate_note="Approximate raw GPS offsets; datum/CRS/accuracy unverified"
        if mode == "raw_gps_relative"
        else "Synthetic fixture"
        if data.get("synthetic")
        else "Reviewed projected geometry",
        geometry_evidence=evidence,
        vertical_axis=basis,
        vertical_unit="m" if basis == "depth" else "ns",
        attribute_unit=reference.attrs["amplitude_units"],
        calibration=reference.attrs.get("depth_model"),
        time_zero_evidence=reference.attrs.get("processing_options", {}).get("time_zero_evidence"),
        processing_signature=profile_signature(reference),
        source_profiles=[
            dict(
                source_id=p.attrs["source_id"],
                profile_id=p.attrs["profile_id"],
                checksums=p.attrs["source_checksums"],
                history=p.attrs["history"],
            )
            for p in profiles
        ],
        synthetic=data.get("synthetic", False),
        diagnostic=mode == "raw_gps_relative",
        flat_ground_assumption=True,
        attribute_options=asdict(attributes),
        track_gap_m=data["track_gap_m"],
        track_buffer_m=data["track_buffer_m"],
        reviewed_time_zero_ns=reference.attrs.get("processing_options", {}).get(
            "reviewed_time_zero_ns"
        ),
        partition_id=partition["partition_id"],
        acquisition_audit=audit,
        combination_evidence=data.get("combination_evidence"),
    )
    return (
        rows,
        {n: np.concatenate(v) for n, v in arrays.items()},
        axes[0][finite_axis],
        np.flatnonzero(finite_axis),
        boundary,
        domain,
        metadata,
    )


def save_blocks(reconstruction, root):
    root.mkdir(parents=True, exist_ok=True)
    offset = 0
    for cube, support in reconstruction.blocks():
        for filename, dataset in (("volume.zarr", cube), ("support.zarr", support)):
            target = root / filename
            if offset == 0:
                encoding = {
                    name: {
                        "_FillValue": None,
                        "chunks": tuple(
                            min(reconstruction.options.block_samples, dataset.sizes[d])
                            if d == "vertical"
                            else min(64, dataset.sizes[d])
                            for d in var.dims
                        ),
                    }
                    for name, var in dataset.variables.items()
                }
                dataset.to_zarr(
                    target, mode="w-", consolidated=True, encoding=encoding, zarr_format=2
                )
            else:
                dataset.to_zarr(target, mode="a", append_dim="vertical", consolidated=True)
            with xr.open_zarr(
                target, chunks=None, mask_and_scale=False, consolidated=True
            ) as stored:
                xr.testing.assert_identical(
                    dataset,
                    stored.isel(vertical=slice(offset, offset + cube.sizes["vertical"])).load(),
                )
        offset += cube.sizes["vertical"]
    write_json(
        root / "contributors.json",
        {
            "schema_version": "1.0",
            "native_sample_reference": "volume.zarr/native_sample",
            "bins": reconstruction.table,
        },
    )


def volume_run(config_path, output, progress=None):
    config_path = Path(config_path).resolve()
    data, grid, options, attributes = load_volume_config(config_path)
    inputs = [
        config_path,
        *data["profiles"],
        *([data["plots_file"]] if data.get("plots_file") else []),
    ]
    output, manifest = begin_run(
        "volume", output, inputs, configuration=json.loads(json.dumps(data, default=str))
    )
    started = time.perf_counter()
    try:
        if data.get("synthetic"):
            profiles, plots, partitions, exclusions = _synthetic(data.get("synthetic_depth", False))
        else:
            total_samples = 0
            for store in data["profiles"]:
                with xr.open_zarr(
                    store, chunks=None, mask_and_scale=False, consolidated=True
                ) as header:
                    total_samples += header.amplitude.size
            if total_samples * 100 > options.max_memory_mb * 1_000_000:
                raise ValueError(
                    "Native input/attribute working set exceeds max_memory_mb; split the selection"
                )
            profiles = [load_profile(p) for p in data["profiles"]]
            plots = load_plots(data["plots_file"]) if data.get("plots_file") else {}
            partitions, exclusions = data["partitions"], {}
        available = {(p.attrs["source_id"], p.attrs["profile_id"]): p for p in profiles}
        if len(available) != len(profiles):
            raise ValueError("Duplicate source/profile identity")
        selected, ids = set(), set()
        products = []
        for partition in partitions:
            if set(partition) - {"partition_id", "lines", "exclusions", "regions"}:
                raise ValueError("Unknown partition field")
            identifier = partition.get("partition_id")
            import re

            if (
                not isinstance(identifier, str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier)
                or identifier in ids
            ):
                raise ValueError("Partition IDs must be unique safe names")
            lines = [tuple(v) for v in partition.get("lines", [])]
            if (
                not lines
                or len(set(lines)) != len(lines)
                or any(v not in available or v in selected for v in lines)
            ):
                raise ValueError("Partitions require disjoint, exact existing source/profile pairs")
            selected.update(lines)
            ids.add(identifier)
            chosen = [available[k] for k in sorted(lines)]
            root = output / identifier
            root.mkdir()
            preliminary = dict(
                coordinate_mode=data["coordinate_mode"],
                crs=chosen[0].attrs.get("target_crs")
                if data["coordinate_mode"] == "projected"
                else None,
                geometry_evidence=plots.get(chosen[0].attrs["plot_id"]).evidence
                if chosen[0].attrs["plot_id"] in plots
                else None,
                calibration=chosen[0].attrs.get("depth_model"),
                time_zero_evidence=chosen[0]
                .attrs.get("processing_options", {})
                .get("time_zero_evidence"),
            )
            write_json(root / "capabilities.json", capabilities(preliminary))
            if progress:
                progress(f"Reconstructing {identifier}: {len(chosen)} native profiles")
            rows, arrays, vertical, native_samples, boundary, domain, metadata = _prepare(
                chosen, partition, plots, data, grid, attributes, exclusions
            )
            selection = freeze_selection(
                identifier,
                chosen,
                manifest["inputs"],
                position_policy=data.get("position_policy", "accepted"),
                direction_policy=data.get("direction_policy", "separate"),
                geometry={
                    "code_sha256": manifest["code_sha256"],
                    "domain": domain.__geo_interface__,
                    "coordinate_mode": data["coordinate_mode"],
                    "anchor": metadata["coordinate_anchor_latlon"],
                    "grid": asdict(grid),
                    "attributes": asdict(attributes),
                    "vertical_axis": metadata["vertical_axis"],
                    "combination_evidence": data.get("combination_evidence"),
                },
            )
            validate_selection(selection)
            metadata["selection_hash"] = selection["selection_hash"]
            reconstruction = Reconstruction(
                rows, arrays, vertical, native_samples, boundary, domain, metadata, grid, options
            )
            save_blocks(reconstruction, root)
            if "detection" in data:
                from soil_sensing.volumes.detection import write_anomalies

                write_anomalies(root, data["detection"])
            write_json(root / "selection.json", selection)
            write_json(root / "capabilities.json", capabilities(metadata))
            from soil_sensing.volumes.audit import geometry_audit

            write_json(
                root / "geometry_audit.json",
                geometry_audit(
                    rows,
                    arrays,
                    metadata["acquisition_audit"],
                    max_gap_m=data["track_gap_m"],
                    proximity_m=grid.bin_m / 2,
                ),
            )
            native = []
            for p in chosen:
                name = (
                    "native/" + digest([p.attrs["source_id"], p.attrs["profile_id"]])[:20] + ".zarr"
                )
                (root / "native").mkdir(exist_ok=True)
                save_profile(p, root / name)
                native.append(
                    dict(
                        source_id=p.attrs["source_id"], profile_id=p.attrs["profile_id"], store=name
                    )
                )
            write_json(root / "native.json", {"profiles": native})
            evaluation = evaluate_volume(
                rows,
                arrays,
                vertical,
                native_samples,
                boundary,
                domain,
                metadata,
                grid,
                options,
                sample_step=data["evaluation_sample_step"],
                buffer_m=data["evaluation_buffer_m"],
                attribute=data.get("evaluation_attribute", "envelope"),
            )
            write_json(root / "evaluation.json", evaluation)
            products.append(
                dict(
                    partition_id=identifier,
                    path=identifier,
                    selection_hash=selection["selection_hash"],
                    capabilities=capabilities(metadata),
                    estimated_memory_bytes=reconstruction.estimated_memory_bytes,
                )
            )
        catalog = dict(
            schema_version="1.0",
            kind="radar_volume",
            synthetic=data.get("synthetic", False),
            partitions=products,
        )
        write_json(output / "products.json", catalog)
        from soil_sensing.visualization.volume import write_volume_viewer

        preview_report = write_volume_viewer(output, output, preview=data.get("preview", {}))
        from soil_sensing.benchmark import peak_memory_bytes

        manifest.update(preview_report)
        manifest.update(
            partition_count=len(products),
            elapsed_seconds=time.perf_counter() - started,
            synthetic=data.get("synthetic", False),
            peak_process_memory_bytes=peak_memory_bytes(),
        )
        finish_run(output, manifest)
        return manifest
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise


def load_partition(root, partition_id):
    root = Path(root).resolve()
    verify_run(root)
    catalog = json.loads((root / "products.json").read_text(encoding="utf-8"))
    product = next((p for p in catalog["partitions"] if p["partition_id"] == partition_id), None)
    if product is None:
        raise ValueError("Unknown partition_id")
    directory = (root / product["path"]).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Product path escapes run")
    with xr.open_zarr(
        directory / "volume.zarr", chunks=None, consolidated=True, mask_and_scale=False
    ) as stored:
        cube = stored.load()
    with xr.open_zarr(
        directory / "support.zarr", chunks=None, consolidated=True, mask_and_scale=False
    ) as stored:
        support = stored.load()
    validate_volume(cube, support)
    selection = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    validate_selection(selection)
    if (
        selection["selection_hash"] != cube.attrs["selection_hash"]
        or product["selection_hash"] != cube.attrs["selection_hash"]
    ):
        raise ValueError("Cube/catalog selection mismatch")
    return directory, cube, support, selection
