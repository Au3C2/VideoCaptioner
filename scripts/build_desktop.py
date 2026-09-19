#!/usr/bin/env python3
"""Build a desktop bundle for the current platform with PyInstaller."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT / "VideoCaptioner.spec"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
ARTIFACT_DIR = ROOT / "artifacts"
RUNTIME_DIR = BUILD_DIR / "desktop-runtime"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(ROOT), check=True, **kwargs)


def _version() -> str:
    try:
        import importlib.metadata

        return importlib.metadata.version("videocaptioner").lstrip("v")
    except Exception:
        pass
    try:
        result = subprocess.run(
            [sys.executable, "-m", "hatchling", "version"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().lstrip("v")
    except Exception:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lstrip("v")
    return "0.0.0-dev"


def ensure_version_file(version: str) -> None:
    version_file = ROOT / "videocaptioner" / "_version.py"
    if version_file.exists():
        return
    version_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    print(f"Generated {version_file.relative_to(ROOT)} ({version})")


def clean() -> None:
    for path in [BUILD_DIR, DIST_DIR, ARTIFACT_DIR]:
        if path.exists():
            print(f"Removing {path.relative_to(ROOT)}")
            shutil.rmtree(path)


def prepare_ffmpeg() -> None:
    """Download the current platform's static ffmpeg/ffprobe into runtime resources."""
    try:
        from static_ffmpeg.run import (
            get_or_fetch_platform_executables_else_raise,
            get_platform_key,
        )
    except ImportError as exc:
        raise RuntimeError(
            "static-ffmpeg is required for desktop builds. "
            "Run with: uv run --with pyinstaller --with static-ffmpeg python scripts/build_desktop.py"
        ) from exc

    runtime_bin = RUNTIME_DIR / "resource" / "bin"
    runtime_bin.mkdir(parents=True, exist_ok=True)
    cache_dir = BUILD_DIR / "static-ffmpeg" / get_platform_key()
    ffmpeg, ffprobe = get_or_fetch_platform_executables_else_raise(download_dir=str(cache_dir))
    for src in [Path(ffmpeg), Path(ffprobe)]:
        dst = runtime_bin / src.name
        if dst.exists():
            dst.chmod(dst.stat().st_mode | stat.S_IWUSR)
        shutil.copy2(src, dst)
        if platform.system() != "Windows":
            mode = dst.stat().st_mode
            dst.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Bundled {dst.relative_to(ROOT)}")


def build_pyinstaller() -> None:
    env = os.environ.copy()
    env["VIDEOCAPTIONER_DESKTOP_RUNTIME_DIR"] = str(RUNTIME_DIR)
    _run([
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC_FILE),
        "--noconfirm",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "pyinstaller"),
    ], env=env)


def _platform_tag() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower().replace("amd64", "x64").replace("x86_64", "x64")
    if system == "darwin":
        system = "macos"
    return f"{system}-{machine}"


def _archive_dir(source: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(source.parent))
    print(f"Created {archive.relative_to(ROOT)}")


def verify_bundle() -> None:
    bundle = DIST_DIR / "VideoCaptioner"
    suffix = ".exe" if platform.system() == "Windows" else ""
    required_exes = [
        bundle / f"VideoCaptioner{suffix}",
        bundle / f"VideoCaptioner-cli{suffix}",
    ]
    for exe in required_exes:
        if not exe.exists():
            raise RuntimeError(f"Executable not found: {exe}")

    data_root = bundle / "_internal"
    required = [
        data_root / "resource" / "assets" / "logo.png",
        data_root / "resource" / "fonts" / "NotoSansSC-Regular.ttf",
        data_root / "resource" / "subtitle_style" / "ass-default.json",
        data_root / "resource" / "bin" / ("ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"),
        data_root / "resource" / "bin" / ("ffprobe.exe" if platform.system() == "Windows" else "ffprobe"),
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Missing bundled resources:\n  - " + "\n  - ".join(missing))
    print(f"Verified desktop bundle: {bundle.relative_to(ROOT)}")


def archive(version: str) -> None:
    bundle = DIST_DIR / "VideoCaptioner"
    tag = _platform_tag()
    _archive_dir(bundle, ARTIFACT_DIR / f"VideoCaptioner-{version}-{tag}-portable.zip")
    app = DIST_DIR / "VideoCaptioner.app"
    if app.exists():
        _archive_dir(app, ARTIFACT_DIR / f"VideoCaptioner-{version}-{tag}-app.zip")


def find_iscc() -> Path | None:
    """Locate the Inno Setup compiler (ISCC.exe)."""
    import winreg

    candidates = []
    env_iscc = os.environ.get("VIDEOCAPTIONER_ISCC")
    if env_iscc:
        candidates.append(Path(env_iscc))
    which = shutil.which("ISCC")
    if which:
        candidates.append(Path(which))
    for base in [
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        # Per-user Inno Setup install location (PrivilegesRequired=lowest)
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    ]:
        if base:
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
    registry_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{8A5D2E9C-4B7F-4E63-9C1A-52D64B7F3A10}_is1"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{8A5D2E9C-4B7F-4E63-9C1A-52D64B7F3A10}_is1"),
    ]
    for hive, key in registry_keys:
        try:
            with winreg.OpenKey(hive, key) as reg_key:
                install_location, _ = winreg.QueryValueEx(reg_key, "InstallLocation")
                candidates.append(Path(install_location) / "ISCC.exe")
        except OSError:
            pass
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def build_installer(version: str) -> Path:
    """Compile packaging/installer.iss into artifacts/VideoCaptioner-<version>-windows-setup.exe."""
    iscc = find_iscc()
    if iscc is None:
        raise RuntimeError(
            "Inno Setup compiler (ISCC.exe) not found. Install it with "
            "'winget install JRSoftware.InnoSetup' (local) or 'choco install innosetup -y' (CI), "
            "or set VIDEOCAPTIONER_ISCC to the ISCC.exe path."
        )
    _run([
        str(iscc),
        f"/DMyAppVersion={version}",
        str(ROOT / "packaging" / "installer.iss"),
    ])
    installer = ARTIFACT_DIR / f"VideoCaptioner-{version}-windows-setup.exe"
    if not installer.exists():
        raise RuntimeError(f"Installer was not produced: {installer}")
    print(f"Created {installer.relative_to(ROOT)}")
    return installer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="Remove build/dist/artifacts first")
    parser.add_argument("--no-archive", action="store_true", help="Build and verify without creating zip archives")
    parser.add_argument("--no-installer", action="store_true", help="Skip the Inno Setup installer (portable zip only)")
    args = parser.parse_args()

    version = _version()
    if args.clean:
        clean()
    ensure_version_file(version)
    prepare_ffmpeg()
    build_pyinstaller()
    verify_bundle()
    if not args.no_archive:
        archive(version)
    if platform.system() == "Windows" and not args.no_installer:
        build_installer(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
