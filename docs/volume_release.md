# M7 implementation and verification — 2026-10-20

Package 0.4.0 implements the volume/interpretation baseline from
[the M7 plan](volume_interpretation_plan.md). The release verification below uses
synthetic fixtures; subsequent [local real-grid checks](real_volume_results.md)
also exercised both diagnostic partitions without establishing field accuracy.
The supplied collection has not acquired field calibration, reviewed CRS/geometry,
production plot identity or independently validated horizons/drains.

## Delivered behavior

`volume` freezes exact namespaced partition membership, hashes and policies. It
constructs native-sample signed-amplitude, Hilbert-envelope and short-window RMS
cubes using per-variable masks and bounded horizontal interpolation. It retains
native trace/sample contributors and weights, position status, flags, acquisition
UTC, processing/calibration evidence and original hashes. Vertical blocks are
written/reopened exactly. Whole-line holdouts rebuild training bins, stencils and
acquisition footprints; optional buffers remove nearby training support. Sparse,
collinear, invalid and unsupported regions remain NaN. Memory/stencil/payload
budgets fail explicitly.

The volume viewer provides offline precomputed cutaways/slices, both independent
partitions, native B-scans, source hover, threshold/opacity controls, supported
layer shells, symbolic drainage paths, camera reset, view persistence and scientific
ZIP downloads. Native profiles and scientific cubes retain their full resolution.
Custom source or position selections hide derived objects and offer a reconstruction
request. Masked slice meshes require all four corners and complete domain containment; hidden native gaps survive preview decimation; no unverified isosurface
topology is used. View changes never alter exports.

`interpret` adds nullable-uncertainty namespaced picks, immutable parent-hash review,
seeded dynamic programming with slope/curvature/waveform constraints and gap splits.
Assisted picks remain proposed. Accepted picks produce supported boundaries and
named layer intervals; contradictory exact-location picks are excluded. Calibrated
thickness uses the finite-offset homogeneous model. Unknown spatial/model uncertainty
stays unknown; evidenced partial errors share velocity/time-zero contributions.
Shell sidecars identify display caps and supported volume/area accounting.

Drainage assistance tests analyst-selected native apices against curved/flat
wavelet templates, records robust time-shape residuals and score components, and
retains alternative bounded cross-line associations. Insufficient crossings remain
point anomalies. Review revisions cannot modify detection evidence. Scores are
not probabilities. No pipe depth, velocity, diameter, material or function is
inferred from these time-domain templates.

`export --kind interpretation` adds numerical GeoTIFF/QML, GeoPackage and PLY
products using the existing GIS/run adapters. Pixel values, centres, orientation,
CRS and vector geometries are reopened/compared. Time products retain ns; metre
thickness requires evidenced calibration. Tested-apex map footprints/gates are
explicit, and unsearched regions remain nodata. Local diagnostic coordinates
cannot enter geographic exports. Reports and batches use existing hash verification.

## Reproduce

Windows/Python 3.13 installation:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-volumes-win-py313.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m soil_sensing batch --config configs/batches/volume_review.toml --output outputs/volume-review-new
.\.venv\Scripts\python.exe -m soil_sensing volume --config configs/volumes/synthetic_depth.toml --output outputs/volume-depth-new
```

For individual commands, see the README and `configs/visualization/volume_demo.toml`.
The viewer config references completed volume/interpretation runs; paths resolve
relative to that config. Choose fresh output paths and update references together.
The batch includes a portable report and does not overwrite existing artifacts.

Unfilled field examples are `configs/volumes/diagnostic_template.toml` and
`geographic_template.toml`. Supply full quantitative stores and exact
`[[partitions]]` memberships; do not identify lines by displayed labels. A diagnostic
config uses raw-GPS relative coordinates and recording time. Split orthogonal
direction groups or supply an explicitly reviewed combination policy/evidence.
Template spacing/thresholds are configuration examples, not field resolution or
precision claims. No private collection is required to reproduce synthetic runs.

Layer seeds use the JSON schema in `data_contracts.md`. Tracking produces proposed
picks for review. A later config can reference `parent_interpretation` and
`review_decisions`; accepted/rejected edits build a new immutable revision. Drainage
uses `apices_file`, explicit `[drainage]` options and `[map]` footprint/gates.
Reviewing candidates likewise references the parent `pipe_candidates.json`.

## Verification evidence

The isolated `.spikes/m7-final-clean` environment installed the built
`soil_sensing-0.4.0-py3-none-any.whl` plus the pinned Windows 3.13 snapshot.
It passed all **92 synthetic Python tests** and generated a six-job volume,
layers, drains, GIS and report release. All **12 Node controller tests** pass.
The wheel contains both old and new viewer HTML/JavaScript assets and Plotly is
bundled at export. Ruff lint/format checks are part of final verification. The
Windows/Linux CI workflow includes the new synthetic batch and controller tests;
remote CI has not been observed in this session.

Key synthetic checks cover constant/linear recovery and exact weights, holes,
disconnected domains, sparse/all-invalid/one-line input, duplicate bins, nearest
support, clipping/Hilbert edges, nullable uncertainty, stale selections, native
tracking/gaps, crossed boundaries, finite-offset/shared-error examples, independent
Gaussian-cosine diffraction waveforms, flat/ringing/truncated negatives and graph
gaps/direction separation. These do not establish real drain detection accuracy.

Verified local artifacts (generated/private artifacts are excluded from Git):

- `outputs/volume-wheel-final-2026-10-20/report/index.html`: clean wheel release.
- `outputs/volume-explorer-2026-10-20/index.html`: integrated explorer.
- `outputs/volume-depth-002/`: explicit simulated calibration/depth cube.
- `outputs/layers-depth-gis-001/`: numerical calibrated thickness/depth exports.

The clean-wheel two-partition scientific build used 2 m horizontal centres and
100 native time samples, 250,000 voxels per partition per attribute. It completed
in **20.71 s**, with **275,857,408 bytes** process peak working set (including Python
and imported libraries). The cube-only embedded preview was **4,205,538 bytes**;
scientific exports were not decimated. Process measurements are in the run manifest.
Scientific writes are blockwise; all native source profiles are resident within
the configured input budget, rather than streamed from an unlimited collection.

Actual WebGL review used the Codex in-app browser, Chrome **155.0.0.0**, on Windows
11 build 26200, AMD64 Family 25 Model 97 Stepping 2, 24 logical CPUs. A 1280 × 900
desktop viewport and the default narrow viewport were checked; the form styling
was scoped to avoid Plotly modebar overflow. GPU/RAM capacity were not measured.
Using the restricted synthetic-only loopback preview, opening to two animation
frames after the first plot took **781.3 ms**; five slice changes in the earlier browser pass took **41.6–62.0 ms**
(mean **50.6 ms**). The final initial plot took **212.5 ms**, and switching to a stale
position policy took **69.2 ms**. These samples meet the relevant opening/slice
targets on this setup; a broad performance distribution and larger field benchmark
remain future work. Browser actions verified both partitions, empty interpretation
states, layer holes/shells, drainage paths and stale custom/position selections.

Installed scientific versions include NumPy 2.5.4, SciPy 1.18.1, xarray 2026.9.0,
Zarr 3.4.1, Plotly 7.1.0, Shapely 2.2.0, pyproj 3.8.0, Rasterio 1.5.2 and
GeoPandas 1.2.0 on Python 3.13.12. The new lock is an exact installed snapshot;
older Windows 3.11 locks remain available and unchanged.

## Outstanding acceptance gates and limitations

- QGIS is not installed here. QML XML parses and numerical/vector round trips pass;
  native QGIS style loading, transparent nodata and vector alignment still require
  application review. Product manifests explicitly say QML loading is unverified.
- In-app browser security policy blocks `file://` navigation. Browser verification
  initially used a restricted read-only synthetic asset preview; the later
  explicitly requested real-grid checks used the same loopback-only asset
  allowlist approach with `connect-src 'none'`.
  Static exports embed data and use local assets, but direct file opening remains
  a manual check. The browser declined a download permission, so actual download/
  restore interactions were stopped. Controller tests cover restoration validation;
  browser downloads and file-chooser restoration are not claimed verified.
- M7 local checks now cover 56 full profiles in diagnostic grids 1 and 2, split
  into four direction groups. All ten opt-in tests pass and all 241 originals
  are unchanged. Clipping excludes all native envelope/RMS products; signed
  cubes have sparse support and only 3–9% whole-line holdout coverage. See
  [the measured results](real_volume_results.md). Geographic
  products need reviewed datum/CRS, identities and geometry; thickness additionally
  needs time zero, antenna geometry and velocity evidence. Absolute elevations,
  geological names and confirmed drains require further independent evidence.
- Conservative support may leave whole held-out lines unsupported; null errors
  and zero coverage must be reported together. Thresholds and geometry limits
  require study-specific, spatially separated selection/validation.
- Horizon interpolation has no fitted spatial/model uncertainty. Partial analytical
  error propagation needs reviewed covariance assumptions. Shared calibration is
  handled; layered/dipping travel paths need another validated adapter.
- Drainage is analyst-seeded feasibility. Independent plans/exposures, broader
  rock/root/clutter/oblique/crossing fixtures, precision/recall and localization
  evaluation remain pending. Alternative association graphs do not resolve all
  crossings/junctions into confirmed pipes.
- Optional scalar isosurfaces, general resampling, migration, absolute elevation,
  topographic rendering and automated exhaustive drain searching are deferred.
  The complete scientific cube and supported slices/shell meshes are available.
