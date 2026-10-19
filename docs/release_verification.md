# Synthetic release verification

M6 delivers GIS exports, verified portable HTML reports, dependency-aware batches,
an explicit synthetic end-to-end demo and a Windows/Linux CI matrix. These are
software release checks. Real plot calibration and soil-texture accuracy remain
unverified because the required field metadata and reference measurements are absent.

## Reproduce locally

On Windows with Python 3.11, create a new environment and install the full lock:

```powershell
py -3.11 -m venv .venv-release
.\.venv-release\Scripts\python.exe -m pip install -r requirements-full-win-py311.lock
.\.venv-release\Scripts\python.exe -m pip install --no-deps -e .
.\.venv-release\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-release\Scripts\python.exe -m ruff check .
.\.venv-release\Scripts\python.exe -m ruff format --check .
node --test tests/viewer.test.cjs
.\.venv-release\Scripts\python.exe -m soil_sensing demo --output outputs/synthetic-release-new
```

The full lock pins 46 dependencies, including NumPy 2.4.6, SciPy 1.17.1,
scikit-learn 1.9.1, xarray 2026.9.0, Zarr 3.1.6, pyproj 3.7.2, Plotly 7.1.0,
Shapely 2.2.0, Rasterio 1.4.4, GeoPandas 1.2.0, pyogrio 0.13.0 and PyArrow 26.0.0.
It does not pin Python itself or the wheel build backend. Linux CI resolves the
declared extras separately; this Windows lock is not a universal platform lock.

The demo generates 160 synthetic lab samples with audited feature contributors,
fits/evaluates four outer spatial folds, predicts fixture supports, builds four
independent radar scenes and exports their GIS products. `report/index.html`
bundles evaluation, residual maps, the explorer, GIS files and parent manifests.
Synthetic in-sample demonstration predictions are not validation scores; the
model evaluation and residual maps use the separately held-out predictions.

The local clean verification environment is `.spikes/release-verify` (Python 3.11.4).
It installed all locked packages and the built wheel without editable source
fallback, then reproduced `outputs/synthetic-wheel-release-003/report/index.html`.
The final wheel repeated all 65 tests successfully (30.7 s) and reproduced
`outputs/synthetic-release-2026-10-20/report/index.html` after the final changes.
Ruff lint/format and all seven Node checks pass. Generated files,
environments and browser evidence remain local and are excluded from Git.

## Batch and cache checks

```powershell
.\.venv\Scripts\python.exe -m soil_sensing batch --config configs/batches/synthetic_review.toml --output outputs/batch-release-001 --cache outputs/release-cache
.\.venv\Scripts\python.exe -m soil_sensing batch --config configs/batches/synthetic_review.toml --output outputs/batch-release-002 --cache outputs/release-cache
```

The example executes one four-scene visualization, four GIS exports and one report.
The recorded first run completed all six jobs; the second reused all six verified
jobs with identical inputs, code and environment. The test suite verifies reuse, changed-input invalidation through
dependencies, corrupt-cache rejection, cycle/path rejection and failed-run markers.
Cache/output directories must be separate. Outputs cannot overwrite an earlier run.

Supported operations are `visualize`, `train`, `predict`, `export-grid`,
`export-predictions`, `report`, `process` and basic `locate`. Locate jobs use the
conservative default GPS options; use the direct `locate` CLI for reviewed CRS and
antenna-offset options, then reference its completed products in a batch. No
unreviewed CRS or antenna offset is inferred by batch execution.

## Scale and browser evidence

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_processing.py --output outputs/processing-scale-new.json
```

Recorded `outputs/processing-scale-2026-10-19.json`: four synthetic plots × 101
lines × 2,001 traces × 250 samples, 808,404 traces and 202,101,000 amplitude samples.
Sequential DC removal and three RMS gates evaluated 2,425,212 attribute rows in
131.0 seconds, at 163.5 MiB peak process working set. Input-equivalent float32
volume was approximately 771 MiB, generated one line at a time. This excludes DT1/GPS/Zarr I/O,
retained output tables and browser rendering. It is a reproducible processing
kernel benchmark, not a field hardware acquisition benchmark.

The hardware report records Windows build 26200, Intel64 Family 6 Model 85 and
20 logical CPUs. The actual explorer browser review, screenshots and timings are
documented in [visualization_plan.md](visualization_plan.md). The full scene opens
in approximately three seconds and measured slice updates meet the 250 ms target
on that setup. Rotatable single plots and shared-scale plan comparison use the
same quantitative grids. Browser memory was not measured.

## Validation and limitations

The release suite contains 65 Python synthetic tests and seven Node controller
tests, with Ruff lint/format checks. GIS checks cover native metric CRS, cell
centres, north-up row order, nodata, support masks, recording-time and calibrated
depth gates, calibration metadata and geometry/value round trips. Report checks
cover verified copying, escaped labels and rejection of corrupted inputs. Preview
checks ensure only explicitly synthetic verified assets are available.

`.github/workflows/synthetic.yml` installs Python 3.11 and Node 24, runs tests/Ruff,
builds the synthetic demo and uploads its report on Windows and Linux. CI results
are recorded after the milestone is pushed; do not infer a passing remote run
from local success alone.

No real plot assignments, datum/control, time-zero/velocity calibration or lab
reference samples were invented. Reports distinguish radar attributes, support
diagnostics and laboratory-calibrated inference. Texture GIS products are points
with depth/support metadata; no new texture surface or resolution claim is made.
PDF reports, optional sensor/pick/volume layers and topographic rendering remain
outside this release. Real field evaluation, vendor-parameter review and a specific
secondary-sensor adapter require the corresponding actual study inputs.
