"""Extensible observation adapters; first supported assay is manual soil texture."""

import csv
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from soil_sensing.inventory import sha256_file


@dataclass(frozen=True)
class Observation:
    measurement_id: str
    sensor_type: str
    sample_id: str
    plot_id: str
    survey_id: str
    acquired_at_utc: datetime
    x_m: float
    y_m: float
    crs: str
    horizontal_uncertainty_m: float | None
    depth_top_m: float
    depth_bottom_m: float
    property: str
    value: float
    unit: str
    method: str
    sand_silt_cutoff_um: float
    silt_clay_cutoff_um: float
    closure_tolerance_percent: float
    uncertainty: float | None
    uncertainty_kind: str | None
    lab_batch_id: str | None
    quality_flag: int
    source_sha256: str


_ADAPTERS: dict[str, Callable[[Path], list[Observation]]] = {}


def register_adapter(name: str, reader: Callable[[Path], list[Observation]]) -> None:
    if not name or name in _ADAPTERS or not callable(reader):
        raise ValueError("Adapter name must be unique and reader callable")
    _ADAPTERS[name] = reader


def read_observations(adapter: str, path: Path) -> list[Observation]:
    if adapter not in _ADAPTERS:
        raise ValueError(f"Unknown observation adapter: {adapter}")
    return _ADAPTERS[adapter](path)


def _number(value: str, name: str, *, optional: bool = False) -> float | None:
    if not value.strip() and optional:
        return None
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Nonfinite {name}")
    return result


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid acquisition time: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Acquisition time must explicitly be UTC")
    return parsed


def read_manual_texture_csv(path: Path) -> list[Observation]:
    """Read sand/silt/clay triplets; retain original units without renormalizing."""
    from pyproj import CRS

    source_hash = sha256_file(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        expected = {
            "measurement_id",
            "sample_id",
            "plot_id",
            "survey_id",
            "acquired_at_utc",
            "x",
            "y",
            "crs",
            "horizontal_uncertainty_m",
            "depth_top_m",
            "depth_bottom_m",
            "property",
            "value",
            "unit",
            "method",
            "sand_silt_cutoff_um",
            "silt_clay_cutoff_um",
            "closure_tolerance_percent",
            "uncertainty",
            "uncertainty_kind",
            "lab_batch_id",
            "quality_flag",
        }
        if set(reader.fieldnames or []) != expected:
            raise ValueError("Manual texture CSV columns differ from the template")
        records = []
        seen = set()
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row {line}")
            required = (
                "measurement_id",
                "sample_id",
                "plot_id",
                "survey_id",
                "acquired_at_utc",
                "x",
                "y",
                "crs",
                "depth_top_m",
                "depth_bottom_m",
                "property",
                "value",
                "unit",
                "method",
                "sand_silt_cutoff_um",
                "silt_clay_cutoff_um",
                "closure_tolerance_percent",
            )
            if any(not row[key].strip() for key in required):
                raise ValueError(f"Missing required texture field at row {line}")
            crs = CRS.from_user_input(row["crs"])
            if not crs.is_projected or crs.axis_info[0].unit_name.lower() not in ("metre", "meter"):
                raise ValueError("Manual sample CRS must be projected in metres")
            if row["measurement_id"] in seen:
                raise ValueError("Duplicate measurement_id")
            seen.add(row["measurement_id"])
            if row["property"] not in ("sand_fraction", "silt_fraction", "clay_fraction"):
                raise ValueError("Manual texture adapter accepts only sand/silt/clay fractions")
            if row["unit"] not in ("percent", "fraction"):
                raise ValueError("Texture unit must be exactly percent or fraction")
            value = _number(row["value"], "value")
            limit = 100 if row["unit"] == "percent" else 1
            if not 0 <= value <= limit:
                raise ValueError("Texture fractions must be within declared unit bounds")
            top = _number(row["depth_top_m"], "depth_top_m")
            bottom = _number(row["depth_bottom_m"], "depth_bottom_m")
            if not 0 <= top < bottom:
                raise ValueError("Texture depth interval must be positive-down and nonempty")
            sand_cut = _number(row["sand_silt_cutoff_um"], "sand_silt_cutoff_um")
            clay_cut = _number(row["silt_clay_cutoff_um"], "silt_clay_cutoff_um")
            if not 0 < clay_cut < sand_cut:
                raise ValueError("Particle-size cutoffs must be positive and ordered")
            tolerance = _number(row["closure_tolerance_percent"], "closure_tolerance_percent")
            if not 0 <= tolerance <= 10:
                raise ValueError("Closure tolerance must be 0–10 percent")
            horizontal = _number(
                row["horizontal_uncertainty_m"], "horizontal_uncertainty_m", optional=True
            )
            uncertainty = _number(row["uncertainty"], "uncertainty", optional=True)
            if (
                horizontal is not None
                and horizontal < 0
                or uncertainty is not None
                and uncertainty < 0
            ):
                raise ValueError("Uncertainties must be nonnegative")
            if (uncertainty is None) != (not row["uncertainty_kind"].strip()):
                raise ValueError("Uncertainty value and kind must be supplied together")
            if row["uncertainty_kind"] and row["uncertainty_kind"] not in (
                "standard_deviation",
                "standard_error",
                "bound",
            ):
                raise ValueError("Unknown uncertainty kind")
            quality = row["quality_flag"].strip()
            if quality and (not quality.isdigit() or int(quality) < 0):
                raise ValueError("quality_flag must be a nonnegative integer")
            records.append(
                Observation(
                    measurement_id=row["measurement_id"],
                    sensor_type="manual_texture",
                    sample_id=row["sample_id"],
                    plot_id=row["plot_id"],
                    survey_id=row["survey_id"],
                    acquired_at_utc=_timestamp(row["acquired_at_utc"]),
                    x_m=_number(row["x"], "x"),
                    y_m=_number(row["y"], "y"),
                    crs=crs.to_string(),
                    horizontal_uncertainty_m=horizontal,
                    depth_top_m=top,
                    depth_bottom_m=bottom,
                    property=row["property"],
                    value=value,
                    unit=row["unit"],
                    method=row["method"],
                    sand_silt_cutoff_um=sand_cut,
                    silt_clay_cutoff_um=clay_cut,
                    closure_tolerance_percent=tolerance,
                    uncertainty=uncertainty,
                    uncertainty_kind=row["uncertainty_kind"] or None,
                    lab_batch_id=row["lab_batch_id"] or None,
                    quality_flag=int(quality or 0),
                    source_sha256=source_hash,
                )
            )
    if not records:
        raise ValueError("No manual texture observations")
    groups: dict[tuple[str, str], list[Observation]] = {}
    for record in records:
        groups.setdefault((record.survey_id, record.sample_id), []).append(record)
    for identity, group in groups.items():
        if len(group) != 3 or {item.property for item in group} != {
            "sand_fraction",
            "silt_fraction",
            "clay_fraction",
        }:
            raise ValueError(f"Texture sample {identity} requires one sand, silt and clay assay")
        comparable = (
            "plot_id",
            "acquired_at_utc",
            "x_m",
            "y_m",
            "crs",
            "depth_top_m",
            "depth_bottom_m",
            "unit",
            "method",
            "sand_silt_cutoff_um",
            "silt_clay_cutoff_um",
            "closure_tolerance_percent",
        )
        if any(
            getattr(item, key) != getattr(group[0], key) for item in group[1:] for key in comparable
        ):
            raise ValueError(f"Inconsistent support/method/cutoffs in texture sample {identity}")
        total = sum(item.value for item in group) * (100 if group[0].unit == "fraction" else 1)
        if abs(total - 100) > group[0].closure_tolerance_percent:
            raise ValueError(f"Texture fractions fail declared closure tolerance: {identity}")
    if source_hash != sha256_file(path):
        raise ValueError("Texture source changed while reading")
    return records


register_adapter("manual_texture_csv", read_manual_texture_csv)
