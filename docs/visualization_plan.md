# M4.5 — Interactive 3D Plot Explorer

Status: core implementation and synthetic scientific/controller checks complete;
browser visual review and interaction performance are pending release gates.
This intermediate milestone follows M2/M3 and precedes M5 texture modelling. It
does not depend on receiving EMI, GRS or laboratory data. Those remain future
optional layers; no sample markers or extra sensor layers have been implemented.

## Experience to build

Open a local, shareable HTML explorer and see a plot as a rotatable 3D block:
survey tracks on top, radar curtains descending along each track, and a movable
horizontal slice showing how radar attributes vary between measurements. A plot
selector switches between the four 100 × 100 m plots. A four-panel overview uses
the same attribute scale and slice interval to make differences easy to compare.

Use a restrained dark background, bright survey tracks, readable metre axes and
perceptually ordered attribute colours. Signed radar amplitude uses a diverging
scale. Include top, oblique and side camera presets, reset view, a scale bar,
legend, and an always-visible time/depth label. Default to curtains plus one slice;
avoid an opaque cube that hides the measurements.

| Control | Behaviour |
|---|---|
| Plot and campaign | Select one reviewed plot/session; never merge unrelated surveys implicitly |
| Layer toggles | Boundary, tracks, radar curtains, interpolated slice, coverage, sample locations |
| Slice slider / play | Move through recording-time gates or calibrated depth intervals; update the interval label |
| Measured / interpolated | Compare acquired traces against the gridded interpretation |
| Attribute selector | Start with quantitative RMS; add envelope after a validated extraction step |
| Coverage overlay | Show unsupported cells as gaps and support distance as a separate layer |
| Hover | Show units, interval, source line/trace for measurements, and method/support for grid cells |
| Vertical exaggeration | Explicit displayed factor; defaults cannot imply a physical time-to-depth conversion |
| Save view | Export an image and preserve camera, layers, limits and slice selection in scene metadata |

Later optional layers include manual sample depth intervals as core markers,
EMI/GRS readings as located points or surfaces, reviewed reflector surfaces, and
M5 texture estimates with their own uncertainty layer. Sensor values retain their
own units and support; unrelated quantities never share one colour scale.

## Rendering and library decision

Use **Plotly** for the first release: Python builds scene arrays and a small local
HTML/JavaScript controller handles slice, layer and plot controls. Bundle Plotly
locally so the result works offline without a cloud account or CDN. Keep the
controller limited to viewing precomputed products; scientific processing stays in
Python. Use existing NumPy, SciPy, xarray, Zarr and pyproj dependencies for data work.

Plotly already provides [3D surfaces and other 3D traces](https://plotly.com/python/3d-charts/)
and [volume rendering](https://plotly.com/python/3d-volume-plots/). Start with surfaces,
curtains and slices; add an optional translucent volume or isosurface only after
the preview benchmark passes. This avoids making volume rendering a prerequisite
for a useful 3D map.

Use SciPy's [scattered interpolation tools](https://docs.scipy.org/doc/scipy/tutorial/interpolate.html)
and spatial indexing rather than adopting a second GPR processing framework.
Do not introduce kriging initially: a fitted variogram and defensible validation
would be additional scientific work. A later local
[RBFInterpolator](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.RBFInterpolator.html)
comparison can use bounded neighbours; global RBF memory grows quadratically.

If measured browser performance blocks volume/section interaction, benchmark
[PyVista](https://docs.pyvista.org/user-guide/plotting.html) with
[trame](https://docs.pyvista.org/api/plotting/trame.html) against the same scene
contract before adding a server-backed viewer. It is a fallback, not a second MVP.
The `visualization` extra now contains Plotly and Shapely. Plotly 7.1.0 (bundled
plotly.js 4.1.1) and Shapely 2.2.0 were installed on Windows/Python 3.11.4;
`requirements-visualization-win-py311.lock` pins the tested scientific/dev environment.
Plotly's [MIT licence](https://github.com/plotly/plotly.py/blob/main/LICENSE.txt) is
copied into offline assets; Shapely uses
[BSD-3-Clause](https://github.com/shapely/shapely/blob/main/LICENSE.txt). The browser
renderer itself has not yet been visually reviewed on this machine.

## Geometry and honest vertical axes

Production scenes require reviewed plot assignments, plot polygons and a confirmed
projected metric CRS. Render local east/north offsets for numerical stability while
retaining the world-coordinate origin and CRS. Each plot gets its own scene;
separate sites are not placed adjacent to one another as if surveyed together.

Without velocity and physical time-zero calibration, show a **recording-time
curtain/block**, with vertical axis in ns. It is a 3D visualization of radar data,
not a physical subsurface volume. Calibrated mode uses the existing depth conversion
and retains calibration identity and depth uncertainty. Profiles with incompatible
time axes or processing settings must be resampled through an explicit reviewed
step or rejected, not silently stacked.

Use a relative ground plane at zero unless measured elevations or a compatible DEM
and vertical datum are provided. Label that plane as assumed flat. With elevation,
render calibrated depth below each local ground elevation; never invent topography.

The current test collection has no confirmed production geometry or depth
calibration. Build the initial complete visual demo from clearly labelled synthetic
100 × 100 m plots with known reflectors and deliberate coverage gaps. Existing real
lines can supply time-domain line previews; geographic plot scenes wait for reviewed
CRS and assignments. This keeps visual development moving immediately.

## Interpolation specification

1. Select a single plot, campaign, processing branch, attribute and compatible
   time/depth gate. Exclude missing/rejected positions and invalid signal windows.
   Preserve measured versus GPS-interpolated position status independently of
   attribute interpolation status.
2. Aggregate dense along-track observations into configurable spatial support bins
   before gridding, retaining contributor counts and IDs. This prevents dense trace
   spacing from masquerading as dense cross-line coverage. Define duplicate-location
   handling and refuse contradictory metadata.
3. Start with nearest-neighbour as a baseline and SciPy linear Delaunay interpolation
   as the default candidate, independently within each gate. Collinear or too-sparse
   inputs produce a coverage message and measured curtains, not a fabricated surface.
4. Clip to the plot polygon and valid acquisition footprint. Apply configurable
   maximum contributor distance and triangle-edge/gap limits; keep unsupported cells
   NaN. Respect explicit exclusion polygons and disconnected survey regions. Do not
   bridge a flagged gap simply because it lies inside the overall convex hull.
5. Record nearest-support distance, contributing support count, interpolation mask
   and method. These are coverage diagnostics, not statistical confidence intervals.
   Do not extrapolate beyond the supported footprint in the default product.
6. Grid RMS or another documented quantitative attribute. Display AGC may colour a
   measured curtain when labelled, but must not feed quantitative gridding. Signed
   oscillating waveforms are not the default interpolated volume because phase and
   acquisition geometry can cancel real features.
7. Validate by withholding entire lines or spatial blocks, rebuild the grid without
   them, and score only supported held-out observations. Report MAE/RMSE in attribute
   units, held-out counts and coverage fraction against the nearest baseline. Use
   synthetic known fields to measure recovery and gap handling. Choose spacing and
   masks from acquisition spacing and validation, not visual smoothness alone.

Horizontal gridding happens in metres within each gate. Never apply an unscaled
3D distance metric that mixes metres with nanoseconds. Depth/time interpolation,
if needed for a common grid, is a separate explicit operation with a maximum gap;
invalid windows remain masked. An interpolated radar attribute is not a soil texture
prediction. M5 will provide separately named calibrated texture layers later.

## Package and product contract

Implemented package boundaries:

| Location | Responsibility |
|---|---|
| `spatial/gridding.py` | Grid geometry, polygon/footprint masks, support bins |
| `spatial/interpolation.py` | Candidate methods, support limits, held-out evaluation |
| `visualization/scene.py` | Validated renderer-independent scene and layer metadata |
| `visualization/plotly_viewer.py` | Curtains, slices, cameras and controls |
| `export/scene.py` | Offline HTML, assets and scene manifest |
| `configs/visualization/plot_explorer.toml` | Grid, gates, support limits, layer defaults and render budgets |

Define a versioned xarray grid with dimensions `(gate, y, x)`, gate bounds and units,
attribute values, valid-support mask, distance and contributor counts. Use either
recording-time gates or calibrated depth gates per product. Retain CRS, local
origin, plot/session IDs, processing/calibration hashes and source contributor
references. Store quantitative grids in Zarr, separate from decimated scene arrays.
Extend `docs/data_contracts.md` when implementing the validators.

Implemented CLI: `soil-sensing visualize --config <scene.toml> --output <new-run>`.
The configuration references processed stores and reviewed geometry; the command
builds grids and an offline explorer without modifying inputs. Output includes
`index.html`, local assets, `scene-01/grid.zarr` and `support.json` for each
geographic scene, `scene.json`, `evaluation.json` and `run.json`. Diagnostic
odometer scenes have no grids. No-overwrite and source/artifact hashes are enforced.
`configs/visualization/synthetic_demo.toml` is the explicit synthetic entry point;
`plot_explorer.toml` is an intentionally unfilled field configuration example.

Use a configurable grid with a 1 m synthetic preview as the initial demonstration,
not a claim of 1 m field resolution. A 100 × 100 cell grid with 40 gates is about
400,000 values per attribute before masks. Cap rendered cells/curtain samples and
total payload, decimate only for display, and render only the selected plot and
slice at full preview detail. Reject over-budget requests with an actionable
message. Benchmark the four-plot payload before deciding whether loading separate
local assets needs a small localhost server; the MVP should open offline directly.

## Delivery checkpoints

### M4.5a — Visual prototype

Build a deterministic four-plot synthetic fixture, ground plane, boundaries,
tracks, measured curtains, cameras and slice controls. Include a buried synthetic
feature and a deliberate unsurveyed region. Review screenshots and interactions
in the actual browser, including labels, colour bars and synthetic-data badges.

Acceptance: rotate/zoom/reset, switch four plots, move a slice and toggle layers;
the saved HTML works offline. The known feature appears at its expected synthetic
location. No field metadata is invented. This is the first visible delivery.

### M4.5b — Connect existing processing products

Validate scene inputs, connect located quantitative profiles and RMS attributes,
and preserve provenance. Implement explicit time versus calibrated-depth modes,
quality overlays, measured gaps and plot selection. Add optional generic observation
markers using the existing M4 contracts; missing extra sensors remain harmless.

Acceptance: source trace hover is traceable, invalid positions stay absent, depth
mode rejects absent calibration, and real unassigned test lines remain diagnostic
line views. Synthetic metric fixtures test the complete geographic path.

### M4.5c — Supported interpolation

Implement baseline/linear interpolation, masks, spatial holdouts, evaluation output
and measured/interpolated comparison. Add gridded slices and optional volume preview
after performance measurement. Persist support information alongside each grid.

Acceptance: tests cover constant/linear fields, withheld lines, collinear input,
duplicate locations, polygon holes, disconnected coverage, long triangles, invalid
windows and incompatible axes/units. Smooth-looking output cannot bypass masks.
Report error and supported coverage together; do not assert universal accuracy.

### M4.5d — Four-plot review release

Add shared-scale overview, exportable views, offline packaging, dependency lock and
end-to-end example. Benchmark a representative four-plot synthetic scene on the
development machine and record browser/hardware/payload/memory measurements.

Acceptance targets: preview opens within 5 seconds and slice changes within 250 ms
on the recorded benchmark setup; tune render budgets if missed. These are targets,
not current measurements. Confirm keyboard-operable controls, visible units and
legends, camera reset, useful empty states, and no CDN/network requirement. Run
the existing synthetic suite and Ruff plus new grid/scene tests. A clean environment
reproduces the demo and its manifest. Production acceptance additionally requires
the real plot metadata and calibration appropriate to the chosen mode.

## Completion boundary

M4.5 delivers a compelling, reproducible 3D radar/measurement explorer and audited
interpolation. M5 still owns soil texture inference and predictive uncertainty.
M6 reuses these scenes in field reports and GIS exports. A survey-planning editor,
cloud service, live acquisition dashboard and full 3D geophysical inversion are
outside this intermediate milestone.

## Implemented behaviour and limitations (2026-10-19)

The offline directory embeds all scene arrays in HTML and bundles Plotly locally;
there is no fetch, CDN, cloud account or scientific browser processing. The viewer
has plot/session selection, an overview of up to four independent local frames,
curtains, measured RMS points, interpolated slices, a distance overlay, camera
presets, playback, vertical display factor and view JSON save/restore. Plotly's
image-export button is present; image export and visual layout remain unverified.
The first plot's synthetic packet is centred near (65,50) m / 38 ns; the default
gate contains it. Subsequent plots shift that event by 4 ns per plot.

Field scenes require existing quantitative stores and reviewed assignments and
geometry. RMS is re-extracted through the M3 function rather than trusting an
unverified external feature table. Depth gates select existing calibrated samples,
retain calibration/uncertainty, and never silently resample. Recording/corrected
time gates are explicitly distinguished. Elevations remain in parent stores; this
release renders an assumed flat relative ground plane. Optional observation/pick
layers, a volume preview and topographic correction remain deferred.

Nearest/linear use per-line mean support bins; exact coincident bins combine
original contributors by their mean. Missing/rejected positions and invalid
windows are excluded. Whole-triangle containment in the polygon/region union
minus exclusions prevents bridges over holes and disconnected regions. Both
methods share hull, longest-edge and maximum vertex-distance masks. Missing
positions split curtains before display decimation. Small gaps without explicit
exclusions are governed by those spatial limits; no measured antenna footprint
or GPS accuracy is assumed. Validation refits bins/triangles after withholding
whole lines, and unsupported folds have null errors.

The default field budget allows 10 million input amplitude samples, 100,000
trace-gate observations, 150,000 curtain vertices and 30 MiB of data-plus-Plotly
payload. Input sizes are checked using Zarr metadata before loading field arrays.
The grid cell budget applies across scenes. The demo tightens these to 400,000
cells, 60,000 vertices and 20,000 observations. Display sampling never affects
scientific grids; over-budget runs fail and retain a failed manifest.

Reproduce a backend benchmark in a new output directory:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_explorer.py --config configs/visualization/synthetic_demo.toml --output outputs/explorer-bench-001 --report outputs/explorer-bench-001.json
node --test tests/viewer.test.cjs
```

The benchmark reports process peak working set and build wall time, including
import-independent scene generation, gridding, holdouts, Zarr write/reopen and
HTML packaging. Browser open and slice timings stay null until measured. The
recorded setup is Windows build 26200, Python 3.11.4, Intel64 Family 6 Model 85,
20 logical CPUs, using the visualization lock. Detailed CPU model queries were
unavailable. The reference run has four 100 × 100 m scenes, 36 lines, 9 gates,
360,000 grid values and 42,800 decimated curtain vertices. See
`outputs/explorer-demo-benchmark-2026-10-19.json` for measured build/memory/payload
numbers: 32.9 s build time, 320.3 MiB peak process working set and 20.3 MiB of
data-plus-Plotly payload. These do not establish browser performance.

Across its 324 whole-line/gate folds, 11,124 of 14,904 valid held-out observations
are supported (74.6%). Pooled descriptive errors are linear MAE 1.471 / RMSE 5.601
and nearest MAE 3.330 / RMSE 13.166 synthetic ADC counts. These numbers describe
only this deterministic demonstration; adjacent-line correlation and hand-chosen
synthetic structure limit any inference beyond the fixture.

Acceptance evidence: 51 Python synthetic tests pass after installation from the
lock in a fresh environment, including after installation of the built wheel;
six Node controller tests cover masking geometry,
slice/plot/camera controls, shared scales, GPS-position filtering, support units
and diagnostic empty states. Ruff lint/format checks pass, and a wheel includes
the HTML/JS assets. Browser automation rejects local `file://` pages in this
session. Consequently checkpoints a/d still require actual browser screenshots,
rotation/zoom, keyboard use, image/view export and the 5 s / 250 ms interaction
benchmark. No browser timing or screenshot has been substituted with a mock.
