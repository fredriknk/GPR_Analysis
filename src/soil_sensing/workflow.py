"""Single-profile, immutable-input M2/M3 runs with reviewable outputs."""

import csv
import tomllib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import xarray as xr

from soil_sensing import __version__
from soil_sensing.export.radargram import plot_radargram
from soil_sensing.ingest import code_hash, tree_hash, write_json
from soil_sensing.inventory import sha256_file
from soil_sensing.io.storage import load_profile, save_profile
from soil_sensing.processing.depth import VelocityCalibration, add_depth_axis
from soil_sensing.processing.gpr import (
    ProcessingOptions,
    display_agc,
    process_profile,
    window_rms_features,
)
from soil_sensing.spatial.gps import GPSOptions, locate_profile


def _prepare(input_store: Path, output: Path) -> tuple[Path, Path]:
    input_store, output = input_store.resolve(), output.resolve()
    if not input_store.is_dir() or not input_store.name.endswith(".zarr"):
        raise ValueError("Input must be an existing profile Zarr store")
    if "gpr_raw_data" in (part.casefold() for part in output.parts):
        raise ValueError("Outputs cannot be written into GPR_raw_data")
    if output.exists() or output.is_relative_to(input_store) or input_store.is_relative_to(output):
        raise ValueError("Output must be a new directory separate from the input")
    output.mkdir(parents=True, exist_ok=False)
    return input_store, output


def _manifest(operation: str, input_store: Path) -> dict:
    return {
        "schema_version": "0.1",
        "tool_version": __version__,
        "status": "running",
        "operation": operation,
        "started_utc": datetime.now(UTC).isoformat(),
        "input_store": str(input_store),
        "input_sha256": tree_hash(input_store),
        "code_sha256": code_hash(),
        "artifacts": {},
    }


def _finish(output: Path, manifest: dict, exc: Exception | None = None) -> dict:
    manifest["finished_utc"] = datetime.now(UTC).isoformat()
    manifest["status"] = "complete" if exc is None else "failed"
    if exc is not None:
        manifest["error"] = f"{type(exc).__name__}: {exc}"
    write_json(output / "run.json", manifest)
    return manifest


def locate_run(
    input_store: Path, gps_path: Path, output: Path, options: GPSOptions | None = None
) -> dict:
    options = options or GPSOptions()
    input_store, output = _prepare(input_store, output)
    manifest = _manifest("locate_gps", input_store)
    write_json(output / "run.json", manifest)
    try:
        raw = load_profile(input_store)
        located, qc = locate_profile(raw, gps_path, options)
        store = output / "located.zarr"
        save_profile(located, store)
        restored = load_profile(store)
        xr.testing.assert_identical(located, restored)
        manifest.update(
            gps_path=str(gps_path.resolve()),
            gps_sha256=sha256_file(gps_path),
            options=asdict(options),
            qc=qc,
        )
        manifest["artifacts"]["located.zarr"] = tree_hash(store)
        raw.close()
        located.close()
        restored.close()
    except Exception as exc:
        _finish(output, manifest, exc)
        raise
    return _finish(output, manifest)


def load_processing_config(
    path: Path,
) -> tuple[ProcessingOptions, list[tuple[float, float]], float, VelocityCalibration | None]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version",
        "pipeline_id",
        "remove_dc",
        "dewow_window_ns",
        "bandpass_low_mhz",
        "bandpass_high_mhz",
        "bandpass_order",
        "reviewed_time_zero_ns",
        "time_zero_evidence",
        "display_agc_window_ns",
        "attribute_windows_ns",
        "velocity_calibration",
    }
    if data.get("schema_version") != "0.2" or set(data) - allowed:
        raise ValueError("Unsupported processing config schema or unknown fields")
    if not data.get("pipeline_id"):
        raise ValueError("pipeline_id is required")
    if not isinstance(data.get("remove_dc", True), bool):
        raise ValueError("remove_dc must be Boolean")
    options = ProcessingOptions(
        **{key: data[key] for key in asdict(ProcessingOptions()) if key in data}
    )
    windows = data.get("attribute_windows_ns", [])
    if (
        not isinstance(windows, list)
        or not windows
        or any(not isinstance(w, list) or len(w) != 2 for w in windows)
    ):
        raise ValueError("attribute_windows_ns must contain [start, stop] pairs")
    agc = data.get("display_agc_window_ns")
    if agc is None or not isinstance(agc, (int, float)):
        raise ValueError("display_agc_window_ns is required")
    calibration_data = data.get("velocity_calibration")
    calibration = None
    if calibration_data is not None:
        fields = set(VelocityCalibration.__dataclass_fields__)
        if not isinstance(calibration_data, dict) or set(calibration_data) != fields:
            raise ValueError("Velocity calibration requires all fields and no extras")
        calibration = VelocityCalibration(**calibration_data)
        if options.reviewed_time_zero_ns is None:
            raise ValueError("Velocity calibration also requires reviewed time zero")
    return options, [tuple(w) for w in windows], float(agc), calibration


def process_run(input_store: Path, config_path: Path, output: Path) -> dict:
    options, windows, agc, calibration = load_processing_config(config_path)
    input_store, output = _prepare(input_store, output)
    manifest = _manifest("process_gpr", input_store)
    manifest.update(
        config_path=str(config_path.resolve()),
        config_sha256=sha256_file(config_path),
        options=asdict(options),
        windows_ns=windows,
        display_agc_window_ns=agc,
        velocity_calibration=asdict(calibration) if calibration else None,
    )
    write_json(output / "run.json", manifest)
    try:
        raw = load_profile(input_store)
        processed = process_profile(raw, options)
        if calibration is not None:
            processed = add_depth_axis(processed, calibration)
        rows = window_rms_features(processed, windows)
        store = output / "quantitative.zarr"
        save_profile(processed, store)
        restored = load_profile(store)
        xr.testing.assert_identical(processed, restored)
        with (output / "attributes.csv").open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        plot_radargram(raw, output / "raw.png")
        plot_radargram(processed, output / "quantitative.png")
        plot_radargram(
            processed, output / "display_agc.png", display_amplitude=display_agc(processed, agc)
        )
        manifest["feature_rows"] = len(rows)
        manifest["excluded_feature_rows"] = sum(row["value"] is None for row in rows)
        for name in (
            "quantitative.zarr",
            "attributes.csv",
            "raw.png",
            "quantitative.png",
            "display_agc.png",
        ):
            path = output / name
            manifest["artifacts"][name] = tree_hash(path) if path.is_dir() else sha256_file(path)
        raw.close()
        processed.close()
        restored.close()
    except Exception as exc:
        _finish(output, manifest, exc)
        raise
    return _finish(output, manifest)


def gps_audit_collection(import_run: Path, raw_root: Path, output: Path, progress=None) -> dict:
    """Check all imported profile/GPS pairs without exporting assumed coordinates."""
    import json

    import_run, raw_root, output = import_run.resolve(), raw_root.resolve(), output.resolve()
    if not (import_run / "run.json").is_file() or not raw_root.is_dir():
        raise ValueError("A completed import run and source root are required")
    if output.exists() or "gpr_raw_data" in (p.casefold() for p in output.parts):
        raise ValueError("GPS audit output must be a new path outside GPR_raw_data")
    source = json.loads((import_run / "run.json").read_text(encoding="utf-8"))
    if source.get("status") != "complete":
        raise ValueError("GPS audit requires a completed import run")
    report = {
        "schema_version": "0.1",
        "status": "running",
        "tool_version": __version__,
        "operation": "gps_audit_collection",
        "started_utc": datetime.now(UTC).isoformat(),
        "import_run": str(import_run),
        "import_manifest_sha256": sha256_file(import_run / "run.json"),
        "raw_root": str(raw_root),
        "code_sha256": code_hash(),
        "profiles": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    for number, item in enumerate(source["profiles"], 1):
        if progress:
            progress(f"[{number}/{len(source['profiles'])}] {item['profile_id']}")
        store = (import_run / item["store"]).resolve()
        if not store.is_relative_to(import_run):
            raise ValueError("Import manifest store escapes the run directory")
        gps_key = next(
            (key for key in item["source_checksums"] if key.casefold().endswith(".gps")),
            None,
        )
        if gps_key is None:
            report["profiles"].append({"profile_id": item["profile_id"], "status": "missing_gps"})
            continue
        gps_path = (raw_root / gps_key).resolve()
        if not gps_path.is_relative_to(raw_root):
            raise ValueError("Import manifest GPS path escapes the source root")
        try:
            raw = load_profile(store)
            located, qc = locate_profile(raw, gps_path)
            report["profiles"].append(
                {"profile_id": item["profile_id"], "status": "parsed", "qc": qc}
            )
            raw.close()
            located.close()
        except (OSError, ValueError) as exc:
            report["profiles"].append(
                {
                    "profile_id": item["profile_id"],
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    report["finished_utc"] = datetime.now(UTC).isoformat()
    report["summary"] = {
        "profiles": len(report["profiles"]),
        "parsed": sum(item["status"] == "parsed" for item in report["profiles"]),
        "missing_gps": sum(item["status"] == "missing_gps" for item in report["profiles"]),
        "failed": sum(item["status"] == "failed" for item in report["profiles"]),
        **{
            key: sum(item.get("qc", {}).get(key, 0) for item in report["profiles"])
            for key in (
                "blocks",
                "measured",
                "interpolated",
                "missing",
                "rejected",
                "invalid_sentences",
                "unusable_fix_blocks",
                "duplicate_markers",
            )
        },
    }
    report["status"] = "failed" if report["summary"]["failed"] else "complete"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report
