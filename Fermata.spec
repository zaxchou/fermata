# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition for Fermata.

Folder (onedir) build, not onefile: the autostart entry points at the EXE, and a
onefile build would re-extract ~40 MB to a temp folder on every logon. onedir
starts immediately, which is what a background utility should do.

Run:  pyinstaller --noconfirm Fermata.spec
"""
import os

ROOT = os.path.abspath(os.getcwd())

a = Analysis(
    [os.path.join("app", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join("app", "ui", "index.html"), "ui"),
    ],
    # pywebview and pystray pick their platform backend at runtime, so the
    # static import graph never mentions them.
    hiddenimports=[
        "pystray._win32",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "pythonnet",
        "bottle",
        "proxy_tools",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # tkinter is absent on this interpreter anyway; excluding it keeps the
    # bundle from growing if a dependency drags it in.
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Fermata",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app: never show a console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join("assets", "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Fermata",
)
