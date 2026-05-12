"""Profile and network routing configuration."""
from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import os
from pathlib import Path
from typing import Any, Callable

import yaml

from .. import _resolve as _resolve_mod
from ._profile import ProfileService
from ._livetv import APP_CONFIG_SECTIONS, STRIPPED_FROM_PROFILE
import logging


class RoutingConfigService:
    """Manages network routing and full-profile read/write."""

    def __init__(self, profile: ProfileService):
        self._profile = profile

    def overrides_path(self) -> Path:
        """Resolve ``${CONFIG_ROOT}/.controller/routing-overrides.yaml``.

        Centralised so the env-read happens in one place rather
        than scattered across multiple methods (and external
        writers like ``api/routes/post_config_writes.py``'s v2
        overrides Command). Callers ``mkdir(parents=True,
        exist_ok=True)`` on the parent before writing.
        """
        return (
            Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
            / ".controller" / "routing-overrides.yaml"
        )

    def get_profile(self) -> dict[str, Any]:
        """Read and return the profile YAML (slim — app config sections stripped).

        Returns both the parsed-and-slimmed ``profile`` dict (for
        section-by-section rendering) AND the raw ``yaml`` text (for
        the read-only excerpt panel + the editor). The UI's
        ``EffectiveProfileCard`` / ``ProfileEditorCard`` both consumed
        ``p.yaml`` against this endpoint and rendered "No profile
        loaded" / an empty textarea when the field was missing —
        symptom seen 2026-05-12 on both compose + k8s.
        """
        profile_data, path = self._profile.load()
        if path is None:
            return {"profile": None, "yaml": "", "error": "Profile not found"}
        ms_id = self._profile.media_server_id()
        strip_keys = STRIPPED_FROM_PROFILE | ({ms_id} if ms_id else set())
        moved = [k for k in (APP_CONFIG_SECTIONS | ({ms_id} if ms_id else set())) if k in profile_data]
        slim = {k: v for k, v in profile_data.items() if k not in strip_keys}
        try:
            raw_yaml = path.read_text(encoding="utf-8")
        except OSError:
            # File disappeared between ``load()`` (which cached the
            # parsed dict) and now. Fall back to re-serialising the
            # parsed dict so the UI still has something to render.
            raw_yaml = yaml.safe_dump(
                profile_data, default_flow_style=False,
                sort_keys=False, allow_unicode=True,
            )
        result: dict[str, Any] = {
            "profile": slim,
            "yaml": raw_yaml,
            "file": str(path),
        }
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
            except Exception as exc:
                log_swallowed(exc)
        overrides_path = self.overrides_path()
        if overrides_path.is_file():
            try:
                overrides = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
                routing.update(overrides.get("routing") or {})
            except Exception as exc:
                log_swallowed(exc)
        return {
            "base_domain": str(routing.get("base_domain", "local")),
            "stack_subdomain": str(routing.get("stack_subdomain", "media-stack")),
            "gateway_host": str(routing.get("gateway_host", "apps.media-stack.local")),
            "gateway_port": int(routing.get("gateway_port", 80)),
            "app_path_prefix": str(routing.get("app_path_prefix", "/app")),
            "strategy": str(routing.get("strategy", "hybrid")),
            "scheme": str(routing.get("scheme") or ""),
            "internet_exposed": bool(routing.get("internet_exposed", False)),
            "direct_hosts": {
                k: str(v) for k, v in (routing.get("direct_hosts") or {}).items()
                if isinstance(v, str) and v
            },
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
            # ``direct_hosts`` is a dict (e.g. ``{"media_server":
            # "jf.example.com"}``) — handled specially below.
            # ``scheme`` lets the operator force ``https`` when the
            # gateway port isn't 443 but the upstream still serves TLS.
            allowed_keys = {"base_domain", "stack_subdomain", "gateway_host", "gateway_port", "app_path_prefix", "strategy", "internet_exposed", "scheme"}
            changed = []
            for key, value in updates.items():
                if key in allowed_keys and str(routing.get(key, "")) != str(value):
                    routing[key] = value
                    changed.append(key)
            # direct_hosts is a dict — merge sub-keys rather than
            # overwriting (so the operator can update one role at a
            # time without losing the others). Empty/None unsets.
            incoming_direct = updates.get("direct_hosts")
            if isinstance(incoming_direct, dict):
                current_direct = dict(routing.get("direct_hosts") or {})
                direct_changed = False
                for role, host in incoming_direct.items():
                    new_val = str(host or "").strip()
                    old_val = str(current_direct.get(role, "")).strip()
                    if new_val != old_val:
                        if new_val:
                            current_direct[role] = new_val
                        else:
                            current_direct.pop(role, None)
                        direct_changed = True
                if direct_changed:
                    routing["direct_hosts"] = current_direct
                    changed.append("direct_hosts")
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
            overrides_path = self.overrides_path()
            overrides_path.parent.mkdir(parents=True, exist_ok=True)
            with open(overrides_path, "w") as f:
                yaml.dump({"routing": routing}, f, default_flow_style=False, sort_keys=False)
            try:
                with open(profile_path, "w") as f:
                    yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            except OSError:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
            if action_trigger:
                # envoy-config rebuilds Envoy's vhosts so the new
                # gateway_host / subdomains route to backends.
                # ingress-config rebuilds the K8s Ingress rules so
                # those hostnames actually reach Envoy in the first
                # place — required on K8s, no-ops elsewhere.
                # (v1.0.162 — without ingress-config the dashboard
                # "Save Routing" silently changed envoy.yaml but
                # left the K8s Ingress on the OLD hostnames, so
                # external requests to the new gateway never reached
                # the cluster.)
                action_trigger("envoy-config", {})
                action_trigger("ingress-config", {})
            return {"status": "updated", "persisted_to": str(overrides_path), "changed": changed, "routing": routing}
        except Exception as exc:
            return {"error": str(exc)[:200]}
