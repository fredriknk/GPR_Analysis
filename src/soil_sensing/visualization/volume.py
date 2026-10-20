"""Offline masked-slice renderer and immutable scientific downloads.

No Plotly volume/isosurface topology is trusted across unknown voxels. Slices
use explicit faces with four valid corners. Scientific arrays are never coarsened.
"""

import json
import math
import shutil
import zipfile
from importlib.metadata import distribution
from pathlib import Path

import numpy as np
import xarray as xr
from plotly.offline import get_plotlyjs
from scipy.ndimage import minimum_filter
from shapely import LineString, Polygon
from shapely.geometry import shape

from soil_sensing.io.storage import load_profile
from soil_sensing.volumes.contracts import ATTRIBUTES, digest


def _safe(value):
    if isinstance(value, np.ndarray):
        return _safe(value.tolist())
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def conservative_preview_mask(mask, step):
    """A hidden unsupported native voxel must not disappear through display stride."""
    if step == 1:
        return np.asarray(mask, bool)
    conservative = minimum_filter(
        np.asarray(mask, np.uint8), size=2 * step - 1, mode="constant", cval=0
    )
    return conservative[::step, ::step, ::step].astype(bool)


def write_volume_viewer(volume_root, output, *, interpretations=(), preview=None):
    volume_root, output = Path(volume_root), Path(output)
    preview = preview or {}
    if set(preview) - {"max_voxels", "max_payload_mb", "max_native_samples", "partition_ids"}:
        raise ValueError("Unknown volume preview option")
    max_voxels = preview.get("max_voxels", 60_000)
    max_native = preview.get("max_native_samples", 1_000_000)
    max_payload = preview.get("max_payload_mb", 30.0)
    if (
        any(type(v) is not int or v <= 0 for v in (max_voxels, max_native))
        or not np.isfinite(max_payload)
        or max_payload <= 0
    ):
        raise ValueError("Preview budgets must be finite positive limits")
    catalog = json.loads((volume_root / "products.json").read_text(encoding="utf-8"))
    selected = preview.get("partition_ids")
    if selected is not None:
        available = {p["partition_id"] for p in catalog["partitions"]}
        if (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(p, str) or p not in available for p in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError("preview.partition_ids must name unique existing complete partitions")
        catalog["partitions"] = [p for p in catalog["partitions"] if p["partition_id"] in selected]
    scenes = []
    downloads = output / "downloads"
    downloads.mkdir(exist_ok=True)
    for product in catalog["partitions"]:
        root = (volume_root / product["path"]).resolve()
        if not root.is_relative_to(volume_root.resolve()):
            raise ValueError("Volume product escapes its root")
        selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
        with xr.open_zarr(
            root / "volume.zarr", chunks=None, mask_and_scale=False, consolidated=True
        ) as stored:
            step = max(1, math.ceil((stored.envelope.size / max_voxels) ** (1 / 3)))
            cube = stored.isel(
                vertical=slice(None, None, step),
                y=slice(None, None, step),
                x=slice(None, None, step),
            ).load()
            for name in ATTRIBUTES:
                valid = conservative_preview_mask(stored[name + "_valid"].values, step)
                cube[name].values[~valid] = np.nan
        if cube.envelope.size > max_voxels:
            raise ValueError("Preview exceeds max_voxels; lower the configured budget/detail")
        native_index = json.loads((root / "native.json").read_text(encoding="utf-8"))
        sources, count = [], 0
        ox, oy = cube.attrs["origin_xy_m"]
        for row in native_index["profiles"]:
            path = (root / row["store"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Native source escapes partition")
            profile = load_profile(path)
            count += profile.amplitude.size
            if count > max_native:
                raise ValueError(
                    "Full-resolution B-scans exceed max_native_samples; "
                    "split preview into smaller selections"
                )
            if cube.attrs["coordinate_mode"] == "raw_gps_relative":
                from soil_sensing.visualization.scene import raw_gps_relative_xy

                xy = raw_gps_relative_xy(profile, cube.attrs["coordinate_anchor_latlon"])
            else:
                xy = np.c_[profile.x_m.values - ox, profile.y_m.values - oy]
            accepted = np.isin(
                profile.position_status.values,
                ["measured"]
                if selection["position_policy"] == "measured"
                else ["measured", "interpolated"],
            )
            xy[~accepted] = np.nan
            source = {
                **row,
                "trace_ids": profile.trace_id.values.astype(str).tolist(),
                "distance_m": profile.distance_m.values.tolist(),
                "xy": xy.tolist(),
                "time": profile.twt_ns.values.tolist(),
                "amplitude": profile.amplitude.values.tolist(),
                "position_status": profile.position_status.values.tolist(),
                "quality_flags": profile.quality_flags.values.tolist(),
            }
            sources.append(source)
        objects = []
        for interpreted_root in interpretations:
            interpreted_root = Path(interpreted_root)
            index = json.loads((interpreted_root / "products.json").read_text(encoding="utf-8"))
            if index.get("selection_hash") != selection["selection_hash"]:
                continue
            item = {"kind": index["kind"]}
            if index["kind"] == "interpreted_layers":
                with xr.open_zarr(
                    interpreted_root / "surfaces.zarr",
                    chunks=None,
                    mask_and_scale=False,
                    consolidated=True,
                ) as stored:
                    surfaces = stored.isel(
                        y=slice(None, None, step), x=slice(None, None, step)
                    ).load()
                    if step > 1:
                        for name, variable in surfaces.data_vars.items():
                            if (
                                variable.dims[-2:] == ("y", "x")
                                and variable.ndim == 3
                                and np.issubdtype(variable.dtype, np.floating)
                            ):
                                mask = minimum_filter(
                                    np.isfinite(stored[name].values).astype(np.uint8),
                                    size=(1, 2 * step - 1, 2 * step - 1),
                                    mode="constant",
                                    cval=0,
                                )[:, ::step, ::step].astype(bool)
                                variable.values[~mask] = np.nan
                item.update(
                    horizons=surfaces.horizon.values.tolist(),
                    values=surfaces[
                        "horizon_depth_m"
                        if cube.attrs["vertical_axis"] == "depth"
                        else "horizon_time_ns"
                    ].values.tolist(),
                    review_status=surfaces.attrs["review_status"],
                    layers=surfaces.attrs["layers"],
                    layer_values=surfaces.get(
                        "layer_thickness_m", surfaces.get("layer_interval_twt_ns")
                    ).values.tolist()
                    if surfaces.sizes.get("layer", 0)
                    else [],
                )
                if cube.attrs["vertical_axis"] != "depth":
                    t0 = cube.attrs.get("reviewed_time_zero_ns")
                    if (
                        surfaces.attrs["time_basis"] == "recording"
                        and cube.attrs["vertical_axis"] == "corrected_time"
                    ):
                        item["values"] = (np.asarray(item["values"]) - t0).tolist()
                    elif (
                        surfaces.attrs["time_basis"] == "corrected"
                        and cube.attrs["vertical_axis"] == "recording_time"
                    ):
                        if t0 is None:
                            raise ValueError(
                                "Corrected picks need explicit time zero "
                                "to render in recording time"
                            )
                        item["values"] = (np.asarray(item["values"]) + t0).tolist()
                item["interpretation"] = json.loads(
                    (interpreted_root / "interpretation.json").read_text(encoding="utf-8")
                )
                from soil_sensing.export.interpretation import supported_surface_mesh

                item["layer_unit"] = "m" if "layer_thickness_m" in surfaces else "ns"
                item["shells"] = []
                for layer in item["layers"]:
                    a, b = (item["horizons"].index(layer[k]) for k in ("top", "bottom"))
                    thickness = (
                        surfaces[
                            "layer_thickness_m"
                            if "layer_thickness_m" in surfaces
                            else "layer_interval_twt_ns"
                        ]
                        .sel(layer=layer["layer_id"])
                        .values
                    )
                    top, bottom = (
                        np.where(np.isfinite(thickness), item["values"][i], np.nan) for i in (a, b)
                    )
                    shell = supported_surface_mesh(
                        cube.x.values,
                        cube.y.values,
                        top,
                        bottom,
                        domain=shape(cube.attrs["domain_geojson"]),
                    )
                    shell["object_id"] = layer["layer_id"]
                    shell["intensity"] = [
                        float(
                            thickness[
                                np.searchsorted(cube.y.values, v[1]),
                                np.searchsorted(cube.x.values, v[0]),
                            ]
                        )
                        for v in shell["vertices"]
                    ]
                    item["shells"].append(shell)
            else:
                item.update(
                    json.loads(
                        (interpreted_root / "pipe_candidates.json").read_text(encoding="utf-8")
                    )
                )
            objects.append(item)
        zip_path = downloads / (product["partition_id"] + ".zip")
        with zipfile.ZipFile(zip_path, "x", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
            for interpreted_root in interpretations:
                catalog_path = Path(interpreted_root) / "products.json"
                interpretation_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                if interpretation_catalog.get("selection_hash") != selection["selection_hash"]:
                    continue
                for path in sorted(Path(interpreted_root).rglob("*")):
                    if path.is_file() and path.suffix != ".zip":
                        archive.write(
                            path,
                            "interpretations/"
                            + Path(interpreted_root).name
                            + "/"
                            + path.relative_to(interpreted_root).as_posix(),
                        )
        domain = shape(cube.attrs["domain_geojson"])
        x, y = cube.x.values, cube.y.values
        cells = [
            [
                domain.covers(Polygon([(a, b), (c, b), (c, d), (a, d)]))
                for a, c in zip(x[:-1], x[1:], strict=True)
            ]
            for b, d in zip(y[:-1], y[1:], strict=True)
        ]
        x_segments = [
            [domain.covers(LineString([(a, b), (a, d)])) for a in x]
            for b, d in zip(y[:-1], y[1:], strict=True)
        ]
        y_segments = [
            [
                domain.covers(LineString([(a, b), (c, b)]))
                for a, c in zip(x[:-1], x[1:], strict=True)
            ]
            for b in y
        ]
        scenes.append(
            dict(
                partition_id=product["partition_id"],
                metadata=cube.attrs,
                selection=selection,
                capabilities=product["capabilities"],
                x=cube.x.values,
                y=cube.y.values,
                vertical=cube.vertical.values,
                attributes={n: cube[n].values for n in ATTRIBUTES},
                source_profiles=sources,
                interpretations=objects,
                preview_step=step,
                cells=cells,
                x_segments=x_segments,
                y_segments=y_segments,
                download="downloads/" + zip_path.name,
            )
        )
    payload = _safe(dict(schema_version="1.0", kind="volume_viewer", scenes=scenes))
    payload["viewer_hash"] = digest(payload)
    encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode()) > max_payload * 1_000_000:
        raise ValueError(
            "Volume viewer exceeds max_payload_mb; reduce preview detail or native selection"
        )
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8")
    package = distribution("plotly")
    license_file = next(p for p in package.files if str(p).endswith("licenses/LICENSE.txt"))
    (assets / "plotly.LICENSE.txt").write_bytes(package.locate_file(license_file).read_bytes())
    templates = Path(__file__).parent / "assets"
    shutil.copyfile(templates / "volume.js", assets / "volume.js")
    template = (templates / "volume.html").read_text(encoding="utf-8")
    safe = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    (output / "index.html").write_text(template.replace("__VOLUME__", safe), encoding="utf-8")
    return {
        "payload_bytes": len(encoded.encode()),
        "partition_count": len(scenes),
        "renderer": "explicit_four_corner_masked_slices",
    }
