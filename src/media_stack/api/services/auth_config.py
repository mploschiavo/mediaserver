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
# hoisted from per-method import to reduce CIRCULAR_IMPORT_RISK_RATCHET drift
# (registry and _resolve are leaf utility modules — no cycle with auth_config)
from media_stack.core.service_registry.registry import SERVICES as _SERVICES
from media_stack.api.services import _resolve as _resolve_mod


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
        profile = self._load_profile()
        auth_cfg = profile.get("auth") or {}
        profile_per_service = auth_cfg.get("per_service") or {}

        result: list[dict[str, Any]] = []
        for svc in _SERVICES:
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

        mode_err = self._apply_mode_update(profile, auth, updates, changed)
        if mode_err:
            return mode_err
        self._apply_oidc_and_per_service_updates(auth, updates, changed)

        if not changed:
            return {"status": "no_changes", "auth": auth}

        persist_err = self._persist_auth_changes(profile, profile_path)
        if persist_err:
            return persist_err

        self._trigger_auth_regen(changed, action_trigger)
        return {"status": "updated", "changed": changed, "auth": auth}

    def _apply_mode_update(
        self,
        profile: dict[str, Any],
        auth: dict[str, Any],
        updates: dict[str, Any],
        changed: list[str],
    ) -> dict[str, Any] | None:
        """Apply the ``mode``/``provider`` swap, returning an error dict on bad input.

        Owning this branch outside of ``update_auth_config`` keeps the
        complex SSO/app_auth sync out of the orchestration path.
        """
        if "mode" not in updates:
            return None
        new_mode = str(updates["mode"]).strip().lower()
        modes = self._contract.get_modes()
        if new_mode not in modes:
            return {"error": f"Unknown auth mode: {new_mode}"}
        if auth.get("provider") == new_mode:
            return None
        auth["provider"] = new_mode
        auth["mode"] = new_mode
        auth["enabled"] = new_mode != "none"
        changed.append("mode")
        mode_spec = modes[new_mode]
        self._apply_middleware_default(auth, new_mode, mode_spec)
        self._sync_app_auth_with_mode(profile, new_mode, mode_spec, changed)
        return None

    @staticmethod
    def _apply_middleware_default(
        auth: dict[str, Any], new_mode: str, mode_spec: Any,
    ) -> None:
        """Set ``auth.middleware`` from the provider registry when applicable."""
        if mode_spec.provider_service:
            from media_stack.core.auth.provider_registry import load_builtin_auth_provider_specs
            for spec in load_builtin_auth_provider_specs():
                if spec.key == new_mode:
                    auth["middleware"] = spec.default_middleware
                    break
        else:
            auth.pop("middleware", None)

    @staticmethod
    def _sync_app_auth_with_mode(
        profile: dict[str, Any],
        new_mode: str,
        mode_spec: Any,
        changed: list[str],
    ) -> None:
        """Sync ``app_auth`` on arr services when gateway auth mode changes.

        When SSO is active we trust the reverse proxy and disable built-
        in auth so users don't get double-prompted. When SSO is off we
        re-enable Forms auth so each arr app has its own login page.
        Reusing ``profile`` as the local name here would shadow the
        dict we later write back — see v1.0.165 tests/unit/test_auth_
        mode_round_trip.py for the bug this caused.
        """
        app_auth = profile.setdefault("app_auth", {})
        if mode_spec.gateway_auth:
            try:
                from media_stack.services.profile_config import get_profile_config
                pconfig = get_profile_config()
                app_auth["method"] = pconfig.effective_app_auth_method
            except Exception:
                app_auth["method"] = "External"
            app_auth["required"] = "DisabledForLocalAddresses"
            app_auth["enabled"] = True
            changed.append(f"app_auth.method={app_auth['method']} (SSO proxy)")
        elif new_mode == "none":
            app_auth["enabled"] = False
            app_auth["method"] = "None"
            changed.append("app_auth.disabled (no auth)")
        else:
            # basic mode → keep Forms on arr apps
            app_auth["enabled"] = True
            app_auth["method"] = "Forms"
            app_auth["required"] = "DisabledForLocalAddresses"
            changed.append("app_auth.method=Forms")

    @staticmethod
    def _apply_oidc_and_per_service_updates(
        auth: dict[str, Any],
        updates: dict[str, Any],
        changed: list[str],
    ) -> None:
        """Apply OIDC provider/config and per-service policy diffs in-place."""
        if "oidc_provider" in updates:
            new_oidc = str(updates["oidc_provider"]).strip().lower()
            if auth.get("oidc_provider") != new_oidc:
                auth["oidc_provider"] = new_oidc
                changed.append("oidc_provider")
        if "oidc_config" in updates and isinstance(updates["oidc_config"], dict):
            existing = auth.setdefault("oidc_config", {})
            for k, v in updates["oidc_config"].items():
                if str(v).strip():
                    existing[str(k)] = str(v).strip()
                    changed.append(f"oidc_config.{k}")
        if "per_service" in updates and isinstance(updates["per_service"], dict):
            per_svc = auth.setdefault("per_service", {})
            valid_policies = {"protected", "native", "public"}
            for svc_id, policy in updates["per_service"].items():
                policy_str = str(policy).strip().lower()
                if policy_str in valid_policies and per_svc.get(svc_id) != policy_str:
                    per_svc[svc_id] = policy_str
                    changed.append(f"per_service.{svc_id}")

    @staticmethod
    def _persist_auth_changes(
        profile: dict[str, Any], profile_path: Path,
    ) -> dict[str, Any] | None:
        """Write auth-overrides (authoritative) and best-effort profile YAML.

        The overrides PVC file is the durable store; the profile mount
        is read-only on K8s so its write is allowed to fail. Same dual-
        write pattern the routing-overrides path established in v1.0.160.
        """
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
        return None

    @staticmethod
    def _trigger_auth_regen(
        changed: list[str], action_trigger: Callable | None,
    ) -> None:
        """Fire downstream regen jobs based on which fields changed.

        Without ``configure-auth`` Authelia never gets its config
        rewritten; without ``envoy-config`` the gateway's ext_authz
        filter still points at the old provider. Kept isolated so the
        main update flow doesn't carry the trigger-matrix logic.
        """
        if not action_trigger:
            return
        mode_changed = any(
            c.startswith("mode") or c.startswith("per_service") for c in changed
        )
        app_auth_changed = any(c.startswith("app_auth") for c in changed)
        oidc_changed = any(c.startswith("oidc") for c in changed)
        if mode_changed:
            action_trigger("envoy-config", {})
            action_trigger("configure-auth", {})
        elif oidc_changed:
            # OIDC-only edit (client_id/secret) — Authelia needs a
            # config rewrite but Envoy's wiring is unchanged.
            action_trigger("configure-auth", {})
        if app_auth_changed:
            action_trigger("bootstrap", {})

    def _load_profile(self) -> dict[str, Any]:
        """Load the profile YAML, with dashboard auth-overrides merged
        on top so dashboard "Save Auth" actually wins on K8s.

        Why: on K8s the bootstrap profile is mounted as a read-only
        ConfigMap; dashboard writes to it fail silently. Same pattern
        as the routing-overrides flow — persist edits to a writable
        PVC location and merge them at read time. (v1.0.165 — auth
        side of the same shape v1.0.160 fixed for routing.)"""
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
