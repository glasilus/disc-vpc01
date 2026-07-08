"""Disc VPC 01 - модульный пакет.

Публичный API повторяет старую плоскую структуру для обратной совместимости:
    from vpc import BreakcoreEngine, AudioAnalyzer, EFFECTS, MysterySection
"""

from .registry import EFFECTS, EffectSpec, ParamSpec, GROUP_ORDER, find_spec
from .render.engine import BreakcoreEngine
from .render.config import RenderConfig
from .mystery import MysterySection

__all__ = [
    'BreakcoreEngine', 'RenderConfig',
    'EFFECTS', 'EffectSpec', 'ParamSpec', 'GROUP_ORDER', 'find_spec',
    'MysterySection',
]
