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
from .effects.glitch import ColorBleedEffect
from .effects.degradation import ScanLinesEffect
from .effects.complex_fx import FeedbackLoopEffect
from .effects.signal import (
    ResonantRowsEffect, SpatialReverbEffect, FFTPhaseCorruptEffect,
    TemporalRGBEffect, HistoLagEffect, WaveshaperEffect,
    WrongSubsamplingEffect,
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
        # ── new state for redesigned VESSEL / ENTROPY_7 / ZERO / DOT ──
        # VESSEL maintains a slowly-decaying accumulator of past output —
        # "то, что вы отдали ему раньше, всё ещё присутствует". This same
        # accumulator is also the source ENTROPY_7 grows inside.
        self._vessel_accum: np.ndarray | None = None
        # ZERO needs a deep buffer (≈4 seconds at 30fps) to recall from.
        self._zero_history: list[np.ndarray] = []
        self._zero_history_max = 120
        # Internal phase counter for VESSEL's wandering polar centre.
        self._t = 0

    def apply(self, frame, seg, draft):
        result = frame.copy()
        self._t += 1
        # Always-on bookkeeping — the deep history feeding ZERO's recall and
        # VESSEL's accumulator is updated on every frame regardless of which
        # knobs are non-zero. Otherwise turning a knob on would suddenly find
        # an empty buffer and pop visually.
        self._zero_history.append(frame.copy())
        if len(self._zero_history) > self._zero_history_max:
            self._zero_history.pop(0)
        if (self._vessel_accum is None
                or self._vessel_accum.shape != frame.shape):
            self._vessel_accum = frame.astype(np.float32)

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

        # ── VESSEL ── polar morph + accumulating memory layer.
        # VESSEL "питает" — it carries an accumulator of past output that
        # grows heavier over time and gradually overpowers the present. The
        # accumulator is read through a polar transform whose centre wanders
        # on a Lissajous path so the memory layer rotates and unspools
        # underneath the live image. DOT cross-link: when DOT is non-zero
        # the polar centre splits — two offset polar morphs are blended,
        # the "три вещи уходят из одного момента" hinted in the docs.
        ev = self.VESSEL * (1.0 + self.DOT * 0.6)
        if ev > 0 and _gate(ev, rms_w=0.1):
            h_v, w_v = result.shape[:2]
            t = self._t * 0.05
            radius = float(np.hypot(w_v, h_v) * 0.5)

            def _polar_morph(img, cx, cy, twist):
                """Forward polar → angular shift (twist) → inverse polar."""
                lp = cv2.linearPolar(img, (cx, cy), radius,
                                     cv2.WARP_FILL_OUTLIERS)
                shift_px = int(round((twist / (2.0 * np.pi)) * lp.shape[0]))
                if shift_px:
                    lp = np.roll(lp, shift_px, axis=0)
                back = cv2.linearPolar(lp, (cx, cy), radius,
                                       cv2.WARP_INVERSE_MAP | cv2.WARP_FILL_OUTLIERS)
                return back

            cx0 = w_v * (0.5 + 0.18 * np.sin(t * 0.9))
            cy0 = h_v * (0.5 + 0.16 * np.sin(t * 1.1 + 0.4))
            twist0 = ev * 0.9 * np.sin(t * 0.7)
            morphed = _polar_morph(result, cx0, cy0, twist0)
            if self.DOT > 0.05:
                # Second offset polar morph mixed in — DOT-controlled split.
                cx1 = w_v * (0.5 - 0.18 * np.cos(t * 0.85) * self.DOT)
                cy1 = h_v * (0.5 + 0.18 * np.cos(t * 1.05) * self.DOT)
                twist1 = -ev * 0.7 * self.DOT
                morphed2 = _polar_morph(result, cx1, cy1, twist1)
                morphed = cv2.addWeighted(morphed, 1.0 - self.DOT * 0.5,
                                          morphed2, self.DOT * 0.5, 0)

            # Memory layer: the accumulator, also polar-morphed, blended in
            # at a weight that grows with VESSEL — high values let the past
            # outweigh the present.
            mem = _polar_morph(self._vessel_accum.astype(np.uint8),
                               cx0, cy0, twist0 * 1.2)
            past_w = min(0.85, ev * 0.9)
            result = cv2.addWeighted(morphed, 1.0 - past_w,
                                     mem, past_w, 0)

            # Update accumulator: heavier with VESSEL, plus a hue rotation
            # so re-seen frames don't congeal into a still average.
            decay = 1.0 - max(0.04, 0.18 - ev * 0.13)
            new_part = result.astype(np.float32) * (1.0 - decay)
            self._vessel_accum = self._vessel_accum * decay + new_part
            if ev > 0.4 and not draft:
                hsv = cv2.cvtColor(self._vessel_accum.clip(0, 255).astype(np.uint8),
                                   cv2.COLOR_RGB2HSV).astype(np.int16)
                hsv[:, :, 0] = (hsv[:, :, 0] + int(2 + ev * 6)) % 180
                self._vessel_accum = cv2.cvtColor(
                    hsv.clip(0, 255).astype(np.uint8), cv2.COLOR_HSV2RGB
                ).astype(np.float32)

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

        # ── ENTROPY_7 ── living quad-tree subdivision with conditional swaps.
        # Frame is recursively split into quadrants down to a depth driven by
        # ENTROPY_7. At each leaf the local luminance variance ("читает кадр
        # как условие") becomes the swap probability — busy regions tear,
        # flat regions hold. Sources of swap material are taken from VESSEL's
        # accumulator when VESSEL is active ("то, что накопил VESSEL,
        # ENTROPY_7 выращивает внутри") — otherwise from the live frame.
        if self.ENTROPY_7 > 0 and _gate(self.ENTROPY_7, rms_w=0.1):
            depth = max(1, int(self.ENTROPY_7 * 4) + (1 if _ve > 0.25 else 0))
            depth = min(5, depth)
            source_pool = result.copy()
            mem_pool = (self._vessel_accum.clip(0, 255).astype(np.uint8)
                        if _ve > 0.05 else None)
            out_e = result.copy()
            h_e, w_e = out_e.shape[:2]

            def _variance(block):
                if block.size == 0:
                    return 0.0
                g = block.mean(axis=2) if block.ndim == 3 else block
                return float(g.var())

            def _subdivide(y0, x0, y1, x1, d):
                if y1 - y0 < 4 or x1 - x0 < 4 or d == 0:
                    block = out_e[y0:y1, x0:x1]
                    var = _variance(block)
                    # Variance normalised to [0, 1] roughly via log curve;
                    # ENTROPY_7 globally scales the trigger probability.
                    p = self.ENTROPY_7 * min(1.0, var / 1500.0 + 0.2)
                    if random.random() < p:
                        op = random.random()
                        if op < 0.45:
                            # Swap with another random block of the same size.
                            sh, sw = y1 - y0, x1 - x0
                            sy = random.randint(0, max(0, h_e - sh))
                            sx = random.randint(0, max(0, w_e - sw))
                            pool = (mem_pool if mem_pool is not None and random.random() < min(0.7, _ve * 1.5)
                                    else source_pool)
                            out_e[y0:y1, x0:x1] = pool[sy:sy + sh, sx:sx + sw]
                        elif op < 0.75:
                            # 90° rotation — bigger structural rip.
                            sub = out_e[y0:y1, x0:x1]
                            if sub.shape[0] == sub.shape[1]:
                                out_e[y0:y1, x0:x1] = np.rot90(sub)
                            else:
                                out_e[y0:y1, x0:x1] = sub[::-1, ::-1]
                        else:
                            # Channel swizzle.
                            ch = list(range(out_e.shape[2]))
                            random.shuffle(ch)
                            out_e[y0:y1, x0:x1] = out_e[y0:y1, x0:x1][:, :, ch]
                    return
                my = (y0 + y1) // 2
                mx = (x0 + x1) // 2
                _subdivide(y0, x0, my, mx, d - 1)
                _subdivide(y0, mx, my, x1, d - 1)
                _subdivide(my, x0, y1, mx, d - 1)
                _subdivide(my, mx, y1, x1, d - 1)

            _subdivide(0, 0, h_e, w_e, depth)
            result = out_e

        # ── ZERO ── negative-time recall.
        # Pulls pixels back from a deep buffer, blends + XORs them through
        # a Bayer-style mask whose seed is shifted by ZERO ("те же значения,
        # другой ZERO — другой результат"). FLESH_K cross-link: when present,
        # the recall mask is biased toward edges of the live frame
        # ("FLESH_K усиливает то, что ZERO раскрывает"), so the recall
        # punches out exactly the contours instead of being a uniform XOR.
        if self.ZERO > 0 and _gate(self.ZERO - 0.05, rms_w=-0.05):
            n_hist = len(self._zero_history)
            lag = max(2, int(self.ZERO * (self._zero_history_max - 4)))
            lag = min(lag, n_hist - 1) if n_hist > 1 else 0
            if lag > 0:
                past = self._zero_history[n_hist - 1 - lag]
                if past.shape == result.shape:
                    h_z, w_z = result.shape[:2]
                    # Bayer mask, seeded by ZERO so different ZERO values
                    # produce different recall patterns at the same RMS.
                    seed = int(self.ZERO * 8191) ^ int(self.FLESH_K * 4093)
                    rng = np.random.RandomState(seed & 0x7FFFFFFF)
                    mask_small = (rng.rand(max(2, h_z // 16),
                                           max(2, w_z // 16))
                                  .astype(np.float32))
                    mask = cv2.resize(mask_small, (w_z, h_z),
                                      interpolation=cv2.INTER_LINEAR)
                    threshold = 1.0 - self.ZERO
                    if self.FLESH_K > 0:
                        # Bias the mask toward strong edges of the live frame.
                        gray = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY)
                        edges = cv2.Sobel(gray, cv2.CV_32F, 1, 1, ksize=3)
                        edge_w = np.abs(edges)
                        edge_w /= (edge_w.max() + 1e-6)
                        mask = mask * (1.0 - self.FLESH_K) + edge_w * self.FLESH_K
                        threshold = max(0.05, threshold - self.FLESH_K * 0.25)

                    sel = (mask > threshold)[..., None]
                    xor_amount = self.ZERO * (0.4 + _zf * 0.6)
                    if xor_amount > 0.05:
                        xored = cv2.bitwise_xor(result, past)
                        recalled = cv2.addWeighted(past, 1.0 - xor_amount,
                                                    xored, xor_amount, 0)
                    else:
                        recalled = past
                    result = np.where(sel, recalled, result)

        # ── DOT ── horizontal slit-scan, plus a vertical scan above 0.6.
        # Buffer length is "удлиняется при VESSEL" — VESSEL multiplies depth.
        # On high values the time grid becomes 2-D: every column reads from
        # one past frame, and inside that frame every row reads from yet
        # another past frame, so a single output pixel encodes two distinct
        # past times.
        if self.DOT > 0 and _gate(self.DOT, rms_w=0.3):
            depth = max(2, int(self.DOT * 6 + self.VESSEL * 8))
            self._frame_buffer.append(result.copy())
            if len(self._frame_buffer) > depth + 4:
                self._frame_buffer.pop(0)
            buf = self._frame_buffer
            if len(buf) >= 2:
                h_d, w_d = result.shape[:2]
                n = len(buf)
                # Horizontal slit-scan: column → frame index.
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
                # Vertical slit-scan compounded on top above the threshold,
                # turning the time grid 2-D.
                if self.DOT > 0.6:
                    row_to_frame = np.clip(
                        (np.arange(h_d) * (n - 1) / max(1, h_d - 1)).astype(int),
                        0, n - 1
                    )
                    # Phase-offset rows so they don't realign with columns.
                    row_to_frame = (row_to_frame + (n // 3)) % n
                    out_dot2 = out_dot.copy()
                    for fi in range(n):
                        if buf[fi].shape != result.shape:
                            continue
                        rows = np.where(row_to_frame == fi)[0]
                        if len(rows) > 0:
                            out_dot2[rows, :] = buf[fi][rows, :]
                    mix = min(1.0, (self.DOT - 0.6) * 2.5)
                    out_dot = cv2.addWeighted(out_dot, 1.0 - mix,
                                              out_dot2, mix, 0)
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
