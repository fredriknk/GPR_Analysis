"""Trace-block NMEA association, conservative QC and optional projection.

Geographic coordinates retain their unknown datum until the caller supplies it.
HDOP is a dimensionless dilution factor, never a positional uncertainty in metres.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pynmea2
import xarray as xr

from soil_sensing.inventory import sha256_file
from soil_sensing.profile import QualityFlag, validate_profile

MARKER = re.compile(r"^Trace\s+#(\d+)\s+at\s+position\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$", re.I)


@dataclass(frozen=True)
class GPSOptions:
    max_interpolation_traces: int = 10
    max_interpolation_distance_m: float = 2.0
    max_interpolation_seconds: float = 5.0
    max_speed_mps: float = 10.0
    max_jump_m: float = 10.0
    marker_tolerance_m: float = 0.05
    sentence_disagreement_m: float = 2.0
    source_crs: str | None = None
    target_crs: str | None = None
    antenna_forward_m: float = 0.0
    antenna_right_m: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.max_interpolation_traces, bool) or not isinstance(
            self.max_interpolation_traces, int
        ):
            raise ValueError("max_interpolation_traces must be an integer")
        numeric = (
            self.max_interpolation_distance_m,
            self.max_interpolation_seconds,
            self.max_speed_mps,
            self.max_jump_m,
            self.marker_tolerance_m,
            self.sentence_disagreement_m,
        )
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in numeric):
            raise ValueError("GPS QC limits must be numeric")
        if self.max_interpolation_traces < 0 or any(not np.isfinite(v) or v < 0 for v in numeric):
            raise ValueError("GPS QC limits must be finite and nonnegative")
        if (
            any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                for v in (self.antenna_forward_m, self.antenna_right_m)
            )
            or not np.isfinite([self.antenna_forward_m, self.antenna_right_m]).all()
        ):
            raise ValueError("Antenna lever arm must be finite")
        if bool(self.source_crs) != bool(self.target_crs):
            raise ValueError("Projection requires both source_crs and target_crs")
        if (self.antenna_forward_m or self.antenna_right_m) and not self.target_crs:
            raise ValueError("Lever-arm correction requires projected coordinates")


@dataclass
class _Block:
    number: int
    marker_position_m: float
    gns: object | None = None
    gga: object | None = None
    rmc: object | None = None
    invalid: int = 0
    unusable_fix: int = 0


def _valid_fix(message: object) -> bool:
    if not getattr(message, "lat", "") or not getattr(message, "lon", ""):
        return False
    if not (-90 <= message.latitude <= 90 and -180 <= message.longitude <= 180):
        return False
    kind = message.sentence_type
    if kind == "GNS":
        mode = getattr(message, "mode_indicator", "")
        return bool(mode) and any(c in "ADPRFC" for c in mode)
    if kind == "GGA":
        return int(message.gps_qual or "0") in (1, 2, 3, 4, 5)
    return (
        kind == "RMC"
        and message.status == "A"
        and getattr(message, "mode_indicator", "A") not in ("E", "M", "N", "S")
    )


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Spherical distance solely for QC, not a survey-grade coordinate operation."""
    radius = 6371008.8
    a, b = np.deg2rad([lat1, lat2])
    da, db = b - a, np.deg2rad(lon2 - lon1)
    h = np.sin(da / 2) ** 2 + np.cos(a) * np.cos(b) * np.sin(db / 2) ** 2
    return float(2 * radius * np.arcsin(min(1.0, np.sqrt(h))))


def _utc(block: _Block, fix: object) -> np.datetime64:
    rmc = block.rmc
    if rmc is None or rmc.datestamp is None or rmc.timestamp is None:
        return np.datetime64("NaT", "ns")
    instant = datetime.combine(rmc.datestamp, rmc.timestamp.replace(tzinfo=None), tzinfo=UTC)
    timestamp = getattr(fix, "timestamp", None)
    if timestamp is not None:
        fixed = datetime.combine(rmc.datestamp, timestamp.replace(tzinfo=None), tzinfo=UTC)
        if fixed - instant > timedelta(hours=12):
            fixed -= timedelta(days=1)
        elif instant - fixed > timedelta(hours=12):
            fixed += timedelta(days=1)
        instant = fixed
    return np.datetime64(instant.replace(tzinfo=None), "ns")


def _read_blocks(path: Path):
    block = None
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            match = MARKER.fullmatch(line)
            if match:
                if block is not None:
                    yield block
                block = _Block(int(match[1]), float(match[2]))
                continue
            if block is None or not line.startswith("$"):
                continue
            if line.startswith("$RD1,"):
                continue  # Proprietary receiver status, not an NMEA position sentence.
            # Parse all sentence checksums, but retain only positioning sentences.
            try:
                sentence = pynmea2.parse(line, check=True)
            except pynmea2.ParseError:
                block.invalid += 1
                continue
            kind = getattr(sentence, "sentence_type", None)
            if kind in ("GNS", "GGA", "RMC"):
                if kind == "GNS" and sentence.talker != "GN":
                    continue  # Empty or partial per-constellation fixes are not a new position.
                if _valid_fix(sentence):
                    if getattr(block, kind.lower()) is None:
                        setattr(block, kind.lower(), sentence)
                else:
                    block.unusable_fix += 1
    if block is not None:
        yield block


def locate_profile(
    profile: xr.Dataset, gps_path: Path, options: GPSOptions | None = None
) -> tuple[xr.Dataset, dict]:
    """Return a new located profile and QC summary without changing the raw dataset."""
    options = options or GPSOptions()
    validate_profile(profile)
    if profile.attrs["processing_branch"] != "raw" or "longitude_deg" in profile:
        raise ValueError("GPS association expects an unlocated raw profile")
    source_key = next(
        (name for name in profile.attrs["source_checksums"] if name.casefold().endswith(".gps")),
        None,
    )
    if source_key is None or gps_path.suffix.casefold() != ".gps":
        raise ValueError("A matching GPS sidecar recorded at ingestion is required")
    if not gps_path.as_posix().casefold().endswith(source_key.casefold()):
        raise ValueError("GPS sidecar path does not match the ingested profile")
    if gps_path.stem.casefold() != Path(profile.attrs["profile_id"]).name.casefold():
        raise ValueError("GPS sidecar basename does not match profile")
    gps_hash = sha256_file(gps_path)
    if gps_hash != profile.attrs["source_checksums"][source_key]:
        raise ValueError("GPS source checksum differs from the ingested sidecar")
    n = profile.sizes["trace"]
    lon, lat, hdop, altitude = (np.full(n, np.nan) for _ in range(4))
    sats = np.full(n, -1, dtype=np.int32)
    mode = np.full(n, "", dtype="<U16")
    time = np.full(n, np.datetime64("NaT", "ns"))
    status = np.full(n, "missing", dtype="<U12")
    flags = profile.quality_flags.values.copy()
    marker_seen = np.zeros(n, dtype=bool)
    marker_scale = 0.3048 if profile.attrs["original_hd"]["position_units"] == "ft" else 1.0
    counts = {
        "blocks": 0,
        "invalid_sentences": 0,
        "unusable_fix_blocks": 0,
        "out_of_range_markers": 0,
        "duplicate_markers": 0,
    }
    for block in _read_blocks(gps_path):
        counts["blocks"] += 1
        counts["invalid_sentences"] += block.invalid
        counts["unusable_fix_blocks"] += int(bool(block.unusable_fix))
        i = block.number - 1
        if not 0 <= i < n:
            counts["out_of_range_markers"] += 1
            continue
        if marker_seen[i]:
            counts["duplicate_markers"] += 1
            flags[i] |= int(QualityFlag.GPS_DUPLICATE)
            status[i] = "rejected"
            lon[i] = lat[i] = np.nan
            continue
        marker_seen[i] = True
        if (
            abs(block.marker_position_m * marker_scale - profile.recorded_position_m.values[i])
            > options.marker_tolerance_m
        ):
            flags[i] |= int(QualityFlag.GPS_POSITION_MISMATCH)
            status[i] = "rejected"
            continue
        if block.unusable_fix:
            flags[i] |= int(QualityFlag.GPS_INVALID)
            status[i] = "rejected"
            continue
        fix = block.gns or block.gga or block.rmc
        if fix is None:
            if block.invalid:
                flags[i] |= int(QualityFlag.GPS_INVALID)
                status[i] = "rejected"
            continue
        if block.rmc is not None and fix is not block.rmc:
            mismatch = _metres(fix.latitude, fix.longitude, block.rmc.latitude, block.rmc.longitude)
            if mismatch > options.sentence_disagreement_m:
                flags[i] |= int(QualityFlag.GPS_INVALID)
                status[i] = "rejected"
                continue
        lat[i], lon[i] = fix.latitude, fix.longitude
        time[i] = _utc(block, fix)
        status[i] = "measured"
        mode[i] = getattr(fix, "mode_indicator", "") or getattr(fix, "gps_qual", "") or "A"
        quality = block.gns or block.gga
        if quality is not None:
            hdop[i] = float(getattr(quality, "horizontal_dil", "nan") or "nan")
            sats[i] = int(getattr(quality, "num_sats", "-1") or "-1")
            altitude[i] = float(getattr(quality, "altitude", "nan") or "nan")
    # Reject implausible moves; compare only consecutive measured trace anchors.
    indices = np.flatnonzero(status == "measured")
    previous = None
    for right in indices:
        if previous is None:
            previous = right
            continue
        left = previous
        dist = _metres(lat[left], lon[left], lat[right], lon[right])
        delta = (
            (time[right] - time[left]) / np.timedelta64(1, "s")
            if not np.isnat(time[left]) and not np.isnat(time[right])
            else np.nan
        )
        if (
            (np.isfinite(delta) and delta < 0)
            or dist > options.max_jump_m
            or (np.isfinite(delta) and delta > 0 and dist / delta > options.max_speed_mps)
        ):
            flags[right] |= int(QualityFlag.GPS_SPEED_OUTLIER)
            status[right] = "rejected"
            lat[right] = lon[right] = np.nan
        else:
            previous = right
    # Only interpolate interior holes bracketed by retained anchors in this profile.
    indices = np.flatnonzero(status == "measured")
    distance = profile.distance_m.values
    for left, right in zip(indices[:-1], indices[1:], strict=True):
        if right - left <= 1 or right - left - 1 > options.max_interpolation_traces:
            continue
        if np.any(status[left + 1 : right] == "rejected"):
            continue
        span_m = distance[right] - distance[left]
        if span_m <= 0 or span_m > options.max_interpolation_distance_m:
            continue
        if np.isnat(time[left]) or np.isnat(time[right]):
            continue
        span_s = (time[right] - time[left]) / np.timedelta64(1, "s")
        if not 0 < span_s <= options.max_interpolation_seconds:
            continue
        for j in range(left + 1, right):
            if status[j] == "rejected":
                continue
            weight = (distance[j] - distance[left]) / span_m
            lat[j] = lat[left] + weight * (lat[right] - lat[left])
            lon[j] = lon[left] + weight * (lon[right] - lon[left])
            time[j] = time[left] + np.timedelta64(int(round(weight * span_s * 1e9)), "ns")
            status[j] = "interpolated"
            flags[j] |= int(QualityFlag.GPS_INTERPOLATED)
    located = profile.copy(deep=True)
    located["latitude_deg"] = ("trace", lat, {"datum": options.source_crs or "unknown"})
    located["longitude_deg"] = ("trace", lon, {"datum": options.source_crs or "unknown"})
    located["gps_hdop"] = ("trace", hdop, {"units": "dimensionless"})
    located["gps_satellites"] = ("trace", sats)
    located["gps_fix_mode"] = ("trace", mode)
    located["gps_orthometric_height_m"] = ("trace", altitude, {"vertical_datum": "unknown"})
    located["time_utc"] = ("trace", time)
    located["position_status"] = ("trace", status)
    flags[np.isin(status, ("measured", "interpolated"))] &= np.uint32(
        0xFFFFFFFF ^ int(QualityFlag.POSITION_MISSING)
    )
    located["quality_flags"] = ("trace", flags)
    if options.target_crs:
        from pyproj import CRS, Transformer

        source, target = (
            CRS.from_user_input(options.source_crs),
            CRS.from_user_input(options.target_crs),
        )
        if not source.is_geographic or not target.is_projected:
            raise ValueError("Source CRS must be geographic and target CRS projected")
        if target.axis_info[0].unit_name.lower() not in ("metre", "meter"):
            raise ValueError("Projected CRS must use metres")
        valid = np.isfinite(lon) & np.isfinite(lat)
        east, north = np.full(n, np.nan), np.full(n, np.nan)
        east[valid], north[valid] = Transformer.from_crs(
            source, target, always_xy=True, allow_ballpark=False
        ).transform(lon[valid], lat[valid])
        if options.antenna_forward_m or options.antenna_right_m:
            base_east, base_north = east.copy(), north.copy()
            moved = valid & np.isfinite(east) & np.isfinite(north)
            for j in np.flatnonzero(moved):
                before = j - 1 if j > 0 and moved[j - 1] else j
                after = j + 1 if j + 1 < n and moved[j + 1] else j
                dx = base_east[after] - base_east[before]
                dy = base_north[after] - base_north[before]
                norm = np.hypot(dx, dy)
                if norm <= 0:
                    moved[j] = False
                    continue
                ux, uy = dx / norm, dy / norm
                east[j] += options.antenna_forward_m * ux + options.antenna_right_m * uy
                north[j] += options.antenna_forward_m * uy - options.antenna_right_m * ux
            east[~moved] = north[~moved] = np.nan
        located["x_m"] = ("trace", east)
        located["y_m"] = ("trace", north)
        located.attrs["target_crs"] = target.to_string()
        located.attrs["source_crs"] = source.to_string()
    located.attrs["gps_status"] = "parsed_trace_blocks"
    located.attrs["quality_flag_definitions"] = {flag.name: int(flag) for flag in QualityFlag}
    located.attrs["gps_options"] = vars(options)
    located.attrs["history"] = [
        *profile.attrs["history"],
        {"operation": "locate_gps", "gps_sha256": gps_hash, "options": vars(options)},
    ]
    counts.update(
        {
            name: int(np.count_nonzero(status == name))
            for name in ("measured", "interpolated", "missing", "rejected")
        }
    )
    counts["projected"] = int(np.count_nonzero(np.isfinite(located.x_m.values)))
    counts["unresolved_utc"] = int(np.count_nonzero(np.isnat(time)))
    accepted = np.flatnonzero((status == "measured") | (status == "interpolated"))
    gps_step, odometer_step = [], []
    for left, right in zip(accepted[:-1], accepted[1:], strict=True):
        if right == left + 1:
            gps_step.append(_metres(lat[left], lon[left], lat[right], lon[right]))
            odometer_step.append(float(distance[right] - distance[left]))
    counts["gps_path_length_approx_m"] = float(sum(gps_step))
    counts["odometer_matched_length_m"] = float(sum(odometer_step))
    counts["odometer_gps_step_p95_abs_difference_m"] = (
        float(np.percentile(np.abs(np.subtract(gps_step, odometer_step)), 95)) if gps_step else None
    )
    recorded = profile.recorded_position_m.values
    counts["recorded_odometer_direction"] = (
        "decreasing" if recorded[-1] < recorded[0] else "increasing"
    )
    validate_profile(located)
    return located, counts
