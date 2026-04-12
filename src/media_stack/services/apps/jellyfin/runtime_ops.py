#!/usr/bin/env python3
"""Jellyfin runtime orchestration boundary.

All Jellyfin-specific bootstrap orchestration helpers live here so the
technology is self-contained and swappable via manifests/adapters.
"""

from __future__ import annotations

import os
import re
from urllib import parse

from media_stack.services.apps.homepage.adapters import DEFAULT_HOSTS as _lib_default_homepage_hosts
from media_stack.services.apps.homepage.adapters import render_services_yaml as _lib_render_homepage_services_yaml
from .adapters import apply_artwork_profile as _lib_jellyfin_apply_artwork_profile
from .adapters import reorder_provider_names as _lib_jellyfin_reorder_provider_names

from media_stack.services.apps.jellyfin.auto_collections import JellyfinAutoCollectionsService
from media_stack.services.config_artifacts_service import ConfigArtifactsService
from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    http_request,
    load_bootstrap_default_json,
    log,
    normalize_url,
    resolve_app_service_class,
    resolve_path,
    to_int,
    wait_for_service,
)
from media_stack.services.runtime_secrets import candidate_config_roots

from .api_key_db import (
    read_jellyfin_api_key_from_db as _read_jellyfin_api_key_from_db,
    resolve_jellyfin_api_key as _resolve_jellyfin_api_key,
)

from .home_rails_service import JellyfinHomeRailsDependencies, JellyfinHomeRailsService
from .libraries_service import JellyfinLibrariesDependencies, JellyfinLibrariesService
from .livetv_service import JellyfinLiveTvDependencies, JellyfinService
from .livetv_source_service import JellyfinLiveTvSourceService
from .livetv_state_service import JellyfinLiveTvStateService
from .playback_service import JellyfinPlaybackDependencies, JellyfinPlaybackService
from .plugins_service import JellyfinPluginsDependencies, JellyfinPluginsService
from .prewarm_service import JellyfinPrewarmDependencies, JellyfinPrewarmService


class JellyfinRuntimeOps:

    @staticmethod
    def _jellyfin_service(cfg=None) -> JellyfinService:
        deps = JellyfinLiveTvDependencies(
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            to_int=to_int,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            resolve_api_key=resolve_jellyfin_api_key,
            jellyfin_request=jellyfin_request,
            prepare_tuner_url=prepare_jellyfin_m3u_tuner_url,
            prepare_guide_path=prepare_jellyfin_xmltv_guide_path,
            load_state=load_jellyfin_livetv_state,
            resolve_tuner_type_id=resolve_jellyfin_tuner_type_id,
            normalize_enabled_tuner_ids=normalize_enabled_tuner_ids,
            delete_entity=delete_jellyfin_livetv_entity,
            trigger_refresh=trigger_jellyfin_livetv_refresh,
        )
        service_cls = resolve_app_service_class("jellyfin_livetv_service", JellyfinService)
        return service_cls(deps=deps)

    @staticmethod
    def _jellyfin_livetv_source_service(cfg=None) -> JellyfinLiveTvSourceService:
        service_cls = resolve_app_service_class(
            "jellyfin_livetv_source_service", JellyfinLiveTvSourceService
        )
        return service_cls(
            coerce_list=coerce_list,
            candidate_config_roots=candidate_config_roots,
            resolve_path=resolve_path,
            log=log,
        )

    @staticmethod
    def _jellyfin_livetv_state_service(cfg=None) -> JellyfinLiveTvStateService:
        service_cls = resolve_app_service_class(
            "jellyfin_livetv_state_service", JellyfinLiveTvStateService
        )
        return service_cls(
            coerce_list=coerce_list,
            resolve_path=resolve_path,
            candidate_config_roots=candidate_config_roots,
            jellyfin_request=jellyfin_request,
            log=log,
        )

    @staticmethod
    def _jellyfin_home_rails_service(cfg=None) -> JellyfinHomeRailsService:
        deps = JellyfinHomeRailsDependencies(
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            to_int=to_int,
            jellyfin_request=jellyfin_request,
            jellyfin_build_query_path=jellyfin_build_query_path,
            jellyfin_items_from_payload=jellyfin_items_from_payload,
            normalize_item_ids=normalize_item_ids,
            chunked=chunked,
            resolve_jellyfin_user_id_value=resolve_jellyfin_user_id_value,
        )
        service_cls = resolve_app_service_class("jellyfin_home_rails_service", JellyfinHomeRailsService)
        return service_cls(deps=deps)

    @staticmethod
    def _jellyfin_libraries_service(cfg=None) -> JellyfinLibrariesService:
        deps = JellyfinLibrariesDependencies(
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            resolve_api_key=resolve_jellyfin_api_key,
            jellyfin_request=jellyfin_request,
            build_query_path=jellyfin_build_query_path,
            reorder_provider_names=_lib_jellyfin_reorder_provider_names,
            apply_artwork_profile=_lib_jellyfin_apply_artwork_profile,
        )
        service_cls = resolve_app_service_class("jellyfin_libraries_service", JellyfinLibrariesService)
        return service_cls(deps=deps)

    @staticmethod
    def _jellyfin_plugins_service(cfg=None) -> JellyfinPluginsService:
        deps = JellyfinPluginsDependencies(
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            resolve_api_key=resolve_jellyfin_api_key,
            jellyfin_request=jellyfin_request,
        )
        service_cls = resolve_app_service_class("jellyfin_plugins_service", JellyfinPluginsService)
        return service_cls(deps=deps)

    @staticmethod
    def _jellyfin_playback_service(cfg=None) -> JellyfinPlaybackService:
        deps = JellyfinPlaybackDependencies(
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            resolve_api_key=resolve_jellyfin_api_key,
            jellyfin_request=jellyfin_request,
            build_query_path=jellyfin_build_query_path,
            resolve_user_id=resolve_jellyfin_user_id_value,
            normalize_plugin_name=normalize_plugin_name,
        )
        service_cls = resolve_app_service_class("jellyfin_playback_service", JellyfinPlaybackService)
        return service_cls(deps=deps)

    @staticmethod
    def _jellyfin_prewarm_service(cfg=None) -> JellyfinPrewarmService:
        deps = JellyfinPrewarmDependencies(
            log=log,
            bool_cfg=bool_cfg,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            resolve_api_key=resolve_jellyfin_api_key,
            jellyfin_request=jellyfin_request,
            build_query_path=jellyfin_build_query_path,
            trigger_livetv_refresh=trigger_jellyfin_livetv_refresh,
        )
        service_cls = resolve_app_service_class("jellyfin_prewarm_service", JellyfinPrewarmService)
        return service_cls(deps=deps)

    def jellyfin_request(self, base_url, path, api_key, method="GET", payload=None, timeout=30):
        if not api_key:
            raise RuntimeError("Jellyfin API key is required for authenticated requests.")
        separator = "&" if "?" in path else "?"
        encoded_key = parse.quote(str(api_key), safe="")
        return http_request(
            base_url,
            f"{path}{separator}api_key={encoded_key}",
            method=method,
            payload=payload,
            timeout=timeout,
        )

    def prepare_jellyfin_m3u_tuner_url(self, tuner, guides, config_root, guide_channel_ids_cache=None):
        return _jellyfin_livetv_source_service().prepare_m3u_tuner_url(
            tuner=tuner,
            guides=guides,
            config_root=config_root,
            guide_channel_ids_cache=guide_channel_ids_cache,
        )

    def prepare_jellyfin_xmltv_guide_path(self, guide, tuners, config_root):
        return _jellyfin_livetv_source_service().prepare_xmltv_guide_path(
            guide=guide,
            tuners=tuners,
            config_root=config_root,
        )

    def read_jellyfin_api_key_from_db(self, config_root, jellyfin_cfg):
        return _read_jellyfin_api_key_from_db(
            config_root, jellyfin_cfg, coerce_list=coerce_list, resolve_path=resolve_path
        )

    def resolve_jellyfin_api_key(self, jellyfin_cfg, config_root):
        return _resolve_jellyfin_api_key(
            jellyfin_cfg,
            config_root,
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            resolve_path=resolve_path,
        )

    def load_jellyfin_livetv_state(self, config_root, live_cfg):
        return _jellyfin_livetv_state_service(
            live_cfg if isinstance(live_cfg, dict) else None
        ).load_state(
            config_root=config_root,
            live_cfg=live_cfg,
        )

    def resolve_jellyfin_tuner_type_id(self, jellyfin_url, jellyfin_api_key, requested_type):
        return _jellyfin_livetv_state_service().resolve_tuner_type_id(
            jellyfin_url=jellyfin_url,
            jellyfin_api_key=jellyfin_api_key,
            requested_type=requested_type,
        )

    def normalize_enabled_tuner_ids(self, enabled_tuners, state):
        return _jellyfin_livetv_state_service().normalize_enabled_tuner_ids(
            enabled_tuners=enabled_tuners,
            state=state,
        )

    def delete_jellyfin_livetv_entity(self, jellyfin_url, jellyfin_api_key, entity, entity_id):
        return _jellyfin_livetv_state_service().delete_entity(
            jellyfin_url=jellyfin_url,
            jellyfin_api_key=jellyfin_api_key,
            entity=entity,
            entity_id=entity_id,
        )

    def trigger_jellyfin_scheduled_task(self, jellyfin_url, jellyfin_api_key, preferred_names):
        return _jellyfin_livetv_state_service().trigger_scheduled_task(
            jellyfin_url=jellyfin_url,
            jellyfin_api_key=jellyfin_api_key,
            preferred_names=preferred_names,
        )

    def trigger_jellyfin_livetv_refresh(self, jellyfin_url, jellyfin_api_key, endpoint_path, label, **kwargs):
        return _jellyfin_livetv_state_service().trigger_refresh(
            jellyfin_url=jellyfin_url,
            jellyfin_api_key=jellyfin_api_key,
            endpoint_path=endpoint_path,
            label=label,
            **kwargs,
        )

    def ensure_jellyfin_livetv(self, cfg, config_root, wait_timeout):
        _jellyfin_service(cfg).ensure_livetv(cfg, config_root, wait_timeout)

    def ensure_jellyfin_libraries(self, cfg, config_root, wait_timeout):
        _jellyfin_libraries_service(cfg).ensure(cfg, config_root, wait_timeout)

    def ensure_jellyfin_prewarm(self, cfg, config_root, wait_timeout):
        _jellyfin_prewarm_service(cfg).ensure(cfg, config_root, wait_timeout)

    def jellyfin_build_query_path(self, path, params):
        pairs = []
        for key, raw_value in (params or {}).items():
            if raw_value is None:
                continue
            if isinstance(raw_value, (list, tuple, set)):
                for item in raw_value:
                    if item is None:
                        continue
                    text = str(item).strip()
                    if text:
                        pairs.append((str(key), text))
                continue
            text = str(raw_value).strip()
            if text:
                pairs.append((str(key), text))
        if not pairs:
            return path
        return f"{path}?{parse.urlencode(pairs, doseq=True)}"

    def jellyfin_items_from_payload(self, payload):
        if isinstance(payload, dict):
            items = payload.get("Items")
            return items if isinstance(items, list) else []
        if isinstance(payload, list):
            return payload
        return []

    def normalize_item_ids(self, items):
        out = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("Id") or "").strip()
            if not item_id:
                continue
            lowered = item_id.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(item_id)
        return out

    def chunked(self, values, size):
        batch = []
        for value in values:
            batch.append(value)
            if len(batch) >= size:
                yield batch
                batch = []
        if batch:
            yield batch

    def resolve_jellyfin_user_id_value(self, section_cfg, jellyfin_url, jellyfin_api_key):
        user_id = (
            os.environ.get(str(section_cfg.get("user_id_env", "JELLYFIN_USER_ID"))) or ""
        ).strip()
        if user_id:
            return user_id

        user_id = str(section_cfg.get("user_id") or "").strip()
        if user_id:
            return user_id

        if bool_cfg(section_cfg, "auto_discover_user_id", True):
            preferred_username = (
                os.environ.get(str(section_cfg.get("preferred_username_env", "STACK_ADMIN_USERNAME")))
                or section_cfg.get("preferred_username")
                or ""
            )
            return detect_jellyfin_user_id(jellyfin_url, jellyfin_api_key, preferred_username)

        return ""

    def ensure_jellyfin_playback_defaults(self, cfg, config_root, wait_timeout):
        _jellyfin_playback_service(cfg).ensure(cfg, config_root, wait_timeout)

    def default_jellyfin_home_rails(self):
        return _jellyfin_home_rails_service().default_rails()

    def find_jellyfin_collection_by_name(self, jellyfin_url, jellyfin_api_key, user_id, collection_name):
        return _jellyfin_home_rails_service().find_collection_by_name(
            jellyfin_url, jellyfin_api_key, user_id, collection_name
        )

    def collection_item_ids(self, jellyfin_url, jellyfin_api_key, user_id, collection_id):
        return _jellyfin_home_rails_service().collection_item_ids(
            jellyfin_url, jellyfin_api_key, user_id, collection_id
        )

    def update_collection_items(self, jellyfin_url, jellyfin_api_key, collection_id, to_add, to_remove):
        return _jellyfin_home_rails_service().update_collection_items(
            jellyfin_url, jellyfin_api_key, collection_id, to_add, to_remove
        )

    def ensure_jellyfin_collection_membership(self, 
        jellyfin_url,
        jellyfin_api_key,
        user_id,
        collection_name,
        desired_ids,
        clear_when_empty=False,
    ):
        return _jellyfin_home_rails_service().ensure_collection_membership(
            jellyfin_url,
            jellyfin_api_key,
            user_id,
            collection_name,
            desired_ids,
            clear_when_empty=clear_when_empty,
        )

    def delete_jellyfin_collection_by_name(self, jellyfin_url, jellyfin_api_key, user_id, collection_name):
        return _jellyfin_home_rails_service().delete_collection_by_name(
            jellyfin_url, jellyfin_api_key, user_id, collection_name
        )

    def run_jellyfin_rail_query(self, jellyfin_url, jellyfin_api_key, user_id, rail_cfg, max_items):
        return _jellyfin_home_rails_service().run_rail_query(
            jellyfin_url, jellyfin_api_key, user_id, rail_cfg, max_items
        )

    def ensure_jellyfin_home_rails(self, cfg, config_root, wait_timeout):
        _jellyfin_home_rails_service(cfg).ensure_home_rails(
            cfg,
            config_root,
            wait_timeout,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            resolve_jellyfin_api_key=resolve_jellyfin_api_key,
        )

    def normalize_plugin_name(self, value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    def ensure_jellyfin_plugins(self, cfg, config_root, wait_timeout):
        _jellyfin_plugins_service(cfg).ensure(cfg, config_root, wait_timeout)

    @staticmethod
    def _jellyfin_auto_collections_service() -> JellyfinAutoCollectionsService:
        return JellyfinAutoCollectionsService(
            bool_cfg=bool_cfg,
            resolve_path=resolve_path,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            resolve_jellyfin_api_key=resolve_jellyfin_api_key,
            jellyfin_request=jellyfin_request,
            log=log,
            render_yaml=ConfigArtifactsService.render_yaml,
        )

    def detect_jellyfin_user_id(self, jellyfin_url, jellyfin_api_key, preferred_username):
        return _jellyfin_auto_collections_service().detect_jellyfin_user_id(
            jellyfin_url, jellyfin_api_key, preferred_username
        )

    def ensure_jellyfin_auto_collections_config(self, cfg, config_root, wait_timeout):
        _jellyfin_auto_collections_service().ensure_config(
            cfg=cfg,
            config_root=config_root,
            wait_timeout=wait_timeout,
            resolve_jellyfin_user_id_value_fn=resolve_jellyfin_user_id_value,
        )


_instance = JellyfinRuntimeOps()
jellyfin_request = _instance.jellyfin_request
prepare_jellyfin_m3u_tuner_url = _instance.prepare_jellyfin_m3u_tuner_url
prepare_jellyfin_xmltv_guide_path = _instance.prepare_jellyfin_xmltv_guide_path
read_jellyfin_api_key_from_db = _instance.read_jellyfin_api_key_from_db
resolve_jellyfin_api_key = _instance.resolve_jellyfin_api_key
load_jellyfin_livetv_state = _instance.load_jellyfin_livetv_state
resolve_jellyfin_tuner_type_id = _instance.resolve_jellyfin_tuner_type_id
normalize_enabled_tuner_ids = _instance.normalize_enabled_tuner_ids
delete_jellyfin_livetv_entity = _instance.delete_jellyfin_livetv_entity
trigger_jellyfin_scheduled_task = _instance.trigger_jellyfin_scheduled_task
trigger_jellyfin_livetv_refresh = _instance.trigger_jellyfin_livetv_refresh
ensure_jellyfin_livetv = _instance.ensure_jellyfin_livetv
ensure_jellyfin_libraries = _instance.ensure_jellyfin_libraries
ensure_jellyfin_prewarm = _instance.ensure_jellyfin_prewarm
jellyfin_build_query_path = _instance.jellyfin_build_query_path
jellyfin_items_from_payload = _instance.jellyfin_items_from_payload
normalize_item_ids = _instance.normalize_item_ids
chunked = _instance.chunked
resolve_jellyfin_user_id_value = _instance.resolve_jellyfin_user_id_value
ensure_jellyfin_playback_defaults = _instance.ensure_jellyfin_playback_defaults
default_jellyfin_home_rails = _instance.default_jellyfin_home_rails
find_jellyfin_collection_by_name = _instance.find_jellyfin_collection_by_name
collection_item_ids = _instance.collection_item_ids
update_collection_items = _instance.update_collection_items
ensure_jellyfin_collection_membership = _instance.ensure_jellyfin_collection_membership
delete_jellyfin_collection_by_name = _instance.delete_jellyfin_collection_by_name
run_jellyfin_rail_query = _instance.run_jellyfin_rail_query
ensure_jellyfin_home_rails = _instance.ensure_jellyfin_home_rails
normalize_plugin_name = _instance.normalize_plugin_name
ensure_jellyfin_plugins = _instance.ensure_jellyfin_plugins
detect_jellyfin_user_id = _instance.detect_jellyfin_user_id
ensure_jellyfin_auto_collections_config = _instance.ensure_jellyfin_auto_collections_config


__all__ = [
    "collection_item_ids",
    "default_jellyfin_home_rails",
    "delete_jellyfin_collection_by_name",
    "delete_jellyfin_livetv_entity",
    "detect_jellyfin_user_id",
    "ensure_jellyfin_auto_collections_config",
    "ensure_jellyfin_collection_membership",
    "ensure_jellyfin_home_rails",
    "ensure_jellyfin_libraries",
    "ensure_jellyfin_livetv",
    "ensure_jellyfin_playback_defaults",
    "ensure_jellyfin_plugins",
    "ensure_jellyfin_prewarm",
    "find_jellyfin_collection_by_name",
    "jellyfin_build_query_path",
    "jellyfin_items_from_payload",
    "jellyfin_request",
    "load_jellyfin_livetv_state",
    "normalize_enabled_tuner_ids",
    "normalize_item_ids",
    "prepare_jellyfin_m3u_tuner_url",
    "prepare_jellyfin_xmltv_guide_path",
    "read_jellyfin_api_key_from_db",
    "resolve_jellyfin_api_key",
    "resolve_jellyfin_tuner_type_id",
    "resolve_jellyfin_user_id_value",
    "run_jellyfin_rail_query",
    "trigger_jellyfin_livetv_refresh",
    "trigger_jellyfin_scheduled_task",
    "update_collection_items",
]
_jellyfin_auto_collections_service = _instance._jellyfin_auto_collections_service
_jellyfin_home_rails_service = _instance._jellyfin_home_rails_service
_jellyfin_libraries_service = _instance._jellyfin_libraries_service
_jellyfin_livetv_source_service = _instance._jellyfin_livetv_source_service
_jellyfin_livetv_state_service = _instance._jellyfin_livetv_state_service
_jellyfin_playback_service = _instance._jellyfin_playback_service
_jellyfin_plugins_service = _instance._jellyfin_plugins_service
_jellyfin_prewarm_service = _instance._jellyfin_prewarm_service
_jellyfin_service = _instance._jellyfin_service
