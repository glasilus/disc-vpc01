"""
Integration test — requires real video and audio files.
Set environment variables TEST_VIDEO and TEST_AUDIO to file paths before running.

Example (Windows CMD):
    set TEST_VIDEO=path\to\video.mp4 && set TEST_AUDIO=path\to\audio.mp3 && python -m pytest tests/test_engine.py -v -s
"""
import os
import pytest
from vpc.render import BreakcoreEngine, RENDER_DRAFT

VIDEO = os.environ.get('TEST_VIDEO')
AUDIO = os.environ.get('TEST_AUDIO')


@pytest.mark.skipif(not VIDEO or not AUDIO, reason="TEST_VIDEO and TEST_AUDIO not set")
def test_draft_render_produces_file(tmp_path):
    output = str(tmp_path / "test_output.mp4")
    cfg = {
        'video_path': VIDEO,
        'audio_path': AUDIO,
        'output_path': output,
        'fx_psort': True, 'fx_psort_chance': 1.0, 'fx_psort_int': 0.5,
        'fx_rgb': True, 'fx_rgb_chance': 1.0,
        'chaos_level': 0.5,
        'min_cut_duration': 0.05,
        'use_scene_detect': False,
    }
    engine = BreakcoreEngine(cfg)
    engine.run(render_mode=RENDER_DRAFT, max_output_duration=3.0)
    assert os.path.exists(output)
    assert os.path.getsize(output) > 1000
