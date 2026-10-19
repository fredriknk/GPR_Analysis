"""Optional homogeneous finite-offset depth conversion with explicit calibration."""

from dataclasses import asdict, dataclass

import numpy as np
import xarray as xr

from soil_sensing.profile import validate_profile


@dataclass(frozen=True)
class VelocityCalibration:
    calibration_id: str
    evidence: str
    velocity_m_per_ns: float
    standard_uncertainty_m_per_ns: float
    time_zero_standard_uncertainty_ns: float

    def __post_init__(self) -> None:
        if not self.calibration_id or not self.evidence:
            raise ValueError("Velocity requires a calibration ID and evidence")
        if not isinstance(self.calibration_id, str) or not isinstance(self.evidence, str):
            raise ValueError("Velocity calibration ID and evidence must be text")
        values = (
            self.velocity_m_per_ns,
            self.standard_uncertainty_m_per_ns,
            self.time_zero_standard_uncertainty_ns,
        )
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
            raise ValueError("Velocity calibration values must be numeric")
        if not np.isfinite(values).all() or values[0] <= 0 or values[1] < 0 or values[2] < 0:
            raise ValueError("Invalid velocity or calibration uncertainty")


def add_depth_axis(profile: xr.Dataset, calibration: VelocityCalibration) -> xr.Dataset:
    """Attach calibrated depth, masking air/direct-wave times and unsupported samples.

    Uses a homogeneous horizontal reflector and the recorded Tx/Rx separation.
    This is not a layered-earth migration or soil-property model.
    """
    validate_profile(profile)
    if profile.attrs["processing_branch"] != "quantitative" or "corrected_twt_ns" not in profile:
        raise ValueError("Depth requires quantitative data with reviewed physical time zero")
    if "depth_m" in profile:
        raise ValueError("Depth axis already exists")
    offset = profile.attrs.get("antenna_separation_m")
    if offset is None or not np.isfinite(offset) or offset < 0:
        raise ValueError("Known nonnegative antenna separation is required")
    time = profile.corrected_twt_ns.values.astype(float)
    velocity = calibration.velocity_m_per_ns
    half_path = velocity * time / 2
    squared = half_path**2 - (offset / 2) ** 2
    valid = (time > 0) & (squared > 0)
    depth = np.full_like(time, np.nan)
    uncertainty = np.full_like(time, np.nan)
    depth[valid] = np.sqrt(squared[valid])
    dz_dv = velocity * time[valid] ** 2 / (4 * depth[valid])
    dz_dt = velocity**2 * time[valid] / (4 * depth[valid])
    uncertainty[valid] = np.hypot(
        dz_dv * calibration.standard_uncertainty_m_per_ns,
        dz_dt * calibration.time_zero_standard_uncertainty_ns,
    )
    result = profile.assign_coords(
        depth_m=("sample", depth), depth_standard_uncertainty_m=("sample", uncertainty)
    )
    result.attrs["depth_model"] = {
        "method": "homogeneous_horizontal_reflector_finite_offset",
        "calibration": asdict(calibration),
        "antenna_separation_m": float(offset),
        "unsupported_samples": int((~valid).sum()),
    }
    result.attrs["history"] = [
        *profile.attrs["history"],
        {"operation": "add_depth_axis", "model": result.attrs["depth_model"]},
    ]
    validate_profile(result)
    return result
