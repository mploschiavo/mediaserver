#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from urllib import parse, request

from bootstrap_lib.bazarr import apply_scalar_updates as _lib_bazarr_apply_scalar_updates
from bootstrap_lib.common import (
    bool_cfg as _lib_bool_cfg,
)
from bootstrap_lib.common import (
    coerce_list as _lib_coerce_list,
)
from bootstrap_lib.common import (
    env_truthy as _lib_env_truthy,
)
from bootstrap_lib.common import (
    normalize_base_path as _lib_normalize_base_path,
)
from bootstrap_lib.common import (
    normalize_url as _lib_normalize_url,
)
from bootstrap_lib.common import (
    parse_service_url as _lib_parse_service_url,
)
from bootstrap_lib.common import (
    to_int as _lib_to_int,
)
from bootstrap_lib.defaults import load_json_default as _lib_load_json_default
from bootstrap_lib.homepage import (
    DEFAULT_HOSTS as _lib_default_homepage_hosts,
)
from bootstrap_lib.homepage import (
    render_services_yaml as _lib_render_homepage_services_yaml,
)
from bootstrap_lib.http_client import http_request as _lib_http_request
from bootstrap_lib.jellyfin import (
    apply_artwork_profile as _lib_jellyfin_apply_artwork_profile,
)
from bootstrap_lib.jellyfin import (
    reorder_provider_names as _lib_jellyfin_reorder_provider_names,
)
from bootstrap_lib.servarr import (
    choose_profile as _lib_choose_profile,
)
from bootstrap_lib.servarr import (
    choose_root_folder as _lib_choose_root_folder,
)
from bootstrap_lib.servarr import (
    find_existing_servarr as _lib_find_existing_servarr,
)
from bootstrap_lib.servarr import (
    normalize_remote_path_mappings as _lib_normalize_remote_path_mappings,
)
from bootstrap_services.api_keys_service import ApiKeysService
from bootstrap_services.arr_queue_cleanup_service import ArrQueueCleanupService
from bootstrap_services.arr_service import ArrService
from bootstrap_services.auth_service import AuthService
from bootstrap_services.bazarr_service import BazarrService
from bootstrap_services.bootstrap_runner_service import (
    BootstrapRunnerDependencies,
    BootstrapRunnerService,
)
from bootstrap_services.config_artifacts_service import ConfigArtifactsService
from bootstrap_services.discovery_lists_service import DiscoveryListsService
from bootstrap_services.disk_guardrails_service import DiskGuardrailsService
from bootstrap_services.enums import BootstrapMode
from bootstrap_services.health_service import HealthService
from bootstrap_services.jellyfin_home_rails_service import (
    JellyfinHomeRailsDependencies,
    JellyfinHomeRailsService,
)
from bootstrap_services.jellyfin_libraries_service import (
    JellyfinLibrariesDependencies,
    JellyfinLibrariesService,
)
from bootstrap_services.jellyfin_livetv_source_service import JellyfinLiveTvSourceService
from bootstrap_services.jellyfin_livetv_state_service import JellyfinLiveTvStateService
from bootstrap_services.jellyfin_playback_service import (
    JellyfinPlaybackDependencies,
    JellyfinPlaybackService,
)
from bootstrap_services.jellyfin_plugins_service import (
    JellyfinPluginsDependencies,
    JellyfinPluginsService,
)
from bootstrap_services.jellyfin_prewarm_service import (
    JellyfinPrewarmDependencies,
    JellyfinPrewarmService,
)
from bootstrap_services.jellyfin_service import JellyfinLiveTvDependencies, JellyfinService
from bootstrap_services.jellyseerr_service import JellyseerrService
from bootstrap_services.media_hygiene_ops_service import MediaHygieneOpsService
from bootstrap_services.media_hygiene_service import MediaHygieneService
from bootstrap_services.operation_wiring import (
    RunnerOperationHandlers,
    build_runner_operation_registry,
)
from bootstrap_services.prowlarr_service import ProwlarrService
from bootstrap_services.qbit_service import QBittorrentService
from bootstrap_services.runtime_factory_service import (
    BootstrapCliArgs,
    BootstrapRuntimeFactoryDependencies,
    BootstrapRuntimeFactoryService,
)
from bootstrap_services.runtime_helpers import (
    disk_usage_percent as _disk_usage_percent,
    fmt_bytes as _fmt_bytes,
    qbit_delete_torrents,
    qbit_list_completed_torrents,
    qbit_list_torrents,
    to_float as _to_float,
)
from bootstrap_services.sabnzbd_service import SabnzbdService
from bootstrap_services.servarr_adapters import AdapterDependencies
from bootstrap_services.servarr_pipeline_service import (
    ServarrPipelineService,
)
from bootstrap_services.servarr_policy_service import ServarrPolicyService


def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] {msg}", flush=True)


def _arr_service() -> ArrService:
    return ArrService(
        http_request=http_request,
        log=log,
        field_map=field_map,
        field_list=field_list,
        coerce_list=coerce_list,
        to_int=to_int,
        normalize_remote_path_mappings=normalize_remote_path_mappings,
    )


def _qbit_service() -> QBittorrentService:
    return QBittorrentService(
        log=log,
        normalize_url=normalize_url,
        bool_cfg=bool_cfg,
        to_int=to_int,
        coerce_list=coerce_list,
    )


def _prowlarr_service() -> ProwlarrService:
    return ProwlarrService(
        http_request=http_request,
        field_map=field_map,
        field_list=field_list,
        log=log,
    )


def _sabnzbd_service() -> SabnzbdService:
    return SabnzbdService(
        http_request=http_request,
        normalize_url=normalize_url,
        normalize_mapping_path=normalize_mapping_path,
        choose_category=choose_category,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        log=log,
    )


def _arr_queue_cleanup_service() -> ArrQueueCleanupService:
    return ArrQueueCleanupService(
        http_request=http_request,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        to_int=to_int,
        normalize_token=normalize_token,
        resolve_arr_overrides_by_app=resolve_arr_overrides_by_app,
        log=log,
    )


def _servarr_policy_service() -> ServarrPolicyService:
    return ServarrPolicyService(
        http_request=http_request,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        normalize_token=normalize_token,
        to_int=to_int,
        resolve_arr_quality_preferences=resolve_arr_quality_preferences,
        get_arr_quality_profile=get_arr_quality_profile,
        log=log,
    )


def _jellyfin_service() -> JellyfinService:
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
        load_state=load_jellyfin_livetv_state,
        resolve_tuner_type_id=resolve_jellyfin_tuner_type_id,
        normalize_enabled_tuner_ids=normalize_enabled_tuner_ids,
        delete_entity=delete_jellyfin_livetv_entity,
        trigger_refresh=trigger_jellyfin_livetv_refresh,
    )
    return JellyfinService(deps=deps)


def _jellyfin_livetv_source_service() -> JellyfinLiveTvSourceService:
    return JellyfinLiveTvSourceService(
        coerce_list=coerce_list,
        candidate_config_roots=_candidate_config_roots,
        resolve_path=resolve_path,
        log=log,
    )


def _jellyfin_livetv_state_service() -> JellyfinLiveTvStateService:
    return JellyfinLiveTvStateService(
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        candidate_config_roots=_candidate_config_roots,
        jellyfin_request=jellyfin_request,
        log=log,
    )


def _jellyfin_home_rails_service() -> JellyfinHomeRailsService:
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
    return JellyfinHomeRailsService(deps=deps)


def _jellyfin_libraries_service() -> JellyfinLibrariesService:
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
    return JellyfinLibrariesService(deps=deps)


def _jellyfin_plugins_service() -> JellyfinPluginsService:
    deps = JellyfinPluginsDependencies(
        log=log,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_api_key=resolve_jellyfin_api_key,
        jellyfin_request=jellyfin_request,
    )
    return JellyfinPluginsService(deps=deps)


def _jellyfin_playback_service() -> JellyfinPlaybackService:
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
    return JellyfinPlaybackService(deps=deps)


def _jellyfin_prewarm_service() -> JellyfinPrewarmService:
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
    return JellyfinPrewarmService(deps=deps)


def _health_service() -> HealthService:
    return HealthService(
        http_request=http_request,
        log=log,
    )


def _bazarr_service() -> BazarrService:
    return BazarrService(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        get_arr_app=get_arr_app,
        parse_service_url=parse_service_url,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        apply_scalar_updates=_lib_bazarr_apply_scalar_updates,
    )


def _jellyseerr_service() -> JellyseerrService:
    return JellyseerrService(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_jellyfin_api_key=resolve_jellyfin_api_key,
        parse_service_url=parse_service_url,
        to_int=to_int,
        coerce_list=coerce_list,
        choose_profile=choose_profile,
        choose_root_folder=choose_root_folder,
        normalize_base_path=normalize_base_path,
        find_existing_servarr=find_existing_servarr,
        read_json_file=read_json_file,
        get_arr_app=get_arr_app,
        detect_arr_api_base=detect_arr_api_base,
        get_arr_quality_profile=get_arr_quality_profile,
        get_arr_root_folder_path=get_arr_root_folder_path,
        get_sonarr_language_profile_id=get_sonarr_language_profile_id,
        read_jellyseerr_api_key=read_jellyseerr_api_key,
        http_request=http_request,
    )


def _media_hygiene_service() -> MediaHygieneService:
    return MediaHygieneService(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        detect_arr_api_base=detect_arr_api_base,
        ensure_arr_failed_queue_cleanup=ensure_arr_failed_queue_cleanup,
        run_filesystem_hygiene=run_filesystem_hygiene,
        run_qbit_ipfilter_refresh=run_qbit_ipfilter_refresh,
        run_qbit_queue_guardrails=run_qbit_queue_guardrails,
        run_qbit_duplicate_prune=run_qbit_duplicate_prune,
    )


def _media_hygiene_ops_service() -> MediaHygieneOpsService:
    return MediaHygieneOpsService(
        log=log,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        to_int=to_int,
        to_float=_to_float,
        normalize_token=normalize_token,
        normalize_url=normalize_url,
        qbit_login=qbit_login,
        qbit_list_completed_torrents=qbit_list_completed_torrents,
        qbit_list_torrents=qbit_list_torrents,
        qbit_delete_torrents=qbit_delete_torrents,
        qbit_set_preferences=qbit_set_preferences,
    )


def _disk_guardrails_service() -> DiskGuardrailsService:
    return DiskGuardrailsService(
        log=log,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        to_int=to_int,
        to_float=_to_float,
        normalize_url=normalize_url,
        disk_usage_percent=_disk_usage_percent,
        fmt_bytes=_fmt_bytes,
        qbit_login=qbit_login,
        qbit_list_completed_torrents=qbit_list_completed_torrents,
        qbit_delete_torrents=qbit_delete_torrents,
    )


def _auth_service() -> AuthService:
    return AuthService(
        http_request=http_request,
        log=log,
        bool_cfg=bool_cfg,
    )


def _discovery_service() -> DiscoveryListsService:
    return DiscoveryListsService(
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        log=log,
        http_request=http_request,
        resolve_env_placeholder=resolve_env_placeholder,
        field_map=field_map,
        field_list=field_list,
        to_int=to_int,
        normalize_token=normalize_token,
        resolve_arr_quality_preferences=resolve_arr_quality_preferences,
        get_arr_quality_profile=get_arr_quality_profile,
        pick_first_profile_id=pick_first_profile_id,
        env_truthy=env_truthy,
        trigger_arr_command=_health_service().trigger_arr_command,
    )


def _servarr_pipeline_service() -> ServarrPipelineService:
    adapter_deps = AdapterDependencies(
        bool_cfg=bool_cfg,
        log=log,
        ensure_readarr_metadata_source=ensure_readarr_metadata_source,
    )
    return ServarrPipelineService(
        log=log,
        normalize_url=normalize_url,
        detect_arr_api_base=detect_arr_api_base,
        ensure_app_auth_settings=ensure_app_auth_settings,
        ensure_arr_media_management=ensure_arr_media_management,
        ensure_root_folder=ensure_root_folder,
        ensure_arr_download_handling=ensure_arr_download_handling,
        ensure_arr_quality_upgrade_policy=ensure_arr_quality_upgrade_policy,
        ensure_prowlarr_application=ensure_prowlarr_application,
        ensure_arr_download_client=ensure_arr_download_client,
        ensure_arr_remote_path_mappings=ensure_arr_remote_path_mappings,
        ensure_arr_discovery_lists_for_app=ensure_arr_discovery_lists_for_app,
        trigger_arr_discovery_kickoff=trigger_arr_discovery_kickoff,
        trigger_health_check=trigger_health_check,
        adapter_deps=adapter_deps,
    )


def _config_artifacts_service() -> ConfigArtifactsService:
    return ConfigArtifactsService(
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_jellyfin_api_key=resolve_jellyfin_api_key,
        jellyfin_request=jellyfin_request,
        log=log,
        load_bootstrap_default_json=load_bootstrap_default_json,
        default_homepage_hosts=list(_lib_default_homepage_hosts),
        render_homepage_services_yaml=_lib_render_homepage_services_yaml,
    )


def _api_keys_service() -> ApiKeysService:
    return ApiKeysService(
        log=log,
        to_int=to_int,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
    )


def normalize_url(url):
    return _lib_normalize_url(url)


def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
    return _lib_http_request(
        base_url,
        path,
        api_key=api_key,
        method=method,
        payload=payload,
        timeout=timeout,
    )


def wait_for_service(name, base_url, path, timeout_seconds):
    interval = int(os.environ.get("BOOTSTRAP_WAIT_INTERVAL_SECONDS", "3"))
    heartbeat = int(os.environ.get("BOOTSTRAP_WAIT_HEARTBEAT_SECONDS", "15"))
    interval = max(1, interval)
    heartbeat = max(interval, heartbeat)

    deadline = time.time() + timeout_seconds
    start = time.time()
    next_heartbeat = start
    attempt = 0
    last_status = None
    last_error = None

    while time.time() < deadline:
        attempt += 1
        try:
            status, _, _ = http_request(base_url, path, timeout=10)
            last_status = status
            last_error = None
            if 200 <= status < 500:
                log(f"[OK] {name} reachable at {base_url}{path} (HTTP {status})")
                return
        except Exception as exc:
            last_error = str(exc)

        now = time.time()
        if now >= next_heartbeat:
            elapsed = int(now - start)
            remaining = int(max(0, deadline - now))
            status_fragment = (
                f"last HTTP {last_status}" if last_status is not None else "no HTTP response yet"
            )
            err_fragment = f"; last error: {last_error}" if last_error else ""
            log(
                f"[WAIT] {name} not ready yet at {base_url}{path} "
                f"(attempt={attempt}, elapsed={elapsed}s, remaining={remaining}s, "
                f"{status_fragment}{err_fragment})"
            )
            next_heartbeat = now + heartbeat

        time.sleep(interval)

    elapsed = int(time.time() - start)
    raise RuntimeError(
        f"Timed out waiting for {name} at {base_url}{path} after {elapsed}s "
        f"(attempts={attempt}, last_status={last_status}, last_error={last_error})"
    )


def _read_api_key_from_env(app_name):
    return _api_keys_service().read_api_key_from_env(app_name)


def _candidate_config_roots(config_root):
    return _api_keys_service().candidate_config_roots(config_root)


def read_api_key(config_root, app_name):
    return _api_keys_service().read_api_key(config_root, app_name)


def read_json_file(path):
    return _api_keys_service().read_json_file(path)


def read_jellyseerr_api_key(config_root, timeout_seconds=120):
    return _api_keys_service().read_jellyseerr_api_key(config_root, timeout_seconds=timeout_seconds)


def jellyfin_request(base_url, path, api_key, method="GET", payload=None, timeout=30):
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


def resolve_path(base_root, maybe_relative):
    p = Path(str(maybe_relative))
    if p.is_absolute():
        return p
    return Path(base_root) / p


def prepare_jellyfin_m3u_tuner_url(tuner, guides, config_root, guide_channel_ids_cache=None):
    return _jellyfin_livetv_source_service().prepare_m3u_tuner_url(
        tuner=tuner,
        guides=guides,
        config_root=config_root,
        guide_channel_ids_cache=guide_channel_ids_cache,
    )


def read_sabnzbd_api_key(config_root, sab_cfg):
    return _sabnzbd_service().read_api_key(config_root, sab_cfg)


def read_jellyfin_api_key_from_db(config_root, jellyfin_cfg):
    return _api_keys_service().read_jellyfin_api_key_from_db(config_root, jellyfin_cfg)


def resolve_jellyfin_api_key(jellyfin_cfg, config_root):
    return _api_keys_service().resolve_jellyfin_api_key(jellyfin_cfg, config_root)


def load_jellyfin_livetv_state(config_root, live_cfg):
    return _jellyfin_livetv_state_service().load_state(
        config_root=config_root,
        live_cfg=live_cfg,
    )


def resolve_jellyfin_tuner_type_id(jellyfin_url, jellyfin_api_key, requested_type):
    return _jellyfin_livetv_state_service().resolve_tuner_type_id(
        jellyfin_url=jellyfin_url,
        jellyfin_api_key=jellyfin_api_key,
        requested_type=requested_type,
    )


def normalize_enabled_tuner_ids(enabled_tuners, state):
    return _jellyfin_livetv_state_service().normalize_enabled_tuner_ids(
        enabled_tuners=enabled_tuners,
        state=state,
    )


def delete_jellyfin_livetv_entity(jellyfin_url, jellyfin_api_key, entity, entity_id):
    return _jellyfin_livetv_state_service().delete_entity(
        jellyfin_url=jellyfin_url,
        jellyfin_api_key=jellyfin_api_key,
        entity=entity,
        entity_id=entity_id,
    )


def trigger_jellyfin_scheduled_task(jellyfin_url, jellyfin_api_key, preferred_names):
    return _jellyfin_livetv_state_service().trigger_scheduled_task(
        jellyfin_url=jellyfin_url,
        jellyfin_api_key=jellyfin_api_key,
        preferred_names=preferred_names,
    )


def trigger_jellyfin_livetv_refresh(jellyfin_url, jellyfin_api_key, endpoint_path, label):
    return _jellyfin_livetv_state_service().trigger_refresh(
        jellyfin_url=jellyfin_url,
        jellyfin_api_key=jellyfin_api_key,
        endpoint_path=endpoint_path,
        label=label,
    )


def ensure_jellyfin_livetv(cfg, config_root, wait_timeout):
    _jellyfin_service().ensure_livetv(cfg, config_root, wait_timeout)


def ensure_jellyfin_libraries(cfg, config_root, wait_timeout):
    _jellyfin_libraries_service().ensure(cfg, config_root, wait_timeout)


def ensure_jellyfin_prewarm(cfg, config_root, wait_timeout):
    _jellyfin_prewarm_service().ensure(cfg, config_root, wait_timeout)


def jellyfin_build_query_path(path, params):
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


def jellyfin_items_from_payload(payload):
    if isinstance(payload, dict):
        items = payload.get("Items")
        return items if isinstance(items, list) else []
    if isinstance(payload, list):
        return payload
    return []


def normalize_item_ids(items):
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


def chunked(values, size):
    batch = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def resolve_jellyfin_user_id_value(section_cfg, jellyfin_url, jellyfin_api_key):
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


def ensure_jellyfin_playback_defaults(cfg, config_root, wait_timeout):
    _jellyfin_playback_service().ensure(cfg, config_root, wait_timeout)


def default_jellyfin_home_rails():
    return _jellyfin_home_rails_service().default_rails()


def find_jellyfin_collection_by_name(jellyfin_url, jellyfin_api_key, user_id, collection_name):
    return _jellyfin_home_rails_service().find_collection_by_name(
        jellyfin_url, jellyfin_api_key, user_id, collection_name
    )


def collection_item_ids(jellyfin_url, jellyfin_api_key, user_id, collection_id):
    return _jellyfin_home_rails_service().collection_item_ids(
        jellyfin_url, jellyfin_api_key, user_id, collection_id
    )


def update_collection_items(jellyfin_url, jellyfin_api_key, collection_id, to_add, to_remove):
    return _jellyfin_home_rails_service().update_collection_items(
        jellyfin_url, jellyfin_api_key, collection_id, to_add, to_remove
    )


def ensure_jellyfin_collection_membership(
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


def delete_jellyfin_collection_by_name(jellyfin_url, jellyfin_api_key, user_id, collection_name):
    return _jellyfin_home_rails_service().delete_collection_by_name(
        jellyfin_url, jellyfin_api_key, user_id, collection_name
    )


def run_jellyfin_rail_query(jellyfin_url, jellyfin_api_key, user_id, rail_cfg, max_items):
    return _jellyfin_home_rails_service().run_rail_query(
        jellyfin_url, jellyfin_api_key, user_id, rail_cfg, max_items
    )


def ensure_jellyfin_home_rails(cfg, config_root, wait_timeout):
    _jellyfin_home_rails_service().ensure_home_rails(
        cfg,
        config_root,
        wait_timeout,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_jellyfin_api_key=resolve_jellyfin_api_key,
    )


def normalize_plugin_name(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def ensure_jellyfin_plugins(cfg, config_root, wait_timeout):
    _jellyfin_plugins_service().ensure(cfg, config_root, wait_timeout)


def yaml_scalar(value):
    return _config_artifacts_service().yaml_scalar(value)


def render_yaml(value, indent=0):
    return _config_artifacts_service().render_yaml(value, indent=indent)


def ensure_homepage_services_config(cfg, config_root):
    return _config_artifacts_service().ensure_homepage_services_config(cfg, config_root)


def ensure_bazarr_arr_integration(cfg, config_root, arr_apps, app_keys, wait_timeout):
    return _bazarr_service().ensure_arr_integration(
        cfg=cfg,
        config_root=config_root,
        arr_apps=arr_apps,
        app_keys=app_keys,
        wait_timeout=wait_timeout,
    )


def detect_jellyfin_user_id(jellyfin_url, jellyfin_api_key, preferred_username):
    return _config_artifacts_service().detect_jellyfin_user_id(
        jellyfin_url, jellyfin_api_key, preferred_username
    )


def default_auto_collections_plugins():
    return _config_artifacts_service().default_auto_collections_plugins()


def ensure_jellyfin_auto_collections_config(cfg, config_root, wait_timeout):
    _config_artifacts_service().ensure_jellyfin_auto_collections_config(
        cfg=cfg,
        config_root=config_root,
        wait_timeout=wait_timeout,
        resolve_jellyfin_user_id_value_fn=resolve_jellyfin_user_id_value,
    )


def normalize_base_path(path_value):
    return _lib_normalize_base_path(path_value)


def parse_service_url(url, default_port):
    return _lib_parse_service_url(url, default_port)


def to_int(value, fallback=None):
    return _lib_to_int(value, fallback=fallback)


def coerce_list(value):
    return _lib_coerce_list(value)


def choose_profile(profiles, preferred_id=None, preferred_names=None):
    return _lib_choose_profile(
        profiles,
        preferred_id=preferred_id,
        preferred_names=preferred_names,
    )


def choose_root_folder(root_folders, preferred_path):
    return _lib_choose_root_folder(root_folders, preferred_path)


def find_existing_servarr(existing, name, hostname, port, base_url, is4k):
    return _lib_find_existing_servarr(existing, name, hostname, port, base_url, is4k)


def normalize_remote_path_mappings(mappings):
    return _lib_normalize_remote_path_mappings(mappings)


def get_arr_app(arr_apps, implementation):
    for app in arr_apps:
        if app.get("implementation") == implementation:
            return app
    return None


def normalize_token(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def resolve_env_placeholder(value):
    if isinstance(value, str):
        raw = value.strip()
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw)
        if match:
            return os.environ.get(match.group(1), "")
    return value


def resolve_arr_quality_preferences(cfg, app_cfg):
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


def bool_cfg(cfg, key, default):
    return _lib_bool_cfg(cfg, key, default)


def env_truthy(name, default=False):
    return _lib_env_truthy(name, default=default)


BOOTSTRAP_DEFAULTS_DIR = Path(__file__).resolve().parent / "bootstrap_defaults"


def load_bootstrap_default_json(filename, fallback):
    return _lib_load_json_default(
        BOOTSTRAP_DEFAULTS_DIR,
        filename,
        fallback,
        log=log,
    )


def field_map(field_list):
    out = {}
    for item in field_list or []:
        name = item.get("name")
        if not name:
            continue
        out[name] = item.get("value", "")
    return out


def field_list(mapping):
    return [{"name": key, "value": value} for key, value in mapping.items()]


def detect_arr_api_base(app_name, app_url, api_key):
    for version in ("v3", "v1"):
        status, _, _ = http_request(app_url, f"/api/{version}/system/status", api_key=api_key)
        if status == 200:
            api_base = f"/api/{version}"
            log(f"[OK] {app_name}: detected API base {api_base}")
            return api_base

    raise RuntimeError(f"{app_name}: unable to detect API base (tried /api/v3 and /api/v1)")


def pick_first_profile_id(app_name, app_url, api_base, api_key, endpoint, field_label):
    status, data, body = http_request(app_url, f"{api_base}/{endpoint}", api_key=api_key)
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(f"{app_name}: failed to list {field_label} (HTTP {status}): {body}")

    for item in data:
        profile_id = to_int(item.get("id"))
        if profile_id and profile_id > 0:
            return profile_id

    raise RuntimeError(f"{app_name}: no valid {field_label} id found")


def build_root_folder_payload(app_name, app_url, api_base, api_key, root_folder):
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
        except Exception:
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


def ensure_root_folder(app_name, app_url, api_base, api_key, root_folder):
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


def trigger_health_check(app_name, app_url, api_base, api_key):
    return _health_service().trigger_health_check(
        app_name,
        app_url,
        api_base,
        api_key,
    )


def trigger_arr_command(app_name, app_url, api_base, api_key, command_name, *, required=False):
    return _health_service().trigger_arr_command(
        app_name,
        app_url,
        api_base,
        api_key,
        command_name,
        required=required,
    )


def trigger_arr_discovery_kickoff(cfg, app_cfg, app_url, api_base, api_key):
    return _discovery_service().trigger_arr_discovery_kickoff(
        cfg,
        app_cfg,
        app_url,
        api_base,
        api_key,
    )


def fetch_arr_download_client_config(app_name, app_url, api_base, api_key):
    return _servarr_policy_service().fetch_download_client_config(
        app_name,
        app_url,
        api_base,
        api_key,
    )


def ensure_arr_download_handling(app_cfg, app_url, api_base, api_key, handling_cfg):
    return _servarr_policy_service().ensure_download_handling(
        app_cfg,
        app_url,
        api_base,
        api_key,
        handling_cfg,
    )


def resolve_arr_overrides_by_app(cfg_section, app_cfg):
    return _servarr_policy_service().resolve_overrides_by_app(cfg_section, app_cfg)


def ensure_arr_media_management(app_cfg, app_url, api_base, api_key, media_cfg):
    return _servarr_policy_service().ensure_media_management(
        app_cfg,
        app_url,
        api_base,
        api_key,
        media_cfg,
    )


def ensure_arr_quality_upgrade_policy(
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


def coerce_for_example(value, example):
    return DiscoveryListsService._coerce_for_example(value, example)


def resolve_import_list_definitions(arr_discovery_cfg, app_cfg):
    return _discovery_service().resolve_import_list_definitions(arr_discovery_cfg, app_cfg)


def build_arr_import_list_payload(
    app_cfg,
    schema,
    list_cfg,
    default_quality_profile_id,
    default_metadata_profile_id=None,
):
    return _discovery_service().build_arr_import_list_payload(
        app_cfg,
        schema,
        list_cfg,
        default_quality_profile_id,
        default_metadata_profile_id,
    )


def ensure_arr_discovery_lists_for_app(cfg, app_cfg, app_url, api_base, api_key):
    return _discovery_service().ensure_arr_discovery_lists_for_app(
        cfg,
        app_cfg,
        app_url,
        api_base,
        api_key,
    )


def ensure_readarr_metadata_source(cfg, app_cfg, app_url, api_base, api_key):
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


def auth_scope_matches(auth_cfg, app_name, implementation):
    return _auth_service().auth_scope_matches(auth_cfg, app_name, implementation)


def ensure_app_auth_settings(app_name, implementation, app_url, api_base, api_key, auth_cfg):
    return _auth_service().ensure_app_auth_settings(
        app_name,
        implementation,
        app_url,
        api_base,
        api_key,
        auth_cfg,
    )


def choose_category(app_cfg, client_cfg):
    return _arr_service().choose_category(app_cfg, client_cfg)


def normalize_mapping_path(path_value):
    return _arr_service().normalize_mapping_path(path_value)


def build_sab_remote_path_mappings(sab_cfg):
    return _arr_service().build_sab_remote_path_mappings(sab_cfg)


def ensure_arr_remote_path_mappings(app_cfg, app_url, api_base, api_key, mappings):
    _arr_service().ensure_arr_remote_path_mappings(app_cfg, app_url, api_base, api_key, mappings)


def ensure_arr_download_client(
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


def qbit_login(base_url, username, password):
    return _qbit_service().login(base_url, username, password)


def qbit_create_category(opener, base_url, category, save_path):
    return _qbit_service().create_category(opener, base_url, category, save_path)


def qbit_set_preferences(opener, base_url, preferences):
    return _qbit_service().set_preferences(opener, base_url, preferences)


def setup_qbit_storage_defaults(opener, qbit_url, qbit_cfg):
    return _qbit_service().setup_storage_defaults(
        opener,
        qbit_url,
        qbit_cfg,
        set_preferences_fn=qbit_set_preferences,
    )


def setup_qbit_categories(arr_apps, qbit_cfg, qb_username, qb_password):
    return _qbit_service().setup_categories(
        arr_apps,
        qbit_cfg,
        qb_username,
        qb_password,
        choose_category_fn=choose_category,
        setup_storage_defaults_fn=setup_qbit_storage_defaults,
        create_category_fn=qbit_create_category,
        login_fn=qbit_login,
    )


def run_qbit_queue_guardrails(qbit_cfg, qb_username, qb_password):
    return _media_hygiene_ops_service().run_qbit_queue_guardrails(
        qbit_cfg=qbit_cfg,
        qb_username=qb_username,
        qb_password=qb_password,
    )


def deep_merge_objects(base_obj, override_obj):
    return _config_artifacts_service().deep_merge_objects(base_obj, override_obj)


def ensure_maintainerr_policy(cfg, config_root):
    _config_artifacts_service().ensure_maintainerr_policy(cfg, config_root)


def arr_queue_records(payload):
    return _arr_queue_cleanup_service().arr_queue_records(payload)


def queue_item_is_failed(item, failed_tokens):
    return _arr_queue_cleanup_service().queue_item_is_failed(item, failed_tokens)


def delete_queue_item(app_name, app_url, api_base, api_key, item_id, remove_from_client, blocklist):
    return _arr_queue_cleanup_service().delete_queue_item(
        app_name=app_name,
        app_url=app_url,
        api_base=api_base,
        api_key=api_key,
        item_id=item_id,
        remove_from_client=remove_from_client,
        blocklist=blocklist,
    )


def ensure_arr_failed_queue_cleanup(app_cfg, app_url, api_base, api_key, hygiene_cfg):
    return _arr_queue_cleanup_service().ensure_arr_failed_queue_cleanup(
        app_cfg=app_cfg,
        app_url=app_url,
        api_base=api_base,
        api_key=api_key,
        hygiene_cfg=hygiene_cfg,
    )


def _walk_existing_files(paths):
    yield from _media_hygiene_ops_service()._walk_existing_files(paths)


def run_filesystem_hygiene(hygiene_cfg):
    return _media_hygiene_ops_service().run_filesystem_hygiene(hygiene_cfg)


def run_qbit_duplicate_prune(hygiene_cfg, qbit_cfg, qb_username, qb_password):
    return _media_hygiene_ops_service().run_qbit_duplicate_prune(
        hygiene_cfg=hygiene_cfg,
        qbit_cfg=qbit_cfg,
        qb_username=qb_username,
        qb_password=qb_password,
    )


def run_qbit_ipfilter_refresh(hygiene_cfg, qbit_cfg, qb_username, qb_password):
    return _media_hygiene_ops_service().run_qbit_ipfilter_refresh(
        hygiene_cfg=hygiene_cfg,
        qbit_cfg=qbit_cfg,
        qb_username=qb_username,
        qb_password=qb_password,
    )


def run_media_hygiene(
    cfg, config_root, arr_apps, app_keys, qbit_cfg=None, qb_username="", qb_password=""
):
    del config_root  # kept for backward-compatible signature
    return _media_hygiene_service().run(
        cfg=cfg,
        arr_apps=arr_apps,
        app_keys=app_keys,
        qbit_cfg=qbit_cfg,
        qb_username=qb_username,
        qb_password=qb_password,
    )


def enforce_disk_guardrails(cfg, config_root, qbit_cfg, qb_username, qb_password):
    return _disk_guardrails_service().enforce(
        cfg=cfg,
        config_root=config_root,
        qbit_cfg=qbit_cfg,
        qb_username=qb_username,
        qb_password=qb_password,
    )


def sabnzbd_request(base_url, api_key, params, timeout=20):
    return _sabnzbd_service().request(
        base_url=base_url,
        api_key=api_key,
        params=params,
        timeout=timeout,
    )


def sabnzbd_get_config_section(base_url, sab_api_key, section):
    return _sabnzbd_service().get_config_section(
        base_url=base_url,
        sab_api_key=sab_api_key,
        section=section,
    )


def ensure_sabnzbd_defaults(sab_cfg, sab_api_key):
    _sabnzbd_service().ensure_defaults(sab_cfg=sab_cfg, sab_api_key=sab_api_key)


def ensure_sabnzbd_categories(arr_apps, sab_cfg, sab_api_key):
    _sabnzbd_service().ensure_categories(
        arr_apps=arr_apps,
        sab_cfg=sab_cfg,
        sab_api_key=sab_api_key,
    )


def resolve_schema_contract(prowlarr_url, prowlarr_key, implementation):
    return _prowlarr_service().resolve_schema_contract(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        implementation=implementation,
    )


def find_existing_application(prowlarr_url, prowlarr_key, implementation, base_url):
    return _prowlarr_service().find_existing_application(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        implementation=implementation,
        base_url=base_url,
    )


def ensure_prowlarr_application(
    prowlarr_url, prowlarr_key, app_name, implementation, app_url, app_key
):
    _prowlarr_service().ensure_application(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        app_name=app_name,
        implementation=implementation,
        app_url=app_url,
        app_key=app_key,
    )


def trigger_prowlarr_sync(prowlarr_url, prowlarr_key):
    _prowlarr_service().trigger_sync(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
    )


def ensure_prowlarr_indexer(prowlarr_url, prowlarr_key, indexer_cfg):
    _prowlarr_service().ensure_indexer(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        indexer_cfg=indexer_cfg,
    )


def build_indexer_payload(template):
    return _prowlarr_service().build_indexer_payload(template)


def auto_add_tested_indexers(prowlarr_url, prowlarr_key):
    _prowlarr_service().auto_add_tested_indexers(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
    )


def get_arr_quality_profile(
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


def get_arr_root_folder_path(app_name, app_url, api_base, api_key, preferred_root):
    status, root_folders, body = http_request(app_url, f"{api_base}/rootfolder", api_key=api_key)
    if status != 200 or not isinstance(root_folders, list):
        raise RuntimeError(f"{app_name}: failed to list root folders (HTTP {status}): {body}")
    chosen = choose_root_folder(root_folders, preferred_root)
    if chosen:
        return chosen
    preferred = str(preferred_root or "").rstrip("/")
    if preferred:
        return preferred
    raise RuntimeError(f"{app_name}: no root folder could be resolved.")


def get_sonarr_language_profile_id(sonarr_url, sonarr_api_base, sonarr_api_key):
    status, language_profiles, _ = http_request(
        sonarr_url, f"{sonarr_api_base}/languageprofile", api_key=sonarr_api_key
    )
    if status == 200 and isinstance(language_profiles, list) and language_profiles:
        return to_int(language_profiles[0].get("id"), 1)
    return 1


def configure_jellyseerr(cfg, arr_apps, app_keys, config_root, wait_timeout):
    return _jellyseerr_service().configure(
        cfg=cfg,
        arr_apps=arr_apps,
        app_keys=app_keys,
        config_root=config_root,
        wait_timeout=wait_timeout,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Idempotent bootstrap for Arr + Prowlarr + Jellyseerr integration."
    )
    parser.add_argument(
        "--config", default="/bootstrap/config.json", help="Bootstrap config JSON path"
    )
    parser.add_argument(
        "--config-root",
        default="/srv-config",
        help="Root path containing app config folders",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=600,
        help="Service readiness timeout (seconds)",
    )
    parser.add_argument(
        "--auto-prowlarr-indexers",
        action="store_true",
        help="Iterate indexer templates/presets and add any that pass connection test",
    )
    parser.add_argument(
        "--mode",
        default=BootstrapMode.FULL.value,
        choices=BootstrapMode.choices(),
        help=(
            "Execution mode: full bootstrap, media-server prewarm-only, "
            "media-server home-rails-only, or media-hygiene-only "
            "(legacy jellyfin-* aliases still supported)"
        ),
    )
    args = parser.parse_args()

    runtime_factory = BootstrapRuntimeFactoryService(
        deps=BootstrapRuntimeFactoryDependencies(
            load_bootstrap_default_json=load_bootstrap_default_json,
            deep_merge_objects=deep_merge_objects,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            env_truthy=env_truthy,
            read_api_key=read_api_key,
            build_sab_remote_path_mappings=build_sab_remote_path_mappings,
        )
    )
    build_result = runtime_factory.build_from_cli(
        BootstrapCliArgs(
            mode=BootstrapMode.from_cli(args.mode),
            config_path=args.config,
            config_root=args.config_root,
            wait_timeout=args.wait_timeout,
            auto_prowlarr_indexers=args.auto_prowlarr_indexers,
        )
    )
    runtime = build_result.runtime
    log(f"[INFO] Bootstrap plan: {build_result.plan.to_log_line()}")
    runner_operations = build_runner_operation_registry(
        RunnerOperationHandlers(
            ensure_app_auth_settings=ensure_app_auth_settings,
            qbit_login=qbit_login,
            read_sabnzbd_api_key=read_sabnzbd_api_key,
            ensure_sabnzbd_defaults=ensure_sabnzbd_defaults,
            ensure_sabnzbd_categories=ensure_sabnzbd_categories,
            setup_qbit_categories=setup_qbit_categories,
            run_servarr_pipeline=_servarr_pipeline_service().run,
            ensure_bazarr_arr_integration=ensure_bazarr_arr_integration,
            configure_jellyseerr=configure_jellyseerr,
            ensure_jellyfin_livetv=ensure_jellyfin_livetv,
            ensure_jellyfin_libraries=ensure_jellyfin_libraries,
            ensure_jellyfin_plugins=ensure_jellyfin_plugins,
            ensure_jellyfin_playback_defaults=ensure_jellyfin_playback_defaults,
            ensure_jellyfin_home_rails=ensure_jellyfin_home_rails,
            ensure_jellyfin_auto_collections_config=ensure_jellyfin_auto_collections_config,
            enforce_disk_guardrails=enforce_disk_guardrails,
            run_media_hygiene=run_media_hygiene,
            ensure_jellyfin_prewarm=ensure_jellyfin_prewarm,
            ensure_maintainerr_policy=ensure_maintainerr_policy,
            ensure_homepage_services_config=ensure_homepage_services_config,
            ensure_prowlarr_indexer=ensure_prowlarr_indexer,
            auto_add_tested_indexers=auto_add_tested_indexers,
            trigger_prowlarr_sync=trigger_prowlarr_sync,
        ),
        operation_handler_specs=(runtime.adapter_hooks_cfg or {}).get("operation_handlers"),
    )

    runner = BootstrapRunnerService(
        deps=BootstrapRunnerDependencies(
            log=log,
            bool_cfg=bool_cfg,
            normalize_url=normalize_url,
            wait_for_service=wait_for_service,
            detect_arr_api_base=detect_arr_api_base,
            operations=runner_operations,
        )
    )
    runner.run(runtime)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"[ERR] {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                log(f"[TRACE] {line}")
        sys.exit(1)
