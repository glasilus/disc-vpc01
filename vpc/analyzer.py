"""Audio segment analysis: data structures, classifier, and AudioAnalyzer."""
import enum
import os
import sys
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import librosa


@contextmanager
def _suppress_stderr():
    """Redirect C-level stderr to /dev/null to silence ffmpeg/audioread noise."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)
        os.close(devnull)
        try:
            yield
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
    except Exception:
        yield  # if fd tricks fail, just continue without suppression


class SegmentType(enum.Enum):
    IMPACT = "impact"     # loud transient, short
    NOISE = "noise"       # high spectral flatness
    SUSTAIN = "sustain"   # loud, longer duration
    SILENCE = "silence"   # low RMS
    BUILD = "build"       # rising RMS trend
    DROP = "drop"         # falling RMS after high


@dataclass
class Segment:
    t_start: float
    t_end: float
    duration: float
    type: SegmentType
    intensity: float      # 0.0–1.0, normalized within segment type
    rms: float
    flatness: float
    rms_change: float


class SegmentClassifier:
    """Classifies a segment into one of 6 types based on audio metrics."""

    TREND_WINDOW = 5

    def __init__(self, rms_mean: float, flat_mean: float,
                 loud_thresh: float = 1.2,
                 transient_thresh: float = 0.5,
                 transient_rms_mean: float = None):
        self.rms_mean = rms_mean
        self.flat_mean = flat_mean
        # loud_thresh: how many × rms_mean a segment must be to count as "loud"
        # mirrors the old cfg.get('threshold', 1.2) the original engine exposed in GUI
        self.loud_thresh = loud_thresh
        self.silence_thresh = 0.5     # fraction of rms_mean below which = silence
        self.noise_thresh = 1.5       # flatness multiplier
        self.impact_max_dur = 0.3     # seconds; short + loud = IMPACT
        # transient_thresh: rms_change / rms_mean above which = sharp attack → IMPACT
        # mirrors old  is_transient = rms_change > rms_mean * 0.5  used for flash
        self.transient_thresh = transient_thresh
        self.transient_rms_mean = transient_rms_mean if transient_rms_mean is not None else rms_mean

    def classify(self, t_start: float, t_end: float, rms: float,
                 flatness: float, rms_change: float,
                 rms_history: list[float]) -> 'Segment':
        duration = t_end - t_start
        is_loud      = rms > self.rms_mean * self.loud_thresh
        is_silent    = rms < self.rms_mean * self.silence_thresh
        is_noisy     = flatness > self.flat_mean * self.noise_thresh
        is_short     = duration < self.impact_max_dur
        is_transient = rms_change > self.transient_rms_mean * self.transient_thresh

        seg_type = self._determine_type(
            is_loud, is_silent, is_noisy, is_short, is_transient, rms_change, rms_history)
        intensity = self._calc_intensity(seg_type, rms, flatness, rms_change)

        return Segment(
            t_start=t_start, t_end=t_end, duration=duration,
            type=seg_type, intensity=intensity,
            rms=rms, flatness=flatness, rms_change=rms_change,
        )

    def _determine_type(self, is_loud, is_silent, is_noisy, is_short,
                        is_transient, rms_change, rms_history):
        """Priority order (highest first):
        1. BUILD / DROP  — multi-segment trend
        2. SILENCE       — too quiet to classify further
        3. IMPACT        — sharp transient attack OR loud+short hit
        4. NOISE         — high spectral flatness
        5. SUSTAIN       — loud, longer duration
        6. SILENCE       — fallback
        """
        if len(rms_history) >= self.TREND_WINDOW:
            slope = np.polyfit(range(len(rms_history)), rms_history, 1)[0]
            trend_thresh = self.rms_mean * 0.07
            if slope > trend_thresh:
                return SegmentType.BUILD
            if slope < -trend_thresh and rms_history[-1] > self.rms_mean:
                return SegmentType.DROP

        if is_silent:
            return SegmentType.SILENCE

        # Transient: sharp upward RMS spike → treat as IMPACT regardless of duration.
        # Restored from the original engine's  is_transient = rms_change > rms_mean * 0.5
        # which was used specifically to trigger flash frames on percussive attacks.
        if is_transient:
            return SegmentType.IMPACT

        if is_noisy:
            return SegmentType.NOISE
        if is_loud and is_short:
            return SegmentType.IMPACT
        if is_loud:
            return SegmentType.SUSTAIN
        return SegmentType.SILENCE

    def _calc_intensity(self, seg_type, rms, flatness, rms_change):
        """Normalize intensity to 0.0–1.0 using the primary metric for this segment type."""
        if seg_type == SegmentType.IMPACT:
            # Take the larger of absolute loudness and spike magnitude so both
            # "hard hit" and "sharp attack from quiet" score correctly.
            raw = max(rms / (self.rms_mean * 3.0),
                      abs(rms_change) / (self.rms_mean * 2.5))
        elif seg_type == SegmentType.NOISE:
            raw = flatness / (self.flat_mean * 3.0)
        elif seg_type == SegmentType.SUSTAIN:
            raw = rms / (self.rms_mean * 2.0)
        elif seg_type == SegmentType.DROP:
            raw = abs(rms_change) / (self.rms_mean * 3.0)
        else:
            raw = rms / (self.rms_mean * 2.0)
        return float(np.clip(raw, 0.0, 1.0))


class AudioAnalyzer:
    """Loads audio via librosa, detects onsets, returns classified Segment list."""

    def __init__(self, audio_path: str, min_segment_dur: float = 0.05,
                 loud_thresh: float = 1.2, transient_thresh: float = 0.5,
                 snap_to_beat: bool = False, snap_tolerance: float = 0.05,
                 manual_bpm: float = 0.0, use_manual_bpm: bool = False):
        self.audio_path = audio_path
        self.min_segment_dur = min_segment_dur
        self.loud_thresh = loud_thresh
        self.transient_thresh = transient_thresh
        self.snap_to_beat = snap_to_beat
        self.snap_tolerance = snap_tolerance
        # Manual BPM override: when both `use_manual_bpm` is True and a
        # positive `manual_bpm` is provided, the beat grid for snap-to-beat
        # is generated from the user value instead of running librosa's
        # tempo estimator. Useful when the track has weak/ambiguous onsets
        # or the user knows the exact target tempo.
        self.manual_bpm = float(manual_bpm)
        self.use_manual_bpm = bool(use_manual_bpm)
        self.detected_bpm: float = 0.0  # filled after analyze()

    def _load_audio(self, path: str):
        """Try to load audio, falling back to ffmpeg transcoding if needed.

        For very large source files we explicitly downsample to 11025 Hz at
        load time. Default `librosa.load` (sr=22050) needs ~600 MB of float32
        STFT scratch for a 30-minute track on top of the raw waveform; on a
        machine with 8 GB free that's a frequent OOM kill of the render
        thread. Downsampling cuts both the waveform and the STFT in half
        with no measurable loss for onset/RMS/flatness detection (those are
        all sensitive to sub-Nyquist content for typical music).
        """
        import subprocess, tempfile

        # Pick a working sample rate based on file size. Anything above
        # ~150 MB on disk is almost certainly a long uncompressed WAV
        # (passthrough extracts at 44.1 kHz s16 stereo → ~10.6 MB/min) or a
        # very long compressed track — either way we want the lower sr.
        try:
            file_bytes = os.path.getsize(path)
        except OSError:
            file_bytes = 0
        target_sr = 11025 if file_bytes > 150 * 1024 * 1024 else 22050

        def _try_librosa(p, sr=target_sr):
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                with _suppress_stderr():
                    try:
                        return librosa.load(p, sr=sr, mono=True)
                    except Exception:
                        return None, None

        y, sr = _try_librosa(path)
        if y is not None and len(y) > 0:
            return y, sr

        # First attempt failed — transcode to 16-bit mono WAV via ffmpeg
        print('[ANALYZER] Direct load failed; transcoding via ffmpeg...')
        try:
            import imageio_ffmpeg as _iio_ffmpeg
            _ffmpeg = _iio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            _ffmpeg = 'ffmpeg'
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp.close()
        try:
            result = subprocess.run(
                [_ffmpeg, '-y', '-i', path,
                 '-ac', '1', '-ar', '22050', '-sample_fmt', 's16',
                 tmp.name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=60,
            )
            if result.returncode == 0:
                y, sr = _try_librosa(tmp.name)
                if y is not None and len(y) > 0:
                    print('[ANALYZER] Transcoding succeeded.')
                    return y, sr
        except Exception as exc:
            print(f'[ANALYZER] ffmpeg transcode error: {exc}')
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        return None, None

    @staticmethod
    def _snap_onsets_to_beats(onsets: list, beat_times: np.ndarray,
                               tolerance: float) -> list:
        """Pull each onset to the nearest beat within tolerance seconds.

        Duplicates that appear after snapping (two onsets land on the same
        beat) are deduplicated — only the first is kept.
        """
        beat_arr = np.asarray(beat_times, dtype=float)
        snapped = []
        for t in onsets:
            diffs = np.abs(beat_arr - t)
            idx = int(np.argmin(diffs))
            if diffs[idx] <= tolerance:
                snapped.append(float(beat_arr[idx]))
            else:
                snapped.append(float(t))

        # Deduplicate while preserving order
        seen: set = set()
        result = []
        for t in sorted(snapped):
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def analyze(self) -> Tuple[List[Segment], float]:
        """Returns (segments, audio_duration).

        If the audio file is unreadable or corrupt, attempts to transcode it
        to PCM WAV via ffmpeg first.  If that also fails, returns an empty
        segment list so the engine can still run (no effects, but no crash).
        """
        y, sr = self._load_audio(self.audio_path)

        if y is None or len(y) == 0:
            print('[ANALYZER] Warning: audio file could not be decoded. '
                  'Running without audio analysis.')
            return [], 0.0

        duration = len(y) / sr

        # Temporal Resolution Tuning:
        # Instead of jumping to a giant hop of 2048 (which degrades frame accuracy to 186ms),
        # we target a constant frame rate of ~40-50 FPS (~20-25ms step) where possible.
        # At sr=22050: hop=512 gives 23.2ms.
        # At sr=11025: hop=256 gives 23.2ms.
        # Only use hop=1024 (46.4ms/92.9ms resolution) for extremely long mixes to keep STFT size optimal.
        if duration > 1200.0:  # > 20 minutes
            hop = 1024
            n_fft = 1024
        else:
            hop = 512 if sr == 22050 else 256
            n_fft = 2048

        # --- Harmonic-Percussive Source Separation (HPSS) & Fallback ---
        print('[ANALYZER] Computing Harmonic-Percussive Source Separation (HPSS)...')
        try:
            y_harmonic, y_percussive = librosa.effects.hpss(y)
            # Estimate percussive energy ratio
            rms_raw_total = float(np.sqrt(np.mean(y**2)))
            rms_perc_total = float(np.sqrt(np.mean(y_percussive**2)))
            percussive_ratio = (rms_perc_total / rms_raw_total) if rms_raw_total > 1e-5 else 0.0
            print(f'[ANALYZER] Percussive energy ratio: {percussive_ratio:.3f}')
        except Exception as e:
            print(f'[ANALYZER] HPSS error: {e}. Falling back to raw audio.')
            y_percussive = y
            y_harmonic = y
            percussive_ratio = 0.0

        # Dynamic fallback: if the track is predominantly harmonic (ambient, acoustic, solo vocals),
        # analyze the raw mix. Otherwise, use isolated percussive components for grid/onset detection.
        use_raw_fallback = percussive_ratio < 0.15
        if use_raw_fallback:
            print('[ANALYZER] Track has low percussive energy. Using raw signal for onset & transient analysis.')
            y_onsets = y
            y_transients = y
        else:
            print('[ANALYZER] Percussive track detected. Focusing on drum transients.')
            y_onsets = y_percussive
            y_transients = y_percussive

        # Onset feature extraction with custom settings tailored for fast breakcore drum rolls
        # wait=max(1, 40ms in frames) to capture fast hits without double-trigger debounces.
        wait_frames = max(1, int(round(0.040 * sr / hop)))
        onset_env = librosa.onset.onset_strength(
            y=y_onsets, sr=sr, hop_length=hop, n_fft=n_fft)
        onsets = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, hop_length=hop,
            units='time', backtrack=True, wait=wait_frames
        )

        # RMS and Flatness arrays:
        # 1. rms_raw_frames: Overall volume of the mix (used for loudness/silence classification)
        # 2. rms_transient_frames: Target signal volume (used to calculate rms_change for transient spikes)
        rms_raw_frames = librosa.feature.rms(y=y, hop_length=hop)[0]
        if use_raw_fallback:
            rms_transient_frames = rms_raw_frames
        else:
            rms_transient_frames = librosa.feature.rms(y=y_transients, hop_length=hop)[0]

        flat_frames = librosa.feature.spectral_flatness(
            y=y, hop_length=hop, n_fft=n_fft)[0]

        onsets = list(onsets)

        # ---- Snap-to-beat -----------------------------------------------
        if self.snap_to_beat:
            if self.use_manual_bpm and self.manual_bpm > 0:
                period = 60.0 / self.manual_bpm
                beat_times = np.arange(0.0, duration + period, period,
                                       dtype=np.float64)
                self.detected_bpm = float(self.manual_bpm)
                src = 'manual'
            else:
                tempo, beat_frames = librosa.beat.beat_track(
                    y=y, sr=sr, onset_envelope=onset_env, trim=False)
                beat_times = librosa.frames_to_time(beat_frames, sr=sr)
                self.detected_bpm = float(np.atleast_1d(tempo)[0])
                src = 'detected'
            onsets = self._snap_onsets_to_beats(
                onsets, beat_times, self.snap_tolerance)
            print(f'[ANALYZER] Beat snap active ({src}) — {self.detected_bpm:.1f} BPM, '
                  f'tolerance ±{self.snap_tolerance*1000:.0f} ms')
        # -----------------------------------------------------------------

        if not onsets or onsets[-1] < duration - 0.1:
            onsets.append(duration)

        # Median calculation for raw audio features
        noise_floor = float(np.percentile(rms_raw_frames, 15))
        active_rms = rms_raw_frames[rms_raw_frames > noise_floor]
        rms_mean = float(np.median(active_rms)) if len(active_rms) > 0 \
                   else float(np.mean(rms_raw_frames))
        flat_mean = float(np.median(flat_frames))

        # Separate transient mean to ensure relative peaks are detected properly in HPSS mode
        active_transient = rms_transient_frames[rms_transient_frames > float(np.percentile(rms_transient_frames, 15))]
        transient_rms_mean = float(np.median(active_transient)) if len(active_transient) > 0 \
                             else float(np.mean(rms_transient_frames))

        classifier = SegmentClassifier(
            rms_mean=rms_mean, flat_mean=flat_mean,
            loud_thresh=self.loud_thresh,
            transient_thresh=self.transient_thresh,
            transient_rms_mean=transient_rms_mean
        )

        segments = []
        rms_history = []

        # Convert physical 100ms lookback window to frames
        lookback_sec = 0.100
        lookback_frames = max(1, int(round(lookback_sec * sr / hop)))

        for i in range(len(onsets) - 1):
            t_start = onsets[i]
            t_end = onsets[i + 1]
            dur = t_end - t_start

            if dur < self.min_segment_dur:
                continue

            frame_idx = min(
                int(librosa.time_to_frames(t_start, sr=sr, hop_length=hop)),
                len(rms_raw_frames) - 1
            )
            rms = float(rms_raw_frames[frame_idx])
            flatness = float(flat_frames[frame_idx])

            prev_idx = max(0, frame_idx - lookback_frames)
            rms_change = float(rms_transient_frames[frame_idx]) - float(rms_transient_frames[prev_idx])

            seg = classifier.classify(
                t_start=t_start, t_end=t_end,
                rms=rms, flatness=flatness, rms_change=rms_change,
                rms_history=rms_history[-SegmentClassifier.TREND_WINDOW:],
            )
            segments.append(seg)
            rms_history.append(rms)

        return segments, duration
