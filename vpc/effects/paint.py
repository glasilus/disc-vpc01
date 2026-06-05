"""Paint Canvas effect: applies a user-drawn binary mask to color overlay, frame lag, or warp."""
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
    """Decode base64 encoded PNG string to a 2D grayscale NumPy array."""
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
    """Applies a custom drawing as a mask for color overlays, frame delay (lag), or warp distortion."""

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
        # History queue must be updated on every single frame so that lag works continuously
        # regardless of trigger/chance gating.
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
        
        # Resize mask to frame size. Use NEAREST to preserve pixel boundaries of the drawing.
        mask_resized = cv2.resize(self.canvas_mask, (w, h), interpolation=cv2.INTER_NEAREST)
        
        # Binary mask: True where the drawing stroke is (black pixels in the canvas)
        is_stroke = mask_resized < 128

        self._t += 1
        intensity_factor = 0.2 + 0.8 * seg.intensity
        amp = self.warp_intensity * 25.0 * intensity_factor

        if self.mode == 'overlay':
            out = frame.copy()
            out[is_stroke] = [self.color_r, self.color_g, self.color_b]
            return out

        elif self.mode == 'lag':
            delayed_frame = self.history[0] if self.history else frame
            out = frame.copy()
            out[is_stroke] = delayed_frame[is_stroke]
            return out

        elif self.mode == 'warp_video':
            # Wave/liquid warp of the current frame, displayed INSIDE the drawing strokes!
            ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
            
            dx = np.sin(ys / 12.0 + self._t * 0.15) * amp
            dy = np.cos(xs / 12.0 + self._t * 0.15) * amp

            map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
            map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

            # Warp the current frame
            warped_frame = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)
            
            out = frame.copy()
            out[is_stroke] = warped_frame[is_stroke]
            return out

        elif self.mode == 'lag_warp':
            # Wobbly/warped strokes containing delayed video
            ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
            
            dx = np.sin(ys / 12.0 + self._t * 0.2) * amp
            dy = np.cos(xs / 12.0 + self._t * 0.2) * amp

            map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
            map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

            # Warp the mask
            warped_mask = cv2.remap(mask_resized, map_x, map_y, cv2.INTER_NEAREST, borderValue=255)
            is_warped_stroke = warped_mask < 128

            delayed_frame = self.history[0] if self.history else frame
            out = frame.copy()
            out[is_warped_stroke] = delayed_frame[is_warped_stroke]
            return out

        return frame
