#!/usr/bin/env python3
"""Pre-render the Mello boot splash as raw RGB565 for /dev/fb0.

Run once at install/migration time to generate the .raw file.
At boot, ExecStartPre writes it with a simple `cat` (instant).

Usage:
    python splash-fb.py render   # generate mello-splash.raw
    python splash-fb.py show     # write to /dev/fb0 directly (fallback)
"""
import sys
from pathlib import Path

LOGO_PATH = Path(__file__).parent / 'plymouth' / 'mello-logo-boot-rotated.png'
RAW_PATH = Path(__file__).parent / 'mello-splash.raw'


def render():
    """Pre-render the splash to a raw RGB565 file."""
    import numpy as np
    from PIL import Image

    try:
        w, h = open('/sys/class/graphics/fb0/virtual_size').read().strip().split(',')
        fb_w, fb_h = int(w), int(h)
    except (OSError, ValueError):
        fb_w, fb_h = 720, 1280  # Default for Mello display

    logo = np.array(Image.open(LOGO_PATH).convert('RGB'))
    lh, lw = logo.shape[:2]

    r = (logo[:, :, 0].astype(np.uint16) >> 3) << 11
    g = (logo[:, :, 1].astype(np.uint16) >> 2) << 5
    b = logo[:, :, 2].astype(np.uint16) >> 3
    logo_565 = (r | g | b).astype(np.uint16)

    canvas = np.zeros((fb_h, fb_w), dtype=np.uint16)
    oy = (fb_h - lh) // 2
    ox = (fb_w - lw) // 2
    canvas[oy:oy + lh, ox:ox + lw] = logo_565

    RAW_PATH.write_bytes(canvas.tobytes())
    print(f'Rendered {fb_w}x{fb_h} splash to {RAW_PATH} ({RAW_PATH.stat().st_size} bytes)')


def show():
    """Write pre-rendered splash (or render on the fly) to /dev/fb0."""
    if not RAW_PATH.exists():
        render()
    with open('/dev/fb0', 'wb') as fb:
        fb.write(RAW_PATH.read_bytes())


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'show'
    if cmd == 'render':
        render()
    else:
        show()
