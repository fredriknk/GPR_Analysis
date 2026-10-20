"""Exploratory coherent RMS anomalies; no material or drainage classification."""

import numpy as np
from scipy.ndimage import find_objects, label


def detect_anomalies(values, cells, *, threshold=3.0, minimum_cells=8, minimum_slice=20):
    """Label face-connected complete cells above a slice-relative robust score.

    Scores use (RMS - median) / max(1.4826 MAD, 0.1 median). A zero scale
    leaves the slice unscored, rather than manufacturing infinite evidence.
    Eight supported corners and a contained horizontal footprint are mandatory.
    Cell counts describe a computational lattice, never independent observations.
    """
    values = np.asarray(values, dtype=float)
    cells = np.asarray(cells, dtype=bool)
    if values.ndim != 3 or min(values.shape) < 2:
        raise ValueError("Detection needs a three-dimensional lattice with two points per axis")
    if cells.shape != tuple(n - 1 for n in values.shape[1:]):
        raise ValueError("Cell footprint shape disagrees with the volume")
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("Detection threshold must be finite and positive")
    if any(type(v) is not int or v < 1 for v in (minimum_cells, minimum_slice)):
        raise ValueError("Detection counts must be positive integers")
    if np.any(values[np.isfinite(values)] < 0):
        raise ValueError("Detection requires nonnegative RMS")
    scores = np.full(values.shape, np.nan)
    baselines = []
    for i, plane in enumerate(values):
        finite = plane[np.isfinite(plane)]
        median = scale = None
        if finite.size >= minimum_slice:
            median = float(np.median(finite))
            scale = float(max(1.4826 * np.median(abs(finite - median)), 0.1 * median))
            if scale > 0:
                scores[i] = (plane - median) / scale
        baselines.append(dict(count=int(finite.size), median=median, scale=scale))
    corners = [
        scores[
            z : z + values.shape[0] - 1, y : y + values.shape[1] - 1, x : x + values.shape[2] - 1
        ]
        for z in (0, 1)
        for y in (0, 1)
        for x in (0, 1)
    ]
    mean = np.mean(corners, axis=0)
    eligible = np.isfinite(mean) & cells[None, :, :]
    labels, _ = label(eligible & (mean >= threshold))  # six-neighbour connectivity
    components = []
    retained = np.zeros_like(labels)
    for old_id, region in enumerate(find_objects(labels), 1):
        if region is None:
            continue
        member = labels[region] == old_id
        count = int(member.sum())
        if count < minimum_cells:
            continue
        candidate_id = len(components) + 1
        retained[region][member] = candidate_id
        components.append(
            dict(
                candidate_id=candidate_id,
                status="proposed",
                cell_count=count,
                peak_score=float(mean[region][member].max()),
                mean_score=float(mean[region][member].mean()),
                index_bounds=[[r.start, r.stop] for r in region],
            )
        )
    report = dict(
        schema_version="1.0",
        method="slice_robust_rms_complete_cells_v1",
        threshold=threshold,
        minimum_cells=minimum_cells,
        minimum_slice=minimum_slice,
        scale_rule="max(1.4826 MAD, 0.1 median); zero scale excluded",
        connectivity=6,
        eligible_cells=int(eligible.sum()),
        selected_cells=int(np.count_nonzero(retained)),
        baselines=baselines,
        candidates=components,
        limitation="Exploratory radar anomalies, not probabilities, pipes or soil properties. "
        "Slice-relative scoring may suppress broad layers; no field accuracy is established.",
    )
    return retained, report


def write_anomalies(root, options=None):
    """Save full-resolution labels and auditable bounds beside a completed cube."""
    import json

    import xarray as xr
    from shapely.geometry import box, shape

    from soil_sensing.ingest import write_json

    with xr.open_zarr(root / "volume.zarr", chunks=None, mask_and_scale=False) as cube:
        domain = shape(cube.attrs["domain_geojson"])
        x, y, z = (cube[n].values for n in ("x", "y", "vertical"))
        cells = [
            [domain.covers(box(a, b, c, d)) for a, c in zip(x[:-1], x[1:], strict=True)]
            for b, d in zip(y[:-1], y[1:], strict=True)
        ]
        labels, report = detect_anomalies(
            np.where(cube.rms_valid.values, cube.rms.values, np.nan), cells, **(options or {})
        )
        report.update(
            selection_hash=cube.attrs["selection_hash"],
            attribute="rms",
            vertical_axis=cube.attrs["vertical_axis"],
            vertical_unit=cube.attrs["vertical_unit"],
            lineage="volume.zarr/native_sample; support.zarr/rms_bin and rms_weight; "
            "contributors.json; native.json",
        )
        for candidate in report["candidates"]:
            bounds = candidate["index_bounds"]
            candidate["bounds"] = {
                name: [float(axis[a]), float(axis[b])]
                for name, axis, (a, b) in zip(
                    ("vertical", "y_m", "x_m"), (z, y, x), bounds, strict=True
                )
            }
        np.savez_compressed(root / "anomaly_labels.npz", labels=labels)
        with np.load(root / "anomaly_labels.npz") as saved:
            np.testing.assert_array_equal(saved["labels"], labels)
        write_json(root / "anomalies.json", report)
        # Ensure the metadata contains no accidental non-finite JSON numbers.
        json.dumps(report, allow_nan=False)
    return report
