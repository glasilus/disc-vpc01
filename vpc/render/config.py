"""Typed render configuration.

Wraps the legacy flat cfg dict with typed accessors and validation. The
legacy dict is kept as the source of truth so existing presets continue to
work; this class adds structure on top of it.

Backlog support:
  * Per-effect always-on (backlog #1) — handled in the registry / build_chain
    via `fx_xxx_always` / `fx_xxx_always_int` cfg keys; nothing to wire here.
  * resolution_mode + custom_w / custom_h — backlog #2: resolution can be
    a preset (240/360/480/720/1080), match the source video, or arbitrary
    user-supplied dimensions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


RENDER_DRAFT = 'draft'
RENDER_PREVIEW = 'preview'
RENDER_FINAL = 'final'

_RES_MAP = {
    '240p': (426, 240), '360p': (640, 360), '480p': (854, 480),
    '720p': (1280, 720), '1080p': (1920, 1080),
}


def _coerce_paths(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


@dataclass
class RenderConfig:
    """Typed view over the flat cfg dict."""
    raw: dict = field(default_factory=dict)

    # ----- file paths -----
    @property
    def video_paths(self) -> List[str]:
        return _coerce_paths(self.raw.get('video_paths') or self.raw.get('video_path'))

    @property
    def audio_path(self) -> str:
        return self.raw.get('audio_path', '')

    @property
    def output_path(self) -> str:
        return self.raw.get('output_path', '')

    @property
    def overlay_dir(self) -> str:
        return self.raw.get('overlay_dir', '') or ''

    # ----- resolution & framerate -----
    def output_size(self, mode: str, source_size: Optional[Tuple[int, int]] = None) -> Tuple[int, int]:
        """Final (w, h) for the render. Honours draft override.

        Width/height are always forced to even numbers — yuv420p (and
        prores 422) require chroma subsampling on even dimensions, and
        ffmpeg refuses to start otherwise. Without this, a user typing an
        odd custom_w/custom_h (or a source video with an odd dimension)
        would crash the render at the ffmpeg pipe stage.
        """
        def _even(w: int, h: int) -> Tuple[int, int]:
            return max(2, w - (w & 1)), max(2, h - (h & 1))

        if mode == RENDER_DRAFT:
            return 480, 270
        rmode = self.raw.get('resolution_mode', 'preset')
        if rmode == 'source' and source_size is not None:
            return _even(int(source_size[0]), int(source_size[1]))
        if rmode == 'custom':
            w = int(self.raw.get('custom_w', 1280) or 1280)
            h = int(self.raw.get('custom_h', 720) or 720)
            return _even(w, h)
        w, h = _RES_MAP.get(self.raw.get('resolution', '720p'), (1280, 720))
        return _even(w, h)

    def fps(self, mode: str) -> int:
        if mode == RENDER_DRAFT:
            return 24
        return int(self.raw.get('fps', 24) or 24)

    def encoder_preset(self, mode: str) -> str:
        if mode == RENDER_DRAFT:
            return 'ultrafast'
        return self.raw.get('export_preset', 'medium')

    def crf(self, mode: str) -> int:
        if mode == RENDER_DRAFT:
            return 28
        return int(self.raw.get('crf', 18) or 18)

    @property
    def use_h265(self) -> bool:
        return 'H.265' in self.raw.get('video_codec', 'H.264')

    @property
    def video_codec_label(self) -> str:
        """User-facing codec/container label, e.g. 'H.264 (MP4)'.

        Looked up against EXPORT_FORMATS in sink.py to resolve the actual
        ffmpeg codec/container/pix_fmt triple.
        """
        return self.raw.get('video_codec', 'H.264 (MP4)')

    # ----- audio analysis params -----
    @property
    def chaos(self) -> float:
        return float(self.raw.get('chaos_level', 0.5))

    @property
    def loud_thresh(self) -> float:
        return float(self.raw.get('threshold', 1.2))

    @property
    def transient_thresh(self) -> float:
        return float(self.raw.get('transient_thresh', 0.5))

    @property
    def min_segment_dur(self) -> float:
        return float(self.raw.get('min_cut_duration', 0.05))

    @property
    def snap_to_beat(self) -> bool:
        return bool(self.raw.get('snap_to_beat', False))

    @property
    def snap_tolerance(self) -> float:
        return float(self.raw.get('snap_tolerance', 0.05))

    # ----- scene detection -----
    @property
    def use_scene_detect(self) -> bool:
        return bool(self.raw.get('use_scene_detect', False))

    @property
    def scene_buffer_size(self) -> int:
        return int(self.raw.get('scene_buffer_size', 10) or 10)

    # ----- silence treatment -----
    @property
    def silence_mode(self) -> str:
        return self.raw.get('silence_mode', 'dim')

    # ----- mystery -----
    @property
    def mystery(self) -> dict:
        return dict(self.raw.get('mystery', {}))

    # ----- specials -----
    @property
    def stutter_enabled(self) -> bool:
        return bool(self.raw.get('fx_stutter', False))

    @property
    def flash_enabled(self) -> bool:
        return bool(self.raw.get('fx_flash', False))

    @property
    def flash_chance_base(self) -> float:
        return float(self.raw.get('fx_flash_chance', 0.5))

    @property
    def datamosh_enabled(self) -> bool:
        return bool(self.raw.get('fx_datamosh', False))

    @property
    def datamosh_chance_base(self) -> float:
        return float(self.raw.get('fx_datamosh_chance', 0.5))

    # ----- validation -----
    def validate(self) -> List[str]:
        """Return a list of human-readable problems (empty list if OK)."""
        errors = []
        if not self.audio_path:
            errors.append('audio_path missing')
        if not self.video_paths:
            errors.append('video_paths missing')
        if not self.output_path:
            errors.append('output_path missing')
        rmode = self.raw.get('resolution_mode', 'preset')
        if rmode == 'custom':
            try:
                int(self.raw.get('custom_w', 0))
                int(self.raw.get('custom_h', 0))
            except (TypeError, ValueError):
                errors.append('custom_w / custom_h must be integers')
        return errors
