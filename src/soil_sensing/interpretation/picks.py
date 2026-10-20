"""Namespaced nullable-uncertainty picks and immutable review revisions."""

import numpy as np

from soil_sensing.volumes.contracts import digest


def validate_interpretation(document, profiles, selection_hash):
    if document.get("schema_version") != "1.0" or document.get("selection_hash") != selection_hash:
        raise ValueError("Interpretation schema/selection mismatch")
    if type(document.get("revision")) is not int or document["revision"] < 1:
        raise ValueError("Interpretation revision must be positive")
    if document["revision"] > 1 and not document.get("parent_hash"):
        raise ValueError("Review revisions require an immutable parent hash")
    value = {k: v for k, v in document.items() if k != "interpretation_hash"}
    if digest(value) != document.get("interpretation_hash"):
        raise ValueError("Interpretation hash mismatch")
    available = {(p.attrs["source_id"], p.attrs["profile_id"]): p for p in profiles}
    identifiers, locations = set(), set()
    for pick in document.get("picks", []):
        required = {
            "pick_id",
            "horizon_id",
            "source_id",
            "profile_id",
            "trace_id",
            "native_sample",
            "time_basis",
            "two_way_time_ns",
            "standard_uncertainty_ns",
            "status",
            "reviewer",
            "evidence",
        }
        if required - pick.keys() or not all(
            isinstance(pick[k], str) and pick[k]
            for k in (
                "pick_id",
                "horizon_id",
                "source_id",
                "profile_id",
                "trace_id",
                "reviewer",
                "evidence",
            )
        ):
            raise ValueError("Incomplete namespaced pick")
        key = (pick["source_id"], pick["profile_id"])
        if key not in available:
            raise ValueError("Pick source/profile is not in the reconstruction")
        profile = available[key]
        traces = list(map(str, profile.trace_id.values))
        if pick["trace_id"] not in traces:
            raise ValueError("Pick trace is not in its source/profile")
        sample = pick["native_sample"]
        if type(sample) is not int or not 0 <= sample < profile.sizes["sample"]:
            raise ValueError("Pick native_sample out of range")
        basis = pick["time_basis"]
        if (
            basis not in ("recording", "corrected")
            or basis == "corrected"
            and (
                "corrected_twt_ns" not in profile
                or not profile.attrs.get("processing_options", {}).get("time_zero_evidence")
            )
        ):
            raise ValueError("Invalid/uncalibrated pick time basis")
        axis = profile.twt_ns.values if basis == "recording" else profile.corrected_twt_ns.values
        time = pick["two_way_time_ns"]
        if (
            isinstance(time, bool)
            or not isinstance(time, (int, float))
            or not np.isfinite(time)
            or not axis.min() <= time <= axis.max()
            or abs(time - axis[sample]) > profile.attrs["sample_interval_ns"] / 2 + 1e-10
        ):
            raise ValueError("Pick time and native sample disagree")
        uncertainty = pick["standard_uncertainty_ns"]
        if uncertainty is not None and (
            isinstance(uncertainty, bool)
            or not isinstance(uncertainty, (int, float))
            or not np.isfinite(uncertainty)
            or uncertainty < 0
        ):
            raise ValueError("Unknown uncertainty must be null, otherwise finite and nonnegative")
        if pick["status"] not in ("proposed", "accepted", "rejected"):
            raise ValueError("Unknown review status")
        identity = (*key, pick["trace_id"], pick["horizon_id"])
        if pick["pick_id"] in identifiers or identity in locations:
            raise ValueError("Duplicate pick ID or namespaced horizon/trace")
        identifiers.add(pick["pick_id"])
        locations.add(identity)


def revision(picks, selection_hash, *, parent=None):
    document = dict(
        schema_version="1.0",
        selection_hash=selection_hash,
        revision=1 if parent is None else parent["revision"] + 1,
        parent_hash=None if parent is None else parent["interpretation_hash"],
        picks=picks,
    )
    return {**document, "interpretation_hash": digest(document)}
