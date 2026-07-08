"""Тесты сборки argv для FFmpegSink.

Реальные видеофайлы не нужны - проверяем только собранную команду ffmpeg,
что флаги встают на нужные места. Дешёвая регрессионная защита для
кодека/качества/тюнинга.
"""
from __future__ import annotations

import pytest

from vpc.render.sink import FFmpegSink, EXPORT_FORMATS


def _make(**kw) -> list[str]:
    """Собирает sink и возвращает его argv, не запуская процесс."""
    defaults = dict(
        width=640, height=360, fps=24,
        audio_path='audio.wav', output_path='out.mp4',
        vcodec='libx264', acodec='aac', pix_fmt='yuv420p',
        preset='medium', crf=18, target_duration=2.0,
    )
    defaults.update(kw)
    return FFmpegSink(**defaults)._cmd


def test_x264_carries_preset_and_crf():
    cmd = _make(vcodec='libx264', preset='medium', crf=18)
    assert '-preset' in cmd and 'medium' in cmd
    assert '-crf' in cmd and '18' in cmd


def test_x265_carries_preset_and_crf():
    cmd = _make(vcodec='libx265', preset='slow', crf=20,
                output_path='out.mp4',
                extra_v_flags=['-tag:v', 'hvc1'])
    assert '-preset' in cmd and 'slow' in cmd
    assert '-crf' in cmd and '20' in cmd
    assert '-tag:v' in cmd and 'hvc1' in cmd


def test_vp9_uses_deadline_not_preset():
    cmd = _make(vcodec='libvpx-vp9', acodec='libopus', preset='medium', crf=32,
                output_path='out.webm', extra_v_flags=['-row-mt', '1', '-b:v', '0'])
    assert '-preset' not in cmd
    assert '-deadline' in cmd and 'good' in cmd
    assert '-cpu-used' in cmd
    assert '-crf' in cmd and '32' in cmd


def test_prores_skips_preset_and_crf():
    cmd = _make(vcodec='prores_ks', acodec='pcm_s16le',
                pix_fmt='yuv422p10le', preset='medium', crf=18,
                output_path='out.mov',
                extra_v_flags=['-profile:v', '3'])
    assert '-preset' not in cmd
    assert '-crf' not in cmd
    assert '-profile:v' in cmd and '3' in cmd


def test_target_duration_present_but_no_shortest():
    """-t ограничивает вывод, но -shortest ставить нельзя (баг усечения)."""
    cmd = _make(target_duration=12.345)
    assert '-t' in cmd
    assert '-shortest' not in cmd
    # значение форматируется с 3 знаками после запятой
    idx = cmd.index('-t')
    assert cmd[idx + 1] == '12.345'


def test_faststart_only_for_mp4_and_mov():
    mp4 = _make(output_path='clip.mp4')
    mov = _make(output_path='clip.mov', vcodec='prores_ks',
                acodec='pcm_s16le', pix_fmt='yuv422p10le')
    mkv = _make(output_path='clip.mkv')
    webm = _make(output_path='clip.webm', vcodec='libvpx-vp9',
                 acodec='libopus')
    assert '+faststart' in mp4
    assert '+faststart' in mov
    assert '+faststart' not in mkv
    assert '+faststart' not in webm


def test_export_formats_are_consistent():
    """У каждой записи есть обязательные ключи, pix_fmt не пустой."""
    required = {'ext', 'vcodec', 'acodec', 'pix_fmt', 'extra_v'}
    for label, spec in EXPORT_FORMATS.items():
        missing = required - spec.keys()
        assert not missing, f'{label} missing keys: {missing}'
        assert spec['pix_fmt'], f'{label} has empty pix_fmt'
        assert isinstance(spec['extra_v'], list), f'{label} extra_v not list'


def _input_pix_fmt(cmd: list[str]) -> str:
    """Достаёт pixel format пайпа с raw-видео (-pix_fmt перед -i pipe:0)."""
    pipe_idx = cmd.index('pipe:0')
    pre = cmd[:pipe_idx]
    return pre[pre.index('-pix_fmt') + 1]


def test_input_pix_fmt_yuv420p_when_output_is_yuv420p():
    """При выводе yuv420p пайп тоже yuv420p (1.5 байта/пиксель - вдвое
    меньше трафика, чем rgb24)."""
    cmd = _make(vcodec='libx264', pix_fmt='yuv420p')
    assert _input_pix_fmt(cmd) == 'yuv420p'


def test_input_pix_fmt_rgb24_for_prores_10bit():
    """ProRes 4:2:2 10-bit должен получать вход в rgb24 - конвертация
    через I420 потеряла бы детали цветности ещё до того, как кадр попадёт в ffmpeg."""
    cmd = _make(vcodec='prores_ks', acodec='pcm_s16le',
                pix_fmt='yuv422p10le', output_path='out.mov',
                extra_v_flags=['-profile:v', '3'])
    assert _input_pix_fmt(cmd) == 'rgb24'


def test_input_pix_fmt_explicit_override():
    """Явный input_pix_fmt перебивает автовыбор (лазейка для ручного контроля)."""
    cmd = _make(vcodec='libx264', pix_fmt='yuv420p', input_pix_fmt='rgb24')
    assert _input_pix_fmt(cmd) == 'rgb24'


def test_pack_frame_rgb24_passthrough():
    """Для 'rgb24' _pack_frame ничего не меняет в байтах."""
    import numpy as np
    from vpc.render.engine import BreakcoreEngine
    rgb = np.arange(360 * 480 * 3, dtype=np.uint8).reshape(360, 480, 3)
    out = BreakcoreEngine._pack_frame(rgb, 'rgb24')
    assert out == rgb.tobytes()
    assert len(out) == 360 * 480 * 3


def test_pack_frame_yuv420p_size():
    """yuv420p упаковывается ровно в 1.5 байта на пиксель (planar I420)."""
    import numpy as np
    from vpc.render.engine import BreakcoreEngine
    rgb = np.full((360, 480, 3), 128, dtype=np.uint8)
    out = BreakcoreEngine._pack_frame(rgb, 'yuv420p')
    assert len(out) == 360 * 480 * 3 // 2


def test_pack_frame_unknown_falls_back_to_rgb24():
    """Неизвестный формат не должен молча выдавать неверные байты."""
    import numpy as np
    from vpc.render.engine import BreakcoreEngine
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    assert BreakcoreEngine._pack_frame(rgb, 'nv12_made_up') == rgb.tobytes()
