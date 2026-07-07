"""Core effects: Flash, GhostTrails, PixelSort, OpticalFlow, ASCII."""
from __future__ import annotations

import random
import cv2
import numpy as np

from vpc.analyzer import Segment, SegmentType
from .base import BaseEffect, _ensure_uint8


class FlashEffect(BaseEffect):
    trigger_types = [SegmentType.DROP, SegmentType.IMPACT]

    def _apply(self, frame, seg, draft):
        alpha = 0.6 + self.scaled_intensity(seg) * 0.4
        flash = np.full_like(frame, 255 if random.random() > 0.5 else 0)
        result = cv2.addWeighted(frame, 1.0 - alpha, flash, alpha, 0)
        return _ensure_uint8(result)


class GhostTrailsEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.last_frame = None

    def _apply(self, frame, seg, draft):
        alpha = self.scaled_intensity(seg)
        if self.last_frame is not None and self.last_frame.shape == frame.shape:
            result = cv2.addWeighted(frame, 1.0 - alpha, self.last_frame, alpha, 0)
        else:
            result = frame.copy()
        self.last_frame = frame.copy()
        return _ensure_uint8(result)


class PixelSortEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.IMPACT, SegmentType.DROP]

    def __init__(self, sort_axis='luminance', sort_mode='block',
                 sort_direction='horizontal', sort_threshold=0.3, **kw):
        super().__init__(**kw)
        self.sort_axis = sort_axis
        self.sort_mode = sort_mode
        self.sort_direction = sort_direction
        self.sort_threshold = sort_threshold

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)

        # Determine strip/band size based on direction
        if self.sort_direction == 'vertical':
            strip_size = max(1, int(w * (0.05 + intensity * 0.4)))
        else:
            strip_size = max(1, int(h * (0.05 + intensity * 0.4)))

        n_strips = 1 if draft else max(1, int(intensity * 8))

        if self.sort_mode == 'block':
            # --- 1. BLOCK MODE: The Original Global Flattening (1st Commit) ---
            for _ in range(n_strips):
                if self.sort_direction == 'vertical':
                    x = random.randint(0, max(0, w - strip_size))
                    crop = result[:, x:x + strip_size]
                    ch, cw = crop.shape[:2]
                    pixels = crop.reshape(-1, 3)
                    
                    if self.sort_axis == 'hue':
                        key_pixels = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)[..., 0].flatten()
                    elif self.sort_axis == 'saturation':
                        key_pixels = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)[..., 1].flatten()
                    else:  # luminance
                        key_pixels = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).flatten()
                    
                    order = np.argsort(key_pixels)
                    result[:, x:x + strip_size] = pixels[order].reshape(ch, cw, 3)
                else:
                    y = random.randint(0, max(0, h - strip_size))
                    crop = result[y:y + strip_size]
                    ch, cw = crop.shape[:2]
                    pixels = crop.reshape(-1, 3)
                    
                    if self.sort_axis == 'hue':
                        key_pixels = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)[..., 0].flatten()
                    elif self.sort_axis == 'saturation':
                        key_pixels = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)[..., 1].flatten()
                    else:  # luminance
                        key_pixels = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).flatten()
                    
                    order = np.argsort(key_pixels)
                    result[y:y + strip_size] = pixels[order].reshape(ch, cw, 3)

        elif self.sort_mode == 'streaks':
            # --- 2. STREAKS MODE: After Effects / Threshold sorting ---
            threshold_val = self.sort_threshold * 255.0
            for _ in range(n_strips):
                if self.sort_direction == 'vertical':
                    x_start = random.randint(0, max(0, w - strip_size))
                    for col_idx in range(x_start, min(w, x_start + strip_size)):
                        col_pixels = result[:, col_idx]
                        
                        if self.sort_axis == 'hue':
                            col_keys = cv2.cvtColor(col_pixels[:, np.newaxis, :], cv2.COLOR_RGB2HSV)[:, 0, 0]
                        elif self.sort_axis == 'saturation':
                            col_keys = cv2.cvtColor(col_pixels[:, np.newaxis, :], cv2.COLOR_RGB2HSV)[:, 0, 1]
                        else:  # luminance
                            col_keys = cv2.cvtColor(col_pixels[:, np.newaxis, :], cv2.COLOR_RGB2GRAY)[:, 0]
                        
                        mask = col_keys >= threshold_val
                        in_segment = False
                        start_idx = 0
                        for y_idx in range(h):
                            if mask[y_idx]:
                                if not in_segment:
                                    start_idx = y_idx
                                    in_segment = True
                            else:
                                if in_segment:
                                    seg_keys = col_keys[start_idx:y_idx]
                                    sort_order = np.argsort(seg_keys)
                                    result[start_idx:y_idx, col_idx] = col_pixels[start_idx:y_idx][sort_order]
                                    in_segment = False
                        if in_segment:
                            seg_keys = col_keys[start_idx:h]
                            sort_order = np.argsort(seg_keys)
                            result[start_idx:h, col_idx] = col_pixels[start_idx:h][sort_order]
                else:
                    y_start = random.randint(0, max(0, h - strip_size))
                    for row_idx in range(y_start, min(h, y_start + strip_size)):
                        row_pixels = result[row_idx, :]
                        
                        if self.sort_axis == 'hue':
                            row_keys = cv2.cvtColor(row_pixels[np.newaxis, :, :], cv2.COLOR_RGB2HSV)[0, :, 0]
                        elif self.sort_axis == 'saturation':
                            row_keys = cv2.cvtColor(row_pixels[np.newaxis, :, :], cv2.COLOR_RGB2HSV)[0, :, 1]
                        else:  # luminance
                            row_keys = cv2.cvtColor(row_pixels[np.newaxis, :, :], cv2.COLOR_RGB2GRAY)[0]
                        
                        mask = row_keys >= threshold_val
                        in_segment = False
                        start_idx = 0
                        for x_idx in range(w):
                            if mask[x_idx]:
                                if not in_segment:
                                    start_idx = x_idx
                                    in_segment = True
                            else:
                                if in_segment:
                                    seg_keys = row_keys[start_idx:x_idx]
                                    sort_order = np.argsort(seg_keys)
                                    result[row_idx, start_idx:x_idx] = row_pixels[start_idx:x_idx][sort_order]
                                    in_segment = False
                        if in_segment:
                            seg_keys = row_keys[start_idx:w]
                            sort_order = np.argsort(seg_keys)
                            result[row_idx, start_idx:w] = row_pixels[start_idx:w][sort_order]

        else:
            # --- 3. COLUMNS MODE: The 0.1.0 column-shifting ("rewind") look ---
            for _ in range(n_strips):
                if self.sort_direction == 'vertical':
                    x = random.randint(0, max(0, w - strip_size))
                    strip = result[:, x:x + strip_size]
                    if self.sort_axis == 'hue':
                        key_strip = cv2.cvtColor(strip, cv2.COLOR_RGB2HSV)[..., 0]
                        row_means = key_strip.mean(axis=1)
                    elif self.sort_axis == 'saturation':
                        key_strip = cv2.cvtColor(strip, cv2.COLOR_RGB2HSV)[..., 1]
                        row_means = key_strip.mean(axis=1)
                    else:  # luminance
                        gray = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY)
                        row_means = gray.mean(axis=1)
                    order = np.argsort(row_means)
                    result[:, x:x + strip_size] = strip[order, :]
                else:
                    y = random.randint(0, max(0, h - strip_size))
                    strip = result[y:y + strip_size]
                    if self.sort_axis == 'hue':
                        key_strip = cv2.cvtColor(strip, cv2.COLOR_RGB2HSV)[..., 0]
                        col_means = key_strip.mean(axis=0)
                    elif self.sort_axis == 'saturation':
                        key_strip = cv2.cvtColor(strip, cv2.COLOR_RGB2HSV)[..., 1]
                        col_means = key_strip.mean(axis=0)
                    else:  # luminance
                        gray = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY)
                        col_means = gray.mean(axis=0)
                    order = np.argsort(col_means)
                    result[y:y + strip_size] = strip[:, order]

        return _ensure_uint8(result)


class OpticalFlowEffect(BaseEffect):
    """Optical-flow-based motion-vector smear.

    Historically shipped under the name "Datamosh" (cfg keys fx_datamosh*
    are kept verbatim so old presets keep driving exactly this effect). For
    the same compatibility reason the engine still wires the legacy
    I-frame-drop source swap on top of this for NOISE segments in Final
    render mode. The real codec-level mosh lives in
    vpc.effects.mosh.TrueDatamoshEffect.
    """
    trigger_types = [SegmentType.NOISE, SegmentType.SUSTAIN,
                     SegmentType.IMPACT, SegmentType.DROP]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.prev_frame = None

    def apply(self, frame, seg, draft):
        # Always update prev_frame, even when effect doesn't fire.
        should_fire = (
            self.enabled and
            seg.type in self.trigger_types and
            random.random() <= self.chance
        )
        if self.prev_frame is None or self.prev_frame.shape != frame.shape:
            self.prev_frame = frame.copy()
            return frame
        if not should_fire:
            self.prev_frame = frame.copy()
            return frame
        return self._apply(frame, seg, draft)

    def _apply(self, frame, seg, draft):
        gray_cur = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        gray_prev = cv2.cvtColor(self.prev_frame, cv2.COLOR_RGB2GRAY)
        intensity = self.scaled_intensity(seg)
        flow_mul = 2.0 + intensity * 5.0

        try:
            preset = (cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST
                      if draft else cv2.DISOPTICAL_FLOW_PRESET_FAST)
            dis = cv2.DISOpticalFlow_create(preset)
            flow = dis.calc(gray_prev, gray_cur, None)
        except AttributeError:
            flow = cv2.calcOpticalFlowFarneback(
                gray_prev, gray_cur, None, 0.5, 2 if draft else 3, 15, 3, 5, 1.2, 0)

        h, w = frame.shape[:2]
        flow_scaled = flow * flow_mul
        map_x = np.float32(np.tile(np.arange(w), (h, 1)) + flow_scaled[..., 0])
        map_y = np.float32(np.tile(np.arange(h).reshape(-1, 1), (1, w)) + flow_scaled[..., 1])
        result = cv2.remap(self.prev_frame, map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REFLECT)
        self.prev_frame = frame.copy()
        return _ensure_uint8(result)


# Backward-compat alias: external code and old imports still refer to the
# optical-flow effect by its historical name.
DatamoshEffect = OpticalFlowEffect


class ASCIIEffect(BaseEffect):
    """Full-frame ASCII art conversion via PIL."""
    trigger_types = [SegmentType.SUSTAIN, SegmentType.SILENCE, SegmentType.BUILD]
    DEFAULT_CHARSET = '@#%S?*+;:,. '

    def __init__(self, char_size=10, charset=None,
                 fg_color=(0, 255, 0), bg_color=(0, 0, 0),
                 blend=0.0, color_mode='fixed', **kw):
        super().__init__(**kw)
        self.char_size = char_size
        self.charset = charset or self.DEFAULT_CHARSET
        self.fg_color = tuple(fg_color)
        self.bg_color = tuple(bg_color)
        self.blend = blend
        self.color_mode = color_mode
        self._pil_font = None
        self._font_size = None

    def _get_pil_font(self, size):
        if self._pil_font is not None and self._font_size == size:
            return self._pil_font
        from PIL import ImageFont
        candidates = [
            'cour.ttf', 'courbd.ttf',
            'DejaVuSansMono.ttf',
            'Menlo.ttc', 'Monaco.ttf',
            'LiberationMono-Regular.ttf',
        ]
        font = None
        for name in candidates:
            try:
                font = ImageFont.truetype(name, size)
                break
            except (OSError, IOError):
                continue
        if font is None:
            font = ImageFont.load_default()
        self._pil_font = font
        self._font_size = size
        return font

    def _apply(self, frame, seg, draft):
        from PIL import Image as PILImage, ImageDraw
        h, w = frame.shape[:2]
        cell_h = (self.char_size * 2) if draft else self.char_size
        cell_h = max(4, cell_h)
        cell_w = max(2, cell_h // 2)
        cols = max(1, w // cell_w)
        rows = max(1, h // cell_h)
        charset = self.charset
        n = len(charset)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        canvas = PILImage.new('RGB', (w, h), self.bg_color)
        draw = ImageDraw.Draw(canvas)
        font = self._get_pil_font(cell_h)

        for r in range(rows):
            for c in range(cols):
                y0 = r * cell_h
                x0 = c * cell_w
                y1 = min(y0 + cell_h, h)
                x1 = min(x0 + cell_w, w)
                brightness = int(gray[y0:y1, x0:x1].mean())
                char_idx = min(int(brightness / 256 * n), n - 1)
                ch = charset[char_idx]
                if ch == ' ':
                    continue
                if self.color_mode == 'original':
                    cell_rgb = frame[y0:y1, x0:x1]
                    color = (
                        int(cell_rgb[:, :, 0].mean()),
                        int(cell_rgb[:, :, 1].mean()),
                        int(cell_rgb[:, :, 2].mean()),
                    )
                elif self.color_mode == 'inverted':
                    cell_rgb = frame[y0:y1, x0:x1]
                    color = (
                        255 - int(cell_rgb[:, :, 0].mean()),
                        255 - int(cell_rgb[:, :, 1].mean()),
                        255 - int(cell_rgb[:, :, 2].mean()),
                    )
                else:
                    color = self.fg_color
                draw.text((x0, y0), ch, fill=color, font=font)

        out = np.array(canvas)
        if self.blend > 0:
            out = cv2.addWeighted(out, 1.0 - self.blend, frame, self.blend, 0)
        return _ensure_uint8(out)
