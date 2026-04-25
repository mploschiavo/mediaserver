"""Unit tests for :class:`WebhookChannel`."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.notifications.dispatcher import (
    DeliveryStatus,
    Notification,
)
from media_stack.core.notifications.webhook_channel import WebhookChannel


class _FakeHttpClient:
    """Records calls and returns a scripted response tuple."""

    def __init__(self, status: int = 200, body: str = "", raises: Exception | None = None):
        self._status = status
        self._body = body
        self._raises = raises
        self.calls: list[dict] = []

    def request(self, base_url, path, *, method="GET", payload=None, timeout=20):
        self.calls.append(
            {
                "base_url": base_url,
                "path": path,
                "method": method,
                "payload": payload,
                "timeout": timeout,
            }
        )
        if self._raises is not None:
            raise self._raises
        return self._status, None, self._body


def _note(event_type: str = "auth.ban", **overrides) -> Notification:
    base = dict(
        event_type=event_type,
        title="IP banned",
        body="ip=9.9.9.9",
        severity="critical",
        structured={"ip": "9.9.9.9", "reason": "brute_force"},
        dedupe_key="",
    )
    base.update(overrides)
    return Notification(**base)


class WebhookChannelSendTests(unittest.TestCase):
    def test_2xx_yields_ok_result(self):
        http = _FakeHttpClient(status=200, body="ok")
        ch = WebhookChannel(
            "hook", "https://example.test/hooks/x", http_client=http,
            allow_internal=True,
        )
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.OK)
        self.assertEqual(result.channel_name, "hook")
        self.assertIn("200", result.detail)

    def test_non_2xx_yields_retryable_with_status(self):
        http = _FakeHttpClient(status=503, body="service unavailable")
        ch = WebhookChannel("hook", "https://example.test/x", http_client=http, allow_internal=True)
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.RETRYABLE)
        self.assertIn("503", result.detail)
        self.assertIn("service unavailable", result.detail)

    def test_4xx_is_retryable(self):
        http = _FakeHttpClient(status=429, body="slow down")
        ch = WebhookChannel("hook", "https://example.test/x", http_client=http, allow_internal=True)
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.RETRYABLE)
        self.assertIn("429", result.detail)

    def test_transport_exception_yields_retryable(self):
        http = _FakeHttpClient(raises=RuntimeError("DNS failure"))
        ch = WebhookChannel("hook", "https://example.test/x", http_client=http, allow_internal=True)
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.RETRYABLE)
        self.assertIn("DNS failure", result.detail)

    def test_payload_shape(self):
        http = _FakeHttpClient(status=200, body="")
        ch = WebhookChannel(
            "hook",
            "https://example.test/hooks/x?token=abc",
            http_client=http,
            allow_internal=True,
        )
        n = _note()
        ch.send(n)
        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["base_url"], "https://example.test")
        self.assertEqual(call["path"], "/hooks/x?token=abc")
        payload = call["payload"]
        self.assertEqual(payload["event_type"], n.event_type)
        self.assertEqual(payload["title"], n.title)
        self.assertEqual(payload["body"], n.body)
        self.assertEqual(payload["severity"], n.severity)
        self.assertEqual(payload["structured"], n.structured)
        self.assertIn("ts", payload)
        # ts must be JSON-serialisable and non-empty
        self.assertTrue(payload["ts"])
        # Whole payload must JSON-round-trip
        json.dumps(payload)

    def test_payload_copies_structured_dict(self):
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel("hook", "https://example.test/x", http_client=http, allow_internal=True)
        original = {"user": "alice"}
        n = Notification(
            event_type="auth.ban",
            title="t", body="b", severity="info",
            structured=original,
        )
        ch.send(n)
        # The payload's structured is a distinct dict from the input.
        sent_struct = http.calls[0]["payload"]["structured"]
        self.assertEqual(sent_struct, original)
        self.assertIsNot(sent_struct, original)

    def test_timeout_forwarded(self):
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel(
            "hook", "https://example.test/x",
            http_client=http, timeout_seconds=7.4,
            allow_internal=True,
        )
        ch.send(_note())
        # timeout is coerced to int (HttpClient signature)
        self.assertEqual(http.calls[0]["timeout"], 7)

    def test_zero_timeout_clamps_to_one(self):
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel(
            "hook", "https://example.test/x",
            http_client=http, timeout_seconds=0,
            allow_internal=True,
        )
        ch.send(_note())
        self.assertEqual(http.calls[0]["timeout"], 1)

    def test_url_without_path_defaults_to_slash(self):
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel("hook", "https://example.test", http_client=http, allow_internal=True)
        ch.send(_note())
        self.assertEqual(http.calls[0]["base_url"], "https://example.test")
        self.assertEqual(http.calls[0]["path"], "/")


class WebhookChannelAcceptsTests(unittest.TestCase):
    def test_accepts_everything_when_filter_is_none(self):
        ch = WebhookChannel(
            "hook", "https://example.test/x", http_client=_FakeHttpClient(),
            allow_internal=True,
        )
        self.assertTrue(ch.accepts("auth.ban"))
        self.assertTrue(ch.accepts("anything.else"))

    def test_accepts_filters_by_event_types(self):
        ch = WebhookChannel(
            "hook", "https://example.test/x",
            event_types=frozenset({"auth.ban", "auth.new_location"}),
            http_client=_FakeHttpClient(),
            allow_internal=True,
        )
        self.assertTrue(ch.accepts("auth.ban"))
        self.assertTrue(ch.accepts("auth.new_location"))
        self.assertFalse(ch.accepts("auth.password_change"))


class WebhookChannelDefaultClientTests(unittest.TestCase):
    def test_default_http_client_is_built_when_not_injected(self):
        # No http_client param → constructor reaches into ``core.http``.
        # We just assert the attribute is set to something request-callable.
        ch = WebhookChannel("hook", "https://example.test/x", allow_internal=True)
        self.assertTrue(hasattr(ch._http_client, "request"))


class WebhookChannelSsrfGuardTests(unittest.TestCase):
    """The SSRF guard rejects URLs that would let an attacker pivot
    the controller into internal namespaces."""

    def _dropped(self, url: str, reason_substr: str) -> None:
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel("hook", url, http_client=http)
        result = ch.send(_note())
        self.assertEqual(
            result.status, DeliveryStatus.DROPPED,
            f"url {url!r} should have been dropped",
        )
        self.assertIn(
            reason_substr, result.detail,
            f"expected reason to mention {reason_substr!r}, got {result.detail!r}",
        )
        # The underlying HTTP client is NEVER called for a dropped URL.
        self.assertEqual(http.calls, [])

    def test_blocks_loopback_ipv4(self) -> None:
        self._dropped("http://127.0.0.1/hook", "loopback")

    def test_blocks_loopback_ipv6(self) -> None:
        self._dropped("http://[::1]/hook", "loopback")

    def test_blocks_localhost_name(self) -> None:
        self._dropped("http://localhost/hook", "loopback")

    def test_blocks_rfc1918_10_dot(self) -> None:
        self._dropped("http://10.1.2.3/hook", "private")

    def test_blocks_rfc1918_172_dot(self) -> None:
        self._dropped("http://172.16.0.1/hook", "private")

    def test_blocks_rfc1918_192_dot(self) -> None:
        self._dropped("http://192.168.0.1/hook", "private")

    def test_blocks_link_local_ipv4(self) -> None:
        # 169.254/16 is AWS IMDS / GCP metadata.
        self._dropped("http://169.254.169.254/latest/meta-data", "link-local")

    def test_blocks_link_local_ipv6(self) -> None:
        self._dropped("http://[fe80::1]/hook", "link-local")

    def test_blocks_k8s_svc_dns(self) -> None:
        self._dropped("http://auth.media-stack.svc/hook", "kubernetes")

    def test_blocks_k8s_svc_cluster_local(self) -> None:
        self._dropped(
            "http://auth.media-stack.svc.cluster.local/hook",
            "kubernetes",
        )

    def test_blocks_non_http_scheme(self) -> None:
        self._dropped("file:///etc/passwd", "scheme")

    def test_blocks_ftp_scheme(self) -> None:
        self._dropped("ftp://example.org/hook", "scheme")

    def test_allow_internal_bypasses_guard(self) -> None:
        # A deliberate internal webhook CAN be registered — it's the
        # admin's job to know this is safe.
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel(
            "hook", "http://127.0.0.1:9200/events",
            http_client=http, allow_internal=True,
        )
        result = ch.send(_note())
        self.assertEqual(result.status, DeliveryStatus.OK)
        self.assertEqual(len(http.calls), 1)

    def test_empty_url_blocked(self) -> None:
        self._dropped("", "missing")

    def test_url_without_host_blocked(self) -> None:
        self._dropped("http:///hook", "missing")


class WebhookChannelDnsResolutionGuardTests(unittest.TestCase):
    """DNS-rebinding + name-based SSRF coverage.

    The guard resolves the hostname via ``socket.getaddrinfo`` and
    classifies every returned address, so a hostname that points at
    ``127.0.0.1`` on second lookup is still blocked on the first.

    These tests mock ``socket.getaddrinfo`` because we can't rely on
    real DNS in CI.
    """

    def _run(self, url: str, getaddrinfo_stub):
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel("hook", url, http_client=http)
        with patch(
            "media_stack.core.notifications.webhook_channel.socket.getaddrinfo",
            side_effect=getaddrinfo_stub,
        ):
            result = ch.send(_note())
        return result, http

    def test_unresolvable_hostname_dropped(self) -> None:
        import socket

        def _raise(*args, **kwargs):
            raise socket.gaierror(8, "nodename nor servname provided")

        result, http = self._run(
            "https://nonexistent.invalid/hook", _raise,
        )
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("does not resolve", result.detail)
        self.assertEqual(http.calls, [])

    def test_name_resolving_to_loopback_blocked(self) -> None:
        # Classic DNS rebinding — domain answers with 127.0.0.1.
        def _ok_loopback(*args, **kwargs):
            return [(2, 1, 6, "", ("127.0.0.1", 0))]

        result, http = self._run("https://evil.example/hook", _ok_loopback)
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("loopback", result.detail)
        self.assertEqual(http.calls, [])

    def test_name_resolving_to_rfc1918_blocked(self) -> None:
        def _ok_private(*args, **kwargs):
            return [(2, 1, 6, "", ("10.0.0.5", 0))]

        result, http = self._run(
            "https://internal.example/hook", _ok_private,
        )
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("private", result.detail)

    def test_name_resolving_to_link_local_ipv6_blocked(self) -> None:
        def _ok_ll(*args, **kwargs):
            # AF_INET6 tuple shape; only first-element IP is read.
            return [(10, 1, 6, "", ("fe80::1", 0, 0, 0))]

        result, _ = self._run(
            "https://ll.example/hook", _ok_ll,
        )
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("link-local", result.detail)

    def test_name_resolving_to_public_ip_allowed(self) -> None:
        def _ok_public(*args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 0))]

        result, http = self._run(
            "https://cdn.example.com/hook", _ok_public,
        )
        # Guard cleared; HTTP client was called.
        self.assertEqual(result.status, DeliveryStatus.OK)
        self.assertEqual(len(http.calls), 1)

    def test_malformed_resolved_address_skipped(self) -> None:
        # Defensive: getaddrinfo returns a junk address. The guard
        # loops past unparseable entries and relies on later real IPs.
        def _ok_junk_then_public(*args, **kwargs):
            return [
                (2, 1, 6, "", ("not-an-ip", 0)),
                (2, 1, 6, "", ("8.8.4.4", 0)),
            ]

        result, http = self._run(
            "https://cdn.example.com/hook", _ok_junk_then_public,
        )
        self.assertEqual(result.status, DeliveryStatus.OK)
        self.assertEqual(len(http.calls), 1)

    def test_multi_address_dns_any_internal_blocks(self) -> None:
        # Mixed resolution: first answer public, second private. Guard
        # checks EVERY returned address — if ANY is internal, drop.
        def _mixed(*args, **kwargs):
            return [
                (2, 1, 6, "", ("8.8.4.4", 0)),
                (2, 1, 6, "", ("192.168.1.5", 0)),
            ]

        result, http = self._run(
            "https://dual.example/hook", _mixed,
        )
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("private", result.detail)
        self.assertEqual(http.calls, [])


class WebhookChannelClassifyIpEdgeTests(unittest.TestCase):
    """Coverage for the IP classifier's defensive branches:
    reserved, multicast, unspecified ranges all get bucketed as
    'private' so operators see a single friendly reason.
    """

    def _run(self, url: str):
        http = _FakeHttpClient(status=200)
        ch = WebhookChannel("hook", url, http_client=http)
        return ch.send(_note()), http

    def test_reserved_ipv4_blocked(self) -> None:
        # 240.0.0.1 — Class E reserved.
        result, http = self._run("https://240.0.0.1/hook")
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("private", result.detail)
        self.assertEqual(http.calls, [])

    def test_multicast_ipv4_blocked(self) -> None:
        # 224.0.0.1 — multicast.
        result, http = self._run("https://224.0.0.1/hook")
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("private", result.detail)

    def test_unspecified_ipv4_blocked(self) -> None:
        # 0.0.0.0 — unspecified.
        result, http = self._run("https://0.0.0.0/hook")
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("private", result.detail)

    def test_multicast_ipv6_blocked(self) -> None:
        result, http = self._run("https://[ff02::1]/hook")
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("private", result.detail)

    def test_unspecified_ipv6_blocked(self) -> None:
        result, http = self._run("https://[::]/hook")
        self.assertEqual(result.status, DeliveryStatus.DROPPED)
        self.assertIn("private", result.detail)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
