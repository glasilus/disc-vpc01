"""Declarative effect registry.

Single source of truth for every effect: id, parameters, defaults, ranges,
trigger types, GUI labels, tooltips. Engine, GUI and config validation all
read from this list — adding a new effect means adding one EffectSpec entry.

Public API:
    EFFECTS              — list[EffectSpec], every registered effect
    GROUP_ORDER          — list[str], display order of groups in GUI
    find_spec(effect_id) — lookup helper
    build_chain(cfg)     — turn a flat cfg dict into a list[BaseEffect]
    iter_cfg_keys()      — yield every cfg key the registry expects
    default_cfg()        — flat dict of all defaults, GUI-ready

Backward compatibility: the cfg keys produced (fx_xxx, fx_xxx_chance, ...)
are identical to those used by the original flat engine — old presets still
load.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Tuple

from vpc.analyzer import SegmentType
from .effects.base import BaseEffect
from .effects import core, glitch, degradation, complex_fx, signal, warp, overlay, formula


# ──────────────────────────────────────────────────────────────────────────
#   Param + Effect specs
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class ParamSpec:
    """Description of one tunable parameter of an effect."""
    key: str                       # cfg-dict key, e.g. 'fx_psort_int'
    label: str                     # GUI label, e.g. 'Pixel Sort Intensity'
    default: Any                   # default value
    lo: float = 0.0                # GUI slider min
    hi: float = 1.0                # GUI slider max
    kind: str = 'float'            # 'float' | 'int' | 'choice' | 'rgb' | 'string'
    choices: Optional[List[str]] = None      # for kind='choice'
    kwarg: Optional[str] = None    # ctor kwarg name (None = not passed to ctor)
    indent: bool = True            # GUI: indent under main checkbox
    tooltip: str = ''              # short description of how it shapes output


@dataclass
class EffectSpec:
    """Description of one effect — source of truth for engine + GUI."""
    id: str                                  # stable id, e.g. 'pixel_sort'
    label: str                               # GUI display name
    group: str                               # GUI group, e.g. 'CORE FX'
    cls: Optional[type] = None               # effect class (None for "special" entries)
    enable_key: str = ''                     # cfg flag key, e.g. 'fx_psort'
    enabled_default: bool = False
    chance_key: Optional[str] = None         # cfg chance key; None = always-on (chance=1.0)
    default_chance: float = 0.5
    params: List[ParamSpec] = field(default_factory=list)
    trigger_types: Optional[List[SegmentType]] = None  # None → use class default
    note: str = ''                           # short inline note in GUI
    tooltip: str = ''                        # long description for hover/[?] popup
    chance_scaled_by_chaos: bool = True      # apply _ch() scaling to chance
    chain_kind: str = 'normal'               # 'normal' (added to chain) | 'special' (engine-handled) | 'mystery'
    requires_overlay_dir: bool = False
    extra_factory: Optional[Callable[[dict], dict]] = None
    # extra_factory(cfg) returns extra kwargs (e.g. overlay_frames, chroma_key)
    intensity_max_kwarg: Optional[str] = None  # if param like fx_xxx_int maps to intensity_max
    supports_always: bool = True             # whether the per-effect "always-on" override applies

    # ── always-on cfg keys (auto-derived from enable_key) ──
    @property
    def always_key(self) -> str:
        """cfg key for the per-effect always-on flag (e.g. 'fx_psort_always')."""
        return self.enable_key + '_always' if self.enable_key else ''

    @property
    def always_int_key(self) -> str:
        """cfg key for the fixed intensity used when always-on is active."""
        return self.enable_key + '_always_int' if self.enable_key else ''

    # ----- helpers -----
    def supports_always_for_chain(self) -> bool:
        """True iff the per-effect always-on override is meaningful for this spec."""
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
        """Construct ctor kwargs from a flat cfg dict."""
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
#   Group order for GUI
# ──────────────────────────────────────────────────────────────────────────

GROUP_ORDER: List[str] = [
    'CUT LOGIC',
    'CORE FX',
    'GLITCH',
    'DEGRADATION',
    'COMPLEX',
    'SIGNAL DOMAIN',
    'WARP',
    'OVERLAYS',
    'FORMULA',           # rendered as its own dedicated tab, not in the accordion
]


# Groups that the effects accordion should NOT render (they get a dedicated tab).
ACCORDION_HIDDEN_GROUPS = {'FORMULA'}


# ──────────────────────────────────────────────────────────────────────────
#   Helpers for tedious chance/int param patterns
# ──────────────────────────────────────────────────────────────────────────


def _chance_scale(chaos: float, base: float) -> float:
    """Mirror the original engine's chaos-chance formula."""
    return min(1.0, base * (0.3 + 0.7 * float(chaos)))


def bi(en: str, ru: str) -> str:
    """Build a bilingual tooltip. EN goes first, RU after the dividing line.

    The dedicated divider lets a future GUI language switcher slice the string
    cleanly. Until then, both languages render in the same balloon.
    """
    return f'{en}\n──\n{ru}'


# ──────────────────────────────────────────────────────────────────────────
#   Overlay extras factory — needs overlay frames + ChromaKey from cfg
# ──────────────────────────────────────────────────────────────────────────


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
    return dict(sort_axis=cfg.get('fx_psort_axis', 'luminance'))


def _formula_extras(cfg: dict) -> dict:
    return dict(
        expression=cfg.get('fx_formula_expr', 'frame'),
        a=float(cfg.get('fx_formula_a', 0.5)),
        b=float(cfg.get('fx_formula_b', 0.5)),
        c=float(cfg.get('fx_formula_c', 0.5)),
        d=float(cfg.get('fx_formula_d', 0.5)),
    )


# ──────────────────────────────────────────────────────────────────────────
#   THE REGISTRY — every tunable effect lives here
# ──────────────────────────────────────────────────────────────────────────


EFFECTS: List[EffectSpec] = [

    # ── CORE FX ────────────────────────────────────────────────────────
    EffectSpec(
        id='stutter', label='Stutter / Drill', group='CORE FX',
        cls=None, chain_kind='special',
        enable_key='fx_stutter', enabled_default=True,
        chance_key=None,
        note='IMPACT segments — repeats short hits 2/4/8× for drillcore stutter.',
        tooltip=bi(
            'Triggers when an IMPACT segment is shorter than 0.3 s. Repeats the frame '
            '2/4/8 times. Higher CHAOS makes it fire more often. Engine-controlled — '
            'no chance slider.',
            'Срабатывает на коротких IMPACT-сегментах (<0.3 с). Повторяет кадр 2/4/8 раз. '
            'Чем выше CHAOS — тем чаще. Управляется движком — без отдельного слайдера шанса.',
        ),
    ),

    EffectSpec(
        id='flash', label='Flash Frame', group='CORE FX',
        cls=core.FlashEffect, chain_kind='special',
        enable_key='fx_flash', enabled_default=True,
        chance_key='fx_flash_chance', default_chance=0.8,
        note='DROP / IMPACT — injects a 1-2 frame full-white/black flash.',
        tooltip=bi(
            'On DROP or IMPACT, inserts a black or white frame before the segment plays. '
            'Higher CHANCE = more flashes. At high values it strobes.',
            'На DROP или IMPACT вставляет чёрный или белый кадр перед сегментом. '
            'Выше CHANCE — больше вспышек. На высоких значениях — стробоскоп.',
        ),
    ),

    EffectSpec(
        id='ghost', label='Ghost Trails', group='CORE FX',
        cls=core.GhostTrailsEffect,
        enable_key='fx_ghost', enabled_default=False,
        chance_key=None,                 # always-on when enabled
        params=[ParamSpec('fx_ghost_int', 'Opacity', 0.5, 0.0, 1.0,
                          kwarg='intensity_max', indent=False,
                          tooltip=bi(
                              'Higher = more bleed from the previous frame; <0.3 a subtle smear, '
                              '>0.7 a heavy ghost echo.',
                              'Выше — сильнее просвечивает предыдущий кадр; <0.3 — лёгкий смаз, '
                              '>0.7 — выраженное «эхо».',
                          ))],
        intensity_max_kwarg='intensity_max',
        note='SUSTAIN / BUILD — always on when enabled.',
        tooltip=bi(
            'Cross-fades the current frame with the previous frame. Combine with FEEDBACK '
            'for compounding smear.',
            'Смешивает текущий кадр с предыдущим. В паре с FEEDBACK даёт нарастающий смаз.',
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
                          'Выше — больше полос и они шире. >0.7 — кадр превращается в цветные '
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
        ],
        extra_factory=_psort_extras,
        note='NOISE / IMPACT / DROP — sorts horizontal strips of pixels.',
        tooltip=bi(
            'Picks N horizontal strips per frame and re-orders columns by the selected axis. '
            'Classic pixel-sort glitch art.',
            'Выбирает N горизонтальных полос и переупорядочивает столбцы по выбранному каналу. '
            'Классический pixel-sort.',
        ),
    ),

    EffectSpec(
        id='datamosh', label='Datamosh', group='CORE FX',
        cls=core.DatamoshEffect,
        enable_key='fx_datamosh', enabled_default=False,
        chance_key='fx_datamosh_chance', default_chance=0.5,
        note='NOISE — optical-flow smear, plus real I-frame drop in Final mode.',
        tooltip=bi(
            'Computes the optical flow between current and previous frame and uses it to drag '
            'the previous one across the current. In Final render the engine ALSO pre-bakes a '
            'P-frame-only source and uses it on NOISE segments — that gives the "real" datamosh '
            'look.',
            'Считает оптический поток между текущим и предыдущим кадрами и тянет предыдущий по '
            'этому полю. В режиме Final движок также пред-собирает источник без ключевых кадров '
            'и применяет его на NOISE — это и есть «настоящий» datamosh.',
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
                          'Высота ячейки в пикселях. Меньше — больше деталей и медленнее. >20 — '
                          'грубый терминальный вид.',
                      )),
            ParamSpec('fx_ascii_blend', 'Blend (0=ASCII, 1=overlay)', 0.0, 0.0, 1.0,
                      kwarg='blend',
                      tooltip=bi(
                          '0 = pure ASCII, 1 = original frame visible, in-between mixes them.',
                          '0 — чистый ASCII, 1 — виден исходный кадр, между — смешение.',
                      )),
            ParamSpec('fx_ascii_color_mode', 'Color Mode', 'fixed', kind='choice',
                      choices=['fixed', 'original', 'inverted'], indent=True,
                      tooltip=bi(
                          'fixed = fg/bg colours; original = character coloured by source pixel; '
                          'inverted = 255 − source.',
                          'fixed — заданные fg/bg; original — символ цвета исходного пикселя; '
                          'inverted — инвертированный исходный.',
                      )),
            ParamSpec('fx_ascii_fg_r', 'FG Red', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_fg_g', 'FG Green', 255, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_fg_b', 'FG Blue', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_bg_r', 'BG Red', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_bg_g', 'BG Green', 0, 0, 255, kind='int', indent=True, tooltip=''),
            ParamSpec('fx_ascii_bg_b', 'BG Blue', 0, 0, 255, kind='int', indent=True, tooltip=''),
        ],
        extra_factory=_ascii_extras,
        note='SUSTAIN / SILENCE / BUILD — full-frame ASCII art.',
        tooltip=bi(
            'Replaces every CHAR_SIZE×CHAR_SIZE block with a character whose density matches '
            'block brightness. fg/bg colours and BLEND control the look.',
            'Заменяет каждый блок CHAR_SIZE×CHAR_SIZE символом, плотность которого соответствует '
            'яркости блока. Внешний вид задают цвета fg/bg и BLEND.',
        ),
    ),

    # ── GLITCH ─────────────────────────────────────────────────────────
    EffectSpec(
        id='rgb_shift', label='RGB Shift', group='GLITCH',
        cls=glitch.RGBShiftEffect,
        enable_key='fx_rgb', enabled_default=True,
        chance_key='fx_rgb_chance', default_chance=0.7,
        note='IMPACT / BUILD / NOISE / DROP — colour fringing.',
        tooltip=bi(
            'Shifts R right and B left by an intensity-driven amount. Higher CHANCE = '
            'more affected frames.',
            'Сдвигает канал R вправо и B влево на величину, зависящую от интенсивности. '
            'Выше CHANCE — больше затронутых кадров.',
        ),
    ),
    EffectSpec(
        id='block_glitch', label='Block Glitch', group='GLITCH',
        cls=glitch.BlockGlitchEffect,
        enable_key='fx_block_glitch', enabled_default=False,
        chance_key='fx_block_glitch_chance', default_chance=0.5,
        note='IMPACT / DROP / NOISE — random 16px blocks corrupted.',
        tooltip=bi(
            'Replaces random 16×16 blocks with pixels copied from elsewhere in the frame or '
            'a flat colour. Looks like macroblock corruption.',
            'Заменяет случайные блоки 16×16 либо пикселями из другого места кадра, либо '
            'однотонным цветом. Похоже на повреждение макроблоков.',
        ),
    ),
    EffectSpec(
        id='pixel_drift', label='Pixel Drift', group='GLITCH',
        cls=glitch.PixelDriftEffect,
        enable_key='fx_pixel_drift', enabled_default=False,
        chance_key='fx_pixel_drift_chance', default_chance=0.5,
        note='NOISE / IMPACT — rows slide using simplex noise.',
        tooltip=bi(
            'Each row is rolled left/right by an opensimplex-noise value. Smooth, organic '
            'horizontal slicing.',
            'Каждая строка сдвигается влево/вправо на величину opensimplex-шума. Плавный '
            'органичный горизонтальный «слайс».',
        ),
    ),
    EffectSpec(
        id='colorbleed', label='Color Bleed / VHS Smear', group='GLITCH',
        cls=glitch.ColorBleedEffect,
        enable_key='fx_colorbleed', enabled_default=False,
        chance_key='fx_colorbleed_chance', default_chance=0.5,
        note='NOISE / SUSTAIN — horizontal colour smear on one channel.',
        tooltip=bi(
            'Picks one channel at random and box-blurs it horizontally — VHS-tape colour bleed.',
            'Случайно выбирает один цветовой канал и горизонтально размывает его — «цветной '
            'смаз» VHS-плёнки.',
        ),
    ),
    EffectSpec(
        id='freeze_corrupt', label='Freeze + Corrupt', group='GLITCH',
        cls=glitch.FreezeCorruptEffect,
        enable_key='fx_freeze_corrupt', enabled_default=False,
        chance_key='fx_freeze_corrupt_chance', default_chance=0.3,
        note='DROP — freezes frame for a few ticks and corrupts it.',
        tooltip=bi(
            'Holds a single frame for several ticks and runs Block Glitch on the held image. '
            'Strong DROP punctuation.',
            'Удерживает один кадр на несколько тактов и применяет к нему Block Glitch. Резкий '
            'акцент на DROP.',
        ),
    ),
    EffectSpec(
        id='negative', label='Negative', group='GLITCH',
        cls=glitch.NegativeEffect,
        enable_key='fx_negative', enabled_default=False,
        chance_key='fx_negative_chance', default_chance=0.2,
        note='IMPACT / DROP / NOISE — full colour invert.',
        tooltip=bi(
            '255 − pixel for every channel. Use sparingly — at high CHANCE it strobes.',
            '255 − значение пикселя по каждому каналу. Используйте умеренно — на высоком CHANCE '
            'превращается в стробоскоп.',
        ),
    ),

    # ── DEGRADATION ────────────────────────────────────────────────────
    EffectSpec(
        id='scanlines', label='Scan Lines', group='DEGRADATION',
        cls=degradation.ScanLinesEffect,
        enable_key='fx_scanlines', enabled_default=False,
        chance_key='fx_scanlines_chance', default_chance=0.8,
        note='SUSTAIN / NOISE — CRT scanline darkening.',
        tooltip=bi(
            'Darkens every Nth row. Higher intensity = darker and denser lines.',
            'Затемняет каждую N-ю строку. Выше интенсивность — темнее и плотнее линии.',
        ),
    ),
    EffectSpec(
        id='bitcrush', label='Bitcrush / Posterize', group='DEGRADATION',
        cls=degradation.BitcrushEffect,
        enable_key='fx_bitcrush', enabled_default=False,
        chance_key='fx_bitcrush_chance', default_chance=0.5,
        note='Any segment — reduces colour depth.',
        tooltip=bi(
            'Drops the lowest bits per channel — maps 8-bit colour to 1-7 levels. Extreme '
            'posterise look.',
            'Отбрасывает младшие биты в каждом канале — 8-битный цвет превращается в 1-7 '
            'уровней. Резкая постеризация.',
        ),
    ),
    EffectSpec(
        id='jpeg_crush', label='JPEG Crush', group='DEGRADATION',
        cls=degradation.JPEGCrushEffect,
        enable_key='fx_jpeg_crush', enabled_default=False,
        chance_key='fx_jpeg_crush_chance', default_chance=0.5,
        note='IMPACT / NOISE — heavy JPEG re-encode artefacts.',
        tooltip=bi(
            'Re-encodes the frame as JPEG at quality 1-40 (intensity-driven) and decodes back. '
            'Block-edge artefacts everywhere.',
            'Перекодирует кадр в JPEG c качеством 1-40 (зависит от интенсивности) и декодирует '
            'обратно. По всем границам блоков — артефакты.',
        ),
    ),
    EffectSpec(
        id='fisheye', label='Fisheye / Barrel', group='DEGRADATION',
        cls=degradation.FisheyeEffect,
        enable_key='fx_fisheye', enabled_default=False,
        chance_key='fx_fisheye_chance', default_chance=0.3,
        note='BUILD / SUSTAIN — barrel lens distortion.',
        tooltip=bi(
            'Bulges the image outward from the centre. Keep CHANCE low — visually heavy.',
            'Выпучивает изображение от центра. Держите CHANCE низким — эффект тяжёлый визуально.',
        ),
    ),
    EffectSpec(
        id='vhs', label='VHS Tracking', group='DEGRADATION',
        cls=degradation.VHSTrackingEffect,
        enable_key='fx_vhs', enabled_default=False,
        chance_key='fx_vhs_chance', default_chance=0.5,
        note='NOISE / DROP — tape tracking error: shifted strips + luminance noise.',
        tooltip=bi(
            'Shifts horizontal strips by simplex-noise amounts and adds per-pixel luminance '
            'noise. Authentic VHS tracking glitch.',
            'Сдвигает горизонтальные полосы на величины opensimplex-шума и добавляет точечный '
            'шум яркости. Аутентичный «съезд» VHS-трекинга.',
        ),
    ),
    EffectSpec(
        id='interlace', label='Interlace', group='DEGRADATION',
        cls=degradation.InterlaceEffect,
        enable_key='fx_interlace', enabled_default=False,
        chance_key='fx_interlace_chance', default_chance=0.4,
        note='SUSTAIN — odd rows from previous frame.',
        tooltip=bi(
            'Even rows = current frame, odd rows = previous frame. Authentic 50i look on motion.',
            'Чётные строки — текущий кадр, нечётные — предыдущий. Похоже на чересстрочный 50i на '
            'движении.',
        ),
    ),
    EffectSpec(
        id='bad_signal', label='Bad Signal', group='DEGRADATION',
        cls=degradation.BadSignalEffect,
        enable_key='fx_bad_signal', enabled_default=False,
        chance_key='fx_bad_signal_chance', default_chance=0.3,
        note='DROP / NOISE — vertical noise bars + row shifts.',
        tooltip=bi(
            'Sprays random-coloured vertical bars and rolls random rows horizontally. Digital '
            'signal breakup.',
            'Раскидывает случайно окрашенные вертикальные полосы и горизонтально смещает '
            'случайные строки. Срыв цифрового сигнала.',
        ),
    ),
    EffectSpec(
        id='dither', label='Dithering', group='DEGRADATION',
        cls=degradation.DitheringEffect,
        enable_key='fx_dither', enabled_default=False,
        chance_key='fx_dither_chance', default_chance=0.4,
        note='SILENCE / SUSTAIN — Bayer 4×4 ordered dither.',
        tooltip=bi(
            'Quantises to 2-16 levels per channel through a 4×4 Bayer matrix. Pixel-art / '
            'GameBoy palette feel.',
            'Квантует к 2-16 уровням на канал через матрицу Bayer 4×4. Атмосфера pixel-art / '
            'палитры GameBoy.',
        ),
    ),
    EffectSpec(
        id='zoom_glitch', label='Zoom Glitch', group='DEGRADATION',
        cls=degradation.ZoomGlitchEffect,
        enable_key='fx_zoom_glitch', enabled_default=False,
        chance_key='fx_zoom_glitch_chance', default_chance=0.5,
        note='IMPACT / DROP — sudden centre zoom-in.',
        tooltip=bi(
            'Crops the centre and upscales it back with INTER_NEAREST. Punchy on hits.',
            'Обрезает центр и растягивает обратно через INTER_NEAREST. Резкий «удар» на хитах.',
        ),
    ),

    # ── COMPLEX ────────────────────────────────────────────────────────
    EffectSpec(
        id='feedback', label='Feedback Loop', group='COMPLEX',
        cls=complex_fx.FeedbackLoopEffect,
        enable_key='fx_feedback', enabled_default=False,
        chance_key=None,                 # always-on
        note='SUSTAIN / BUILD — accumulates frames recursively.',
        tooltip=bi(
            'accumulator = current·(1−w) + accumulator·w. IMPACT clears it. Builds wash-style '
            'trails on sustained energy.',
            'аккумулятор = текущий·(1−w) + аккумулятор·w. IMPACT обнуляет. На устойчивой '
            'энергии нарастают «шлейфы».',
        ),
    ),
    EffectSpec(
        id='phase_shift', label='Phase Shift (L/R bands)', group='COMPLEX',
        cls=complex_fx.PhaseShiftEffect,
        enable_key='fx_phase_shift', enabled_default=False,
        chance_key='fx_phase_shift_chance', default_chance=0.4,
        note='NOISE / DROP — alternating bands shift left/right.',
        tooltip=bi(
            'Splits the frame into horizontal bands; even bands roll left, odd bands roll right '
            'by intensity·width.',
            'Делит кадр на горизонтальные полосы; чётные сдвигаются влево, нечётные — вправо на '
            'величину интенсивность·ширина.',
        ),
    ),
    EffectSpec(
        id='mosaic', label='Mosaic Pulse (bass RMS)', group='COMPLEX',
        cls=complex_fx.MosaicPulseEffect,
        enable_key='fx_mosaic', enabled_default=False,
        chance_key='fx_mosaic_chance', default_chance=0.5,
        note='IMPACT / BUILD — pixelation pulse.',
        tooltip=bi(
            'Down-up resampling produces blocky pixelation. Block size scales with intensity '
            '(4-44 px).',
            'Down-up ресемплинг даёт квадратную пикселизацию. Размер блока зависит от '
            'интенсивности (4-44 px).',
        ),
    ),
    EffectSpec(
        id='echo', label='Echo Compound (hue shift)', group='COMPLEX',
        cls=complex_fx.EchoCompoundEffect,
        enable_key='fx_echo', enabled_default=False,
        chance_key='fx_echo_chance', default_chance=0.4,
        note='SUSTAIN / BUILD — layered colour echoes from the past.',
        tooltip=bi(
            'Blends current·0.5 + frame_N_ago·0.3 + frame_2N_ago_hue+30°·0.2. Triple-exposure '
            'feel with a colour shift.',
            'Смешивает текущий·0.5 + кадр_N_назад·0.3 + кадр_2N_назад_hue+30°·0.2. Похоже на '
            'тройную экспозицию со сдвигом цвета.',
        ),
    ),
    EffectSpec(
        id='kali', label='Kali Mirror (kaleidoscope)', group='COMPLEX',
        cls=complex_fx.KaliMirrorEffect,
        enable_key='fx_kali', enabled_default=False,
        chance_key='fx_kali_chance', default_chance=0.3,
        note='BUILD / SUSTAIN — kaleidoscopic mirror+rotate.',
        tooltip=bi(
            'hstack(frame, frame[:,::-1]) → vstack(_, 255-_) → rotate by intensity·180°. '
            'Symmetry mandala.',
            'hstack(кадр, отражённый) → vstack с инверсией → поворот на интенсивность·180°. '
            'Симметричная мандала.',
        ),
    ),
    EffectSpec(
        id='cascade', label='Glitch Cascade', group='COMPLEX',
        cls=complex_fx.GlitchCascadeEffect,
        enable_key='fx_cascade', enabled_default=False,
        chance_key='fx_cascade_chance', default_chance=0.4,
        note='IMPACT / DROP / NOISE — chains random glitch effects.',
        tooltip=bi(
            'Picks N random effects from {RGB, Block, Drift, Bitcrush}, N = intensity·4, '
            'applies them in sequence.',
            'Берёт N случайных эффектов из {RGB, Block, Drift, Bitcrush}, где N = '
            'интенсивность·4, и применяет их подряд.',
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
                          'Центральная частота IIR-полосового фильтра. Ниже — шире «волны»; выше '
                          '— плотный «звон» по микродеталям.',
                      )),
            ParamSpec('fx_resonant_q', 'Q factor (sharpness)', 12.0, 2.0, 30.0,
                      kwarg='q',
                      tooltip=bi(
                          'Bandpass sharpness. >15 produces clearly visible resonance bands at '
                          'edges.',
                          'Острота полосы. >15 даёт чёткие «резонансные полосы» вдоль контуров.',
                      )),
        ],
        note='IIR bandpass along pixel rows — spatial ringing at edges.',
        tooltip=bi(
            'Treats each row as audio and runs a 2nd-order bandpass filter. The output adds '
            'ringing to luminance edges.',
            'Каждая строка обрабатывается как аудио — IIR-полосовой фильтр второго порядка. '
            'Вокруг яркостных контуров появляется «звон».',
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
        note='R/G/B from different time offsets — chromatic time ghost.',
        tooltip=bi(
            'Reads each colour channel from a different past frame. Static scenes are '
            'unaffected; motion gets a rainbow trail.',
            'Каждый цветовой канал читается из своего прошлого кадра. На статике эффекта нет; '
            'на движении — радужный «след».',
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
                              'в волновую интерференцию; >0.7 — изображение полностью «расходится».',
                          ))],
        note='Scrambles 2-D FFT phase, preserves magnitude — wave interference.',
        tooltip=bi(
            'Forward FFT, randomly shift phase, inverse FFT. Looks like a hologram corrupted '
            'in transit.',
            'Прямое FFT, случайный сдвиг фазы, обратное FFT. Похоже на голограмму, повреждённую '
            'при передаче.',
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
                              'Сила tanh-сатурации. 1 — нейтрально, 3 — «тёплый» окрас, >5 — '
                              'жёсткий клип / мультяшные цвета.',
                          ))],
        note='Tube-amplifier saturation on pixel values.',
        tooltip=bi(
            'Maps pixels through tanh(drive · pixel)/tanh(drive). Soft-clip colour distortion '
            'that retains shape but punches saturation.',
            'Прогоняет пиксели через tanh(drive · pixel)/tanh(drive). Мягкая клиппинг-окраска: '
            'форма сохраняется, насыщенность взрывается.',
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
                              'Большие значения — палитра «застряла в прошлом».',
                          ))],
        note='Match palette to a frame from N back — colour memory.',
        tooltip=bi(
            'Histogram-matching against a delayed buffer. The composition stays current, the '
            'palette is from the past.',
            'Согласование гистограмм по задержанному буферу. Композиция — актуальная, палитра — '
            'из прошлого.',
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
                              '2 — мягко, 8 — цветные блоки заметно «вытекают» за контуры.',
                          ))],
        note='Chroma subsampling abuse — colour blocks bleed over sharp edges.',
        tooltip=bi(
            'Downsamples Cr and Cb planes by FACTOR via INTER_AREA, upsamples back by '
            'INTER_NEAREST. Luma stays sharp.',
            'Даунсэмплит Cr и Cb плоскости в FACTOR раз через INTER_AREA и апсэмплит обратно '
            'INTER_NEAREST. Яркость остаётся резкой.',
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
                              '1 — едва заметно, 5 — маска становится «инопланетной».',
                          ))],
        note='Conway automaton on frame as corruption mask — organic glitch.',
        tooltip=bi(
            'Binarise (>128), evolve N steps of Game of Life, XOR random noise into living '
            'cells. Bio-glitch overlay.',
            'Бинаризация (>128), N шагов Conway, XOR шума в живых клетках. «Био-глитч» поверх '
            'кадра.',
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
                              '0 — полная «криминалистическая» тепло-карта, 1 — выключено. ~0.4 '
                              '— свечение по контурам поверх оригинала.',
                          ))],
        note='JPEG compression error map — forensic edge glow.',
        tooltip=bi(
            'Re-compresses frame at quality 75, takes |diff|·amplify. Edges and high-frequency '
            'areas glow.',
            'Перекодирует кадр в JPEG q=75, считает |разность|·amplify. Контуры и '
            'высокочастотные зоны светятся.',
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
                              'Сила возмущения float16-вида байтов. 0.05 — чистый VRAM-глитч; '
                              '0.3 — полное визуальное «уничтожение».',
                          ))],
        note='Frame bytes reread as float16 — VRAM-corruption look.',
        tooltip=bi(
            'Reinterprets the byte buffer as float16, adds Gaussian noise, views back as '
            'uint8. Looks like a corrupted GPU framebuffer.',
            'Переинтерпретирует байтовый буфер как float16, добавляет гауссовский шум и '
            'возвращает в uint8. Похоже на повреждённый кадровый буфер GPU.',
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
                              'Сила отражений. Выше — заметнее «эхо-шлейфы» вдоль каждой строки.',
                          ))],
        note='Decaying horizontal echo — acoustic reverb on light.',
        tooltip=bi(
            'FFT-convolves each row with a sparse impulse response (6 reflections, '
            'decay-shaped). Light echoes laterally.',
            'FFT-свёртка каждой строки с разреженной импульсной характеристикой (6 отражений '
            'с затуханием). Свет «эхом» расходится по горизонтали.',
        ),
    ),

    # ── WARP (the four newly-integrated effects) ───────────────────────
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
                              'Насколько просвечивает предыдущий кадр. 0 — чистое смещение; '
                              '0.6 — сильный смаз.',
                          ))],
        note='IMPACT / NOISE / DROP / SUSTAIN — Sobel of prev frame as motion field.',
        tooltip=bi(
            'Closest CPU analogue to datamosh. Computes a gradient of the previous frame and '
            'uses it as an optical-flow-like vector field to displace the current frame.',
            'Ближайший CPU-аналог датамоша. Считает градиент предыдущего кадра и использует '
            'его как векторное поле оптического потока для смещения текущего.',
        ),
    ),
    EffectSpec(
        id='vortex_warp', label='Vortex Warp (spiral)', group='WARP',
        cls=warp.VortexWarpEffect,
        enable_key='fx_vortex_warp', enabled_default=False,
        chance_key='fx_vortex_warp_chance', default_chance=0.4,
        note='BUILD / IMPACT / SUSTAIN / DROP — Gaussian-falloff spiral.',
        tooltip=bi(
            'Rotates pixels around the centre with angle = intensity · gaussian-falloff(radius). '
            'Subtle swirl at low intensity, full collapse at high.',
            'Вращает пиксели вокруг центра — угол = интенсивность · гауссово-затухание(радиус). '
            'На малой интенсивности — лёгкий завиток, на большой — полный «коллапс» в спираль.',
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
                              'Сколько октав шума суммируется. 2 — плавные «капли», 5 — рваный '
                              'фрактал.',
                          ))],
        note='Any segment — fBm noise displacement field.',
        tooltip=bi(
            'Builds a multi-octave noise field and uses it as XY displacement. Field is '
            'reseeded per audio segment so it constantly evolves.',
            'Строит многооктавное шумовое поле и использует его как XY-смещение. Поле '
            'перерождается на каждом аудио-сегменте — постоянно меняется.',
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
                              'Больше — заметнее «разрыв» из-за задержки.',
                          ))],
        note='IMPACT / NOISE / DROP / BUILD / SUSTAIN — past frame is the warp map.',
        tooltip=bi(
            "Past frame's R channel = X offset, G channel = Y offset. Image literally uses its "
            'own colour to tear itself apart. With FEEDBACK active, cascades into '
            'datamosh-grade smear.',
            'Канал R прошлого кадра — смещение по X, канал G — по Y. Изображение буквально '
            'разрывает само себя своими же цветами. В паре с FEEDBACK — каскадный смаз уровня '
            'датамоша.',
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
                          '0 — чистый результат формулы, 1 — исходный кадр. Между — кроссфейд.',
                      )),
            ParamSpec('fx_formula_a', 'a', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider — referenced as `a` inside the formula.',
                          'Живой слайдер — обращайтесь к нему в формуле как к переменной `a`.',
                      )),
            ParamSpec('fx_formula_b', 'b', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider — referenced as `b` inside the formula.',
                          'Живой слайдер — обращайтесь к нему в формуле как к переменной `b`.',
                      )),
            ParamSpec('fx_formula_c', 'c', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider — referenced as `c` inside the formula.',
                          'Живой слайдер — обращайтесь к нему в формуле как к переменной `c`.',
                      )),
            ParamSpec('fx_formula_d', 'd', 0.5, 0.0, 1.0,
                      tooltip=bi(
                          'Live slider — referenced as `d` inside the formula.',
                          'Живой слайдер — обращайтесь к нему в формуле как к переменной `d`.',
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
                          'Итоговый коэффициент альфы. 0.85 — полупрозрачная «наклейка».',
                      )),
            ParamSpec('fx_overlay_scale', 'Scale Max', 0.4, 0.05, 1.0, indent=False,
                      tooltip=bi(
                          'Maximum size as a fraction of frame height. Intensity interpolates '
                          'between min and max.',
                          'Максимальный размер — доля высоты кадра. Интенсивность '
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
                          'screen — высветляет; multiply — затемняет; normal — заменяет.',
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
                          'dominant — авто-ключ по самому частому оттенку; secondary — по '
                          'второму по частоте; manual — по RGB ниже.',
                      )),
            ParamSpec('fx_overlay_ck_tolerance', 'CK Tolerance', 30, 5, 60, kind='int',
                      tooltip=bi(
                          'How wide the keyed hue range is. Higher = more pixels removed.',
                          'Ширина диапазона по оттенку, который вырезается. Больше — больше '
                          'удалённых пикселей.',
                      )),
            ParamSpec('fx_overlay_ck_softness', 'CK Edge Softness', 5, 1, 21, kind='int',
                      tooltip=bi(
                          'Gaussian blur applied to the key mask. Higher = softer edges.',
                          'Гауссово размытие маски ключа. Больше — мягче края.',
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
]


# ──────────────────────────────────────────────────────────────────────────
#   Lookup + iteration helpers
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
#   Engine-side: build the chain from a flat cfg dict
# ──────────────────────────────────────────────────────────────────────────


def build_chain(cfg: dict) -> List[BaseEffect]:
    """Construct the ordered effect chain for a render from a flat cfg dict.

    Mirrors the original engine order — effects appear in the same order as
    EFFECTS so any preset chained behaviour (e.g. Cascade after individual
    glitches) is preserved.

    `chance` is scaled by chaos_level via the same formula as the original
    engine (`base * (0.3 + 0.7 * chaos)`) for every effect that opted in.

    Per-effect always-on (backlog #1):
        cfg[fx_xxx_always]      — when True, this effect:
                                  · ignores its trigger_types (fires on every segment),
                                  · ignores its chance slider (chance = 1.0),
                                  · uses a fixed intensity (no audio scaling).
        cfg[fx_xxx_always_int]  — that fixed intensity, in [0, 1].
        Other effects in the chain remain unaffected by this override.
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

        # Compute chance (overridden to 1.0 in always-on mode)
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
            # Bypass triggers, pin intensity to the user-set fixed value.
            fixed = float(cfg.get(spec.always_int_key, 0.6))
            fx.trigger_types = list(SegmentType)
            fx.intensity_min = fixed
            fx.intensity_max = fixed
            fx.chance = 1.0
        elif spec.trigger_types is not None:
            fx.trigger_types = list(spec.trigger_types)

        chain.append(fx)

    return chain


def default_cfg() -> dict:
    """Return a flat dict of every default value the registry provides.

    Used by GUI to populate Tk vars and by tests to build minimal configs.
    """
    cfg: dict = {}
    for k, v in iter_cfg_keys():
        cfg[k] = v
    # Composite RGB defaults — stored in cfg as lists (matches old format)
    cfg['fx_ascii_fg'] = [
        cfg.get('fx_ascii_fg_r', 0), cfg.get('fx_ascii_fg_g', 255), cfg.get('fx_ascii_fg_b', 0)]
    cfg['fx_ascii_bg'] = [
        cfg.get('fx_ascii_bg_r', 0), cfg.get('fx_ascii_bg_g', 0), cfg.get('fx_ascii_bg_b', 0)]
    cfg['fx_overlay_ck_color'] = [
        cfg.get('fx_overlay_ck_r', 0), cfg.get('fx_overlay_ck_g', 255), cfg.get('fx_overlay_ck_b', 0)]
    return cfg
