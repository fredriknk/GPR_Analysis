"""Opt-in gprMax4 experiment stages; scientific functions stay in the package."""

import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["audit", "bank", "fit", "validate"])
    parser.add_argument("--input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--observations")
    parser.add_argument("--validation", help="Completed mesh validation run for refined recovery")
    args = parser.parse_args()
    if args.stage != "bank" and not args.input:
        parser.error(f"{args.stage} requires --input")
    if args.stage == "fit" and not args.observations:
        parser.error("fit requires --observations")
    if args.stage != "fit" and (args.observations or args.validation):
        parser.error("--observations and --validation apply only to fit")
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
    elif args.stage == "validate":
        from soil_sensing.inversion.gprmax import cuda_validation

        cuda_validation(args.input, args.output)
    else:
        from soil_sensing.inversion.fitting import fit_experiment

        fit_experiment(args.input, args.observations, args.output, validation_root=args.validation)


if __name__ == "__main__":
    main()
