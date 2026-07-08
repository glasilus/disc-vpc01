"""Абстрактные визуализаторы: плазменное поле, бит-частицы, поле потока."""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


class PlasmaFieldEffect(VisualizerEffect):
    """Процедурная плазма; цвет и скорость модулируются частотными полосами."""

    def __init__(self, scale=0.04, **kw):
        super().__init__(**kw)
        self.scale = float(scale)

    def _render(self, h, w, sample):
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        t = sample.t * (1.0 + sample.mid * 3.0)
        s = self.scale
        v = (np.sin(xx * s + t) + np.sin(yy * s + t * 1.3)
             + np.sin((xx + yy) * s * 0.5 + t * 0.7)
             + np.sin(np.sqrt((xx - w / 2) ** 2 + (yy - h / 2) ** 2) * s + t))
        v = (v + 4) / 8.0
        hue = ((v * 180 + sample.bass * 90) % 180).astype(np.uint8)
        sat = np.full((h, w), 255, np.uint8)
        val = np.clip(v * 255 * (0.5 + sample.high), 0, 255).astype(np.uint8)
        hsv = cv2.merge([hue, sat, val])
        vis = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        return vis, val


class BeatParticlesEffect(VisualizerEffect):
    """Частицы-фейерверк вылетают из центра на каждый удар (beat).

    Пул частиц: каждый beat порождает радиальный залп, чей размер зависит от
    баса, громкий onset даёт залп поменьше, а фоновая энергия подкидывает
    слабую струйку частиц, чтобы поле не пустовало даже на треках с мягким
    кик-драмом. Частицы летят наружу, гравитация тянет их вниз, они остывают
    от горячего к холодному цвету и УЛЕТАЮТ за кадр (без wrap по модулю -
    в старой версии искры телепортировались через экран, и это читалось как
    шум, а не фейерверк). Мягкий bloom даёт свечение искрам.
    """

    # Колонки пула частиц.
    _X, _Y, _VX, _VY, _LIFE, _HOT = range(6)

    def __init__(self, count=120, gravity=0.3, **kw):
        super().__init__(**kw)
        self.count = int(count)
        self.gravity = float(gravity)
        self._p = None     # (N,6): x, y, vx, vy, life(0..1), hot(0..1)

    def apply(self, frame, seg, draft):
        # Физика шагает каждый кадр, чтобы движение было непрерывным
        # независимо от trigger/chance-гейтинга (тот же паттерн, что и
        # история в PaintCanvasEffect).
        self._step(frame.shape[0], frame.shape[1], seg)
        return super().apply(frame, seg, draft)

    def _ensure(self):
        if self._p is None:
            self._p = np.zeros((self.count, 6), np.float32)  # все мертвы (life 0)

    def _emit(self, k, h, w, bass, hot):
        if k <= 0:
            return
        dead = np.where(self._p[:, self._LIFE] <= 0.02)[0]
        if dead.size == 0:
            return
        idx = dead[:k]
        m = idx.size
        ang = np.random.uniform(0, 2 * np.pi, m).astype(np.float32)
        spd = np.random.uniform(1.0, 3.0, m).astype(np.float32) * (1.5 + bass * 7.0)
        self._p[idx, self._X] = w / 2.0
        self._p[idx, self._Y] = h / 2.0
        self._p[idx, self._VX] = np.cos(ang) * spd
        # Небольшой сдвиг вверх, чтобы траектория выгибалась дугой как у
        # фейерверка, пока не возьмёт верх гравитация.
        self._p[idx, self._VY] = np.sin(ang) * spd - spd * 0.3
        self._p[idx, self._LIFE] = 1.0
        self._p[idx, self._HOT] = hot

    def _step(self, h, w, seg):
        self._ensure()
        live = getattr(seg, 'live', None)
        bass = float(getattr(live, 'bass', 0.0) or 0.0)
        onset = float(getattr(live, 'onset', 0.0) or 0.0)
        beat = bool(getattr(live, 'beat', False))

        if beat:
            self._emit(int(self.count * (0.4 + 0.6 * bass)), h, w, bass, 1.0)
        elif onset > 0.5:
            self._emit(int(self.count * 0.15), h, w, bass, 0.6)
        else:
            self._emit(int(self.count * 0.03 * (bass + onset)), h, w, bass, 0.3)

        alive = self._p[:, self._LIFE] > 0.02
        self._p[alive, self._X] += self._p[alive, self._VX]
        self._p[alive, self._Y] += self._p[alive, self._VY]
        self._p[alive, self._VY] += self.gravity
        self._p[alive, self._VX] *= 0.99   # сопротивление воздуха
        self._p[:, self._LIFE] *= 0.955

    def _render(self, h, w, sample):
        self._ensure()
        vis = np.zeros((h, w, 3), np.uint8)
        for row in self._p:
            life = row[self._LIFE]
            if life <= 0.02:
                continue
            x, y = row[self._X], row[self._Y]
            xi, yi = int(x), int(y)
            if xi < -8 or xi >= w + 8 or yi < -8 or yi >= h + 8:
                continue
            # Горячая искра (бело-жёлтая) остывает до красно-синих угольков по мере затухания.
            hot = min(1.0, row[self._HOT] + 0.3)
            col = (int(255 * hot * life),
                   int(220 * life * life),
                   int(255 * (1.0 - life) * 0.6))
            # След движения: короткий хвост позади искры вдоль вектора скорости -
            # характерный вид фейерверка. Сверху яркая точка-голова.
            px, py = int(x - row[self._VX]), int(y - row[self._VY])
            cv2.line(vis, (px, py), (xi, yi), col, max(1, int(1 + 2 * life)),
                     cv2.LINE_AA)
            cv2.circle(vis, (xi, yi), max(1, int(1 + 2.5 * life)), col, -1,
                       cv2.LINE_AA)
        glow = cv2.GaussianBlur(vis, (0, 0), 3.5)
        vis = cv2.addWeighted(vis, 1.0, glow, 0.9, 0.0)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)


class FlowFieldEffect(VisualizerEffect):
    """Тысячи частиц, вычерчивающих линии тока турбулентного векторного поля.

    Узнаваемый вид "flow field": облако частиц движется вдоль плавно
    меняющегося векторного поля, каждая оставляет затухающий след, и по
    следам видна завихрённость поля. Само поле - слоистая синусоидальная
    турбулентность в духе "curl-noise", медленно эволюционирующая во времени;
    mid-полоса задаёт скорость потока, а текущие полосы окрашивают "чернила".
    Частицы, покидающие кадр, возрождаются в случайной точке, чтобы плотность
    оставалась стабильной.
    """

    def __init__(self, noise_scale=0.02, count=1600, **kw):
        super().__init__(**kw)
        self.noise_scale = float(noise_scale)
        self.count = int(count)
        self._acc = None
        self._pts = None

    def _ensure(self, h, w):
        if self._acc is None or self._acc.shape[:2] != (h, w):
            self._acc = np.zeros((h, w, 3), np.float32)
            self._pts = (np.random.rand(self.count, 2).astype(np.float32)
                         * np.array([w, h], np.float32))

    def _field_angle(self, x, y, t):
        s = self.noise_scale
        a = (np.sin(x * s + t)
             + np.cos(y * s * 1.3 - t * 0.7)
             + 0.5 * np.sin((x + y) * s * 0.5 + t * 1.3))
        return a * np.pi

    def _render(self, h, w, sample):
        self._ensure(h, w)
        self._acc *= 0.94   # затухание следов

        t = sample.t * 0.6
        x = self._pts[:, 0]
        y = self._pts[:, 1]
        ang = self._field_angle(x, y, t)
        spd = 1.0 + 2.5 * float(np.clip(sample.mid, 0.0, 1.0)) \
            + 1.5 * float(np.clip(sample.bass, 0.0, 1.0))
        nx = x + np.cos(ang) * spd
        ny = y + np.sin(ang) * spd

        # Возрождаем в случайной точке частицы, покинувшие кадр.
        oob = (nx < 0) | (nx >= w) | (ny < 0) | (ny >= h)
        m = int(oob.sum())
        if m:
            nx[oob] = np.random.rand(m).astype(np.float32) * w
            ny[oob] = np.random.rand(m).astype(np.float32) * h

        # Цвет "чернил" из текущих полос (пайплайн в RGB). Значения занижены,
        # чтобы плотные линии тока насыщались до яркого цвета, а не выбеливались
        # в сплошной белый за долгий рендер.
        col = np.array([30 + 120 * sample.high,
                        45 + 110 * sample.mid,
                        80 + 90 * sample.bass], np.float32)
        xi = np.clip(nx.astype(np.int32), 0, w - 1)
        yi = np.clip(ny.astype(np.int32), 0, h - 1)
        np.add.at(self._acc, (yi, xi), col)
        np.clip(self._acc, 0, 255, out=self._acc)

        self._pts[:, 0] = nx
        self._pts[:, 1] = ny
        vis = np.clip(self._acc, 0, 255).astype(np.uint8)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
