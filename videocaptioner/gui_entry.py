"""Entry point for the windowed GUI executable (VideoCaptioner.exe).

Kept separate from ``videocaptioner/__main__.py`` (the CLI entry) so the
PyInstaller build can produce a windowed GUI executable and a console CLI
executable that share one ``_internal`` runtime directory.
"""

from videocaptioner.ui.main import main

if __name__ == "__main__":
    main()
