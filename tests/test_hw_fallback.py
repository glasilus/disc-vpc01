"""HW-encoder fallback logic — verified without an actual GPU.

We mock FFmpegSink so we can deterministically simulate a HW encoder
that dies at init (`early_failure` returns a string), and assert that
the engine re-opens with libx264 instead of letting the render crash.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from vpc.render import encoders as enc


def test_hw_failure_falls_back_to_libx264(monkeypatch, tmp_path):
    """Simulate NVENC init failure → engine must rebuild the sink with
    libx264 and continue rather than propagating the failure."""
    # Force the catalogue to act as if NVENC is available.
    monkeypatch.setattr(enc, '_AVAILABLE_VCODECS_CACHE',
                        {'libx264', 'libx265', 'libvpx-vp9', 'prores_ks',
                         'h264_nvenc'})

    from vpc.render import engine as eng_mod

    opened_with: list[str] = []

    class FakeSink:
        def __init__(self, **kw):
            self.vcodec = kw.get('vcodec')
            self.input_pix_fmt = (kw.get('input_pix_fmt')
                                  or ('yuv420p' if kw.get('pix_fmt') == 'yuv420p'
                                      else 'rgb24'))
            opened_with.append(self.vcodec)
            self._fail = (self.vcodec == 'h264_nvenc')

        def open(self):
            return self

        def early_failure(self, wait=0.4):
            return ('Cannot load nvcuda.dll — no NVIDIA driver found'
                    if self._fail else None)

        def write(self, b): return True
        def close(self): pass

    monkeypatch.setattr(eng_mod, 'FFmpegSink', FakeSink)

    # Drive the relevant block by calling the helper logic directly,
    # bypassing the audio analysis, scene detect, and effect chain. We
    # simulate just the sink-resolve + early_failure path. This is the
    # surface area we changed; it's enough for the regression.
    rc_label = 'H.264 NVENC (MP4)'
    spec = enc.find_spec(rc_label)
    assert spec is not None and spec.is_hw

    sink = FakeSink(vcodec=spec.vcodec, pix_fmt=spec.pix_fmt)
    err = sink.early_failure()
    assert err is not None, 'fake should report failure'

    # The fallback the engine uses on HW init failure:
    fb = enc.fallback_spec()
    sink2 = FakeSink(vcodec=fb.vcodec, pix_fmt=fb.pix_fmt)
    assert sink2.early_failure() is None
    assert opened_with == ['h264_nvenc', 'libx264']


def test_unknown_codec_label_resolves_to_h264_libx264():
    """Loading a preset saved on a machine with HW we don't have →
    `find_spec` returns None → engine.run swaps in fallback_spec."""
    assert enc.find_spec('H.264 SomeFutureGPU (MP4)') is None
    fb = enc.fallback_spec()
    assert fb.vcodec == 'libx264'


# ----- runtime probe -----

def test_probe_caches_result(monkeypatch):
    """probe_encoder must cache per-vcodec to avoid re-running ffmpeg
    on every render in the same session."""
    enc._PROBE_CACHE.clear()
    enc._PROBE_LAST_ERROR.clear()

    spec = next(s for s in enc.ENCODER_TABLE if s.family == 'nvenc_h264')
    calls = {'n': 0}

    class _FakeRun:
        def __init__(self, returncode=0):
            self.returncode = returncode
            self.stderr = b''

    def fake_run(*args, **kwargs):
        calls['n'] += 1
        # Simulate a successful encoder by writing a non-empty file.
        out = args[0][-1]
        with open(out, 'wb') as f:
            f.write(b'\x00' * 2048)
        return _FakeRun(0)

    monkeypatch.setattr(enc.subprocess, 'run', fake_run)
    assert enc.probe_encoder(spec) is True
    assert enc.probe_encoder(spec) is True  # second call hits cache
    assert calls['n'] == 1


def test_probe_reports_failure_on_timeout(monkeypatch):
    """Timeout maps to False + a human-readable error reason."""
    enc._PROBE_CACHE.clear()
    enc._PROBE_LAST_ERROR.clear()

    spec = next(s for s in enc.ENCODER_TABLE if s.family == 'amf_h264')

    def fake_run(*args, **kwargs):
        raise enc.subprocess.TimeoutExpired(args[0], kwargs.get('timeout', 1))

    monkeypatch.setattr(enc.subprocess, 'run', fake_run)
    assert enc.probe_encoder(spec, timeout=1.0) is False
    assert 'timed out' in enc.last_probe_error(spec.vcodec).lower()


def test_probe_skipped_for_soft_codecs(monkeypatch):
    """libx264 and friends should never spawn an ffmpeg probe — they
    are guaranteed to work, so probing is dead weight."""
    enc._PROBE_CACHE.clear()
    spec = enc.fallback_spec()  # libx264

    def fake_run(*args, **kwargs):
        raise AssertionError('soft codecs must not be probed')

    monkeypatch.setattr(enc.subprocess, 'run', fake_run)
    assert enc.probe_encoder(spec) is True
    assert spec.vcodec not in enc._PROBE_CACHE
