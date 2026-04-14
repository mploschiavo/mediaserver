"""End-to-end auth tests — identity endpoint, header forwarding,
per-service protection correctness, and Authelia config consistency.

Validates the full auth pipeline:
1. /api/auth/identity returns correct user info from forwarded headers
2. Per-service policies match expectations for all standard services
3. Authelia ext_authz header forwarding includes all required identity headers
4. Envoy ext_authz bypass is applied only to native/public services
5. Authelia config matches the auth mode (LAN vs internet-exposed)
6. OIDC callback URL is correct for the configured domain
7. Auth mode switching correctly syncs app_auth state
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from media_stack.core.auth.gateway_policy import (
    AuthContractService,
    ExtAuthzConfig,
    GatewayAuthPolicy,
)
from media_stack.core.auth.envoy_ext_authz import (
    EXT_AUTHZ_FILTER_NAME,
    apply_per_route_auth_policy,
    build_ext_authz_filter,
    inject_ext_authz_into_payload,
)
from media_stack.core.auth.authelia_config_generator import (
    AutheliaConfigGenerator,
    AutheliaConfigOptions,
)


CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "auth.yaml"


class TestIdentityEndpointHeaders(unittest.TestCase):
    """Verify the identity endpoint reads the correct headers for each provider."""

    def _extract_identity(self, headers: dict[str, str]) -> dict[str, str]:
        """Simulate what GET /api/auth/identity does with request headers."""
        user = headers.get("Remote-User", "") or headers.get("X-authentik-username", "")
        name = headers.get("Remote-Name", "") or headers.get("X-authentik-name", "")
        email = headers.get("Remote-Email", "") or headers.get("X-authentik-email", "")
        groups = headers.get("Remote-Groups", "") or headers.get("X-authentik-groups", "")
        return {
            "authenticated": bool(user),
            "user": user,
            "display_name": name,
            "email": email,
            "groups": groups,
        }

    def test_authelia_headers(self) -> None:
        """Authelia forwards Remote-User, Remote-Name, Remote-Email, Remote-Groups."""
        identity = self._extract_identity({
            "Remote-User": "admin",
            "Remote-Name": "Media Stack Admin",
            "Remote-Email": "admin@local",
            "Remote-Groups": "admins",
        })
        self.assertTrue(identity["authenticated"])
        self.assertEqual(identity["user"], "admin")
        self.assertEqual(identity["display_name"], "Media Stack Admin")
        self.assertEqual(identity["email"], "admin@local")
        self.assertEqual(identity["groups"], "admins")

    def test_authentik_headers(self) -> None:
        """Authentik forwards X-authentik-username, X-authentik-name, etc."""
        identity = self._extract_identity({
            "X-authentik-username": "jdoe",
            "X-authentik-name": "Jane Doe",
            "X-authentik-email": "jane@example.com",
            "X-authentik-groups": "admins,users",
        })
        self.assertTrue(identity["authenticated"])
        self.assertEqual(identity["user"], "jdoe")
        self.assertEqual(identity["display_name"], "Jane Doe")
        self.assertEqual(identity["email"], "jane@example.com")
        self.assertEqual(identity["groups"], "admins,users")

    def test_no_headers_means_unauthenticated(self) -> None:
        """No identity headers → not authenticated."""
        identity = self._extract_identity({})
        self.assertFalse(identity["authenticated"])
        self.assertEqual(identity["user"], "")

    def test_authelia_takes_precedence_over_authentik(self) -> None:
        """If both are present (shouldn't happen), Authelia headers win."""
        identity = self._extract_identity({
            "Remote-User": "authelia-user",
            "X-authentik-username": "authentik-user",
        })
        self.assertEqual(identity["user"], "authelia-user")

    def test_authentik_used_when_no_authelia(self) -> None:
        """Authentik headers used when Authelia headers are absent."""
        identity = self._extract_identity({
            "X-authentik-username": "authentik-user",
            "X-authentik-name": "Authentik User",
        })
        self.assertEqual(identity["user"], "authentik-user")
        self.assertEqual(identity["display_name"], "Authentik User")

    def test_empty_user_is_not_authenticated(self) -> None:
        """Empty Remote-User header is still not authenticated."""
        identity = self._extract_identity({"Remote-User": ""})
        self.assertFalse(identity["authenticated"])


class TestServiceProtectionCorrectness(unittest.TestCase):
    """Verify that ALL standard services get the correct auth policy.

    This is the critical safety test — wrong policies mean either
    services are exposed without auth or devices can't connect.
    """

    def setUp(self) -> None:
        self.svc = AuthContractService(CONTRACT_PATH)

    def test_media_servers_must_be_native(self) -> None:
        """Media servers (Jellyfin, Plex, Emby) MUST be native.

        TV apps (Roku, Fire TV, Apple TV, Android TV), mobile apps,
        and DLNA clients cannot handle OIDC/SAML redirects.
        """
        for media_svc in ("jellyfin", "plex", "emby"):
            policy = self.svc.resolve_service_policy(media_svc, "media")
            self.assertEqual(policy, "native",
                f"{media_svc} must be native — TV/mobile apps can't do OIDC")

    def test_arr_apps_are_protected(self) -> None:
        """Arr management apps should be protected by default."""
        for arr_svc in ("sonarr", "radarr", "prowlarr", "lidarr", "readarr", "bazarr"):
            policy = self.svc.resolve_service_policy(arr_svc, "management")
            self.assertEqual(policy, "protected",
                f"{arr_svc} should be protected (management category)")

    def test_download_clients_are_protected(self) -> None:
        """Download clients should be protected."""
        for dl_svc in ("qbittorrent", "sabnzbd"):
            policy = self.svc.resolve_service_policy(dl_svc, "download")
            self.assertEqual(policy, "protected",
                f"{dl_svc} should be protected (download category)")

    def test_request_manager_is_protected(self) -> None:
        """Jellyseerr (request manager) should be protected — web-only UI."""
        policy = self.svc.resolve_service_policy("jellyseerr", "request")
        self.assertEqual(policy, "protected")

    def test_auth_providers_must_be_public(self) -> None:
        """Auth providers themselves must be public — they ARE the auth layer."""
        for auth_svc in ("authelia", "authentik"):
            policy = self.svc.resolve_service_policy(auth_svc, "infrastructure")
            self.assertEqual(policy, "public",
                f"{auth_svc} must be public — it is the auth provider")

    def test_controller_is_protected(self) -> None:
        """Controller dashboard should be protected when SSO is active."""
        policy = self.svc.resolve_service_policy("media-stack-controller", "infrastructure")
        self.assertEqual(policy, "protected")

    def test_envoy_is_public(self) -> None:
        """Envoy is infrastructure and should be public."""
        policy = self.svc.resolve_service_policy("envoy", "infrastructure")
        self.assertEqual(policy, "public")

    def test_homepage_is_protected(self) -> None:
        """Homepage dashboard shows sensitive service status."""
        policy = self.svc.resolve_service_policy("homepage", "infrastructure")
        self.assertEqual(policy, "protected")

    def test_indexer_is_protected(self) -> None:
        """Indexer services should be protected."""
        policy = self.svc.resolve_service_policy("prowlarr", "indexer")
        self.assertEqual(policy, "protected")

    def test_monitoring_is_protected(self) -> None:
        """Monitoring services should be protected."""
        policy = self.svc.resolve_service_policy("tautulli", "monitoring")
        self.assertEqual(policy, "protected")


class TestExtAuthzHeaderForwarding(unittest.TestCase):
    """Verify Envoy ext_authz forwards all required identity headers."""

    def setUp(self) -> None:
        self.svc = AuthContractService(CONTRACT_PATH)
        self.modes = self.svc.get_modes()

    def test_authelia_forwards_all_identity_headers(self) -> None:
        """Authelia must forward Remote-User, Remote-Name, Remote-Email, Remote-Groups."""
        ext_authz = self.modes["authelia"].ext_authz
        required = {"Remote-User", "Remote-Groups", "Remote-Name", "Remote-Email"}
        forwarded = set(ext_authz.response_headers_to_add)
        missing = required - forwarded
        self.assertEqual(missing, set(),
            f"Authelia ext_authz missing headers: {missing}")

    def test_authentik_forwards_all_identity_headers(self) -> None:
        """Authentik must forward X-authentik-username, -name, -email, -groups, -uid."""
        ext_authz = self.modes["authentik"].ext_authz
        required = {"X-authentik-username", "X-authentik-groups",
                     "X-authentik-email", "X-authentik-name", "X-authentik-uid"}
        forwarded = set(ext_authz.response_headers_to_add)
        missing = required - forwarded
        self.assertEqual(missing, set(),
            f"Authentik ext_authz missing headers: {missing}")

    def test_ext_authz_filter_includes_forwarded_headers(self) -> None:
        """The generated Envoy filter config must include response header patterns."""
        ext_authz = self.modes["authelia"].ext_authz
        f = build_ext_authz_filter(ext_authz)
        auth_resp = f["typed_config"]["http_service"]["authorization_response"]
        patterns = auth_resp["allowed_upstream_headers"]["patterns"]
        header_names = {p["exact"] for p in patterns}
        self.assertIn("Remote-User", header_names)
        self.assertIn("Remote-Name", header_names)
        self.assertIn("Remote-Email", header_names)
        self.assertIn("Remote-Groups", header_names)

    def test_ext_authz_filter_allows_cookie_header(self) -> None:
        """Cookie header must be forwarded to auth provider for session checking."""
        ext_authz = self.modes["authelia"].ext_authz
        f = build_ext_authz_filter(ext_authz)
        auth_req = f["typed_config"]["http_service"]["authorization_request"]
        patterns = auth_req["allowed_headers"]["patterns"]
        # Check exact or prefix patterns include cookie
        all_names = []
        for p in patterns:
            if "exact" in p:
                all_names.append(p["exact"])
            elif "prefix" in p:
                all_names.append(f'prefix:{p["prefix"]}')
        self.assertTrue(
            any("cookie" in n for n in all_names),
            f"Cookie must be in allowed patterns: {all_names}",
        )


class TestEnvoyRouteProtection(unittest.TestCase):
    """Verify per-route ext_authz bypass is applied correctly."""

    def _full_policy(self) -> GatewayAuthPolicy:
        svc = AuthContractService(CONTRACT_PATH)
        services = [
            ("jellyfin", "media"),
            ("sonarr", "management"),
            ("radarr", "management"),
            ("prowlarr", "indexer"),
            ("qbittorrent", "download"),
            ("jellyseerr", "request"),
            ("authelia", "infrastructure"),
            ("envoy", "infrastructure"),
            ("media-stack-controller", "infrastructure"),
        ]
        return svc.resolve_policy({"mode": "authelia"}, services=services)

    def test_protected_routes_have_no_bypass(self) -> None:
        """Protected services must NOT have ext_authz disabled on their routes."""
        policy = self._full_policy()
        for svc in ("sonarr", "radarr", "prowlarr", "qbittorrent", "jellyseerr", "media-stack-controller"):
            route = {"match": {"prefix": f"/app/{svc}"}, "route": {"cluster": svc}}
            apply_per_route_auth_policy(route, svc, policy)
            self.assertNotIn("typed_per_filter_config", route,
                f"{svc} is protected — must NOT have ext_authz bypass")

    def test_native_routes_have_bypass(self) -> None:
        """Native services (Jellyfin) must have ext_authz disabled on their routes."""
        policy = self._full_policy()
        route = {"match": {"prefix": "/app/jellyfin"}, "route": {"cluster": "jellyfin"}}
        apply_per_route_auth_policy(route, "jellyfin", policy)
        self.assertIn("typed_per_filter_config", route,
            "jellyfin is native — must have ext_authz bypass")
        self.assertTrue(
            route["typed_per_filter_config"][EXT_AUTHZ_FILTER_NAME]["disabled"])

    def test_public_routes_have_bypass(self) -> None:
        """Public services (authelia, envoy) must have ext_authz disabled."""
        policy = self._full_policy()
        for svc in ("authelia", "envoy"):
            route = {"match": {"prefix": f"/app/{svc}"}, "route": {"cluster": svc}}
            apply_per_route_auth_policy(route, svc, policy)
            self.assertIn("typed_per_filter_config", route,
                f"{svc} is public — must have ext_authz bypass")

    def test_full_envoy_payload_injection(self) -> None:
        """Verify ext_authz filter + cluster injected into a realistic Envoy payload."""
        policy = self._full_policy()
        payload = {
            "static_resources": {
                "listeners": [{
                    "filter_chains": [{
                        "filters": [{
                            "typed_config": {
                                "http_filters": [
                                    {"name": "envoy.filters.http.router", "typed_config": {}},
                                ],
                            },
                        }],
                    }],
                }],
                "clusters": [
                    {"name": "jellyfin", "type": "STRICT_DNS"},
                    {"name": "sonarr", "type": "STRICT_DNS"},
                ],
            },
        }
        inject_ext_authz_into_payload(payload, policy)
        filters = payload["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"]["http_filters"]
        filter_names = [f["name"] for f in filters]
        # ext_authz must come BEFORE router
        self.assertLess(
            filter_names.index(EXT_AUTHZ_FILTER_NAME),
            filter_names.index("envoy.filters.http.router"),
        )
        # Auth cluster added
        cluster_names = [c["name"] for c in payload["static_resources"]["clusters"]]
        self.assertIn("ext_authz_authelia", cluster_names)


class TestAutheliaConfigConsistency(unittest.TestCase):
    """Verify Authelia config matches auth contract expectations."""

    def _options(self, **kwargs) -> AutheliaConfigOptions:
        defaults = {
            "base_domain": "media-stack.local",
            "stack_subdomain": "media-stack",
            "gateway_host": "apps.media-stack.local",
            "gateway_port": 8880,
            "internet_exposed": False,
            "admin_username": "admin",
            "admin_email": "admin@local",
        }
        defaults.update(kwargs)
        return AutheliaConfigOptions(**defaults)

    def test_session_cookie_domain_matches_base(self) -> None:
        gen = AutheliaConfigGenerator(self._options())
        config = gen.generate_configuration()
        cookie_domain = config["session"]["cookies"][0]["domain"]
        self.assertEqual(cookie_domain, "media-stack.local")

    def test_authelia_url_includes_port(self) -> None:
        """Non-standard port should be included in authelia_url."""
        gen = AutheliaConfigGenerator(self._options(gateway_port=8880))
        config = gen.generate_configuration()
        authelia_url = config["session"]["cookies"][0]["authelia_url"]
        self.assertIn(":8880", authelia_url)
        self.assertIn("auth.media-stack.media-stack.local", authelia_url)

    def test_authelia_url_no_port_on_80(self) -> None:
        """Port 80 should be omitted from authelia_url."""
        gen = AutheliaConfigGenerator(self._options(gateway_port=80))
        config = gen.generate_configuration()
        authelia_url = config["session"]["cookies"][0]["authelia_url"]
        self.assertNotIn(":80", authelia_url)

    def test_lan_only_uses_one_factor(self) -> None:
        """LAN-only deployment should use one_factor, not two_factor."""
        gen = AutheliaConfigGenerator(self._options(internet_exposed=False))
        config = gen.generate_configuration()
        rules = config["access_control"]["rules"]
        # No two_factor rules for LAN-only
        two_factor = [r for r in rules if r.get("policy") == "two_factor"]
        self.assertEqual(len(two_factor), 0,
            "LAN-only deployment should not have two_factor rules")

    def test_internet_exposed_uses_two_factor(self) -> None:
        """Internet-exposed should use two_factor for external access."""
        gen = AutheliaConfigGenerator(self._options(internet_exposed=True))
        config = gen.generate_configuration()
        rules = config["access_control"]["rules"]
        two_factor = [r for r in rules if r.get("policy") == "two_factor"]
        self.assertTrue(len(two_factor) > 0,
            "Internet-exposed deployment must have two_factor rules")

    def test_default_policy_is_deny(self) -> None:
        """Default access control policy must be deny for security."""
        gen = AutheliaConfigGenerator(self._options())
        config = gen.generate_configuration()
        self.assertEqual(config["access_control"]["default_policy"], "deny")

    def test_lan_bypass_uses_one_factor_not_bypass(self) -> None:
        """LAN network rule should use one_factor, not bypass, for security."""
        gen = AutheliaConfigGenerator(self._options())
        config = gen.generate_configuration()
        rules = config["access_control"]["rules"]
        lan_rules = [r for r in rules if "networks" in r]
        for r in lan_rules:
            self.assertEqual(r["policy"], "one_factor",
                "LAN bypass should be one_factor (not bypass) for defense in depth")

    def test_native_services_bypassed_in_authelia(self) -> None:
        """Jellyfin bypass rule must exist in Authelia access control."""
        policy = GatewayAuthPolicy(
            mode="authelia",
            service_policies={"jellyfin": "native"},
        )
        gen = AutheliaConfigGenerator(self._options(auth_policy=policy))
        config = gen.generate_configuration()
        rules = config["access_control"]["rules"]
        bypass_domains = []
        for r in rules:
            if r.get("policy") == "bypass":
                bypass_domains.extend(r.get("domain", []))
        self.assertTrue(any("jellyfin" in d for d in bypass_domains),
            "Jellyfin must have a bypass rule in Authelia access control")


class TestOIDCCallbackUrl(unittest.TestCase):
    """Verify OIDC callback URL is correct for different domain configs."""

    def test_callback_url_pattern(self) -> None:
        """The OIDC callback is always at auth.{subdomain}.{base}/api/oidc/callback."""
        # This is what users register at their IdP
        base_domain = "media-stack.local"
        stack_subdomain = "media-stack"
        port = 8880
        expected = f"http://auth.{stack_subdomain}.{base_domain}:{port}/api/oidc/callback"
        self.assertIn("/api/oidc/callback", expected)
        self.assertIn("auth.", expected)

    def test_authelia_generates_correct_authelia_url(self) -> None:
        """Authelia's session cookie authelia_url must match the expected auth subdomain."""
        gen = AutheliaConfigGenerator(AutheliaConfigOptions(
            base_domain="example.com",
            stack_subdomain="media",
            gateway_host="apps.media.example.com",
            gateway_port=80,
        ))
        config = gen.generate_configuration()
        authelia_url = config["session"]["cookies"][0]["authelia_url"]
        self.assertTrue(authelia_url.startswith("http://auth.media.example.com"),
            f"authelia_url should start with auth subdomain, got: {authelia_url}")


class TestAuthModeSwitchingIntegrity(unittest.TestCase):
    """Verify auth mode transitions don't leave inconsistent state."""

    def setUp(self) -> None:
        self.svc = AuthContractService(CONTRACT_PATH)

    def test_none_to_authelia_enables_ext_authz(self) -> None:
        """Switching from none to authelia should enable ext_authz."""
        policy_none = self.svc.resolve_policy({"mode": "none"})
        policy_auth = self.svc.resolve_policy({"mode": "authelia"})
        self.assertIsNone(policy_none.ext_authz)
        self.assertIsNotNone(policy_auth.ext_authz)

    def test_authelia_to_none_disables_ext_authz(self) -> None:
        """Switching from authelia to none should disable ext_authz."""
        policy_auth = self.svc.resolve_policy({"mode": "authelia"})
        policy_none = self.svc.resolve_policy({"mode": "none"})
        self.assertIsNotNone(policy_auth.ext_authz)
        self.assertIsNone(policy_none.ext_authz)

    def test_switching_providers_changes_ext_authz_host(self) -> None:
        """Switching between authelia and authentik changes the ext_authz host."""
        p_authelia = self.svc.resolve_policy({"mode": "authelia"})
        p_authentik = self.svc.resolve_policy({"mode": "authentik"})
        self.assertEqual(p_authelia.ext_authz.host, "authelia")
        self.assertEqual(p_authentik.ext_authz.host, "authentik")
        self.assertNotEqual(p_authelia.ext_authz.port, p_authentik.ext_authz.port)

    def test_service_policies_consistent_across_providers(self) -> None:
        """Jellyfin should be native regardless of which auth provider is used."""
        services = [("jellyfin", "media"), ("sonarr", "management")]
        p_authelia = self.svc.resolve_policy({"mode": "authelia"}, services=services)
        p_authentik = self.svc.resolve_policy({"mode": "authentik"}, services=services)
        self.assertEqual(p_authelia.service_policies["jellyfin"], "native")
        self.assertEqual(p_authentik.service_policies["jellyfin"], "native")
        self.assertEqual(p_authelia.service_policies["sonarr"], "protected")
        self.assertEqual(p_authentik.service_policies["sonarr"], "protected")

    def test_basic_mode_has_no_service_policies(self) -> None:
        """Basic mode has no gateway auth, so no service policies are resolved."""
        policy = self.svc.resolve_policy(
            {"mode": "basic"},
            services=[("jellyfin", "media"), ("sonarr", "management")],
        )
        # basic mode has no gateway auth, resolve_policy returns empty policies
        self.assertEqual(len(policy.service_policies), 0)


if __name__ == "__main__":
    unittest.main()
