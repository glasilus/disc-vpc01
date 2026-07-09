"""Тесты настоящего кодекового датамоша (TrueDatamoshEffect) и разделения
Datamosh -> Optical Flow.

Покрыто:
  * реестр: старые ключи fx_datamosh по-прежнему управляют optical-flow
    эффектом (обратная совместимость пресетов), новые ключи fx_truemosh
    существуют с разумными дефолтами и встраиваются в цепочку сразу
    после Optical Flow;
  * контракт цепочки: 1 кадр на входе -> 1 кадр на выходе, shape и dtype
    сохраняются в любом режиме;
  * мош реально мошит: во время сработавшего эпизода вывод расходится с
    чистым входом (тянется устаревший референс), а несработавший сегмент
    возвращается к чистому passthrough;
  * стабильность: apply() не падает даже на вырожденном входе (крошечные
    кадры, нечётные размеры, однородный цвет).
"""
from __future__ import annotations

import numpy as np
import pytest

from vpc.analyzer import Segment, SegmentType
from vpc.effects.core import OpticalFlowEffect, DatamoshEffect
from vpc.effects.mosh import TrueDatamoshEffect, _AV_OK
from vpc.registry import EFFECTS, build_chain, default_cfg, find_spec


def _seg(t0: float, t1: float, stype=SegmentType.NOISE,
         intensity: float = 0.7) -> Segment:
    return Segment(t0, t1, t1 - t0, stype, intensity, 0.3, 0.1, 0.05)


def _scene_a(i: int, h: int = 96, w: int = 128) -> np.ndarray:
    f = np.zeros((h, w, 3), np.uint8)
    f[:, :, 0] = 30
    x = 10 + i * 4
    f[20:70, x:x + 24, 1] = 220
    return f


def _scene_b(i: int, h: int = 96, w: int = 128) -> np.ndarray:
    f = np.full((h, w, 3), 200, np.uint8)
    y = 8 + i * 3
    f[y:y + 20, 30:100, 2] = 40
    return f


# реестр / совместимость пресетов

def test_old_datamosh_keys_drive_optical_flow():
    spec = find_spec('datamosh')
    assert spec is not None
    assert spec.enable_key == 'fx_datamosh'
    assert spec.chance_key == 'fx_datamosh_chance'
    assert spec.cls is OpticalFlowEffect
    assert spec.label == 'Optical Flow'
    # алиас нужен, чтобы внешний импорт старого имени продолжал работать
    assert DatamoshEffect is OpticalFlowEffect


def test_true_datamosh_spec_registered():
    spec = find_spec('true_datamosh')
    assert spec is not None
    assert spec.group == 'CORE FX'
    assert spec.enable_key == 'fx_truemosh'
    assert spec.enabled_default is False
    keys = [p.key for p in spec.params]
    assert keys == ['fx_truemosh_mode', 'fx_truemosh_bloom',
                    'fx_truemosh_crunch']
    # Порядок применения: True Datamosh стоит между OVERLAYS и PAINT, чтобы
    # мошить оверлеи, но не задевать paint/dvd (они рисуются поверх).
    ids = [s.id for s in EFFECTS]
    assert ids.index('overlay') < ids.index('true_datamosh') < ids.index('paint')


def test_old_preset_config_enables_only_optical_flow():
    """Пресет, сохранённый до разделения эффектов, ничего не знает про
    fx_truemosh: наложение его на дефолты реестра должно включить
    optical-flow и оставить True Datamosh выключенным."""
    cfg = default_cfg()
    cfg.update({'fx_datamosh': True, 'fx_datamosh_chance': 0.8})
    chain = build_chain(cfg)
    names = [type(fx).__name__ for fx in chain]
    assert 'OpticalFlowEffect' in names
    assert 'TrueDatamoshEffect' not in names


def test_both_effects_build_in_order():
    cfg = default_cfg()
    cfg.update({'fx_datamosh': True, 'fx_truemosh': True})
    names = [type(fx).__name__ for fx in build_chain(cfg)]
    # Optical Flow остаётся в CORE FX (рано), True Datamosh теперь применяется
    # заметно позже (между оверлеями и paint), поэтому просто позже optical-flow.
    assert 'OpticalFlowEffect' in names and 'TrueDatamoshEffect' in names
    assert names.index('TrueDatamoshEffect') > names.index('OpticalFlowEffect')


# контракт цепочки

pytestmark_needs_av = pytest.mark.skipif(not _AV_OK, reason='PyAV missing')


@pytestmark_needs_av
@pytest.mark.parametrize('mode', ['melt', 'bloom', 'hybrid'])
def test_one_in_one_out_all_modes(mode):
    fx = TrueDatamoshEffect(enabled=True, chance=1.0, mode=mode,
                            bloom_frames=4, crunch=0.4)
    seg1 = _seg(0.0, 0.5, SegmentType.SUSTAIN)
    seg2 = _seg(0.5, 1.0, SegmentType.NOISE)
    n_out = 0
    for i in range(6):
        out = fx.apply(_scene_a(i), seg1, draft=True)
        assert out.shape == (96, 128, 3) and out.dtype == np.uint8
        n_out += 1
    for i in range(10):
        out = fx.apply(_scene_b(i), seg2, draft=True)
        assert out.shape == (96, 128, 3) and out.dtype == np.uint8
        n_out += 1
    assert n_out == 16


@pytestmark_needs_av
def test_melt_diverges_then_clean_segment_resyncs():
    fx = TrueDatamoshEffect(enabled=True, chance=1.0, mode='melt', crunch=0.3)
    seg1 = _seg(0.0, 0.5, SegmentType.SUSTAIN)
    seg2 = _seg(0.5, 1.0, SegmentType.NOISE)
    for i in range(6):
        fx.apply(_scene_a(i), seg1, draft=True)

    diffs = []
    for i in range(8):
        clean = _scene_b(i)
        out = fx.apply(clean, seg2, draft=True)
        diffs.append(np.abs(out.astype(np.float32) - clean.astype(np.float32)).mean())
    # мошнутая картинка не должна повторять чистый вход: декодер всё ещё
    # тянет за собой сцену A
    assert max(diffs) > 20.0, f'no melt detected, diffs={diffs}'

    # сегмент, не прошедший триггер (chance=0), должен вернуть кадр без изменений
    fx.chance = 0.0
    seg3 = _seg(1.0, 1.5, SegmentType.NOISE)
    clean = _scene_a(0)
    out = fx.apply(clean, seg3, draft=True)
    assert np.array_equal(out, clean)


@pytestmark_needs_av
def test_disabled_or_silence_is_passthrough():
    fx = TrueDatamoshEffect(enabled=False, chance=1.0)
    src = _scene_a(0)
    assert np.array_equal(fx.apply(src, _seg(0, 1), draft=True), src)

    fx2 = TrueDatamoshEffect(enabled=True, chance=1.0)
    sil = _seg(0.0, 1.0, SegmentType.SILENCE)
    assert np.array_equal(fx2.apply(src, sil, draft=True), src)


@pytestmark_needs_av
def test_degenerate_input_never_raises():
    fx = TrueDatamoshEffect(enabled=True, chance=1.0, mode='hybrid',
                            bloom_frames=3, crunch=1.0)
    # нечётные размеры и почти однородный цвет
    seg1 = _seg(0.0, 0.5, SegmentType.SUSTAIN)
    seg2 = _seg(0.5, 1.0, SegmentType.IMPACT)
    for i in range(3):
        out = fx.apply(np.full((31, 45, 3), 128, np.uint8), seg1, draft=False)
        assert out.shape == (31, 45, 3)
    for i in range(5):
        out = fx.apply(np.full((31, 45, 3), 20 + i, np.uint8), seg2, draft=False)
        assert out.shape == (31, 45, 3)


@pytestmark_needs_av
def test_resolution_change_resets_episode():
    fx = TrueDatamoshEffect(enabled=True, chance=1.0, mode='melt')
    seg1 = _seg(0.0, 0.5, SegmentType.NOISE)
    for i in range(4):
        fx.apply(_scene_a(i), seg1, draft=True)
    bigger = np.zeros((120, 160, 3), np.uint8)
    out = fx.apply(bigger, seg1, draft=True)
    assert out.shape == (120, 160, 3)
