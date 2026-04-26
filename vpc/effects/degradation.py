"""CRT / VHS / JPEG / dither degradation effects."""
from __future__ import annotations

import random
import cv2
import numpy as np
from opensimplex import noise2

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


class ScanLinesEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        result = frame.astype(np.float32)
        intensity = self.scaled_intensity(seg)
        n = max(2, int(8 - intensity * 6))
        darkness = 0.3 + intensity * 0.5
        result[::n] = result[::n] * (1.0 - darkness)
        return _ensure_uint8(result)


class BitcrushEffect(BaseEffect):
    trigger_types = list(SegmentType)

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        bits = max(1, int(7 - intensity * 5))
        shift = 8 - bits
        return ((frame >> shift) << shift).astype(np.uint8)


class JPEGCrushEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        quality = max(1, int(40 - intensity * 38))
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        result = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
        return _ensure_uint8(result)


class FisheyeEffect(BaseEffect):
    trigger_types = [SegmentType.BUILD, SegmentType.SUSTAIN]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        strength = intensity * 0.8
        K = np.array([[w, 0, w / 2.0],
                      [0, w, h / 2.0],
                      [0, 0, 1.0]], dtype=np.float64)
        D = np.array([[strength], [strength * 0.3], [0.0], [0.0]], dtype=np.float64)
        try:
            map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                K, D, np.eye(3), K, (w, h), cv2.CV_32FC1)
            result = cv2.remap(frame, map1, map2, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)
        except cv2.error:
            cx, cy = w / 2.0, h / 2.0
            xs = np.linspace(-1, 1, w)
            ys = np.linspace(-1, 1, h)
            xg, yg = np.meshgrid(xs, ys)
            r2 = xg ** 2 + yg ** 2
            factor = 1.0 + strength * r2
            map_x = ((xg * factor + 1) / 2 * w).astype(np.float32)
            map_y = ((yg * factor + 1) / 2 * h).astype(np.float32)
            result = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)
        return _ensure_uint8(result)


class VHSTrackingEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        n_strips = max(1, int(intensity * 8))
        noise_amp = int(intensity * 20)
        strip_h = h // max(1, n_strips)
        for i in range(n_strips):
            y = random.randint(0, max(0, h - strip_h))
            shift = int(noise2(float(i) * 0.5, intensity * 50.0) * noise_amp)
            result[y:y + strip_h] = np.roll(result[y:y + strip_h], shift, axis=1)
            noise_val = np.clip(
                np.array([noise2(float(x) * 0.1, float(y) * 0.1) * noise_amp
                          for x in range(w)]),
                -30, 30).astype(np.int16)
            for row in range(y, min(y + strip_h, h)):
                result[row] = np.clip(
                    result[row].astype(np.int16) + noise_val.reshape(-1, 1), 0, 255
                ).astype(np.uint8)
        return result


class InterlaceEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.prev_frame = None

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        if self.prev_frame is not None and self.prev_frame.shape == frame.shape:
            result[1::2] = self.prev_frame[1::2]
        self.prev_frame = frame.copy()
        return result


class BadSignalEffect(BaseEffect):
    trigger_types = [SegmentType.DROP, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        n_bars = int(intensity * 5)
        for _ in range(n_bars):
            x = random.randint(0, w - 1)
            bw = random.randint(1, 4)
            val = random.randint(0, 255)
            result[:, x:min(x + bw, w)] = val
        n_shift = int(intensity * h * 0.1)
        for _ in range(n_shift):
            row = random.randint(0, h - 1)
            shift = random.randint(-20, 20)
            result[row] = np.roll(result[row], shift, axis=0)
        return result


class DitheringEffect(BaseEffect):
    trigger_types = [SegmentType.SILENCE, SegmentType.SUSTAIN]

    BAYER_4X4 = np.array([
        [0, 8, 2, 10],
        [12, 4, 14, 6],
        [3, 11, 1, 9],
        [15, 7, 13, 5]
    ], dtype=np.float32) / 16.0

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        levels = max(2, int(16 - intensity * 12))
        h, w = frame.shape[:2]
        tile_r = (h + 3) // 4
        tile_c = (w + 3) // 4
        bayer = np.tile(self.BAYER_4X4, (tile_r, tile_c))[:h, :w]
        bayer3 = np.stack([bayer] * 3, axis=-1)
        normalized = frame.astype(np.float32) / 255.0
        step = 1.0 / levels
        dithered = normalized + (bayer3 - 0.5) * step
        quantized = np.floor(dithered * levels) / levels
        return _ensure_uint8(quantized * 255.0)


class ZoomGlitchEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        zoom = 1.0 + intensity * 0.4
        cw, ch = int(w / zoom), int(h / zoom)
        x1 = (w - cw) // 2
        y1 = (h - ch) // 2
        cropped = frame[y1:y1 + ch, x1:x1 + cw]
        result = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_NEAREST)
        return _ensure_uint8(result)
