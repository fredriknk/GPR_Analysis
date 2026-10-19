"""Explicit, deterministic laboratory/GPR fixtures for model integration checks."""

import csv
from pathlib import Path


def write_synthetic_training_inputs(directory: Path, *, samples_per_plot=40):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Synthetic inputs require a new directory")
    directory.mkdir(parents=True)
    assays, features = [], []
    for plot in range(4):
        for i in range(samples_per_plot):
            plot_id, sample_id = f"synthetic_{plot + 1:02d}", f"sample_{i:03d}"
            survey = f"synthetic_survey_{plot + 1:02d}"
            x, y = 500000 + plot * 10000 + (i % 10) * 10, 6600000 + (i // 10) * 10
            signal = float(10 + i % 11)
            common = {
                "sample_id": sample_id,
                "plot_id": plot_id,
                "survey_id": survey,
                "acquired_at_utc": "2026-01-01T12:00:00Z",
                "x": x,
                "y": y,
                "crs": "EPSG:32632",
                "horizontal_uncertainty_m": "",
                "depth_top_m": 0.1,
                "depth_bottom_m": 0.3,
                "unit": "percent",
                "method": "synthetic_sieving",
                "sand_silt_cutoff_um": 50,
                "silt_clay_cutoff_um": 2,
                "closure_tolerance_percent": 0.1,
                "uncertainty": "",
                "uncertainty_kind": "",
                "lab_batch_id": "synthetic",
                "quality_flag": 0,
            }
            for property_name, value in (
                ("sand_fraction", 40 + signal),
                ("silt_fraction", 35 - signal / 2),
                ("clay_fraction", 25 - signal / 2),
            ):
                assays.append(
                    {
                        "measurement_id": f"{survey}/{sample_id}/{property_name}",
                        **common,
                        "property": property_name,
                        "value": value,
                    }
                )
            features.append(
                {
                    "trace_id": f"synthetic/{survey}/line:{i}",
                    "source_id": "synthetic",
                    "profile_id": f"{survey}/line",
                    "survey_id": survey,
                    "plot_id": plot_id,
                    "x_m": x,
                    "y_m": y,
                    "target_crs": "EPSG:32632",
                    "time_utc": common["acquired_at_utc"],
                    "position_status": "measured",
                    "feature_name": "gpr_rms",
                    "value": signal,
                    "unit": "ADC counts",
                    "branch": "quantitative",
                    "time_basis": "corrected_twt_ns",
                    "window_start_ns": 20,
                    "window_stop_ns": 40,
                    "depth_top_m": 0.1,
                    "depth_bottom_m": 0.3,
                    "velocity_calibration_id": "synthetic-velocity-v1",
                    "velocity_calibration_evidence": "synthetic known medium; fixture calibration",
                    "processing_signature": "synthetic-window-rms-v1",
                    "quality_flags": 0,
                }
            )
    paths = [directory / "texture.csv", directory / "attributes.csv", directory / "model.toml"]
    for path, rows in zip(paths, (assays, features), strict=False):
        with path.open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    paths[2].write_text(
        'schema_version="0.1"\nsynthetic=true\nfeature_names=["gpr_rms_20_40_ns"]\n'
        'calibration_independence_evidence="synthetic fixed medium independent of targets"\n'
        "radius_m=1\nmax_lag_hours=1\nblock_size_m=5\nbuffer_m=1\n"
        "location_tolerance_m=0.01\nridge_alphas=[0.1,1.0,10.0]\n"
        "interval_level=0.9\ncalibration_fraction=0.3\n",
        encoding="utf-8",
    )
    return paths
