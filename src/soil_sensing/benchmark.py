"""Opt-in, line-by-line synthetic processing scale measurement."""

import ctypes
import os
import platform
import time

import numpy as np
import xarray as xr

from soil_sensing.processing.gpr import ProcessingOptions, process_profile, window_rms_features


def peak_memory_bytes():
    if os.name != "nt":
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if platform.system() == "Darwin" else value * 1024

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize


def _profile(traces, samples, identifier):
    t = np.arange(samples) * 0.4
    distance = np.linspace(0, 100, traces)
    amplitude = (
        300 * np.sin(t[None, :] / 3 + distance[:, None] / 10) * np.exp(-t[None, :] / 80)
    ).astype(np.float32)
    missing = np.full(traces, np.nan)
    return xr.Dataset(
        {
            "amplitude": (("trace", "sample"), amplitude),
            "trace_id": ("trace", [f"synthetic-benchmark/{identifier}:{i}" for i in range(traces)]),
            "distance_m": ("trace", distance),
            "recorded_position_m": ("trace", distance),
            "x_m": ("trace", missing.copy()),
            "y_m": ("trace", missing.copy()),
            "elevation_m": ("trace", missing.copy()),
            "horizontal_uncertainty_m": ("trace", missing.copy()),
            "time_utc": ("trace", np.full(traces, np.datetime64("NaT", "ns"))),
            "position_status": ("trace", np.full(traces, "missing")),
            "quality_flags": ("trace", np.ones(traces, dtype=np.uint32)),
            "trace_header_bytes": (
                ("trace", "header_byte"),
                np.zeros((traces, 128), dtype=np.uint8),
            ),
        },
        coords={"trace": np.arange(traces), "sample": np.arange(samples), "twt_ns": ("sample", t)},
        attrs={
            "schema_version": "0.2",
            "source_id": "synthetic-benchmark-v1",
            "profile_id": identifier,
            "survey_id": "synthetic-benchmark",
            "plot_id": None,
            "source_crs": None,
            "target_crs": None,
            "vertical_datum": None,
            "source_checksums": {},
            "sample_interval_ns": 0.4,
            "time_axis_convention": "synthetic arange(N)*0.4 ns; no physical depth",
            "processing_branch": "raw",
            "amplitude_units": "synthetic ADC counts",
            "history": [{"operation": "synthetic_benchmark_generator"}],
        },
    )


def benchmark_processing(
    *, plots=4, lines_per_plot=101, traces_per_line=2001, samples=250, progress=None
):
    for value in (plots, lines_per_plot, traces_per_line, samples):
        if type(value) is not int or value <= 0:
            raise ValueError("Benchmark sizes must be positive integers")
    if traces_per_line < 2 or samples < 226 or samples * traces_per_line > 10_000_000:
        raise ValueError(
            "Use at least 2 traces/226 samples and at most 10 million samples per line"
        )
    start = time.perf_counter()
    windows = [(0, 20), (20, 50), (50, 90)]
    rows = 0
    for plot in range(plots):
        for line in range(lines_per_plot):
            raw = _profile(traces_per_line, samples, f"synthetic_{plot:02d}/line_{line:03d}")
            processed = process_profile(raw, ProcessingOptions())
            features = window_rms_features(processed, windows)
            rows += len(features)
            del raw, processed, features
        if progress:
            progress(f"Completed synthetic plot {plot + 1}/{plots}")
    elapsed = time.perf_counter() - start
    total = plots * lines_per_plot * traces_per_line * samples
    return {
        "synthetic": True,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "plots": plots,
        "lines_per_plot": lines_per_plot,
        "traces_per_line": traces_per_line,
        "samples_per_trace": samples,
        "total_traces": plots * lines_per_plot * traces_per_line,
        "float32_input_bytes_processed": total * 4,
        "attribute_rows_evaluated": rows,
        "elapsed_seconds": elapsed,
        "million_samples_per_second": total / elapsed / 1e6,
        "peak_working_set_bytes": peak_memory_bytes(),
        "limitation": (
            "Generated one line at a time; measures DC/RMS processing, "
            "not DT1/GPS/Zarr I/O or browser rendering"
        ),
    }
