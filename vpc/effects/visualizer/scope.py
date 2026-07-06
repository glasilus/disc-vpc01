"""Scope visualizers: oscilloscope waveform and Lissajous XY figures.

Both mimic a phosphor CRT scope: a single bright anti-aliased trace with a
soft bloom around it. The oscilloscope reconstructs a *time-domain waveform*
from the spectrum (additive synthesis — each band is a harmonic), so a
bass-heavy moment shows big slow swings and a bright hi-hat shows fast ripple,
exactly like a real audio scope. The Lissajous holds integer frequency ratios
so the figure stays a clean closed curve and lets the audio drive its phase
drift and size instead of its frequencies (non-integer ratios never close and
degenerate into scribble).
"""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


def _phosphor(vis: np.ndarray, sigma: float = 3.0, gain: float = 0.7) -> np.ndarray:
    """Add a soft CRT bloom around a drawn trace (screen-add of a blur)."""
    glow = cv2.GaussianBlur(vis, (0, 0), sigma)
    return cv2.addWeighted(vis, 1.0, glow, gain, 0.0)


class OscilloscopeEffect(VisualizerEffect):
    """Time-domain waveform scope reconstructed from the spectrum.

    The trace is a full-width, per-pixel waveform built by summing one
    harmonic per frequency band (amplitude = band magnitude). Its *shape*
    comes from the spectrum and its *size* from the overall level, so it is
    never a flat 24-point zig-zag — it reads as an actual oscilloscope.
    """

    def __init__(self, color=(60, 255, 120), thickness=2, **kw):
        super().__init__(**kw)
        self.color = tuple(int(c) for c in color)
        self.thickness = max(1, int(thickness))

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        bins = np.asarray(sample.bins, np.float32)
        n = max(1, len(bins))
        x = np.linspace(0.0, 1.0, w, dtype=np.float32)

        # Additive reconstruction: band k drives harmonic (k+1). Each harmonic
        # scrolls at its own rate so the trace drifts and evolves like a scope
        # not quite locked to the signal.
        scroll = sample.t * 2.2
        wave = np.zeros(w, np.float32)
        for k in range(n):
            f = k + 1
            wave += bins[k] * np.sin(2.0 * np.pi * f * x + f * scroll)

        # Shape from spectrum (unit amplitude), size from loudness.
        wave /= (np.abs(wave).max() + 1e-6)
        level = float(np.clip(bins.mean() * 2.5, 0.02, 1.0))
        amp = (h / 2.0 - 2.0) * (0.12 + 0.88 * level)
        ys = (h / 2.0 - wave * amp).astype(np.int32)
        xs = np.linspace(0, w - 1, w).astype(np.int32)

        pts = np.stack([xs, ys], axis=1).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], False, self.color, self.thickness, cv2.LINE_AA)
        vis = _phosphor(vis, sigma=2.5, gain=0.7)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)


class LissajousEffect(VisualizerEffect):
    """XY Lissajous figure — a clean closed curve that morphs with the music.

    Frequencies are pinned to a small integer ratio (a : round(ratio)) so the
    curve always closes; the audio drives the phase drift (how fast it rotates
    through its family) and the amplitude (how large it breathes), which is the
    signature XY-scope motion. Amplitude is capped so it never leaves frame.
    """

    def __init__(self, color=(0, 220, 255), ratio=3.0, **kw):
        super().__init__(**kw)
        self.color = tuple(int(c) for c in color)
        self.ratio = float(ratio)
        self._phase = 0.0

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        tt = np.linspace(0.0, 2.0 * np.pi, 2000, dtype=np.float32)

        a = 2
        b = max(1, int(round(self.ratio)))
        # Phase drift advances every frame → the figure slowly rotates/morphs.
        # Bass nudges the drift speed so it surges on hits.
        self._phase += 0.015 + sample.bass * 0.05
        delta = self._phase

        level = float(np.clip(max(sample.bass, sample.mid, sample.high), 0.0, 1.0))
        scale = min(0.95, 0.55 + 0.4 * level)
        rx = (w / 2.0 - 4.0) * scale
        ry = (h / 2.0 - 4.0) * scale
        xs = (w / 2.0 + np.sin(a * tt + delta) * rx).astype(np.int32)
        ys = (h / 2.0 + np.sin(b * tt) * ry).astype(np.int32)

        pts = np.stack([xs, ys], axis=1).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], False, self.color, 1, cv2.LINE_AA)
        vis = _phosphor(vis, sigma=2.5, gain=0.8)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
