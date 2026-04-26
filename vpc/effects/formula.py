"""User-defined math-expression effect — backlog item #3.

The user types any NumPy expression that produces an HxWx3 uint8 array.
Compiled once and evaluated per frame in a restricted namespace exposing:

    frame      — current uint8 array, shape (H, W, 3)
    r, g, b    — channel views (H, W) uint8
    x, y       — coordinate grids float32, shape (H, W)
    t          — segment.t_start (seconds)
    i          — scaled intensity in [0, 1]
    a, b, c, d — live slider values in [0, 1] (set from the FORMULA tab)
    np         — numpy module
    cv2        — OpenCV module (available for advanced ops like remap)
    sin, cos, tan, abs, clip, sqrt, exp, log, pi  — convenience aliases

The result is broadcast to the frame shape and clipped to uint8. Errors
during evaluation fall back to the input frame so a typo never crashes the
render.
"""
from __future__ import annotations

import cv2
import numpy as np

from vpc.analyzer import SegmentType
from .base import BaseEffect, _ensure_uint8


_SAFE_BUILTINS = {
    'abs': abs, 'min': min, 'max': max, 'int': int, 'float': float,
    'len': len, 'range': range, 'round': round, 'sum': sum,
}

_SAFE_GLOBALS = {
    '__builtins__': _SAFE_BUILTINS,
    'np': np,
    'cv2': cv2,
    'sin': np.sin, 'cos': np.cos, 'tan': np.tan,
    'abs': np.abs, 'clip': np.clip, 'sqrt': np.sqrt,
    'exp': np.exp, 'log': np.log,
    'pi': float(np.pi),
}


def compile_formula(expression: str):
    """Compile an expression. Returns (code_object, error_message_or_None)."""
    src = expression or 'frame'
    try:
        return compile(src, '<formula>', 'eval'), None
    except SyntaxError as e:
        return None, f'SyntaxError: {e.msg} (line {e.lineno})'


class FormulaEffect(BaseEffect):
    """Apply a user-typed NumPy expression as a per-frame transform."""
    trigger_types = list(SegmentType)

    def __init__(self, expression: str = 'frame', blend: float = 0.0,
                 a: float = 0.5, b: float = 0.5, c: float = 0.5, d: float = 0.5,
                 **kw):
        super().__init__(**kw)
        self.expression = expression
        self.blend = blend
        self.a, self.b, self.c, self.d = float(a), float(b), float(c), float(d)
        self._compiled = None
        self._compiled_src = None

    def _compile(self):
        if self._compiled is not None and self._compiled_src == self.expression:
            return self._compiled
        code, _err = compile_formula(self.expression)
        self._compiled = code
        self._compiled_src = self.expression
        return code

    def evaluate(self, frame: np.ndarray, *, t: float = 0.0, i: float = 1.0):
        """Evaluate the formula on a frame outside the segment-driven path.

        Used by the GUI's snippet-test panel and by tests. Returns a uint8
        frame, or the input on error.
        """
        code = self._compile()
        if code is None:
            return frame
        env = self._build_env(frame, t=t, i=i)
        try:
            out = eval(code, _SAFE_GLOBALS, env)
        except Exception:
            return frame
        return self._coerce_output(frame, out)

    def _build_env(self, frame: np.ndarray, *, t: float, i: float) -> dict:
        h, w = frame.shape[:2]
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        return {
            'frame': frame,
            'r': frame[:, :, 0], 'g': frame[:, :, 1], 'b': frame[:, :, 2],
            'x': xs, 'y': ys,
            't': float(t), 'i': float(i),
            'a': self.a, 'b': self.b, 'c': self.c, 'd': self.d,
        }

    def _coerce_output(self, frame: np.ndarray, out) -> np.ndarray:
        if not isinstance(out, np.ndarray):
            return frame
        if out.shape != frame.shape:
            try:
                out = np.broadcast_to(out, frame.shape).copy()
            except ValueError:
                return frame
        out = _ensure_uint8(out)
        if self.blend > 0:
            out = cv2.addWeighted(out, 1.0 - float(self.blend),
                                  frame, float(self.blend), 0)
        return _ensure_uint8(out)

    def _apply(self, frame, seg, draft):
        code = self._compile()
        if code is None:
            return frame
        env = self._build_env(frame, t=seg.t_start,
                              i=self.scaled_intensity(seg))
        try:
            out = eval(code, _SAFE_GLOBALS, env)
        except Exception:
            return frame
        return self._coerce_output(frame, out)
