# Supplied test-data assessment

Read-only inspection on 2026-10-19. This is a development collection, not a verified
assignment to the four planned 100 × 100 m plots. Full manifest:
`outputs/test_inventory_2026-10-19.json` (generated locally and excluded from Git).

## Inventory

390 files totaling 6,820,090,085 bytes (about 6.82 GB decimal). Most volume is in
vendor CSV/GRD products; raw DT1 files total only about 40 MiB.

| Extension | Count | Interpretation at inventory stage |
|---|---:|---|
| DT1 / HD | 80 / 80 | Sensors & Software radar and companion headers |
| GPS | 81 | Trace-associated GNSS/NMEA sidecars; one orphan |
| GFP | 2 | Vendor XML project/grid metadata |
| CSV / GRD | 34 / 68 | Existing depth-slice products; transforms/settings not verified |
| JPG | 33 | Existing processed radargram images |
| KMZ | 7 | Geographic comparison exports |
| GPZ / VOXB | 2 / 3 | Existing vendor project/volume products |

Header-derived totals: **66,774 traces**, 250 samples per trace, nominal 200 MHz,
100 ns window and 0.5 m antenna separation. Observed acquisition step sizes range
from 0.01 to 0.10 m. These describe recording settings, not achievable soil-map
resolution, depth penetration, or coordinate accuracy.

| Directory | Profiles | Traces | Header dates |
|---|---:|---:|---|
| DATA08line | 11 | 2,434 | 2026-08-11 |
| DATA08line/Lines08 | 10 | 17,175 | Eight on 2026-06-11; two on 2026-08-11 |
| Data09line | 45 | 6,108 | 2026-08-11 |
| Data09line/Lines | 14 | 41,057 | 2026-08-11 |

## Issues and interpretation limits

- `DATA08line/XLINE11.GPS` has no matching DT1 or HD. Preserve it and report the
  missing profile; do not fabricate a radar record or silently delete the sidecar.
- Dates and locations are mixed within directories. For example, the first GNGNS
  record in `DATA08line/Lines08/LINE00.GPS` contains latitude `6027.73939255 N`,
  longitude `01036.98640828 E`; `DATA08line/XLINE00.GPS` contains
  `5939.95890785 N`, `01045.46010353 E`. These are NMEA degree-minute strings at
  clearly different locations. RMC samples agree with the respective June/August
  header dates. This is evidence for assignment review, not verified map coordinates.
- Sampled GPS blocks contain a populated GNGNS sentence followed by empty-position
  per-constellation GNS messages, plus RMC/VTG/GSA/GSV. Do not assume GGA-only input
  or one sentence per trace. Full checksums, fix quality, temporal coverage and
  interpolation have **not** been validated by this scaffold.
- All 80 DT1 file sizes match `trace_count * (128 + 2 * samples_per_trace)`.
  All endpoint headers also pass the CLI's checks. This does not validate every interior
  record, establish timing conventions, or prove georeferencing accuracy.
- Original time-zero points are fractional. Keep them in metadata pending an
  explicit sample-index/time convention; rounding would lose information.
- Some recorded line lengths exceed 100 m. Do not clip or rescale them to fit the
  eventual plot dimensions. A test line may span another geometry or trajectory.
- Existing slice filenames span 0–4.25 m in 0.125 m intervals. These are vendor
  product labels, not evidence that those depths are independently calibrated.
  CSV previews contain local X/Y/Z/depth/amplitude fields with blank amplitudes
  in some rows. Encoding, nodata, grid transform and velocity must be inspected
  before quantitative reuse.
- The GPRPy spike now confirms all 80 supplied headers parse. Their CR/CR/LF bytes
  explain the apparent blank lines under universal newline conversion. A synthetic
  file with actual empty lines fails in the upstream parser. The project metadata
  inspector uses field names and handles both. See dt1_reader_decision.md.

No original data files were moved or rewritten. Production plot geometry, receiver
datum, lever arm, texture reference samples, velocity calibration and final sensor
selection remain unspecified.

## M1 follow-up

All 80 signals are now ingested, every trace record is checked, and all profile
datasets are saved/reopened with exact equality. The generated raw review is
`outputs/raw_import_verified_2026-10-19/index.html`. Geographic positions and GPS
quality remain unevaluated. Time centers use 0.4 ns spacing; physical time-zero is
explicitly uncorrected. The original file hashes remain the comparison baseline.

## Current GPS and regression status

The M1 paragraphs above describe that historical checkpoint. M2 subsequently
audited all 80 GPS sidecars, as recorded in `docs/implementation_plan.md` and
`outputs/gps_audit_release_2026-10-19.json`. The audit retained 758 missing and
36 rejected positions rather than interpolating through unsupported gaps.
Field CRS/control and position accuracy remain unverified.

An opt-in local regression suite is implemented at `tests_local/test_real_data.py`.
See [local_data_checks.md](local_data_checks.md) for the representative selection,
full-collection option, immutable-source checks and limits. It is separate from
the synthetic CI suite and never assigns these files to production plots.

All six representative checks passed on 2026-10-20. Actual processed radargrams,
GPS quality and window attributes from the five recordings are described in
[real_data_results.md](real_data_results.md). The 540 missing positions in LINE02
are spread across 111 spans, not one continuous gap; the longest has 325 traces.
