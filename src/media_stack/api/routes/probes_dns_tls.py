"""Networking probes + TLS certificate GET routes
(ADR-0007 Phase 2 wave 4).

Six routes migrated off the ``handlers_get.handle()`` elif chain
covering the cluster-network diagnostic surface
(``Networking`` tag) and the TLS operator card
(``TLS`` tag):

* ``GET /api/dns-check`` — lightweight DNS-resolution probe
  (single-host with ``?host=`` or bulk-by-routing-config when no
  query string is supplied).
* ``GET /api/route-probe`` — server-side reachability probe of
  one URL. Bypasses three browser-side traps: mixed-content
  blocking, self-signed cert errors, and the opaque-response
  shape of ``no-cors`` ``fetch``.
* ``GET /api/routing-probe`` — server-side matrix probe across
  every service's four user-facing access URLs (localhost,
  gateway, subdomain, direct).
* ``GET /api/gateway-hostnames`` — hostnames Envoy is configured
  to serve, derived from routing config (NOT scraped from the
  rendered envoy.yaml — see ``_GatewayHostnameProbe`` docstring
  for the regex-on-Lua-identifiers pitfall).
* ``GET /api/tls/certificate`` — describe the controller's
  installed TLS certificate (subject, issuer, validity dates,
  SANs, fingerprint).
* ``GET /api/tls/certificate/download`` — public cert PEM as a
  browser-friendly download (NEVER the private key). Returns
  ``application/x-pem-file`` via ``handler._raw_response`` —
  the only non-JSON route in this module.

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* The two complex probes (``routing-probe`` + ``gateway-hostnames``)
  are owned by stateful classes (``_RoutingMatrixProbe`` +
  ``_GatewayHostnameProbe``) defined in ``handlers_get`` whose
  module-level instances ARE the source of truth. The route
  module imports those instances and delegates verbatim — moving
  the classes themselves would be a larger refactor that doesn't
  belong in a Phase 2 wave. Tests can patch the singleton symbols
  on this module to control probe behaviour.
* ``dns-check`` + ``route-probe`` lift the legacy bodies (query-
  string parse + service-module call). The legacy chain
  defer-imports ``api.services.dns_check`` / ``api.services.route_probe``
  inside the elif branch; we keep the same lazy shape so the
  route module's import graph stays tight at startup.
* ``tls/certificate`` + ``tls/certificate/download`` go through
  ``build_default_tls_service`` (constructor-injected as
  ``_tls_service_factory`` — defaults to the package singleton
  but tests pass a stub). The download branch uses
  ``_raw_response`` because PEM bytes need ``application/x-pem-file``
  + a ``Content-Disposition`` header — the same shape as
  ``epg.py``'s ``feed.xml`` route.

OO discipline: ``RouteModule`` subclass; instance-method handlers
tagged with ``@get(path)``; a small ``_QueryStringReader`` helper
class encapsulates the ``parse_qs(urlparse(...).query)`` shape
shared between ``dns-check`` and ``route-probe`` so neither
handler hand-rolls the same two-line dance. No ``@staticmethod``.
No loose top-level handler functions. Constructor-injected
dependencies (TLS factory, query-string reader, probe singletons)
all carry sensible defaults for production wiring.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from media_stack.api.routing import RouteModule, get


_PEM_CONTENT_TYPE = "application/x-pem-file"
_PEM_DOWNLOAD_FILENAME = "media-stack-ca.pem"
_ERR_LEN = 99  # matches the convention used across handlers_get/post


class _QueryStringReader:
    """Reads a single query-string parameter off a handler's
    ``handler.path``.

    Encapsulates the ``parse_qs(urlparse(handler.path).query)``
    pattern that the legacy ``dns-check`` and ``route-probe``
    branches both hand-rolled inline. Returning the first value (or
    empty string) matches the legacy ``(qs.get(name) or [""])[0]``
    shape exactly.
    """

    def first_value(self, full_path: str, key: str) -> str:
        qs = parse_qs(urlparse(full_path).query)
        values = qs.get(key) or [""]
        return values[0]


class ProbesDnsTlsGetRoutes(RouteModule):
    """Networking + TLS GET routes. The Router auto-discovers and
    instantiates this class at startup, then walks tagged methods
    for registration.

    Constructor-injected dependencies:

    * ``query_reader`` — pulls a single query-string value off
      ``handler.path``. Default is ``_QueryStringReader()``; tests
      can pass a stub if they want to bypass URL parsing entirely.
    * ``tls_service_factory`` — zero-arg callable returning a
      ``TlsCertificateService``. Default is the package singleton
      ``build_default_tls_service``; tests pass a lambda returning
      a stub service.
    * ``routing_matrix_probe`` / ``gateway_hostname_probe`` —
      probe singletons with ``probe_all()`` / ``read()`` methods.
      Defaults are the legacy instances in ``handlers_get`` so the
      route registration produces identical results to the legacy
      chain. Tests pass stubs here to keep dispatch hermetic.
    """

    def __init__(
        self,
        *,
        query_reader: _QueryStringReader | None = None,
        tls_service_factory: Callable[[], Any] | None = None,
        routing_matrix_probe: Any = None,
        gateway_hostname_probe: Any = None,
    ) -> None:
        self._query_reader = query_reader or _QueryStringReader()
        # Lazy default-resolution: the legacy probe singletons live
        # in ``handlers_get`` and importing that module pulls a
        # large dep graph. Defer the import until first use so route
        # module construction stays cheap.
        self._tls_service_factory = tls_service_factory
        self._routing_matrix_probe = routing_matrix_probe
        self._gateway_hostname_probe = gateway_hostname_probe

    # --- Lazy singleton resolvers ----------------------------------

    def _resolve_tls_factory(self) -> Callable[[], Any]:
        if self._tls_service_factory is not None:
            return self._tls_service_factory
        # Fresh attribute lookup on the tls_factory module each call
        # so ``mock.patch`` on the canonical symbol takes effect.
        # (Caching the default would freeze the pre-patch reference
        # and break tests that patch the singleton symbol.)
        from media_stack.api import tls_factory
        return tls_factory.build_default_tls_service

    def _resolve_routing_matrix_probe(self) -> Any:
        if self._routing_matrix_probe is not None:
            return self._routing_matrix_probe
        # Fresh attribute lookup so test patches reach the call site
        # (caching the default would freeze the pre-patch reference).
        from media_stack.api.services import routing_probes
        return routing_probes._routing_matrix_probe

    def _resolve_gateway_hostname_probe(self) -> Any:
        if self._gateway_hostname_probe is not None:
            return self._gateway_hostname_probe
        from media_stack.api.services import routing_probes
        return routing_probes._gateway_hostname_probe

    # --- Routes -----------------------------------------------------

    @get("/api/dns-check")
    def handle_dns_check(self, handler: Any) -> None:
        """Lightweight DNS reachability probe.

        Two modes, distinguished by the ``host`` query param:

        * ``?host=<name>`` — single-host check returning a flat
          object (used by the in-form save validator on the
          Routing tab).
        * No query string — bulk check across every hostname the
          routing config implies. Returns
          ``{"entries": [...]}`` for the SPA's DNS-resolution
          table, which pre-populates from routing config rather
          than asking the operator to type each hostname.

        DNS-only — never opens an HTTP connection. Body lifted
        from ``handlers_get`` line 356 verbatim except the
        query-parse is delegated to ``_QueryStringReader``.
        """
        try:
            from media_stack.api.services import dns_check as dns_check_svc
            host = self._query_reader.first_value(handler.path, "host")
            if host.strip():
                result = dns_check_svc.check(host)
            else:
                result = dns_check_svc.check_all()
            handler._json_response(HTTPStatus.OK, result)
        except Exception as exc:  # noqa: BLE001
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:_ERR_LEN]},
            )

    @get("/api/route-probe")
    def handle_route_probe(self, handler: Any) -> None:
        """Server-side reachability probe for the dashboard's
        "Test All Paths" matrix.

        Bypasses three structural browser-side problems:
        mixed-content blocking, self-signed cert errors, and the
        opaque-response trap of ``fetch(..., {mode: 'no-cors'})``.
        See ``api/services/route_probe.py`` for the full why.
        """
        try:
            from media_stack.api.services import (
                route_probe as route_probe_svc,
            )
            target = self._query_reader.first_value(handler.path, "url")
            handler._json_response(
                HTTPStatus.OK, route_probe_svc.probe(target),
            )
        except Exception as exc:  # noqa: BLE001
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:_ERR_LEN]},
            )

    @get("/api/routing-probe")
    def handle_routing_probe(self, handler: Any) -> None:
        """Server-side matrix probe across every service's four
        user-facing access URLs (localhost, gateway, subdomain,
        direct).

        Delegates to the ``_RoutingMatrixProbe`` singleton in
        ``handlers_get`` — that class owns the per-platform
        listener-port logic (compose's 8080/8880 vs k8s's
        single-port Service) and the TLS-disabled HTTP probing
        shape that's load-bearing for self-signed setups.
        """
        try:
            handler._json_response(
                HTTPStatus.OK,
                self._resolve_routing_matrix_probe().probe_all(),
            )
        except Exception as exc:  # noqa: BLE001
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                # Legacy used a tighter cap (80) here only — kept
                # for behaviour parity with the v1.0.165 baseline.
                {"error": str(exc)[:80]},
            )

    @get("/api/gateway-hostnames")
    def handle_gateway_hostnames(self, handler: Any) -> None:
        """Hostnames Envoy is configured to serve.

        Derived from routing config (``base_domain``,
        ``stack_subdomain``, ``gateway_host``, ``direct_hosts``) —
        NEVER regex-scraped from the rendered envoy.yaml. The
        legacy "secondary" envoy.yaml scraper produced false
        positives matching inline-Lua identifiers and minified-JS
        token sequences — see ``_GatewayHostnameProbe`` docstring
        for the full bug class.
        """
        try:
            handler._json_response(
                HTTPStatus.OK,
                {"hostnames": self._resolve_gateway_hostname_probe().read()},
            )
        except Exception as exc:  # noqa: BLE001
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:_ERR_LEN]},
            )

    @get("/api/tls/certificate")
    def handle_tls_certificate(self, handler: Any) -> None:
        """Describe the controller's installed TLS certificate.

        Returns ``CertificateInfo.to_dict()`` —
        ``{present, subject, issuer, not_before, not_after,
        sans, fingerprint_sha256, path}``. ``present=False``
        with empty fields when no cert is installed (e.g. fresh
        compose bootstrap before regenerate).
        """
        try:
            info = self._resolve_tls_factory()().describe().to_dict()
            handler._json_response(HTTPStatus.OK, info)
        except Exception as exc:  # noqa: BLE001
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:_ERR_LEN]},
            )

    @get("/api/tls/certificate/download")
    def handle_tls_certificate_download(self, handler: Any) -> None:
        """Browser-friendly download of the public cert PEM.

        Returns ``application/x-pem-file`` bytes via
        ``_raw_response`` (NOT JSON) so a one-click "trust this
        CA" works from the post-bootstrap wizard. Only the
        public cert is returned — the private key is intentionally
        never read by this handler. Returns 404 when no cert is
        present.
        """
        try:
            cert_path = self._resolve_tls_factory()().cert_path
            if not cert_path.is_file():
                handler._json_response(
                    HTTPStatus.NOT_FOUND,
                    {"error": "no certificate installed"},
                )
                return
            pem = cert_path.read_bytes()
            handler._raw_response(
                HTTPStatus.OK,
                _PEM_CONTENT_TYPE,
                pem,
                {
                    "Content-Disposition":
                        f'attachment; filename="{_PEM_DOWNLOAD_FILENAME}"',
                },
            )
        except Exception as exc:  # noqa: BLE001
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:_ERR_LEN]},
            )


__all__ = ["ProbesDnsTlsGetRoutes"]
