"""Источник кадров: VideoPool поверх нескольких хендлов cv2.VideoCapture.

Наряду с видео поддерживаются неподвижные изображения: путь к картинке
оборачивается в `ImageCapture` (по интерфейсу совместим с cv2.VideoCapture),
так что она попадает в пул как "замороженный" клип. Определяется это
исключительно по расширению файла.
"""
from __future__ import annotations

import os
import random
from typing import List, Optional, Tuple

import cv2

from .image_source import ImageCapture

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.tif'}


def is_image(path: str) -> bool:
    """True, если `path` похож на изображение (по расширению, без учёта регистра)."""
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


class VideoPool:
    """Управляет несколькими хендлами VideoCapture; выбор случайный на каждый сегмент."""

    def __init__(self, paths: List[str]):
        if not paths:
            raise ValueError('VideoPool requires at least one path')
        self.paths = paths
        self.caps: List = []
        self.is_image_list: List[bool] = []
        self.fps_list: List[float] = []
        self.total_frames_list: List[int] = []
        self.durations: List[float] = []
        self.sizes: List[Tuple[int, int]] = []

        for path in paths:
            img = is_image(path)
            if img:
                cap = ImageCapture(path)     # бросает RuntimeError, если файл не читается
            else:
                cap = cv2.VideoCapture(path)
                if not cap.isOpened():
                    raise RuntimeError(f'Cannot open video: {path}')
            self.is_image_list.append(img)
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            self.caps.append(cap)
            self.fps_list.append(fps)
            self.total_frames_list.append(total)
            self.durations.append(total / fps if fps else 0.0)
            self.sizes.append((w, h))

        self.vid_fps = self.fps_list[0]
        self.vid_total_frames = self.total_frames_list[0]
        self.vid_duration = max(self.durations) if self.durations else 0.0
        self.primary_size = self.sizes[0] if self.sizes else (0, 0)

    def random_cap(self):
        i = random.randrange(len(self.caps))
        return self.caps[i], self.fps_list[i], self.total_frames_list[i], self.durations[i]

    def primary_cap(self):
        return self.caps[0], self.fps_list[0], self.total_frames_list[0], self.durations[0]

    def first_video_index(self) -> Optional[int]:
        """Индекс первого не-изображения в пуле, либо None, если пул состоит только из картинок.

        Используется passthrough: ему нужно соответствие 1:1 с настоящим видео
        (таймлайн + звук), а неподвижное изображение этого дать не может.
        """
        for i, img in enumerate(self.is_image_list):
            if not img:
                return i
        return None

    def release_all(self):
        for cap in self.caps:
            try:
                cap.release()
            except Exception:
                pass
