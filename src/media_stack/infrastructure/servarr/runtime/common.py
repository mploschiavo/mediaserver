#!/usr/bin/env python3
"""Servarr-specific runtime helpers."""

from __future__ import annotations

from media_stack.adapters.servarr import choose_profile as _lib_choose_profile
from media_stack.adapters.servarr import choose_root_folder as _lib_choose_root_folder
from media_stack.adapters.servarr import find_existing_servarr as _lib_find_existing_servarr
from media_stack.adapters.servarr import (
    normalize_remote_path_mappings as _lib_normalize_remote_path_mappings,
)

from media_stack.services.runtime_platform import coerce_list, http_request


class ServarrCommon:

    def choose_profile(self, profiles, preferred_id=None, preferred_names=None):
        return _lib_choose_profile(
            profiles,
            preferred_id=preferred_id,
            preferred_names=preferred_names,
        )

    def choose_root_folder(self, root_folders, preferred_path):
        return _lib_choose_root_folder(root_folders, preferred_path)

    def find_existing_servarr(self, existing, name, hostname, port, base_url, is4k):
        return _lib_find_existing_servarr(existing, name, hostname, port, base_url, is4k)

    def normalize_remote_path_mappings(self, mappings):
        return _lib_normalize_remote_path_mappings(mappings)

    def resolve_arr_quality_preferences(self, cfg, app_cfg):
        quality_cfg = cfg.get("quality_profiles") or {}
        by_app = quality_cfg.get("by_app") or {}

        app_name = str(app_cfg.get("name") or "")
        app_impl = str(app_cfg.get("implementation") or "")

        app_overrides = (
            by_app.get(app_name)
            or by_app.get(app_impl)
            or by_app.get(app_name.lower())
            or by_app.get(app_impl.lower())
            or {}
        )

        preferred_id = (
            app_cfg.get("quality_profile_id")
            if "quality_profile_id" in app_cfg
            else app_overrides.get("preferred_id")
        )
        preferred_names = coerce_list(
            app_cfg.get("quality_profile_preferred_names")
            or app_overrides.get("preferred_names")
            or quality_cfg.get("preferred_names")
            or []
        )
        return preferred_id, preferred_names

    def get_arr_quality_profile(self,
        app_name,
        app_url,
        api_base,
        api_key,
        preferred_id=None,
        preferred_names=None,
    ):
        status, profiles, body = http_request(app_url, f"{api_base}/qualityprofile", api_key=api_key)
        if status != 200 or not isinstance(profiles, list):
            raise RuntimeError(f"{app_name}: failed to list quality profiles (HTTP {status}): {body}")
        selected = choose_profile(
            profiles,
            preferred_id=preferred_id,
            preferred_names=preferred_names,
        )
        if not selected:
            raise RuntimeError(f"{app_name}: no quality profiles returned by API.")
        return selected

    def get_arr_app(self, arr_apps, implementation):
        target = str(implementation or "").strip()
        for app in arr_apps or []:
            if str((app or {}).get("implementation") or "").strip() == target:
                return app
        return None


_instance = ServarrCommon()
choose_profile = _instance.choose_profile
choose_root_folder = _instance.choose_root_folder
find_existing_servarr = _instance.find_existing_servarr
normalize_remote_path_mappings = _instance.normalize_remote_path_mappings
resolve_arr_quality_preferences = _instance.resolve_arr_quality_preferences
get_arr_quality_profile = _instance.get_arr_quality_profile
get_arr_app = _instance.get_arr_app
