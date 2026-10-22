"""Static real-data waveform comparisons from a verified conditional fit."""

import csv
import html
import json
from pathlib import Path

import numpy as np

from soil_sensing.export.report import verify_run
from soil_sensing.runs import begin_run, finish_run


def _number(value):
    return "undefined (zero observed energy)" if value is None else f"{value:.4f}"


def report_experiment(fit_root, output):
    """Show every held-out line without refitting or modifying the source run."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fit_root = Path(fit_root).resolve()
    verify_run(fit_root)
    reports = json.loads((fit_root / "results.json").read_text(encoding="utf-8"))["reports"]
    fields = {k: v for k, v in reports.items() if "field_accuracy_improved" in v}
    if not fields:
        raise ValueError("Fit contains no field waveform comparisons")
    output, manifest = begin_run("gprmax_field_report", output, [fit_root])
    try:
        colors = dict(measured="#8797a5", mean="#142d40", model="#cb4933", baseline="#2166ac")
        fig, axes = plt.subplots(len(fields), 2, figsize=(13, 4.1 * len(fields)), squeeze=False)
        rows, table, detail_images = [], [], []
        for index, (name, report) in enumerate(fields.items()):
            with np.load(fit_root / f"{name}-fit.npz") as stored:
                time = stored["time_ns"]
                observed = stored["observations"].astype(float)
                train = stored["train"]
                prediction = stored["prediction"]
            baseline = observed[train].mean(axis=0)
            held = np.flatnonzero(~train)
            model_mse = float(np.mean((observed[~train] - prediction) ** 2))
            baseline_mse = float(np.mean((observed[~train] - baseline) ** 2))
            if not (
                np.isclose(model_mse, report["holdout"]["mse"])
                and np.isclose(baseline_mse, report["training_mean_holdout"]["mse"])
            ):
                raise ValueError("Stored fit metrics disagree with waveform arrays")
            ax, errors = axes[index]
            for line in observed[~train]:
                ax.plot(time, line, color=colors["measured"], linewidth=1, alpha=0.65)
            ax.plot(
                time,
                observed[~train].mean(axis=0),
                color=colors["mean"],
                linewidth=2,
                label="Measured holdout mean",
            )
            ax.plot(
                time,
                baseline,
                color=colors["baseline"],
                linewidth=2,
                linestyle="--",
                label="Training-mean baseline",
            )
            ax.plot(
                time,
                prediction,
                color=colors["model"],
                linewidth=2,
                label="gprMax fit (training only)",
            )
            ax.axhline(0, color="#ced6dd", linewidth=0.7)
            ax.set(
                title=f"{name}: {len(held)} held-out lines; {int(train.sum())} training",
                xlabel="Original recording time (ns)",
                ylabel="Signed amplitude (ADC counts)",
            )
            positions = np.arange(len(held))
            model_rmse, baseline_rmse, labels = [], [], []
            detail, panels = plt.subplots(
                3,
                int(np.ceil(len(held) / 3)),
                figsize=(12, 8),
                squeeze=False,
                sharex=True,
                sharey=True,
            )
            for panel, row in zip(panels.flat, held, strict=False):
                identity = report["per_line"][row]
                measured = observed[row]
                rmse = float(np.sqrt(np.mean((measured - prediction) ** 2)))
                base_rmse = float(np.sqrt(np.mean((measured - baseline) ** 2)))
                model_rmse.append(rmse)
                baseline_rmse.append(base_rmse)
                labels.append(identity["profile_id"])
                rows.append(
                    dict(
                        partition_id=name,
                        source_id=identity["source_id"],
                        profile_id=identity["profile_id"],
                        split="holdout",
                        retained_traces=identity["retained_traces"],
                        gprmax_rmse_adc=rmse,
                        baseline_rmse_adc=base_rmse,
                    )
                )
                panel.plot(time, measured, color=colors["mean"], linewidth=1.8)
                panel.plot(time, baseline, color=colors["baseline"], linestyle="--")
                panel.plot(time, prediction, color=colors["model"])
                panel.set_title(identity["profile_id"], fontsize=9)
                panel.grid(alpha=0.15)
            for panel in list(panels.flat)[len(held) :]:
                panel.set_visible(False)
            detail.suptitle(f"{name}: every held-out field line (median of retained native traces)")
            detail.supxlabel("Original recording time (ns)", y=0.07)
            detail.supylabel("Signed amplitude (ADC counts)")
            detail.legend(
                ax.get_legend_handles_labels()[0],
                ["Measured line median", "Training-mean baseline", "gprMax fit (training only)"],
                loc="lower center",
                ncol=3,
                frameon=False,
                fontsize=9,
            )
            detail.tight_layout(rect=(0.02, 0.11, 1, 0.96))
            detail_name = f"{name}-heldout-waveforms.png"
            detail.savefig(output / detail_name, dpi=150)
            plt.close(detail)
            detail_images.append(detail_name)
            errors.barh(positions - 0.18, model_rmse, 0.34, color=colors["model"])
            errors.barh(positions + 0.18, baseline_rmse, 0.34, color=colors["baseline"])
            errors.set_yticks(positions, labels, fontsize=8)
            errors.invert_yaxis()
            errors.set(
                title=f"{name}: error on each held-out line (lower is better)",
                xlabel="Waveform RMSE (ADC counts)",
            )
            for axis in (ax, errors):
                axis.spines[["top", "right"]].set_visible(False)
                axis.grid(axis="x", alpha=0.15)
            ratio = model_mse / baseline_mse if baseline_mse else None
            ratio_text = "undefined (zero baseline error)" if ratio is None else f"{ratio:.2f}×"
            table.append(
                f"<tr><td>{html.escape(name)}</td><td>{len(held)}</td>"
                f"<td>{_number(report['holdout']['normalized_mse'])}</td>"
                f"<td>{_number(report['training_mean_holdout']['normalized_mse'])}</td>"
                f"<td>{ratio_text}</td></tr>"
            )
        fig.suptitle(
            "Real field data: the layered gprMax fit misses much of the measured signal",
            fontsize=16,
            y=0.985,
        )
        handles, labels = axes[0, 0].get_legend_handles_labels()
        fig.legend(
            handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.95), ncol=3, frameon=False
        )
        fig.text(
            0.5,
            0.015,
            "Gray curves: individual held-out line medians. No display gain. "
            "ROIs selected before splitting; these are conditional waveform checks.",
            ha="center",
            fontsize=9,
            color="#465765",
        )
        fig.tight_layout(rect=(0, 0.045, 1, 0.89))
        fig.savefig(output / "real-data-comparison.png", dpi=160)
        plt.close(fig)
        with (output / "heldout-errors.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        page = (
            """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>gprMax real-data results</title><style>
body{font:16px/1.55 system-ui,sans-serif;max-width:1250px;margin:32px auto;
padding:0 20px;color:#142d40}
img{width:100%;height:auto}table{border-collapse:collapse;width:100%}td,th{text-align:left;
padding:10px;border-bottom:1px solid #ddd}a{color:#2166ac}</style>
<h1>gprMax: measured field results</h1>
<p>The tested layered models perform worse than the average of the training waveforms.
These results do not establish field depth, material properties or improved volume accuracy.</p>
<img src="real-data-comparison.png"
alt="Measured held-out waveforms, model predictions and per-line errors">
<table><tr><th>Grid</th><th>Held-out lines</th><th>Model NMSE</th><th>Baseline NMSE</th>
<th>Model / baseline squared error</th></tr>"""
            + "".join(table)
            + "</table>"
        )
        page += (
            "<p>NMSE is squared residual divided by squared measured signal; zero prediction "
            "scores 1. Lower is better. Each observation is a median native signed waveform "
            "for one retained line. Model and baseline use training lines only. Gray curves "
            "are separate held-out line summaries, not uncertainty bands.</p>"
            "<p>Coordinates, physical time zero, source response and calibration remain "
            "unresolved. Native recording times do not imply depth. A 0.25 m spatial buffer "
            "does not establish statistical independence.</p>"
            '<p><a href="heldout-errors.csv">Download all held-out line errors (CSV)</a></p>'
        )
        for name in detail_images:
            page += f'<img src="{name}" alt="Every held-out line: {html.escape(name)}">'
        page += "<p>Source fit: " + html.escape(str(fit_root)) + "</p></html>"
        (output / "index.html").write_text(page, encoding="utf-8")
        finish_run(output, manifest)
        return output
    except Exception as exc:
        plt.close("all")
        finish_run(output, manifest, exc)
        raise
