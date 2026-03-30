#!/usr/bin/env python3
import argparse
from collections import defaultdict
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from http import cookiejar
from pathlib import Path
from urllib import error, parse, request
from bootstrap_lib.common import (
    bool_cfg as _lib_bool_cfg,
    coerce_list as _lib_coerce_list,
    env_truthy as _lib_env_truthy,
    normalize_base_path as _lib_normalize_base_path,
    normalize_url as _lib_normalize_url,
    parse_service_url as _lib_parse_service_url,
    to_int as _lib_to_int,
)
from bootstrap_lib.http_client import http_request as _lib_http_request
from bootstrap_lib.servarr import (
    choose_profile as _lib_choose_profile,
    choose_root_folder as _lib_choose_root_folder,
    find_existing_servarr as _lib_find_existing_servarr,
    normalize_remote_path_mappings as _lib_normalize_remote_path_mappings,
)
from bootstrap_lib.homepage import (
    DEFAULT_HOSTS as _lib_default_homepage_hosts,
    render_services_yaml as _lib_render_homepage_services_yaml,
)
from bootstrap_lib.bazarr import apply_scalar_updates as _lib_bazarr_apply_scalar_updates
from bootstrap_lib.jellyfin import (
    apply_artwork_profile as _lib_jellyfin_apply_artwork_profile,
    reorder_provider_names as _lib_jellyfin_reorder_provider_names,
)


def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] {msg}", flush=True)


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
    pattern = r'tvg-id=\"[^\"]*\"'
    replacement = f'tvg-id=\"{new_id}\"'
    if re.search(pattern, extinf_line):
        return re.sub(pattern, replacement, extinf_line, count=1)
    return extinf_line


def _transform_m3u_for_guide(
    m3u_text, normalize_tvg_id_suffix=False, guide_channel_ids=None
):
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
        tvg_id_match = re.search(r'tvg-id=\"([^\"]*)\"', extinf)
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


def prepare_jellyfin_m3u_tuner_url(
    tuner, guides, config_root, guide_channel_ids_cache=None
):
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
        tuner.get("materialized_output_path")
        or f"jellyfin/livetv-tuners/{source_hash}.m3u"
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
    env_name = str(sab_cfg.get("api_key_env", "SABNZBD_API_KEY")).strip() or "SABNZBD_API_KEY"
    env_value = (os.environ.get(env_name) or "").strip()
    if env_value:
        log(f"[OK] SABnzbd: using API key from env {env_name}")
        return env_value

    ini_rel_path = sab_cfg.get("api_key_config_path", "sabnzbd/sabnzbd.ini")
    ini_path = resolve_path(config_root, ini_rel_path)
    if not ini_path.exists():
        return ""

    text = ini_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\s*api_key\s*=\s*(\S+)\s*$", text, flags=re.MULTILINE)
    if match:
        log(f"[OK] SABnzbd: discovered API key from {ini_path}")
        return match.group(1).strip()

    return ""


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
        log(
            "[OK] Jellyfin: discovered API key from db "
            f"(source key name='{source_name}')"
        )
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
            f"Jellyfin Live TV: failed deleting {entity} {entity_id} "
            f"(HTTP {status}): {body}"
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
    live_cfg = cfg.get("jellyfin_livetv") or {}
    if not bool_cfg(live_cfg, "enabled", False):
        return

    tuners = coerce_list(live_cfg.get("tuners"))
    guides = coerce_list(live_cfg.get("guides"))
    refresh_on_bootstrap = bool_cfg(live_cfg, "refresh_on_bootstrap", True)
    cleanup_duplicates = bool_cfg(live_cfg, "cleanup_duplicates", True)
    recreate_managed_guides = bool_cfg(live_cfg, "recreate_managed_guides", True)
    prune_unmanaged_tuners = bool_cfg(live_cfg, "prune_unmanaged_tuners", True)
    prune_unmanaged_guides = bool_cfg(live_cfg, "prune_unmanaged_guides", True)
    fallback_enable_all_tuners = bool_cfg(
        live_cfg, "fallback_enable_all_tuners_when_mapping_missing", True
    )
    if not tuners and not guides and not refresh_on_bootstrap:
        log("[WARN] Jellyfin Live TV: enabled but no tuners/guides configured.")
        return

    prepared_tuners = []
    guide_channel_ids_cache = {}
    for tuner in tuners:
        if not isinstance(tuner, dict):
            raise RuntimeError(
                f"Jellyfin Live TV: each tuner entry must be an object, got: {tuner}"
            )
        source_url = str(tuner.get("url") or "").strip()
        if not source_url:
            raise RuntimeError("Jellyfin Live TV: tuner entry missing required field 'url'")

        effective_url = prepare_jellyfin_m3u_tuner_url(
            tuner,
            guides,
            config_root,
            guide_channel_ids_cache=guide_channel_ids_cache,
        )
        prepared = dict(tuner)
        prepared["_effective_url"] = effective_url
        prepared_tuners.append(prepared)

    desired_tuner_keys = set()
    for tuner in prepared_tuners:
        desired_tuner_keys.add(
            (
                str(tuner.get("type", "m3u")).strip().lower(),
                str(tuner.get("_effective_url") or "").strip(),
            )
        )
    desired_guide_keys = set()
    for guide in guides:
        if not isinstance(guide, dict):
            continue
        guide_path = str(guide.get("path") or "").strip()
        if not guide_path:
            continue
        guide_type = str(guide.get("type", "xmltv")).strip().lower()
        desired_guide_keys.add((guide_type, guide_path))

    jellyfin_url = normalize_url(live_cfg.get("url", "http://jellyfin:8096"))
    wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

    jellyfin_api_key = resolve_jellyfin_api_key(live_cfg, config_root)
    if not jellyfin_api_key:
        raise RuntimeError(
            "Jellyfin Live TV: API key unavailable. Set JELLYFIN_API_KEY or keep "
            "jellyfin_livetv.auto_discover_api_key_from_db=true and ensure "
            "jellyfin/data/jellyfin.db contains a usable key."
        )

    status, _, body = jellyfin_request(jellyfin_url, "/LiveTv/Info", jellyfin_api_key)
    if status != 200:
        raise RuntimeError(
            f"Jellyfin Live TV: failed auth/health check against /LiveTv/Info "
            f"(HTTP {status}): {body}"
        )

    added_tuners = 0
    added_guides = 0
    state = {
        "tuner_keys": set(),
        "guide_keys": set(),
        "tuner_ids_by_key": {},
    }

    if tuners or guides:
        state = load_jellyfin_livetv_state(config_root, live_cfg)
        total_existing_tuners = sum(
            len(items) for items in (state.get("tuners_by_key") or {}).values()
        )
        total_existing_guides = sum(
            len(items) for items in (state.get("guides_by_key") or {}).values()
        )
        log(
            "[INFO] Jellyfin Live TV: state before reconcile "
            f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
            f"tuners={total_existing_tuners}, "
            f"guide_keys={len(state.get('guide_keys') or [])}, "
            f"guides={total_existing_guides}, "
            f"source={state.get('source_path', 'unknown')})"
        )
        cleanup_changed = False
        recreate_changed = False

        if cleanup_duplicates:
            for tuner_key, tuner_entries in (state.get("tuners_by_key") or {}).items():
                if len(tuner_entries) <= 1:
                    continue
                for duplicate in tuner_entries[1:]:
                    tuner_id = str((duplicate or {}).get("id") or "").strip()
                    if not tuner_id:
                        continue
                    delete_jellyfin_livetv_entity(
                        jellyfin_url, jellyfin_api_key, "tuner", tuner_id
                    )
                    cleanup_changed = True
                    log(
                        "[INFO] Jellyfin Live TV: removed duplicate tuner "
                        f"(type={tuner_key[0]}, url={tuner_key[1]}, id={tuner_id})"
                    )

            for guide_key, guide_entries in (state.get("guides_by_key") or {}).items():
                if len(guide_entries) <= 1:
                    continue
                for duplicate in guide_entries[1:]:
                    guide_id = str((duplicate or {}).get("id") or "").strip()
                    if not guide_id:
                        continue
                    delete_jellyfin_livetv_entity(
                        jellyfin_url, jellyfin_api_key, "guide", guide_id
                    )
                    cleanup_changed = True
                    log(
                        "[INFO] Jellyfin Live TV: removed duplicate guide "
                        f"(type={guide_key[0]}, path={guide_key[1]}, id={guide_id})"
                    )

        if cleanup_changed:
            state = load_jellyfin_livetv_state(config_root, live_cfg)
            total_existing_tuners = sum(
                len(items) for items in (state.get("tuners_by_key") or {}).values()
            )
            total_existing_guides = sum(
                len(items) for items in (state.get("guides_by_key") or {}).values()
            )
            log(
                "[INFO] Jellyfin Live TV: state after cleanup "
                f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
                f"tuners={total_existing_tuners}, "
                f"guide_keys={len(state.get('guide_keys') or [])}, "
                f"guides={total_existing_guides}, "
                f"source={state.get('source_path', 'unknown')})"
            )

        if recreate_managed_guides and guides:
            for guide in guides:
                if not isinstance(guide, dict):
                    continue
                guide_path = str(guide.get("path") or "").strip()
                if not guide_path:
                    continue
                guide_type = str(guide.get("type", "xmltv")).strip().lower()
                guide_key = (guide_type, guide_path)
                for existing_guide in (state.get("guides_by_key") or {}).get(guide_key, []):
                    guide_id = str((existing_guide or {}).get("id") or "").strip()
                    if not guide_id:
                        continue
                    delete_jellyfin_livetv_entity(
                        jellyfin_url, jellyfin_api_key, "guide", guide_id
                    )
                    recreate_changed = True
                    log(
                        "[INFO] Jellyfin Live TV: recreated managed guide binding "
                        f"(type={guide_type}, path={guide_path}, id={guide_id})"
                    )

        if recreate_changed:
            state = load_jellyfin_livetv_state(config_root, live_cfg)
            total_existing_tuners = sum(
                len(items) for items in (state.get("tuners_by_key") or {}).values()
            )
            total_existing_guides = sum(
                len(items) for items in (state.get("guides_by_key") or {}).values()
            )
            log(
                "[INFO] Jellyfin Live TV: state after cleanup "
                f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
                f"tuners={total_existing_tuners}, "
                f"guide_keys={len(state.get('guide_keys') or [])}, "
                f"guides={total_existing_guides}, "
                f"source={state.get('source_path', 'unknown')})"
            )

        prune_changed = False
        if prune_unmanaged_tuners:
            for tuner_key, tuner_entries in (state.get("tuners_by_key") or {}).items():
                if tuner_key in desired_tuner_keys:
                    continue
                for entry in tuner_entries:
                    tuner_id = str((entry or {}).get("id") or "").strip()
                    if not tuner_id:
                        continue
                    delete_jellyfin_livetv_entity(jellyfin_url, jellyfin_api_key, "tuner", tuner_id)
                    prune_changed = True
                    log(
                        "[INFO] Jellyfin Live TV: pruned unmanaged tuner "
                        f"(type={tuner_key[0]}, url={tuner_key[1]}, id={tuner_id})"
                    )

        if prune_unmanaged_guides:
            for guide_key, guide_entries in (state.get("guides_by_key") or {}).items():
                if guide_key in desired_guide_keys:
                    continue
                for entry in guide_entries:
                    guide_id = str((entry or {}).get("id") or "").strip()
                    if not guide_id:
                        continue
                    delete_jellyfin_livetv_entity(jellyfin_url, jellyfin_api_key, "guide", guide_id)
                    prune_changed = True
                    log(
                        "[INFO] Jellyfin Live TV: pruned unmanaged guide "
                        f"(type={guide_key[0]}, path={guide_key[1]}, id={guide_id})"
                    )

        if prune_changed:
            state = load_jellyfin_livetv_state(config_root, live_cfg)
            total_existing_tuners = sum(
                len(items) for items in (state.get("tuners_by_key") or {}).values()
            )
            total_existing_guides = sum(
                len(items) for items in (state.get("guides_by_key") or {}).values()
            )
            log(
                "[INFO] Jellyfin Live TV: state after pruning unmanaged entries "
                f"(tuner_keys={len(state.get('tuner_keys') or [])}, "
                f"tuners={total_existing_tuners}, "
                f"guide_keys={len(state.get('guide_keys') or [])}, "
                f"guides={total_existing_guides}, "
                f"source={state.get('source_path', 'unknown')})"
            )

        for tuner in prepared_tuners:
            tuner_url = str(tuner.get("_effective_url") or tuner.get("url") or "").strip()
            tuner_type_requested = str(tuner.get("type", "m3u")).strip()
            tuner_type = resolve_jellyfin_tuner_type_id(
                jellyfin_url, jellyfin_api_key, tuner_type_requested
            )
            key = (tuner_type.lower(), tuner_url)
            if key in state["tuner_keys"]:
                log(f"[OK] Jellyfin Live TV: tuner already exists ({tuner_type} {tuner_url})")
                continue

            payload = {
                "Type": tuner_type,
                "Url": tuner_url,
                "FriendlyName": str(
                    tuner.get("friendly_name")
                    or tuner.get("name")
                    or f"{tuner_type.upper()} {tuner_url}"
                ),
                "ImportFavoritesOnly": bool(tuner.get("import_favorites_only", False)),
                "AllowHWTranscoding": bool(tuner.get("allow_hw_transcoding", True)),
                "AllowFmp4TranscodingContainer": bool(
                    tuner.get("allow_fmp4_transcoding_container", False)
                ),
                "AllowStreamSharing": bool(tuner.get("allow_stream_sharing", True)),
                "EnableStreamLooping": bool(tuner.get("enable_stream_looping", False)),
                "IgnoreDts": bool(tuner.get("ignore_dts", True)),
                "ReadAtNativeFramerate": bool(tuner.get("read_at_native_framerate", False)),
            }
            max_bitrate = to_int(tuner.get("fallback_max_streaming_bitrate"), 30000000)
            if max_bitrate is not None:
                payload["FallbackMaxStreamingBitrate"] = max_bitrate

            status, data, body = jellyfin_request(
                jellyfin_url,
                "/LiveTv/TunerHosts",
                jellyfin_api_key,
                method="POST",
                payload=payload,
            )
            if status not in (200, 201, 202):
                raise RuntimeError(
                    f"Jellyfin Live TV: failed creating tuner {tuner_url} (HTTP {status}): {body}"
                )

            created_id = str((data or {}).get("Id") or "").strip() if isinstance(data, dict) else ""
            state["tuner_keys"].add(key)
            if created_id:
                state["tuner_ids_by_key"][key] = created_id
            added_tuners += 1
            log(f"[OK] Jellyfin Live TV: added tuner ({tuner_type} {tuner_url})")

        # Refresh state from file after tuner writes in case Jellyfin adds/normalizes values.
        state = load_jellyfin_livetv_state(config_root, live_cfg)

        for guide in guides:
            if not isinstance(guide, dict):
                raise RuntimeError(
                    f"Jellyfin Live TV: each guide entry must be an object, got: {guide}"
                )

            guide_path = str(guide.get("path") or "").strip()
            if not guide_path:
                raise RuntimeError("Jellyfin Live TV: guide entry missing required field 'path'")

            guide_type = str(guide.get("type", "xmltv")).strip()
            guide_key = (guide_type.lower(), guide_path)
            if guide_key in state["guide_keys"]:
                log(f"[OK] Jellyfin Live TV: guide already exists ({guide_type} {guide_path})")
                continue

            payload = {
                "Type": guide_type,
                "Path": guide_path,
                "EnableAllTuners": bool(guide.get("enable_all_tuners", True)),
            }

            enabled_tuners = normalize_enabled_tuner_ids(guide.get("enabled_tuners"), state)
            if enabled_tuners:
                payload["EnabledTuners"] = enabled_tuners
                payload["EnableAllTuners"] = False
            elif not payload["EnableAllTuners"] and fallback_enable_all_tuners:
                payload["EnableAllTuners"] = True
                log(
                    "[WARN] Jellyfin Live TV: guide enabled_tuners resolved empty; "
                    f"falling back to EnableAllTuners=true for path={guide_path}"
                )

            optional_string_fields = {
                "username": "Username",
                "password": "Password",
                "listings_id": "ListingsId",
                "zip_code": "ZipCode",
                "country": "Country",
                "preferred_language": "PreferredLanguage",
                "user_agent": "UserAgent",
            }
            for src_key, dst_key in optional_string_fields.items():
                value = guide.get(src_key)
                if value is not None and str(value).strip():
                    payload[dst_key] = str(value).strip()

            optional_array_fields = {
                "news_categories": "NewsCategories",
                "sports_categories": "SportsCategories",
                "kids_categories": "KidsCategories",
                "movie_categories": "MovieCategories",
                "channel_mappings": "ChannelMappings",
            }
            for src_key, dst_key in optional_array_fields.items():
                if src_key in guide:
                    payload[dst_key] = coerce_list(guide.get(src_key))

            status, _, body = jellyfin_request(
                jellyfin_url,
                "/LiveTv/ListingProviders",
                jellyfin_api_key,
                method="POST",
                payload=payload,
            )
            if status not in (200, 201, 202):
                raise RuntimeError(
                    f"Jellyfin Live TV: failed creating guide {guide_path} (HTTP {status}): {body}"
                )

            state["guide_keys"].add(guide_key)
            added_guides += 1
            log(f"[OK] Jellyfin Live TV: added guide ({guide_type} {guide_path})")

    if added_tuners == 0 and added_guides == 0 and refresh_on_bootstrap:
        log("[INFO] Jellyfin Live TV: no tuner/guide changes, requesting refresh for UX consistency.")

    if added_tuners > 0 or added_guides > 0 or refresh_on_bootstrap:
        refresh_ops = [
            ("/LiveTv/RefreshChannels", "channel refresh"),
            ("/LiveTv/RefreshGuide", "guide refresh"),
        ]
        for path, label in refresh_ops:
            ok, detail = trigger_jellyfin_livetv_refresh(
                jellyfin_url, jellyfin_api_key, path, label
            )
            if ok:
                log(f"[OK] Jellyfin Live TV: {detail}")
            else:
                log(f"[WARN] Jellyfin Live TV: {detail}")

    log(
        "[OK] Jellyfin Live TV: reconcile complete "
        f"(tuners_added={added_tuners}, guides_added={added_guides})"
    )


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
                        item.get("MetadataFetcherOrder")
                        or item.get("metadataFetcherOrder")
                        or []
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
        current_by_type = {
            str(entry.get("Type") or "").strip().lower(): entry for entry in current
        }
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
            status, _, body = jellyfin_request(
                jellyfin_url, path, jellyfin_api_key, method="POST"
            )
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
                    folder_name = str(folder.get("Name") or folder.get("name") or "").strip().lower()
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
            enable_trickplay = enable_trickplay_movies if collection_key == "movies" else enable_trickplay_tv
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
            "[OK] Jellyfin playback: updated user defaults "
            f"(keys={','.join(changed_user_keys)})"
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
            status, display_payload, body = jellyfin_request(
                jellyfin_url, path, jellyfin_api_key
            )
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
                normalize_plugin_name(
                    item.get("Name") or item.get("name") or ""
                )
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
    return [
        {
            "name": "Trending",
            "path": "/Items",
            "query": {
                "includeItemTypes": "Movie",
                "recursive": "true",
                "sortBy": "PlayCount,DatePlayed",
                "sortOrder": "Descending",
            },
            "limit": 40,
        },
        {
            "name": "Top Rated",
            "path": "/Items",
            "query": {
                "includeItemTypes": "Movie",
                "recursive": "true",
                "sortBy": "CommunityRating,CriticRating",
                "sortOrder": "Descending",
                "minCommunityRating": "7",
            },
            "limit": 40,
        },
        {
            "name": "New This Week",
            "path": "/Items",
            "query": {
                "includeItemTypes": "Movie",
                "recursive": "true",
                "sortBy": "DateCreated,PremiereDate",
                "sortOrder": "Descending",
            },
            "rolling_premiere_days": 7,
            "limit": 40,
        },
        {
            "name": "Because You Watched",
            "path": "/Items/Suggestions",
            "query": {
                "mediaType": "Video",
                "type": "Movie",
            },
            "allowed_item_types": ["Movie"],
            "fallback_query": {
                "path": "/Items",
                "query": {
                    "includeItemTypes": "Movie",
                    "recursive": "true",
                    "isPlayed": "true",
                    "sortBy": "DatePlayed,CommunityRating",
                    "sortOrder": "Descending",
                },
            },
            "limit": 40,
        },
    ]


def find_jellyfin_collection_by_name(jellyfin_url, jellyfin_api_key, user_id, collection_name):
    path = jellyfin_build_query_path(
        "/Items",
        {
            "userId": user_id,
            "includeItemTypes": "BoxSet",
            "recursive": "true",
            "searchTerm": collection_name,
            "limit": "200",
        },
    )
    status, data, body = jellyfin_request(jellyfin_url, path, jellyfin_api_key)
    if status != 200:
        raise RuntimeError(
            f"Jellyfin home rails: failed listing collections (HTTP {status}): {body}"
        )
    target = str(collection_name or "").strip().lower()
    for item in jellyfin_items_from_payload(data):
        if not isinstance(item, dict):
            continue
        if str(item.get("Name") or "").strip().lower() == target:
            return str(item.get("Id") or "").strip()
    return ""


def collection_item_ids(jellyfin_url, jellyfin_api_key, user_id, collection_id):
    if not collection_id:
        return []
    path = jellyfin_build_query_path(
        "/Items",
        {
            "userId": user_id,
            "parentId": collection_id,
            # Reconcile direct members only; recursive listings can include nested
            # items and other BoxSets, which pollutes membership diffs.
            "recursive": "false",
            "limit": "5000",
        },
    )
    status, data, body = jellyfin_request(jellyfin_url, path, jellyfin_api_key)
    if status != 200:
        raise RuntimeError(
            f"Jellyfin home rails: failed listing collection items (HTTP {status}): {body}"
        )
    return normalize_item_ids(jellyfin_items_from_payload(data))


def update_collection_items(jellyfin_url, jellyfin_api_key, collection_id, to_add, to_remove):
    added = 0
    removed = 0

    for batch in chunked(to_remove, 100):
        path = jellyfin_build_query_path(
            f"/Collections/{parse.quote(collection_id, safe='')}/Items",
            {"ids": ",".join(batch)},
        )
        status, _, body = jellyfin_request(
            jellyfin_url, path, jellyfin_api_key, method="DELETE"
        )
        if status not in (200, 201, 202, 204):
            raise RuntimeError(
                f"Jellyfin home rails: failed removing collection items (HTTP {status}): {body}"
            )
        removed += len(batch)

    for batch in chunked(to_add, 100):
        path = jellyfin_build_query_path(
            f"/Collections/{parse.quote(collection_id, safe='')}/Items",
            {"ids": ",".join(batch)},
        )
        status, _, body = jellyfin_request(
            jellyfin_url, path, jellyfin_api_key, method="POST"
        )
        if status not in (200, 201, 202, 204):
            raise RuntimeError(
                f"Jellyfin home rails: failed adding collection items (HTTP {status}): {body}"
            )
        added += len(batch)

    return added, removed


def ensure_jellyfin_collection_membership(
    jellyfin_url,
    jellyfin_api_key,
    user_id,
    collection_name,
    desired_ids,
    clear_when_empty=False,
):
    desired_ids = [str(v).strip() for v in desired_ids if str(v).strip()]
    if not desired_ids and not clear_when_empty:
        return {"created": False, "added": 0, "removed": 0}

    collection_id = find_jellyfin_collection_by_name(
        jellyfin_url, jellyfin_api_key, user_id, collection_name
    )
    created = False
    if not collection_id:
        create_path = jellyfin_build_query_path(
            "/Collections",
            {
                "name": collection_name,
                "ids": ",".join(desired_ids) if desired_ids else "",
            },
        )
        status, create_data, body = jellyfin_request(
            jellyfin_url, create_path, jellyfin_api_key, method="POST"
        )
        if status not in (200, 201, 202):
            raise RuntimeError(
                f"Jellyfin home rails: failed creating collection '{collection_name}' "
                f"(HTTP {status}): {body}"
            )
        created = True
        collection_id = str(
            (create_data or {}).get("Id")
            or (create_data or {}).get("CollectionId")
            or ""
        ).strip()
        if not collection_id:
            collection_id = find_jellyfin_collection_by_name(
                jellyfin_url, jellyfin_api_key, user_id, collection_name
            )

    # Guard against self-referential collection membership.
    if collection_id:
        collection_id_norm = collection_id.lower()
        desired_ids = [item for item in desired_ids if item.lower() != collection_id_norm]

    current_ids = collection_item_ids(jellyfin_url, jellyfin_api_key, user_id, collection_id)
    current_set = {item.lower() for item in current_ids}
    desired_set = {item.lower() for item in desired_ids}

    to_add = [item for item in desired_ids if item.lower() not in current_set]
    to_remove = [item for item in current_ids if item.lower() not in desired_set]
    added, removed = update_collection_items(
        jellyfin_url, jellyfin_api_key, collection_id, to_add, to_remove
    )

    return {"created": created, "added": added, "removed": removed}


def delete_jellyfin_collection_by_name(
    jellyfin_url, jellyfin_api_key, user_id, collection_name
):
    collection_id = find_jellyfin_collection_by_name(
        jellyfin_url, jellyfin_api_key, user_id, collection_name
    )
    if not collection_id:
        return False

    status, _, body = jellyfin_request(
        jellyfin_url,
        f"/Items/{parse.quote(collection_id, safe='')}",
        jellyfin_api_key,
        method="DELETE",
    )
    if status not in (200, 202, 204):
        raise RuntimeError(
            f"Jellyfin home rails: failed deleting collection '{collection_name}' "
            f"(HTTP {status}): {body}"
        )
    return True


def run_jellyfin_rail_query(jellyfin_url, jellyfin_api_key, user_id, rail_cfg, max_items):
    def split_type_values(raw_value):
        values = []
        for value in coerce_list(raw_value):
            text = str(value or "").strip()
            if not text:
                continue
            if "," in text:
                values.extend([part.strip() for part in text.split(",") if part.strip()])
            else:
                values.append(text)
        return values

    path = str(rail_cfg.get("path") or "/Items").strip()
    query = rail_cfg.get("query") if isinstance(rail_cfg.get("query"), dict) else {}
    query = dict(query)
    query.setdefault("userId", user_id)

    limit = to_int(rail_cfg.get("limit"), max_items)
    if limit and "limit" not in query:
        query["limit"] = str(limit)

    rolling_days = to_int(rail_cfg.get("rolling_premiere_days"))
    if rolling_days and "minPremiereDate" not in query:
        min_premiere = (
            datetime.now(timezone.utc) - timedelta(days=int(rolling_days))
        ).isoformat().replace("+00:00", "Z")
        query["minPremiereDate"] = min_premiere

    full_path = jellyfin_build_query_path(path, query)
    status, data, body = jellyfin_request(jellyfin_url, full_path, jellyfin_api_key)
    if status != 200:
        raise RuntimeError(
            f"Jellyfin home rails: failed querying '{rail_cfg.get('name', path)}' "
            f"(HTTP {status}): {body}"
        )

    items = jellyfin_items_from_payload(data)
    allowed_types = {
        str(v).strip().lower()
        for v in coerce_list(rail_cfg.get("allowed_item_types"))
        if str(v).strip()
    }
    if not allowed_types:
        # If allowed types were not explicitly provided, infer them from the
        # query shape so BoxSet/group artifacts do not leak into rails.
        inferred = split_type_values(query.get("includeItemTypes"))
        inferred.extend(split_type_values(query.get("type")))
        allowed_types = {str(v).strip().lower() for v in inferred if str(v).strip()}
    if allowed_types:
        items = [
            item
            for item in items
            if str((item or {}).get("Type") or "").strip().lower() in allowed_types
        ]

    ids = normalize_item_ids(items)
    if ids:
        return ids

    fallback = rail_cfg.get("fallback_query")
    if not isinstance(fallback, dict):
        return []

    fallback_cfg = {
        "name": str(rail_cfg.get("name") or "rail"),
        "path": str(fallback.get("path") or "/Items"),
        "query": fallback.get("query") if isinstance(fallback.get("query"), dict) else {},
        "limit": fallback.get("limit", limit),
        "rolling_premiere_days": fallback.get("rolling_premiere_days"),
        "allowed_item_types": fallback.get("allowed_item_types")
        or rail_cfg.get("allowed_item_types"),
    }
    return run_jellyfin_rail_query(
        jellyfin_url, jellyfin_api_key, user_id, fallback_cfg, max_items
    )


def ensure_jellyfin_home_rails(cfg, config_root, wait_timeout):
    rails_cfg = cfg.get("jellyfin_home_rails") or {}
    rails_enabled = bool_cfg(rails_cfg, "enabled", False)
    cleanup_when_disabled = bool_cfg(
        rails_cfg, "cleanup_collections_when_disabled", False
    )
    if not rails_enabled and not cleanup_when_disabled:
        return

    jellyfin_url = normalize_url(rails_cfg.get("url", "http://jellyfin:8096"))
    wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

    jellyfin_api_key = resolve_jellyfin_api_key(rails_cfg, config_root)
    if not jellyfin_api_key:
        raise RuntimeError(
            "Jellyfin home rails: API key unavailable. Set JELLYFIN_API_KEY or keep "
            "jellyfin_home_rails.auto_discover_api_key_from_db=true."
        )

    user_id = resolve_jellyfin_user_id_value(rails_cfg, jellyfin_url, jellyfin_api_key)
    if not user_id:
        raise RuntimeError(
            "Jellyfin home rails: no Jellyfin user id could be resolved. Set JELLYFIN_USER_ID "
            "or keep jellyfin_home_rails.auto_discover_user_id=true."
        )

    if not rails_enabled:
        cleanup_names = [
            str(name or "").strip()
            for name in coerce_list(rails_cfg.get("cleanup_collection_names"))
            if str(name or "").strip()
        ]
        if not cleanup_names:
            cleanup_names = [
                str(item.get("name") or "").strip()
                for item in default_jellyfin_home_rails()
                if str(item.get("name") or "").strip()
            ]

        removed = 0
        for name in cleanup_names:
            if delete_jellyfin_collection_by_name(
                jellyfin_url, jellyfin_api_key, user_id, name
            ):
                removed += 1

        log(
            "[OK] Jellyfin home rails: disabled; cleaned up synthetic collections "
            f"(removed={removed}, checked={len(cleanup_names)})"
        )
        return

    rails = coerce_list(rails_cfg.get("rails"))
    if not rails:
        rails = default_jellyfin_home_rails()

    max_items = to_int(rails_cfg.get("max_items_per_rail"), 40) or 40
    max_items = max(1, max_items)
    processed = 0
    total_items = 0

    for rail in rails:
        if not isinstance(rail, dict):
            continue
        name = str(rail.get("name") or "").strip()
        if not name:
            continue

        item_ids = run_jellyfin_rail_query(
            jellyfin_url, jellyfin_api_key, user_id, rail, max_items
        )
        if not item_ids:
            log(
                f"[WARN] Jellyfin home rails: no items matched for '{name}'. "
                "Leaving existing collection unchanged."
            )
            continue

        result = ensure_jellyfin_collection_membership(
            jellyfin_url,
            jellyfin_api_key,
            user_id,
            name,
            item_ids,
            clear_when_empty=bool_cfg(rail, "clear_when_empty", False),
        )
        processed += 1
        total_items += len(item_ids)
        log(
            f"[OK] Jellyfin home rails: reconciled '{name}' "
            f"(items={len(item_ids)}, created={result['created']}, "
            f"added={result['added']}, removed={result['removed']})"
        )

    log(
        "[OK] Jellyfin home rails: reconcile complete "
        f"(rails={processed}, total_items={total_items})"
    )


def normalize_plugin_name(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def ensure_jellyfin_plugin_repositories(jellyfin_url, jellyfin_api_key, repositories):
    desired = [repo for repo in coerce_list(repositories) if isinstance(repo, dict)]
    if not desired:
        return

    status, current, body = jellyfin_request(jellyfin_url, "/Repositories", jellyfin_api_key)
    if status != 200 or not isinstance(current, list):
        raise RuntimeError(
            f"Jellyfin plugins: failed listing repositories (HTTP {status}): {body}"
        )

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

    raise RuntimeError(
        f"Jellyfin plugins: failed updating repositories (HTTP {status}): {body}"
    )


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
            candidate = str(
                version.get("repositoryUrl")
                or version.get("RepositoryUrl")
                or ""
            ).strip().lower()
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
            message = (
                f"Jellyfin plugins: package not found for '{plugin_name}'"
                + (f" in repo {repository_url}" if repository_url else "")
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

        status, _, body = jellyfin_request(
            jellyfin_url, path, jellyfin_api_key, method="POST"
        )
        if status in (200, 201, 202, 204):
            requested += 1
            log(f"[OK] Jellyfin plugins: install requested for {pkg_name}")
            continue

        message = (
            f"Jellyfin plugins: failed to install {pkg_name} "
            f"(HTTP {status}): {body}"
        )
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
    hosts = [str(h).strip().lower() for h in coerce_list(homepage_cfg.get("hosts")) if str(h).strip()]
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
    rendered = _lib_render_homepage_services_yaml(
        hosts, scheme=scheme, onboarding=onboarding_cfg
    )
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
    bazarr_cfg = cfg.get("bazarr") or {}
    if not bool_cfg(bazarr_cfg, "enabled", False):
        return False

    bazarr_url = normalize_url(bazarr_cfg.get("url", "http://bazarr:6767"))
    wait_for_service("Bazarr", bazarr_url, "/", wait_timeout)

    sonarr_cfg = get_arr_app(arr_apps, "Sonarr")
    radarr_cfg = get_arr_app(arr_apps, "Radarr")
    sonarr_key = (app_keys.get("Sonarr") or "").strip()
    radarr_key = (app_keys.get("Radarr") or "").strip()

    if not sonarr_cfg and not radarr_cfg:
        log("[WARN] Bazarr: no Sonarr/Radarr app config found; skipping integration wiring.")
        return False

    config_rel_path = str(
        bazarr_cfg.get("config_relative_path") or "bazarr/config/config.yaml"
    ).strip()
    config_path = resolve_path(config_root, config_rel_path)
    if not config_path.exists():
        raise RuntimeError(
            f"Bazarr: config file not found at {config_path}. "
            "Ensure Bazarr has started at least once."
        )

    updates = {"general": {}}
    if sonarr_cfg and sonarr_key:
        parsed = parse_service_url(sonarr_cfg["url"], 8989)
        updates["general"]["use_sonarr"] = True
        updates["sonarr"] = {
            "apikey": sonarr_key,
            "ip": parsed["hostname"],
            "port": parsed["port"],
            "base_url": parsed["base_url"] or "/",
            "ssl": parsed["use_ssl"],
        }
    elif sonarr_cfg and not sonarr_key:
        log("[WARN] Bazarr: Sonarr config exists but Sonarr API key is missing; skipping Sonarr link.")
        updates["general"]["use_sonarr"] = False

    if radarr_cfg and radarr_key:
        parsed = parse_service_url(radarr_cfg["url"], 7878)
        updates["general"]["use_radarr"] = True
        updates["radarr"] = {
            "apikey": radarr_key,
            "ip": parsed["hostname"],
            "port": parsed["port"],
            "base_url": parsed["base_url"] or "/",
            "ssl": parsed["use_ssl"],
        }
    elif radarr_cfg and not radarr_key:
        log("[WARN] Bazarr: Radarr config exists but Radarr API key is missing; skipping Radarr link.")
        updates["general"]["use_radarr"] = False

    subtitle_defaults = bazarr_cfg.get("subtitle_defaults")
    if isinstance(subtitle_defaults, dict) and bool_cfg(subtitle_defaults, "enabled", True):
        general_defaults = subtitle_defaults.get("general")
        if isinstance(general_defaults, dict):
            updates.setdefault("general", {}).update(general_defaults)

        providers = [str(x).strip() for x in coerce_list(subtitle_defaults.get("providers")) if str(x).strip()]
        if providers:
            updates.setdefault("general", {})["enabled_providers"] = providers

        section_defaults = subtitle_defaults.get("sections")
        if isinstance(section_defaults, dict):
            for section_name, section_values in section_defaults.items():
                if not isinstance(section_values, dict):
                    continue
                normalized_section = str(section_name or "").strip()
                if not normalized_section:
                    continue
                updates.setdefault(normalized_section, {}).update(section_values)

    current = config_path.read_text(encoding="utf-8", errors="replace")
    rendered, changed = _lib_bazarr_apply_scalar_updates(current, updates)
    if not changed:
        log("[OK] Bazarr: Sonarr/Radarr + subtitle automation config already matches desired state")
        return False

    config_path.write_text(rendered, encoding="utf-8")
    log(f"[OK] Bazarr: wrote integration config {config_path}")
    log("[INFO] Bazarr: restart required to apply updated integration/subtitle settings.")
    return True


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
        raise RuntimeError(
            "Jellyfin Auto Collections: no Jellyfin user id could be resolved."
        )
    if not user_id:
        log(
            "[WARN] Jellyfin Auto Collections: could not resolve Jellyfin user id. "
            "Config will be written with an empty fallback user id."
        )

    plugins_cfg = auto_cfg.get("plugins")
    if not isinstance(plugins_cfg, dict) or not plugins_cfg:
        plugins_cfg = default_auto_collections_plugins()

    timezone_value = str(
        auto_cfg.get("timezone")
        or os.environ.get("TZ")
        or "UTC"
    ).strip()
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
        status, _, _ = http_request(
            app_url, f"/api/{version}/system/status", api_key=api_key
        )
        if status == 200:
            api_base = f"/api/{version}"
            log(f"[OK] {app_name}: detected API base {api_base}")
            return api_base

    raise RuntimeError(
        f"{app_name}: unable to detect API base (tried /api/v3 and /api/v1)"
    )


def pick_first_profile_id(app_name, app_url, api_base, api_key, endpoint, field_label):
    status, data, body = http_request(app_url, f"{api_base}/{endpoint}", api_key=api_key)
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(
            f"{app_name}: failed to list {field_label} (HTTP {status}): {body}"
        )

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
    status, data, body = http_request(
        app_url, f"{api_base}/rootfolder", api_key=api_key
    )
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(
            f"{app_name}: failed to list root folders (HTTP {status}): {body}"
        )

    desired = root_folder.rstrip("/")
    for item in data:
        if str(item.get("path", "")).rstrip("/") == desired:
            log(f"[OK] {app_name}: root folder already exists: {root_folder}")
            return

    create_payload = build_root_folder_payload(
        app_name, app_url, api_base, api_key, root_folder
    )

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
    status, _, body = http_request(
        app_url,
        f"{api_base}/command",
        api_key=api_key,
        method="POST",
        payload={"name": "CheckHealth"},
    )
    if status in (200, 201, 202):
        log(f"[OK] {app_name}: triggered CheckHealth")
        return
    log(f"[WARN] {app_name}: failed to trigger CheckHealth (HTTP {status}): {body}")


def trigger_arr_command(app_name, app_url, api_base, api_key, command_name, *, required=False):
    # Avoid queueing duplicate long-running commands on repeated bootstrap runs.
    status, commands, body = http_request(
        app_url,
        f"{api_base}/command",
        api_key=api_key,
    )
    if status == 200 and isinstance(commands, list):
        for item in commands:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            state = str(item.get("status") or "").strip().lower()
            if name == command_name.strip().lower() and state in ("queued", "started"):
                log(
                    f"[OK] {app_name}: command {command_name} already {state}; "
                    "skipping duplicate trigger"
                )
                return True

    status, _, body = http_request(
        app_url,
        f"{api_base}/command",
        api_key=api_key,
        method="POST",
        payload={"name": command_name},
    )
    if status in (200, 201, 202):
        log(f"[OK] {app_name}: triggered {command_name}")
        return True

    message = f"{app_name}: failed to trigger {command_name} (HTTP {status}): {body}"
    if required:
        raise RuntimeError(message)
    log(f"[WARN] {message}")
    return False


def trigger_arr_discovery_kickoff(cfg, app_cfg, app_url, api_base, api_key):
    arr_discovery_cfg = cfg.get("arr_discovery_lists") or {}
    if not bool_cfg(arr_discovery_cfg, "trigger_initial_sync", True):
        return

    impl = str(app_cfg.get("implementation") or "").strip()
    app_name = str(app_cfg.get("name") or impl or "Arr")
    commands = []
    if impl == "Lidarr":
        commands = ["MissingAlbumSearch", "RssSync"]
    elif impl == "Readarr":
        commands = ["MissingBookSearch", "RssSync"]
    else:
        return

    # Import list sync can be expensive/rate-limited (especially Readarr metadata providers).
    # Force it only on first-run (empty library) unless explicitly overridden.
    force_import_sync = env_truthy("ARR_FORCE_IMPORTLIST_SYNC", False)
    if force_import_sync:
        commands.insert(0, "ImportListSync")
    else:
        seed_endpoint = None
        if impl == "Lidarr":
            seed_endpoint = f"{api_base}/artist"
        elif impl == "Readarr":
            seed_endpoint = f"{api_base}/author"
        should_seed = True
        if seed_endpoint:
            status, existing, _ = http_request(app_url, seed_endpoint, api_key=api_key)
            if status == 200 and isinstance(existing, list) and len(existing) > 0:
                should_seed = False
        if should_seed:
            commands.insert(0, "ImportListSync")
        else:
            log(
                f"[OK] {app_name}: skipping ImportListSync during bootstrap "
                "(library already has managed entries)"
            )

    for command_name in commands:
        trigger_arr_command(app_name, app_url, api_base, api_key, command_name)


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

    raise RuntimeError(
        f"{app_name}: failed updating download handling (HTTP {status}): {body}"
    )


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


def ensure_arr_media_management(
    app_cfg, app_url, api_base, api_key, media_cfg
):
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
            desired_season_folders = bool(
                app_overrides.get("create_empty_series_folders")
            )
        else:
            desired_season_folders = bool_cfg(
                media_cfg, "create_empty_series_folders", True
            )
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
        raise RuntimeError(f"{app_name}: quality upgrade policy could not resolve quality profile id")

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
    if isinstance(example, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(example, int) and not isinstance(example, bool):
        parsed = to_int(value)
        if parsed is not None:
            return parsed
    return value


def resolve_import_list_definitions(arr_discovery_cfg, app_cfg):
    app_impl = str(app_cfg.get("implementation") or "")
    return coerce_list(
        arr_discovery_cfg.get(app_impl)
        or arr_discovery_cfg.get(app_impl.lower())
        or []
    )


def build_arr_import_list_payload(
    app_cfg,
    schema,
    list_cfg,
    default_quality_profile_id,
    default_metadata_profile_id=None,
):
    name = str(list_cfg.get("name") or schema.get("implementationName") or "").strip()
    if not name:
        raise RuntimeError(
            f"{app_cfg.get('name', app_cfg.get('implementation', 'Arr'))}: import list entry missing name."
        )

    values = field_map(schema.get("fields"))
    allow_unknown_overrides = bool(list_cfg.get("allow_unknown_field_overrides", False))
    for field_name, field_value in (list_cfg.get("field_overrides") or {}).items():
        resolved_value = resolve_env_placeholder(field_value)
        if field_name in values:
            values[field_name] = coerce_for_example(resolved_value, values.get(field_name))
        elif allow_unknown_overrides:
            values[field_name] = resolved_value

    payload = {
        "name": name,
        "implementation": schema.get("implementation"),
        "configContract": schema.get("configContract"),
        "fields": field_list(values),
    }

    for key in (
        "enabled",
        "enableAuto",
        "monitor",
        "qualityProfileId",
        "metadataProfileId",
        "searchOnAdd",
        "minimumAvailability",
        "listType",
        "listOrder",
        "minRefreshInterval",
        "enableAutomaticAdd",
        "searchForMissingEpisodes",
        "shouldMonitor",
        "monitorNewItems",
        "seriesType",
        "seasonFolder",
        "shouldSearch",
    ):
        if key in schema:
            payload[key] = schema.get(key)

    cfg_key_map = {
        "enabled": "enabled",
        "enable_auto": "enableAuto",
        "monitor": "monitor",
        "quality_profile_id": "qualityProfileId",
        "metadata_profile_id": "metadataProfileId",
        "search_on_add": "searchOnAdd",
        "minimum_availability": "minimumAvailability",
        "list_type": "listType",
        "list_order": "listOrder",
        "min_refresh_interval": "minRefreshInterval",
        "enable_automatic_add": "enableAutomaticAdd",
        "search_for_missing_episodes": "searchForMissingEpisodes",
        "should_monitor": "shouldMonitor",
        "monitor_new_items": "monitorNewItems",
        "series_type": "seriesType",
        "season_folder": "seasonFolder",
        "should_search": "shouldSearch",
    }
    for src_key, dst_key in cfg_key_map.items():
        if src_key not in list_cfg:
            continue
        value = resolve_env_placeholder(list_cfg.get(src_key))
        if dst_key in payload:
            payload[dst_key] = coerce_for_example(value, payload.get(dst_key))
        else:
            payload[dst_key] = value

    # Backward-compatibility across Arr variants:
    # some schemas (Lidarr/Readarr) use shouldMonitor/shouldSearch/enableAutomaticAdd,
    # while others (older Sonarr/Radarr-style) use monitor/searchOnAdd/enableAuto.
    def apply_alias(src_key, dst_keys):
        if src_key not in list_cfg:
            return
        value = resolve_env_placeholder(list_cfg.get(src_key))
        for dst_key in dst_keys:
            if dst_key in payload:
                payload[dst_key] = coerce_for_example(value, payload.get(dst_key))

    apply_alias("enable_auto", ("enableAutomaticAdd", "enableAuto"))
    apply_alias("enable_automatic_add", ("enableAutomaticAdd", "enableAuto"))
    apply_alias("monitor", ("shouldMonitor", "monitor"))
    apply_alias("should_monitor", ("shouldMonitor", "monitor"))
    apply_alias("search_on_add", ("shouldSearch", "searchOnAdd"))
    apply_alias("should_search", ("shouldSearch", "searchOnAdd"))

    quality_profile_id = to_int(payload.get("qualityProfileId"))
    if (quality_profile_id is None or quality_profile_id <= 0) and default_quality_profile_id:
        payload["qualityProfileId"] = int(default_quality_profile_id)

    metadata_profile_id = to_int(payload.get("metadataProfileId"))
    if (metadata_profile_id is None or metadata_profile_id <= 0) and default_metadata_profile_id:
        payload["metadataProfileId"] = int(default_metadata_profile_id)

    app_impl = str(app_cfg.get("implementation") or "").strip().lower()
    # Readarr/Lidarr use different enums for shouldMonitor than Sonarr-style "all/none".
    monitor_value = str(payload.get("shouldMonitor") or "").strip().lower()
    if monitor_value == "all":
        if app_impl == "readarr":
            payload["shouldMonitor"] = "entireAuthor"
        elif app_impl == "lidarr":
            payload["shouldMonitor"] = "entireArtist"

    root_folder_path = (
        str(list_cfg.get("root_folder_path") or "").strip()
        or str(app_cfg.get("root_folder") or "").strip()
    )
    if root_folder_path:
        payload["rootFolderPath"] = root_folder_path

    return payload


def ensure_arr_discovery_lists_for_app(cfg, app_cfg, app_url, api_base, api_key):
    arr_discovery_cfg = cfg.get("arr_discovery_lists") or {}
    if not bool_cfg(arr_discovery_cfg, "enabled", False):
        return

    app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
    list_defs = resolve_import_list_definitions(arr_discovery_cfg, app_cfg)
    if not list_defs:
        return
    prune_unmanaged = bool_cfg(arr_discovery_cfg, "prune_unmanaged", True)

    status, schemas, body = http_request(
        app_url, f"{api_base}/importlist/schema", api_key=api_key
    )
    if status != 200 or not isinstance(schemas, list):
        raise RuntimeError(
            f"{app_name}: failed reading import list schema (HTTP {status}): {body}"
        )
    schemas_by_impl = {
        str(item.get("implementation") or "").strip().lower(): item
        for item in schemas
        if isinstance(item, dict) and str(item.get("implementation") or "").strip()
    }

    status, existing_lists, body = http_request(
        app_url, f"{api_base}/importlist", api_key=api_key
    )
    if status != 200 or not isinstance(existing_lists, list):
        raise RuntimeError(
            f"{app_name}: failed listing import lists (HTTP {status}): {body}"
        )

    preferred_id, preferred_names = resolve_arr_quality_preferences(cfg, app_cfg)
    selected_profile = get_arr_quality_profile(
        app_name,
        app_url,
        api_base,
        api_key,
        preferred_id=preferred_id,
        preferred_names=preferred_names,
    )
    selected_profile_id = to_int(selected_profile.get("id"))
    selected_profile_name = str(selected_profile.get("name") or "")
    log(
        f"[OK] {app_name}: using quality profile '{selected_profile_name}' "
        f"(id={selected_profile_id}) for discovery lists"
    )

    app_impl = str(app_cfg.get("implementation") or "")
    selected_metadata_profile_id = None
    if app_impl in ("Lidarr", "Readarr"):
        for metadata_endpoint in ("metadataprofile", "metadataProfile"):
            try:
                selected_metadata_profile_id = pick_first_profile_id(
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
        if selected_metadata_profile_id:
            log(
                f"[OK] {app_name}: using metadata profile id "
                f"{selected_metadata_profile_id} for discovery lists"
            )
        else:
            log(
                f"[WARN] {app_name}: could not resolve metadata profile id; "
                "list creation may fail if this Arr requires metadataProfileId."
            )

    created = 0
    updated = 0
    deleted = 0
    skipped = 0
    desired_keys = set()
    managed_implementations = {
        str(item.get("implementation") or "").strip().lower()
        for item in list_defs
        if isinstance(item, dict) and str(item.get("implementation") or "").strip()
    }

    for list_cfg in list_defs:
        if not isinstance(list_cfg, dict):
            continue

        impl_raw = str(list_cfg.get("implementation") or "").strip()
        if not impl_raw:
            log(f"[WARN] {app_name}: skipping import list entry without implementation")
            skipped += 1
            continue
        schema = schemas_by_impl.get(impl_raw.lower())
        if not schema:
            msg = (
                f"{app_name}: import list implementation '{impl_raw}' is not supported by this Arr build."
            )
            if bool_cfg(list_cfg, "required", False):
                raise RuntimeError(msg)
            log(f"[WARN] {msg}")
            skipped += 1
            continue

        schema_fields = {str(f.get("name") or "") for f in (schema.get("fields") or [])}
        list_name = str(
            list_cfg.get("name") or schema.get("implementationName") or impl_raw
        ).strip()

        # Some providers (for example Trakt popular imports in Sonarr) require OAuth.
        if "signIn" in schema_fields:
            access_token = str(
                resolve_env_placeholder(
                    ((list_cfg.get("field_overrides") or {}).get("accessToken", ""))
                )
            ).strip()
            if not access_token and bool_cfg(list_cfg, "skip_if_auth_required", True):
                log(
                    f"[WARN] {app_name}: skipping import list '{list_name}' "
                    f"({impl_raw}) because provider auth is required "
                    "(set field_overrides.accessToken/refreshToken to enable)."
                )
                skipped += 1
                continue

        payload = build_arr_import_list_payload(
            app_cfg,
            schema,
            list_cfg,
            selected_profile_id,
            selected_metadata_profile_id,
        )
        desired_keys.add(
            (
                str(payload.get("implementation") or "").strip().lower(),
                str(payload.get("name") or "").strip().lower(),
            )
        )

        existing = None
        for item in existing_lists:
            if not isinstance(item, dict):
                continue
            if (
                str(item.get("implementation") or "").strip().lower()
                == str(payload.get("implementation") or "").strip().lower()
                and str(item.get("name") or "").strip().lower()
                == str(payload.get("name") or "").strip().lower()
            ):
                existing = item
                break

        if existing and existing.get("id") is not None:
            payload["id"] = existing.get("id")
            status, _, body = http_request(
                app_url,
                f"{api_base}/importlist/{existing.get('id')}",
                api_key=api_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 201, 202):
                updated += 1
                log(f"[OK] {app_name}: updated discovery list '{payload['name']}'")
                continue
            msg = (
                f"{app_name}: failed updating discovery list '{payload['name']}' "
                f"(HTTP {status}): {body}"
            )
            if bool_cfg(list_cfg, "required", False):
                raise RuntimeError(msg)
            log(f"[WARN] {msg}")
            skipped += 1
            continue

        status, _, body = http_request(
            app_url,
            f"{api_base}/importlist",
            api_key=api_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            created += 1
            log(f"[OK] {app_name}: created discovery list '{payload['name']}'")
            continue

        msg = (
            f"{app_name}: failed creating discovery list '{payload['name']}' "
            f"(HTTP {status}): {body}"
        )
        if bool_cfg(list_cfg, "required", False):
            raise RuntimeError(msg)
        log(f"[WARN] {msg}")
        skipped += 1

    if prune_unmanaged and managed_implementations:
        status, existing_lists, body = http_request(
            app_url, f"{api_base}/importlist", api_key=api_key
        )
        if status != 200 or not isinstance(existing_lists, list):
            raise RuntimeError(
                f"{app_name}: failed listing import lists for prune (HTTP {status}): {body}"
            )
        for item in existing_lists:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            impl = str(item.get("implementation") or "").strip().lower()
            name = str(item.get("name") or "").strip().lower()
            key = (impl, name)
            if item_id is None or impl not in managed_implementations or key in desired_keys:
                continue
            status, _, body = http_request(
                app_url,
                f"{api_base}/importlist/{item_id}",
                api_key=api_key,
                method="DELETE",
            )
            if status in (200, 202, 204):
                deleted += 1
                log(
                    f"[OK] {app_name}: pruned unmanaged discovery list "
                    f"'{item.get('name', item_id)}'"
                )
                continue
            log(
                f"[WARN] {app_name}: failed pruning unmanaged discovery list "
                f"'{item.get('name', item_id)}' (HTTP {status}): {body}"
            )

    log(
        f"[OK] {app_name}: discovery list reconcile complete "
        f"(created={created}, updated={updated}, deleted={deleted}, skipped={skipped})"
    )


def ensure_readarr_metadata_source(cfg, app_cfg, app_url, api_base, api_key):
    app_impl = str(app_cfg.get("implementation") or "").strip().lower()
    if app_impl != "readarr":
        return

    readarr_cfg = cfg.get("readarr") or {}
    desired_source = str(readarr_cfg.get("metadata_source") or "").strip()
    if not desired_source:
        return

    status, current, body = http_request(
        app_url, f"{api_base}/config/development", api_key=api_key
    )
    if status != 200 or not isinstance(current, dict):
        raise RuntimeError(
            f"Readarr: failed reading development config (HTTP {status}): {body}"
        )

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

    raise RuntimeError(
        f"Readarr: failed updating metadata source (HTTP {status}): {body}"
    )


def auth_scope_matches(auth_cfg, app_name, implementation):
    include = [
        str(x).strip().lower()
        for x in (auth_cfg.get("include") or [])
        if str(x).strip()
    ]
    if not include:
        return True
    app_lower = str(app_name).strip().lower()
    impl_lower = str(implementation).strip().lower()
    return app_lower in include or impl_lower in include


def ensure_app_auth_settings(
    app_name, implementation, app_url, api_base, api_key, auth_cfg
):
    if not bool_cfg(auth_cfg, "enabled", False):
        return
    if not auth_scope_matches(auth_cfg, app_name, implementation):
        return

    status, current, body = http_request(
        app_url, f"{api_base}/config/host", api_key=api_key
    )
    if status != 200 or not isinstance(current, dict):
        raise RuntimeError(
            f"{app_name}: failed reading host config for auth bootstrap (HTTP {status}): {body}"
        )

    method = str(auth_cfg.get("method", "None"))
    required = str(auth_cfg.get("required", "DisabledForLocalAddresses"))
    username_env = auth_cfg.get("username_env", "STACK_ADMIN_USERNAME")
    password_env = auth_cfg.get("password_env", "STACK_ADMIN_PASSWORD")
    username = (os.environ.get(username_env) or "").strip()
    password = (os.environ.get(password_env) or "").strip()

    desired = dict(current)
    changed = False

    if str(desired.get("authenticationMethod")) != method:
        desired["authenticationMethod"] = method
        changed = True

    if str(desired.get("authenticationRequired")) != required:
        desired["authenticationRequired"] = required
        changed = True

    if method.lower() != "none":
        if not username or not password:
            raise RuntimeError(
                f"{app_name}: auth method '{method}' requires env creds {username_env}/{password_env}"
            )
        if str(desired.get("username", "")) != username:
            desired["username"] = username
            changed = True
        desired["password"] = password
        changed = True
        # Arr/Prowlarr host config validation may require explicit password confirmation.
        desired["passwordConfirmation"] = password
        # Some versions validate PascalCase property names.
        desired["PasswordConfirmation"] = password
        # Some versions use confirmPassword style keys.
        desired["confirmPassword"] = password
        desired["ConfirmPassword"] = password
        changed = True
    else:
        if desired.get("username"):
            desired["username"] = ""
            changed = True
        if desired.get("password"):
            desired["password"] = ""
            changed = True
        if desired.get("passwordConfirmation"):
            desired["passwordConfirmation"] = ""
            changed = True
        if desired.get("PasswordConfirmation"):
            desired["PasswordConfirmation"] = ""
            changed = True
        if desired.get("confirmPassword"):
            desired["confirmPassword"] = ""
            changed = True
        if desired.get("ConfirmPassword"):
            desired["ConfirmPassword"] = ""
            changed = True

    if not changed:
        log(f"[OK] {app_name}: auth settings already match desired config")
        return

    status, _, body = http_request(
        app_url,
        f"{api_base}/config/host",
        api_key=api_key,
        method="PUT",
        payload=desired,
    )
    if status in (200, 201, 202):
        log(
            f"[OK] {app_name}: auth settings applied "
            f"(method={method}, required={required})"
        )
        return

    # Retry once for versions that validate one specific confirmation key casing.
    if status == 400 and "passwordconfirmation" in str(body or "").lower():
        retry_payload = dict(desired)
        retry_payload["passwordConfirmation"] = password
        retry_payload["PasswordConfirmation"] = password
        retry_payload["confirmPassword"] = password
        retry_payload["ConfirmPassword"] = password
        status2, _, body2 = http_request(
            app_url,
            f"{api_base}/config/host",
            api_key=api_key,
            method="PUT",
            payload=retry_payload,
        )
        if status2 in (200, 201, 202):
            log(
                f"[OK] {app_name}: auth settings applied "
                f"(method={method}, required={required})"
            )
            return
        status = status2
        body = body2

    raise RuntimeError(
        f"{app_name}: failed applying auth settings (HTTP {status}): {body}"
    )


def choose_category(app_cfg, client_cfg):
    if app_cfg.get("qbit_category"):
        return app_cfg["qbit_category"]

    categories = client_cfg.get("categories", {})
    if app_cfg["implementation"] in categories:
        return categories[app_cfg["implementation"]]

    default_map = {
        "Sonarr": "tv",
        "Radarr": "movies",
        "Lidarr": "music",
        "Readarr": "books",
    }
    return default_map.get(app_cfg["implementation"], "downloads")


def normalize_mapping_path(path_value):
    text = str(path_value or "").strip()
    if not text:
        return ""
    if text != "/":
        text = text.rstrip("/")
    return text


def build_sab_remote_path_mappings(sab_cfg):
    raw = coerce_list(sab_cfg.get("remote_path_mappings"))
    host = str(sab_cfg.get("host", "sabnzbd")).strip() or "sabnzbd"
    complete_dir = normalize_mapping_path(
        sab_cfg.get("complete_dir", "/data/usenet/completed")
    )

    # Keep a compatibility mapping for SAB setups that still report legacy /config paths.
    if complete_dir:
        raw.extend(
            [
                {
                    "host": host,
                    "remote_path": "/config/Downloads/complete",
                    "local_path": complete_dir,
                },
                {
                    "host": host,
                    "remote_path": "Downloads/complete",
                    "local_path": complete_dir,
                },
            ]
        )

    return normalize_remote_path_mappings(raw)


def ensure_arr_remote_path_mappings(app_cfg, app_url, api_base, api_key, mappings):
    desired_mappings = normalize_remote_path_mappings(mappings)
    if not desired_mappings:
        return

    app_name = app_cfg.get("name", app_cfg.get("implementation", "Arr"))
    status, existing, body = http_request(
        app_url, f"{api_base}/remotepathmapping", api_key=api_key
    )
    if status != 200 or not isinstance(existing, list):
        raise RuntimeError(
            f"{app_name}: failed listing remote path mappings (HTTP {status}): {body}"
        )

    existing_by_key = {}
    for item in existing:
        if not isinstance(item, dict):
            continue
        host = str(item.get("host", "")).strip()
        remote = normalize_mapping_path(item.get("remotePath"))
        if not host or not remote:
            continue
        key = (host.lower(), remote)
        if key not in existing_by_key:
            existing_by_key[key] = item

    for mapping in desired_mappings:
        host = str(mapping.get("host", "")).strip()
        remote = normalize_mapping_path(mapping.get("remotePath"))
        local = normalize_mapping_path(mapping.get("localPath"))
        if not host or not remote or not local:
            continue

        key = (host.lower(), remote)
        current = existing_by_key.get(key)
        if current:
            current_local = normalize_mapping_path(current.get("localPath"))
            if current_local == local:
                log(
                    f"[OK] {app_name}: remote path mapping already set "
                    f"({host}: {remote} -> {local})"
                )
                continue

            payload = {
                "id": current.get("id"),
                "host": host,
                "remotePath": remote,
                "localPath": local,
            }
            status, _, body = http_request(
                app_url,
                f"{api_base}/remotepathmapping/{current.get('id')}",
                api_key=api_key,
                method="PUT",
                payload=payload,
            )
            if status in (200, 201, 202):
                log(
                    f"[OK] {app_name}: updated remote path mapping "
                    f"({host}: {remote} -> {local})"
                )
                continue
            raise RuntimeError(
                f"{app_name}: failed updating remote path mapping "
                f"({host}: {remote} -> {local}) (HTTP {status}): {body}"
            )

        payload = {"host": host, "remotePath": remote, "localPath": local}
        status, _, body = http_request(
            app_url,
            f"{api_base}/remotepathmapping",
            api_key=api_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            log(
                f"[OK] {app_name}: created remote path mapping "
                f"({host}: {remote} -> {local})"
            )
            continue
        raise RuntimeError(
            f"{app_name}: failed creating remote path mapping "
            f"({host}: {remote} -> {local}) (HTTP {status}): {body}"
        )


def ensure_arr_download_client(
    app_cfg,
    app_url,
    api_base,
    api_key,
    client_cfg,
    client_auth,
):
    status, schemas, body = http_request(
        app_url, f"{api_base}/downloadclient/schema", api_key=api_key
    )
    if status != 200 or not isinstance(schemas, list):
        raise RuntimeError(
            f"{app_cfg['name']}: failed to read download client schema (HTTP {status}): {body}"
        )

    impl_raw = str(client_cfg.get("implementation", "QBittorrent")).strip()
    impl_target = impl_raw.lower()
    client_label = str(client_cfg.get("name") or impl_raw or "download client")
    client_host = str(client_cfg.get("host", "")).strip()
    client_port = to_int(client_cfg.get("port"))
    client_use_ssl = bool(client_cfg.get("use_ssl", False))
    client_url_base = str(client_cfg.get("url_base", "")).strip()
    client_priority = to_int(client_cfg.get("priority"), 1)
    if client_priority is None:
        client_priority = 1
    if client_priority < 1:
        client_priority = 1
    if client_priority > 50:
        client_priority = 50

    auth_username = str((client_auth or {}).get("username", "")).strip()
    auth_password = str((client_auth or {}).get("password", "")).strip()
    auth_api_key = str(
        (client_auth or {}).get("api_key")
        or (client_auth or {}).get("apikey")
        or ""
    ).strip()

    schema = None
    for entry in schemas:
        if str(entry.get("implementation", "")).lower() == impl_target:
            schema = entry
            break
    if not schema:
        raise RuntimeError(
            f"{app_cfg['name']}: schema '{impl_raw}' not found in downloadclient/schema"
        )

    values = field_map(schema.get("fields"))
    if "host" in values:
        values["host"] = client_host
    if "hostname" in values:
        values["hostname"] = client_host
    if client_port is not None and "port" in values:
        values["port"] = int(client_port)

    if "useSsl" in values:
        values["useSsl"] = client_use_ssl
    if "ssl" in values:
        values["ssl"] = client_use_ssl
    if "urlBase" in values:
        values["urlBase"] = client_url_base
    if "baseUrl" in values:
        values["baseUrl"] = client_url_base
    if "username" in values:
        values["username"] = auth_username
    if "password" in values:
        values["password"] = auth_password
    if "apiKey" in values:
        values["apiKey"] = auth_api_key
    if "apikey" in values:
        values["apikey"] = auth_api_key
    app_impl_lower = str(app_cfg.get("implementation") or "").strip().lower()
    enforce_dual_priority_fields = app_impl_lower == "readarr"
    for priority_key in list(values.keys()):
        key_lower = str(priority_key).strip().lower()
        if key_lower in ("priority", "torrentpriority", "nzbpriority") or key_lower.endswith(
            "priority"
        ):
            values[priority_key] = client_priority
    # Readarr has been observed to validate an explicit Priority value and reject defaults (0).
    has_priority_field = any(str(k).strip().lower() == "priority" for k in values.keys())
    if not has_priority_field:
        values["priority"] = client_priority
        if enforce_dual_priority_fields:
            values["Priority"] = client_priority
    elif enforce_dual_priority_fields:
        values["priority"] = client_priority
        values["Priority"] = client_priority

    category = choose_category(app_cfg, client_cfg)
    for key in (
        "category",
        "tvCategory",
        "movieCategory",
        "musicCategory",
        "bookCategory",
        "animeCategory",
    ):
        if key in values:
            values[key] = category

    payload = {
        "name": client_label,
        "implementation": schema.get("implementation", impl_raw),
        "configContract": schema.get("configContract", "QBittorrentSettings"),
        "enable": True,
        "priority": client_priority,
        "tags": [],
        "fields": field_list(values),
    }
    if enforce_dual_priority_fields:
        payload["Priority"] = client_priority

    status, clients, body = http_request(app_url, f"{api_base}/downloadclient", api_key=api_key)
    if status != 200 or not isinstance(clients, list):
        raise RuntimeError(
            f"{app_cfg['name']}: failed to list download clients (HTTP {status}): {body}"
        )

    existing = None
    existing_by_name = None
    named_matches = []
    desired_name = client_label
    for client in clients:
        if str(client.get("implementation", "")).lower() != impl_target:
            continue
        if str(client.get("name", "")).strip().lower() == desired_name.strip().lower():
            existing_by_name = client
            named_matches.append(client)
        fields = field_map(client.get("fields"))
        field_host = str(fields.get("host", "") or fields.get("hostname", "")).strip()
        field_port = to_int(fields.get("port"))
        host_match = bool(client_host and field_host == client_host)
        port_match = bool(client_port is None or field_port == client_port)
        if host_match and port_match:
            existing = client
            break

    def delete_client(client_id):
        status, _, body = http_request(
            app_url, f"{api_base}/downloadclient/{client_id}", api_key=api_key, method="DELETE"
        )
        if status not in (200, 202, 204):
            raise RuntimeError(
                f"{app_cfg['name']}: failed deleting duplicate {client_label} client id={client_id} "
                f"(HTTP {status}): {body}"
            )

    # Some environments can accumulate duplicate named clients across repeated bootstrap runs.
    # Remove extras up-front so PUT/POST validation does not fail with "Name should be unique".
    if len(named_matches) > 1:
        keep = existing.get("id") if existing else named_matches[0].get("id")
        for item in named_matches:
            item_id = item.get("id")
            if item_id is None or item_id == keep:
                continue
            delete_client(item_id)
            log(
                f"[OK] {app_cfg['name']}: removed duplicate named {client_label} client id={item_id}"
            )
        status, clients, body = http_request(
            app_url, f"{api_base}/downloadclient", api_key=api_key
        )
        if status != 200 or not isinstance(clients, list):
            raise RuntimeError(
                f"{app_cfg['name']}: failed to refresh download clients after duplicate cleanup "
                f"(HTTP {status}): {body}"
            )
        existing = None
        existing_by_name = None
        for client in clients:
            if str(client.get("implementation", "")).lower() != impl_target:
                continue
            if str(client.get("name", "")).strip().lower() == desired_name.strip().lower():
                existing_by_name = client
            fields = field_map(client.get("fields"))
            field_host = str(fields.get("host", "") or fields.get("hostname", "")).strip()
            field_port = to_int(fields.get("port"))
            host_match = bool(client_host and field_host == client_host)
            port_match = bool(client_port is None or field_port == client_port)
            if host_match and port_match:
                existing = client

    def save_client(method, path, request_payload):
        status, _, response_body = http_request(
            app_url, path, api_key=api_key, method=method, payload=request_payload
        )
        if status in (200, 201, 202):
            return True, status, response_body

        body_lower = str(response_body or "").lower()
        priority_hints = (
            "additional properties",
            "not allowed",
            "unknown",
            "unrecognized",
            "deserialize",
            "invalid property",
        )
        priority_validation_hints = ("inclusivebetweenvalidator", "between 1 and 50")

        if "priority" in body_lower and any(hint in body_lower for hint in priority_validation_hints):
            fallback = dict(request_payload)
            fallback["priority"] = client_priority
            if enforce_dual_priority_fields:
                fallback["Priority"] = client_priority
            normalized_fields = []
            has_priority_field = False
            has_priority_upper = False
            for field in coerce_list(fallback.get("fields")):
                if not isinstance(field, dict):
                    normalized_fields.append(field)
                    continue
                original_name = str(field.get("name") or "").strip()
                field_name = str(field.get("name") or "").strip().lower()
                if field_name in ("priority", "torrentpriority", "nzbpriority") or field_name.endswith(
                    "priority"
                ):
                    fixed = dict(field)
                    fixed["value"] = client_priority
                    normalized_fields.append(fixed)
                    if field_name == "priority":
                        has_priority_field = True
                    if original_name == "Priority":
                        has_priority_upper = True
                else:
                    normalized_fields.append(field)
            if not has_priority_field:
                normalized_fields.append({"name": "priority", "value": client_priority})
            if enforce_dual_priority_fields and not has_priority_upper:
                normalized_fields.append({"name": "Priority", "value": client_priority})
            fallback["fields"] = normalized_fields
            status2, _, response_body2 = http_request(
                app_url, path, api_key=api_key, method=method, payload=fallback
            )
            if status2 in (200, 201, 202):
                return True, status2, response_body2
            status = status2
            response_body = response_body2
            body_lower = str(response_body or "").lower()

        if "priority" not in body_lower or not any(hint in body_lower for hint in priority_hints):
            return False, status, response_body

        fallback = dict(request_payload)
        fallback.pop("priority", None)
        fallback.pop("Priority", None)
        status2, _, response_body2 = http_request(
            app_url, path, api_key=api_key, method=method, payload=fallback
        )
        if status2 in (200, 201, 202):
            return True, status2, response_body2
        return False, status2, response_body2

    def reconcile_existing_by_name():
        status_list, clients_list, body_list = http_request(
            app_url, f"{api_base}/downloadclient", api_key=api_key
        )
        if status_list != 200 or not isinstance(clients_list, list):
            raise RuntimeError(
                f"{app_cfg['name']}: failed refreshing download clients after duplicate-name response (HTTP {status_list}): {body_list}"
            )

        target = None
        for item in clients_list:
            if str(item.get("implementation", "")).lower() != impl_target:
                continue
            if str(item.get("name", "")).strip().lower() == desired_name.strip().lower():
                target = item
                break
        if not target:
            raise RuntimeError(
                f"{app_cfg['name']}: duplicate '{client_label}' client name detected but no matching existing client was found to reconcile"
            )

        payload["id"] = target.get("id")
        ok3, status3, body3 = save_client(
            "PUT", f"{api_base}/downloadclient/{target.get('id')}", payload
        )
        if ok3:
            log(
                f"[OK] {app_cfg['name']}: reconciled existing named {client_label} download client"
            )
            return
        raise RuntimeError(
            f"{app_cfg['name']}: failed reconciling existing {client_label} client by name (HTTP {status3}): {body3}"
        )

    if existing:
        payload["id"] = existing.get("id")
        ok, status, body = save_client(
            "PUT", f"{api_base}/downloadclient/{existing.get('id')}", payload
        )
        if ok:
            log(f"[OK] {app_cfg['name']}: updated {client_label} download client")
            return
        body_lower = str(body or "").lower()
        if status == 400 and "should be unique" in body_lower and "name" in body_lower:
            reconcile_existing_by_name()
            return
        raise RuntimeError(
            f"{app_cfg['name']}: failed updating {client_label} client (HTTP {status}): {body}"
        )

    ok, status, body = save_client("POST", f"{api_base}/downloadclient", payload)
    if ok:
        log(f"[OK] {app_cfg['name']}: created {client_label} download client")
        return

    # If a previous run already created the same named client, reconcile by update.
    body_lower = str(body or "").lower()
    if status == 400 and "should be unique" in body_lower and "name" in body_lower:
        if existing_by_name is not None:
            payload["id"] = existing_by_name.get("id")
            ok2, status2, body2 = save_client(
                "PUT", f"{api_base}/downloadclient/{existing_by_name.get('id')}", payload
            )
            if ok2:
                log(
                    f"[OK] {app_cfg['name']}: reconciled existing named {client_label} download client"
                )
                return
            raise RuntimeError(
                f"{app_cfg['name']}: failed reconciling existing {client_label} client by name (HTTP {status2}): {body2}"
            )
        reconcile_existing_by_name()
        return

    raise RuntimeError(
        f"{app_cfg['name']}: failed creating {client_label} client (HTTP {status}): {body}"
    )


def qbit_login(base_url, username, password):
    jar = cookiejar.CookieJar()
    opener = request.build_opener(request.HTTPCookieProcessor(jar))
    data = parse.urlencode({"username": username, "password": password}).encode("utf-8")
    req = request.Request(
        f"{normalize_url(base_url)}/api/v2/auth/login",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with opener.open(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if "Ok." not in body:
        raise RuntimeError("qBittorrent login rejected credentials.")
    return opener


def qbit_create_category(opener, base_url, category, save_path):
    data = parse.urlencode({"category": category, "savePath": save_path}).encode("utf-8")
    req = request.Request(
        f"{normalize_url(base_url)}/api/v2/torrents/createCategory",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener.open(req, timeout=20):
            pass
        log(f"[OK] qBittorrent: category {category} -> {save_path}")
    except error.HTTPError as exc:
        # qBittorrent may return 409 when category already exists.
        if exc.code == 409:
            log(f"[OK] qBittorrent: category already exists: {category}")
            return
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"qBittorrent: failed to create category {category} (HTTP {exc.code}): {body}"
        )


def qbit_set_preferences(opener, base_url, preferences):
    data = parse.urlencode({"json": json.dumps(preferences)}).encode("utf-8")
    req = request.Request(
        f"{normalize_url(base_url)}/api/v2/app/setPreferences",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener.open(req, timeout=20):
            pass
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"qBittorrent: failed updating preferences (HTTP {exc.code}): {body}"
        ) from exc


def _normalize_qbit_subnet_list(values):
    normalized = []
    seen = set()
    for raw in coerce_list(values):
        subnet = str(raw or "").strip()
        if not subnet or subnet in seen:
            continue
        seen.add(subnet)
        normalized.append(subnet)
    return normalized


def setup_qbit_storage_defaults(opener, qbit_url, qbit_cfg):
    save_path = str(
        qbit_cfg.get("default_save_path", "/data/torrents/completed")
    ).rstrip("/")
    temp_path = str(
        qbit_cfg.get("temp_path", "/data/torrents/incomplete")
    ).rstrip("/")
    temp_path_enabled = bool(qbit_cfg.get("temp_path_enabled", True))
    auto_tmm_enabled = bool(qbit_cfg.get("auto_tmm_enabled", True))

    prefs = {
        "save_path": save_path,
        "temp_path": temp_path,
        "temp_path_enabled": temp_path_enabled,
        "auto_tmm_enabled": auto_tmm_enabled,
        # Keep moving torrents between incomplete/completed category paths when state changes.
        "torrent_changed_tmm_enabled": True,
    }

    # Keep UI auth convenient for localhost and trusted internal cluster ranges,
    # while preventing accidental world-open bypass defaults.
    auth_bypass = qbit_cfg.get("auth_bypass")
    if not isinstance(auth_bypass, dict):
        auth_bypass = {}

    bypass_local_auth = bool_cfg(auth_bypass, "localhost", True)
    bypass_whitelist_enabled = bool_cfg(auth_bypass, "whitelist_enabled", True)
    whitelist_subnets = _normalize_qbit_subnet_list(
        auth_bypass.get(
            "whitelist_subnets",
            [
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "127.0.0.1/32",
                "::1/128",
            ],
        )
    )
    allow_open_world = bool_cfg(auth_bypass, "allow_open_world", False)
    world_open_tokens = {"0.0.0.0", "0.0.0.0/0", "::/0"}
    if not allow_open_world:
        filtered = []
        for subnet in whitelist_subnets:
            if subnet in world_open_tokens:
                log(
                    "[WARN] qBittorrent: refusing world-open auth bypass subnet "
                    f"'{subnet}'. Set download_clients.qbittorrent.auth_bypass.allow_open_world=true "
                    "to allow it explicitly."
                )
                continue
            filtered.append(subnet)
        whitelist_subnets = filtered

    if bypass_whitelist_enabled and not whitelist_subnets:
        log(
            "[WARN] qBittorrent: auth bypass whitelist enabled but no valid subnets "
            "resolved; disabling subnet whitelist bypass."
        )
        bypass_whitelist_enabled = False

    prefs["bypass_local_auth"] = bypass_local_auth
    prefs["bypass_auth_subnet_whitelist_enabled"] = bypass_whitelist_enabled
    prefs["bypass_auth_subnet_whitelist"] = (
        ",".join(whitelist_subnets) if bypass_whitelist_enabled else ""
    )

    # Optional seeding/retention guardrails to reduce long-lived seeding and reclaim space.
    seeding_policy = qbit_cfg.get("seeding_policy")
    if isinstance(seeding_policy, dict) and bool_cfg(seeding_policy, "enabled", False):
        max_ratio = seeding_policy.get("max_ratio")
        max_ratio_val = None
        try:
            if max_ratio is not None and str(max_ratio).strip() != "":
                max_ratio_val = float(max_ratio)
        except Exception:
            max_ratio_val = None

        max_seed_minutes = to_int(seeding_policy.get("max_seeding_time_minutes"))
        remove_on_limit = bool_cfg(seeding_policy, "remove_on_limit_reached", False)
        if remove_on_limit:
            log(
                "[WARN] qBittorrent: seeding_policy.remove_on_limit_reached=true "
                "conflicts with Arr completed-download handling; forcing pause-on-limit."
            )
            remove_on_limit = False

        if max_ratio_val is not None and max_ratio_val > 0:
            prefs["max_ratio_enabled"] = True
            prefs["max_ratio"] = max_ratio_val
            prefs["max_ratio_act"] = 1 if remove_on_limit else 0
        elif bool_cfg(seeding_policy, "max_ratio_enabled", False):
            prefs["max_ratio_enabled"] = False

        if max_seed_minutes is not None and max_seed_minutes > 0:
            prefs["max_seeding_time_enabled"] = True
            prefs["max_seeding_time"] = int(max_seed_minutes)
            prefs["max_ratio_act"] = 1 if remove_on_limit else prefs.get("max_ratio_act", 0)
        elif bool_cfg(seeding_policy, "max_seeding_time_enabled", False):
            prefs["max_seeding_time_enabled"] = False

    qbit_set_preferences(opener, qbit_url, prefs)
    log(
        "[OK] qBittorrent: storage defaults set "
        f"(save_path={save_path}, temp_path={temp_path}, "
        f"temp_path_enabled={temp_path_enabled}, auto_tmm_enabled={auto_tmm_enabled}, "
        f"bypass_local_auth={bypass_local_auth}, "
        f"bypass_auth_subnet_whitelist_enabled={bypass_whitelist_enabled}, "
        f"whitelist_count={len(whitelist_subnets)})"
    )


def setup_qbit_categories(arr_apps, qbit_cfg, qb_username, qb_password):
    qbit_url = normalize_url(qbit_cfg.get("url", "http://qbittorrent:8080"))
    opener = qbit_login(qbit_url, qb_username, qb_password)
    setup_qbit_storage_defaults(opener, qbit_url, qbit_cfg)

    completed_paths = qbit_cfg.get("completed_paths", {})
    for app in arr_apps:
        category = choose_category(app, qbit_cfg)
        default_path = f"/data/torrents/completed/{category}"
        save_path = completed_paths.get(category, default_path)
        qbit_create_category(opener, qbit_url, category, save_path)


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
        raise RuntimeError(f"qBittorrent: failed parsing completed torrents payload: {exc}") from exc
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
        str(x).strip().lower()
        for x in coerce_list(queue_cfg.get("count_states"))
        if str(x).strip()
    } or {x.lower() for x in default_count_states}

    default_prune_states = ["queuedDL", "stalledDL", "metaDL", "pausedDL", "error", "missingFiles"]
    prune_states = {
        str(x).strip().lower()
        for x in coerce_list(queue_cfg.get("prune_states"))
        if str(x).strip()
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
        str(x).strip().lower()
        for x in coerce_list(stale_cfg.get("states"))
        if str(x).strip()
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
            if stale_max_download_speed_bps is not None and dlspeed > int(stale_max_download_speed_bps):
                continue
            age_trigger = float(rec.get("age_hours") or 0.0) >= float(stale_max_age_hours)
            stalled_trigger = float(rec.get("stalled_hours") or 0.0) >= float(stale_max_stalled_hours)
            eta_val = int(rec.get("eta") or -1)
            eta_trigger = bool(stale_max_eta_seconds is not None and eta_val > int(stale_max_eta_seconds))
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

    default_policy = {
        "version": 1,
        "retention": {
            "max_disk_used_percent": 65,
            "target_disk_used_percent": 58,
            "protect_recently_added_days": 21,
            "protect_unwatched_days": 30,
            "minimum_rating_to_keep": 6.5,
        },
        "rules": [
            {
                "name": "Purge watched movies after 120 days",
                "libraries": ["Movies"],
                "conditions": {"watched": True, "not_watched_for_days": 120},
                "actions": {"delete_item": True},
            },
            {
                "name": "Purge watched episodes after 45 days",
                "libraries": ["TV Shows"],
                "conditions": {"watched": True, "not_watched_for_days": 45},
                "actions": {"delete_item": True},
            },
            {
                "name": "Keep top-rated media",
                "libraries": ["Movies", "TV Shows"],
                "conditions": {"community_rating_gte": 7.5},
                "actions": {"protect_item": True},
            },
        ],
    }
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
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("records", "Records", "items", "Items"):
        items = payload.get(key)
        if isinstance(items, list):
            return items
    return []


def queue_item_is_failed(item, failed_tokens):
    if not isinstance(item, dict):
        return False

    hay = []
    for key in (
        "status",
        "statusText",
        "trackedDownloadState",
        "trackedDownloadStatus",
        "errorMessage",
        "trackedDownloadError",
        "outputPath",
    ):
        value = item.get(key)
        if value is not None:
            hay.append(str(value))

    messages = item.get("statusMessages")
    if isinstance(messages, list):
        for entry in messages:
            if isinstance(entry, dict):
                for mk in ("title", "messages", "message"):
                    mv = entry.get(mk)
                    if mv is None:
                        continue
                    if isinstance(mv, list):
                        hay.extend(str(x) for x in mv if x is not None)
                    else:
                        hay.append(str(mv))
            elif entry is not None:
                hay.append(str(entry))

    text = normalize_token(" ".join(hay))
    if not text:
        return False
    return any(token and token in text for token in failed_tokens)


def delete_queue_item(app_name, app_url, api_base, api_key, item_id, remove_from_client, blocklist):
    query_paths = [
        (
            f"{api_base}/queue/{item_id}?"
            f"removeFromClient={'true' if remove_from_client else 'false'}&"
            f"blocklist={'true' if blocklist else 'false'}&skipRedownload=false"
        ),
        (
            f"{api_base}/queue/{item_id}?"
            f"removeFromClient={'true' if remove_from_client else 'false'}&"
            f"blacklist={'true' if blocklist else 'false'}"
        ),
        f"{api_base}/queue/{item_id}",
    ]
    last_status = None
    last_body = ""
    for path in query_paths:
        status, _, body = http_request(
            app_url, path, api_key=api_key, method="DELETE"
        )
        last_status = status
        last_body = body
        if status in (200, 202, 204):
            return
        if status == 404:
            return
    raise RuntimeError(
        f"{app_name}: failed deleting queue item id={item_id} "
        f"(HTTP {last_status}): {last_body}"
    )


def ensure_arr_failed_queue_cleanup(app_cfg, app_url, api_base, api_key, hygiene_cfg):
    app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
    queue_cfg = (hygiene_cfg.get("arr_failed_queue_cleanup") or {})
    if not bool_cfg(queue_cfg, "enabled", True):
        return 0

    app_overrides = resolve_arr_overrides_by_app(queue_cfg, app_cfg)
    if "enabled" in app_overrides and not bool(app_overrides.get("enabled")):
        return 0

    failed_tokens = [
        normalize_token(x)
        for x in coerce_list(
            app_overrides.get("failed_status_tokens")
            or queue_cfg.get("failed_status_tokens")
            or ["failed", "error", "importfailed", "warning"]
        )
        if normalize_token(x)
    ]
    page_size = to_int(app_overrides.get("page_size"), to_int(queue_cfg.get("page_size"), 250))
    if page_size is None or page_size <= 0:
        page_size = 250
    max_delete = to_int(
        app_overrides.get("max_delete_per_run"),
        to_int(queue_cfg.get("max_delete_per_run"), 50),
    )
    if max_delete is None or max_delete <= 0:
        max_delete = 50

    queue_paths = [
        f"{api_base}/queue?page=1&pageSize={page_size}&sortKey=timeleft&sortDirection=ascending",
        f"{api_base}/queue?page=1&pageSize={page_size}",
        f"{api_base}/queue",
    ]
    payload = None
    last_status = None
    last_body = ""
    for path in queue_paths:
        status, data, body = http_request(app_url, path, api_key=api_key)
        last_status = status
        last_body = body
        if status == 200:
            payload = data
            break
        if status in (404, 405):
            continue
        raise RuntimeError(
            f"{app_name}: failed reading queue (HTTP {status}): {body}"
        )
    if payload is None:
        log(
            f"[WARN] {app_name}: queue endpoint unavailable; skipping failed queue cleanup "
            f"(last_status={last_status}, last_body={last_body})"
        )
        return 0

    records = arr_queue_records(payload)
    if not records:
        log(f"[OK] {app_name}: queue cleanup found no records.")
        return 0

    to_delete = []
    for record in records:
        if queue_item_is_failed(record, failed_tokens):
            item_id = to_int(record.get("id"))
            if item_id is not None:
                to_delete.append(int(item_id))
    if not to_delete:
        log(f"[OK] {app_name}: queue cleanup found no failed records.")
        return 0

    remove_from_client = bool_cfg(queue_cfg, "remove_from_client", True)
    blocklist = bool_cfg(queue_cfg, "blocklist", True)
    deleted = 0
    for item_id in to_delete[:max_delete]:
        delete_queue_item(
            app_name,
            app_url,
            api_base,
            api_key,
            item_id,
            remove_from_client=remove_from_client,
            blocklist=blocklist,
        )
        deleted += 1

    log(
        f"[OK] {app_name}: cleaned failed queue items "
        f"(deleted={deleted}, matched={len(to_delete)}, remove_from_client={remove_from_client}, blocklist={blocklist})"
    )
    return deleted


def _walk_existing_files(paths):
    for root in paths:
        if not root.exists():
            continue
        for dirpath, _, filenames in os.walk(root):
            base = Path(dirpath)
            for name in filenames:
                yield base / name


def run_filesystem_hygiene(hygiene_cfg):
    fs_cfg = (hygiene_cfg.get("filesystem") or {})
    if not bool_cfg(fs_cfg, "enabled", True):
        return {"removed_temp": 0, "removed_zero": 0, "removed_dupes": 0, "removed_empty_dirs": 0}

    default_roots = [
        "/srv-stack/data/torrents/incomplete",
        "/srv-stack/data/torrents/completed",
        "/srv-stack/data/usenet/incomplete",
        "/srv-stack/data/usenet/completed",
    ]
    raw_roots = coerce_list(fs_cfg.get("roots")) or default_roots
    roots = [Path(str(p)).resolve() for p in raw_roots if str(p).strip()]
    min_age_hours = _to_float(fs_cfg.get("min_file_age_hours"), 24.0)
    if min_age_hours is None:
        min_age_hours = 24.0
    now_ts = time.time()

    remove_zero = bool_cfg(fs_cfg, "remove_zero_byte_files", True)
    temp_extensions = {
        str(x).strip().lower()
        for x in coerce_list(fs_cfg.get("temp_extensions"))
        if str(x).strip()
    } or {".part", ".tmp", ".temp", ".nzb", ".!qb"}
    remove_empty_dirs = bool_cfg(fs_cfg, "remove_empty_dirs", True)

    dedupe_cfg = fs_cfg.get("dedupe") or {}
    dedupe_enabled = bool_cfg(dedupe_cfg, "enabled", True)
    dedupe_dry_run = bool_cfg(dedupe_cfg, "dry_run", False)
    dedupe_max_deletes = to_int(dedupe_cfg.get("max_delete_per_run"), 20) or 20
    dedupe_min_size = to_int(dedupe_cfg.get("min_size_bytes"), 100 * 1024 * 1024) or (
        100 * 1024 * 1024
    )

    removed_temp = 0
    removed_zero = 0
    removed_dupes = 0
    removed_empty = 0
    dedupe_map = defaultdict(list)

    for file_path in _walk_existing_files(roots):
        try:
            st = file_path.stat()
        except FileNotFoundError:
            continue
        except Exception:
            continue

        age_hours = max(0.0, (now_ts - float(st.st_mtime)) / 3600.0)
        suffix = file_path.suffix.lower()
        if age_hours >= min_age_hours:
            if remove_zero and int(st.st_size) <= 0:
                try:
                    file_path.unlink()
                    removed_zero += 1
                except Exception:
                    pass
                continue
            if suffix in temp_extensions:
                try:
                    file_path.unlink()
                    removed_temp += 1
                except Exception:
                    pass
                continue

        if dedupe_enabled and int(st.st_size) >= dedupe_min_size:
            key = (file_path.name.lower(), int(st.st_size))
            dedupe_map[key].append((file_path, st.st_mtime))

    if dedupe_enabled and dedupe_map:
        deletions_left = dedupe_max_deletes
        for _, items in dedupe_map.items():
            if deletions_left <= 0:
                break
            if len(items) <= 1:
                continue
            items.sort(key=lambda t: t[1], reverse=True)
            for dup_path, _ in items[1:]:
                if deletions_left <= 0:
                    break
                if dedupe_dry_run:
                    log(f"[INFO] Media hygiene: dedupe candidate (dry-run): {dup_path}")
                    continue
                try:
                    dup_path.unlink()
                    removed_dupes += 1
                    deletions_left -= 1
                    log(f"[OK] Media hygiene: removed duplicate file {dup_path}")
                except Exception:
                    continue

    if remove_empty_dirs:
        for root in roots:
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                if dirnames or filenames:
                    continue
                p = Path(dirpath)
                if p == root:
                    continue
                try:
                    p.rmdir()
                    removed_empty += 1
                except Exception:
                    continue

    summary = {
        "removed_temp": removed_temp,
        "removed_zero": removed_zero,
        "removed_dupes": removed_dupes,
        "removed_empty_dirs": removed_empty,
    }
    log(
        "[OK] Media hygiene filesystem cleanup: "
        f"temp={removed_temp}, zero_byte={removed_zero}, duplicates={removed_dupes}, empty_dirs={removed_empty}"
    )
    return summary


def run_qbit_duplicate_prune(hygiene_cfg, qbit_cfg, qb_username, qb_password):
    prune_cfg = (hygiene_cfg.get("qbit_duplicate_prune") or {})
    enabled = bool_cfg(prune_cfg, "enabled", False)
    summary = {
        "enabled": enabled,
        "dry_run": bool_cfg(prune_cfg, "dry_run", False),
        "groups": 0,
        "candidates": 0,
        "deleted": 0,
    }
    if not enabled:
        return summary

    if not str(qb_username or "").strip() or not str(qb_password or "").strip():
        raise RuntimeError(
            "qB duplicate prune requires qB credentials (QBITTORRENT_USERNAME/QBITTORRENT_PASSWORD)."
        )

    qbit_url = normalize_url((qbit_cfg or {}).get("url", "http://qbittorrent:8080"))
    dry_run = summary["dry_run"]
    delete_files = bool_cfg(prune_cfg, "delete_files", False)
    max_delete_per_run = to_int(prune_cfg.get("max_delete_per_run"), 30)
    if max_delete_per_run is None or max_delete_per_run <= 0:
        max_delete_per_run = 30
    min_completion_age_hours = _to_float(prune_cfg.get("min_completion_age_hours"), 24.0)
    if min_completion_age_hours is None:
        min_completion_age_hours = 24.0
    keep_strategy = str(prune_cfg.get("keep", "oldest") or "oldest").strip().lower()
    if keep_strategy not in ("oldest", "newest"):
        keep_strategy = "oldest"
    include_category = bool_cfg(prune_cfg, "include_category_in_key", True)
    match_on_hash = bool_cfg(prune_cfg, "match_on_hash", True)
    match_on_name_size = bool_cfg(prune_cfg, "match_on_name_size", True)
    if not match_on_hash and not match_on_name_size:
        match_on_name_size = True

    raw_categories = [str(x).strip() for x in coerce_list(prune_cfg.get("categories")) if str(x).strip()]
    if not raw_categories:
        raw_categories = [
            str(v).strip()
            for v in ((qbit_cfg or {}).get("categories") or {}).values()
            if str(v).strip()
        ]
    categories = sorted(set(raw_categories))

    opener = qbit_login(qbit_url, qb_username, qb_password)
    torrents = qbit_list_completed_torrents(opener, qbit_url)
    now = int(time.time())
    groups = defaultdict(list)

    for item in torrents:
        if not isinstance(item, dict):
            continue
        thash = str(item.get("hash") or "").strip()
        if not thash:
            continue
        category = str(item.get("category") or "").strip()
        if categories and category not in categories:
            continue

        completion_on = to_int(item.get("completion_on"), 0) or 0
        added_on = to_int(item.get("added_on"), 0) or 0
        reference_on = completion_on if completion_on > 0 else added_on
        age_hours = 0.0
        if reference_on > 0:
            age_hours = max(0.0, float(now - reference_on) / 3600.0)
        if age_hours < min_completion_age_hours:
            continue

        normalized_name = normalize_token(item.get("name") or "")
        size_bytes = to_int(item.get("size"), 0) or 0
        record = {
            "hash": thash,
            "name": str(item.get("name") or "").strip(),
            "normalized_name": normalized_name,
            "size": size_bytes,
            "category": category,
            "completion_on": completion_on,
            "added_on": added_on,
        }

        if match_on_hash:
            groups[("hash", thash)].append(record)
        if match_on_name_size and normalized_name and size_bytes > 0:
            if include_category:
                groups[("name_size", category.lower(), normalized_name, size_bytes)].append(record)
            else:
                groups[("name_size", normalized_name, size_bytes)].append(record)

    delete_hashes = []
    delete_seen = set()
    duplicate_groups = 0
    for group_items in groups.values():
        if len(group_items) <= 1:
            continue
        duplicate_groups += 1
        sorted_items = sorted(
            group_items,
            key=lambda x: (
                x.get("completion_on") or x.get("added_on") or 0,
                x.get("hash") or "",
            ),
            reverse=(keep_strategy == "newest"),
        )
        keep_hash = str(sorted_items[0].get("hash") or "").strip()
        for candidate in sorted_items[1:]:
            candidate_hash = str(candidate.get("hash") or "").strip()
            if not candidate_hash or candidate_hash == keep_hash or candidate_hash in delete_seen:
                continue
            delete_hashes.append(candidate_hash)
            delete_seen.add(candidate_hash)
            if len(delete_hashes) >= max_delete_per_run:
                break
        if len(delete_hashes) >= max_delete_per_run:
            break

    summary["groups"] = duplicate_groups
    summary["candidates"] = len(delete_hashes)
    if not delete_hashes:
        log(
            "[OK] Media hygiene qB duplicate prune: no duplicate completed torrents found "
            f"(groups={duplicate_groups}, categories={categories or 'all'})."
        )
        return summary

    if dry_run:
        for thash in delete_hashes:
            log(f"[INFO] Media hygiene qB duplicate prune candidate (dry-run): {thash}")
        log(
            "[OK] Media hygiene qB duplicate prune: dry-run complete "
            f"(groups={duplicate_groups}, candidates={len(delete_hashes)})."
        )
        return summary

    qbit_delete_torrents(opener, qbit_url, delete_hashes, delete_files=delete_files)
    summary["deleted"] = len(delete_hashes)
    log(
        "[OK] Media hygiene qB duplicate prune: removed duplicate torrents "
        f"(deleted={len(delete_hashes)}, groups={duplicate_groups}, delete_files={delete_files})."
    )
    return summary


def run_qbit_ipfilter_refresh(hygiene_cfg, qbit_cfg, qb_username, qb_password):
    ipf_cfg = (hygiene_cfg.get("qbit_ipfilter") or {})
    enabled = bool_cfg(ipf_cfg, "enabled", False)
    summary = {
        "enabled": enabled,
        "downloaded": False,
        "applied": False,
        "skipped_reason": "",
        "source_url": "",
        "target_path": "",
        "bytes": 0,
    }
    if not enabled:
        return summary

    if not str(qb_username or "").strip() or not str(qb_password or "").strip():
        raise RuntimeError(
            "qB IP filter refresh requires qB credentials (QBITTORRENT_USERNAME/QBITTORRENT_PASSWORD)."
        )

    qbit_url = normalize_url((qbit_cfg or {}).get("url", "http://qbittorrent:8080"))
    required = bool_cfg(ipf_cfg, "required", False)
    apply_existing_on_failure = bool_cfg(
        ipf_cfg, "apply_existing_on_download_failure", True
    )
    source_url = str(
        ipf_cfg.get("url")
        or ipf_cfg.get("source_url")
        or "https://github.com/DavidMoore/ipfilter/releases/download/lists/ipfilter.dat"
    ).strip()
    fallback_urls = [
        str(x).strip()
        for x in coerce_list(ipf_cfg.get("fallback_urls"))
        if str(x).strip()
    ]
    urls = []
    if source_url:
        urls.append(source_url)
    for item in fallback_urls:
        if item not in urls:
            urls.append(item)

    target_path = str(
        ipf_cfg.get("target_path") or "/srv-stack/data/torrents/ipfilter.dat"
    ).strip()
    qbit_filter_path = str(
        ipf_cfg.get("qbit_filter_path") or "/data/torrents/ipfilter.dat"
    ).strip()
    mirror_target_paths = [
        str(x).strip()
        for x in coerce_list(
            ipf_cfg.get("mirror_target_paths")
            or ["/srv-host-stack/data/torrents/ipfilter.dat"]
        )
        if str(x).strip()
    ]
    target_candidates = [target_path]
    for mirror in mirror_target_paths:
        if mirror not in target_candidates:
            target_candidates.append(mirror)
    state_path = str(
        ipf_cfg.get("state_path")
        or "/srv-stack/data/torrents/.ipfilter-refresh-state.json"
    ).strip()
    timeout_seconds = to_int(ipf_cfg.get("download_timeout_seconds"), 30) or 30
    min_valid_bytes = to_int(ipf_cfg.get("min_valid_bytes"), 1024) or 1024
    min_refresh_interval_hours = (
        _to_float(ipf_cfg.get("min_refresh_interval_hours"), 24.0) or 24.0
    )

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mirror_paths = [Path(p) for p in target_candidates[1:]]
    for mirror in mirror_paths:
        try:
            mirror.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
    state_file = Path(state_path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    summary["target_path"] = str(target)

    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}

    now_epoch = int(time.time())
    min_refresh_seconds = max(0, int(min_refresh_interval_hours * 3600))
    last_success = to_int(state.get("last_success_epoch"), 0) or 0
    downloaded = False

    if (
        min_refresh_seconds > 0
        and last_success > 0
        and (now_epoch - last_success) < min_refresh_seconds
    ):
        summary["skipped_reason"] = "min_refresh_interval"
        summary["source_url"] = str(state.get("source_url") or source_url)
        summary["bytes"] = to_int(state.get("bytes"), 0) or 0
        log(
            "[INFO] qB IP filter: skipping download due to min refresh interval "
            f"(last_success={last_success}, min_hours={min_refresh_interval_hours})."
        )
    else:
        errors = []
        data = b""
        selected_url = ""
        for candidate in urls:
            selected_url = candidate
            try:
                req = request.Request(
                    candidate,
                    method="GET",
                    headers={"User-Agent": "media-stack-ipfilter/1.0"},
                )
                with request.urlopen(req, timeout=timeout_seconds) as resp:
                    data = resp.read()
                if len(data) < min_valid_bytes:
                    raise RuntimeError(
                        f"Downloaded file too small ({len(data)} bytes, expected >= {min_valid_bytes})."
                    )
                tmp_path = target.with_name(f"{target.name}.tmp")
                tmp_path.write_bytes(data)
                os.replace(tmp_path, target)
                for mirror in mirror_paths:
                    try:
                        mirror_tmp = mirror.with_name(f"{mirror.name}.tmp")
                        mirror_tmp.write_bytes(data)
                        os.replace(mirror_tmp, mirror)
                    except Exception as mirror_exc:
                        log(
                            f"[WARN] qB IP filter: mirror write failed for {mirror} "
                            f"({mirror_exc})"
                        )
                downloaded = True
                summary["downloaded"] = True
                summary["source_url"] = selected_url
                summary["bytes"] = len(data)
                break
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue

        if not downloaded:
            cached_target = None
            for candidate in [target] + mirror_paths:
                if candidate.exists():
                    cached_target = candidate
                    break
            if cached_target is not None and apply_existing_on_failure:
                summary["skipped_reason"] = "source_unavailable_using_cached_filter"
                summary["source_url"] = str(state.get("source_url") or source_url)
                summary["bytes"] = cached_target.stat().st_size
                summary["target_path"] = str(cached_target)
                log(
                    "[WARN] qB IP filter: download source unavailable; using cached filter file "
                    f"at {cached_target}."
                )
                if errors:
                    log(f"[WARN] qB IP filter: download errors: {' | '.join(errors)}")
            else:
                message = (
                    "qB IP filter: unable to download filter and no usable cached copy exists "
                    f"(targets={target_candidates}, urls={urls}, errors={errors})."
                )
                if required:
                    raise RuntimeError(message)
                log(f"[WARN] {message}")
                return summary

    opener = qbit_login(qbit_url, qb_username, qb_password)
    qbit_set_preferences(
        opener,
        qbit_url,
        {
            "ip_filter_enabled": True,
            "ip_filter_path": qbit_filter_path,
        },
    )
    summary["applied"] = True
    log(
        "[OK] qB IP filter: preferences applied "
        f"(enabled=True, path={qbit_filter_path}, downloaded={summary['downloaded']})."
    )

    if downloaded:
        now = datetime.now(timezone.utc)
        state.update(
            {
                "last_success_epoch": int(now.timestamp()),
                "last_success_iso": now.isoformat(),
                "source_url": summary["source_url"],
                "bytes": summary["bytes"],
                "target_path": str(target),
                "qbit_filter_path": qbit_filter_path,
            }
        )
        try:
            state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            log(f"[WARN] qB IP filter: failed writing state file {state_file} ({exc})")
    return summary


def run_media_hygiene(cfg, config_root, arr_apps, app_keys, qbit_cfg=None, qb_username="", qb_password=""):
    hygiene_cfg = cfg.get("media_hygiene") or {}
    if not bool_cfg(hygiene_cfg, "enabled", False):
        return

    deleted_queue = 0
    app_errors = 0
    if bool_cfg(hygiene_cfg, "cleanup_arr_failed_queue", True):
        for app in arr_apps:
            impl = str(app.get("implementation") or "")
            app_url = normalize_url(app.get("url") or "")
            if not impl or not app_url:
                continue
            app_key = app_keys.get(impl)
            if not app_key:
                continue
            try:
                api_base = detect_arr_api_base(app.get("name") or impl, app_url, app_key)
                deleted_queue += ensure_arr_failed_queue_cleanup(
                    app, app_url, api_base, app_key, hygiene_cfg
                )
            except Exception as exc:
                app_errors += 1
                log(
                    f"[WARN] Media hygiene: queue cleanup skipped for {app.get('name') or impl} "
                    f"({exc})"
                )

    fs_summary = run_filesystem_hygiene(hygiene_cfg)
    qbit_queue_summary = {
        "enabled": False,
        "dry_run": False,
        "total": 0,
        "over_limit_candidates": 0,
        "stale_candidates": 0,
        "over_limit_deleted": 0,
        "stale_deleted": 0,
        "by_category": {},
    }
    qbit_ipfilter_summary = {
        "enabled": False,
        "downloaded": False,
        "applied": False,
        "skipped_reason": "",
        "source_url": "",
        "target_path": "",
        "bytes": 0,
    }
    qbit_summary = {"enabled": False, "dry_run": False, "groups": 0, "candidates": 0, "deleted": 0}
    qbit_errors = 0
    if bool_cfg(hygiene_cfg.get("qbit_ipfilter") or {}, "enabled", False):
        try:
            qbit_ipfilter_summary = run_qbit_ipfilter_refresh(
                hygiene_cfg,
                qbit_cfg or {},
                qb_username,
                qb_password,
            )
        except Exception as exc:
            qbit_errors += 1
            log(f"[WARN] Media hygiene: qB IP filter refresh skipped ({exc})")

    if bool_cfg((qbit_cfg or {}).get("queue_guardrails") or {}, "enabled", False):
        try:
            qbit_queue_summary = run_qbit_queue_guardrails(
                qbit_cfg or {},
                qb_username,
                qb_password,
            )
        except Exception as exc:
            qbit_errors += 1
            log(f"[WARN] Media hygiene: qB queue guardrails skipped ({exc})")

    if bool_cfg(hygiene_cfg.get("qbit_duplicate_prune") or {}, "enabled", False):
        try:
            qbit_summary = run_qbit_duplicate_prune(
                hygiene_cfg,
                qbit_cfg or {},
                qb_username,
                qb_password,
            )
        except Exception as exc:
            qbit_errors += 1
            log(f"[WARN] Media hygiene: qB duplicate prune skipped ({exc})")

    log(
        "[OK] Media hygiene: reconcile complete "
        f"(queue_deleted={deleted_queue}, queue_errors={app_errors}, "
        f"qbit_ipfilter={qbit_ipfilter_summary}, "
        f"qbit_queue={qbit_queue_summary}, qbit_dupes={qbit_summary}, "
        f"qbit_errors={qbit_errors}, fs={fs_summary})"
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
    categories = [str(x).strip() for x in coerce_list(qbit_cleanup_cfg.get("categories")) if str(x).strip()]
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
        candidates = candidates[: max_delete_per_run]

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
    query = dict(params or {})
    query["apikey"] = api_key
    query["output"] = "json"
    path = f"/api?{parse.urlencode(query)}"
    return http_request(normalize_url(base_url), path, timeout=timeout)


def sabnzbd_get_config_section(base_url, sab_api_key, section):
    status, data, body = sabnzbd_request(
        base_url, sab_api_key, {"mode": "get_config", "section": section}
    )
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(
            f"SABnzbd: failed reading config section '{section}' (HTTP {status}): {body}"
        )
    config = data.get("config", {})
    if not isinstance(config, dict):
        return None
    return config.get(section)


def ensure_sabnzbd_defaults(sab_cfg, sab_api_key):
    if not sab_api_key:
        return

    sab_url = normalize_url(sab_cfg.get("url", "http://sabnzbd:8080"))
    misc = sabnzbd_get_config_section(sab_url, sab_api_key, "misc")
    if not isinstance(misc, dict):
        raise RuntimeError("SABnzbd: unexpected misc config payload from API.")

    desired_misc = {
        "download_dir": str(
            sab_cfg.get("incomplete_dir", "/data/usenet/incomplete")
        ).strip(),
        "complete_dir": str(
            sab_cfg.get("complete_dir", "/data/usenet/completed")
        ).strip(),
    }
    if "auto_browser" in sab_cfg:
        desired_misc["auto_browser"] = "1" if bool(sab_cfg.get("auto_browser")) else "0"

    for key, desired in desired_misc.items():
        if not desired:
            continue
        current = misc.get(key)
        if isinstance(current, bool):
            current_normalized = "1" if current else "0"
        elif current is None:
            current_normalized = ""
        else:
            current_normalized = str(current).strip()

        desired_normalized = str(desired).strip()
        if current_normalized == desired_normalized:
            log(f"[OK] SABnzbd: {key} already set to {desired_normalized}")
            continue

        status, data, body = sabnzbd_request(
            sab_url,
            sab_api_key,
            {
                "mode": "set_config",
                "section": "misc",
                "keyword": key,
                "value": desired_normalized,
            },
        )
        if status != 200:
            raise RuntimeError(
                f"SABnzbd: failed setting misc.{key} (HTTP {status}): {body}"
            )
        if isinstance(data, dict) and data.get("status") is False:
            raise RuntimeError(
                f"SABnzbd: API rejected misc.{key} update request: {body}"
            )
        log(f"[OK] SABnzbd: set {key}={desired_normalized}")


def ensure_sabnzbd_categories(arr_apps, sab_cfg, sab_api_key):
    if not sab_api_key:
        return

    sab_url = normalize_url(sab_cfg.get("url", "http://sabnzbd:8080"))
    categories_section = sabnzbd_get_config_section(sab_url, sab_api_key, "categories")
    current_by_name = {}
    if isinstance(categories_section, list):
        for entry in categories_section:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            current_by_name[name.lower()] = normalize_mapping_path(entry.get("dir"))
    else:
        status, data, body = sabnzbd_request(sab_url, sab_api_key, {"mode": "get_cats"})
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(
                f"SABnzbd: failed listing categories (HTTP {status}): {body}"
            )
        for category_name in coerce_list(data.get("categories")):
            c = str(category_name).strip()
            if c:
                current_by_name[c.lower()] = ""

    category_values = [choose_category(app, sab_cfg) for app in arr_apps]
    desired_categories = []
    seen = set()
    for cat in category_values:
        c = str(cat).strip()
        if not c:
            continue
        low = c.lower()
        if low in seen:
            continue
        seen.add(low)
        desired_categories.append(c)

    completed_paths = sab_cfg.get("completed_paths", {})
    complete_root = normalize_mapping_path(
        sab_cfg.get("complete_dir", "/data/usenet/completed")
    ) or "/data/usenet/completed"
    for category in desired_categories:
        current_dir = current_by_name.get(category.lower())
        category_dir = normalize_mapping_path(
            completed_paths.get(category, f"{complete_root}/{category}")
        )
        if current_dir is not None and current_dir == category_dir:
            log(f"[OK] SABnzbd: category already set: {category} -> {category_dir}")
            continue

        status, data, body = sabnzbd_request(
            sab_url,
            sab_api_key,
            {
                "mode": "set_config",
                "section": "categories",
                "name": category,
                "dir": category_dir,
            },
        )
        if status != 200:
            raise RuntimeError(
                f"SABnzbd: failed creating category '{category}' (HTTP {status}): {body}"
            )
        if isinstance(data, dict) and data.get("status") is False:
            raise RuntimeError(
                f"SABnzbd: API rejected category '{category}' create request: {body}"
            )
        action = "updated" if current_dir is not None else "created"
        log(f"[OK] SABnzbd: {action} category {category} -> {category_dir}")


def resolve_schema_contract(prowlarr_url, prowlarr_key, implementation):
    status, data, body = http_request(
        prowlarr_url, "/api/v1/applications/schema", api_key=prowlarr_key
    )
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(
            f"Prowlarr: failed to read application schema (HTTP {status}): {body}"
        )

    for entry in data:
        if entry.get("implementation") == implementation:
            return entry
    raise RuntimeError(f"Prowlarr: no application schema found for {implementation}")


def find_existing_application(prowlarr_url, prowlarr_key, implementation, base_url):
    status, data, body = http_request(
        prowlarr_url, "/api/v1/applications", api_key=prowlarr_key
    )
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(
            f"Prowlarr: failed to list applications (HTTP {status}): {body}"
        )

    for app in data:
        if app.get("implementation") != implementation:
            continue
        values = field_map(app.get("fields"))
        app_base = str(values.get("baseUrl", "")).rstrip("/")
        if app_base == base_url.rstrip("/"):
            return app
    return None


def ensure_prowlarr_application(
    prowlarr_url, prowlarr_key, app_name, implementation, app_url, app_key
):
    schema = resolve_schema_contract(prowlarr_url, prowlarr_key, implementation)
    current = find_existing_application(
        prowlarr_url, prowlarr_key, implementation, app_url
    )

    values = field_map(schema.get("fields"))
    values["baseUrl"] = app_url
    values["apiKey"] = app_key
    if "prowlarrUrl" in values:
        values["prowlarrUrl"] = prowlarr_url

    payload = {
        "name": app_name,
        "implementation": implementation,
        "configContract": schema.get("configContract", f"{implementation}Settings"),
        "enable": True,
        "fields": field_list(values),
        "tags": [],
        "syncLevel": "fullSync",
    }

    def put_or_post(method, path, body):
        status, _, response_body = http_request(
            prowlarr_url,
            path,
            api_key=prowlarr_key,
            method=method,
            payload=body,
        )
        if status in (200, 201, 202):
            return True, status, response_body

        # Compatibility fallback for versions that don't accept syncLevel shape/value.
        if "syncLevel" in body:
            fallback = dict(body)
            fallback.pop("syncLevel", None)
            status2, _, response_body2 = http_request(
                prowlarr_url,
                path,
                api_key=prowlarr_key,
                method=method,
                payload=fallback,
            )
            if status2 in (200, 201, 202):
                return True, status2, response_body2
            return False, status2, response_body2

        return False, status, response_body

    if current:
        payload["id"] = current.get("id")
        ok, status, body = put_or_post(
            "PUT", f"/api/v1/applications/{current.get('id')}", payload
        )
        if ok:
            log(f"[OK] Prowlarr: updated application link for {app_name}")
            return
        raise RuntimeError(
            f"Prowlarr: failed updating app {app_name} (HTTP {status}): {body}"
        )

    ok, status, body = put_or_post("POST", "/api/v1/applications", payload)
    if ok:
        log(f"[OK] Prowlarr: created application link for {app_name}")
        return
    raise RuntimeError(
        f"Prowlarr: failed creating app {app_name} (HTTP {status}): {body}"
    )


def trigger_prowlarr_sync(prowlarr_url, prowlarr_key):
    status, _, body = http_request(
        prowlarr_url,
        "/api/v1/command",
        api_key=prowlarr_key,
        method="POST",
        payload={"name": "ApplicationIndexerSync"},
    )
    if status in (200, 201, 202):
        log("[OK] Prowlarr: triggered ApplicationIndexerSync")
        return
    raise RuntimeError(
        f"Prowlarr: failed to trigger ApplicationIndexerSync (HTTP {status}): {body}"
    )


def ensure_prowlarr_indexer(prowlarr_url, prowlarr_key, indexer_cfg):
    implementation = indexer_cfg["implementation"]
    name = indexer_cfg["name"]
    field_overrides = indexer_cfg.get("fields", {})

    status, schemas, body = http_request(
        prowlarr_url, "/api/v1/indexer/schema", api_key=prowlarr_key
    )
    if status != 200 or not isinstance(schemas, list):
        raise RuntimeError(
            f"Prowlarr: failed to read indexer schema (HTTP {status}): {body}"
        )

    schema = None
    for entry in schemas:
        if entry.get("implementation") == implementation:
            schema = entry
            break
    if not schema:
        raise RuntimeError(f"Prowlarr: no indexer schema found for {implementation}")

    status, current_indexers, body = http_request(
        prowlarr_url, "/api/v1/indexer", api_key=prowlarr_key
    )
    if status != 200 or not isinstance(current_indexers, list):
        raise RuntimeError(
            f"Prowlarr: failed to list indexers (HTTP {status}): {body}"
        )

    current = None
    for item in current_indexers:
        if item.get("implementation") == implementation and item.get("name") == name:
            current = item
            break

    values = field_map(schema.get("fields"))
    values.update(field_overrides)

    payload = {
        "name": name,
        "implementation": implementation,
        "configContract": schema.get("configContract", f"{implementation}Settings"),
        "enable": bool(indexer_cfg.get("enable", True)),
        "priority": int(indexer_cfg.get("priority", 25)),
        "tags": indexer_cfg.get("tags", []),
        "fields": field_list(values),
    }

    if current:
        payload["id"] = current.get("id")
        status, _, body = http_request(
            prowlarr_url,
            f"/api/v1/indexer/{current.get('id')}",
            api_key=prowlarr_key,
            method="PUT",
            payload=payload,
        )
        if status in (200, 202):
            log(f"[OK] Prowlarr: updated indexer {name}")
            return
        raise RuntimeError(
            f"Prowlarr: failed to update indexer {name} (HTTP {status}): {body}"
        )

    status, _, body = http_request(
        prowlarr_url,
        "/api/v1/indexer",
        api_key=prowlarr_key,
        method="POST",
        payload=payload,
    )
    if status in (200, 201, 202):
        log(f"[OK] Prowlarr: created indexer {name}")
        return
    raise RuntimeError(
        f"Prowlarr: failed to create indexer {name} (HTTP {status}): {body}"
    )


def build_indexer_payload(template):
    allowed_keys = {
        "name",
        "implementation",
        "configContract",
        "fields",
        "priority",
        "tags",
        "appProfileId",
        "downloadClientId",
        "enable",
        "redirect",
        "enableRss",
        "enableAutomaticSearch",
        "enableInteractiveSearch",
    }
    payload = {}
    for key in allowed_keys:
        if key in template and template[key] is not None:
            payload[key] = template[key]

    payload.setdefault("enable", True)
    payload.setdefault("priority", 25)
    payload.setdefault("tags", [])
    payload.setdefault("fields", [])
    return payload


def auto_add_tested_indexers(prowlarr_url, prowlarr_key):
    status, schemas, body = http_request(
        prowlarr_url, "/api/v1/indexer/schema", api_key=prowlarr_key
    )
    if status != 200 or not isinstance(schemas, list):
        raise RuntimeError(
            f"Prowlarr: failed to read indexer schema (HTTP {status}): {body}"
        )

    status, existing, body = http_request(
        prowlarr_url, "/api/v1/indexer", api_key=prowlarr_key
    )
    if status != 200 or not isinstance(existing, list):
        raise RuntimeError(
            f"Prowlarr: failed to list existing indexers (HTTP {status}): {body}"
        )

    existing_keys = {
        (item.get("implementation"), item.get("name"))
        for item in existing
        if item.get("implementation") and item.get("name")
    }

    candidates = []
    for schema in schemas:
        presets = schema.get("presets") or []
        if presets:
            candidates.extend(presets)
        else:
            candidates.append(schema)

    heartbeat_every = int(os.environ.get("AUTO_INDEXER_HEARTBEAT_EVERY", "25"))
    heartbeat_every = max(1, heartbeat_every)
    log_skip_details = str(os.environ.get("AUTO_INDEXER_LOG_SKIPS", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    scanned = 0
    attempted = 0
    added = 0
    skipped_existing = 0
    skipped_test = 0
    failed_create = 0

    for candidate in candidates:
        payload = build_indexer_payload(candidate)
        impl = payload.get("implementation")
        name = payload.get("name")
        if not impl or not name:
            continue

        scanned += 1
        key = (impl, name)
        if key in existing_keys:
            skipped_existing += 1
            if scanned % heartbeat_every == 0:
                log(
                    "[WAIT] Auto indexer progress: "
                    f"scanned={scanned}/{len(candidates)}, attempted={attempted}, "
                    f"added={added}, skipped_existing={skipped_existing}, "
                    f"skipped_test={skipped_test}, failed_create={failed_create}"
                )
            continue

        attempted += 1

        status, _, body = http_request(
            prowlarr_url,
            "/api/v1/indexer/test",
            api_key=prowlarr_key,
            method="POST",
            payload=payload,
        )
        if status not in (200, 201, 202):
            skipped_test += 1
            if log_skip_details:
                log(f"[SKIP] {name}: test failed (HTTP {status})")
            continue

        status, _, body = http_request(
            prowlarr_url,
            "/api/v1/indexer",
            api_key=prowlarr_key,
            method="POST",
            payload=payload,
        )
        if status in (200, 201, 202):
            existing_keys.add(key)
            added += 1
            log(f"[ADD] {name}")
        else:
            failed_create += 1
            log(f"[FAIL] {name}: create failed (HTTP {status}) {body}")

        if scanned % heartbeat_every == 0:
            log(
                "[WAIT] Auto indexer progress: "
                f"scanned={scanned}/{len(candidates)}, attempted={attempted}, "
                f"added={added}, skipped_existing={skipped_existing}, "
                f"skipped_test={skipped_test}, failed_create={failed_create}"
            )

    log(
        "[OK] Auto indexer summary: "
        f"scanned={scanned}/{len(candidates)}, attempted={attempted}, added={added}, "
        f"skipped_existing={skipped_existing}, skipped_test={skipped_test}, "
        f"failed_create={failed_create}"
    )


def ensure_jellyseerr_main_settings(jellyseerr_url, jellyseerr_key, jelly_cfg):
    media_server_type = jelly_cfg.get("media_server_type")
    if media_server_type is None and bool_cfg(
        jelly_cfg, "set_media_server_type_jellyfin", True
    ):
        # Jellyseerr MediaServerType enum: 2 = Jellyfin.
        media_server_type = 2

    if media_server_type is None:
        return

    status, current, body = http_request(
        jellyseerr_url, "/api/v1/settings/main", api_key=jellyseerr_key
    )
    if status != 200 or not isinstance(current, dict):
        raise RuntimeError(
            f"Jellyseerr: failed to read main settings (HTTP {status}): {body}"
        )

    desired_type = int(media_server_type)
    if to_int(current.get("mediaServerType")) == desired_type:
        log(f"[OK] Jellyseerr: mediaServerType already set to {desired_type}")
        return

    status, _, body = http_request(
        jellyseerr_url,
        "/api/v1/settings/main",
        api_key=jellyseerr_key,
        method="POST",
        payload={"mediaServerType": desired_type},
    )
    if status in (200, 201, 202):
        log(f"[OK] Jellyseerr: set mediaServerType={desired_type}")
        return

    raise RuntimeError(
        f"Jellyseerr: failed to set mediaServerType (HTTP {status}): {body}"
    )


def ensure_jellyseerr_jellyfin_settings(jellyseerr_url, jellyseerr_key, jelly_cfg, config_root):
    jellyfin_cfg = jelly_cfg.get("jellyfin") or {}
    if not bool_cfg(jellyfin_cfg, "configure", False):
        return

    jellyfin_api_key = resolve_jellyfin_api_key(jellyfin_cfg, config_root)
    if not jellyfin_api_key:
        raise RuntimeError(
            "Jellyseerr: jellyfin.configure=true but Jellyfin API key could not be resolved."
        )

    jellyfin_url = jellyfin_cfg.get("url", "http://jellyfin:8096")
    parsed = parse_service_url(jellyfin_url, 8096)

    payload = {
        "ip": parsed["hostname"],
        "port": parsed["port"],
        "useSsl": parsed["use_ssl"],
        "urlBase": parsed["base_url"],
        "apiKey": jellyfin_api_key,
        "externalHostname": jellyfin_cfg.get("external_url", ""),
        "jellyfinForgotPasswordUrl": jellyfin_cfg.get("forgot_password_url", ""),
    }

    status, _, body = http_request(
        jellyseerr_url,
        "/api/v1/settings/jellyfin",
        api_key=jellyseerr_key,
        method="POST",
        payload=payload,
    )
    if status in (200, 201, 202):
        log("[OK] Jellyseerr: configured Jellyfin connection")
        return

    raise RuntimeError(
        f"Jellyseerr: failed to configure Jellyfin settings (HTTP {status}): {body}"
    )


def ensure_jellyseerr_radarr(
    jellyseerr_url, jellyseerr_key, radarr_app_cfg, radarr_api_key, jelly_cfg
):
    radarr_cfg = jelly_cfg.get("radarr") or {}
    if not bool_cfg(radarr_cfg, "enabled", True):
        return

    parsed = parse_service_url(radarr_app_cfg["url"], 7878)
    test_payload = {
        "hostname": parsed["hostname"],
        "port": parsed["port"],
        "apiKey": radarr_api_key,
        "useSsl": parsed["use_ssl"],
        "baseUrl": parsed["base_url"],
    }

    status, test_data, body = http_request(
        jellyseerr_url,
        "/api/v1/settings/radarr/test",
        api_key=jellyseerr_key,
        method="POST",
        payload=test_payload,
    )
    if status != 200 or not isinstance(test_data, dict):
        raise RuntimeError(
            f"Jellyseerr: Radarr connection test failed (HTTP {status}): {body}"
        )

    profiles = test_data.get("profiles") or []
    if not profiles:
        raise RuntimeError("Jellyseerr: Radarr test returned no quality profiles.")

    selected_profile = choose_profile(
        profiles,
        preferred_id=radarr_cfg.get("active_profile_id"),
        preferred_names=coerce_list(
            radarr_cfg.get("quality_profile_preferred_names")
            or radarr_cfg.get("preferred_profile_names")
            or []
        ),
    )
    if not selected_profile:
        raise RuntimeError("Jellyseerr: unable to choose Radarr profile.")

    root_folders = test_data.get("rootFolders") or []
    preferred_root = radarr_cfg.get("root_folder") or radarr_app_cfg.get("root_folder")
    active_directory = choose_root_folder(root_folders, preferred_root)
    if not active_directory:
        raise RuntimeError("Jellyseerr: unable to choose Radarr root folder.")

    resolved_base_url = normalize_base_path(test_data.get("urlBase") or parsed["base_url"])
    service_name = radarr_cfg.get("name", radarr_app_cfg.get("name", "Radarr"))
    is4k = bool_cfg(radarr_cfg, "is4k", False)

    payload = {
        "name": service_name,
        "hostname": parsed["hostname"],
        "port": parsed["port"],
        "apiKey": radarr_api_key,
        "useSsl": parsed["use_ssl"],
        "baseUrl": resolved_base_url,
        "activeProfileId": to_int(selected_profile.get("id")),
        "activeProfileName": selected_profile.get("name"),
        "activeDirectory": active_directory,
        "is4k": is4k,
        "minimumAvailability": radarr_cfg.get("minimum_availability", "released"),
        "isDefault": bool_cfg(radarr_cfg, "is_default", True),
        "externalUrl": radarr_cfg.get("external_url", ""),
        "syncEnabled": bool_cfg(radarr_cfg, "sync_enabled", True),
        "preventSearch": bool_cfg(radarr_cfg, "prevent_search", False),
        "tagRequests": bool_cfg(radarr_cfg, "tag_requests", False),
        "tags": coerce_list(radarr_cfg.get("tags")),
        "overrideRule": coerce_list(radarr_cfg.get("override_rule")),
    }

    status, existing, body = http_request(
        jellyseerr_url, "/api/v1/settings/radarr", api_key=jellyseerr_key
    )
    if status != 200 or not isinstance(existing, list):
        raise RuntimeError(
            f"Jellyseerr: failed to list Radarr settings (HTTP {status}): {body}"
        )

    current = find_existing_servarr(
        existing,
        payload["name"],
        payload["hostname"],
        payload["port"],
        payload["baseUrl"],
        payload["is4k"],
    )

    if current:
        current_id = current.get("id")
        if current_id is None:
            # Older Jellyseerr settings payloads can contain legacy entries without ids.
            # If a matching entry already exists and our live connection test passed, keep it.
            log(
                "[OK] Jellyseerr: existing Radarr mapping found "
                "(legacy entry without id)"
            )
            return
        status, _, body = http_request(
            jellyseerr_url,
            f"/api/v1/settings/radarr/{current_id}",
            api_key=jellyseerr_key,
            method="PUT",
            payload=payload,
        )
        if status in (200, 201, 202):
            log("[OK] Jellyseerr: updated Radarr service mapping")
            return
        raise RuntimeError(
            f"Jellyseerr: failed updating Radarr mapping (HTTP {status}): {body}"
        )

    status, _, body = http_request(
        jellyseerr_url,
        "/api/v1/settings/radarr",
        api_key=jellyseerr_key,
        method="POST",
        payload=payload,
    )
    if status in (200, 201, 202):
        log("[OK] Jellyseerr: created Radarr service mapping")
        return

    raise RuntimeError(
        f"Jellyseerr: failed creating Radarr mapping (HTTP {status}): {body}"
    )


def ensure_jellyseerr_sonarr(
    jellyseerr_url, jellyseerr_key, sonarr_app_cfg, sonarr_api_key, jelly_cfg
):
    sonarr_cfg = jelly_cfg.get("sonarr") or {}
    if not bool_cfg(sonarr_cfg, "enabled", True):
        return

    parsed = parse_service_url(sonarr_app_cfg["url"], 8989)
    test_payload = {
        "hostname": parsed["hostname"],
        "port": parsed["port"],
        "apiKey": sonarr_api_key,
        "useSsl": parsed["use_ssl"],
        "baseUrl": parsed["base_url"],
    }

    status, test_data, body = http_request(
        jellyseerr_url,
        "/api/v1/settings/sonarr/test",
        api_key=jellyseerr_key,
        method="POST",
        payload=test_payload,
    )
    if status != 200 or not isinstance(test_data, dict):
        raise RuntimeError(
            f"Jellyseerr: Sonarr connection test failed (HTTP {status}): {body}"
        )

    profiles = test_data.get("profiles") or []
    if not profiles:
        raise RuntimeError("Jellyseerr: Sonarr test returned no quality profiles.")
    selected_profile = choose_profile(
        profiles,
        preferred_id=sonarr_cfg.get("active_profile_id"),
        preferred_names=coerce_list(
            sonarr_cfg.get("quality_profile_preferred_names")
            or sonarr_cfg.get("preferred_profile_names")
            or []
        ),
    )
    if not selected_profile:
        raise RuntimeError("Jellyseerr: unable to choose Sonarr profile.")

    root_folders = test_data.get("rootFolders") or []
    preferred_root = sonarr_cfg.get("root_folder") or sonarr_app_cfg.get("root_folder")
    active_directory = choose_root_folder(root_folders, preferred_root)
    if not active_directory:
        raise RuntimeError("Jellyseerr: unable to choose Sonarr root folder.")

    language_profiles = test_data.get("languageProfiles") or []
    selected_language_profile = choose_profile(
        language_profiles, sonarr_cfg.get("active_language_profile_id")
    )
    active_language_profile_id = (
        to_int(selected_language_profile.get("id"))
        if selected_language_profile
        else to_int(sonarr_cfg.get("active_language_profile_id"))
    )

    active_anime_profile = choose_profile(profiles, sonarr_cfg.get("active_anime_profile_id"))
    active_anime_language_profile = choose_profile(
        language_profiles, sonarr_cfg.get("active_anime_language_profile_id")
    )

    resolved_base_url = normalize_base_path(test_data.get("urlBase") or parsed["base_url"])
    service_name = sonarr_cfg.get("name", sonarr_app_cfg.get("name", "Sonarr"))
    is4k = bool_cfg(sonarr_cfg, "is4k", False)

    series_type = str(sonarr_cfg.get("series_type", "standard")).strip().lower()
    if series_type not in ("standard", "daily", "anime"):
        series_type = "standard"

    anime_series_type = str(sonarr_cfg.get("anime_series_type", "anime")).strip().lower()
    if anime_series_type not in ("standard", "daily", "anime"):
        anime_series_type = "anime"

    monitor_new_items = str(sonarr_cfg.get("monitor_new_items", "all")).strip().lower()
    if monitor_new_items not in ("all", "none"):
        monitor_new_items = "all"

    payload = {
        "name": service_name,
        "hostname": parsed["hostname"],
        "port": parsed["port"],
        "apiKey": sonarr_api_key,
        "useSsl": parsed["use_ssl"],
        "baseUrl": resolved_base_url,
        "activeProfileId": to_int(selected_profile.get("id")),
        "activeProfileName": selected_profile.get("name"),
        "activeLanguageProfileId": active_language_profile_id,
        "activeDirectory": active_directory,
        "seriesType": series_type,
        "animeSeriesType": anime_series_type,
        "activeAnimeProfileId": (
            to_int(active_anime_profile.get("id"))
            if active_anime_profile
            else to_int(sonarr_cfg.get("active_anime_profile_id"))
        ),
        "activeAnimeProfileName": (
            active_anime_profile.get("name") if active_anime_profile else None
        ),
        "activeAnimeLanguageProfileId": (
            to_int(active_anime_language_profile.get("id"))
            if active_anime_language_profile
            else to_int(sonarr_cfg.get("active_anime_language_profile_id"))
        ),
        "activeAnimeDirectory": sonarr_cfg.get("active_anime_directory"),
        "is4k": is4k,
        "isDefault": bool_cfg(sonarr_cfg, "is_default", True),
        "enableSeasonFolders": bool_cfg(sonarr_cfg, "enable_season_folders", True),
        "externalUrl": sonarr_cfg.get("external_url", ""),
        "syncEnabled": bool_cfg(sonarr_cfg, "sync_enabled", True),
        "preventSearch": bool_cfg(sonarr_cfg, "prevent_search", False),
        "tagRequests": bool_cfg(sonarr_cfg, "tag_requests", False),
        "monitorNewItems": monitor_new_items,
        "tags": coerce_list(sonarr_cfg.get("tags")),
        "animeTags": coerce_list(sonarr_cfg.get("anime_tags")),
        "overrideRule": coerce_list(sonarr_cfg.get("override_rule")),
    }

    status, existing, body = http_request(
        jellyseerr_url, "/api/v1/settings/sonarr", api_key=jellyseerr_key
    )
    if status != 200 or not isinstance(existing, list):
        raise RuntimeError(
            f"Jellyseerr: failed to list Sonarr settings (HTTP {status}): {body}"
        )

    current = find_existing_servarr(
        existing,
        payload["name"],
        payload["hostname"],
        payload["port"],
        payload["baseUrl"],
        payload["is4k"],
    )

    if current:
        current_id = current.get("id")
        if current_id is None:
            # Older Jellyseerr settings payloads can contain legacy entries without ids.
            # If a matching entry already exists and our live connection test passed, keep it.
            log(
                "[OK] Jellyseerr: existing Sonarr mapping found "
                "(legacy entry without id)"
            )
            return
        status, _, body = http_request(
            jellyseerr_url,
            f"/api/v1/settings/sonarr/{current_id}",
            api_key=jellyseerr_key,
            method="PUT",
            payload=payload,
        )
        if status in (200, 201, 202):
            log("[OK] Jellyseerr: updated Sonarr service mapping")
            return
        raise RuntimeError(
            f"Jellyseerr: failed updating Sonarr mapping (HTTP {status}): {body}"
        )

    status, _, body = http_request(
        jellyseerr_url,
        "/api/v1/settings/sonarr",
        api_key=jellyseerr_key,
        method="POST",
        payload=payload,
    )
    if status in (200, 201, 202):
        log("[OK] Jellyseerr: created Sonarr service mapping")
        return

    raise RuntimeError(
        f"Jellyseerr: failed creating Sonarr mapping (HTTP {status}): {body}"
    )


def get_arr_quality_profile(
    app_name,
    app_url,
    api_base,
    api_key,
    preferred_id=None,
    preferred_names=None,
):
    status, profiles, body = http_request(
        app_url, f"{api_base}/qualityprofile", api_key=api_key
    )
    if status != 200 or not isinstance(profiles, list):
        raise RuntimeError(
            f"{app_name}: failed to list quality profiles (HTTP {status}): {body}"
        )
    selected = choose_profile(
        profiles,
        preferred_id=preferred_id,
        preferred_names=preferred_names,
    )
    if not selected:
        raise RuntimeError(f"{app_name}: no quality profiles returned by API.")
    return selected


def get_arr_root_folder_path(app_name, app_url, api_base, api_key, preferred_root):
    status, root_folders, body = http_request(
        app_url, f"{api_base}/rootfolder", api_key=api_key
    )
    if status != 200 or not isinstance(root_folders, list):
        raise RuntimeError(
            f"{app_name}: failed to list root folders (HTTP {status}): {body}"
        )
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


def jellyseerr_permission_error(exc):
    text = str(exc).lower()
    return (
        "(http 403)" in text
        or "permission to access this endpoint" in text
        or "you do not have permission" in text
    )


def configure_jellyseerr_via_settings_file(cfg, arr_apps, app_keys, config_root):
    jelly_cfg = cfg.get("jellyseerr") or {}
    settings_path = Path(config_root) / "jellyseerr" / "settings.json"
    settings = read_json_file(settings_path)

    main_cfg = settings.setdefault("main", {})
    if bool_cfg(jelly_cfg, "set_media_server_type_jellyfin", True):
        main_cfg["mediaServerType"] = 2
    settings.setdefault("public", {})["initialized"] = True

    jellyfin_cfg = jelly_cfg.get("jellyfin") or {}
    if bool_cfg(jellyfin_cfg, "configure", False):
        jellyfin_api_key = resolve_jellyfin_api_key(jellyfin_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyseerr file bootstrap: jellyfin.configure=true but Jellyfin API key could not be resolved."
            )
        parsed_jf = parse_service_url(jellyfin_cfg.get("url", "http://jellyfin:8096"), 8096)
        jf = settings.setdefault("jellyfin", {})
        jf["name"] = jellyfin_cfg.get("name", "Jellyfin")
        jf["ip"] = parsed_jf["hostname"]
        jf["port"] = parsed_jf["port"]
        jf["useSsl"] = parsed_jf["use_ssl"]
        jf["urlBase"] = parsed_jf["base_url"]
        jf["externalHostname"] = jellyfin_cfg.get("external_url", "")
        jf["jellyfinForgotPasswordUrl"] = jellyfin_cfg.get("forgot_password_url", "")
        jf["apiKey"] = jellyfin_api_key
        log("[OK] Jellyseerr: wrote Jellyfin settings via file bootstrap")

    radarr_app = get_arr_app(arr_apps, "Radarr")
    if radarr_app and "Radarr" in app_keys and bool_cfg((jelly_cfg.get("radarr") or {}), "enabled", True):
        radarr_cfg = jelly_cfg.get("radarr") or {}
        radarr_url = normalize_url(radarr_app["url"])
        radarr_api_base = detect_arr_api_base("Radarr", radarr_url, app_keys["Radarr"])
        radarr_profile_names = coerce_list(
            radarr_cfg.get("quality_profile_preferred_names")
            or radarr_app.get("quality_profile_preferred_names")
            or []
        )
        radarr_profile = get_arr_quality_profile(
            "Radarr",
            radarr_url,
            radarr_api_base,
            app_keys["Radarr"],
            preferred_id=radarr_cfg.get("active_profile_id"),
            preferred_names=radarr_profile_names,
        )
        radarr_root = get_arr_root_folder_path(
            "Radarr",
            radarr_url,
            radarr_api_base,
            app_keys["Radarr"],
            radarr_app.get("root_folder"),
        )
        parsed_radarr = parse_service_url(radarr_app["url"], 7878)
        settings["radarr"] = [
            {
                "name": radarr_cfg.get("name", "Radarr"),
                "hostname": parsed_radarr["hostname"],
                "port": parsed_radarr["port"],
                "apiKey": app_keys["Radarr"],
                "useSsl": parsed_radarr["use_ssl"],
                "baseUrl": parsed_radarr["base_url"],
                "activeProfileId": to_int(radarr_profile.get("id"), 1),
                "activeProfileName": str(radarr_profile.get("name") or "Default"),
                "activeDirectory": radarr_root,
                "is4k": bool(radarr_cfg.get("is4k", False)),
                "minimumAvailability": str(
                    radarr_cfg.get("minimum_availability", "released")
                ),
                "isDefault": bool(radarr_cfg.get("is_default", True)),
                "externalUrl": radarr_cfg.get("external_url", ""),
                "syncEnabled": bool(radarr_cfg.get("sync_enabled", True)),
                "preventSearch": bool(radarr_cfg.get("prevent_search", False)),
            }
        ]
        log("[OK] Jellyseerr: wrote Radarr settings via file bootstrap")

    sonarr_app = get_arr_app(arr_apps, "Sonarr")
    if sonarr_app and "Sonarr" in app_keys and bool_cfg((jelly_cfg.get("sonarr") or {}), "enabled", True):
        sonarr_cfg = jelly_cfg.get("sonarr") or {}
        sonarr_url = normalize_url(sonarr_app["url"])
        sonarr_api_base = detect_arr_api_base("Sonarr", sonarr_url, app_keys["Sonarr"])
        sonarr_profile_names = coerce_list(
            sonarr_cfg.get("quality_profile_preferred_names")
            or sonarr_app.get("quality_profile_preferred_names")
            or []
        )
        sonarr_profile = get_arr_quality_profile(
            "Sonarr",
            sonarr_url,
            sonarr_api_base,
            app_keys["Sonarr"],
            preferred_id=sonarr_cfg.get("active_profile_id"),
            preferred_names=sonarr_profile_names,
        )
        sonarr_root = get_arr_root_folder_path(
            "Sonarr",
            sonarr_url,
            sonarr_api_base,
            app_keys["Sonarr"],
            sonarr_app.get("root_folder"),
        )
        parsed_sonarr = parse_service_url(sonarr_app["url"], 8989)
        settings["sonarr"] = [
            {
                "name": sonarr_cfg.get("name", "Sonarr"),
                "hostname": parsed_sonarr["hostname"],
                "port": parsed_sonarr["port"],
                "apiKey": app_keys["Sonarr"],
                "useSsl": parsed_sonarr["use_ssl"],
                "baseUrl": parsed_sonarr["base_url"],
                "activeProfileId": to_int(sonarr_profile.get("id"), 1),
                "activeProfileName": str(sonarr_profile.get("name") or "Default"),
                "activeDirectory": sonarr_root,
                "activeLanguageProfileId": get_sonarr_language_profile_id(
                    sonarr_url, sonarr_api_base, app_keys["Sonarr"]
                ),
                "is4k": bool(sonarr_cfg.get("is4k", False)),
                "enableSeasonFolders": bool(
                    sonarr_cfg.get("enable_season_folders", True)
                ),
                "isDefault": bool(sonarr_cfg.get("is_default", True)),
                "externalUrl": sonarr_cfg.get("external_url", ""),
                "syncEnabled": bool(sonarr_cfg.get("sync_enabled", True)),
                "preventSearch": bool(sonarr_cfg.get("prevent_search", False)),
            }
        ]
        log("[OK] Jellyseerr: wrote Sonarr settings via file bootstrap")

    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log("[OK] Jellyseerr: settings file bootstrap applied")


def configure_jellyseerr(cfg, arr_apps, app_keys, config_root, wait_timeout):
    jelly_cfg = cfg.get("jellyseerr") or {}
    if not bool_cfg(jelly_cfg, "enabled", False):
        return

    jellyseerr_url = normalize_url(jelly_cfg.get("url", "http://jellyseerr:5055"))
    wait_for_service("Jellyseerr", jellyseerr_url, "/api/v1/status", wait_timeout)

    jellyseerr_key = read_jellyseerr_api_key(config_root, wait_timeout)
    radarr_app = get_arr_app(arr_apps, "Radarr")
    sonarr_app = get_arr_app(arr_apps, "Sonarr")
    enforced_file_bootstrap = False

    try:
        ensure_jellyseerr_main_settings(jellyseerr_url, jellyseerr_key, jelly_cfg)
        ensure_jellyseerr_jellyfin_settings(
            jellyseerr_url, jellyseerr_key, jelly_cfg, config_root
        )

        if radarr_app and "Radarr" in app_keys:
            ensure_jellyseerr_radarr(
                jellyseerr_url, jellyseerr_key, radarr_app, app_keys["Radarr"], jelly_cfg
            )
        else:
            log("[WARN] Jellyseerr: Radarr app config not found; skipping Radarr mapping.")

        if sonarr_app and "Sonarr" in app_keys:
            ensure_jellyseerr_sonarr(
                jellyseerr_url, jellyseerr_key, sonarr_app, app_keys["Sonarr"], jelly_cfg
            )
        else:
            log("[WARN] Jellyseerr: Sonarr app config not found; skipping Sonarr mapping.")
    except Exception as exc:
        if not jellyseerr_permission_error(exc):
            raise
        log(
            "[WARN] Jellyseerr API bootstrap hit permission gate; "
            "applying settings-file bootstrap fallback."
        )
        configure_jellyseerr_via_settings_file(cfg, arr_apps, app_keys, config_root)
        enforced_file_bootstrap = True

    if bool_cfg(jelly_cfg, "enforce_settings_file", True) and not enforced_file_bootstrap:
        configure_jellyseerr_via_settings_file(cfg, arr_apps, app_keys, config_root)


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
    arr_discovery_lists_cfg = cfg.get("arr_discovery_lists") or {}
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
    configure_arr_discovery_lists = bool_cfg(arr_discovery_lists_cfg, "enabled", False)
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
    configure_jellyfin_home_rails = bool_cfg(
        jellyfin_home_rails_cfg, "enabled", False
    ) or bool_cfg(jellyfin_home_rails_cfg, "cleanup_collections_when_disabled", False)
    jellyfin_home_rails_required = bool_cfg(jellyfin_home_rails_cfg, "required", False)
    configure_auto_collections = bool_cfg(
        jellyfin_auto_collections_cfg, "enabled", False
    )
    auto_collections_required = bool_cfg(
        jellyfin_auto_collections_cfg, "required", False
    )
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
        os.environ.get(str(sab_cfg.get("username_env", "SABNZBD_USERNAME")))
        or ""
    ).strip()
    sab_password = (
        os.environ.get(str(sab_cfg.get("password_env", "SABNZBD_PASSWORD")))
        or ""
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

    for app in arr_apps:
        impl = app["implementation"]
        app_url = normalize_url(app["url"])
        app_key = app_keys[impl]
        log(f"[STEP] Processing {app['name']} ({impl})")
        api_base = detect_arr_api_base(app["name"], app_url, app_key)
        try:
            ensure_app_auth_settings(
                app["name"],
                impl,
                app_url,
                api_base,
                app_key,
                app_auth_cfg,
            )
        except Exception as exc:
            if bool_cfg(app_auth_cfg, "fail_on_error", False):
                raise
            log(f"[WARN] {app['name']}: auth bootstrap skipped ({exc})")

        if str(impl).strip().lower() == "readarr":
            readarr_cfg = cfg.get("readarr") or {}
            try:
                ensure_readarr_metadata_source(
                    cfg,
                    app,
                    app_url,
                    api_base,
                    app_key,
                )
            except Exception as exc:
                if bool_cfg(readarr_cfg, "metadata_source_required", False):
                    raise
                log(
                    f"[WARN] Readarr metadata source: bootstrap skipped ({exc}). "
                    "Set readarr.metadata_source_required=true to fail the bootstrap instead."
                )

        if configure_arr_media_management:
            ensure_arr_media_management(
                app,
                app_url,
                api_base,
                app_key,
                arr_media_management_cfg,
            )

        ensure_root_folder(app["name"], app_url, api_base, app_key, app["root_folder"])
        if configure_arr_download_handling:
            ensure_arr_download_handling(
                app["name"],
                app_url,
                api_base,
                app_key,
                arr_download_handling_cfg,
            )
        if configure_arr_quality_upgrade:
            ensure_arr_quality_upgrade_policy(
                cfg,
                app,
                app_url,
                api_base,
                app_key,
                arr_quality_upgrade_cfg,
            )
        ensure_prowlarr_application(
            prowlarr_url,
            prowlarr_key,
            app["name"],
            impl,
            app_url,
            app_key,
        )
        if configure_qbit_arr_clients and qbit_login_ok:
            ensure_arr_download_client(
                app,
                app_url,
                api_base,
                app_key,
                qbit_cfg,
                {
                    "username": qb_user,
                    "password": qb_pass,
                },
            )
        if configure_sab_arr_clients and sab_api_key:
            ensure_arr_download_client(
                app,
                app_url,
                api_base,
                app_key,
                sab_cfg,
                {
                    "username": sab_username,
                    "password": sab_password,
                    "api_key": sab_api_key,
                },
            )
            ensure_arr_remote_path_mappings(
                app,
                app_url,
                api_base,
                app_key,
                sab_remote_path_mappings,
            )
        if configure_arr_discovery_lists:
            ensure_arr_discovery_lists_for_app(
                cfg,
                app,
                app_url,
                api_base,
                app_key,
            )
            trigger_arr_discovery_kickoff(
                cfg,
                app,
                app_url,
                api_base,
                app_key,
            )
        if refresh_health_after_bootstrap:
            trigger_health_check(app["name"], app_url, api_base, app_key)

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
            ensure_jellyfin_auto_collections_config(
                cfg, args.config_root, args.wait_timeout
            )
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
