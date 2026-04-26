"""FormulaEffect — backlog item #3."""
import numpy as np
import pytest

from vpc.analyzer import Segment, SegmentType
from vpc.effects import FormulaEffect


def make_seg():
    return Segment(0.0, 1.0, 1.0, SegmentType.SUSTAIN, 0.5, 0.4, 0.2, 0.05)


def make_frame():
    rng = np.random.RandomState(0)
    return rng.randint(0, 256, (32, 32, 3), dtype=np.uint8)


def test_identity_expression():
    fx = FormulaEffect(expression='frame', enabled=True, chance=1.0)
    f = make_frame()
    out = fx.apply(f, make_seg(), False)
    assert np.array_equal(out, f)


def test_invert_expression():
    fx = FormulaEffect(expression='255 - frame', enabled=True, chance=1.0)
    f = make_frame()
    out = fx.apply(f, make_seg(), False)
    assert np.array_equal(out, (255 - f).astype(np.uint8))


def test_safe_against_attribute_access():
    """Sandbox should reject __builtins__-only attribute escapes."""
    fx = FormulaEffect(expression="__import__('os')", enabled=True, chance=1.0)
    f = make_frame()
    out = fx.apply(f, make_seg(), False)
    # Falls back to original frame on error
    assert np.array_equal(out, f)


def test_syntax_error_no_crash():
    fx = FormulaEffect(expression='frame +', enabled=True, chance=1.0)
    f = make_frame()
    out = fx.apply(f, make_seg(), False)
    assert np.array_equal(out, f)


def test_blend_with_original():
    fx = FormulaEffect(expression='255 - frame', blend=1.0,
                       enabled=True, chance=1.0)
    f = make_frame()
    out = fx.apply(f, make_seg(), False)
    # blend=1.0 → output is original
    assert np.array_equal(out, f)


def test_uses_coordinate_grids():
    fx = FormulaEffect(expression='clip(x, 0, 255).astype(np.uint8)[:, :, None] + 0*frame',
                       enabled=True, chance=1.0)
    f = make_frame()
    out = fx.apply(f, make_seg(), False)
    assert out.shape == f.shape
