"""BreakcoreEngine - оркестрирует анализ -> цепочку эффектов -> ffmpeg sink.

Облегчённая реализация: разбор, анализ, детекция сцен, сборка цепочки
эффектов, подготовка датамоша и рендер по сегментам - каждое живёт в своём
небольшом методе (или в соседнем модуле). Цепочка собирается из реестра,
а не из рукописной лестницы if.
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
from vpc.render.reactor import AudioReactor
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

# Датамош намеренно производит битый поток H.264 (без I-кадров). Когда
# встроенный в OpenCV ffmpeg декодирует его, он спамит предупреждениями
# "Invalid NAL unit size", "Error splitting the input into NAL units",
# "partial file". Они ожидаемы и безвредны - заглушаем их, чтобы лог
# оставался читаемым.
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
    """Общее состояние, передаваемое циклам рендера по режимам.

    Изменяемые поля (`frames_emitted`) живут здесь, чтобы `run()` мог
    посмотреть, что успел выдать цикл после BrokenPipeError, даже если
    цикл завершился досрочно.
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
    # Датамош в режиме passthrough: предзапечённый на уровне битстрима
    # источник, который ЗАМЕЩАЕТ живой cap внутри `_run_passthrough_loop`
    # (кодирование с длинным GOP, сохраняющее длину, поэтому покадровое
    # соответствие 1:1 с аудио не нарушается).
    # None = нет предзапечённого датамоша / не в режиме passthrough.
    passthrough_dm_cap: object = None
    # Детерминированный seed для RNG триггеров stutter/flash в passthrough.
    # Планировщик проходит сегменты до sink.open с Random(seed) и пишет
    # аудиопетли; цикл создаёт свежий Random(seed) и прогоняет те же
    # проверки триггеров в том же порядке, получая те же события.
    # None вне passthrough.
    event_rng_seed: Optional[int] = None
    frames_emitted: int = 0
    # Поканальный аудиореактор для эффектов-визуализаторов. Сэмплируется в
    # `_apply_chain` и прикрепляется к seg.live на каждом кадре. None = нет аудио.
    reactor: object = None


class BreakcoreEngine:
    """Оркестратор рендера. Публичный API:

        engine = BreakcoreEngine(cfg, progress_callback)
        engine.run(render_mode='final', max_output_duration=None)
        engine.abort = True   # кооперативная отмена

    `cfg` - устаревший плоский словарь; RenderConfig оборачивает его для
    типизированного доступа.
    """

    def __init__(self, config: dict, progress_callback: Optional[Callable] = None):
        self.cfg = config
        self.config = RenderConfig(config)
        self.progress_callback = progress_callback
        self.abort = False
        self.scene_cuts: List[float] = []
        # Путь к временному wav, извлечённому из исходного видео в режиме
        # passthrough. Отслеживается, чтобы ветка очистки в run() могла его удалить.
        self._tmp_audio_to_clean: Optional[str] = None

    # ----- логирование -----
    def log(self, message: str, value: Optional[int] = None):
        print(f'[ENGINE] {message}')
        if self.progress_callback:
            self.progress_callback(message, value)

    def _log_fx_fail(self, fx, exc: BaseException) -> None:
        """Дросселированный лог ошибок эффекта.

        Раньше один сбойный эффект спамил по строке лога на каждый кадр -
        на получасовом рендере это сотни тысяч строк, что раздувало
        текстовый виджет GUI и буфер stdout вплоть до OOM. Теперь логируем
        первые 3 срабатывания, затем раз в 500 кадров на класс эффекта, а
        после 100 сбоев отключаем эффект до конца рендера.
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

    # ----- детекция сцен -----
    def detect_scenes(self, video_paths: List[str], duration: float):
        if not self.config.use_scene_detect:
            return
        from scenedetect import VideoManager, SceneManager
        from scenedetect.detectors import ContentDetector
        # Жёсткая верхняя граница длительности источника для сканирования.
        # Детекция сцен на 30-минутном источнике занимала минуты CPU и
        # давала скачок RAM на несколько гигабайт в потоке рендера, часто
        # роняя его в OOM ещё до старта рендера. 8 минут покрывают типичный
        # музыкальный клип.
        SCENE_DETECT_LIMIT_SEC = 480
        self.log('Detecting scenes...')
        all_cuts: List[float] = []
        for video_path in video_paths:
            if self.abort:
                break
            # Дёшево прикидываем длительность через cv2 (аналог ffprobe).
            # Пропускаем детекцию сцен для очень длинных источников - они бы
            # уронили поток рендера в OOM ещё до кодирования первого кадра.
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
                # Агрессивный downscale: дефолтный 'auto' у PySceneDetect
                # берёт слишком крупный кадр для HD-контента, тратя RAM
                # без выигрыша в качестве детекции.
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
        """Удаляет временный WAV, извлечённый для passthrough, если он есть. Идемпотентно."""
        p = self._tmp_audio_to_clean
        if p and os.path.exists(p):
            try: os.remove(p)
            except OSError: pass
        self._tmp_audio_to_clean = None

    # ----- извлечение аудио для passthrough -----
    def _extract_audio_track(self, video_path: str) -> Optional[str]:
        """Тонкая обёртка над `engine_setup.extract_audio_track` с нашим логом."""
        return extract_audio_track(video_path, self.log)

    # ----- хелпер датамоша -----
    def _prepare_datamosh_source(self, video_path: str, output_path: str,
                                 *, mode: str = 'strip') -> bool:
        """Тонкая обёртка над `engine_setup.prepare_datamosh_source`."""
        return prepare_datamosh_source(video_path, output_path, self.log,
                                       mode=mode)

    # ----- прогресс / ETA -----
    @staticmethod
    def _fmt_dur(secs: float) -> str:
        """Компактный формат 'Xm YYs' / 'YYs' / '1.2s' для строк ETA."""
        if secs < 0 or secs != secs:  # защита от NaN
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
        """Дросселированный прогресс + ETA. Вызывается после каждого
        закодированного кадра; колбэк реально срабатывает не чаще раза в
        PROGRESS_INTERVAL секунд реального времени, чтобы не заваливать GUI.

        ETA - линейная экстраполяция: elapsed * (remaining / done). Первая
        секунда рендера исключается (слишком шумная оценка) - процент всё
        равно выдаём, просто без ETA.
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
            rate = frames_emitted / elapsed   # закодированных кадров в сек
            # Ниже ~0.5 fps оценка скорости забита шумом от setup (сик
            # первого сегмента, разогрев кодека). Показывать "ETA 22h" в
            # этом окне вводит в заблуждение сильнее, чем помогает; выдаём
            # заглушку, пока не долетит хотя бы 5 кадров.
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

    # ----- прогон цепочки эффектов (общий для обоих циклов рендера) -----
    def _apply_chain(self, frame: np.ndarray, seg: 'Segment',
                     ctx: '_RenderCtx') -> np.ndarray:
        """Прогоняет покадровую цепочку эффектов + mystery section.

        Идентичное тело, которое раньше дублировалось инлайном в
        `_run_segment_loop` и `_run_passthrough_loop`. Поведение побайтово
        совпадает - тот же порядок итерации, то же дросселированное
        логирование ошибок на эффект.
        """
        # Поканальный аудиосэмпл для эффектов-визуализаторов. Рендер
        # однопоточный и последовательный -> мутировать общий seg здесь
        # безопасно; невизуализаторские эффекты игнорируют seg.live.
        if ctx.reactor is not None:
            t = ctx.frames_emitted / ctx.fps if ctx.fps else 0.0
            seg.live = (ctx.reactor.sample(t) if getattr(ctx.reactor, 'f', None) is not None
                        else ctx.reactor.synth(seg.intensity, ctx.frames_emitted))
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

    # ----- упаковка для пайпа -----
    @staticmethod
    def _pack_frame(rgb: np.ndarray, input_pix_fmt: str) -> bytes:
        """Конвертирует uint8 RGB HxWx3 кадр в пиксельный формат пайпа.

        - 'rgb24' -> сырые байты, 3 байта/пиксель.
        - 'yuv420p' -> планарный I420 через OpenCV, 1.5 байта/пиксель.
          ffmpeg сделал бы то же преобразование внутри себя, так что в
          качестве мы ничего не теряем, зато вдвое режем пропускную
          способность межпроцессного канала.

        Нераспознанный формат откатывается на rgb24, чтобы будущая
        ошибка конфигурации кодека не породила молча битые байты.
        """
        if input_pix_fmt == 'yuv420p':
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2YUV_I420).tobytes()
        return rgb.tobytes()

    # ----- обработка тишины -----
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

    # ----- горячий цикл -----
    def run(self, render_mode: str = RENDER_FINAL,
            max_output_duration: Optional[float] = None):
        cfg = self.cfg
        rc = self.config
        video_paths = rc.video_paths
        audio_path = rc.audio_path
        output_path = rc.output_path

        # Режим passthrough берёт аудио из исходного видео. Если
        # пользователь дополнительно выбрал внешний аудиофайл, всё равно
        # предпочитаем извлечённую дорожку - passthrough означает "всё из
        # этого одного видео".
        if rc.passthrough_mode:
            if not video_paths:
                self.log('ERROR: passthrough mode requires a source video.')
                return False
            # Passthrough работает 1:1 поверх настоящего видео (таймлайн +
            # своё аудио) - фото не может дать ни того, ни другого. Берём
            # первый видеоисточник и выбрасываем фото из пула для этого
            # рендера; пул из одних фото - жёсткая ошибка.
            from vpc.render.source import is_image
            video_only = [p for p in video_paths if not is_image(p)]
            if not video_only:
                self.log('ERROR: passthrough mode requires a video source '
                         '(a photo has no timeline or audio). Load a video or '
                         'turn passthrough off.')
                return False
            dropped = len(video_paths) - len(video_only)
            if len(video_only) > 1 or dropped:
                msg = f'Passthrough: using only {os.path.basename(video_only[0])}'
                if dropped:
                    msg += f' ({dropped} image source(s) ignored)'
                self.log(msg + '.')
            video_paths = [video_only[0]]
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

        # Внешний guard вокруг setup + render: гарантирует, что временный
        # WAV, извлечённый в режиме passthrough, будет удалён, даже если
        # что-то между этим местом и finally внутреннего pool/sink
        # выбросит исключение (конструктор анализатора, VideoPool,
        # проба энкодера, открытие sink, ...).
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

        # Анализ аудио выполняется первым, чтобы использовать его
        # длительность для расчёта размеров. В режиме passthrough аудио
        # может вовсе отсутствовать (немое видео) - тогда segments=[] и
        # цикл ниже рендерит passthrough без эффектов (всё классифицируется
        # как SILENCE).
        segments: List[Segment] = []
        audio_duration = 0.0
        analyzer_bpm = 0.0
        audio_features = None
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
            segments, audio_duration, audio_features = analyzer.analyze()
            analyzer_bpm = analyzer.detected_bpm
            if audio_duration == 0.0 or not segments:
                self.log('Warning: audio unreadable / no segments — output will have no effects.')

        # Открываем пул видео заранее - нужен и для расчёта размеров, и для
        # выбора эффективной длительности при работе в passthrough.
        pool = VideoPool(video_paths)

        # Эффективная длительность рендера. Обычный режим: определяется
        # длиной аудио. Passthrough: определяется исходным видео, так как
        # выход 1:1 со входными кадрами. Если аудио короче видео - дополняем
        # аудио; если длиннее - игнорируем хвост (для него нет видео).
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
        # В режиме passthrough движок читает входные кадры последовательно
        # и пишет один выходной кадр на один входной. Если выбранный
        # пользователем выходной FPS отличается от нативного FPS источника,
        # видео проигралось бы со скоростью `out_fps / src_fps` × аудио -
        # гарантированный рассинхрон. Принудительно ставим выходной FPS
        # равным нативному FPS источника; сообщаем об этом пользователю.
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

        # В режиме passthrough рендерер читает кадры последовательно - там
        # нет случайной выборки, которой могли бы помочь границы сцен.
        if not rc.passthrough_mode:
            self.detect_scenes(video_paths, pool.vid_duration)

        # Цепочка эффектов из реестра.
        effects = build_chain({**cfg, 'overlay_dir': rc.overlay_dir})
        mystery = MysterySection()
        for k, v in rc.mystery.items():
            if hasattr(mystery, k):
                try:
                    setattr(mystery, k, float(v))
                except (TypeError, ValueError):
                    pass
        # Флаги always-on по каждой ручке. Неизвестные ключи молча
        # игнорируются - это сохраняет прямую/обратную совместимость
        # движка с диалектами cfg.
        for k, v in rc.mystery_always.items():
            attr = f'always_{k}'
            if hasattr(mystery, attr):
                setattr(mystery, attr, bool(v))

        chaos = rc.chaos
        flash_chance = min(1.0, rc.flash_chance_base * (0.3 + 0.7 * chaos))
        flash_fx = FlashEffect(enabled=True, chance=1.0)

        # ----- пред-проход аудиопетель stutter для passthrough -----
        # Делается ДО sink.open, потому что WAV поглощается ffmpeg в тот
        # момент, когда sink стартует. Детерминизм через seed -> идентичные
        # события в цикле, поэтому аудиопетли совпадают с видеопетлями
        # посэмпльно точно.
        event_rng_seed: Optional[int] = None
        if (rc.passthrough_mode and audio_path
                and (rc.stutter_enabled or rc.flash_enabled)):
            try:
                pre_target_total = int(round(target_duration * fps))
                # Строим тот же seg_list, что использует цикл (заполнитель
                # начального пробела, если segments[0].t_start > 0; иначе
                # сегменты как есть). Триггеры считаются именно по этому
                # списку, чтобы проходы курсора совпадали.
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

        # Пред-проход аудиодефектов - НЕЗАВИСИМ от гейта stutter/flash.
        # Пользователь может включить аудио-связку, не трогая stutter или
        # flash; прежняя вложенность молча съедала эти дефекты, потому что
        # внешний `if (... stutter or flash)` оказывался False. Дефекты
        # должны всегда выполняться при (режим passthrough + есть
        # аудиоисточник); сам пайплайн дефектов рано выходит, если ни одна
        # связка не включена, так что безусловный вызов здесь дёшев.
        if rc.passthrough_mode and audio_path:
            try:
                apply_passthrough_audio_defects(audio_path, cfg, self.log)
            except Exception as exc:
                self.log(f'Audio defects skipped: {exc}')

        # ----- ffmpeg sink -----
        # Определяем спецификацию энкодера по выбранной пользователем
        # метке. Если метка была сохранена на машине с HW-поддержкой, а
        # здесь её нет, переходим на программный fallback ещё до попытки.
        spec = find_encoder_spec(rc.video_codec_label)
        if spec is None:
            self.log(f"Unknown codec label '{rc.video_codec_label}', "
                     f"using {encoder_fallback_spec().label}.")
            spec = encoder_fallback_spec()

        # Рантайм-самопроверка для HW-энкодеров. Некоторые заявленные, но
        # нерабочие энкодеры принимают пайп при инициализации, а потом
        # никогда не выдают закодированный кадр - симптом: рендер намертво
        # застревает на 0%. Проба коротким testsrc + жёстким таймаутом
        # ловит это ДО открытия настоящего sink, затем автоматически
        # откатывается на libx264. Результат кэшируется на процесс, так что
        # это стоит ~1-2с только на первом рендере сессии, использующем
        # данный HW-энкодер. Программным кодекам доверяем безусловно и
        # пропускаем пробу.
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

        # Аппаратные энкодеры падают при инициализации, если драйвер
        # отсутствует или занят ('No NVENC capable devices', 'Failed to
        # initialize MFX session', 'Cannot load amfrt64.dll'). Проверяем
        # рано - если ffmpeg уже завершился, гасим сбой и переоткрываем с
        # libx264 вместо падения рендера. Программные кодеки пропускают эту
        # проверку; таймаут достаточно короткий, чтобы не ощущаться.
        if spec.is_hw:
            err = sink.early_failure(wait=0.5)
            if err is not None:
                self.log(f'HW encoder {spec.vcodec} failed at init '
                         f'(falling back to libx264). Cause:\n{err.strip()[:400]}')
                fb = encoder_fallback_spec()
                spec = fb
                sink = _open_sink(fb)

        # ----- legacy предзапекание датамоша (fx_datamosh / Optical Flow) -----
        # Старый путь сохранён дословно ради совместимости пресетов:
        # пресеты, сохранённые до разделения на Optical Flow / True
        # Datamosh, ожидают, что fx_datamosh сохранит всё историческое
        # поведение, включая это предзапекание в Final-режиме. Новому True
        # Datamosh (fx_truemosh) ничего этого не нужно - это обычный
        # эффект цепочки с внутрипроцессной парой кодек-декодек.
        # Оба режима используют ОДНО И ТО ЖЕ 'strip'-предзапекание
        # битстрима (длинный GOP, только P-кадры, одна референсная,
        # затем удаление каждого исходного I-кадра). Эффект на уровне
        # битстрима - это настоящий смаз датамоша: декодер вынужден
        # продолжать применять векторы движения к устаревшему содержимому,
        # потому что ключевых кадров, которые бы его обновили, больше нет.
        #
        # Strip выбрасывает кадры, поэтому предзапечённый файл КОРОЧЕ
        # исходника. Два режима расходятся только в том, как движок
        # потребляет получившийся cap:
        #   * Cut-режим -> подмена на NOISE: живой cap заменяется
        #     предзапечённым только на сегментах NOISE. Случайная выборка
        #     означает, что меньшая длина неважна - движок просто сикает
        #     в тот member пула, который сейчас читает.
        #   * Passthrough-режим -> stretch-replay: предзапечённый cap
        #     ЗАМЕЩАЕТ живой, и цикл отображает каждый выходной кадр fi в
        #     индекс предзапека `int(fi * n_prebake / target_total_frames)`.
        #     Каждый сохранённый P-кадр показывается ~target/n_prebake раз,
        #     поэтому там, где в исходнике был I-кадр, уцелевшая P-цепочка
        #     проигрывается через несколько выходных кадров как
        #     заморозка-со-смазом. `target_total_frames = audio_duration ×
        #     fps` не меняется, так что аудио остаётся выровнено 1:1 с
        #     выходными кадрами.
        # Питоновский OpticalFlowEffect (смаз на основе оптического потока)
        # работает в обоих режимах через обычную цепочку эффектов -
        # независимо от предзапекания битстрима и дополняя его.
        datamosh_source_path = None
        datamosh_cap = None
        datamosh_total_frames = pool.vid_total_frames
        passthrough_dm_cap = None
        passthrough_dm_path = None
        # Предзапеканию датамоша нужно настоящее видео (оно выдирает
        # I-кадры из битстрима). Берём первое видео в пуле - в passthrough
        # пул и так состоит из одного видео, в обычном режиме это
        # пропускает фото-источники, которые иначе молча испортили бы
        # предзапек.
        dm_idx = pool.first_video_index() if (is_final and rc.datamosh_enabled) else None
        if is_final and rc.datamosh_enabled and dm_idx is None:
            self.log('Datamosh: no video source in pool — using optical flow only.')
        if is_final and rc.datamosh_enabled and dm_idx is not None:
            dm_src_path = pool.paths[dm_idx]
            dm_path = output_path + '_dmosh_src.mp4'
            if os.path.exists(dm_path):
                try: os.remove(dm_path)
                except OSError: pass
            self.log('Preparing datamosh source (strip)...')
            if self._prepare_datamosh_source(dm_src_path, dm_path, mode='strip'):
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

        # ----- главный цикл -----
        # Итоговое целевое число кадров для всего рендера. Сверяем
        # frames_emitted с этим счётчиком - любая недостача от округления
        # компенсируется дублированием последнего кадра после конца цикла,
        # так что закодированное видео точно совпадает с целевой
        # длительностью.
        target_total_frames = int(round(target_duration * fps))

        # Учёт ETA. _render_t0 стартует здесь (после анализа + детекции
        # сцен + предзапекания датамоша), чтобы выводимый ETA отражал сам
        # цикл кодирования, а не работу по настройке. _last_progress_t
        # дросселирует колбэки примерно до 2 Гц.
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
            reactor=AudioReactor(audio_features, fps=fps),
        )

        try:
            if rc.passthrough_mode:
                frames_emitted = self._run_passthrough_loop(ctx)
            else:
                frames_emitted = self._run_segment_loop(ctx)
        except (BrokenPipeError, OSError):
            self.log('ffmpeg pipe closed early.')
            frames_emitted = ctx.frames_emitted
            # Считаем смерть пайпа сбоем - без этого лог успеха ("Done in
            # Xs (Y.YYx realtime)") напечатался бы после краха.
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
            # Очистка временного WAV обрабатывается внешним guard'ом в run().

        if not self.abort:
            elapsed = time.perf_counter() - self._render_t0
            rt_factor = (target_duration / elapsed) if elapsed > 0 else 0.0
            self.log(f'Done in {self._fmt_dur(elapsed)} '
                     f'({rt_factor:.2f}x realtime). Output: {output_path}')
            return True
        return False

    # ----- цикл нарезки по сегментам (режим по умолчанию) -----
    def _run_segment_loop(self, ctx: '_RenderCtx') -> int:
        """Исходный путь рендера "вырезать-и-склеить": случайное время
        источника на сегмент, вставка stutter/flash, подмена на датамош
        на NOISE.

        Возвращает число кадров, реально записанных в sink.
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
            # Цель на сегмент использует накопительное округление: считаем,
            # куда ДОЛЖЕН попасть хвост сегмента в абсолютном кадровом
            # пространстве, и вычитаем уже выданные кадры. Это устраняет
            # накопленную потерю в полкадра, которую давал старый путь
            # `int(seg_dur * fps)` на сотнях сегментов.
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

            # Статтер
            stutter_repeat = 1
            if (rc.stutter_enabled and seg.type == SegmentType.IMPACT
                    and seg.duration < 0.3):
                if random.random() < (0.3 + ctx.chaos * 0.5):
                    stutter_repeat = random.choice([2, 4, 8])

            # Флэш
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

                # Кооперативная отмена внутри цикла по сегменту, чтобы
                # долгий SUSTAIN не создавал ощущение зависшей кнопки отмены.
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

        # Добивка хвоста: покрывает недостачу от округления, чтобы видео совпадало с аудио.
        if not self.abort and last_frame_bytes is not None:
            pad_count = ctx.target_total_frames - ctx.frames_emitted
            if pad_count > 0:
                self.log(f'Padding tail: {pad_count} frame(s) to match audio.')
                for _ in range(pad_count):
                    if not sink.write(last_frame_bytes):
                        break
                    ctx.frames_emitted += 1
        return ctx.frames_emitted

    # ----- цикл passthrough (1:1 источник -> выход) -----
    def _run_passthrough_loop(self, ctx: '_RenderCtx') -> int:
        """Читает кадры из исходного видео последовательно; отображает
        временную метку каждого кадра на сегмент через монотонный линейный
        курсор (кадры идут по порядку, поэтому полный бинарный поиск не нужен).

        Stutter, Flash и Datamosh здесь работают, но Stutter и Flash
        переключаются в режим REPLACE (INSERT-режим как в cut-режиме сдвинул
        бы выход относительно входного аудио): следующие N кадров они
        перезаписывают выданный кадр удержанной копией (stutter) или цветом
        флэша, при этом всё равно вызывая cap.grab(), чтобы указатель
        источника продолжал шагать синхронно с аудио. Optical Flow (старые
        ключи fx_datamosh) - это просто обычный эффект в цепочке
        (OpticalFlowEffect, смаз векторов движения на основе оптического
        потока) - предзапека здесь нет, так как удаление I-кадров из потока
        1:1 изменило бы число кадров и рассинхронизировало аудио. True
        Datamosh (fx_truemosh) аналогично является обычным эффектом цепочки
        1-в-1-из.

        Если `segments` пуст (нет аудио / извлечение не удалось), каждый
        кадр рендерится с синтезированным сегментом SILENCE, т.е. эффекты,
        завязанные на не-тихие типы, молчат.
        """
        rc = self.config
        sink = ctx.sink; pool = ctx.pool
        # Подмена датамоша для passthrough. Предзапечённый cap короче
        # источника (I-кадры выдраны), поэтому не читаем его 1:1; вместо
        # этого `dm_stretch` включает ниже покадровый stretch-replay.
        if ctx.passthrough_dm_cap is not None:
            cap = ctx.passthrough_dm_cap
            dm_stretch = True
            dm_n_prebake = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
            # `dm_step` - насколько продвигаемся по предзапеку за один
            # выходной кадр (дробное значение, хранимое в `dm_cursor`).
            # Например, если 12% исходных кадров были I-кадрами, в
            # предзапеке ~88% от их числа, dm_step ≈ 0.88, и в среднем
            # один и тот же кадр предзапека показывается ~1.14× прежде чем
            # курсор перекатится на следующий - визуально это
            # заморозка-со-смазом.
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

        # Синтетический SILENCE используется и как заполнитель "до любого
        # сегмента", и как fallback при полном отсутствии аудио. Если
        # первый настоящий сегмент начинается с t > 0 (аудио начинается с
        # тишины, которую анализатор пропустил), добавляем этот
        # заполнитель спереди, чтобы не красить задним числом кадры до
        # t_start типом первого сегмента.
        idle_seg = Segment(
            t_start=0.0, t_end=ctx.target_duration,
            duration=ctx.target_duration, type=SegmentType.SILENCE,
            intensity=0.0, rms=0.0, flatness=0.0, rms_change=0.0,
        )

        # Линейный курсор по `seg_starts`: кадры идут монотонно, поэтому
        # бинарный поиск того не стоит.
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

        # Состояние режима replace для Stutter и Flash. Оба эффекта
        # "срабатывают", защёлкивая один из этих счётчиков в N>0 -
        # следующие N кадров цикл переопределяет выход, ВСЁ ЖЕ продвигая
        # указатель источника (cap.grab в режиме 1:1, dm_cursor в
        # dm_stretch). Так выход остаётся 1:1 со входными кадрами и
        # синхронизация с аудио сохраняется. Триггеры срабатывают не чаще
        # раза за сегмент, чтобы фаза replace аккуратно охватывала границу
        # сегмента вместо повторного срабатывания на каждом кадре внутри
        # одного IMPACT.
        #
        # Stutter - это DRILL-петля: она не замораживает один кадр, а
        # проигрывает последние `loop_size` декодированных кадров по
        # тесному кругу. Именно это даёт дриллкору характерное
        # микро-заикание "br-r-r-r-rt" вместо плоской заморозки.
        # `frame_history` - кольцевой буфер, из которого строится петля при
        # срабатывании триггера; maxlen ограничивает максимально возможную
        # длину дрилла.
        STUTTER_HISTORY = 4
        frame_history: deque = deque(maxlen=STUTTER_HISTORY)
        stutter_remaining = 0
        stutter_loop: Optional[List[bytes]] = None
        stutter_idx = 0
        flash_remaining = 0
        flash_frame_bytes: Optional[bytes] = None
        last_trigger_cursor = -1
        # RNG триггеров зеркалит планировщик. Когда seed is None (нет
        # аудио / пред-проход пропущен) откатываемся на глобальный модуль
        # `random` - дрилл всё равно проиграется, но аудио не будет
        # заранее зациклено.
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

            # В режиме replace переопределяем ВЫХОДНОЙ кадр этой итерации,
            # но всё равно продвигаем позицию в источнике - в режиме 1:1
            # это `cap.grab()` (дешёвый шаг без декодирования); в режиме
            # dm_stretch это `dm_cursor += dm_step` (тот же логический шаг,
            # но отслеживаемый в вещественном пространстве предзапека, а
            # не в целочисленном пространстве источника).
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
                    # Drill-replay: цикл по захваченной петле. idx
                    # заворачивается через modulo, поэтому любая
                    # комбинация loop_size + оставшегося числа безопасна.
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
                    # Защита: самый первый кадр каким-то образом попал в
                    # replace - откатываемся на реальное декодирование в
                    # этой итерации.
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
                # Stretch-replay: продвигаем cap предзапека, пока индекс
                # его текущего загруженного кадра не покроет `dm_cursor`.
                # Каждая итерация шагает `dm_cursor` на `dm_step`; потолок
                # `dm_cursor` говорит, какой индекс кадра предзапека нужно
                # загрузить прямо сейчас.
                desired_idx = int(dm_cursor)
                while dm_loaded_idx < desired_idx:
                    ret_dm, dm_bgr = cap.read()
                    if not ret_dm:
                        # Предзапек исчерпан - откатываемся на последний
                        # успешно удержанный кадр до конца рендера, чтобы
                        # аудио всё равно было спарено с видео.
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
                        # Первое чтение не удалось - sink завис бы на
                        # видеопотоке с нулём кадров + ненулевым аудио. Выходим.
                        self.log('ERROR: passthrough failed on first frame — '
                                 'source video unreadable.')
                        self.abort = True
                    # Источник закончился раньше target_total_frames -
                    # дополняем последним хорошим кадром (если есть), чтобы
                    # ffmpeg не обрезал аудио.
                    break

            frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            if frame.shape[0] != ctx.out_h or frame.shape[1] != ctx.out_w:
                frame = cv2.resize(frame, (ctx.out_w, ctx.out_h))

            if seg.type == SegmentType.SILENCE and seg.duration > 1.0:
                frame = self._apply_silence(frame)

            frame = self._apply_chain(frame, seg, ctx)

            fb = self._pack_frame(frame, sink.input_pix_fmt)

            # Взведение триггера по сегменту. cursor продвигается при
            # пересечении границы сегмента; используем его как одноразовый
            # ключ, чтобы долгий SUSTAIN/IMPACT не переспускал stutter
            # каждый кадр. Пред-проход планировщика идёт по тому же
            # seg_list с идентичным RNG, поэтому взведённые здесь события
            # совпадают с тем, под что был заранее зациклен WAV - аудио-
            # дриллы попадают в те же куски, что и видео-дриллы.
            #
            # `last_trigger_cursor = cursor` выполняется БЕЗУСЛОВНО при
            # смене cursor. Если счётчики всё ещё > 0, мы намеренно
            # проглатываем триггер этого сегмента - так же как планировщик
            # делает в своей ветке `if state_remaining > 0: continue`.
            # Обновление только при успешном взведении позволило бы
            # триггеру сработать ПОЗЖЕ в том же сегменте (после того как
            # счётчики истощатся), а планировщик не знал бы, что это нужно
            # отразить в аудио.
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
                        # Фиксированный размер петли = STUTTER_LOOP_SIZE
                        # (планировщик использует ту же константу).
                        # Содержимое петли - последние (loop_size - 1)
                        # выданных кадра + текущий `fb`, чтобы внутри цикла
                        # дрилла реально проигрывалось движение - именно
                        # это делает эффект слышимым/видимым как заикание,
                        # а не заморозку.
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

        # Добивка хвоста, если исходное видео короче target_total_frames.
        if not self.abort and last_frame_bytes is not None:
            pad_count = ctx.target_total_frames - ctx.frames_emitted
            if pad_count > 0:
                self.log(f'Padding tail: {pad_count} frame(s) (source ran out).')
                for _ in range(pad_count):
                    if not sink.write(last_frame_bytes):
                        break
                    ctx.frames_emitted += 1
        return ctx.frames_emitted


