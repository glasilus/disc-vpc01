"""Точка входа для `python -m vpc`.

Запускает Tk GUI. Тот же модуль работает и при прямом `python -m vpc.gui`
через блок `if __name__ == '__main__'` в `vpc/gui.py`.
"""
import sys as _sys
import subprocess as _subprocess

# PyInstaller --splash подмешивает модуль `pyi_splash` в рантайме, чтобы
# держать окно сплэша открытым, пока onefile-бандл распаковывается и
# импорты Python завершаются. На холодном старте это может занимать 1-5
# минут (дерево librosa/numba/scipy/llvmlite). Без сплэша пользователи
# решат, что программа зависла. Импорт мягкий - вне собранного билда
# модуля просто нет.
try:
    import pyi_splash as _pyi_splash  # type: ignore
except Exception:
    _pyi_splash = None

if _pyi_splash is not None:
    try:
        _pyi_splash.update_text('Loading Disc VPC 01...')
    except Exception:
        pass

# На Windows любой subprocess.Popen (и subprocess.run, который использует
# его внутри) мелькает чёрным окном консоли, если явно не подавить это
# через creationflags + STARTUPINFO. ffmpeg запускается из нескольких мест
# в коде; патч Popen в одном месте закрывает все случаи разом, включая
# те, что могут добавить сторонние библиотеки.
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
    """Сбрасывает полный traceback в файл `crash.log` рядом с exe.

    Диалог "Unhandled exception" от бутлоадера PyInstaller показывает
    только первые несколько строк сообщения - бесполезно для диагностики
    сбоев импорта (numpy C-ext, PIL и т.п.), где настоящая причина лежит
    на несколько кадров глубже. Запись traceback в соседний файл даёт
    пользователю что-то реальное для отправки в баг-репорт.

    Возвращает путь к записанному файлу (или пустую строку, если не вышло).
    """
    import os as _os
    import traceback as _tb
    base = _os.path.dirname(_sys.executable) if getattr(_sys, 'frozen', False) \
        else _os.getcwd()
    target = _os.path.join(base, 'crash.log')
    try:
        with open(target, 'w', encoding='utf-8') as f:
            f.write('Disc VPC 01 - startup crash\n')
            f.write(f'Python: {_sys.version}\n')
            f.write(f'Executable: {_sys.executable}\n')
            f.write(f'Frozen: {getattr(_sys, "frozen", False)}\n')
            f.write(f'_MEIPASS: {getattr(_sys, "_MEIPASS", None)}\n')
            f.write(f'cwd: {_os.getcwd()}\n')
            f.write('-' * 70 + '\n')
            _tb.print_exception(type(exc), exc, exc.__traceback__, file=f)
        return target
    except Exception:
        return ''


# Поздние импорты обёрнуты: раньше сбой здесь (numpy C-ext, отсутствующая
# DLL, битый бандл, коллизия non-ASCII путей) всплывал как общий диалог
# бутлоадера "Failed to execute script" без каких-либо деталей. Теперь
# полный traceback пишется в crash.log и исключение перебрасывается дальше,
# так что диалог всё ещё появляется, но настоящая причина лежит на диске.
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
    # Перебрасываем, чтобы бутлоадер всё равно показал диалог. crash.log
    # при этом уже содержит полный traceback, что бы диалог ни обрезал.
    raise


def main() -> None:
    try:
        app = MainGUI()
        app.protocol('WM_DELETE_WINDOW', app.on_closing)
        # Закрываем сплэш PyInstaller уже после создания Tk root. Если
        # сделать это после MainGUI(), сплэш держится всё время тяжёлых
        # импортов и инициализации Tk и пропадает ровно тогда, когда
        # настоящее окно готово к отрисовке.
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
