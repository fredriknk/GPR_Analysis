# M8 — gprMax4 CUDA inverse-solving experiment

Requested 2026-10-21. Work is isolated on branch `gprmax`; the completed M7 work
has been published to `main` at `d90c969`.

## Milestones

1. **M8a: environment and observation audit.** Verify gprMax4 and CUDA execution
   on the Quadro RTX 4000 (8 GiB), freeze a separate environment, and audit native
   measurements associated with both complete-grid volumes. Commit and push.
2. **M8b: conditional inverse experiment.** Run a bounded CUDA forward-model bank,
   demonstrate synthetic recovery, and compare against native field waveforms
   using whole-line training/holdout splits. Record every assumed physical model,
   nuisance adjustment, input hash, CUDA log and candidate loss. Commit and push.
   Completed 2026-10-22, including mesh sensitivity and synthetic recovery failure
   under refinement; see [measured results](gprmax_results.md).
3. **M8c: results.** Report synthetic versus field results, holdout coverage and
   parameter ambiguity. Only call the field volume more accurate if independent
   evidence supports that statement. Preserve current scientific cubes. Commit
   and push results and reproducible commands.
   Completed 2026-10-22: field templates lose to the training-mean baseline;
   no material, depth or 3D-volume improvement is supported.

## Scientific scope

The RMS cubes select regions for review; they are not electromagnetic field
observations. The experiment must return to immutable native quantitative stores
and preserve trace identity, sample clipping, positions and acquisition direction.
Both grids have recorded 200 MHz antenna frequency and 0.5 m antenna separation.
The largest coherent candidates are grid-01 candidate 16 (66.8–77.2 ns) and
grid-02 candidate 4 (48.8–65.6 ns). Selection is exploratory, using all-data
volumes, so any subsequent holdouts are conditional checks on these selected
regions, not unbiased detector validation.

Unknown source wavelet/antenna response, physical time zero, velocity, calibration,
position precision and production CRS remain unknown. Hypothetical material and
geometry values are modelling assumptions, never field metadata. A small 2D
layered-medium model can test waveform compatibility and parameter identifiability;
it cannot establish a unique 3D permittivity volume. Full waveform inversion of
interpolated RMS as though it were raw electric-field data would be invalid.

## Primary references

- [gprMax4 installation](https://docs.gprmax.com/en/latest/inc_README.html)
- [CUDA and solver precision](https://docs.gprmax.com/en/latest/accelerators.html)
- [Model commands and SI units](https://docs.gprmax.com/en/latest/input_hash_cmds.html)

Use an isolated `.spikes/gprmax4` environment. gprMax and generated CUDA code stay
outside the scientific package. New adapters/functions belong in `src/soil_sensing`;
scripts are thin callers. No automatic CPU fallback is allowed for this experiment.

M8a completed: gprMax **4.0.0**, PyCUDA **2026.1**, and CUDA GPU 0 successfully
ran the synthetic smoke model on the **Quadro RTX 4000**. The solver log explicitly
names the CUDA backend. It recorded a 0.1217 s solve and 20.9945 s total including
first-time kernel compilation; these timings are not inversion throughput.
Driver version is 582.16; CUDA toolkits 11.6, 12.5 and 13.0 are installed.
Windows kernel compilation also required activating the installed MSVC x64 C++
toolchain; the wrapper does this only for its process environment.

The observation audit retained 13 lines in grid 1 (8 training / 5 holdout) and
16 in grid 2 (10 training / 6 holdout). Training observations within 0.25 m of
held-out trace positions were excluded. Each retained trace has finite,
sample-unclipped support across its complete ROI time window. It saves native
trace IDs, position status and quality flags, plus a median waveform per line.
The ROI radius of 2.5 m is an experiment choice, not positional accuracy.

```powershell
uv venv .spikes/gprmax4 --python .venv/Scripts/python.exe
uv pip install --python .spikes/gprmax4/Scripts/python.exe -r requirements-gprmax4-win-py311.lock
uv pip install --python .spikes/gprmax4/Scripts/python.exe --no-deps -e .
.\.venv\Scripts\python.exe scripts/gprmax_experiment.py audit --input outputs/grid-detection-final-2026-10-21 --output outputs/gprmax-observations-new
.\tools\run-gprmax-cuda.ps1 -Script scripts/gprmax_experiment.py bank --output outputs/gprmax-cuda-bank-new
```

The isolated environment is pinned in `requirements-gprmax4-win-py311.lock`.
Runtime evidence remains local: `outputs/gprmax-cuda-smoke/solver-final.log`,
`smoke.in`, `smoke.h5`, and `outputs/gprmax-observations-001/`. Inputs are
hash-verified before and after the observation audit. The completed bank contains
18 CUDA solves (three homogeneous references and 15 layered templates). Homogeneous
subtraction affects synthetic templates only; native field waveforms remain unchanged.

## Continue or reproduce M8b/M8c

Use fresh output paths and substitute them consistently. Validation needs the
isolated CUDA environment and MSVC wrapper. Fitting needs the ordinary processing
environment, with no solver or GPU import. The optional `--validation` additionally
tests recovery using finer-mesh synthetic observations; omitting it only runs the
same-bank software recovery and field comparisons.

```powershell
.\tools\run-gprmax-cuda.ps1 -Script scripts/gprmax_experiment.py validate --input outputs/gprmax-cuda-bank-001 --output outputs/gprmax-cuda-validation-new
.\.venv\Scripts\python.exe scripts/gprmax_experiment.py fit --input outputs/gprmax-cuda-bank-001 --observations outputs/gprmax-observations-001 --validation outputs/gprmax-cuda-validation-new --output outputs/gprmax-fit-new
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
```

The fit searches 15 assumed models and shifts from −8 to +30 ns at 0.4 ns spacing.
One global signed gain is fitted by least squares using equally weighted training
line medians; only training loss selects the model/shift. All candidates, near ties,
per-line errors and identities, holdout sample support and two baselines are saved.
The finite-offset geometric arrival must have a complete additional 12 ns gate
inside the simulation, its peak must fall in the shifted ROI, interpolation must
have complete temporal support, and the ROI must contain at least 10% of template
energy. These are experiment screening choices, not physical time-zero evidence.
Near ties use best training NMSE + 0.01, not a confidence interval.

`validate` preserves model inputs, HDF5 outputs, CUDA logs and refined truth.
Its 3D x-polarized capability check reads `Ex`; layered 2D templates read `Ez`.
Receiver axes use HDF5 `SampleInterval` and `TimeSampleOffset`, not a guessed
simulation linspace. Both CUDA stages require frozen gprMax 4.0.0 and device 0;
there is no CPU fallback. The online upstream documentation now describes 4.0.1;
this experiment retains its verified 4.0.0 lock.

`fit` writes `results.json`, `summary.md` and numerical `*-fit.npz` arrays, with
hash-verified completed input runs and no overwrite. Mesh validation must bind to
the same bank hash. Full details, input fingerprints, measured errors and limits
are in [gprmax_results.md](gprmax_results.md). No production plot assignment,
source wavelet, calibration or field accuracy has been inferred from the fits.
