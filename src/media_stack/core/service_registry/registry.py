"""Service registry — loaded from contracts/services.yaml.

To add, remove, or modify services, edit the YAML file.
No Python code changes needed. Third-party developers can extend
the registry by editing the config file.

The registry is loaded once at import time. The controller, health
probes, key discovery, rotation, and password reset all read from
this module.

ADR-0012 Phase D: the original 21 loose helpers are now organized
into three classes by cohesion:

* :class:`ServiceRegistryLoader` — pure file I/O (locate YAML, parse,
  load).
* :class:`ServiceLookup` — primary lookup surface (``get_service``,
  ``service_internal_url``, key/password feature filters).
* :class:`ServiceQueryHelpers` — smaller registry queries (profile
  gating, scale flags, web-ui flags, reload, HTTP/file key readers).

Every public + underscore-private name still exists at module scope
(via singleton aliases) so ``mock.patch("…registry.<name>", …)``
keeps working for callers and tests.
"""

from __future__ import annotations


import logging
import os
import re as _re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import Path as _Path
from typing import Any

import yaml

from media_stack.core.logging_utils import log_swallowed
from media_stack.api.services.key_formats import READERS as KEY_READERS

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
    # Host-published port, when different from the container's internal
    # ``port``. qBittorrent already claims 8080 at the host, so SABnzbd
    # has to remap to 8085 externally while still serving on 8080 inside.
    # Defaults to 0 meaning "same as ``port``" — most services are
    # symmetric.  The dashboard builds browser-side "direct" URLs using
    # this when set, falling back to ``port`` otherwise.  The controller
    # still uses ``port`` for in-network probes.
    published_port: int = 0
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
    # Optional icon override. Empty string means "use the dashboard's
    # default icon resolver" (CDN-served logo by service id, falling
    # back to a generic glyph when the CDN 404s). YAML can set this
    # to a fully-qualified URL or to a relative path the dashboard
    # serves locally — the field is verbatim user-controlled.
    icon_url: str = ""


# ---------------------------------------------------------------------------
# Loader (file I/O)
# ---------------------------------------------------------------------------


class ServiceRegistryLoader:
    """Locate and parse the per-service YAML contracts.

    Pure file-I/O: knows where the YAML lives, how to coerce a single
    entry into a :class:`ServiceDef`, and how to compose the final
    ``(services, categories)`` tuple. Each filesystem method dispatches
    through ``sys.modules[__name__]`` so tests can monkey-patch
    ``registry._find_services_dir`` etc. without poking at the class.
    """

    _logger: logging.Logger

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._logger = log or logger

    def find_services_dir(self) -> Path | None:
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

    def find_services_yaml(self) -> Path | None:
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

    def parse_service_entry(self, entry: dict[str, Any]) -> ServiceDef | None:
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
            # ``published_port`` defaults to 0 (= same as ``port``).  When
            # the contract sets a different value (only SABnzbd at the
            # moment, internal 8080 / published 8085 because qBittorrent
            # owns 8080 on the host), the dashboard's direct-link URL
            # builder consults this.  Without parsing it here the YAML
            # value gets dropped and ``/api/services`` returns
            # ``published_port == port`` regardless of the contract.
            published_port=int(entry.get("published_port", 0) or 0),
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
            icon_url=str(entry.get("icon_url", "")),
        )

    def load_registry(self) -> tuple[list[ServiceDef], list[str]]:
        """Load services from per-service YAML files or legacy services.yaml.

        Each filesystem step routes through the module-level alias so
        tests can ``mock.patch("…registry._find_services_dir", …)`` and
        intercept the discovery without re-instancing the loader.
        """
        services: list[ServiceDef] = []
        categories: list[str] = []

        mod = sys.modules[__name__]

        # Strategy 1: Per-service YAML files (preferred — one file per service)
        svc_dir = mod._find_services_dir()
        if svc_dir:
            for yaml_file in sorted(svc_dir.glob("*.yaml")):
                if yaml_file.name.startswith("_"):
                    continue  # Skip templates
                try:
                    with open(yaml_file, encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    entry = data.get("service", data)
                    svc = mod._parse_service_entry(entry)
                    if svc:
                        services.append(svc)
                except Exception as exc:
                    self._logger.warning(
                        "Failed to load service YAML %s: %s", yaml_file.name, exc,
                    )

        # Strategy 2: Legacy services.yaml (fallback — all services in one file)
        if not services:
            legacy = mod._find_services_yaml()
            if legacy:
                with open(legacy, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                categories = [str(c) for c in (data.get("categories") or []) if c]
                for entry in data.get("services") or []:
                    svc = mod._parse_service_entry(entry)
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


# Module-level singleton + aliases preserve every underscore name. The
# legacy module-level names are what tests and a handful of callers
# import directly (``from …registry import _find_services_dir``).
_LOADER = ServiceRegistryLoader()
_find_services_dir = _LOADER.find_services_dir
_find_services_yaml = _LOADER.find_services_yaml
_parse_service_entry = _LOADER.parse_service_entry
_load_registry = _LOADER.load_registry


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
# Lookup helpers (primary surface)
# ---------------------------------------------------------------------------


class ServiceLookup:
    """Primary lookup surface for the service registry.

    Reads the module-level ``SERVICES`` / ``SERVICE_MAP`` singletons
    via ``sys.modules[__name__]`` so tests that
    ``mock.patch("…registry.SERVICES", …)`` or
    ``mock.patch("…registry.SERVICE_MAP", …)`` keep intercepting.
    """

    def _services(self) -> list[ServiceDef]:
        return sys.modules[__name__].SERVICES

    def _service_map(self) -> dict[str, ServiceDef]:
        return sys.modules[__name__].SERVICE_MAP

    def get_service(self, service_id: str) -> ServiceDef | None:
        """Look up a service by ID."""
        return self._service_map().get(service_id)

    def service_internal_url(self, service_id: str) -> str:
        """Cluster-internal URL for ``service_id`` from the registry.

        Single source of truth for the ``http://<service>:<port>``
        pattern; raises ``KeyError`` if unknown.
        """
        svc = self._service_map().get(service_id)
        if not svc or not svc.host or not svc.port:
            raise KeyError(f"unknown service: {service_id}")
        return f"http://{svc.host}:{svc.port}"

    def get_services_with_api_keys(self) -> list[ServiceDef]:
        """Services that have API keys (for rotation/discovery)."""
        return [s for s in self._services() if s.api_key_env]

    def get_services_with_password_api(self) -> list[ServiceDef]:
        """Services that support password changes via API."""
        return [s for s in self._services() if s.password_api_path]


_LOOKUP = ServiceLookup()
get_service = _LOOKUP.get_service
service_internal_url = _LOOKUP.service_internal_url
get_services_with_api_keys = _LOOKUP.get_services_with_api_keys
get_services_with_password_api = _LOOKUP.get_services_with_password_api


# ---------------------------------------------------------------------------
# Query helpers (profile gating, scale flags, reload, HTTP/file key readers)
# ---------------------------------------------------------------------------


class ServiceQueryHelpers:
    """Smaller registry queries grouped by cohesion.

    All ``SERVICES`` / ``SERVICE_MAP`` reads route through
    ``sys.modules[__name__]`` so tests can patch the module-level
    singletons (``mock.patch("…registry.SERVICES", …)``,
    ``mock.patch("…registry.is_service_enabled", …)``) and the
    helpers see the override.
    """

    def _services(self) -> list[ServiceDef]:
        return sys.modules[__name__].SERVICES

    def _service_map(self) -> dict[str, ServiceDef]:
        return sys.modules[__name__].SERVICE_MAP

    def get_services_with_password_config(self) -> list[ServiceDef]:
        """Services that support password changes via config file."""
        return [s for s in self._services() if s.password_config]

    def get_active_service_ids(self) -> set[str]:
        """Services that are always active (no profile gate)."""
        return {s.id for s in self._services() if not s.profiles}

    def active_compose_profiles(self, env: dict[str, str] | None = None) -> set[str]:
        """Parse the comma-separated ``COMPOSE_PROFILES`` env var into a set.

        Used to decide whether a profile-gated service should
        be considered enabled at runtime.
        """
        src = env if env is not None else os.environ
        raw = str(src.get("COMPOSE_PROFILES", "") or "")
        return {p.strip() for p in raw.split(",") if p.strip()}

    def is_service_enabled(
        self, svc: ServiceDef, env: dict[str, str] | None = None,
    ) -> bool:
        """Return True iff ``svc`` should be considered enabled.

        Rule: a service is enabled when either
          - its ``profiles`` list is empty (no compose-profile gate;
            runs on any deploy), OR
          - at least one of its declared profiles appears in the
            active ``COMPOSE_PROFILES`` env var.

        The original anti-pattern this replaces: the homepage renderer
        used a hardcoded ``DEFAULT_HOSTS`` list that included every
        service the registry could declare (Authelia, Authentik, Plex,
        nvidia-only ones), so users got broken tiles for things they
        never deployed. (See docs/ratchet log: "homepage shows tiles
        for services not installed".)

        This is a coarse filter — it doesn't know whether the actual
        container is running or healthy, only whether the deploy
        *should* have spun it up. Runtime-state-driven filtering
        (asking Docker/K8s "does the container exist?") is option (a)
        on the auto-heal backlog. (b) catches the everyday case
        cheaply without the platform-SDK plumbing.
        """
        if not svc.profiles:
            return True
        active = sys.modules[__name__]._active_compose_profiles(env)
        return any(p in active for p in svc.profiles)

    def get_enabled_services(
        self, env: dict[str, str] | None = None,
    ) -> list[ServiceDef]:
        """Filtered SERVICES list — drops profile-gated services that
        aren't enabled by the active deploy."""
        is_enabled = sys.modules[__name__].is_service_enabled
        return [s for s in self._services() if is_enabled(s, env)]

    def build_apps_listing(
        self,
        services: list[ServiceDef] | None = None,
        *,
        include_all: bool = False,
        controller_port: int = 9100,
        env: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the JSON shape returned by ``GET /api/services``.

        Pure helper extracted from ``_handle_services`` so the filter
        logic is unit-testable without spinning up the full request
        handler (which transitively imports auth modules that need
        optional native deps).

        Default path filters out:
          * ``web_ui: false`` entries (job-DAG anchors with no UI).
          * Profile-gated services whose declared profiles aren't in the
            active ``COMPOSE_PROFILES`` env var.

        ``include_all=True`` returns every registry entry — useful for
        tooling and the registry inspector.
        """
        is_enabled = sys.modules[__name__].is_service_enabled
        src = list(self._services() if services is None else services)
        if include_all:
            candidates = src
        else:
            candidates = [s for s in src if is_enabled(s, env) and s.web_ui]

        out: list[dict[str, Any]] = [
            {
                "id": s.id, "name": s.name, "desc": s.desc,
                "category": s.category,
                "host": s.host, "port": s.port,
                "published_port": (s.published_port or s.port),
                "preserve_path_prefix": bool(s.preserve_path_prefix),
                "web_ui": bool(s.web_ui),
                "profiles": list(s.profiles),
                "enabled": bool(is_enabled(s, env)),
                "icon_url": s.icon_url,
            }
            for s in candidates
        ]
        out.append({
            "id": "controller", "name": "Media Stack Controller",
            "desc": "Orchestration API and dashboard",
            "category": "infrastructure", "host": "media-stack-controller",
            "port": controller_port, "published_port": controller_port,
            "health_path": "/healthz",
            "web_ui": True,
            "profiles": [],
            "enabled": True,
            "icon_url": "",
        })
        return out

    def get_scalable_services(self) -> list[ServiceDef]:
        """Services that participate in scale policy (replicas managed by deploy)."""
        return [s for s in self._services() if s.scalable]

    def get_scale_to_zero_services(self) -> list[ServiceDef]:
        """Services that start at 0 replicas and are enabled on demand."""
        return [s for s in self._services() if s.scale_to_zero]

    def get_web_ui_services(self) -> list[ServiceDef]:
        """Services that have a browser-accessible web interface."""
        return [s for s in self._services() if s.web_ui]

    def get_preserve_path_prefix_services(self) -> list[ServiceDef]:
        """Services whose APIs require the path prefix to be preserved (not stripped)."""
        return [s for s in self._services() if s.preserve_path_prefix]

    def reload_registry(self) -> None:
        """Reload services from YAML. Call after editing services.yaml."""
        global SERVICES, SERVICE_MAP, CATEGORIES, _CATEGORY_ORDER
        SERVICES, _CATEGORY_ORDER = sys.modules[__name__]._load_registry()
        SERVICE_MAP = {s.id: s for s in SERVICES}
        CATEGORIES.clear()
        for cat in _CATEGORY_ORDER:
            ids = [s.id for s in SERVICES if s.category == cat]
            if ids:
                CATEGORIES.append({"label": cat.capitalize(), "ids": ids})

    def read_api_key_from_file(self, service_id: str, config_root: str) -> str:
        """Read an API key from a service's config file using its declared format.

        Returns the key string, or empty string if not found or unsupported.
        This is the single entry point for all file-based key discovery — driven
        entirely by the service's contract YAML fields (api_key_config,
        api_key_format).
        """
        svc = self._service_map().get(service_id)
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

    def read_api_key_via_http(self, service_id: str) -> str:
        """Try to fetch an API key from a running service over HTTP.

        Uses the api_key_http_path declared in the contract, or falls back to
        /initialize.js (common for Arr apps). Returns empty string on failure.
        """
        svc = self._service_map().get(service_id)
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
            log_swallowed(exc)
        return ""


_QUERY_HELPERS = ServiceQueryHelpers()
get_services_with_password_config = _QUERY_HELPERS.get_services_with_password_config
get_active_service_ids = _QUERY_HELPERS.get_active_service_ids
_active_compose_profiles = _QUERY_HELPERS.active_compose_profiles
is_service_enabled = _QUERY_HELPERS.is_service_enabled
get_enabled_services = _QUERY_HELPERS.get_enabled_services
build_apps_listing = _QUERY_HELPERS.build_apps_listing
get_scalable_services = _QUERY_HELPERS.get_scalable_services
get_scale_to_zero_services = _QUERY_HELPERS.get_scale_to_zero_services
get_web_ui_services = _QUERY_HELPERS.get_web_ui_services
get_preserve_path_prefix_services = _QUERY_HELPERS.get_preserve_path_prefix_services
reload_registry = _QUERY_HELPERS.reload_registry
read_api_key_from_file = _QUERY_HELPERS.read_api_key_from_file
read_api_key_via_http = _QUERY_HELPERS.read_api_key_via_http


# ---------------------------------------------------------------------------
# Registry-driven API key readers — format-agnostic.
#
# Key formats are declared in each service's YAML contract (api_key_format).
# To support a new format, add a reader to key_formats.py and the format
# name in your service YAML — no other code changes needed.
# ``KEY_READERS`` is re-exported from this module for legacy callers
# that import it directly (``from …registry import KEY_READERS``).
# ---------------------------------------------------------------------------

