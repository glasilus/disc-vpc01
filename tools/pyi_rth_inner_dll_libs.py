"""PyInstaller runtime hook: register inner `.libs/` directories on Windows.

Wheel layout conventions
------------------------
Python wheels for the scientific stack ship native DLL dependencies
in two different ways:

  (a) **delvewheel** convention (numpy 1.26+, scipy, av, ...):
      `<pkg>.libs/` lives as a *sibling* of the package directory.
      PyInstaller has a purpose-built utility for this:
      `PyInstaller.utils.hooks.collect_delvewheel_libs_directory()`,
      and runtime DLL search is wired up by the bootloader.

  (b) **auditwheel-cibuildwheel** convention (numpy 1.24, sklearn,
      Pillow legacy, ...): `<pkg>/.libs/` lives *inside* the package
      directory. `--collect-all <pkg>` already bundles those DLLs —
      they ride along with the package — but Windows' default DLL
      search path does not recurse into subdirectories. So the .pyd
      that depends on them (e.g. `numpy/core/_multiarray_umath.pyd`
      → libopenblas) cannot find them at runtime, and import dies
      with the generic "DLL load failed" message.

This hook handles convention (b) at runtime: it walks `_MEIPASS`,
finds every `<pkg>/.libs/` subdirectory, and registers each via
`os.add_dll_directory()` (Python 3.8+ on Windows). For convention
(a) the per-package hook files in `tools/pyi_hooks/` (hook-scipy,
hook-av, ...) handle bundling, and PyInstaller's bootloader
already registers `_MEIPASS` on the search path so the bundled
sibling `<pkg>.libs/` directories are reachable from there.

Why this is the right tool, not a workaround
--------------------------------------------
PyInstaller's `collect_delvewheel_libs_directory` API only handles
the sibling layout (a). There is no equivalent built-in for the
inner-`.libs/` layout (b) because PyInstaller's static collection
already places those DLLs in the right relative location — what's
missing is the *runtime* search-path registration. `--runtime-hook`
is PyInstaller's documented mechanism for exactly that:
"Custom runtime hook code that runs before the bundled python
imports, intended for situations like setting environment
variables or extending the DLL search path."

Refs:
- https://pyinstaller.org/en/stable/spec-files.html#using-the-spec-file-built-in-runtime-hooks
- https://pyinstaller.org/en/stable/hooks.html#PyInstaller.utils.hooks.collect_delvewheel_libs_directory
"""
import os
import sys

# add_dll_directory is Windows-only and Python 3.8+. On macOS/Linux
# this hook is a no-op — the dynamic linker uses RPATH/RUNPATH and
# PyInstaller already sets those correctly.
def _diag(msg):
    """Append a single diagnostic line to dll_search.log next to the
    executable. Used to verify that this runtime hook actually runs and
    to record what it did. Stripped to a no-op once the bundle works.
    """
    try:
        log = os.path.join(os.path.dirname(sys.executable), "dll_search.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


_diag("=" * 60)
_diag(f"runtime hook fired. _MEIPASS={getattr(sys, '_MEIPASS', None)}")
_diag(f"add_dll_directory available={hasattr(os, 'add_dll_directory')}")

if hasattr(os, "add_dll_directory") and getattr(sys, "_MEIPASS", None):
    _base = sys._MEIPASS
    _diag(f"scanning {_base}")
    try:
        _entries = sorted(os.listdir(_base))
        _diag(f"  top-level entries: {len(_entries)}")
        for _entry in _entries:
            _full = os.path.join(_base, _entry)
            # Sibling .libs (delvewheel convention).
            if os.path.isdir(_full) and _entry.endswith(".libs"):
                _diag(f"  sibling-libs: {_entry}/")
                try:
                    os.add_dll_directory(_full)
                    _diag(f"    add_dll_directory OK")
                except OSError as e:
                    _diag(f"    add_dll_directory FAIL: {e}")
            # Inner .libs (auditwheel/cibuildwheel convention).
            _inner = os.path.join(_full, ".libs")
            if os.path.isdir(_inner):
                _dlls = [f for f in os.listdir(_inner)
                         if f.lower().endswith(".dll")]
                _diag(f"  inner-libs: {_entry}/.libs/ ({len(_dlls)} DLL)")
                try:
                    os.add_dll_directory(_inner)
                    _diag(f"    add_dll_directory OK")
                except OSError as e:
                    _diag(f"    add_dll_directory FAIL: {e}")
        # Also walk one level deeper inside numpy/scipy in case PyInstaller
        # placed DLLs directly in `numpy/core/` etc. — diagnostic only.
        for _key in ("numpy", "scipy", "sklearn"):
            _pkg = os.path.join(_base, _key)
            if os.path.isdir(_pkg):
                _diag(f"  {_key}/ contents: {sorted(os.listdir(_pkg))[:20]}")
    except OSError as e:
        _diag(f"scan FAIL: {e}")
