"""Аудио-реактор рендер-времени: отображает абсолютное время в сглаженный AudioSample."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from vpc.analyzer import AudioFeatures, AudioSample, N_BINS


class AudioReactor:
    """Стейтфул-семплер по кадрам. sample()/synth() нужно звать в порядке времени.

    attack: как быстро значение поднимается к новому пику (0..1, больше = резче).
    release: как быстро оно спадает (0..1, меньше = длиннее хвост после пика).
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
        # Осциллограф: окно волны на один кадр. Длина = длительность кадра,
        # зажатая в 12..45 мс (короче - реактивнее, длиннее видно и бас).
        # Триггер ищет восходящий переход через ноль в окне поиска, чтобы
        # волна не ёрзала по фазе между кадрами, оставаясь честной по форме.
        self._wave_ok = (features is not None and getattr(features, 'y', None) is not None
                         and len(features.y) > 0)
        if self._wave_ok:
            sr = int(features.sr)
            # Окно берём с запасом (60 мс): осциллограф покажет его часть по
            # своему масштабу (зуму), поэтому здесь важна лишь верхняя граница.
            self._wave_win = max(2, int(0.060 * sr))
            self._wave_search = self._wave_win           # окно поиска триггера
            self._wave_peak = float(np.abs(features.y).max()) or 1.0

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

    def _wave_window(self, t: float) -> Optional[np.ndarray]:
        """Сырые сэмплы окна, начиная от ближайшего к t восходящего нуля.

        Возвращает нормированную к пику трека волну ~[-1,1] длиной _wave_win,
        либо None, если сырого аудио нет. Нормировка к пику трека даёт
        стабильную вертикальную шкалу: тихо - маленькая линия, громко - во
        весь экран, как на настоящем осциллографе.
        """
        if not self._wave_ok:
            return None
        y = self.f.y
        n = len(y)
        sr = int(self.f.sr)
        i0 = int(t * sr)
        # Триггер: восходящий переход через ноль, ближайший к i0.
        lo = max(0, i0 - self._wave_search)
        hi = min(n - 1, i0 + self._wave_search)
        start = i0
        if hi - lo > 2:
            seg = y[lo:hi]
            cross = np.nonzero((seg[:-1] <= 0.0) & (seg[1:] > 0.0))[0]
            if cross.size:
                centers = cross + lo
                start = int(centers[np.argmin(np.abs(centers - i0))])
        start = max(0, min(start, max(0, n - 1)))
        end = start + self._wave_win
        if end > n:
            w = np.pad(y[start:n], (0, end - n))
        else:
            w = y[start:end]
        return (w / self._wave_peak).astype(np.float32)

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
            wave=self._wave_window(t),
        )

    def synth(self, intensity: float, frame_idx: int) -> AudioSample:
        """Фолбэк без звука: анимация на основе intensity + фазовых осцилляторов."""
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
