"""Vector (line) G-code helpers — bed grid, etc."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GridSettings:
    width_mm: float
    height_mm: float
    origin_x: float = 0.0
    origin_y: float = 0.0
    minor_mm: float = 50.0
    major_mm: float = 100.0
    feed: float = 900.0
    max_power: int = 300  # S value 1..1000
    home_first: bool = False
    inset_mm: float = 0.0


def _line_positions(length_mm: float, step_mm: float) -> list[float]:
    """Interior ticks at step, excluding 0 and length (border drawn separately)."""
    if step_mm <= 0 or length_mm <= 0:
        return []
    out: list[float] = []
    g = step_mm
    # Float-safe: stop before the far edge
    while g < length_mm - 1e-6:
        out.append(round(g, 6))
        g += step_mm
    return out


def grid_to_gcode(settings: GridSettings) -> str:
    """
    Full-bed (or inset) alignment grid as vector burns.
    Matches on-screen preview: lines every minor_mm, over width×height from origin.
    Draws the outer rectangle plus interior vertical/horizontal lines.
    """
    inset = max(0.0, float(settings.inset_mm))
    w = max(1.0, float(settings.width_mm) - 2 * inset)
    h = max(1.0, float(settings.height_mm) - 2 * inset)
    ox = float(settings.origin_x) + inset
    oy = float(settings.origin_y) + inset
    minor = max(1.0, float(settings.minor_mm))
    power = max(1, min(1000, int(settings.max_power)))
    feed = max(100.0, float(settings.feed))

    lines: list[str] = [
        f"; bed-grid {w:.1f}x{h:.1f}mm minor={minor} major={float(settings.major_mm)} power=S{power} feed={feed}",
        "G21",
        "G90",
        "G94",
        "M5",
        "S0",
    ]
    if settings.home_first:
        lines.append("$H")
    lines.append(f"G0 X{ox:.3f} Y{oy:.3f}")
    lines.append(f"M4 S{power}")

    def burn(x0: float, y0: float, x1: float, y1: float) -> None:
        lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        lines.append(f"G1 X{x1:.3f} Y{y1:.3f} F{feed:.1f} S{power}")

    # Outer border
    burn(ox, oy, ox + w, oy)
    burn(ox + w, oy, ox + w, oy + h)
    burn(ox + w, oy + h, ox, oy + h)
    burn(ox, oy + h, ox, oy)

    # Interior verticals (constant X)
    for gx in _line_positions(w, minor):
        burn(ox + gx, oy, ox + gx, oy + h)

    # Interior horizontals (constant Y)
    for gy in _line_positions(h, minor):
        burn(ox, oy + gy, ox + w, oy + gy)

    lines.extend(
        [
            "M5",
            "S0",
            f"G0 X{ox:.3f} Y{oy:.3f}",
            "; end",
        ]
    )
    return "\n".join(lines) + "\n"
