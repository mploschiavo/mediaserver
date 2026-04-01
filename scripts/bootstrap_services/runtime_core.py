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
from bootstrap_services.bootstrap_runner_service import (
    BootstrapRunnerDependencies,
    BootstrapRunnerService,
)
from bootstrap_services.config_artifacts_service import ConfigArtifactsService
from bootstrap_services.discovery_lists_service import DiscoveryListsService
from bootstrap_services.disk_guardrails_service import DiskGuardrailsService
from bootstrap_services.enums import BootstrapMode
from bootstrap_services.health_service import HealthService
from bootstrap_services.maintainerr_service import MaintainerrService
from bootstrap_services.media_hygiene_ops_service import MediaHygieneOpsService
from bootstrap_services.media_hygiene_service import MediaHygieneService
from bootstrap_services.operation_wiring import build_runner_operation_registry
from bootstrap_services.runtime_factory import (
    BootstrapCliArgs,
    BootstrapRuntimeFactoryDependencies,
    BootstrapRuntimeFactoryService,
)
from bootstrap_services.runtime_helpers import (
    disk_usage_percent as _disk_usage_percent,
)
from bootstrap_services.runtime_helpers import (
    fmt_bytes as _fmt_bytes,
)
from bootstrap_services.runtime_helpers import (
    qbit_delete_torrents,
    qbit_list_completed_torrents,
    qbit_list_torrents,
)
from bootstrap_services.runtime_helpers import (
    to_float as _to_float,
)
from bootstrap_services.runtime_service_registry import (
    resolve_app_service_class,
    set_runtime_context_cfg,
)
from bootstrap_services.servarr_adapters import AdapterDependencies


def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] {msg}", flush=True)


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


def resolve_jellyfin_api_key(jellyfin_cfg, config_root):
    return _api_keys_service().resolve_jellyfin_api_key(jellyfin_cfg, config_root)


def resolve_path(base_root, maybe_relative):
    p = Path(str(maybe_relative))
    if p.is_absolute():
        return p
    return Path(base_root) / p


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


BOOTSTRAP_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "bootstrap_defaults"


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
