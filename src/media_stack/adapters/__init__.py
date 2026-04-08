"""Reusable bootstrap helpers for media-stack automation."""

from media_stack.services.apps.bazarr.adapters import apply_scalar_updates as apply_bazarr_scalar_updates
from .common import (
    bool_cfg,
    coerce_list,
    env_truthy,
    normalize_base_path,
    normalize_url,
    parse_service_url,
    to_int,
)
from media_stack.services.apps.homepage.adapters import DEFAULT_HOSTS as DEFAULT_HOMEPAGE_HOSTS
from media_stack.services.apps.homepage.adapters import render_services_yaml
from .http_client import http_request
from media_stack.services.apps.jellyfin.adapters import apply_artwork_profile, normalize_provider_name, reorder_provider_names
from .servarr import choose_profile, choose_root_folder, find_existing_servarr

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
    "DEFAULT_HOMEPAGE_HOSTS",
    "render_services_yaml",
    "apply_bazarr_scalar_updates",
    "normalize_provider_name",
    "reorder_provider_names",
    "apply_artwork_profile",
]
