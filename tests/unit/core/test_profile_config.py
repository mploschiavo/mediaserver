"""Tests for ProfileConfig — single source of truth for deployment config.

Validates:
1. Profile with auth section produces cfg dict containing auth
2. Profile with routing section produces cfg dict containing routing
3. SSO active sets effective_app_auth_method to External
4. No SSO sets effective_app_auth_method from profile
5. Missing profile sections get sane defaults
6. to_cfg() output includes all critical sections
7. from_dict handles empty/None/partial data gracefully
8. Platform detection works
9. Scheme auto-detection from gateway_port
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from media_stack.services.profile_config import (
    AppAuthConfig,
    AuthConfig,
    BootstrapConfig,
    ProfileConfig,
    ProfileMetadata,
    RoutingConfig,
    load_profile_config,
)


class TestProfileMetadata(unittest.TestCase):
    def test_defaults(self) -> None:
        m = ProfileMetadata.from_dict(None)
        self.assertEqual(m.platform, "compose")
        self.assertEqual(m.name, "media-stack")

    def test_k8s_platform(self) -> None:
        m = ProfileMetadata.from_dict({"platform": "k8s"})
        self.assertEqual(m.platform, "k8s")

    def test_case_normalization(self) -> None:
        m = ProfileMetadata.from_dict({"platform": "K8S"})
        self.assertEqual(m.platform, "k8s")


class TestAuthConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        a = AuthConfig.from_dict(None)
        self.assertEqual(a.provider, "none")
        self.assertFalse(a.is_sso)
        self.assertFalse(a.enabled)

    def test_authelia(self) -> None:
        a = AuthConfig.from_dict({"provider": "authelia", "mode": "authelia"})
        self.assertEqual(a.provider, "authelia")
        self.assertTrue(a.is_sso)
        self.assertTrue(a.enabled)

    def test_authentik(self) -> None:
        a = AuthConfig.from_dict({"provider": "authentik"})
        self.assertTrue(a.is_sso)

    def test_basic_not_sso(self) -> None:
        a = AuthConfig.from_dict({"provider": "basic"})
        self.assertFalse(a.is_sso)

    def test_per_service_policies(self) -> None:
        a = AuthConfig.from_dict({
            "provider": "authelia",
            "per_service": {"jellyfin": "native", "sonarr": "protected"},
        })
        self.assertEqual(a.per_service["jellyfin"], "native")
        self.assertEqual(a.per_service["sonarr"], "protected")

    def test_provider_fallback_from_mode(self) -> None:
        a = AuthConfig.from_dict({"mode": "authelia"})
        self.assertEqual(a.provider, "authelia")


class TestAppAuthConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        a = AppAuthConfig.from_dict(None)
        self.assertTrue(a.enabled)
        self.assertEqual(a.method, "Forms")

    def test_explicit_external(self) -> None:
        a = AppAuthConfig.from_dict({"method": "External"})
        self.assertEqual(a.method, "External")


class TestRoutingConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        r = RoutingConfig.from_dict(None)
        self.assertEqual(r.gateway_host, "apps.media-stack.local")
        self.assertEqual(r.gateway_port, 80)
        self.assertEqual(r.resolved_scheme, "http")

    def test_https_scheme_from_port(self) -> None:
        r = RoutingConfig.from_dict({"gateway_port": 443})
        self.assertEqual(r.resolved_scheme, "https")

    def test_explicit_scheme(self) -> None:
        r = RoutingConfig.from_dict({"scheme": "https", "gateway_port": 8080})
        self.assertEqual(r.resolved_scheme, "https")

    def test_iomio_config(self) -> None:
        r = RoutingConfig.from_dict({
            "gateway_host": "m.iomio.io",
            "gateway_port": 443,
            "base_domain": "iomio.io",
            "stack_subdomain": "m",
        })
        self.assertEqual(r.gateway_host, "m.iomio.io")
        self.assertEqual(r.base_domain, "iomio.io")
        self.assertEqual(r.resolved_scheme, "https")


class TestProfileConfig(unittest.TestCase):
    def test_from_empty_dict(self) -> None:
        p = ProfileConfig.from_dict({})
        self.assertEqual(p.auth.provider, "none")
        self.assertFalse(p.is_sso_active)
        self.assertEqual(p.platform, "compose")

    def test_sso_active(self) -> None:
        p = ProfileConfig.from_dict({"auth": {"provider": "authelia"}})
        self.assertTrue(p.is_sso_active)

    def test_is_k8s(self) -> None:
        p = ProfileConfig.from_dict({"metadata": {"platform": "k8s"}})
        self.assertTrue(p.is_k8s)
        self.assertEqual(p.platform, "k8s")

    def test_effective_app_auth_external_when_sso(self) -> None:
        """When SSO is active, effective auth method is always External."""
        p = ProfileConfig.from_dict({
            "auth": {"provider": "authelia"},
            "app_auth": {"method": "Forms"},
        })
        self.assertEqual(p.effective_app_auth_method, "External")

    def test_effective_app_auth_forms_when_basic(self) -> None:
        """When auth is basic, Forms is preserved."""
        p = ProfileConfig.from_dict({
            "auth": {"provider": "basic"},
            "app_auth": {"method": "Forms"},
        })
        self.assertEqual(p.effective_app_auth_method, "Forms")

    def test_effective_app_auth_none_when_no_auth(self) -> None:
        """When auth is none, method is None."""
        p = ProfileConfig.from_dict({
            "auth": {"provider": "none"},
        })
        self.assertEqual(p.effective_app_auth_method, "None")

    def test_effective_app_auth_same_on_compose_and_k8s(self) -> None:
        """External auth method is the same on both platforms when SSO active."""
        for platform in ("compose", "k8s"):
            p = ProfileConfig.from_dict({
                "metadata": {"platform": platform},
                "auth": {"provider": "authelia"},
                "app_auth": {"method": "Forms"},
            })
            self.assertEqual(
                p.effective_app_auth_method, "External",
                f"Platform {platform} should use External with SSO",
            )


class TestToCfg(unittest.TestCase):
    """to_cfg() must include ALL critical sections."""

    def test_cfg_includes_auth(self) -> None:
        p = ProfileConfig.from_dict({"auth": {"provider": "authelia"}})
        cfg = p.to_cfg()
        self.assertIn("auth", cfg)
        self.assertEqual(cfg["auth"]["provider"], "authelia")

    def test_cfg_includes_routing(self) -> None:
        p = ProfileConfig.from_dict({
            "routing": {"gateway_host": "m.iomio.io"},
        })
        cfg = p.to_cfg()
        self.assertIn("routing", cfg)
        self.assertEqual(cfg["routing"]["gateway_host"], "m.iomio.io")

    def test_cfg_includes_app_auth_with_sso_override(self) -> None:
        """cfg.app_auth.method is External when SSO active."""
        p = ProfileConfig.from_dict({
            "auth": {"provider": "authelia"},
            "app_auth": {"method": "Forms"},
        })
        cfg = p.to_cfg()
        self.assertEqual(cfg["app_auth"]["method"], "External")

    def test_cfg_includes_technology_bindings(self) -> None:
        p = ProfileConfig.from_dict({
            "technology_bindings": {"media_server": "jellyfin"},
        })
        cfg = p.to_cfg()
        self.assertEqual(cfg["technology_bindings"]["media_server"], "jellyfin")

    def test_cfg_includes_bootstrap_flags(self) -> None:
        p = ProfileConfig.from_dict({
            "bootstrap": {"trigger_indexer_sync": True},
        })
        cfg = p.to_cfg()
        self.assertTrue(cfg["trigger_indexer_sync"])

    def test_cfg_from_empty_profile(self) -> None:
        """Even an empty profile produces a valid cfg dict."""
        p = ProfileConfig.from_dict({})
        cfg = p.to_cfg()
        self.assertIn("auth", cfg)
        self.assertIn("routing", cfg)
        self.assertIn("app_auth", cfg)
        self.assertEqual(cfg["auth"]["provider"], "none")


class TestLoadProfileConfig(unittest.TestCase):
    def test_load_from_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({
                "metadata": {"platform": "k8s", "name": "test"},
                "auth": {"provider": "authelia"},
                "routing": {"gateway_host": "m.iomio.io", "gateway_port": 443},
            }, f)
            f.flush()
            config = load_profile_config(f.name)

        self.assertEqual(config.platform, "k8s")
        self.assertTrue(config.is_sso_active)
        self.assertEqual(config.routing.gateway_host, "m.iomio.io")
        self.assertEqual(config.effective_app_auth_method, "External")

    def test_load_returns_defaults_when_no_file(self) -> None:
        config = load_profile_config("/nonexistent/path.yaml")
        self.assertEqual(config.auth.provider, "none")
        self.assertEqual(config.platform, "compose")

    def test_load_from_real_profile(self) -> None:
        """Load the actual compose profile from the repo."""
        profile_path = (
            Path(__file__).resolve().parents[3]
            / "examples"
            / "bootstrap-profiles"
            / "media-compose-standard.yaml"
        )
        if not profile_path.exists():
            self.skipTest("Profile not found")
        config = load_profile_config(profile_path)
        self.assertIn(config.platform, ("compose", "k8s"))
        self.assertIsNotNone(config.auth.provider)
        # to_cfg() should not crash
        cfg = config.to_cfg()
        self.assertIn("auth", cfg)
        self.assertIn("routing", cfg)


class TestTopLevelSchemaAllowsProfileKeys(unittest.TestCase):
    """The top-level config schema must allow auth and routing keys.

    Root cause regression: config_loader merges auth/routing from the
    profile into the cfg dict, but TopLevelBootstrapConfig rejected
    them as unknown keys, crashing the controller on startup.
    """

    def test_schema_allows_auth(self) -> None:
        import json
        schema_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "contracts"
            / "top_level_config_schema.json"
        )
        schema = json.loads(schema_path.read_text())
        allowed = schema.get("allowed_keys", {})
        self.assertIn("auth", allowed,
            "top_level_config_schema must allow 'auth' key")

    def test_schema_allows_routing(self) -> None:
        import json
        schema_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "media_stack" / "contracts"
            / "top_level_config_schema.json"
        )
        schema = json.loads(schema_path.read_text())
        allowed = schema.get("allowed_keys", {})
        self.assertIn("routing", allowed,
            "top_level_config_schema must allow 'routing' key")


if __name__ == "__main__":
    unittest.main()
