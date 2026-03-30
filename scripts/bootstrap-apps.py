#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import parse, request

from bootstrap_lib.bazarr import apply_scalar_updates as _lib_bazarr_apply_scalar_updates
from bootstrap_lib.common import (
    bool_cfg as _lib_bool_cfg,
)
from bootstrap_lib.defaults import load_json_default as _lib_load_json_default
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
from bootstrap_services.arr_service import ArrService
from bootstrap_services.arr_queue_cleanup_service import ArrQueueCleanupService
from bootstrap_services.auth_service import AuthService
from bootstrap_services.bazarr_service import BazarrService
from bootstrap_services.config_models import ArrDiscoveryListsConfig
from bootstrap_services.discovery_lists_service import DiscoveryListsService
from bootstrap_services.health_service import HealthService
from bootstrap_services.jellyfin_service import JellyfinLiveTvDependencies, JellyfinService
from bootstrap_services.jellyfin_home_rails_service import (
    JellyfinHomeRailsDependencies,
    JellyfinHomeRailsService,
)
from bootstrap_services.jellyseerr_service import JellyseerrService
from bootstrap_services.media_hygiene_ops_service import MediaHygieneOpsService
from bootstrap_services.media_hygiene_service import MediaHygieneService
from bootstrap_services.prowlarr_service import ProwlarrService
from bootstrap_services.qbit_service import QBittorrentService
from bootstrap_services.sabnzbd_service import SabnzbdService
from bootstrap_services.servarr_adapters import AdapterDependencies
from bootstrap_services.servarr_pipeline_service import (
    ClientAuth,
    ServarrPipelineInputs,
    ServarrPipelineService,
    ServarrRunConfig,
)


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
        qbit_delete_torrents=qbit_delete_torrents,
        qbit_set_preferences=qbit_set_preferences,
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
    app_token = str(app_name or "").strip().upper()
    if not app_token:
        return ""

    candidates = [f"{app_token}_API_KEY", f"UNPACKERR_{app_token}_API_KEY"]
    for env_name in candidates:
        value = (os.environ.get(env_name) or "").strip()
        if not value:
            continue
        if value.lower() == "replace-after-first-boot":
            continue
        log(f"[OK] {app_name}: using API key from env {env_name}")
        return value
    return ""


def _candidate_config_roots(config_root):
    roots = [Path(str(config_root))]
    alt_root = (os.environ.get("BOOTSTRAP_ALT_CONFIG_ROOT") or "").strip()
    if alt_root:
        alt_path = Path(alt_root)
        if alt_path not in roots:
            roots.append(alt_path)
    return roots


def read_api_key(config_root, app_name):
    env_value = _read_api_key_from_env(app_name)
    if env_value:
        return env_value

    timeout_seconds = max(
        5, to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_TIMEOUT_SECONDS"), 180) or 180
    )
    heartbeat_seconds = max(
        5, to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_HEARTBEAT_SECONDS"), 15) or 15
    )
    interval_seconds = max(
        1, to_int(os.environ.get("BOOTSTRAP_APIKEY_FILE_INTERVAL_SECONDS"), 2) or 2
    )

    xml_paths = [root / app_name / "config.xml" for root in _candidate_config_roots(config_root)]
    start = time.time()
    next_heartbeat = start
    last_error = ""

    while True:
        missing_paths = []
        for xml_path in xml_paths:
            if xml_path.exists():
                try:
                    text = xml_path.read_text(encoding="utf-8", errors="replace")
                    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
                    if match and match.group(1).strip():
                        return match.group(1).strip()
                    last_error = f"ApiKey not found in {xml_path}"
                except Exception as exc:
                    last_error = f"{xml_path}: {exc}"
            else:
                missing_paths.append(str(xml_path))
        if missing_paths and not last_error:
            last_error = f"Missing config file(s): {', '.join(missing_paths)}"

        now = time.time()
        elapsed = int(now - start)
        if elapsed >= timeout_seconds:
            raise RuntimeError(
                f"Unable to read API key for {app_name} after {elapsed}s "
                f"(last_error={last_error})."
            )

        if now >= next_heartbeat:
            log(
                f"[WAIT] {app_name}: waiting for API key material "
                f"(paths={', '.join(str(p) for p in xml_paths)}, "
                f"elapsed={elapsed}s, timeout={timeout_seconds}s, "
                f"last_error={last_error})"
            )
            next_heartbeat = now + heartbeat_seconds

        time.sleep(interval_seconds)


def read_json_file(path):
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"Missing file: {p}")
    return json.loads(p.read_text(encoding="utf-8", errors="replace"))


def read_jellyseerr_api_key(config_root, timeout_seconds=120):
    settings_paths = [
        root / "jellyseerr" / "settings.json" for root in _candidate_config_roots(config_root)
    ]
    start = time.time()
    next_heartbeat = start
    interval = 2

    while True:
        for settings_path in settings_paths:
            if not settings_path.exists():
                continue
            try:
                data = read_json_file(settings_path)
                api_key = ((data.get("main") or {}).get("apiKey") or "").strip()
                if api_key:
                    return api_key
            except Exception:
                # File can exist but still be in the middle of being written on first boot.
                pass

        now = time.time()
        elapsed = int(now - start)
        if elapsed >= int(timeout_seconds):
            raise RuntimeError(
                "Jellyseerr API key not found after "
                f"{elapsed}s (paths={', '.join(str(p) for p in settings_paths)})"
            )

        if now >= next_heartbeat:
            log(
                "[WAIT] Jellyseerr: waiting for api key in "
                f"{', '.join(str(p) for p in settings_paths)} "
                f"(elapsed={elapsed}s, timeout={timeout_seconds}s)"
            )
            next_heartbeat = now + 15

        time.sleep(interval)


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


def _read_text_from_source(source, config_root, timeout_seconds=60):
    src = str(source or "").strip()
    if not src:
        return ""

    if src.lower().startswith("http://") or src.lower().startswith("https://"):
        with request.urlopen(src, timeout=timeout_seconds) as resp:
            payload = resp.read()
        return payload.decode("utf-8", errors="replace")

    candidate_paths = []
    src_path = Path(src)
    if src_path.is_absolute():
        candidate_paths.append(src_path)
        if src.startswith("/config/"):
            config_relative = src[len("/config/") :].lstrip("/")
            for root in _candidate_config_roots(config_root):
                candidate_paths.append(root / "jellyfin" / config_relative)
    else:
        for root in _candidate_config_roots(config_root):
            candidate_paths.append(resolve_path(root, src))

    seen = set()
    for path in candidate_paths:
        path_key = str(path)
        if path_key in seen:
            continue
        seen.add(path_key)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")

    raise RuntimeError(f"Unable to read source data from {src}")


def _extract_xmltv_channel_ids(xml_text):
    if not xml_text:
        return set()
    return {match for match in re.findall(r"<channel id=\"([^\"]+)\"", xml_text) if match}


def _rewrite_extinf_tvg_id(extinf_line, new_id):
    pattern = r"tvg-id=\"[^\"]*\""
    replacement = f'tvg-id="{new_id}"'
    if re.search(pattern, extinf_line):
        return re.sub(pattern, replacement, extinf_line, count=1)
    return extinf_line


def _transform_m3u_for_guide(m3u_text, normalize_tvg_id_suffix=False, guide_channel_ids=None):
    lines = m3u_text.splitlines()
    output = []
    pending_extinf = None
    pending_meta = []
    total_entries = 0
    kept_entries = 0
    dropped_entries = 0
    normalized_ids = 0

    for line in lines:
        raw = str(line).rstrip("\r\n")
        stripped = raw.strip()
        if not stripped:
            continue

        if pending_extinf is None and stripped.startswith("#EXTM3U"):
            if not output:
                output.append(stripped)
            continue

        if stripped.startswith("#EXTINF"):
            pending_extinf = stripped
            pending_meta = []
            continue

        if pending_extinf is not None and stripped.startswith("#"):
            pending_meta.append(stripped)
            continue

        if pending_extinf is None:
            if stripped.startswith("#") and output:
                output.append(stripped)
            continue

        # Current line is the stream URL for the pending EXTINF.
        total_entries += 1
        extinf = pending_extinf
        pending_extinf = None
        tvg_id_match = re.search(r"tvg-id=\"([^\"]*)\"", extinf)
        tvg_id = str((tvg_id_match.group(1) if tvg_id_match else "") or "").strip()
        effective_tvg_id = tvg_id

        if normalize_tvg_id_suffix and tvg_id:
            stripped_id = tvg_id.split("@", 1)[0].strip()
            if stripped_id and stripped_id != tvg_id:
                effective_tvg_id = stripped_id
                normalized_ids += 1

        if guide_channel_ids is not None:
            if not effective_tvg_id or effective_tvg_id not in guide_channel_ids:
                dropped_entries += 1
                pending_meta = []
                continue

        if effective_tvg_id and effective_tvg_id != tvg_id:
            extinf = _rewrite_extinf_tvg_id(extinf, effective_tvg_id)

        if not output:
            output.append("#EXTM3U")
        output.append(extinf)
        output.extend(pending_meta)
        output.append(stripped)
        pending_meta = []
        kept_entries += 1

    if not output:
        output = ["#EXTM3U"]

    rendered = "\n".join(output) + "\n"
    summary = {
        "total_entries": total_entries,
        "kept_entries": kept_entries,
        "dropped_entries": dropped_entries,
        "normalized_ids": normalized_ids,
    }
    return rendered, summary


def _container_path_for_materialized_playlist(output_rel_path):
    rel = str(output_rel_path or "").strip().lstrip("/")
    if not rel:
        return ""
    if rel.startswith("jellyfin/"):
        return "/config/" + rel[len("jellyfin/") :]
    return "/" + rel


def prepare_jellyfin_m3u_tuner_url(tuner, guides, config_root, guide_channel_ids_cache=None):
    if not isinstance(tuner, dict):
        return str(tuner or "").strip()

    tuner_type = str(tuner.get("type", "m3u")).strip().lower()
    source_url = str(tuner.get("url") or "").strip()
    if tuner_type != "m3u" or not source_url:
        return source_url

    normalize_tvg_id_suffix = bool(tuner.get("normalize_tvg_id_suffix", False))
    filter_to_guide_channels = bool(tuner.get("filter_to_guide_channels", False))
    if not normalize_tvg_id_suffix and not filter_to_guide_channels:
        return source_url

    source_hash = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
    output_rel_path = str(
        tuner.get("materialized_output_path") or f"jellyfin/livetv-tuners/{source_hash}.m3u"
    ).strip()
    if not output_rel_path:
        output_rel_path = f"jellyfin/livetv-tuners/{source_hash}.m3u"

    try:
        m3u_text = _read_text_from_source(source_url, config_root, timeout_seconds=90)

        guide_channel_ids = None
        selected_guide_path = ""
        if filter_to_guide_channels:
            selected_guide_path = str(tuner.get("filter_guide_path") or "").strip()
            if not selected_guide_path:
                for guide in coerce_list(guides):
                    if not isinstance(guide, dict):
                        continue
                    candidate = str(guide.get("path") or "").strip()
                    if candidate:
                        selected_guide_path = candidate
                        break

            if selected_guide_path:
                cache = guide_channel_ids_cache if isinstance(guide_channel_ids_cache, dict) else {}
                if selected_guide_path in cache:
                    guide_channel_ids = cache[selected_guide_path]
                else:
                    xml_text = _read_text_from_source(
                        selected_guide_path, config_root, timeout_seconds=150
                    )
                    guide_channel_ids = _extract_xmltv_channel_ids(xml_text)
                    cache[selected_guide_path] = guide_channel_ids
                if not guide_channel_ids:
                    log(
                        "[WARN] Jellyfin Live TV: guide channel list is empty; "
                        f"disabling channel filter for tuner={source_url}"
                    )
                    guide_channel_ids = None
            else:
                log(
                    "[WARN] Jellyfin Live TV: filter_to_guide_channels is enabled but no guide path "
                    f"was resolved for tuner={source_url}; continuing without guide filtering."
                )

        rendered, summary = _transform_m3u_for_guide(
            m3u_text,
            normalize_tvg_id_suffix=normalize_tvg_id_suffix,
            guide_channel_ids=guide_channel_ids,
        )
        if filter_to_guide_channels and summary.get("kept_entries", 0) == 0:
            rendered, summary = _transform_m3u_for_guide(
                m3u_text,
                normalize_tvg_id_suffix=normalize_tvg_id_suffix,
                guide_channel_ids=None,
            )
            log(
                "[WARN] Jellyfin Live TV: guide-filtered playlist was empty; "
                f"falling back to unfiltered normalized playlist for tuner={source_url}"
            )

        target_paths = []
        for root in _candidate_config_roots(config_root):
            path = resolve_path(root, output_rel_path)
            key = str(path)
            if key not in target_paths:
                target_paths.append(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rendered, encoding="utf-8")

        container_path = _container_path_for_materialized_playlist(output_rel_path)
        log(
            "[INFO] Jellyfin Live TV: prepared tuner playlist "
            f"({source_url} -> {container_path}, total={summary.get('total_entries', 0)}, "
            f"kept={summary.get('kept_entries', 0)}, dropped={summary.get('dropped_entries', 0)}, "
            f"normalized_ids={summary.get('normalized_ids', 0)}, "
            f"guide_filter={'on' if guide_channel_ids is not None else 'off'})"
        )
        return container_path or source_url
    except Exception as exc:
        log(
            "[WARN] Jellyfin Live TV: playlist preprocessing failed "
            f"for tuner={source_url} ({exc}); continuing with source URL."
        )
        return source_url


def read_sabnzbd_api_key(config_root, sab_cfg):
    return _sabnzbd_service().read_api_key(config_root, sab_cfg)


def read_jellyfin_api_key_from_db(config_root, jellyfin_cfg):
    db_rel_path = jellyfin_cfg.get("api_key_db_path", "jellyfin/data/jellyfin.db")
    db_path = resolve_path(config_root, db_rel_path)
    if not db_path.exists():
        raise RuntimeError(f"Jellyfin API key db not found: {db_path}")

    preferred_names = coerce_list(
        jellyfin_cfg.get("api_key_name_preference", ["Jellyfin", "Jellyseerr"])
    )
    preferred_names = [str(x).strip().lower() for x in preferred_names if str(x).strip()]

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT Id, Name, AccessToken FROM ApiKeys ORDER BY Id DESC")
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Jellyfin API key db query failed ({db_path}): {exc}") from exc
    finally:
        if conn is not None:
            conn.close()

    if not rows:
        raise RuntimeError(f"No API keys found in {db_path}")

    by_name = {}
    for _, name, token in rows:
        key_name = str(name or "").strip().lower()
        if key_name and token and key_name not in by_name:
            by_name[key_name] = str(token).strip()

    for preferred in preferred_names:
        token = by_name.get(preferred)
        if token:
            return token, preferred

    # Fallback to newest non-empty token when preferred names are not present.
    for _, name, token in rows:
        if token:
            return str(token).strip(), str(name or "unknown")

    raise RuntimeError(f"No usable API token found in {db_path}")


def resolve_jellyfin_api_key(jellyfin_cfg, config_root):
    api_key_env = jellyfin_cfg.get("api_key_env", "JELLYFIN_API_KEY")
    env_value = (os.environ.get(api_key_env) or "").strip()
    if env_value:
        log(f"[OK] Jellyfin: using API key from env {api_key_env}")
        return env_value

    if bool_cfg(jellyfin_cfg, "auto_discover_api_key_from_db", True):
        token, source_name = read_jellyfin_api_key_from_db(config_root, jellyfin_cfg)
        log("[OK] Jellyfin: discovered API key from db " f"(source key name='{source_name}')")
        return token

    return ""


def load_jellyfin_livetv_state(config_root, live_cfg):
    xml_rel_path = live_cfg.get("livetv_xml_path", "jellyfin/config/livetv.xml")
    candidate_paths = [
        resolve_path(root, xml_rel_path) for root in _candidate_config_roots(config_root)
    ]
    xml_path = None
    for candidate in candidate_paths:
        if candidate.exists():
            xml_path = candidate
            break
    if xml_path is None:
        xml_path = candidate_paths[0]
    if not xml_path.exists():
        return {
            "tuner_keys": set(),
            "guide_keys": set(),
            "tuner_ids_by_key": {},
            "tuners_by_key": {},
            "guides_by_key": {},
            "source_path": str(xml_path),
        }

    try:
        root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed parsing Jellyfin Live TV config {xml_path}: {exc}") from exc

    tuner_keys = set()
    guide_keys = set()
    tuner_ids_by_key = {}
    tuners_by_key = defaultdict(list)
    guides_by_key = defaultdict(list)

    for node in root.findall("./TunerHosts/TunerHostInfo"):
        tuner_type = str((node.findtext("Type") or "")).strip().lower()
        tuner_url = str((node.findtext("Url") or "")).strip()
        tuner_id = str((node.findtext("Id") or "")).strip()
        if tuner_type and tuner_url:
            key = (tuner_type, tuner_url)
            tuner_keys.add(key)
            if tuner_id:
                tuner_ids_by_key[key] = tuner_id
            tuners_by_key[key].append(
                {
                    "id": tuner_id,
                    "type": tuner_type,
                    "url": tuner_url,
                }
            )

    for node in root.findall("./ListingProviders/ListingsProviderInfo"):
        guide_type = str((node.findtext("Type") or "")).strip().lower()
        guide_path = str((node.findtext("Path") or "")).strip()
        if guide_type and guide_path:
            key = (guide_type, guide_path)
            guide_keys.add(key)
            enabled_tuners = []
            for tuner_node in node.findall("./EnabledTuners/string"):
                value = str((tuner_node.text or "")).strip()
                if value:
                    enabled_tuners.append(value)
            enable_all_tuners_raw = str((node.findtext("EnableAllTuners") or "")).strip().lower()
            enable_all_tuners = enable_all_tuners_raw in ("1", "true", "yes", "on")
            guides_by_key[key].append(
                {
                    "id": str((node.findtext("Id") or "")).strip(),
                    "type": guide_type,
                    "path": guide_path,
                    "enabled_tuners": enabled_tuners,
                    "enable_all_tuners": enable_all_tuners,
                }
            )

    return {
        "tuner_keys": tuner_keys,
        "guide_keys": guide_keys,
        "tuner_ids_by_key": tuner_ids_by_key,
        "tuners_by_key": dict(tuners_by_key),
        "guides_by_key": dict(guides_by_key),
        "source_path": str(xml_path),
    }


def resolve_jellyfin_tuner_type_id(jellyfin_url, jellyfin_api_key, requested_type):
    status, data, body = jellyfin_request(
        jellyfin_url, "/LiveTv/TunerHosts/Types", jellyfin_api_key
    )
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(
            f"Jellyfin Live TV: failed to list tuner host types (HTTP {status}): {body}"
        )

    requested_norm = str(requested_type or "m3u").strip().lower()
    id_map = {}
    name_map = {}
    for item in data:
        type_id = str(item.get("Id") or "").strip()
        type_name = str(item.get("Name") or "").strip()
        if not type_id:
            continue
        id_map[type_id.lower()] = type_id
        if type_name:
            name_map[type_name.lower()] = type_id

    if requested_norm in id_map:
        return id_map[requested_norm]
    if requested_norm in name_map:
        return name_map[requested_norm]

    # Convenience fallback: allow loose match for values like "M3U Tuner".
    for type_name, type_id in name_map.items():
        if requested_norm in type_name:
            return type_id

    available = sorted(set(list(id_map.values()) + list(name_map.keys())))
    raise RuntimeError(
        "Jellyfin Live TV: requested tuner type "
        f"'{requested_type}' not available. Available: {available}"
    )


def normalize_enabled_tuner_ids(enabled_tuners, state):
    out = []
    for item in coerce_list(enabled_tuners):
        value = str(item or "").strip()
        if not value:
            continue
        if value.startswith("tuner-url:"):
            raw_url = value.split(":", 1)[1].strip()
            for key, tuner_id in state["tuner_ids_by_key"].items():
                _, tuner_url = key
                if tuner_url == raw_url and tuner_id:
                    out.append(tuner_id)
        elif value.startswith("tuner-type-url:"):
            raw = value.split(":", 1)[1].strip()
            if "|" in raw:
                raw_type, raw_url = raw.split("|", 1)
                lookup = (raw_type.strip().lower(), raw_url.strip())
                tuner_id = state["tuner_ids_by_key"].get(lookup)
                if tuner_id:
                    out.append(tuner_id)
        else:
            out.append(value)
    # Preserve order while removing duplicates.
    deduped = []
    seen = set()
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def delete_jellyfin_livetv_entity(jellyfin_url, jellyfin_api_key, entity, entity_id):
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return

    if entity == "tuner":
        endpoint = "/LiveTv/TunerHosts"
    elif entity == "guide":
        endpoint = "/LiveTv/ListingProviders"
    else:
        raise RuntimeError(f"Unsupported Jellyfin Live TV entity type: {entity}")

    encoded_id = parse.quote(entity_id, safe="")
    status, _, body = jellyfin_request(
        jellyfin_url,
        f"{endpoint}?id={encoded_id}",
        jellyfin_api_key,
        method="DELETE",
    )
    if status not in (200, 202, 204):
        raise RuntimeError(
            f"Jellyfin Live TV: failed deleting {entity} {entity_id} " f"(HTTP {status}): {body}"
        )


def trigger_jellyfin_scheduled_task(jellyfin_url, jellyfin_api_key, preferred_names):
    names = [str(name or "").strip() for name in (preferred_names or []) if str(name or "").strip()]
    if not names:
        return False, ""

    status, tasks, body = jellyfin_request(jellyfin_url, "/ScheduledTasks", jellyfin_api_key)
    if status != 200 or not isinstance(tasks, list):
        return False, f"failed listing scheduled tasks (HTTP {status}): {body}"

    by_name = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_name = str(task.get("Name") or "").strip()
        task_id = str(task.get("Id") or "").strip()
        if not task_name or not task_id:
            continue
        by_name[task_name.lower()] = (task_name, task_id)

    selected = None
    for requested in names:
        selected = by_name.get(requested.lower())
        if selected:
            break
    if not selected:
        return False, f"scheduled tasks not found: {', '.join(names)}"

    selected_name, selected_id = selected
    status, _, body = jellyfin_request(
        jellyfin_url,
        f"/ScheduledTasks/Running/{parse.quote(selected_id, safe='')}",
        jellyfin_api_key,
        method="POST",
    )
    if status in (200, 201, 202, 204):
        return True, selected_name
    return False, f"failed running '{selected_name}' (HTTP {status}): {body}"


def trigger_jellyfin_livetv_refresh(jellyfin_url, jellyfin_api_key, endpoint_path, label):
    status, _, body = jellyfin_request(
        jellyfin_url,
        endpoint_path,
        jellyfin_api_key,
        method="POST",
    )
    if status in (200, 201, 202, 204):
        return True, f"requested {label}"

    # Jellyfin 10.11+ may expose refresh via scheduled tasks instead of legacy /LiveTv/Refresh* endpoints.
    if status == 404:
        fallback_names = []
        if "channel" in label:
            fallback_names = ["TasksRefreshChannels", "Refresh Channels"]
        elif "guide" in label:
            fallback_names = ["Refresh Guide"]
        if fallback_names:
            ok, detail = trigger_jellyfin_scheduled_task(
                jellyfin_url, jellyfin_api_key, fallback_names
            )
            if ok:
                return True, f"requested {label} via scheduled task '{detail}'"
            return False, (
                f"could not request {label} via endpoint (HTTP {status}); "
                f"fallback failed: {detail}"
            )

    return False, f"could not request {label} (HTTP {status}): {body}"


def ensure_jellyfin_livetv(cfg, config_root, wait_timeout):
    _jellyfin_service().ensure_livetv(cfg, config_root, wait_timeout)


def ensure_jellyfin_libraries(cfg, config_root, wait_timeout):
    libraries_cfg = cfg.get("jellyfin_libraries") or {}
    if not bool_cfg(libraries_cfg, "enabled", False):
        return

    jellyfin_url = normalize_url(libraries_cfg.get("url", "http://jellyfin:8096"))
    wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

    jellyfin_api_key = resolve_jellyfin_api_key(libraries_cfg, config_root)
    if not jellyfin_api_key:
        raise RuntimeError(
            "Jellyfin libraries: API key unavailable. Set JELLYFIN_API_KEY or keep "
            "jellyfin_libraries.auto_discover_api_key_from_db=true."
        )

    libraries = coerce_list(libraries_cfg.get("libraries"))
    if not libraries:
        log("[WARN] Jellyfin libraries: enabled but no libraries were declared.")
        return

    status, existing, body = jellyfin_request(
        jellyfin_url, "/Library/VirtualFolders", jellyfin_api_key
    )
    if status != 200 or not isinstance(existing, list):
        raise RuntimeError(
            f"Jellyfin libraries: failed listing virtual folders (HTTP {status}): {body}"
        )

    existing_by_name = {}
    for folder in existing:
        if not isinstance(folder, dict):
            continue
        name = str(folder.get("Name") or folder.get("name") or "").strip().lower()
        if not name:
            continue
        existing_by_name[name] = folder

    tune_cfg = libraries_cfg.get("tuning")
    if not isinstance(tune_cfg, dict):
        tune_cfg = {}

    # Defaults target a strong "Netflix-like" baseline while staying idempotent.
    tune_enabled = bool_cfg(tune_cfg, "enabled", True)
    realtime_monitor = bool_cfg(tune_cfg, "enable_realtime_monitor", True)
    enable_trickplay_movies = bool_cfg(tune_cfg, "enable_preview_thumbnails_movies", True)
    enable_trickplay_tv = bool_cfg(tune_cfg, "enable_preview_thumbnails_tv", True)
    preferred_metadata_language = str(
        tune_cfg.get("preferred_metadata_language", "en") or ""
    ).strip()
    metadata_country_code = str(tune_cfg.get("metadata_country_code", "US") or "").strip()
    metadata_priority = coerce_list(
        tune_cfg.get(
            "metadata_provider_priority",
            ["TheMovieDb", "Fanart", "The Open Movie Database"],
        )
    )
    image_priority = coerce_list(
        tune_cfg.get(
            "image_provider_priority",
            [
                "TheMovieDb",
                "Fanart",
                "The Open Movie Database",
                "Embedded Image Extractor",
                "Screen Grabber",
            ],
        )
    )
    artwork_profile = tune_cfg.get("artwork_profile")
    if not isinstance(artwork_profile, dict):
        artwork_profile = {
            "Backdrop": {"limit": 3, "min_width": 1280},
            "Logo": {"limit": 1, "min_width": 0},
            "Primary": {"limit": 1, "min_width": 0},
            "Thumb": {"limit": 1, "min_width": 0},
        }

    available_options_cache = {}

    def library_available_options(collection_type):
        key = str(collection_type or "").strip().lower()
        if not key:
            return {}
        if key in available_options_cache:
            return available_options_cache[key]
        path = jellyfin_build_query_path(
            "/Libraries/AvailableOptions",
            {"libraryContentType": key, "isNewLibrary": "false"},
        )
        status, payload, body = jellyfin_request(jellyfin_url, path, jellyfin_api_key)
        if status == 200 and isinstance(payload, dict):
            available_options_cache[key] = payload
            return payload
        log(
            f"[WARN] Jellyfin libraries: could not fetch available options for {key} "
            f"(HTTP {status}): {body}"
        )
        available_options_cache[key] = {}
        return {}

    def normalize_names(entries):
        out = []
        seen = set()
        for raw in entries:
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def names_from_option_info(entries):
        out = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or item.get("name") or "").strip()
            if name:
                out.append(name)
        return normalize_names(out)

    def default_image_options(entries):
        out = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            image_type = str(item.get("Type") or "").strip()
            if not image_type:
                continue
            out.append(
                {
                    "Type": image_type,
                    "Limit": int(item.get("Limit", 0) or 0),
                    "MinWidth": int(item.get("MinWidth", 0) or 0),
                }
            )
        return out

    def normalize_type_options(entries):
        out = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            type_name = str(item.get("Type") or item.get("type") or "").strip()
            if not type_name:
                continue
            out.append(
                {
                    "Type": type_name,
                    "MetadataFetchers": normalize_names(
                        item.get("MetadataFetchers") or item.get("metadataFetchers") or []
                    ),
                    "MetadataFetcherOrder": normalize_names(
                        item.get("MetadataFetcherOrder") or item.get("metadataFetcherOrder") or []
                    ),
                    "ImageFetchers": normalize_names(
                        item.get("ImageFetchers") or item.get("imageFetchers") or []
                    ),
                    "ImageFetcherOrder": normalize_names(
                        item.get("ImageFetcherOrder") or item.get("imageFetcherOrder") or []
                    ),
                    "ImageOptions": default_image_options(
                        item.get("ImageOptions") or item.get("imageOptions") or []
                    ),
                }
            )
        return out

    def type_options_from_available_payload(payload):
        type_entries = []
        for raw in coerce_list(payload.get("TypeOptions")):
            if not isinstance(raw, dict):
                continue
            type_name = str(raw.get("Type") or "").strip()
            if not type_name:
                continue
            metadata_fetchers = names_from_option_info(raw.get("MetadataFetchers"))
            image_fetchers = names_from_option_info(raw.get("ImageFetchers"))
            type_entries.append(
                {
                    "Type": type_name,
                    "MetadataFetchers": metadata_fetchers,
                    "MetadataFetcherOrder": list(metadata_fetchers),
                    "ImageFetchers": image_fetchers,
                    "ImageFetcherOrder": list(image_fetchers),
                    "ImageOptions": default_image_options(raw.get("DefaultImageOptions")),
                    "_supported_image_types": normalize_names(raw.get("SupportedImageTypes") or []),
                }
            )
        return type_entries

    def reconcile_type_options(current_options, available_payload):
        current = normalize_type_options(current_options)
        available = type_options_from_available_payload(available_payload)
        available_by_type = {
            str(entry.get("Type") or "").strip().lower(): entry for entry in available
        }
        current_by_type = {str(entry.get("Type") or "").strip().lower(): entry for entry in current}
        ordered_keys = []
        for entry in available:
            key = str(entry.get("Type") or "").strip().lower()
            if key and key not in ordered_keys:
                ordered_keys.append(key)
        for entry in current:
            key = str(entry.get("Type") or "").strip().lower()
            if key and key not in ordered_keys:
                ordered_keys.append(key)

        merged = []
        for key in ordered_keys:
            base = available_by_type.get(key, {})
            cur = current_by_type.get(key, {})
            type_name = str(cur.get("Type") or base.get("Type") or "").strip()
            if not type_name:
                continue

            metadata_fetchers = normalize_names(
                coerce_list(cur.get("MetadataFetchers")) + coerce_list(base.get("MetadataFetchers"))
            )
            image_fetchers = normalize_names(
                coerce_list(cur.get("ImageFetchers")) + coerce_list(base.get("ImageFetchers"))
            )

            metadata_order_seed = normalize_names(
                coerce_list(cur.get("MetadataFetcherOrder"))
                + coerce_list(base.get("MetadataFetcherOrder"))
                + metadata_fetchers
            )
            image_order_seed = normalize_names(
                coerce_list(cur.get("ImageFetcherOrder"))
                + coerce_list(base.get("ImageFetcherOrder"))
                + image_fetchers
            )

            metadata_order = _lib_jellyfin_reorder_provider_names(
                metadata_order_seed, metadata_priority
            )
            image_order = _lib_jellyfin_reorder_provider_names(image_order_seed, image_priority)

            # Keep configured/available providers only.
            metadata_set = {name.lower() for name in metadata_fetchers}
            image_set = {name.lower() for name in image_fetchers}
            metadata_order = [name for name in metadata_order if name.lower() in metadata_set]
            image_order = [name for name in image_order if name.lower() in image_set]

            image_options = _lib_jellyfin_apply_artwork_profile(
                coerce_list(cur.get("ImageOptions")) or coerce_list(base.get("ImageOptions")),
                coerce_list(base.get("_supported_image_types")),
                artwork_profile,
            )

            merged.append(
                {
                    "Type": type_name,
                    "MetadataFetchers": metadata_fetchers,
                    "MetadataFetcherOrder": metadata_order,
                    "ImageFetchers": image_fetchers,
                    "ImageFetcherOrder": image_order,
                    "ImageOptions": image_options,
                }
            )
        return merged

    added = 0
    tuned = 0
    scan_requested = False
    for entry in libraries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        collection_type = str(entry.get("collection_type") or "").strip()
        paths = [str(p).strip() for p in coerce_list(entry.get("paths")) if str(p).strip()]

        if not name or not paths:
            continue

        key = name.lower()
        current = existing_by_name.get(key)
        if current:
            current_paths = {str(p).rstrip("/") for p in (current.get("Locations") or [])}
            desired_paths = {p.rstrip("/") for p in paths}
            if desired_paths.issubset(current_paths):
                log(f"[OK] Jellyfin libraries: already present: {name}")
            else:
                log(
                    f"[WARN] Jellyfin libraries: '{name}' exists but paths differ "
                    f"(existing={sorted(current_paths)}, desired={sorted(desired_paths)}). "
                    "Update manually in Jellyfin if you want path changes."
                )

        if not current:
            query = {
                "name": name,
                "collectionType": collection_type,
                "paths": paths[0],
                "refreshLibrary": "true",
            }
            path = f"/Library/VirtualFolders?{parse.urlencode(query)}"
            status, _, body = jellyfin_request(jellyfin_url, path, jellyfin_api_key, method="POST")
            if status in (200, 201, 202, 204):
                added += 1
                scan_requested = True
                log(f"[OK] Jellyfin libraries: created library '{name}' -> {paths[0]}")
                status, existing, body = jellyfin_request(
                    jellyfin_url, "/Library/VirtualFolders", jellyfin_api_key
                )
                if status != 200 or not isinstance(existing, list):
                    raise RuntimeError(
                        "Jellyfin libraries: created library but failed reloading folders "
                        f"(HTTP {status}): {body}"
                    )
                existing_by_name = {}
                for folder in existing:
                    if not isinstance(folder, dict):
                        continue
                    folder_name = (
                        str(folder.get("Name") or folder.get("name") or "").strip().lower()
                    )
                    if folder_name:
                        existing_by_name[folder_name] = folder
                current = existing_by_name.get(key)
            else:
                raise RuntimeError(
                    f"Jellyfin libraries: failed creating '{name}' (HTTP {status}): {body}"
                )

        if not tune_enabled or not isinstance(current, dict):
            continue

        item_id = str(current.get("ItemId") or current.get("itemId") or "").strip()
        if not item_id:
            log(f"[WARN] Jellyfin libraries: cannot tune '{name}' (missing ItemId)")
            continue

        current_options = current.get("LibraryOptions")
        if not isinstance(current_options, dict):
            log(f"[WARN] Jellyfin libraries: cannot tune '{name}' (missing LibraryOptions)")
            continue

        desired_options = json.loads(json.dumps(current_options))
        if realtime_monitor and "EnableRealtimeMonitor" in desired_options:
            desired_options["EnableRealtimeMonitor"] = True
        if preferred_metadata_language and "PreferredMetadataLanguage" in desired_options:
            desired_options["PreferredMetadataLanguage"] = preferred_metadata_language
        if metadata_country_code and "MetadataCountryCode" in desired_options:
            desired_options["MetadataCountryCode"] = metadata_country_code

        collection_key = str(collection_type or current.get("CollectionType") or "").strip().lower()
        if collection_key in ("movies", "tvshows"):
            enable_trickplay = (
                enable_trickplay_movies if collection_key == "movies" else enable_trickplay_tv
            )
            if enable_trickplay:
                for trickplay_key in (
                    "EnableTrickplayImageExtraction",
                    "ExtractTrickplayImagesDuringLibraryScan",
                ):
                    if trickplay_key in desired_options:
                        desired_options[trickplay_key] = True

        available_payload = library_available_options(collection_key)
        desired_options["TypeOptions"] = reconcile_type_options(
            desired_options.get("TypeOptions"), available_payload
        )

        if desired_options != current_options:
            update_payload = {"Id": item_id, "LibraryOptions": desired_options}
            status, _, body = jellyfin_request(
                jellyfin_url,
                "/Library/VirtualFolders/LibraryOptions",
                jellyfin_api_key,
                method="POST",
                payload=update_payload,
            )
            if status in (200, 201, 202, 204):
                tuned += 1
                scan_requested = True
                log(
                    f"[OK] Jellyfin libraries: tuned '{name}' options "
                    f"(realtime={desired_options.get('EnableRealtimeMonitor')}, "
                    f"trickplay={desired_options.get('EnableTrickplayImageExtraction')})"
                )
            else:
                raise RuntimeError(
                    f"Jellyfin libraries: failed updating options for '{name}' "
                    f"(HTTP {status}): {body}"
                )
        else:
            log(f"[OK] Jellyfin libraries: tuning already matches desired config for '{name}'")

    if scan_requested and bool_cfg(tune_cfg, "scan_all_libraries_after_reconcile", True):
        status, _, body = jellyfin_request(
            jellyfin_url, "/Library/Refresh", jellyfin_api_key, method="POST"
        )
        if status in (200, 201, 202, 204):
            log("[OK] Jellyfin libraries: triggered library refresh")
        else:
            log(
                f"[WARN] Jellyfin libraries: failed to trigger library refresh "
                f"(HTTP {status}): {body}"
            )

    log(f"[OK] Jellyfin libraries: reconcile complete (added={added}, tuned={tuned})")


def ensure_jellyfin_prewarm(cfg, config_root, wait_timeout):
    prewarm_cfg = cfg.get("jellyfin_prewarm") or {}
    if not bool_cfg(prewarm_cfg, "enabled", False):
        return

    libraries_cfg = cfg.get("jellyfin_libraries") or {}
    livetv_cfg = cfg.get("jellyfin_livetv") or {}
    api_cfg = dict(libraries_cfg)
    if not isinstance(api_cfg, dict):
        api_cfg = {}
    for key in (
        "api_key_env",
        "auto_discover_api_key_from_db",
        "api_key_db_path",
        "api_key_name_preference",
    ):
        if key in prewarm_cfg:
            api_cfg[key] = prewarm_cfg.get(key)
    api_cfg["url"] = (
        prewarm_cfg.get("url")
        or libraries_cfg.get("url")
        or livetv_cfg.get("url")
        or "http://jellyfin:8096"
    )

    jellyfin_url = normalize_url(api_cfg.get("url"))
    wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)
    jellyfin_api_key = resolve_jellyfin_api_key(api_cfg, config_root)

    refresh_params = prewarm_cfg.get("library_refresh_query")
    if not isinstance(refresh_params, dict):
        refresh_params = {
            "metadataRefreshMode": "FullRefresh",
            "imageRefreshMode": "FullRefresh",
            "replaceAllMetadata": "false",
            "replaceAllImages": "false",
        }

    if bool_cfg(prewarm_cfg, "refresh_library", True):
        refresh_path = jellyfin_build_query_path("/Library/Refresh", refresh_params)
        status, _, body = jellyfin_request(
            jellyfin_url, refresh_path, jellyfin_api_key, method="POST"
        )
        if status in (200, 201, 202, 204):
            log("[OK] Jellyfin prewarm: requested library metadata/artwork refresh")
        else:
            raise RuntimeError(
                f"Jellyfin prewarm: failed requesting library refresh (HTTP {status}): {body}"
            )

    if bool_cfg(prewarm_cfg, "refresh_channels", True):
        ok, detail = trigger_jellyfin_livetv_refresh(
            jellyfin_url,
            jellyfin_api_key,
            "/LiveTv/RefreshChannels",
            "Live TV channel refresh",
        )
        if ok:
            log(f"[OK] Jellyfin prewarm: {detail}")
        else:
            log(f"[WARN] Jellyfin prewarm: {detail}")

    if bool_cfg(prewarm_cfg, "refresh_guide", True):
        ok, detail = trigger_jellyfin_livetv_refresh(
            jellyfin_url,
            jellyfin_api_key,
            "/LiveTv/RefreshGuide",
            "Live TV guide refresh",
        )
        if ok:
            log(f"[OK] Jellyfin prewarm: {detail}")
        else:
            log(f"[WARN] Jellyfin prewarm: {detail}")

    log("[OK] Jellyfin prewarm: reconcile complete")


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
    playback_cfg = cfg.get("jellyfin_playback") or {}
    if not bool_cfg(playback_cfg, "enabled", False):
        return

    jellyfin_url = normalize_url(playback_cfg.get("url", "http://jellyfin:8096"))
    wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

    jellyfin_api_key = resolve_jellyfin_api_key(playback_cfg, config_root)
    if not jellyfin_api_key:
        raise RuntimeError(
            "Jellyfin playback: API key unavailable. Set JELLYFIN_API_KEY or keep "
            "jellyfin_playback.auto_discover_api_key_from_db=true."
        )

    user_id = resolve_jellyfin_user_id_value(playback_cfg, jellyfin_url, jellyfin_api_key)
    if not user_id:
        raise RuntimeError(
            "Jellyfin playback: no Jellyfin user id could be resolved. Set JELLYFIN_USER_ID or "
            "keep jellyfin_playback.auto_discover_user_id=true."
        )

    user_defaults = playback_cfg.get("user_defaults")
    if not isinstance(user_defaults, dict) or not user_defaults:
        user_defaults = {
            "AudioLanguagePreference": "eng",
            "PlayDefaultAudioTrack": True,
            "SubtitleLanguagePreference": "eng",
            "SubtitleMode": "Smart",
            "RememberAudioSelections": True,
            "RememberSubtitleSelections": True,
            "EnableNextEpisodeAutoPlay": True,
            "DisplayCollectionsView": False,
            "HidePlayedInLatest": False,
        }

    user_path = jellyfin_build_query_path(
        f"/Users/{parse.quote(user_id, safe='')}",
        {},
    )
    status, user_payload, body = jellyfin_request(jellyfin_url, user_path, jellyfin_api_key)
    if status != 200 or not isinstance(user_payload, dict):
        raise RuntimeError(
            f"Jellyfin playback: failed reading user ({user_id}) (HTTP {status}): {body}"
        )

    current_user_cfg = user_payload.get("Configuration")
    if not isinstance(current_user_cfg, dict):
        raise RuntimeError("Jellyfin playback: user payload missing Configuration object.")

    desired_user_cfg = dict(current_user_cfg)
    changed_user_keys = []
    for key, value in user_defaults.items():
        if desired_user_cfg.get(key) != value:
            desired_user_cfg[key] = value
            changed_user_keys.append(key)

    if changed_user_keys:
        update_path = jellyfin_build_query_path("/Users/Configuration", {"userId": user_id})
        status, _, body = jellyfin_request(
            jellyfin_url,
            update_path,
            jellyfin_api_key,
            method="POST",
            payload=desired_user_cfg,
        )
        if status not in (200, 201, 202, 204):
            raise RuntimeError(
                f"Jellyfin playback: failed updating user defaults (HTTP {status}): {body}"
            )
        log(
            "[OK] Jellyfin playback: updated user defaults " f"(keys={','.join(changed_user_keys)})"
        )
    else:
        log("[OK] Jellyfin playback: user defaults already match desired config")

    server_defaults = playback_cfg.get("server_defaults")
    if not isinstance(server_defaults, dict) or not server_defaults:
        server_defaults = {
            "PreferredMetadataLanguage": "en",
            "MetadataCountryCode": "US",
            "UICulture": "en-US",
            "ImageSavingConvention": "Compatible",
            "ChapterImageResolution": "P720",
            "EnableGroupingMoviesIntoCollections": True,
            "EnableGroupingShowsIntoCollections": True,
            "EnableExternalContentInSuggestions": True,
        }

    status, server_payload, body = jellyfin_request(
        jellyfin_url,
        "/System/Configuration",
        jellyfin_api_key,
    )
    if status != 200 or not isinstance(server_payload, dict):
        raise RuntimeError(
            f"Jellyfin playback: failed reading server config (HTTP {status}): {body}"
        )

    desired_server_cfg = dict(server_payload)
    changed_server_keys = []
    for key, value in server_defaults.items():
        if key not in desired_server_cfg:
            continue
        if desired_server_cfg.get(key) != value:
            desired_server_cfg[key] = value
            changed_server_keys.append(key)

    if changed_server_keys:
        status, _, body = jellyfin_request(
            jellyfin_url,
            "/System/Configuration",
            jellyfin_api_key,
            method="POST",
            payload=desired_server_cfg,
        )
        if status not in (200, 201, 202, 204):
            raise RuntimeError(
                f"Jellyfin playback: failed updating server defaults (HTTP {status}): {body}"
            )
        log(
            "[OK] Jellyfin playback: updated server defaults "
            f"(keys={','.join(changed_server_keys)})"
        )
    else:
        log("[OK] Jellyfin playback: server defaults already match desired config")

    display_cfg = playback_cfg.get("display_preferences")
    if not isinstance(display_cfg, dict):
        display_cfg = {}
    if bool_cfg(display_cfg, "enabled", True):
        client = str(display_cfg.get("client") or "emby").strip() or "emby"
        preference_ids = [
            str(item).strip()
            for item in coerce_list(
                display_cfg.get(
                    "preference_ids",
                    ["usersettings", "home", "movies", "tv"],
                )
            )
            if str(item).strip()
        ]
        show_backdrop = bool_cfg(display_cfg, "show_backdrop", True)
        custom_prefs_cfg = display_cfg.get("custom_prefs")
        if not isinstance(custom_prefs_cfg, dict):
            custom_prefs_cfg = {
                "enableNextVideoInfoOverlay": True,
                "enableBackdrops": True,
                "enableThemeVideos": True,
            }
        update_existing_only = bool_cfg(display_cfg, "update_existing_custom_prefs_only", False)

        updated_display = 0
        for pref_id in preference_ids:
            path = jellyfin_build_query_path(
                f"/DisplayPreferences/{parse.quote(pref_id, safe='')}",
                {"userId": user_id, "client": client},
            )
            status, display_payload, body = jellyfin_request(jellyfin_url, path, jellyfin_api_key)
            if status != 200 or not isinstance(display_payload, dict):
                log(
                    f"[WARN] Jellyfin playback: unable to load DisplayPreferences '{pref_id}' "
                    f"(HTTP {status}): {body}"
                )
                continue

            desired_display = dict(display_payload)
            changed = False
            if desired_display.get("ShowBackdrop") != show_backdrop:
                desired_display["ShowBackdrop"] = show_backdrop
                changed = True

            custom_prefs = desired_display.get("CustomPrefs")
            if not isinstance(custom_prefs, dict):
                custom_prefs = {}
            new_custom = dict(custom_prefs)
            custom_changed = False
            for key, value in custom_prefs_cfg.items():
                pref_key = str(key or "").strip()
                if not pref_key:
                    continue
                if update_existing_only and pref_key not in custom_prefs:
                    continue
                if isinstance(value, bool):
                    pref_value = "True" if value else "False"
                else:
                    pref_value = str(value)
                if new_custom.get(pref_key) != pref_value:
                    new_custom[pref_key] = pref_value
                    custom_changed = True
            if custom_changed:
                desired_display["CustomPrefs"] = new_custom
                changed = True

            if not changed:
                continue

            status, _, body = jellyfin_request(
                jellyfin_url,
                path,
                jellyfin_api_key,
                method="POST",
                payload=desired_display,
            )
            if status not in (200, 201, 202, 204):
                log(
                    f"[WARN] Jellyfin playback: failed updating DisplayPreferences '{pref_id}' "
                    f"(HTTP {status}): {body}"
                )
                continue
            updated_display += 1

        if updated_display:
            log(
                "[OK] Jellyfin playback: updated display preferences "
                f"(count={updated_display}, client={client})"
            )
        else:
            log("[OK] Jellyfin playback: display preferences already match desired config")

    if bool_cfg(playback_cfg, "check_intro_skip_plugin", True):
        status, installed_plugins, body = jellyfin_request(
            jellyfin_url, "/Plugins", jellyfin_api_key
        )
        if status == 200 and isinstance(installed_plugins, list):
            has_intro_skip = any(
                normalize_plugin_name(item.get("Name") or item.get("name") or "")
                == normalize_plugin_name("Intro Skipper")
                for item in installed_plugins
                if isinstance(item, dict)
            )
            if has_intro_skip:
                log("[OK] Jellyfin playback: Intro Skipper plugin is installed")
            else:
                log(
                    "[WARN] Jellyfin playback: Intro Skipper plugin is not installed; "
                    "enable jellyfin_plugins.install for Intro Skipper."
                )
        else:
            log(
                f"[WARN] Jellyfin playback: could not verify Intro Skipper install "
                f"(HTTP {status}): {body}"
            )

    log("[OK] Jellyfin playback: reconcile complete")


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


def ensure_jellyfin_plugin_repositories(jellyfin_url, jellyfin_api_key, repositories):
    desired = [repo for repo in coerce_list(repositories) if isinstance(repo, dict)]
    if not desired:
        return

    status, current, body = jellyfin_request(jellyfin_url, "/Repositories", jellyfin_api_key)
    if status != 200 or not isinstance(current, list):
        raise RuntimeError(f"Jellyfin plugins: failed listing repositories (HTTP {status}): {body}")

    merged = []
    by_url = {}
    for repo in current:
        if not isinstance(repo, dict):
            continue
        repo_url = str(repo.get("Url") or repo.get("url") or "").strip()
        if not repo_url:
            continue
        normalized = repo_url.lower()
        canonical = {
            "Name": str(repo.get("Name") or repo.get("name") or repo_url).strip(),
            "Url": repo_url,
            "Enabled": bool(repo.get("Enabled", repo.get("enabled", True))),
        }
        by_url[normalized] = canonical
        merged.append(canonical)

    changed = False
    for repo in desired:
        repo_url = str(repo.get("url") or repo.get("Url") or "").strip()
        if not repo_url:
            continue
        normalized = repo_url.lower()
        desired_name = str(repo.get("name") or repo.get("Name") or repo_url).strip()
        desired_enabled = bool(repo.get("enabled", repo.get("Enabled", True)))

        if normalized in by_url:
            existing = by_url[normalized]
            if (
                existing.get("Name") != desired_name
                or bool(existing.get("Enabled", True)) != desired_enabled
            ):
                existing["Name"] = desired_name
                existing["Enabled"] = desired_enabled
                changed = True
        else:
            entry = {"Name": desired_name, "Url": repo_url, "Enabled": desired_enabled}
            merged.append(entry)
            by_url[normalized] = entry
            changed = True

    if not changed:
        log("[OK] Jellyfin plugins: repositories already match desired config")
        return

    status, _, body = jellyfin_request(
        jellyfin_url,
        "/Repositories",
        jellyfin_api_key,
        method="POST",
        payload=merged,
    )
    if status in (200, 201, 202, 204):
        log("[OK] Jellyfin plugins: repositories updated")
        return

    raise RuntimeError(f"Jellyfin plugins: failed updating repositories (HTTP {status}): {body}")


def find_jellyfin_package(packages, target_name, repository_url=None):
    target_norm = normalize_plugin_name(target_name)
    repo_norm = str(repository_url or "").strip().lower()
    exact_name = str(target_name or "").strip().lower()

    def package_repo_match(pkg):
        if not repo_norm:
            return True
        versions = coerce_list(pkg.get("versions") or pkg.get("Versions"))
        for version in versions:
            if not isinstance(version, dict):
                continue
            candidate = (
                str(version.get("repositoryUrl") or version.get("RepositoryUrl") or "")
                .strip()
                .lower()
            )
            if candidate == repo_norm:
                return True
        return False

    for package in packages:
        name = str(package.get("name") or package.get("Name") or "").strip()
        if not name:
            continue
        if name.lower() == exact_name and package_repo_match(package):
            return package

    for package in packages:
        name = str(package.get("name") or package.get("Name") or "").strip()
        if not name:
            continue
        if normalize_plugin_name(name) == target_norm and package_repo_match(package):
            return package

    return None


def ensure_jellyfin_plugins(cfg, config_root, wait_timeout):
    plugins_cfg = cfg.get("jellyfin_plugins") or {}
    if not bool_cfg(plugins_cfg, "enabled", False):
        return

    jellyfin_url = normalize_url(plugins_cfg.get("url", "http://jellyfin:8096"))
    wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

    jellyfin_api_key = resolve_jellyfin_api_key(plugins_cfg, config_root)
    if not jellyfin_api_key:
        raise RuntimeError(
            "Jellyfin plugins: API key unavailable. Set JELLYFIN_API_KEY or keep "
            "jellyfin_plugins.auto_discover_api_key_from_db=true."
        )

    ensure_jellyfin_plugin_repositories(
        jellyfin_url,
        jellyfin_api_key,
        plugins_cfg.get("repositories"),
    )

    status, installed, body = jellyfin_request(jellyfin_url, "/Plugins", jellyfin_api_key)
    if status != 200 or not isinstance(installed, list):
        raise RuntimeError(
            f"Jellyfin plugins: failed listing installed plugins (HTTP {status}): {body}"
        )
    installed_names = {
        normalize_plugin_name(item.get("Name") or item.get("name") or "")
        for item in installed
        if isinstance(item, dict)
    }

    status, packages, body = jellyfin_request(jellyfin_url, "/Packages", jellyfin_api_key)
    if status != 200 or not isinstance(packages, list):
        raise RuntimeError(
            f"Jellyfin plugins: failed listing available packages (HTTP {status}): {body}"
        )

    installs = coerce_list(plugins_cfg.get("install"))
    if not installs:
        log("[WARN] Jellyfin plugins: enabled but install list is empty.")
        return

    requested = 0
    already = 0
    for entry in installs:
        if isinstance(entry, dict):
            plugin_name = str(entry.get("name") or "").strip()
            repository_url = str(entry.get("repository_url") or "").strip()
            required = bool(entry.get("required", False))
            version = str(entry.get("version") or "").strip()
        else:
            plugin_name = str(entry).strip()
            repository_url = ""
            required = False
            version = ""

        if not plugin_name:
            continue

        normalized_name = normalize_plugin_name(plugin_name)
        if normalized_name in installed_names:
            already += 1
            log(f"[OK] Jellyfin plugins: already installed: {plugin_name}")
            continue

        package = find_jellyfin_package(packages, plugin_name, repository_url or None)
        if not package:
            message = f"Jellyfin plugins: package not found for '{plugin_name}'" + (
                f" in repo {repository_url}" if repository_url else ""
            )
            if required:
                raise RuntimeError(message)
            log(f"[WARN] {message}")
            continue

        pkg_name = str(package.get("name") or package.get("Name") or plugin_name).strip()
        pkg_guid = str(package.get("guid") or package.get("Guid") or "").strip()
        query = []
        if pkg_guid:
            query.append(("assemblyGuid", pkg_guid))
        if version:
            query.append(("version", version))
        if repository_url:
            query.append(("repositoryUrl", repository_url))
        path = f"/Packages/Installed/{parse.quote(pkg_name, safe='')}"
        if query:
            path = f"{path}?{parse.urlencode(query)}"

        status, _, body = jellyfin_request(jellyfin_url, path, jellyfin_api_key, method="POST")
        if status in (200, 201, 202, 204):
            requested += 1
            log(f"[OK] Jellyfin plugins: install requested for {pkg_name}")
            continue

        message = f"Jellyfin plugins: failed to install {pkg_name} " f"(HTTP {status}): {body}"
        if required:
            raise RuntimeError(message)
        log(f"[WARN] {message}")

    log(
        "[OK] Jellyfin plugins: reconcile complete "
        f"(install_requested={requested}, already_installed={already})"
    )


def yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def render_yaml(value, indent=0):
    prefix = " " * indent
    lines = []

    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key_text}:")
                if isinstance(item, list) and not item:
                    lines[-1] = f"{prefix}{key_text}: []"
                else:
                    lines.extend(render_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}{key_text}: {yaml_scalar(item)}")
        return lines

    if isinstance(value, list):
        if not value:
            lines.append(f"{prefix}[]")
            return lines
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(render_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}- {yaml_scalar(item)}")
        return lines

    lines.append(f"{prefix}{yaml_scalar(value)}")
    return lines


def ensure_homepage_services_config(cfg, config_root):
    homepage_cfg = cfg.get("homepage") or {}
    hosts = [
        str(h).strip().lower() for h in coerce_list(homepage_cfg.get("hosts")) if str(h).strip()
    ]
    enabled = bool_cfg(homepage_cfg, "enabled", False) or bool(hosts)
    if not enabled:
        return False

    scheme = str(homepage_cfg.get("scheme", "http")).strip().lower() or "http"
    services_rel_path = str(
        homepage_cfg.get("services_relative_path") or "homepage/services.yaml"
    ).strip()
    services_path = resolve_path(config_root, services_rel_path)
    services_path.parent.mkdir(parents=True, exist_ok=True)

    if not hosts:
        hosts = list(_lib_default_homepage_hosts)

    onboarding_cfg = homepage_cfg.get("device_onboarding")
    if not isinstance(onboarding_cfg, dict):
        onboarding_cfg = {}
    rendered = _lib_render_homepage_services_yaml(hosts, scheme=scheme, onboarding=onboarding_cfg)
    current = (
        services_path.read_text(encoding="utf-8", errors="replace")
        if services_path.exists()
        else ""
    )
    if current == rendered:
        log(f"[OK] Homepage: services config already up-to-date at {services_path}")
        return False

    services_path.write_text(rendered, encoding="utf-8")
    log(f"[OK] Homepage: wrote services config {services_path} (hosts={len(hosts)})")
    log("[INFO] Homepage: restart recommended to pick up updated services config.")
    return True


def ensure_bazarr_arr_integration(cfg, config_root, arr_apps, app_keys, wait_timeout):
    return _bazarr_service().ensure_arr_integration(
        cfg=cfg,
        config_root=config_root,
        arr_apps=arr_apps,
        app_keys=app_keys,
        wait_timeout=wait_timeout,
    )


def detect_jellyfin_user_id(jellyfin_url, jellyfin_api_key, preferred_username):
    status, users, body = jellyfin_request(jellyfin_url, "/Users", jellyfin_api_key)
    if status != 200 or not isinstance(users, list):
        raise RuntimeError(
            f"Jellyfin Auto Collections: failed listing users (HTTP {status}): {body}"
        )

    preferred = str(preferred_username or "").strip().lower()
    if preferred:
        for user in users:
            if not isinstance(user, dict):
                continue
            if str(user.get("Name") or "").strip().lower() == preferred:
                candidate = str(user.get("Id") or "").strip()
                if candidate:
                    return candidate

    for user in users:
        if not isinstance(user, dict):
            continue
        policy = user.get("Policy") or {}
        if bool(policy.get("IsAdministrator", False)):
            candidate = str(user.get("Id") or "").strip()
            if candidate:
                return candidate

    for user in users:
        if not isinstance(user, dict):
            continue
        candidate = str(user.get("Id") or "").strip()
        if candidate:
            return candidate

    return ""


def default_auto_collections_plugins():
    # Keep defaults stable and non-empty. Some app versions error if plugins is null.
    return {"jellyfin_api": {"enabled": False, "list_ids": []}}


def ensure_jellyfin_auto_collections_config(cfg, config_root, wait_timeout):
    auto_cfg = cfg.get("jellyfin_auto_collections") or {}
    if not bool_cfg(auto_cfg, "enabled", False):
        return

    jellyfin_url = normalize_url(auto_cfg.get("url", "http://jellyfin:8096"))
    wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

    jellyfin_api_key = resolve_jellyfin_api_key(auto_cfg, config_root)
    if not jellyfin_api_key:
        raise RuntimeError(
            "Jellyfin Auto Collections: API key unavailable. Set JELLYFIN_API_KEY or keep "
            "jellyfin_auto_collections.auto_discover_api_key_from_db=true."
        )

    user_id = resolve_jellyfin_user_id_value(auto_cfg, jellyfin_url, jellyfin_api_key)

    if not user_id and bool_cfg(auto_cfg, "required_user_id", False):
        raise RuntimeError("Jellyfin Auto Collections: no Jellyfin user id could be resolved.")
    if not user_id:
        log(
            "[WARN] Jellyfin Auto Collections: could not resolve Jellyfin user id. "
            "Config will be written with an empty fallback user id."
        )

    plugins_cfg = auto_cfg.get("plugins")
    if not isinstance(plugins_cfg, dict) or not plugins_cfg:
        plugins_cfg = default_auto_collections_plugins()

    timezone_value = str(auto_cfg.get("timezone") or os.environ.get("TZ") or "UTC").strip()
    crontab_value = str(auto_cfg.get("crontab") or "0 */6 * * *").strip()

    config_data = {
        "crontab": crontab_value,
        "timezone": timezone_value,
        "jellyfin": {
            "server_url": jellyfin_url,
            "api_key": jellyfin_api_key,
            "user_id": user_id,
        },
        "plugins": plugins_cfg,
    }

    config_rel_path = str(
        auto_cfg.get("config_relative_path") or "jellyfin-auto-collections/config.yaml"
    ).strip()
    config_path = resolve_path(config_root, config_rel_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_yaml = "\n".join(render_yaml(config_data)) + "\n"
    config_path.write_text(config_yaml, encoding="utf-8")
    log(f"[OK] Jellyfin Auto Collections: wrote config {config_path}")


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
    candidate_paths = (
        f"{api_base}/config/downloadclient",
        f"{api_base}/config/downloadClient",
    )
    last_status = None
    last_body = ""

    for path in candidate_paths:
        status, data, body = http_request(app_url, path, api_key=api_key)
        last_status = status
        last_body = body
        if status == 200 and isinstance(data, dict):
            return path, data
        if status not in (404, 405):
            raise RuntimeError(
                f"{app_name}: failed reading download client config (HTTP {status}): {body}"
            )

    log(
        f"[WARN] {app_name}: download client config endpoint not found; "
        f"skipping CDH reconcile (last_status={last_status}, last_body={last_body})"
    )
    return None, None


def ensure_arr_download_handling(app_name, app_url, api_base, api_key, handling_cfg):
    endpoint, current = fetch_arr_download_client_config(app_name, app_url, api_base, api_key)
    if not endpoint or not isinstance(current, dict):
        return

    desired_enable = bool_cfg(handling_cfg, "enable_completed_download_handling", True)
    desired_remove_completed = bool_cfg(handling_cfg, "remove_completed_downloads", False)
    desired_remove_failed = bool_cfg(handling_cfg, "remove_failed_downloads", False)
    desired_redownload_failed = bool_cfg(handling_cfg, "auto_redownload_failed", False)

    payload = dict(current)
    payload["enableCompletedDownloadHandling"] = desired_enable
    payload["removeCompletedDownloads"] = desired_remove_completed
    payload["removeFailedDownloads"] = desired_remove_failed
    payload["autoRedownloadFailed"] = desired_redownload_failed

    changed = False
    for key in (
        "enableCompletedDownloadHandling",
        "removeCompletedDownloads",
        "removeFailedDownloads",
        "autoRedownloadFailed",
    ):
        if bool(current.get(key)) != bool(payload.get(key)):
            changed = True
            break

    if not changed:
        log(
            f"[OK] {app_name}: download handling already set "
            f"(CDH={desired_enable}, removeCompleted={desired_remove_completed}, "
            f"removeFailed={desired_remove_failed}, autoRedownloadFailed={desired_redownload_failed})"
        )
        return

    status, _, body = http_request(
        app_url,
        endpoint,
        api_key=api_key,
        method="PUT",
        payload=payload,
    )
    if status in (200, 201, 202):
        log(
            f"[OK] {app_name}: updated download handling "
            f"(CDH={desired_enable}, removeCompleted={desired_remove_completed}, "
            f"removeFailed={desired_remove_failed}, autoRedownloadFailed={desired_redownload_failed})"
        )
        return

    raise RuntimeError(f"{app_name}: failed updating download handling (HTTP {status}): {body}")


def resolve_arr_overrides_by_app(cfg_section, app_cfg):
    by_app = (cfg_section or {}).get("by_app") or {}
    app_name = str(app_cfg.get("name") or "")
    app_impl = str(app_cfg.get("implementation") or "")
    return (
        by_app.get(app_name)
        or by_app.get(app_impl)
        or by_app.get(app_name.lower())
        or by_app.get(app_impl.lower())
        or {}
    )


def ensure_arr_media_management(app_cfg, app_url, api_base, api_key, media_cfg):
    if not bool_cfg(media_cfg, "enabled", True):
        return

    app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
    app_impl = str(app_cfg.get("implementation") or "")
    app_overrides = resolve_arr_overrides_by_app(media_cfg, app_cfg)

    status, current, body = http_request(
        app_url, f"{api_base}/config/mediamanagement", api_key=api_key
    )
    if status != 200 or not isinstance(current, dict):
        raise RuntimeError(
            f"{app_name}: failed reading media management config (HTTP {status}): {body}"
        )

    desired = dict(current)
    changed = False

    if "copy_using_hardlinks" in app_overrides:
        desired_hardlinks = bool(app_overrides.get("copy_using_hardlinks"))
    else:
        desired_hardlinks = bool_cfg(media_cfg, "copy_using_hardlinks", True)
    if "copyUsingHardlinks" in desired and bool(desired.get("copyUsingHardlinks")) != bool(
        desired_hardlinks
    ):
        desired["copyUsingHardlinks"] = bool(desired_hardlinks)
        changed = True

    if app_impl == "Sonarr":
        if "create_empty_series_folders" in app_overrides:
            desired_season_folders = bool(app_overrides.get("create_empty_series_folders"))
        else:
            desired_season_folders = bool_cfg(media_cfg, "create_empty_series_folders", True)
        if "createEmptySeriesFolders" in desired and bool(
            desired.get("createEmptySeriesFolders")
        ) != bool(desired_season_folders):
            desired["createEmptySeriesFolders"] = bool(desired_season_folders)
            changed = True

    if not changed:
        log(
            f"[OK] {app_name}: media management already set "
            f"(hardlinks={bool(desired.get('copyUsingHardlinks', False))})"
        )
        return

    status, _, body = http_request(
        app_url,
        f"{api_base}/config/mediamanagement",
        api_key=api_key,
        method="PUT",
        payload=desired,
    )
    if status in (200, 201, 202):
        log(
            f"[OK] {app_name}: updated media management "
            f"(hardlinks={bool(desired.get('copyUsingHardlinks', False))})"
        )
        return

    raise RuntimeError(
        f"{app_name}: failed updating media management config (HTTP {status}): {body}"
    )


def ensure_arr_quality_upgrade_policy(
    cfg,
    app_cfg,
    app_url,
    api_base,
    api_key,
    quality_upgrade_cfg,
):
    if not bool_cfg(quality_upgrade_cfg, "enabled", False):
        return

    app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
    app_overrides = resolve_arr_overrides_by_app(quality_upgrade_cfg, app_cfg)
    if "enabled" in app_overrides and not bool(app_overrides.get("enabled")):
        return

    allow_upgrades = bool_cfg(
        app_overrides,
        "allow_upgrades",
        bool_cfg(quality_upgrade_cfg, "allow_upgrades", True),
    )
    disallow_tokens = [
        normalize_token(x)
        for x in coerce_list(
            app_overrides.get("disallow_quality_name_tokens")
            or quality_upgrade_cfg.get("disallow_quality_name_tokens")
            or ["2160", "4k", "uhd"]
        )
        if normalize_token(x)
    ]
    cutoff_tokens = [
        normalize_token(x)
        for x in coerce_list(
            app_overrides.get("cutoff_preferred_name_tokens")
            or quality_upgrade_cfg.get("cutoff_preferred_name_tokens")
            or ["1080"]
        )
        if normalize_token(x)
    ]

    preferred_id, preferred_names = resolve_arr_quality_preferences(cfg, app_cfg)
    selected = get_arr_quality_profile(
        app_name,
        app_url,
        api_base,
        api_key,
        preferred_id=preferred_id,
        preferred_names=preferred_names,
    )
    profile_id = selected.get("id")
    if profile_id is None:
        raise RuntimeError(
            f"{app_name}: quality upgrade policy could not resolve quality profile id"
        )

    desired = json.loads(json.dumps(selected))
    changed = False

    for key in ("upgradeAllowed", "upgradesAllowed"):
        if key in desired and bool(desired.get(key)) != bool(allow_upgrades):
            desired[key] = bool(allow_upgrades)
            changed = True

    def entry_quality_name(entry):
        if not isinstance(entry, dict):
            return ""
        quality = entry.get("quality")
        if isinstance(quality, dict):
            name = str(quality.get("name") or "").strip()
            if name:
                return name
        return str(entry.get("name") or "").strip()

    def entry_quality_id(entry):
        if not isinstance(entry, dict):
            return None
        quality = entry.get("quality")
        if isinstance(quality, dict):
            qid = to_int(quality.get("id"))
            if qid:
                return qid
        return to_int(entry.get("qualityId"))

    cutoff_id = None
    items = desired.get("items")
    if isinstance(items, list):
        rewritten = []
        for entry in items:
            if not isinstance(entry, dict):
                rewritten.append(entry)
                continue
            current = dict(entry)
            qname = entry_quality_name(current)
            qtoken = normalize_token(qname)

            if disallow_tokens and any(token in qtoken for token in disallow_tokens):
                if "allowed" in current and bool(current.get("allowed")):
                    current["allowed"] = False
                    changed = True

            if cutoff_id is None and cutoff_tokens:
                is_allowed = bool(current.get("allowed", True))
                if is_allowed and any(token in qtoken for token in cutoff_tokens):
                    qid = entry_quality_id(current)
                    if qid:
                        cutoff_id = int(qid)

            rewritten.append(current)

        if rewritten != items:
            desired["items"] = rewritten
            changed = True

    if cutoff_id and "cutoff" in desired and to_int(desired.get("cutoff")) != int(cutoff_id):
        desired["cutoff"] = int(cutoff_id)
        changed = True

    if not changed:
        log(
            f"[OK] {app_name}: quality-upgrade policy already set "
            f"(allowUpgrades={allow_upgrades}, cutoff={desired.get('cutoff')})"
        )
        return

    status, _, body = http_request(
        app_url,
        f"{api_base}/qualityprofile/{profile_id}",
        api_key=api_key,
        method="PUT",
        payload=desired,
    )
    if status in (200, 201, 202):
        log(
            f"[OK] {app_name}: updated quality-upgrade policy "
            f"(allowUpgrades={allow_upgrades}, cutoff={desired.get('cutoff')})"
        )
        return

    raise RuntimeError(
        f"{app_name}: failed updating quality-upgrade policy (HTTP {status}): {body}"
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


def _to_float(value, fallback=None):
    try:
        if value is None:
            return fallback
        text = str(value).strip()
        if text == "":
            return fallback
        return float(text)
    except Exception:
        return fallback


def _disk_usage_percent(path):
    st = os.statvfs(path)
    total = int(st.f_blocks) * int(st.f_frsize)
    avail = int(st.f_bavail) * int(st.f_frsize)
    if total <= 0:
        return 0.0, total, avail
    used = total - avail
    used_pct = (float(used) * 100.0) / float(total)
    return used_pct, total, avail


def _fmt_bytes(num):
    value = float(max(0, int(num)))
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    return f"{value:.2f} {units[idx]}"


def qbit_list_completed_torrents(opener, base_url):
    req = request.Request(
        f"{normalize_url(base_url)}/api/v2/torrents/info?{parse.urlencode({'filter': 'completed'})}",
        method="GET",
    )
    with opener.open(req, timeout=25) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise RuntimeError(
            f"qBittorrent: failed parsing completed torrents payload: {exc}"
        ) from exc
    if isinstance(payload, list):
        return payload
    raise RuntimeError("qBittorrent: completed torrent payload was not a list.")


def qbit_list_torrents(opener, base_url, filter_value="all"):
    req = request.Request(
        f"{normalize_url(base_url)}/api/v2/torrents/info?"
        f"{parse.urlencode({'filter': str(filter_value or 'all')})}",
        method="GET",
    )
    with opener.open(req, timeout=25) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"qBittorrent: failed parsing torrents payload: {exc}") from exc
    if isinstance(payload, list):
        return payload
    raise RuntimeError("qBittorrent: torrents payload was not a list.")


def run_qbit_queue_guardrails(qbit_cfg, qb_username, qb_password):
    queue_cfg = (qbit_cfg or {}).get("queue_guardrails") or {}
    enabled = bool_cfg(queue_cfg, "enabled", False)
    summary = {
        "enabled": enabled,
        "dry_run": bool_cfg(queue_cfg, "dry_run", False),
        "total": 0,
        "over_limit_candidates": 0,
        "stale_candidates": 0,
        "over_limit_deleted": 0,
        "stale_deleted": 0,
        "by_category": {},
    }
    if not enabled:
        return summary

    if not str(qb_username or "").strip() or not str(qb_password or "").strip():
        raise RuntimeError(
            "qB queue guardrails requires qB credentials (QBITTORRENT_USERNAME/QBITTORRENT_PASSWORD)."
        )

    qbit_url = normalize_url((qbit_cfg or {}).get("url", "http://qbittorrent:8080"))
    dry_run = summary["dry_run"]
    now = int(time.time())

    default_count_states = [
        "downloading",
        "queuedDL",
        "stalledDL",
        "metaDL",
        "forcedDL",
        "checkingDL",
        "pausedDL",
        "allocating",
        "checkingResumeData",
    ]
    count_states = {
        str(x).strip().lower() for x in coerce_list(queue_cfg.get("count_states")) if str(x).strip()
    } or {x.lower() for x in default_count_states}

    default_prune_states = ["queuedDL", "stalledDL", "metaDL", "pausedDL", "error", "missingFiles"]
    prune_states = {
        str(x).strip().lower() for x in coerce_list(queue_cfg.get("prune_states")) if str(x).strip()
    } or {x.lower() for x in default_prune_states}

    include_uncategorized = bool_cfg(queue_cfg, "include_uncategorized", False)
    default_max_queued = to_int(queue_cfg.get("default_max_queued"))
    prune_when_over_limit = bool_cfg(queue_cfg, "prune_when_over_limit", True)
    over_limit_delete_files = bool_cfg(queue_cfg, "over_limit_delete_files", True)
    over_limit_max_delete_per_category = to_int(
        queue_cfg.get("over_limit_max_delete_per_category"), 15
    )
    if over_limit_max_delete_per_category is None or over_limit_max_delete_per_category <= 0:
        over_limit_max_delete_per_category = 15

    max_by_category_raw = queue_cfg.get("max_queued_by_category") or {}
    max_by_category = {}
    if isinstance(max_by_category_raw, dict):
        for key, value in max_by_category_raw.items():
            norm_key = str(key or "").strip().lower()
            if not norm_key:
                continue
            parsed = to_int(value)
            if parsed is None or parsed < 0:
                continue
            max_by_category[norm_key] = int(parsed)

    stale_cfg = queue_cfg.get("stale_prune") or {}
    stale_enabled = bool_cfg(stale_cfg, "enabled", True)
    stale_max_age_hours = _to_float(stale_cfg.get("max_age_hours"), 168.0) or 168.0
    stale_max_stalled_hours = _to_float(stale_cfg.get("max_stalled_hours"), 24.0) or 24.0
    stale_max_eta_seconds = to_int(stale_cfg.get("max_eta_seconds"), 14 * 24 * 3600)
    stale_min_progress = _to_float(stale_cfg.get("min_progress"), 0.98)
    if stale_min_progress is None:
        stale_min_progress = 0.98
    stale_max_download_speed_bps = to_int(stale_cfg.get("max_download_speed_bps"), 32768)
    stale_max_delete_per_run = to_int(stale_cfg.get("max_delete_per_run"), 25)
    if stale_max_delete_per_run is None or stale_max_delete_per_run <= 0:
        stale_max_delete_per_run = 25
    stale_delete_files = bool_cfg(stale_cfg, "delete_files", True)
    stale_states = {
        str(x).strip().lower() for x in coerce_list(stale_cfg.get("states")) if str(x).strip()
    } or set(prune_states)

    opener = qbit_login(qbit_url, qb_username, qb_password)
    torrents = qbit_list_torrents(opener, qbit_url, filter_value="all")
    summary["total"] = len(torrents)

    def parse_record(item):
        if not isinstance(item, dict):
            return None
        thash = str(item.get("hash") or "").strip()
        if not thash:
            return None
        state = str(item.get("state") or "").strip().lower()
        category_raw = str(item.get("category") or "").strip()
        category = category_raw.lower()
        if not category:
            category = "uncategorized"
        progress = _to_float(item.get("progress"), 0.0) or 0.0
        added_on = to_int(item.get("added_on"), 0) or 0
        completion_on = to_int(item.get("completion_on"), 0) or 0
        last_activity = to_int(item.get("last_activity"), 0) or 0
        dlspeed = to_int(item.get("dlspeed"), 0) or 0
        eta = to_int(item.get("eta"), -1) or -1
        reference_on = completion_on if completion_on > 0 else added_on
        age_hours = 0.0
        if reference_on > 0:
            age_hours = max(0.0, float(now - reference_on) / 3600.0)
        stalled_hours = 0.0
        if last_activity > 0:
            stalled_hours = max(0.0, float(now - last_activity) / 3600.0)
        return {
            "hash": thash,
            "name": str(item.get("name") or "").strip(),
            "category": category,
            "state": state,
            "progress": progress,
            "added_on": added_on,
            "completion_on": completion_on,
            "last_activity": last_activity,
            "age_hours": age_hours,
            "stalled_hours": stalled_hours,
            "dlspeed": dlspeed,
            "eta": eta,
        }

    records = []
    queue_by_category = defaultdict(list)
    for item in torrents:
        rec = parse_record(item)
        if not rec:
            continue
        records.append(rec)
        if rec["state"] not in count_states:
            continue
        if rec["progress"] >= 1.0:
            continue
        if rec["category"] == "uncategorized" and not include_uncategorized:
            continue
        queue_by_category[rec["category"]].append(rec)

    state_rank = {
        "error": 0,
        "missingfiles": 1,
        "stalleddl": 2,
        "metadl": 3,
        "queueddl": 4,
        "pauseddl": 5,
        "checkingdl": 6,
        "downloading": 7,
        "forceddl": 8,
        "allocating": 9,
        "checkingresumedata": 10,
    }

    over_limit_hashes = []
    over_limit_seen = set()
    if prune_when_over_limit:
        for category, items in queue_by_category.items():
            category_limit = max_by_category.get(category, default_max_queued)
            if category_limit is None or category_limit < 0:
                continue
            queue_count = len(items)
            if queue_count <= category_limit:
                continue
            over_by = queue_count - category_limit
            prune_pool = [x for x in items if x.get("state") in prune_states]
            prune_pool.sort(
                key=lambda x: (
                    state_rank.get(x.get("state") or "", 50),
                    x.get("progress") or 0.0,
                    x.get("dlspeed") or 0,
                    -(x.get("eta") if (x.get("eta") or -1) > 0 else 0),
                    x.get("added_on") or 0,
                )
            )

            chosen = []
            for rec in prune_pool:
                if len(chosen) >= over_by:
                    break
                if len(chosen) >= over_limit_max_delete_per_category:
                    break
                thash = str(rec.get("hash") or "").strip()
                if not thash or thash in over_limit_seen:
                    continue
                chosen.append(thash)
                over_limit_seen.add(thash)
                over_limit_hashes.append(thash)

            summary["by_category"][category] = {
                "limit": int(category_limit),
                "queue_count": queue_count,
                "over_by": over_by,
                "selected": len(chosen),
            }

    stale_hashes = []
    stale_seen = set()
    if stale_enabled:
        stale_pool = []
        for rec in records:
            category = rec.get("category") or ""
            if category == "uncategorized" and not include_uncategorized:
                continue
            if rec.get("state") not in stale_states:
                continue
            progress = float(rec.get("progress") or 0.0)
            if progress >= float(stale_min_progress):
                continue
            dlspeed = int(rec.get("dlspeed") or 0)
            if stale_max_download_speed_bps is not None and dlspeed > int(
                stale_max_download_speed_bps
            ):
                continue
            age_trigger = float(rec.get("age_hours") or 0.0) >= float(stale_max_age_hours)
            stalled_trigger = float(rec.get("stalled_hours") or 0.0) >= float(
                stale_max_stalled_hours
            )
            eta_val = int(rec.get("eta") or -1)
            eta_trigger = bool(
                stale_max_eta_seconds is not None and eta_val > int(stale_max_eta_seconds)
            )
            if not (age_trigger or stalled_trigger or eta_trigger):
                continue
            stale_pool.append(rec)

        stale_pool.sort(
            key=lambda x: (
                x.get("progress") or 0.0,
                x.get("dlspeed") or 0,
                -(x.get("eta") if (x.get("eta") or -1) > 0 else 0),
                -(x.get("age_hours") or 0.0),
                -(x.get("stalled_hours") or 0.0),
            )
        )
        for rec in stale_pool:
            if len(stale_hashes) >= stale_max_delete_per_run:
                break
            thash = str(rec.get("hash") or "").strip()
            if not thash or thash in stale_seen:
                continue
            stale_hashes.append(thash)
            stale_seen.add(thash)

    summary["over_limit_candidates"] = len(over_limit_hashes)
    summary["stale_candidates"] = len(stale_hashes)

    if dry_run:
        for thash in over_limit_hashes:
            log(f"[INFO] qB queue guardrails over-limit candidate (dry-run): {thash}")
        for thash in stale_hashes:
            log(f"[INFO] qB queue guardrails stale candidate (dry-run): {thash}")
        log(
            "[OK] qB queue guardrails: dry-run complete "
            f"(over_limit_candidates={len(over_limit_hashes)}, stale_candidates={len(stale_hashes)})."
        )
        return summary

    if over_limit_hashes:
        qbit_delete_torrents(
            opener,
            qbit_url,
            over_limit_hashes,
            delete_files=over_limit_delete_files,
        )
        summary["over_limit_deleted"] = len(over_limit_hashes)
        log(
            "[OK] qB queue guardrails: pruned over-limit queued torrents "
            f"(deleted={len(over_limit_hashes)}, delete_files={over_limit_delete_files})."
        )
    else:
        log("[OK] qB queue guardrails: no over-limit queue pruning required.")

    stale_to_delete = [x for x in stale_hashes if x not in set(over_limit_hashes)]
    if stale_to_delete:
        qbit_delete_torrents(
            opener,
            qbit_url,
            stale_to_delete,
            delete_files=stale_delete_files,
        )
        summary["stale_deleted"] = len(stale_to_delete)
        log(
            "[OK] qB queue guardrails: pruned stale/slow torrents "
            f"(deleted={len(stale_to_delete)}, delete_files={stale_delete_files})."
        )
    else:
        log("[OK] qB queue guardrails: no stale/slow torrent pruning required.")
    return summary


def qbit_delete_torrents(opener, base_url, hashes, delete_files=True):
    if not hashes:
        return
    data = parse.urlencode(
        {
            "hashes": "|".join(hashes),
            "deleteFiles": "true" if delete_files else "false",
        }
    ).encode("utf-8")
    req = request.Request(
        f"{normalize_url(base_url)}/api/v2/torrents/delete",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with opener.open(req, timeout=30):
        pass


def deep_merge_objects(base_obj, override_obj):
    if not isinstance(base_obj, dict):
        base_obj = {}
    if not isinstance(override_obj, dict):
        return json.loads(json.dumps(base_obj))

    out = json.loads(json.dumps(base_obj))
    for key, value in override_obj.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge_objects(out.get(key), value)
        else:
            out[key] = json.loads(json.dumps(value))
    return out


def ensure_maintainerr_policy(cfg, config_root):
    maintainerr_cfg = cfg.get("maintainerr") or {}
    if not bool_cfg(maintainerr_cfg, "enabled", False):
        return

    default_policy = load_bootstrap_default_json(
        "maintainerr_policy.json",
        {
            "version": 1,
            "retention": {},
            "rules": [],
        },
    )
    desired = deep_merge_objects(default_policy, maintainerr_cfg.get("policy") or {})

    relative_path = str(
        maintainerr_cfg.get("policy_relative_path") or "maintainerr/policy.json"
    ).strip()
    policy_path = resolve_path(config_root, relative_path)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(desired, ensure_ascii=True, indent=2, sort_keys=True) + "\n"

    if policy_path.exists():
        current = policy_path.read_text(encoding="utf-8", errors="replace")
        if current == rendered:
            log(f"[OK] Maintainerr policy: already up-to-date at {policy_path}")
            return

    policy_path.write_text(rendered, encoding="utf-8")
    log(f"[OK] Maintainerr policy: wrote {policy_path}")


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
    guard_cfg = cfg.get("disk_guardrails") or {}
    if not bool_cfg(guard_cfg, "enabled", False):
        return

    monitor_path = str(guard_cfg.get("monitor_path") or "").strip()
    if not monitor_path:
        candidates = [
            str(os.environ.get("DISK_GUARDRAILS_MONITOR_PATH", "")).strip(),
            "/srv-stack/media",
            "/srv-stack/data/torrents",
            "/srv-stack/data/usenet",
            "/srv-stack",
            config_root,
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                monitor_path = candidate
                break
    if not monitor_path:
        monitor_path = config_root
    max_used_percent = _to_float(guard_cfg.get("max_used_percent"), 65.0)
    target_used_percent = _to_float(guard_cfg.get("target_used_percent"), 58.0)
    if target_used_percent is None:
        target_used_percent = 58.0
    if max_used_percent is None:
        max_used_percent = 65.0
    target_used_percent = max(0.0, min(target_used_percent, 99.0))
    max_used_percent = max(target_used_percent, min(max_used_percent, 99.0))

    try:
        used_pct, total, avail = _disk_usage_percent(monitor_path)
    except Exception as exc:
        raise RuntimeError(
            f"Disk guardrails: failed reading filesystem usage at '{monitor_path}': {exc}"
        ) from exc

    log(
        "[INFO] Disk guardrails: usage check "
        f"(path={monitor_path}, used={used_pct:.2f}%, total={_fmt_bytes(total)}, "
        f"available={_fmt_bytes(avail)}, max={max_used_percent:.2f}%, target={target_used_percent:.2f}%)"
    )
    if used_pct <= max_used_percent:
        log("[OK] Disk guardrails: usage is within threshold.")
        return

    qbit_cleanup_cfg = guard_cfg.get("qbit_cleanup")
    if not isinstance(qbit_cleanup_cfg, dict):
        qbit_cleanup_cfg = {}
    if not bool_cfg(qbit_cleanup_cfg, "enabled", True):
        log(
            "[WARN] Disk guardrails: usage above threshold but qB cleanup is disabled "
            "(disk_guardrails.qbit_cleanup.enabled=false)."
        )
        return

    qbit_url = normalize_url(qbit_cfg.get("url", "http://qbittorrent:8080"))
    min_age_hours = _to_float(qbit_cleanup_cfg.get("min_completion_age_hours"), 36.0) or 36.0
    min_ratio = _to_float(qbit_cleanup_cfg.get("min_ratio"), 1.0)
    min_seed_minutes = to_int(qbit_cleanup_cfg.get("min_seeding_time_minutes"), 720)
    max_delete_per_run = to_int(qbit_cleanup_cfg.get("max_delete_per_run"), 80)
    categories = [
        str(x).strip() for x in coerce_list(qbit_cleanup_cfg.get("categories")) if str(x).strip()
    ]
    delete_files = bool_cfg(qbit_cleanup_cfg, "delete_files", True)

    opener = qbit_login(qbit_url, qb_username, qb_password)
    torrents = qbit_list_completed_torrents(opener, qbit_url)
    now = int(time.time())

    candidates = []
    for item in torrents:
        if not isinstance(item, dict):
            continue
        thash = str(item.get("hash") or "").strip()
        if not thash:
            continue
        cat = str(item.get("category") or "").strip()
        if categories and cat not in categories:
            continue

        completion_on = to_int(item.get("completion_on"), 0) or 0
        age_hours = 0.0
        if completion_on > 0:
            age_hours = max(0.0, float(now - completion_on) / 3600.0)
        if age_hours < min_age_hours:
            continue

        ratio = _to_float(item.get("ratio"), 0.0) or 0.0
        seeding_time_minutes = int((to_int(item.get("seeding_time"), 0) or 0) / 60)
        meets_ratio = (min_ratio is None) or (ratio >= float(min_ratio))
        meets_seed = (min_seed_minutes is None) or (seeding_time_minutes >= int(min_seed_minutes))
        if not (meets_ratio or meets_seed):
            continue

        size_bytes = to_int(item.get("size"), 0) or 0
        candidates.append(
            {
                "hash": thash,
                "category": cat,
                "completion_on": completion_on,
                "size": size_bytes,
            }
        )

    candidates.sort(key=lambda x: (x.get("completion_on") or 0, x.get("size") or 0), reverse=False)
    if max_delete_per_run is not None and max_delete_per_run > 0:
        candidates = candidates[:max_delete_per_run]

    if not candidates:
        log(
            "[WARN] Disk guardrails: usage above threshold but no qB torrents matched cleanup "
            f"criteria (min_age_hours={min_age_hours}, min_ratio={min_ratio}, "
            f"min_seeding_time_minutes={min_seed_minutes}, categories={categories or 'all'})."
        )
        return

    to_delete = [c["hash"] for c in candidates]
    reclaimed_est = sum(c.get("size") or 0 for c in candidates)
    qbit_delete_torrents(opener, qbit_url, to_delete, delete_files=delete_files)
    log(
        "[OK] Disk guardrails: deleted completed qB torrents "
        f"(count={len(to_delete)}, delete_files={delete_files}, estimated_bytes={_fmt_bytes(reclaimed_est)})"
    )

    try:
        used_after, _, avail_after = _disk_usage_percent(monitor_path)
        log(
            "[INFO] Disk guardrails: usage after cleanup "
            f"(used={used_after:.2f}%, available={_fmt_bytes(avail_after)}, target={target_used_percent:.2f}%)"
        )
        if used_after > target_used_percent:
            log(
                "[WARN] Disk guardrails: still above target after cleanup. "
                "Consider stronger retention rules (Maintainerr) or larger storage."
            )
    except Exception:
        pass


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
        default="full",
        choices=["full", "jellyfin-prewarm", "jellyfin-home-rails", "media-hygiene"],
        help=(
            "Execution mode: full bootstrap, Jellyfin prewarm-only, "
            "Jellyfin home-rails-only, or media-hygiene-only"
        ),
    )
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    prowlarr_url = normalize_url(cfg["prowlarr_url"])
    arr_apps = cfg.get("arr_apps", [])
    download_clients_cfg = cfg.get("download_clients") or {}
    qbit_cfg = download_clients_cfg.get("qbittorrent", {})
    sab_cfg = download_clients_cfg.get("sabnzbd", {})
    arr_download_handling_cfg = cfg.get("arr_download_handling") or {}
    arr_media_management_cfg = cfg.get("arr_media_management") or {}
    arr_quality_upgrade_cfg = cfg.get("arr_quality_upgrade") or {}
    arr_discovery_lists_cfg = ArrDiscoveryListsConfig.from_dict(
        cfg.get("arr_discovery_lists") or {}
    )
    jellyseerr_cfg = cfg.get("jellyseerr") or {}
    homepage_cfg = cfg.get("homepage") or {}
    bazarr_cfg = cfg.get("bazarr") or {}
    jellyfin_libraries_cfg = cfg.get("jellyfin_libraries") or {}
    jellyfin_livetv_cfg = cfg.get("jellyfin_livetv") or {}
    jellyfin_plugins_cfg = cfg.get("jellyfin_plugins") or {}
    jellyfin_playback_cfg = cfg.get("jellyfin_playback") or {}
    jellyfin_home_rails_cfg = cfg.get("jellyfin_home_rails") or {}
    jellyfin_auto_collections_cfg = cfg.get("jellyfin_auto_collections") or {}
    jellyfin_prewarm_cfg = cfg.get("jellyfin_prewarm") or {}
    disk_guardrails_cfg = cfg.get("disk_guardrails") or {}
    media_hygiene_cfg = cfg.get("media_hygiene") or {}
    maintainerr_cfg = cfg.get("maintainerr") or {}
    adapter_hooks_cfg = deep_merge_objects(
        load_bootstrap_default_json(
            "adapter_hooks.json",
            {
                "before_common_steps": {
                    "readarr": "bootstrap_services.servarr_adapters:readarr_before_common_steps"
                }
            },
        ),
        cfg.get("adapter_hooks") or {},
    )
    app_auth_cfg = cfg.get("app_auth") or {}
    prowlarr_indexers = cfg.get("prowlarr_indexers", [])
    fully_preconfigured = env_truthy("FULLY_PRECONFIGURED", False)
    if fully_preconfigured and not app_auth_cfg:
        app_auth_cfg = {
            "enabled": True,
            "method": "Forms",
            "required": "Enabled",
            "username_env": "STACK_ADMIN_USERNAME",
            "password_env": "STACK_ADMIN_PASSWORD",
            "include": ["Sonarr", "Radarr", "Lidarr", "Readarr", "Prowlarr"],
        }
    auto_indexers = bool(
        cfg.get("prowlarr_auto_add_tested_indexers", False) or args.auto_prowlarr_indexers
    )
    configure_qbit_arr_clients = bool(qbit_cfg.get("configure_arr_clients", False))
    configure_sab_arr_clients = bool(sab_cfg.get("configure_arr_clients", False))
    sab_remote_path_mappings = (
        build_sab_remote_path_mappings(sab_cfg) if configure_sab_arr_clients else []
    )
    configure_arr_clients = configure_qbit_arr_clients or configure_sab_arr_clients
    configure_arr_media_management = bool_cfg(arr_media_management_cfg, "enabled", True)
    configure_arr_quality_upgrade = bool_cfg(arr_quality_upgrade_cfg, "enabled", False)
    configure_arr_download_handling = bool_cfg(arr_download_handling_cfg, "enabled", True)
    configure_arr_discovery_lists = arr_discovery_lists_cfg.enabled
    set_qbit_categories = bool(qbit_cfg.get("set_categories_in_qbit", False))
    qbit_login_required = bool(qbit_cfg.get("login_required", fully_preconfigured))
    refresh_health_after_bootstrap = bool(cfg.get("refresh_health_after_bootstrap", True))
    configure_jellyseerr_services = bool_cfg(jellyseerr_cfg, "enabled", False)
    jellyseerr_required = bool_cfg(jellyseerr_cfg, "required", False)
    configure_homepage_services = bool_cfg(homepage_cfg, "enabled", False) or bool(
        coerce_list(homepage_cfg.get("hosts"))
    )
    homepage_required = bool_cfg(homepage_cfg, "required", False)
    configure_bazarr_integration = bool_cfg(bazarr_cfg, "enabled", False)
    bazarr_required = bool_cfg(bazarr_cfg, "required", False)
    configure_jellyfin_libraries = bool_cfg(jellyfin_libraries_cfg, "enabled", False)
    jellyfin_libraries_required = bool_cfg(jellyfin_libraries_cfg, "required", False)
    configure_jellyfin_livetv = bool_cfg(jellyfin_livetv_cfg, "enabled", False)
    jellyfin_livetv_required = bool_cfg(jellyfin_livetv_cfg, "required", False)
    configure_jellyfin_plugins = bool_cfg(jellyfin_plugins_cfg, "enabled", False)
    jellyfin_plugins_required = bool_cfg(jellyfin_plugins_cfg, "required", False)
    configure_jellyfin_playback = bool_cfg(jellyfin_playback_cfg, "enabled", False)
    jellyfin_playback_required = bool_cfg(jellyfin_playback_cfg, "required", False)
    configure_jellyfin_home_rails = bool_cfg(jellyfin_home_rails_cfg, "enabled", False) or bool_cfg(
        jellyfin_home_rails_cfg, "cleanup_collections_when_disabled", False
    )
    jellyfin_home_rails_required = bool_cfg(jellyfin_home_rails_cfg, "required", False)
    configure_auto_collections = bool_cfg(jellyfin_auto_collections_cfg, "enabled", False)
    auto_collections_required = bool_cfg(jellyfin_auto_collections_cfg, "required", False)
    configure_disk_guardrails = bool_cfg(disk_guardrails_cfg, "enabled", False)
    disk_guardrails_required = bool_cfg(disk_guardrails_cfg, "required", False)
    configure_jellyfin_prewarm = bool_cfg(jellyfin_prewarm_cfg, "enabled", False)
    jellyfin_prewarm_required = bool_cfg(jellyfin_prewarm_cfg, "required", False)
    configure_media_hygiene = bool_cfg(media_hygiene_cfg, "enabled", False)
    media_hygiene_required = bool_cfg(media_hygiene_cfg, "required", False)
    configure_maintainerr_policy = bool_cfg(maintainerr_cfg, "enabled", False)
    maintainerr_required = bool_cfg(maintainerr_cfg, "required", False)
    trigger_sync = bool(cfg.get("trigger_indexer_sync", True))

    prowlarr_key = ""
    app_keys = {}
    if args.mode in ("full", "media-hygiene"):
        for app in arr_apps:
            app_dir = app["implementation"].lower()
            app_keys[app["implementation"]] = read_api_key(args.config_root, app_dir)
    if args.mode == "full":
        prowlarr_key = read_api_key(args.config_root, "prowlarr")

    qb_user = (
        os.environ.get("QBITTORRENT_USERNAME")
        or os.environ.get("STACK_ADMIN_USERNAME")
        or "mediaadmin"
    )
    qb_pass = (
        os.environ.get("QBITTORRENT_PASSWORD")
        or os.environ.get("STACK_ADMIN_PASSWORD")
        or "media-stack-admin"
    )
    qbit_login_ok = False
    sab_api_key = ""
    sab_username = (
        os.environ.get(str(sab_cfg.get("username_env", "SABNZBD_USERNAME"))) or ""
    ).strip()
    sab_password = (
        os.environ.get(str(sab_cfg.get("password_env", "SABNZBD_PASSWORD"))) or ""
    ).strip()

    log(
        "[INFO] Bootstrap plan: "
        f"mode={args.mode}, "
        f"arr_apps={len(arr_apps)}, "
        f"prowlarr_indexers={len(prowlarr_indexers)}, "
        f"auto_indexers={auto_indexers}, "
        f"configure_arr_clients={configure_arr_clients}, "
        f"configure_qbit_arr_clients={configure_qbit_arr_clients}, "
        f"configure_sab_arr_clients={configure_sab_arr_clients}, "
        f"sab_remote_path_mappings={len(sab_remote_path_mappings)}, "
        f"configure_arr_media_management={configure_arr_media_management}, "
        f"configure_arr_quality_upgrade={configure_arr_quality_upgrade}, "
        f"configure_arr_download_handling={configure_arr_download_handling}, "
        f"configure_arr_discovery_lists={configure_arr_discovery_lists}, "
        f"set_qbit_categories={set_qbit_categories}, "
        f"qbit_login_required={qbit_login_required}, "
        f"refresh_health_after_bootstrap={refresh_health_after_bootstrap}, "
        f"app_auth_enabled={bool_cfg(app_auth_cfg, 'enabled', False)}, "
        f"configure_homepage={configure_homepage_services}, "
        f"configure_bazarr={configure_bazarr_integration}, "
        f"configure_jellyseerr={configure_jellyseerr_services}, "
        f"configure_jellyfin_libraries={configure_jellyfin_libraries}, "
        f"configure_jellyfin_livetv={configure_jellyfin_livetv}, "
        f"configure_jellyfin_plugins={configure_jellyfin_plugins}, "
        f"configure_jellyfin_playback={configure_jellyfin_playback}, "
        f"configure_jellyfin_home_rails={configure_jellyfin_home_rails}, "
        f"configure_auto_collections={configure_auto_collections}, "
        f"configure_disk_guardrails={configure_disk_guardrails}, "
        f"configure_jellyfin_prewarm={configure_jellyfin_prewarm}, "
        f"configure_media_hygiene={configure_media_hygiene}, "
        f"configure_maintainerr_policy={configure_maintainerr_policy}, "
        f"jellyfin_livetv_tuners={len(coerce_list(jellyfin_livetv_cfg.get('tuners')))}, "
        f"jellyfin_livetv_guides={len(coerce_list(jellyfin_livetv_cfg.get('guides')))}, "
        f"fully_preconfigured={fully_preconfigured}, "
        f"trigger_sync={trigger_sync}"
    )

    if args.mode == "jellyfin-prewarm":
        ensure_jellyfin_prewarm(cfg, args.config_root, args.wait_timeout)
        log("[OK] Jellyfin prewarm mode complete.")
        return

    if args.mode == "jellyfin-home-rails":
        ensure_jellyfin_home_rails(cfg, args.config_root, args.wait_timeout)
        log("[OK] Jellyfin home rails mode complete.")
        return

    if args.mode == "media-hygiene":
        for app in arr_apps:
            try:
                wait_for_service(app["name"], app["url"], "/ping", args.wait_timeout)
            except Exception as exc:
                log(
                    f"[WARN] Media hygiene mode: service wait skipped for {app.get('name')} ({exc})"
                )
        run_media_hygiene(
            cfg,
            args.config_root,
            arr_apps,
            app_keys,
            qbit_cfg,
            qb_user,
            qb_pass,
        )
        try:
            ensure_maintainerr_policy(cfg, args.config_root)
        except Exception as exc:
            if maintainerr_required:
                raise
            log(
                f"[WARN] Maintainerr policy: automation skipped ({exc}). "
                "Set maintainerr.required=true to fail the bootstrap instead."
            )
        log("[OK] Media hygiene mode complete.")
        return

    if configure_maintainerr_policy:
        try:
            ensure_maintainerr_policy(cfg, args.config_root)
        except Exception as exc:
            if maintainerr_required:
                raise
            log(
                f"[WARN] Maintainerr policy: automation skipped ({exc}). "
                "Set maintainerr.required=true to fail the bootstrap instead."
            )

    if configure_homepage_services:
        try:
            ensure_homepage_services_config(cfg, args.config_root)
        except Exception as exc:
            if homepage_required:
                raise
            log(
                f"[WARN] Homepage: config-as-code bootstrap skipped ({exc}). "
                "Set homepage.required=true to fail the bootstrap instead."
            )

    wait_for_service("Prowlarr", prowlarr_url, "/ping", args.wait_timeout)
    prowlarr_api_base = detect_arr_api_base("Prowlarr", prowlarr_url, prowlarr_key)
    try:
        ensure_app_auth_settings(
            "Prowlarr",
            "Prowlarr",
            prowlarr_url,
            prowlarr_api_base,
            prowlarr_key,
            app_auth_cfg,
        )
    except Exception as exc:
        if bool_cfg(app_auth_cfg, "fail_on_error", False):
            raise
        log(f"[WARN] Prowlarr: auth bootstrap skipped ({exc})")
    for app in arr_apps:
        wait_for_service(app["name"], app["url"], "/ping", args.wait_timeout)

    if configure_qbit_arr_clients or set_qbit_categories:
        qbit_url = normalize_url(qbit_cfg.get("url", "http://qbittorrent:8080"))
        wait_for_service("qBittorrent", qbit_url, "/", args.wait_timeout)
        try:
            qbit_login(qbit_url, qb_user, qb_pass)
            qbit_login_ok = True
            log("[OK] qBittorrent: authenticated for bootstrap automation")
        except Exception as exc:
            if qbit_login_required:
                raise RuntimeError(
                    "qBittorrent login failed with secret credentials. "
                    "Update QBITTORRENT_USERNAME/QBITTORRENT_PASSWORD."
                ) from exc
            log(
                "[WARN] qBittorrent login failed. "
                "Continuing because qbit login is not required in config "
                "(set download_clients.qbittorrent.login_required=true to fail hard)."
            )

    if configure_sab_arr_clients:
        sab_url = normalize_url(sab_cfg.get("url", "http://sabnzbd:8080"))
        wait_for_service("SABnzbd", sab_url, "/", args.wait_timeout)
        sab_api_key = read_sabnzbd_api_key(args.config_root, sab_cfg)
        if sab_api_key:
            log("[OK] SABnzbd: resolved API key for bootstrap automation")
            ensure_sabnzbd_defaults(sab_cfg, sab_api_key)
            if bool_cfg(sab_cfg, "set_categories_in_sab", True):
                ensure_sabnzbd_categories(arr_apps, sab_cfg, sab_api_key)
        elif bool_cfg(sab_cfg, "api_key_required", fully_preconfigured):
            raise RuntimeError(
                "SABnzbd API key not found. Set SABNZBD_API_KEY or ensure "
                "download_clients.sabnzbd.api_key_config_path points to sabnzbd.ini."
            )
        else:
            log(
                "[WARN] SABnzbd API key not found; skipping Arr -> SABnzbd "
                "download client wiring. Set SABNZBD_API_KEY to enforce."
            )

    if set_qbit_categories and qbit_login_ok:
        setup_qbit_categories(arr_apps, qbit_cfg, qb_user, qb_pass)

    _servarr_pipeline_service().run(
        ServarrPipelineInputs(
            cfg=cfg,
            arr_apps=arr_apps,
            app_keys=app_keys,
            prowlarr_url=prowlarr_url,
            prowlarr_key=prowlarr_key,
            app_auth_cfg=app_auth_cfg,
            arr_media_management_cfg=arr_media_management_cfg,
            arr_download_handling_cfg=arr_download_handling_cfg,
            arr_quality_upgrade_cfg=arr_quality_upgrade_cfg,
            qbit_cfg=qbit_cfg,
            qbit_auth=ClientAuth(
                username=qb_user,
                password=qb_pass,
            ),
            sab_cfg=sab_cfg,
            sab_auth=ClientAuth(
                username=sab_username,
                password=sab_password,
            ),
            sab_remote_path_mappings=sab_remote_path_mappings,
            adapter_hooks_cfg=adapter_hooks_cfg,
            run_cfg=ServarrRunConfig(
                configure_arr_media_management=configure_arr_media_management,
                configure_arr_download_handling=configure_arr_download_handling,
                configure_arr_quality_upgrade=configure_arr_quality_upgrade,
                configure_arr_discovery_lists=configure_arr_discovery_lists,
                configure_qbit_arr_clients=configure_qbit_arr_clients,
                qbit_login_ok=qbit_login_ok,
                configure_sab_arr_clients=configure_sab_arr_clients,
                sab_api_key=sab_api_key,
                refresh_health_after_bootstrap=refresh_health_after_bootstrap,
            ),
        )
    )

    if configure_bazarr_integration:
        try:
            ensure_bazarr_arr_integration(
                cfg, args.config_root, arr_apps, app_keys, args.wait_timeout
            )
        except Exception as exc:
            if bazarr_required:
                raise
            log(
                f"[WARN] Bazarr: integration bootstrap skipped ({exc}). "
                "Set bazarr.required=true to fail the bootstrap instead."
            )

    if configure_jellyseerr_services:
        try:
            configure_jellyseerr(cfg, arr_apps, app_keys, args.config_root, args.wait_timeout)
        except Exception as exc:
            if jellyseerr_required:
                raise
            log(
                f"[WARN] Jellyseerr: automation skipped ({exc}). "
                "Set jellyseerr.required=true to fail the bootstrap instead."
            )

    if configure_jellyfin_livetv:
        try:
            ensure_jellyfin_livetv(cfg, args.config_root, args.wait_timeout)
        except Exception as exc:
            if jellyfin_livetv_required:
                raise
            log(
                f"[WARN] Jellyfin Live TV: automation skipped ({exc}). "
                "Set jellyfin_livetv.required=true to fail the bootstrap instead."
            )

    if configure_jellyfin_libraries:
        try:
            ensure_jellyfin_libraries(cfg, args.config_root, args.wait_timeout)
        except Exception as exc:
            if jellyfin_libraries_required:
                raise
            log(
                f"[WARN] Jellyfin libraries: automation skipped ({exc}). "
                "Set jellyfin_libraries.required=true to fail the bootstrap instead."
            )

    if configure_jellyfin_plugins:
        try:
            ensure_jellyfin_plugins(cfg, args.config_root, args.wait_timeout)
        except Exception as exc:
            if jellyfin_plugins_required:
                raise
            log(
                f"[WARN] Jellyfin plugins: automation skipped ({exc}). "
                "Set jellyfin_plugins.required=true to fail the bootstrap instead."
            )

    if configure_jellyfin_playback:
        try:
            ensure_jellyfin_playback_defaults(cfg, args.config_root, args.wait_timeout)
        except Exception as exc:
            if jellyfin_playback_required:
                raise
            log(
                f"[WARN] Jellyfin playback: automation skipped ({exc}). "
                "Set jellyfin_playback.required=true to fail the bootstrap instead."
            )

    if configure_jellyfin_home_rails:
        try:
            ensure_jellyfin_home_rails(cfg, args.config_root, args.wait_timeout)
        except Exception as exc:
            if jellyfin_home_rails_required:
                raise
            log(
                f"[WARN] Jellyfin home rails: automation skipped ({exc}). "
                "Set jellyfin_home_rails.required=true to fail the bootstrap instead."
            )

    if configure_auto_collections:
        try:
            ensure_jellyfin_auto_collections_config(cfg, args.config_root, args.wait_timeout)
        except Exception as exc:
            if auto_collections_required:
                raise
            log(
                f"[WARN] Jellyfin Auto Collections: automation skipped ({exc}). "
                "Set jellyfin_auto_collections.required=true to fail the bootstrap instead."
            )

    if configure_disk_guardrails:
        try:
            enforce_disk_guardrails(
                cfg,
                args.config_root,
                qbit_cfg,
                qb_user,
                qb_pass,
            )
        except Exception as exc:
            if disk_guardrails_required:
                raise
            log(
                f"[WARN] Disk guardrails: automation skipped ({exc}). "
                "Set disk_guardrails.required=true to fail the bootstrap instead."
            )

    if configure_media_hygiene:
        try:
            run_media_hygiene(
                cfg,
                args.config_root,
                arr_apps,
                app_keys,
                qbit_cfg,
                qb_user,
                qb_pass,
            )
        except Exception as exc:
            if media_hygiene_required:
                raise
            log(
                f"[WARN] Media hygiene: automation skipped ({exc}). "
                "Set media_hygiene.required=true to fail the bootstrap instead."
            )

    if configure_jellyfin_prewarm:
        try:
            ensure_jellyfin_prewarm(cfg, args.config_root, args.wait_timeout)
        except Exception as exc:
            if jellyfin_prewarm_required:
                raise
            log(
                f"[WARN] Jellyfin prewarm: automation skipped ({exc}). "
                "Set jellyfin_prewarm.required=true to fail the bootstrap instead."
            )

    indexer_failures = 0
    for indexer in prowlarr_indexers:
        idx_name = indexer.get("name") or indexer.get("implementation") or "unnamed-indexer"
        try:
            ensure_prowlarr_indexer(prowlarr_url, prowlarr_key, indexer)
        except Exception as exc:
            indexer_failures += 1
            log(f"[WARN] Prowlarr: failed indexer '{idx_name}': {exc}")

    if indexer_failures:
        if bool(cfg.get("fail_on_indexer_error", False)):
            raise RuntimeError(
                f"Prowlarr: {indexer_failures} configured indexer(s) failed and fail_on_indexer_error=true."
            )
        log(
            f"[WARN] Prowlarr: {indexer_failures} configured indexer(s) failed; "
            "continuing because fail_on_indexer_error is false."
        )

    if auto_indexers:
        auto_add_tested_indexers(prowlarr_url, prowlarr_key)

    if trigger_sync:
        trigger_prowlarr_sync(prowlarr_url, prowlarr_key)

    log("[OK] Bootstrap complete.")


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
