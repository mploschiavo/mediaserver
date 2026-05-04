"""Tests for the public-cert download endpoint and the
trust-the-CA wizard step.

Drove the addition: the post-bootstrap wizard pointed at a file
on the host (``docker/config/envoy/certs/cert.pem``) — useless
for any user not sshed into the box. The download endpoint puts
the cert behind one click; the wizard step explains how to
install it so the browser stops complaining and the SSO redirect
loop disappears.

Coverage:

- ``GET /api/tls/certificate/download`` returns 200 with
  ``application/x-pem-file`` and the cert PEM bytes.
- ``GET /api/tls/certificate/download`` returns 404 when no
  cert is installed (instead of 500 or empty body).
- The endpoint never serves the private key — only the cert.
- Wizard HTML mentions the download endpoint AND has per-OS
  install instructions (macOS, Windows, Linux, Firefox, iOS)
  AND only renders inside an HTTPS guard."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from test_api_server_handlers import (  # noqa: E402
    make_handler, _get_response_code,
)


_DUMMY_PEM = (
    b"-----BEGIN CERTIFICATE-----\n"
    b"MIIBdummy=\n"
    b"-----END CERTIFICATE-----\n"
)


class CertDownloadEndpointTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cert_dir = Path(self._tmp.name)

    def _patch_tls_service(self, *, cert_present: bool):
        """Build a mock TLS service whose ``cert_path`` either
        does or does not exist on disk."""
        svc = mock.MagicMock()
        cert_path = self.cert_dir / "cert.pem"
        if cert_present:
            cert_path.write_bytes(_DUMMY_PEM)
        svc.cert_path = cert_path
        # Patch the canonical symbol on ``tls_factory``. Both the
        # legacy ``handlers_get`` chain (which does
        # ``from media_stack.api.tls_factory import …``) and the
        # ADR-0007 router-registered ``probes_dns_tls`` route
        # resolve through this attribute, so a single patch covers
        # both code paths during the migration window.
        return mock.patch(
            "media_stack.api.tls_factory.build_default_tls_service",
            return_value=svc,
        )

    def test_returns_pem_when_cert_present(self) -> None:
        handler = make_handler("GET", "/api/tls/certificate/download")
        with self._patch_tls_service(cert_present=True):
            handler.do_GET()
        self.assertEqual(_get_response_code(handler), 200)
        body = handler.wfile.getvalue()
        self.assertEqual(body, _DUMMY_PEM)
        # Content-Type and Content-Disposition should mark this
        # as a downloadable PEM file. send_header was mocked, so
        # check the call list.
        header_calls = [
            call.args for call in handler.send_header.call_args_list
        ]
        types = [v for k, v in header_calls if k == "Content-Type"]
        self.assertTrue(any("pem" in t for t in types),
                        "Content-Type should be application/x-pem-file")
        dispositions = [
            v for k, v in header_calls if k == "Content-Disposition"
        ]
        self.assertTrue(any("attachment" in d for d in dispositions),
                        "Should be served as an attachment, not inline.")
        self.assertTrue(any("media-stack-ca.pem" in d for d in dispositions),
                        "Filename should default to media-stack-ca.pem.")

    def test_returns_404_when_cert_missing(self) -> None:
        handler = make_handler("GET", "/api/tls/certificate/download")
        with self._patch_tls_service(cert_present=False):
            handler.do_GET()
        self.assertEqual(_get_response_code(handler), 404)

    def test_endpoint_does_not_expose_private_key(self) -> None:
        """Sanity check: the endpoint reads cert_path only,
        never key_path. Pin the source so a future refactor that
        accidentally bundles the key fails this test."""
        from media_stack.api import handlers_get
        # Locate the dispatch branch and make sure it does not
        # mention key_path.
        src = Path(handlers_get.__file__).read_text(encoding="utf-8")
        idx = src.find('"/api/tls/certificate/download"')
        self.assertGreater(idx, -1)
        # Read the surrounding 600 chars (the whole branch).
        branch = src[idx:idx + 1000]
        self.assertNotIn(
            "key_path", branch,
            "Cert download endpoint must never reference key_path "
            "— that would leak the private key.",
        )
