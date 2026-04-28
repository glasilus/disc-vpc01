"""FFmpeg pipe sink — receives raw RGB frames, writes encoded video+audio."""
from __future__ import annotations

import os
import subprocess
import threading
from typing import Optional


def ffmpeg_bin() -> str:
    """Path to ffmpeg — bundled via imageio-ffmpeg when available."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'


# Container/codec presets. Each entry maps a user-facing codec label to:
#   container extension (without dot), video codec, audio codec, optional pix_fmt,
#   optional extra video flags (e.g. profile tag for H.265 in MP4).
EXPORT_FORMATS = {
    'H.264 (MP4)':   {'ext': 'mp4', 'vcodec': 'libx264', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'H.265 (MP4)':   {'ext': 'mp4', 'vcodec': 'libx265', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': ['-tag:v', 'hvc1']},
    'H.264 (MKV)':   {'ext': 'mkv', 'vcodec': 'libx264', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'H.265 (MKV)':   {'ext': 'mkv', 'vcodec': 'libx265', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'H.264 (MOV)':   {'ext': 'mov', 'vcodec': 'libx264', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'ProRes (MOV)':  {'ext': 'mov', 'vcodec': 'prores_ks', 'acodec': 'pcm_s16le',
                      'pix_fmt': 'yuv422p10le',
                      'extra_v': ['-profile:v', '3']},  # ProRes 422 HQ
    'VP9 (WebM)':    {'ext': 'webm', 'vcodec': 'libvpx-vp9', 'acodec': 'libopus',
                      'pix_fmt': 'yuv420p', 'extra_v': ['-row-mt', '1', '-b:v', '0']},
}


class FFmpegSink:
    """Spawns ffmpeg, accepts raw uint8 RGB frame bytes, finalises on close."""

    def __init__(self, *, width: int, height: int, fps: int,
                 audio_path: str, output_path: str,
                 vcodec: str = 'libx264', acodec: str = 'aac',
                 pix_fmt: str = 'yuv420p',
                 preset: str = 'medium',
                 crf: int = 18, target_duration: Optional[float] = None,
                 extra_v_flags: Optional[list] = None):
        self.width = width
        self.height = height
        self.fps = fps
        self.output_path = output_path
        self._proc: Optional[subprocess.Popen] = None
        ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        self._cmd = [
            ffmpeg_bin(), '-y',
            '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}',
            '-pix_fmt', 'rgb24',
            '-r', str(fps),
            '-i', 'pipe:0',
            '-i', audio_path,
            '-vcodec', vcodec,
            '-pix_fmt', pix_fmt,
        ]
        # libvpx-vp9 / prores_ks don't take -preset/-crf the same way as x264/x265.
        if vcodec in ('libx264', 'libx265'):
            self._cmd.extend(['-preset', preset, '-crf', str(crf)])
        elif vcodec == 'libvpx-vp9':
            # Map CRF to VP9 CRF (same scale, 0–63 nominally).
            self._cmd.extend(['-crf', str(crf), '-deadline', 'good',
                              '-cpu-used', '4'])
        if extra_v_flags:
            self._cmd.extend(extra_v_flags)
        self._cmd.extend(['-acodec', acodec])
        if target_duration is not None:
            self._cmd.extend(['-t', f'{target_duration:.3f}'])
        # NOTE: -shortest deliberately omitted. With -shortest, any rounding
        # shortfall in the video frame count truncates the AUDIO stream too,
        # which was the visible "song ends before video" bug. We pad video
        # frames to match target_duration on the engine side instead, then
        # let -t cap the output exactly.
        if ext == 'mp4' or ext == 'mov':
            self._cmd.extend(['-movflags', '+faststart'])
        self._cmd.append(output_path)

    def open(self):
        self._proc = subprocess.Popen(
            self._cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        # Drain stderr in background to keep ffmpeg's pipe buffer from filling.
        def _drain(pipe):
            try:
                pipe.read()
            except Exception:
                pass
        threading.Thread(target=_drain, args=(self._proc.stderr,), daemon=True).start()
        return self

    def write(self, frame_bytes: bytes) -> bool:
        if self._proc is None or self._proc.stdin is None:
            return False
        try:
            self._proc.stdin.write(frame_bytes)
            return True
        except (BrokenPipeError, OSError):
            return False

    def close(self):
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.wait()
