"""Симуляция VHS-кассеты - составной эффект.

Собирает вместе артефакты бытового VHS-плеера: яркость остаётся чёткой,
а цвет смазывается по горизонтали, зерно плёнки медленно колышет
яркость пятнами, низ кадра забит шумом переключения видеоголовок,
плюс лёгкий wow-and-flutter сдвигает картинку по горизонтали на
доли пикселя. По отдельности каждый слой ничего не решает, но вместе
они и дают узнаваемый "VHS"-вид.

Единый параметр `wear` (0..1, это стандартный слайдер интенсивности)
масштабирует все слои разом. Опциональный флаг `dust` добавляет редкие
царапины поверх всего остального.
"""
from __future__ import annotations

import random
import cv2
import numpy as np

from .base import BaseEffect, _ensure_uint8


class VHSTapeEffect(BaseEffect):
    """Составной VHS-look: смаз цвета + зерно + head-switch + wow + опциональная пыль.

    Интенсивность (через `scaled_intensity`) - это и есть `wear`:
    0 - чистый источник, 1 - сильная деградация от перезаписи.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 dust: bool = False):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self.dust = bool(dust)
        self._t = 0

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        wear = self.scaled_intensity(seg)
        if wear <= 0.0:
            return frame
        self._t += 1
        h, w = frame.shape[:2]

        out = frame
        out = self._chroma_smear(out, wear)
        out = self._wow_flutter(out, wear)
        out = self._tape_grain(out, wear)
        out = self._gen_loss(out, wear)
        out = self._head_switch(out, wear)
        if self.dust and wear > 0.15:
            out = self._dust(out, wear)
        return _ensure_uint8(out)

    # ------------------------------------------------------------------ layers
    def _chroma_smear(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Разделение Y/C + горизонтальный блюр только по цветности.

        У реальной VHS полоса пропускания цветовой поднесущей гораздо уже,
        чем у яркости, поэтому цвет плывёт по горизонтали, а края остаются
        относительно чёткими.
        """
        ycc = cv2.cvtColor(frame, cv2.COLOR_RGB2YCrCb)
        # Ширина ядра 3..21 px, растёт с wear, всегда нечётная.
        kw = int(3 + wear * 18) | 1
        cr = cv2.GaussianBlur(ycc[:, :, 1], (kw, 1), 0)
        cb = cv2.GaussianBlur(ycc[:, :, 2], (kw, 1), 0)
        ycc[:, :, 1] = cr
        ycc[:, :, 2] = cb
        return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB)

    def _wow_flutter(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Субпиксельное дрожание по горизонтали: медленная синусоида (wow)
        + быстрый случайный компонент (flutter). Амплитуда растёт с wear
        до ~3 px, так что картинка заметно "дышит" вбок, но не рвётся.
        """
        amp = wear * 3.0
        if amp < 0.2:
            return frame
        slow = np.sin(self._t * 0.08) * amp * 0.7
        fast = (random.random() - 0.5) * amp * 0.6
        dx = float(slow + fast)
        if abs(dx) < 0.05:
            return frame
        h, w = frame.shape[:2]
        M = np.float32([[1, 0, dx], [0, 1, 0]])
        return cv2.warpAffine(frame, M, (w, h),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)

    def _tape_grain(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Низкочастотный мультипликативный шум по яркости - имитирует
        неровное магнитное покрытие плёнки, из-за которого яркость
        медленно "плывёт" пятнами.
        """
        h, w = frame.shape[:2]
        # Генерируем шум в 1/8 разрешения и апсемплим билинейно, чтобы
        # пятна получились крупными и гладкими - это читается как
        # неровность плёнки, а не как поэлементный шум.
        nh, nw = max(2, h // 8), max(2, w // 8)
        noise = np.random.rand(nh, nw).astype(np.float32)
        noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_LINEAR)
        # Множитель в диапазоне [1 - amp, 1 + amp]; amp растёт вместе с wear.
        amp = 0.04 + wear * 0.12
        mult = 1.0 + (noise - 0.5) * 2.0 * amp
        out = frame.astype(np.float32) * mult[..., None]
        return out

    def _gen_loss(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Деградация от перезаписи: лёгкое сжатие контраста + слабый
        аддитивный шум. Каждая копия VHS теряет немного динамического
        диапазона и приобретает немного шума; оба эффекта масштабируются `wear`.
        """
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        # Чёрный подтягивается к 8, белый - к 240, по мере роста wear.
        lo = wear * 8.0
        hi = 255.0 - wear * 15.0
        frame = np.clip(frame, lo, hi)
        # Равномерный шум, sigma ~ wear * 6. Берём int8 напрямую
        # (1 байт на ячейку) вместо float64 из np.random.rand
        # (8 байт на ячейку) - на 1080p это 6 МБ/кадр вместо 50 МБ.
        noise_sigma = wear * 6.0
        if noise_sigma > 0.25:
            scale = noise_sigma / 127.0
            noise = np.random.randint(-127, 128, frame.shape,
                                      dtype=np.int8).astype(np.float32) * scale
            frame = frame + noise
        return frame

    def _head_switch(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Нижние 6..14 строк превращаются в шум переключения видеоголовок -
        та самая рваная/размытая/радужная полоса внизу кадра, характерная
        для любого бытового VHS-плеера.
        """
        h, w = frame.shape[:2]
        band = int(6 + wear * 8)
        if band <= 0 or h - band <= 0:
            return frame
        # Смесь хаотичного шума и горизонтального смаза строки прямо над
        # полосой - именно эта смесь даёт эффект "рваной картинки",
        # а не просто белый шум.
        smear_src = frame[h - band - 1].astype(np.float32)
        smear = np.tile(smear_src, (band, 1, 1))
        # Сдвигаем каждую строку на разную величину, имитируя разрыв.
        for i in range(band):
            roll = (i * 17 + self._t * 3) % w
            smear[i] = np.roll(smear[i], roll, axis=0)
        rainbow_noise = (np.random.rand(band, w, 3).astype(np.float32) * 255.0)
        mix = 0.55 * smear + 0.45 * rainbow_noise
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        frame[h - band:] = mix
        return frame

    def _dust(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Редкие вертикальные царапины шириной 1px, полупрозрачные тёмные -
        рисуются только когда включена галка dust и wear заметный.
        """
        h, w = frame.shape[:2]
        n = int(wear * 3) + 1
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        for _ in range(n):
            if random.random() > 0.35:
                continue
            x = random.randint(0, w - 1)
            alpha = 0.25 + random.random() * 0.35
            frame[:, x, :] = frame[:, x, :] * (1.0 - alpha)
        return frame
