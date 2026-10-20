# Real-data diagnostic results — 2026-10-20

The pipeline was run on five original DT1 recordings, comprising 23,415 traces
and 5,853,750 amplitude samples. The local, verified report is:

`outputs/real-data-results-2026-10-20-final/index.html`

It includes original radargrams, separate 20 ns display-only AGC views, GPS status
strips, quantitative window RMS curves, CSV attributes and complete quantitative
Zarr stores. The summary figures are `radargram-overview.png` and
`quality-overview.png` in that directory. Private data and derived products remain
local and ignored by Git.

## Measured results

All profiles have 250 samples at 0.4 ns intervals, spanning 0–99.6 ns recording
time. The conservative baseline removes each trace's DC mean, excluding ADC-limit
samples from that mean. No filter, time-zero correction, depth conversion, plot
assignment or geographic projection is applied.

| Recording | Traces | Missing GPS | Rejected GPS | Valid RMS, 50–90 ns | Median RMS, ADC counts |
|---|---:|---:|---:|---:|---:|
| DATA08line/Lines08/LINE00 | 700 | 0 | 0 | 601 / 700 | 3,623.3 |
| DATA08line/XLINE00 | 223 | 0 | 0 | 223 / 223 | 2,215.8 |
| Data09line/YLINE00 | 223 | 0 | 0 | 223 / 223 | 2,885.3 |
| Data09line/Lines/LINE02 | 20,002 | 540 | 0 | 20,002 / 20,002 | 1,852.1 |
| Data09line/Lines/LINE04 | 2,267 | 0 | 34 | 2,267 / 2,267 | 2,951.7 |

Every tested trace reaches an ADC limit somewhere within 20–50 ns, so every RMS
measurement in that window is excluded. In 50–90 ns, 23,316 / 23,415 traces
(99.6%) pass the existing signal checks. In 0–20 ns, 700 June LINE00 traces and
41 August LINE02 traces pass. These are window-specific quality results, not
independent field validation or sensor precision estimates.

LINE02 has 19,460 measured and two interpolated positions. Its 540 missing
positions span 111 separate intervals. The longest is traces 14,189–14,513,
approximately 141.88–145.12 m on the cumulative odometer axis. The radargram also
shows a visibly uniform interval in this region, which merits acquisition review.
LINE04 has 2,232 measured, one interpolated and 34 rejected positions; those 34
blocks carry unusable fixes. The shorter three profiles have measured positions
for every trace. No datum or positional accuracy is inferred from acceptance.

The longer June and August radargrams show substantial variation in signal
structure along the odometer axis. Display gain makes later weak signals easier
to inspect; it is never used for RMS computation. Brightness after AGC cannot
compare quantitative strength between profiles. Even the unnormalized ADC-count
statistics remain acquisition-dependent, not calibrated material properties.

## Verification and reproduction

The six opt-in integration checks passed in 49.406 s; the public synthetic suite
passed all 65 tests afterward, and Ruff lint/format checks passed.
See [local_data_checks.md](local_data_checks.md) for commands and scope.

The result run independently wrote and reopened all five quantitative Zarr stores
with exact dataset equality, checked that display processing left quantitative
values unchanged, rechecked all 241 original source-file hashes, and verified its
output hashes using `soil_sensing.export.report.verify_run`. The figures were
visually reviewed, including the GPS strips and the long-line display sampling.

The result folder contains `run.json` with source/artifact SHA-256 hashes,
environment and processing configuration, plus the exact presentation runner
`render_results.py` and `processing.toml`. To regenerate with the installed
project environment, from the repository root:

```powershell
.\.venv\Scripts\python.exe outputs/real-data-results-2026-10-20-final/render_results.py --root GPR_raw_data --output outputs/real-data-results-new
```

The output directory must not already exist. Scientific processing uses the
existing package functions; the saved runner only orchestrates the run and
presents results. All quantitative calculations retain every trace and sample.
Radargram displays retain at most 2,001 traces; LINE02 displays every 11th trace
plus its final trace. CSV rows and Zarr arrays remain complete.

An initial export encountered a Windows file-replacement error in Zarr metadata;
that run is marked failed. A fresh run completed successfully. A subsequent
presentation review corrected an empty GPS strip before generating the final
verified run above. Failed/intermediate outputs are not the delivered report.

Physical time zero, velocity, datum/control, production plot identity and
laboratory texture references remain unresolved. These outputs support review
of recorded signals and acquisition quality; they do not identify soil texture,
calibrated depths or production plot coverage. Missing GPS does not exclude a
per-trace signal attribute, but prevents its geographic use.

## Combined recorded-GPS 3D viewer

The current M4.5 diagnostic run positions all curtains from accepted raw NMEA
coordinates and groups profile centres that are connected within 1,000 m. The
verified local explorer is:

`outputs/real-data-3d-gps-grid-2026-10-20-viewer-final/index.html`

The 80 profiles are not one continuous grid. They form three disconnected scenes:
the main 72-line acquisition, a five-line site near 60.46206° N / 10.61554° E and
a three-line site near 60.35881° N / 10.46836° E. Combining all three in one axis
would put the sites tens of kilometres apart and make the main grid unreadable.
The selector keeps the sites separate while rendering every line at its recorded
relative position within its site.

The run retains 66,774 traces and 16,693,500 full-resolution signed amplitude
samples. Its GPS audit contains 65,971 measured, nine interpolated, 758 missing and
36 rejected positions. Browser geometry uses every twentieth trace and every fourth
time sample, producing 219,555 curtain vertices and a 31,444,953-byte payload.
The final viewer build took 40.0 seconds. The main grid opens first in the browser
review and contains 72 source profiles in one rotatable scene.

Coordinates are approximate small-area east/north offsets from a recorded
latitude/longitude anchor. The source did not establish a reviewed datum, projected
CRS, plot boundary or GPS accuracy, so `crs` remains null and no surface grid is
interpolated. Recording time is vertical; it is not calibrated depth. Color is
signed quantitative amplitude in ADC counts and does not imply a soil property.

Reproduce the combined layout in a new output directory:

```powershell
.\.venv\Scripts\python.exe scripts/build_real_3d_viewer.py --root GPR_raw_data --output outputs/real-data-3d-gps-grid-new
```

The all-data run saves and exactly reopens every quantitative Zarr store. The final
viewer independently hashes those 80 stores and verifies its artifacts, clustering
threshold and coordinate warning. All 67 Python tests, eight Node controller tests
and Ruff pass.

## Initial all-recording odometer 3D viewer

The M4.5 viewer was run on every valid source profile on 2026-10-20. The verified
local explorer is:

`outputs/real-data-3d-all-2026-10-20/viewer/index.html`

It contains 80 selectable diagnostic scenes, 66,774 traces and 16,693,500
full-resolution processed amplitude samples. Each quantitative Zarr store was
reopened and compared exactly before scene construction. Source and derived
artifact hashes were verified again at completion.

The browser curtains use every 20th trace and every fourth time sample, retaining
212,814 signed-amplitude vertices across the collection. This display decimation
does not alter the full stores or the 66,774 quantitative RMS observations for the
50–90 ns recording-time gate. The self-contained viewer payload is 30,195,154
bytes and took 49.1 seconds to build after processing on the recorded workstation.

The collection GPS audit represented in these stores contains 65,971 measured,
nine interpolated, 758 missing and 36 rejected trace positions. GPS status is
retained in each scene, although diagnostic curtains use independent odometer
frames because datum/control and relative placement are not verified.

Use the line selector to move among all 80 curtains, drag to rotate, scroll to
zoom, and use the top/oblique/side camera buttons. Recording time runs downward;
the signed quantitative amplitude sets curtain color. The 3D shape is a display
of radar traces along an acquisition line, not a subsurface volume or calibrated
depth model. The overview remains disabled for diagnostic lines because combining
their frames would imply an unsupported spatial relationship.

Reproduce in a new output directory:

```powershell
.\.venv\Scripts\python.exe scripts/build_real_3d_viewer.py --root GPR_raw_data --output outputs/real-data-3d-all-new
```

The resulting root `run.json`, `collection_summary.json`, per-line Zarr stores,
`viewer/run.json` and viewer artifacts form one verified immutable-input run.
