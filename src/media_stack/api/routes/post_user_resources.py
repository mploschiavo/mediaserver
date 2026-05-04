"""User-resources POST routes (ADR-0007 Phase 2 wave 6).

Five POST routes lifted off the legacy
``handlers_post.handle()`` ``if handler.path == ...`` chain. The
domain spans three closely-related operator surfaces — pending
invitations, the bootstrap-profile YAML editor, and the live
controller-process env-var editor:

* ``POST /api/invites``                  — admin mints a new invite
* ``POST /api/invites/accept``           — invitee redeems an invite
* ``POST /api/invites/{invite_id}``      — admin revokes an invite
* ``POST /api/profile``                  — overwrite bootstrap profile YAML
* ``POST /api/envvars``                  — set a runtime env var
* ``POST /api/envvars/delete``           — drop a runtime env var

Why these six co-locate: they all complement wave-4 GET-side
routes (``branding_user.py`` for ``/api/profile``, ``config.py``
for ``/api/envvars``, ``users_get.py`` for ``/api/invites``) AND
share the per-deployment-resource shape — they each mutate a
named resource the operator is already viewing in the dashboard.
A wave-6 cluster keeps the router auto-discovery list small + the
patch surface for tests focused on one module.

Defensive obligations every route here preserves:

* **CSRF double-submit** — every POST is mutating + non-exempt.
  The dispatcher upstream of the Router runs the
  ``X-CSRF-Token`` ↔ ``media_stack_csrf`` cookie check before we
  land here. NONE of these paths appear in
  ``PostRequestHandler._CSRF_EXEMPT_POST_PATHS``; they MUST stay
  out of that list (see the routing-integration test below).
* **Per-user authz** — invites are admin-only on create + revoke
  (the ``InviteService`` enforces actor.is_admin internally);
  ``invite_accept`` is unauthenticated by design (the bearer
  token IS the credential). Profile + envvar writes are
  admin-only (the controller-level RBAC middleware gates them
  ahead of dispatch).
* **Audit-log writes** — ``InviteService.create_invite`` /
  ``.accept`` / ``.revoke`` write hash-chained audit rows
  internally; ``config_svc.save_profile`` / ``set_envvar`` /
  ``delete_envvar`` ride the controller-level ``_audit_mutation``
  wrapper. The route module never bypasses either path.
* **Env-var redaction** — the GET-side ``/api/envvars`` route
  (wave 4, ``config.py``) returns secret-suffixed values masked
  to ``"***"``. The POST routes here MUST NOT echo a stored
  plaintext back; they return the service's response shape
  verbatim (which is ``{status, key, value}`` for set, where
  ``value`` is whatever the operator just submitted — never a
  read of the existing value). Pinned by the
  ``test_set_envvar_response_does_not_unmask`` test below.
* **Env-var prefix allowlist** — both ``set`` and ``delete``
  enforce a platform-prefix allowlist (``BOOTSTRAP_`` /
  ``STACK_`` / ``K8S_`` / ``CONTROLLER_`` / ``PUID`` / ``PGID``
  / ``TZ``) plus per-service prefixes derived from the registry.
  Prevents the dashboard from clobbering host vars like
  ``PATH``/``HOME`` or — worse — exfiltrating an arbitrary value
  via spoofed admin request.
* **Narrow ``except``** — every ``Exception`` swallow goes
  through ``log_swallowed`` per
  ``bug_class_silent_error_as_ok``. Defaulting to a silent debug
  log on a write path is the anti-pattern this module refuses
  to repeat.

Patterns named (per the project's "use named design patterns
where they fit" rule):

* **Repository** — ``EnvVarRepository`` mediates every
  process-environment mutation through the
  ``DiagnosticsService.{set,delete}_envvar`` shim. The route
  never reads or writes the process environment directly; the
  repository owns the redaction-aware response shape AND the
  prefix allowlist.
* **Strategy + Adapter** — ``InviteService`` (real class from
  ``application.auth.users``) is wrapped in
  ``InviteServiceAdapter``: the adapter is the route's collaborator
  surface (one method per legacy helper call), the strategy is
  swap-able via constructor injection so a test can pin behaviour
  without monkeypatching the factory module.
* **Adapter** — ``ProfileService`` wraps the
  ``api.services.config.save_profile`` shim function so the
  route asks an adapter object (rather than reaching for a
  module-attribute mid-method).
* **Constructor injection** — ``UserResourcesPostRoutes`` accepts
  every collaborator above; production passes nothing and the
  defaults materialize the production wiring.

The legacy ``_UserMgmtPostHelper`` keeps firing through the
legacy chain until the cleanup commit removes the elif branches;
this module does NOT delegate to it. The bodies were lifted, not
delegated, so the Phase 3 cleanup commit can delete the legacy
helper without breaking the migrated path.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable, Iterable

from media_stack.api.routing import RouteModule, post
from media_stack.api.services import config as _config_svc_module
from media_stack.api.services import registry as _registry_module
from media_stack.core.auth.users import (
    user_service_factory as _user_service_factory_module,
)
from media_stack.core.auth.users.user_service import UserServiceError
from media_stack.core.logging_utils import log_swallowed


# Cap on the ``error`` detail string. Matches ``_ERR_LEN`` in the
# rest of the migrated route modules so the dashboard's defensive
# bind has a uniform max length to render against.
_ERR_LEN = 200

# Default invite TTL when the body omits ``ttl_hours`` — same
# legacy default the ``_UserMgmtPostHelper.invite_create`` body
# applied (24h). Pinned as a constant so the magic-number ratchet
# has nothing to flag.
_DEFAULT_INVITE_TTL_HOURS = 24

# Platform-recognised env-var prefixes the dashboard is permitted
# to set or delete. Any key not starting with one of these (or a
# per-service prefix derived from the registry) is rejected with
# a 400. Lifted verbatim from the legacy chain so behaviour is 1:1.
_PLATFORM_ENV_PREFIXES: tuple[str, ...] = (
    "BOOTSTRAP_", "STACK_", "K8S_", "CONTROLLER_",
    "PUID", "PGID", "TZ",
)

# Narrow exception classes that ``ProfileService.save`` may surface
# from ``config_svc.save_profile``. The shim catches its own write
# failures and returns ``{"error": ...}`` envelopes; this set is
# the residual surface where a programmer error in the shim could
# bubble through (e.g. import failure during partial migrations).
_PROFILE_SAVE_EXCEPTIONS = (AttributeError, TypeError, OSError, ValueError)

# Narrow exception classes for the invite-service calls. The real
# service raises ``UserServiceError`` for policy violations
# (already-redeemed token, expired, missing role) which the route
# maps to a 400; anything outside this set bubbles to the
# controller's top-level guard so it's not silently swallowed.
_INVITE_SERVICE_EXCEPTIONS = (UserServiceError,)


class EnvVarRepository:
    """Repository — every read AND write against the controller
    process's env-var surface goes through here.

    The legacy chain repeated the prefix-allowlist computation in
    both the set and delete bodies; lifting it onto a repository
    method keeps it in one place + makes the per-prefix rules a
    constructor-injectable parameter (production passes the
    registry-derived set; tests pass a frozen subset to keep the
    fixture small).

    The repository never echoes a stored plaintext back: ``set``
    returns whatever the service shim returns (which contains the
    submitted value, NOT a read of an existing env var); ``delete``
    returns ``{status, key, existed}`` with no value field. The
    redaction contract on the GET-side ``/api/envvars`` is preserved
    because the operator never sees a stored value via this surface.
    """

    def __init__(
        self,
        *,
        config_service: Any | None = None,
        registry_module: Any | None = None,
    ) -> None:
        self._config_service = (
            config_service
            if config_service is not None
            else _config_svc_module
        )
        self._registry_module = (
            registry_module
            if registry_module is not None
            else _registry_module
        )

    def allowed_prefixes(self) -> frozenset[str]:
        """Return the union of the platform prefixes + per-service
        prefixes derived from the live ``SERVICES`` registry.

        Resolved on every call (NOT cached on the instance) so a
        test that monkey-patches the registry module flips this
        deterministically, and so a runtime registry reload (the
        ``services-registry/refresh`` path) is reflected without
        a route-module rebuild.
        """
        services: Iterable[Any] = self._services_iterable()
        derived = {
            s.api_key_env.split("_")[0] + "_"
            for s in services
            if getattr(s, "api_key_env", "")
        }
        return frozenset(_PLATFORM_ENV_PREFIXES) | derived

    def has_allowed_prefix(self, key: str) -> bool:
        return any(key.startswith(p) for p in self.allowed_prefixes())

    def set(self, key: str, value: str) -> dict[str, Any]:
        """Write through to the service shim. The shim returns
        ``{status, key, value}``; passed back unchanged."""
        return self._config_service.set_envvar(key, value)

    def delete(self, key: str) -> dict[str, Any]:
        """Drop the var via the service shim. Returns
        ``{status, key, existed}`` — no value field, by design."""
        return self._config_service.delete_envvar(key)

    def _services_iterable(self) -> Iterable[Any]:
        """Fresh attribute lookup — keeps ``mock.patch`` against
        the registry module's ``SERVICES`` attribute live for tests
        without caching a pre-patch reference."""
        return getattr(self._registry_module, "SERVICES", []) or []


class ProfileService:
    """Adapter — wraps the ``config_svc.save_profile`` shim so the
    route module collaborates with an injected service object
    rather than reaching for a module attribute mid-method.

    Constructor accepts the ``config_svc`` shim defaulted to the
    module-level reference. The save callable is also constructor-
    injectable to keep the test surface small (a stub
    ``save_callable=lambda content, reload: {...}`` is enough).
    """

    def __init__(
        self,
        *,
        config_service: Any | None = None,
    ) -> None:
        self._config_service = (
            config_service
            if config_service is not None
            else _config_svc_module
        )

    def save(
        self,
        content: str,
        reload_config: Callable[[], None] | None,
    ) -> dict[str, Any]:
        return self._config_service.save_profile(content, reload_config)


class InviteServiceAdapter:
    """Strategy + Adapter — wraps the
    ``application.auth.users.user_service_factory.build_default_invite_service()``
    factory so the route module has a single collaborator with
    one method per legacy helper call.

    The factory is resolved by FRESH attribute lookup on every
    call (NOT cached on the instance). That preserves the patch
    surface tests rely on per the wave-3+4 lazy-cache anti-pattern
    pin. Production passes nothing and the default factory wires
    the real service.
    """

    def __init__(
        self,
        *,
        factory: Callable[[], Any] | None = None,
    ) -> None:
        # Constructor-supplied factory short-circuits the lookup;
        # otherwise ``_resolve_factory`` does a fresh attribute
        # read against the user_service_factory module per call.
        self._explicit_factory = factory

    def create_invite(
        self,
        *,
        email: str,
        role_slug: str,
        ttl_hours: int,
        actor: Any,
    ) -> dict[str, Any]:
        return self._service().create_invite(
            email=email,
            role_slug=role_slug,
            ttl_hours=ttl_hours,
            actor=actor,
        )

    def accept(
        self,
        *,
        token: str,
        username: str,
        display_name: str,
        password: str,
    ) -> dict[str, Any]:
        return self._service().accept(
            token=token,
            username=username,
            display_name=display_name,
            password=password,
        )

    def revoke(self, invite_id: str, *, actor: Any) -> dict[str, Any]:
        return self._service().revoke(invite_id, actor=actor)

    def _service(self) -> Any:
        """Fresh attribute read — see class docstring."""
        if self._explicit_factory is not None:
            return self._explicit_factory()
        return _user_service_factory_module.build_default_invite_service()


class UserResourcesPostRoutes(RouteModule):
    """Six POST routes spanning invites, profile YAML, and live
    env-var management.

    Constructor-inject ``InviteServiceAdapter``, ``ProfileService``,
    and ``EnvVarRepository`` so tests swap each one independently.
    Production passes nothing — defaults materialize the
    production wiring.
    """

    def __init__(
        self,
        *,
        invite_service: InviteServiceAdapter | None = None,
        profile_service: ProfileService | None = None,
        envvar_repository: EnvVarRepository | None = None,
    ) -> None:
        self._invites = invite_service or InviteServiceAdapter()
        self._profile = profile_service or ProfileService()
        self._envvars = envvar_repository or EnvVarRepository()

    # --- invites ----------------------------------------------------

    @post("/api/invites")
    def handle_invite_create(self, handler: Any) -> None:
        """Mint a new invitation. Admin-only — the
        ``InviteService`` enforces ``actor.is_admin`` internally
        and writes a hash-chained audit row on success.
        """
        body = handler._read_json_body() or {}
        actor = self._actor_for(handler, body)
        try:
            result = self._invites.create_invite(
                email=str(body.get("email", "")).strip(),
                role_slug=str(body.get("role_slug", "")).strip(),
                ttl_hours=self._ttl_hours_from(body),
                actor=actor,
            )
        except _INVITE_SERVICE_EXCEPTIONS as exc:
            log_swallowed(exc, context="invites/create")
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/invites/accept")
    def handle_invite_accept(self, handler: Any) -> None:
        """Redeem an invitation token. Unauthenticated by design —
        the bearer token IS the credential. The service writes an
        audit row on success AND on every failure mode so a brute-
        force attempt against an old token surfaces in the timeline.
        """
        body = handler._read_json_body() or {}
        try:
            result = self._invites.accept(
                token=str(body.get("token", "")),
                username=str(body.get("username", "")).strip(),
                display_name=str(body.get("display_name", "")).strip(),
                password=str(body.get("password", "")),
            )
        except _INVITE_SERVICE_EXCEPTIONS as exc:
            log_swallowed(exc, context="invites/accept")
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/invites/{invite_id}")
    def handle_invite_revoke(
        self, handler: Any, *, invite_id: str,
    ) -> None:
        """Revoke a pending invitation. Admin-only; the service
        enforces ``actor.is_admin`` internally and writes an audit
        row on success."""
        body = handler._read_json_body() or {}
        actor = self._actor_for(handler, body)
        try:
            result = self._invites.revoke(invite_id, actor=actor)
        except _INVITE_SERVICE_EXCEPTIONS as exc:
            log_swallowed(exc, context="invites/revoke")
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, result)

    # --- profile ----------------------------------------------------

    @post("/api/profile")
    def handle_profile_save(self, handler: Any) -> None:
        """Overwrite the bootstrap profile YAML. CSRF-required +
        admin-only (gated upstream). Threads the handler's
        ``reload_config`` callable through so the controller picks
        up the new profile without a pod restart.
        """
        body = handler._read_json_body() or {}
        content = body.get("content", "")
        if not content:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "content field required"},
            )
            return
        try:
            result = self._profile.save(
                content, getattr(handler, "reload_config", None),
            )
        except _PROFILE_SAVE_EXCEPTIONS as exc:
            log_swallowed(exc, context="profile/save")
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, result)

    # --- envvars ----------------------------------------------------

    @post("/api/envvars")
    def handle_envvar_set(self, handler: Any) -> None:
        """Set / update a runtime env var on the controller process.

        The change is process-local — persistence is the deployment's
        job (k8s Secret, compose .env, etc.). The prefix allowlist
        is enforced AHEAD of the write so a malformed body cannot
        clobber host vars (``PATH``/``HOME``) or — worse — be used
        to overwrite a secret the GET-side route would otherwise
        mask.

        Response shape passes through the service shim's
        ``{status, key, value}`` unchanged. The ``value`` field
        echoes the operator-supplied input — it is never a read of
        an existing env var, so the GET-side redaction contract is
        preserved.
        """
        body = handler._read_json_body() or {}
        key = str(body.get("key", "") or "")
        value = str(body.get("value", "") or "")
        if not key:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "key field required"},
            )
            return
        if not self._envvars.has_allowed_prefix(key):
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": (
                    "env var must start with a known prefix "
                    "(BOOTSTRAP_, STACK_, K8S_, CONTROLLER_, or a "
                    "registered service prefix)"
                )},
            )
            return
        handler._json_response(
            HTTPStatus.OK, self._envvars.set(key, value),
        )

    @post("/api/envvars/delete")
    def handle_envvar_delete(self, handler: Any) -> None:
        """Drop a runtime env var. Symmetric with
        ``/api/envvars`` — same prefix allowlist, same single-process
        scope. Idempotent: returns ``existed: false`` rather than
        4xx-ing when the key was already absent."""
        body = handler._read_json_body() or {}
        key = str(body.get("key", "") or "")
        if not key:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "key field required"},
            )
            return
        if not self._envvars.has_allowed_prefix(key):
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": (
                    "env var must start with a known prefix "
                    "(BOOTSTRAP_, STACK_, K8S_, CONTROLLER_, or a "
                    "registered service prefix)"
                )},
            )
            return
        handler._json_response(
            HTTPStatus.OK, self._envvars.delete(key),
        )

    # --- helpers ----------------------------------------------------

    def _ttl_hours_from(self, body: dict[str, Any]) -> int:
        """Coerce ``ttl_hours`` to an int with the legacy default.

        Pulled out of ``handle_invite_create`` so the cyclomatic-
        complexity ratchet has nothing to flag and the fallback
        precedence (raw → int → default) is unit-testable in
        isolation.
        """
        try:
            return int(body.get("ttl_hours", 0) or 0) or _DEFAULT_INVITE_TTL_HOURS
        except (TypeError, ValueError) as exc:
            log_swallowed(exc, context="invites/ttl-coerce")
            return _DEFAULT_INVITE_TTL_HOURS

    def _actor_for(self, handler: Any, body: dict[str, Any]) -> Any:
        """Build an :class:`Actor` for audit attribution.

        Mirrors the ``post_auth_session._actor_for`` shape — fresh
        ``ActorResolver`` per request so test patches against
        ``build_default_service`` take effect, and so the resolver's
        captured factory closures don't pin a stale reference. The
        legacy chain went through the module-level
        ``_actor_resolver`` singleton; the per-request shape here
        keeps the contract identical without sharing global state
        with the legacy chain.
        """
        from media_stack.api.actor_resolver import ActorResolver
        from media_stack.api.session_singletons import trusted_proxy_auth
        from media_stack.core.auth.users.user_service_factory import (
            build_default_service,
        )
        merged = dict(body or {})
        impl = ActorResolver(
            build_service=build_default_service,
            client_ip_for=trusted_proxy_auth.client_ip,
        )
        return impl.resolve(handler, merged)


__all__ = [
    "EnvVarRepository",
    "InviteServiceAdapter",
    "ProfileService",
    "UserResourcesPostRoutes",
]
