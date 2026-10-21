"""Optional gprMax4 CUDA forward adapter; no bundled upstream solver code."""

import json
import time
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version

import numpy as np

from soil_sensing.ingest import write_json
from soil_sensing.runs import begin_run, finish_run


def layered_input(epsilon, depth_m=None, *, cell_m=0.01):
    """Explicit hypothetical 2D layered medium, 200 MHz and 0.5 m offset.

    y increases upwards; ground is y=6 m. Depth is an assumed model coordinate,
    never field depth. Source/receiver sit one cell inside the upper medium.
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
        f"#hertzian_dipole: z 1.75 {6 - cell_m} 0 source\n"
        f"#rx: 2.25 {6 - cell_m} 0\n"
        f"#material: {epsilon} 0.005 1 0 upper\n"
        f"#material: {epsilon * 1.5} 0.005 1 0 lower\n"
        "#box: 0 0 0 4 6 0.01 upper\n"
    )
    if depth_m is not None:
        text += f"#box: 0 0 0 4 {6 - depth_m} 0.01 lower\n"
    return text


def read_ez(path):
    """Read actual receiver timing metadata from a v4 HDF5 output."""
    import h5py

    with h5py.File(path) as stored:
        signal = stored["rxs/rx1/Ez"]
        if stored.attrs["gprMax"] != "4.0.0":
            raise ValueError("This adapter is verified for gprMax 4.0.0")
        dt = float(signal.attrs["SampleInterval"])
        offset = float(signal.attrs["TimeSampleOffset"])
        values = signal[:].astype(float)
        if not np.isfinite(values).all() or not np.any(values):
            raise ValueError("Solver returned empty or nonfinite fields")
        return (np.arange(len(values)) * dt + offset) * 1e9, values


def cuda_bank(output):
    """Evaluate a bounded physical parameter bank on CUDA device zero only."""
    import gprMax
    import pycuda.driver as cuda

    if gprMax.__version__ != "4.0.0":
        raise ValueError("Use the frozen gprMax4 experiment environment")
    cuda.init()
    device = cuda.Device(0)  # Deliberately fails instead of substituting CPU.
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
    manifest["gprmax"] = dict(
        version=gprMax.__version__,
        pycuda=version("pycuda"),
        device=device.name(),
        compute_capability=device.compute_capability(),
        driver=cuda.get_driver_version(),
    )
    started = time.perf_counter()
    try:
        traces, rows = [], []
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
