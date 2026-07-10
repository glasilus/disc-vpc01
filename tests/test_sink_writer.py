"""Тесты потокового писателя FFmpegSink (очередь + фоновый тред).

ffmpeg не запускается по-настоящему: subprocess.Popen подменяется фейком,
чтобы детерминированно проверить порядок записи, реакцию на обрыв пайпа
(без дедлоков) и идемпотентность close().
"""
import queue

import pytest

from vpc.render import sink as sink_mod
from vpc.render.sink import FFmpegSink


class _FakeStdin:
    def __init__(self, fail_after=None):
        self.chunks = []
        self.fail_after = fail_after
        self.closed = False
        self._n = 0

    def write(self, b):
        if self.fail_after is not None and self._n >= self.fail_after:
            raise BrokenPipeError('pipe closed')
        self.chunks.append(b)
        self._n += 1

    def close(self):
        self.closed = True


class _FakeStderr:
    def read(self, n):
        return b''   # мгновенный EOF - дренажный тред stderr сразу выходит


class _FakeProc:
    def __init__(self, fail_after=None):
        self.stdin = _FakeStdin(fail_after)
        self.stderr = _FakeStderr()
        self.returncode = 0
        self.waited = False

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def poll(self):
        return None


def _patch_popen(monkeypatch, fail_after=None):
    created = {}

    def _fake_popen(cmd, **kw):
        proc = _FakeProc(fail_after)
        created['proc'] = proc
        return proc

    monkeypatch.setattr(sink_mod.subprocess, 'Popen', _fake_popen)
    return created


def _make_sink(**over):
    kw = dict(width=64, height=64, fps=24, audio_path='a.wav',
              output_path='o.mp4', target_duration=1.0)
    kw.update(over)
    return FFmpegSink(**kw)


def test_frames_written_in_order(monkeypatch):
    created = _patch_popen(monkeypatch)
    s = _make_sink()
    s.open()
    frames = [bytes([i]) * 4 for i in range(10)]
    for fb in frames:
        assert s.write(fb) is True
    s.close()
    assert created['proc'].stdin.chunks == frames
    assert created['proc'].stdin.closed is True
    assert created['proc'].waited is True


def test_queue_is_bounded(monkeypatch):
    _patch_popen(monkeypatch)
    s = _make_sink()
    s.open()
    assert isinstance(s._frame_q, queue.Queue)
    assert 4 <= s._frame_q.maxsize <= 16
    s.close()


def test_broken_pipe_reports_false_without_deadlock(monkeypatch):
    # Пайп рвётся после 2 успешных записей. Продолжаем подавать много кадров
    # (больше размера очереди) - write() обязан в какой-то момент вернуть
    # False, а close() не должен зависнуть.
    _patch_popen(monkeypatch, fail_after=2)
    s = _make_sink()
    s.open()
    saw_false = False
    for i in range(500):
        if not s.write(bytes([i % 256]) * 4):
            saw_false = True
            break
    assert saw_false, 'write() должен сообщить об обрыве пайпа'
    assert s._write_failed is True
    s.close()   # не должно повиснуть


def test_close_is_idempotent(monkeypatch):
    _patch_popen(monkeypatch)
    s = _make_sink()
    s.open()
    s.write(b'\x00' * 4)
    s.close()
    s.close()   # повторный close безопасен


def test_write_before_open_returns_false(monkeypatch):
    _patch_popen(monkeypatch)
    s = _make_sink()
    assert s.write(b'\x00' * 4) is False
    s.close()   # close без open тоже безопасен


def test_write_after_close_returns_false(monkeypatch):
    _patch_popen(monkeypatch)
    s = _make_sink()
    s.open()
    s.close()
    assert s.write(b'\x00' * 4) is False
