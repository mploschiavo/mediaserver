"""Configuration services: profile, routing, backup, env vars, manifests, user settings."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from ._resolve import resolve_config_path, resolve_profile_path


# ---------------------------------------------------------------------------
# Profile section helpers — read/write specific YAML sections
# ---------------------------------------------------------------------------

_profile_cache: tuple[dict[str, Any], Path | None, float] = ({}, None, 0.0)
_PROFILE_CACHE_TTL = 5.0  # seconds

def _load_profile_yaml() -> tuple[dict[str, Any], Path | None]:
    """Load the profile YAML with short TTL cache. Returns (data, path) or ({}, None)."""
    global _profile_cache
    import time as _t
    if _t.time() - _profile_cache[2] < _PROFILE_CACHE_TTL and _profile_cache[1] is not None:
        return _profile_cache[0], _profile_cache[1]
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if not resolved:
        return {}, None
    path = Path(resolved)
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        _profile_cache = (data, path, _t.time())
        return data, path
    except Exception:
        return {}, path


def _invalidate_profile_cache() -> None:
    global _profile_cache
    _profile_cache = ({}, None, 0.0)


def _validate_profile_data(data: dict[str, Any]) -> str | None:
    """Validate profile data before saving. Returns error string or None."""
    if not isinstance(data, dict):
        return "Profile must be a YAML mapping"
    meta = data.get("metadata")
    if not isinstance(meta, dict) or not meta.get("name"):
        return "Profile metadata.name is required — save would corrupt the profile"
    return None


def _save_profile_yaml(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """Write profile YAML back to disk after validation.

    Creates a backup before overwriting so corruption can be recovered.
    """
    # Validate before writing to prevent corruption
    err = _validate_profile_data(data)
    if err:
        return {"error": err}
    try:
        # Backup before overwriting
        backup = path.with_suffix(".yaml.bak")
        if path.is_file():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        _invalidate_profile_cache()
        return {"status": "saved", "file": str(path)}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def update_profile_section(section: str, value: Any) -> dict[str, Any]:
    """Update a top-level section in the profile YAML."""
    data, path = _load_profile_yaml()
    if path is None:
        return {"error": "Profile file not found"}
    data[section] = value
    return _save_profile_yaml(data, path)


# ---------------------------------------------------------------------------
# Jellyfin library management
# ---------------------------------------------------------------------------

def _media_server_id() -> str:
    """Resolve the configured media server ID from the profile technology bindings."""
    data, _ = _load_profile_yaml()
    bindings = data.get("technology_bindings", {})
    return str(bindings.get("media_server", "")).strip()


def get_libraries() -> dict[str, Any]:
    """Return the configured media server libraries.

    Reads from per-app config (jellyfin/controller.yaml) first,
    falls back to profile, then contract defaults.
    """
    from media_stack.services.app_config_service import load_app_config
    ms_id = _media_server_id()
    app_cfg = load_app_config(ms_id) if ms_id else {}
    if "libraries" in app_cfg:
        return {"libraries": app_cfg["libraries"], "source": "app_config", "media_server": ms_id}
    # Migration: check profile
    data, _ = _load_profile_yaml()
    ms_overrides = data.get(ms_id, {}) if ms_id else {}
    if isinstance(ms_overrides, dict) and "libraries" in ms_overrides:
        return {"libraries": ms_overrides["libraries"], "source": "profile", "media_server": ms_id}
    # Fall back to service contract defaults
    libs = []
    try:
        from .registry import _find_services_dir
        svc_dir = _find_services_dir()
        svc_yaml = (svc_dir / f"{ms_id}.yaml") if svc_dir and ms_id else None
        if svc_yaml and svc_yaml.is_file():
            svc_cfg = yaml.safe_load(svc_yaml.read_text()) or {}
            libs = svc_cfg.get("defaults", {}).get("libraries", {}).get("libraries", [])
    except Exception:
        pass
    return {"libraries": libs, "source": "defaults" if libs else "not_configured", "media_server": ms_id}


def update_libraries(libraries: list[dict[str, Any]]) -> dict[str, Any]:
    """Update media server library configuration in per-app config."""
    for lib in libraries:
        if not lib.get("name") or not lib.get("collection_type") or not lib.get("paths"):
            return {"error": f"Each library needs name, collection_type, and paths. Invalid: {lib.get('name', '?')}"}
    ms_id = _media_server_id()
    if not ms_id:
        return {"error": "No media server configured"}
    from media_stack.services.app_config_service import update_app_config_section
    result = update_app_config_section(ms_id, "libraries", libraries)
    if "error" not in result:
        result["libraries"] = libraries
        result["note"] = "Run configure-libraries to apply changes"
    return result


# ---------------------------------------------------------------------------
# Download category management
# ---------------------------------------------------------------------------

def get_download_categories() -> dict[str, Any]:
    """Return configured download categories from per-app config or profile."""
    from media_stack.services.app_config_service import load_app_config
    # Try torrent client first
    data, _ = _load_profile_yaml()
    bindings = data.get("technology_bindings", {})
    tc_id = bindings.get("torrent_client", "qbittorrent")
    app_cfg = load_app_config(tc_id)
    if "categories" in app_cfg:
        return {"categories": app_cfg["categories"], "source": "app_config"}
    # Migration: check profile
    cats = data.get("download_categories")
    if isinstance(cats, dict) and cats:
        return {"categories": cats, "source": "profile"}
    return {"categories": {}, "source": "not_configured",
            "note": "Add categories in Config > Downloads"}


def update_download_categories(categories: dict[str, str]) -> dict[str, Any]:
    """Update download categories in per-app config."""
    if not categories:
        return {"error": "At least one category is required"}
    data, _ = _load_profile_yaml()
    bindings = data.get("technology_bindings", {})
    tc_id = bindings.get("torrent_client", "qbittorrent")
    from media_stack.services.app_config_service import update_app_config_section
    result = update_app_config_section(tc_id, "categories", categories)
    if "error" not in result:
        result["categories"] = categories
        result["note"] = "Run configure-categories to apply changes"
    return result


# ---------------------------------------------------------------------------
# Metadata language
# ---------------------------------------------------------------------------

def get_metadata_settings() -> dict[str, Any]:
    """Return metadata language and country settings + available presets."""
    data, _ = _load_profile_yaml()
    meta = data.get("metadata", {})
    # Presets can be overridden in profile YAML
    presets = data.get("metadata_presets")
    if not isinstance(presets, list) or not presets:
        presets = [
            {"language": "en", "country": "US", "label": "English (US)"},
            {"language": "en", "country": "GB", "label": "English (UK)"},
            {"language": "en", "country": "AU", "label": "English (AU)"},
            {"language": "de", "country": "DE", "label": "Deutsch"},
            {"language": "fr", "country": "FR", "label": "Fran\u00e7ais"},
            {"language": "es", "country": "ES", "label": "Espa\u00f1ol"},
            {"language": "es", "country": "MX", "label": "Espa\u00f1ol (MX)"},
            {"language": "pt", "country": "BR", "label": "Portugu\u00eas (BR)"},
            {"language": "pt", "country": "PT", "label": "Portugu\u00eas (PT)"},
            {"language": "it", "country": "IT", "label": "Italiano"},
            {"language": "nl", "country": "NL", "label": "Nederlands"},
            {"language": "sv", "country": "SE", "label": "Svenska"},
            {"language": "no", "country": "NO", "label": "Norsk"},
            {"language": "da", "country": "DK", "label": "Dansk"},
            {"language": "fi", "country": "FI", "label": "Suomi"},
            {"language": "pl", "country": "PL", "label": "Polski"},
            {"language": "ru", "country": "RU", "label": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439"},
            {"language": "ja", "country": "JP", "label": "\u65e5\u672c\u8a9e"},
            {"language": "ko", "country": "KR", "label": "\ud55c\uad6d\uc5b4"},
            {"language": "zh", "country": "CN", "label": "\u4e2d\u6587 (CN)"},
            {"language": "zh", "country": "TW", "label": "\u4e2d\u6587 (TW)"},
            {"language": "ar", "country": "AE", "label": "\u0627\u0644\u0639\u0631\u0628\u064a\u0629"},
            {"language": "hi", "country": "IN", "label": "\u0939\u093f\u0928\u094d\u0926\u0940"},
            {"language": "tr", "country": "TR", "label": "T\u00fcrk\u00e7e"},
            {"language": "th", "country": "TH", "label": "\u0e44\u0e17\u0e22"},
        ]
    return {
        "language": meta.get("language", "en"),
        "country": meta.get("country", "US"),
        "source": "profile" if meta else "defaults",
        "presets": presets,
    }


def update_metadata_settings(language: str, country: str) -> dict[str, Any]:
    """Update metadata language/country in the profile (merges, doesn't replace)."""
    if not language or not country:
        return {"error": "language and country are required"}
    data, path = _load_profile_yaml()
    if path is None:
        return {"error": "Profile file not found"}
    meta = data.get("metadata", {})
    if not isinstance(meta, dict):
        meta = {}
    meta["language"] = language
    meta["country"] = country
    data["metadata"] = meta
    result = _save_profile_yaml(data, path)
    if "error" not in result:
        result["metadata"] = {"language": language, "country": country}
        result["note"] = "Run bootstrap to apply metadata settings to media server and Arr apps"
    return result


# ---------------------------------------------------------------------------
# IPTV / Live TV sources
# ---------------------------------------------------------------------------

def get_livetv_sources() -> dict[str, Any]:
    """Return configured Live TV tuner and guide sources.

    Reads from per-app config (jellyfin/controller.yaml) first,
    falls back to profile live_tv_defaults for migration.
    """
    from media_stack.services.app_config_service import load_app_config
    app_cfg = load_app_config("jellyfin")
    ltv = app_cfg.get("livetv", {})
    # Migration: fall back to profile if per-app config is empty
    if not ltv:
        data, _ = _load_profile_yaml()
        ltv = data.get("live_tv_defaults", {})
    tuners = ltv.get("tuners", [])
    guides = ltv.get("guides", [])
    if not tuners and ltv.get("tuner_url"):
        tuners = [{"url": ltv["tuner_url"], "name": "Default"}]
    if not guides and ltv.get("guide_url"):
        guides = [{"url": ltv["guide_url"], "name": "Default"}]
    return {
        "tuners": tuners,
        "guides": guides,
        "tuner_url": tuners[0]["url"] if tuners else "",
        "guide_url": guides[0]["url"] if guides else "",
        "load_all_tuners": bool(ltv.get("load_all_tuners", False)),
        "source": "app_config" if app_cfg.get("livetv") else ("profile" if tuners else "not_configured"),
    }


def get_discovery_lists() -> dict[str, Any]:
    """Return configured discovery lists (Trakt, IMDb, etc.) from the profile."""
    data, _ = _load_profile_yaml()
    lists = data.get("discovery_lists", [])
    if not isinstance(lists, list):
        lists = []
    return {"lists": lists, "count": len(lists)}


def update_discovery_lists(lists: list[dict[str, Any]]) -> dict[str, Any]:
    """Update discovery list configuration in the profile."""
    result = update_profile_section("discovery_lists", lists)
    if "error" not in result:
        result["lists"] = lists
        result["note"] = "Run bootstrap to apply discovery list changes"
    return result


def update_livetv_sources(
    tuners: list[dict[str, str]] | None = None,
    guides: list[dict[str, str]] | None = None,
    tuner_url: str = "", guide_url: str = "",
    load_all_tuners: bool | None = None,
) -> dict[str, Any]:
    """Update IPTV sources. Saves to per-app config (jellyfin/controller.yaml)."""
    from media_stack.services.app_config_service import load_app_config, save_app_config
    app_cfg = load_app_config("jellyfin")
    ltv = app_cfg.get("livetv", {})
    if load_all_tuners is not None:
        ltv["load_all_tuners"] = bool(load_all_tuners)
    if tuners is not None:
        ltv["tuners"] = tuners
    elif tuner_url:
        ltv["tuners"] = [{"url": tuner_url, "name": "Default"}]
    if guides is not None:
        ltv["guides"] = guides
    elif guide_url:
        ltv["guides"] = [{"url": guide_url, "name": "Default"}]
    if ltv.get("tuners"):
        ltv["tuner_url"] = ltv["tuners"][0].get("url", "")
    if ltv.get("guides"):
        ltv["guide_url"] = ltv["guides"][0].get("url", "")
    app_cfg["livetv"] = ltv
    result = save_app_config("jellyfin", app_cfg)
    if "error" not in result:
        result["tuners"] = ltv.get("tuners", [])
        result["guides"] = ltv.get("guides", [])
        result["note"] = "Run configure-livetv to apply Live TV changes"
    return result


def get_iptv_countries() -> dict[str, Any]:
    """Return available IPTV country sources.

    The country list is read from the profile YAML if available,
    otherwise falls back to a well-known set from iptv-org.
    """
    data, _ = _load_profile_yaml()
    custom = data.get("iptv_countries")
    if isinstance(custom, list) and custom:
        return {"countries": custom, "source": "profile"}
    # Default set — URLs are iptv-org templates. Users can override in profile YAML.
    ltv_defaults = data.get("live_tv_defaults", {})
    tuner_tpl = ltv_defaults.get("tuner_url_template", "")
    guide_tpl = ltv_defaults.get("guide_url_template", "")
    # Build country list using templates — no live URL probing.
    # Actual provider resolution (with fallback) happens at job run time,
    # not on every dashboard page load.
    from media_stack.services.epg_provider_service import get_guide_providers, _expand_url
    guide_providers = get_guide_providers()
    countries = []
    for c, n in [
        ("us", "United States"), ("gb", "United Kingdom"), ("ca", "Canada"),
        ("au", "Australia"), ("de", "Germany"), ("fr", "France"),
        ("es", "Spain"), ("it", "Italy"), ("br", "Brazil"), ("mx", "Mexico"),
        ("jp", "Japan"), ("kr", "South Korea"), ("in", "India"),
        ("nl", "Netherlands"), ("se", "Sweden"), ("no", "Norway"),
        ("dk", "Denmark"), ("fi", "Finland"), ("pl", "Poland"),
        ("pt", "Portugal"), ("ru", "Russia"), ("za", "South Africa"),
        ("ar", "Argentina"), ("co", "Colombia"), ("cl", "Chile"),
        ("tr", "Turkey"), ("il", "Israel"), ("ae", "UAE"),
        ("cn", "China"), ("tw", "Taiwan"), ("ph", "Philippines"),
        ("th", "Thailand"), ("id", "Indonesia"), ("hk", "Hong Kong"),
    ]:
        # Use first provider that has a URL for this country
        g_url = ""
        for p in guide_providers:
            url = _expand_url(p, c)
            if url:
                g_url = url
                break
        countries.append({
            "code": c, "name": n,
            "tuner_url": tuner_tpl.replace("{code}", c),
            "guide_url": g_url or guide_tpl.replace("{code}", c),
        })
    return {"countries": countries, "source": "defaults"}


def get_profile() -> dict[str, Any]:
    """Read and return the bootstrap profile YAML."""
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if not resolved:
        return {"profile": None, "error": "Profile not found"}
    path = Path(resolved)
    try:
        import yaml
        with open(path) as f:
            profile = yaml.safe_load(f) or {}
        return {"profile": profile, "file": str(path)}
    except ImportError:
        return {"profile_raw": path.read_text(encoding="utf-8"), "file": str(path)}
    except Exception as exc:
        return {"profile": None, "error": str(exc)[:120]}


def save_profile(content: str, reload_config: Callable[[], None] | None = None) -> dict[str, Any]:
    """Save bootstrap profile YAML."""
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if not resolved:
        return {"error": "Profile file not found"}
    path = Path(resolved)
    try:
        path.write_text(content, encoding="utf-8")
        if reload_config:
            reload_config()
        return {"status": "saved", "file": str(path)}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def get_routing() -> dict[str, Any]:
    """Return current routing configuration — persisted overrides take precedence."""
    import yaml

    routing: dict[str, Any] = {}

    # 1. Load base from profile YAML
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if resolved:
        try:
            with open(resolved) as f:
                profile = yaml.safe_load(f) or {}
            routing = dict(profile.get("routing") or {})
        except Exception:
            pass

    # 2. Overlay persisted runtime overrides (from POST /api/routing)
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    overrides_path = config_root / ".controller" / "routing-overrides.yaml"
    if overrides_path.is_file():
        try:
            overrides = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
            routing.update(overrides.get("routing") or {})
        except Exception:
            pass

    return {
        "base_domain": str(routing.get("base_domain", "local")),
        "stack_subdomain": str(routing.get("stack_subdomain", "media-stack")),
        "gateway_host": str(routing.get("gateway_host", "apps.media-stack.local")),
        "gateway_port": int(routing.get("gateway_port", 80)),
        "app_path_prefix": str(routing.get("app_path_prefix", "/app")),
        "strategy": str(routing.get("strategy", "hybrid")),
        "internet_exposed": bool(routing.get("internet_exposed", False)),
        "direct_hosts": dict(routing.get("direct_hosts") or {}),
    }


def update_routing(updates: dict[str, Any], action_trigger: Callable | None = None) -> dict[str, Any]:
    """Update routing config in profile YAML and trigger regeneration."""
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if not resolved:
        return {"error": "Profile file not found"}
    profile_path = Path(resolved)
    try:
        import yaml
        with open(profile_path) as f:
            profile = yaml.safe_load(f) or {}
        routing = profile.setdefault("routing", {})
        allowed_keys = {"base_domain", "stack_subdomain", "gateway_host", "gateway_port", "app_path_prefix", "strategy", "internet_exposed"}
        changed = []
        for key, value in updates.items():
            if key in allowed_keys and str(routing.get(key, "")) != str(value):
                routing[key] = value
                changed.append(key)
        # Sync gateway_host <-> subdomain/domain in both directions
        if ("stack_subdomain" in changed or "base_domain" in changed) and "gateway_host" not in changed:
            # Derive gateway_host from subdomain + domain
            sub = routing.get("stack_subdomain", "media-stack")
            dom = routing.get("base_domain", "local")
            old_host = str(routing.get("gateway_host", ""))
            prefix = old_host.split(".")[0] if old_host and "." in old_host else "apps"
            routing["gateway_host"] = f"{prefix}.{sub}.{dom}"
            changed.append("gateway_host")
        elif "gateway_host" in changed and "stack_subdomain" not in changed and "base_domain" not in changed:
            # Derive subdomain + domain from gateway_host
            parts = str(routing["gateway_host"]).split(".")
            if len(parts) >= 3:
                routing["stack_subdomain"] = parts[1]
                routing["base_domain"] = ".".join(parts[2:])
                if "stack_subdomain" not in changed:
                    changed.append("stack_subdomain")
                if "base_domain" not in changed:
                    changed.append("base_domain")
        if not changed:
            return {"status": "no_changes", "routing": routing}
        # Persist to writable config root (survives container restarts)
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        overrides_path = config_root / ".controller" / "routing-overrides.yaml"
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        with open(overrides_path, "w") as f:
            yaml.dump({"routing": routing}, f, default_flow_style=False, sort_keys=False)
        # Also try to update the profile source (may be read-only)
        try:
            with open(profile_path, "w") as f:
                yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except OSError:
            pass
        if action_trigger:
            action_trigger("envoy-config", {})
        return {
            "status": "updated",
            "persisted_to": str(overrides_path),
            "changed": changed,
            "routing": routing,
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def get_env() -> dict[str, Any]:
    """Return runtime environment information."""
    import platform
    import socket

    namespace = os.environ.get("K8S_NAMESPACE", "")
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
    profile_name = ""
    resolved = resolve_profile_path(profile_file)
    if resolved:
        profile_name = Path(resolved).name

    node_ip = os.environ.get("NODE_IP", "")
    if not node_ip:
        try:
            node_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            node_ip = ""

    # Multi-node K8s: discover all node IPs
    node_ips: list[str] = [node_ip] if node_ip else []
    if namespace:
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            nodes = v1.list_node()
            node_ips = []
            for node in nodes.items:
                for addr in (node.status.addresses or []):
                    if addr.type == "InternalIP":
                        node_ips.append(addr.address)
                        break
        except Exception:
            pass

    return {
        "namespace": namespace,
        "profile_name": profile_name,
        "node_ip": node_ip,
        "node_ips": node_ips,
        "node_count": len(node_ips),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "runtime": "kubernetes" if namespace else "compose",
    }


def get_backup(state: Any) -> bytes:
    """Create a JSON backup of all discoverable config and service state."""
    backup: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "version": "2",
        "env": get_env(),
        "state": state.to_dict() if hasattr(state, "to_dict") else {},
    }

    # Profile YAML
    resolved_profile = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    if resolved_profile:
        backup["profile_raw"] = Path(resolved_profile).read_text(encoding="utf-8", errors="replace")

    # Service configs from config root — registry-driven paths
    from .registry import SERVICES as _backup_svcs
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    service_configs: dict[str, str] = {}
    if config_root.is_dir():
        # Collect config file paths from the service registry
        config_files: list[str] = []
        for svc in _backup_svcs:
            if svc.api_key_config:
                config_files.append(svc.api_key_config)
            if svc.password_config:
                config_files.append(svc.password_config)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_configs: list[str] = []
        for cf in config_files:
            if cf not in seen:
                seen.add(cf)
                unique_configs.append(cf)
        for rel_path in unique_configs:
            full_path = config_root / rel_path
            if full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    if len(content) < 100_000:  # Skip huge files
                        service_configs[rel_path] = content
                except Exception:
                    pass
    if service_configs:
        backup["service_configs"] = service_configs

    # API keys — full values for restore, masked preview for display
    api_keys: dict[str, str] = {}
    api_keys_masked: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        if key.endswith("_API_KEY") and value:
            api_keys[key] = value
            api_keys_masked[key] = value[:8] + "..." if len(value) > 8 else value
    if api_keys:
        backup["api_keys"] = api_keys
        backup["api_keys_masked"] = api_keys_masked

    # Known config paths from registry (for restore validation)
    valid_paths: list[str] = []
    for svc in _backup_svcs:
        if svc.api_key_config:
            valid_paths.append(svc.api_key_config)
        if svc.password_config:
            valid_paths.append(svc.password_config)
    backup["valid_config_paths"] = sorted(set(valid_paths))

    return json.dumps(backup, indent=2, default=str).encode("utf-8")


def restore_backup(backup: dict[str, Any], state: Any = None) -> dict[str, Any]:
    """Restore service configs from a backup JSON payload.

    Creates a pre-restore backup, validates paths against the service
    registry, restores API keys to env vars, and rolls back on failure.
    """
    # Validate backup version
    version = str(backup.get("version", ""))
    if version not in ("1", "2"):
        return {"status": "error", "error": f"unsupported backup version: {version!r}"}

    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    restored: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    # Build set of valid config paths from the registry
    from .registry import SERVICES as _restore_svcs
    valid_paths: set[str] = set()
    for svc in _restore_svcs:
        if svc.api_key_config:
            valid_paths.add(svc.api_key_config)
        if svc.password_config:
            valid_paths.add(svc.password_config)

    # Pre-restore backup — save current state before overwriting
    pre_restore: dict[str, str] = {}
    service_configs = backup.get("service_configs", {})
    if not isinstance(service_configs, dict):
        return {"status": "error", "error": "service_configs must be an object"}

    for rel_path in service_configs:
        existing = config_root / rel_path
        if existing.is_file():
            try:
                pre_restore[rel_path] = existing.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # Restore service configs
    for rel_path, content in service_configs.items():
        if ".." in rel_path or rel_path.startswith("/"):
            errors.append(f"skipped unsafe path: {rel_path}")
            continue
        if valid_paths and rel_path not in valid_paths:
            skipped.append(rel_path)
            continue
        target = config_root / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            restored.append(rel_path)
        except Exception as exc:
            errors.append(f"{rel_path}: {exc}")

    # Rollback on critical failure (>50% errors)
    if errors and len(errors) > len(restored):
        rollback_ok = 0
        for rel_path, content in pre_restore.items():
            try:
                (config_root / rel_path).write_text(content, encoding="utf-8")
                rollback_ok += 1
            except Exception:
                pass
        return {
            "status": "rolled_back",
            "errors": errors,
            "rollback_count": rollback_ok,
            "note": "More errors than successes — rolled back to pre-restore state",
        }

    # Restore API keys to environment
    api_keys = backup.get("api_keys", {})
    keys_restored: list[str] = []
    if isinstance(api_keys, dict):
        for key, value in api_keys.items():
            if key.endswith("_API_KEY") and isinstance(value, str) and value and "..." not in value:
                os.environ[key] = value
                keys_restored.append(key)

    return {
        "status": "ok" if not errors else "partial",
        "restored": restored,
        "skipped": skipped,
        "keys_restored": keys_restored,
        "errors": errors,
        "pre_restore_count": len(pre_restore),
        "note": "Restart services to apply restored configs",
    }


def get_envvars() -> dict[str, str]:
    """Return relevant environment variables — prefixes derived from registry."""
    from .registry import SERVICES as _env_svcs
    # Platform prefixes that are always relevant
    _platform = ("BOOTSTRAP_", "STACK_", "K8S_", "CONTROLLER_", "PUID", "PGID", "TZ")
    # Service-derived prefixes from api_key_env (e.g. SONARR_API_KEY → SONARR_)
    _svc = {e.api_key_env.split("_")[0] + "_" for e in _env_svcs if e.api_key_env}
    relevant_prefixes = set(_platform) | _svc
    return {
        k: v for k, v in sorted(os.environ.items())
        if any(k.startswith(p) for p in relevant_prefixes)
    }


def set_envvar(key: str, value: str) -> dict[str, Any]:
    """Set an environment variable."""
    os.environ[key] = value
    return {"status": "set", "key": key, "value": value}


def get_manifests() -> dict[str, Any]:
    """Return the compose file, bootstrap config, or kustomization content."""
    namespace = os.environ.get("K8S_NAMESPACE", "")

    # K8s: try to get kustomization or deployment spec
    if namespace:
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            apps_v1 = k8s_client.AppsV1Api()
            deps = apps_v1.list_namespaced_deployment(namespace)
            services = [{"name": d.metadata.name, "image": d.spec.template.spec.containers[0].image if d.spec.template.spec.containers else ""} for d in deps.items]
            return {"type": "kubernetes", "namespace": namespace, "deployments": len(services), "services": services}
        except Exception as exc:
            return {"type": "kubernetes", "error": str(exc)[:80]}

    # Compose: try to find compose file
    compose_file = os.environ.get("COMPOSE_FILE", "")
    if not compose_file:
        for candidate in ["/compose/docker-compose.yml", "./docker-compose.yml"]:
            if Path(candidate).is_file():
                compose_file = candidate
                break
    if compose_file and Path(compose_file).is_file():
        return {"type": "compose", "file": compose_file, "content": Path(compose_file).read_text(encoding="utf-8", errors="replace")}

    # Fallback: show bootstrap config JSON (always available in image)
    config_path = resolve_config_path()
    if config_path:
        try:
            cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
            # Show a summary, not the full 60KB config
            summary = {
                "services": list((cfg.get("services") or {}).keys()) if isinstance(cfg.get("services"), dict) else [],
                "disk_guardrails": cfg.get("disk_guardrails", {}).get("enabled", False),
                "preflight_handlers": [h.get("name") for h in cfg.get("container_preflight_handlers", [])],
                "post_handlers": [h.get("name") for h in cfg.get("container_post_setup_handlers", [])],
            }
            return {"type": "bootstrap-config", "file": config_path, "content": json.dumps(summary, indent=2)}
        except Exception:
            pass

    # Also try listing running containers as a manifest equivalent
    try:
        import docker
        client = docker.from_env()
        containers = [{"name": c.name, "image": c.image.tags[0] if c.image.tags else str(c.image.short_id), "status": c.status} for c in client.containers.list()]
        return {"type": "compose-runtime", "content": json.dumps(containers, indent=2), "note": "Compose file not mounted. Showing running containers."}
    except Exception:
        pass

    return {"type": "unknown", "content": None, "error": "No manifest found. Mount compose file or use K8s."}


def get_onboarding_status() -> dict[str, Any]:
    """Return onboarding checklist — what's configured vs what needs attention."""
    from .registry import SERVICES
    from .health import discover_api_keys, probe_services
    from ..cache import api_cache

    steps: list[dict[str, Any]] = []

    # 1. Services running?
    health = probe_services(api_cache)
    healthy = health.get("healthy", 0)
    total = health.get("total", 0)
    steps.append({
        "id": "services_running",
        "label": "Services running",
        "status": "ok" if healthy >= total * 0.8 else "warn" if healthy > 0 else "error",
        "detail": f"{healthy}/{total} healthy",
    })

    # 2. API keys discovered?
    keys = discover_api_keys()
    key_count = len(keys)
    expected = len([s for s in SERVICES if s.api_key_env])
    steps.append({
        "id": "api_keys",
        "label": "API keys discovered",
        "status": "ok" if key_count >= expected else "warn",
        "detail": f"{key_count}/{expected} keys",
    })

    # 3. Media libraries configured?
    libs = get_libraries()
    lib_count = len(libs.get("libraries", []))
    steps.append({
        "id": "libraries",
        "label": "Media libraries configured",
        "status": "ok" if lib_count > 0 else "pending",
        "detail": f"{lib_count} libraries" if lib_count else "No libraries — go to Config > Libraries",
    })

    # 4. Routing configured?
    routing = get_routing()
    has_routing = routing.get("gateway_host", "") != ""
    steps.append({
        "id": "routing",
        "label": "Network routing configured",
        "status": "ok" if has_routing else "pending",
        "detail": routing.get("gateway_host", "not set"),
    })

    # 5. Download clients working?
    data, _ = _load_profile_yaml()
    bindings = data.get("technology_bindings", {})
    has_torrent = bool(bindings.get("torrent_client"))
    has_usenet = bool(bindings.get("usenet_client"))
    steps.append({
        "id": "download_clients",
        "label": "Download clients configured",
        "status": "ok" if (has_torrent or has_usenet) else "pending",
        "detail": ", ".join(filter(None, [
            bindings.get("torrent_client"), bindings.get("usenet_client"),
        ])) or "none configured",
    })

    # 6. Bootstrap completed?
    steps.append({
        "id": "bootstrap",
        "label": "Initial bootstrap completed",
        "status": "ok" if health.get("healthy", 0) > 0 else "pending",
        "detail": "Run 'Configure All' to bootstrap the stack",
    })

    completed = sum(1 for s in steps if s["status"] == "ok")
    return {
        "steps": steps,
        "completed": completed,
        "total": len(steps),
        "progress_pct": round(completed / len(steps) * 100) if steps else 0,
        "is_first_run": completed < len(steps) * 0.5,
    }


def add_custom_service(service_def: dict[str, Any]) -> dict[str, Any]:
    """Add a custom service by writing a new YAML file to contracts/services/.

    Requires at minimum: id, name, host, port. Creates a minimal service
    YAML that the registry will pick up on reload.
    """
    svc_id = str(service_def.get("id", "")).strip().lower()
    if not svc_id or not service_def.get("name") or not service_def.get("port"):
        return {"error": "id, name, and port are required"}
    if not svc_id.replace("-", "").replace("_", "").isalnum():
        return {"error": "id must be alphanumeric (hyphens and underscores allowed)"}

    # Find the services directory
    svc_dir = Path(os.environ.get("SERVICES_REGISTRY_DIR", ""))
    if not svc_dir.is_dir():
        svc_dir = Path(__file__).resolve().parents[4] / "contracts" / "services"
    if not svc_dir.is_dir():
        return {"error": "Services directory not found"}

    target = svc_dir / f"{svc_id}.yaml"
    if target.exists():
        return {"error": f"Service '{svc_id}' already exists"}

    svc_yaml = {
        "service": {
            "id": svc_id,
            "name": str(service_def.get("name", svc_id)),
            "desc": str(service_def.get("desc", "")),
            "category": str(service_def.get("category", "custom")),
            "host": str(service_def.get("host", svc_id)),
            "port": int(service_def.get("port", 0)),
            "health_path": str(service_def.get("health_path", "/")),
            "web_ui": bool(service_def.get("web_ui", True)),
        }
    }
    try:
        with open(target, "w") as f:
            yaml.dump(svc_yaml, f, default_flow_style=False, sort_keys=False)
        # Reload registry to pick up the new service
        from .registry import reload_registry
        reload_registry()
        return {"status": "created", "file": str(target), "service_id": svc_id}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def get_config_drift() -> dict[str, Any]:
    """Compare expected config (profile YAML) vs actual running state.

    Checks:
    - Routing: profile routing vs live routing overrides
    - Service auth: expected auth mode vs actual config.xml settings
    - API keys: env vars vs config file keys (stale?)
    - Container images: declared vs running
    """
    drifts: list[dict[str, str]] = []

    # 1. Routing drift — compare profile vs overrides
    import yaml
    resolved = resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
    profile_routing: dict[str, Any] = {}
    if resolved:
        try:
            with open(resolved) as f:
                profile = yaml.safe_load(f) or {}
            profile_routing = profile.get("routing") or {}
        except Exception:
            pass
    live_routing = get_routing()
    for key in ("base_domain", "stack_subdomain", "gateway_host", "gateway_port", "strategy"):
        expected = str(profile_routing.get(key, ""))
        actual = str(live_routing.get(key, ""))
        if expected and actual and expected != actual:
            drifts.append({"area": "routing", "key": key, "expected": expected, "actual": actual})

    # 2. API key drift — env var vs config file
    from .registry import SERVICES, read_api_key_from_file
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    for svc in SERVICES:
        if not svc.api_key_env or not svc.api_key_config:
            continue
        env_key = (os.environ.get(svc.api_key_env) or "").strip()
        file_key = read_api_key_from_file(svc.id, config_root)
        if env_key and file_key and env_key != file_key:
            drifts.append({
                "area": "api_key", "key": svc.id,
                "expected": f"{env_key[:4]}...{env_key[-4:]}" if len(env_key) > 8 else "set",
                "actual": f"{file_key[:4]}...{file_key[-4:]}" if len(file_key) > 8 else "set",
                "note": "Env var differs from config file — run bootstrap to resync",
            })

    # 3. Container image drift — check running vs declared
    namespace = os.environ.get("K8S_NAMESPACE", "")
    if not namespace:
        try:
            import docker
            client = docker.from_env()
            for c in client.containers.list():
                image = c.image.tags[0] if c.image.tags else ""
                if image and "@sha256:" not in image:
                    # Check if image has been updated since container started
                    created = c.image.attrs.get("Created", "") if c.image.attrs else ""
                    started = c.attrs.get("State", {}).get("StartedAt", "")
                    if created and started and created > started:
                        drifts.append({
                            "area": "image", "key": c.name,
                            "expected": "latest pulled image",
                            "actual": f"running image created {created[:19]}",
                            "note": "Container running older image than what's pulled",
                        })
        except Exception:
            pass

    # 4. Credential drift — login validation status
    try:
        from .health import probe_credentials
        cred_result = probe_credentials()
        for svc_id, status in cred_result.get("credentials", {}).items():
            if status == "fail":
                drifts.append({
                    "area": "credentials", "key": svc_id,
                    "expected": "ok (valid login)",
                    "actual": "fail (wrong password)",
                    "note": "Run Validate Credentials to auto-sync",
                })
    except Exception:
        pass

    # 5. Live TV — configured but no tuners?
    ltv = get_livetv_sources()
    if ltv.get("source") == "not_configured":
        drifts.append({
            "area": "live_tv", "key": "tuners",
            "expected": "at least 1 tuner configured",
            "actual": "none",
            "note": "Go to Config > Live TV to add IPTV sources",
        })

    # 6. Libraries — none configured?
    libs = get_libraries()
    if not libs.get("libraries"):
        drifts.append({
            "area": "libraries", "key": "media_libraries",
            "expected": "at least 1 library configured",
            "actual": "none",
            "note": "Go to Config > Libraries to add media folders",
        })

    # 7. Metadata — still on defaults?
    meta = get_metadata_settings()
    if meta.get("source") == "defaults":
        drifts.append({
            "area": "metadata", "key": "language",
            "expected": "configured",
            "actual": f"{meta.get('language', '?')}/{meta.get('country', '?')} (default)",
            "note": "Review in Config > Metadata if you need a different language",
        })

    return {
        "drifts": drifts,
        "total": len(drifts),
        "clean": len(drifts) == 0,
    }
