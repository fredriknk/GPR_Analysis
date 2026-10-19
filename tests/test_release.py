"""Synthetic GIS roundtrips, verified reports and dependency-cache invalidation."""

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HAS_RELEASE = all(
    importlib.util.find_spec(m)
    for m in ("numpy", "xarray", "rasterio", "geopandas", "pyarrow", "shapely")
)
if HAS_RELEASE:
    import geopandas as gpd
    import numpy as np
    import rasterio

    from soil_sensing.batch import batch_run, load_batch_config
    from soil_sensing.export.gis import export_grid_run, export_predictions_run
    from soil_sensing.export.report import report_run, verify_run
    from soil_sensing.ingest import write_json
    from soil_sensing.runs import begin_run, finish_run
    from soil_sensing.spatial.gridding import GridOptions, polygon_domain
    from soil_sensing.spatial.interpolation import build_grid
    from soil_sensing.visualization.preview import ALLOWED, preview_files


@unittest.skipUnless(HAS_RELEASE, "Requires geo, visualization and processing extras")
class ReleaseTests(unittest.TestCase):
    def test_loopback_preview_allows_only_verified_explicit_synthetic_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = begin_run("visualize", Path(tmp) / "scene", [])
            for name in ALLOWED:
                if name == "run.json":
                    continue
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture", encoding="utf-8")
            write_json(root / "scene.json", {"synthetic": True})
            finish_run(root, manifest)
            files = preview_files(root)
            self.assertEqual(set(files), {"/", *("/" + name for name in ALLOWED)})
            self.assertNotIn("/../scene.json", files)
            write_json(root / "scene.json", {"synthetic": False})
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                preview_files(root)
            finish_run(root, manifest)
            with self.assertRaisesRegex(ValueError, "explicitly synthetic"):
                preview_files(root)

    def grid(self, root, *, depth=False):
        boundary = [[0, 0], [20, 0], [20, 20], [0, 20]]
        rows = [
            {
                "xy": [x, y],
                "value": x + 2 * y,
                "line_id": str(x),
                "trace_id": f"fixture/{x}/{y}",
                "position_status": "measured",
            }
            for x, y in boundary
        ]
        metadata = {
            "plot_id": "synthetic_01",
            "survey_id": "fixture",
            "crs": "EPSG:32632",
            "origin_xy_m": [500000.0, 6600000.0],
            "gate_bounds": [[20, 40]],
            "gate_unit": "ns",
            "vertical_axis": "recording_time",
            "attribute_unit": "ADC counts",
            "source_profiles": [],
            "processing_signature": "fixture",
            "calibration": None,
            "synthetic": True,
        }
        if depth:
            metadata.update(
                vertical_axis="depth",
                gate_unit="m",
                gate_bounds=[[0.1, 0.3]],
                calibration={"calibration_id": "synthetic-fixture", "evidence": "synthetic only"},
            )
        grid, support = build_grid(
            [rows],
            boundary,
            polygon_domain(boundary),
            GridOptions(spacing_m=10, max_edge_m=30, max_distance_m=30),
            metadata,
        )
        grid["valid_support"].values[0, 0, 0] = False
        grid["value"].values[0, 0, 0] = np.nan
        grid["nearest_value"].values[0, 0, 0] = np.nan
        grid["contributor_count"].values[0, 0, 0] = 0
        grid["support_index"].values[0, 0, 0] = -1
        grid["nearest_support_index"].values[0, 0, 0] = -1
        store = root / "grid.zarr"
        grid.to_zarr(
            store,
            consolidated=True,
            zarr_format=2,
            encoding={v: {"_FillValue": None} for v in grid.variables},
        )
        write_json(root / "support.json", {"gates": support})
        return store, grid

    def seed_run(self, root, text="fixture"):
        root, manifest = begin_run("<script>fixture</script>", root, [])
        (root / "index.html").write_text(text, encoding="utf-8")
        finish_run(root, manifest)
        return root

    def test_geotiff_preserves_world_crs_north_up_centres_gates_and_missing_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, grid = self.grid(root)
            result = export_grid_run(store, root / "gis")
            self.assertEqual(result["status"], "complete")
            with rasterio.open(root / "gis/radar_attribute.tif") as image:
                self.assertEqual(image.crs.to_epsg(), 32632)
                np.testing.assert_array_equal(image.read(1), np.flip(grid.value.values[0], axis=0))
                self.assertEqual(image.xy(0, 0), (500005.0, 6600015.0))
                self.assertEqual(image.tags(1)["gate_unit"], "ns")
                self.assertTrue(np.isnan(image.nodata))
            with rasterio.open(root / "gis/valid_support.tif") as mask:
                self.assertEqual(mask.read(1)[1, 0], 0)
                self.assertEqual(mask.nodata, 255)
            coverage = gpd.read_file(root / "gis/coverage.gpkg", layer="supported_cells")
            self.assertEqual(coverage.crs.to_epsg(), 32632)
            self.assertAlmostEqual(float(coverage.geometry.area.sum()), 300.0)
            self.assertTrue((root / "gis/support.json").exists())
            with self.assertRaisesRegex(ValueError, "new directory"):
                export_grid_run(store, root / "gis")

    def test_texture_vectors_keep_unsupported_nodata_units_model_and_crs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.json"
            write_json(
                reference,
                {"model_id": "synthetic-model", "target_definition": {"method": "synthetic"}},
            )
            path = root / "predictions.csv"
            rows = [
                {
                    "support_id": "a",
                    "plot_id": "fixture",
                    "survey_id": "fixture",
                    "x_m": 500000,
                    "y_m": 6600000,
                    "crs": "EPSG:32632",
                    "depth_top_m": 0.1,
                    "depth_bottom_m": 0.3,
                    "model_id": "synthetic-model",
                    "unit": "fraction",
                    "valid_support": "true",
                    "sand_fraction": 0.5,
                    "silt_fraction": 0.3,
                    "clay_fraction": 0.2,
                },
                {
                    "support_id": "b",
                    "plot_id": "fixture",
                    "survey_id": "fixture",
                    "x_m": 500001,
                    "y_m": 6600000,
                    "crs": "EPSG:32632",
                    "depth_top_m": 0.1,
                    "depth_bottom_m": 0.3,
                    "model_id": "synthetic-model",
                    "unit": "fraction",
                    "valid_support": "false",
                    "sand_fraction": "",
                    "silt_fraction": "",
                    "clay_fraction": "",
                },
            ]
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            result = export_predictions_run(path, reference, root / "gis")
            self.assertEqual(result["valid_support_count"], 1)
            frame = gpd.read_parquet(root / "gis/texture_estimates_01.parquet")
            self.assertTrue(np.isnan(frame.sand_fraction.iloc[1]))
            self.assertEqual(frame.unit.iloc[0], "fraction")
            self.assertFalse(bool(frame.valid_support.iloc[1]))

    def test_calibrated_depth_gate_and_evidence_survive_gis_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, _ = self.grid(root, depth=True)
            export_grid_run(store, root / "gis")
            with rasterio.open(root / "gis/radar_attribute.tif") as image:
                self.assertEqual(image.tags()["vertical_axis"], "depth")
                self.assertEqual(image.tags(1)["gate_unit"], "m")
                self.assertEqual(float(image.tags(1)["gate_start"]), 0.1)
                self.assertEqual(float(image.tags(1)["gate_stop"]), 0.3)
            metadata = json.loads((root / "gis/grid_metadata.json").read_text())
            self.assertEqual(
                metadata["grid_attributes"]["calibration"]["calibration_id"], "synthetic-fixture"
            )

    def test_report_copies_verified_artifacts_escapes_labels_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = self.seed_run(root / "seed")
            report_run([seed], root / "report")
            page = (root / "report/index.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>fixture</script>", page)
            self.assertIn("&lt;script&gt;fixture", page)
            self.assertEqual((root / "report/run-01/index.html").read_text(), "fixture")
            (seed / "index.html").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                report_run([seed], root / "bad")
            self.assertFalse((root / "bad").exists())

    def test_batch_cache_reuses_verified_dependencies_and_invalidates_changed_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = self.seed_run(root / "seed")
            config = root / "batch.toml"
            config.write_text(
                'schema_version="0.1"\n[[jobs]]\njob_id="first"\noperation="report"\nruns=["seed"]\n'
                '[[jobs]]\njob_id="second"\noperation="report"\nruns=["@first"]\ndepends_on=["first"]\n'
            )
            first = batch_run(config, root / "batch-1", cache=root / "cache")
            self.assertEqual(first["reused_jobs"], 0)
            second = batch_run(config, root / "batch-2", cache=root / "cache")
            self.assertEqual(second["reused_jobs"], 2)
            record = verify_run(seed)
            (seed / "index.html").write_text("new verified source version", encoding="utf-8")
            finish_run(seed, record)
            third = batch_run(config, root / "batch-3", cache=root / "cache")
            self.assertEqual(third["reused_jobs"], 0)
            cached = Path(third["jobs"][0]["run"])
            (cached / "index.html").write_text("corruption", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                batch_run(config, root / "batch-4", cache=root / "cache")
            self.assertEqual(
                json.loads((root / "batch-4/run.json").read_text())["status"], "failed"
            )

    def test_failed_runs_cycles_and_escaping_artifact_references_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = self.seed_run(root / "seed")
            record = json.loads((seed / "run.json").read_text())
            record["status"] = "failed"
            write_json(seed / "run.json", record)
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                verify_run(seed)
            config = root / "cycle.toml"
            config.write_text(
                'schema_version="0.1"\n[[jobs]]\njob_id="a"\noperation="report"\nruns=["seed"]\ndepends_on=["a"]\n'
            )
            with self.assertRaisesRegex(ValueError, "cycle"):
                load_batch_config(config)
            seed = self.seed_run(root / "fresh")
            config.write_text(
                'schema_version="0.1"\n[[jobs]]\njob_id="a"\noperation="report"\nruns=["fresh"]\n'
                '[[jobs]]\njob_id="b"\noperation="report"\nruns=["@a/../fresh"]\ndepends_on=["a"]\n'
            )
            with self.assertRaisesRegex(ValueError, "escapes"):
                batch_run(config, root / "escape")
