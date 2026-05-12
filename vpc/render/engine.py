"""BreakcoreEngine — orchestrates analysis → effect chain → ffmpeg sink.

Slim re-implementation: parsing, analysis, scene detection, effect-chain
construction, datamosh prep and per-segment rendering each live in their own
small method (or in a sibling module). The chain is built from the registry,
not from a hand-written if-ladder.
"""
from __future__ import annotations

import os
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional

import cv2
import numpy as np

from vpc.analyzer import AudioAnalyzer, Segment, SegmentType
from .config import RenderConfig, RENDER_DRAFT, RENDER_FINAL
from .source import VideoPool
from .sink import FFmpegSink, EXPORT_FORMATS, ffmpeg_bin
from .engine_setup import (
    extract_audio_track, prepare_datamosh_source,
    plan_passthrough_events, apply_passthrough_stutter_audio,
    event_seed_for_passthrough, _trigger_decision,
    STUTTER_LOOP_SIZE,
)
from ..audio.pipeline import apply_passthrough_audio_defects
from .encoders import (
    EncoderSpec, find_spec as find_encoder_spec,
    fallback_spec as encoder_fallback_spec,
    build_rate_control_args,
    probe_encoder, last_probe_error,
)

# Datamosh deliberately produces a broken H.264 stream (no I-frames). When
# OpenCV's bundled ffmpeg decodes it, it spams "Invalid NAL unit size",
# "Error splitting the input into NAL units", "partial file" warnings. They
# are expected and harmless — silence them so the log stays readable.
os.environ.setdefault('OPENCV_FFMPEG_LOGLEVEL', '-8')  # quiet
try:
    cv2.setLogLevel(0)  # SILENT
except Exception:
    pass
from ..mystery import MysterySection
from ..registry import build_chain
from ..effects.core import FlashEffect


@dataclass
class _RenderCtx:
    """Shared state passed to the per-mode render loops.

    Mutable fields (`frames_emitted`) live here so `run()` can inspect what
    the loop produced after a BrokenPipeError, even though the loop returned
    early.
    """
    sink: 'FFmpegSink'
    pool: 'VideoPool'
    segments: List[Segment]
    effects: list
    mystery: object
    flash_fx: object
    out_w: int
    out_h: int
    fps: int
    target_duration: float
    target_total_frames: int
    is_draft: bool
    is_final: bool
    chaos: float
    flash_chance: float
    datamosh_cap: object = None
    datamosh_total_frames: int = 0
    # Passthrough-mode datamosh: bitstream-prebaked source that REPLACES
    # the live cap inside `_run_passthrough_loop` (length-preserving
    # long-GOP encode, so 1:1 frame alignment with audio is unchanged).
    # None = no datamosh prebake / not in passthrough mode.
    passthrough_dm_cap: object = None
    # Deterministic seed for passthrough stutter/flash trigger RNG. The
    # planner walks segments before sink.open with a Random(seed) and
    # writes audio loops; the loop builds a fresh Random(seed) and runs
    # the same trigger checks in the same order, producing the same
    # events. None outside passthrough.
    event_rng_seed: Optional[int] = None
    frames_emitted: int = 0


class BreakcoreEngine:
    """Render orchestrator. Public API:

        engine = BreakcoreEngine(cfg, progress_callback)
        engine.run(render_mode='final', max_output_duration=None)
        engine.abort = True   # cooperative cancellation

    `cfg` is the legacy flat dict — RenderConfig wraps it for typed access.
    """

    def __init__(self, config: dict, progress_callback: Optional[Callable] = None):
        self.cfg = config
        self.config = RenderConfig(config)
        self.progress_callback = progress_callback
        self.abort = False
        self.scene_cuts: List[float] = []
        # Path to a temp wav extracted from the source video in passthrough
        # mode. Tracked so the cleanup branch in run() can delete it.
        self._tmp_audio_to_clean: Optional[str] = None

    # ----- logging -----
    def log(self, message: str, value: Optional[int] = None):
        print(f'[ENGINE] {message}')
        if self.progress_callback:
            self.progress_callback(message, value)

    def _log_fx_fail(self, fx, exc: BaseException) -> None:
        """Throttled per-effect failure log.

        A single misbehaving effect used to spam one log line per frame —
        on a 30-min render that's hundreds of thousands of lines, which
        bloated the GUI Text widget and the stdout buffer enough to OOM.
        Now we log the first 3 occurrences then once every 500 frames per
        effect class, and after 100 failures we disable the effect for
        the rest of the render.
        """
        cache = getattr(self, '_fx_fail_counts', None)
        if cache is None:
            cache = {}
            self._fx_fail_counts = cache
        name = type(fx).__name__
        n = cache.get(name, 0) + 1
        cache[name] = n
        if n <= 3 or n % 500 == 0:
            self.log(f'Effect error ({name}) [{n}x]: {exc}')
        if n >= 100 and getattr(fx, 'enabled', False):
            try:
                fx.enabled = False
                self.log(f'{name}: disabled after {n} failures.')
            except Exception:
                pass

    # ----- scene detection -----
    def detect_scenes(self, video_paths: List[str], duration: float):
        if not self.config.use_scene_detect:
            return
        from scenedetect import VideoManager, SceneManager
        from scenedetect.detectors import ContentDetector
        # Hard upper bound on per-source duration we'll scan. Scene
        # detection on a 30-min source took minutes of CPU + a multi-GB
        # RAM spike on the render thread and frequently OOM'd before the
        # render even started. 8 minutes covers the typical music video.
        SCENE_DETECT_LIMIT_SEC = 480
        self.log('Detecting scenes...')
        all_cuts: List[float] = []
        for video_path in video_paths:
            if self.abort:
                break
            # Probe duration cheaply via ffprobe-equivalent (cv2). Skip
            # scene detection for very long sources — they'd OOM the
            # render thread before the first frame is encoded.
            try:
                _cap = cv2.VideoCapture(video_path)
                _fps = float(_cap.get(cv2.CAP_PROP_FPS) or 24.0)
                _n = float(_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                _cap.release()
                src_dur = (_n / _fps) if _fps > 0 else 0.0
            except Exception:
                src_dur = 0.0
            if src_dur > SCENE_DETECT_LIMIT_SEC:
                self.log(f'Skipping scene detection on {os.path.basename(video_path)} '
                         f'({src_dur:.0f}s > {SCENE_DETECT_LIMIT_SEC}s) — too long.')
                continue
            vm = VideoManager([video_path])
            sm = SceneManager()
            sm.add_detector(ContentDetector(threshold=30.0))
            try:
                # Aggressive downscale: PySceneDetect's default 'auto' picks
                # too large a frame for HD content, costing RAM with no
                # detection-quality gain.
                vm.set_downscale_factor(4)
                vm.start()
                sm.detect_scenes(frame_source=vm)
                scene_list = sm.get_scene_list()
                cuts = [x[0].get_seconds() for x in scene_list
                        if x[0].get_seconds() < duration - 1.0]
                all_cuts.extend(cuts)
            except Exception as e:
                self.log(f'Scene detection warning ({video_path}): {e}')
            finally:
                vm.release()
        all_cuts = sorted(set(all_cuts))
        buf = self.config.scene_buffer_size
        self.scene_cuts = all_cuts[:buf] if buf < len(all_cuts) else all_cuts
        self.log(f'Found {len(all_cuts)} scene cuts across '
                 f'{len(video_paths)} source(s), using {len(self.scene_cuts)}.')

    def _get_source_time(self, video_duration: float, seg_duration: float) -> float:
        chaos = self.config.chaos
        if self.scene_cuts and random.random() > chaos * 0.8:
            t = random.choice(self.scene_cuts) + random.uniform(0, 1.0)
        else:
            t = random.uniform(0, max(0, video_duration - seg_duration))
        return max(0.0, min(t, video_duration - seg_duration - 0.1))

    def _cleanup_tmp_audio(self) -> None:
        """Delete the passthrough-extracted temp WAV if any. Idempotent."""
        p = self._tmp_audio_to_clean
        if p and os.path.exists(p):
            try: os.remove(p)
            except OSError: pass
        self._tmp_audio_to_clean = None

    # ----- passthrough audio extraction -----
    def _extract_audio_track(self, video_path: str) -> Optional[str]:
        """Thin wrapper over `engine_setup.extract_audio_track` with our log."""
        return extract_audio_track(video_path, self.log)

    # ----- datamosh helper -----
    def _prepare_datamosh_source(self, video_path: str, output_path: str,
                                 *, mode: str = 'strip') -> bool:
        """Thin wrapper over `engine_setup.prepare_datamosh_source`."""
        return prepare_datamosh_source(video_path, output_path, self.log,
                                       mode=mode)

    # ----- progress / ETA -----
    @staticmethod
    def _fmt_dur(secs: float) -> str:
        """Compact 'Xm YYs' / 'YYs' / '1.2s' formatting for ETA strings."""
        if secs < 0 or secs != secs:  # NaN guard
            return '?'
        if secs < 10:
            return f'{secs:.1f}s'
        secs = int(round(secs))
        if secs < 60:
            return f'{secs}s'
        m, s = divmod(secs, 60)
        if m < 60:
            return f'{m}m{s:02d}s'
        h, m = divmod(m, 60)
        return f'{h}h{m:02d}m'

    def _emit_progress(self, frames_emitted: int, total_frames: int) -> None:
        """Throttled progress + ETA. Called after every encoded frame; only
        actually fires the callback once every PROGRESS_INTERVAL seconds
        of wall time so the GUI doesn't get hammered.

        ETA is linear extrapolation: elapsed * (remaining / done). The
        first second of rendering is excluded (very noisy estimate) — we
        still emit pct, just without ETA.
        """
        now = time.perf_counter()
        if now - self._last_progress_t < 0.5:
            return
        self._last_progress_t = now
        if self.progress_callback is None:
            return
        if total_frames <= 0:
            return
        pct = int(min(100, frames_emitted * 100 // total_frames))
        elapsed = now - self._render_t0
        if frames_emitted >= 1 and elapsed > 1.0:
            rate = frames_emitted / elapsed   # encoded frames per sec
            # Below ~0.5 fps the rate estimate is dominated by setup
            # noise (first segment seek, codec warm-up). Showing
            # "ETA 22h" in that window misleads more than it informs;
            # report a placeholder until at least 5 frames have landed.
            if rate < 0.5 or frames_emitted < 5:
                msg = f'Rendering {pct}% — warming up...'
            else:
                remaining = max(0, total_frames - frames_emitted)
                eta = remaining / rate
                msg = (f'Rendering {pct}% — '
                       f'ETA {self._fmt_dur(eta)} '
                       f'({rate:.1f} fps)')
        else:
            msg = f'Rendering {pct}%...'
        self.progress_callback(msg, pct)

    # ----- effect-chain runner (shared by both render loops) -----
    def _apply_chain(self, frame: np.ndarray, seg: 'Segment',
                     ctx: '_RenderCtx') -> np.ndarray:
        """Run the per-frame effect chain + mystery section.

        Identical body that lived inline twice (`_run_segment_loop` and
        `_run_passthrough_loop`). Behaviour is byte-for-byte equivalent —
        same iteration order, same per-effect throttled error logging.
        """
        for fx in ctx.effects:
            try:
                frame = fx.apply(frame, seg, ctx.is_draft)
            except Exception as e:
                self._log_fx_fail(fx, e)
        try:
            frame = ctx.mystery.apply(frame, seg, ctx.is_draft)
        except Exception as e:
            self._log_fx_fail(ctx.mystery, e)
        return frame

    # ----- pipe packing -----
    @staticmethod
    def _pack_frame(rgb: np.ndarray, input_pix_fmt: str) -> bytes:
        """Convert a uint8 RGB HxWx3 frame to the pipe's pixel format.

        - 'rgb24' → raw bytes, 3 bytes/pixel.
        - 'yuv420p' → planar I420 via OpenCV, 1.5 bytes/pixel. ffmpeg
          would do the same conversion internally, so we lose nothing
          in fidelity but cut the inter-process bandwidth in half.

        Any unrecognised format falls back to rgb24 so a future codec
        misconfig can't silently produce broken bytes.
        """
        if input_pix_fmt == 'yuv420p':
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2YUV_I420).tobytes()
        return rgb.tobytes()

    # ----- silence treatment -----
    def _apply_silence(self, frame: np.ndarray) -> np.ndarray:
        mode = self.config.silence_mode
        if mode == 'dim':
            return (frame.astype(np.float32) * 0.6).clip(0, 255).astype(np.uint8)
        if mode == 'blur':
            return cv2.GaussianBlur(frame, (15, 15), 0)
        if mode == 'both':
            blurred = cv2.GaussianBlur(frame, (11, 11), 0)
            return (blurred.astype(np.float32) * 0.7).clip(0, 255).astype(np.uint8)
        return frame

    # ----- the hot loop -----
    def run(self, render_mode: str = RENDER_FINAL,
            max_output_duration: Optional[float] = None):
        cfg = self.cfg
        rc = self.config
        video_paths = rc.video_paths
        audio_path = rc.audio_path
        output_path = rc.output_path

        # Passthrough mode pulls audio from the source video. If the user
        # also picked an external audio file we still prefer the extracted
        # track — passthrough means "everything from this one video".
        if rc.passthrough_mode:
            if not video_paths:
                self.log('ERROR: passthrough mode requires a source video.')
                return False
            if len(video_paths) > 1:
                self.log(f'Passthrough: {len(video_paths)} sources loaded — '
                         f'using only the first ({os.path.basename(video_paths[0])}).')
            self.log('Passthrough: extracting audio from source video...')
            extracted = self._extract_audio_track(video_paths[0])
            if extracted is None:
                self.log('Passthrough: source has no readable audio. '
                         'Render will continue without effects.')
                audio_path = ''
            else:
                audio_path = extracted
                self._tmp_audio_to_clean = extracted

        if audio_path and not os.path.exists(audio_path):
            self.log(f'ERROR: audio file not found: {audio_path}')
            self._cleanup_tmp_audio()
            return False

        # Outer guard around setup + render: guarantees the temp WAV
        # extracted in passthrough mode is removed even if anything between
        # here and the inner pool/sink finally raises (analyzer constructor,
        # VideoPool, encoder probe, sink open, …).
        try:
            return self._run_inner(render_mode, max_output_duration,
                                    cfg, rc, video_paths,
                                    audio_path, output_path)
        finally:
            self._cleanup_tmp_audio()

    def _run_inner(self, render_mode, max_output_duration,
                   cfg, rc, video_paths, audio_path, output_path):
        is_draft = render_mode == RENDER_DRAFT
        is_final = render_mode == RENDER_FINAL

        # Audio analysis runs first so we can use its duration for sizing.
        # In passthrough mode we may have no audio at all (silent video) —
        # then segments=[] and the loop below renders a passthrough with
        # zero effects (everything classifies as SILENCE).
        segments: List[Segment] = []
        audio_duration = 0.0
        analyzer_bpm = 0.0
        if audio_path:
            self.log('Analyzing audio...')
            analyzer = AudioAnalyzer(
                audio_path,
                min_segment_dur=rc.min_segment_dur,
                loud_thresh=rc.loud_thresh,
                transient_thresh=rc.transient_thresh,
                snap_to_beat=rc.snap_to_beat,
                snap_tolerance=rc.snap_tolerance,
                manual_bpm=rc.manual_bpm,
                use_manual_bpm=rc.use_manual_bpm,
            )
            segments, audio_duration = analyzer.analyze()
            analyzer_bpm = analyzer.detected_bpm
            if audio_duration == 0.0 or not segments:
                self.log('Warning: audio unreadable / no segments — output will have no effects.')

        # Open video pool early — needed both for sizing and for picking the
        # effective duration when running in passthrough.
        pool = VideoPool(video_paths)

        # Effective render duration. Normal mode: governed by audio length.
        # Passthrough: governed by the source video, since output is 1:1
        # with input frames. If audio is shorter than video we pad audio;
        # if audio is longer we ignore the tail (no video to back it).
        if rc.passthrough_mode:
            target_duration = pool.vid_duration
        else:
            target_duration = audio_duration
        if max_output_duration:
            target_duration = min(target_duration, max_output_duration)
        segments = [s for s in segments if s.t_start < target_duration]

        bpm_str = f' | {analyzer_bpm:.1f} BPM' if analyzer_bpm else ''
        self.log(f'Audio: {audio_duration:.1f}s | Segments: {len(segments)}{bpm_str}')

        out_w, out_h = rc.output_size(render_mode, source_size=pool.primary_size)
        fps = rc.fps(render_mode)
        # In passthrough mode the engine reads input frames sequentially and
        # writes one output frame per input frame. If the user-picked output
        # FPS differs from the source's native FPS, video would play at
        # `out_fps / src_fps` × the audio rate — a guaranteed desync. Force
        # output FPS to the source's native rate; tell the user we did so.
        if rc.passthrough_mode:
            try:
                src_fps_native = float(pool.fps_list[0]) if pool.fps_list else 0.0
            except (IndexError, TypeError):
                src_fps_native = 0.0
            if src_fps_native > 0:
                src_fps_int = int(round(src_fps_native))
                if src_fps_int != fps:
                    self.log(f'Passthrough: forcing output FPS to source '
                             f'native {src_fps_int} (was {fps}) to keep audio in sync.')
                    fps = src_fps_int
        preset = rc.encoder_preset(render_mode)
        crf = rc.crf(render_mode)
        self.log(f'Mode: {render_mode} | {out_w}x{out_h} @ {fps}fps | '
                 f'preset={preset} crf={crf}')

        # In passthrough mode the renderer reads frames sequentially — there
        # is no random sampling that could benefit from scene cuts.
        if not rc.passthrough_mode:
            self.detect_scenes(video_paths, pool.vid_duration)

        # Effect chain from the registry.
        effects = build_chain({**cfg, 'overlay_dir': rc.overlay_dir})
        mystery = MysterySection()
        for k, v in rc.mystery.items():
            if hasattr(mystery, k):
                try:
                    setattr(mystery, k, float(v))
                except (TypeError, ValueError):
                    pass
        # Per-knob always-on flags. Unknown keys silently ignored — keeps
        # the engine forward/backward-compatible with cfg dialects.
        for k, v in rc.mystery_always.items():
            attr = f'always_{k}'
            if hasattr(mystery, attr):
                setattr(mystery, attr, bool(v))

        chaos = rc.chaos
        flash_chance = min(1.0, rc.flash_chance_base * (0.3 + 0.7 * chaos))
        flash_fx = FlashEffect(enabled=True, chance=1.0)

        # ----- passthrough stutter audio loop pre-pass -----
        # Done BEFORE sink.open because the WAV is consumed by ffmpeg the
        # moment the sink starts. Determinism via seed → identical events
        # in the loop, so audio loops align to video loops sample-perfect.
        event_rng_seed: Optional[int] = None
        if (rc.passthrough_mode and audio_path
                and (rc.stutter_enabled or rc.flash_enabled)):
            try:
                pre_target_total = int(round(target_duration * fps))
                # Build the same seg_list the loop uses (a leading gap
                # filler if segments[0].t_start > 0; otherwise segments
                # as-is). Triggers are computed against this exact list
                # so cursor walks match.
                if segments and segments[0].t_start > 0:
                    pre_seg_list = [Segment(
                        t_start=0.0, t_end=segments[0].t_start,
                        duration=segments[0].t_start,
                        type=SegmentType.SILENCE,
                        intensity=0.0, rms=0.0, flatness=0.0, rms_change=0.0,
                    )] + list(segments)
                else:
                    pre_seg_list = list(segments)
                event_rng_seed = event_seed_for_passthrough(
                    audio_path, pre_target_total, chaos)
                events = plan_passthrough_events(
                    pre_seg_list, fps=fps, rc=rc,
                    flash_chance=flash_chance, chaos=chaos,
                    seed=event_rng_seed)
                if events:
                    apply_passthrough_stutter_audio(
                        audio_path, events, fps, self.log)
            except Exception as exc:
                self.log(f'Stutter pre-pass skipped: {exc}')
                event_rng_seed = None

        # Audio defects pre-pass — INDEPENDENT of the stutter/flash
        # gate. A user can enable an audio-link without touching stutter
        # or flash; the previous nesting silently swallowed those defects
        # because the outer `if (... stutter or flash)` was False.
        # Defects must always run when (passthrough mode + audio source
        # exist); the defect pipeline itself early-returns if no
        # coupling is enabled, so unconditionally calling here is cheap.
        if rc.passthrough_mode and audio_path:
            try:
                apply_passthrough_audio_defects(audio_path, cfg, self.log)
            except Exception as exc:
                self.log(f'Audio defects skipped: {exc}')

        # ----- ffmpeg sink -----
        # Resolve the encoder spec from the user's chosen label. If the
        # label was saved on a machine with HW support and we don't have
        # it here, drop to the soft fallback before even trying.
        spec = find_encoder_spec(rc.video_codec_label)
        if spec is None:
            self.log(f"Unknown codec label '{rc.video_codec_label}', "
                     f"using {encoder_fallback_spec().label}.")
            spec = encoder_fallback_spec()

        # Runtime self-probe for HW encoders. Some advertised-but-broken
        # encoders accept the pipe at init, then never emit an encoded
        # frame — symptom is a render stuck at 0% forever. Probing with
        # a short testsrc + hard timeout catches these BEFORE we open
        # the real sink, then auto-falls back to libx264. Result is
        # cached per-process, so this only costs ~1-2s on the first
        # render of the session that uses a given HW encoder. Soft
        # codecs are trusted unconditionally and skip the probe.
        if spec.is_hw:
            self.log(f'Probing HW encoder {spec.vcodec} (1s testsrc)...')
            if not probe_encoder(spec, timeout=8.0):
                err = last_probe_error(spec.vcodec) or 'unknown'
                self.log(f'HW encoder {spec.vcodec} probe failed '
                         f'({err}). Falling back to libx264 — render '
                         f'continues automatically.')
                spec = encoder_fallback_spec()
            else:
                self.log(f'HW encoder {spec.vcodec} OK.')

        def _open_sink(s: EncoderSpec) -> FFmpegSink:
            rc_args = build_rate_control_args(
                s, crf=crf, preset=preset, tune=rc.tune)
            sk = FFmpegSink(
                width=out_w, height=out_h, fps=fps,
                audio_path=audio_path, output_path=output_path,
                vcodec=s.vcodec, acodec=s.acodec, pix_fmt=s.pix_fmt,
                preset=preset, crf=crf,
                target_duration=target_duration,
                extra_v_flags=list(s.extra_v),
                tune=rc.tune,
                rate_control_args=rc_args,
            )
            self.log(f'Starting ffmpeg pipe ({s.vcodec})...')
            sk.open()
            return sk

        sink = _open_sink(spec)

        # Hardware encoders die at init when the driver's missing or busy
        # ('No NVENC capable devices', 'Failed to initialize MFX session',
        # 'Cannot load amfrt64.dll'). Probe early — if ffmpeg already
        # exited, swallow the failure and re-open with libx264 instead of
        # bombing the render. Soft codecs skip this check; the timeout
        # is short enough not to be felt.
        if spec.is_hw:
            err = sink.early_failure(wait=0.5)
            if err is not None:
                self.log(f'HW encoder {spec.vcodec} failed at init '
                         f'(falling back to libx264). Cause:\n{err.strip()[:400]}')
                fb = encoder_fallback_spec()
                spec = fb
                sink = _open_sink(fb)

        # ----- datamosh pre-bake -----
        # Both modes use the SAME 'strip' bitstream prebake (long-GOP,
        # P-only, single-reference, then drop every source I-frame). The
        # bitstream effect is the real-deal datamosh smear: forcing the
        # decoder to keep applying motion vectors against stale content
        # because the keyframes that would refresh it are gone.
        #
        # Strip drops frames, so the prebaked file is SHORTER than the
        # source. The two modes diverge only in how the engine consumes
        # the resulting cap:
        #   • Cut mode → swap-on-NOISE: live cap is replaced by the
        #     prebaked one only on NOISE segments. Random sampling means
        #     the shorter length is irrelevant — the engine just seeks
        #     into whichever pool member it's reading.
        #   • Passthrough mode → stretch-replay: the prebaked cap REPLACES
        #     the live cap, and the loop maps each output frame fi to a
        #     prebake index `int(fi * n_prebake / target_total_frames)`.
        #     Each kept P-frame is shown ~target/n_prebake times, so where
        #     the source had an I-frame the surviving P-chain plays out
        #     across multiple output frames as a freeze-and-smear.
        #     `target_total_frames = audio_duration × fps` is unchanged,
        #     so audio still aligns 1:1 with output frames.
        # The Python-side DatamoshEffect (optical-flow smear) runs in
        # both modes via the regular effect chain — independent of and
        # complementary to the bitstream prebake.
        datamosh_source_path = None
        datamosh_cap = None
        datamosh_total_frames = pool.vid_total_frames
        passthrough_dm_cap = None
        passthrough_dm_path = None
        if is_final and rc.datamosh_enabled:
            dm_path = output_path + '_dmosh_src.mp4'
            if os.path.exists(dm_path):
                try: os.remove(dm_path)
                except OSError: pass
            self.log('Preparing datamosh source (strip)...')
            if self._prepare_datamosh_source(video_paths[0], dm_path, mode='strip'):
                if rc.passthrough_mode:
                    cap_try = cv2.VideoCapture(dm_path)
                    if cap_try.isOpened():
                        passthrough_dm_path = dm_path
                        passthrough_dm_cap = cap_try
                        self.log('Datamosh passthrough source ready.')
                    else:
                        cap_try.release()
                        try: os.remove(dm_path)
                        except OSError: pass
                        self.log('Datamosh passthrough cap could not open prebaked '
                                 'file — falling back to live source + optical flow.')
                else:
                    cap_try = cv2.VideoCapture(dm_path)
                    if cap_try.isOpened():
                        datamosh_source_path = dm_path
                        datamosh_cap = cap_try
                        datamosh_total_frames = int(
                            datamosh_cap.get(cv2.CAP_PROP_FRAME_COUNT)) or pool.vid_total_frames
                        self.log('Datamosh source ready.')
                    else:
                        cap_try.release()
                        try: os.remove(dm_path)
                        except OSError: pass
                        self.log('Datamosh cap could not open prebaked file — '
                                 'falling back to optical flow.')
            else:
                self.log('Datamosh pre-processing failed, falling back to optical flow.')

        # ----- main loop -----
        # Total target frames for the whole render. We track frames_emitted
        # against this counter — any rounding shortfall is paid back by
        # padding with the last produced frame after the loop ends, so the
        # encoded video matches the target duration exactly.
        target_total_frames = int(round(target_duration * fps))

        # ETA bookkeeping. _render_t0 starts here (after analysis +
        # scene-detect + datamosh prebake) so the printed ETA reflects
        # the actual encode loop, not setup work. _last_progress_t
        # throttles callbacks to ~2 Hz.
        self._render_t0 = time.perf_counter()
        self._last_progress_t = 0.0

        ctx = _RenderCtx(
            sink=sink, pool=pool, segments=segments,
            effects=effects, mystery=mystery, flash_fx=flash_fx,
            out_w=out_w, out_h=out_h, fps=fps,
            target_duration=target_duration,
            target_total_frames=target_total_frames,
            is_draft=is_draft, is_final=is_final,
            chaos=chaos, flash_chance=flash_chance,
            datamosh_cap=datamosh_cap,
            datamosh_total_frames=datamosh_total_frames,
            passthrough_dm_cap=passthrough_dm_cap,
            event_rng_seed=event_rng_seed,
        )

        try:
            if rc.passthrough_mode:
                frames_emitted = self._run_passthrough_loop(ctx)
            else:
                frames_emitted = self._run_segment_loop(ctx)
        except (BrokenPipeError, OSError):
            self.log('ffmpeg pipe closed early.')
            frames_emitted = ctx.frames_emitted
            # Treat pipe death as failure — without this, the success log
            # ("Done in Xs (Y.YYx realtime)") would print after a crash.
            self.abort = True
        finally:
            pool.release_all()
            if datamosh_cap:
                datamosh_cap.release()
            if datamosh_source_path and os.path.exists(datamosh_source_path):
                try: os.remove(datamosh_source_path)
                except OSError: pass
            if passthrough_dm_cap:
                passthrough_dm_cap.release()
            if passthrough_dm_path and os.path.exists(passthrough_dm_path):
                try: os.remove(passthrough_dm_path)
                except OSError: pass
            sink.close()
            # Temp WAV cleanup is handled by the outer guard in run().

        if not self.abort:
            elapsed = time.perf_counter() - self._render_t0
            rt_factor = (target_duration / elapsed) if elapsed > 0 else 0.0
            self.log(f'Done in {self._fmt_dur(elapsed)} '
                     f'({rt_factor:.2f}x realtime). Output: {output_path}')
            return True
        return False

    # ----- segment-cut loop (default mode) -----
    def _run_segment_loop(self, ctx: '_RenderCtx') -> int:
        """Original cut-and-paste rendering path: per-segment random source
        time, stutter/flash insertion, datamosh swap on NOISE.

        Returns the number of frames actually written to the sink.
        """
        rc = self.config
        sink = ctx.sink; pool = ctx.pool
        last_frame_bytes: Optional[bytes] = None

        for seg in ctx.segments:
            if self.abort:
                break
            seg_dur = min(seg.duration, ctx.target_duration - seg.t_start)
            if seg_dur <= 0:
                break
            # Per-segment target uses cumulative rounding: compute where
            # the segment's tail SHOULD land in absolute frame space and
            # subtract frames already emitted. This eliminates the
            # accumulated half-frame loss the old `int(seg_dur * fps)`
            # path produced over hundreds of segments.
            seg_end_frame = int(round((seg.t_start + seg_dur) * ctx.fps))
            seg_end_frame = min(seg_end_frame, ctx.target_total_frames)
            n_frames = max(1, seg_end_frame - ctx.frames_emitted)

            seg_cap, seg_fps, seg_total_frames, seg_duration = pool.random_cap()
            use_datamosh_src = (
                ctx.is_final and ctx.datamosh_cap is not None
                and seg.type == SegmentType.NOISE
                and rc.datamosh_enabled
                and random.random() < rc.datamosh_chance_base
            )
            active_cap = ctx.datamosh_cap if use_datamosh_src else seg_cap
            active_total_frames = (ctx.datamosh_total_frames
                                   if use_datamosh_src else seg_total_frames)

            src_t = self._get_source_time(seg_duration, seg_dur)
            src_frame_idx = int(src_t * seg_fps)
            active_cap.set(cv2.CAP_PROP_POS_FRAMES,
                           min(src_frame_idx, active_total_frames - 1))

            # Stutter
            stutter_repeat = 1
            if (rc.stutter_enabled and seg.type == SegmentType.IMPACT
                    and seg.duration < 0.3):
                if random.random() < (0.3 + ctx.chaos * 0.5):
                    stutter_repeat = random.choice([2, 4, 8])

            # Flash
            if (rc.flash_enabled
                    and seg.type in (SegmentType.DROP, SegmentType.IMPACT)
                    and random.random() < ctx.flash_chance):
                flash_frames = random.randint(1, 2)
                dummy = np.zeros((ctx.out_h, ctx.out_w, 3), dtype=np.uint8)
                try:
                    flash_frame = ctx.flash_fx._apply(dummy, seg, ctx.is_draft)
                    flash_frame = cv2.resize(flash_frame, (ctx.out_w, ctx.out_h))
                except (cv2.error, ValueError, MemoryError) as e:
                    self._log_fx_fail(ctx.flash_fx, e)
                    flash_frame = dummy
                flash_bytes = self._pack_frame(flash_frame, sink.input_pix_fmt)
                aborted = False
                for _ in range(flash_frames):
                    if ctx.frames_emitted >= ctx.target_total_frames:
                        break
                    if not sink.write(flash_bytes):
                        aborted = True; break
                    ctx.frames_emitted += 1
                    last_frame_bytes = flash_bytes
                if aborted:
                    break

            frames_written = 0
            while frames_written < n_frames:
                ret, frame_bgr = active_cap.read()
                if not ret:
                    active_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame_bgr = active_cap.read()
                    if not ret:
                        break
                frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                frame = cv2.resize(frame, (ctx.out_w, ctx.out_h))

                if seg.type == SegmentType.SILENCE and seg.duration > 1.0:
                    frame = self._apply_silence(frame)

                frame = self._apply_chain(frame, seg, ctx)

                # Cooperative cancel inside the per-segment loop so a long
                # SUSTAIN doesn't make the cancel button feel frozen.
                if frames_written and (frames_written & 31) == 0 and self.abort:
                    break

                fb = self._pack_frame(frame, sink.input_pix_fmt)
                for _ in range(stutter_repeat):
                    if ctx.frames_emitted >= ctx.target_total_frames:
                        break
                    if not sink.write(fb):
                        break
                    frames_written += 1
                    ctx.frames_emitted += 1
                    last_frame_bytes = fb
                    self._emit_progress(ctx.frames_emitted, ctx.target_total_frames)
                    if frames_written >= n_frames:
                        break
            if ctx.frames_emitted >= ctx.target_total_frames:
                break

        # Tail-pad: cover any rounding shortfall so video matches audio.
        if not self.abort and last_frame_bytes is not None:
            pad_count = ctx.target_total_frames - ctx.frames_emitted
            if pad_count > 0:
                self.log(f'Padding tail: {pad_count} frame(s) to match audio.')
                for _ in range(pad_count):
                    if not sink.write(last_frame_bytes):
                        break
                    ctx.frames_emitted += 1
        return ctx.frames_emitted

    # ----- passthrough loop (1:1 source → output) -----
    def _run_passthrough_loop(self, ctx: '_RenderCtx') -> int:
        """Read frames sequentially from the source video; map each frame's
        timestamp to a segment via a monotonic linear cursor (frames advance
        in order, so a full binary search isn't needed).

        Stutter, Flash and Datamosh all work here, but Stutter and Flash
        switch to REPLACE-mode (cut-mode INSERT-mode would shift output
        relative to input audio): for the next N frames they overwrite the
        emitted frame with a held copy (stutter) or a flash colour while
        still calling cap.grab() so the source pointer keeps stepping in
        lockstep with audio. Datamosh is just a regular effect in the chain
        (DatamoshEffect, optical-flow-based motion-vector smear) — no
        prebake here, since dropping I-frames from a 1:1 stream would
        change frame count and desync audio.

        If `segments` is empty (no audio / extraction failed), every frame
        is rendered with a synthesised SILENCE segment, i.e. effects gated
        on non-silence types stay quiet.
        """
        rc = self.config
        sink = ctx.sink; pool = ctx.pool
        # Passthrough datamosh substitution. The prebaked cap is shorter
        # than the source (I-frames stripped), so we don't read it 1:1;
        # instead `dm_stretch` enables per-frame stretch-replay below.
        if ctx.passthrough_dm_cap is not None:
            cap = ctx.passthrough_dm_cap
            dm_stretch = True
            dm_n_prebake = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
            # `dm_step` is how much we advance through the prebake per
            # output frame (a fractional float kept in `dm_cursor`).
            # E.g. if 12 % of source frames were I-frames, the prebake
            # has ~88 % as many frames, dm_step ≈ 0.88, and on average
            # the same prebake frame is shown ~1.14× before the cursor
            # rolls into the next one — visible as freeze-and-smear.
            dm_step = dm_n_prebake / max(1, ctx.target_total_frames)
            dm_cursor = 0.0
            dm_loaded_idx = -1
            dm_held_bgr = None
        else:
            cap, src_fps, src_total, _src_dur = pool.primary_cap()
            dm_stretch = False
            dm_n_prebake = 0
            dm_step = 0.0
            dm_cursor = 0.0
            dm_loaded_idx = -1
            dm_held_bgr = None
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        # Synthetic SILENCE used both as a leading "before any segment" filler
        # and as a fallback when there is no audio at all. If the first real
        # segment starts at t > 0 (audio begins with silence the analyzer
        # skipped), prepend this gap-filler to avoid retroactively painting
        # frames before t_start with the first segment's type.
        idle_seg = Segment(
            t_start=0.0, t_end=ctx.target_duration,
            duration=ctx.target_duration, type=SegmentType.SILENCE,
            intensity=0.0, rms=0.0, flatness=0.0, rms_change=0.0,
        )

        # Linear cursor over `seg_starts`: frames advance monotonically so
        # binary search isn't worth it.
        if ctx.segments and ctx.segments[0].t_start > 0:
            seg_list: List[Segment] = [Segment(
                t_start=0.0, t_end=ctx.segments[0].t_start,
                duration=ctx.segments[0].t_start, type=SegmentType.SILENCE,
                intensity=0.0, rms=0.0, flatness=0.0, rms_change=0.0,
            )] + list(ctx.segments)
        else:
            seg_list = list(ctx.segments)
        seg_starts = [s.t_start for s in seg_list]
        n_segs = len(seg_list)
        cursor = 0

        # Replace-mode state for Stutter and Flash. Both effects "fire" by
        # latching one of these counters to N>0 — for the next N frames the
        # loop overrides the per-frame output while STILL advancing the
        # source pointer (cap.grab in 1:1 mode, dm_cursor in dm_stretch).
        # This way output stays 1:1 with input frames and audio sync is
        # preserved. Triggers fire at most once per segment so the
        # replace phase spans the segment boundary cleanly instead of
        # re-firing each frame inside the same IMPACT.
        #
        # Stutter is a DRILL loop: it doesn't freeze a single frame, it
        # replays the last `loop_size` decoded frames in a tight cycle.
        # That's what gives drillcore its characteristic "br-r-r-r-rt"
        # micro-stutter rather than a flat freeze. `frame_history` is
        # the rolling buffer the loop is built from on trigger; maxlen
        # caps the worst-case drill length we can produce.
        STUTTER_HISTORY = 4
        frame_history: deque = deque(maxlen=STUTTER_HISTORY)
        stutter_remaining = 0
        stutter_loop: Optional[List[bytes]] = None
        stutter_idx = 0
        flash_remaining = 0
        flash_frame_bytes: Optional[bytes] = None
        last_trigger_cursor = -1
        # Trigger RNG mirrors the planner's. When seed is None (no audio
        # / pre-pass skipped) we fall back to the global `random` module
        # — drill still plays, but audio won't have been pre-looped.
        if ctx.event_rng_seed is not None:
            event_rng = random.Random(ctx.event_rng_seed)
        else:
            event_rng = random.Random()

        last_frame_bytes: Optional[bytes] = None
        for fi in range(ctx.target_total_frames):
            if self.abort:
                break
            t = fi / ctx.fps
            if n_segs > 0:
                while cursor + 1 < n_segs and seg_starts[cursor + 1] <= t:
                    cursor += 1
                seg = seg_list[cursor]
            else:
                seg = idle_seg

            # In replace-mode we override the OUTPUT frame for this
            # iteration but still keep our source position moving — in
            # 1:1 mode that's `cap.grab()` (cheap advance, no decode);
            # in dm_stretch mode it's `dm_cursor += dm_step` (the same
            # logical advance, but tracked in floating-point prebake
            # space rather than integer source space).
            in_replace = (stutter_remaining > 0 or flash_remaining > 0)
            if in_replace:
                if dm_stretch:
                    dm_cursor += dm_step
                else:
                    if not cap.grab():
                        break
                if flash_remaining > 0:
                    fb = flash_frame_bytes or last_frame_bytes
                    flash_remaining -= 1
                elif stutter_loop:
                    # Drill-replay: cycle through the captured loop. idx
                    # wraps via modulo so any combination of loop_size +
                    # remaining count is safe.
                    fb = stutter_loop[stutter_idx % len(stutter_loop)]
                    stutter_idx += 1
                    stutter_remaining -= 1
                    if stutter_remaining <= 0:
                        stutter_loop = None
                        stutter_idx = 0
                else:
                    fb = last_frame_bytes
                    stutter_remaining -= 1
                if fb is None:
                    # Defensive: very first frame somehow entered replace —
                    # fall back to a real decode this iteration.
                    in_replace = False
                else:
                    if not sink.write(fb):
                        break
                    ctx.frames_emitted += 1
                    last_frame_bytes = fb
                    frame_history.append(fb)
                    self._emit_progress(ctx.frames_emitted, ctx.target_total_frames)
                    continue

            if dm_stretch:
                # Stretch-replay: advance the prebake cap until its
                # currently-loaded frame index covers `dm_cursor`. Each
                # iteration steps `dm_cursor` by `dm_step`; the ceiling
                # of `dm_cursor` tells us which prebake frame index we
                # need loaded right now.
                desired_idx = int(dm_cursor)
                while dm_loaded_idx < desired_idx:
                    ret_dm, dm_bgr = cap.read()
                    if not ret_dm:
                        # Prebake exhausted — fall back to the last
                        # successfully held frame for the rest of the
                        # render so audio still gets paired with video.
                        break
                    dm_held_bgr = dm_bgr
                    dm_loaded_idx += 1
                dm_cursor += dm_step
                if dm_held_bgr is None:
                    if fi == 0:
                        self.log('ERROR: passthrough failed on first frame — '
                                 'datamosh prebake unreadable.')
                        self.abort = True
                    break
                frame_bgr = dm_held_bgr
            else:
                ret, frame_bgr = cap.read()
                if not ret:
                    if fi == 0:
                        # First read failed — sink would hang on a
                        # zero-frame video stream + non-zero audio. Bail.
                        self.log('ERROR: passthrough failed on first frame — '
                                 'source video unreadable.')
                        self.abort = True
                    # Source ran out earlier than target_total_frames —
                    # pad with the last good frame (if any) so audio
                    # doesn't get truncated by ffmpeg.
                    break

            frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            if frame.shape[0] != ctx.out_h or frame.shape[1] != ctx.out_w:
                frame = cv2.resize(frame, (ctx.out_w, ctx.out_h))

            if seg.type == SegmentType.SILENCE and seg.duration > 1.0:
                frame = self._apply_silence(frame)

            frame = self._apply_chain(frame, seg, ctx)

            fb = self._pack_frame(frame, sink.input_pix_fmt)

            # Per-segment trigger arming. cursor advances when we cross a
            # segment boundary; we use it as a one-shot key so a long
            # SUSTAIN/IMPACT doesn't re-fire stutter every frame. The
            # planner's pre-pass walks the same seg_list with an
            # identical RNG, so the events armed here match what the
            # WAV was pre-looped for — audio drills hit the same chunks
            # as video drills.
            #
            # `last_trigger_cursor = cursor` runs UNCONDITIONALLY on
            # cursor change. If counters are still > 0 we deliberately
            # swallow this segment's trigger — same as the planner does
            # in its `if state_remaining > 0: continue` branch. Updating
            # only on successful arm would let the trigger fire LATER
            # in the same segment (after counters drain), and the
            # planner wouldn't know to mirror that in audio.
            if cursor != last_trigger_cursor:
                last_trigger_cursor = cursor
                decision = (_trigger_decision(seg, rc, event_rng,
                                              ctx.flash_chance, ctx.chaos)
                            if (stutter_remaining == 0 and flash_remaining == 0)
                            else None)
                if decision is not None:
                    kind, params = decision
                    if kind == 'flash':
                        dummy = np.zeros((ctx.out_h, ctx.out_w, 3),
                                         dtype=np.uint8)
                        try:
                            ff = ctx.flash_fx._apply(dummy, seg, ctx.is_draft)
                            ff = cv2.resize(ff, (ctx.out_w, ctx.out_h))
                        except (cv2.error, ValueError, MemoryError) as e:
                            self._log_fx_fail(ctx.flash_fx, e)
                            ff = dummy
                        flash_frame_bytes = self._pack_frame(
                            ff, sink.input_pix_fmt)
                        flash_remaining = params['n_flash']
                    elif kind == 'stutter':
                        # Fixed loop size = STUTTER_LOOP_SIZE (planner
                        # uses the same constant). Loop content is the
                        # last (loop_size - 1) emitted frames + current
                        # `fb` so motion actually plays inside the drill
                        # cycle — that's what makes it audibly/visibly
                        # a stutter rather than a freeze.
                        cycles = params['cycles']
                        loop_size = STUTTER_LOOP_SIZE
                        history_list = list(frame_history)
                        n_old = loop_size - 1
                        if len(history_list) >= n_old:
                            loop = (history_list[-n_old:] + [fb]
                                    if n_old > 0 else [fb])
                        else:
                            pad = [fb] * (n_old - len(history_list))
                            loop = pad + history_list + [fb]
                        stutter_loop = loop
                        stutter_idx = 0
                        stutter_remaining = loop_size * (cycles - 1)

            if not sink.write(fb):
                break
            ctx.frames_emitted += 1
            last_frame_bytes = fb
            frame_history.append(fb)
            self._emit_progress(ctx.frames_emitted, ctx.target_total_frames)

        # Tail-pad if source video was shorter than target_total_frames.
        if not self.abort and last_frame_bytes is not None:
            pad_count = ctx.target_total_frames - ctx.frames_emitted
            if pad_count > 0:
                self.log(f'Padding tail: {pad_count} frame(s) (source ran out).')
                for _ in range(pad_count):
                    if not sink.write(last_frame_bytes):
                        break
                    ctx.frames_emitted += 1
        return ctx.frames_emitted


