"""Win95-virus-aesthetic effects: things that look like a 90s machine
under malware attack rather than a video glitch.

  - CursorStormEffect: a swarm of fake Win95 mouse pointers crawling
    over the frame, leaving short trails.
  - BSODShredEffect: random horizontal bands of the frame are replaced
    with fragments of bluescreen text.

Both share the in-module helpers `_make_cursor_sprite()` and
`_BSOD_LINES` so adding more virus-style effects later doesn't have to
reinvent the chrome.
"""
from __future__ import annotations

import random
from collections import deque
from typing import Deque, List, Tuple

import cv2
import numpy as np

from .base import BaseEffect, _ensure_uint8


# ──────────────────────────────────────────────────────────────────────────
#   Win95 cursor sprite — built once, cached at module scope.
# ──────────────────────────────────────────────────────────────────────────
#
# Hand-coded 16x22 bitmap of the canonical Win95 arrow pointer.
# Glyphs:
#     '#' = black border
#     '.' = white fill
#     ' ' = transparent
# The shape comes from the standard cursor image distributed with NT4 /
# Win95: a sharp arrow with a short tail and a single-pixel black border.
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
    """Return (rgb, alpha) arrays for the Win95 arrow pointer.

    rgb is uint8 HxWx3, alpha is uint8 HxW with 0 = transparent and
    255 = opaque. Computed once at import; subsequent calls are free.
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
    """Alpha-blit `rgb` onto `frame` at top-left (x, y), in place.

    Off-screen positions are clipped silently — overdraw at edges of the
    frame is the desired look (cursors crawling off the side).
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
    """Single brownian-walking cursor with its own short trail history."""

    __slots__ = ('x', 'y', 'vx', 'vy', 'trail')

    def __init__(self, w: int, h: int):
        self.x = float(random.randint(0, max(0, w - 1)))
        self.y = float(random.randint(0, max(0, h - 1)))
        self.vx = (random.random() - 0.5) * 6.0
        self.vy = (random.random() - 0.5) * 6.0
        self.trail: Deque[Tuple[int, int]] = deque(maxlen=6)

    def step(self, w: int, h: int, jitter: float) -> None:
        # Per-frame velocity perturbation; clamp speed so cursors don't
        # run away off-screen instantly. Bounce off frame edges.
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
    """Swarm of Win95 cursors crawling over the picture with brief
    trails. Number of cursors and motion jitter scale with intensity.

    Stateful: pointer positions persist across frames so motion is
    continuous. The pointer count is recomputed per-segment, which lets
    intensity changes through audio segments grow / shrink the swarm
    without a hard reset.
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

        # Number of pointers grows with intensity; never less than 1
        # when the effect fires at all so the user always sees something.
        target_n = max(1, int(round(intensity * self.max_pointers)))
        # Add or trim pointers — not a hard reset, so existing motion
        # stays smooth across slider changes.
        while len(self._pointers) < target_n:
            self._pointers.append(_Pointer(w, h))
        while len(self._pointers) > target_n:
            self._pointers.pop()

        out = frame.copy()
        # Higher intensity = wilder motion = scarier "infestation" feel.
        jitter = 0.6 + intensity * 1.6
        for p in self._pointers:
            p.step(w, h, jitter)
            # Trail first (oldest = most translucent), pointer last so it
            # always sits on top of its own tail.
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


# Authentic-looking bluescreen scraps. Picked at random per band; the
# mix of titles, hex addresses and dump prose sells the effect.
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

# Classic NT bluescreen background; close to the canonical RGB(0, 0, 168).
_BSOD_BG = (0, 0, 168)
_BSOD_FG = (255, 255, 255)


class BSODShredEffect(BaseEffect):
    """Slices random horizontal bands out of the frame and replaces them
    with bluescreen-styled text. Number of bands and per-band height
    scale with intensity. Stateless per-frame: every frame picks fresh
    bands so the effect strobes / shreds.
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
        # Font scale & line spacing tuned so a single line nests inside
        # the smallest band we draw without overlapping the next band.
        font = cv2.FONT_HERSHEY_PLAIN
        font_scale = 1.0
        line_h = 14
        for _ in range(n_bands):
            band_h = random.randint(int(line_h * 1.5),
                                    int(line_h * (2 + intensity * 4)))
            band_h = min(band_h, max(line_h * 2, h // 4))
            y0 = random.randint(0, max(0, h - band_h))
            out[y0:y0 + band_h] = _BSOD_BG
            # How many text lines fit in the band; minus a 4px top margin.
            n_lines = max(1, (band_h - 4) // line_h)
            for li in range(n_lines):
                line = random.choice(_BSOD_LINES)
                # Truncate so it never extends beyond the right edge —
                # cv2.putText silently overflows otherwise.
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
