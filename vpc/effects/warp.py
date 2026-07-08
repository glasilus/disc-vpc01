"""Семейство warp-эффектов: градиентный поток, вихрь, фрактальный шум, self-displace.

Эти четыре эффекта - CPU-only альтернатива optical-flow датамошингу,
построенная на разных источниках поля смещений: собеловский градиент
предыдущего кадра, спираль с гауссовым затуханием, fBm-шум и цветовые
каналы прошлого кадра.

У всех есть общий счётчик кадров (`_t`), который тикает при каждом вызове
`apply()` независимо от того, сработал эффект или нет. Он двигает медленные
фазовые модуляции (центр по фигуре Лиссажу, срез 3D-шума, дыхание амплитуды),
чтобы поле смещений было в непрерывном движении на протяжении сегмента,
а не застывало статичным паттерном.
"""
from __future__ import annotations

import cv2
import numpy as np
from opensimplex import noise3array

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


_GRID_CACHE: dict = {}


def _grid(h: int, w: int):
    """Возвращает закешированные координатные сетки (xs, ys) для формы (h, w).

    Без кеша always-on warp-эффекты выделяли бы ~80-150 МБ float32-сеток
    на каждый кадр. Кеш по (h, w) держит живыми всего два массива вместо
    постоянных аллокаций, с которыми GC не успевает справляться.
    Закешированные массивы по соглашению read-only (вызывающий код всегда
    делает `xs - cx` / `xs + dx`, что создаёт новый массив).
    """
    key = (h, w)
    g = _GRID_CACHE.get(key)
    if g is None:
        # Ограничиваем кеш: больше ~3 разных размеров означало бы, что
        # одновременно живы draft+preview+final, а больше нам не нужно.
        if len(_GRID_CACHE) > 4:
            _GRID_CACHE.clear()
        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
        xs.setflags(write=False)
        ys.setflags(write=False)
        g = (xs, ys)
        _GRID_CACHE[key] = g
    return g


class _Warpable(BaseEffect):
    """Миксин: монотонный счётчик кадров, растёт на каждом apply()."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._t = 0

    def apply(self, frame, seg, draft):
        self._t += 1
        return super().apply(frame, seg, draft)


class DerivWarpEffect(_Warpable):
    """Собелевский градиент предыдущего кадра как поле векторов смещения,
    плюс медленный вращательный дрейф.

    Градиент Собеля даёт локальное направление "где край"; поверх него
    накладывается медленное закручивание с колеблющейся во времени силой,
    чтобы поле не застывало даже на статичном входе.
    """
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE,
                     SegmentType.DROP, SegmentType.SUSTAIN]

    def __init__(self, blend=0.35, **kw):
        super().__init__(**kw)
        self.blend = blend
        self._prev = None

    def apply(self, frame, seg, draft):
        result = super().apply(frame, seg, draft)
        if result is frame:
            self._prev = frame.copy()
        return result

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        if self._prev is None or self._prev.shape != frame.shape:
            self._prev = frame.copy()
            return frame

        gray = cv2.cvtColor(self._prev, cv2.COLOR_RGB2GRAY).astype(np.float32)
        scale_f = 2 if draft else 1
        if draft:
            gray = cv2.resize(gray, (w // scale_f, h // scale_f))

        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)

        if draft:
            gx = cv2.resize(gx, (w, h))
            gy = cv2.resize(gy, (w, h))

        max_mag = float(np.sqrt(gx ** 2 + gy ** 2).max()) + 1e-6
        # Амплитуда плавает во времени - дышит ±25% вокруг базового значения.
        breath = 1.0 + 0.25 * np.sin(self._t * 0.13)
        disp_scale = intensity * 40.0 * breath
        dx = (gx / max_mag) * disp_scale
        dy = (gy / max_mag) * disp_scale

        # Медленный вращательный дрейф поверх собелевского поля: весь кадр
        # покачивается вокруг центра, пока локальный градиентный warp
        # делает своё дело.
        cx, cy = w * 0.5, h * 0.5
        xs_g, ys_g = _grid(h, w)
        swirl_amp = intensity * 0.06 * np.sin(self._t * 0.07)
        dx += -(ys_g - cy) * swirl_amp
        dy += (xs_g - cx) * swirl_amp

        map_x = np.clip(xs_g + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys_g + dy, 0, h - 1).astype(np.float32)

        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        warped = cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_REFLECT)

        blend = min(0.9, self.blend * (0.5 + intensity))
        if blend > 0:
            prev_disp = cv2.remap(self._prev, map_x * 0.4, map_y * 0.4,
                                  interp, borderMode=cv2.BORDER_REFLECT)
            warped = cv2.addWeighted(warped, 1.0 - blend, prev_disp, blend, 0)

        self._prev = frame.copy()
        return _ensure_uint8(warped)


class VortexWarpEffect(_Warpable):
    """Спираль с гауссовым затуханием, центр которой блуждает по фигуре Лиссажу.

    Центр, угловая скорость и сигма затухания эволюционируют вместе с `_t`,
    поэтому спираль никогда не повторяется от кадра к кадру: прецессирует,
    скорость вращения дышит, а затронутая область то растёт, то сжимается.
    """
    trigger_types = [SegmentType.BUILD, SegmentType.IMPACT,
                     SegmentType.SUSTAIN, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        t = self._t * 0.04
        # Центр блуждает по фигуре Лиссажу - не повторяется на коротких интервалах.
        cx = w * (0.5 + 0.20 * np.sin(t * 1.0))
        cy = h * (0.5 + 0.18 * np.sin(t * 1.3 + 0.7))
        sigma = min(w, h) * (0.35 + 0.15 * np.sin(t * 0.6))
        # Направление периодически меняется, отсюда дыхание вихря.
        rot_mod = np.sin(t * 0.9)

        xs_g, ys_g = _grid(h, w)
        xs = xs_g - cx
        ys = ys_g - cy
        r_sq = xs * xs + ys * ys
        # Мягкое ограничение угла, чтобы always-on с максимальной интенсивностью
        # не проворачивал пиксель на произвольно большой угол (было 5 рад → 286°).
        angle = (intensity * 3.5 * rot_mod
                 * np.exp(-r_sq / (2.0 * sigma * sigma + 1e-6))).astype(np.float32)
        cos_a = np.cos(angle, dtype=np.float32)
        sin_a = np.sin(angle, dtype=np.float32)
        map_x = np.clip(xs * cos_a - ys * sin_a + cx, 0, w - 1).astype(np.float32)
        map_y = np.clip(xs * sin_a + ys * cos_a + cy, 0, h - 1).astype(np.float32)
        # На всякий случай вычищаем NaN/Inf, которые могли просочиться из
        # вырожденной sigma или угла. cv2.remap на NaN-координатах даёт
        # жёсткий SIGSEGV на некоторых Windows-сборках OpenCV.
        np.nan_to_num(map_x, copy=False, nan=0.0, posinf=w - 1.0, neginf=0.0)
        np.nan_to_num(map_y, copy=False, nan=0.0, posinf=h - 1.0, neginf=0.0)

        if draft:
            mh, mw = h // 2, w // 2
            map_xd = cv2.resize(map_x, (mw, mh)) * 0.5
            map_yd = cv2.resize(map_y, (mw, mh)) * 0.5
            small = cv2.resize(frame, (mw, mh))
            result = cv2.remap(small, map_xd, map_yd,
                               cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)
            return _ensure_uint8(cv2.resize(result, (w, h), interpolation=cv2.INTER_NEAREST))

        return _ensure_uint8(
            cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        )


class FractalNoiseWarpEffect(_Warpable):
    """Поле фрактального шума, текущее через 3D-объём шума.

    Поле смещений сэмплируется из `opensimplex.noise3` с медленно растущей
    z-координатой (`_t·dt`). Получается непрерывный срез анимированного
    3D-"флюида" - поле течёт, а не пересоздаётся заново на каждом сегменте.
    """
    trigger_types = list(SegmentType)

    def __init__(self, octaves=4, **kw):
        super().__init__(**kw)
        self.octaves = octaves

    def _make_flow_field(self, h, w, t, octaves, draft):
        """Строит dx, dy суммированием октав opensimplex noise3.

        Сэмплируется на грубой сетке через векторизованный на C `noise3array`,
        затем апсемплится линейной интерполяцией. z-координата растёт с `_t`,
        поэтому поле течёт непрерывно, а не пересоздаётся заново.
        """
        dx = np.zeros((h, w), dtype=np.float32)
        dy = np.zeros((h, w), dtype=np.float32)
        amp = 1.0
        scale = 16 if draft else 8
        # Два независимых z-канала, чтобы dx и dy не двигались синхронно.
        z_x = np.asarray([t * 0.05], dtype=np.float64)
        z_y = np.asarray([t * 0.05 + 17.3], dtype=np.float64)
        for _ in range(octaves):
            nh = max(2, h // scale)
            nw = max(2, w // scale)
            freq = 4.0 / max(1, scale // 4)
            xi = np.linspace(0.0, freq, nw, dtype=np.float64)
            yi = np.linspace(0.0, freq, nh, dtype=np.float64)
            nx = np.asarray(noise3array(xi, yi, z_x), dtype=np.float32)[0]
            ny = np.asarray(noise3array(xi, yi, z_y), dtype=np.float32)[0]
            nx_up = cv2.resize(nx, (w, h), interpolation=cv2.INTER_LINEAR)
            ny_up = cv2.resize(ny, (w, h), interpolation=cv2.INTER_LINEAR)
            dx += nx_up * amp
            dy += ny_up * amp
            scale = max(2, scale // 2)
            amp *= 0.55
        return dx, dy

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        dx, dy = self._make_flow_field(h, w, float(self._t),
                                        max(2, self.octaves), draft)
        for arr in (dx, dy):
            m = float(np.abs(arr).max()) + 1e-6
            arr /= m
        # Амплитуда пульсирует поверх текущего поля - даёт ощущение
        # "прилива" вместо постоянного по силе толчка.
        pulse = 1.0 + 0.3 * np.sin(self._t * 0.11)
        disp = intensity * 60.0 * pulse
        dx *= disp
        dy *= disp
        xs_g, ys_g = _grid(h, w)
        map_x = np.clip(xs_g + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys_g + dy, 0, h - 1).astype(np.float32)
        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        return _ensure_uint8(
            cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)
        )


class SelfDisplaceEffect(_Warpable):
    """RGB-каналы прошлого кадра используются как векторы смещения XY, с дыханием."""
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE, SegmentType.DROP,
                     SegmentType.BUILD, SegmentType.SUSTAIN]

    def __init__(self, depth=2, history_len=6, **kw):
        super().__init__(**kw)
        self.depth = depth
        self.history_len = history_len
        self._history = []

    def apply(self, frame, seg, draft):
        # История пополняется всегда, независимо от того, сработал ли эффект.
        self._history.append(frame.copy())
        if len(self._history) > self.history_len + 1:
            self._history.pop(0)
        return super().apply(frame, seg, draft)

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        n = len(self._history)

        def get_hist(age):
            idx = max(0, n - 1 - age)
            src = self._history[idx]
            if src.shape != frame.shape:
                return frame
            return src

        # Глубина и амплитуда дышат во времени, чтобы смещение продолжало
        # меняться даже на длинном статичном SUSTAIN-сегменте.
        breath = 0.7 + 0.3 * np.sin(self._t * 0.09)
        cross = 0.7 + 0.3 * np.cos(self._t * 0.05)
        dyn_depth = max(1, min(self.history_len,
                               self.depth + int(np.sin(self._t * 0.04) * 1.5)))

        d1 = get_hist(dyn_depth).astype(np.float32)
        d2 = get_hist(min(dyn_depth * 2, n - 1)).astype(np.float32)
        dx = ((d1[:, :, 0] - 128.0) / 128.0) * intensity * 55.0 * breath
        dy = ((d1[:, :, 1] - 128.0) / 128.0) * intensity * 55.0 * cross
        dx += ((d2[:, :, 0] - 128.0) / 128.0) * intensity * 25.0 * cross
        dy += ((d2[:, :, 2] - 128.0) / 128.0) * intensity * 25.0 * breath
        xs_g, ys_g = _grid(h, w)
        map_x = np.clip(xs_g + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys_g + dy, 0, h - 1).astype(np.float32)
        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        displaced = cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)

        ghost_age = get_hist(1)
        ghost_map_x = np.clip(xs_g + dx * 0.3, 0, w - 1).astype(np.float32)
        ghost_map_y = np.clip(ys_g + dy * 0.3, 0, h - 1).astype(np.float32)
        ghost = cv2.remap(ghost_age, ghost_map_x, ghost_map_y,
                          interp, borderMode=cv2.BORDER_WRAP)
        blend = min(0.45, intensity * 0.4)
        result = cv2.addWeighted(displaced, 1.0 - blend, ghost, blend, 0)
        return _ensure_uint8(result)
