"""Cross-platform PyInstaller build wrapper.

Why this exists
---------------
A few of our dependencies — numpy 1.24, scikit-learn — ship native
DLLs inside `<pkg>/.libs/` (cibuildwheel/auditwheel inner-libs
convention). PyInstaller's `--collect-all` and the standard
`collect_dynamic_libs()` hook utility skip these directories on
Windows, leading to "DLL load failed: module not found" errors
in the resulting bundle. We've verified this empirically by
inspecting the produced `_internal/` tree — `numpy/.libs/` is
absent despite `--collect-all numpy` being passed.

The reliable workaround is `--add-binary src;dst` for each DLL,
which goes through a different code path that does not skip
dot-prefixed directories. This script discovers those DLLs in
the active Python's site-packages and emits the corresponding
flags before invoking PyInstaller.

It also handles the "sibling" delvewheel layout (scipy.libs/,
av.libs/) the same way for consistency, so the entire DLL story
lives in one explicit place instead of being split between hooks
and CLI flags.
"""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import subprocess
import sys
from pathlib import Path


# Packages that may carry native DLL deps either as inner `.libs/`
# (cibuildwheel) or sibling `<pkg>.libs/` (delvewheel). The list is a
# superset — packages not actually installed are skipped silently.
LIBS_PACKAGES = ("numpy", "scipy", "sklearn", "av", "PIL")


def _find_libs_for(pkg: str) -> list[tuple[Path, str]]:
    """Return [(src_dll_path, dst_relative_dir), ...] for one package.

    Looks for both layouts:
      - inner: `<pkg>/.libs/` → bundle dest `<pkg>/.libs`
      - sibling: `<pkg>.libs/` → bundle dest `<pkg>.libs`
    """
    try:
        mod = importlib.import_module(pkg)
    except Exception:
        return []
    pkg_file = getattr(mod, "__file__", None)
    if not pkg_file:
        return []
    pkg_dir = Path(pkg_file).resolve().parent
    results: list[tuple[Path, str]] = []
    for src_dir, dest in (
        (pkg_dir / ".libs", f"{pkg}/.libs"),
        (pkg_dir.parent / f"{pkg}.libs", f"{pkg}.libs"),
    ):
        if src_dir.is_dir():
            for entry in sorted(src_dir.iterdir()):
                if entry.is_file():
                    results.append((entry, dest))
    return results


def _add_binary_flags() -> list[str]:
    """Build --add-binary flags for every discovered .libs DLL."""
    sep = ";" if platform.system() == "Windows" else ":"
    flags: list[str] = []
    for pkg in LIBS_PACKAGES:
        entries = _find_libs_for(pkg)
        if not entries:
            continue
        print(f"[build] {pkg}: {len(entries)} native DLL(s) to add")
        for src, dest in entries:
            flags.append(f"--add-binary={src}{sep}{dest}")
    return flags


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("onefile", "onedir"),
                        default="onedir",
                        help="PyInstaller bundle layout.")
    parser.add_argument("--name", default="DiscVPC01")
    parser.add_argument("--icon", required=False, default=None)
    parser.add_argument("--splash", required=False, default=None)
    parser.add_argument("--manifest", required=False, default=None)
    parser.add_argument("--entry", default="vpc/__main__.py")
    args = parser.parse_args()

    sep = ";" if platform.system() == "Windows" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        f"--{args.mode}",
        "--noconsole",
        "--clean",
        "--noupx",
        "--name", args.name,
    ]
    if args.icon:
        cmd += ["--icon", args.icon]
    if args.splash:
        cmd += ["--splash", args.splash]
    if args.manifest:
        cmd += ["--manifest", args.manifest]

    # Bundle the runtime icon PNG so MainGUI can pick it up via iconphoto.
    cmd += [f"--add-data=CDDRIVE.png{sep}."]

    # Discovered native .libs DLLs. Goes through --add-binary which
    # bypasses PyInstaller's hook-based filters — this is what makes
    # numpy 1.24's inner `.libs` actually land in the bundle.
    cmd += _add_binary_flags()

    cmd += [
        "--runtime-hook", "tools/pyi_rth_inner_dll_libs.py",
    ]

    # Belt-and-suspenders --collect-all for everything we use. This is
    # cheap (PyInstaller dedupes) and catches anything not picked up by
    # static analysis.
    for pkg in (
        "numpy", "PIL", "cv2", "librosa", "scipy",
        "soundfile", "sounddevice", "cffi", "audioread",
        "pooch", "lazy_loader", "joblib",
        "imageio_ffmpeg", "numba", "llvmlite",
        "scenedetect", "opensimplex",
    ):
        cmd += [f"--collect-all={pkg}"]

    cmd += [
        "--hidden-import=scenedetect.detectors",
        "--collect-submodules=vpc",
        "--paths=.",
        args.entry,
    ]

    print("[build] running:", " ".join(repr(c) if " " in c else c for c in cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
