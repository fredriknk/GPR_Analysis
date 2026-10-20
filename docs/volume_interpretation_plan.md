# M7 — Radar volumes, interpreted layers and drainage candidates

Status: **planned, not implemented**. Prepared 2026-10-20 for implementation with
**6.1 Sol, xhigh reasoning**. This is a coding handoff, not a runtime model choice.
The requested outcome is to inspect grid partitions 1 and 2 as volumes, interpret
layers and possible drainage, and export numerical layers to QGIS.

## 1. Recommendation and scope

Build three distinct, linked products:

1. **Radar volume:** support-masked interpolation of quantitative radar attributes,
   with slices, cutaways and optional isosurfaces. Begin with the signal envelope;
   retain signed amplitude and gate RMS as separate attributes.
2. **Layer model:** reviewed reflector picks, assisted horizon tracking, supported
   boundary surfaces and the space between them. Derive vertical layer thickness
   only when the depth model and boundary meaning are supported.
3. **Drainage candidates:** diffraction detections on native radar profiles linked
   across survey lines, with reviewable paths and evidence. Show symbolic tubes
   without claiming an observed pipe diameter or material.

An amplitude isosurface is neither a soil boundary nor a physical pipe wall.
Brightness alone cannot label topsoil, subsoil, moisture, texture or drainage.
Use neutral horizon/layer IDs until cores, pits, field records or other independent
observations justify names. The first thickness deliverable is **interpreted
upper-layer thickness**; calling it topsoil thickness requires additional evidence.

Deliver offline previews first, followed by interpretation and GIS exports.
Full 3D inversion, neural segmentation and migration are not prerequisites.

| Method | Decision | Reason / limit |
|---|---|---|
| Bounded horizontal nearest/linear interpolation per vertical sample | Initial volume baseline | Reuses support rules; auditable and easy to compare |
| Hilbert envelope on each native trace | Default volume attribute | Nonnegative reflection-strength visualization; loses polarity |
| Short-window RMS | Companion slices | Reuses M3; window averaging limits vertical detail |
| Seeded dynamic-programming horizon tracking | Initial layer assistance | Reviewable continuity constraints, gaps and manual correction |
| Hyperbola/template matching plus cross-line association | Initial pipe assistance | Uses native diffraction evidence before cube interpolation |
| Velocity-aware migration | Optional later spike | Focusing depends on geometry, velocity and aperture |
| Kriging/RBF/splines | Defer | Smoothness and fitted variance are not proof of structure |
| CNN/U-Net segmentation or full-waveform inversion | Defer | No representative independently labelled field data |

SciPy supplies the analytic-signal operation used for envelopes
([Hilbert documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.hilbert.html)).
Plotly provides [volume](https://plotly.com/python/3d-volume-plots/) and
[isosurface](https://plotly.com/javascript/3d-isosurface-plots/) traces. Reuse the
offline renderer first, but verify masking and performance rather than assuming
library support satisfies our requirements.

## 2. Repository starting point

The current main GPS scene has 72 lines. Suggested partitions contain 34 and 22
lines; 16 other/traverse lines are hidden by default. Resolve membership from
exact `source_id` / `profile_id` pairs, never labels, ordering or these counts.
Keep reconstruction separate per partition; their union must not bridge
unsurveyed space. These are display groupings, not surveyed production plots.

The current field viewer shows quantitative curtains in recording time (ns).
The collection lacks reviewed receiver datum, projected CRS, plot assignment,
physical time zero and velocity calibration. Diagnostics intentionally have no
geographic grids. Introduce a separate **local diagnostic volume** contract;
do not weaken existing geographic validators or fabricate a CRS.

| Existing implementation | Reuse / extension |
|---|---|
| `visualization/scene.py`: `Scene`, `profile_signature`, `axis_values` | Compatibility checks, identity, curtains and partition metadata |
| `export/scene.py`: config validation, partition suggestions, `visualize_run` | Versioned integration of precomputed products |
| `spatial/gridding.py`, `spatial/interpolation.py` | Domain, whole-triangle, gap and contributor-distance rules |
| `processing/gpr.py` | Quantitative processing and clipping rules; never display AGC |
| `processing/depth.py`: `VelocityCalibration`, `add_depth_axis` | Homogeneous horizontal-reflector depth model within its assumptions |
| `features/picks.py`: `read_reflector_picks` | Preserve old per-profile CSV; add namespaced interpretation schema |
| `export/gis.py` | Rasterio/GeoPandas adapters and reopen verification |
| `visualization/assets/viewer.js`, `viewer.html`, `plotly_viewer.py` | Controls, rendering, view persistence and downloads |
| `runs.py`, `batch.py`, `export/report.py` | Immutable runs, hashes, cache keys and portable reports |

The current `(gate,y,x)` grid is RMS over comparatively broad gates, not a
sample-resolution cube. Do not derive fine horizons or pipe depths from screenshots,
decimated curtains or those broad gates. Read full quantitative Zarr profiles.

## 3. Capability gates and evidence

| Available evidence | Allowed result | Still unavailable |
|---|---|---|
| Current approximate raw-GPS layout and recording axes | Per-partition local time volume, time picks, candidate anomalies, local Zarr/JSON export | Georeferenced GeoTIFF, metre thickness, absolute elevation |
| Reviewed datum/CRS, survey identity, boundary and membership | Geographic time volume and time/attribute GeoTIFFs | Depth/thickness without calibration |
| Above plus time zero, antenna geometry and applicable velocity evidence | Calibrated depth volume, boundary depths and thickness | Absolute elevations without vertical reference |
| Above plus surveyed ground elevation and vertical datum | Topographic rendering and boundary elevations | Geological labels without interpretation evidence |
| Independent horizon/drainage checks | Validated interpretation within documented scope | Unchecked transfer to other sites/campaigns |

A reviewed development site may remain a development survey: do not assign it to
the four intended production plots to unlock export. A later local-to-world
transform needs independently checked control points, residuals, reviewer and
applicability. Raw GPS alone does not provide that evidence.

Preflight emits a capability report identifying missing evidence per action.
The UI explains disabled depth/GIS actions. Missing field metadata must not block
synthetic implementation or explicitly labelled local time-volume previews.

Audit line spacing/direction, duplicates, intersection discrepancies, acquisition
dates, antenna orientation/polarization and processing/settings compatibility.
Orthogonal X/Y traverses can have different responses: retain direction groups,
compare them and require an explicit combination policy. Do not silently normalize
or average incompatible acquisitions. Unknown positioning precision stays unknown.
Choose field spacing using acquisition geometry, usable bandwidth, penetration
and validation; small voxels do not increase sensor resolution.

## 4. Scientific implementation

### 4.1 Supported radar cubes

1. Freeze partition ID, exact line IDs, source hashes, position-status policy,
   acquisition compatibility and exclusions in a reconstruction selection.
2. Validate before allocation. Start with identical recording axes. Corrected
   time/depth require their evidence gates. Future mismatched-axis support needs
   explicit resampling with native sample weights, anti-aliasing policy, maximum
   vertical gap and provenance. Never silently align incompatible axes.
3. Compute `abs(scipy.signal.hilbert(trace))` along the sample axis on valid
   contiguous segments. Record padding, edge exclusions and minimum segment
   length. Hilbert/filter operations spread contamination: mask affected
   segments/traces conservatively, not just originally clipped samples.
4. Keep signed amplitude, envelope and optional RMS separate, with units/history.
   Compute envelope before horizontal aggregation; taking the absolute value of
   interpolated signed oscillations is a different operation. Display gain and
   background subtraction remain separately labelled review branches.
5. Bin within each line/location and retain contributors/weights. Interpolate
   horizontally at each vertical sample. Use nearest as baseline and linear as
   initial candidate. Never mix metres and ns in an isotropic distance metric.
6. Preserve hull, complete-triangle, exclusion, edge-length and distance masks at
   every vertical level. Reject sparse/collinear support. Require a configured
   minimum number of distinct lines, not several bins on one bent line.
7. Diagnostic coverage envelopes are computational limits in approximate local
   coordinates, explicitly unreviewed. Break at missing track sections and
   disconnected components. No domain derived from all 72 lines may fill the
   space between partitions.
8. Write chunked scientific arrays independently of display levels of detail.
   Reuse horizontal triangulations only when locations **and validity masks**
   match. Process bounded vertical blocks and measure actual memory use.

Unknown voxels are NaN plus reason-coded masks. All gridded values are estimates,
even directly above a surveyed line after binning. Preserve measured-trace overlays.
Do not close unknown holes with morphology merely to create solid-looking objects.
Retain direction diversity as a support diagnostic, not statistical confidence.

### 4.2 Horizons, layers and thickness

Start with manual seeds on full-resolution B-scans. Score reflection candidates
using chosen polarity/phase, envelope and local waveform agreement. Use dynamic
programming with vertical search bounds, maximum slope, curvature penalties and
a gap state. Record the cost definition and parameters in native time/distance
units. Trace gaps split paths. Do not jump to a stronger deeper event when a
horizon disappears.

Use multiple seeds, alternative tracks and cross-line consistency checks. Flag
ambiguous intersections rather than forcing agreement. Keep proposed, accepted
and rejected picks distinct; review edits create immutable revisions. Default
interpreted surfaces use accepted picks only. Proposed surfaces need an explicit
preview status.

Interpolate horizons using conservative spatial support. A layer needs two named
boundaries or an evidenced ground reference plus one boundary. Check ordering
per cell; crossed boundaries, excessive gaps, unresolved thin beds and unsupported
surfaces produce nodata/reasons. Zero thickness at pinch-out requires explicit
interpretation evidence; never clip negative thickness to zero.

In calibrated coordinates, vertical thickness is
`depth_bottom(x,y) - depth_top(x,y)`, positive down. It is not thickness normal
to a dipping layer. In time mode, export `horizon_time_ns` and `interval_twt_ns`;
time separation is not metre thickness. Where applicable, reuse the finite-offset
horizontal-reflector model:
`z = sqrt((v*t/2)^2 - (antenna_separation/2)^2)`,
with corrected time and unsupported shallow-path masking. Do not blindly use
`v*delta_t/2` with finite offset, layer-dependent velocity or dipping reflectors.

Propagate pick, time-zero and velocity uncertainty with documented covariance or
joint Monte Carlo draws. Shared velocity/time-zero draws remain shared between
boundaries; blindly adding variances double-counts common errors. Treat spatial
interpolation/model uncertainty separately when estimable. Unknown contributions
stay unknown; label partial uncertainty as partial, and parameter sensitivity
as sensitivity rather than calibrated confidence.

Ground-relative depth needs no invented absolute height. Topographic elevation
requires an evidenced ground surface and vertical datum. A layered velocity model
needs a separate validated travel-path adapter; defer if only the homogeneous
model is supported. Label depth as approximate under its model.

Render layer shells between supported surfaces, leaving holes open. A survey-edge
closure is a display cap, not an observed wall. Compute cubic metres only for
calibrated supported cells; report included/excluded area and do not fill the rest.

### 4.3 Drainage candidates

Detect diffraction evidence on native B-scans before volume interpolation.
Begin with analyst apex picks and bounded hyperbola/template correlation.
Optionally use robust fitting with `scipy.optimize.least_squares`; reject weak,
truncated or inconsistent events. Search ranges/thresholds are explicit study
configuration, not hidden field defaults. Without adequate geometry/calibration,
return time/apex anomalies without depth or velocity claims.

The monostatic homogeneous hyperbola is an approximation. Quantitative fits need
actual Tx/Rx geometry where offset matters. Effective fitted velocity has limited
applicability and uncertainty; one pipe fit cannot automatically calibrate a whole
soil volume. Oblique crossings and off-line targets can shift apparent apex location.

Associate detections across distinct lines using bounded plan distance, time/depth
agreement, direction and continuity. Save the association graph and alternatives.
Allow disconnected paths, junctions and crossing candidates. Require multiple
independent crossings for a linked candidate and record the configured minimum.
One detection remains a point anomaly. Evaluate orthogonal directions separately;
do not bridge unsupported gaps or connect partitions through an unreviewed traverse.

Output a documented **evidence score**, not probability without independent
calibration. Retain score components, fit residuals, contributor IDs and review
state. Negative fixtures include rocks, roots, ringing, clutter and interfaces.
A candidate does not establish diameter, material, function or absence of other
drains. Rendered tube radius is symbolic and labelled.

Migration is a separate later experiment. Compare a justified adapter-backed
method with unmigrated synthetic targets and evaluate localization/artefacts.
[RGPR's migration tools](https://github.com/emanuelhuber/RGPR) are an independent
reference candidate; record exact version, licence, assumptions and Windows
compatibility. Do not vendor code silently, use guessed velocity, or call 2D line
migration full 3D focusing. Keep migrated product identity/amplitude meaning separate.

The multisite [Koganti et al. drainage study](https://biblio.ugent.be/publication/8670389)
reports site-dependent detection and penetration. It supports a feasibility study,
not transfer of its accuracy to this collection. Validate drains against independent
plans, exposed ends or targeted field checks and horizons against independent
cores/profiles. Attractive rendering is not field validation.

## 5. Proposed versioned contracts

These are specifications to implement in `docs/data_contracts.md`, not supported
formats yet. Keep existing profile/grid/CSV readers working.

| Artifact | Minimum contents |
|---|---|
| `selection.json` | Scene/partition IDs, sorted canonical line IDs, input hashes, selection hash, position/direction policies, geometry evidence |
| `volume.zarr` | `(vertical,y,x)` coordinates with centres/bounds; separate attributes; per-variable valid/reason masks |
| Volume metadata | Coordinate mode, nullable CRS, origin/anchor, vertical basis/unit/direction/datum, survey and nullable plot IDs, processing/calibration IDs, acquisition times, source hashes, synthetic/diagnostic flags |
| `support.zarr` and contributor table | Per-voxel stencil/bin IDs and weights; nearest distance, distinct-line counts and position support; bins resolve to namespaced traces/native samples |
| `interpretation.json` and picks table | Version/revision/parent hash, object IDs, source/profile/trace/sample identity, time basis, reviewer/evidence, review status, nullable typed uncertainty |
| `surfaces.zarr` | `(horizon,y,x)` boundary time/depth/optional elevation and support/uncertainty; `(layer,y,x)` thickness or named time interval and boundary IDs |
| `pipe_candidates.json` | Paths/crossing points, coordinate/vertical basis, contributor graph, score components, review status, uncertainty and optional depth |
| `meshes/` | Vertices/faces/object IDs, units/transforms, support/parent hashes; PLY plus JSON sidecar initially |
| `evaluation.json`, `products.json`, `run.json` | Folds/metrics/coverage, product catalog/eligibility, checksums and completion evidence |

Per-variable support matters: envelope, signed amplitude and RMS can have different
validity. Separate input-quality, geometric-support, calibration and interpretation
reason codes. Distance/counts are not uncertainty. The old pick CSV requires numeric
uncertainty: add an explicit nullable field in the new schema instead of writing
zero for unknown precision. Namespacing must prevent repeated trace IDs colliding.

`coordinate_mode=raw_gps_relative` means null CRS and null production plot ID;
it cannot pass the geographic exporter. Do not overload geographic grid schema
0.1. Add new product validators/dispatch and version scene/view manifests.
Meshes in time coordinates are local visual assets, not metric physical solids.

## 6. Viewer behavior and offline exports

Extend the existing viewer with curtains, radar-volume, layer and drainage modes.
Default to one partition at a time. Add:

- X/Y/time-or-depth clipping planes, moving slices, opacity and isovalue controls.
- Linked full-resolution B-scan inspection and source trace/pick provenance.
- Horizon seeds/review editing with validated interpretation JSON import/export.
- Boundary surfaces and layer shells, thickness colours and optional labelled
  exploded view. Exaggeration/exploded offsets never change scientific exports.
- Candidate crossings and symbolic paths, with status, score and evidence.
- Support/unknown overlays and persistent recording-time or calibrated-depth labels.
- Downloads for the full scientific cube, meshes and prepared QGIS products.

Prototype Plotly volume/isosurface traces on NaN holes and disconnected domains.
If they bridge holes, use masked slices or precomputed supported meshes; do not
zero-fill missing voxels. If adopting
[scikit-image marching cubes](https://scikit-image.org/docs/stable/api/skimage.measure.html#skimage.measure.marching_cubes),
enforce validity of **all eight corners** of each contributing cell rather than
assuming its mask semantics do that. Test boundary-index cases and remove faces
touching unsupported parent cells. Add vtk.js only if measured benefit warrants
another renderer and its offline dependency footprint.

Scientific work remains in Python. Browser editing collects review decisions:
save JSON, run interpretation, then open the revised viewer. It does not silently
reconstruct volumes or refit horizons. Display thresholds can change previews,
but scientific mesh exports retain build thresholds and units.

**Selection correctness:** hiding a contributor cannot leave a volume labelled as
reconstructed from the remaining visible lines. Initially prebuild per-partition
products for supported position policies. A custom line set shows curtains and a
"rebuild required" state for derived layers, plus a downloadable reconstruction
request. Cache keys include selection, hashes, validity policy, parameters,
calibration and interpretation revision. No scientific regridding in JavaScript.

Prepare exports in Python, list them in `products.json`, and offer local links.
ZIP directory-based Zarr products for download with checksums/layout retained.
Offline HTML cannot launch Python or write arbitrary files. Unavailable exports
offer the capability reason and a request/config download, not a fake live action.

Persist selection/product hashes, cameras, attribute, clipping, opacity and review
revision in view JSON. Reject incompatible restores. Handle empty support, no
accepted horizons and no candidates explicitly. Dispose unused GPU data when
switching partitions.

## 7. QGIS products

Export numeric GeoTIFFs with matching `.qml` pseudocolour styles and a manifest.
Values remain queryable; optional RGBA quicklooks are separately named visual
products. QGIS supports these
[raster rendering/style options](https://docs.qgis.org/3.10/en/docs/user_manual/working_with_raster/raster_properties.html);
test style loading in the installed version and record it.

| Output | Pixel value / evidence requirement |
|---|---|
| `upper_layer_thickness_m.tif` | Vertical thickness between reviewed boundaries; calibrated depth |
| `horizon_<id>_depth_m.tif` | Boundary depth below local ground, one file per boundary |
| `horizon_<id>_time_ns.tif` | Boundary recording/corrected time with basis metadata; geographic frame required |
| `layer_<id>_interval_ns.tif` | Horizon time separation, never called physical thickness |
| `layer_<id>_thickness_m.tif` | Deeper layer thickness where both boundaries are supported |
| `horizon_<id>_elevation_m.tif` | Optional absolute elevation with vertical datum |
| `radar_<attribute>_<gate>.tif` | Named gate statistic, units, bounds and coverage |
| `drain_candidate_score_<gate>.tif` | Evidence-score plan projection in a named interval, not probability |
| `drain_candidate_depth_m.tif` | Depth only at supported candidate footprints; null elsewhere |
| `drain_candidate_time_ns.tif` | Time counterpart without depth calibration |
| Support/review/uncertainty rasters | Separate masks/statuses and only evidenced uncertainty |
| `drain_candidates.gpkg` | Companion crossing points and paths with IDs/status/vertical attributes |

Record the pipe-score projection rule (e.g. maximum within a gate) and vertical
support. Preserve overlapping candidates at different depths as separate objects,
gate bands or layers; one raster value cannot represent multiple pipe depths.
Do not spread pipe depths across the field. Raster footprint width is an explicit
map setting, not inferred diameter. Zero score means evaluated with no candidate;
unsearched/unknown support is nodata.

Reuse north-up affine construction and explicit increasing-y row reversal.
Validate CRS, origin, pixel centres/extents, nodata, units, vertical direction,
gate bounds and lineage. Maintain aligned lattices per partition. Resampling is
explicit and cannot create support; categorical masks use nearest sampling.
Reopen and compare values/masks/transforms without silent precision loss.
Optional overviews must respect nodata and product type.

QML styles have documented units and limits; shared scales require compatible
products. Verify known pixel values, transparent nodata and alignment with line
vectors in QGIS. Ship a bundle README explaining each layer and limitations.

## 8. Delivery checkpoints and package boundaries

Each checkpoint is a reviewable commit with updated status, commands, versions,
limitations and measured evidence. Sequence: M7a → M7b → M7c → M7d; M7e depends
on M7b/M7c and can reuse M7d review infrastructure; M7f integrates all products.

| Checkpoint | Implementation | Acceptance |
|---|---|---|
| **M7a — contracts/feasibility** | Selection/capabilities, local/geographic contracts, geometry audit, fixtures, renderer spike | Exact partition identities; only dependent actions gated; old scenes/validators unchanged; mask/performance decision recorded |
| **M7b — scientific cube** | Envelope, axis policy, partition interpolation, masks/lineage, chunked storage, CLI/evaluation | Constant/linear recovery; no bridges; safe sparse/all-invalid cases; exact reopen; original hashes unchanged |
| **M7c — volume viewer** | Offline volume/cutaways, source slices, masks, selection invalidation, downloads/save/restore | Actual browser review of both partitions, holes, policy changes and stale selections; measured offline performance |
| **M7d — layers** | Namespaced picks, assisted tracking/review, surfaces/shells, intervals/calibrated thickness | Known horizons/gaps/pinch-outs; no forced crossings; finite-offset/shared-error tests; no uncalibrated metre output |
| **M7e — drainage** | Apex/template candidates, robust fits, cross-line graph, evidence/review and symbolic paths | Crossed/oblique targets and negative clutter; no gap links; traceable detections; no probability/diameter claims |
| **M7f — GIS/release** | GeoTIFF/QML, GeoPackage, meshes/Zarr, reports/batches and synthetic demo | QGIS/browser review, round trips, wheel/offline checks, tests/CI, preview/export independence and documented field gates |

Suggested new modules under `src/soil_sensing`:

- `volumes/contracts.py`, `reconstruction.py`, `workflow.py`: schemas, capabilities,
  selection, chunking and run orchestration.
- `processing/attributes.py`: mask-aware envelope; optional later migration adapter.
- `interpretation/picks.py`, `horizons.py`, `layers.py`, `drainage.py`,
  `evaluation.py`: scientific and review functions.
- `visualization/volume.py`: renderer adapters and supported meshes/previews.
- `export/volume.py`, `export/interpretation.py`: bundles/catalog and extensions
  of existing GIS helpers, avoiding duplicate CRS/nodata logic.
- Thin `cli.py` dispatch; `configs/volumes/`, `configs/interpretation/` templates;
  deterministic fixtures/public API tests under `tests/`.

Adjust filenames to conventions while preserving responsibilities. Prefer existing
NumPy/SciPy/xarray/Zarr/Plotly/Rasterio/GeoPandas. Add scikit-image only if the mesh
spike justifies it, behind an optional extra with verified Windows/Linux locks.

### Proposed commands

These new commands/configs **do not exist yet**. Implement and verify them before
presenting them as runnable README examples. Config-relative paths and fresh output
directories follow existing conventions.

```powershell
python -m soil_sensing volume --config configs/volumes/synthetic_demo.toml --output outputs/volume-demo-001
python -m soil_sensing interpret --config configs/interpretation/synthetic_layers.toml --volume outputs/volume-demo-001 --output outputs/layers-demo-001
python -m soil_sensing interpret --config configs/interpretation/synthetic_drains.toml --volume outputs/volume-demo-001 --output outputs/drains-demo-001
python -m soil_sensing export --kind interpretation --input outputs/layers-demo-001 --output outputs/layers-gis-001
python -m soil_sensing export --kind interpretation --input outputs/drains-demo-001 --output outputs/drains-gis-001
python -m soil_sensing visualize --config configs/visualization/volume_demo.toml --output outputs/volume-viewer-001
```

Interpretation consumes the full cube and linked native profiles/picks. The viewer
config references volume/interpretation runs and prepared export bundles. Add
separate unfilled field and explicit diagnostic templates. Diagnostics consume
existing stores/frozen partition membership without CRS, and refuse GIS/depth
output with an actionable capability report. No guessed calibration or plot IDs.

Use the existing installed full environment initially and update locks only when
dependencies change. Existing validation commands:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
node --test tests/viewer.test.cjs
```

Extend the synthetic demo and batch registry without silently invalidating prior
report/cache contracts. Private field checks remain opt-in and outside CI.

## 9. Validation and performance

Fixtures need dipping/two-layer events, irregular spacing, missing strips,
clipped/dead samples, conflicting orthogonal lines, truncated diffractions,
crossed/oblique pipes and clutter. Keep analytic truth separate from observations.
Later add independently generated/forward-model fixtures to avoid testing only
the detector's own assumptions; gprMax can be an optional experiment.

| Component | Required checks/metrics |
|---|---|
| Cube | Constant/linear recovery, envelope edges/axis, nearest baseline, sample weights, per-variable masks, duplicates/collinearity/one-line support, disconnected partitions |
| Spatial evaluation | Whole-line and buffered-block holdouts; refit bins/stencils from training only; error and supported coverage together |
| Horizons | Boundary time/depth and thickness error/bias/coverage; ordering, gaps, ambiguous reflectors, pinch-outs and missing seeds |
| Drainage | Precision/recall and false detections per surveyed length/area with independent labels; localization, completeness and fragmentation; unobservable regions explicit |
| Calibration | Missing evidence, finite-offset shallow exclusions, unknown uncertainty, shared errors, layered-model rejection and datum gates |
| Viewer | Actual WebGL interactions, topology/masks, stale selection, source inspection, bad restore, empty products and offline assets |
| Exports | Centres/CRS/orientation/units/nodata, overlapping pipe depths, QML loading and immutable values under display changes |
| Runs | Version migration, path confinement, failed markers, no overwrite, cache invalidation and exact Zarr reopen |

Tune thresholds, tracking penalties, spacing and estimated velocity inside training
folds only. Reviewed validation-block picks cannot also seed/calibrate that fold.
Withhold shared supports/repeated locations and calibration evidence appropriately.
Reuse fixed independent calibration only with documented independence. Keep synthetic
software metrics separate from field validation. Do not invent a numeric field
accuracy target before acquisition geometry and reference labels are available.

Budget storage and rendering independently. Start the spike at 250,000 preview
voxels or 100,000 mesh triangles for one partition, with an explicit payload cap.
These are provisional engineering settings, not sensor resolution. Estimate values,
all masks/stencils/weights, serialization copies and renderer arrays before allocation.
One float32 variable at 100×100×250 costs 10 MB decimal before overhead; several
attributes/support arrays cost substantially more. Measure blockwise processing
on a representative larger fixture.

Retain <=5 s opening and <=250 ms slice updates on a recorded machine as viewer
targets; initially target <=500 ms clipping/opacity updates. Record runtime/peak
memory, browser/hardware, payload, counts/resolution and timing distributions.
If missed, reduce preview detail or use supported meshes/slices; never silently
coarsen scientific exports. Targets are not measurements.

Release requires clean-install synthetic reproduction, private-data-free tests,
actual offline browser/QGIS checks, and documentation of unmeasured performance
or field limits. Current field success means supported **diagnostic time volumes**
and time-domain interpretation where evidence permits. Geographic thickness and
drainage maps remain conditional on real metadata/calibration.

## 10. Copy-ready handoff

> Use 6.1 Sol with xhigh reasoning. Implement the M7 checkpoints in
> docs/volume_interpretation_plan.md, starting with M7a/M7b and the offline volume
> viewer. Read AGENTS.md, README.md, docs/architecture.md, docs/data_contracts.md,
> docs/implementation_plan.md and docs/visualization_plan.md first. Reuse the
> implemented reader, quantitative processing, support rules, run manifests,
> viewer and GIS adapters. Keep science in src/soil_sensing and callers thin.
> Grid partitions 1 and 2 are not production plots. Add an explicitly local
> diagnostic volume contract; never weaken geographic export validation. Work
> from full quantitative profiles, not screenshots/decimated curtains. Separate
> radar volumes, reviewed layers and drainage candidates. Preserve missingness,
> units, identity, support, uncertainty semantics and calibration evidence.
> Deliver synthetic features while missing field evidence gates only dependent
> products. Implement horizon review/tracking, candidate drainage linking,
> numerical GeoTIFF/QML and volume/mesh downloads in checkpoint order. Test each
> checkpoint, update docs with actual commands/results/limitations, then commit
> and push completed checkpoints on the working branch after checking its state.
> Keep raw data and generated/private artifacts out of Git. No LLM, cloud service
> or network dependency belongs in the scientific pipeline. Do not mark
> unimplemented or field-unvalidated products complete.
