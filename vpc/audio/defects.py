"""Чистые функции, которые портят стерео float32-буфер так, чтобы звук
соответствовал конкретным визуальным эффектам.

Каждая функция принимает (samples, sr), где:
  samples : np.ndarray формы (n_samples, n_channels), dtype float32,
            значения примерно в [-1.0, 1.0]
  sr      : int, частота дискретизации (Гц)

и возвращает новый массив ТОЙ ЖЕ формы и dtype, не трогая исходный.
Благодаря этому функции легко тестировать и комбинировать в цепочку.

scipy.signal уже используется в проекте (эффекты SIGNAL DOMAIN). Если
scipy вдруг не импортировался, конкретный дефект превращается в no-op
и возвращает вход как есть - аудио-пайплайн не должен падать в потоке
рендера.
"""
from __future__ import annotations

import numpy as np

try:
    from scipy.signal import butter, sosfilt, iircomb, lfilter
    _SCIPY_OK = True
except ImportError:                              # pragma: no cover
    _SCIPY_OK = False


# ──────────────────────────────────────────────────────────────────────
#   VHS tape - завал АЧХ до ~6 кГц + гребенчатый резонанс + плавание питча
# ──────────────────────────────────────────────────────────────────────


def defect_vhs_tape(samples: np.ndarray, sr: int) -> np.ndarray:
    """ФНЧ + гребенчатый резонанс + медленное LFO-плавание питча.

    Три стадии:
      1. ФНЧ Баттерворта 4-го порядка на 6 кГц - именно на этой частоте
         обрывались HiFi-дорожки VHS, обычные линейные дорожки были еще уже.
      2. Гребенчатый notch-фильтр на ~120 Гц - дает ту самую гармоническую
         "тонкую и металлическую" окраску бытового ленточного звука.
      3. Wow & flutter через ресемплинг с переменной скоростью (линейная
         интерполяция): медленная синусоида двигает позицию чтения, из-за
         чего питч плавает на ~0.5%.
    """
    if samples.size == 0 or not _SCIPY_OK:
        return samples
    out = samples.astype(np.float32, copy=True)

    # 1. ФНЧ на 6 кГц (с защитой от превышения Найквиста при низком sr).
    nyq = sr * 0.5
    cutoff = min(6000.0, nyq * 0.9) / nyq
    sos = butter(4, cutoff, btype='lowpass', output='sos')
    for c in range(out.shape[1]):
        out[:, c] = sosfilt(sos, out[:, c])

    # 2. Гребенка на ~120 Гц с умеренной добротностью. iircomb требует
    # sr % w0_freq == 0 для устойчивого расчета, поэтому округляем до
    # ближайшего целочисленного делителя.
    base = 120.0
    period = max(1, int(round(sr / base)))
    w0 = sr / period / nyq
    if 0.0 < w0 < 1.0:
        try:
            b, a = iircomb(w0, 6.0, ftype='notch', fs=2.0)
            for c in range(out.shape[1]):
                out[:, c] = lfilter(b, a, out[:, c])
        except (ValueError, ZeroDivisionError):
            pass

    # 3. Wow & flutter - позиция чтения гуляет по медленной синусоиде,
    # питч плавает на ~0.4%. Реализовано как np.interp по float-индексу.
    n = out.shape[0]
    t = np.arange(n, dtype=np.float32)
    wow = np.sin(t * (2.0 * np.pi * 0.6 / sr)) * 0.004 * sr
    src_idx = np.clip(t + wow, 0, n - 1)
    src_floor = src_idx.astype(np.int32)
    src_frac = (src_idx - src_floor).astype(np.float32)
    src_next = np.minimum(src_floor + 1, n - 1)
    for c in range(out.shape[1]):
        col = out[:, c]
        out[:, c] = col[src_floor] * (1.0 - src_frac) + col[src_next] * src_frac

    return out


# ──────────────────────────────────────────────────────────────────────
#   SelfCannibalize - эхо с обратной связью (звук "поедает сам себя")
# ──────────────────────────────────────────────────────────────────────


def defect_self_echo(samples: np.ndarray, sr: int,
                     delay_ms: float = 220.0,
                     feedback: float = 0.45) -> np.ndarray:
    """Однотаповая задержка с обратной связью: звук повторяется с
    затуханием - звуковой аналог визуальной рекурсии.

    Векторизовано блоками по `delay_n` сэмплов. Каждый блок зависит
    ТОЛЬКО от блока, завершившегося на `delay_n` сэмплов раньше, поэтому
    внутри блока достаточно одного векторного сложения с предыдущим
    блоком, умноженным на feedback - без Python-цикла по сэмплам и без
    scipy IIR (lfilter на разреженном знаменателе из тысяч коэффициентов
    как ни странно медленнее наивного Python-цикла, т.к. проходит весь
    вектор `a` на каждый сэмпл). На 60 с стерео @ 44.1 кГц это ~5 мс.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    delay_n = max(1, int(round(delay_ms * 0.001 * sr)))
    if delay_n >= n:
        return out
    fb = float(np.clip(feedback, 0.0, 0.85))
    # Идем по буферу непересекающимися окнами по `delay_n` сэмплов.
    # В каждое окно подмешивается (fb * предыдущее окно), которое уже
    # полностью записано и потому стабильно. Источник заканчивается
    # ровно там, где начинается назначение - внутриоконной зависимости
    # нет, поэтому операция векторизуется.
    pos = delay_n
    while pos < n:
        end = min(n, pos + delay_n)
        src_start = pos - delay_n
        actual = end - pos
        out[pos:end] += out[src_start:src_start + actual] * fb
        pos = end
    # Мягкий клиппинг, чтобы сильная обратная связь не улетала за шкалу.
    return np.tanh(out)


# ──────────────────────────────────────────────────────────────────────
#   CursorStorm - редкие импульсные щелчки
# ──────────────────────────────────────────────────────────────────────


def defect_cursor_clicks(samples: np.ndarray, sr: int,
                         density_per_sec: float = 12.0) -> np.ndarray:
    """Разбрасывает по треку короткие биполярные импульсы - звучит как
    треск контакта / дождь из щелчков курсора.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_clicks = int((n / sr) * density_per_sec)
    if n_clicks <= 0:
        return out
    rng = np.random.default_rng(0x5C5C5C)
    positions = rng.integers(0, n, size=n_clicks)
    # Амплитуды рандомизированы, чтобы щелчки не звучали метрономом.
    amps = (rng.random(n_clicks).astype(np.float32) * 0.45 + 0.15)
    for pos, amp in zip(positions, amps):
        end = min(n, pos + 4)
        for c in range(ch):
            out[pos:end, c] += amp * np.array([1, -1, 0.5, -0.25],
                                              dtype=np.float32)[:end - pos]
    return np.clip(out, -1.0, 1.0)


# ──────────────────────────────────────────────────────────────────────
#   BSODShred - короткие вспышки белого шума
# ──────────────────────────────────────────────────────────────────────


def defect_bsod_static(samples: np.ndarray, sr: int,
                       bursts_per_sec: float = 1.5,
                       burst_ms: float = 90.0) -> np.ndarray:
    """В случайных коротких участках сигнал ЗАМЕНЯЕТСЯ резким белым
    шумом - звуковой аналог визуальной нарезки на полосы: там, где
    появляется полоса синего экрана, звук тоже "рвется".
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_bursts = int((n / sr) * bursts_per_sec)
    if n_bursts <= 0:
        return out
    burst_len = max(1, int(burst_ms * 0.001 * sr))
    rng = np.random.default_rng(0xB50DB50D)
    starts = rng.integers(0, max(1, n - burst_len), size=n_bursts)
    for start in starts:
        end = min(n, start + burst_len)
        # Небольшая рандомизация громкости каждой вспышки - иначе все
        # они звучат одинаково и эффект воспринимается как заученный ритм.
        gain = 0.55 + rng.random() * 0.35
        noise = rng.standard_normal(size=(end - start, ch)).astype(np.float32) * gain
        out[start:end] = noise
    return np.clip(out, -1.0, 1.0)


# ──────────────────────────────────────────────────────────────────────
#   VSyncRoll - медленное плавание питча (аналог вертикальной прокрутки)
# ──────────────────────────────────────────────────────────────────────


def defect_pitch_wobble(samples: np.ndarray, sr: int,
                        rate_hz: float = 0.4,
                        depth: float = 0.012) -> np.ndarray:
    """Медленное LFO-плавание питча - усиленная версия VHS wow, звучит
    как намеренная расстройка. Та же техника (сдвиг позиции чтения
    линейной интерполяцией), но глубина и период примерно втрое больше,
    чем в VHS - должно читаться как "плеер сбоит", а не как зерно ленты.
    Парная пара к визуальному шву, ползущему по вертикали.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    t = np.arange(n, dtype=np.float32)
    # Смещение позиции (в сэмплах) идет по синусоиде с периодом 1/rate_hz
    # и амплитудой ~depth*sr.
    lfo = np.sin(t * (2.0 * np.pi * rate_hz / sr)) * depth * sr
    src_idx = np.clip(t + lfo, 0, n - 1)
    floor = src_idx.astype(np.int32)
    frac = (src_idx - floor).astype(np.float32)
    nxt = np.minimum(floor + 1, n - 1)
    for c in range(ch):
        col = out[:, c]
        out[:, c] = col[floor] * (1.0 - frac) + col[nxt] * frac
    return out


# ──────────────────────────────────────────────────────────────────────
#   PFrameLag - короткая многотаповая "призрачная реверберация"
# ──────────────────────────────────────────────────────────────────────


def defect_ghost_reverb(samples: np.ndarray, sr: int) -> np.ndarray:
    """Три короткие задержанные копии суммируются с убывающей
    амплитудой - примитивная имитация ранних отражений. Звук "отстает
    от самого себя" так же, как визуальный декодер отстает от исходного
    кадра: текущий момент доминирует, но прошлое постоянно просачивается
    на низком уровне. В отличие от `defect_self_echo` (один тап с
    обратной связью, рекурсивно) здесь фиксированный набор тапов, FIR.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    taps_ms = (35.0, 90.0, 170.0)
    gains = (0.40, 0.25, 0.15)
    accum = out.copy()
    for ms, g in zip(taps_ms, gains):
        delay_n = int(round(ms * 0.001 * sr))
        if delay_n <= 0 or delay_n >= n:
            continue
        accum[delay_n:] += out[:n - delay_n] * g
    return np.tanh(accum)


# ──────────────────────────────────────────────────────────────────────
#   BitFlip - точечные всплески битового кранча
# ──────────────────────────────────────────────────────────────────────


def defect_bitcrush_bursts(samples: np.ndarray, sr: int,
                           bursts_per_sec: float = 4.0,
                           burst_ms: float = 60.0,
                           bits: int = 4) -> np.ndarray:
    """В случайных коротких окнах сигнал грубо квантуется до `bits`
    уровней. Звук "проседает до 4 бит" пятнами - так же, как визуальный
    кадр местами ловит XOR-сдвиги. Вне вспышек звук не тронут, поэтому
    эффект получается точечным, а не сплошной кашей - соответствует
    пятнистой картине битового гниения на видео.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_bursts = int((n / sr) * bursts_per_sec)
    if n_bursts <= 0:
        return out
    burst_len = max(1, int(burst_ms * 0.001 * sr))
    levels = float(2 ** max(1, min(8, bits)) - 1)
    rng = np.random.default_rng(0xB17F11)
    starts = rng.integers(0, max(1, n - burst_len), size=n_bursts)
    for start in starts:
        end = min(n, start + burst_len)
        chunk = out[start:end]
        # Симметричное квантование вокруг нуля.
        out[start:end] = np.round(chunk * levels) / levels
    return out


# ──────────────────────────────────────────────────────────────────────
#   WrongMotionVector - подмена окон сэмплов из смещенных позиций
# ──────────────────────────────────────────────────────────────────────


def defect_sample_swap(samples: np.ndarray, sr: int,
                       swaps_per_sec: float = 6.0,
                       window_ms: float = 35.0) -> np.ndarray:
    """Короткие окна аудио ЗАМЕНЯЮТСЯ фрагментами, скопированными из
    случайной другой позиции трека. Прямой звуковой аналог визуального
    смещения макроблоков: правильный звук попадает не в то место, как
    правильные пиксели попадают не в тот блок. В отличие от тайлинга/
    зацикливания, позиция источника никак не связана с позицией назначения.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_swaps = int((n / sr) * swaps_per_sec)
    if n_swaps <= 0:
        return out
    win_n = max(1, int(window_ms * 0.001 * sr))
    if win_n >= n:
        return out
    rng = np.random.default_rng(0x14F1DAB)
    dst = rng.integers(0, n - win_n, size=n_swaps)
    src = rng.integers(0, n - win_n, size=n_swaps)
    for d, s in zip(dst, src):
        out[d:d + win_n] = samples[s:s + win_n]
    return out
