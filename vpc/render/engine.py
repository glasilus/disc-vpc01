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
from typing import Callable, List, Optional

import cv2
import numpy as np

from vpc.analyzer import AudioAnalyzer, Segment, SegmentType
from .config import RenderConfig, RENDER_DRAFT, RENDER_FINAL
from .source import VideoPool
from .sink import FFmpegSink, ffmpeg_bin
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
        vcodec = 'libx265' if rc.use_h265 else 'libx264'
        extra_v = ['-tag:v', 'hvc1'] if rc.use_h265 else []
        sink = FFmpegSink(
            width=out_w, height=out_h, fps=fps,
            audio_path=audio_path, output_path=output_path,
            vcodec=vcodec, preset=preset, crf=crf,
            target_duration=target_duration,
            extra_v_flags=extra_v,
        )
        self.log('Starting ffmpeg pipe...')
        sink.open()

        # ----- datamosh pre-bake -----
        datamosh_source_path = None
        datamosh_cap = None
        datamosh_total_frames = pool.vid_total_frames
        if is_final and rc.datamosh_enabled:
            dm_path = output_path + '_dmosh_src.mp4'
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
        try:
            for seg_idx, seg in enumerate(segments):
                if self.abort:
                    break
                seg_dur = min(seg.duration, target_duration - seg.t_start)
                if seg_dur <= 0:
                    break
                n_frames = max(1, int(seg_dur * fps))

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
                    flash_bytes = flash_frame.tobytes()
                    aborted = False
                    for _ in range(flash_frames):
                        if not sink.write(flash_bytes):
                            aborted = True; break
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

                    fb = frame.tobytes()
                    for _ in range(stutter_repeat):
                        if not sink.write(fb):
                            break
                        frames_written += 1
                        if frames_written >= n_frames:
                            break

                if self.progress_callback:
                    pct = int((seg_idx / max(1, len(segments))) * 100)
                    self.progress_callback(f'Rendering... {pct}%', pct)

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
            self.log(f'Done. Output: {output_path}')
            return True
        return False
