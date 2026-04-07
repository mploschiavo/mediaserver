"""Discover API keys from app config files on the shared config mount.

Reads API keys from XML config files (Sonarr, Radarr, etc.) and INI files
(SABnzbd) so the bootstrap runner has the keys without them being passed
as env vars from the host.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _read_xml_api_key(config_path: Path) -> str:
    """Extract <ApiKey>value</ApiKey> from an XML config file."""
    if not config_path.exists():
        return ""
    text = config_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
    return match.group(1).strip() if match else ""


def _read_ini_api_key(config_path: Path) -> str:
    """Extract api_key = value from an INI file."""
    if not config_path.exists():
        return ""
    text = config_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\s*api_key\s*=\s*(\S+)", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _read_bazarr_api_key(config_path: Path) -> str:
    """Extract API key from Bazarr's YAML config (auth.apikey)."""
    if not config_path.exists():
        return ""
    text = config_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\s*apikey:\s*['\"]?(\S+?)['\"]?\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


# Map of env var name → (relative config path, reader function).
_KEY_SOURCES: dict[str, tuple[str, Any]] = {
    "SONARR_API_KEY": ("sonarr/config.xml", _read_xml_api_key),
    "RADARR_API_KEY": ("radarr/config.xml", _read_xml_api_key),
    "LIDARR_API_KEY": ("lidarr/config.xml", _read_xml_api_key),
    "READARR_API_KEY": ("readarr/config.xml", _read_xml_api_key),
    "PROWLARR_API_KEY": ("prowlarr/config.xml", _read_xml_api_key),
    "BAZARR_API_KEY": ("bazarr/config/config.yaml", _read_bazarr_api_key),
    "SABNZBD_API_KEY": ("sabnzbd/sabnzbd.ini", _read_ini_api_key),
}


def run_preflight(
    *,
    config_root: str = "/srv-config",
    log: Any = None,
    **kwargs: Any,
) -> dict[str, str]:
    """Discover API keys from app config files.

    Returns dict of env var name → API key value for all discovered keys.
    """

    def info(msg: str) -> None:
        if log:
            log(msg)

    root = Path(config_root)
    discovered: dict[str, str] = {}

    for env_var, (rel_path, reader) in _KEY_SOURCES.items():
        config_path = root / rel_path
        value = reader(config_path)

        if value:
            discovered[env_var] = value
            info(f"API key discovered: {env_var} from {rel_path}")

    # Jellyseerr: read from settings.json.
    jellyseerr_settings = root / "jellyseerr" / "settings.json"
    if jellyseerr_settings.exists():
        try:
            import json

            with open(jellyseerr_settings) as f:
                settings = json.load(f)
            api_key = str(settings.get("main", {}).get("apiKey", "")).strip()
            if api_key:
                discovered["JELLYSEERR_API_KEY"] = api_key
                info(f"API key discovered: JELLYSEERR_API_KEY from jellyseerr/settings.json")
        except Exception:
            pass

    # Tautulli: read from config.ini (same INI format as SABnzbd).
    tautulli_ini = root / "tautulli" / "config.ini"
    if tautulli_ini.exists():
        value = _read_ini_api_key(tautulli_ini)
        if value:
            discovered["TAUTULLI_API_KEY"] = value
            info(f"API key discovered: TAUTULLI_API_KEY from tautulli/config.ini")

    # Jellyfin: read from SQLite database.
    jf_db = root / "jellyfin" / "data" / "jellyfin.db"
    if jf_db.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute(
                "SELECT AccessToken FROM ApiKeys ORDER BY Id DESC LIMIT 1"
            )
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                discovered["JELLYFIN_API_KEY"] = str(row[0]).strip()
                info(f"API key discovered: JELLYFIN_API_KEY from jellyfin/data/jellyfin.db")
        except Exception:
            pass

    # Jellyfin user ID: read from SQLite database.
    if jf_db.exists() and "JELLYFIN_API_KEY" in discovered:
        try:
            conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute(
                "SELECT Id FROM Users WHERE IsAdministrator=1 ORDER BY Id LIMIT 1"
            )
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                discovered["JELLYFIN_USER_ID"] = str(row[0]).strip()
                info(f"API key discovered: JELLYFIN_USER_ID from jellyfin/data/jellyfin.db")
        except Exception:
            pass

    info(f"API key discovery: {len(discovered)} keys found")
    return discovered
