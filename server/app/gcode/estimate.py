"""Rough job duration from G-code motion (mm / feed)."""

from __future__ import annotations

import math
import re

# Typical Ortur / GRBL rapid when F is omitted on G0
DEFAULT_RAPID_MM_MIN = 3000.0
# Planner / serial overhead per accepted line
LINE_OVERHEAD_S = 0.004
# Homing cycle when send starts with home_first
HOME_EST_SECONDS = 25.0

_AXIS = re.compile(r"([XYZ])\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", re.I)
_FEED = re.compile(r"\bF\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", re.I)
_MOTION = re.compile(r"^G0*([01])\b", re.I)


def estimate_gcode_seconds(
    gcode: str,
    *,
    rapid_mm_min: float = DEFAULT_RAPID_MM_MIN,
    include_home: bool = False,
) -> float:
    """Sum motion time for G0/G1 moves. Comments and non-motion lines ignored."""
    x = y = 0.0
    feed = 1000.0
    total = 0.0
    has_home = False

    for raw in gcode.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        upper = line.upper()
        if upper.startswith("$H"):
            has_home = True
            continue

        feed_m = _FEED.search(line)
        if feed_m:
            try:
                feed = max(1.0, float(feed_m.group(1)))
            except ValueError:
                pass

        motion = _MOTION.match(line)
        if not motion:
            total += LINE_OVERHEAD_S
            continue

        nx, ny = x, y
        for axis, val in _AXIS.findall(line):
            try:
                v = float(val)
            except ValueError:
                continue
            if axis.upper() == "X":
                nx = v
            elif axis.upper() == "Y":
                ny = v

        dist = math.hypot(nx - x, ny - y)
        x, y = nx, ny
        if dist <= 0:
            total += LINE_OVERHEAD_S
            continue

        mode = motion.group(1)
        rate = rapid_mm_min if mode == "0" else feed
        total += (dist / rate) * 60.0 + LINE_OVERHEAD_S

    if include_home or has_home:
        total += HOME_EST_SECONDS
    return max(0.0, total)


def format_duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"
