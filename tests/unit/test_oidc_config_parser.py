"""Tests for OIDC provider JSON config parser.

Verifies auto-detection and field extraction for:
- Google Cloud Console client_secret_*.json
- Auth0 application settings
- Okta app integration / .okta.json
- Microsoft Azure AD app registration
- Keycloak adapter config (keycloak.json)
- GitHub OAuth app
- Generic OIDC with client_id/client_secret
- Unknown / invalid JSON
"""

import unittest

from media_stack.core.auth.oidc_config_parser import parse_oidc_config


class TestGoogleParser(unittest.TestCase):
    """Google Cloud Console client_secret_*.json."""

    def test_web_app(self):
        raw = {
            "web": {
                "client_id": "123-abc.apps.googleusercontent.com",
                "project_id": "my-project",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_secret": "GOCSPX-xyz",
                "redirect_uris": ["https://auth.media.example.com/api/oidc/callback"],
            }
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "google")
        self.assertEqual(result["client_id"], "123-abc.apps.googleusercontent.com")
        self.assertEqual(result["client_secret"], "GOCSPX-xyz")
        self.assertIn("accounts.google.com", result["discovery_url"])

    def test_installed_app(self):
        raw = {
            "installed": {
                "client_id": "456-def.apps.googleusercontent.com",
                "client_secret": "secret123",
            }
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "google")
        self.assertEqual(result["client_id"], "456-def.apps.googleusercontent.com")


class TestAuth0Parser(unittest.TestCase):
    """Auth0 application settings."""

    def test_standard_format(self):
        raw = {
            "client_id": "abc123",
            "client_secret": "secret456",
            "domain": "myapp.auth0.com",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "auth0")
        self.assertEqual(result["client_id"], "abc123")
        self.assertEqual(result["tenant"], "myapp")
        self.assertIn("myapp.auth0.com", result["discovery_url"])

    def test_camel_case_keys(self):
        raw = {
            "clientId": "abc123",
            "clientSecret": "secret456",
            "domain": "myapp.us.auth0.com",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "auth0")
        self.assertEqual(result["client_id"], "abc123")


class TestOktaParser(unittest.TestCase):
    """Okta app integration."""

    def test_direct_format(self):
        raw = {
            "client_id": "0oa123",
            "client_secret": "secret",
            "issuer": "https://myorg.okta.com/oauth2/default",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "okta")
        self.assertEqual(result["client_id"], "0oa123")
        self.assertEqual(result["domain"], "myorg.okta.com")
        self.assertIn("okta.com", result["discovery_url"])

    def test_nested_okta_json(self):
        raw = {
            "okta": {
                "idx": {
                    "issuer": "https://dev-12345.okta.com/oauth2/default",
                    "clientId": "0oa789",
                }
            }
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "okta")
        self.assertEqual(result["client_id"], "0oa789")


class TestMicrosoftParser(unittest.TestCase):
    """Microsoft Azure AD app registration."""

    def test_standard_format(self):
        raw = {
            "clientId": "11111111-2222-3333-4444-555555555555",
            "clientSecret": "secret~abc",
            "tenantId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "microsoft")
        self.assertEqual(result["client_id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(result["tenant_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertIn("login.microsoftonline.com", result["discovery_url"])

    def test_cli_format(self):
        raw = {
            "appId": "11111111-2222-3333-4444-555555555555",
            "password": "secret",
            "tenant": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "microsoft")
        self.assertEqual(result["client_secret"], "secret")


class TestKeycloakParser(unittest.TestCase):
    """Keycloak adapter config (keycloak.json)."""

    def test_standard_format(self):
        raw = {
            "realm": "myrealm",
            "auth-server-url": "https://keycloak.example.com",
            "resource": "my-client",
            "credentials": {"secret": "kc-secret"},
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "keycloak")
        self.assertEqual(result["client_id"], "my-client")
        self.assertEqual(result["client_secret"], "kc-secret")
        self.assertEqual(result["realm"], "myrealm")
        self.assertIn("keycloak.example.com/realms/myrealm", result["discovery_url"])


class TestGitHubParser(unittest.TestCase):
    """GitHub OAuth app."""

    def test_with_auth_url(self):
        raw = {
            "client_id": "gh_abc123",
            "client_secret": "gh_secret",
            "authorization_url": "https://github.com/login/oauth/authorize",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "github")
        self.assertEqual(result["client_id"], "gh_abc123")

    def test_with_github_flag(self):
        raw = {
            "client_id": "gh_abc123",
            "client_secret": "gh_secret",
            "github": True,
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "github")


class TestGenericParser(unittest.TestCase):
    """Generic OIDC with client_id/client_secret."""

    def test_basic_fields(self):
        raw = {
            "client_id": "my-client",
            "client_secret": "my-secret",
            "issuer": "https://idp.example.com",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "custom")
        self.assertEqual(result["client_id"], "my-client")
        self.assertEqual(result["discovery_url"], "https://idp.example.com")

    def test_camel_case(self):
        raw = {
            "clientId": "my-client",
            "clientSecret": "my-secret",
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["provider"], "custom")
        self.assertEqual(result["client_id"], "my-client")


class TestUnknownFormat(unittest.TestCase):
    """Unrecognizable JSON."""

    def test_empty_dict(self):
        result = parse_oidc_config({})
        self.assertEqual(result["provider"], "unknown")
        self.assertIn("error", result)

    def test_no_client_id(self):
        result = parse_oidc_config({"foo": "bar"})
        self.assertEqual(result["provider"], "unknown")

    def test_random_json(self):
        result = parse_oidc_config({"name": "test", "version": 1})
        self.assertEqual(result["provider"], "unknown")


class TestNoSecretLeakage(unittest.TestCase):
    """Parsed results must contain secrets (they're needed for config)
    but the API endpoint strips 'raw' to avoid echoing the full upload.
    """

    def test_raw_is_included_in_parse_result(self):
        raw = {"client_id": "x", "client_secret": "s"}
        result = parse_oidc_config(raw)
        self.assertIn("raw", result)

    def test_secrets_preserved_for_config(self):
        raw = {
            "web": {
                "client_id": "x.apps.googleusercontent.com",
                "client_secret": "GOCSPX-secret",
            }
        }
        result = parse_oidc_config(raw)
        self.assertEqual(result["client_secret"], "GOCSPX-secret")


if __name__ == "__main__":
    unittest.main()
