"""Типизированная конфигурация рендера.

Оборачивает старый плоский cfg-словарь типизированными аксессорами и
валидацией. Сам словарь остаётся источником истины, чтобы старые пресеты
продолжали работать - этот класс просто добавляет структуру поверх него.

resolution_mode + custom_w / custom_h: разрешение может быть пресетом
(240/360/480/720/1080), совпадать с исходным видео или задаваться
произвольными размерами вручную.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


RENDER_DRAFT = 'draft'
RENDER_PREVIEW = 'preview'
RENDER_FINAL = 'final'

_RES_MAP = {
    '240p': (426, 240), '360p': (640, 360), '480p': (854, 480),
    '720p': (1280, 720), '1080p': (1920, 1080),
}


def _coerce_paths(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


@dataclass
class RenderConfig:
    """Типизированное представление плоского cfg-словаря."""
    raw: dict = field(default_factory=dict)

    # ----- пути к файлам -----
    @property
    def video_paths(self) -> List[str]:
        return _coerce_paths(self.raw.get('video_paths') or self.raw.get('video_path'))

    @property
    def audio_path(self) -> str:
        return self.raw.get('audio_path', '')

    @property
    def output_path(self) -> str:
        return self.raw.get('output_path', '')

    @property
    def overlay_dir(self) -> str:
        return self.raw.get('overlay_dir', '') or ''

    # ----- разрешение и частота кадров -----
    def output_size(self, mode: str, source_size: Optional[Tuple[int, int]] = None) -> Tuple[int, int]:
        """Итоговый (w, h) для рендера. В draft-режиме отдаёт фиксированный размер.

        Ширина и высота всегда приводятся к чётным числам - yuv420p (и
        prores 422) требуют чётных размеров для chroma subsampling, иначе
        ffmpeg просто откажется стартовать. Без этого нечётный
        custom_w/custom_h или исходное видео с нечётным размером ронял бы
        рендер прямо на стадии ffmpeg pipe.
        """
        def _even(w: int, h: int) -> Tuple[int, int]:
            return max(2, w - (w & 1)), max(2, h - (h & 1))

        if mode == RENDER_DRAFT:
            return 480, 270
        rmode = self.raw.get('resolution_mode', 'preset')
        if rmode == 'source' and source_size is not None:
            return _even(int(source_size[0]), int(source_size[1]))
        if rmode == 'custom':
            w = int(self.raw.get('custom_w', 1280) or 1280)
            h = int(self.raw.get('custom_h', 720) or 720)
            return _even(w, h)
        w, h = _RES_MAP.get(self.raw.get('resolution', '720p'), (1280, 720))
        return _even(w, h)

    def fps(self, mode: str) -> int:
        if mode == RENDER_DRAFT:
            return 24
        return int(self.raw.get('fps', 24) or 24)

    def encoder_preset(self, mode: str) -> str:
        if mode == RENDER_DRAFT:
            return 'ultrafast'
        return self.raw.get('export_preset', 'medium')

    def crf(self, mode: str) -> int:
        if mode == RENDER_DRAFT:
            return 28
        # Дефолт 22, а не 18: 18 визуально безлосси для чистого контента,
        # но при тяжёлых цепочках эффектов артефакты и так забивают детали,
        # так что 18 только раздувает файл (примерно в 2 раза) без заметной
        # разницы в качестве. 22 - стандартный x264-ный CRF "good quality web".
        return int(self.raw.get('crf', 22) or 22)

    @property
    def use_h265(self) -> bool:
        return 'H.265' in self.raw.get('video_codec', 'H.264')

    @property
    def tune(self) -> str:
        """Значение -tune для libx264/libx265. 'none' (или пусто) - не передавать флаг.

        Хранится плоской строкой, потому что и пресеты качества, и GUI
        трактуют её как одно из {'none', 'film', 'grain', 'animation',
        'stillimage'}. Канонический список - в vpc.render.quality.normalize_tune.
        """
        v = self.raw.get('tune')
        if v is None:
            return 'none'
        s = str(v).strip().lower()
        return s if s else 'none'

    @property
    def quality_preset(self) -> str:
        """Метка пресета качества ('Archive'/'High'/'Web'/'Compact'/'Custom').

        Чисто информационное поле - реальные флаги энкодера берутся из
        crf/export_preset/tune, пресет их только проставляет. Метка
        хранится в cfg, чтобы при загрузке сохранённого пресета в
        выпадающем списке снова выбирался тот же вариант."""
        v = self.raw.get('quality_preset')
        return str(v) if v else 'Custom'

    @property
    def video_codec_label(self) -> str:
        """Метка кодека/контейнера для пользователя, например 'H.264 (MP4)'.

        Ищется в EXPORT_FORMATS (sink.py) для получения реальной тройки
        ffmpeg codec/container/pix_fmt.
        """
        return self.raw.get('video_codec', 'H.264 (MP4)')

    # ----- параметры анализа аудио -----
    @property
    def chaos(self) -> float:
        return float(self.raw.get('chaos_level', 0.5))

    @property
    def loud_thresh(self) -> float:
        return float(self.raw.get('threshold', 1.2))

    @property
    def transient_thresh(self) -> float:
        return float(self.raw.get('transient_thresh', 0.5))

    @property
    def min_segment_dur(self) -> float:
        return float(self.raw.get('min_cut_duration', 0.05))

    @property
    def snap_to_beat(self) -> bool:
        return bool(self.raw.get('snap_to_beat', False))

    @property
    def snap_tolerance(self) -> float:
        return float(self.raw.get('snap_tolerance', 0.05))

    @property
    def manual_bpm(self) -> float:
        return float(self.raw.get('manual_bpm', 0.0) or 0.0)

    @property
    def use_manual_bpm(self) -> bool:
        return bool(self.raw.get('use_manual_bpm', False))

    # ----- окно отрывка (превью/экспорт) -----
    @property
    def preview_start(self) -> float:
        """Начало окна превью/экспорта в секундах (0 = с начала)."""
        try:
            return max(0.0, float(self.raw.get('preview_start', 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @property
    def preview_end(self):
        """Конец окна в секундах, либо None (до конца). Пусто/мусор -> None."""
        v = self.raw.get('preview_end', None)
        if v in (None, ''):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # ----- passthrough режим -----
    @property
    def passthrough_mode(self) -> bool:
        """Обработка исходного видео 1:1 - без нарезки, без ресемплинга, в исходном порядке.

        Аудио извлекается из самого исходного видео и используется и для
        анализа (триггеры эффектов), и как итоговая звуковая дорожка.
        Внешний аудиофайл не требуется.
        """
        return bool(self.raw.get('passthrough_mode', False))

    # ----- детекция сцен -----
    @property
    def use_scene_detect(self) -> bool:
        return bool(self.raw.get('use_scene_detect', False))

    @property
    def scene_buffer_size(self) -> int:
        return int(self.raw.get('scene_buffer_size', 10) or 10)

    # ----- обработка тишины -----
    @property
    def silence_mode(self) -> str:
        return self.raw.get('silence_mode', 'dim')

    # ----- mystery -----
    @property
    def mystery(self) -> dict:
        return dict(self.raw.get('mystery', {}))

    @property
    def mystery_always(self) -> dict:
        """Флаги always-on для отдельных ручек (обход гейта). По умолчанию
        пусто - все выключены, как и было в старых пресетах до этого поля."""
        return dict(self.raw.get('mystery_always', {}))

    # ----- спецэффекты -----
    @property
    def stutter_enabled(self) -> bool:
        return bool(self.raw.get('fx_stutter', False))

    @property
    def flash_enabled(self) -> bool:
        return bool(self.raw.get('fx_flash', False))

    @property
    def flash_chance_base(self) -> float:
        return float(self.raw.get('fx_flash_chance', 0.5))

    @property
    def datamosh_enabled(self) -> bool:
        return bool(self.raw.get('fx_datamosh', False))

    @property
    def datamosh_chance_base(self) -> float:
        return float(self.raw.get('fx_datamosh_chance', 0.5))

    # ----- валидация -----
    def validate(self) -> List[str]:
        """Возвращает список проблем в читаемом виде (пустой список, если всё ОК)."""
        errors = []
        # В passthrough-режиме звук берётся из самого исходного видео,
        # поэтому отдельный audio_path не нужен.
        if not self.audio_path and not self.passthrough_mode:
            errors.append('audio_path missing')
        if not self.video_paths:
            errors.append('video_paths missing')
        if not self.output_path:
            errors.append('output_path missing')
        rmode = self.raw.get('resolution_mode', 'preset')
        if rmode == 'custom':
            try:
                int(self.raw.get('custom_w', 0))
                int(self.raw.get('custom_h', 0))
            except (TypeError, ValueError):
                errors.append('custom_w / custom_h must be integers')
        return errors
