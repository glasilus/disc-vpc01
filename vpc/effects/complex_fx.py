"""Complex compound effects: Feedback, PhaseShift, Mosaic, Echo, Kali, Cascade."""
from __future__ import annotations

import random
import cv2
import numpy as np

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8
from .glitch import RGBShiftEffect, BlockGlitchEffect, PixelDriftEffect
from .degradation import BitcrushEffect


class FeedbackLoopEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.accumulated = None

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        weight = intensity * 0.7
        if seg.type == SegmentType.IMPACT:
            self.accumulated = None
        if self.accumulated is None or self.accumulated.shape != frame.shape:
            self.accumulated = frame.astype(np.float32)
            return frame.copy()
        self.accumulated = frame.astype(np.float32) * (1 - weight) + self.accumulated * weight
        return _ensure_uint8(self.accumulated)


class PhaseShiftEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        band_h = max(4, int(h * 0.05))
        shift = int(intensity * w * 0.2)
        for y in range(0, h, band_h):
            band_idx = y // band_h
            s = shift if band_idx % 2 == 0 else -shift
            end = min(y + band_h, h)
            result[y:end] = np.roll(result[y:end], s, axis=1)
        return result


class MosaicPulseEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.BUILD]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        block = max(2, int(4 + intensity * 40))
        small = cv2.resize(frame, (max(1, w // block), max(1, h // block)),
                           interpolation=cv2.INTER_NEAREST)
        result = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        return _ensure_uint8(result)


class EchoCompoundEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD]

    def __init__(self, echo_n=8, **kw):
        super().__init__(**kw)
        self.echo_n = echo_n
        self.history = []

    def _apply(self, frame, seg, draft):
        self.history.append(frame.copy())
        max_len = self.echo_n * 2 + 1
        if len(self.history) > max_len:
            self.history = self.history[-max_len:]

        result = frame.astype(np.float32) * 0.5
        n = self.echo_n
        if len(self.history) > n:
            past1 = self.history[-(n + 1)]
            result += past1.astype(np.float32) * 0.3
        else:
            result += frame.astype(np.float32) * 0.3
        if len(self.history) > 2 * n:
            past2 = self.history[-(2 * n + 1)]
            hsv = cv2.cvtColor(past2, cv2.COLOR_RGB2HSV)
            hsv[:, :, 0] = (hsv[:, :, 0].astype(np.int16) + 30) % 180
            past2_shifted = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
            result += past2_shifted.astype(np.float32) * 0.2
        else:
            result += frame.astype(np.float32) * 0.2
        return _ensure_uint8(result)


class KaliMirrorEffect(BaseEffect):
    trigger_types = [SegmentType.BUILD, SegmentType.SUSTAIN]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        mirrored = np.hstack([frame, frame[:, ::-1]])
        full = np.vstack([mirrored, 255 - mirrored])
        angle = intensity * 180.0
        fh, fw = full.shape[:2]
        M = cv2.getRotationMatrix2D((fw / 2, fh / 2), angle, 1.0)
        rotated = cv2.warpAffine(full, M, (fw, fh), borderMode=cv2.BORDER_REFLECT)
        cy, cx = fh // 2, fw // 2
        result = rotated[cy - h // 2:cy - h // 2 + h, cx - w // 2:cx - w // 2 + w]
        return _ensure_uint8(result)


class GlitchCascadeEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.pool = [
            RGBShiftEffect(enabled=True, chance=1.0),
            BlockGlitchEffect(enabled=True, chance=1.0),
            PixelDriftEffect(enabled=True, chance=1.0),
            BitcrushEffect(enabled=True, chance=1.0),
        ]

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        n = max(1, int(intensity * len(self.pool)))
        chosen = random.sample(self.pool, min(n, len(self.pool)))
        result = frame.copy()
        for fx in chosen:
            fx.trigger_types = list(SegmentType)
            result = fx._apply(result, seg, draft)
        return _ensure_uint8(result)
