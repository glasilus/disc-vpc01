"""Регрессионный end-to-end тест рендера - следит за багами усечения
числа кадров / длительности.

Два способа запуска:
    1. Задать TEST_VIDEO и TEST_AUDIO реальными медиафайлами (как в
       устаревшем `test_engine.py`) - тест прогоняется на своих ассетах.
    2. Задать RUN_RENDER_REGRESSION=1 - тест сам сгенерирует короткий
       testsrc + sine фикстур в tools/.bench_fixtures/ и прогонится на нём.

Второй путь нужен, чтобы тест был самодостаточным для CI и не тащить
бинарные фикстуры в репозиторий.

Проверяем, что при рендере длительности D и FPS F контейнер содержит
ровно round(D*F) видеокадров, а duration по ffprobe отличается от D не
больше чем на 1/F. Это контракт, который закрепил фикс усечения в
engine.py (кумулятивный счётчик кадров + добивка хвоста, без -shortest).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from vpc.render import BreakcoreEngine, RENDER_FINAL
from vpc.render.sink import ffmpeg_bin


VIDEO = os.environ.get('TEST_VIDEO')
AUDIO = os.environ.get('TEST_AUDIO')
RUN_AUTO = os.environ.get('RUN_RENDER_REGRESSION') == '1'

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / 'tools' / '.bench_fixtures'


def _ensure_synth_fixtures(duration: float = 5.0) -> tuple[Path, Path]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    v = FIXTURE_DIR / f'testsrc_{int(duration)}s.mp4'
    a = FIXTURE_DIR / f'sine_{int(duration)}s.wav'
    ff = ffmpeg_bin()
    if not v.exists():
        subprocess.run([
            ff, '-y', '-f', 'lavfi',
            '-i', f'testsrc=duration={duration}:size=1280x720:rate=24',
            '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-preset', 'ultrafast',
            str(v),
        ], check=True, capture_output=True)
    if not a.exists():
        subprocess.run([
            ff, '-y', '-f', 'lavfi',
            '-i', f'sine=frequency=220:duration={duration}',
            '-ar', '44100', str(a),
        ], check=True, capture_output=True)
    return v, a


def _ffprobe(path: Path) -> dict:
    ff = ffmpeg_bin()
    probe = Path(ff).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    cmd = [str(probe) if probe.exists() else 'ffprobe',
           '-v', 'error', '-select_streams', 'v:0',
           '-count_frames', '-show_entries', 'stream=nb_read_frames,duration',
           '-of', 'json', str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    s = (json.loads(r.stdout or '{}').get('streams') or [{}])[0]
    return {'duration': float(s.get('duration') or 0.0),
            'nb_frames': int(s.get('nb_read_frames') or 0)}


def _resolve_inputs() -> tuple[str, str, float]:
    if VIDEO and AUDIO:
        return VIDEO, AUDIO, 2.0
    if RUN_AUTO:
        v, a = _ensure_synth_fixtures(5.0)
        return str(v), str(a), 2.0
    pytest.skip('Set TEST_VIDEO+TEST_AUDIO or RUN_RENDER_REGRESSION=1.')


def _render(tmp_path: Path, duration: float, video: str, audio: str,
            output_name: str = 'reg.mp4', extra: dict | None = None) -> Path:
    out = tmp_path / output_name
    cfg = {
        'video_paths': [video], 'audio_path': audio,
        'output_path': str(out),
        'fps': 24, 'crf': 23, 'export_preset': 'ultrafast',
        'video_codec': 'H.264 (MP4)',
        'resolution': '480p', 'resolution_mode': 'preset',
        'silence_mode': 'none',
        'chaos_level': 0.5, 'threshold': 1.2, 'transient_thresh': 0.5,
        'min_cut_duration': 0.05,
        'snap_to_beat': False, 'snap_tolerance': 0.05,
        'use_scene_detect': False,
    }
    if extra:
        cfg.update(extra)
    engine = BreakcoreEngine(cfg)
    ok = engine.run(render_mode=RENDER_FINAL, max_output_duration=duration)
    assert ok, 'engine.run() returned False'
    assert out.exists() and out.stat().st_size > 1000
    return out


def test_frame_count_matches_audio_duration(tmp_path):
    """В контейнере должно быть ровно round(D*F) кадров, без усечения."""
    video, audio, dur = _resolve_inputs()
    out = _render(tmp_path, dur, video, audio)
    info = _ffprobe(out)
    fps = 24
    expected = round(dur * fps)
    # допуск ±1 из-за округления таймскейла mp4 на границе контейнера
    # (может сдвинуть PTS последнего кадра)
    assert abs(info['nb_frames'] - expected) <= 1, (
        f'expected {expected} frames, got {info["nb_frames"]}')
    # длительность должна укладываться в один кадр
    assert abs(info['duration'] - dur) <= 1.0 / fps + 0.01


def test_no_shortest_flag_in_sink_argv():
    """Дублирует проверку инварианта sink'а с другого угла - на случай,
    если test_sink_command.py пропущен или удалён."""
    from vpc.render.sink import FFmpegSink
    s = FFmpegSink(width=640, height=360, fps=24,
                   audio_path='a.wav', output_path='o.mp4',
                   target_duration=3.0)
    assert '-shortest' not in s._cmd
    assert '-t' in s._cmd
