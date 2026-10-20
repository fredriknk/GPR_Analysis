"""Native-profile interpretation and immutable review revisions."""

import json
import tomllib
from pathlib import Path

import numpy as np

from soil_sensing.export.report import verify_run
from soil_sensing.ingest import write_json
from soil_sensing.interpretation.drainage import (
    DrainOptions,
    associate_detections,
    detect_diffraction,
    review_candidates,
)
from soil_sensing.interpretation.horizons import TrackingOptions, track_horizon
from soil_sensing.interpretation.layers import build_surfaces
from soil_sensing.interpretation.picks import revision, validate_interpretation
from soil_sensing.io.storage import load_profile
from soil_sensing.runs import begin_run, finish_run
from soil_sensing.visualization.scene import raw_gps_relative_xy
from soil_sensing.volumes.contracts import digest, line_id
from soil_sensing.volumes.reconstruction import domain_from_metadata
from soil_sensing.volumes.workflow import load_partition


def _native(root, cube, selection):
    index = json.loads((root / "native.json").read_text(encoding="utf-8"))
    profiles, rows = [], []
    for item in index["profiles"]:
        path = (root / item["store"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Native profile escapes partition")
        profile = load_profile(path)
        from soil_sensing.processing.attributes import AttributeOptions, native_attributes

        sample_valid = np.isfinite(
            native_attributes(profile, AttributeOptions(**cube.attrs["attribute_options"]))[
                "envelope"
            ]
        )
        profiles.append(profile)
        if cube.attrs["coordinate_mode"] == "raw_gps_relative":
            xy = raw_gps_relative_xy(profile, cube.attrs["coordinate_anchor_latlon"])
        else:
            xy = np.c_[profile.x_m.values, profile.y_m.values] - cube.attrs["origin_xy_m"]
        allowed = (
            ["measured"]
            if selection["position_policy"] == "measured"
            else ["measured", "interpolated"]
        )
        audit = next(a for a in cube.attrs["acquisition_audit"] if a["line_id"] == line_id(profile))
        own = [
            dict(
                source_id=profile.attrs["source_id"],
                profile_id=profile.attrs["profile_id"],
                trace_id=str(t),
                line_id=line_id(profile),
                xy=v.tolist(),
                located=bool(np.isfinite(v).all() and status in allowed),
                position_status=str(status),
                direction_group=audit["direction_group"],
                sample_valid=sample_valid[i],
            )
            for i, (t, v, status) in enumerate(
                zip(profile.trace_id.values, xy, profile.position_status.values, strict=True)
            )
        ]
        rows.extend(own)
    return profiles, rows


def _synthetic_picks(profiles):
    picks = []
    for p in profiles:
        for i in range(p.sizes["trace"]):
            if p.position_status.values[i] not in ("measured", "interpolated"):
                continue
            for horizon, time in (
                ("H1", 18 + 0.04 * p.distance_m.values[i]),
                ("H2", 55 + 0.06 * p.distance_m.values[i]),
            ):
                sample = int(np.argmin(np.abs(p.twt_ns.values - time)))
                picks.append(
                    dict(
                        pick_id=digest([line_id(p), i, horizon])[:24],
                        horizon_id=horizon,
                        source_id=p.attrs["source_id"],
                        profile_id=p.attrs["profile_id"],
                        trace_id=str(p.trace_id.values[i]),
                        native_sample=sample,
                        time_basis="recording",
                        two_way_time_ns=float(p.twt_ns.values[sample]),
                        standard_uncertainty_ns=None,
                        status="accepted",
                        reviewer="explicit synthetic fixture",
                        evidence="generator-defined synthetic boundary; no field validation",
                    )
                )
    return picks


def _save_dataset(dataset, path):
    encoding = {name: {"_FillValue": None} for name in dataset.variables}
    dataset.to_zarr(path, mode="w-", consolidated=True, encoding=encoding, zarr_format=2)
    import xarray as xr

    with xr.open_zarr(path, chunks=None, mask_and_scale=False, consolidated=True) as stored:
        xr.testing.assert_identical(dataset, stored.load())


def interpret_run(config_path, volume_root, output):
    config_path, volume_root = Path(config_path).resolve(), Path(volume_root).resolve()
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version",
        "partition_id",
        "kind",
        "synthetic",
        "picks_file",
        "parent_interpretation",
        "review_decisions",
        "track",
        "tracking",
        "preview_proposed",
        "layers",
        "apices_file",
        "drainage",
        "map",
        "uncertainty",
    }
    if (
        data.get("schema_version") != "1.0"
        or set(data) - allowed
        or data.get("kind") not in ("layers", "drainage")
    ):
        raise ValueError("Invalid interpretation schema/kind")
    for name in ("synthetic", "track", "preview_proposed"):
        if type(data.get(name, False)) is not bool:
            raise ValueError(f"{name} must be Boolean")
    inputs = [config_path, volume_root]
    for name in ("picks_file", "parent_interpretation", "review_decisions", "apices_file"):
        if data.get(name):
            data[name] = (config_path.parent / data[name]).resolve()
            inputs.append(data[name])
    directory, cube, _, selection = load_partition(volume_root, data.get("partition_id"))
    if data.get("synthetic") and not cube.attrs["synthetic"]:
        raise ValueError("Synthetic interpretation cannot borrow field profiles")
    output, manifest = begin_run(
        "interpret", output, inputs, configuration=json.loads(json.dumps(data, default=str))
    )
    try:
        profiles, rows = _native(directory, cube, selection)
        domain = domain_from_metadata(cube.attrs)
        parent = None
        if data.get("parent_interpretation"):
            parent = json.loads(data["parent_interpretation"].read_text(encoding="utf-8"))
            if data["kind"] == "layers":
                validate_interpretation(parent, profiles, selection["selection_hash"])
        if data["kind"] == "layers":
            if data.get("synthetic"):
                if parent or data.get("picks_file") or data.get("review_decisions"):
                    raise ValueError("Synthetic picks cannot mix with external review inputs")
                picks = _synthetic_picks(profiles)
            elif parent:
                picks = [dict(p) for p in parent["picks"]]
            elif data.get("picks_file"):
                supplied = json.loads(data["picks_file"].read_text(encoding="utf-8"))
                picks = supplied.get("picks", [])
            else:
                raise ValueError(
                    "Layer interpretation requires native picks/seeds "
                    "or an explicit synthetic fixture"
                )
            provisional = revision(picks, selection["selection_hash"], parent=parent)
            validate_interpretation(provisional, profiles, selection["selection_hash"])
            if data.get("track"):
                tracked = []
                options = TrackingOptions(**data.get("tracking", {}))
                for profile in profiles:
                    seeds = [
                        p
                        for p in picks
                        if p["source_id"] == profile.attrs["source_id"]
                        and p["profile_id"] == profile.attrs["profile_id"]
                        and p["status"] != "rejected"
                    ]
                    for horizon in sorted({s["horizon_id"] for s in seeds}):
                        tracked.extend(
                            track_horizon(
                                profile,
                                [s for s in seeds if s["horizon_id"] == horizon],
                                horizon,
                                options,
                            )
                        )
                existing = {
                    (p["source_id"], p["profile_id"], p["trace_id"], p["horizon_id"]): p
                    for p in picks
                }
                for pick in tracked:
                    existing.setdefault(
                        (
                            pick["source_id"],
                            pick["profile_id"],
                            pick["trace_id"],
                            pick["horizon_id"],
                        ),
                        pick,
                    )
                picks = list(existing.values())
            if data.get("review_decisions"):
                decisions = json.loads(data["review_decisions"].read_text(encoding="utf-8"))
                if (
                    decisions.get("schema_version") != "1.0"
                    or decisions.get("kind") != "review_decisions"
                    or decisions.get("selection_hash") != selection["selection_hash"]
                    or decisions.get("parent_hash")
                    != (parent["interpretation_hash"] if parent else None)
                ):
                    raise ValueError("Review decisions have stale selection or parent revision")
                by_identity = {
                    (p["source_id"], p["profile_id"], p["trace_id"], p["horizon_id"]): p
                    for p in picks
                }
                for decision in decisions.get("decisions", []):
                    key = (
                        decision["source_id"],
                        decision["profile_id"],
                        decision["trace_id"],
                        decision["horizon_id"],
                    )
                    old = by_identity.get(key)
                    if old:
                        decision["pick_id"] = old["pick_id"]
                    by_identity[key] = decision
                picks = list(by_identity.values())
            document = revision(picks, selection["selection_hash"], parent=parent)
            validate_interpretation(document, profiles, selection["selection_hash"])
            surfaces = build_surfaces(
                cube,
                document,
                rows,
                data.get("layers", []),
                preview_proposed=data.get("preview_proposed", False),
                uncertainty=data.get("uncertainty"),
            )
            contributors = surfaces.attrs.pop("surface_contributors")
            surfaces.attrs["surface_contributor_reference"] = "surface_contributors.json"
            write_json(
                output / "surface_contributors.json",
                {"schema_version": "1.0", "bins": contributors},
            )
            _save_dataset(surfaces, output / "surfaces.zarr")
            write_json(output / "interpretation.json", document)
            from soil_sensing.export.interpretation import export_layer_meshes

            mesh_report = export_layer_meshes(surfaces, output / "meshes")
            write_json(
                output / "evaluation.json",
                dict(
                    schema_version="1.0",
                    field_validation=False,
                    uncertainty=(
                        "unknown unless explicitly supplied; spatial uncertainty not estimated"
                    ),
                    accepted_picks=sum(p["status"] == "accepted" for p in picks),
                    proposed_picks=sum(p["status"] == "proposed" for p in picks),
                    supported_horizon_cells=int(np.isfinite(surfaces.horizon_time_ns.values).sum()),
                    mesh_report=mesh_report,
                ),
            )
            kind = "interpreted_layers"
        else:
            options = DrainOptions(**data.get("drainage", {}))
            if data.get("synthetic"):
                apices = [
                    dict(
                        source_id=p.attrs["source_id"],
                        profile_id=p.attrs["profile_id"],
                        trace_id=str(
                            p.trace_id.values[int(np.argmin(np.abs(p.distance_m.values - 25)))]
                        ),
                        two_way_time_ns=30.0,
                        time_basis="recording",
                        reviewer="synthetic fixture",
                        evidence="simulated point diffraction; no field evidence",
                    )
                    for p in profiles
                ]
            elif data.get("apices_file"):
                apices = json.loads(data["apices_file"].read_text(encoding="utf-8"))["apices"]
            else:
                raise ValueError("Drainage assistance requires analyst native-profile apex picks")
            detections = []
            by_profile = {(p.attrs["source_id"], p.attrs["profile_id"]): p for p in profiles}
            for apex in apices:
                key = apex["source_id"], apex["profile_id"]
                if key not in by_profile:
                    raise ValueError("Apex is not in this exact partition")
                own = [r for r in rows if (r["source_id"], r["profile_id"]) == key]
                detections.append(detect_diffraction(by_profile[key], apex, own, domain, options))
            document = associate_detections(
                detections, domain, data["partition_id"], selection["selection_hash"], options
            )
            document["coordinate_metadata"] = cube.attrs
            document["lattice"] = {"x": cube.x.values.tolist(), "y": cube.y.values.tolist()}
            document["map"] = data.get("map")
            from dataclasses import asdict

            document["detector_signature"] = digest(asdict(options))
            decisions = (
                json.loads(data["review_decisions"].read_text(encoding="utf-8"))
                if data.get("review_decisions")
                else None
            )
            document = review_candidates(document, parent=parent, decisions=decisions)
            write_json(output / "pipe_candidates.json", document)
            write_json(
                output / "evaluation.json",
                dict(
                    schema_version="1.0",
                    field_validation=False,
                    tested_apices=len(detections),
                    template_candidates=sum(d["accepted_detection"] for d in detections),
                    independently_labelled_precision=None,
                    independently_labelled_recall=None,
                    limitation=(
                        "Analyst-seeded feasibility; site accuracy "
                        "and negative-search coverage unknown"
                    ),
                ),
            )
            kind = "drainage_candidates"
        write_json(
            output / "products.json",
            dict(
                schema_version="1.0",
                kind=kind,
                partition_id=data["partition_id"],
                selection_hash=selection["selection_hash"],
                volume_run=str(volume_root),
                synthetic=cube.attrs["synthetic"],
                metadata=cube.attrs,
                source_partition_path=str(directory),
            ),
        )
        manifest.update(synthetic=cube.attrs["synthetic"], product_kind=kind)
        finish_run(output, manifest)
        return manifest
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise


def volume_visualize_run(config_path, output):
    from soil_sensing.visualization.volume import write_volume_viewer

    config_path = Path(config_path).resolve()
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0" or set(data) - {
        "schema_version",
        "volume_run",
        "interpretation_runs",
        "preview",
    }:
        raise ValueError("Invalid volume viewer config")
    root = (config_path.parent / data["volume_run"]).resolve()
    objects = [(config_path.parent / p).resolve() for p in data.get("interpretation_runs", [])]
    for path in (root, *objects):
        verify_run(path)
    output, manifest = begin_run(
        "visualize_volume", output, [config_path, root, *objects], configuration=data
    )
    try:
        manifest.update(
            write_volume_viewer(root, output, interpretations=objects, preview=data.get("preview"))
        )
        manifest["synthetic"] = json.loads((root / "products.json").read_text())["synthetic"]
        finish_run(output, manifest)
        return manifest
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
