"""Configure auth job — render Authelia config when the auth profile is active.

Registered in contracts/services/authelia.yaml as:
  configure-auth:
    handler: media_stack.core.auth.configure_auth_job:configure_auth
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from argon2 import PasswordHasher

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

        output_dir = self._resolve_output_dir(ctx)
        options = self._build_options(
            ctx, profile, auth_cfg, output_dir, AutheliaConfigOptions,
        )
        try:
            written = AutheliaConfigGenerator(options).write_config(output_dir)
        except Exception as exc:
            runtime_platform.log(f"[WARN] configure-auth: {exc}")
            return {"error": str(exc)[:200]}

        rel_paths = [str(p.relative_to(Path(ctx.config_root))) for p in written]
        runtime_platform.log(f"[OK] Authelia config written: {', '.join(rel_paths)}")
        return {"written": rel_paths, "provider": provider}

    def _build_options(
        self, ctx: Any, profile: dict, auth_cfg: dict,
        output_dir: Path, options_cls: Any,
    ) -> Any:
        """Assemble AutheliaConfigOptions for this deploy.

        The admin password is seed-only from STACK_ADMIN_PASSWORD:
        applied on first deploy but never overwriting a dashboard-
        reset password on a subsequent regen."""
        ingress = profile.get("ingress") or {}
        routing = profile.get("routing") or {}
        admin_username = ctx.admin_username
        existing_admin_pw = self._read_existing_admin_password(
            output_dir, admin_username,
        )
        admin_hash = self._resolve_admin_hash(auth_cfg, existing_admin_pw)
        return options_cls(
            base_domain=str(ingress.get("domain") or "local"),
            stack_subdomain=str(ingress.get("subdomain") or "media-stack"),
            gateway_host=str(routing.get("gateway_host") or "apps.media-stack.local"),
            gateway_port=int(routing.get("gateway_port") or 80),
            internet_exposed=bool(profile.get("internet_exposed", False)),
            admin_username=admin_username,
            admin_password_hash=admin_hash,
            admin_email=str(auth_cfg.get("admin_email") or "admin@local"),
            oidc_provider=str(auth_cfg.get("oidc_provider") or "local"),
            oidc_config=dict(auth_cfg.get("oidc_config") or {}),
        )


    def _read_existing_admin_password(
        self, output_dir: Path, admin_username: str,
    ) -> str:
        """Return the current on-disk password for admin, or empty
        string if the file is missing/unreadable/has no password set.

        Used to decide whether the env-derived STACK_ADMIN_PASSWORD
        should seed a fresh install or stay out of the way."""
        path = output_dir / "users_database.yml"
        if not path.is_file():
            return ""
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return ""
        if not isinstance(loaded, dict):
            return ""
        entry = (loaded.get("users") or {}).get(admin_username) or {}
        if not isinstance(entry, dict):
            return ""
        return str(entry.get("password") or "").strip()

    def _resolve_admin_hash(
        self, auth_cfg: dict, existing_admin_pw: str = "",
    ) -> str:
        """Hash priority (first hit wins):
          1. profile.auth.admin_password_hash (explicit override)
          2. STACK_ADMIN_PASSWORD env, but only if admin has no
             password on disk yet — env is a first-boot seed, not a
             steady-state source of truth
          3. empty string (preserve whatever's already in
             users_database.yml via the merge path)

        (2) is intentionally seed-only: once the admin resets their
        password through the dashboard, the new hash lives only on
        disk. A routine regen (triggered by any routing/auth edit)
        would re-hash the unchanged env var and overwrite the
        dashboard-set password if this check weren't here."""
        explicit = str(auth_cfg.get("admin_password_hash") or "").strip()
        if explicit:
            return explicit
        if existing_admin_pw:
            return ""
        password = os.getenv("STACK_ADMIN_PASSWORD", "").strip()
        if not password:
            return ""
        try:
            return PasswordHasher().hash(password)
        except Exception as exc:  # noqa: BLE001
            runtime_platform.log(
                f"[WARN] configure-auth: couldn't hash "
                f"STACK_ADMIN_PASSWORD: {exc}",
            )
            return ""


_instance = ConfigureAuthJob()
configure_auth = _instance.configure_auth
_resolve_output_dir = _instance._resolve_output_dir
