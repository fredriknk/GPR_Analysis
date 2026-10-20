# Coherent volume anomaly review — 2026-10-21

The complete-grid runner now enables an exploratory RMS anomaly search on the
full scientific lattice. For each recording-time slice it subtracts the median
supported RMS and divides by `max(1.4826 MAD, 0.1 median)`. Slices with fewer than
20 supported samples or zero scale remain unscored. This derived score never
replaces quantitative RMS or uses display gain.

A candidate cell requires eight supported corners, a complete footprint inside
the reconstruction domain, and a mean corner score of at least 3. Six-neighbour
connected components with at least eight cells are retained. These are explicit
exploratory defaults, not field-calibrated sensitivity or independent observations.
No gap filling, morphology closing or depth conversion is performed. Broad
horizontal responses can be suppressed by the slice-relative baseline.

Enable or override the detector in a volume configuration:

```toml
[detection]
threshold = 3.0
minimum_cells = 8
minimum_slice = 20
```

`anomalies.json` retains per-slice baselines, settings, selection hash, proposed
component IDs, counts, scores and bounds. `anomaly_labels.npz` stores integer cell
labels on `(vertical-1, y-1, x-1)`; zero means no retained candidate, not surveyed
absence. Index bounds are half-open cell ranges; physical bounds use enclosing
lattice vertices. Existing volume/support/native files resolve exact contributors.
The run manifest hashes both artifacts; scientific downloads include them.

Full-detail viewers add **Coherent 3D anomalies** and a candidate selector. Selecting
a candidate isolates its supported cells and sets the vertical interval; the
native B-scan displays corresponding interval markers and opens the nearest
source track to the candidate centre for inspection. That proximity is not
independent confirmation. The colour remains original
RMS in ADC counts, with its display scale following visible anomaly cells so that
late-time responses remain legible. Saved views preserve the candidate. Horizontal 3D aspect now
respects the east/north span ratio. When preview thinning is necessary, anomaly
view is disabled rather than relabelling or merging full-resolution components.

Reproduce both actual grid locations from the retained verified collection:

```powershell
.\.venv\Scripts\python.exe scripts/build_real_volumes.py --inputs outputs/real-data-3d-grid-partitions-2026-10-20 --output outputs/grid-detection-new --partition grid-01 --partition grid-02 --combination-evidence "Joint diagnostic review of both complete grid locations; directional response is uncalibrated"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/viewer.test.cjs tests/volume_viewer.test.cjs
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

These detections describe coherent radar responses only. They do not identify
pipes, material, soil properties or physical volumes. Missing CRS, datum, position
precision, time zero and velocity remain missing. Ground-truth detection accuracy
has not been measured. Whole-line interpolation errors do not validate detection.

## Local execution

The new scientific run is `outputs/grid-detection-final-2026-10-21/`, with
standalone viewers at `outputs/grid-01-detection-review/index.html` and
`outputs/grid-02-detection-review/index.html`. The two complete partitions retain
34 and 22 profiles, respectively. Both directions are pooled only for diagnostic
review, with unchanged acquisition and support metadata.

| Grid | Eligible complete cells | Candidates at score 3 | Retained cells |
|---|---:|---:|---:|
| grid-01 | 262,473 | 25 | 1,064 |
| grid-02 | 71,192 | 9 | 595 |

A descriptive threshold sensitivity check (minimum eight cells unchanged) yielded
78 / 25 / 13 candidates for grid 1 and 23 / 9 / 9 for grid 2 at score thresholds
2 / 3 / 4. Counts are threshold dependent; this is not a tuned operating point,
false-positive estimate or ground-truth validation. The sensitivity output is
`outputs/detection-sensitivity.json`.

The first attempted run, `outputs/grid-detection-2026-10-21/`, is marked failed
after Windows denied an atomic native Zarr write. It is retained for diagnosis;
use the final run and standalone viewers above.

Verification: 100 synthetic Python tests and 18 Node controller tests pass, as do
Ruff lint/format and whitespace checks. Five opt-in real-volume tests pass in
84.2 seconds, including independent recomputation of every retained cell score,
valid eight-corner support, exact memberships/native stores, holdout reporting
and unchanged quantitative inputs and all 241 original DT1/HD/GPS hashes.
Logs are `outputs/detection-tests-final.log` and `outputs/detection-real-checks.log`.

The scientific run completed in 284.8 seconds with 1.12 GB peak process working
set and an 81.43 MB combined viewer payload. Detection currently materializes
the full score/label lattice; the reconstruction working-array budget is not a
process-wide memory limit. Larger grids need additional memory budgeting before
using this detector. The separate grid viewers avoid the combined browser payload.

Actual in-app browser review verified both grids' volume and anomaly modes,
25/9 candidate options, candidate isolation and corresponding native source/time
selection. Grid 1 candidate 16 retains 275 cells at 66.8–77.2 ns; grid 2 candidate
4 retains 194 cells at 48.8–65.6 ns. The latter view is exported as
`outputs/grid-02-anomaly-preview.png`. Browser review prompted the visible-anomaly
colour-scale correction. Direct file-browser and geographic application behavior
were not tested in this follow-up.
