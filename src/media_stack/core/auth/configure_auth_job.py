"""Configure auth job — render Authelia config when the auth profile is active.

Registered in contracts/services/authelia.yaml as:
  configure-auth:
    handler: media_stack.core.auth.configure_auth_job:configure_auth
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import media_stack.services.runtime_platform as runtime_platform


class ConfigureAuthJob:

    @staticmethod
    def _resolve_output_dir(ctx: Any) -> Path:
        config_root = Path(ctx.config_root)
        return config_root / "authelia"

    def configure_auth(self, ctx: Any) -> dict[str, Any]:
        """Generate Authelia configuration from profile settings."""
        profile = ctx.profile or {}
        auth_cfg = profile.get("auth") or {}
        provider = str(auth_cfg.get("provider", "") or "").strip().lower()
        if provider not in {"authelia", "authelia+oidc"}:
            return {"skipped": f"auth provider is '{provider or 'none'}' — Authelia config not needed"}

        try:
            from media_stack.core.auth.authelia_config_generator import (
                AutheliaConfigGenerator,
                AutheliaConfigOptions,
            )
        except ImportError as exc:
            return {"error": f"authelia config generator import failed: {exc}"[:200]}

        ingress = profile.get("ingress") or {}
        routing = profile.get("routing") or {}
        options = AutheliaConfigOptions(
            base_domain=str(ingress.get("domain") or "local"),
            stack_subdomain=str(ingress.get("subdomain") or "media-stack"),
            gateway_host=str(routing.get("gateway_host") or "apps.media-stack.local"),
            gateway_port=int(routing.get("gateway_port") or 80),
            internet_exposed=bool(profile.get("internet_exposed", False)),
            admin_username=ctx.admin_username,
            admin_password_hash=str(auth_cfg.get("admin_password_hash") or ""),
            admin_email=str(auth_cfg.get("admin_email") or "admin@local"),
            oidc_provider=str(auth_cfg.get("oidc_provider") or "local"),
            oidc_config=dict(auth_cfg.get("oidc_config") or {}),
        )

        output_dir = _resolve_output_dir(ctx)
        try:
            written = AutheliaConfigGenerator(options).write_config(output_dir)
        except Exception as exc:
            runtime_platform.log(f"[WARN] configure-auth: {exc}")
            return {"error": str(exc)[:200]}

        rel_paths = [str(p.relative_to(Path(ctx.config_root))) for p in written]
        runtime_platform.log(f"[OK] Authelia config written: {', '.join(rel_paths)}")
        return {"written": rel_paths, "provider": provider}


_instance = ConfigureAuthJob()
configure_auth = _instance.configure_auth
_resolve_output_dir = _instance._resolve_output_dir
