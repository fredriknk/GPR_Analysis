"""Native geometry and acquisition comparisons, with unknown precision preserved."""

import numpy as np
from scipy.spatial import cKDTree
from shapely import LineString


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
