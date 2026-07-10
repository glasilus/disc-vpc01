"""Тесты кэша анализа аудио: ключи, роундтрип, устойчивость к сбоям, очистка."""
import os

import numpy as np
import pytest

from vpc.render import analysis_cache as ac
from vpc.analyzer import AudioFeatures, Segment, SegmentType, N_BINS


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / 'cache'
    monkeypatch.setattr(ac, '_cache_dir', lambda: str(d))
    return d


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / 'song.wav'
    p.write_bytes(b'RIFF' + b'\x00' * 1000)
    return str(p)


def _payload():
    seg = Segment(t_start=0.0, t_end=1.0, duration=1.0,
                  type=SegmentType.IMPACT, intensity=0.5, rms=0.1,
                  flatness=0.2, rms_change=0.0)
    n = 8
    feats = AudioFeatures(
        times=np.linspace(0, 1, n), bass=np.zeros(n), mid=np.zeros(n),
        high=np.zeros(n), onset=np.zeros(n), bins=np.zeros((n, N_BINS)),
        sr=22050, hop=512, y=np.zeros(4096, np.float32))
    return ([seg], 1.0, feats, 128.0)


def test_make_key_stable_and_sensitive(audio_file):
    p = dict(loud_thresh=1.2, snap_to_beat=True)
    k1 = ac.make_key(audio_file, p)
    k2 = ac.make_key(audio_file, dict(p))
    assert k1 == k2 and k1
    # Другой параметр -> другой ключ.
    assert ac.make_key(audio_file, dict(p, loud_thresh=1.3)) != k1
    # Другое окно -> другой ключ.
    assert ac.make_key(audio_file, p, window=(1.0, 2.0)) != k1
    assert ac.make_key(audio_file, p, window=(1.0, 2.0)) != \
        ac.make_key(audio_file, p, window=(1.0, 3.0))


def test_make_key_none_for_missing_file(tmp_path):
    assert ac.make_key(str(tmp_path / 'nope.wav'), {}) is None
    assert ac.make_key('', {}) is None


def test_make_key_changes_when_file_changes(audio_file):
    k1 = ac.make_key(audio_file, {})
    with open(audio_file, 'ab') as f:
        f.write(b'\x01' * 500)   # меняем размер (и mtime)
    assert ac.make_key(audio_file, {}) != k1


def test_store_load_roundtrip(cache_dir, audio_file):
    key = ac.make_key(audio_file, {'x': 1})
    ac.store(key, _payload())
    got = ac.load(key)
    assert got is not None
    segs, dur, feats, bpm = got
    assert len(segs) == 1 and segs[0].type == SegmentType.IMPACT
    assert dur == 1.0 and bpm == 128.0
    assert feats.bins.shape == (8, N_BINS)
    assert feats.y.shape == (4096,)


def test_load_miss_returns_none(cache_dir):
    assert ac.load('deadbeef') is None
    assert ac.load(None) is None


def test_load_corrupt_file_returns_none(cache_dir, audio_file):
    key = ac.make_key(audio_file, {})
    os.makedirs(str(cache_dir), exist_ok=True)
    with open(ac._path_for(key), 'wb') as f:
        f.write(b'not a pickle at all')
    assert ac.load(key) is None


def test_invalid_payload_not_stored(cache_dir, audio_file):
    key = ac.make_key(audio_file, {})
    ac.store(key, ('wrong', 'shape'))          # не 4-кортеж нужной формы
    assert ac.load(key) is None
    ac.store(key, ([], 'notnum', None, 1.0))   # duration не число
    assert ac.load(key) is None


def test_prune_bounds_file_count(cache_dir, audio_file, monkeypatch):
    monkeypatch.setattr(ac, '_MAX_FILES', 3)
    # Кладём больше файлов, чем лимит; последний store должен подрезать.
    for i in range(6):
        ac.store(ac.make_key(audio_file, {'i': i}), _payload())
    files = [f for f in os.listdir(str(cache_dir)) if f.endswith('.pkl')]
    assert len(files) <= 3
