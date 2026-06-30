"""Disc VPC 01 — Tk GUI generated from the effect registry.

Sections, sliders, checkboxes, tooltips, and the cfg dict are all derived from
`registry.EFFECTS`. Adding a new effect to the registry makes it appear in the
GUI automatically — no changes here required.

Backlog support:
  * Tooltip / [?] popup on every label and slider (item #4) — uses the
    `tooltip` field on EffectSpec / ParamSpec.
  * Per-effect "always-on" override (item #1) — each effect's accordion block
    has an `always` checkbox + intensity slider that bypasses its triggers.
  * Resolution mode: preset / source / custom (item #2).
  * Formula effect block (item #3) — same registry mechanism, with a free-form
    Entry widget for the expression.
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
from vpc.registry import ACCORDION_HIDDEN_GROUPS
from vpc.mystery import MYSTERY_KNOBS, MYSTERY_ALWAYS_LABELS
from vpc.effects.formula import compile_formula
from vpc.paths import presets_path, temp_preview_path

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
    """Poll until `path` can be opened for exclusive write, or `timeout` seconds pass.

    On Windows 11, cv2.VideoCapture uses the Media Foundation (MSMF) backend
    which releases the underlying OS file handle asynchronously via MF worker
    threads after cap.release() is called. This means the handle can remain
    alive for ~100-200 ms after the Python thread has exited and join()
    returned. ffmpeg needs GENERIC_WRITE access (no read sharing) to overwrite
    the file; while MSMF still holds it with FILE_SHARE_READ, that open fails
    with ERROR_SHARING_VIOLATION. This function retries every 50 ms using the
    Win32 CreateFile API with dwShareMode=0 (exclusive), which is the most
    conservative test: if it succeeds, ffmpeg will too.
    """
    if sys.platform != 'win32':
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    # Set restype explicitly so Python gets a signed 32-bit int back.
    # Without this, ctypes defaults to c_int anyway, but being explicit
    # avoids the comparison trap: INVALID_HANDLE_VALUE as c_int is -1,
    # while ctypes.c_void_p(-1).value on 64-bit is 0xFFFFFFFFFFFFFFFF —
    # they are not equal as Python ints even though they represent the same
    # handle, which would make the check silently wrong.
    k32.CreateFileW.restype = ctypes.c_long  # 32-bit signed; INVALID_HANDLE_VALUE → -1
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

# ── Win95 colours ──
C_SILVER = '#C0C0C0'
C_DARK_GRAY = '#808080'
C_BLACK = '#000000'
C_WHITE = '#FFFFFF'
C_TITLE_BAR = '#000080'
C_TEXT = '#000000'
C_BLUE_LIGHT = '#D0D8F0'
C_GREEN_DOT = '#00AA00'
C_RED_BTN = '#CC2222'

# ── TUI palette (FORMULA tab) ──
C_TUI_BG = '#0A1208'           # deep terminal background
C_TUI_FG = '#39FF14'           # phosphor green
C_TUI_DIM = '#1F8C0E'          # dim green for separators / labels
C_TUI_AMBER = '#FFB000'        # amber for headings / values
C_TUI_RED = '#FF5555'          # error red
C_TUI_HL = '#0F1F0A'           # subtle highlight row

# ── Built-in presets ─────────────────────────────────────────────────────
# Only one entry: Empty. All effects off, all sliders at 0, mystery zeroed,
# silence_mode = none. Anything beyond that is filled in from default_cfg().
# Custom presets the user makes through the UI live alongside this entry in
# presets.json (with builtin=False).
EMPTY_PRESET_NAME = 'Empty'
PRESETS = {
    EMPTY_PRESET_NAME: {},
}


# ── Effects considered "color-altering" ──────────────────────────────────
# When the "Hide color effects" checkbox is on, these are force-disabled
# AND hidden from the EFFECTS accordion. Original states are snapshotted
# on enable and restored on disable so the user can re-enter the mode
# without losing their settings.
#
# The list is curated by the user — these are effects that directly mess
# with RGB channels or the source palette in a way that breaks "color
# fidelity" of the input video.
# Effects that materially break passthrough's 1:1 input→output frame
# mapping by inserting extra frames into the stream (Stutter duplicates
# frames, Flash inserts a 1–2 frame strobe, Datamosh swaps to a prebaked
# I-frame-dropped source). Their blocks are pack_forget()-ed when
# passthrough mode is on, exactly like COLOR_EFFECT_KEYS does for
# hide-color-fx, and their cfg flags are forced off so the engine can't
# accidentally trip them either.
# Empty: stutter / flash / datamosh now all WORK in passthrough mode.
#   • Datamosh runs via DatamoshEffect (optical-flow motion-vector smear)
#     through the regular effect chain — no prebake needed in passthrough.
#   • Stutter and Flash use REPLACE-mode in the passthrough loop instead
#     of the cut-mode INSERT-mode: source `cap.grab()` advances the read
#     pointer normally, but the written frame is overridden with a held
#     copy (stutter) or a flash colour (flash). Output frame count stays
#     1:1 with input, so the source audio stays in perfect sync.
# Left as a tuple constant so the existing snapshot/restore machinery
# still works if any key is ever re-added.
PASSTHROUGH_HIDDEN_KEYS = ()


COLOR_EFFECT_KEYS = (
    # Direct colour overwrites / inversions / quantisations.
    'fx_flash',           # Flash Frame — replaces frame with white/black
    'fx_negative',        # Negative — 255 - pixel for every channel
    'fx_bitcrush',        # Bitcrush / Posterize — quantises to 1-7 levels
    'fx_dither',          # Dithering — Bayer 4x4 quantises palette to 2-16 levels
    'fx_bsod_shred',      # BSOD Shred — replaces bands with NT-blue + white text
    # Hue / chroma transforms (palette is recoloured, not preserved).
    'fx_rgb',             # RGB Shift — chromatic-aberration fringes
    'fx_colorbleed',      # Color Bleed / VHS Smear — channel blur
    'fx_temporal_rgb',    # Temporal RGB Shift — R/G/B from different frames
    'fx_echo',            # Echo Compound — explicit +30 deg hue-shifted echoes
    'fx_kali',            # Kali Mirror — composite includes 255-pixel inversion
    'fx_wrong_sub',       # Wrong Chroma Sub — chroma blocks bleed past edges
    # Aggressive numerical corruptions whose visible signature is colour shifts.
    'fx_waveshaper',      # Waveshaper — tanh saturation / hue distortion
    'fx_fft_phase',       # FFT Phase Corrupt — colour interference patterns
    'fx_dtype_corrupt',   # Dtype Reinterpret — float16 byte view = colour cliffs
    'fx_bad_signal',      # Bad Signal — randomly coloured vertical noise bars
    # Forensic / acoustic mappings whose output replaces the source palette.
    'fx_ela',             # ELA — error-level heat map replaces colours
    'fx_spatial_reverb',  # Spatial Reverb — convolves rows, palette mixes laterally
    # Compound effect chain: any of its random sub-effects above.
    'fx_cascade',         # Glitch Cascade — chains random palette-altering effects
)


# ── BSOD palette (FORMULA tab) ───────────────────────────────────────────
# Classic Win9x bluescreen — high-contrast, monospace. Used when the
# default Win95-silver theme is too soft to read code against.
C_BSOD_BG = '#0000AA'
C_BSOD_FG = '#FFFFFF'
C_BSOD_ACCENT = '#FFFF55'   # bright yellow for headings / values
C_BSOD_DIM = '#AAAAFF'      # muted blue-white for hints
C_BSOD_RED = '#FF5555'      # error red
C_BSOD_HL = '#1A1ABB'       # subtle highlight row


# ────────────────────────────────────────────────────────────────────────
class Tooltip:
    """Hover tooltip — used on labels, sliders, [?] icons."""
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
        self.geometry('1500x900')
        self.minsize(900, 700)
        self.configure(bg=C_SILVER)
        self.resizable(True, True)
        # Window icon (Tk uses iconphoto regardless of OS — covers Linux
        # WMs and the Tk titlebar on Windows/macOS where the OS-level icon
        # comes from the bundle/exe). Resource path resolves both the
        # frozen PyInstaller bundle (sys._MEIPASS) and a dev checkout.
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
        self.video_cap = None
        self.playback_thread = None
        self._audio_thread = None
        self._audio_wav = None
        self.stop_playback = threading.Event()
        # Cooperative cancellation: GUI sets engine.abort = True from the
        # main thread; the worker thread reads it. CPython GIL makes plain
        # attribute writes atomic, so no lock is needed.
        self.engine = None
        # Preview-player pause/volume state. Volume is applied per playback
        # loop iteration (≤5s latency) — sufficient for a 5s looping preview.
        self.pause_event = threading.Event()
        self._volume = 0.8
        self._volume_pre_mute = 0.8
        self._muted = False

        self.style = ttk.Style(self)
        self._setup_styles()
        self._setup_vars()
        self._build_ui()
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
        """Tk-vars are created from the registry's default_cfg().

        Side-channel state vars (cut-logic, mystery, export, custom resolution,
        formula expression, single-segment mode) are added on top.
        """
        self.vars = {}
        self._defaults_all = {}

        # Cut-logic + audio analysis
        cut_defaults = {
            'chaos_level': 0.6, 'threshold': 1.2, 'transient_thresh': 0.5,
            'min_cut_duration': 0.05, 'scene_buffer_size': 10.0,
            'use_scene_detect': False, 'snap_to_beat': False, 'snap_tolerance': 0.05,
            'use_manual_bpm': False, 'manual_bpm': 120.0,
            'passthrough_mode': False,
        }
        # Export
        export_defaults = {'fps': 24.0, 'crf': 22.0, 'custom_w': 1280.0, 'custom_h': 720.0}
        # Mystery
        mystery_keys = ('VESSEL', 'ENTROPY_7', 'DELTA_OMEGA', 'STATIC_MIND',
                        'RESONANCE', 'COLLAPSE', 'ZERO', 'FLESH_K', 'DOT')
        mystery_defaults = {f'mystery_{k}': 0.0 for k in mystery_keys}
        # "Always-on" toggles per mystery knob. Default False — preserves
        # legacy behaviour bit-identical (mystery golden tests rely on this).
        mystery_defaults.update({f'always_mystery_{k}': False
                                 for k in mystery_keys})

        # Registry defaults
        reg = default_cfg()

        # Per-effect audio-link toggles for passthrough mode. One BooleanVar
        # per coupled effect; default False so the existing audio path is
        # bit-identical until the user opts in. The `audio_link_<key>` keys
        # flow through self.vars and therefore through preset save/load.
        from vpc.audio.pipeline import EFFECT_AUDIO_COUPLING
        audio_link_defaults = {f'audio_link_{k}': False
                               for k in EFFECT_AUDIO_COUPLING.keys()}

        defaults = {**cut_defaults, **reg, **export_defaults,
                    **mystery_defaults, **audio_link_defaults}
        # Composite RGB lists handled via *_r/_g/_b ints below — drop the list keys
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

        # Side-channel string vars: silence + resolution mode + formula text
        self.var_silence_mode = tk.StringVar(value='none')
        # Hide-color-effects checkbox state + snapshot taken when toggled on
        self.var_hide_color_fx = tk.BooleanVar(value=False)
        self._color_fx_snapshot: dict = {}
        # Passthrough snapshot mirrors `_color_fx_snapshot`: stores user's
        # original choices for keys we force off while passthrough is on.
        self._passthrough_snapshot: dict = {}
        # Registry of audio-link Checkbutton holders, populated by
        # `_build_effect_block` for every spec whose enable_key is in
        # `EFFECT_AUDIO_COUPLING`. The passthrough_mode trace iterates
        # this list to pack/unpack all holders in one go when the user
        # flips passthrough on or off. Each entry: (enable_key, holder_frame).
        self._audio_link_holders: list = []
        self.var_resolution_mode = tk.StringVar(value='preset')
        self.var_formula_expr = tk.StringVar(value='frame')
        # Preview length (seconds) — UI-only, intentionally NOT in self.vars
        # so it bypasses preset save/load (avoids any compat risk with
        # existing preset files). Read at render-time via
        # `_get_preview_seconds()` which clamps to [1, 90].
        self.var_preview_seconds = tk.IntVar(value=5)
        # Encoder quality fields. The Quality dropdown is a convenience —
        # picking a preset writes crf/export_preset/tune below; touching
        # any of those by hand flips the dropdown to 'Custom'. Manual
        # editing always wins.
        self.var_quality_preset = tk.StringVar(value='High')
        self.var_tune = tk.StringVar(value='none')
        # Reentrancy guard: set True while a Quality preset is writing
        # the three managed fields, so their traces don't immediately
        # bounce the dropdown back to 'Custom'.
        self._applying_quality = False

        # Display vars for slider numeric labels
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
            # snap_tolerance is a small float (0.01..0.15) — including
            # `_tolerance` in int_suffixes was a bug: it forced the slider
            # display to int(0) and the slider stopped responding.
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
    # Sidebar width is locked: long filenames must NOT be allowed to push
    # the column wider, otherwise the centre and right panels shrink.
    SIDEBAR_W = 260

    def _build_ui(self):
        # weight=0 + uniform group keeps the sidebar at SIDEBAR_W regardless
        # of label content. The centre and right panels then split the
        # remaining horizontal space 3:2.
        self.grid_columnconfigure(0, weight=0, minsize=self.SIDEBAR_W)
        self.grid_columnconfigure(1, weight=3, minsize=400)
        self.grid_columnconfigure(2, weight=2, minsize=300)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar — propagate=False locks its width to SIDEBAR_W even if a
        # child widget asks for more room (e.g. a 60-char song filename).
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
        # Preview length row — sits directly above the PREVIEW button so
        # the duration control is visually attached to it. fill='x' keeps
        # the sidebar grid stable; spinbox is right-justified, label left.
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

        # Center
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

        # Right panel: preview monitor + transport + render controls + log.
        # Layout (top → bottom):
        #   1) Title bar
        #   2) Preview Monitor (640×360 frame)
        #   3) Transport strip: Pause / Mute / Volume / Clear-monitor
        #   4) Render progress bar
        #   5) CANCEL RENDER (only enabled while a render is running)
        #   6) Status Log (Clear button is inline with the LabelFrame title
        #      strip so it doesn't waste a full row).
        right = ttk.Frame(self, style='W95.TFrame')
        right.grid(row=0, column=2, padx=(4, 8), pady=8, sticky='nsew')
        tb2 = tk.Frame(right, bg=C_TITLE_BAR, height=28)
        tb2.pack(fill='x')
        tk.Label(tb2, text='Live Preview & Console',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 11, 'bold')).pack(side='left', padx=6, pady=3)
        cr = tk.Frame(right, bg=C_SILVER)
        cr.pack(fill='both', expand=True, padx=2, pady=2)

        # 2) Preview monitor
        pmon = tk.LabelFrame(cr, text='Preview Monitor (640×360)',
                             bg=C_SILVER, fg=C_TEXT, bd=2, relief='sunken',
                             font=('MS Sans Serif', 10, 'bold'))
        pmon.pack(fill='x', padx=8, pady=(6, 2))
        _blank = ImageTk.PhotoImage(Image.new('RGB', (640, 360), 'black'))
        self.player_label = tk.Label(pmon, image=_blank, bg=C_BLACK, bd=2, relief='sunken')
        self.player_label.imgtk = _blank
        self.player_label.pack(padx=4, pady=4)

        # 3) Transport strip — single row, controls scoped to the preview
        # player (NOT the renderer). Disabled until a preview is loaded;
        # toggled together by start_playback / stop_and_clear_playback.
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

        # 4) Render progress
        self.progress = ttk.Progressbar(cr, style='green.W95.Horizontal.TProgressbar',
                                        mode='determinate', maximum=100,
                                        variable=self.progress_var)
        self.progress.pack(fill='x', padx=8, pady=(2, 2))

        # 5) Cancel Render — separate, full-width, disabled outside renders.
        # Sets engine.abort=True (cooperative cancel implemented in
        # vpc/render/engine.py). NOT the same as the transport X button.
        self.btn_cancel_render = ttk.Button(
            cr, text='CANCEL RENDER',
            command=self.cancel_render,
            style='Stop.TButton', state='disabled')
        self.btn_cancel_render.pack(fill='x', padx=8, pady=(2, 4), ipady=4)

        # 6) Status log — Clear button sits in a thin top strip above
        # the Text widget. Using a real frame (not place()) so HiDPI /
        # theme-engine layout shifts can't push the button over the
        # log content.
        cp = tk.LabelFrame(cr, text='Status Log', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='sunken', font=('MS Sans Serif', 10, 'bold'))
        cp.pack(fill='both', expand=True, padx=8, pady=(2, 6))
        log_top = tk.Frame(cp, bg=C_SILVER)
        log_top.pack(fill='x', padx=4, pady=(2, 0))
        ttk.Button(log_top, text='Clear', style='W95.TButton',
                   width=7, command=self._clear_log).pack(side='right')
        self.console = tk.Text(cp, height=6, font=('Courier New', 9),
                               bg=C_WHITE, fg=C_BLACK, bd=2, relief='sunken')
        self.console.pack(fill='both', expand=True, padx=4, pady=(2, 4))

    # ─── source files / presets / tabs ───
    @staticmethod
    def _shorten_name(name: str, width: int = 28) -> str:
        """Truncate a filename to `width` characters, ellipsizing the middle.

        `Some_very_long_song_name_2026_master_v3_final_FINAL.mp3` →
        `Some_very_long…3_final_FINAL.mp3`. Keeps the extension visible.
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
        # Cached so passthrough mode can disable it: the audio file isn't
        # used when the engine extracts the source video's own track.
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

        # Three preset action buttons on one row. With `pack(side='left')`
        # each button auto-sized to its label text, so on the locked-260px
        # sidebar 'Save Current' ate most of the row and 'Delete' shrank
        # to a single character. Switching to grid with weight=1 on every
        # column gives the three buttons exactly one third of the row.
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

    # ─── widgets primitives ───
    @staticmethod
    def _parent_bg(parent):
        """Return the parent's bg colour so child rows match seamlessly.

        Effect blocks live inside white accordion bodies, but cut-logic /
        export panels are silver. Hard-coding bg=C_SILVER inside helpers
        produced visible silver strips on white — the "broken UI" symptom.
        Reading from the parent makes every helper self-adapting.
        """
        try:
            return parent.cget('bg') or C_SILVER
        except tk.TclError:
            return C_SILVER

    def _bind_scale_click_jump(self, scale: ttk.Scale):
        """Make a ttk.Scale jump to the click position AND keep drag working.

        Default Tk behaviour for ttk.Scale on the `clam` theme is
        page-step on trough click (the value pings to whichever extreme
        is closer to the click), which is unusable on continuous sliders.
        Returning 'break' from the press handler stopped page-step, but
        also blocked the class binding that normally starts dragging —
        so dragging stopped working too.

        The fix is to handle BOTH press and B1-Motion ourselves. Pressing
        sets the value; subsequent motion with B1 held continues setting
        the value. Both bindings return 'break' so the page-step class
        binding never runs. Clicks on the slider thumb still map to the
        thumb's centre (effectively a no-op on the visual position) and
        then drag tracks naturally from there.
        """
        def _value_from_x(x: int):
            try:
                lo = float(scale.cget('from'))
                hi = float(scale.cget('to'))
            except tk.TclError:
                return None
            w = scale.winfo_width() or 1
            # Account for the slider thumb's half-width on each side so
            # clicks at the visible extremes map to lo/hi cleanly.
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
        """Label + small [?] help icon to the right; both carry the tooltip."""
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
        """Like _row_with_help, but the [?] icon opens a modal popup with
        the full text instead of relying on the hover tooltip alone.

        Used for fields whose explanation is too long to fit a hover
        tooltip without overflowing the screen (Codec is the canonical
        case — bilingual EN/RU description with hardware-encoder caveats).
        Hover still shows a short one-line summary so the user has a hint
        without having to click.
        """
        bg = self._parent_bg(parent)
        row = tk.Frame(parent, bg=bg)
        row.pack(fill='x', padx=(8, 8), pady=(2, 0))
        f = ('Courier New', 9, 'bold') if mono else ('MS Sans Serif', 9)
        lbl = tk.Label(row, text=text, bg=bg, fg=C_TEXT, font=f, anchor='w')
        lbl.pack(side='left')
        # Distinct cursor + tinted background so the icon visibly differs
        # from the plain-tooltip [?] — signals "click for more".
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
        """Modal-ish popup with scrollable body. Uses Toplevel + transient
        + grab so it floats above the main window without true OS-modal
        blocking (Tk's grab_set is enough for a help dialog).

        Sized to ~600x460 with vertical scrollbar — fits any screen down
        to 800x600 and avoids the screen-overflow problem of the long
        hover tooltips this replaces.
        """
        win = tk.Toplevel(self)
        win.title(f'Help — {title}')
        win.configure(bg=C_SILVER)
        win.transient(self)
        win.geometry('600x460')
        win.resizable(True, True)
        # Title bar (Win95-themed, matches the rest of the app).
        tb = tk.Frame(win, bg=C_TITLE_BAR, height=24)
        tb.pack(fill='x')
        tk.Label(tb, text=title, fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 10, 'bold')).pack(side='left',
                                                           padx=6, pady=2)
        # Body: Text + Scrollbar (Text rather than Label so users can
        # select / copy snippets like codec names).
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
        # Close button + Esc binding for keyboard exit.
        bottom = tk.Frame(win, bg=C_SILVER)
        bottom.pack(fill='x', padx=8, pady=8)
        ttk.Button(bottom, text='Close', style='W95.TButton',
                   width=10, command=win.destroy).pack(side='right')
        win.bind('<Escape>', lambda _e: win.destroy())
        # Center over the parent window.
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
        """Standalone slider with header showing live numeric value."""
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
        ttk.Combobox(f, values=values, textvariable=self.vars[name],
                     style='W95.TCombobox', width=14).pack(side='left')
        return f

    # ─── conditional enable / position-preserving repack helpers ───
    # Two-tier UX: HIDE for "this whole control group is irrelevant in
    # the current mode" (effect block when its enable_key is OFF, or
    # passthrough hiding stutter/flash/datamosh) and DISABLE-grey for
    # "this control still belongs here but its parent flag pinned its
    # value" (chance slider while `always` is ON, BPM entry while
    # snap-to-beat is OFF). Disable cascades naturally: a predicate of
    # the form `lambda: a.get() and b.get()` is enabled only when both
    # parents are truthy.

    _SKIP_RECOLOR_TEXTS = ('[?]', '[ ? ]')

    def _set_widget_enabled(self, w, enabled: bool):
        """Recursively grey/ungrey a Tk subtree (skips help-icon labels).

        Uses a per-widget cache (`_sb_enabled` attribute) so a no-op call
        skips the underlying configure/state/cget round-trip — important
        because preset apply fires every effect's traces in a tight burst,
        which used to walk every label/slider tree on every write.
        """
        prev = getattr(w, '_sb_enabled', None)
        if prev is enabled:
            # Children already in the desired state too: subtree-wide cache
            # invariant means no recursion needed.
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
        """Tie `widgets` enabled-state to `predicate()`, re-evaluated
        whenever any var in `watch_vars` is written. Applies once
        immediately so layout matches current var values.
        """
        targets = list(widgets)
        def _apply(*_a):
            ok = bool(predicate())
            for w in targets:
                self._set_widget_enabled(w, ok)
        for v in watch_vars:
            v.trace_add('write', _apply)
        _apply()

    def _repack_in_order(self, widget, order, **pack_kw):
        """Re-pack `widget` so it lands at its original position from
        `order` (a snapshot of its parent's children taken at build).
        Walks `order` forward from `widget`'s slot to find the next
        currently-mapped sibling and packs `before=` it. Falls back to
        plain pack (end) if nothing later is visible.

        Fixes the bug where pack_forget'd widgets always reappeared at
        the bottom of their parent (passthrough cut-widgets, color-fx
        hidden effect blocks).
        """
        try:
            idx = order.index(widget)
        except ValueError:
            widget.pack(**pack_kw)
            return
        parent = widget.master
        for sibling in order[idx + 1:]:
            try:
                if (sibling.winfo_exists() and sibling.winfo_ismapped()
                        and sibling.master is parent):
                    widget.pack(before=sibling, **pack_kw)
                    return
            except tk.TclError:
                continue
        widget.pack(**pack_kw)

    # ─── effects accordion (registry-driven) ───
    def _build_effects_accordion(self, parent):
        for w in parent.winfo_children():
            w.destroy()
        outer = tk.Frame(parent, bg=C_SILVER)
        outer.pack(fill='both', expand=True)

        # Top toolbar: hide-color-effects switch lives here so it's reachable
        # without scrolling and clearly separate from per-group accordions.
        tools = tk.Frame(outer, bg=C_SILVER, bd=1, relief='groove')
        tools.pack(fill='x', side='top', padx=4, pady=(4, 2))
        cb = ttk.Checkbutton(
            tools, text='Hide color-altering effects',
            variable=self.var_hide_color_fx,
            style='W95.TCheckbutton',
            command=self._on_toggle_hide_color_fx)
        cb.pack(side='left', padx=6, pady=4)
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

        canvas = tk.Canvas(outer, bg=C_SILVER, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        cf = tk.Frame(canvas, bg=C_SILVER)
        cf_window = canvas.create_window((0, 0), window=cf, anchor='nw')

        # Recompute scrollregion AND clamp the current view to it.
        # Debounced via a single after_idle slot so multiple <Configure>
        # events (cf + canvas + accordion toggles can all fire in the same
        # tick) coalesce into one refresh — was producing visible
        # "doubling" / scrollbar-thumb-flicker on every group expansion.
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
            # Only re-stretch the inner frame when width actually changed —
            # itemconfig on the same width still triggers a <Configure>
            # cascade that fed back into refresh and caused the flicker.
            if e.width != _scroll_state['last_w']:
                _scroll_state['last_w'] = e.width
                canvas.itemconfig(cf_window, width=e.width)
            _refresh_scrollregion()
        canvas.bind('<Configure>', _on_canvas_configure)
        self._effects_refresh_scroll = _refresh_scrollregion

        # Mousewheel scroll only when pointer is over this canvas. Using
        # bind_all is the only reliable way to catch wheel events that
        # children would otherwise swallow; pair it with Enter/Leave so
        # the global handler is only attached while the pointer is here.
        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _wheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        # Track per-effect block frames so the color-fx hide toggle can
        # pack_forget / pack them again without rebuilding the whole tree.
        self._effect_block_frames: dict = {}

        # CUT LOGIC group — manual fields (these are not effects, but cfg knobs)
        body = self._acc_group(cf, 'CUT LOGIC', open=True)
        self._build_cut_logic(body)

        # Generated effect groups
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
            if group_name == 'PAINT':
                body = self._acc_group(cf, 'PAINT CANVAS FX', open=opened, bg_color='#008080', fg_color='#FFFFFF')
            else:
                body = self._acc_group(cf, group_name, open=opened)
            for s in specs:
                blk = self._build_effect_block(body, s)
                self._effect_block_frames[s.enable_key] = blk
            if group_name == 'OVERLAYS':
                self._build_overlay_dir_picker(body)
            # Snapshot child order so passthrough/color-fx restoration
            # re-packs hidden blocks back into their original slots
            # instead of dumping them at the end of the body.
            body._sb_initial_order = list(body.winfo_children())

        # Apply current hide-color-fx state to freshly built blocks.
        if self.var_hide_color_fx.get():
            self._apply_hide_color_fx(active=True, take_snapshot=False)
        # Same for passthrough — if the checkbox was already on (e.g. loaded
        # from a saved preset), make sure the frame-inserting effect blocks
        # are hidden right after they're built.
        if self.vars.get('passthrough_mode') and self.vars['passthrough_mode'].get():
            self._apply_passthrough_hide(active=True, take_snapshot=False)

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

        def _toggle(_e=None):
            if body.winfo_ismapped():
                body.pack_forget()
                hdr.configure(bg=C_SILVER)
                ar_l.configure(bg=C_SILVER, fg=C_TEXT)
                t_l.configure(bg=C_SILVER, fg=C_TEXT)
                arrow.set('▶')
            else:
                body.pack(fill='x')
                active_bg = bg_color if bg_color else C_TITLE_BAR
                active_fg = fg_color if fg_color else C_WHITE
                hdr.configure(bg=active_bg)
                ar_l.configure(bg=active_bg, fg=active_fg)
                t_l.configure(bg=active_bg, fg=active_fg)
                arrow.set('▼')
            refresh = getattr(self, '_effects_refresh_scroll', None)
            if refresh is not None:
                self.after_idle(refresh)
        for w in (hdr, ar_l, t_l):
            w.bind('<Button-1>', _toggle)
        return body

    # ─── color-fx hide toggle ───
    def _on_toggle_passthrough(self):
        """Checkbox callback — hide/restore frame-inserting effect blocks."""
        active = bool(self.vars['passthrough_mode'].get())
        self._apply_passthrough_hide(active=active, take_snapshot=True)

    def _apply_passthrough_hide(self, *, active: bool, take_snapshot: bool):
        """Hide and force-off frame-inserting effects when passthrough is on.

        Mirrors `_apply_hide_color_fx`: snapshot prior states on enable,
        restore them on disable. Snapshot lives in `_passthrough_snapshot`
        so a user toggling the box back off recovers their original effect
        choices instead of waking up with everything off.
        """
        if active:
            if take_snapshot:
                snap = {}
                # Some keys (e.g. fx_flash) live in BOTH PASSTHROUGH_HIDDEN_KEYS
                # and COLOR_EFFECT_KEYS. If hide-color-fx is currently on,
                # the live var is False (forced off by that mode) — but the
                # USER's true choice is preserved in `_color_fx_snapshot`.
                # We must snapshot from there, otherwise restoring later
                # writes a stale False back into the user's preset.
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
            for key in PASSTHROUGH_HIDDEN_KEYS:
                if key in self.vars:
                    try:
                        self.vars[key].set(False)
                    except Exception:
                        pass
                blk = self._effect_block_frames.get(key) if hasattr(
                    self, '_effect_block_frames') else None
                if blk is not None and blk.winfo_ismapped():
                    blk.pack_forget()
            # Cut Logic widgets that have no meaning in passthrough.
            for w in getattr(self, '_passthrough_cut_widgets', []):
                if w.winfo_ismapped():
                    w.pack_forget()
            # Disable the audio file picker — passthrough uses the source
            # video's own audio track. The user can still see the previous
            # selection (label retains state), they just can't change it.
            btn = getattr(self, '_audio_btn', None)
            if btn is not None:
                btn.state(['disabled'])
        else:
            snap = self._passthrough_snapshot or {}
            color_active = self.var_hide_color_fx.get()
            for key in PASSTHROUGH_HIDDEN_KEYS:
                if key in self.vars and key in snap:
                    if color_active and key in COLOR_EFFECT_KEYS:
                        # color-fx hide is still in effect for this key — if
                        # we wrote `snap[key]=True` to the live var, color-fx
                        # would force it back to False on its next sync, and
                        # the user's choice would be silently lost when they
                        # later disable color-fx hide. Write to the
                        # color-fx snapshot instead so the value survives
                        # until that mode is exited.
                        self._color_fx_snapshot[key] = snap[key]
                    else:
                        try:
                            self.vars[key].set(snap[key])
                        except Exception:
                            pass
                blk = self._effect_block_frames.get(key) if hasattr(
                    self, '_effect_block_frames') else None
                # Don't re-pack a block that color-fx hide is currently
                # holding hidden — let that mode own visibility for shared
                # keys until it's disabled.
                shared_hidden = color_active and key in COLOR_EFFECT_KEYS
                if (blk is not None and not blk.winfo_ismapped()
                        and not shared_hidden):
                    order = getattr(blk.master, '_sb_initial_order', None)
                    if order is not None:
                        self._repack_in_order(blk, order, fill='x')
                    else:
                        blk.pack(fill='x')
            # Re-pack cut-only widgets in their ORIGINAL slots, not at
            # the end. Plain `pack(fill='x')` previously dumped them
            # below every Cut Logic control because tk.pack appends.
            for w in getattr(self, '_passthrough_cut_widgets', []):
                if not w.winfo_ismapped():
                    order = getattr(w.master, '_sb_initial_order', None)
                    if order is not None:
                        self._repack_in_order(w, order, fill='x')
                    else:
                        w.pack(fill='x')
            # Re-enable the audio picker.
            btn = getattr(self, '_audio_btn', None)
            if btn is not None:
                btn.state(['!disabled'])
            self._passthrough_snapshot = {}

        refresh = getattr(self, '_effects_refresh_scroll', None)
        if refresh is not None:
            self.after_idle(refresh)

    def _on_toggle_hide_color_fx(self):
        """Checkbox callback. Snapshots states + applies, or restores."""
        active = self.var_hide_color_fx.get()
        self._apply_hide_color_fx(active=active, take_snapshot=True)

    def _apply_hide_color_fx(self, *, active: bool, take_snapshot: bool):
        """Hide+disable all color-altering effects, or restore.

        On enable: snapshot current enable-state of every key in
        COLOR_EFFECT_KEYS plus silence_mode, then force them off / 'none'
        and pack_forget the corresponding effect blocks.
        On disable: restore from the snapshot (if any) and re-pack.
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
                self._color_fx_snapshot = snap

            # Disable + hide
            for key in COLOR_EFFECT_KEYS:
                if key in self.vars:
                    try:
                        self.vars[key].set(False)
                    except Exception:
                        pass
                blk = self._effect_block_frames.get(key) if hasattr(
                    self, '_effect_block_frames') else None
                if blk is not None and blk.winfo_ismapped():
                    blk.pack_forget()
            # Force silence_mode 'dim' off (other modes survive)
            if self.var_silence_mode.get() == 'dim':
                self.var_silence_mode.set('none')
            self._sync_silence_radio_visibility()
        else:
            # Restore
            snap = self._color_fx_snapshot or {}
            for key in COLOR_EFFECT_KEYS:
                if key in self.vars and key in snap:
                    try:
                        self.vars[key].set(snap[key])
                    except Exception:
                        pass
                blk = self._effect_block_frames.get(key) if hasattr(
                    self, '_effect_block_frames') else None
                if blk is not None and not blk.winfo_ismapped():
                    order = getattr(blk.master, '_sb_initial_order', None)
                    if order is not None:
                        self._repack_in_order(blk, order, fill='x')
                    else:
                        blk.pack(fill='x')
            prev_silence = snap.get('__silence_mode__')
            if prev_silence:
                self.var_silence_mode.set(prev_silence)
            self._color_fx_snapshot = {}
            self._sync_silence_radio_visibility()

        refresh = getattr(self, '_effects_refresh_scroll', None)
        if refresh is not None:
            self.after_idle(refresh)

    # ─── Quality preset ↔ manual fields sync ───
    def _on_quality_preset_changed(self):
        """Quality dropdown changed → write its (crf, preset, tune) into
        the manual fields. 'Custom' is a marker — it doesn't mutate."""
        name = self.var_quality_preset.get()
        spec = QUALITY_PRESETS.get(name)
        if spec is None:  # Custom or unknown
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
        """Manual field changed → flip Quality dropdown to whichever
        preset (if any) now matches, else 'Custom'. Skipped while a
        preset is mid-apply to avoid trace ping-pong."""
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
        """Hide the 'Dim' radio button while color-fx hiding is active."""
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
        # Widgets that lose their meaning in passthrough mode (no random
        # sampling → no scene buffer, no cuts → no min-cut filter) are
        # collected here so `_apply_passthrough_hide` can pack_forget /
        # pack them as a group, mirroring how color-fx hide handles its
        # set of effect blocks.
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

        # Always-shown sliders. Ones that only matter outside passthrough
        # are built separately below so they can be hidden as a group.
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

        # Cut-only sliders: irrelevant in passthrough.
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

        # Scene Buffer Size only matters when scene-cut detection is on —
        # grey it out otherwise so the user knows the slider is dormant.
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

        # Manual BPM override — generates a uniform beat grid from a
        # user-typed tempo value when snap-to-beat is on. Bypasses librosa's
        # tempo estimator entirely; useful for tracks with weak onsets or
        # when the exact target BPM is known.
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

        # Snap cascade. Beat-snap-tolerance and the entire manual-BPM
        # subgroup (header label + checkbox + numeric entry) only matter
        # when snap-to-beat is ON. Inside that subgroup the BPM entry has
        # an extra dependency on `use_manual_bpm` — disabled even with
        # snap ON if the user hasn't ticked "Use manual BPM:". The
        # cascade is encoded directly in the predicate (AND of vars).
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
                # Fall back to current var value if the user typed garbage.
                self._display_vars['manual_bpm'].set(
                    f"{self.vars['manual_bpm'].get():.1f}")
        bpm_entry.bind('<FocusOut>', _commit_manual_bpm)
        bpm_entry.bind('<Return>', _commit_manual_bpm)

        # Passthrough mode — read frames sequentially from one source video,
        # analyse its OWN audio track, no cut/sample/random sampling. Frame-
        # inserting effects (Stutter, Flash, Datamosh prebake) get hidden
        # from the EFFECTS panel while this is on, since they'd shift the
        # input→output frame mapping and desync the audio.
        self._row_with_help(body, 'Passthrough Mode', bi(
            "Process the source video 1:1 — no cuts, no resampling, native frame order. "
            "Audio is taken from the video's own track and used both for analysis (effect "
            "triggers) and for the output. No external audio file is needed. Stutter / "
            "Flash / Datamosh ALL work here too: they switch to replace-mode (overwrite "
            "frames in place instead of inserting new ones) so audio stays in sync.",
            "Прогон исходного видео 1:1 — без нарезки, без рандомного семплинга, в "
            "нативном порядке кадров. Аудио берётся из самого видео и используется и для "
            "анализа (триггеры эффектов), и в выводе. Внешний аудиофайл не нужен. "
            "Stutter / Flash / Datamosh тоже работают: они переключаются в режим замены "
            "кадров (вместо вставки новых) — аудио остаётся в синхроне."))
        ttk.Checkbutton(body, text='Passthrough mode (use source video as-is)',
                        variable=self.vars['passthrough_mode'],
                        command=self._on_toggle_passthrough,
                        style='W95.TCheckbutton').pack(anchor='w', padx=24, pady=(0, 4))

        # Silence
        self._row_with_help(body, 'Silence Treatment', bi(
            'How long (>1s) silent stretches are rendered: dim, soft blur, both, or untouched. '
            'Default: none.',
            'Как обрабатывать длинные (>1 с) тихие участки: затемнение, размытие, оба варианта '
            'или без обработки. По умолчанию: none.'))
        sf = tk.Frame(body, bg=self._parent_bg(body))
        sf.pack(fill='x', padx=20, pady=(2, 6))
        # Order: None first so it visually reads as the default.
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

        # Snapshot the body's child order so the passthrough toggle can
        # re-pack cut-only widgets back into their original slots via
        # `before=` anchors, instead of dumping them at the bottom.
        body._sb_initial_order = list(body.winfo_children())

    def _build_effect_block(self, parent, spec):
        """Build the GUI block for one EffectSpec.

        Three visibility tiers cooperate inside the returned frame:
          • header (checkbox + label + `always` toggle) is always visible
            while the block itself is mapped — it's the user's only handle
            to turn the effect back on;
          • `inner` (chance / params / always-on intensity) is HIDDEN while
            the effect's enable_key is OFF — keeps the panel uncluttered
            and removes "dead" sliders the user can't influence;
          • `ai_holder` (always-on intensity) is hidden unless `always` is
            ON, and lives INSIDE `inner` between the chance row and the
            params (sentinel anchors the slot) — fixes the previous bug
            where it appeared below the block separator.

        The thin separator stays a direct child of `block` (NOT `inner`)
        so it keeps marking the boundary between blocks even when the
        effect is off and `inner` is hidden.

        Returns the outer frame so callers (hide-color-fx, passthrough)
        can pack_forget/pack the WHOLE block as a unit.
        """
        block = tk.Frame(parent, bg=C_WHITE)
        block.pack(fill='x')

        # Header row: checkbox + label + tooltip + (optional) always toggle.
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

        # `inner` carries every per-effect knob and is hidden whole when
        # the effect's enable checkbox is OFF.
        inner = tk.Frame(block, bg=C_WHITE)

        chance_widgets: list = []
        if spec.chance_key is not None:
            ch_row = self._row_with_help(inner, 'Chance', bi(
                'Probability the effect fires per qualifying frame. Scaled by Global Chaos.',
                'Вероятность срабатывания эффекта на подходящем кадре. Масштабируется ползунком '
                'Global Chaos.'))
            ch_slider = self._slider(inner, spec.chance_key, 0.0, 1.0, indent=True)
            chance_widgets = [ch_row, ch_slider]

        # Always-on intensity. Built INSIDE inner so its visual slot sits
        # between chance and params. A zero-height sentinel pinned right
        # after it serves as the `before=` anchor when the holder is
        # toggled visible later — without it, re-pack would land below
        # the params instead.
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

        # Per-effect parameter controls.
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
            else:
                self._row_with_help(inner, p.label, p.tooltip)
                self._slider(inner, p.key, p.lo, p.hi, indent=True)

        # Audio-link checkbox (only for effects that have a paired audio
        # defect registered in vpc.audio.pipeline.EFFECT_AUDIO_COUPLING).
        # Lives at the bottom of `inner` so it inherits the effect-off
        # auto-hide; visibility within `inner` is further gated on
        # passthrough_mode via `_sync_audio_link` below.
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

        # Bottom separator — sits in `block`, not `inner`, so it remains
        # visible while the effect is off and keeps marking boundaries.
        sep = tk.Frame(block, bg=C_DARK_GRAY, height=1)
        sep.pack(fill='x', padx=4)

        # Visibility wiring.
        # `pack_info()` (returns dict; empty when the widget isn't being
        # managed by `pack`) is the AUTHORITATIVE check here, NOT
        # `winfo_ismapped()`. The latter reflects actual on-screen
        # visibility — which requires every ancestor to be mapped too.
        # During build `_sync_always` runs BEFORE `_sync_inner` packs
        # the `inner` frame, so `ai_holder.winfo_ismapped()` is False
        # even when `ai_holder.pack()` was just called: that left the
        # else-branch's pack_forget unreached, and a moment later when
        # `_sync_inner` mapped `inner` the still-managed `ai_holder`
        # surfaced — visible always-int slider despite `always` OFF.
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

        enable_var.trace_add('write', _sync_inner)

        if ai_holder is not None:
            always_var = self.vars[spec.always_key]

            def _sync_always(*_a):
                if always_var.get():
                    if not _is_packed(ai_holder):
                        # `before=params_anchor` keeps ai_holder slotted
                        # between chance and params instead of bouncing
                        # to the bottom of `inner` on each toggle.
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
            # Chance is meaningless under `always` — grey it out so the
            # user sees it's pinned. When `inner` is hidden (effect OFF)
            # the grey state is invisible anyway, which is fine.
            if chance_widgets:
                self._bind_dep(chance_widgets,
                               lambda av=always_var: not av.get(),
                               [always_var])
            _sync_always()

        if audio_link_holder is not None:
            passthrough_var = self.vars.get('passthrough_mode')

            def _sync_audio_link(*_a, holder=audio_link_holder,
                                 pt_var=passthrough_var):
                # Visible iff (a) passthrough mode is on. The effect-off
                # auto-hide is handled by `inner` collapsing — we don't
                # need to re-check `enable_var` here.
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

        # Dynamically set canvas dimensions based on video source aspect ratio
        ratio = self._get_source_aspect_ratio()
        if ratio >= 1.0:
            canvas_w = 640
            canvas_h = int(640 / ratio)
        else:
            canvas_h = 360
            canvas_w = int(360 * ratio)
            
        win.canvas_w = canvas_w
        win.canvas_h = canvas_h

        # Set default geometry with safe minimum height to prevent toolbar clipping
        win.geometry(f"{canvas_w + 180}x{max(canvas_h + 30, 480)}")
        win.minsize(400, 450)

        main_frame = tk.Frame(win, bg=C_SILVER, padx=5, pady=5)
        main_frame.pack(fill='both', expand=True)

        # Toolbar sidebar
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
            import winreg
            import platform
            fonts = {}
            if platform.system() == 'Windows':
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
            # standard fallback list
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

        # Text input references
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

        # 1. Tools Section
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

        # 1b. Text Options (packed below tools)
        tk.Label(toolbar, text="Text / Текст", font=('MS Sans Serif', 8), bg=C_SILVER).pack(anchor='w', padx=2, pady=(4, 0))
        text_entry = ttk.Entry(toolbar, width=12)
        text_entry.insert(0, "VPC")
        text_entry.pack(fill='x', padx=4, pady=1)
        Tooltip(text_entry, bi("Type text to place on click", "Введите текст для вставки по клику"))

        font_combo = ttk.Combobox(toolbar, values=font_names, style='W95.TCombobox', width=10)
        font_combo.set(default_font)
        font_combo.pack(fill='x', padx=4, pady=1)
        Tooltip(font_combo, bi("Select text font", "Выберите шрифт текста"))

        # 2. Size Section
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

        # 3. Canvas Actions Section (Packed horizontally side-by-side to prevent vertical clipping)
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

            # Center relative to parent
            dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 100, parent.winfo_rooty() + 100))

            choice = [None]  # Mutable box to store selection

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
                # Read file bytes first to correctly support Unicode paths on Windows (e.g. Cyrillic characters)
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
                    # Apply Otsu's thresholding to get a clean black-and-white mask
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

        # 4. Color Selection Section
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

        # Swatches grid (2 rows of 5 classic Paint colors)
        swatches_frame = tk.Frame(toolbar, bg=C_SILVER)
        swatches_frame.pack(pady=4)

        classic_colors = [
            (0, 0, 0),        # Black
            (128, 128, 128),  # Dark Gray
            (255, 0, 0),      # Red
            (0, 255, 0),      # Green
            (0, 0, 255),      # Blue
            
            (255, 255, 255),  # White
            (192, 192, 192),  # Light Gray
            (255, 255, 0),    # Yellow
            (255, 0, 255),    # Magenta
            (0, 255, 255),    # Cyan
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

    def _get_source_aspect_ratio(self):
        if self.video_paths:
            path = self.video_paths[0]
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


    # ─── export panel ───
    def _build_export_panel(self, parent):
        for w in parent.winfo_children():
            w.destroy()
        wr = tk.Frame(parent, bg=C_SILVER)
        wr.pack(fill='both', expand=True, padx=4, pady=4)

        # FPS
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
        # Match-source button: probe the loaded source video's native FPS
        # via OpenCV and write it into the combobox + cfg var. Useful in
        # passthrough (so output is bit-exact in time) and outside it (so
        # the cut pipeline samples at the source's native rate). Read-only
        # against an empty selection — logs an error instead of crashing.
        match_btn = ttk.Button(fr, text='Match source FPS',
                               command=self._fps_match_source,
                               style='W95.TButton')
        match_btn.pack(side='left', padx=4)
        Tooltip(match_btn, bi(
            "Read the native FPS of the currently loaded source video and use it. "
            "Requires Load Source Video to be set first.",
            "Считать нативный FPS текущего загруженного видео и поставить его. "
            "Сначала нужно загрузить источник через Load Source Video."))

        # Resolution mode (backlog #2)
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

        # Preset combo
        rr = tk.Frame(wr, bg=C_SILVER); rr.pack(fill='x', padx=20, pady=2)
        tk.Label(rr, text='Preset:', bg=C_SILVER, width=10, anchor='w').pack(side='left')
        self.res_combo = ttk.Combobox(rr, values=['240p', '360p', '480p', '720p', '1080p'],
                                      style='W95.TCombobox', width=12)
        self.res_combo.set('720p'); self.res_combo.pack(side='left', padx=4)

        # Custom WxH
        cwf = tk.Frame(wr, bg=C_SILVER); cwf.pack(fill='x', padx=20, pady=2)
        tk.Label(cwf, text='Custom W×H:', bg=C_SILVER, width=12, anchor='w').pack(side='left')
        ttk.Spinbox(cwf, from_=64, to=7680, textvariable=self.vars['custom_w'],
                    width=8).pack(side='left', padx=2)
        tk.Label(cwf, text='×', bg=C_SILVER).pack(side='left')
        ttk.Spinbox(cwf, from_=64, to=4320, textvariable=self.vars['custom_h'],
                    width=8).pack(side='left', padx=2)

        # Quality preset — convenience layer that fills CRF / ffmpeg
        # preset / tune below. Selecting an entry writes those three
        # fields; touching them by hand flips this dropdown back to
        # 'Custom'. Manual control is never taken away.
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

        # CRF / codec / preset (manual)
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
        # Codec list is filtered at startup against `ffmpeg -encoders` —
        # HW variants (NVENC/QSV/AMF/VideoToolbox) only show up if the
        # local ffmpeg build actually supports them. The runtime-side
        # fallback in engine.py covers the case where the encoder is
        # listed but fails to initialize (no driver, GPU busy, etc.).
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

        # Tune — x264/x265-only film/grain/animation/stillimage hint.
        # 'none' means the flag is omitted entirely. Available for non-
        # x264/x265 codecs but ignored downstream (see sink.py).
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

        # Manual CRF edits → flip quality dropdown to Custom. Trace is
        # added once here, after var_quality_preset exists. The reentrancy
        # guard protects against trace firing while a preset is being
        # applied (which writes crf itself).
        self.vars['crf'].trace_add('write',
                                   lambda *_: self._refresh_quality_label())
        self._refresh_quality_label()

    # ─── FORMULA panel (TUI-styled, dedicated tab) ───
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

    # ── BSOD-styled tooltip (yellow-on-blue, monospace) ──
    def _bsod_tip(self, widget, text):
        """Hover tooltip styled like the rest of the BSOD tab.

        Yellow text on the BSOD background with a thin white border —
        matches the "system error" aesthetic instead of clashing with
        Win95-yellow tooltips. Bilingual EN/RU divider supported.
        """
        # Use the existing Tooltip helper but post-style the popup. The
        # Tooltip class creates its label on enter, so we wrap creation.
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
        """FORMULA tab — Win9x BSOD palette, monospace, fully scrollable.

        Layout sized so nothing escapes horizontally: every long line
        wraps to the panel width (which is tracked dynamically), the
        snippet grid drops to 2 columns when the panel is narrow, and
        every control gets a bilingual hover tooltip — formula authors
        who don't know NumPy still get pointers per control.
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

        # Wrap-length is a moving target — the user can resize the window
        # at any time. Track every label that needs to wrap and update
        # them all on canvas <Configure>.
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

        # ── Heading ────────────────────────────────────────────────
        # The "A problem has been detected..." line is intentional BSOD
        # cosplay — it's the recognisable Win9x crash text — and acts as
        # a visual signal that this tab is "the dangerous one." Kept on
        # request; only its sizing was the problem before.
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

        # ── Controls row (enable / chance / blend) ────────────────
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

        # ── Editor box ────────────────────────────────────────────
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

        # ── Status line ──────────────────────────────────────────
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

        # ── Live params a/b/c/d in 2x2 grid ───────────────────────
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
        # Per-letter usage hints — short, bilingual, concrete.
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

        # ── Snippets — one bilingual hint per snippet ────────────
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

        # Per-snippet plain-language explanations (EN + RU). Index aligns
        # with FORMULA_SNIPPETS; the order is fixed there.
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
        # 5 columns by default; the inner frame is width-tracked, so on
        # narrow windows the row simply wraps via the canvas scroll.
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

        # ── Reference — bilingual cheat-sheet ─────────────────────
        ref = tk.Frame(inner, bg=C_BSOD_BG)
        ref.pack(fill='x', padx=12, pady=(8, 4))
        self._bsod_label(ref, '[ REFERENCE / СПРАВКА ]',
                         fg=C_BSOD_ACCENT, font=MONO).pack(anchor='w')
        # Lines must stay under ~40 monospace chars so they fit even on a
        # 600px panel. The previous version had the description after the
        # colon on the same line and the example expressions inline with
        # comments, both of which spilled past the right edge on narrow
        # windows. Now: short header + value on the row, comments on the
        # line below the example.
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

        # ── Footer ────────────────────────────────────────────────
        ft = tk.Frame(inner, bg=C_BSOD_BG)
        ft.pack(fill='x', padx=12, pady=(8, 14))
        _wrap_lbl(ft,
                  'Save the formula via Preset — expression + a/b/c/d are '
                  'stored in the preset config.\n'
                  'Чтобы сохранить формулу, сохраните Preset — выражение '
                  'и a/b/c/d попадут в конфиг пресета.',
                  fg=C_BSOD_DIM, font=MONO_S).pack(fill='x')

    def _bsod_slider(self, parent, key, lo, hi, length=160):
        """ttk.Scale with click-jump bound — for the BSOD formula tab."""
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=self.vars[key],
                       orient=tk.HORIZONTAL, length=length)
        sc.pack(side='left', padx=4)
        self._bind_scale_click_jump(sc)
        return sc

    # Back-compat shim — older code paths still referenced _tui_slider.
    def _tui_slider(self, parent, key, lo, hi, length=160):
        return self._bsod_slider(parent, key, lo, hi, length=length)

    def _on_formula_text_changed(self, _e=None):
        text = self.formula_text.get('1.0', 'end-1c')
        # Persist into the StringVar so get_current_config sees it
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
        """Push the StringVar into the Text widget — used after preset load."""
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
        # Map knob → unique always-label so order in MYSTERY_KNOBS stays
        # the source of truth for layout while always-names live next to
        # the engine-side flags in vpc/mystery.py.
        always_label_by_key = dict(MYSTERY_ALWAYS_LABELS)
        for label, key in MYSTERY_KNOBS:
            self._row_with_help(wr, label, '?', mono=True)
            self._slider(wr, key, 0.0, 1.0)
            # `key` is `mystery_<KNOB>` — derive the engine knob name and
            # look up its cryptic always-label. Skip silently if a knob
            # ever lacks a label (shouldn't happen — guarded by tests).
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

    # ─── file selection ───
    def sel_audio(self):
        p = filedialog.askopenfilename(filetypes=[('Audio', '*.mp3 *.wav')])
        if p:
            self.audio_path = p
            self.lbl_audio_name.configure(
                text=self._shorten_name(os.path.basename(p)))
            Tooltip(self.lbl_audio_name, p)
            self._audio_dot.configure(fg=C_GREEN_DOT)

    def sel_video(self):
        ps = filedialog.askopenfilenames(
            title='Select Video Source(s)',
            filetypes=[('Video', '*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.mpg *.mpeg')])
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

    # ─── config + preset I/O ───
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
        # Skip writes whose value already matches — every var.set fires
        # every registered trace (effect inner-pack toggles, dep-cascade
        # greying, scroll refreshes), so redundant writes used to walk
        # every effect block twice for no visible change.
        def _set_if_changed(var, new_val):
            try:
                if var.get() != new_val:
                    var.set(new_val)
            except Exception:
                try: var.set(new_val)
                except Exception: pass

        # Reset to defaults first
        for k, v in self._defaults_all.items():
            if k in self.vars:
                _set_if_changed(self.vars[k], v)
        # Empty preset = everything off + silence none.
        if name == EMPTY_PRESET_NAME:
            for spec in EFFECTS:
                if spec.enable_key in self.vars:
                    _set_if_changed(self.vars[spec.enable_key], False)
        _set_if_changed(self.var_silence_mode, 'none')
        _set_if_changed(self.var_resolution_mode, 'preset')
        # Apply overrides
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
                self.log('presets.json corrupt — regenerating')
                self._generate_builtin_presets_file()
                self._refresh_presets_listbox()
                if self._user_presets:
                    self._presets_listbox.selection_set(0)
                    self._load_selected_preset()
                return

            # Migration: drop the old built-in presets but keep any custom
            # ones the user saved. If after stripping there is no Empty
            # preset, regenerate the empty-only file.
            kept = [p for p in self._user_presets if not p.get('builtin')]
            had_old_builtins = len(kept) != len(self._user_presets)
            has_empty = any(p.get('name') == EMPTY_PRESET_NAME for p in kept)
            if had_old_builtins:
                self.log('Dropped old built-in presets; user presets kept.')
            if not has_empty:
                # Build the canonical Empty entry inline.
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
        # Now generates ONLY the Empty preset. Custom presets the user
        # builds get appended via _save_current_preset.
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
            self.log(f'ERROR: could not save presets.json — {e}')

    def _refresh_presets_listbox(self):
        self._presets_listbox.delete(0, tk.END)
        for entry in self._user_presets:
            disp = f"[B] {entry['name']}" if entry.get('builtin') else entry['name']
            self._presets_listbox.insert(tk.END, disp)

    def apply_preset_config(self, cfg, name):
        for k, v in cfg.items():
            if k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception:
                    pass
        # Composite RGB unpack
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
        # Restore "always" flags. Older presets predate this field, so
        # missing entries fall back to False (legacy behaviour).
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
        # Tune + Quality dropdown. Apply tune from cfg (default 'none' if
        # absent — old presets predate this field). Then trust the saved
        # quality_preset label if present, otherwise re-derive it from
        # the (crf, export_preset, tune) triple. Apply through the guard
        # so traces don't fight each other.
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

    # ─── log helpers ───
    def _clear_log(self):
        self.console.delete('1.0', tk.END)

    def log(self, msg):
        self.console.insert(tk.END, f'[{time.strftime("%H:%M:%S")}] > {msg}\n')
        self.console.see(tk.END)

    # ─── FPS match source ───
    def _fps_match_source(self):
        """Probe the loaded source video for its native FPS and apply it.

        Falls back gracefully (no-op + log) if no video is loaded yet or the
        capture refuses to open. Uses OpenCV directly so this is cheap and
        does not touch the engine's render path.
        """
        if not self.video_paths:
            self.log('Match FPS: no source video loaded yet.')
            return
        import cv2
        cap = cv2.VideoCapture(self.video_paths[0])
        try:
            if not cap.isOpened():
                self.log(f'Match FPS: could not open '
                         f'{os.path.basename(self.video_paths[0])}.')
                return
            src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        finally:
            cap.release()
        if src_fps <= 0.0:
            self.log('Match FPS: source reports zero/unknown FPS.')
            return
        # Round to int — the export pipeline expects an integer. Display the
        # original in the log so the user knows what was rounded if anything.
        rounded = int(round(src_fps))
        self.vars['fps'].set(float(rounded))
        if hasattr(self, 'fps_combo'):
            self.fps_combo.set(str(rounded))
        self.log(f'Match FPS: source {src_fps:.3f} → output {rounded} fps.')

    # ─── render ───
    def _get_preview_seconds(self) -> float:
        """Read preview length from the spinbox, clamped to [1, 90].

        Isolates the engine from any user-typed garbage in the spinbox
        (TclError on `.get()` for non-numeric input). Falls back to 5s
        on any parse failure so a render is still possible.
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
            # Draft is a fixed 5s 480p sanity-check; only PREVIEW honors
            # the user-controlled length so its role stays predictable.
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
            # Derive container extension from the selected codec label so
            # the Save dialog and the ffmpeg sink agree on the file type.
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
            # If user typed a different extension, trust them but warn.
            user_ext = os.path.splitext(out)[1].lower().lstrip('.')
            if user_ext and user_ext != ext:
                self.log(f'WARNING: extension .{user_ext} does not match codec '
                         f'container .{ext} — keeping user extension.')
            cfg['output_path'] = out
            cfg['max_duration'] = None
            self.log('Starting FULL RENDER...')
            self.progress.configure(mode='determinate', value=0)

        if not self.overlay_dir and cfg.get('fx_overlay'):
            self.log('WARNING: Overlay folder not set — overlay effect skipped.')

        for btn in (self.btn_draft, self.btn_preview, self.btn_run_full):
            btn.configure(state='disabled')
        # Cancel button is the inverse: enabled exactly during a render.
        self.btn_cancel_render.configure(state='normal', text='CANCEL RENDER')
        # Stop any running playback before render — the render writes to
        # temp_preview_path, and on Windows a cv2.VideoCapture in the
        # playback loop holds the file open, causing ffmpeg to fail or
        # cv2 to read corrupt data and segfault on the second draft.
        if mode in ('draft', 'preview'):
            self.stop_and_clear_playback()
            # Windows 11 uses the Media Foundation (MSMF) backend for
            # cv2.VideoCapture. MSMF releases the OS file handle
            # asynchronously through MF worker threads AFTER cap.release()
            # returns, so even after the playback thread joins, the handle
            # can still be alive for ~100-200 ms. ffmpeg needs GENERIC_WRITE
            # access to overwrite the file; if MSMF still holds it with
            # FILE_SHARE_READ (no write sharing), ffmpeg gets
            # ERROR_SHARING_VIOLATION and the render silently fails or
            # crashes. Poll via CreateFile until the file is writable.
            if sys.platform == 'win32' and os.path.exists(self.temp_preview_path):
                _wait_file_writable(self.temp_preview_path)
        threading.Thread(target=self._render_thread, args=(cfg,), daemon=True).start()

    def cancel_render(self):
        """Request cooperative abort of the running render.

        Engine checks `self.abort` at segment / frame / pad boundaries
        (see vpc/render/engine.py). Setting the flag from the GUI thread
        is safe — CPython makes attribute writes atomic under the GIL,
        and the worker thread reads the same attribute.

        Note: scene-detection runs synchronously before the segment loop
        and has no abort checkpoint, so cancellation may take up to the
        scene-detect phase to actually fire.
        """
        eng = self.engine
        if eng is not None and not eng.abort:
            eng.abort = True
            self.log('Cancel requested — finishing current frame and closing sink...')
            self.btn_cancel_render.configure(state='disabled', text='CANCELLING...')

    def _render_thread(self, cfg):
        is_preview = cfg['render_mode'] in ('draft', 'preview')

        def on_progress(message=None, value=None):
            if message:
                self.after(0, self.log, message)
            if not is_preview and value is not None:
                self.after(0, self.progress_var.set, value)

        engine = BreakcoreEngine(cfg, progress_callback=on_progress)
        # Publish the engine reference BEFORE engine.run() so a fast Cancel
        # click between thread start and run() entry still lands the abort.
        self.engine = engine
        try:
            ok = engine.run(render_mode=cfg['render_mode'],
                            max_output_duration=cfg.get('max_duration'))
            label = 'PREVIEW' if is_preview else 'FULL RENDER'
            if engine.abort or not ok:
                # Don't auto-delete the partial output — for full renders
                # the user picked the path themselves and could have
                # overwritten an existing file. Just log the path.
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

    # ─── transport (preview player) ───
    def _on_volume_change(self):
        """Volume slider callback. Picked up by _audio_loop on the next
        playback iteration (≤5s — preview is a 5s loop, so this is fine).
        Moving the slider also implicitly un-mutes (matches every other
        media player on the planet)."""
        v = max(0.0, min(1.0, self.var_volume.get() / 100.0))
        if self._muted and v > 0.0:
            self._muted = False
            self.btn_mute.configure(text='MUTE')
        self._volume = v
        if not self._muted:
            self._volume_pre_mute = v

    def _toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.btn_pause.configure(text='PAUSE')
        else:
            self.pause_event.set()
            self.btn_pause.configure(text='PLAY')
            # Cut audio immediately so pause feels instant. The audio loop
            # will re-enter sd.play() on the next unpause iteration.
            if _AUDIO_OK:
                try: _sd.stop()
                except Exception: pass

    def _toggle_mute(self):
        if self._muted:
            # If the user manually dragged the slider to 0 *before* hitting
            # MUTE, _volume_pre_mute is 0 and UNMUTE would silently leave
            # the slider at 0 (looks broken). Snap to a sensible default.
            restore = self._volume_pre_mute if self._volume_pre_mute > 0.01 else 0.8
            self._muted = False
            self._volume = restore
            # Setting var_volume fires _on_volume_change, but the un-mute
            # branch there is a no-op (we already cleared _muted).
            self.var_volume.set(restore * 100.0)
            self.btn_mute.configure(text='MUTE')
        else:
            self._volume_pre_mute = self._volume
            self._muted = True
            self._volume = 0.0
            self.btn_mute.configure(text='UNMUTE')

    # ─── playback ───
    # Settle delay between stop_and_clear_playback() and re-opening the
    # capture / re-spawning threads. Gives the OS a moment to release the
    # previous file handle on the temp preview before we read it again
    # (especially on Windows, where a still-mapped file can refuse a new
    # VideoCapture open). Empirically 150ms is enough.
    _PLAYBACK_RESTART_MS = 150

    def start_playback(self, path):
        """Two-phase start. Phase 1 (here, sync on Tk): tear down any
        previous playback and request controls disabled. Phase 2 runs via
        `after()` so the settle delay doesn't freeze the GUI thread —
        previously this used `time.sleep(0.15)` and visibly stuttered Tk
        on every preview start.
        """
        self.stop_and_clear_playback()
        self.after(self._PLAYBACK_RESTART_MS,
                   lambda: self._start_playback_phase2(path))

    def _start_playback_phase2(self, path):
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release(); self.log('ERROR: Cannot open preview video.')
            return
        cap.release()
        self.video_cap = None
        self._playback_path = path
        self.log('Starting playback (looping)...')
        # Enable transport controls. Mute/Volume only meaningful when the
        # audio backend (sounddevice + soundfile) is available.
        self.btn_pause.configure(state='normal', text='PAUSE')
        self.btn_clear_monitor.configure(state='normal')
        self.btn_mute.configure(state=('normal' if _AUDIO_OK else 'disabled'),
                                text='MUTE')
        self.pause_event.clear()
        self.stop_playback.clear()

        self._audio_wav = None
        if _AUDIO_OK:
            wav_fd, wav_path = tempfile.mkstemp(suffix='.wav')
            os.close(wav_fd)
            try:
                from vpc.render.sink import ffmpeg_bin
                _ff = ffmpeg_bin()
                subprocess.run(
                    [_ff, '-y', '-i', path, '-vn', '-acodec', 'pcm_s16le',
                     '-ar', '44100', '-ac', '2', wav_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                self._audio_wav = wav_path
            except Exception as e:
                self.log(f'WARNING: audio extract failed ({e}); preview will be silent.')
                try: os.remove(wav_path)
                except OSError: pass

        self.playback_thread = threading.Thread(
            target=self._playback_loop, args=(path,), daemon=True)
        self.playback_thread.start()
        if self._audio_wav:
            self._audio_thread = threading.Thread(
                target=self._audio_loop, args=(self._audio_wav,), daemon=True)
            self._audio_thread.start()

    def _playback_loop(self, path):
        W, H = 640, 360
        while not self.stop_playback.is_set():
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                break
            fps = cap.get(cv2.CAP_PROP_FPS) or 24
            frame_dur = 1.0 / fps
            loop_start = time.time(); idx = 0
            while not self.stop_playback.is_set():
                # Pause: freeze the current frame and shift loop_start by
                # the paused duration so frame scheduling stays correct
                # after resume (otherwise the loop "fast-forwards" to make
                # up the missed time and stutters).
                if self.pause_event.is_set():
                    pause_t0 = time.time()
                    while (self.pause_event.is_set()
                           and not self.stop_playback.is_set()):
                        self.stop_playback.wait(0.05)
                    loop_start += time.time() - pause_t0
                    continue
                ret, frame = cap.read()
                if not ret:
                    break
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                img = img.resize((W, H), Image.Resampling.LANCZOS)
                imgtk = ImageTk.PhotoImage(image=img)
                self.after(0, self._show_frame, imgtk)
                idx += 1
                wait = (loop_start + idx * frame_dur) - time.time()
                if wait > 0:
                    self.stop_playback.wait(wait)
            cap.release()

    def _show_frame(self, imgtk):
        if self.stop_playback.is_set():
            return
        self.player_label.imgtk = imgtk
        self.player_label.configure(image=imgtk, text='')

    def _audio_loop(self, wav_path):
        if not _AUDIO_OK:
            return
        try:
            data, sr = _sf.read(wav_path, dtype='float32')
        except Exception as e:
            # Surface decode failure: previously this branch was silent and
            # the audio thread would just exit, leaving the user wondering
            # why preview is muted with the slider untouched.
            self.after(0, self.log,
                       f'ERROR: preview audio decode failed ({e}); preview is silent.')
            return
        dur = len(data) / sr
        while not self.stop_playback.is_set():
            if self.pause_event.is_set():
                self.stop_playback.wait(0.1)
                continue
            try:
                # Per-iteration volume scaling. ~5s of stereo float32 at
                # 44.1kHz is ~1.7 MB; multiply is sub-millisecond. Skip
                # the copy when volume is effectively unity.
                v = self._volume
                if v <= 0.0:
                    # Fully muted — sleep one loop length and re-check.
                    # _sd.stop() is mandatory here: if v dropped to 0
                    # mid-playback (slider → 0 / mute hit), the previous
                    # sd.play buffer would otherwise keep playing at the
                    # OLD gain through to its natural end (~5s ghost).
                    try: _sd.stop()
                    except Exception: pass
                    if self.stop_playback.wait(dur):
                        break
                    continue
                buf = data if v >= 0.999 else (data * v).astype(np.float32)
                _sd.play(buf, sr)
                # Wake periodically so pause/stop/volume-zero respond
                # within ~50ms instead of waiting up to 5s for the loop
                # to expire. Bailing on `_volume <= 0.0` matters: without
                # it, dragging the slider to 0 would let the prior
                # sd.play buffer finish at the ORIGINAL gain (ghost play).
                end_t = time.time() + dur
                while (not self.stop_playback.is_set()
                       and not self.pause_event.is_set()
                       and self._volume > 0.0
                       and time.time() < end_t):
                    self.stop_playback.wait(0.05)
                _sd.stop()
            except Exception as e:
                # Same rationale as the decode-error branch above: a
                # mid-playback PortAudio failure (device unplugged, sample
                # rate refused, etc.) used to silently kill this thread.
                # Log once and exit — the loop is unrecoverable from here
                # because the device handle is what failed.
                self.after(0, self.log,
                           f'ERROR: preview audio playback aborted ({e}).')
                break

    def stop_and_clear_playback(self):
        self.stop_playback.set()
        if _AUDIO_OK:
            try: _sd.stop()
            except Exception: pass
        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=1.0)
        if self._audio_thread and self._audio_thread.is_alive():
            self._audio_thread.join(timeout=1.0)
        if self.video_cap:
            self.video_cap.release(); self.video_cap = None
        wav = getattr(self, '_audio_wav', None)
        if wav and os.path.exists(wav):
            try: os.remove(wav)
            except OSError: pass
        self._audio_wav = None
        self.stop_playback.clear()
        # Reset transport state. _muted/_volume_pre_mute persist across
        # previews intentionally (user-set preference).
        self.pause_event.clear()
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
