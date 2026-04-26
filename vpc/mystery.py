"""MysterySection — undocumented compound knobs.

DESIGN NOTE: This module is intentionally chaotic. The nine knobs (VESSEL,
ENTROPY_7, DELTA_OMEGA, STATIC_MIND, RESONANCE, COLLAPSE, ZERO, FLESH_K, DOT)
cross-multiply, drive each other's thresholds, and reach into private
internals of other effects. That is the design — a hidden artistic surface.

When changing this file, run `tests/test_mystery.py` (golden hashes against
fixed seeds). Any deliberate change in the visible output is a knob-redesign;
any unintended change is a regression.

Logic, formulas, magic numbers — verbatim from the original engine.
"""
from __future__ import annotations

import random
import cv2
import numpy as np

from .effects.base import _ensure_uint8, _reseg
from .effects.core import GhostTrailsEffect
from .effects.glitch import PixelDriftEffect, ColorBleedEffect
from .effects.degradation import ScanLinesEffect
from .effects.complex_fx import FeedbackLoopEffect
from .effects.signal import (
    ResonantRowsEffect, SpatialReverbEffect, FFTPhaseCorruptEffect,
    TemporalRGBEffect, HistoLagEffect, WaveshaperEffect,
    WrongSubsamplingEffect, DtypeReinterpretEffect, GameOfLifeEffect, ELAEffect,
)


class MysterySection:
    def __init__(self):
        self.VESSEL = 0.0
        self.ENTROPY_7 = 0.0
        self.DELTA_OMEGA = 0.0
        self.STATIC_MIND = 0.0
        self.RESONANCE = 0.0
        self.COLLAPSE = 0.0
        self.ZERO = 0.0
        self.FLESH_K = 0.0
        self.DOT = 0.0
        self._feedback = FeedbackLoopEffect(enabled=True, chance=1.0)
        self._ghost = GhostTrailsEffect(enabled=True, chance=1.0)
        self._scanlines = ScanLinesEffect(enabled=True, chance=1.0)
        self._colorbleed = ColorBleedEffect(enabled=True, chance=1.0)
        self._frame_buffer = []
        self._resonant = ResonantRowsEffect(enabled=True, chance=1.0)
        self._spatial_rev = SpatialReverbEffect(enabled=True, chance=1.0)
        self._fft_phase = FFTPhaseCorruptEffect(enabled=True, chance=1.0)
        self._temporal_rgb = TemporalRGBEffect(enabled=True, chance=1.0)
        self._histo_lag = HistoLagEffect(enabled=True, chance=1.0)
        self._waveshaper = WaveshaperEffect(enabled=True, chance=1.0)
        self._wrong_sub = WrongSubsamplingEffect(enabled=True, chance=1.0)
        self._dtype_cor = DtypeReinterpretEffect(enabled=True, chance=1.0)
        self._gameoflife = GameOfLifeEffect(enabled=True, chance=1.0)
        self._ela = ELAEffect(enabled=True, chance=1.0)

    def apply(self, frame, seg, draft):
        result = frame.copy()

        if self.ZERO > 0:
            random.seed(int(self.ZERO * 9999) ^ int(seg.rms * 1000))
            np.random.seed(random.randint(0, 2**31 - 1))

        _ve = self.VESSEL * self.ENTROPY_7
        _rc = self.RESONANCE * self.COLLAPSE
        _ds = self.DOT * self.STATIC_MIND
        _zf = self.ZERO * self.FLESH_K

        _rg = seg.rms * (1.0 + self.DELTA_OMEGA * 3.0)

        def _gate(base, rms_w=0.0, sign=1.0):
            return random.random() < min(1.0, max(0.0, base + sign * rms_w * _rg))

        # ── FLESH_K ──
        flesh_thr = 0.33 - _zf * 0.18 + self.ENTROPY_7 * 0.08
        if self.FLESH_K > flesh_thr and _gate(self.FLESH_K, rms_w=0.2, sign=-1.0):
            result = cv2.cvtColor(result, cv2.COLOR_RGB2YCrCb)
            if self.FLESH_K > 0.66 - _ve * 0.12:
                result = cv2.cvtColor(result, cv2.COLOR_YCrCb2RGB)
                result = cv2.cvtColor(result, cv2.COLOR_RGB2HSV)
                result = cv2.cvtColor(result, cv2.COLOR_HSV2RGB)
            else:
                result = cv2.cvtColor(result, cv2.COLOR_YCrCb2RGB)
            self._waveshaper.drive = 1.0 + self.FLESH_K * 7.0 + _rc * 4.0
            result = self._waveshaper._apply(result, _reseg(seg, self.FLESH_K), draft)
            self._wrong_sub.factor = 2 + int(self.FLESH_K * 6 + _ds * 4)
            result = self._wrong_sub._apply(result, _reseg(seg, self.FLESH_K), draft)

        # ── VESSEL ──
        ev = self.VESSEL * (1.0 + self.DOT * 0.6)
        if ev > 0 and _gate(ev, rms_w=0.1):
            self._feedback.intensity_max = ev
            self._ghost.intensity_max = max(0.0, 1.0 - ev + _ve * 0.4)
            result = self._feedback._apply(result, seg, draft)
            result = self._ghost._apply(result, seg, draft)
            self._histo_lag.lag_frames = max(2, int(ev * 60))
            result = self._histo_lag._apply(result, _reseg(seg, ev * 0.8), draft)
            split_thr = 0.5 - self.FLESH_K * 0.08
            if ev > split_thr:
                self._temporal_rgb.lag = max(1, int((ev - split_thr) * 35))
                result = self._temporal_rgb._apply(result, _reseg(seg, ev - split_thr), draft)

        # ── STATIC_MIND ──
        if self.STATIC_MIND > 0 and _gate(self.STATIC_MIND, rms_w=0.15):
            h_sm, w_sm = result.shape[:2]
            gray_sm = cv2.cvtColor(result[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32)
            gx = cv2.Sobel(gray_sm, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(gray_sm, cv2.CV_32F, 0, 1, ksize=3)
            max_g = max(float(np.abs(gx).max()), float(np.abs(gy).max()), 1.0)
            scale = self.STATIC_MIND * 32.0
            dx = (gx / max_g) * scale
            dy = (gy / max_g) * scale
            xm = np.tile(np.arange(w_sm, dtype=np.float32), (h_sm, 1))
            ym = np.tile(np.arange(h_sm, dtype=np.float32).reshape(-1, 1), (1, w_sm))
            x_src = np.clip(xm + dx, 0, w_sm - 1)
            y_src = np.clip(ym + dy, 0, h_sm - 1)
            interp = cv2.INTER_LINEAR if not draft else cv2.INTER_NEAREST
            result = cv2.remap(result, x_src, y_src, interp)
            ring_c = self.RESONANCE * self.STATIC_MIND
            if ring_c > 0.28 and _gate(ring_c):
                self._resonant.q = 5.0 + self.STATIC_MIND * 20.0
                self._resonant.cutoff = 0.015 + self.STATIC_MIND * 0.05
                result = self._resonant._apply(result, _reseg(seg, ring_c), draft)

        # ── RESONANCE ──
        if self.RESONANCE > 0 and _gate(self.RESONANCE, rms_w=0.5):
            self._resonant.q = 3.0 + self.RESONANCE * 27.0 + self.ENTROPY_7 * 14.0
            self._resonant.cutoff = 0.04 + self.RESONANCE * 0.12
            result = self._resonant._apply(result, _reseg(seg, self.RESONANCE), draft)
            self._spatial_rev.decay = self.RESONANCE * 0.4 + _ve * 0.15
            result = self._spatial_rev._apply(result, _reseg(seg, self.RESONANCE), draft)
            fft_thr = 0.7 - _rc * 0.35
            if self.RESONANCE > fft_thr:
                self._fft_phase.amount = (self.RESONANCE - fft_thr) * 3.0
                result = self._fft_phase._apply(result, _reseg(seg, self.RESONANCE - fft_thr), draft)

        # ── COLLAPSE ──
        if self.COLLAPSE > 0 and _gate(self.COLLAPSE, rms_w=0.2):
            h_c, w_c = result.shape[:2]
            n_sorted = int(h_c * self.COLLAPSE * 0.85)
            if n_sorted > 0:
                row_idx = np.random.choice(h_c, n_sorted, replace=False)
                out_c = result.copy()
                ascending = self.ZERO < 0.5
                for y in row_idx:
                    row = result[y].copy()
                    if not draft:
                        lum = (0.299 * row[:, 0].astype(float)
                               + 0.587 * row[:, 1]
                               + 0.114 * row[:, 2])
                        idx_s = np.argsort(lum)
                        out_c[y] = row[idx_s] if ascending else row[idx_s[::-1]]
                    else:
                        if random.random() < self.COLLAPSE:
                            out_c[y] = row[::-1]
                result = out_c
            fft_thr_c = 0.35 - _rc * 0.15
            if self.COLLAPSE > fft_thr_c and not draft:
                keep = max(0.08, 1.0 - (self.COLLAPSE - fft_thr_c) * 1.5 - _rc * 0.2)
                for c in range(min(3, result.shape[2])):
                    F = np.fft.fft2(result[:, :, c].astype(np.float32))
                    Fs = np.fft.fftshift(F)
                    cy2, cx2 = h_c // 2, w_c // 2
                    ry = max(1, int(cy2 * keep))
                    rx = max(1, int(cx2 * keep))
                    mask = np.zeros((h_c, w_c), dtype=np.float32)
                    mask[cy2 - ry:cy2 + ry, cx2 - rx:cx2 + rx] = 1.0
                    Fs *= mask
                    result[:, :, c] = np.clip(
                        np.abs(np.fft.ifft2(np.fft.ifftshift(Fs))), 0, 255
                    ).astype(np.uint8)

        # ── ENTROPY_7 ──
        if self.ENTROPY_7 > 0 and _gate(self.ENTROPY_7, rms_w=0.1):
            drift = PixelDriftEffect(enabled=True, chance=1.0)
            result = drift._apply(result, _reseg(seg, self.ENTROPY_7), draft)
            self._dtype_cor.amount = self.ENTROPY_7 * 0.2 + _ve * 0.1
            result = self._dtype_cor._apply(result, _reseg(seg, self.ENTROPY_7), draft)
            base_iter = max(1, int(self.ENTROPY_7 * 4))
            if _ve > 0.12:
                base_iter = max(base_iter, int(_ve * 12))
            self._gameoflife.iterations = base_iter
            self._gameoflife.corrupt_strength = int(30 + self.ENTROPY_7 * 80)
            result = self._gameoflife._apply(result, _reseg(seg, self.ENTROPY_7), draft)

        # ── ZERO ──
        if self.ZERO > 0 and _gate(self.ZERO - 0.05, rms_w=-0.05):
            self._ela.amplify = int(5 + self.ZERO * 20 + _zf * 20)
            self._ela.blend = max(0.0, 1.0 - self.ZERO * 1.4 - _zf * 0.3)
            result = self._ela._apply(result, _reseg(seg, self.ZERO), draft)

        # ── DOT ──
        if self.DOT > 0 and _gate(self.DOT, rms_w=0.3):
            depth = max(2, int(self.DOT * 6 + self.VESSEL * 4))
            self._frame_buffer.append(result.copy())
            if len(self._frame_buffer) > depth + 4:
                self._frame_buffer.pop(0)
            buf = self._frame_buffer
            if len(buf) >= 2:
                h_d, w_d = result.shape[:2]
                n = len(buf)
                col_to_frame = np.clip(
                    (np.arange(w_d) * (n - 1) / max(1, w_d - 1)).astype(int), 0, n - 1
                )
                out_dot = result.copy()
                for fi in range(n):
                    if buf[fi].shape != result.shape:
                        continue
                    cols = np.where(col_to_frame == fi)[0]
                    if len(cols) > 0:
                        out_dot[:, cols] = buf[fi][:, cols]
                w_slit = min(0.97, self.DOT * 1.1 + self.VESSEL * 0.15)
                result = cv2.addWeighted(out_dot, w_slit, result, 1.0 - w_slit, 0)

        random.seed()
        np.random.seed()
        return _ensure_uint8(result)

    def get_threshold_shift(self):
        return self.DELTA_OMEGA

    def get_remap_curve(self):
        return self.RESONANCE


# ── Knob metadata for GUI generation ────────────────────────────────────

MYSTERY_KNOBS = [
    ('VESSEL', 'mystery_VESSEL'),
    ('ENTROPY_7', 'mystery_ENTROPY_7'),
    ('dO THRESH', 'mystery_DELTA_OMEGA'),
    ('static.mind', 'mystery_STATIC_MIND'),
    ('__RESONANCE', 'mystery_RESONANCE'),
    ('COLLAPSE//', 'mystery_COLLAPSE'),
    ('000', 'mystery_ZERO'),
    ('FLESH_K', 'mystery_FLESH_K'),
    ('[  .  ]', 'mystery_DOT'),
]
