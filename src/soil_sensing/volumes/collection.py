"""Reconstruct complete diagnostic grids from verified collection memberships."""

import json
from pathlib import Path

from soil_sensing.export.report import verify_run
from soil_sensing.volumes.workflow import volume_run


def collection_volume_run(inputs, output, partition_ids, combination_evidence, progress=None):
    """Preserve parent grid membership, including crossing acquisition directions.

    This is an explicit diagnostic pooling choice, not evidence of antenna-response
    equivalence. Existing workflow compatibility checks and direction audits apply.
    """
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if not combination_evidence.strip():
        raise ValueError("Describe the diagnostic direction-combination choice")
    verify_run(inputs)
    summary = json.loads((inputs / "collection_summary.json").read_text(encoding="utf-8"))
    available = {p["partition_id"]: p for p in summary["diagnostic_partitions"]}
    if not partition_ids or len(set(partition_ids)) != len(partition_ids):
        raise ValueError("Select unique complete grid partition IDs")
    if any(p not in available for p in partition_ids):
        raise ValueError("Unknown collection partition")
    partitions = [available[p] for p in partition_ids]
    stores = {
        (p["gps_geometry"]["source_id"], p["profile_id"]): p["store"] for p in summary["profiles"]
    }
    selected = [tuple(line) for p in partitions for line in p["lines"]]
    if len(set(selected)) != len(selected):
        raise ValueError("Collection grid memberships overlap")
    paths = [(inputs / stores[line]).resolve() for line in selected]
    if any(not p.is_relative_to(inputs) for p in paths):
        raise ValueError("Collection store escapes verified input run")
    config = output.with_suffix(".toml")
    if output.exists() or config.exists():
        raise ValueError("Use a fresh output run and adjacent config path")
    config.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        [
            'schema_version="1.0"',
            "synthetic=false",
            'coordinate_mode="raw_gps_relative"',
            'vertical_axis="recording_time"',
            'position_policy="accepted"',
            'direction_policy="combine_reviewed"',
            "combination_evidence=" + json.dumps(combination_evidence),
            "track_gap_m=1.0",
            "track_buffer_m=0.5",
            "evaluation_sample_step=16",
            'evaluation_attribute="rms"',
            "evaluation_buffer_m=0.25",
            "profiles=" + json.dumps([p.as_posix() for p in paths]),
            "[attributes]",
            "rms_samples=5",
            'rms_clipping_policy="window"',
            "[grid]",
            "spacing_m=0.25",
            "bin_m=0.25",
            "max_distance_m=1.0",
            "max_edge_m=1.5",
            "max_cells=3000000",
            "[volume]",
            "minimum_lines=2",
            "block_samples=8",
            "max_memory_mb=512.0",
            "max_stencil_patterns=256",
            "[preview]",
            "max_voxels=2000000",
            "max_native_samples=1500000",
            "max_payload_mb=120.0",
        ]
    )
    for partition in partitions:
        text += "\n[[partitions]]\npartition_id=" + json.dumps(partition["partition_id"])
        text += "\nlines=" + json.dumps(partition["lines"]) + "\n"
    config.write_text(text, encoding="utf-8")
    return volume_run(config, output, progress=progress)
