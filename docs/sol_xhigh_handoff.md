# Sol / xhigh implementation handoff

The requested coding workflow is Codex Sol with xhigh reasoning. That setting is
chosen in the Codex app; it is not a runtime dependency of this scientific tool.
M0–M3, the manual-texture portion of M4 and the scientific/export core of M4.5
are implemented in this repository. M4.5 browser acceptance is still pending.
See [implementation_plan.md](implementation_plan.md) for status and acceptance.

For a later session, continue from the current code and artifacts:

```text
Read AGENTS.md, README.md, docs/architecture.md, docs/data_contracts.md and
docs/implementation_plan.md. Run the synthetic tests before changing code.

First review the M4.5 demo in a real WebGL browser. Use
configs/visualization/synthetic_demo.toml and a new output path; the existing
outputs/explorer-demo-2026-10-19/index.html is a local reference. Install the
Windows/Python 3.11 visualization lock or verify a separate supported environment.
Review actual screenshots, rotation/zoom, keyboard controls, layers, plot/slice
selection, save/restore, image export and performance. The previous browser tool
blocked file:// pages, so the 5 s opening / 250 ms slice targets are unverified.
Do not treat Node controller tests as browser/WebGL acceptance. Scientific grid
and profile integration tests already cover calibration, masks and line holdouts.

Optional M4 sample/pick layers and topographic rendering remain deferred. The
backend benchmark and limits are in docs/visualization_plan.md. Keep decimation
out of quantitative grids; interpolate only compatible quantitative gates.

Production scene work still depends on reviewed four-plot polygons and control/CRS,
confirmed receiver datum and antenna offset, and calibration appropriate to the
chosen vertical mode. M5 also needs independent lab sand/silt/clay assays with
method/cutoffs/depth. Do not assign the mixed test project to a production plot
by folder name. Existing unassigned lines may use explicit diagnostic odometer
scenes, which have no geographic grid.

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
