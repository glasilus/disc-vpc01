"""Тесты источников статичных изображений (интеграция ImageCapture + VideoPool)."""
import numpy as np
import pytest
import cv2

from vpc.render.image_source import (
    ImageCapture, NOMINAL_FPS, NOMINAL_FRAME_COUNT,
)
from vpc.render.source import VideoPool, is_image


def _png(tmp_path, w=40, h=30, name='p.png'):
    # Пишем через imencode+tofile, а не cv2.imwrite - тот не умеет в
    # non-ASCII пути на Windows, а tmp_path может лежать под кириллическим
    # домашним каталогом. Аналог imread_unicode для записи.
    p = tmp_path / name
    img = np.random.randint(0, 255, (h, w, 3), np.uint8)
    ok, buf = cv2.imencode('.png', img)
    assert ok
    buf.tofile(str(p))
    return str(p), (w, h)


def _tiny_mp4(tmp_path, frames=5, w=32, h=24):
    p = str(tmp_path / 'v.mp4')
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    vw = cv2.VideoWriter(p, fourcc, 24.0, (w, h))
    if not vw.isOpened():
        return None
    for _ in range(frames):
        vw.write(np.random.randint(0, 255, (h, w, 3), np.uint8))
    vw.release()
    import os
    return p if os.path.getsize(p) > 0 else None


# ── is_image ─────────────────────────────────────────────────────────────
def test_is_image_extensions():
    assert is_image('a.JPG') and is_image('b.png') and is_image('c.webp')
    assert is_image('d.TIFF')
    assert not is_image('e.mp4') and not is_image('f.mov') and not is_image('g')


# ── контракт ImageCapture ───────────────────────────────────────────────────
def test_image_capture_never_eofs(tmp_path):
    path, (w, h) = _png(tmp_path)
    cap = ImageCapture(path)
    assert cap.isOpened()
    for _ in range(200):
        ret, frame = cap.read()
        assert ret is True
        assert frame.shape == (h, w, 3) and frame.dtype == np.uint8
    ok, f2 = cap.retrieve()
    assert ok and f2.shape == (h, w, 3)
    assert cap.grab() is True


def test_image_capture_get_set_contract(tmp_path):
    path, (w, h) = _png(tmp_path)
    cap = ImageCapture(path)
    assert cap.get(cv2.CAP_PROP_FPS) == NOMINAL_FPS
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT) == float(NOMINAL_FRAME_COUNT)
    assert cap.get(cv2.CAP_PROP_FRAME_WIDTH) == float(w)
    assert cap.get(cv2.CAP_PROP_FRAME_HEIGHT) == float(h)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 999)
    assert cap.get(cv2.CAP_PROP_POS_FRAMES) == 999.0


def test_image_capture_release_then_read(tmp_path):
    path, _ = _png(tmp_path)
    cap = ImageCapture(path)
    cap.release()
    assert cap.isOpened() is False
    ret, frame = cap.read()
    assert ret is False and frame is None


def test_image_capture_unreadable_raises(tmp_path):
    bad = tmp_path / 'not_an_image.png'
    bad.write_bytes(b'this is not a PNG')
    with pytest.raises(RuntimeError):
        ImageCapture(str(bad))


# ── VideoPool с источниками-изображениями ───────────────────────────────────
def test_pool_from_single_image(tmp_path):
    path, (w, h) = _png(tmp_path)
    pool = VideoPool([path])
    assert pool.is_image_list == [True]
    assert pool.sizes[0] == (w, h)
    assert pool.first_video_index() is None       # только изображения
    cap, fps, total, dur = pool.random_cap()
    ret, frame = cap.read()
    assert ret and frame.shape == (h, w, 3)
    assert fps == NOMINAL_FPS


def test_pool_mixed_first_video_index(tmp_path):
    img, _ = _png(tmp_path)
    vid = _tiny_mp4(tmp_path)
    if vid is None:
        pytest.skip('no mp4 writer codec available in this environment')
    pool = VideoPool([img, vid])
    assert pool.is_image_list == [True, False]
    assert pool.first_video_index() == 1          # первый не-image


def test_passthrough_selection_helper():
    # Это ровно тот фильтр, которым движок проверяет наличие видео.
    paths = ['a.png', 'b.jpg']
    video_only = [p for p in paths if not is_image(p)]
    assert video_only == []                        # → путь жёсткой ошибки
    paths2 = ['a.png', 'clip.mp4', 'b.jpg']
    assert [p for p in paths2 if not is_image(p)] == ['clip.mp4']
