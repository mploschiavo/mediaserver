"""Service registry — loaded from contracts/services.yaml.

To add, remove, or modify services, edit the YAML file.
No Python code changes needed. Third-party developers can extend
the registry by editing the config file.

The registry is loaded once at import time. The controller, health
probes, key discovery, rotation, and password reset all read from
this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ServiceDef:
    """Definition of a managed service."""

    id: str
    name: str
    desc: str = ""
    category: str = "management"
    host: str = ""
    port: int = 0
    health_path: str = "/"
    auth_path: str = ""
    auth_mode: str = "X-Api-Key"
    api_key_env: str = ""
    api_key_config: str = ""
    api_key_format: str = ""
    version_path: str = ""
    version_json_key: str = ""
    password_api_path: str = ""
    password_config: str = ""
    profiles: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Load from YAML
# ---------------------------------------------------------------------------

def _find_services_yaml() -> Path:
    """Locate the services.yaml config file."""
    candidates = [
        Path(os.environ.get("SERVICES_REGISTRY_FILE", "")),
        Path("/opt/media-stack/contracts/services.yaml"),
        Path(__file__).resolve().parents[4] / "contracts" / "services.yaml",
        Path("contracts/services.yaml"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "services.yaml not found. Set SERVICES_REGISTRY_FILE env var "
        "or place it at contracts/services.yaml"
    )


def _load_registry() -> tuple[list[ServiceDef], list[str]]:
    """Load services and categories from YAML."""
    path = _find_services_yaml()
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    categories = [str(c) for c in (data.get("categories") or []) if c]
    services: list[ServiceDef] = []

    for entry in data.get("services") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        # Convert profiles from YAML list/None to Python list
        profiles = entry.get("profiles") or []
        if not isinstance(profiles, list):
            profiles = [profiles]
        services.append(ServiceDef(
            id=str(entry["id"]),
            name=str(entry.get("name", entry["id"])),
            desc=str(entry.get("desc", "")),
            category=str(entry.get("category", "management")),
            host=str(entry.get("host", entry["id"])),
            port=int(entry.get("port", 0)),
            health_path=str(entry.get("health_path", "/")),
            auth_path=str(entry.get("auth_path", "")),
            auth_mode=str(entry.get("auth_mode", "X-Api-Key")),
            api_key_env=str(entry.get("api_key_env", "")),
            api_key_config=str(entry.get("api_key_config", "")),
            api_key_format=str(entry.get("api_key_format", "")),
            version_path=str(entry.get("version_path", "")),
            version_json_key=str(entry.get("version_json_key", "")),
            password_api_path=str(entry.get("password_api_path", "")),
            password_config=str(entry.get("password_config", "")),
            profiles=[str(p) for p in profiles],
        ))

    return services, categories


# ---------------------------------------------------------------------------
# Module-level state — loaded once at import
# ---------------------------------------------------------------------------

SERVICES, _CATEGORY_ORDER = _load_registry()
SERVICE_MAP: dict[str, ServiceDef] = {s.id: s for s in SERVICES}

CATEGORIES: list[dict[str, Any]] = []
for _cat in _CATEGORY_ORDER:
    _ids = [s.id for s in SERVICES if s.category == _cat]
    if _ids:
        CATEGORIES.append({"label": _cat.capitalize(), "ids": _ids})


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

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


def reload_registry() -> None:
    """Reload services from YAML. Call after editing services.yaml."""
    global SERVICES, SERVICE_MAP, CATEGORIES, _CATEGORY_ORDER
    SERVICES, _CATEGORY_ORDER = _load_registry()
    SERVICE_MAP = {s.id: s for s in SERVICES}
    CATEGORIES.clear()
    for cat in _CATEGORY_ORDER:
        ids = [s.id for s in SERVICES if s.category == cat]
        if ids:
            CATEGORIES.append({"label": cat.capitalize(), "ids": ids})
