#!/usr/bin/env python3
"""Arr and Servarr policy/runtime operations."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
from pathlib import Path

from media_stack.services.runtime_platform import (
    http_request,
    log,
    to_int,
)

import logging

from .factory import (
    _arr_service,
    _auth_service,
    _health_service,
    _servarr_policy_service,
)


class ServarrArrOps:

    def detect_arr_api_base(self, app_name, app_url, api_key, max_retries=3, retry_delay=5):
        """Detect the API base path for an Arr service with automatic retry.

        Retries with exponential backoff when the service is temporarily
        unreachable (connection refused, timeout, 502/503/504). This handles
        the common case where a service is still starting up during bootstrap
        or reconcile.
        """
        import time
        last_error = ""
        for attempt in range(max(1, max_retries)):
            for version in ("v3", "v1"):
                try:
                    status, parsed, body = http_request(
                        app_url, f"/api/{version}/system/status", api_key=api_key
                    )
                except Exception as exc:
                    last_error = f"{app_name}: /api/{version}/system/status connection error: {exc}"
                    continue
                if status == 200 and isinstance(parsed, dict):
                    api_base = f"/api/{version}"
                    log(f"[OK] {app_name}: detected API base {api_base}")
                    return api_base
                if status == 200 and not isinstance(parsed, dict):
                    log(
                        f"[WARN] {app_name}: /api/{version}/system/status returned HTTP 200 "
                        "but the response body is not JSON — possible auth redirect."
                    )
                last_error = f"{app_name}: /api/{version}/system/status returned HTTP {status}"

            if attempt < max_retries - 1:
                delay = retry_delay * (2 ** attempt)
                log(f"[WAIT] {app_name}: API base detection failed, retrying in {delay}s "
                    f"(attempt {attempt + 1}/{max_retries}, last_error={last_error})")
                time.sleep(delay)

        raise RuntimeError(
            f"{app_name}: unable to detect API base after {max_retries} attempts "
            f"(tried /api/v3 and /api/v1, last_error={last_error})"
        )

    def pick_first_profile_id(self, app_name, app_url, api_base, api_key, endpoint, field_label):
        status, data, body = http_request(app_url, f"{api_base}/{endpoint}", api_key=api_key)
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(f"{app_name}: failed to list {field_label} (HTTP {status}): {body}")

        for item in data:
            profile_id = to_int(item.get("id"))
            if profile_id and profile_id > 0:
                return profile_id

        raise RuntimeError(f"{app_name}: no valid {field_label} id found")

    def build_root_folder_payload(self, app_name, app_url, api_base, api_key, root_folder):
        payload = {"path": root_folder}

        # Lidarr/Readarr require extra properties when creating root folders.
        if app_name not in ("Lidarr", "Readarr"):
            return payload

        folder_name = Path(str(root_folder).rstrip("/")).name or app_name.lower()
        payload["name"] = folder_name

        quality_id = pick_first_profile_id(
            app_name, app_url, api_base, api_key, "qualityprofile", "quality profiles"
        )
        payload["defaultQualityProfileId"] = quality_id

        metadata_id = None
        for metadata_endpoint in ("metadataprofile", "metadataProfile"):
            try:
                metadata_id = pick_first_profile_id(
                    app_name,
                    app_url,
                    api_base,
                    api_key,
                    metadata_endpoint,
                    "metadata profiles",
                )
                break
            except Exception as exc:
                log_swallowed(exc)
                continue

        if metadata_id is None:
            raise RuntimeError(
                f"{app_name}: unable to discover metadata profile id for root folder creation"
            )

        payload["defaultMetadataProfileId"] = metadata_id

        # Safe defaults used by Lidarr/Readarr APIs when present.
        payload.setdefault("defaultMonitorOption", "all")
        payload.setdefault("defaultTags", [])

        return payload

    def ensure_root_folder(self, app_name, app_url, api_base, api_key, root_folder):
        status, data, body = http_request(app_url, f"{api_base}/rootfolder", api_key=api_key)
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(f"{app_name}: failed to list root folders (HTTP {status}): {body}")

        desired = root_folder.rstrip("/")
        for item in data:
            if str(item.get("path", "")).rstrip("/") == desired:
                log(f"[OK] {app_name}: root folder already exists: {root_folder}")
                return

        create_payload = build_root_folder_payload(app_name, app_url, api_base, api_key, root_folder)

        status, _, body = http_request(
            app_url,
            f"{api_base}/rootfolder",
            api_key=api_key,
            method="POST",
            payload=create_payload,
        )
        if status in (200, 201):
            log(f"[OK] {app_name}: created root folder {root_folder}")
            return
        if status == 400 and "already exists" in body.lower():
            log(f"[OK] {app_name}: root folder already exists: {root_folder}")
            return
        raise RuntimeError(
            f"{app_name}: failed to create root folder {root_folder} (HTTP {status}): {body}"
        )

    def trigger_health_check(self, app_name, app_url, api_base, api_key):
        return _health_service().trigger_health_check(
            app_name,
            app_url,
            api_base,
            api_key,
        )

    def trigger_arr_command(self, app_name, app_url, api_base, api_key, command_name, *, required=False):
        return _health_service().trigger_arr_command(
            app_name,
            app_url,
            api_base,
            api_key,
            command_name,
            required=required,
        )

    def fetch_arr_download_client_config(self, app_name, app_url, api_base, api_key):
        return _servarr_policy_service().fetch_download_client_config(
            app_name,
            app_url,
            api_base,
            api_key,
        )

    def ensure_arr_download_handling(self, app_cfg, app_url, api_base, api_key, handling_cfg):
        return _servarr_policy_service().ensure_download_handling(
            app_cfg,
            app_url,
            api_base,
            api_key,
            handling_cfg,
        )

    def resolve_arr_overrides_by_app(self, cfg_section, app_cfg):
        return _servarr_policy_service().resolve_overrides_by_app(cfg_section, app_cfg)

    def ensure_arr_media_management(self, app_cfg, app_url, api_base, api_key, media_cfg):
        return _servarr_policy_service().ensure_media_management(
            app_cfg,
            app_url,
            api_base,
            api_key,
            media_cfg,
        )

    def ensure_arr_quality_upgrade_policy(self, 
        cfg,
        app_cfg,
        app_url,
        api_base,
        api_key,
        quality_upgrade_cfg,
    ):
        return _servarr_policy_service().ensure_quality_upgrade_policy(
            cfg,
            app_cfg,
            app_url,
            api_base,
            api_key,
            quality_upgrade_cfg,
        )

    def ensure_readarr_metadata_source(self, cfg, app_cfg, app_url, api_base, api_key):
        app_impl = str(app_cfg.get("implementation") or "").strip().lower()
        if app_impl != "readarr":
            return

        readarr_cfg = cfg.get("readarr") or {}
        desired_source = str(readarr_cfg.get("metadata_source") or "").strip()
        if not desired_source:
            return

        status, current, body = http_request(app_url, f"{api_base}/config/development", api_key=api_key)
        if status != 200 or not isinstance(current, dict):
            raise RuntimeError(f"Readarr: failed reading development config (HTTP {status}): {body}")

        existing_source = str(current.get("metadataSource") or "").strip()
        if existing_source == desired_source:
            log(f"[OK] Readarr: metadata source already set to {desired_source}")
            return

        desired = dict(current)
        desired["metadataSource"] = desired_source
        status, _, body = http_request(
            app_url,
            f"{api_base}/config/development",
            api_key=api_key,
            method="PUT",
            payload=desired,
        )
        if status in (200, 201, 202):
            log(f"[OK] Readarr: updated metadata source to {desired_source}")
            return

        raise RuntimeError(f"Readarr: failed updating metadata source (HTTP {status}): {body}")

    def auth_scope_matches(self, auth_cfg, app_name, implementation):
        return _auth_service().auth_scope_matches(auth_cfg, app_name, implementation)

    def ensure_app_auth_settings(self, app_name, implementation, app_url, api_base, api_key, auth_cfg):
        return _auth_service().ensure_app_auth_settings(
            app_name,
            implementation,
            app_url,
            api_base,
            api_key,
            auth_cfg,
        )

    def choose_category(self, app_cfg, client_cfg):
        return _arr_service().choose_category(app_cfg, client_cfg)

    def normalize_mapping_path(self, path_value):
        return _arr_service().normalize_mapping_path(path_value)

    def build_sab_remote_path_mappings(self, sab_cfg):
        return _arr_service().build_sab_remote_path_mappings(sab_cfg)

    def ensure_arr_remote_path_mappings(self, app_cfg, app_url, api_base, api_key, mappings):
        _arr_service().ensure_arr_remote_path_mappings(app_cfg, app_url, api_base, api_key, mappings)

    def ensure_arr_download_client(self, 
        app_cfg,
        app_url,
        api_base,
        api_key,
        client_cfg,
        client_auth,
    ):
        _arr_service().ensure_arr_download_client(
            app_cfg=app_cfg,
            app_url=app_url,
            api_base=api_base,
            api_key=api_key,
            client_cfg=client_cfg,
            client_auth=client_auth,
        )


_instance = ServarrArrOps()
detect_arr_api_base = _instance.detect_arr_api_base
pick_first_profile_id = _instance.pick_first_profile_id
build_root_folder_payload = _instance.build_root_folder_payload
ensure_root_folder = _instance.ensure_root_folder
trigger_health_check = _instance.trigger_health_check
trigger_arr_command = _instance.trigger_arr_command
fetch_arr_download_client_config = _instance.fetch_arr_download_client_config
ensure_arr_download_handling = _instance.ensure_arr_download_handling
resolve_arr_overrides_by_app = _instance.resolve_arr_overrides_by_app
ensure_arr_media_management = _instance.ensure_arr_media_management
ensure_arr_quality_upgrade_policy = _instance.ensure_arr_quality_upgrade_policy
ensure_readarr_metadata_source = _instance.ensure_readarr_metadata_source
auth_scope_matches = _instance.auth_scope_matches
ensure_app_auth_settings = _instance.ensure_app_auth_settings
choose_category = _instance.choose_category
normalize_mapping_path = _instance.normalize_mapping_path
build_sab_remote_path_mappings = _instance.build_sab_remote_path_mappings
ensure_arr_remote_path_mappings = _instance.ensure_arr_remote_path_mappings
ensure_arr_download_client = _instance.ensure_arr_download_client
