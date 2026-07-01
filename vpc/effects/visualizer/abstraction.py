"""Abstraction visualizers: plasma field, beat particles, flow field."""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


class PlasmaFieldEffect(VisualizerEffect):
    """Procedural plasma; colour and speed modulated by the bands."""

    def __init__(self, scale=0.04, **kw):
        super().__init__(**kw)
        self.scale = float(scale)

    def _render(self, h, w, sample):
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        t = sample.t * (1.0 + sample.mid * 3.0)
        s = self.scale
        v = (np.sin(xx * s + t) + np.sin(yy * s + t * 1.3)
             + np.sin((xx + yy) * s * 0.5 + t * 0.7)
             + np.sin(np.sqrt((xx - w / 2) ** 2 + (yy - h / 2) ** 2) * s + t))
        v = (v + 4) / 8.0
        hue = ((v * 180 + sample.bass * 90) % 180).astype(np.uint8)
        sat = np.full((h, w), 255, np.uint8)
        val = np.clip(v * 255 * (0.5 + sample.high), 0, 255).astype(np.uint8)
        hsv = cv2.merge([hue, sat, val])
        vis = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        return vis, val


class BeatParticlesEffect(VisualizerEffect):
    """Particles emitted from the centre; bass throws a burst on the beat."""

    def __init__(self, count=120, gravity=0.3, **kw):
        super().__init__(**kw)
        self.count = int(count)
        self.gravity = float(gravity)
        self._p = None     # (N,4): x, y, vx, vy
        self._life = None  # (N,) 0..1

    def apply(self, frame, seg, draft):
        # Step physics on every frame so motion is continuous regardless of
        # trigger/chance gating (mirrors PaintCanvasEffect's history pattern).
        self._step(frame.shape[0], frame.shape[1], seg)
        return super().apply(frame, seg, draft)

    def _ensure(self, h, w):
        if self._p is None:
            self._p = np.zeros((self.count, 4), np.float32)
            self._p[:, 0] = np.random.uniform(0, w, self.count)
            self._p[:, 1] = np.random.uniform(0, h, self.count)
            self._life = np.zeros(self.count, np.float32)

    def _step(self, h, w, seg):
        self._ensure(h, w)
        live = getattr(seg, 'live', None)
        bass = float(getattr(live, 'bass', 0.0) or 0.0)
        beat = bool(getattr(live, 'beat', False))
        if beat:
            k = max(1, int(self.count * (0.3 + bass)))
            idx = np.random.choice(self.count, k, replace=False)
            ang = np.random.uniform(0, 2 * np.pi, k)
            spd = (2 + bass * 8)
            self._p[idx, 0] = w / 2
            self._p[idx, 1] = h / 2
            self._p[idx, 2] = np.cos(ang) * spd
            self._p[idx, 3] = np.sin(ang) * spd
            self._life[idx] = 1.0
        self._p[:, 0] += self._p[:, 2]
        self._p[:, 1] += self._p[:, 3]
        self._p[:, 3] += self.gravity
        self._life *= 0.96

    def _render(self, h, w, sample):
        self._ensure(h, w)
        vis = np.zeros((h, w, 3), np.uint8)
        for (x, y, _vx, _vy), l in zip(self._p, self._life):
            if l <= 0.02:
                continue
            c = int(255 * l)
            cv2.circle(vis, (int(x) % w, int(y) % h), 2, (c, c, 255 - c), -1)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)


class FlowFieldEffect(VisualizerEffect):
    """A self-advecting trail buffer flowing under the spectrum."""

    def __init__(self, noise_scale=0.02, **kw):
        super().__init__(**kw)
        self.noise_scale = float(noise_scale)
        self._acc = None

    def _render(self, h, w, sample):
        if self._acc is None or self._acc.shape[:2] != (h, w):
            self._acc = np.zeros((h, w, 3), np.float32)
        self._acc *= 0.90   # fade trails
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        t = sample.t
        ang = (np.sin(xx * self.noise_scale + t)
               + np.cos(yy * self.noise_scale - t)) * np.pi
        mag = 4.0 * (0.3 + sample.mid)
        map_x = np.clip(xx + np.cos(ang) * mag, 0, w - 1).astype(np.float32)
        map_y = np.clip(yy + np.sin(ang) * mag, 0, h - 1).astype(np.float32)
        self._acc = cv2.remap(self._acc, map_x, map_y, cv2.INTER_LINEAR)
        # Inject energy at the centre, coloured by the bands.
        cv2.circle(self._acc, (w // 2, h // 2), int(4 + sample.bass * 20),
                   (0.0, 255.0 * sample.high, 255.0 * sample.bass), -1)
        vis = np.clip(self._acc, 0, 255).astype(np.uint8)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
