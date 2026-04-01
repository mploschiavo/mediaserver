#!/usr/bin/env python3
"""Media-server and UI-facing runtime operations (Jellyfin/Bazarr/Jellyseerr/Homepage)."""

import bootstrap_services.apps.jellyfin.runtime_ops as jellyfin_runtime_ops
import bootstrap_services.runtime_core as _core
import bootstrap_services.runtime_servarr.arr_ops as _servarr_arr_ops

log = _core.log
bool_cfg = _core.bool_cfg
coerce_list = _core.coerce_list
to_int = _core.to_int
normalize_url = _core.normalize_url
wait_for_service = _core.wait_for_service
get_arr_app = _core.get_arr_app
parse_service_url = _core.parse_service_url
resolve_path = _core.resolve_path
read_json_file = _core.read_json_file
read_api_key = _core.read_api_key
load_bootstrap_default_json = _core.load_bootstrap_default_json
choose_profile = _core.choose_profile
choose_root_folder = _core.choose_root_folder
normalize_base_path = _core.normalize_base_path
find_existing_servarr = _core.find_existing_servarr
get_arr_quality_profile = _core.get_arr_quality_profile
read_jellyseerr_api_key = _core.read_jellyseerr_api_key
http_request = _core.http_request
resolve_app_service_class = _core.resolve_app_service_class
BazarrService = _core.BazarrService
JellyseerrService = _core.JellyseerrService
ConfigArtifactsService = _core.ConfigArtifactsService
MaintainerrService = _core.MaintainerrService

_api_keys_service = _core._api_keys_service
_candidate_config_roots = _core._candidate_config_roots
_lib_bazarr_apply_scalar_updates = _core._lib_bazarr_apply_scalar_updates
_lib_default_homepage_hosts = _core._lib_default_homepage_hosts
_lib_render_homepage_services_yaml = _core._lib_render_homepage_services_yaml
detect_arr_api_base = _servarr_arr_ops.detect_arr_api_base


def _bazarr_service(cfg=None) -> BazarrService:
    service_cls = resolve_app_service_class("bazarr_service", BazarrService)
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
    bindings = (cfg or {}).get("technology_bindings") if isinstance(cfg, dict) else {}
    request_manager = ""
    if isinstance(bindings, dict):
        request_manager = str(bindings.get("request_manager") or "").strip().lower()
    if not request_manager:
        request_manager = "jellyseerr"
    service_cls = resolve_app_service_class(
        "request_manager_service",
        JellyseerrService,
        technology=request_manager,
    )
    return service_cls(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_jellyfin_api_key=jellyfin_runtime_ops.resolve_jellyfin_api_key,
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
    service_cls = resolve_app_service_class("config_artifacts_service", ConfigArtifactsService)
    return service_cls(
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        resolve_jellyfin_api_key=jellyfin_runtime_ops.resolve_jellyfin_api_key,
        jellyfin_request=jellyfin_runtime_ops.jellyfin_request,
        log=log,
        load_bootstrap_default_json=load_bootstrap_default_json,
        default_homepage_hosts=list(_lib_default_homepage_hosts),
        render_homepage_services_yaml=_lib_render_homepage_services_yaml,
    )


def _maintainerr_service(cfg=None) -> MaintainerrService:
    service_cls = resolve_app_service_class("maintainerr_service", MaintainerrService)
    return service_cls(
        log=log,
        bool_cfg=bool_cfg,
        normalize_url=normalize_url,
        wait_for_service=wait_for_service,
        http_request=http_request,
        read_api_key=read_api_key,
        read_jellyseerr_api_key=read_jellyseerr_api_key,
        get_arr_app=get_arr_app,
        resolve_path=resolve_path,
    )


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


def default_auto_collections_plugins():
    return _config_artifacts_service().default_auto_collections_plugins()


def deep_merge_objects(base_obj, override_obj):
    return _config_artifacts_service().deep_merge_objects(base_obj, override_obj)


def ensure_maintainerr_policy(cfg, config_root):
    _config_artifacts_service(cfg).ensure_maintainerr_policy(cfg, config_root)


def ensure_maintainerr_integrations(cfg, config_root, arr_apps, wait_timeout):
    _maintainerr_service(cfg).ensure_integrations(
        cfg=cfg,
        config_root=config_root,
        arr_apps=arr_apps,
        wait_timeout=wait_timeout,
    )


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
