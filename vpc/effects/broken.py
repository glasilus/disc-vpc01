"""Broken-decoder family: effects that look like the decoder lost track
of where data is meant to go, rather than like decorative glitch art.

Currently:
  - SelfCannibalizeEffect: random rectangles in the frame are filled
    with a downscaled copy of the *whole current frame*, so the picture
    appears to contain miniature copies of itself in wrong places. With
    higher intensity those miniatures are themselves overlaid recursively.
  - VSyncRollEffect: vertical roll with a visible torn seam — the CRT
    "lost vertical hold" look.
  - PFrameLagEffect: stateful motion-delta carryover. The decoder seems
    to be holding a P-frame too long, so motion smears with the past
    frame instead of resolving cleanly.
  - BitFlipEffect: raw bytes of the frame are XORed against a sparse
    mask whose set bits are picked from a random plane (LSB ... MSB).
    Genuine "bit rot" appearance — quantised colour shifts in plateaus.
  - WrongMotionVectorEffect: ~10% of 16x16 macroblocks copy their
    contents from a block offset by 32-64 px in the same frame, as if
    the codec lost a motion vector and grabbed the wrong reference.
"""
from __future__ import annotations

import random
import cv2
import numpy as np

from .base import BaseEffect, _ensure_uint8


class SelfCannibalizeEffect(BaseEffect):
    """Frame-eats-itself recursion.

    The frame is scaled down to a thumbnail, then 1..N random rectangles
    inside the same frame are painted with that thumbnail. With high
    intensity the operation recurses: the thumbnail itself is processed
    again before being pasted, so each rectangle contains a smaller copy
    that contains an even smaller copy, etc.

    Reads as a memory-corruption / texture-atlas-leak pattern — the
    frame's pixels appear in places they have no business being, and
    those places contain the same self-similarity, like the GPU keeps
    reading from the same source pointer.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 max_depth: int = 3):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        # `max_depth` capped on construction; engine never adapts it.
        self.max_depth = max(1, min(4, int(max_depth)))

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]
        if h < 16 or w < 16:
            return frame

        # Number of cannibal rectangles scales with intensity (1..8) so
        # low values are subtle and high values fill the frame.
        n_rects = max(1, int(round(intensity * 8)))
        # Recursion depth scales separately so the user sees self-similar
        # nesting only past the midpoint of the slider.
        depth = max(1, int(round(intensity * self.max_depth)))

        out = frame.copy()
        thumb = self._make_thumb(out, depth, draft)
        if thumb is None:
            return frame

        for _ in range(n_rects):
            self._paste_rect(out, thumb, intensity)
        return _ensure_uint8(out)

    def _make_thumb(self, frame: np.ndarray, depth: int,
                    draft: bool) -> np.ndarray:
        """Build the recursive thumbnail used as paste source.

        At depth=1 it's just a small downscale. At depth>1 we recurse:
        downscale, then in turn paint a few rectangles inside the thumb
        with an even smaller version of itself before scaling back up.
        That nesting is what makes the result feel "infinite" rather
        than "just a picture-in-picture".
        """
        h, w = frame.shape[:2]
        # Scale factor per level: ~0.4 keeps things visible, but smaller
        # for draft so it doesn't dominate cheap previews.
        scale = 0.35 if not draft else 0.5
        thumb_w = max(8, int(w * scale))
        thumb_h = max(8, int(h * scale))
        thumb = cv2.resize(frame, (thumb_w, thumb_h),
                           interpolation=cv2.INTER_AREA)

        if depth <= 1 or draft:
            return thumb

        # Recurse. We scale our nested thumb by 0.4 of *this* thumb so
        # each level shrinks meaningfully and the deepest one is tiny.
        for _ in range(2):
            inner = self._make_thumb(thumb, depth - 1, draft)
            if inner is None:
                continue
            ih, iw = inner.shape[:2]
            x = random.randint(0, max(0, thumb_w - iw))
            y = random.randint(0, max(0, thumb_h - ih))
            thumb[y:y + ih, x:x + iw] = inner
        return thumb

    def _paste_rect(self, frame: np.ndarray, thumb: np.ndarray,
                    intensity: float) -> None:
        """Paste `thumb` (or a scaled variant of it) into a random
        rectangle inside `frame`. Mutates frame in-place.
        """
        h, w = frame.shape[:2]
        th, tw = thumb.shape[:2]
        # Random secondary scale per paste so individual cannibals vary
        # in size — without this, every paste is identical and reads as
        # "deliberate decoration", not corruption.
        rscale = 0.4 + random.random() * 0.9
        ph = max(4, min(h, int(th * rscale)))
        pw = max(4, min(w, int(tw * rscale)))
        if ph >= h or pw >= w:
            return
        scaled = cv2.resize(thumb, (pw, ph), interpolation=cv2.INTER_AREA)
        x = random.randint(0, w - pw)
        y = random.randint(0, h - ph)
        # Light alpha-blend at low intensity so subtle settings don't
        # punch out. At intensity >= ~0.7 we paste opaque for full
        # corruption look.
        if intensity < 0.7:
            alpha = 0.55 + intensity * 0.5
            existing = frame[y:y + ph, x:x + pw].astype(np.float32)
            mixed = existing * (1.0 - alpha) + scaled.astype(np.float32) * alpha
            frame[y:y + ph, x:x + pw] = np.clip(mixed, 0, 255).astype(np.uint8)
        else:
            frame[y:y + ph, x:x + pw] = scaled


# ──────────────────────────────────────────────────────────────────────
#   B2 — VSyncRoll
# ──────────────────────────────────────────────────────────────────────


class VSyncRollEffect(BaseEffect):
    """Vertical-hold loss: the frame is split horizontally and the two
    halves are stacked in the wrong order, with a torn black seam at
    the cut. The cut position drifts over time so the seam crawls up
    the picture, exactly like an old CRT losing vsync.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self._t = 0

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]
        self._t += 1

        # Cut row drifts by `intensity * 8` px per frame; the modulo by h
        # makes it wrap around cleanly. Floor 1 row guards against h=0.
        speed = max(1, int(round(intensity * 8)))
        cut = (self._t * speed) % h
        if cut == 0:
            cut = 1
        out = np.vstack([frame[cut:], frame[:cut]])
        # Black tear band at the seam — a few rows tall, also drifting.
        # Width grows with intensity so low values look like a clean
        # roll, high values like a heavily corrupted vsync signal.
        seam_h = max(1, int(intensity * 5))
        seam_y = h - cut
        if 0 <= seam_y < h:
            y0 = max(0, seam_y - seam_h // 2)
            y1 = min(h, seam_y + seam_h // 2 + 1)
            out[y0:y1] = 0
        return _ensure_uint8(out)


# ──────────────────────────────────────────────────────────────────────
#   B4 — PFrameLag
# ──────────────────────────────────────────────────────────────────────


class PFrameLagEffect(BaseEffect):
    """Stateful motion-delta carryover. We hold the previous frame and
    output `prev + alpha * (current - prev)` so motion only PARTIALLY
    resolves each frame — exactly the look of a video stream where the
    decoder is dropping P-frames and the picture lags behind.

    Higher intensity = lower alpha = more lag (image takes more frames
    to "catch up" to the present).
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self._prev: np.ndarray | None = None

    def apply(self, frame, seg, draft):
        # We override apply() to keep the prev buffer fresh on frames
        # where the effect doesn't actually run (chance roll failed,
        # wrong segment, disabled). Without that, the lag would compound
        # against an outdated baseline and visibly snap when re-armed.
        #
        # We split the logic in two: a structural-gate check (which
        # avoids the float32 copy entirely when the effect is disabled
        # or simply the wrong segment) and a tracker-update for the
        # frame-by-frame chance miss case. The previous identity check
        # `out is frame` was fragile because BaseEffect.apply also
        # returns `frame` on the exception path; the new structure is
        # explicit about which predicate decided.
        if not self.enabled or seg.type not in self.trigger_types:
            # Structurally off — no _apply call, no float copy. Drop
            # any stale prev so a future re-enable starts from a clean
            # slate against whatever the new "now" looks like.
            self._prev = None
            return frame
        # Effect is structurally armed; let BaseEffect handle the
        # chance roll + exception capture.
        out = super().apply(frame, seg, draft)
        # If _apply ran successfully, it updated self._prev to the
        # blended output. If chance failed OR _apply raised, _prev was
        # NOT touched and would compound against stale data on next
        # fire. In both cases, refresh prev to the live frame so the
        # next successful fire produces a meaningful (not catastrophic)
        # smear.
        if out is frame:
            self._prev = frame.astype(np.float32)
        return out

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0 or self._prev is None or self._prev.shape != frame.shape:
            self._prev = frame.astype(np.float32)
            return frame
        # alpha in [0.15, 0.85]: high intensity = small alpha = lots of lag.
        alpha = 1.0 - (0.15 + intensity * 0.7)
        cur = frame.astype(np.float32)
        out = self._prev * (1.0 - alpha) + cur * alpha
        # Update prev to the BLENDED output, not the raw current — that's
        # what makes the lag compound across frames instead of resetting.
        self._prev = out
        return out


# ──────────────────────────────────────────────────────────────────────
#   B8 — BitFlip
# ──────────────────────────────────────────────────────────────────────


class BitFlipEffect(BaseEffect):
    """Sparse byte-XOR corruption — the "bit rot" look.

    A boolean mask of density `intensity * 0.05` is generated, an
    intensity-driven bit-plane (LSB ... MSB) is selected, and every
    byte where the mask is True has that bit toggled. Result: the
    image is mostly intact, but plateaus of solid colour show
    quantised XOR shifts that look exactly like an SD card with
    failing flash cells.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        # Density caps at 5% of bytes — past that the picture stops
        # being recognisable. The cap keeps the slider's high end
        # interesting without going to noise.
        density = intensity * 0.05
        # Sample uint8 (1 byte/cell) and threshold instead of float64
        # (8 bytes/cell). At 1080p that's 6 MB/frame instead of 50 MB.
        thresh = max(0, min(255, int(density * 256)))
        mask = np.random.randint(0, 256, frame.shape, dtype=np.uint8) < thresh
        if not mask.any():
            return frame
        # Pick the bit plane: low intensity favours LSBs (subtle), high
        # intensity allows MSB flips for catastrophic colour shifts.
        max_bit = max(0, min(7, int(round(intensity * 7))))
        bit_plane = random.randint(0, max_bit)
        flip_value = np.uint8(1 << bit_plane)
        out = frame.copy()
        out[mask] = out[mask] ^ flip_value
        return out


# ──────────────────────────────────────────────────────────────────────
#   B3-bis — WrongMotionVector
# ──────────────────────────────────────────────────────────────────────


class WrongMotionVectorEffect(BaseEffect):
    """Pseudo-MPEG with lost motion vectors.

    A random fraction of 16x16 macroblocks (driven by intensity) is
    overwritten with the contents of *another* 16x16 region of the
    SAME frame, located 32-64 px away. Reads exactly like an H.264
    stream where the motion-vector field is corrupt: chunks of the
    image surface in places they don't belong.
    """

    BLOCK = 16

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        intensity = self.scaled_intensity(seg)
        if intensity <= 0.0:
            return frame
        h, w = frame.shape[:2]
        bs = self.BLOCK
        if h < bs * 4 or w < bs * 4:
            return frame
        n_by = h // bs
        n_bx = w // bs
        # Fraction of blocks affected: 1% .. 30% of the grid.
        n_total = n_by * n_bx
        n_corrupt = max(1, int(n_total * (0.01 + intensity * 0.29)))
        out = frame.copy()
        # Pre-roll the random offsets vector so all corrupt blocks get
        # different sources but with bounded magnitude (32..64 px).
        for _ in range(n_corrupt):
            by = random.randint(0, n_by - 1)
            bx = random.randint(0, n_bx - 1)
            y0 = by * bs
            x0 = bx * bs
            # Source displacement: 32..64 px, sign random per axis.
            mag = random.randint(32, 64)
            dy = random.choice((-1, 1)) * mag
            dx = random.choice((-1, 1)) * mag
            sy0 = y0 + dy
            sx0 = x0 + dx
            # If the source block falls off the frame, wrap around — the
            # wrap is itself a glitchy artefact and matches "bad pointer
            # arithmetic" feel. Modulo handles negative values correctly
            # in Python, but we still need to clamp the high end so the
            # source block fits within the frame fully.
            sy0 = sy0 % (h - bs + 1) if h > bs else 0
            sx0 = sx0 % (w - bs + 1) if w > bs else 0
            out[y0:y0 + bs, x0:x0 + bs] = frame[sy0:sy0 + bs, sx0:sx0 + bs]
        return out
