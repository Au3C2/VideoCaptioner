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
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT / "VideoCaptioner.spec"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
ARTIFACT_DIR = ROOT / "artifacts"
RUNTIME_DIR = BUILD_DIR / "desktop-runtime"
DMG_ASSETS_DIR = ROOT / "packaging" / "dmg-assets"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(ROOT), check=True, **kwargs)


def _version() -> str:
    # Explicit override (mirrors auto_culling's CULL_VERSION): useful for
    # local builds where a dirty working tree would make hatch-vcs emit a
    # dev version instead of the tagged release version.
    env_ver = os.environ.get("VIDEOCAPTIONER_BUILD_VERSION", "").strip().lstrip("v")
    if env_ver:
        return env_ver
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

    data_root = bundle / "lib"
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


def build_app_icns() -> Path:
    """Render the app logo into a full .icns (app bundle + DMG volume icon).

    PyInstaller's BUNDLE falls back to its default Python icon unless a .icns
    is supplied, and iconutil requires a complete .iconset of PNG sizes.
    The artwork is inset to ~80% of the canvas (Apple's macOS icon guideline,
    matching e.g. auto_culling's ~82%) so the icon does not render larger
    than its neighbours in the Dock/Finder. Sizes larger than the source
    logo are skipped instead of upscaled.
    """
    from PIL import Image, ImageChops, ImageDraw

    logo = ROOT / "resource" / "assets" / "logo-big.png"
    if not logo.exists():
        logo = ROOT / "resource" / "assets" / "logo.png"
    if not logo.exists():
        raise RuntimeError("No logo asset found to build the .icns icon")

    source = Image.open(logo).convert("RGBA")
    source_size = source.size[0]
    # Inset artwork onto a transparent canvas: artwork 80% of the tile
    # (Apple's macOS icon grid), corners re-rounded to the Apple-specified
    # radius (22.5% of the artwork width — the logo ships with ~14%, which
    # reads as too square next to native macOS icons).
    artwork_size = int(source_size * 0.8)
    inset = (source_size - artwork_size) // 2
    artwork = Image.new("RGBA", (source_size, source_size), (0, 0, 0, 0))
    scaled = source.resize((artwork_size, artwork_size), Image.LANCZOS)
    artwork.paste(scaled, (inset, inset))
    mask = Image.new("L", (source_size, source_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, source_size - inset, source_size - inset],
        radius=int(artwork_size * 0.225),
        fill=255,
    )
    artwork.putalpha(ImageChops.multiply(artwork.getchannel("A"), mask))

    iconset = BUILD_DIR / "appicon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for size in [16, 32, 128, 256, 512]:
        for scale, suffix in [(1, ""), (2, "@2x")]:
            pixel = size * scale
            if pixel > source_size:
                continue
            artwork.resize((pixel, pixel), Image.LANCZOS).save(
                iconset / f"icon_{size}x{size}{suffix}.png"
            )

    icns = BUILD_DIR / "appicon.icns"
    _run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
    shutil.rmtree(iconset, ignore_errors=True)
    print(f"Generated {icns.relative_to(ROOT)}")
    return icns


def _stage_dmg_background(stage: Path) -> None:
    """Create the DMG drag-install guide background image.

    A committed asset (packaging/dmg-assets/dmg-background.png) wins so the
    layout stays byte-identical between builds; otherwise a deterministic
    placeholder is rendered from the app logo with PIL. build_app_icns has
    already verified the logo and PIL exist by the time this runs. The
    canvas matches the 660x400 DMG window; icon slots sit at (150, 130)
    and (510, 130).
    """
    background_dir = stage / ".background"
    background_dir.mkdir(parents=True, exist_ok=True)
    target = background_dir / "dmg-background.png"

    committed = DMG_ASSETS_DIR / "dmg-background.png"
    if committed.exists():
        shutil.copy2(committed, target)
        print("Using committed DMG background image")
        return

    from PIL import Image, ImageDraw, ImageFont

    width, height = 660, 400
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Horizontal guide arrow between the .app and Applications icon slots
    arrow_y = 130
    draw.line([(270, arrow_y), (396, arrow_y)], fill=(120, 120, 120, 255), width=8)
    draw.polygon([(390, arrow_y - 22), (390, arrow_y + 22), (418, arrow_y)],
                 fill=(120, 120, 120, 255))

    font_path = ROOT / "resource" / "fonts" / "NotoSansSC-Regular.ttf"
    font = ImageFont.truetype(str(font_path), 22) if font_path.exists() else ImageFont.load_default()
    text = "将 VideoCaptioner 拖入 Applications 文件夹完成安装"
    text_box = draw.textbbox((0, 0), text, font=font)
    # Vertically centered between the icon bottoms (~y 215 incl. labels)
    # and the window bottom edge.
    text_y = 300 - (text_box[3] - text_box[1]) // 2
    draw.text(((width - text_box[2] + text_box[0]) // 2, text_y),
              text, font=font, fill=(90, 90, 90, 255))

    canvas.save(target)
    print("Rendered deterministic DMG background image")


def _write_ds_store(volume_root: Path) -> None:
    """Write the Finder layout into the mounted volume's .DS_Store.

    Window bounds, background image and icon positions — the same entries
    dmgbuild produces. The background alias is resolved against the mounted
    volume so Finder finds it after recompression and re-mounting.
    """
    try:
        import ds_store
        from mac_alias import Alias
    except ImportError as exc:
        raise RuntimeError(
            "ds-store and mac-alias are required for DMG builds. "
            "Run with: uv run --with ds-store --with mac-alias python scripts/build_desktop.py"
        ) from exc

    alias = Alias.for_file(str(volume_root / ".background" / "dmg-background.png"))
    # APFS file IDs exceed the 32-bit CNID fields of the alias format;
    # Finder resolves the background through the recorded POSIX path, so the
    # CNID hints can be safely zeroed.
    alias.target.cnid_path = None
    alias.target.folder_cnid = 0
    alias.target.cnid = 0

    bwsp = {
        "ShowStatusBar": False,
        "WindowBounds": "{{0, 0}, {660, 400}}",
        "ContainerShowSidebar": False,
        "PreviewPaneVisibility": False,
        "SidebarWidth": 0,
        "ShowTabView": False,
        "ShowToolbar": False,
        "ShowPathbar": False,
        "ShowSidebar": False,
    }
    icvp = {
        "viewOptionsVersion": 1,
        "backgroundType": 2,
        "backgroundColorRed": 1.0,
        "backgroundColorGreen": 1.0,
        "backgroundColorBlue": 1.0,
        "gridOffsetX": 0.0,
        "gridOffsetY": 0.0,
        "gridSpacing": 100.0,
        "arrangeBy": "none",
        "showIconPreview": False,
        "showItemInfo": False,
        "labelOnBottom": True,
        "textSize": 13.0,
        "iconSize": 128.0,
        "scrollPositionX": 0.0,
        "scrollPositionY": 0.0,
        "backgroundImageAlias": alias.to_bytes(),
    }
    icon_locations = {
        "VideoCaptioner.app": (150, 130),
        "Applications": (510, 130),
    }

    with ds_store.DSStore.open(str(volume_root / ".DS_Store"), "w+") as store:
        store["."]["vSrn"] = ("long", 1)
        store["."]["bwsp"] = bwsp
        store["."]["icvp"] = icvp
        store["."]["icvl"] = (b"type", "icnv")
        for name, location in icon_locations.items():
            store[name]["Iloc"] = location
    print("Wrote Finder layout (.DS_Store): window bounds, background, icon positions")


def build_dmg(version: str) -> Path:
    """Assemble the macOS DMG from the PyInstaller .app bundle.

    Stage the volume contents (.app, Applications drag link, background,
    volume icon), re-sign the bundle ad-hoc, create a read-write image,
    inject the .DS_Store Finder layout on the mounted volume (needs a real
    mount so the background alias resolves), then compress to UDZO. No
    Finder/AppleScript involvement, so the flow is safe in CI.
    """
    app = DIST_DIR / "VideoCaptioner.app"
    if not app.exists():
        raise RuntimeError(
            f"VideoCaptioner.app not found at {app} — the PyInstaller BUNDLE step requires macOS"
        )
    # hdiutil convert needs the target directory to exist; nothing else
    # creates it when the zip-archive step is skipped.
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    stage = BUILD_DIR / "dmg-stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    staged_app = stage / "VideoCaptioner.app"
    shutil.copytree(app, staged_app, symlinks=True)
    (stage / "Applications").symlink_to("/Applications")

    _stage_dmg_background(stage)
    icns = os.environ.get("VIDEOCAPTIONER_APP_ICON")
    if icns and Path(icns).exists():
        shutil.copy2(icns, stage / ".VolumeIcon.icns")
        print("Injected DMG volume icon")

    # Ad-hoc re-sign seals the staged bundle; without it Gatekeeper may flag
    # the copied app as damaged when the build host's signature is stale.
    _run(["codesign", "--force", "--deep", "-s", "-", str(staged_app)])

    rw_image = BUILD_DIR / "VideoCaptioner-rw.dmg"
    if rw_image.exists():
        rw_image.unlink()
    _run([
        "hdiutil", "create",
        "-volname", "VideoCaptioner",
        "-srcfolder", str(stage),
        "-fs", "HFS+",
        "-ov", "-format", "UDRW",
        str(rw_image),
    ])
    shutil.rmtree(stage, ignore_errors=True)

    mount_point = BUILD_DIR / "dmg-mount"
    shutil.rmtree(mount_point, ignore_errors=True)
    mount_point.mkdir(parents=True)
    try:
        _run([
            "hdiutil", "attach", str(rw_image),
            "-mountpoint", str(mount_point),
            "-nobrowse", "-quiet",
        ])
        _write_ds_store(mount_point)
    finally:
        detach = subprocess.run(
            ["hdiutil", "detach", str(mount_point), "-force", "-quiet"],
            capture_output=True,
        )
        if detach.returncode != 0:
            # Detach can transiently fail with 'Resource busy'; retry once
            # before leaving a stale mounted volume behind.
            time.sleep(2)
            subprocess.run(
                ["hdiutil", "detach", str(mount_point), "-force", "-quiet"],
                capture_output=True,
            )
        shutil.rmtree(mount_point, ignore_errors=True)

    dmg = ARTIFACT_DIR / f"VideoCaptioner-{version}-{_platform_tag()}.dmg"
    if dmg.exists():
        dmg.unlink()
    _run(["hdiutil", "convert", str(rw_image), "-format", "UDZO", "-o", str(dmg)])
    rw_image.unlink(missing_ok=True)
    print(f"Created {dmg.relative_to(ROOT)} ({dmg.stat().st_size / 1024 / 1024:.1f} MB)")
    return dmg


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
    """Compile packaging/installer.iss into artifacts/VideoCaptioner-<version>-windows-x64-setup.exe."""
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
    installer = ARTIFACT_DIR / f"VideoCaptioner-{version}-windows-x64-setup.exe"
    if not installer.exists():
        raise RuntimeError(f"Installer was not produced: {installer}")
    print(f"Created {installer.relative_to(ROOT)}")
    return installer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="Remove build/dist/artifacts first")
    parser.add_argument("--no-archive", action="store_true", help="Build and verify without creating zip archives")
    parser.add_argument("--no-installer", action="store_true", help="Skip the Inno Setup installer (portable zip only)")
    parser.add_argument("--no-dmg", action="store_true", help="Skip the macOS DMG assembly")
    parser.add_argument(
        "--dmg-only",
        action="store_true",
        help="Only re-assemble the DMG from the existing dist/VideoCaptioner.app (macOS)",
    )
    args = parser.parse_args()

    version = _version()
    # The spec's BUNDLE reads this for Info.plist CFBundleShortVersionString.
    os.environ["VIDEOCAPTIONER_APP_VERSION"] = version
    if args.clean:
        clean()
    if args.dmg_only:
        if platform.system() != "Darwin":
            print("--dmg-only requires macOS")
            return 1
        icns = build_app_icns()
        os.environ["VIDEOCAPTIONER_APP_ICON"] = str(icns)
        build_dmg(version)
        return 0
    ensure_version_file(version)
    prepare_ffmpeg()
    if platform.system() == "Darwin":
        # Set before the PyInstaller run: the spec's BUNDLE reads this env
        # var, otherwise the .app gets PyInstaller's default Python icon.
        icns = build_app_icns()
        os.environ["VIDEOCAPTIONER_APP_ICON"] = str(icns)
    build_pyinstaller()
    verify_bundle()
    # Windows ships the portable zip alongside the installer; macOS ships
    # the DMG only.
    if platform.system() == "Windows" and not args.no_archive:
        archive(version)
    if platform.system() == "Windows" and not args.no_installer:
        build_installer(version)
    if platform.system() == "Darwin" and not args.no_dmg:
        build_dmg(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
