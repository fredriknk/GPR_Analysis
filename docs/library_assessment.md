# Library assessment

Primary sources checked 2026-10-19. These are adoption decisions for this project,
not claims that every candidate has been installed or tested. Keep experimental
backends optional; validate exact versions on Windows before locking the environment.

| Library | Evidence and fit | Decision |
|---|---|---|
| [GPRPy](https://github.com/NSGeophysics/GPRPy) | Python GPR processing/visualization; MIT license. Its [DT1 reader](https://github.com/NSGeophysics/GPRPy/blob/master/gprpy/toolbox/gprIO_DT1.py) reads trace records and returns samples × traces. | Evaluated at commit 3b1f75e / v1.0.14: exact amplitude parity on 80 profiles; malformed-record validation and metadata retention require additional code. Keep as an independent reference. The production adapter uses NumPy with strict checks; see dt1_reader_decision.md. |
| [RGPR](https://emanuelhuber.github.io/RGPR/00_RGPR_tutorial_import-GPR-data/) | R package with Sensors & Software DT1/HD import and other formats. | Independent reference for reader/processing comparisons; optional R environment, not a required Python runtime. |
| [readgssi](https://github.com/iannesbitt/readgssi/blob/master/README.md) | GSSI DZT handling. | Useful future adapter if GSSI files arrive; it does not solve the present DT1 requirement. |
| [SciPy signal](https://docs.scipy.org/doc/scipy/reference/signal.html) | Standard filters, detrending and signal-analysis routines. | Use with NumPy for transparent transforms; specify axes, sampling units and boundary behavior. |
| [pynmea2](https://github.com/Knio/pynmea2) | Strict checksum parsing and multi-GNSS GNS/GGA/RMC sentence fields. | Adopted v1.19.0 behind trace-block association. Empty per-constellation sentences and proprietary `$RD1` status are skipped; invalid checksums are counted. |
| [pyproj Transformer](https://pyproj4.github.io/pyproj/stable/api/transformer.html) | Explicit source/target CRS conversion with `always_xy=True` and no ballpark transform. | Adopted v3.8.0 for confirmed geographic-to-metric projection and plot/sample CRS validation. No receiver datum is inferred. |
| [xarray I/O](https://docs.xarray.dev/en/stable/user-guide/io.html) | Labelled multidimensional data, including Zarr storage. | Adopted xarray 2026.9.0 with Zarr runtime 3.4.1, writing format-2 stores. Full dataset round trips pass. The initially proposed Zarr 2 runtime was incompatible with this xarray release. |
| [GeoPandas projections](https://geopandas.org/en/stable/docs/user_guide/projections.html) | CRS-aware geometries and reprojection via pyproj. | Spatial tables, plot boundaries and joins; use explicit projected metric coordinates. |
| [EMagPy](https://hkex.gitlab.io/emagpy/) | Python API and GUI for FDEM/EMI analysis and inversion; [project mirror](https://github.com/hkexgroup/emagpy) identifies GPL-3.0. | Optional EMI inversion adapter once the instrument and measurements are known. Keep a separate compatibility environment if needed; assess redistribution obligations before bundling. |
| [pyGIMLi](https://www.pygimli.org/) | Geophysical modelling/inversion framework. | Defer until a specific inversion need justifies its compiled dependencies and complexity. |
| [gprMax](https://docs.gprmax.com/en/latest/faqs.html) | FDTD electromagnetic forward modelling. | Optional synthetic experiments to test sensitivity/confounding. It is not the raw-data processing backend. |
| [scikit-learn validation](https://scikit-learn.org/stable/modules/cross_validation.html) | Pipelines and grouped cross-validation support. | Initial texture models and fold isolation. Construct spatial groups/buffers explicitly; generic grouped CV alone does not establish spatial independence. |

`pynmea2` and pyproj were verified against synthetic cases and both supplied
GPS site groups; the 80-profile GPS audit parsed 65,971 measured positions without
checksum failures. SciPy `butter(..., output="sos")` and `sosfiltfilt` passed
synthetic passband/stopband tests. Matplotlib renders comparison plots. Parquet,
rasterio and GeoPandas remain future GIS/export options. The minimal `field`
extra and `requirements-field-win-py313.lock` cover the current M2–M4 code.

## GPRPy adoption spike

1. Install a pinned release/commit in an isolated environment; record package and
   dependency versions and license notices. Do not silently choose an unpinned branch.
2. Read representative files from all four source subdirectories. Check amplitude
   arrays against known binary record locations and synthetic exact-valued fixtures.
3. Compare all sample counts, trace counts, signed amplitudes and positions. Preserve
   trace headers even if the backend exposes only amplitudes.
4. Test blank-line HD files, truncated DT1, unsupported dtype/channel, lower-case
   sidecars, corrupted trace numbering and sample timing. Fail with useful diagnostics.
5. Confirm processing parity on simple synthetic signals. Choose adapter, small
   upstream fix, or narrow documented reader only after recording the result.

The spike is complete. The project imports verified DT1 signals using an independently
written NumPy adapter, preserving original header bytes. It contains no copied GPRPy
source. SciPy-based signal transforms were added in M3. Detailed results,
timing decisions and reproducible commands are in [dt1_reader_decision.md](dt1_reader_decision.md).

## Texture research gate

No single library makes GPR a direct texture measurement. Define reference texture
labels, sample support, confounders and a validation protocol before selecting a
predictive model. A future sensor can be useful without being mandatory for the
GPR import workflow. Evaluate added value using sensor ablations on the same
spatially held-out soil samples, rather than assuming more channels improve accuracy.
