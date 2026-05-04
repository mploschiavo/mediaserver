"""Tests for ``api/routes/post_security_tls.py``
(ADR-0007 Phase 2 wave 5).

Seven route tests + a CSRF gate suite + a routing-integration
sanity check + a defence-in-depth security suite. The route module
lifts each legacy POST body off the ``handlers_post.handle()``
``if/elif`` chain — most tests mock at the collaborator boundary
(``_TlsService`` / ``_CredentialsRotator`` / ``_KeyRotationService``
/ ``_ServiceKeyRegenerator`` / ``_PasswordPolicyMutator`` /
``_ServiceHardResetRepository``) and assert response shape +
security invariants:

* CSRF double-submit is enforced on every mutation. The
  ``_CsrfGate`` smart-default rules (browser strict, header-less
  API client pass-through, ``CSRF_ENFORCE=0`` disable, ``=1``
  strict-for-everyone) are pinned by per-mode tests.
* The TLS install path NEVER echoes the private key in any
  response field — pinned by a regex anti-leak assertion.
* Auto-discovery + manual-set on ``/api/services/{serviceId}/api-key``
  never echoes the raw key — pinned by an entropy-style regex
  anti-leak assertion.
* Per-route auth gating is delegated to the upstream
  ``_check_auth`` + ``_sudo_gate`` middleware in production; the
  tests pin that the route itself doesn't bypass that by leaking
  metadata before those gates would have a chance to fire.

Test patch targets
==================

* ``_TlsService`` collaborator methods are stubbed via direct
  injection through the constructor (the ``Router`` instantiates
  ``SecurityTlsPostRoutes`` with no args at startup, so the
  default-wiring path lifts the live ``build_default_tls_service``
  + ``admin_svc.restart_service`` symbols. Tests that exercise the
  defaulted lookup path patch those canonical symbols).
* The constructor-injected collaborators are kept as the
  preferred patch surface — no monkey-patching of the route
  module's own attributes.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from media_stack.api.routes.post_security_tls import (
    SecurityTlsPostRoutes,
    _CredentialsRotator,
    _CsrfGate,
    _KeyRotationService,
    _PasswordPolicyMutator,
    _ServiceHardResetRepository,
    _ServiceKeyRegenerator,
    _TlsService,
    redact_api_key_map,
)
from media_stack.api.routing import DefaultDispatcher, DispatchOutcome
from tests.unit.api.routes._helpers import (
    MockControllerHandler as _BaseMockHandler,
    RouteDispatchHarness as _BaseHarness,
    CapturedResponse,
)


class MockControllerHandler(_BaseMockHandler):
    """Local subclass that adds ``_read_json_body`` for the POST
    surface. The shared ``_helpers.MockControllerHandler`` covers
    the GET-only routes that landed in earlier waves; this
    extension is needed by every POST migration that lifts a body
    parsed from ``rfile``.

    Drops a ``_read_json_body`` method that mirrors the production
    ``ControllerAPIHandler._read_json_body`` shape: read up to
    Content-Length bytes off ``rfile`` and ``json.loads`` them,
    returning ``{}`` on empty/malformed body."""

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            # Try to read whatever's in the buffer (helpful for
            # tests that didn't bother to set Content-Length).
            buf = self.rfile.read()
            if not buf:
                return {}
            try:
                return json.loads(buf)
            except (json.JSONDecodeError, ValueError):
                return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return {}


class RouteDispatchHarness(_BaseHarness):
    """Shadow the shared harness so ``dispatch`` builds the
    body-aware ``MockControllerHandler`` subclass above."""

    def dispatch(
        self,
        verb: str,
        path: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> CapturedResponse:
        handler = MockControllerHandler(
            path=path, body=body, headers=headers, state=state,
        )
        outcome = self._dispatcher.try_dispatch(verb, path, handler)
        if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
            self._dispatcher.write_method_not_allowed(handler, path)
        return handler.captured


# ---------------------------------------------------------------------------
# CSRF gate
# ---------------------------------------------------------------------------


class TestCsrfGate:
    """Smart-default behaviour pinning. Mirrors the legacy
    ``_check_csrf`` rules:

    * ``CSRF_ENFORCE=0`` — disabled entirely (compose dev).
    * ``CSRF_ENFORCE=1`` — strict for every request (including
      header-less API clients).
    * Default — strict only for browsers (Cookie header present);
      header-less API clients pass through.
    """

    def test_disabled_mode_lets_anything_through(self) -> None:
        gate = _CsrfGate(env={"CSRF_ENFORCE": "0"})
        handler = MockControllerHandler(path="/api/credentials")
        assert gate.allow(handler) is True

    def test_browser_with_matching_token_allowed(self) -> None:
        gate = _CsrfGate(env={})
        handler = MockControllerHandler(
            path="/api/credentials",
            headers={
                "Cookie": "media_stack_csrf=abc123",
                "X-CSRF-Token": "abc123",
            },
        )
        assert gate.allow(handler) is True

    def test_browser_with_mismatched_token_rejected(self) -> None:
        gate = _CsrfGate(env={})
        handler = MockControllerHandler(
            path="/api/credentials",
            headers={
                "Cookie": "media_stack_csrf=abc123",
                "X-CSRF-Token": "different",
            },
        )
        assert gate.allow(handler) is False

    def test_browser_with_missing_header_rejected(self) -> None:
        gate = _CsrfGate(env={})
        handler = MockControllerHandler(
            path="/api/credentials",
            headers={"Cookie": "media_stack_csrf=abc123"},
        )
        assert gate.allow(handler) is False

    def test_no_cookie_no_enforce_pass_through(self) -> None:
        """Header-less API client = no Cookie header -> pass."""
        gate = _CsrfGate(env={})
        handler = MockControllerHandler(
            path="/api/credentials", headers={},
        )
        assert gate.allow(handler) is True

    def test_strict_mode_rejects_header_less_client(self) -> None:
        """``CSRF_ENFORCE=1`` overrides the smart default and
        forces strict mode even for API clients without a Cookie."""
        gate = _CsrfGate(env={"CSRF_ENFORCE": "1"})
        handler = MockControllerHandler(
            path="/api/credentials", headers={},
        )
        assert gate.allow(handler) is False


# ---------------------------------------------------------------------------
# Helpers — build a passing-CSRF handler
# ---------------------------------------------------------------------------


_CSRF_TOKEN = "test-csrf-token-deadbeef"


def _csrf_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Cookie": f"media_stack_csrf={_CSRF_TOKEN}",
        "X-CSRF-Token": _CSRF_TOKEN,
    }
    if extra:
        headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# Route: POST /api/tls/certificate
# ---------------------------------------------------------------------------


class _StubTlsCertInfo:
    """Mirror of ``CertificateInfo.to_dict`` for stubs."""

    def __init__(self, **kwargs: Any) -> None:
        self._fields = kwargs

    def to_dict(self) -> dict[str, Any]:
        return dict(self._fields)


class _StubTlsService:
    """Stub for the ``_TlsService`` strategy. ``install`` /
    ``regenerate`` return whatever payload the test injected; the
    raw key is NEVER part of the returned shape."""

    def __init__(
        self,
        install_payload: dict[str, Any] | None = None,
        regenerate_payload: dict[str, Any] | None = None,
        install_raises: BaseException | None = None,
        regenerate_raises: BaseException | None = None,
    ) -> None:
        self.install_payload = install_payload or {}
        self.regenerate_payload = regenerate_payload or {}
        self.install_raises = install_raises
        self.regenerate_raises = regenerate_raises
        self.install_calls: list[tuple[str, str]] = []
        self.regenerate_calls: list[dict[str, Any]] = []

    def install(self, cert_pem: str, key_pem: str) -> dict[str, Any]:
        self.install_calls.append((cert_pem, key_pem))
        if self.install_raises is not None:
            raise self.install_raises
        return self.install_payload

    def regenerate(
        self,
        *,
        hostnames: list[str] | None,
        days: int,
    ) -> dict[str, Any]:
        self.regenerate_calls.append({
            "hostnames": hostnames, "days": days,
        })
        if self.regenerate_raises is not None:
            raise self.regenerate_raises
        return self.regenerate_payload


class TestInstallTlsCertificateRoute:
    """``POST /api/tls/certificate`` — install custom PEM bundle."""

    def test_returns_envoy_reload_envelope_on_success(self) -> None:
        stub = _StubTlsService(install_payload={
            "installed": True,
            "envoy_reload": {"regen": "ok", "status": "ok"},
            "subject": "CN=example",
            "fingerprint_sha256": "AB:CD:EF",
        })
        routes = SecurityTlsPostRoutes(tls_service=stub)
        body = json.dumps({
            "cert_pem": "-----BEGIN CERTIFICATE-----\nMII..\n-----END CERTIFICATE-----\n",
            "key_pem": "-----BEGIN PRIVATE KEY-----\nMIIE..\n-----END PRIVATE KEY-----\n",
        }).encode()
        handler = MockControllerHandler(
            path="/api/tls/certificate",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_install_tls_certificate(handler)

        assert handler.captured.status == 200
        payload = json.loads(handler.captured.body)
        assert payload["installed"] is True
        assert payload["envoy_reload"]["regen"] == "ok"
        assert len(stub.install_calls) == 1
        cert, key = stub.install_calls[0]
        assert "BEGIN CERTIFICATE" in cert
        assert "PRIVATE KEY" in key

    def test_response_never_echoes_private_key_pem(self) -> None:
        """Critical anti-leak: install must not return the key
        body. The CertificateInfo shape exposes only the public
        cert metadata; a future regression that surfaced the key
        would be caught by this regex."""
        stub = _StubTlsService(install_payload={
            "installed": True,
            "envoy_reload": {"regen": "ok"},
            "subject": "CN=example",
        })
        routes = SecurityTlsPostRoutes(tls_service=stub)
        secret_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQ\n"
            "-----END PRIVATE KEY-----\n"
        )
        body = json.dumps({
            "cert_pem": "-----BEGIN CERTIFICATE-----\nMII..\n-----END CERTIFICATE-----\n",
            "key_pem": secret_key,
        }).encode()
        handler = MockControllerHandler(
            path="/api/tls/certificate",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_install_tls_certificate(handler)

        body_text = handler.captured.body.decode("utf-8")
        assert "PRIVATE KEY" not in body_text, (
            f"private key leaked into response: {body_text!r}"
        )
        assert not re.search(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            body_text,
        ), f"private key leaked into response: {body_text!r}"

    def test_missing_cert_pem_returns_400(self) -> None:
        routes = SecurityTlsPostRoutes(tls_service=_StubTlsService())
        body = json.dumps({"cert_pem": "", "key_pem": "key"}).encode()
        handler = MockControllerHandler(
            path="/api/tls/certificate",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_install_tls_certificate(handler)

        assert handler.captured.status == 400
        assert "required" in json.loads(handler.captured.body)["error"]

    def test_install_service_error_returns_400(self) -> None:
        from media_stack.core.edge.tls_certificate_service import (
            TlsCertificateServiceError,
        )
        stub = _StubTlsService(install_raises=TlsCertificateServiceError(
            "key/cert mismatch",
        ))
        routes = SecurityTlsPostRoutes(tls_service=stub)
        body = json.dumps({
            "cert_pem": "cert", "key_pem": "key",
        }).encode()
        handler = MockControllerHandler(
            path="/api/tls/certificate",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_install_tls_certificate(handler)
        assert handler.captured.status == 400
        body_obj = json.loads(handler.captured.body)
        assert "key/cert mismatch" in body_obj["error"]

    def test_missing_csrf_token_returns_403(self) -> None:
        """Browser-shaped request (Cookie header present) without
        a matching CSRF header is rejected before the body is
        touched."""
        stub = _StubTlsService()
        routes = SecurityTlsPostRoutes(tls_service=stub)
        body = json.dumps({
            "cert_pem": "cert", "key_pem": "key",
        }).encode()
        handler = MockControllerHandler(
            path="/api/tls/certificate",
            body=body,
            headers={
                # Cookie present (browser) but no X-CSRF-Token.
                "Cookie": f"media_stack_csrf={_CSRF_TOKEN}",
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_install_tls_certificate(handler)
        assert handler.captured.status == 403
        assert json.loads(handler.captured.body)["error"].startswith(
            "CSRF",
        )
        # Critical: install was NEVER called — preflight short-
        # circuits before the cert is written.
        assert stub.install_calls == []


# ---------------------------------------------------------------------------
# Route: POST /api/tls/certificate/regenerate
# ---------------------------------------------------------------------------


class TestRegenerateTlsCertificateRoute:
    """``POST /api/tls/certificate/regenerate`` — self-signed
    refresh."""

    def test_default_body_uses_default_days(self) -> None:
        stub = _StubTlsService(regenerate_payload={
            "regenerated": True,
            "envoy_reload": {"regen": "ok"},
        })
        routes = SecurityTlsPostRoutes(tls_service=stub)
        handler = MockControllerHandler(
            path="/api/tls/certificate/regenerate",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_regenerate_tls_certificate(handler)

        assert handler.captured.status == 200
        assert stub.regenerate_calls == [
            {"hostnames": None, "days": 365},
        ]

    def test_custom_hostnames_and_days_passed_through(self) -> None:
        stub = _StubTlsService(regenerate_payload={
            "regenerated": True,
            "envoy_reload": {},
        })
        routes = SecurityTlsPostRoutes(tls_service=stub)
        body = json.dumps({
            "hostnames": ["a.example", "b.example"],
            "days": 90,
        }).encode()
        handler = MockControllerHandler(
            path="/api/tls/certificate/regenerate",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_regenerate_tls_certificate(handler)

        assert stub.regenerate_calls == [{
            "hostnames": ["a.example", "b.example"],
            "days": 90,
        }]

    def test_bad_hostnames_type_treated_as_none(self) -> None:
        stub = _StubTlsService(regenerate_payload={
            "regenerated": True,
        })
        routes = SecurityTlsPostRoutes(tls_service=stub)
        body = json.dumps({"hostnames": "not-a-list"}).encode()
        handler = MockControllerHandler(
            path="/api/tls/certificate/regenerate",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_regenerate_tls_certificate(handler)

        assert stub.regenerate_calls[0]["hostnames"] is None

    def test_service_error_returns_400(self) -> None:
        from media_stack.core.edge.tls_certificate_service import (
            TlsCertificateServiceError,
        )
        stub = _StubTlsService(regenerate_raises=TlsCertificateServiceError(
            "openssl missing",
        ))
        routes = SecurityTlsPostRoutes(tls_service=stub)
        handler = MockControllerHandler(
            path="/api/tls/certificate/regenerate",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_regenerate_tls_certificate(handler)
        assert handler.captured.status == 400


# ---------------------------------------------------------------------------
# Route: POST /api/credentials
# ---------------------------------------------------------------------------


class _StubCredentialsRotator:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[list[str] | None] = []

    def revalidate(
        self, target_services: list[str] | None,
    ) -> dict[str, Any]:
        self.calls.append(target_services)
        return self.payload


class TestRevalidateCredentialsRoute:
    """``POST /api/credentials`` — ad-hoc revalidation."""

    def test_no_body_revalidates_all(self) -> None:
        stub = _StubCredentialsRotator({
            "credentials": {"jellyfin": "ok"},
            "ok": 1,
            "total": 1,
        })
        routes = SecurityTlsPostRoutes(credentials_rotator=stub)
        handler = MockControllerHandler(
            path="/api/credentials",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_revalidate_credentials(handler)

        assert handler.captured.status == 200
        assert stub.calls == [None]

    def test_targeted_services_passed_through(self) -> None:
        stub = _StubCredentialsRotator({"credentials": {}})
        routes = SecurityTlsPostRoutes(credentials_rotator=stub)
        body = json.dumps({"services": ["sonarr", "radarr"]}).encode()
        handler = MockControllerHandler(
            path="/api/credentials",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_revalidate_credentials(handler)
        assert stub.calls == [["sonarr", "radarr"]]

    def test_response_carries_no_raw_api_keys(self) -> None:
        """Defence-in-depth — same shape as the GET surface test
        in ``security_audit.py``. The collaborator returns status
        strings; the route must never echo a raw key even if a
        future regression accidentally surfaced one."""
        stub = _StubCredentialsRotator({
            "credentials": {"jellyfin": "ok", "sonarr": "fail"},
            "ok": 1,
            "total": 2,
        })
        routes = SecurityTlsPostRoutes(credentials_rotator=stub)
        handler = MockControllerHandler(
            path="/api/credentials",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_revalidate_credentials(handler)
        body_text = handler.captured.body.decode("utf-8")
        assert not re.search(r"[a-f0-9]{32,}", body_text), (
            f"suspected raw API key in response: {body_text!r}"
        )


# ---------------------------------------------------------------------------
# Route: POST /api/rotate-keys
# ---------------------------------------------------------------------------


class _StubKeyRotation:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[list[str] | None] = []

    def rotate(
        self, target_services: list[str] | None,
    ) -> dict[str, Any]:
        self.calls.append(target_services)
        return self.payload


class TestRotateKeysRoute:
    """``POST /api/rotate-keys`` — bulk API-key rotation."""

    def test_rotates_all_when_no_body(self) -> None:
        stub = _StubKeyRotation({
            "rotated": ["sonarr", "radarr"],
            "restarted": ["sonarr"],
        })
        routes = SecurityTlsPostRoutes(key_rotation_service=stub)
        handler = MockControllerHandler(
            path="/api/rotate-keys",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_rotate_keys(handler)

        assert handler.captured.status == 200
        assert stub.calls == [None]

    def test_targeted_subset(self) -> None:
        stub = _StubKeyRotation({"rotated": ["sonarr"]})
        routes = SecurityTlsPostRoutes(key_rotation_service=stub)
        body = json.dumps({"services": ["sonarr"]}).encode()
        handler = MockControllerHandler(
            path="/api/rotate-keys",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_rotate_keys(handler)
        assert stub.calls == [["sonarr"]]


# ---------------------------------------------------------------------------
# Route: POST /api/password-policy
# ---------------------------------------------------------------------------


class _StubPolicyMutator:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.payload = payload or {}
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(body)
        if self.raises is not None:
            raise self.raises
        return self.payload


class TestUpdatePasswordPolicyRoute:
    """``POST /api/password-policy`` — partial-update mutation."""

    def test_persists_partial_update(self) -> None:
        stub = _StubPolicyMutator(payload={
            "min_length": 14,
            "require_uppercase": True,
        })
        routes = SecurityTlsPostRoutes(password_policy_mutator=stub)
        body = json.dumps({"min_length": 14}).encode()
        handler = MockControllerHandler(
            path="/api/password-policy",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_update_password_policy(handler)

        assert handler.captured.status == 200
        body_obj = json.loads(handler.captured.body)
        assert body_obj["status"] == "updated"
        assert body_obj["policy"]["min_length"] == 14
        assert stub.calls == [{"min_length": 14}]

    def test_empty_body_returns_400(self) -> None:
        routes = SecurityTlsPostRoutes(
            password_policy_mutator=_StubPolicyMutator(),
        )
        handler = MockControllerHandler(
            path="/api/password-policy",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_update_password_policy(handler)

        assert handler.captured.status == 400

    def test_oserror_on_persist_returns_500_with_short_error(
        self,
    ) -> None:
        """Narrow ``OSError`` swallow — write failure surfaces with
        the central ``log_swallowed`` so the path is observable."""
        stub = _StubPolicyMutator(raises=OSError("no space left"))
        routes = SecurityTlsPostRoutes(password_policy_mutator=stub)
        body = json.dumps({"min_length": 14}).encode()
        handler = MockControllerHandler(
            path="/api/password-policy",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        with patch(
            "media_stack.api.routes.post_security_tls.log_swallowed",
        ) as mock_log:
            routes.handle_update_password_policy(handler)

        assert handler.captured.status == 500
        body_obj = json.loads(handler.captured.body)
        assert "write failed" in body_obj["error"]
        assert "no space left" in body_obj["error"]
        mock_log.assert_called_once()

    def test_unexpected_exception_propagates(self) -> None:
        """``RuntimeError`` is not in the narrow catch — it must
        propagate to the dispatcher's 500 handler so silent
        swallows don't mask real bugs."""
        stub = _StubPolicyMutator(raises=RuntimeError("policy boom"))
        routes = SecurityTlsPostRoutes(password_policy_mutator=stub)
        body = json.dumps({"min_length": 14}).encode()
        handler = MockControllerHandler(
            path="/api/password-policy",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        with pytest.raises(RuntimeError, match="policy boom"):
            routes.handle_update_password_policy(handler)


# ---------------------------------------------------------------------------
# Route: POST /api/services/{serviceId}/api-key
# ---------------------------------------------------------------------------


class _StubServiceDef:
    def __init__(self, api_key_env: str) -> None:
        self.api_key_env = api_key_env


class _StubServiceKeyRegen:
    def __init__(
        self,
        svc: Any = None,
        discover_result: tuple[str, str] = ("", ""),
    ) -> None:
        self.svc = svc
        self.discover_result = discover_result
        self.set_calls: list[tuple[str, str]] = []
        self.discover_calls: list[str] = []

    def lookup(self, service_id: str) -> Any:
        return self.svc

    def set_manual(self, env_name: str, key: str) -> None:
        self.set_calls.append((env_name, key))

    def discover(self, service_id: str) -> tuple[str, str]:
        self.discover_calls.append(service_id)
        return self.discover_result


class TestSetServiceApiKeyRoute:
    """``POST /api/services/{serviceId}/api-key`` — manual set or
    auto-discover."""

    def test_unknown_service_returns_404(self) -> None:
        routes = SecurityTlsPostRoutes(
            service_key_regenerator=_StubServiceKeyRegen(svc=None),
        )
        handler = MockControllerHandler(
            path="/api/services/envoy/api-key",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_set_service_api_key(handler, serviceId="envoy")
        assert handler.captured.status == 404

    def test_manual_key_persisted_and_safe_response(self) -> None:
        stub = _StubServiceKeyRegen(
            svc=_StubServiceDef(api_key_env="SONARR_API_KEY"),
        )
        routes = SecurityTlsPostRoutes(service_key_regenerator=stub)
        manual_key = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        body = json.dumps({"api_key": manual_key}).encode()
        handler = MockControllerHandler(
            path="/api/services/sonarr/api-key",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_set_service_api_key(handler, serviceId="sonarr")

        assert handler.captured.status == 200
        body_obj = json.loads(handler.captured.body)
        assert body_obj == {
            "status": "set",
            "service": "sonarr",
            "env": "SONARR_API_KEY",
        }
        # Persistence happened.
        assert stub.set_calls == [("SONARR_API_KEY", manual_key)]
        # CRITICAL: the raw key is NEVER echoed back. The test
        # value is a 32-hex-char Sonarr-shaped key; the response
        # body must not contain it as a substring.
        body_text = handler.captured.body.decode("utf-8")
        assert manual_key not in body_text, (
            f"raw API key leaked into response: {body_text!r}"
        )
        assert not re.search(r"[a-f0-9]{32,}", body_text)

    def test_auto_discover_uses_config_file_source(self) -> None:
        discovered = "deadbeefcafef00ddeadbeefcafef00d"
        stub = _StubServiceKeyRegen(
            svc=_StubServiceDef(api_key_env="SONARR_API_KEY"),
            discover_result=(discovered, "config_file"),
        )
        routes = SecurityTlsPostRoutes(service_key_regenerator=stub)
        handler = MockControllerHandler(
            path="/api/services/sonarr/api-key",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_set_service_api_key(handler, serviceId="sonarr")

        assert handler.captured.status == 200
        body_obj = json.loads(handler.captured.body)
        assert body_obj == {
            "status": "discovered",
            "service": "sonarr",
            "source": "config_file",
        }
        assert stub.set_calls == [("SONARR_API_KEY", discovered)]
        # CRITICAL: discovered key NEVER appears in the response.
        body_text = handler.captured.body.decode("utf-8")
        assert discovered not in body_text

    def test_auto_discover_failure_returns_404(self) -> None:
        stub = _StubServiceKeyRegen(
            svc=_StubServiceDef(api_key_env="SONARR_API_KEY"),
            discover_result=("", ""),
        )
        routes = SecurityTlsPostRoutes(service_key_regenerator=stub)
        handler = MockControllerHandler(
            path="/api/services/sonarr/api-key",
            body=b"",
            headers=_csrf_headers(),
        )
        routes.handle_set_service_api_key(handler, serviceId="sonarr")

        assert handler.captured.status == 404
        body_obj = json.loads(handler.captured.body)
        assert "Could not discover" in body_obj["error"]


# ---------------------------------------------------------------------------
# Route: POST /api/services/{service_id}/reset
# ---------------------------------------------------------------------------


class _StubHardReset:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def reset(
        self, service_id: str, options: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((service_id, options))
        return self.payload


class TestHardResetServiceRoute:
    """``POST /api/services/{service_id}/reset`` — restart +
    re-discover key + re-run preflight."""

    def test_passes_service_id_and_body_through(self) -> None:
        stub = _StubHardReset({
            "ok": True,
            "service_id": "sonarr",
            "restarted": True,
            "api_key_rediscovered": True,
            "preflight_passed": True,
        })
        routes = SecurityTlsPostRoutes(hard_reset_repository=stub)
        body = json.dumps({"wipe_config": False}).encode()
        handler = MockControllerHandler(
            path="/api/services/sonarr/reset",
            body=body,
            headers={
                **_csrf_headers(),
                "Content-Length": str(len(body)),
            },
        )
        routes.handle_hard_reset_service(handler, service_id="sonarr")

        assert handler.captured.status == 200
        assert stub.calls == [
            ("sonarr", {"wipe_config": False}),
        ]


# ---------------------------------------------------------------------------
# Routing-integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    post-security-tls domain. If a future change drops a handler
    from the registry, this test fires before any per-route test
    does."""

    def test_all_post_security_tls_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        expected = {
            ("POST", "/api/tls/certificate"),
            ("POST", "/api/tls/certificate/regenerate"),
            ("POST", "/api/credentials"),
            ("POST", "/api/rotate-keys"),
            ("POST", "/api/password-policy"),
            ("POST", "/api/services/{serviceId}/api-key"),
            ("POST", "/api/services/{service_id}/reset"),
        }
        registered = {
            (r.verb, r.path)
            for r in harness._dispatcher._router.registered_routes()
            if (r.verb, r.path) in expected
        }
        assert registered == expected, (
            f"Missing post-security-tls routes: "
            f"{expected - registered}"
        )

    def test_dispatch_through_router_for_credentials_post(self) -> None:
        """End-to-end smoke test: the harness drives the production
        dispatcher; with CSRF disabled the route must be reached
        and the collaborator invoked."""
        DefaultDispatcher.reset_for_tests()
        with patch(
            "media_stack.api.routes.post_security_tls."
            "health_svc.probe_credentials",
            return_value={"credentials": {}, "ok": 0, "total": 0},
        ), patch.dict(
            "os.environ", {"CSRF_ENFORCE": "0"}, clear=False,
        ):
            harness = RouteDispatchHarness.with_default_router()
            response = harness.dispatch(
                "POST", "/api/credentials",
                body=b"",
                headers={},
            )
        assert response.status == 200
        body = json.loads(response.body)
        assert body == {"credentials": {}, "ok": 0, "total": 0}


# ---------------------------------------------------------------------------
# Defaulted collaborator wiring (live attribute lookup, no lazy cache)
# ---------------------------------------------------------------------------


class TestDefaultedCollaboratorLookups:
    """Pin that the route module's defaulted collaborators do
    fresh attribute lookups so ``mock.patch`` of the canonical
    symbol still takes effect. The lazy-cache resolver pattern
    (caching the default at construction time) is forbidden by
    the project rules — these tests guard against that
    regression.
    """

    def test_credentials_rotator_reflects_patched_symbol(self) -> None:
        rotator = _CredentialsRotator()
        with patch(
            "media_stack.api.routes.post_security_tls."
            "health_svc.probe_credentials",
            return_value={"credentials": {"jellyfin": "ok"}},
        ) as mock_probe:
            result = rotator.revalidate(["jellyfin"])
        assert result == {"credentials": {"jellyfin": "ok"}}
        mock_probe.assert_called_once_with(["jellyfin"])

    def test_key_rotation_reflects_patched_symbol(self) -> None:
        rotator = _KeyRotationService()
        with patch(
            "media_stack.api.routes.post_security_tls."
            "admin_svc.rotate_keys",
            return_value={"rotated": ["sonarr"]},
        ) as mock_rotate:
            result = rotator.rotate(["sonarr"])
        assert result == {"rotated": ["sonarr"]}
        mock_rotate.assert_called_once_with(["sonarr"])

    def test_hard_reset_reflects_patched_symbol(self) -> None:
        repo = _ServiceHardResetRepository()
        with patch(
            "media_stack.api.routes.post_security_tls."
            "admin_svc.hard_reset_service",
            return_value={"ok": True, "service_id": "sonarr"},
        ) as mock_reset:
            result = repo.reset("sonarr", {})
        assert result == {"ok": True, "service_id": "sonarr"}
        mock_reset.assert_called_once_with("sonarr", {})

    def test_tls_factory_lookup_is_fresh_each_call(self) -> None:
        """The ``_TlsService._resolve_factory`` defaults must
        re-import on every call so a mid-test patch on
        ``tls_factory.build_default_tls_service`` is observed."""
        svc = _TlsService()
        # Patch the canonical symbol AFTER construction.
        fake_service = MagicMock()
        fake_service.install.return_value = MagicMock(
            to_dict=lambda: {"present": True},
        )
        with patch(
            "media_stack.api.tls_factory.build_default_tls_service",
            return_value=fake_service,
        ):
            # Stub the envoy reload helpers so we don't touch
            # admin_svc / envoy generator on the test path.
            svc._envoy_restarter = lambda _name: {"status": "ok"}
            svc._envoy_config_generator = MagicMock()
            svc._envoy_config_generator.main = lambda _: None
            with patch.dict(
                "os.environ", {"CONFIG_ROOT": "/nonexistent"},
                clear=False,
            ):
                payload = svc.install("cert", "key")
        assert payload["installed"] is True
        fake_service.install.assert_called_once_with("cert", "key")


# ---------------------------------------------------------------------------
# Defence-in-depth: redaction helper is exposed
# ---------------------------------------------------------------------------


class TestRedactionHelperExposed:
    """The module re-exports ``redact_api_key_map`` so future
    callers have it within reach. Pin that the symbol is callable
    + returns the safe shape (``has_key`` + ``fingerprint``;
    NEVER the raw key)."""

    def test_redact_api_key_map_returns_safe_shape(self) -> None:
        out = redact_api_key_map(
            {"sonarr": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"},
            source="env",
        )
        assert "sonarr" in out
        assert out["sonarr"]["has_key"] is True
        assert out["sonarr"]["source"] == "env"
        # The fingerprint is a short shape; the raw key MUST NOT
        # appear anywhere in the safe-shape dict.
        assert (
            "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
            not in json.dumps(out)
        )
