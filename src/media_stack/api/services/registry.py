"""Service registry — single source of truth for all managed services.

Add, remove, or modify services here. Controller, health probes, auth
probes, key discovery, key rotation, and password reset all read from
this registry instead of hardcoding service details.

To add a new service: add an entry to SERVICES.
To remove a service: delete its entry.
No other files need to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServiceDef:
    """Definition of a managed service."""

    id: str                          # e.g. "sonarr"
    name: str                        # Display name: "Sonarr"
    desc: str                        # Short description
    category: str                    # "automation", "media", "downloads", "management"
    host: str                        # Container/pod hostname
    port: int                        # Primary port
    health_path: str = "/"           # HTTP health probe path
    auth_path: str = ""              # Authenticated API probe path (empty = no auth probe)
    auth_mode: str = "X-Api-Key"     # Header name or "query:param"
    api_key_env: str = ""            # Env var name for API key
    api_key_config: str = ""         # Config file relative path for key discovery
    api_key_format: str = ""         # "xml", "ini", "yaml", "json", "sqlite"
    version_path: str = ""           # API path to get version
    version_json_key: str = ""       # JSON key in version response
    password_api_path: str = ""      # API path for password change (PUT)
    password_api_version: str = ""   # "v1" or "v3" for arr apps
    password_config: str = ""        # Config file for file-based password (yaml/ini)
    profiles: list[str] = field(default_factory=list)  # Compose profiles (empty = always on)


# ---------------------------------------------------------------------------
# Service definitions — THE source of truth
# ---------------------------------------------------------------------------

SERVICES: list[ServiceDef] = [
    # --- Media servers ---
    ServiceDef(
        id="jellyfin", name="Jellyfin", desc="Media server", category="media",
        host="jellyfin", port=8096,
        health_path="/System/Info/Public",
        auth_path="/System/Info", auth_mode="X-Emby-Token",
        api_key_env="JELLYFIN_API_KEY", api_key_format="sqlite",
        api_key_config="jellyfin/data/jellyfin.db",
        version_path="/System/Info/Public", version_json_key="Version",
    ),
    ServiceDef(
        id="plex", name="Plex", desc="Alt media server", category="media",
        host="plex", port=32400,
        health_path="/identity",
        profiles=["plex"],
    ),

    # --- Request management ---
    ServiceDef(
        id="jellyseerr", name="Jellyseerr", desc="Request manager", category="media",
        host="jellyseerr", port=5055,
        health_path="/api/v1/status",
        auth_path="/api/v1/settings/main", auth_mode="X-Api-Key",
        api_key_env="JELLYSEERR_API_KEY", api_key_format="json",
        api_key_config="jellyseerr/settings.json",
    ),

    # --- Arr automation ---
    ServiceDef(
        id="sonarr", name="Sonarr", desc="TV automation", category="automation",
        host="sonarr", port=8989,
        health_path="/ping",
        auth_path="/api/v3/system/status", auth_mode="X-Api-Key",
        api_key_env="SONARR_API_KEY", api_key_format="xml",
        api_key_config="sonarr/config.xml",
        version_path="/api/v3/system/status", version_json_key="version",
        password_api_path="/api/v3/config/host", password_api_version="v3",
    ),
    ServiceDef(
        id="radarr", name="Radarr", desc="Movie automation", category="automation",
        host="radarr", port=7878,
        health_path="/ping",
        auth_path="/api/v3/system/status", auth_mode="X-Api-Key",
        api_key_env="RADARR_API_KEY", api_key_format="xml",
        api_key_config="radarr/config.xml",
        version_path="/api/v3/system/status", version_json_key="version",
        password_api_path="/api/v3/config/host", password_api_version="v3",
    ),
    ServiceDef(
        id="lidarr", name="Lidarr", desc="Music automation", category="automation",
        host="lidarr", port=8686,
        health_path="/ping",
        auth_path="/api/v1/system/status", auth_mode="X-Api-Key",
        api_key_env="LIDARR_API_KEY", api_key_format="xml",
        api_key_config="lidarr/config.xml",
        version_path="/api/v1/system/status", version_json_key="version",
        password_api_path="/api/v1/config/host", password_api_version="v1",
    ),
    ServiceDef(
        id="readarr", name="Readarr", desc="Books automation", category="automation",
        host="readarr", port=8787,
        health_path="/ping",
        auth_path="/api/v1/system/status", auth_mode="X-Api-Key",
        api_key_env="READARR_API_KEY", api_key_format="xml",
        api_key_config="readarr/config.xml",
        version_path="/api/v1/system/status", version_json_key="version",
        password_api_path="/api/v1/config/host", password_api_version="v1",
    ),
    ServiceDef(
        id="prowlarr", name="Prowlarr", desc="Indexer manager", category="automation",
        host="prowlarr", port=9696,
        health_path="/ping",
        auth_path="/api/v1/system/status", auth_mode="X-Api-Key",
        api_key_env="PROWLARR_API_KEY", api_key_format="xml",
        api_key_config="prowlarr/config.xml",
        version_path="/api/v1/system/status", version_json_key="version",
        password_api_path="/api/v1/config/host", password_api_version="v1",
    ),
    ServiceDef(
        id="bazarr", name="Bazarr", desc="Subtitles", category="automation",
        host="bazarr", port=6767,
        health_path="/",
        auth_path="/api/system/status", auth_mode="X-Api-Key",
        api_key_env="BAZARR_API_KEY", api_key_format="yaml",
        api_key_config="bazarr/config/config.yaml",
        version_path="/api/system/status", version_json_key="data.bazarr_version",
        password_config="bazarr/config/config.yaml",
    ),

    # --- Download clients ---
    ServiceDef(
        id="qbittorrent", name="qBittorrent", desc="Torrent client", category="downloads",
        host="qbittorrent", port=8080,
        health_path="/",
    ),
    ServiceDef(
        id="sabnzbd", name="SABnzbd", desc="Usenet client", category="downloads",
        host="sabnzbd", port=8080,
        health_path="/",
        auth_path="/api", auth_mode="query:apikey",
        api_key_env="SABNZBD_API_KEY", api_key_format="ini",
        api_key_config="sabnzbd/sabnzbd.ini",
        password_config="sabnzbd/sabnzbd.ini",
    ),

    # --- Support services ---
    ServiceDef(
        id="flaresolverr", name="FlareSolverr", desc="Indexer proxy", category="management",
        host="flaresolverr", port=8191,
        health_path="/",
    ),
    ServiceDef(
        id="maintainerr", name="Maintainerr", desc="Library cleanup", category="management",
        host="maintainerr", port=6246,
        health_path="/app/maintainerr/api/settings",
    ),
    ServiceDef(
        id="tautulli", name="Tautulli", desc="Analytics", category="management",
        host="tautulli", port=8181,
        health_path="/",
        auth_path="/api/v2", auth_mode="query:apikey",
        api_key_env="TAUTULLI_API_KEY", api_key_format="ini",
        api_key_config="tautulli/config.ini",
        password_config="tautulli/config.ini",
    ),
    ServiceDef(
        id="homepage", name="Homepage", desc="Dashboard", category="management",
        host="homepage", port=3000,
        health_path="/",
    ),
    ServiceDef(
        id="envoy", name="Envoy", desc="Gateway proxy", category="infrastructure",
        host="envoy", port=9901,
        health_path="/ready",
    ),

    # --- Infrastructure (behind profiles or optional) ---
    ServiceDef(
        id="traefik", name="Traefik", desc="Reverse proxy (alt)", category="infrastructure",
        host="traefik", port=8080,
        health_path="/ping",
        profiles=["traefik"],
    ),
    ServiceDef(
        id="authelia", name="Authelia", desc="SSO auth provider", category="infrastructure",
        host="authelia", port=9091,
        health_path="/api/health",
        profiles=["auth-authelia"],
    ),
    ServiceDef(
        id="authentik", name="Authentik", desc="Identity provider", category="infrastructure",
        host="authentik", port=9000,
        health_path="/-/health/live/",
        profiles=["auth-authentik"],
    ),
    ServiceDef(
        id="unpackerr", name="Unpackerr", desc="Archive extractor", category="downloads",
        host="unpackerr", port=0,  # No HTTP port — background worker
        health_path="",  # No health endpoint — process check only
    ),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

SERVICE_MAP: dict[str, ServiceDef] = {s.id: s for s in SERVICES}

CATEGORIES: list[dict[str, Any]] = []
_cat_order = ["media", "automation", "downloads", "management", "infrastructure"]
for _cat in _cat_order:
    _ids = [s.id for s in SERVICES if s.category == _cat]
    if _ids:
        CATEGORIES.append({"label": _cat.capitalize(), "ids": _ids})


def get_service(service_id: str) -> ServiceDef | None:
    """Look up a service by ID."""
    return SERVICE_MAP.get(service_id)


def get_services_with_api_keys() -> list[ServiceDef]:
    """Services that have API keys (for rotation/discovery)."""
    return [s for s in SERVICES if s.api_key_env]


def get_services_with_password_api() -> list[ServiceDef]:
    """Services that support password changes via API."""
    return [s for s in SERVICES if s.password_api_path]


def get_services_with_password_config() -> list[ServiceDef]:
    """Services that support password changes via config file."""
    return [s for s in SERVICES if s.password_config]


def get_active_service_ids() -> set[str]:
    """Services that are always active (no profile gate)."""
    return {s.id for s in SERVICES if not s.profiles}
