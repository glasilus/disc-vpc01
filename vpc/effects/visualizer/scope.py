"""Scope-визуализаторы: осциллограф-волна и фигуры Лиссажу.

Оба имитируют люминофорный CRT-осциллограф: одна яркая сглаженная линия с
мягким свечением вокруг. Осциллограф рисует *настоящую форму волны* играющего
кадра - сырые PCM-сэмплы, выровненные по триггеру (переход через ноль), - так
что бас даёт большие плавные качели, а хай-хэт - быструю рябь, точь-в-точь как
настоящий аудиоосциллограф; при отсутствии сырого аудио есть аддитивный
фолбэк из полос спектра. Фигура Лиссажу держит
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
    """Осциллограф во временной области - настоящая форма волны текущего кадра.

    Реактор кладёт в сэмпл окно сырых PCM-сэмплов, играющих ровно в этот
    момент, выровненное по восходящему переходу через ноль (триггер), - линия
    реагирует мгновенно, как спектр-бары, и не ёрзает по фазе. Вертикальный
    масштаб фиксирован (нормировка к пику трека): тихо - маленькая линия,
    громко - во весь экран. Если сырого аудио нет (превью статичного кадра),
    форма восстанавливается аддитивным синтезом из полос спектра.
    """

    def __init__(self, color=(0, 255, 0), thickness=2, scale=2.0, **kw):
        super().__init__(**kw)
        self.color = tuple(int(c) for c in color)
        self.thickness = max(1, int(thickness))
        # Масштаб времени (зум): во сколько раз сузить показанное окно. Больше -
        # меньше периодов в кадре и глаже линия, меньше "шипов".
        self.scale = max(1.0, float(scale))

    def _trace_from_wave(self, wave, w):
        """Ресемпл части окна волны на ширину кадра. Возвращает форму в ~[-1,1].

        Показываем только 1/scale начала окна (от точки триггера): чем больше
        масштаб, тем меньше периодов помещается в кадр и тем глаже линия.
        """
        wave = np.asarray(wave, np.float32)
        n = max(2, int(len(wave) / self.scale))
        wave = wave[:n]
        src = np.linspace(0.0, 1.0, len(wave), dtype=np.float32)
        dst = np.linspace(0.0, 1.0, w, dtype=np.float32)
        return np.interp(dst, src, wave).astype(np.float32)

    def _trace_from_bins(self, sample, w):
        """Фолбэк без сырого аудио: аддитивный синтез волны из полос спектра."""
        bins = np.asarray(sample.bins, np.float32)
        n = max(1, len(bins))
        x = np.linspace(0.0, 1.0, w, dtype=np.float32)
        scroll = sample.t * 2.2
        wave = np.zeros(w, np.float32)
        for k in range(n):
            f = k + 1
            wave += bins[k] * np.sin(2.0 * np.pi * f * x + f * scroll)
        wave /= (np.abs(wave).max() + 1e-6)
        level = float(np.clip(bins.mean() * 2.5, 0.02, 1.0))
        return wave * (0.12 + 0.88 * level)

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        wave = getattr(sample, 'wave', None)
        if wave is not None and len(wave) > 1:
            trace = self._trace_from_wave(wave, w)
            fill = 0.94   # шкала фиксирована, динамику несёт сама волна
        else:
            trace = self._trace_from_bins(sample, w)
            fill = 1.0    # синтез уже промасштабирован уровнем

        amp = (h / 2.0 - 2.0) * fill
        ys = np.clip(h / 2.0 - trace * amp, 0, h - 1).astype(np.int32)
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
