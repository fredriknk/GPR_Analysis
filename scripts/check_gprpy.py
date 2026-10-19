"""Opt-in local compatibility spike; upstream sources stay outside the project package."""

import argparse
import hashlib
import importlib.util
import json
import struct
import tempfile
import time
from pathlib import Path

import numpy as np

from soil_sensing.inventory import sha256_file
from soil_sensing.io.sensors_software import read_header


def check(module_path: Path, root: Path) -> dict:
    normalized_hash = hashlib.sha256(module_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if normalized_hash != "49d697601a873854bed980329aec4a5c33283161d5fc1c2056688f09dded54da":
        raise ValueError("Reader source does not match the pinned GPRPy commit used for this spike")
    spec = importlib.util.spec_from_file_location("gprpy_dt1_spike", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for path in sorted(root.rglob("*.DT1")):
        hd = next(
            p
            for p in path.parent.iterdir()
            if p.name.casefold() == path.with_suffix(".hd").name.casefold()
        )
        header = read_header(hd)
        dtype = np.dtype([("header", "u1", 128), ("data", "<i2", header.samples_per_trace)])
        records = np.fromfile(path, dtype=dtype)
        started = time.perf_counter()
        amplitudes = np.asarray(module.readdt1(str(path)))
        matched = np.array_equal(amplitudes.T, records["data"])
        try:
            module.readdt1Header(str(hd))
            header_result = "ok"
        except Exception as exc:
            header_result = f"{type(exc).__name__}: {exc}"
        first_header = np.frombuffer(records["header"][0].tobytes()[:100], dtype="<f4")
        results.append(
            {
                "path": path.relative_to(root).as_posix(),
                "shape_samples_traces": list(amplitudes.shape),
                "amplitudes_match_records": matched,
                "hd_reader": header_result,
                "binary_field_7": float(first_header[6]),
                "window_ns_divided_by_samples": header.time_window_ns / header.samples_per_trace,
                "seconds": round(time.perf_counter() - started, 4),
            }
        )
    with tempfile.TemporaryDirectory() as directory:
        synthetic_header = Path(directory) / "blank_lines.HD"
        with next(root.rglob("*.HD")).open(newline="\n") as stream:
            lines = [line.strip() for line in stream if line.strip()]
        synthetic_header.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        try:
            module.readdt1Header(str(synthetic_header))
            blank_lines_result = "accepted"
        except Exception as exc:
            blank_lines_result = type(exc).__name__
        path = Path(directory) / "fixture.DT1"
        rows = np.array([[-32768, -1, 0, 32767], [7, 8, 9, 10]], dtype="<i2")
        payload = bytearray()
        for i, row in enumerate(rows):
            fields = [0.0] * 32
            fields[0], fields[1], fields[2], fields[5], fields[6] = i + 1, 1 - i, 4, 2, 400
            payload.extend(struct.pack("<32f", *fields))
            payload.extend(row.tobytes())
        path.write_bytes(payload)
        exact = np.array_equal(np.asarray(module.readdt1(str(path))).T, rows)
        payload[20:24] = struct.pack("<f", 4)  # Unsupported bytes/sample, unchanged length.
        path.write_bytes(payload)
        accepts_wrong_dtype = False
        try:
            module.readdt1(str(path))
            accepts_wrong_dtype = True
        except Exception:
            pass
        payload[0:4] = struct.pack("<f", 99)
        path.write_bytes(payload)
        accepts_bad_number = False
        try:
            module.readdt1(str(path))
            accepts_bad_number = True
        except Exception:
            pass
        path.write_bytes(payload[:-1])
        try:
            module.readdt1(str(path))
            truncated_result = "accepted"
        except Exception as exc:
            truncated_result = type(exc).__name__
    return {
        "upstream_version": "1.0.14",
        "upstream_commit": "3b1f75eba820764b2147568fc0cc40f3a47919d5",
        "upstream_reader_sha256": sha256_file(module_path),
        "numpy_version": np.__version__,
        "profile_count": len(results),
        "all_amplitudes_match": all(r["amplitudes_match_records"] for r in results),
        "header_failures": sum(r["hd_reader"] != "ok" for r in results),
        "synthetic_blank_line_header_result": blank_lines_result,
        "synthetic_exact_signed_values_and_reversed_positions": exact,
        "accepts_unsupported_bytes_per_sample": accepts_wrong_dtype,
        "accepts_corrupt_first_trace_number": accepts_bad_number,
        "truncated_file_result": truncated_result,
        "profiles": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error("Output must be outside the source tree")
    if args.output.exists():
        parser.error("Output already exists")
    report = check(args.reader, args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "profiles"}, indent=2))
