"""Synthetic calibration, spatial leakage and fraction/uncertainty regressions."""

import csv
import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

HAS_INFERENCE = all(importlib.util.find_spec(m) for m in ("numpy", "scipy", "sklearn", "pyproj"))
if HAS_INFERENCE:
    import numpy as np

    from soil_sensing.fusion.matching import match_texture_to_gpr
    from soil_sensing.fusion.observations import read_manual_texture_csv
    from soil_sensing.inference.dataset import prepare_dataset, purged_training, spatial_groups
    from soil_sensing.inference.model import (
        ModelOptions,
        evaluate_model,
        fit_calibrated_model,
        fit_estimator,
        predict_estimator,
        project_composition,
    )
    from soil_sensing.inference.synthetic import write_synthetic_training_inputs
    from soil_sensing.inference.workflow import predict_run, prediction_table, train_run


@unittest.skipUnless(HAS_INFERENCE, "Requires fusion, processing and field extras")
class InferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.texture, cls.features, cls.config = write_synthetic_training_inputs(
            Path(cls.temp.name) / "inputs"
        )
        cls.obs = read_manual_texture_csv(cls.texture)
        with cls.features.open(newline="", encoding="utf-8") as stream:
            cls.feature_rows = list(csv.DictReader(stream))
        cls.rows, cls.audit = match_texture_to_gpr(
            cls.obs, cls.feature_rows, radius_m=1, max_lag_hours=1
        )
        cls.dataset, cls.exclusions = prepare_dataset(
            cls.rows, ["gpr_rms_20_40_ns"], block_size_m=5, location_tolerance_m=0.01
        )
        cls.options = ModelOptions(5, 1, 0.01)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_simplex_projection_preserves_closure_and_zero_components(self):
        x = np.array([[2.0, -1.0, 0.0], [0.1, 0.2, 0.7], [0.0, 0.0, 0.0]])
        predicted = project_composition(x)
        np.testing.assert_allclose(predicted.sum(axis=1), 1)
        self.assertTrue((predicted >= 0).all())
        np.testing.assert_allclose(predicted[0], [1, 0, 0])
        np.testing.assert_array_equal(x[0], [2, -1, 0])

    def test_shared_contributors_blocks_and_repeat_locations_are_inseparable(self):
        rows = [dict(self.rows[i]) for i in range(4)]
        rows[1]["x_m"] = rows[0]["x_m"] + 0.01
        contributors = [
            {("root", "line", "1")},
            {("root", "line", "2")},
            {("root", "line", "2")},
            {("other-root", "line", "1")},
        ]
        groups = spatial_groups(rows, contributors, block_size_m=1, location_tolerance_m=0.05)
        self.assertEqual(groups[0], groups[1])
        self.assertEqual(groups[1], groups[2])
        self.assertNotEqual(groups[0], groups[3])
        train = purged_training(self.dataset, np.arange(len(self.rows)), [0], buffer_m=11)
        self.assertNotIn(0, train)
        self.assertNotIn(1, train)

    def test_fold_fit_imputation_scaling_and_calibration_never_see_held_out_plot(self):
        ids = np.array([i for i, r in enumerate(self.rows) if r["plot_id"] != "synthetic_01"])
        model = fit_calibrated_model(self.dataset, ids, self.options)
        proper_keys = {tuple(k) for k in model["proper_training_ids"]}
        proper = [
            i for i, r in enumerate(self.rows) if (r["survey_id"], r["sample_id"]) in proper_keys
        ]
        self.assertFalse(set(model["proper_training_groups"]) & set(model["calibration_groups"]))
        self.assertFalse(any(k[0] == "synthetic_survey_01" for k in proper_keys))
        np.testing.assert_allclose(model["scaler_mean"], np.mean(self.dataset.x[proper], axis=0))
        np.testing.assert_allclose(
            model["imputer_medians"], np.median(self.dataset.x[proper], axis=0)
        )
        before = self.dataset.y[:40].copy()
        try:
            self.dataset.y[:40] = [1, 0, 0]
            second = fit_calibrated_model(self.dataset, ids, self.options)
            self.assertEqual(model, second)
        finally:
            self.dataset.y[:40] = before

    def test_four_plot_transfer_beats_mean_and_reports_independent_counts_and_coverage(self):
        folds, predictions = evaluate_model(self.dataset, self.options)
        self.assertEqual(len(folds), 4)
        self.assertEqual(len(predictions), 160)
        for fold in folds:
            self.assertEqual(fold["status"], "evaluated")
            self.assertGreater(fold["supported_count"], 0)
            self.assertEqual(fold["held_out_groups"], 40)
            self.assertFalse(set(fold["held_out_group_ids"]) & set(fold["proper_training_groups"]))
            scores = fold["metrics_percentage_points"]
            self.assertLess(
                scores["ridge_simplex"]["sand_fraction"]["mae"],
                scores["training_group_mean"]["sand_fraction"]["mae"],
            )
        for row in predictions:
            if row["valid_support"]:
                self.assertAlmostEqual(
                    sum(row[t] for t in ("sand_fraction", "silt_fraction", "clay_fraction")), 1
                )

    def test_unknown_feature_provenance_units_and_sparse_groups_fail(self):
        altered = [dict(r) for r in self.rows]
        altered[0]["lineage"] = '{"gpr_rms_20_40_ns":[]}'
        with self.assertRaisesRegex(ValueError, "lineage"):
            prepare_dataset(
                altered, ["gpr_rms_20_40_ns"], block_size_m=5, location_tolerance_m=0.01
            )
        altered = [dict(r) for r in self.rows]
        altered[0]["gpr_rms_20_40_ns_unit"] = "V"
        with self.assertRaisesRegex(ValueError, "Incompatible"):
            prepare_dataset(
                altered, ["gpr_rms_20_40_ns"], block_size_m=5, location_tolerance_m=0.01
            )
        with self.assertRaisesRegex(ValueError, "Too few"):
            fit_calibrated_model(self.dataset, np.arange(3), self.options)

    def test_model_json_roundtrip_domain_masks_missing_features_and_insufficient_intervals(self):
        model = fit_estimator(self.dataset, np.arange(160), 0.1)
        restored = json.loads(json.dumps(model))
        query = [[15], [1000], [np.nan]]
        p, valid, distance, missing = predict_estimator(restored, query)
        np.testing.assert_array_equal(valid, [True, False, False])
        self.assertTrue(np.isnan(p[1:]).all())
        self.assertGreater(distance[1], 0)
        self.assertTrue(missing[2])
        calibrated = fit_calibrated_model(
            self.dataset, np.arange(160), replace(self.options, interval_level=0.99999)
        )
        self.assertIsNone(calibrated["interval"]["radius_fraction"])
        self.assertEqual(calibrated["interval"]["status"], "insufficient_calibration_groups")

    def test_training_prediction_runs_preserve_sources_and_mask_extrapolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = train_run(self.texture, self.features, self.config, root / "model")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["evaluated_plots"], 4)
            model_path = root / "model/model.json"
            model = json.loads(model_path.read_text())
            row = {
                "support_id": "cell_1",
                "plot_id": "synthetic_01",
                "survey_id": "synthetic_survey_01",
                "x_m": 500020,
                "y_m": 6600000,
                "crs": "EPSG:32632",
                "acquired_at_utc": "2026-01-01T12:00:00Z",
                "depth_top_m": 0.1,
                "depth_bottom_m": 0.3,
                "gpr_rms_20_40_ns": 15,
                "gpr_rms_20_40_ns_unit": "ADC counts",
                "gpr_rms_20_40_ns_processing_signature": "synthetic-window-rms-v1",
                "gpr_rms_20_40_ns_contributors": 1,
            }
            outside = {**row, "support_id": "cell_2", "gpr_rms_20_40_ns": 10000}
            table = prediction_table(model, [row, outside])
            self.assertTrue(table[0]["valid_support"])
            self.assertFalse(table[1]["valid_support"])
            self.assertIsNone(table[1]["sand_fraction"])
            self.assertTrue(table[0]["synthetic"])
            csv_path = root / "supports.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerows([row, outside])
            predicted = predict_run(model_path, csv_path, root / "predictions")
            self.assertEqual(predicted["valid_support_count"], 1)
            with self.assertRaisesRegex(ValueError, "new directory"):
                train_run(self.texture, self.features, self.config, root / "model")
