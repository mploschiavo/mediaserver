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
from media_stack.core.auth.authelia_oidc_crypto import OidcClientDef


class ConfigureAuthJob:

    def __init__(self, env: dict[str, str] | None = None) -> None:
        # Sample os.environ once at construction so runtime methods
        # stay off it (class-structure ratchet). Tests pass a fake
        # env dict to point the OIDC-clients loader at a fixture.
        self._env = dict(env) if env is not None else dict(os.environ)

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
        base_domain, stack_subdomain = self._resolve_domain_pair(
            ingress, routing,
        )
        return options_cls(
            base_domain=base_domain,
            stack_subdomain=stack_subdomain,
            gateway_host=str(routing.get("gateway_host") or "apps.media-stack.local"),
            gateway_port=int(routing.get("gateway_port") or 80),
            internet_exposed=bool(profile.get("internet_exposed", False)),
            admin_username=admin_username,
            admin_password_hash=admin_hash,
            admin_email=str(auth_cfg.get("admin_email") or "admin@local"),
            oidc_provider=str(auth_cfg.get("oidc_provider") or "local"),
            oidc_config=dict(auth_cfg.get("oidc_config") or {}),
            oidc_clients=self._build_oidc_clients(profile, auth_cfg),
        )

    def _resolve_domain_pair(
        self, ingress: dict, routing: dict,
    ) -> tuple[str, str]:
        """Derive (base_domain, stack_subdomain) for Authelia's cookie
        scope. Supports two profile layouts:

        Nested (compose default): ``ingress.domain=local``,
        ``ingress.subdomain=media-stack`` → apps served at
        ``<svc>.media-stack.local``, Authelia at
        ``auth.media-stack.local``. Returns ("local", "media-stack").

        Flat (K8s): ``routing.base_domain=iomio.io``,
        ``routing.gateway_host=m.iomio.io`` → apps served at
        ``<svc>.iomio.io``, Authelia at ``auth.iomio.io``. Returns
        ("iomio.io", "") — empty sub signals the flat form to the
        downstream Authelia URL / cookie builders.

        Priority:
          1. ingress.domain explicit → base=ingress.domain,
             sub=ingress.subdomain (may be empty).
          2. routing.base_domain explicit → flat topology: base=that
             domain, sub="".
          3. Last resort: parse gateway_host.
        """
        explicit_base = str(ingress.get("domain") or "").strip()
        explicit_sub = str(ingress.get("subdomain") or "").strip()
        if explicit_base:
            return explicit_base, explicit_sub
        routing_base = str(routing.get("base_domain") or "").strip()
        if routing_base:
            # The compose profile keeps the stack subdomain under
            # ``routing.stack_subdomain`` (alongside ``base_domain``);
            # only the K8s-flat layout omits it. Reading
            # ``ingress.subdomain`` exclusively here used to drop the
            # subdomain on the floor, leaving Authelia with a bare
            # ``cookie_domain="local"`` — which Authelia 4.38 rejects
            # ("must have at least a single period"), crashlooping
            # the whole SSO stack.
            routing_sub = str(routing.get("stack_subdomain") or "").strip()
            return routing_base, routing_sub or explicit_sub
        gateway = str(routing.get("gateway_host") or "").strip().lower()
        if gateway and "." in gateway:
            first, _, rest = gateway.partition(".")
            if rest:
                return rest, first
        return "local", "media-stack"

    def _build_oidc_clients(
        self, profile: dict, auth_cfg: dict,
    ) -> list[Any]:
        """Return Authelia OIDC client registrations. Clients are
        declared in ``contracts/auth/oidc_clients.yaml`` so adding a
        new downstream SSO app doesn't require platform code
        changes. Extras under ``auth.oidc_clients`` are appended for
        profile-specific overrides."""
        clients: list[Any] = [
            OidcClientDef(
                client_id=spec["client_id"],
                client_name=spec.get("client_name", spec["client_id"]),
                client_secret=spec["client_secret"],
                redirect_uris=[self._expand_uri(u, profile)
                               for u in spec.get("redirect_uris", [])],
                scopes=list(spec.get("scopes")
                            or ["openid", "email", "profile", "groups"]),
            )
            for spec in self._load_oidc_client_contract()
        ]
        clients.extend(self._extra_oidc_clients(auth_cfg, OidcClientDef))
        return clients

    def _load_oidc_client_contract(self) -> list[dict]:
        """Load the downstream-OIDC-client registry. The contract
        path is discoverable from the repo root; an env override
        (``AUTH_OIDC_CLIENTS_CONTRACT``) lets tests point at a
        fixture without touching the real file."""
        override = str(self._env.get("AUTH_OIDC_CLIENTS_CONTRACT", "")).strip()
        candidates: list[Path] = []
        if override:
            candidates.append(Path(override))
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "contracts" / "auth" / "oidc_clients.yaml"
            if candidate.is_file():
                candidates.append(candidate)
                break
        candidates.append(Path("/srv-app/contracts/auth/oidc_clients.yaml"))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            rows = data.get("clients") or []
            return [r for r in rows if isinstance(r, dict)
                    and r.get("client_id") and r.get("client_secret")]
        return []

    def _expand_uri(self, template: str, profile: dict) -> str:
        """Replace {base}/{sub}/{gateway} placeholders with values
        from the profile's ingress + routing sections."""
        ingress = profile.get("ingress") or {}
        routing = profile.get("routing") or {}
        base = str(ingress.get("domain") or "local")
        sub = str(ingress.get("subdomain") or "media-stack")
        gateway = str(routing.get("gateway_host") or (
            f"apps.{sub}.{base}" if sub else f"apps.{base}"
        ))
        return (str(template)
                .replace("{base}", base)
                .replace("{sub}", sub)
                .replace("{gateway}", gateway))

    def _extra_oidc_clients(
        self, auth_cfg: dict, client_cls: type[OidcClientDef],
    ) -> list[Any]:
        out: list[Any] = []
        default_scopes = ["openid", "email", "profile", "groups"]
        for extra in (auth_cfg.get("oidc_clients") or []):
            if not isinstance(extra, dict):
                continue
            if not extra.get("client_id") or not extra.get("client_secret"):
                continue
            out.append(client_cls(
                client_id=str(extra["client_id"]),
                client_name=str(extra.get("client_name", extra["client_id"])),
                client_secret=str(extra["client_secret"]),
                redirect_uris=[str(u) for u in
                               (extra.get("redirect_uris") or [])],
                scopes=[str(s) for s in (extra.get("scopes") or [])]
                       or default_scopes,
            ))
        return out


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
