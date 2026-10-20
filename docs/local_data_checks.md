# Opt-in checks with the supplied real data

The normal `tests/` suite uses synthetic fixtures and never requires private data.
`tests_local/test_real_data.py` is a separate, explicitly enabled integration suite
for the supplied development collection. Discovering it without an opt-in skips
all six tests without reading sensor files.

Install the full Windows/Python 3.11 lock, or the tested processing, field and
visualization extras. From the project root in PowerShell:

```powershell
$env:SOIL_SENSING_REAL_DATA_ROOT = (Resolve-Path -LiteralPath GPR_raw_data).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests_local -v
Remove-Item Env:SOIL_SENSING_REAL_DATA_ROOT
```

The representative selection is explicit:

- `DATA08line/XLINE00.DT1`: short August development line.
- `DATA08line/Lines08/LINE00.DT1`: June acquisition from another location.
- `Data09line/YLINE00.DT1`: another line family.
- `Data09line/Lines/LINE02.DT1`: the audited 540 missing GPS positions.
- `Data09line/Lines/LINE04.DT1`: the audited 34 rejected fixes.

Those paths and recording expectations identify this test collection; they are
not production plot assignments. A missing representative file fails explicitly.
To extend the same local checks to every DT1 in the declared root:

```powershell
$env:SOIL_SENSING_REAL_DATA_ROOT = (Resolve-Path -LiteralPath GPR_raw_data).Path
$env:SOIL_SENSING_REAL_DATA_ALL = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests_local -v
Remove-Item Env:SOIL_SENSING_REAL_DATA_ALL
Remove-Item Env:SOIL_SENSING_REAL_DATA_ROOT
```

The checks cover:

1. Independent `struct` extraction at first, interior and last records, exact raw
   header bytes, signed amplitudes, record counts and the observed 0.4 ns axis.
2. GPS association without invented datum/CRS, unchanged amplitudes and trace IDs,
   missing projected coordinates/uncertainty, and documented gap/rejection counts.
3. Quantitative RMS against an independent scalar calculation, clipping exclusions,
   trace lineage and missing physical depth/velocity calibration.
4. Exact raw, located and quantitative Zarr write/reopen equality in temporary
   derived stores. Temporary stores are removed after the test.
5. Display gain leaves quantitative values unchanged; depth requests fail without
   calibration evidence.
6. SHA-256 equality for all original DT1/HD/GPS files in the collection before and
   after the checks. A class cleanup repeats the check even after a test failure.

Every source is read-only; only temporary derived stores and an optional redirected
local test log are written. The suite has no baseline-update command, external
network calls, training step or geographic export. Counts are diagnostic regression
expectations established by the local audit, not field accuracy or sensor precision.

For geographic/soil-property tests, first supply reviewed plot geometry and CRS/
control, evidenced physical time zero and velocity, and independent laboratory
texture samples with support/method definitions. The current development collection
cannot establish texture accuracy, production plot coverage or calibrated depth.

Executed on 2026-10-20: all six checks passed in 49.406 seconds on five recordings
with 23,415 traces; all 241 source checksums were unchanged. The ordinary synthetic
suite then passed all 65 tests. The local logs are
`outputs/real-data-checks-2026-10-20-final.log` and
`outputs/synthetic-checks-after-local-tests-2026-10-20.log`.
The separately saved [real-data results](real_data_results.md) include radargrams
and full quantitative products, rather than only test logs.
