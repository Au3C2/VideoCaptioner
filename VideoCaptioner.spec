# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for the VideoCaptioner desktop bundle.

Produces two executables sharing one COLLECT (``lib`` runtime dir):
- VideoCaptioner.exe     windowed GUI entry (videocaptioner/gui_entry.py)
- VideoCaptioner-cli.exe console CLI entry (videocaptioner/__main__.py)
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

ROOT = Path(SPECPATH)
RUNTIME_DIR = Path(os.environ.get("VIDEOCAPTIONER_DESKTOP_RUNTIME_DIR", ROOT / "build" / "desktop-runtime"))


def _data(src: Path, dest: str):
    return (str(src), dest)


datas = [
    _data(ROOT / "resource" / "assets", "resource/assets"),
    _data(ROOT / "resource" / "fonts", "resource/fonts"),
    _data(ROOT / "resource" / "subtitle_style", "resource/subtitle_style"),
    _data(ROOT / "resource" / "translations", "resource/translations"),
    _data(ROOT / "videocaptioner" / "core" / "prompts", "videocaptioner/core/prompts"),
]

runtime_bin = RUNTIME_DIR / "resource" / "bin"
if runtime_bin.exists():
    datas.append(_data(runtime_bin, "resource/bin"))

hiddenimports = [
    "PyQt5",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt5.QtMultimedia",
    "PyQt5.QtMultimediaWidgets",
    "PyQt5.QtSvg",
    "PyQt5.sip",
    "openai",
    "requests",
    "edge_tts",
    "diskcache",
    "yt_dlp",
    "modelscope",
    "psutil",
    "json_repair",
    "langdetect",
    "pydub",
    "tenacity",
    "GPUtil",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "fontTools",
    "fontTools.ttLib",
]
hiddenimports += collect_submodules("qfluentwidgets")

excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "numpy.testing",
    "pytest",
    "pyright",
    "ruff",
    "test",
    "unittest",
]

# MSVC runtime DLLs must not be UPX-compressed (loader compatibility)
upx_exclude = [
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "concrt140.dll",
    "ucrtbase.dll",
]

icon = str(ROOT / "resource" / "assets" / "logo.ico")
if not Path(icon).exists():
    icon = None


def _analysis(entry: str):
    return Analysis(
        [str(ROOT / entry)],
        pathex=[str(ROOT)],
        binaries=[],
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=excludes,
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=block_cipher,
        noarchive=False,
    )


gui_a = _analysis("videocaptioner/gui_entry.py")
cli_a = _analysis("videocaptioner/__main__.py")

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="VideoCaptioner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    contents_directory="lib",
)

cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="VideoCaptioner-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    contents_directory="lib",
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    gui_a.binaries,
    gui_a.zipfiles,
    gui_a.datas,
    cli_a.binaries,
    cli_a.zipfiles,
    cli_a.datas,
    strip=False,
    upx=True,
    upx_exclude=upx_exclude,
    name="VideoCaptioner",
)

if sys.platform == "darwin":
    # build_desktop.py renders resource/assets logo into a .icns and exports
    # its path here; without it the bundle falls back to PyInstaller's
    # default Python icon.
    bundle_icon = os.environ.get("VIDEOCAPTIONER_APP_ICON", "")
    bundle_kwargs = {"icon": bundle_icon} if bundle_icon and Path(bundle_icon).exists() else {}
    bundle_version = os.environ.get("VIDEOCAPTIONER_APP_VERSION", "") or "0.0.0"
    app = BUNDLE(
        coll,
        name="VideoCaptioner.app",
        version=bundle_version,
        bundle_identifier="com.weifeng.videocaptioner",
        info_plist={
            "CFBundleName": "VideoCaptioner",
            "CFBundleDisplayName": "VideoCaptioner",
            "NSHighResolutionCapable": True,
            # The COLLECT contains the console CLI executable, so PyInstaller
            # marks the whole bundle LSBackgroundOnly (no Dock icon, no
            # Cmd-Tab). The GUI is a regular windowed app — override it.
            "LSBackgroundOnly": False,
        },
        **bundle_kwargs,
    )
