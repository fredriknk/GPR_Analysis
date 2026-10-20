"""Native quantitative attributes; display transforms never enter this adapter."""

from dataclasses import asdict, dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import hilbert

from soil_sensing.profile import QualityFlag, validate_profile


@dataclass(frozen=True)
class AttributeOptions:
    edge_samples: int = 4
    minimum_segment_samples: int = 16
    padding_samples: int = 16
    rms_samples: int = 5

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) is not int or value < (0 if name == "padding_samples" else 1):
                raise ValueError(f"{name} must be an appropriate nonnegative integer")
        if self.rms_samples % 2 != 1:
            raise ValueError("rms_samples must be odd")
        if self.minimum_segment_samples <= 2 * self.edge_samples:
            raise ValueError("Minimum segment must exceed both excluded edges")


def native_attributes(profile, options=None):
    """Hilbert on contiguous finite segments, with reflection padding and edge masks.

    A clipped trace is entirely excluded from envelope/RMS: Hilbert and filters
    spread contamination. Signed samples retain their own, less restrictive mask.
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
    if profile.attrs.get("clipping_can_spread_across_windows"):
        valid_signed &= ~contaminated[:, None]
    signed = np.where(valid_signed, data, np.nan)
    envelope = np.full_like(data, np.nan)
    rms = np.full_like(data, np.nan)
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
            half = options.rms_samples // 2
            power = np.sqrt(
                np.maximum(0.0, uniform_filter1d(segment**2, options.rms_samples, mode="reflect"))
            )
            lo, hi = start + half, stop - half
            if lo < hi:
                rms[trace, lo:hi] = power[half : len(segment) - half or None]
    return {"signed_amplitude": signed, "envelope": envelope, "rms": rms}
