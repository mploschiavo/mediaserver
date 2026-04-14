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
        """Build OIDC identity provider configuration."""
        if self._opts.oidc_provider == "local" or not self._opts.oidc_provider:
            return None

        oidc_cfg = self._opts.oidc_config
        provider = self._opts.oidc_provider

        # Build the OIDC provider entry
        provider_entry: dict[str, Any] = {
            "id": provider,
            "implementation": "generic",
            "client_id": oidc_cfg.get("client_id", ""),
            "client_secret": oidc_cfg.get("client_secret", ""),
            "authorization_endpoint": oidc_cfg.get("authorization_url", ""),
            "token_endpoint": oidc_cfg.get("token_url", ""),
            "userinfo_endpoint": oidc_cfg.get("userinfo_url", ""),
        }

        # Use discovery URL if available
        discovery_url = oidc_cfg.get("discovery_url", "")
        if discovery_url:
            provider_entry["issuer_url"] = discovery_url.replace(
                "/.well-known/openid-configuration", ""
            )

        # Well-known providers get specific implementations
        implementation_map = {
            "google": "google",
            "github": "github",
            "microsoft": "microsoft",
            "auth0": "generic",
            "okta": "generic",
            "keycloak": "generic",
        }
        if provider in implementation_map:
            provider_entry["implementation"] = implementation_map[provider]

        return {
            "identity_providers": {
                "oidc": {
                    "clients": [provider_entry],
                },
            },
        }

    def generate_configuration(self) -> dict[str, Any]:
        """Generate the complete Authelia configuration.yml content."""
        self._ensure_secrets()
        authelia_url = f"https://auth.{self._opts.stack_subdomain}.{self._opts.base_domain}"
        if not self._opts.internet_exposed:
            scheme = "http"
            port_suffix = f":{self._opts.gateway_port}" if self._opts.gateway_port not in (80, 443) else ""
            authelia_url = f"{scheme}://auth.{self._opts.stack_subdomain}.{self._opts.base_domain}{port_suffix}"

        config: dict[str, Any] = {
            "server": {
                "address": "tcp://0.0.0.0:9091",
            },
            "log": {
                "level": "info",
            },
            "theme": "auto",
            "default_redirection_url": f"{'https' if self._opts.internet_exposed else 'http'}://{self._opts.gateway_host}",
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
                        "domain": self._opts.base_domain,
                        "authelia_url": authelia_url,
                        "default_redirection_url": f"{'https' if self._opts.internet_exposed else 'http'}://{self._opts.gateway_host}",
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
        users_path.write_text(
            yaml.dump(
                self.generate_users_database(),
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        written.append(users_path)

        return written
