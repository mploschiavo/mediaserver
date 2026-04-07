"""Bootstrap config mutators for platform/policy-specific runtime preparation."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib import parse

import yaml

_POLICY_CATALOG_PATH = (
    Path(__file__).resolve().parents[5] / "contracts" / "media-stack.bootstrap.policy.yaml"
)


def _tokenize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _slugify(value: str) -> str:
    """Lowercase slug preserving hyphens — used for URL path segments."""
    return re.sub(r"[^a-z0-9\-]+", "", str(value or "").strip().lower()).strip("-")


def parse_selected_apps_csv(value: str) -> set[str]:
    selected: set[str] = set()
    for raw in str(value or "").split(","):
        token = _tokenize(raw)
        if token:
            selected.add(token)
    return selected


def _set_enabled(section: dict[str, Any] | None, enabled: bool) -> None:
    if not isinstance(section, dict):
        return
    if "enabled" in section:
        section["enabled"] = bool(enabled)


def _walk_path(cfg: dict[str, object], path: str) -> dict[str, Any] | None:
    token = str(path or "").strip()
    if not token:
        return None
    current: Any = cfg
    for segment in token.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    if isinstance(current, dict):
        return current
    return None


def _set_bool_path(cfg: dict[str, object], path: str, value: bool) -> None:
    token = str(path or "").strip()
    if not token:
        return
    parent_path, _, leaf = token.rpartition(".")
    leaf_name = str(leaf or "").strip()
    if not leaf_name:
        return
    parent: Any = cfg if not parent_path else _walk_path(cfg, parent_path)
    if not isinstance(parent, dict):
        return
    parent[leaf_name] = bool(value)


@lru_cache(maxsize=1)
def _load_policy_catalog() -> dict[str, Any]:
    if not _POLICY_CATALOG_PATH.exists():
        raise RuntimeError(
            "Bootstrap runtime policy catalog file not found: " f"{_POLICY_CATALOG_PATH}"
        )
    payload = yaml.safe_load(_POLICY_CATALOG_PATH.read_text(encoding="utf-8"))
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise RuntimeError("Bootstrap runtime policy catalog must be an object")
    return payload


def _selected_apps_policy_cfg() -> dict[str, Any]:
    payload = _load_policy_catalog()
    policy = payload.get("selected_apps_policy")
    if not isinstance(policy, dict):
        raise RuntimeError(
            "selected_apps_policy must be an object in bootstrap runtime policy catalog"
        )
    return policy


def _policy_map(policy: dict[str, Any], key: str) -> dict[str, str]:
    raw = policy.get(key)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        token = _tokenize(str(raw_key or ""))
        section = str(raw_value or "").strip()
        if token and section:
            out[token] = section
    return out


def _policy_set(policy: dict[str, Any], key: str) -> set[str]:
    raw = policy.get(key)
    if not isinstance(raw, list):
        return set()
    out: set[str] = set()
    for item in raw:
        token = _tokenize(str(item or ""))
        if token:
            out.add(token)
    return out


def _policy_map_of_sets(policy: dict[str, Any], key: str) -> dict[str, set[str]]:
    raw = policy.get(key)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, set[str]] = {}
    for raw_key, raw_values in raw.items():
        token = _tokenize(str(raw_key or ""))
        if not token or not isinstance(raw_values, list):
            continue
        values: set[str] = set()
        for item in raw_values:
            item_token = _tokenize(str(item or ""))
            if item_token:
                values.add(item_token)
        if values:
            out[token] = values
    return out


def _policy_list(policy: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = policy.get(key)
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        token = str(item or "").strip()
        if token:
            out.append(token)
    return tuple(out)


def _section_enabled(cfg: dict[str, object], path: str) -> bool:
    section = _walk_path(cfg, path)
    if not isinstance(section, dict):
        return False
    if "enabled" not in section:
        return False
    return bool(section.get("enabled"))


def _path_prefix_url_base_tokens(cfg: dict[str, object], include_values: list[Any]) -> set[str]:
    tokens: set[str] = set()
    for item in include_values:
        token = _tokenize(str(item or ""))
        if token:
            tokens.add(token)

    policy = _selected_apps_policy_cfg()
    app_toggle_sections = _policy_map(policy, "app_toggle_sections")
    for app_token, section_path in app_toggle_sections.items():
        if _section_enabled(cfg, section_path):
            tokens.add(app_token)

    arr_allowed = _policy_set(policy, "arr_app_keys")
    arr_apps = cfg.get("arr_apps")
    if isinstance(arr_apps, list):
        for entry in arr_apps:
            if not isinstance(entry, dict):
                continue
            enabled = entry.get("enabled")
            if enabled is not None and not bool(enabled):
                continue
            token = _tokenize(str(entry.get("implementation") or entry.get("name") or ""))
            if token and token in arr_allowed:
                tokens.add(token)
    return tokens


def _normalize_prefix(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return "/app"
    if not token.startswith("/"):
        token = f"/{token}"
    token = token.rstrip("/")
    return token or "/app"


def _url_host(url: str) -> str:
    token = str(url or "").strip()
    if token.startswith("https://"):
        token = token[len("https://") :]
    elif token.startswith("http://"):
        token = token[len("http://") :]
    return token.rstrip("/")


def _normalize_port(value: object) -> str:
    token = str(value or "").strip()
    if token.startswith(":"):
        token = token[1:]
    if not token or not token.isdigit():
        return ""
    port = int(token)
    if port < 1 or port > 65535:
        return ""
    return str(port)


def _public_port(value: object, *, scheme: str) -> str:
    token = _normalize_port(value)
    if not token:
        return ""
    if scheme == "http" and token == "80":
        return ""
    if scheme == "https" and token == "443":
        return ""
    return token


def _host_name(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    parsed = parse.urlparse(text if "://" in text else f"http://{text}")
    return str(parsed.hostname or "").strip().lower()


def _host_with_port(value: str, *, port: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not port:
        return text
    parsed = parse.urlparse(text if "://" in text else f"http://{text}")
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return text
    selected_port = str(parsed.port) if parsed.port else port
    path = str(parsed.path or "")
    query = str(parsed.query or "")
    fragment = str(parsed.fragment or "")
    out = f"{host}:{selected_port}{path}"
    if query:
        out = f"{out}?{query}"
    if fragment:
        out = f"{out}#{fragment}"
    return out


def _homepage_host_token(value: str) -> str:
    """Extract a URL-safe slug from a homepage host entry.

    Preserves hyphens so the token can be used directly as a URL path
    segment that matches Envoy/K8s service names (e.g. media-stack-controller).
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    parsed = parse.urlparse(text if "://" in text else f"http://{text}")
    path = str(parsed.path or "").strip("/")
    if path:
        parts = [part for part in path.split("/") if part]
        if parts:
            if len(parts) >= 2 and parts[0] == "app":
                return _slugify(parts[1])
            return _slugify(parts[-1])
    host = str(parsed.netloc or "").split(":", 1)[0]
    prefix = host.split(".", 1)[0]
    return _slugify(prefix)


def _homepage_direct_host(value: str, *, internet_exposed: bool, ingress: str, token: str) -> str:
    text = str(value or "").strip().lower()
    parsed = parse.urlparse(text if "://" in text else f"http://{text}")
    host = str(parsed.netloc or "").split(":", 1)[0].strip().lower()
    if not host:
        host = str(parsed.path or "").strip().split("/", 1)[0].strip().lower()
    if not host:
        if internet_exposed and ingress:
            return f"{token}.{ingress}"
        return f"{token}.local"
    if internet_exposed and ingress and host.endswith(".local"):
        return f"{host[:-6]}.{ingress}"
    return host


def _path_prefix_url_base(token: str, prefix: str) -> str:
    app_token = _tokenize(token)
    if not app_token:
        return ""
    normalized_prefix = _normalize_prefix(prefix)
    return f"{normalized_prefix}/{app_token}"


def apply_selected_apps_policy(cfg: dict[str, object], *, selected_apps_csv: str) -> None:
    policy = _selected_apps_policy_cfg()
    app_toggle_sections = _policy_map(policy, "app_toggle_sections")
    arr_app_keys = _policy_set(policy, "arr_app_keys")
    selected_app_expansions = _policy_map_of_sets(policy, "selected_app_expansions")
    homepage_host_reserved_tokens = _policy_set(policy, "homepage_host_reserved_tokens")
    arr_disable_sections = _policy_list(policy, "arr_disable_sections_when_unselected")
    arr_discovery_reserved_keys = _policy_set(policy, "arr_discovery_reserved_keys")
    jellyfin_disable_sections = _policy_list(policy, "jellyfin_disable_sections_when_unselected")
    maintainerr_integrations_section = str(
        policy.get("maintainerr_integrations_section") or ""
    ).strip()
    jellyfin_home_rails_cleanup_path = str(
        policy.get("jellyfin_home_rails_cleanup_path") or ""
    ).strip()

    selected = parse_selected_apps_csv(selected_apps_csv)
    if not selected:
        return
    if selected_app_expansions:
        pending = list(selected)
        while pending:
            token = pending.pop()
            for expanded in selected_app_expansions.get(token, set()):
                if expanded in selected:
                    continue
                selected.add(expanded)
                pending.append(expanded)
    selected_arr = bool(arr_app_keys.intersection(selected))

    homepage_cfg = cfg.get("homepage")
    if isinstance(homepage_cfg, dict):
        hosts = homepage_cfg.get("hosts")
        if isinstance(hosts, list):
            filtered_hosts: list[str] = []
            for raw_host in hosts:
                host_text = str(raw_host or "").strip()
                if not host_text:
                    continue
                token = _homepage_host_token(host_text)
                if token and token not in selected and token not in homepage_host_reserved_tokens:
                    continue
                filtered_hosts.append(host_text)
            homepage_cfg["hosts"] = filtered_hosts

    for app_key, section_key in app_toggle_sections.items():
        _set_enabled(cfg.get(section_key), app_key in selected)

    arr_apps = cfg.get("arr_apps")
    if isinstance(arr_apps, list):
        filtered = []
        for item in arr_apps:
            if not isinstance(item, dict):
                continue
            app_key = _tokenize(str(item.get("implementation") or item.get("name") or ""))
            if app_key in selected:
                filtered.append(item)
        cfg["arr_apps"] = filtered

    if not selected_arr:
        for section in arr_disable_sections:
            _set_enabled(cfg.get(section), False)

    arr_discovery_lists = cfg.get("arr_discovery_lists")
    if isinstance(arr_discovery_lists, dict):
        for key in list(arr_discovery_lists.keys()):
            if _tokenize(key) in arr_discovery_reserved_keys:
                continue
            token = _tokenize(key)
            if token and token not in selected:
                arr_discovery_lists.pop(key, None)

    if "sonarr" not in selected:
        sonarr_seed = cfg.get("sonarr_seed_series")
        if isinstance(sonarr_seed, dict):
            sonarr_seed["enabled"] = False
            sonarr_seed["search_for_missing_episodes"] = False

    jellyseerr = cfg.get("jellyseerr")
    if isinstance(jellyseerr, dict):
        if isinstance(jellyseerr.get("radarr"), dict):
            jellyseerr["radarr"]["enabled"] = "radarr" in selected
        if isinstance(jellyseerr.get("sonarr"), dict):
            jellyseerr["sonarr"]["enabled"] = "sonarr" in selected
        if "jellyseerr" not in selected:
            jellyseerr["enabled"] = False

    if "prowlarr" not in selected:
        cfg["prowlarr_url"] = ""
        cfg["prowlarr_indexers"] = []
        cfg["trigger_indexer_sync"] = False
        cfg["prowlarr_auto_add_tested_indexers"] = False

    if "flaresolverr" not in selected:
        _set_enabled(cfg.get("flaresolverr"), False)

    download_clients = cfg.get("download_clients")
    if isinstance(download_clients, dict):
        if "qbittorrent" not in selected:
            qbit_cfg = download_clients.get("qbittorrent")
            if isinstance(qbit_cfg, dict):
                qbit_cfg["configure_arr_clients"] = False
                qbit_cfg["login_required"] = False
        if "sabnzbd" not in selected:
            sab_cfg = download_clients.get("sabnzbd")
            if isinstance(sab_cfg, dict):
                sab_cfg["configure_arr_clients"] = False
                sab_cfg["login_required"] = False

    technology_bindings = cfg.get("technology_bindings")
    if isinstance(technology_bindings, dict):
        if "qbittorrent" not in selected:
            technology_bindings["torrent_client"] = ""
        if "sabnzbd" not in selected:
            technology_bindings["usenet_client"] = ""

    if "jellyfin" not in selected:
        for section in jellyfin_disable_sections:
            _set_enabled(cfg.get(section), False)
        _set_bool_path(cfg, jellyfin_home_rails_cleanup_path, False)
        if isinstance(jellyseerr, dict):
            jelly_cfg = jellyseerr.get("jellyfin")
            if isinstance(jelly_cfg, dict):
                jelly_cfg["configure"] = False

    if "maintainerr" not in selected:
        _set_enabled(_walk_path(cfg, maintainerr_integrations_section), False)

    app_auth = cfg.get("app_auth")
    if isinstance(app_auth, dict):
        include = app_auth.get("include")
        if isinstance(include, list):
            filtered = []
            for item in include:
                token = _tokenize(str(item))
                if token in selected:
                    filtered.append(item)
            app_auth["include"] = filtered


def apply_api_key_policy(cfg: dict[str, object], *, preconfigure_api_keys: bool) -> None:
    """Disable app auth setup when API key provisioning is opted out."""
    if not preconfigure_api_keys:
        app_auth = cfg.get("app_auth")
        if isinstance(app_auth, dict):
            app_auth["enabled"] = False


def apply_content_download_policy(cfg: dict[str, object], *, auto_download_content: bool) -> None:
    download_enabled = bool(auto_download_content)
    cfg["prowlarr_auto_add_tested_indexers"] = download_enabled

    arr_discovery_lists = cfg.get("arr_discovery_lists")
    if isinstance(arr_discovery_lists, dict):
        arr_discovery_lists["trigger_initial_sync"] = download_enabled
        for value in arr_discovery_lists.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                for key in (
                    "enable_auto",
                    "enable_automatic_add",
                    "search_on_add",
                    "should_search",
                ):
                    if key in item:
                        item[key] = download_enabled

    sonarr_seed_series = cfg.get("sonarr_seed_series")
    if isinstance(sonarr_seed_series, dict):
        sonarr_seed_series["enabled"] = download_enabled
        sonarr_seed_series["search_for_missing_episodes"] = download_enabled

    for request_manager_key in ("jellyseerr", "openseerr"):
        request_manager_cfg = cfg.get(request_manager_key)
        if not isinstance(request_manager_cfg, dict):
            continue
        for app_key in ("radarr", "sonarr"):
            app_cfg = request_manager_cfg.get(app_key)
            if isinstance(app_cfg, dict):
                app_cfg["prevent_search"] = not download_enabled


def apply_edge_url_policy(
    cfg: dict[str, object],
    *,
    internet_exposed: bool,
    route_strategy: str,
    ingress_domain: str,
    app_gateway_host: str,
    app_gateway_port: str = "",
    app_path_prefix: str,
    media_server_direct_host: str,
) -> None:
    scheme = "https" if bool(internet_exposed) else "http"
    strategy = str(route_strategy or "").strip().lower()
    gateway_host = str(app_gateway_host or "").strip().lower()
    direct_host = str(media_server_direct_host or "").strip().lower()
    public_port = _public_port(app_gateway_port, scheme=scheme)
    gateway_host_with_port = _host_with_port(gateway_host, port=public_port)
    direct_host_with_port = _host_with_port(direct_host, port=public_port)
    ingress = str(ingress_domain or "").strip().lower()
    prefix = _normalize_prefix(app_path_prefix)

    def _public_url(app_key: str) -> str:
        token = _tokenize(app_key)
        if strategy == "hybrid" and token == "jellyfin" and direct_host_with_port:
            return f"{scheme}://{direct_host_with_port}"
        if strategy in {"path-prefix", "hybrid"} and gateway_host_with_port:
            return f"{scheme}://{gateway_host_with_port}{prefix}/{token}"
        if not ingress:
            return ""
        return f"{scheme}://{token}.{ingress}"

    jellyseerr_cfg = cfg.get("jellyseerr")
    if isinstance(jellyseerr_cfg, dict):
        jellyfin_cfg = jellyseerr_cfg.get("jellyfin")
        if isinstance(jellyfin_cfg, dict):
            jellyfin_public = _public_url("jellyfin")
            if jellyfin_public:
                jellyfin_cfg["external_url"] = jellyfin_public

    app_auth_cfg = cfg.get("app_auth")
    if isinstance(app_auth_cfg, dict):
        include = app_auth_cfg.get("include")
        include_values = include if isinstance(include, list) else []
        path_prefix_url_bases: dict[str, str] = {}
        if strategy in {"path-prefix", "hybrid"} and gateway_host_with_port:
            for token in sorted(_path_prefix_url_base_tokens(cfg, include_values)):
                path_prefix_url_bases[token] = _path_prefix_url_base(token, prefix)
        app_auth_cfg["path_prefix_url_base_by_app"] = path_prefix_url_bases

    homepage_cfg = cfg.get("homepage")
    if not isinstance(homepage_cfg, dict):
        return

    device_cfg = homepage_cfg.get("device_onboarding")
    if isinstance(device_cfg, dict):
        jellyfin_public = _public_url("jellyfin")
        jellyseerr_public = _public_url("jellyseerr")
        if jellyfin_public:
            device_cfg["jellyfin_url"] = jellyfin_public
            device_cfg["jellyfin_short_link"] = _url_host(jellyfin_public)
        if jellyseerr_public:
            device_cfg["jellyseerr_url"] = jellyseerr_public
            device_cfg["jellyseerr_short_link"] = _url_host(jellyseerr_public)

    hosts = homepage_cfg.get("hosts")
    if not isinstance(hosts, list):
        return

    # Determine the active edge router so the inactive one is excluded from tiles.
    active_edge_provider = _tokenize(
        str((cfg.get("adapter_hooks") or {}).get("edge", {}).get("router_provider", ""))
    )
    edge_router_tokens = {"traefik", "envoy"}

    rewritten_hosts: list[str] = []
    if strategy == "path-prefix" and gateway_host_with_port:
        for raw_host in hosts:
            token = _homepage_host_token(str(raw_host or ""))
            if not token:
                continue
            if token in edge_router_tokens and token != active_edge_provider:
                continue
            rewritten_hosts.append(f"{gateway_host_with_port}{prefix}/{token}")
    elif strategy == "hybrid" and gateway_host_with_port:
        for raw_host in hosts:
            host = str(raw_host or "").strip().lower()
            if not host:
                continue
            token = _homepage_host_token(host)
            if not token:
                continue
            if token in edge_router_tokens and token != active_edge_provider:
                continue
            if token == "jellyfin":
                if direct_host_with_port:
                    rewritten_hosts.append(direct_host_with_port)
                continue
            if token == "homepage":
                homepage_direct_host = _homepage_direct_host(
                    host,
                    internet_exposed=bool(internet_exposed),
                    ingress=ingress,
                    token=token,
                )
                rewritten_hosts.append(_host_with_port(homepage_direct_host, port=public_port))
                continue
            rewritten_hosts.append(f"{gateway_host_with_port}{prefix}/{token}")
    else:
        for raw_host in hosts:
            host = str(raw_host or "").strip().lower()
            if not host:
                continue
            token = _homepage_host_token(host)
            if token in edge_router_tokens and token != active_edge_provider:
                continue
            if ingress and host.endswith(".local"):
                host = f"{host[:-6]}.{ingress}"
            rewritten_hosts.append(_host_with_port(host, port=public_port))
        if direct_host_with_port:
            rewritten_hosts.append(direct_host_with_port)

    deduped: list[str] = []
    seen: set[str] = set()
    for host in rewritten_hosts:
        token = str(host or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    homepage_cfg["hosts"] = deduped


def apply_bootstrap_runtime_policy(
    cfg: dict[str, object],
    *,
    selected_apps_csv: str = "",
    preconfigure_api_keys: bool = True,
    auto_download_content: bool = False,
    internet_exposed: bool = False,
    route_strategy: str = "subdomain",
    ingress_domain: str = "local",
    app_gateway_host: str = "",
    app_gateway_port: str = "",
    app_path_prefix: str = "/app",
    media_server_direct_host: str = "",
) -> None:
    apply_selected_apps_policy(
        cfg,
        selected_apps_csv=selected_apps_csv,
    )
    apply_api_key_policy(
        cfg,
        preconfigure_api_keys=preconfigure_api_keys,
    )
    apply_content_download_policy(
        cfg,
        auto_download_content=auto_download_content,
    )
    apply_edge_url_policy(
        cfg,
        internet_exposed=internet_exposed,
        route_strategy=route_strategy,
        ingress_domain=ingress_domain,
        app_gateway_host=app_gateway_host,
        app_gateway_port=app_gateway_port,
        app_path_prefix=app_path_prefix,
        media_server_direct_host=media_server_direct_host,
    )
