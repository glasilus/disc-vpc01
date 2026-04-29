"""Tests for the four warp effects integrated into the registry."""
import random
import numpy as np

from vpc.analyzer import Segment, SegmentType
from vpc.effects import (
    DerivWarpEffect, VortexWarpEffect, FractalNoiseWarpEffect, SelfDisplaceEffect,
)


def make_seg(t=SegmentType.IMPACT, intensity=0.6):
    return Segment(0.0, 1.0, 1.0, t, intensity, 0.4, 0.2, 0.05)


def make_frame(seed=0, h=96, w=96):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


def test_deriv_warp_changes_pixels():
    fx = DerivWarpEffect(enabled=True, chance=1.0)
    f1 = make_frame(0); _ = fx.apply(f1, make_seg(), False)
    f2 = make_frame(1)
    out = fx.apply(f2, make_seg(), False)
    assert out.shape == f2.shape
    assert not np.array_equal(out, f2), 'DerivWarp should modify the frame'


def test_deriv_warp_history_kept_on_skip():
    """Even when chance prevents firing, _prev must update — otherwise the
    next fire would warp against a stale frame."""
    fx = DerivWarpEffect(enabled=True, chance=0.0)
    fx.apply(make_frame(0), make_seg(), False)
    assert fx._prev is not None
    fx.apply(make_frame(1), make_seg(), False)
    assert fx._prev is not None


def test_vortex_warp_invariant_at_zero_intensity():
    fx = VortexWarpEffect(enabled=True, chance=1.0)
    f = make_frame(7)
    seg = make_seg(intensity=0.0)
    out = fx.apply(f, seg, False)
    # intensity=0 → angle=0 everywhere → identity remap; allow tiny rounding diff
    assert np.abs(out.astype(int) - f.astype(int)).max() <= 2


def test_vortex_warp_changes_at_high_intensity():
    fx = VortexWarpEffect(enabled=True, chance=1.0)
    f = make_frame(8)
    out = fx.apply(f, make_seg(intensity=1.0), False)
    assert not np.array_equal(out, f)


def test_fractal_warp_field_flows_in_time():
    """Field must visibly evolve frame-to-frame so warp is animated, not static.

    The redesigned FractalNoiseWarp samples opensimplex noise3 with a z-axis
    advanced by an internal frame counter — passing the SAME frame twice in
    a row should now produce DIFFERENT outputs (the field has flowed),
    instead of the previous per-segment determinism.
    """
    fx = FractalNoiseWarpEffect(enabled=True, chance=1.0)
    seg = make_seg()
    f = make_frame(3)
    o1 = fx.apply(f, seg, False)
    o2 = fx.apply(f, seg, False)
    assert not np.array_equal(o1, o2)


def test_self_displace_history_grows():
    fx = SelfDisplaceEffect(enabled=True, chance=1.0, history_len=4)
    for i in range(6):
        fx.apply(make_frame(i), make_seg(), False)
    assert len(fx._history) <= fx.history_len + 1


def test_self_displace_no_crash_when_history_short():
    fx = SelfDisplaceEffect(enabled=True, chance=1.0)
    out = fx.apply(make_frame(0), make_seg(), False)
    assert out.shape == (96, 96, 3)
