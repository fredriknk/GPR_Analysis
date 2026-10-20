"""Build a verified M4.5 diagnostic 3D viewer for every DT1 in a local collection."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import xarray as xr

from soil_sensing.export.report import verify_run
from soil_sensing.export.scene import suggest_diagnostic_partitions, visualize_run
from soil_sensing.ingest import write_json
from soil_sensing.inventory import build_inventory
from soil_sensing.io.dt1 import find_sidecar, read_dt1
from soil_sensing.io.storage import load_profile, save_profile
from soil_sensing.processing.gpr import process_profile
from soil_sensing.runs import begin_run, finish_run
from soil_sensing.spatial.gps import GPSOptions, locate_profile
from soil_sensing.visualization.scene import gps_line_metrics
from soil_sensing.workflow import load_processing_config


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _partition_toml(partitions) -> str:
    blocks = []
    for partition in partitions:
        lines = ",\n".join(
            f"  [{_toml_string(source_id)}, {_toml_string(profile_id)}]"
            for source_id, profile_id in partition["lines"]
        )
        blocks.append(
            "[[diagnostic_partitions]]\n"
            f"partition_id = {_toml_string(partition['partition_id'])}\n"
            f"label = {_toml_string(partition['label'])}\n"
            f"default_visible = {str(partition['default_visible']).lower()}\n"
            "lines = [\n" + lines + "\n]\n"
        )
    return "\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Original DT1/HD/GPS root")
    parser.add_argument("--output", type=Path, required=True, help="New derived run directory")
    parser.add_argument(
        "--processing-config",
        type=Path,
        default=Path("configs/pipelines/gpr_baseline.toml"),
    )
    parser.add_argument(
        "--layout",
        choices=("gps", "odometer"),
        default="gps",
        help="Combine nearby lines by raw GPS layout, or keep one odometer scene per line",
    )
    parser.add_argument("--cluster-distance-m", type=float, default=1000.0)
    parser.add_argument("--grid-line-max-length-m", type=float, default=15.0)
    parser.add_argument("--grid-partition-distance-m", type=float, default=20.0)
    parser.add_argument("--grid-partition-min-lines", type=int, default=4)
    args = parser.parse_args()
    root = args.root.resolve()
    processing_config = args.processing_config.resolve()
    options, _, _, calibration = load_processing_config(processing_config)
    if calibration is not None:
        parser.error("This diagnostic runner requires recording time, without depth calibration")
    inventory = build_inventory(root)
    entries = inventory["profiles"]
    if not entries:
        parser.error("No DT1 profiles found")
    invalid = [entry for entry in entries if entry["issues"]]
    if invalid:
        parser.error(f"Resolve source issues before the all-data run: {invalid[0]}")
    if len(entries) > 100:
        parser.error("The diagnostic viewer supports at most 100 lines per run")
    sources = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in (".dt1", ".hd", ".gps")
    )
    output, manifest = begin_run(
        "all_real_data_3d_diagnostic",
        args.output,
        [*sources, processing_config, Path(__file__).resolve()],
        configuration={
            "source_root": str(root),
            "profile_count": len(entries),
            "processing": asdict(options),
            "gps": asdict(GPSOptions()),
            "vertical_axis": "recording_time",
            "diagnostic_layout": args.layout,
            "diagnostic_cluster_distance_m": args.cluster_distance_m,
            "grid_line_max_length_m": args.grid_line_max_length_m,
            "grid_partition_distance_m": args.grid_partition_distance_m,
            "grid_partition_min_lines": args.grid_partition_min_lines,
            "rms_gate_ns": [50.0, 90.0],
            "display_trace_step": 20,
            "display_sample_step": 4,
            "plot_assignments": None,
            "depth_calibration": None,
            "geographic_crs": None,
        },
    )
    summaries = []
    line_geometry = []
    stores = []
    try:
        for number, entry in enumerate(entries, 1):
            print(f"[{number}/{len(entries)}] {entry['profile_id']}", flush=True)
            source = root / entry["dt1_path"]
            raw = read_dt1(source, root=root, source_id="local-real-data-unassigned")
            located, gps_qc = locate_profile(
                raw, find_sidecar(source, ".gps", required=True), GPSOptions()
            )
            processed = process_profile(located, options)
            relative = Path("profiles") / Path(entry["profile_id"]) / "quantitative.zarr"
            store = output / relative
            store.parent.mkdir(parents=True, exist_ok=True)
            save_profile(processed, store)
            with load_profile(store) as restored:
                xr.testing.assert_identical(processed, restored)
            geometry = gps_line_metrics(processed)
            line_geometry.append(geometry)
            summaries.append(
                {
                    "profile_id": entry["profile_id"],
                    "trace_count": processed.sizes["trace"],
                    "sample_count": processed.sizes["sample"],
                    "odometer_distance_m": float(processed.distance_m.values[-1]),
                    "acquisition_date": processed.attrs["original_hd"]["acquisition_date"],
                    "gps": gps_qc,
                    "gps_geometry": geometry,
                    "store": relative.as_posix(),
                    "round_trip_verified": True,
                }
            )
            stores.append(relative)
            raw.close()
            located.close()
            processed.close()

        partitions = (
            suggest_diagnostic_partitions(
                line_geometry,
                max_line_length_m=args.grid_line_max_length_m,
                center_distance_m=args.grid_partition_distance_m,
                min_lines=args.grid_partition_min_lines,
            )
            if args.layout == "gps"
            else []
        )
        config = output / "viewer.toml"
        profile_lines = "\n".join(f"  {_toml_string(path.as_posix())}," for path in stores)
        config.write_text(
            'schema_version = "0.1"\n'
            "diagnostic = true\n"
            f'diagnostic_layout = "{args.layout}"\n'
            f"diagnostic_cluster_distance_m = {args.cluster_distance_m}\n"
            'vertical_axis = "recording_time"\n'
            "profiles = [\n" + profile_lines + "\n]\n"
            "gates = [[50.0, 90.0]]\n\n" + _partition_toml(partitions) + "\n"
            "[render]\n"
            "trace_step = 20\n"
            "sample_step = 4\n"
            "max_curtain_vertices = 250000\n"
            "max_measured_points = 100000\n"
            "max_input_samples = 20000000\n"
            "max_payload_mb = 80.0\n",
            encoding="utf-8",
        )
        write_json(
            output / "collection_summary.json",
            {
                "schema_version": "0.1",
                "profile_count": len(summaries),
                "trace_count": sum(item["trace_count"] for item in summaries),
                "amplitude_sample_count": sum(
                    item["trace_count"] * item["sample_count"] for item in summaries
                ),
                "profiles": summaries,
                "diagnostic_partitions": partitions,
                "limitation": (
                    "Approximate relative layout from raw NMEA coordinates; no verified "
                    "datum, geographic CRS, depth calibration, spatial interpolation, or "
                    "soil inference"
                    if args.layout == "gps"
                    else "Independent odometer/recording-time diagnostic scenes; no relative "
                    "placement, geographic CRS, depth calibration, or soil inference"
                ),
            },
        )
        viewer = output / "viewer"
        viewer_manifest = visualize_run(
            config, viewer, progress=lambda message: print(f"3D {message}", flush=True)
        )
        verify_run(viewer)
        manifest["viewer"] = {
            "path": "viewer/index.html",
            "scene_count": viewer_manifest["scene_count"],
            "rendered_curtain_vertices": viewer_manifest["rendered_curtain_vertices"],
            "payload_bytes": viewer_manifest["payload_bytes"],
        }
        manifest["summary"] = {
            "profiles": len(summaries),
            "traces": sum(item["trace_count"] for item in summaries),
            "amplitude_samples": sum(
                item["trace_count"] * item["sample_count"] for item in summaries
            ),
            "gps": {
                key: sum(item["gps"][key] for item in summaries)
                for key in ("measured", "interpolated", "missing", "rejected")
            },
        }
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    finish_run(output, manifest)
    verify_run(output)
    print(f"VERIFIED: {output / 'viewer/index.html'}", flush=True)


if __name__ == "__main__":
    main()
