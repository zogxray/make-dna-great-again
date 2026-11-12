# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Make DNA Great Again (Linux onedir)."""

from __future__ import annotations

from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
ENTRY = ROOT / "ydna_tui.py"

datas: list = []
binaries: list = []
hiddenimports: list[str] = [
    "ydna",
    "ydna.__main__",
    "ydna.app",
    "httpx2",
    "httpcore2",
    "google.genai",
    "duckdb",
    "pyarrow",
    "numpy",
    "textual",
    "rich",
]

# Full collect only for packages that ship data/assets or lazy native modules.
for pkg in ("textual", "rich", "google.genai", "duckdb", "httpx2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pyarrow: keep native libs, skip pulling pandas/scipy/matplotlib via collect_all.
binaries += collect_dynamic_libs("pyarrow")
hiddenimports += [
    "pyarrow.lib",
    "pyarrow.parquet",
    "pyarrow.feather",
    "pyarrow.csv",
    "pyarrow.compute",
]

for dist in ("make-dna-great-again", "textual", "google-genai", "duckdb", "pyarrow", "httpx2"):
    try:
        datas += copy_metadata(dist)
    except Exception:  # noqa: BLE001
        pass

# Shared DNA venv may have heavy optional stacks — never ship them.
excludes = [
    "matplotlib",
    "mpl_toolkits",
    "scipy",
    "pandas",
    "PIL",
    "Pillow",
    "tkinter",
    "_tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "pytest_asyncio",
    "mypy",
    "mypyc",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ydna",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ydna",
)
