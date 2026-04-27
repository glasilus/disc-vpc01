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
from PIL import Image, ImageTk

from vpc.render import BreakcoreEngine
from vpc.registry import EFFECTS, GROUP_ORDER, default_cfg, bi
from vpc.registry import ACCORDION_HIDDEN_GROUPS
from vpc.mystery import MYSTERY_KNOBS
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

# ── Built-in presets (sparse overrides on top of defaults) ──
PRESETS = {
    'Blank (No Effects)': {
        'chaos_level': 0.0, 'threshold': 1.0, 'min_cut_duration': 0.08,
        'scene_buffer_size': 10, 'use_scene_detect': False,
        # Blank wipes the True defaults of pixel_sort + rgb_shift
        'fx_psort': False, 'fx_rgb': False, 'fx_stutter': False, 'fx_flash': False,
    },
    'Drillcore (Fast Cut/Stutter)': {
        'chaos_level': 0.8, 'threshold': 1.0, 'min_cut_duration': 0.05,
        'scene_buffer_size': 5, 'use_scene_detect': False,
        'fx_stutter': True,
        'fx_flash': True, 'fx_flash_chance': 0.5,
        'fx_rgb': True, 'fx_rgb_chance': 0.7,
        'fx_block_glitch': True, 'fx_block_glitch_chance': 0.6,
        'fx_pixel_drift': True, 'fx_pixel_drift_chance': 0.5,
        'fx_zoom_glitch': True, 'fx_zoom_glitch_chance': 0.7,
    },
    'Datamosh (P-frame Bleed)': {
        'chaos_level': 0.3, 'threshold': 1.4, 'min_cut_duration': 0.12,
        'scene_buffer_size': 20, 'use_scene_detect': True,
        'fx_ghost': True, 'fx_ghost_int': 0.6,
        'fx_datamosh': True, 'fx_datamosh_chance': 0.8,
        'fx_colorbleed': True, 'fx_colorbleed_chance': 0.7,
        'fx_freeze_corrupt': True, 'fx_freeze_corrupt_chance': 0.4,
        'fx_feedback': True,
        'fx_echo': True, 'fx_echo_chance': 0.5,
        'fx_spatial_reverb': True, 'fx_spatial_reverb_chance': 0.5, 'fx_spatial_reverb_decay': 0.2,
        'fx_deriv_warp': True, 'fx_deriv_warp_chance': 0.6, 'fx_deriv_warp_blend': 0.4,
    },
    'ASCII Rave': {
        'chaos_level': 0.5, 'threshold': 1.0, 'min_cut_duration': 0.08,
        'scene_buffer_size': 10, 'use_scene_detect': False,
        'fx_ascii': True, 'fx_ascii_chance': 0.9,
        'fx_ascii_size': 10, 'fx_ascii_blend': 0.3,
        'fx_rgb': True, 'fx_rgb_chance': 0.5,
        'fx_scanlines': True, 'fx_scanlines_chance': 0.8,
        'fx_dither': True, 'fx_dither_chance': 0.5,
        'fx_kali': True, 'fx_kali_chance': 0.3,
        'fx_temporal_rgb': True, 'fx_temporal_rgb_lag': 6.0,
        'mystery_DOT': 0.4,
    },
    'Death Grips Mode': {
        'chaos_level': 0.9, 'threshold': 0.9, 'min_cut_duration': 0.04,
        'scene_buffer_size': 5, 'use_scene_detect': False,
        'fx_stutter': True,
        'fx_flash': True, 'fx_flash_chance': 0.8,
        'fx_psort': True, 'fx_psort_chance': 0.5, 'fx_psort_int': 0.5,
        'fx_negative': True, 'fx_negative_chance': 0.3,
        'fx_jpeg_crush': True, 'fx_jpeg_crush_chance': 0.6,
        'fx_bad_signal': True, 'fx_bad_signal_chance': 0.5,
        'fx_vhs': True, 'fx_vhs_chance': 0.4,
        'fx_cascade': True, 'fx_cascade_chance': 0.5,
        'fx_self_displace': True, 'fx_self_displace_chance': 0.5,
    },
    'Rhythm Flash (Scene Mix)': {
        'chaos_level': 0.5, 'threshold': 1.1, 'min_cut_duration': 0.08,
        'scene_buffer_size': 15, 'use_scene_detect': True,
        'fx_flash': True, 'fx_flash_chance': 1.0,
        'fx_bitcrush': True, 'fx_bitcrush_chance': 0.4,
        'fx_fisheye': True, 'fx_fisheye_chance': 0.3,
        'fx_interlace': True, 'fx_interlace_chance': 0.5,
        'fx_phase_shift': True, 'fx_phase_shift_chance': 0.5,
        'fx_overlay': True, 'fx_overlay_chance': 0.4,
        'mystery_RESONANCE': 0.3,
    },
    'Vortex Dream': {
        'chaos_level': 0.4, 'threshold': 1.0, 'min_cut_duration': 0.1,
        'use_scene_detect': True, 'scene_buffer_size': 10,
        'fx_vortex_warp': True, 'fx_vortex_warp_chance': 0.7,
        'fx_fractal_warp': True, 'fx_fractal_warp_chance': 0.5,
        'fx_ghost': True, 'fx_ghost_int': 0.5,
        'fx_temporal_rgb': True, 'fx_temporal_rgb_lag': 10.0,
    },
}


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

        self.style = ttk.Style(self)
        self._setup_styles()
        self._setup_vars()
        self._build_ui()
        self._load_presets_file()

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
        }
        # Export
        export_defaults = {'fps': 24.0, 'crf': 18.0, 'custom_w': 1280.0, 'custom_h': 720.0}
        # Mystery
        mystery_defaults = {f'mystery_{k}': 0.0 for k in
                            ('VESSEL', 'ENTROPY_7', 'DELTA_OMEGA', 'STATIC_MIND',
                             'RESONANCE', 'COLLAPSE', 'ZERO', 'FLESH_K', 'DOT')}

        # Registry defaults
        reg = default_cfg()

        defaults = {**cut_defaults, **reg, **export_defaults, **mystery_defaults}
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
        self.var_silence_mode = tk.StringVar(value='dim')
        self.var_resolution_mode = tk.StringVar(value='preset')
        self.var_formula_expr = tk.StringVar(value='frame')

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
                            '_frames', '_softness', '_tolerance', '_octaves',
                            '_depth')
            is_int = (name in int_keys
                      or any(name.endswith(s) for s in int_suffixes))

            def _make_trace(dv, sv, int_mode):
                def _cb(*_):
                    v = dv.get()
                    sv.set(str(int(round(v))) if int_mode else f'{v:.2f}')
                dv.trace_add('write', _cb)
                _cb()
            _make_trace(dvar, sv, is_int)

    # ─── ui ───
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=200)
        self.grid_columnconfigure(1, weight=3)
        self.grid_columnconfigure(2, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        sidebar = ttk.Frame(self, style='W95.TFrame')
        sidebar.grid(row=0, column=0, padx=(8, 4), pady=8, sticky='nsew')
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
        self.btn_preview = ttk.Button(rf, text='PREVIEW  (5 sec)',
                                      command=lambda: self.run('preview'),
                                      style='Preview.TButton')
        self.btn_preview.pack(fill='x', pady=2, ipady=4)
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
        formula_frame = tk.Frame(content_host, bg=C_TUI_BG)
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

        # Right panel: preview + console
        right = ttk.Frame(self, style='W95.TFrame')
        right.grid(row=0, column=2, padx=(4, 8), pady=8, sticky='nsew')
        tb2 = tk.Frame(right, bg=C_TITLE_BAR, height=28)
        tb2.pack(fill='x')
        tk.Label(tb2, text='Live Preview & Console',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 11, 'bold')).pack(side='left', padx=6, pady=3)
        cr = tk.Frame(right, bg=C_SILVER)
        cr.pack(fill='both', expand=True, padx=2, pady=2)
        pmon = tk.LabelFrame(cr, text='Preview Monitor (640×360)',
                             bg=C_SILVER, fg=C_TEXT, bd=2, relief='sunken',
                             font=('MS Sans Serif', 10, 'bold'))
        pmon.pack(fill='x', padx=8, pady=6)
        _blank = ImageTk.PhotoImage(Image.new('RGB', (640, 360), 'black'))
        self.player_label = tk.Label(pmon, image=_blank, bg=C_BLACK, bd=2, relief='sunken')
        self.player_label.imgtk = _blank
        self.player_label.pack(padx=4, pady=4)
        self.progress = ttk.Progressbar(cr, style='green.W95.Horizontal.TProgressbar',
                                        mode='determinate', maximum=100,
                                        variable=self.progress_var)
        self.progress.pack(fill='x', padx=8, pady=3)
        cp = tk.LabelFrame(cr, text='Status Log', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='sunken', font=('MS Sans Serif', 10, 'bold'))
        cp.pack(fill='both', expand=True, padx=8, pady=4)
        cb = tk.Frame(cp, bg=C_SILVER)
        cb.pack(fill='x', padx=4, pady=(4, 0))
        ttk.Button(cb, text='Clear Log', style='W95.TButton',
                   command=self._clear_log).pack(side='right', padx=2)
        self.console = tk.Text(cp, height=8, font=('Courier New', 9),
                               bg=C_WHITE, fg=C_BLACK, bd=2, relief='sunken')
        self.console.pack(fill='both', expand=True, padx=4, pady=4)
        self.btn_stop = ttk.Button(cr, text='STOP',
                                   command=self.stop_and_clear_playback,
                                   style='Stop.TButton', state='disabled')
        self.btn_stop.pack(fill='x', padx=8, pady=(2, 6), ipady=4)

    # ─── source files / presets / tabs ───
    def _build_source_files(self, parent):
        fp = tk.LabelFrame(parent, text='Source Files', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='groove', font=('MS Sans Serif', 10, 'bold'))
        fp.pack(pady=4, padx=6, fill='x')
        ar = tk.Frame(fp, bg=C_SILVER); ar.pack(fill='x', padx=6, pady=(4, 0))
        self._audio_dot = tk.Label(ar, text='●', fg='#AAAAAA', bg=C_SILVER, font=('MS Sans Serif', 12))
        self._audio_dot.pack(side='left', padx=(0, 4))
        ttk.Button(ar, text='Load Audio (WAV / MP3)',
                   command=self.sel_audio, style='W95.TButton').pack(side='left', fill='x', expand=True)
        self.lbl_audio_name = tk.Label(fp, text='— not loaded —',
                                       bg=C_SILVER, fg=C_DARK_GRAY,
                                       font=('Courier New', 9), anchor='w')
        self.lbl_audio_name.pack(fill='x', padx=24, pady=(0, 3))

        vr = tk.Frame(fp, bg=C_SILVER); vr.pack(fill='x', padx=6, pady=(2, 0))
        self._video_dot = tk.Label(vr, text='●', fg='#AAAAAA', bg=C_SILVER, font=('MS Sans Serif', 12))
        self._video_dot.pack(side='left', padx=(0, 4))
        ttk.Button(vr, text='Load Source Video',
                   command=self.sel_video, style='W95.TButton').pack(side='left', fill='x', expand=True)
        self.lbl_video_name = tk.Label(fp, text='— not loaded —',
                                       bg=C_SILVER, fg=C_DARK_GRAY,
                                       font=('Courier New', 9), anchor='w')
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

        br = tk.Frame(pp, bg=C_SILVER); br.pack(fill='x', padx=4, pady=2)
        ttk.Button(br, text='Load', style='W95.TButton',
                   command=self._load_selected_preset).pack(side='left', padx=2)
        ttk.Button(br, text='Save Current', style='W95.TButton',
                   command=self._save_current_preset).pack(side='left', padx=2)
        ttk.Button(br, text='Delete', style='W95.TButton',
                   command=self._delete_preset).pack(side='left', padx=2)
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
    def _row_with_help(self, parent, text, tooltip='', mono=False):
        """Label + small [?] help icon to the right; both carry the tooltip."""
        row = tk.Frame(parent, bg=C_SILVER)
        row.pack(fill='x', padx=(8, 8), pady=(2, 0))
        f = ('Courier New', 9, 'bold') if mono else ('MS Sans Serif', 9)
        lbl = tk.Label(row, text=text, bg=C_SILVER, fg=C_TEXT, font=f, anchor='w')
        lbl.pack(side='left')
        if tooltip:
            help_lbl = tk.Label(row, text='[?]', bg=C_SILVER, fg='#3060A0',
                                cursor='question_arrow',
                                font=('MS Sans Serif', 8, 'bold'))
            help_lbl.pack(side='left', padx=(4, 0))
            Tooltip(lbl, tooltip); Tooltip(help_lbl, tooltip)
        return row

    def _slider(self, parent, name, lo, hi, indent=False):
        """Standalone slider with header showing live numeric value."""
        pad = 20 if indent else 8
        f = tk.Frame(parent, bg=C_SILVER)
        f.pack(fill='x', padx=(pad, 8), pady=(0, 2))
        if name in self._display_vars:
            tk.Label(f, textvariable=self._display_vars[name],
                     bg=C_SILVER, fg=C_TEXT,
                     font=('MS Sans Serif', 9, 'bold'),
                     width=7, anchor='e').pack(side='right')
        ttk.Scale(f, from_=lo, to=hi, variable=self.vars[name],
                  orient=tk.HORIZONTAL, style='W95.Horizontal.TScale').pack(
            fill='x', side='right', expand=True)

    def _combo(self, parent, name, values, indent=False):
        pad = 20 if indent else 8
        f = tk.Frame(parent, bg=C_SILVER)
        f.pack(fill='x', padx=(pad, 8), pady=(0, 2))
        ttk.Combobox(f, values=values, textvariable=self.vars[name],
                     style='W95.TCombobox', width=14).pack(side='left')

    # ─── effects accordion (registry-driven) ───
    def _build_effects_accordion(self, parent):
        for w in parent.winfo_children():
            w.destroy()
        outer = tk.Frame(parent, bg=C_SILVER)
        outer.pack(fill='both', expand=True)
        canvas = tk.Canvas(outer, bg=C_SILVER, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        cf = tk.Frame(canvas, bg=C_SILVER)
        cf_window = canvas.create_window((0, 0), window=cf, anchor='nw')
        cf.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfig(cf_window, width=e.width))
        canvas.bind_all('<MouseWheel>',
                        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units'))

        # CUT LOGIC group — manual fields (these are not effects, but cfg knobs)
        body = self._acc_group(cf, 'CUT LOGIC', open=True)
        self._build_cut_logic(body)

        # Generated effect groups
        # Bucket effects by group
        by_group = {}
        for spec in EFFECTS:
            by_group.setdefault(spec.group, []).append(spec)
        for group_name in GROUP_ORDER:
            if group_name in ACCORDION_HIDDEN_GROUPS:
                continue
            specs = by_group.get(group_name)
            if not specs:
                continue
            opened = group_name in ('CORE FX',)
            body = self._acc_group(cf, group_name, open=opened)
            for s in specs:
                self._build_effect_block(body, s)
            if group_name == 'OVERLAYS':
                self._build_overlay_dir_picker(body)

    def _acc_group(self, parent, title, open=False):
        g = tk.Frame(parent, bg=C_SILVER, bd=1, relief='solid')
        g.pack(fill='x', padx=4, pady=2)
        hdr = tk.Frame(g, bg=C_TITLE_BAR if open else C_SILVER, cursor='hand2')
        hdr.pack(fill='x')
        arrow = tk.StringVar(value='▼' if open else '▶')
        ar_l = tk.Label(hdr, textvariable=arrow,
                        bg=hdr['bg'], fg=C_WHITE if open else C_TEXT,
                        font=('MS Sans Serif', 9))
        ar_l.pack(side='left', padx=4)
        t_l = tk.Label(hdr, text=title, bg=hdr['bg'],
                       fg=C_WHITE if open else C_TEXT,
                       font=('MS Sans Serif', 10, 'bold'))
        t_l.pack(side='left', pady=4)
        body = tk.Frame(g, bg=C_WHITE, bd=1, relief='sunken')
        if open:
            body.pack(fill='x')

        def _toggle(_e=None):
            if body.winfo_ismapped():
                body.pack_forget()
                hdr.configure(bg=C_SILVER); ar_l.configure(bg=C_SILVER, fg=C_TEXT); t_l.configure(bg=C_SILVER, fg=C_TEXT)
                arrow.set('▶')
            else:
                body.pack(fill='x')
                hdr.configure(bg=C_TITLE_BAR); ar_l.configure(bg=C_TITLE_BAR, fg=C_WHITE); t_l.configure(bg=C_TITLE_BAR, fg=C_WHITE)
                arrow.set('▼')
        for w in (hdr, ar_l, t_l):
            w.bind('<Button-1>', _toggle)
        return body

    def _build_cut_logic(self, body):
        self._row_with_help(body, 'Smart Scene Detection', bi(
            'Detects scene changes in the source video and prefers to start segments at those '
            'cuts. Off = uniform random sampling.',
            'Находит смены сцен в исходном видео и предпочитает стартовать сегменты с этих '
            'точек. Выкл — равномерная случайная выборка.'))
        ttk.Checkbutton(body, text='Detect scene cuts',
                        variable=self.vars['use_scene_detect'],
                        style='W95.TCheckbutton').pack(anchor='w', padx=24, pady=(0, 4))

        sliders = [
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
            ('Min Cut Duration (sec)', 'min_cut_duration', 0.0, 0.3, bi(
                'Drops segments shorter than this. Higher = calmer pacing.',
                'Отбрасывает сегменты короче этого значения. Больше — спокойнее темп.')),
            ('Scene Buffer Size', 'scene_buffer_size', 2, 30, bi(
                'How many detected scene cuts to keep around as candidates.',
                'Сколько найденных точек смены сцен держать в пуле кандидатов.')),
        ]
        for lbl, key, lo, hi, tt in sliders:
            self._row_with_help(body, lbl, tt)
            self._slider(body, key, lo, hi)

        self._row_with_help(body, 'Snap Cuts to Beat Grid', bi(
            'After onset detection, pull each onset to the nearest beat within tolerance. '
            'Improves rhythmic precision; required for tight drillcore sync.',
            'После детекции онсетов притягивает каждый онсет к ближайшему биту в пределах '
            'tolerance. Улучшает ритмическую точность; обязательно для плотного drillcore.'))
        ttk.Checkbutton(body, text='Snap onsets to beat grid',
                        variable=self.vars['snap_to_beat'],
                        style='W95.TCheckbutton').pack(anchor='w', padx=24, pady=(0, 2))
        self._row_with_help(body, 'Beat Snap Tolerance (sec)', bi(
            'Maximum onset→beat distance for snapping. Larger = more onsets pulled to grid but '
            'at the cost of micro-rhythm.',
            'Максимальное расстояние онсет→бит для снэпа. Больше — больше онсетов прилипает к '
            'сетке, но теряется микро-ритмика.'))
        self._slider(body, 'snap_tolerance', 0.01, 0.15, indent=True)

        # Silence
        self._row_with_help(body, 'Silence Treatment', bi(
            'How long (>1s) silent stretches are rendered: dim, soft blur, both, or untouched.',
            'Как обрабатывать длинные (>1 с) тихие участки: затемнение, размытие, оба варианта '
            'или без обработки.'))
        sf = tk.Frame(body, bg=C_WHITE)
        sf.pack(fill='x', padx=20, pady=(2, 6))
        for val, lbl in [('dim', 'Dim'), ('blur', 'Blur'), ('both', 'Both'), ('none', 'None')]:
            tk.Radiobutton(sf, text=lbl, variable=self.var_silence_mode, value=val,
                           bg=C_WHITE, fg=C_TEXT, selectcolor=C_WHITE,
                           font=('MS Sans Serif', 9)).pack(side='left', padx=4)

    def _build_effect_block(self, parent, spec):
        """Build the GUI block for one EffectSpec."""
        # Header row: checkbox + label + tooltip
        hr = tk.Frame(parent, bg=C_WHITE)
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

        # Per-effect "always-on" override (backlog #1).
        # Disables triggers / chance for this effect only — others stay normal.
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
            tk.Label(parent, text=spec.note, bg=C_WHITE, fg=C_DARK_GRAY,
                     font=('MS Sans Serif', 7, 'italic')).pack(
                anchor='w', padx=22, pady=(0, 2))

        # Chance slider (if any)
        if spec.chance_key is not None:
            self._row_with_help(parent, 'Chance', bi(
                'Probability the effect fires per qualifying frame. Scaled by Global Chaos.',
                'Вероятность срабатывания эффекта на подходящем кадре. Масштабируется ползунком '
                'Global Chaos.'))
            self._slider(parent, spec.chance_key, 0.0, 1.0, indent=True)

        # Always-on intensity slider (only meaningful when always-on is checked)
        if spec.supports_always_for_chain():
            self._row_with_help(parent, 'Always-on intensity', bi(
                'Fixed intensity used while "always" is ON. Has no effect otherwise.',
                'Фиксированная интенсивность, когда чекбокс «always» включён. В обычном режиме '
                'не используется.'))
            self._slider(parent, spec.always_int_key, 0.0, 1.0, indent=True)

        # Per-param controls
        for p in spec.params:
            if p.kind == 'choice':
                self._row_with_help(parent, p.label, p.tooltip)
                self._combo(parent, p.key, p.choices, indent=True)
            elif p.kind == 'string':
                self._row_with_help(parent, p.label, p.tooltip)
                row = tk.Frame(parent, bg=C_WHITE)
                row.pack(fill='x', padx=20, pady=2)
                ent = ttk.Entry(row, textvariable=self.vars[p.key])
                ent.pack(fill='x')
            else:
                self._row_with_help(parent, p.label, p.tooltip)
                self._slider(parent, p.key, p.lo, p.hi, indent=True)

        tk.Frame(parent, bg=C_DARK_GRAY, height=1).pack(fill='x', padx=4)

    def _build_overlay_dir_picker(self, body):
        bf = tk.Frame(body, bg=C_WHITE)
        bf.pack(fill='x', padx=10, pady=(4, 8))
        ttk.Button(bf, text='Select Overlay Folder...',
                   command=self.sel_ov, style='W95.TButton').pack(fill='x')
        self.lbl_overlay_dir = tk.Label(bf, text='No folder selected',
                                        bg=C_WHITE, fg=C_DARK_GRAY,
                                        font=('Courier New', 9))
        self.lbl_overlay_dir.pack(anchor='w', pady=(2, 0))

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

        # CRF / codec / preset
        self._row_with_help(wr, 'Quality CRF', bi(
            '0 = lossless, 18 = visually lossless, 28 = small files, 51 = artifact art.',
            '0 — без потерь, 18 — визуально без потерь, 28 — малый размер, 51 — арт из '
            'артефактов.'))
        self._slider(wr, 'crf', 0, 51)

        self._row_with_help(wr, 'Codec', bi(
            'H.264 = universal. H.265 = smaller files, slower encode, less compatible.',
            'H.264 — универсально. H.265 — меньше файл, медленнее кодирование, хуже '
            'совместимость.'))
        cf = tk.Frame(wr, bg=C_SILVER); cf.pack(fill='x', padx=20, pady=2)
        self.fmt_combo = ttk.Combobox(cf, values=['H.264 (MP4)', 'H.265 (MP4)'],
                                      style='W95.TCombobox', width=14)
        self.fmt_combo.set('H.264 (MP4)'); self.fmt_combo.pack(side='left', padx=4)

        self._row_with_help(wr, 'ffmpeg Preset', bi(
            'ultrafast = quick test, slow = best compression.',
            'ultrafast — быстрая проверка, slow — лучшее сжатие.'))
        ef = tk.Frame(wr, bg=C_SILVER); ef.pack(fill='x', padx=20, pady=2)
        self.preset_enc_combo = ttk.Combobox(
            ef, values=['ultrafast', 'fast', 'medium', 'slow'],
            style='W95.TCombobox', width=12)
        self.preset_enc_combo.set('medium'); self.preset_enc_combo.pack(side='left', padx=4)

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

    def _tui_label(self, parent, text, *, fg=None, font=None, **kw):
        return tk.Label(parent, text=text,
                        bg=C_TUI_BG, fg=fg or C_TUI_FG,
                        font=font or ('Courier New', 9),
                        **kw)

    def _build_formula_panel(self, parent):
        """Dedicated 'FORMULA' tab — TUI-styled editor for the user formula effect."""
        for w in parent.winfo_children():
            w.destroy()
        parent.configure(bg=C_TUI_BG)

        # ── Header ───────────────────────────────────────────────────
        header = tk.Frame(parent, bg=C_TUI_BG)
        header.pack(fill='x', padx=10, pady=(10, 4))
        self._tui_label(
            header, '┌─ FORMULA  ───  user-defined math expression effect',
            fg=C_TUI_AMBER, font=('Courier New', 11, 'bold')
        ).pack(anchor='w')
        self._tui_label(
            header,
            '  type a NumPy expression returning an HxWx3 uint8 frame.  '
            'available vars: frame  r,g,b  x,y  t  i  a,b,c,d  np  cv2',
            fg=C_TUI_DIM, font=('Courier New', 8)
        ).pack(anchor='w')
        self._tui_label(
            header,
            '  введите NumPy-выражение, возвращающее кадр HxWx3 uint8.  '
            'доступные переменные те же.',
            fg=C_TUI_DIM, font=('Courier New', 8)
        ).pack(anchor='w')

        # ── Top row: enable + chance + blend ─────────────────────────
        ctl = tk.Frame(parent, bg=C_TUI_BG)
        ctl.pack(fill='x', padx=14, pady=(6, 4))

        en_frame = tk.Frame(ctl, bg=C_TUI_BG)
        en_frame.pack(side='left')
        self._tui_label(en_frame, '[ enable ]', fg=C_TUI_AMBER).pack(side='left')
        cb = tk.Checkbutton(
            en_frame, variable=self.vars['fx_formula'],
            bg=C_TUI_BG, fg=C_TUI_FG, selectcolor=C_TUI_BG,
            activebackground=C_TUI_BG, activeforeground=C_TUI_FG,
            highlightthickness=0, bd=0)
        cb.pack(side='left', padx=2)

        ch_frame = tk.Frame(ctl, bg=C_TUI_BG)
        ch_frame.pack(side='left', padx=20)
        self._tui_label(ch_frame, 'chance', fg=C_TUI_AMBER).pack(side='left')
        self._tui_slider(ch_frame, 'fx_formula_chance', 0.0, 1.0, length=140)

        bl_frame = tk.Frame(ctl, bg=C_TUI_BG)
        bl_frame.pack(side='left', padx=20)
        self._tui_label(bl_frame, 'blend', fg=C_TUI_AMBER).pack(side='left')
        self._tui_slider(bl_frame, 'fx_formula_blend', 0.0, 1.0, length=140)

        # ── Editor box ───────────────────────────────────────────────
        editor_box = tk.Frame(parent, bg=C_TUI_DIM, bd=1)
        editor_box.pack(fill='x', padx=12, pady=(8, 0))
        self._tui_label(editor_box, ' EDITOR ',
                        fg=C_TUI_BG, font=('Courier New', 8, 'bold')
                        ).pack(side='top', anchor='w', padx=4)
        editor_inner = tk.Frame(editor_box, bg=C_TUI_BG)
        editor_inner.pack(fill='x', padx=2, pady=2)

        self.formula_text = tk.Text(
            editor_inner, height=10, font=('Courier New', 11),
            bg=C_TUI_BG, fg=C_TUI_FG, insertbackground=C_TUI_FG,
            selectbackground=C_TUI_DIM, selectforeground=C_TUI_BG,
            bd=0, relief='flat', wrap='none', undo=True)
        self.formula_text.pack(side='left', fill='both', expand=True, padx=4, pady=4)
        # Initial content from var
        initial = self.vars['fx_formula_expr'].get() or 'frame'
        self.formula_text.insert('1.0', initial)
        # Sync editor → StringVar on every keystroke + recompile for status
        self.formula_text.bind('<KeyRelease>', self._on_formula_text_changed)
        self.formula_text.bind('<<Modified>>', self._on_formula_text_changed)

        # Status line
        self.formula_status_var = tk.StringVar(value='')
        status = tk.Frame(parent, bg=C_TUI_BG)
        status.pack(fill='x', padx=14, pady=(2, 6))
        self._tui_label(status, '> ', fg=C_TUI_DIM).pack(side='left')
        self.formula_status_label = tk.Label(
            status, textvariable=self.formula_status_var,
            bg=C_TUI_BG, fg=C_TUI_FG, font=('Courier New', 9),
            anchor='w')
        self.formula_status_label.pack(side='left', fill='x', expand=True)
        self._update_formula_status()

        # ── Live params a / b / c / d ─────────────────────────────────
        params_box = tk.Frame(parent, bg=C_TUI_BG)
        params_box.pack(fill='x', padx=12, pady=(4, 4))
        self._tui_label(
            params_box,
            '[ live params — referenced in formula as a, b, c, d ]',
            fg=C_TUI_AMBER).pack(anchor='w', padx=2)
        grid = tk.Frame(params_box, bg=C_TUI_BG)
        grid.pack(fill='x', pady=2)
        for col, (letter, key) in enumerate([
            ('a', 'fx_formula_a'), ('b', 'fx_formula_b'),
            ('c', 'fx_formula_c'), ('d', 'fx_formula_d'),
        ]):
            cell = tk.Frame(grid, bg=C_TUI_BG)
            cell.grid(row=0, column=col, sticky='ew', padx=8)
            grid.grid_columnconfigure(col, weight=1)
            self._tui_label(cell, f' {letter}', fg=C_TUI_AMBER,
                            font=('Courier New', 11, 'bold')).pack(side='left')
            if key in self._display_vars:
                self._tui_label(cell, '', fg=C_TUI_FG).pack_forget()
                tk.Label(cell, textvariable=self._display_vars[key],
                         bg=C_TUI_BG, fg=C_TUI_FG,
                         font=('Courier New', 9), width=5, anchor='e'
                         ).pack(side='right')
            self._tui_slider(cell, key, 0.0, 1.0, length=160)

        # ── Snippets (click to insert) ───────────────────────────────
        sn_box = tk.Frame(parent, bg=C_TUI_BG)
        sn_box.pack(fill='x', padx=12, pady=(8, 4))
        self._tui_label(sn_box, '[ snippets — click to load into the editor ]',
                        fg=C_TUI_AMBER).pack(anchor='w', padx=2)
        sn_grid = tk.Frame(sn_box, bg=C_TUI_BG)
        sn_grid.pack(fill='x', pady=2)
        for i, (label, expr) in enumerate(self.FORMULA_SNIPPETS):
            row, col = divmod(i, 5)
            btn = tk.Button(
                sn_grid, text=label,
                command=lambda e=expr: self._formula_load_snippet(e),
                bg=C_TUI_HL, fg=C_TUI_FG, activebackground=C_TUI_DIM,
                activeforeground=C_TUI_BG,
                font=('Courier New', 9), bd=1, relief='solid',
                highlightthickness=0, cursor='hand2', width=14, pady=2)
            btn.grid(row=row, column=col, padx=3, pady=3, sticky='ew')
            sn_grid.grid_columnconfigure(col, weight=1)

        # ── Reference block ──────────────────────────────────────────
        ref = tk.Frame(parent, bg=C_TUI_BG)
        ref.pack(fill='x', padx=12, pady=(8, 4))
        self._tui_label(ref, '[ reference / справка ]', fg=C_TUI_AMBER).pack(anchor='w', padx=2)
        ref_text = (
            '  vars / переменные  :  frame (HxWx3 uint8)   r,g,b (HxW uint8)   x,y (HxW float32)\n'
            '                      :  t (segment time, sec)  i (intensity 0..1)  a,b,c,d (sliders 0..1)\n'
            '  funcs / функции    :  np  cv2  sin cos tan abs clip sqrt exp log  pi\n'
            '  output / результат :  HxWx3 uint8 (auto-broadcast & clip; ошибки тихо возвращают кадр)\n'
            '  examples           :  255 - frame                            # invert / инверт\n'
            '                      :  np.roll(frame, int(20*np.sin(t*5)), 1)# horizontal slide\n'
            '                      :  cv2.GaussianBlur(frame, (15,15), 0)   # размытие через cv2'
        )
        tk.Label(ref, text=ref_text, bg=C_TUI_BG, fg=C_TUI_DIM,
                 font=('Courier New', 8), justify='left', anchor='w'
                 ).pack(anchor='w', padx=8, pady=(2, 0))

        # ── Footer ───────────────────────────────────────────────────
        footer = tk.Frame(parent, bg=C_TUI_BG)
        footer.pack(fill='x', padx=12, pady=(6, 10), side='bottom')
        self._tui_label(
            footer,
            'note: save / share your formula by saving a Preset — '
            "the expression and a/b/c/d live in the preset's config.",
            fg=C_TUI_DIM, font=('Courier New', 8, 'italic')
        ).pack(anchor='w')
        self._tui_label(
            footer,
            'примечание: чтобы сохранить или поделиться формулой — сохраните Preset; '
            'выражение и значения a/b/c/d живут в конфиге пресета.',
            fg=C_TUI_DIM, font=('Courier New', 8, 'italic')
        ).pack(anchor='w')

    def _tui_slider(self, parent, key, lo, hi, length=160):
        """A ttk.Scale with TUI palette (the global ttk style is reused)."""
        ttk.Scale(parent, from_=lo, to=hi, variable=self.vars[key],
                  orient=tk.HORIZONTAL, length=length).pack(side='left', padx=4)

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
            self.formula_status_var.set('OK    compiled clean — formula ready')
            self.formula_status_label.configure(fg=C_TUI_FG)
        else:
            self.formula_status_var.set(err)
            self.formula_status_label.configure(fg=C_TUI_RED)

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
        for label, key in MYSTERY_KNOBS:
            self._row_with_help(wr, label, '?', mono=True)
            self._slider(wr, key, 0.0, 1.0)

    # ─── file selection ───
    def sel_audio(self):
        p = filedialog.askopenfilename(filetypes=[('Audio', '*.mp3 *.wav')])
        if p:
            self.audio_path = p
            self.lbl_audio_name.configure(text=os.path.basename(p))
            self._audio_dot.configure(fg=C_GREEN_DOT)

    def sel_video(self):
        ps = filedialog.askopenfilenames(
            title='Select Video Source(s)',
            filetypes=[('Video', '*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.mpg *.mpeg')])
        if ps:
            self.video_paths = list(ps)
            n = len(self.video_paths)
            self.lbl_video_name.configure(
                text=os.path.basename(self.video_paths[0]) if n == 1
                else f'{n} files loaded')
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
        cfg['crf'] = int(cfg.get('crf', 18))
        cfg['fx_ascii_size'] = int(cfg.get('fx_ascii_size', 12))
        cfg['custom_w'] = int(cfg.get('custom_w', 1280))
        cfg['custom_h'] = int(cfg.get('custom_h', 720))

        cfg['resolution'] = self.res_combo.get() if hasattr(self, 'res_combo') else '720p'
        cfg['resolution_mode'] = self.var_resolution_mode.get()
        cfg['export_preset'] = self.preset_enc_combo.get() if hasattr(self, 'preset_enc_combo') else 'medium'
        cfg['video_codec'] = self.fmt_combo.get() if hasattr(self, 'fmt_combo') else 'H.264 (MP4)'
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

        cfg['mystery'] = {k: float(cfg.pop(f'mystery_{k}', 0.0))
                          for k in ('VESSEL', 'ENTROPY_7', 'DELTA_OMEGA',
                                    'STATIC_MIND', 'RESONANCE', 'COLLAPSE',
                                    'ZERO', 'FLESH_K', 'DOT')}
        return cfg

    def apply_preset(self, name):
        # Reset to defaults first
        for k, v in self._defaults_all.items():
            if k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception:
                    pass
        self.var_silence_mode.set('dim')
        self.var_resolution_mode.set('preset')
        # Apply overrides
        for key, val in PRESETS.get(name, {}).items():
            if key in self.vars:
                self.vars[key].set(val)
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

    def _generate_builtin_presets_file(self):
        self._user_presets = []
        for name in PRESETS:
            self.res_combo.set('720p')
            self.preset_enc_combo.set('medium')
            self.apply_preset(name)
            cfg = self.get_current_config()
            self._user_presets.append({'name': name, 'builtin': True, 'config': cfg})
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
        self.var_silence_mode.set(cfg.get('silence_mode', 'dim'))
        self.var_resolution_mode.set(cfg.get('resolution_mode', 'preset'))
        self._sync_formula_editor_from_var()
        self.res_combo.set(cfg.get('resolution', '720p'))
        self.preset_enc_combo.set(cfg.get('export_preset', 'medium'))
        if hasattr(self, 'fmt_combo') and cfg.get('video_codec'):
            self.fmt_combo.set(cfg['video_codec'])
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

    # ─── render ───
    def run(self, mode='final'):
        if not self.audio_path or not self.video_paths:
            self.log('ERROR: Select Audio and Video source!')
            return
        cfg = self.get_current_config()
        cfg['audio_path'] = self.audio_path
        cfg['video_paths'] = self.video_paths
        cfg['overlay_dir'] = self.overlay_dir

        if mode in ('draft', 'preview'):
            cfg['render_mode'] = mode
            cfg['max_duration'] = 5.0
            cfg['output_path'] = self.temp_preview_path
            label = 'DRAFT (5 sec · 480p)' if mode == 'draft' else 'PREVIEW (5 sec)'
            self.log(f'Starting {label}...')
            self.progress.configure(mode='indeterminate'); self.progress.start(10)
        else:
            cfg['render_mode'] = 'final'
            out = filedialog.asksaveasfilename(
                defaultextension='.mp4', filetypes=[('MP4', '*.mp4')],
                initialfile=f'disc_{random.randint(1000, 9999)}.mp4')
            if not out:
                return
            cfg['output_path'] = out
            cfg['max_duration'] = None
            self.log('Starting FULL RENDER...')
            self.progress.configure(mode='determinate', value=0)

        if not self.overlay_dir and cfg.get('fx_overlay'):
            self.log('WARNING: Overlay folder not set — overlay effect skipped.')

        for btn in (self.btn_draft, self.btn_preview, self.btn_run_full):
            btn.configure(state='disabled')
        threading.Thread(target=self._render_thread, args=(cfg,), daemon=True).start()

    def _render_thread(self, cfg):
        is_preview = cfg['render_mode'] in ('draft', 'preview')

        def on_progress(message=None, value=None):
            if message:
                self.after(0, self.log, message)
            if not is_preview and value is not None:
                self.after(0, self.progress_var.set, value)

        engine = BreakcoreEngine(cfg, progress_callback=on_progress)
        try:
            engine.run(render_mode=cfg['render_mode'],
                       max_output_duration=cfg.get('max_duration'))
            self.after(0, self.log,
                       f"--- {'PREVIEW' if is_preview else 'FULL RENDER'} COMPLETE: "
                       f"{cfg['output_path']} ---")
            if is_preview:
                self.after(0, self.start_playback, self.temp_preview_path)
        except Exception as e:
            self.after(0, self.log, f'ERROR: {e}')
        finally:
            for btn in (self.btn_draft, self.btn_preview, self.btn_run_full):
                self.after(0, lambda b=btn: b.configure(state='normal'))
            self.after(0, self.progress.stop)
            self.after(0, self.progress_var.set, 0)
            if is_preview:
                self.after(0, self.progress.configure,
                           {'mode': 'determinate', 'value': 0})

    # ─── playback ───
    def start_playback(self, path):
        self.stop_and_clear_playback()
        time.sleep(0.15)
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release(); self.log('ERROR: Cannot open preview video.')
            return
        cap.release()
        self.video_cap = None
        self._playback_path = path
        self.log('Starting playback (looping)...')
        self.btn_stop.configure(state='normal')
        self.stop_playback.clear()

        self._audio_wav = None
        if _AUDIO_OK:
            wav_fd, wav_path = tempfile.mkstemp(suffix='.wav')
            os.close(wav_fd)
            try:
                try:
                    import imageio_ffmpeg as _iio
                    _ff = _iio.get_ffmpeg_exe()
                except Exception:
                    _ff = 'ffmpeg'
                subprocess.run(
                    [_ff, '-y', '-i', path, '-vn', '-acodec', 'pcm_s16le',
                     '-ar', '44100', '-ac', '2', wav_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                self._audio_wav = wav_path
            except Exception:
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
        except Exception:
            return
        while not self.stop_playback.is_set():
            try:
                _sd.play(data, sr)
                self.stop_playback.wait(len(data) / sr)
                _sd.stop()
            except Exception:
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
        self.btn_stop.configure(state='disabled')
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
