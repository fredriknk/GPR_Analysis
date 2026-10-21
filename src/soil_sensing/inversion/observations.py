"""Native waveform supports selected by an existing diagnostic radar volume."""

import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from soil_sensing.export.report import verify_run
from soil_sensing.ingest import write_json
from soil_sensing.io.storage import load_profile
from soil_sensing.runs import begin_run, finish_run
from soil_sensing.visualization.scene import raw_gps_relative_xy


def separated_training_mask(training_xy, held_out_xy, buffer_m):
    """Remove training positions within a declared radius of held-out positions."""
    if not np.isfinite(buffer_m) or buffer_m < 0:
        raise ValueError("Spatial buffer must be nonnegative and finite")
    if not len(held_out_xy):
        raise ValueError("A held-out position set is required")
    return cKDTree(held_out_xy).query(training_xy)[0] > buffer_m


def audit_observations(volume_run, output, *, radius_m=2.5, buffer_m=0.25):
    """Freeze both largest-anomaly ROIs; clipping and whole-line splits stay explicit.

    The ROI selection uses all-data anomalies. These are conditional holdouts,
    not an independent evaluation of discovery or field material accuracy.
    """
    import xarray as xr

    root = Path(volume_run).resolve()
    verify_run(root)
    if not np.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("ROI radius must be positive and finite")
    output, manifest = begin_run(
        "inverse_observation_audit",
        output,
        [root],
        configuration=dict(radius_m=radius_m, buffer_m=buffer_m),
    )
    try:
        catalog = json.loads((root / "products.json").read_text())
        reports = []
        for product in catalog["partitions"]:
            partition = root / product["path"]
            anomalies = json.loads((partition / "anomalies.json").read_text())
            candidate = max(anomalies["candidates"], key=lambda c: c["cell_count"])
            centre = np.array([np.mean(candidate["bounds"][k]) for k in ("x_m", "y_m")])
            with xr.open_zarr(partition / "volume.zarr", chunks=None) as cube:
                anchor = cube.attrs["coordinate_anchor_latlon"]
                if cube.attrs["coordinate_mode"] != "raw_gps_relative":
                    raise ValueError("This experiment requires an explicit raw-GPS local frame")
            native = json.loads((partition / "native.json").read_text())["profiles"]
            lines = []
            for row in sorted(native, key=lambda r: (r["source_id"], r["profile_id"])):
                profile = load_profile(partition / row["store"])
                xy = raw_gps_relative_xy(profile, anchor)
                time = profile.twt_ns.values
                lo, hi = candidate["bounds"]["vertical"]
                samples = np.flatnonzero((time >= lo - 6) & (time <= hi + 6))
                if not len(samples):
                    raise ValueError("Candidate has no native time support")
                amplitude = profile.amplitude.values[:, samples]
                if "adc_limit_mask" not in profile:
                    raise ValueError("Sample-level clipping evidence is required")
                valid = (
                    np.isfinite(xy).all(axis=1)
                    & np.isin(profile.position_status.values, ["measured", "interpolated"])
                    & (np.linalg.norm(xy - centre, axis=1) <= radius_m)
                    & np.isfinite(amplitude).all(axis=1)
                    & ~profile.adc_limit_mask.values[:, samples].any(axis=1)
                )
                indices = np.flatnonzero(valid)
                if len(indices) < 3:
                    continue
                if (
                    profile.attrs["antenna_frequency_mhz"] != 200
                    or profile.attrs["antenna_separation_m"] != 0.5
                ):
                    raise ValueError("Experiment model does not match recorded frequency/offset")
                lines.append(
                    dict(
                        **row,
                        xy=xy[indices],
                        amplitude=amplitude[indices],
                        trace_ids=profile.trace_id.values[indices].astype(str),
                        position_status=profile.position_status.values[indices].astype(str),
                        quality_flags=profile.quality_flags.values[indices],
                    )
                )
            if len(lines) < 4:
                raise ValueError("Too few independently identified lines in candidate ROI")
            held_indices = set(range(0, len(lines), 3))
            held_xy = np.concatenate([p["xy"] for i, p in enumerate(lines) if i in held_indices])
            waveforms, identities = [], []
            for i, line in enumerate(lines):
                keep = np.ones(len(line["xy"]), dtype=bool)
                if i not in held_indices:
                    keep = separated_training_mask(line["xy"], held_xy, buffer_m)
                if keep.sum() < 3:
                    continue
                waveforms.append(np.median(line["amplitude"][keep], axis=0))
                identities.append(
                    dict(
                        source_id=line["source_id"],
                        profile_id=line["profile_id"],
                        split="holdout" if i in held_indices else "train",
                        trace_ids=line["trace_ids"][keep].tolist(),
                        position_status=line["position_status"][keep].tolist(),
                        quality_flags=line["quality_flags"][keep].astype(int).tolist(),
                        excluded_by_buffer=int((~keep).sum()),
                    )
                )
            splits = [r["split"] for r in identities]
            if splits.count("train") < 2 or splits.count("holdout") < 1:
                raise ValueError("Buffered ROI has insufficient train/holdout lines")
            identifier = product["partition_id"]
            np.savez_compressed(
                output / (identifier + ".npz"),
                time_ns=time[samples],
                waveforms=np.array(waveforms),
                train=np.array([s == "train" for s in splits]),
            )
            reports.append(
                dict(
                    partition_id=identifier,
                    selection_hash=product["selection_hash"],
                    candidate=candidate,
                    lines=identities,
                    radius_m=radius_m,
                    buffer_m=buffer_m,
                    frequency_mhz=200.0,
                    separation_m=0.5,
                    time_zero_ns=None,
                    velocity_m_per_ns=None,
                    crs=None,
                    position_precision_m=None,
                    preprocessing="median native signed waveform per line; no display gain/filter",
                    selection_bias="ROI chosen using all-data volume; conditional holdouts only",
                )
            )
        write_json(output / "observations.json", dict(schema_version="1.0", partitions=reports))
        finish_run(output, manifest)
        return reports
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
