"""Discover API keys from app config files on the shared config mount.

Uses the service registry and shared key readers — no hardcoded app
names or paths. To add a new service, update registry.py only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from media_stack.api.services.admin import _KEY_READERS
from media_stack.api.services.registry import SERVICES


def _read_bazarr_api_key(config_path: Path) -> str:
    """Extract API key from Bazarr's YAML config (auth.apikey).

    Kept as a named export for backward compatibility with code that
    imports this function directly.
    """
    return _KEY_READERS.get("yaml", lambda p: "")(config_path)


def run_preflight(
    *,
    config_root: str = "/srv-config",
    log: Any = None,
    **kwargs: Any,
) -> dict[str, str]:
    """Discover API keys from app config files.

    Iterates the service registry. For each service with an api_key_env
    and api_key_config, reads the key using the format-appropriate reader.
    """

    def info(msg: str) -> None:
        if log:
            log(msg)

    root = Path(config_root)
    discovered: dict[str, str] = {}

    for svc in SERVICES:
        if not svc.api_key_env or not svc.api_key_config or not svc.api_key_format:
            continue

        config_path = root / svc.api_key_config
        reader = _KEY_READERS.get(svc.api_key_format)
        if not reader:
            continue

        value = reader(config_path)
        if value:
            discovered[svc.api_key_env] = value
            info(f"API key discovered: {svc.api_key_env} from {svc.api_key_config}")

    # Jellyfin user ID — special case (not an API key, but needed for password reset)
    jf_db = root / "jellyfin" / "data" / "jellyfin.db"
    if jf_db.exists() and "JELLYFIN_API_KEY" in discovered:
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT Id FROM Users WHERE IsAdministrator=1 ORDER BY Id LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                discovered["JELLYFIN_USER_ID"] = str(row[0]).strip()
                info(f"API key discovered: JELLYFIN_USER_ID from jellyfin/data/jellyfin.db")
        except Exception:
            pass

    info(f"API key discovery: {len(discovered)} keys found")
    return discovered
