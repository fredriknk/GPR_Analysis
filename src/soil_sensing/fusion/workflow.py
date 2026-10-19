"""Reproducible manual assay validation and support matching runs."""

import csv
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from soil_sensing import __version__
from soil_sensing.fusion.matching import match_texture_to_gpr
from soil_sensing.fusion.observations import read_observations
from soil_sensing.ingest import code_hash, write_json
from soil_sensing.inventory import sha256_file


def _new_run(output: Path, inputs: list[Path]) -> Path:
    output = output.resolve()
    if "gpr_raw_data" in (part.casefold() for part in output.parts):
        raise ValueError("Fusion output cannot be written in GPR_raw_data")
    if output.exists() or any(output.is_relative_to(p.resolve()) for p in inputs if p.is_dir()):
        raise ValueError("Fusion output must be a new directory outside inputs")
    output.mkdir(parents=True, exist_ok=False)
    return output


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("Cannot write an empty output table")
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def texture_run(texture_csv: Path, output: Path, *, adapter: str = "manual_texture_csv") -> dict:
    observations = read_observations(adapter, texture_csv)
    output = _new_run(output, [texture_csv])
    rows = []
    for item in observations:
        row = asdict(item)
        row["acquired_at_utc"] = item.acquired_at_utc.isoformat()
        rows.append(row)
    _csv(output / "observations.csv", rows)
    manifest = {
        "schema_version": "0.1",
        "status": "complete",
        "operation": "validate_texture",
        "tool_version": __version__,
        "finished_utc": datetime.now(UTC).isoformat(),
        "adapter": adapter,
        "source_path": str(texture_csv.resolve()),
        "source_sha256": sha256_file(texture_csv),
        "code_sha256": code_hash(),
        "measurement_count": len(observations),
        "sample_count": len({(o.survey_id, o.sample_id) for o in observations}),
        "observations_sha256": sha256_file(output / "observations.csv"),
    }
    write_json(output / "run.json", manifest)
    return manifest


def match_run(
    texture_csv: Path,
    feature_csv: Path,
    output: Path,
    *,
    radius_m: float,
    max_lag_hours: float,
    minimum_depth_overlap: float = 0.25,
) -> dict:
    observations = read_observations("manual_texture_csv", texture_csv)
    with feature_csv.open(newline="", encoding="utf-8") as stream:
        features = list(csv.DictReader(stream))
    if not features:
        raise ValueError("No GPR feature rows")
    rows, audit = match_texture_to_gpr(
        observations,
        features,
        radius_m=radius_m,
        max_lag_hours=max_lag_hours,
        minimum_depth_overlap=minimum_depth_overlap,
    )
    output = _new_run(output, [texture_csv, feature_csv])
    _csv(output / "matched_samples.csv", rows)
    _csv(output / "join_audit.csv", audit)
    manifest = {
        "schema_version": "0.1",
        "status": "complete",
        "operation": "match_texture_gpr",
        "tool_version": __version__,
        "finished_utc": datetime.now(UTC).isoformat(),
        "texture_sha256": sha256_file(texture_csv),
        "features_sha256": sha256_file(feature_csv),
        "code_sha256": code_hash(),
        "radius_m": radius_m,
        "max_lag_hours": max_lag_hours,
        "minimum_depth_overlap": minimum_depth_overlap,
        "sample_count": len(rows),
        "candidate_count": len(audit),
        "contributor_count": sum(item["included"] for item in audit),
        "artifacts": {
            "matched_samples.csv": sha256_file(output / "matched_samples.csv"),
            "join_audit.csv": sha256_file(output / "join_audit.csv"),
        },
    }
    write_json(output / "run.json", manifest)
    return manifest
