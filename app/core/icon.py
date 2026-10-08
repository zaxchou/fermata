"""App icon, drawn in code.

Generating the icon rather than shipping a binary asset keeps the repository
free of opaque blobs and lets the tray icon carry live state: its colour is the
status (green feeding, amber waiting, red error, grey stopped), which is
information the user can read at a glance without opening the window.
"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw

TRAY_GREEN = (21, 163, 74, 255)
TRAY_AMBER = (217, 119, 6, 255)
TRAY_RED = (220, 38, 38, 255)
TRAY_GREY = (148, 163, 184, 255)
TRAY_SLATE = (51, 70, 94, 255)

STATE_COLORS = {
    "running": TRAY_GREEN,
    "waiting": TRAY_AMBER,
    "reconnecting": TRAY_AMBER,
    "error": TRAY_RED,
    "stopped": TRAY_GREY,
}


def make_image(size: int = 64, color: tuple = TRAY_GREEN) -> Image.Image:
    """A speaker glyph in `color` on a transparent background.

    Drawn at 4x and downsampled: GDI renders the 16px tray variant from this
    image, and hard edges at small sizes look like debris.
    """
    scale = 4
    n = size * scale
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def s(v: float) -> float:
        return v * n

    # speaker body: a rounded box plus a cone
    body = [s(0.16), s(0.36), s(0.34), s(0.62)]
    d.rounded_rectangle(body, radius=s(0.04), fill=color)
    d.polygon([(s(0.33), s(0.42)), (s(0.60), s(0.20)),
               (s(0.60), s(0.80)), (s(0.33), s(0.58))], fill=color)

    # two waves
    waves = ((0.66, 0.80, 0.30, 0.70),
             (0.80, 0.94, 0.20, 0.80))
    stroke = max(2, int(s(0.055)))
    for x0, x1, y0, y1 in waves:
        d.arc([s(x0), s(y0), s(x1), s(y1)], start=-58, end=58,
              fill=color, width=stroke)

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
        base = make_image(256, TRAY_SLATE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        base.save(path, format="ICO",
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
        return True
    except Exception:
        return False
