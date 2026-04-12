"""Profile and network routing configuration."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import yaml

from .. import _resolve as _resolve_mod
from ._profile import ProfileService
from ._livetv import APP_CONFIG_SECTIONS, STRIPPED_FROM_PROFILE


class RoutingConfigService:
    """Manages network routing and full-profile read/write."""

    def __init__(self, profile: ProfileService):
        self._profile = profile

    def get_profile(self) -> dict[str, Any]:
        """Read and return the profile YAML (slim — app config sections stripped)."""
        profile_data, path = self._profile.load()
        if path is None:
            return {"profile": None, "error": "Profile not found"}
        ms_id = self._profile.media_server_id()
        strip_keys = STRIPPED_FROM_PROFILE | ({ms_id} if ms_id else set())
        moved = [k for k in (APP_CONFIG_SECTIONS | ({ms_id} if ms_id else set())) if k in profile_data]
        slim = {k: v for k, v in profile_data.items() if k not in strip_keys}
        result: dict[str, Any] = {"profile": slim, "file": str(path)}
        if moved:
            result["moved_to_app_config"] = moved
        return result

    def save_profile(self, content: str, reload_config: Callable[[], None] | None = None) -> dict[str, Any]:
        resolved = _resolve_mod.resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
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

    def get_routing(self) -> dict[str, Any]:
        routing: dict[str, Any] = {}
        resolved = _resolve_mod.resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
        if resolved:
            try:
                with open(resolved) as f:
                    profile = yaml.safe_load(f) or {}
                routing = dict(profile.get("routing") or {})
            except Exception:
                pass
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

    def update_routing(self, updates: dict[str, Any], action_trigger: Callable | None = None) -> dict[str, Any]:
        resolved = _resolve_mod.resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
        if not resolved:
            return {"error": "Profile file not found"}
        profile_path = Path(resolved)
        try:
            with open(profile_path) as f:
                profile = yaml.safe_load(f) or {}
            routing = profile.setdefault("routing", {})
            allowed_keys = {"base_domain", "stack_subdomain", "gateway_host", "gateway_port", "app_path_prefix", "strategy", "internet_exposed"}
            changed = []
            for key, value in updates.items():
                if key in allowed_keys and str(routing.get(key, "")) != str(value):
                    routing[key] = value
                    changed.append(key)
            if ("stack_subdomain" in changed or "base_domain" in changed) and "gateway_host" not in changed:
                sub = routing.get("stack_subdomain", "media-stack")
                dom = routing.get("base_domain", "local")
                old_host = str(routing.get("gateway_host", ""))
                prefix = old_host.split(".")[0] if old_host and "." in old_host else "apps"
                routing["gateway_host"] = f"{prefix}.{sub}.{dom}"
                changed.append("gateway_host")
            elif "gateway_host" in changed and "stack_subdomain" not in changed and "base_domain" not in changed:
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
            config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
            overrides_path = config_root / ".controller" / "routing-overrides.yaml"
            overrides_path.parent.mkdir(parents=True, exist_ok=True)
            with open(overrides_path, "w") as f:
                yaml.dump({"routing": routing}, f, default_flow_style=False, sort_keys=False)
            try:
                with open(profile_path, "w") as f:
                    yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            except OSError:
                pass
            if action_trigger:
                action_trigger("envoy-config", {})
            return {"status": "updated", "persisted_to": str(overrides_path), "changed": changed, "routing": routing}
        except Exception as exc:
            return {"error": str(exc)[:200]}
