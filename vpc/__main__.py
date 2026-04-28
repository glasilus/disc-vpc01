"""Entry point for `python -m vpc`.

Runs the Tk GUI. The same module powers `python -m vpc.gui` directly via
the `if __name__ == '__main__'` block in `vpc/gui.py`.
"""
import sys as _sys
import subprocess as _subprocess

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
    app.mainloop()


if __name__ == '__main__':
    main()
