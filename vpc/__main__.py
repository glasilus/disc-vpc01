"""Entry point for `python -m vpc`.

Runs the Tk GUI. The same module powers `python -m vpc.gui` directly via
the `if __name__ == '__main__'` block in `vpc/gui.py`.
"""
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
