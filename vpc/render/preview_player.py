"""Audio-mastered A/V preview player.

The problem this solves
-----------------------
The previous preview ran video and audio as two free-running threads with
*independent clocks and no resync*. The video loop scheduled frames off wall
time (and did a heavy per-frame LANCZOS resize, so it could not always hold
realtime), while the audio loop played the whole buffer via one blocking
``sd.play`` and repeated. Their loop periods differed, and because each loop
restarted from its own anchor, the difference **accumulated every cycle** — the
audio progressively drifted out of sync with the video and got worse the longer
you watched.

The fix: a single master clock, with the video slaved to it
-----------------------------------------------------------
Standard A/V sync uses the audio hardware clock as the master. Here an
``sd.OutputStream`` callback loops the decoded audio *gaplessly* (a sample
cursor mod N) and exposes that cursor as the master clock. The video is then a
**pure function of the clock**: the presented frame is always
``int(clock_seconds * fps)``. That is what makes drift impossible — there is no
independent video clock to accumulate error. If the video decode can't keep up
it simply drops frames to catch up; if it runs ahead it waits; and at the loop
boundary the audio cursor wraps and the video seeks back to 0, so both restart
together, perfectly re-aligned, every cycle.

When no audio backend/track is available, a monotonic wall clock stands in as
the master with the identical slaving contract, so the video path is unchanged.

The module is deliberately decoupled from Tk: ``PreviewPlayer`` calls an
``on_frame(rgb_ndarray)`` callback from its worker thread; the GUI wraps that
into its own thread-marshalling. The pure ``frame_for_time`` helper and the
isolated audio ``_fill`` are unit-tested without any real audio device.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np

try:
    import sounddevice as _sd
    import soundfile as _sf
    _AUDIO_OK = True
except Exception:                                    # pragma: no cover - env dependent
    _sd = None
    _sf = None
    _AUDIO_OK = False


def frame_for_time(t: float, fps: float, nframes: int) -> int:
    """Master-clock time (seconds) → video frame index.

    Pure and total. This single mapping is the heart of the sync design: the
    video frame is a function of the audio clock, so it cannot drift.
    """
    if nframes <= 0 or fps <= 0:
        return 0
    return int(t * fps) % nframes


class _AudioClock:
    """A looping audio output whose sample cursor is the master clock.

    The buffer is played gaplessly (cursor advances mod N inside the PortAudio
    callback), volume is read live each block, and pause freezes the cursor and
    emits silence — so the master clock (and therefore the video) freezes too.
    """

    def __init__(self, data: np.ndarray, sr: int):
        if data.ndim == 1:
            data = data[:, None]
        self._data = np.ascontiguousarray(data, dtype=np.float32)
        self._n = int(len(self._data))
        self._ch = int(self._data.shape[1])
        self.sr = int(sr)
        self._cursor = 0
        self._paused = False
        self.volume = 1.0
        self._lock = threading.Lock()
        self._stream = None

    @property
    def duration(self) -> float:
        return self._n / self.sr if self.sr else 0.0

    def position_seconds(self) -> float:
        with self._lock:
            return self._cursor / self.sr if self.sr else 0.0

    def _fill(self, out: np.ndarray, frames: int) -> None:
        """Fill ``out`` (frames × channels) from the looped buffer.

        Isolated from PortAudio so it can be unit-tested directly. Advances the
        cursor mod N unless paused (then emits silence and holds the cursor).
        """
        with self._lock:
            if self._paused or self._n == 0:
                out[:] = 0.0
                return
            idx = (np.arange(frames) + self._cursor) % self._n
            np.multiply(self._data[idx], self.volume, out=out)
            self._cursor = int((self._cursor + frames) % self._n)

    def _callback(self, outdata, frames, time_info, status):  # pragma: no cover
        self._fill(outdata, frames)

    def start(self) -> None:
        self._stream = _sd.OutputStream(
            samplerate=self.sr, channels=self._ch, dtype='float32',
            callback=self._callback)
        self._stream.start()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)

    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, float(v))

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:                        # pragma: no cover
                pass
            self._stream = None


class _WallClock:
    """Monotonic fallback master clock, wrapped to a fixed duration.

    Used when there is no audio backend or the track failed to decode. Same
    ``position_seconds`` / ``set_paused`` contract as :class:`_AudioClock`, so
    the video slave loop is identical.
    """

    def __init__(self, duration: float):
        self._duration = max(1e-3, float(duration))
        self._t0 = time.monotonic()
        self._paused = False
        self._pause_t0 = 0.0
        self._lock = threading.Lock()

    @property
    def duration(self) -> float:
        return self._duration

    def position_seconds(self) -> float:
        with self._lock:
            base = self._pause_t0 if self._paused else time.monotonic()
            return (base - self._t0) % self._duration

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            paused = bool(paused)
            if paused and not self._paused:
                self._pause_t0 = time.monotonic()
                self._paused = True
            elif not paused and self._paused:
                self._t0 += time.monotonic() - self._pause_t0
                self._paused = False

    def set_volume(self, v: float) -> None:
        pass

    def start(self) -> None:
        with self._lock:
            self._t0 = time.monotonic()
            self._paused = False

    def stop(self) -> None:
        pass


class PreviewPlayer:
    """Play a rendered preview clip with audio-mastered A/V sync, looping.

    Parameters
    ----------
    video_path : str
        Path to the rendered preview video (has both streams).
    on_frame : callable(np.ndarray)
        Called from the worker thread with each RGB frame to present
        (already resized to ``size``). The caller marshals it onto its UI
        thread.
    size : (int, int)
        Target (width, height) for presented frames.
    wav_path : str | None
        PCM wav of the clip's audio for the master clock. When absent (or the
        audio backend is missing) a wall clock is used and playback is silent.
    log : callable(str)
        Status logger.
    clock : object | None
        Test seam: inject a master clock exposing ``position_seconds()``. When
        ``None`` the player builds an audio or wall clock itself.
    """

    def __init__(self, video_path: str,
                 on_frame: Callable[[np.ndarray], None],
                 size=(640, 360), wav_path: Optional[str] = None,
                 log: Callable[[str], None] = lambda m: None,
                 clock=None):
        self.video_path = video_path
        self.on_frame = on_frame
        self.W, self.H = int(size[0]), int(size[1])
        self.wav_path = wav_path
        self.log = log
        self._clock = clock
        self._external_clock = clock is not None
        self._stop = threading.Event()
        self._thread = None
        self._fps = 24.0
        self._nframes = 1
        self._volume = 0.8

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> bool:
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            cap.release()
            self.log('ERROR: cannot open preview video.')
            return False
        self._fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
        vframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()

        if self._clock is None:
            self._clock = self._build_clock(vframes)

        # Wrap period: prefer the real frame count; else derive from the master
        # clock duration. Both describe the same clip, so target = int(t*fps)
        # stays in range and the modulo only absorbs the final-frame rounding.
        self._nframes = vframes or int(round(self._clock.duration * self._fps)) or 1

        self._stop.clear()
        if not self._external_clock:
            try:
                self._clock.start()
            except Exception as e:
                self.log(f'WARNING: audio start failed ({e}); silent preview.')
                self._clock = _WallClock((vframes / self._fps) if vframes else 5.0)
                self._clock.start()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _build_clock(self, vframes: int):
        if _AUDIO_OK and self.wav_path:
            try:
                data, sr = _sf.read(self.wav_path, dtype='float32')
                clk = _AudioClock(data, sr)
                clk.set_volume(self._volume)
                return clk
            except Exception as e:
                self.log(f'WARNING: preview audio decode failed ({e}); silent preview.')
        dur = (vframes / self._fps) if (vframes and self._fps) else 5.0
        return _WallClock(dur)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._clock is not None and not self._external_clock:
            try:
                self._clock.stop()
            except Exception:                        # pragma: no cover
                pass

    # ── transport ────────────────────────────────────────────────────────
    def pause(self) -> None:
        self._paused = True
        if self._clock is not None:
            self._clock.set_paused(True)

    def resume(self) -> None:
        self._paused = False
        if self._clock is not None:
            self._clock.set_paused(False)

    def is_paused(self) -> bool:
        return getattr(self, '_paused', False)

    def toggle_pause(self) -> bool:
        if self.is_paused():
            self.resume()
        else:
            self.pause()
        return self.is_paused()

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, float(v))
        if self._clock is not None:
            self._clock.set_volume(self._volume)

    # ── video slave ──────────────────────────────────────────────────────
    def _advance(self, cap, cur: int):
        """Decode forward to the frame the master clock currently demands.

        Returns ``(frame_or_None, new_cur, target)``. Drops intermediate frames
        when behind (keeps only the latest); seeks to 0 when the clock wraps.
        Isolated for unit testing.
        """
        t = self._clock.position_seconds()
        target = frame_for_time(t, self._fps, self._nframes)
        if target < cur:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            cur = -1
        frame = None
        guard = 0
        while cur < target and guard < self._nframes + 2:
            ok, f = cap.read()
            guard += 1
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                cur = -1
                break
            frame = f
            cur += 1
        return frame, cur, target

    def _run(self) -> None:
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            self.log('ERROR: preview video reopen failed.')
            return
        frame_dur = 1.0 / self._fps if self._fps else 1.0 / 24.0
        cur = -1
        try:
            while not self._stop.is_set():
                frame, cur, _target = self._advance(cap, cur)
                if frame is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    if (self.W, self.H) != (rgb.shape[1], rgb.shape[0]):
                        rgb = cv2.resize(rgb, (self.W, self.H),
                                         interpolation=cv2.INTER_LINEAR)
                    self.on_frame(rgb)
                # Sleep until the next frame is due by the master clock. If we
                # are behind (sleep<=0) loop straight back so _advance drops
                # frames to catch up rather than falling further behind.
                nxt = (cur + 1) * frame_dur
                sleep = nxt - self._clock.position_seconds()
                self._stop.wait(min(sleep, 0.1) if sleep > 0 else 0.001)
        finally:
            cap.release()
