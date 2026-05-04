"""Auth-domain GET routes (ADR-0007 Phase 2 wave 3).

Five routes migrated off the ``handlers_get.handle()`` elif chain,
all sharing the OpenAPI ``Auth`` tag:

* ``GET /api/auth/identity`` — resolve the caller's identity from
  forwarded SSO headers (Authelia / Authentik), then the controller
  session cookie, then HTTP Basic credentials, then hydrate
  display_name + email from the user store when only the username
  is known.
* ``GET /api/auth/config`` — current auth configuration snapshot
  (mode, internet_exposed, oidc_provider/config, per_service,
  app_auth).
* ``GET /api/auth/modes`` — catalog of supported auth modes for
  the dashboard's mode-chooser tiles.
* ``GET /api/auth/oidc-providers`` — OIDC upstream provider catalog
  (local, google, microsoft, keycloak …).
* ``GET /api/auth/service-policies`` — resolved per-service auth
  policy (public / protected / native), with provenance.

Implementation patterns (named per the project's "use named design
patterns where they fit" rule):

* **Strategy + Chain of Responsibility** — ``IdentityResolver`` walks
  an ordered list of identity-source strategies (forwarded-header →
  session-cookie → basic-auth) and stops at the first hit, then
  hydrates display_name / email from the user store when the
  username is known but the supplementary fields aren't. Same
  resolution order the legacy elif branch grew over time; the
  three-strategy chain here is what guarantees the "??" topbar
  avatar bug stays fixed for every auth posture.
* **Factory @classmethod** — ``AuthGetRoutes._auth_config_service``
  builds an ``AuthConfigService`` per request. The legacy chain
  builds one inside each elif body via lazy ``from ... import``;
  routing it through a classmethod lets test code monkey-replace
  the factory without poking at module-level state, and keeps the
  import path uniform across the four config-tied routes.

The auth-config service import is at module level (not lazy)
because every method on this class uses it — keeping it lazy
would just defer the import to the first request. The session
cookie reader + user-service factory imports stay lazy in the
identity flow because they're only relevant when no SSO header
fired, and dragging them into the import graph at startup would
make this module pull the entire auth/users/* tree just to satisfy
the four config-only routes.
"""

from __future__ import annotations

import base64
import binascii
from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services.auth_config import AuthConfigService
from media_stack.api.session_singletons import session_cookie_reader
from media_stack.core.logging_utils import log_swallowed


# Header names the upstream SSO proxies (Authelia / Authentik) set
# when they finish forward-auth and pass the request on to the
# controller. Pulled out as a constant so a test can pin the order
# AND so a future Phase-3 SSO addition has one place to drop in
# its header pair.
_SSO_USERNAME_HEADERS: tuple[str, ...] = ("Remote-User", "X-authentik-username")
_SSO_NAME_HEADERS: tuple[str, ...] = ("Remote-Name", "X-authentik-name")
_SSO_EMAIL_HEADERS: tuple[str, ...] = ("Remote-Email", "X-authentik-email")
_SSO_GROUPS_HEADERS: tuple[str, ...] = ("Remote-Groups", "X-authentik-groups")


class _SsoHeaderStrategy:
    """First identity strategy: read forwarded SSO headers.

    The upstream proxy (Authelia via Envoy ext_authz, or Authentik
    in forward-auth mode) sets ``Remote-*`` / ``X-authentik-*``
    headers AFTER it has authenticated the user. The controller
    trusts these headers because the gateway only forwards them
    when its own ext_authz check passed; spoofing them on the open
    internet is blocked by the trusted-proxy CIDR check that runs
    earlier in the request lifecycle (see
    ``TrustedProxyAuth.identity``).
    """

    def resolve(self, headers: Any) -> tuple[str, str, str, str]:
        user = self._first_nonblank(headers, _SSO_USERNAME_HEADERS)
        name = self._first_nonblank(headers, _SSO_NAME_HEADERS)
        email = self._first_nonblank(headers, _SSO_EMAIL_HEADERS)
        groups = self._first_nonblank(headers, _SSO_GROUPS_HEADERS)
        return user, name, email, groups

    def _first_nonblank(
        self, headers: Any, candidates: tuple[str, ...],
    ) -> str:
        for hdr in candidates:
            val = headers.get(hdr, "") or ""
            if val:
                return val
        return ""


class _SessionCookieStrategy:
    """Second identity strategy: read the controller's session cookie.

    Only meaningful when no SSO header fired — covers the direct-
    access deployment shape where the operator signed in via the
    controller's in-page login form rather than going through
    Authelia/Authentik.
    """

    def __init__(self, reader: Any = session_cookie_reader) -> None:
        self._reader = reader

    def resolve(self, handler: Any) -> str:
        return self._reader.username_for_handler(handler) or ""


class _BasicAuthStrategy:
    """Third identity strategy: parse HTTP Basic credentials.

    Used when the browser is supplying ``Authorization: Basic …``
    on every request via the WWW-Authenticate popup (the basic-mode
    deployment shape — no gateway, no cookie). Returns just the
    username; the password is only validated by the gateway-auth
    chain, never trusted from this header alone.
    """

    def resolve(self, headers: Any) -> str:
        auth_hdr = headers.get("Authorization", "") or ""
        if not auth_hdr.startswith("Basic "):
            return ""
        try:
            decoded = base64.b64decode(auth_hdr[6:]).decode(
                "utf-8", "replace",
            )
        except (binascii.Error, ValueError) as exc:
            log_swallowed(exc)
            return ""
        return decoded.partition(":")[0] or ""


class _UserStoreHydrator:
    """Hydrate display_name + email from the user store when only
    the username is known.

    Without this, the Topbar avatar shows a bare ``admin`` instead
    of the configured display name (e.g. ``Administrator``) — same
    "??" symptom that drove the legacy chain to grow this branch.
    The hydration is best-effort: any failure to load the user
    service is swallowed so the identity flow never breaks because
    the user-store sqlite happens to be locked or missing.
    """

    def hydrate(
        self, user: str, name: str, email: str,
    ) -> tuple[str, str]:
        if not user or name:
            return name, email
        try:
            from media_stack.core.auth.users.user_service_factory import (
                build_default_service,
            )
            svc = build_default_service()
            row = svc._store.get_by_username(user)
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc)
            return name, email
        if row is None:
            return name, email
        new_name = (getattr(row, "display_name", "") or "").strip()
        new_email = email
        if not email:
            new_email = (getattr(row, "email", "") or "").strip()
        return new_name, new_email


class IdentityResolver:
    """Strategy + Chain-of-Responsibility coordinator.

    Walks the SSO-header → session-cookie → basic-auth chain in
    order and stops at the first strategy that yields a username,
    then hydrates display_name / email when missing. Constructor-
    injected strategies make the chain fully exercisable in tests
    without ``monkeypatch`` or import hacks.
    """

    def __init__(
        self,
        sso: _SsoHeaderStrategy | None = None,
        cookie: _SessionCookieStrategy | None = None,
        basic: _BasicAuthStrategy | None = None,
        hydrator: _UserStoreHydrator | None = None,
    ) -> None:
        self._sso = sso or _SsoHeaderStrategy()
        self._cookie = cookie or _SessionCookieStrategy()
        self._basic = basic or _BasicAuthStrategy()
        self._hydrator = hydrator or _UserStoreHydrator()

    def resolve(self, handler: Any) -> dict[str, Any]:
        """Return the identity envelope shape pinned by
        ``getAuthIdentity`` in ``contracts/api/openapi.yaml``."""
        user, name, email, groups = self._sso.resolve(handler.headers)
        if not user:
            user = self._cookie.resolve(handler)
        if not user:
            user = self._basic.resolve(handler.headers)
        if user and not name:
            name, email = self._hydrator.hydrate(user, name, email)
        return {
            "authenticated": bool(user),
            "user": user,
            "display_name": name or user,
            "email": email,
            "groups": groups,
        }


class AuthGetRoutes(RouteModule):
    """Auth-tag GET routes — identity, config, modes, OIDC providers,
    per-service policies. The Router auto-discovers and instantiates
    this class at startup, then walks tagged methods for
    registration.

    Constructor-inject ``identity_resolver`` and the
    ``AuthConfigService`` factory so unit tests can swap either
    without ``monkeypatch``. Production passes nothing — defaults
    materialize the production wiring.
    """

    def __init__(
        self,
        identity_resolver: IdentityResolver | None = None,
    ) -> None:
        self._identity_resolver = identity_resolver or IdentityResolver()

    @classmethod
    def _auth_config_service(cls) -> AuthConfigService:
        """Factory @classmethod — production builds a fresh
        ``AuthConfigService`` per request (cheap; reads a YAML file
        on construction). Tests monkey-patch this classmethod to
        return a stub instead of patching the import inside each
        method body.
        """
        return AuthConfigService()

    @get("/api/auth/identity")
    def handle_auth_identity(self, handler: Any) -> None:
        """Resolved caller identity (SSO headers → session cookie
        → Basic-auth header), with display_name + email hydrated
        from the user store when missing.

        Resolution order matches the auth policy: Authelia /
        Authentik forwarded headers first, then the session cookie
        (for direct-access deployments where the user signed in
        with the controller's in-page form), then Basic auth (when
        the browser is supplying credentials on every request via
        the WWW-Authenticate popup). Without all three branches the
        avatar in the top-right falls back to ``??`` even though
        the operator is authenticated — exactly the symptom that
        drove this branch to grow over time.
        """
        handler._json_response(
            HTTPStatus.OK, self._identity_resolver.resolve(handler),
        )

    @get("/api/auth/config")
    def handle_auth_config(self, handler: Any) -> None:
        """Current auth configuration snapshot — mode,
        internet_exposed, oidc provider + config, per-service
        overrides, and the controller-level app_auth posture.
        """
        handler._json_response(
            HTTPStatus.OK,
            self._auth_config_service().get_current_config(),
        )

    @get("/api/auth/modes")
    def handle_auth_modes(self, handler: Any) -> None:
        """Catalog of supported auth modes for the dashboard's
        mode-chooser tiles. Each entry carries display name,
        description, controller-side strategy, gateway-auth
        boolean, and provider service id.
        """
        handler._json_response(
            HTTPStatus.OK,
            {"modes": self._auth_config_service().get_auth_modes()},
        )

    @get("/api/auth/oidc-providers")
    def handle_auth_oidc_providers(self, handler: Any) -> None:
        """OIDC upstream provider catalog (local, google,
        microsoft, keycloak, ...) with each provider's required
        config fields.
        """
        handler._json_response(
            HTTPStatus.OK,
            {"providers": self._auth_config_service().get_oidc_providers()},
        )

    @get("/api/auth/service-policies")
    def handle_auth_service_policies(self, handler: Any) -> None:
        """Resolved per-service auth policy (public / protected /
        native) for every service the controller knows about, with
        the source (``contract`` vs ``profile``) attached.
        """
        handler._json_response(
            HTTPStatus.OK,
            {"services": self._auth_config_service().get_service_policies()},
        )


__all__ = [
    "AuthGetRoutes",
    "IdentityResolver",
]
