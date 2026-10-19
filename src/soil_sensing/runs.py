"""Shared immutable-input run manifests for inference and export workflows."""

import platform
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from soil_sensing import __version__
from soil_sensing.ingest import code_hash, tree_hash, write_json
from soil_sensing.inventory import sha256_file


def input_hash(path):
    path = Path(path)
    if not path.exists():
        raise ValueError(f"Input does not exist: {path}")
    return tree_hash(path) if path.is_dir() else sha256_file(path)


def begin_run(operation, output, inputs, *, configuration=None):
    output = Path(output).resolve()
    inputs = [Path(p).resolve() for p in inputs]
    if output.exists() or any(p.casefold() in ("gpr_raw_data", ".git") for p in output.parts):
        raise ValueError("Run output must be a new directory outside sources and .git")
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in inputs):
        raise ValueError("Run output must be separate from its inputs")
    hashes = {str(p): input_hash(p) for p in inputs}
    versions = {}
    for name in ("numpy", "scipy", "scikit-learn", "xarray", "zarr", "pyproj", "rasterio"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            continue
    manifest = {
        "schema_version": "0.1",
        "operation": operation,
        "status": "running",
        "tool_version": __version__,
        "started_utc": datetime.now(UTC).isoformat(),
        "code_sha256": code_hash(),
        "inputs": hashes,
        "configuration": configuration,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": versions,
        },
        "artifacts": {},
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "run.json", manifest)
    return output, manifest


def finish_run(output, manifest, error=None):
    if error is None:
        try:
            if manifest["inputs"] != {p: input_hash(p) for p in manifest["inputs"]}:
                raise ValueError("Inputs changed during the run")
            manifest["artifacts"] = {
                p.relative_to(output).as_posix(): sha256_file(p)
                for p in sorted(output.rglob("*"))
                if p.is_file() and p != output / "run.json"
            }
        except Exception as exc:
            finish_run(output, manifest, exc)
            raise
    manifest.update(
        status="failed" if error is not None else "complete",
        finished_utc=datetime.now(UTC).isoformat(),
    )
    if error is not None:
        manifest["error"] = f"{type(error).__name__}: {error}"
    write_json(output / "run.json", manifest)
    return manifest
