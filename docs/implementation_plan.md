# Implementation plan

Deliver working, reviewable milestones in dependency order. Soil texture is the primary
target. EMI, GRS and other future measurements remain optional; no final sensor list
has been selected. Each milestone updates this file, records dependency versions,
and includes a runnable example with explicit limitations.

## M0 — Scaffold and inventory (implemented)

- Standard `src/` package, CLI, optional dependency groups, architecture and templates.
- HD metadata reader; deterministic path-based inventory; DT1 size/endpoint checks;
  optional SHA-256 of DT1/HD/GPS; missing companions and mixed-date warnings.
- No writes to sources; refuse output overwrite; synthetic regression tests.
- Local test collection is inventoried separately from the four production plots.

Verified on 2026-10-19: editable installation in `.venv` on Python 3.13.12;
11 synthetic tests pass; Ruff lint and formatting checks pass; all supplied TOML
files parse. Local inventory reports 80 profiles / 66,774 traces and hashes 241
DT1/HD/GPS files. All size/endpoint checks pass, with one orphan GPS file and
multiple acquisition dates reported. GPS quality and scientific backends are not
yet validated. `requirements-dev.lock` pins the installed development tools.

## M1 — Verified DT1 ingestion (implemented)

Dependencies: M0. Complete the GPRPy spike in library_assessment.md first.

Implement a source adapter and RadarProfile contract, typed configuration validation,
explicit assignment ingestion, per-trace metadata and provenance. Read a single
profile into xarray and persist/reopen it. Keep unresolved geographic positions null.
Inspect field timing rather than choosing `window / N` or `window / (N-1)` by guesswork.

Acceptance: exact integer amplitudes match synthetic fixtures and independently
checked raw records; every trace/count/dtype is validated; array orientation is
tested; invalid/truncated data fail; source checksums remain unchanged; sample
interval and time-zero conventions are documented with evidence. One raw radargram
shows axes/units and flags and renders correctly. Resolve and commit an environment
lock for the chosen backend; document any Windows/Python compatibility limits.

Implementation: strict NumPy DT1 adapter, validated xarray schema 0.2, lossless Zarr
format-2 storage, single/subset/batch ingestion CLI, assignment/config validation,
raw radargrams and HTML review, source/environment/artifact provenance and failed-run
tracking. GPRPy 1.0.14 was evaluated on all 80 supplied profiles before selection;
see [reader decision](dt1_reader_decision.md). All 24 synthetic tests pass, including
malformed interior records and full storage round trips, in a fresh environment
installed from `requirements-processing-win-py313.lock`.

Timing: raw recording spacing follows the documented window/N reference convention
(0.4 ns here); fractional HD and per-trace zero metadata are retained. Physical
time-zero indexing remains unresolved because the reference readers disagree. Raw
plots explicitly show uncorrected recording time; resolving physical zero is a gate
for M3 correction/depth products, not an invented default at ingestion.

Local review: `outputs/raw_import_verified_2026-10-19/index.html` and `run.json`.
All 80 profiles are imported with no assumed plot coordinates. Geographic timestamps
and positions remain missing until M2.

## M2 — GPS and plot geometry (implemented; field control pending)

Dependencies: M1 (GPS parsing experiments can happen earlier).

Implement NMEA trace-block association, checksum/quality handling, UTC dates,
bounded interpolation, odometer comparisons, CRS transforms and optional lever-arm
correction. Add plot polygon config validation and a coverage/quality report. Review
explicit assignments for this mixed test folder. Allow time-domain diagnostics for
unassigned lines but exclude them from production plot products.

Acceptance: synthetic checksum failures, empty constellation positions, midnight,
duplicate markers, reversed lines, gaps and missing endpoints are tested; no
interpolation crosses configured gaps or sessions; coordinates agree with independent
control within stated uncertainty; unknown CRS/geometry prevents geospatial export.
All four eventual plots can have separate configs without duplicated pipeline code.

Implemented: checksummed multi-GNSS trace blocks, UTC, fix metadata, conservative
interpolation, odometer comparison, explicit pyproj CRS transforms and antenna
offsets, reviewed plot-polygon validation and `locate`/`gps-audit`/`coverage` CLI.
The full local audit `outputs/gps_audit_release_2026-10-19.json` parsed all 80 lines:
65,971 measured positions, 9 interpolated, 758 missing and 36 rejected among 66,774
traces. No checksum failures or duplicate markers were found. Thirty-four invalid
fix blocks in `Data09line/Lines/LINE04` and two outliers in `LINE05` were rejected;
`Data09line/Lines/LINE02` has 540
missing traces kept as gaps. Synthetic known-control, midnight, checksum,
duplicate, gap and reversed-line tests pass. Field datum, control, plot polygons,
antenna offsets and assignments are absent, so projected field accuracy and
four-plot coverage remain unverified. The supplied lines remain unassigned.

## M3 — Reproducible GPR processing and attributes (core implemented; review pending)

Dependencies: M1; M2 for located products.

Implement pure transforms, quantitative/display branches, parameterized time-zero,
dewow/DC and bandpass, masks, provenance, and raw-versus-processed plots. Produce a
small set of time-window attributes and a reviewed reflector-pick format. Add
optional calibrated depth conversion only when velocity evidence is available.

Acceptance: known synthetic events preserve expected timing/amplitude within stated
tolerances; invalid filter settings fail; branch identity prevents gained amplitudes
from being used as quantitative input; masks survive processing; uniform/finite-offset
depth examples and uncertainty are checked. Compare vendor images qualitatively;
numerical parity requires documented original processing settings. No unsupported
soil-property prediction is emitted.

Implemented: per-trace DC removal excluding ADC-limit samples, optional dewow and
zero-phase bandpass, evidence-gated time-zero, quantitative/display separation,
window-local clipping masks, RMS attributes, reviewed reflector-pick CSV and
finite-offset homogeneous depth with velocity evidence and uncertainty. The
`process` CLI writes Zarr, attribute CSV and raw/quantitative/display plots.
Synthetic passband/stopband, timing, branch and depth tests pass. On a 700-trace
test line, three windows produced 2,100 rows, with 799 excluded by clipping/signal
flags (`outputs/process_line00_release_2026-10-19/`). No field
time-zero or velocity evidence is supplied; the baseline remains recording-time
only. Vendor-image review and field processing parameter selection remain pending.

## M4 — Extensible measurement ingestion and fusion (manual foundation implemented)

Dependencies: M2 and initial M3 features; real reference files when available.

Implement generic observation/unit/support validators and manual texture sample
ingestion first. Add a registry and one additional sensor adapter based on the actual
next supplied dataset. EMI/GRS schemas are provisional examples, not obligations.
Preserve spectra/profiles separately from summary features. Create explicit spatial,
temporal and depth-support matching plus a contributor/exclusion audit.

Acceptance: adding a synthetic new sensor requires no changes to the GPR core;
unknown units fail; missing sensors stay optional; same-named lines across surveys
cannot collide; out-of-radius, out-of-time or incompatible-depth matches are excluded;
texture fractions/methods/closure are validated; joins are reproducible. Confirm GRS
meaning before implementing a gamma-ray adapter.

Implemented: an adapter registry, strict manual sand/silt/clay CSV validation
(metric CRS, UTC, depth, method, size cutoffs, uncertainty, units and closure),
sample-level spatial/temporal/calibrated-depth matching, median aggregation and
contributor/exclusion audit. `texture` and `match` commands write traceable runs.
Synthetic adapter, validation, exclusion and repeatability tests pass. No real
texture assays or secondary sensor files are supplied, so field joins and a
specific EMI/GRS adapter remain pending. GRS meaning is still unconfirmed.

## M4.5 — Interactive 3D Plot Explorer (implemented; synthetic and field diagnostic browser review complete)

Dependencies: M2/M3 for field scenes; synthetic fixtures allow immediate development.
M4 observation layers are optional. This intermediate milestone comes before M5.

Build a local offline explorer with a four-plot selector, rotatable survey tracks,
radar curtains, movable time/depth slices, measured/interpolated toggles and coverage
overlays. Start with Plotly and existing SciPy/xarray tools. Interpolate quantitative
attributes within compatible gates, with footprint/gap masks and whole-line spatial
validation. Use calibrated depth only when evidence is available; otherwise label
recording time in ns. Do not imply radar attributes are soil texture predictions.

Deliver in four checkpoints: **a)** synthetic visual prototype, **b)** existing
processing-product integration, **c)** supported interpolation, **d)** four-plot
comparison, offline export and performance verification. The current unassigned
field data remains diagnostic until CRS and plot geometry are reviewed.

Acceptance: an offline synthetic four-plot demo supports rotation, layer/plot
selection and slice navigation; measured gaps and unsupported regions stay visible;
grids retain units, sources, calibration and support diagnostics; held-out-line
evaluation reports error and supported coverage; rendering meets documented budgets.
See [the detailed visualization implementation plan](visualization_plan.md) for
UX, package boundaries, interpolation rules, artifacts and checkpoint criteria.

Implemented on 2026-10-19: deterministic synthetic four-plot scenes, typed scene/grid
contracts, quantitative located-profile integration, explicit recording/corrected
time and calibration-gated depth modes, gap-preserving curtains, RMS slices,
whole-line evaluation, support-masked nearest/linear interpolation and offline
Plotly packaging. Four-plot overview uses compatible units/processing and shared
limits. Viewer controls include cameras, layers, position support, slice playback,
vertical aspect and save/restore views. Input metadata preflight, grid/render/payload
budgets, checksums, failed-run markers and lossless grid reopen checks are enforced.

Checkpoint status: **a)** prototype and actual WebGL browser review implemented.
**b)** profile integration
and metric/depth synthetic tests implemented; optional M4 sample markers deferred.
**c)** implemented and tested on constant/linear fields, holes, disconnected regions,
long triangles, invalid windows and whole-line holdouts. Field spacing/mask tuning
needs real acquisition geometry. **d)** overview, offline assets, wheel packaging,
Windows/Python 3.11 lock, backend benchmark and browser review implemented.
The comparison overview uses four full-resolution plan maps with shared scales;
the selected plot retains rotatable 3D curtains and slices. This measured rendering
choice avoids four expensive concurrent WebGL scenes. Volume preview is deferred.

Run the demo and checks using the visualization lock:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-visualization-win-py311.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m soil_sensing visualize --config configs/visualization/synthetic_demo.toml --output outputs/explorer-001
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
node --test tests/viewer.test.cjs
```

The Node tests use only its built-in test runner and exercise precomputed controller
traces; Node is not a scientific runtime dependency. Verification from a fresh
lock-installed environment originally passed 51 Python tests, including from the built wheel.
The wheel includes viewer assets. Browser review on 2026-10-20 uses a read-only,
synthetic-only loopback preview because browser automation does not allow file URLs.
No scientific network service or external asset fetch is required. The full demo
is `outputs/explorer-release-2026-10-20-v3/index.html`; see the visualization plan
for measured opening/slice timings, screenshots and interaction evidence. Seven
Node controller tests now include combined-curtain gap/identity checks and plan
overview masks. These controller tests supplement actual browser review.

The supplied real lines still lack reviewed assignments/CRS/plot geometry and
velocity evidence. They may enter explicit odometer diagnostics or combined
raw-NMEA relative-layout diagnostics, with no geographic grid. A verified local
run on 2026-10-20 grouped all 80 profiles into three spatially separate scenes:
72 lines at the main site, five at a second site and three at a third. It preserved
66,774 trace identities and 16,693,500 full-resolution amplitude samples, while
the browser used 219,555 decimated curtain vertices. No texture inference or
extra sensor adapter was invented.

Follow-up on 2026-10-20: the main 72-line GPS scene now exposes two compact
display partitions (34 and 22 lines) and hides its 16 miscellaneous/traverse lines
by default. The two other site scenes retain their five and three lines. A line
subset menu, individual checkboxes, all/none actions, saved-view line selection
and selection-relative camera bounds were browser-reviewed on the real data.
Partition suggestions use recorded line length and centre proximity with explicit
configurable thresholds; they do not establish production plot identities.

## M5 — Calibrated texture inference (software implemented; field validation pending)

Dependencies: M4 and enough independent, representative texture reference samples.

Choose fractions or a named texture taxonomy. Implement mean/regularized baselines,
constrained/compositional fraction predictions or class models as appropriate,
spatial grouping/buffers, nested preprocessing/tuning and uncertainty. Evaluate
sensor ablations and leave-one-plot-out transfer where reference coverage permits.
Treat moisture/campaign conditions as potential confounders.

Acceptance: no shared sample/support leaks across folds; preprocessing never fits on
held-out data; predicted fractions are valid and close to the declared total;
metrics have units and independent counts; report all four plot results when possible;
interval coverage and residual maps are assessed; unsupported regions are masked.
Sparse reference data produces a clear limitation, not fabricated model accuracy.

Implemented on 2026-10-19: evidenced quantitative GPR/lab sample table, source
contributor lineage, connected block/location/shared-support grouping, buffered
leave-one-plot-out evaluation, isolated inner ridge tuning, training-only median
imputation/scaling and active-feature selection, group-weighted mean comparator,
simplex-constrained fractions and independent-group split-calibration intervals.
JSON model and CSV estimates preserve units, lab definition, lineage, calibration,
missingness, support/domain masks and source hashes. Too little independent support
fails explicitly or produces null interval/error fields. Campaign shifts are flagged.

Commands: `soil-sensing train --texture <lab.csv> --features <attributes.csv>
--config configs/inference/texture_baseline.toml --output <new-run>`, then
`soil-sensing predict --model <run>/model.json --input <supports.csv> --output <new-run>`.
See README for PowerShell examples and the generated prediction template. The
template config requires reviewed independent-calibration evidence before fitting.
`requirements-modeling-win-py311.lock` pins the verified environment including
scikit-learn 1.9.1, NumPy 2.4.6 and SciPy 1.17.1.

Seven new inference regressions check constraints, trace/location/group leakage,
held-out target isolation, training-only preprocessing, portable coefficients,
unsupported domains, insufficient intervals and end-to-end runs. The explicit
synthetic integration check fits 160 independent samples across four plots and
evaluates all four outer folds (`outputs/inference-demo-model-001/`). It is a
software fixture, not evidence of field texture accuracy. No real reference samples
are supplied. Field calibration/transfer, additional sensor ablations and any
texture taxonomy remain conditional on actual study data; no defaults establish
ground truth, sensor accuracy or adequate sample size.

Verification: 58 Python synthetic tests and six Node controller tests pass; Ruff
lint/format checks pass. No private dataset is needed by the model tests.

## M6 — Field-ready reports and packaging (implemented; field deployment conditional)

Dependencies: M3 for radar reports; M4.5 for interactive scenes; M5 for texture products.

Export plot coverage, radargrams, attributes, texture estimates, uncertainty and
support masks in GIS formats and an HTML/PDF summary if needed. Provide a batch CLI
over the four configs, complete/incomplete run markers and dependency-aware caching.
Add synthetic CI on Windows and Linux; private data remains an opt-in local check.

Acceptance: a clean environment reproduces a small synthetic end-to-end run; GIS
files round-trip with matching CRS/nodata/depth units; a second run cannot overwrite
the first; reports distinguish observations from inference; a full-scale line-by-line
benchmark documents memory/runtime. Reuse the M4.5 viewer in reports; consider a
broader application or distributed compute only when measured needs justify it.

Earlier M2–M4 validation on 2026-10-19: 35 synthetic tests passed, Ruff checks passed,
two representative GPS lines and one GPR line were run locally, and all 80 lines
were GPS-audited. `requirements-field-win-py313.lock` pins NumPy 2.5.4, SciPy
1.18.1, xarray 2026.9.0, Zarr 3.4.1, pynmea2 1.19.0, pyproj 3.8.0 and
Matplotlib 3.11.2.

Implemented on 2026-10-20: north-up GeoTIFF attributes/baselines/support diagnostics,
coverage GeoPackage/GeoParquet, texture support-point vectors with fractions and
intervals, lossless reopen checks, portable verified HTML reports with residual
maps and the offline explorer, sequential dependency batches and verified caching.
`demo` runs the explicitly synthetic model/scene/GIS/report workflow without private
files. A Windows/Python 3.11 full lock and Windows/Linux synthetic CI are included.
The synthetic-only loopback preview enables actual browser QA under the browser's
file-URL restriction. No cloud service is part of the scientific pipeline.

Commands and exact release evidence are in [release_verification.md](release_verification.md)
and README. A fresh lock-installed environment and wheel reproduced the complete
demo. Synthetic tests cover GIS CRS/centres/nodata/gate units, masks, calibrated
depth metadata, report integrity, cache reuse/invalidation, failed runs, path
confinement and synthetic preview restrictions. Full CI execution is checked after
the milestone push; its result is recorded in the release verification document.

The full-size sequential DC/RMS benchmark processed 808,404 traces / 202,101,000
samples over 404 synthetic lines in 131.0 s with 163.5 MiB peak process working
set. It generated one line at a time and excluded DT1/GPS/Zarr I/O; it is not a
complete field-ingestion throughput claim. No distributed processing is needed
on the evidence from this benchmark.

The software milestones are implemented for the available contracts. Field gates
remain explicit: reviewed CRS/control/plot assignments, physical time-zero and
velocity evidence, laboratory texture references, actual secondary-sensor files
and the meaning of GRS. Vendor-image/field-parameter review and field transfer
accuracy require those inputs. Optional extra sensor/pick/volume/topographic layers
and PDF reports are deferred; the delivered report is portable HTML. Missing
evidence was not replaced with synthetic production data.

Follow-up: `tests_local/test_real_data.py` implements six opt-in checks against the
actual development collection, including independent binary records, documented
GPS gaps, quantitative RMS/clipping, display separation, exact Zarr round trips
and all-source checksum protection. See [local_data_checks.md](local_data_checks.md)
for commands. The ordinary `tests/` suite remains synthetic and private-data-free.
Remote CI status remains unverified because the read-only status query could not
pass automatic approval review while account usage was exhausted.

Real-data execution on 2026-10-20: all six local checks passed on five recordings
and 23,415 traces; all 241 original DT1/HD/GPS hashes were unchanged. The synthetic
suite still passes all 65 tests and Ruff passes. A separate completed diagnostic
run saves full quantitative Zarr/CSV products, original and display-gain
radargrams, GPS strips, window-quality summaries and a local HTML report.
See [real_data_results.md](real_data_results.md) for measured results, reproduction
commands and scientific limits. This run does not establish field texture accuracy.

## M7 — Radar volumes, layers and drainage candidates (baseline implemented)

2026-10-21 follow-up: opt-in coherent RMS anomaly detection and an isolated
candidate volume view are implemented. Both complete grid locations are used for
local execution. Commands, artifact contracts and limits are documented in
[volume_detection.md](volume_detection.md). Detection accuracy remains unvalidated
without field ground truth; candidates retain proposed status.

Implemented 2026-10-20 in package 0.4.0. The detailed
[volume and interpretation plan](volume_interpretation_plan.md) defines six
checkpoints: contracts/feasibility, supported scientific cubes, offline volume
viewer, reviewed horizons/thickness, drainage candidates, and QGIS/release
integration. It includes recommended methods, contracts, package boundaries,
commands, validation and a 6.1 Sol / xhigh implementation prompt.

Reuse M3 quantitative profiles, M4.5 support rules/viewer and M6 GIS/run adapters.
The existing 34-line and 22-line grid partitions are diagnostic display groups,
not production plots. A new explicitly local time-volume contract can support
their review without inventing a CRS or calibration. Geographic exports require
reviewed geometry/CRS; metre thickness additionally requires physical time zero
and applicable velocity evidence. Radar isosurfaces, interpreted layer boundaries
and potential drainage paths remain separate products with source/support lineage.
Implemented: M7a frozen selections/capabilities and a separate local contract;
M7b native envelope/signed/RMS cubes, per-variable stencils/masks, bounded writes
and whole-line/buffer evaluation; M7c masked cutaways, B-scans, stale-selection
handling and downloads; M7d immutable pick review, seeded tracking, supported
surfaces/shells, time intervals and evidenced finite-offset thickness; M7e analyst
apex templates, robust time-shape fits, bounded graphs and candidate review;
M7f numeric GeoTIFF/QML, GeoPackage, PLY/Zarr, reports, batches and wheel assets.

A clean wheel environment passes 92 synthetic Python tests; Node passes 12 controller
tests. Runs exercise both synthetic partitions and an explicitly calibrated depth
fixture. Commands, versions, measurements and limits are in
[volume_release.md](volume_release.md). Native QGIS style loading, direct file-browser
verification and field validation are outstanding release gates. Migration,
arbitrary resampling, absolute elevation, fitted spatial/model uncertainty and
independently validated pipe identification remain outside the baseline. New tests
require no private data.

Subsequent local M7 checks reconstructed grids 1 and 2 from 56 full profiles,
split into four observed direction groups. The updated source suite passes 93
Python and 13 Node tests; ten opt-in private tests pass with all 241 originals
unchanged. Repeated GPS fixes exposed and corrected a direction-classification
bug. All envelope/RMS voxels are conservatively excluded by clipping; supported
signed-amplitude holdout coverage is only 3–9%. Full-detail viewers are separated
by parent grid to fit the in-app transport limit. Commands, measurements and
limitations are in [real_volume_results.md](real_volume_results.md); these checks
do not establish field calibration or interpretation accuracy.

## Field metadata to collect

## M8 — gprMax4 CUDA inverse-solving experiment (M8a–M8c implemented)

Work is isolated on `gprmax`. Milestones cover CUDA verification and native-data
audit, synthetic/conditional field inverse experiments, and results with whole-line
holdouts. See [gprmax_experiment.md](gprmax_experiment.md) for scope and limitations.
Each completed milestone is committed and pushed. Existing radar cubes remain the
baseline until independent evidence supports an accuracy improvement.
M8a verified native gprMax 4.0.0 / PyCUDA 2026.1 CUDA execution and audited
13/16 candidate-region lines, with 8/10 buffered training and 5/6 held-out lines.

M8b/M8c completed 2026-10-22: 15 layered CUDA templates with three homogeneous
references, training-only signed gain/model/shift search, all candidate losses,
per-line holdout metrics, simple baselines and readable results. A separate 5 mm
CUDA check and 3D capability smoke run passed. Same-bank synthetic recovery passed;
the refined-mesh synthetic test selected a different model, documenting parameter
ambiguity. Both field fits lose to the training-mean baseline; no improved field
depth/material/volume accuracy is established. Existing cubes remain the baseline.
Commands, fingerprints, measurements and limitations are in
[gprmax_experiment.md](gprmax_experiment.md) and [gprmax_results.md](gprmax_results.md).
Verification: all 109 synthetic Python tests pass after installation; Ruff lint
and changed-file formatting pass. Ordinary tests require neither CUDA nor private
data. Completed local CUDA/fit artifacts pass manifest hash verification.

## Field metadata to collect (continued)

M7 real-data makeover: complete parent-grid reconstruction now retains crossing
directions under an explicit combination choice. Window-supported RMS replaces
whole-trace RMS exclusion by default, with the legacy policy configurable.
The offline viewer adds actual supported 3D cell geometry, cutaways and plan maps,
responsive controls, lossless payloads and explicit unavailable states. The
opt-in command is `python scripts/build_real_volumes.py --inputs <collection-run>
--output <new-run> --partition <grid-id> --combination-evidence <review-choice>`.
Tests, real-data measurements and remaining limits are recorded in
[volume_makeover.md](volume_makeover.md). This supersedes the four-direction-group
delivery above; no field calibration or interpretation validation is implied.


Plot boundaries/control/CRS, instrument settings, line spacing/direction, antenna/GNSS
offsets, fix quality and datum, timestamps/weather, sample coordinates and uncertainty,
depth intervals, laboratory particle-size methods, repeats, bulk density/moisture
context and calibration measurements. Sampling should represent each plot's
variability and support spatially separated evaluation. Exact sample counts and
sensor choices need the study design; this scaffold does not invent them.
