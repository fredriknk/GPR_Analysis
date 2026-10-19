# Data contracts

The raw RadarProfile schema `0.2`, survey configuration and assignment CSV are
validated by code. M2/M3 extend the same profile with optional located and derived
fields; M4 validates manual texture observations and audited joins. Predictions
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

## Predictions and run manifests

Predictions carry estimate, units, interval bounds/level/method, valid-support mask,
model/calibration/run IDs, depth support, acquisition period and training-domain
distance/flag. A grid adds CRS, transform, pixel interpretation and nodata.
Separate inferred properties from measured sensor attributes in filenames and metadata.

Run manifests include input hashes, resolved configuration, exact environment,
code version, parameters, seed, provenance DAG, artifact paths/hashes, QA counts,
and completion status. Version contracts and provide migrations when they change.
