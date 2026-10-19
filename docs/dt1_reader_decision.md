# DT1 reader decision and verification

Implemented M1 on 2026-10-19, tested on Windows / CPython 3.13.12. The initial
release imports raw reflection profiles, preserves provenance and produces review
artifacts. It does not yet correct time-zero, georeference traces or infer texture.

## GPRPy compatibility spike

Checked out and installed GPRPy 1.0.14 at commit
`3b1f75eba820764b2147568fc0cc40f3a47919d5` into the ignored `.spikes/` directory.
The installed DT1 module was loaded independently of its GUI and optional migration
code. Source: [pinned reader](https://github.com/NSGeophysics/GPRPy/blob/3b1f75eba820764b2147568fc0cc40f3a47919d5/gprpy/toolbox/gprIO_DT1.py).
The reference module's normalized LF source SHA-256 is
`49d697601a873854bed980329aec4a5c33283161d5fc1c2056688f09dded54da`.

Results under NumPy 2.5.4:

- All 80 supplied amplitude arrays match independent binary record extraction exactly.
  The upstream orientation is samples × traces; the project uses traces × samples.
- All 80 supplied HD files parse in the upstream header reader. Their newline bytes
  include CR/CR/LF; its newline handling succeeds. A synthetic HD with actual blank
  lines fails with AttributeError. The project header inspector handles either form.
- Signed int16 extremes and reversed-position synthetic examples match exactly.
- The upstream amplitude reader accepts synthetic unsupported bytes-per-sample metadata,
  corrupt first trace numbers, and a truncated fixture without a reliable rejection.
  The project reader rejects each of these, including damage to interior records.
- Its public return value does not retain per-trace header metadata. Using that
  function directly would require a second record pass for validation and retention.

Local report: `outputs/gprpy_spike_verified_2026-10-19.json`. Reproduce after obtaining
the pinned module in `.spikes/`:

```powershell
.\.venv\Scripts\python.exe scripts/check_gprpy.py --reader .spikes/gprpy-installed/gprpy/toolbox/gprIO_DT1.py --root GPR_raw_data --output outputs/gprpy-spike-new.json
```

The script verifies its reference module's normalized hash, uses synthetic malformed
fixtures, and leaves the source collection unchanged. The upstream checkout and
installation are local spike artifacts, excluded from the package and Git. GPRPy's
MIT license is retained in that checkout; no upstream reader code is copied into
the project package.

## Chosen adapter

Use an independently written NumPy structured-record adapter with a 128-byte header
and signed little-endian two-byte samples. NumPy handles the array decoding;
xarray provides the canonical labelled data model. The local code adds record-level
validation, metadata retention and quality flags. GPRPy remains the independent
amplitude reference. This avoids importing GUI dependencies and performing an
unchecked amplitude pass plus a separate header pass in production.

The adapter verifies exact file length, every trace number, sample count, bytes per
sample, finite odometer position, constant finite positive binary timing field,
time-zero adjustment, vendor zero/comment flags and instrument time-of-day range.
It checks odometer endpoints against HD values with float32-aware tolerances.
All 128 header bytes are stored exactly, including annotation bytes and reserved
fields that may encode packed doubles. Unknown fields are not converted to floats
or interpreted as geographic coordinates.

Current support is reflection, single-profile, little-endian int16 layout. Explicit
multiple-channel metadata is rejected; no missing channel identity is fabricated.
Other layouts/survey modes require a tested adapter extension. Bad traces are
flagged but preserved. Reversed/irregular odometer positions retain acquisition
order; `distance_m` is cumulative absolute odometer movement, with the original
recorded position stored separately. Repeated positions use trace index in plots.

## Timing decision

The [RGPR reference implementation](https://github.com/emanuelhuber/RGPR/blob/master/R/readDT1.R)
constructs sample spacing from the HD window divided by the sample count. Adopt
that convention for the raw recording axis: `t[n] = n * window_ns / N` for
zero-based array index n. For these profiles, N=250 and window=100 ns, giving
0.4 ns spacing and sample centers 0–99.6 ns. The preserved binary field 7 is 400
in every trace; it is retained as a raw vendor field, not the sole timing authority.

Do not use an inclusive 0–100 ns linspace, which produces a different interval.
The [GPRPy profile importer](https://github.com/NSGeophysics/GPRPy/blob/3b1f75eba820764b2147568fc0cc40f3a47919d5/gprpy/gprpy.py)
uses such a linspace and subtracts a zero-based-looking HD time-zero offset. RGPR
subtracts one sample when constructing its HD time-zero offset. This disagreement
does not establish the instrument's physical zero for this acquisition.

Accordingly, the project preserves fractional HD time-zero and all per-trace
adjustments, records that their indexing is unresolved, and applies **no** time-zero
or depth correction. `twt_ns` is explicitly marked recording time / uncorrected in
its attributes and plot label. Resolve the physical origin with instrument/version
documentation or an airwave/control acquisition before M3 quantitative time/depth work.
Do not use the uncorrected recording time directly in a depth formula.

## Storage and environment

Store each line in Zarr **format 2**, accessed through the Zarr **3.4.1 runtime** and
xarray 2026.9.0. Initial testing exposed an incompatibility between the old Zarr 2
runtime and this xarray version, so the dependency group was updated. Format and
library version are separate concepts. Chunk by up to 256 traces with complete
time/header axes. The pinned backend's default compression is used and recorded
in Zarr metadata. Explicit fill handling preserves integer header bytes and flags;
unknown float positions and UTC times round-trip as NaN/NaT.

`requirements-processing-win-py313.lock` records exact dependencies. It was installed
offline from the package cache into a fresh `.spikes/clean-env`, followed by an
editable project installation. All 24 synthetic tests pass there; dependency checks,
Ruff lint and formatting pass. The lock covers Windows/Python 3.13, not an asserted
cross-platform scientific environment. Core metadata inventory remains standard-library-only.

Each ingestion run refuses existing output paths, validates assignment/config inputs,
stores source hashes and exact environment/code/configuration identifiers, reopens
every Zarr store and checks the complete Dataset for identity, then marks the manifest
complete. A failed run is marked failed and is never automatically reused. Original
source hashes are checked before and after each import. All 241 original DT1/HD/GPS
hashes are also compared with the pre-implementation inventory during local verification.

## Evidence and limits

The generated review is `outputs/raw_import_verified_2026-10-19/index.html`, containing
80 profiles / 66,774 traces. The run manifest records per-profile hashes, quality
counts and successful round trips. Radargram layout is visually checked. All 66,774
traces reach an ADC extreme somewhere in the recording; the flag indicates possible
clipping, not soil properties. It is currently trace-wide: later window-based quality
checks must distinguish direct-wave clipping from the soil-return windows, rather
than automatically discarding every complete trace.
Missing GPS positions remain missing, and no production plot assignment is inferred.
The first release establishes trusted signal ingestion; M2 begins GPS validation.

`outputs/local_verification_2026-10-19.json` confirms all 241 original raw-file hashes
match the pre-implementation inventory, every stored amplitude array matches GPRPy,
all stored-data/image/report hashes match, and the run's source-code hash matches
the implemented package. No source data changed.
