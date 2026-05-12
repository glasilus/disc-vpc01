"""Audio defects: pure-function pipeline that mangles WAV samples to
match the visual effects active in passthrough mode.

Public surface:
  defects.defect_vhs_tape, defects.defect_self_echo,
  defects.defect_cursor_clicks, defects.defect_bsod_static
  pipeline.EFFECT_AUDIO_COUPLING
  pipeline.apply_passthrough_audio_defects
"""
from .pipeline import (
    EFFECT_AUDIO_COUPLING,
    apply_passthrough_audio_defects,
)

__all__ = [
    'EFFECT_AUDIO_COUPLING',
    'apply_passthrough_audio_defects',
]
