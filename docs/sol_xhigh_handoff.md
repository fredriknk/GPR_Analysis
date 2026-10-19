# Sol / xhigh implementation handoff

The requested coding workflow is Codex Sol with xhigh reasoning. That setting is
chosen in the Codex app; it is not a runtime dependency of this scientific tool.
M0–M3 and the manual-texture portion of M4 are implemented in this repository.
See [implementation_plan.md](implementation_plan.md) for status and acceptance.

For a later session, continue from the current code and artifacts:

```text
Read AGENTS.md, README.md, docs/architecture.md, docs/data_contracts.md and
docs/implementation_plan.md. Run the synthetic tests before changing code.

The immediate next work depends on real field inputs: reviewed four-plot polygons
and control/CRS, confirmed receiver datum and antenna offset, velocity/time-zero
evidence, and independent lab sand/silt/clay assays with method/cutoffs/depth.
Do not assign the mixed test project to a production plot by folder name.

When another sensor file is supplied, identify its format and measurement support,
then add a sensor adapter through the existing registry. Do not assume EMI or GRS
is required; confirm what GRS means. Preserve raw spectra/profile payloads and
their calibration metadata.

Only once independent texture reference samples are available, implement the M5
spatially separated training/validation baseline. Keep sample locations/repeats in
the same fold, fit preprocessing within training folds, and report unsupported
regions. Do not infer soil texture directly from GPR amplitudes.

Keep original GPR_raw_data immutable. Add synthetic regressions, update the lock
when dependencies change, and update milestone status with run evidence.
```

The current test collection has 80 imported DT1 profiles and a complete GPS audit
in `outputs/gps_audit_release_2026-10-19.json`. It has no validated production plot
assignments or texture ground truth. Results from that collection are diagnostics,
not four-plot soil characterization.
