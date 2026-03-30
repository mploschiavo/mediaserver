"""Reusable bootstrap helpers for media-stack automation."""

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
from .homepage import DEFAULT_HOSTS as DEFAULT_HOMEPAGE_HOSTS, render_services_yaml
from .bazarr import apply_scalar_updates as apply_bazarr_scalar_updates
from .jellyfin import apply_artwork_profile, normalize_provider_name, reorder_provider_names

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
