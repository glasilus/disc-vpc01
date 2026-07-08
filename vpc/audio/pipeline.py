"""Оркестратор: открывает passthrough WAV, накладывает включенные аудио-
дефекты поверх существующих сэмплов, пишет обратно. Встраивается в
pre-pass движка сразу после `apply_passthrough_stutter_audio`.

EFFECT_AUDIO_COUPLING - единственный источник истины, связывающий
визуальный эффект с парным ему аудио-дефектом. Чтобы добавить новую
связку, достаточно одной записи здесь - Checkbutton в блоке этого
эффекта появится в GUI автоматически (при включенном passthrough-режиме).
"""
from __future__ import annotations

import wave
from typing import Callable, Dict, Tuple

import numpy as np

from .defects import (
    defect_vhs_tape,
    defect_self_echo,
    defect_cursor_clicks,
    defect_bsod_static,
    defect_pitch_wobble,
    defect_ghost_reverb,
    defect_bitcrush_bursts,
    defect_sample_swap,
)

LogFn = Callable[[str], None]
DefectFn = Callable[[np.ndarray, int], np.ndarray]


# ──────────────────────────────────────────────────────────────────────
#   РЕЕСТР СВЯЗОК
#
#   Ключ    = enable_key визуального эффекта (совпадает с EffectSpec.enable_key)
#   Значение = (gui_label_short, defect_function, gui_tooltip)
#
#   GUI использует gui_label_short как текст Checkbutton, а gui_tooltip -
#   как текст всплывающей подсказки/[?] (у каждого дефекта своя, общая
#   формулировка не давала пользователю понять, что именно происходит).
#   Движок использует саму функцию. Порядок ключей держим таким же, как
#   у визуальных эффектов в registry.py - так проще ориентироваться.
# ──────────────────────────────────────────────────────────────────────


EFFECT_AUDIO_COUPLING: Dict[str, Tuple[str, DefectFn, str]] = {
    'fx_vhstape': (
        'audio: tape wobble + bandlimit',
        defect_vhs_tape,
        'Highs cut at 6 kHz (VHS HiFi bandwidth) + IIR comb notch around '
        '120 Hz that adds the metallic phase-smear of analog tape + a slow '
        '0.6 Hz LFO that detunes pitch by ~0.4 percent (wow & flutter). '
        'Result: bandlimited and slightly seasick.\n──\n'
        'Срез ВЧ на 6 кГц (полоса VHS HiFi) + IIR-comb-нотч около 120 Гц, '
        'дающий металлический фазовый смаз аналоговой плёнки + медленная '
        '0.6 Гц LFO детюнит питч на ~0.4 процента (wow & flutter). '
        'В результате — полосово-ограниченный, слегка «укачанный» звук.'
    ),
    'fx_vsync_roll': (
        'audio: pitch wobble',
        defect_pitch_wobble,
        'A slow 0.4 Hz LFO smoothly bends pitch up and down by ~1.2 '
        'percent — three times deeper than VHS wow. Sounds like a tape '
        'player whose motor is unstable: pitch never sits still.\n──\n'
        'Медленная 0.4 Гц LFO плавно гнёт питч вверх-вниз на ~1.2 '
        'процента — втрое глубже VHS wow. Звучит как магнитофон с '
        'нестабильным мотором: высота тона никогда не стоит на месте.'
    ),
    'fx_pframe_lag': (
        'audio: ghost reverb',
        defect_ghost_reverb,
        'Three fixed echoes at 35 / 90 / 170 ms with fading volume '
        '(0.40 / 0.25 / 0.15) — a poor mans early-reflection reverb. '
        'Audio "lags behind itself" the way the picture lags behind '
        'the source frame.\n──\n'
        'Три фиксированных эха на 35 / 90 / 170 мс с затухающей '
        'громкостью (0.40 / 0.25 / 0.15) — упрощённый early-reflection '
        'ревер. Звук «отстаёт от самого себя» так же, как изображение '
        'отстаёт от исходного кадра.'
    ),
    'fx_bit_flip': (
        'audio: bitcrush bursts',
        defect_bitcrush_bursts,
        'Random ~60 ms windows (~4 per second) are aggressively '
        'quantised to 4-bit (16 levels). Audio briefly drops into harsh '
        '8-bit-console resolution and recovers. Outside the bursts the '
        'sound is untouched — percussive rather than a wash.\n──\n'
        'Случайные окна ~60 мс (~4 в секунду) жёстко квантуются до '
        '4 бит (16 уровней). Звук кратко проваливается в грубое разрешение '
        '8-битной консоли и восстанавливается. Вне burst-ов звук не '
        'тронут — эффект перкуссивный, не сплошной.'
    ),
    'fx_wrong_mvec': (
        'audio: sample swap',
        defect_sample_swap,
        'Random ~35 ms windows (~6 per second) are REPLACED with audio '
        'copied from a different random position in the track — right '
        'type of audio appears in the wrong place, the audio analogue '
        'of wrong macroblocks surfacing in wrong locations.\n──\n'
        'Случайные окна ~35 мс (~6 в секунду) ЗАМЕНЯЮТСЯ аудио, '
        'скопированным из другой случайной позиции трека — правильный '
        'тип звука всплывает не на своём месте, аудио-аналог чужих '
        'макроблоков в неправильных позициях.'
    ),
    'fx_self_cannibalize': (
        'audio: feedback echo',
        defect_self_echo,
        'Single-tap delay at 220 ms with 0.45 feedback. Audio repeats '
        'at exponentially fading amplitude, then those repeats feed '
        'back into themselves — recursive echo that "eats itself" the '
        'same way the visual rectangles do.\n──\n'
        'Одинарный delay 220 мс с feedback 0.45. Звук повторяется с '
        'экспоненциально затухающей громкостью, и эти повторы сами '
        'подаются обратно — рекурсивное эхо, которое «жрёт себя» '
        'так же, как визуальные прямоугольники.'
    ),
    'fx_cursor_storm': (
        'audio: click crackle',
        defect_cursor_clicks,
        '~12 short bipolar 4-sample clicks per second sprinkled over '
        'the track at random positions and amplitudes. Sounds like '
        'contact-crackle / pointer-click rain — fits the swarm of '
        'crawling cursors visually.\n──\n'
        '~12 коротких биполярных кликов по 4 сэмпла в секунду, '
        'разбросанных по треку в случайных позициях и амплитудах. '
        'Звучит как треск контакта / дождь клик-курсоров — '
        'соответствует ползающему рою курсоров визуально.'
    ),
    'fx_bsod_shred': (
        'audio: static bursts',
        defect_bsod_static,
        'Random ~90 ms windows (~1.5 per second) are REPLACED with '
        'harsh white noise — the audio is "shredded" exactly where '
        'the bluescreen bands shred the picture. Per-burst gain is '
        'randomised so they vary in severity.\n──\n'
        'Случайные окна ~90 мс (~1.5 в секунду) ЗАМЕНЯЮТСЯ резким '
        'белым шумом — звук «шинкуется» там же, где синеэкранные '
        'полосы шинкуют картинку. Громкость каждого burst рандомна, '
        'поэтому по тяжести они разные.'
    ),
}


# ──────────────────────────────────────────────────────────────────────
#   Хелперы для GUI (используются в gui.py)
# ──────────────────────────────────────────────────────────────────────


def audio_link_var_name(enable_key: str) -> str:
    """Имя cfg/Tk-переменной для чекбокса привязки аудио к эффекту."""
    return 'audio_link_' + enable_key


def coupled_keys() -> Tuple[str, ...]:
    return tuple(EFFECT_AUDIO_COUPLING.keys())


# ──────────────────────────────────────────────────────────────────────
#   Точка входа для движка
# ──────────────────────────────────────────────────────────────────────


def apply_passthrough_audio_defects(audio_path: str, cfg: dict,
                                    log: LogFn) -> bool:
    """Открывает passthrough WAV заново, применяет все включенные
    аудио-дефекты, пишет обратно. Можно звать и когда дефекты выключены -
    тогда функция выходит раньше, не трогая диск.

    Возвращает True, если применен хотя бы один дефект; False во всех
    остальных случаях, включая ошибки. Ошибки логируются и гасятся,
    чтобы упавший аудио-дефект никогда не обрывал рендер.
    """
    if not audio_path:
        return False

    enabled: list[Tuple[str, DefectFn]] = []
    for key, (label, fn, _tip) in EFFECT_AUDIO_COUPLING.items():
        if not cfg.get(key, False):
            continue
        if not cfg.get(audio_link_var_name(key), False):
            continue
        enabled.append((label, fn))
    if not enabled:
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
            log(f'Audio defects: unsupported sample width {sw}, skipped.')
            return False
        if sw == 1:
            # У 8-битного PCM шумовой пол ~-42 дБFS, и round-trip через
            # float32 этот шум только усилит. На практике 8-битный WAV
            # почти не встречается (ffmpeg-passthrough всегда пишет
            # 16 бит), но предупреждаем, чтобы было понятно, откуда
            # взялся лишний шип.
            log('Audio defects: source WAV is 8-bit; expect added hiss '
                'after defect round-trip. Re-extract at 16-bit for clean '
                'results.')
        in_dtype = dtype_map[sw]
        # Переводим в float32 [-1, 1], чтобы дефекты считали математику,
        # не думая о переполнении/знаке/особенностях исходного dtype.
        max_int = float(np.iinfo(in_dtype).max)
        samples_int = np.frombuffer(raw, dtype=in_dtype).reshape(-1, n_ch)
        samples = samples_int.astype(np.float32) / max_int

        for label, fn in enabled:
            try:
                samples = fn(samples, sr)
            except Exception as exc:                      # noqa: BLE001
                log(f'Audio defect "{label}" failed ({exc!r}) — skipped.')

        # Дефекты могут слегка выходить за шкалу - клампим перед записью.
        samples = np.clip(samples, -1.0, 1.0)
        out_int = (samples * max_int).astype(in_dtype)

        with wave.open(audio_path, 'wb') as w:
            w.setnchannels(n_ch)
            w.setsampwidth(sw)
            w.setframerate(sr)
            w.writeframes(out_int.tobytes())
        log(f'Audio defects applied: {", ".join(lbl for lbl, _ in enabled)}.')
        return True
    except Exception as exc:                              # noqa: BLE001
        # Ловим широко намеренно: неожиданное несовпадение dtype/shape в
        # numpy или битый заголовок WAV, всплывающий как TypeError, иначе
        # уронит поток рендера. Провал пайплайна всегда безопасен -
        # неизмененный WAV все еще валиден для sink-а, пользователь просто
        # не получит запрошенный дефект.
        log(f'Audio defects pipeline failed: {exc!r}. WAV left untouched.')
        return False
