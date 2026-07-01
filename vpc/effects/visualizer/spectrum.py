"""Spectrum visualizers: linear equalizer bars and radial corona."""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


def _resample_bins(bins: np.ndarray, n: int) -> np.ndarray:
    if len(bins) == n:
        return bins
    x = np.linspace(0, len(bins) - 1, n)
    return np.interp(x, np.arange(len(bins)), bins)


class SpectrumBarsEffect(VisualizerEffect):
    """Classic WMP equalizer: per-band bars with peak-hold smoothing."""

    def __init__(self, n_bands=24, color=(0, 255, 0), mirror=False, **kw):
        super().__init__(**kw)
        self.n_bands = int(n_bands)
        self.color = tuple(int(c) for c in color)
        self.mirror = bool(mirror)

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        vals = _resample_bins(sample.bins, self.n_bands)
        bw = max(1, w // self.n_bands)
        col = self.color
        for i, v in enumerate(vals):
            bh = int(float(v) * (h - 2))
            if bh <= 0:
                continue
            x0 = i * bw
            if self.mirror:
                y0 = (h - bh) // 2
                cv2.rectangle(vis, (x0, y0), (x0 + bw - 1, y0 + bh), col, -1)
            else:
                cv2.rectangle(vis, (x0, h - bh), (x0 + bw - 1, h - 1), col, -1)
        field = cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
        return vis, field


class RadialSpectrumEffect(VisualizerEffect):
    """Equalizer bars wrapped around a circle — a pulsing corona."""

    def __init__(self, rays=48, color=(0, 255, 128), rotate=0.0, **kw):
        super().__init__(**kw)
        self.rays = int(rays)
        self.color = tuple(int(c) for c in color)
        self.rotate = float(rotate)

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        cx, cy = w // 2, h // 2
        r0 = min(h, w) // 6
        vals = _resample_bins(sample.bins, self.rays)
        col = self.color
        for i, v in enumerate(vals):
            ang = (i / self.rays) * 2 * np.pi + self.rotate + sample.t * 0.5
            r1 = r0 + int(float(v) * min(h, w) * 0.4)
            x1 = int(cx + np.cos(ang) * r1)
            y1 = int(cy + np.sin(ang) * r1)
            x0 = int(cx + np.cos(ang) * r0)
            y0 = int(cy + np.sin(ang) * r0)
            cv2.line(vis, (x0, y0), (x1, y1), col, 2)
        field = cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
        return vis, field
