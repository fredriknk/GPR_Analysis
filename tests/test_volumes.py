"""Synthetic cube recovery, support lineage, evidence gates and immutable runs."""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

HAS_VOLUME = all(
    importlib.util.find_spec(m)
    for m in ("numpy", "scipy", "xarray", "zarr", "plotly", "shapely", "pyproj")
)
if HAS_VOLUME:
    import numpy as np
    import xarray as xr

    from soil_sensing.export.interpretation import export_interpretation_run
    from soil_sensing.ingest import tree_hash
    from soil_sensing.interpretation.workflow import interpret_run, volume_visualize_run
    from soil_sensing.processing.attributes import AttributeOptions, native_attributes
    from soil_sensing.spatial.gridding import GridOptions, polygon_domain
    from soil_sensing.visualization.synthetic import synthetic_plots
    from soil_sensing.visualization.volume import conservative_preview_mask
    from soil_sensing.volumes.audit import track_direction
    from soil_sensing.volumes.contracts import (
        ATTRIBUTES,
        Reason,
        capabilities,
        freeze_selection,
        validate_selection,
        validate_volume,
    )
    from soil_sensing.volumes.reconstruction import Reconstruction, VolumeOptions, evaluate_volume
    from soil_sensing.volumes.workflow import (
        _prepare,
        _synthetic,
        load_partition,
        load_volume_config,
        volume_run,
    )


def fixture_rows(points):
    return [
        dict(
            xy=[x, y],
            located=True,
            source_id="fixture",
            profile_id=f"line-{y}",
            trace_id=str(i),
            line_id=f"line-{y}",
            position_status="measured",
            direction_group="east_west",
            quality_flags=0,
            time_utc=None,
        )
        for i, (x, y) in enumerate(points)
    ]


def metadata():
    return dict(
        coordinate_mode="synthetic_local",
        crs=None,
        plot_id=None,
        survey_id="synthetic",
        origin_xy_m=[0.0, 0.0],
        vertical_axis="recording_time",
        vertical_unit="ns",
        selection_hash="fixture",
        partition_id="one",
        calibration=None,
        source_profiles=[],
        attribute_unit="ADC",
        synthetic=True,
        geometry_evidence=None,
    )


@unittest.skipUnless(HAS_VOLUME, "Requires processing, field, visualization extras")
class CubeTests(unittest.TestCase):
    def test_direction_repeated_fixes_reversal_and_ambiguous_tracks(self):
        north = np.repeat([[0, 0], [0, 1], [0, 2], [0, 3]], 8, axis=0)
        for xy in (north, north[::-1]):
            self.assertEqual(track_direction(xy, np.ones(len(xy) - 1, bool)), "north_south")
            self.assertEqual(track_direction(xy[:, ::-1], np.ones(len(xy) - 1, bool)), "east_west")
        for xy in (np.zeros((12, 2)), np.array([[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]])):
            self.assertEqual(track_direction(xy, np.ones(len(xy) - 1, bool)), "unknown")
        # An isolated rejected outlier cannot rotate a valid north/south track.
        xy = np.r_[north, [[100, 0]]]
        self.assertEqual(
            track_direction(xy, np.r_[np.ones(len(north) - 1, bool), False]), "north_south"
        )
        self.assertEqual(track_direction(north, np.zeros(len(north) - 1, bool)), "unknown")

    def setUp(self):
        self.boundary = [[0, 0], [10, 0], [10, 10], [0, 10]]
        self.domain = polygon_domain(self.boundary)
        self.rows = fixture_rows([(0, 0), (10, 0), (0, 5), (10, 5), (0, 10), (10, 10)])
        self.grid = GridOptions(spacing_m=2, bin_m=0.1, max_edge_m=20, max_distance_m=20)
        self.options = VolumeOptions(block_samples=2, max_stencil_patterns=8)

    def reconstruct(
        self,
        function=lambda x, y: 2 * x + 3 * y + 7,
        *,
        rows=None,
        domain=None,
        grid=None,
        corrupt=False,
    ):
        rows = self.rows if rows is None else rows
        values = np.array([[function(*r["xy"])] * 4 for r in rows], dtype=float).reshape(
            len(rows), 4
        )
        attrs = {name: values.copy() for name in ATTRIBUTES}
        if corrupt:
            attrs["envelope"][:, 0] = np.nan
        recon = Reconstruction(
            rows,
            attrs,
            np.arange(4.0),
            np.arange(4),
            self.boundary,
            self.domain if domain is None else domain,
            metadata(),
            grid or self.grid,
            self.options,
        )
        blocks = list(recon.blocks())
        return (
            xr.concat([v[0] for v in blocks], "vertical"),
            xr.concat([v[1] for v in blocks], "vertical"),
            recon,
        )

    def test_constant_linear_recovery_weights_and_native_lineage(self):
        for function in (lambda x, y: 11, lambda x, y: 2 * x + 3 * y + 7):
            cube, support, recon = self.reconstruct(function)
            xx, yy = np.meshgrid(cube.x, cube.y)
            expected = np.broadcast_to(function(xx, yy), cube.envelope.shape)
            np.testing.assert_allclose(cube.envelope.values, expected)
            validate_volume(cube, support)
            ids = support.envelope_bin.values[0, 1, 1]
            weights = support.envelope_weight.values[0, 1, 1]
            means = [
                np.mean(
                    [
                        function(*self.rows[int(m["trace_id"])]["xy"])
                        for m in recon.table[i]["members"]
                    ]
                )
                for i in ids
            ]
            self.assertAlmostEqual(
                float(np.dot(weights, means)), float(cube.envelope.values[0, 1, 1])
            )
            self.assertTrue(all(b["members"][0]["source_id"] == "fixture" for b in recon.table))
            np.testing.assert_array_equal(cube.native_sample, [0, 1, 2, 3])

    def test_per_variable_quality_and_edge_reason_masks(self):
        cube, support, _ = self.reconstruct(corrupt=True)
        self.assertTrue(np.isnan(cube.envelope.values[0]).all())
        self.assertTrue(np.isfinite(cube.signed_amplitude.values[0]).all())
        self.assertTrue((cube.envelope_reason.values[0] == int(Reason.INPUT_QUALITY)).all())
        self.assertTrue((support.envelope_bin.values[0] == -1).all())
        cube.envelope_reason.values[1, 0, 0] = int(Reason.INPUT_QUALITY)
        with self.assertRaises(ValueError):
            validate_volume(cube, support)

    def test_holes_disconnected_domains_sparse_and_one_bent_line(self):
        hole = [[4, 2], [6, 2], [6, 8], [4, 8]]
        cube, _, _ = self.reconstruct(domain=polygon_domain(self.boundary, exclusions=[hole]))
        self.assertTrue(np.isnan(cube.envelope.values).all())
        regions = [[[0, 0], [3, 0], [3, 10], [0, 10]], [[7, 0], [10, 0], [10, 10], [7, 10]]]
        cube, _, _ = self.reconstruct(domain=polygon_domain(self.boundary, regions=regions))
        self.assertTrue(np.isnan(cube.envelope.values).all())
        for rows in ([], self.rows[:2], [{**r, "line_id": "bent"} for r in self.rows]):
            cube, _, _ = self.reconstruct(rows=rows)
            self.assertFalse(cube.envelope_valid.values.any())

    def test_nearest_uses_same_geometric_mask_and_duplicate_bins_remain_traceable(self):
        linear, _, _ = self.reconstruct()
        nearest, support, recon = self.reconstruct(
            grid=replace(self.grid, method="nearest"),
            rows=[*self.rows, {**self.rows[0], "trace_id": "6"}],
        )
        np.testing.assert_array_equal(linear.envelope_valid, nearest.envelope_valid)
        validate_volume(nearest, support)
        self.assertTrue(any(len(b["members"]) == 2 for b in recon.table))

    def test_whole_line_evaluation_refits_and_reports_coverage(self):
        rows = fixture_rows([(x, y) for y in range(0, 11, 2) for x in (0, 5, 10)])
        attrs = {
            n: np.array([[2 * r["xy"][0] + 3 * r["xy"][1] + 7] * 4 for r in rows])
            for n in ATTRIBUTES
        }
        report = evaluate_volume(
            rows,
            attrs,
            np.arange(4),
            np.arange(4),
            self.boundary,
            self.domain,
            metadata(),
            self.grid,
            self.options,
            sample_step=2,
        )
        inner = [f for f in report["folds"] if f["held_out_line"] == "line-4"]
        self.assertTrue(
            all(
                f["supported_count"] > 0 and f["metrics"]["candidate"]["rmse"] < 1e-9 for f in inner
            )
        )
        outer = [f for f in report["folds"] if f["held_out_line"] == "line-0"]
        self.assertTrue(
            all(
                f["supported_fraction"] == 0 and f["metrics"]["candidate"]["mae"] is None
                for f in outer
            )
        )
        attrs["envelope"][[i for i, r in enumerate(rows) if r["line_id"] == "line-4"]] += 1000
        changed = evaluate_volume(
            rows,
            attrs,
            np.arange(4),
            np.arange(4),
            self.boundary,
            self.domain,
            metadata(),
            self.grid,
            self.options,
            sample_step=2,
        )
        self.assertAlmostEqual(
            next(f for f in changed["folds"] if f["held_out_line"] == "line-4")["metrics"][
                "candidate"
            ]["mae"],
            1000,
        )
        attrs["envelope"] = np.full(attrs["envelope"].shape, np.nan)
        signed = evaluate_volume(
            rows,
            attrs,
            np.arange(4),
            np.arange(4),
            self.boundary,
            self.domain,
            metadata(),
            self.grid,
            self.options,
            sample_step=2,
            attribute="signed_amplitude",
        )
        self.assertEqual(signed["attribute"], "signed_amplitude")
        self.assertTrue(signed["folds"])
        self.assertTrue(any(f["supported_count"] for f in signed["folds"]))
        self.assertTrue(
            all(
                f["metrics"]["candidate"]["mae"] < 1e-9
                for f in signed["folds"]
                if f["supported_count"]
            )
        )

    def test_budgets_checked_before_dense_mesh_or_stencil_growth(self):
        attrs = {n: np.ones((len(self.rows), 4)) for n in ATTRIBUTES}
        with patch(
            "soil_sensing.volumes.reconstruction.np.meshgrid",
            side_effect=AssertionError("allocated"),
        ):
            with self.assertRaisesRegex(ValueError, "max_memory_mb"):
                Reconstruction(
                    self.rows,
                    attrs,
                    np.arange(4),
                    np.arange(4),
                    self.boundary,
                    self.domain,
                    metadata(),
                    self.grid,
                    replace(self.options, max_memory_mb=0.0001),
                )

    def test_preview_stride_cannot_erase_an_unknown_native_voxel(self):
        mask = np.ones((7, 7, 7), bool)
        mask[3, 3, 3] = False
        preview = conservative_preview_mask(mask, 2)
        self.assertFalse(preview[1:3, 1:3, 1:3].any())
        np.testing.assert_array_equal(conservative_preview_mask(mask, 1), mask)


@unittest.skipUnless(HAS_VOLUME, "Requires processing, field, visualization extras")
class AttributeAndSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles, cls.plots, cls.exclusions = synthetic_plots()

    def test_hilbert_axis_amplitude_edges_and_conservative_clipping(self):
        p = self.profiles[0].copy(deep=True)
        t = np.arange(p.sizes["sample"])
        p.amplitude.values[:] = 123 * np.cos(2 * np.pi * t / 10)
        attrs = native_attributes(
            p, AttributeOptions(edge_samples=8, padding_samples=0, minimum_segment_samples=32)
        )
        np.testing.assert_allclose(attrs["envelope"][:, 8:-8], 123, atol=2e-5)
        self.assertTrue(np.isnan(attrs["envelope"][:, :8]).all())
        p.adc_limit_mask.values[0, 20] = True
        attrs = native_attributes(p)
        self.assertTrue(np.isnan(attrs["envelope"][0]).all())
        self.assertTrue(np.isfinite(attrs["signed_amplitude"][0, 21]))
        self.assertTrue(np.isnan(attrs["signed_amplitude"][0, 20]))
        p.attrs["processing_branch"] = "display"
        with self.assertRaises(ValueError):
            native_attributes(p)

    def test_selections_hash_exact_namespaces_policies_and_geometry(self):
        selected = freeze_selection(
            "one",
            self.profiles[:2],
            {},
            position_policy="accepted",
            direction_policy="separate",
            geometry={},
        )
        validate_selection(selected)
        altered = {**selected, "position_policy": "measured"}
        with self.assertRaises(ValueError):
            validate_selection(altered)
        with self.assertRaises(ValueError):
            freeze_selection(
                "one",
                [self.profiles[0], self.profiles[0]],
                {},
                position_policy="accepted",
                direction_policy="separate",
                geometry={},
            )

    def test_local_contract_never_unlocks_crs_or_depth(self):
        caps = capabilities(metadata())
        self.assertTrue(caps["local_time_volume"]["allowed"])
        self.assertFalse(caps["geographic_export"]["allowed"])
        self.assertFalse(caps["depth_and_thickness"]["allowed"])

    def test_raw_gps_contract_keeps_datum_plot_and_precision_missing(self):
        pair = [p.copy(deep=True) for p in self.profiles[:2]]
        for p in pair:
            x, y = p.x_m.values.copy(), p.y_m.values.copy()
            p["latitude_deg"] = ("trace", 60.0 + (y - 6600000) / 111320.0)
            p["longitude_deg"] = ("trace", 10.0 + (x - 500000) / (111320.0 * 0.5))
            p.x_m.values[:] = np.nan
            p.y_m.values[:] = np.nan
            p.attrs.update(source_crs=None, target_crs=None, survey_id=None, plot_id=None)
        data = dict(
            coordinate_mode="raw_gps_relative",
            vertical_axis="recording_time",
            track_gap_m=6.0,
            track_buffer_m=7.0,
            position_policy="accepted",
        )
        rows, arrays, axis, native, boundary, domain, attrs = _prepare(
            pair, {"partition_id": "one"}, {}, data, GridOptions(), AttributeOptions(), {}
        )
        attrs["selection_hash"] = "fixture"
        recon = Reconstruction(
            rows,
            arrays,
            axis,
            native,
            boundary,
            domain,
            attrs,
            GridOptions(),
            VolumeOptions(max_stencil_patterns=8),
        )
        cube, support = next(recon.blocks())
        validate_volume(cube, support)
        self.assertIsNone(cube.attrs["crs"])
        self.assertIsNone(cube.attrs["plot_id"])
        self.assertFalse(capabilities(cube.attrs)["geographic_export"]["allowed"])
        cube.attrs["crs"] = "EPSG:32632"
        with self.assertRaises(ValueError):
            validate_volume(cube, support)

    def test_incompatible_axes_units_sessions_and_orthogonal_policy_fail(self):
        data = dict(
            coordinate_mode="projected",
            vertical_axis="recording_time",
            track_gap_m=6.0,
            track_buffer_m=7.0,
            position_policy="accepted",
        )
        for mutate in (
            lambda p: p.attrs.update(amplitude_units="volts"),
            lambda p: p.attrs.update(survey_id="other"),
            lambda p: p.attrs.update(time_axis_convention="other"),
        ):
            pair = [p.copy(deep=True) for p in self.profiles[:2]]
            mutate(pair[1])
            with self.assertRaises(ValueError):
                _prepare(
                    pair,
                    {"partition_id": "one"},
                    self.plots,
                    data,
                    GridOptions(),
                    AttributeOptions(),
                    {},
                )
        pair = [p.copy(deep=True) for p in self.profiles[:2]]
        p = pair[1]
        p.x_m.values[:] = p.x_m.values[0]
        p.y_m.values[:] = p.y_m.values[0] + p.distance_m.values
        with self.assertRaisesRegex(ValueError, "direction"):
            _prepare(
                pair,
                {"partition_id": "one"},
                self.plots,
                data,
                GridOptions(),
                AttributeOptions(),
                {},
            )


@unittest.skipUnless(HAS_VOLUME, "Requires scientific extras")
class VolumeWorkflowTests(unittest.TestCase):
    def test_two_partition_roundtrip_layers_viewer_and_geographic_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "volume.toml"
            config.write_text(
                'schema_version="1.0"\nsynthetic=true\n[grid]\nspacing_m=10.0\nbin_m=1.0\nmax_edge_m=20.0\nmax_distance_m=20.0\n[volume]\nmax_stencil_patterns=8\n',
                encoding="utf-8",
            )
            native, plots, partitions, exclusions = _synthetic(False)
            subset = [
                p.isel(trace=slice(0, 11), sample=slice(0, 80))
                for p in native
                if p.attrs["profile_id"].endswith(("line_00", "line_01", "line_02"))
            ]
            for partition in partitions:
                partition["lines"] = [
                    [p.attrs["source_id"], p.attrs["profile_id"]]
                    for p in subset
                    if p.attrs["plot_id"]
                    == (
                        "synthetic_01" if partition["partition_id"] == "grid-01" else "synthetic_02"
                    )
                ]
            with patch(
                "soil_sensing.volumes.workflow._synthetic",
                return_value=(subset, plots, partitions, exclusions),
            ):
                volume_run(config, root / "volume")
            native_hash = tree_hash(root / "volume")
            for partition in ("grid-01", "grid-02"):
                _, cube, support, selection = load_partition(root / "volume", partition)
                validate_volume(cube, support)
                self.assertEqual(len(selection["lines"]), 3)
                self.assertTrue(cube.envelope_valid.values.any())
            interpret = root / "layers.toml"
            interpret.write_text(
                'schema_version="1.0"\nkind="layers"\nsynthetic=true\npartition_id="grid-01"\n[[layers]]\nlayer_id="upper"\ntop="H1"\nbottom="H2"\n',
                encoding="utf-8",
            )
            interpret_run(interpret, root / "volume", root / "layers")
            with xr.open_zarr(
                root / "layers/surfaces.zarr", chunks=None, mask_and_scale=False
            ) as surfaces:
                self.assertNotIn("layer_thickness_m", surfaces)
                self.assertGreater(np.isfinite(surfaces.layer_interval_twt_ns.values).sum(), 0)
            if importlib.util.find_spec("rasterio") and importlib.util.find_spec("geopandas"):
                export_interpretation_run(root / "layers", root / "gis")
                self.assertTrue((root / "gis/layer_upper_interval_ns.tif").exists())
                self.assertFalse(list((root / "gis").glob("*thickness*")))
            view = root / "view.toml"
            view.write_text(
                'schema_version="1.0"\nvolume_run="volume"\ninterpretation_runs=["layers"]\n'
                '[preview]\npartition_ids=["grid-01"]\n',
                encoding="utf-8",
            )
            volume_visualize_run(view, root / "viewer")
            self.assertTrue((root / "viewer/downloads/grid-01.zip").is_file())
            self.assertFalse((root / "viewer/downloads/grid-02.zip").exists())
            self.assertEqual(
                json.loads((root / "viewer/run.json").read_text())["partition_count"], 1
            )
            self.assertIn("assets/volume.js", (root / "viewer/index.html").read_text())
            self.assertEqual(native_hash, tree_hash(root / "volume"))
            view.write_text(
                'schema_version="1.0"\nvolume_run="volume"\n[preview]\npartition_ids=["absent"]\n'
            )
            with self.assertRaisesRegex(ValueError, "unique existing complete partitions"):
                volume_visualize_run(view, root / "unknown-view")
            with self.assertRaises(ValueError):
                volume_run(config, root / "volume")

    def test_bad_configs_and_failed_run_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "bad.toml"
            for text in (
                'schema_version="1.0"\nprofiles=[]\n',
                'schema_version="1.0"\nsynthetic=true\nunknown=1\n',
                'schema_version="1.0"\nsynthetic=true\nprofiles=["raw"]\n',
            ):
                config.write_text(text)
                with self.assertRaises(ValueError):
                    load_volume_config(config)
            config.write_text(
                'schema_version="1.0"\nsynthetic=true\n[volume]\nmax_memory_mb=0.001\n'
            )
            with self.assertRaisesRegex(ValueError, "max_memory"):
                volume_run(config, root / "failed")
            self.assertEqual(json.loads((root / "failed/run.json").read_text())["status"], "failed")
