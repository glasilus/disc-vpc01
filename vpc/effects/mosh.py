"""True codec-level datamosh (PyAV / libavcodec MPEG-4).

OpticalFlowEffect in core.py APPROXIMATES the datamosh look by warping the
previous frame along optical flow. This module reproduces the real thing:
an actual MPEG-4 encoder + decoder pair runs in-process, and the effect
performs the exact bitstream surgery classic datamosh tools (aviglitch,
datamosher, autodatamosh) do on AVI files:

  melt  - at a cut the encoder is forced to emit an I-frame and that
          I-frame is thrown away. The decoder keeps applying the following
          P-frames' motion vectors and DCT residuals to the stale reference
          picture, so the old scene drags and smears along the new scene's
          motion and only "heals" patch by patch where the encoder emits
          intra macroblocks. This is the canonical I-frame-drop mosh.
  bloom - one P-frame packet is decoded repeatedly, so its motion field
          compounds frame after frame (the classic P-frame-duplication
          "bloom" where moving regions grow out of themselves).

The smear, the 16x16 block structure and the patchy self-healing all come
out of libavcodec's real motion compensation - nothing is simulated.

The effect is a normal chain effect: it consumes one frame and emits one
frame, so it behaves identically in draft, preview, final and passthrough
modes and never desyncs audio. The codec pair only exists while a mosh
episode is running; outside an episode frames pass through untouched at
zero cost.
"""
from __future__ import annotations

import random
from fractions import Fraction
from typing import Optional

import cv2
import numpy as np

from vpc.analyzer import Segment, SegmentType
from .base import BaseEffect, _ensure_uint8

try:
    import av
    from av.video.frame import PictureType as _PictureType
    _AV_OK = True
except ImportError:                                    # pragma: no cover
    av = None
    _PictureType = None
    _AV_OK = False

# Nominal fps for encoder rate control. The chain does not know the real
# output fps; this only scales the target bitrate, never frame timing.
_NOMINAL_FPS = 24


class TrueDatamoshEffect(BaseEffect):
    """Real I-frame-drop / P-frame-duplication datamosh.

    Per-SEGMENT gating (like the engine's cut logic, unlike the per-frame
    base class): each new segment whose type matches and whose chance roll
    passes starts (or extends) a mosh episode. Consecutive firing segments
    chain into one continuous melt - the datamix look. The first segment
    that fails the roll ends the episode, which reads exactly like a moshed
    stream hitting a surviving keyframe: the picture snaps back clean.
    """
    trigger_types = [SegmentType.NOISE, SegmentType.SUSTAIN,
                     SegmentType.IMPACT, SegmentType.DROP]

    MODES = ('melt', 'bloom', 'hybrid')

    def __init__(self, mode: str = 'melt', bloom_frames: int = 8,
                 crunch: float = 0.35, **kw):
        super().__init__(**kw)
        self.mode = mode if mode in self.MODES else 'melt'
        self.bloom_frames = max(2, int(bloom_frames))
        self.crunch = float(min(1.0, max(0.0, crunch)))

        self.prev_frame: Optional[np.ndarray] = None
        self._seg_id = None
        self._enc = None
        self._dec = None
        # (encode_w, encode_h) - even-floored; (out_w, out_h) - chain size.
        self._enc_size = None
        self._out_size = None
        self._last_out: Optional[np.ndarray] = None
        self._bloom_pkt: Optional[bytes] = None
        self._bloom_left = 0
        self._await_bloom_pkt = False
        self._fails = 0
        self._broken = not _AV_OK
        if not _AV_OK:
            print('[FX-FAIL] TrueDatamoshEffect: PyAV (av) is not installed; '
                  'effect is inert.')

    # ── chain contract ───────────────────────────────────────────────────
    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        # Stateful override (same pattern as OpticalFlowEffect): the previous
        # frame must be tracked on EVERY frame so an episode can prime the
        # decoder with the picture that was actually on screen at the cut.
        seg_id = (seg.t_start, seg.t_end, seg.type)
        new_seg = seg_id != self._seg_id
        self._seg_id = seg_id

        if self._broken or not self.enabled:
            self.prev_frame = frame
            return frame
        if self.prev_frame is not None and self.prev_frame.shape != frame.shape:
            # Resolution changed under us (new render / new chain) - the
            # codec pair is sized for the old frames, drop the episode.
            self._teardown()
            self.prev_frame = frame
            return frame

        try:
            out = self._step(frame, seg, draft, new_seg)
        except Exception as e:
            # av raises library-specific errors (av.error.*); any codec
            # failure must never kill the render loop.
            self._fails += 1
            print(f'[FX-FAIL] TrueDatamoshEffect: {e!r} (fail {self._fails})')
            self._teardown()
            if self._fails >= 3:
                self._broken = True
                print('[FX-FAIL] TrueDatamoshEffect disabled after repeated '
                      'codec failures.')
            out = frame
        self.prev_frame = frame
        return out

    def _apply(self, frame, seg, draft):  # pragma: no cover - unused
        # BaseEffect requires the hook; gating happens in apply() instead.
        return frame

    # ── episode state machine ────────────────────────────────────────────
    def _step(self, frame: np.ndarray, seg: Segment, draft: bool,
              new_seg: bool) -> np.ndarray:
        if new_seg:
            fire = (seg.type in self.trigger_types
                    and random.random() <= self.chance)
            if not fire:
                # Clean resync - identical to a kept keyframe in a real mosh.
                self._teardown()
                return frame
            return self._cut(frame, seg, draft)
        if self._enc is None:
            return frame
        return self._continue(frame)

    def _pick_event(self) -> str:
        if self.mode == 'hybrid':
            return random.choice(('melt', 'bloom', 'both'))
        return self.mode

    def _cut(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        """First frame of a firing segment - the moment of bitstream surgery."""
        if self._enc is None:
            self._start_episode(frame, draft)
        event = self._pick_event()

        # Arm the bloom before encoding so the very first P of the new
        # segment (the one with the hardest motion mismatch) is captured.
        if event in ('bloom', 'both'):
            n = 2 + int(round(self.scaled_intensity(seg)
                              * (self.bloom_frames - 2)))
            self._bloom_left = n
            self._bloom_pkt = None
            self._await_bloom_pkt = True

        packets = self._encode(frame, force_i=True)
        if event == 'bloom':
            # Pure bloom keeps the stream intact: the decoder resyncs on the
            # I-frame, then the next P gets duplicated in _continue().
            return self._feed(packets, frame)
        # melt / both: the forced I-frame is thrown away. The decoder holds
        # the previous picture for this one frame (real moshes do exactly
        # this where the keyframe was cut out) and melts from the next P on.
        return self._held(frame)

    def _continue(self, frame: np.ndarray) -> np.ndarray:
        packets = self._encode(frame, force_i=False)

        if self._await_bloom_pkt:
            p_pkts = [p for p in packets if not p.is_keyframe]
            if p_pkts:
                self._bloom_pkt = bytes(p_pkts[0])
                self._await_bloom_pkt = False

        if self._bloom_left > 0 and self._bloom_pkt is not None:
            # Feed the same P packet again: its motion vectors compound on
            # the decoder's current reference. The fresh packets for this
            # input frame are discarded, which keeps the decoder reference
            # diverging - part of the authentic look.
            self._bloom_left -= 1
            pkt = av.Packet(self._bloom_pkt)
            return self._feed([pkt], frame)

        # Any keyframe the encoder sneaks in mid-episode would resync the
        # picture and kill the melt - drop it, exactly like mosh tools strip
        # every I-frame in the affected range.
        p_pkts = [p for p in packets if not p.is_keyframe]
        if not p_pkts and packets:
            return self._held(frame)
        return self._feed(p_pkts, frame)

    # ── codec plumbing ───────────────────────────────────────────────────
    def _start_episode(self, frame: np.ndarray, draft: bool) -> None:
        h, w = frame.shape[:2]
        ew, eh = w & ~1, h & ~1          # yuv420p needs even dimensions
        self._enc_size = (ew, eh)
        self._out_size = (w, h)

        enc = av.CodecContext.create('mpeg4', 'w')
        enc.width, enc.height = ew, eh
        enc.pix_fmt = 'yuv420p'
        enc.time_base = Fraction(1, _NOMINAL_FPS)
        enc.framerate = Fraction(_NOMINAL_FPS, 1)
        # One long P-chain: huge GOP plus disabled scene-cut detection, the
        # same stream shape prepare_datamosh_source() builds with ffmpeg.
        enc.gop_size = 10 ** 6
        # crunch maps to bits per pixel: 0.60 bpp is visually clean, 0.05
        # bpp is heavy macroblock soup. Lower bitrate = blockier smear.
        bpp = 0.60 - 0.55 * self.crunch
        enc.bit_rate = max(64_000, int(ew * eh * _NOMINAL_FPS * bpp))
        opts = {
            'sc_threshold': '1000000000',  # never auto-insert I on cuts
            'flags': '+mv4',               # 8x8 vectors: finer, soupier drag
        }
        if not draft:
            opts['mbd'] = 'rd'             # better ME = smoother melt
        enc.options = opts
        enc.open()

        dec = av.CodecContext.create('mpeg4', 'r')
        dec.open()
        self._enc, self._dec = enc, dec
        self._last_out = None
        self._bloom_pkt = None
        self._bloom_left = 0
        self._await_bloom_pkt = False

        # Prime both contexts with the picture currently on screen: the
        # in-band headers of this I-frame configure the decoder, and its
        # payload becomes the stale reference everything will melt from.
        base = self.prev_frame if self.prev_frame is not None else frame
        self._feed(self._encode(base, force_i=True), base)

    def _encode(self, frame: np.ndarray, force_i: bool) -> list:
        ew, eh = self._enc_size
        arr = frame
        if (frame.shape[1], frame.shape[0]) != (ew, eh):
            arr = cv2.resize(frame, (ew, eh))
        arr = np.ascontiguousarray(arr)
        vf = av.VideoFrame.from_ndarray(arr, format='rgb24')
        vf = vf.reformat(format='yuv420p')
        if force_i:
            vf.pict_type = _PictureType.I
        return list(self._enc.encode(vf))

    def _feed(self, packets: list, fallback: np.ndarray) -> np.ndarray:
        out = None
        for pkt in packets:
            for df in self._dec.decode(pkt):
                out = df.to_ndarray(format='rgb24')
        if out is None:
            return self._held(fallback)
        w, h = self._out_size
        if (out.shape[1], out.shape[0]) != (w, h):
            out = cv2.resize(out, (w, h))
        out = _ensure_uint8(out)
        self._last_out = out
        return out

    def _held(self, fallback: np.ndarray) -> np.ndarray:
        return self._last_out if self._last_out is not None else fallback

    def _teardown(self) -> None:
        self._enc = None
        self._dec = None
        self._last_out = None
        self._bloom_pkt = None
        self._bloom_left = 0
        self._await_bloom_pkt = False
