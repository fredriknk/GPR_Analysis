"""Native diffraction templates and bounded cross-line evidence graphs."""

from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import least_squares
from shapely import LineString, Point

from soil_sensing.processing.attributes import native_attributes
from soil_sensing.volumes.contracts import digest


@dataclass(frozen=True)
class DrainOptions:
    aperture_m: float = 4.0
    curvature_ns_per_m: float = 10.0
    wavelet_width_ns: float = 1.5
    minimum_correlation: float = 0.55
    minimum_shape_advantage: float = 0.08
    maximum_residual_ns: float = 2.0
    minimum_traces: int = 5
    max_plan_distance_m: float = 16.0
    max_time_difference_ns: float = 4.0
    minimum_crossings: int = 3
    symbolic_radius_m: float = 0.2

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name in ("minimum_traces", "minimum_crossings"):
                if type(value) is not int or value < (3 if name == "minimum_traces" else 2):
                    raise ValueError(f"Invalid {name}")
            elif isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid {name}")
        if self.minimum_correlation > 1 or self.minimum_shape_advantage >= 1:
            raise ValueError("Correlation thresholds must fit their dimensionless range")


def _correlation(a, b):
    a, b = np.asarray(a).ravel(), np.asarray(b).ravel()
    a, b = a - a.mean(), b - b.mean()
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / norm) if norm else 0.0


def detect_diffraction(profile, apex, rows, domain, options=None):
    """Test an analyst apex against curved and flat signed-wavelet templates.

    Recording-time curvature is descriptive. It is never reported as velocity,
    depth, material, diameter or calibrated detection probability.
    """
    options = options or DrainOptions()
    ids = list(map(str, profile.trace_id.values))
    if (
        apex.get("source_id") != profile.attrs["source_id"]
        or apex.get("profile_id") != profile.attrs["profile_id"]
        or apex.get("trace_id") not in ids
        or not apex.get("reviewer")
        or not apex.get("evidence")
    ):
        raise ValueError("Apex requires native source/profile/trace and analyst evidence")
    index = ids.index(apex["trace_id"])
    time = apex.get("two_way_time_ns")
    if (
        isinstance(time, bool)
        or not isinstance(time, (int, float))
        or not np.isfinite(time)
        or time <= 0
    ):
        raise ValueError("Apex time must be finite and positive")
    basis = apex.get("time_basis", "recording")
    if (
        basis not in ("recording", "corrected")
        or basis == "corrected"
        and "corrected_twt_ns" not in profile
    ):
        raise ValueError("Invalid diffraction time basis")
    axis = profile.twt_ns.values if basis == "recording" else profile.corrected_twt_ns.values
    if time < axis[0] or time > axis[-1]:
        raise ValueError("Apex time falls outside native axis")
    result = dict(
        detection_id=digest(
            [profile.attrs["source_id"], profile.attrs["profile_id"], apex["trace_id"], time]
        )[:24],
        source_id=profile.attrs["source_id"],
        profile_id=profile.attrs["profile_id"],
        trace_id=apex["trace_id"],
        native_sample=int(np.argmin(np.abs(axis - time))),
        time_basis=basis,
        two_way_time_ns=float(time),
        xy=rows[index]["xy"],
        direction_group=rows[index]["direction_group"],
        status="proposed",
        analyst=apex["reviewer"],
        evidence=apex["evidence"],
        accepted_detection=False,
        rejection_reason=None,
        score=None,
        score_components={},
        depth_m=None,
        standard_uncertainty_ns=None,
        template_options=asdict(options),
    )
    distance = profile.distance_m.values
    selected = np.flatnonzero(np.abs(distance - distance[index]) <= options.aperture_m)
    if (
        len(selected) < options.minimum_traces
        or distance[index] - distance[selected[0]] < 0.8 * options.aperture_m
        or distance[selected[-1]] - distance[index] < 0.8 * options.aperture_m
    ):
        result["rejection_reason"] = "truncated_or_insufficient_aperture"
        return result
    if not all(rows[i]["located"] for i in selected) or not domain.covers(
        LineString([rows[i]["xy"] for i in selected])
    ):
        result["rejection_reason"] = "unsupported_native_track"
        return result
    # The template aperture must have both-sided continuous acquired support.
    if np.any(np.diff(selected) != 1) or np.any(np.diff(distance[selected]) <= 0):
        result["rejection_reason"] = "discontinuous_aperture"
        return result
    signal = native_attributes(profile)["signed_amplitude"][selected]
    envelope = native_attributes(profile)["envelope"][selected]
    x = distance[selected] - distance[index]
    predicted = np.sqrt(time**2 + (options.curvature_ns_per_m * x) ** 2)
    if (
        predicted.max() + 3 * options.wavelet_width_ns >= axis[-1]
        or time - 3 * options.wavelet_width_ns <= axis[0]
        or not np.isfinite(envelope).any()
    ):
        result["rejection_reason"] = "truncated_vertical_template"
        return result
    gate = (axis >= time - 3 * options.wavelet_width_ns) & (
        axis <= predicted.max() + 3 * options.wavelet_width_ns
    )
    if not np.isfinite(signal[:, gate]).all() or not np.isfinite(envelope[:, gate]).all():
        result["rejection_reason"] = "contaminated_aperture"
        return result

    def wave(centre):
        a = (axis[gate][None, :] - np.asarray(centre)[:, None]) / options.wavelet_width_ns
        return (1 - 2 * a**2) * np.exp(-(a**2))

    observed = signal[:, gate]
    peak_times = []
    for row, guess in zip(envelope, predicted, strict=True):
        local = np.flatnonzero(np.abs(axis - guess) <= 2 * options.wavelet_width_ns)
        peak_times.append(axis[local[np.nanargmax(row[local])]])
    peak_times = np.array(peak_times)
    fit = least_squares(
        lambda params: np.sqrt(params[0] ** 2 + (params[1] * x) ** 2) - peak_times,
        [time, options.curvature_ns_per_m],
        bounds=(
            [max(axis[0], time - options.wavelet_width_ns), 0.5 * options.curvature_ns_per_m],
            [time + options.wavelet_width_ns, 1.5 * options.curvature_ns_per_m],
        ),
        loss="soft_l1",
    )
    residual = float(np.sqrt(np.mean(fit.fun**2)))
    fitted = np.sqrt(fit.x[0] ** 2 + (fit.x[1] * x) ** 2)
    correlation = _correlation(observed, wave(fitted))
    flat = _correlation(observed, wave(np.full(len(x), fit.x[0])))
    advantage = correlation - flat
    accepted = (
        correlation >= options.minimum_correlation
        and advantage >= options.minimum_shape_advantage
        and residual <= options.maximum_residual_ns
    )
    result.update(
        accepted_detection=bool(accepted),
        rejection_reason=None if accepted else "weak_or_non_diffraction_evidence",
        score=float(np.clip(correlation, 0, 1) * np.clip(advantage, 0, 1) / (1 + residual)),
        score_components=dict(
            template_correlation=correlation,
            flat_template_correlation=flat,
            curved_shape_advantage=advantage,
            fit_rmse_ns=residual,
            independent_trace_count=len(selected),
            fitted_apex_time_ns=float(fit.x[0]),
            fitted_curvature_ns_per_m=float(fit.x[1]),
            aperture_fraction=1.0,
        ),
        native_contributors=[
            dict(
                source_id=profile.attrs["source_id"],
                profile_id=profile.attrs["profile_id"],
                trace_id=ids[i],
            )
            for i in selected
        ],
    )
    return result


def associate_detections(detections, domain, partition_id, selection_hash, options=None):
    options = options or DrainOptions()
    valid = [d for d in detections if d["accepted_detection"]]
    if len({d["time_basis"] for d in valid}) > 1:
        raise ValueError("Cannot associate different vertical bases")
    edges, graph = [], {i: set() for i in range(len(valid))}
    for i, left in enumerate(valid):
        for j in range(i + 1, len(valid)):
            right = valid[j]
            if (left["source_id"], left["profile_id"]) == (
                right["source_id"],
                right["profile_id"],
            ) or left["direction_group"] != right["direction_group"]:
                continue
            distance = float(np.linalg.norm(np.array(left["xy"]) - right["xy"]))
            lag = abs(left["two_way_time_ns"] - right["two_way_time_ns"])
            geom = Point(left["xy"]) if distance == 0 else LineString([left["xy"], right["xy"]])
            if (
                distance > options.max_plan_distance_m
                or lag > options.max_time_difference_ns
                or not domain.covers(geom)
            ):
                continue
            edges.append(
                dict(
                    left=left["detection_id"],
                    right=right["detection_id"],
                    distance_m=distance,
                    time_difference_ns=lag,
                    status="alternative_association",
                )
            )
            graph[i].add(j)
            graph[j].add(i)
    unseen, candidates = set(graph), []
    while unseen:
        component, frontier = set(), [min(unseen)]
        while frontier:
            i = frontier.pop()
            if i in component:
                continue
            component.add(i)
            frontier.extend(graph[i] - component)
        unseen -= component
        members = [valid[i] for i in sorted(component)]
        count = len({(d["source_id"], d["profile_id"]) for d in members})
        linked = count >= options.minimum_crossings
        candidates.append(
            dict(
                candidate_id=digest([partition_id, [d["detection_id"] for d in members]])[:24],
                kind="linked_drainage_candidate" if linked else "point_anomaly",
                detection_ids=[d["detection_id"] for d in members],
                independent_crossings=count,
                status="proposed",
                evidence_score=float(np.mean([d["score"] for d in members])),
                score_is_probability=False,
                symbolic_radius_m=options.symbolic_radius_m,
                diameter_m=None,
                material=None,
                function=None,
                depth_m=None,
                uncertainty=None,
            )
        )
    return dict(
        schema_version="1.0",
        partition_id=partition_id,
        selection_hash=selection_hash,
        detections=detections,
        association_graph=edges,
        candidates=candidates,
        minimum_crossings=options.minimum_crossings,
        unobserved_regions="unknown; no absence claim",
        field_validation=False,
    )


def review_candidates(document, *, parent=None, decisions=None):
    """Review changes are immutable and cannot alter scientific candidate evidence."""
    if parent is not None:
        previous = {k: v for k, v in parent.items() if k != "interpretation_hash"}
        if (
            digest(previous) != parent.get("interpretation_hash")
            or parent.get("selection_hash") != document["selection_hash"]
        ):
            raise ValueError("Drain review parent has a stale hash/selection")
        if parent.get("detector_signature") != document.get("detector_signature"):
            raise ValueError("Detector settings changed; previous review must be reconsidered")
        prior = {c["candidate_id"]: c for c in parent["candidates"]}
        for candidate in document["candidates"]:
            old = prior.get(candidate["candidate_id"])
            if old:
                for name in ("status", "reviewer", "review_evidence"):
                    if name in old:
                        candidate[name] = old[name]
    document.update(
        revision=1 if parent is None else parent["revision"] + 1,
        parent_hash=None if parent is None else parent["interpretation_hash"],
    )
    if decisions is not None:
        if (
            decisions.get("schema_version") != "1.0"
            or decisions.get("kind") != "drainage_review"
            or decisions.get("selection_hash") != document["selection_hash"]
            or decisions.get("parent_hash") != document["parent_hash"]
        ):
            raise ValueError("Drain review decisions have a stale selection/parent")
        seen = set()
        candidates = {c["candidate_id"]: c for c in document["candidates"]}
        for decision in decisions.get("decisions", []):
            identifier = decision.get("candidate_id")
            if (
                identifier not in candidates
                or identifier in seen
                or decision.get("status") not in ("accepted", "proposed", "rejected")
                or not decision.get("reviewer")
                or not decision.get("evidence")
            ):
                raise ValueError(
                    "Drain review requires unique existing candidates, "
                    "status, reviewer and evidence"
                )
            seen.add(identifier)
            candidates[identifier].update(
                status=decision["status"],
                reviewer=decision["reviewer"],
                review_evidence=decision["evidence"],
            )
    document["interpretation_hash"] = digest(document)
    return document
