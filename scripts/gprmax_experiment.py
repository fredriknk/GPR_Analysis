"""Opt-in gprMax4 experiment stages; scientific functions stay in the package."""

import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["audit", "bank", "fit"])
    parser.add_argument("--input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--observations")
    args = parser.parse_args()
    if args.stage == "audit":
        from soil_sensing.inversion.observations import audit_observations

        reports = audit_observations(args.input, args.output)
        for report in reports:
            print(
                report["partition_id"], [(r["split"], len(r["trace_ids"])) for r in report["lines"]]
            )
    elif args.stage == "bank":
        from soil_sensing.inversion.gprmax import cuda_bank

        cuda_bank(args.output)
    else:
        from soil_sensing.inversion.fitting import fit_experiment

        fit_experiment(args.input, args.observations, args.output)


if __name__ == "__main__":
    main()
