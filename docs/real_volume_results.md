# M7 real-data grid checks — 2026-10-20

**Historical baseline.** The direction split and whole-trace RMS policy described
below are superseded by the [complete-grid makeover](volume_makeover.md), which
reconstructs two parent grids and renders supported local-window RMS volumes.

Grid partitions 1 and 2 were reconstructed from **56 full quantitative profiles,
8,542 traces and 2,135,500 native amplitude samples**. The exact memberships come
from the verified all-recording collection summary, rather than counts or labels.
These remain diagnostic groups, with null production plot, survey identity,
datum/CRS, positional precision, physical time zero and velocity calibration.
All selected recordings have the same processing/settings signature and the
recorded acquisition date 2026-08-11. No field interpretation was accepted.

## Delivered local artifacts

- `outputs/real-grid-volumes-final-2026-10-20/`: scientific cubes, per-variable
  support/stencils, complete native profiles, acquisition audits and evaluations.
- `outputs/real-grid-01-explorer-2026-10-20/index.html`: grid 1, with its two
  independent direction groups.
- `outputs/real-grid-02-explorer-2026-10-20/index.html`: grid 2, likewise separated.
- `outputs/real-volume-inputs-2026-10-20/`: all 80 imported/located/processed
  profiles, collection summary and original checksums.
- `outputs/real-grid-volume-checks-2026-10-20.log`: ten passing opt-in checks.
- `outputs/real-grid-validation-2026-10-20/index.html`: verified result summary,
  screenshots and machine-readable `validation.json`.
- `outputs/real-grid-browser-review-2026-10-20/`: browser screenshots and timings.

These private/generated artifacts and exact local configs remain ignored by Git.
Each completed scientific/viewer run has a verified artifact manifest.

## Findings and corrections

Repeated GPS fixes made the old median step vector zero, incorrectly classifying
both crossing directions as east/west. Direction now uses the principal axis of
accepted, gap-limited track endpoints, independent of travel sign. Stationary or
near-isotropic tracks remain unknown (principal variance must be at least twice
the other variance). This is a diagnostic track grouping, not antenna orientation
or position accuracy. Synthetic regressions cover repeated fixes, reversal,
stationary/ambiguous geometry and isolated rejected outliers.

Grid 1 has 17 lines in each direction; grid 2 has 11 in each. Their observed
dominant bearings are approximately 31–35°/118–120° and 21–24°/113–117° relative
to local east. They are built as four compatible subpartitions, without an
invented reviewed combination policy. Native dates, missing metadata and
namespaced source/profile/trace identities are preserved.

Every selected trace has at least one ADC-limit sample. Consequently the current
conservative native Hilbert-envelope and short-window RMS policies exclude every
trace, yielding all-NaN envelope/RMS cubes. Signed amplitude excludes individual
clipped samples; no spreading filter was applied. This differs from the earlier
50–90 ns *window RMS* product, which checks clipping only within its declared gate.
The two products must not be substituted silently. Whether a less conservative
local attribute is scientifically justified needs a separate reviewed policy.

The viewer chooses a supported attribute at opening, and explains all-missing
attributes and cutaways with no supported cells. It never fills the excluded
values. No horizons or drains were fabricated to populate interpretation views.

## Scientific support and holdouts

All cubes retain 250 samples at 0.4 ns, spanning 0–99.6 ns recording time. The
horizontal computational spacing/bin width is 0.25 m, maximum support distance
1 m and triangle edge 1.5 m. Track footprints buffer 0.5 m, with steps over 1 m
broken. At least two distinct lines must support an interpolated value. These
settings were declared for this diagnostic run; they are not established sensor
resolution, positional accuracy or optimized field parameters.

| Subpartition | Lines / traces | Cube (time × north × east) | Supported signed voxels | Fraction of rectangular cube |
|---|---:|---|---:|---:|
| grid-01-east-west | 17 / 1,832 | 250 × 58 × 60 | 188,933 | 21.72% |
| grid-01-north-south | 17 / 1,924 | 250 × 61 × 57 | 181,949 | 20.93% |
| grid-02-east-west | 11 / 2,434 | 250 × 36 × 36 | 46,137 | 14.24% |
| grid-02-north-south | 11 / 2,352 | 250 × 36 × 34 | 64,490 | 21.08% |

Envelope and RMS have zero supported voxels in all four cubes. Fractions above
include outside-footprint cells, geometric exclusions and input-quality masks;
they are not fractions of a reviewed field boundary or measures of confidence.

Whole-line signed-amplitude holdouts rebuild training bins, triangulations and
track footprints, removing training positions within 0.25 m of the held-out line.
They evaluate every sixteenth native time sample. Errors below are pooled by
supported sample count; candidate and nearest baseline use identical support.

| Subpartition | Supported / eligible held-out samples | Coverage | Candidate MAE / RMSE (ADC counts) | Nearest MAE / RMSE (ADC counts) |
|---|---:|---:|---:|---:|
| grid-01-east-west | 2,343 / 26,160 | 8.96% | 1,006.6 / 2,180.4 | 1,135.1 / 2,499.2 |
| grid-01-north-south | 1,621 / 27,452 | 5.90% | 1,298.5 / 2,641.6 | 1,511.8 / 3,151.7 |
| grid-02-east-west | 994 / 33,647 | 2.95% | 1,034.8 / 2,382.5 | 1,074.6 / 2,513.4 |
| grid-02-north-south | 2,595 / 32,710 | 7.93% | 1,450.7 / 2,989.8 | 1,744.9 / 3,649.6 |

Many held-out line/sample folds have zero coverage, with null errors. Lower errors
on this small supported subset do not establish general interpolation accuracy,
soil properties, pipe detection or field calibration. No parameter search or
field-based feature/model fitting was performed.

## Verification and browser review

Ten opt-in integration tests passed in **56.210 s**, including exact native Zarr
equality, exact parent membership, cube/support contracts, independent recomputation
of sampled voxels from exported native/bin/voxel weights, disabled geographic/depth
capabilities, holdout coverage and unchanged inputs. All **241 original DT1/HD/GPS
SHA-256 hashes** matched; cleanup repeats the original-file check. The public suite
passed **93 synthetic Python tests**, **13 Node controller tests** and Ruff.
Without explicit environment opt-ins, all ten private tests skip without reading
the collection.

The scientific run took **132.465 s**, with measured peak process working set
**623,194,112 bytes**, including interpreter/libraries and viewer serialization.
The configured 512 MB working-array estimate is not a process-wide memory cap.

Conservative stride-2 preview masks erased all horizontal faces from grid-02's
east/west group. Raising preview detail to stride 1 retained its supported cells;
scientific arrays/masks were unchanged. The combined 81,198,906-byte viewer hit
the in-app browser message-size limit, so delivered viewers contain complete
subpartitions from one parent grid each: **48,621,008 / 32,578,037 payload bytes**.
`preview.partition_ids` selects whole existing products only; it does not rebuild
or change their frozen contributors. Full B-scans remain embedded.

Both direction groups in both delivered viewers rendered in Chrome 155 on the
local in-app browser. Instrumented opening times were 664.6/910.6 ms, with initial
plot times 267.7/241.0 ms; these are individual browser measurements, not a latency
distribution or transport benchmark. Screenshots verify masked cutaways and native
B-scans. The restricted read-only loopback server exposes exact HTML/JS paths only,
with `connect-src 'none'`; downloads and file-chooser restoration were not retried.
Direct `file://` and QGIS application checks remain outstanding.

## Reproduce against the retained local inputs

The import command is:

```powershell
.\.venv\Scripts\python.exe scripts/build_real_3d_viewer.py --root GPR_raw_data --output outputs/real-volume-inputs-2026-10-20 --layout gps
```

It requires a fresh output directory. The retained
`outputs/real-grid-volumes-final-2026-10-20.toml` freezes all exact store paths and
memberships from that collection summary, split using the corrected direction
adapter. It declares `evaluation_attribute="signed_amplitude"`, a 0.25 m holdout
buffer, eight-sample write blocks and a 256-stencil cache. To reuse those inputs:

```powershell
.\.venv\Scripts\python.exe -m soil_sensing volume --config outputs/real-grid-volumes-final-2026-10-20.toml --output outputs/real-grid-volumes-new
.\.venv\Scripts\python.exe -m soil_sensing visualize --config outputs/real-grid-01-explorer-2026-10-20.toml --output outputs/real-grid-01-view-new
.\.venv\Scripts\python.exe -m soil_sensing visualize --config outputs/real-grid-02-explorer-2026-10-20.toml --output outputs/real-grid-02-view-new
```

The retained viewer configs reference the completed final scientific run; change
`volume_run` explicitly to view a new reconstruction. Set preview `max_voxels` to
1,000,000, `max_native_samples` to 1,500,000 and `max_payload_mb` to 65 per parent
grid. See [local_data_checks.md](local_data_checks.md) for test commands. On another
checkout, populate the diagnostic template with verified full stores and exact
memberships; private artifacts are intentionally absent from Git/CI.
