"""Эффект с пользовательским математическим выражением.

Пользователь вводит любое NumPy-выражение, дающее на выходе массив uint8
формы HxWx3. Компилируется один раз и вычисляется на каждом кадре в
ограниченном пространстве имён:

    frame      - текущий массив uint8, форма (H, W, 3)
    r, g, b    - представления каналов (H, W) uint8
    x, y       - сетки координат float32, форма (H, W)
    t          - segment.t_start (секунды)
    i          - масштабированная интенсивность в [0, 1]
    a, b, c, d - значения слайдеров в [0, 1] (вкладка FORMULA)
    np         - модуль numpy
    cv2        - модуль OpenCV (для операций вроде remap)
    sin, cos, tan, abs, clip, sqrt, exp, log, pi  - удобные алиасы

Результат приводится к форме кадра и клипается в uint8. Ошибки во время
вычисления откатываются к исходному кадру, чтобы опечатка в выражении не
роняла рендер.
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
    """Компилирует выражение. Возвращает (code_object, error_message_or_None)."""
    src = expression or 'frame'
    try:
        return compile(src, '<formula>', 'eval'), None
    except SyntaxError as e:
        return None, f'SyntaxError: {e.msg} (line {e.lineno})'


class FormulaEffect(BaseEffect):
    """Применяет введённое пользователем NumPy-выражение как трансформацию кадра."""
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
        """Вычисляет формулу на кадре в обход обычного пути через сегменты.

        Используется панелью тестирования сниппетов в GUI и тестами.
        Возвращает кадр uint8 или исходный кадр при ошибке.
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
