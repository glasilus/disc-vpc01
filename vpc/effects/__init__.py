"""Effects package — re-exports every concrete effect for backward compat.

Old code can still do `from vpc.effects import PixelSortEffect`. The flat
shim module `effects.py` at the project root re-exports all of these for
the original `from effects import ...` form too.
"""
from .base import BaseEffect, _ensure_uint8, _reseg

from .core import (
    FlashEffect, GhostTrailsEffect, PixelSortEffect, DatamoshEffect, ASCIIEffect,
)

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

from .overlay import OverlayEffect, ChromaKeyEffect, load_overlay_frames

__all__ = [
    'BaseEffect', '_ensure_uint8', '_reseg',
    'FlashEffect', 'GhostTrailsEffect', 'PixelSortEffect', 'DatamoshEffect', 'ASCIIEffect',
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
    'FormulaEffect',
    'OverlayEffect', 'ChromaKeyEffect', 'load_overlay_frames',
]
