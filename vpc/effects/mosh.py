"""Настоящий датамош на уровне кодека (PyAV / libavcodec MPEG-4).

OpticalFlowEffect в core.py лишь ПРИБЛИЖАЕТ вид датамоша, искажая предыдущий
кадр по оптическому потоку. Этот модуль воспроизводит реальный эффект: в
процессе крутится настоящая пара кодер+декодер MPEG-4, и эффект делает ту же
хирургию битстрима, что классические тулзы датамоша (aviglitch, datamosher,
autodatamosh) делают с AVI-файлами:

  melt  - на склейке кодер принудительно выдаёт I-frame, и этот I-frame
          выбрасывается. Декодер продолжает применять векторы движения и
          DCT-остатки следующих P-frame к устаревшей опорной картинке, из-за
          чего старая сцена тянется и размазывается по движению новой, и
          "заживает" только пятнами там, где кодер выдаёт intra-макроблоки.
          Это канонический мош через выброс I-frame.
  bloom - один пакет P-frame декодируется многократно подряд, поэтому его
          поле движения накапливается кадр за кадром (классический "bloom"
          через дублирование P-frame, когда движущиеся области разрастаются
          сами из себя).

Размазывание, структура блоков 16x16 и пятнистое самозаживление - всё это
результат реальной motion compensation libavcodec, ничего не симулируется.

Эффект работает как обычный chain-эффект: на входе один кадр, на выходе один
кадр, поэтому он ведёт себя одинаково в draft, preview, final и passthrough
режимах и никогда не рассинхронизирует звук. Пара кодек существует только
пока идёт эпизод моша; вне эпизода кадры проходят насквозь без затрат.
"""
from __future__ import annotations

import random
from fractions import Fraction
from typing import Optional

import cv2
import numpy as np

from vpc.analyzer import Segment, SegmentType
from .base import BaseEffect, _ensure_uint8

try:
    import av
    from av.video.frame import PictureType as _PictureType
    _AV_OK = True
except ImportError:                                    # pragma: no cover
    av = None
    _PictureType = None
    _AV_OK = False

# Номинальный fps для управления битрейтом кодера. Chain не знает реальный
# fps вывода; это значение влияет только на целевой битрейт, не на тайминг кадров.
_NOMINAL_FPS = 24


class TrueDatamoshEffect(BaseEffect):
    """Настоящий датамош через выброс I-frame / дублирование P-frame.

    Гейтинг по СЕГМЕНТАМ (как логика склеек движка, а не как обычный
    покадровый базовый класс): каждый новый сегмент подходящего типа,
    прошедший бросок chance, запускает (или продолжает) эпизод моша.
    Идущие подряд "выстрелившие" сегменты сливаются в один непрерывный
    melt - тот самый datamix-вид. Первый сегмент, не прошедший бросок,
    завершает эпизод - это выглядит ровно как мошнутый поток, упёршийся
    в уцелевший keyframe: картинка резко становится чистой.
    """
    trigger_types = [SegmentType.NOISE, SegmentType.SUSTAIN,
                     SegmentType.IMPACT, SegmentType.DROP]

    MODES = ('melt', 'bloom', 'hybrid')

    def __init__(self, mode: str = 'melt', bloom_frames: int = 8,
                 crunch: float = 0.35, **kw):
        super().__init__(**kw)
        self.mode = mode if mode in self.MODES else 'melt'
        self.bloom_frames = max(2, int(bloom_frames))
        self.crunch = float(min(1.0, max(0.0, crunch)))

        self.prev_frame: Optional[np.ndarray] = None
        self._seg_id = None
        self._enc = None
        self._dec = None
        # (encode_w, encode_h) - округлено до чётного; (out_w, out_h) - размер chain.
        self._enc_size = None
        self._out_size = None
        self._last_out: Optional[np.ndarray] = None
        self._bloom_pkt: Optional[bytes] = None
        self._bloom_left = 0
        self._await_bloom_pkt = False
        self._fails = 0
        self._broken = not _AV_OK
        if not _AV_OK:
            print('[FX-FAIL] TrueDatamoshEffect: PyAV (av) is not installed; '
                  'effect is inert.')

    # ── контракт chain ───────────────────────────────────────────────────
    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        # Переопределение с состоянием (как у OpticalFlowEffect): предыдущий
        # кадр нужно отслеживать на КАЖДОМ кадре, чтобы эпизод мог
        # проинициализировать декодер картинкой, реально бывшей на экране в момент склейки.
        seg_id = (seg.t_start, seg.t_end, seg.type)
        new_seg = seg_id != self._seg_id
        self._seg_id = seg_id

        if self._broken or not self.enabled:
            self.prev_frame = frame
            return frame
        if self.prev_frame is not None and self.prev_frame.shape != frame.shape:
            # Разрешение поменялось на лету (новый рендер / новый chain) -
            # пара кодек рассчитана на старые кадры, эпизод приходится сбросить.
            self._teardown()
            self.prev_frame = frame
            return frame

        try:
            out = self._step(frame, seg, draft, new_seg)
        except Exception as e:
            # av кидает свои специфичные ошибки (av.error.*); любой сбой
            # кодека не должен ронять цикл рендера.
            self._fails += 1
            print(f'[FX-FAIL] TrueDatamoshEffect: {e!r} (fail {self._fails})')
            self._teardown()
            if self._fails >= 3:
                self._broken = True
                print('[FX-FAIL] TrueDatamoshEffect disabled after repeated '
                      'codec failures.')
            out = frame
        self.prev_frame = frame
        return out

    def _apply(self, frame, seg, draft):  # pragma: no cover - unused
        # BaseEffect требует этот метод, но гейтинг реализован в apply().
        return frame

    # ── state machine эпизода ────────────────────────────────────────────
    def _step(self, frame: np.ndarray, seg: Segment, draft: bool,
              new_seg: bool) -> np.ndarray:
        if new_seg:
            fire = (seg.type in self.trigger_types
                    and random.random() <= self.chance)
            if not fire:
                # Чистый ресинк - как сохранившийся keyframe в реальном моше.
                self._teardown()
                return frame
            return self._cut(frame, seg, draft)
        if self._enc is None:
            return frame
        return self._continue(frame)

    def _pick_event(self) -> str:
        if self.mode == 'hybrid':
            return random.choice(('melt', 'bloom', 'both'))
        return self.mode

    def _cut(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        """Первый кадр выстрелившего сегмента - момент хирургии битстрима."""
        if self._enc is None:
            self._start_episode(frame, draft)
        event = self._pick_event()

        # Взводим bloom до кодирования, чтобы захватить самый первый P нового
        # сегмента (тот, где рассогласование движения максимально).
        if event in ('bloom', 'both'):
            n = 2 + int(round(self.scaled_intensity(seg)
                              * (self.bloom_frames - 2)))
            self._bloom_left = n
            self._bloom_pkt = None
            self._await_bloom_pkt = True

        packets = self._encode(frame, force_i=True)
        if event == 'bloom':
            # Чистый bloom не трогает поток: декодер ресинкается на I-frame,
            # а следующий P дублируется уже в _continue().
            return self._feed(packets, frame)
        # melt / both: принудительный I-frame выбрасывается. Декодер держит
        # предыдущую картинку этот один кадр (так же ведут себя реальные моши
        # там, где вырезан keyframe) и дальше плавится начиная со следующего P.
        return self._held(frame)

    def _continue(self, frame: np.ndarray) -> np.ndarray:
        packets = self._encode(frame, force_i=False)

        if self._await_bloom_pkt:
            p_pkts = [p for p in packets if not p.is_keyframe]
            if p_pkts:
                self._bloom_pkt = bytes(p_pkts[0])
                self._await_bloom_pkt = False

        if self._bloom_left > 0 and self._bloom_pkt is not None:
            # Скармливаем тот же P-пакет повторно: его векторы движения
            # накапливаются на текущей опорной картинке декодера. Свежие
            # пакеты для этого входного кадра отбрасываются - это и держит
            # опорную картинку расходящейся, в этом суть эффекта.
            self._bloom_left -= 1
            pkt = av.Packet(self._bloom_pkt)
            return self._feed([pkt], frame)

        # Если кодер посреди эпизода всё же подсунет keyframe, он ресинканёт
        # картинку и убьёт melt - выбрасываем его, как это делают тулзы
        # датамоша, вычищая все I-frame в затронутом диапазоне.
        p_pkts = [p for p in packets if not p.is_keyframe]
        if not p_pkts and packets:
            return self._held(frame)
        return self._feed(p_pkts, frame)

    # ── обвязка кодека ───────────────────────────────────────────────────
    def _start_episode(self, frame: np.ndarray, draft: bool) -> None:
        h, w = frame.shape[:2]
        ew, eh = w & ~1, h & ~1          # yuv420p требует чётные размеры
        self._enc_size = (ew, eh)
        self._out_size = (w, h)

        enc = av.CodecContext.create('mpeg4', 'w')
        enc.width, enc.height = ew, eh
        enc.pix_fmt = 'yuv420p'
        enc.time_base = Fraction(1, _NOMINAL_FPS)
        enc.framerate = Fraction(_NOMINAL_FPS, 1)
        # Одна длинная P-цепочка: огромный GOP плюс отключённое обнаружение
        # смены сцены - та же форма потока, что prepare_datamosh_source() строит через ffmpeg.
        enc.gop_size = 10 ** 6
        # crunch отображается в биты на пиксель: 0.60 bpp - визуально чисто,
        # 0.05 bpp - густое макроблочное месиво. Ниже битрейт = крупнее блоки размазывания.
        bpp = 0.60 - 0.55 * self.crunch
        enc.bit_rate = max(64_000, int(ew * eh * _NOMINAL_FPS * bpp))
        opts = {
            'sc_threshold': '1000000000',  # не давать кодеру самому вставлять I на склейках
            'flags': '+mv4',               # векторы 8x8: тоньше и "мыльнее" размазывание
        }
        if not draft:
            opts['mbd'] = 'rd'             # лучше motion estimation = более гладкий melt
        enc.options = opts
        enc.open()

        dec = av.CodecContext.create('mpeg4', 'r')
        dec.open()
        self._enc, self._dec = enc, dec
        self._last_out = None
        self._bloom_pkt = None
        self._bloom_left = 0
        self._await_bloom_pkt = False

        # Инициализируем оба контекста картинкой, которая сейчас на экране:
        # заголовки этого I-frame настраивают декодер, а его данные становятся
        # той самой устаревшей опорной картинкой, от которой всё будет плавиться.
        base = self.prev_frame if self.prev_frame is not None else frame
        self._feed(self._encode(base, force_i=True), base)

    def _encode(self, frame: np.ndarray, force_i: bool) -> list:
        ew, eh = self._enc_size
        arr = frame
        if (frame.shape[1], frame.shape[0]) != (ew, eh):
            arr = cv2.resize(frame, (ew, eh))
        arr = np.ascontiguousarray(arr)
        vf = av.VideoFrame.from_ndarray(arr, format='rgb24')
        vf = vf.reformat(format='yuv420p')
        if force_i:
            vf.pict_type = _PictureType.I
        return list(self._enc.encode(vf))

    def _feed(self, packets: list, fallback: np.ndarray) -> np.ndarray:
        out = None
        for pkt in packets:
            for df in self._dec.decode(pkt):
                out = df.to_ndarray(format='rgb24')
        if out is None:
            return self._held(fallback)
        w, h = self._out_size
        if (out.shape[1], out.shape[0]) != (w, h):
            out = cv2.resize(out, (w, h))
        out = _ensure_uint8(out)
        self._last_out = out
        return out

    def _held(self, fallback: np.ndarray) -> np.ndarray:
        return self._last_out if self._last_out is not None else fallback

    def _teardown(self) -> None:
        self._enc = None
        self._dec = None
        self._last_out = None
        self._bloom_pkt = None
        self._bloom_left = 0
        self._await_bloom_pkt = False
