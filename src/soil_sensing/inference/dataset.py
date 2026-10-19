"""Audited, unit-aware sample tables and shared-support spatial groups."""

import hashlib
import json
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

TARGETS = ("sand_fraction", "silt_fraction", "clay_fraction")


@dataclass
class TextureDataset:
    rows: list[dict]
    x: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    feature_names: list[str]
    definitions: dict


def spatial_groups(rows, contributors, *, block_size_m, location_tolerance_m):
    """Union same blocks, nearby locations, repeat samples and shared trace support.

    The metric CRS zero is a deterministic partition lattice, not a plot origin.
    Transitive shared support is inseparable even across plots or acquisition dates.
    """
    parents = list(range(len(rows)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(i, j):
        parents[root(j)] = root(i)

    seen = {}
    for i, (row, traces) in enumerate(zip(rows, contributors, strict=True)):
        keys = [
            (
                "block",
                row["crs"],
                int(np.floor(row["x_m"] / block_size_m)),
                int(np.floor(row["y_m"] / block_size_m)),
            ),
            ("sample", row["survey_id"], row["sample_id"]),
            *[("trace", *trace) for trace in traces],
        ]
        for key in keys:
            if key in seen:
                join(i, seen[key])
            seen[key] = i
    for crs in sorted({r["crs"] for r in rows}):
        ids = [i for i, r in enumerate(rows) if r["crs"] == crs]
        xy = [[rows[i]["x_m"], rows[i]["y_m"]] for i in ids]
        for i, j in cKDTree(xy).query_pairs(location_tolerance_m):
            join(ids[i], ids[j])
    members = {}
    for i, row in enumerate(rows):
        members.setdefault(root(i), []).append(json.dumps([row["survey_id"], row["sample_id"]]))
    labels = {
        key: hashlib.sha256("|".join(sorted(values)).encode()).hexdigest()[:16]
        for key, values in members.items()
    }
    return np.array([labels[root(i)] for i in range(len(rows))])


def prepare_dataset(rows, feature_names, *, block_size_m, location_tolerance_m):
    if not rows or not feature_names or len(set(feature_names)) != len(feature_names):
        raise ValueError("Independent texture samples and unique feature_names are required")
    if any(not isinstance(f, str) or not f.startswith("gpr_rms_") for f in feature_names):
        raise ValueError(
            "Initial texture models accept explicitly selected quantitative gpr_rms gates"
        )
    accepted, excluded, definitions, contributors = [], [], {}, []
    seen = set()
    for row in rows:
        key = (row["survey_id"], row["sample_id"])
        if key in seen:
            raise ValueError("Duplicate independent sample identity")
        seen.add(key)
        available = [f for f in feature_names if row.get(f) is not None]
        if not available:
            excluded.append(
                {
                    "sample_id": row["sample_id"],
                    "survey_id": row["survey_id"],
                    "reason": "no_selected_supported_features",
                }
            )
            continue
        lineage = json.loads(row["lineage"])
        traces = set()
        for f in available:
            records = lineage.get(f, [])
            if not records:
                raise ValueError("Training features require source-contributor lineage")
            for r in records:
                if any(
                    not r.get(k)
                    for k in (
                        "trace_id",
                        "source_id",
                        "profile_id",
                        "processing_signature",
                        "time_basis",
                        "velocity_calibration_id",
                        "velocity_calibration_evidence",
                    )
                ):
                    raise ValueError(
                        "Training requires evidenced calibrated feature provenance; "
                        "rerun process/match"
                    )
                definition = {
                    "unit": row[f + "_unit"],
                    "processing_signature": r["processing_signature"],
                    "time_basis": r["time_basis"],
                }
                if f in definitions and definition != definitions[f]:
                    raise ValueError(
                        "Incompatible feature units, time basis or processing/calibration; "
                        "harmonize first"
                    )
                definitions[f] = definition
                traces.add((r["source_id"], r["profile_id"], r["trace_id"]))
        accepted.append(row)
        contributors.append(traces)
    if not accepted:
        raise ValueError("No independently labelled samples have supported calibrated features")
    if len({r["crs"] for r in accepted}) != 1:
        raise ValueError(
            "Spatial validation requires one reviewed metric CRS; harmonize coordinates first"
        )
    if set(feature_names) - definitions.keys():
        raise ValueError("Selected feature has no supported observations")
    target_definitions = {
        (r["method"], r["sand_silt_cutoff_um"], r["silt_clay_cutoff_um"]) for r in accepted
    }
    if len(target_definitions) != 1:
        raise ValueError("Texture lab definitions require explicit harmonization")
    x = np.array([[r.get(f, np.nan) for f in feature_names] for r in accepted], dtype=float)
    y = np.array([[r[t] for t in TARGETS] for r in accepted], dtype=float)
    for i, row in enumerate(accepted):
        if row["unit"] == "percent":
            y[i] /= 100
        elif row["unit"] != "fraction":
            raise ValueError("Unknown texture target unit")
    if not np.isfinite(y).all() or np.any(y < 0) or np.any(y > 1):
        raise ValueError("Invalid texture targets")
    groups = spatial_groups(
        accepted, contributors, block_size_m=block_size_m, location_tolerance_m=location_tolerance_m
    )
    return TextureDataset(accepted, x, y, groups, feature_names, definitions), excluded


def purged_training(dataset, train, held_out, buffer_m):
    """Remove shared-support groups and buffered locations without using targets."""
    train, held_out = np.asarray(train, dtype=int), np.asarray(held_out, dtype=int)
    if not len(held_out):
        return train
    held_groups = set(dataset.groups[held_out])
    keep = np.array([dataset.groups[i] not in held_groups for i in train])
    for crs in {dataset.rows[i]["crs"] for i in held_out}:
        target_ids = [i for i in held_out if dataset.rows[i]["crs"] == crs]
        train_positions = [k for k, i in enumerate(train) if dataset.rows[i]["crs"] == crs]
        if not train_positions:
            continue

        def xy(ids):
            return [[dataset.rows[i]["x_m"], dataset.rows[i]["y_m"]] for i in ids]

        distance, _ = cKDTree(xy(target_ids)).query(xy(train[train_positions]))
        keep[train_positions] &= distance > buffer_m
    return train[keep]
