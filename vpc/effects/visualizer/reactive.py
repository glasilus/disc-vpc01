"""Shared helpers for reading the per-frame AudioSample off a segment."""
from __future__ import annotations

import numpy as np

from vpc.analyzer import AudioSample, N_BINS


def read_sample(seg) -> AudioSample:
    """Return the per-frame AudioSample the engine attached, or a fallback.

    The engine sets ``seg.live`` in ``_apply_chain`` before the effect chain
    runs. When it is absent (e.g. a GUI still-frame preview with no render
    context) we synthesize a zeroed sample modulated by segment intensity so
    the visual is never blank.
    """
    live = getattr(seg, 'live', None)
    if live is not None:
        return live
    i = float(getattr(seg, 'intensity', 0.0) or 0.0)
    return AudioSample(bass=i, mid=i, high=i, onset=i, beat=False,
                       bins=np.full(N_BINS, i, np.float32), t=0.0)
