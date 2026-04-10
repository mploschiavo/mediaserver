"""Reusable bootstrap helpers for media-stack automation.

App-specific adapters are loaded dynamically from services/apps/<id>/adapters.py
at first access — no hardcoded service imports.
"""

import importlib
from typing import Any

from .common import (
    bool_cfg,
    coerce_list,
    env_truthy,
    normalize_base_path,
    normalize_url,
    parse_service_url,
    to_int,
)
from .http_client import http_request
from .servarr import choose_profile, choose_root_folder, find_existing_servarr


# ---------------------------------------------------------------------------
# Lazy-loaded app-specific adapters — resolved at first call
# ---------------------------------------------------------------------------

def _load_app_adapter(app_id: str, attr: str) -> Any:
    """Import services.apps.<app_id>.adapters and return the named attribute."""
    mod = importlib.import_module(f"media_stack.services.apps.{app_id}.adapters")
    return getattr(mod, attr)


def _find_svc_id(category: str = "", **match: Any) -> str:
    """Find a service ID by category or attribute match."""
    from media_stack.api.services.registry import SERVICES
    for s in SERVICES:
        if category and s.category == category:
            return s.id
        for k, v in match.items():
            if getattr(s, k, None) == v:
                return s.id
    return ""

def apply_bazarr_scalar_updates(*args: Any, **kwargs: Any) -> Any:
    svc_id = _find_svc_id(category="automation") or ""
    # Find the subtitles service
    from media_stack.api.services.registry import SERVICES
    svc_id = next((s.id for s in SERVICES if s.desc and "subtitle" in s.desc.lower()), svc_id)
    return _load_app_adapter(svc_id, "apply_scalar_updates")(*args, **kwargs)

def render_services_yaml(*args: Any, **kwargs: Any) -> Any:
    from media_stack.api.services.registry import SERVICES
    svc_id = next((s.id for s in SERVICES if s.category == "management" and s.health_path == "/"), "")
    return _load_app_adapter(svc_id, "render_services_yaml")(*args, **kwargs)

def normalize_provider_name(*args: Any, **kwargs: Any) -> Any:
    svc_id = _find_svc_id(category="media")
    return _load_app_adapter(svc_id, "normalize_provider_name")(*args, **kwargs)

def reorder_provider_names(*args: Any, **kwargs: Any) -> Any:
    svc_id = _find_svc_id(category="media")
    return _load_app_adapter(svc_id, "reorder_provider_names")(*args, **kwargs)

def apply_artwork_profile(*args: Any, **kwargs: Any) -> Any:
    svc_id = _find_svc_id(category="media")
    return _load_app_adapter(svc_id, "apply_artwork_profile")(*args, **kwargs)

def _get_default_homepage_hosts() -> Any:
    from media_stack.api.services.registry import SERVICES
    svc_id = next((s.id for s in SERVICES if s.category == "management" and s.health_path == "/"), "")
    if not svc_id:
        return {}
    return _load_app_adapter(svc_id, "DEFAULT_HOSTS")

DEFAULT_HOMEPAGE_HOSTS = property(lambda self: _get_default_homepage_hosts())


__all__ = [
    "bool_cfg",
    "coerce_list",
    "env_truthy",
    "normalize_base_path",
    "normalize_url",
    "parse_service_url",
    "to_int",
    "http_request",
    "choose_profile",
    "choose_root_folder",
    "find_existing_servarr",
    "render_services_yaml",
    "apply_bazarr_scalar_updates",
    "normalize_provider_name",
    "reorder_provider_names",
    "apply_artwork_profile",
]
