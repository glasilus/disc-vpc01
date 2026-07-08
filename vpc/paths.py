"""Единая точка правды для путей рантайм-данных.

Определяет, где лежат пользовательские файлы (presets.json, временные
превью). Корректно работает в трёх сценариях запуска:

1. Разработка (`python -m vpc.gui` из чекаута)
   -> корень репозитория (родитель пакета `vpc/`).

2. Собранный exe (PyInstaller `--onefile` или `--onedir`)
   -> директория с исполняемым файлом. Read-only ресурсы, вшитые
   PyInstaller, всё равно идут через `sys._MEIPASS`, но пользовательские
   данные должны переживать перезапуски, поэтому используем директорию exe.

3. Установленный wheel
   -> пользовательская config-директория (`%APPDATA%/disc_vpc` на Windows,
   `~/.config/disc_vpc` на остальных). Так файлы не попадают внутрь
   site-packages.

Любой из вариантов можно переопределить переменной окружения `DISC_VPC_HOME`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


_PACKAGE_DIR = Path(__file__).resolve().parent          # .../vpc
_REPO_ROOT = _PACKAGE_DIR.parent                        # .../sync-break-ultimate


def _is_dev_checkout() -> bool:
    """True, если пакет лежит рядом с узнаваемым корнем репозитория."""
    return (_REPO_ROOT / 'requirements.txt').is_file() or \
           (_REPO_ROOT / 'README.MD').is_file()


def data_home() -> Path:
    """Директория, где хранятся пользовательские данные (presets.json,
    временные превью).

    Создаётся при первом обращении, если ещё не существует.
    """
    override = os.environ.get('DISC_VPC_HOME')
    if override:
        p = Path(override).expanduser()
    elif getattr(sys, 'frozen', False):
        if sys.platform == 'darwin':
            # Директория exe внутри .app (Contents/MacOS) лежит в бандле:
            # после установки в /Applications она read-only, а запись туда
            # ещё и ломает подпись кода. Поэтому храним данные вне бандла.
            p = Path.home() / 'Library' / 'Application Support' / 'disc_vpc'
        else:
            p = Path(sys.executable).resolve().parent
    elif _is_dev_checkout():
        p = _REPO_ROOT
    else:
        # Установленный пакет вне чекаута - используем per-user config dir.
        if sys.platform == 'win32':
            base = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
            p = base / 'disc_vpc'
        elif sys.platform == 'darwin':
            p = Path.home() / 'Library' / 'Application Support' / 'disc_vpc'
        else:
            base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
            p = base / 'disc_vpc'
    p.mkdir(parents=True, exist_ok=True)
    return p


def presets_path() -> Path:
    return data_home() / 'presets.json'


def temp_preview_path() -> Path:
    return data_home() / 'temp_preview.mp4'
