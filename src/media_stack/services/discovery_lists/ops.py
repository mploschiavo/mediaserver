"""Compatibility export surface for discovery-list operations."""

from __future__ import annotations

from .common import (
    coerce_for_example,
    normalize_title,
    pick_series_lookup_candidate,
    service_to_int,
)
from .import_lists import (
    build_arr_import_list_payload,
    ensure_arr_discovery_lists_for_app,
    resolve_import_list_definitions,
)
from .kickoff import trigger_arr_discovery_kickoff
from media_stack.services.apps.sonarr.sonarr_seed import (
    ensure_sonarr_seed_series,
    resolve_series_language_profile_id,
    resolve_series_quality_profile_id,
)

__all__ = [
    "coerce_for_example",
    "normalize_title",
    "pick_series_lookup_candidate",
    "service_to_int",
    "resolve_series_quality_profile_id",
    "resolve_series_language_profile_id",
    "ensure_sonarr_seed_series",
    "resolve_import_list_definitions",
    "build_arr_import_list_payload",
    "ensure_arr_discovery_lists_for_app",
    "trigger_arr_discovery_kickoff",
]
