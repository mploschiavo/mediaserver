#!/usr/bin/env python3
"""Media-server and UI-facing runtime operations (Jellyfin/Bazarr/Jellyseerr/Homepage)."""

import bootstrap_services.runtime_core as _core
import bootstrap_services.runtime_servarr_ops as _servarr_ops
from bootstrap_services.runtime_core import *  # noqa: F401,F403

_api_keys_service = _core._api_keys_service
_candidate_config_roots = _core._candidate_config_roots
_lib_bazarr_apply_scalar_updates = _core._lib_bazarr_apply_scalar_updates
_lib_default_homepage_hosts = _core._lib_default_homepage_hosts
_lib_jellyfin_apply_artwork_profile = _core._lib_jellyfin_apply_artwork_profile
_lib_jellyfin_reorder_provider_names = _core._lib_jellyfin_reorder_provider_names
_lib_render_homepage_services_yaml = _core._lib_render_homepage_services_yaml
detect_arr_api_base = _servarr_ops.detect_arr_api_base

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
    service_cls = resolve_app_service_class(cfg, "jellyfin_livetv_service", JellyfinService)
    return service_cls(deps=deps)

def _jellyfin_livetv_source_service(cfg=None) -> JellyfinLiveTvSourceService:
    service_cls = resolve_app_service_class(
        cfg, "jellyfin_livetv_source_service", JellyfinLiveTvSourceService
    )
    return service_cls(
        coerce_list=coerce_list,
        candidate_config_roots=_candidate_config_roots,
        resolve_path=resolve_path,
        log=log,
    )

def _jellyfin_livetv_state_service(cfg=None) -> JellyfinLiveTvStateService:
    service_cls = resolve_app_service_class(
        cfg, "jellyfin_livetv_state_service", JellyfinLiveTvStateService
    )
    return service_cls(
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        candidate_config_roots=_candidate_config_roots,
        jellyfin_request=jellyfin_request,
        log=log,
    )

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
    service_cls = resolve_app_service_class(cfg, "jellyfin_home_rails_service", JellyfinHomeRailsService)
    return service_cls(deps=deps)

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
    service_cls = resolve_app_service_class(cfg, "jellyfin_libraries_service", JellyfinLibrariesService)
    return service_cls(deps=deps)

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
    service_cls = resolve_app_service_class(cfg, "jellyfin_plugins_service", JellyfinPluginsService)
    return service_cls(deps=deps)

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
    service_cls = resolve_app_service_class(cfg, "jellyfin_playback_service", JellyfinPlaybackService)
    return service_cls(deps=deps)

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
    service_cls = resolve_app_service_class(cfg, "jellyfin_prewarm_service", JellyfinPrewarmService)
    return service_cls(deps=deps)

def _bazarr_service(cfg=None) -> BazarrService:
    service_cls = resolve_app_service_class(cfg, "bazarr_service", BazarrService)
    return service_cls(
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

def _jellyseerr_service(cfg=None) -> JellyseerrService:
    service_cls = resolve_app_service_class(cfg, "jellyseerr_service", JellyseerrService)
    return service_cls(
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

def _config_artifacts_service(cfg=None) -> ConfigArtifactsService:
    service_cls = resolve_app_service_class(cfg, "config_artifacts_service", ConfigArtifactsService)
    return service_cls(
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

def prepare_jellyfin_m3u_tuner_url(tuner, guides, config_root, guide_channel_ids_cache=None):
    return _jellyfin_livetv_source_service().prepare_m3u_tuner_url(
        tuner=tuner,
        guides=guides,
        config_root=config_root,
        guide_channel_ids_cache=guide_channel_ids_cache,
    )

def prepare_jellyfin_xmltv_guide_path(guide, tuners, config_root):
    return _jellyfin_livetv_source_service().prepare_xmltv_guide_path(
        guide=guide,
        tuners=tuners,
        config_root=config_root,
    )

def read_jellyfin_api_key_from_db(config_root, jellyfin_cfg):
    return _api_keys_service().read_jellyfin_api_key_from_db(config_root, jellyfin_cfg)

def resolve_jellyfin_api_key(jellyfin_cfg, config_root):
    return _api_keys_service().resolve_jellyfin_api_key(jellyfin_cfg, config_root)

def load_jellyfin_livetv_state(config_root, live_cfg):
    return _jellyfin_livetv_state_service(live_cfg if isinstance(live_cfg, dict) else None).load_state(
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
    _jellyfin_service(cfg).ensure_livetv(cfg, config_root, wait_timeout)

def ensure_jellyfin_libraries(cfg, config_root, wait_timeout):
    _jellyfin_libraries_service(cfg).ensure(cfg, config_root, wait_timeout)

def ensure_jellyfin_prewarm(cfg, config_root, wait_timeout):
    _jellyfin_prewarm_service(cfg).ensure(cfg, config_root, wait_timeout)

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
    _jellyfin_playback_service(cfg).ensure(cfg, config_root, wait_timeout)

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
    _jellyfin_home_rails_service(cfg).ensure_home_rails(
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
    _jellyfin_plugins_service(cfg).ensure(cfg, config_root, wait_timeout)

def yaml_scalar(value):
    return _config_artifacts_service().yaml_scalar(value)

def render_yaml(value, indent=0):
    return _config_artifacts_service().render_yaml(value, indent=indent)

def ensure_homepage_services_config(cfg, config_root):
    return _config_artifacts_service(cfg).ensure_homepage_services_config(cfg, config_root)

def ensure_bazarr_arr_integration(cfg, config_root, arr_apps, app_keys, wait_timeout):
    return _bazarr_service(cfg).ensure_arr_integration(
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
    _config_artifacts_service(cfg).ensure_jellyfin_auto_collections_config(
        cfg=cfg,
        config_root=config_root,
        wait_timeout=wait_timeout,
        resolve_jellyfin_user_id_value_fn=resolve_jellyfin_user_id_value,
    )

def deep_merge_objects(base_obj, override_obj):
    return _config_artifacts_service().deep_merge_objects(base_obj, override_obj)

def ensure_maintainerr_policy(cfg, config_root):
    _config_artifacts_service(cfg).ensure_maintainerr_policy(cfg, config_root)

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
    return _jellyseerr_service(cfg).configure(
        cfg=cfg,
        arr_apps=arr_apps,
        app_keys=app_keys,
        config_root=config_root,
        wait_timeout=wait_timeout,
    )
