"""Tests for ``api/routes/auth.py`` (ADR-0007 Phase 2 wave 3).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used in
production.

Patching strategy:

* The four config-tied routes (``/api/auth/config``, ``/modes``,
  ``/oidc-providers``, ``/service-policies``) all build a fresh
  ``AuthConfigService`` via the route module's
  ``AuthGetRoutes._auth_config_service`` factory classmethod.
  Tests monkey-patch that classmethod onto a stub instead of
  patching the module-level ``AuthConfigService`` import; that
  way the test code never has to know which import path the
  legacy chain used (which kept moving as the auth module was
  refactored over v1.0.16x).
* The ``/api/auth/identity`` route runs a constructor-injected
  ``IdentityResolver``; we exercise the production resolver
  end-to-end with header / cookie / Basic-auth fixtures and a
  patched user-store hydrator. The resolver's strategy chain is
  the load-bearing logic; patching individual strategies inside
  the chain would re-test mocks instead of behaviour.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import patch

from tests.unit.api.routes._helpers import RouteDispatchHarness


class _StubAuthConfigService:
    """Minimal ``AuthConfigService``-shaped stub.

    Tests assign one or more attributes to drive the response
    shape; the four config routes invoke whichever method matches
    the route they exercise.
    """

    def __init__(
        self,
        current_config: dict[str, Any] | None = None,
        modes: list[dict[str, Any]] | None = None,
        providers: list[dict[str, Any]] | None = None,
        policies: list[dict[str, Any]] | None = None,
    ) -> None:
        self._config = current_config or {}
        self._modes = modes or []
        self._providers = providers or []
        self._policies = policies or []

    def get_current_config(self) -> dict[str, Any]:
        return self._config

    def get_auth_modes(self) -> list[dict[str, Any]]:
        return self._modes

    def get_oidc_providers(self) -> list[dict[str, Any]]:
        return self._providers

    def get_service_policies(self) -> list[dict[str, Any]]:
        return self._policies


def _patch_auth_config(stub: _StubAuthConfigService) -> Any:
    """Build the patch context manager that swaps the route
    module's classmethod factory to return ``stub``. Centralized
    so test methods don't repeat the long dotted path."""
    return patch(
        "media_stack.api.routes.auth.AuthGetRoutes._auth_config_service",
        classmethod(lambda cls: stub),
    )


# --- Identity --------------------------------------------------------


class TestAuthIdentityRouteSsoHeaders:
    """``GET /api/auth/identity`` — SSO header strategy.

    The first hit in the chain comes from the upstream proxy's
    ``Remote-*`` (Authelia) or ``X-authentik-*`` headers. Both
    flavours must work; both must populate display_name/email/
    groups when present.
    """

    def test_resolves_authelia_remote_headers(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET",
            "/api/auth/identity",
            headers={
                "Remote-User": "alice",
                "Remote-Name": "Alice Anderson",
                "Remote-Email": "alice@example.com",
                "Remote-Groups": "admins,operators",
            },
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "authenticated": True,
            "user": "alice",
            "display_name": "Alice Anderson",
            "email": "alice@example.com",
            "groups": "admins,operators",
        }

    def test_resolves_authentik_x_authentik_headers(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET",
            "/api/auth/identity",
            headers={
                "X-authentik-username": "bob",
                "X-authentik-name": "Bob Builder",
                "X-authentik-email": "bob@example.com",
                "X-authentik-groups": "operators",
            },
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["authenticated"] is True
        assert body["user"] == "bob"
        assert body["display_name"] == "Bob Builder"
        assert body["email"] == "bob@example.com"
        assert body["groups"] == "operators"

    def test_authelia_headers_win_over_authentik(self) -> None:
        """When both header sets are present (mixed-deployment
        edge case during a provider swap), the Authelia headers
        come first in the strategy's candidate list.
        """
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch(
            "GET",
            "/api/auth/identity",
            headers={
                "Remote-User": "from-authelia",
                "X-authentik-username": "from-authentik",
            },
        )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["user"] == "from-authelia"


class TestAuthIdentityRouteCookieFallback:
    """When no SSO header fired, the controller falls back to its
    in-process session cookie. Patches ``session_cookie_reader``
    on the route module so we don't need a real session store."""

    def test_resolves_username_from_session_cookie(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.api.routes.auth.session_cookie_reader."
            "username_for_handler",
            return_value="cookie-user",
        ):
            response = harness.dispatch(
                "GET",
                "/api/auth/identity",
                headers={"Cookie": "ms_session=abc"},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["authenticated"] is True
        assert body["user"] == "cookie-user"
        # display_name falls back to user when no hydrator hit.
        assert body["display_name"] == "cookie-user"
        assert body["email"] == ""
        assert body["groups"] == ""


class TestAuthIdentityRouteBasicAuth:
    """When no SSO header AND no cookie, the controller parses the
    ``Authorization: Basic …`` header — the basic-mode deployment
    where the browser supplies credentials via the
    WWW-Authenticate popup."""

    def test_resolves_username_from_basic_auth(self) -> None:
        creds = base64.b64encode(b"basic-user:hunter2").decode()
        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.api.routes.auth.session_cookie_reader."
            "username_for_handler",
            return_value="",
        ):
            response = harness.dispatch(
                "GET",
                "/api/auth/identity",
                headers={"Authorization": f"Basic {creds}"},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["user"] == "basic-user"
        assert body["authenticated"] is True

    def test_malformed_basic_header_is_swallowed(self) -> None:
        """A garbage ``Authorization: Basic …`` value (e.g. not
        base64) is logged and ignored — the legacy chain swallows
        the same shape with ``except Exception``; we narrow to
        ``binascii.Error`` / ``ValueError`` instead."""
        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.api.routes.auth.session_cookie_reader."
            "username_for_handler",
            return_value="",
        ):
            response = harness.dispatch(
                "GET",
                "/api/auth/identity",
                headers={"Authorization": "Basic !!!not-base64!!!"},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["authenticated"] is False
        assert body["user"] == ""

    def test_non_basic_authorization_header_is_ignored(self) -> None:
        """Bearer / Negotiate / etc. don't go through the basic
        parser; the strategy returns empty, so the response is
        ``authenticated: false``."""
        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.api.routes.auth.session_cookie_reader."
            "username_for_handler",
            return_value="",
        ):
            response = harness.dispatch(
                "GET",
                "/api/auth/identity",
                headers={"Authorization": "Bearer something"},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["authenticated"] is False


class TestAuthIdentityRouteUnauthenticated:
    """No headers, no cookie, no Basic — fully unauthenticated."""

    def test_unauthenticated_envelope(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.api.routes.auth.session_cookie_reader."
            "username_for_handler",
            return_value="",
        ):
            response = harness.dispatch("GET", "/api/auth/identity")
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "authenticated": False,
            "user": "",
            "display_name": "",
            "email": "",
            "groups": "",
        }
        # Pin the spec's response keys verbatim — the Topbar
        # avatar relies on every key being present, even when
        # blank.
        assert set(body) == {
            "authenticated", "user", "display_name", "email", "groups",
        }


class TestAuthIdentityRouteUserStoreHydration:
    """When SSO headers supplied just a username (no name/email)
    — common with Authelia file-backend deployments — the
    controller hydrates display_name + email from its own user
    store."""

    def test_hydrates_display_name_when_only_user_present(self) -> None:
        """Mock the user-service factory so the resolver picks up
        a row with display_name + email and rolls them into the
        response."""
        class _Row:
            display_name = "Administrator"
            email = "admin@local"

        class _Store:
            def get_by_username(self, _username: str) -> Any:
                return _Row()

        class _Svc:
            _store = _Store()

        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.core.auth.users.user_service_factory."
            "build_default_service",
            return_value=_Svc(),
        ):
            response = harness.dispatch(
                "GET",
                "/api/auth/identity",
                headers={"Remote-User": "admin"},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["user"] == "admin"
        assert body["display_name"] == "Administrator"
        assert body["email"] == "admin@local"

    def test_hydration_swallows_user_service_failure(self) -> None:
        """If the user store can't be built (sqlite locked,
        migrations pending, etc.), the resolver still returns the
        bare username envelope rather than 500ing."""
        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.core.auth.users.user_service_factory."
            "build_default_service",
            side_effect=OSError("db locked"),
        ):
            response = harness.dispatch(
                "GET",
                "/api/auth/identity",
                headers={"Remote-User": "admin"},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["user"] == "admin"
        # No name in headers + hydration failed → display_name
        # falls back to the username.
        assert body["display_name"] == "admin"

    def test_hydration_skipped_when_name_already_provided(self) -> None:
        """Hydration only runs when ``name`` is empty. When the
        SSO proxy already gave us a name, we don't hit the user
        store — important because the user store may not have a
        row for an SSO-only operator."""
        class _Boom:
            def get_by_username(self, _u: str) -> Any:
                raise AssertionError("hydrator should not have run")

        class _Svc:
            _store = _Boom()

        harness = RouteDispatchHarness.with_default_router()
        with patch(
            "media_stack.core.auth.users.user_service_factory."
            "build_default_service",
            return_value=_Svc(),
        ):
            response = harness.dispatch(
                "GET",
                "/api/auth/identity",
                headers={
                    "Remote-User": "alice",
                    "Remote-Name": "Alice From Authelia",
                },
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body["display_name"] == "Alice From Authelia"


# --- Config / Modes / Providers / Policies ---------------------------


class TestAuthConfigRoute:
    """``GET /api/auth/config`` — current auth-configuration
    snapshot. Pinned response shape (mode + internet_exposed +
    oidc_provider + oidc_config + per_service + app_auth +
    app_auth_method + app_auth_summary)."""

    def test_returns_current_config_payload(self) -> None:
        stub = _StubAuthConfigService(current_config={
            "mode": "authelia",
            "internet_exposed": False,
            "oidc_provider": "local",
            "oidc_config": {},
            "per_service": {},
            "app_auth": {
                "enabled": True,
                "method": "None",
                "required": "DisabledForLocalAddresses",
            },
            "app_auth_method": "None",
            "app_auth_summary": "SSO gateway — local network",
        })
        harness = RouteDispatchHarness.with_default_router()
        with _patch_auth_config(stub):
            response = harness.dispatch("GET", "/api/auth/config")
        assert response.status == 200
        body = json.loads(response.body)
        assert body["mode"] == "authelia"
        assert body["internet_exposed"] is False
        assert body["oidc_provider"] == "local"
        assert body["app_auth_method"] == "None"
        # Pin the OpenAPI spec's response keys.
        for key in (
            "mode", "internet_exposed", "oidc_provider", "oidc_config",
            "per_service", "app_auth", "app_auth_method",
            "app_auth_summary",
        ):
            assert key in body, f"missing key {key!r}"


class TestAuthModesRoute:
    """``GET /api/auth/modes`` — supported-mode catalog wrapped in
    ``{"modes": [...]}``."""

    def test_returns_modes_list_wrapped(self) -> None:
        modes = [
            {
                "key": "none",
                "display_name": "No Authentication",
                "description": "Trusted LAN only.",
                "gateway_auth": False,
                "controller_auth": "none",
                "provider_service": "",
            },
            {
                "key": "authelia",
                "display_name": "Authelia (SSO)",
                "description": "Authelia forward-auth via Envoy.",
                "gateway_auth": True,
                "controller_auth": "forwarded",
                "provider_service": "authelia",
            },
        ]
        stub = _StubAuthConfigService(modes=modes)
        harness = RouteDispatchHarness.with_default_router()
        with _patch_auth_config(stub):
            response = harness.dispatch("GET", "/api/auth/modes")
        assert response.status == 200
        body = json.loads(response.body)
        # Pin the wrapper key — UI binds against ``data.modes``.
        assert set(body) == {"modes"}
        assert body["modes"] == modes

    def test_returns_empty_modes_list(self) -> None:
        stub = _StubAuthConfigService(modes=[])
        harness = RouteDispatchHarness.with_default_router()
        with _patch_auth_config(stub):
            response = harness.dispatch("GET", "/api/auth/modes")
        assert response.status == 200
        assert json.loads(response.body) == {"modes": []}


class TestAuthOidcProvidersRoute:
    """``GET /api/auth/oidc-providers`` — provider catalog wrapped
    in ``{"providers": [...]}``."""

    def test_returns_providers_list_wrapped(self) -> None:
        providers = [
            {
                "key": "local",
                "display_name": "Local Accounts",
                "description": "File-based users.",
                "required_fields": [],
            },
            {
                "key": "google",
                "display_name": "Google",
                "description": "Google Workspace.",
                "required_fields": ["client_id", "client_secret"],
            },
        ]
        stub = _StubAuthConfigService(providers=providers)
        harness = RouteDispatchHarness.with_default_router()
        with _patch_auth_config(stub):
            response = harness.dispatch(
                "GET", "/api/auth/oidc-providers",
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert set(body) == {"providers"}
        assert body["providers"] == providers


class TestAuthServicePoliciesRoute:
    """``GET /api/auth/service-policies`` — per-service policy
    catalog wrapped in ``{"services": [...]}``."""

    def test_returns_service_policies_wrapped(self) -> None:
        policies = [
            {
                "service_id": "sonarr",
                "service_name": "Sonarr",
                "category": "automation",
                "policy": "protected",
                "source": "contract",
            },
            {
                "service_id": "jellyfin",
                "service_name": "Jellyfin",
                "category": "media",
                "policy": "native",
                "source": "contract",
            },
        ]
        stub = _StubAuthConfigService(policies=policies)
        harness = RouteDispatchHarness.with_default_router()
        with _patch_auth_config(stub):
            response = harness.dispatch(
                "GET", "/api/auth/service-policies",
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert set(body) == {"services"}
        assert body["services"] == policies


# --- Routing-integration --------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behavior for the Auth
    domain. If a future change accidentally drops a handler from
    the registry, this fires before any per-route test does."""

    def test_all_auth_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            "/api/auth/identity",
            "/api/auth/config",
            "/api/auth/modes",
            "/api/auth/oidc-providers",
            "/api/auth/service-policies",
        }
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in expected
        }
        assert registered == expected, (
            f"Missing auth routes: {expected - registered}"
        )

    def test_post_to_auth_modes_returns_method_not_allowed(self) -> None:
        # /api/auth/modes is GET-only in the spec; POST must 405.
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/auth/modes")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED

    def test_post_to_auth_identity_returns_method_not_allowed(
        self,
    ) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("POST", "/api/auth/identity")
        from media_stack.api.routing import DispatchOutcome
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
