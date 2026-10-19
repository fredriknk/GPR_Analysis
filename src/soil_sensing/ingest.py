"""Reproducible single-profile or collection ingestion into a new run directory."""

import hashlib
import html
import json
import platform
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import distributions
from pathlib import Path
from urllib.parse import quote

import numpy as np
import xarray as xr

from soil_sensing import __version__
from soil_sensing.config import SurveyConfig, load_assignments, validate_profile_id
from soil_sensing.export.radargram import plot_radargram
from soil_sensing.inventory import build_inventory, sha256_file
from soil_sensing.io.dt1 import read_dt1
from soil_sensing.io.storage import load_profile, save_profile
from soil_sensing.profile import QualityFlag


def tree_hash(root: Path) -> str:
    """Hash ordered relative filenames and content, including Zarr metadata."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def code_hash() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(p for p in root.rglob("*") if p.suffix in (".py", ".html", ".js")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def write_json(path: Path, values: dict) -> None:
    temporary = path.with_suffix(".json.pending")
    temporary.write_text(json.dumps(values, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_index(path: Path, manifest: dict) -> None:
    rows = []
    for item in manifest["profiles"]:
        label = html.escape(item["profile_id"])
        link = quote(item["radargram"], safe="/")
        flags = html.escape(
            ", ".join(f"{k}: {v}" for k, v in item["quality_counts"].items() if v) or "None"
        )
        rows.append(
            f'<tr><td><a href="{link}">{label}</a></td>'
            f"<td>{item['acquisition_date'] or 'Unknown'}</td>"
            f"<td>{item['trace_count']:,}</td><td>{item['samples_per_trace']}</td>"
            f"<td>{item['sample_interval_ns']:g}</td><td>{flags}</td></tr>"
        )
    count = len(manifest["profiles"])
    total = sum(p["trace_count"] for p in manifest["profiles"])
    body = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Raw GPR import review</title><style>
body{font:16px/1.5 system-ui,sans-serif;color:#172b38;background:#f4f7f7;margin:3rem auto;
max-width:1200px;padding:0 24px}h1{font-size:2rem;margin-bottom:.3rem}p{max-width:80ch}
.notice{border-left:4px solid #a56819;padding:16px;background:#fff7e8;margin:24px 0}
table{background:white;border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:12px;border-bottom:1px solid #d9e4e6}th{background:#e3edef}
a{color:#086b77}code{font-size:.9em}footer{margin-top:24px;color:#52616a}
</style><h1>Raw GPR import review</h1>"""
    body += f"<p>{count} profiles · {total:,} traces · soil-sensing {__version__}</p>"
    body += """<div class="notice">Radargrams use recording time in nanoseconds.
Time-zero correction, GPS quality, geographic positions and depth conversion are pending.
No soil texture estimates have been calculated. Vendor metadata and raw trace headers
are retained in each profile store.</div><p>Select a profile to open its radargram.
Each store has been reopened and checked against the imported array.</p>
<table><thead><tr><th>Profile</th><th>Recorded date</th><th>Traces</th><th>Samples</th>
<th>Interval (ns)</th><th>Quality flag counts</th></tr></thead><tbody>"""
    body += "\n".join(rows)
    body += (
        '</tbody></table><footer><a href="run.json">Run manifest and provenance</a></footer></html>'
    )
    path.write_text(body, encoding="utf-8")


def ingest_collection(
    config: SurveyConfig,
    *,
    output: Path,
    profiles: list[str] | None = None,
    progress=None,
) -> dict:
    """Import selected relative DT1 paths, or all profiles when profiles is None."""
    root, output = config.raw_root.resolve(), output.resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Run directory must be outside, and must not contain, the source tree")
    if output.exists():
        raise ValueError(f"Run directory already exists: {output}")
    inventory = build_inventory(root)
    available = {p["dt1_path"].casefold(): p for p in inventory["profiles"]}
    assignments = load_assignments(config.manifest_csv) if config.manifest_csv else {}
    known_ids = {p["profile_id"].casefold() for p in available.values()}
    if set(assignments) - known_ids:
        raise ValueError(
            f"Assignments reference unknown profiles: {sorted(set(assignments) - known_ids)}"
        )
    if profiles is None:
        selected = list(available.values())
        selected = [
            p
            for p in selected
            if not ((a := assignments.get(p["profile_id"].casefold())) and a.include is False)
        ]
    else:
        keys = [validate_profile_id(p).casefold() for p in profiles]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate selected profile")
        if set(keys) - available.keys():
            raise ValueError(f"Unknown DT1 paths: {sorted(set(keys) - available.keys())}")
        selected = [available[k] for k in keys]
    if not selected:
        raise ValueError("No profiles selected for ingestion")
    allowed_issues = {"missing_gps", "missing_acquisition_date"}
    issues = [p for p in selected if set(p["issues"]) - allowed_issues]
    if issues:
        raise ValueError(
            f"Resolve structural inventory issues before ingestion: {issues[0]['dt1_path']}"
        )
    excluded = [
        p
        for p in selected
        if (a := assignments.get(p["profile_id"].casefold())) and a.include is False
    ]
    if excluded:
        raise ValueError(f"Profile explicitly excluded: {excluded[0]['profile_id']}")
    manifest = {
        "schema_version": "0.2",
        "tool_version": __version__,
        "status": "running",
        "started_utc": datetime.now(UTC).isoformat(),
        "configuration": config.resolved(),
        "configuration_sha256": hashlib.sha256(
            json.dumps(config.resolved(), sort_keys=True).encode()
        ).hexdigest(),
        "assignment_sha256": sha256_file(config.manifest_csv) if config.manifest_csv else None,
        "code_sha256": code_hash(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dict(sorted((d.metadata["Name"], d.version) for d in distributions())),
        "warnings": inventory["summary"]["warnings"],
        "orphan_sidecars": inventory["orphan_sidecars"],
        "selected_profiles": [p["profile_id"] for p in selected],
        "profiles": [],
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "run.json", manifest)
    try:
        for i, entry in enumerate(selected, 1):
            if progress:
                progress(f"[{i}/{len(selected)}] {entry['profile_id']}")
            assignment = assignments.get(entry["profile_id"].casefold())
            profile = read_dt1(
                root / entry["dt1_path"],
                root=root,
                source_id=config.project_id,
                assignment=assignment,
            )
            relative = Path("profiles") / entry["profile_id"]
            folder = output / relative
            folder.mkdir(parents=True, exist_ok=False)
            store = folder / "raw.zarr"
            save_profile(profile, store)
            restored = load_profile(store)
            xr.testing.assert_identical(profile, restored)
            plot_radargram(profile, folder / "radargram.png")
            item = {
                "profile_id": entry["profile_id"],
                "survey_id": profile.attrs["survey_id"],
                "plot_id": profile.attrs["plot_id"],
                "assignment": asdict(assignment) if assignment else None,
                "assignment_review_status": config.assignment_status,
                "source_checksums": profile.attrs["source_checksums"],
                "acquisition_date": profile.attrs["original_hd"]["acquisition_date"],
                "trace_count": profile.sizes["trace"],
                "samples_per_trace": profile.sizes["sample"],
                "sample_interval_ns": profile.attrs["sample_interval_ns"],
                "quality_counts": {
                    flag.name: int(np.count_nonzero(profile.quality_flags.values & int(flag)))
                    for flag in QualityFlag
                },
                "store": (relative / "raw.zarr").as_posix(),
                "store_sha256": tree_hash(store),
                "radargram": (relative / "radargram.png").as_posix(),
                "radargram_sha256": sha256_file(folder / "radargram.png"),
                "round_trip_verified": True,
            }
            manifest["profiles"].append(item)
            profile.close()
            restored.close()
        if (
            config.manifest_csv
            and sha256_file(config.manifest_csv) != manifest["assignment_sha256"]
        ):
            raise ValueError("Assignment CSV changed during ingestion")
        _write_index(output / "index.html", manifest)
        manifest.update(
            status="complete",
            finished_utc=datetime.now(UTC).isoformat(),
            report_sha256=sha256_file(output / "index.html"),
        )
    except Exception as exc:
        manifest.update(
            status="failed",
            finished_utc=datetime.now(UTC).isoformat(),
            error=f"{type(exc).__name__}: {exc}",
        )
        write_json(output / "run.json", manifest)
        raise
    write_json(output / "run.json", manifest)
    return manifest
