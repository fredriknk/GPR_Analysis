"""Native geometry and acquisition comparisons, with unknown precision preserved."""

import numpy as np
from scipy.spatial import cKDTree
from shapely import LineString


def track_direction(xy, valid_steps):
    """Classify the dominant track axis, independent of travel sign/repeated fixes.

    Only endpoints of accepted, gap-limited steps enter the fit. Near-isotropic
    tracks (principal variance less than twice the other variance) stay unknown.
    This diagnostic grouping does not establish antenna orientation or precision.
    """
    xy, valid_steps = np.asarray(xy, float), np.asarray(valid_steps, bool)
    if xy.ndim != 2 or xy.shape[1] != 2 or valid_steps.shape != (max(0, len(xy) - 1),):
        raise ValueError("Direction requires XY points and one mask per consecutive step")
    if len(xy) < 2:
        return "unknown"
    endpoints = np.r_[valid_steps, False] | np.r_[False, valid_steps]
    points = xy[endpoints & np.isfinite(xy).all(axis=1)]
    if len(points) < 2:
        return "unknown"
    centered = points - points.mean(axis=0)
    variance, vectors = np.linalg.eigh(centered.T @ centered)
    if variance[-1] <= 0 or variance[-1] < 2 * variance[0]:
        return "unknown"
    angle = float(np.degrees(np.arctan2(*vectors[:, -1][::-1])) % 180)
    return "east_west" if angle < 45 or angle >= 135 else "north_south"


def geometry_audit(rows, attributes, acquisition_audit, *, max_gap_m, proximity_m):
    lines = sorted({r["line_id"] for r in rows})
    indices = {
        line: [i for i, r in enumerate(rows) if r["line_id"] == line and r["located"]]
        for line in lines
    }
    distances, comparisons = {line: [] for line in lines}, []
    for left_index, left in enumerate(lines):
        a = indices[left]
        if not a:
            continue
        xy_a = np.array([rows[i]["xy"] for i in a])
        for right in lines[left_index + 1 :]:
            b = indices[right]
            if not b:
                continue
            xy_b = np.array([rows[i]["xy"] for i in b])
            distance, nearest = cKDTree(xy_b).query(xy_a)
            distances[left].append(float(distance.min()))
            distances[right].append(float(distance.min()))
            for i in np.flatnonzero(distance <= proximity_m):
                j = int(nearest[i])
                connection = LineString([xy_a[i], xy_b[j]])
                # This is a native proximity comparison, not co-located truth or
                # a fitted positional error/normalization correction.
                if connection.length > max_gap_m:
                    continue
                va, vb = attributes["envelope"][a[i]], attributes["envelope"][b[j]]
                valid = np.isfinite(va) & np.isfinite(vb)
                comparisons.append(
                    dict(
                        left_line=left,
                        right_line=right,
                        left_trace=rows[a[i]]["trace_id"],
                        right_trace=rows[b[j]]["trace_id"],
                        distance_m=float(distance[i]),
                        compared_native_samples=int(valid.sum()),
                        mean_absolute_envelope_difference=float(
                            np.mean(np.abs(va[valid] - vb[valid]))
                        )
                        if valid.any()
                        else None,
                        attribute_comparisons={
                            name: {
                                "compared_native_samples": int(
                                    (np.isfinite(values[a[i]]) & np.isfinite(values[b[j]])).sum()
                                ),
                                "mean_absolute_difference": float(
                                    np.mean(np.abs(values[a[i]][mask] - values[b[j]][mask]))
                                )
                                if (
                                    mask := np.isfinite(values[a[i]]) & np.isfinite(values[b[j]])
                                ).any()
                                else None,
                            }
                            for name, values in attributes.items()
                        },
                        interpretation=(
                            "nearby traces; unknown positioning error, footprint and orientation"
                        ),
                    )
                )
    return dict(
        schema_version="1.0",
        lines=[
            {
                **audit,
                "nearest_other_line_distance_m": min(distances[audit["line_id"]])
                if distances[audit["line_id"]]
                else None,
            }
            for audit in acquisition_audit
        ],
        native_proximity_comparisons=comparisons,
        proximity_limit_m=proximity_m,
        coordinate_precision_m=None,
        residuals_are_position_accuracy=False,
    )
