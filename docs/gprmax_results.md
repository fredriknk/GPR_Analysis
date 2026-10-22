# M8b/M8c measured results — 2026-10-22

The bounded gprMax4 experiment is complete. The implementation recovers a planted
same-bank synthetic model, but parameter recovery is unstable under mesh refinement.
On both native field ROIs, its layered templates perform worse than a training-mean
waveform baseline. There is no evidence here for more accurate field depths,
permittivity, soil properties or 3D volumes. The existing radar cubes remain the
baseline. Reproduction commands are in [gprmax_experiment.md](gprmax_experiment.md).

Visual follow-up: `outputs/gprmax-real-data-report-003/index.html` shows both
field ROIs with all 11 held-out line medians, the training-only gprMax prediction
and training-mean baseline. Separate grid figures show every held-out waveform;
`heldout-errors.csv` retains source/profile IDs and per-line RMSE in ADC counts.
The combined `real-data-comparison.png` is suitable for direct image viewing.
No source signal or existing fit was changed to make these figures.
The report's extended synthetic end-to-end check verifies exported artifacts and
unchanged input manifests. All 109 tests pass in 65.35 s after this addition
(`outputs/gprmax-report-tests-001.log`); Ruff and changed-file formatting pass.

## Execution evidence

The frozen gprMax 4.0.0 / PyCUDA 2026.1 environment ran on CUDA device 0, Quadro
RTX 4000, single precision. Each retained solver log names the CUDA backend. The
existing 18-solve bank took 188.25 s including setup/compilation. The new validation
run took 78.30 s for two refined 2D solves and one explicitly synthetic 3D smoke
solve. These small model timings do not establish field inversion throughput.

Runtime artifacts are local, ignored outputs:

| Stage | Verified run |
|---|---|
| CUDA bank | `outputs/gprmax-cuda-bank-001` |
| Native observations | `outputs/gprmax-observations-001` |
| Refined CUDA / 3D validation | `outputs/gprmax-cuda-validation-003` |
| Final fits and readable summary | `outputs/gprmax-fit-validated-002` |

The final fit's `run.json` records these input tree SHA-256 hashes and verifies
that inputs are unchanged at completion:

| Input | SHA-256 |
|---|---|
| Bank | `a49cb0b36f5d5f709190006e98c98ec8fedde64ef7066780a688dcd05dff9cc2` |
| Observations | `2fcb39c74928fa015421fca5bb1ef15e27b70234f3f2e51cac99afce72125427` |
| CUDA validation | `c583ade7f59fc33c6cc6f6d829249c7628b6559e1b213ae2e2f710353c431174` |

Earlier partial `gprmax-fit-*` runs are superseded by the final validated fit.
`gprmax-cuda-validation-001.log` records an earlier missing-import failure;
`gprmax-cuda-validation-002/run.json` records kernel preprocessing failure inside
the sandbox. Run 003 completed using the installed MSVC/NVIDIA compiler outside
the sandbox. Failed outputs were preserved, with no CPU substitution.

## Synthetic checks and mesh sensitivity

The assumed truth has upper relative permittivity ε = 9, interface depth 3 m,
source/receiver 0.01 m below the assumed ground surface, 0.5 m offset, conductivity
0.005 S/m, a 1.5 permittivity contrast, and an ideal 200 MHz Ricker source. This is
2D TMz physics with a flat interface and ideal dipole, not an antenna calibration.

For the same geometry, 10 mm and 5 mm cell responses have correlation **0.995684**
and relative L2 difference **0.092935** after interpolating finer output onto the
coarser time samples. No amplitude or time fit enters that comparison. Two meshes
do not establish convergence; the 3D smoke run establishes execution capability
only and is not a 3D inverse experiment.

Both recovery checks use 10 synthetic line summaries, 6 training / 4 holdout,
seed 874, independent Gaussian noise at 2% of clean waveform RMS, gain 1.7 and
shift +8.4 ns. The template library stays at 10 mm resolution.

| Synthetic observation | Selected ε / assumed depth | Shift (ns) | Fitted gain | Holdout NMSE | Known bank model recovered |
|---|---|---|---|---|---|
| Same bank / same mesh | 9 / 3 m | 8.4 | 1.700594 | 0.000371538 | Yes |
| Separately solved 5 mm mesh | 16 / 2 m | 14.8 | 0.548223 | 0.00904630 | No |

The first is explicitly an inverse-crime software test. The refined check changes
discretization but retains the solver and physical assumptions, so it is not
independent model or field validation. Its timing error is +6.4 ns despite a good
waveform fit. Each case has three candidates within +0.01 training NMSE, spanning
(ε, assumed depth m) = (4, 4), (9, 3), (16, 2). A fitted nuisance shift and gain
trade against travel time and material assumptions. These near ties demonstrate
sensitivity; they are neither confidence bounds nor proof of unique recovery.

## Conditional native-field comparison

The all-data RMS volume selected the candidate ROIs before splitting, so these are
conditional checks, not unbiased anomaly-detection validation. The audit retains
13 grid-01 lines (8 training / 5 holdout) and 16 grid-02 lines (10 / 6), with a
0.25 m training exclusion buffer around held-out trace positions. The buffer is
an experiment choice, not measured positional precision or proven independence.

Each retained trace is finite and sample-unclipped across the complete ROI window;
each line contributes its median native signed waveform with equal weight. The fit
uses all audited line summaries/samples and no extrapolation. This support does
not represent coverage across a full grid or infer an antenna footprint. Source,
profile and trace identities and quality remain in the audit; final fit per-line
errors retain source/profile identity and retained trace counts.

NMSE is mean squared waveform residual divided by mean squared observations in
the reported split. MSE has squared ADC-count units. Zero prediction has NMSE 1;
the training-mean comparator uses the same holdout rows without fitting holdouts.
Only training rows choose template, shift and global signed gain.

| ROI | Selected ε / assumed depth | Shift (ns) | Training NMSE | Holdout NMSE | Training-mean holdout NMSE |
|---|---|---|---|---|---|
| grid-01 | 4 / 4 m | 2.0 | 0.982375 | 0.982988 | 0.443253 |
| grid-02 | 4 / 3 m | −2.8 | 0.995385 | 0.990739 | 0.109616 |

The templates improve on zero only slightly and lose substantially to the simple
training-mean baseline. There are 26 near-equivalent candidates spanning seven
material/depth pairs for grid-01, and 450 spanning ten pairs for grid-02. These
selected parameter values describe hypothetical library entries only.

Unknown source/antenna response, physical time zero, velocity, calibration,
position precision and CRS stay unknown. Homogeneous background subtraction is
applied to simulated responses only. No field normalization, background removal,
display gain or material assignment is introduced. This limited bank cannot rule
out other physical models. Further physical inversion requires independent timing,
antenna/geometry and material constraints and appropriately separated validation.

## Verification

Synthetic regressions cover signed gain/model/shift recovery, held-out-value
perturbation without fit changes, invalid axes/splits/shapes, zero-energy and
unsupported templates, censored arrival gates, near ties, explicit zero-energy
metrics, HDF5 timing/component selection, hash verification, no overwrite and a
solver-free end-to-end fit with refined truth. Private data and CUDA are not
required by the ordinary suite. After editable installation of soil-sensing 0.4.0,
`python -m unittest discover -s tests -v` passed all **109 tests** in 73.72 s
(`outputs/gprmax-m8bc-tests-final.log`). `python -m ruff check .`, changed Python
file formatting checks and `git diff --check` passed. Final fit and CUDA validation
manifests were reopened and all declared artifact hashes verified. The field
accuracy and refined parameter-recovery failures above are scientific outcomes,
not hidden or reclassified software test failures.
