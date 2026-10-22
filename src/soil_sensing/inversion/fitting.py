"""Bounded template inversion with training-only nuisance fits and holdouts."""

import json
import re
from pathlib import Path

import numpy as np

from soil_sensing.export.report import verify_run
from soil_sensing.ingest import write_json
from soil_sensing.runs import begin_run, finish_run


def template_search(bank_time, signals, time, observed, train, shifts_ns, peak_times=None):
    """Variable projection: choose model/time shift, then fit one global signed gain.

    Target rows are independent line summaries. Held-out rows never affect model,
    shift or gain selection. No amplitude calibration is inferred by this fit.
    """
    bank_time, time, shifts_ns = [np.asarray(v, float) for v in (bank_time, time, shifts_ns)]
    signals, observed, train = (
        np.asarray(signals, float),
        np.asarray(observed, float),
        np.asarray(train),
    )
    if (
        any(
            v.ndim != 1 or len(v) < 2 or not np.isfinite(v).all() or np.any(np.diff(v) <= 0)
            for v in (bank_time, time)
        )
        or observed.ndim != 2
        or observed.shape[1] != len(time)
        or train.dtype != bool
        or train.shape != (len(observed),)
        or not train.any()
        or train.all()
        or not np.isfinite(observed).all()
        or signals.ndim != 2
        or not len(signals)
        or signals.shape[1] != len(bank_time)
        or not np.isfinite(signals).all()
        or shifts_ns.ndim != 1
        or not len(shifts_ns)
        or not np.isfinite(shifts_ns).all()
        or np.any(np.diff(bank_time) <= 0)
    ):
        raise ValueError("Use finite compatible waveforms with separate train and holdout rows")
    if peak_times is not None:
        peak_times = np.asarray(peak_times, float)
        if peak_times.shape != (len(signals),) or np.isinf(peak_times).any():
            raise ValueError("Supply one finite or censored (NaN) peak time per model")
    training = observed[train]
    mean = training.mean(axis=0)
    normalizer = float(np.mean(training**2))
    if normalizer <= 0:
        raise ValueError("Training waveforms have no signal energy")
    candidates = []
    for model, signal in enumerate(signals):
        for shift in shifts_ns:
            if peak_times is not None and not time[0] <= peak_times[model] + shift <= time[-1]:
                continue
            template = np.interp(time - shift, bank_time, signal, left=np.nan, right=np.nan)
            energy = np.dot(template, template)
            window_energy = energy * float(np.median(np.diff(time)))
            total_energy = np.dot(signal, signal) * float(np.median(np.diff(bank_time)))
            if (
                not np.isfinite(template).all()
                or energy <= 0
                or total_energy <= 0
                or window_energy < 0.1 * total_energy
            ):
                continue
            gain = float(np.linalg.lstsq(template[:, None], mean, rcond=None)[0][0])
            prediction = gain * template
            loss = float(np.mean((training - prediction) ** 2) / normalizer)
            candidates.append(dict(model=model, shift_ns=float(shift), gain=gain, train_loss=loss))
    if not candidates:
        raise ValueError("No template has complete temporal support and usable energy")
    candidates.sort(key=lambda c: (c["train_loss"], c["model"], c["shift_ns"]))
    best = candidates[0]
    prediction = best["gain"] * np.interp(
        time - best["shift_ns"], bank_time, signals[best["model"]]
    )

    def metrics(data, estimate):
        mse = float(np.mean((data - estimate) ** 2))
        energy = float(np.mean(data**2))
        return dict(mse=mse, normalized_mse=mse / energy if energy else None)

    report = dict(
        best=best,
        candidates=candidates,
        near_equivalent=[c for c in candidates if c["train_loss"] <= best["train_loss"] + 0.01],
        train=metrics(observed[train], prediction),
        holdout=metrics(observed[~train], prediction),
        zero_holdout=metrics(observed[~train], 0),
        training_mean_holdout=metrics(observed[~train], mean),
        train_lines=int(train.sum()),
        held_out_lines=int((~train).sum()),
        per_line=[
            dict(row=i, split="train" if train[i] else "holdout", **metrics(row, prediction))
            for i, row in enumerate(observed)
        ],
        supported_samples_per_line=len(time),
        near_equivalent_loss_tolerance=0.01,
        scope="conditional waveform compatibility; not calibration or volume accuracy",
    )
    return report, prediction


def fit_experiment(bank_root, observations_root, output, *, validation_root=None):
    """Compare the CUDA library with the audited native observations and a fixture."""
    bank_root, observations_root = Path(bank_root).resolve(), Path(observations_root).resolve()
    verify_run(bank_root)
    verify_run(observations_root)
    inputs = [bank_root, observations_root]
    if validation_root is not None:
        validation_root = Path(validation_root).resolve()
        validation_manifest = verify_run(validation_root)
        # Mesh validation must refer to this exact bank, not just the same model name.
        from soil_sensing.runs import input_hash

        if validation_manifest["inputs"].get(str(bank_root)) != input_hash(bank_root):
            raise ValueError("Refined validation was generated against a different bank")
        inputs.append(validation_root)
    shifts = np.round(np.arange(-8, 30.01, 0.4), 6)
    output, manifest = begin_run(
        "gprmax_inverse_fit",
        output,
        inputs,
        configuration=dict(
            shifts_ns=shifts.tolist(),
            minimum_template_window_energy_fraction=0.1,
            near_equivalent_loss_tolerance=0.01,
            synthetic_seed=874,
            synthetic_noise_rms_fraction=0.02,
        ),
    )
    try:
        catalog = json.loads((bank_root / "bank.json").read_text())
        models = catalog["models"]
        with np.load(bank_root / "bank.npz") as stored:
            axis, signals = stored["time_ns"], stored["signals"]
        peaks = []
        for model, signal in zip(models, signals, strict=True):
            # Reject censored arrivals and pre-arrival numerical residuals.
            arrival = (
                2
                * np.hypot(model["depth_m"] - 0.01, 0.25)
                * np.sqrt(model["epsilon"])
                / 0.299792458
            )
            gate = (axis >= arrival) & (axis <= arrival + 12)
            peaks.append(
                float(axis[np.flatnonzero(gate)[np.argmax(abs(signal[gate]))]])
                if arrival + 12 <= axis[-1] and gate.any()
                else np.nan
            )
        observations = json.loads((observations_root / "observations.json").read_text())
        reports = {}
        for partition in observations["partitions"]:
            name = partition["partition_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in reports:
                raise ValueError("Partition IDs must be unique safe filenames")
            with np.load(observations_root / (name + ".npz")) as data:
                report, prediction = template_search(
                    axis, signals, data["time_ns"], data["waveforms"], data["train"], shifts, peaks
                )
                if len(partition["lines"]) != len(data["waveforms"]):
                    raise ValueError("Observation line identities do not match waveform rows")
                for metrics, line in zip(report["per_line"], partition["lines"], strict=True):
                    if metrics["split"] != line["split"]:
                        raise ValueError("Observation line splits do not match waveform rows")
                    metrics.update(
                        source_id=line["source_id"],
                        profile_id=line["profile_id"],
                        retained_traces=len(line["trace_ids"]),
                    )
                np.savez_compressed(
                    output / (name + "-fit.npz"),
                    time_ns=data["time_ns"],
                    observations=data["waveforms"],
                    train=data["train"],
                    prediction=prediction,
                )
            report["hypothetical_model"] = models[report["best"]["model"]]
            report["selection_hash"] = partition["selection_hash"]
            report["selection_bias"] = partition["selection_bias"]
            report["amplitude_unit"] = "ADC counts"
            report["holdout_scope"] = "All audited line medians/samples; not whole-grid coverage"
            report["field_accuracy_improved"] = None
            reports[name] = report
        # A controlled software fixture: same bank, known noise and nuisance shift.
        # This is deliberately identified as an inverse-crime test, not independent validation.
        truth = next(i for i, m in enumerate(models) if m["epsilon"] == 9 and m["depth_m"] == 3)
        t = np.arange(20, 90, 0.4)
        exact = 1.7 * np.interp(t - 8.4, axis, signals[truth])
        rng = np.random.default_rng(874)
        observed = exact + rng.normal(0, np.sqrt(np.mean(exact**2)) * 0.02, (10, len(t)))
        train = np.arange(10) < 6
        report, prediction = template_search(axis, signals, t, observed, train, shifts, peaks)
        report["truth"] = dict(model=models[truth], shift_ns=8.4, gain=1.7)
        report["hypothetical_model"] = models[report["best"]["model"]]
        report["inverse_crime"] = True
        reports["synthetic_recovery"] = report
        report["recovered_bank_model"] = report["best"]["model"] == truth
        report["shift_error_ns"] = report["best"]["shift_ns"] - 8.4
        np.savez_compressed(
            output / "synthetic-fit.npz",
            time_ns=t,
            observations=observed,
            train=train,
            prediction=prediction,
        )
        if validation_root is not None:
            with np.load(validation_root / "refined-truth.npz") as data:
                fine_axis, fine_signal = data["time_ns"], data["signal"]
            validation = json.loads((validation_root / "validation.json").read_text())
            if validation["truth_model"] != models[truth]:
                raise ValueError("Refined truth model does not match declared synthetic recovery")
            if t[0] - 8.4 < fine_axis[0] or t[-1] - 8.4 > fine_axis[-1]:
                raise ValueError("Refined truth has incomplete synthetic temporal support")
            exact = 1.7 * np.interp(t - 8.4, fine_axis, fine_signal)
            rng = np.random.default_rng(874)
            observed = exact + rng.normal(0, np.sqrt(np.mean(exact**2)) * 0.02, (10, len(t)))
            report, prediction = template_search(axis, signals, t, observed, train, shifts, peaks)
            report.update(
                truth=dict(model=models[truth], shift_ns=8.4, gain=1.7),
                hypothetical_model=models[report["best"]["model"]],
                inverse_crime=False,
                recovered_bank_model=report["best"]["model"] == truth,
                shift_error_ns=report["best"]["shift_ns"] - 8.4,
                limitation="Different mesh, same solver/physics; not independent field validation",
            )
            reports["refined_synthetic_recovery"] = report
            np.savez_compressed(
                output / "refined-synthetic-fit.npz",
                time_ns=t,
                observations=observed,
                train=train,
                prediction=prediction,
            )
        write_json(
            output / "results.json",
            dict(
                schema_version="1.0",
                reports=reports,
                shifts_ns=shifts.tolist(),
                assumed_model=catalog["assumptions"],
                minimum_template_window_energy_fraction=0.1,
                causal_gate="Geometric upper-medium arrival plus 12 ns fits in simulation; "
                "peak in that gate lies in observation window after shift",
                accuracy_claim="No field depth, material or volume accuracy has been established",
            ),
        )
        _write_summary(output, reports, models)
        finish_run(output, manifest)
        return reports
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise


def _write_summary(output, reports, models):
    """Write reviewable numerical results without adding a plotting dependency."""
    lines = [
        "# gprMax conditional waveform experiment",
        "",
        "No field depth, material or volume accuracy has been established.",
        "",
        "| Case | Train / holdout lines | Model ε / depth (assumed m) | Shift (ns) | "
        "Holdout NMSE | Zero NMSE | Training-mean NMSE |",
        "|---|---|---|---|---|---|---|",
    ]
    details = []

    def number(value):
        return "unavailable" if value is None else f"{value:.6g}"

    for name, report in reports.items():
        model = report["hypothetical_model"]
        lines.append(
            f"| {name} | {report['train_lines']} / {report['held_out_lines']} | "
            f"{model['epsilon']:g} / {model['depth_m']:g} | {report['best']['shift_ns']:g} | "
            f"{number(report['holdout']['normalized_mse'])} | "
            f"{number(report['zero_holdout']['normalized_mse'])} | "
            f"{number(report['training_mean_holdout']['normalized_mse'])} |"
        )
        alternatives = sorted(
            {
                (models[c["model"]]["epsilon"], models[c["model"]]["depth_m"])
                for c in report["near_equivalent"]
            }
        )
        details.extend(
            [
                "",
                f"{name}: {len(report['near_equivalent'])} candidates within 0.01 "
                f"training NMSE of best; assumed (ε, depth m): {alternatives}.",
                "",
            ]
        )
        if "recovered_bank_model" in report:
            details.extend(
                [
                    f"Known synthetic bank model recovered: {report['recovered_bank_model']}; "
                    f"shift error: {report['shift_error_ns']:g} ns; "
                    f"same-discretization inverse crime: {report['inverse_crime']}.",
                    "",
                ]
            )
    lines.extend(details)
    lines.extend(
        [
            "Gain, model and shift fit training lines only. Loss equally weights line medians.",
            "Shifts are nuisance parameters; assumed depths/permittivities are not calibration.",
            "Field ROIs were selected using all-data volumes; holdouts are conditional.",
            "Near-equivalent candidates are a sensitivity diagnostic, not confidence bounds.",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
