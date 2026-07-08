"""Эффекты деградации сигнала: CRT / VHS / JPEG / дизеринг."""
from __future__ import annotations

import random
import cv2
import numpy as np
from opensimplex import noise2

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


class ScanLinesEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        result = frame.astype(np.float32)
        intensity = self.scaled_intensity(seg)
        n = max(2, int(8 - intensity * 6))
        darkness = 0.3 + intensity * 0.5
        result[::n] = result[::n] * (1.0 - darkness)
        return _ensure_uint8(result)


class BitcrushEffect(BaseEffect):
    trigger_types = list(SegmentType)

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        bits = max(1, int(7 - intensity * 5))
        shift = 8 - bits
        return ((frame >> shift) << shift).astype(np.uint8)


class JPEGCrushEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        quality = max(1, int(40 - intensity * 38))
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        result = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
        return _ensure_uint8(result)


class FisheyeEffect(BaseEffect):
    trigger_types = [SegmentType.BUILD, SegmentType.SUSTAIN]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        strength = intensity * 0.8
        K = np.array([[w, 0, w / 2.0],
                      [0, w, h / 2.0],
                      [0, 0, 1.0]], dtype=np.float64)
        D = np.array([[strength], [strength * 0.3], [0.0], [0.0]], dtype=np.float64)
        try:
            map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                K, D, np.eye(3), K, (w, h), cv2.CV_32FC1)
            result = cv2.remap(frame, map1, map2, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)
        except cv2.error:
            cx, cy = w / 2.0, h / 2.0
            xs = np.linspace(-1, 1, w)
            ys = np.linspace(-1, 1, h)
            xg, yg = np.meshgrid(xs, ys)
            r2 = xg ** 2 + yg ** 2
            factor = 1.0 + strength * r2
            map_x = ((xg * factor + 1) / 2 * w).astype(np.float32)
            map_y = ((yg * factor + 1) / 2 * h).astype(np.float32)
            result = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)
        return _ensure_uint8(result)


class VHSTrackingEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        n_strips = max(1, int(intensity * 8))
        noise_amp = int(intensity * 20)
        strip_h = h // max(1, n_strips)
        for i in range(n_strips):
            y = random.randint(0, max(0, h - strip_h))
            shift = int(noise2(float(i) * 0.5, intensity * 50.0) * noise_amp)
            result[y:y + strip_h] = np.roll(result[y:y + strip_h], shift, axis=1)
            noise_val = np.clip(
                np.array([noise2(float(x) * 0.1, float(y) * 0.1) * noise_amp
                          for x in range(w)]),
                -30, 30).astype(np.int16)
            for row in range(y, min(y + strip_h, h)):
                result[row] = np.clip(
                    result[row].astype(np.int16) + noise_val.reshape(-1, 1), 0, 255
                ).astype(np.uint8)
        return result


class InterlaceEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.prev_frame = None

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        if self.prev_frame is not None and self.prev_frame.shape == frame.shape:
            result[1::2] = self.prev_frame[1::2]
        self.prev_frame = frame.copy()
        return self._blend_by_intensity(seg, result, frame)


class BadSignalEffect(BaseEffect):
    trigger_types = [SegmentType.DROP, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        n_bars = int(intensity * 5)
        for _ in range(n_bars):
            x = random.randint(0, w - 1)
            bw = random.randint(1, 4)
            val = random.randint(0, 255)
            result[:, x:min(x + bw, w)] = val
        n_shift = int(intensity * h * 0.1)
        for _ in range(n_shift):
            row = random.randint(0, h - 1)
            shift = random.randint(-20, 20)
            result[row] = np.roll(result[row], shift, axis=0)
        return result


class DitheringEffect(BaseEffect):
    trigger_types = [SegmentType.SILENCE, SegmentType.SUSTAIN]

    BAYER_4X4 = np.array([
        [0, 8, 2, 10],
        [12, 4, 14, 6],
        [3, 11, 1, 9],
        [15, 7, 13, 5]
    ], dtype=np.float32) / 16.0

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        levels = max(2, int(16 - intensity * 12))
        h, w = frame.shape[:2]
        tile_r = (h + 3) // 4
        tile_c = (w + 3) // 4
        bayer = np.tile(self.BAYER_4X4, (tile_r, tile_c))[:h, :w]
        bayer3 = np.stack([bayer] * 3, axis=-1)
        normalized = frame.astype(np.float32) / 255.0
        step = 1.0 / levels
        dithered = normalized + (bayer3 - 0.5) * step
        quantized = np.floor(dithered * levels) / levels
        return _ensure_uint8(quantized * 255.0)


class ZoomGlitchEffect(BaseEffect):
    """Анизотропное сжатие/растяжение по одной оси с плавным возвратом.

    По триггеру (IMPACT / DROP) запускается анимация: выбирается ось (X или
    Y), направление (сжатие до ~0.45x или растяжение до ~1.85x, случайно) и
    длительность в N кадров. Каждый следующий кадр интерполирует текущий
    масштаб обратно к 1.0 по ease-out кубической кривой, поэтому картинку
    резко дёргает на ударе и она упруго "садится" обратно. Пока анимация
    активна, эффект работает на каждом кадре независимо от типа сегмента -
    после завершения простаивает до следующего триггера.
    """
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP]

    def __init__(self, duration_frames=10, **kw):
        super().__init__(**kw)
        self.duration_frames = duration_frames
        self._active = False
        self._progress = 0
        self._total = 1
        self._axis = 'x'
        self._peak = 1.0   # пиковый масштаб по активной оси при progress=0

    def apply(self, frame, seg, draft):
        # Пока анимация идёт, применяем эффект на каждом кадре независимо
        # от гейтинга по триггерам. Иначе передаём управление в цепочку
        # гейтинга BaseEffect, которая может запустить новую анимацию.
        if not self.enabled:
            return frame
        if self._active:
            return self._step(frame, draft)
        return super().apply(frame, seg, draft)

    def _arm(self, intensity: float):
        self._active = True
        self._progress = 0
        self._total = max(3, int(self.duration_frames * (0.6 + intensity * 0.8)))
        self._axis = random.choice(('x', 'y'))
        # 50/50: растяжение (>1) или сжатие (<1). Величина растёт с
        # интенсивностью - громкие удары дёргают сильнее.
        if random.random() < 0.5:
            self._peak = 1.0 + 0.4 + intensity * 0.6   # 1.4 .. 2.0
        else:
            self._peak = 1.0 - (0.3 + intensity * 0.25)  # 0.45 .. 0.7

    def _apply(self, frame, seg, draft):
        # Сюда попадаем только на новом триггере - запускаем анимацию и
        # рендерим её первый кадр.
        self._arm(self.scaled_intensity(seg))
        return self._step(frame, draft)

    def _step(self, frame, draft):
        h, w = frame.shape[:2]
        # Ease-out кубическая кривая от peak обратно к 1.0 за `_total` кадров.
        u = min(1.0, self._progress / max(1, self._total - 1))
        ease = 1.0 - (1.0 - u) ** 3
        scale = self._peak + (1.0 - self._peak) * ease

        if self._axis == 'x':
            sx, sy = scale, 1.0
        else:
            sx, sy = 1.0, scale

        nw = max(2, int(round(w / sx)))
        nh = max(2, int(round(h / sy)))
        # При sx > 1 читаем из меньшей центральной области и растягиваем
        # (зум по этой оси); при sx < 1 читаем из большей области,
        # выходя за края через BORDER_REFLECT - получается сжатие.
        # cv2.warpAffine справляется с обоими случаями одной матрицей.
        cx, cy = w * 0.5, h * 0.5
        # Аффинное преобразование: центр остаётся на месте, масштаб (sx, sy).
        M = np.array([
            [sx, 0.0, cx * (1.0 - sx)],
            [0.0, sy, cy * (1.0 - sy)],
        ], dtype=np.float32)
        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        result = cv2.warpAffine(frame, M, (w, h),
                                flags=interp, borderMode=cv2.BORDER_REFLECT)

        self._progress += 1
        if self._progress >= self._total:
            self._active = False
            self._peak = 1.0
        return _ensure_uint8(result)


class SharpenEffect(BaseEffect):
    """Сильная нерезкая маска: frame + amount·(frame - blur(frame))."""
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP,
                     SegmentType.SUSTAIN, SegmentType.BUILD]

    def __init__(self, amount=1.5, radius=2.0, **kw):
        super().__init__(**kw)
        self.amount = amount
        self.radius = radius

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        # Итоговая сила зависит и от интенсивности звука, и от потолка
        # `amount`, заданного пользователем. На низкой интенсивности -
        # аккуратная чёткость, на пике - явный овершут с ореолами на краях.
        amt = float(self.amount) * (0.4 + intensity * 1.6)
        # Радиус ядра округляется до ближайшего нечётного числа >= 3.
        r = max(3, int(round(self.radius)) | 1)
        if draft:
            r = max(3, r // 2 | 1)
        blurred = cv2.GaussianBlur(frame, (r, r), 0)
        f32 = frame.astype(np.float32)
        out = f32 + amt * (f32 - blurred.astype(np.float32))
        return _ensure_uint8(out)
