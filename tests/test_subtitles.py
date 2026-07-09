"""Тесты субтитров (SubtitleEffect / decode_subtitles) и общего модуля режимов.

Покрыто:
  * decode: битый/пустой JSON -> []; невалидные реплики (нет текста,
    t_start >= t_end) отбрасываются; поля приводятся к безопасным типам;
  * тайминг: реплика видна только в своём окне [t_start, t_end);
  * перекрытие: несколько реплик активны одновременно, дубликаты допустимы;
  * режимы: overlay кладёт цвет, lag берёт из истории; общий mask_modes
    даёт тот же результат, что старый инлайн Paint;
  * стабильность: apply() не падает на вырожденных входах и всегда
    возвращает кадр той же формы.
"""
from __future__ import annotations

import json

import numpy as np

from vpc.analyzer import Segment, SegmentType
from vpc.effects.subtitles import SubtitleEffect, decode_subtitles
from vpc.effects.mask_modes import apply_mask_mode


def _seg() -> Segment:
    return Segment(0.0, 10.0, 10.0, SegmentType.SUSTAIN, 0.5, 0.3, 0.1, 0.05)


def _frame(h=120, w=160, v=100) -> np.ndarray:
    return np.full((h, w, 3), v, np.uint8)


def _fx(cues, **kw) -> SubtitleEffect:
    fx = SubtitleEffect(cues=decode_subtitles(json.dumps(cues)),
                        enabled=True, **kw)
    return fx


# ── decode ────────────────────────────────────────────────────────────────

def test_decode_bad_input_returns_empty():
    assert decode_subtitles('') == []
    assert decode_subtitles('not json') == []
    assert decode_subtitles('{"not": "a list"}') == []
    assert decode_subtitles('[1, 2, "x"]') == []


def test_decode_filters_invalid_cues():
    cues = decode_subtitles(json.dumps([
        {'text': 'ok', 't_start': 1, 't_end': 2},
        {'text': '', 't_start': 0, 't_end': 5},        # пустой текст
        {'text': 'rev', 't_start': 5, 't_end': 4},     # t_end <= t_start
        {'text': '   ', 't_start': 0, 't_end': 1},     # только пробелы
        'garbage',
    ]))
    assert len(cues) == 1
    assert cues[0]['text'] == 'ok'


def test_decode_normalizes_fields_and_defaults():
    cues = decode_subtitles(json.dumps([
        {'text': 'A', 't_start': 0, 't_end': 1, 'x': 5.0, 'y': -1,
         'color': [300, -5, 40], 'mode': 'bogus'},
    ]))
    c = cues[0]
    assert c['x'] == 1.0 and c['y'] == 0.0           # клампинг 0..1
    assert c['color'] == (255, 0, 40)                # клампинг 0..255
    assert c['mode'] is None                          # неизвестный режим -> дефолт
    assert c['font'] is None


# ── тайминг и перекрытие ────────────────────────────────────────────────────

def test_cue_visible_only_in_window():
    fx = _fx([{'text': 'HI', 'x': 0.5, 'y': 0.5, 'size': 50,
               'color': [0, 255, 0], 'mode': 'overlay',
               't_start': 1.0, 't_end': 3.0}])
    seg = _seg()
    frame = _frame()

    fx.frame_time = 0.5
    assert np.array_equal(fx.apply(frame, seg, True), frame)   # до окна
    fx.frame_time = 2.0
    assert not np.array_equal(fx.apply(frame, seg, True), frame)  # в окне
    fx.frame_time = 3.0
    assert np.array_equal(fx.apply(frame, seg, True), frame)   # ровно на конце (полуинтервал)


def test_overlapping_cues_both_render():
    fx = _fx([
        {'text': 'AAAA', 'x': 0.25, 'y': 0.3, 'size': 40,
         'color': [255, 0, 0], 'mode': 'overlay', 't_start': 0, 't_end': 5},
        {'text': 'BBBB', 'x': 0.75, 'y': 0.7, 'size': 40,
         'color': [0, 0, 255], 'mode': 'overlay', 't_start': 0, 't_end': 5},
    ])
    fx.frame_time = 1.0
    out = fx.apply(_frame(), _seg(), True)
    has_red = bool(((out[:, :, 0] > 200) & (out[:, :, 2] < 50)).any())
    has_blue = bool(((out[:, :, 2] > 200) & (out[:, :, 0] < 50)).any())
    assert has_red and has_blue


# ── режимы / mask_modes ─────────────────────────────────────────────────────

def test_overlay_writes_color_inside_mask():
    frame = _frame(v=10)
    mask = np.full((120, 160), 255, np.uint8)
    mask[40:80, 60:100] = 0                       # штрих в центре
    out = apply_mask_mode(frame, mask, 'overlay',
                          delayed_frame=frame, color=(0, 255, 0), amp=0.0, t=1)
    assert (out[60, 80] == [0, 255, 0]).all()      # внутри штриха - цвет
    assert (out[0, 0] == [10, 10, 10]).all()       # вне - без изменений


def test_lag_pulls_from_delayed_frame():
    frame = _frame(v=200)
    delayed = _frame(v=20)
    mask = np.full((120, 160), 255, np.uint8)
    mask[10:30, 10:30] = 0
    out = apply_mask_mode(frame, mask, 'lag',
                          delayed_frame=delayed, color=(0, 0, 0), amp=0.0, t=1)
    assert (out[20, 20] == [20, 20, 20]).all()     # внутри - задержанный кадр
    assert (out[100, 100] == [200, 200, 200]).all()


def test_empty_mask_returns_same_frame():
    frame = _frame()
    mask = np.full((120, 160), 255, np.uint8)      # ни одного штриха
    out = apply_mask_mode(frame, mask, 'warp_video',
                          delayed_frame=frame, color=(0, 0, 0), amp=5.0, t=3)
    assert out is frame


# ── стабильность ─────────────────────────────────────────────────────────────

def test_degenerate_inputs_never_raise():
    fx = _fx([{'text': 'X', 'x': 0.5, 'y': 0.5, 'size': 30,
               'mode': 'lag_warp', 't_start': 0, 't_end': 100}],
             delay_frames=3, warp_intensity=1.0)
    seg = _seg()
    for hw in [(31, 45), (2, 2), (200, 100)]:
        f = np.full((*hw, 3), 128, np.uint8)
        fx.frame_time = 1.0
        out = fx.apply(f, seg, False)
        assert out.shape == (*hw, 3) and out.dtype == np.uint8


def test_disabled_or_no_cues_is_passthrough():
    seg = _seg()
    frame = _frame()
    fx = _fx([{'text': 'X', 't_start': 0, 't_end': 5}])
    fx.enabled = False
    fx.frame_time = 1.0
    assert np.array_equal(fx.apply(frame, seg, True), frame)

    fx2 = SubtitleEffect(cues=[], enabled=True)
    fx2.frame_time = 1.0
    assert np.array_equal(fx2.apply(frame, seg, True), frame)
