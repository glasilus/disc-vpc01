"""Tests for PaintCanvasEffect class."""
import pytest
import numpy as np
from vpc.analyzer import Segment, SegmentType
from vpc.effects.paint import PaintCanvasEffect, decode_paint_canvas


def make_seg(type=SegmentType.SUSTAIN, intensity=0.5):
    return Segment(0.0, 1.0, 1.0, type, intensity, 0.5, 0.3, 0.1)


def test_decode_paint_canvas():
    # Empty string should return None
    assert decode_paint_canvas("") is None
    assert decode_paint_canvas(None) is None

    # Invalid base64 string should return None
    assert decode_paint_canvas("invalid_base64") is None


def test_paint_canvas_effect_no_mask():
    # When canvas_mask is None, it should return the frame unchanged
    frame = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    fx = PaintCanvasEffect(canvas_mask=None, enabled=True, chance=1.0)
    seg = make_seg()
    result = fx.apply(frame, seg, draft=False)
    assert np.array_equal(result, frame)


def test_paint_canvas_effect_modes():
    frame = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
    
    # Create a simple binary mask of shape (100, 100)
    # Background is 255 (white), strokes are 0 (black). Let's make a stroke at (10, 10) to (20, 20)
    mask = np.full((100, 100), 255, dtype=np.uint8)
    mask[10:30, 10:30] = 0

    seg = make_seg()

    # Test overlay mode
    fx_overlay = PaintCanvasEffect(canvas_mask=mask, mode='overlay', color_r=255, color_g=0, color_b=0, enabled=True, chance=1.0)
    res_overlay = fx_overlay.apply(frame, seg, draft=False)
    assert res_overlay.shape == frame.shape
    # Check that overlay pixels are red
    assert np.all(res_overlay[15, 15] == [255, 0, 0])
    # Check that background pixels are unchanged
    assert np.array_equal(res_overlay[50, 50], frame[50, 50])

    # Test lag mode
    fx_lag = PaintCanvasEffect(canvas_mask=mask, mode='lag', delay_frames=5, enabled=True, chance=1.0)
    
    # Apply first frame
    frame1 = np.full((100, 100, 3), 10, dtype=np.uint8)
    res1 = fx_lag.apply(frame1, seg, draft=False)
    
    # Apply subsequent frames to build history
    for i in range(2, 6):
        frame_i = np.full((100, 100, 3), i * 10, dtype=np.uint8)
        res_i = fx_lag.apply(frame_i, seg, draft=False)
    
    # Current frame is frame5 (value 50), delayed frame should be frame1 (value 10)
    # The result in stroke area should be 10, in background should be 50.
    assert np.all(res_i[15, 15] == [10, 10, 10])
    assert np.all(res_i[50, 50] == [50, 50, 50])

    # Test warp_video mode
    fx_warp_v = PaintCanvasEffect(canvas_mask=mask, mode='warp_video', warp_intensity=0.5, enabled=True, chance=1.0)
    res_warp_v = fx_warp_v.apply(frame, seg, draft=False)
    assert res_warp_v.shape == frame.shape
    assert res_warp_v.dtype == np.uint8

    # Test lag_warp mode
    fx_lag_warp = PaintCanvasEffect(canvas_mask=mask, mode='lag_warp', delay_frames=5, warp_intensity=0.5, enabled=True, chance=1.0)
    
    # Build history
    for i in range(1, 6):
        frame_i = np.full((100, 100, 3), i * 10, dtype=np.uint8)
        res_i = fx_lag_warp.apply(frame_i, seg, draft=False)
    
    assert res_i.shape == frame.shape
    assert res_i.dtype == np.uint8
