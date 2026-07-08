"""Эффекты в эстетике Win95-вируса: похоже на заражённую малварью машину
90-х, а не на видео-глитч.

  - CursorStormEffect: рой фейковых курсоров Win95, ползающих по кадру
    с короткими хвостами.
  - BSODShredEffect: случайные горизонтальные полосы кадра заменяются
    обрывками текста синего экрана.

Оба используют общие хелперы `_make_cursor_sprite()` и `_BSOD_LINES`,
чтобы для новых virus-эффектов не пришлось изобретать всё заново.
"""
from __future__ import annotations

import random
from collections import deque
from typing import Deque, List, Tuple

import cv2
import numpy as np

from .base import BaseEffect, _ensure_uint8


# ──────────────────────────────────────────────────────────────────────────
#   Спрайт курсора Win95 - строится один раз, кешируется на уровне модуля.
# ──────────────────────────────────────────────────────────────────────────
#
# Битмап 16x22, вручную нарисованный по образу стандартной стрелки Win95.
# Глифы:
#     '#' = чёрная обводка
#     '.' = белая заливка
#     ' ' = прозрачно
# Форма повторяет стандартный курсор из NT4/Win95: острая стрелка с
# коротким хвостом и однопиксельной чёрной обводкой.
_CURSOR_BITMAP = [
    '#               ',
    '##              ',
    '#.#             ',
    '#..#            ',
    '#...#           ',
    '#....#          ',
    '#.....#         ',
    '#......#        ',
    '#.......#       ',
    '#........#      ',
    '#.........#     ',
    '#..........#    ',
    '#......#####    ',
    '#...#..#        ',
    '#..##..#        ',
    '#.#  #..#       ',
    '##   #..#       ',
    '#     #..#      ',
    '      #..#      ',
    '       #.#      ',
    '       ##       ',
    '       #        ',
]


def _make_cursor_sprite() -> Tuple[np.ndarray, np.ndarray]:
    """Возвращает (rgb, alpha) массивы для стрелки-курсора Win95.

    rgb - uint8 HxWx3, alpha - uint8 HxW, где 0 = прозрачно, 255 = непрозрачно.
    Считается один раз при импорте, дальше просто переиспользуется.
    """
    h = len(_CURSOR_BITMAP)
    w = max(len(r) for r in _CURSOR_BITMAP)
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    alpha = np.zeros((h, w), dtype=np.uint8)
    for y, row in enumerate(_CURSOR_BITMAP):
        for x, ch in enumerate(row):
            if ch == '#':
                rgb[y, x] = (0, 0, 0)
                alpha[y, x] = 255
            elif ch == '.':
                rgb[y, x] = (255, 255, 255)
                alpha[y, x] = 255
    return rgb, alpha


_CURSOR_RGB, _CURSOR_ALPHA = _make_cursor_sprite()


def _blit_sprite(frame: np.ndarray, rgb: np.ndarray, alpha: np.ndarray,
                 x: int, y: int, opacity: float = 1.0) -> None:
    """Альфа-блит `rgb` на `frame`, левый верхний угол в (x, y), на месте.

    Выход за границы кадра просто обрезается без ошибок - курсоры,
    уползающие за край, это часть задуманного вида.
    """
    h_f, w_f = frame.shape[:2]
    h_s, w_s = rgb.shape[:2]
    x0 = max(0, x); y0 = max(0, y)
    x1 = min(w_f, x + w_s); y1 = min(h_f, y + h_s)
    if x1 <= x0 or y1 <= y0:
        return
    sx0 = x0 - x; sy0 = y0 - y
    sx1 = sx0 + (x1 - x0); sy1 = sy0 + (y1 - y0)
    a = alpha[sy0:sy1, sx0:sx1].astype(np.float32) * (opacity / 255.0)
    a = a[..., None]
    src = rgb[sy0:sy1, sx0:sx1].astype(np.float32)
    dst = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = np.clip(dst * (1.0 - a) + src * a, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────────────────────────────────
#   CursorStorm
# ──────────────────────────────────────────────────────────────────────────


class _Pointer:
    """Один курсор с броуновским блужданием и собственным коротким хвостом."""

    __slots__ = ('x', 'y', 'vx', 'vy', 'trail')

    def __init__(self, w: int, h: int):
        self.x = float(random.randint(0, max(0, w - 1)))
        self.y = float(random.randint(0, max(0, h - 1)))
        self.vx = (random.random() - 0.5) * 6.0
        self.vy = (random.random() - 0.5) * 6.0
        self.trail: Deque[Tuple[int, int]] = deque(maxlen=6)

    def step(self, w: int, h: int, jitter: float) -> None:
        # Случайное возмущение скорости на каждом кадре; ограничиваем
        # скорость, чтобы курсор не улетал моментально. Отскок от краёв.
        self.vx += (random.random() - 0.5) * jitter
        self.vy += (random.random() - 0.5) * jitter
        speed_sq = self.vx * self.vx + self.vy * self.vy
        if speed_sq > 36.0:
            scale = 6.0 / (speed_sq ** 0.5)
            self.vx *= scale; self.vy *= scale
        self.x += self.vx
        self.y += self.vy
        if self.x < 0 or self.x > w - 1:
            self.vx = -self.vx
            self.x = max(0.0, min(float(w - 1), self.x))
        if self.y < 0 or self.y > h - 1:
            self.vy = -self.vy
            self.y = max(0.0, min(float(h - 1), self.y))
        self.trail.append((int(self.x), int(self.y)))


class CursorStormEffect(BaseEffect):
    """Рой курсоров Win95, ползающих по картинке с короткими хвостами.
    Количество курсоров и амплитуда дрожания растут с интенсивностью.

    Хранит состояние: позиции курсоров сохраняются между кадрами, так
    что движение непрерывное. Количество курсоров пересчитывается на
    каждом сегменте, поэтому смена интенсивности между аудио-сегментами
    плавно меняет размер роя без жёсткого сброса.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 max_pointers: int = 12):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self.max_pointers = max(1, min(32, int(max_pointers)))
        self._pointers: List[_Pointer] = []

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]

        # Число курсоров растёт с интенсивностью; при сработавшем эффекте
        # всегда минимум 1, чтобы что-то было видно.
        target_n = max(1, int(round(intensity * self.max_pointers)))
        # Добавляем/убираем курсоры постепенно, а не жёстким сбросом -
        # так движение остаётся плавным при изменении слайдера.
        while len(self._pointers) < target_n:
            self._pointers.append(_Pointer(w, h))
        while len(self._pointers) > target_n:
            self._pointers.pop()

        out = frame.copy()
        # Чем выше интенсивность, тем безумнее движение.
        jitter = 0.6 + intensity * 1.6
        for p in self._pointers:
            p.step(w, h, jitter)
            # Сначала хвост (старые точки прозрачнее), курсор поверх всего.
            for i, (tx, ty) in enumerate(p.trail):
                fade = (i + 1) / (len(p.trail) + 1)
                _blit_sprite(out, _CURSOR_RGB, _CURSOR_ALPHA,
                             tx, ty, opacity=0.25 + fade * 0.45)
            _blit_sprite(out, _CURSOR_RGB, _CURSOR_ALPHA,
                         int(p.x), int(p.y), opacity=1.0)
        return _ensure_uint8(out)


# ──────────────────────────────────────────────────────────────────────────
#   BSODShred
# ──────────────────────────────────────────────────────────────────────────


# Правдоподобные обрывки синего экрана. Выбираются случайно на каждую полосу;
# смесь заголовков, hex-адресов и текста дампа продаёт эффект.
_BSOD_LINES: List[str] = [
    '*** STOP: 0x0000007E (0xC0000005, 0x804E12C8, 0xF7AB68C4)',
    'A problem has been detected and Windows has been shut down',
    'KMODE_EXCEPTION_NOT_HANDLED',
    'PAGE_FAULT_IN_NONPAGED_AREA',
    'IRQL_NOT_LESS_OR_EQUAL',
    'Beginning dump of physical memory',
    'Physical memory dump complete.',
    'DRIVER_IRQL_NOT_LESS_OR_EQUAL  vmm.sys',
    '0x804E12C8  0xC0000005  0xF7AB68C4  0x00000000',
    'If this is the first time you have seen this stop error screen,',
    'restart your computer. If this screen appears again, follow these',
    'steps: Check to make sure any new hardware or software is properly',
    'NTOSKRNL.EXE - Address F7AB68C4 base at F7A8F000',
    'INACCESSIBLE_BOOT_DEVICE',
    'UNEXPECTED_KERNEL_MODE_TRAP',
]

# Классический фон NT-синего экрана, близко к каноничному RGB(0, 0, 168).
_BSOD_BG = (0, 0, 168)
_BSOD_FG = (255, 255, 255)


class BSODShredEffect(BaseEffect):
    """Вырезает случайные горизонтальные полосы кадра и заменяет их
    текстом в стиле синего экрана. Число полос и их высота растут с
    интенсивностью. Без состояния между кадрами: каждый кадр берёт
    новые полосы, отсюда эффект мерцания/рваности.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 max_bands: int = 5):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self.max_bands = max(1, min(12, int(max_bands)))

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]
        n_bands = max(1, int(round(intensity * self.max_bands)))
        out = frame.copy()
        # Масштаб шрифта и межстрочный интервал подобраны так, чтобы
        # строка помещалась даже в самую тонкую полосу без наложения.
        font = cv2.FONT_HERSHEY_PLAIN
        font_scale = 1.0
        line_h = 14
        for _ in range(n_bands):
            band_h = random.randint(int(line_h * 1.5),
                                    int(line_h * (2 + intensity * 4)))
            band_h = min(band_h, max(line_h * 2, h // 4))
            y0 = random.randint(0, max(0, h - band_h))
            out[y0:y0 + band_h] = _BSOD_BG
            # Сколько строк текста влезает в полосу с учётом отступа 4px сверху.
            n_lines = max(1, (band_h - 4) // line_h)
            for li in range(n_lines):
                line = random.choice(_BSOD_LINES)
                # Обрезаем, чтобы не вылезало за правый край -
                # cv2.putText сам по себе просто рисует за пределами кадра.
                while line and self._text_width(line, font, font_scale) > w - 8:
                    line = line[:-2]
                if not line:
                    continue
                ty = y0 + 4 + (li + 1) * line_h - 3
                if ty >= h:
                    break
                cv2.putText(out, line, (4, ty), font, font_scale,
                            _BSOD_FG, 1, cv2.LINE_AA)
        return _ensure_uint8(out)

    @staticmethod
    def _text_width(text: str, font: int, scale: float) -> int:
        (tw, _), _ = cv2.getTextSize(text, font, scale, 1)
        return tw
