"""Shared PIL helpers for all platform backends.

JPEG encoding, scaling/grayscale conversion, and frame-diff motion detection —
logic copied verbatim from backends.windows (screenshot_jpeg, screenshot_scaled,
frame_diff), with the module-level screenshot() capture call replaced by an
`img` parameter so any backend can reuse it.
"""

from __future__ import annotations

import io

from PIL import Image


def encode_jpeg(img: Image.Image, quality: int = 80) -> bytes:
    """Return JPEG bytes — faster than PNG for live streaming."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def scale_image(img: Image.Image, scale: float = 1.0,
                grayscale: bool = False) -> Image.Image:
    """
    Scaled/grayscale frame for image-model consumption.
    scale < 1.0 shrinks the image (saves tokens/bandwidth for vision models).
    """
    if scale != 1.0:
        resample = getattr(Image, "Resampling", Image).BILINEAR
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))), resample)
    if grayscale:
        img = img.convert("L")
    return img


# ---------------------------------------------------------------------------
# Frame-diff motion detection — text-based "what moved on screen"
#
# For agents that cannot see images (or want to save bandwidth):
#   8x6 tile grid, per-tile changed-pixel percentage + clickable tile-center
#   coordinates, plus an overall change bounding box.
#   All coordinates are relative to the captured region.
# ---------------------------------------------------------------------------

DIFF_GRID_COLS = 8
DIFF_GRID_ROWS = 6
DIFF_PIX_THRESHOLD = 12   # grayscale intensity difference threshold
DIFF_TILE_MIN_PCT = 1.0   # minimum % for a tile to count as "changed"


def frame_diff(prev: Image.Image, cur: Image.Image) -> dict:
    """Summarise the difference between two frames as structured text/data."""
    import numpy as np
    if prev.size != cur.size:
        cur = cur.resize(prev.size)
    a = np.asarray(prev.convert("L"), dtype=np.int16)
    b = np.asarray(cur.convert("L"), dtype=np.int16)
    d = np.abs(a - b)
    h, w = d.shape
    th, tw = max(1, h // DIFF_GRID_ROWS), max(1, w // DIFF_GRID_COLS)
    mask = d > DIFF_PIX_THRESHOLD
    tiles = []
    for r in range(DIFF_GRID_ROWS):
        for c in range(DIFF_GRID_COLS):
            t = mask[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
            pct = float(t.mean() * 100)
            if pct >= DIFF_TILE_MIN_PCT:
                tiles.append({"row": r, "col": c, "pct": round(pct, 1),
                              "center": [int((c + 0.5) * tw), int((r + 0.5) * th)]})
    bbox = None
    if mask.any():
        ys, xs = np.where(mask)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    return {"changed": bool(tiles), "bbox": bbox,
            "changed_pct": round(float(mask.mean() * 100), 2), "tiles": tiles}
