"""Bootstrap config mutators for platform/policy-specific runtime preparation."""

from __future__ import annotations

import re
from typing import Any

_ARR_APP_KEYS = {"sonarr", "radarr", "lidarr", "readarr"}
_RESERVED_DISCOVERY_KEYS = {"enabled", "required", "trigger_initial_sync", "prune_unmanaged"}


def _tokenize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


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


def apply_selected_apps_policy(cfg: dict[str, object], *, selected_apps_csv: str) -> None:
    selected = parse_selected_apps_csv(selected_apps_csv)
    if not selected:
        return
    selected_arr = bool(_ARR_APP_KEYS.intersection(selected))

    for app_key, section_key in (
        ("homepage", "homepage"),
        ("jellyseerr", "jellyseerr"),
        ("bazarr", "bazarr"),
        ("maintainerr", "maintainerr"),
        ("tautulli", "tautulli"),
    ):
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
        for section in (
            "arr_media_management",
            "arr_download_handling",
            "arr_quality_upgrade",
            "arr_discovery_lists",
            "disk_guardrails",
            "media_hygiene",
        ):
            _set_enabled(cfg.get(section), False)

    arr_discovery_lists = cfg.get("arr_discovery_lists")
    if isinstance(arr_discovery_lists, dict):
        for key in list(arr_discovery_lists.keys()):
            if key in _RESERVED_DISCOVERY_KEYS:
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
        for section in (
            "jellyfin_libraries",
            "jellyfin_livetv",
            "jellyfin_plugins",
            "jellyfin_playback",
            "jellyfin_home_rails",
            "jellyfin_auto_collections",
            "jellyfin_prewarm",
        ):
            _set_enabled(cfg.get(section), False)
        jellyfin_home_rails = cfg.get("jellyfin_home_rails")
        if isinstance(jellyfin_home_rails, dict):
            jellyfin_home_rails["cleanup_collections_when_disabled"] = False
        if isinstance(jellyseerr, dict):
            jelly_cfg = jellyseerr.get("jellyfin")
            if isinstance(jelly_cfg, dict):
                jelly_cfg["configure"] = False

    if "maintainerr" not in selected:
        maintainerr_cfg = cfg.get("maintainerr")
        if isinstance(maintainerr_cfg, dict):
            _set_enabled(maintainerr_cfg.get("integrations"), False)

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
    app_path_prefix: str,
    media_server_direct_host: str,
) -> None:
    if not internet_exposed:
        return

    strategy = str(route_strategy or "").strip().lower()
    gateway_host = str(app_gateway_host or "").strip().lower()
    direct_host = str(media_server_direct_host or "").strip().lower()
    ingress = str(ingress_domain or "").strip().lower()
    prefix = _normalize_prefix(app_path_prefix)

    def _public_url(app_key: str) -> str:
        token = _tokenize(app_key)
        if token == "jellyfin" and direct_host:
            return f"https://{direct_host}"
        if token != "jellyfin" and strategy in {"path-prefix", "hybrid"} and gateway_host:
            return f"https://{gateway_host}{prefix}/{token}"
        if not ingress:
            return ""
        return f"https://{token}.{ingress}"

    jellyseerr_cfg = cfg.get("jellyseerr")
    if isinstance(jellyseerr_cfg, dict):
        jellyfin_cfg = jellyseerr_cfg.get("jellyfin")
        if isinstance(jellyfin_cfg, dict):
            jellyfin_public = _public_url("jellyfin")
            if jellyfin_public:
                jellyfin_cfg["external_url"] = jellyfin_public

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

    rewritten_hosts: list[str] = []
    if strategy in {"path-prefix", "hybrid"} and gateway_host:
        rewritten_hosts.append(gateway_host)
        if direct_host:
            rewritten_hosts.append(direct_host)
    else:
        for raw_host in hosts:
            host = str(raw_host or "").strip().lower()
            if not host:
                continue
            if ingress and host.endswith(".local"):
                host = f"{host[:-6]}.{ingress}"
            rewritten_hosts.append(host)
        if direct_host:
            rewritten_hosts.append(direct_host)

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
    auto_download_content: bool = False,
    internet_exposed: bool = False,
    route_strategy: str = "subdomain",
    ingress_domain: str = "local",
    app_gateway_host: str = "",
    app_path_prefix: str = "/app",
    media_server_direct_host: str = "",
) -> None:
    apply_selected_apps_policy(
        cfg,
        selected_apps_csv=selected_apps_csv,
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
        app_path_prefix=app_path_prefix,
        media_server_direct_host=media_server_direct_host,
    )
