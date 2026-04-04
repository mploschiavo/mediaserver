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


# Map of env var name → (relative config path, reader function).
_KEY_SOURCES: dict[str, tuple[str, Any]] = {
    "SONARR_API_KEY": ("sonarr/config.xml", _read_xml_api_key),
    "RADARR_API_KEY": ("radarr/config.xml", _read_xml_api_key),
    "LIDARR_API_KEY": ("lidarr/config.xml", _read_xml_api_key),
    "READARR_API_KEY": ("readarr/config.xml", _read_xml_api_key),
    "PROWLARR_API_KEY": ("prowlarr/config.xml", _read_xml_api_key),
    "BAZARR_API_KEY": ("bazarr/config/config.yaml", None),  # Bazarr uses YAML
    "SABNZBD_API_KEY": ("sabnzbd/sabnzbd.ini", _read_ini_api_key),
}


def _read_bazarr_api_key(config_path: Path) -> str:
    """Extract API key from Bazarr's YAML config."""
    if not config_path.exists():
        return ""
    text = config_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\s*apikey:\s*['\"]?(\S+?)['\"]?\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def run_preflight(
    *,
    config_root: str = "/srv-config",
    log: Any = None,
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
        if reader is None and env_var == "BAZARR_API_KEY":
            value = _read_bazarr_api_key(config_path)
        elif reader is not None:
            value = reader(config_path)
        else:
            continue

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

    info(f"API key discovery: {len(discovered)} keys found")
    return discovered
