"""Bootstrap config mutators for platform/policy-specific runtime preparation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .controller_config_policy_helpers import (
    _apply_discovery_auto_flags,
    _homepage_direct_host,
    _homepage_host_token,
    _host_name,
    _host_with_port,
    _normalize_port,
    _normalize_prefix,
    _path_prefix_url_base,
    _public_port,
    _set_bool_path,
    _set_enabled,
    _slugify,
    _tokenize,
    _url_host,
    _walk_path,
)

import sys as _sys

# Resolve media-stack.bootstrap.policy.yaml across deploy modes —
# same install-path bug class as v1.0.231 / v1.0.235; see
# test_install_path_resolvers_ratchet. The pre-existing
# _IMAGE_POLICY_PATH was a partial mitigation referenced by specific
# call sites; the candidate-list form supersedes it for default
# resolution.
_POLICY_CATALOG_PATH_CANDIDATES = (
    Path(__file__).resolve().parents[5] / "contracts" / "media-stack.bootstrap.policy.yaml",
    Path("/opt/media-stack/contracts/media-stack.bootstrap.policy.yaml"),
    Path(_sys.prefix) / "share" / "media-stack" / "contracts" / "media-stack.bootstrap.policy.yaml",
    Path("/contracts/media-stack.bootstrap.policy.yaml"),
)


def _resolve_policy_catalog() -> Path:
    for p in _POLICY_CATALOG_PATH_CANDIDATES:
        if p.is_file():
            return p
    return _POLICY_CATALOG_PATH_CANDIDATES[0]


_POLICY_CATALOG_PATH = _resolve_policy_catalog()
_IMAGE_POLICY_PATH = Path("/opt/media-stack/contracts/media-stack.bootstrap.policy.yaml")


class StackControllerConfigPolicy:

    def parse_selected_apps_csv(self, value: str) -> set[str]:
        selected: set[str] = set()
        for raw in str(value or "").split(","):
            token = _tokenize(raw)
            if token:
                selected.add(token)
        return selected

    @staticmethod
    @lru_cache(maxsize=4)
    def _load_policy_catalog() -> dict[str, Any]:
        for path in [_POLICY_CATALOG_PATH, _IMAGE_POLICY_PATH]:
            if path.is_file():
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
        # Generate a minimal policy from the service registry
        try:
            from media_stack.api.services.registry import SERVICES
            arr_keys = [s.id for s in SERVICES if s.category == "automation" and s.api_key_format == "xml"]
            toggle_sections = {s.id: s.id for s in SERVICES if s.category in ("management", "media") and s.id not in ("envoy", "homepage")}
            return {
                "selected_apps_policy": {
                    "app_toggle_sections": toggle_sections,
                    "arr_app_keys": arr_keys,
                    "selected_app_expansions": {},
                    "arr_disable_sections_when_unselected": ["arr_media_management", "arr_download_handling", "arr_quality_upgrade", "arr_discovery_lists", "disk_guardrails", "media_hygiene"],
                    "arr_discovery_reserved_keys": ["enabled", "required", "trigger_initial_sync", "prune_unmanaged"],
                }
            }
        except Exception:
            return {"selected_apps_policy": {"app_toggle_sections": {}, "arr_app_keys": []}}

    @staticmethod
    def _selected_apps_policy_cfg() -> dict[str, Any]:
        payload = _load_policy_catalog()
        policy = payload.get("selected_apps_policy")
        if not isinstance(policy, dict):
            raise RuntimeError(
                "selected_apps_policy must be an object in bootstrap runtime policy catalog"
            )
        return policy

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _section_enabled(cfg: dict[str, object], path: str) -> bool:
        section = _walk_path(cfg, path)
        if not isinstance(section, dict):
            return False
        if "enabled" not in section:
            return False
        return bool(section.get("enabled"))

    @staticmethod
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

    def apply_selected_apps_policy(self, cfg: dict[str, object], *, selected_apps_csv: str) -> None:
        policy = _selected_apps_policy_cfg()
        selected = parse_selected_apps_csv(selected_apps_csv)
        if not selected:
            return

        selected_app_expansions = _policy_map_of_sets(policy, "selected_app_expansions")
        self._expand_selected_apps(selected, selected_app_expansions)
        arr_app_keys = _policy_set(policy, "arr_app_keys")
        selected_arr = bool(arr_app_keys.intersection(selected))

        self._filter_homepage_hosts_by_selection(
            cfg, selected, _policy_set(policy, "homepage_host_reserved_tokens"),
        )
        for app_key, section_key in _policy_map(policy, "app_toggle_sections").items():
            _set_enabled(cfg.get(section_key), app_key in selected)
        self._filter_arr_apps_and_discovery(
            cfg, selected, selected_arr, policy,
        )
        self._apply_unselected_arr_side_effects(cfg, selected)
        self._apply_unselected_download_clients(cfg, selected)
        self._apply_jellyfin_disable_when_unselected(cfg, selected, policy)
        if "maintainerr" not in selected:
            maintainerr_integrations_section = str(
                policy.get("maintainerr_integrations_section") or ""
            ).strip()
            _set_enabled(_walk_path(cfg, maintainerr_integrations_section), False)
        self._filter_app_auth_include_by_selection(cfg, selected)

    @staticmethod
    def _expand_selected_apps(
        selected: set[str], selected_app_expansions: dict[str, set[str]],
    ) -> None:
        """Transitively expand selection via the policy's expansion map."""
        if not selected_app_expansions:
            return
        pending = list(selected)
        while pending:
            token = pending.pop()
            for expanded in selected_app_expansions.get(token, set()):
                if expanded in selected:
                    continue
                selected.add(expanded)
                pending.append(expanded)

    @staticmethod
    def _filter_homepage_hosts_by_selection(
        cfg: dict[str, object], selected: set[str], reserved: set[str],
    ) -> None:
        """Drop Homepage tiles whose host token isn't selected or reserved."""
        homepage_cfg = cfg.get("homepage")
        if not isinstance(homepage_cfg, dict):
            return
        hosts = homepage_cfg.get("hosts")
        if not isinstance(hosts, list):
            return
        filtered_hosts: list[str] = []
        for raw_host in hosts:
            host_text = str(raw_host or "").strip()
            if not host_text:
                continue
            token = _homepage_host_token(host_text)
            if token and token not in selected and token not in reserved:
                continue
            filtered_hosts.append(host_text)
        homepage_cfg["hosts"] = filtered_hosts

    @staticmethod
    def _filter_arr_apps_and_discovery(
        cfg: dict[str, object],
        selected: set[str],
        selected_arr: bool,
        policy: dict[str, Any],
    ) -> None:
        """Filter arr_apps list, disable related sections, prune discovery lists."""
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
            for section in _policy_list(policy, "arr_disable_sections_when_unselected"):
                _set_enabled(cfg.get(section), False)

        arr_discovery_lists = cfg.get("arr_discovery_lists")
        if isinstance(arr_discovery_lists, dict):
            arr_discovery_reserved_keys = _policy_set(policy, "arr_discovery_reserved_keys")
            for key in list(arr_discovery_lists.keys()):
                if _tokenize(key) in arr_discovery_reserved_keys:
                    continue
                token = _tokenize(key)
                if token and token not in selected:
                    arr_discovery_lists.pop(key, None)

    @staticmethod
    def _apply_unselected_arr_side_effects(
        cfg: dict[str, object], selected: set[str],
    ) -> None:
        """Turn off Sonarr seeding + Jellyseerr integrations + Prowlarr state."""
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

    @staticmethod
    def _apply_unselected_download_clients(
        cfg: dict[str, object], selected: set[str],
    ) -> None:
        """Defang qBittorrent / SABnzbd config when not selected."""
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

    @staticmethod
    def _apply_jellyfin_disable_when_unselected(
        cfg: dict[str, object], selected: set[str], policy: dict[str, Any],
    ) -> None:
        """Disable jellyfin-adjacent sections and cleanup path when unselected."""
        if "jellyfin" in selected:
            return
        for section in _policy_list(policy, "jellyfin_disable_sections_when_unselected"):
            _set_enabled(cfg.get(section), False)
        cleanup_path = str(policy.get("jellyfin_home_rails_cleanup_path") or "").strip()
        _set_bool_path(cfg, cleanup_path, False)
        jellyseerr = cfg.get("jellyseerr")
        if isinstance(jellyseerr, dict):
            jelly_cfg = jellyseerr.get("jellyfin")
            if isinstance(jelly_cfg, dict):
                jelly_cfg["configure"] = False

    @staticmethod
    def _filter_app_auth_include_by_selection(
        cfg: dict[str, object], selected: set[str],
    ) -> None:
        """Trim ``app_auth.include`` to apps that survived the selection filter."""
        app_auth = cfg.get("app_auth")
        if not isinstance(app_auth, dict):
            return
        include = app_auth.get("include")
        if not isinstance(include, list):
            return
        filtered = []
        for item in include:
            token = _tokenize(str(item))
            if token in selected:
                filtered.append(item)
        app_auth["include"] = filtered

    def apply_api_key_policy(self, cfg: dict[str, object], *, preconfigure_api_keys: bool) -> None:
        """Disable app auth setup when API key provisioning is opted out."""
        if not preconfigure_api_keys:
            app_auth = cfg.get("app_auth")
            if isinstance(app_auth, dict):
                app_auth["enabled"] = False

    def apply_content_download_policy(self, cfg: dict[str, object], *, auto_download_content: bool) -> None:
        download_enabled = bool(auto_download_content)
        cfg["prowlarr_auto_add_tested_indexers"] = download_enabled

        arr_discovery_lists = cfg.get("arr_discovery_lists")
        if isinstance(arr_discovery_lists, dict):
            arr_discovery_lists["trigger_initial_sync"] = download_enabled
            _apply_discovery_auto_flags(arr_discovery_lists, download_enabled)

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

    def apply_edge_url_policy(self,
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
        ctx = self._edge_url_context(
            internet_exposed=internet_exposed,
            route_strategy=route_strategy,
            ingress_domain=ingress_domain,
            app_gateway_host=app_gateway_host,
            app_gateway_port=app_gateway_port,
            app_path_prefix=app_path_prefix,
            media_server_direct_host=media_server_direct_host,
        )
        self._apply_jellyseerr_external_url(cfg, ctx)
        self._apply_app_auth_path_prefix_bases(cfg, ctx)

        homepage_cfg = cfg.get("homepage")
        if not isinstance(homepage_cfg, dict):
            return
        self._apply_device_onboarding_links(homepage_cfg, ctx)
        self._rewrite_homepage_hosts(cfg, homepage_cfg, ctx)

    @staticmethod
    def _edge_url_context(
        *,
        internet_exposed: bool,
        route_strategy: str,
        ingress_domain: str,
        app_gateway_host: str,
        app_gateway_port: str,
        app_path_prefix: str,
        media_server_direct_host: str,
    ) -> dict[str, Any]:
        """Pre-compute scheme/ports/prefix so each helper takes one ctx arg.

        Packs ``public_url`` as a callable into the context so downstream
        helpers can resolve any app's public URL without re-deriving the
        strategy every time.
        """
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

        return {
            "scheme": scheme,
            "strategy": strategy,
            "gateway_host_with_port": gateway_host_with_port,
            "direct_host_with_port": direct_host_with_port,
            "public_port": public_port,
            "ingress": ingress,
            "prefix": prefix,
            "internet_exposed": bool(internet_exposed),
            "public_url": _public_url,
        }

    @staticmethod
    def _apply_jellyseerr_external_url(cfg: dict[str, object], ctx: dict[str, Any]) -> None:
        """Pin Jellyseerr's external Jellyfin URL to the derived public URL."""
        jellyseerr_cfg = cfg.get("jellyseerr")
        if not isinstance(jellyseerr_cfg, dict):
            return
        jellyfin_cfg = jellyseerr_cfg.get("jellyfin")
        if not isinstance(jellyfin_cfg, dict):
            return
        jellyfin_public = ctx["public_url"]("jellyfin")
        if jellyfin_public:
            jellyfin_cfg["external_url"] = jellyfin_public

    @staticmethod
    def _apply_app_auth_path_prefix_bases(
        cfg: dict[str, object], ctx: dict[str, Any],
    ) -> None:
        """Record the public path-prefix URL base per app on ``app_auth``."""
        app_auth_cfg = cfg.get("app_auth")
        if not isinstance(app_auth_cfg, dict):
            return
        include = app_auth_cfg.get("include")
        include_values = include if isinstance(include, list) else []
        path_prefix_url_bases: dict[str, str] = {}
        if ctx["strategy"] in {"path-prefix", "hybrid"} and ctx["gateway_host_with_port"]:
            for token in sorted(_path_prefix_url_base_tokens(cfg, include_values)):
                path_prefix_url_bases[token] = _path_prefix_url_base(token, ctx["prefix"])
        app_auth_cfg["path_prefix_url_base_by_app"] = path_prefix_url_bases

    @staticmethod
    def _apply_device_onboarding_links(
        homepage_cfg: dict[str, object], ctx: dict[str, Any],
    ) -> None:
        """Seed Jellyfin + Jellyseerr device-onboarding URLs from public URLs."""
        device_cfg = homepage_cfg.get("device_onboarding")
        if not isinstance(device_cfg, dict):
            return
        jellyfin_public = ctx["public_url"]("jellyfin")
        jellyseerr_public = ctx["public_url"]("jellyseerr")
        if jellyfin_public:
            device_cfg["jellyfin_url"] = jellyfin_public
            device_cfg["jellyfin_short_link"] = _url_host(jellyfin_public)
        if jellyseerr_public:
            device_cfg["jellyseerr_url"] = jellyseerr_public
            device_cfg["jellyseerr_short_link"] = _url_host(jellyseerr_public)

    @classmethod
    def _rewrite_homepage_hosts(
        cls,
        cfg: dict[str, object],
        homepage_cfg: dict[str, object],
        ctx: dict[str, Any],
    ) -> None:
        """Rewrite Homepage tiles to match the active routing strategy."""
        hosts = homepage_cfg.get("hosts")
        if not isinstance(hosts, list):
            return
        active_edge_provider = _tokenize(
            str((cfg.get("adapter_hooks") or {}).get("edge", {}).get("router_provider", ""))
        )
        edge_router_tokens = {"traefik", "envoy"}
        strategy = ctx["strategy"]
        gateway_host_with_port = ctx["gateway_host_with_port"]
        if strategy == "path-prefix" and gateway_host_with_port:
            rewritten_hosts = cls._homepage_hosts_path_prefix(
                hosts, ctx, active_edge_provider, edge_router_tokens,
            )
        elif strategy == "hybrid" and gateway_host_with_port:
            rewritten_hosts = cls._homepage_hosts_hybrid(
                hosts, ctx, active_edge_provider, edge_router_tokens,
            )
        else:
            rewritten_hosts = cls._homepage_hosts_default(
                hosts, ctx, active_edge_provider, edge_router_tokens,
            )
        homepage_cfg["hosts"] = cls._dedupe_hosts(rewritten_hosts)

    @staticmethod
    def _homepage_hosts_path_prefix(
        hosts: list, ctx: dict[str, Any],
        active_edge_provider: str, edge_router_tokens: set[str],
    ) -> list[str]:
        """Under path-prefix strategy every tile collapses to gateway+prefix+token."""
        rewritten: list[str] = []
        for raw_host in hosts:
            token = _homepage_host_token(str(raw_host or ""))
            if not token:
                continue
            if token in edge_router_tokens and token != active_edge_provider:
                continue
            rewritten.append(f"{ctx['gateway_host_with_port']}{ctx['prefix']}/{token}")
        return rewritten

    @staticmethod
    def _homepage_hosts_hybrid(
        hosts: list, ctx: dict[str, Any],
        active_edge_provider: str, edge_router_tokens: set[str],
    ) -> list[str]:
        """Hybrid strategy: Jellyfin goes direct, homepage stays native, rest gateway."""
        rewritten: list[str] = []
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
                if ctx["direct_host_with_port"]:
                    rewritten.append(ctx["direct_host_with_port"])
                continue
            if token == "homepage":
                homepage_direct_host = _homepage_direct_host(
                    host,
                    internet_exposed=ctx["internet_exposed"],
                    ingress=ctx["ingress"],
                    token=token,
                )
                rewritten.append(_host_with_port(homepage_direct_host, port=ctx["public_port"]))
                continue
            rewritten.append(f"{ctx['gateway_host_with_port']}{ctx['prefix']}/{token}")
        return rewritten

    @staticmethod
    def _homepage_hosts_default(
        hosts: list, ctx: dict[str, Any],
        active_edge_provider: str, edge_router_tokens: set[str],
    ) -> list[str]:
        """Subdomain strategy: swap .local for ingress suffix, append direct host."""
        rewritten: list[str] = []
        for raw_host in hosts:
            host = str(raw_host or "").strip().lower()
            if not host:
                continue
            token = _homepage_host_token(host)
            if token in edge_router_tokens and token != active_edge_provider:
                continue
            if ctx["ingress"] and host.endswith(".local"):
                host = f"{host[:-6]}.{ctx['ingress']}"
            rewritten.append(_host_with_port(host, port=ctx["public_port"]))
        if ctx["direct_host_with_port"]:
            rewritten.append(ctx["direct_host_with_port"])
        return rewritten

    @staticmethod
    def _dedupe_hosts(rewritten_hosts: list[str]) -> list[str]:
        """Remove case-insensitive duplicates while preserving order."""
        deduped: list[str] = []
        seen: set[str] = set()
        for host in rewritten_hosts:
            token = str(host or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            deduped.append(token)
        return deduped

    def apply_bootstrap_runtime_policy(self, 
        cfg: dict[str, object],
        *,
        selected_apps_csv: str = "",
        preconfigure_api_keys: bool = True,
        auto_download_content: bool = True,
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


_instance = StackControllerConfigPolicy()
parse_selected_apps_csv = _instance.parse_selected_apps_csv
apply_selected_apps_policy = _instance.apply_selected_apps_policy
apply_api_key_policy = _instance.apply_api_key_policy
apply_content_download_policy = _instance.apply_content_download_policy
apply_edge_url_policy = _instance.apply_edge_url_policy
apply_bootstrap_runtime_policy = _instance.apply_bootstrap_runtime_policy
# Pure helpers (``_tokenize``, ``_slugify``, ``_walk_path``, ``_set_bool_path``,
# ``_set_enabled``, ``_normalize_prefix``, ``_url_host``, ``_normalize_port``,
# ``_public_port``, ``_host_name``, ``_host_with_port``, ``_homepage_host_token``,
# ``_homepage_direct_host``, ``_path_prefix_url_base``) are now imported at the
# top of this module from ``controller_config_policy_helpers`` — they are
# already module-level and no re-export is required.
_load_policy_catalog = StackControllerConfigPolicy._load_policy_catalog
_selected_apps_policy_cfg = _instance._selected_apps_policy_cfg
_policy_map = _instance._policy_map
_policy_set = _instance._policy_set
_policy_map_of_sets = _instance._policy_map_of_sets
_policy_list = _instance._policy_list
_section_enabled = _instance._section_enabled
_path_prefix_url_base_tokens = _instance._path_prefix_url_base_tokens
