"""Read-only discovery and structural checks; no survey assignment or GPS interpolation."""

import hashlib
import struct
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from soil_sensing import __version__
from soil_sensing.io.sensors_software import read_header

RAW_SUFFIXES = {".dt1", ".hd", ".gps"}
REFERENCE_SUFFIXES = {".csv", ".grd", ".jpg", ".jpeg", ".kmz", ".gpz", ".voxb"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_inventory(root: Path, *, hash_raw: bool = False) -> dict:
    """Inventory by relative path, matching sidecars case-insensitively.

    Size and endpoint checks assume the observed 128-byte trace header + int16
    payload layout. Passing these checks does not validate every trace or GPS fix.
    Symlinks are rejected so discovery cannot silently leave the requested root.
    """
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Input directory does not exist: {root}")
    paths = sorted(root.rglob("*"), key=lambda p: p.as_posix().casefold())
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in paths):
        raise ValueError("Input tree contains a link/junction; use an explicit source directory")
    paths = [p for p in paths if p.is_file()]
    lookup = {}
    files = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        key = relative.casefold()
        if key in lookup:
            raise ValueError(f"Ambiguous case-insensitive source path: {relative}")
        lookup[key] = path
        suffix = path.suffix.lower()
        role = "unknown"
        if suffix in RAW_SUFFIXES:
            role = "raw_candidate"
        elif suffix in REFERENCE_SUFFIXES:
            role = "vendor_reference_candidate"
        elif suffix == ".gfp":
            role = "survey_metadata_candidate"
        item = {"path": relative, "bytes": path.stat().st_size, "role": role}
        if hash_raw and suffix in RAW_SUFFIXES:
            item["sha256"] = sha256_file(path)
        files.append(item)

    def sibling(path: Path, suffix: str) -> Path | None:
        return lookup.get(path.relative_to(root).with_suffix(suffix).as_posix().casefold())

    profiles = []
    for path in paths:
        if path.suffix.lower() != ".dt1":
            continue
        relative = path.relative_to(root).as_posix()
        hd, gps = sibling(path, ".hd"), sibling(path, ".gps")
        issues = []
        profile = {
            "profile_id": str(Path(relative).with_suffix("")).replace("\\", "/"),
            "dt1_path": relative,
            "hd_path": hd.relative_to(root).as_posix() if hd else None,
            "gps_path": gps.relative_to(root).as_posix() if gps else None,
            "survey_id": None,
            "plot_id": None,
            "issues": issues,
        }
        if gps is None:
            issues.append("missing_gps")
        if hd is None:
            issues.append("missing_header")
        else:
            try:
                header = read_header(hd)
                profile["header"] = asdict(header)
                if header.acquisition_date is None:
                    issues.append("missing_acquisition_date")
                record_bytes = 128 + 2 * header.samples_per_trace
                expected = record_bytes * header.trace_count
                profile["expected_dt1_bytes"] = expected
                if path.stat().st_size != expected:
                    issues.append("dt1_size_mismatch")
                else:
                    with path.open("rb") as stream:
                        for index in (0, header.trace_count - 1):
                            stream.seek(index * record_bytes)
                            trace_no, _, samples = struct.unpack("<3f", stream.read(12))
                            if trace_no != index + 1 or samples != header.samples_per_trace:
                                issues.append("dt1_endpoint_header_mismatch")
                                break
            except (ValueError, UnicodeError, struct.error) as exc:
                issues.append(f"invalid_header_or_layout: {exc}")
        profiles.append(profile)
    orphans = [
        p.relative_to(root).as_posix()
        for p in paths
        if p.suffix.lower() in {".hd", ".gps"} and sibling(p, ".dt1") is None
    ]
    dates = sorted(
        {
            p["header"]["acquisition_date"]
            for p in profiles
            if p.get("header", {}).get("acquisition_date")
        }
    )
    warnings = ["survey_and_plot_assignment_required", "gps_quality_not_evaluated"]
    if len(dates) > 1:
        warnings.append("multiple_acquisition_dates")
    if orphans:
        warnings.append("orphan_sidecars")
    return {
        "schema_version": "0.1",
        "tool_version": __version__,
        "created_utc": datetime.now(UTC).isoformat(),
        "source_root": str(root),
        "hash_scope": "dt1_hd_gps" if hash_raw else "none",
        "validation_scope": "metadata, file sizes, DT1 endpoint headers; GPS not parsed",
        "summary": {
            "file_count": len(files),
            "total_bytes": sum(f["bytes"] for f in files),
            "extension_counts": dict(sorted(Counter(p.suffix.lower() for p in paths).items())),
            "profile_count": len(profiles),
            "trace_count_from_headers": sum(
                p.get("header", {}).get("trace_count", 0) for p in profiles
            ),
            "profiles_with_issues": sum(bool(p["issues"]) for p in profiles),
            "acquisition_dates": dates,
            "warnings": warnings,
        },
        "orphan_sidecars": orphans,
        "profiles": profiles,
        "files": files,
    }
