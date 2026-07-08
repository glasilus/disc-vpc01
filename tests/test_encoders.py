"""Юнит-тесты каталога энкодеров и маппинга rate-control.

Здесь ffmpeg не запускается и железо не требуется - тестируются только
чистые функции маппинга и структурные инварианты каталога.
"""
from __future__ import annotations

import pytest

from vpc.render import encoders as enc


# ----- инварианты каталога -----

def test_catalogue_labels_are_unique():
    labels = [s.label for s in enc.ENCODER_TABLE]
    assert len(labels) == len(set(labels))


def test_soft_codecs_always_present_in_available():
    """Даже если проба `ffmpeg -encoders` не удалась, программный минимум
    должен оставить libx264/libx265/libvpx-vp9/prores_ks в списке - иначе
    GUI будет нечего предложить."""
    specs = enc.available_specs()
    vcodecs = {s.vcodec for s in specs}
    assert {'libx264', 'libx265', 'libvpx-vp9', 'prores_ks'} <= vcodecs


def test_fallback_spec_is_h264_mp4():
    s = enc.fallback_spec()
    assert s.vcodec == 'libx264'
    assert s.container_ext == 'mp4'
    assert s.is_hw is False


def test_find_spec_known_label():
    s = enc.find_spec('H.264 (MP4)')
    assert s is not None and s.vcodec == 'libx264'


def test_find_spec_unknown_label_returns_none():
    assert enc.find_spec('Nonexistent (XYZ)') is None


# ----- маппинг rate-control -----

def _spec(family: str) -> enc.EncoderSpec:
    """Взять любую запись каталога из заданного семейства для теста."""
    for s in enc.ENCODER_TABLE:
        if s.family == family:
            return s
    raise AssertionError(f'no spec with family {family}')


def test_rc_x264_emits_preset_crf_and_optional_tune():
    s = _spec('x264')
    a = enc.build_rate_control_args(s, crf=20, preset='medium', tune='grain')
    assert a == ['-preset', 'medium', '-crf', '20', '-tune', 'grain']

    a2 = enc.build_rate_control_args(s, crf=18, preset='slow', tune='none')
    assert a2 == ['-preset', 'slow', '-crf', '18']
    assert '-tune' not in a2


def test_rc_x265_same_shape_as_x264():
    s = _spec('x265')
    a = enc.build_rate_control_args(s, crf=22, preset='fast', tune=None)
    assert a == ['-preset', 'fast', '-crf', '22']


def test_rc_nvenc_uses_p_preset_cq_and_vbr():
    s = _spec('nvenc_h264')
    a = enc.build_rate_control_args(s, crf=20, preset='medium', tune='grain')
    # NVENC игнорирует tune (свой параметр tune мы для него не выставляем).
    assert '-tune' not in a
    assert '-rc' in a and 'vbr' in a
    assert '-cq' in a and '20' in a
    assert '-b:v' in a and '0' in a
    # Preset должен быть токеном вида p1..p7.
    p_idx = a.index('-preset') + 1
    assert a[p_idx].startswith('p') and a[p_idx][1:].isdigit()


def test_rc_nvenc_preset_mapping_keeps_speed_order():
    s = _spec('nvenc_h264')
    fast = enc.build_rate_control_args(s, crf=20, preset='fast', tune=None)
    slow = enc.build_rate_control_args(s, crf=20, preset='slow', tune=None)
    fp = int(fast[fast.index('-preset') + 1][1:])
    sp = int(slow[slow.index('-preset') + 1][1:])
    # NVENC: чем меньше номер p, тем быстрее, чем больше - тем лучше качество.
    # Поэтому x264 'slow' должен мапиться на больший номер p, чем 'fast'.
    assert fp < sp


def test_rc_qsv_emits_preset_and_global_quality():
    s = _spec('qsv_h264')
    a = enc.build_rate_control_args(s, crf=22, preset='medium', tune=None)
    assert '-preset' in a and 'medium' in a
    assert '-global_quality' in a and '22' in a


def test_rc_amf_uses_quality_and_cqp():
    s = _spec('amf_h264')
    a = enc.build_rate_control_args(s, crf=23, preset='medium', tune=None)
    assert '-quality' in a and 'balanced' in a
    assert '-rc' in a and 'cqp' in a
    assert '-qp_i' in a and '23' in a
    assert '-qp_p' in a and '23' in a


def test_rc_videotoolbox_inverts_crf_to_qv():
    """У VT шкала обратная - меньший CRF должен давать больший q."""
    s = _spec('vt_h264')
    low = enc.build_rate_control_args(s, crf=10, preset='medium', tune=None)
    high = enc.build_rate_control_args(s, crf=40, preset='medium', tune=None)
    qlow = int(low[low.index('-q:v') + 1])
    qhigh = int(high[high.index('-q:v') + 1])
    assert qlow > qhigh
    assert 1 <= qhigh <= 100 and 1 <= qlow <= 100


def test_rc_vp9_uses_deadline_not_preset():
    s = _spec('vp9')
    a = enc.build_rate_control_args(s, crf=32, preset='slow', tune=None)
    assert '-preset' not in a
    assert '-deadline' in a and 'good' in a
    assert '-cpu-used' in a
    assert '-crf' in a and '32' in a


def test_rc_prores_emits_nothing_extra():
    """Качество зафиксировано статическим -profile:v 3 в extra_v."""
    s = _spec('prores')
    assert enc.build_rate_control_args(
        s, crf=18, preset='medium', tune='grain') == []
