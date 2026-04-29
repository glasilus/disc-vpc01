"""Entry point for `python -m vpc`.

Runs the Tk GUI. The same module powers `python -m vpc.gui` directly via
the `if __name__ == '__main__'` block in `vpc/gui.py`.
"""
import sys as _sys
import subprocess as _subprocess

# PyInstaller --splash injects a `pyi_splash` module at runtime that the
# bootloader uses to keep a splash window alive while the onefile bundle
# extracts and Python imports finish. On a cold start this can be 1–5
# minutes for our build (librosa/numba/scipy/llvmlite tree). Without the
# splash, users assume the program hung. Soft import — outside a frozen
# build the module doesn't exist.
try:
    import pyi_splash as _pyi_splash  # type: ignore
except Exception:
    _pyi_splash = None

if _pyi_splash is not None:
    try:
        _pyi_splash.update_text('Loading Disc VPC 01...')
    except Exception:
        pass

# On Windows, every subprocess.Popen (and subprocess.run, which uses it
# internally) flashes a black console window unless we explicitly suppress
# it via creationflags + STARTUPINFO. We shell out to ffmpeg from several
# call sites; patching Popen once here covers all of them, including any
# that third-party libs may add later.
if _sys.platform == 'win32':
    _orig_popen_init = _subprocess.Popen.__init__

    def _silent_popen_init(self, *args, **kwargs):
        if 'creationflags' not in kwargs:
            kwargs['creationflags'] = _subprocess.CREATE_NO_WINDOW
        if 'startupinfo' not in kwargs:
            si = _subprocess.STARTUPINFO()
            si.dwFlags |= _subprocess.STARTF_USESHOWWINDOW
            kwargs['startupinfo'] = si
        _orig_popen_init(self, *args, **kwargs)

    _subprocess.Popen.__init__ = _silent_popen_init

try:
    from .gui import MainGUI
except ImportError:
    from vpc.gui import MainGUI


def main() -> None:
    app = MainGUI()
    app.protocol('WM_DELETE_WINDOW', app.on_closing)
    # Close the PyInstaller splash window once the Tk root exists. Doing
    # it after MainGUI() means the splash stays up through the heavy
    # imports + Tk init, then disappears the moment the real window is
    # ready to be drawn.
    if _pyi_splash is not None:
        try:
            _pyi_splash.close()
        except Exception:
            pass
    app.mainloop()


if __name__ == '__main__':
    main()
