"""Inspect Sensors & Software text headers without changing source files.

Preserve the vendor's time-zero value; signal ingestion lives in io.dt1 and leaves
time-zero uncorrected until its indexing convention is independently established.
"""

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Header:
    acquisition_date: str | None
    trace_count: int
    samples_per_trace: int
    time_window_ns: float
    time_zero_point: float
    start_position: float
    final_position: float
    step_size: float
    position_units: str
    nominal_frequency_mhz: float
    antenna_separation: float
    raw_fields: dict[str, str]


def read_header(path: Path) -> Header:
    """Read named fields, tolerating blank lines and CRLF, and reject invalid metadata."""
    text = path.read_text(encoding="utf-8-sig", errors="strict")
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key.strip().upper() in fields:
                raise ValueError(f"{path.name}: duplicate header field {key.strip()}")
            fields[key.strip().upper()] = value.strip()

    def number(key: str, *, positive: bool = False) -> float:
        try:
            value = float(fields[key])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path.name}: missing or invalid {key}") from exc
        if not math.isfinite(value) or (positive and value <= 0):
            raise ValueError(f"{path.name}: invalid {key}: {value}")
        return value

    def count(key: str) -> int:
        value = number(key, positive=True)
        if not value.is_integer():
            raise ValueError(f"{path.name}: {key} must be an integer")
        return int(value)

    match = re.search(r"(?m)^\s*(\d{4}-\d{2}-\d{2})\s*$", text)
    acquisition_date = date.fromisoformat(match[1]).isoformat() if match else None
    units = fields.get("POSITION UNITS", "").lower()
    units = {
        "metre": "m",
        "metres": "m",
        "meter": "m",
        "meters": "m",
        "foot": "ft",
        "feet": "ft",
    }.get(units, units)
    if units not in {"m", "ft"}:
        raise ValueError(f"{path.name}: unsupported POSITION UNITS: {units!r}")
    return Header(
        acquisition_date=acquisition_date,
        trace_count=count("NUMBER OF TRACES"),
        samples_per_trace=count("NUMBER OF PTS/TRC"),
        time_window_ns=number("TOTAL TIME WINDOW", positive=True),
        time_zero_point=number("TIMEZERO AT POINT"),
        start_position=number("STARTING POSITION"),
        final_position=number("FINAL POSITION"),
        step_size=number("STEP SIZE USED", positive=True),
        position_units=units,
        nominal_frequency_mhz=number("NOMINAL FREQUENCY", positive=True),
        antenna_separation=number("ANTENNA SEPARATION", positive=True),
        raw_fields=fields,
    )
