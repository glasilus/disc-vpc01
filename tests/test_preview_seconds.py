"""Unit tests for the preview-length clamp.

The helper isolates the engine from any garbage typed into the spinbox.
We avoid spinning up a full Tk root by binding the unbound method to a
lightweight stand-in object that mimics the only attribute the helper
touches: `var_preview_seconds.get()`.
"""
from types import SimpleNamespace
import tkinter as tk
import pytest

from vpc.gui import MainGUI


class _FakeVar:
    def __init__(self, payload):
        self._payload = payload

    def get(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _call(payload):
    stand_in = SimpleNamespace(var_preview_seconds=_FakeVar(payload))
    return MainGUI._get_preview_seconds(stand_in)


@pytest.mark.parametrize('value,expected', [
    (5, 5.0),
    (1, 1.0),
    (90, 90.0),
    (45.5, 45.5),
])
def test_passthrough_in_range(value, expected):
    assert _call(value) == expected


@pytest.mark.parametrize('value,expected', [
    (0, 1.0),
    (-100, 1.0),
    (91, 90.0),
    (10_000, 90.0),
])
def test_clamped_to_bounds(value, expected):
    assert _call(value) == expected


@pytest.mark.parametrize('payload', [
    tk.TclError('expected floating-point'),
    ValueError('not a number'),
    TypeError('NoneType'),
])
def test_falls_back_on_parse_failure(payload):
    assert _call(payload) == 5.0
