"""Synthetic M2/M3 regressions; no private field files required."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_ingest import fixture

from soil_sensing.cli import main
from soil_sensing.features.picks import read_reflector_picks
from soil_sensing.fusion.matching import match_texture_to_gpr
from soil_sensing.fusion.observations import (
    read_manual_texture_csv,
    read_observations,
    register_adapter,
)
from soil_sensing.io.dt1 import read_dt1
from soil_sensing.processing.depth import VelocityCalibration, add_depth_axis
from soil_sensing.processing.gpr import (
    ProcessingOptions,
    display_agc,
    process_profile,
    window_rms_features,
)
from soil_sensing.profile import QualityFlag, validate_profile
from soil_sensing.spatial.gps import GPSOptions, locate_profile
from soil_sensing.spatial.plots import coverage_report, load_plots


def sentence(body: str) -> str:
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    return f"${body}*{checksum:02X}\n"


def block(
    trace: int,
    position: float,
    epoch: str,
    date: str,
    latitude: str,
    longitude: str = "01000.000000",
) -> str:
    gns = sentence(f"GNGNS,{epoch},{latitude},N,{longitude},E,DDDDN,10,0.6,100.0,40.0,,,")
    rmc = sentence(f"GPRMC,{epoch},A,{latitude},N,{longitude},E,0.0,0.0,{date},,,D")
    return f"Trace #{trace} at position {position:.6f}\n{gns}{rmc}"


class GPSTests(unittest.TestCase):
    def _read(self, gps: str):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        path = fixture(root)
        path.with_suffix(".GPS").write_text(gps, encoding="utf-8")
        return temporary, read_dt1(path, root=root, source_id="synthetic"), path.with_suffix(".GPS")

    def test_midnight_reversed_line_and_bounded_interpolation(self):
        gps = block(1, 2, "235959.00", "110826", "6000.000000")
        gps += block(3, 1.8, "000001.00", "120826", "6000.000500")
        temp, raw, path = self._read(gps)
        with temp:
            located, qc = locate_profile(raw, path)
            self.assertEqual(qc["measured"], 2)
            self.assertEqual(qc["interpolated"], 1)
            self.assertEqual(qc["recorded_odometer_direction"], "decreasing")
            self.assertEqual(str(located.time_utc.values[1]), "2026-08-12T00:00:00.000000000")
            self.assertAlmostEqual(located.latitude_deg.values[1], 60.0000041667, places=7)
            self.assertTrue(np.isnan(located.x_m.values).all())
            self.assertTrue(
                bool(located.quality_flags.values[1] & int(QualityFlag.GPS_INTERPOLATED))
            )
            limited, qc = locate_profile(raw, path, GPSOptions(max_interpolation_distance_m=0.1))
            self.assertEqual(qc["missing"], 1)
            self.assertTrue(np.isnat(limited.time_utc.values[1]))

    def test_invalid_checksum_empty_constellation_duplicate_and_missing_endpoint(self):
        bad = "$GPRMC,120000.00,A,6000.000000,N,01000.000000,E,0,0,110826,,,D*00\n"
        gps = f"Trace #1 at position 2.0\n{bad}"
        gps += f"Trace #2 at position 1.9\n{sentence('GPGNS,120001.00,,,,,,10,,,,')}"
        gps += block(2, 1.9, "120001.00", "110826", "6000.000100")
        temp, raw, path = self._read(gps)
        with temp:
            located, qc = locate_profile(raw, path)
            self.assertEqual(qc["invalid_sentences"], 1)
            self.assertEqual(qc["duplicate_markers"], 1)
            self.assertEqual(qc["measured"], 0)
            self.assertEqual(qc["rejected"], 2)
            self.assertEqual(qc["missing"], 1)  # no endpoint extrapolation
            self.assertTrue(np.isnan(located.longitude_deg.values).all())

    def test_marker_position_mismatch_rejected(self):
        gps = block(1, 99, "120000.00", "110826", "6000.000000")
        temp, raw, path = self._read(gps)
        with temp:
            located, qc = locate_profile(raw, path)
            self.assertEqual(qc["rejected"], 1)
            self.assertTrue(
                bool(located.quality_flags.values[0] & int(QualityFlag.GPS_POSITION_MISMATCH))
            )

    def test_explicit_projection_and_lever_arm_known_control(self):
        gps = block(1, 2, "120000.00", "110826", "0000.000000", "00300.000000")
        gps += block(3, 1.8, "120002.00", "110826", "0000.000000", "00300.000600")
        temp, raw, path = self._read(gps)
        with temp:
            options = GPSOptions(
                source_crs="EPSG:4326", target_crs="EPSG:32631", antenna_forward_m=1.0
            )
            located, qc = locate_profile(raw, path, options)
            self.assertEqual(qc["projected"], 3)
            self.assertAlmostEqual(located.x_m.values[0], 500001.0, delta=0.02)
            self.assertAlmostEqual(located.y_m.values[0], 0.0, delta=0.02)
            self.assertEqual(located.attrs["target_crs"], "EPSG:32631")
            located.attrs["plot_id"] = "plot_01"
            plot_config = Path(temp.name) / "plots.toml"
            plot_config.write_text(
                'schema_version="0.1"\nproject_id="test"\n'
                '[[plots]]\nplot_id="plot_01"\nwidth_m=100\nlength_m=100\n'
                'geometry_status="reviewed"\ncrs="EPSG:32631"\nevidence="synthetic control"\n'
                "polygon=[[499950,-50],[500050,-50],[500050,50],[499950,50]]\n",
                encoding="utf-8",
            )
            self.assertEqual(coverage_report(located, load_plots(plot_config))["inside_traces"], 3)
            with self.assertRaisesRegex(ValueError, "geographic"):
                locate_profile(
                    raw, path, GPSOptions(source_crs="EPSG:32631", target_crs="EPSG:32631")
                )


class ProcessingTests(unittest.TestCase):
    def _profile(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        raw = read_dt1(fixture(root), root=root, source_id="synthetic")
        raw = raw.drop_dims("sample")
        n = 256
        raw = raw.assign_coords(sample=np.arange(n), twt_ns=("sample", np.arange(n) * 0.4))
        t = np.arange(n)
        pulse = 100 * np.exp(-0.5 * ((t - 100) / 2) ** 2)
        signal = np.tile(20 + pulse, (3, 1)).astype(np.float32)
        signal[0, 120] = 32767
        raw["amplitude"] = (("trace", "sample"), signal)
        raw["quality_flags"] = ("trace", np.array([5, 1, 1], dtype=np.uint32))
        validate_profile(raw)
        return temporary, raw

    def test_time_zero_filter_and_window_local_clipping(self):
        temp, raw = self._profile()
        with temp:
            options = ProcessingOptions(
                reviewed_time_zero_ns=2.0, time_zero_evidence="synthetic direct-wave control"
            )
            quantitative = process_profile(raw, options)
            self.assertEqual(quantitative.attrs["processing_branch"], "quantitative")
            self.assertEqual(float(quantitative.corrected_twt_ns.values[5]), 0.0)
            self.assertEqual(int(np.argmax(quantitative.amplitude.values[1])), 100)
            self.assertEqual(float(raw.amplitude.values[1, 0]), 20.0)
            features = window_rms_features(quantitative, [(0, 20), (40, 60)])
            self.assertIsNotNone(features[0]["value"])
            self.assertIsNone(features[3]["value"])
            self.assertIsNotNone(features[4]["value"])
            gained = display_agc(quantitative, 20.0)
            self.assertFalse(np.array_equal(gained, quantitative.amplitude.values))
            self.assertEqual(quantitative.attrs["processing_branch"], "quantitative")
            calibration = VelocityCalibration("test-v", "synthetic reflector", 0.1, 0.01, 0.2)
            depth = add_depth_axis(quantitative, calibration)
            self.assertTrue(np.isnan(depth.depth_m.values[5]))
            expected = np.sqrt((0.1 * 20 / 2) ** 2 - (0.5 / 2) ** 2)
            self.assertAlmostEqual(depth.depth_m.values[55], expected, places=6)
            self.assertGreater(depth.depth_standard_uncertainty_m.values[55], 0)

    def test_invalid_filter_and_branch_guards(self):
        temp, raw = self._profile()
        with temp:
            with self.assertRaisesRegex(ValueError, "cutoffs"):
                process_profile(
                    raw, ProcessingOptions(bandpass_low_mhz=100, bandpass_high_mhz=2000)
                )
            with self.assertRaisesRegex(ValueError, "evidence"):
                process_profile(raw, ProcessingOptions(reviewed_time_zero_ns=2))
            processed = process_profile(
                raw, ProcessingOptions(bandpass_low_mhz=50, bandpass_high_mhz=400)
            )
            self.assertEqual(processed.amplitude.shape, raw.amplitude.shape)
            with self.assertRaisesRegex(ValueError, "raw profile"):
                process_profile(processed, ProcessingOptions())
            with self.assertRaisesRegex(ValueError, "quantitative branch"):
                window_rms_features(raw, [(0, 20)])
            with self.assertRaisesRegex(ValueError, "reviewed physical time zero"):
                add_depth_axis(
                    processed,
                    VelocityCalibration("test-v", "synthetic reflector", 0.1, 0.01, 0.2),
                )

    def test_bandpass_transfer_and_reviewed_pick_format(self):
        temp, raw = self._profile()
        with temp:
            t_ns = raw.twt_ns.values
            passband = np.sin(2 * np.pi * 200 * t_ns / 1000)
            stopband = np.sin(2 * np.pi * 800 * t_ns / 1000)
            raw["amplitude"] = (
                ("trace", "sample"),
                np.tile(passband + stopband, (3, 1)).astype(np.float32),
            )
            raw["quality_flags"] = ("trace", np.ones(3, dtype=np.uint32))
            processed = process_profile(
                raw, ProcessingOptions(remove_dc=False, bandpass_low_mhz=50, bandpass_high_mhz=400)
            )
            centre = slice(40, 215)
            output = processed.amplitude.values[0, centre]
            pass_gain = 2 * abs(np.mean(output * passband[centre]))
            stop_gain = 2 * abs(np.mean(output * stopband[centre]))
            self.assertGreater(pass_gain, 0.8)
            self.assertLess(stop_gain, 0.1)
            picks_csv = Path(temp.name) / "picks.csv"
            fields = Path("data/templates/reflector_picks.csv").read_text().strip().split(",")
            row = {
                "pick_id": "pick1",
                "trace_id": str(raw.trace_id.values[0]),
                "horizon_id": "h1",
                "time_basis": "recording",
                "two_way_time_ns": 40,
                "standard_uncertainty_ns": 0.4,
                "reviewer": "synthetic_reviewer",
                "evidence": "known event",
                "quality_flag": 0,
            }
            with picks_csv.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)
            self.assertEqual(len(read_reflector_picks(picks_csv, raw)), 1)
            row["time_basis"] = "corrected"
            with picks_csv.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(ValueError, "reviewed time zero"):
                read_reflector_picks(picks_csv, raw)


class PlotTests(unittest.TestCase):
    def test_missing_geometry_and_unassigned_profile(self):
        temp = tempfile.TemporaryDirectory()
        with temp:
            root = Path(temp.name)
            raw = read_dt1(fixture(root), root=root, source_id="synthetic")
            plots = load_plots(Path("configs/plots/four_plots.toml"))
            self.assertEqual(coverage_report(raw, plots)["status"], "unassigned")
            raw.attrs["plot_id"] = "plot_01"
            self.assertEqual(coverage_report(raw, plots)["status"], "geometry_missing")


class FusionTests(unittest.TestCase):
    def _texture(self, path: Path, *, unit: str = "percent", clay: float = 20) -> None:
        fields = Path("data/templates/soil_measurements.csv").read_text().strip().split(",")
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for property_name, value in (
                ("sand_fraction", 40),
                ("silt_fraction", 40),
                ("clay_fraction", clay),
            ):
                writer.writerow(
                    {
                        "measurement_id": f"m_{property_name}",
                        "sample_id": "sample_1",
                        "plot_id": "plot_01",
                        "survey_id": "lab_2026",
                        "acquired_at_utc": "2026-08-11T12:00:00Z",
                        "x": 500000,
                        "y": 0,
                        "crs": "EPSG:32631",
                        "horizontal_uncertainty_m": "",
                        "depth_top_m": 0.1,
                        "depth_bottom_m": 0.3,
                        "property": property_name,
                        "value": value,
                        "unit": unit,
                        "method": "synthetic_sieving",
                        "sand_silt_cutoff_um": 50,
                        "silt_clay_cutoff_um": 2,
                        "closure_tolerance_percent": 1,
                        "uncertainty": "",
                        "uncertainty_kind": "",
                        "lab_batch_id": "batch_1",
                        "quality_flag": 0,
                    }
                )

    def test_texture_validation_and_adapter_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "texture.csv"
            self._texture(path)
            observations = read_manual_texture_csv(path)
            self.assertEqual(len(observations), 3)
            self.assertEqual(
                {o.source_sha256 for o in observations}, {observations[0].source_sha256}
            )
            self._texture(path, unit="mystery")
            with self.assertRaisesRegex(ValueError, "unit"):
                read_manual_texture_csv(path)
            self._texture(path, clay=18)
            with self.assertRaisesRegex(ValueError, "closure"):
                read_manual_texture_csv(path)
            register_adapter("synthetic_future_sensor", lambda _: [])
            self.assertEqual(read_observations("synthetic_future_sensor", path), [])

    def test_support_matching_and_exclusion_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "texture.csv"
            self._texture(path)
            observations = read_manual_texture_csv(path)
            base = {
                "trace_id": "gpr/profile:1",
                "feature_name": "gpr_rms",
                "window_start_ns": 20,
                "window_stop_ns": 40,
                "branch": "quantitative",
                "unit": "ADC counts",
                "value": 3.0,
                "plot_id": "plot_01",
                "target_crs": "EPSG:32631",
                "position_status": "measured",
                "x_m": 500000,
                "y_m": 0,
                "time_utc": "2026-08-11T12:30:00Z",
                "depth_top_m": 0.1,
                "depth_bottom_m": 0.3,
                "velocity_calibration_id": "synthetic_v1",
            }
            features = [
                base,
                {**base, "trace_id": "gpr/profile:2", "value": 5.0},
                {**base, "trace_id": "gpr/profile:3", "x_m": 500100},
                {**base, "trace_id": "gpr/profile:4", "time_utc": "2026-08-13T00:00:00Z"},
                {**base, "trace_id": "gpr/profile:5", "depth_top_m": 0.4, "depth_bottom_m": 0.5},
                {**base, "trace_id": "gpr/profile:6", "velocity_calibration_id": None},
            ]
            rows, audit = match_texture_to_gpr(
                observations,
                features,
                radius_m=2,
                max_lag_hours=24,
            )
            self.assertEqual(rows[0]["gpr_rms_20_40_ns"], 4.0)
            self.assertEqual(rows[0]["gpr_rms_20_40_ns_contributors"], 2)
            self.assertEqual(sum(item["included"] for item in audit), 2)
            self.assertEqual(
                {item["exclusion_reason"] for item in audit if not item["included"]},
                {
                    "outside_radius",
                    "outside_time_tolerance",
                    "insufficient_depth_overlap",
                    "uncalibrated_depth",
                },
            )
            with self.assertRaisesRegex(ValueError, "Unknown GPR amplitude feature unit"):
                match_texture_to_gpr(
                    observations,
                    [{**base, "unit": "unverified"}],
                    radius_m=2,
                    max_lag_hours=24,
                )

    def test_cli_texture_and_match_runs_are_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texture = root / "texture.csv"
            features = root / "features.csv"
            self._texture(texture)
            feature = {
                "trace_id": "site/line:1",
                "feature_name": "gpr_rms",
                "window_start_ns": 20,
                "window_stop_ns": 40,
                "branch": "quantitative",
                "unit": "ADC counts",
                "value": 3.0,
                "plot_id": "plot_01",
                "target_crs": "EPSG:32631",
                "position_status": "measured",
                "x_m": 500000,
                "y_m": 0,
                "time_utc": "2026-08-11T12:30:00Z",
                "depth_top_m": 0.1,
                "depth_bottom_m": 0.3,
                "velocity_calibration_id": "synthetic_v1",
            }
            with features.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(feature))
                writer.writeheader()
                writer.writerow(feature)
            texture_out = root / "validated"
            self.assertEqual(
                main(["texture", "--input", str(texture), "--output", str(texture_out)]), 0
            )
            self.assertEqual(json.loads((texture_out / "run.json").read_text())["sample_count"], 1)
            results = []
            for i in range(2):
                output = root / f"match_{i}"
                self.assertEqual(
                    main(
                        [
                            "match",
                            "--texture",
                            str(texture),
                            "--features",
                            str(features),
                            "--radius-m",
                            "2",
                            "--max-lag-hours",
                            "24",
                            "--output",
                            str(output),
                        ]
                    ),
                    0,
                )
                results.append((output / "matched_samples.csv").read_bytes())
                self.assertEqual(
                    json.loads((output / "run.json").read_text())["contributor_count"], 1
                )
            self.assertEqual(results[0], results[1])
