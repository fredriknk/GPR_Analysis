"""Explicit local synthetic end-to-end demonstration; never a field-data fallback."""

import csv
import json

from soil_sensing.export.gis import export_grid_run, export_predictions_run
from soil_sensing.export.report import report_run
from soil_sensing.export.scene import visualize_run
from soil_sensing.inference.synthetic import write_synthetic_training_inputs
from soil_sensing.inference.workflow import predict_run, train_run
from soil_sensing.runs import begin_run, finish_run


def demo_run(output, *, progress=None):
    output, manifest = begin_run(
        "synthetic_end_to_end_demo", output, [], configuration={"synthetic": True}
    )
    manifest["synthetic"] = True

    def message(text):
        if progress:
            progress(text)

    try:
        message("Generating explicit synthetic GPR/laboratory inputs")
        texture, features, config = write_synthetic_training_inputs(output / "inputs")
        train_run(texture, features, config, output / "model")
        with (output / "model/matched_samples.csv").open(newline="", encoding="utf-8") as stream:
            matched = list(csv.DictReader(stream))
        with (output / "model/prediction_template.csv").open(
            newline="", encoding="utf-8"
        ) as stream:
            header = next(csv.reader(stream))
        with (output / "inputs/prediction_supports.csv").open(
            "x", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=header)
            writer.writeheader()
            for row in matched:
                lineage = json.loads(row["lineage"])
                record = {k: row[k] for k in header if k in row}
                record["support_id"] = row["sample_id"]
                for feature, contributors in lineage.items():
                    record[feature + "_processing_signature"] = contributors[0][
                        "processing_signature"
                    ]
                writer.writerow(record)
        predict_run(
            output / "model/model.json",
            output / "inputs/prediction_supports.csv",
            output / "predictions",
        )
        message("Building four synthetic plot scenes and coverage masks")
        scene_config = output / "inputs/scene.toml"
        scene_config.write_text(
            'schema_version="0.1"\nsynthetic=true\ngates=[[30,40],[40,50]]\n'
            "[grid]\nspacing_m=5\n[render]\ntrace_step=4\nsample_step=4\n",
            encoding="utf-8",
        )
        visualize_run(scene_config, output / "explorer")
        report_inputs = [output / "model", output / "predictions", output / "explorer"]
        for index in range(1, 5):
            message(f"Exporting synthetic plot {index} to GIS")
            target = output / f"gis-plot-{index:02d}"
            export_grid_run(output / f"explorer/scene-{index:02d}/grid.zarr", target)
            report_inputs.append(target)
        export_predictions_run(
            output / "predictions/texture_estimates.csv",
            output / "predictions/model_reference.json",
            output / "gis-texture",
        )
        report_inputs.append(output / "gis-texture")
        message("Bundling verified HTML review and held-out residual maps")
        report_run(report_inputs, output / "report")
        manifest.update(report="report/index.html", explorer="explorer/index.html")
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return finish_run(output, manifest)
