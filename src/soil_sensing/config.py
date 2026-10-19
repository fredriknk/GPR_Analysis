"""Validated survey configuration and explicit assignment metadata."""

import csv
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class SurveyConfig:
    project_id: str
    raw_root: Path
    manifest_csv: Path | None = None
    assignment_status: str = "unreviewed"
    schema_version: str = "0.1"

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", self.project_id
        ):
            raise ValueError("project_id must contain letters, digits, underscores or hyphens")
        if not isinstance(self.raw_root, Path):
            raise ValueError("raw_root must be a Path")
        if self.manifest_csv is not None and not isinstance(self.manifest_csv, Path):
            raise ValueError("manifest_csv must be a Path or None")
        if not isinstance(self.assignment_status, str) or self.assignment_status not in {
            "unreviewed",
            "reviewed",
        }:
            raise ValueError("assignment_status must be unreviewed or reviewed")
        if self.schema_version != "0.1":
            raise ValueError("Unsupported survey configuration schema_version")

    def resolved(self) -> dict:
        return {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self).items()}


@dataclass(frozen=True)
class Assignment:
    profile_id: str
    survey_id: str | None
    plot_id: str | None
    include: bool | None
    source_crs: str | None
    vertical_datum: str | None
    assignment_evidence: str | None
    reviewer: str | None


def validate_profile_id(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError(f"Invalid relative profile ID: {value!r}")
    if any(c in value for c in ':*?"<>|'):
        raise ValueError(f"Invalid profile ID: {value!r}")
    return value


def load_config(path: Path) -> SurveyConfig:
    path = path.resolve()
    with path.open("rb") as stream:
        values = tomllib.load(stream)
    unknown = set(values) - set(SurveyConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Unknown survey configuration fields: {sorted(unknown)}")
    for key in ("project_id", "raw_root"):
        if not isinstance(values.get(key), str) or not values[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", values["project_id"]):
        raise ValueError("project_id must contain letters, digits, underscores or hyphens")
    if values.get("schema_version", "0.1") != "0.1":
        raise ValueError("Unsupported survey configuration schema_version")
    status = values.get("assignment_status", "unreviewed")
    if not isinstance(status, str) or status not in {"unreviewed", "reviewed"}:
        raise ValueError("assignment_status must be unreviewed or reviewed")
    for key in ("raw_root", "manifest_csv"):
        if key in values:
            if not isinstance(values[key], str) or not values[key].strip():
                raise ValueError(f"{key} must be a nonempty path string")
            values[key] = (path.parent / values[key]).resolve()
    if not values["raw_root"].is_dir():
        raise ValueError(f"Source directory does not exist: {values['raw_root']}")
    return SurveyConfig(**values)


def load_assignments(path: Path) -> dict[str, Assignment]:
    assignments = {}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = set(Assignment.__dataclass_fields__)
        if set(reader.fieldnames or []) != required:
            raise ValueError(f"Assignment columns must be: {', '.join(sorted(required))}")
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Assignment line {line}: malformed CSV row")
            row = {k: v.strip() for k, v in row.items()}
            profile_id = validate_profile_id(row["profile_id"])
            key = profile_id.casefold()
            if key in assignments:
                raise ValueError(f"Duplicate assignment: {profile_id}")
            if row["include"].lower() not in {"true", "false", ""}:
                raise ValueError(f"Invalid include value on assignment line {line}")
            include = {"true": True, "false": False, "": None}[row["include"].lower()]
            if include and not all(
                row[k] for k in ("survey_id", "assignment_evidence", "reviewer")
            ):
                raise ValueError(
                    f"Included assignment {profile_id} needs survey, evidence and reviewer"
                )
            if row["plot_id"] and not row["survey_id"]:
                raise ValueError(f"Plot assignment {profile_id} requires a survey_id")
            values = {k: v or None for k, v in row.items()}
            values.update(profile_id=profile_id, include=include)
            assignments[key] = Assignment(**values)
    return assignments
