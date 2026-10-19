"""Sequential dependency-aware batches with verified content-addressed run reuse."""

import hashlib
import json
import re
import tomllib
import uuid
from pathlib import Path

from soil_sensing.export.report import verify_run
from soil_sensing.ingest import write_json
from soil_sensing.runs import begin_run, finish_run, input_hash

OPERATIONS = {
    "visualize": {"config"},
    "train": {"texture", "features", "config"},
    "predict": {"model", "input"},
    "export-grid": {"input"},
    "export-predictions": {"input", "reference"},
    "report": {"runs"},
    "process": {"input", "config"},
    "locate": {"input", "gps"},
}


def load_batch_config(path):
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "0.1" or set(data) - {"schema_version", "jobs"}:
        raise ValueError("Invalid batch schema")
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("Batch requires at least one job")
    ids = set()
    for job in jobs:
        identifier = job.get("job_id", "")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier)
            or identifier in ids
        ):
            raise ValueError("Batch job IDs must be unique lowercase identifiers")
        ids.add(identifier)
        operation = job.get("operation")
        if operation not in OPERATIONS or set(job) - {
            "job_id",
            "operation",
            "depends_on",
            *OPERATIONS[operation],
        }:
            raise ValueError("Unknown batch operation/fields")
        if OPERATIONS[operation] - job.keys():
            raise ValueError("Missing batch job inputs")
        if not isinstance(job.get("depends_on", []), list) or any(
            not isinstance(v, str) for v in job.get("depends_on", [])
        ):
            raise ValueError("depends_on must be a list of job IDs")
    ordered, remaining = [], list(jobs)
    while remaining:
        ready = [
            j for j in remaining if set(j.get("depends_on", [])) <= {r["job_id"] for r in ordered}
        ]
        if not ready:
            raise ValueError("Batch dependencies contain a cycle or unknown job")
        for job in ready:
            ordered.append(job)
            remaining.remove(job)
    return ordered


def _resolve(value, base, completed, dependencies):
    if not isinstance(value, str) or not value:
        raise ValueError("Job input must be a nonempty path string")
    if value.startswith("@"):
        reference, _, relative = value[1:].partition("/")
        if reference not in dependencies or reference not in completed:
            raise ValueError("Artifact references require an explicit completed dependency")
        root = completed[reference]
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Artifact reference escapes its dependency")
        return path
    return (base / value).resolve()


def _execute(operation, values, output):
    if operation == "visualize":
        from soil_sensing.export.scene import visualize_run

        return visualize_run(values["config"], output)
    if operation == "train":
        from soil_sensing.inference.workflow import train_run

        return train_run(values["texture"], values["features"], values["config"], output)
    if operation == "predict":
        from soil_sensing.inference.workflow import predict_run

        return predict_run(values["model"], values["input"], output)
    if operation == "export-grid":
        from soil_sensing.export.gis import export_grid_run

        return export_grid_run(values["input"], output)
    if operation == "export-predictions":
        from soil_sensing.export.gis import export_predictions_run

        return export_predictions_run(values["input"], values["reference"], output)
    if operation == "report":
        from soil_sensing.export.report import report_run

        return report_run(values["runs"], output)
    if operation == "process":
        from soil_sensing.workflow import process_run

        return process_run(values["input"], values["config"], output)
    if operation == "locate":
        from soil_sensing.workflow import locate_run

        return locate_run(values["input"], values["gps"], output)
    raise ValueError("Unsupported operation")


def _inputs(operation, values):
    inputs = [p for v in values.values() for p in (v if isinstance(v, list) else [v])]
    if operation == "visualize":
        from soil_sensing.export.scene import load_scene_config

        config, _, _ = load_scene_config(values["config"])
        inputs.extend(config["profiles"])
        if config.get("plots_file"):
            inputs.append(config["plots_file"])
    if operation == "export-grid":
        inputs.append(values["input"].parent / "support.json")
    return inputs


def batch_run(config_path, output, *, cache=None, progress=None):
    config_path = Path(config_path).resolve()
    jobs = load_batch_config(config_path)
    if cache is not None:
        cache = Path(cache).resolve()
        if any(p.casefold() in ("gpr_raw_data", ".git") for p in cache.parts):
            raise ValueError("Cache cannot be inside original sources or .git")
        if cache.is_relative_to(Path(output).resolve()) or Path(output).resolve().is_relative_to(
            cache
        ):
            raise ValueError("Shared cache and batch output must be separate")
        cache.mkdir(parents=True, exist_ok=True)
    output, manifest = begin_run("batch", output, [config_path])
    completed = {}
    manifest["jobs"] = []
    try:
        for job in jobs:
            operation, identifier = job["operation"], job["job_id"]
            if progress:
                progress(f"{identifier}: {operation}")
            values = {}
            for key in OPERATIONS[operation]:
                value = job[key]
                values[key] = (
                    [
                        _resolve(v, config_path.parent, completed, job.get("depends_on", []))
                        for v in value
                    ]
                    if key == "runs"
                    else _resolve(value, config_path.parent, completed, job.get("depends_on", []))
                )
            inputs = {str(p): input_hash(p) for p in _inputs(operation, values)}
            fingerprint = {
                "operation": operation,
                "inputs": inputs,
                "code": manifest["code_sha256"],
                "environment": manifest["environment"],
            }
            key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
            target = None
            if cache is not None:
                for candidate in sorted(cache.glob(key + "-*")):
                    record_path = candidate / "run.json"
                    if not record_path.is_file():
                        continue
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if record.get("status") != "complete":
                        continue
                    verify_run(candidate)
                    if record.get("cache_fingerprint") == fingerprint:
                        target = candidate.resolve()
                        break
            reused = target is not None
            if target is None:
                target = (
                    (cache / (key + "-" + uuid.uuid4().hex[:8]))
                    if cache is not None
                    else output / identifier
                )
                record = _execute(operation, values, target)
                if cache is not None:
                    record["cache_fingerprint"] = fingerprint
                    write_json(target / "run.json", record)
            completed[identifier] = target
            manifest["jobs"].append(
                {
                    "job_id": identifier,
                    "operation": operation,
                    "run": str(target),
                    "cache_key": key,
                    "reused": reused,
                    "status": "complete",
                }
            )
            write_json(output / "run.json", manifest)
        for root in completed.values():
            record = verify_run(root)
            if any(input_hash(p) != digest for p, digest in record.get("inputs", {}).items()):
                raise ValueError("Job input changed during batch")
        manifest["completed_jobs"] = len(completed)
        manifest["reused_jobs"] = sum(j["reused"] for j in manifest["jobs"])
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return finish_run(output, manifest)
