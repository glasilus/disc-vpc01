"""Single source of truth for runtime data paths.

Resolves where user-editable data files (presets.json, temp previews) live.
Behaves correctly in three deploy modes:

1. Development (`python -m vpc.gui` from a checkout)
   → repository root (parent of the `vpc/` package).

2. Frozen executable (PyInstaller `--onefile` or `--onedir`)
   → directory containing the executable. PyInstaller-bundled read-only
   resources still go through `sys._MEIPASS`, but user data must persist
   between runs, so we use the exe directory.

3. Installed wheel
   → user-config directory (`%APPDATA%/disc_vpc` on Windows,
   `~/.config/disc_vpc` elsewhere). Avoids dropping files inside
   site-packages.

Override with the `DISC_VPC_HOME` environment variable for any of the above.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


_PACKAGE_DIR = Path(__file__).resolve().parent          # …/vpc
_REPO_ROOT = _PACKAGE_DIR.parent                        # …/sync-break-ultimate


def _is_dev_checkout() -> bool:
    """True when the package lives next to a recognisable repo root."""
    return (_REPO_ROOT / 'requirements.txt').is_file() or \
           (_REPO_ROOT / 'README.MD').is_file()


def data_home() -> Path:
    """Directory where user data (presets.json, temp previews) is stored.

    Created on first access if missing.
    """
    override = os.environ.get('DISC_VPC_HOME')
    if override:
        p = Path(override).expanduser()
    elif getattr(sys, 'frozen', False):
        p = Path(sys.executable).resolve().parent
    elif _is_dev_checkout():
        p = _REPO_ROOT
    else:
        # Installed package, no checkout in sight — use per-user config dir.
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
