"""Native quantitative attributes; display transforms never enter this adapter."""

from dataclasses import asdict, dataclass

import numpy as np
from scipy.signal import hilbert

from soil_sensing.profile import QualityFlag, validate_profile


@dataclass(frozen=True)
class AttributeOptions:
    edge_samples: int = 4
    minimum_segment_samples: int = 16
    padding_samples: int = 16
    rms_samples: int = 5
    rms_clipping_policy: str = "window"

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "rms_clipping_policy":
                if value not in ("window", "trace"):
                    raise ValueError("rms_clipping_policy must be window or trace")
                continue
            if type(value) is not int or value < (0 if name == "padding_samples" else 1):
                raise ValueError(f"{name} must be an appropriate nonnegative integer")
        if self.rms_samples % 2 != 1:
            raise ValueError("rms_samples must be odd")
        if self.minimum_segment_samples <= 2 * self.edge_samples:
            raise ValueError("Minimum segment must exceed both excluded edges")


def native_attributes(profile, options=None):
    """Hilbert on contiguous finite segments, with reflection padding and edge masks.

    Hilbert excludes clipped traces because its support is nonlocal. RMS uses
    only complete, unclipped windows by default; legacy whole-trace exclusion is
    available explicitly. Upstream spreading filters still exclude whole traces.
    No gain, normalization or background removal is applied.
    """
    options = options or AttributeOptions()
    validate_profile(profile)
    if profile.attrs["processing_branch"] != "quantitative":
        raise ValueError("Volume attributes require the quantitative branch")
    data = profile.amplitude.values.astype(float)
    bad = (
        profile.quality_flags.values & int(QualityFlag.ZERO_SIGNAL | QualityFlag.VENDOR_ZERO)
    ) != 0
    clipped = profile.get("adc_limit_mask")
    clipped = np.zeros(data.shape, bool) if clipped is None else clipped.values
    contaminated = clipped.any(axis=1) | (
        (profile.quality_flags.values & int(QualityFlag.ADC_LIMIT)) != 0
    )
    valid_signed = np.isfinite(data) & ~bad[:, None] & ~clipped
    # A flag without sample-level locations cannot establish clean windows.
    unlocated_clip = contaminated & ~clipped.any(axis=1)
    valid_signed &= ~unlocated_clip[:, None]
    if profile.attrs.get("clipping_can_spread_across_windows"):
        valid_signed &= ~contaminated[:, None]
    signed = np.where(valid_signed, data, np.nan)
    envelope = np.full_like(data, np.nan)
    rms = np.full_like(data, np.nan)
    window_valid = valid_signed.copy()
    if options.rms_clipping_policy == "trace":
        window_valid &= ~contaminated[:, None]
    # Sliding windows have finite support. No reflected/zero-padded edge samples
    # are accepted, and a clipped/nonfinite sample invalidates every window using it.
    width = options.rms_samples
    if width <= data.shape[1]:
        windows = np.lib.stride_tricks.sliding_window_view(window_valid, width, axis=1)
        values = np.lib.stride_tricks.sliding_window_view(
            np.where(window_valid, data, 0.0), width, axis=1
        )
        half = width // 2
        power = np.sqrt(np.mean(values**2, axis=-1))
        rms[:, half : data.shape[1] - half or None] = np.where(windows.all(axis=-1), power, np.nan)
    for trace in range(len(data)):
        if bad[trace] or contaminated[trace]:
            continue
        edges = np.diff(np.r_[False, valid_signed[trace], False].astype(int))
        for start, stop in zip(
            np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True
        ):
            if stop - start < options.minimum_segment_samples:
                continue
            segment = data[trace, start:stop]
            pad = min(options.padding_samples, len(segment) - 1)
            padded = np.pad(segment, pad, mode="reflect") if pad else segment
            analytic = np.abs(hilbert(padded))
            analytic = analytic[pad : pad + len(segment)]
            edge = options.edge_samples
            envelope[trace, start + edge : stop - edge] = analytic[edge:-edge]
    return {"signed_amplitude": signed, "envelope": envelope, "rms": rms}
