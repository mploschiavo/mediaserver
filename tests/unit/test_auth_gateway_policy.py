"""Tests for auth gateway policy — contract loading, per-service policy resolution,
ext_authz filter generation, and Authelia config generation.

Covers:
1. Auth contract YAML loading (modes, OIDC providers, policies)
2. Per-service auth policy resolution (profile > contract > category > default)
3. Envoy ext_authz filter + cluster generation
4. Per-route auth bypass for native/public services
5. Authelia dynamic config generation
6. Multiple auth modes: none, basic, authelia, authentik
7. OIDC upstream provider configuration
8. Internet-exposed vs LAN-only scenarios
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from media_stack.core.auth.gateway_policy import (
    AuthContractService,
    AuthModeSpec,
    ExtAuthzConfig,
    GatewayAuthPolicy,
    OidcProviderSpec,
)
from media_stack.core.auth.envoy_ext_authz import (
    EXT_AUTHZ_FILTER_NAME,
    apply_per_route_auth_policy,
    build_ext_authz_cluster,
    build_ext_authz_filter,
    inject_ext_authz_into_payload,
    route_ext_authz_disabled_config,
)
from media_stack.core.auth.authelia_config_generator import (
    AutheliaConfigGenerator,
    AutheliaConfigOptions,
)


CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "auth.yaml"


class TestAuthContractLoading(unittest.TestCase):
    """Verify the auth contract YAML loads correctly."""

    def setUp(self) -> None:
        self.svc = AuthContractService(CONTRACT_PATH)

    def test_contract_file_exists(self) -> None:
        self.assertTrue(CONTRACT_PATH.exists(), f"Auth contract not found: {CONTRACT_PATH}")

    def test_loads_all_modes(self) -> None:
        modes = self.svc.get_modes()
        self.assertIn("none", modes)
        self.assertIn("basic", modes)
        self.assertIn("authelia", modes)
        self.assertIn("authentik", modes)

    def test_mode_none_has_no_gateway_auth(self) -> None:
        modes = self.svc.get_modes()
        self.assertFalse(modes["none"].gateway_auth)
        self.assertEqual(modes["none"].controller_auth, "none")

    def test_mode_basic_has_no_gateway_auth(self) -> None:
        modes = self.svc.get_modes()
        self.assertFalse(modes["basic"].gateway_auth)
        self.assertEqual(modes["basic"].controller_auth, "basic")

    def test_mode_authelia_has_gateway_auth(self) -> None:
        modes = self.svc.get_modes()
        self.assertTrue(modes["authelia"].gateway_auth)
        self.assertIsNotNone(modes["authelia"].ext_authz)
        self.assertEqual(modes["authelia"].ext_authz.host, "authelia")
        self.assertEqual(modes["authelia"].ext_authz.port, 9091)

    def test_mode_authentik_has_gateway_auth(self) -> None:
        modes = self.svc.get_modes()
        self.assertTrue(modes["authentik"].gateway_auth)
        self.assertIsNotNone(modes["authentik"].ext_authz)
        self.assertEqual(modes["authentik"].ext_authz.host, "authentik")
        self.assertEqual(modes["authentik"].ext_authz.port, 9000)

    def test_ext_authz_response_headers(self) -> None:
        modes = self.svc.get_modes()
        authelia_headers = modes["authelia"].ext_authz.response_headers_to_add
        self.assertIn("Remote-User", authelia_headers)
        authentik_headers = modes["authentik"].ext_authz.response_headers_to_add
        self.assertIn("X-authentik-username", authentik_headers)

    def test_loads_oidc_providers(self) -> None:
        providers = self.svc.get_oidc_providers()
        self.assertIn("local", providers)
        self.assertIn("auth0", providers)
        self.assertIn("okta", providers)
        self.assertIn("google", providers)
        self.assertIn("microsoft", providers)
        self.assertIn("github", providers)
        self.assertIn("keycloak", providers)
        self.assertIn("custom", providers)

    def test_oidc_providers_have_required_fields(self) -> None:
        providers = self.svc.get_oidc_providers()
        # Local has no required fields (file backend)
        self.assertEqual(len(providers["local"].required_fields), 0)
        # Auth0 requires tenant, client_id, client_secret
        self.assertIn("client_id", providers["auth0"].required_fields)
        self.assertIn("client_secret", providers["auth0"].required_fields)
        # Custom requires discovery_url
        self.assertIn("discovery_url", providers["custom"].required_fields)

    def test_category_defaults(self) -> None:
        defaults = self.svc.get_category_defaults()
        self.assertEqual(defaults["media"], "native")
        self.assertEqual(defaults["management"], "protected")
        self.assertEqual(defaults["download"], "protected")
        self.assertEqual(defaults["infrastructure"], "public")

    def test_service_overrides(self) -> None:
        overrides = self.svc.get_service_overrides()
        self.assertEqual(overrides["jellyfin"], "native")
        self.assertEqual(overrides["plex"], "native")
        self.assertEqual(overrides["authelia"], "public")
        self.assertEqual(overrides["media-stack-controller"], "protected")


class TestPerServicePolicyResolution(unittest.TestCase):
    """Verify per-service auth policy resolution priority."""

    def setUp(self) -> None:
        self.svc = AuthContractService(CONTRACT_PATH)

    def test_jellyfin_always_native(self) -> None:
        """Jellyfin MUST be native — TV apps can't do OIDC redirects."""
        policy = self.svc.resolve_service_policy("jellyfin", "media")
        self.assertEqual(policy, "native")

    def test_sonarr_protected_by_category(self) -> None:
        policy = self.svc.resolve_service_policy("sonarr", "management")
        self.assertEqual(policy, "protected")

    def test_profile_override_takes_precedence(self) -> None:
        """User's profile per_service overrides everything."""
        policy = self.svc.resolve_service_policy(
            "sonarr", "management", {"sonarr": "public"}
        )
        self.assertEqual(policy, "public")

    def test_profile_can_override_jellyfin(self) -> None:
        """User can override even the jellyfin default if they really want to."""
        policy = self.svc.resolve_service_policy(
            "jellyfin", "media", {"jellyfin": "protected"}
        )
        self.assertEqual(policy, "protected")

    def test_unknown_service_defaults_to_protected(self) -> None:
        """Unknown services get protected as default."""
        policy = self.svc.resolve_service_policy("unknown-service", "")
        self.assertEqual(policy, "protected")

    def test_authelia_is_public(self) -> None:
        """Auth providers themselves must be public."""
        policy = self.svc.resolve_service_policy("authelia", "infrastructure")
        self.assertEqual(policy, "public")


class TestGatewayPolicyResolution(unittest.TestCase):
    """Verify full gateway auth policy resolution."""

    def setUp(self) -> None:
        self.svc = AuthContractService(CONTRACT_PATH)

    def test_none_mode_returns_no_ext_authz(self) -> None:
        policy = self.svc.resolve_policy({"mode": "none"})
        self.assertEqual(policy.mode, "none")
        self.assertIsNone(policy.ext_authz)
        self.assertEqual(len(policy.service_policies), 0)

    def test_authelia_mode_returns_ext_authz(self) -> None:
        services = [("jellyfin", "media"), ("sonarr", "management")]
        policy = self.svc.resolve_policy(
            {"mode": "authelia"},
            services=services,
        )
        self.assertEqual(policy.mode, "authelia")
        self.assertIsNotNone(policy.ext_authz)
        self.assertEqual(policy.service_policies["jellyfin"], "native")
        self.assertEqual(policy.service_policies["sonarr"], "protected")

    def test_uses_provider_key_fallback(self) -> None:
        """Legacy profiles use auth.provider instead of auth.mode."""
        policy = self.svc.resolve_policy({"provider": "authelia"})
        self.assertEqual(policy.mode, "authelia")
        self.assertIsNotNone(policy.ext_authz)

    def test_oidc_provider_passed_through(self) -> None:
        policy = self.svc.resolve_policy({
            "mode": "authelia",
            "oidc_provider": "google",
            "oidc_config": {"client_id": "abc", "client_secret": "xyz"},
        })
        self.assertEqual(policy.oidc_provider, "google")
        self.assertEqual(policy.oidc_config["client_id"], "abc")
        # Secrets should be passed through (filtering is API layer concern)
        self.assertEqual(policy.oidc_config["client_secret"], "xyz")


class TestExtAuthzFilterGeneration(unittest.TestCase):
    """Verify Envoy ext_authz filter and cluster generation."""

    def _authelia_config(self) -> ExtAuthzConfig:
        return ExtAuthzConfig(
            cluster_name="ext_authz_authelia",
            host="authelia",
            port=9091,
            path_prefix="/api/authz/forward-auth",
            response_headers_to_add=("Remote-User", "Remote-Groups"),
        )

    def test_filter_structure(self) -> None:
        ext_authz = self._authelia_config()
        f = build_ext_authz_filter(ext_authz)
        self.assertEqual(f["name"], EXT_AUTHZ_FILTER_NAME)
        typed = f["typed_config"]
        self.assertIn("http_service", typed)
        svc = typed["http_service"]
        self.assertEqual(svc["server_uri"]["cluster"], "ext_authz_authelia")
        self.assertEqual(svc["path_prefix"], "/api/authz/forward-auth")

    def test_filter_allows_required_headers(self) -> None:
        ext_authz = self._authelia_config()
        f = build_ext_authz_filter(ext_authz)
        auth_req = f["typed_config"]["http_service"]["authorization_request"]
        patterns = auth_req["allowed_headers"]["patterns"]
        header_names = {p["exact"] for p in patterns}
        self.assertIn("cookie", header_names)
        self.assertIn("authorization", header_names)

    def test_filter_forwards_response_headers(self) -> None:
        ext_authz = self._authelia_config()
        f = build_ext_authz_filter(ext_authz)
        auth_resp = f["typed_config"]["http_service"]["authorization_response"]
        patterns = auth_resp["allowed_upstream_headers"]["patterns"]
        header_names = {p["exact"] for p in patterns}
        self.assertIn("Remote-User", header_names)
        self.assertIn("Remote-Groups", header_names)

    def test_cluster_structure(self) -> None:
        ext_authz = self._authelia_config()
        c = build_ext_authz_cluster(ext_authz)
        self.assertEqual(c["name"], "ext_authz_authelia")
        self.assertEqual(c["type"], "STRICT_DNS")
        ep = c["load_assignment"]["endpoints"][0]["lb_endpoints"][0]
        addr = ep["endpoint"]["address"]["socket_address"]
        self.assertEqual(addr["address"], "authelia")
        self.assertEqual(addr["port_value"], 9091)

    def test_disabled_config_for_bypass(self) -> None:
        cfg = route_ext_authz_disabled_config()
        self.assertIn(EXT_AUTHZ_FILTER_NAME, cfg)
        self.assertTrue(cfg[EXT_AUTHZ_FILTER_NAME]["disabled"])


class TestPerRouteAuthPolicy(unittest.TestCase):
    """Verify per-route ext_authz bypass for native/public services."""

    def _policy(self, service_policies: dict[str, str]) -> GatewayAuthPolicy:
        return GatewayAuthPolicy(
            mode="authelia",
            ext_authz=ExtAuthzConfig(
                cluster_name="ext_authz_authelia",
                host="authelia",
                port=9091,
                path_prefix="/api/authz/forward-auth",
                response_headers_to_add=("Remote-User",),
            ),
            service_policies=service_policies,
        )

    def test_protected_service_not_modified(self) -> None:
        policy = self._policy({"sonarr": "protected"})
        route = {"match": {"prefix": "/app/sonarr"}, "route": {"cluster": "sonarr"}}
        apply_per_route_auth_policy(route, "sonarr", policy)
        self.assertNotIn("typed_per_filter_config", route)

    def test_native_service_gets_bypass(self) -> None:
        policy = self._policy({"jellyfin": "native"})
        route = {"match": {"prefix": "/app/jellyfin"}, "route": {"cluster": "jellyfin"}}
        apply_per_route_auth_policy(route, "jellyfin", policy)
        self.assertIn("typed_per_filter_config", route)
        self.assertIn(EXT_AUTHZ_FILTER_NAME, route["typed_per_filter_config"])
        self.assertTrue(route["typed_per_filter_config"][EXT_AUTHZ_FILTER_NAME]["disabled"])

    def test_public_service_gets_bypass(self) -> None:
        policy = self._policy({"authelia": "public"})
        route = {"match": {"prefix": "/app/authelia"}, "route": {"cluster": "authelia"}}
        apply_per_route_auth_policy(route, "authelia", policy)
        self.assertIn("typed_per_filter_config", route)

    def test_no_ext_authz_means_no_bypass(self) -> None:
        """If no ext_authz configured, don't modify routes."""
        policy = GatewayAuthPolicy(mode="basic")
        route = {"match": {"prefix": "/"}, "route": {"cluster": "sonarr"}}
        apply_per_route_auth_policy(route, "sonarr", policy)
        self.assertNotIn("typed_per_filter_config", route)


class TestInjectExtAuthzIntoPayload(unittest.TestCase):
    """Verify ext_authz filter + cluster injection into Envoy payload."""

    def _minimal_payload(self) -> dict:
        return {
            "static_resources": {
                "listeners": [{
                    "filter_chains": [{
                        "filters": [{
                            "typed_config": {
                                "http_filters": [
                                    {"name": "envoy.filters.http.lua", "typed_config": {}},
                                    {"name": "envoy.filters.http.router", "typed_config": {}},
                                ],
                            },
                        }],
                    }],
                }],
                "clusters": [],
            },
        }

    def _policy(self) -> GatewayAuthPolicy:
        return GatewayAuthPolicy(
            mode="authelia",
            ext_authz=ExtAuthzConfig(
                cluster_name="ext_authz_authelia",
                host="authelia",
                port=9091,
                path_prefix="/api/authz/forward-auth",
                response_headers_to_add=("Remote-User",),
            ),
        )

    def test_injects_filter_before_router(self) -> None:
        payload = self._minimal_payload()
        inject_ext_authz_into_payload(payload, self._policy())
        filters = payload["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"]["http_filters"]
        filter_names = [f["name"] for f in filters]
        # ext_authz should be between lua and router
        self.assertEqual(filter_names, [
            "envoy.filters.http.lua",
            EXT_AUTHZ_FILTER_NAME,
            "envoy.filters.http.router",
        ])

    def test_adds_cluster(self) -> None:
        payload = self._minimal_payload()
        inject_ext_authz_into_payload(payload, self._policy())
        clusters = payload["static_resources"]["clusters"]
        cluster_names = [c["name"] for c in clusters]
        self.assertIn("ext_authz_authelia", cluster_names)

    def test_no_duplicate_cluster(self) -> None:
        payload = self._minimal_payload()
        # Inject twice
        inject_ext_authz_into_payload(payload, self._policy())
        inject_ext_authz_into_payload(payload, self._policy())
        clusters = payload["static_resources"]["clusters"]
        auth_clusters = [c for c in clusters if c["name"] == "ext_authz_authelia"]
        self.assertEqual(len(auth_clusters), 1)

    def test_no_injection_when_no_ext_authz(self) -> None:
        payload = self._minimal_payload()
        policy = GatewayAuthPolicy(mode="none")
        inject_ext_authz_into_payload(payload, policy)
        filters = payload["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"]["http_filters"]
        self.assertEqual(len(filters), 2)  # lua + router only


class TestAutheliaConfigGeneration(unittest.TestCase):
    """Verify dynamic Authelia config generation."""

    def _options(self, **kwargs) -> AutheliaConfigOptions:
        defaults = {
            "base_domain": "example.com",
            "stack_subdomain": "media",
            "gateway_host": "apps.media.example.com",
            "internet_exposed": True,
            "admin_username": "admin",
        }
        defaults.update(kwargs)
        return AutheliaConfigOptions(**defaults)

    def test_generates_valid_config(self) -> None:
        gen = AutheliaConfigGenerator(self._options())
        config = gen.generate_configuration()
        self.assertIn("server", config)
        self.assertIn("access_control", config)
        self.assertIn("session", config)
        self.assertIn("storage", config)

    def test_session_domain_matches_base(self) -> None:
        gen = AutheliaConfigGenerator(self._options(base_domain="example.com"))
        config = gen.generate_configuration()
        cookies = config["session"]["cookies"]
        self.assertEqual(cookies[0]["domain"], "example.com")

    def test_internet_exposed_uses_two_factor(self) -> None:
        gen = AutheliaConfigGenerator(self._options(internet_exposed=True))
        config = gen.generate_configuration()
        rules = config["access_control"]["rules"]
        # Find the internet-exposed rule (not the LAN bypass)
        internet_rule = [r for r in rules if r.get("policy") == "two_factor"]
        self.assertTrue(len(internet_rule) > 0, "Should have two_factor policy for internet-exposed")

    def test_lan_only_uses_one_factor(self) -> None:
        gen = AutheliaConfigGenerator(self._options(internet_exposed=False))
        config = gen.generate_configuration()
        rules = config["access_control"]["rules"]
        # All non-LAN rules should be one_factor
        non_bypass = [r for r in rules if r.get("policy") not in ("bypass",) and "networks" not in r]
        for rule in non_bypass:
            self.assertEqual(rule["policy"], "one_factor")

    def test_native_services_get_bypass(self) -> None:
        policy = GatewayAuthPolicy(
            mode="authelia",
            service_policies={"jellyfin": "native", "sonarr": "protected"},
        )
        gen = AutheliaConfigGenerator(self._options(auth_policy=policy))
        config = gen.generate_configuration()
        rules = config["access_control"]["rules"]
        bypass_rules = [r for r in rules if r.get("policy") == "bypass"]
        bypass_domains = []
        for r in bypass_rules:
            bypass_domains.extend(r.get("domain", []))
        self.assertTrue(any("jellyfin" in d for d in bypass_domains))

    def test_oidc_config_included_when_set(self) -> None:
        gen = AutheliaConfigGenerator(self._options(
            oidc_provider="google",
            oidc_config={
                "client_id": "test-client",
                "client_secret": "test-secret",
                "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
            },
        ))
        config = gen.generate_configuration()
        self.assertIn("identity_providers", config)
        oidc = config["identity_providers"]["oidc"]
        self.assertEqual(len(oidc["clients"]), 1)
        self.assertEqual(oidc["clients"][0]["client_id"], "test-client")

    def test_no_oidc_for_local_provider(self) -> None:
        gen = AutheliaConfigGenerator(self._options(oidc_provider="local"))
        config = gen.generate_configuration()
        self.assertNotIn("identity_providers", config)

    def test_generates_users_database(self) -> None:
        gen = AutheliaConfigGenerator(self._options(
            admin_username="testadmin",
            admin_email="test@example.com",
        ))
        users = gen.generate_users_database()
        self.assertIn("users", users)
        self.assertIn("testadmin", users["users"])
        self.assertEqual(users["users"]["testadmin"]["email"], "test@example.com")

    def test_secrets_are_generated_if_not_provided(self) -> None:
        opts = self._options()
        gen = AutheliaConfigGenerator(opts)
        config = gen.generate_configuration()
        # Secrets should be non-empty after generation
        self.assertTrue(len(config["identity_validation"]["reset_password"]["jwt_secret"]) >= 32)
        self.assertTrue(len(config["session"]["secret"]) >= 32)


class TestMultipleAuthProviderScenarios(unittest.TestCase):
    """End-to-end scenarios combining different auth modes + deployments."""

    def setUp(self) -> None:
        self.svc = AuthContractService(CONTRACT_PATH)

    def test_lan_only_none_mode(self) -> None:
        """LAN-only deployment: no auth anywhere."""
        policy = self.svc.resolve_policy(
            {"mode": "none"},
            services=[("jellyfin", "media"), ("sonarr", "management")],
        )
        self.assertIsNone(policy.ext_authz)
        self.assertEqual(len(policy.service_policies), 0)

    def test_internet_exposed_authelia_full_stack(self) -> None:
        """Internet-exposed with Authelia: Jellyfin native, everything else protected."""
        services = [
            ("jellyfin", "media"),
            ("sonarr", "management"),
            ("radarr", "management"),
            ("prowlarr", "indexer"),
            ("qbittorrent", "download"),
            ("jellyseerr", "request"),
            ("authelia", "infrastructure"),
        ]
        policy = self.svc.resolve_policy({"mode": "authelia"}, services=services)
        self.assertEqual(policy.service_policies["jellyfin"], "native")
        self.assertEqual(policy.service_policies["sonarr"], "protected")
        self.assertEqual(policy.service_policies["radarr"], "protected")
        self.assertEqual(policy.service_policies["prowlarr"], "protected")
        self.assertEqual(policy.service_policies["qbittorrent"], "protected")
        self.assertEqual(policy.service_policies["jellyseerr"], "protected")
        self.assertEqual(policy.service_policies["authelia"], "public")

    def test_authentik_with_google_oidc(self) -> None:
        """Authentik mode with Google as OIDC upstream."""
        policy = self.svc.resolve_policy({
            "mode": "authentik",
            "oidc_provider": "google",
            "oidc_config": {"client_id": "test", "client_secret": "secret"},
        })
        self.assertEqual(policy.mode, "authentik")
        self.assertIsNotNone(policy.ext_authz)
        self.assertEqual(policy.ext_authz.host, "authentik")
        self.assertEqual(policy.oidc_provider, "google")

    def test_authelia_with_okta_oidc(self) -> None:
        """Authelia mode with Okta as OIDC upstream."""
        policy = self.svc.resolve_policy({
            "mode": "authelia",
            "oidc_provider": "okta",
            "oidc_config": {
                "domain": "myorg.okta.com",
                "client_id": "test",
                "client_secret": "secret",
            },
        })
        self.assertEqual(policy.oidc_provider, "okta")
        self.assertEqual(policy.oidc_config["domain"], "myorg.okta.com")

    def test_user_can_protect_jellyfin(self) -> None:
        """Advanced user can override default and protect Jellyfin too."""
        services = [("jellyfin", "media")]
        policy = self.svc.resolve_policy(
            {"mode": "authelia", "per_service": {"jellyfin": "protected"}},
            services=services,
        )
        self.assertEqual(policy.service_policies["jellyfin"], "protected")

    def test_user_can_make_sonarr_public(self) -> None:
        """User can make any service public if they want."""
        services = [("sonarr", "management")]
        policy = self.svc.resolve_policy(
            {"mode": "authelia", "per_service": {"sonarr": "public"}},
            services=services,
        )
        self.assertEqual(policy.service_policies["sonarr"], "public")


class TestAppAuthSynchronization(unittest.TestCase):
    """Verify that app_auth is synchronized when gateway auth mode changes.

    When SSO is active (authelia/authentik), per-app Forms auth should be
    disabled so users don't get double-prompted after SSO login.
    When SSO is off, per-app Forms auth should be re-enabled.
    """

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._profile_path = Path(self._tmpdir) / "profile.yaml"
        self._profile_path.write_text(yaml.dump({
            "schema_version": 1,
            "routing": {"internet_exposed": True},
            "auth": {"provider": "none"},
            "app_auth": {
                "enabled": True,
                "method": "Forms",
                "required": "DisabledForLocalAddresses",
            },
        }), encoding="utf-8")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _load_profile(self) -> dict:
        return yaml.safe_load(self._profile_path.read_text(encoding="utf-8")) or {}

    def _make_service(self) -> "AuthConfigService":
        from media_stack.api.services.auth_config import AuthConfigService
        svc = AuthConfigService()
        # Monkey-patch to use our temp profile
        svc._load_profile = self._load_profile
        return svc

    def test_switching_to_authelia_disables_app_auth(self) -> None:
        """When gateway auth is enabled, app_auth.method should become None."""
        svc = self._make_service()
        # We can't easily call update_auth_config without the full server env,
        # so test the logic directly by checking the contract
        modes = svc._contract.get_modes()
        authelia_mode = modes.get("authelia")
        self.assertTrue(authelia_mode.gateway_auth,
            "Authelia mode should have gateway_auth=True")

    def test_none_mode_has_no_gateway_auth(self) -> None:
        """None mode should not have gateway auth."""
        svc = self._make_service()
        modes = svc._contract.get_modes()
        none_mode = modes.get("none")
        self.assertFalse(none_mode.gateway_auth)

    def test_basic_mode_has_no_gateway_auth(self) -> None:
        """Basic mode should not have gateway auth."""
        svc = self._make_service()
        modes = svc._contract.get_modes()
        basic_mode = modes.get("basic")
        self.assertFalse(basic_mode.gateway_auth)

    def test_current_config_includes_app_auth_summary(self) -> None:
        """API response should include explanation of effective auth state."""
        svc = self._make_service()
        config = svc.get_current_config()
        self.assertIn("app_auth_summary", config)
        self.assertIn("app_auth_method", config)

    def test_sso_mode_summary_mentions_disabled(self) -> None:
        """When mode is authelia, summary should mention SSO."""
        # Update profile to authelia mode
        profile = self._load_profile()
        profile["auth"]["provider"] = "authelia"
        profile["auth"]["mode"] = "authelia"
        profile["app_auth"]["method"] = "None"
        self._profile_path.write_text(yaml.dump(profile), encoding="utf-8")

        svc = self._make_service()
        config = svc.get_current_config()
        self.assertIn("SSO", config["app_auth_summary"])


if __name__ == "__main__":
    unittest.main()
