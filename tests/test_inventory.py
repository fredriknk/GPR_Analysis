"""Synthetic tests; no private survey files are needed."""

import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from soil_sensing.cli import main
from soil_sensing.inventory import build_inventory, sha256_file
from soil_sensing.io.sensors_software import read_header

HEADER = """1234

Data Collected with test instrument

2026-08-11

NUMBER OF TRACES = 2
NUMBER OF PTS/TRC = 3
TIMEZERO AT POINT = 1.25
TOTAL TIME WINDOW = 10.000
STARTING POSITION = 0.0
FINAL POSITION = 0.1
STEP SIZE USED = 0.1
POSITION UNITS = m
NOMINAL FREQUENCY = 200
ANTENNA SEPARATION = 0.5
UNKNOWN VENDOR FIELD = preserve this
"""


def make_profile(folder: Path, name: str = "LINE00") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.DT1"
    with path.open("wb") as stream:
        for number in (1, 2):
            stream.write(struct.pack("<32f", number, (number - 1) * 0.1, 3, *([0] * 29)))
            stream.write(struct.pack("<3h", -32768, 0, 32767))
    path.with_suffix(".hd").write_text(HEADER, encoding="utf-8")
    path.with_suffix(".GPS").write_text("Trace #1 at position 0.0\n", encoding="utf-8")
    return path


class HeaderTests(unittest.TestCase):
    def test_blank_lines_crlf_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "line.HD"
            path.write_bytes(HEADER.replace("\n", "\r\n").encode())
            header = read_header(path)
            self.assertEqual(header.trace_count, 2)
            self.assertEqual(header.samples_per_trace, 3)
            self.assertEqual(header.time_zero_point, 1.25)
            self.assertEqual(header.raw_fields["UNKNOWN VENDOR FIELD"], "preserve this")

    def test_invalid_metadata_is_rejected(self):
        cases = [
            ("NUMBER OF TRACES = 2", "NUMBER OF TRACES = 2.5"),
            ("NUMBER OF TRACES = 2", "NUMBER OF TRACES = 0"),
            ("NUMBER OF PTS/TRC = 3", ""),
            ("TOTAL TIME WINDOW = 10.000", "TOTAL TIME WINDOW = NaN"),
            ("POSITION UNITS = m", "POSITION UNITS = unknown"),
            ("2026-08-11", "2026-19-11"),
            ("NUMBER OF TRACES = 2", "NUMBER OF TRACES = 2\nNUMBER OF TRACES = 3"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "line.HD"
            for old, new in cases:
                with self.subTest(new=new):
                    path.write_text(HEADER.replace(old, new), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_header(path)

    def test_feet_are_preserved_not_mislabeled_as_metres(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "line.HD"
            path.write_text(HEADER.replace("POSITION UNITS = m", "POSITION UNITS = ft"))
            header = read_header(path)
            self.assertEqual(header.position_units, "ft")
            self.assertEqual(header.step_size, 0.1)


class InventoryTests(unittest.TestCase):
    def test_case_insensitive_companions_hashes_and_immutable_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_profile(root)
            before = {p.name: sha256_file(p) for p in root.iterdir()}
            result = build_inventory(root, hash_raw=True)
            self.assertEqual(result["summary"]["trace_count_from_headers"], 2)
            self.assertEqual(result["profiles"][0]["issues"], [])
            self.assertIsNone(result["profiles"][0]["plot_id"])
            self.assertEqual(result["profiles"][0]["hd_path"], "LINE00.hd")
            self.assertEqual(result["files"][0]["sha256"], sha256_file(path))
            self.assertEqual(before, {p.name: sha256_file(p) for p in root.iterdir()})

    def test_duplicate_basenames_dates_and_vendor_products(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_profile(root / "survey_a")
            path = make_profile(root / "survey_b")
            path.with_suffix(".hd").write_text(HEADER.replace("2026-08-11", "2026-06-11"))
            (root / "slice.CSV").write_text("vendor output")
            result = build_inventory(root)
            self.assertEqual(len({p["profile_id"] for p in result["profiles"]}), 2)
            self.assertIn("multiple_acquisition_dates", result["summary"]["warnings"])
            vendor = next(f for f in result["files"] if f["path"] == "slice.CSV")
            self.assertEqual(vendor["role"], "vendor_reference_candidate")
            self.assertNotIn("sha256", vendor)

    def test_missing_header_gps_and_orphans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_profile(root)
            path.with_suffix(".hd").unlink()
            path.with_suffix(".GPS").unlink()
            (root / "orphan.GPS").write_text("unpaired")
            result = build_inventory(root)
            self.assertEqual(result["profiles"][0]["issues"], ["missing_gps", "missing_header"])
            self.assertEqual(result["orphan_sidecars"], ["orphan.GPS"])

    def test_truncation_and_endpoint_header_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_profile(root)
            with path.open("r+b") as stream:
                stream.write(struct.pack("<f", 99))
            result = build_inventory(root)
            self.assertIn("dt1_endpoint_header_mismatch", result["profiles"][0]["issues"])
            with path.open("r+b") as stream:
                stream.truncate(17)
            result = build_inventory(root)
            self.assertIn("dt1_size_mismatch", result["profiles"][0]["issues"])

    def test_bad_header_reported_without_aborting_other_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_profile(root, "BAD")
            path.with_suffix(".hd").write_text("invalid header")
            make_profile(root, "GOOD")
            result = build_inventory(root)
            self.assertEqual(result["summary"]["profile_count"], 2)
            self.assertEqual(result["summary"]["profiles_with_issues"], 1)


class CliTests(unittest.TestCase):
    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(args)

    def test_write_manifest_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "raw"
            make_profile(root)
            output = Path(directory) / "result.json"
            args = ["inventory", str(root), "--output", str(output)]
            self.assertEqual(self.invoke(args), 0)
            self.assertEqual(json.loads(output.read_text())["summary"]["profile_count"], 1)
            before = output.read_bytes()
            self.assertEqual(self.invoke(args), 2)
            self.assertEqual(output.read_bytes(), before)

    def test_refuse_output_inside_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_profile(root)
            output = root / "nested" / "result.json"
            self.assertEqual(self.invoke(["inventory", str(root), "--output", str(output)]), 2)
            self.assertFalse(output.exists())

    def test_strict_and_invalid_root_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_profile(root)
            (root / "orphan.GPS").write_text("unpaired")
            self.assertEqual(self.invoke(["inventory", str(root)]), 0)
            self.assertEqual(self.invoke(["inventory", str(root), "--strict"]), 1)
            self.assertEqual(self.invoke(["inventory", str(root / "absent")]), 2)


if __name__ == "__main__":
    unittest.main()
