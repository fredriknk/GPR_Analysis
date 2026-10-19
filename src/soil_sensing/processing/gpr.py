"""Explicit, per-trace GPR transforms. No soil-property inference is performed."""

from dataclasses import asdict, dataclass

import numpy as np
import xarray as xr
from scipy import ndimage, signal

from soil_sensing.profile import QualityFlag, validate_profile


@dataclass(frozen=True)
class ProcessingOptions:
    remove_dc: bool = True
    dewow_window_ns: float | None = None
    bandpass_low_mhz: float | None = None
    bandpass_high_mhz: float | None = None
    bandpass_order: int = 4
    reviewed_time_zero_ns: float | None = None
    time_zero_evidence: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.remove_dc, bool):
            raise ValueError("remove_dc must be Boolean")
        if isinstance(self.bandpass_order, bool) or not isinstance(self.bandpass_order, int):
            raise ValueError("bandpass_order must be an integer")
        for name in (
            "dewow_window_ns",
            "bandpass_low_mhz",
            "bandpass_high_mhz",
            "reviewed_time_zero_ns",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ValueError(f"{name} must be numeric")
        if self.time_zero_evidence is not None and not isinstance(self.time_zero_evidence, str):
            raise ValueError("time_zero_evidence must be text")

    def validate(self, dt_ns: float, samples: int) -> None:
        nyquist_mhz = 500 / dt_ns
        if self.dewow_window_ns is not None:
            if not np.isfinite(self.dewow_window_ns) or self.dewow_window_ns <= dt_ns:
                raise ValueError("Dewow window must exceed one sample interval")
            if round(self.dewow_window_ns / dt_ns) >= samples:
                raise ValueError("Dewow window must fit within a trace")
        if (self.bandpass_low_mhz is None) != (self.bandpass_high_mhz is None):
            raise ValueError("Bandpass needs both low and high cutoffs")
        if self.bandpass_low_mhz is not None and not (
            0 < self.bandpass_low_mhz < self.bandpass_high_mhz < nyquist_mhz
        ):
            raise ValueError(f"Bandpass cutoffs must satisfy 0 < low < high < {nyquist_mhz:g} MHz")
        if not 1 <= self.bandpass_order <= 8:
            raise ValueError("Bandpass order must be between 1 and 8")
        if self.reviewed_time_zero_ns is not None and not np.isfinite(self.reviewed_time_zero_ns):
            raise ValueError("Reviewed time zero must be finite")
        if self.reviewed_time_zero_ns is not None and not self.time_zero_evidence:
            raise ValueError("Reviewed time zero requires evidence")
        if self.reviewed_time_zero_ns is None and self.time_zero_evidence:
            raise ValueError("Time-zero evidence requires an explicit value")


def process_profile(profile: xr.Dataset, options: ProcessingOptions) -> xr.Dataset:
    """Process each trace independently, preserving original time and metadata."""
    validate_profile(profile)
    if profile.attrs["processing_branch"] != "raw":
        raise ValueError("Quantitative processing requires a raw profile")
    dt = float(profile.attrs["sample_interval_ns"])
    options.validate(dt, profile.sizes["sample"])
    raw = profile.amplitude.values
    adc_limit_mask = (raw == -32768) | (raw == 32767)
    data = raw.astype(np.float64)
    steps = []
    if options.remove_dc:
        counts = (~adc_limit_mask).sum(axis=1, keepdims=True)
        baseline = np.sum(np.where(adc_limit_mask, 0.0, data), axis=1, keepdims=True)
        baseline /= np.maximum(counts, 1)
        data = data - baseline
        steps.append("per_trace_dc_mean_excluding_adc_limits")
    if options.dewow_window_ns is not None:
        width = max(3, int(round(options.dewow_window_ns / dt)))
        if width % 2 == 0:
            width += 1
        if width >= profile.sizes["sample"]:
            raise ValueError("Odd dewow window must fit inside a trace")
        trend = ndimage.uniform_filter1d(data, size=width, axis=1, mode="reflect")
        data = data - trend
        steps.append(f"moving_mean_dewow_{width}_samples")
    if options.bandpass_low_mhz is not None:
        sos = signal.butter(
            options.bandpass_order,
            [options.bandpass_low_mhz, options.bandpass_high_mhz],
            btype="bandpass",
            fs=1000 / dt,
            output="sos",
        )
        try:
            data = signal.sosfiltfilt(sos, data, axis=1)
        except ValueError as exc:
            raise ValueError("Trace too short for zero-phase bandpass padding") from exc
        steps.append("zero_phase_butterworth_bandpass")
    result = profile.copy(deep=True)
    result["amplitude"] = (
        ("trace", "sample"),
        data.astype(np.float32),
        {"units": profile.attrs["amplitude_units"]},
    )
    result["adc_limit_mask"] = (("trace", "sample"), adc_limit_mask)
    result.attrs["processing_branch"] = "quantitative"
    result.attrs["processing_options"] = asdict(options)
    result.attrs["clipping_can_spread_across_windows"] = bool(
        options.dewow_window_ns is not None or options.bandpass_low_mhz is not None
    )
    result.attrs["history"] = [
        *profile.attrs["history"],
        {"operation": "process_gpr", "steps": steps, "options": asdict(options)},
    ]
    if options.reviewed_time_zero_ns is not None:
        result = result.assign_coords(
            corrected_twt_ns=("sample", profile.twt_ns.values - options.reviewed_time_zero_ns)
        )
        result.attrs["time_zero_status"] = "reviewed_explicit_value"
    validate_profile(result)
    return result


def display_agc(profile: xr.Dataset, window_ns: float) -> np.ndarray:
    """Return a display-only normalized array; never store as quantitative amplitude."""
    validate_profile(profile)
    if profile.attrs["processing_branch"] not in ("raw", "quantitative"):
        raise ValueError("Display gain requires raw or quantitative source")
    dt = float(profile.attrs["sample_interval_ns"])
    width = int(round(window_ns / dt))
    if not np.isfinite(window_ns) or width < 3 or width >= profile.sizes["sample"]:
        raise ValueError("AGC window must span at least 3 samples and fit within the trace")
    data = profile.amplitude.values.astype(np.float64)
    power = ndimage.uniform_filter1d(data * data, size=width, axis=1, mode="reflect")
    scale = np.sqrt(np.maximum(power, 1.0))
    return (data / scale).astype(np.float32)


def window_rms_features(profile: xr.Dataset, windows_ns: list[tuple[float, float]]) -> list[dict]:
    """Per-trace recording-time RMS attributes with explicit signal-quality exclusion."""
    validate_profile(profile)
    if profile.attrs["processing_branch"] != "quantitative":
        raise ValueError("RMS attributes require the quantitative branch")
    if not windows_ns:
        raise ValueError("At least one time window is required")
    time_name = "corrected_twt_ns" if "corrected_twt_ns" in profile else "twt_ns"
    time = profile[time_name].values
    data = profile.amplitude.values.astype(np.float64)
    bad_bits = int(QualityFlag.ZERO_SIGNAL | QualityFlag.VENDOR_ZERO)
    global_valid = (profile.quality_flags.values & bad_bits) == 0
    if profile.attrs.get("clipping_can_spread_across_windows"):
        global_valid &= (profile.quality_flags.values & int(QualityFlag.ADC_LIMIT)) == 0
    rows = []
    x = profile.x_m.values
    y = profile.y_m.values
    utc = profile.time_utc.values
    for start, stop in windows_ns:
        if not np.isfinite([start, stop]).all() or not 0 <= start < stop:
            raise ValueError("Time windows must be finite, ordered and nonnegative")
        selection = (time >= start) & (time < stop)
        if not selection.any():
            raise ValueError(f"Time window {start:g}–{stop:g} ns selects no samples")
        values = np.sqrt(np.mean(data[:, selection] ** 2, axis=1))
        if "adc_limit_mask" in profile:
            clipped = profile.adc_limit_mask.values[:, selection].any(axis=1)
        else:
            clipped = (profile.quality_flags.values & int(QualityFlag.ADC_LIMIT)) != 0
        valid = global_valid & ~clipped
        depth = profile.depth_m.values[selection] if "depth_m" in profile else np.array([])
        finite_depth = depth[np.isfinite(depth)]
        depth_top = float(finite_depth.min()) if len(finite_depth) else None
        depth_bottom = float(finite_depth.max()) if len(finite_depth) else None
        for i, trace_id in enumerate(profile.trace_id.values):
            rows.append(
                {
                    "trace_id": str(trace_id),
                    "profile_id": profile.attrs["profile_id"],
                    "source_id": profile.attrs["source_id"],
                    "survey_id": profile.attrs["survey_id"],
                    "plot_id": profile.attrs["plot_id"],
                    "x_m": float(x[i]) if np.isfinite(x[i]) else None,
                    "y_m": float(y[i]) if np.isfinite(y[i]) else None,
                    "target_crs": profile.attrs["target_crs"],
                    "time_utc": f"{utc[i]}Z" if not np.isnat(utc[i]) else None,
                    "position_status": str(profile.position_status.values[i]),
                    "feature_name": "gpr_rms",
                    "value": float(values[i]) if valid[i] else None,
                    "unit": profile.attrs["amplitude_units"],
                    "branch": "quantitative",
                    "time_basis": time_name,
                    "window_start_ns": start,
                    "window_stop_ns": stop,
                    "sample_count": int(selection.sum()),
                    "depth_top_m": depth_top,
                    "depth_bottom_m": depth_bottom,
                    "depth_support": (
                        "approximate_homogeneous_time_gate" if depth_top is not None else None
                    ),
                    "velocity_calibration_id": (
                        profile.attrs["depth_model"]["calibration"]["calibration_id"]
                        if "depth_model" in profile.attrs
                        else None
                    ),
                    "quality_flags": int(profile.quality_flags.values[i]),
                    "exclusion_reason": None if valid[i] else "signal_quality_or_window_clipping",
                }
            )
    return rows
