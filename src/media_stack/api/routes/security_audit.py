"""Security-audit GET routes (ADR-0007 Phase 2 wave 4).

Five read-only routes lifted off the legacy ``handlers_get.handle()``
``elif`` chain. The domain bundles three OpenAPI tags
(``Health`` / ``AuditLog`` / ``Networking`` / ``PasswordPolicy``)
because they all share the same security-sensitive characteristic:
each surface either references credentials, audit-log integrity,
or network reachability hints that an attacker could leverage to
fingerprint the deployment. Co-locating them in one module makes
the redaction + safe-detail discipline auditable from a single file
rather than scattered across four one-route modules.

Spec parity:

* ``/api/credentials``           -> ``getCredentialsReport``       (Health tag)
* ``/api/password-policy``       -> ``getPasswordPolicy``          (PasswordPolicy tag)
* ``/api/password-propagation``  -> ``getPasswordPropagationReport`` (Health tag)
* ``/api/audit-log/verify``      -> ``verifyAuditLogChain``        (AuditLog tag)
* ``/api/access-urls``           -> ``getAccessUrls``              (Networking tag)

Security posture:

* The ``HealthService.probe_credentials`` + ``probe_password_propagation``
  collaborators return **status strings only** (``ok`` / ``fail`` /
  ``no_key`` / ``not_propagated`` / ``error`` / ``n/a``). Neither
  echoes the raw API key or the admin password — the returned
  payload is per-service status keyed by service id. The route
  passes those payloads through verbatim. As a defence-in-depth,
  the ``redact_api_key_map`` helper from
  ``media_stack.domain.auth.secret_redaction`` is imported here too;
  the tests assert that no key-shaped string appears in the
  response body even though the service is the canonical author of
  the safe shape.
* ``/api/audit-log/verify`` runs the audit-log hash-chain check.
  The legacy body wraps the call in a broad ``except Exception``;
  this module narrows the swallow to the concrete exceptions the
  ``AuditLog`` collaborator can raise (``OSError`` for the file,
  ``ValueError`` for hash-format drift) and **logs every swallow**
  via ``log_swallowed`` — security-relevant paths must never silently
  drop an error. A truly unexpected ``RuntimeError`` will propagate
  to the dispatcher's 500 handler.
* ``/api/access-urls`` reads the ``Host`` header off the request,
  which is attacker-controlled. The legacy body swallowed
  ``AttributeError`` on the read; we preserve that, but route the
  swallow through the central ``log_swallowed`` so a truly broken
  handler shows up in the logs instead of failing closed silently.
* CSRF: every route here is GET, so the controller's CSRF
  double-submit middleware is bypassed by design — the
  X-CSRF-Token requirement only fires on mutations. No new auth
  check is added by this module beyond what the dispatcher /
  upstream proxy already enforces; the goal is a 1:1 lift.

Implementation patterns:

* **Strategy** — ``_CredentialSourceStrategy`` resolves which
  service-state collaborator to call for the credentials /
  propagation paths. Default strategy delegates to the module-level
  ``health_svc`` aliases (matching the legacy bodies exactly);
  tests inject a ``_StubCredentialSource`` to drive the response
  shape without monkeypatching module imports.
* **Repository** — ``_AuditChainRepository`` wraps the
  ``UserService._audit.verify_chain()`` call so the route never
  touches private attributes directly and the test can swap the
  repository with a stub that returns canned ``(ok, detail)`` pairs
  or raises one of the documented failure modes.
* **Constructor injection** — ``SecurityAuditGetRoutes`` accepts
  the credential source, the audit repository, and the access-URL
  discovery factory, defaulting each to the production wiring.
  Tests build the class with stubs and the routes are exercised
  through the production ``Router`` via ``RouteDispatchHarness``.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import health as health_svc
from media_stack.api.services.access_urls import AccessUrlDiscovery
from media_stack.api.services.password_policy_config import (
    PasswordPolicyConfig,
)
from media_stack.core.auth.users.user_service_factory import (
    build_default_service,
)
from media_stack.core.logging_utils import log_swallowed
from media_stack.domain.auth.secret_redaction import redact_api_key_map

_log = logging.getLogger("media_stack")

# Cap on the ``error`` detail string when the audit-log verification
# raises. Matches the ``_ERR_LEN`` convention the legacy chain uses
# across the rest of ``handlers_get.py`` so the error envelope shape
# stays consistent across migrated and not-yet-migrated routes.
_ERR_LEN = 99


class _CredentialSourceStrategy:
    """Strategy: resolve credential + password-propagation reports.

    The default implementation delegates to the module-level
    ``health_svc.probe_credentials`` /
    ``health_svc.probe_password_propagation`` aliases — same calls
    the legacy ``handlers_get.handle()`` body made. Tests pass a
    stub instance so the route is exercised end-to-end without
    touching the real ``HealthService`` (which would attempt
    network probes against every configured service on import).
    """

    def credentials(self) -> dict[str, Any]:
        return health_svc.probe_credentials()

    def password_propagation(self) -> dict[str, Any]:
        return health_svc.probe_password_propagation()


class _AuditChainRepository:
    """Repository: wrap the audit-log hash-chain verification call.

    Hides the legacy ``build_default_service()._audit.verify_chain()``
    private-attribute reach. Lets tests swap the repository for a
    fixture without touching the user-service factory or the
    ``AuditLog`` infrastructure module.
    """

    def __init__(
        self,
        service_factory: Callable[[], Any] = build_default_service,
    ) -> None:
        self._service_factory = service_factory

    def verify(self) -> tuple[bool, str]:
        """Run the chain check and return ``(ok, detail)``.

        ``detail`` is the human-readable note emitted by
        ``AuditLog.verify_chain`` when the chain is broken; empty
        when the chain is intact (and the route swaps in
        ``"hash chain intact"`` to keep the on-the-wire shape the
        UI binds against).
        """
        service = self._service_factory()
        return service._audit.verify_chain()


class SecurityAuditGetRoutes(RouteModule):
    """Five security-audit GET routes — credentials report,
    password policy, password propagation, audit-log chain
    verification, access-URL discovery.

    Constructor-inject the three collaborators so tests can swap
    each one independently. Production passes nothing — defaults
    materialize the production wiring.
    """

    def __init__(
        self,
        credential_source: _CredentialSourceStrategy | None = None,
        audit_chain_repository: _AuditChainRepository | None = None,
        access_url_factory: Callable[[str], AccessUrlDiscovery] | None = None,
        password_policy_factory: Callable[[], PasswordPolicyConfig] | None = None,
    ) -> None:
        self._credentials = credential_source or _CredentialSourceStrategy()
        self._audit_chain = (
            audit_chain_repository or _AuditChainRepository()
        )
        self._access_url_factory = (
            access_url_factory
            or (lambda host: AccessUrlDiscovery(host_ip_hint=host))
        )
        self._password_policy_factory = (
            password_policy_factory or PasswordPolicyConfig
        )

    @get("/api/credentials")
    def handle_credentials(self, handler: Any) -> None:
        """Return per-service credential-validation status.

        The collaborator returns ``{"credentials": {svc: status},
        "ok": int, "total": int}`` where each ``status`` is one of
        ``ok`` / ``fail`` / ``no_key`` / ``error`` / ``disabled``.
        Raw API keys never appear in the payload — the probe
        validates the key and reports the boolean outcome only.
        """
        handler._json_response(
            HTTPStatus.OK, self._credentials.credentials(),
        )

    @get("/api/password-propagation")
    def handle_password_propagation(self, handler: Any) -> None:
        """Read-only check that the stack admin password has been
        propagated to each service's local user record.

        Distinct from ``/api/credentials``: this endpoint only
        reads metadata via the API key (today: Jellyfin's
        ``/Users`` ``HasPassword`` boolean). It NEVER attempts a
        login — that path historically caused noisy
        ``InvalidLoginAttemptCount`` increments on every miss.
        """
        handler._json_response(
            HTTPStatus.OK, self._credentials.password_propagation(),
        )

    @get("/api/password-policy")
    def handle_password_policy(self, handler: Any) -> None:
        """Return the active password policy + bounds for the
        controller's password-management UI.

        ``policy`` carries the live values (min_length, the four
        character-class booleans, history length, max age, lockout
        threshold, lockout window). ``bounds`` carries per-field
        min/max/default for the UI's slider inputs. Mutations go
        through POST ``/api/password-policy`` and require sudo
        (see ``server.py::_DEFAULT_SUDO_PATHS``); this GET surface
        is read-only.
        """
        cfg = self._password_policy_factory()
        handler._json_response(HTTPStatus.OK, {
            "policy": cfg.load_values(),
            "bounds": cfg.bounds(),
        })

    @get("/api/audit-log/verify")
    def handle_audit_log_verify(self, handler: Any) -> None:
        """Verify the audit-log hash chain end-to-end.

        Returns ``{"ok": bool, "detail": str}``. ``detail`` is
        either the chain-break diagnostic string from
        ``AuditLog.verify_chain`` or the canned
        ``"hash chain intact"`` placeholder when the verifier
        returned an empty detail string (chain healthy). Errors
        from the verification call are surfaced as a 500 with a
        short error string; we narrow the catch to the documented
        failure modes (``OSError`` on the file, ``ValueError`` on
        format drift) and log every swallow.
        """
        try:
            ok, detail = self._audit_chain.verify()
        except (OSError, ValueError) as exc:
            log_swallowed(exc, context="audit-log/verify")
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, {
            "ok": ok,
            "detail": detail or "hash chain intact",
        })

    @get("/api/access-urls")
    def handle_access_urls(self, handler: Any) -> None:
        """Surface clickable URLs the operator can use to reach
        the stack — direct LAN IP, gateway subdomain, and the
        path-prefixed apps host. Used by the post-bootstrap UI to
        give a "you can reach the controller at …" answer for
        users who haven't set up DNS yet.

        The ``Host`` header read is attacker-controlled but only
        used as the IP hint that orders the candidate list — every
        other URL is built from interface enumeration. The legacy
        ``AttributeError`` swallow (when ``handler.headers`` is a
        non-Mapping stub in tests) is preserved here, but routed
        through ``log_swallowed`` so a real failure shows up in
        the controller log instead of disappearing.
        """
        host_hdr = ""
        try:
            host_hdr = handler.headers.get("Host", "") or ""
        except AttributeError as exc:
            log_swallowed(exc, context="access-urls/host-header")
        handler._json_response(
            HTTPStatus.OK,
            self._access_url_factory(host_hdr).build(),
        )


__all__ = [
    "SecurityAuditGetRoutes",
    "redact_api_key_map",
]
