"""Alchemy: a WMP-style feedback "liquid light" visualizer.

The classic Windows Media Player *Alchemy* look is a video-feedback field —
each frame the previous image is rotated a little, zoomed a little and dimmed,
then fresh audio-reactive geometry is drawn on top. The compounding
rotate+zoom turns every stroke into an endless glowing spiral tunnel, and a
slowly cycling hue keeps the palette morphing. Here the fresh geometry is a
radially symmetric "audio rose" whose petal radii track the spectrum, so the
tunnel pulses and blooms with the music.

It fits the same render→composite contract as every other visualizer: it
returns (visual_rgb, field_gray) and honours the shared Composite Mode, so it
can replace the frame, blend over it, or warp/mask the source.
"""
from __future__ import annotations

import cv2
import numpy as np

from .base import VisualizerEffect


class AlchemyEffect(VisualizerEffect):
    """Feedback spiral tunnel with a symmetric spectrum rose (WMP Alchemy)."""

    def __init__(self, symmetry=6, zoom=1.035, spin=2.0, **kw):
        super().__init__(**kw)
        self.symmetry = max(1, int(symmetry))
        self.zoom = float(zoom)
        self.spin = float(spin)
        self._acc = None      # HxWx3 float32 feedback buffer
        self._hue = 0.0
        self._phase = 0.0

    def _ensure(self, h, w):
        if self._acc is None or self._acc.shape[:2] != (h, w):
            self._acc = np.zeros((h, w, 3), np.float32)

    @staticmethod
    def _hsv(hue, val):
        rgb = cv2.cvtColor(np.uint8([[[int(hue) % 180, 255, 255]]]),
                           cv2.COLOR_HSV2RGB)[0, 0].astype(np.float32)
        return rgb * val

    def _render(self, h, w, sample):
        self._ensure(h, w)
        cx, cy = w / 2.0, h / 2.0

        # ── Feedback: rotate + zoom the previous frame by a small per-frame
        # delta and dim it. Compounded over time this is the infinite spiral
        # tunnel. Mids speed the spin, bass widens the zoom → it surges on hits.
        delta = self.spin * (0.4 + 1.6 * float(np.clip(sample.mid, 0, 1)))
        z = self.zoom + 0.04 * float(np.clip(sample.bass, 0, 1))
        M = cv2.getRotationMatrix2D((cx, cy), delta, z)
        self._acc = cv2.warpAffine(self._acc, M, (w, h),
                                   flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        self._acc *= 0.89

        bins = np.asarray(sample.bins, np.float32)
        n = max(1, len(bins))
        level = float(np.clip(max(sample.bass, sample.mid, sample.high), 0.0, 1.0))
        self._hue = (self._hue + 1.5 + 8.0 * float(np.clip(sample.bass, 0, 1))) % 180.0
        self._phase += 0.04 + 0.12 * float(np.clip(sample.mid, 0, 1))

        # ── Fresh geometry: soft glowing emitters placed along the spectrum
        # and replicated with N-fold rotational + mirror symmetry (the
        # kaleidoscope). Each emitter is a filled disc; the whole overlay is
        # blurred so it reads as smoke, and the feedback rush smears every disc
        # into a spiral tendril. Per-emitter hue makes it richly multicoloured.
        overlay = np.zeros_like(self._acc)
        arms = self.symmetry
        base_r = min(cx, cy)
        K = 9
        for j in range(K):
            b = float(bins[int(j / K * n)])
            rr = base_r * (0.12 + 0.72 * b)
            blob = int(max(2.0, w * 0.018 * (0.6 + b)))
            col = self._hsv(self._hue + j * 10, 0.5 + 0.5 * level)
            col = (float(col[0]), float(col[1]), float(col[2]))
            for k in range(arms):
                ang = self._phase + 2.0 * np.pi * k / arms + j * 0.18
                x = int(cx + np.cos(ang) * rr)
                y = int(cy + np.sin(ang) * rr)
                cv2.circle(overlay, (x, y), blob, col, -1, cv2.LINE_AA)
                # Mirror across the arm axis → kaleidoscopic reflection.
                xm = int(cx + np.cos(-ang) * rr)
                ym = int(cy + np.sin(-ang) * rr)
                cv2.circle(overlay, (xm, ym), blob, col, -1, cv2.LINE_AA)

        overlay = cv2.GaussianBlur(overlay, (0, 0), max(1.5, w * 0.012))
        # Soft central pulse so the tunnel core glows instead of gaping black —
        # kept modest so it doesn't wash the coloured petals to flat white.
        core = self._hsv(self._hue + 40, 0.25 + 0.4 * level)
        cv2.circle(overlay, (int(cx), int(cy)), int(w * 0.02 * (0.5 + level)),
                   (float(core[0]), float(core[1]), float(core[2])), -1, cv2.LINE_AA)
        self._acc = np.clip(self._acc + overlay, 0, 255)

        vis = self._acc.astype(np.uint8)
        glow = cv2.GaussianBlur(vis, (0, 0), 4.0)
        vis = cv2.addWeighted(vis, 1.0, glow, 0.5, 0.0)
        return vis, cv2.cvtColor(vis, cv2.COLOR_RGB2GRAY)
