"""Launch the desktop app.

    python run_app.py

This is the entry point the Desktop shortcut targets, and the one PyInstaller will use.
The package lives under src/, so this puts src on the path before importing it.

Startup is logged to %LOCALAPPDATA%\\nse-screener\\launch.log. When the app is started by
double-clicking a shortcut there is no console attached, so an exception during startup
would otherwise leave the user with a process that silently does nothing and no way to
find out why.
"""

from __future__ import annotations

import datetime
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def _log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(base) / "nse-screener"
    path.mkdir(parents=True, exist_ok=True)
    return path / "launch.log"


def _log(message: str) -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except OSError:
        pass  # logging must never be the thing that stops the app starting


def main() -> int:
    _log(f"starting - python={sys.executable}")
    try:
        from nse_screener.gui.app import main as run_gui
    except Exception:
        _log("import failed:\n" + traceback.format_exc())
        raise

    _log("imports ok, creating window")
    try:
        code = run_gui()
    except Exception:
        _log("crashed:\n" + traceback.format_exc())
        raise
    _log(f"exited with {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
