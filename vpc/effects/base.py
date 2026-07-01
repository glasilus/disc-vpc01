"""Base classes and shared utilities for all effects."""
from __future__ import annotations

from abc import ABC, abstractmethod
import random
from typing import List

import cv2
import numpy as np

from vpc.analyzer import Segment, SegmentType


class BaseEffect(ABC):
    """Common contract for every effect.

    Subclass MUST implement `_apply(frame, seg, draft)`. The default `apply()`
    runs the gating chain (enabled → trigger_types → chance roll) and dispatches
    to `_apply` only on success. Stateful effects (Datamosh, DerivWarp,
    SelfDisplace) override `apply()` to keep their history in sync regardless
    of whether the effect actually fires this frame.
    """
    trigger_types: List[SegmentType] = list(SegmentType)

    # ── audio-reactivity knobs (opt-in, set generically by build_chain) ──
    # audio_drive: which per-frame band replaces seg.intensity in
    #   scaled_intensity — 'segment' (default, today's behaviour), 'auto'
    #   (loudest of bass/mid/high, so no track is ever dead), or a band name.
    # beat_gate: gate apply() on a per-frame beat/onset INSIDE a segment —
    #   'off' (default), 'beat', or 'onset'.
    # react: opt-in flag for effects with bespoke seg.live wiring.
    audio_drive: str = 'segment'
    beat_gate: str = 'off'
    react: bool = False

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        self.enabled = enabled
        self.chance = chance
        self.intensity_min = intensity_min
        self.intensity_max = intensity_max

    def _beat_pass(self, seg: Segment) -> bool:
        """True if the per-frame beat gate lets this frame through.

        Missing seg.live (no audio / still preview) always passes — an
        enabled effect must never be silently muted on the no-audio path.
        """
        live = getattr(seg, 'live', None)
        if live is None:
            return True
        if self.beat_gate == 'beat':
            return bool(getattr(live, 'beat', True))
        if self.beat_gate == 'onset':
            return float(getattr(live, 'onset', 1.0)) > 0.45
        return True

    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        if not self.enabled:
            return frame
        if seg.type not in self.trigger_types:
            return frame
        if random.random() > self.chance:
            return frame
        if self.beat_gate != 'off' and not self._beat_pass(seg):
            return frame
        try:
            out = self._apply(frame, seg, draft)
        except (MemoryError, ValueError, cv2.error) as e:  # type: ignore[name-defined]
            # Don't kill the whole render pipeline on a transient effect
            # failure — most often a numpy/cv2 OOM under heavy always-on
            # combos, or an out-of-range remap on degenerate input.
            self._fail_count = getattr(self, '_fail_count', 0) + 1
            if self._fail_count <= 3 or self._fail_count % 250 == 0:
                print(f'[FX-FAIL] {type(self).__name__}: {e!r} '
                      f'(suppressed={self._fail_count})')
            if self._fail_count > 50:
                self.enabled = False
                print(f'[FX-FAIL] {type(self).__name__} disabled '
                      f'after {self._fail_count} failures.')
            return frame
        # Sanity: NaN/Inf in float intermediates → cv2.remap can crash later
        # if the value silently propagates into a follow-up effect.
        if out is None:
            return frame
        if out.dtype != np.uint8:
            out = _ensure_uint8(out)
        return out

    def _driven_value(self, seg: Segment) -> float:
        """The 0..1 value that drives intensity — segment loudness by default,
        or a per-frame audio band when audio_drive is set. Falls back to
        seg.intensity whenever the live sample is absent, so a band-driven
        effect is never dead on the no-audio path."""
        drive = self.audio_drive
        if drive == 'segment':
            return seg.intensity
        live = getattr(seg, 'live', None)
        if live is None:
            return seg.intensity
        if drive == 'auto':
            return max(live.bass, live.mid, live.high)
        return float(getattr(live, drive, seg.intensity))

    def scaled_intensity(self, seg: Segment) -> float:
        v = self.intensity_min + self._driven_value(seg) * (self.intensity_max - self.intensity_min)
        # Hard-cap the upper end. Always-on with intensity=1.0 pushes some
        # warps (Vortex angle 5 rad, Sobel breath ×1.25) into edge-case
        # parameter regions that occasionally crash cv2.remap on Windows.
        return max(0.0, min(0.95, v))

    @abstractmethod
    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray: ...


def _ensure_uint8(frame: np.ndarray) -> np.ndarray:
    return np.clip(frame, 0, 255).astype(np.uint8)


def _reseg(seg: Segment, intensity: float) -> Segment:
    """Return a copy of seg with overridden intensity."""
    return Segment(seg.t_start, seg.t_end, seg.duration, seg.type, intensity,
                   seg.rms, seg.flatness, seg.rms_change, seg.live)


# scipy availability is detected once and shared across signal-domain modules.
try:
    from scipy.signal import butter, sosfilt, fftconvolve  # noqa: F401
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False
