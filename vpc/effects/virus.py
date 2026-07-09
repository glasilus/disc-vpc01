"""Эффекты в эстетике Win95-вируса: похоже на заражённую малварью машину
90-х, а не на видео-глитч.

  - CursorStormEffect: рой фейковых курсоров Win95, ползающих по кадру
    с короткими хвостами.
  - BSODShredEffect: случайные горизонтальные полосы кадра заменяются
    обрывками текста синего экрана.
  - DVDBounceEffect: летающий DVD-логотип, отскакивающий от краёв со
    сменой цвета и вспышкой при попадании точно в угол.
  - WinPipesEffect: псевдо-3D трубопровод в духе скринсейвера «3D Pipes».

Общие хелперы `_make_cursor_sprite()`, `_blit_sprite()` и `_BSOD_LINES`
переиспользуются, чтобы для новых virus-эффектов не изобретать всё заново.
"""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Deque, List, Optional, Tuple

import cv2
import numpy as np

from vpc.analyzer import SegmentType
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


# ──────────────────────────────────────────────────────────────────────────
#   DVDBounce
# ──────────────────────────────────────────────────────────────────────────


def _make_dvd_sprite() -> Tuple[np.ndarray, np.ndarray]:
    """Возвращает (rgb, alpha) встроенного DVD-логотипа.

    Логотип вшит как PNG в base64 (модуль `_dvd_logo_data`) - белый силуэт
    на прозрачном фоне. rgb белый по всей фигуре, тонировка считается по
    яркости, форму задаёт альфа.
    """
    import base64
    from ._dvd_logo_data import DVD_LOGO_PNG_B64
    raw = np.frombuffer(base64.b64decode(DVD_LOGO_PNG_B64), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    rgb = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2RGB)
    alpha = img[:, :, 3].copy()
    return rgb, alpha


_DVD_RGB, _DVD_ALPHA = _make_dvd_sprite()

# Палитра циклической смены цвета - насыщенные тона в духе оригинала.
_DVD_CYCLE = [
    (255, 60, 60), (255, 160, 40), (255, 240, 60), (80, 230, 90),
    (60, 200, 255), (110, 110, 255), (230, 90, 230), (255, 255, 255),
]


def _load_logo(path: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Грузит пользовательский PNG-логотип в (rgb, alpha), сохраняя альфу.

    Читает байты через numpy (юникодные пути на Windows иначе не открываются)
    и декодирует с IMREAD_UNCHANGED, чтобы не потерять прозрачность. При любой
    ошибке возвращает (None, None) - вызывающий откатывается на встроенный глиф.
    """
    if not path:
        return None, None
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None, None
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    except (OSError, ValueError):
        return None, None
    if img is None:
        return None, None
    if img.ndim == 2:
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        alpha = np.full(img.shape[:2], 255, dtype=np.uint8)
    elif img.shape[2] == 4:
        rgb = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2RGB)
        alpha = img[:, :, 3].copy()
    else:
        rgb = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2RGB)
        alpha = np.full(img.shape[:2], 255, dtype=np.uint8)
    return rgb, alpha


class DVDBounceEffect(BaseEffect):
    """Летающий DVD-логотип, отскакивающий от краёв кадра.

    Хранит позицию и скорость между кадрами - движение непрерывное. Три
    режима цвета: `mono` (без тонировки), `cycle` (новый цвет на каждом
    ударе о стену) и `custom` (постоянный заданный цвет). При попадании
    точно в угол включается короткая вспышка-эйфория.
    """
    trigger_types = [SegmentType.SILENCE, SegmentType.SUSTAIN]

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 speed: float = 4.0, color_mode: str = 'cycle',
                 color_r: int = 0, color_g: int = 200, color_b: int = 255,
                 logo_rgb: Optional[np.ndarray] = None,
                 logo_alpha: Optional[np.ndarray] = None):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self.speed = max(0.5, float(speed))
        self.color_mode = color_mode if color_mode in ('mono', 'cycle', 'custom', 'lag') else 'cycle'
        self.custom_color = (int(color_r), int(color_g), int(color_b))
        if logo_rgb is not None and logo_alpha is not None:
            self._src_rgb = logo_rgb
            self._src_alpha = logo_alpha
        else:
            self._src_rgb = _DVD_RGB
            self._src_alpha = _DVD_ALPHA
        # Яркость исходника - основа тонировки по режимам cycle/custom.
        self._src_lum = cv2.cvtColor(self._src_rgb, cv2.COLOR_RGB2GRAY)

        self.x = self.y = 0.0
        self.vx = self.vy = 0.0
        self._color_idx = 0
        self._euphoria = 0
        self._last_wh: Tuple[int, int] = (0, 0)
        self._sprite_h = -1
        self._draw_rgb: Optional[np.ndarray] = None
        self._draw_alpha: Optional[np.ndarray] = None
        self._color_dirty = True
        # Режим lag: замороженный кадр-снимок, обновляемый на каждом ударе.
        self._lag_frame: Optional[np.ndarray] = None

    def _current_color(self) -> Tuple[int, int, int]:
        if self.color_mode == 'custom':
            return self.custom_color
        return _DVD_CYCLE[self._color_idx % len(_DVD_CYCLE)]

    def _rebuild_sprite(self, target_h: int) -> None:
        # Тонировка по яркости: белый силуэт становится ровно целевым цветом,
        # цветной PNG перекрашивается с сохранением своей светотени.
        if self.color_mode in ('mono', 'lag'):
            tinted = self._src_rgb
        else:
            r, g, b = self._current_color()
            lum = self._src_lum.astype(np.float32) / 255.0
            tinted = np.empty_like(self._src_rgb)
            tinted[:, :, 0] = np.clip(lum * r, 0, 255)
            tinted[:, :, 1] = np.clip(lum * g, 0, 255)
            tinted[:, :, 2] = np.clip(lum * b, 0, 255)
        sh, sw = self._src_alpha.shape[:2]
        tw = max(8, int(round(target_h * sw / max(sh, 1))))
        self._draw_rgb = cv2.resize(tinted, (tw, target_h), interpolation=cv2.INTER_LINEAR)
        self._draw_alpha = cv2.resize(self._src_alpha, (tw, target_h), interpolation=cv2.INTER_LINEAR)
        self._sprite_h = target_h
        self._color_dirty = False

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        h, w = frame.shape[:2]

        # Размер логотипа растёт с интенсивностью; квантуем, чтобы не
        # пересобирать спрайт на каждый микроскачок аудио.
        target_h = int(h * (0.09 + 0.22 * intensity))
        target_h = max(16, (target_h // 4) * 4)

        if (w, h) != self._last_wh:
            self._last_wh = (w, h)
            self.x = float(random.randint(0, max(1, w // 2)))
            self.y = float(random.randint(0, max(1, h // 2)))
            sign_x = 1.0 if random.random() < 0.5 else -1.0
            sign_y = 1.0 if random.random() < 0.5 else -1.0
            self.vx = sign_x * self.speed
            self.vy = sign_y * self.speed
            self._sprite_h = -1
            self._lag_frame = None

        # Режим lag: снимок кадра берётся при запуске и на каждом ударе о стену;
        # логотип-силуэт показывает этот замороженный кадр, пока летит.
        if self.color_mode == 'lag' and (
                self._lag_frame is None or self._lag_frame.shape[:2] != (h, w)):
            self._lag_frame = frame.copy()

        if target_h != self._sprite_h or self._color_dirty or self._draw_rgb is None:
            self._rebuild_sprite(target_h)

        sh, sw = self._draw_alpha.shape[:2]
        self.x += self.vx
        self.y += self.vy

        hit_x = hit_y = False
        if self.x <= 0:
            self.x = 0.0; self.vx = abs(self.vx); hit_x = True
        elif self.x + sw >= w:
            self.x = float(w - sw); self.vx = -abs(self.vx); hit_x = True
        if self.y <= 0:
            self.y = 0.0; self.vy = abs(self.vy); hit_y = True
        elif self.y + sh >= h:
            self.y = float(h - sh); self.vy = -abs(self.vy); hit_y = True

        if hit_x or hit_y:
            if self.color_mode == 'cycle':
                self._color_idx += 1
                self._color_dirty = True
            elif self.color_mode == 'lag':
                # Новый удар - берём свежий снимок текущего кадра.
                self._lag_frame = frame.copy()
            if hit_x and hit_y:
                self._euphoria = 8

        if self._color_dirty:
            self._rebuild_sprite(self._sprite_h)

        out = frame.copy()
        ix, iy = int(self.x), int(self.y)

        # Вспышка-эйфория при попадании в угол: увеличенное свечение позади лого.
        if self._euphoria > 0:
            glow_a = (self._draw_alpha.astype(np.float32) * (self._euphoria / 8.0))
            gh = int(sh * 1.6); gw = int(sw * 1.6)
            glow_rgb = cv2.resize(self._draw_rgb, (gw, gh), interpolation=cv2.INTER_LINEAR)
            glow_alpha = cv2.resize(_ensure_uint8(glow_a), (gw, gh), interpolation=cv2.INTER_LINEAR)
            glow_alpha = cv2.GaussianBlur(glow_alpha, (0, 0), gw * 0.06 + 1)
            _blit_sprite(out, glow_rgb, glow_alpha,
                         ix - (gw - sw) // 2, iy - (gh - sh) // 2, opacity=0.55)
            self._euphoria -= 1

        if (self.color_mode == 'lag' and self._lag_frame is not None
                and self._lag_frame.shape[:2] == (h, w)):
            # Силуэт-окно показывает замороженный кадр на текущей позиции.
            rgb_lag = np.ascontiguousarray(self._lag_frame[iy:iy + sh, ix:ix + sw])
            _blit_sprite(out, rgb_lag, self._draw_alpha, ix, iy, opacity=1.0)
        else:
            _blit_sprite(out, self._draw_rgb, self._draw_alpha, ix, iy, opacity=1.0)
        return _ensure_uint8(out)


# ──────────────────────────────────────────────────────────────────────────
#   WinPipes - перспективный 3D-трубопровод в духе скринсейвера «3D Pipes»
# ──────────────────────────────────────────────────────────────────────────


def _shade(color: Tuple[int, int, int], f: float) -> Tuple[int, int, int]:
    return (int(np.clip(color[0] * f, 0, 255)),
            int(np.clip(color[1] * f, 0, 255)),
            int(np.clip(color[2] * f, 0, 255)))


def _lighten(color: Tuple[int, int, int], f: float) -> Tuple[int, int, int]:
    return (int(color[0] + (255 - color[0]) * f),
            int(color[1] + (255 - color[1]) * f),
            int(color[2] + (255 - color[2]) * f))


# Палитра оригинального скринсейвера: приглушённый металлик - тил,
# оранжевый, жёлтый, серый, белый.
_PIPE_COLORS = [
    (52, 116, 116), (196, 108, 54), (200, 180, 92),
    (150, 150, 155), (198, 198, 202), (44, 92, 104),
]


def _norm3(x, y, z):
    n = math.sqrt(x * x + y * y + z * z)
    return x / n, y / n, z / n


# Источник света сверху (чуть слева) и к зрителю; half-vector для specular.
# Низкий ambient + широкий мягкий блик дают контраст тёмный низ / светлый
# верх, характерный для оригинального скринсейвера.
_LX, _LY, _LZ = _norm3(-0.35, -0.8, 0.55)
_HX, _HY, _HZ = _norm3(_LX + 0.0, _LY + 0.0, _LZ + 1.0)
_AMBIENT = 0.18
_DIFF = 0.80
_SPEC = 0.40
_SHINE = 15.0

# Шесть направлений решётки: +x -x +y -y +z -z; пары (0,1) (2,3) (4,5) обратны.
_PIPE_DIRS = [
    (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
]


class _PipeHead:
    """Голова трубопровода, идущая по целочисленной 3D-решётке.

    `seg_idx` - индекс текущего сегмента этой трубы; пока голова идёт прямо,
    сегмент продлевается, а не плодятся короткие куски (гладкий цилиндр).
    """

    __slots__ = ('cell', 'dir', 'color', 'seg_idx')

    def __init__(self, cell: Tuple[int, int, int], dir_idx: int,
                 color: Tuple[int, int, int]):
        self.cell = cell
        self.dir = dir_idx
        self.color = color
        self.seg_idx = -1


class WinPipesEffect(BaseEffect):
    """Перспективный 3D-трубопровод в духе скринсейвера Win95 «3D Pipes».

    Трубы строятся в 3D-решётке и проецируются перспективой с точкой схода
    в центре кадра. Каждый сегмент рисуется как затенённый цилиндр-impostor с
    бликом; скруглённые торцы капсул образуют гладкие колена. Вся сеть
    рисуется через z-буфер, поэтому перекрытие корректное. Сеть копится, при
    заполнении - сброс. Фон видео притемняется к чёрному на величину `takeover`.
    """
    trigger_types = [SegmentType.SILENCE, SegmentType.SUSTAIN]

    # Границы решётки в ячейках. Решётка заметно больше видимой области -
    # её боковые грани уходят за кадр, а дальняя тонет в тумане, поэтому
    # граница генерации никогда не видна.
    _NX = 18
    _NY = 11
    _NZ = 18

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 thickness: int = 10, takeover: float = 0.9,
                 speed: float = 3.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self.thickness = max(3, int(thickness))
        self.takeover = max(0.0, min(1.0, float(takeover)))
        self.speed = max(1.0, float(speed))

        # Сегменты: [cell_a, cell_b, color] (продлеваются на прямых прогонах).
        # Суставы: (cell, color) - шар в точке поворота.
        self._segments: List[list] = []
        self._joints: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = []
        self._heads: List[_PipeHead] = []
        self._occupied: set = set()
        self._wh: Tuple[int, int] = (0, 0)
        self._focal = 0.0
        self._cy = 1.0; self._sy = 0.0
        self._cp = 1.0; self._sp = 0.0
        self._cam_dist = 10.0
        self._fog_near = 6.0
        self._fog_far = 18.0

    # ── проекция ──────────────────────────────────────────────────────────
    def _project(self, cell: Tuple[int, int, int], w: int, h: int):
        """Ячейка решётки -> (sx, sy, depth).

        Мир повёрнут на небольшой угол (yaw+pitch) перед перспективой -
        иначе камера смотрит строго вдоль оси и трубы ложатся идеальной
        сеткой; лёгкий угол даёт объёмный вид, как в оригинале.
        """
        x = (cell[0] - self._NX * 0.5)
        y = (cell[1] - self._NY * 0.5)
        z = (cell[2] - self._NZ * 0.5)
        # Поворот вокруг Y (yaw), затем вокруг X (pitch).
        xr = x * self._cy + z * self._sy
        zr = -x * self._sy + z * self._cy
        yr = y * self._cp - zr * self._sp
        zr = y * self._sp + zr * self._cp
        depth = max(0.8, zr + self._cam_dist)
        f = self._focal
        sx = w * 0.5 + f * xr / depth
        sy = h * 0.5 - f * yr / depth
        return sx, sy, depth

    def _radius_px(self, depth: float) -> float:
        # Толщина трубы в мире -> экранный радиус по перспективе.
        world_r = self.thickness / 100.0
        return max(1.0, self._focal * world_r / depth)

    # ── состояние решётки ─────────────────────────────────────────────────
    def _reset(self, w: int, h: int) -> None:
        self._wh = (w, h)
        # В кадре только центральная часть большой решётки, ближние трубы
        # переполняют кадр - краёв сети не видно.
        self._focal = w * 1.0
        # Небольшой наклон камеры; знак yaw слегка варьируется, чтобы циклы
        # не выглядели одинаково.
        yaw = math.radians(22.0) * (1.0 if random.random() < 0.5 else -1.0)
        pitch = math.radians(12.0)
        self._cy = math.cos(yaw); self._sy = math.sin(yaw)
        self._cp = math.cos(pitch); self._sp = math.sin(pitch)
        self._cam_dist = self._NZ * 0.5 + 3.5
        # Туман: трубы плавно тонут в чёрном с глубиной - дальняя граница
        # растворяется, а не читается плоскостью.
        self._fog_near = self._cam_dist - self._NZ * 0.30
        self._fog_far = self._cam_dist + self._NZ * 0.55
        self._segments = []
        self._joints = []
        self._occupied = set()
        self._heads = []

    def _spawn(self) -> _PipeHead:
        # Спавн в центральной зоне решётки - рост идёт в видимой области,
        # а не теряется на дальней периферии огромной решётки.
        def near(center, span, lo, hi):
            return max(lo, min(hi, center + random.randint(-span, span)))
        cell = (near(self._NX // 2, 7, 1, self._NX - 1),
                near(self._NY // 2, 4, 1, self._NY - 1),
                near(self._NZ // 2, 7, 1, self._NZ - 1))
        self._occupied.add(cell)
        return _PipeHead(cell, random.randint(0, 5), random.choice(_PIPE_COLORS))

    def _in_bounds(self, c: Tuple[int, int, int]) -> bool:
        return 0 <= c[0] <= self._NX and 0 <= c[1] <= self._NY and 0 <= c[2] <= self._NZ

    def _candidates(self, head: _PipeHead) -> List[int]:
        rev = head.dir ^ 1
        out = []
        for d in range(6):
            if d == rev:
                continue
            dx, dy, dz = _PIPE_DIRS[d]
            nc = (head.cell[0] + dx, head.cell[1] + dy, head.cell[2] + dz)
            if self._in_bounds(nc) and nc not in self._occupied:
                out.append(d)
        return out

    def _advance(self, head: _PipeHead) -> None:
        cand = self._candidates(head)
        if not cand:
            # Труба уперлась - снимаем голову (новую заведёт _apply при нехватке).
            if head in self._heads:
                self._heads.remove(head)
            return
        # Чаще прямо, но повороты достаточно часты для плотной коленчатой сети.
        straight = head.dir in cand and head.seg_idx >= 0 and random.random() > 0.42
        if straight:
            nd = head.dir
        else:
            nd = random.choice(cand)
            if head.seg_idx >= 0:
                self._joints.append((head.cell, head.color))
        dx, dy, dz = _PIPE_DIRS[nd]
        nc = (head.cell[0] + dx, head.cell[1] + dy, head.cell[2] + dz)
        if straight:
            # Продлеваем текущий сегмент - остаётся гладким цилиндром.
            self._segments[head.seg_idx][1] = nc
        else:
            self._segments.append([head.cell, nc, head.color])
            head.seg_idx = len(self._segments) - 1
        self._occupied.add(nc)
        head.cell = nc
        head.dir = nd

    # ── растеризация: per-pixel impostor-шейдинг с z-буфером ────────────────
    def _render_cylinder(self, zbuf, cbuf, covbuf, p0, p1, r0, r1, d0, d1, color) -> None:
        sx = p1[0] - p0[0]; sy = p1[1] - p0[1]
        l2 = sx * sx + sy * sy
        if l2 < 1e-6:
            self._render_sphere(zbuf, cbuf, covbuf, p0[0], p0[1], max(r0, r1), d0, color)
            return
        H, W = zbuf.shape
        maxr = max(r0, r1) + 1.0
        x0 = max(0, int(math.floor(min(p0[0], p1[0]) - maxr)))
        x1 = min(W, int(math.ceil(max(p0[0], p1[0]) + maxr)))
        y0 = max(0, int(math.floor(min(p0[1], p1[1]) - maxr)))
        y1 = min(H, int(math.ceil(max(p0[1], p1[1]) + maxr)))
        if x1 <= x0 or y1 <= y0:
            return
        ys, xs = np.mgrid[y0:y1, x0:x1]
        xs = xs.astype(np.float32); ys = ys.astype(np.float32)
        wx = xs - p0[0]; wy = ys - p0[1]
        u = (wx * sx + wy * sy) / l2
        u = np.clip(u, 0.0, 1.0)
        cxp = p0[0] + u * sx; cyp = p0[1] + u * sy
        ddx = xs - cxp; ddy = ys - cyp
        dist = np.sqrt(ddx * ddx + ddy * ddy)
        r = r0 + (r1 - r0) * u
        cov = np.clip(r + 0.5 - dist, 0.0, 1.0)
        if not (cov > 0.0).any():
            return
        # Нормаль капсулы единообразно: на теле вектор перпендикулярен оси,
        # на скруглённых торцах включает осевую составляющую - те же формулы,
        # что у сферы, поэтому колено из двух торцов одного цвета выходит гладким.
        rr = np.maximum(r, 1e-3)
        nx = ddx / rr; ny = ddy / rr
        nz = np.sqrt(np.maximum(0.0, 1.0 - np.minimum(1.0, nx * nx + ny * ny)))
        self._write_shaded(zbuf, cbuf, covbuf, cov, nx, ny, nz, color,
                           d0 + (d1 - d0) * u - nz * 0.3, x0, x1, y0, y1)

    def _render_sphere(self, zbuf, cbuf, covbuf, cx, cy, radius, depth, color) -> None:
        H, W = zbuf.shape
        # Сустав чуть толще трубы - как в оригинале шар на повороте; та же
        # модель света и сглаживание, поэтому он гладко сливается с коленом.
        R = radius * 1.3
        x0 = max(0, int(math.floor(cx - R - 1)))
        x1 = min(W, int(math.ceil(cx + R + 1)))
        y0 = max(0, int(math.floor(cy - R - 1)))
        y1 = min(H, int(math.ceil(cy + R + 1)))
        if x1 <= x0 or y1 <= y0:
            return
        ys, xs = np.mgrid[y0:y1, x0:x1]
        dx = xs.astype(np.float32) - cx; dy = ys.astype(np.float32) - cy
        dist = np.sqrt(dx * dx + dy * dy)
        cov = np.clip(R + 0.5 - dist, 0.0, 1.0)
        if not (cov > 0.0).any():
            return
        nx = dx / R; ny = dy / R
        nz = np.sqrt(np.maximum(0.0, 1.0 - np.minimum(1.0, nx * nx + ny * ny)))
        self._write_shaded(zbuf, cbuf, covbuf, cov, nx, ny, nz, color,
                           depth - nz * 0.3, x0, x1, y0, y1)

    @staticmethod
    def _write_shaded(zbuf, cbuf, covbuf, cov, nx, ny, nz, color, depthz,
                      x0, x1, y0, y1) -> None:
        # Освещение по нормали: диффуз (Ламберт) + мягкий широкий блик.
        diff = np.clip(nx * _LX + ny * _LY + nz * _LZ, 0.0, 1.0)
        spec = np.clip(nx * _HX + ny * _HY + nz * _HZ, 0.0, 1.0) ** _SHINE
        shade = _AMBIENT + _DIFF * diff
        col = np.clip(
            np.stack([color[0] * shade, color[1] * shade, color[2] * shade], axis=-1)
            + (spec * (_SPEC * 255.0))[..., None], 0, 255)
        zsub = zbuf[y0:y1, x0:x1]
        csub = cbuf[y0:y1, x0:x1]
        covsub = covbuf[y0:y1, x0:x1]
        # Побеждает ближайший фрагмент; храним его цвет (без тумана), глубину и
        # покрытие. Туман и сглаживание применяются позже как прозрачность,
        # поэтому дальние трубы растворяют фон, а не закрашивают его чёрным.
        front = (depthz < zsub) & (cov > 0.004)
        if not front.any():
            return
        zsub[front] = depthz[front]
        csub[front] = col[front]
        covsub[front] = cov[front]

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        if (w, h) != self._wh or not self._heads:
            self._reset(w, h)

        # Одновременных труб больше с интенсивностью - как в оригинале, где
        # несколько разноцветных веток растут вместе.
        target_heads = 5 + int(round(intensity * 5))
        # Бюджет роста задаёт плотность в видимой области (решётка много
        # больше кадра, доля заполнения тут не годится).
        budget = 1300

        steps = max(1, int(round(self.speed * (0.6 + intensity))))
        for _ in range(steps):
            for head in list(self._heads):
                self._advance(head)
            while len(self._heads) < target_heads and len(self._occupied) < budget:
                self._heads.append(self._spawn())

        # Сброс, когда набрали бюджет, - экран очищается и рост начинается
        # заново, как в настоящем скринсейвере.
        if len(self._occupied) > budget:
            self._reset(w, h)

        zbuf = np.full((h, w), 1e9, dtype=np.float32)
        cbuf = np.zeros((h, w, 3), dtype=np.float32)
        covbuf = np.zeros((h, w), dtype=np.float32)

        # Тела труб.
        for a, b, color in self._segments:
            sax, say, dpa = self._project(a, w, h)
            sbx, sby, dpb = self._project(b, w, h)
            ra = self._radius_px(dpa); rb = self._radius_px(dpb)
            col = np.array(color, dtype=np.float32)
            self._render_cylinder(zbuf, cbuf, covbuf, (sax, say), (sbx, sby),
                                  ra, rb, dpa, dpb, col)
        # Шары-суставы на поворотах (чуть ближе, чтобы чисто закрыть угол).
        for c, color in self._joints:
            scx, scy, dpc = self._project(c, w, h)
            rc = self._radius_px(dpc)
            col = np.array(color, dtype=np.float32)
            self._render_sphere(zbuf, cbuf, covbuf, scx, scy, rc, dpc - 0.12, col)

        # Прозрачность = покрытие (сглаживание кромок) * туман по глубине.
        # Дальние трубы плавно исчезают, показывая фон, а не чернят его;
        # `takeover` отдельно притемняет сам фон под трубопроводом.
        fog = np.clip((self._fog_far - zbuf) / (self._fog_far - self._fog_near),
                      0.0, 1.0)
        alpha = (covbuf * fog)[..., None]
        bg = frame.astype(np.float32) * (1.0 - self.takeover)
        out = bg * (1.0 - alpha) + cbuf * alpha
        return _ensure_uint8(out)
