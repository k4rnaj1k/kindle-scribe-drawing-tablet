"""
Generate app.ico for the Windows .exe bundle.

Requires: Pillow
    pip install Pillow

Run:
    python packaging/windows/make_icon.py
"""
import math
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    raise SystemExit("Pillow is required: pip install Pillow")

OUT_PATH = Path(__file__).parent / "app.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw_icon(size: int) -> Image.Image:
    """Draw a simple pen-on-screen icon (same design as macOS)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size * 0.06

    r = max(2, int(size * 0.18))
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=r,
        fill=(30, 30, 50, 255),
    )

    pen_w = size * 0.12
    pen_h = size * 0.55
    cx = size * 0.58
    cy = size * 0.42
    angle = -35.0
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    hw, hh = pen_w / 2, pen_h / 2

    def rot(px, py):
        return (cx + px * cos_a - py * sin_a,
                cy + px * sin_a + py * cos_a)

    corners = [rot(-hw, -hh), rot(hw, -hh), rot(hw, hh), rot(-hw, hh)]
    draw.polygon(corners, fill=(124, 58, 237, 255))

    tip = rot(0, hh + pen_w * 0.7)
    tl = rot(-hw * 0.6, hh)
    tr = rot(hw * 0.6, hh)
    draw.polygon([tl, tr, tip], fill=(200, 180, 255, 255))

    hl = [rot(-hw * 0.15, -hh * 0.9), rot(hw * 0.15, -hh * 0.9),
          rot(hw * 0.15, hh * 0.3), rot(-hw * 0.15, hh * 0.3)]
    draw.polygon(hl, fill=(180, 140, 255, 80))

    return img


def build_ico() -> None:
    frames = [draw_icon(s).convert("RGBA") for s in SIZES]
    frames[0].save(
        OUT_PATH,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=frames[1:],
    )
    print(f"Icon saved to {OUT_PATH}")


if __name__ == "__main__":
    build_ico()
