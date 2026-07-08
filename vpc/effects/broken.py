"""Семейство "сломанный декодер": эффекты, имитирующие потерю данных
декодером, а не декоративный глитч-арт.

Состав:
  - SelfCannibalizeEffect: случайные прямоугольники кадра заполняются
    уменьшенной копией всего текущего кадра, из-за чего в картинке
    появляются миниатюрные копии самой себя не на своих местах. При
    высокой интенсивности миниатюры рекурсивно накладываются друг на друга.
  - VSyncRollEffect: вертикальная прокрутка с рваным швом - вид CRT,
    "потерявшего вертикальную синхронизацию".
  - PFrameLagEffect: стейтфул-перенос дельты движения между кадрами.
    Выглядит так, будто декодер слишком долго держит P-кадр, и движение
    смазывается прошлым кадром вместо чистого разрешения.
  - BitFlipEffect: сырые байты кадра ксорятся с разреженной маской, чьи
    установленные биты берутся из случайной битовой плоскости (LSB...MSB).
    Даёт вид настоящего "bit rot" - квантованные цветовые сдвиги на плато.
  - WrongMotionVectorEffect: ~10% макроблоков 16x16 копируют содержимое
    блока со смещением 32-64 px в том же кадре, как будто кодек потерял
    вектор движения и взял не тот референс.
"""
from __future__ import annotations

import random
import cv2
import numpy as np

from .base import BaseEffect, _ensure_uint8


class SelfCannibalizeEffect(BaseEffect):
    """Рекурсивное "самопоедание" кадра.

    Кадр уменьшается до миниатюры, затем 1..N случайных прямоугольников
    внутри того же кадра закрашиваются этой миниатюрой. При высокой
    интенсивности операция рекурсивна: миниатюра сама обрабатывается ещё
    раз перед вставкой, так что в каждом прямоугольнике оказывается копия
    поменьше, а в ней ещё меньше, и т.д.

    Похоже на утечку текстурного атласа / повреждение памяти - пиксели
    кадра всплывают там, где их быть не должно, и там же повторяется та
    же самоподобная структура, будто GPU читает из одного и того же
    указателя.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 max_depth: int = 3):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self.max_depth = max(1, min(4, int(max_depth)))

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]
        if h < 16 or w < 16:
            return frame

        # Число прямоугольников растёт с интенсивностью (1..8): на малых
        # значениях эффект едва заметен, на высоких заполняет весь кадр.
        n_rects = max(1, int(round(intensity * 8)))
        # Глубина рекурсии масштабируется отдельно, чтобы вложенность
        # была видна только со второй половины слайдера.
        depth = max(1, int(round(intensity * self.max_depth)))

        out = frame.copy()
        thumb = self._make_thumb(out, depth, draft)
        if thumb is None:
            return frame

        for _ in range(n_rects):
            self._paste_rect(out, thumb, intensity)
        return _ensure_uint8(out)

    def _make_thumb(self, frame: np.ndarray, depth: int,
                    draft: bool) -> np.ndarray:
        """Строит рекурсивную миниатюру для вставки.

        При depth=1 это просто уменьшенная копия. При depth>1 рекурсия:
        уменьшаем, затем закрашиваем пару прямоугольников внутри миниатюры
        ещё меньшей версией её самой перед финальным масштабированием.
        Именно эта вложенность даёт ощущение "бесконечности", а не просто
        картинки в картинке.
        """
        h, w = frame.shape[:2]
        scale = 0.35 if not draft else 0.5
        thumb_w = max(8, int(w * scale))
        thumb_h = max(8, int(h * scale))
        thumb = cv2.resize(frame, (thumb_w, thumb_h),
                           interpolation=cv2.INTER_AREA)

        if depth <= 1 or draft:
            return thumb

        for _ in range(2):
            inner = self._make_thumb(thumb, depth - 1, draft)
            if inner is None:
                continue
            ih, iw = inner.shape[:2]
            x = random.randint(0, max(0, thumb_w - iw))
            y = random.randint(0, max(0, thumb_h - ih))
            thumb[y:y + ih, x:x + iw] = inner
        return thumb

    def _paste_rect(self, frame: np.ndarray, thumb: np.ndarray,
                    intensity: float) -> None:
        """Вставляет `thumb` (или его масштабированный вариант) в случайный
        прямоугольник внутри `frame`. Мутирует frame на месте.
        """
        h, w = frame.shape[:2]
        th, tw = thumb.shape[:2]
        # Случайный доп. масштаб на каждую вставку, иначе все копии
        # одинаковы и выглядят как декорация, а не как порча данных.
        rscale = 0.4 + random.random() * 0.9
        ph = max(4, min(h, int(th * rscale)))
        pw = max(4, min(w, int(tw * rscale)))
        if ph >= h or pw >= w:
            return
        scaled = cv2.resize(thumb, (pw, ph), interpolation=cv2.INTER_AREA)
        x = random.randint(0, w - pw)
        y = random.randint(0, h - ph)
        # На низкой интенсивности - мягкий альфа-блендинг, чтобы не било
        # по глазам. От ~0.7 вставляем непрозрачно, для полного эффекта порчи.
        if intensity < 0.7:
            alpha = 0.55 + intensity * 0.5
            existing = frame[y:y + ph, x:x + pw].astype(np.float32)
            mixed = existing * (1.0 - alpha) + scaled.astype(np.float32) * alpha
            frame[y:y + ph, x:x + pw] = np.clip(mixed, 0, 255).astype(np.uint8)
        else:
            frame[y:y + ph, x:x + pw] = scaled


# ──────────────────────────────────────────────────────────────────────
#   B2 - VSyncRoll
# ──────────────────────────────────────────────────────────────────────


class VSyncRollEffect(BaseEffect):
    """Потеря вертикальной синхронизации: кадр режется по горизонтали,
    половины меняются местами, на стыке - рваный чёрный шов. Позиция
    разреза со временем смещается, поэтому шов ползёт по картинке -
    ровно как у старого CRT при потере vsync.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self._t = 0

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]
        self._t += 1

        # Строка разреза смещается на `intensity * 8` px за кадр, модуль
        # по h заворачивает её по кругу. Нижняя граница в 1 строку - на
        # случай h=0.
        speed = max(1, int(round(intensity * 8)))
        cut = (self._t * speed) % h
        if cut == 0:
            cut = 1
        out = np.vstack([frame[cut:], frame[:cut]])
        # Чёрная полоса шва в несколько строк, тоже плывёт. Толщина растёт
        # с интенсивностью: на низкой - чистая прокрутка, на высокой -
        # сильно "разбитый" сигнал.
        seam_h = max(1, int(intensity * 5))
        seam_y = h - cut
        if 0 <= seam_y < h:
            y0 = max(0, seam_y - seam_h // 2)
            y1 = min(h, seam_y + seam_h // 2 + 1)
            out[y0:y1] = 0
        return _ensure_uint8(out)


# ──────────────────────────────────────────────────────────────────────
#   B4 - PFrameLag
# ──────────────────────────────────────────────────────────────────────


class PFrameLagEffect(BaseEffect):
    """Стейтфул-перенос дельты движения. Храним предыдущий кадр и выдаём
    `prev + alpha * (current - prev)`, поэтому движение разрешается лишь
    ЧАСТИЧНО за кадр - ровно вид видеопотока, где декодер теряет P-кадры
    и картинка отстаёт.

    Чем выше интенсивность, тем меньше alpha и больше лаг (картинке
    нужно больше кадров, чтобы "догнать" настоящее).
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self._prev: np.ndarray | None = None

    def apply(self, frame, seg, draft):
        # apply() переопределён, чтобы буфер prev не устаревал на кадрах,
        # где эффект фактически не срабатывает (chance не выпал, не тот
        # сегмент, выключен). Иначе лаг накапливался бы поверх устаревшей
        # базы и давал заметный скачок при повторном включении.
        #
        # Проверка разбита на два случая: структурное отключение (эффект
        # выключен или сегмент не тот - тут даже float32-копию делать не
        # нужно) и промах по chance на отдельном кадре. Раньше это
        # определялось через identity-проверку `out is frame`, но она
        # хрупкая - BaseEffect.apply тоже возвращает `frame` при
        # исключении внутри _apply, так что нельзя было понять причину.
        if not self.enabled or seg.type not in self.trigger_types:
            # Эффект структурно выключен: сбрасываем prev, чтобы при
            # следующем включении лаг считался от актуального кадра,
            # а не от устаревшего состояния.
            self._prev = None
            return frame
        out = super().apply(frame, seg, draft)
        # Если _apply отработал, он сам обновил self._prev смешанным
        # результатом. Если chance не выпал или _apply упал с исключением,
        # prev остался нетронутым и накопился бы поверх устаревших данных
        # на следующем срабатывании. В обоих случаях подтягиваем prev к
        # текущему кадру, чтобы следующий успешный проход дал разумный
        # смаз, а не катастрофический.
        if out is frame:
            self._prev = frame.astype(np.float32)
        return out

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0 or self._prev is None or self._prev.shape != frame.shape:
            self._prev = frame.astype(np.float32)
            return frame
        # alpha в [0.15, 0.85]: чем выше интенсивность, тем меньше alpha
        # и сильнее лаг.
        alpha = 1.0 - (0.15 + intensity * 0.7)
        cur = frame.astype(np.float32)
        out = self._prev * (1.0 - alpha) + cur * alpha
        # prev обновляется СМЕШАННЫМ результатом, а не сырым текущим
        # кадром - именно так лаг накапливается от кадра к кадру, а не
        # сбрасывается.
        self._prev = out
        return out


# ──────────────────────────────────────────────────────────────────────
#   B8 - BitFlip
# ──────────────────────────────────────────────────────────────────────


class BitFlipEffect(BaseEffect):
    """Разреженная порча байтов через XOR - вид "bit rot".

    Генерируется булева маска плотности `intensity * 0.05`, выбирается
    битовая плоскость (LSB...MSB) в зависимости от интенсивности, и у
    каждого байта под маской переключается этот бит. В итоге картинка
    в основном цела, но на однотонных участках видны квантованные
    XOR-сдвиги - точь-в-точь как SD-карта с отказавшими ячейками флеша.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        # Плотность ограничена 5% байт - выше картинка перестаёт быть
        # узнаваемой, а верх слайдера остаётся интересным, не уходя в шум.
        density = intensity * 0.05
        # Семплируем uint8 (1 байт/ячейка) и сравниваем с порогом вместо
        # float64 (8 байт/ячейка) - на 1080p это 6 МБ на кадр вместо 50 МБ.
        thresh = max(0, min(255, int(density * 256)))
        mask = np.random.randint(0, 256, frame.shape, dtype=np.uint8) < thresh
        if not mask.any():
            return frame
        # Выбор битовой плоскости: низкая интенсивность тяготеет к LSB
        # (незаметно), высокая допускает MSB - катастрофичный сдвиг цвета.
        max_bit = max(0, min(7, int(round(intensity * 7))))
        bit_plane = random.randint(0, max_bit)
        flip_value = np.uint8(1 << bit_plane)
        out = frame.copy()
        out[mask] = out[mask] ^ flip_value
        return out


# ──────────────────────────────────────────────────────────────────────
#   B3-bis - WrongMotionVector
# ──────────────────────────────────────────────────────────────────────


class WrongMotionVectorEffect(BaseEffect):
    """Псевдо-MPEG с потерянными векторами движения.

    Случайная доля макроблоков 16x16 (зависит от интенсивности)
    перезаписывается содержимым ДРУГОЙ области 16x16 того же кадра,
    смещённой на 32-64 px. Выглядит ровно как поток H.264 с побитым
    полем векторов движения: куски изображения всплывают не там, где
    должны быть.
    """

    BLOCK = 16

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]
        bs = self.BLOCK
        if h < bs * 4 or w < bs * 4:
            return frame
        n_by = h // bs
        n_bx = w // bs
        # Доля затронутых блоков: 1%..30% сетки.
        n_total = n_by * n_bx
        n_corrupt = max(1, int(n_total * (0.01 + intensity * 0.29)))
        out = frame.copy()
        for _ in range(n_corrupt):
            by = random.randint(0, n_by - 1)
            bx = random.randint(0, n_bx - 1)
            y0 = by * bs
            x0 = bx * bs
            # Смещение источника: 32..64 px, знак случаен по каждой оси.
            mag = random.randint(32, 64)
            dy = random.choice((-1, 1)) * mag
            dx = random.choice((-1, 1)) * mag
            sy0 = y0 + dy
            sx0 = x0 + dx
            # Если блок-источник вылетает за пределы кадра, заворачиваем
            # по кругу - сам по себе такой wrap читается как глитч и
            # вписывается в образ "битой арифметики указателей". Modulo
            # в Python корректно обрабатывает отрицательные значения, но
            # верхнюю границу всё равно нужно клампить, чтобы блок
            # целиком помещался в кадр.
            sy0 = sy0 % (h - bs + 1) if h > bs else 0
            sx0 = sx0 % (w - bs + 1) if w > bs else 0
            out[y0:y0 + bs, x0:x0 + bs] = frame[sy0:sy0 + bs, sx0:sx0 + bs]
        return out
