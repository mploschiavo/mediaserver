"""Unit tests for _TrustedProxyAuth.

Covers the four cases that matter:
  - Disabled (env not set)                        → None
  - Enabled, IP inside CIDR, header present       → identity returned
  - Enabled, IP inside CIDR, header absent        → None
  - Enabled, IP OUTSIDE CIDR, header present      → None  (spoof rejected)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.session_singletons import TrustedProxyAuth as _TrustedProxyAuth  # noqa: E402


class _FakeHeaders:
    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def get(self, name: str, default: str = "") -> str:
        return self._m.get(name, default)


class _FakeHandler:
    def __init__(self, client_ip: str, headers: dict) -> None:
        self.client_address = (client_ip, 0)
        self.headers = _FakeHeaders(headers)


class TrustedProxyAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth = _TrustedProxyAuth()

    def test_disabled_when_env_not_set(self):
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": ""},
            clear=False,
        ):
            h = _FakeHandler("10.0.0.5", {"Remote-User": "alice"})
            self.assertIsNone(self.auth.identity(h))

    def test_accepts_identity_from_trusted_cidr(self):
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"},
            clear=False,
        ):
            h = _FakeHandler("10.5.1.2", {"Remote-User": "alice"})
            self.assertEqual(self.auth.identity(h), "alice")

    def test_rejects_spoofed_header_from_untrusted_ip(self):
        """Attacker from the open internet sends Remote-User=admin.
        Without the trusted-proxy CIDR covering their IP, we MUST NOT
        honor the header."""
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"},
            clear=False,
        ):
            h = _FakeHandler("203.0.113.7", {"Remote-User": "admin"})
            self.assertIsNone(self.auth.identity(h))

    def test_trusted_cidr_no_header_means_no_identity(self):
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"},
            clear=False,
        ):
            h = _FakeHandler("10.0.0.5", {})
            self.assertIsNone(self.auth.identity(h))

    def test_custom_header_name_honored(self):
        with mock.patch.dict(
            "os.environ",
            {
                "CONTROLLER_TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
                "CONTROLLER_TRUSTED_PROXY_HEADER": "X-Authelia-User",
            },
            clear=False,
        ):
            h = _FakeHandler("10.0.0.1", {"X-Authelia-User": "bob"})
            self.assertEqual(self.auth.identity(h), "bob")

    def test_multiple_cidrs_comma_separated(self):
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "192.168.0.0/16, 10.0.0.0/8"},
            clear=False,
        ):
            h = _FakeHandler("192.168.5.7", {"Remote-User": "carol"})
            self.assertEqual(self.auth.identity(h), "carol")

    def test_malformed_cidr_silently_ignored(self):
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "garbage,10.0.0.0/8"},
            clear=False,
        ):
            h = _FakeHandler("10.0.0.1", {"Remote-User": "dave"})
            self.assertEqual(self.auth.identity(h), "dave")

    def test_empty_header_value_returns_none(self):
        """If Authelia returns Remote-User="" (unauthenticated upstream),
        we must NOT treat empty-string as a valid identity."""
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"},
            clear=False,
        ):
            h = _FakeHandler("10.0.0.1", {"Remote-User": ""})
            self.assertIsNone(self.auth.identity(h))


if __name__ == "__main__":
    unittest.main()
