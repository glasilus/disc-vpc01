"""Tests for the audio-mastered preview player's sync core.

These exercise the drift-free design without any real audio device: the pure
clock→frame mapping, the looping audio fill (gapless wrap + live volume +
freeze-on-pause), the wall-clock fallback, and the video slave step that keys
every presented frame off the master clock.
"""
import numpy as np
import pytest
import cv2

from vpc.render.preview_player import (
    frame_for_time, _AudioClock, _WallClock, PreviewPlayer,
)


# ── frame_for_time ────────────────────────────────────────────────────────
def test_frame_for_time_maps_and_wraps():
    assert frame_for_time(0.0, 24, 120) == 0
    assert frame_for_time(1.0, 24, 120) == 24
    assert frame_for_time(0.5, 24, 120) == 12
    # wraps modulo nframes at the loop boundary
    assert frame_for_time(5.0, 24, 120) == 0
    assert frame_for_time(5.0 + 1 / 24, 24, 120) == 1


def test_frame_for_time_degenerate_guards():
    assert frame_for_time(3.0, 24, 0) == 0
    assert frame_for_time(3.0, 0, 120) == 0


# ── _AudioClock: gapless looping + volume + pause ─────────────────────────
def _ramp_clock():
    data = np.arange(10, dtype=np.float32).reshape(10, 1)
    return _AudioClock(data, sr=10)


def test_audio_fill_advances_and_loops_gaplessly():
    clk = _ramp_clock()
    out = np.zeros((4, 1), np.float32)

    clk._fill(out, 4)
    assert list(out[:, 0]) == [0, 1, 2, 3]
    assert clk.position_seconds() == pytest.approx(4 / 10)

    clk._fill(out, 4)
    assert list(out[:, 0]) == [4, 5, 6, 7]

    # Third block must wrap without a gap: 8, 9, then back to 0, 1.
    clk._fill(out, 4)
    assert list(out[:, 0]) == [8, 9, 0, 1]
    assert clk.position_seconds() == pytest.approx(2 / 10)


def test_audio_fill_applies_live_volume():
    clk = _ramp_clock()
    clk.set_volume(0.5)
    out = np.zeros((4, 1), np.float32)
    clk._fill(out, 4)
    assert list(out[:, 0]) == [0.0, 0.5, 1.0, 1.5]


def test_audio_pause_emits_silence_and_freezes_cursor():
    clk = _ramp_clock()
    out = np.zeros((4, 1), np.float32)
    clk._fill(out, 4)                      # cursor → 4
    clk.set_paused(True)
    out[:] = 7.0
    clk._fill(out, 4)
    assert list(out[:, 0]) == [0, 0, 0, 0]         # silence while paused
    assert clk.position_seconds() == pytest.approx(4 / 10)   # cursor frozen
    clk.set_paused(False)
    clk._fill(out, 4)
    assert list(out[:, 0]) == [4, 5, 6, 7]         # resumes exactly where frozen


# ── _WallClock: pause freeze + wrap ───────────────────────────────────────
def test_wall_clock_pause_freezes_position():
    clk = _WallClock(duration=5.0)
    clk.set_paused(True)
    p0 = clk.position_seconds()
    import time
    time.sleep(0.05)
    assert clk.position_seconds() == pytest.approx(p0, abs=1e-3)
    assert clk.duration == 5.0


# ── video slave: every frame keyed off the master clock ───────────────────
class _ManualClock:
    """Injectable master clock with an explicitly settable position."""
    def __init__(self, duration):
        self.duration = duration
        self.t = 0.0
    def position_seconds(self):
        return self.t
    def set_paused(self, p):
        pass
    def set_volume(self, v):
        pass


def _tiny_mp4(tmp_path, frames=20, w=32, h=24):
    p = str(tmp_path / 'clip.mp4')
    vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'mp4v'), 24.0, (w, h))
    if not vw.isOpened():
        return None
    for i in range(frames):
        vw.write(np.full((h, w, 3), i * 10 % 256, np.uint8))
    vw.release()
    import os
    return p if os.path.getsize(p) > 0 else None


def test_video_advance_tracks_clock(tmp_path):
    path = _tiny_mp4(tmp_path)
    if path is None:
        pytest.skip('no mp4 writer codec available in this environment')
    clk = _ManualClock(duration=20 / 24)
    player = PreviewPlayer(path, on_frame=lambda rgb: None, clock=clk)
    player._fps = 24.0
    player._nframes = 20
    cap = cv2.VideoCapture(path)
    try:
        # Jump the clock to ~frame 10; the slave must decode up to it.
        clk.t = 10 / 24
        frame, cur, target = player._advance(cap, -1)
        assert target == 10
        assert cur == 10
        assert frame is not None

        # Advance a little — sequential, no seek.
        clk.t = 12 / 24
        frame, cur, target = player._advance(cap, cur)
        assert target == 12 and cur == 12

        # Clock wraps back to the loop start → slave seeks to 0.
        clk.t = 1 / 24
        frame, cur, target = player._advance(cap, cur)
        assert target == 1 and cur == 1
    finally:
        cap.release()


def test_video_advance_is_pure_function_of_clock(tmp_path):
    """Drift-free invariant: the presented frame index is always
    frame_for_time(clock), independent of how many steps were taken."""
    path = _tiny_mp4(tmp_path)
    if path is None:
        pytest.skip('no mp4 writer codec available in this environment')
    clk = _ManualClock(duration=20 / 24)
    player = PreviewPlayer(path, on_frame=lambda rgb: None, clock=clk)
    player._fps = 24.0
    player._nframes = 20
    cap = cv2.VideoCapture(path)
    try:
        cur = -1
        for t in [0.0, 0.1, 0.2, 0.05, 0.3, 0.9, 0.0]:
            clk.t = t
            _frame, cur, target = player._advance(cap, cur)
            assert cur == target == frame_for_time(t, 24.0, 20)
    finally:
        cap.release()
