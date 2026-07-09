"""Disc VPC 01 - Tk GUI, генерируется из реестра эффектов.

Секции, слайдеры, чекбоксы, тултипы и словарь cfg - всё выводится из
`registry.EFFECTS`. Добавление нового эффекта в реестр делает его видимым
в GUI автоматически - никаких изменений здесь не требуется.

Поддерживаемые возможности:
  * Тултип / всплывашка [?] на каждой метке и слайдере - использует поле
    `tooltip` из EffectSpec / ParamSpec.
  * Переопределение "always-on" для каждого эффекта - у аккордеон-блока
    каждого эффекта есть чекбокс `always` + слайдер интенсивности, которые
    обходят его триггеры.
  * Режим разрешения: preset / source / custom.
  * Блок формульного эффекта - тот же механизм реестра, со свободным полем
    Entry для выражения.
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import tempfile
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw
import io
import base64

from vpc.render import BreakcoreEngine
from vpc.render.quality import (
    QUALITY_PRESETS, TUNE_VALUES, CUSTOM as QUALITY_CUSTOM,
    preset_names as quality_preset_names, detect_preset as detect_quality,
)
from vpc.render.encoders import available_specs as available_encoder_specs
from vpc.registry import EFFECTS, GROUP_ORDER, default_cfg, bi
from vpc.registry import ACCORDION_HIDDEN_GROUPS, GROUP_DISPLAY_NAMES
from vpc.mystery import MYSTERY_KNOBS, MYSTERY_ALWAYS_LABELS
from vpc.effects.formula import compile_formula
from vpc.paths import presets_path, temp_preview_path
from vpc.render.preview_player import PreviewPlayer

try:
    import sounddevice as _sd
    import soundfile as _sf
    _AUDIO_OK = True
except Exception:
    _AUDIO_OK = False

try:
    cv2.setLogLevel(0)
except Exception:
    pass


def _wait_file_writable(path: str, timeout: float = 2.0) -> None:
    """Ждёт в цикле, пока `path` не откроется для эксклюзивной записи, либо пока не пройдёт `timeout` секунд.

    На Windows 11 cv2.VideoCapture использует backend Media Foundation (MSMF),
    который освобождает нижележащий файловый хендл ОС асинхронно через
    рабочие потоки MF после вызова cap.release(). Из-за этого хендл может
    оставаться живым ещё ~100-200 мс после того, как поток Python завершился
    и join() вернул управление. ffmpeg нужен доступ GENERIC_WRITE (без
    расшаривания на чтение), чтобы перезаписать файл; пока MSMF всё ещё
    держит его с FILE_SHARE_READ, такое открытие падает с
    ERROR_SHARING_VIOLATION. Эта функция повторяет попытку каждые 50 мс через
    Win32 API CreateFile с dwShareMode=0 (эксклюзивный доступ) - это самая
    строгая проверка: если она проходит, то и ffmpeg пройдёт.
    """
    if sys.platform != 'win32':
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    # Явно задаём restype, чтобы Python получал знаковое 32-битное число.
    # Без этого ctypes и так по умолчанию использует c_int, но явное указание
    # избегает ловушки сравнения: INVALID_HANDLE_VALUE как c_int равен -1,
    # а ctypes.c_void_p(-1).value на 64-битной системе - это
    # 0xFFFFFFFFFFFFFFFF - как числа Python они не равны, хотя представляют
    # один и тот же хендл, из-за чего проверка тихо сломалась бы.
    k32.CreateFileW.restype = ctypes.c_long  # знаковое 32-бит; INVALID_HANDLE_VALUE → -1
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_NONE = 0
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = -1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        h = k32.CreateFileW(path, GENERIC_WRITE, FILE_SHARE_NONE,
                            None, OPEN_EXISTING, 0, None)
        if h != INVALID_HANDLE_VALUE:
            k32.CloseHandle(h)
            return
        time.sleep(0.05)

# ── цвета Win95 ──
C_SILVER = '#C0C0C0'
C_DARK_GRAY = '#808080'
C_BLACK = '#000000'
C_WHITE = '#FFFFFF'
C_TITLE_BAR = '#000080'
C_TEXT = '#000000'
C_BLUE_LIGHT = '#D0D8F0'
C_GREEN_DOT = '#00AA00'
C_RED_BTN = '#CC2222'

# ── палитра TUI (вкладка FORMULA) ──
C_TUI_BG = '#0A1208'           # глубокий терминальный фон
C_TUI_FG = '#39FF14'           # фосфорный зелёный
C_TUI_DIM = '#1F8C0E'          # приглушённый зелёный для разделителей / меток
C_TUI_AMBER = '#FFB000'        # янтарный для заголовков / значений
C_TUI_RED = '#FF5555'          # красный для ошибок
C_TUI_HL = '#0F1F0A'           # тонкая подсветка строки

# ── Встроенные пресеты ─────────────────────────────────────────────────
# Только одна запись: Empty. Все эффекты выключены, все слайдеры на 0,
# mystery обнулён, silence_mode = none. Всё остальное подставляется из
# default_cfg(). Пользовательские пресеты, созданные через UI, живут
# рядом с этой записью в presets.json (с builtin=False).
EMPTY_PRESET_NAME = 'Empty'
PRESETS = {
    EMPTY_PRESET_NAME: {},
}


# ── Эффекты, считающиеся "меняющими цвет" ────────────────────────────────
# Когда включён чекбокс "Hide color effects", они принудительно
# отключаются И скрываются из аккордеона EFFECTS. Исходные состояния
# сохраняются при включении режима и восстанавливаются при выключении,
# чтобы пользователь мог повторно войти в режим без потери настроек.
#
# Список подобран вручную - это эффекты, которые напрямую портят RGB-каналы
# или исходную палитру так, что нарушается "цветовая достоверность"
# входного видео.
# Эффекты, которые существенно ломают соответствие входных и выходных
# кадров 1:1 в режиме passthrough, вставляя в поток лишние кадры (Stutter
# дублирует кадры, Flash вставляет строб на 1-2 кадра, Datamosh
# подменяет источник на предзаготовленный с выброшенными I-кадрами). Их
# блоки скрываются через pack_forget(), когда включён режим passthrough,
# точно так же, как COLOR_EFFECT_KEYS делает для hide-color-fx, а их
# флаги cfg принудительно выключаются, чтобы движок тоже не мог случайно
# их запустить.
# Сейчас пусто: stutter / flash / datamosh теперь все РАБОТАЮТ в режиме
# passthrough.
#   • Optical Flow (старые ключи "Datamosh") идёт через OpticalFlowEffect
#     по обычной цепочке эффектов - предзаготовка в passthrough не нужна.
#   • True Datamosh (fx_truemosh) - обычный эффект цепочки 1-в-1-выход
#     (пара кодеков в процессе), так что особый случай для passthrough
#     ему тоже не нужен.
#   • Stutter и Flash в цикле passthrough используют режим REPLACE вместо
#     режима INSERT с вырезанием: `cap.grab()` источника продвигает
#     указатель чтения как обычно, но записываемый кадр подменяется
#     удержанной копией (stutter) или цветом вспышки (flash). Количество
#     выходных кадров остаётся 1:1 с входными, поэтому звук источника не
#     теряет синхронизацию.
# Оставлено как кортеж-константа, чтобы существующий механизм
# снапшот/восстановление продолжал работать, если какой-то ключ снова
# добавят.
PASSTHROUGH_HIDDEN_KEYS = ()


COLOR_EFFECT_KEYS = (
    # Прямая перезапись цвета / инверсии / квантование.
    'fx_flash',           # Flash Frame - заменяет кадр белым/чёрным
    'fx_negative',        # Negative - 255 - пиксель по каждому каналу
    'fx_bitcrush',        # Bitcrush / Posterize - квантует к 1-7 уровням
    'fx_dither',          # Dithering - Bayer 4x4 квантует палитру до 2-16 уровней
    'fx_bsod_shred',      # BSOD Shred - заменяет полосы NT-синим + белым текстом
    # Трансформации оттенка / цветности (палитра перекрашивается, а не сохраняется).
    'fx_rgb',             # RGB Shift - хроматические каёмки
    'fx_colorbleed',      # Color Bleed / VHS Smear - размытие каналов
    'fx_temporal_rgb',    # Temporal RGB Shift - R/G/B из разных кадров
    'fx_echo',            # Echo Compound - явные эхо со сдвигом оттенка +30 град
    'fx_kali',            # Kali Mirror - композит включает инверсию 255-пиксель
    'fx_wrong_sub',       # Wrong Chroma Sub - блоки цветности выходят за края
    # Агрессивные числовые искажения, чей видимый эффект - сдвиги цвета.
    'fx_waveshaper',      # Waveshaper - tanh-насыщение / искажение оттенка
    'fx_fft_phase',       # FFT Phase Corrupt - цветные интерференционные узоры
    'fx_dtype_corrupt',   # Dtype Reinterpret - побайтовый вид float16 = цветовые обрывы
    'fx_bad_signal',      # Bad Signal - случайно окрашенные вертикальные шумовые полосы
    # Форензик / акустические отображения, чей результат заменяет палитру источника.
    'fx_ela',             # ELA - тепловая карта уровня ошибок заменяет цвета
    'fx_spatial_reverb',  # Spatial Reverb - свёртка по строкам, палитра смешивается по горизонтали
    # Составная цепочка эффектов: любой из случайных под-эффектов выше.
    'fx_cascade',         # Glitch Cascade - цепочка случайных эффектов, меняющих палитру
)


# ── палитра BSOD (вкладка FORMULA) ───────────────────────────────────────
# Классический синий экран Win9x - высокая контрастность, моноширинный
# шрифт. Используется, когда стандартная серебристая тема Win95 слишком
# мягкая, чтобы читать код на её фоне.
C_BSOD_BG = '#0000AA'
C_BSOD_FG = '#FFFFFF'
C_BSOD_ACCENT = '#FFFF55'   # bright yellow for headings / values
C_BSOD_DIM = '#AAAAFF'      # muted blue-white for hints
C_BSOD_RED = '#FF5555'      # error red
C_BSOD_HL = '#1A1ABB'       # subtle highlight row


# ────────────────────────────────────────────────────────────────────────
class Tooltip:
    """Всплывающая подсказка при наведении - используется на метках, слайдерах, значках [?]."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind('<Enter>', self._enter)
        widget.bind('<Leave>', self._leave)

    def _enter(self, e):
        if self.tip or not self.text:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f'+{e.x_root + 14}+{e.y_root + 8}')
        tk.Label(self.tip, text=self.text, bg='#FFFFCC', fg=C_BLACK,
                 font=('MS Sans Serif', 9), bd=1, relief='solid',
                 padx=4, pady=2, wraplength=420, justify='left').pack()

    def _leave(self, e):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ────────────────────────────────────────────────────────────────────────
class MainGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Disc VPC 01')
        # Ограничиваем начальный размер экраном, чтобы окно не открывалось больше
        # дисплея (частый случай на ноутбуках / небольших Mac, где фиксированные
        # 1500x900 вылезли бы за границы экрана и скрыли элементы управления).
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = min(1500, sw - 80), min(900, sh - 120)
            self.geometry(f'{max(900, w)}x{max(700, h)}')
        except Exception:
            self.geometry('1500x900')
        self.minsize(900, 700)
        self.configure(bg=C_SILVER)
        self.resizable(True, True)
        # Иконка окна (Tk использует iconphoto независимо от ОС - покрывает
        # оконные менеджеры Linux и заголовок Tk на Windows/macOS, где
        # иконка на уровне ОС берётся из бандла/exe). Путь к ресурсу
        # разрешается и для собранного PyInstaller-бандла (sys._MEIPASS),
        # и для рабочей копии из репозитория.
        try:
            base = getattr(sys, '_MEIPASS', None) or os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            ico_png = os.path.join(base, 'CDDRIVE.png')
            if os.path.exists(ico_png):
                self._icon_img = tk.PhotoImage(file=ico_png)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

        self.audio_path = ''
        self.video_paths = []
        self.overlay_dir = ''
        self.temp_preview_path = str(temp_preview_path())

        self.progress_var = tk.DoubleVar(value=0)
        # Плеер предпросмотра, синхронизированный по звуку (vpc.render.preview_player).
        # Владеет собственным рабочим потоком и мастер-часами; GUI лишь
        # передаёт кадры в поток Tk и управляет транспортом. Схема синхронизации
        # описана в PreviewPlayer (видео - чистая функция звуковых часов, без дрейфа).
        self._player = None
        self._audio_wav = None
        # Кооперативная отмена: GUI выставляет engine.abort = True из главного
        # потока; рабочий поток это читает. GIL CPython делает обычную запись
        # атрибута атомарной, так что блокировка не нужна.
        self.engine = None
        # Состояние громкости/mute плеера предпросмотра (применяется вживую через плеер).
        self._volume = 0.8
        self._volume_pre_mute = 0.8
        self._muted = False

        self.style = ttk.Style(self)
        self._setup_styles()
        self._setup_vars()
        self._build_ui()
        self._setup_dnd()          # опциональный drag-and-drop (tkinterdnd2)
        self._load_presets_file()
        self._setup_paint_canvas_trace()

    # ─── styles ───
    def _setup_styles(self):
        self.style.theme_use('clam')
        self.option_add('*Font', 'MS_Sans_Serif 10')
        base = {'background': C_SILVER, 'foreground': C_TEXT, 'font': 'MS_Sans_Serif 10'}
        self.style.configure('.', **base)
        self.style.configure('W95.TButton', background=C_SILVER, foreground=C_TEXT,
                             relief='raised', borderwidth=2)
        self.style.map('W95.TButton',
                       background=[('active', '#D6D6D6'), ('disabled', C_SILVER)],
                       relief=[('pressed', 'sunken'), ('active', 'raised')])
        # Узкий вариант для тесных полос (например, строка Copy/Clear лога) -
        # тот же вид Win95, минимальные отступы + меньший шрифт, чтобы не
        # занимать лишнее место по вертикали/горизонтали.
        self.style.configure('W95Thin.TButton', background=C_SILVER,
                             foreground=C_TEXT, relief='raised', borderwidth=1,
                             padding=(2, 0), font=('MS Sans Serif', 8))
        self.style.map('W95Thin.TButton',
                       background=[('active', '#D6D6D6'), ('disabled', C_SILVER)],
                       relief=[('pressed', 'sunken'), ('active', 'raised')])
        self.style.configure('Draft.TButton', background=C_BLUE_LIGHT, foreground=C_TEXT,
                             relief='raised', borderwidth=2, font=('MS Sans Serif', 10, 'bold'))
        self.style.configure('Preview.TButton', background='#D0EED0', foreground=C_TEXT,
                             relief='raised', borderwidth=2, font=('MS Sans Serif', 10, 'bold'))
        self.style.configure('Stop.TButton', background='#EE8888', foreground=C_WHITE,
                             relief='raised', borderwidth=2, font=('MS Sans Serif', 10, 'bold'))
        self.style.configure('ActiveTab.TButton', background='#B8C8E8', foreground=C_TEXT,
                             relief='sunken', borderwidth=2, font=('MS Sans Serif', 9, 'bold'))
        self.style.configure('FullRender.TButton', background='#404040', foreground=C_WHITE,
                             relief='raised', borderwidth=3, font=('MS Sans Serif', 11, 'bold'))
        self.style.configure('W95.TFrame', background=C_SILVER, relief='sunken', borderwidth=2)
        self.style.configure('W95.TCheckbutton', background=C_SILVER, foreground=C_TEXT,
                             font=('MS Sans Serif', 10, 'bold'))
        self.style.configure('W95.Horizontal.TScale', background=C_SILVER,
                             troughcolor=C_SILVER, relief='sunken', borderwidth=2)
        self.style.configure('W95.TCombobox', fieldbackground=C_WHITE,
                             background=C_SILVER, foreground=C_TEXT)
        self.style.configure('W95.Horizontal.TProgressbar',
                             background=C_SILVER, troughcolor=C_SILVER,
                             relief='sunken', bordercolor=C_DARK_GRAY,
                             borderwidth=2, thickness=18)
        self.style.configure('green.W95.Horizontal.TProgressbar',
                             foreground=C_TITLE_BAR, background=C_TITLE_BAR)

    # ─── vars ───
    def _setup_vars(self):
        """Tk-переменные создаются из default_cfg() реестра.

        Поверх добавляются вспомогательные переменные состояния (логика
        нарезки, mystery, экспорт, произвольное разрешение, выражение
        формулы, режим одного сегмента).
        """
        self.vars = {}
        self._defaults_all = {}

        # Логика нарезки + анализ звука
        cut_defaults = {
            'chaos_level': 0.6, 'threshold': 1.2, 'transient_thresh': 0.5,
            'min_cut_duration': 0.05, 'scene_buffer_size': 10.0,
            'use_scene_detect': False, 'snap_to_beat': False, 'snap_tolerance': 0.05,
            'use_manual_bpm': False, 'manual_bpm': 120.0,
            'passthrough_mode': False,
        }
        # Экспорт
        export_defaults = {'fps': 24.0, 'crf': 22.0, 'custom_w': 1280.0, 'custom_h': 720.0}
        # Mystery
        mystery_keys = ('VESSEL', 'ENTROPY_7', 'DELTA_OMEGA', 'STATIC_MIND',
                        'RESONANCE', 'COLLAPSE', 'ZERO', 'FLESH_K', 'DOT')
        mystery_defaults = {f'mystery_{k}': 0.0 for k in mystery_keys}
        # Переключатели "always-on" для каждого mystery-регулятора. По
        # умолчанию False - сохраняет старое поведение побитово идентичным
        # (golden-тесты mystery на это опираются).
        mystery_defaults.update({f'always_mystery_{k}': False
                                 for k in mystery_keys})

        # Значения по умолчанию из реестра
        reg = default_cfg()

        # Переключатели audio-link для каждого эффекта в режиме passthrough.
        # По одной BooleanVar на связанный эффект; по умолчанию False, чтобы
        # существующий аудио-путь оставался побитово идентичным, пока
        # пользователь сам не включит опцию. Ключи `audio_link_<key>` идут
        # через self.vars и, соответственно, через сохранение/загрузку пресетов.
        from vpc.audio.pipeline import EFFECT_AUDIO_COUPLING
        audio_link_defaults = {f'audio_link_{k}': False
                               for k in EFFECT_AUDIO_COUPLING.keys()}

        defaults = {**cut_defaults, **reg, **export_defaults,
                    **mystery_defaults, **audio_link_defaults}
        # Составные RGB-списки обрабатываются через *_r/_g/_b ints ниже - убираем ключи-списки
        for compkey in ('fx_ascii_fg', 'fx_ascii_bg', 'fx_overlay_ck_color'):
            defaults.pop(compkey, None)

        for name, val in defaults.items():
            if isinstance(val, bool):
                self.vars[name] = tk.BooleanVar(value=val)
            elif isinstance(val, str):
                self.vars[name] = tk.StringVar(value=val)
            else:
                self.vars[name] = tk.DoubleVar(value=float(val))
        self._defaults_all = dict(defaults)

        # Вспомогательные строковые переменные: silence + режим разрешения + текст формулы
        self.var_silence_mode = tk.StringVar(value='none')
        # Состояние чекбокса hide-color-effects + снапшот, снимаемый при включении
        self.var_hide_color_fx = tk.BooleanVar(value=False)
        self._color_fx_snapshot: dict = {}
        # Снапшот passthrough зеркалит `_color_fx_snapshot`: хранит исходный
        # выбор пользователя для ключей, которые мы принудительно выключаем,
        # пока passthrough включён.
        self._passthrough_snapshot: dict = {}
        # Реестр держателей чекбоксов audio-link, заполняется
        # `_build_effect_block` для каждого спека, чей enable_key есть в
        # `EFFECT_AUDIO_COUPLING`. Трейс passthrough_mode проходит по этому
        # списку, чтобы одним махом показать/скрыть все держатели, когда
        # пользователь включает или выключает passthrough. Каждая запись:
        # (enable_key, holder_frame).
        self._audio_link_holders: list = []
        self.var_resolution_mode = tk.StringVar(value='preset')
        self.var_formula_expr = tk.StringVar(value='frame')
        # Длина превью (в секундах) - только для UI, намеренно НЕ в self.vars,
        # чтобы не проходить через сохранение/загрузку пресетов (избегаем
        # риска несовместимости с уже существующими файлами пресетов).
        # Читается во время рендера через `_get_preview_seconds()`, которая
        # ограничивает диапазоном [1, 90].
        self.var_preview_seconds = tk.IntVar(value=5)
        # Поля качества кодировщика. Выпадающий список Quality - это удобство:
        # выбор пресета записывает crf/export_preset/tune ниже; ручное
        # изменение любого из них переключает список на 'Custom'. Ручное
        # редактирование всегда имеет приоритет.
        self.var_quality_preset = tk.StringVar(value='High')
        self.var_tune = tk.StringVar(value='none')
        # Защита от повторного входа: True, пока пресет Quality пишет три
        # управляемых поля, чтобы их трейсы не отбрасывали список обратно
        # на 'Custom'.
        self._applying_quality = False

        # Переменные отображения для числовых меток слайдеров
        self._display_vars = {}
        for name, dvar in self.vars.items():
            if not isinstance(dvar, tk.DoubleVar):
                continue
            sv = tk.StringVar()
            self._display_vars[name] = sv
            int_keys = {'fps', 'crf', 'fx_ascii_size', 'scene_buffer_size',
                        'custom_w', 'custom_h'}
            int_suffixes = ('_r', '_g', '_b', '_lag', '_iters', '_factor',
                            '_frames', '_softness', '_octaves', '_depth')
            # snap_tolerance - маленькое float-значение (0.01..0.15) - включение
            # `_tolerance` в int_suffixes было багом: оно принудительно
            # округляло отображение слайдера до int(0), и слайдер переставал реагировать.
            float_overrides = {'snap_tolerance'}
            is_int = ((name in int_keys
                       or any(name.endswith(s) for s in int_suffixes))
                      and name not in float_overrides)

            def _make_trace(dv, sv, int_mode):
                def _cb(*_):
                    v = dv.get()
                    sv.set(str(int(round(v))) if int_mode else f'{v:.2f}')
                dv.trace_add('write', _cb)
                _cb()
            _make_trace(dvar, sv, is_int)

    # ─── ui ───
    # Ширина боковой панели зафиксирована: длинные имена файлов НЕ должны
    # расширять колонку, иначе центральная и правая панели сожмутся.
    SIDEBAR_W = 260

    def _build_ui(self):
        # weight=0 + группа uniform удерживают боковую панель на ширине
        # SIDEBAR_W независимо от содержимого меток. Центральная и правая
        # панели затем делят оставшееся горизонтальное пространство 3:2.
        self.grid_columnconfigure(0, weight=0, minsize=self.SIDEBAR_W)
        self.grid_columnconfigure(1, weight=3, minsize=400)
        self.grid_columnconfigure(2, weight=2, minsize=300)
        self.grid_rowconfigure(0, weight=1)

        # Боковая панель - propagate=False фиксирует её ширину на SIDEBAR_W,
        # даже если дочерний виджет запрашивает больше места (например,
        # имя файла песни на 60 символов).
        sidebar = ttk.Frame(self, style='W95.TFrame', width=self.SIDEBAR_W)
        sidebar.grid(row=0, column=0, padx=(8, 4), pady=8, sticky='nsew')
        sidebar.grid_propagate(False)
        sidebar.pack_propagate(False)
        tk.Frame(sidebar, bg=C_TITLE_BAR, height=28).pack(fill='x')
        tk.Label(sidebar.winfo_children()[-1], text='Disc VPC 01',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 10, 'bold')).pack(side='left', padx=6, pady=3)
        self._build_source_files(sidebar)
        self._build_presets_panel(sidebar)
        tk.Frame(sidebar, bg=C_SILVER).pack(fill='both', expand=True)
        rf = tk.Frame(sidebar, bg=C_SILVER)
        rf.pack(fill='x', padx=6, pady=(4, 8))
        self.btn_draft = ttk.Button(rf, text='DRAFT  (5 sec / 480p)',
                                    command=lambda: self.run('draft'),
                                    style='Draft.TButton')
        self.btn_draft.pack(fill='x', pady=2, ipady=4)
        # Строка длины превью - расположена прямо над кнопкой PREVIEW, чтобы
        # регулятор длительности визуально был привязан к ней. fill='x'
        # держит сетку боковой панели стабильной; spinbox выровнен по
        # правому краю, метка по левому.
        prf = tk.Frame(rf, bg=C_SILVER)
        prf.pack(fill='x', pady=(4, 0))
        tk.Label(prf, text='Preview length (s):', bg=C_SILVER, fg=C_TEXT,
                 font=('MS Sans Serif', 9)).pack(side='left')
        ttk.Spinbox(prf, from_=1, to=90,
                    textvariable=self.var_preview_seconds,
                    width=5).pack(side='right')
        self.btn_preview = ttk.Button(rf, text='PREVIEW',
                                      command=lambda: self.run('preview'),
                                      style='Preview.TButton')
        self.btn_preview.pack(fill='x', pady=(2, 2), ipady=4)
        self.btn_run_full = ttk.Button(rf, text='RENDER FULL VIDEO',
                                       command=lambda: self.run('final'),
                                       style='FullRender.TButton')
        self.btn_run_full.pack(fill='x', pady=(4, 2), ipady=6)

        # Центр
        center = ttk.Frame(self, style='W95.TFrame')
        center.grid(row=0, column=1, padx=4, pady=8, sticky='nsew')
        tb_c = tk.Frame(center, bg=C_TITLE_BAR, height=28)
        tb_c.pack(fill='x')
        tk.Label(tb_c, text='Disc VPC 01  —  Effects',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 11, 'bold')).pack(side='left', padx=6, pady=3)
        tab_strip = tk.Frame(center, bg=C_SILVER)
        tab_strip.pack(fill='x', padx=2, pady=(2, 0))
        content_host = tk.Frame(center, bg=C_SILVER)
        content_host.pack(fill='both', expand=True, padx=2, pady=2)
        effects_frame = tk.Frame(content_host, bg=C_SILVER)
        export_frame = tk.Frame(content_host, bg=C_SILVER)
        formula_frame = tk.Frame(content_host, bg=C_BSOD_BG)
        mystery_frame = tk.Frame(content_host, bg=C_SILVER)
        self._center_frames = {
            'EFFECTS': effects_frame,
            'EXPORT': export_frame,
            'FORMULA': formula_frame,
            'MYSTERY': mystery_frame,
        }
        self._build_effects_accordion(effects_frame)
        self._build_export_panel(export_frame)
        self._build_formula_panel(formula_frame)
        self._build_mystery_panel(mystery_frame)
        self._active_center_tab = None
        self._center_tab_btns = {}
        for label, key in [('EFFECTS', 'EFFECTS'), ('EXPORT', 'EXPORT'),
                           ('FORMULA', 'FORMULA'), ('[ ? ]', 'MYSTERY')]:
            btn = ttk.Button(tab_strip, text=label, style='W95.TButton',
                             command=lambda k=key: self._switch_center_tab(k))
            btn.pack(side='left', padx=2, pady=2)
            self._center_tab_btns[key] = btn
        self._switch_center_tab('EFFECTS')

        # Правая панель: монитор превью + транспорт + элементы рендера + лог.
        # Раскладка (сверху вниз):
        #   1) Заголовок
        #   2) Preview Monitor (кадр 640×360)
        #   3) Полоса транспорта: Pause / Mute / Volume / очистка монитора
        #   4) Прогресс-бар рендера
        #   5) CANCEL RENDER (активна только пока идёт рендер)
        #   6) Status Log (кнопка Clear встроена в полосу заголовка
        #      LabelFrame, чтобы не занимать отдельную строку).
        right = ttk.Frame(self, style='W95.TFrame')
        right.grid(row=0, column=2, padx=(4, 8), pady=8, sticky='nsew')
        tb2 = tk.Frame(right, bg=C_TITLE_BAR, height=28)
        tb2.pack(fill='x')
        tk.Label(tb2, text='Live Preview & Console',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 11, 'bold')).pack(side='left', padx=6, pady=3)
        cr = tk.Frame(right, bg=C_SILVER)
        cr.pack(fill='both', expand=True, padx=2, pady=2)

        # 2) Монитор превью
        pmon = tk.LabelFrame(cr, text='Preview Monitor (640×360)',
                             bg=C_SILVER, fg=C_TEXT, bd=2, relief='sunken',
                             font=('MS Sans Serif', 10, 'bold'))
        pmon.pack(fill='x', padx=8, pady=(6, 2))
        _blank = ImageTk.PhotoImage(Image.new('RGB', (640, 360), 'black'))
        self.player_label = tk.Label(pmon, image=_blank, bg=C_BLACK, bd=2, relief='sunken')
        self.player_label.imgtk = _blank
        self.player_label.pack(padx=4, pady=4)

        # 3) Полоса транспорта - одна строка, элементы относятся к плееру
        # предпросмотра (НЕ к рендереру). Отключена, пока не загружено
        # превью; включается/выключается вместе через start_playback /
        # stop_and_clear_playback.
        pc = tk.Frame(cr, bg=C_SILVER)
        pc.pack(fill='x', padx=8, pady=(0, 4))
        self.btn_pause = ttk.Button(pc, text='PAUSE', style='W95.TButton',
                                    width=7, command=self._toggle_pause,
                                    state='disabled')
        self.btn_pause.pack(side='left', padx=(0, 4))
        self.btn_mute = ttk.Button(pc, text='MUTE', style='W95.TButton',
                                   width=7, command=self._toggle_mute,
                                   state='disabled')
        self.btn_mute.pack(side='left', padx=(0, 4))
        tk.Label(pc, text='Vol', bg=C_SILVER, fg=C_TEXT,
                 font=('MS Sans Serif', 9, 'bold')).pack(side='left')
        self.var_volume = tk.DoubleVar(value=80.0)
        self.var_volume.trace_add('write', lambda *_: self._on_volume_change())
        vol = ttk.Scale(pc, from_=0, to=100, variable=self.var_volume,
                        orient=tk.HORIZONTAL, style='W95.Horizontal.TScale')
        vol.pack(side='left', fill='x', expand=True, padx=4)
        self._bind_scale_click_jump(vol)
        self.btn_clear_monitor = ttk.Button(pc, text='X', style='W95.TButton',
                                            width=3,
                                            command=self.stop_and_clear_playback,
                                            state='disabled')
        self.btn_clear_monitor.pack(side='left')

        # 4) Прогресс рендера
        self.progress = ttk.Progressbar(cr, style='green.W95.Horizontal.TProgressbar',
                                        mode='determinate', maximum=100,
                                        variable=self.progress_var)
        self.progress.pack(fill='x', padx=8, pady=(2, 2))

        # 5) Cancel Render - отдельная кнопка на всю ширину, отключена вне
        # рендера. Выставляет engine.abort=True (кооперативная отмена
        # реализована в vpc/render/engine.py). Это НЕ то же самое, что
        # кнопка X в транспорте.
        self.btn_cancel_render = ttk.Button(
            cr, text='CANCEL RENDER',
            command=self.cancel_render,
            style='Stop.TButton', state='disabled')
        self.btn_cancel_render.pack(fill='x', padx=8, pady=(2, 4), ipady=4)

        # 6) Status log - кнопка Clear в тонкой полосе сверху над виджетом
        # Text. Используется настоящий frame (а не place()), чтобы сдвиги
        # раскладки из-за HiDPI / движка темы не могли надвинуть кнопку
        # на содержимое лога.
        cp = tk.LabelFrame(cr, text='Status Log', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='sunken', font=('MS Sans Serif', 10, 'bold'))
        cp.pack(fill='both', expand=True, padx=8, pady=(2, 6))
        log_top = tk.Frame(cp, bg=C_SILVER)
        log_top.pack(fill='x', padx=4, pady=(2, 0))
        clr_btn = ttk.Button(log_top, text='Clear', style='W95Thin.TButton',
                             width=6, command=self._clear_log)
        clr_btn.pack(side='right')
        copy_btn = ttk.Button(log_top, text='Copy', style='W95Thin.TButton',
                              width=6, command=self._copy_log)
        copy_btn.pack(side='right', padx=(0, 4))
        Tooltip(copy_btn,
                'Copy the entire log to the clipboard.\n──\n'
                'Скопировать весь лог в буфер обмена.')
        Tooltip(clr_btn, 'Clear the log.\n──\nОчистить лог.')
        self.console = tk.Text(cp, height=6, font=('Courier New', 9),
                               bg=C_WHITE, fg=C_BLACK, bd=2, relief='sunken')
        self.console.pack(fill='both', expand=True, padx=4, pady=(2, 4))

    # ─── файлы источников / пресеты / вкладки ───
    @staticmethod
    def _shorten_name(name: str, width: int = 28) -> str:
        """Обрезает имя файла до `width` символов, ставя многоточие в середине.

        `Some_very_long_song_name_2026_master_v3_final_FINAL.mp3` →
        `Some_very_long…3_final_FINAL.mp3`. Расширение остаётся видимым.
        """
        if len(name) <= width:
            return name
        keep_tail = max(8, width // 2)
        keep_head = width - keep_tail - 1
        return name[:keep_head] + '…' + name[-keep_tail:]

    def _build_source_files(self, parent):
        fp = tk.LabelFrame(parent, text='Source Files', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='groove', font=('MS Sans Serif', 10, 'bold'))
        fp.pack(pady=4, padx=6, fill='x')
        ar = tk.Frame(fp, bg=C_SILVER); ar.pack(fill='x', padx=6, pady=(4, 0))
        self._audio_dot = tk.Label(ar, text='●', fg='#AAAAAA', bg=C_SILVER, font=('MS Sans Serif', 12))
        self._audio_dot.pack(side='left', padx=(0, 4))
        # Кэшируется, чтобы режим passthrough мог её отключить: аудиофайл не
        # используется, когда движок извлекает собственную звуковую дорожку
        # исходного видео.
        self._audio_btn = ttk.Button(
            ar, text='Load Audio (WAV / MP3)',
            command=self.sel_audio, style='W95.TButton')
        self._audio_btn.pack(side='left', fill='x', expand=True)
        self.lbl_audio_name = tk.Label(fp, text='— not loaded —',
                                       bg=C_SILVER, fg=C_DARK_GRAY,
                                       font=('Courier New', 9), anchor='w',
                                       wraplength=self.SIDEBAR_W - 30,
                                       justify='left')
        self.lbl_audio_name.pack(fill='x', padx=24, pady=(0, 3))

        vr = tk.Frame(fp, bg=C_SILVER); vr.pack(fill='x', padx=6, pady=(2, 0))
        self._video_dot = tk.Label(vr, text='●', fg='#AAAAAA', bg=C_SILVER, font=('MS Sans Serif', 12))
        self._video_dot.pack(side='left', padx=(0, 4))
        ttk.Button(vr, text='Load Source Video',
                   command=self.sel_video, style='W95.TButton').pack(side='left', fill='x', expand=True)
        self.lbl_video_name = tk.Label(fp, text='— not loaded —',
                                       bg=C_SILVER, fg=C_DARK_GRAY,
                                       font=('Courier New', 9), anchor='w',
                                       wraplength=self.SIDEBAR_W - 30,
                                       justify='left')
        self.lbl_video_name.pack(fill='x', padx=24, pady=(0, 3))

    def _build_presets_panel(self, parent):
        pp = tk.LabelFrame(parent, text='Presets', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='groove', font=('MS Sans Serif', 10, 'bold'))
        pp.pack(pady=4, padx=6, fill='x')
        lbf = tk.Frame(pp, bg=C_SILVER); lbf.pack(fill='x', padx=4, pady=(4, 2))
        sb = ttk.Scrollbar(lbf, orient='vertical')
        self._presets_listbox = tk.Listbox(
            lbf, height=8, yscrollcommand=sb.set,
            bg=C_WHITE, fg=C_TEXT, selectbackground=C_TITLE_BAR,
            selectforeground=C_WHITE, font=('MS Sans Serif', 9),
            activestyle='none', bd=2, relief='sunken')
        sb.config(command=self._presets_listbox.yview)
        self._presets_listbox.pack(side='left', fill='x', expand=True)
        sb.pack(side='left', fill='y')
        self._presets_listbox.bind('<Double-Button-1>', lambda e: self._load_selected_preset())

        # Три кнопки действий с пресетами в одной строке. С `pack(side='left')`
        # каждая кнопка автоматически подгонялась под текст метки, поэтому на
        # зафиксированной 260px боковой панели 'Save Current' съедала большую
        # часть строки, а 'Delete' сжималась до одного символа. Переход на
        # grid с weight=1 на каждой колонке даёт трём кнопкам ровно треть строки.
        br = tk.Frame(pp, bg=C_SILVER); br.pack(fill='x', padx=4, pady=2)
        for col, (label, cmd) in enumerate([
                ('Load', self._load_selected_preset),
                ('Save', self._save_current_preset),
                ('Delete', self._delete_preset)]):
            ttk.Button(br, text=label, style='W95.TButton', command=cmd
                       ).grid(row=0, column=col, padx=2, sticky='ew')
            br.grid_columnconfigure(col, weight=1, uniform='preset_btns')
        self._active_preset_label = tk.Label(pp, text='Active: —',
                                             bg=C_SILVER, fg=C_DARK_GRAY,
                                             font=('MS Sans Serif', 8))
        self._active_preset_label.pack(anchor='w', padx=6, pady=(0, 4))

    def _switch_center_tab(self, key):
        if self._active_center_tab == key:
            return
        if self._active_center_tab:
            self._center_frames[self._active_center_tab].pack_forget()
            prev = self._center_tab_btns.get(self._active_center_tab)
            if prev:
                prev.configure(style='W95.TButton')
        self._center_frames[key].pack(fill='both', expand=True)
        self._active_center_tab = key
        ab = self._center_tab_btns.get(key)
        if ab:
            ab.configure(style='ActiveTab.TButton')

    # ─── примитивы виджетов ───
    @staticmethod
    def _parent_bg(parent):
        """Возвращает цвет фона родителя, чтобы дочерние строки сливались с ним бесшовно.

        Блоки эффектов живут внутри белых тел аккордеона, а панели
        логики нарезки / экспорта - серебристые. Жёстко прописанный
        bg=C_SILVER внутри хелперов давал видимые серебристые полосы на
        белом фоне - симптом "сломанного UI". Чтение цвета у родителя
        делает каждый хелпер самоадаптирующимся.
        """
        try:
            return parent.cget('bg') or C_SILVER
        except tk.TclError:
            return C_SILVER

    def _bind_scale_click_jump(self, scale: ttk.Scale):
        """Делает так, чтобы ttk.Scale прыгал в точку клика И при этом продолжало работать перетаскивание.

        Поведение ttk.Scale по умолчанию в теме `clam` при клике по желобу -
        это page-step (значение скачет к тому краю, что ближе к клику),
        что неюзабельно для непрерывных слайдеров. Возврат 'break' из
        обработчика нажатия останавливал page-step, но заодно блокировал
        привязку класса, которая обычно запускает перетаскивание - так
        что drag тоже переставал работать.

        Решение - обрабатывать И press, И B1-Motion самостоятельно. Нажатие
        задаёт значение; последующее движение с зажатой B1 продолжает его
        менять. Оба обработчика возвращают 'break', чтобы привязка
        page-step класса никогда не срабатывала. Клики по ползунку слайдера
        по-прежнему отображаются в его центр (визуально ничего не меняя),
        а дальше drag естественным образом продолжает оттуда.
        """
        def _value_from_x(x: int):
            try:
                lo = float(scale.cget('from'))
                hi = float(scale.cget('to'))
            except tk.TclError:
                return None
            w = scale.winfo_width() or 1
            # Учитываем половину ширины ползунка с каждой стороны, чтобы
            # клики по видимым краям чисто отображались в lo/hi.
            margin = 6
            xc = max(margin, min(w - margin, x))
            frac = (xc - margin) / max(1, w - 2 * margin)
            return lo + frac * (hi - lo)

        def _set(ev):
            v = _value_from_x(ev.x)
            if v is not None:
                scale.set(v)
            return 'break'
        scale.bind('<Button-1>', _set)
        scale.bind('<B1-Motion>', _set)

    def _row_with_help(self, parent, text, tooltip='', mono=False):
        """Метка + маленький значок справки [?] справа; оба несут тултип."""
        bg = self._parent_bg(parent)
        row = tk.Frame(parent, bg=bg)
        row.pack(fill='x', padx=(8, 8), pady=(2, 0))
        f = ('Courier New', 9, 'bold') if mono else ('MS Sans Serif', 9)
        lbl = tk.Label(row, text=text, bg=bg, fg=C_TEXT, font=f, anchor='w')
        lbl.pack(side='left')
        if tooltip:
            help_lbl = tk.Label(row, text='[?]', bg=bg, fg='#3060A0',
                                cursor='question_arrow',
                                font=('MS Sans Serif', 8, 'bold'))
            help_lbl.pack(side='left', padx=(4, 0))
            Tooltip(lbl, tooltip); Tooltip(help_lbl, tooltip)
        return row

    def _row_with_help_popup(self, parent, text, short_tip, full_text, mono=False):
        """Как _row_with_help, но значок [?] открывает модальное окно с
        полным текстом вместо одной лишь всплывающей подсказки.

        Используется для полей, чьё объяснение слишком длинное для
        всплывающей подсказки без переполнения экрана (Codec - типичный
        случай - двуязычное EN/RU описание с оговорками про аппаратные
        кодировщики). Наведение по-прежнему показывает короткую однострочную
        сводку, чтобы у пользователя была подсказка без клика.
        """
        bg = self._parent_bg(parent)
        row = tk.Frame(parent, bg=bg)
        row.pack(fill='x', padx=(8, 8), pady=(2, 0))
        f = ('Courier New', 9, 'bold') if mono else ('MS Sans Serif', 9)
        lbl = tk.Label(row, text=text, bg=bg, fg=C_TEXT, font=f, anchor='w')
        lbl.pack(side='left')
        # Отдельный курсор + подкрашенный фон, чтобы значок заметно отличался
        # от обычного [?] с тултипом - сигнализирует "кликни для подробностей".
        help_lbl = tk.Label(row, text='[ ? ]', bg='#E8E8FF', fg='#1A1A80',
                            cursor='hand2', bd=1, relief='raised',
                            font=('MS Sans Serif', 8, 'bold'),
                            padx=2)
        help_lbl.pack(side='left', padx=(6, 0))
        if short_tip:
            Tooltip(lbl, short_tip); Tooltip(help_lbl, short_tip + '\n(click for full info)')
        help_lbl.bind('<Button-1>',
                      lambda _e: self._open_help_popup(text, full_text))
        return row

    def _open_help_popup(self, title, body):
        """Псевдо-модальное окно с прокручиваемым телом. Использует Toplevel +
        transient + grab, чтобы оно плавало над главным окном без настоящей
        ОС-модальности (grab_set у Tk достаточно для окна справки).

        Размер ~600x460 с вертикальной прокруткой - помещается на любом
        экране от 800x600 и избегает проблемы переполнения экрана, которая
        была у длинных всплывающих подсказок, которые это окно заменяет.
        """
        win = tk.Toplevel(self)
        win.title(f'Help — {title}')
        win.configure(bg=C_SILVER)
        win.transient(self)
        win.geometry('600x460')
        win.resizable(True, True)
        # Заголовок (в стиле Win95, соответствует остальному приложению).
        tb = tk.Frame(win, bg=C_TITLE_BAR, height=24)
        tb.pack(fill='x')
        tk.Label(tb, text=title, fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 10, 'bold')).pack(side='left',
                                                           padx=6, pady=2)
        # Тело: Text + Scrollbar (Text, а не Label, чтобы пользователь мог
        # выделять / копировать фрагменты вроде названий кодеков).
        body_frame = tk.Frame(win, bg=C_SILVER)
        body_frame.pack(fill='both', expand=True, padx=8, pady=(8, 0))
        sb = ttk.Scrollbar(body_frame, orient='vertical')
        sb.pack(side='right', fill='y')
        txt = tk.Text(body_frame, wrap='word',
                      font=('MS Sans Serif', 10),
                      bg=C_WHITE, fg=C_BLACK, bd=2, relief='sunken',
                      yscrollcommand=sb.set)
        txt.pack(side='left', fill='both', expand=True)
        sb.configure(command=txt.yview)
        txt.insert('1.0', body)
        txt.configure(state='disabled')
        # Кнопка Close + привязка Esc для выхода с клавиатуры.
        bottom = tk.Frame(win, bg=C_SILVER)
        bottom.pack(fill='x', padx=8, pady=8)
        ttk.Button(bottom, text='Close', style='W95.TButton',
                   width=10, command=win.destroy).pack(side='right')
        win.bind('<Escape>', lambda _e: win.destroy())
        # Центрируем поверх родительского окна.
        win.update_idletasks()
        try:
            px = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
            py = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
            win.geometry(f'+{max(0, px)}+{max(0, py)}')
        except tk.TclError:
            pass
        win.grab_set()
        win.focus_set()
        return win

    def _slider(self, parent, name, lo, hi, indent=False):
        """Отдельный слайдер с заголовком, показывающим текущее числовое значение."""
        pad = 20 if indent else 8
        bg = self._parent_bg(parent)
        f = tk.Frame(parent, bg=bg)
        f.pack(fill='x', padx=(pad, 8), pady=(0, 2))
        if name in self._display_vars:
            tk.Label(f, textvariable=self._display_vars[name],
                     bg=bg, fg=C_TEXT,
                     font=('MS Sans Serif', 9, 'bold'),
                     width=7, anchor='e').pack(side='right')
        sc = ttk.Scale(f, from_=lo, to=hi, variable=self.vars[name],
                       orient=tk.HORIZONTAL, style='W95.Horizontal.TScale')
        sc.pack(fill='x', side='right', expand=True)
        self._bind_scale_click_jump(sc)
        return f

    def _combo(self, parent, name, values, indent=False):
        pad = 20 if indent else 8
        bg = self._parent_bg(parent)
        f = tk.Frame(parent, bg=bg)
        f.pack(fill='x', padx=(pad, 8), pady=(0, 2))
        cb = ttk.Combobox(f, values=values, textvariable=self.vars[name],
                          style='W95.TCombobox', width=14)
        cb.pack(side='left')
        # Отслеживаем по ключу cfg + запоминаем полный список вариантов,
        # чтобы такие режимы, как hide-color-fx, могли временно ограничить
        # опции и потом восстановить их.
        cb._sb_full_values = list(values)
        if not hasattr(self, '_combos'):
            self._combos = {}
        self._combos[name] = cb
        return f

    # ─── хелперы условного включения / repack с сохранением позиции ───
    # Двухуровневый UX: HIDE для "вся эта группа элементов неактуальна в
    # текущем режиме" (блок эффекта, когда его enable_key выключен, или
    # любая запись PASSTHROUGH_HIDDEN_KEYS) и DISABLE-серый для "этот
    # элемент управления всё ещё здесь уместен, но родительский флаг
    # зафиксировал его значение" (слайдер chance, пока `always` включён,
    # поле BPM, пока snap-to-beat выключен). Disable каскадируется
    # естественно: предикат вида `lambda: a.get() and b.get()` включён,
    # только когда истинны оба родителя.

    _SKIP_RECOLOR_TEXTS = ('[?]', '[ ? ]')

    def _set_widget_enabled(self, w, enabled: bool):
        """Рекурсивно делает серым/не-серым поддерево Tk (пропускает метки-значки справки).

        Использует покомпонентный кэш (атрибут `_sb_enabled`), чтобы
        холостой вызов пропускал обращение к configure/state/cget - это
        важно, потому что применение пресета залпом вызывает трейсы всех
        эффектов, и раньше это заставляло обходить всё дерево меток/слайдеров
        на каждой записи.
        """
        prev = getattr(w, '_sb_enabled', None)
        if prev is enabled:
            # Дочерние элементы уже в нужном состоянии тоже: инвариант кэша
            # на всё поддерево означает, что рекурсия не нужна.
            return
        cls = w.winfo_class()
        try:
            if cls in ('TButton', 'TCheckbutton', 'TEntry', 'TCombobox',
                       'TScale', 'TRadiobutton'):
                w.state(['!disabled'] if enabled else ['disabled'])
            elif cls in ('Button', 'Checkbutton', 'Entry', 'Radiobutton',
                         'Scale', 'Spinbox'):
                w.configure(state=('normal' if enabled else 'disabled'))
            elif cls == 'Label':
                if w.cget('text') not in self._SKIP_RECOLOR_TEXTS:
                    w.configure(fg=(C_TEXT if enabled else C_DARK_GRAY))
        except tk.TclError:
            pass
        w._sb_enabled = enabled
        for child in w.winfo_children():
            self._set_widget_enabled(child, enabled)

    def _bind_dep(self, widgets, predicate, watch_vars):
        """Привязывает включённое состояние `widgets` к `predicate()`,
        переоцениваемому при каждой записи в любую переменную из
        `watch_vars`. Применяется сразу один раз, чтобы раскладка
        соответствовала текущим значениям переменных.
        """
        targets = list(widgets)
        def _apply(*_a):
            ok = bool(predicate())
            for w in targets:
                self._set_widget_enabled(w, ok)
        for v in watch_vars:
            v.trace_add('write', _apply)
        _apply()

    def _snapshot_pack_order(self, parent):
        """Сохраняет снапшот порядка дочерних элементов родителя И исходные
        параметры pack каждого из них, чтобы `_repack_in_order` мог вернуть
        скрытый дочерний элемент и на исходное место, и с исходной геометрией
        (padx/pady/anchor/side). Без снапшота параметров перепакованные
        слайдеры теряли отступ и прижимались к левому краю.
        """
        children = list(parent.winfo_children())
        parent._sb_initial_order = children
        info = {}
        for ch in children:
            try:
                pi = ch.pack_info()
                for k in ('in', 'before', 'after'):
                    pi.pop(k, None)
                info[ch] = pi
            except tk.TclError:
                pass
        parent._sb_pack_info = info

    def _repack_in_order(self, widget, order, **pack_kw):
        """Перепаковывает `widget` так, чтобы он оказался на исходной позиции
        из `order` (снапшот дочерних элементов родителя, снятый при сборке) И
        с исходными параметрами pack (сохранены в `_sb_pack_info`). Проходит
        по `order` вперёд от слота `widget`, чтобы найти следующего сейчас
        видимого соседа, и пакует с `before=` этим соседом. Если дальше ничего
        не видно, откатывается к обычному pack (в конец). Явный `pack_kw`
        переопределяет снапшот.

        Исправляет баг, при котором виджеты после pack_forget всегда
        оказывались снизу своего родителя (cut-виджеты passthrough, скрытые
        блоки эффектов color-fx).
        """
        parent = widget.master
        info_map = getattr(parent, '_sb_pack_info', None)
        opts = dict(info_map[widget]) if info_map and widget in info_map else {}
        opts.update(pack_kw)
        try:
            idx = order.index(widget)
        except ValueError:
            widget.pack(**opts)
            return
        for sibling in order[idx + 1:]:
            try:
                if (sibling.winfo_exists() and sibling.winfo_ismapped()
                        and sibling.master is parent):
                    widget.pack(before=sibling, **opts)
                    return
            except tk.TclError:
                continue
        widget.pack(**opts)

    # ─── effects accordion (registry-driven) ───
    def _build_effects_accordion(self, parent):
        for w in parent.winfo_children():
            w.destroy()
        outer = tk.Frame(parent, bg=C_SILVER)
        outer.pack(fill='both', expand=True)

        # Реестры, которые читает навигационный контракт. Инициализируются до
        # построения любой группы/блока (в них пишут и _acc_group, и
        # _build_effect_block).
        self._acc_groups: dict = {}
        self._effect_block_group: dict = {}
        self._fx_filter_group_snapshot = None
        # Желаемая видимость блока по фильтру, отслеживается отдельно от
        # winfo_ismapped (который тоже возвращает False, если просто свёрнута
        # группа - это разные вещи). Блоки стартуют упакованными → True.
        self._block_visible: dict = {}
        self._combos: dict = {}
        self._build_search_index()

        # Верхняя панель навигации (поиск / фильтры / переход по группам).
        # Заменяет старый тулбар с одним чекбоксом; переключатель
        # Hide color-altering теперь живёт внутри неё.
        self._build_effects_navbar(outer)

        canvas = tk.Canvas(outer, bg=C_SILVER, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        cf = tk.Frame(canvas, bg=C_SILVER)
        cf_window = canvas.create_window((0, 0), window=cf, anchor='nw')

        # Пересчитывает scrollregion И подтягивает текущий просмотр в его
        # границы. Дебаунсится через один слот after_idle, чтобы несколько
        # событий <Configure> (cf + canvas + переключение аккордеона могут
        # сработать в один тик) схлопывались в одно обновление - иначе было
        # заметное "задвоение" / мерцание бегунка скроллбара при каждом
        # раскрытии группы.
        _scroll_state = {'pending': False, 'last_w': -1}

        def _do_refresh():
            _scroll_state['pending'] = False
            bbox = canvas.bbox('all')
            if bbox is None:
                return
            x1, y1, x2, y2 = bbox
            canvas.configure(scrollregion=(x1, y1, x2, y2))
            top, _bot = canvas.yview()
            content_h = max(1, y2 - y1)
            canvas_h = canvas.winfo_height() or 1
            max_top = max(0.0, 1.0 - canvas_h / content_h)
            if top > max_top:
                canvas.yview_moveto(max_top)
            elif top < 0:
                canvas.yview_moveto(0.0)

        def _refresh_scrollregion(_evt=None):
            if _scroll_state['pending']:
                return
            _scroll_state['pending'] = True
            self.after_idle(_do_refresh)

        cf.bind('<Configure>', _refresh_scrollregion)

        def _on_canvas_configure(e):
            # Растягиваем внутренний фрейм заново только если ширина
            # реально изменилась - itemconfig с той же шириной всё равно
            # порождает каскад <Configure>, который питал обновление
            # и вызывал мерцание.
            if e.width != _scroll_state['last_w']:
                _scroll_state['last_w'] = e.width
                canvas.itemconfig(cf_window, width=e.width)
            _refresh_scrollregion()
        canvas.bind('<Configure>', _on_canvas_configure)
        self._effects_refresh_scroll = _refresh_scrollregion

        # Прокрутка колесом мыши только когда курсор над этим канвасом.
        # bind_all - единственный надёжный способ поймать события колеса,
        # которые иначе поглотили бы дочерние виджеты; паруем с Enter/Leave,
        # чтобы глобальный обработчик висел только пока курсор здесь.
        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _wheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        # Ссылки на канвас, которые использует контракт навигации для scroll_to_group.
        self._effects_canvas = canvas
        self._effects_cf = cf

        # Учёт фреймов каждого блока эффекта, чтобы переключатель color-fx hide мог
        # делать pack_forget / pack заново без пересборки всего дерева.
        self._effect_block_frames: dict = {}

        # Группа CUT LOGIC - вручную заданные поля (это не эффекты, а настройки cfg)
        body = self._acc_group(cf, 'CUT LOGIC', open=True)
        self._build_cut_logic(body)

        # Сгенерированные группы эффектов
        by_group = {}
        for spec in EFFECTS:
            by_group.setdefault(spec.group, []).append(spec)
        for group_name in GROUP_ORDER:
            if group_name in ACCORDION_HIDDEN_GROUPS:
                continue
            specs = by_group.get(group_name)
            if not specs:
                continue
            opened = group_name in ('CORE FX', 'PAINT')
            title = GROUP_DISPLAY_NAMES.get(group_name, group_name)
            if group_name == 'PAINT':
                body = self._acc_group(cf, title, open=opened, bg_color='#008080', fg_color='#FFFFFF')
            else:
                body = self._acc_group(cf, title, open=opened)
            for s in specs:
                blk = self._build_effect_block(body, s)
                self._effect_block_frames[s.enable_key] = blk
                self._effect_block_group[s.enable_key] = title
            if group_name == 'OVERLAYS':
                self._build_overlay_dir_picker(body)
            # Снимок порядка детей + опций pack, чтобы восстановление
            # passthrough/color-fx возвращало скрытые блоки на их исходные
            # места (и геометрию), а не сваливало их в конец.
            self._snapshot_pack_order(body)

        # Применяем текущее состояние hide-color-fx к только что собранным блокам.
        if self.var_hide_color_fx.get():
            self._apply_hide_color_fx(active=True, take_snapshot=False)
        # То же для passthrough - если чекбокс уже был включён (например,
        # загружен из сохранённого пресета), убеждаемся, что блоки
        # вставляющих кадры эффектов скрыты сразу после сборки.
        if self.vars.get('passthrough_mode') and self.vars['passthrough_mode'].get():
            self._apply_passthrough_hide(active=True, take_snapshot=False)

        # Устанавливаем видимость через единый источник истины, чтобы
        # активный поиск / фильтр "только активные" отражались при пересборке.
        self._recompute_block_visibility()

    # ─── навигация: индекс поиска, панель, контракт, видимость ───
    def _build_search_index(self):
        """Строит для каждого эффекта строку поиска в нижнем регистре.

        Индексирует отображаемое имя эффекта, его подсказку и подписи +
        подсказки всех параметров - чтобы запрос совпадал "как в Google"
        по имени И по описаниям, а не только по заголовку.
        """
        self._search_index: dict = {}
        for spec in EFFECTS:
            parts = [spec.label, spec.tooltip]
            for p in spec.params:
                parts.append(getattr(p, 'label', ''))
                parts.append(getattr(p, 'tooltip', ''))
            self._search_index[spec.enable_key] = ' '.join(
                str(x) for x in parts if x).lower()

    def _build_effects_navbar(self, parent):
        """Прилипающая панель навигации над аккордеоном эффектов.

        Чистый view-контроллер: владеет своими tk-переменными и управляет
        аккордеоном исключительно через контракт навигации
        (_recompute_block_visibility / expand_all_groups / scroll_to_group).
        Никогда не трогает cfg-переменные эффектов, поэтому не может
        повлиять на то, что сериализуется в пресет.
        """
        bar = tk.Frame(parent, bg=C_SILVER, bd=1, relief='groove')
        bar.pack(fill='x', side='top', padx=4, pady=(4, 2))

        # Строка 1 - поле поиска + переключатели фильтров + свернуть/развернуть.
        row1 = tk.Frame(bar, bg=C_SILVER)
        row1.pack(fill='x', padx=4, pady=(4, 2))

        srch = tk.Label(row1, text='🔍', bg=C_SILVER, fg=C_TEXT,
                        font=('MS Sans Serif', 9))
        srch.pack(side='left', padx=(2, 2))
        self.var_fx_search = tk.StringVar(value='')
        ent = ttk.Entry(row1, textvariable=self.var_fx_search, width=22)
        ent.pack(side='left', padx=(0, 2))
        self.var_fx_search.trace_add(
            'write', lambda *_: self._recompute_block_visibility())
        # Esc очищает запрос (дублирует кнопку ✕) - привычный сброс.
        ent.bind('<Escape>', lambda e: self.var_fx_search.set(''))
        clr = tk.Button(row1, text='✕', bg=C_SILVER, fg=C_TEXT,
                        relief='raised', bd=1, padx=2, pady=0,
                        font=('MS Sans Serif', 8),
                        command=lambda: self.var_fx_search.set(''))
        clr.pack(side='left', padx=(0, 8))
        Tooltip(ent,
                'Search effects by name OR description. Multiple words must '
                'all match (AND), like a search engine. Matching groups '
                'auto-expand; clear to restore.\n──\n'
                'Поиск эффектов по названию ИЛИ описанию. Несколько слов '
                'должны совпасть все (И), как в поисковике. Группы с '
                'совпадениями авто-раскрываются; очистите для возврата.')
        Tooltip(clr, 'Clear search\n──\nОчистить поиск')

        self.var_fx_active_only = tk.BooleanVar(value=False)
        ao = ttk.Checkbutton(row1, text='Active only',
                             variable=self.var_fx_active_only,
                             style='W95.TCheckbutton')
        ao.pack(side='left', padx=6)
        # trace (а не command=), чтобы программные изменения тоже пересчитывались.
        self.var_fx_active_only.trace_add(
            'write', lambda *_: self._recompute_block_visibility())
        Tooltip(ao,
                'Show only effects that are currently enabled — a quick '
                'overview of what is on. Does not change any setting.\n──\n'
                'Показывать только включённые сейчас эффекты — быстрый обзор '
                'того, что активно. Никакие настройки не меняются.')

        cb = ttk.Checkbutton(
            row1, text='Hide color-altering effects',
            variable=self.var_hide_color_fx,
            style='W95.TCheckbutton',
            command=self._on_toggle_hide_color_fx)
        cb.pack(side='left', padx=6)
        Tooltip(cb,
                'Hides and disables effects that significantly alter the '
                'source palette / RGB channels (Flash, RGB Shift, Color '
                'Bleed, Negative, Posterize, Glitch Cascade, Temporal RGB, '
                'FFT Phase, Tube Sat, Dtype Reinterpret, ELA, Spatial '
                "Reverb) and forces silence-treatment 'dim' to 'none'. "
                'Previous settings are restored when unchecked.\n──\n'
                'Скрывает и выключает эффекты, заметно меняющие палитру / '
                'RGB-каналы исходника. Состояния сохраняются и '
                'восстанавливаются при снятии галочки.')

        # Строка 2 - компактный выпадающий список "перейти к группе" +
        # переключатель свернуть/развернуть. Плоский ряд чипов переполнил
        # бы панель при достаточном числе групп, поэтому один menubutton
        # перечисляет все группы и не съедает ширину. Переключатель
        # сворачивания живёт здесь же, на том же уровне.
        row2 = tk.Frame(bar, bg=C_SILVER)
        row2.pack(fill='x', padx=4, pady=(0, 4))

        self.var_fx_all_collapsed = tk.BooleanVar(value=False)
        collapse_btn = tk.Button(
            row2, text='⊟ Collapse all', bg=C_SILVER, fg=C_TEXT,
            relief='raised', bd=2, padx=6, pady=0,
            font=('MS Sans Serif', 8),
            cursor='hand2')

        def _toggle_all():
            if self.var_fx_all_collapsed.get():
                self.expand_all_groups()
                self.var_fx_all_collapsed.set(False)
                collapse_btn.configure(text='⊟ Collapse all')
            else:
                self.collapse_all_groups()
                self.var_fx_all_collapsed.set(True)
                collapse_btn.configure(text='⊞ Expand all')
        collapse_btn.configure(command=_toggle_all)
        collapse_btn.pack(side='right', padx=4, pady=2)
        Tooltip(collapse_btn,
                'Collapse every group at once, then expand them all again — '
                'tames the long scroll.\n──\n'
                'Свернуть все группы сразу, затем снова развернуть — '
                'укрощает длинную простыню.')

        by_group = {}
        for spec in EFFECTS:
            by_group.setdefault(spec.group, []).append(spec)
        chip_titles = ['CUT LOGIC']
        for group_name in GROUP_ORDER:
            if group_name in ACCORDION_HIDDEN_GROUPS:
                continue
            if not by_group.get(group_name):
                continue
            chip_titles.append(GROUP_DISPLAY_NAMES.get(group_name, group_name))

        jump_mb = tk.Menubutton(
            row2, text='⤓ Jump to group ▾', bg=C_SILVER, fg=C_TEXT,
            relief='raised', bd=2, padx=6, pady=0,
            font=('MS Sans Serif', 8), cursor='hand2')
        jump_menu = tk.Menu(jump_mb, tearoff=0,
                            font=('MS Sans Serif', 8),
                            bg=C_SILVER, fg=C_TEXT,
                            activebackground=C_TITLE_BAR,
                            activeforeground=C_WHITE)
        for title in chip_titles:
            jump_menu.add_command(
                label=title,
                command=lambda t=title: self.scroll_to_group(t))
        jump_mb.configure(menu=jump_menu)
        jump_mb.pack(side='left', padx=2, pady=2)
        Tooltip(jump_mb,
                'Jump to and expand any effect group.\n──\n'
                'Перейти к любой группе эффектов и раскрыть её.')

    # ─── контракт навигации (управляется панелью) ───
    def expand_group(self, title):
        h = self._acc_groups.get(title)
        if h is not None:
            h.set_open(True)

    def collapse_group(self, title):
        h = self._acc_groups.get(title)
        if h is not None:
            h.set_open(False)

    def expand_all_groups(self):
        for h in self._acc_groups.values():
            h.set_open(True)

    def collapse_all_groups(self):
        for h in self._acc_groups.values():
            h.set_open(False)

    def scroll_to_group(self, title):
        h = self._acc_groups.get(title)
        canvas = getattr(self, '_effects_canvas', None)
        if h is None or canvas is None:
            return
        self.expand_group(title)
        # Даём геометрии обновиться, чтобы bbox ниже был точным.
        self.update_idletasks()
        bbox = canvas.bbox('all')
        if not bbox:
            return
        total = max(1, bbox[3] - bbox[1])
        y = max(0, h.frame.winfo_y() - bbox[1])
        canvas.yview_moveto(min(1.0, y / total))

    def _recompute_block_visibility(self):
        """Единый источник истины для того, какие блоки эффектов показаны.

        Каждый механизм скрытия сводится к этому одному решению вместо
        того, чтобы каждый сам вызывал pack_forget/repack:

            visible = not hidden_by_color_fx and not hidden_by_passthrough
                      and matches_search and (not active_only or enabled)

        Семантика (принудительное выключение переменных, снимок/восстановление)
        по-прежнему живёт в обработчиках color-fx / passthrough; этот метод
        владеет только *отображением*. Так как он никогда не пишет cfg-переменные,
        пресеты остаются побайтово идентичными независимо от того, что отфильтровано.
        """
        if not hasattr(self, '_effect_block_frames'):
            return
        query = (self.var_fx_search.get().strip().lower()
                 if hasattr(self, 'var_fx_search') else '')
        words = query.split()
        active_only = bool(self.var_fx_active_only.get()) if hasattr(
            self, 'var_fx_active_only') else False
        color_active = bool(self.var_hide_color_fx.get()) if hasattr(
            self, 'var_hide_color_fx') else False
        pass_var = self.vars.get('passthrough_mode')
        pass_active = bool(pass_var.get()) if pass_var is not None else False
        filtering = bool(words) or active_only

        groups_with_visible: set = set()
        for key, blk in self._effect_block_frames.items():
            hidden_color = color_active and key in COLOR_EFFECT_KEYS
            hidden_pass = pass_active and key in PASSTHROUGH_HIDDEN_KEYS
            haystack = self._search_index.get(key, '')
            matches = all(w in haystack for w in words)
            enabled = bool(self.vars[key].get()) if key in self.vars else False
            visible = (not hidden_color and not hidden_pass and matches
                       and (not active_only or enabled))
            # Сравниваем с отслеживаемым желаемым состоянием, а НЕ с
            # winfo_ismapped - блок в свёрнутой группе не отображён, но
            # всё ещё видим для фильтра.
            prev = self._block_visible.get(key, True)
            if visible and not prev:
                order = getattr(blk.master, '_sb_initial_order', None)
                if order is not None:
                    self._repack_in_order(blk, order, fill='x')
                else:
                    blk.pack(fill='x')
            elif not visible and prev:
                blk.pack_forget()
            self._block_visible[key] = visible
            if visible:
                groups_with_visible.add(self._effect_block_group.get(key))

        # Пока идёт фильтрация, состояние открытости групп следует за
        # совпадениями, а ручное состояние пользователя сохраняется, чтобы
        # восстановиться при очистке. Это тот же приём снимок/восстановление,
        # что уже используют переключатели скрытия.
        if filtering:
            if self._fx_filter_group_snapshot is None:
                self._fx_filter_group_snapshot = {
                    t: h.is_open() for t, h in self._acc_groups.items()}
            for title, h in self._acc_groups.items():
                # В CUT LOGIC нет блоков эффектов - оставляем как задал пользователь.
                if title == 'CUT LOGIC':
                    continue
                h.set_open(title in groups_with_visible, refresh=False)
        elif self._fx_filter_group_snapshot is not None:
            for title, was_open in self._fx_filter_group_snapshot.items():
                h = self._acc_groups.get(title)
                if h is not None:
                    h.set_open(was_open, refresh=False)
            self._fx_filter_group_snapshot = None

        refresh = getattr(self, '_effects_refresh_scroll', None)
        if refresh is not None:
            self.after_idle(refresh)

    def _acc_group(self, parent, title, open=False, bg_color=None, fg_color=None):
        g = tk.Frame(parent, bg=C_SILVER, bd=1, relief='solid')
        g.pack(fill='x', padx=4, pady=2)
        default_bg = bg_color if bg_color else (C_TITLE_BAR if open else C_SILVER)
        default_fg = fg_color if fg_color else (C_WHITE if open else C_TEXT)

        hdr = tk.Frame(g, bg=default_bg, cursor='hand2')
        hdr.pack(fill='x')
        arrow = tk.StringVar(value='▼' if open else '▶')
        ar_l = tk.Label(hdr, textvariable=arrow,
                        bg=hdr['bg'], fg=default_fg,
                        font=('MS Sans Serif', 9))
        ar_l.pack(side='left', padx=4)
        t_l = tk.Label(hdr, text=title, bg=hdr['bg'],
                       fg=default_fg,
                       font=('MS Sans Serif', 10, 'bold'))
        t_l.pack(side='left', pady=4)
        body = tk.Frame(g, bg=C_WHITE, bd=1, relief='sunken')
        if open:
            body.pack(fill='x')

        # Состояние открытости отслеживается явно, а НЕ читается обратно из
        # winfo_ismapped - последний устаревает между pack()/pack_forget() и
        # обработкой цикла событий, из-за чего быстрые программные
        # переключения (восстановление фильтра → collapse_all) сбоят.
        state = {'open': bool(open)}

        # `set_open` - единственный примитив, которым управляют и ручное
        # переключение пользователем, и контракт навигации
        # (expand_group / collapse_all / scroll_to_group) - так программное
        # и ручное раскрытие остаются побайтово идентичными.
        def _set_open(open_, *, refresh=True):
            open_ = bool(open_)
            if open_ == state['open']:
                return
            state['open'] = open_
            if open_:
                body.pack(fill='x')
                active_bg = bg_color if bg_color else C_TITLE_BAR
                active_fg = fg_color if fg_color else C_WHITE
                hdr.configure(bg=active_bg)
                ar_l.configure(bg=active_bg, fg=active_fg)
                t_l.configure(bg=active_bg, fg=active_fg)
                arrow.set('▼')
            else:
                body.pack_forget()
                hdr.configure(bg=C_SILVER)
                ar_l.configure(bg=C_SILVER, fg=C_TEXT)
                t_l.configure(bg=C_SILVER, fg=C_TEXT)
                arrow.set('▶')
            if refresh:
                _refresh = getattr(self, '_effects_refresh_scroll', None)
                if _refresh is not None:
                    self.after_idle(_refresh)

        def _toggle(_e=None):
            _set_open(not state['open'])
        for w in (hdr, ar_l, t_l):
            w.bind('<Button-1>', _toggle)

        handle = SimpleNamespace(
            name=title, frame=g, hdr=hdr, body=body,
            arrow=arrow, arrow_label=ar_l, title_label=t_l,
            set_open=_set_open,
            is_open=lambda: state['open'])
        self._acc_groups[title] = handle
        return body

    # ─── переключатель скрытия color-fx ───
    def _on_toggle_passthrough(self):
        """Обработчик чекбокса - скрывает/восстанавливает блоки вставляющих кадры эффектов."""
        active = bool(self.vars['passthrough_mode'].get())
        self._apply_passthrough_hide(active=active, take_snapshot=True)

    def _apply_passthrough_hide(self, *, active: bool, take_snapshot: bool):
        """Скрывает и принудительно выключает вставляющие кадры эффекты при включённом passthrough.

        Повторяет `_apply_hide_color_fx`: снимок прежних состояний при
        включении, восстановление при выключении. Снимок хранится в
        `_passthrough_snapshot`, чтобы при выключении галочки пользователь
        получал обратно свой исходный набор эффектов, а не "всё выключено".
        """
        if active:
            if take_snapshot:
                snap = {}
                # Некоторые ключи (например fx_flash) состоят и в PASSTHROUGH_HIDDEN_KEYS,
                # и в COLOR_EFFECT_KEYS. Если hide-color-fx сейчас включён,
                # живая переменная равна False (принудительно выключена этим режимом) - но
                # ИСТИННЫЙ выбор пользователя хранится в `_color_fx_snapshot`.
                # Снимок нужно брать оттуда, иначе восстановление позже
                # запишет устаревший False обратно в пресет пользователя.
                color_snap = self._color_fx_snapshot or {}
                for key in PASSTHROUGH_HIDDEN_KEYS:
                    if key in self.vars:
                        if key in color_snap:
                            snap[key] = bool(color_snap[key])
                        else:
                            try:
                                snap[key] = bool(self.vars[key].get())
                            except Exception:
                                snap[key] = False
                self._passthrough_snapshot = snap
            # Принудительно выключаем ключи; видимость блоков следует через
            # _recompute_block_visibility (единый источник истины).
            for key in PASSTHROUGH_HIDDEN_KEYS:
                if key in self.vars:
                    try:
                        self.vars[key].set(False)
                    except Exception:
                        pass
            # Виджеты Cut Logic, не имеющие смысла в passthrough.
            for w in getattr(self, '_passthrough_cut_widgets', []):
                if w.winfo_ismapped():
                    w.pack_forget()
            # Отключаем выбор аудиофайла - passthrough использует
            # собственную звуковую дорожку исходного видео. Пользователь
            # всё ещё видит прежний выбор (подпись сохраняет состояние),
            # просто не может его изменить.
            btn = getattr(self, '_audio_btn', None)
            if btn is not None:
                btn.state(['disabled'])
        else:
            snap = self._passthrough_snapshot or {}
            color_active = self.var_hide_color_fx.get()
            for key in PASSTHROUGH_HIDDEN_KEYS:
                if key in self.vars and key in snap:
                    if color_active and key in COLOR_EFFECT_KEYS:
                        # для этого ключа hide-color-fx всё ещё действует - если
                        # записать `snap[key]=True` в живую переменную, color-fx
                        # при следующей синхронизации принудительно вернёт её в
                        # False, и выбор пользователя незаметно потеряется при
                        # последующем выключении hide-color-fx. Вместо этого
                        # пишем в снимок color-fx, чтобы значение дожило
                        # до выхода из этого режима.
                        self._color_fx_snapshot[key] = snap[key]
                    else:
                        try:
                            self.vars[key].set(snap[key])
                        except Exception:
                            pass
            # Возвращаем cut-only виджеты на их ИСХОДНЫЕ места, а не в конец.
            # Обычный `pack(fill='x')` раньше сваливал их ниже всех
            # элементов Cut Logic, потому что tk.pack добавляет в конец.
            for w in getattr(self, '_passthrough_cut_widgets', []):
                if not w.winfo_ismapped():
                    order = getattr(w.master, '_sb_initial_order', None)
                    if order is not None:
                        self._repack_in_order(w, order, fill='x')
                    else:
                        w.pack(fill='x')
            # Снова включаем выбор аудио.
            btn = getattr(self, '_audio_btn', None)
            if btn is not None:
                btn.state(['!disabled'])
            self._passthrough_snapshot = {}

        self._recompute_block_visibility()

    def _on_toggle_hide_color_fx(self):
        """Обработчик чекбокса. Снимает состояния + применяет, либо восстанавливает."""
        active = self.var_hide_color_fx.get()
        self._apply_hide_color_fx(active=active, take_snapshot=True)

    def _apply_hide_color_fx(self, *, active: bool, take_snapshot: bool):
        """Скрывает+выключает все меняющие цвет эффекты, либо восстанавливает их.

        Владеет только *семантикой*: снимок/восстановление состояния
        включённости и silence_mode, принудительное выключение цветовых
        ключей. Видимость блоков делегирована `_recompute_block_visibility`,
        единому источнику истины - этот метод больше сам ничего не
        packs/unpacks.
        """
        if active:
            if take_snapshot:
                snap = {}
                for key in COLOR_EFFECT_KEYS:
                    if key in self.vars:
                        try:
                            snap[key] = bool(self.vars[key].get())
                        except Exception:
                            snap[key] = False
                snap['__silence_mode__'] = self.var_silence_mode.get()
                if 'fx_ascii_color_mode' in self.vars:
                    snap['__ascii_color_mode__'] = \
                        self.vars['fx_ascii_color_mode'].get()
                self._color_fx_snapshot = snap

            # Выключаем (видимость следует через _recompute_block_visibility).
            for key in COLOR_EFFECT_KEYS:
                if key in self.vars:
                    try:
                        self.vars[key].set(False)
                    except Exception:
                        pass
            # Принудительно выключаем silence_mode 'dim' (остальные режимы сохраняются)
            if self.var_silence_mode.get() == 'dim':
                self.var_silence_mode.set('none')
            self._sync_silence_radio_visibility()
            # Режимы ASCII 'fixed'/'inverted' перекрашивают вывод, поэтому
            # блокируем выпадающий список на 'original', пока включён color-fx hide.
            self._lock_ascii_color_mode(True)
        else:
            # Восстанавливаем
            snap = self._color_fx_snapshot or {}
            for key in COLOR_EFFECT_KEYS:
                if key in self.vars and key in snap:
                    try:
                        self.vars[key].set(snap[key])
                    except Exception:
                        pass
            prev_silence = snap.get('__silence_mode__')
            if prev_silence:
                self.var_silence_mode.set(prev_silence)
            # Разблокируем выпадающий список ASCII-цвета и восстанавливаем выбор пользователя.
            self._lock_ascii_color_mode(False)
            prev_ascii = snap.get('__ascii_color_mode__')
            if prev_ascii and 'fx_ascii_color_mode' in self.vars:
                self.vars['fx_ascii_color_mode'].set(prev_ascii)
            self._color_fx_snapshot = {}
            self._sync_silence_radio_visibility()

        self._recompute_block_visibility()

    def _lock_ascii_color_mode(self, locked: bool):
        """Ограничивает выпадающий список ASCII цвет-режима до 'original'
        (locked) или восстанавливает полный список. 'fixed' и 'inverted'
        перекрашивают кадр, поэтому они недоступны, пока включён
        Hide color-altering. Чистая UI-блокировка - при locked принудительно
        ставит 'original'; предыдущее значение снимается/восстанавливается
        вызывающим кодом.
        """
        combo = getattr(self, '_combos', {}).get('fx_ascii_color_mode')
        if combo is None:
            return
        try:
            if locked:
                if self.vars['fx_ascii_color_mode'].get() != 'original':
                    self.vars['fx_ascii_color_mode'].set('original')
                combo.configure(values=['original'])
            else:
                combo.configure(values=getattr(
                    combo, '_sb_full_values', ['fixed', 'original', 'inverted']))
        except tk.TclError:
            pass

    # ─── синхронизация пресета качества ↔ ручных полей ───
    def _on_quality_preset_changed(self):
        """Выпадающий список качества изменился → записывает (crf, preset, tune)
        в ручные поля. 'Custom' - это маркер, он ничего не меняет."""
        name = self.var_quality_preset.get()
        spec = QUALITY_PRESETS.get(name)
        if spec is None:  # Custom или неизвестное значение
            return
        self._applying_quality = True
        try:
            self.vars['crf'].set(int(spec['crf']))
            if hasattr(self, 'preset_enc_combo'):
                self.preset_enc_combo.set(spec['export_preset'])
            self.var_tune.set(spec['tune'])
        finally:
            self._applying_quality = False

    def _refresh_quality_label(self):
        """Ручное поле изменилось → переключает выпадающий список качества на
        подходящий пресет (если есть), иначе 'Custom'. Пропускается, пока
        идёт применение пресета, чтобы избежать пинг-понга трейсов."""
        if getattr(self, '_applying_quality', False):
            return
        if not hasattr(self, 'quality_combo'):
            return
        try:
            crf = int(round(float(self.vars['crf'].get())))
        except (tk.TclError, TypeError, ValueError):
            return
        preset = (self.preset_enc_combo.get()
                  if hasattr(self, 'preset_enc_combo') else 'medium')
        tune = self.var_tune.get() or 'none'
        label = detect_quality(crf=crf, export_preset=preset, tune=tune)
        if self.var_quality_preset.get() != label:
            self.var_quality_preset.set(label)

    def _sync_silence_radio_visibility(self):
        """Скрывает радиокнопку 'Dim', пока активно скрытие color-fx."""
        radios = getattr(self, '_silence_radios', None)
        if not radios:
            return
        hide_dim = self.var_hide_color_fx.get()
        for val, btn in radios.items():
            if val == 'dim' and hide_dim:
                if btn.winfo_ismapped():
                    btn.pack_forget()
            else:
                if not btn.winfo_ismapped():
                    btn.pack(side='left', padx=4)

    def _build_cut_logic(self, body):
        # Виджеты, теряющие смысл в режиме passthrough (нет случайного
        # семплинга → нет буфера сцен, нет нарезки → нет фильтра min-cut)
        # собираются здесь, чтобы `_apply_passthrough_hide` мог делать
        # pack_forget / pack над ними как над группой - так же, как
        # color-fx hide обрабатывает свой набор блоков эффектов.
        self._passthrough_cut_widgets: list = []

        scene_hdr = self._row_with_help(body, 'Smart Scene Detection', bi(
            'Detects scene changes in the source video and prefers to start segments at those '
            'cuts. Off = uniform random sampling.',
            'Находит смены сцен в исходном видео и предпочитает стартовать сегменты с этих '
            'точек. Выкл — равномерная случайная выборка.'))
        scene_cb_wrap = tk.Frame(body, bg=self._parent_bg(body))
        scene_cb_wrap.pack(fill='x')
        ttk.Checkbutton(scene_cb_wrap, text='Detect scene cuts',
                        variable=self.vars['use_scene_detect'],
                        style='W95.TCheckbutton').pack(anchor='w', padx=24, pady=(0, 4))
        self._passthrough_cut_widgets.extend([scene_hdr, scene_cb_wrap])

        # Всегда видимые слайдеры. Те, что важны только вне passthrough,
        # собираются отдельно ниже, чтобы их можно было скрыть группой.
        sliders_always = [
            ('Global Chaos Level', 'chaos_level', 0.0, 1.0, bi(
                'Master dial. Scales every effect chance by 0.3 + 0.7·CHAOS plus stutter/flash '
                'probability. 0 = polite, 1 = unhinged.',
                'Главная ручка. Масштабирует шанс каждого эффекта по 0.3 + 0.7·CHAOS и '
                'вероятность stutter/flash. 0 — спокойно, 1 — без тормозов.')),
            ('Beat Threshold', 'threshold', 0.5, 2.0, bi(
                'How loud (×rms_mean) a segment must be to count as "loud". Lower = more '
                'impacts trigger; higher = only the punchiest beats.',
                'Насколько громким (×rms_mean) должен быть сегмент, чтобы считаться громким. '
                'Ниже — больше импактов; выше — только самые сильные удары.')),
            ('Transient Sensitivity', 'transient_thresh', 0.1, 1.5, bi(
                'How sharp an attack must be to count as IMPACT. Lower = more frequent flashes.',
                'Насколько резкой должна быть атака, чтобы попасть в IMPACT. Ниже — чаще '
                'срабатывают вспышки.')),
        ]
        for lbl, key, lo, hi, tt in sliders_always:
            self._row_with_help(body, lbl, tt)
            self._slider(body, key, lo, hi)

        # Слайдеры только для нарезки: не имеют значения в passthrough.
        sliders_cut_only = [
            ('Min Cut Duration (sec)', 'min_cut_duration', 0.0, 0.3, bi(
                'Drops segments shorter than this. Higher = calmer pacing.',
                'Отбрасывает сегменты короче этого значения. Больше — спокойнее темп.')),
            ('Scene Buffer Size', 'scene_buffer_size', 2, 30, bi(
                'How many detected scene cuts to keep around as candidates.',
                'Сколько найденных точек смены сцен держать в пуле кандидатов.')),
        ]
        cut_only_widgets: dict = {}
        for lbl, key, lo, hi, tt in sliders_cut_only:
            row = self._row_with_help(body, lbl, tt)
            sf = self._slider(body, key, lo, hi)
            self._passthrough_cut_widgets.extend([row, sf])
            cut_only_widgets[key] = (row, sf)

        # Scene Buffer Size важен только при включённой детекции смен сцен -
        # иначе делаем серым, чтобы пользователь видел, что слайдер спит.
        sb_pair = cut_only_widgets.get('scene_buffer_size')
        if sb_pair is not None:
            self._bind_dep(list(sb_pair),
                           lambda: bool(self.vars['use_scene_detect'].get()),
                           [self.vars['use_scene_detect']])

        snap_hdr = self._row_with_help(body, 'Snap Cuts to Beat Grid', bi(
            'After onset detection, pull each onset to the nearest beat within tolerance. '
            'Improves rhythmic precision; required for tight drillcore sync.',
            'После детекции онсетов притягивает каждый онсет к ближайшему биту в пределах '
            'tolerance. Улучшает ритмическую точность; обязательно для плотного drillcore.'))
        snap_cb = ttk.Checkbutton(body, text='Snap onsets to beat grid',
                        variable=self.vars['snap_to_beat'],
                        style='W95.TCheckbutton')
        snap_cb.pack(anchor='w', padx=24, pady=(0, 2))
        tol_row = self._row_with_help(body, 'Beat Snap Tolerance (sec)', bi(
            'Maximum onset→beat distance for snapping. Larger = more onsets pulled to grid but '
            'at the cost of micro-rhythm.',
            'Максимальное расстояние онсет→бит для снэпа. Больше — больше онсетов прилипает к '
            'сетке, но теряется микро-ритмика.'))
        tol_slider = self._slider(body, 'snap_tolerance', 0.01, 0.15, indent=True)

        # Ручной override BPM - строит равномерную сетку битов из введённого
        # пользователем темпа, когда snap-to-beat включён. Полностью
        # обходит оценщик темпа librosa; полезно для треков со слабыми
        # онсетами или когда точный целевой BPM уже известен.
        bpm_hdr = self._row_with_help(body, 'Manual BPM Override', bi(
            'Bypass automatic tempo detection and use a hand-typed BPM for the snap-to-beat grid. '
            'Requires Snap onsets to beat grid above to be ON.',
            'Подменяет автодетекцию темпа на введённый вручную BPM при снэпе к биту. '
            'Чтобы это сработало, выше должно быть включено «Snap onsets to beat grid».'))
        bpm_row = tk.Frame(body, bg=self._parent_bg(body))
        bpm_row.pack(fill='x', padx=24, pady=(0, 4))
        bpm_cb = ttk.Checkbutton(bpm_row, text='Use manual BPM:',
                        variable=self.vars['use_manual_bpm'],
                        style='W95.TCheckbutton')
        bpm_cb.pack(side='left')
        bpm_entry = ttk.Entry(bpm_row, width=7,
                              textvariable=self._display_vars.get('manual_bpm'))
        bpm_entry.pack(side='left', padx=6)

        # Каскад снэпа. Beat-snap-tolerance и вся подгруппа ручного BPM
        # (заголовок + чекбокс + числовое поле) важны только при
        # включённом snap-to-beat. Внутри этой подгруппы поле BPM имеет
        # дополнительную зависимость от `use_manual_bpm` - остаётся
        # выключенным даже при включённом snap, если пользователь не
        # отметил "Use manual BPM:". Каскад закодирован прямо в
        # предикате (И переменных).
        snap_var = self.vars['snap_to_beat']
        manual_var = self.vars['use_manual_bpm']
        self._bind_dep([tol_row, tol_slider, bpm_hdr, bpm_cb],
                       lambda: bool(snap_var.get()),
                       [snap_var])
        self._bind_dep([bpm_entry],
                       lambda: bool(snap_var.get()) and bool(manual_var.get()),
                       [snap_var, manual_var])

        def _commit_manual_bpm(*_):
            try:
                v = float(self._display_vars['manual_bpm'].get())
                v = max(20.0, min(400.0, v))
                self.vars['manual_bpm'].set(v)
                self._display_vars['manual_bpm'].set(f'{v:.1f}')
            except (ValueError, KeyError):
                # Возвращаемся к текущему значению переменной, если пользователь ввёл мусор.
                self._display_vars['manual_bpm'].set(
                    f"{self.vars['manual_bpm'].get():.1f}")
        bpm_entry.bind('<FocusOut>', _commit_manual_bpm)
        bpm_entry.bind('<Return>', _commit_manual_bpm)

        # Режим Passthrough - читает кадры последовательно из одного
        # исходного видео, анализирует ЕГО СОБСТВЕННУЮ звуковую дорожку,
        # без нарезки/семплинга/рандома. Все эффекты сохраняют здесь
        # соответствие кадров 1:1 вход→выход: Stutter и Flash переключаются
        # в режим замены, Optical Flow и True Datamosh - обычные эффекты
        # цепочки один-в-один.
        self._row_with_help(body, 'Passthrough Mode', bi(
            "Process the source video 1:1 — no cuts, no resampling, native frame order. "
            "Audio is taken from the video's own track and used both for analysis (effect "
            "triggers) and for the output. No external audio file is needed. Stutter / "
            "Flash / Optical Flow / True Datamosh ALL work here too: frame-inserting "
            "effects switch to replace-mode (overwrite frames in place instead of "
            "inserting new ones) so audio stays in sync.",
            "Прогон исходного видео 1:1 — без нарезки, без рандомного семплинга, в "
            "нативном порядке кадров. Аудио берётся из самого видео и используется и для "
            "анализа (триггеры эффектов), и в выводе. Внешний аудиофайл не нужен. "
            "Stutter / Flash / Optical Flow / True Datamosh тоже работают: вставляющие "
            "кадры эффекты переключаются в режим замены кадров (вместо вставки новых) — "
            "аудио остаётся в синхроне."))
        ttk.Checkbutton(body, text='Passthrough mode (use source video as-is)',
                        variable=self.vars['passthrough_mode'],
                        command=self._on_toggle_passthrough,
                        style='W95.TCheckbutton').pack(anchor='w', padx=24, pady=(0, 4))

        # Тишина
        self._row_with_help(body, 'Silence Treatment', bi(
            'How long (>1s) silent stretches are rendered: dim, soft blur, both, or untouched. '
            'Default: none.',
            'Как обрабатывать длинные (>1 с) тихие участки: затемнение, размытие, оба варианта '
            'или без обработки. По умолчанию: none.'))
        sf = tk.Frame(body, bg=self._parent_bg(body))
        sf.pack(fill='x', padx=20, pady=(2, 6))
        # Порядок: None первым, чтобы визуально читалось как значение по умолчанию.
        self._silence_radios = {}
        for val, lbl in [('none', 'None'), ('dim', 'Dim'),
                         ('blur', 'Blur'), ('both', 'Both')]:
            rb = tk.Radiobutton(sf, text=lbl, variable=self.var_silence_mode,
                                value=val, bg=sf.cget('bg'), fg=C_TEXT,
                                selectcolor=C_WHITE,
                                font=('MS Sans Serif', 9))
            rb.pack(side='left', padx=4)
            self._silence_radios[val] = rb
        self._sync_silence_radio_visibility()

        # Снимок порядка детей body + опций pack, чтобы переключатель
        # passthrough мог возвращать cut-only виджеты на исходные места
        # (с исходным отступом), а не сваливать их прижатыми влево внизу.
        self._snapshot_pack_order(body)

    def _build_effect_block(self, parent, spec):
        """Строит GUI-блок для одного EffectSpec.

        Внутри возвращаемого фрейма сотрудничают три уровня видимости:
          • header (чекбокс + подпись + переключатель `always`) виден всегда,
            пока сам блок отображён - это единственная ручка пользователя,
            чтобы снова включить эффект;
          • `inner` (шанс / параметры / интенсивность always-on) СКРЫТ,
            пока enable_key эффекта выключен - панель остаётся опрятной
            и не показывает "мёртвые" слайдеры, на которые пользователь
            не может повлиять;
          • `ai_holder` (интенсивность always-on) скрыт, пока `always` не
            включён, и живёт ВНУТРИ `inner` между строкой шанса и
            параметрами (sentinel фиксирует слот) - исправляет прежний
            баг, когда он появлялся ниже разделителя блока.

        Тонкий разделитель остаётся прямым потомком `block` (НЕ `inner`),
        поэтому продолжает отмечать границу между блоками, даже когда
        эффект выключен и `inner` скрыт.

        Возвращает внешний фрейм, чтобы вызывающие (hide-color-fx,
        passthrough) могли делать pack_forget/pack над ВСЕМ блоком целиком.
        """
        block = tk.Frame(parent, bg=C_WHITE)
        block.pack(fill='x')

        # Строка заголовка: чекбокс + подпись + подсказка + (опционально) переключатель always.
        hr = tk.Frame(block, bg=C_WHITE)
        hr.pack(fill='x', padx=4, pady=(4, 0))
        cb = ttk.Checkbutton(hr, text=spec.label, variable=self.vars[spec.enable_key],
                             style='W95.TCheckbutton')
        cb.pack(side='left', padx=6)
        if spec.tooltip:
            help_lbl = tk.Label(hr, text='[?]', bg=C_WHITE, fg='#3060A0',
                                cursor='question_arrow',
                                font=('MS Sans Serif', 8, 'bold'))
            help_lbl.pack(side='left', padx=(2, 0))
            Tooltip(cb, spec.tooltip); Tooltip(help_lbl, spec.tooltip)

        if spec.supports_always_for_chain():
            ao_tooltip = (
                'When ON, this effect ignores its segment-type triggers and chance slider — '
                'it fires on EVERY frame at the fixed intensity below. Other effects keep '
                'their normal audio-driven behaviour.\n──\n'
                'Когда включено — эффект игнорирует свои триггеры по типу сегмента и слайдер '
                'шанса: он будет применяться на КАЖДОМ кадре с фиксированной интенсивностью. '
                'Остальные эффекты продолжают работать в обычном аудио-реактивном режиме.'
            )
            ao_cb = ttk.Checkbutton(hr, text='always',
                                    variable=self.vars[spec.always_key],
                                    style='W95.TCheckbutton')
            ao_cb.pack(side='right', padx=8)
            Tooltip(ao_cb, ao_tooltip)

        if spec.note:
            tk.Label(block, text=spec.note, bg=C_WHITE, fg=C_DARK_GRAY,
                     font=('MS Sans Serif', 7, 'italic')).pack(
                anchor='w', padx=22, pady=(0, 2))

        # `inner` несёт все ручки настройки эффекта и целиком скрывается,
        # когда чекбокс включения эффекта выключен.
        inner = tk.Frame(block, bg=C_WHITE)

        chance_widgets: list = []
        if spec.chance_key is not None:
            ch_row = self._row_with_help(inner, 'Chance', bi(
                'Probability the effect fires per qualifying frame. Scaled by Global Chaos.',
                'Вероятность срабатывания эффекта на подходящем кадре. Масштабируется ползунком '
                'Global Chaos.'))
            ch_slider = self._slider(inner, spec.chance_key, 0.0, 1.0, indent=True)
            chance_widgets = [ch_row, ch_slider]

        # Интенсивность always-on. Строится ВНУТРИ inner, поэтому её
        # визуальное место оказывается между chance и params. Прикреплённый
        # сразу после неё sentinel нулевой высоты служит якорем `before=`,
        # когда holder позже переключается на видимый - без него повторный
        # pack приземлился бы ниже params.
        ai_holder = None
        params_anchor = None
        if spec.supports_always_for_chain():
            ai_holder = tk.Frame(inner, bg=C_WHITE)
            self._row_with_help(ai_holder, 'Always-on intensity', bi(
                'Fixed intensity used while "always" is ON. Has no effect otherwise.',
                'Фиксированная интенсивность, когда чекбокс «always» включён. В обычном режиме '
                'не используется.'))
            self._slider(ai_holder, spec.always_int_key, 0.0, 1.0, indent=True)
            ai_holder.pack(fill='x')
            params_anchor = tk.Frame(inner, bg=C_WHITE, height=0)
            params_anchor.pack(fill='x')

        # Элементы управления параметрами конкретного эффекта.
        for p in spec.params:
            if p.key in ('fx_paint_canvas_data', 'fx_paint_color_r', 'fx_paint_color_g', 'fx_paint_color_b'):
                continue
            if p.kind == 'choice':
                self._row_with_help(inner, p.label, p.tooltip)
                self._combo(inner, p.key, p.choices, indent=True)
            elif p.kind == 'string':
                self._row_with_help(inner, p.label, p.tooltip)
                row = tk.Frame(inner, bg=C_WHITE)
                row.pack(fill='x', padx=20, pady=2)
                ent = ttk.Entry(row, textvariable=self.vars[p.key])
                ent.pack(fill='x')
            elif p.kind == 'file':
                self._build_file_picker(inner, p)
            else:
                self._row_with_help(inner, p.label, p.tooltip)
                self._slider(inner, p.key, p.lo, p.hi, indent=True)

        # Чекбокс привязки к аудио (только для эффектов, у которых
        # зарегистрирован парный аудио-дефект в
        # vpc.audio.pipeline.EFFECT_AUDIO_COUPLING). Живёт внизу `inner`,
        # чтобы наследовать авто-скрытие при выключенном эффекте;
        # видимость внутри `inner` дополнительно зависит от passthrough_mode
        # через `_sync_audio_link` ниже.
        from vpc.audio.pipeline import EFFECT_AUDIO_COUPLING
        audio_link_holder = None
        coupling_entry = EFFECT_AUDIO_COUPLING.get(spec.enable_key)
        if coupling_entry is not None:
            link_label, _link_fn, link_tip = coupling_entry
            audio_link_var_key = 'audio_link_' + spec.enable_key
            audio_link_holder = tk.Frame(inner, bg=C_WHITE)
            ttk.Checkbutton(
                audio_link_holder, text=link_label,
                variable=self.vars[audio_link_var_key],
                style='W95.TCheckbutton',
            ).pack(side='left', padx=22, pady=(2, 0))
            for child in audio_link_holder.winfo_children():
                Tooltip(child, link_tip)

        # Нижний разделитель - лежит в `block`, а не в `inner`, поэтому
        # остаётся видимым при выключенном эффекте и продолжает отмечать границы.
        sep = tk.Frame(block, bg=C_DARK_GRAY, height=1)
        sep.pack(fill='x', padx=4)

        # Управление видимостью.
        # `pack_info()` (возвращает dict; пустой, если виджет не управляется
        # `pack`) - АВТОРИТЕТНАЯ проверка здесь, а НЕ `winfo_ismapped()`.
        # Последняя отражает реальную видимость на экране - которая
        # требует, чтобы все предки тоже были отображены. При сборке
        # `_sync_always` выполняется ДО того, как `_sync_inner` упаковывает
        # фрейм `inner`, поэтому `ai_holder.winfo_ismapped()` равен False,
        # даже если `ai_holder.pack()` только что был вызван: это оставляло
        # pack_forget в ветке else недостигнутым, и мгновением позже, когда
        # `_sync_inner` отображал `inner`, всё ещё управляемый `ai_holder`
        # всплывал наружу - видимый слайдер always-int несмотря на выключенный `always`.
        enable_var = self.vars[spec.enable_key]

        def _refresh_scroll():
            fn = getattr(self, '_effects_refresh_scroll', None)
            if fn is not None:
                self.after_idle(fn)

        def _is_packed(w):
            try:
                return bool(w.pack_info())
            except tk.TclError:
                return False

        def _sync_inner(*_a):
            if enable_var.get():
                if not _is_packed(inner):
                    inner.pack(fill='x', before=sep)
            else:
                if _is_packed(inner):
                    inner.pack_forget()
            _refresh_scroll()
            # Держим отображение "Active only" актуальным при переключении эффектов.
            ao = getattr(self, 'var_fx_active_only', None)
            if ao is not None and ao.get():
                self._recompute_block_visibility()

        enable_var.trace_add('write', _sync_inner)

        if ai_holder is not None:
            always_var = self.vars[spec.always_key]

            def _sync_always(*_a):
                if always_var.get():
                    if not _is_packed(ai_holder):
                        # `before=params_anchor` держит ai_holder между
                        # chance и params вместо того, чтобы прыгать
                        # в конец `inner` при каждом переключении.
                        if (params_anchor is not None
                                and _is_packed(params_anchor)):
                            ai_holder.pack(fill='x', before=params_anchor)
                        else:
                            ai_holder.pack(fill='x')
                else:
                    if _is_packed(ai_holder):
                        ai_holder.pack_forget()
                _refresh_scroll()

            always_var.trace_add('write', _sync_always)
            # Chance не имеет смысла при `always` - делаем серым, чтобы
            # пользователь видел, что он зафиксирован. Когда `inner` скрыт
            # (эффект выключен), серое состояние всё равно не видно, и это нормально.
            if chance_widgets:
                self._bind_dep(chance_widgets,
                               lambda av=always_var: not av.get(),
                               [always_var])
            _sync_always()

        if audio_link_holder is not None:
            passthrough_var = self.vars.get('passthrough_mode')

            def _sync_audio_link(*_a, holder=audio_link_holder,
                                 pt_var=passthrough_var):
                # Видим тогда и только тогда, когда включён режим passthrough.
                # Авто-скрытие при выключенном эффекте обеспечивает сворачивание
                # `inner` - здесь не нужно повторно проверять `enable_var`.
                want = bool(pt_var.get()) if pt_var is not None else False
                if want:
                    if not _is_packed(holder):
                        holder.pack(fill='x')
                else:
                    if _is_packed(holder):
                        holder.pack_forget()
                _refresh_scroll()

            if passthrough_var is not None:
                passthrough_var.trace_add('write', _sync_audio_link)
            self._audio_link_holders.append(
                (spec.enable_key, audio_link_holder))
            _sync_audio_link()

        if spec.id == 'paint':
            btn = ttk.Button(inner, text='Open Paint Editor / Открыть холст', style='W95.TButton',
                             command=self._open_paint_window)
            btn.pack(fill='x', padx=20, pady=4)

        _sync_inner()
        return block

    def _open_paint_window(self):
        old_win = getattr(self, '_paint_win', None)
        if old_win and old_win.winfo_exists():
            old_win.lift()
            old_win.focus_set()
            return

        win = tk.Toplevel(self)
        win.title("Paint Canvas Editor / Редактор холста")
        win.resizable(True, True)
        win.transient(self)
        win.grab_set()
        win.configure(bg=C_SILVER)
        self._paint_win = win

        # Размер холста подбирается под соотношение сторон источника видео
        ratio = self._get_source_aspect_ratio()
        if ratio >= 1.0:
            canvas_w = 640
            canvas_h = int(640 / ratio)
        else:
            canvas_h = 360
            canvas_w = int(360 * ratio)
            
        win.canvas_w = canvas_w
        win.canvas_h = canvas_h

        # Минимальная высота задана так, чтобы панель инструментов не обрезалась
        win.geometry(f"{canvas_w + 180}x{max(canvas_h + 30, 480)}")
        win.minsize(400, 450)

        main_frame = tk.Frame(win, bg=C_SILVER, padx=5, pady=5)
        main_frame.pack(fill='both', expand=True)

        # Боковая панель инструментов
        toolbar = tk.Frame(main_frame, bg=C_SILVER, bd=2, relief='raised', padx=4, pady=4)
        toolbar.pack(side='left', fill='y', padx=(0, 5))

        drawing_frame = tk.Frame(main_frame, bg=C_DARK_GRAY, bd=2, relief='sunken')
        drawing_frame.pack(side='left', fill='both', expand=True)

        canvas = tk.Canvas(drawing_frame, width=canvas_w, height=canvas_h, bg=C_WHITE, highlightthickness=0)
        canvas.pack(padx=10, pady=10, expand=True)
        win.canvas = canvas

        raw_data = self.vars['fx_paint_canvas_data'].get()
        if raw_data:
            from vpc.effects.paint import decode_paint_canvas
            mask = decode_paint_canvas(raw_data)
            if mask is not None:
                if mask.shape[1] != canvas_w or mask.shape[0] != canvas_h:
                    mask = cv2.resize(mask, (canvas_w, canvas_h), interpolation=cv2.INTER_NEAREST)
                win.paint_image = Image.fromarray(mask).convert('L')
            else:
                win.paint_image = Image.new('L', (canvas_w, canvas_h), 255)
        else:
            win.paint_image = Image.new('L', (canvas_w, canvas_h), 255)

        win.paint_draw = ImageDraw.Draw(win.paint_image)

        def refresh_canvas():
            win.tk_photo = ImageTk.PhotoImage(win.paint_image)
            canvas.delete("all")
            canvas.create_image(0, 0, image=win.tk_photo, anchor='nw')

        win.refresh_canvas = refresh_canvas
        refresh_canvas()

        win.current_tool = 'brush'
        win.brush_size = 8
        win.last_x = None
        win.last_y = None

        def save_canvas_to_var():
            self._updating_canvas_var = True
            try:
                buffered = io.BytesIO()
                win.paint_image.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
                self.vars['fx_paint_canvas_data'].set(img_str)
            finally:
                self._updating_canvas_var = False

        def draw_stroke(x1, y1, x2, y2):
            color_val = 0 if win.current_tool == 'brush' else 255
            win.paint_draw.line([x1, y1, x2, y2], fill=color_val, width=win.brush_size, joint='round')
            color_hex = C_BLACK if win.current_tool == 'brush' else C_WHITE
            canvas.create_line(x1, y1, x2, y2, fill=color_hex, width=win.brush_size, capstyle='round', joinstyle='round')

        from PIL import ImageFont

        def get_system_fonts():
            import platform
            fonts = {}
            if platform.system() == 'Windows':
                import winreg   # модуль только для Windows - импорт внутри проверки
                for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    try:
                        key = winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
                        windir = os.environ.get('WINDIR', 'C:\\Windows')
                        local_fonts_dir = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Windows\Fonts')
                        num_values = winreg.QueryInfoKey(key)[1]
                        for i in range(num_values):
                            try:
                                name, data, _ = winreg.EnumValue(key, i)
                                clean_name = name.split('(')[0].strip()
                                if not (data.lower().endswith('.ttf') or data.lower().endswith('.otf')):
                                    continue
                                if os.path.isabs(data):
                                    fonts[clean_name] = data
                                else:
                                    sys_path = os.path.join(windir, 'Fonts', data)
                                    if os.path.exists(sys_path):
                                        fonts[clean_name] = sys_path
                                    else:
                                        user_path = os.path.join(local_fonts_dir, data)
                                        if os.path.exists(user_path):
                                            fonts[clean_name] = user_path
                                        else:
                                            fonts[clean_name] = data
                            except OSError:
                                continue
                        winreg.CloseKey(key)
                    except Exception:
                        pass
            # стандартный запасной список шрифтов
            fallback = {
                'Arial': 'arial.ttf',
                'Courier New': 'cour.ttf',
                'Times New Roman': 'times.ttf',
                'Tahoma': 'tahoma.ttf',
                'Verdana': 'verdana.ttf',
                'MS Sans Serif': 'micross.ttf'
            }
            for name, path in fallback.items():
                if name not in fonts:
                    fonts[name] = path
            return fonts

        system_fonts = get_system_fonts()
        font_names = sorted(list(system_fonts.keys()))
        default_font = 'Arial' if 'Arial' in system_fonts else (font_names[0] if font_names else '')

        def get_pil_font(name: str, size: int):
            font_path = system_fonts.get(name, 'arial.ttf')
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                try:
                    return ImageFont.truetype("arial.ttf", size)
                except Exception:
                    return ImageFont.load_default()

        # Ссылки на поля ввода текста
        text_entry = None
        font_combo = None

        def on_click(event):
            if win.current_tool == 'text':
                text_str = text_entry.get() if text_entry else ""
                if not text_str:
                    return
                font_name = font_combo.get() if font_combo else "Arial"
                size = max(8, int(win.brush_size * 2))
                
                pil_font = get_pil_font(font_name, size)
                win.paint_draw.text((event.x, event.y), text_str, fill=0, font=pil_font, anchor='mm')
                canvas.create_text(event.x, event.y, text=text_str, font=(font_name, size), fill=C_BLACK, anchor='center')
                save_canvas_to_var()
            else:
                win.last_x = event.x
                win.last_y = event.y
                draw_stroke(event.x, event.y, event.x, event.y)

        def on_drag(event):
            if win.current_tool == 'text':
                return
            if win.last_x is not None and win.last_y is not None:
                draw_stroke(win.last_x, win.last_y, event.x, event.y)
            win.last_x = event.x
            win.last_y = event.y

        def on_release(event):
            if win.current_tool == 'text':
                return
            win.last_x = None
            win.last_y = None
            save_canvas_to_var()

        canvas.bind('<Button-1>', on_click)
        canvas.bind('<B1-Motion>', on_drag)
        canvas.bind('<ButtonRelease-1>', on_release)

        def on_right_click(event):
            if win.current_tool == 'text':
                return
            win.last_x = event.x
            win.last_y = event.y
            old_tool = win.current_tool
            win.current_tool = 'eraser'
            draw_stroke(event.x, event.y, event.x, event.y)
            win.current_tool = old_tool

        def on_right_drag(event):
            if win.current_tool == 'text':
                return
            if win.last_x is not None and win.last_y is not None:
                old_tool = win.current_tool
                win.current_tool = 'eraser'
                draw_stroke(win.last_x, win.last_y, event.x, event.y)
                win.current_tool = old_tool
            win.last_x = event.x
            win.last_y = event.y

        canvas.bind('<Button-3>', on_right_click)
        canvas.bind('<B3-Motion>', on_right_drag)
        canvas.bind('<ButtonRelease-3>', on_release)

        # 1. Секция инструментов
        tk.Label(toolbar, text="Tools / Инстр.", font=('MS Sans Serif', 8, 'bold'), bg=C_SILVER).pack(anchor='w', padx=2, pady=2)
        
        tools_frame = tk.Frame(toolbar, bg=C_SILVER)
        tools_frame.pack(fill='x', pady=2)

        btn_brush = tk.Button(tools_frame, text="Brush", relief='sunken', bg='#D0D0D0', bd=2)
        btn_eraser = tk.Button(tools_frame, text="Eraser", relief='raised', bg=C_SILVER, bd=2)
        btn_text = tk.Button(tools_frame, text="Text", relief='raised', bg=C_SILVER, bd=2)
        
        btn_brush.pack(side='left', fill='x', expand=True, padx=1)
        btn_eraser.pack(side='left', fill='x', expand=True, padx=1)
        btn_text.pack(side='left', fill='x', expand=True, padx=1)
        
        def set_brush_tool():
            win.current_tool = 'brush'
            btn_brush.configure(relief='sunken', bg='#D0D0D0')
            btn_eraser.configure(relief='raised', bg=C_SILVER)
            btn_text.configure(relief='raised', bg=C_SILVER)
            
        def set_eraser_tool():
            win.current_tool = 'eraser'
            btn_brush.configure(relief='raised', bg=C_SILVER)
            btn_eraser.configure(relief='sunken', bg='#D0D0D0')
            btn_text.configure(relief='raised', bg=C_SILVER)
            
        def set_text_tool():
            win.current_tool = 'text'
            btn_brush.configure(relief='raised', bg=C_SILVER)
            btn_eraser.configure(relief='raised', bg=C_SILVER)
            btn_text.configure(relief='sunken', bg='#D0D0D0')
            
        btn_brush.configure(command=set_brush_tool)
        btn_eraser.configure(command=set_eraser_tool)
        btn_text.configure(command=set_text_tool)

        Tooltip(btn_brush, bi("Draw black strokes (LMB draw, RMB erase)", "Рисовать черным цветом (ЛКМ - кисть, ПКМ - стерка)"))
        Tooltip(btn_eraser, bi("Erase strokes (turns area white)", "Стереть нарисованное (стирает белым цветом)"))
        Tooltip(btn_text, bi("Type text on canvas (click to place)", "Написать текст на холсте (кликните для размещения)"))

        # 1б. Настройки текста (под инструментами)
        tk.Label(toolbar, text="Text / Текст", font=('MS Sans Serif', 8), bg=C_SILVER).pack(anchor='w', padx=2, pady=(4, 0))
        text_entry = ttk.Entry(toolbar, width=12)
        text_entry.insert(0, "VPC")
        text_entry.pack(fill='x', padx=4, pady=1)
        Tooltip(text_entry, bi("Type text to place on click", "Введите текст для вставки по клику"))

        font_combo = ttk.Combobox(toolbar, values=font_names, style='W95.TCombobox', width=10)
        font_combo.set(default_font)
        font_combo.pack(fill='x', padx=4, pady=1)
        Tooltip(font_combo, bi("Select text font", "Выберите шрифт текста"))

        # 2. Секция размера
        tk.Label(toolbar, text="Size / Размер", font=('MS Sans Serif', 8), bg=C_SILVER).pack(anchor='w', padx=2, pady=(6, 0))
        size_label = tk.Label(toolbar, text=f"{win.brush_size} px", font=('MS Sans Serif', 8), bg=C_SILVER)
        
        def on_size_change(val):
            win.brush_size = int(float(val))
            size_label.configure(text=f"{win.brush_size} px")
            
        size_scale = ttk.Scale(toolbar, from_=1, to=50, value=win.brush_size, command=on_size_change, orient='horizontal')
        size_scale.pack(fill='x', padx=4)
        size_label.pack(anchor='w', padx=2)
        Tooltip(size_scale, bi("Adjust brush size or font size", "Настроить размер кисти/ластика или шрифта"))

        tk.Frame(toolbar, bg=C_DARK_GRAY, height=2).pack(fill='x', pady=6)

        # 3. Секция действий с холстом (кнопки в ряд, чтобы не растягивать панель по вертикали)
        tk.Label(toolbar, text="Canvas / Холст", font=('MS Sans Serif', 8, 'bold'), bg=C_SILVER).pack(anchor='w', padx=2, pady=2)

        def clear_canvas():
            if messagebox.askyesno("Clear? / Очистить?", "Clear the whole canvas?\nОчистить весь холст?", parent=win):
                win.paint_draw.rectangle([0, 0, canvas_w, canvas_h], fill=255)
                refresh_canvas()
                save_canvas_to_var()

        def invert_canvas():
            img_arr = np.array(win.paint_image)
            inverted_arr = 255 - img_arr
            win.paint_image = Image.fromarray(inverted_arr).convert('L')
            win.paint_draw = ImageDraw.Draw(win.paint_image)
            refresh_canvas()
            save_canvas_to_var()

        def ask_import_mode(parent):
            dialog = tk.Toplevel(parent)
            dialog.title("Import Mode / Режим импорта")
            dialog.transient(parent)
            dialog.grab_set()
            dialog.resizable(False, False)
            dialog.configure(bg=C_SILVER)

            # Центрируем относительно родительского окна
            dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 100, parent.winfo_rooty() + 100))

            choice = [None]  # изменяемый контейнер для хранения выбора

            tk.Label(
                dialog,
                text="Choose image import mode / Выберите режим импорта:",
                font=('MS Sans Serif', 9, 'bold'),
                bg=C_SILVER,
                padx=15,
                pady=10
            ).pack()

            btn_frame = tk.Frame(dialog, bg=C_SILVER, pady=10)
            btn_frame.pack()

            def choose(mode):
                choice[0] = mode
                dialog.destroy()

            b_contours = tk.Button(btn_frame, text="Contours / Контуры", font=('MS Sans Serif', 9), bg=C_SILVER, bd=2, relief='raised', command=lambda: choose('contours'))
            b_contours.pack(side='left', padx=5)
            Tooltip(b_contours, bi("Extract thin outlines of the image", "Извлечь тонкие контуры изображения (линии)"))

            b_silhouette = tk.Button(btn_frame, text="Silhouette / Силуэт", font=('MS Sans Serif', 9), bg=C_SILVER, bd=2, relief='raised', command=lambda: choose('silhouette'))
            b_silhouette.pack(side='left', padx=5)
            Tooltip(b_silhouette, bi("Binarize image into black and white regions", "Бинаризовать изображение на черные и белые области (заливка)"))

            b_cancel = tk.Button(btn_frame, text="Cancel / Отмена", font=('MS Sans Serif', 9), bg=C_SILVER, bd=2, relief='raised', command=dialog.destroy)
            b_cancel.pack(side='left', padx=5)

            parent.wait_window(dialog)
            return choice[0]

        def load_image_outline():
            path = filedialog.askopenfilename(
                title="Select Photo / Выберите фото",
                filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp *.webp")],
                parent=win
            )
            if not path:
                return
            
            mode = ask_import_mode(win)
            if mode is None:
                return

            try:
                # Сначала читаем байты файла, чтобы корректно работать с юникодными путями на Windows (например, кириллицей)
                with open(path, "rb") as f:
                    file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                img_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
                
                if img_bgr is None:
                    messagebox.showerror("Error", "Could not read image.\nНе удалось загрузить изображение.", parent=win)
                    return
                img_resized = cv2.resize(img_bgr, (canvas_w, canvas_h))
                gray = cv2.cvtColor(img_resized, cv2.COLOR_BGR2GRAY)

                if mode == 'contours':
                    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
                    edges = cv2.Canny(blurred, 40, 120)
                    outline = 255 - edges
                else:  # silhouette
                    # Метод Оцу дает чистую черно-белую маску
                    _, binarized = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    outline = binarized
                
                win.paint_image = Image.fromarray(outline).convert('L')
                win.paint_draw = ImageDraw.Draw(win.paint_image)
                refresh_canvas()
                save_canvas_to_var()
            except Exception as ex:
                messagebox.showerror("Error", f"Failed to load image:\n{ex}", parent=win)

        actions_frame = tk.Frame(toolbar, bg=C_SILVER)
        actions_frame.pack(fill='x', pady=2)

        btn_clear = tk.Button(actions_frame, text="Clear", bg=C_SILVER, bd=2, relief='raised', command=clear_canvas)
        btn_invert = tk.Button(actions_frame, text="Invert", bg=C_SILVER, bd=2, relief='raised', command=invert_canvas)
        btn_load = tk.Button(actions_frame, text="Load", bg=C_SILVER, bd=2, relief='raised', command=load_image_outline)

        btn_clear.pack(side='left', fill='x', expand=True, padx=1)
        btn_invert.pack(side='left', fill='x', expand=True, padx=1)
        btn_load.pack(side='left', fill='x', expand=True, padx=1)

        Tooltip(btn_clear, bi("Clear the entire canvas to white", "Полностью очистить холст в белый цвет"))
        Tooltip(btn_invert, bi("Invert colors (black <-> white)", "Инвертировать цвета (черный <-> белый)"))
        Tooltip(btn_load, bi("Load a photo and extract its contours/outlines", "Загрузить фото и извлечь его контуры (рисунок)"))

        tk.Frame(toolbar, bg=C_DARK_GRAY, height=2).pack(fill='x', pady=6)

        # 4. Секция выбора цвета
        tk.Label(toolbar, text="Overlay Color / Цвет", font=('MS Sans Serif', 8, 'bold'), bg=C_SILVER).pack(anchor='w', padx=2, pady=2)

        color_top_frame = tk.Frame(toolbar, bg=C_SILVER)
        color_top_frame.pack(fill='x', pady=2)

        color_preview = tk.Frame(color_top_frame, width=24, height=24, bd=2, relief='sunken')
        color_preview.pack(side='left', padx=4)
        color_preview.pack_propagate(False)

        color_box = tk.Frame(color_preview, bg='#00ff00')
        color_box.pack(fill='both', expand=True)

        def update_color_preview():
            r = int(self.vars['fx_paint_color_r'].get())
            g = int(self.vars['fx_paint_color_g'].get())
            b = int(self.vars['fx_paint_color_b'].get())
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            color_box.configure(bg=hex_color)

        update_color_preview()

        # Сетка образцов (2 ряда по 5 классических цветов Paint)
        swatches_frame = tk.Frame(toolbar, bg=C_SILVER)
        swatches_frame.pack(pady=4)

        classic_colors = [
            (0, 0, 0),        # Черный
            (128, 128, 128),  # Темно-серый
            (255, 0, 0),      # Красный
            (0, 255, 0),      # Зеленый
            (0, 0, 255),      # Синий
            
            (255, 255, 255),  # Белый
            (192, 192, 192),  # Светло-серый
            (255, 255, 0),    # Желтый
            (255, 0, 255),    # Пурпурный
            (0, 255, 255),    # Голубой
        ]

        def select_color(r, g, b):
            self.vars['fx_paint_color_r'].set(r)
            self.vars['fx_paint_color_g'].set(g)
            self.vars['fx_paint_color_b'].set(b)
            update_color_preview()

        for idx, (r, g, b) in enumerate(classic_colors):
            row_idx = idx // 5
            col_idx = idx % 5
            hex_c = f"#{r:02x}{g:02x}{b:02x}"
            btn = tk.Button(swatches_frame, bg=hex_c, width=2, height=0, bd=1, relief='raised',
                            command=lambda r=r, g=g, b=b: select_color(r, g, b))
            btn.grid(row=row_idx, column=col_idx, padx=1, pady=1)
            Tooltip(btn, bi(f"Set color to {hex_c}", f"Выбрать цвет {hex_c}"))

        from tkinter import colorchooser

        def choose_custom_color():
            r = int(self.vars['fx_paint_color_r'].get())
            g = int(self.vars['fx_paint_color_g'].get())
            b = int(self.vars['fx_paint_color_b'].get())
            initial = f"#{r:02x}{g:02x}{b:02x}"
            color_choice = colorchooser.askcolor(initialcolor=initial, parent=win)
            if color_choice and color_choice[0]:
                rc, gc, bc = color_choice[0]
                select_color(int(rc), int(gc), int(bc))

        btn_palette = tk.Button(color_top_frame, text="Palette...", font=('MS Sans Serif', 8), bg=C_SILVER, bd=2, relief='raised', command=choose_custom_color)
        btn_palette.pack(side='left', fill='x', expand=True, padx=4)
        Tooltip(btn_palette, bi("Open system color picker to select custom color", "Открыть системную палитру для выбора цвета"))

    def _build_overlay_dir_picker(self, body):
        bf = tk.Frame(body, bg=C_WHITE)
        bf.pack(fill='x', padx=10, pady=(4, 8))
        ttk.Button(bf, text='Select Overlay Folder...',
                   command=self.sel_ov, style='W95.TButton').pack(fill='x')
        self.lbl_overlay_dir = tk.Label(bf, text='No folder selected',
                                        bg=C_WHITE, fg=C_DARK_GRAY,
                                        font=('Courier New', 9))
        self.lbl_overlay_dir.pack(anchor='w', pady=(2, 0))

    def _build_file_picker(self, parent, p):
        """Кнопка выбора файла + подпись с именем для ParamSpec kind='file'.

        Путь хранится в self.vars[p.key] как обычная строка cfg, поэтому
        подхватывается сохранением/загрузкой пресетов без доп. кода.
        """
        var = self.vars[p.key]
        bf = tk.Frame(parent, bg=C_WHITE)
        bf.pack(fill='x', padx=20, pady=2)

        lbl = tk.Label(bf, bg=C_WHITE, fg=C_DARK_GRAY, font=('Courier New', 9),
                       anchor='w')

        def _refresh(*_a):
            cur = var.get()
            lbl.configure(text=os.path.basename(cur) if cur else 'No file selected')

        def _pick():
            path = filedialog.askopenfilename(
                filetypes=[('Image files', '*.png *.jpg *.jpeg *.bmp *.webp')])
            if path:
                var.set(path)

        def _clear():
            var.set('')

        btns = tk.Frame(bf, bg=C_WHITE)
        btns.pack(fill='x')
        ttk.Button(btns, text=p.label, command=_pick,
                   style='W95.TButton').pack(side='left', fill='x', expand=True)
        ttk.Button(btns, text='×', width=3, command=_clear,
                   style='W95.TButton').pack(side='left', padx=(4, 0))
        lbl.pack(fill='x', pady=(2, 0))
        var.trace_add('write', _refresh)
        _refresh()
        if p.tooltip:
            Tooltip(lbl, p.tooltip)

    def _get_source_aspect_ratio(self):
        if self.video_paths:
            path = self.video_paths[0]
            from vpc.render.source import is_image
            from vpc.render.image_source import imread_unicode
            if is_image(path):
                img = imread_unicode(path)
                if img is not None and img.shape[0] > 0:
                    return img.shape[1] / img.shape[0]
            else:
                cap = cv2.VideoCapture(path)
                try:
                    if cap.isOpened():
                        w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                        h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                        if w > 0 and h > 0:
                            return w / h
                finally:
                    cap.release()
        return 16.0 / 9.0

    def _setup_paint_canvas_trace(self):
        self.vars['fx_paint_canvas_data'].trace_add('write', self._on_paint_canvas_data_changed)

    def _on_paint_canvas_data_changed(self, *args):
        if getattr(self, '_updating_canvas_var', False):
            return
        win = getattr(self, '_paint_win', None)
        if win and win.winfo_exists():
            raw_data = self.vars['fx_paint_canvas_data'].get()
            canvas_w = win.canvas_w
            canvas_h = win.canvas_h
            if raw_data:
                from vpc.effects.paint import decode_paint_canvas
                mask = decode_paint_canvas(raw_data)
                if mask is not None:
                    if mask.shape[1] != canvas_w or mask.shape[0] != canvas_h:
                        mask = cv2.resize(mask, (canvas_w, canvas_h), interpolation=cv2.INTER_NEAREST)
                    win.paint_image = Image.fromarray(mask).convert('L')
                else:
                    win.paint_image = Image.new('L', (canvas_w, canvas_h), 255)
            else:
                win.paint_image = Image.new('L', (canvas_w, canvas_h), 255)
            win.paint_draw = ImageDraw.Draw(win.paint_image)
            if hasattr(win, 'refresh_canvas'):
                win.refresh_canvas()


    # ─── панель экспорта ───
    def _build_export_panel(self, parent):
        for w in parent.winfo_children():
            w.destroy()
        wr = tk.Frame(parent, bg=C_SILVER)
        wr.pack(fill='both', expand=True, padx=4, pady=4)

        # Частота кадров
        self._row_with_help(wr, 'Frame Rate', bi(
            'Output FPS. Higher = smoother and bigger files.',
            'FPS выходного видео. Выше — плавнее и тяжелее файл.'))
        fr = tk.Frame(wr, bg=C_SILVER); fr.pack(fill='x', padx=20, pady=2)
        tk.Label(fr, text='FPS:', bg=C_SILVER, width=10, anchor='w').pack(side='left')
        self.fps_combo = ttk.Combobox(fr, values=['12', '24', '30', '60'],
                                      style='W95.TCombobox', width=8)
        self.fps_combo.set('24'); self.fps_combo.pack(side='left', padx=4)
        self.fps_combo.bind('<<ComboboxSelected>>',
                            lambda e: self.vars['fps'].set(float(self.fps_combo.get())))
        # Кнопка Match source: читает нативный FPS загруженного исходного видео
        # через OpenCV и подставляет его в комбобокс + cfg. Полезно в режиме
        # passthrough (чтобы выход был битово точен по времени) и вне его (чтобы
        # пайплайн нарезки сэмплировал с нативной частотой источника). При
        # пустом выборе просто пишет ошибку в лог, а не падает.
        match_btn = ttk.Button(fr, text='Match source FPS',
                               command=self._fps_match_source,
                               style='W95.TButton')
        match_btn.pack(side='left', padx=4)
        Tooltip(match_btn, bi(
            "Read the native FPS of the currently loaded source video and use it. "
            "Requires Load Source Video to be set first.",
            "Считать нативный FPS текущего загруженного видео и поставить его. "
            "Сначала нужно загрузить источник через Load Source Video."))

        # Режим разрешения
        self._row_with_help(wr, 'Resolution Mode', bi(
            'preset = pick from 240p–1080p list. '
            'source = output matches the input video pixel-for-pixel. '
            'custom = type your own width/height.',
            'preset — выбрать из списка 240p–1080p. '
            'source — выход совпадает с источником пиксель в пиксель. '
            'custom — задать свои ширину/высоту.'))
        rmf = tk.Frame(wr, bg=C_SILVER); rmf.pack(fill='x', padx=20, pady=2)
        for val, lbl in [('preset', 'Preset'), ('source', 'Match source'), ('custom', 'Custom')]:
            tk.Radiobutton(rmf, text=lbl, variable=self.var_resolution_mode, value=val,
                           bg=C_SILVER, fg=C_TEXT, selectcolor=C_WHITE,
                           font=('MS Sans Serif', 9)).pack(side='left', padx=4)

        # Выпадающий список пресетов
        rr = tk.Frame(wr, bg=C_SILVER); rr.pack(fill='x', padx=20, pady=2)
        tk.Label(rr, text='Preset:', bg=C_SILVER, width=10, anchor='w').pack(side='left')
        self.res_combo = ttk.Combobox(rr, values=['240p', '360p', '480p', '720p', '1080p'],
                                      style='W95.TCombobox', width=12)
        self.res_combo.set('720p'); self.res_combo.pack(side='left', padx=4)

        # Произвольные ширина/высота
        cwf = tk.Frame(wr, bg=C_SILVER); cwf.pack(fill='x', padx=20, pady=2)
        tk.Label(cwf, text='Custom W×H:', bg=C_SILVER, width=12, anchor='w').pack(side='left')
        ttk.Spinbox(cwf, from_=64, to=7680, textvariable=self.vars['custom_w'],
                    width=8).pack(side='left', padx=2)
        tk.Label(cwf, text='×', bg=C_SILVER).pack(side='left')
        ttk.Spinbox(cwf, from_=64, to=4320, textvariable=self.vars['custom_h'],
                    width=8).pack(side='left', padx=2)

        # Пресет качества - удобная надстройка, заполняющая CRF / ffmpeg
        # preset / tune ниже. Выбор пункта записывает эти три поля;
        # ручное изменение любого из них переключает выпадающий список
        # обратно в 'Custom'. Ручное управление никогда не блокируется.
        self._row_with_help(wr, 'Quality', bi(
            'Convenience preset that fills CRF, ffmpeg Preset and Tune below. '
            "Pick 'Custom' or just edit any of those manually for full control. "
            'Archive = grain-tuned archival, High = visually lossless default, '
            'Web = smaller/faster, Compact = smallest watchable.',
            'Удобный пресет: заполняет CRF, ffmpeg Preset и Tune ниже одним кликом. '
            "'Custom' или ручное редактирование любого из полей — всё под контролем. "
            'Archive — архив с tune=grain, High — визуально без потерь по умолчанию, '
            'Web — меньше/быстрее, Compact — самый компактный смотрибельный.'))
        qf = tk.Frame(wr, bg=C_SILVER); qf.pack(fill='x', padx=20, pady=2)
        self.quality_combo = ttk.Combobox(
            qf, values=quality_preset_names(), textvariable=self.var_quality_preset,
            style='W95.TCombobox', width=12, state='readonly')
        self.quality_combo.pack(side='left', padx=4)
        self.quality_combo.bind('<<ComboboxSelected>>',
                                lambda e: self._on_quality_preset_changed())

        # CRF / кодек / preset (вручную)
        self._row_with_help(wr, 'Quality CRF', bi(
            '0 = lossless, 18 = visually lossless, 28 = small files, 51 = artifact art.',
            '0 — без потерь, 18 — визуально без потерь, 28 — малый размер, 51 — арт из '
            'артефактов.'))
        self._slider(wr, 'crf', 0, 51)

        self._row_with_help_popup(wr, 'Codec',
            short_tip=bi(
                'Codec / container combo. Click [?] for the full guide '
                '(use cases, HW encoders, fallback behaviour).',
                'Связка кодек / контейнер. Клик по [?] — полный гид '
                '(сценарии, аппаратные кодеры, fallback).'),
            full_text=bi(
            'Codec / container combination.\n\n'
            'GUIDE — pick by use case:\n'
            '• H.264 (MP4) — default. Universal compatibility, libx264 software '
            'encode. Always works. Use this if unsure.\n'
            '• H.265 (MP4/MKV) — ~30% smaller files at same quality, slower CPU '
            'encode. Less compatible playback (older devices, web embeds).\n'
            '• MKV variants — Matroska container. Use if MP4 muxer rejects your '
            'stream (rare; happens with some experimental codec settings).\n'
            '• ProRes (MOV) — editing-grade master, huge files, perfect for '
            'handing off to DaVinci/Premiere. Ignores CRF/preset/tune.\n'
            '• VP9 (WebM) — open codec for web. Slow encode.\n\n'
            'HARDWARE encoders (only listed if your ffmpeg supports them):\n'
            '• NVENC — NVIDIA GPU. Fast at 1080p+. Slight quality loss vs '
            'libx264 at the same bitrate.\n'
            '• QSV — Intel iGPU / Arc. Similar tradeoff.\n'
            '• AMF — AMD GPU on Windows.\n'
            '• VideoToolbox — Apple Silicon / Intel Macs.\n\n'
            'CAVEAT: HW encoders can be SLOWER than libx264 for sub-720p '
            'output (PCIe upload + driver init overhead). For ≤480p material, '
            'use H.264 (MP4).\n\n'
            'SAFETY: before each render the program runs a 1-second self-test '
            'against the chosen HW encoder. If the encoder hangs or errors '
            'out, the render automatically falls back to libx264 with a log '
            'note — no action needed from you. Result is cached for the '
            'session, so the test only fires once per HW encoder.',

            'Связка кодек/контейнер.\n\n'
            'ВЫБОР по сценарию:\n'
            '• H.264 (MP4) — по умолчанию. Универсальная совместимость, '
            'программный libx264. Работает всегда. Выбирайте если не уверены.\n'
            '• H.265 (MP4/MKV) — ~30% меньше файл при том же качестве, '
            'медленнее на CPU. Хуже совместимость (старые устройства, веб).\n'
            '• MKV-варианты — контейнер Matroska. Брать, если MP4 muxer '
            'не пропускает поток (редко; экспериментальные настройки).\n'
            '• ProRes (MOV) — мастер монтажного качества, огромные файлы, '
            'для передачи в DaVinci/Premiere. Игнорирует CRF/preset/tune.\n'
            '• VP9 (WebM) — открытый кодек для веба. Медленный.\n\n'
            'АППАРАТНЫЕ кодеры (показываются только если их есть в ffmpeg):\n'
            '• NVENC — GPU NVIDIA. Быстро на 1080p+. Чуть хуже качество '
            'на том же битрейте, чем libx264.\n'
            '• QSV — Intel iGPU / Arc. Похожий компромисс.\n'
            '• AMF — AMD GPU на Windows.\n'
            '• VideoToolbox — Apple Silicon / Intel Macs.\n\n'
            'ВАЖНО: HW-кодеры могут быть МЕДЛЕННЕЕ libx264 на разрешениях '
            'ниже 720p (PCIe-upload + инициализация драйвера). Для ≤480p '
            'материала используйте H.264 (MP4).\n\n'
            'БЕЗОПАСНОСТЬ: перед каждым рендером программа сама прогоняет '
            '1-секундный self-test выбранного HW-кодера. Если он зависает '
            'или ошибочный — рендер автоматически переключается на libx264 '
            'с записью в лог, никаких действий от вас не требуется. '
            'Результат кешируется на сессию: тест запускается один раз '
            'на каждый HW-кодек.'))
        cf = tk.Frame(wr, bg=C_SILVER); cf.pack(fill='x', padx=20, pady=2)
        # Список кодеков фильтруется при старте по `ffmpeg -encoders` -
        # аппаратные варианты (NVENC/QSV/AMF/VideoToolbox) показываются, только
        # если локальная сборка ffmpeg их реально поддерживает. Fallback в
        # engine.py на стороне рантайма покрывает случай, когда кодер
        # значится в списке, но не смог инициализироваться (нет драйвера, GPU занят и т.п.).
        codec_labels = [s.label for s in available_encoder_specs()]
        self.fmt_combo = ttk.Combobox(
            cf, values=codec_labels,
            style='W95.TCombobox', width=26, state='readonly')
        self.fmt_combo.set('H.264 (MP4)'); self.fmt_combo.pack(side='left', padx=4)

        self._row_with_help(wr, 'ffmpeg Preset', bi(
            'ultrafast = quick test, slow = best compression.',
            'ultrafast — быстрая проверка, slow — лучшее сжатие.'))
        ef = tk.Frame(wr, bg=C_SILVER); ef.pack(fill='x', padx=20, pady=2)
        self.preset_enc_combo = ttk.Combobox(
            ef, values=['ultrafast', 'fast', 'medium', 'slow'],
            style='W95.TCombobox', width=12)
        self.preset_enc_combo.set('medium'); self.preset_enc_combo.pack(side='left', padx=4)
        self.preset_enc_combo.bind('<<ComboboxSelected>>',
                                   lambda e: self._refresh_quality_label())

        # Tune - подсказка film/grain/animation/stillimage только для x264/x265.
        # 'none' означает, что флаг вообще не передаётся. Доступно и для других
        # кодеков, но там игнорируется дальше по цепочке (см. sink.py).
        self._row_with_help(wr, 'Tune', bi(
            'x264/x265 -tune hint. film = clean live action, grain = preserves '
            'noise (good for datamosh/glitch material), animation, stillimage. '
            "'none' = no -tune flag. Ignored for non-x264/x265 codecs.",
            'Подсказка -tune для x264/x265. film — чистое видео, grain — сохраняет '
            'шум (полезно для datamosh/глитча), animation, stillimage. '
            "'none' — флаг не передаётся. Игнорируется для других кодеков."))
        tf = tk.Frame(wr, bg=C_SILVER); tf.pack(fill='x', padx=20, pady=2)
        self.tune_combo = ttk.Combobox(
            tf, values=list(TUNE_VALUES), textvariable=self.var_tune,
            style='W95.TCombobox', width=12, state='readonly')
        self.tune_combo.pack(side='left', padx=4)
        self.tune_combo.bind('<<ComboboxSelected>>',
                             lambda e: self._refresh_quality_label())

        # Ручное изменение CRF переключает выпадающий список качества в Custom.
        # Trace добавляется один раз здесь, после появления var_quality_preset.
        # Защита от реентерабельности не даёт trace сработать во время
        # применения пресета (который сам пишет crf).
        self.vars['crf'].trace_add('write',
                                   lambda *_: self._refresh_quality_label())
        self._refresh_quality_label()

    # ─── панель FORMULA (в стиле TUI, отдельная вкладка) ───
    FORMULA_SNIPPETS = [
        ('Identity', 'frame'),
        ('Invert', '255 - frame'),
        ('Pulse', 'np.clip(frame.astype(np.int16) * (1 + a*np.sin(t*5)), 0, 255).astype(np.uint8)'),
        ('Channel sweep',
         'np.dstack([np.roll(r, int(40*a*np.sin(t*3)), 1), g, '
         'np.roll(b, -int(40*a*np.sin(t*3)), 1)])'),
        ('Posterize', '(frame >> int(1 + a*4)) << int(1 + a*4)'),
        ('Scanlines',
         "np.where((y.astype(int) % 2 == 0)[:,:,None], "
         "frame, (frame * (1 - a)).astype(np.uint8))"),
        ('Wave',
         'np.clip(frame.astype(np.int16) + '
         '(np.sin(y*0.1 + t*3)*60*a).astype(np.int16)[:,:,None], '
         '0, 255).astype(np.uint8)'),
        ('Plasma',
         'np.clip(frame.astype(np.int16) + '
         '((np.sin(x*0.05 + t)*120 + np.cos(y*0.05 + t)*120)*a)'
         '.astype(np.int16)[:,:,None], 0, 255).astype(np.uint8)'),
        ('Threshold',
         'np.where(frame > int(128 + 100*a*np.sin(t*2)), 255, 0).astype(np.uint8)'),
        ('Mirror',
         'np.where((x < frame.shape[1]/2)[:,:,None], frame, frame[:,::-1])'),
    ]

    def _bsod_label(self, parent, text, *, fg=None, font=None, **kw):
        bg = parent.cget('bg') if hasattr(parent, 'cget') else C_BSOD_BG
        return tk.Label(parent, text=text,
                        bg=bg, fg=fg or C_BSOD_FG,
                        font=font or ('Consolas', 10),
                        **kw)

    # ── подсказка в стиле BSOD (желтое на синем, моноширинный шрифт) ──
    def _bsod_tip(self, widget, text):
        """Всплывающая подсказка в стиле остальной вкладки BSOD.

        Желтый текст на фоне BSOD с тонкой белой рамкой - соответствует
        эстетике "системной ошибки" вместо конфликта с желтыми подсказками
        Win95. Поддерживается двуязычный разделитель EN/RU.
        """
        # Используем существующий хелпер Tooltip, но переопределяем стиль
        # всплывающего окна. Класс Tooltip создает свой label по событию
        # enter, поэтому оборачиваем создание.
        class _BsodTip(Tooltip):
            def _enter(self_, e):
                if self_.tip or not self_.text:
                    return
                self_.tip = tk.Toplevel(self_.widget)
                self_.tip.wm_overrideredirect(True)
                self_.tip.wm_geometry(f'+{e.x_root + 14}+{e.y_root + 8}')
                tk.Label(self_.tip, text=self_.text,
                         bg=C_BSOD_BG, fg=C_BSOD_ACCENT,
                         font=('Consolas', 9), bd=1, relief='solid',
                         padx=6, pady=4, wraplength=420, justify='left'
                         ).pack()
        _BsodTip(widget, text)

    def _build_formula_panel(self, parent):
        """Вкладка FORMULA - палитра Win9x BSOD, моноширинный шрифт, полностью со скроллом.

        Раскладка подобрана так, чтобы ничего не вылезало по горизонтали:
        каждая длинная строка переносится по ширине панели (отслеживается
        динамически), сетка сниппетов сжимается до 2 колонок на узкой панели,
        и у каждого элемента управления есть двуязычная подсказка - авторы
        формул без знания NumPy всё равно получают подсказки по каждому полю.
        """
        for w in parent.winfo_children():
            w.destroy()
        parent.configure(bg=C_BSOD_BG)

        canvas = tk.Canvas(parent, bg=C_BSOD_BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        inner = tk.Frame(canvas, bg=C_BSOD_BG)
        inner_id = canvas.create_window((0, 0), window=inner, anchor='nw')

        # Длина переноса - плавающая величина: пользователь может изменить
        # размер окна в любой момент. Отслеживаем все подписи, которым нужен
        # перенос, и обновляем их все по событию canvas <Configure>.
        wrap_labels: list[tk.Label] = []

        def _refresh_scroll(_e=None):
            bbox = canvas.bbox('all')
            if bbox is not None:
                canvas.configure(scrollregion=bbox)
        inner.bind('<Configure>', _refresh_scroll)

        def _on_canvas_resize(e):
            canvas.itemconfig(inner_id, width=e.width)
            wrap = max(200, e.width - 32)
            for lbl in wrap_labels:
                try:
                    lbl.configure(wraplength=wrap)
                except tk.TclError:
                    pass
            _refresh_scroll()
        canvas.bind('<Configure>', _on_canvas_resize)

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _wheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        MONO = ('Consolas', 10)
        MONO_B = ('Consolas', 11, 'bold')
        MONO_S = ('Consolas', 9)

        def _wrap_lbl(parent, text, *, fg, font):
            lbl = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=fg,
                           font=font, anchor='w', justify='left',
                           wraplength=400)
            wrap_labels.append(lbl)
            return lbl

        # ── Заголовок ────────────────────────────────────────────────
        # Строка "A problem has been detected..." - намеренная стилизация
        # под BSOD (узнаваемый текст краша Win9x), она сигнализирует, что
        # эта вкладка "опасная". Проблема была только в размерах текста.
        head = tk.Frame(inner, bg=C_BSOD_BG)
        head.pack(fill='x', padx=12, pady=(10, 2))
        _wrap_lbl(head,
                  'A problem has been detected and Windows has been shut '
                  'down to prevent damage to your video.',
                  fg=C_BSOD_FG, font=MONO_B).pack(fill='x')
        _wrap_lbl(head,
                  'FORMULA_EFFECT_EDITOR :: user-defined NumPy expression',
                  fg=C_BSOD_ACCENT, font=MONO_B).pack(fill='x', pady=(6, 0))
        _wrap_lbl(head,
                  'Type a NumPy expression returning an HxWx3 uint8 frame. '
                  'No NumPy experience needed — start from a snippet, '
                  'then poke values. Hover any [?] for a hint.\n'
                  'Введите NumPy-выражение, возвращающее кадр HxWx3 uint8. '
                  'NumPy знать не обязательно — начните со сниппета и '
                  'крутите цифры. Наведите на [?] для подсказки.',
                  fg=C_BSOD_DIM, font=MONO_S).pack(fill='x', pady=(2, 0))

        # ── Строка управления (enable / chance / blend) ────────────────
        ctl = tk.Frame(inner, bg=C_BSOD_BG)
        ctl.pack(fill='x', padx=12, pady=(8, 4))

        en_lbl = self._bsod_label(ctl, '[ENABLE]', fg=C_BSOD_ACCENT, font=MONO)
        en_lbl.pack(side='left')
        en_cb = tk.Checkbutton(
            ctl, variable=self.vars['fx_formula'],
            bg=C_BSOD_BG, fg=C_BSOD_FG, selectcolor=C_BSOD_BG,
            activebackground=C_BSOD_BG, activeforeground=C_BSOD_FG,
            highlightthickness=0, bd=0)
        en_cb.pack(side='left', padx=(2, 14))
        en_tip = ('Enable the formula effect. When off, the expression is '
                  'ignored even if it compiles cleanly.\n'
                  'Включить эффект-формулу. Если выключено — выражение '
                  'игнорируется, даже если компилируется без ошибок.')
        for w in (en_lbl, en_cb):
            self._bsod_tip(w, en_tip)

        for label, key, tip in [
            ('chance', 'fx_formula_chance',
             'Probability of firing on each frame (0..1). 0 = never, 1 = '
             'every frame. Internally also scaled by Global Chaos.\n'
             'Вероятность срабатывания на каждом кадре (0..1). 0 — никогда, '
             '1 — каждый кадр. Внутри ещё масштабируется Global Chaos.'),
            ('blend', 'fx_formula_blend',
             'Mix between formula output (0) and original frame (1). '
             '0 = pure formula, 1 = effect invisible.\n'
             'Смесь между выходом формулы (0) и оригинальным кадром (1). '
             '0 — чистая формула, 1 — эффект невидим.'),
        ]:
            lbl_w = self._bsod_label(ctl, label, fg=C_BSOD_ACCENT, font=MONO)
            lbl_w.pack(side='left', padx=(8, 2))
            sc = self._bsod_slider(ctl, key, 0.0, 1.0, length=140)
            self._bsod_tip(lbl_w, tip)
            self._bsod_tip(sc, tip)
            if key in self._display_vars:
                v = tk.Label(ctl, textvariable=self._display_vars[key],
                             bg=C_BSOD_BG, fg=C_BSOD_FG, font=MONO_S,
                             width=5, anchor='w')
                v.pack(side='left', padx=(2, 0))

        # ── Блок редактора ────────────────────────────────────────────
        ed_outer = tk.Frame(inner, bg=C_BSOD_FG, bd=0)
        ed_outer.pack(fill='x', padx=12, pady=(6, 0))
        ed_head = tk.Frame(ed_outer, bg=C_BSOD_FG)
        ed_head.pack(fill='x')
        ed_h_lbl = tk.Label(ed_head, text=' EDITOR ', bg=C_BSOD_FG,
                            fg=C_BSOD_BG,
                            font=('Consolas', 9, 'bold'))
        ed_h_lbl.pack(side='left', padx=4, pady=1)
        ed_help = tk.Label(ed_head, text='[?]', bg=C_BSOD_FG, fg=C_BSOD_BG,
                           font=('Consolas', 9, 'bold'),
                           cursor='question_arrow')
        ed_help.pack(side='left', padx=(2, 0))
        ed_tip = (
            'Type any Python expression that returns the next frame. '
            'Available names: frame, r, g, b, x, y, t, i, a, b, c, d, np, '
            'cv2. Errors silently fall back to the source frame, so a '
            'typo never crashes the render.\n'
            'Введите любое Python-выражение, которое возвращает следующий '
            'кадр. Доступно: frame, r, g, b, x, y, t, i, a, b, c, d, np, '
            'cv2. При ошибке возвращается оригинальный кадр — опечатка не '
            'падает рендер.')
        for w in (ed_h_lbl, ed_help):
            self._bsod_tip(w, ed_tip)

        ed_inner = tk.Frame(ed_outer, bg=C_BSOD_BG)
        ed_inner.pack(fill='x', padx=1, pady=1)
        self.formula_text = tk.Text(
            ed_inner, height=8, font=('Consolas', 11),
            bg=C_BSOD_BG, fg=C_BSOD_FG, insertbackground=C_BSOD_FG,
            selectbackground=C_BSOD_FG, selectforeground=C_BSOD_BG,
            bd=0, relief='flat', wrap='word', undo=True)
        self.formula_text.pack(side='left', fill='both', expand=True,
                               padx=4, pady=4)
        initial = self.vars['fx_formula_expr'].get() or 'frame'
        self.formula_text.insert('1.0', initial)
        self.formula_text.bind('<KeyRelease>', self._on_formula_text_changed)
        self.formula_text.bind('<<Modified>>', self._on_formula_text_changed)

        # ── Строка статуса ──────────────────────────────────────────
        self.formula_status_var = tk.StringVar(value='')
        status = tk.Frame(inner, bg=C_BSOD_BG)
        status.pack(fill='x', padx=12, pady=(4, 6))
        tk.Label(status, text='>>> ', bg=C_BSOD_BG, fg=C_BSOD_ACCENT,
                 font=MONO_B).pack(side='left')
        self.formula_status_label = tk.Label(
            status, textvariable=self.formula_status_var,
            bg=C_BSOD_BG, fg=C_BSOD_FG, font=MONO, anchor='w',
            justify='left', wraplength=400)
        wrap_labels.append(self.formula_status_label)
        self.formula_status_label.pack(side='left', fill='x', expand=True)
        self._update_formula_status()

        # ── Живые параметры a/b/c/d в сетке 2x2 ───────────────────────
        pbox = tk.Frame(inner, bg=C_BSOD_BG)
        pbox.pack(fill='x', padx=12, pady=(2, 4))
        ph = tk.Frame(pbox, bg=C_BSOD_BG)
        ph.pack(fill='x')
        self._bsod_label(ph, '[ LIVE PARAMS — referenced as a, b, c, d ]',
                         fg=C_BSOD_ACCENT, font=MONO).pack(side='left')
        ph_help = tk.Label(ph, text='[?]', bg=C_BSOD_BG, fg=C_BSOD_ACCENT,
                           font=MONO_S, cursor='question_arrow')
        ph_help.pack(side='left', padx=(4, 0))
        self._bsod_tip(ph_help,
            'Four free sliders (0..1) you can wire into the expression. '
            'Use them as "knobs" — `a` could control speed, `b` size, etc.\n'
            'Четыре свободных слайдера (0..1), которые можно использовать '
            'в выражении. Используйте как «ручки» — `a` для скорости, '
            '`b` для размера и т.д.')

        grid = tk.Frame(pbox, bg=C_BSOD_BG)
        grid.pack(fill='x')
        # Короткие двуязычные подсказки по каждой букве.
        param_tips = {
            'fx_formula_a': 'Free knob #1. Common idiom: amplitude.\n'
                            'Свободная ручка №1. Часто — амплитуда.',
            'fx_formula_b': 'Free knob #2. Common idiom: speed/frequency.\n'
                            'Свободная ручка №2. Часто — скорость/частота.',
            'fx_formula_c': 'Free knob #3. Common idiom: threshold.\n'
                            'Свободная ручка №3. Часто — порог.',
            'fx_formula_d': 'Free knob #4. Common idiom: blend/mix.\n'
                            'Свободная ручка №4. Часто — микс/блендинг.',
        }
        for idx, (letter, key) in enumerate([
                ('a', 'fx_formula_a'), ('b', 'fx_formula_b'),
                ('c', 'fx_formula_c'), ('d', 'fx_formula_d')]):
            r, c = divmod(idx, 2)
            cell = tk.Frame(grid, bg=C_BSOD_BG)
            cell.grid(row=r, column=c, sticky='ew', padx=8, pady=2)
            grid.grid_columnconfigure(c, weight=1)
            ll = self._bsod_label(cell, f' {letter} ', fg=C_BSOD_ACCENT,
                                  font=MONO_B)
            ll.pack(side='left')
            sc = self._bsod_slider(cell, key, 0.0, 1.0, length=160)
            self._bsod_tip(ll, param_tips[key])
            self._bsod_tip(sc, param_tips[key])
            if key in self._display_vars:
                tk.Label(cell, textvariable=self._display_vars[key],
                         bg=C_BSOD_BG, fg=C_BSOD_FG, font=MONO_S,
                         width=5, anchor='w').pack(side='left', padx=(4, 0))

        # ── Сниппеты - у каждого двуязычная подсказка ────────────
        sn = tk.Frame(inner, bg=C_BSOD_BG)
        sn.pack(fill='x', padx=12, pady=(8, 4))
        sn_head = tk.Frame(sn, bg=C_BSOD_BG)
        sn_head.pack(fill='x')
        self._bsod_label(sn_head, '[ SNIPPETS — click to load ]',
                         fg=C_BSOD_ACCENT, font=MONO).pack(side='left')
        snip_help = tk.Label(sn_head, text='[?]', bg=C_BSOD_BG,
                             fg=C_BSOD_ACCENT, font=MONO_S,
                             cursor='question_arrow')
        snip_help.pack(side='left', padx=(4, 0))
        self._bsod_tip(snip_help,
            "Each button replaces the editor with a working example. "
            "Click one, then tweak — that's the recommended way to learn "
            "the syntax without reading any NumPy docs.\n"
            "Каждая кнопка заменяет редактор готовым примером. Нажмите, "
            "потом поменяйте цифры — рекомендованный способ освоиться "
            "без чтения документации NumPy.")

        # Простые описания для каждого сниппета (EN + RU). Индекс совпадает
        # с FORMULA_SNIPPETS; порядок задан там.
        snippet_tips = [
            ('Pass-through. Use as a base when you want to blend a small '
             'change on top of the original frame.\n'
             'Пасс-через. База, когда хочется чуть-чуть изменить кадр.'),
            ('Photographic negative — every colour flipped (255 - value).\n'
             'Фотонегатив — каждый цвет инвертирован (255 - value).'),
            ('Brightness pulses with time. Speed grows with `a`.\n'
             'Яркость пульсирует со временем. Скорость зависит от `a`.'),
            ('Red channel slides right, blue slides left. Magnitude '
             'driven by `a`. Bigger `a` → wider chromatic split.\n'
             'Красный канал сдвигается вправо, синий — влево. Размер '
             'сдвига зависит от `a`. Больше — шире цветной разрыв.'),
            ('Reduces colour depth — chunky retro palette. `a` controls '
             'how aggressive the chunking is.\n'
             'Снижает цветовую глубину — крупная ретро-палитра. `a` '
             'управляет агрессивностью.'),
            ('Every other row darkened. CRT-monitor style.\n'
             'Каждая вторая строка затемнена. Стиль CRT-монитора.'),
            ('Sinusoidal vertical wave moves through the frame. `a` = '
             'amplitude.\n'
             'Синусоидальная вертикальная волна. `a` — амплитуда.'),
            ('Demoscene plasma overlay — colourful waves. `a` controls '
             'intensity.\n'
             'Demoscene-плазма поверх кадра — цветные волны. `a` — '
             'интенсивность.'),
            ('Binary threshold — pixels become pure black or pure white. '
             'Threshold level oscillates with time, modulated by `a`.\n'
             'Бинарный порог — пиксели становятся чисто чёрными или белыми. '
             'Уровень порога осциллирует во времени, модуляция — `a`.'),
            ('Left half of the frame is mirrored on the right. Cheap, '
             'classy psychedelic look.\n'
             'Левая половина кадра отражена на правую. Дешёвый и '
             'эффектный психоделический приём.'),
        ]
        sg = tk.Frame(sn, bg=C_BSOD_BG)
        sg.pack(fill='x', pady=2)
        # По умолчанию 5 колонок; внутренний фрейм отслеживает ширину,
        # поэтому на узких окнах ряд просто переносится через скролл канвы.
        for i, (lbl, expr) in enumerate(self.FORMULA_SNIPPETS):
            r, c = divmod(i, 5)
            btn = tk.Button(
                sg, text=lbl,
                command=lambda e=expr: self._formula_load_snippet(e),
                bg=C_BSOD_HL, fg=C_BSOD_FG,
                activebackground=C_BSOD_FG, activeforeground=C_BSOD_BG,
                font=MONO_S, bd=1, relief='solid',
                highlightthickness=0, cursor='hand2', pady=1)
            btn.grid(row=r, column=c, padx=3, pady=3, sticky='ew')
            sg.grid_columnconfigure(c, weight=1)
            if i < len(snippet_tips):
                self._bsod_tip(btn, snippet_tips[i])

        # ── Справка - двуязычная шпаргалка ─────────────────────
        ref = tk.Frame(inner, bg=C_BSOD_BG)
        ref.pack(fill='x', padx=12, pady=(8, 4))
        self._bsod_label(ref, '[ REFERENCE / СПРАВКА ]',
                         fg=C_BSOD_ACCENT, font=MONO).pack(anchor='w')
        # Строки должны быть короче ~40 моноширинных символов, чтобы
        # помещаться даже на панели шириной 600px, иначе текст вылезает
        # за правый край на узких окнах. Поэтому: короткий заголовок +
        # значение в строке, а комментарий - строкой ниже примера.
        ref_text = (
            'EN ── available variables ──\n'
            '  frame    : (H, W, 3) uint8\n'
            '  r, g, b  : (H, W) uint8 channels\n'
            '  x, y     : (H, W) float32 grids\n'
            '  t        : segment time, sec\n'
            '  i        : intensity 0..1\n'
            '  a,b,c,d  : live sliders 0..1\n'
            '  np, cv2  : NumPy + OpenCV\n'
            '\n'
            'RU ── доступные переменные ──\n'
            '  frame    : (H, W, 3) uint8\n'
            '  r, g, b  : (H, W) uint8 — каналы\n'
            '  x, y     : (H, W) float32 — сетки\n'
            '  t        : время сегмента, сек\n'
            '  i        : интенсивность 0..1\n'
            '  a,b,c,d  : live-слайдеры 0..1\n'
            '  np, cv2  : NumPy и OpenCV\n'
            '\n'
            'Examples / примеры:\n'
            '  255 - frame\n'
            '      # invert / инверт\n'
            '  np.roll(frame, int(20 * a), 1)\n'
            '      # h-slide / горизонт. сдвиг\n'
            '  cv2.GaussianBlur(frame, (15,15), 0)\n'
            '      # blur / размытие'
        )
        ref_lbl = tk.Label(ref, text=ref_text, bg=C_BSOD_BG, fg=C_BSOD_DIM,
                           font=MONO_S, justify='left', anchor='w')
        ref_lbl.pack(anchor='w', padx=4, pady=(2, 0))

        # ── Подвал ────────────────────────────────────────────────
        ft = tk.Frame(inner, bg=C_BSOD_BG)
        ft.pack(fill='x', padx=12, pady=(8, 14))
        _wrap_lbl(ft,
                  'Save the formula via Preset — expression + a/b/c/d are '
                  'stored in the preset config.\n'
                  'Чтобы сохранить формулу, сохраните Preset — выражение '
                  'и a/b/c/d попадут в конфиг пресета.',
                  fg=C_BSOD_DIM, font=MONO_S).pack(fill='x')

    def _bsod_slider(self, parent, key, lo, hi, length=160):
        """ttk.Scale с привязанным click-jump - для вкладки формул BSOD."""
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=self.vars[key],
                       orient=tk.HORIZONTAL, length=length)
        sc.pack(side='left', padx=4)
        self._bind_scale_click_jump(sc)
        return sc

    # Обёртка для обратной совместимости - старый код ещё ссылается на _tui_slider.
    def _tui_slider(self, parent, key, lo, hi, length=160):
        return self._bsod_slider(parent, key, lo, hi, length=length)

    def _on_formula_text_changed(self, _e=None):
        text = self.formula_text.get('1.0', 'end-1c')
        # Сохраняем в StringVar, чтобы get_current_config это видел
        try:
            self.vars['fx_formula_expr'].set(text)
        except Exception:
            pass
        try:
            self.formula_text.edit_modified(False)
        except tk.TclError:
            pass
        self._update_formula_status()

    def _update_formula_status(self):
        text = self.vars['fx_formula_expr'].get() or 'frame'
        _code, err = compile_formula(text)
        if err is None:
            self.formula_status_var.set('OK  compiled clean — formula ready')
            self.formula_status_label.configure(fg=C_BSOD_FG)
        else:
            self.formula_status_var.set(err)
            self.formula_status_label.configure(fg=C_BSOD_RED)

    def _formula_load_snippet(self, expr: str):
        self.formula_text.delete('1.0', 'end')
        self.formula_text.insert('1.0', expr)
        self._on_formula_text_changed()

    def _sync_formula_editor_from_var(self):
        """Переносит StringVar в Text-виджет - используется после загрузки пресета."""
        if not hasattr(self, 'formula_text'):
            return
        text = self.vars['fx_formula_expr'].get() or 'frame'
        self.formula_text.delete('1.0', 'end')
        self.formula_text.insert('1.0', text)
        self._update_formula_status()

    # ─── mystery panel ───
    def _build_mystery_panel(self, parent):
        for w in parent.winfo_children():
            w.destroy()
        wr = tk.Frame(parent, bg=C_SILVER)
        wr.pack(fill='both', expand=True, padx=4, pady=4)
        tk.Label(wr, text='[ UNKNOWN PARAMETERS — USE WITH CAUTION ]',
                 bg=C_SILVER, fg=C_DARK_GRAY,
                 font=('Courier New', 8, 'italic')).pack(pady=(8, 6), padx=10, anchor='w')
        # Сопоставляем ручку → уникальную always-подпись: порядок в
        # MYSTERY_KNOBS остаётся источником истины для раскладки, а
        # always-имена живут рядом с флагами движка в vpc/mystery.py.
        always_label_by_key = dict(MYSTERY_ALWAYS_LABELS)
        for label, key in MYSTERY_KNOBS:
            self._row_with_help(wr, label, '?', mono=True)
            self._slider(wr, key, 0.0, 1.0)
            # `key` имеет вид `mystery_<KNOB>` - выводим имя ручки движка и
            # ищем её криптическую always-подпись. Если у ручки вдруг нет
            # подписи, молча пропускаем (не должно происходить - есть тесты).
            knob = key[len('mystery_'):]
            always_text = always_label_by_key.get(knob)
            if always_text is None:
                continue
            arow = tk.Frame(wr, bg=C_SILVER)
            arow.pack(fill='x', padx=(20, 8), pady=(0, 6))
            tk.Checkbutton(arow, text=always_text,
                           variable=self.vars[f'always_mystery_{knob}'],
                           bg=C_SILVER, fg='#3A3A60',
                           activebackground=C_SILVER,
                           selectcolor=C_SILVER,
                           font=('Courier New', 8, 'bold'),
                           padx=0, pady=0, bd=0,
                           highlightthickness=0).pack(side='left')

    # ─── выбор файлов ───
    def sel_audio(self):
        p = filedialog.askopenfilename(filetypes=[('Audio', '*.mp3 *.wav')])
        if p:
            self.audio_path = p
            self.lbl_audio_name.configure(
                text=self._shorten_name(os.path.basename(p)))
            Tooltip(self.lbl_audio_name, p)
            self._audio_dot.configure(fg=C_GREEN_DOT)

    def sel_video(self):
        from vpc.render.source import IMAGE_EXTS
        _vid = '*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.mpg *.mpeg'
        _img = ' '.join(sorted('*' + e for e in IMAGE_EXTS))
        ps = filedialog.askopenfilenames(
            title='Select Source(s) — video or photo',
            filetypes=[('Media (video + photo)', f'{_vid} {_img}'),
                       ('Video', _vid),
                       ('Images', _img),
                       ('All files', '*.*')])
        if ps:
            self.video_paths = list(ps)
            n = len(self.video_paths)
            label_text = (self._shorten_name(os.path.basename(self.video_paths[0]))
                          if n == 1 else f'{n} files loaded')
            self.lbl_video_name.configure(text=label_text)
            tip = self.video_paths[0] if n == 1 else '\n'.join(self.video_paths)
            Tooltip(self.lbl_video_name, tip)
            self._video_dot.configure(fg=C_GREEN_DOT)

    def sel_ov(self):
        p = filedialog.askdirectory()
        if p:
            self.overlay_dir = p
            self.lbl_overlay_dir.configure(text=os.path.basename(p))

    # ─── drag-and-drop ───
    def _setup_dnd(self):
        """Включает загрузку файлов перетаскиванием через tkinterdnd2 (опционально).

        Деградирует полностью: если пакета или его нативного tkdnd-бинарника
        нет (например, сборка без него), приложение работает как раньше, файлы
        загружаются через обычные кнопки. Зоны сброса - слоты видео/аудио/
        оверлея, но файл, брошенный на любую из них, классифицируется по
        расширению, так что точная цель не важна.
        """
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
            # Загружаем Tcl-пакет tkdnd в этот (обычный tk.Tk) интерпретатор.
            TkinterDnD._require(self)
        except Exception:
            return

        targets = []
        for name in ('lbl_video_name', 'lbl_audio_name', 'lbl_overlay_dir'):
            w = getattr(self, name, None)
            if w is None:
                continue
            # Регистрируем и строку (более крупную цель), и саму метку.
            for tgt in (getattr(w, 'master', None), w):
                if tgt is not None and tgt not in targets:
                    targets.append(tgt)
        for tgt in targets:
            try:
                tgt.drop_target_register(DND_FILES)
                tgt.dnd_bind('<<Drop>>', self._on_dnd_drop)
            except Exception:
                pass

    def _on_dnd_drop(self, event):
        try:
            paths = list(self.tk.splitlist(event.data))
        except Exception:
            paths = [event.data] if getattr(event, 'data', None) else []
        self._handle_dropped_paths([p for p in paths if p])
        return getattr(event, 'action', 'copy')

    def _handle_dropped_paths(self, paths):
        """Классифицирует брошенные пути по расширению и раскладывает по слотам,
        повторяя sel_video / sel_audio / sel_ov (метки, тултипы, статус-точки)."""
        try:
            from vpc.render.source import IMAGE_EXTS
            img_exts = {e.lower() for e in IMAGE_EXTS}
        except Exception:
            img_exts = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tga', '.webp'}
        vid_exts = {'.mp4', '.mov', '.mkv', '.avi', '.wmv', '.flv',
                    '.mpg', '.mpeg', '.webm', '.m4v'}
        aud_exts = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac'}

        new_vids, new_audio, new_folder = [], None, None
        for p in paths:
            if os.path.isdir(p):
                new_folder = p
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext in vid_exts or ext in img_exts:
                new_vids.append(p)
            elif ext in aud_exts:
                new_audio = p

        if new_vids:
            merged = list(self.video_paths)
            for v in new_vids:
                if v not in merged:
                    merged.append(v)
            self.video_paths = merged
            n = len(self.video_paths)
            label_text = (self._shorten_name(os.path.basename(self.video_paths[0]))
                          if n == 1 else f'{n} files loaded')
            self.lbl_video_name.configure(text=label_text)
            Tooltip(self.lbl_video_name,
                    self.video_paths[0] if n == 1 else '\n'.join(self.video_paths))
            self._video_dot.configure(fg=C_GREEN_DOT)

        if new_audio:
            self.audio_path = new_audio
            self.lbl_audio_name.configure(
                text=self._shorten_name(os.path.basename(new_audio)))
            Tooltip(self.lbl_audio_name, new_audio)
            self._audio_dot.configure(fg=C_GREEN_DOT)

        if new_folder:
            self.overlay_dir = new_folder
            self.lbl_overlay_dir.configure(text=os.path.basename(new_folder))

    # ─── конфиг + I/O пресетов ───
    def get_current_config(self):
        cfg = {}
        for name, var in self.vars.items():
            try:
                cfg[name] = var.get()
            except Exception:
                cfg[name] = self._defaults_all.get(name)

        cfg['scene_buffer_size'] = int(cfg.get('scene_buffer_size', 10))
        cfg['fps'] = int(cfg.get('fps', 24))
        cfg['crf'] = int(cfg.get('crf', 22))
        cfg['fx_ascii_size'] = int(cfg.get('fx_ascii_size', 12))
        cfg['custom_w'] = int(cfg.get('custom_w', 1280))
        cfg['custom_h'] = int(cfg.get('custom_h', 720))

        cfg['resolution'] = self.res_combo.get() if hasattr(self, 'res_combo') else '720p'
        cfg['resolution_mode'] = self.var_resolution_mode.get()
        cfg['export_preset'] = self.preset_enc_combo.get() if hasattr(self, 'preset_enc_combo') else 'medium'
        cfg['video_codec'] = self.fmt_combo.get() if hasattr(self, 'fmt_combo') else 'H.264 (MP4)'
        cfg['tune'] = self.var_tune.get() if hasattr(self, 'var_tune') else 'none'
        cfg['quality_preset'] = (self.var_quality_preset.get()
                                 if hasattr(self, 'var_quality_preset') else 'Custom')
        cfg['silence_mode'] = self.var_silence_mode.get()

        cfg['fx_ascii_fg'] = [int(cfg.pop('fx_ascii_fg_r', 0)),
                              int(cfg.pop('fx_ascii_fg_g', 255)),
                              int(cfg.pop('fx_ascii_fg_b', 0))]
        cfg['fx_ascii_bg'] = [int(cfg.pop('fx_ascii_bg_r', 0)),
                              int(cfg.pop('fx_ascii_bg_g', 0)),
                              int(cfg.pop('fx_ascii_bg_b', 0))]
        cfg['fx_overlay_ck_color'] = [int(cfg.pop('fx_overlay_ck_r', 0)),
                                       int(cfg.pop('fx_overlay_ck_g', 255)),
                                       int(cfg.pop('fx_overlay_ck_b', 0))]

        _mystery_keys = ('VESSEL', 'ENTROPY_7', 'DELTA_OMEGA',
                         'STATIC_MIND', 'RESONANCE', 'COLLAPSE',
                         'ZERO', 'FLESH_K', 'DOT')
        cfg['mystery'] = {k: float(cfg.pop(f'mystery_{k}', 0.0))
                          for k in _mystery_keys}
        cfg['mystery_always'] = {k: bool(cfg.pop(f'always_mystery_{k}', False))
                                 for k in _mystery_keys}
        return cfg

    def apply_preset(self, name):
        # Пропускаем запись, если значение уже совпадает - каждый var.set
        # вызывает все зарегистрированные trace (переключение вложенных
        # блоков эффекта, каскадное затенение зависимостей, обновление
        # скролла), поэтому лишние записи заставляли дважды обходить
        # каждый блок эффекта без видимых изменений.
        def _set_if_changed(var, new_val):
            try:
                if var.get() != new_val:
                    var.set(new_val)
            except Exception:
                try: var.set(new_val)
                except Exception: pass

        # Сначала сброс к значениям по умолчанию
        for k, v in self._defaults_all.items():
            if k in self.vars:
                _set_if_changed(self.vars[k], v)
        # Пустой пресет = всё выключено + silence none.
        if name == EMPTY_PRESET_NAME:
            for spec in EFFECTS:
                if spec.enable_key in self.vars:
                    _set_if_changed(self.vars[spec.enable_key], False)
        _set_if_changed(self.var_silence_mode, 'none')
        _set_if_changed(self.var_resolution_mode, 'preset')
        # Применяем переопределения
        for key, val in PRESETS.get(name, {}).items():
            if key in self.vars:
                _set_if_changed(self.vars[key], val)
        self._sync_formula_editor_from_var()
        self.log(f"Preset '{name}' loaded.")

    @property
    def _PRESETS_PATH(self) -> str:
        return str(presets_path())

    def _load_presets_file(self):
        if not os.path.exists(self._PRESETS_PATH):
            self._generate_builtin_presets_file()
        else:
            try:
                with open(self._PRESETS_PATH, 'r', encoding='utf-8') as f:
                    self._user_presets = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.log('presets.json corrupt - regenerating')
                self._generate_builtin_presets_file()
                self._refresh_presets_listbox()
                if self._user_presets:
                    self._presets_listbox.selection_set(0)
                    self._load_selected_preset()
                return

            # Миграция: убираем старые встроенные пресеты, но сохраняем
            # пользовательские. Если после чистки не осталось Empty-пресета,
            # пересоздаём файл только с ним.
            kept = [p for p in self._user_presets if not p.get('builtin')]
            had_old_builtins = len(kept) != len(self._user_presets)
            has_empty = any(p.get('name') == EMPTY_PRESET_NAME for p in kept)
            if had_old_builtins:
                self.log('Dropped old built-in presets; user presets kept.')
            if not has_empty:
                # Собираем канонический Empty-пресет прямо здесь.
                self.res_combo.set('720p')
                self.preset_enc_combo.set('medium')
                self.apply_preset(EMPTY_PRESET_NAME)
                cfg = self.get_current_config()
                kept.insert(0, {'name': EMPTY_PRESET_NAME,
                                'builtin': True, 'config': cfg})
            self._user_presets = kept
            if had_old_builtins or not has_empty:
                self._save_presets_file()
        self._refresh_presets_listbox()
        if self._user_presets:
            self._presets_listbox.selection_set(0)
            self._load_selected_preset()

    def _generate_builtin_presets_file(self):
        # Теперь генерирует ТОЛЬКО Empty-пресет. Пользовательские пресеты
        # добавляются через _save_current_preset.
        self._user_presets = []
        self.res_combo.set('720p')
        self.preset_enc_combo.set('medium')
        self.apply_preset(EMPTY_PRESET_NAME)
        cfg = self.get_current_config()
        self._user_presets.append({'name': EMPTY_PRESET_NAME,
                                   'builtin': True, 'config': cfg})
        self._save_presets_file()

    def _save_presets_file(self):
        try:
            with open(self._PRESETS_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._user_presets, f, indent=2)
        except OSError as e:
            self.log(f'ERROR: could not save presets.json - {e}')

    def _refresh_presets_listbox(self):
        self._presets_listbox.delete(0, tk.END)
        for entry in self._user_presets:
            disp = f"[B] {entry['name']}" if entry.get('builtin') else entry['name']
            self._presets_listbox.insert(tk.END, disp)

    def apply_preset_config(self, cfg, name):
        # СНАЧАЛА сбрасываем все переменные к дефолтам реестра, потом
        # накладываем сохранённый конфиг. Без этого любой ключ, которого
        # нет в `cfg`, сохраняет то, что сейчас в UI - так пресет, сохранённый
        # старой сборкой (без новых ключей fx_viz_*/_drive/_gate/_react),
        # незаметно оставлял бы включёнными старые эффекты и старую
        # разводку audio-drive/gate. Это тот самый класс багов "появляются
        # эффекты, которые я не включал, а правки влияют не на то".
        # Встроенный `apply_preset` уже делает сброс первым; этот путь
        # должен ему соответствовать, чтобы сохранённый конфиг полностью
        # определял состояние.
        for k, v in self._defaults_all.items():
            if k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception:
                    pass
        for k, v in cfg.items():
            if k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception:
                    pass
        # Распаковка составных RGB
        for compkey, prefix in (('fx_ascii_fg', 'fx_ascii_fg_'),
                                ('fx_ascii_bg', 'fx_ascii_bg_'),
                                ('fx_overlay_ck_color', 'fx_overlay_ck_')):
            if compkey in cfg:
                vals = cfg[compkey]
                for letter, idx in (('r', 0), ('g', 1), ('b', 2)):
                    self.vars[prefix + letter].set(vals[idx])
        if 'mystery' in cfg:
            for k, v in cfg['mystery'].items():
                key = f'mystery_{k}'
                if key in self.vars:
                    self.vars[key].set(v)
        # Восстанавливаем флаги "always". Старые пресеты созданы до
        # появления этого поля, поэтому отсутствующие записи по умолчанию
        # False (поведение для совместимости).
        if 'mystery_always' in cfg:
            for k, v in cfg['mystery_always'].items():
                key = f'always_mystery_{k}'
                if key in self.vars:
                    self.vars[key].set(bool(v))
        self.var_silence_mode.set(cfg.get('silence_mode', 'none'))
        self.var_resolution_mode.set(cfg.get('resolution_mode', 'preset'))
        self._sync_formula_editor_from_var()
        self.res_combo.set(cfg.get('resolution', '720p'))
        self.preset_enc_combo.set(cfg.get('export_preset', 'medium'))
        if hasattr(self, 'fmt_combo') and cfg.get('video_codec'):
            self.fmt_combo.set(cfg['video_codec'])
        # Выпадающие списки Tune + Quality. Применяем tune из cfg (по
        # умолчанию 'none', если отсутствует - старые пресеты созданы до
        # появления этого поля). Затем доверяем сохранённой метке
        # quality_preset, если она есть, иначе выводим её заново из тройки
        # (crf, export_preset, tune). Применяем через guard, чтобы trace
        # не конфликтовали друг с другом.
        if hasattr(self, 'var_tune'):
            self._applying_quality = True
            try:
                self.var_tune.set(cfg.get('tune', 'none') or 'none')
            finally:
                self._applying_quality = False
        if hasattr(self, 'var_quality_preset'):
            saved_label = cfg.get('quality_preset')
            if saved_label and saved_label in QUALITY_PRESETS:
                self.var_quality_preset.set(saved_label)
            else:
                self._refresh_quality_label()
        self.log(f"Preset '{name}' loaded.")

    def _load_selected_preset(self):
        sel = self._presets_listbox.curselection()
        if not sel:
            return
        entry = self._user_presets[sel[0]]
        self.apply_preset_config(entry['config'], entry['name'])
        self._active_preset_label.configure(text=f"Active: {entry['name']}")

    def _save_current_preset(self):
        name = simpledialog.askstring('Save Preset', 'Preset name:', parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        names_lower = [p['name'].lower() for p in self._user_presets]
        if name.lower() in names_lower:
            if not messagebox.askyesno('Overwrite?', f"Preset '{name}' exists. Overwrite?",
                                       parent=self):
                return
            idx = names_lower.index(name.lower())
            self._user_presets.pop(idx)
        cfg = self.get_current_config()
        self._user_presets.append({'name': name, 'builtin': False, 'config': cfg})
        self._save_presets_file()
        self._refresh_presets_listbox()
        new_idx = len(self._user_presets) - 1
        self._presets_listbox.selection_clear(0, tk.END)
        self._presets_listbox.selection_set(new_idx)
        self._presets_listbox.see(new_idx)
        self._active_preset_label.configure(text=f'Active: {name}')

    def _delete_preset(self):
        sel = self._presets_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        name = self._user_presets[idx]['name']
        if not messagebox.askyesno('Delete Preset', f"Delete '{name}'?", parent=self):
            return
        self._user_presets.pop(idx)
        self._save_presets_file()
        self._refresh_presets_listbox()
        if self._user_presets:
            self._presets_listbox.selection_set(min(idx, len(self._user_presets) - 1))
        cur = self._active_preset_label.cget('text')
        if cur == f'Active: {name}':
            self._active_preset_label.configure(text='Active: —')

    # ─── помощники лога ───
    def _clear_log(self):
        self.console.delete('1.0', tk.END)

    def _copy_log(self):
        """Копирует весь лог статуса в буфер обмена."""
        text = self.console.get('1.0', tk.END).rstrip('\n')
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log('Log copied to clipboard.')

    def log(self, msg):
        self.console.insert(tk.END, f'[{time.strftime("%H:%M:%S")}] > {msg}\n')
        self.console.see(tk.END)

    # ─── подстройка FPS под источник ───
    def _fps_match_source(self):
        """Определяет родной FPS загруженного исходного видео и применяет его.

        Аккуратно ничего не делает (+ лог), если видео ещё не загружено или
        поток не открывается. Использует OpenCV напрямую, поэтому дёшево и
        не затрагивает путь рендера движка.
        """
        if not self.video_paths:
            self.log('Match FPS: no source video loaded yet.')
            return
        import cv2
        from vpc.render.source import is_image
        video_only = [p for p in self.video_paths if not is_image(p)]
        if not video_only:
            self.log('Match FPS: no video source loaded (photos have no FPS).')
            return
        src_path = video_only[0]
        cap = cv2.VideoCapture(src_path)
        try:
            if not cap.isOpened():
                self.log(f'Match FPS: could not open '
                         f'{os.path.basename(src_path)}.')
                return
            src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        finally:
            cap.release()
        if src_fps <= 0.0:
            self.log('Match FPS: source reports zero/unknown FPS.')
            return
        # Округляем до целого - конвейер экспорта ожидает int. В логе
        # показываем исходное значение, чтобы было видно, что округлилось.
        rounded = int(round(src_fps))
        self.vars['fps'].set(float(rounded))
        if hasattr(self, 'fps_combo'):
            self.fps_combo.set(str(rounded))
        self.log(f'Match FPS: source {src_fps:.3f} → output {rounded} fps.')

    # ─── рендер ───
    def _get_preview_seconds(self) -> float:
        """Читает длину превью из спинбокса, ограничивает диапазоном [1, 90].

        Изолирует движок от любого мусора, введённого пользователем в
        спинбокс (TclError у `.get()` на нечисловом вводе). При любой
        ошибке разбора откатывается на 5с, чтобы рендер всё равно был
        возможен.
        """
        try:
            v = float(self.var_preview_seconds.get())
        except (tk.TclError, ValueError, TypeError):
            v = 5.0
        return max(1.0, min(90.0, v))

    def run(self, mode='final'):
        passthrough = bool(self.vars.get('passthrough_mode')
                           and self.vars['passthrough_mode'].get())
        if not self.video_paths:
            self.log('ERROR: Select a source video!')
            return
        if not passthrough and not self.audio_path:
            self.log('ERROR: Select an audio file '
                     '(or enable Passthrough mode in Cut Logic).')
            return
        cfg = self.get_current_config()
        cfg['audio_path'] = self.audio_path
        cfg['video_paths'] = self.video_paths
        cfg['overlay_dir'] = self.overlay_dir

        if mode in ('draft', 'preview'):
            cfg['render_mode'] = mode
            # Draft - фиксированная проверка 5с/480p; только PREVIEW
            # учитывает заданную пользователем длину, чтобы роли не путались.
            preview_secs = (5.0 if mode == 'draft'
                            else self._get_preview_seconds())
            cfg['max_duration'] = preview_secs
            cfg['output_path'] = self.temp_preview_path
            label = ('DRAFT (5 sec · 480p)' if mode == 'draft'
                     else f'PREVIEW ({preview_secs:g} sec)')
            self.log(f'Starting {label}...')
            self.progress.configure(mode='indeterminate'); self.progress.start(10)
        else:
            cfg['render_mode'] = 'final'
            # Выводим расширение контейнера из выбранной метки кодека,
            # чтобы диалог сохранения и ffmpeg-сток сходились по типу файла.
            from vpc.render.sink import EXPORT_FORMATS
            fmt = EXPORT_FORMATS.get(cfg.get('video_codec', 'H.264 (MP4)'),
                                     EXPORT_FORMATS['H.264 (MP4)'])
            ext = fmt['ext']
            out = filedialog.asksaveasfilename(
                defaultextension=f'.{ext}',
                filetypes=[(ext.upper(), f'*.{ext}'), ('All files', '*.*')],
                initialfile=f'disc_{random.randint(1000, 9999)}.{ext}')
            if not out:
                return
            # Если пользователь ввёл другое расширение, доверяем, но предупреждаем.
            user_ext = os.path.splitext(out)[1].lower().lstrip('.')
            if user_ext and user_ext != ext:
                self.log(f'WARNING: extension .{user_ext} does not match codec '
                         f'container .{ext} - keeping user extension.')
            cfg['output_path'] = out
            cfg['max_duration'] = None
            self.log('Starting FULL RENDER...')
            self.progress.configure(mode='determinate', value=0)

        if not self.overlay_dir and cfg.get('fx_overlay'):
            self.log('WARNING: Overlay folder not set - overlay effect skipped.')

        for btn in (self.btn_draft, self.btn_preview, self.btn_run_full):
            btn.configure(state='disabled')
        # Кнопка Cancel работает наоборот: активна ровно во время рендера.
        self.btn_cancel_render.configure(state='normal', text='CANCEL RENDER')
        # Останавливаем воспроизведение перед рендером - рендер пишет в
        # temp_preview_path, а на Windows cv2.VideoCapture в цикле
        # воспроизведения держит файл открытым, из-за чего ffmpeg падает
        # или cv2 читает битые данные и падает с segfault на втором draft.
        if mode in ('draft', 'preview'):
            self.stop_and_clear_playback()
            # Windows 11 использует бэкенд Media Foundation (MSMF) для
            # cv2.VideoCapture. MSMF освобождает файловый хендл ОС
            # асинхронно через рабочие потоки MF ПОСЛЕ возврата
            # из cap.release(), поэтому даже после join потока
            # воспроизведения хендл может жить ещё ~100-200 мс.
            # ffmpeg нужен доступ GENERIC_WRITE для перезаписи
            # файла; если MSMF всё ещё держит его с FILE_SHARE_READ
            # (без разделения на запись), ffmpeg получает
            # ERROR_SHARING_VIOLATION и рендер молча падает или
            # крашится. Опрашиваем через CreateFile, пока файл не
            # станет доступен для записи.
            if sys.platform == 'win32' and os.path.exists(self.temp_preview_path):
                _wait_file_writable(self.temp_preview_path)
        threading.Thread(target=self._render_thread, args=(cfg,), daemon=True).start()

    def cancel_render(self):
        """Запрашивает кооперативную остановку текущего рендера.

        Движок проверяет `self.abort` на границах сегмента / кадра / пада
        (см. vpc/render/engine.py). Установка флага из потока GUI безопасна -
        CPython делает запись атрибута атомарной под GIL, а рабочий
        поток читает тот же атрибут.

        Замечание: определение сцен выполняется синхронно до цикла по
        сегментам и не имеет точки проверки abort, поэтому отмена
        может сработать только после этой фазы.
        """
        eng = self.engine
        if eng is not None and not eng.abort:
            eng.abort = True
            self.log('Cancel requested - finishing current frame and closing sink...')
            self.btn_cancel_render.configure(state='disabled', text='CANCELLING...')

    def _render_thread(self, cfg):
        is_preview = cfg['render_mode'] in ('draft', 'preview')

        def on_progress(message=None, value=None):
            if message:
                self.after(0, self.log, message)
            if not is_preview and value is not None:
                self.after(0, self.progress_var.set, value)

        engine = BreakcoreEngine(cfg, progress_callback=on_progress)
        # Публикуем ссылку на движок ДО engine.run(), чтобы быстрый
        # клик по Cancel между стартом потока и входом в run() всё равно сработал.
        self.engine = engine
        try:
            ok = engine.run(render_mode=cfg['render_mode'],
                            max_output_duration=cfg.get('max_duration'))
            label = 'PREVIEW' if is_preview else 'FULL RENDER'
            if engine.abort or not ok:
                # Не удаляем частичный результат автоматически - для
                # полного рендера путь выбирал сам пользователь и мог перезаписать
                # существующий файл. Просто логируем путь.
                self.after(0, self.log,
                           f"--- {label} CANCELLED. Partial output left at: "
                           f"{cfg['output_path']} ---")
            else:
                self.after(0, self.log,
                           f"--- {label} COMPLETE: {cfg['output_path']} ---")
                if is_preview:
                    self.after(0, self.start_playback, self.temp_preview_path)
        except Exception as e:
            self.after(0, self.log, f'ERROR: {e}')
        finally:
            self.engine = None
            for btn in (self.btn_draft, self.btn_preview, self.btn_run_full):
                self.after(0, lambda b=btn: b.configure(state='normal'))
            self.after(0, lambda: self.btn_cancel_render.configure(
                state='disabled', text='CANCEL RENDER'))
            self.after(0, self.progress.stop)
            self.after(0, self.progress_var.set, 0)
            if is_preview:
                self.after(0, self.progress.configure,
                           {'mode': 'determinate', 'value': 0})

    # ─── транспорт (плеер превью) ───
    def _apply_player_volume(self):
        """Передаёт эффективную (с учётом mute) громкость живому плееру."""
        if self._player is not None:
            self._player.set_volume(0.0 if self._muted else self._volume)

    def _on_volume_change(self):
        """Колбэк слайдера громкости - применяется к плееру на лету (аудио-
        колбэк читает gain на каждый блок). Движение слайдера также неявно
        снимает mute (как у любого другого медиаплеера)."""
        v = max(0.0, min(1.0, self.var_volume.get() / 100.0))
        if self._muted and v > 0.0:
            self._muted = False
            self.btn_mute.configure(text='MUTE')
        self._volume = v
        if not self._muted:
            self._volume_pre_mute = v
        self._apply_player_volume()

    def _toggle_pause(self):
        if self._player is None:
            return
        if self._player.toggle_pause():
            self.btn_pause.configure(text='PLAY')
        else:
            self.btn_pause.configure(text='PAUSE')

    def _toggle_mute(self):
        if self._muted:
            # Если пользователь вручную дотащил слайдер до 0 *перед*
            # нажатием MUTE, _volume_pre_mute равен 0, и UNMUTE молча
            # оставил бы слайдер на 0 (выглядит как баг). Ставим разумное
            # значение по умолчанию.
            restore = self._volume_pre_mute if self._volume_pre_mute > 0.01 else 0.8
            self._muted = False
            self._volume = restore
            # Установка var_volume вызывает _on_volume_change, который
            # заново применяет громкость к плееру.
            self.var_volume.set(restore * 100.0)
            self.btn_mute.configure(text='MUTE')
        else:
            self._volume_pre_mute = self._volume
            self._muted = True
            self._volume = 0.0
            self.btn_mute.configure(text='UNMUTE')
            self._apply_player_volume()

    # ─── воспроизведение ───
    # Задержка между stop_and_clear_playback() и повторным открытием
    # захвата / перезапуском потоков. Даёт ОС момент на освобождение
    # предыдущего файлового хендла temp preview перед повторным чтением
    # (особенно на Windows, где ещё замапленный файл может отказать в
    # новом открытии VideoCapture). Эмпирически 150мс достаточно.
    _PLAYBACK_RESTART_MS = 150

    def start_playback(self, path):
        """Двухфазный запуск. Фаза 1 (здесь, синхронно в Tk): останавливаем
        предыдущее воспроизведение и запрашиваем отключение управления.
        Фаза 2 идёт через `after()`, чтобы задержка на устакивание не
        замораживала поток GUI - раньше использовался `time.sleep(0.15)`,
        что заметно подтормаживало Tk на каждом старте превью.
        """
        self.stop_and_clear_playback()
        self.after(self._PLAYBACK_RESTART_MS,
                   lambda: self._start_playback_phase2(path))

    def _extract_preview_wav(self, path):
        """Извлекает аудио клипа во временный PCM wav для мастер-часов.

        soundfile не умеет читать mp4 напрямую, а плееру с аудио-мастерингом
        нужен сырой PCM. Возвращает путь к wav или None (беззвучное превью
        при ошибке)."""
        wav_fd, wav_path = tempfile.mkstemp(suffix='.wav')
        os.close(wav_fd)
        try:
            from vpc.render.sink import ffmpeg_bin
            _ff = ffmpeg_bin()
            subprocess.run(
                [_ff, '-y', '-i', path, '-vn', '-acodec', 'pcm_s16le',
                 '-ar', '44100', '-ac', '2', wav_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return wav_path
        except Exception as e:
            self.log(f'WARNING: audio extract failed ({e}); preview will be silent.')
            try: os.remove(wav_path)
            except OSError: pass
            return None

    def _start_playback_phase2(self, path):
        self._audio_wav = self._extract_preview_wav(path) if _AUDIO_OK else None
        self._player = PreviewPlayer(
            path, on_frame=self._present_frame, size=(640, 360),
            wav_path=self._audio_wav, log=self.log)
        self._player.set_volume(0.0 if self._muted else self._volume)
        if not self._player.start():
            self._player = None
            self._cleanup_preview_wav()
            return
        self.log('Playback started (audio-synced, looping).')
        # Включаем элементы транспорта. Mute/Volume имеют смысл только
        # если доступен аудио-бэкенд (sounddevice + soundfile).
        self.btn_pause.configure(state='normal', text='PAUSE')
        self.btn_clear_monitor.configure(state='normal')
        self.btn_mute.configure(state=('normal' if _AUDIO_OK else 'disabled'),
                                text='MUTE')

    def _present_frame(self, rgb):
        """Колбэк рабочего потока плеера: собирает Tk-изображение и
        передаёт его в поток GUI. Сборка PhotoImage вне потока Tk сохраняет
        отзывчивость интерфейса; в основном потоке выполняется только
        дешёвое присваивание виджету."""
        try:
            imgtk = ImageTk.PhotoImage(image=Image.fromarray(rgb))
        except Exception:
            return
        self.after(0, self._show_frame, imgtk)

    def _show_frame(self, imgtk):
        if self._player is None:
            return
        self.player_label.imgtk = imgtk
        self.player_label.configure(image=imgtk, text='')

    def _cleanup_preview_wav(self):
        wav = getattr(self, '_audio_wav', None)
        if wav and os.path.exists(wav):
            try: os.remove(wav)
            except OSError: pass
        self._audio_wav = None

    def stop_and_clear_playback(self):
        if self._player is not None:
            self._player.stop()
            self._player = None
        self._cleanup_preview_wav()
        # Сбрасываем состояние транспорта. _muted/_volume_pre_mute
        # намеренно сохраняются между превью (это настройка пользователя).
        self.btn_pause.configure(state='disabled', text='PAUSE')
        self.btn_mute.configure(state='disabled')
        self.btn_clear_monitor.configure(state='disabled')
        self.player_label.configure(image=None,
                                    text='Preview stopped / ready', bg=C_BLACK)

    def on_closing(self):
        self.stop_and_clear_playback()
        if os.path.exists(self.temp_preview_path):
            try: os.remove(self.temp_preview_path)
            except OSError: pass
        self.destroy()


if __name__ == '__main__':
    app = MainGUI()
    app.protocol('WM_DELETE_WINDOW', app.on_closing)
    app.mainloop()
