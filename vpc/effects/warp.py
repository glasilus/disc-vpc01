"""Warp-family effects: gradient flow, vortex spiral, fractal noise, self-displace.

These four effects exist as a CPU-only family of motion-vector distortions —
direct alternatives to optical-flow datamoshing. Each one uses a different
displacement-field source: previous-frame Sobel gradient, Gaussian-falloff
spiral, fBm noise, and the past frame's own colour channels.
"""
from __future__ import annotations

import cv2
import numpy as np

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


class DerivWarpEffect(BaseEffect):
    """Sobel-of-prev-frame as a motion-vector field — closest CPU analogue to datamosh."""
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE,
                     SegmentType.DROP, SegmentType.SUSTAIN]

    def __init__(self, blend=0.35, **kw):
        super().__init__(**kw)
        self.blend = blend
        self._prev = None

    def apply(self, frame, seg, draft):
        # Always update _prev, even when effect doesn't fire.
        result = super().apply(frame, seg, draft)
        if result is frame:
            self._prev = frame.copy()
        return result

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        if self._prev is None or self._prev.shape != frame.shape:
            self._prev = frame.copy()
            return frame

        gray = cv2.cvtColor(self._prev, cv2.COLOR_RGB2GRAY).astype(np.float32)
        scale_f = 2 if draft else 1
        if draft:
            gray = cv2.resize(gray, (w // scale_f, h // scale_f))

        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)

        if draft:
            gx = cv2.resize(gx, (w, h))
            gy = cv2.resize(gy, (w, h))

        mag = np.sqrt(gx ** 2 + gy ** 2)
        max_mag = float(mag.max()) + 1e-6
        disp_scale = intensity * 40.0
        dx = (gx / max_mag) * disp_scale
        dy = (gy / max_mag) * disp_scale

        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        warped = cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_REFLECT)

        blend = min(0.9, self.blend * (0.5 + intensity))
        if blend > 0:
            prev_disp = cv2.remap(self._prev, map_x * 0.4, map_y * 0.4,
                                  interp, borderMode=cv2.BORDER_REFLECT)
            warped = cv2.addWeighted(warped, 1.0 - blend, prev_disp, blend, 0)

        self._prev = frame.copy()
        return _ensure_uint8(warped)


class VortexWarpEffect(BaseEffect):
    """Gaussian-falloff spiral rotation around frame centre."""
    trigger_types = [SegmentType.BUILD, SegmentType.IMPACT,
                     SegmentType.SUSTAIN, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        cx, cy = w * 0.5, h * 0.5
        sigma = min(w, h) * 0.35
        xs = (np.tile(np.arange(w, dtype=np.float32), (h, 1)) - cx)
        ys = (np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w)) - cy)
        r_sq = xs ** 2 + ys ** 2
        angle = intensity * 5.0 * np.exp(-r_sq / (2.0 * sigma ** 2))
        cos_a = np.cos(angle).astype(np.float32)
        sin_a = np.sin(angle).astype(np.float32)
        map_x = np.clip(xs * cos_a - ys * sin_a + cx, 0, w - 1).astype(np.float32)
        map_y = np.clip(xs * sin_a + ys * cos_a + cy, 0, h - 1).astype(np.float32)

        if draft:
            mh, mw = h // 2, w // 2
            map_xd = cv2.resize(map_x, (mw, mh)) * 0.5
            map_yd = cv2.resize(map_y, (mw, mh)) * 0.5
            small = cv2.resize(frame, (mw, mh))
            result = cv2.remap(small, map_xd, map_yd,
                               cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)
            return _ensure_uint8(cv2.resize(result, (w, h), interpolation=cv2.INTER_NEAREST))

        return _ensure_uint8(
            cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        )


class FractalNoiseWarpEffect(BaseEffect):
    """Domain-warped fractal Brownian motion displacement, reseeded per segment."""
    trigger_types = list(SegmentType)

    def __init__(self, octaves=4, **kw):
        super().__init__(**kw)
        self.octaves = octaves

    def _make_fbm(self, h, w, seed, octaves, draft):
        rng = np.random.RandomState(seed)
        dx = np.zeros((h, w), dtype=np.float32)
        dy = np.zeros((h, w), dtype=np.float32)
        amp = 1.0
        scale = 8 if draft else 4
        for _ in range(octaves):
            nh = max(1, h // scale)
            nw = max(1, w // scale)
            nx = rng.randn(nh, nw).astype(np.float32)
            ny = rng.randn(nh, nw).astype(np.float32)
            nx_up = cv2.resize(nx, (w, h), interpolation=cv2.INTER_LINEAR)
            ny_up = cv2.resize(ny, (w, h), interpolation=cv2.INTER_LINEAR)
            dx += nx_up * amp
            dy += ny_up * amp
            scale = max(2, scale // 2)
            amp *= 0.55
        return dx, dy

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        seed = int(abs(seg.rms * 1e6 + seg.flatness * 1e4)) & 0x7FFFFFFF
        dx, dy = self._make_fbm(h, w, seed, max(2, self.octaves), draft)
        for arr in (dx, dy):
            m = float(np.abs(arr).max()) + 1e-6
            arr /= m
        disp = intensity * 60.0
        dx *= disp
        dy *= disp
        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)
        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        return _ensure_uint8(
            cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)
        )


class SelfDisplaceEffect(BaseEffect):
    """Past frame's RGB channels used as XY displacement vectors."""
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE, SegmentType.DROP,
                     SegmentType.BUILD, SegmentType.SUSTAIN]

    def __init__(self, depth=2, history_len=6, **kw):
        super().__init__(**kw)
        self.depth = depth
        self.history_len = history_len
        self._history = []

    def apply(self, frame, seg, draft):
        # Always append to history regardless of fire decision.
        self._history.append(frame.copy())
        if len(self._history) > self.history_len + 1:
            self._history.pop(0)
        return super().apply(frame, seg, draft)

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        n = len(self._history)

        def get_hist(age):
            idx = max(0, n - 1 - age)
            src = self._history[idx]
            if src.shape != frame.shape:
                return frame
            return src

        d1 = get_hist(self.depth).astype(np.float32)
        d2 = get_hist(min(self.depth * 2, n - 1)).astype(np.float32)
        dx = ((d1[:, :, 0] - 128.0) / 128.0) * intensity * 55.0
        dy = ((d1[:, :, 1] - 128.0) / 128.0) * intensity * 55.0
        dx += ((d2[:, :, 0] - 128.0) / 128.0) * intensity * 25.0
        dy += ((d2[:, :, 2] - 128.0) / 128.0) * intensity * 25.0
        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)
        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        displaced = cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)

        ghost_age = get_hist(1)
        ghost_map_x = np.clip(xs + dx * 0.3, 0, w - 1).astype(np.float32)
        ghost_map_y = np.clip(ys + dy * 0.3, 0, h - 1).astype(np.float32)
        ghost = cv2.remap(ghost_age, ghost_map_x, ghost_map_y,
                          interp, borderMode=cv2.BORDER_WRAP)
        blend = min(0.45, intensity * 0.4)
        result = cv2.addWeighted(displaced, 1.0 - blend, ghost, blend, 0)
        return _ensure_uint8(result)
