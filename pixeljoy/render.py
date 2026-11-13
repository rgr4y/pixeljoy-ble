"""Phone-side rendering helpers (the firmware has no fonts, so we rasterise)."""

from __future__ import annotations

from .protocol import Glyph, simple_png


def render_glyphs(text: str, height: int = 16, font_path: str | None = None, threshold: int = 128) -> list[Glyph]:
    """Rasterise each character to a 1-bpp glyph ``height`` pixels tall."""
    from PIL import Image, ImageDraw, ImageFont

    if font_path:
        font = ImageFont.truetype(font_path, height)
    else:
        try:
            font = ImageFont.load_default(size=height)
        except TypeError:  # Pillow < 10.1
            font = ImageFont.load_default()
    glyphs = []
    for ch in text:
        if ch == " ":
            w = max(3, height // 3)
            glyphs.append(Glyph([[0] * w for _ in range(height)]))
            continue
        left, _, right, _ = font.getbbox(ch)
        w = max(1, right - min(left, 0))
        img = Image.new("L", (w, height), 0)
        ImageDraw.Draw(img).text((-min(left, 0), 0), ch, fill=255, font=font)
        px = img.load()
        rows = [[1 if px[x, y] >= threshold else 0 for x in range(w)] for y in range(height)]
        glyphs.append(Glyph(rows))
    return glyphs


def image_to_png(path: str, width: int, height: int, fit: str = "stretch") -> bytes:
    """``fit``: stretch (ignore aspect), contain (letterbox in black), cover (crop to fill)."""
    from PIL import Image, ImageOps

    img = Image.open(path).convert("RGB")
    size, lanczos = (width, height), Image.LANCZOS
    if fit == "contain":
        img = ImageOps.pad(img, size, lanczos, color=(0, 0, 0))
    elif fit == "cover":
        img = ImageOps.fit(img, size, lanczos)
    else:
        img = img.resize(size, lanczos)
    px = img.load()
    return simple_png([[px[x, y] for x in range(width)] for y in range(height)])
