"""Media-integrity GET routes (ADR-0007 Phase 2 wave 5).

Two read-only routes lifted off the legacy ``handlers_get.handle()``
``elif`` chain (the ``_media_integrity_handlers.matches_get`` /
``dispatch_get`` branch at handlers_get.py:472-477):

* ``GET /api/media-integrity/status``   — last-pass enforce + reconcile
  outcome snapshot. Drives the Security-tab "media integrity" card.
* ``GET /api/media-integrity/progress`` — poll-able snapshot of the
  in-flight reconcile/enforce pass. The UI polls this every few
  hundred ms while a "Run reconcile" / "Enforce config" button is
  depressed so the user sees a spinner instead of a silent network
  request.

Per ADR-0007 Phase 2's "lift the body OR call the helper — agent's
choice" rule, both bodies here delegate directly to the existing
``MediaIntegrityService`` methods (``status`` / ``get_progress``);
the legacy dispatcher's wrapper logic (service-not-configured 503,
authenticated-actor 401, path branching) is preserved verbatim but
re-expressed as constructor-injected collaborators on the route
module rather than module-level helpers in the legacy handler.

POST counterparts (``reconcile``, ``enforce-config``,
``resolve-review``) stay on the legacy ``handlers_post.py`` path
until ADR-0007's POST-domain wave migrates them.

Service wiring
--------------
The ``MediaIntegrityService`` is constructed at controller-serve
boot (see ``cli/commands/controller_serve.py``) and stashed on the
module-level ``_instance`` of ``MediaIntegrityHandlers``. This
route module reads the live service via a constructor-injected
provider object that defaults to the production-wired provider.
Tests inject a stub provider so the route is exercised end-to-end
without going through the legacy handler at all.

Auth posture
------------
* ``ControllerAPIHandler._check_auth`` runs BEFORE the dispatcher
  and gates HTTP-Basic / session / proxy-trust at the wire.
* Inside the route, the actor is resolved from the request via
  ``HandlerActorResolverFactory`` (session cookie first, then the
  trusted-proxy ``Remote-User`` header) so the 401 fallback fires
  for an unauthenticated actor regardless of how the wire-level
  auth resolved.
* Admin is NOT required: read-only Security-tab observers see the
  status card too. Mutations (POSTs) live on the legacy chain and
  are admin-gated there.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services.media_integrity_handlers import (
    _instance as _legacy_handler,
)
from media_stack.api.services.security_get_deps import (
    HandlerActorResolverFactory,
)
from media_stack.core.auth.authz import Actor, AuthorizationError


# Path constants — single source-of-truth so the legacy handler's
# ``_GET_EXACT`` and the OpenAPI spec stay in lockstep with the
# router registrations. Named at module scope so the
# ``json-keys-outside-serializer`` ratchet sees one identifier per
# route instead of inline literals at each emit site.
_PATH_STATUS = "/api/media-integrity/status"
_PATH_PROGRESS = "/api/media-integrity/progress"


class _LegacyHandlerServiceProvider:
    """Default ``MediaIntegrityService`` provider — reads the live
    instance off the module-level ``MediaIntegrityHandlers`` singleton
    that ``controller_serve`` populates via ``set_service`` at boot.

    Returning ``None`` lets the route emit a 503 with the same shape
    the legacy handler used pre-migration. The lookup is wrapped in
    a tiny class (rather than a free function) so the route's only
    dependency on module state is constructor-injected and the
    ``loose-functions`` ratchet sees no top-level helper.
    """

    def get(self) -> Any:
        return getattr(_legacy_handler, "_service", None)


class MediaIntegrityGetRoutes(RouteModule):
    """Media-integrity-tag GET routes — ``status`` + ``progress``.

    Constructor-inject the service provider + actor-resolver factory
    so tests can swap each independently. Production passes nothing —
    defaults materialize the production wiring (live service from
    the legacy handler's singleton; default actor resolver wired
    against the session/proxy stack).
    """

    def __init__(
        self,
        service_provider: _LegacyHandlerServiceProvider | None = None,
        actor_resolver: HandlerActorResolverFactory | None = None,
    ) -> None:
        self._service_provider = (
            service_provider
            if service_provider is not None
            else _LegacyHandlerServiceProvider()
        )
        self._actor_resolver = (
            actor_resolver if actor_resolver is not None
            else HandlerActorResolverFactory()
        )

    @get(_PATH_STATUS)
    def handle_status(self, handler: Any) -> None:
        """Last-pass enforce + reconcile snapshot.

        Returns the ``{last_enforce, last_reconcile, policy_version,
        servarr_adapters, bazarr_present, missing_api_keys}`` shape
        documented in OpenAPI ``getMediaIntegrityStatus``. Empty pass
        history is normal at boot — every leaf is initialized to
        ``{"ts": "", "detail": {}}`` so the UI never sees a missing
        key.
        """
        service = self._resolve_service_or_unavailable(handler)
        if service is None:
            return
        if not self._require_authenticated(handler):
            return
        handler._json_response(HTTPStatus.OK, service.status())

    @get(_PATH_PROGRESS)
    def handle_progress(self, handler: Any) -> None:
        """Poll-able snapshot of the in-flight reconcile/enforce pass.

        Returns ``{"in_progress": false}`` when idle, or
        ``{"in_progress": true, ...}`` with the current pass's
        progress dict merged in. The UI polls this on a tight cadence
        while a button is depressed; the route stays cheap so the
        polling cost is negligible.
        """
        service = self._resolve_service_or_unavailable(handler)
        if service is None:
            return
        if not self._require_authenticated(handler):
            return
        handler._json_response(HTTPStatus.OK, service.get_progress())

    # -- shared gating ------------------------------------------------

    def _resolve_service_or_unavailable(self, handler: Any) -> Any:
        """Return the live service, or write a 503 + return ``None``.

        Mirrors the legacy ``dispatch_get``'s first guard: if no
        service has been wired (controller still bootstrapping, or
        the policy contract was missing at boot and the subsystem
        was disabled), every GET fails closed with a 503.
        """
        service = self._service_provider.get()
        if service is None:
            handler._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "media-integrity service not configured"},
            )
            return None
        return service

    def _require_authenticated(self, handler: Any) -> bool:
        """Resolve the actor and emit a 401 if unauthenticated.

        Returns ``True`` when the route should proceed.
        ``HandlerActorResolverFactory.resolve`` is allowed to raise
        ``AuthorizationError`` when the request has no usable
        identity — we narrow the catch to that one type so any
        unexpected exception (e.g. a corrupted session-store row)
        propagates to the dispatcher's 500 handler instead of being
        swallowed silently as a 401.
        """
        try:
            actor = self._actor_resolver.resolve(handler)
        except AuthorizationError:
            self._write_unauthorized(handler)
            return False
        if not self._is_authenticated(actor):
            self._write_unauthorized(handler)
            return False
        return True

    def _is_authenticated(self, actor: Actor) -> bool:
        """A resolved actor with a non-empty username is authenticated.

        We deliberately do NOT require ``is_admin`` here: Security-tab
        observers with read-only role still see the media-integrity
        status card. Matches the legacy ``_is_authenticated`` helper
        exactly.
        """
        return bool(actor and actor.is_authenticated)

    def _write_unauthorized(self, handler: Any) -> None:
        handler._json_response(
            HTTPStatus.UNAUTHORIZED,
            {"error": "authentication required"},
        )


__all__ = [
    "MediaIntegrityGetRoutes",
    "_LegacyHandlerServiceProvider",
]
