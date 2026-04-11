"""
Generate AppIcon.icns for the macOS .app bundle.

Requires: Pillow
    pip install Pillow

Run:
    python packaging/macos/make_icon.py
"""
import math
import os
import subprocess
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Pillow is required: pip install Pillow")

OUT_DIR = Path(__file__).parent
ICONSET_DIR = OUT_DIR / "AppIcon.iconset"
ICNS_PATH = OUT_DIR / "AppIcon.icns"

SIZES = [16, 32, 64, 128, 256, 512, 1024]


def draw_icon(size: int) -> Image.Image:
    """Draw a simple pen-on-screen icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size * 0.06

    # Rounded rectangle background
    r = size * 0.18
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=r,
        fill=(30, 30, 50, 255),
    )

    # Pen body
    pen_w = size * 0.12
    pen_h = size * 0.55
    cx = size * 0.58
    cy = size * 0.42
    angle = -35.0  # degrees

    # Build pen rectangle points
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    hw, hh = pen_w / 2, pen_h / 2

    def rot(px, py):
        return (cx + px * cos_a - py * sin_a,
                cy + px * sin_a + py * cos_a)

    corners = [rot(-hw, -hh), rot(hw, -hh), rot(hw, hh), rot(-hw, hh)]
    draw.polygon(corners, fill=(124, 58, 237, 255))

    # Pen tip triangle
    tip = rot(0, hh + pen_w * 0.7)
    tl = rot(-hw * 0.6, hh)
    tr = rot(hw * 0.6, hh)
    draw.polygon([tl, tr, tip], fill=(200, 180, 255, 255))

    # Highlight stripe on pen
    hl = [rot(-hw * 0.15, -hh * 0.9), rot(hw * 0.15, -hh * 0.9),
          rot(hw * 0.15, hh * 0.3), rot(-hw * 0.15, hh * 0.3)]
    draw.polygon(hl, fill=(180, 140, 255, 80))

    return img


def build_icns() -> None:
    ICONSET_DIR.mkdir(exist_ok=True)

    for sz in SIZES:
        img = draw_icon(sz)
        img.save(ICONSET_DIR / f"icon_{sz}x{sz}.png")
        if sz <= 512:
            img2 = draw_icon(sz * 2)
            img2.save(ICONSET_DIR / f"icon_{sz}x{sz}@2x.png")

    result = subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(ICNS_PATH)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("iconutil error:", result.stderr)
        # Fallback: copy the 512px PNG as a placeholder
        import shutil
        shutil.copy(ICONSET_DIR / "icon_512x512.png", ICNS_PATH.with_suffix(".png"))
        print("Saved fallback PNG icon (rename to .icns if needed)")
    else:
        print(f"Icon saved to {ICNS_PATH}")

    import shutil
    shutil.rmtree(ICONSET_DIR)


if __name__ == "__main__":
    build_icns()
