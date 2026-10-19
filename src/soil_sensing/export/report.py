"""Verified, portable HTML reports over completed local run artifacts."""

import csv
import html
import json
import shutil
from pathlib import Path
from urllib.parse import quote

from soil_sensing.runs import begin_run, finish_run, input_hash


def verify_run(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "run.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError(f"Incomplete/failed run cannot be reused: {root}")
    for name, digest in manifest.get("artifacts", {}).items():
        candidate = root / name
        path = candidate.resolve()
        if not path.is_relative_to(root) or path == root or candidate.is_symlink():
            raise ValueError("Run artifact paths must stay inside their run")
        if path.is_dir() and any(p.is_symlink() for p in path.rglob("*")):
            raise ValueError("Run artifacts cannot contain symbolic links")
        if input_hash(path) != digest:
            raise ValueError(f"Run artifact hash mismatch: {name}")
    return manifest


def _residual_maps(root, output):
    path = root / "held_out_predictions.csv"
    if not path.is_file():
        return []
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    pictures = []
    for index, plot_id in enumerate(sorted({r["plot_id"] for r in rows}), 1):
        selected = [
            r for r in rows if r["plot_id"] == plot_id and r["valid_support"].lower() == "true"
        ]
        if not selected:
            continue
        fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), layout="constrained")
        for axis, component in zip(
            axes, ("sand_fraction", "silt_fraction", "clay_fraction"), strict=True
        ):
            values = [
                100 * (float(r[component]) - float(r[component + "_observed"])) for r in selected
            ]
            limit = max(max(abs(v) for v in values), 0.01)
            image = axis.scatter(
                [float(r["x_m"]) for r in selected],
                [float(r["y_m"]) for r in selected],
                c=values,
                cmap="RdBu_r",
                vmin=-limit,
                vmax=limit,
                s=18,
            )
            axis.set(
                title=component.replace("_fraction", ""),
                xlabel="Easting (m)",
                ylabel="Northing (m)",
                aspect="equal",
            )
            fig.colorbar(image, ax=axis, label="Estimate − assay (percentage points)")
        fig.suptitle(f"{plot_id}: spatially held-out residuals")
        target = output / f"residuals-{index:02d}.png"
        fig.savefig(target, dpi=140)
        plt.close(fig)
        pictures.append(target.name)
    return pictures


def report_run(runs, output, *, max_bundle_mb=200):
    roots = [Path(p).resolve() for p in runs]
    if not roots or len(set(roots)) != len(roots):
        raise ValueError("Report requires unique completed run directories")
    manifests = [verify_run(p) for p in roots]
    declared = []
    for root, manifest in zip(roots, manifests, strict=True):
        for name in manifest.get("artifacts", {}):
            path = root / name
            declared.extend(path.rglob("*") if path.is_dir() else [path])
    size = sum(p.stat().st_size for p in declared if p.is_file())
    if size > max_bundle_mb * 1024**2:
        raise ValueError(
            "Report exceeds max_bundle_mb; select fewer runs or explicitly increase the budget"
        )
    output, manifest = begin_run(
        "report", output, roots, configuration={"max_bundle_mb": max_bundle_mb}
    )
    try:
        sections = []
        for index, (root, parent) in enumerate(zip(roots, manifests, strict=True), 1):
            target = output / f"run-{index:02d}"
            target.mkdir()
            shutil.copyfile(root / "run.json", target / "run.json")
            for name in parent.get("artifacts", {}):
                source = root / name
                destination = target / name
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copyfile(source, destination)
            operation = html.escape(parent.get("operation", "run"))
            status = (
                "SYNTHETIC DEMONSTRATION"
                if parent.get("synthetic") or (parent.get("configuration") or {}).get("synthetic")
                else "Recorded run"
            )
            sections.append(f'<section><h2>{operation}</h2><p class="badge">{status}</p>')
            summary = {
                k: parent[k]
                for k in (
                    "sample_count",
                    "independent_groups",
                    "evaluated_plots",
                    "scene_count",
                    "valid_support_count",
                    "supported_fraction",
                )
                if k in parent
            }
            sections.append(f"<pre>{html.escape(json.dumps(summary, indent=2))}</pre>")
            if (target / "index.html").is_file():
                sections.append(
                    f'<p><a href="run-{index:02d}/index.html">Open interactive review</a></p>'
                )
            if (target / "evaluation.json").is_file():
                evaluation = json.loads((target / "evaluation.json").read_text(encoding="utf-8"))
                sections.append(
                    "<details><summary>Evaluation, support and limitations</summary><pre>"
                    + html.escape(json.dumps(evaluation, indent=2))
                    + "</pre></details>"
                )
            for picture in _residual_maps(target, target):
                sections.append(
                    '<img alt="Spatially held-out residual map" '
                    f'src="run-{index:02d}/{quote(picture)}">'
                )
            sections.append(
                f'<p><a href="run-{index:02d}/run.json">Verified source manifest</a></p></section>'
            )
        document = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Soil sensing report</title><style>
body{font:16px/1.5 system-ui;max-width:1200px;margin:30px auto;padding:0 24px;
color:#18303d;background:#f3f6f7}
section{background:white;padding:24px;margin:24px 0;border:1px solid #cbd8dd}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
img{width:100%;height:auto}a{color:#076d73}.badge{color:#7b4b00;font-weight:bold}</style>
<h1>Soil sensing · reproducible run review</h1><p>Radar attributes and support diagnostics are
distinct from laboratory-calibrated texture estimates. Field validity depends on reviewed metadata,
calibration and independent samples. Synthetic results demonstrate software behaviour only.</p>"""
        (output / "index.html").write_text(
            document + "".join(sections) + "</html>", encoding="utf-8"
        )
        manifest.update(parent_runs=[str(p) for p in roots], bundle_input_bytes=size)
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return finish_run(output, manifest)
