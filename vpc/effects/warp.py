"""Warp-family effects: gradient flow, vortex spiral, fractal noise, self-displace.

These four effects exist as a CPU-only family of motion-vector distortions —
direct alternatives to optical-flow datamoshing. Each one uses a different
displacement-field source: previous-frame Sobel gradient, Gaussian-falloff
spiral, fBm noise, and the past frame's own colour channels.

All warps maintain a per-effect frame counter (`_t`) that advances on every
call to `apply()` regardless of whether the effect fires. The counter drives
slow phase modulations (Lissajous-moving centres, evolving 3-D noise slice,
breathing amplitude) so the displacement field is in continuous motion
within a sustained segment instead of snapping to a new static field.
"""
from __future__ import annotations

import cv2
import numpy as np
from opensimplex import noise3array

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


class _Warpable(BaseEffect):
    """Mixin: monotonic frame counter advanced on every apply()."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._t = 0

    def apply(self, frame, seg, draft):
        self._t += 1
        return super().apply(frame, seg, draft)


class DerivWarpEffect(_Warpable):
    """Sobel-of-prev-frame as a motion-vector field with a slow rotational drift.

    The Sobel gradient gives the local "where is the edge" direction; on top
    of that we add a slow swirl whose strength oscillates with time so the
    field never freezes into a still pattern even on a static input.
    """
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE,
                     SegmentType.DROP, SegmentType.SUSTAIN]

    def __init__(self, blend=0.35, **kw):
        super().__init__(**kw)
        self.blend = blend
        self._prev = None

    def apply(self, frame, seg, draft):
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

        max_mag = float(np.sqrt(gx ** 2 + gy ** 2).max()) + 1e-6
        # Time-varying amplitude — breathes ±25% around the base.
        breath = 1.0 + 0.25 * np.sin(self._t * 0.13)
        disp_scale = intensity * 40.0 * breath
        dx = (gx / max_mag) * disp_scale
        dy = (gy / max_mag) * disp_scale

        # Slow rotational drift superimposed on top of the Sobel field.
        # The whole frame rocks back and forth around its centre while the
        # local gradient warp does its thing.
        cx, cy = w * 0.5, h * 0.5
        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1)) - cx
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w)) - cy
        swirl_amp = intensity * 0.06 * np.sin(self._t * 0.07)
        dx += -ys * swirl_amp
        dy += xs * swirl_amp

        map_x = np.clip(xs + cx + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + cy + dy, 0, h - 1).astype(np.float32)

        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        warped = cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_REFLECT)

        blend = min(0.9, self.blend * (0.5 + intensity))
        if blend > 0:
            prev_disp = cv2.remap(self._prev, map_x * 0.4, map_y * 0.4,
                                  interp, borderMode=cv2.BORDER_REFLECT)
            warped = cv2.addWeighted(warped, 1.0 - blend, prev_disp, blend, 0)

        self._prev = frame.copy()
        return _ensure_uint8(warped)


class VortexWarpEffect(_Warpable):
    """Gaussian-falloff spiral whose centre wanders on a Lissajous curve.

    Centre, angular speed and falloff sigma all evolve with `_t`, so the
    spiral is never the same two frames in a row — it precesses, the
    rotation rate breathes, and the affected region swells and shrinks.
    """
    trigger_types = [SegmentType.BUILD, SegmentType.IMPACT,
                     SegmentType.SUSTAIN, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        t = self._t * 0.04
        # Lissajous-wandering centre — never repeats over short timescales.
        cx = w * (0.5 + 0.20 * np.sin(t * 1.0))
        cy = h * (0.5 + 0.18 * np.sin(t * 1.3 + 0.7))
        sigma = min(w, h) * (0.35 + 0.15 * np.sin(t * 0.6))
        # Direction reverses periodically so the swirl breathes.
        rot_mod = np.sin(t * 0.9)

        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1)) - cx
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w)) - cy
        r_sq = xs ** 2 + ys ** 2
        angle = intensity * 5.0 * rot_mod * np.exp(-r_sq / (2.0 * sigma ** 2))
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


class FractalNoiseWarpEffect(_Warpable):
    """Domain-warped fractal noise field that flows through a 3-D noise volume.

    The displacement field is sampled from `opensimplex.noise3` with a slowly
    advancing z-coordinate (`_t·dt`). That makes the field a continuous
    cross-section of an animated 3-D fluid — it streams instead of being
    reseeded as a still pattern per segment.
    """
    trigger_types = list(SegmentType)

    def __init__(self, octaves=4, **kw):
        super().__init__(**kw)
        self.octaves = octaves

    def _make_flow_field(self, h, w, t, octaves, draft):
        """Build dx, dy by summing octaves of opensimplex noise3.

        Sampled on a coarse grid via the C-vectorised `noise3array`, then
        upsampled with linear interp. The z-coordinate scales with `_t`,
        so the field flows continuously instead of being reseeded.
        """
        dx = np.zeros((h, w), dtype=np.float32)
        dy = np.zeros((h, w), dtype=np.float32)
        amp = 1.0
        scale = 16 if draft else 8
        # Two independent z-channels so dx and dy don't move in lockstep.
        z_x = np.asarray([t * 0.05], dtype=np.float64)
        z_y = np.asarray([t * 0.05 + 17.3], dtype=np.float64)
        for _ in range(octaves):
            nh = max(2, h // scale)
            nw = max(2, w // scale)
            freq = 4.0 / max(1, scale // 4)
            xi = np.linspace(0.0, freq, nw, dtype=np.float64)
            yi = np.linspace(0.0, freq, nh, dtype=np.float64)
            nx = np.asarray(noise3array(xi, yi, z_x), dtype=np.float32)[0]
            ny = np.asarray(noise3array(xi, yi, z_y), dtype=np.float32)[0]
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
        dx, dy = self._make_flow_field(h, w, float(self._t),
                                        max(2, self.octaves), draft)
        for arr in (dx, dy):
            m = float(np.abs(arr).max()) + 1e-6
            arr /= m
        # Amplitude pulses on top of the flowing field — gives a subtle
        # "tide" feel instead of a constant-strength push.
        pulse = 1.0 + 0.3 * np.sin(self._t * 0.11)
        disp = intensity * 60.0 * pulse
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


class SelfDisplaceEffect(_Warpable):
    """Past frame's RGB channels used as XY displacement vectors, breathing."""
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

        # Depth and amplitude breathe with time so the displacement keeps
        # changing even on a long static SUSTAIN segment.
        breath = 0.7 + 0.3 * np.sin(self._t * 0.09)
        cross = 0.7 + 0.3 * np.cos(self._t * 0.05)
        dyn_depth = max(1, min(self.history_len,
                               self.depth + int(np.sin(self._t * 0.04) * 1.5)))

        d1 = get_hist(dyn_depth).astype(np.float32)
        d2 = get_hist(min(dyn_depth * 2, n - 1)).astype(np.float32)
        dx = ((d1[:, :, 0] - 128.0) / 128.0) * intensity * 55.0 * breath
        dy = ((d1[:, :, 1] - 128.0) / 128.0) * intensity * 55.0 * cross
        dx += ((d2[:, :, 0] - 128.0) / 128.0) * intensity * 25.0 * cross
        dy += ((d2[:, :, 2] - 128.0) / 128.0) * intensity * 25.0 * breath
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
