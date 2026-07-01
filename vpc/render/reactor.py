"""Render-time audio reactor: maps absolute time to a smoothed AudioSample."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from vpc.analyzer import AudioFeatures, AudioSample, N_BINS


class AudioReactor:
    """Stateful per-frame sampler. Call sample()/synth() in time order.

    attack: how fast a value rises toward a new peak (0..1, higher = snappier).
    release: how fast it falls (0..1, lower = longer peak-hold tails).
    """

    def __init__(self, features: Optional[AudioFeatures], fps: float,
                 attack: float = 0.6, release: float = 0.15):
        self.f = features
        self.fps = max(1.0, float(fps))
        self.attack = attack
        self.release = release
        self._bins = np.zeros(N_BINS, dtype=np.float32)
        self._scalars = {k: 0.0 for k in ('bass', 'mid', 'high')}
        self._prev_onset = 0.0

    def _smooth_scalar(self, key: str, target: float) -> float:
        cur = self._scalars[key]
        k = self.attack if target > cur else self.release
        cur += (target - cur) * k
        self._scalars[key] = cur
        return cur

    def _smooth_bins(self, target: np.ndarray) -> np.ndarray:
        rising = target > self._bins
        k = np.where(rising, self.attack, self.release).astype(np.float32)
        self._bins += (target - self._bins) * k
        return self._bins.copy()

    def sample(self, t: float) -> AudioSample:
        if self.f is None or len(self.f.times) == 0:
            return self.synth(0.0, int(round(t * self.fps)))
        ts = self.f.times
        bass = float(np.interp(t, ts, self.f.bass))
        mid = float(np.interp(t, ts, self.f.mid))
        high = float(np.interp(t, ts, self.f.high))
        onset = float(np.interp(t, ts, self.f.onset))
        idx = int(np.clip(np.searchsorted(ts, t), 0, self.f.bins.shape[0] - 1))
        raw_bins = self.f.bins[idx]
        beat = onset > 0.45 and onset > self._prev_onset
        self._prev_onset = onset
        return AudioSample(
            bass=self._smooth_scalar('bass', bass),
            mid=self._smooth_scalar('mid', mid),
            high=self._smooth_scalar('high', high),
            onset=onset, beat=beat,
            bins=self._smooth_bins(raw_bins.astype(np.float32)), t=t,
        )

    def synth(self, intensity: float, frame_idx: int) -> AudioSample:
        """No-audio fallback: animate from intensity + phase oscillators."""
        p = frame_idx / self.fps
        bass = intensity * (0.5 + 0.5 * math.sin(p * 2.0))
        mid = intensity * (0.5 + 0.5 * math.sin(p * 3.3 + 1.0))
        high = intensity * (0.5 + 0.5 * math.sin(p * 5.1 + 2.0))
        phases = np.linspace(0, math.pi * 2, N_BINS, endpoint=False)
        bins = (intensity * (0.5 + 0.5 * np.sin(phases + p * 4.0))).astype(np.float32)
        beat = (frame_idx % max(1, int(self.fps / 2))) == 0
        return AudioSample(
            bass=self._smooth_scalar('bass', bass),
            mid=self._smooth_scalar('mid', mid),
            high=self._smooth_scalar('high', high),
            onset=intensity, beat=beat,
            bins=self._smooth_bins(bins), t=p,
        )
