"""Compatibility export surface for discovery-list operations."""

from __future__ import annotations

from media_stack.domain.discovery_lists.common import (
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
import importlib as _importlib


class DiscoveryListOpsService:
    @staticmethod
    def _load_tv_seed_module():
        from media_stack.core.service_registry.registry import SERVICES
        svc_id = next((s.id for s in SERVICES if s.stats_label and "series" in s.stats_label.lower()), "")
        if not svc_id:
            return None
        return _importlib.import_module(f"media_stack.services.apps.{svc_id}.{svc_id}_seed")

    def ensure_sonarr_seed_series(self, *args, **kwargs):
        mod = _load_tv_seed_module()
        return mod.ensure_sonarr_seed_series(*args, **kwargs) if mod else None

    def resolve_series_language_profile_id(self, *args, **kwargs):
        mod = _load_tv_seed_module()
        return mod.resolve_series_language_profile_id(*args, **kwargs) if mod else None

    def resolve_series_quality_profile_id(self, *args, **kwargs):
        mod = _load_tv_seed_module()
        return mod.resolve_series_quality_profile_id(*args, **kwargs) if mod else None

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


_instance = DiscoveryListOpsService()
ensure_sonarr_seed_series = _instance.ensure_sonarr_seed_series
resolve_series_language_profile_id = _instance.resolve_series_language_profile_id
resolve_series_quality_profile_id = _instance.resolve_series_quality_profile_id
_load_tv_seed_module = _instance._load_tv_seed_module
