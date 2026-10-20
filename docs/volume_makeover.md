# Real-data volume makeover — 2026-10-20–21

The previous delivery split each grid into direction-specific products, rejected
all native RMS traces because each contained some clipping, and called three
cutaway planes a volume. Conservative preview thinning could remove the remaining
geometry. This revision addresses those failures using the actual local inputs.

## Delivered behavior

- Each complete parent grid retains both crossing directions: 34 lines in grid 1
  and 22 in grid 2. Exact membership comes from the verified collection summary;
  neither filenames nor direction labels establish a new survey or plot.
- An explicit diagnostic combination choice is frozen in provenance. Processing,
  sample-axis and acquisition compatibility checks remain enforced. Directions,
  native identities and support weights remain available, and proximity audits
  now compare all available attributes, including RMS when envelope is absent.
- Five-sample RMS checks its complete local window. A clipped sample excludes every
  window that uses it, rather than all other samples in the trace. Trace edges,
  missing samples, unknown clipping locations and upstream spreading filters are
  excluded. `rms_clipping_policy="trace"` retains whole-trace clipping exclusion.
  Hilbert envelope still excludes clipped traces because it has nonlocal support.
- The 3D view draws boundaries of supported cells, requiring eight finite corners
  and a complete footprint inside the computational domain. Internal faces are
  removed. Threshold selects mean absolute corner amplitude; cell colour is the
  signed/nonnegative corner mean. These faces are display closures, not interpreted
  geological surfaces or smooth isosurfaces.
- Orthogonal cutaways, time/depth maps and native curtains are separate views.
  Threshold, vertical interval, opacity and contrast update the display. Native
  B-scans retain trace identity, quality flags and sample indices, with markers
  linking their recording-time axis to the selected slice/volume interval.
- Missing attributes and unavailable layer/drainage modes are disabled with an
  explanation. Empty slices explain why the view is empty. Reset selects a slice
  with maximum supported sample count. Source-track filters explicitly leave the
  frozen scientific cube intact; rebuilding requires a new Python run.
- Lossless float64/base64 payloads retain native samples without huge decimal JSON
  expansion. These real viewers use stride 1. View files retain contrast, selected
  source and vertical interval as well as the original controls and viewer hash.
  Narrow panels keep controls in a bounded scrolling area so the plot is visible.

## Local outputs

All paths below are generated, private and ignored by Git.

- Scientific run: `outputs/real-grid-makeover-final-2026-10-20/`.
- Grid 1 viewer: `outputs/grid-01-makeover-final/index.html`.
- Grid 2 viewer: `outputs/grid-02-makeover-final/index.html`.
- Reproduction TOMLs: adjacent to each output directory, with matching names.
- Verification logs: `outputs/makeover-synthetic-final.log`,
  `outputs/makeover-controller-final.log`, `outputs/makeover-real-checks.log`.
- Browser evidence: `outputs/grid-01-makeover.png`,
  `outputs/grid-02-makeover.png` and the corresponding `*-browser.json` files.

Each viewer contains an entire parent grid, not a direction subgroup. The combined
scientific run also contains an HTML viewer; the smaller standalone files are
preferable in the app browser. Keep each viewer's adjacent assets/downloads folders
with its HTML file. Scientific ZIPs contain full arrays, masks, weights and profiles.

## Numerical results

The source selection has 8,542 traces and 2,135,500 native amplitude samples. Both
cubes retain 250 recording-time samples at 0.4 ns, with no physical depth conversion.
Horizontal spacing/bin width is 0.25 m; support distance 1 m; triangle edge limit
1.5 m; track buffer 0.5 m; track breaks 1 m; minimum two supporting lines. These
are declared computational choices, not positional accuracy or sensor resolution.

| Complete grid | Cube (time × north × east) | Supported RMS samples | RMS fraction of bounding box | Supported signed samples |
|---|---|---:|---:|---:|
| grid-01 | 250 × 63 × 63 | 311,965 | 31.44% | 328,280 |
| grid-02 | 250 × 40 × 36 | 92,305 | 25.64% | 97,345 |

Envelope remains entirely unsupported in both grids. The default RMS display
thresholds produce 92,436 / 25,589 complete 3D cells, respectively. Unknown space
remains absent; no gap filling was introduced to make the volumes look solid.

Whole-line RMS holdouts rebuild training footprints and interpolation, remove
training observations within 0.25 m of the held-out line and sample every sixteenth
native vertical sample. Pooled errors use supported sample counts as weights:

| Grid | Supported / eligible samples | Coverage | Linear MAE / RMSE (ADC counts) | Nearest MAE / RMSE (ADC counts) |
|---|---:|---:|---:|---:|
| grid-01 | 17,347 / 48,821 | 35.53% | 966.0 / 1,673.5 | 1,129.6 / 1,979.6 |
| grid-02 | 2,389 / 60,943 | 3.92% | 1,244.0 / 2,218.5 | 1,433.7 / 2,682.6 |

Grid 2's holdout coverage is low. These errors describe the small supported subset,
not field-wide interpolation accuracy. They cannot be compared directly with the
previous signed-amplitude errors, which measured a different attribute/selection.

The scientific run took 138.0 s and reached 842.2 MB peak process working set.
The 512 MB configuration estimates working arrays, not total interpreter, cached
data, browser packaging and library memory. The combined payload is 67.55 MB;
standalone grid payloads are 42.50 MB and 25.05 MB.

## Verification

The installed editable environment passes **96 synthetic Python tests**, **16 Node
controller tests**, Ruff lint and Ruff format checks. Four opt-in real-data tests
pass in 30.4 s: complete parent memberships, exact native-store round trips,
independent signed/RMS voxel recomputation from exported weights, capability gates,
holdout coverage and hashes. All **241 original DT1/HD/GPS hashes** and all declared
quantitative input hashes are unchanged. Source tests require no private data.

Actual browser review used Chrome 155 through the in-app browser, with restricted
read-only loopback access to the generated HTML and local JS. Both grids render
volumes with both directions visible. Threshold extremes change the cell geometry;
interval clipping, cutaways, map navigation, empty-slice notices, native source
changes, signed/RMS switching, contrast, opacity and track filtering were exercised.
Saving and restoring a view restored its map mode and cross-direction source.
Grid 1 opened in about 3.0 s including decode/statistics; its initial plot took
541 ms, subsequent tested volume renders 261–357 ms and plan views 31–43 ms.
Grid 2 opened in 1.84 s with a 315 ms initial plot. These are individual local
measurements, not a latency distribution.

Direct `file://` browser behavior and native GIS applications were not newly tested.
ZIP integrity is covered by run manifests; the restricted QA server deliberately
does not serve download archives. No external service is needed to use the HTML.

## Reproduce

Use fresh output paths. The runner verifies the retained collection run before
reading its exact grid memberships:

```powershell
.\.venv\Scripts\python.exe scripts/build_real_volumes.py --inputs outputs/real-volume-inputs-2026-10-20 --output outputs/real-grid-review-new --partition grid-01 --partition grid-02 --combination-evidence "Joint diagnostic review of each complete grid; direction responses remain uncalibrated"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/viewer.test.cjs tests/volume_viewer.test.cjs
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
$env:SOIL_SENSING_REAL_VOLUME_RUN='outputs/real-grid-review-new'
$env:SOIL_SENSING_REAL_VOLUME_INPUTS='outputs/real-volume-inputs-2026-10-20'
.\.venv\Scripts\python.exe -m unittest discover -s tests_local -p test_real_volumes.py -v
```

For a standalone parent-grid viewer, create a TOML next to the scientific run:

```toml
schema_version = "1.0"
volume_run = "real-grid-review-new"
[preview]
partition_ids = ["grid-01"]
max_voxels = 2000000
max_native_samples = 1500000
max_payload_mb = 100.0
```

Then run `python -m soil_sensing visualize --config <viewer.toml> --output <new-view>`.
Use `grid-02` for the other complete grid. The runner uses this collection's declared
computational limits; edit the generated TOML explicitly before rebuilding other
datasets whose geometry requires different limits.

CRS/datum, production plot identity, position precision, physical time zero,
velocity and field interpretation remain missing. Joint directional pooling is a
diagnostic choice, not evidence of direction-independent antenna response. No soil
properties, calibrated depth, accepted horizons or drains have been invented.
