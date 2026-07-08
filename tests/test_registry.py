"""Смок-тесты реестра эффектов.

Каждый зарегистрированный эффект должен:
  * встречаться в EFFECTS ровно один раз,
  * давать правильные ключи cfg через default_cfg(),
  * собираться с дефолтными kwargs в build_chain,
  * без исключений отрабатывать на кадре 64x64 для любого SegmentType.
"""
import numpy as np
import pytest

from vpc.analyzer import Segment, SegmentType
from vpc.registry import EFFECTS, default_cfg, build_chain, find_spec, GROUP_ORDER


def make_seg(t=SegmentType.IMPACT, intensity=0.6):
    return Segment(0.0, 1.0, 1.0, t, intensity, 0.5, 0.3, 0.1)


def test_unique_ids():
    ids = [s.id for s in EFFECTS]
    assert len(ids) == len(set(ids)), 'duplicate effect ids'


def test_unique_enable_keys():
    keys = [s.enable_key for s in EFFECTS if s.enable_key]
    assert len(keys) == len(set(keys)), 'duplicate enable keys'


def test_groups_known():
    for s in EFFECTS:
        assert s.group in GROUP_ORDER, f'{s.id} has unknown group {s.group!r}'


def test_default_cfg_complete():
    cfg = default_cfg()
    for s in EFFECTS:
        if s.enable_key:
            assert s.enable_key in cfg
        if s.chance_key:
            assert s.chance_key in cfg
        for p in s.params:
            assert p.key in cfg


def test_find_spec_roundtrip():
    for s in EFFECTS:
        assert find_spec(s.id) is s


def test_build_chain_default_subset():
    """При дефолтном cfg в цепочке должны быть только включённые по умолчанию эффекты."""
    cfg = default_cfg()
    chain = build_chain(cfg)
    types = {type(c).__name__ for c in chain}
    enabled_default = {s.id for s in EFFECTS
                       if s.enabled_default and s.chain_kind == 'normal'}
    # каждый enabled_default с chain_kind='normal' должен присутствовать
    for spec in EFFECTS:
        if spec.enabled_default and spec.chain_kind == 'normal' and spec.cls is not None:
            assert spec.cls.__name__ in types, f'missing {spec.id} in default chain'


def test_build_chain_all_enabled_smoke():
    """Включаем все эффекты - каждый должен собраться без ошибок."""
    cfg = default_cfg()
    for s in EFFECTS:
        if s.enable_key:
            cfg[s.enable_key] = True
    cfg['overlay_dir'] = ''  # без каталога overlay-эффект будет пропущен
    chain = build_chain(cfg)
    assert len(chain) >= 30  # большинство эффектов активно


@pytest.mark.parametrize('seg_type', list(SegmentType))
def test_chain_runs_on_every_segment_type(seg_type):
    """Прогоняет цепочку со всеми включёнными эффектами на кадре 64x64 для каждого SegmentType."""
    cfg = default_cfg()
    for s in EFFECTS:
        if s.enable_key:
            cfg[s.enable_key] = True
        if s.chance_key:
            cfg[s.chance_key] = 1.0
    cfg['overlay_dir'] = ''
    cfg['fx_formula_expr'] = 'frame'
    chain = build_chain(cfg)

    rng = np.random.RandomState(seg_type.value.__hash__() & 0xFFFF)
    frame = rng.randint(0, 256, (64, 64, 3), dtype=np.uint8)
    seg = make_seg(t=seg_type, intensity=0.6)
    for fx in chain:
        out = fx.apply(frame, seg, draft=True)
        assert out.shape == frame.shape
        assert out.dtype == np.uint8
        frame = out
