"""Strict little-endian, int16 Sensors & Software DT1 profile adapter.

Independent NumPy implementation, evaluated against GPRPy 1.0.14. Trace headers
are preserved as bytes because reserved fields may contain doubles or comments.
See docs/dt1_reader_decision.md for format evidence and time-zero limitations.
"""

from dataclasses import asdict
from pathlib import Path

import numpy as np
import xarray as xr

from soil_sensing import __version__
from soil_sensing.config import Assignment, validate_profile_id
from soil_sensing.inventory import sha256_file
from soil_sensing.io.sensors_software import read_header
from soil_sensing.profile import QualityFlag, validate_profile


def find_sidecar(path: Path, suffix: str, *, required: bool = False) -> Path | None:
    matches = [
        p
        for p in path.parent.iterdir()
        if p.name.casefold() == path.with_suffix(suffix).name.casefold()
    ]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous {suffix} companion for {path}")
    if not matches:
        if required:
            raise ValueError(f"Missing {suffix} companion for {path}")
        return None
    return matches[0]


def read_dt1(
    path: Path, *, root: Path, source_id: str, assignment: Assignment | None = None
) -> xr.Dataset:
    """Read and validate every record, retaining acquisition order and raw metadata.

    The supported layout has 128-byte headers and signed little-endian int16 samples.
    No geographic positions, UTC timestamps or corrected time-zero are inferred.
    """
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".dt1":
        raise ValueError("DT1 input must be a file inside the declared source root")
    profile_id = validate_profile_id(path.relative_to(root).with_suffix("").as_posix())
    if assignment and assignment.profile_id.casefold() != profile_id.casefold():
        raise ValueError("Assignment identity does not match the input profile")
    if assignment and assignment.include is False:
        raise ValueError(f"Profile explicitly excluded by assignment: {profile_id}")
    hd, gps = find_sidecar(path, ".hd", required=True), find_sidecar(path, ".gps")
    sources = [path, hd] + ([gps] if gps else [])
    if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in sources):
        raise ValueError("Source companions must be regular files inside the source root")
    checksums = {p.relative_to(root).as_posix(): sha256_file(p) for p in sources}
    header = read_header(hd)
    if header.raw_fields.get("SURVEY MODE", "Reflection").casefold() != "reflection":
        raise ValueError("This adapter currently supports Reflection survey mode only")
    if header.raw_fields.get("NUMBER OF CHANNELS", "1") != "1":
        raise ValueError("Multiplexed/multiple-channel DT1 is not supported")
    dtype = np.dtype([("header_bytes", "u1", 128), ("samples", "<i2", header.samples_per_trace)])
    expected_bytes = header.trace_count * dtype.itemsize
    if path.stat().st_size != expected_bytes:
        raise ValueError(
            f"DT1 byte count mismatch: expected {expected_bytes}, got {path.stat().st_size}"
        )
    if expected_bytes > 1024**3:
        raise ValueError("Profile exceeds the 1 GiB in-memory reader limit; split or stream it")
    records = np.fromfile(path, dtype=dtype, count=header.trace_count)
    if len(records) != header.trace_count:
        raise ValueError("DT1 changed or was truncated during reading")
    raw_headers = records["header_bytes"].copy()
    # Interpret only the known float fields; bytes 32:56 can contain packed doubles.
    fields = raw_headers[:, :100].copy().view("<f4").reshape(header.trace_count, 25)

    def require(condition: np.ndarray, message: str) -> None:
        failures = np.flatnonzero(~condition)
        if len(failures):
            raise ValueError(f"{message} at trace {int(failures[0]) + 1}")

    require(fields[:, 0] == np.arange(1, header.trace_count + 1), "Invalid trace sequence")
    require(fields[:, 2] == header.samples_per_trace, "Inconsistent sample count")
    require(fields[:, 5] == 2, "Unsupported bytes per sample (requires int16)")
    require(np.isfinite(fields[:, 1]), "Nonfinite odometer position")
    require(np.isfinite(fields[:, 6]) & (fields[:, 6] > 0), "Invalid binary timing field")
    require(fields[:, 6] == fields[0, 6], "Variable binary timing is not supported")
    require(np.isfinite(fields[:, 20]), "Invalid time-zero adjustment")
    require(np.isin(fields[:, 21], [0, 1]), "Invalid vendor zero flag")
    require(
        np.isfinite(fields[:, 23]) & (fields[:, 23] >= 0) & (fields[:, 23] < 86400),
        "Invalid instrument time of day",
    )
    require(np.isin(fields[:, 24], [0, 1]), "Invalid comment flag")
    scale = 0.3048 if header.position_units == "ft" else 1.0
    positions = fields[:, 1].astype(np.float64) * scale
    # Float32 position precision increases with absolute coordinate magnitude.
    tolerance = max(1e-5, np.max(np.abs(positions)) * 2e-7)
    if not np.isclose(positions[0], header.start_position * scale, atol=tolerance, rtol=0):
        raise ValueError("First DT1 position disagrees with HD STARTING POSITION")
    if not np.isclose(positions[-1], header.final_position * scale, atol=tolerance, rtol=0):
        raise ValueError("Last DT1 position disagrees with HD FINAL POSITION")
    data = records["samples"].astype(np.float32)
    flags = np.full(header.trace_count, int(QualityFlag.POSITION_MISSING), dtype=np.uint32)
    flags[np.all(data == 0, axis=1)] |= int(QualityFlag.ZERO_SIGNAL)
    flags[np.any((data == -32768) | (data == 32767), axis=1)] |= int(QualityFlag.ADC_LIMIT)
    flags[fields[:, 21] == 1] |= int(QualityFlag.VENDOR_ZERO)
    interval = header.time_window_ns / header.samples_per_trace
    missing = np.full(header.trace_count, np.nan)
    profile = xr.Dataset(
        data_vars={
            "amplitude": (("trace", "sample"), data, {"units": "ADC counts"}),
            "trace_id": (
                "trace",
                [f"{source_id}/{profile_id}:{i + 1}" for i in range(header.trace_count)],
            ),
            "distance_m": (
                "trace",
                np.r_[0.0, np.cumsum(np.abs(np.diff(positions)))],
                {"units": "m", "source": "cumulative absolute odometer increments"},
            ),
            "recorded_position_m": ("trace", positions, {"units": "m"}),
            "x_m": ("trace", missing.copy()),
            "y_m": ("trace", missing.copy()),
            "elevation_m": ("trace", missing.copy()),
            "horizontal_uncertainty_m": ("trace", missing.copy()),
            "time_utc": ("trace", np.full(header.trace_count, np.datetime64("NaT", "ns"))),
            "position_status": ("trace", np.full(header.trace_count, "missing", dtype="<U12")),
            "quality_flags": ("trace", flags),
            "trace_header_bytes": (("trace", "header_byte"), raw_headers),
            "vendor_time_zero_adjustment": ("trace", fields[:, 20].copy()),
            "vendor_seconds_since_midnight": (
                "trace",
                fields[:, 23].copy(),
                {"timezone": "unknown", "units": "s"},
            ),
            "vendor_binary_field_7": ("trace", fields[:, 6].copy()),
        },
        coords={
            "trace": np.arange(header.trace_count),
            "sample": np.arange(header.samples_per_trace),
            "header_byte": np.arange(128),
            "twt_ns": (
                "sample",
                np.arange(header.samples_per_trace) * interval,
                {"units": "ns", "long_name": "recording time; time-zero uncorrected"},
            ),
        },
        attrs={
            "schema_version": "0.2",
            "tool_version": __version__,
            "profile_id": profile_id,
            "source_id": source_id,
            "survey_id": assignment.survey_id if assignment else None,
            "plot_id": assignment.plot_id if assignment else None,
            "assignment": asdict(assignment) if assignment else None,
            "source_crs": assignment.source_crs if assignment else None,
            "vertical_datum": assignment.vertical_datum if assignment else None,
            "target_crs": None,
            "processing_branch": "raw",
            "amplitude_units": "ADC counts",
            "antenna_frequency_mhz": header.nominal_frequency_mhz,
            "antenna_separation_m": header.antenna_separation * scale,
            "original_hd": asdict(header),
            "original_hd_text": hd.read_text(encoding="utf-8-sig"),
            "source_checksums": checksums,
            "sample_interval_ns": interval,
            "time_axis_convention": "arange(N) * HD_total_time_window_ns / N; recording origin",
            "time_zero_point_raw": header.time_zero_point,
            "time_zero_status": "uncorrected; vendor indexing convention unresolved",
            "gps_status": "sidecar_preserved_not_parsed" if gps else "missing_sidecar",
            "channel_model": "single-profile int16 layout; no channel identity inferred",
            "quality_flag_definitions": {flag.name: int(flag) for flag in QualityFlag},
            "checks_performed": [
                "every_trace_number",
                "every_sample_count",
                "every_dtype",
                "file_size",
                "odometer_endpoints",
                "timing_consistency",
                "vendor_flags",
                "zero_signal",
                "adc_limits",
            ],
            "history": [
                {
                    "operation": "read_dt1",
                    "adapter": "numpy-strict-dt1-v1",
                    "time_zero_applied": False,
                    "georeferenced": False,
                }
            ],
        },
    )
    validate_profile(profile)
    if checksums != {p.relative_to(root).as_posix(): sha256_file(p) for p in sources}:
        raise ValueError("Source files changed during ingestion")
    return profile
