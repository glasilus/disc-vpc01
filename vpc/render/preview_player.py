"""Плеер превью с A/V-синхронизацией по аудио как мастер-часам.

Какую проблему это решает
--------------------------
Раньше видео и аудио в превью крутились как два независимых потока со
*своими часами и без ресинка*. Видео-цикл планировал кадры по wall time
(плюс тяжёлый LANCZOS-ресайз на каждый кадр, из-за чего realtime не всегда
выдерживался), а аудио-цикл проигрывал весь буфер одним блокирующим
``sd.play`` и перезапускал его по кругу. Периоды циклов не совпадали, и
поскольку каждый цикл стартовал от своего якоря, расхождение **копилось на
каждом витке** - звук постепенно уезжал от видео, и чем дольше смотришь,
тем хуже.

Решение: единые мастер-часы, видео - их раб
--------------------------------------------
Стандартный подход к A/V-синку - взять аппаратные часы звуковой карты как
мастер. Здесь callback ``sd.OutputStream`` крутит декодированное аудио
*без пауз* (курсор сэмплов по модулю N) и этот курсор и есть мастер-часы.
Видео тогда становится **чистой функцией от часов**: показываемый кадр -
всегда ``int(clock_seconds * fps)``. Именно это делает рассинхрон
невозможным - у видео просто нет своих часов, в которых могла бы копиться
ошибка. Если декодирование не успевает - кадры пропускаются, чтобы
догнать; если обгоняет - видео ждёт; а на границе цикла аудио-курсор
оборачивается и видео сикает на 0, так что оба перезапускаются вместе,
идеально выровненные, на каждом витке.

Если аудио-бэкенда или дорожки нет, роль мастера играют монотонные wall
clock с тем же контрактом slaving, так что видео-путь не меняется.

Модуль намеренно не завязан на Tk: ``PreviewPlayer`` зовёт callback
``on_frame(rgb_ndarray)`` из рабочего потока, а GUI сам заворачивает это в
свой thread-marshalling. Чистый хелпер ``frame_for_time`` и изолированный
аудио ``_fill`` тестируются юнит-тестами без реального аудио-устройства.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np

try:
    import sounddevice as _sd
    import soundfile as _sf
    _AUDIO_OK = True
except Exception:                                    # pragma: no cover - зависит от окружения
    _sd = None
    _sf = None
    _AUDIO_OK = False


def frame_for_time(t: float, fps: float, nframes: int) -> int:
    """Время мастер-часов (сек) -> индекс кадра видео.

    Чистая тотальная функция. Это отображение - сердце всей схемы синка:
    кадр видео есть функция от аудио-часов, поэтому рассинхрон невозможен.
    """
    if nframes <= 0 or fps <= 0:
        return 0
    return int(t * fps) % nframes


class _AudioClock:
    """Зацикленный аудио-вывод, чей курсор сэмплов служит мастер-часами.

    Буфер проигрывается без пауз (курсор двигается по модулю N внутри
    PortAudio callback), громкость читается на каждом блоке заново, а пауза
    замораживает курсор и отдаёт тишину - поэтому мастер-часы (а значит и
    видео) тоже замирают.
    """

    def __init__(self, data: np.ndarray, sr: int):
        if data.ndim == 1:
            data = data[:, None]
        self._data = np.ascontiguousarray(data, dtype=np.float32)
        self._n = int(len(self._data))
        self._ch = int(self._data.shape[1])
        self.sr = int(sr)
        self._cursor = 0
        self._paused = False
        self.volume = 1.0
        self._lock = threading.Lock()
        self._stream = None

    @property
    def duration(self) -> float:
        return self._n / self.sr if self.sr else 0.0

    def position_seconds(self) -> float:
        with self._lock:
            return self._cursor / self.sr if self.sr else 0.0

    def _fill(self, out: np.ndarray, frames: int) -> None:
        """Заполняет ``out`` (frames x channels) из зацикленного буфера.

        Изолирован от PortAudio, чтобы тестироваться напрямую юнит-тестами.
        Двигает курсор по модулю N, если не на паузе (иначе тишина и курсор
        стоит на месте).
        """
        with self._lock:
            if self._paused or self._n == 0:
                out[:] = 0.0
                return
            idx = (np.arange(frames) + self._cursor) % self._n
            np.multiply(self._data[idx], self.volume, out=out)
            self._cursor = int((self._cursor + frames) % self._n)

    def _callback(self, outdata, frames, time_info, status):  # pragma: no cover
        self._fill(outdata, frames)

    def start(self) -> None:
        self._stream = _sd.OutputStream(
            samplerate=self.sr, channels=self._ch, dtype='float32',
            callback=self._callback)
        self._stream.start()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)

    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, float(v))

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:                        # pragma: no cover
                pass  # закрытие потока лучше не даст упасть UI
            self._stream = None


class _WallClock:
    """Резервные мастер-часы на монотонном времени, обёрнутые в фикс. длительность.

    Используются, когда нет аудио-бэкенда или дорожка не декодировалась.
    Тот же контракт ``position_seconds`` / ``set_paused``, что и у
    :class:`_AudioClock`, поэтому видео-цикл не меняется.
    """

    def __init__(self, duration: float):
        self._duration = max(1e-3, float(duration))
        self._t0 = time.monotonic()
        self._paused = False
        self._pause_t0 = 0.0
        self._lock = threading.Lock()

    @property
    def duration(self) -> float:
        return self._duration

    def position_seconds(self) -> float:
        with self._lock:
            base = self._pause_t0 if self._paused else time.monotonic()
            return (base - self._t0) % self._duration

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            paused = bool(paused)
            if paused and not self._paused:
                self._pause_t0 = time.monotonic()
                self._paused = True
            elif not paused and self._paused:
                self._t0 += time.monotonic() - self._pause_t0
                self._paused = False

    def set_volume(self, v: float) -> None:
        pass

    def start(self) -> None:
        with self._lock:
            self._t0 = time.monotonic()
            self._paused = False

    def stop(self) -> None:
        pass


class PreviewPlayer:
    """Проигрывает отрендеренный превью-клип с A/V-синком по аудио, по кругу.

    Parameters
    ----------
    video_path : str
        Путь к отрендеренному превью-видео (содержит оба потока).
    on_frame : callable(np.ndarray)
        Зовётся из рабочего потока с каждым RGB-кадром для показа (уже
        отресайженным под ``size``). Вызывающий сам маршалит его в свой UI-поток.
    size : (int, int)
        Целевые (ширина, высота) показываемых кадров.
    wav_path : str | None
        PCM wav звука клипа для мастер-часов. Если отсутствует (или нет
        аудио-бэкенда) используются wall clock и проигрывание идёт без звука.
    log : callable(str)
        Логгер статусов.
    clock : object | None
        Точка для подмены в тестах: можно передать мастер-часы с методом
        ``position_seconds()``. При ``None`` плеер сам строит аудио- или
        wall-часы.
    """

    def __init__(self, video_path: str,
                 on_frame: Callable[[np.ndarray], None],
                 size=(640, 360), wav_path: Optional[str] = None,
                 log: Callable[[str], None] = lambda m: None,
                 clock=None):
        self.video_path = video_path
        self.on_frame = on_frame
        self.W, self.H = int(size[0]), int(size[1])
        self.wav_path = wav_path
        self.log = log
        self._clock = clock
        self._external_clock = clock is not None
        self._stop = threading.Event()
        self._thread = None
        self._fps = 24.0
        self._nframes = 1
        self._volume = 0.8

    # ── жизненный цикл ──────────────────────────────────────────────────
    def start(self) -> bool:
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            cap.release()
            self.log('ERROR: cannot open preview video.')
            return False
        self._fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
        vframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()

        if self._clock is None:
            self._clock = self._build_clock(vframes)

        # Период цикла: сначала пробуем реальный frame count, иначе выводим
        # из длительности мастер-часов. Оба описывают один и тот же клип,
        # так что target = int(t*fps) остаётся в диапазоне, а модуло только
        # гасит округление на последнем кадре.
        self._nframes = vframes or int(round(self._clock.duration * self._fps)) or 1

        self._stop.clear()
        if not self._external_clock:
            try:
                self._clock.start()
            except Exception as e:
                self.log(f'WARNING: audio start failed ({e}); silent preview.')
                self._clock = _WallClock((vframes / self._fps) if vframes else 5.0)
                self._clock.start()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _build_clock(self, vframes: int):
        if _AUDIO_OK and self.wav_path:
            try:
                data, sr = _sf.read(self.wav_path, dtype='float32')
                clk = _AudioClock(data, sr)
                clk.set_volume(self._volume)
                return clk
            except Exception as e:
                self.log(f'WARNING: preview audio decode failed ({e}); silent preview.')
        dur = (vframes / self._fps) if (vframes and self._fps) else 5.0
        return _WallClock(dur)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._clock is not None and not self._external_clock:
            try:
                self._clock.stop()
            except Exception:                        # pragma: no cover
                pass  # закрытие потока лучше не даст упасть UI

    # ── управление воспроизведением ─────────────────────────────────────
    def pause(self) -> None:
        self._paused = True
        if self._clock is not None:
            self._clock.set_paused(True)

    def resume(self) -> None:
        self._paused = False
        if self._clock is not None:
            self._clock.set_paused(False)

    def is_paused(self) -> bool:
        return getattr(self, '_paused', False)

    def toggle_pause(self) -> bool:
        if self.is_paused():
            self.resume()
        else:
            self.pause()
        return self.is_paused()

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, float(v))
        if self._clock is not None:
            self._clock.set_volume(self._volume)

    # ── видео как раб мастер-часов ──────────────────────────────────────
    def _advance(self, cap, cur: int):
        """Декодирует вперёд до кадра, который сейчас требуют мастер-часы.

        Возвращает ``(frame_or_None, new_cur, target)``. Если отстаём -
        пропускает промежуточные кадры (оставляет только последний); при
        оборачивании часов сикает на 0. Вынесен отдельно ради юнит-тестов.
        """
        t = self._clock.position_seconds()
        target = frame_for_time(t, self._fps, self._nframes)
        if target < cur:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            cur = -1
        frame = None
        guard = 0
        while cur < target and guard < self._nframes + 2:
            ok, f = cap.read()
            guard += 1
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                cur = -1
                break
            frame = f
            cur += 1
        return frame, cur, target

    def _run(self) -> None:
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            self.log('ERROR: preview video reopen failed.')
            return
        frame_dur = 1.0 / self._fps if self._fps else 1.0 / 24.0
        cur = -1
        try:
            while not self._stop.is_set():
                frame, cur, _target = self._advance(cap, cur)
                if frame is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    if (self.W, self.H) != (rgb.shape[1], rgb.shape[0]):
                        rgb = cv2.resize(rgb, (self.W, self.H),
                                         interpolation=cv2.INTER_LINEAR)
                    self.on_frame(rgb)
                # Спим до момента, когда мастер-часы потребуют следующий кадр.
                # Если уже отстаём (sleep<=0), сразу идём на новый виток - тогда
                # _advance пропустит кадры и догонит, а не отстанет ещё сильнее.
                nxt = (cur + 1) * frame_dur
                sleep = nxt - self._clock.position_seconds()
                self._stop.wait(min(sleep, 0.1) if sleep > 0 else 0.001)
        finally:
            cap.release()
