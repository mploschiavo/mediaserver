"""Tests for ``TrustedProxyAuth.client_ip`` — real-client resolution.

The old ``_client_ip`` returned ``handler.client_address[0]``. Behind
Envoy that's the pod IP, so IP bans targeted the wrong address. These
tests pin the new contract:

  * Direct-connect inside a trusted-proxy CIDR → walk
    ``X-Forwarded-For`` and return the first un-trusted hop.
  * Direct-connect outside the trusted CIDR list → XFF is ignored
    (attacker can set it) and the direct source is authoritative.
  * Missing / malformed / all-trusted XFF in the trusted-proxy path
    → strict fallback to ``''`` (never return a proxy IP).
  * IPv6 works through the same code path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.session_singletons import (  # noqa: E402
    TrustedProxyAuth as _TrustedProxyAuth,
)


class _FakeHeaders:
    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def get(self, name: str, default: str = "") -> str:
        return self._m.get(name, default)


class _FakeHandler:
    def __init__(self, client_ip: str, headers: dict | None = None) -> None:
        self.client_address = (client_ip, 0)
        self.headers = _FakeHeaders(headers or {})


class _NoAddrHandler:
    def __init__(self, headers: dict | None = None) -> None:
        self.headers = _FakeHeaders(headers or {})


_DEFAULT_CIDRS = {
    "CONTROLLER_TRUSTED_PROXY_CIDRS":
        "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8",
}


class TrustedProxyClientIpTests(unittest.TestCase):
    """Happy-path: trusted proxy forwards the real client via XFF."""

    def setUp(self) -> None:
        self.auth = _TrustedProxyAuth()

    def test_trusted_proxy_uses_xff_first_untrusted(self):
        """Direct-connect from 10.0.0.5 (a trusted CIDR) must cause
        ``client_ip`` to walk XFF and return the first hop that is NOT
        itself a trusted proxy — that's the real external client."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "203.0.113.7, 10.0.0.5"},
            )
            self.assertEqual(self.auth.client_ip(h), "203.0.113.7")

    def test_untrusted_direct_source_wins(self):
        """A request hitting the controller directly from an untrusted
        IP must ignore XFF — the attacker can forge XFF but not the TCP
        peer address."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler(
                "203.0.113.99",
                {"X-Forwarded-For": "1.2.3.4"},
            )
            self.assertEqual(self.auth.client_ip(h), "203.0.113.99")

    def test_trusted_proxy_no_xff_returns_empty(self):
        """Trusted proxy path but no XFF: strict fallback to ''. We do
        NOT return the proxy IP because banning a proxy hop would lock
        out every client behind it."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler("10.0.0.5", {})
            self.assertEqual(self.auth.client_ip(h), "")

    def test_malformed_xff_returns_empty(self):
        """A garbage token in the XFF chain must NOT silently let us
        pick a different hop — fail closed, the request gets '' and
        the IP-ban paths treat it as a no-op."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "not-an-ip, 10.0.0.5"},
            )
            self.assertEqual(self.auth.client_ip(h), "")

    def test_ipv6_client_through_trusted_proxy(self):
        """IPv6 flows through the same path. Direct from loopback-v6
        (inside the default CIDR list because ::1 maps into the
        ``127.0.0.0/8`` intent? no — add explicit v6 trust)."""
        env = dict(_DEFAULT_CIDRS)
        env["CONTROLLER_TRUSTED_PROXY_CIDRS"] = (
            _DEFAULT_CIDRS["CONTROLLER_TRUSTED_PROXY_CIDRS"] + ",::1/128"
        )
        with mock.patch.dict("os.environ", env, clear=False):
            h = _FakeHandler(
                "::1",
                {"X-Forwarded-For": "2001:db8::1, ::1"},
            )
            self.assertEqual(self.auth.client_ip(h), "2001:db8::1")

    def test_multiple_hops_first_untrusted_wins(self):
        """Three proxy layers: closest proxy is 10.0.0.5, next is
        10.0.0.6, then the real client 198.51.100.42. Walking
        right-to-left we skip both trusted hops and stop at the first
        public IP."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "198.51.100.42, 10.0.0.6, 10.0.0.5"},
            )
            self.assertEqual(self.auth.client_ip(h), "198.51.100.42")

    def test_all_trusted_hops_returns_empty(self):
        """Edge case: every hop in XFF is itself a trusted proxy. The
        real client is not visible — strict fallback to '' rather than
        picking the leftmost proxy."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "10.0.0.7, 10.0.0.6"},
            )
            self.assertEqual(self.auth.client_ip(h), "")

    def test_empty_direct_connect_returns_empty(self):
        """No client_address at all (odd BaseHTTPRequestHandler edge
        case) — resolver returns ''."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _NoAddrHandler({"X-Forwarded-For": "203.0.113.7"})
            self.assertEqual(self.auth.client_ip(h), "")

    def test_env_override_for_trusted_cidrs(self):
        """Operator override: only 192.168.0.0/16 is trusted. A 10.x
        direct-connect is now UN-trusted and its XFF is ignored."""
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "192.168.0.0/16"},
            clear=False,
        ):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "1.2.3.4"},
            )
            self.assertEqual(self.auth.client_ip(h), "10.0.0.5")

    def test_private_client_ip_through_trusted_proxy(self):
        """Narrowed trust set: only 10.0.0.0/8 is trusted. A LAN client
        on 192.168.1.20 that traversed one 10.x proxy is correctly
        returned — the LAN IP is NOT in the trust set and wins."""
        with mock.patch.dict(
            "os.environ",
            {"CONTROLLER_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"},
            clear=False,
        ):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "192.168.1.20, 10.0.0.5"},
            )
            self.assertEqual(self.auth.client_ip(h), "192.168.1.20")

    def test_private_back_compat_delegates_to_public(self):
        """``_client_ip`` is kept as a shim that delegates to the new
        public ``client_ip`` — pre-existing callsites get the ratchet
        behaviour without having to rename."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "203.0.113.7, 10.0.0.5"},
            )
            self.assertEqual(self.auth._client_ip(h), "203.0.113.7")

    def test_default_cidrs_apply_when_env_unset(self):
        """When CONTROLLER_TRUSTED_PROXY_CIDRS is absent, ``client_ip``
        falls back to the built-in RFC1918 + loopback default so
        standard in-cluster deploys Just Work."""
        with mock.patch.dict(
            "os.environ", {"CONTROLLER_TRUSTED_PROXY_CIDRS": ""},
            clear=False,
        ):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "203.0.113.7, 10.0.0.5"},
            )
            self.assertEqual(self.auth.client_ip(h), "203.0.113.7")

    def test_whitespace_xff_ignored(self):
        """Extra whitespace around commas is tolerated (common from
        some proxies that append ' IP' with a space)."""
        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            h = _FakeHandler(
                "10.0.0.5",
                {"X-Forwarded-For": "   203.0.113.10   ,  10.0.0.5"},
            )
            self.assertEqual(self.auth.client_ip(h), "203.0.113.10")

    def test_broken_headers_object_fails_closed(self):
        """A handler without a usable ``headers`` attribute must not
        crash — we return '' (strict fallback) so the caller's audit
        path still runs."""

        class _H:
            client_address = ("10.0.0.5", 0)
            headers = None

        with mock.patch.dict("os.environ", _DEFAULT_CIDRS, clear=False):
            self.assertEqual(self.auth.client_ip(_H()), "")


if __name__ == "__main__":
    unittest.main()
