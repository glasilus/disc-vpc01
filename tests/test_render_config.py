"""RenderConfig wrapper tests."""
import pytest

from vpc.render.config import RenderConfig, RENDER_DRAFT, RENDER_FINAL


def test_defaults():
    rc = RenderConfig({})
    assert rc.fps('final') == 24
    assert rc.crf('final') == 18
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
    assert rc.output_size(RENDER_FINAL, source_size=(1234, 567)) == (1234, 567)


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
