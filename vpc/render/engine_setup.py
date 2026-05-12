"""Self-contained ffmpeg setup helpers used by BreakcoreEngine.

Extracted from `engine.py` so the orchestrator stays focused on the render
pipeline. Each function here is a pure helper — it does not touch engine
state, only the filesystem + ffmpeg subprocess. The engine wraps each call
in a thin method that supplies its `log` callback.
"""
from __future__ import annotations

import hashlib
import os
import random
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from vpc.analyzer import Segment, SegmentType
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
                            log: LogFn, *,
                            mode: str = 'strip') -> bool:
    """Re-encode `video_path` for "true" datamosh in two flavours.

    Common flags (both modes):
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

    Modes:
      • ``mode='strip'`` (cut-mode default) — additionally drops every
        source I-frame via ``select=not(eq(pict_type,I))``. Frame count
        SHRINKS, so this is only safe in cut mode where audio sync
        comes from random sampling, not from 1:1 frame alignment.
      • ``mode='longgop'`` (passthrough mode) — keeps every source
        frame; only the encode side enforces long-GOP P-only output.
        Frame count is preserved 1:1, so audio sync survives, but the
        decoder still produces the characteristic motion-vector smear
        on scene cuts (since the encoder isn't allowed to insert new
        I-frames where the source content jumps).
    """
    cmd = [ffmpeg_bin(), '-y', '-i', video_path]
    if mode == 'strip':
        cmd += ['-vf', 'select=not(eq(pict_type\\,I))', '-vsync', 'vfr']
    elif mode == 'longgop':
        # No filter: keep frame count 1:1 with source so the passthrough
        # loop can still align frames to audio. The encoder flags below
        # are what produce the datamosh look.
        pass
    else:
        log(f'Datamosh prebake: unknown mode {mode!r}, aborting.')
        return False
    cmd += [
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
    # Scale the timeout to the source duration. `-preset slow` plus a
    # 30-min input would blow past any fixed ceiling; without a timeout
    # ffmpeg occasionally hangs on bad streams indefinitely.
    try:
        _cap = cv2.VideoCapture(video_path)
        _fps = float(_cap.get(cv2.CAP_PROP_FPS) or 24.0)
        _n = float(_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        _cap.release()
        src_dur = (_n / _fps) if _fps > 0 else 0.0
    except Exception:
        src_dur = 0.0
    # `slow` preset is roughly 1× realtime on modern hardware; allow 4×
    # headroom and clamp to a 5-min floor / 60-min ceiling.
    timeout = int(max(300.0, min(3600.0, 60.0 + src_dur * 4.0)))
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f'Datamosh prebake failed: {exc}')
        try: os.remove(output_path)
        except OSError: pass
        return False
    if result.returncode != 0:
        log(f'Datamosh ffmpeg error: '
            f'{result.stderr[:200].decode(errors="replace")}')
        return False
    return True


# ─── passthrough stutter / flash event planner ──────────────────────────
# In passthrough the engine has to know stutter triggers BEFORE the
# render loop starts, because the audio pipeline requires a finished WAV
# at sink.open time and the audio loop has to mirror the video loop. The
# planner walks `seg_list` once with a deterministic RNG and returns the
# events it would arm; the render loop then re-creates the SAME RNG from
# the same seed and walks the segments in the same order, so its
# decisions match the planner exactly. This is what keeps the audio loop
# and the video loop pointing at the same chunk of source media.

@dataclass
class StutterEvent:
    """A drill-loop event armed by the planner.

    `trigger_frame_index` is the OUTPUT frame at which the segment that
    fired the trigger starts. The current frame plays naturally as the
    first cycle slot; replacement frames span
    `trigger_frame_index + 1 ... trigger_frame_index + total_replace_frames`.
    """
    trigger_frame_index: int
    loop_size_frames: int
    total_replace_frames: int


# Drill-loop sizing knobs. `LOOP_SIZE_FRAMES = 2` means each loop spans
# two source frames (~83 ms @ 24 fps) and the cycle audibly switches
# every other output frame — that's the rapid drill character vs. the
# longer "freeze and twitch" you'd get with 3-4. `CYCLE_CHOICES` then
# decides how many cycles to play, so total drill duration sits in the
# 83-250 ms window — short enough to read as a STUTTER, not a freeze.
STUTTER_LOOP_SIZE = 2
STUTTER_CYCLE_CHOICES = (2, 3, 4)


def event_seed_for_passthrough(audio_path: str, target_total_frames: int,
                               chaos: float) -> int:
    """Build the deterministic RNG seed for the passthrough event plan.

    Uses audio path + frame count + chaos rounded to 2 decimals. Same
    inputs → same seed → same events both in the planner and the loop,
    which is what keeps audio and video loops mirroring each other.
    """
    sig = f'{audio_path}|{target_total_frames}|{round(chaos, 2)}'
    digest = hashlib.md5(sig.encode('utf-8')).hexdigest()
    return int(digest[:8], 16)


def _trigger_decision(seg: Segment, rc, event_rng: random.Random,
                      flash_chance: float, chaos: float) -> Optional[Tuple[str, dict]]:
    """One unified arming check used by BOTH the planner and the loop.

    Returns ('flash', {'n_flash': N}) or ('stutter', {'cycles': C}) or
    None. The RNG calls (and their order) MUST match between planner
    and loop or the audio loop will land on the wrong source chunk.
    """
    if (rc.flash_enabled
            and seg.type in (SegmentType.DROP, SegmentType.IMPACT)
            and event_rng.random() < flash_chance):
        n_flash = event_rng.randint(1, 2)
        return ('flash', {'n_flash': n_flash})
    if (rc.stutter_enabled
            and seg.type == SegmentType.IMPACT
            and seg.duration < 0.3
            and event_rng.random() < (0.3 + chaos * 0.5)):
        cycles = event_rng.choice(STUTTER_CYCLE_CHOICES)
        return ('stutter', {'cycles': cycles})
    return None


def plan_passthrough_events(seg_list: List[Segment], *,
                            fps: int, rc, flash_chance: float, chaos: float,
                            seed: int) -> List[StutterEvent]:
    """Walk `seg_list` with a deterministic RNG and produce stutter events.

    Mirrors the loop's `cursor != last_trigger_cursor and counters == 0`
    arming logic: between two consecutive segment starts only `dt_frames`
    output frames pass, so a long flash/stutter latch may suppress the
    NEXT segment's trigger entirely. The planner tracks the same residual
    counter so its decisions stay aligned with what the loop will do.
    """
    rng = random.Random(seed)
    events: List[StutterEvent] = []
    state_remaining = 0
    prev_t_start = 0.0
    for i, seg in enumerate(seg_list):
        if i > 0:
            dt = max(0, int(round((seg.t_start - prev_t_start) * fps)))
            state_remaining = max(0, state_remaining - dt)
        prev_t_start = seg.t_start
        if state_remaining > 0:
            # Loop's arming check is gated on counters being zero — same
            # gate here, so this segment doesn't consume RNG calls.
            continue
        decision = _trigger_decision(seg, rc, rng, flash_chance, chaos)
        if decision is None:
            continue
        kind, params = decision
        if kind == 'flash':
            state_remaining = params['n_flash']
        else:  # stutter
            cycles = params['cycles']
            total_replace = STUTTER_LOOP_SIZE * (cycles - 1)
            trigger_fi = int(round(seg.t_start * fps))
            events.append(StutterEvent(
                trigger_frame_index=trigger_fi,
                loop_size_frames=STUTTER_LOOP_SIZE,
                total_replace_frames=total_replace,
            ))
            state_remaining = total_replace
    return events


def apply_passthrough_stutter_audio(audio_path: str,
                                    events: List[StutterEvent],
                                    fps: int, log: LogFn) -> bool:
    """Rewrite `audio_path` (in-place) so each stutter event is audible:
    inside the drill window the audio loops the same chunk as the video.

    Region layout per event (samples computed at the WAV's own rate):
      • LOOP source = audio of frames
        `[trigger_fi - loop_size_frames + 1, trigger_fi + 1)`.
        That's the last `loop_size` source frames including the current
        one (the "first slot" of the drill).
      • REPLACE target = `[trigger_fi + 1, trigger_fi + 1 + total_replace)`.
        The current frame plays naturally; only the residual slots get
        overwritten with copies of LOOP.
    Returns False on unsupported sample widths or wave-module errors —
    caller should treat that as "audio drill skipped, video drill still
    plays" rather than fatal.
    """
    if not audio_path or not events:
        return False
    try:
        with wave.open(audio_path, 'rb') as w:
            n_ch = w.getnchannels()
            sr = w.getframerate()
            sw = w.getsampwidth()
            n_audio_frames = w.getnframes()
            raw = w.readframes(n_audio_frames)
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        if sw not in dtype_map:
            log(f'Stutter audio: unsupported sample width {sw}, '
                f'leaving WAV untouched.')
            return False
        dtype = dtype_map[sw]
        samples = np.frombuffer(raw, dtype=dtype).reshape(-1, n_ch).copy()
        applied = 0
        for ev in events:
            current_frame_end_smp = int(round(
                (ev.trigger_frame_index + 1) * sr / fps))
            loop_dur_smp = int(round(ev.loop_size_frames * sr / fps))
            replace_dur_smp = int(round(ev.total_replace_frames * sr / fps))
            loop_start_smp = max(0, current_frame_end_smp - loop_dur_smp)
            actual_loop_n = current_frame_end_smp - loop_start_smp
            if actual_loop_n <= 0:
                continue
            loop_audio = samples[loop_start_smp:current_frame_end_smp]
            replace_start_smp = current_frame_end_smp
            replace_end_smp = min(replace_start_smp + replace_dur_smp,
                                  len(samples))
            actual_n = replace_end_smp - replace_start_smp
            if actual_n <= 0:
                continue
            n_repeats = (actual_n + actual_loop_n - 1) // actual_loop_n
            repeated = np.tile(loop_audio, (n_repeats, 1))[:actual_n]
            samples[replace_start_smp:replace_end_smp] = repeated
            applied += 1
        with wave.open(audio_path, 'wb') as w:
            w.setnchannels(n_ch)
            w.setsampwidth(sw)
            w.setframerate(sr)
            w.writeframes(samples.tobytes())
        log(f'Stutter audio: applied {applied}/{len(events)} drill loop(s) '
            f'to source WAV.')
        return True
    except (wave.Error, OSError, ValueError) as exc:
        log(f'Stutter audio modification failed: {exc}. '
            f'Video drill will still play; audio will not loop.')
        return False
