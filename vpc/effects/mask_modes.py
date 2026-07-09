"""Общая логика режимов маски для Paint и Subtitles.

Обе фичи делают одно и то же: берут бинарную маску (штрих / буквы) и применяют
внутри неё один из режимов - залить цветом, показать задержанное видео, исказить
видео или исказить саму маску. Раньше это жило только в ``paint.py``; вынесено
сюда, чтобы субтитры дёргали ровно тот же код и поведение не расходилось.

Функция stateless: историю кадров (для lag) и счётчик фазы (для warp) хранит сам
эффект и передаёт готовыми. Соглашение о маске повторяет Paint: штрих - это
ТЁМНЫЕ пиксели (``mask_gray < 128``), фон - светлые.
"""
from __future__ import annotations

import cv2
import numpy as np


def apply_mask_mode(frame: np.ndarray, mask_gray: np.ndarray, mode: str, *,
                    delayed_frame: np.ndarray, color, amp: float,
                    t: int) -> np.ndarray:
    """Применяет ``mode`` внутри маски и возвращает новый кадр.

    Parameters
    ----------
    frame : HxWx3 uint8
        Текущий кадр (RGB).
    mask_gray : HxW uint8
        Маска, штрих = значения < 128 (чёрное на белом).
    mode : str
        'overlay' | 'lag' | 'warp_video' | 'lag_warp'.
    delayed_frame : HxWx3 uint8
        Задержанный кадр для 'lag' / 'lag_warp' (эффект берёт из своей истории).
    color : (r, g, b)
        Цвет заливки для 'overlay'.
    amp : float
        Амплитуда волнового искажения (для warp-режимов).
    t : int
        Счётчик кадров эффекта - фаза синусоид искажения.
    """
    h, w = frame.shape[:2]
    is_stroke = mask_gray < 128
    # Нет ни одного пикселя маски - возвращаем кадр как есть (дёшево, частый
    # случай для субтитров вне активного окна).
    if not is_stroke.any():
        return frame

    if mode == 'overlay':
        result = frame.copy()
        result[is_stroke] = [int(color[0]), int(color[1]), int(color[2])]
        return result

    if mode == 'lag':
        result = frame.copy()
        result[is_stroke] = delayed_frame[is_stroke]
        return result

    if mode == 'warp_video':
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        dx = np.sin(ys / 12.0 + t * 0.15) * amp
        dy = np.cos(xs / 12.0 + t * 0.15) * amp
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)
        warped = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)
        result = frame.copy()
        result[is_stroke] = warped[is_stroke]
        return result

    if mode == 'lag_warp':
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        dx = np.sin(ys / 12.0 + t * 0.2) * amp
        dy = np.cos(xs / 12.0 + t * 0.2) * amp
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)
        warped_mask = cv2.remap(mask_gray, map_x, map_y, cv2.INTER_NEAREST,
                                borderValue=255)
        is_warped_stroke = warped_mask < 128
        result = frame.copy()
        result[is_warped_stroke] = delayed_frame[is_warped_stroke]
        return result

    return frame
