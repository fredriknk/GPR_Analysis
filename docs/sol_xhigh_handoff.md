# Implementation handoff

## Next requested work: M7 volumes and interpretation

Use **6.1 Sol / xhigh** for the next implementation session. The detailed
[M7 handoff](volume_interpretation_plan.md) specifies supported volumes for grid
partitions 1 and 2, reviewed horizon/layer thickness, potential drainage paths,
offline viewer controls, and numeric GeoTIFF/QML plus volume/mesh exports.
It contains a copy-ready prompt and ordered acceptance checkpoints. This work is
planned, not implemented; the current field data permits local recording-time
diagnostics, with geographic/depth products conditional on actual evidence.

## Existing implementation and constraints

M0–M3 tooling, the manual-texture foundation of M4, the M4.5 explorer, M5 calibrated
texture software and M6 reporting/packaging are implemented. Read AGENTS.md,
README.md, docs/architecture.md, docs/data_contracts.md and the implementation plan
before extending them. Release commands and evidence are in
[release_verification.md](release_verification.md).

The branch is `codex/complete-milestones`. The user requested a commit and push
for each completed milestone. Preserve this workflow when continuing milestone
work. Generated outputs, private sensor files and environments remain excluded
from Git. No cloud/model dependency belongs in the scientific pipeline.

The explorer has actual synthetic browser review, fast single-plot 3D slices and
four shared-scale plan maps. Camera resets force a new camera revision after user
rotation. A synthetic-only read-only loopback preview enables browser QA; exported
HTML still bundles local assets. Do not serve private field data through preview.

M5 already implements audited laboratory matching, connected spatial/support
groups, buffered held-out plots, isolated preprocessing/tuning and calibration,
simplex-constrained fractions, numerical JSON models and domain masks. Use the
existing implementation when actual reference data arrives. Synthetic scores are
software checks and cannot establish field accuracy or adequate sample size.

M6 adds native-CRS GIS round trips, verified portable HTML reports, held-out residual
maps, sequential dependency batches, validated cache reuse, a full dependency lock,
a wheel-install demo and Windows/Linux synthetic CI. Check the recorded CI outcome
and repository status before changing the release; do not repeat completed work.

Remaining field gates: reviewed plot polygons/CRS/control and assignments, receiver
datum, measured antenna offsets, evidenced physical time zero and velocity,
independent lab sand/silt/clay samples with method/cutoffs/depth, and actual new
sensor files. Confirm the meaning of GRS before adding its adapter. Add adapters
through the registry and preserve spectra/profile payloads and calibration.

The local test collection contains 80 DT1 profiles with a complete GPS audit at
`outputs/gps_audit_release_2026-10-19.json`. It has no validated production plot
assignments or texture ground truth. Keep it diagnostic; folder names are not
plot or provenance evidence. Vendor-image/field-parameter review, optional sensor
and pick layers, topographic rendering and volumes remain conditional/deferred.

After code changes, run `python -m unittest discover -s tests -v`, Ruff and the
Node controller tests when relevant. Use synthetic fixtures in CI; private-data
checks are explicitly opt-in. Keep docs status, commands and limitations current.
