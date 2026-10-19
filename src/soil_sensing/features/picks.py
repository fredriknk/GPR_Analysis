"""Reviewed reflector-pick table validation; no automatic horizon interpretation."""

import csv
import math
from pathlib import Path

import xarray as xr

from soil_sensing.profile import validate_profile


def read_reflector_picks(path: Path, profile: xr.Dataset) -> list[dict]:
    validate_profile(profile)
    expected = {
        "pick_id",
        "trace_id",
        "horizon_id",
        "time_basis",
        "two_way_time_ns",
        "standard_uncertainty_ns",
        "reviewer",
        "evidence",
        "quality_flag",
    }
    trace_ids = set(map(str, profile.trace_id.values))
    seen_ids, seen_horizons = set(), set()
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if set(reader.fieldnames or []) != expected:
            raise ValueError("Reflector pick CSV columns differ from the template")
        picks = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed reflector pick row")
            if not all(row[k].strip() for k in expected):
                raise ValueError("Reflector picks require every field")
            if row["trace_id"] not in trace_ids:
                raise ValueError("Reflector pick trace_id does not belong to this profile")
            if row["pick_id"] in seen_ids:
                raise ValueError("Duplicate reflector pick_id")
            identity = (row["trace_id"], row["horizon_id"])
            if identity in seen_horizons:
                raise ValueError("Duplicate horizon pick on a trace")
            seen_ids.add(row["pick_id"])
            seen_horizons.add(identity)
            basis = row["time_basis"]
            if basis not in ("recording", "corrected"):
                raise ValueError("Pick time_basis must be recording or corrected")
            if basis == "corrected" and "corrected_twt_ns" not in profile:
                raise ValueError("Corrected picks require reviewed time zero")
            time = float(row["two_way_time_ns"])
            uncertainty = float(row["standard_uncertainty_ns"])
            axis = (
                profile.corrected_twt_ns.values if basis == "corrected" else profile.twt_ns.values
            )
            if not math.isfinite(time) or not axis.min() <= time <= axis.max():
                raise ValueError("Reflector pick falls outside the profile time axis")
            if not math.isfinite(uncertainty) or uncertainty < 0:
                raise ValueError("Pick uncertainty must be nonnegative and finite")
            quality = row["quality_flag"]
            if not quality.isdigit():
                raise ValueError("Pick quality_flag must be a nonnegative integer")
            picks.append(
                {
                    **row,
                    "two_way_time_ns": time,
                    "standard_uncertainty_ns": uncertainty,
                    "quality_flag": int(quality),
                }
            )
    return picks
