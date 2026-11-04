"""App icon, drawn in code.

The mark matches the one in the window header (index.html `.mark`): a dark
rounded square with a 150deg slate gradient and a centred dot. The dot carries
the engine state (green feeding, amber waiting/reconnecting, red error, grey
stopped), so the tray stays readable without opening the window.

`assets/icon.ico` is generated from `make_image()` and committed so PyInstaller
can embed it in the EXE; runtime never loads it back.
"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw

# Dot colours. MARK_GREEN / TRAY_GREY are `.mark.live` and `--idle` in the UI.
MARK_GREEN = (74, 222, 128, 255)      # #4ade80
TRAY_AMBER = (217, 119, 6, 255)
TRAY_RED = (220, 38, 38, 255)
TRAY_GREY = (148, 163, 184, 255)      # #94a3b8

# Square gradient: `linear-gradient(150deg,#1f2b3a,#33465e)`.
SQUARE_TOP = (31, 43, 58)             # #1f2b3a, top-left
SQUARE_BOTTOM = (51, 70, 94)          # #33465e, bottom-right

STATE_COLORS = {
    "running": MARK_GREEN,
    "waiting": TRAY_AMBER,
    "reconnecting": TRAY_AMBER,
    "error": TRAY_RED,
    "stopped": TRAY_GREY,
}

# Geometry transcribed from the CSS (a 34px box, 10px radius, 11px dot).
_PAD = 0.04                          # transparent margin, keeps 16px legible
_RADIUS = 10 / 34
_DOT = 11 / 34
# CSS 150deg gradient line: (sin 150, -cos 150) in screen coordinates.
_UX, _UY = 0.5, 0.8660254037844386


def _square(n: int) -> Image.Image:
    """Supersampled rounded square with the header's 150deg gradient."""
    pad = n * _PAD
    sq = n - 2 * pad
    c = (n - 1) / 2
    maxp = c * (_UX + _UY)
    a, b = SQUARE_TOP, SQUARE_BOTTOM
    lut = [
        bytes((round(a[0] + (b[0] - a[0]) * i / 255),
               round(a[1] + (b[1] - a[1]) * i / 255),
               round(a[2] + (b[2] - a[2]) * i / 255), 255))
        for i in range(256)
    ]
    rows = []
    for y in range(n):
        # projection of (x-c, y-c) onto the gradient line, at x = 0
        base = (y - c) * _UY - c * _UX
        row = bytearray()
        for x in range(n):
            t = (base + x * _UX + maxp) / (2 * maxp)
            row += lut[0 if t <= 0 else 255 if t >= 1 else int(t * 255)]
        rows.append(bytes(row))
    img = Image.frombytes("RGBA", (n, n), b"".join(rows))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [pad, pad, n - 1 - pad, n - 1 - pad], radius=sq * _RADIUS, fill=255)
    img.putalpha(mask)
    return img


def make_image(size: int = 64, color: tuple = MARK_GREEN) -> Image.Image:
    """The header mark with a state-coloured dot, on transparency.

    Drawn at 4x and downsampled: GDI renders the 16px tray variant from this
    image, and hard edges at small sizes look like debris.
    """
    n = size * 4
    img = _square(n)
    r = (n - 2 * n * _PAD) * _DOT / 2
    c = (n - 1) / 2
    ImageDraw.Draw(img).ellipse([c - r, c - r, c + r, c + r], fill=color)
    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: str) -> bool:
    """Write a multi-resolution .ico for the EXE.

    Run this to regenerate `assets/icon.ico`, which is committed and picked up
    by Fermata.spec as the EXE's icon:

        python -c "from app.core import icon; icon.write_ico('assets/icon.ico')"

    The file has to be on disk for the build; it is not needed at runtime, which
    is why nothing loads it back.
    """
    try:
        base = make_image(256, MARK_GREEN)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        base.save(path, format="ICO",
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
        return True
    except Exception:
        return False
