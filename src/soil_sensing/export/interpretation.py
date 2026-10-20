"""Supported layer meshes and numerical QGIS bundles, separate from display."""

import json
import re
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import xarray as xr

from soil_sensing.export.report import verify_run
from soil_sensing.ingest import write_json
from soil_sensing.runs import begin_run, finish_run
from soil_sensing.volumes.contracts import capabilities


def supported_surface_mesh(x, y, top, bottom=None, *, domain=None):
    """Only complete supported quads; shells have explicitly labelled display caps."""
    top = np.asarray(top)
    valid = np.isfinite(top) & (np.isfinite(bottom) if bottom is not None else True)
    vertices, faces, parent_cells, edges = [], [], [], {}
    ny, nx = top.shape
    for row in range(ny - 1):
        for col in range(nx - 1):
            corners = [(row, col), (row, col + 1), (row + 1, col + 1), (row + 1, col)]
            if not all(valid[r, c] for r, c in corners):
                continue
            if domain is not None:
                from shapely import Polygon

                if not domain.covers(Polygon([(x[c], y[r]) for r, c in corners])):
                    continue
            start = len(vertices)
            vertices.extend([float(x[c]), float(y[r]), float(top[r, c])] for r, c in corners)
            faces.extend([[start, start + 1, start + 2], [start, start + 2, start + 3]])
            parent_cells.extend([[row, col], [row, col]])
            if bottom is not None:
                vertices.extend([float(x[c]), float(y[r]), float(bottom[r, c])] for r, c in corners)
                faces.extend([[start + 4, start + 6, start + 5], [start + 4, start + 7, start + 6]])
                parent_cells.extend([[row, col], [row, col]])
                for i in range(4):
                    j = (i + 1) % 4
                    edge = tuple(sorted([corners[i], corners[j]]))
                    edges.setdefault(edge, []).append(
                        (start + i, start + j, start + 4 + i, start + 4 + j, row, col)
                    )
    caps = []
    for occurrences in edges.values():
        if len(occurrences) == 1:
            a, b, c, d, row, col = occurrences[0]
            caps.extend([len(faces), len(faces) + 1])
            faces.extend([[a, c, b], [b, c, d]])
            parent_cells.extend([[row, col], [row, col]])
    return dict(vertices=vertices, faces=faces, parent_cells=parent_cells, display_cap_faces=caps)


def write_ply(path, mesh, metadata):
    lines = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(mesh['vertices'])}",
        "property double x",
        "property double y",
        "property double z",
        f"element face {len(mesh['faces'])}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    lines.extend(" ".join(format(v, ".17g") for v in row) for row in mesh["vertices"])
    lines.extend("3 " + " ".join(map(str, face)) for face in mesh["faces"])
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    write_json(
        path.with_suffix(".json"),
        {
            **metadata,
            "display_cap_faces": mesh["display_cap_faces"],
            "parent_cells": mesh["parent_cells"],
            "caps_are_observed_walls": False,
        },
    )


def _safe_name(name):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def export_layer_meshes(surfaces, output):
    from shapely.geometry import shape

    domain = shape(surfaces.attrs["domain_geojson"])
    output.mkdir()
    calibrated = "horizon_depth_m" in surfaces
    values = surfaces["horizon_depth_m" if calibrated else "horizon_time_ns"].values
    horizons = list(surfaces.horizon.values)
    report = []
    names = [_safe_name(str(v)) for v in horizons]
    if len(set(names)) != len(names):
        raise ValueError("Horizon names collide as export filenames")
    for i, name in enumerate(names):
        mesh = supported_surface_mesh(
            surfaces.x.values, surfaces.y.values, values[i], domain=domain
        )
        write_ply(
            output / ("horizon_" + name + ".ply"),
            mesh,
            {
                **surfaces.attrs,
                "object_id": str(horizons[i]),
                "vertical_unit": "m" if calibrated else "ns",
            },
        )
    for layer in surfaces.attrs["layers"]:
        top, bottom = (values[horizons.index(layer[k])] for k in ("top", "bottom"))
        layer_array = (
            surfaces["layer_thickness_m" if calibrated else "layer_interval_twt_ns"]
            .sel(layer=layer["layer_id"])
            .values
        )
        mask = np.isfinite(layer_array)
        top, bottom = np.where(mask, top, np.nan), np.where(mask, bottom, np.nan)
        mesh = supported_surface_mesh(
            surfaces.x.values, surfaces.y.values, top, bottom, domain=domain
        )
        write_ply(
            output / ("layer_" + _safe_name(layer["layer_id"]) + ".ply"),
            mesh,
            {
                **surfaces.attrs,
                "object_id": layer["layer_id"],
                "vertical_unit": "m" if calibrated else "ns",
            },
        )
        cells = set(map(tuple, mesh["parent_cells"]))
        spacing = surfaces.attrs["grid_options"]["spacing_m"]
        area = len(cells) * spacing**2
        volume = (
            sum(float(np.mean(layer_array[r : r + 2, c : c + 2])) * spacing**2 for r, c in cells)
            if calibrated
            else None
        )
        report.append(
            dict(
                layer_id=layer["layer_id"],
                included_area_m2=area,
                excluded_lattice_area_m2=max(
                    0.0, (len(surfaces.x) - 1) * (len(surfaces.y) - 1) * spacing**2 - area
                ),
                supported_vertical_volume_m3=volume,
                shell_cap_semantics="display closure, not observed walls",
            )
        )
    return report


def _qml(path, values, unit):
    finite = np.asarray(values)[np.isfinite(values)]
    lo, hi = (float(finite.min()), float(finite.max())) if len(finite) else (0.0, 1.0)
    if lo == hi:
        hi = lo + 1
    root = ET.Element("qgis", version="3.40", styleCategories="AllStyleCategories")
    pipe = ET.SubElement(root, "pipe")
    renderer = ET.SubElement(
        pipe,
        "rasterrenderer",
        type="singlebandpseudocolor",
        band="1",
        opacity="1",
        classificationMin=str(lo),
        classificationMax=str(hi),
    )
    shader = ET.SubElement(
        ET.SubElement(renderer, "rastershader"),
        "colorrampshader",
        colorRampType="INTERPOLATED",
        classificationMode="1",
        minimumValue=str(lo),
        maximumValue=str(hi),
    )
    for value, colour in ((lo, "#440154"), ((lo + hi) / 2, "#21918c"), (hi, "#fde725")):
        ET.SubElement(
            shader, "item", value=str(value), color=colour, alpha="255", label=f"{value:g} {unit}"
        )
    ET.SubElement(renderer, "rasterTransparency")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    ET.parse(path)


def export_interpretation_run(input_run, output):
    input_run = Path(input_run).resolve()
    verify_run(input_run)
    product = json.loads((input_run / "products.json").read_text(encoding="utf-8"))
    metadata = product["metadata"]
    capability = capabilities(metadata)["geographic_export"]
    if not capability["allowed"]:
        raise ValueError(
            "Geographic interpretation export unavailable: "
            + "; ".join(capability["missing"])
            + ". Local Zarr/JSON/PLY downloads remain available."
        )
    output, manifest = begin_run("export_interpretation", output, [input_run])
    try:
        from soil_sensing.export.gis import write_numeric_raster

        catalog = []

        def raster(name, values, unit, x, y, tags=None):
            path = output / (name + ".tif")
            if path.exists():
                raise ValueError("Export object names collide")
            write_numeric_raster(path, values, x=x, y=y, metadata=metadata, unit=unit, tags=tags)
            _qml(path.with_suffix(".qml"), values, unit)
            catalog.append(
                dict(
                    path=path.name,
                    unit=unit,
                    nodata="NaN",
                    selection_hash=metadata["selection_hash"],
                    style=path.with_suffix(".qml").name,
                )
            )

        if product["kind"] == "interpreted_layers":
            with xr.open_zarr(
                input_run / "surfaces.zarr", chunks=None, mask_and_scale=False, consolidated=True
            ) as stored:
                surfaces = stored.load()
            if surfaces.attrs["review_status"] != "accepted_only":
                raise ValueError(
                    "Geographic scientific exports require accepted picks; "
                    "proposed surfaces are preview products"
                )
            for i, horizon in enumerate(surfaces.horizon.values):
                for key, suffix, unit in (
                    ("horizon_time_ns", "time_ns", "ns"),
                    ("horizon_depth_m", "depth_m", "m"),
                ):
                    if key in surfaces:
                        raster(
                            "horizon_" + _safe_name(str(horizon)) + "_" + suffix,
                            surfaces[key].values[i],
                            unit,
                            surfaces.x.values,
                            surfaces.y.values,
                            {
                                "time_basis": surfaces.attrs["time_basis"],
                                "review_status": "accepted_only",
                                "interpretation_hash": surfaces.attrs["interpretation_hash"],
                            },
                        )
                raster(
                    "horizon_" + _safe_name(str(horizon)) + "_support",
                    np.isfinite(surfaces.horizon_time_ns.values[i]).astype(float),
                    "bool",
                    surfaces.x.values,
                    surfaces.y.values,
                )
                for variable in ("horizon_pick_component_standard_uncertainty_ns",):
                    if variable in surfaces and np.isfinite(surfaces[variable].values[i]).any():
                        raster(
                            "horizon_"
                            + _safe_name(str(horizon))
                            + "_pick_component_uncertainty_ns",
                            surfaces[variable].values[i],
                            "ns",
                            surfaces.x.values,
                            surfaces.y.values,
                            {
                                "uncertainty_scope": (
                                    "partial_pick_component; spatial/model contributions unknown"
                                )
                            },
                        )
            for i, layer in enumerate(surfaces.layer.values):
                calibrated = "layer_thickness_m" in surfaces
                key, suffix, unit = (
                    ("layer_thickness_m", "thickness_m", "m")
                    if calibrated
                    else ("layer_interval_twt_ns", "interval_ns", "ns")
                )
                raster(
                    "layer_" + _safe_name(str(layer)) + "_" + suffix,
                    surfaces[key].values[i],
                    unit,
                    surfaces.x.values,
                    surfaces.y.values,
                    {
                        "thickness_direction": "vertical_positive_down",
                        "interpretation_hash": surfaces.attrs["interpretation_hash"],
                    },
                )
                variable = (
                    "layer_partial_standard_uncertainty_m"
                    if calibrated
                    else "layer_pick_component_standard_uncertainty_ns"
                )
                if variable in surfaces and np.isfinite(surfaces[variable].values[i]).any():
                    raster(
                        "layer_" + _safe_name(str(layer)) + "_partial_uncertainty_" + unit,
                        surfaces[variable].values[i],
                        unit,
                        surfaces.x.values,
                        surfaces.y.values,
                        {"uncertainty_scope": "partial; spatial/model contributions unknown"},
                    )
            shutil.copytree(input_run / "meshes", output / "meshes")
            shutil.copyfile(input_run / "interpretation.json", output / "interpretation.json")
        elif product["kind"] == "drainage_candidates":
            import geopandas as gpd
            from shapely import LineString, Point

            document = json.loads((input_run / "pipe_candidates.json").read_text(encoding="utf-8"))
            mapping = document.get("map")
            if not isinstance(mapping, dict) or set(mapping) != {
                "footprint_radius_m",
                "gate_bounds_ns",
            }:
                raise ValueError(
                    "Drain raster export requires explicit footprint_radius_m "
                    "and gate_bounds_ns map settings"
                )
            radius = mapping["footprint_radius_m"]
            gates = np.asarray(mapping["gate_bounds_ns"], float)
            if (
                isinstance(radius, bool)
                or not np.isfinite(radius)
                or radius <= 0
                or gates.ndim != 2
                or gates.shape[1] != 2
                or not np.isfinite(gates).all()
                or np.any(gates[:, 0] >= gates[:, 1])
                or np.any(gates[1:, 0] < gates[:-1, 1])
            ):
                raise ValueError("Invalid candidate map footprint/gates")
            x, y = np.array(document["lattice"]["x"]), np.array(document["lattice"]["y"])
            xx, yy = np.meshgrid(x, y)
            from shapely.geometry import shape

            from soil_sensing.spatial.gridding import inside_domain

            domain_mask = inside_domain(
                shape(metadata["domain_geojson"]), np.c_[xx.ravel(), yy.ravel()]
            ).reshape(xx.shape)
            detections = document["detections"]
            for index, (start, stop) in enumerate(gates):
                score, time = np.full(xx.shape, np.nan), np.full(xx.shape, np.nan)
                for detection in detections:
                    if (
                        not start <= detection["two_way_time_ns"] < stop
                        or detection["score"] is None
                    ):
                        continue
                    footprint = np.hypot(xx - detection["xy"][0], yy - detection["xy"][1]) <= radius
                    footprint &= domain_mask
                    candidate_score = detection["score"] if detection["accepted_detection"] else 0.0
                    better = footprint & (~np.isfinite(score) | (candidate_score > score))
                    score[better] = candidate_score
                    time[better] = (
                        detection["two_way_time_ns"] if detection["accepted_detection"] else np.nan
                    )
                tags = dict(
                    projection_rule="maximum_evidence_score_in_gate_at_tested_apex_footprints",
                    gate_start=str(start),
                    gate_stop=str(stop),
                    gate_unit="ns",
                    footprint_radius_m=str(radius),
                    score_is_probability="false",
                )
                raster(f"drain_candidate_score_{index:03d}", score, "evidence_score", x, y, tags)
                raster(f"drain_candidate_time_ns_{index:03d}", time, "ns", x, y, tags)
            ox, oy = metadata["origin_xy_m"]
            point_rows = [
                dict(
                    detection_id=d["detection_id"],
                    time_ns=d["two_way_time_ns"],
                    time_basis=d["time_basis"],
                    evidence_score=d["score"],
                    status=d["status"],
                    source_id=d["source_id"],
                    profile_id=d["profile_id"],
                    trace_id=d["trace_id"],
                    geometry=Point(ox + d["xy"][0], oy + d["xy"][1]),
                )
                for d in detections
                if d["accepted_detection"]
            ]
            if point_rows:
                frame = gpd.GeoDataFrame(point_rows, crs=metadata["crs"])
                frame.to_file(
                    output / "drain_candidates.gpkg",
                    layer="crossings",
                    driver="GPKG",
                    engine="pyogrio",
                    index=False,
                )
                restored = gpd.read_file(output / "drain_candidates.gpkg", layer="crossings")
                if (
                    restored.crs != frame.crs
                    or not restored.geometry.geom_equals(frame.geometry).all()
                ):
                    raise ValueError("Drain crossing GeoPackage roundtrip failed")
                lookup = {d["detection_id"]: d for d in detections}
                paths = []
                for edge in document["association_graph"]:
                    a, b = lookup[edge["left"]], lookup[edge["right"]]
                    paths.append(
                        dict(
                            left=edge["left"],
                            right=edge["right"],
                            status="alternative_association",
                            geometry=LineString(
                                [
                                    (ox + a["xy"][0], oy + a["xy"][1]),
                                    (ox + b["xy"][0], oy + b["xy"][1]),
                                ]
                            ),
                        )
                    )
                if paths:
                    frame = gpd.GeoDataFrame(paths, crs=metadata["crs"])
                    frame.to_file(
                        output / "drain_candidates.gpkg",
                        layer="paths",
                        driver="GPKG",
                        engine="pyogrio",
                        index=False,
                    )
                    restored = gpd.read_file(output / "drain_candidates.gpkg", layer="paths")
                    if (
                        restored.crs != frame.crs
                        or not restored.geometry.geom_equals(frame.geometry).all()
                    ):
                        raise ValueError("Drain path GeoPackage roundtrip failed")
            shutil.copyfile(input_run / "pipe_candidates.json", output / "pipe_candidates.json")
        else:
            raise ValueError("Unknown interpretation product kind")
        write_json(
            output / "products.json",
            {
                "schema_version": "1.0",
                "kind": "qgis_interpretation_bundle",
                "products": catalog,
                "metadata": metadata,
                "qml_loading_verified_in_qgis": False,
            },
        )
        (output / "README.txt").write_text(
            "Numerical radar interpretation products. NaN pixels are unknown.\n"
            "QML styles preserve queryable values; style loading requires QGIS review.\n"
            "Time separation is not metre thickness. Scores are not probabilities.\n"
            "Mesh edge caps are display closures. No field accuracy or soil labels.\n",
            encoding="utf-8",
        )
        manifest.update(synthetic=metadata["synthetic"], raster_count=len(catalog))
        finish_run(output, manifest)
        return manifest
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
