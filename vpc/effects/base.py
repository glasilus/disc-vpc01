"""Base classes and shared utilities for all effects."""
from __future__ import annotations

from abc import ABC, abstractmethod
import random
from typing import List

import numpy as np

from vpc.analyzer import Segment, SegmentType


class BaseEffect(ABC):
    """Common contract for every effect.

    Subclass MUST implement `_apply(frame, seg, draft)`. The default `apply()`
    runs the gating chain (enabled → trigger_types → chance roll) and dispatches
    to `_apply` only on success. Stateful effects (Datamosh, DerivWarp,
    SelfDisplace) override `apply()` to keep their history in sync regardless
    of whether the effect actually fires this frame.
    """
    trigger_types: List[SegmentType] = list(SegmentType)

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        self.enabled = enabled
        self.chance = chance
        self.intensity_min = intensity_min
        self.intensity_max = intensity_max

    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        if not self.enabled:
            return frame
        if seg.type not in self.trigger_types:
            return frame
        if random.random() > self.chance:
            return frame
        return self._apply(frame, seg, draft)

    def scaled_intensity(self, seg: Segment) -> float:
        return self.intensity_min + seg.intensity * (self.intensity_max - self.intensity_min)

    @abstractmethod
    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray: ...


def _ensure_uint8(frame: np.ndarray) -> np.ndarray:
    return np.clip(frame, 0, 255).astype(np.uint8)


def _reseg(seg: Segment, intensity: float) -> Segment:
    """Return a copy of seg with overridden intensity."""
    return Segment(seg.t_start, seg.t_end, seg.duration, seg.type, intensity,
                   seg.rms, seg.flatness, seg.rms_change)


# scipy availability is detected once and shared across signal-domain modules.
try:
    from scipy.signal import butter, sosfilt, fftconvolve  # noqa: F401
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False
