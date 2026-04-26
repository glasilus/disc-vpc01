"""Codec/Block-level glitch effects."""
from __future__ import annotations

import random
import cv2
import numpy as np
from opensimplex import noise2

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


class RGBShiftEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.BUILD,
                     SegmentType.NOISE, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        shift = int(self.scaled_intensity(seg) * 20)
        result = frame.copy()
        result[:, :, 0] = np.roll(frame[:, :, 0], shift, axis=1)
        result[:, :, 2] = np.roll(frame[:, :, 2], -shift, axis=1)
        return result


class BlockGlitchEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def __init__(self, block_size=16, **kw):
        super().__init__(**kw)
        self.block_size = block_size

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        n_blocks = int(self.scaled_intensity(seg) * 20)
        bs = self.block_size
        for _ in range(n_blocks):
            y = random.randint(0, max(0, h - bs))
            x = random.randint(0, max(0, w - bs))
            if random.random() < 0.5:
                sy = random.randint(0, max(0, h - bs))
                sx = random.randint(0, max(0, w - bs))
                result[y:y + bs, x:x + bs] = frame[sy:sy + bs, sx:sx + bs]
            else:
                result[y:y + bs, x:x + bs] = random.randint(0, 255)
        return result


class PixelDriftEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.IMPACT]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        max_shift = int(self.scaled_intensity(seg) * 30)
        step = 4 if draft else 1
        for row in range(0, h, step):
            n = noise2(float(row) * 0.1, seg.intensity * 100.0)
            shift = int(n * max_shift)
            result[row] = np.roll(frame[row], shift, axis=0)
        return result


class ColorBleedEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.SUSTAIN]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        intensity = self.scaled_intensity(seg)
        channel = random.randint(0, 2)
        kernel_w = max(3, int(intensity * 40))
        if kernel_w % 2 == 0:
            kernel_w += 1
        result[:, :, channel] = cv2.blur(result[:, :, channel], (kernel_w, 1))
        return result


class FreezeCorruptEffect(BaseEffect):
    trigger_types = [SegmentType.DROP]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._held = None
        self._hold_count = 0
        self._glitch = BlockGlitchEffect(enabled=True, chance=1.0)

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        hold_frames = max(1, int(intensity * 6))
        if self._held is None or self._hold_count >= hold_frames:
            self._held = frame.copy()
            self._hold_count = 0
        self._hold_count += 1
        return self._glitch._apply(self._held, seg, draft)


class NegativeEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        return (255 - frame).astype(np.uint8)
