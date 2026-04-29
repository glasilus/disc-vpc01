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

def _write_crash_log(exc: BaseException) -> str:
    """Dump full traceback to a `crash.log` file next to the executable.

    The PyInstaller bootloader's "Unhandled exception" dialog only shows
    the first few lines of the message — useless for diagnosing import
    failures (numpy C-ext, PIL, etc.) where the real cause is several
    frames deep. Writing the traceback to a sibling file gives the user
    something to actually send back.

    Returns the path written (or an empty string if everything failed).
    """
    import os as _os
    import traceback as _tb
    base = _os.path.dirname(_sys.executable) if getattr(_sys, 'frozen', False) \
        else _os.getcwd()
    target = _os.path.join(base, 'crash.log')
    try:
        with open(target, 'w', encoding='utf-8') as f:
            f.write('Disc VPC 01 — startup crash\n')
            f.write(f'Python: {_sys.version}\n')
            f.write(f'Executable: {_sys.executable}\n')
            f.write(f'Frozen: {getattr(_sys, "frozen", False)}\n')
            f.write(f'_MEIPASS: {getattr(_sys, "_MEIPASS", "n/a")}\n')
            f.write(f'cwd: {_os.getcwd()}\n')
            f.write('-' * 70 + '\n')
            _tb.print_exception(type(exc), exc, exc.__traceback__, file=f)
        return target
    except Exception:
        return ''


# Late imports are wrapped: a failure here (numpy C-ext, missing DLL,
# corrupt bundle, non-ASCII path collision) used to surface as the
# bootloader's generic "Failed to execute script" dialog with no
# actionable detail. Now we write the full traceback to crash.log AND
# rethrow, so the dialog still pops but the real cause is on disk.
try:
    try:
        from .gui import MainGUI
    except ImportError:
        from vpc.gui import MainGUI
except BaseException as _import_err:
    _log = _write_crash_log(_import_err)
    if _pyi_splash is not None:
        try: _pyi_splash.close()
        except Exception: pass
    # Re-raise so the bootloader still shows its dialog. crash.log now
    # contains the real traceback regardless of what the dialog truncates.
    raise


def main() -> None:
    try:
        app = MainGUI()
        app.protocol('WM_DELETE_WINDOW', app.on_closing)
        # Close the PyInstaller splash window once the Tk root exists.
        # Doing it after MainGUI() means the splash stays up through
        # the heavy imports + Tk init, then disappears the moment the
        # real window is ready to be drawn.
        if _pyi_splash is not None:
            try:
                _pyi_splash.close()
            except Exception:
                pass
        app.mainloop()
    except BaseException as e:
        _write_crash_log(e)
        raise


if __name__ == '__main__':
    main()
