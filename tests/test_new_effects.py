"""Shape / dtype / non-trivial-change tests for the four new visual effects.

We don't golden-hash any of these — they all involve random sources or
state — but each one MUST: preserve frame shape, return uint8, and
actually mutate the frame at non-zero intensity. Wired through the
public BaseEffect.apply() so we also implicitly test that the chain
gating doesn't accidentally skip them.
"""
from __future__ import annotations

import numpy as np
import pytest

from vpc.analyzer import Segment, SegmentType
from vpc.effects.vhs import VHSTapeEffect
from vpc.effects.broken import (
    SelfCannibalizeEffect,
    VSyncRollEffect,
    PFrameLagEffect,
    BitFlipEffect,
    WrongMotionVectorEffect,
)
from vpc.effects.virus import CursorStormEffect, BSODShredEffect


def _seg(intensity: float = 0.7) -> Segment:
    return Segment(0.0, 1.0, 1.0, SegmentType.SUSTAIN, intensity, 0.3, 0.1, 0.05)


def _frame(seed: int = 7, h: int = 180, w: int = 320) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


@pytest.mark.parametrize('cls,kwargs', [
    (VHSTapeEffect, {}),
    (VHSTapeEffect, {'dust': True}),
    (SelfCannibalizeEffect, {}),
    (CursorStormEffect, {}),
    (BSODShredEffect, {}),
    (VSyncRollEffect, {}),
    (PFrameLagEffect, {}),
    (BitFlipEffect, {}),
    (WrongMotionVectorEffect, {}),
])
def test_preserves_shape_and_dtype(cls, kwargs):
    fx = cls(enabled=True, chance=1.0,
             intensity_min=0.6, intensity_max=0.6, **kwargs)
    src = _frame()
    out = fx.apply(src, _seg(), draft=False)
    assert out.shape == src.shape
    assert out.dtype == np.uint8


@pytest.mark.parametrize('cls,kwargs', [
    (VHSTapeEffect, {}),
    (SelfCannibalizeEffect, {}),
    (CursorStormEffect, {}),
    (BSODShredEffect, {}),
    (VSyncRollEffect, {}),
    (BitFlipEffect, {}),
    (WrongMotionVectorEffect, {}),
])
def test_actually_mutates_at_non_zero_intensity(cls, kwargs):
    fx = cls(enabled=True, chance=1.0,
             intensity_min=0.7, intensity_max=0.7, **kwargs)
    src = _frame()
    out = fx.apply(src, _seg(), draft=False)
    diff = int(np.abs(out.astype(int) - src.astype(int)).sum())
    assert diff > 0, f'{cls.__name__} produced identical output to input'


def test_pframe_lag_warmup_then_smear():
    """PFrameLag is a no-op on its very first frame (no prev buffer
    yet) but must produce a smear on the second different frame."""
    fx = PFrameLagEffect(enabled=True, chance=1.0,
                         intensity_min=0.7, intensity_max=0.7)
    a = _frame(seed=1)
    b = _frame(seed=2)
    out1 = fx.apply(a, _seg(), draft=False)
    assert np.array_equal(out1, a), 'first frame should be identity (warmup)'
    out2 = fx.apply(b, _seg(), draft=False)
    assert not np.array_equal(out2, b), 'second frame should smear'


def test_pframe_lag_keeps_prev_fresh_when_chance_fails():
    """When chance roll fails, PFrameLag.apply must still update its
    prev buffer (custom apply override) — otherwise the next firing
    would smear against an outdated frame and snap visibly."""
    # Disable by setting chance=0; apply() will short-circuit but our
    # override must still update _prev.
    fx = PFrameLagEffect(enabled=True, chance=0.0,
                         intensity_min=0.7, intensity_max=0.7)
    a = _frame(seed=1)
    b = _frame(seed=2)
    fx.apply(a, _seg(), draft=False)
    fx.apply(b, _seg(), draft=False)
    # _prev must reflect b (the most recent input), not a.
    assert fx._prev is not None
    assert np.allclose(fx._prev, b.astype(np.float32))


def test_pframe_lag_disabled_drops_state_no_copy():
    """When the effect is structurally disabled, the `apply` override
    must early-return without copying frame to float32 (perf path) and
    must drop the prev buffer so a future re-enable starts cleanly."""
    fx = PFrameLagEffect(enabled=True, chance=1.0,
                         intensity_min=0.7, intensity_max=0.7)
    a = _frame(seed=1)
    b = _frame(seed=2)
    fx.apply(a, _seg(), draft=False)
    # Prime: now disable
    fx.enabled = False
    out = fx.apply(b, _seg(), draft=False)
    assert out is b, 'disabled apply should return frame by identity (no copy)'
    assert fx._prev is None, 'disabled apply should drop prev buffer'


def test_bit_flip_random_alloc_is_uint8_not_float64():
    """Regression guard against re-introducing the float64 mask alloc.
    We can't directly inspect the temporary, but we CAN check that the
    effect runs in much less memory than a float64 mask would imply.
    Indirect: just verify it produces correct output on a large frame
    without OOM (4K is too big for CI; we use 1080p)."""
    fx = BitFlipEffect(enabled=True, chance=1.0,
                       intensity_min=0.5, intensity_max=0.5)
    big = np.random.randint(0, 256, (1080, 1920, 3), dtype=np.uint8)
    out = fx.apply(big, _seg(), draft=False)
    assert out.shape == big.shape
    assert out.dtype == np.uint8


def test_zero_intensity_is_passthrough():
    """All effects must be a no-op when intensity is pinned to 0."""
    src = _frame()
    for cls in (VHSTapeEffect, SelfCannibalizeEffect,
                CursorStormEffect, BSODShredEffect,
                VSyncRollEffect, PFrameLagEffect,
                BitFlipEffect, WrongMotionVectorEffect):
        fx = cls(enabled=True, chance=1.0,
                 intensity_min=0.0, intensity_max=0.0)
        out = fx.apply(src, _seg(intensity=0.0), draft=False)
        assert np.array_equal(out, src), f'{cls.__name__} not pass-through at intensity 0'


def test_cursor_storm_state_persists_across_frames():
    """Stateful pointer positions must move between frames — verifies
    `_pointers` carries state, not re-seeds each call."""
    fx = CursorStormEffect(enabled=True, chance=1.0,
                           intensity_min=0.5, intensity_max=0.5)
    src = _frame()
    fx.apply(src, _seg(), draft=False)
    snapshot = [(p.x, p.y) for p in fx._pointers]
    fx.apply(src, _seg(), draft=False)
    moved = [(p.x, p.y) for p in fx._pointers]
    assert snapshot != moved, 'pointers did not advance between frames'


def test_vhstape_dust_changes_output():
    """Toggling `dust` must produce a different result from the same input
    (proves the dust path runs at all)."""
    src = _frame()
    common = dict(enabled=True, chance=1.0,
                  intensity_min=0.5, intensity_max=0.5)
    a = VHSTapeEffect(**common, dust=False)
    b = VHSTapeEffect(**common, dust=True)
    import random; random.seed(0); np.random.seed(0)
    out_a = a.apply(src, _seg(), draft=False)
    random.seed(0); np.random.seed(0)
    out_b = b.apply(src, _seg(), draft=False)
    # Dust draws dark vertical lines — they SHOULD make the result differ
    # somewhere even with matched RNG seeds. (Not guaranteed every seed
    # because dust has its own probability gate; we use seed 0 which
    # was empirically chosen to fire the gate at least once.)
    assert not np.array_equal(out_a, out_b), 'dust off vs on identical'
