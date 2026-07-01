"""Tests for audio-reactive wiring of existing effects (Audio Drive / Beat
Gate / bespoke react) built on the per-frame seg.live track."""
import numpy as np
import pytest

from vpc.analyzer import Segment, SegmentType, AudioSample, N_BINS
from vpc.effects.base import BaseEffect


def _seg(intensity=0.5, bass=0.0, mid=0.0, high=0.0, beat=False, onset=0.0,
         seg_type=None, bins=None, with_live=True):
    st = seg_type or SegmentType.SUSTAIN
    s = Segment(0, 1, 1, st, intensity, 0.1, 0.1, 0.0)
    if with_live:
        if bins is None:
            bins = np.full(N_BINS, high, np.float32)
        s.live = AudioSample(bass, mid, high, onset, beat,
                             np.asarray(bins, np.float32), 0.0)
    return s


class _Marker(BaseEffect):
    """Minimal effect that fires on every segment type and marks the frame."""
    trigger_types = list(SegmentType)

    def _apply(self, frame, seg, draft):
        return (frame + 1).astype(np.uint8)


# ── Audio Drive (scaled_intensity) ─────────────────────────────────────────
def test_audio_drive_selects_band():
    fx = _Marker(); fx.intensity_min = 0.0; fx.intensity_max = 1.0
    seg = _seg(intensity=0.2, bass=0.9, mid=0.3, high=0.1)
    fx.audio_drive = 'segment'
    assert abs(fx.scaled_intensity(seg) - 0.2) < 1e-6
    fx.audio_drive = 'bass'
    assert abs(fx.scaled_intensity(seg) - 0.9) < 1e-6
    fx.audio_drive = 'high'
    assert abs(fx.scaled_intensity(seg) - 0.1) < 1e-6
    fx.audio_drive = 'auto'          # loudest band = bass
    assert abs(fx.scaled_intensity(seg) - 0.9) < 1e-6


def test_audio_drive_falls_back_without_live():
    fx = _Marker(); fx.intensity_min = 0.0; fx.intensity_max = 1.0
    fx.audio_drive = 'bass'
    seg = _seg(intensity=0.33, with_live=False)   # no seg.live
    assert abs(fx.scaled_intensity(seg) - 0.33) < 1e-6   # never dead


# ── Beat Gate ──────────────────────────────────────────────────────────────
def test_beat_gate_blocks_offbeat_frames():
    frame = np.full((8, 8, 3), 100, np.uint8)
    fx = _Marker(enabled=True, chance=1.0); fx.beat_gate = 'beat'
    off = fx.apply(frame, _seg(beat=False), draft=False)
    on = fx.apply(frame, _seg(beat=True), draft=False)
    assert np.array_equal(off, frame)          # blocked → unchanged
    assert on.max() == 101                      # passed → marked


def test_beat_gate_passes_when_no_live():
    frame = np.full((8, 8, 3), 100, np.uint8)
    fx = _Marker(enabled=True, chance=1.0); fx.beat_gate = 'beat'
    out = fx.apply(frame, _seg(with_live=False), draft=False)
    assert out.max() == 101                     # never muted on no-audio path


# ── build_chain generic wiring ─────────────────────────────────────────────
def _get(chain, name):
    return next(f for f in chain if type(f).__name__ == name)


def test_build_chain_wires_reactivity_attrs():
    from vpc.registry import default_cfg, build_chain
    cfg = default_cfg()
    cfg.update({'fx_mosaic': True, 'fx_mosaic_drive': 'bass',
                'fx_negative': True, 'fx_negative_gate': 'beat',
                'fx_resonant': True, 'fx_resonant_react': 'on'})
    chain = build_chain(cfg)
    assert _get(chain, 'MosaicPulseEffect').audio_drive == 'bass'
    assert _get(chain, 'NegativeEffect').beat_gate == 'beat'
    assert _get(chain, 'ResonantRowsEffect').react is True


# ── registry params + preset safety ────────────────────────────────────────
def test_reactive_params_registered_with_safe_defaults():
    from vpc.registry import default_cfg
    cfg = default_cfg()
    drives = ['fx_mosaic', 'fx_rgb', 'fx_block_glitch', 'fx_bad_signal',
              'fx_bit_flip', 'fx_zoom_glitch', 'fx_feedback']
    for ek in drives:
        assert cfg[ek + '_drive'] == 'segment', ek
    for ek in ['fx_negative', 'fx_zoom_glitch']:
        assert cfg[ek + '_gate'] == 'off', ek
    for ek in ['fx_feedback', 'fx_resonant', 'fx_fft_phase', 'fx_spatial_reverb']:
        assert cfg[ek + '_react'] == 'off', ek


def test_old_preset_without_reactive_keys_builds():
    from vpc.registry import default_cfg, build_chain
    cfg = default_cfg()
    for k in list(cfg):
        if k.endswith(('_drive', '_gate', '_react')):
            del cfg[k]
    cfg['fx_mosaic'] = True
    cfg['fx_resonant'] = True
    chain = build_chain(cfg)                 # must not raise
    m = _get(chain, 'MosaicPulseEffect')
    assert m.audio_drive == 'segment' and m.beat_gate == 'off' and m.react is False


# ── bespoke Signal Domain react ────────────────────────────────────────────
def test_resonant_react_runs_and_reads_centroid():
    from vpc.effects.signal import ResonantRowsEffect, _SCIPY_OK, _spectral_centroid
    # centroid helper: energy skewed to high bins → centroid near 1
    hi = np.zeros(N_BINS, np.float32); hi[-3:] = 1.0
    assert _spectral_centroid(hi) > 0.7
    if not _SCIPY_OK:
        pytest.skip('scipy unavailable')
    fx = ResonantRowsEffect(enabled=True, chance=1.0); fx.react = True
    seg = _seg(high=1.0, bins=hi, seg_type=SegmentType.NOISE, intensity=0.8)
    out = fx.apply(np.random.randint(0, 255, (20, 24, 3), np.uint8), seg, False)
    assert out.shape == (20, 24, 3) and out.dtype == np.uint8


def test_fft_phase_react_runs():
    from vpc.effects.signal import FFTPhaseCorruptEffect
    fx = FFTPhaseCorruptEffect(enabled=True, chance=1.0); fx.react = True
    bins = np.linspace(0, 1, N_BINS, dtype=np.float32)
    seg = _seg(high=0.8, bins=bins, onset=0.5, seg_type=SegmentType.NOISE, intensity=0.7)
    out = fx.apply(np.random.randint(0, 255, (24, 32, 3), np.uint8), seg, False)
    assert out.shape == (24, 32, 3) and out.dtype == np.uint8
    # ring index cache populated for the rfft2 shape
    assert len(fx._ring_idx) >= 1


def test_spatial_reverb_react_runs():
    from vpc.effects.signal import SpatialReverbEffect, _SCIPY_OK
    if not _SCIPY_OK:
        pytest.skip('scipy unavailable')
    fx = SpatialReverbEffect(enabled=True, chance=1.0); fx.react = True
    for onset in (0.1, 0.9, 0.5):
        seg = _seg(mid=0.6, onset=onset, seg_type=SegmentType.SUSTAIN, intensity=0.7)
        out = fx.apply(np.random.randint(0, 255, (16, 24, 3), np.uint8), seg, False)
        assert out.shape == (16, 24, 3)
    assert len(fx._onset_hist) == 3


def test_feedback_react_clears_on_beat():
    from vpc.effects.complex_fx import FeedbackLoopEffect
    fx = FeedbackLoopEffect(enabled=True, chance=1.0)
    fx.react = True; fx.intensity_min = 0.0; fx.intensity_max = 1.0
    A = np.full((8, 8, 3), 50, np.uint8)
    B = np.full((8, 8, 3), 200, np.uint8)
    fx.apply(A, _seg(mid=0.9, intensity=0.9), False)          # init accumulator
    out = fx.apply(B, _seg(mid=0.9, intensity=0.9, beat=True), False)
    assert np.array_equal(out, B)                              # beat cleared → fresh B
