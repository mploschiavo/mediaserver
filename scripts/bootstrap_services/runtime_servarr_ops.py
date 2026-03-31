#!/usr/bin/env python3
"""Servarr/download-client/runtime hygiene operations."""

import bootstrap_services.runtime_core as _core
from bootstrap_services.arr_indexer_sync_service import ArrIndexerSyncService
from bootstrap_services.runtime_core import *  # noqa: F401,F403

_disk_usage_percent = _core._disk_usage_percent
_fmt_bytes = _core._fmt_bytes
_to_float = _core._to_float

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

def _qbit_service(cfg=None) -> QBittorrentService:
    service_cls = resolve_app_service_class(cfg, "qbittorrent_service", QBittorrentService)
    return service_cls(
        log=log,
        normalize_url=normalize_url,
        bool_cfg=bool_cfg,
        to_int=to_int,
        coerce_list=coerce_list,
    )

def _prowlarr_service(cfg=None) -> ProwlarrService:
    service_cls = resolve_app_service_class(cfg, "prowlarr_service", ProwlarrService)
    return service_cls(
        http_request=http_request,
        field_map=field_map,
        field_list=field_list,
        log=log,
    )

def _arr_indexer_sync_service() -> ArrIndexerSyncService:
    return ArrIndexerSyncService(
        http_request=http_request,
        detect_arr_api_base=detect_arr_api_base,
        log=log,
    )

def _sabnzbd_service(cfg=None) -> SabnzbdService:
    service_cls = resolve_app_service_class(cfg, "sabnzbd_service", SabnzbdService)
    return service_cls(
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

def _health_service() -> HealthService:
    return HealthService(
        http_request=http_request,
        log=log,
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

def _auth_service(cfg=None) -> AuthService:
    service_cls = resolve_app_service_class(cfg, "auth_service", AuthService)
    return service_cls(
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

def read_sabnzbd_api_key(config_root, sab_cfg):
    return _sabnzbd_service().read_api_key(config_root, sab_cfg)

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
    return _qbit_service(qbit_cfg if isinstance(qbit_cfg, dict) else None).setup_storage_defaults(
        opener,
        qbit_url,
        qbit_cfg,
        set_preferences_fn=qbit_set_preferences,
    )

def setup_qbit_categories(arr_apps, qbit_cfg, qb_username, qb_password):
    return _qbit_service(qbit_cfg if isinstance(qbit_cfg, dict) else None).setup_categories(
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
    _sabnzbd_service(sab_cfg if isinstance(sab_cfg, dict) else None).ensure_defaults(
        sab_cfg=sab_cfg, sab_api_key=sab_api_key
    )

def ensure_sabnzbd_categories(arr_apps, sab_cfg, sab_api_key):
    _sabnzbd_service(sab_cfg if isinstance(sab_cfg, dict) else None).ensure_categories(
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
    _prowlarr_service(indexer_cfg if isinstance(indexer_cfg, dict) else None).ensure_indexer(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        indexer_cfg=indexer_cfg,
    )

def build_indexer_payload(template):
    return _prowlarr_service(template if isinstance(template, dict) else None).build_indexer_payload(
        template
    )

def auto_add_tested_indexers(
    prowlarr_url,
    prowlarr_key,
    exclude_name_tokens=None,
    reputation_cfg=None,
):
    _prowlarr_service().auto_add_tested_indexers(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        exclude_name_tokens=exclude_name_tokens,
        reputation_cfg=reputation_cfg,
    )

def sync_arr_indexers_from_prowlarr(
    prowlarr_url,
    prowlarr_key,
    arr_apps,
    app_keys,
    prune_stale=True,
):
    return _arr_indexer_sync_service().reconcile(
        prowlarr_url=prowlarr_url,
        prowlarr_key=prowlarr_key,
        arr_apps=arr_apps,
        app_keys=app_keys,
        prune_stale=bool(prune_stale),
    )
