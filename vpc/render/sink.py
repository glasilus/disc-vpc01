"""FFmpeg pipe sink — receives raw RGB frames, writes encoded video+audio."""
from __future__ import annotations

import os
import subprocess
import threading
import shutil
import sys
import stat
from typing import Optional


def ffmpeg_bin() -> str:
    """Path to ffmpeg — prioritizes native macOS Homebrew/MacPorts builds,
    then falls back to imageio-ffmpeg bundled binary, then system PATH."""
    if sys.platform == 'darwin':
        # macOS .app bundles do not inherit the user's PATH. Prioritize
        # native global installs (avoiding Rosetta 2 overhead and gaining
        # hardware acceleration) before falling back to the bundled wheel.
        for p in ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/local/bin/ffmpeg']:
            resolved = shutil.which(p)
            if resolved:
                return resolved

    try:
        import imageio_ffmpeg
        
        # PyInstaller --collect-all often strips +x from datas (which is how
        # imageio_ffmpeg binaries are packaged). get_ffmpeg_exe() checks os.X_OK
        # and raises an exception if false. We must proactively restore +x.
        bin_dir = os.path.join(os.path.dirname(imageio_ffmpeg.__file__), 'binaries')
        if os.path.isdir(bin_dir):
            for f in os.listdir(bin_dir):
                if 'ffmpeg' in f:
                    bin_path = os.path.join(bin_dir, f)
                    try:
                        st = os.stat(bin_path)
                        if not (st.st_mode & stat.S_IXUSR):
                            os.chmod(bin_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    except Exception:
                        pass

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass

    resolved = shutil.which('ffmpeg')
    if resolved:
        return resolved

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
                 extra_v_flags: Optional[list] = None,
                 tune: Optional[str] = None,
                 input_pix_fmt: Optional[str] = None,
                 rate_control_args: Optional[list] = None):
        self.width = width
        self.height = height
        self.fps = fps
        self.output_path = output_path
        self._proc: Optional[subprocess.Popen] = None
        # Auto-pick input pix_fmt: when output is yuv420p we can feed
        # planar I420 (1.5 bytes/pixel) instead of RGB24 (3 bytes/pixel),
        # halving pipe bandwidth. For 10-bit / 4:2:2 outputs (ProRes) we
        # stay on rgb24 — converting through I420 would lose chroma
        # detail before ffmpeg even gets the frame.
        if input_pix_fmt is None:
            input_pix_fmt = 'yuv420p' if pix_fmt == 'yuv420p' else 'rgb24'
        self.input_pix_fmt = input_pix_fmt
        ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        self._cmd = [
            ffmpeg_bin(), '-y',
            '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}',
            '-pix_fmt', input_pix_fmt,
            '-r', str(fps),
            '-i', 'pipe:0',
            '-i', audio_path,
            '-vcodec', vcodec,
            '-pix_fmt', pix_fmt,
        ]
        # Rate-control flags. Two paths:
        #   1. If `rate_control_args` is supplied, use it verbatim — this
        #      is the encoders.py-driven path (needed for HW encoders
        #      whose flags don't match the x264 family). Engine builds
        #      these via `build_rate_control_args(spec, crf, preset, tune)`.
        #   2. Otherwise the legacy auto-pick: x264/x265 get -preset/-crf
        #      (+ -tune), VP9 gets -crf/-deadline. This branch is what
        #      the sink-only callers and existing tests exercise.
        if rate_control_args is not None:
            self._cmd.extend(rate_control_args)
        elif vcodec in ('libx264', 'libx265'):
            self._cmd.extend(['-preset', preset, '-crf', str(crf)])
            if tune and str(tune).lower() not in ('', 'none'):
                self._cmd.extend(['-tune', str(tune).lower()])
        elif vcodec == 'libvpx-vp9':
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
        # Capture stderr (NVENC init failures live here) but cap the
        # retained tail. ffmpeg can emit warnings on every GOP for noisy
        # encoders; an unbounded list grew into tens of MB on long
        # renders and pinned the GIL on every append.
        self._stderr_chunks: list[bytes] = []
        self._stderr_total = [0]
        STDERR_CAP_BYTES = 64 * 1024  # last 64 KiB is plenty for diagnostics.

        def _drain(pipe, sink, total):
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    sink.append(chunk)
                    total[0] += len(chunk)
                    # Trim from the head so we keep a rolling tail.
                    while total[0] > STDERR_CAP_BYTES and len(sink) > 1:
                        head = sink.pop(0)
                        total[0] -= len(head)
            except Exception:
                pass
        threading.Thread(target=_drain, args=(self._proc.stderr,
                                              self._stderr_chunks,
                                              self._stderr_total),
                         daemon=True).start()
        return self

    def early_failure(self, wait: float = 0.4) -> Optional[str]:
        """If ffmpeg has already exited (typical for HW encoder init
        failures — they die before the first frame is written), wait up
        to `wait` seconds and return the captured stderr tail. Returns
        None if the process is still alive and accepting bytes."""
        if self._proc is None:
            return 'sink not open'
        try:
            self._proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            return None  # still running — assume OK
        # Process died; gather stderr.
        tail = b''.join(self._stderr_chunks).decode(errors='replace')
        return tail[-2000:] if tail else f'ffmpeg exited (rc={self._proc.returncode})'

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
