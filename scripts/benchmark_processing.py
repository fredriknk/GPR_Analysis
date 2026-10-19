"""Opt-in full-size synthetic DC/RMS processing benchmark; no private files."""

import argparse
import json
from pathlib import Path

from soil_sensing.benchmark import benchmark_processing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plots", type=int, default=4)
    parser.add_argument("--lines-per-plot", type=int, default=101)
    parser.add_argument("--traces-per-line", type=int, default=2001)
    parser.add_argument("--samples", type=int, default=250)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists() or any(p.casefold() in ("gpr_raw_data", ".git") for p in target.parts):
        parser.error("Benchmark report must be a new file outside original sources")
    result = benchmark_processing(
        plots=args.plots,
        lines_per_plot=args.lines_per_plot,
        traces_per_line=args.traces_per_line,
        samples=args.samples,
        progress=lambda s: print(s, flush=True),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
