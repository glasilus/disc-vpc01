"""Субтитры: тайм-окошные позиционированные реплики, отрисованные в маску и
прогнанные через те же режимы, что и Paint (overlay/lag/warp_video/lag_warp).

Каждая реплика - словарь ``{text, x, y, font, size, color, mode, t_start,
t_end, align}``. На кадре ``t`` собираются все активные реплики (``t_start <= t
< t_end``), их буквы рисуются в бинарную маску, и режим применяется через
общий ``mask_modes.apply_mask_mode``. Реплики могут перекрываться по времени и
дублироваться - несколько активны одновременно в разных местах.

Размер шрифта хранится в пикселях при эталонной высоте ``REF_H``; на рендере он
масштабируется под фактическую высоту кадра, поэтому текст выглядит одинаково в
превью 480p и в экспорте 1080p (WYSIWYG).
"""
from __future__ import annotations

import collections
import json

import numpy as np
from PIL import Image, ImageDraw

from vpc.analyzer import Segment, SegmentType
from vpc.fonts import get_pil_font
from .base import BaseEffect
from .mask_modes import apply_mask_mode

# Эталонная высота, к которой привязан размер шрифта реплики.
REF_H = 720

_MODES = ('overlay', 'lag', 'warp_video', 'lag_warp')


def _clampf(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return max(lo, min(hi, f))


def decode_subtitles(data: str) -> list[dict]:
    """Разбирает JSON-строку списка реплик в нормализованный список словарей.

    Битый/пустой ввод -> []. Каждая реплика приводится к безопасным типам;
    реплики без текста или с ``t_start >= t_end`` отбрасываются. Поля mode/color
    могут отсутствовать (тогда на рендере подставляются глобальные дефолты
    эффекта) - здесь они сохраняются как есть (None), если их нет.
    """
    if not data or not isinstance(data, str):
        return []
    try:
        raw = json.loads(data)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    cues: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get('text', '')
        if not isinstance(text, str) or not text.strip():
            continue
        t0 = _clampf(item.get('t_start', 0.0), 0.0, 1e9, 0.0)
        t1 = _clampf(item.get('t_end', 0.0), 0.0, 1e9, 0.0)
        if t1 <= t0:
            continue
        cue = {
            'text': text,
            'x': _clampf(item.get('x', 0.5), 0.0, 1.0, 0.5),
            'y': _clampf(item.get('y', 0.85), 0.0, 1.0, 0.85),
            'size': int(_clampf(item.get('size', 48), 1, 2000, 48)),
            't_start': t0,
            't_end': t1,
            'align': item.get('align', 'center')
                     if item.get('align') in ('left', 'center', 'right') else 'center',
        }
        font = item.get('font')
        cue['font'] = font if isinstance(font, str) and font else None
        mode = item.get('mode')
        cue['mode'] = mode if mode in _MODES else None
        color = item.get('color')
        if (isinstance(color, (list, tuple)) and len(color) == 3):
            cue['color'] = tuple(int(_clampf(c, 0, 255, 0)) for c in color)
        else:
            cue['color'] = None
        cues.append(cue)
    return cues


class SubtitleEffect(BaseEffect):
    """Отрисовывает активные по времени реплики через режимы маски.

    В отличие от аудиореактивных эффектов, субтитры НЕ гейтятся типом сегмента,
    chance и громкостью - они появляются строго по своим таймкодам на полной
    непрозрачности. История кадров толкается каждый кадр (для режима lag).
    """
    trigger_types = list(SegmentType)

    def __init__(self, cues: list[dict] | None = None, mode: str = 'overlay',
                 color_r: int = 255, color_g: int = 255, color_b: int = 255,
                 delay_frames: int = 10, warp_intensity: float = 0.3,
                 font: str = 'Arial', size: int = 48, **kw):
        super().__init__(**kw)
        self.cues = cues or []
        self.mode = mode if mode in _MODES else 'overlay'
        self.color = (int(color_r), int(color_g), int(color_b))
        self.delay_frames = max(1, int(delay_frames))
        self.warp_intensity = float(warp_intensity)
        self.font = font or 'Arial'
        self.size = int(size)
        self.history: collections.deque[np.ndarray] | None = None
        self._t = 0

    # История кадров нужна для lag - обновляется на КАЖДОМ кадре, иначе
    # задержка «дёргается». Гейт apply() у субтитров отсутствует.
    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        if (self.history is None or not self.history
                or self.history[0].shape != frame.shape):
            self.history = collections.deque(maxlen=self.delay_frames)
            for _ in range(self.delay_frames):
                self.history.append(frame.copy())
        else:
            if self.history.maxlen != self.delay_frames:
                new_hist = collections.deque(maxlen=self.delay_frames)
                new_hist.extend(self.history)
                self.history = new_hist
            self.history.append(frame.copy())

        self._t += 1
        if not self.enabled or not self.cues:
            return frame
        try:
            return self._apply(frame, seg, draft)
        except Exception as e:  # стабильность важнее любого сбоя отрисовки
            self._fail_count = getattr(self, '_fail_count', 0) + 1
            if self._fail_count <= 3:
                print(f'[FX-FAIL] SubtitleEffect: {e!r}')
            return frame

    def _active_cues(self, t: float) -> list[dict]:
        return [c for c in self.cues if c['t_start'] <= t < c['t_end']]

    def _build_mask(self, cues: list[dict], h: int, w: int) -> np.ndarray:
        """Рисует буквы реплик чёрным (0) на белом (255) - соглашение Paint."""
        img = Image.new('L', (w, h), 255)
        draw = ImageDraw.Draw(img)
        scale = h / REF_H
        for cue in cues:
            text = cue['text']
            px = max(1, int(round(cue['size'] * scale)))
            font_name = cue['font'] or self.font
            pil_font = get_pil_font(font_name, px)
            cx = int(round(cue['x'] * w))
            cy = int(round(cue['y'] * h))
            try:
                draw.multiline_text((cx, cy), text, fill=0, font=pil_font,
                                    anchor='mm', align=cue['align'])
            except (ValueError, OSError):
                # anchor 'mm' не поддерживается древними bitmap-шрифтами -
                # откатываемся на отрисовку без якоря.
                draw.multiline_text((cx, cy), text, fill=0, font=pil_font,
                                    align=cue['align'])
        return np.asarray(img)

    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        t = float(self.frame_time)
        active = self._active_cues(t)
        if not active:
            return frame

        h, w = frame.shape[:2]
        delayed = self.history[0] if self.history else frame
        if delayed.shape != frame.shape:
            delayed = frame
        amp = self.warp_intensity * 25.0

        # Группируем по (mode, color): реплики с одинаковым режимом и цветом
        # ложатся одной маской за один проход; разные группы стекаются поверх.
        groups: dict[tuple, list[dict]] = collections.OrderedDict()
        for cue in active:
            key = (cue['mode'] or self.mode, cue['color'] or self.color)
            groups.setdefault(key, []).append(cue)

        result = frame
        for (mode, color), grp in groups.items():
            mask = self._build_mask(grp, h, w)
            result = apply_mask_mode(
                result, mask, mode,
                delayed_frame=delayed, color=color, amp=amp, t=self._t)
        return result
