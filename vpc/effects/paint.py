"""Paint Canvas: применяет нарисованную пользователем бинарную маску к цветовому оверлею, лагу кадров или искажению."""
from __future__ import annotations

import collections
import base64
import io
import cv2
import numpy as np
from PIL import Image

from vpc.analyzer import Segment, SegmentType
from .base import BaseEffect


def decode_paint_canvas(base64_str: str) -> np.ndarray | None:
    """Декодирует base64-строку PNG в 2D grayscale-массив NumPy."""
    if not base64_str or not isinstance(base64_str, str):
        return None
    try:
        if ',' in base64_str:
            base64_str = base64_str.split(',')[1]
        img_bytes = base64.b64decode(base64_str)
        img = Image.open(io.BytesIO(img_bytes)).convert('L')
        return np.array(img)
    except Exception as e:
        print(f"[PAINT] Failed to decode canvas: {e}")
        return None


class PaintCanvasEffect(BaseEffect):
    """Использует пользовательский рисунок как маску для цветовых оверлеев, задержки кадров (lag) или искажения (warp)."""

    trigger_types = [
        SegmentType.IMPACT, SegmentType.NOISE, SegmentType.DROP,
        SegmentType.SUSTAIN, SegmentType.BUILD, SegmentType.SILENCE
    ]

    def __init__(self, canvas_mask: np.ndarray | None = None, mode: str = 'lag',
                 delay_frames: int = 10, warp_intensity: float = 0.3,
                 color_r: int = 0, color_g: int = 255, color_b: int = 0, **kw):
        super().__init__(**kw)
        self.canvas_mask = canvas_mask
        self.mode = mode
        self.delay_frames = max(1, int(delay_frames))
        self.warp_intensity = warp_intensity
        self.color_r = color_r
        self.color_g = color_g
        self.color_b = color_b
        
        self.history: collections.deque[np.ndarray] | None = None
        self._t = 0

    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        # Очередь истории нужно обновлять на каждом кадре без исключений, иначе
        # lag будет прерываться из-за gating по trigger/chance.
        h, w = frame.shape[:2]
        if self.history is None or not self.history or self.history[0].shape != frame.shape:
            self.history = collections.deque(maxlen=self.delay_frames)
            for _ in range(self.delay_frames):
                self.history.append(frame.copy())
        else:
            if self.history.maxlen != self.delay_frames:
                new_hist = collections.deque(maxlen=self.delay_frames)
                new_hist.extend(self.history)
                self.history = new_hist
            self.history.append(frame.copy())

        return super().apply(frame, seg, draft)

    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        if self.canvas_mask is None:
            return frame

        h, w = frame.shape[:2]

        # NEAREST, а не линейная интерполяция - иначе края штриха размажутся.
        mask_resized = cv2.resize(self.canvas_mask, (w, h), interpolation=cv2.INTER_NEAREST)

        # Штрих - это чёрные пиксели на канвасе.
        is_stroke = mask_resized < 128

        self._t += 1
        intensity_factor = 0.2 + 0.8 * self.scaled_intensity(seg)
        amp = self.warp_intensity * 25.0 * intensity_factor

        if self.mode == 'overlay':
            result = frame.copy()
            result[is_stroke] = [self.color_r, self.color_g, self.color_b]

        elif self.mode == 'lag':
            delayed_frame = self.history[0] if self.history else frame
            result = frame.copy()
            result[is_stroke] = delayed_frame[is_stroke]

        elif self.mode == 'warp_video':
            # Волновое искажение текущего кадра, видно ТОЛЬКО внутри штрихов.
            ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)

            dx = np.sin(ys / 12.0 + self._t * 0.15) * amp
            dy = np.cos(xs / 12.0 + self._t * 0.15) * amp

            map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
            map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

            warped_frame = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)

            result = frame.copy()
            result[is_stroke] = warped_frame[is_stroke]

        elif self.mode == 'lag_warp':
            # Дрожащие/искажённые штрихи с задержанным видео внутри.
            ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)

            dx = np.sin(ys / 12.0 + self._t * 0.2) * amp
            dy = np.cos(xs / 12.0 + self._t * 0.2) * amp

            map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
            map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

            warped_mask = cv2.remap(mask_resized, map_x, map_y, cv2.INTER_NEAREST, borderValue=255)
            is_warped_stroke = warped_mask < 128

            delayed_frame = self.history[0] if self.history else frame
            result = frame.copy()
            result[is_warped_stroke] = delayed_frame[is_warped_stroke]

        else:
            result = frame

        return self._blend_by_intensity(seg, result, frame)
