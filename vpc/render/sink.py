"""FFmpeg pipe sink - принимает сырые RGB-кадры, пишет закодированные видео+аудио."""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import shutil
import sys
import stat
from typing import Optional


def ffmpeg_bin() -> str:
    """Путь к ffmpeg - сначала нативные сборки Homebrew/MacPorts на macOS,
    потом бинарник из imageio-ffmpeg, потом системный PATH."""
    if sys.platform == 'darwin':
        # .app-бандлы на macOS не наследуют PATH пользователя. Сначала
        # пробуем нативные глобальные установки (без накладных расходов
        # Rosetta 2 и с аппаратным ускорением), потом fallback на бандл.
        for p in ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/local/bin/ffmpeg']:
            resolved = shutil.which(p)
            if resolved:
                return resolved

    try:
        import imageio_ffmpeg

        # PyInstaller --collect-all часто снимает +x с data-файлов (а именно
        # так упакованы бинарники imageio_ffmpeg). get_ffmpeg_exe() проверяет
        # os.X_OK и падает, если бита нет. Приходится восстанавливать +x заранее.
        bin_dir = os.path.join(os.path.dirname(imageio_ffmpeg.__file__), 'binaries')
        if os.path.isdir(bin_dir):
            for f in os.listdir(bin_dir):
                if 'ffmpeg' in f:
                    bin_path = os.path.join(bin_dir, f)
                    try:
                        st = os.stat(bin_path)
                        if not (st.st_mode & stat.S_IXUSR):
                            os.chmod(bin_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    except Exception:
                        pass

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass

    resolved = shutil.which('ffmpeg')
    if resolved:
        return resolved

    return 'ffmpeg'


# Пресеты контейнер/кодек. Каждая запись мапит видимую пользователю метку
# кодека на: расширение контейнера (без точки), видеокодек, аудиокодек,
# опциональный pix_fmt, опциональные доп. флаги видео (например тег профиля
# для H.265 в MP4).
EXPORT_FORMATS = {
    'H.264 (MP4)':   {'ext': 'mp4', 'vcodec': 'libx264', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'H.265 (MP4)':   {'ext': 'mp4', 'vcodec': 'libx265', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': ['-tag:v', 'hvc1']},
    'H.264 (MKV)':   {'ext': 'mkv', 'vcodec': 'libx264', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'H.265 (MKV)':   {'ext': 'mkv', 'vcodec': 'libx265', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'H.264 (MOV)':   {'ext': 'mov', 'vcodec': 'libx264', 'acodec': 'aac',
                      'pix_fmt': 'yuv420p', 'extra_v': []},
    'ProRes (MOV)':  {'ext': 'mov', 'vcodec': 'prores_ks', 'acodec': 'pcm_s16le',
                      'pix_fmt': 'yuv422p10le',
                      'extra_v': ['-profile:v', '3']},  # ProRes 422 HQ
    'VP9 (WebM)':    {'ext': 'webm', 'vcodec': 'libvpx-vp9', 'acodec': 'libopus',
                      'pix_fmt': 'yuv420p', 'extra_v': ['-row-mt', '1', '-b:v', '0']},
}


# Маркер конца потока для тред-писателя (уникальный объект, не спутать с кадром).
_SENTINEL = object()


class FFmpegSink:
    """Запускает ffmpeg, принимает сырые uint8 RGB-кадры, финализирует файл при close()."""

    def __init__(self, *, width: int, height: int, fps: int,
                 audio_path: str, output_path: str,
                 vcodec: str = 'libx264', acodec: str = 'aac',
                 pix_fmt: str = 'yuv420p',
                 preset: str = 'medium',
                 crf: int = 18, target_duration: Optional[float] = None,
                 extra_v_flags: Optional[list] = None,
                 tune: Optional[str] = None,
                 input_pix_fmt: Optional[str] = None,
                 rate_control_args: Optional[list] = None):
        self.width = width
        self.height = height
        self.fps = fps
        self.output_path = output_path
        self._proc: Optional[subprocess.Popen] = None
        # Состояние потокового писателя (заполняется в open()).
        self._frame_q: Optional[queue.Queue] = None
        self._writer_thread: Optional[threading.Thread] = None
        self._closed = False
        self._write_failed = False
        self._write_error: Optional[BaseException] = None
        # Автовыбор input pix_fmt: если выход yuv420p, можно подавать
        # планарный I420 (1.5 байта/пиксель) вместо RGB24 (3 байта/пиксель),
        # это вдвое снижает нагрузку на пайп. Для 10-бит/4:2:2 выходов
        # (ProRes) остаёмся на rgb24 - конвертация через I420 потеряла бы
        # цветность ещё до того, как кадр попадёт в ffmpeg.
        if input_pix_fmt is None:
            input_pix_fmt = 'yuv420p' if pix_fmt == 'yuv420p' else 'rgb24'
        self.input_pix_fmt = input_pix_fmt
        ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        self._cmd = [
            ffmpeg_bin(), '-y',
            '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}',
            '-pix_fmt', input_pix_fmt,
            '-r', str(fps),
            '-i', 'pipe:0',
            '-i', audio_path,
            '-vcodec', vcodec,
            '-pix_fmt', pix_fmt,
        ]
        # Флаги rate-control. Два пути:
        #   1. Если передан `rate_control_args` - используем его как есть,
        #      это путь через encoders.py (нужен для HW-энкодеров, у которых
        #      флаги не совпадают с семейством x264). Движок строит их через
        #      `build_rate_control_args(spec, crf, preset, tune)`.
        #   2. Иначе легаси автовыбор: x264/x265 получают -preset/-crf
        #      (+ -tune), VP9 получает -crf/-deadline. Эту ветку используют
        #      вызовы только через sink и существующие тесты.
        if rate_control_args is not None:
            self._cmd.extend(rate_control_args)
        elif vcodec in ('libx264', 'libx265'):
            self._cmd.extend(['-preset', preset, '-crf', str(crf)])
            if tune and str(tune).lower() not in ('', 'none'):
                self._cmd.extend(['-tune', str(tune).lower()])
        elif vcodec == 'libvpx-vp9':
            self._cmd.extend(['-crf', str(crf), '-deadline', 'good',
                              '-cpu-used', '4'])
        if extra_v_flags:
            self._cmd.extend(extra_v_flags)
        self._cmd.extend(['-acodec', acodec])
        if target_duration is not None:
            self._cmd.extend(['-t', f'{target_duration:.3f}'])
        # NOTE: -shortest deliberately omitted. With -shortest, any rounding
        # shortfall in the video frame count truncates the AUDIO stream too,
        # which was the visible "song ends before video" bug. We pad video
        # frames to match target_duration on the engine side instead, then
        # let -t cap the output exactly.
        if ext == 'mp4' or ext == 'mov':
            self._cmd.extend(['-movflags', '+faststart'])
        self._cmd.append(output_path)

    # Потоковая запись кадров: цикл рендера кладёт готовые кадры в очередь и
    # сразу считает следующий, а фоновый тред опустошает очередь в stdin
    # ffmpeg. Запись в пайп освобождает GIL на время системного вызова,
    # поэтому кодирование ffmpeg и счёт следующего кадра идут внахлёст.
    # Очередь ограничена по памяти: полная очередь = естественный
    # backpressure (не даём кадрам копиться, если ffmpeg отстаёт).
    def _frame_writer(self):
        proc = self._proc
        q = self._frame_q
        while True:
            item = q.get()
            if item is _SENTINEL:
                break
            # После сбоя продолжаем ЗАБИРАТЬ элементы (и отбрасывать), иначе
            # продюсер навсегда заблокируется на put() в полной очереди.
            if self._write_failed:
                continue
            try:
                proc.stdin.write(item)
            except (BrokenPipeError, OSError) as e:
                self._write_failed = True
                self._write_error = e

    def open(self):
        self._closed = False
        self._write_failed = False
        self._write_error: Optional[BaseException] = None
        # Потолок буфера ~64 МиБ, но не меньше 4 и не больше 16 кадров, чтобы
        # на больших кадрах (4K) не раздувать память, а на мелких - хватало
        # запаса для внахлёста.
        bytes_per_pixel = 1.5 if self.input_pix_fmt == 'yuv420p' else 3.0
        frame_bytes = max(1, int(self.width * self.height * bytes_per_pixel))
        maxsize = max(4, min(16, int(64 * 1024 * 1024 / frame_bytes)))
        self._frame_q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._proc = subprocess.Popen(
            self._cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        # daemon: если процесс приложения завершится аварийно, тред не удержит
        # интерпретатор. Штатно тред всегда останавливается через sentinel в close().
        self._writer_thread = threading.Thread(
            target=self._frame_writer, daemon=True)
        self._writer_thread.start()
        # Тут же оседают ошибки инициализации NVENC. Хвост ограничен -
        # у шумных энкодеров ffmpeg сыпет предупреждениями на каждый GOP,
        # неограниченный список разрастался до десятков МБ на долгих
        # рендерах и подвешивал GIL на каждом append.
        self._stderr_chunks: list[bytes] = []
        self._stderr_total = [0]
        STDERR_CAP_BYTES = 64 * 1024  # последних 64 КиБ достаточно для диагностики

        def _drain(pipe, sink, total):
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    sink.append(chunk)
                    total[0] += len(chunk)
                    # Обрезаем с головы, чтобы оставался именно хвост.
                    while total[0] > STDERR_CAP_BYTES and len(sink) > 1:
                        head = sink.pop(0)
                        total[0] -= len(head)
            except Exception:
                pass
        threading.Thread(target=_drain, args=(self._proc.stderr,
                                              self._stderr_chunks,
                                              self._stderr_total),
                         daemon=True).start()
        return self

    def early_failure(self, wait: float = 0.4) -> Optional[str]:
        """Если ffmpeg уже завершился (типично при сбое инициализации HW-энкодера -
        процесс умирает раньше первого записанного кадра), ждёт `wait` секунд
        и возвращает захваченный хвост stderr. None означает, что процесс жив
        и принимает данные."""
        if self._proc is None:
            return 'sink not open'
        try:
            self._proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            return None  # ещё работает - считаем, что всё в порядке
        # Процесс умер, собираем stderr.
        tail = b''.join(self._stderr_chunks).decode(errors='replace')
        return tail[-2000:] if tail else f'ffmpeg exited (rc={self._proc.returncode})'

    def write(self, frame_bytes: bytes) -> bool:
        """Ставит кадр в очередь на запись. False - если пайп уже мёртв.

        Возврат False означает "прекращай подавать кадры": цикл рендера на
        это реагирует выходом. Реальная запись идёт в фоновом треде; ошибка
        пайпа проявится здесь как _write_failed на следующем вызове.
        """
        if (self._proc is None or self._frame_q is None
                or self._closed or self._write_failed):
            return False
        # put() блокируется при полной очереди - это и есть backpressure.
        # Тред-писатель всегда продолжает забирать элементы (даже после сбоя),
        # поэтому put() не зависнет навсегда.
        self._frame_q.put(frame_bytes)
        return True

    def close(self):
        # Идемпотентность: повторный close() безопасен.
        if self._proc is None or self._closed:
            self._closed = True
            return
        self._closed = True
        # Останавливаем тред-писателя: sentinel гарантированно разбирается даже
        # если очередь была полной (писатель всегда потребляет).
        if self._frame_q is not None:
            try:
                self._frame_q.put(_SENTINEL)
            except Exception:
                pass
        if self._writer_thread is not None:
            self._writer_thread.join()
        # Все поставленные в очередь кадры уже записаны - теперь закрываем stdin,
        # чтобы ffmpeg до-финализировал файл, и ждём его завершения.
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except Exception:
            pass
        self._proc.wait()
