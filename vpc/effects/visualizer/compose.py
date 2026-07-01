"""Four compositing modes shared by every visualizer renderer.

Each renderer produces a visual (HxWx3) plus a single-channel field (HxW) that
is its luminance. The compositor decides how that visual meets the source:

    replace  — visual fully replaces the frame (full-screen visualizer)
    over     — blend the visual over the source (screen / add / alpha)
    warp     — field's brightness gradient displaces the source (cv2.remap)
    mask     — field's brightness reveals the source against black
"""
from __future__ import annotations

import cv2
import numpy as np


def _blend(src: np.ndarray, vis: np.ndarray, mode: str) -> np.ndarray:
    s = src.astype(np.float32)
    v = vis.astype(np.float32)
    if mode == 'add':
        return np.clip(s + v, 0, 255)
    if mode == 'alpha':
        return v
    # screen (default)
    return 255.0 - (255.0 - s) * (255.0 - v) / 255.0


def composite(src: np.ndarray, visual: np.ndarray, field: np.ndarray,
              mode: str, opacity: float = 0.85, blend: str = 'screen') -> np.ndarray:
    """Compose ``visual`` onto ``src`` per ``mode``. Returns HxWx3 uint8."""
    h, w = src.shape[:2]
    if visual.shape[:2] != (h, w):
        visual = cv2.resize(visual, (w, h))
    if field.shape[:2] != (h, w):
        field = cv2.resize(field, (w, h))

    if mode == 'over':
        blended = _blend(src, visual, blend)
        out = src.astype(np.float32) * (1 - opacity) + blended * opacity
        return np.clip(out, 0, 255).astype(np.uint8)

    if mode == 'warp':
        f = field.astype(np.float32) / 255.0
        gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=5)
        gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=5)
        amp = 30.0 * opacity
        map_x = np.tile(np.arange(w), (h, 1)).astype(np.float32) + gx * amp
        map_y = np.tile(np.arange(h)[:, None], (1, w)).astype(np.float32) + gy * amp
        return cv2.remap(src, map_x, map_y, cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REFLECT)

    if mode == 'mask':
        a = (field.astype(np.float32) / 255.0)[..., None]
        out = src.astype(np.float32) * a
        return np.clip(out, 0, 255).astype(np.uint8)

    # 'replace' and any unknown mode
    return visual.astype(np.uint8)
