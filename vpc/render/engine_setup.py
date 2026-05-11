"""Self-contained ffmpeg setup helpers used by BreakcoreEngine.

Extracted from `engine.py` so the orchestrator stays focused on the render
pipeline. Each function here is a pure helper — it does not touch engine
state, only the filesystem + ffmpeg subprocess. The engine wraps each call
in a thin method that supplies its `log` callback.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Callable, Optional

import cv2

from .sink import ffmpeg_bin


LogFn = Callable[[str], None]


def extract_audio_track(video_path: str, log: LogFn) -> Optional[str]:
    """Demux the audio of `video_path` into a temp WAV; return its path.

    Returns None if the video has no audio stream or extraction fails — in
    that case the engine still renders, but with no segments and no audio
    in the output.

    Stereo 44.1 kHz s16 is preserved deliberately: this WAV is BOTH analysed
    AND muxed back into the rendered video as the audio track. Downsampling
    here would be audible (mono panorama collapse, lost high-end). The
    analyzer does its own downsample on the in-memory waveform.
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    tmp.close()
    # Probe duration cheaply so we can scale the ffmpeg timeout. The old
    # hard 120 s ceiling killed extraction on 30+ minute passthrough sources
    # mid-write and left an empty/broken WAV behind.
    try:
        _cap = cv2.VideoCapture(video_path)
        _fps = float(_cap.get(cv2.CAP_PROP_FPS) or 24.0)
        _n = float(_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        _cap.release()
        src_dur = (_n / _fps) if _fps > 0 else 0.0
    except Exception:
        src_dur = 0.0
    # ~1 s of wall-clock per 60 s of audio is conservative; clamp to a
    # 60 s floor and a 30 min ceiling to avoid runaway hangs.
    extract_timeout = int(max(60.0, min(1800.0, 60.0 + src_dur)))
    cmd = [
        ffmpeg_bin(), '-y', '-i', video_path,
        '-vn', '-ac', '2', '-ar', '44100', '-sample_fmt', 's16',
        tmp.name,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True,
                                timeout=extract_timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f'Audio extraction failed: {exc}')
        try: os.remove(tmp.name)
        except OSError: pass
        return None
    if result.returncode != 0 or os.path.getsize(tmp.name) == 0:
        err = (result.stderr or b'')[:200].decode(errors='replace').strip()
        log(f'Audio extraction: no track / ffmpeg error ({err}).')
        try: os.remove(tmp.name)
        except OSError: pass
        return None
    return tmp.name


def prepare_datamosh_source(video_path: str, output_path: str,
                            log: LogFn) -> bool:
    """Re-encode `video_path` for "true" datamosh: long-GOP, P-frames only,
    single-reference chains, then strip the source's existing I-frames.

    The encoder flags matter for the look:
      • ``-bf 0`` — kill B-frames. B-frames decode in non-display order
        and reference both directions; they'd reset the smear chain and
        ruin the effect.
      • ``-sc_threshold 0`` — forbid the encoder from inserting its own
        scene-cut I-frames. Without this libx264 silently sprinkles I's
        wherever motion changes a lot, breaking the long P-chain that
        the datamosh look depends on.
      • ``-g 99999 -keyint_min 99999`` — force the longest possible GOP
        so essentially everything is a P-frame.
      • ``-refs 1`` — each P-frame references only its immediate
        predecessor; produces the long, drifting motion-vector chain
        characteristic of "real" datamosh.
      • ``-preset slow`` — far better motion estimation than ultrafast.
        With ultrafast the encoder gives up on hard-to-track regions
        and emits intra blocks INSIDE P-frames, which look like static
        bricks instead of smear.
    Then ``select=not(eq(pict_type,I))`` drops the source's own I-frames
    from the resulting stream so the decoder is forced to reuse the
    previous P-frame's content — that's where the motion smear comes from.
    """
    cmd = [
        ffmpeg_bin(), '-y', '-i', video_path,
        '-vf', 'select=not(eq(pict_type\\,I))',
        '-vsync', 'vfr',
        '-c:v', 'libx264',
        '-preset', 'slow',
        '-bf', '0',
        '-g', '99999',
        '-keyint_min', '99999',
        '-sc_threshold', '0',
        '-refs', '1',
        '-crf', '20',
        '-pix_fmt', 'yuv420p',
        '-an',
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        log(f'Datamosh ffmpeg error: '
            f'{result.stderr[:200].decode(errors="replace")}')
    return result.returncode == 0
