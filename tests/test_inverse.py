"""Synthetic inverse-adapter checks; CUDA/gprMax/private data are not required."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from soil_sensing.export.report import verify_run
from soil_sensing.ingest import write_json
from soil_sensing.inversion.fitting import fit_experiment, template_search
from soil_sensing.inversion.gprmax import layered_input, read_ez
from soil_sensing.inversion.observations import separated_training_mask
from soil_sensing.runs import begin_run, finish_run


class InverseTests(unittest.TestCase):
    def test_spatial_buffer_removes_shared_support(self):
        mask = separated_training_mask([[0, 0], [0.1, 0], [1, 0]], [[0, 0]], 0.25)
        np.testing.assert_array_equal(mask, [False, False, True])
        with self.assertRaises(ValueError):
            separated_training_mask([[0, 0]], [], 0.25)
        with self.assertRaises(ValueError):
            separated_training_mask([[0, 0]], [[1, 1]], -1)

    def test_physical_input_is_explicit_and_background_separate(self):
        text = layered_input(9.0, 3.0)
        self.assertIn("#material: 9.0 0.005 1 0 upper", text)
        self.assertIn("4 3.0 0.01 lower", text)
        self.assertIn("#rx: 2.25", text)
        self.assertNotIn("0.01 lower", layered_input(9.0))
        self.assertIn("not field calibration", text)
        with self.assertRaises(ValueError):
            layered_input(0, 2)
        with self.assertRaises(ValueError):
            layered_input(9, 7)

    def test_receiver_uses_dataset_timing_and_requested_component(self):
        # Synthetic HDF5 protocol fixture keeps h5py optional for ordinary CI.
        receiver, stored, h5py = MagicMock(), MagicMock(), MagicMock()
        receiver.attrs = {"SampleInterval": 0.2e-9, "TimeSampleOffset": 0.1e-9}
        receiver.__getitem__.return_value = np.array([1, 2, 3])
        stored.attrs = {"gprMax": "4.0.0"}
        stored.__getitem__.return_value = receiver
        h5py.File.return_value.__enter__.return_value = stored
        with patch.dict("sys.modules", {"h5py": h5py}):
            time, values = read_ez("synthetic.h5", component="Ex")
            stored.__getitem__.assert_called_with("rxs/rx1/Ex")
            np.testing.assert_allclose(time, [0.1, 0.3, 0.5])
            np.testing.assert_array_equal(values, [1, 2, 3])
            for dt in (0, -1, np.nan):
                receiver.attrs["SampleInterval"] = dt
                with self.subTest(dt=dt), self.assertRaises(ValueError):
                    read_ez("synthetic.h5")
            stored.attrs["gprMax"] = "unsupported"
            with self.assertRaises(ValueError):
                read_ez("synthetic.h5")


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.axis = np.arange(0, 100, 0.1)
        self.signals = np.array(
            [
                np.exp(-(((self.axis - 50) / 2) ** 2)),
                np.exp(-(((self.axis - 50) / 4) ** 2)),
            ]
        )
        self.time = np.arange(30, 75, 0.4)
        self.exact = -2 * np.interp(self.time - 2, self.axis, self.signals[1])
        self.observed = np.tile(self.exact, (4, 1))
        self.train = np.array([True, True, False, False])
        self.shifts = [-2, 0, 2, 4]

    def search(self, **overrides):
        options = dict(
            bank_time=self.axis,
            signals=self.signals,
            time=self.time,
            observed=self.observed,
            train=self.train,
            shifts_ns=self.shifts,
        )
        options.update(overrides)
        return template_search(**options)

    def test_recovers_signed_gain_model_and_shift(self):
        report, prediction = self.search()
        self.assertEqual(report["best"]["model"], 1)
        self.assertEqual(report["best"]["shift_ns"], 2)
        self.assertAlmostEqual(report["best"]["gain"], -2)
        np.testing.assert_allclose(prediction, self.exact)
        self.assertLess(report["holdout"]["normalized_mse"], 1e-25)
        self.assertEqual(report["zero_holdout"]["normalized_mse"], 1)
        self.assertEqual(len(report["per_line"]), 4)

    def test_holdout_cannot_change_model_gain_shift_or_candidate_losses(self):
        first, prediction = self.search()
        modified = self.observed.copy()
        modified[~self.train] = np.random.default_rng(5).normal(100, 70, (2, len(self.time)))
        second, other = self.search(observed=modified)
        self.assertEqual(first["best"], second["best"])
        self.assertEqual(first["candidates"], second["candidates"])
        self.assertEqual(first["near_equivalent"], second["near_equivalent"])
        np.testing.assert_array_equal(prediction, other)
        self.assertGreater(second["holdout"]["mse"], first["holdout"]["mse"])

    def test_invalid_axes_shapes_and_splits_are_rejected(self):
        cases = [
            dict(time=self.time[::-1]),
            dict(time=self.time * np.nan),
            dict(time=self.time.reshape(1, -1)),
            dict(bank_time=[0]),
            dict(bank_time=self.axis * np.nan),
            dict(bank_time=self.axis[::-1]),
            dict(shifts_ns=[]),
            dict(shifts_ns=[[0, 1]]),
            dict(shifts_ns=[np.inf]),
            dict(train=[0, 1, 0, 1]),
            dict(train=np.ones(4, bool)),
            dict(train=np.zeros(4, bool)),
            dict(signals=self.signals[:, :-1]),
            dict(signals=np.empty((0, len(self.axis)))),
            dict(peak_times=[50]),
            dict(observed=self.observed * np.nan),
            dict(observed=self.observed[:, :-1]),
        ]
        for options in cases:
            with self.subTest(options=list(options)), self.assertRaises(ValueError):
                self.search(**options)

    def test_no_energy_incomplete_support_and_censored_arrivals_are_rejected(self):
        for options in [
            dict(signals=np.zeros_like(self.signals)),
            dict(observed=np.zeros_like(self.observed)),
            dict(shifts_ns=[100]),
            dict(peak_times=[np.nan, np.nan]),
            dict(peak_times=[95, 95]),
        ]:
            with self.subTest(options=list(options)), self.assertRaises(ValueError):
                self.search(**options)
        # A huge pulse outside the ROI cannot be fitted through tiny numerical tails.
        tail = np.ones((1, len(self.axis))) * 1e-5
        tail[0, self.axis > 85] = 1
        with self.assertRaises(ValueError):
            self.search(signals=tail)

    def test_equivalent_candidates_and_zero_holdout_have_explicit_metrics(self):
        duplicates = np.tile(self.signals[1], (2, 1))
        modified = self.observed.copy()
        modified[~self.train] = 0
        report, _ = self.search(signals=duplicates, observed=modified)
        self.assertEqual(report["best"]["model"], 0)
        self.assertEqual({c["model"] for c in report["near_equivalent"]}, {0, 1})
        self.assertIsNone(report["holdout"]["normalized_mse"])

    def test_end_to_end_uses_verified_runs_and_refined_truth_without_solver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank, manifest = begin_run("synthetic_bank", root / "bank", [])
            axis = np.arange(0, 105, 0.1)
            signal = np.exp(-(((axis - 65) / 2) ** 2))
            model = dict(epsilon=9, depth_m=3, model="synthetic-only")
            np.savez_compressed(bank / "bank.npz", time_ns=axis, signals=signal[None, :])
            write_json(bank / "bank.json", dict(models=[model], assumptions={"synthetic": True}))
            finish_run(bank, manifest)
            observations, manifest = begin_run("synthetic_observations", root / "obs", [])
            time = np.arange(50, 85, 0.4)
            waveforms = np.tile(1.7 * np.interp(time - 8.4, axis, signal), (4, 1))
            np.savez_compressed(
                observations / "fixture.npz", time_ns=time, waveforms=waveforms, train=self.train
            )
            partition = dict(
                partition_id="fixture",
                selection_hash="synthetic",
                selection_bias="synthetic ROI",
                lines=[
                    dict(
                        source_id="fixture",
                        profile_id=f"line-{i}",
                        trace_ids=[str(i)],
                        split="train" if value else "holdout",
                    )
                    for i, value in enumerate(self.train)
                ],
            )
            write_json(observations / "observations.json", dict(partitions=[partition]))
            finish_run(observations, manifest)
            validation, manifest = begin_run("synthetic_validation", root / "validation", [bank])
            np.savez_compressed(validation / "refined-truth.npz", time_ns=axis, signal=signal)
            write_json(validation / "validation.json", dict(truth_model=model))
            finish_run(validation, manifest)
            reports = fit_experiment(bank, observations, root / "fit", validation_root=validation)
            self.assertTrue(reports["synthetic_recovery"]["inverse_crime"])
            self.assertFalse(reports["refined_synthetic_recovery"]["inverse_crime"])
            self.assertTrue(reports["refined_synthetic_recovery"]["recovered_bank_model"])
            self.assertIsNone(reports["fixture"]["field_accuracy_improved"])
            self.assertEqual(reports["fixture"]["per_line"][0]["profile_id"], "line-0")
            completed = verify_run(root / "fit")
            self.assertIn("summary.md", completed["artifacts"])
            self.assertIn(str(validation.resolve()), completed["inputs"])
            # A rerun cannot replace results; a corrupted library cannot enter a new fit.
            with self.assertRaises(ValueError):
                fit_experiment(bank, observations, root / "fit")
            (bank / "bank.json").write_text(json.dumps({}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                fit_experiment(bank, observations, root / "corrupt-fit")
            self.assertFalse((root / "corrupt-fit").exists())
