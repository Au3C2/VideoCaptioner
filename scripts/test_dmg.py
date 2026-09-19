#!/usr/bin/env python3
"""Acceptance test for the packaged macOS DMG artifact.

Covers the full install lifecycle in the style of the Windows
test_installer.py flow:

1. Mount the DMG read-only via hdiutil.
2. Verify the drag-install layout: one .app, the Applications symlink,
   the background image, optional pre-baked .DS_Store, and a valid
   (ad-hoc) code signature.
3. Verify the .app bundle tree: GUI + CLI executables and the bundled
   resource/bin ffmpeg/ffprobe.
4. Install: copy the .app into a target directory (default /Applications
   when writable, otherwise a temp sandbox) and run the CLI smoke flow
   from the installed location.
5. Uninstall: remove the installed .app and verify nothing is left behind.

The DMG is always detached in a finally block.

Usage:
  python scripts/test_dmg.py                        # find DMG in artifacts/
  python scripts/test_dmg.py --dmg path/to.dmg
  python scripts/test_dmg.py --skip-gui --install-dir /tmp/sandbox
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from smoke_desktop import run_smoke

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = ROOT / "artifacts"

GUI_PROBE_SECONDS = 10


def _detach_stale_attachments(dmg_path: Path) -> None:
    """Detach leftover mounts of this exact image (double attach fails with
    'Resource busy' on hdiutil)."""
    try:
        info = subprocess.run(
            ["hdiutil", "info"], capture_output=True, text=True, check=True
        ).stdout
    except Exception:
        return
    current_image: str | None = None
    for line in info.splitlines():
        line = line.strip()
        if line.startswith("image-path"):
            current_image = line.split(":", 1)[1].strip()
        elif line.startswith("/dev/disk") and current_image == str(dmg_path):
            subprocess.run(
                ["hdiutil", "detach", line.split()[0], "-force"], capture_output=True
            )


def _find_dmg() -> Path:
    candidates = sorted(ARTIFACT_DIR.glob("VideoCaptioner-*-macos-*.dmg"))
    if not candidates:
        raise FileNotFoundError(
            f"No VideoCaptioner-*-macos-*.dmg found in {ARTIFACT_DIR} — "
            "build one with scripts/build_desktop.py first"
        )
    return candidates[-1]


def _require(path: Path, description: str) -> None:
    if not path.exists():
        raise RuntimeError(f"DMG verification failed, missing {description}: {path}")
    print(f"PASS: {description} ({path})")


def _verify_layout(mount_point: Path) -> Path:
    """Verify the drag-install layout and return the .app bundle path."""
    apps = [entry for entry in mount_point.iterdir() if entry.suffix == ".app"]
    if len(apps) != 1:
        raise RuntimeError(f"Expected exactly one .app in the DMG, found: {apps}")
    app = apps[0]

    _require(app, "app bundle")
    _require(mount_point / "Applications", "Applications drag-install symlink")
    if not (mount_point / "Applications").is_symlink():
        raise RuntimeError("Applications entry exists but is not a symlink")

    backgrounds = list((mount_point / ".background").glob("*.png")) if (
        mount_point / ".background"
    ).exists() else []
    if backgrounds and backgrounds[0].stat().st_size > 1_000:
        print(f"PASS: drag-install background image ({backgrounds[0].name})")
    else:
        raise RuntimeError("DMG missing a usable .background/dmg-background.png")

    _require(mount_point / ".DS_Store", "Finder layout (.DS_Store with icon positions)")
    if (mount_point / ".VolumeIcon.icns").exists():
        print("PASS: DMG volume icon present")
    else:
        print("WARNING: no .VolumeIcon.icns — mounted volume uses the default icon")

    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
        capture_output=True,
        text=True,
    )
    print("PASS: ad-hoc code signature verifies (--deep --strict)")

    macos_dir = app / "Contents" / "MacOS"
    _require(macos_dir / "VideoCaptioner", "GUI executable")
    _require(macos_dir / "VideoCaptioner-cli", "CLI executable")
    # PyInstaller BUNDLE relocates the onedir COLLECT: binaries (and the
    # bundled ffmpeg/ffprobe) go to Contents/Frameworks, data files to
    # Contents/Resources.
    _require(
        app / "Contents" / "Frameworks" / "resource" / "bin" / "ffmpeg",
        "bundled ffmpeg",
    )
    _require(
        app / "Contents" / "Frameworks" / "resource" / "bin" / "ffprobe",
        "bundled ffprobe",
    )
    _require(
        app / "Contents" / "Resources" / "resource" / "assets" / "logo.png",
        "bundled app assets",
    )
    _require(
        app / "Contents" / "Resources" / "resource" / "subtitle_style" / "ass-default.json",
        "bundled subtitle style",
    )
    return app


def _default_install_dir() -> Path:
    """Prefer a real /Applications install; fall back to a sandbox dir when
    not writable (restricted machines)."""
    applications = Path("/Applications")
    if os_writable(applications):
        return applications
    sandbox = Path(tempfile.mkdtemp(prefix="videocaptioner-dmg-install-"))
    print(f"/Applications not writable, using sandbox install dir: {sandbox}")
    return sandbox


def os_writable(directory: Path) -> bool:
    probe = directory / ".videocaptioner-dmg-write-probe"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _install(app: Path, install_dir: Path) -> Path:
    installed = install_dir / app.name
    shutil.rmtree(installed, ignore_errors=True)
    print(f"+ Installing {app.name} into {install_dir}")
    shutil.copytree(app, installed, symlinks=True)
    _require(installed / "Contents" / "MacOS" / "VideoCaptioner-cli", "installed CLI executable")
    return installed


def _probe_gui(installed: Path) -> None:
    exe = installed / "Contents" / "MacOS" / "VideoCaptioner"
    print(f"+ Launching GUI for liveness probe: {exe}")
    process = subprocess.Popen(
        [str(exe)],
        cwd=str(installed.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(GUI_PROBE_SECONDS)
    if process.poll() is not None:
        raise RuntimeError(
            f"GUI process exited during liveness probe (returncode={process.returncode})"
        )
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    print("GUI liveness probe passed")


def _uninstall(installed: Path) -> None:
    print(f"+ Uninstalling {installed}")
    shutil.rmtree(installed)
    if installed.exists():
        raise RuntimeError(f"Uninstall failed, {installed} still exists")
    print("Uninstall passed: no leftover files")


def test_dmg(dmg: Path, install_dir: Path, skip_gui: bool) -> None:
    print(f"Testing DMG artifact: {dmg} ({dmg.stat().st_size / 1024 / 1024:.1f} MB)")
    _detach_stale_attachments(dmg)
    mount_point = Path(tempfile.mkdtemp(prefix="videocaptioner-dmg-mount-"))
    owned_install = False
    try:
        subprocess.run(
            [
                "hdiutil", "attach", str(dmg),
                "-mountpoint", str(mount_point),
                "-nobrowse", "-readonly", "-quiet",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print("PASS: DMG mounted read-only")

        app = _verify_layout(mount_point)
        if not install_dir.exists():
            install_dir.mkdir(parents=True)
            owned_install = True
        installed = _install(app, install_dir)
        run_smoke(install_dir)
        print("PASS: CLI smoke flow from installed location")
        if not skip_gui:
            _probe_gui(installed)
        _uninstall(installed)
        if owned_install:
            shutil.rmtree(install_dir, ignore_errors=True)
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(mount_point), "-force", "-quiet"],
            capture_output=True,
        )
        shutil.rmtree(mount_point, ignore_errors=True)
        print("Cleaned up DMG mount point")


def main() -> int:
    if platform.system() != "Darwin":
        print("test_dmg.py only runs on macOS")
        return 1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dmg", type=Path, help="Path to the DMG (default: newest in artifacts/)")
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=None,
        help="Install target directory (default: /Applications, or a temp sandbox if not writable)",
    )
    parser.add_argument("--skip-gui", action="store_true", help="Skip the GUI liveness probe")
    args = parser.parse_args()

    dmg = args.dmg if args.dmg else _find_dmg()
    if not dmg.is_file():
        raise FileNotFoundError(f"DMG not found: {dmg}")
    install_dir = args.install_dir if args.install_dir else _default_install_dir()

    test_dmg(dmg, install_dir, args.skip_gui)
    print("DMG acceptance test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
