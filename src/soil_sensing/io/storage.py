"""Lossless per-profile Zarr persistence."""

from pathlib import Path

import xarray as xr

from soil_sensing.profile import validate_profile


def save_profile(profile: xr.Dataset, path: Path) -> None:
    validate_profile(profile)
    if path.exists():
        raise ValueError(f"Profile store already exists: {path}")
    encoding = {}
    for name, variable in profile.variables.items():
        chunks = tuple(
            min(256, profile.sizes[d]) if d == "trace" else profile.sizes[d] for d in variable.dims
        )
        encoding[name] = {"chunks": chunks, "_FillValue": None}
    profile.to_zarr(path, mode="w-", consolidated=True, encoding=encoding, zarr_format=2)


def load_profile(path: Path) -> xr.Dataset:
    with xr.open_zarr(path, consolidated=True, chunks=None, mask_and_scale=False) as stored:
        profile = stored.load()
    validate_profile(profile)
    return profile
