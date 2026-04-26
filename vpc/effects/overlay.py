"""Overlay compositing + ChromaKey utility."""
from __future__ import annotations

import os
import random
import cv2
import numpy as np

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


def _dominant_hue(img_rgb: np.ndarray, rank: int = 0) -> int:
    rgb3 = img_rgb[:, :, :3]
    hsv = cv2.cvtColor(rgb3, cv2.COLOR_RGB2HSV)
    h_ch = hsv[:, :, 0].flatten()
    s_ch = hsv[:, :, 1].flatten()
    saturated = h_ch[s_ch > 40]
    source = saturated if len(saturated) > 200 else h_ch
    bins, _ = np.histogram(source, bins=18, range=(0, 180))
    order = np.argsort(bins)[::-1]
    idx = int(order[rank]) if rank < len(order) else int(order[0])
    return idx * 10 + 5


class ChromaKeyEffect:
    """Standalone chroma-key utility used by OverlayEffect (not a BaseEffect)."""

    def __init__(self, key_color=(0, 255, 0), tolerance=30, edge_softness=5):
        self.key_color = key_color
        self.tolerance = tolerance
        self.edge_softness = edge_softness

    @classmethod
    def from_frame(cls, img_rgb: np.ndarray, rank: int = 0,
                   tolerance: int = 30, edge_softness: int = 5) -> 'ChromaKeyEffect':
        hue = _dominant_hue(img_rgb, rank)
        key_hsv = np.uint8([[[hue, 200, 200]]])
        key_rgb = cv2.cvtColor(key_hsv, cv2.COLOR_HSV2RGB)[0, 0]
        return cls(key_color=tuple(int(x) for x in key_rgb),
                   tolerance=tolerance, edge_softness=edge_softness)

    def get_mask(self, frame):
        hsv = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_RGB2HSV)
        key_hsv = cv2.cvtColor(
            np.uint8([[list(self.key_color)]]), cv2.COLOR_RGB2HSV)[0, 0]
        h_center = int(key_hsv[0])
        lower = np.array([max(0, h_center - self.tolerance), 40, 40], dtype=np.uint8)
        upper = np.array([min(179, h_center + self.tolerance), 255, 255], dtype=np.uint8)
        keyed = cv2.inRange(hsv, lower, upper)
        mask = 255 - keyed
        if self.edge_softness > 1:
            ks = self.edge_softness | 1
            mask = cv2.GaussianBlur(mask, (ks, ks), 0)
        return mask

    def apply_to_frame(self, frame, replacement=None):
        mask = self.get_mask(frame)
        mask3 = cv2.merge([mask, mask, mask]).astype(np.float32) / 255.0
        if replacement is None:
            replacement = np.zeros_like(frame)
        result = (frame.astype(np.float32) * mask3 +
                  replacement.astype(np.float32) * (1.0 - mask3))
        return _ensure_uint8(result)


def load_overlay_frames(folder: str):
    """Load all PNG/JPG/video images from folder as RGB numpy arrays."""
    from PIL import Image as PILImage
    img_exts = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
    vid_exts = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpg', '.mpeg'}
    frames = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return frames
    for name in entries:
        ext = os.path.splitext(name)[1].lower()
        path = os.path.join(folder, name)
        if ext in img_exts:
            try:
                img = PILImage.open(path).convert('RGB')
                frames.append(np.array(img))
            except Exception:
                pass
        elif ext in vid_exts:
            cap = None
            try:
                cap = cv2.VideoCapture(path)
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            except Exception:
                pass
            finally:
                if cap is not None:
                    cap.release()
    return frames


class OverlayEffect(BaseEffect):
    """Composite image overlays at scaled size and random/fixed position.

    Per-segment decision: show/hide, frame index, position and scale are
    decided ONCE per segment to match the original moviepy contract.
    """
    trigger_types = list(SegmentType)

    def __init__(self, overlay_frames=None, chroma_key=None,
                 chroma_mode='none', chroma_tolerance=30, chroma_softness=5,
                 opacity=0.85, blend_mode='screen',
                 scale=0.4, scale_min=0.15,
                 position='random', **kw):
        super().__init__(**kw)
        self.overlay_frames = overlay_frames or []
        self.chroma_key = chroma_key
        self.chroma_mode = chroma_mode
        self.chroma_tolerance = chroma_tolerance
        self.chroma_softness = chroma_softness
        self.opacity = opacity
        self.blend_mode = blend_mode
        self.scale = scale
        self.scale_min = scale_min
        self.position = position
        self._idx = 0
        self._corner = 0
        self._ck_cache = {}
        self._seg_t_start = -1.0
        self._seg_active = False
        self._seg_ov_idx = 0
        self._seg_x0 = 0
        self._seg_y0 = 0
        self._seg_tw = 0
        self._seg_th = 0

    def _blend(self, base_f, ov_f, alpha):
        if self.blend_mode == 'screen':
            blended = 255.0 - (255.0 - base_f) * (255.0 - ov_f) / 255.0
        elif self.blend_mode == 'multiply':
            blended = base_f * ov_f / 255.0
        else:
            blended = ov_f
        return base_f * (1.0 - alpha) + blended * alpha

    def apply(self, frame, seg, draft):
        if not self.enabled:
            return frame
        if seg.type not in self.trigger_types:
            return frame
        return self._apply(frame, seg, draft)

    def composite(self, base, overlay, opacity):
        bf = base.astype(np.float32)
        of = overlay.astype(np.float32)
        return _ensure_uint8(self._blend(bf, of, opacity))

    def _apply(self, frame, seg, draft):
        if not self.overlay_frames:
            return frame
        h, w = frame.shape[:2]

        if seg.t_start != self._seg_t_start:
            self._seg_t_start = seg.t_start
            self._seg_active = random.random() <= self.chance
            if self._seg_active:
                self._seg_ov_idx = self._idx % len(self.overlay_frames)
                self._idx += 1
                intensity = self.scaled_intensity(seg)
                cur_scale = self.scale_min + (self.scale - self.scale_min) * intensity
                cur_scale = max(0.05, min(1.0, cur_scale))
                ov_src = self.overlay_frames[self._seg_ov_idx]
                ov_h_src, ov_w_src = ov_src.shape[:2]
                th = max(4, int(h * cur_scale))
                tw = max(4, int(th * ov_w_src / max(ov_h_src, 1)))
                tw = min(tw, w); th = min(th, h)
                if self.position == 'center':
                    x0 = (w - tw) // 2; y0 = (h - th) // 2
                elif self.position == 'random_corner':
                    corners = [(0, 0), (w - tw, 0), (0, h - th), (w - tw, h - th)]
                    x0, y0 = corners[self._corner % 4]
                    self._corner += 1
                else:
                    x0 = random.randint(0, max(0, w - tw))
                    y0 = random.randint(0, max(0, h - th))
                self._seg_x0 = max(0, min(x0, w - tw))
                self._seg_y0 = max(0, min(y0, h - th))
                self._seg_tw = tw; self._seg_th = th

        if not self._seg_active:
            return frame

        tw, th = self._seg_tw, self._seg_th
        x0, y0 = self._seg_x0, self._seg_y0
        ov_src = self.overlay_frames[self._seg_ov_idx]
        interp = cv2.INTER_AREA if draft else cv2.INTER_LINEAR
        ov = cv2.resize(ov_src, (tw, th), interpolation=interp)
        intensity = self.scaled_intensity(seg)
        alpha = min(1.0, self.opacity * (0.4 + intensity * 0.6))
        result = frame.copy()
        roi = result[y0:y0 + th, x0:x0 + tw].astype(np.float32)
        ov_f = ov[:, :, :3].astype(np.float32)

        ck = None
        if self.chroma_mode == 'dominant':
            ck = self._ck_cache.get(self._seg_ov_idx)
            if ck is None:
                ck = ChromaKeyEffect.from_frame(ov_src, rank=0,
                                                tolerance=self.chroma_tolerance,
                                                edge_softness=self.chroma_softness)
                self._ck_cache[self._seg_ov_idx] = ck
        elif self.chroma_mode == 'secondary':
            ck = self._ck_cache.get(self._seg_ov_idx)
            if ck is None:
                ck = ChromaKeyEffect.from_frame(ov_src, rank=1,
                                                tolerance=self.chroma_tolerance,
                                                edge_softness=self.chroma_softness)
                self._ck_cache[self._seg_ov_idx] = ck
        elif self.chroma_mode == 'manual' and self.chroma_key is not None:
            ck = self.chroma_key

        if ck is not None:
            mask_src = ck.get_mask(ov_src)
            mask = cv2.resize(mask_src, (tw, th), interpolation=interp)
            per_pixel_alpha = (mask.astype(np.float32) / 255.0) * alpha
            ppa = per_pixel_alpha[:, :, np.newaxis]
            blended = self._blend(roi, ov_f, 1.0)
            blended_roi = _ensure_uint8(roi * (1.0 - ppa) + blended * ppa)
        else:
            blended_roi = _ensure_uint8(self._blend(roi, ov_f, alpha))

        result[y0:y0 + th, x0:x0 + tw] = blended_roi
        return result
