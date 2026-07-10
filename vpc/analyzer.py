"""Анализ аудио на сегменты: структуры данных, классификатор и AudioAnalyzer."""
import enum
import os
import sys
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import librosa


N_BINS = 24


@dataclass
class AudioFeatures:
    """Покадровые аудио-фичи, синхронизированные по времени с hop-фреймами STFT.

    Каждый массив полосы нормирован к ~0..1 относительно собственного
    максимума, чтобы визуализаторы получали стабильный динамический
    диапазон вне зависимости от общего уровня громкости.
    """
    times: np.ndarray          # (n_frames,) секунды
    bass: np.ndarray           # (n_frames,) 0..1
    mid: np.ndarray            # (n_frames,) 0..1
    high: np.ndarray           # (n_frames,) 0..1
    onset: np.ndarray          # (n_frames,) 0..1 сила онсета
    bins: np.ndarray           # (n_frames, N_BINS) 0..1, лог-полосы амплитуды
    sr: int
    hop: int
    y: Optional[np.ndarray] = None   # моно-волна целиком (для осциллографа)


@dataclass
class AudioSample:
    """Сглаженная аудио-реактивность одного кадра, готовая для рендера.

    Живет здесь, а не в слое рендера, чтобы модули эффектов могли
    зависеть от нее, не импортируя пакет render - связь effect→render
    остается однонаправленной, без цикла импортов.
    """
    bass: float
    mid: float
    high: float
    onset: float
    beat: bool
    bins: np.ndarray   # (N_BINS,) 0..1, сглажено по peak-hold
    t: float
    wave: Optional[np.ndarray] = None   # сырые сэмплы окна кадра, ~[-1,1], для осциллографа


@contextmanager
def _suppress_stderr():
    """Перенаправляет C-level stderr в /dev/null, чтобы заглушить шум ffmpeg/audioread."""
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
        yield  # если трюк с fd не сработал, просто продолжаем без заглушения


class SegmentType(enum.Enum):
    IMPACT = "impact"     # громкий короткий транзиент
    NOISE = "noise"       # высокая спектральная плоскостность
    SUSTAIN = "sustain"   # громко, длительно
    SILENCE = "silence"   # низкий RMS
    BUILD = "build"       # нарастающий тренд RMS
    DROP = "drop"         # падение RMS после пика


@dataclass
class Segment:
    t_start: float
    t_end: float
    duration: float
    type: SegmentType
    intensity: float      # 0.0-1.0, нормировано внутри своего типа сегмента
    rms: float
    flatness: float
    rms_change: float
    live: object = None   # временный покадровый AudioSample, выставляется при рендере


class SegmentClassifier:
    """Определяет тип сегмента (один из 6) по аудио-метрикам."""

    TREND_WINDOW = 5

    def __init__(self, rms_mean: float, flat_mean: float,
                 loud_thresh: float = 1.2,
                 transient_thresh: float = 0.5,
                 transient_rms_mean: float = None):
        self.rms_mean = rms_mean
        self.flat_mean = flat_mean
        # loud_thresh: во сколько раз rms_mean сегмент должен превышать,
        # чтобы считаться "громким"; значение приходит из настройки GUI
        self.loud_thresh = loud_thresh
        self.silence_thresh = 0.5     # доля от rms_mean, ниже которой = тишина
        self.noise_thresh = 1.5       # множитель flatness
        self.impact_max_dur = 0.3     # секунды; короткий + громкий = IMPACT
        # transient_thresh: порог rms_change / rms_mean, выше которого
        # резкая атака классифицируется как IMPACT (нужно для флеш-кадров
        # на перкуссионных ударах)
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
        """Порядок приоритета (от высшего к низшему):
        1. BUILD / DROP  - тренд по нескольким сегментам
        2. SILENCE       - слишком тихо, дальше не классифицируем
        3. IMPACT        - резкая транзиентная атака ИЛИ громкий короткий удар
        4. NOISE         - высокая спектральная плоскостность
        5. SUSTAIN       - громко, длительно
        6. SILENCE       - запасной вариант
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

        # Резкий скачок RMS вверх считаем IMPACT независимо от длительности
        # сегмента - иначе долгий, но начинающийся с удара сегмент терял
        # бы флеш-эффект.
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
        """Нормирует интенсивность в 0.0-1.0 по основной метрике для данного типа сегмента."""
        if seg_type == SegmentType.IMPACT:
            # Берем максимум из абсолютной громкости и величины скачка,
            # чтобы правильно оценивались и "жесткий удар", и "резкая
            # атака из тишины".
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
    """Загружает аудио через librosa, ищет онсеты, возвращает список классифицированных Segment."""

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
        # Ручной BPM: если use_manual_bpm=True и manual_bpm положительный,
        # сетка битов для snap-to-beat строится из значения пользователя
        # вместо запуска librosa-детектора темпа. Полезно, когда онсеты в
        # треке слабые/неоднозначные или темп известен заранее.
        self.manual_bpm = float(manual_bpm)
        self.use_manual_bpm = bool(use_manual_bpm)
        self.detected_bpm: float = 0.0  # заполняется после analyze()

    def _load_audio(self, path: str):
        """Пробует загрузить аудио, при неудаче транскодирует через ffmpeg.

        Для очень больших файлов сразу занижаем частоту до 11025 Гц.
        Дефолтный `librosa.load` (sr=22050) на 30-минутном треке требует
        ~600 МБ float32 под STFT сверх самой волны - на машине со
        свободными 8 ГБ это регулярно роняет поток рендера по OOM.
        Понижение sr вдвое режет и волну, и STFT, а на детекции
        onset/RMS/flatness для обычной музыки это не сказывается заметно
        (весь нужный контент лежит ниже пониженного Найквиста).
        """
        import subprocess, tempfile

        # Выбираем рабочий sample rate по размеру файла. Все, что больше
        # ~150 МБ на диске, почти наверняка длинный несжатый WAV
        # (passthrough-экстракция дает 44.1 кГц s16 стерео, ~10.6 МБ/мин)
        # или очень длинный сжатый трек - в обоих случаях нужен sr пониже.
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

        # Первая попытка не удалась - транскодируем в 16-битный моно WAV через ffmpeg
        print('[ANALYZER] Direct load failed; transcoding via ffmpeg...')
        from vpc.render.sink import ffmpeg_bin
        _ffmpeg = ffmpeg_bin()
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
        """Притягивает каждый онсет к ближайшему биту в пределах tolerance секунд.

        Дубликаты, возникающие после притяжения (два онсета попадают на
        один бит), схлопываются - остается только первый.
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

        # Убираем дубли, сохраняя порядок
        seen: set = set()
        result = []
        for t in sorted(snapped):
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def analyze(self) -> Tuple[List[Segment], float, Optional[AudioFeatures]]:
        """Возвращает (segments, audio_duration, features).

        Если файл не читается или поврежден, сначала пробует
        транскодировать его в PCM WAV через ffmpeg. Если и это не
        помогло, возвращает пустой список сегментов, чтобы движок мог
        отработать дальше (без эффектов, но без падения).
        """
        y, sr = self._load_audio(self.audio_path)

        if y is None or len(y) == 0:
            print('[ANALYZER] Warning: audio file could not be decoded. '
                  'Running without audio analysis.')
            return [], 0.0, None

        duration = len(y) / sr

        # Подбор временного разрешения: вместо того чтобы сразу брать
        # огромный hop=2048 (просадка точности кадра до 186 мс), где
        # возможно держим частоту кадров ~40-50 FPS (шаг ~20-25 мс).
        # При sr=22050: hop=512 дает 23.2 мс.
        # При sr=11025: hop=256 дает 23.2 мс.
        # hop=1024 (разрешение 46.4/92.9 мс) берем только для очень
        # длинных миксов, чтобы не раздувать STFT.
        if duration > 1200.0:  # > 20 минут
            hop = 1024
            n_fft = 1024
        else:
            hop = 512 if sr == 22050 else 256
            n_fft = 2048

        # --- Harmonic-Percussive Source Separation (HPSS) и фолбэк ---
        print('[ANALYZER] Computing Harmonic-Percussive Source Separation (HPSS)...')
        try:
            y_harmonic, y_percussive = librosa.effects.hpss(y)
            # Доля перкуссионной энергии в сигнале
            rms_raw_total = float(np.sqrt(np.mean(y**2)))
            rms_perc_total = float(np.sqrt(np.mean(y_percussive**2)))
            percussive_ratio = (rms_perc_total / rms_raw_total) if rms_raw_total > 1e-5 else 0.0
            print(f'[ANALYZER] Percussive energy ratio: {percussive_ratio:.3f}')
        except Exception as e:
            print(f'[ANALYZER] HPSS error: {e}. Falling back to raw audio.')
            y_percussive = y
            y_harmonic = y
            percussive_ratio = 0.0

        # Если трек преимущественно гармонический (эмбиент, акустика,
        # соло-вокал) - анализируем сырой микс. Иначе используем
        # выделенную перкуссионную составляющую для сетки/онсетов.
        use_raw_fallback = percussive_ratio < 0.15
        if use_raw_fallback:
            print('[ANALYZER] Track has low percussive energy. Using raw signal for onset & transient analysis.')
            y_onsets = y
            y_transients = y
        else:
            print('[ANALYZER] Percussive track detected. Focusing on drum transients.')
            y_onsets = y_percussive
            y_transients = y_percussive

        # Настройки детекции онсетов под быстрые breakcore-дроны на барабанах:
        # wait = 40 мс в кадрах, чтобы ловить частые удары без двойного
        # срабатывания на одном ударе.
        wait_frames = max(1, int(round(0.040 * sr / hop)))
        onset_env = librosa.onset.onset_strength(
            y=y_onsets, sr=sr, hop_length=hop, n_fft=n_fft)
        onsets = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, hop_length=hop,
            units='time', backtrack=True, wait=wait_frames
        )

        # Массивы RMS и flatness:
        # 1. rms_raw_frames - общая громкость микса (для классификации loud/silence)
        # 2. rms_transient_frames - громкость целевого сигнала (для rms_change при поиске транзиентов)
        rms_raw_frames = librosa.feature.rms(y=y, hop_length=hop)[0]
        if use_raw_fallback:
            rms_transient_frames = rms_raw_frames
        else:
            rms_transient_frames = librosa.feature.rms(y=y_transients, hop_length=hop)[0]

        flat_frames = librosa.feature.spectral_flatness(
            y=y, hop_length=hop, n_fft=n_fft)[0]

        # --- Покадровые полосы/спектр для визуализаторов ---
        # Используем те же hop / n_fft, чтобы дорожка была синхронизирована
        # с фичами, которые уже потребляет классификатор. Каждый массив
        # нормируется к ~0..1 относительно своего максимума.
        def _norm01(a: np.ndarray) -> np.ndarray:
            a = np.asarray(a, dtype=np.float32)
            m = float(a.max()) if a.size else 0.0
            return (a / m) if m > 1e-6 else np.zeros_like(a)

        S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))   # (1+n_fft/2, T)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        n_t = S.shape[1]
        times_track = librosa.frames_to_time(np.arange(n_t), sr=sr, hop_length=hop)

        def _band(lo: float, hi: float) -> np.ndarray:
            sel = (freqs >= lo) & (freqs < hi)
            return S[sel].mean(axis=0) if sel.any() else np.zeros(n_t)

        bass_track = _norm01(_band(0, 250))
        mid_track = _norm01(_band(250, 2000))
        high_track = _norm01(_band(2000, sr / 2))
        onset_env_times = librosa.frames_to_time(
            np.arange(len(onset_env)), sr=sr, hop_length=hop)
        onset_track = _norm01(np.interp(times_track, onset_env_times, onset_env))

        # Лог-полосы амплитуды для полос настоящего эквалайзера.
        edges = np.logspace(np.log10(20), np.log10(sr / 2), N_BINS + 1)
        bins = np.zeros((n_t, N_BINS), dtype=np.float32)
        for b in range(N_BINS):
            sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
            if sel.any():
                bins[:, b] = S[sel].mean(axis=0)
        bins = _norm01(bins)

        features = AudioFeatures(
            times=times_track, bass=bass_track, mid=mid_track, high=high_track,
            onset=onset_track, bins=bins, sr=sr, hop=hop, y=y,
        )

        onsets = list(onsets)

        # ---- Притяжение к битам -------------------------------------------
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

        # Медианные значения по сырым аудио-фичам
        noise_floor = float(np.percentile(rms_raw_frames, 15))
        active_rms = rms_raw_frames[rms_raw_frames > noise_floor]
        rms_mean = float(np.median(active_rms)) if len(active_rms) > 0 \
                   else float(np.mean(rms_raw_frames))
        flat_mean = float(np.median(flat_frames))

        # Отдельное среднее для транзиентного сигнала - иначе в HPSS-режиме
        # относительные пики определялись бы некорректно
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

        return segments, duration, features
