"""Opt-in checks for the supplied local DT1/HD/GPS collection; never required by CI.

Set SOIL_SENSING_REAL_DATA_ROOT explicitly before discovering tests_local.
SOIL_SENSING_REAL_DATA_ALL=1 expands the representative selection to every DT1.
Only temporary derived stores are written. No datum, plot, velocity or target is assumed.
"""

import math
import os
import struct
import tempfile
import unittest
from pathlib import Path

REAL_ROOT = os.environ.get("SOIL_SENSING_REAL_DATA_ROOT")
REPRESENTATIVE = (
    "DATA08line/XLINE00.DT1",
    "DATA08line/Lines08/LINE00.DT1",
    "Data09line/YLINE00.DT1",
    "Data09line/Lines/LINE02.DT1",  # Documented missing GPS positions.
    "Data09line/Lines/LINE04.DT1",  # Documented rejected fixes.
)
WINDOWS = [(0, 20), (20, 50), (50, 90)]


@unittest.skipUnless(REAL_ROOT, "Opt in with SOIL_SENSING_REAL_DATA_ROOT; private data is optional")
class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import xarray as xr

        from soil_sensing.inventory import sha256_file
        from soil_sensing.io.dt1 import find_sidecar, read_dt1
        from soil_sensing.processing.gpr import ProcessingOptions, process_profile
        from soil_sensing.spatial.gps import GPSOptions, locate_profile

        cls.np, cls.xr = np, xr
        cls.root = Path(REAL_ROOT).resolve()
        if not cls.root.is_dir():
            raise ValueError("Configured real-data root does not exist")
        cls.sources = sorted(
            p for p in cls.root.rglob("*") if p.suffix.lower() in (".dt1", ".hd", ".gps")
        )
        if not cls.sources or any(
            p.is_symlink() or not p.resolve().is_relative_to(cls.root) for p in cls.sources
        ):
            raise ValueError("Local checks require regular source files inside the explicit root")
        cls.before = {p: sha256_file(p) for p in cls.sources}

        def unchanged():
            if cls.before != {p: sha256_file(p) for p in cls.sources}:
                raise AssertionError("Original DT1/HD/GPS checksums changed during local tests")

        cls.addClassCleanup(unchanged)
        paths = (
            [p for p in cls.sources if p.suffix.lower() == ".dt1"]
            if os.environ.get("SOIL_SENSING_REAL_DATA_ALL") == "1"
            else [cls.root / name for name in REPRESENTATIVE]
        )
        cls.profiles = []
        for path in paths:
            if not path.is_file():
                raise ValueError(f"Representative profile missing: {path.relative_to(cls.root)}")
            raw = read_dt1(path, root=cls.root, source_id="local-validation-unassigned")
            gps = find_sidecar(path, ".gps", required=True)
            located, qc = locate_profile(raw, gps, GPSOptions())
            processed = process_profile(located, ProcessingOptions())
            cls.profiles.append((path, raw, located, processed, qc))
        print(
            f"\nLocal selection: {len(cls.profiles)} profiles / "
            f"{sum(raw.sizes['trace'] for _, raw, *_ in cls.profiles)} traces; "
            f"{len(cls.sources)} original files checksummed."
        )

    def test_binary_record_samples_headers_and_recording_time(self):
        for path, raw, *_ in self.profiles:
            with self.subTest(profile=raw.attrs["profile_id"]):
                count, samples = raw.sizes["trace"], raw.sizes["sample"]
                self.assertEqual(path.stat().st_size, count * (128 + 2 * samples))
                # The canonical float32 array exactly represents every stored int16 value.
                self.assertEqual(raw.amplitude.dtype, self.np.dtype("float32"))
                # Independent struct extraction at the first/interior/last record.
                with path.open("rb") as stream:
                    for index in sorted({0, count // 2, count - 1}):
                        stream.seek(index * (128 + 2 * samples))
                        header = stream.read(128)
                        amplitude = struct.unpack(f"<{samples}h", stream.read(2 * samples))
                        self.assertEqual(float(struct.unpack_from("<f", header)[0]), index + 1)
                        self.assertEqual(raw.trace_header_bytes.values[index].tobytes(), header)
                        self.np.testing.assert_array_equal(raw.amplitude.values[index], amplitude)
                self.assertEqual(samples, 250)  # Observed supplied-collection contract.
                self.np.testing.assert_allclose(raw.twt_ns.values, self.np.arange(250) * 0.4)
                self.assertEqual(raw.attrs["processing_branch"], "raw")
                self.assertNotIn("corrected_twt_ns", raw)

    def test_gps_preserves_trace_identity_and_missing_metadata(self):
        for _, raw, located, _, qc in self.profiles:
            with self.subTest(profile=raw.attrs["profile_id"]):
                self.np.testing.assert_array_equal(raw.amplitude, located.amplitude)
                self.np.testing.assert_array_equal(raw.trace_id, located.trace_id)
                self.assertEqual(
                    sum(qc[k] for k in ("measured", "interpolated", "missing", "rejected")),
                    raw.sizes["trace"],
                )
                self.assertEqual(qc["projected"], 0)
                self.assertIsNone(located.attrs["plot_id"])
                self.assertIsNone(located.attrs["source_crs"])
                self.assertIsNone(located.attrs["target_crs"])
                self.assertTrue(self.np.isnan(located.x_m.values).all())
                self.assertTrue(self.np.isnan(located.horizontal_uncertainty_m.values).all())
                profile_id = raw.attrs["profile_id"].casefold()
                if profile_id == "data09line/lines/line02":
                    self.assertEqual(qc["missing"], 540)
                if profile_id == "data09line/lines/line04":
                    self.assertEqual(qc["rejected"], 34)
                print(
                    f"GPS {raw.attrs['profile_id']}: "
                    + ", ".join(
                        f"{k}={qc[k]}" for k in ("measured", "interpolated", "missing", "rejected")
                    )
                )

    def test_quantitative_rms_excludes_clipping_and_keeps_uncalibrated_depth_missing(self):
        from soil_sensing.processing.gpr import window_rms_features

        for _, raw, _, processed, _ in self.profiles:
            with self.subTest(profile=raw.attrs["profile_id"]):
                rows = window_rms_features(processed, WINDOWS)
                self.assertEqual(len(rows), 3 * raw.sizes["trace"])
                for gate, (start, stop) in enumerate(WINDOWS):
                    selected = (processed.twt_ns.values >= start) & (processed.twt_ns.values < stop)
                    for index, row in enumerate(
                        rows[gate * raw.sizes["trace"] : (gate + 1) * raw.sizes["trace"]]
                    ):
                        self.assertEqual(row["trace_id"], str(raw.trace_id.values[index]))
                        self.assertIsNone(row["plot_id"])
                        self.assertIsNone(row["depth_top_m"])
                        self.assertIsNone(row["velocity_calibration_evidence"])
                        self.assertEqual(row["branch"], "quantitative")
                        if processed.adc_limit_mask.values[index, selected].any():
                            self.assertIsNone(row["value"])
                        if row["value"] is not None:
                            values = processed.amplitude.values[index, selected]
                            expected = math.sqrt(
                                math.fsum(float(v) ** 2 for v in values) / len(values)
                            )
                            self.assertAlmostEqual(row["value"], expected, places=7)
                print(
                    f"RMS {raw.attrs['profile_id']}: {len(rows)} rows / "
                    f"{sum(r['value'] is None for r in rows)} excluded"
                )

    def test_raw_located_and_quantitative_zarr_roundtrip_exactly(self):
        from soil_sensing.io.storage import load_profile, save_profile

        with tempfile.TemporaryDirectory(prefix="soil-sensing-real-check-") as temporary:
            for index, (_, raw, located, processed, _) in enumerate(self.profiles):
                for name, profile in (
                    ("raw", raw),
                    ("located", located),
                    ("quantitative", processed),
                ):
                    with self.subTest(profile=profile.attrs["profile_id"], branch=name):
                        store = Path(temporary) / f"profile-{index}-{name}.zarr"
                        save_profile(profile, store)
                        with load_profile(store) as restored:
                            self.xr.testing.assert_identical(profile, restored)

    def test_display_gain_never_mutates_quantitative_values_or_enables_depth(self):
        from soil_sensing.processing.gpr import display_agc
        from soil_sensing.visualization.scene import axis_values

        for _, raw, _, processed, _ in self.profiles:
            with self.subTest(profile=raw.attrs["profile_id"]):
                before = processed.amplitude.values.copy()
                display = display_agc(processed, 10.0)
                self.assertEqual(display.shape, before.shape)
                self.np.testing.assert_array_equal(processed.amplitude.values, before)
                self.assertEqual(processed.attrs["processing_branch"], "quantitative")
                self.assertNotIn("depth_m", processed)
                with self.assertRaises(ValueError):
                    axis_values(processed, "depth")

    def test_z_original_collection_checksums_remain_identical(self):
        from soil_sensing.inventory import sha256_file

        self.assertEqual(self.before, {p: sha256_file(p) for p in self.sources})


if __name__ == "__main__":
    unittest.main(verbosity=2)
