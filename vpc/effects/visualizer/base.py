"""VisualizerEffect: render→composite flow shared by all WMP visualizers."""
from __future__ import annotations

from abc import abstractmethod

import numpy as np

from vpc.analyzer import Segment, SegmentType
from vpc.effects.base import BaseEffect
from .reactive import read_sample
from .compose import composite


class VisualizerEffect(BaseEffect):
    """Base for audio-reactive visualizers.

    Subclasses implement ``_render(h, w, sample)`` returning ``(visual_rgb,
    field_gray)``. The base class handles reading the per-frame audio sample
    off the segment and compositing onto the source via the shared modes.
    """
    trigger_types = list(SegmentType)   # reactive on every segment type

    def __init__(self, mode: str = 'replace', opacity: float = 0.85,
                 blend: str = 'screen', **kw):
        super().__init__(**kw)
        self.mode = mode
        self.opacity = float(opacity)
        self.blend = blend

    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        h, w = frame.shape[:2]
        sample = read_sample(seg)
        visual, field = self._render(h, w, sample)
        composed = composite(frame, visual, field, self.mode, self.opacity, self.blend)
        return self._blend_by_intensity(seg, composed, frame)

    @abstractmethod
    def _render(self, h: int, w: int, sample) -> tuple[np.ndarray, np.ndarray]:
        """Return (visual_rgb HxWx3 uint8, field_gray HxW uint8)."""
        ...
