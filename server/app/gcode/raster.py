from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# Common Windows font files (name -> relative path under Fonts)
FONT_CANDIDATES: dict[str, list[str]] = {
    "Arial": ["arial.ttf", "Arial.ttf"],
    "Arial Bold": ["arialbd.ttf", "Arial Bold.ttf"],
    "Times New Roman": ["times.ttf", "timesnr.ttf", "Times New Roman.ttf"],
    "Georgia": ["georgia.ttf", "Georgia.ttf"],
    "Verdana": ["verdana.ttf", "Verdana.ttf"],
    "Trebuchet MS": ["trebuc.ttf", "Trebuchet MS.ttf"],
    "Comic Sans MS": ["comic.ttf", "Comic Sans MS.ttf"],
    "Impact": ["impact.ttf", "Impact.ttf"],
    "Courier New": ["cour.ttf", "Courier New.ttf"],
    "Consolas": ["consola.ttf", "Consolas.ttf"],
    "Segoe UI": ["segoeui.ttf", "Segoe UI.ttf"],
    "Calibri": ["calibri.ttf", "Calibri.ttf"],
}

ENGRAVE_MODES = {
    "fill": {
        "label": "Fill / solid ink",
        "description": "Burns solid shapes (letter fills, filled logos).",
    },
    "outline": {
        "label": "Lines / outline only",
        "description": "Burns only edges — best for line art logos.",
    },
}


def list_fonts() -> list[str]:
    available = []
    fonts_dir = Path("C:/Windows/Fonts")
    for name, files in FONT_CANDIDATES.items():
        for f in files:
            if (fonts_dir / f).exists():
                available.append(name)
                break
    return available or ["Default"]


def load_font(name: str, size: int) -> ImageFont.ImageFont:
    fonts_dir = Path("C:/Windows/Fonts")
    for f in FONT_CANDIDATES.get(name, []):
        path = fonts_dir / f
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    for f in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
        path = fonts_dir / f
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


@dataclass
class RasterSettings:
    width_mm: float
    height_mm: float
    origin_x: float = 0.0
    origin_y: float = 0.0
    line_interval_mm: float = 0.2
    feed: float = 1000.0
    max_power: int = 200  # S value 0-1000
    invert: bool = False
    home_first: bool = False
    mode: str = "fill"  # fill | outline
    passes: int = 1  # repeat burn path N times at same power


def image_from_upload(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    return ImageOps.exif_transpose(img).convert("L")


def should_invert_for_burn(img: Image.Image) -> bool:
    """True when image is mostly dark (typical white line-art on black)."""
    small = img.convert("L").resize((64, 64), Image.Resampling.BILINEAR)
    hist = small.histogram()
    total = sum(hist) or 1
    # mean brightness 0..255
    mean = sum(i * hist[i] for i in range(256)) / total
    return mean < 140


def prepare_burn_image(img: Image.Image, invert: bool, mode: str) -> Image.Image:
    """
    Normalize to white background + black ink (burn), then optional outline.
    Laser burns dark pixels only.
    """
    work = img.convert("L")
    if invert:
        work = ImageOps.invert(work)
    # Harden to ink vs empty so gray anti-alias doesn't fill the box
    work = work.point(lambda p: 0 if p < 200 else 255)
    if mode == "outline":
        work = to_outline(work)
        work = work.point(lambda p: 0 if p < 200 else 255)
    return work


def fit_image(img: Image.Image, width_px: int, height_px: int, fit: str = "fill") -> Image.Image:
    """Place grayscale image into exact width_px x height_px box."""
    src = img.convert("L")
    fit = (fit or "fill").lower()
    if fit == "contain":
        ov = ImageOps.contain(src, (width_px, height_px))
        out = Image.new("L", (width_px, height_px), 255)
        out.paste(ov, ((width_px - ov.width) // 2, (height_px - ov.height) // 2))
        return out
    if fit == "cover":
        return ImageOps.fit(src, (width_px, height_px), method=Image.Resampling.LANCZOS)
    # fill / stretch — exact box, may distort
    return src.resize((width_px, height_px), Image.Resampling.LANCZOS)


def render_canvas(
    width_px: int,
    height_px: int,
    text: str = "",
    font_name: str = "Arial",
    shapes: list[dict] | None = None,
    overlay: Image.Image | None = None,
    fit: str = "fill",
) -> Image.Image:
    """White background, black ink — only glyphs/artwork, no filled plate."""
    img = Image.new("L", (width_px, height_px), 255)
    draw = ImageDraw.Draw(img)
    if overlay is not None:
        img.paste(fit_image(overlay, width_px, height_px, fit), (0, 0))
    for shape in shapes or []:
        kind = shape.get("type")
        if kind == "rect":
            draw.rectangle(
                [shape["x"], shape["y"], shape["x"] + shape["w"], shape["y"] + shape["h"]],
                outline=0,
                width=max(1, int(shape.get("stroke", 3))),
            )
        elif kind == "circle":
            r = shape["r"]
            draw.ellipse(
                [shape["cx"] - r, shape["cy"] - r, shape["cx"] + r, shape["cy"] + r],
                outline=0,
                width=max(1, int(shape.get("stroke", 3))),
            )
    if text.strip():
        size = max(12, int(height_px * 0.7))
        font = load_font(font_name, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw > width_px * 0.92 and tw > 0:
            size = max(10, int(size * (width_px * 0.92) / tw))
            font = load_font(font_name, size)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((width_px - tw) / 2, (height_px - th) / 2), text, fill=0, font=font)
    return img


def to_outline(img: Image.Image, threshold: int = 200) -> Image.Image:
    """Keep edge pixels of dark ink, then thicken so burns are continuous."""
    src = img.convert("L")
    w, h = src.size
    edge = Image.new("L", (w, h), 255)
    sp = src.load()
    ep = edge.load()
    for y in range(h):
        for x in range(w):
            if sp[x, y] >= threshold:
                continue
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h or sp[nx, ny] >= threshold:
                    ep[x, y] = 0
                    break
    # Dilate outline 1px so stroke has width (avoids single-pixel flashes)
    return edge.filter(ImageFilter.MinFilter(3))


def _ink_runs(ink_cols: list[int]) -> list[tuple[int, int]]:
    """Collapse sorted column indices into inclusive (start, end) runs."""
    if not ink_cols:
        return []
    cols = sorted(ink_cols)
    runs = []
    start = prev = cols[0]
    for c in cols[1:]:
        if c == prev + 1:
            prev = c
            continue
        runs.append((start, prev))
        start = prev = c
    runs.append((start, prev))
    return runs


def raster_to_gcode(img: Image.Image, settings: RasterSettings) -> str:
    """
    Ink-only raster: one G1 per contiguous horizontal burn run.
    In laser mode ($32=1), G0 keeps beam off while M4 stays armed.
    """
    cols = max(1, int(settings.width_mm / settings.line_interval_mm))
    rows = max(1, int(settings.height_mm / settings.line_interval_mm))
    work = img.convert("L").resize((cols, rows), Image.Resampling.BILINEAR)
    work = prepare_burn_image(work, invert=settings.invert, mode=settings.mode)
    pixels = work.load()

    power = max(1, min(1000, int(settings.max_power)))
    feed = max(100.0, float(settings.feed))
    n_passes = max(1, min(5, int(settings.passes or 1)))

    lines: list[str] = [
        f"; mode={settings.mode} invert={settings.invert} power=S{power} feed={feed} passes={n_passes}",
        "G21",
        "G90",
        "G94",
        "M5",
        "S0",
    ]
    if settings.home_first:
        lines.append("$H")
    lines.append(f"G0 X{settings.origin_x:.3f} Y{settings.origin_y:.3f}")
    # Arm laser once; G0 moves stay dark in laser mode, G1 burns with S
    lines.append(f"M4 S{power}")

    def emit_pass(pass_idx: int) -> None:
        lines.append(f"; pass {pass_idx + 1}/{n_passes}")
        # Alternate start direction each pass so heat/toolpath isn't identical
        row_order = range(rows) if pass_idx % 2 == 0 else range(rows - 1, -1, -1)
        for row in row_order:
            # Image row 0 is the top of the artwork; map it to high machine Y
            y = settings.origin_y + ((rows - 1 - row) * settings.line_interval_mm)
            ink_cols = [col for col in range(cols) if pixels[col, row] < 200]
            if not ink_cols:
                continue
            runs = _ink_runs(ink_cols)
            # Serpentine within pass; flip serpentine sense on odd passes
            serpentine = (row % 2 == 1) ^ (pass_idx % 2 == 1)
            if serpentine:
                runs = list(reversed(runs))
                runs = [(b, a) for a, b in runs]

            for a, b in runs:
                x0 = settings.origin_x + (a * settings.line_interval_mm)
                x1 = settings.origin_x + (b * settings.line_interval_mm)
                lines.append(f"G0 X{x0:.3f} Y{y:.3f}")
                lines.append(f"G1 X{x1:.3f} Y{y:.3f} F{feed:.1f} S{power}")

    for p in range(n_passes):
        emit_pass(p)

    lines.extend(
        [
            "M5",
            "S0",
            f"G0 X{settings.origin_x:.3f} Y{settings.origin_y:.3f}",
            "; end",
        ]
    )
    return "\n".join(lines) + "\n"


PRESETS = {
    # --- Cardboard / paper ---
    "cardboard_light": {
        "label": "Cardboard light (first test)",
        "feed": 1200.0,
        "max_power": 150,  # 15%
        "line_interval_mm": 0.15,
    },
    "cardboard": {
        "label": "Cardboard (recommended)",
        "feed": 1000.0,
        "max_power": 250,  # 25% — verified good mark
        "line_interval_mm": 0.15,
    },
    "cardboard_deep": {
        "label": "Cardboard deep / darker",
        "feed": 700.0,
        "max_power": 350,  # 35%
        "line_interval_mm": 0.12,
    },
    "kraft_paper": {
        "label": "Kraft / packing paper",
        "feed": 1500.0,
        "max_power": 120,  # 12%
        "line_interval_mm": 0.15,
    },
    "cardstock": {
        "label": "Cardstock / greeting card",
        "feed": 1100.0,
        "max_power": 200,  # 20%
        "line_interval_mm": 0.14,
    },
    # --- Wood ---
    "basswood_light": {
        "label": "Basswood light engrave",
        "feed": 900.0,
        "max_power": 300,  # 30%
        "line_interval_mm": 0.15,
    },
    "basswood": {
        "label": "Basswood medium",
        "feed": 700.0,
        "max_power": 450,  # 45%
        "line_interval_mm": 0.12,
    },
    "plywood_light": {
        "label": "Plywood light",
        "feed": 800.0,
        "max_power": 400,  # 40%
        "line_interval_mm": 0.15,
    },
    "plywood": {
        "label": "Plywood medium",
        "feed": 600.0,
        "max_power": 550,  # 55%
        "line_interval_mm": 0.12,
    },
    "hardwood_mark": {
        "label": "Hardwood mark (oak/maple)",
        "feed": 500.0,
        "max_power": 650,  # 65%
        "line_interval_mm": 0.1,
    },
    "bamboo": {
        "label": "Bamboo",
        "feed": 750.0,
        "max_power": 400,  # 40%
        "line_interval_mm": 0.12,
    },
    "cork": {
        "label": "Cork",
        "feed": 1000.0,
        "max_power": 250,  # 25%
        "line_interval_mm": 0.15,
    },
    # --- Leather / fabric ---
    "leather_light": {
        "label": "Leather light",
        "feed": 800.0,
        "max_power": 300,  # 30%
        "line_interval_mm": 0.12,
    },
    "leather": {
        "label": "Leather medium",
        "feed": 600.0,
        "max_power": 450,  # 45%
        "line_interval_mm": 0.1,
    },
    "felt": {
        "label": "Felt / soft fabric",
        "feed": 1200.0,
        "max_power": 200,  # 20%
        "line_interval_mm": 0.15,
    },
    "denim": {
        "label": "Denim / canvas",
        "feed": 900.0,
        "max_power": 350,  # 35%
        "line_interval_mm": 0.12,
    },
    # --- Other ---
    "slate": {
        "label": "Slate coaster",
        "feed": 800.0,
        "max_power": 600,  # 60%
        "line_interval_mm": 0.1,
    },
    "anodized_alu": {
        "label": "Anodized aluminum mark",
        "feed": 1500.0,
        "max_power": 800,  # 80% — fast mark
        "line_interval_mm": 0.08,
    },
    "acrylic_opaque": {
        "label": "Opaque acrylic engrave",
        "feed": 700.0,
        "max_power": 500,  # 50%
        "line_interval_mm": 0.1,
    },
    "rubber_stamp": {
        "label": "Rubber stamp / eraser",
        "feed": 500.0,
        "max_power": 500,  # 50%
        "line_interval_mm": 0.1,
    },
    "glass_frost": {
        "label": "Glass frost (with paint/mask)",
        "feed": 400.0,
        "max_power": 700,  # 70%
        "line_interval_mm": 0.08,
    },
    # Aliases for older UI / scripts
    "cardboard_test": {
        "label": "Cardboard (recommended)",
        "feed": 1000.0,
        "max_power": 250,
        "line_interval_mm": 0.15,
    },
    "cardboard_hot": {
        "label": "Cardboard deep / darker",
        "feed": 700.0,
        "max_power": 350,
        "line_interval_mm": 0.12,
    },
    "wood_light": {
        "label": "Basswood light engrave",
        "feed": 900.0,
        "max_power": 300,
        "line_interval_mm": 0.15,
    },
    "wood_deep": {
        "label": "Plywood medium",
        "feed": 600.0,
        "max_power": 550,
        "line_interval_mm": 0.12,
    },
}

# Preferred order in the UI (aliases omitted)
PRESET_ORDER = [
    "cardboard_light",
    "cardboard",
    "cardboard_deep",
    "kraft_paper",
    "cardstock",
    "basswood_light",
    "basswood",
    "plywood_light",
    "plywood",
    "hardwood_mark",
    "bamboo",
    "cork",
    "leather_light",
    "leather",
    "felt",
    "denim",
    "slate",
    "anodized_alu",
    "acrylic_opaque",
    "rubber_stamp",
    "glass_frost",
]
