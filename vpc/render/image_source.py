"""ImageCapture: a still image that quacks like a cv2.VideoCapture.

The render engine and VideoPool touch a source only through a narrow slice of
the cv2.VideoCapture interface (get/set/read/grab/retrieve/isOpened/release).
ImageCapture implements exactly that slice over one decoded image, so a photo
can join the source pool as a frozen "clip" with no changes to the engine.

Key property: read()/grab() NEVER report end-of-stream — a still is infinitely
readable, so no render loop is ever truncated by a photo's reported length.
Its reported length (fps / frame count) is a small nominal value, used only so
`max(durations)` and any UI stay sane; it is decoupled from readability.
"""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

# Nominal "clip" the still reports itself as. Cosmetic only — read()/grab()
# ignore position and never EOF, so these never bound the render.
NOMINAL_FPS = 24.0
NOMINAL_DURATION = 3.0
NOMINAL_FRAME_COUNT = int(NOMINAL_FPS * NOMINAL_DURATION)  # 72


def imread_unicode(path: str) -> Optional[np.ndarray]:
    """Read an image to a BGR array, tolerant of non-ASCII paths.

    `cv2.imread` cannot open Unicode paths on Windows (it uses an ANSI file
    API), which would break every photo under a Cyrillic/accented home
    directory. Reading the bytes with numpy and decoding in memory sidesteps
    that entirely. Returns None on any read/decode failure.
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class ImageCapture:
    """Duck-typed cv2.VideoCapture over a single still image (BGR)."""

    def __init__(self, path: str):
        img = imread_unicode(path)
        if img is None:
            raise RuntimeError(f'Cannot open image: {path}')
        self._frame = img                    # BGR, matching VideoCapture.read()
        self._h, self._w = img.shape[:2]
        self._pos = 0
        self._opened = True

    # ── VideoCapture-compatible surface ──────────────────────────────────
    def isOpened(self) -> bool:
        return self._opened

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_FPS:
            return NOMINAL_FPS
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(NOMINAL_FRAME_COUNT)
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self._pos)
        return 0.0

    def set(self, prop: int, value: float) -> bool:
        # Position is meaningless for a still, but honour the contract so the
        # engine's cap.set(POS_FRAMES, ...) calls are no-ops rather than errors.
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self._pos = int(value)
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._opened:
            return False, None
        self._pos += 1
        return True, self._frame            # engine's next cvtColor allocates a copy

    def grab(self) -> bool:
        if not self._opened:
            return False
        self._pos += 1
        return True

    def retrieve(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._opened:
            return False, None
        return True, self._frame

    def release(self) -> None:
        self._opened = False
        self._frame = None
