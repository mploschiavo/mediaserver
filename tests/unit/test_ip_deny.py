"""Unit tests for the IPDeny dataclass + protocol.

Covers CIDR normalisation (bare addr -> /32 or /128; preserve
existing), validation rejection of malformed input, expiry
comparison, immutability, and the runtime-checkable protocol.
"""

from __future__ import annotations

import unittest

from media_stack.core.auth.users.ip_deny import IPDeny, IPDenyProvider


class IPDenyNormalisationTests(unittest.TestCase):

    def test_bare_ipv4_gets_slash32(self) -> None:
        d = IPDeny(cidr="203.0.113.45")
        self.assertEqual(d.cidr, "203.0.113.45/32")

    def test_bare_ipv6_gets_slash128(self) -> None:
        d = IPDeny(cidr="2001:db8::1")
        self.assertEqual(d.cidr, "2001:db8::1/128")

    def test_ipv4_cidr_preserved(self) -> None:
        d = IPDeny(cidr="10.0.0.0/8")
        self.assertEqual(d.cidr, "10.0.0.0/8")

    def test_ipv4_cidr_canonicalised(self) -> None:
        # 10.0.0.5/24 is not strict; with strict=False we get the
        # containing network "10.0.0.0/24".
        d = IPDeny(cidr="10.0.0.5/24")
        self.assertEqual(d.cidr, "10.0.0.0/24")

    def test_ipv6_cidr_preserved(self) -> None:
        d = IPDeny(cidr="2001:db8::/48")
        self.assertEqual(d.cidr, "2001:db8::/48")

    def test_whitespace_stripped(self) -> None:
        d = IPDeny(cidr="  203.0.113.45/32  ")
        self.assertEqual(d.cidr, "203.0.113.45/32")


class IPDenyValidationTests(unittest.TestCase):

    def test_empty_string_rejected(self) -> None:
        with self.assertRaises(ValueError):
            IPDeny(cidr="")

    def test_not_an_ip_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            IPDeny(cidr="not-an-ip")
        self.assertIn("invalid cidr", str(ctx.exception))

    def test_bad_prefix_rejected(self) -> None:
        with self.assertRaises(ValueError):
            IPDeny(cidr="203.0.113.45/99")

    def test_ipv4_in_ipv6_style_rejected(self) -> None:
        # Not a real IP form — regression on silent acceptance.
        with self.assertRaises(ValueError):
            IPDeny(cidr="203.0.113.x")


class IPDenyExpiryTests(unittest.TestCase):

    def test_no_expires_never_expired(self) -> None:
        d = IPDeny(cidr="10.0.0.0/8")
        self.assertFalse(d.is_expired("9999-12-31T00:00:00Z"))

    def test_expired_when_now_past(self) -> None:
        d = IPDeny(cidr="10.0.0.0/8", expires_at="2020-01-01T00:00:00Z")
        self.assertTrue(d.is_expired("2026-04-24T00:00:00Z"))

    def test_not_expired_when_future(self) -> None:
        d = IPDeny(cidr="10.0.0.0/8", expires_at="2030-01-01T00:00:00Z")
        self.assertFalse(d.is_expired("2026-04-24T00:00:00Z"))

    def test_boundary_equal_is_expired(self) -> None:
        # Matching timestamps are treated as expired (the instant
        # of expiry is over). Lexical comparison gives us this for
        # free because '<=' resolves equals to True.
        t = "2026-04-24T10:00:00Z"
        d = IPDeny(cidr="10.0.0.0/8", expires_at=t)
        self.assertTrue(d.is_expired(t))


class IPDenyShapeTests(unittest.TestCase):

    def test_to_dict(self) -> None:
        d = IPDeny(
            cidr="198.51.100.0/24",
            reason="credential_stuffing",
            actor="admin",
            banned_at="2026-04-24T00:00:00Z",
            expires_at="2026-05-24T00:00:00Z",
        )
        self.assertEqual(d.to_dict(), {
            "cidr": "198.51.100.0/24",
            "reason": "credential_stuffing",
            "actor": "admin",
            "banned_at": "2026-04-24T00:00:00Z",
            "expires_at": "2026-05-24T00:00:00Z",
        })

    def test_frozen(self) -> None:
        d = IPDeny(cidr="10.0.0.0/8")
        with self.assertRaises(Exception):
            d.reason = "nope"  # type: ignore[misc]


class IPDenyProviderProtocolTests(unittest.TestCase):

    def test_impl_is_recognised(self) -> None:
        class _Impl:
            name = "stub"

            def list_ip_denies(self) -> list:
                return []

            def add_ip_deny(self, rule: IPDeny) -> None:
                pass

            def remove_ip_deny(self, cidr: str) -> None:
                pass

        self.assertIsInstance(_Impl(), IPDenyProvider)

    def test_missing_method_not_recognised(self) -> None:
        class _Partial:
            name = "partial"

            def list_ip_denies(self) -> list:
                return []

            # missing add_ip_deny + remove_ip_deny

        self.assertNotIsInstance(_Partial(), IPDenyProvider)


if __name__ == "__main__":
    unittest.main()
