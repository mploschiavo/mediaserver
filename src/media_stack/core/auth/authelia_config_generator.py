"""Generate Authelia configuration dynamically from profile settings.

Produces a complete Authelia configuration.yml that adapts to:
- Profile domain (session cookies, access control rules)
- OIDC upstream provider (Auth0, Okta, Google, etc.)
- Per-service auth policy (protected vs native vs public)
- Gateway hostname and routing strategy
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.auth.gateway_policy import GatewayAuthPolicy


_HTTP_PORT = 80
_HTTPS_PORT = 443
_DEFAULT_PORTS = (_HTTP_PORT, _HTTPS_PORT)


@dataclass
class AutheliaConfigOptions:
    """Options for generating Authelia configuration."""
    base_domain: str = "local"
    stack_subdomain: str = "media-stack"
    gateway_host: str = "apps.media-stack.local"
    gateway_port: int = 80
    internet_exposed: bool = False
    admin_username: str = "admin"
    admin_password_hash: str = ""
    admin_email: str = "admin@local"
    oidc_provider: str = "local"
    oidc_config: dict[str, str] = field(default_factory=dict)
    auth_policy: GatewayAuthPolicy | None = None
    jwt_secret: str = ""
    session_secret: str = ""
    storage_encryption_key: str = ""


class AutheliaConfigGenerator:
    """Generates Authelia configuration files."""

    def __init__(self, options: AutheliaConfigOptions) -> None:
        self._opts = options

    def _ensure_secrets(self) -> None:
        """Generate random secrets if not provided."""
        if not self._opts.jwt_secret:
            self._opts.jwt_secret = secrets.token_hex(32)
        if not self._opts.session_secret:
            self._opts.session_secret = secrets.token_hex(32)
        if not self._opts.storage_encryption_key:
            self._opts.storage_encryption_key = secrets.token_hex(32)

    def _reuse_existing_secrets(self, output_dir: Path) -> None:
        """Pull jwt/session/storage secrets out of an existing
        configuration.yml so a regen doesn't rotate them.

        storage.encryption_key is the critical one: Authelia
        encrypts rows in db.sqlite3 with it, and a rotated key
        makes every row undecryptable. The container then
        crashloops with 'configured encryption key does not
        appear to be valid for this database'. Same failure mode
        for session.secret (signed cookies) and jwt_secret (reset
        tokens) — regenerating them silently invalidates in-flight
        tokens but at least doesn't brick startup.

        Placeholder values seeded from the bootstrap defaults file
        ('PLACEHOLDER_*' or legacy 'change-this-*') are treated as
        unset so the first real regen after a fresh deploy swaps
        them for real random secrets."""
        path = output_dir / "configuration.yml"
        if not path.is_file():
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return
        if not isinstance(data, dict):
            return
        if not self._opts.storage_encryption_key:
            prev = self._real_secret(
                (data.get("storage") or {}).get("encryption_key"))
            if prev:
                self._opts.storage_encryption_key = prev
        if not self._opts.session_secret:
            prev = self._real_secret(
                (data.get("session") or {}).get("secret"))
            if prev:
                self._opts.session_secret = prev
        if not self._opts.jwt_secret:
            prev = self._real_secret(
                ((data.get("identity_validation") or {})
                 .get("reset_password") or {}).get("jwt_secret"))
            if prev:
                self._opts.jwt_secret = prev

    def _real_secret(self, value: Any) -> str:
        """Return value only if it's a real secret — empty string
        for missing, placeholder, or change-me values. Bootstrap
        defaults seed the file with these so Authelia's schema
        check passes; the first regen must replace them."""
        s = str(value or "").strip()
        if not s:
            return ""
        lower = s.lower()
        if lower.startswith("placeholder_") or lower.startswith("change-this-"):
            return ""
        return s

    def _build_access_control(self) -> dict[str, Any]:
        """Build access control rules based on per-service auth policy."""
        rules: list[dict[str, Any]] = []

        # Local network bypass — one_factor only (not bypass) for security
        rules.append({
            "domain": [f"*.{self._opts.base_domain}"],
            "networks": ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"],
            "policy": "one_factor",
        })

        # Native auth services (Jellyfin etc.) — bypass ext_authz entirely
        # These are handled at the Envoy level (no ext_authz filter on route),
        # but we also bypass at Authelia level for defense in depth.
        if self._opts.auth_policy:
            native_services = [
                svc for svc, pol in self._opts.auth_policy.service_policies.items()
                if pol == "native"
            ]
            if native_services:
                # Build domain patterns for native services
                for svc in native_services:
                    # Subdomain pattern
                    rules.append({
                        "domain": [f"{svc}.{self._opts.stack_subdomain}.{self._opts.base_domain}"],
                        "policy": "bypass",
                    })

        # Internet-exposed: require two_factor for external access
        if self._opts.internet_exposed:
            rules.append({
                "domain": [f"*.{self._opts.stack_subdomain}.{self._opts.base_domain}"],
                "policy": "two_factor",
            })
        else:
            rules.append({
                "domain": [f"*.{self._opts.stack_subdomain}.{self._opts.base_domain}"],
                "policy": "one_factor",
            })

        return {
            "default_policy": "deny",
            "rules": rules,
        }

    def _build_oidc_config(self) -> dict[str, Any] | None:
        """OIDC client-app configuration is intentionally omitted here.

        Authelia 4.38 reworked the OIDC client schema — keys like
        ``implementation``, ``authorization_endpoint``, and
        ``token_endpoint`` no longer exist on client entries, and the
        provider block now requires a ``jwks`` section. The legacy
        generator produced a 4.37-era payload that fails strict 4.38
        validation and prevents Authelia from starting at all, which
        takes the whole gateway login flow down.

        Returning None here leaves Authelia running as a file-auth
        identity provider without an OIDC upstream. The downstream
        jellyseerr-OIDC integration needs to be rebuilt against the
        4.38 schema in a follow-up change."""
        return None

    def generate_configuration(self) -> dict[str, Any]:
        """Generate the complete Authelia configuration.yml content."""
        self._ensure_secrets()
        # Use https when the gateway serves TLS (port 443) OR when the
        # deployment is internet-exposed. Authelia 4.38 rejects http
        # URLs in its cookie config, so a local LAN stack with a
        # self-signed cert needs https here too.
        tls_active = self._opts.internet_exposed or self._opts.gateway_port == _HTTPS_PORT
        scheme = "https" if tls_active else "http"
        port_suffix = (
            f":{self._opts.gateway_port}"
            if self._opts.gateway_port and self._opts.gateway_port not in _DEFAULT_PORTS
            else ""
        )
        authelia_url = (
            f"{scheme}://auth.{self._opts.stack_subdomain}."
            f"{self._opts.base_domain}{port_suffix}"
        )
        gateway_url = f"{scheme}://{self._opts.gateway_host}{port_suffix}"
        # Cookie domain must have at least one dot in Authelia 4.38.
        # Callers pass either a bare TLD ("local") + stack_subdomain
        # ("media-stack") or a fully-qualified base ("media-stack.local")
        # whose first label already IS the stack subdomain. Detect and
        # avoid doubling up as "media-stack.media-stack.local".
        base = self._opts.base_domain or "local"
        sub = self._opts.stack_subdomain or ""
        already_qualified = "." in base and (not sub or base.split(".", 1)[0] == sub)
        if already_qualified or not sub:
            cookie_domain = base
        else:
            cookie_domain = f"{sub}.{base}"

        config: dict[str, Any] = {
            "server": {
                "address": "tcp://0.0.0.0:9091",
            },
            "log": {
                "level": "info",
            },
            "theme": "auto",
            # Top-level default_redirection_url was deprecated in 4.38
            # and now fails validation; the per-cookie one below is the
            # replacement.
            "identity_validation": {
                "reset_password": {
                    "jwt_secret": self._opts.jwt_secret,
                },
            },
            "authentication_backend": {
                "file": {
                    "path": "/config/users_database.yml",
                },
            },
            "access_control": self._build_access_control(),
            "session": {
                "secret": self._opts.session_secret,
                "cookies": [
                    {
                        "domain": cookie_domain,
                        "authelia_url": authelia_url,
                        "default_redirection_url": gateway_url,
                    },
                ],
            },
            "regulation": {
                "max_retries": 3,
                "find_time": 120,
                "ban_time": 300,
            },
            "storage": {
                "encryption_key": self._opts.storage_encryption_key,
                "local": {
                    "path": "/config/db.sqlite3",
                },
            },
            "notifier": {
                "filesystem": {
                    "filename": "/config/notification.txt",
                },
            },
        }

        # Add OIDC config if upstream provider is configured
        oidc_config = self._build_oidc_config()
        if oidc_config:
            config.update(oidc_config)

        return config

    def generate_users_database(self) -> dict[str, Any]:
        """Generate the Authelia users_database.yml content."""
        user_entry: dict[str, Any] = {
            "disabled": False,
            "displayname": "Media Stack Admin",
            "email": self._opts.admin_email,
            "groups": ["admins"],
        }
        if self._opts.admin_password_hash:
            user_entry["password"] = self._opts.admin_password_hash

        return {
            "users": {
                self._opts.admin_username: user_entry,
            },
        }

    def write_config(self, output_dir: Path) -> list[Path]:
        """Write Authelia config files to the given directory.

        Returns list of paths written.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        # CRITICAL: reuse any secrets already on disk BEFORE
        # generate_configuration() runs. Without this, every
        # regen rotates storage.encryption_key and Authelia
        # can't decrypt its db.sqlite3 on next start.
        self._reuse_existing_secrets(output_dir)
        written: list[Path] = []

        config_path = output_dir / "configuration.yml"
        config_path.write_text(
            yaml.dump(
                self.generate_configuration(),
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        written.append(config_path)

        users_path = output_dir / "users_database.yml"
        # CRITICAL: merge into the existing users_database instead of
        # overwriting it. The generator only knows about the admin user
        # from the profile; any additional user (created via the
        # dashboard or self-healed by user_write_service when the
        # Authelia row was missing) lives only in this file. A blind
        # overwrite wipes their passwords and locks them out — which
        # was the root cause of the recurring "I reset my password but
        # can't log in" bug.
        existing = self._read_existing_users_database(users_path)
        merged = self._merge_users_database(
            existing, self.generate_users_database())
        users_path.write_text(
            yaml.dump(merged, default_flow_style=False,
                      sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        written.append(users_path)

        return written

    def _read_existing_users_database(self, path: Path) -> dict[str, Any]:
        """Load users_database.yml from disk, returning an empty dict
        on missing/malformed files. The caller merges its new admin
        entry into this — we never drop existing user rows."""
        if not path.is_file():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
            loaded = yaml.safe_load(text) or {}
            if isinstance(loaded, dict):
                return loaded
        except (OSError, yaml.YAMLError):
            pass
        return {}

    def _merge_users_database(self, existing: dict[str, Any],
                              fresh: dict[str, Any]) -> dict[str, Any]:
        """Merge the freshly-generated admin entry INTO existing,
        preserving every other user + never clobbering a password
        that's already set on disk. When the generator has no
        admin_password_hash (the common case), the existing admin
        password is kept as-is so Authelia doesn't silently lose its
        credentials on a routine regen."""
        merged_users: dict[str, Any] = dict(
            (existing.get("users") or {}).items())
        fresh_users = (fresh.get("users") or {})
        for name, fresh_entry in fresh_users.items():
            prev_entry = merged_users.get(name) or {}
            combined = dict(prev_entry)
            for key, value in fresh_entry.items():
                if key == "password" and not value and prev_entry.get("password"):
                    continue  # don't overwrite a real password with empty
                combined[key] = value
            merged_users[name] = combined
        return {"users": merged_users}
