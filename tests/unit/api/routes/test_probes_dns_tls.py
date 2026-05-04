"""Tests for ``api/routes/probes_dns_tls.py``
(ADR-0007 Phase 2 wave 4).

Each test class owns one route. Each test invokes the production
Router via ``RouteDispatchHarness.with_default_router()`` — same
auto-discovery, same spec-parity check, same dispatch path used
in production.

Patch targets:

* ``dns-check`` + ``route-probe`` defer-import their service
  modules inside the handler — patch the symbols on the
  service's source module (``media_stack.api.services.dns_check``
  / ``media_stack.api.services.route_probe``) so the lazy
  ``from … import …`` resolves to the mock.
* ``routing-probe`` + ``gateway-hostnames`` go through singleton
  references defer-imported from ``handlers_get``. Tests construct
  the route module with stub probes injected via the constructor
  to keep the handler hermetic, but also include one
  patch-the-module test pinning the lazy-resolution path so
  default wiring is exercised.
* ``tls/certificate`` + ``tls/certificate/download`` go through
  the constructor-injected ``tls_service_factory`` callable —
  tests inject a stub returning a fake service whose
  ``describe()`` / ``cert_path`` shape matches the production
  ``TlsCertificateService`` surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from media_stack.api.routes.probes_dns_tls import (
    ProbesDnsTlsGetRoutes,
    _QueryStringReader,
)
from media_stack.api.routing import DefaultDispatcher, DispatchOutcome
from tests.unit.api.routes._helpers import (
    CapturedResponse,
    MockControllerHandler,
    RouteDispatchHarness,
)


def _dispatch_with_query(
    verb: str, path_with_query: str,
) -> CapturedResponse:
    """Mimic the production dispatch path: strip the query string
    before route-matching, but leave it on ``handler.path`` so the
    handler-side ``_QueryStringReader`` reparse finds the params.

    The shared ``RouteDispatchHarness.dispatch`` doesn't strip
    today (it passes ``path`` to both the dispatcher AND the
    handler). Production strips at ``server.py`` before invoking
    the dispatcher; this helper simulates that step for the tests
    that need a query string. Same shape as ``test_logs.py``'s
    helper.
    """
    DefaultDispatcher.reset_for_tests()
    dispatcher = DefaultDispatcher.instance()
    bare_path = path_with_query.split("?", 1)[0]
    handler = MockControllerHandler(path=path_with_query)
    outcome = dispatcher.try_dispatch(verb, bare_path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        dispatcher.write_method_not_allowed(handler, bare_path)
    return handler.captured


# ---------------------------------------------------------------------------
# _QueryStringReader unit tests
# ---------------------------------------------------------------------------


class TestQueryStringReader:
    """The shared helper that pulls a single query-string value off
    a handler ``path``. Pinned because both ``dns-check`` and
    ``route-probe`` rely on the empty-string-when-missing shape."""

    def test_returns_first_value_for_known_key(self) -> None:
        reader = _QueryStringReader()
        assert reader.first_value(
            "/api/dns-check?host=jellyfin.example.com", "host",
        ) == "jellyfin.example.com"

    def test_returns_empty_string_when_key_absent(self) -> None:
        reader = _QueryStringReader()
        assert reader.first_value(
            "/api/dns-check?other=1", "host",
        ) == ""

    def test_returns_empty_string_when_no_query_at_all(self) -> None:
        reader = _QueryStringReader()
        assert reader.first_value("/api/dns-check", "host") == ""

    def test_returns_first_value_when_repeated(self) -> None:
        reader = _QueryStringReader()
        assert reader.first_value(
            "/api/route-probe?url=a&url=b", "url",
        ) == "a"

    def test_url_decoded_value(self) -> None:
        reader = _QueryStringReader()
        assert reader.first_value(
            "/api/dns-check?host=foo%2Ebar", "host",
        ) == "foo.bar"


# ---------------------------------------------------------------------------
# DNS-check route
# ---------------------------------------------------------------------------


class TestDnsCheckRoute:
    """``GET /api/dns-check`` — bulk vs single-host modes routed
    by the ``host`` query parameter."""

    @patch("media_stack.api.services.dns_check.check_all")
    def test_no_query_string_calls_check_all(
        self, mock_check_all,
    ) -> None:
        mock_check_all.return_value = {
            "entries": [
                {"host": "apps.media-stack.local", "resolves": True},
            ],
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/dns-check")

        assert response.status == 200
        body = json.loads(response.body)
        assert body == {
            "entries": [
                {"host": "apps.media-stack.local", "resolves": True},
            ],
        }
        mock_check_all.assert_called_once_with()

    @patch("media_stack.api.services.dns_check.check")
    def test_host_query_calls_check_single(self, mock_check) -> None:
        mock_check.return_value = {
            "host": "jellyfin.example.com",
            "resolves": True,
            "resolved_ip": "10.1.55.177",
        }
        response = _dispatch_with_query(
            "GET", "/api/dns-check?host=jellyfin.example.com",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["host"] == "jellyfin.example.com"
        assert body["resolves"] is True
        mock_check.assert_called_once_with("jellyfin.example.com")

    @patch("media_stack.api.services.dns_check.check_all")
    def test_blank_host_treated_as_bulk(self, mock_check_all) -> None:
        """``?host=`` (empty) and ``?host=   `` (whitespace) both
        fall through to ``check_all`` — the legacy chain tested
        ``host.strip()``."""
        mock_check_all.return_value = {"entries": []}
        response = _dispatch_with_query("GET", "/api/dns-check?host=")
        assert response.status == 200
        mock_check_all.assert_called_once_with()

    @patch("media_stack.api.services.dns_check.check_all")
    def test_internal_error_returns_500_with_error_payload(
        self, mock_check_all,
    ) -> None:
        mock_check_all.side_effect = RuntimeError("dns boom")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/dns-check")

        assert response.status == 500
        body = json.loads(response.body)
        assert "dns boom" in body["error"]


# ---------------------------------------------------------------------------
# Route-probe route
# ---------------------------------------------------------------------------


class TestRouteProbeRoute:
    """``GET /api/route-probe`` — single-URL server-side reach
    probe."""

    @patch("media_stack.api.services.route_probe.probe")
    def test_passes_url_query_param_to_service(self, mock_probe) -> None:
        mock_probe.return_value = {
            "url": "https://jellyfin.example.com/",
            "ok": True,
            "status": 200,
            "elapsed_ms": 32,
            "location": "",
            "error": "",
        }
        response = _dispatch_with_query(
            "GET",
            "/api/route-probe?url=https://jellyfin.example.com/",
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["ok"] is True
        assert body["status"] == 200
        mock_probe.assert_called_once_with(
            "https://jellyfin.example.com/",
        )

    @patch("media_stack.api.services.route_probe.probe")
    def test_missing_url_passes_empty_string_to_service(
        self, mock_probe,
    ) -> None:
        """No ``url=`` query → service called with ``""``; the
        service returns its own empty-url error sentinel.
        """
        mock_probe.return_value = {
            "url": "", "ok": False, "status": 0, "elapsed_ms": 0,
            "location": "", "error": "empty url",
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/route-probe")

        assert response.status == 200
        body = json.loads(response.body)
        assert body["error"] == "empty url"
        mock_probe.assert_called_once_with("")

    @patch("media_stack.api.services.route_probe.probe")
    def test_internal_error_returns_500(self, mock_probe) -> None:
        mock_probe.side_effect = RuntimeError("probe failed")
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/route-probe")

        assert response.status == 500
        body = json.loads(response.body)
        assert "probe failed" in body["error"]


# ---------------------------------------------------------------------------
# Routing-probe route
# ---------------------------------------------------------------------------


class TestRoutingProbeRoute:
    """``GET /api/routing-probe`` — matrix probe across every
    service. The route delegates to a ``probe_all()``-shaped probe
    object resolved out of ``handlers_get`` by default; tests use
    direct construction to inject a stub."""

    def test_delegates_to_injected_probe(self) -> None:
        stub_probe = MagicMock()
        stub_probe.probe_all.return_value = {
            "rows": [
                {"app": "jellyfin", "ok": True, "status_code": 200},
            ],
            "routing": {
                "scheme": "https",
                "gateway_port": 443,
                "gateway_host": "apps.example.com",
                "app_path_prefix": "/app",
            },
            "services": {"jellyfin": {}},
        }
        module = ProbesDnsTlsGetRoutes(routing_matrix_probe=stub_probe)
        handler = MockControllerHandler(path="/api/routing-probe")

        module.handle_routing_probe(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["routing"]["scheme"] == "https"
        assert body["rows"][0]["app"] == "jellyfin"
        stub_probe.probe_all.assert_called_once_with()

    def test_probe_exception_returns_500(self) -> None:
        stub_probe = MagicMock()
        stub_probe.probe_all.side_effect = RuntimeError("matrix down")
        module = ProbesDnsTlsGetRoutes(routing_matrix_probe=stub_probe)
        handler = MockControllerHandler(path="/api/routing-probe")

        module.handle_routing_probe(handler)

        assert handler.captured.status == 500
        body = json.loads(handler.captured.body)
        assert "matrix down" in body["error"]

    @patch("media_stack.api.services.routing_probes._routing_matrix_probe")
    def test_default_resolution_pulls_singleton_from_handlers_get(
        self, mock_singleton,
    ) -> None:
        """The Router-resolved instance has no probe injected; the
        first call resolves the routing-probes service singleton
        lazily. Pinning this contract guards against accidental
        hard-coupling at construction time (which would fire module
        import side effects on Router build)."""
        mock_singleton.probe_all.return_value = {
            "rows": [], "routing": {}, "services": {},
        }
        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/api/routing-probe")

        assert response.status == 200
        mock_singleton.probe_all.assert_called_once_with()


# ---------------------------------------------------------------------------
# Gateway-hostnames route
# ---------------------------------------------------------------------------


class TestGatewayHostnamesRoute:
    """``GET /api/gateway-hostnames`` — hostname list derived from
    routing config."""

    def test_delegates_to_injected_probe(self) -> None:
        stub_probe = MagicMock()
        stub_probe.read.return_value = [
            "apps.example.com",
            "auth.example.com",
            "jellyfin.example.com",
        ]
        module = ProbesDnsTlsGetRoutes(gateway_hostname_probe=stub_probe)
        handler = MockControllerHandler(path="/api/gateway-hostnames")

        module.handle_gateway_hostnames(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {
            "hostnames": [
                "apps.example.com",
                "auth.example.com",
                "jellyfin.example.com",
            ],
        }
        stub_probe.read.assert_called_once_with()

    def test_empty_hostname_list(self) -> None:
        stub_probe = MagicMock()
        stub_probe.read.return_value = []
        module = ProbesDnsTlsGetRoutes(gateway_hostname_probe=stub_probe)
        handler = MockControllerHandler(path="/api/gateway-hostnames")

        module.handle_gateway_hostnames(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {"hostnames": []}

    def test_probe_exception_returns_500(self) -> None:
        stub_probe = MagicMock()
        stub_probe.read.side_effect = OSError("config read failed")
        module = ProbesDnsTlsGetRoutes(gateway_hostname_probe=stub_probe)
        handler = MockControllerHandler(path="/api/gateway-hostnames")

        module.handle_gateway_hostnames(handler)

        assert handler.captured.status == 500
        body = json.loads(handler.captured.body)
        assert "config read failed" in body["error"]


# ---------------------------------------------------------------------------
# TLS certificate describe route
# ---------------------------------------------------------------------------


class _StubCertInfo:
    """Drop-in stand-in for ``CertificateInfo`` exposing the same
    ``to_dict()`` shape the production handler reads.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _StubTlsService:
    """``TlsCertificateService``-shaped stub. Tests configure
    ``describe_payload`` and ``cert_path`` to drive the route's
    branches. Keeps the test free of ``openssl`` subprocess calls
    + on-disk cert files."""

    def __init__(
        self,
        *,
        describe_payload: dict[str, Any] | None = None,
        cert_path: Path | None = None,
        describe_exc: Exception | None = None,
    ) -> None:
        self._describe_payload = describe_payload or {}
        self._cert_path = cert_path or Path("/nonexistent/cert.crt")
        self._describe_exc = describe_exc

    @property
    def cert_path(self) -> Path:
        return self._cert_path

    def describe(self) -> _StubCertInfo:
        if self._describe_exc is not None:
            raise self._describe_exc
        return _StubCertInfo(self._describe_payload)


class TestTlsCertificateRoute:
    """``GET /api/tls/certificate`` — describe shape."""

    def test_returns_cert_info_dict_when_present(self) -> None:
        stub_service = _StubTlsService(describe_payload={
            "present": True,
            "subject": "CN=*.media-stack.local",
            "issuer": "CN=*.media-stack.local",
            "not_before": "2026-01-01T00:00:00Z",
            "not_after": "2027-01-01T00:00:00Z",
            "sans": ["DNS:*.media-stack.local"],
            "fingerprint_sha256": "ab:cd:ef",
            "path": "/srv-config/certs/media-stack.crt",
        })
        module = ProbesDnsTlsGetRoutes(
            tls_service_factory=lambda: stub_service,
        )
        handler = MockControllerHandler(path="/api/tls/certificate")

        module.handle_tls_certificate(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["present"] is True
        assert body["subject"] == "CN=*.media-stack.local"
        assert body["sans"] == ["DNS:*.media-stack.local"]

    def test_returns_present_false_when_no_cert(self) -> None:
        stub_service = _StubTlsService(describe_payload={
            "present": False,
            "path": "/srv-config/certs/media-stack.crt",
            "subject": "", "issuer": "",
            "not_before": "", "not_after": "",
            "sans": [], "fingerprint_sha256": "",
        })
        module = ProbesDnsTlsGetRoutes(
            tls_service_factory=lambda: stub_service,
        )
        handler = MockControllerHandler(path="/api/tls/certificate")

        module.handle_tls_certificate(handler)

        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["present"] is False

    def test_describe_exception_returns_500(self) -> None:
        stub_service = _StubTlsService(
            describe_exc=RuntimeError("openssl crashed"),
        )
        module = ProbesDnsTlsGetRoutes(
            tls_service_factory=lambda: stub_service,
        )
        handler = MockControllerHandler(path="/api/tls/certificate")

        module.handle_tls_certificate(handler)

        assert handler.captured.status == 500
        body = json.loads(handler.captured.body)
        assert "openssl crashed" in body["error"]


# ---------------------------------------------------------------------------
# TLS certificate download route
# ---------------------------------------------------------------------------


class TestTlsCertificateDownloadRoute:
    """``GET /api/tls/certificate/download`` — raw PEM download.

    Returns ``application/x-pem-file`` (NOT JSON) via
    ``_raw_response`` when the cert exists; falls through to a
    JSON 404 when missing. The private key is intentionally
    never read in this branch.
    """

    def test_returns_pem_bytes_with_content_type_and_disposition(
        self, tmp_path: Path,
    ) -> None:
        cert_path = tmp_path / "media-stack.crt"
        pem_bytes = (
            b"-----BEGIN CERTIFICATE-----\n"
            b"MIIDazCCAlOgAwIBAgIUFakeBytesForTestingOnly\n"
            b"-----END CERTIFICATE-----\n"
        )
        cert_path.write_bytes(pem_bytes)

        stub_service = _StubTlsService(cert_path=cert_path)
        module = ProbesDnsTlsGetRoutes(
            tls_service_factory=lambda: stub_service,
        )
        handler = MockControllerHandler(
            path="/api/tls/certificate/download",
        )

        module.handle_tls_certificate_download(handler)

        assert handler.captured.status == 200
        assert handler.captured.content_type == "application/x-pem-file"
        assert handler.captured.body == pem_bytes
        # Content-Disposition must specify a filename so browsers
        # save (rather than render) the response.
        disp = handler.captured.extra_headers.get("Content-Disposition")
        assert disp is not None
        assert 'filename="media-stack-ca.pem"' in disp
        assert disp.startswith("attachment;")

    def test_returns_404_when_cert_missing(
        self, tmp_path: Path,
    ) -> None:
        missing_path = tmp_path / "nope.crt"  # never written
        stub_service = _StubTlsService(cert_path=missing_path)
        module = ProbesDnsTlsGetRoutes(
            tls_service_factory=lambda: stub_service,
        )
        handler = MockControllerHandler(
            path="/api/tls/certificate/download",
        )

        module.handle_tls_certificate_download(handler)

        assert handler.captured.status == 404
        # 404 path is JSON, not PEM — sanity-check the shape so a
        # future refactor doesn't accidentally serve PEM with a
        # 404 status.
        assert handler.captured.content_type == "application/json"
        body = json.loads(handler.captured.body)
        assert body == {"error": "no certificate installed"}

    def test_read_failure_returns_500(self, tmp_path: Path) -> None:
        """If the cert path passes ``is_file()`` but reading bytes
        raises (e.g. permissions error), the handler must emit a
        500 — not crash the dispatcher loop."""
        # A directory-as-cert-path is a clean way to make
        # ``is_file()`` False, so we instead patch the read.
        cert_path = tmp_path / "media-stack.crt"
        cert_path.write_bytes(b"placeholder")
        stub_service = _StubTlsService(cert_path=cert_path)
        module = ProbesDnsTlsGetRoutes(
            tls_service_factory=lambda: stub_service,
        )
        handler = MockControllerHandler(
            path="/api/tls/certificate/download",
        )

        with patch.object(
            Path, "read_bytes",
            side_effect=PermissionError("cert unreadable"),
        ):
            module.handle_tls_certificate_download(handler)

        assert handler.captured.status == 500
        body = json.loads(handler.captured.body)
        assert "cert unreadable" in body["error"]

    @patch("media_stack.api.tls_factory.build_default_tls_service")
    def test_default_factory_resolution_lazy(
        self, mock_factory,
    ) -> None:
        """Pin that the route module does NOT call
        ``build_default_tls_service`` at construction — the import
        + invocation are deferred to first request. Otherwise a
        Router build on a controller that doesn't have a cert dir
        bound yet would crash before any route fires.
        """
        # Importing/constructing the module must not touch the
        # factory.
        ProbesDnsTlsGetRoutes()
        mock_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Spec-parity + auto-discovery integration
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the
    probes/dns/tls domain. If a future change accidentally drops a
    handler from the registry, this fires before any per-route test
    does.
    """

    _EXPECTED_PATHS = frozenset({
        "/api/dns-check",
        "/api/route-probe",
        "/api/routing-probe",
        "/api/gateway-hostnames",
        "/api/tls/certificate",
        "/api/tls/certificate/download",
    })

    def test_all_probes_dns_tls_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in self._EXPECTED_PATHS
        }
        assert registered == self._EXPECTED_PATHS, (
            f"Missing probes/dns/tls routes: "
            f"{self._EXPECTED_PATHS - registered}"
        )

    @pytest.mark.parametrize("path", sorted(_EXPECTED_PATHS))
    def test_post_to_get_only_path_returns_method_not_allowed(
        self, path: str,
    ) -> None:
        """All six routes are GET-only in the spec; POSTs must 405.

        Note: ``/api/tls/certificate`` does have a POST in the spec
        (install-cert), so this assertion holds only for the GET-
        only paths. We special-case it by skipping when POST is in
        the spec for that path.
        """
        harness = RouteDispatchHarness.with_default_router()
        from media_stack.api.routing import DispatchOutcome
        spec_paths = harness._dispatcher._router.spec_paths()
        verbs_for_path = spec_paths.get(path, frozenset())
        if "POST" in verbs_for_path:
            pytest.skip(
                f"{path} accepts POST in the OpenAPI spec — "
                f"the 405-on-POST contract doesn't apply.",
            )
        outcome, _ = harness.try_dispatch("POST", path)
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
