"""Deterministic metric demo data. Never a fallback for missing field metadata."""

import numpy as np
import xarray as xr

from soil_sensing.processing.gpr import ProcessingOptions, process_profile
from soil_sensing.spatial.plots import PlotGeometry


def synthetic_plots():
    """Four independent 100 m plots with a known event near (65, 50, 38 ns)."""
    plots, profiles, exclusions = {}, [], {}
    axis = np.arange(100, dtype=float)
    for p in range(4):
        plot_id = f"synthetic_{p + 1:02d}"
        ox, oy = 500000.0 + p * 1000, 6600000.0
        polygon = ((ox, oy), (ox + 100, oy), (ox + 100, oy + 100), (ox, oy + 100))
        plots[plot_id] = PlotGeometry(
            plot_id, "reviewed", "EPSG:32632", polygon, "synthetic fixture; not field control"
        )
        exclusions[plot_id] = [
            [[ox + 42, oy + 30], [ox + 58, oy + 30], [ox + 58, oy + 70], [ox + 42, oy + 70]]
        ]
        for line, y in enumerate(range(10, 100, 10)):
            x = np.arange(0, 101, 2, dtype=float)
            n = len(x)
            # Signed wave packet in time; location and time vary explicitly by plot.
            event = 38 + p * 4 + 0.025 * (x - 65) ** 2 / 10
            strength = 200 + 600 * np.exp(-((x - 65) ** 2 + (y - 50) ** 2) / 400)
            t = axis[None, :] - event[:, None]
            data = strength[:, None] * (1 - 2 * (t / 3) ** 2) * np.exp(-((t / 3) ** 2))
            data += 25 * np.sin(axis[None, :] / 4 + x[:, None] / 15 + p)
            missing = (x >= 42) & (x <= 58) & (30 <= y <= 70)
            projected_x, projected_y = x + ox, np.full(n, oy + y, dtype=float)
            projected_x[missing], projected_y[missing] = np.nan, np.nan
            statuses = np.where(missing, "missing", "measured").astype("<U12")
            statuses[(x == 20) & ~missing] = "interpolated"
            profile_id = f"{plot_id}/line_{line:02d}"
            raw = xr.Dataset(
                data_vars={
                    "amplitude": (("trace", "sample"), data.astype(np.float32)),
                    "trace_id": ("trace", [f"demo/{profile_id}:{i}" for i in range(n)]),
                    "distance_m": ("trace", x),
                    "recorded_position_m": ("trace", x),
                    "x_m": ("trace", projected_x),
                    "y_m": ("trace", projected_y),
                    "elevation_m": ("trace", np.full(n, np.nan)),
                    "horizontal_uncertainty_m": ("trace", np.full(n, np.nan)),
                    "time_utc": (
                        "trace",
                        np.datetime64("2026-01-01T12:00:00", "ns")
                        + np.arange(n).astype("timedelta64[s]"),
                    ),
                    "position_status": ("trace", statuses),
                    "quality_flags": ("trace", np.where(missing, 1, 0).astype(np.uint32)),
                    "trace_header_bytes": (("trace", "header_byte"), np.zeros((n, 128), np.uint8)),
                },
                coords={
                    "trace": np.arange(n),
                    "sample": np.arange(100),
                    "twt_ns": ("sample", axis),
                },
                attrs={
                    "schema_version": "0.2",
                    "source_id": "synthetic-demo-v1",
                    "profile_id": profile_id,
                    "plot_id": plot_id,
                    "survey_id": "synthetic-session",
                    "source_crs": "EPSG:4326",
                    "target_crs": "EPSG:32632",
                    "vertical_datum": None,
                    "source_checksums": {},
                    "sample_interval_ns": 1.0,
                    "time_axis_convention": "synthetic arange(N) ns; no depth conversion",
                    "processing_branch": "raw",
                    "amplitude_units": "synthetic ADC counts",
                    "antenna_frequency_mhz": None,
                    "antenna_separation_m": None,
                    "assignment": {
                        "assignment_evidence": "synthetic fixture",
                        "reviewer": "generator",
                    },
                    "history": [{"operation": "synthetic_fixture", "generator": "demo-v1"}],
                },
            )
            profiles.append(process_profile(raw, ProcessingOptions(remove_dc=False)))
    return profiles, plots, exclusions
