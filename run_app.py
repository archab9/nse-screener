"""Launch the desktop app.

    python run_app.py

This is the intended entry point and the one PyInstaller will be pointed at. The package
lives under src/, so this puts src on the path before importing it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nse_screener.gui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
