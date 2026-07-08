"""Scope-визуализаторы: осциллограф-волна и фигуры Лиссажу.

Оба имитируют люминофорный CRT-осциллограф: одна яркая сглаженная линия с
мягким свечением вокруг. Осциллограф восстанавливает *волну во временной
области* из спектра (аддитивный синтез - каждая полоса это гармоника),
поэтому басовый момент даёт большие плавные качели, а яркий хай-хэт - быструю
рябь, точь-в-точь как настоящий аудиоосциллограф. Фигура Лиссажу держит
целочисленные соотношения частот, чтобы кривая оставалась чистой замкнутой
линией, а звук управляет только дрейфом фазы и размером, а не частотами
(нецелые соотношения никогда не замыкаются и вырождаются в каракули).
"""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


def _phosphor(vis: np.ndarray, sigma: float = 3.0, gain: float = 0.7) -> np.ndarray:
    """Добавляет мягкое CRT-свечение вокруг нарисованной линии (screen-add размытия)."""
    glow = cv2.GaussianBlur(vis, (0, 0), sigma)
    return cv2.addWeighted(vis, 1.0, glow, gain, 0.0)


class OscilloscopeEffect(VisualizerEffect):
    """Осциллограф во временной области, восстановленный из спектра.

    Линия - это волна на всю ширину, попиксельно построенная суммированием
    одной гармоники на каждую частотную полосу (амплитуда = магнитуда полосы).
    Её *форма* берётся из спектра, а *размер* - из общего уровня, так что это
    никогда не плоский зигзаг из 24 точек, а вид настоящего осциллографа.
    """

    def __init__(self, color=(60, 255, 120), thickness=2, **kw):
        super().__init__(**kw)
        self.color = tuple(int(c) for c in color)
        self.thickness = max(1, int(thickness))

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        bins = np.asarray(sample.bins, np.float32)
        n = max(1, len(bins))
        x = np.linspace(0.0, 1.0, w, dtype=np.float32)

        # Аддитивное восстановление: полоса k управляет гармоникой (k+1).
        # Каждая гармоника скроллится со своей скоростью, поэтому линия
        # дрейфует и эволюционирует, как осциллограф, не совсем
        # синхронизированный с сигналом.
        scroll = sample.t * 2.2
        wave = np.zeros(w, np.float32)
        for k in range(n):
            f = k + 1
            wave += bins[k] * np.sin(2.0 * np.pi * f * x + f * scroll)

        # Форма из спектра (единичная амплитуда), размер из громкости.
        wave /= (np.abs(wave).max() + 1e-6)
        level = float(np.clip(bins.mean() * 2.5, 0.02, 1.0))
        amp = (h / 2.0 - 2.0) * (0.12 + 0.88 * level)
        ys = (h / 2.0 - wave * amp).astype(np.int32)
        xs = np.linspace(0, w - 1, w).astype(np.int32)

        pts = np.stack([xs, ys], axis=1).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], False, self.color, self.thickness, cv2.LINE_AA)
        vis = _phosphor(vis, sigma=2.5, gain=0.7)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)


class LissajousEffect(VisualizerEffect):
    """XY-фигура Лиссажу - чистая замкнутая кривая, морфящаяся под музыку.

    Частоты закреплены на небольшом целочисленном соотношении
    (a : round(ratio)), так что кривая всегда замкнута; звук управляет
    дрейфом фазы (скоростью вращения по семейству фигур) и амплитудой
    (насколько сильно она "дышит") - это и есть характерное движение
    XY-осциллографа. Амплитуда ограничена, чтобы фигура не выходила за кадр.
    """

    def __init__(self, color=(0, 220, 255), ratio=3.0, **kw):
        super().__init__(**kw)
        self.color = tuple(int(c) for c in color)
        self.ratio = float(ratio)
        self._phase = 0.0

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        tt = np.linspace(0.0, 2.0 * np.pi, 2000, dtype=np.float32)

        a = 2
        b = max(1, int(round(self.ratio)))
        # Фаза дрейфует каждый кадр - фигура медленно вращается/морфит.
        # Бас подталкивает скорость дрейфа, давая всплеск на ударах.
        self._phase += 0.015 + sample.bass * 0.05
        delta = self._phase

        level = float(np.clip(max(sample.bass, sample.mid, sample.high), 0.0, 1.0))
        scale = min(0.95, 0.55 + 0.4 * level)
        rx = (w / 2.0 - 4.0) * scale
        ry = (h / 2.0 - 4.0) * scale
        xs = (w / 2.0 + np.sin(a * tt + delta) * rx).astype(np.int32)
        ys = (h / 2.0 + np.sin(b * tt) * ry).astype(np.int32)

        pts = np.stack([xs, ys], axis=1).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], False, self.color, 1, cv2.LINE_AA)
        vis = _phosphor(vis, sigma=2.5, gain=0.8)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
