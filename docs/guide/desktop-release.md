# Desktop release build

VideoCaptioner publishes Windows desktop artifacts from GitHub Actions:

- `VideoCaptioner-<version>-windows-setup.exe` — Inno Setup installer
  (per-user install, no admin required, optional CLI PATH integration)
- `VideoCaptioner-<version>-windows-x64-portable.zip` — portable bundle,
  extract and run

Both artifacts are built from the same PyInstaller bundle, which contains a
windowed GUI executable (`VideoCaptioner.exe`) and a console CLI executable
(`VideoCaptioner-cli.exe`) sharing one `_internal` runtime directory. Users
can run them without installing Python or FFmpeg (static ffmpeg/ffprobe are
bundled).

## Local build

```bash
uv sync --frozen
uv run --with pyinstaller --with static-ffmpeg python scripts/build_desktop.py --clean
uv run python scripts/smoke_desktop.py dist/VideoCaptioner
```

Requires the Inno Setup compiler for the installer step
(`winget install JRSoftware.InnoSetup`, or set `VIDEOCAPTIONER_ISCC` to the
`ISCC.exe` path); use `--no-installer` to build the portable zip only.

The build script downloads static `ffmpeg` and `ffprobe` for the current
platform and bundles them under `resource/bin` inside the PyInstaller app.
Runtime user data is kept in the system user-data directory, so app upgrades
do not overwrite settings, logs, cache, models, or custom subtitle styles.

## Artifact acceptance tests

`scripts/test_installer.py` verifies the packaged artifacts the same way
locally and in CI:

```bash
# installer: silent install into a temp dir, tree verification, CLI smoke
# (version / doctor / real subtitle burn with bundled ffmpeg), GUI liveness
# probe, silent uninstall
uv run python scripts/test_installer.py --installer artifacts/VideoCaptioner-*-windows-setup.exe

# portable: extract zip and run the same verification and smoke flow
uv run python scripts/test_installer.py --portable artifacts/VideoCaptioner-*-windows-x64-portable.zip

# --skip-gui omits the GUI liveness probe (used in CI)
```

## CI and releases

`.github/workflows/build-desktop.yml` builds on `windows-latest`:

- PyInstaller bundle → portable zip → Inno Setup installer
- packaged CLI smoke test (`smoke_desktop.py`)
- installer acceptance test (`test_installer.py`, GUI probe skipped)

On `v*` tags, the installer and portable zip are uploaded to a **draft**
GitHub Release for manual review; publish the draft after checking it.
Note: the version comes from the tag via hatch-vcs — building without any
tag yields `0.0.0.devNNN` artifact names.

The PyPI workflow (`.github/workflows/publish-pypi.yml`) builds the wheel and
sdist as workflow artifacts but does **not** publish to PyPI: this fork cannot
pass PyPI Trusted Publishing (the OIDC publisher is bound to the upstream
`WEIFENG2333/VideoCaptioner` repository).

## Installer notes

- The installer writes only to `HKEY_CURRENT_USER` (per-user install to
  `%LOCALAPPDATA%\Programs\VideoCaptioner`).
- The "Add VideoCaptioner CLI to PATH" task is enabled by default; silent
  deployments can deselect it with `/MERGETASKS="!addtopath"` or pass
  `/TASKS=` explicitly. Uninstall removes the PATH entry.
