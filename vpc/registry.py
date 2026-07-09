"""Декларативный реестр эффектов.

Единый источник истины по каждому эффекту: id, параметры, дефолты, диапазоны,
типы триггеров, подписи и тултипы для GUI. Движок, GUI и валидация конфига
читают из этого списка - чтобы добавить эффект, достаточно одной записи
EffectSpec.

Публичный API:
    EFFECTS              - list[EffectSpec], все зарегистрированные эффекты
    GROUP_ORDER           - list[str], порядок групп в GUI
    find_spec(effect_id) - поиск спека по id
    build_chain(cfg)     - собрать список[BaseEffect] из плоского cfg-словаря
    iter_cfg_keys()      - перебрать все ключи cfg, которые ожидает реестр
    default_cfg()        - плоский словарь дефолтов, готовый для GUI

Ключи cfg (fx_xxx, fx_xxx_chance, ...) совпадают с теми, что были в старом
плоском движке, поэтому старые пресеты по-прежнему загружаются.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Tuple

from vpc.analyzer import SegmentType
from .effects.base import BaseEffect
from .effects import (
    core, glitch, degradation, complex_fx, signal, warp, overlay, formula,
    mosh, vhs as vhs_fx, broken as broken_fx, virus as virus_fx,
)
from .effects.paint import PaintCanvasEffect
from .effects.subtitles import SubtitleEffect
from .effects.visualizer import (
    SpectrumBarsEffect, RadialSpectrumEffect, OscilloscopeEffect,
    LissajousEffect, PlasmaFieldEffect, BeatParticlesEffect, FlowFieldEffect,
    AlchemyEffect,
)


# ──────────────────────────────────────────────────────────────────────────
#   Спеки параметров и эффектов
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class ParamSpec:
    """Описание одного настраиваемого параметра эффекта."""
    key: str                       # ключ в cfg-словаре, напр. 'fx_psort_int'
    label: str                     # подпись в GUI, напр. 'Pixel Sort Intensity'
    default: Any
    lo: float = 0.0                # мин. значения слайдера
    hi: float = 1.0                # макс. значения слайдера
    kind: str = 'float'            # 'float' | 'int' | 'choice' | 'rgb' | 'string'
    choices: Optional[List[str]] = None      # варианты для kind='choice'
    kwarg: Optional[str] = None    # имя kwarg конструктора (None = в конструктор не передаётся)
    indent: bool = True            # GUI: с отступом под главным чекбоксом
    tooltip: str = ''              # короткое описание, как параметр влияет на результат


@dataclass
class EffectSpec:
    """Описание одного эффекта - источник истины для движка и GUI."""
    id: str                                  # стабильный id, напр. 'pixel_sort'
    label: str                               # отображаемое имя в GUI
    group: str                               # группа в GUI, напр. 'CORE FX'
    cls: Optional[type] = None               # класс эффекта (None у "особых" записей)
    enable_key: str = ''                     # ключ флага включения в cfg, напр. 'fx_psort'
    enabled_default: bool = False
    chance_key: Optional[str] = None         # ключ chance в cfg; None = always-on (chance=1.0)
    default_chance: float = 0.5
    params: List[ParamSpec] = field(default_factory=list)
    trigger_types: Optional[List[SegmentType]] = None  # None → использовать дефолт класса
    note: str = ''                           # короткая пометка в GUI
    tooltip: str = ''                        # длинное описание для hover/[?] попапа
    chance_scaled_by_chaos: bool = True      # применять к chance масштабирование от _ch()
    chain_kind: str = 'normal'               # 'normal' (идёт в цепочку) | 'special' (обрабатывает движок) | 'mystery'
    requires_overlay_dir: bool = False
    extra_factory: Optional[Callable[[dict], dict]] = None
    # extra_factory(cfg) возвращает дополнительные kwargs (например overlay_frames, chroma_key)
    intensity_max_kwarg: Optional[str] = None  # если параметр вида fx_xxx_int маппится на intensity_max
    supports_always: bool = True             # применим ли для эффекта override "always-on"

    # ── ключи cfg для always-on (выводятся из enable_key) ──
    @property
    def always_key(self) -> str:
        """Ключ в cfg для персонального флага always-on (напр. 'fx_psort_always')."""
        return self.enable_key + '_always' if self.enable_key else ''

    @property
    def always_int_key(self) -> str:
        """Ключ в cfg для фиксированной интенсивности при активном always-on."""
        return self.enable_key + '_always_int' if self.enable_key else ''

    def supports_always_for_chain(self) -> bool:
        """True, если override always-on вообще имеет смысл для этого спека."""
        return self.supports_always and self.chain_kind == 'normal' and self.cls is not None

    def all_keys(self) -> List[str]:
        keys = []
        if self.enable_key:
            keys.append(self.enable_key)
        if self.chance_key:
            keys.append(self.chance_key)
        for p in self.params:
            keys.append(p.key)
        if self.supports_always_for_chain():
            keys.extend([self.always_key, self.always_int_key])
        return keys

    def build_kwargs(self, cfg: dict) -> dict:
        """Собрать kwargs конструктора из плоского cfg-словаря."""
        kw: dict = {}
        for p in self.params:
            if p.kwarg is None:
                continue
            val = cfg.get(p.key, p.default)
            if p.kind == 'int':
                kw[p.kwarg] = int(val)
            elif p.kind == 'rgb':
                kw[p.kwarg] = tuple(int(x) for x in val)
            else:
                kw[p.kwarg] = val
        if self.extra_factory is not None:
            kw.update(self.extra_factory(cfg))
        return kw


# ──────────────────────────────────────────────────────────────────────────
#   Порядок групп в GUI
# ──────────────────────────────────────────────────────────────────────────

GROUP_ORDER: List[str] = [
    'CUT LOGIC',
    'CORE FX',
    'GLITCH',
    'DEGRADATION',
    'COMPLEX',
    'SIGNAL DOMAIN',
    'WARP',
    'BROKEN',            # семейство "сломанный декодер"/повреждение памяти
    'VIRUS',             # эстетика Win95-вируса
    'PAINT',
    'VISUALIZER',        # аудио-реактивные визуализаторы в духе WMP
    'OVERLAYS',
    'FORMULA',           # отдельная вкладка, не часть аккордеона
]


# Отображаемые названия групп эффектов. Внутренние ключи групп (выше и в
# каждом EffectSpec.group) остаются неизменными - на них завязана логика
# группировки, фильтр скрытых групп в аккордеоне и переходы в навбаре, а вот
# эти строки - то, что реально видит пользователь в GUI. Пресеты никогда не
# ссылаются на группы (они привязаны к ключам fx_* enable), так что
# переименование здесь ничего не сломает.
#
# Названия читаются слева направо как цепочка деградации сигнала:
#   SOURCE FEED → RASTER FAULT → TAPE ROT → FEEDBACK BUS → DSP KERNEL →
#   GEOMETRY FAULT → CODEC ROT → MALWARE → (PAINT) → (OVERLAYS)
# с общим словарём: FAULT = внезапный глитч, ROT = постепенный распад.
# Группы, которых нет в этом словаре (CUT LOGIC, OVERLAYS, FORMULA),
# отображаются под своим исходным именем.
GROUP_DISPLAY_NAMES: dict = {
    'CORE FX': 'SOURCE FEED',
    'GLITCH': 'RASTER FAULT',
    'DEGRADATION': 'TAPE ROT',
    'COMPLEX': 'FEEDBACK BUS',
    'SIGNAL DOMAIN': 'DSP KERNEL',
    'WARP': 'GEOMETRY FAULT',
    'BROKEN': 'CODEC ROT',
    'VIRUS': 'MALWARE',
    'PAINT': 'PAINT CANVAS FX',
    'VISUALIZER': 'WINDOWS MEDIA PLAYER',
}


# Группы, которые аккордеон эффектов не рисует (у них своя отдельная вкладка).
ACCORDION_HIDDEN_GROUPS = {'FORMULA'}


# ──────────────────────────────────────────────────────────────────────────
#   Хелперы для рутинных паттернов chance/int параметров
# ──────────────────────────────────────────────────────────────────────────


def _chance_scale(chaos: float, base: float) -> float:
    """Mirror the original engine's chaos-chance formula."""
    return min(1.0, base * (0.3 + 0.7 * float(chaos)))


def bi(en: str, ru: str) -> str:
    """Собрать двуязычный tooltip. Сначала EN, после разделителя - RU.

    Разделитель нужен на будущее - если в GUI появится переключатель языка,
    строку можно будет разрезать по нему. Пока же обе версии показываются
    в одном и том же всплывающем окне.
    """
    return f'{en}\n──\n{ru}'


# ──────────────────────────────────────────────────────────────────────────
#   Фабрика доп. параметров оверлея - нужны кадры оверлея и ChromaKey из cfg
# ──────────────────────────────────────────────────────────────────────────


def _dvd_extras(cfg: dict) -> dict:
    from .effects.virus import _load_logo
    rgb, alpha = _load_logo(cfg.get('fx_dvd_logo_path', '') or '')
    return dict(logo_rgb=rgb, logo_alpha=alpha)


def _overlay_extras(cfg: dict) -> dict:
    from .effects.overlay import ChromaKeyEffect
    overlay_dir = cfg.get('overlay_dir')
    overlay_frames = []
    if overlay_dir:
        from .effects.overlay import load_overlay_frames
        overlay_frames = load_overlay_frames(overlay_dir)
    ck_mode = cfg.get('fx_overlay_ck_mode', 'none')
    ck_tol = int(cfg.get('fx_overlay_ck_tolerance', 30))
    ck_soft = int(cfg.get('fx_overlay_ck_softness', 5))
    manual_ck = None
    if ck_mode == 'manual':
        ck_color = cfg.get('fx_overlay_ck_color', [0, 255, 0])
        manual_ck = ChromaKeyEffect(
            key_color=tuple(int(v) for v in ck_color),
            tolerance=ck_tol, edge_softness=ck_soft,
        )
    return dict(
        overlay_frames=overlay_frames,
        opacity=float(cfg.get('fx_overlay_opacity', 0.85)),
        blend_mode=cfg.get('fx_overlay_blend', 'screen'),
        scale=float(cfg.get('fx_overlay_scale', 0.4)),
        scale_min=float(cfg.get('fx_overlay_scale_min', 0.15)),
        position=cfg.get('fx_overlay_position', 'random'),
        chroma_mode=ck_mode,
        chroma_tolerance=ck_tol,
        chroma_softness=ck_soft,
        chroma_key=manual_ck,
    )


def _ascii_extras(cfg: dict) -> dict:
    fg = cfg.get('fx_ascii_fg', [0, 255, 0])
    bg = cfg.get('fx_ascii_bg', [0, 0, 0])
    return dict(
        fg_color=tuple(int(v) for v in fg),
        bg_color=tuple(int(v) for v in bg),
        color_mode=cfg.get('fx_ascii_color_mode', 'fixed'),
    )


def _psort_extras(cfg: dict) -> dict:
    return dict(
        sort_axis=cfg.get('fx_psort_axis', 'luminance'),
        sort_mode=cfg.get('fx_psort_mode', 'block'),
        sort_direction=cfg.get('fx_psort_direction', 'horizontal'),
        sort_threshold=float(cfg.get('fx_psort_threshold', 0.3)),
    )


def _formula_extras(cfg: dict) -> dict:
    return dict(
        expression=cfg.get('fx_formula_expr', 'frame'),
        a=float(cfg.get('fx_formula_a', 0.5)),
        b=float(cfg.get('fx_formula_b', 0.5)),
        c=float(cfg.get('fx_formula_c', 0.5)),
        d=float(cfg.get('fx_formula_d', 0.5)),
    )


def _drive_param(ek: str, recommend: str = '') -> 'ParamSpec':
    """Opt-in Audio Drive selector for an existing effect. Default 'segment'
    reproduces today's behaviour, so it's fully preset-safe."""
    rec_en = f' Recommended for this effect: {recommend}.' if recommend else ''
    rec_ru = f' Рекомендуется для этого эффекта: {recommend}.' if recommend else ''
    return ParamSpec(
        f'{ek}_drive', 'Audio Drive', 'segment', kind='choice',
        choices=['segment', 'auto', 'bass', 'mid', 'high'], indent=True, kwarg=None,
        tooltip=bi(
            'What drives this effect\'s intensity per frame: segment (overall '
            'loudness, the default), auto (loudest of bass/mid/high - always '
            'reacts on any track), or a specific band.' + rec_en,
            'Что покадрово задаёт интенсивность эффекта: segment (общая громкость, '
            'по умолчанию), auto (самая громкая из bass/mid/high - реагирует на '
            'любом треке) или конкретная полоса.' + rec_ru))


def _gate_param(ek: str) -> 'ParamSpec':
    """Opt-in intra-segment Beat Gate. Default 'off' is preset-safe. Only
    meaningful for effects that fire every frame across a held segment."""
    return ParamSpec(
        f'{ek}_gate', 'Beat Gate', 'off', kind='choice',
        choices=['off', 'beat', 'onset'], indent=True, kwarg=None,
        tooltip=bi(
            'Fire only on a per-frame beat/onset INSIDE a segment: off (default), '
            'beat (locked to detected beats), onset (any transient). Segment cuts '
            'already sit on onsets, so this only adds pulses within long segments.',
            'Срабатывать только на покадровом бите/онсете ВНУТРИ сегмента: off (по '
            'умолчанию), beat (по детектированным битам), onset (любой транзиент). '
            'Нарезка и так идёт по онсетам - это добавляет пульс лишь внутри длинных '
            'сегментов.'))


def _react_param(ek: str, what: str, what_ru: str) -> 'ParamSpec':
    """Opt-in bespoke audio-reactivity toggle. Default 'off' is preset-safe."""
    return ParamSpec(
        f'{ek}_react', 'Audio React', 'off', kind='choice',
        choices=['off', 'on'], indent=True, kwarg=None,
        tooltip=bi(
            f'When on, {what} Off (default) keeps the plain, non-reactive behaviour.',
            f'Когда on, {what_ru} Off (по умолчанию) - обычное, нереактивное поведение.'))


def _viz_mode_params(ek: str) -> List['ParamSpec']:
    """Shared composite-mode + opacity params for every visualizer effect.

    Keys are concrete per-effect (`<ek>_mode`, `<ek>_opacity`); both are
    consumed by the effect's extra_factory (kwarg=None here), never passed
    through the generic param→ctor path.
    """
    return [
        ParamSpec(f'{ek}_mode', 'Composite Mode', 'replace', kind='choice',
                  choices=['replace', 'over', 'warp', 'mask'], indent=True, kwarg=None,
                  tooltip=bi(
                      'How the visual meets the source: replace (full-screen), '
                      'over (blend on top), warp (visual brightness displaces the source), '
                      'mask (visual brightness reveals the source against black).',
                      'Как визуал встречает источник: replace (на весь экран), '
                      'over (подмешать сверху), warp (яркость визуала смещает источник), '
                      'mask (яркость визуала проявляет источник на чёрном).')),
        ParamSpec(f'{ek}_opacity', 'Opacity / Amount', 0.85, 0.0, 1.0, indent=True, kwarg=None,
                  tooltip=bi(
                      'Blend strength for over, displacement amount for warp. '
                      'No effect in replace mode.',
                      'Сила смешения для over, величина смещения для warp. '
                      'В режиме replace не действует.')),
    ]


def _viz_extras_base(cfg: dict, ek: str) -> dict:
    return dict(
        mode=cfg.get(f'{ek}_mode', 'replace'),
        opacity=float(cfg.get(f'{ek}_opacity', 0.85)),
    )


def _paint_extras(cfg: dict) -> dict:
    from .effects.paint import decode_paint_canvas
    canvas_data = cfg.get('fx_paint_canvas_data', '')
    mask = decode_paint_canvas(canvas_data)
    return dict(
        canvas_mask=mask,
        mode=cfg.get('fx_paint_mode', 'lag'),
        color_r=int(cfg.get('fx_paint_color_r', 0)),
        color_g=int(cfg.get('fx_paint_color_g', 255)),
        color_b=int(cfg.get('fx_paint_color_b', 0)),
    )


def _subtitles_extras(cfg: dict) -> dict:
    from .effects.subtitles import decode_subtitles
    return dict(cues=decode_subtitles(cfg.get('fx_subtitles_data', '')))


# ──────────────────────────────────────────────────────────────────────────
#   РЕЕСТР - здесь описан каждый настраиваемый эффект
# ──────────────────────────────────────────────────────────────────────────


EFFECTS: List[EffectSpec] = [

    # ── VIRUS: Pipes применяются первыми - остальные эффекты вшиваются поверх ──
    EffectSpec(
        id='win_pipes', label='Win95 Pipes', group='VIRUS',
        trigger_types=[SegmentType.SILENCE, SegmentType.SUSTAIN],
        cls=virus_fx.WinPipesEffect,
        enable_key='fx_win_pipes', enabled_default=False,
        chance_key='fx_win_pipes_chance', default_chance=1.0,
        params=[
            ParamSpec('fx_win_pipes_int', 'Growth', 0.5, 0.0, 1.0,
                      kwarg='growth',
                      tooltip=bi(
                          'How fast the pipes grow and how many run at once. Low: a single '
                          'slow pipe. High: several pipes racing across the frame. Works in '
                          'always-on too; segment loudness only modulates it.',
                          'Как быстро растут трубы и сколько их одновременно. Низко - одна '
                          'медленная труба. Высоко - несколько труб мчатся по кадру. Работает '
                          'и в always; громкость сегмента лишь модулирует.',
                      )),
            ParamSpec('fx_win_pipes_thick', 'Thickness', 10, 4, 24, kind='int',
                      kwarg='thickness',
                      tooltip=bi(
                          'Pipe radius in pixels. Also sets the grid spacing.',
                          'Толщина трубы в пикселях. Заодно задаёт шаг решётки.',
                      )),
            ParamSpec('fx_win_pipes_takeover', 'Takeover', 0.85, 0.0, 1.0, kind='float',
                      kwarg='takeover',
                      tooltip=bi(
                          'How far the video is dimmed to black behind the pipes. 1.0: full '
                          'black-screen screensaver. 0.0: pipes over the untouched video.',
                          'Насколько видео притемняется к чёрному за трубами. 1.0 - полноценный '
                          'чёрный экран скринсейвера. 0.0 - трубы поверх нетронутого видео.',
                      )),
            ParamSpec('fx_win_pipes_speed', 'Speed', 3.0, 1.0, 8.0, kind='float',
                      kwarg='speed',
                      tooltip=bi(
                          'Base growth steps per frame before audio scaling.',
                          'Базовое число шагов роста за кадр до аудио-масштабирования.',
                      )),
        ],
        note='SILENCE / SUSTAIN - pipes slowly reclaim the screen like a real screensaver.',
        tooltip=bi(
            'A pseudo-3D isometric pipeline in the spirit of the Win95 "3D Pipes" saver: '
            'shaded metallic cylinders with a specular highlight and shiny ball joints at '
            'every turn, drawn onto a persistent canvas that accumulates across frames and '
            'resets when it fills. Growth speed reacts to audio; the background dims to black.',
            'Псевдо-3D изометрический трубопровод в духе скринсейвера Win95 «3D Pipes»: '
            'шейдированные металлик-цилиндры с бликом и блестящие шары-суставы на поворотах '
            'рисуются на персистентном холсте, который копится между кадрами и сбрасывается '
            'при заполнении. Скорость роста реагирует на аудио, фон притемняется к чёрному.',
        ),
    ),


    # ── CORE FX ────────────────────────────────────────────────────────
    EffectSpec(
        id='stutter', label='Stutter / Drill', group='CORE FX',
        cls=None, chain_kind='special',
        enable_key='fx_stutter', enabled_default=True,
        chance_key=None,
        note='IMPACT segments - repeats short hits 2/4/8× for drillcore stutter.',
        tooltip=bi(
            'The picture judders in place - a short slice freezes and machine-guns 2/4/8 '
            'times, like a scratched DVD or a drum-and-bass edit. Fires on short IMPACT '
            'hits; higher CHAOS = more often.',
            'Картинка дёргается на месте - короткий кусок замирает и «строчит» 2/4/8 раз, '
            'будто заевший DVD или drill-монтаж. Бьёт на коротких IMPACT-ударах; выше CHAOS - чаще.',
        ),
    ),

    EffectSpec(
        id='flash', label='Flash Frame', group='CORE FX',
        cls=core.FlashEffect, chain_kind='special',
        enable_key='fx_flash', enabled_default=True,
        chance_key='fx_flash_chance', default_chance=0.8,
        note='DROP / IMPACT - injects a 1-2 frame full-white/black flash.',
        tooltip=bi(
            'A hard black or white frame punches in on drops and hits - a camera-flash '
            'blink over the video. Higher CHANCE = more blinks; cranked up it becomes a strobe.',
            'На дропах и ударах вбивается жёсткий чёрный или белый кадр - моргание как от '
            'фотовспышки поверх видео. Выше CHANCE - больше морганий; на максимуме - стробоскоп.',
        ),
    ),

    EffectSpec(
        id='ghost', label='Ghost Trails', group='CORE FX',
        cls=core.GhostTrailsEffect,
        enable_key='fx_ghost', enabled_default=False,
        chance_key=None,                 # always-on при включении
        params=[ParamSpec('fx_ghost_int', 'Opacity', 0.5, 0.0, 1.0,
                          kwarg='intensity_max', indent=False,
                          tooltip=bi(
                              'Higher = more bleed from the previous frame; <0.3 a subtle smear, '
                              '>0.7 a heavy ghost echo.',
                              'Выше - сильнее просвечивает предыдущий кадр; <0.3 - лёгкий смаз, '
                              '>0.7 - выраженное «эхо».',
                          ))],
        intensity_max_kwarg='intensity_max',
        note='SUSTAIN / BUILD - always on when enabled.',
        tooltip=bi(
            'Motion leaves a translucent trail - moving objects smear into a soft echo '
            'behind themselves. Pair with FEEDBACK for long comet tails.',
            'Движение оставляет полупрозрачный след - объекты размазываются в мягкое «эхо» '
            'позади себя. В паре с FEEDBACK - длинные кометные хвосты.',
        ),
    ),

    EffectSpec(
        id='pixel_sort', label='Pixel Sort', group='CORE FX',
        cls=core.PixelSortEffect,
        enable_key='fx_psort', enabled_default=True,
        chance_key='fx_psort_chance', default_chance=0.5,
        params=[
            ParamSpec('fx_psort_int', 'Pixel Sort Intensity', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'Higher = more strips, taller strips. >0.7 turns the frame into '
                          'colour bars.',
                          'Выше - больше полос и они шире. >0.7 - кадр превращается в цветные '
                          'столбцы.',
                      )),
            ParamSpec('fx_psort_axis', 'Sort Axis', 'luminance', kind='choice',
                      choices=['luminance', 'hue', 'saturation'], indent=True,
                      tooltip=bi(
                          'Which channel decides column order: luminance (bright→dark), '
                          'hue (rainbow), saturation (vivid→grey).',
                          'По чему сортировать столбцы: luminance (от светлого к тёмному), '
                          'hue (радуга), saturation (от насыщенного к серому).',
                      )),
            ParamSpec('fx_psort_mode', 'Sort Mode', 'block', kind='choice',
                       choices=['block', 'streaks', 'columns'], indent=True,
                       tooltip=bi(
                           'Sorting algorithm: block (original crystalline melting), '
                           'streaks (After Effects / threshold-based bleeding), '
                           'columns (legacy shifting).',
                           'Алгоритм сортировки: block (оригинальное кристаллическое плавление), '
                           'streaks (стиль After Effects / построчные шлейфы), '
                           'columns (старый сдвиг колонок).',
                       )),
            ParamSpec('fx_psort_direction', 'Sort Direction', 'horizontal', kind='choice',
                       choices=['horizontal', 'vertical'], indent=True,
                       tooltip=bi(
                           'Sorting direction: horizontal (along rows) or vertical (along columns).',
                           'Направление сортировки: horizontal (вдоль строк) или vertical (вдоль столбцов).',
                       )),
            ParamSpec('fx_psort_threshold', 'Streaks Threshold', 0.3, 0.0, 1.0, indent=True,
                       tooltip=bi(
                           'Luminance threshold for streaks mode. Higher = only brighter pixels are sorted.',
                           'Порог яркости для режима streaks. Выше - сортируются только самые яркие пиксели.',
                       )),
        ],
        extra_factory=_psort_extras,
        note='NOISE / IMPACT / DROP - sorts horizontal/vertical strips of pixels.',
        tooltip=bi(
            'Pixels melt into long streaks - bright (or vivid) pixels bleed and drag into '
            'smooth colour bands, the classic glitch-art smear. High intensity turns whole '
            'regions into colour bars.',
            'Пиксели растекаются в длинные полосы - яркие (или насыщенные) пиксели «текут» в '
            'гладкие цветовые ленты, классический glitch-art смаз. На высокой интенсивности '
            'целые области превращаются в цветные столбцы.',
        ),
    ),

    EffectSpec(
        id='datamosh', label='Optical Flow', group='CORE FX',
        cls=core.OpticalFlowEffect,
        enable_key='fx_datamosh', enabled_default=False,
        chance_key='fx_datamosh_chance', default_chance=0.5,
        note='NOISE - optical-flow smear; legacy Final-mode I-frame source swap kept.',
        tooltip=bi(
            'Motion-flow smear: the previous frame is warped along the picture\'s own motion '
            'vectors, so moving areas drag and flow like wet paint. This is the effect old '
            'presets saved as "Datamosh" - they keep working unchanged, including the legacy '
            'I-frame-drop source swap on NOISE segments in Final render. For the real '
            'codec-level mosh see True Datamosh below.',
            'Смаз по оптическому потоку: предыдущий кадр деформируется вдоль векторов движения '
            'самой картинки, движущиеся зоны текут, как мокрая краска. Именно этот эффект старые '
            'пресеты сохраняли под именем «Datamosh» - они продолжают работать без изменений, '
            'включая старую подмену источника без I-кадров на NOISE в режиме Final. Настоящий '
            'кодековый мош - в True Datamosh ниже.',
        ),
    ),


    EffectSpec(
        id='ascii', label='ASCII Filter', group='CORE FX',
        cls=core.ASCIIEffect,
        enable_key='fx_ascii', enabled_default=False,
        chance_key='fx_ascii_chance', default_chance=0.7,
        params=[
            ParamSpec('fx_ascii_size', 'Char Size (px)', 12, 4, 40, kind='int',
                      kwarg='char_size',
                      tooltip=bi(
                          'Cell height in pixels. Smaller = more detail, slower. >20 looks chunky '
                          'terminal.',
                          'Высота ячейки в пикселях. Меньше - больше деталей и медленнее. >20 - '
                          'грубый терминальный вид.',
                      )),
            ParamSpec('fx_ascii_blend', 'Blend (0=ASCII, 1=overlay)', 0.0, 0.0, 1.0,
                      kwarg='blend',
                      tooltip=bi(
                          '0 = pure ASCII, 1 = original frame visible, in-between mixes them.',
                          '0 - чистый ASCII, 1 - виден исходный кадр, между - смешение.',
                      )),
            ParamSpec('fx_ascii_color_mode', 'Color Mode', 'fixed', kind='choice',
                      choices=['fixed', 'original', 'inverted'], indent=True,
                      tooltip=bi(
                          'fixed = fg/bg colours; original = character coloured by source pixel; '
                          'inverted = 255 − source.',
                          'fixed - заданные fg/bg; original - символ цвета исходного пикселя; '
                          'inverted - инвертированный исходный.',
                      )),
            ParamSpec('fx_ascii_fg_r', 'FG Red', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_fg_g', 'FG Green', 255, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_fg_b', 'FG Blue', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_bg_r', 'BG Red', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_bg_g', 'BG Green', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_bg_b', 'BG Blue', 0, 0, 255, kind='int', indent=True, tooltip=''),
        ],
        extra_factory=_ascii_extras,
        note='SUSTAIN / SILENCE / BUILD - full-frame ASCII art.',
        tooltip=bi(
            'The whole frame is rebuilt out of text characters - dense glyphs fill the dark '
            'areas, sparse ones the bright, like a terminal / green-screen render of the video. '
            'fg/bg colours and BLEND set the look.',
            'Весь кадр пересобран из текстовых символов - плотные глифы в тёмных зонах, '
            'разреженные в светлых, как терминальный / «зелёный экран» вывод видео. '
            'Внешний вид задают цвета fg/bg и BLEND.',
        ),
    ),

    # ── GLITCH ─────────────────────────────────────────────────────────
    EffectSpec(
        id='rgb_shift', label='RGB Shift', group='GLITCH',
        cls=glitch.RGBShiftEffect,
        enable_key='fx_rgb', enabled_default=True,
        chance_key='fx_rgb_chance', default_chance=0.7,
        params=[_drive_param('fx_rgb', 'high')],
        note='IMPACT / BUILD / NOISE / DROP - colour fringing.',
        tooltip=bi(
            'Colours split apart - red and blue fringes peel off the edges, the classic '
            '3D-glasses / chromatic-aberration glitch. Stronger on hits; higher CHANCE = more frames.',
            'Цвета расходятся - по краям отслаиваются красная и синяя каёмки, классический '
            'вид «3D-очки» / хроматическая аберрация. Сильнее на ударах; выше CHANCE - больше кадров.',
        ),
    ),
    EffectSpec(
        id='block_glitch', label='Block Glitch', group='GLITCH',
        cls=glitch.BlockGlitchEffect,
        enable_key='fx_block_glitch', enabled_default=False,
        chance_key='fx_block_glitch_chance', default_chance=0.5,
        params=[_drive_param('fx_block_glitch', 'high')],
        note='IMPACT / DROP / NOISE - random 16px blocks corrupted.',
        tooltip=bi(
            'Rectangular chunks of the image jump to the wrong place or go flat-coloured - '
            'looks like a corrupted video stream, with broken macroblocks scattered over the frame.',
            'Прямоугольные куски картинки прыгают не на своё место или заливаются плоским '
            'цветом - как повреждённый видеопоток, битые макроблоки по всему кадру.',
        ),
    ),
    EffectSpec(
        id='pixel_drift', label='Pixel Drift', group='GLITCH',
        cls=glitch.PixelDriftEffect,
        enable_key='fx_pixel_drift', enabled_default=False,
        chance_key='fx_pixel_drift_chance', default_chance=0.5,
        note='NOISE / IMPACT - rows slide using simplex noise.',
        tooltip=bi(
            'Rows slide sideways by smoothly-varying amounts, so the image ripples and shears '
            'horizontally like a reflection on moving water.',
            'Строки плавно съезжают вбок на разную величину - изображение волнисто «плывёт» и '
            'срезается по горизонтали, как отражение на воде.',
        ),
    ),
    EffectSpec(
        id='colorbleed', label='Color Bleed / VHS Smear', group='GLITCH',
        cls=glitch.ColorBleedEffect,
        enable_key='fx_colorbleed', enabled_default=False,
        chance_key='fx_colorbleed_chance', default_chance=0.5,
        note='NOISE / SUSTAIN - horizontal colour smear on one channel.',
        tooltip=bi(
            'One colour channel smears sideways and bleeds past its edges - the watery VHS '
            '"colour running off the picture" look.',
            'Один цветовой канал размазывается вбок и вытекает за края - водянистый VHS-эффект '
            '«цвет уползает с картинки».',
        ),
    ),
    EffectSpec(
        id='freeze_corrupt', label='Freeze + Corrupt', group='GLITCH',
        cls=glitch.FreezeCorruptEffect,
        enable_key='fx_freeze_corrupt', enabled_default=False,
        chance_key='fx_freeze_corrupt_chance', default_chance=0.3,
        note='DROP - freezes frame for a few ticks and corrupts it.',
        tooltip=bi(
            'The image freezes for a beat and rots in place - the held frame breaks up into '
            'corrupted blocks. A hard stop-and-shatter accent on drops.',
            'Картинка замирает на миг и «гниёт» на месте - застывший кадр рассыпается на битые '
            'блоки. Жёсткий акцент «стоп-и-осыпание» на дропах.',
        ),
    ),
    EffectSpec(
        id='negative', label='Negative', group='GLITCH',
        cls=glitch.NegativeEffect,
        enable_key='fx_negative', enabled_default=False,
        chance_key='fx_negative_chance', default_chance=0.2,
        params=[_gate_param('fx_negative')],
        note='IMPACT / DROP / NOISE - full colour invert.',
        tooltip=bi(
            'Colours flip to their photographic negative - a jarring inverted-film blink. '
            'Use sparingly; at high CHANCE it strobes.',
            'Цвета переворачиваются в фотонегатив - резкое моргание «инвертированная плёнка». '
            'Используйте умеренно; на высоком CHANCE - стробоскоп.',
        ),
    ),

    # ── DEGRADATION ────────────────────────────────────────────────────
    EffectSpec(
        id='scanlines', label='Scan Lines', group='DEGRADATION',
        cls=degradation.ScanLinesEffect,
        enable_key='fx_scanlines', enabled_default=False,
        chance_key='fx_scanlines_chance', default_chance=0.8,
        note='SUSTAIN / NOISE - CRT scanline darkening.',
        tooltip=bi(
            'Thin dark horizontal lines lie over the picture - an old CRT / broadcast-monitor '
            'look. Higher intensity = darker, denser lines.',
            'Поверх картинки - тонкие тёмные горизонтальные линии, вид старого CRT / эфирного '
            'монитора. Выше интенсивность - темнее и плотнее линии.',
        ),
    ),
    EffectSpec(
        id='bitcrush', label='Bitcrush / Posterize', group='DEGRADATION',
        cls=degradation.BitcrushEffect,
        enable_key='fx_bitcrush', enabled_default=False,
        chance_key='fx_bitcrush_chance', default_chance=0.5,
        note='Any segment - reduces colour depth.',
        tooltip=bi(
            'Colour collapses into a few flat bands - smooth gradients turn into hard '
            'posterised steps, a cheap-LCD / retro-console palette look.',
            'Цвет схлопывается в несколько плоских полос - плавные градиенты становятся '
            'резкими постеризованными ступенями, вид дешёвого LCD / ретро-консоли.',
        ),
    ),
    EffectSpec(
        id='jpeg_crush', label='JPEG Crush', group='DEGRADATION',
        cls=degradation.JPEGCrushEffect,
        enable_key='fx_jpeg_crush', enabled_default=False,
        chance_key='fx_jpeg_crush_chance', default_chance=0.5,
        note='IMPACT / NOISE - heavy JPEG re-encode artefacts.',
        tooltip=bi(
            'The frame looks like a JPEG saved at the lowest quality - blocky 8×8 artefacts '
            'and smeared "mosquito" colour crawling around every edge.',
            'Кадр выглядит как JPEG на минимальном качестве - блочные 8×8 артефакты и '
            'замыленный «москитный» цвет вокруг каждого края.',
        ),
    ),
    EffectSpec(
        id='fisheye', label='Fisheye / Barrel', group='DEGRADATION',
        cls=degradation.FisheyeEffect,
        enable_key='fx_fisheye', enabled_default=False,
        chance_key='fx_fisheye_chance', default_chance=0.3,
        note='BUILD / SUSTAIN - barrel lens distortion.',
        tooltip=bi(
            'The image bulges outward through a rounded lens - a peephole / GoPro barrel warp. '
            'Keep CHANCE low; it is visually heavy.',
            'Картинка выпучивается наружу через круглую линзу - искажение «дверной глазок» / '
            'GoPro. Держите CHANCE низким - эффект тяжёлый.',
        ),
    ),
    EffectSpec(
        id='vhstape', label='VHS Tape (composite)', group='DEGRADATION',
        trigger_types=[SegmentType.BUILD, SegmentType.SUSTAIN],
        cls=vhs_fx.VHSTapeEffect,
        enable_key='fx_vhstape', enabled_default=False,
        chance_key='fx_vhstape_chance', default_chance=0.7,
        params=[
            ParamSpec('fx_vhstape_int', 'Wear', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'Master VHS-wear knob. 0 = pristine, 1 = heavy generation loss '
                          '(blurred chroma, tape grain, contrast crush, head-switch noise '
                          'at the bottom).',
                          'Главный ползунок «изношенности VHS». 0 - чистый источник, 1 - '
                          'тяжёлая потеря качества (размытая цветность, плёночный шум, '
                          'сжатие контраста, head-switch шум по низу кадра).',
                      )),
            ParamSpec('fx_vhstape_dust', 'Dust scratches', 'off', kind='choice',
                      choices=['off', 'on'], kwarg=None,
                      tooltip=bi(
                          'Adds rare 1-px vertical scratches to imitate physical tape dust.',
                          'Добавляет редкие вертикальные царапины 1-px - имитация пыли на плёнке.',
                      )),
        ],
        intensity_max_kwarg='intensity_max',
        extra_factory=lambda cfg: dict(dust=(cfg.get('fx_vhstape_dust', 'off') == 'on')),
        note='BUILD / SUSTAIN - slow tape wear that accumulates over time.',
        tooltip=bi(
            'A full VHS-look pipeline in a single effect: Y/C separation with chroma blur '
            '(the canonical "luma sharp, colour smeared" tape signature), low-frequency '
            'tape grain that modulates brightness in patches, sub-pixel horizontal '
            'wow & flutter, generation-loss contrast crush + additive noise, and '
            'head-switch noise on the bottom rows. One slider tunes everything.',
            'Полный VHS-look в одном эффекте: Y/C-разделение с размытием цветности '
            '(канонический VHS-признак - резкая яркость и расплывшийся цвет), '
            'низкочастотный плёночный шум с пятнами, субпиксельный wow & flutter, '
            'compression-loss + аддитивный шум, head-switch шум по нижним строкам. '
            'Один слайдер на всё.',
        ),
    ),
    EffectSpec(
        id='vhs', label='VHS Tracking', group='DEGRADATION',
        cls=degradation.VHSTrackingEffect,
        enable_key='fx_vhs', enabled_default=False,
        chance_key='fx_vhs_chance', default_chance=0.5,
        note='NOISE / DROP - tape tracking error: shifted strips + luminance noise.',
        tooltip=bi(
            'The picture tears into horizontally-shifted bands with a strip of hissing noise '
            'rolling through - a tape losing tracking, wobbling and breaking up.',
            'Картинка рвётся на горизонтально смещённые полосы, сквозь которые катится полоса '
            'шипящего шума - плёнка теряет трекинг, дрожит и рассыпается; добавляется точечный '
            'шум яркости. Аутентичный «съезд» VHS-трекинга.',
        ),
    ),
    EffectSpec(
        id='interlace', label='Interlace', group='DEGRADATION',
        cls=degradation.InterlaceEffect,
        enable_key='fx_interlace', enabled_default=False,
        chance_key='fx_interlace_chance', default_chance=0.4,
        note='SUSTAIN - odd rows from previous frame.',
        tooltip=bi(
            'Fast motion splits into a fine horizontal comb - moving edges tear into '
            'interlaced teeth, the classic 50i broadcast / deinterlacing artefact.',
            'Быстрое движение распадается на мелкую горизонтальную «гребёнку» - края рвутся на '
            'чересстрочные зубцы, классический артефакт 50i / деинтерлейса.',
        ),
    ),
    EffectSpec(
        id='bad_signal', label='Bad Signal', group='DEGRADATION',
        cls=degradation.BadSignalEffect,
        enable_key='fx_bad_signal', enabled_default=False,
        chance_key='fx_bad_signal_chance', default_chance=0.3,
        params=[_drive_param('fx_bad_signal', 'high')],
        note='DROP / NOISE - vertical noise bars + row shifts.',
        tooltip=bi(
            'The signal breaks up - random coloured vertical bars flicker across the frame and '
            'whole rows jump sideways, like a dying digital broadcast.',
            'Сигнал срывается - по кадру мелькают случайно окрашенные вертикальные полосы, а '
            'целые строки прыгают вбок, как умирающий цифровой эфир.',
        ),
    ),
    EffectSpec(
        id='dither', label='Dithering', group='DEGRADATION',
        cls=degradation.DitheringEffect,
        enable_key='fx_dither', enabled_default=False,
        chance_key='fx_dither_chance', default_chance=0.4,
        note='SILENCE / SUSTAIN - Bayer 4×4 ordered dither.',
        tooltip=bi(
            'Smooth shading breaks into a fine stipple of dots - the retro 1-bit / GameBoy look '
            'where gradients become cross-hatched pixel patterns.',
            'Плавные тени рассыпаются в мелкую «крапчатую» сетку точек - ретро-вид 1-бит / '
            'GameBoy, где градиенты превращаются в штриховку из пикселей.',
        ),
    ),
    EffectSpec(
        id='zoom_glitch', label='Zoom Glitch', group='DEGRADATION',
        cls=degradation.ZoomGlitchEffect,
        enable_key='fx_zoom_glitch', enabled_default=False,
        chance_key='fx_zoom_glitch_chance', default_chance=0.5,
        params=[ParamSpec('fx_zoom_glitch_dur', 'Snap-back Duration (frames)', 10, 3, 30,
                          kind='int', kwarg='duration_frames',
                          tooltip=bi(
                              'How many frames the elastic return takes. Shorter = sharper '
                              'whip; longer = visible breathing.',
                              'За сколько кадров эффект упруго возвращается. Меньше - резче '
                              'хлыст; больше - заметное «дыхание».',
                          )),
                _drive_param('fx_zoom_glitch', 'bass'),
                _gate_param('fx_zoom_glitch')],
        note='IMPACT / DROP - anisotropic squash/stretch with curved return.',
        tooltip=bi(
            'The frame gets yanked - snapped taller or wider on one axis on the hit, then '
            'springs elastically back to normal over a few frames. A rubber-band punch.',
            'Кадр дёргает - на ударе резко растягивает выше или шире по одной оси, затем он '
            'упруго отскакивает к норме за пару кадров. «Резиновый» рывок.',
        ),
    ),
    EffectSpec(
        id='sharpen', label='Sharpen (unsharp mask)', group='DEGRADATION',
        cls=degradation.SharpenEffect,
        enable_key='fx_sharpen', enabled_default=False,
        chance_key='fx_sharpen_chance', default_chance=0.7,
        params=[
            ParamSpec('fx_sharpen_amount', 'Amount', 1.5, 0.2, 4.0,
                      kwarg='amount',
                      tooltip=bi(
                          'Strength of the high-pass overshoot. 0.5 polite crispness, 2 hard '
                          'halo, 4 edge-glow.',
                          'Сила усиления высоких частот. 0.5 - лёгкая резкость, 2 - жёсткие '
                          'ореолы, 4 - «свечение» по контурам.',
                      )),
            ParamSpec('fx_sharpen_radius', 'Radius (px)', 2.0, 1.0, 9.0,
                      kwarg='radius',
                      tooltip=bi(
                          'Gaussian blur radius for the low-pass component. Larger = thicker '
                          'halo around edges.',
                          'Радиус гауссова низкочастотного компонента. Больше - толще «ореолы» '
                          'вокруг контуров.',
                      )),
        ],
        note='IMPACT / DROP / SUSTAIN / BUILD - unsharp-mask high-pass overshoot.',
        tooltip=bi(
            'Edges get hard, glowing outlines - detail is over-crisped into bright halos. '
            'Combine with COLOR BLEED for a neon-edge look.',
            'Контуры становятся жёсткими, светящимися - детали переточены в яркие ореолы. '
            'В паре с COLOR BLEED - «неоновая» окантовка.',
        ),
    ),

    # ── COMPLEX ────────────────────────────────────────────────────────
    EffectSpec(
        id='feedback', label='Feedback Loop', group='COMPLEX',
        cls=complex_fx.FeedbackLoopEffect,
        enable_key='fx_feedback', enabled_default=False,
        chance_key=None,                 # always-on
        params=[
            _drive_param('fx_feedback', 'mid'),
            _react_param('fx_feedback',
                         'the accumulator is cleared on each detected beat, so trails '
                         'reset on the kick instead of only on IMPACT segments.',
                         'аккумулятор сбрасывается на каждый детектированный бит - '
                         'шлейфы обнуляются на кике, а не только на IMPACT-сегментах.'),
        ],
        note='SUSTAIN / BUILD - accumulates frames recursively.',
        tooltip=bi(
            'The image smears into itself, leaving long glowing wash-trails that pile up on '
            'sustained energy and wipe clean on the kick - a video-feedback / long-exposure look.',
            'Картинка размазывается сама в себя, оставляя длинные светящиеся шлейфы, которые '
            'копятся на устойчивой энергии и стираются на кике - вид видео-фидбэка / длинной выдержки.',
        ),
    ),
    EffectSpec(
        id='phase_shift', label='Phase Shift (L/R bands)', group='COMPLEX',
        cls=complex_fx.PhaseShiftEffect,
        enable_key='fx_phase_shift', enabled_default=False,
        chance_key='fx_phase_shift_chance', default_chance=0.4,
        note='NOISE / DROP - alternating bands shift left/right.',
        tooltip=bi(
            'The frame splits into horizontal bands that slide opposite ways - every other '
            'strip shoves left or right, shearing the picture into offset ribbons.',
            'Кадр делится на горизонтальные полосы, съезжающие в разные стороны - через одну '
            'влево/вправо, картинка режется на смещённые ленты.',
        ),
    ),
    EffectSpec(
        id='mosaic', label='Mosaic Pulse (bass RMS)', group='COMPLEX',
        cls=complex_fx.MosaicPulseEffect,
        enable_key='fx_mosaic', enabled_default=False,
        chance_key='fx_mosaic_chance', default_chance=0.5,
        params=[_drive_param('fx_mosaic', 'bass')],
        note='IMPACT / BUILD - pixelation pulse.',
        tooltip=bi(
            'The picture pixelates into chunky blocks that pump with the bass - big squares on '
            'the hit, fine again between, a censor-bar / mosaic pulse.',
            'Картинка пикселизуется в крупные блоки, пульсирующие с басом - большие квадраты на '
            'ударе, мельче между, «мозаика-цензура» в такт.',
        ),
    ),
    EffectSpec(
        id='echo', label='Echo Compound (hue shift)', group='COMPLEX',
        cls=complex_fx.EchoCompoundEffect,
        enable_key='fx_echo', enabled_default=False,
        chance_key='fx_echo_chance', default_chance=0.4,
        note='SUSTAIN / BUILD - layered colour echoes from the past.',
        tooltip=bi(
            'Ghostly colour-shifted copies of past frames layer over the present - a triple-'
            'exposure smear with a rainbow tint drifting behind motion.',
            'Поверх настоящего наслаиваются призрачные копии прошлых кадров со сдвигом цвета - '
            'тройная экспозиция с радужным «хвостом» за движением.',
        ),
    ),
    EffectSpec(
        id='kali', label='Kali Mirror (kaleidoscope)', group='COMPLEX',
        cls=complex_fx.KaliMirrorEffect,
        enable_key='fx_kali', enabled_default=False,
        chance_key='fx_kali_chance', default_chance=0.3,
        note='BUILD / SUSTAIN - kaleidoscopic mirror+rotate.',
        tooltip=bi(
            'The frame folds into a mirror-symmetric kaleidoscope mandala - the video '
            'reflected and rotated into a shifting symmetrical pattern.',
            'Кадр складывается в зеркально-симметричную калейдоскоп-мандалу - видео, отражённое '
            'и повёрнутое в меняющийся симметричный узор.',
        ),
    ),
    EffectSpec(
        id='cascade', label='Glitch Cascade', group='COMPLEX',
        cls=complex_fx.GlitchCascadeEffect,
        enable_key='fx_cascade', enabled_default=False,
        chance_key='fx_cascade_chance', default_chance=0.4,
        note='IMPACT / DROP / NOISE - chains random glitch effects.',
        tooltip=bi(
            'A pile-up of glitches at once - several random corruption effects stack on one '
            'frame for a chaotic "everything breaks" burst on the hit.',
            'Куча глитчей разом - несколько случайных эффектов порчи наслаиваются на один кадр, '
            'хаотичный взрыв «всё сломалось» на ударе.',
        ),
    ),

    # ── SIGNAL DOMAIN ──────────────────────────────────────────────────
    EffectSpec(
        id='resonant', label='Resonant Rows', group='SIGNAL DOMAIN',
        cls=signal.ResonantRowsEffect,
        enable_key='fx_resonant', enabled_default=False,
        chance_key='fx_resonant_chance', default_chance=0.5,
        params=[
            ParamSpec('fx_resonant_freq', 'Resonance Freq (cycles/px)', 0.08, 0.01, 0.3,
                      kwarg='cutoff',
                      tooltip=bi(
                          'Centre frequency of the IIR bandpass. Lower = wider rings; higher = '
                          'tight micro-detail ringing.',
                          'Центральная частота IIR-полосового фильтра. Ниже - шире «волны»; выше '
                          '- плотный «звон» по микродеталям.',
                      )),
            ParamSpec('fx_resonant_q', 'Q factor (sharpness)', 12.0, 2.0, 30.0,
                      kwarg='q',
                      tooltip=bi(
                          'Bandpass sharpness. >15 produces clearly visible resonance bands at '
                          'edges.',
                          'Острота полосы. >15 даёт чёткие «резонансные полосы» вдоль контуров.',
                      )),
            _react_param('fx_resonant',
                         'the resonance centre frequency tracks the music\'s spectral '
                         'centroid, so the visual ringing rises and falls with the pitch '
                         'of the track (a rising synth raises the resonance).',
                         'центральная частота резонанса следует за спектральным центроидом '
                         'музыки - визуальный «звон» поднимается и опускается вместе с '
                         'высотой трека (растёт синт - растёт резонанс).'),
        ],
        note='IIR bandpass along pixel rows - spatial ringing at edges.',
        tooltip=bi(
            'Bright edges ring and echo into fine ripples - thin resonant bands shimmer '
            'alongside contours, as if the picture were vibrating.',
            'Яркие края «звенят» и отдаются мелкой рябью - тонкие резонансные полосы дрожат '
            'вдоль контуров, будто картинка вибрирует.',
        ),
    ),
    EffectSpec(
        id='temporal_rgb', label='Temporal RGB Shift', group='SIGNAL DOMAIN',
        cls=signal.TemporalRGBEffect,
        enable_key='fx_temporal_rgb', enabled_default=False,
        chance_key=None,                 # always-on
        params=[ParamSpec('fx_temporal_rgb_lag', 'Lag (frames)', 8, 2, 20, kind='int',
                          kwarg='lag', indent=False,
                          tooltip=bi(
                              'Max frames of separation between R, G and B. >10 frames creates '
                              'obvious chroma ghost trails.',
                              'Максимальный разрыв в кадрах между R, G и B. >10 даёт заметный '
                              'хроматический «шлейф» на движении.',
                          ))],
        note='R/G/B from different time offsets - chromatic time ghost.',
        tooltip=bi(
            'Colours lag behind motion at different speeds - moving objects trail red, green '
            'and blue ghosts, while static scenes stay clean.',
            'Цвета отстают от движения с разной скоростью - за объектами тянутся красный, '
            'зелёный и синий призраки, а на статике чисто.',
        ),
    ),
    EffectSpec(
        id='fft_phase', label='FFT Phase Corrupt', group='SIGNAL DOMAIN',
        cls=signal.FFTPhaseCorruptEffect,
        enable_key='fx_fft_phase', enabled_default=False,
        chance_key='fx_fft_phase_chance', default_chance=0.5,
        params=[ParamSpec('fx_fft_phase_amount', 'Phase Noise Amount', 0.5, 0.05, 1.0,
                          kwarg='amount',
                          tooltip=bi(
                              'Adds noise to FFT phase but keeps magnitude. Image scrambles into '
                              'wave-interference patterns; >0.7 fully ungrounds it.',
                              'Подмешивает шум в фазу FFT, сохраняя амплитуду. Кадр превращается '
                              'в волновую интерференцию; >0.7 - изображение полностью «расходится».',
                          )),
                _react_param('fx_fft_phase',
                             'the audio spectrum is mapped onto the radial frequency rings of '
                             'the frame\'s 2-D FFT, so phase noise hits the image at exactly the '
                             'spatial frequencies where the music currently has energy.',
                             'аудио-спектр накладывается на радиальные частотные кольца 2D-FFT '
                             'кадра - фазовый шум бьёт по изображению именно на тех '
                             'пространственных частотах, где у музыки сейчас энергия.')],
        note='Scrambles 2-D FFT phase, preserves magnitude - wave interference.',
        tooltip=bi(
            'The image dissolves into rippling wave-interference - recognisable shapes smear '
            'into a shimmering "hologram corrupted in transit" pattern.',
            'Изображение растворяется в рябь волновой интерференции - узнаваемые формы '
            'расплываются в мерцающий узор «голограмма, повреждённая при передаче».',
        ),
    ),
    EffectSpec(
        id='waveshaper', label='Waveshaper / Tube Sat', group='SIGNAL DOMAIN',
        cls=signal.WaveshaperEffect,
        enable_key='fx_waveshaper', enabled_default=False,
        chance_key='fx_waveshaper_chance', default_chance=0.5,
        params=[ParamSpec('fx_waveshaper_drive', 'Drive', 3.0, 0.5, 8.0,
                          kwarg='drive',
                          tooltip=bi(
                              'tanh saturation amount. 1 = neutral, 3 = warm, >5 = hard clip / '
                              'cartoon colours.',
                              'Сила tanh-сатурации. 1 - нейтрально, 3 - «тёплый» окрас, >5 - '
                              'жёсткий клип / мультяшные цвета.',
                          ))],
        note='Tube-amplifier saturation on pixel values.',
        tooltip=bi(
            'Colours get pushed into thick, saturated, cartoon-poster tones - shapes stay but '
            'hues bloom and hard-clip like an over-driven tube amp.',
            'Цвета уходят в густые, насыщенные, «мультяшно-плакатные» тона - формы остаются, но '
            'оттенки взрываются и жёстко клиппируются, как перегруженный ламповый усилитель.',
        ),
    ),
    EffectSpec(
        id='histo_lag', label='Histogram Lag', group='SIGNAL DOMAIN',
        cls=signal.HistoLagEffect,
        enable_key='fx_histo_lag', enabled_default=False,
        chance_key=None,                 # always-on
        params=[ParamSpec('fx_histo_lag_frames', 'Palette Lag (frames)', 30, 5, 90, kind='int',
                          kwarg='lag_frames', indent=False,
                          tooltip=bi(
                              'Match the histogram of the current frame to a frame N frames in the '
                              'past. Big values = palette feels stuck in time.',
                              'Подстраивает гистограмму текущего кадра под кадр N кадров назад. '
                              'Большие значения - палитра «застряла в прошлом».',
                          ))],
        note='Match palette to a frame from N back - colour memory.',
        tooltip=bi(
            'The scene is current but wears an old palette - colours feel time-lagged and '
            'stuck, as if the picture is remembering how it looked seconds ago.',
            'Сцена актуальная, но в старой палитре - цвета словно «застряли во времени», будто '
            'картинка помнит, как выглядела секунды назад.',
        ),
    ),
    EffectSpec(
        id='wrong_sub', label='Wrong Chroma Sub (4:1:N)', group='SIGNAL DOMAIN',
        cls=signal.WrongSubsamplingEffect,
        enable_key='fx_wrong_sub', enabled_default=False,
        chance_key='fx_wrong_sub_chance', default_chance=0.5,
        params=[ParamSpec('fx_wrong_sub_factor', 'Downsample Factor', 4, 2, 8, kind='int',
                          kwarg='factor',
                          tooltip=bi(
                              'How aggressively to downsample chroma vs luma. 2 = mild, 8 = '
                              'colour blocks visibly bleed past edges.',
                              'Насколько агрессивно даунсэмплить цветность относительно яркости. '
                              '2 - мягко, 8 - цветные блоки заметно «вытекают» за контуры.',
                          ))],
        note='Chroma subsampling abuse - colour blocks bleed over sharp edges.',
        tooltip=bi(
            'Colour smears in blocky patches that bleed past the sharp outlines - like a badly-'
            'compressed clip where the colour and the detail no longer line up.',
            'Цвет размазывается блочными пятнами, вытекающими за чёткие контуры - как плохо '
            'сжатый клип, где цвет и детали больше не совпадают.',
        ),
    ),
    EffectSpec(
        id='gameoflife', label='Game of Life Mask', group='SIGNAL DOMAIN',
        cls=signal.GameOfLifeEffect,
        enable_key='fx_gameoflife', enabled_default=False,
        chance_key='fx_gameoflife_chance', default_chance=0.5,
        params=[ParamSpec('fx_gameoflife_iters', 'Iterations', 2, 1, 5, kind='int',
                          kwarg='iterations',
                          tooltip=bi(
                              'How many Conway steps to evolve the binarised mask. 1 = subtle, '
                              '5 = mask becomes alien.',
                              'Сколько шагов Conway-эволюции применить к бинаризованной маске. '
                              '1 - едва заметно, 5 - маска становится «инопланетной».',
                          ))],
        note='Conway automaton on frame as corruption mask - organic glitch.',
        tooltip=bi(
            'A crawling cellular pattern eats into the frame - living cells sparkle and mutate '
            'over the image like a spreading organic corruption.',
            'По кадру ползёт клеточный узор - живые клетки искрят и мутируют поверх изображения, '
            'как расползающаяся органическая порча.',
        ),
    ),
    EffectSpec(
        id='ela', label='ELA (Error Level Analysis)', group='SIGNAL DOMAIN',
        cls=signal.ELAEffect,
        enable_key='fx_ela', enabled_default=False,
        chance_key='fx_ela_chance', default_chance=0.5,
        params=[ParamSpec('fx_ela_blend', 'ELA Blend (0=pure ELA, 1=original)', 0.5, 0.0, 1.0,
                          kwarg='blend',
                          tooltip=bi(
                              '0 = full forensic heat-map, 1 = fully off. ~0.4 gives glow on '
                              'edges over the original.',
                              '0 - полная «криминалистическая» тепло-карта, 1 - выключено. ~0.4 '
                              '- свечение по контурам поверх оригинала.',
                          ))],
        note='JPEG compression error map - forensic edge glow.',
        tooltip=bi(
            'Only the edges and textured areas light up in a glowing forensic heat-map, while '
            'flat regions go dark - an X-ray outline of the picture.',
            'Светятся только края и текстурные зоны - «криминалистическая» тепло-карта, а ровные '
            'области темнеют; рентгеновский контур картинки.',
        ),
    ),
    EffectSpec(
        id='dtype_corrupt', label='Dtype Reinterpret', group='SIGNAL DOMAIN',
        cls=signal.DtypeReinterpretEffect,
        enable_key='fx_dtype_corrupt', enabled_default=False,
        chance_key='fx_dtype_corrupt_chance', default_chance=0.5,
        params=[ParamSpec('fx_dtype_corrupt_amount', 'Noise Amount', 0.05, 0.01, 0.4,
                          kwarg='amount',
                          tooltip=bi(
                              'How hard to perturb the float16 view of the bytes. 0.05 = clean '
                              'VRAM-glitch; 0.3 = total visual death.',
                              'Сила возмущения float16-вида байтов. 0.05 - чистый VRAM-глитч; '
                              '0.3 - полное визуальное «уничтожение».',
                          ))],
        note='Frame bytes reread as float16 - VRAM-corruption look.',
        tooltip=bi(
            'The image shatters into harsh coloured static and torn bands - looks like a '
            'corrupted GPU framebuffer / dumped VRAM.',
            'Изображение рассыпается в резкий цветной «снег» и рваные полосы - как повреждённый '
            'кадровый буфер GPU / дамп VRAM.',
        ),
    ),
    EffectSpec(
        id='spatial_reverb', label='Spatial Reverb', group='SIGNAL DOMAIN',
        cls=signal.SpatialReverbEffect,
        enable_key='fx_spatial_reverb', enabled_default=False,
        chance_key='fx_spatial_reverb_chance', default_chance=0.5,
        params=[ParamSpec('fx_spatial_reverb_decay', 'Reverb Decay', 0.15, 0.05, 0.45,
                          kwarg='decay',
                          tooltip=bi(
                              'Reflection strength. Higher = more pronounced echo trails along '
                              'each row.',
                              'Сила отражений. Выше - заметнее «эхо-шлейфы» вдоль каждой строки.',
                          )),
                _react_param('fx_spatial_reverb',
                             'the echo tail follows onset density: busy percussion gives short, '
                             'tight echoes while sparse passages open up into long tails.',
                             'хвост эха следует за плотностью онсетов: плотная перкуссия даёт '
                             'короткое тугое эхо, а разреженные места раскрываются в длинные хвосты.')],
        note='Decaying horizontal echo - acoustic reverb on light.',
        tooltip=bi(
            'Light smears sideways into soft repeating echoes - bright shapes leave a fading '
            'horizontal reverb tail, as if the image had an acoustic space.',
            'Свет размазывается вбок в мягкие повторяющиеся отголоски - яркие формы оставляют '
            'затухающий горизонтальный «реверберационный» хвост, будто у картинки есть акустика.',
        ),
    ),

    # ── WARP ─────────────────────────────────────────────────────────
    EffectSpec(
        id='deriv_warp', label='Deriv Warp (gradient flow)', group='WARP',
        cls=warp.DerivWarpEffect,
        enable_key='fx_deriv_warp', enabled_default=False,
        chance_key='fx_deriv_warp_chance', default_chance=0.5,
        params=[ParamSpec('fx_deriv_warp_blend', 'Prev Blend', 0.35, 0.0, 0.6,
                          kwarg='blend',
                          tooltip=bi(
                              'How much of the previous frame ghosts through. 0 = pure '
                              'displacement; 0.6 = heavy smear.',
                              'Насколько просвечивает предыдущий кадр. 0 - чистое смещение; '
                              '0.6 - сильный смаз.',
                          ))],
        note='IMPACT / NOISE / DROP / SUSTAIN - Sobel of prev frame as motion field.',
        tooltip=bi(
            'The closest CPU take on datamosh - the picture flows and tears along its own '
            'motion, smearing moving areas into liquid displacement.',
            'Ближайший CPU-датамош - картинка течёт и рвётся вдоль собственного движения, '
            'размазывая движущиеся области в текучее смещение.',
        ),
    ),
    EffectSpec(
        id='vortex_warp', label='Vortex Warp (spiral)', group='WARP',
        cls=warp.VortexWarpEffect,
        enable_key='fx_vortex_warp', enabled_default=False,
        chance_key='fx_vortex_warp_chance', default_chance=0.4,
        note='BUILD / IMPACT / SUSTAIN / DROP - Gaussian-falloff spiral.',
        tooltip=bi(
            'Pixels swirl around the centre into a spiral - a gentle twist at low intensity, '
            'a full whirlpool collapse at high.',
            'Пиксели закручиваются вокруг центра в спираль - лёгкий завиток на малой '
            'интенсивности, полный «водоворот» на большой.',
        ),
    ),
    EffectSpec(
        id='fractal_warp', label='Fractal Warp (organic)', group='WARP',
        cls=warp.FractalNoiseWarpEffect,
        enable_key='fx_fractal_warp', enabled_default=False,
        chance_key='fx_fractal_warp_chance', default_chance=0.4,
        params=[ParamSpec('fx_fractal_warp_octaves', 'Octaves', 4, 2, 5, kind='int',
                          kwarg='octaves',
                          tooltip=bi(
                              'Number of noise scales summed. 2 = smooth blobs, 5 = jagged '
                              'fractal.',
                              'Сколько октав шума суммируется. 2 - плавные «капли», 5 - рваный '
                              'фрактал.',
                          ))],
        note='Any segment - fBm noise displacement field.',
        tooltip=bi(
            'The image ripples through an organic, ever-shifting noise field - soft billowing '
            'blobs at low octaves, jagged fractal churn at high.',
            'Изображение волнуется в органическом, постоянно меняющемся шумовом поле - мягкие '
            'клубящиеся «капли» на малых октавах, рваная фрактальная толча на больших.',
        ),
    ),
    EffectSpec(
        id='self_displace', label='Self Displace (auto-warp)', group='WARP',
        cls=warp.SelfDisplaceEffect,
        enable_key='fx_self_displace', enabled_default=False,
        chance_key='fx_self_displace_chance', default_chance=0.4,
        params=[ParamSpec('fx_self_displace_depth', 'Depth (frames back)', 2, 1, 8, kind='int',
                          kwarg='depth',
                          tooltip=bi(
                              'How many frames back the displacement source is taken from. '
                              'Larger = more pronounced lag-induced tearing.',
                              'Из какого по глубине прошлого кадра берётся источник смещения. '
                              'Больше - заметнее «разрыв» из-за задержки.',
                          ))],
        note='IMPACT / NOISE / DROP / BUILD / SUSTAIN - past frame is the warp map.',
        tooltip=bi(
            'The image tears itself apart using its own colours as a warp map - flowing, '
            'self-eating distortion that cascades into full datamosh smear with FEEDBACK on.',
            'Изображение разрывает само себя, используя собственные цвета как карту деформации - '
            'текучее, самопожирающее искажение, переходящее в полный датамош-смаз при '
            'включённом FEEDBACK.',
        ),
    ),

    # ── FORMULA (dedicated tab, not in accordion) ──────────────────────
    EffectSpec(
        id='formula', label='Formula (math expression)', group='FORMULA',
        cls=formula.FormulaEffect,
        enable_key='fx_formula', enabled_default=False,
        chance_key='fx_formula_chance', default_chance=0.6,
        params=[
            ParamSpec('fx_formula_expr', 'Expression', 'frame', kind='string', indent=False,
                      tooltip=bi(
                          'NumPy expression. Available: frame, r, g, b, x, y, t, i, a, b, c, d, '
                          'np, cv2, sin, cos, abs, clip. Returns HxWx3 uint8.',
                          'NumPy-выражение. Доступно: frame, r, g, b, x, y, t, i, a, b, c, d, '
                          'np, cv2, sin, cos, abs, clip. Возвращает HxWx3 uint8.',
                      )),
            ParamSpec('fx_formula_blend', 'Blend with original', 0.0, 0.0, 1.0,
                      kwarg='blend',
                      tooltip=bi(
                          '0 = pure formula output, 1 = original frame. In-between cross-fades.',
                          '0 - чистый результат формулы, 1 - исходный кадр. Между - кроссфейд.',
                      )),
            ParamSpec('fx_formula_a', 'a', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider - referenced as `a` inside the formula.',
                          'Живой слайдер - обращайтесь к нему в формуле как к переменной `a`.',
                      )),
            ParamSpec('fx_formula_b', 'b', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider - referenced as `b` inside the formula.',
                          'Живой слайдер - обращайтесь к нему в формуле как к переменной `b`.',
                      )),
            ParamSpec('fx_formula_c', 'c', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider - referenced as `c` inside the formula.',
                          'Живой слайдер - обращайтесь к нему в формуле как к переменной `c`.',
                      )),
            ParamSpec('fx_formula_d', 'd', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider - referenced as `d` inside the formula.',
                          'Живой слайдер - обращайтесь к нему в формуле как к переменной `d`.',
                      )),
        ],
        extra_factory=_formula_extras,
        note='User-defined math expression evaluated per frame.',
        tooltip=bi(
            'Type any NumPy expression that produces an HxWx3 uint8 frame. Sandboxed: only '
            'numpy + safe builtins are exposed.',
            'Введите любое NumPy-выражение, возвращающее кадр HxWx3 uint8. Песочница: '
            'доступны только numpy и безопасные встроенные функции.',
        ),
    ),

    # ── BROKEN (decoder / memory corruption) ───────────────────────────
    EffectSpec(
        id='vsync_roll', label='VSync Roll', group='BROKEN',
        trigger_types=[SegmentType.BUILD, SegmentType.DROP],
        cls=broken_fx.VSyncRollEffect,
        enable_key='fx_vsync_roll', enabled_default=False,
        chance_key='fx_vsync_roll_chance', default_chance=0.4,
        params=[
            ParamSpec('fx_vsync_roll_int', 'Roll Speed', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'How fast the seam crawls and how thick it is. '
                          'Low = slow gentle roll with a hairline tear. '
                          'High = fast scroll with a wide black tear band.',
                          'Скорость движения шва и его толщина. Низко - медленная мягкая '
                          'прокрутка с тонким разрывом. Высоко - быстрый скролл с широкой '
                          'чёрной разрывной полосой.',
                      )),
        ],
        intensity_max_kwarg='intensity_max',
        note='BUILD / DROP - sync loss as a metaphor for "system gives way at peaks".',
        tooltip=bi(
            'The frame is split horizontally and the two halves are stacked in the wrong '
            'order; the cut position drifts up the frame so the seam crawls. A black tear '
            'band (width grows with intensity) marks the cut, the way an old CRT showed '
            'the vertical retrace pulse when it lost vsync lock.',
            'Кадр разрезается по горизонтали и две половины ставятся в обратном порядке; '
            'место разреза дрейфует вверх по кадру, шов «ползёт». Чёрная разрывная полоса '
            '(толщина растёт с интенсивностью) отмечает разрез - как на старом CRT, '
            'когда тот терял vsync-синхронизацию.',
        ),
    ),
    EffectSpec(
        id='pframe_lag', label='P-Frame Lag', group='BROKEN',
        trigger_types=[SegmentType.IMPACT, SegmentType.BUILD, SegmentType.DROP],
        cls=broken_fx.PFrameLagEffect,
        enable_key='fx_pframe_lag', enabled_default=False,
        chance_key='fx_pframe_lag_chance', default_chance=0.5,
        params=[
            ParamSpec('fx_pframe_lag_int', 'Lag Amount', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'How much the picture trails behind the live source. Low = slight '
                          'motion blur. High = heavy "decoder is two frames behind" smear, '
                          'compounding across frames.',
                          'Насколько изображение отстаёт от источника. Низко - лёгкий motion '
                          'blur. Высоко - тяжёлый смаз «декодер отстаёт на два кадра», '
                          'накапливающийся между кадрами.',
                      )),
        ],
        intensity_max_kwarg='intensity_max',
        note='IMPACT / BUILD / DROP - lag is visible only when motion changes.',
        tooltip=bi(
            'The picture lags behind the action and only half-catches-up each frame - moving '
            'objects leave a heavy, compounding motion smear, like a decoder that keeps '
            'dropping frames and never quite redraws.',
            'Картинка отстаёт от происходящего и лишь наполовину «догоняет» каждый кадр - за '
            'движущимися объектами тянется тяжёлый нарастающий смаз, как у декодера, который '
            'теряет кадры и не успевает перерисоваться.',
        ),
    ),
    EffectSpec(
        id='bit_flip', label='Bit Flip (bit rot)', group='BROKEN',
        trigger_types=[SegmentType.SUSTAIN, SegmentType.NOISE],
        cls=broken_fx.BitFlipEffect,
        enable_key='fx_bit_flip', enabled_default=False,
        chance_key='fx_bit_flip_chance', default_chance=0.4,
        params=[
            ParamSpec('fx_bit_flip_int', 'Corruption', 0.4, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'Density of bit-flips and which bit-plane is hit. Low = sparse '
                          'LSB jitter (subtle dithering). High = dense MSB flips '
                          '(catastrophic colour shifts in plateaus of solid colour).',
                          'Плотность бит-флипов и в какой бит-плоскости они идут. Низко - '
                          'редкие LSB-флипы (мягкое дрожание). Высоко - частые MSB-флипы '
                          '(катастрофические цветовые сдвиги по плоским цветовым областям).',
                      )),
            _drive_param('fx_bit_flip', 'high'),
        ],
        intensity_max_kwarg='intensity_max',
        note='SUSTAIN / NOISE - quiet bit rot during steady passages, masked by noise.',
        tooltip=bi(
            'Flat areas of solid colour suddenly break out in sparse wrong-coloured speckles '
            'and blocky colour jumps - the "bit rot" look of failing memory eating the image.',
            'Ровные области сплошного цвета внезапно покрываются редкими «не теми» крапинами и '
            'блочными скачками цвета - вид «bit rot» отказывающей памяти, разъедающей картинку.',
        ),
    ),
    EffectSpec(
        id='wrong_mvec', label='Wrong Motion Vector', group='BROKEN',
        trigger_types=[SegmentType.IMPACT, SegmentType.NOISE],
        cls=broken_fx.WrongMotionVectorEffect,
        enable_key='fx_wrong_mvec', enabled_default=False,
        chance_key='fx_wrong_mvec_chance', default_chance=0.4,
        params=[
            ParamSpec('fx_wrong_mvec_int', 'Block Density', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'Fraction of 16x16 macroblocks corrupted. Low = a few stray '
                          'blocks at random. High = up to 30 percent of the grid replaced '
                          'with displaced content from elsewhere in the frame.',
                          'Доля 16x16 макроблоков, попадающих под порчу. Низко - пара '
                          'случайных блоков. Высоко - до 30 процентов решётки замещены '
                          'смещённым содержимым из других мест кадра.',
                      )),
        ],
        intensity_max_kwarg='intensity_max',
        note='IMPACT / NOISE - codec confusion is purely about motion.',
        tooltip=bi(
            'Blocky chunks of the image pop up in the wrong places, pulled from elsewhere in '
            'the frame - exactly like an H.264 stream with a corrupt motion-vector field.',
            'Блочные куски изображения всплывают не на своих местах, стянутые из других частей '
            'кадра - точь-в-точь H.264-поток с повреждённым motion-vector полем.',
        ),
    ),
    EffectSpec(
        id='self_cannibalize', label='Self Cannibalize', group='BROKEN',
        trigger_types=[SegmentType.BUILD, SegmentType.NOISE],
        cls=broken_fx.SelfCannibalizeEffect,
        enable_key='fx_self_cannibalize', enabled_default=False,
        chance_key='fx_self_cannibalize_chance', default_chance=0.4,
        params=[
            ParamSpec('fx_self_cannibalize_int', 'Density', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'How many cannibal rectangles per frame and how recursive. '
                          'Low values: subtle 1-2 picture-in-picture inserts. High values: '
                          'frame fills with nested self-similar copies.',
                          'Сколько прямоугольников-каннибалов на кадр и насколько рекурсивно. '
                          'Малые значения - едва заметные 1-2 «картинки-в-картинке». Высокие '
                          '- кадр забит вложенными самоподобными копиями.',
                      )),
        ],
        intensity_max_kwarg='intensity_max',
        note='BUILD / NOISE - recursion grows with energy; nothing on quiet/steady.',
        tooltip=bi(
            'Rectangles across the frame fill with shrunken copies of the whole picture, '
            'nesting into itself picture-in-picture - a recursive memory-corruption collapse.',
            'Прямоугольники по кадру заполняются уменьшенными копиями всей картинки, вкладываясь '
            'сами в себя «картинка-в-картинке» - рекурсивный коллапс повреждённой памяти.',
        ),
    ),

    # ── VIRUS (Win95 malware aesthetic) ────────────────────────────────
    EffectSpec(
        id='cursor_storm', label='Cursor Storm', group='VIRUS',
        trigger_types=[SegmentType.SILENCE, SegmentType.SUSTAIN],
        cls=virus_fx.CursorStormEffect,
        enable_key='fx_cursor_storm', enabled_default=False,
        chance_key='fx_cursor_storm_chance', default_chance=0.6,
        params=[
            ParamSpec('fx_cursor_storm_int', 'Swarm Size', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'Number of fake Win95 mouse cursors crawling over the frame and '
                          'how wildly they move. Low: a couple of pointers drifting. '
                          'High: a full infestation of 12+ cursors with jittery trails.',
                          'Сколько fake Win95-курсоров ползает по кадру и насколько резко '
                          'они двигаются. Низко - пара курсоров плывёт. Высоко - рой из '
                          '12+ курсоров со рваными следами.',
                      )),
        ],
        intensity_max_kwarg='intensity_max',
        note='SILENCE / SUSTAIN - eerie infestation; loudest in quiet passages.',
        tooltip=bi(
            'A swarm of authentic 16x22 Win95 arrow cursors is overlaid on every frame, '
            'each pointer following its own brownian-motion path with a short fading '
            'trail. Pointer state is stateful across frames so motion is continuous. '
            'Reads as a 90s machine under malware infestation.',
            'Поверх каждого кадра - рой подлинных Win95-курсоров (16x22), каждый идёт '
            'своей броуновской траекторией с коротким затухающим следом. Состояние '
            'курсоров хранится между кадрами - движение непрерывное. Похоже на '
            'заражённую вирусом машину 90-х.',
        ),
    ),
    EffectSpec(
        id='bsod_shred', label='BSOD Shred', group='VIRUS',
        trigger_types=[SegmentType.IMPACT, SegmentType.DROP],
        cls=virus_fx.BSODShredEffect,
        enable_key='fx_bsod_shred', enabled_default=False,
        chance_key='fx_bsod_shred_chance', default_chance=0.4,
        params=[
            ParamSpec('fx_bsod_shred_int', 'Shred Density', 0.5, 0.0, 1.0,
                      kwarg='intensity_max',
                      tooltip=bi(
                          'How many bluescreen bands to slice into the frame per fire and '
                          'how tall each band is. Low: a single thin band per hit. High: '
                          '5+ thick bands of bluescreen text crowding the picture.',
                          'Сколько синеэкранных полос врезается в кадр за срабатывание и '
                          'насколько они толстые. Низко - одна тонкая полоса на удар. '
                          'Высоко - 5+ толстых полос BSOD-текста забивают картинку.',
                      )),
        ],
        intensity_max_kwarg='intensity_max',
        note='IMPACT / DROP - pure system-crash punctuation on hits.',
        tooltip=bi(
            'Authentic NT-bluescreen palette (RGB 0,0,168) and a vocabulary of real STOP '
            'codes, hex addresses and dump-prose lines are painted into random horizontal '
            'bands of the frame. Stateless per-frame - every frame picks fresh bands so '
            'the effect strobes / shreds.',
            'Канонический фон NT-синего экрана (RGB 0,0,168) и набор реальных STOP-кодов, '
            'hex-адресов и фраз dump-вывода врезаются в случайные горизонтальные полосы '
            'кадра. Без состояния между кадрами - каждый кадр выбирает новые полосы, '
            'поэтому эффект стробит и «шинкует».',
        ),
    ),

    # ── OVERLAYS ───────────────────────────────────────────────────────
    EffectSpec(
        id='overlay', label='Enable Overlays', group='OVERLAYS',
        cls=overlay.OverlayEffect, requires_overlay_dir=True,
        enable_key='fx_overlay', enabled_default=False,
        chance_key='fx_overlay_chance', default_chance=0.5,
        params=[
            ParamSpec('fx_overlay_opacity', 'Opacity', 0.85, 0.0, 1.0, indent=False,
                      tooltip=bi(
                          'Final alpha multiplier. 0.85 looks like a translucent decal.',
                          'Итоговый коэффициент альфы. 0.85 - полупрозрачная «наклейка».',
                      )),
            ParamSpec('fx_overlay_scale', 'Scale Max', 0.4, 0.05, 1.0, indent=False,
                      tooltip=bi(
                          'Maximum size as a fraction of frame height. Intensity interpolates '
                          'between min and max.',
                          'Максимальный размер - доля высоты кадра. Интенсивность '
                          'интерполирует между min и max.',
                      )),
            ParamSpec('fx_overlay_scale_min', 'Scale Min', 0.15, 0.05, 1.0, indent=False,
                      tooltip=bi(
                          'Minimum overlay size at intensity 0.',
                          'Минимальный размер оверлея при интенсивности 0.',
                      )),
            ParamSpec('fx_overlay_blend', 'Blend Mode', 'screen', kind='choice',
                      choices=['screen', 'normal', 'multiply'], indent=False,
                      tooltip=bi(
                          'screen brightens; multiply darkens; normal replaces.',
                          'screen - высветляет; multiply - затемняет; normal - заменяет.',
                      )),
            ParamSpec('fx_overlay_position', 'Position', 'random', kind='choice',
                      choices=['random', 'center', 'random_corner'], indent=False,
                      tooltip=bi(
                          'Where on the frame to place the overlay. Decided once per segment.',
                          'Куда ставить оверлей в кадре. Выбирается один раз на сегмент.',
                      )),
            ParamSpec('fx_overlay_ck_mode', 'Chroma Key Mode', 'none', kind='choice',
                      choices=['none', 'dominant', 'secondary', 'manual'], indent=False,
                      tooltip=bi(
                          'dominant = auto-key the most common hue; secondary = the second most '
                          'common; manual = use the RGB below.',
                          'dominant - авто-ключ по самому частому оттенку; secondary - по '
                          'второму по частоте; manual - по RGB ниже.',
                      )),
            ParamSpec('fx_overlay_ck_tolerance', 'CK Tolerance', 30, 5, 60, kind='int',
                      tooltip=bi(
                          'How wide the keyed hue range is. Higher = more pixels removed.',
                          'Ширина диапазона по оттенку, который вырезается. Больше - больше '
                          'удалённых пикселей.',
                      )),
            ParamSpec('fx_overlay_ck_softness', 'CK Edge Softness', 5, 1, 21, kind='int',
                      tooltip=bi(
                          'Gaussian blur applied to the key mask. Higher = softer edges.',
                          'Гауссово размытие маски ключа. Больше - мягче края.',
                      )),
            ParamSpec('fx_overlay_ck_r', 'Manual Key R', 0, 0, 255, kind='int',
                      tooltip=bi('Manual key colour red component.',
                                 'Красная компонента ручного ключевого цвета.')),
            ParamSpec('fx_overlay_ck_g', 'Manual Key G', 255, 0, 255, kind='int',
                      tooltip=bi('Manual key colour green component.',
                                 'Зелёная компонента ручного ключевого цвета.')),
            ParamSpec('fx_overlay_ck_b', 'Manual Key B', 0, 0, 255, kind='int',
                      tooltip=bi('Manual key colour blue component.',
                                 'Синяя компонента ручного ключевого цвета.')),
        ],
        extra_factory=_overlay_extras,
        note='Composites image/video files from the selected folder onto frames.',
        tooltip=bi(
            'Loads every PNG/JPG/MP4 in the selected folder and chooses one per active '
            'segment. Per-segment scale/position/chroma-key are decided once and held for the '
            'segment duration.',
            'Загружает все PNG/JPG/MP4 из выбранной папки и берёт по одному на активный '
            'сегмент. Размер/позиция/хрома-ключ выбираются один раз на сегмент и держатся '
            'до его конца.',
        ),
    ),

    # ── True Datamosh: между OVERLAYS и PAINT - мошит оверлеи (они уже
    #    в кадре), но не трогает paint/dvd, которые рисуются поверх ──
    EffectSpec(
        id='true_datamosh', label='True Datamosh', group='CORE FX',
        cls=mosh.TrueDatamoshEffect,
        enable_key='fx_truemosh', enabled_default=False,
        chance_key='fx_truemosh_chance', default_chance=0.5,
        params=[
            ParamSpec('fx_truemosh_mode', 'Mosh Mode', 'melt', kind='choice',
                      choices=['melt', 'bloom', 'hybrid'], kwarg='mode', indent=True,
                      tooltip=bi(
                          'melt = I-frames are dropped at cuts, the old scene smears along the new '
                          'scene\'s motion (the canonical datamosh). bloom = one P-frame is decoded '
                          'repeatedly, moving regions grow out of themselves. hybrid = randomly picks '
                          'melt, bloom or both per event.',
                          'melt - I-кадры выбрасываются на склейках, старая сцена размазывается по '
                          'движению новой (канонический датамош). bloom - один P-кадр декодируется '
                          'многократно, движущиеся зоны прорастают сами из себя. hybrid - на каждое '
                          'событие случайно выбирается melt, bloom или оба.',
                      )),
            ParamSpec('fx_truemosh_bloom', 'Bloom Frames', 8, 2, 24, kind='int',
                      kwarg='bloom_frames', indent=True,
                      tooltip=bi(
                          'Maximum length of a bloom burst in frames (the actual length scales with '
                          'segment loudness). Longer = motion compounds further before the stream '
                          'moves on.',
                          'Максимальная длина bloom-вспышки в кадрах (фактическая длина зависит от '
                          'громкости сегмента). Длиннее - движение накапливается дольше, прежде чем '
                          'поток пойдёт дальше.',
                      )),
            ParamSpec('fx_truemosh_crunch', 'Block Crunch', 0.35, 0.0, 1.0,
                      kwarg='crunch', indent=True,
                      tooltip=bi(
                          'Bitrate starvation of the internal MPEG-4 stream. Low = smooth, almost '
                          'painterly melt. High = coarse 16px macroblock soup with visible DCT '
                          'blocks, like a heavily corrupted download.',
                          'Битрейтное голодание внутреннего MPEG-4 потока. Мало - гладкое, почти '
                          'живописное плавление. Много - грубая каша из 16px макроблоков с видимыми '
                          'DCT-блоками, как сильно битая скачка.',
                      )),
        ],
        note='NOISE / SUSTAIN / IMPACT / DROP - real MPEG-4 I-frame-drop mosh.',
        tooltip=bi(
            'The genuine article. A real MPEG-4 encoder and decoder run inside the effect: at '
            'a cut the keyframe is physically removed from the bitstream, and the decoder drags '
            'the old scene along the new scene\'s motion vectors until it patchily heals - the '
            'exact mechanism of classic datamosh tools, not an imitation. Consecutive triggered '
            'segments chain into one continuous datamix melt; the first segment that does not '
            'trigger snaps the picture back clean.',
            'Настоящий датамош. Внутри эффекта работают реальные энкодер и декодер MPEG-4: на '
            'склейке ключевой кадр физически удаляется из битстрима, и декодер тащит старую '
            'сцену по векторам движения новой, пока картинка не «заживёт» пятнами - тот же '
            'механизм, что в классических инструментах датамоша, а не имитация. Подряд идущие '
            'сработавшие сегменты сцепляются в непрерывное datamix-плавление; первый '
            'несработавший сегмент возвращает чистую картинку.',
        ),
    ),

    # ── PAINT ──────────────────────────────────────────────────────────
    EffectSpec(
        id='paint', label='Paint Canvas FX', group='PAINT',
        cls=PaintCanvasEffect,
        enable_key='fx_paint', enabled_default=False,
        chance_key='fx_paint_chance', default_chance=1.0,
        params=[
            ParamSpec('fx_paint_mode', 'Mode', 'lag', kind='choice',
                      choices=['overlay', 'lag', 'warp_video', 'lag_warp'], indent=False,
                      tooltip=bi(
                          'overlay = draw the strokes; lag = frame delay in strokes; '
                          'warp_video = distort video along outlines; lag_warp = lag + warped strokes',
                          'overlay - рисовать линии; lag - задержка кадра в линиях; '
                          'warp_video - искажение видео по контурам; lag_warp - задержка + искажение линий'
                      )),
            ParamSpec('fx_paint_delay', 'Lag Frames', 10, 2, 30, kind='int',
                      kwarg='delay_frames',
                      tooltip=bi(
                          'Number of frames to delay the video inside the strokes.',
                          'Количество кадров задержки видео внутри нарисованных линий.'
                      )),
            ParamSpec('fx_paint_warp_int', 'Warp Intensity', 0.3, 0.0, 1.0,
                      kwarg='warp_intensity',
                      tooltip=bi(
                          'Strength of the distortion applied to the strokes.',
                          'Сила искажения, применяемого к нарисованным линиям.'
                      )),
            ParamSpec('fx_paint_color_r', 'Color R', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_paint_color_g', 'Color G', 255, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_paint_color_b', 'Color B', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_paint_canvas_data', 'Canvas Data', '', kind='string', kwarg='canvas_data', indent=False),
        ],
        extra_factory=_paint_extras,
        note='Open the editor window to draw paint strokes or load outline images.',
        tooltip=bi(
            'Applies drawing strokes as a mask for color overlays, frame delay (lag), or warp distortion.',
            'Применяет рисунок как маску для наложения цвета, задержки кадров (lag) или искажения.'
        ),
    ),

    EffectSpec(
        id='subtitles', label='Subtitles', group='PAINT',
        cls=SubtitleEffect,
        enable_key='fx_subtitles', enabled_default=False,
        chance_key=None,          # субтитры не гейтятся chance - строго по таймкодам
        supports_always=False,    # непрозрачность не зависит от аудио
        params=[
            ParamSpec('fx_subtitles_mode', 'Default Mode', 'overlay', kind='choice',
                      choices=['overlay', 'lag', 'warp_video', 'lag_warp'], indent=False,
                      kwarg='mode',
                      tooltip=bi(
                          'Default mode for cues that do not override it. overlay = solid '
                          'coloured text; lag = delayed video inside the glyphs; '
                          'warp_video = distorted video inside the glyphs; lag_warp = both.',
                          'Режим по умолчанию для реплик без своего. overlay - сплошной '
                          'цветной текст; lag - задержанное видео внутри букв; '
                          'warp_video - искажённое видео внутри букв; lag_warp - и то, и другое.'
                      )),
            ParamSpec('fx_subtitles_delay', 'Lag Frames', 10, 2, 30, kind='int',
                      kwarg='delay_frames',
                      tooltip=bi('Frames of video delay inside glyphs (lag / lag_warp).',
                                 'Кадры задержки видео внутри букв (lag / lag_warp).')),
            ParamSpec('fx_subtitles_warp_int', 'Warp Intensity', 0.3, 0.0, 1.0,
                      kwarg='warp_intensity',
                      tooltip=bi('Strength of distortion for warp_video / lag_warp.',
                                 'Сила искажения для warp_video / lag_warp.')),
            ParamSpec('fx_subtitles_color_r', 'Default R', 255, 0, 255, kind='int',
                      kwarg='color_r', indent=True, tooltip=''),
            ParamSpec('fx_subtitles_color_g', 'Default G', 255, 0, 255, kind='int',
                      kwarg='color_g', indent=True, tooltip=''),
            ParamSpec('fx_subtitles_color_b', 'Default B', 255, 0, 255, kind='int',
                      kwarg='color_b', indent=True, tooltip=''),
            # Шрифт/размер по умолчанию и данные реплик редактируются в окне
            # редактора, в основном блоке не показываются (см. skip-список GUI).
            ParamSpec('fx_subtitles_font', 'Default Font', 'Arial', kind='string',
                      kwarg='font', indent=False),
            ParamSpec('fx_subtitles_size', 'Default Size', 48, 8, 400, kind='int',
                      kwarg='size', indent=False),
            ParamSpec('fx_subtitles_data', 'Cues Data', '', kind='string',
                      kwarg=None, indent=False),
        ],
        extra_factory=_subtitles_extras,
        note='Open the editor to place timed subtitle lines on the canvas.',
        tooltip=bi(
            'Timed subtitle lines placed on the canvas. Each cue has its own timecodes, '
            'position, font, colour and mode; cues may overlap. In a windowed preview the '
            'timecodes follow the source timeline.',
            'Субтитры с таймкодами, расставленные по холсту. У каждой реплики свои таймкоды, '
            'позиция, шрифт, цвет и режим; реплики могут перекрываться. В превью-окне таймкоды '
            'следуют исходному таймлайну.'),
    ),

    # ── VISUALIZER (WINDOWS MEDIA PLAYER - реагирует на аудио) ──────────
    # Каждый рендерер рисует картинку по аудио-полосам текущего кадра (seg.live),
    # а общий Composite Mode решает, как она смешивается с исходным кадром.
    EffectSpec(
        id='viz_bars', label='Spectrum Bars', group='VISUALIZER',
        cls=SpectrumBarsEffect, enable_key='fx_viz_bars', enabled_default=False,
        chance_key='fx_viz_bars_chance', default_chance=1.0,
        params=_viz_mode_params('fx_viz_bars') + [
            ParamSpec('fx_viz_bars_bands', 'Band Count', 24, 4, 64, kind='int', indent=True,
                      kwarg=None,
                      tooltip=bi('Number of equalizer bars.', 'Количество столбиков эквалайзера.')),
            ParamSpec('fx_viz_bars_mirror', 'Mirror', 'off', kind='choice',
                      choices=['off', 'on'], indent=True, kwarg=None,
                      tooltip=bi('Grow bars from the centre instead of the bottom.',
                                 'Растить столбики от центра, а не от низа.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_bars'),
            n_bands=int(cfg.get('fx_viz_bars_bands', 24)),
            mirror=(cfg.get('fx_viz_bars_mirror', 'off') == 'on'),
        ),
        note='Audio-reactive - classic equalizer bars driven by the spectrum.',
        tooltip=bi(
            'Classic WMP equalizer: per-band bars with peak-hold smoothing. Use Composite '
            'Mode to overlay or warp the source instead of replacing it.',
            'Классический эквалайзер WMP: столбики по полосам со сглаживанием peak-hold. '
            'Composite Mode позволяет накладывать или варпить источник вместо замены.'),
    ),
    EffectSpec(
        id='viz_radial', label='Radial Spectrum', group='VISUALIZER',
        cls=RadialSpectrumEffect, enable_key='fx_viz_radial', enabled_default=False,
        chance_key='fx_viz_radial_chance', default_chance=1.0,
        params=_viz_mode_params('fx_viz_radial') + [
            ParamSpec('fx_viz_radial_rays', 'Ray Count', 48, 8, 128, kind='int', indent=True,
                      kwarg=None,
                      tooltip=bi('Number of radial rays around the circle.',
                                 'Количество лучей по кругу.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_radial'),
            rays=int(cfg.get('fx_viz_radial_rays', 48)),
        ),
        note='Audio-reactive - spectrum bars wrapped into a pulsing corona.',
        tooltip=bi(
            'The equalizer wrapped around a circle: each ray length tracks a frequency band, '
            'the whole corona rotates slowly. Strong as a full-screen replace or a warp map.',
            'Эквалайзер, свёрнутый в круг: длина каждого луча следует за полосой частот, вся '
            'корона медленно вращается. Хорош и на весь экран, и как warp-карта.'),
    ),
    EffectSpec(
        id='viz_scope', label='Oscilloscope', group='VISUALIZER',
        cls=OscilloscopeEffect, enable_key='fx_viz_scope', enabled_default=False,
        chance_key='fx_viz_scope_chance', default_chance=1.0,
        params=_viz_mode_params('fx_viz_scope') + [
            ParamSpec('fx_viz_scope_thick', 'Line Thickness', 2, 1, 8, kind='int', indent=True,
                      kwarg=None,
                      tooltip=bi('Scope line thickness in pixels.',
                                 'Толщина линии осциллографа в пикселях.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_scope'),
            thickness=int(cfg.get('fx_viz_scope_thick', 2)),
        ),
        note='Audio-reactive - waveform scope line.',
        tooltip=bi(
            'A horizontal scope line whose amplitude follows the spectrum, scrolling in phase '
            'with time. Cleanest as an over-blend on top of the video.',
            'Горизонтальная линия осциллографа, амплитуда которой следует за спектром и '
            'смещается по фазе со временем. Лучше всего как over-наложение поверх видео.'),
    ),
    EffectSpec(
        id='viz_lissajous', label='Lissajous (XY)', group='VISUALIZER',
        cls=LissajousEffect, enable_key='fx_viz_lissajous', enabled_default=False,
        chance_key='fx_viz_lissajous_chance', default_chance=1.0,
        trigger_types=[SegmentType.SUSTAIN, SegmentType.BUILD],
        params=_viz_mode_params('fx_viz_lissajous') + [
            ParamSpec('fx_viz_lissajous_ratio', 'Frequency Ratio', 3.0, 1.0, 8.0, indent=True,
                      kwarg=None,
                      tooltip=bi('Base X:Y frequency ratio of the figure.',
                                 'Базовое соотношение частот X:Y фигуры.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_lissajous'),
            ratio=float(cfg.get('fx_viz_lissajous_ratio', 3.0)),
        ),
        note='Audio-reactive - XY Lissajous figures.',
        tooltip=bi(
            'XY oscilloscope figures: bass and high steer the two axis frequencies while time '
            'drifts the phase, drawing evolving loops. A retro lab-scope look.',
            'XY-фигуры осциллографа: бас и верх управляют частотами по двум осям, а время '
            'дрейфует фазу, рисуя меняющиеся петли. Ретро-вайб лабораторного осциллографа.'),
    ),
    EffectSpec(
        id='viz_plasma', label='Plasma Field', group='VISUALIZER',
        cls=PlasmaFieldEffect, enable_key='fx_viz_plasma', enabled_default=False,
        chance_key='fx_viz_plasma_chance', default_chance=1.0,
        params=_viz_mode_params('fx_viz_plasma') + [
            ParamSpec('fx_viz_plasma_scale', 'Scale', 0.04, 0.01, 0.15, indent=True,
                      kwarg=None,
                      tooltip=bi('Spatial frequency of the plasma. Higher = finer ripples.',
                                 'Пространственная частота плазмы. Выше - мельче рябь.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_plasma'),
            scale=float(cfg.get('fx_viz_plasma_scale', 0.04)),
        ),
        note='Audio-reactive - procedural plasma; colour & speed from the bands.',
        tooltip=bi(
            'Demoscene plasma built from summed sine fields. Bass shifts the palette, mids '
            'drive the speed, highs the brightness. Use warp mode to ripple the source.',
            'Demoscene-плазма из суммы синусоид. Бас сдвигает палитру, середина задаёт '
            'скорость, верх - яркость. В режиме warp создаёт рябь по источнику.'),
    ),
    EffectSpec(
        id='viz_particles', label='Beat Particles', group='VISUALIZER',
        cls=BeatParticlesEffect, enable_key='fx_viz_particles', enabled_default=False,
        chance_key='fx_viz_particles_chance', default_chance=1.0,
        trigger_types=[SegmentType.IMPACT, SegmentType.DROP],
        params=_viz_mode_params('fx_viz_particles') + [
            ParamSpec('fx_viz_particles_count', 'Particle Count', 120, 16, 512, kind='int', indent=True,
                      kwarg=None,
                      tooltip=bi('Maximum number of particles in the system.',
                                 'Максимальное число частиц в системе.')),
            ParamSpec('fx_viz_particles_grav', 'Gravity', 0.3, 0.0, 1.5, indent=True,
                      kwarg=None,
                      tooltip=bi('Downward pull applied to particles each frame.',
                                 'Сила, тянущая частицы вниз каждый кадр.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_particles'),
            count=int(cfg.get('fx_viz_particles_count', 120)),
            gravity=float(cfg.get('fx_viz_particles_grav', 0.3)),
        ),
        note='Audio-reactive - particle bursts thrown on the beat.',
        tooltip=bi(
            'A particle system emitting from the centre: each detected beat throws a burst '
            'whose size scales with bass, then gravity pulls them down. Great over the video.',
            'Система частиц с эмиссией из центра: каждый бит выбрасывает рой, размер которого '
            'растёт с басом, затем гравитация тянет их вниз. Отлично смотрится поверх видео.'),
    ),
    EffectSpec(
        id='viz_flow', label='Flow Field', group='VISUALIZER',
        cls=FlowFieldEffect, enable_key='fx_viz_flow', enabled_default=False,
        chance_key='fx_viz_flow_chance', default_chance=1.0,
        trigger_types=[SegmentType.SUSTAIN, SegmentType.BUILD],
        params=_viz_mode_params('fx_viz_flow') + [
            ParamSpec('fx_viz_flow_noise', 'Flow Scale', 0.02, 0.005, 0.08, indent=True,
                      kwarg=None,
                      tooltip=bi('Spatial scale of the flow turbulence. Higher = tighter swirls.',
                                 'Пространственный масштаб турбулентности потока. Выше - туже завитки.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_flow'),
            noise_scale=float(cfg.get('fx_viz_flow_noise', 0.02)),
        ),
        note='Audio-reactive - thousands of particles tracing a turbulent flow field.',
        tooltip=bi(
            'A cloud of particles advected along a slowly evolving turbulent vector field, each '
            'leaving a fading trail so the streamlines reveal the flow. Mids and bass drive the '
            'flow speed; the current bands tint the ink. Flow Scale sets the swirl tightness.',
            'Облако частиц, переносимых по медленно эволюционирующему турбулентному векторному '
            'полю; каждая оставляет затухающий след, и линии тока проявляют форму потока. '
            'Середина и бас задают скорость; полосы окрашивают «чернила». Flow Scale - плотность завитков.'),
    ),
    EffectSpec(
        id='viz_alchemy', label='Alchemy', group='VISUALIZER',
        cls=AlchemyEffect, enable_key='fx_viz_alchemy', enabled_default=False,
        chance_key='fx_viz_alchemy_chance', default_chance=1.0,
        trigger_types=[SegmentType.SUSTAIN, SegmentType.BUILD],
        params=_viz_mode_params('fx_viz_alchemy') + [
            ParamSpec('fx_viz_alchemy_symmetry', 'Symmetry', 6, 2, 12, kind='int', indent=True,
                      kwarg=None,
                      tooltip=bi('Number of petals / fold symmetry of the rose.',
                                 'Число лепестков / кратность симметрии розетки.')),
            ParamSpec('fx_viz_alchemy_zoom', 'Feedback Zoom', 1.035, 1.0, 1.09, indent=True,
                      kwarg=None,
                      tooltip=bi('Per-frame zoom of the feedback tunnel. Higher = faster outward rush.',
                                 'Покадровый зум feedback-тоннеля. Выше - быстрее «наплыв» наружу.')),
            ParamSpec('fx_viz_alchemy_spin', 'Feedback Spin', 2.0, 0.0, 6.0, indent=True,
                      kwarg=None,
                      tooltip=bi('Per-frame rotation of the tunnel, in degrees. Drives the spiral twist.',
                                 'Покадровый поворот тоннеля в градусах. Задаёт закрутку спирали.')),
        ],
        extra_factory=lambda cfg: dict(
            **_viz_extras_base(cfg, 'fx_viz_alchemy'),
            symmetry=int(cfg.get('fx_viz_alchemy_symmetry', 6)),
            zoom=float(cfg.get('fx_viz_alchemy_zoom', 1.035)),
            spin=float(cfg.get('fx_viz_alchemy_spin', 2.0)),
        ),
        note='Audio-reactive - WMP "Alchemy" feedback spiral tunnel with a spectrum rose.',
        tooltip=bi(
            'A video-feedback "liquid light" field: each frame the previous image is rotated, '
            'zoomed and dimmed, then a radially symmetric rose whose petals track the spectrum is '
            'drawn on top. The compounding rotate+zoom becomes an endless glowing spiral tunnel '
            'with a slowly cycling hue. Strong as a full-screen replace or a warp map.',
            'Поле видео-обратной связи в духе «жидкого света»: каждый кадр предыдущее изображение '
            'поворачивается, увеличивается и притухает, а поверх рисуется радиально-симметричная '
            'розетка, лепестки которой следуют за спектром. Накапливающийся поворот+зум даёт '
            'бесконечный светящийся спиральный тоннель с плавно меняющимся оттенком. Хорош на '
            'весь экран и как warp-карта.'),
    ),

    # ── VIRUS: DVD применяется последним - поверх всех эффектов ──
    EffectSpec(
        id='dvd_bounce', label='DVD Screensaver', group='VIRUS',
        trigger_types=[SegmentType.SILENCE, SegmentType.SUSTAIN],
        cls=virus_fx.DVDBounceEffect,
        enable_key='fx_dvd_bounce', enabled_default=False,
        chance_key='fx_dvd_bounce_chance', default_chance=1.0,
        params=[
            ParamSpec('fx_dvd_bounce_int', 'Size', 0.5, 0.0, 1.0,
                      kwarg='size',
                      tooltip=bi(
                          'Size of the bouncing logo relative to frame height. Low: a small '
                          'travelling logo. High: a large logo filling much of the frame. '
                          'Independent of audio and of the always-on intensity.',
                          'Размер летающего логотипа относительно высоты кадра. Низко - '
                          'маленький логотип. Высоко - крупный, занимающий заметную часть кадра. '
                          'Не зависит от аудио и от интенсивности always-on.',
                      )),
            ParamSpec('fx_dvd_speed', 'Speed', 4.0, 1.0, 12.0, kind='float',
                      kwarg='speed',
                      tooltip=bi(
                          'Travel speed of the logo in pixels per frame. Low: slow drift. '
                          'High: fast bouncing.',
                          'Скорость движения логотипа, пикселей за кадр. Низко - медленный дрейф. '
                          'Высоко - быстрые отскоки.',
                      )),
            ParamSpec('fx_dvd_color_mode', 'Color Mode', 'cycle', kind='choice',
                      choices=['cycle', 'mono', 'custom', 'lag'], kwarg='color_mode',
                      tooltip=bi(
                          'cycle: logo changes colour on every wall hit (classic). '
                          'mono: no tint (white built-in logo or the image as-is). '
                          'custom: a single fixed colour of your choice. '
                          'lag: the silhouette is filled with a frozen frame snapshotted at '
                          'each wall hit - a moving window into the past.',
                          'cycle: цвет меняется при каждом ударе о стену (классика). '
                          'mono: без тонировки (белый встроенный логотип или картинка как есть). '
                          'custom: один постоянный цвет на ваш выбор. '
                          'lag: силуэт заполняется замороженным кадром, снятым при каждом ударе '
                          'о стену, - движущееся окно в прошлое.',
                      )),
            ParamSpec('fx_dvd_col_r', 'Custom Red', 0, 0, 255, kind='int',
                      kwarg='color_r', indent=True,
                      tooltip=bi('Red of the custom tint (Color Mode = custom).',
                                 'Красная компонента постоянного цвета (Color Mode = custom).')),
            ParamSpec('fx_dvd_col_g', 'Custom Green', 200, 0, 255, kind='int',
                      kwarg='color_g', indent=True,
                      tooltip=bi('Green of the custom tint (Color Mode = custom).',
                                 'Зелёная компонента постоянного цвета (Color Mode = custom).')),
            ParamSpec('fx_dvd_col_b', 'Custom Blue', 255, 0, 255, kind='int',
                      kwarg='color_b', indent=True,
                      tooltip=bi('Blue of the custom tint (Color Mode = custom).',
                                 'Синяя компонента постоянного цвета (Color Mode = custom).')),
            ParamSpec('fx_dvd_logo_path', 'Select DVD Logo…', '', kind='file',
                      indent=False,
                      tooltip=bi(
                          'Pick a PNG (transparency recommended) to replace the built-in DVD '
                          'logo. Leave empty for the classic logo.',
                          'Выберите PNG (лучше с прозрачностью), чтобы заменить встроенный '
                          'DVD-логотип. Оставьте пустым для классического логотипа.',
                      )),
        ],
        extra_factory=_dvd_extras,
        note='SILENCE / SUSTAIN - idle-machine screensaver drifting over the frame.',
        tooltip=bi(
            'The classic bouncing DVD logo drifts across the frame and reflects off every '
            'edge; a rare exact corner hit triggers a short glow of euphoria. Position and '
            'velocity are stateful across frames so motion is continuous. Swap the logo for '
            'your own PNG and pick how it recolours.',
            'Классический DVD-логотип летает по кадру и отражается от краёв; редкое точное '
            'попадание в угол вызывает короткую вспышку-эйфорию. Позиция и скорость хранятся '
            'между кадрами - движение непрерывное. Логотип можно заменить своим PNG и выбрать '
            'режим смены цвета.',
        ),
    ),
]


# ──────────────────────────────────────────────────────────────────────────
#   Хелперы поиска и обхода
# ──────────────────────────────────────────────────────────────────────────


_BY_ID = {s.id: s for s in EFFECTS}


def find_spec(effect_id: str) -> Optional[EffectSpec]:
    return _BY_ID.get(effect_id)


def iter_cfg_keys() -> Iterable[Tuple[str, Any]]:
    """Yield (cfg_key, default_value) for every key the registry expects.

    Includes enable flag, chance, every param, and the per-effect always-on
    override pair (`fx_xxx_always`, `fx_xxx_always_int`) for any effect that
    supports it.
    """
    for s in EFFECTS:
        if s.enable_key:
            yield s.enable_key, s.enabled_default
        if s.chance_key:
            yield s.chance_key, s.default_chance
        for p in s.params:
            yield p.key, p.default
        if s.supports_always_for_chain():
            yield s.always_key, False
            yield s.always_int_key, 0.6


# ──────────────────────────────────────────────────────────────────────────
#   Сторона движка: собрать цепочку эффектов из плоского cfg-словаря
# ──────────────────────────────────────────────────────────────────────────


def build_chain(cfg: dict) -> List[BaseEffect]:
    """Собрать упорядоченную цепочку эффектов для рендера из плоского cfg-словаря.

    Эффекты идут в том же порядке, что и в EFFECTS, поэтому цепное поведение
    пресетов (например Cascade после отдельных глитчей) сохраняется.

    `chance` масштабируется через chaos_level по формуле
    `base * (0.3 + 0.7 * chaos)` для каждого эффекта, который это поддерживает.

    Per-effect always-on:
        cfg[fx_xxx_always]      - если True, этот эффект:
                                  · игнорирует trigger_types (срабатывает на каждом сегменте),
                                  · игнорирует слайдер chance (chance = 1.0),
                                  · использует фиксированную интенсивность (без аудио-скейлинга).
        cfg[fx_xxx_always_int]  - эта фиксированная интенсивность, в диапазоне [0, 1].
        На остальные эффекты цепочки этот override не влияет.
    """
    chaos = float(cfg.get('chaos_level', 0.5))
    chain: List[BaseEffect] = []

    for spec in EFFECTS:
        if spec.cls is None or spec.chain_kind != 'normal':
            continue
        if not cfg.get(spec.enable_key, False):
            continue
        if spec.requires_overlay_dir and not cfg.get('overlay_dir'):
            continue

        always_on = (spec.supports_always_for_chain()
                     and bool(cfg.get(spec.always_key, False)))

        # В always-on режиме chance всегда 1.0
        if always_on:
            chance = 1.0
        elif spec.chance_key is None:
            chance = 1.0
        else:
            base = float(cfg.get(spec.chance_key, spec.default_chance))
            chance = _chance_scale(chaos, base) if spec.chance_scaled_by_chaos else base

        kw = spec.build_kwargs(cfg)
        kw.update(enabled=True, chance=chance)

        try:
            fx = spec.cls(**kw)
        except TypeError:
            sane = {k: v for k, v in kw.items()
                    if k in spec.cls.__init__.__code__.co_varnames}
            fx = spec.cls(**sane)

        if always_on:
            # Игнорируем триггеры, фиксируем интенсивность на значении из настроек.
            fixed = float(cfg.get(spec.always_int_key, 0.6))
            fx.trigger_types = list(SegmentType)
            fx.intensity_min = fixed
            fx.intensity_max = fixed
            fx.chance = 1.0
        elif spec.trigger_types is not None:
            fx.trigger_types = list(spec.trigger_types)

        # Общая обвязка аудио-реактивности. Дефолты повторяют текущее
        # поведение, так что эффекты без этих параметров (и старые пресеты)
        # не затрагиваются. Контрол в GUI появляется только у эффектов,
        # объявивших соответствующий ParamSpec.
        fx.audio_drive = cfg.get(spec.enable_key + '_drive', 'segment')
        fx.beat_gate = cfg.get(spec.enable_key + '_gate', 'off')
        _react_val = cfg.get(spec.enable_key + '_react', 'off')
        fx.react = (_react_val is True) or (str(_react_val).lower() == 'on')

        chain.append(fx)

    return chain


def default_cfg() -> dict:
    """Return a flat dict of every default value the registry provides.

    Used by GUI to populate Tk vars and by tests to build minimal configs.
    """
    cfg: dict = {}
    for k, v in iter_cfg_keys():
        cfg[k] = v
    # RGB-дефолты для composite - хранятся в cfg списками (совместимость со старым форматом)
    cfg['fx_ascii_fg'] = [
        cfg.get('fx_ascii_fg_r', 0), cfg.get('fx_ascii_fg_g', 255), cfg.get('fx_ascii_fg_b', 0)]
    cfg['fx_ascii_bg'] = [
        cfg.get('fx_ascii_bg_r', 0), cfg.get('fx_ascii_bg_g', 0), cfg.get('fx_ascii_bg_b', 0)]
    cfg['fx_overlay_ck_color'] = [
        cfg.get('fx_overlay_ck_r', 0), cfg.get('fx_overlay_ck_g', 255), cfg.get('fx_overlay_ck_b', 0)]
    return cfg
