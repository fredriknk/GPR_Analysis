"""Seed-constrained dynamic programming on full native B-scans."""

from dataclasses import asdict, dataclass

import numpy as np

from soil_sensing.processing.attributes import AttributeOptions, native_attributes
from soil_sensing.volumes.contracts import digest


@dataclass(frozen=True)
class TrackingOptions:
    search_ns: float = 5.0
    max_slope_ns_per_m: float = 3.0
    slope_penalty: float = 0.05
    curvature_penalty: float = 0.1
    waveform_weight: float = 0.3
    minimum_strength: float = 40.0
    max_track_gap_m: float = 6.0
    polarity: str = "positive"

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "polarity":
                if value not in ("positive", "negative", "envelope"):
                    raise ValueError("Unknown tracking polarity")
            elif (
                isinstance(value, bool)
                or not np.isfinite(value)
                or value < (0 if "penalty" in name else 1e-12)
            ):
                raise ValueError(f"Invalid {name}")


def _path(candidates, costs, distance, axis, options):
    """Second-order state (previous,current); hard slope bounds and curvature cost."""
    if len(candidates) == 1:
        return [candidates[0][np.argmin(costs[0])]]
    delta = axis[candidates[1]][None, :] - axis[candidates[0]][:, None]
    slope = delta / (distance[1] - distance[0])
    score = costs[0][:, None] + costs[1][None, :] + options.slope_penalty * slope**2
    score[np.abs(slope) > options.max_slope_ns_per_m] = np.inf
    parents = []
    for t in range(2, len(candidates)):
        previous_slope = (axis[candidates[t - 1]][None, :] - axis[candidates[t - 2]][:, None]) / (
            distance[t - 1] - distance[t - 2]
        )
        new_slope = (axis[candidates[t]][None, :] - axis[candidates[t - 1]][:, None]) / (
            distance[t] - distance[t - 1]
        )
        total = (
            score[:, :, None]
            + options.curvature_penalty * (previous_slope[:, :, None] - new_slope[None, :, :]) ** 2
        )
        parent = np.argmin(total, axis=0)
        score = np.min(total, axis=0) + costs[t][None, :] + options.slope_penalty * new_slope**2
        score[np.abs(new_slope) > options.max_slope_ns_per_m] = np.inf
        parents.append(parent)
    if not np.isfinite(score).any():
        return []
    a, b = np.unravel_index(np.argmin(score), score.shape)
    indices = [a, b]
    for parent in reversed(parents):
        indices.insert(0, int(parent[indices[0], indices[1]]))
    return [int(candidates[i][j]) for i, j in enumerate(indices)]


def track_horizon(profile, seeds, horizon_id, options=None, *, attribute_options=None):
    """Paths split at absent signal, missing positions and distance gaps.

    Segments without a seed remain unknown. Proposed picks never acquire an
    accepted status automatically; supplied seed review states are preserved.
    """
    options = options or TrackingOptions()
    attribute_options = attribute_options or AttributeOptions()
    if not seeds:
        return []
    keys = {(s["source_id"], s["profile_id"], s["horizon_id"]) for s in seeds}
    if keys != {(profile.attrs["source_id"], profile.attrs["profile_id"], horizon_id)}:
        raise ValueError("Tracking seeds must share source/profile/horizon")
    if len({s["time_basis"] for s in seeds}) != 1:
        raise ValueError("Cannot mix recording/corrected seeds")
    trace_ids = list(map(str, profile.trace_id.values))
    seed_by_trace = {trace_ids.index(s["trace_id"]): s for s in seeds}
    if len(seed_by_trace) != len(seeds):
        raise ValueError("Duplicate seed trace")
    axis = (
        profile.twt_ns.values
        if seeds[0]["time_basis"] == "recording"
        else profile.corrected_twt_ns.values
    )
    distance = profile.distance_m.values
    attrs = native_attributes(profile, attribute_options)
    strength = (
        attrs["envelope"]
        if options.polarity == "envelope"
        else attrs["signed_amplitude"] * (1 if options.polarity == "positive" else -1)
    )
    strength = np.where(np.isfinite(attrs["envelope"]), strength, np.nan)
    seed_indices = sorted(seed_by_trace)
    wave_half = 2
    templates = []
    for seed_ in seeds:
        trace = trace_ids.index(seed_["trace_id"])
        sample = seed_["native_sample"]
        if wave_half <= sample < profile.sizes["sample"] - wave_half:
            wave = profile.amplitude.values[
                trace, sample - wave_half : sample + wave_half + 1
            ].astype(float)
            norm = np.linalg.norm(wave)
            if norm:
                templates.append(wave / norm)
    template = np.mean(templates, axis=0) if templates else None
    expected = np.interp(
        distance,
        distance[seed_indices],
        [seed_by_trace[i]["two_way_time_ns"] for i in seed_indices],
    )
    candidates, costs, segments, segment = [], [], [], []
    for i in range(len(distance)):
        valid = (
            np.isfinite(strength[i])
            & (np.abs(axis - expected[i]) <= options.search_ns)
            & (strength[i] >= options.minimum_strength)
        )
        ids = np.flatnonzero(valid)
        if i in seed_by_trace:
            sample = seed_by_trace[i]["native_sample"]
            ids = np.array([sample]) if valid[sample] else np.array([], int)
        usable = profile.position_status.values[i] in ("measured", "interpolated")
        gap = i > 0 and (
            distance[i] - distance[i - 1] > options.max_track_gap_m
            or distance[i] <= distance[i - 1]
        )
        if gap or not len(ids) or not usable:
            if segment:
                segments.append(segment)
            segment = []
        if len(ids) and usable:
            segment.append(i)
        candidates.append(ids)
        # Local waveform agreement penalizes selecting a different phase/shape.
        local_cost = -strength[i, ids] / max(
            options.minimum_strength,
            np.nanmax(np.abs(strength[i])) if np.isfinite(strength[i]).any() else 1,
        )
        if template is not None:
            for j, sample in enumerate(ids):
                if wave_half <= sample < profile.sizes["sample"] - wave_half:
                    wave = profile.amplitude.values[i, sample - wave_half : sample + wave_half + 1]
                    norm = np.linalg.norm(wave) * np.linalg.norm(template)
                    agreement = float(np.dot(wave, template) / norm) if norm else 0.0
                    local_cost[j] += options.waveform_weight * (1 - agreement)
        costs.append(local_cost)
    if segment:
        segments.append(segment)
    picks = []
    for indices in segments:
        if not set(indices).intersection(seed_by_trace):
            continue
        path = _path(
            [candidates[i] for i in indices],
            [costs[i] for i in indices],
            distance[indices],
            axis,
            options,
        )
        for i, sample in zip(indices, path, strict=bool(path)):
            if i in seed_by_trace:
                picks.append(dict(seed_by_trace[i]))
                continue
            peak_values = np.sort(strength[i, candidates[i]])
            ambiguous = len(peak_values) > 1 and peak_values[-2] > 0.95 * peak_values[-1]
            picks.append(
                dict(
                    pick_id=digest(
                        [
                            profile.attrs["source_id"],
                            profile.attrs["profile_id"],
                            horizon_id,
                            str(profile.trace_id.values[i]),
                        ]
                    )[:24],
                    horizon_id=horizon_id,
                    source_id=profile.attrs["source_id"],
                    profile_id=profile.attrs["profile_id"],
                    trace_id=str(profile.trace_id.values[i]),
                    native_sample=sample,
                    time_basis=seeds[0]["time_basis"],
                    two_way_time_ns=float(axis[sample]),
                    standard_uncertainty_ns=None,
                    status="proposed",
                    reviewer="assisted_tracker",
                    evidence="seed-constrained native-profile path; analyst review required",
                    ambiguous=bool(ambiguous),
                    tracking_options=asdict(options),
                )
            )
    return picks
