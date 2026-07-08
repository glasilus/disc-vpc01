"""Общие хелперы для чтения AudioSample кадра с сегмента."""
from __future__ import annotations

import numpy as np

from vpc.analyzer import AudioSample, N_BINS


def read_sample(seg) -> AudioSample:
    """Возвращает AudioSample, прикреплённый движком к сегменту, либо заглушку.

    Движок выставляет ``seg.live`` в ``_apply_chain`` перед прогоном цепочки
    эффектов. Если его нет (например, превью статичного кадра в GUI без
    контекста рендера), собираем сэмпл на основе intensity сегмента, чтобы
    визуализация не была пустой.
    """
    live = getattr(seg, 'live', None)
    if live is not None:
        return live
    i = float(getattr(seg, 'intensity', 0.0) or 0.0)
    return AudioSample(bass=i, mid=i, high=i, onset=i, beat=False,
                       bins=np.full(N_BINS, i, np.float32), t=0.0)
