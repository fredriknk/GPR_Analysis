"""Synthetic spatial recovery, leakage, scene contracts and offline run regressions."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from soil_sensing.cli import main

HAS_VISUALIZATION = all(
    importlib.util.find_spec(m)
    for m in ("numpy", "scipy", "xarray", "zarr", "pyproj", "plotly", "shapely")
)
if HAS_VISUALIZATION:
    import numpy as np
    import xarray as xr

    from soil_sensing.export.scene import load_scene_config, visualize_run
    from soil_sensing.ingest import tree_hash
    from soil_sensing.io.storage import save_profile
    from soil_sensing.processing.depth import VelocityCalibration, add_depth_axis
    from soil_sensing.spatial.gridding import (
        GridOptions,
        aggregate_support,
        grid_coordinates,
        polygon_domain,
    )
    from soil_sensing.spatial.interpolation import (
        build_grid,
        evaluate_lines,
        interpolate_support,
        validate_grid,
    )
    from soil_sensing.visualization.scene import build_scene, validate_gates
    from soil_sensing.visualization.synthetic import synthetic_plots

BOUNDARY = [[0, 0], [10, 0], [10, 10], [0, 10]]


def observations(points, function=lambda x, y: 3 * x + 2 * y + 5):
    return [
        {
            "xy": [x, y],
            "value": function(x, y),
            "trace_id": f"source/line/{i}",
            "line_id": f"line/{x}",
            "position_status": "measured",
        }
        for i, (x, y) in enumerate(points)
    ]


@unittest.skipUnless(HAS_VISUALIZATION, "Requires processing, field and visualization extras")
class InterpolationTests(unittest.TestCase):
    def setUp(self):
        self.domain = polygon_domain(BOUNDARY)
        self.options = GridOptions(spacing_m=1, bin_m=0.5, max_distance_m=20, max_edge_m=20)
        self.rows = observations([(0, 0), (10, 0), (0, 10), (10, 10)])

    def interpolate(self, rows, query, domain=None, options=None):
        return interpolate_support(
            aggregate_support(rows, 0.5, [0, 0]),
            query,
            self.domain if domain is None else domain,
            options or self.options,
        )

    def test_constant_linear_recovery_and_no_hull_extrapolation(self):
        for function in (lambda x, y: 7, lambda x, y: 3 * x + 2 * y + 5):
            rows = observations([(0, 0), (10, 0), (0, 10), (10, 10)], function)
            result = self.interpolate(rows, [[3, 4], [7, 8], [11, 1]])
            np.testing.assert_allclose(result["value"][:2], [function(3, 4), function(7, 8)])
            self.assertFalse(result["valid_support"][-1])
            self.assertTrue(np.isnan(result["value"][-1]))
            self.assertTrue((result["support_index"][-1] == -1).all())

    def test_holes_and_disconnected_regions_reject_whole_crossing_triangles(self):
        hole = [[4, 4], [6, 4], [6, 6], [4, 6]]
        domain = polygon_domain(BOUNDARY, exclusions=[hole])
        # All corner triangles cross the hole, including queries outside the hole.
        result = self.interpolate(self.rows, [[1, 1], [5, 5], [9, 9]], domain)
        self.assertFalse(result["valid_support"].any())
        regions = [[[0, 0], [3, 0], [3, 10], [0, 10]], [[7, 0], [10, 0], [10, 10], [7, 10]]]
        domain = polygon_domain(BOUNDARY, regions=regions)
        rows = observations([(x, y) for x in (0, 2, 8, 10) for y in (0, 2, 8, 10)])
        result = self.interpolate(rows, [[1, 1], [5, 5], [9, 9]], domain)
        np.testing.assert_array_equal(result["valid_support"], [True, False, True])

    def test_collinear_sparse_long_edges_and_contributor_distance(self):
        for rows in ([], self.rows[:2], observations([(0, 0), (5, 0), (10, 0)])):
            self.assertFalse(self.interpolate(rows, [[1, 1]])["valid_support"].any())
        for options in (
            replace(self.options, max_edge_m=4),
            replace(self.options, max_distance_m=2),
        ):
            self.assertFalse(
                self.interpolate(self.rows, [[1, 1]], options=options)["valid_support"].any()
            )

    def test_duplicate_locations_and_dense_bins_retain_sources(self):
        rows = observations([(0, 0), (0, 0), (0.1, 0.1), (10, 0), (0, 10), (10, 10)])
        rows[1]["line_id"] = "other-survey-line"
        support = aggregate_support(rows, 1, [0, 0])
        self.assertEqual(sum(s["count"] for s in support), len(rows))
        self.assertEqual(
            {t for s in support for t in s["trace_ids"]}, {r["trace_id"] for r in rows}
        )
        result = interpolate_support(support, [[2, 2]], self.domain, self.options)
        self.assertAlmostEqual(result["value"][0], 15)

    def test_whole_line_holdout_refits_bins_and_reports_coverage(self):
        rows = observations([(x, y) for x in (0, 2, 4, 6, 8, 10) for y in (0, 2, 4, 6, 8, 10)])
        fitted = []
        original = aggregate_support

        def recording_fit(training, *args):
            fitted.append({r["line_id"] for r in training})
            return original(training, *args)

        with patch(
            "soil_sensing.spatial.interpolation.aggregate_support", side_effect=recording_fit
        ):
            reports = evaluate_lines([rows], self.domain, self.options, [0, 0])
        self.assertEqual(len(reports), 6)
        for report, lines in zip(reports, fitted, strict=True):
            self.assertNotIn(report["held_out_line"], lines)
            self.assertEqual(report["held_out_count"], 6)
            if report["supported_count"]:
                self.assertAlmostEqual(report["metrics"]["linear"]["mae"], 0)
            else:
                self.assertIsNone(report["metrics"]["linear"]["rmse"])
        self.assertEqual(reports[0]["supported_fraction"], 0)

    def test_grid_contract_roundtrip_masks_units_and_budget(self):
        metadata = {
            "crs": "EPSG:32632",
            "origin_xy_m": [500000, 6600000],
            "plot_id": "test",
            "survey_id": "session",
            "gate_unit": "ns",
            "attribute_unit": "ADC counts",
            "vertical_axis": "recording_time",
            "source_profiles": [],
            "processing_signature": "fixture",
            "calibration": None,
            "gate_bounds": [[0, 10]],
        }
        grid, support = build_grid([self.rows], BOUNDARY, self.domain, self.options, metadata)
        self.assertTrue(grid.valid_support.values.all())
        self.assertEqual(len(support), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grid.zarr"
            grid.to_zarr(
                path,
                zarr_format=2,
                consolidated=True,
                encoding={v: {"_FillValue": None} for v in grid.variables},
            )
            with xr.open_zarr(path, chunks=None, mask_and_scale=False) as stored:
                xr.testing.assert_identical(grid, stored.load())
        invalid = grid.copy(deep=True)
        invalid["valid_support"].values[:] = False
        with self.assertRaisesRegex(ValueError, "mask"):
            validate_grid(invalid)
        with self.assertRaisesRegex(ValueError, "max_cells"):
            grid_coordinates(BOUNDARY, replace(self.options, max_cells=2), 1)


@unittest.skipUnless(HAS_VISUALIZATION, "Requires processing, field and visualization extras")
class SceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles, cls.plots, cls.exclusions = synthetic_plots()

    def scene(self, profiles=None, **options):
        profiles = profiles if profiles is not None else self.profiles[:9]
        return build_scene(profiles, self.plots["synthetic_01"], [[30, 40], [40, 50]], **options)

    def test_metric_scene_preserves_identity_position_status_and_gaps(self):
        scene = self.scene(exclusions=self.exclusions["synthetic_01"])
        self.assertEqual(scene.origin_xy_m, [500000, 6600000])
        self.assertEqual(scene.gate_unit, "ns")
        self.assertEqual(scene.quality["excluded_positions"], 45)
        self.assertTrue(any(r["position_status"] == "interpolated" for r in scene.observations[0]))
        self.assertGreater(len(scene.curtains), 9)
        for curtain in scene.curtains:
            x = np.array(curtain["xy"])[:, 0]
            self.assertTrue((np.diff(x) <= 6).all())
            self.assertTrue(all(t.startswith("demo/synthetic_01/") for t in curtain["trace_ids"]))

    def test_incompatible_units_axes_processing_survey_and_duplicates_fail(self):
        for key, value, message in (
            ("amplitude_units", "volts", "Incompatible processing"),
            ("survey_id", "another-survey", "surveys"),
        ):
            altered = self.profiles[1].copy(deep=True)
            altered.attrs[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, message):
                self.scene([self.profiles[0], altered])
        altered = self.profiles[1].copy(deep=True)
        altered.attrs["sample_interval_ns"] = 2
        altered = altered.assign_coords(twt_ns=("sample", np.arange(100) * 2.0))
        with self.assertRaisesRegex(ValueError, "axes"):
            self.scene([self.profiles[0], altered])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.scene([self.profiles[0], self.profiles[0]])
        raw = self.profiles[0].copy(deep=True)
        raw.attrs["processing_branch"] = "display"
        with self.assertRaisesRegex(ValueError, "quantitative"):
            self.scene([raw])

    def test_unassigned_only_diagnostic_and_no_false_depth(self):
        profile = self.profiles[0].copy(deep=True)
        profile.attrs.update(plot_id=None, survey_id=None, assignment=None)
        with self.assertRaisesRegex(ValueError, "assignments"):
            self.scene([profile])
        diagnostic = self.scene([profile], diagnostic=True)
        self.assertIsNone(diagnostic.crs)
        self.assertTrue(diagnostic.diagnostic)
        with self.assertRaisesRegex(ValueError, "time zero"):
            self.scene(vertical_axis="depth")

    def test_calibrated_depth_retains_uncertainty_and_rejects_missing_evidence(self):
        profile = self.profiles[0].copy(deep=True)
        profile.attrs["processing_options"] = {
            "reviewed_time_zero_ns": 0,
            "time_zero_evidence": "synthetic",
        }
        profile.attrs["antenna_separation_m"] = 0.5
        profile = profile.assign_coords(corrected_twt_ns=("sample", profile.twt_ns.values))
        calibration = VelocityCalibration("synthetic-v", "synthetic control", 0.1, 0.005, 0.2)
        profile = add_depth_axis(profile, calibration)
        scene = build_scene(
            [profile], self.plots["synthetic_01"], [[1, 2], [2, 3]], vertical_axis="depth"
        )
        self.assertEqual(scene.gate_unit, "m")
        self.assertEqual(scene.calibration["calibration"]["calibration_id"], "synthetic-v")
        self.assertTrue(all(v > 0 for v in scene.curtains[0]["depth_standard_uncertainty_m"]))
        profile.attrs["depth_model"]["calibration"]["evidence"] = ""
        with self.assertRaisesRegex(ValueError, "evidence"):
            build_scene([profile], self.plots["synthetic_01"], [[1, 2]], vertical_axis="depth")

    def test_invalid_windows_and_clipped_samples_never_enter_grid_observations(self):
        profile = self.profiles[0].copy(deep=True)
        profile["adc_limit_mask"].values[0, 30:40] = True
        scene = self.scene([profile])
        trace_id = str(profile.trace_id.values[0])
        self.assertNotIn(trace_id, {r["trace_id"] for r in scene.observations[0]})
        self.assertIn(trace_id, {r["trace_id"] for r in scene.observations[1]})
        for gates in ([], [[1, 1]], [[0, 10], [5, 15]], [[True, 10]], [[0, float("nan")]]):
            with self.assertRaises(ValueError):
                validate_gates(gates)

    def test_display_decimation_does_not_change_quantitative_support(self):
        detailed = self.scene(trace_step=1, sample_step=1)
        preview = self.scene(trace_step=8, sample_step=8)
        self.assertEqual(detailed.observations, preview.observations)
        self.assertGreater(
            len(detailed.curtains[0]["trace_ids"]), len(preview.curtains[0]["trace_ids"])
        )


@unittest.skipUnless(HAS_VISUALIZATION, "Requires processing, field and visualization extras")
class ExplorerRunTests(unittest.TestCase):
    def test_reviewed_processed_stores_build_geographic_grid_with_parent_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles, plots, _ = synthetic_plots()
            for index, profile in enumerate(profiles[:3]):
                save_profile(profile, root / f"line-{index}.zarr")
            points = [list(p) for p in plots["synthetic_01"].polygon]
            (root / "plots.toml").write_text(
                'schema_version="0.1"\n[[plots]]\nplot_id="synthetic_01"\n'
                'width_m=100\nlength_m=100\ngeometry_status="reviewed"\n'
                'crs="EPSG:32632"\nevidence="synthetic control"\npolygon=' + json.dumps(points)
            )
            config = root / "scene.toml"
            config.write_text(
                'schema_version="0.1"\nprofiles=["line-0.zarr","line-1.zarr","line-2.zarr"]\n'
                'plots_file="plots.toml"\ngates=[[30,40]]\n[grid]\nspacing_m=10\n'
            )
            result = visualize_run(config, root / "reviewed")
            self.assertEqual(result["grid_cells"], 100)
            with xr.open_zarr(
                root / "reviewed/scene-01/grid.zarr", chunks=None, mask_and_scale=False
            ) as grid:
                self.assertTrue(grid.valid_support.values.any())
                parent = grid.attrs["source_profiles"][0]["input_store"]
                self.assertEqual(parent["sha256"], tree_hash(Path(parent["path"])))
                self.assertEqual(grid.attrs["geometry_evidence"], "synthetic control")
            config.write_text(config.read_text() + "[render]\nmax_input_samples=1\n")
            with patch("soil_sensing.export.scene.load_profile") as loader:
                with self.assertRaisesRegex(ValueError, "max_input_samples"):
                    visualize_run(config, root / "over-budget")
                loader.assert_not_called()

    def test_synthetic_offline_run_hashes_roundtrip_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "scene.toml"
            config.write_text(
                'schema_version="0.1"\nsynthetic=true\ngates=[[30,40],[40,50]]\n'
                "[grid]\nspacing_m=10\n[render]\ntrace_step=8\nsample_step=8\n"
            )
            output = root / "run"
            result = visualize_run(config, output)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["scene_count"], 4)
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('src="assets/plotly.min.js"', html)
            self.assertNotIn('src="https://', html)
            self.assertIn("SYNTHETIC", (output / "assets/viewer.js").read_text())
            self.assertEqual(
                tree_hash(output / "scene-01/grid.zarr"), result["artifacts"]["scene-01/grid.zarr"]
            )
            scene = json.loads((output / "scene.json").read_text())
            self.assertEqual(len(scene["scenes"]), 4)
            self.assertIn("support_file", scene["scenes"][0])
            report = json.loads((output / "evaluation.json").read_text())
            self.assertEqual(len(report["plots"][0]["folds"]), 18)
            with self.assertRaisesRegex(ValueError, "new path"):
                visualize_run(config, output)

    def test_processed_collection_diagnostic_preserves_inputs_and_escapes_html_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stores, before = [], {}
            for index in range(5):
                profile = synthetic_plots()[0][0].copy(deep=True)
                profile.attrs.update(
                    plot_id=None,
                    survey_id=None,
                    assignment=None,
                    profile_id=("</script><script>alert(1)" if index == 0 else f"line-{index}"),
                )
                store = root / f"quantitative-{index}.zarr"
                save_profile(profile, store)
                stores.append(store)
                before[store] = tree_hash(store)
            config = root / "scene.toml"
            profile_list = ",".join(f'"{store.name}"' for store in stores)
            config.write_text(
                f'schema_version="0.1"\ndiagnostic=true\nprofiles=[{profile_list}]\n'
                "gates=[[30,40]]\n"
            )
            result = visualize_run(config, root / "diagnostic")
            self.assertEqual(before, {store: tree_hash(store) for store in stores})
            self.assertEqual(result["scene_count"], 5)
            self.assertEqual(result["grid_cells"], 0)
            html = (root / "diagnostic/index.html").read_text()
            self.assertNotIn("</script><script>alert(1)", html)
            self.assertIn("\\u003c/script", html)

    def test_invalid_config_and_budget_failures_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "scene.toml"
            base = 'schema_version="0.1"\nsynthetic=true\ngates=[[30,40]]\n'
            for extra in ("typo=1\n", 'profiles=["field.zarr"]\n', '[grid]\nmethod="kriging"\n'):
                config.write_text(base + extra)
                with self.assertRaises(ValueError):
                    load_scene_config(config)
            config.write_text(base + "[render]\nmax_curtain_vertices=2\n")
            output = root / "failed"
            with self.assertRaisesRegex(ValueError, "max_curtain_vertices"):
                visualize_run(config, output)
            self.assertEqual(json.loads((output / "run.json").read_text())["status"], "failed")
            config.write_text(base)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "visualize",
                            "--config",
                            str(config),
                            "--output",
                            str(root / "GPR_raw_data/run"),
                        ]
                    ),
                    2,
                )


if __name__ == "__main__":
    unittest.main()
