"""Disc VPC 01 — modular package.

Public API mirrors the old flat layout for backward compatibility:
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
