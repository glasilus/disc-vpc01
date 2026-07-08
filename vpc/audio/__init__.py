"""Аудио-дефекты: пайплайн чистых функций, портящих WAV-сэмплы так,
чтобы это соответствовало видеоэффектам, активным в режиме passthrough.

Публичный API:
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
