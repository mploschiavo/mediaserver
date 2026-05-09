"""Parse OIDC provider JSON config files into normalized fields.

Each provider's console exports a different JSON format. This module
auto-detects the provider from the JSON structure and extracts the
fields needed for Authelia/Authentik OIDC upstream configuration.

Supported formats:
- Google: client_secret_*.json from Cloud Console
- Auth0: application settings JSON
- Okta: .okta.json or app integration settings
- Microsoft: app registration JSON from Azure portal
- Keycloak: keycloak.json adapter config
- GitHub: OAuth app settings (manual JSON)
- Generic: any JSON with client_id + client_secret
"""

from __future__ import annotations

from typing import Any


class OidcConfigParser:
    """Detect the OIDC provider from a raw JSON config and normalize fields.

    The parsers are tried in order via :meth:`parse`; the first match
    wins. Each ``_parse_<provider>`` instance method returns ``None``
    when its provider-specific shape isn't matched, or a dict with the
    normalized fields when it is.
    """

    def parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse an OIDC provider JSON config and return normalized fields.

        Returns:
            {
                "provider": "google" | "auth0" | "okta" | "microsoft" | "keycloak" | "github" | "custom",
                "client_id": str,
                "client_secret": str,
                ...provider-specific fields...,
                "raw": dict  # original JSON for reference
            }
        """
        for parser in (
            self._parse_google,
            self._parse_auth0,
            self._parse_okta,
            self._parse_microsoft,
            self._parse_keycloak,
            self._parse_github,
            self._parse_generic,
        ):
            result = parser(raw)
            if result:
                result["raw"] = raw
                return result

        return {
            "provider": "unknown",
            "error": "Could not detect provider from JSON structure",
            "raw": raw,
        }

    def _parse_google(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Google Cloud Console: client_secret_*.json

        Structure:
        {"web": {"client_id": "...", "client_secret": "...", "auth_uri": "...", ...}}
        or
        {"installed": {"client_id": "...", "client_secret": "...", ...}}
        """
        inner = raw.get("web") or raw.get("installed")
        if not isinstance(inner, dict):
            return None
        client_id = inner.get("client_id", "")
        if not client_id or ".googleusercontent.com" not in str(client_id):
            return None
        return {
            "provider": "google",
            "client_id": str(client_id),
            "client_secret": str(inner.get("client_secret", "")),
            "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
            "project_id": str(inner.get("project_id", "")),
            "auth_uri": str(inner.get("auth_uri", "")),
            "token_uri": str(inner.get("token_uri", "")),
            "redirect_uris": list(inner.get("redirect_uris") or []),
        }

    def _parse_auth0(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Auth0 application settings export.

        Structure:
        {"client_id": "...", "client_secret": "...", "domain": "myapp.auth0.com", ...}
        or
        {"clientId": "...", "clientSecret": "...", "domain": "...", ...}
        """
        domain = str(raw.get("domain", ""))
        if not domain:
            return None
        if "auth0.com" not in domain and "auth0" not in domain.lower():
            return None
        client_id = str(raw.get("client_id") or raw.get("clientId", ""))
        client_secret = str(raw.get("client_secret") or raw.get("clientSecret", ""))
        if not client_id:
            return None
        tenant = domain.replace(".auth0.com", "").replace(".us.auth0.com", "")
        return {
            "provider": "auth0",
            "client_id": client_id,
            "client_secret": client_secret,
            "tenant": tenant,
            "domain": domain,
            "discovery_url": f"https://{domain}/.well-known/openid-configuration",
        }

    def _parse_okta(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Okta app integration or .okta.json.

        Structure:
        {"client_id": "...", "client_secret": "...", "issuer": "https://myorg.okta.com/oauth2/default"}
        or
        {"okta": {"idx": {"issuer": "...", "clientId": "...", "scopes": [...]}}}
        """
        # Direct format
        issuer = str(raw.get("issuer", ""))
        if issuer and "okta.com" in issuer:
            return {
                "provider": "okta",
                "client_id": str(raw.get("client_id") or raw.get("clientId", "")),
                "client_secret": str(raw.get("client_secret") or raw.get("clientSecret", "")),
                "domain": issuer.split("/oauth2")[0].replace("https://", ""),
                "discovery_url": f"{issuer}/.well-known/openid-configuration",
            }
        # Nested .okta.json format
        okta = raw.get("okta", {})
        idx = okta.get("idx") if isinstance(okta, dict) else None
        if isinstance(idx, dict) and "okta.com" in str(idx.get("issuer", "")):
            issuer = str(idx["issuer"])
            return {
                "provider": "okta",
                "client_id": str(idx.get("clientId", "")),
                "client_secret": str(idx.get("clientSecret", "")),
                "domain": issuer.split("/oauth2")[0].replace("https://", ""),
                "discovery_url": f"{issuer}/.well-known/openid-configuration",
            }
        return None

    def _parse_microsoft(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Microsoft Azure AD app registration.

        Structure:
        {"clientId": "...", "clientSecret": "...", "tenantId": "..."}
        or
        {"appId": "...", "password": "...", "tenant": "..."}
        """
        tenant_id = str(raw.get("tenantId") or raw.get("tenant", ""))
        client_id = str(raw.get("clientId") or raw.get("appId") or raw.get("client_id", ""))
        client_secret = str(raw.get("clientSecret") or raw.get("password") or raw.get("client_secret", ""))

        if not tenant_id or not client_id:
            return None
        # Heuristic: Azure tenant IDs are UUIDs
        if len(tenant_id) < 30 and "onmicrosoft.com" not in tenant_id:
            return None
        return {
            "provider": "microsoft",
            "client_id": client_id,
            "client_secret": client_secret,
            "tenant_id": tenant_id,
            "discovery_url": f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration",
        }

    def _parse_keycloak(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Keycloak adapter config (keycloak.json).

        Structure:
        {"realm": "myrealm", "auth-server-url": "https://...", "resource": "my-client",
         "credentials": {"secret": "..."}}
        """
        realm = raw.get("realm")
        auth_server_url = raw.get("auth-server-url") or raw.get("authServerUrl")
        resource = raw.get("resource")
        if not realm or not auth_server_url:
            return None
        credentials = raw.get("credentials") or {}
        client_secret = str(credentials.get("secret", "")) if isinstance(credentials, dict) else ""
        host = str(auth_server_url).rstrip("/")
        return {
            "provider": "keycloak",
            "client_id": str(resource or ""),
            "client_secret": client_secret,
            "host": host.replace("https://", "").replace("http://", ""),
            "realm": str(realm),
            "discovery_url": f"{host}/realms/{realm}/.well-known/openid-configuration",
        }

    def _parse_github(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """GitHub OAuth app settings.

        No standard export — users manually create JSON:
        {"client_id": "...", "client_secret": "...", "github": true}
        or detected by authorization_url containing github.com
        """
        auth_url = str(raw.get("authorization_url") or raw.get("authorize_url", ""))
        if "github.com" in auth_url or raw.get("github"):
            return {
                "provider": "github",
                "client_id": str(raw.get("client_id") or raw.get("clientId", "")),
                "client_secret": str(raw.get("client_secret") or raw.get("clientSecret", "")),
            }
        return None

    def _parse_generic(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Generic OIDC — any JSON with client_id/client_secret.

        Falls back to this when no specific provider is detected.
        """
        client_id = str(raw.get("client_id") or raw.get("clientId", ""))
        client_secret = str(raw.get("client_secret") or raw.get("clientSecret", ""))
        if not client_id:
            return None
        result: dict[str, Any] = {
            "provider": "custom",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        # Pull any discovery/issuer URL
        for key in ("discovery_url", "issuer", "issuerUrl", "openid_configuration"):
            val = raw.get(key)
            if val:
                result["discovery_url"] = str(val)
                break
        return result


_PARSER = OidcConfigParser()

# Public API alias — preserves the existing import surface used by
# ``api/routes/post_auth_session.py`` and the unit tests.
parse_oidc_config = _PARSER.parse
