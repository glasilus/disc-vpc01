"""FFmpeg pipe sink — receives raw RGB frames, writes encoded video+audio."""
from __future__ import annotations

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


class FFmpegSink:
    """Spawns ffmpeg, accepts raw uint8 RGB frame bytes, finalises on close."""

    def __init__(self, *, width: int, height: int, fps: int,
                 audio_path: str, output_path: str,
                 vcodec: str = 'libx264', preset: str = 'medium',
                 crf: int = 18, target_duration: Optional[float] = None,
                 extra_v_flags: Optional[list] = None):
        self.width = width
        self.height = height
        self.fps = fps
        self.output_path = output_path
        self._proc: Optional[subprocess.Popen] = None
        self._cmd = [
            ffmpeg_bin(), '-y',
            '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}',
            '-pix_fmt', 'rgb24',
            '-r', str(fps),
            '-i', 'pipe:0',
            '-i', audio_path,
            '-vcodec', vcodec,
            '-pix_fmt', 'yuv420p',
            '-preset', preset,
            '-crf', str(crf),
        ]
        if extra_v_flags:
            self._cmd.extend(extra_v_flags)
        self._cmd.extend(['-acodec', 'aac'])
        if target_duration is not None:
            self._cmd.extend(['-t', str(target_duration)])
        self._cmd.extend(['-shortest', '-movflags', '+faststart', output_path])

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
