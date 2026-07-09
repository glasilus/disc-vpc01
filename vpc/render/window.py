"""Окно отрывка для превью/экспорта.

Пользователь может выбрать отрезок таймлайна ``[start, end]`` для рендера
превью и его экспорта. В обычном режиме видео сэмплируется случайно, к
таймлайну привязано только аудио - поэтому «отрывок» задаётся срезом аудио:
вырезаем ``[t0, t1]`` в отдельный WAV и скармливаем его движку как обычный
аудиофайл. Анализ, тайминги сегментов и муксинг остаются нетронутыми - сдвиг
поглощается на границе. В passthrough дополнительно сикается видео.

Функции здесь чистые/изолированные, чтобы клампинг тестировался без ffmpeg.
"""
from __future__ import annotations

import tempfile
from typing import Optional, Tuple

from vpc.render.encoders import ffmpeg_bin
import subprocess


def resolve_window(preview_start, preview_end,
                   total_duration: float) -> Tuple[float, Optional[float], bool]:
    """Приводит сырые start/end к валидному окну.

    Возвращает ``(t0, t1, active)``:
      * ``t0`` - начало, кламп в ``[0, total)``;
      * ``t1`` - конец (``None`` = до конца), кламп в ``(t0, total]``;
      * ``active`` - True, если окно реально отличается от полного трека.

    Мусорный ввод (None, нечисло, end<=start, за пределами) не роняет рендер -
    просто откатывается к разумному окну или к полному треку.
    """
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    total = max(0.0, float(total_duration or 0.0))
    t0 = _f(preview_start) or 0.0
    if t0 < 0.0:
        t0 = 0.0
    if total > 0.0 and t0 >= total:
        # Старт за пределами - откатываемся к полному треку, чтобы не выдать
        # пустой рендер.
        return 0.0, None, False

    t1 = _f(preview_end)
    if t1 is not None:
        if total > 0.0:
            t1 = min(t1, total)
        if t1 <= t0:
            t1 = None                      # некорректный конец -> до конца

    active = (t0 > 0.001) or (t1 is not None)
    return t0, t1, active


def slice_audio_window(audio_path: str, t0: float, t1: Optional[float],
                       log=lambda m: None) -> Optional[str]:
    """Вырезает ``[t0, t1]`` из ``audio_path`` в временный WAV. Возвращает путь
    к срезу или ``None`` при сбое (тогда вызывающий оставляет исходное аудио)."""
    if not audio_path:
        return None
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    tmp.close()
    cmd = [ffmpeg_bin(), '-y', '-ss', f'{max(0.0, t0):.3f}', '-i', audio_path]
    if t1 is not None:
        cmd += ['-t', f'{max(0.01, t1 - t0):.3f}']
    cmd += ['-ac', '2', '-ar', '44100', '-sample_fmt', 's16', tmp.name]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=300)
        if proc.returncode != 0:
            log(f'Preview window: audio slice failed '
                f'({proc.stderr.decode("utf-8", "ignore")[:200].strip()}).')
            _safe_remove(tmp.name)
            return None
        return tmp.name
    except (subprocess.SubprocessError, OSError) as e:
        log(f'Preview window: audio slice error ({e}).')
        _safe_remove(tmp.name)
        return None


def _safe_remove(path: str) -> None:
    import os
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
