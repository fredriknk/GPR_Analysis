# Soil sensing

A Python project for turning geotagged ground-penetrating radar (GPR) measurements
into traceable soil observations, then combining them with electromagnetic
induction (EMI), GRS, and manual/laboratory soil measurements. Soil texture is the
initial target; the final mix of complementary measurements is still undecided.
The intended field
campaign covers four 100 × 100 m plots. The supplied files are development data,
not an assumed representation of those plots.

**Current status:** M0–M3 tools and the manual-texture foundation of M4 are
implemented. M4.5 now adds an offline four-plot radar explorer, quantitative grids
and whole-line interpolation evaluation, with synthetic browser review and measured
interaction budgets. Import verified DT1 profiles, associate checksummed NMEA trace blocks,
audit GPS across the test collection, optionally project coordinates when datum/CRS
are supplied, and process quantitative GPR traces. Runs preserve source hashes,
trace IDs, quality flags and parameter history. Manual texture assays can be
validated and matched to located, depth-calibrated GPR attributes with an exclusion
audit. M5 adds evidenced texture training/prediction tools with spatial evaluation;
only synthetic model checks have been run. M6 adds verified GIS exports, portable
HTML reports, dependency-aware batches, a full synthetic demo and Windows/Linux CI.
No production plot assignments, field
velocity calibration, real lab texture samples or field texture predictions are present.
GRS provisionally means gamma-ray spectrometry; confirm before implementing that adapter.
All complementary sensor modules are optional and must accept new sensor types later.

## Start here

- [Architecture](docs/architecture.md): boundaries, scientific workflow, decisions.
- [Data contracts](docs/data_contracts.md): arrays, units, coordinates, sensor support.
- [Implementation plan](docs/implementation_plan.md): ordered tasks and acceptance criteria.
- [3D Plot Explorer](docs/visualization_plan.md): implemented M4.5 tools, commands, benchmark and release gates.
- [Volumes, layers and drainage plan](docs/volume_interpretation_plan.md): planned M7 reconstruction, interpretation, QGIS exports and 6.1 Sol / xhigh handoff.
- [Library assessment](docs/library_assessment.md): reuse decisions and primary sources.
- [Test-data assessment](docs/test_data_assessment.md): observations and unresolved issues.
- [Opt-in real-data checks](docs/local_data_checks.md): local binary/GPS/processing/storage regression suite.
- [Real-data results](docs/real_data_results.md): five recordings processed, radargrams, signal quality and local report.
- [Reader decision and verification](docs/dt1_reader_decision.md): GPRPy spike, timing and storage.
- [Sol / xhigh handoff](docs/sol_xhigh_handoff.md): prompt for the next implementation session.

## Run on Windows / PowerShell

The core supports Python 3.11 or newer. The original scientific environment is
locked for **Windows / Python 3.13**. The explorer additionally has a verified
**Windows / Python 3.11** lock (see below). The field lock adds pyproj to the original
processing lock for CRS validation/projection and manual sample support. From the
project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-field-win-py313.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m soil_sensing inventory .\GPR_raw_data --hash-raw --output .\outputs\inventory-run-001.json
.\.venv\Scripts\python.exe -m soil_sensing ingest --config .\configs\surveys\test_project.toml --all --output .\outputs\import-run-001
.\.venv\Scripts\python.exe -m soil_sensing gps-audit --run .\outputs\import-run-001 --raw-root .\GPR_raw_data --output .\outputs\gps-audit-001.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
```

Activation is optional. `soil-sensing` and `python -m soil_sensing` expose the same CLI.
Choose a fresh output filename for each run. The command refuses to overwrite an
existing output or write inside the source directory. Without `--output`, it emits
the manifest to stdout. Without `--hash-raw`, it inventories sizes without hashing.
`--strict` exits 1 for profile issues or orphan sidecars; other errors exit 2.
An ordinary inventory can succeed with warnings: it is not scientific validation.

For a subset, replace `--all` with one or more `--profile` options:

```powershell
.\.venv\Scripts\python.exe -m soil_sensing ingest --config .\configs\surveys\test_project.toml --profile DATA08line/XLINE00.DT1 --output .\outputs\one-line-001
```

Alternatively pass the source root directly with `--project-id` and optional
`--assignments`, instead of `--config`. Open the run's `index.html` to review the
radargrams; `run.json` records completion/failure, dependencies, hashes and quality
counts. Each line has `profiles/<relative-profile-id>/raw.zarr` and `radargram.png`.
Every saved dataset is reopened and compared with its imported data before success.

The supplied `survey_assignment.csv` is intentionally empty. Import works in
diagnostic mode with unknown survey/plot/coordinates. Explicit `include=false`
assignments are skipped by `--all` and rejected when explicitly selected. Included
assignments require a survey, evidence and reviewer. Config paths resolve relative
to the TOML file. Plot polygons remain unfilled; the processing config is executable.

Raw radargrams show **recording time**, using `arange(N) * window / N` (0.4 ns here).
The stored time-zero point is fractional and preserved without a correction because
reference readers disagree on its indexing. GPS timestamps/coordinates remain missing
in M1 artifacts; M2 adds them in separate located stores. ADC-limit and zero-signal
flags are diagnostic; they do not measure soil texture.
The reader supports little-endian int16 reflection profiles with 128-byte headers,
and rejects unsupported layouts or corrupt records. Maximum source size is 1 GiB per
profile; processing is sequential by line.

## GPS, processing and texture workflows

Use a raw Zarr profile from an import run and its original matching GPS sidecar.
The first command stores geographic coordinates and UTC without assuming a datum.
The source/test collection has mixed dates and locations, so plot coverage remains
`unassigned` until the reviewed assignment CSV and polygon configuration are filled.

```powershell
.\.venv\Scripts\python.exe -m soil_sensing locate --input .\outputs\import-run-001\profiles\DATA08line\Lines08\LINE00\raw.zarr --gps .\GPR_raw_data\DATA08line\Lines08\LINE00.GPS --output .\outputs\located-line-001
.\.venv\Scripts\python.exe -m soil_sensing coverage --input .\outputs\located-line-001\located.zarr --plots .\configs\plots\four_plots.toml --output .\outputs\coverage-line-001.json
.\.venv\Scripts\python.exe -m soil_sensing process --input .\outputs\located-line-001\located.zarr --config .\configs\pipelines\gpr_baseline.toml --output .\outputs\processed-line-001
```

When the receiver datum and projected site CRS are documented, pass both
`--source-crs` and `--target-crs` to `locate`. Optional `--antenna-forward-m` and
`--antenna-right-m` require measured offsets and usable travel headings. Latitude
and longitude without a documented datum do not authorize GIS export. The plot
TOML accepts reviewed metric polygons with evidence; the placeholders contain no
geometry. GPS QC tracks valid/invalid sentences, missing and rejected traces,
bounded interpolation, UTC, fix mode, HDOP, satellites and odometer comparisons.
HDOP is not a position error in metres; horizontal uncertainty remains unknown.

The baseline processing config removes the per-trace DC mean using unclipped
samples. Dewow, bandpass and physical time zero stay off until reviewed parameters
are supplied. Output includes raw, quantitative and display-AGC radargrams, a
quantitative Zarr store and time-window RMS attributes. ADC-limit exclusions are
evaluated in each window; filtering can spread clipping, so filtered profiles
exclude any trace with an ADC limit. Display AGC never enters the attribute table.
Depth conversion requires both an evidenced time-zero value and a
`[velocity_calibration]` TOML section with `calibration_id`, `evidence`,
`velocity_m_per_ns`, `standard_uncertainty_m_per_ns` and
`time_zero_standard_uncertainty_ns`. The homogeneous finite-offset model masks
unsupported shallow times. Reflector picks have a reviewed CSV format at
`data/templates/reflector_picks.csv`.

For future lab data, fill `data/templates/soil_measurements.csv` with all three
sand/silt/clay rows for each sample, particle-size cutoffs, method, depth interval,
CRS, UTC and closure tolerance. Validate and match with:

```powershell
.\.venv\Scripts\python.exe -m soil_sensing texture --input .\data\manual_texture.csv --output .\outputs\texture-001
.\.venv\Scripts\python.exe -m soil_sensing match --texture .\data\manual_texture.csv --features .\outputs\processed-line-001\attributes.csv --radius-m 2 --max-lag-hours 24 --output .\outputs\fusion-001
```

Matching requires the same plot/CRS, a valid quantitative feature, spatial and
time bounds, and overlapping calibrated depth support. The run writes one row per
independent sample plus a contributor/exclusion audit. With the supplied test
data, no lab samples or depth calibration exist; matching yields no soil-property
estimate. Radius, lag and depth-overlap settings are study choices, not defaults
implied by GPR.

## Offline 3D radar explorer

The deterministic demo creates four independent synthetic 100 × 100 m plots, a
known radar event near local (65, 50) m / 38 ns in the first plot, and an explicit
unsurveyed region. It never substitutes synthetic data for a missing field input.
On Windows / Python 3.11, install the tested explorer environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-visualization-win-py311.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m soil_sensing visualize --config configs/visualization/synthetic_demo.toml --output outputs/explorer-demo-001
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
```

Other supported environments can resolve `pip install -e ".[processing,field,visualization,dev]"`
and need their own verification. Open the output's `index.html` in a WebGL-capable
browser. Keep its `assets/` directory alongside it when copying the explorer;
Plotly and its MIT licence are bundled locally, with no CDN, account or server.
The viewer includes plot/session selection, shared-scale overview, signed
quantitative curtains, RMS slices, measured/GPS-interpolated position filtering,
support-distance overlay, cameras, slice playback and save/restore view JSON.
Plotly's image button exports the current view. The comparison overview uses four
full-resolution plan maps; select a plot for 3D rotation. Synthetic browser review
and measured opening/slice performance are recorded in the visualization plan.

For field data, copy `configs/visualization/plot_explorer.toml`, supply existing
located `quantitative.zarr` stores and reviewed geometry, and run the same command.
Store and geometry paths resolve relative to that TOML. RMS is re-extracted with
the M3 quality rules from quantitative stores; display decimation cannot change it.
Each scene contains one explicit plot/survey. Processing settings, amplitude units,
calibration and sample axes must be compatible. `vertical_axis="depth"` requires
existing evidenced physical time zero and velocity calibration, with metre gates.
`recording_time` and `corrected_time` use ns and never imply physical depth.

For unassigned lines, set `diagnostic=true`, omit `plots_file`, and provide up to
100 quantitative stores. The default `diagnostic_layout="odometer"` shows each
line separately. `diagnostic_layout="gps"` groups nearby lines and places their
curtains with approximate local east/north offsets derived from accepted raw NMEA
latitude/longitude. Set `diagnostic_cluster_distance_m` explicitly for that review.
GPS diagnostic scenes retain no claimed datum, CRS, plot assignment or geographic
grid. The current private test collection remains diagnostic.

To process every local DT1/HD/GPS set and build one selector containing all
diagnostic 3D curtains:

```powershell
.\.venv\Scripts\python.exe scripts/build_real_3d_viewer.py --root GPR_raw_data --output outputs/real-data-3d-gps-grid-new
```

The runner saves and exactly reopens every full-resolution quantitative store,
then decimates only browser curtain geometry. GPS layout is the runner default;
pass `--layout odometer` to retain one independent scene per line. Its output is
local and ignored by Git. See [real_data_results.md](docs/real_data_results.md)
for the verified run.

For GPS diagnostics, the runner also suggests display-only grid partitions from
short `XLINE`/`YLINE` groups with nearby recorded centres. The default thresholds
are 15 m maximum line length, 20 m centre linkage and at least four lines; override
them with `--grid-line-max-length-m`, `--grid-partition-distance-m` and
`--grid-partition-min-lines`. These groups do not assign plots. The viewer opens
with grid partitions only, offers each partition and the other/traverse lines as
presets, and provides an individual line checklist. Camera bounds and the metric
scale bar follow the visible lines.

The exporter writes `scene.json`, `evaluation.json`, `run.json`, and per-scene
`scene-01/grid.zarr` / `support.json` (no grids for diagnostic lines). Nearest and
linear estimates share hull, polygon, full-triangle, gap and contributor-distance
masks; unsupported cells remain missing. The support bins and every source trace
are retained. Evaluation refits support after withholding each whole line and
reports error **and** supported coverage. These are radar attributes, with no
texture predictions or statistical uncertainty inferred from coverage.

The checked demo at `outputs/explorer-demo-2026-10-19/` uses 360,000 grid cells
and 42,800 curtain vertices. See [the visualization status](docs/visualization_plan.md)
for the build benchmark, configurable budgets and remaining acceptance work.

## Laboratory-calibrated texture models

Install `requirements-modeling-win-py311.lock` on Windows/Python 3.11, or resolve
`.[processing,field,fusion,visualization,dev]` for another verified environment.
Fill `configs/inference/texture_baseline.toml` with the reviewed study choices and
evidence that velocity/feature calibration was independent of the target assays.
The empty evidence field deliberately prevents unattended field fitting. Use
complete laboratory triplets and newly processed quantitative attributes:

```powershell
.\.venv\Scripts\python.exe -m soil_sensing train --texture data/manual_texture.csv --features outputs/processed-line-001/attributes.csv --config configs/inference/texture_baseline.toml --output outputs/texture-model-001
.\.venv\Scripts\python.exe -m soil_sensing predict --model outputs/texture-model-001/model.json --input data/prediction_supports.csv --output outputs/texture-estimates-001
```

Training reuses the audited spatial/time/depth matcher. It requires source trace
lineage, quantitative processing signatures and velocity evidence, so old attribute
tables without those fields must be regenerated. All samples sharing a spatial
block, repeated location or radar contributor stay in one group. Leave-one-plot-out
evaluation removes shared groups and a configured spatial buffer, reserves separate
calibration groups, then fits feature selection, median imputation, scaling and
ridge tuning only within the remaining training groups. A group-mean baseline uses
the same supported held-out rows. Predictions are projected onto nonnegative
sand/silt/clay fractions summing to one; observed assays are not renormalized.

`model.json` stores numerical preprocessing/coefficients and provenance without
executable serialization. `evaluation.json`, `held_out_predictions.csv`, matched
samples and the join audit retain support/counts and per-plot errors. A generated
`prediction_template.csv` specifies future feature supports and units/signatures.
Unsupported feature/depth/CRS extrapolation stays missing. Group split-calibration
intervals remain null when too few calibration groups support their declared level;
nominal coverage relies on exchangeable groups and is not a guarantee under spatial
or campaign shift. No soil texture model is fitted to unlabelled radar amplitude.

The explicit synthetic check at `outputs/inference-demo-model-001/` uses 160
independent fixture samples across four synthetic plots. Its scores demonstrate
software behaviour only; they do not establish field accuracy or a sampling design.

`requirements-field-win-py313.lock` pins the validated M2–M4 scientific/development
environment and has been installed into a fresh verification environment.
`requirements-processing-win-py313.lock` remains the M1/M3 environment without pyproj.
`requirements-dev.lock` remains a minimal core-development option. Neither lock pins
the Python interpreter or build backend. Other platforms/Python versions can resolve
`pip install -e ".[processing,field,dev]"` but require their own compatibility checks.
The core uses only Python's standard library. For offline source execution:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
python -m soil_sensing inventory .\GPR_raw_data
python -m unittest discover -s tests -v
```

The `processing` extra enables ingestion, NMEA parsing and signal processing; the
small `field` extra adds pyproj. Heavier `geo`, `fusion` and `notebooks` extras remain
optional. GPRPy is an independent compatibility reference, not a runtime requirement.

## Reports, GIS and reproducible releases

Install `requirements-full-win-py311.lock` in a fresh Python 3.11 environment,
then install this package with `pip install --no-deps -e .`. The lock includes the
scientific, modelling, visualization, GIS and development extras. Other platforms
can resolve `pip install -e ".[processing,field,fusion,visualization,geo,dev]"`;
the synthetic CI matrix exercises Windows and Linux separately.

```powershell
.\.venv\Scripts\python.exe -m soil_sensing demo --output outputs/synthetic-release-001
.\.venv\Scripts\python.exe -m soil_sensing export --kind grid --input outputs/explorer-001/scene-01/grid.zarr --output outputs/gis-plot-01
.\.venv\Scripts\python.exe -m soil_sensing export --kind predictions --input outputs/texture-estimates-001/texture_estimates.csv --reference outputs/texture-estimates-001/model_reference.json --output outputs/gis-texture-001
.\.venv\Scripts\python.exe -m soil_sensing report --run outputs/explorer-001 --run outputs/texture-model-001 --run outputs/gis-plot-01 --output outputs/report-001
.\.venv\Scripts\python.exe -m soil_sensing batch --config configs/batches/synthetic_review.toml --output outputs/batch-001 --cache outputs/batch-cache
.\.venv\Scripts\python.exe -m soil_sensing batch --config configs/batches/synthetic_review.toml --output outputs/batch-002 --cache outputs/batch-cache
```

Use new output directories. `demo` generates explicitly synthetic inputs, trains
and evaluates the model, builds four radar scenes, exports GIS products and bundles
a portable `report/index.html` with held-out residual maps. It uses no private files.
The model's demonstration predictions reuse fixture supports and are labelled
synthetic; accuracy assessment comes from the separate held-out predictions.

GIS exports retain native metric CRS, world cell centres, gate units/calibration,
nodata/support masks and source references. Radar grids become GeoTIFFs; coverage
and texture support points become GeoPackage/GeoParquet. No texture raster or
interpolation of texture estimates is implied by the point export. Reports copy
verified artifacts into a self-contained folder, including the explorer's assets.

Batch jobs declare dependencies and reference artifacts as `@job_id/path`.
Cached results are reused only when source/configuration hashes, package code,
environment and verified completed outputs agree. Failed or incomplete runs are
never reused; corrupt completed cache entries fail visibly. Copy a generated
report folder for sharing; a cached batch manifest itself references its local cache.
See [release verification](docs/release_verification.md) for commands, measurements
and field limitations.

## Layout

```text
GPR_raw_data/                  Existing files; immutable, excluded from Git
src/soil_sensing/
  cli.py, inventory.py         Inventory and workflow entry points
  config.py, profile.py        Survey/assignment and profile validation
  ingest.py, workflow.py       Sequential runs, provenance and review artifacts
  io/                         HD/DT1 readers and lossless Zarr storage
  spatial/                    NMEA positioning and plot coverage
  processing/                 GPR signal processing and calibrated depth
  features/                   RMS attributes and reviewed reflector picks
  fusion/                     Manual texture adapter and audited matching
  inference/                  Planned calibrated estimates and validation
  export/                     Raw/quantitative/display radargrams; GIS planned
  visualization/              Validated scenes, explicit synthetic demo, offline viewer
configs/{surveys,plots,pipelines}/  Survey, plot and processing configurations
data/metadata/                Explicit survey assignments
data/templates/               Empty sensor import templates
data/interim/                 Normalized arrays and QA artifacts
data/processed/               Features, calibrated models and predictions
outputs/                     Run manifests, figures, reports and GIS exports
tests/                       Synthetic fixtures created during tests
scripts/                     Opt-in GPRPy compatibility checks
notebooks/                   Exploration; reusable logic belongs in src/
docs/                        Architecture and implementation handoff
```

No original files are moved or modified. Vendor depth slices and images are
comparison products with their own processing assumptions. No source/data license
is assigned by this scaffold; decide project licensing before redistribution.
