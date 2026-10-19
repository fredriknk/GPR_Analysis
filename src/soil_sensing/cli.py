"""Small CLI for implemented capabilities only."""

import argparse
import json
import sys
from pathlib import Path

from soil_sensing import __version__
from soil_sensing.inventory import build_inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Soil sensing project tools")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory", help="Inspect sources without modifying them")
    inventory.add_argument("root", type=Path)
    inventory.add_argument(
        "--output", type=Path, help="Write a new JSON manifest outside the source"
    )
    inventory.add_argument("--hash-raw", action="store_true", help="SHA-256 DT1/HD/GPS sources")
    inventory.add_argument("--strict", action="store_true", help="Exit 1 on profile issues/orphans")
    ingest = commands.add_parser("ingest", help="Validate and save DT1 profiles and raw radargrams")
    ingest.add_argument(
        "root", type=Path, nargs="?", help="Source root; alternatively use --config"
    )
    ingest.add_argument(
        "--config", type=Path, help="Survey TOML; paths resolve relative to its file"
    )
    ingest.add_argument(
        "--project-id", default="local", help="Source namespace for direct-root imports"
    )
    ingest.add_argument("--assignments", type=Path, help="Explicit survey assignment CSV")
    selection = ingest.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--profile", action="append", help="Relative DT1 path; repeat for a subset"
    )
    selection.add_argument("--all", action="store_true", help="Import all non-excluded profiles")
    ingest.add_argument(
        "--output", type=Path, required=True, help="New run directory outside sources"
    )
    locate = commands.add_parser("locate", help="Associate an imported line with GPS trace blocks")
    locate.add_argument("--input", type=Path, required=True, help="Raw profile Zarr store")
    locate.add_argument("--gps", type=Path, required=True, help="Original matching GPS sidecar")
    locate.add_argument("--output", type=Path, required=True, help="New run directory")
    locate.add_argument("--source-crs", help="Explicit geographic CRS/datum, e.g. EPSG:4326")
    locate.add_argument("--target-crs", help="Explicit projected metric CRS")
    locate.add_argument("--antenna-forward-m", type=float, default=0.0)
    locate.add_argument("--antenna-right-m", type=float, default=0.0)
    audit = commands.add_parser("gps-audit", help="Audit GPS quality over a completed import run")
    audit.add_argument("--run", type=Path, required=True, help="Completed raw import run")
    audit.add_argument("--raw-root", type=Path, required=True, help="Original raw data root")
    audit.add_argument("--output", type=Path, required=True, help="New JSON report")
    process = commands.add_parser("process", help="Run quantitative and display GPR processing")
    process.add_argument("--input", type=Path, required=True, help="Raw profile Zarr store")
    process.add_argument("--config", type=Path, required=True, help="Processing TOML")
    process.add_argument("--output", type=Path, required=True, help="New run directory")
    coverage = commands.add_parser("coverage", help="Check located traces against a reviewed plot")
    coverage.add_argument("--input", type=Path, required=True, help="Located profile Zarr store")
    coverage.add_argument("--plots", type=Path, required=True, help="Plot geometry TOML")
    coverage.add_argument("--output", type=Path, required=True, help="New JSON report")
    texture = commands.add_parser("texture", help="Validate manual sand/silt/clay assays")
    texture.add_argument("--input", type=Path, required=True, help="Manual texture CSV")
    texture.add_argument("--output", type=Path, required=True, help="New run directory")
    match = commands.add_parser("match", help="Audit GPR-to-texture spatial/time/depth matches")
    match.add_argument("--texture", type=Path, required=True, help="Manual texture CSV")
    match.add_argument("--features", type=Path, required=True, help="GPR attributes CSV")
    match.add_argument("--radius-m", type=float, required=True)
    match.add_argument("--max-lag-hours", type=float, required=True)
    match.add_argument("--minimum-depth-overlap", type=float, default=0.25)
    match.add_argument("--output", type=Path, required=True, help="New run directory")
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            from soil_sensing.config import SurveyConfig, load_config
            from soil_sensing.ingest import ingest_collection

            if args.config:
                if args.root or args.assignments or args.project_id != "local":
                    raise ValueError("Use either --config or direct-root options")
                config = load_config(args.config)
            else:
                if args.root is None:
                    raise ValueError("ingest requires a source root or --config")
                config = SurveyConfig(
                    args.project_id,
                    args.root.resolve(),
                    args.assignments.resolve() if args.assignments else None,
                )
            result = ingest_collection(
                config,
                output=args.output,
                profiles=args.profile,
                progress=lambda message: print(message, flush=True),
            )
            print(
                f"Imported {len(result['profiles'])} profiles. Report: "
                f"{args.output.resolve() / 'index.html'}"
            )
            return 0
        if args.command == "locate":
            from soil_sensing.spatial.gps import GPSOptions
            from soil_sensing.workflow import locate_run

            result = locate_run(
                args.input,
                args.gps,
                args.output,
                GPSOptions(
                    source_crs=args.source_crs,
                    target_crs=args.target_crs,
                    antenna_forward_m=args.antenna_forward_m,
                    antenna_right_m=args.antenna_right_m,
                ),
            )
            print(f"Located profile: {args.output.resolve()} ({result['qc']})")
            return 0
        if args.command == "gps-audit":
            from soil_sensing.workflow import gps_audit_collection

            result = gps_audit_collection(
                args.run,
                args.raw_root,
                args.output,
                progress=lambda message: print(message, flush=True),
            )
            print(json.dumps(result["summary"], indent=2))
            return int(result["summary"]["failed"] > 0)
        if args.command == "process":
            from soil_sensing.workflow import process_run

            result = process_run(args.input, args.config, args.output)
            print(f"Processed {result['feature_rows']} attribute rows: {args.output.resolve()}")
            return 0
        if args.command == "coverage":
            from soil_sensing.io.storage import load_profile
            from soil_sensing.spatial.plots import coverage_report, load_plots

            target = args.output.resolve()
            if target.exists() or "gpr_raw_data" in (p.casefold() for p in target.parts):
                raise ValueError("Coverage output must be a new path outside GPR_raw_data")
            report = coverage_report(load_profile(args.input), load_plots(args.plots))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
            print(json.dumps(report, indent=2))
            return 0
        if args.command == "texture":
            from soil_sensing.fusion.workflow import texture_run

            result = texture_run(args.input, args.output)
            print(f"Validated {result['sample_count']} texture samples: {args.output.resolve()}")
            return 0
        if args.command == "match":
            from soil_sensing.fusion.workflow import match_run

            result = match_run(
                args.texture,
                args.features,
                args.output,
                radius_m=args.radius_m,
                max_lag_hours=args.max_lag_hours,
                minimum_depth_overlap=args.minimum_depth_overlap,
            )
            print(
                f"Audited {result['candidate_count']} pairs; "
                f"{result['contributor_count']} contributors"
            )
            return 0
        if args.output:
            target = args.output.resolve()
            if target.is_relative_to(args.root.resolve()):
                raise ValueError("Output must be outside the input tree")
            if target.exists():
                raise ValueError(f"Output already exists; choose a new run path: {target}")
        result = build_inventory(args.root, hash_raw=args.hash_raw)
        serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as stream:
                stream.write(serialized)
            print(json.dumps(result["summary"], indent=2))
            print(f"Manifest: {target}")
        else:
            print(serialized, end="")
        return int(
            args.strict
            and (result["summary"]["profiles_with_issues"] > 0 or bool(result["orphan_sidecars"]))
        )
    except ModuleNotFoundError as exc:
        print(
            f"error: missing dependency {exc.name!r}; install soil-sensing[processing]",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
