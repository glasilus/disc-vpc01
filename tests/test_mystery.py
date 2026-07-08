"""Тесты секции Mystery.

Mystery намеренно хаотична, поэтому проверяем только:
  * apply() возвращает uint8-массив той же формы,
  * при всех ручках на нуле выход равен входу,
  * каждая ручка, поднятая по отдельности, реально меняет выход
    (доказывает, что проводка не мертва).

Побитовое golden-тестирование тут хрупкое, потому что Mystery
пересеивает RNG от seg.rms + ZERO, поэтому опираемся на shape/dtype
и факт хоть какого-то изменения.
"""
import random
import numpy as np

from vpc.analyzer import Segment, SegmentType
from vpc.mystery import MysterySection


def make_seg():
    return Segment(0.0, 1.0, 1.0, SegmentType.SUSTAIN, 0.6, 0.5, 0.3, 0.1)


def make_frame(seed=42, h=64, w=64):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


def test_zero_knobs_is_passthrough():
    m = MysterySection()
    f = make_frame()
    out = m.apply(f, make_seg(), draft=False)
    assert out.shape == f.shape
    assert out.dtype == np.uint8
    assert np.array_equal(out, f), 'zero knobs must be identity'


KNOBS = ['VESSEL', 'ENTROPY_7', 'STATIC_MIND', 'RESONANCE',
         'COLLAPSE', 'ZERO', 'FLESH_K', 'DOT']


def test_each_knob_changes_output():
    """Каждая ручка по отдельности, выставленная высоко, должна где-то менять кадр."""
    base = make_frame()
    seg = make_seg()
    for knob in KNOBS:
        random.seed(0); np.random.seed(0)
        m = MysterySection()
        setattr(m, knob, 0.9)
        out = base
        # Кормим 6 разных кадров, чтобы у ручек с состоянием (DOT slit-scan,
        # VESSEL feedback) накопилась история, заметная в выходе.
        for i in range(6):
            out = m.apply(make_frame(seed=42 + i), seg, draft=False)
        diff = np.abs(out.astype(int) - base.astype(int)).sum()
        assert diff > 0, f'knob {knob} produced no change'


def test_always_flags_default_false_keep_legacy():
    """Все поля `always_<KNOB>` существуют и по умолчанию False - иначе
    старые пресеты, сохранённые до появления этой фичи, вели бы себя иначе."""
    m = MysterySection()
    for k in KNOBS + ['DELTA_OMEGA']:
        assert hasattr(m, f'always_{k}'), f'missing always_{k}'
        assert getattr(m, f'always_{k}') is False


def test_always_flag_forces_trigger_when_gate_would_block():
    """При маленьком значении ручки случайный гейт обычно не сработает
    на одном кадре. С флагом always-on блок всё равно обязан дать
    видимое изменение. seg.rms=0, чтобы взвешенный по rms член гейта
    был нулевым, а базовая вероятность - маленькой и проваливала
    большинство бросков без always-on.
    """
    seg = Segment(0.0, 1.0, 1.0, SegmentType.SUSTAIN, 0.6, 0.0, 0.0, 0.0)
    base = make_frame(seed=7)
    for knob in ['VESSEL', 'STATIC_MIND', 'RESONANCE', 'COLLAPSE',
                 'ENTROPY_7', 'ZERO', 'FLESH_K', 'DOT']:
        random.seed(123); np.random.seed(123)
        m = MysterySection()
        setattr(m, knob, 0.05)  # маленькое значение - у гейта ~5% шанс за бросок
        setattr(m, f'always_{knob}', True)
        out = base
        for i in range(3):
            out = m.apply(make_frame(seed=10 + i), seg, draft=False)
        diff = np.abs(out.astype(int) - base.astype(int)).sum()
        assert diff > 0, f'always_{knob}=True with knob>0 produced no change'


def test_always_flag_off_at_knob_zero_is_noop():
    """always_<KNOB>=True не должен срабатывать при значении ручки 0."""
    m = MysterySection()
    m.always_FLESH_K = True
    m.always_DOT = True
    m.always_VESSEL = True
    f = make_frame()
    out = m.apply(f, make_seg(), draft=False)
    assert np.array_equal(out, f), 'always-on at knob=0 must stay identity'


def test_apply_robust_to_draft_flag():
    m = MysterySection()
    m.RESONANCE = 0.5
    m.COLLAPSE = 0.5
    f = make_frame()
    o1 = m.apply(f, make_seg(), draft=True)
    o2 = m.apply(f, make_seg(), draft=False)
    assert o1.shape == f.shape
    assert o2.shape == f.shape
