"""Scope visualizers: oscilloscope line and Lissajous XY figures."""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


class OscilloscopeEffect(VisualizerEffect):
    """Waveform-style scope line whose amplitude tracks the spectrum."""

    def __init__(self, color=(0, 255, 0), thickness=2, **kw):
        super().__init__(**kw)
        self.color = tuple(int(c) for c in color)
        self.thickness = int(thickness)

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        n = len(sample.bins)
        xs = np.linspace(0, w - 1, n).astype(np.int32)
        amp = (h / 2 - 2)
        phase = sample.t * 6.0
        ys = (h / 2 + np.sin(np.linspace(0, 4 * np.pi, n) + phase)
              * sample.bins * amp).astype(np.int32)
        pts = np.stack([xs, ys], axis=1).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], False, self.color, self.thickness)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)


class LissajousEffect(VisualizerEffect):
    """XY Lissajous figures driven by bass/mid/high."""

    def __init__(self, color=(0, 200, 255), ratio=3.0, **kw):
        super().__init__(**kw)
        self.color = tuple(int(c) for c in color)
        self.ratio = float(ratio)

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        tt = np.linspace(0, 2 * np.pi, 400)
        a = 1.0 + sample.bass * 2.0
        b = self.ratio + sample.high * 2.0
        delta = sample.t
        scale = 0.4 + sample.mid
        xs = (w / 2 + np.sin(a * tt + delta) * (w / 2 - 4) * scale).astype(np.int32)
        ys = (h / 2 + np.sin(b * tt) * (h / 2 - 4) * scale).astype(np.int32)
        pts = np.stack([xs, ys], axis=1).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], False, self.color, 2)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
