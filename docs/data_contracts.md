# Data contracts

The raw RadarProfile schema `0.2`, survey configuration and assignment CSV are
validated by code. M2/M3 extend the same profile with optional located and derived
fields; M4 validates manual texture observations and audited joins. M4.5 validates
attribute-grid schema `0.1`, scene inputs and offline manifests. Predictions
remain specifications for later milestones. Inventory JSON remains schema `0.1`; it contains `summary`, `files`,
`profiles`, and `orphan_sidecars`. Its raw fields retain vendor units. Null survey
and plot IDs are intentional. Production validators will reject unresolved metadata
only for operations that require it; metadata inventory must still work.

## Identity and manifest

Project → plot → survey/session → line/profile → trace → sample. A survey can include
several instruments and dates only when acquisition sessions are separately identified.
`profile_id` initially equals the root-relative DT1 path without its suffix.
The root manifest gives that identifier its namespace; when combining roots, prepend
a stable project/source ID. Never identify a line by basename alone.

`data/metadata/survey_assignment.csv` is an explicit reviewed mapping. Each included
profile gets exactly one survey assignment and, where known, one plot assignment.
`include` is `true`/`false`; blank means unresolved. Assignment evidence may be field
logs, control points, or reviewed geography. Contradictory identities fail validation.

## RadarProfile: xarray Dataset (implemented, schema 0.2)

| Field | Shape/type | Units / meaning |
|---|---|---|
| `amplitude` | `(trace, sample)` float32 | ADC counts initially; record conversion if calibrated |
| `trace_id` | `(trace,)` string/integer | Stable source identity, independent of array index |
| `sample` | `(sample,)` integer | Zero-based array index |
| `twt_ns` | `(sample,)` | Raw recording time in ns, window/N spacing; no physical time-zero correction |
| `distance_m` | `(trace,)` float64 | Along-line distance, origin and source recorded |
| `x_m`, `y_m`, `elevation_m` | `(trace,)` float64 | Projected coordinates; NaN when unavailable |
| `time_utc` | `(trace,)` datetime | UTC; missing stays missing |
| `position_status` | `(trace,)` enum | measured / interpolated / missing / rejected |
| `quality_flags` | `(trace,)` uint32 | Versioned bitmask; zero means passed checks that were run |
| `horizontal_uncertainty_m` | `(trace,)` float | Unknown is NaN, not zero |
| `recorded_position_m` | `(trace,)` float64 | Vendor odometer position, including acquisition direction |
| `trace_header_bytes` | `(trace, header_byte)` uint8 | All 128 original bytes, including reserved fields/comments |
| `vendor_time_zero_adjustment` | `(trace,)` float32 | Original per-trace adjustment; not applied |
| `vendor_seconds_since_midnight` | `(trace,)` float32 | Instrument clock; timezone unknown, separate from UTC |
| `vendor_binary_field_7` | `(trace,)` float32 | Original timing-related field; no guessed units |

Required attrs: schema_version, profile_id, source_id, survey_id, optional plot_id,
antenna frequency/separation, amplitude units, source and target CRS identifiers/WKT,
vertical datum, original HD metadata, time-axis convention, processing branch,
source checksums, and ordered processing history. Raw trace headers are stored inline
as bytes, keyed by the trace dimension; decoded fields retain vendor prefixes.
Validate rectangular shape, finite sampling
interval and monotonic time; do not invent coordinates for unlocated traces.

`distance_m` is cumulative absolute odometer movement in acquisition order and starts
at zero; `recorded_position_m` preserves its original sign/origin. The raw `twt_ns`
coordinate must never be used directly as corrected subsurface travel time. See the
[reader decision](dt1_reader_decision.md) for the unresolved HD time-zero indexing.
Raw quality flags: 1 = position missing, 2 = all samples zero, 4 = an int16 ADC extreme,
8 = vendor zero flag. An ADC extreme is a possible clipping indicator. Missing UTC
and geographic coordinates stay NaT/NaN. Source/root IDs namespace trace identities;
the original profile-relative ID is retained separately.

M2 located profiles add `latitude_deg`, `longitude_deg`, `gps_hdop`,
`gps_satellites`, `gps_fix_mode` and `gps_orthometric_height_m` on `trace`.
Unknown receiver/vertical datum is recorded as unknown; geodetic coordinates
alone are not a survey-grade CRS. `x_m`/`y_m` become finite only with an explicit
geographic source CRS and projected metric target CRS. The antenna offset is
applied only when supplied and a direction can be calculated. Horizontal
uncertainty remains NaN because HDOP has no units of metres. `position_status`
distinguishes measured/interpolated/missing/rejected, and UTC comes from NMEA RMC
dates plus the selected fix epoch. Additional quality bits: 16 = GPS invalid,
32 = duplicate marker, 64 = marker/odometer disagreement, 128 = implausible move,
256 = interpolated position. The original `POSITION_MISSING` bit remains set
for missing and rejected fixes.

M3 quantitative profiles retain the original `twt_ns` recording axis. With an
evidenced time-zero setting they add `corrected_twt_ns`; with velocity calibration
they add `depth_m` and `depth_standard_uncertainty_m`, masking direct-wave/
unsupported shallow times. `adc_limit_mask(trace,sample)` preserves original
sample-level clipping through processing. `processing_branch=quantitative` is
required by attribute extraction; display AGC is only an image array. The
homogeneous finite-offset depth model and calibration ID are attributes, not
universal soil-depth truth. Reviewed reflector picks use the CSV template with
trace/horizon identity, explicit time basis, uncertainty, reviewer and evidence.

Depth is a separate derived coordinate/product with a velocity-model ID and
uncertainty, not a replacement for original twt. Resampled traces need parent IDs
and weights. If an adapter uses `(sample, trace)`, transpose exactly once at its boundary.

## Observations and features

Canonical observations use a long table: measurement_id, sensor_type, plot_id,
survey_id, source_id, acquired_at_utc, geometry, CRS, horizontal/vertical uncertainty,
support geometry or footprint, depth interval or sensitivity-kernel ID, property,
value, unit, method, calibration_id, quality_flags, source_hash and processing_run_id.
Missing timestamps and uncertainty remain null. Uncertainty must specify whether
it is standard deviation, standard error, an interval, or a bound.

The empty CSV templates are convenient instrument-specific input formats; adapters
map these to the canonical long table. All identifiers are strings. Dates use ISO
8601 UTC; coordinates require CRS; depths are metres positive down from local ground.
Use one row per property per soil sample. Validate `depth_top_m < depth_bottom_m`
for interval samples; represent a point sensor with an explicit support type.
Validate unit compatibility instead of guessing percent versus fraction.

The implemented manual texture adapter accepts complete sand/silt/clay triplets
only, with exact `percent` or `fraction` units, positive-down depth interval,
projected metric CRS, explicit UTC, lab method, sand/silt and silt/clay cutoffs
in µm, and a declared closure tolerance. It rejects inconsistent triplets and
does not renormalize. A sample is keyed by `(survey_id, sample_id)`; unique
measurement IDs identify its three assays. `texture` writes normalized
observations and source hash. No real lab file has been imported yet.

Texture assays use properties `sand_fraction`, `silt_fraction`, `clay_fraction`
with an explicit fraction/percent unit, or `texture_class` with a named taxonomy.
Add method metadata for particle-size cutoffs, pretreatment, detection limits and
closure tolerance; retain the original lab report. Validate fractions jointly per
sample/depth/method. A mixed taxonomy or unknown cutoff blocks texture-model training.
The generic property/value contract also admits later measurements without changing
the radar schema; instrument-specific additions belong in namespaced metadata.

EMI `eca_ms_per_m` means mS/m (case is normalized in the adapter); in-phase ppt and
ppm are not interchangeable. Record coil geometry/orientation using an instrument
dictionary. GRS eU/eTh are equivalent concentrations, not direct isotope assays.
Do not invent concentrations from uncalibrated counts. A raw spectrum belongs in
its source file with energy/channel calibration and live-time metadata, not a
silently flattened CSV cell. If GRS means something else, replace this contract.

Feature rows include feature_name, value, unit, branch, time_window_ns or calibrated
depth interval, trace/support IDs, algorithm and calibration versions, and quality.
Support bounds must distinguish a measured footprint from an assumed approximation.

The implemented GPR RMS rows include recording/corrected time basis, window
start/stop, signal exclusion reason, plot/survey/source identity, projected
coordinates and UTC when known, and approximate homogeneous depth bounds only
when calibrated. A GPR time gate is not an exact soil sampling footprint.

## Fusion table

One row per target sample/support or prediction cell/depth/time. Include target
value/unit (training only), feature columns, contributing sensor IDs, spatial and
temporal offsets, support weights, missingness, and plot/spatial-group IDs.
Save a separate join-audit table for one-to-many contributors. A missing measurement
does not become a zero reading. Feature lineage must make leakage checks possible.

The current matcher produces one row per independent texture sample, median GPR
attributes and contributor counts, plus per-candidate inclusion/exclusion reason,
distance, time lag and depth overlap. It requires matching plot/CRS and explicit
radius/lag/depth tolerances; uncalibrated GPR windows cannot satisfy depth support.
It does not fit or evaluate a texture model.

## Attribute grids and scenes (M4.5, schema 0.1)

Each geographic scene belongs to exactly one reviewed `plot_id` / `survey_id`,
with a projected metric CRS and a two-element world origin. Its x/y coordinates
are local metre offsets from that origin. Explicit plot assignment evidence and
reviewer are required. Separate source roots are namespaced by `source_id` and
`profile_id`; repeated line or trace identities fail. Related scenes share a
quantitative processing signature and attribute unit before a shared-scale overview
is permitted. Scenes never silently resample mismatched time/depth axes.

`vertical_axis` is `recording_time`, `corrected_time` or `depth`. Gates are ordered,
nonoverlapping `[start, stop)` intervals in ns or positive-down metres. Corrected
time needs physical time-zero evidence; depth also needs the existing calibrated
depth model and its uncertainty. Gate RMS uses exactly the selected quantitative
samples with the M3 signal/clipping rules. A calibrated gate still represents an
approximate radar support, not an exact soil sampling volume. Flat relative ground
is explicitly labelled; measured elevation/topographic rendering is not implemented.

Per-scene `grid.zarr` is an xarray Dataset with these fields:

| Field | Dimensions | Meaning |
|---|---|---|
| `value` | `(gate,y,x)` | Linear or nearest quantitative RMS, original amplitude unit |
| `nearest_value` | `(gate,y,x)` | Nearest baseline under the identical support mask |
| `valid_support` | `(gate,y,x)` bool | Exactly the finite value/baseline mask |
| `nearest_distance_m` | `(gate,y,x)` | Distance to nearest aggregated support; may be finite outside valid support |
| `contributor_count` | `(gate,y,x)` int | Number of raw contributing traces; zero outside support |
| `support_index` | `(gate,y,x,vertex)` int | Up to three per-gate support-bin references; `-1` is absent |
| `nearest_support_index` | `(gate,y,x)` int | Per-gate nearest baseline reference; `-1` outside support |
| `gate_start`, `gate_stop` | `(gate,)` | Explicit gate bounds in `gate_unit` |
| `x`, `y` | `(x,)`, `(y,)` | Increasing local cell-centre offsets in metres |

Required attrs include schema, CRS/origin, plot/survey, gate unit and vertical axis,
attribute unit, method, processing signature, source profiles and calibration.
Produced grids also retain all grid parameters, footprint GeoJSON, geometry
evidence and run input hashes. `support_reference="support.json"` resolves next
to the store. That JSON contains per-gate bins with mean position/value, trace
counts, original trace/line IDs and measured/interpolated position statuses.
Bins use the configured local metre lattice independently within each line;
exact duplicate bin locations are combined by the mean of their original rows.
Centroids outside the valid domain are discarded conservatively.

No gridding extrapolates beyond the convex hull. The complete Delaunay triangle
must be inside the plot/region union minus exclusions, its longest edge must meet
`max_edge_m`, and every vertex must meet `max_distance_m` from the query. These
limits refer to aggregated support positions. Collinear/sparse support yields
missing surfaces, including for the nearest baseline. Grid masks/distances/counts
describe support, not calibrated precision or predictive uncertainty. Explicit
exclusion/region polygons retain evidence in the resolved run configuration.

`scene.json` stores renderer-independent metadata, source checksums/history,
parent-store hashes, quality summaries and references to grids/support files.
Decimated curtain arrays and gate observations are embedded in `index.html`;
nonfinite values become JSON `null`. Decimation is only for measured rendering,
never for grid fitting. Source trace hover retains identity and quality/position
status; depth curtains also retain sampled standard uncertainty. `view.json`
exports camera, scene identity, plot/gate, layer flags, scale limits and vertical
display factor, and can be restored only into the matching scene.

Explicit `diagnostic=true` scenes have no geographic grid. The `odometer` layout
uses odometer x and schematic cross-line y for one source line. The `gps` layout
groups nearby profile centres, derives approximate small-area east/north offsets
directly from accepted raw NMEA latitude/longitude, and records
`coordinate_mode="raw_gps_relative"`, an anchor latitude/longitude and a warning
that datum, CRS and positional accuracy are unverified. It does not project,
interpolate, assign a production plot or fill missing positions. Both layouts
preserve source position status and leave `crs` null.
Explicit `synthetic=true` scenes use the versioned deterministic generator, with
synthetic evidence/units and a visible demo badge. These modes cannot borrow field
geometry implicitly. Extra sensors and soil predictions are not synthesized.

Optional `diagnostic_partitions` contain a unique `partition_id`, display `label`,
`default_visible` Boolean and nonempty `lines` list of exact `[source_id,
profile_id]` pairs. A line can occur in only one partition, and every reference
must be among the selected input stores. Scene metadata resolves each partition
to canonical JSON `line_id` values. Unlisted lines remain explicitly
`unpartitioned`; if a scene has no default-visible partition, its first available
partition is shown so a remote site never opens empty. View JSON can preserve a
custom set of selected line IDs.

`evaluation.json` records each whole held-out line, training line count, held-out
and supported counts/fraction, and candidate/baseline MAE, RMSE and bias in the
attribute unit. Unsupported folds have null errors. Binning and triangulation
are fitted again in every fold, with no held-out contributors. Metrics are
descriptive interpolation checks, not soil-property validation.

## Predictions and run manifests

M5 implements fraction models, not texture-class taxonomy. The M4 matched rows now
include metric sample coordinates/CRS, UTC, lab method/cutoffs, closure tolerance,
target source hash and a JSON `lineage` map from each feature to its source/root,
profile and trace IDs, processing signature, time basis and evidenced velocity
calibration. M3 RMS extraction supplies those signature/evidence fields. Missing
lineage is tolerated for diagnostic matching, but rejected for model training.

Model config schema `0.1` requires explicit selected gates, spatial block size,
buffer, repeat-location tolerance, matching limits and independent-calibration
evidence. Target fractions are converted from percent to fraction explicitly;
measured closure is never silently normalized. Connected blocks/locations/shared
contributors define independent spatial groups across repeated dates/depths.

`model.json` schema `0.1` stores median imputation, active feature mask, scaling,
ridge coefficients/intercepts, constrained-output algorithm ID, feature units and
processing/calibration signatures, training domain, target lab definition, proper
training and calibration identities/groups, tuning results, interval method/level,
source hashes and model identity. It contains no executable pickle/joblib payload.
The group calibration radius is the `ceil((G+1)*level)`-th ordered maximum residual;
if this rank exceeds G, radius/intervals remain null. Bounds are componentwise
fraction intervals, with simultaneous nominal group coverage only under the stated
exchangeability assumption. They are not measurement uncertainty estimates.

Future prediction support CSV rows carry support/plot/survey IDs, metric x/y/CRS,
UTC, positive-down depth interval and the selected feature values, units, processing
signatures and contributor counts. Missing features remain missing and use only
training-fitted imputation. All-selected-features-missing, out-of-domain features,
untrained CRS or unsupported depths mask the estimate. Acquisition-period changes
are explicitly flagged. Estimates include fractions, interval bounds/status, domain
distance, missingness, support validity, model ID and synthetic status. Synthetic
model runs stay explicitly labelled; none are substituted for field calibration.

Outer evaluation reports each held-out plot, sample and independent-group counts,
supported fraction, candidate/group-mean errors in percentage points, and empirical
joint interval coverage aggregated by independent group. Unsupported folds state
their reason; absent supported calibration does not become an invented interval.

Predictions carry estimate, units, interval bounds/level/method, valid-support mask,
model/calibration/run IDs, depth support, acquisition period and training-domain
distance/flag. A grid adds CRS, transform, pixel interpretation and nodata.
Separate inferred properties from measured sensor attributes in filenames and metadata.

Run manifests include input hashes, resolved configuration, exact environment,
code version, parameters, seed, provenance DAG, artifact paths/hashes, QA counts,
and completion status. Version contracts and provide migrations when they change.

## GIS, reports and batches (M6)

GeoTIFF attribute exports retain the grid's metric projected CRS and world origin.
The affine transform describes north-up cell areas; stored increasing-y grid rows
are reversed explicitly. Each band carries gate start/stop/unit, and dataset tags
carry attribute units, vertical-axis meaning, synthetic status and metadata reference.
Float64 attribute/baseline/distance/count rasters preserve values exactly; missing
attributes are NaN. `valid_support.tif` uses 0/1 and reserves 255 for nodata.
Zero contributor count remains a valid diagnostic value for unsupported cells.
`grid_metadata.json` and `support.json` retain processing/calibration, sources and
trace/bin contributors. Grid spacing is a product setting, not sensor resolution.

`coverage.gpkg` stores the configured domain and per-gate supported-cell polygons;
the latter also have GeoParquet output when nonempty. An empty supported domain
does not become an invented polygon. Texture GeoPackage/GeoParquet products contain
point supports, positive-down depth bounds, model identity, lab definition reference,
fractions, interval bounds/status and domain/missingness flags. Different CRSs use
separate files. Unsupported estimates remain missing. Exports reopen raster/vector
products to verify values, geometry, CRS and transforms; no silent reprojection occurs.

Reports accept complete runs only and verify their declared artifact checksums
before copying them, rejecting escaping paths and symbolic links. The report
copies the artifacts and parent manifest, links bundled explorers/evaluation,
and plots held-out residuals in percentage points. Its own run records parent
hashes and a default 200 MiB input bundle budget. Synthetic status stays visible;
radar attributes are distinguished from laboratory-calibrated inference. A report
is a portable artifact bundle, not a claim that missing field evidence exists.

Batch TOML schema `0.1` contains unique `jobs` with `job_id`, `operation`, optional
`depends_on`, and operation-specific input paths. Relative paths resolve from the
TOML. `@job/path` references must name a completed declared dependency and stay
within that run. Cycles/unknown jobs fail. Cache fingerprints include resolved
input content, referenced scene geometry/profiles, configuration, code and exact
environment. Each cache execution gets a new directory; only complete, matching,
hash-verified runs are reused. A corrupt completed entry fails instead of silently
being trusted. Batch manifests record jobs, paths, cache keys and reuse status.
