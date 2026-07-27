"""Configuration loading. All thresholds live in config/*.json, never inline in logic."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# repo root = .../src/nse_screener/config.py -> up three
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    return _load_json(CONFIG_DIR / "settings.json")


@lru_cache(maxsize=1)
def sector_map() -> dict[str, Any]:
    return _load_json(CONFIG_DIR / "sector_map.json")


@lru_cache(maxsize=1)
def holiday_calendar(year: int = 2026) -> dict[str, Any]:
    path = CONFIG_DIR / f"nse_holidays_{year}.json"
    if not path.exists():
        return {"year": year, "verified": False, "holidays": [], "expected_count": None}
    return _load_json(path)


def resolve_path(key: str) -> Path:
    """Resolve a configured path relative to the repo root."""
    raw = settings()["paths"][key]
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else ROOT / candidate


def kite_api_key() -> str | None:
    """Read the Kite API key from the environment, falling back to Windows Credential Manager.

    Deliberately never reads from a file inside the repo - spec section 9 keeps this
    credential on the machine and out of anything that could be committed or pasted.
    """
    cfg = settings()["kite"]
    value = os.environ.get(cfg["api_key_env"])
    if value:
        return value
    return _keyring_get(cfg["keyring_service"], "api_key")


def kite_api_secret() -> str | None:
    cfg = settings()["kite"]
    value = os.environ.get("KITE_API_SECRET")
    if value:
        return value
    return _keyring_get(cfg["keyring_service"], "api_secret")


def kite_access_token() -> str | None:
    cfg = settings()["kite"]
    value = os.environ.get("KITE_ACCESS_TOKEN")
    if value:
        return value
    return _keyring_get(cfg["keyring_service"], "access_token")


def store_kite_access_token(token: str) -> bool:
    cfg = settings()["kite"]
    return _keyring_set(cfg["keyring_service"], "access_token", token)


def _keyring_get(service: str, key: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(service, key)
    except Exception:
        # A broken/locked credential backend should degrade to "no token" and let the
        # caller raise the login banner, not crash the run.
        return None


def _keyring_set(service: str, key: str, value: str) -> bool:
    try:
        import keyring
    except ImportError:
        return False
    try:
        keyring.set_password(service, key, value)
        return True
    except Exception:
        return False
