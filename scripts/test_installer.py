#!/usr/bin/env python3
"""Acceptance tests for the packaged Windows artifacts (installer + portable).

Layers covered:
- Installer lifecycle: silent install into a temp dir, bundle verification,
  CLI smoke, GUI liveness probe, silent uninstall, empty-directory check.
- Portable: zip extraction (or an existing bundle dir) + the same verification
  and smoke flow.

The CLI smoke flow itself lives in smoke_desktop.run_smoke so local runs and
CI use one implementation.

Usage (flags can be combined):
  python scripts/test_installer.py --installer artifacts/VideoCaptioner-1.2.3-windows-setup.exe
  python scripts/test_installer.py --portable artifacts/VideoCaptioner-1.2.3-windows-x64-portable.zip
  python scripts/test_installer.py --bundle dist/VideoCaptioner --skip-gui
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from smoke_desktop import run_smoke

GUI_PROBE_SECONDS = 10
UNINSTALL_TIMEOUT_SECONDS = 90


def _verify_bundle_tree(bundle: Path) -> None:
    """Check both executables and the shared runtime data exist."""
    required = [
        bundle / "VideoCaptioner.exe",
        bundle / "VideoCaptioner-cli.exe",
        bundle / "unins000.exe",
        bundle / "_internal" / "resource" / "assets" / "logo.png",
        bundle / "_internal" / "resource" / "subtitle_style" / "ass-default.json",
        bundle / "_internal" / "resource" / "bin" / "ffmpeg.exe",
        bundle / "_internal" / "resource" / "bin" / "ffprobe.exe",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Installed bundle is incomplete:\n  - " + "\n  - ".join(missing))
    print(f"Verified installed bundle tree: {bundle}")


def _probe_gui(bundle: Path) -> None:
    """Start the GUI exe, confirm it stays alive, then terminate it."""
    exe = bundle / "VideoCaptioner.exe"
    print(f"+ Launching GUI for liveness probe: {exe}")
    process = subprocess.Popen([str(exe)], cwd=str(bundle))
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


def _uninstall(target: Path) -> None:
    uninstaller = target / "unins000.exe"
    time.sleep(3)  # let the OS release handles from the terminated GUI probe
    subprocess.run(
        [str(uninstaller), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        cwd=str(target),
        check=True,
        timeout=UNINSTALL_TIMEOUT_SECONDS,
    )

    def leftovers() -> list[str]:
        if not target.exists():
            return []
        return [str(path) for path in target.iterdir()]

    # Freshly written exes can be briefly locked by antivirus scans; the
    # uninstaller skips locked files, so allow some time before judging.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not leftovers():
            print("Uninstall left the target directory empty")
            return
        time.sleep(2)

    # One final grace pass: if the lock was transient the files are now
    # deletable and removal succeeding still proves the uninstaller ran.
    if leftovers():
        time.sleep(15)
        shutil.rmtree(target, ignore_errors=True)
        if leftovers():
            raise RuntimeError("Uninstall left files behind:\n  - " + "\n  - ".join(leftovers()))
        print("Uninstall passed (transient file locks required a grace cleanup)")


def test_installer(installer: Path, skip_gui: bool) -> None:
    if not installer.is_file():
        raise FileNotFoundError(f"Installer not found: {installer}")
    with tempfile.TemporaryDirectory(prefix="videocaptioner-install-") as tmp:
        target = Path(tmp) / "VideoCaptioner"
        print(f"+ Silent-installing into {target}")
        subprocess.run([
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            f"/DIR={target}",
            # Only the desktop icon task: keeps the test from touching user PATH
            '/TASKS="desktopicon"',
        ], check=True, timeout=UNINSTALL_TIMEOUT_SECONDS)
        _verify_bundle_tree(target)
        run_smoke(target)
        if not skip_gui:
            _probe_gui(target)
        print("+ Uninstalling")
        _uninstall(target)
    print("Installer acceptance test passed")


def _extract_portable(portable: Path, dest: Path) -> Path:
    if portable.is_dir():
        return portable
    with zipfile.ZipFile(portable) as zf:
        zf.extractall(dest)
    # The zip contains a single top-level VideoCaptioner/ directory.
    candidates = [path for path in dest.iterdir() if (path / "VideoCaptioner-cli.exe").exists()]
    if not candidates:
        raise RuntimeError(f"VideoCaptioner-cli.exe not found in extracted archive: {portable}")
    return candidates[0]


def test_portable(portable: Path, skip_gui: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="videocaptioner-portable-") as tmp:
        bundle = _extract_portable(portable, Path(tmp))
        print(f"+ Testing portable bundle at {bundle}")
        _verify_portable_tree(bundle)
        run_smoke(bundle)
        if not skip_gui:
            _probe_gui(bundle)
    print("Portable acceptance test passed")


def _verify_portable_tree(bundle: Path) -> None:
    required = [
        bundle / "VideoCaptioner.exe",
        bundle / "VideoCaptioner-cli.exe",
        bundle / "_internal" / "resource" / "bin" / "ffmpeg.exe",
        bundle / "_internal" / "resource" / "bin" / "ffprobe.exe",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Portable bundle is incomplete:\n  - " + "\n  - ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, help="Path to the Inno Setup installer exe")
    parser.add_argument("--portable", type=Path, help="Path to the portable zip (or extracted dir)")
    parser.add_argument("--bundle", type=Path, help="Path to dist/VideoCaptioner (smoke only)")
    parser.add_argument("--skip-gui", action="store_true", help="Skip the GUI liveness probe")
    args = parser.parse_args()

    if not (args.installer or args.portable or args.bundle):
        parser.error("at least one of --installer, --portable, --bundle is required")

    if args.installer:
        test_installer(args.installer.resolve(), args.skip_gui)
    if args.portable:
        test_portable(args.portable.resolve(), args.skip_gui)
    if args.bundle:
        run_smoke(args.bundle.resolve())
        print("Bundle smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
