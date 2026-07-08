"""ImageCapture: статичное изображение, притворяющееся cv2.VideoCapture.

Render engine и VideoPool трогают источник только через узкий срез интерфейса
cv2.VideoCapture (get/set/read/grab/retrieve/isOpened/release). ImageCapture
реализует ровно этот срез поверх одной декодированной картинки, так что фото
может попасть в пул источников как замороженный "клип" без изменений движка.

Важное свойство: read()/grab() НИКОГДА не сообщают о конце потока - картинка
читается бесконечно, поэтому её "длительность" никогда не обрежет цикл рендера.
Заявленная длительность (fps / frame count) - небольшое номинальное значение,
нужное только чтобы `max(durations)` и UI не сходили с ума; на читаемость оно
не влияет.
"""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

# Номинальный "клип", которым представляется картинка. Чисто косметика -
# read()/grab() игнорируют позицию и никогда не отдают EOF, так что на
# рендер эти числа не влияют.
NOMINAL_FPS = 24.0
NOMINAL_DURATION = 3.0
NOMINAL_FRAME_COUNT = int(NOMINAL_FPS * NOMINAL_DURATION)  # 72


def imread_unicode(path: str) -> Optional[np.ndarray]:
    """Читает изображение в BGR-массив, независимо от юникода в пути.

    `cv2.imread` не открывает юникодные пути на Windows (использует ANSI file
    API), из-за чего ломается любое фото из-под кириллической домашней папки.
    Чтение байтов через numpy и декодирование в памяти обходит эту проблему.
    Возвращает None при любой ошибке чтения/декодирования.
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class ImageCapture:
    """Duck-typing cv2.VideoCapture поверх одной статичной картинки (BGR)."""

    def __init__(self, path: str):
        img = imread_unicode(path)
        if img is None:
            raise RuntimeError(f'Cannot open image: {path}')
        self._frame = img                    # BGR, как у VideoCapture.read()
        self._h, self._w = img.shape[:2]
        self._pos = 0
        self._opened = True

    # -- совместимый с VideoCapture интерфейс -----------------------------
    def isOpened(self) -> bool:
        return self._opened

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_FPS:
            return NOMINAL_FPS
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(NOMINAL_FRAME_COUNT)
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self._pos)
        return 0.0

    def set(self, prop: int, value: float) -> bool:
        # Позиция для статичной картинки не имеет смысла, но контракт нужно
        # соблюсти - иначе cap.set(POS_FRAMES, ...) в движке будет падать.
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self._pos = int(value)
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._opened:
            return False, None
        self._pos += 1
        return True, self._frame            # следующий cvtColor в движке сам сделает копию

    def grab(self) -> bool:
        if not self._opened:
            return False
        self._pos += 1
        return True

    def retrieve(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._opened:
            return False, None
        return True, self._frame

    def release(self) -> None:
        self._opened = False
        self._frame = None
