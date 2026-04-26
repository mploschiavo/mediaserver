"""Unit tests for TlsCertificateService."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.edge.tls_certificate_service import (
    TlsCertificateService,
    TlsCertificateServiceError,
)


def _openssl_available() -> bool:
    return shutil.which("openssl") is not None


@unittest.skipUnless(_openssl_available(), "openssl binary required")
class TlsCertificateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.svc = TlsCertificateService(cert_dir=Path(self._tmp.name))

    def _gen_sample_pair(self) -> tuple[str, str]:
        out_dir = tempfile.mkdtemp()
        cert = Path(out_dir) / "c.pem"
        key = Path(out_dir) / "k.pem"
        subprocess.check_output([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert),
            "-days", "30", "-nodes",
            "-subj", "/CN=test.media-stack.local",
            "-addext", "subjectAltName=DNS:test.media-stack.local",
        ], stderr=subprocess.STDOUT, timeout=30)
        return cert.read_text(), key.read_text()

    def test_describe_returns_absent_when_no_cert(self):
        info = self.svc.describe()
        self.assertFalse(info.present)

    def test_regenerate_writes_cert_and_key(self):
        info = self.svc.regenerate(
            hostnames=["test.media-stack.local"], days=30,
        )
        self.assertTrue(info.present)
        self.assertIn("media-stack", info.subject.lower())
        self.assertTrue(self.svc.cert_path.is_file())
        self.assertTrue(self.svc.key_path.is_file())
        # Key is written 0o644 so Envoy (uid 1000, read-only mount)
        # can read it regardless of which uid the controller runs as.
        # See module-level _MODE_PRIVATE note in tls_certificate_service.
        self.assertEqual(self.svc.key_path.stat().st_mode & 0o777, 0o644)

    def test_install_accepts_matched_pair(self):
        cert_pem, key_pem = self._gen_sample_pair()
        info = self.svc.install(cert_pem, key_pem)
        self.assertTrue(info.present)
        self.assertEqual(
            self.svc.cert_path.read_text().strip(), cert_pem.strip(),
        )

    def test_install_rejects_cert_without_begin_marker(self):
        _, key_pem = self._gen_sample_pair()
        with self.assertRaises(TlsCertificateServiceError):
            self.svc.install("not a cert", key_pem)

    def test_install_rejects_key_without_begin_marker(self):
        cert_pem, _ = self._gen_sample_pair()
        with self.assertRaises(TlsCertificateServiceError):
            self.svc.install(cert_pem, "not a key")

    def test_install_rejects_mismatched_pair(self):
        cert_pem, _ = self._gen_sample_pair()
        _, mismatched_key = self._gen_sample_pair()
        with self.assertRaises(TlsCertificateServiceError):
            self.svc.install(cert_pem, mismatched_key)

    def test_describe_never_returns_private_key(self):
        cert_pem, key_pem = self._gen_sample_pair()
        self.svc.install(cert_pem, key_pem)
        info = self.svc.describe().to_dict()
        # Defensive: assert no private-key marker leaks into any string field.
        for k, v in info.items():
            if isinstance(v, str):
                self.assertNotIn("BEGIN PRIVATE KEY", v, f"leaked in {k}")
                self.assertNotIn("BEGIN RSA PRIVATE KEY", v, f"leaked in {k}")


if __name__ == "__main__":
    unittest.main()
