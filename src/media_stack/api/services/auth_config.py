"""Auth configuration API service.

Provides read/write access to auth settings for the dashboard:
- Get current auth mode, provider, per-service policies
- Get available auth modes + OIDC providers from contract
- Update auth configuration in the profile
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import yaml

from media_stack.core.logging_utils import log_swallowed

from media_stack.core.auth.gateway_policy import AuthContractService


class AuthConfigService:
    """Manages auth configuration through the API."""

    def __init__(self) -> None:
        self._contract = AuthContractService()

    def get_auth_modes(self) -> list[dict[str, Any]]:
        """Return all available auth modes for the UI dropdown."""
        modes = self._contract.get_modes()
        return [
            {
                "key": spec.key,
                "display_name": spec.display_name,
                "description": spec.description,
                "gateway_auth": spec.gateway_auth,
                "controller_auth": spec.controller_auth,
                "provider_service": spec.provider_service,
            }
            for spec in modes.values()
        ]

    def get_oidc_providers(self) -> list[dict[str, Any]]:
        """Return all known OIDC identity providers for the UI dropdown."""
        providers = self._contract.get_oidc_providers()
        return [
            {
                "key": spec.key,
                "display_name": spec.display_name,
                "description": spec.description,
                "required_fields": list(spec.required_fields),
            }
            for spec in providers.values()
        ]

    def get_service_policies(self) -> list[dict[str, Any]]:
        """Return per-service auth policies (resolved from contract + profile)."""
        from media_stack.api.services.registry import SERVICES

        profile = self._load_profile()
        auth_cfg = profile.get("auth") or {}
        profile_per_service = auth_cfg.get("per_service") or {}

        result: list[dict[str, Any]] = []
        for svc in SERVICES:
            policy = self._contract.resolve_service_policy(
                svc.id, svc.category, profile_per_service
            )
            source = "profile" if svc.id in profile_per_service else "contract"
            result.append({
                "service_id": svc.id,
                "service_name": svc.name,
                "category": svc.category,
                "policy": policy,
                "source": source,
            })

        # Add controller itself
        controller_policy = self._contract.resolve_service_policy(
            "media-stack-controller", "infrastructure", profile_per_service
        )
        result.append({
            "service_id": "media-stack-controller",
            "service_name": "Controller",
            "category": "infrastructure",
            "policy": controller_policy,
            "source": "profile" if "media-stack-controller" in profile_per_service else "contract",
        })

        return result

    def get_current_config(self) -> dict[str, Any]:
        """Return the current auth configuration."""
        profile = self._load_profile()
        auth_cfg = profile.get("auth") or {}
        routing = profile.get("routing") or {}

        mode = auth_cfg.get("provider", auth_cfg.get("mode", "none"))
        modes = self._contract.get_modes()
        mode_spec = modes.get(mode)
        app_auth = profile.get("app_auth") or {}
        app_auth_method = str(app_auth.get("method", "Forms"))

        # Explain the effective auth state to the UI
        if mode_spec and mode_spec.gateway_auth:
            app_auth_summary = "SSO gateway — arr apps trust local network (no second login)"
        elif mode == "basic":
            app_auth_summary = f"Per-app {app_auth_method} auth (username/password on each service)"
        elif mode == "none":
            app_auth_summary = "No authentication"
        else:
            app_auth_summary = f"Per-app {app_auth_method} auth"

        return {
            "mode": mode,
            "internet_exposed": bool(routing.get("internet_exposed", False)),
            "oidc_provider": auth_cfg.get("oidc_provider", "local"),
            "oidc_config": {
                k: v for k, v in (auth_cfg.get("oidc_config") or {}).items()
                if k != "client_secret"  # Don't expose secrets
            },
            "per_service": auth_cfg.get("per_service") or {},
            "app_auth": app_auth,
            "app_auth_method": app_auth_method,
            "app_auth_summary": app_auth_summary,
        }

    def update_auth_config(
        self,
        updates: dict[str, Any],
        action_trigger: Callable | None = None,
    ) -> dict[str, Any]:
        """Update auth configuration in the profile.

        Accepts:
          mode: str — auth mode key (none, basic, authelia, authentik)
          oidc_provider: str — OIDC upstream provider key
          oidc_config: dict — OIDC config (client_id, client_secret, etc.)
          per_service: dict — per-service policy overrides
        """
        from media_stack.api.services import _resolve as _resolve_mod

        resolved = _resolve_mod.resolve_profile_path(
            os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        )
        if not resolved:
            return {"error": "Profile file not found"}

        profile_path = Path(resolved)
        try:
            profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return {"error": f"Failed to read profile: {str(exc)[:120]}"}

        auth = profile.setdefault("auth", {})
        changed: list[str] = []

        # Update mode/provider
        if "mode" in updates:
            new_mode = str(updates["mode"]).strip().lower()
            modes = self._contract.get_modes()
            if new_mode not in modes:
                return {"error": f"Unknown auth mode: {new_mode}"}
            if auth.get("provider") != new_mode:
                auth["provider"] = new_mode
                auth["mode"] = new_mode
                changed.append("mode")

                # Set enabled based on mode
                auth["enabled"] = new_mode != "none"

                # Set middleware default
                mode_spec = modes[new_mode]
                if mode_spec.provider_service:
                    from media_stack.core.auth.provider_registry import load_builtin_auth_provider_specs
                    for spec in load_builtin_auth_provider_specs():
                        if spec.key == new_mode:
                            auth["middleware"] = spec.default_middleware
                            break
                else:
                    auth.pop("middleware", None)

                # Sync app_auth with gateway auth mode:
                # When SSO is active, disable built-in auth on protected arr apps
                # so users don't get double-prompted after SSO login.
                # When SSO is off, re-enable Forms auth on arr apps.
                app_auth = profile.setdefault("app_auth", {})
                if mode_spec.gateway_auth:
                    # SSO active → use ProfileConfig.effective_app_auth_method
                    # which returns External (trust reverse proxy).
                    # CRITICAL: use a distinct variable here; reusing
                    # `profile` would shadow the dict we write below,
                    # leaving a non-serializable ProfileConfig object
                    # as the write target — yaml.dump produces a
                    # Python-tag payload that fails safe_load on
                    # every subsequent read. Caught by the auth-mode
                    # round-trip test in
                    # tests/unit/test_auth_mode_round_trip.py.
                    try:
                        from media_stack.services.profile_config import get_profile_config
                        pconfig = get_profile_config()
                        app_auth["method"] = pconfig.effective_app_auth_method
                    except Exception:
                        app_auth["method"] = "External"
                    app_auth["required"] = "DisabledForLocalAddresses"
                    app_auth["enabled"] = True
                    changed.append(f"app_auth.method={app_auth['method']} (SSO proxy)")
                else:
                    # No SSO → re-enable Forms auth so apps have their own login
                    if new_mode == "none":
                        app_auth["enabled"] = False
                        app_auth["method"] = "None"
                        changed.append("app_auth.disabled (no auth)")
                    else:
                        # basic mode → keep Forms on arr apps
                        app_auth["enabled"] = True
                        app_auth["method"] = "Forms"
                        app_auth["required"] = "DisabledForLocalAddresses"
                        changed.append("app_auth.method=Forms")

        # Update OIDC provider
        if "oidc_provider" in updates:
            new_oidc = str(updates["oidc_provider"]).strip().lower()
            if auth.get("oidc_provider") != new_oidc:
                auth["oidc_provider"] = new_oidc
                changed.append("oidc_provider")

        # Update OIDC config
        if "oidc_config" in updates and isinstance(updates["oidc_config"], dict):
            existing = auth.setdefault("oidc_config", {})
            for k, v in updates["oidc_config"].items():
                if str(v).strip():
                    existing[str(k)] = str(v).strip()
                    changed.append(f"oidc_config.{k}")

        # Update per-service policies
        if "per_service" in updates and isinstance(updates["per_service"], dict):
            per_svc = auth.setdefault("per_service", {})
            valid_policies = {"protected", "native", "public"}
            for svc_id, policy in updates["per_service"].items():
                policy_str = str(policy).strip().lower()
                if policy_str in valid_policies:
                    if per_svc.get(svc_id) != policy_str:
                        per_svc[svc_id] = policy_str
                        changed.append(f"per_service.{svc_id}")

        if not changed:
            return {"status": "no_changes", "auth": auth}

        # Persist — auth-overrides on the writable PVC is the
        # authoritative store; profile YAML is best-effort. On K8s
        # the profile is a read-only ConfigMap mount, so the profile
        # write fails silently and only the overrides file survives
        # restarts. _load_profile() merges them at read time.
        # (v1.0.165 — same dual-write pattern as routing-overrides.)
        override_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        overrides_path = Path(override_root) / ".controller" / "auth-overrides.yaml"
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        overrides_payload = {
            "auth": profile.get("auth") or {},
            "app_auth": profile.get("app_auth") or {},
        }
        try:
            overrides_path.write_text(
                yaml.dump(overrides_payload, default_flow_style=False, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception as exc:
            return {"error": f"Failed to write auth overrides: {str(exc)[:120]}"}
        try:
            profile_path.write_text(
                yaml.dump(profile, default_flow_style=False, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception:
            # Best-effort — read-only mount is expected on K8s. The
            # auth-overrides file above is the durable store.
            pass

        # Trigger downstream regen. Without configure-auth, Authelia
        # never gets its config file rewritten — the stored mode says
        # "authelia" but the container has no user-database entries
        # and can't authenticate anyone. Without envoy-config, the
        # gateway's ext_authz filter points at the old provider.
        if action_trigger:
            mode_changed = any(
                c.startswith("mode") or c.startswith("per_service")
                for c in changed)
            app_auth_changed = any(
                c.startswith("app_auth") for c in changed)
            oidc_changed = any(
                c.startswith("oidc") for c in changed)
            if mode_changed:
                action_trigger("envoy-config", {})
                action_trigger("configure-auth", {})
            elif oidc_changed:
                # OIDC-only edit (client_id/secret) — Authelia needs a
                # config rewrite but Envoy's wiring is unchanged.
                action_trigger("configure-auth", {})
            if app_auth_changed:
                action_trigger("bootstrap", {})

        return {"status": "updated", "changed": changed, "auth": auth}

    def _load_profile(self) -> dict[str, Any]:
        """Load the profile YAML, with dashboard auth-overrides merged
        on top so dashboard "Save Auth" actually wins on K8s.

        Why: on K8s the bootstrap profile is mounted as a read-only
        ConfigMap; dashboard writes to it fail silently. Same pattern
        as the routing-overrides flow — persist edits to a writable
        PVC location and merge them at read time. (v1.0.165 — auth
        side of the same shape v1.0.160 fixed for routing.)"""
        from media_stack.api.services import _resolve as _resolve_mod

        resolved = _resolve_mod.resolve_profile_path(
            os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        )
        profile: dict[str, Any] = {}
        if resolved:
            try:
                profile = yaml.safe_load(Path(resolved).read_text(encoding="utf-8")) or {}
            except Exception:
                profile = {}
        # Merge dashboard auth overrides on top.
        try:
            override_root = os.environ.get("CONFIG_ROOT", "/srv-config")
            ov_path = Path(override_root) / ".controller" / "auth-overrides.yaml"
            if ov_path.is_file():
                ov = yaml.safe_load(ov_path.read_text(encoding="utf-8")) or {}
                if isinstance(ov.get("auth"), dict):
                    profile.setdefault("auth", {}).update(ov["auth"])
                if isinstance(ov.get("app_auth"), dict):
                    profile.setdefault("app_auth", {}).update(ov["app_auth"])
        except Exception as exc:
            # Overrides file may be present-but-malformed after a
            # manual edit or a mid-write crash. Fall back to the
            # profile-only view so auth still works; the malformed
            # file gets caught by the auth-overrides-is-valid-yaml
            # probe and the operator fixes it.
            log_swallowed(exc)
        return profile
