"""Pure-function tests for the audio defects pipeline.

Defects are pure (samples, sr) -> samples; no file I/O, no global state,
trivially testable. We verify shape/dtype, non-trivial mutation, and
that the orchestrator correctly skips when nothing is enabled.
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
    """Defects must accept a zero-sample buffer without crashing."""
    empty = np.zeros((0, 2), dtype=np.float32)
    for fn in _ALL_DEFECTS:
        out = fn(empty, SR)
        assert out.shape == empty.shape


def test_coupling_registry_keys_match_visual_effects():
    """Every key in EFFECT_AUDIO_COUPLING must correspond to an actual
    EffectSpec.enable_key in the visual registry — protects against typos
    when adding new couplings."""
    from vpc.registry import EFFECTS
    enable_keys = {s.enable_key for s in EFFECTS if s.enable_key}
    for k in EFFECT_AUDIO_COUPLING:
        assert k in enable_keys, f'coupling key {k!r} has no matching effect'


def test_audio_link_var_name_format():
    assert audio_link_var_name('fx_vhstape') == 'audio_link_fx_vhstape'


def _write_wav(path: str, samples: np.ndarray, sr: int = SR) -> None:
    """Helper: write a stereo int16 WAV from float32 samples in [-1, 1]."""
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
    """When neither effect-enable nor audio_link is set, pipeline must
    return False and leave the WAV byte-identical."""
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
    """Regression guard: defect_self_echo must complete 60s of stereo
    44.1kHz audio under 1 second. The earlier Python-loop implementation
    took ~5s; lfilter on a 9700-tap denominator paradoxically took ~50s;
    the chunked-vector implementation does it in <0.2s. If this test
    starts failing the most likely cause is someone re-introduced a
    per-sample Python loop."""
    import time
    from vpc.audio.defects import defect_self_echo
    sig = (np.random.rand(SR * 60, 2).astype(np.float32) - 0.5) * 0.4
    t0 = time.perf_counter()
    _ = defect_self_echo(sig, SR)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f'self_echo took {elapsed:.2f}s on 60s stereo (budget 1.0s)'


def test_defects_handle_mono():
    """Defects shouldn't assume stereo. Mono path exercises the
    `out.shape[1]` access on a 1-channel buffer."""
    sig = (np.random.rand(SR, 1).astype(np.float32) - 0.5) * 0.4
    for fn in _ALL_DEFECTS:
        out = fn(sig, SR)
        assert out.shape == sig.shape, f'{fn.__name__} broke mono shape'
        assert out.dtype == np.float32, f'{fn.__name__} broke mono dtype'


def test_pipeline_applies_when_enabled():
    """With one coupling enabled, the WAV must change."""
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
