"""Автономные хелперы для настройки ffmpeg, используемые BreakcoreEngine.

Вынесены из `engine.py`, чтобы оркестратор оставался сфокусирован на самом
пайплайне рендера. Каждая функция здесь - чистый хелпер: не трогает
состояние движка, работает только с файловой системой и субпроцессом
ffmpeg. Движок оборачивает каждый вызов тонким методом, который прокидывает
свой `log`-колбэк.
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
    """Демультиплексирует аудио из `video_path` во временный WAV, возвращает путь к нему.

    Возвращает None, если в видео нет аудиодорожки или извлечение не удалось -
    в этом случае движок всё равно рендерит, но без сегментов и без звука
    на выходе.

    Стерео 44.1 кГц s16 сохраняется намеренно: этот WAV одновременно и
    анализируется, и вмуксовывается обратно в результат как аудиодорожка.
    Понижение частоты здесь было бы слышно (схлопывание стерео-панорамы,
    потеря верхов). Анализатор делает собственный даунсемпл уже на
    waveform в памяти.
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    tmp.close()
    # Дёшево прикидываем длительность, чтобы отмасштабировать таймаут ffmpeg.
    # Старый жёсткий потолок в 120 с обрывал извлечение на исходниках для
    # passthrough длиннее 30 минут прямо посреди записи, оставляя битый WAV.
    try:
        _cap = cv2.VideoCapture(video_path)
        _fps = float(_cap.get(cv2.CAP_PROP_FPS) or 24.0)
        _n = float(_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        _cap.release()
        src_dur = (_n / _fps) if _fps > 0 else 0.0
    except Exception:
        src_dur = 0.0
    # ~1 с реального времени на 60 с аудио - с запасом; ограничиваем снизу
    # 60 с, сверху 30 минутами, чтобы избежать бесконечного зависания.
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
    """Перекодирует `video_path` для "настоящего" датамоша, в двух вариантах.

    Общие флаги (для обоих режимов):
      • ``-bf 0`` - убивает B-кадры. Они декодируются не в порядке показа
        и ссылаются в обе стороны по времени; они бы сбрасывали цепочку
        смазывания и портили эффект.
      • ``-sc_threshold 0`` - запрещает энкодеру самому вставлять I-кадры
        на смене сцены. Без этого libx264 молча рассыпает I-кадры там,
        где движение сильно меняется, и рвёт длинную P-цепочку, на
        которой держится вид датамоша.
      • ``-g 99999 -keyint_min 99999`` - форсирует максимально длинный GOP,
        так что практически всё становится P-кадрами.
      • ``-refs 1`` - каждый P-кадр ссылается только на непосредственного
        предшественника; это и даёт длинную, "плывущую" цепочку векторов
        движения, характерную для настоящего датамоша.
      • ``-preset slow`` - заметно лучше оценка движения, чем на ultrafast.
        На ultrafast энкодер сдаётся на сложных для трекинга участках и
        вставляет интра-блоки ВНУТРИ P-кадров, что выглядит как статичные
        "кирпичи", а не смазывание.

    Режимы:
      • ``mode='strip'`` (дефолт для cut-режима) - дополнительно выкидывает
        все исходные I-кадры через ``select=not(eq(pict_type,I))``. Число
        кадров УМЕНЬШАЕТСЯ, поэтому безопасно только в cut-режиме, где
        синхронизация звука идёт через случайную выборку, а не через
        покадровое соответствие 1:1.
      • ``mode='longgop'`` (passthrough-режим) - сохраняет каждый исходный
        кадр, длинный GOP с одними P-кадрами форсируется только на стороне
        энкодинга. Число кадров остаётся 1:1, синхронизация со звуком не
        ломается, а декодер всё равно даёт характерное смазывание
        векторов движения на сменах сцен (энкодеру запрещено вставлять
        новые I-кадры там, где исходный контент резко меняется).
    """
    cmd = [ffmpeg_bin(), '-y', '-i', video_path]
    if mode == 'strip':
        cmd += ['-vf', 'select=not(eq(pict_type\\,I))', '-vsync', 'vfr']
    elif mode == 'longgop':
        # Без фильтра: число кадров остаётся 1:1 с исходником, чтобы
        # passthrough-цикл мог выравнивать кадры со звуком. Вид датамоша
        # тут дают только флаги энкодера ниже.
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
    # Масштабируем таймаут под длительность исходника. `-preset slow` на
    # 30-минутном видео пробьёт любой фиксированный потолок; а без таймаута
    # ffmpeg на битых потоках иногда зависает насовсем.
    try:
        _cap = cv2.VideoCapture(video_path)
        _fps = float(_cap.get(cv2.CAP_PROP_FPS) or 24.0)
        _n = float(_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        _cap.release()
        src_dur = (_n / _fps) if _fps > 0 else 0.0
    except Exception:
        src_dur = 0.0
    # Пресет `slow` на современном железе примерно 1x realtime; берём
    # запас x4 и ограничиваем снизу 5 минутами, сверху 60.
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


# ─── планировщик stutter/flash событий для passthrough ──────────────────
# В passthrough движок обязан знать триггеры stutter ДО старта цикла
# рендера, потому что аудио-пайплайну нужен готовый WAV уже к моменту
# sink.open, а аудио-цикл должен зеркалить видео-цикл. Планировщик один
# раз проходит `seg_list` с детерминированным RNG и возвращает события,
# которые он бы взвёл; цикл рендера затем пересоздаёт ТОТ ЖЕ RNG с тем же
# сидом и идёт по сегментам в том же порядке, так что его решения точно
# совпадают с планировщиком. Именно это удерживает аудио- и видео-циклы
# указывающими на один и тот же кусок исходника.

@dataclass
class StutterEvent:
    """Событие drill-петли, взведённое планировщиком.

    `trigger_frame_index` - это ВЫХОДНОЙ кадр, на котором начинается
    сегмент, вызвавший триггер. Текущий кадр играет как обычно и занимает
    первый слот цикла; кадры-замены занимают диапазон
    `trigger_frame_index + 1 ... trigger_frame_index + total_replace_frames`.
    """
    trigger_frame_index: int
    loop_size_frames: int
    total_replace_frames: int


# Настройки размера drill-петли. `LOOP_SIZE_FRAMES = 2` значит, что петля
# занимает два исходных кадра (~83 мс при 24 fps), и звук слышимо
# переключается через кадр - это и даёт быстрый "дрель"-характер, в
# отличие от более медленного "заморозка и подёргивание" при 3-4.
# `CYCLE_CHOICES` определяет, сколько циклов проиграть, так что общая
# длительность дрели укладывается в окно 83-250 мс - достаточно коротко,
# чтобы читаться как STUTTER, а не как фриз.
STUTTER_LOOP_SIZE = 2
STUTTER_CYCLE_CHOICES = (2, 3, 4)


def event_seed_for_passthrough(audio_path: str, target_total_frames: int,
                               chaos: float) -> int:
    """Строит детерминированный сид RNG для плана событий passthrough.

    Использует путь к аудио + число кадров + chaos, округлённый до 2
    знаков. Одинаковые входные данные дают одинаковый сид, а значит и
    одинаковые события и в планировщике, и в цикле - это и держит
    аудио- и видео-циклы синхронными друг с другом.
    """
    sig = f'{audio_path}|{target_total_frames}|{round(chaos, 2)}'
    digest = hashlib.md5(sig.encode('utf-8')).hexdigest()
    return int(digest[:8], 16)


def _trigger_decision(seg: Segment, rc, event_rng: random.Random,
                      flash_chance: float, chaos: float) -> Optional[Tuple[str, dict]]:
    """Единая проверка взведения события, общая и для планировщика, и для цикла.

    Возвращает ('flash', {'n_flash': N}) или ('stutter', {'cycles': C})
    либо None. Вызовы RNG (и их порядок) ОБЯЗАНЫ совпадать между
    планировщиком и циклом, иначе аудио-цикл попадёт не на тот кусок
    исходника.
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
    """Проходит `seg_list` с детерминированным RNG и формирует события stutter.

    Отражает логику взведения цикла `cursor != last_trigger_cursor and
    counters == 0`: между началами двух последовательных сегментов
    проходит всего `dt_frames` выходных кадров, поэтому долгий
    flash/stutter-латч может целиком подавить триггер СЛЕДУЮЩЕГО
    сегмента. Планировщик ведёт тот же остаточный счётчик, чтобы его
    решения оставались согласованы с тем, что сделает цикл.
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
            # Взведение в цикле разрешено только при нулевых счётчиках -
            # тот же гейт здесь, так что этот сегмент не расходует вызовы RNG.
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
    """Переписывает `audio_path` (на месте) так, чтобы каждое событие stutter
    было слышимым: внутри окна дрели аудио зацикливает тот же кусок, что и видео.

    Раскладка региона на событие (сэмплы считаются в частоте самого WAV):
      • источник LOOP = аудио кадров
        `[trigger_fi - loop_size_frames + 1, trigger_fi + 1)`.
        Это последние `loop_size` исходных кадров, включая текущий
        ("первый слот" дрели).
      • цель REPLACE = `[trigger_fi + 1, trigger_fi + 1 + total_replace)`.
        Текущий кадр играет как обычно; перезаписываются копиями LOOP
        только остаточные слоты.
    Возвращает False при неподдерживаемой ширине сэмпла или ошибках
    модуля wave - вызывающий код должен воспринимать это как "аудио-дрель
    пропущена, видео-дрель всё равно проигрывается", а не как фатальную ошибку.
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
