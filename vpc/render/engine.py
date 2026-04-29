"""BreakcoreEngine — orchestrates analysis → effect chain → ffmpeg sink.

Slim re-implementation: parsing, analysis, scene detection, effect-chain
construction, datamosh prep and per-segment rendering each live in their own
small method (or in a sibling module). The chain is built from the registry,
not from a hand-written if-ladder.
"""
from __future__ import annotations

import os
import random
import subprocess
import time
from typing import Callable, List, Optional

import cv2
import numpy as np

from vpc.analyzer import AudioAnalyzer, Segment, SegmentType
from .config import RenderConfig, RENDER_DRAFT, RENDER_FINAL
from .source import VideoPool
from .sink import FFmpegSink, EXPORT_FORMATS, ffmpeg_bin
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

    # ----- logging -----
    def log(self, message: str, value: Optional[int] = None):
        print(f'[ENGINE] {message}')
        if self.progress_callback:
            self.progress_callback(message, value)

    # ----- scene detection -----
    def detect_scenes(self, video_paths: List[str], duration: float):
        if not self.config.use_scene_detect:
            return
        from scenedetect import VideoManager, SceneManager
        from scenedetect.detectors import ContentDetector
        self.log('Detecting scenes...')
        all_cuts: List[float] = []
        for video_path in video_paths:
            vm = VideoManager([video_path])
            sm = SceneManager()
            sm.add_detector(ContentDetector(threshold=30.0))
            try:
                vm.set_downscale_factor()
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

    # ----- datamosh helper -----
    def _prepare_datamosh_source(self, video_path: str, output_path: str) -> bool:
        cmd = [
            ffmpeg_bin(), '-y', '-i', video_path,
            '-vf', "select=not(eq(pict_type\\,I))",
            '-vsync', 'vfr',
            '-vcodec', 'libx264',
            '-x264opts', 'keyint=1000:no-scenecut',
            '-preset', 'ultrafast',
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            self.log(f'Datamosh ffmpeg error: '
                     f'{result.stderr[:200].decode(errors="replace")}')
        return result.returncode == 0

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

        if not os.path.exists(audio_path):
            self.log(f'ERROR: audio file not found: {audio_path}')
            return False

        is_draft = render_mode == RENDER_DRAFT
        is_final = render_mode == RENDER_FINAL

        # Audio analysis runs first so we can use its duration for sizing.
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
        if audio_duration == 0.0 or not segments:
            self.log('Warning: audio unreadable / no segments — output will have no effects.')

        target_duration = audio_duration
        if max_output_duration:
            target_duration = min(audio_duration, max_output_duration)
            segments = [s for s in segments if s.t_start < target_duration]

        bpm_str = f' | {analyzer.detected_bpm:.1f} BPM' if analyzer.detected_bpm else ''
        self.log(f'Audio: {audio_duration:.1f}s | Segments: {len(segments)}{bpm_str}')

        # Open video pool, then derive output size (backlog #2 needs source).
        pool = VideoPool(video_paths)
        out_w, out_h = rc.output_size(render_mode, source_size=pool.primary_size)
        fps = rc.fps(render_mode)
        preset = rc.encoder_preset(render_mode)
        crf = rc.crf(render_mode)
        self.log(f'Mode: {render_mode} | {out_w}x{out_h} @ {fps}fps | '
                 f'preset={preset} crf={crf}')

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

        chaos = rc.chaos
        flash_chance = min(1.0, rc.flash_chance_base * (0.3 + 0.7 * chaos))
        flash_fx = FlashEffect(enabled=True, chance=1.0)

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
        datamosh_source_path = None
        datamosh_cap = None
        datamosh_total_frames = pool.vid_total_frames
        if is_final and rc.datamosh_enabled:
            dm_path = output_path + '_dmosh_src.mp4'
            # Stale leftover from a previously aborted render — its missing
            # moov atom is what produces the "moov atom not found" warning
            # next time around. Drop it before regenerating.
            if os.path.exists(dm_path):
                try: os.remove(dm_path)
                except OSError: pass
            self.log('Preparing datamosh source (I-frame drop)...')
            if self._prepare_datamosh_source(video_paths[0], dm_path):
                datamosh_source_path = dm_path
                datamosh_cap = cv2.VideoCapture(dm_path)
                datamosh_total_frames = int(
                    datamosh_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                self.log('Datamosh source ready.')
            else:
                self.log('Datamosh pre-processing failed, falling back to optical flow.')

        # ----- main loop -----
        # Total target frames for the whole render. We track frames_emitted
        # against this counter — any rounding shortfall is paid back by
        # padding with the last produced frame after the segment loop ends,
        # so the encoded video matches audio length exactly. This is the
        # fix for the "video ends before song" truncation bug.
        target_total_frames = int(round(target_duration * fps))
        frames_emitted = 0
        last_frame_bytes: Optional[bytes] = None

        # ETA bookkeeping. _render_t0 starts here (after analysis +
        # scene-detect + datamosh prebake) so the printed ETA reflects
        # the actual encode loop, not setup work. _last_progress_t
        # throttles callbacks to ~2 Hz.
        self._render_t0 = time.perf_counter()
        self._last_progress_t = 0.0

        try:
            for seg_idx, seg in enumerate(segments):
                if self.abort:
                    break
                seg_dur = min(seg.duration, target_duration - seg.t_start)
                if seg_dur <= 0:
                    break
                # Per-segment target uses cumulative rounding: compute where
                # the segment's tail SHOULD land in absolute frame space and
                # subtract frames already emitted. This eliminates the
                # accumulated half-frame loss the old `int(seg_dur * fps)`
                # path produced over hundreds of segments.
                seg_end_frame = int(round((seg.t_start + seg_dur) * fps))
                seg_end_frame = min(seg_end_frame, target_total_frames)
                n_frames = max(1, seg_end_frame - frames_emitted)

                seg_cap, seg_fps, seg_total_frames, seg_duration = pool.random_cap()
                use_datamosh_src = (
                    is_final and datamosh_cap is not None
                    and seg.type == SegmentType.NOISE
                    and rc.datamosh_enabled
                    and random.random() < rc.datamosh_chance_base
                )
                active_cap = datamosh_cap if use_datamosh_src else seg_cap
                active_total_frames = (datamosh_total_frames
                                       if use_datamosh_src else seg_total_frames)

                src_t = self._get_source_time(seg_duration, seg_dur)
                src_frame_idx = int(src_t * seg_fps)
                active_cap.set(cv2.CAP_PROP_POS_FRAMES,
                               min(src_frame_idx, active_total_frames - 1))

                # Stutter
                stutter_repeat = 1
                if (rc.stutter_enabled and seg.type == SegmentType.IMPACT
                        and seg.duration < 0.3):
                    if random.random() < (0.3 + chaos * 0.5):
                        stutter_repeat = random.choice([2, 4, 8])

                # Flash
                if (rc.flash_enabled
                        and seg.type in (SegmentType.DROP, SegmentType.IMPACT)
                        and random.random() < flash_chance):
                    flash_frames = random.randint(1, 2)
                    dummy = np.zeros((out_h, out_w, 3), dtype=np.uint8)
                    flash_frame = flash_fx._apply(dummy, seg, is_draft)
                    flash_frame = cv2.resize(flash_frame, (out_w, out_h))
                    flash_bytes = self._pack_frame(flash_frame, sink.input_pix_fmt)
                    aborted = False
                    for _ in range(flash_frames):
                        if frames_emitted >= target_total_frames:
                            break
                        if not sink.write(flash_bytes):
                            aborted = True; break
                        frames_emitted += 1
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
                    frame = cv2.resize(frame, (out_w, out_h))

                    if seg.type == SegmentType.SILENCE and seg.duration > 1.0:
                        frame = self._apply_silence(frame)

                    for fx in effects:
                        try:
                            frame = fx.apply(frame, seg, is_draft)
                        except Exception as e:
                            self.log(f'Effect error ({type(fx).__name__}): {e}')
                    try:
                        frame = mystery.apply(frame, seg, is_draft)
                    except Exception as e:
                        self.log(f'Mystery error: {e}')

                    fb = self._pack_frame(frame, sink.input_pix_fmt)
                    for _ in range(stutter_repeat):
                        if frames_emitted >= target_total_frames:
                            break
                        if not sink.write(fb):
                            break
                        frames_written += 1
                        frames_emitted += 1
                        last_frame_bytes = fb
                        self._emit_progress(frames_emitted, target_total_frames)
                        if frames_written >= n_frames:
                            break
                if frames_emitted >= target_total_frames:
                    break

            # ----- pad to target_total_frames -----
            # Cover any residual gap (last segment dropped by min_segment_dur,
            # rounding leftovers, or a track that ends in silence with no
            # final onset). Without this, ffmpeg sees a video stream shorter
            # than audio and the audio gets truncated at output.
            if not self.abort and last_frame_bytes is not None:
                pad_count = target_total_frames - frames_emitted
                if pad_count > 0:
                    self.log(f'Padding tail: {pad_count} frame(s) to match audio.')
                    for _ in range(pad_count):
                        if not sink.write(last_frame_bytes):
                            break
                        frames_emitted += 1

        except (BrokenPipeError, OSError):
            self.log('ffmpeg pipe closed early.')
        finally:
            pool.release_all()
            if datamosh_cap:
                datamosh_cap.release()
            if datamosh_source_path and os.path.exists(datamosh_source_path):
                try: os.remove(datamosh_source_path)
                except OSError: pass
            sink.close()

        if not self.abort:
            elapsed = time.perf_counter() - self._render_t0
            rt_factor = (target_duration / elapsed) if elapsed > 0 else 0.0
            self.log(f'Done in {self._fmt_dur(elapsed)} '
                     f'({rt_factor:.2f}x realtime). Output: {output_path}')
            return True
        return False
