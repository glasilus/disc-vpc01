"""Спектральные визуализаторы: линейный эквалайзер и радиальная корона."""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


def _resample_bins(bins: np.ndarray, n: int) -> np.ndarray:
    if len(bins) == n:
        return bins
    x = np.linspace(0, len(bins) - 1, n)
    return np.interp(x, np.arange(len(bins)), bins)


class SpectrumBarsEffect(VisualizerEffect):
    """Классический эквалайзер WMP: полосы по частотным диапазонам."""

    def __init__(self, n_bands=24, color=(0, 255, 0), mirror=False, **kw):
        super().__init__(**kw)
        self.n_bands = int(n_bands)
        self.color = tuple(int(c) for c in color)
        self.mirror = bool(mirror)

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        vals = _resample_bins(sample.bins, self.n_bands)
        bw = max(1, w // self.n_bands)
        col = self.color
        for i, v in enumerate(vals):
            bh = int(float(v) * (h - 2))
            if bh <= 0:
                continue
            x0 = i * bw
            if self.mirror:
                y0 = (h - bh) // 2
                cv2.rectangle(vis, (x0, y0), (x0 + bw - 1, y0 + bh), col, -1)
            else:
                cv2.rectangle(vis, (x0, h - bh), (x0 + bw - 1, h - 1), col, -1)
        field = cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
        return vis, field


class RadialSpectrumEffect(VisualizerEffect):
    """Полосы эквалайзера, обёрнутые вокруг круга - пульсирующая корона.

    Каждая полоса - прямоугольный бар, выходящий из окружности наружу (а не
    тонкая линия): ширина бара занимает почти весь сектор своего луча, длина
    следует за громкостью полосы.
    """

    def __init__(self, rays=48, color=(0, 255, 0), rotate=0.0, **kw):
        super().__init__(**kw)
        self.rays = int(rays)
        self.color = tuple(int(c) for c in color)
        self.rotate = float(rotate)

    def _render(self, h, w, sample):
        vis = np.zeros((h, w, 3), np.uint8)
        cx, cy = w // 2, h // 2
        r0 = min(h, w) // 6
        vals = _resample_bins(sample.bins, self.rays)
        col = self.color
        # Половина ширины бара по дуге: почти весь сектор луча, с зазором.
        half_w = max(1.0, (np.pi * r0 / self.rays) * 0.8)
        for i, v in enumerate(vals):
            ang = (i / self.rays) * 2 * np.pi + self.rotate + sample.t * 0.5
            r1 = r0 + float(v) * min(h, w) * 0.4
            dx, dy = np.cos(ang), np.sin(ang)
            px, py = -dy, dx   # перпендикуляр к лучу
            # Четыре угла бара: основание на окружности r0, вершина на r1.
            quad = np.array([
                [cx + dx * r0 + px * half_w, cy + dy * r0 + py * half_w],
                [cx + dx * r0 - px * half_w, cy + dy * r0 - py * half_w],
                [cx + dx * r1 - px * half_w, cy + dy * r1 - py * half_w],
                [cx + dx * r1 + px * half_w, cy + dy * r1 + py * half_w],
            ], np.int32)
            cv2.fillConvexPoly(vis, quad, col, cv2.LINE_AA)
        field = cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
        return vis, field
