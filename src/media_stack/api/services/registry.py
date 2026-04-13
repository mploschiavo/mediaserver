"""Service registry — loaded from contracts/services.yaml.

To add, remove, or modify services, edit the YAML file.
No Python code changes needed. Third-party developers can extend
the registry by editing the config file.

The registry is loaded once at import time. The controller, health
probes, key discovery, rotation, and password reset all read from
this module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("controller_api")


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
    api_key_http_path: str = ""
    version_path: str = ""
    version_json_key: str = ""
    stats_path: str = ""
    stats_label: str = ""
    history_path: str = ""
    quality_profile_path: str = ""
    import_list_path: str = ""
    recent_path: str = ""
    indexer_path: str = ""
    indexer_stats_path: str = ""
    password_api_path: str = ""
    password_config: str = ""
    login_mode: str = ""  # "json_credentials", "basic", "form", or "" (none)
    login_path: str = ""  # endpoint to test username/password login
    profiles: list[str] = field(default_factory=list)
    web_ui: bool = True
    preserve_path_prefix: bool = False
    scalable: bool = True
    scale_to_zero: bool = False
    top_level_config_key: bool = False


# ---------------------------------------------------------------------------
# Load from YAML
# ---------------------------------------------------------------------------

def _find_services_dir() -> Path | None:
    """Locate the per-service YAML directory."""
    env_dir = os.environ.get("SERVICES_REGISTRY_DIR", "").strip()
    candidates = [
        Path(env_dir) if env_dir else None,
        Path("/opt/media-stack/contracts/services"),
        Path(__file__).resolve().parents[4] / "contracts" / "services",
        Path("contracts/services"),
    ]
    for p in candidates:
        if p and p.is_dir() and any(p.glob("*.yaml")):
            return p
    return None


def _find_services_yaml() -> Path | None:
    """Locate the legacy services.yaml config file (fallback)."""
    env_file = os.environ.get("SERVICES_REGISTRY_FILE", "").strip()
    candidates = [
        Path(env_file) if env_file else None,
        Path("/opt/media-stack/contracts/services.yaml"),
        Path(__file__).resolve().parents[4] / "contracts" / "services.yaml",
        Path("contracts/services.yaml"),
    ]
    for p in candidates:
        if p and p.is_file():
            return p
    return None


def _parse_service_entry(entry: dict[str, Any]) -> ServiceDef | None:
    """Parse a service dict into a ServiceDef."""
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    profiles = entry.get("profiles") or []
    if not isinstance(profiles, list):
        profiles = [profiles]
    return ServiceDef(
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
        api_key_http_path=str(entry.get("api_key_http_path", "")),
        version_path=str(entry.get("version_path", "")),
        version_json_key=str(entry.get("version_json_key", "")),
        stats_path=str(entry.get("stats_path", "")),
        stats_label=str(entry.get("stats_label", "")),
        history_path=str(entry.get("history_path", "")),
        quality_profile_path=str(entry.get("quality_profile_path", "")),
        import_list_path=str(entry.get("import_list_path", "")),
        recent_path=str(entry.get("recent_path", "")),
        indexer_path=str(entry.get("indexer_path", "")),
        indexer_stats_path=str(entry.get("indexer_stats_path", "")),
        password_api_path=str(entry.get("password_api_path", "")),
        password_config=str(entry.get("password_config", "")),
        login_mode=str(entry.get("login_mode", "")),
        login_path=str(entry.get("login_path", "")),
        profiles=[str(p) for p in profiles],
        web_ui=bool(entry.get("web_ui", True)),
        preserve_path_prefix=bool(entry.get("preserve_path_prefix", False)),
        scalable=bool(entry.get("scalable", True)),
        scale_to_zero=bool(entry.get("scale_to_zero", False)),
        top_level_config_key=bool(entry.get("top_level_config_key", False)),
    )


def _load_registry() -> tuple[list[ServiceDef], list[str]]:
    """Load services from per-service YAML files or legacy services.yaml."""
    services: list[ServiceDef] = []
    categories: list[str] = []

    # Strategy 1: Per-service YAML files (preferred — one file per service)
    svc_dir = _find_services_dir()
    if svc_dir:
        for yaml_file in sorted(svc_dir.glob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue  # Skip templates
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                entry = data.get("service", data)
                svc = _parse_service_entry(entry)
                if svc:
                    services.append(svc)
            except Exception as exc:
                logger.warning("Failed to load service YAML %s: %s", yaml_file.name, exc)

    # Strategy 2: Legacy services.yaml (fallback — all services in one file)
    if not services:
        legacy = _find_services_yaml()
        if legacy:
            with open(legacy, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            categories = [str(c) for c in (data.get("categories") or []) if c]
            for entry in data.get("services") or []:
                svc = _parse_service_entry(entry)
                if svc:
                    services.append(svc)

    # Derive categories from services if not explicitly set
    if not categories:
        seen: set[str] = set()
        for s in services:
            if s.category not in seen:
                categories.append(s.category)
                seen.add(s.category)

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


def get_scalable_services() -> list[ServiceDef]:
    """Services that participate in scale policy (replicas managed by deploy)."""
    return [s for s in SERVICES if s.scalable]


def get_scale_to_zero_services() -> list[ServiceDef]:
    """Services that start at 0 replicas and are enabled on demand."""
    return [s for s in SERVICES if s.scale_to_zero]


def get_web_ui_services() -> list[ServiceDef]:
    """Services that have a browser-accessible web interface."""
    return [s for s in SERVICES if s.web_ui]


def get_preserve_path_prefix_services() -> list[ServiceDef]:
    """Services whose APIs require the path prefix to be preserved (not stripped)."""
    return [s for s in SERVICES if s.preserve_path_prefix]


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


# ---------------------------------------------------------------------------
# Registry-driven API key readers — format-agnostic.
#
# Key formats are declared in each service's YAML contract (api_key_format).
# To support a new format, add a reader to key_formats.py and the format
# name in your service YAML — no other code changes needed.
# ---------------------------------------------------------------------------

import re as _re
from pathlib import Path as _Path

from .key_formats import READERS as KEY_READERS


def read_api_key_from_file(service_id: str, config_root: str) -> str:
    """Read an API key from a service's config file using its declared format.

    Returns the key string, or empty string if not found or unsupported.
    This is the single entry point for all file-based key discovery — driven
    entirely by the service's contract YAML fields (api_key_config, api_key_format).
    """
    svc = SERVICE_MAP.get(service_id)
    if not svc or not svc.api_key_config or not svc.api_key_format:
        return ""
    reader = KEY_READERS.get(svc.api_key_format)
    if not reader:
        return ""
    cfg_path = _Path(config_root) / svc.api_key_config
    if not cfg_path.is_file():
        return ""
    try:
        return reader(cfg_path)
    except Exception:
        return ""


def read_api_key_via_http(service_id: str) -> str:
    """Try to fetch an API key from a running service over HTTP.

    Uses the api_key_http_path declared in the contract, or falls back to
    /initialize.js (common for Arr apps). Returns empty string on failure.
    """
    import urllib.request
    svc = SERVICE_MAP.get(service_id)
    if not svc or not svc.host or not svc.port:
        return ""
    http_path = svc.api_key_http_path or "/initialize.js"
    try:
        url = f"http://{svc.host}:{svc.port}{http_path}"
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        m = _re.search(r"apiKey['\"]?\s*[:=]\s*['\"]([a-f0-9A-F]+)['\"]", body)
        if m and m.group(1).strip():
            return m.group(1).strip()
    except Exception as exc:
        logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
        pass
    return ""
