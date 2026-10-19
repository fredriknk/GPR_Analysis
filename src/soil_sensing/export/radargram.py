"""Headless raw radargram rendering without modifying quantitative amplitudes."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from soil_sensing.profile import QualityFlag, validate_profile


def plot_radargram(
    profile: xr.Dataset, path: Path, *, display_amplitude: np.ndarray | None = None
) -> None:
    validate_profile(profile)
    if path.exists():
        raise ValueError(f"Radargram already exists: {path}")
    amplitudes = profile.amplitude.values if display_amplitude is None else display_amplitude
    if amplitudes.shape != profile.amplitude.shape or not np.isfinite(amplitudes).all():
        raise ValueError("Display amplitudes must be finite and match the profile")
    limit = max(float(np.quantile(np.abs(amplitudes), 0.99)), 1.0)
    distances = profile.distance_m.values
    dt = profile.attrs["sample_interval_ns"]
    # pcolormesh supports repeated/irregular/reversed odometer paths through cumulative distance.
    if len(distances) == 1 or np.any(np.diff(distances) == 0):
        horizontal = np.arange(len(distances), dtype=float)
        xlabel = "Acquisition trace index (zero based; duplicate odometer positions)"
    else:
        horizontal = distances
        xlabel = "Distance along acquisition path (m; odometer)"
    fig, (axis, quality) = plt.subplots(
        2, 1, figsize=(12, 6), sharex=True, height_ratios=[12, 1], layout="constrained"
    )
    fig.get_layout_engine().set(rect=(0, 0.06, 1, 0.94))
    image = axis.pcolormesh(
        horizontal,
        profile.twt_ns,
        amplitudes.T,
        shading="nearest",
        cmap="gray",
        vmin=-limit,
        vmax=limit,
        rasterized=True,
    )
    axis.set_ylim(float(profile.twt_ns[-1]) + dt / 2, -dt / 2)
    axis.set_ylabel("Recording time (ns)\ntime-zero uncorrected")
    label = "display AGC" if display_amplitude is not None else profile.attrs["processing_branch"]
    axis.set_title(
        f"{profile.attrs['profile_id']} — {label} GPR | "
        f"{profile.sizes['trace']:,} traces × {profile.sizes['sample']} samples"
    )
    unit = (
        "normalized display amplitude"
        if display_amplitude is not None
        else profile.attrs["amplitude_units"]
    )
    fig.colorbar(image, ax=axis, label=f"{unit} (display clips at 99th percentile)", pad=0.015)
    bad = profile.quality_flags.values & np.uint32(0xFFFFFFFF ^ int(QualityFlag.POSITION_MISSING))
    quality.scatter(
        horizontal, np.zeros(len(horizontal)), s=4, c=np.where(bad != 0, "#b42318", "#2c7a5a")
    )
    quality.set_yticks([])
    quality.set_ylabel("Signal\nflags", rotation=0, ha="right", va="center")
    quality.set_xlabel(xlabel)
    quality.set_ylim(-1, 1)
    quality.spines[["left", "right", "top"]].set_visible(False)
    fig.text(
        0.5,
        0.005,
        "Green: signal checks passed · Red: signal flag · "
        f"{label} · recording time; depth conversion unavailable",
        ha="center",
        fontsize=8,
        color="#444444",
    )
    try:
        fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight")
    finally:
        plt.close(fig)
