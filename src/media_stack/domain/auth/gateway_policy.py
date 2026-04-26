"""Gateway auth policy — resolves per-service ext_authz decisions.

Reads contracts/auth.yaml and the profile auth section to determine:
1. Whether gateway auth (ext_authz) is active
2. Which provider to use (authelia / authentik)
3. Per-service policy: protected | native | public
4. OIDC upstream IdP configuration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Resolve auth contract path: repo-relative first, then container mount fallback.
_CONTRACT_PATH_REPO = Path(__file__).resolve().parents[4] / "contracts" / "auth.yaml"
_CONTRACT_PATH_CONTAINER = Path("/contracts/auth.yaml")
_CONTRACT_PATH = _CONTRACT_PATH_REPO if _CONTRACT_PATH_REPO.exists() else _CONTRACT_PATH_CONTAINER


@dataclass(frozen=True)
class ExtAuthzConfig:
    """Envoy ext_authz HTTP service configuration for a provider."""
    cluster_name: str
    host: str
    port: int
    path_prefix: str
    response_headers_to_add: tuple[str, ...]


@dataclass(frozen=True)
class AuthModeSpec:
    """Specification for a single auth mode."""
    key: str
    display_name: str
    description: str
    gateway_auth: bool
    controller_auth: str
    provider_service: str = ""
    ext_authz: ExtAuthzConfig | None = None


@dataclass(frozen=True)
class OidcProviderSpec:
    """Specification for an OIDC identity provider."""
    key: str
    display_name: str
    description: str
    discovery_url: str = ""
    discovery_url_template: str = ""
    authorization_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    required_fields: tuple[str, ...] = ()


@dataclass
class GatewayAuthPolicy:
    """Resolved gateway auth policy for the stack."""
    mode: str = "none"
    ext_authz: ExtAuthzConfig | None = None
    service_policies: dict[str, str] = field(default_factory=dict)
    oidc_provider: str = "local"
    oidc_config: dict[str, str] = field(default_factory=dict)


class AuthContractService:
    """Loads auth contract and resolves gateway auth policy."""

    def __init__(self, contract_path: Path | None = None) -> None:
        self._contract_path = contract_path or _CONTRACT_PATH
        self._contract: dict[str, Any] | None = None

    def _load_contract(self) -> dict[str, Any]:
        if self._contract is not None:
            return self._contract
        if not self._contract_path.exists():
            self._contract = {}
            return self._contract
        self._contract = yaml.safe_load(
            self._contract_path.read_text(encoding="utf-8")
        ) or {}
        return self._contract

    def get_modes(self) -> dict[str, AuthModeSpec]:
        """Return all supported auth modes."""
        contract = self._load_contract()
        raw_modes = contract.get("modes") or {}
        result: dict[str, AuthModeSpec] = {}
        for key, cfg in raw_modes.items():
            if not isinstance(cfg, dict):
                continue
            ext_authz_raw = cfg.get("ext_authz")
            ext_authz = None
            if isinstance(ext_authz_raw, dict):
                ext_authz = ExtAuthzConfig(
                    cluster_name=str(ext_authz_raw.get("cluster_name", "")),
                    host=str(ext_authz_raw.get("host", "")),
                    port=int(ext_authz_raw.get("port", 0)),
                    path_prefix=str(ext_authz_raw.get("path_prefix", "")),
                    response_headers_to_add=tuple(
                        str(h) for h in (ext_authz_raw.get("response_headers_to_add") or [])
                    ),
                )
            result[key] = AuthModeSpec(
                key=key,
                display_name=str(cfg.get("display_name", key)),
                description=str(cfg.get("description", "")),
                gateway_auth=bool(cfg.get("gateway_auth", False)),
                controller_auth=str(cfg.get("controller_auth", "none")),
                provider_service=str(cfg.get("provider_service", "")),
                ext_authz=ext_authz,
            )
        return result

    def get_oidc_providers(self) -> dict[str, OidcProviderSpec]:
        """Return all known OIDC identity providers."""
        contract = self._load_contract()
        raw = contract.get("oidc_providers") or {}
        result: dict[str, OidcProviderSpec] = {}
        for key, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            result[key] = OidcProviderSpec(
                key=key,
                display_name=str(cfg.get("display_name", key)),
                description=str(cfg.get("description", "")),
                discovery_url=str(cfg.get("discovery_url", "")),
                discovery_url_template=str(cfg.get("discovery_url_template", "")),
                authorization_url=str(cfg.get("authorization_url", "")),
                token_url=str(cfg.get("token_url", "")),
                userinfo_url=str(cfg.get("userinfo_url", "")),
                required_fields=tuple(
                    str(f) for f in (cfg.get("required_fields") or [])
                ),
            )
        return result

    def get_category_defaults(self) -> dict[str, str]:
        """Return default auth policy per service category."""
        contract = self._load_contract()
        raw = contract.get("category_defaults") or {}
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}

    def get_service_overrides(self) -> dict[str, str]:
        """Return explicit per-service auth policy overrides from contract."""
        contract = self._load_contract()
        raw = contract.get("service_overrides") or {}
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}

    def resolve_service_policy(
        self,
        service_id: str,
        service_category: str = "",
        profile_per_service: dict[str, str] | None = None,
    ) -> str:
        """Resolve the auth policy for a specific service.

        Priority: profile per_service > contract service_overrides > category_defaults > 'protected'
        """
        # 1. User's profile-level per-service override (highest priority)
        if profile_per_service and service_id in profile_per_service:
            return str(profile_per_service[service_id])

        # 2. Contract-level service overrides
        overrides = self.get_service_overrides()
        if service_id in overrides:
            return overrides[service_id]

        # 3. Category defaults
        if service_category:
            cat_defaults = self.get_category_defaults()
            if service_category in cat_defaults:
                return cat_defaults[service_category]

        # 4. Fallback: protected
        return "protected"

    def resolve_policy(
        self,
        profile_auth: dict[str, Any],
        services: list[tuple[str, str]] | None = None,
    ) -> GatewayAuthPolicy:
        """Resolve the full gateway auth policy from profile auth section.

        Args:
            profile_auth: The `auth:` section from the profile YAML.
            services: List of (service_id, category) tuples for per-service resolution.
        """
        mode_key = str(profile_auth.get("mode", "") or profile_auth.get("provider", "") or "none").strip().lower()
        modes = self.get_modes()
        mode_spec = modes.get(mode_key)

        if not mode_spec or not mode_spec.gateway_auth:
            return GatewayAuthPolicy(mode=mode_key)

        # Resolve per-service policies
        profile_per_service = {}
        raw_ps = profile_auth.get("per_service") or {}
        if isinstance(raw_ps, dict):
            profile_per_service = {str(k): str(v) for k, v in raw_ps.items()}

        service_policies: dict[str, str] = {}
        for svc_id, svc_cat in (services or []):
            service_policies[svc_id] = self.resolve_service_policy(
                svc_id, svc_cat, profile_per_service
            )

        # OIDC provider config
        oidc_provider = str(profile_auth.get("oidc_provider", "local"))
        oidc_config = {}
        raw_oidc = profile_auth.get("oidc_config") or {}
        if isinstance(raw_oidc, dict):
            oidc_config = {str(k): str(v) for k, v in raw_oidc.items() if v}

        return GatewayAuthPolicy(
            mode=mode_key,
            ext_authz=mode_spec.ext_authz,
            service_policies=service_policies,
            oidc_provider=oidc_provider,
            oidc_config=oidc_config,
        )
