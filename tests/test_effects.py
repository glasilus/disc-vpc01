"""Tests for effects.py — all effect classes."""
import pytest
import random
import numpy as np
from vpc.analyzer import Segment, SegmentType
from vpc.effects import (
    BaseEffect, _ensure_uint8,
    FlashEffect, GhostTrailsEffect, PixelSortEffect, DatamoshEffect,
    ASCIIEffect, RGBShiftEffect, BlockGlitchEffect, PixelDriftEffect,
    ScanLinesEffect, BitcrushEffect, ColorBleedEffect, FreezeCorruptEffect,
    NegativeEffect, JPEGCrushEffect, FisheyeEffect, VHSTrackingEffect,
    InterlaceEffect, BadSignalEffect, DitheringEffect, ZoomGlitchEffect,
    FeedbackLoopEffect, PhaseShiftEffect, MosaicPulseEffect,
    EchoCompoundEffect, KaliMirrorEffect, GlitchCascadeEffect,
    ChromaKeyEffect, OverlayEffect, HistoLagEffect,
)
from vpc.mystery import MysterySection


def make_seg(type=SegmentType.IMPACT, intensity=0.8):
    return Segment(0.0, 1.0, 1.0, type, intensity, 0.5, 0.3, 0.1)


# ── Concrete subclass for testing BaseEffect ──

class DummyEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT]

    def _apply(self, frame, seg, draft):
        return np.zeros_like(frame)


# ── BaseEffect tests (Task 5) ──

def test_base_effect_skips_wrong_type(noise_frame):
    fx = DummyEffect(enabled=True, chance=1.0)
    seg = make_seg(type=SegmentType.SILENCE)
    result = fx.apply(noise_frame, seg, draft=False)
    assert result is noise_frame


def test_base_effect_applies_correct_type(noise_frame):
    fx = DummyEffect(enabled=True, chance=1.0)
    seg = make_seg(type=SegmentType.IMPACT)
    result = fx.apply(noise_frame, seg, draft=False)
    assert np.all(result == 0)


def test_base_effect_disabled(noise_frame):
    fx = DummyEffect(enabled=False, chance=1.0)
    seg = make_seg(type=SegmentType.IMPACT)
    result = fx.apply(noise_frame, seg, draft=False)
    assert result is noise_frame


# ── Shape preservation parametrized test ──

@pytest.mark.parametrize("EffectClass", [
    RGBShiftEffect, BlockGlitchEffect, PixelDriftEffect, ScanLinesEffect,
    BitcrushEffect, ColorBleedEffect, NegativeEffect,
    JPEGCrushEffect, FisheyeEffect, VHSTrackingEffect, InterlaceEffect,
    BadSignalEffect, DitheringEffect, ZoomGlitchEffect,
    FeedbackLoopEffect, PhaseShiftEffect, MosaicPulseEffect,
    EchoCompoundEffect, KaliMirrorEffect, GlitchCascadeEffect,
    FlashEffect, GhostTrailsEffect, ASCIIEffect, PixelSortEffect, DatamoshEffect,
    FreezeCorruptEffect, OverlayEffect,
])
def test_effect_preserves_shape(EffectClass, noise_frame):
    random.seed(42)
    np.random.seed(42)
    fx = EffectClass(enabled=True, chance=1.0)
    seg = make_seg(type=SegmentType.IMPACT, intensity=0.8)
    fx.trigger_types = list(SegmentType)
    result = fx.apply(noise_frame, seg, draft=False)
    assert result.shape == noise_frame.shape
    assert result.dtype == np.uint8


# ── Specific behavior tests ──

def test_ghost_trails_blends(noise_frame):
    fx = GhostTrailsEffect(enabled=True, chance=1.0)
    fx.trigger_types = list(SegmentType)
    seg = make_seg(type=SegmentType.SUSTAIN, intensity=0.5)
    prev = np.full_like(noise_frame, 128)
    fx.last_frame = prev
    result = fx.apply(noise_frame, seg, draft=False)
    assert result.shape == noise_frame.shape
    assert not np.array_equal(result, noise_frame)
    assert not np.array_equal(result, prev)


def test_flash_effect(noise_frame):
    fx = FlashEffect(enabled=True, chance=1.0)
    seg = make_seg(type=SegmentType.DROP, intensity=0.9)
    result = fx.apply(noise_frame, seg, draft=False)
    assert result.shape == noise_frame.shape
    assert result.dtype == np.uint8


def test_datamosh_with_prev(noise_frame):
    fx = DatamoshEffect(enabled=True, chance=1.0)
    fx.trigger_types = list(SegmentType)
    seg = make_seg(type=SegmentType.NOISE, intensity=0.5)
    prev = np.roll(noise_frame, 10, axis=1)
    fx.prev_frame = prev.copy()
    result = fx.apply(noise_frame, seg, draft=False)
    assert result.shape == noise_frame.shape


def test_ascii_effect_shape(noise_frame):
    fx = ASCIIEffect(enabled=True, chance=1.0)
    fx.trigger_types = list(SegmentType)
    seg = make_seg(type=SegmentType.SUSTAIN, intensity=0.5)
    result = fx.apply(noise_frame, seg, draft=False)
    assert result.shape == noise_frame.shape


def test_chroma_key_green():
    green_frame = np.full((100, 100, 3), [0, 255, 0], dtype=np.uint8)
    ck = ChromaKeyEffect(key_color=(0, 255, 0), tolerance=30)
    mask = ck.get_mask(green_frame)
    assert mask.shape == (100, 100)
    # Most pixels should be keyed out (mask ~0)
    assert np.mean(mask) < 50


def test_overlay_composites(noise_frame):
    overlay = np.full_like(noise_frame, 200)
    fx = OverlayEffect(overlay_frames=[overlay], opacity=0.5,
                       enabled=True, chance=1.0)
    seg = make_seg(type=SegmentType.IMPACT, intensity=0.5)
    result = fx.apply(noise_frame, seg, draft=False)
    assert result.shape == noise_frame.shape
    assert result.dtype == np.uint8


def test_mystery_section_shape(noise_frame):
    ms = MysterySection()
    ms.VESSEL = 0.5
    ms.STATIC_MIND = 0.3
    ms.COLLAPSE = 0.2
    seg = make_seg(type=SegmentType.SUSTAIN, intensity=0.5)
    result = ms.apply(noise_frame, seg, draft=False)
    assert result.shape == noise_frame.shape
    assert result.dtype == np.uint8


# ── always / always-on-intensity consistency ──

def test_blend_by_intensity_zero_is_passthrough(noise_frame):
    fx = DummyEffect(enabled=True, chance=1.0, intensity_min=0.0, intensity_max=0.0)
    seg = make_seg(type=SegmentType.IMPACT, intensity=0.9)
    result = np.zeros_like(noise_frame)
    out = fx._blend_by_intensity(seg, result, noise_frame)
    assert np.array_equal(out, noise_frame)


def test_blend_by_intensity_nonzero_mixes_toward_result(noise_frame):
    fx = DummyEffect(enabled=True, chance=1.0, intensity_min=0.7, intensity_max=0.7)
    seg = make_seg(type=SegmentType.IMPACT, intensity=0.9)
    result = np.zeros_like(noise_frame)
    out = fx._blend_by_intensity(seg, result, noise_frame)
    assert not np.array_equal(out, noise_frame)
    assert not np.array_equal(out, result)


def test_ascii_effect_intensity_scales(noise_frame):
    seg = make_seg(type=SegmentType.SUSTAIN, intensity=0.6)

    fx_zero = ASCIIEffect(enabled=True, chance=1.0, intensity_min=0.0, intensity_max=0.0)
    fx_zero.trigger_types = list(SegmentType)
    result_zero = fx_zero.apply(noise_frame, seg, draft=False)
    assert np.array_equal(result_zero, noise_frame)

    fx_full = ASCIIEffect(enabled=True, chance=1.0, intensity_min=0.7, intensity_max=0.7)
    fx_full.trigger_types = list(SegmentType)
    result_full = fx_full.apply(noise_frame, seg, draft=False)
    diff = int(np.abs(result_full.astype(int) - noise_frame.astype(int)).sum())
    assert diff > 0


def test_negative_effect_intensity_scales(noise_frame):
    seg = make_seg(type=SegmentType.IMPACT, intensity=0.6)

    fx_zero = NegativeEffect(enabled=True, chance=1.0, intensity_min=0.0, intensity_max=0.0)
    fx_zero.trigger_types = list(SegmentType)
    result_zero = fx_zero.apply(noise_frame, seg, draft=False)
    assert np.array_equal(result_zero, noise_frame)

    fx_full = NegativeEffect(enabled=True, chance=1.0, intensity_min=0.7, intensity_max=0.7)
    fx_full.trigger_types = list(SegmentType)
    result_full = fx_full.apply(noise_frame, seg, draft=False)
    diff = int(np.abs(result_full.astype(int) - noise_frame.astype(int)).sum())
    assert diff > 0


def test_interlace_effect_intensity_scales(noise_frame):
    seg = make_seg(type=SegmentType.SUSTAIN, intensity=0.6)
    prev = np.roll(noise_frame, 5, axis=0)

    fx_zero = InterlaceEffect(enabled=True, chance=1.0, intensity_min=0.0, intensity_max=0.0)
    fx_zero.trigger_types = list(SegmentType)
    fx_zero.prev_frame = prev
    result_zero = fx_zero.apply(noise_frame, seg, draft=False)
    assert np.array_equal(result_zero, noise_frame)

    fx_full = InterlaceEffect(enabled=True, chance=1.0, intensity_min=0.7, intensity_max=0.7)
    fx_full.trigger_types = list(SegmentType)
    fx_full.prev_frame = prev
    result_full = fx_full.apply(noise_frame, seg, draft=False)
    diff = int(np.abs(result_full.astype(int) - noise_frame.astype(int)).sum())
    assert diff > 0


def test_echo_compound_intensity_scales(noise_frame):
    seg = make_seg(type=SegmentType.SUSTAIN, intensity=0.6)
    other_frame = np.clip(noise_frame.astype(int) + 60, 0, 255).astype(np.uint8)

    # EchoCompoundEffect (echo_n=8 default) only pulls its first tap from real
    # history once len(history) > echo_n — until then it echoes the current
    # frame back at itself (a warm-up no-op, same idiom as PFrameLag's
    # warmup-then-smear elsewhere in this suite). Prime past echo_n frames.
    fx_zero = EchoCompoundEffect(enabled=True, chance=1.0, intensity_min=0.0, intensity_max=0.0)
    fx_zero.trigger_types = list(SegmentType)
    for _ in range(9):
        fx_zero.apply(other_frame, seg, draft=False)
    result_zero = fx_zero.apply(noise_frame, seg, draft=False)
    assert np.array_equal(result_zero, noise_frame)

    fx_full = EchoCompoundEffect(enabled=True, chance=1.0, intensity_min=0.7, intensity_max=0.7)
    fx_full.trigger_types = list(SegmentType)
    for _ in range(9):
        fx_full.apply(other_frame, seg, draft=False)
    result_full = fx_full.apply(noise_frame, seg, draft=False)
    diff = int(np.abs(result_full.astype(int) - noise_frame.astype(int)).sum())
    assert diff > 0


def test_histo_lag_effect_intensity_scales(noise_frame):
    seg = make_seg(type=SegmentType.SUSTAIN, intensity=0.6)
    # A brightness shift (not a roll) actually changes the value histogram,
    # so palette-matching against it has something to do.
    other_frame = np.clip(noise_frame.astype(int) + 80, 0, 255).astype(np.uint8)

    fx_zero = HistoLagEffect(enabled=True, chance=1.0, intensity_min=0.0, intensity_max=0.0)
    fx_zero.trigger_types = list(SegmentType)
    fx_zero.apply(other_frame, seg, draft=False)   # seed history with a different frame
    result_zero = fx_zero.apply(noise_frame, seg, draft=False)
    assert np.array_equal(result_zero, noise_frame)

    fx_full = HistoLagEffect(enabled=True, chance=1.0, intensity_min=0.7, intensity_max=0.7)
    fx_full.trigger_types = list(SegmentType)
    fx_full.apply(other_frame, seg, draft=False)
    result_full = fx_full.apply(noise_frame, seg, draft=False)
    diff = int(np.abs(result_full.astype(int) - noise_frame.astype(int)).sum())
    assert diff > 0
