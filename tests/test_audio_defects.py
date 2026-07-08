"""Тесты чистых функций пайплайна аудио-дефектов.

Дефекты - чистые функции (samples, sr) -> samples, без файлового I/O и
глобального состояния, поэтому тестируются тривиально. Проверяем
shape/dtype, что выход реально меняется, и что оркестратор корректно
ничего не делает, когда ни один дефект не включён.
"""
from __future__ import annotations

import os
import tempfile
import wave

import numpy as np
import pytest

from vpc.audio.defects import (
    defect_vhs_tape,
    defect_self_echo,
    defect_cursor_clicks,
    defect_bsod_static,
    defect_pitch_wobble,
    defect_ghost_reverb,
    defect_bitcrush_bursts,
    defect_sample_swap,
)


_ALL_DEFECTS = [
    defect_vhs_tape,
    defect_self_echo,
    defect_cursor_clicks,
    defect_bsod_static,
    defect_pitch_wobble,
    defect_ghost_reverb,
    defect_bitcrush_bursts,
    defect_sample_swap,
]
from vpc.audio.pipeline import (
    EFFECT_AUDIO_COUPLING,
    apply_passthrough_audio_defects,
    audio_link_var_name,
)


SR = 44100


def _signal(seconds: float = 1.0, ch: int = 2,
            seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    return (rng.standard_normal((n, ch)).astype(np.float32) * 0.2)


@pytest.mark.parametrize('fn', _ALL_DEFECTS)
def test_preserves_shape_and_dtype(fn):
    src = _signal()
    out = fn(src, SR)
    assert out.shape == src.shape
    assert out.dtype == np.float32


@pytest.mark.parametrize('fn', _ALL_DEFECTS)
def test_does_not_mutate_input(fn):
    src = _signal()
    snapshot = src.copy()
    _ = fn(src, SR)
    assert np.array_equal(src, snapshot), f'{fn.__name__} mutated its input'


@pytest.mark.parametrize('fn', _ALL_DEFECTS)
def test_changes_output(fn):
    src = _signal()
    out = fn(src, SR)
    diff = float(np.abs(out - src).mean())
    assert diff > 0.0, f'{fn.__name__} produced identical output'


def test_handles_empty():
    """Дефекты не должны падать на пустом (нулевой длины) буфере."""
    empty = np.zeros((0, 2), dtype=np.float32)
    for fn in _ALL_DEFECTS:
        out = fn(empty, SR)
        assert out.shape == empty.shape


def test_coupling_registry_keys_match_visual_effects():
    """Каждый ключ в EFFECT_AUDIO_COUPLING должен соответствовать реальному
    EffectSpec.enable_key в визуальном реестре - страхует от опечаток при
    добавлении новых связок."""
    from vpc.registry import EFFECTS
    enable_keys = {s.enable_key for s in EFFECTS if s.enable_key}
    for k in EFFECT_AUDIO_COUPLING:
        assert k in enable_keys, f'coupling key {k!r} has no matching effect'


def test_audio_link_var_name_format():
    assert audio_link_var_name('fx_vhstape') == 'audio_link_fx_vhstape'


def _write_wav(path: str, samples: np.ndarray, sr: int = SR) -> None:
    """Пишет стерео int16 WAV из float32-сэмплов в диапазоне [-1, 1]."""
    int16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, 'wb') as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(int16.tobytes())


def _read_wav(path: str) -> np.ndarray:
    with wave.open(path, 'rb') as w:
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    return np.frombuffer(raw, dtype=np.int16).reshape(-1, ch)


def test_pipeline_no_op_when_no_defects_enabled():
    """Если не включён ни enable-флаг эффекта, ни audio_link, пайплайн
    должен вернуть False и не менять WAV побайтово."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'in.wav')
        sig = _signal(0.5)
        _write_wav(path, sig)
        before = _read_wav(path)
        result = apply_passthrough_audio_defects(path, cfg={}, log=lambda m: None)
        after = _read_wav(path)
        assert result is False
        assert np.array_equal(before, after), 'WAV touched despite no defects enabled'


def test_self_echo_perf_under_budget():
    """Регрессионный тест на производительность: defect_self_echo должен
    обрабатывать 60с стерео 44.1kHz быстрее секунды. Старая реализация
    с Python-циклом занимала ~5с; lfilter с 9700-tap знаменателем давал
    парадоксально ~50с; текущая chunked-vector реализация укладывается
    в <0.2с. Если тест начал падать - скорее всего кто-то вернул
    поэлементный Python-цикл."""
    import time
    from vpc.audio.defects import defect_self_echo
    sig = (np.random.rand(SR * 60, 2).astype(np.float32) - 0.5) * 0.4
    t0 = time.perf_counter()
    _ = defect_self_echo(sig, SR)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f'self_echo took {elapsed:.2f}s on 60s stereo (budget 1.0s)'


def test_defects_handle_mono():
    """Дефекты не должны считать, что вход всегда стерео. Проверяем доступ
    к `out.shape[1]` на буфере с одним каналом."""
    sig = (np.random.rand(SR, 1).astype(np.float32) - 0.5) * 0.4
    for fn in _ALL_DEFECTS:
        out = fn(sig, SR)
        assert out.shape == sig.shape, f'{fn.__name__} broke mono shape'
        assert out.dtype == np.float32, f'{fn.__name__} broke mono dtype'


def test_pipeline_applies_when_enabled():
    """При включённой одной связке WAV должен измениться."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'in.wav')
        sig = _signal(0.5)
        _write_wav(path, sig)
        before = _read_wav(path)
        cfg = {'fx_self_cannibalize': True,
               audio_link_var_name('fx_self_cannibalize'): True}
        result = apply_passthrough_audio_defects(path, cfg, log=lambda m: None)
        after = _read_wav(path)
        assert result is True
        assert not np.array_equal(before, after), 'WAV unchanged despite defect enabled'
