# Working on soil-sensing

Read README.md, docs/architecture.md, docs/data_contracts.md, and the relevant
milestone in docs/implementation_plan.md before implementation.

- Treat GPR_raw_data/ and all original sensor files as immutable. It also contains
  processed vendor products; directory names do not establish provenance or plot identity.
- Keep scientific functions in src/soil_sensing, with thin CLI/notebook callers.
- Reuse established libraries behind adapters. Complete the GPRPy compatibility
  spike before choosing a production DT1 reader; do not silently vendor upstream code.
- Preserve units, CRS/datum, acquisition time, trace identity, quality flags,
  sensor support, calibration evidence, and processing history.
- Missing metadata stays missing. Never invent plot origins, velocity, calibration,
  sensor precision, or ground truth. Do not assign test profiles to production plots.
- Keep display gain/background removal separate from quantitative amplitude products.
- No soil-property claims from amplitude alone. Use spatially separated validation;
  fit preprocessing and feature selection only on training folds.
- Source tests use synthetic fixtures. Local-data checks are opt-in; never require
  private data in CI. Test changes with `python -m unittest discover -s tests -v`
  after installation. Use Ruff when the dev extra is installed.
- Every implemented milestone must update status, commands, and limitations in docs.
- No cloud/API/model dependency is needed for the scientific pipeline. Sol / xhigh
  describes the requested coding workflow, not a soil-inference algorithm.
