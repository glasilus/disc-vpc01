"""Signal-domain effects — DSP-style operations applied to image data."""
from __future__ import annotations

import cv2
import numpy as np

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8, _SCIPY_OK

if _SCIPY_OK:
    from scipy.signal import butter, sosfilt, fftconvolve


def _match_histograms(src: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Match src histogram to ref per channel using CDF interpolation."""
    result = np.empty_like(src)
    for c in range(3):
        s = src[:, :, c].flatten()
        r = ref[:, :, c].flatten()
        s_vals, s_idx, s_cnt = np.unique(s, return_inverse=True, return_counts=True)
        r_vals, r_cnt = np.unique(r, return_counts=True)
        s_cdf = np.cumsum(s_cnt).astype(np.float64); s_cdf /= s_cdf[-1]
        r_cdf = np.cumsum(r_cnt).astype(np.float64); r_cdf /= r_cdf[-1]
        mapped = np.interp(s_cdf, r_cdf, r_vals.astype(np.float64))
        result[:, :, c] = mapped[s_idx].reshape(src.shape[:2]).astype(np.uint8)
    return result


class ResonantRowsEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.DROP, SegmentType.IMPACT]

    def __init__(self, cutoff=0.08, q=12.0, **kw):
        super().__init__(**kw)
        self.cutoff = cutoff
        self.q = q

    def _apply(self, frame, seg, draft):
        if not _SCIPY_OK:
            return frame
        intensity = self.scaled_intensity(seg)
        freq = float(np.clip(self.cutoff, 0.01, 0.45))
        low = max(0.001, freq * 0.7)
        high = min(0.499, freq * 1.3)
        try:
            sos = butter(2, [low, high], btype='bandpass', fs=1.0, output='sos')
        except Exception:
            return frame
        result = frame.astype(np.float32)
        step = 2 if draft else 1
        scale = float(self.q) * 0.15 * intensity
        for c in range(3):
            for y in range(0, frame.shape[0], step):
                ringing = sosfilt(sos, result[y, :, c])
                result[y, :, c] += ringing * scale
                if draft and y + 1 < frame.shape[0]:
                    result[y + 1, :, c] = result[y, :, c]
        return _ensure_uint8(result)


class TemporalRGBEffect(BaseEffect):
    trigger_types = list(SegmentType)

    def __init__(self, lag=8, **kw):
        super().__init__(**kw)
        self.lag = lag
        self._history = []

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        lag_g = max(1, int(self.lag * intensity * 0.5))
        lag_b = max(1, int(self.lag * intensity))
        self._history.append(frame.copy())
        max_len = self.lag * 2 + 4
        if len(self._history) > max_len:
            self._history.pop(0)
        n = len(self._history)
        r = frame[:, :, 0]
        g_src = self._history[-min(lag_g + 1, n)]
        b_src = self._history[-min(lag_b + 1, n)]
        if g_src.shape != frame.shape:
            g_src = frame
        if b_src.shape != frame.shape:
            b_src = frame
        g = g_src[:, :, 1]
        b = b_src[:, :, 2]
        return np.stack([r, g, b], axis=2).astype(np.uint8)


class FFTPhaseCorruptEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.DROP,
                     SegmentType.IMPACT, SegmentType.BUILD]

    def __init__(self, amount=0.5, **kw):
        super().__init__(**kw)
        self.amount = amount

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        noise_scale = float(self.amount) * intensity * np.pi
        result = np.zeros_like(frame)
        if draft:
            small = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2))
            for c in range(3):
                ch = small[:, :, c].astype(np.float32)
                F = np.fft.rfft2(ch)
                phase_noise = np.random.uniform(-noise_scale, noise_scale, F.shape)
                F2 = np.abs(F) * np.exp(1j * (np.angle(F) + phase_noise))
                rec = np.clip(np.fft.irfft2(F2, s=ch.shape), 0, 255).astype(np.uint8)
                result[:, :, c] = cv2.resize(rec, (frame.shape[1], frame.shape[0]))
        else:
            for c in range(3):
                ch = frame[:, :, c].astype(np.float32)
                F = np.fft.rfft2(ch)
                phase_noise = np.random.uniform(-noise_scale, noise_scale, F.shape)
                F2 = np.abs(F) * np.exp(1j * (np.angle(F) + phase_noise))
                result[:, :, c] = np.clip(np.fft.irfft2(F2, s=ch.shape), 0, 255).astype(np.uint8)
        return result


class WaveshaperEffect(BaseEffect):
    trigger_types = list(SegmentType)

    def __init__(self, drive=3.0, **kw):
        super().__init__(**kw)
        self.drive = drive

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        d = float(1.0 + (self.drive - 1.0) * intensity)
        d = max(0.1, d)
        f = frame.astype(np.float32) / 127.5 - 1.0
        saturated = np.tanh(f * d) / np.tanh(min(d, 50.0))
        return _ensure_uint8((saturated + 1.0) * 127.5)


class HistoLagEffect(BaseEffect):
    trigger_types = list(SegmentType)

    def __init__(self, lag_frames=30, **kw):
        super().__init__(**kw)
        self.lag_frames = lag_frames
        self._history = []

    def _apply(self, frame, seg, draft):
        self._history.append(frame.copy())
        max_len = self.lag_frames + 2
        if len(self._history) > max_len:
            self._history.pop(0)
        ref = self._history[0]
        if ref.shape != frame.shape:
            return frame
        if draft:
            dw, dh = max(1, frame.shape[1] // 2), max(1, frame.shape[0] // 2)
            s = cv2.resize(frame, (dw, dh))
            r = cv2.resize(ref, (dw, dh))
            matched = _match_histograms(s, r)
            return cv2.resize(matched, (frame.shape[1], frame.shape[0]))
        return _match_histograms(frame, ref)


class WrongSubsamplingEffect(BaseEffect):
    trigger_types = list(SegmentType)

    def __init__(self, factor=4, **kw):
        super().__init__(**kw)
        self.factor = factor

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        factor = max(2, int(2 + (float(self.factor) - 2.0) * intensity))
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        h, w = yuv.shape[:2]
        y_ch, cr, cb = yuv[:, :, 0], yuv[:, :, 1], yuv[:, :, 2]
        sw, sh = max(1, w // factor), max(1, h // factor)
        cr_s = cv2.resize(cr, (sw, sh), interpolation=cv2.INTER_AREA)
        cb_s = cv2.resize(cb, (sw, sh), interpolation=cv2.INTER_AREA)
        cr_u = cv2.resize(cr_s, (w, h), interpolation=cv2.INTER_NEAREST)
        cb_u = cv2.resize(cb_s, (w, h), interpolation=cv2.INTER_NEAREST)
        yuv_out = cv2.merge([y_ch, cr_u, cb_u])
        return cv2.cvtColor(cv2.cvtColor(yuv_out, cv2.COLOR_YCrCb2BGR), cv2.COLOR_BGR2RGB)


class GameOfLifeEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.IMPACT, SegmentType.DROP]

    def __init__(self, iterations=2, corrupt_strength=60, **kw):
        super().__init__(**kw)
        self.iterations = iterations
        self.corrupt_strength = corrupt_strength

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        iters = max(1, int(self.iterations * intensity))
        strength = max(1, int(self.corrupt_strength * intensity))
        scale = 4 if draft else 2
        small = cv2.resize(frame, (max(1, frame.shape[1] // scale),
                                   max(1, frame.shape[0] // scale)))
        gray = (cv2.cvtColor(small, cv2.COLOR_RGB2GRAY) > 128).astype(np.uint8)
        for _ in range(iters):
            neighbors = sum(
                np.roll(np.roll(gray, dy, 0), dx, 1)
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dy != 0 or dx != 0)
            )
            gray = ((neighbors == 3) | ((gray == 1) & (neighbors == 2))).astype(np.uint8)
        mask = cv2.resize(gray.astype(np.uint8) * 255,
                          (frame.shape[1], frame.shape[0]),
                          interpolation=cv2.INTER_NEAREST)
        mask3 = (mask[:, :, np.newaxis] > 127)
        noise = np.random.randint(0, strength, frame.shape, dtype=np.uint8)
        return np.where(mask3, np.bitwise_xor(frame, noise), frame).astype(np.uint8)


class ELAEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD, SegmentType.NOISE]

    def __init__(self, quality=75, amplify=12, blend=0.5, **kw):
        super().__init__(**kw)
        self.quality = quality
        self.amplify = amplify
        self.blend = blend

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        amp = int(5 + self.amplify * intensity)
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        compressed = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if compressed is None:
            return frame
        diff = cv2.absdiff(bgr, compressed).astype(np.float32) * amp
        ela = cv2.cvtColor(np.clip(diff, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGB)
        alpha = float(np.clip(self.blend + (1.0 - self.blend) * (1.0 - intensity), 0.0, 1.0))
        return _ensure_uint8(cv2.addWeighted(frame.astype(np.float32), alpha,
                                              ela.astype(np.float32), 1.0 - alpha, 0))


class DtypeReinterpretEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def __init__(self, amount=0.05, **kw):
        super().__init__(**kw)
        self.amount = amount

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        scale = float(self.amount) * intensity
        raw = frame.tobytes()
        n = len(raw) // 2
        as_f16 = np.frombuffer(raw[:n * 2], dtype=np.float16).copy()
        finite = np.isfinite(as_f16)
        as_f16[finite] += (np.random.randn(int(finite.sum())) * scale).astype(np.float16)
        result_bytes = as_f16.tobytes()
        needed = frame.nbytes
        if len(result_bytes) < needed:
            result_bytes = result_bytes + b'\x00' * (needed - len(result_bytes))
        return np.frombuffer(result_bytes[:needed], dtype=np.uint8).reshape(frame.shape).copy()


class SpatialReverbEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD, SegmentType.DROP]

    def __init__(self, decay=0.15, reflections=6, **kw):
        super().__init__(**kw)
        self.decay = decay
        self.reflections = reflections

    def _apply(self, frame, seg, draft):
        if not _SCIPY_OK:
            return frame
        intensity = self.scaled_intensity(seg)
        decay = float(self.decay) * intensity
        ir_len = min(frame.shape[1] // 3, 256)
        if ir_len < 1:
            return frame
        ir = np.zeros(ir_len, dtype=np.float32)
        ir[0] = 1.0
        for k in range(1, self.reflections + 1):
            pos = min(int(ir_len * k / (self.reflections + 1)), ir_len - 1)
            ir[pos] = float((1.0 - decay) ** k) * decay * 2.0
        result = frame.astype(np.float32)
        step = 2 if draft else 1
        for c in range(3):
            for y in range(0, frame.shape[0], step):
                result[y, :, c] += fftconvolve(result[y, :, c], ir, mode='same') * intensity
                if draft and y + 1 < frame.shape[0]:
                    result[y + 1, :, c] = result[y, :, c]
        return _ensure_uint8(result)
