"""RenderConfig wrapper tests."""
import pytest

from vpc.render.config import RenderConfig, RENDER_DRAFT, RENDER_FINAL


def test_defaults():
    rc = RenderConfig({})
    assert rc.fps('final') == 24
    assert rc.crf('final') == 22
    assert rc.crf(RENDER_DRAFT) == 28
    assert rc.fps(RENDER_DRAFT) == 24
    assert rc.encoder_preset(RENDER_DRAFT) == 'ultrafast'


def test_resolution_preset():
    rc = RenderConfig({'resolution': '1080p'})
    assert rc.output_size(RENDER_FINAL) == (1920, 1080)
    rc2 = RenderConfig({'resolution': '480p'})
    assert rc2.output_size(RENDER_FINAL) == (854, 480)


def test_resolution_draft_override():
    rc = RenderConfig({'resolution': '1080p'})
    assert rc.output_size(RENDER_DRAFT) == (480, 270)


def test_resolution_match_source():
    rc = RenderConfig({'resolution_mode': 'source'})
    # Even dimensions pass through unchanged.
    assert rc.output_size(RENDER_FINAL, source_size=(1234, 568)) == (1234, 568)


def test_resolution_match_source_rounds_odd_to_even():
    """yuv420p chroma subsampling requires even dims — odd source sizes
    must be rounded down by one pixel to keep ffmpeg from refusing the
    pipe."""
    rc = RenderConfig({'resolution_mode': 'source'})
    assert rc.output_size(RENDER_FINAL, source_size=(1234, 567)) == (1234, 566)
    assert rc.output_size(RENDER_FINAL, source_size=(1235, 567)) == (1234, 566)


def test_resolution_custom():
    rc = RenderConfig({'resolution_mode': 'custom',
                       'custom_w': 800, 'custom_h': 600})
    assert rc.output_size(RENDER_FINAL) == (800, 600)


def test_h265_detection():
    assert RenderConfig({'video_codec': 'H.265 (MP4)'}).use_h265 is True
    assert RenderConfig({'video_codec': 'H.264 (MP4)'}).use_h265 is False


def test_validate_reports_missing_paths():
    errors = RenderConfig({}).validate()
    assert any('audio_path' in e for e in errors)
    assert any('video_paths' in e for e in errors)
    assert any('output_path' in e for e in errors)


def test_validate_passes_when_paths_provided():
    rc = RenderConfig({
        'audio_path': '/x.wav',
        'video_paths': ['/y.mp4'],
        'output_path': '/z.mp4',
    })
    assert rc.validate() == []


def test_passthrough_default_off():
    assert RenderConfig({}).passthrough_mode is False


def test_validate_passthrough_audio_optional():
    """In passthrough mode the audio is extracted from the video, so
    `audio_path` is no longer required for a config to validate."""
    rc = RenderConfig({
        'passthrough_mode': True,
        'video_paths': ['/y.mp4'],
        'output_path': '/z.mp4',
    })
    assert rc.validate() == []


def test_validate_normal_mode_still_requires_audio():
    rc = RenderConfig({
        'video_paths': ['/y.mp4'],
        'output_path': '/z.mp4',
    })
    errors = rc.validate()
    assert any('audio_path' in e for e in errors)
