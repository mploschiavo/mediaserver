"""TLS + credential + key-rotation POST routes
(ADR-0007 Phase 2 wave 5).

This module is the first POST migration off the legacy
``handlers_post.handle()`` ``if/elif`` chain. Seven routes lifted
verbatim from the legacy chain (``_TlsCertHandler.install`` /
``regenerate``, ad-hoc credential revalidation, key rotation,
password-policy mutation, per-service api-key set/discover, and
hard-reset). They all share the security-sensitive characteristic
that motivates ``security_audit.py``'s grouping rule: each one
either rotates a secret, mutates auth/identity state, or surfaces
a service's authentication metadata. Co-locating them here keeps
the redaction + CSRF discipline auditable from a single file
rather than scattered across seven one-route modules.

Spec parity (every path is declared in
``contracts/api/openapi.yaml``):

* ``POST /api/tls/certificate``               -> ``installTlsCertificate``
* ``POST /api/tls/certificate/regenerate``    -> ``regenerateTlsCertificate``
* ``POST /api/credentials``                   -> ``revalidateCredentials``
* ``POST /api/rotate-keys``                   -> ``rotateKeys``
* ``POST /api/password-policy``               -> ``updatePasswordPolicy``
* ``POST /api/services/{service_id}/api-key``  -> ``setServiceApiKey``
* ``POST /api/services/{service_id}/reset``   -> ``hardResetService``

Note the parameter-name asymmetry: the api-key path uses
``service_id`` while the reset path uses ``service_id``. Both are
declared in the live spec verbatim and the Router's
``_check_handler_signature`` enforces that the kwargs match. A
mismatch raises ``RouterMisconfigured`` before the server binds.

Security posture
================

CSRF (double-submit cookie)
---------------------------
The legacy ``handlers_post._global_preflight`` is invoked at the
top of ``handlers_post.handle()`` and applies rate-limit + CSRF
to every mutating request. ``server.do_POST`` consults the Router
BEFORE falling through to the legacy chain, so once a path is
registered here the global preflight is skipped. To preserve the
CSRF invariant (the ``test_csrf_on_mutating_security_endpoints``
ratchet pins ``_global_preflight`` calling ``_check_csrf``; that
ratchet does not yet cover RouteModule subclasses), this module
runs the same CSRF double-submit gate inside ``_CsrfGate.allow``
on every handler. The exact set of CSRF-exempt paths
(``/api/auth/login``, ``/api/auth/logout``, ``/api/tokens/refresh``,
``/webhooks/arr``) is irrelevant here — none of the seven routes
in this module are exempt.

Auth gating (sudo)
------------------
``server._sudo_gate.allows`` runs before the dispatcher fires, so
any of these routes that the production wiring lists as
``_DEFAULT_SUDO_PATHS`` already gets the X-Sudo-Password gate
upstream of this module. Routes inside this file therefore do not
re-implement sudo — that would be defence in depth at the wrong
layer (the gate is centralized for a reason). The unit tests
exercise the route bodies directly via the harness, which mirrors
the production flow: by the time dispatch fires, sudo + auth +
RBAC have already passed.

API-key redaction
-----------------
``/api/services/{service_id}/api-key`` POST returns a status string
plus the ``service`` + ``env`` identifiers. The raw key is never
echoed: on a manual set, only ``{status: set, service, env}`` is
returned; on auto-discovery, only ``{status: discovered, service,
source}`` is returned. The route preserves this shape exactly. As
a defence-in-depth, this module exposes ``redact_api_key_map``
from ``media_stack.domain.auth.secret_redaction`` so future
adopters have it within reach.

Cert/key separation
-------------------
``/api/tls/certificate`` POST receives ``cert_pem`` + ``key_pem``
as the request body and writes them via
``TlsCertificateService.install``. The response carries
``CertificateInfo.to_dict()`` (subject / issuer / validity /
fingerprint / SANs / cert path) plus the envoy-reload result. The
private key is NEVER echoed back — only the public cert metadata.
The test suite has an explicit anti-leak check that pins the
response body never contains a ``-----BEGIN ... PRIVATE KEY-----``
fragment.

Implementation patterns
=======================

* **Strategy** — ``_TlsService`` wraps the
  ``build_default_tls_service`` factory + the envoy-reload
  procedure (compose-side ``envoy_config_generator.main`` +
  ``admin_svc.restart_service('envoy')``). Lifted verbatim from
  the legacy ``_TlsCertHandler`` so behaviour parity is exact.
* **Repository** — ``_CredentialsRotator`` /
  ``_KeyRotationService`` / ``_ServiceKeyRegenerator`` /
  ``_PasswordPolicyMutator`` /
  ``_ServiceHardResetRepository`` hide the
  ``health_svc`` / ``admin_svc`` / ``PasswordPolicyConfig`` /
  ``registry`` collaborators behind narrow interfaces, so tests
  swap each one independently without touching the underlying
  service module.
* **Constructor injection** — ``SecurityTlsPostRoutes`` accepts
  every collaborator as a kwarg with a production-default
  factory. Tests build the class with stubs and the routes are
  exercised through the production ``Router`` via
  ``RouteDispatchHarness``. Constructor defaults are LIVE — no
  lazy-cache resolver pattern (caching the default symbol would
  freeze a pre-patch reference and break tests). When a
  defaulted collaborator needs a fresh attribute lookup so
  ``mock.patch`` of the canonical symbol takes effect, the route
  body resolves via the dotted module reference each call.

OO discipline
=============

* ``SecurityTlsPostRoutes(RouteModule)`` — instance methods
  ``@post``-tagged.
* No ``@staticmethod``. No loose top-level handler functions.
* ``_TlsService`` / ``_CredentialsRotator`` /
  ``_KeyRotationService`` / ``_ServiceKeyRegenerator`` /
  ``_PasswordPolicyMutator`` / ``_ServiceHardResetRepository`` /
  ``_CsrfGate`` are all classes; each has a single
  responsibility.
* Narrow exceptions: ``TlsCertificateServiceError`` is the only
  TLS-shape known-failure; ``OSError`` covers password-policy
  persist; everything else propagates so the dispatcher's 500
  handler records it. Security-relevant swallows go through
  ``log_swallowed`` per the ``security_audit.py`` template.
"""

from __future__ import annotations

import logging
import os
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable

from media_stack.api.routing import RouteModule, post
from media_stack.api.services import admin as admin_svc
from media_stack.api.services import health as health_svc
from media_stack.core.auth.csrf import CsrfProtector
from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateServiceError,
)
from media_stack.core.logging_utils import log_swallowed
from media_stack.domain.auth.secret_redaction import redact_api_key_map

_log = logging.getLogger("media_stack")
_ERR_LEN = 99


# Single module-level reference to the live process environment.
# Every collaborator that needs env access takes this (or a test
# stub) via constructor injection — no per-method ``os.environ``
# reads, which keeps the
# ``OS_ENVIRON_IN_METHODS_RATCHET`` count flat across this domain.
_PROCESS_ENV: Any = os.environ


# ---------------------------------------------------------------------------
# CSRF gate
# ---------------------------------------------------------------------------


class _CsrfGate:
    """Double-submit cookie CSRF check for POST handlers.

    The legacy ``handlers_post._global_preflight`` runs this same
    test (via ``_check_csrf``) at the top of ``handle()``. Once a
    POST route lands in the Router-dispatched flow, the global
    preflight is bypassed by the dispatcher early-return in
    ``server.do_POST``; this gate restores the invariant for
    every route in this module.

    Smart-default behaviour (matches legacy):

    * ``CSRF_ENFORCE=0`` env var disables the check entirely
      (compose dev mode).
    * ``CSRF_ENFORCE=1`` forces strict mode for every request,
      including header-less API clients.
    * Default: strict for browsers (Cookie header present),
      pass-through for header-less API clients (no cookie =
      basic-auth = not CSRF-vulnerable).

    Constructor-inject the ``CsrfProtector`` so a stub can be
    supplied in tests; the live ``os.environ`` is read on
    construction so a test can override ``CSRF_ENFORCE`` via
    ``patch.dict``.
    """

    def __init__(
        self,
        protector: CsrfProtector | None = None,
        env: Any = None,
    ) -> None:
        self._protector = protector or CsrfProtector()
        self._env = env if env is not None else _PROCESS_ENV

    def _mode(self) -> str:
        return (self._env.get("CSRF_ENFORCE", "") or "").strip()

    def allow(self, handler: Any) -> bool:
        mode = self._mode()
        if mode == "0":
            return True
        headers = getattr(handler, "headers", None)
        if headers is None:
            return True
        try:
            cookie_header = headers.get("Cookie", "") or ""
            csrf_header = headers.get(
                self._protector.header_name, "",
            ) or ""
        except AttributeError as exc:
            log_swallowed(exc, context="csrf-gate/header-read")
            return True
        has_cookie = bool(
            isinstance(cookie_header, str) and cookie_header.strip()
        )
        if not (mode == "1" or has_cookie):
            return True
        return self._protector.verify(
            cookie_header=cookie_header,
            header_value=csrf_header,
        )


# ---------------------------------------------------------------------------
# TLS service strategy
# ---------------------------------------------------------------------------


class _TlsService:
    """Strategy wrapping ``TlsCertificateService`` + the
    envoy-reload procedure.

    Lifted from the legacy ``_TlsCertHandler``; the install +
    regenerate methods return the ``CertificateInfo.to_dict()``
    payload merged with the reload status. ``_reload_envoy`` runs
    the compose-side config generator (skipped on k8s; the
    config is a ConfigMap there) and then restarts the envoy
    service so the new cert is picked up.

    Constructor-inject the factory + restart callable so tests
    can supply stubs without monkeypatching module imports.
    """

    def __init__(
        self,
        *,
        tls_factory: Callable[[], Any] | None = None,
        envoy_restarter: Callable[[str], dict[str, Any]] | None = None,
        envoy_config_generator: Any = None,
        env: Any = None,
    ) -> None:
        self._tls_factory = tls_factory
        self._envoy_restarter = envoy_restarter
        self._envoy_config_generator = envoy_config_generator
        self._env = env if env is not None else _PROCESS_ENV

    def _resolve_factory(self) -> Callable[[], Any]:
        if self._tls_factory is not None:
            return self._tls_factory
        # Fresh attribute lookup so ``mock.patch`` on the canonical
        # symbol takes effect (caching would freeze the pre-patch
        # reference and silently break tests).
        from media_stack.api import tls_factory
        return tls_factory.build_default_tls_service

    def _resolve_restarter(self) -> Callable[[str], dict[str, Any]]:
        if self._envoy_restarter is not None:
            return self._envoy_restarter
        # Same fresh-lookup discipline for the admin service.
        return admin_svc.restart_service

    def _resolve_envoy_generator(self) -> Any:
        if self._envoy_config_generator is not None:
            return self._envoy_config_generator
        try:
            from media_stack.services.edge import (
                envoy_config_generator as gen,
            )
            return gen
        except ImportError:
            return None

    def install(
        self, cert_pem: str, key_pem: str,
    ) -> dict[str, Any]:
        info = self._resolve_factory()().install(cert_pem, key_pem)
        reload_payload = self._reload_envoy()
        return {
            "installed": True,
            "envoy_reload": reload_payload,
            **info.to_dict(),
        }

    def regenerate(
        self,
        *,
        hostnames: list[str] | None,
        days: int,
    ) -> dict[str, Any]:
        info = self._resolve_factory()().regenerate(
            hostnames=hostnames, days=days,
        )
        reload_payload = self._reload_envoy()
        return {
            "regenerated": True,
            "envoy_reload": reload_payload,
            **info.to_dict(),
        }

    def _reload_envoy(self) -> dict[str, Any]:
        regen = self._regenerate_envoy_config()
        try:
            restart = self._resolve_restarter()("envoy")
        except (OSError, RuntimeError) as exc:
            log_swallowed(exc, context="tls-install/envoy-restart")
            restart = {
                "status": "error",
                "detail": str(exc)[:_ERR_LEN],
            }
        return {"regen": regen, **restart}

    def _regenerate_envoy_config(self) -> str:
        gen = self._resolve_envoy_generator()
        if gen is None:
            return "skipped (generator module unavailable)"
        if (self._env.get("K8S_NAMESPACE", "") or "").strip():
            return "skipped (k8s runtime; config is ConfigMap-managed)"
        envoy_yaml_dir = Path(
            self._env.get("CONFIG_ROOT", "/srv-config")
        ) / "envoy"
        if not envoy_yaml_dir.is_dir():
            return "skipped (no envoy config dir mounted)"
        try:
            gen.main([])
            return "ok"
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="tls-install/envoy-regen")
            return f"error: {str(exc)[:_ERR_LEN]}"


# ---------------------------------------------------------------------------
# Credentials / key-rotation / api-key / hard-reset repositories
# ---------------------------------------------------------------------------


class _CredentialsRotator:
    """Repository: ad-hoc credential revalidation.

    Wraps ``health_svc.probe_credentials`` so the route never
    reaches into the service module directly. Tests inject a stub
    instance; production passes nothing and the default delegates
    via fresh attribute lookup so ``mock.patch`` on the canonical
    symbol still takes effect.
    """

    def revalidate(
        self, target_services: list[str] | None,
    ) -> dict[str, Any]:
        # Fresh lookup; see ``_TlsService._resolve_factory`` for
        # the rationale.
        return health_svc.probe_credentials(target_services)


class _KeyRotationService:
    """Repository: bulk API-key rotation across the registered
    services. Wraps ``admin_svc.rotate_keys``.

    The underlying service generates fresh keys in-place and
    persists them to the K8s secret / env. The response carries
    a ``keys`` map keyed by service id; this module does NOT
    pass that map to ``redact_api_key_map`` because the underlying
    service already returns metadata (status + per-service detail)
    rather than raw keys. The redaction helper is exposed on the
    module for callers that need it.
    """

    def rotate(
        self, target_services: list[str] | None,
    ) -> dict[str, Any]:
        return admin_svc.rotate_keys(target_services)


class _ServiceKeyRegenerator:
    """Repository: set or auto-discover one service's API key.

    The legacy body lived in
    ``PostRequestHandler._handle_service_api_key_post`` and
    rolled together three concerns: registry lookup, manual-set
    persistence, and config-file/HTTP discovery. This wrapper
    keeps the same shape but exposes them through narrow methods
    so the route handler stays a thin orchestrator.
    """

    def __init__(
        self,
        *,
        env: Any = None,
        config_root_default: str = "/srv-config",
    ) -> None:
        self._env = env if env is not None else _PROCESS_ENV
        self._config_root_default = config_root_default

    def lookup(self, service_id: str) -> Any:
        from media_stack.api.services.registry import SERVICE_MAP
        return SERVICE_MAP.get(service_id)

    def set_manual(self, env_name: str, key: str) -> None:
        self._env[env_name] = key
        admin_svc.persist_keys_to_secret({env_name: key})

    def discover(self, service_id: str) -> tuple[str, str]:
        """Return ``(key, source)`` — empty key when discovery
        failed entirely."""
        from media_stack.api.services.registry import (
            read_api_key_from_file,
            read_api_key_via_http,
        )
        config_root = self._env.get(
            "CONFIG_ROOT", self._config_root_default,
        )
        key = read_api_key_from_file(service_id, config_root) or ""
        if key:
            return key, "config_file"
        key = read_api_key_via_http(service_id) or ""
        if key:
            return key, "http"
        return "", ""


class _PasswordPolicyMutator:
    """Repository: persist a password-policy update. Wraps
    ``PasswordPolicyConfig().save_values(body)``; raises
    ``OSError`` on persist failure (the route narrows the catch
    to that single shape per the security_audit template)."""

    def __init__(
        self,
        config_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._config_factory = config_factory

    def _resolve(self) -> Any:
        if self._config_factory is not None:
            return self._config_factory()
        # Fresh import so a test ``mock.patch`` on the canonical
        # symbol takes effect.
        from media_stack.api.services.password_policy_config import (
            PasswordPolicyConfig,
        )
        return PasswordPolicyConfig()

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._resolve().save_values(body)


class _ServiceHardResetRepository:
    """Repository: ``admin_svc.hard_reset_service`` wrapper.

    Restart + re-discover key + re-run preflight for one service.
    The underlying service handles registry validation; this
    wrapper exists so the route's collaborator surface is uniform
    with the rest of the module.
    """

    def reset(
        self, service_id: str, options: dict[str, Any],
    ) -> dict[str, Any]:
        return admin_svc.hard_reset_service(service_id, options)


# ---------------------------------------------------------------------------
# Route module
# ---------------------------------------------------------------------------


class SecurityTlsPostRoutes(RouteModule):
    """Seven security-sensitive POST routes. The Router
    auto-discovers + instantiates this class at startup, then
    walks tagged methods for registration.

    Constructor-inject the six collaborators + the CSRF gate.
    Production passes nothing — defaults materialize the live
    wiring.
    """

    def __init__(
        self,
        *,
        tls_service: _TlsService | None = None,
        credentials_rotator: _CredentialsRotator | None = None,
        key_rotation_service: _KeyRotationService | None = None,
        service_key_regenerator: _ServiceKeyRegenerator | None = None,
        password_policy_mutator: _PasswordPolicyMutator | None = None,
        hard_reset_repository: _ServiceHardResetRepository | None = None,
        csrf_gate: _CsrfGate | None = None,
    ) -> None:
        self._tls = tls_service or _TlsService()
        self._credentials = (
            credentials_rotator or _CredentialsRotator()
        )
        self._key_rotation = (
            key_rotation_service or _KeyRotationService()
        )
        self._service_key = (
            service_key_regenerator or _ServiceKeyRegenerator()
        )
        self._policy = (
            password_policy_mutator or _PasswordPolicyMutator()
        )
        self._hard_reset = (
            hard_reset_repository or _ServiceHardResetRepository()
        )
        self._csrf = csrf_gate or _CsrfGate()

    # --- CSRF helper --------------------------------------------------

    def _csrf_or_403(self, handler: Any) -> bool:
        """Run the CSRF gate; return True if the request should
        proceed. On reject, write a 403 envelope and return False
        so the caller short-circuits the body."""
        if self._csrf.allow(handler):
            return True
        handler._json_response(
            HTTPStatus.FORBIDDEN,
            {"error": "CSRF token missing or invalid"},
        )
        return False

    # --- TLS routes ---------------------------------------------------

    @post("/api/tls/certificate")
    def handle_install_tls_certificate(self, handler: Any) -> None:
        """Install a custom PEM cert + key bundle.

        Body: ``{cert_pem, key_pem}``. Both required + non-empty.
        On success, regenerates Envoy config (compose only) and
        restarts envoy so the new cert is picked up. Response
        carries the ``CertificateInfo`` describing the now-installed
        cert plus the envoy-reload status.

        The private key is NEVER echoed back — only the public
        cert metadata. This is a documented security invariant
        pinned by the test suite.
        """
        if not self._csrf_or_403(handler):
            return
        body = handler._read_json_body() or {}
        cert_pem = str(body.get("cert_pem", "") or "").strip()
        key_pem = str(body.get("key_pem", "") or "").strip()
        if not cert_pem or not key_pem:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "cert_pem and key_pem required"},
            )
            return
        try:
            payload = self._tls.install(cert_pem, key_pem)
        except TlsCertificateServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, payload)

    @post("/api/tls/certificate/regenerate")
    def handle_regenerate_tls_certificate(
        self, handler: Any,
    ) -> None:
        """Self-signed cert refresh.

        Body (optional): ``{hostnames: [...], days: <int>}``. Both
        fields default to the service's notion of "sensible
        defaults" (every-host SAN list + 365 days). Reload shape is
        identical to install.
        """
        if not self._csrf_or_403(handler):
            return
        body = handler._read_json_body() or {}
        hostnames = body.get("hostnames")
        if not isinstance(hostnames, list):
            hostnames = None
        try:
            days = int(body.get("days", 0) or 73 * 5)
        except (TypeError, ValueError):
            days = 73 * 5
        try:
            payload = self._tls.regenerate(
                hostnames=hostnames, days=days,
            )
        except TlsCertificateServiceError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": str(exc)[:_ERR_LEN]},
            )
            return
        handler._json_response(HTTPStatus.OK, payload)

    # --- Credentials & key rotation -----------------------------------

    @post("/api/credentials")
    def handle_revalidate_credentials(self, handler: Any) -> None:
        """Ad-hoc credential revalidation across the service list.

        Body (optional): ``{services: [<service_id>, ...]}`` — when
        omitted, every registered service is checked. Response is
        the same status-only payload the GET surface returns; raw
        API keys are NEVER echoed.

        Distinct from GET ``/api/credentials`` (lives in
        ``routes/security_audit.py``) which is the read-only steady-
        state view; this POST drives an on-demand probe.
        """
        if not self._csrf_or_403(handler):
            return
        body = handler._read_json_body() or {}
        target = body.get("services")
        if not isinstance(target, list):
            target = None
        handler._json_response(
            HTTPStatus.OK, self._credentials.revalidate(target),
        )

    @post("/api/rotate-keys")
    def handle_rotate_keys(self, handler: Any) -> None:
        """Bulk API-key rotation.

        Body (optional): ``{services: [<service_id>, ...]}``. When
        omitted, every key-bearing service is rotated. Returned
        payload is the admin-svc rotate result (per-service status
        + restart map); the live shape never includes raw keys.
        """
        if not self._csrf_or_403(handler):
            return
        body = handler._read_json_body() or {}
        target = body.get("services")
        if not isinstance(target, list):
            target = None
        handler._json_response(
            HTTPStatus.OK, self._key_rotation.rotate(target),
        )

    # --- Password policy ----------------------------------------------

    @post("/api/password-policy")
    def handle_update_password_policy(self, handler: Any) -> None:
        """Persist a password-policy update.

        Body must be a non-empty JSON object; partial updates are
        supported (any field omitted is preserved). Persistence
        failure raises ``OSError`` and is mapped to a 500 envelope
        with a short error string. Other shapes propagate so the
        dispatcher's 500 handler can record them.
        """
        if not self._csrf_or_403(handler):
            return
        body = handler._read_json_body()
        if not body:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "JSON body required"},
            )
            return
        try:
            new_values = self._policy.save(body)
        except OSError as exc:
            log_swallowed(exc, context="password-policy/save")
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"write failed: {str(exc)[:80]}"},
            )
            return
        handler._json_response(HTTPStatus.OK, {
            "status": "updated",
            "policy": new_values,
        })

    # --- Per-service mutations ----------------------------------------

    @post("/api/services/{service_id}/api-key")
    def handle_set_service_api_key(
        self, handler: Any, service_id: str,
    ) -> None:
        """Manually set or auto-discover one service's API key.

        Body (optional): ``{api_key: "<raw key>"}`` for manual set;
        empty body triggers auto-discovery (config file -> HTTP).
        The persisted key is written to the K8s secret + env. The
        response carries ``{status, service, env}`` (manual) or
        ``{status, service, source}`` (discovery) — the raw key
        is NEVER echoed back.

        The kwarg name ``service_id`` matches the spec verbatim;
        ``_check_handler_signature`` enforces that mismatches fail
        at startup.
        """
        if not self._csrf_or_403(handler):
            return
        svc = self._service_key.lookup(service_id)
        if svc is None or not getattr(svc, "api_key_env", ""):
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {
                    "error": (
                        f"Service '{service_id}' not found or has "
                        f"no API key"
                    ),
                },
            )
            return
        body = handler._read_json_body() or {}
        manual = str(body.get("api_key", "") or "").strip()
        if manual:
            self._service_key.set_manual(svc.api_key_env, manual)
            handler._json_response(HTTPStatus.OK, {
                "status": "set",
                "service": service_id,
                "env": svc.api_key_env,
            })
            return
        key, source = self._service_key.discover(service_id)
        if key:
            self._service_key.set_manual(svc.api_key_env, key)
            handler._json_response(HTTPStatus.OK, {
                "status": "discovered",
                "service": service_id,
                "source": source,
            })
            return
        handler._json_response(
            HTTPStatus.NOT_FOUND,
            {
                "error": (
                    f"Could not discover API key for {service_id}. "
                    f"Provide it manually via api_key field."
                ),
            },
        )

    @post("/api/services/{service_id}/reset")
    def handle_hard_reset_service(
        self, handler: Any, service_id: str,
    ) -> None:
        """Hard-reset one service: restart + re-discover key +
        re-run preflight.

        Body (optional): forwarded as ``options`` to the underlying
        service. The kwarg name ``service_id`` matches the spec
        verbatim; ``_check_handler_signature`` enforces that mismatches
        fail at startup.
        """
        if not self._csrf_or_403(handler):
            return
        body = handler._read_json_body() or {}
        handler._json_response(
            HTTPStatus.OK,
            self._hard_reset.reset(service_id, body),
        )


__all__ = [
    "SecurityTlsPostRoutes",
    "redact_api_key_map",
]
