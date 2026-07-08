"""Каталог энкодеров + определение доступности + маппинг rate-control.

Единственный источник истины о том, какие видеоэнкодеры мы поддерживаем,
какие из них реально есть в локальной сборке ffmpeg, и как пользовательские
настройки качества (CRF / ffmpeg preset / tune) превращаются в специфичные
для каждого семейства энкодеров флаги.

Почему это отдельный модуль:
  * `EXPORT_FORMATS` в `sink.py` был жёстко завязан на набор софтверных
    кодеков. Добавление флагов NVENC/QSV/AMF в тот же словарь смешало бы
    контейнер, энкодер и rate control в одну кучу.
  * Аппаратные энкодеры опциональны - они появляются в GUI только если
    локальная сборка ffmpeg собрана с их поддержкой И рантайм-проверка
    прошла успешно (сама проверка рантайма - в engine.py, через fallback
    при первой неудачной записи, не здесь).

Контракт модуля:
  * `available_specs()` возвращает список `EncoderSpec`, отфильтрованный
    по тому, что реально показывает `ffmpeg -encoders`. Результат
    кэшируется на весь процесс.
  * `build_rate_control_args(spec, crf, preset, tune)` возвращает список
    CLI-флагов, которые добавляются после `-vcodec <vcodec> -pix_fmt <pix_fmt>`.
    Это единственное место, которое знает про особенности каждого семейства.
  * Софтверные кодеки (libx264/libx265/libvpx-vp9/prores_ks) присутствуют
    в списке всегда - это чисто программный fallback-путь.

Маппинг rate-control по семействам (вход - CRF пользователя 0-51 + ffmpeg
preset + tune):

    libx264 / libx265
        -preset <p> -crf <n> [-tune <t>]
        Прямая передача без изменений - каноническая трактовка.

    h264_nvenc / hevc_nvenc
        -preset p1..p7 -rc vbr -cq <n> -b:v 0
        В свежих версиях ffmpeg `-preset` у NVENC переименован из старых
        slow/medium/fast в p1..p7, поэтому мапим имена x264 на p-индексы.
        `-cq` принимает ту же шкалу 0-51, что и CRF; `-b:v 0` держит режим
        constant-quality. `-tune` молча игнорируется (у NVENC есть свой
        `-tune ll/ull/hq`, но мы его тут не выставляем).

    h264_qsv / hevc_qsv
        -preset <veryfast..veryslow> -global_quality <n>
        Имена пресетов QSV совпадают с x264, поэтому передаём как есть.
        ICQ rate control включается неявно при заданном -global_quality.

    h264_amf / hevc_amf
        -quality <speed|balanced|quality> -rc cqp -qp_i <n> -qp_p <n>
        У AMF нет аналога CRF - ближайшее приближение - constant QP.

    h264_videotoolbox / hevc_videotoolbox
        -q:v <map(crf)>
        VideoToolbox использует шкалу качества 1-100 (чем больше, тем
        лучше) - обратное направление относительно CRF. Мапим crf=18 → q=64
        и так далее.

    libvpx-vp9
        -crf <n> -b:v 0 -deadline good -cpu-used 4
        Та же шкала CRF, но другая обвязка rate control.

    prores_ks
        -profile:v 3
        ProRes 422 HQ; CRF/preset/tune полностью игнорируются - ProRes
        внутрикадровый, с фиксированным качеством для каждого профиля.

Добавление нового энкодера:
  1. Добавить EncoderSpec в ENCODER_TABLE.
  2. Если флаги семейства не совпадают ни с одним существующим - добавить
     ветку в build_rate_control_args().
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

from .sink import ffmpeg_bin


# ----- спецификация -----

@dataclass(frozen=True)
class EncoderSpec:
    label: str            # для пользователя, напр. 'H.264 NVENC (MP4)'
    container_ext: str    # 'mp4'
    vcodec: str           # 'h264_nvenc'
    acodec: str           # 'aac'
    pix_fmt: str          # 'yuv420p'
    family: str           # 'x264' | 'nvenc_h264' | 'qsv_h264' | ...
    is_hw: bool
    extra_v: List[str] = field(default_factory=list)


# ----- каталог -----
#
# Порядок важен: именно в этом порядке пункты появляются в выпадающем
# списке GUI, поэтому софтверные кодеки идут первыми (безопасный вариант
# по умолчанию), аппаратные - ниже.

ENCODER_TABLE: List[EncoderSpec] = [
    # ----- софтверные (доступны всегда, есть в любой сборке ffmpeg) -----
    EncoderSpec('H.264 (MP4)',  'mp4',  'libx264',    'aac',
                'yuv420p', 'x264', False),
    EncoderSpec('H.265 (MP4)',  'mp4',  'libx265',    'aac',
                'yuv420p', 'x265', False, ['-tag:v', 'hvc1']),
    EncoderSpec('H.264 (MKV)',  'mkv',  'libx264',    'aac',
                'yuv420p', 'x264', False),
    EncoderSpec('H.265 (MKV)',  'mkv',  'libx265',    'aac',
                'yuv420p', 'x265', False),
    EncoderSpec('H.264 (MOV)',  'mov',  'libx264',    'aac',
                'yuv420p', 'x264', False),
    EncoderSpec('ProRes (MOV)', 'mov',  'prores_ks',  'pcm_s16le',
                'yuv422p10le', 'prores', False, ['-profile:v', '3']),
    EncoderSpec('VP9 (WebM)',   'webm', 'libvpx-vp9', 'libopus',
                'yuv420p', 'vp9', False, ['-row-mt', '1', '-b:v', '0']),

    # ----- NVIDIA NVENC -----
    EncoderSpec('H.264 NVENC (MP4)', 'mp4', 'h264_nvenc', 'aac',
                'yuv420p', 'nvenc_h264', True),
    EncoderSpec('H.265 NVENC (MP4)', 'mp4', 'hevc_nvenc', 'aac',
                'yuv420p', 'nvenc_hevc', True, ['-tag:v', 'hvc1']),

    # ----- Intel Quick Sync -----
    EncoderSpec('H.264 QSV (MP4)', 'mp4', 'h264_qsv', 'aac',
                'yuv420p', 'qsv_h264', True),
    EncoderSpec('H.265 QSV (MP4)', 'mp4', 'hevc_qsv', 'aac',
                'yuv420p', 'qsv_hevc', True, ['-tag:v', 'hvc1']),

    # ----- AMD AMF -----
    EncoderSpec('H.264 AMF (MP4)', 'mp4', 'h264_amf', 'aac',
                'yuv420p', 'amf_h264', True),
    EncoderSpec('H.265 AMF (MP4)', 'mp4', 'hevc_amf', 'aac',
                'yuv420p', 'amf_hevc', True, ['-tag:v', 'hvc1']),

    # ----- Apple VideoToolbox (macOS) -----
    EncoderSpec('H.264 VideoToolbox (MP4)', 'mp4', 'h264_videotoolbox',
                'aac', 'yuv420p', 'vt_h264', True),
    EncoderSpec('H.265 VideoToolbox (MP4)', 'mp4', 'hevc_videotoolbox',
                'aac', 'yuv420p', 'vt_hevc', True, ['-tag:v', 'hvc1']),
]


# ----- определение доступности -----

_AVAILABLE_VCODECS_CACHE: Optional[set] = None


def _probe_vcodecs() -> set:
    """Один раз запускает `ffmpeg -encoders` и возвращает набор имён
    vcodec из вывода. При неудаче (ffmpeg не найден, ошибка парсинга)
    возвращает минимальный набор софтверных кодеков, чтобы GUI хотя бы
    работал."""
    soft_floor = {'libx264', 'libx265', 'libvpx-vp9', 'prores_ks'}
    try:
        r = subprocess.run([ffmpeg_bin(), '-hide_banner', '-encoders'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return soft_floor
        names = set()
        # У таблицы ffmpeg колоночный формат; имя энкодера - второй токен
        # после блока флагов, например:
        #   "V....D h264_nvenc           NVIDIA NVENC ..."
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith('V'):
                names.add(parts[1])
        return names | soft_floor  # минимальный набор добавляем всегда
    except (FileNotFoundError, subprocess.SubprocessError):
        return soft_floor


def available_specs() -> List[EncoderSpec]:
    """Кэшированный список EncoderSpec, поддерживаемых данной сборкой ffmpeg."""
    global _AVAILABLE_VCODECS_CACHE
    if _AVAILABLE_VCODECS_CACHE is None:
        _AVAILABLE_VCODECS_CACHE = _probe_vcodecs()
    avail = _AVAILABLE_VCODECS_CACHE
    return [s for s in ENCODER_TABLE if s.vcodec in avail]


def find_spec(label: str) -> Optional[EncoderSpec]:
    """Поиск по пользовательской метке, например 'H.264 NVENC (MP4)'."""
    for s in ENCODER_TABLE:
        if s.label == label:
            return s
    return None


def fallback_spec() -> EncoderSpec:
    """Энкодер, на который откатываемся, если аппаратный падает в рантайме."""
    s = find_spec('H.264 (MP4)')
    assert s is not None  # всегда есть в статической таблице
    return s


# ----- рантайм-проверка аппаратных энкодеров -----
#
# `available_specs()` отвечает на вопрос "энкодер заявлен в сборке?",
# парся `ffmpeg -encoders`. Этого недостаточно: NVENC / QSV / AMF /
# VideoToolbox могут быть заявлены и при этом зависать или падать при
# реальном использовании (нет драйвера, GPU занят, урезанная VM, битая
# сборка ffmpeg). Симптом для пользователя - рендер вечно висит на 0%,
# потому что GPU проглотил наш pipe, но не отдаёт закодированные кадры.
#
# Чтобы это было безопасно для EXE-сборки (где пользователь не может
# запустить диагностику руками), движок перед использованием проверяет
# каждый аппаратный энкодер: запускает пайплайн `testsrc → encoder →
# temp file` длиной в 1 секунду с жёстким таймаутом. Если получилось -
# кэшируем успех и используем энкодер как обычно. Если завис или упал -
# кэшируем неудачу и молча откатываемся на libx264. Кэш живёт в рамках
# процесса, так что проверка стоит ~1-2 секунды ровно один раз за сессию
# на каждый аппаратный энкодер.

_PROBE_CACHE: dict = {}      # vcodec -> bool
_PROBE_LAST_ERROR: dict = {}  # vcodec -> str


def probe_encoder(spec: EncoderSpec, *, timeout: float = 8.0) -> bool:
    """True, если `spec` реально даёт валидный файл на этой машине.

    Софтверные кодеки (libx264 / libx265 / libvpx-vp9 / prores_ks)
    доверяются без проверки - они чисто CPU, есть в любой сборке ffmpeg,
    и запуск проверки для них просто тратит ~1 секунду на каждый рендер.
    Через рантайм-проверку проходят только аппаратные энкодеры.

    Проверка нарочно лёгкая: 720p @ 24fps на 1 секунду (24 кадра), через
    `-f lavfi testsrc`, чтобы не зависеть от файлов пользователя. Вывод
    идёт во временный .mp4, который удаляется в любом случае.
    """
    if not spec.is_hw:
        return True
    if spec.vcodec in _PROBE_CACHE:
        return _PROBE_CACHE[spec.vcodec]

    cmd = [
        ffmpeg_bin(), '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc=duration=1:size=1280x720:rate=24',
        '-c:v', spec.vcodec, '-pix_fmt', spec.pix_fmt,
    ]
    cmd += build_rate_control_args(spec, crf=22, preset='fast', tune='none')
    if spec.extra_v:
        cmd += list(spec.extra_v)
    fd, out = tempfile.mkstemp(suffix='.' + spec.container_ext,
                               prefix='vpc_hwprobe_')
    os.close(fd)
    cmd += ['-t', '1', out]

    ok = False
    err = ''
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        ok = (r.returncode == 0
              and os.path.exists(out)
              and os.path.getsize(out) > 1000)
        if not ok:
            tail = (r.stderr or b'').decode(errors='replace').strip()
            err = tail.splitlines()[-1] if tail else f'rc={r.returncode}'
    except subprocess.TimeoutExpired:
        err = f'timed out after {timeout:.0f}s — encoder hung'
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
    finally:
        try: os.remove(out)
        except OSError: pass

    _PROBE_CACHE[spec.vcodec] = ok
    _PROBE_LAST_ERROR[spec.vcodec] = err
    return ok


def last_probe_error(vcodec: str) -> str:
    """Причина неудачи последней проверки (для логов). '' если неизвестна."""
    return _PROBE_LAST_ERROR.get(vcodec, '')


# ----- маппинг rate-control -----

# Имена пресетов x264 → пресеты NVENC. p1=самый быстрый..p7=самый медленный.
_NVENC_PRESET_MAP = {
    'ultrafast': 'p1', 'superfast': 'p2', 'veryfast': 'p2',
    'faster': 'p3',    'fast': 'p3',
    'medium': 'p4',
    'slow': 'p6',      'slower': 'p7',     'veryslow': 'p7',
}

# Пресет x264 → AMF -quality. У AMF всего 3 уровня.
_AMF_QUALITY_MAP = {
    'ultrafast': 'speed', 'superfast': 'speed', 'veryfast': 'speed',
    'faster': 'speed',    'fast': 'speed',
    'medium': 'balanced',
    'slow': 'quality',    'slower': 'quality', 'veryslow': 'quality',
}


def _vt_quality_from_crf(crf: int) -> int:
    """Переводит нашу шкалу CRF 0-51 в шкалу качества VideoToolbox 1-100.
    Чем больше q, тем лучше качество - направление обратное CRF."""
    crf = max(0, min(51, int(crf)))
    return max(1, 100 - 2 * crf)


def build_rate_control_args(spec: EncoderSpec, *, crf: int, preset: str,
                            tune: Optional[str]) -> List[str]:
    """Возвращает флаги rate control под конкретный энкодер для заданных
    параметров качества.

    Всегда отдаёт что-то осмысленное (по умолчанию constant-quality),
    чтобы рендер никогда не шёл со случайными дефолтами энкодера.
    """
    fam = spec.family
    crf = int(crf)
    preset = str(preset or 'medium')
    tune_clean = (str(tune).lower() if tune else 'none')

    if fam in ('x264', 'x265'):
        args = ['-preset', preset, '-crf', str(crf)]
        if tune_clean and tune_clean != 'none':
            args += ['-tune', tune_clean]
        return args

    if fam.startswith('nvenc'):
        p = _NVENC_PRESET_MAP.get(preset, 'p4')
        # -rc vbr + -cq <n> + -b:v 0 = constant-quality VBR, аналог
        # CRF-режима libx264 для этого семейства.
        return ['-preset', p, '-rc', 'vbr', '-cq', str(crf), '-b:v', '0']

    if fam.startswith('qsv'):
        # Имена пресетов QSV пересекаются с x264, передаём как есть -
        # неизвестные значения ffmpeg отклонит сам (у нас таких нет).
        return ['-preset', preset, '-global_quality', str(crf)]

    if fam.startswith('amf'):
        q = _AMF_QUALITY_MAP.get(preset, 'balanced')
        return ['-quality', q, '-rc', 'cqp',
                '-qp_i', str(crf), '-qp_p', str(crf)]

    if fam.startswith('vt'):  # videotoolbox
        return ['-q:v', str(_vt_quality_from_crf(crf))]

    if fam == 'vp9':
        return ['-crf', str(crf), '-deadline', 'good', '-cpu-used', '4']

    if fam == 'prores':
        # Качество зафиксировано через -profile:v в extra_v, добавлять нечего.
        return []

    # Для незнакомых семейств отдаём пустой список - рендер пойдёт на
    # дефолтах энкодера, а не упадёт из-за отсутствующей ветки.
    return []
