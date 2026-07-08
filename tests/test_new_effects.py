"""Проверка shape/dtype и реального изменения кадра для новых visual-эффектов.

Golden-хэши тут не годятся - у всех эффектов есть случайность или
внутреннее состояние. Вместо этого каждый эффект обязан: сохранять
форму кадра, возвращать uint8 и реально менять кадр при ненулевой
интенсивности. Вызов идёт через публичный BaseEffect.apply(), так что
заодно проверяется, что гейтинг цепочки не пропускает эффекты.
"""
from __future__ import annotations

import numpy as np
import pytest

from vpc.analyzer import Segment, SegmentType
from vpc.effects.vhs import VHSTapeEffect
from vpc.effects.broken import (
    SelfCannibalizeEffect,
    VSyncRollEffect,
    PFrameLagEffect,
    BitFlipEffect,
    WrongMotionVectorEffect,
)
from vpc.effects.virus import CursorStormEffect, BSODShredEffect


def _seg(intensity: float = 0.7) -> Segment:
    return Segment(0.0, 1.0, 1.0, SegmentType.SUSTAIN, intensity, 0.3, 0.1, 0.05)


def _frame(seed: int = 7, h: int = 180, w: int = 320) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


@pytest.mark.parametrize('cls,kwargs', [
    (VHSTapeEffect, {}),
    (VHSTapeEffect, {'dust': True}),
    (SelfCannibalizeEffect, {}),
    (CursorStormEffect, {}),
    (BSODShredEffect, {}),
    (VSyncRollEffect, {}),
    (PFrameLagEffect, {}),
    (BitFlipEffect, {}),
    (WrongMotionVectorEffect, {}),
])
def test_preserves_shape_and_dtype(cls, kwargs):
    fx = cls(enabled=True, chance=1.0,
             intensity_min=0.6, intensity_max=0.6, **kwargs)
    src = _frame()
    out = fx.apply(src, _seg(), draft=False)
    assert out.shape == src.shape
    assert out.dtype == np.uint8


@pytest.mark.parametrize('cls,kwargs', [
    (VHSTapeEffect, {}),
    (SelfCannibalizeEffect, {}),
    (CursorStormEffect, {}),
    (BSODShredEffect, {}),
    (VSyncRollEffect, {}),
    (BitFlipEffect, {}),
    (WrongMotionVectorEffect, {}),
])
def test_actually_mutates_at_non_zero_intensity(cls, kwargs):
    fx = cls(enabled=True, chance=1.0,
             intensity_min=0.7, intensity_max=0.7, **kwargs)
    src = _frame()
    out = fx.apply(src, _seg(), draft=False)
    diff = int(np.abs(out.astype(int) - src.astype(int)).sum())
    assert diff > 0, f'{cls.__name__} produced identical output to input'


def test_pframe_lag_warmup_then_smear():
    """На самом первом кадре PFrameLag - no-op (буфера prev ещё нет),
    но на втором, отличающемся кадре обязан дать смаз."""
    fx = PFrameLagEffect(enabled=True, chance=1.0,
                         intensity_min=0.7, intensity_max=0.7)
    a = _frame(seed=1)
    b = _frame(seed=2)
    out1 = fx.apply(a, _seg(), draft=False)
    assert np.array_equal(out1, a), 'first frame should be identity (warmup)'
    out2 = fx.apply(b, _seg(), draft=False)
    assert not np.array_equal(out2, b), 'second frame should smear'


def test_pframe_lag_keeps_prev_fresh_when_chance_fails():
    """Даже если бросок chance не выпал, PFrameLag.apply обязан обновить
    свой буфер prev (кастомный override apply) - иначе следующее
    срабатывание смажет кадр против устаревшего prev и даст заметный скачок."""
    # chance=0 отключает эффект; apply() уйдёт по короткому пути, но
    # override всё равно должен обновлять _prev.
    fx = PFrameLagEffect(enabled=True, chance=0.0,
                         intensity_min=0.7, intensity_max=0.7)
    a = _frame(seed=1)
    b = _frame(seed=2)
    fx.apply(a, _seg(), draft=False)
    fx.apply(b, _seg(), draft=False)
    # _prev должен отражать b (последний вход), а не a.
    assert fx._prev is not None
    assert np.allclose(fx._prev, b.astype(np.float32))


def test_pframe_lag_disabled_drops_state_no_copy():
    """Когда эффект структурно выключен, override `apply` обязан выйти
    рано без копирования кадра в float32 (путь производительности) и
    сбросить буфер prev, чтобы повторное включение стартовало с чистого листа."""
    fx = PFrameLagEffect(enabled=True, chance=1.0,
                         intensity_min=0.7, intensity_max=0.7)
    a = _frame(seed=1)
    b = _frame(seed=2)
    fx.apply(a, _seg(), draft=False)
    # Прогрели буфер, теперь выключаем
    fx.enabled = False
    out = fx.apply(b, _seg(), draft=False)
    assert out is b, 'disabled apply should return frame by identity (no copy)'
    assert fx._prev is None, 'disabled apply should drop prev buffer'


def test_bit_flip_random_alloc_is_uint8_not_float64():
    """Регрессия на повторное появление аллокации маски в float64.
    Напрямую temporary не проверить, но можно косвенно убедиться, что
    эффект отрабатывает на большом кадре без OOM (4K для CI слишком
    тяжело, берём 1080p)."""
    fx = BitFlipEffect(enabled=True, chance=1.0,
                       intensity_min=0.5, intensity_max=0.5)
    big = np.random.randint(0, 256, (1080, 1920, 3), dtype=np.uint8)
    out = fx.apply(big, _seg(), draft=False)
    assert out.shape == big.shape
    assert out.dtype == np.uint8


def test_zero_intensity_is_passthrough():
    """При интенсивности 0 все эффекты обязаны быть no-op."""
    src = _frame()
    for cls in (VHSTapeEffect, SelfCannibalizeEffect,
                CursorStormEffect, BSODShredEffect,
                VSyncRollEffect, PFrameLagEffect,
                BitFlipEffect, WrongMotionVectorEffect):
        fx = cls(enabled=True, chance=1.0,
                 intensity_min=0.0, intensity_max=0.0)
        out = fx.apply(src, _seg(intensity=0.0), draft=False)
        assert np.array_equal(out, src), f'{cls.__name__} not pass-through at intensity 0'


def test_cursor_storm_state_persists_across_frames():
    """Позиции курсоров должны двигаться между кадрами - проверяет,
    что `_pointers` хранит состояние, а не пересоздаётся на каждом вызове."""
    fx = CursorStormEffect(enabled=True, chance=1.0,
                           intensity_min=0.5, intensity_max=0.5)
    src = _frame()
    fx.apply(src, _seg(), draft=False)
    snapshot = [(p.x, p.y) for p in fx._pointers]
    fx.apply(src, _seg(), draft=False)
    moved = [(p.x, p.y) for p in fx._pointers]
    assert snapshot != moved, 'pointers did not advance between frames'


def test_vhstape_dust_changes_output():
    """Переключение `dust` обязано дать другой результат на том же входе
    (доказывает, что ветка dust вообще выполняется)."""
    src = _frame()
    common = dict(enabled=True, chance=1.0,
                  intensity_min=0.5, intensity_max=0.5)
    a = VHSTapeEffect(**common, dust=False)
    b = VHSTapeEffect(**common, dust=True)
    import random; random.seed(0); np.random.seed(0)
    out_a = a.apply(src, _seg(), draft=False)
    random.seed(0); np.random.seed(0)
    out_b = b.apply(src, _seg(), draft=False)
    # Dust рисует тёмные вертикальные линии - результат должен отличаться
    # даже при одинаковом seed RNG. Гарантии на любой seed нет: у dust
    # свой вероятностный гейт, seed 0 подобран опытным путём так, чтобы
    # гейт сработал хотя бы раз.
    assert not np.array_equal(out_a, out_b), 'dust off vs on identical'
