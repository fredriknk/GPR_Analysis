"""Opt-in synthetic build benchmark; browser interaction timings require browser QA."""

import argparse
import ctypes
import json
import os
import platform
from pathlib import Path

from soil_sensing.export.scene import visualize_run


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = args.report.resolve()
    if report.exists() or "gpr_raw_data" in (p.casefold() for p in report.parts):
        parser.error("Benchmark report must be a new file outside GPR_raw_data")
    result = visualize_run(args.config, args.output, progress=lambda s: print(s, flush=True))
    metrics = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "build_seconds": result["elapsed_seconds"],
        "peak_working_set_bytes": peak_memory_bytes(),
        "grid_cells": result["grid_cells"],
        "payload_bytes": result["payload_bytes"],
        "curtain_vertices": result["rendered_curtain_vertices"],
        "run_manifest": str(args.output.resolve() / "run.json"),
        "browser_open_ms": None,
        "browser_slice_ms": None,
        "limitation": (
            "Build benchmark only. Browser/WebGL review and interaction timing remain required."
        ),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(metrics, indent=2, allow_nan=False) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
