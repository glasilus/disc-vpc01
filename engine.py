"""
engine.py — Breakcore Engine
Pipeline: cv2.VideoCapture → EffectChain → ffmpeg subprocess pipe → output file.
Replaces MoviePy. All frame processing is numpy/cv2.
"""

import os
import random
import subprocess
import numpy as np
import cv2
from typing import Dict, Any, Optional, List, Callable


def _ffmpeg_bin() -> str:
    """Return path to ffmpeg binary — bundled via imageio-ffmpeg when available."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'

from analyzer import AudioAnalyzer, Segment, SegmentType
from effects import (
    PixelSortEffect, DatamoshEffect, ASCIIEffect, FlashEffect,
    GhostTrailsEffect, RGBShiftEffect, BlockGlitchEffect, PixelDriftEffect,
    ScanLinesEffect, BitcrushEffect, ColorBleedEffect, FreezeCorruptEffect,
    NegativeEffect, JPEGCrushEffect, FisheyeEffect, VHSTrackingEffect,
    InterlaceEffect, BadSignalEffect, DitheringEffect, ZoomGlitchEffect,
    FeedbackLoopEffect, PhaseShiftEffect, MosaicPulseEffect, EchoCompoundEffect,
    KaliMirrorEffect, GlitchCascadeEffect, OverlayEffect, ChromaKeyEffect,
    MysterySection,
)

from scenedetect import VideoManager, SceneManager
from scenedetect.detectors import ContentDetector

RENDER_DRAFT = 'draft'
RENDER_PREVIEW = 'preview'
RENDER_FINAL = 'final'


class VideoPool:
    """Manages multiple VideoCapture objects; selects randomly per segment."""

    def __init__(self, paths: List[str]):
        if not paths:
            raise ValueError("VideoPool requires at least one path")
        self.paths = paths
        self.caps: List[cv2.VideoCapture] = []
        self.fps_list: List[float] = []
        self.total_frames_list: List[int] = []
        self.durations: List[float] = []

        for path in paths:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open video: {path}")
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.caps.append(cap)
            self.fps_list.append(fps)
            self.total_frames_list.append(total)
            self.durations.append(total / fps)

        # Expose primary-source properties for backward compat
        self.vid_fps = self.fps_list[0]
        self.vid_total_frames = self.total_frames_list[0]
        self.vid_duration = max(self.durations)

    def random_cap(self):
        """Return (cap, fps, total_frames, duration) for a randomly selected source."""
        i = random.randrange(len(self.caps))
        return self.caps[i], self.fps_list[i], self.total_frames_list[i], self.durations[i]

    def primary_cap(self):
        """Return the first source's cap (used for datamosh)."""
        return self.caps[0], self.fps_list[0], self.total_frames_list[0], self.durations[0]

    def release_all(self):
        for cap in self.caps:
            try:
                cap.release()
            except Exception:
                pass


class BreakcoreEngine:
    def __init__(self, config: Dict[str, Any], progress_callback: Optional[Callable] = None):
        self.cfg = config
        self.progress_callback = progress_callback
        self.abort = False
        self.scene_cuts: List[float] = []

    def log(self, message: str, value: Optional[int] = None):
        print(f"[ENGINE] {message}")
        if self.progress_callback:
            self.progress_callback(message, value)

    def detect_scenes(self, video_paths: List[str], duration: float):
        if not self.cfg.get('use_scene_detect', False):
            return
        self.log("Detecting scenes...")
        all_scene_cuts: List[float] = []
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
                all_scene_cuts.extend(cuts)
            except Exception as e:
                self.log(f"Scene detection warning ({video_path}): {e}")
            finally:
                vm.release()
        all_scene_cuts = sorted(set(all_scene_cuts))
        buf = int(self.cfg.get('scene_buffer_size', 10))
        self.scene_cuts = all_scene_cuts[:buf] if buf < len(all_scene_cuts) else all_scene_cuts
        self.log(f"Found {len(all_scene_cuts)} scene cuts across {len(video_paths)} source(s), using {len(self.scene_cuts)}.")

    def _get_source_time(self, video_duration: float, seg_duration: float) -> float:
        chaos = self.cfg.get('chaos_level', 0.5)
        if self.scene_cuts and random.random() > chaos * 0.8:
            t = random.choice(self.scene_cuts) + random.uniform(0, 1.0)
        else:
            t = random.uniform(0, max(0, video_duration - seg_duration))
        return max(0.0, min(t, video_duration - seg_duration - 0.1))

    def _build_effects(self) -> List:
        c = self.cfg
        chaos = float(c.get('chaos_level', 0.5))

        # Scale every effect's chance by chaos so the slider actually controls
        # how aggressive the pipeline feels — matching the original engine where
        # each effect used  random.random() < (base + chaos * boost).
        # Formula: chaos=0 → 30 % of base chance; chaos=1 → 100 % of base chance.
        def _ch(base: float) -> float:
            return min(1.0, base * (0.3 + 0.7 * chaos))

        chain = []
        if c.get('fx_rgb'):          chain.append(RGBShiftEffect(enabled=True, chance=_ch(c.get('fx_rgb_chance', 0.7))))
        if c.get('fx_psort'):        chain.append(PixelSortEffect(enabled=True, chance=_ch(c.get('fx_psort_chance', 0.5)), sort_axis=c.get('fx_psort_axis', 'luminance'), intensity_min=0.0, intensity_max=c.get('fx_psort_int', 0.5)))
        if c.get('fx_block_glitch'): chain.append(BlockGlitchEffect(enabled=True, chance=_ch(c.get('fx_block_glitch_chance', 0.5))))
        if c.get('fx_pixel_drift'):  chain.append(PixelDriftEffect(enabled=True, chance=_ch(c.get('fx_pixel_drift_chance', 0.5))))
        if c.get('fx_scanlines'):    chain.append(ScanLinesEffect(enabled=True, chance=_ch(c.get('fx_scanlines_chance', 0.8))))
        if c.get('fx_bitcrush'):     chain.append(BitcrushEffect(enabled=True, chance=_ch(c.get('fx_bitcrush_chance', 0.5))))
        if c.get('fx_colorbleed'):   chain.append(ColorBleedEffect(enabled=True, chance=_ch(c.get('fx_colorbleed_chance', 0.5))))
        if c.get('fx_freeze_corrupt'): chain.append(FreezeCorruptEffect(enabled=True, chance=_ch(c.get('fx_freeze_corrupt_chance', 0.3))))
        if c.get('fx_negative'):     chain.append(NegativeEffect(enabled=True, chance=_ch(c.get('fx_negative_chance', 0.2))))
        if c.get('fx_jpeg_crush'):   chain.append(JPEGCrushEffect(enabled=True, chance=_ch(c.get('fx_jpeg_crush_chance', 0.5))))
        if c.get('fx_fisheye'):      chain.append(FisheyeEffect(enabled=True, chance=_ch(c.get('fx_fisheye_chance', 0.3))))
        if c.get('fx_vhs'):          chain.append(VHSTrackingEffect(enabled=True, chance=_ch(c.get('fx_vhs_chance', 0.5))))
        if c.get('fx_interlace'):    chain.append(InterlaceEffect(enabled=True, chance=_ch(c.get('fx_interlace_chance', 0.4))))
        if c.get('fx_bad_signal'):   chain.append(BadSignalEffect(enabled=True, chance=_ch(c.get('fx_bad_signal_chance', 0.3))))
        if c.get('fx_dither'):       chain.append(DitheringEffect(enabled=True, chance=_ch(c.get('fx_dither_chance', 0.4))))
        if c.get('fx_zoom_glitch'):  chain.append(ZoomGlitchEffect(enabled=True, chance=_ch(c.get('fx_zoom_glitch_chance', 0.5))))
        if c.get('fx_feedback'):     chain.append(FeedbackLoopEffect(enabled=True, chance=1.0))
        if c.get('fx_phase_shift'):  chain.append(PhaseShiftEffect(enabled=True, chance=_ch(c.get('fx_phase_shift_chance', 0.4))))
        if c.get('fx_mosaic'):       chain.append(MosaicPulseEffect(enabled=True, chance=_ch(c.get('fx_mosaic_chance', 0.5))))
        if c.get('fx_echo'):         chain.append(EchoCompoundEffect(enabled=True, chance=_ch(c.get('fx_echo_chance', 0.4))))
        if c.get('fx_kali'):         chain.append(KaliMirrorEffect(enabled=True, chance=_ch(c.get('fx_kali_chance', 0.3))))
        if c.get('fx_cascade'):      chain.append(GlitchCascadeEffect(enabled=True, chance=_ch(c.get('fx_cascade_chance', 0.4))))
        if c.get('fx_ghost'):        chain.append(GhostTrailsEffect(enabled=True, chance=1.0, intensity_max=c.get('fx_ghost_int', 0.5)))
        if c.get('fx_overlay') and c.get('overlay_dir'):
            overlay_frames = self._load_overlay_frames(c['overlay_dir'])
            if overlay_frames:
                chain.append(OverlayEffect(
                    enabled=True,
                    chance=_ch(c.get('fx_overlay_chance', 0.2)),
                    overlay_frames=overlay_frames,
                    opacity=c.get('fx_overlay_opacity', 0.85),
                    blend_mode=c.get('fx_overlay_blend', 'screen'),
                    scale=c.get('fx_overlay_scale', 0.4),
                    scale_min=c.get('fx_overlay_scale_min', 0.15),
                    position=c.get('fx_overlay_position', 'random'),
                ))
            else:
                self.log(f"WARNING: No images found in overlay folder: {c['overlay_dir']}")
        if c.get('fx_datamosh'): chain.append(DatamoshEffect(enabled=True, chance=_ch(c.get('fx_datamosh_chance', 0.5))))
        if c.get('fx_ascii'):    chain.append(ASCIIEffect(enabled=True, chance=_ch(c.get('fx_ascii_chance', 0.5)), char_size=c.get('fx_ascii_size', 10), fg_color=tuple(c.get('fx_ascii_fg', [0, 255, 0])), bg_color=tuple(c.get('fx_ascii_bg', [0, 0, 0])), blend=c.get('fx_ascii_blend', 0.0), color_mode=c.get('fx_ascii_color_mode', 'fixed')))
        return chain

    def _load_overlay_frames(self, folder: str) -> List[np.ndarray]:
        """Load all PNG/JPG images from folder as RGB numpy arrays."""
        from PIL import Image as PILImage
        exts = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
        frames = []
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            return frames
        for name in entries:
            if os.path.splitext(name)[1].lower() in exts:
                path = os.path.join(folder, name)
                try:
                    img = PILImage.open(path).convert('RGB')
                    frames.append(np.array(img))
                except Exception:
                    pass
        return frames

    def _prepare_datamosh_source(self, video_path: str, output_path: str) -> bool:
        """
        Create a P-frame-only version of the source video for datamosh.
        Returns True if successful.
        ffmpeg removes keyframes to make motion vectors bleed across scene cuts.
        """
        cmd = [
            _ffmpeg_bin(), '-y', '-i', video_path,
            '-vf', "select=not(eq(pict_type\,I))",
            '-vsync', 'vfr',
            '-vcodec', 'libx264',
            '-x264opts', 'keyint=1000:no-scenecut',
            '-preset', 'ultrafast',
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            self.log(f"Datamosh ffmpeg error: {result.stderr[:200].decode(errors='replace')}")
        return result.returncode == 0

    def run(self, render_mode: str = RENDER_FINAL, max_output_duration: Optional[float] = None):
        raw_paths = self.cfg.get('video_paths') or self.cfg.get('video_path')
        video_paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
        audio_path = self.cfg['audio_path']
        output_path = self.cfg['output_path']

        is_draft = render_mode == RENDER_DRAFT
        is_final = render_mode == RENDER_FINAL

        if is_draft:
            out_w, out_h = 480, 270
            fps = 24
            preset = 'ultrafast'
            crf = 28
        else:
            res_map = {'240p': (426,240), '360p': (640,360), '480p': (854,480),
                       '720p': (1280,720), '1080p': (1920,1080)}
            res_key = self.cfg.get('resolution', '720p')
            out_w, out_h = res_map.get(res_key, (1280, 720))
            fps = int(self.cfg.get('fps', 24))
            preset = self.cfg.get('export_preset', 'medium')
            crf = int(self.cfg.get('crf', 18))

        self.log(f"Mode: {render_mode} | {out_w}x{out_h} @ {fps}fps | preset={preset} crf={crf}")

        self.log("Analyzing audio...")
        analyzer = AudioAnalyzer(
            audio_path,
            min_segment_dur=self.cfg.get('min_cut_duration', 0.05),
            loud_thresh=float(self.cfg.get('threshold', 1.2)),
            transient_thresh=float(self.cfg.get('transient_thresh', 0.5)),
            snap_to_beat=bool(self.cfg.get('snap_to_beat', False)),
            snap_tolerance=float(self.cfg.get('snap_tolerance', 0.05)),
        )
        segments, audio_duration = analyzer.analyze()

        if audio_duration == 0.0 or not segments:
            self.log("Warning: audio unreadable or produced no segments — output will have no effects.")

        target_duration = audio_duration
        if max_output_duration:
            target_duration = min(audio_duration, max_output_duration)
            segments = [s for s in segments if s.t_start < target_duration]

        bpm_str = f" | {analyzer.detected_bpm:.1f} BPM" if analyzer.detected_bpm else ""
        self.log(f"Audio: {audio_duration:.1f}s | Segments: {len(segments)}{bpm_str}")

        pool = VideoPool(video_paths)
        vid_duration = pool.vid_duration
        self.detect_scenes(video_paths, vid_duration)

        effects = self._build_effects()
        mystery = MysterySection()
        mystery_cfg = self.cfg.get('mystery', {})
        for k, v in mystery_cfg.items():
            if hasattr(mystery, k):
                try:
                    setattr(mystery, k, float(v))
                except (TypeError, ValueError):
                    pass

        chaos = float(self.cfg.get('chaos_level', 0.5))

        flash_enabled = self.cfg.get('fx_flash', False)
        # Flash and stutter chances scale with chaos (original engine behaviour)
        flash_chance  = min(1.0, self.cfg.get('fx_flash_chance', 0.5) * (0.3 + 0.7 * chaos))
        flash_fx = FlashEffect(enabled=True, chance=1.0)

        stutter_enabled = self.cfg.get('fx_stutter', False)

        # H.265 support — fmt_combo in GUI now wired up
        use_h265 = 'H.265' in self.cfg.get('video_codec', 'H.264')
        vcodec = 'libx265' if use_h265 else 'libx264'
        # x265 needs a different movflags tag; also add -tag:v hvc1 for Apple compat
        extra_flags = ['-tag:v', 'hvc1'] if use_h265 else []

        ffmpeg_cmd = [
            _ffmpeg_bin(), '-y',
            '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-s', f'{out_w}x{out_h}',
            '-pix_fmt', 'rgb24',
            '-r', str(fps),
            '-i', 'pipe:0',
            '-i', audio_path,
            '-vcodec', vcodec,
            '-pix_fmt', 'yuv420p',
            '-preset', preset,
            '-crf', str(crf),
            *extra_flags,
            '-acodec', 'aac',
            '-t', str(target_duration),
            '-shortest',
            '-movflags', '+faststart',
            output_path
        ]

        self.log("Starting ffmpeg pipe...")
        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE,
                                stderr=subprocess.PIPE)

        # Drain ffmpeg stderr in background so its pipe buffer never fills up
        import threading as _threading
        def _drain(pipe):
            try:
                pipe.read()
            except Exception:
                pass
        _threading.Thread(target=_drain, args=(proc.stderr,), daemon=True).start()

        vid_fps = pool.vid_fps
        vid_total_frames = pool.vid_total_frames

        # --- Real datamosh: pre-process source to P-frames only (final mode) ---
        datamosh_source_path = None
        datamosh_cap = None
        datamosh_total_frames = vid_total_frames
        if is_final and self.cfg.get('fx_datamosh'):
            dm_path = output_path + '_dmosh_src.mp4'
            self.log("Preparing datamosh source (I-frame drop)...")
            if self._prepare_datamosh_source(video_paths[0], dm_path):
                datamosh_source_path = dm_path
                datamosh_cap = cv2.VideoCapture(dm_path)
                datamosh_total_frames = int(datamosh_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                self.log("Datamosh source ready.")
            else:
                self.log("Datamosh pre-processing failed, falling back to optical flow.")

        try:
            for seg_idx, seg in enumerate(segments):
                if self.abort:
                    break

                seg_dur = min(seg.duration, target_duration - seg.t_start)
                if seg_dur <= 0:
                    break

                n_frames = max(1, int(seg_dur * fps))

                # Pick a random source video for this segment
                seg_cap, seg_fps, seg_total_frames, seg_duration = pool.random_cap()

                use_datamosh_src = (
                    is_final and
                    datamosh_cap is not None and
                    seg.type == SegmentType.NOISE and
                    self.cfg.get('fx_datamosh') and
                    random.random() < self.cfg.get('fx_datamosh_chance', 0.5)
                )
                active_cap = datamosh_cap if use_datamosh_src else seg_cap
                active_total_frames = datamosh_total_frames if use_datamosh_src else seg_total_frames

                src_t = self._get_source_time(seg_duration, seg_dur)
                src_frame_idx = int(src_t * seg_fps)
                active_cap.set(cv2.CAP_PROP_POS_FRAMES, min(src_frame_idx, active_total_frames - 1))

                stutter_repeat = 1
                if stutter_enabled and seg.type == SegmentType.IMPACT and seg.duration < 0.3:
                    if random.random() < (0.3 + self.cfg.get('chaos_level', 0.5) * 0.5):
                        stutter_repeat = random.choice([2, 4, 8])

                if flash_enabled and seg.type in (SegmentType.DROP, SegmentType.IMPACT):
                    if random.random() < flash_chance:
                        flash_frames = random.randint(1, 2)
                        dummy_frame = np.zeros((out_h, out_w, 3), dtype=np.uint8)
                        flash_frame = flash_fx._apply(dummy_frame, seg, is_draft)
                        flash_frame = cv2.resize(flash_frame, (out_w, out_h))
                        for _ in range(flash_frames):
                            proc.stdin.write(flash_frame.tobytes())

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

                    # Silence treatment for long silent segments
                    if seg.type == SegmentType.SILENCE and seg.duration > 1.0:
                        _smode = self.cfg.get('silence_mode', 'dim')
                        if _smode == 'dim':
                            frame = (frame.astype(np.float32) * 0.6).clip(0, 255).astype(np.uint8)
                        elif _smode == 'blur':
                            frame = cv2.GaussianBlur(frame, (15, 15), 0)
                        elif _smode == 'both':
                            frame = cv2.GaussianBlur(frame, (11, 11), 0)
                            frame = (frame.astype(np.float32) * 0.7).clip(0, 255).astype(np.uint8)
                        # 'none': no treatment

                    for fx in effects:
                        try:
                            frame = fx.apply(frame, seg, is_draft)
                        except Exception as _fx_err:
                            self.log(f"Effect error ({type(fx).__name__}): {_fx_err}")
                    try:
                        frame = mystery.apply(frame, seg, is_draft)
                    except Exception as _m_err:
                        self.log(f"Mystery error: {_m_err}")

                    frame_bytes = frame.tobytes()
                    for _ in range(stutter_repeat):
                        proc.stdin.write(frame_bytes)
                        frames_written += 1
                        if frames_written >= n_frames:
                            break

                if self.progress_callback:
                    pct = int((seg_idx / len(segments)) * 100)
                    self.progress_callback(f"Rendering... {pct}%", pct)

        except (BrokenPipeError, OSError):
            self.log("ffmpeg pipe closed early.")
        finally:
            pool.release_all()
            if datamosh_cap:
                datamosh_cap.release()
            if datamosh_source_path and os.path.exists(datamosh_source_path):
                os.remove(datamosh_source_path)
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()

        if not self.abort:
            self.log(f"Done. Output: {output_path}")

