"""Opt-in complete-grid reconstruction from an existing real-data collection run."""

import argparse

from soil_sensing.volumes.collection import collection_volume_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--partition", action="append", required=True)
    parser.add_argument("--combination-evidence", required=True)
    args = parser.parse_args()
    collection_volume_run(
        args.inputs, args.output, args.partition, args.combination_evidence, progress=print
    )


if __name__ == "__main__":
    main()
