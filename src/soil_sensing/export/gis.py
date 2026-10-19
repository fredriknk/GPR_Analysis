"""Lossless metric GIS adapters with explicit support, units and provenance."""

import csv
import json
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import xarray as xr
from pyproj import CRS
from rasterio.features import shapes
from rasterio.transform import from_origin, xy
from shapely.affinity import translate
from shapely.geometry import shape

from soil_sensing.ingest import write_json
from soil_sensing.runs import begin_run, finish_run
from soil_sensing.spatial.interpolation import validate_grid


def export_grid_run(input_store, output):
    input_store = Path(input_store).resolve()
    support_file = input_store.parent / "support.json"
    output, manifest = begin_run("export_attribute_grid", output, [input_store, support_file])
    try:
        with xr.open_zarr(
            input_store, chunks=None, mask_and_scale=False, consolidated=True
        ) as source:
            grid = source.load()
        validate_grid(grid)
        x, y = grid.x.values, grid.y.values
        spacing = grid.attrs.get("grid_options", {}).get("spacing_m")
        if spacing is None or not np.isfinite(spacing) or spacing <= 0:
            raise ValueError("GIS raster export requires explicit positive cell spacing")
        if any(
            len(v) > 1 and not np.allclose(np.diff(v), spacing, rtol=0, atol=1e-8) for v in (x, y)
        ):
            raise ValueError(
                "Nonuniform grids require an explicit resampling step before raster export"
            )
        ox, oy = grid.attrs["origin_xy_m"]
        transform = from_origin(ox + x[0] - spacing / 2, oy + y[-1] + spacing / 2, spacing, spacing)
        files = {
            "radar_attribute": (grid.value.values, "attribute_unit"),
            "nearest_baseline": (grid.nearest_value.values, "attribute_unit"),
            "nearest_support_distance_m": (grid.nearest_distance_m.values, "m"),
            "contributor_count": (grid.contributor_count.values, "count"),
            "valid_support": (grid.valid_support.values, "bool"),
        }
        for name, (values, unit) in files.items():
            is_mask = name == "valid_support"
            data = np.flip(values, axis=1).astype(np.uint8 if is_mask else np.float64)
            path = output / (name + ".tif")
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                height=len(y),
                width=len(x),
                count=grid.sizes["gate"],
                dtype=data.dtype,
                crs=grid.attrs["crs"],
                transform=transform,
                nodata=255 if is_mask else np.nan,
                compress="deflate",
            ) as target:
                target.write(data)
                target.update_tags(
                    AREA_OR_POINT="Area",
                    plot_id=grid.attrs["plot_id"],
                    survey_id=grid.attrs["survey_id"],
                    vertical_axis=grid.attrs["vertical_axis"],
                    unit=grid.attrs["attribute_unit"] if unit == "attribute_unit" else unit,
                    metadata_reference="grid_metadata.json",
                    synthetic=str(grid.attrs.get("synthetic", False)).lower(),
                )
                for gate in range(grid.sizes["gate"]):
                    start, stop = (
                        float(grid.gate_start.values[gate]),
                        float(grid.gate_stop.values[gate]),
                    )
                    target.set_band_description(
                        gate + 1, f"{start:g}–{stop:g} {grid.attrs['gate_unit']}"
                    )
                    target.update_tags(
                        gate + 1,
                        gate_start=str(start),
                        gate_stop=str(stop),
                        gate_unit=grid.attrs["gate_unit"],
                    )
            with rasterio.open(path) as restored:
                np.testing.assert_array_equal(restored.read(), data)
                if (
                    restored.crs != rasterio.crs.CRS.from_user_input(grid.attrs["crs"])
                    or restored.transform != transform
                ):
                    raise ValueError("Raster CRS/transform roundtrip failed")
                centre = xy(restored.transform, len(y) - 1, 0)
                np.testing.assert_allclose(centre, [ox + x[0], oy + y[0]], rtol=0, atol=1e-7)
        domain = translate(shape(grid.attrs["domain_geojson"]), xoff=ox, yoff=oy)
        footprint = gpd.GeoDataFrame(
            [
                {
                    "plot_id": grid.attrs["plot_id"],
                    "survey_id": grid.attrs["survey_id"],
                    "synthetic": grid.attrs.get("synthetic", False),
                    "kind": "configured_supported_domain",
                }
            ],
            geometry=[domain],
            crs=grid.attrs["crs"],
        )
        footprint.to_file(
            output / "coverage.gpkg", layer="domain", driver="GPKG", engine="pyogrio", index=False
        )
        restored_domain = gpd.read_file(output / "coverage.gpkg", layer="domain")
        if (
            restored_domain.crs != footprint.crs
            or not restored_domain.geometry.geom_equals(footprint.geometry).all()
        ):
            raise ValueError("Domain GeoPackage roundtrip failed")
        cells = []
        for gate in range(grid.sizes["gate"]):
            mask = np.flip(grid.valid_support.values[gate], axis=0).astype(np.uint8)
            for geometry, value in shapes(mask, mask=mask.astype(bool), transform=transform):
                if value:
                    cells.append(
                        {
                            "plot_id": grid.attrs["plot_id"],
                            "survey_id": grid.attrs["survey_id"],
                            "gate_start": float(grid.gate_start.values[gate]),
                            "gate_stop": float(grid.gate_stop.values[gate]),
                            "gate_unit": grid.attrs["gate_unit"],
                            "geometry": shape(geometry),
                        }
                    )
        if cells:
            frame = gpd.GeoDataFrame(cells, crs=grid.attrs["crs"])
            frame.to_file(
                output / "coverage.gpkg",
                layer="supported_cells",
                driver="GPKG",
                engine="pyogrio",
                index=False,
            )
            frame.to_parquet(output / "supported_cells.parquet", index=False)
            for restored in (
                gpd.read_parquet(output / "supported_cells.parquet"),
                gpd.read_file(output / "coverage.gpkg", layer="supported_cells"),
            ):
                if (
                    restored.crs != frame.crs
                    or not restored.geometry.geom_equals(frame.geometry).all()
                ):
                    raise ValueError("Coverage vector roundtrip failed")
        write_json(
            output / "grid_metadata.json",
            {
                "schema_version": "0.1",
                "kind": "radar_attribute_not_soil_prediction",
                "grid_attributes": grid.attrs,
                "pixel_interpretation": "cell_area; spacing is not measured resolution",
                "gate_start": grid.gate_start.values.tolist(),
                "gate_stop": grid.gate_stop.values.tolist(),
                "support_file": "support.json",
                "source_grid": str(input_store),
            },
        )
        shutil.copyfile(support_file, output / "support.json")
        manifest.update(
            plot_id=grid.attrs["plot_id"],
            survey_id=grid.attrs["survey_id"],
            synthetic=grid.attrs.get("synthetic", False),
            supported_fraction=float(grid.valid_support.values.mean()),
        )
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return finish_run(output, manifest)


def export_predictions_run(input_csv, reference, output):
    output, manifest = begin_run("export_texture_estimates", output, [input_csv, reference])
    try:
        metadata = json.loads(Path(reference).read_text(encoding="utf-8"))
        if not metadata.get("model_id") or not metadata.get("target_definition"):
            raise ValueError(
                "GIS texture estimates require model identity and laboratory definition"
            )
        with Path(input_csv).open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            raise ValueError("No texture estimate rows")
        by_crs = {}
        for row in rows:
            if row.get("model_id") != metadata["model_id"] or row.get("unit") != "fraction":
                raise ValueError("Texture estimate model/unit mismatch")
            crs = CRS.from_user_input(row["crs"])
            if not crs.is_projected or any(
                a.unit_name.lower() not in ("metre", "meter") for a in crs.axis_info[:2]
            ):
                raise ValueError("GIS texture supports require metric projected coordinates")
            for key in ("x_m", "y_m", "depth_top_m", "depth_bottom_m"):
                row[key] = float(row[key])
                if not np.isfinite(row[key]):
                    raise ValueError("Texture support coordinates/depth must be finite")
            if not 0 <= row["depth_top_m"] < row["depth_bottom_m"]:
                raise ValueError(
                    "Texture support depth interval must be nonempty and positive-down"
                )
            if row.get("valid_support", "").lower() not in ("true", "false"):
                raise ValueError("Estimate valid_support must be Boolean")
            row["valid_support"] = row["valid_support"].lower() == "true"
            for component in ("sand_fraction", "silt_fraction", "clay_fraction"):
                for suffix in ("", "_lower", "_upper"):
                    key = component + suffix
                    row[key] = float(row[key]) if row.get(key) not in (None, "") else np.nan
                    if np.isinf(row[key]):
                        raise ValueError("Texture estimates and intervals cannot be infinite")
            values = np.array([row[t] for t in ("sand_fraction", "silt_fraction", "clay_fraction")])
            if row["valid_support"]:
                if (
                    not np.isfinite(values).all()
                    or (values < 0).any()
                    or (values > 1).any()
                    or not np.isclose(values.sum(), 1)
                ):
                    raise ValueError("Supported fraction estimates must close to one")
            elif np.isfinite(values).any():
                raise ValueError("Unsupported estimates must remain missing")
            for component in ("sand_fraction", "silt_fraction", "clay_fraction"):
                lower, upper = row[component + "_lower"], row[component + "_upper"]
                if np.isfinite(lower) != np.isfinite(upper):
                    raise ValueError("Interval bounds must be supplied together")
                if np.isfinite(lower) and (
                    not row["valid_support"] or not 0 <= lower <= row[component] <= upper <= 1
                ):
                    raise ValueError("Invalid or unsupported texture interval bounds")
            by_crs.setdefault(crs.to_string(), []).append(row)
        for i, (crs, group) in enumerate(sorted(by_crs.items()), 1):
            frame = gpd.GeoDataFrame(
                group,
                geometry=gpd.points_from_xy([r["x_m"] for r in group], [r["y_m"] for r in group]),
                crs=crs,
            )
            stem = f"texture_estimates_{i:02d}"
            frame.to_file(
                output / (stem + ".gpkg"),
                layer="texture_estimates",
                driver="GPKG",
                engine="pyogrio",
                index=False,
            )
            frame.to_parquet(output / (stem + ".parquet"), index=False)
            for restored in (
                gpd.read_file(output / (stem + ".gpkg")),
                gpd.read_parquet(output / (stem + ".parquet")),
            ):
                if (
                    restored.crs != frame.crs
                    or not restored.geometry.geom_equals(frame.geometry).all()
                ):
                    raise ValueError("Texture vector CRS roundtrip failed")
                for key in (
                    "sand_fraction",
                    "silt_fraction",
                    "clay_fraction",
                    "depth_top_m",
                    "depth_bottom_m",
                    "sand_fraction_lower",
                    "sand_fraction_upper",
                    "silt_fraction_lower",
                    "silt_fraction_upper",
                    "clay_fraction_lower",
                    "clay_fraction_upper",
                ):
                    np.testing.assert_allclose(
                        restored[key], frame[key], equal_nan=True, rtol=0, atol=1e-12
                    )
        write_json(output / "model_reference.json", metadata)
        manifest.update(
            model_id=metadata["model_id"],
            support_count=len(rows),
            valid_support_count=sum(r["valid_support"] for r in rows),
            synthetic=metadata.get("synthetic"),
        )
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return finish_run(output, manifest)
