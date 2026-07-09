"""Базовые классы и общие утилиты для всех эффектов."""
from __future__ import annotations

from abc import ABC, abstractmethod
import random
from typing import List

import cv2
import numpy as np

from vpc.analyzer import Segment, SegmentType


class BaseEffect(ABC):
    """Общий контракт для всех эффектов.

    Наследник ОБЯЗАН реализовать `_apply(frame, seg, draft)`. Дефолтный
    `apply()` прогоняет цепочку условий (enabled -> trigger_types -> бросок
    chance) и вызывает `_apply` только если все пройдены. Эффекты с состоянием
    (OpticalFlow, TrueDatamosh, DerivWarp, SelfDisplace) переопределяют
    `apply()`, чтобы обновлять свою историю даже в кадрах, где сам эффект не
    сработал.
    """
    trigger_types: List[SegmentType] = list(SegmentType)

    # ── настройки аудио-реактивности (опциональные, выставляются build_chain) ──
    # audio_drive: какая по-кадровая полоса подменяет seg.intensity в
    #   scaled_intensity - 'segment' (дефолт, старое поведение), 'auto'
    #   (самая громкая из bass/mid/high, чтобы трек никогда не был "мёртвым"),
    #   либо имя конкретной полосы.
    # beat_gate: гейтит apply() по битам/онсетам внутри сегмента -
    #   'off' (дефолт), 'beat' или 'onset'.
    # react: флаг для эффектов с собственной проводкой к seg.live.
    audio_drive: str = 'segment'
    beat_gate: str = 'off'
    react: bool = False

    # Абсолютное время текущего кадра (сек) на таймлайне рендера. Движок
    # проставляет его в _apply_chain перед вызовом эффекта. Нужно эффектам,
    # завязанным на время (субтитры); остальные его просто игнорируют.
    frame_time: float = 0.0

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        self.enabled = enabled
        self.chance = chance
        self.intensity_min = intensity_min
        self.intensity_max = intensity_max
        self.frame_time = 0.0

    def _beat_pass(self, seg: Segment) -> bool:
        """True, если по-кадровый beat gate пропускает этот кадр.

        Отсутствие seg.live (нет аудио / превью без звука) всегда пропускает -
        включённый эффект не должен молча глушиться на пути без аудио.
        """
        live = getattr(seg, 'live', None)
        if live is None:
            return True
        if self.beat_gate == 'beat':
            return bool(getattr(live, 'beat', True))
        if self.beat_gate == 'onset':
            return float(getattr(live, 'onset', 1.0)) > 0.45
        return True

    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        if not self.enabled:
            return frame
        if seg.type not in self.trigger_types:
            return frame
        if random.random() > self.chance:
            return frame
        if self.beat_gate != 'off' and not self._beat_pass(seg):
            return frame
        try:
            out = self._apply(frame, seg, draft)
        except (MemoryError, ValueError, cv2.error) as e:  # type: ignore[name-defined]
            # Не роняем весь рендер-пайплайн из-за разового сбоя эффекта -
            # чаще всего это OOM numpy/cv2 при тяжёлых комбо always-on,
            # либо remap с выходом за диапазон на вырожденном входе.
            self._fail_count = getattr(self, '_fail_count', 0) + 1
            if self._fail_count <= 3 or self._fail_count % 250 == 0:
                print(f'[FX-FAIL] {type(self).__name__}: {e!r} '
                      f'(suppressed={self._fail_count})')
            if self._fail_count > 50:
                self.enabled = False
                print(f'[FX-FAIL] {type(self).__name__} disabled '
                      f'after {self._fail_count} failures.')
            return frame
        # Подстраховка: NaN/Inf во float-промежутках могут уронить cv2.remap
        # позже, если значение молча просочится в следующий эффект.
        if out is None:
            return frame
        if out.dtype != np.uint8:
            out = _ensure_uint8(out)
        return out

    def _driven_value(self, seg: Segment) -> float:
        """Значение 0..1, задающее интенсивность - по умолчанию громкость
        сегмента, либо по-кадровая аудио-полоса, если задан audio_drive.
        Откатывается на seg.intensity, если live-сэмпла нет, чтобы эффект
        на полосе не "умирал" без аудио."""
        drive = self.audio_drive
        if drive == 'segment':
            return seg.intensity
        live = getattr(seg, 'live', None)
        if live is None:
            return seg.intensity
        if drive == 'auto':
            return max(live.bass, live.mid, live.high)
        return float(getattr(live, drive, seg.intensity))

    def scaled_intensity(self, seg: Segment) -> float:
        v = self.intensity_min + self._driven_value(seg) * (self.intensity_max - self.intensity_min)
        # Жёсткий потолок. Always-on с intensity=1.0 загоняет некоторые warp'ы
        # (угол Vortex 5 rad, Sobel breath x1.25) в граничные значения
        # параметров, из-за которых cv2.remap иногда падает на Windows.
        return max(0.0, min(0.95, v))

    def _blend_by_intensity(self, seg: Segment, result: np.ndarray,
                             frame: np.ndarray) -> np.ndarray:
        """Кросс-фейд `result` к нетронутому `frame` по `scaled_intensity(seg)`.
        Даёт эффектам без собственной непрерывной ручки "amount" рабочий
        контроль `always`/`always-on intensity` бесплатно - и на
        аудио-driven пути (обычный режим), и на пути с фиксированным
        значением (always-on режим), поскольку оба уже проходят через
        `intensity_min`/`intensity_max`."""
        strength = self.scaled_intensity(seg)
        return cv2.addWeighted(result, strength, frame, 1.0 - strength, 0.0)

    @abstractmethod
    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray: ...


def _ensure_uint8(frame: np.ndarray) -> np.ndarray:
    return np.clip(frame, 0, 255).astype(np.uint8)


def _reseg(seg: Segment, intensity: float) -> Segment:
    """Копия seg с переопределённой intensity."""
    return Segment(seg.t_start, seg.t_end, seg.duration, seg.type, intensity,
                   seg.rms, seg.flatness, seg.rms_change, seg.live)


# Наличие scipy определяется один раз и переиспользуется в модулях signal-domain.
try:
    from scipy.signal import butter, sosfilt, fftconvolve  # noqa: F401
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False
