import random
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox, ttk
import threading
import os
import json
import subprocess
import tempfile
import cv2
from PIL import Image, ImageTk
import time

from engine import BreakcoreEngine

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

# --- COLOURS ---
C_SILVER    = '#C0C0C0'
C_DARK_GRAY = '#808080'
C_BLACK     = '#000000'
C_WHITE     = '#FFFFFF'
C_TITLE_BAR = '#000080'
C_TEXT      = '#000000'
C_BLUE_LIGHT = '#D0D8F0'
C_GREEN_DOT  = '#00AA00'
C_RED_BTN    = '#CC2222'

# --- PRESETS ---
PRESETS = {
    "Drillcore (Fast Cut/Stutter)": {
        'chaos_level': 0.8, 'threshold': 1.0, 'min_cut_duration': 0.05,
        'scene_buffer_size': 5, 'use_scene_detect': False,
        'fx_stutter': True,
        'fx_flash': True, 'fx_flash_chance': 0.5,
        'fx_rgb': True, 'fx_rgb_chance': 0.7,
        'fx_psort': True, 'fx_psort_chance': 0.2, 'fx_psort_int': 0.3,
        'fx_block_glitch': True, 'fx_block_glitch_chance': 0.6,
        'fx_zoom_glitch': True, 'fx_zoom_glitch_chance': 0.7,
        'fx_pixel_drift': True, 'fx_pixel_drift_chance': 0.4,
    },
    "Datamosh (P-frame Bleed)": {
        'chaos_level': 0.4, 'threshold': 1.4, 'min_cut_duration': 0.1,
        'scene_buffer_size': 20, 'use_scene_detect': True,
        'fx_datamosh': True, 'fx_datamosh_chance': 0.8,
        'fx_psort': True, 'fx_psort_chance': 0.5, 'fx_psort_int': 0.6,
        'fx_feedback': True,
        'fx_ghost': True, 'fx_ghost_int': 0.6,
        'fx_colorbleed': True, 'fx_colorbleed_chance': 0.7,
    },
    "ASCII Rave": {
        'chaos_level': 0.6, 'threshold': 1.0, 'min_cut_duration': 0.08,
        'scene_buffer_size': 10, 'use_scene_detect': False,
        'fx_ascii': True, 'fx_ascii_chance': 0.9, 'fx_ascii_size': 10, 'fx_ascii_blend': 0.3,
        'fx_stutter': True,
        'fx_flash': True, 'fx_flash_chance': 0.6,
        'fx_rgb': True, 'fx_rgb_chance': 0.5,
        'fx_scanlines': True, 'fx_scanlines_chance': 0.8,
    },
    "Death Grips Mode": {
        'chaos_level': 0.9, 'threshold': 0.9, 'min_cut_duration': 0.04,
        'scene_buffer_size': 5, 'use_scene_detect': False,
        'fx_stutter': True,
        'fx_flash': True, 'fx_flash_chance': 0.8,
        'fx_block_glitch': True, 'fx_block_glitch_chance': 0.7,
        'fx_jpeg_crush': True, 'fx_jpeg_crush_chance': 0.6,
        'fx_pixel_drift': True, 'fx_pixel_drift_chance': 0.6,
        'fx_bad_signal': True, 'fx_bad_signal_chance': 0.5,
        'fx_cascade': True, 'fx_cascade_chance': 0.5,
        'fx_negative': True, 'fx_negative_chance': 0.2,
        'fx_vhs': True, 'fx_vhs_chance': 0.4,
    },
    "Rhythm Flash (Scene Mix)": {
        'chaos_level': 0.6, 'threshold': 1.1, 'min_cut_duration': 0.08,
        'scene_buffer_size': 15, 'use_scene_detect': True,
        'fx_stutter': True,
        'fx_flash': True, 'fx_flash_chance': 1.0,
        'fx_zoom_glitch': True, 'fx_zoom_glitch_chance': 0.5,
    },
}


class MainGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Disc VPC 01")
        self.geometry("1500x900")
        self.minsize(900, 700)
        self.configure(bg=C_SILVER)
        self.resizable(True, True)

        self.audio_path = ""
        self.video_paths = []
        self.overlay_dir = ""
        self.temp_preview_path = "temp_preview.mp4"

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

    # ------------------------------------------------------------------ styles
    def _setup_styles(self):
        self.style.theme_use('clam')
        self.option_add('*Font', 'MS_Sans_Serif 10')
        base = {'background': C_SILVER, 'foreground': C_TEXT, 'font': 'MS_Sans_Serif 10'}
        self.style.configure('.', **base)

        self.style.configure('W95.TButton', background=C_SILVER, foreground=C_TEXT,
                             relief='raised', borderwidth=2,
                             highlightthickness=0, focusthickness=0)
        self.style.map('W95.TButton',
                       background=[('active', '#D6D6D6'), ('disabled', C_SILVER)],
                       relief=[('pressed', 'sunken'), ('active', 'raised')])

        self.style.configure('Draft.TButton', background=C_BLUE_LIGHT, foreground=C_TEXT,
                             relief='raised', borderwidth=2, font=('MS Sans Serif', 10, 'bold'))
        self.style.map('Draft.TButton',
                       background=[('active', '#BBC8E8'), ('disabled', C_SILVER)],
                       relief=[('pressed', 'sunken'), ('active', 'raised')])

        self.style.configure('Preview.TButton', background='#D0EED0', foreground=C_TEXT,
                             relief='raised', borderwidth=2, font=('MS Sans Serif', 10, 'bold'))
        self.style.map('Preview.TButton',
                       background=[('active', '#B8DDB8'), ('disabled', C_SILVER)],
                       relief=[('pressed', 'sunken'), ('active', 'raised')])

        self.style.configure('Stop.TButton', background='#EE8888', foreground=C_WHITE,
                             relief='raised', borderwidth=2, font=('MS Sans Serif', 10, 'bold'))
        self.style.map('Stop.TButton',
                       background=[('active', '#DD5555'), ('disabled', '#CCAAAA')],
                       relief=[('pressed', 'sunken'), ('active', 'raised')])

        self.style.configure('ActiveTab.TButton',
            background='#B8C8E8', foreground=C_TEXT,
            relief='sunken', borderwidth=2,
            font=('MS Sans Serif', 9, 'bold'))
        self.style.map('ActiveTab.TButton',
            background=[('active', '#A0B8D8')],
            relief=[('active', 'sunken')])

        self.style.configure('FullRender.TButton', background='#404040', foreground=C_WHITE,
                             relief='raised', borderwidth=3, font=('MS Sans Serif', 11, 'bold'))
        self.style.map('FullRender.TButton',
                       background=[('active', '#606060'), ('disabled', C_SILVER)],
                       relief=[('pressed', 'sunken'), ('active', 'raised')])


        self.style.configure('W95.TFrame', background=C_SILVER, relief='sunken', borderwidth=2)

        self.style.configure('W95.TNotebook', background=C_SILVER, borderwidth=2,
                             tabmargins=[2, 2, 2, 0])
        self.style.configure('W95.TNotebook.Tab', background=C_SILVER, foreground=C_TEXT,
                             relief='raised', borderwidth=2, padding=[6, 3])
        self.style.map('W95.TNotebook.Tab',
                       background=[('selected', C_SILVER)],
                       expand=[('selected', [1, 1, 0, 0])])

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
        self.style.layout('W95.Horizontal.TProgressbar',
                          [('W95.Horizontal.TProgressbar.trough',
                            {'children': [('W95.Horizontal.TProgressbar.pbar',
                                           {'side': 'left', 'sticky': 'ns'})],
                             'sticky': 'nswe'})])
        self.style.configure('green.W95.Horizontal.TProgressbar',
                             foreground=C_TITLE_BAR, background=C_TITLE_BAR)

    # ------------------------------------------------------------------ vars
    def _setup_vars(self):
        self.vars = {}
        self._bool_defaults = set()   # track which vars are bool
        defaults = {
            # Cut logic
            'chaos_level': 0.6,
            'threshold': 1.2,
            'transient_thresh': 0.5,
            'min_cut_duration': 0.05,
            'scene_buffer_size': 10.0,
            'use_scene_detect': False,
            'snap_to_beat': False,
            'snap_tolerance': 0.05,
            # Core effects
            'fx_stutter': True,
            'fx_flash': True,       'fx_flash_chance': 0.8,
            'fx_ghost': False,      'fx_ghost_int': 0.5,
            'fx_psort': True,       'fx_psort_chance': 0.5,  'fx_psort_int': 0.5,
            'fx_datamosh': False,   'fx_datamosh_chance': 0.5,
            'fx_ascii': False,      'fx_ascii_chance': 0.7,
                                    'fx_ascii_size': 12.0,   'fx_ascii_blend': 0.0,
            # Codec breakers
            'fx_rgb': True,             'fx_rgb_chance': 0.7,
            'fx_block_glitch': False,   'fx_block_glitch_chance': 0.5,
            'fx_pixel_drift': False,    'fx_pixel_drift_chance': 0.5,
            'fx_scanlines': False,      'fx_scanlines_chance': 0.8,
            'fx_bitcrush': False,       'fx_bitcrush_chance': 0.5,
            'fx_colorbleed': False,     'fx_colorbleed_chance': 0.5,
            'fx_freeze_corrupt': False, 'fx_freeze_corrupt_chance': 0.3,
            'fx_negative': False,       'fx_negative_chance': 0.2,
            # Degradation
            'fx_jpeg_crush': False, 'fx_jpeg_crush_chance': 0.5,
            'fx_fisheye': False,    'fx_fisheye_chance': 0.3,
            'fx_vhs': False,        'fx_vhs_chance': 0.5,
            'fx_interlace': False,  'fx_interlace_chance': 0.4,
            'fx_bad_signal': False, 'fx_bad_signal_chance': 0.3,
            'fx_dither': False,     'fx_dither_chance': 0.4,
            'fx_zoom_glitch': False,'fx_zoom_glitch_chance': 0.5,
            # Complex
            'fx_feedback': False,
            'fx_phase_shift': False,    'fx_phase_shift_chance': 0.4,
            'fx_mosaic': False,         'fx_mosaic_chance': 0.5,
            'fx_echo': False,           'fx_echo_chance': 0.4,
            'fx_kali': False,           'fx_kali_chance': 0.3,
            'fx_cascade': False,        'fx_cascade_chance': 0.4,
            # Overlays
            'fx_overlay': False,
            # Export
            'fps': 24.0,
            'crf': 18.0,
            # Mystery
            'mystery_VESSEL': 0.0,
            'mystery_ENTROPY_7': 0.0,
            'mystery_DELTA_OMEGA': 0.0,
            'mystery_STATIC_MIND': 0.0,
            'mystery_RESONANCE': 0.0,
            'mystery_COLLAPSE': 0.0,
            'mystery_ZERO': 0.0,
            'mystery_FLESH_K': 0.0,
            'mystery_DOT': 0.0,
        }
        self._defaults_all = dict(defaults)   # save for preset reset
        for name, val in defaults.items():
            if isinstance(val, bool):
                self.vars[name] = tk.BooleanVar(value=val)
                self._bool_defaults.add(name)
            else:
                self.vars[name] = tk.DoubleVar(value=val)

        # Non-numeric vars (string / combobox)
        self.var_psort_axis       = tk.StringVar(value='luminance')
        self.var_ascii_color_mode = tk.StringVar(value='fixed')

        # Extra numeric vars not in defaults dict
        extras = {
            'fx_overlay_chance':   0.5,
            'fx_overlay_opacity':  0.85,
            'fx_overlay_scale':    0.4,
            'fx_overlay_scale_min':0.15,
            'fx_ascii_fg_r': 0.0, 'fx_ascii_fg_g': 255.0, 'fx_ascii_fg_b': 0.0,
            'fx_ascii_bg_r': 0.0, 'fx_ascii_bg_g': 0.0,   'fx_ascii_bg_b': 0.0,
        }
        self.var_overlay_blend    = tk.StringVar(value='screen')
        self.var_overlay_position = tk.StringVar(value='random')
        self.var_silence_mode = tk.StringVar(value='dim')
        for name, val in extras.items():
            self.vars[name] = tk.DoubleVar(value=val)
        self._defaults_all.update(extras)   # include extras in reset

        # StringVars for formatted display of slider values
        self._display_vars = {}
        for name, dvar in self.vars.items():
            if isinstance(dvar, tk.DoubleVar):
                sv = tk.StringVar()
                self._display_vars[name] = sv
                # Determine format based on known ranges
                if name in ('fx_ascii_fg_r', 'fx_ascii_fg_g', 'fx_ascii_fg_b',
                            'fx_ascii_bg_r', 'fx_ascii_bg_g', 'fx_ascii_bg_b',
                            'fps', 'crf', 'fx_ascii_size', 'scene_buffer_size'):
                    fmt = 'int'
                else:
                    fmt = '2f'
                def _make_trace(dv, sv, f):
                    def _cb(*_):
                        v = dv.get()
                        sv.set(str(int(round(v))) if f == 'int' else f'{v:.2f}')
                    dv.trace_add('write', _cb)
                    _cb()
                _make_trace(dvar, sv, fmt)

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=200)
        self.grid_columnconfigure(1, weight=3)
        self.grid_columnconfigure(2, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # ---- SIDEBAR ----
        sidebar = ttk.Frame(self, style='W95.TFrame')
        sidebar.grid(row=0, column=0, padx=(8, 4), pady=8, sticky='nsew')

        tb_s = tk.Frame(sidebar, bg=C_TITLE_BAR, height=28)
        tb_s.pack(fill='x')
        tk.Label(tb_s, text='Disc VPC 01',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 10, 'bold')).pack(side='left', padx=6, pady=3)

        self._build_source_files(sidebar)
        self._build_presets_panel(sidebar)

        # Spacer pushes render buttons to bottom
        tk.Frame(sidebar, bg=C_SILVER).pack(fill='both', expand=True)

        # Render buttons pinned to bottom
        render_frame = tk.Frame(sidebar, bg=C_SILVER)
        render_frame.pack(fill='x', padx=6, pady=(4, 8))
        self.btn_draft = ttk.Button(render_frame, text='DRAFT  (5 sec / 480p)',
                                    command=lambda: self.run('draft'),
                                    style='Draft.TButton')
        self.btn_draft.pack(fill='x', pady=2, ipady=4)
        self.btn_preview = ttk.Button(render_frame, text='PREVIEW  (5 sec)',
                                      command=lambda: self.run('preview'),
                                      style='Preview.TButton')
        self.btn_preview.pack(fill='x', pady=2, ipady=4)
        self.btn_run_full = ttk.Button(render_frame, text='RENDER FULL VIDEO',
                                       command=lambda: self.run('final'),
                                       style='FullRender.TButton')
        self.btn_run_full.pack(fill='x', pady=(4, 2), ipady=6)

        # ---- CENTER PANEL ----
        center = ttk.Frame(self, style='W95.TFrame')
        center.grid(row=0, column=1, padx=4, pady=8, sticky='nsew')

        tb_c = tk.Frame(center, bg=C_TITLE_BAR, height=28)
        tb_c.pack(fill='x')
        tk.Label(tb_c, text='Disc VPC 01  \u2014  Effects',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 11, 'bold')).pack(side='left', padx=6, pady=3)

        # Tab strip
        tab_strip = tk.Frame(center, bg=C_SILVER)
        tab_strip.pack(fill='x', padx=2, pady=(2, 0))

        # Content host
        content_host = tk.Frame(center, bg=C_SILVER)
        content_host.pack(fill='both', expand=True, padx=2, pady=2)

        # Pre-build content frames (populated in Tasks 6+7)
        effects_frame = tk.Frame(content_host, bg=C_SILVER)
        export_frame  = tk.Frame(content_host, bg=C_SILVER)
        mystery_frame = tk.Frame(content_host, bg=C_SILVER)

        self._center_frames = {
            'EFFECTS': effects_frame,
            'EXPORT':  export_frame,
            'MYSTERY': mystery_frame,
        }

        # Placeholder content — will be replaced by Tasks 6 and 7
        self._build_effects_accordion(effects_frame)
        self._build_export_panel(export_frame)
        self._build_mystery_panel(mystery_frame)

        # Tab buttons
        self._active_center_tab = None
        self._center_tab_btns = {}
        tab_defs = [('EFFECTS', 'EFFECTS'), ('EXPORT', 'EXPORT'), ('[ ? ]', 'MYSTERY')]
        for label, key in tab_defs:
            btn = ttk.Button(tab_strip, text=label, style='W95.TButton',
                             command=lambda k=key: self._switch_center_tab(k))
            btn.pack(side='left', padx=2, pady=2)
            self._center_tab_btns[key] = btn

        # Show EFFECTS by default
        self._switch_center_tab('EFFECTS')

        # ---- RIGHT PANEL ----
        right = ttk.Frame(self, style='W95.TFrame')
        right.grid(row=0, column=2, padx=(4, 8), pady=8, sticky='nsew')

        tb2 = tk.Frame(right, bg=C_TITLE_BAR, height=28)
        tb2.pack(fill='x')
        tk.Label(tb2, text='Live Preview & Console',
                 fg=C_WHITE, bg=C_TITLE_BAR,
                 font=('MS Sans Serif', 11, 'bold')).pack(side='left', padx=6, pady=3)

        cr = tk.Frame(right, bg=C_SILVER)
        cr.pack(fill='both', expand=True, padx=2, pady=2)

        # Preview monitor
        pmon = tk.LabelFrame(cr, text='Preview Monitor (640\u00d7360)',
                             bg=C_SILVER, fg=C_TEXT, bd=2, relief='sunken',
                             font=('MS Sans Serif', 10, 'bold'))
        pmon.pack(fill='x', padx=8, pady=6)
        _blank = ImageTk.PhotoImage(Image.new('RGB', (640, 360), 'black'))
        self.player_label = tk.Label(pmon, image=_blank,
                                     bg=C_BLACK, bd=2, relief='sunken')
        self.player_label.imgtk = _blank
        self.player_label.pack(padx=4, pady=4)

        # Progress
        self.progress = ttk.Progressbar(cr, style='green.W95.Horizontal.TProgressbar',
                                        mode='determinate', maximum=100,
                                        variable=self.progress_var)
        self.progress.pack(fill='x', padx=8, pady=3)

        # Console
        cp = tk.LabelFrame(cr, text='Status Log', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='sunken', font=('MS Sans Serif', 10, 'bold'))
        cp.pack(fill='both', expand=True, padx=8, pady=4)

        console_btns = tk.Frame(cp, bg=C_SILVER)
        console_btns.pack(fill='x', padx=4, pady=(4, 0))
        ttk.Button(console_btns, text='Clear Log', style='W95.TButton',
                   command=self._clear_log).pack(side='right', padx=2)

        self.console = tk.Text(cp, height=8, font=('Courier New', 9),
                               bg=C_WHITE, fg=C_BLACK, bd=2, relief='sunken')
        self.console.pack(fill='both', expand=True, padx=4, pady=4)

        # Stop button
        self.btn_stop = ttk.Button(cr, text='STOP',
                                   command=self.stop_and_clear_playback,
                                   style='Stop.TButton', state='disabled')
        self.btn_stop.pack(fill='x', padx=8, pady=(2, 6), ipady=4)

    # ------------------------------------------------------------------ source + presets panels
    def _build_source_files(self, parent):
        fp = tk.LabelFrame(parent, text='Source Files', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='groove', font=('MS Sans Serif', 10, 'bold'))
        fp.pack(pady=4, padx=6, fill='x')
        fp.grid_columnconfigure(0, weight=1)

        self._audio_loaded = False
        self._video_loaded = False

        # Audio row
        audio_row = tk.Frame(fp, bg=C_SILVER)
        audio_row.pack(fill='x', padx=6, pady=(4, 0))
        self._audio_dot = tk.Label(audio_row, text='\u25cf', fg='#AAAAAA', bg=C_SILVER,
                                   font=('MS Sans Serif', 12))
        self._audio_dot.pack(side='left', padx=(0, 4))
        self.btn_audio = ttk.Button(audio_row, text='Load Audio (WAV / MP3)',
                                    command=self.sel_audio, style='W95.TButton')
        self.btn_audio.pack(side='left', fill='x', expand=True)
        self.lbl_audio_name = tk.Label(fp, text='\u2014 not loaded \u2014',
                                       bg=C_SILVER, fg=C_DARK_GRAY,
                                       font=('Courier New', 9), anchor='w')
        self.lbl_audio_name.pack(fill='x', padx=24, pady=(0, 3))

        # Video row
        video_row = tk.Frame(fp, bg=C_SILVER)
        video_row.pack(fill='x', padx=6, pady=(2, 0))
        self._video_dot = tk.Label(video_row, text='\u25cf', fg='#AAAAAA', bg=C_SILVER,
                                   font=('MS Sans Serif', 12))
        self._video_dot.pack(side='left', padx=(0, 4))
        self.btn_video = ttk.Button(video_row, text='Load Source Video',
                                    command=self.sel_video, style='W95.TButton')
        self.btn_video.pack(side='left', fill='x', expand=True)
        self.lbl_video_name = tk.Label(fp, text='\u2014 not loaded \u2014',
                                       bg=C_SILVER, fg=C_DARK_GRAY,
                                       font=('Courier New', 9), anchor='w')
        self.lbl_video_name.pack(fill='x', padx=24, pady=(0, 3))

    def _build_presets_panel(self, parent):
        pp = tk.LabelFrame(parent, text='Presets', bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='groove', font=('MS Sans Serif', 10, 'bold'))
        pp.pack(pady=4, padx=6, fill='x')

        # Listbox + scrollbar
        lb_frame = tk.Frame(pp, bg=C_SILVER)
        lb_frame.pack(fill='x', padx=4, pady=(4, 2))
        scrollbar = ttk.Scrollbar(lb_frame, orient='vertical')
        self._presets_listbox = tk.Listbox(
            lb_frame, height=8, yscrollcommand=scrollbar.set,
            bg=C_WHITE, fg=C_TEXT, selectbackground=C_TITLE_BAR,
            selectforeground=C_WHITE, font=('MS Sans Serif', 9),
            activestyle='none', bd=2, relief='sunken')
        scrollbar.config(command=self._presets_listbox.yview)
        self._presets_listbox.pack(side='left', fill='x', expand=True)
        scrollbar.pack(side='left', fill='y')
        self._presets_listbox.bind('<Double-Button-1>', lambda e: self._load_selected_preset())

        # Buttons
        btn_row = tk.Frame(pp, bg=C_SILVER)
        btn_row.pack(fill='x', padx=4, pady=2)
        ttk.Button(btn_row, text='Load', style='W95.TButton',
                   command=self._load_selected_preset).pack(side='left', padx=2)
        ttk.Button(btn_row, text='Save Current', style='W95.TButton',
                   command=self._save_current_preset).pack(side='left', padx=2)
        ttk.Button(btn_row, text='Delete', style='W95.TButton',
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
            prev_btn = self._center_tab_btns.get(self._active_center_tab)
            if prev_btn:
                prev_btn.configure(style='W95.TButton')
        self._center_frames[key].pack(fill='both', expand=True)
        self._active_center_tab = key
        active_btn = self._center_tab_btns.get(key)
        if active_btn:
            active_btn.configure(style='ActiveTab.TButton')

    def _add_tooltip(self, widget, text):
        tip = None
        def _enter(e):
            nonlocal tip
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f'+{e.x_root+12}+{e.y_root+6}')
            tk.Label(tip, text=text, bg='#FFFFCC', fg=C_BLACK,
                     font=('MS Sans Serif', 9), bd=1, relief='solid',
                     padx=4, pady=2).pack()
        def _leave(e):
            nonlocal tip
            if tip:
                tip.destroy()
                tip = None
        widget.bind('<Enter>', _enter)
        widget.bind('<Leave>', _leave)

    # ------------------------------------------------------------------ tab builders
    def _tab_cut_logic(self, f):
        self._effect_row_simple(f, 'Smart Scene Detection', 'use_scene_detect',
                                note='Detects scene changes for more natural cuts')
        self._slider_block(f, 'Global Chaos Level', 'chaos_level', 0.0, 1.0)
        self._slider_block(f, 'Beat Threshold', 'threshold', 0.5, 2.0)
        self._slider_block(f, 'Transient Sensitivity', 'transient_thresh', 0.1, 1.5)
        self._slider_block(f, 'Min Cut Duration (sec)', 'min_cut_duration', 0.0, 0.3)
        self._slider_block(f, 'Scene Buffer Size', 'scene_buffer_size', 2, 30)
        self._effect_row_simple(f, 'Snap Cuts to Beat Grid', 'snap_to_beat',
                                note='Pulls onset times to nearest beat — improves rhythmic precision')
        self._slider_block(f, 'Beat Snap Tolerance (sec)', 'snap_tolerance', 0.01, 0.15, indent=True)

        # Silence treatment
        sf = self._sub_labelframe(f, 'Silence Treatment')
        for val, lbl in [('dim', 'Dim (darken)'), ('blur', 'Soft Blur'), ('both', 'Blur + Dim'), ('none', 'None')]:
            tk.Radiobutton(sf, text=lbl, variable=self.var_silence_mode, value=val,
                           bg=C_SILVER, fg=C_TEXT, selectcolor=C_WHITE,
                           font=('MS Sans Serif', 9),
                           activebackground=C_SILVER).pack(anchor='w', padx=8, pady=1)

    def _tab_core_effects(self, f):
        self._effect_row_simple(f, 'Stutter / Drill',
                                'fx_stutter',
                                note='Triggers on IMPACT segments — no chance slider, always fires')

        self._effect_row(f, 'Flash Frame', 'fx_flash', 'fx_flash_chance',
                         note='Triggers on DROP / IMPACT segments')

        self._effect_row(f, 'Ghost Trails', 'fx_ghost', 'fx_ghost_int',
                         chance_label='Opacity',
                         note='Triggers on SUSTAIN / BUILD segments')

        self._effect_row(f, 'Pixel Sort', 'fx_psort', 'fx_psort_chance',
                         note='Triggers on NOISE / IMPACT segments')
        self._slider_block(f, 'Pixel Sort Intensity', 'fx_psort_int', 0.0, 1.0, indent=True)
        lf = self._sub_labelframe(f, 'Sort Axis')
        ttk.Combobox(lf, values=['luminance', 'hue', 'saturation'],
                     textvariable=self.var_psort_axis,
                     style='W95.TCombobox', width=14).pack(padx=6, pady=3)

        self._effect_row(f, 'Datamosh', 'fx_datamosh', 'fx_datamosh_chance',
                         note='NOISE segment — I-frame drop only in Final render mode')

        self._effect_row(f, 'ASCII Filter', 'fx_ascii', 'fx_ascii_chance',
                         note='Triggers on SUSTAIN segments')
        self._slider_block(f, 'Char Size (px)', 'fx_ascii_size', 4, 40, indent=True)
        self._slider_block(f, 'Blend  (0=full ASCII, 1=overlay)',
                           'fx_ascii_blend', 0.0, 1.0, indent=True)

        lf2 = self._sub_labelframe(f, 'ASCII Color Mode')
        ttk.Combobox(lf2, values=['fixed', 'original', 'inverted'],
                     textvariable=self.var_ascii_color_mode,
                     style='W95.TCombobox', width=10).pack(padx=6, pady=3)

        lf3 = self._sub_labelframe(f, 'ASCII FG Color R / G / B  (mode = fixed)')
        self._slider_block(lf3, 'Red',   'fx_ascii_fg_r', 0, 255)
        self._slider_block(lf3, 'Green', 'fx_ascii_fg_g', 0, 255)
        self._slider_block(lf3, 'Blue',  'fx_ascii_fg_b', 0, 255)

        lf4 = self._sub_labelframe(f, 'ASCII BG Color R / G / B')
        self._slider_block(lf4, 'Red',   'fx_ascii_bg_r', 0, 255)
        self._slider_block(lf4, 'Green', 'fx_ascii_bg_g', 0, 255)
        self._slider_block(lf4, 'Blue',  'fx_ascii_bg_b', 0, 255)

    def _tab_glitch(self, f):
        rows = [
            ('RGB Shift',              'fx_rgb',            'fx_rgb_chance',
             'IMPACT / BUILD \u2014 shifts colour channels horizontally'),
            ('Block Glitch',           'fx_block_glitch',   'fx_block_glitch_chance',
             'IMPACT / DROP \u2014 replaces random blocks with garbage'),
            ('Pixel Drift',            'fx_pixel_drift',    'fx_pixel_drift_chance',
             'NOISE / IMPACT \u2014 rows of pixels slide sideways'),
            ('Color Bleed / VHS Smear','fx_colorbleed',     'fx_colorbleed_chance',
             'NOISE \u2014 bleeds colour channels like VHS tape'),
            ('Freeze + Corrupt',       'fx_freeze_corrupt', 'fx_freeze_corrupt_chance',
             'DROP \u2014 freezes frame and corrupts pixels'),
            ('Negative',               'fx_negative',       'fx_negative_chance',
             'IMPACT \u2014 inverts all colours'),
        ]
        for (label, flag_key, chance_key, note) in rows:
            self._effect_row(f, label, flag_key, chance_key, note=note)

    def _tab_complex(self, f):
        self._effect_row_simple(f, 'Feedback Loop', 'fx_feedback',
                                note='SUSTAIN / BUILD — always on when enabled, no chance')
        rows = [
            ('Phase Shift (L/R bands)', 'fx_phase_shift', 'fx_phase_shift_chance',
             'Shifts image bands left and right alternately'),
            ('Mosaic Pulse (bass RMS)', 'fx_mosaic',      'fx_mosaic_chance',
             'Pixelates image driven by bass amplitude'),
            ('Echo Compound (hue shift)','fx_echo',       'fx_echo_chance',
             'Layers shifted colour echoes of the frame'),
            ('Kali Mirror (kaleidoscope)','fx_kali',      'fx_kali_chance',
             'Applies kaleidoscopic mirror transform'),
            ('Glitch Cascade (chain)',   'fx_cascade',    'fx_cascade_chance',
             'Chains multiple glitch effects together'),
        ]
        for (label, flag_key, chance_key, note) in rows:
            self._effect_row(f, label, flag_key, chance_key, note=note)

    def _tab_degradation(self, f):
        rows = [
            ('Scan Lines',          'fx_scanlines',    'fx_scanlines_chance',
             'SUSTAIN / NOISE \u2014 horizontal CRT scanline overlay'),
            ('Bitcrush / Posterize','fx_bitcrush',     'fx_bitcrush_chance',
             'Any segment \u2014 reduces colour depth'),
            ('JPEG Crush',          'fx_jpeg_crush',   'fx_jpeg_crush_chance',
             'IMPACT / NOISE \u2014 heavy JPEG compression artefacts'),
            ('Fisheye / Barrel',    'fx_fisheye',      'fx_fisheye_chance',
             'BUILD \u2014 barrel lens distortion'),
            ('VHS Tracking',        'fx_vhs',          'fx_vhs_chance',
             'NOISE / DROP \u2014 VHS tape tracking error'),
            ('Interlace',           'fx_interlace',    'fx_interlace_chance',
             'SUSTAIN \u2014 splits even/odd scan lines'),
            ('Bad Signal',          'fx_bad_signal',   'fx_bad_signal_chance',
             'DROP / NOISE \u2014 digital signal breakup'),
            ('Dithering',           'fx_dither',       'fx_dither_chance',
             'SILENCE / SUSTAIN \u2014 ordered dither pattern'),
            ('Zoom Glitch',         'fx_zoom_glitch',  'fx_zoom_glitch_chance',
             'IMPACT \u2014 rapid random zoom pulse'),
        ]
        for (label, flag_key, chance_key, note) in rows:
            self._effect_row(f, label, flag_key, chance_key, note=note)

    def _tab_overlays(self, f):
        self._effect_row(f, 'Enable Overlays', 'fx_overlay', 'fx_overlay_chance',
                         note='Composites image files from the selected folder onto frames')
        self._slider_block(f, 'Opacity', 'fx_overlay_opacity', 0.0, 1.0)
        self._slider_block(f, 'Scale Max (fraction of frame)', 'fx_overlay_scale', 0.05, 1.0)
        self._slider_block(f, 'Scale Min', 'fx_overlay_scale_min', 0.05, 1.0)

        lf = self._sub_labelframe(f, 'Blend Mode')
        ttk.Combobox(lf, values=['screen', 'normal', 'multiply'],
                     textvariable=self.var_overlay_blend,
                     style='W95.TCombobox', width=12).pack(padx=6, pady=3)

        lf2 = self._sub_labelframe(f, 'Position')
        ttk.Combobox(lf2, values=['random', 'center', 'random_corner'],
                     textvariable=self.var_overlay_position,
                     style='W95.TCombobox', width=14).pack(padx=6, pady=3)

        btn_frame = tk.Frame(f, bg=C_SILVER)
        btn_frame.pack(fill='x', padx=10, pady=6)
        ttk.Button(btn_frame, text='Select Overlay Folder...',
                   command=self.sel_ov, style='W95.TButton').pack(fill='x')
        self.lbl_overlay_dir = tk.Label(btn_frame, text='No folder selected',
                                        bg=C_SILVER, fg=C_DARK_GRAY,
                                        font=('Courier New', 9))
        self.lbl_overlay_dir.pack(anchor='w', pady=(2, 0))

    def _tab_mystery(self, f):
        tk.Label(f, text='[ UNKNOWN PARAMETERS — USE WITH CAUTION ]',
                 bg=C_SILVER, fg=C_DARK_GRAY,
                 font=('Courier New', 8, 'italic')).pack(pady=(8, 2), padx=10, anchor='w')
        entries = [
            ('VESSEL',       'mystery_VESSEL'),
            ('ENTROPY_7',    'mystery_ENTROPY_7'),
            ('dO THRESH',    'mystery_DELTA_OMEGA'),
            ('static.mind',  'mystery_STATIC_MIND'),
            ('__RESONANCE',  'mystery_RESONANCE'),
            ('COLLAPSE//',   'mystery_COLLAPSE'),
            ('000',          'mystery_ZERO'),
            ('FLESH_K',      'mystery_FLESH_K'),
            ('[  .  ]',      'mystery_DOT'),
        ]
        for (label, key) in entries:
            self._slider_block(f, label, key, 0.0, 1.0, mono=True)

    def _tab_export(self, f):
        lf = self._sub_labelframe(f, 'Frame Rate')
        fps_row = tk.Frame(lf, bg=C_SILVER)
        fps_row.pack(fill='x', padx=6, pady=4)
        tk.Label(fps_row, text='FPS:', bg=C_SILVER, width=10, anchor='w').pack(side='left')
        self.fps_combo = ttk.Combobox(fps_row, values=['12', '24', '30', '60'],
                                      style='W95.TCombobox', width=8)
        self.fps_combo.set('24')
        self.fps_combo.pack(side='left', padx=4)
        self.fps_combo.bind('<<ComboboxSelected>>',
                            lambda e: self.vars['fps'].set(float(self.fps_combo.get())))

        lf2 = self._sub_labelframe(f, 'Resolution')
        res_row = tk.Frame(lf2, bg=C_SILVER)
        res_row.pack(fill='x', padx=6, pady=4)
        tk.Label(res_row, text='Resolution:', bg=C_SILVER, width=10, anchor='w').pack(side='left')
        self.res_combo = ttk.Combobox(res_row, values=['240p', '360p', '480p', '720p', '1080p'],
                                      style='W95.TCombobox', width=12)
        self.res_combo.set('720p')
        self.res_combo.pack(side='left', padx=4)

        self._slider_block(f, 'Quality CRF  (0=lossless  18=good  51=artifact art)', 'crf', 0, 51)

        lf3 = self._sub_labelframe(f, 'Codec & Encoding')
        codec_row = tk.Frame(lf3, bg=C_SILVER)
        codec_row.pack(fill='x', padx=6, pady=4)
        tk.Label(codec_row, text='Codec:', bg=C_SILVER, width=14, anchor='w').pack(side='left')
        self.fmt_combo = ttk.Combobox(codec_row, values=['H.264 (MP4)', 'H.265 (MP4)'],
                                      style='W95.TCombobox', width=14)
        self.fmt_combo.set('H.264 (MP4)')
        self.fmt_combo.pack(side='left', padx=4)

        enc_row = tk.Frame(lf3, bg=C_SILVER)
        enc_row.pack(fill='x', padx=6, pady=4)
        tk.Label(enc_row, text='ffmpeg Preset:', bg=C_SILVER, width=14, anchor='w').pack(side='left')
        self.preset_enc_combo = ttk.Combobox(
            enc_row, values=['ultrafast', 'fast', 'medium', 'slow'],
            style='W95.TCombobox', width=12)
        self.preset_enc_combo.set('medium')
        self.preset_enc_combo.pack(side='left', padx=4)

    # ------------------------------------------------------------------ widget helpers
    def _sub_labelframe(self, parent, title):
        lf = tk.LabelFrame(parent, text=title, bg=C_SILVER, fg=C_TEXT,
                           bd=2, relief='groove', font=('MS Sans Serif', 9, 'bold'))
        lf.pack(fill='x', padx=10, pady=(6, 2))
        return lf

    def _build_effects_accordion(self, parent):
        """Build scrollable accordion with 6 effect groups inside parent frame."""
        # Remove placeholder label if present
        for widget in parent.winfo_children():
            widget.destroy()

        # Scrollable canvas
        scroll_outer = tk.Frame(parent, bg=C_SILVER)
        scroll_outer.pack(fill='both', expand=True)

        canvas = tk.Canvas(scroll_outer, bg=C_SILVER, highlightthickness=0)
        vscroll = ttk.Scrollbar(scroll_outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        cf = tk.Frame(canvas, bg=C_SILVER)
        cf_window = canvas.create_window((0, 0), window=cf, anchor='nw')

        def _on_cf_configure(event):
            canvas.configure(scrollregion=canvas.bbox('all'))

        def _on_canvas_configure(event):
            canvas.itemconfig(cf_window, width=event.width)

        cf.bind('<Configure>', _on_cf_configure)
        canvas.bind('<Configure>', _on_canvas_configure)
        canvas.bind_all('<MouseWheel>',
                        lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), 'units'))

        # Group: CUT LOGIC (open by default)
        body = self._acc_group(cf, 'CUT LOGIC', open=True)
        self._tab_cut_logic(body)

        # Group: CORE FX (open by default)
        body = self._acc_group(cf, 'CORE FX', open=True)
        self._tab_core_effects(body)

        # Group: GLITCH
        body = self._acc_group(cf, 'GLITCH', open=False)
        self._tab_glitch(body)

        # Group: DEGRADATION
        body = self._acc_group(cf, 'DEGRADATION', open=False)
        self._tab_degradation(body)

        # Group: COMPLEX
        body = self._acc_group(cf, 'COMPLEX', open=False)
        self._tab_complex(body)

        # Group: OVERLAYS
        body = self._acc_group(cf, 'OVERLAYS', open=False)
        self._tab_overlays(body)

    def _acc_group(self, parent, title, open=False):
        """Build an accordion group. Returns the body frame to populate."""
        group = tk.Frame(parent, bg=C_SILVER, bd=1, relief='solid')
        group.pack(fill='x', padx=4, pady=2)

        header = tk.Frame(group,
                          bg=C_TITLE_BAR if open else C_SILVER,
                          cursor='hand2')
        header.pack(fill='x')

        arrow_var = tk.StringVar(value='\u25bc' if open else '\u25ba')
        arrow_lbl = tk.Label(header, textvariable=arrow_var,
                             bg=C_TITLE_BAR if open else C_SILVER,
                             fg=C_WHITE if open else C_TEXT,
                             font=('MS Sans Serif', 9))
        arrow_lbl.pack(side='left', padx=4)

        title_lbl = tk.Label(header, text=title,
                             bg=C_TITLE_BAR if open else C_SILVER,
                             fg=C_WHITE if open else C_TEXT,
                             font=('MS Sans Serif', 10, 'bold'))
        title_lbl.pack(side='left', pady=4)

        body = tk.Frame(group, bg=C_WHITE, bd=1, relief='sunken')
        if open:
            body.pack(fill='x')

        def _toggle(e=None):
            is_open = body.winfo_ismapped()
            if is_open:
                body.pack_forget()
                header.configure(bg=C_SILVER)
                arrow_lbl.configure(bg=C_SILVER, fg=C_TEXT)
                title_lbl.configure(bg=C_SILVER, fg=C_TEXT)
                arrow_var.set('\u25ba')
            else:
                body.pack(fill='x')
                header.configure(bg=C_TITLE_BAR)
                arrow_lbl.configure(bg=C_TITLE_BAR, fg=C_WHITE)
                title_lbl.configure(bg=C_TITLE_BAR, fg=C_WHITE)
                arrow_var.set('\u25bc')

        header.bind('<Button-1>', _toggle)
        arrow_lbl.bind('<Button-1>', _toggle)
        title_lbl.bind('<Button-1>', _toggle)

        return body

    def _build_export_panel(self, parent):
        """Build export settings panel inside parent frame."""
        for widget in parent.winfo_children():
            widget.destroy()
        wrapper = tk.Frame(parent, bg=C_SILVER)
        wrapper.pack(fill='both', expand=True, padx=4, pady=4)
        self._tab_export(wrapper)

    def _build_mystery_panel(self, parent):
        """Build mystery knobs panel inside parent frame."""
        for widget in parent.winfo_children():
            widget.destroy()
        wrapper = tk.Frame(parent, bg=C_SILVER)
        wrapper.pack(fill='both', expand=True, padx=4, pady=4)
        self._tab_mystery(wrapper)

    def _effect_row(self, parent, label, flag_key, chance_key,
                    chance_label='Chance', note='', lo=0.0, hi=1.0):
        """Compact 2-row effect: checkbox + inline chance slider, no LabelFrame."""
        outer = tk.Frame(parent, bg=C_SILVER)
        outer.pack(fill='x', padx=4, pady=0)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_columnconfigure(1, weight=2)

        ttk.Checkbutton(outer, text=label, variable=self.vars[flag_key],
                        style='W95.TCheckbutton').grid(
            row=0, column=0, sticky='w', padx=6, pady=(4, 0))
        if note:
            tk.Label(outer, text=note, bg=C_SILVER, fg=C_DARK_GRAY,
                     font=('MS Sans Serif', 7, 'italic')).grid(
                row=1, column=0, sticky='w', padx=22, pady=(0, 2))

        right = tk.Frame(outer, bg=C_SILVER)
        right.grid(row=0, column=1, rowspan=2, sticky='nsew', padx=6, pady=(4, 2))
        hdr = tk.Frame(right, bg=C_SILVER)
        hdr.pack(fill='x')
        tk.Label(hdr, text=chance_label + ':', bg=C_SILVER, fg=C_TEXT,
                 font=('MS Sans Serif', 9)).pack(side='left')
        if chance_key in self._display_vars:
            tk.Label(hdr, textvariable=self._display_vars[chance_key],
                     bg=C_SILVER, fg=C_TEXT, font=('MS Sans Serif', 9, 'bold'),
                     width=5, anchor='e').pack(side='right')
        ttk.Scale(right, from_=lo, to=hi, variable=self.vars[chance_key],
                  orient=tk.HORIZONTAL, style='W95.Horizontal.TScale').pack(
            fill='x', pady=(1, 0))

        tk.Frame(parent, bg=C_DARK_GRAY, height=1).pack(fill='x', padx=4)

    def _effect_row_simple(self, parent, label, flag_key, note=''):
        """Checkbox-only effect row (no chance slider)."""
        outer = tk.Frame(parent, bg=C_SILVER)
        outer.pack(fill='x', padx=4, pady=0)
        ttk.Checkbutton(outer, text=label, variable=self.vars[flag_key],
                        style='W95.TCheckbutton').pack(
            side='left', padx=6, pady=(4, 2))
        if note:
            tk.Label(outer, text=note, bg=C_SILVER, fg=C_DARK_GRAY,
                     font=('MS Sans Serif', 7, 'italic')).pack(
                side='left', padx=4, pady=(4, 2))
        tk.Frame(parent, bg=C_DARK_GRAY, height=1).pack(fill='x', padx=4)

    def _slider_block(self, parent, label, var_name, lo, hi,
                      indent=False, mono=False):
        """Full-width slider: label left, value right, scale below."""
        pad_left = 20 if indent else 8
        frame = tk.Frame(parent, bg=C_SILVER)
        frame.pack(fill='x', padx=(pad_left, 8), pady=(2, 0))

        hdr = tk.Frame(frame, bg=C_SILVER)
        hdr.pack(fill='x')
        font_lbl = ('Courier New', 9, 'bold') if mono else ('MS Sans Serif', 9)
        tk.Label(hdr, text=label, bg=C_SILVER, fg=C_TEXT,
                 font=font_lbl, anchor='w').pack(side='left')
        if var_name in self._display_vars:
            tk.Label(hdr, textvariable=self._display_vars[var_name],
                     bg=C_SILVER, fg=C_TEXT, font=('MS Sans Serif', 9, 'bold'),
                     width=7, anchor='e').pack(side='right')
        ttk.Scale(frame, from_=lo, to=hi, variable=self.vars[var_name],
                  orient=tk.HORIZONTAL, style='W95.Horizontal.TScale').pack(
            fill='x', pady=(1, 4))

    def _mk_btn(self, master, txt, cmd, state='normal'):
        return ttk.Button(master, text=txt, command=cmd,
                          style='W95.TButton', state=state)

    # ------------------------------------------------------------------ file selection
    def sel_audio(self):
        p = filedialog.askopenfilename(filetypes=[('Audio', '*.mp3 *.wav')])
        if p:
            self.audio_path = p
            self.lbl_audio_name.configure(text=os.path.basename(p))
            self._audio_dot.configure(fg=C_GREEN_DOT)

    def sel_video(self):
        paths = filedialog.askopenfilenames(
            title="Select Video Source(s)",
            filetypes=[('Video', '*.mp4 *.mov *.mkv *.avi *.wmv *.flv')])
        if paths:
            self.video_paths = list(paths)
            count = len(self.video_paths)
            if count == 1:
                self.lbl_video_name.configure(text=os.path.basename(self.video_paths[0]))
            else:
                self.lbl_video_name.configure(text=f"{count} files loaded")
            self._video_dot.configure(fg=C_GREEN_DOT)

    def sel_ov(self):
        p = filedialog.askdirectory()
        if p:
            self.overlay_dir = p
            self.lbl_overlay_dir.configure(text=os.path.basename(p))

    # ------------------------------------------------------------------ config
    def get_current_config(self):
        cfg = {name: var.get() for name, var in self.vars.items()}

        # Explicit casts
        cfg['scene_buffer_size'] = int(cfg['scene_buffer_size'])
        cfg['fps']               = int(cfg.get('fps', 24))
        cfg['crf']               = int(cfg.get('crf', 18))
        cfg['fx_ascii_size']     = int(cfg.get('fx_ascii_size', 12))

        # Non-var fields
        cfg['resolution']    = self.res_combo.get() if hasattr(self, 'res_combo') else '720p'
        cfg['export_preset'] = self.preset_enc_combo.get() if hasattr(self, 'preset_enc_combo') else 'medium'
        cfg['video_codec']   = self.fmt_combo.get() if hasattr(self, 'fmt_combo') else 'H.264 (MP4)'
        cfg['fx_psort_axis']        = self.var_psort_axis.get()
        cfg['fx_ascii_color_mode']  = self.var_ascii_color_mode.get()
        cfg['fx_overlay_blend']     = self.var_overlay_blend.get()
        cfg['fx_overlay_position']  = self.var_overlay_position.get()
        cfg['silence_mode'] = self.var_silence_mode.get()
        cfg['fx_ascii_fg']          = [int(cfg.pop('fx_ascii_fg_r', 0)),
                                        int(cfg.pop('fx_ascii_fg_g', 255)),
                                        int(cfg.pop('fx_ascii_fg_b', 0))]
        cfg['fx_ascii_bg']          = [int(cfg.pop('fx_ascii_bg_r', 0)),
                                        int(cfg.pop('fx_ascii_bg_g', 0)),
                                        int(cfg.pop('fx_ascii_bg_b', 0))]

        # Pack mystery dict
        cfg['mystery'] = {
            'VESSEL':      float(cfg.pop('mystery_VESSEL',      0.0)),
            'ENTROPY_7':   float(cfg.pop('mystery_ENTROPY_7',   0.0)),
            'DELTA_OMEGA': float(cfg.pop('mystery_DELTA_OMEGA', 0.0)),
            'STATIC_MIND': float(cfg.pop('mystery_STATIC_MIND', 0.0)),
            'RESONANCE':   float(cfg.pop('mystery_RESONANCE',   0.0)),
            'COLLAPSE':    float(cfg.pop('mystery_COLLAPSE',     0.0)),
            'ZERO':        float(cfg.pop('mystery_ZERO',         0.0)),
            'FLESH_K':     float(cfg.pop('mystery_FLESH_K',     0.0)),
            'DOT':         float(cfg.pop('mystery_DOT',          0.0)),
        }
        return cfg

    def apply_preset(self, preset_name):
        # Reset ALL vars to defaults first so presets don't accumulate
        for name, val in self._defaults_all.items():
            if name in self.vars:
                self.vars[name].set(val)
        # Reset string vars
        self.var_psort_axis.set('luminance')
        self.var_ascii_color_mode.set('fixed')
        self.var_overlay_blend.set('screen')
        self.var_overlay_position.set('random')
        # Apply preset overrides
        cfg = PRESETS.get(preset_name, {})
        for key, value in cfg.items():
            if key in self.vars:
                self.vars[key].set(value)
        self.log(f"Preset '{preset_name}' loaded.")

    # ------------------------------------------------------------------ presets file I/O
    _PRESETS_PATH = os.path.join(os.path.dirname(__file__), 'presets.json')

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
            display = f"[B] {entry['name']}" if entry.get('builtin') else entry['name']
            self._presets_listbox.insert(tk.END, display)

    def apply_preset_config(self, cfg: dict, name: str):
        for key, value in cfg.items():
            if key in self.vars:
                self.vars[key].set(value)
        # Unpack composite fields
        if 'fx_ascii_fg' in cfg:
            r, g, b = cfg['fx_ascii_fg']
            self.vars['fx_ascii_fg_r'].set(r)
            self.vars['fx_ascii_fg_g'].set(g)
            self.vars['fx_ascii_fg_b'].set(b)
        if 'fx_ascii_bg' in cfg:
            r, g, b = cfg['fx_ascii_bg']
            self.vars['fx_ascii_bg_r'].set(r)
            self.vars['fx_ascii_bg_g'].set(g)
            self.vars['fx_ascii_bg_b'].set(b)
        if 'mystery' in cfg:
            for sub_key, val in cfg['mystery'].items():
                k = f'mystery_{sub_key}'
                if k in self.vars:
                    self.vars[k].set(val)
        self.var_psort_axis.set(cfg.get('fx_psort_axis', 'luminance'))
        self.var_ascii_color_mode.set(cfg.get('fx_ascii_color_mode', 'fixed'))
        self.var_overlay_blend.set(cfg.get('fx_overlay_blend', 'screen'))
        self.var_overlay_position.set(cfg.get('fx_overlay_position', 'random'))
        self.res_combo.set(cfg.get('resolution', '720p'))
        self.preset_enc_combo.set(cfg.get('export_preset', 'medium'))
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
            if not messagebox.askyesno('Overwrite?', f"Preset '{name}' already exists. Overwrite?", parent=self):
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
            new_sel = min(idx, len(self._user_presets) - 1)
            self._presets_listbox.selection_set(new_sel)
        current_label = self._active_preset_label.cget('text')
        if current_label == f'Active: {name}':
            self._active_preset_label.configure(text='Active: —')

    def _clear_log(self):
        self.console.delete('1.0', tk.END)

    def log(self, msg):
        self.console.insert(tk.END, f'[{time.strftime("%H:%M:%S")}] > {msg}\n')
        self.console.see(tk.END)

    # ------------------------------------------------------------------ render
    def run(self, mode='final'):
        if not self.audio_path or not self.video_paths:
            self.log('ERROR: Select Audio and Video source!')
            return

        cfg = self.get_current_config()
        cfg['audio_path']   = self.audio_path
        cfg['video_paths']  = self.video_paths
        cfg['overlay_dir']  = self.overlay_dir

        if mode in ('draft', 'preview'):
            cfg['render_mode']  = mode
            cfg['max_duration'] = 5.0
            cfg['output_path']  = self.temp_preview_path
            label = 'DRAFT (5 sec \u00b7 480p)' if mode == 'draft' else 'PREVIEW (5 sec)'
            self.log(f'Starting {label}...')
            self.progress.configure(mode='indeterminate')
            self.progress.start(10)
        else:
            cfg['render_mode'] = 'final'
            out = filedialog.asksaveasfilename(
                defaultextension='.mp4',
                filetypes=[('MP4', '*.mp4')],
                initialfile=f'disc_{random.randint(1000, 9999)}.mp4')
            if not out:
                return
            cfg['output_path']  = out
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
                       f"--- {'PREVIEW' if is_preview else 'FULL RENDER'} COMPLETE:"
                       f" {cfg['output_path']} ---")
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
                self.after(0, self.progress.configure, {'mode': 'determinate', 'value': 0})

    # ------------------------------------------------------------------ playback
    def start_playback(self, path):
        self.stop_and_clear_playback()
        time.sleep(0.15)
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            self.log('ERROR: Cannot open preview video.')
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
                    import imageio_ffmpeg as _iio_ffmpeg
                    _ffmpeg = _iio_ffmpeg.get_ffmpeg_exe()
                except Exception:
                    _ffmpeg = 'ffmpeg'
                subprocess.run(
                    [_ffmpeg, '-y', '-i', path,
                     '-vn', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
                     wav_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=True
                )
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
        """Loop preview video with time-based pacing. Re-open on each cycle."""
        W, H = 640, 360
        while not self.stop_playback.is_set():
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                break
            fps = cap.get(cv2.CAP_PROP_FPS) or 24
            frame_dur = 1.0 / fps
            loop_start = time.time()
            frame_idx = 0

            while not self.stop_playback.is_set():
                ret, frame = cap.read()
                if not ret:
                    break
                img   = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                img   = img.resize((W, H), Image.Resampling.LANCZOS)
                imgtk = ImageTk.PhotoImage(image=img)
                self.after(0, self._show_frame, imgtk)
                frame_idx += 1
                wait = (loop_start + frame_idx * frame_dur) - time.time()
                if wait > 0:
                    self.stop_playback.wait(wait)

            cap.release()

    def _show_frame(self, imgtk):
        if self.stop_playback.is_set():
            return
        self.player_label.imgtk = imgtk
        self.player_label.configure(image=imgtk, text='')

    def _audio_loop(self, wav_path):
        """Play WAV audio in a loop, synchronized with video playback."""
        if not _AUDIO_OK:
            return
        try:
            data, samplerate = _sf.read(wav_path, dtype='float32')
        except Exception:
            return
        while not self.stop_playback.is_set():
            try:
                _sd.play(data, samplerate)
                frames = len(data)
                duration = frames / samplerate
                self.stop_playback.wait(duration)
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
            self.video_cap.release()
            self.video_cap = None
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
            try:
                os.remove(self.temp_preview_path)
            except OSError:
                pass
        self.destroy()


if __name__ == '__main__':
    app = MainGUI()
    app.protocol('WM_DELETE_WINDOW', app.on_closing)
    app.mainloop()
