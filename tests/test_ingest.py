"""Exact-valued binary fixtures and end-to-end ingestion without private data."""

import contextlib
import csv
import importlib.util
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from soil_sensing.cli import main
from soil_sensing.config import SurveyConfig, load_assignments, load_config

HAS_PROCESSING = all(importlib.util.find_spec(m) for m in ("numpy", "xarray", "zarr", "matplotlib"))
if HAS_PROCESSING:
    import numpy as np
    import xarray as xr

    from soil_sensing.ingest import ingest_collection
    from soil_sensing.inventory import sha256_file
    from soil_sensing.io.dt1 import read_dt1
    from soil_sensing.io.storage import load_profile, save_profile
    from soil_sensing.profile import QualityFlag, validate_profile

SAMPLES = [[-32768, -1, 0, 32767], [0, 0, 0, 0], [10, 20, -30, -40]]
HEADER = """1234
Data Collected with test system
2026-08-11
NUMBER OF TRACES = 3
NUMBER OF PTS/TRC = 4
TIMEZERO AT POINT = 1.5
TOTAL TIME WINDOW = 1.6
STARTING POSITION = 2.0
FINAL POSITION = 1.8
STEP SIZE USED = 0.1
POSITION UNITS = m
NOMINAL FREQUENCY = 200
ANTENNA SEPARATION = 0.5
SURVEY MODE = Reflection
"""
ASSIGNMENT_COLUMNS = [
    "profile_id",
    "survey_id",
    "plot_id",
    "include",
    "source_crs",
    "vertical_datum",
    "assignment_evidence",
    "reviewer",
]


def fixture(root: Path, name="LINE00") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.DT1"
    with path.open("wb") as stream:
        for i, samples in enumerate(SAMPLES):
            fields = [0.0] * 25
            fields[0], fields[1], fields[2], fields[5] = i + 1, 2 - i * 0.1, 4, 2
            fields[6], fields[7], fields[21], fields[23] = 400, 4, int(i == 1), 36000 + i
            fields[24] = int(i == 0)
            header = bytearray(struct.pack("<25f", *fields))
            header[32:40] = struct.pack("<d", 60.123456789)  # packed double, not two floats
            comment = b"annotated \xff trace".ljust(28, b"\0")
            stream.write(header + comment)
            stream.write(struct.pack("<4h", *samples))
    path.with_suffix(".hd").write_text(HEADER, encoding="utf-8")
    path.with_suffix(".GPS").write_text("unparsed original navigation", encoding="utf-8")
    return path


def assignment_csv(path: Path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=ASSIGNMENT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


class ConfigTests(unittest.TestCase):
    def test_config_paths_are_relative_to_file_and_unknown_fields_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "raw").mkdir()
            config_dir = base / "configs"
            config_dir.mkdir()
            path = config_dir / "survey.toml"
            content = 'project_id="test"\nraw_root="../raw"\n'
            path.write_text(content)
            self.assertEqual(load_config(path).raw_root, (base / "raw").resolve())
            for extra in (
                'schema_version="bad"',
                "assignment_status=true",
                "assignment_status=[]",
                "typo=1",
            ):
                with self.subTest(extra=extra):
                    path.write_text(content + extra)
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_assignment_identity_evidence_and_include_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assign.csv"
            good = {
                "profile_id": "group/LINE00",
                "survey_id": "test_survey",
                "plot_id": "plot_01",
                "include": "true",
                "assignment_evidence": "field log",
                "reviewer": "tester",
            }
            assignment_csv(path, [good])
            parsed = load_assignments(path)["group/line00"]
            self.assertTrue(parsed.include)
            self.assertEqual(parsed.plot_id, "plot_01")
            for change in (
                {"include": "yes"},
                {"reviewer": ""},
                {"profile_id": "../LINE00"},
                {"survey_id": ""},
            ):
                with self.subTest(change=change):
                    assignment_csv(path, [good | change])
                    with self.assertRaises(ValueError):
                        load_assignments(path)
            assignment_csv(path, [good, good | {"profile_id": "GROUP/line00"}])
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_assignments(path)


@unittest.skipUnless(HAS_PROCESSING, "Install the processing extra to run radar ingestion tests")
class ReaderTests(unittest.TestCase):
    def test_exact_signed_values_axes_time_and_reversed_odometer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = fixture(root)
            before = {p.name: sha256_file(p) for p in root.iterdir()}
            profile = read_dt1(path, root=root, source_id="test")
            np.testing.assert_array_equal(profile.amplitude, SAMPLES)
            self.assertEqual(profile.amplitude.dtype, np.float32)
            self.assertEqual(profile.amplitude.dims, ("trace", "sample"))
            np.testing.assert_allclose(profile.twt_ns, [0, 0.4, 0.8, 1.2], rtol=1e-12)
            np.testing.assert_allclose(profile.distance_m, [0, 0.1, 0.2], atol=1e-6)
            np.testing.assert_allclose(profile.recorded_position_m, [2, 1.9, 1.8], atol=1e-6)
            self.assertEqual(profile.attrs["time_zero_point_raw"], 1.5)
            self.assertIn("uncorrected", profile.attrs["time_zero_status"])
            self.assertEqual(
                list(profile.trace_id.values), ["test/LINE00:1", "test/LINE00:2", "test/LINE00:3"]
            )
            self.assertTrue(np.isnan(profile.x_m).all())
            self.assertTrue(np.isnat(profile.time_utc.values).all())
            self.assertIsNone(profile.attrs["plot_id"])
            self.assertEqual(before, {p.name: sha256_file(p) for p in root.iterdir()})

    def test_raw_header_bytes_and_flags_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = fixture(root)
            profile = read_dt1(path, root=root, source_id="test")
            raw = path.read_bytes()
            for i in range(3):
                self.assertEqual(
                    profile.trace_header_bytes.values[i].tobytes(), raw[i * 136 : i * 136 + 128]
                )
            self.assertEqual(
                int(profile.quality_flags[0]),
                int(QualityFlag.POSITION_MISSING | QualityFlag.ADC_LIMIT),
            )
            self.assertEqual(
                int(profile.quality_flags[1]),
                int(
                    QualityFlag.POSITION_MISSING | QualityFlag.ZERO_SIGNAL | QualityFlag.VENDOR_ZERO
                ),
            )

    def test_interior_record_corruption_is_rejected(self):
        cases = [
            (0, 77, "trace sequence"),
            (2, 9, "sample count"),
            (5, 4, "bytes per sample"),
            (1, float("nan"), "odometer"),
            (6, 500, "Variable binary timing"),
            (20, float("inf"), "time-zero"),
            (21, 3, "zero flag"),
            (23, 86400, "time of day"),
            (24, -1, "comment flag"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field, value, message in cases:
                with self.subTest(field=field):
                    path = fixture(root)
                    with path.open("r+b") as stream:
                        stream.seek(136 + field * 4)
                        stream.write(struct.pack("<f", value))
                    with self.assertRaisesRegex(ValueError, message):
                        read_dt1(path, root=root, source_id="test")

    def test_truncation_extra_data_bad_endian_and_hd_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("truncate", "append", "big_endian", "hd_position"):
                with self.subTest(mode=mode):
                    path = fixture(root)
                    if mode == "truncate":
                        path.write_bytes(path.read_bytes()[:-1])
                    elif mode == "append":
                        with path.open("ab") as stream:
                            stream.write(b"x")
                    elif mode == "big_endian":
                        with path.open("r+b") as stream:
                            stream.write(struct.pack(">f", 1))
                    else:
                        path.with_suffix(".hd").write_text(
                            HEADER.replace("FINAL POSITION = 1.8", "FINAL POSITION = 8")
                        )
                    with self.assertRaises(ValueError):
                        read_dt1(path, root=root, source_id="test")

    def test_feet_convert_once_and_nonreflection_modes_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = fixture(root)
            path.with_suffix(".hd").write_text(
                HEADER.replace("POSITION UNITS = m", "POSITION UNITS = ft")
            )
            profile = read_dt1(path, root=root, source_id="test")
            np.testing.assert_allclose(
                profile.recorded_position_m, np.array([2, 1.9, 1.8]) * 0.3048, atol=1e-6
            )
            self.assertEqual(profile.attrs["antenna_separation_m"], 0.5 * 0.3048)
            path.with_suffix(".hd").write_text(HEADER.replace("Reflection", "WARR"))
            with self.assertRaisesRegex(ValueError, "Reflection"):
                read_dt1(path, root=root, source_id="test")

    def test_contract_rejects_transpose_and_duplicate_trace_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = read_dt1(fixture(root), root=root, source_id="test")
            bad = profile.copy(deep=True)
            bad["amplitude"] = bad.amplitude.transpose("sample", "trace")
            with self.assertRaisesRegex(ValueError, "amplitude"):
                validate_profile(bad)
            bad = profile.copy(deep=True)
            bad.trace_id.values[1] = bad.trace_id.values[0]
            with self.assertRaisesRegex(ValueError, "unique"):
                validate_profile(bad)

    def test_lossless_zarr_roundtrip_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = read_dt1(fixture(root), root=root, source_id="test")
            store = root / "result.zarr"
            save_profile(profile, store)
            restored = load_profile(store)
            xr.testing.assert_identical(profile, restored)
            self.assertEqual(restored.quality_flags.dtype, np.uint32)
            self.assertEqual(restored.trace_header_bytes.dtype, np.uint8)
            with self.assertRaisesRegex(ValueError, "already exists"):
                save_profile(profile, store)


@unittest.skipUnless(HAS_PROCESSING, "Install the processing extra to run radar ingestion tests")
class IngestionTests(unittest.TestCase):
    def test_collection_keeps_duplicate_line_names_and_writes_complete_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "raw"
            fixture(root / "a")
            fixture(root / "b")
            output = base / "run"
            manifest = ingest_collection(SurveyConfig("test", root), output=output)
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len(manifest["profiles"]), 2)
            self.assertEqual(
                {p["profile_id"] for p in manifest["profiles"]}, {"a/LINE00", "b/LINE00"}
            )
            for item in manifest["profiles"]:
                self.assertTrue(item["round_trip_verified"])
                self.assertTrue((output / item["store"]).is_dir())
                self.assertGreater((output / item["radargram"]).stat().st_size, 1000)
            self.assertTrue((output / "index.html").is_file())
            self.assertEqual(json.loads((output / "run.json").read_text())["status"], "complete")
            with self.assertRaisesRegex(ValueError, "already exists"):
                ingest_collection(SurveyConfig("test", root), output=output)

    def test_explicit_exclusion_and_unknown_assignment_fail_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "raw"
            fixture(root)
            path = base / "assign.csv"
            assignment_csv(path, [{"profile_id": "LINE00", "include": "false"}])
            config = SurveyConfig("test", root, path)
            with self.assertRaisesRegex(ValueError, "excluded"):
                ingest_collection(config, output=base / "run", profiles=["LINE00.DT1"])
            assignment_csv(path, [{"profile_id": "DOES_NOT_EXIST", "include": "false"}])
            with self.assertRaisesRegex(ValueError, "unknown profiles"):
                ingest_collection(config, output=base / "run")
            self.assertFalse((base / "run").exists())

    def test_failed_validation_never_emits_complete_status(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "raw"
            path = fixture(root)
            with path.open("r+b") as stream:
                stream.seek(136)
                stream.write(struct.pack("<f", 100))
            output = base / "run"
            with self.assertRaisesRegex(ValueError, "trace sequence"):
                ingest_collection(SurveyConfig("test", root), output=output)
            manifest = json.loads((output / "run.json").read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertFalse((output / "index.html").exists())

    def test_cli_subset_and_source_output_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "raw"
            fixture(root)
            fixture(root, "LINE01")
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "ingest",
                            str(root),
                            "--profile",
                            "LINE01.DT1",
                            "--output",
                            str(base / "run"),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(["ingest", str(root), "--all", "--output", str(root / "bad-output")]), 2
                )
            self.assertFalse((root / "bad-output").exists())
            result = json.loads((base / "run" / "run.json").read_text())
            self.assertEqual(result["selected_profiles"], ["LINE01"])


if __name__ == "__main__":
    unittest.main()
