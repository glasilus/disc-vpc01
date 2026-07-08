"""Пакет эффектов - реэкспортирует все конкретные эффекты для обратной совместимости.

Старый код по-прежнему может делать `from vpc.effects import PixelSortEffect`.
Плоский shim-модуль `effects.py` в корне проекта реэкспортирует всё это же
и для исходной формы `from effects import ...`.
"""
from .base import BaseEffect, _ensure_uint8, _reseg

from .core import (
    FlashEffect, GhostTrailsEffect, PixelSortEffect, OpticalFlowEffect,
    DatamoshEffect, ASCIIEffect,
)
from .mosh import TrueDatamoshEffect

from .glitch import (
    RGBShiftEffect, BlockGlitchEffect, PixelDriftEffect,
    ColorBleedEffect, FreezeCorruptEffect, NegativeEffect,
)

from .degradation import (
    ScanLinesEffect, BitcrushEffect, JPEGCrushEffect, FisheyeEffect,
    VHSTrackingEffect, InterlaceEffect, BadSignalEffect, DitheringEffect,
    ZoomGlitchEffect, SharpenEffect,
)

from .complex_fx import (
    FeedbackLoopEffect, PhaseShiftEffect, MosaicPulseEffect, EchoCompoundEffect,
    KaliMirrorEffect, GlitchCascadeEffect,
)

from .signal import (
    ResonantRowsEffect, TemporalRGBEffect, FFTPhaseCorruptEffect,
    WaveshaperEffect, HistoLagEffect, WrongSubsamplingEffect,
    GameOfLifeEffect, ELAEffect, DtypeReinterpretEffect, SpatialReverbEffect,
)

from .warp import (
    DerivWarpEffect, VortexWarpEffect, FractalNoiseWarpEffect, SelfDisplaceEffect,
)
from .formula import FormulaEffect
from .paint import PaintCanvasEffect

from .overlay import OverlayEffect, ChromaKeyEffect, load_overlay_frames

__all__ = [
    'BaseEffect', '_ensure_uint8', '_reseg',
    'FlashEffect', 'GhostTrailsEffect', 'PixelSortEffect', 'OpticalFlowEffect',
    'DatamoshEffect', 'TrueDatamoshEffect', 'ASCIIEffect',
    'RGBShiftEffect', 'BlockGlitchEffect', 'PixelDriftEffect',
    'ColorBleedEffect', 'FreezeCorruptEffect', 'NegativeEffect',
    'ScanLinesEffect', 'BitcrushEffect', 'JPEGCrushEffect', 'FisheyeEffect',
    'VHSTrackingEffect', 'InterlaceEffect', 'BadSignalEffect', 'DitheringEffect',
    'ZoomGlitchEffect', 'SharpenEffect',
    'FeedbackLoopEffect', 'PhaseShiftEffect', 'MosaicPulseEffect', 'EchoCompoundEffect',
    'KaliMirrorEffect', 'GlitchCascadeEffect',
    'ResonantRowsEffect', 'TemporalRGBEffect', 'FFTPhaseCorruptEffect',
    'WaveshaperEffect', 'HistoLagEffect', 'WrongSubsamplingEffect',
    'GameOfLifeEffect', 'ELAEffect', 'DtypeReinterpretEffect', 'SpatialReverbEffect',
    'DerivWarpEffect', 'VortexWarpEffect', 'FractalNoiseWarpEffect', 'SelfDisplaceEffect',
    'FormulaEffect', 'PaintCanvasEffect',
    'OverlayEffect', 'ChromaKeyEffect', 'load_overlay_frames',
]
