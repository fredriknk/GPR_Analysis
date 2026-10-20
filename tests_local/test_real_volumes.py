"""Explicitly enabled M7 checks on completed local grid-01/grid-02 runs.

Set SOIL_SENSING_REAL_VOLUME_RUN and SOIL_SENSING_REAL_VOLUME_INPUTS. These
checks only read existing artifacts and originals; no private data enters CI.
"""

import json
import os
import unittest
from pathlib import Path

VOLUME_RUN = os.environ.get("SOIL_SENSING_REAL_VOLUME_RUN")
INPUT_RUN = os.environ.get("SOIL_SENSING_REAL_VOLUME_INPUTS")


@unittest.skipUnless(VOLUME_RUN and INPUT_RUN, "Opt in with both real-volume run paths")
class RealVolumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from soil_sensing.export.report import verify_run
        from soil_sensing.runs import input_hash

        cls.root, cls.inputs = Path(VOLUME_RUN).resolve(), Path(INPUT_RUN).resolve()
        cls.manifest = verify_run(cls.root)
        cls.input_manifest = verify_run(cls.inputs)
        cls.input_hash = staticmethod(input_hash)
        cls.catalog = json.loads((cls.root / "products.json").read_text())
        cls.summary = json.loads((cls.inputs / "collection_summary.json").read_text())
        cls.originals = {
            p: digest
            for p, digest in cls.input_manifest["inputs"].items()
            if Path(p).suffix.lower() in (".dt1", ".hd", ".gps")
        }
        cls.addClassCleanup(cls.check_originals)

    @classmethod
    def check_originals(cls):
        if len(cls.originals) != 241:
            raise AssertionError("Expected the audited collection's 241 original files")
        for path, digest in cls.originals.items():
            if cls.input_hash(path) != digest:
                raise AssertionError(f"Original changed: {path}")

    def test_exact_parent_memberships_and_full_native_roundtrips(self):
        import xarray as xr

        from soil_sensing.io.storage import load_profile

        parents = {
            p["partition_id"]: set(map(tuple, p["lines"]))
            for p in self.summary["diagnostic_partitions"]
            if p["partition_id"] in ("grid-01", "grid-02")
        }
        actual = {p: set() for p in parents}
        stores = {p["profile_id"]: p["store"] for p in self.summary["profiles"]}
        traces = samples = 0
        combined = all(p["partition_id"] in parents for p in self.catalog["partitions"])
        self.assertEqual(len(self.catalog["partitions"]), 2 if combined else 4)
        for product in self.catalog["partitions"]:
            root = self.root / product["path"]
            selection = json.loads((root / "selection.json").read_text())
            parent = product["partition_id"][:7]
            lines = set(map(tuple, selection["lines"]))
            self.assertFalse(actual[parent] & lines)
            actual[parent].update(lines)
            for row in json.loads((root / "native.json").read_text())["profiles"]:
                p = load_profile(root / row["store"])
                original = load_profile(self.inputs / stores[row["profile_id"]])
                xr.testing.assert_identical(p, original)
                self.assertTrue(p.adc_limit_mask.values.any(axis=1).all())
                traces += p.sizes["trace"]
                samples += p.amplitude.size
        self.assertEqual(actual, parents)
        self.assertEqual((traces, samples), (8542, 2135500))

    def test_quality_support_contracts_and_capability_gates(self):
        import numpy as np

        from soil_sensing.io.storage import load_profile
        from soil_sensing.volumes.workflow import load_partition

        for product in self.catalog["partitions"]:
            with self.subTest(partition=product["partition_id"]):
                _, cube, support, _ = load_partition(self.root, product["partition_id"])
                np.testing.assert_array_equal(cube.native_sample, np.arange(250))
                np.testing.assert_allclose(cube.vertical, np.arange(250) * 0.4, atol=1e-5)
                self.assertEqual(cube.attrs["vertical_axis"], "recording_time")
                self.assertIsNone(cube.attrs["crs"])
                self.assertIsNone(cube.attrs["plot_id"])
                self.assertIsNone(cube.attrs["calibration"])
                groups = {r["direction_group"] for r in cube.attrs["acquisition_audit"]}
                combined = product["partition_id"] in ("grid-01", "grid-02")
                self.assertEqual(len(groups), 2 if combined else 1)
                self.assertNotIn("unknown", groups)
                self.assertTrue(cube.signed_amplitude_valid.values.any())
                self.assertFalse(cube.envelope_valid.values.any())
                window_rms = cube.attrs["attribute_options"].get("rms_clipping_policy") == "window"
                self.assertEqual(bool(cube.rms_valid.values.any()), window_rms)
                self.assertTrue(np.isnan(cube.envelope.values).all())
                if not window_rms:
                    self.assertTrue(np.isnan(cube.rms.values).all())
                mask = cube.signed_amplitude_valid.values
                self.assertTrue((support.signed_amplitude_line_count.values[mask] >= 2).all())
                for name in ("geographic_export", "depth_and_thickness", "absolute_elevation"):
                    self.assertFalse(product["capabilities"][name]["allowed"])
                # Recompute sampled supported voxels from the full native ADC
                # values, using exported native/bin/interpolation weights only.
                root = self.root / product["path"]
                natives = {}
                for row in json.loads((root / "native.json").read_text())["profiles"]:
                    p = load_profile(root / row["store"])
                    natives[(row["source_id"], row["profile_id"])] = (
                        p,
                        {str(t): i for i, t in enumerate(p.trace_id.values)},
                    )
                bins = json.loads((root / "contributors.json").read_text())["bins"]
                valid = np.argwhere(mask)
                for v, y, x in valid[np.linspace(0, len(valid) - 1, 12, dtype=int)]:
                    expected = 0.0
                    for b, weight in zip(
                        support.signed_amplitude_bin.values[v, y, x],
                        support.signed_amplitude_weight.values[v, y, x],
                        strict=True,
                    ):
                        if not weight:
                            continue
                        value = 0.0
                        for member in bins[b]["members"]:
                            p, traces = natives[(member["source_id"], member["profile_id"])]
                            i = traces[member["trace_id"]]
                            self.assertFalse(p.adc_limit_mask.values[i, v])
                            value += member["weight"] * float(p.amplitude.values[i, v])
                        expected += weight * value
                    self.assertAlmostEqual(
                        float(cube.signed_amplitude.values[v, y, x]), expected, places=6
                    )
                if window_rms:
                    valid = np.argwhere(cube.rms_valid.values)
                    half = cube.attrs["attribute_options"]["rms_samples"] // 2
                    for v, y, x in valid[np.linspace(0, len(valid) - 1, 12, dtype=int)]:
                        expected = 0.0
                        for b, weight in zip(
                            support.rms_bin.values[v, y, x],
                            support.rms_weight.values[v, y, x],
                            strict=True,
                        ):
                            if not weight:
                                continue
                            value = 0.0
                            for member in bins[b]["members"]:
                                p, traces = natives[(member["source_id"], member["profile_id"])]
                                i = traces[member["trace_id"]]
                                self.assertGreaterEqual(v, half)
                                self.assertLess(v + half, p.sizes["sample"])
                                self.assertFalse(
                                    p.adc_limit_mask.values[i, v - half : v + half + 1].any()
                                )
                                window = p.amplitude.values[i, v - half : v + half + 1].astype(
                                    float
                                )
                                value += member["weight"] * np.sqrt(np.mean(window**2))
                            expected += weight * value
                        self.assertAlmostEqual(float(cube.rms.values[v, y, x]), expected, places=6)

    def test_whole_line_holdouts_are_reported_with_coverage(self):
        for product in self.catalog["partitions"]:
            root = self.root / product["path"]
            report = json.loads((root / "evaluation.json").read_text())
            self.assertEqual(
                report["attribute"],
                "rms" if product["partition_id"] in ("grid-01", "grid-02") else "signed_amplitude",
            )
            self.assertEqual(report["fold_unit"], "whole_line")
            self.assertFalse(report["field_validation"])
            self.assertTrue(report["folds"])
            self.assertTrue(any(f["supported_count"] for f in report["folds"]))
            for fold in report["folds"]:
                self.assertEqual(fold["buffer_m"], 0.25)
                self.assertGreaterEqual(fold["supported_fraction"], 0)
                self.assertLessEqual(fold["supported_fraction"], 1)
                if not fold["supported_count"]:
                    self.assertIsNone(fold["metrics"]["candidate"]["mae"])

    def test_z_all_original_and_quantitative_inputs_unchanged(self):
        self.check_originals()
        for path, digest in self.manifest["inputs"].items():
            self.assertEqual(self.input_hash(path), digest, path)


if __name__ == "__main__":
    unittest.main()
