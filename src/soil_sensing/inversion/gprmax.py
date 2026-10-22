"""Optional gprMax4 CUDA forward adapter; no bundled upstream solver code."""

import json
import time
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version
from pathlib import Path

import numpy as np

from soil_sensing.ingest import write_json
from soil_sensing.runs import begin_run, finish_run


def layered_input(epsilon, depth_m=None, *, cell_m=0.01):
    """Explicit hypothetical 2D layered medium, 200 MHz and 0.5 m offset.

    y increases upwards; ground is y=6 m. Depth is an assumed model coordinate,
    never field depth. Source/receiver remain 0.01 m inside the upper medium
    at both mesh resolutions, keeping the physical geometry fixed.
    """
    if epsilon not in (4.0, 9.0, 16.0) or cell_m not in (0.005, 0.01):
        raise ValueError("Use the experiment's declared permittivity/resolution set")
    if depth_m is not None and not 0.3 <= depth_m <= 5.0:
        raise ValueError("Hypothetical interface must be inside the model")
    text = (
        "#title: Hypothetical layered medium; not field calibration\n"
        "#domain: 4 6.5 0.01\n"
        f"#dx_dy_dz: {cell_m} {cell_m} 0.01\n"
        "#time_window: 105e-9\n#omp_threads: 4\n"
        "#waveform: ricker 1 200e6 source\n"
        "#hertzian_dipole: z 1.75 5.99 0 source\n"
        "#rx: 2.25 5.99 0\n"
        f"#material: {epsilon} 0.005 1 0 upper\n"
        f"#material: {epsilon * 1.5} 0.005 1 0 lower\n"
        "#box: 0 0 0 4 6 0.01 upper\n"
    )
    if depth_m is not None:
        text += f"#box: 0 0 0 4 {6 - depth_m} 0.01 lower\n"
    return text


def read_ez(path, *, component="Ez"):
    """Read actual receiver timing metadata from a v4 HDF5 output."""
    import h5py

    with h5py.File(path) as stored:
        signal = stored[f"rxs/rx1/{component}"]
        if stored.attrs["gprMax"] != "4.0.0":
            raise ValueError("This adapter is verified for gprMax 4.0.0")
        dt = float(signal.attrs["SampleInterval"])
        offset = float(signal.attrs["TimeSampleOffset"])
        values = signal[:].astype(float)
        if (
            values.ndim != 1
            or len(values) < 2
            or not np.isfinite([dt, offset]).all()
            or dt <= 0
            or not np.isfinite(values).all()
            or not np.any(values)
        ):
            raise ValueError("Solver returned empty or nonfinite fields")
        return (np.arange(len(values)) * dt + offset) * 1e9, values


def _cuda_environment():
    import gprMax
    import pycuda.driver as cuda

    if gprMax.__version__ != "4.0.0":
        raise ValueError("Use the frozen gprMax4 experiment environment")
    cuda.init()
    device = cuda.Device(0)  # Deliberately fails instead of substituting CPU.
    return dict(
        version=gprMax.__version__,
        pycuda=version("pycuda"),
        device=device.name(),
        compute_capability=device.compute_capability(),
        driver=cuda.get_driver_version(),
    )


def cuda_bank(output):
    """Evaluate a bounded physical parameter bank on CUDA device zero only."""
    import gprMax

    environment = _cuda_environment()
    output, manifest = begin_run(
        "gprmax_cuda_bank",
        output,
        [],
        configuration=dict(
            epsilon=[4, 9, 16],
            depth_m=[1, 2, 3, 4, 5],
            cell_m=0.01,
            conductivity_s_per_m=0.005,
            contrast_ratio=1.5,
            waveform="ricker",
            frequency_hz=200e6,
            tx_rx_offset_m=0.5,
            dimensionality="2D TMz",
            gpu=0,
            precision="single",
        ),
    )
    manifest["gprmax"] = environment
    started = time.perf_counter()
    try:
        traces, rows, reference_axis = [], [], None
        for epsilon in (4.0, 9.0, 16.0):
            background = None
            for depth in (None, 1.0, 2.0, 3.0, 4.0, 5.0):
                name = f"eps-{epsilon:g}-depth-{depth}"
                model = output / (name + ".in")
                model.write_text(layered_input(epsilon, depth), encoding="utf-8")
                print("CUDA forward", name, flush=True)
                with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                    with redirect_stdout(log), redirect_stderr(log):
                        gprMax.run(
                            inputfile=str(model),
                            gpu=[0],
                            gpu_precision="single",
                            hide_progress_bars=True,
                            log_level=20,
                        )
                axis, signal = read_ez(model.with_suffix(".h5"))
                if reference_axis is not None and not np.array_equal(axis, reference_axis):
                    raise ValueError("Bank receiver time axes differ")
                reference_axis = axis
                if depth is None:
                    background = signal
                else:
                    traces.append(signal - background)
                    rows.append(dict(epsilon=epsilon, depth_m=depth, model=name))
        np.savez_compressed(output / "bank.npz", time_ns=axis, signals=np.array(traces))
        write_json(
            output / "bank.json",
            dict(
                schema_version="1.0",
                models=rows,
                assumptions=manifest["configuration"],
                background="modelled homogeneous response subtracted from synthetic traces only",
                scope="hypothetical 2D reflection templates; not a recovered 3D material volume",
            ),
        )
        manifest["elapsed_seconds"] = time.perf_counter() - started
        finish_run(output, manifest)
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
    return json.loads((output / "bank.json").read_text())


def cuda_validation(bank_root, output):
    """Independent mesh refinement and an explicitly synthetic 3D CUDA smoke run."""
    import gprMax

    from soil_sensing.export.report import verify_run

    bank_root = Path(bank_root).resolve()
    verify_run(bank_root)
    environment = _cuda_environment()
    output, manifest = begin_run(
        "gprmax_cuda_validation",
        output,
        [bank_root],
        configuration=dict(
            epsilon=9, depth_m=3, coarse_cell_m=0.01, fine_cell_m=0.005, gpu=0, precision="single"
        ),
    )
    manifest["gprmax"] = environment
    started = time.perf_counter()
    try:
        fine = []
        inputs = [layered_input(9.0, cell_m=0.005), layered_input(9.0, 3, cell_m=0.005)]
        inputs.append(
            "#title: Synthetic 3D CUDA capability check\n"
            "#domain: 0.6 0.6 0.6\n#dx_dy_dz: 0.01 0.01 0.01\n"
            "#time_window: 8e-9\n#omp_threads: 4\n"
            "#waveform: ricker 1 200e6 source\n"
            "#hertzian_dipole: x 0.25 0.3 0.4 source\n#rx: 0.35 0.3 0.4\n"
            "#material: 9 0.005 1 0 synthetic_soil\n"
            "#box: 0 0 0 0.6 0.6 0.3 synthetic_soil\n"
        )
        for i, text in enumerate(inputs):
            model = output / f"validation-{i}.in"
            model.write_text(text, encoding="utf-8")
            print("CUDA validation", i, flush=True)
            with (output / f"validation-{i}.log").open("w", encoding="utf-8") as log:
                with redirect_stdout(log), redirect_stderr(log):
                    gprMax.run(
                        inputfile=str(model),
                        gpu=[0],
                        gpu_precision="single",
                        hide_progress_bars=True,
                        log_level=20,
                    )
            axis, signal = read_ez(model.with_suffix(".h5"), component="Ex" if i == 2 else "Ez")
            fine.append((axis, signal))
        fine_axis, background = fine[0]
        if not np.array_equal(fine_axis, fine[1][0]):
            raise ValueError("Refined model/background receiver time axes differ")
        truth = fine[1][1] - background
        models = json.loads((bank_root / "bank.json").read_text())["models"]
        index = next(i for i, m in enumerate(models) if m["epsilon"] == 9 and m["depth_m"] == 3)
        with np.load(bank_root / "bank.npz") as stored:
            coarse_axis = stored["time_ns"]
            coarse = stored["signals"][index]
        if coarse_axis[0] < fine_axis[0] or coarse_axis[-1] > fine_axis[-1]:
            raise ValueError("Refined simulation does not cover the coarse time axis")
        aligned = np.interp(coarse_axis, fine_axis, truth)
        report = dict(
            relative_l2=float(np.linalg.norm(aligned - coarse) / np.linalg.norm(aligned)),
            waveform_correlation=float(np.corrcoef(aligned, coarse)[0, 1]),
            coarse_cell_m=0.01,
            fine_cell_m=0.005,
            unchanged_geometry=True,
            three_dimensional_cuda_smoke=True,
            three_dimensional_receiver_component="Ex",
            truth_model=models[index],
            limitation="Two resolutions do not establish mesh convergence or field accuracy",
        )
        np.savez_compressed(output / "refined-truth.npz", time_ns=fine_axis, signal=truth)
        write_json(output / "validation.json", report)
        manifest["elapsed_seconds"] = time.perf_counter() - started
        finish_run(output, manifest)
        return report
    except Exception as exc:
        finish_run(output, manifest, exc)
        raise
