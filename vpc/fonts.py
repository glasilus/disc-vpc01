"""Разрешение системных шрифтов (имя -> путь -> PIL ImageFont).

Вынесено из Paint-редактора, чтобы делиться с эффектом субтитров: и GUI
(выбор шрифта в списке), и рендер (отрисовка текста в маску) должны одинаково
отображать имя шрифта в файл. Результаты кэшируются на процесс.

Кросс-платформенно: на Windows читается реестр шрифтов, на macOS/Linux
сканируются стандартные каталоги шрифтов. Финальный фолбэк - масштабируемый
шрифт по умолчанию из Pillow, поэтому отрисовка текста работает на любой ОС,
даже если ни один именованный шрифт не нашёлся.
"""
from __future__ import annotations

import os
import platform
from functools import lru_cache

from PIL import ImageFont

# Стандартные каталоги шрифтов по ОС (кроме Windows - там реестр).
_FONT_DIRS = {
    'Darwin': [
        '/System/Library/Fonts',
        '/System/Library/Fonts/Supplemental',
        '/Library/Fonts',
        os.path.expanduser('~/Library/Fonts'),
    ],
    'Linux': [
        '/usr/share/fonts',
        '/usr/local/share/fonts',
        os.path.expanduser('~/.fonts'),
        os.path.expanduser('~/.local/share/fonts'),
    ],
}

# Запасные имена, которые могут разрешиться самим PIL/системой без пути.
_FALLBACK = {
    'Arial': 'arial.ttf',
    'Courier New': 'cour.ttf',
    'Times New Roman': 'times.ttf',
    'Tahoma': 'tahoma.ttf',
    'Verdana': 'verdana.ttf',
    'DejaVu Sans': 'DejaVuSans.ttf',
}

_FONT_EXTS = ('.ttf', '.otf', '.ttc')


def _scan_dirs(dirs) -> dict:
    fonts: dict = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            for root, _sub, files in os.walk(d):
                for fn in files:
                    if fn.lower().endswith(_FONT_EXTS):
                        name = os.path.splitext(fn)[0]
                        fonts.setdefault(name, os.path.join(root, fn))
        except OSError:
            continue
    return fonts


def _scan_windows_registry() -> dict:
    fonts: dict = {}
    try:
        import winreg  # только Windows
    except ImportError:
        return fonts
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(
                hive, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
            windir = os.environ.get('WINDIR', 'C:\\Windows')
            local_fonts_dir = os.path.expandvars(
                r'%LOCALAPPDATA%\Microsoft\Windows\Fonts')
            num_values = winreg.QueryInfoKey(key)[1]
            for i in range(num_values):
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                    clean_name = name.split('(')[0].strip()
                    if not data.lower().endswith(('.ttf', '.otf', '.ttc')):
                        continue
                    if os.path.isabs(data):
                        fonts[clean_name] = data
                    else:
                        sys_path = os.path.join(windir, 'Fonts', data)
                        if os.path.exists(sys_path):
                            fonts[clean_name] = sys_path
                        else:
                            user_path = os.path.join(local_fonts_dir, data)
                            fonts[clean_name] = (user_path
                                                 if os.path.exists(user_path)
                                                 else data)
                except OSError:
                    continue
            winreg.CloseKey(key)
        except Exception:
            pass
    return fonts


@lru_cache(maxsize=1)
def get_system_fonts() -> dict:
    """Возвращает {имя_шрифта: путь_к_ttf}. Кэшируется целиком."""
    system = platform.system()
    if system == 'Windows':
        fonts = _scan_windows_registry()
    else:
        fonts = _scan_dirs(_FONT_DIRS.get(system, _FONT_DIRS['Linux']))
    # Запасные имена добавляем только если своих не нашлось - на голой ОС без
    # шрифтов список всё равно будет непустым, а get_pil_font подстрахует
    # масштабируемым дефолтом Pillow.
    for name, path in _FALLBACK.items():
        fonts.setdefault(name, path)
    return fonts


def default_font_name() -> str:
    """Имя шрифта по умолчанию, реально присутствующего в системе, если можно."""
    fonts = get_system_fonts()
    for pref in ('Arial', 'Helvetica', 'DejaVu Sans', 'DejaVuSans',
                 'Liberation Sans', 'Verdana'):
        if pref in fonts:
            return pref
    names = sorted(fonts.keys())
    return names[0] if names else 'Arial'


@lru_cache(maxsize=256)
def get_pil_font(name: str, size: int):
    """PIL-шрифт по имени и размеру. Кэшируется по (имя, размер).

    Порядок разрешения: точный путь из карты шрифтов -> набор частых имён
    (PIL сам поищет их в системных путях) -> масштабируемый шрифт по умолчанию
    из Pillow. Последний доступен всегда, поэтому отрисовка не падает ни на
    одной ОС, даже если именованный шрифт не найден.
    """
    size = max(1, int(size))
    candidates = []
    path = get_system_fonts().get(name)
    if path:
        candidates.append(path)
    candidates += [name, 'arial.ttf', 'DejaVuSans.ttf', 'LiberationSans-Regular.ttf']
    for cand in candidates:
        if not cand:
            continue
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    # Масштабируемый дефолт (Pillow >= 10.1). На старых Pillow - фикс. bitmap.
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()
