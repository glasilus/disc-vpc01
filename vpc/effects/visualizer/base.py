"""VisualizerEffect: общий для всех WMP-визуализаторов цикл render→composite."""
from __future__ import annotations

from abc import abstractmethod

import cv2
import numpy as np

from vpc.analyzer import Segment, SegmentType
from vpc.effects.base import BaseEffect
from .reactive import read_sample
from .compose import composite


class VisualizerEffect(BaseEffect):
    """Базовый класс аудиореактивных визуализаторов.

    Наследники реализуют ``_render(h, w, sample)``, возвращающий ``(visual_rgb,
    field_gray)``. Базовый класс сам читает аудиосэмпл кадра с сегмента и
    компонует результат с исходным кадром через общие режимы.
    """
    trigger_types = list(SegmentType)   # реагирует на любой тип сегмента

    def __init__(self, mode: str = 'replace', opacity: float = 0.85,
                 blend: str = 'alpha', **kw):
        super().__init__(**kw)
        self.mode = mode
        self.opacity = float(opacity)
        self.blend = blend

    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        h, w = frame.shape[:2]
        sample = read_sample(seg)
        visual, field = self._render(h, w, sample)
        composed = composite(frame, visual, field, self.mode, self.opacity, self.blend)
        # Единый кроссфейд к исходнику. Плотность визуала уже задаёт composite
        # (opacity/mask), поэтому наследуемый _blend_by_intensity здесь не нужен -
        # его повторное смешивание оставляло минимум 5% оригинала поверх
        # результата (потолок 0.95) и подмешивало видео сквозь чёрное в mask.
        # intensity выступает мастер-непрозрачностью и доходит до честных 100%.
        amount = self.intensity_min + self._driven_value(seg) * (self.intensity_max - self.intensity_min)
        amount = max(0.0, min(1.0, amount))
        if amount >= 0.999:
            return composed
        return cv2.addWeighted(composed, amount, frame, 1.0 - amount, 0.0)

    @abstractmethod
    def _render(self, h: int, w: int, sample) -> tuple[np.ndarray, np.ndarray]:
        """Возвращает (visual_rgb HxWx3 uint8, field_gray HxW uint8)."""
        ...
