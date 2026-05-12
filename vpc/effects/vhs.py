"""VHS tape simulation — single composite effect.

Designed to *look like* the real artefacts of consumer VHS playback:
luma stays sharp while chroma smears horizontally, tape grain modulates
brightness in slow blobs, the bottom of the picture carries head-switch
noise, and very slight wow-and-flutter shifts the whole picture sideways
sub-pixel by sub-pixel. None of these layers individually is special;
the combination is what reads as "VHS" to the eye.

A single master `wear` knob (0..1, exposed through the standard
intensity slider) scales every layer at once, so the user has one
control to tune. An optional `dust` boolean adds rare scratch lines on
top of everything.
"""
from __future__ import annotations

import random
import cv2
import numpy as np

from .base import BaseEffect, _ensure_uint8


class VHSTapeEffect(BaseEffect):
    """Composite VHS-tape look: chroma smear + grain + head-switch +
    sub-pixel wow + optional dust.

    The intensity passed in (via `scaled_intensity`) is the master `wear`
    knob: 0 means pristine source, 1 means heavy generation-loss.
    """

    def __init__(self, enabled: bool = True, chance: float = 1.0,
                 intensity_min: float = 0.0, intensity_max: float = 1.0,
                 dust: bool = False):
        super().__init__(enabled=enabled, chance=chance,
                         intensity_min=intensity_min,
                         intensity_max=intensity_max)
        self.dust = bool(dust)
        self._t = 0

    def _apply(self, frame: np.ndarray, seg, draft: bool) -> np.ndarray:
        wear = self.scaled_intensity(seg)
        if wear <= 0.0:
            return frame
        self._t += 1
        h, w = frame.shape[:2]

        out = frame
        out = self._chroma_smear(out, wear)
        out = self._wow_flutter(out, wear)
        out = self._tape_grain(out, wear)
        out = self._gen_loss(out, wear)
        out = self._head_switch(out, wear)
        if self.dust and wear > 0.15:
            out = self._dust(out, wear)
        return _ensure_uint8(out)

    # ------------------------------------------------------------------ layers
    def _chroma_smear(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Y/C separation + horizontal blur on chroma only.

        Real VHS bandwidth-limits the chroma carrier far more than luma,
        so colour smears sideways while edges stay reasonably crisp.
        """
        ycc = cv2.cvtColor(frame, cv2.COLOR_RGB2YCrCb)
        # Kernel width 3..21 px scaling with wear, always odd.
        kw = int(3 + wear * 18) | 1
        cr = cv2.GaussianBlur(ycc[:, :, 1], (kw, 1), 0)
        cb = cv2.GaussianBlur(ycc[:, :, 2], (kw, 1), 0)
        ycc[:, :, 1] = cr
        ycc[:, :, 2] = cb
        return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB)

    def _wow_flutter(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Sub-pixel horizontal jitter — slow sin component (wow) + fast
        random component (flutter). Maximum amplitude grows with wear up
        to ~3 px so the picture visibly breathes sideways but never tears.
        """
        amp = wear * 3.0
        if amp < 0.2:
            return frame
        slow = np.sin(self._t * 0.08) * amp * 0.7
        fast = (random.random() - 0.5) * amp * 0.6
        dx = float(slow + fast)
        if abs(dx) < 0.05:
            return frame
        h, w = frame.shape[:2]
        M = np.float32([[1, 0, dx], [0, 1, 0]])
        return cv2.warpAffine(frame, M, (w, h),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)

    def _tape_grain(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Low-frequency multiplicative noise on luma — emulates uneven
        tape coating so the brightness slowly mottles in patches.
        """
        h, w = frame.shape[:2]
        # Generate noise at 1/8 resolution and bilinearly upsample so the
        # resulting "blobs" are large and smooth — this reads as analog
        # tape unevenness, not pixel-level noise.
        nh, nw = max(2, h // 8), max(2, w // 8)
        noise = np.random.rand(nh, nw).astype(np.float32)
        noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_LINEAR)
        # Map noise to a multiplier in [1 - amp, 1 + amp]; amp grows with wear.
        amp = 0.04 + wear * 0.12
        mult = 1.0 + (noise - 0.5) * 2.0 * amp
        out = frame.astype(np.float32) * mult[..., None]
        return out

    def _gen_loss(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Generation-loss: mild contrast crush + low-amplitude additive
        noise. Each VHS dub loses a little headroom and gains a little
        hash; both are multiplicative in `wear`.
        """
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        # Crush blacks toward 8 and whites toward 240 as wear grows.
        lo = wear * 8.0
        hi = 255.0 - wear * 15.0
        frame = np.clip(frame, lo, hi)
        # Additive uniform noise, sigma ~ wear * 6. Sample int8
        # directly (1 byte/cell) instead of float64 from np.random.rand
        # (8 bytes/cell). At 1080p that's 6 MB/frame instead of 50 MB.
        noise_sigma = wear * 6.0
        if noise_sigma > 0.25:
            scale = noise_sigma / 127.0
            noise = np.random.randint(-127, 128, frame.shape,
                                      dtype=np.int8).astype(np.float32) * scale
            frame = frame + noise
        return frame

    def _head_switch(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Bottom 6..14 rows become tape-head switching noise — that
        characteristic torn / blurred / rainbow band at the very bottom
        of every consumer-VHS playback.
        """
        h, w = frame.shape[:2]
        band = int(6 + wear * 8)
        if band <= 0 or h - band <= 0:
            return frame
        # Mix of scrambled noise and a horizontal smear of the row just
        # above the band — that mixture is what makes a real head-switch
        # look like "torn picture", not pure white noise.
        smear_src = frame[h - band - 1].astype(np.float32)
        smear = np.tile(smear_src, (band, 1, 1))
        # Roll each row by a different amount to fake the tearing.
        for i in range(band):
            roll = (i * 17 + self._t * 3) % w
            smear[i] = np.roll(smear[i], roll, axis=0)
        rainbow_noise = (np.random.rand(band, w, 3).astype(np.float32) * 255.0)
        mix = 0.55 * smear + 0.45 * rainbow_noise
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        frame[h - band:] = mix
        return frame

    def _dust(self, frame: np.ndarray, wear: float) -> np.ndarray:
        """Rare 1-px-wide vertical scratches of slightly translucent dark
        colour — only drawn when the dust checkbox is on and wear is
        non-trivial.
        """
        h, w = frame.shape[:2]
        n = int(wear * 3) + 1
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        for _ in range(n):
            if random.random() > 0.35:
                continue
            x = random.randint(0, w - 1)
            alpha = 0.25 + random.random() * 0.35
            frame[:, x, :] = frame[:, x, :] * (1.0 - alpha)
        return frame
