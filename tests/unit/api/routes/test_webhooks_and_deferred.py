"""Tests for ``api/routes/webhooks_and_deferred.py``
(ADR-0007 Phase 2 wave 6 — final cleanup).

One test class per route + per collaborator + a routing-integration
sanity test that pins auto-discovery for every wave-6 path through
the production ``DefaultDispatcher``.

Mocking strategy:

* Constructor injection lets each test build a route module with
  a hand-crafted collaborator (``MagicMock`` or a stub class),
  exercising real route methods against a deterministic surface.
* ``_PostHandler`` extends ``MockControllerHandler`` to add the
  POST surface (``_read_json_body``, an attached
  ``state.webhook_urls`` list, and a captured ``update_config``
  signal). HMAC tests subclass it again to attach an
  ``rfile``-backed body so the verifier reads the bytes the test
  wrote.
* CSRF, rate-limiting, and audit-log writes are enforced upstream
  by ``server.py`` wrappers; we don't re-test them here. We DO
  pin that the URL validator + HMAC verifier are invoked on every
  mutating webhook route so a future refactor doesn't accidentally
  drop them.

The SSE route is asserted at the "Router-entered-the-SSE-branch"
level only — the helper it delegates to (``handlers_get._handle_logs_sse``)
is covered by the legacy handler's own tests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from media_stack.api.routes.webhooks_and_deferred import (
    AggregateLogStreamer,
    BazarrSubtitleConfigService,
    JellyfinScanTrigger,
    WebhookHmacVerifier,
    WebhookTestPinger,
    WebhookUrlValidator,
    WebhooksAndDeferredRoutes,
)
from media_stack.api.routing import DispatchOutcome
from tests.unit.api.routes._helpers import (
    MockControllerHandler,
    RouteDispatchHarness,
)


class _PostHandler(MockControllerHandler):
    """``MockControllerHandler`` with the POST surface the route
    methods read: ``_read_json_body`` returning a constructor-set
    dict, plus an attribute-based ``state`` surface with a real
    ``webhook_urls`` list and a captured ``update_config`` call.
    """

    def __init__(
        self,
        *,
        path: str = "/",
        body_json: Any = None,
        webhook_urls: list[str] | None = None,
        headers: dict[str, str] | None = None,
        body_bytes: bytes = b"",
    ) -> None:
        super().__init__(path=path, headers=headers, body=body_bytes)
        self._body_json = body_json
        # Re-shape ``state.webhook_urls`` so tests can assert against
        # the post-write list. ``_MockState`` already provides an
        # empty list, so this just lets tests pre-seed.
        self.state.webhook_urls = (
            list(webhook_urls) if webhook_urls is not None else []
        )
        self.state.update_config = MagicMock(
            side_effect=lambda updates: dict(updates),
        )

    def _read_json_body(self) -> Any:
        return {} if self._body_json is None else self._body_json


# ---------------------------------------------------------------------------
# WebhookUrlValidator (Strategy)
# ---------------------------------------------------------------------------


class TestWebhookUrlValidator:
    """SSRF allow-list — the load-bearing piece of the webhook
    register flow. Every blocked range MUST stay rejected; the
    legacy behaviour was lifted bodily so this test pins parity."""

    def test_accepts_public_https_url(self) -> None:
        # 93.184.216.34 == example.com (public, non-reserved).
        def fake_resolver(_host, _port):
            return [(0, 0, 0, "", ("93.184.216.34", 0))]
        validator = WebhookUrlValidator(resolver=fake_resolver)
        assert validator.validate("https://example.com/hook") is None

    def test_rejects_loopback(self) -> None:
        def fake_resolver(_host, _port):
            return [(0, 0, 0, "", ("127.0.0.1", 0))]
        validator = WebhookUrlValidator(resolver=fake_resolver)
        err = validator.validate("https://localhost/hook")
        assert err is not None
        assert "blocked address" in err

    def test_rejects_private_rfc1918(self) -> None:
        def fake_resolver(_host, _port):
            return [(0, 0, 0, "", ("10.0.0.1", 0))]
        validator = WebhookUrlValidator(resolver=fake_resolver)
        err = validator.validate("https://internal.example/hook")
        assert err is not None
        assert "blocked address" in err

    def test_rejects_link_local(self) -> None:
        # 169.254.169.254 == AWS / GCE metadata service. CRITICAL
        # SSRF target — must stay rejected.
        def fake_resolver(_host, _port):
            return [(0, 0, 0, "", ("169.254.169.254", 0))]
        validator = WebhookUrlValidator(resolver=fake_resolver)
        err = validator.validate("https://metadata.example/hook")
        assert err is not None

    def test_rejects_non_http_scheme(self) -> None:
        validator = WebhookUrlValidator(resolver=lambda *_: [])
        err = validator.validate("ftp://example.com/hook")
        assert err is not None
        assert "http://" in err

    def test_rejects_unresolvable_hostname(self) -> None:
        import socket

        def fake_resolver(_host, _port):
            raise socket.gaierror("DNS fail")
        validator = WebhookUrlValidator(resolver=fake_resolver)
        err = validator.validate("https://nope.invalid/hook")
        assert err is not None
        assert "does not resolve" in err

    def test_rejects_dns_rebinding_via_any_address(self) -> None:
        """If the hostname resolves to BOTH a public AND a private
        address, the validator must reject — DNS rebinding."""
        def fake_resolver(_host, _port):
            return [
                (0, 0, 0, "", ("8.8.8.8", 0)),
                (0, 0, 0, "", ("10.0.0.1", 0)),
            ]
        validator = WebhookUrlValidator(resolver=fake_resolver)
        err = validator.validate("https://rebind.example/hook")
        assert err is not None


# ---------------------------------------------------------------------------
# WebhookHmacVerifier (Strategy)
# ---------------------------------------------------------------------------


class _HmacHandler(MockControllerHandler):
    """Handler with a hand-crafted body + Content-Length header.

    The HMAC verifier reads bytes off ``rfile`` itself rather than
    going through ``_read_json_body``; this stub mirrors the
    production socket reader exactly.
    """

    def __init__(self, body: bytes, signature: str | None) -> None:
        headers = {"Content-Length": str(len(body))}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        super().__init__(headers=headers, body=body)

    def _read_json_body(self) -> dict:
        # Mirror the production fallback path — when no HMAC secret
        # is set, the verifier delegates to this method instead of
        # reading rfile.
        if not self.rfile.getvalue():
            return {}
        try:
            return json.loads(self.rfile.getvalue())
        except (ValueError, TypeError):
            return {}


class TestWebhookHmacVerifier:
    """HMAC pass-through, missing-header reject, signature compare."""

    def test_pass_through_when_secret_unset(self) -> None:
        verifier = WebhookHmacVerifier(env=lambda *_: "")
        body_bytes = json.dumps({"eventType": "Download"}).encode("utf-8")
        handler = _HmacHandler(body=body_bytes, signature=None)
        body, ok = verifier.verify_and_parse(handler)
        assert ok is True
        assert body == {"eventType": "Download"}

    def test_rejects_when_header_missing_and_secret_set(self) -> None:
        verifier = WebhookHmacVerifier(env=lambda k, d="": "secret")
        handler = _HmacHandler(
            body=json.dumps({"eventType": "Download"}).encode(),
            signature=None,
        )
        body, ok = verifier.verify_and_parse(handler)
        assert ok is False
        assert body == {}

    def test_accepts_correct_signature(self) -> None:
        import hashlib
        import hmac as _hmac
        secret = "topsecret"
        body_bytes = json.dumps({"eventType": "Download"}).encode("utf-8")
        sig_hex = _hmac.new(
            secret.encode(), body_bytes, hashlib.sha256,
        ).hexdigest()
        verifier = WebhookHmacVerifier(env=lambda k, d="": secret)
        handler = _HmacHandler(
            body=body_bytes, signature=f"sha256={sig_hex}",
        )
        body, ok = verifier.verify_and_parse(handler)
        assert ok is True
        assert body == {"eventType": "Download"}

    def test_rejects_wrong_signature(self) -> None:
        verifier = WebhookHmacVerifier(env=lambda k, d="": "topsecret")
        body_bytes = json.dumps({"eventType": "Download"}).encode("utf-8")
        handler = _HmacHandler(
            body=body_bytes,
            signature="sha256=" + ("0" * 64),
        )
        body, ok = verifier.verify_and_parse(handler)
        assert ok is False
        assert body == {}

    def test_rejects_non_sha256_prefix(self) -> None:
        verifier = WebhookHmacVerifier(env=lambda k, d="": "topsecret")
        handler = _HmacHandler(body=b"{}", signature="md5=deadbeef")
        body, ok = verifier.verify_and_parse(handler)
        assert ok is False


# ---------------------------------------------------------------------------
# Bazarr subtitle-config (Adapter)
# ---------------------------------------------------------------------------


class TestBazarrSubtitleConfigService:
    """Adapter — wraps ``bazarr_proxy.get_subtitle_config``."""

    def test_fetch_delegates_to_loader(self) -> None:
        sentinel = {"profiles": [{"id": 1, "name": "English"}]}
        service = BazarrSubtitleConfigService(loader=lambda: sentinel)
        assert service.fetch() is sentinel

    def test_default_loader_resolves_lazily(self) -> None:
        """The default loader pulls in
        ``media_stack.api.services.bazarr_proxy.get_subtitle_config``
        only when ``fetch`` is called — NOT at construction time. We
        prove the deferral by injecting a stand-in module via
        ``sys.modules`` so the lazy import lands on the stub.
        """
        import sys
        import types

        stub_module = types.ModuleType("media_stack.api.services.bazarr_proxy")
        stub_module.get_subtitle_config = lambda: {"profiles": ["lazy"]}
        prior = sys.modules.get("media_stack.api.services.bazarr_proxy")
        sys.modules["media_stack.api.services.bazarr_proxy"] = stub_module
        try:
            service = BazarrSubtitleConfigService()
            assert service.fetch() == {"profiles": ["lazy"]}
        finally:
            if prior is not None:
                sys.modules["media_stack.api.services.bazarr_proxy"] = prior
            else:
                del sys.modules["media_stack.api.services.bazarr_proxy"]


# ---------------------------------------------------------------------------
# /api/webhooks GET — list webhook URLs
# ---------------------------------------------------------------------------


class TestWebhooksListRoute:

    def test_returns_seeded_webhook_urls(self) -> None:
        routes = WebhooksAndDeferredRoutes()
        handler = _PostHandler(
            path="/api/webhooks",
            webhook_urls=["https://hooks.example/A"],
        )
        routes.handle_webhooks_list(handler)
        assert handler.captured.status == 200
        assert json.loads(handler.captured.body) == {
            "webhook_urls": ["https://hooks.example/A"],
        }


# ---------------------------------------------------------------------------
# /api/webhooks POST + /webhooks POST — register a URL
# ---------------------------------------------------------------------------


class TestWebhooksRegisterRoutes:
    """Both ``/api/webhooks`` and ``/webhooks`` POST share
    ``_do_register``; we test the alias once and the canonical once
    to pin both decorators against the shared body."""

    def test_register_validates_and_persists(self) -> None:
        validator = MagicMock()
        validator.validate.return_value = None  # accept
        routes = WebhooksAndDeferredRoutes(url_validator=validator)
        handler = _PostHandler(
            body_json={"url": "https://hooks.example/new"},
        )
        routes.handle_webhooks_register_api(handler)
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert "https://hooks.example/new" in body["webhook_urls"]
        validator.validate.assert_called_once_with(
            "https://hooks.example/new",
        )
        handler.state.update_config.assert_called_once()
        persisted = handler.state.update_config.call_args.args[0]
        assert "_webhook_urls" in persisted
        assert "https://hooks.example/new" in persisted["_webhook_urls"]

    def test_register_rejects_blocked_url(self) -> None:
        validator = MagicMock()
        validator.validate.return_value = "blocked address"
        routes = WebhooksAndDeferredRoutes(url_validator=validator)
        handler = _PostHandler(
            body_json={"url": "https://internal.example/x"},
        )
        routes.handle_webhooks_register_api(handler)
        assert handler.captured.status == 400
        body = json.loads(handler.captured.body)
        assert body == {"error": "blocked address"}
        # Persistence MUST NOT happen on rejected URLs.
        handler.state.update_config.assert_not_called()
        # And the in-memory list MUST stay empty.
        assert handler.state.webhook_urls == []

    def test_register_with_empty_url_returns_current_list_unchanged(
        self,
    ) -> None:
        """Legacy quirk preserved: an empty ``url`` body is treated
        as a "list me" call. The registration step is skipped, no
        validator is invoked, and the current list is echoed back."""
        validator = MagicMock()
        routes = WebhooksAndDeferredRoutes(url_validator=validator)
        handler = _PostHandler(
            body_json={"url": ""},
            webhook_urls=["https://existing.example/A"],
        )
        routes.handle_webhooks_register(handler)
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {
            "webhook_urls": ["https://existing.example/A"],
        }
        validator.validate.assert_not_called()
        handler.state.update_config.assert_not_called()

    def test_register_dedupes_existing_url(self) -> None:
        """Re-registering an already-known URL must not double-add
        it. The legacy behaviour used set semantics; we preserve
        the same observable shape via a membership check."""
        validator = MagicMock()
        validator.validate.return_value = None
        routes = WebhooksAndDeferredRoutes(url_validator=validator)
        handler = _PostHandler(
            body_json={"url": "https://hooks.example/A"},
            webhook_urls=["https://hooks.example/A"],
        )
        routes.handle_webhooks_register(handler)
        body = json.loads(handler.captured.body)
        assert body["webhook_urls"] == ["https://hooks.example/A"]
        # update_config still fires because we re-persist the list
        # (matches the legacy chain's "persist whatever the in-memory
        # set looks like after a successful register" shape).
        handler.state.update_config.assert_called_once()


# ---------------------------------------------------------------------------
# /webhooks/test POST — fan-out test ping
# ---------------------------------------------------------------------------


class TestWebhookTestPinger:
    """Strategy — fan out a deterministic test payload to every
    registered URL."""

    def test_no_webhooks_returns_no_webhooks(self) -> None:
        pinger = WebhookTestPinger(opener=MagicMock(), request_factory=MagicMock())
        assert pinger.ping_all([]) == {"status": "no_webhooks", "tested": 0}

    def test_pings_each_url_with_test_payload(self) -> None:
        opener_mock = MagicMock()
        # ``urlopen`` returns a context manager whose value has a
        # ``.status`` attribute.
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock(status=200))
        cm.__exit__ = MagicMock(return_value=False)
        opener_mock.return_value = cm
        request_factory = MagicMock()
        pinger = WebhookTestPinger(
            opener=opener_mock,
            request_factory=request_factory,
        )
        result = pinger.ping_all([
            "https://a.example/hook",
            "https://b.example/hook",
        ])
        assert result["status"] == "tested"
        assert result["tested"] == 2
        assert result["results"]["https://a.example/hook"] == "ok (200)"
        assert opener_mock.call_count == 2

    def test_records_per_url_error_on_network_failure(self) -> None:
        import urllib.error
        opener_mock = MagicMock(
            side_effect=urllib.error.URLError("connection refused"),
        )
        pinger = WebhookTestPinger(
            opener=opener_mock,
            request_factory=MagicMock(),
        )
        result = pinger.ping_all(["https://broken.example/hook"])
        assert result["status"] == "tested"
        assert result["tested"] == 1
        assert "error" in result["results"]["https://broken.example/hook"]


class TestWebhookTestRoutes:
    """``/webhooks/test`` and ``/api/webhooks/test`` both hit
    ``_do_test``."""

    def test_test_route_dispatches_to_pinger(self) -> None:
        pinger = MagicMock()
        pinger.ping_all.return_value = {
            "status": "tested",
            "results": {"https://a.example/hook": "ok (200)"},
            "tested": 1,
        }
        routes = WebhooksAndDeferredRoutes(test_pinger=pinger)
        handler = _PostHandler(
            webhook_urls=["https://a.example/hook"],
        )
        routes.handle_webhooks_test(handler)
        assert handler.captured.status == 200
        assert json.loads(handler.captured.body)["tested"] == 1
        pinger.ping_all.assert_called_once_with(["https://a.example/hook"])

    def test_api_alias_dispatches_to_pinger(self) -> None:
        pinger = MagicMock()
        pinger.ping_all.return_value = {
            "status": "no_webhooks", "tested": 0,
        }
        routes = WebhooksAndDeferredRoutes(test_pinger=pinger)
        handler = _PostHandler()
        routes.handle_webhooks_test_api(handler)
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {"status": "no_webhooks", "tested": 0}


# ---------------------------------------------------------------------------
# /webhooks/arr POST — HMAC + scan trigger
# ---------------------------------------------------------------------------


class _ArrHandler(_PostHandler):
    """``_PostHandler`` shape with HMAC-aware ``rfile``+``Content-Length``.

    Exposes ``_read_json_body`` as a thin wrapper around the
    constructor-supplied dict (matching the production
    fallback when ``WEBHOOK_HMAC_SECRET`` is unset).
    """


class TestWebhooksArrRoute:

    def test_rejects_invalid_signature(self) -> None:
        verifier = MagicMock()
        verifier.verify_and_parse.return_value = ({}, False)
        routes = WebhooksAndDeferredRoutes(
            hmac_verifier=verifier,
            scan_trigger=MagicMock(),
        )
        handler = _ArrHandler(body_json={})
        routes.handle_webhooks_arr(handler)
        assert handler.captured.status == 403
        body = json.loads(handler.captured.body)
        assert "signature" in body["error"].lower()

    def test_triggers_scan_on_known_event(self) -> None:
        verifier = MagicMock()
        verifier.verify_and_parse.return_value = (
            {
                "eventType": "Download",
                "movie": {"title": "Dune"},
            },
            True,
        )
        scan_trigger = MagicMock()
        routes = WebhooksAndDeferredRoutes(
            hmac_verifier=verifier,
            scan_trigger=scan_trigger,
        )
        handler = _ArrHandler(body_json={})
        routes.handle_webhooks_arr(handler)
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body == {"status": "ok", "event": "Download"}
        scan_trigger.trigger.assert_called_once_with("Download")

    def test_skips_scan_on_unknown_event(self) -> None:
        verifier = MagicMock()
        verifier.verify_and_parse.return_value = (
            {"eventType": "Test"},
            True,
        )
        scan_trigger = MagicMock()
        routes = WebhooksAndDeferredRoutes(
            hmac_verifier=verifier,
            scan_trigger=scan_trigger,
        )
        handler = _ArrHandler(body_json={})
        routes.handle_webhooks_arr(handler)
        assert handler.captured.status == 200
        scan_trigger.trigger.assert_not_called()

    @pytest.mark.parametrize(
        "body,expected_title",
        [
            ({"movie": {"title": "Dune"}}, "Dune"),
            ({"series": {"title": "Severance"}}, "Severance"),
            (
                {"episodes": [{"title": "Pilot"}, {"title": "Two"}]},
                "Pilot",
            ),
            ({}, ""),
            ({"movie": {}, "series": None}, ""),
        ],
    )
    def test_extract_arr_title_payload_shapes(
        self, body, expected_title,
    ) -> None:
        routes = WebhooksAndDeferredRoutes()
        assert routes._extract_arr_title(body) == expected_title


class TestJellyfinScanTrigger:
    """The trigger is best-effort: a missing API key, missing
    service entry, or network failure must NOT raise."""

    def test_no_op_when_api_key_missing(self) -> None:
        opener = MagicMock()
        trigger = JellyfinScanTrigger(
            api_keys_loader=lambda: {},
            service_map_loader=lambda: {"jellyfin": MagicMock()},
            opener=opener,
            log=lambda _msg: None,
        )
        trigger.trigger("Download")
        opener.assert_not_called()

    def test_no_op_when_service_missing(self) -> None:
        opener = MagicMock()
        trigger = JellyfinScanTrigger(
            api_keys_loader=lambda: {"jellyfin": "abc"},
            service_map_loader=lambda: {},
            opener=opener,
            log=lambda _msg: None,
        )
        trigger.trigger("Download")
        opener.assert_not_called()

    def test_calls_opener_with_emby_token_header(self) -> None:
        ms = MagicMock(host="jf.local", port=8096)
        request_factory = MagicMock()
        opener = MagicMock()
        log_calls: list[str] = []
        trigger = JellyfinScanTrigger(
            api_keys_loader=lambda: {"jellyfin": "secret"},
            service_map_loader=lambda: {"jellyfin": ms},
            opener=opener,
            request_factory=request_factory,
            log=log_calls.append,
        )
        trigger.trigger("Download")
        request_factory.assert_called_once_with(
            "http://jf.local:8096/Library/Refresh",
            method="POST",
            headers={"X-Emby-Token": "secret"},
        )
        opener.assert_called_once()
        assert any("Jellyfin scan triggered" in m for m in log_calls)

    def test_swallows_network_error(self) -> None:
        import urllib.error
        ms = MagicMock(host="jf.local", port=8096)
        opener = MagicMock(
            side_effect=urllib.error.URLError("conn refused"),
        )
        log_calls: list[str] = []
        trigger = JellyfinScanTrigger(
            api_keys_loader=lambda: {"jellyfin": "secret"},
            service_map_loader=lambda: {"jellyfin": ms},
            opener=opener,
            log=log_calls.append,
        )
        # No raise — just a WARN log.
        trigger.trigger("Download")
        assert any("failed" in m for m in log_calls)


# ---------------------------------------------------------------------------
# /api/bazarr/subtitle-config GET
# ---------------------------------------------------------------------------


class TestBazarrSubtitleConfigRoute:

    def test_returns_proxy_payload_on_success(self) -> None:
        bazarr = MagicMock()
        bazarr.fetch.return_value = {
            "available_languages": [{"code": "en", "name": "English"}],
            "profiles": [],
            "default_profile_id": None,
            "errors": [],
        }
        routes = WebhooksAndDeferredRoutes(bazarr_service=bazarr)
        handler = _PostHandler()
        routes.handle_bazarr_subtitle_config(handler)
        assert handler.captured.status == 200
        body = json.loads(handler.captured.body)
        assert body["available_languages"][0]["code"] == "en"

    def test_returns_500_on_proxy_failure(self) -> None:
        import urllib.error
        bazarr = MagicMock()
        bazarr.fetch.side_effect = urllib.error.URLError("dead")
        routes = WebhooksAndDeferredRoutes(bazarr_service=bazarr)
        handler = _PostHandler()
        routes.handle_bazarr_subtitle_config(handler)
        assert handler.captured.status == 500
        body = json.loads(handler.captured.body)
        assert "error" in body


# ---------------------------------------------------------------------------
# /api/logs/stream GET (SSE)
# ---------------------------------------------------------------------------


class TestLogsStreamRoute:

    def test_dispatches_to_aggregate_log_streamer(self) -> None:
        streamer = MagicMock()
        routes = WebhooksAndDeferredRoutes(log_streamer=streamer)
        handler = _PostHandler()
        routes.handle_logs_stream(handler)
        streamer.stream.assert_called_once_with(handler)

    def test_default_streamer_delegates_to_legacy_helper(self) -> None:
        captured = []

        def fake_helper(handler):
            captured.append(handler)

        streamer = AggregateLogStreamer(legacy_streamer=fake_helper)
        sentinel = object()
        streamer.stream(sentinel)
        assert captured == [sentinel]


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec parity for every wave-6 path
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity behaviour for the wave-6
    domain. If a future change accidentally drops a handler from
    the registry, this test fires before any per-route test does.
    """

    _EXPECTED_PATHS: frozenset[str] = frozenset({
        "/api/webhooks",
        "/webhooks",
        "/webhooks/test",
        "/api/webhooks/test",
        "/webhooks/arr",
        "/api/bazarr/subtitle-config",
        "/api/logs/stream",
    })

    def test_all_wave6_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.path in self._EXPECTED_PATHS
        }
        assert registered == self._EXPECTED_PATHS, (
            f"Missing wave-6 routes: "
            f"{self._EXPECTED_PATHS - registered}"
        )

    def test_get_api_webhooks_handled_by_router(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("GET", "/api/webhooks")
        assert outcome == DispatchOutcome.HANDLED

    def test_get_api_logs_stream_handled_by_router(self) -> None:
        """The legacy ``_handle_logs_sse`` helper writes to the real
        socket via ``send_response`` / ``send_header`` — the mock
        handler doesn't emulate those. We patch the legacy helper
        on the route module BEFORE the harness instantiates the
        ``AggregateLogStreamer`` (whose ``__init__`` binds the
        import-time symbol as its default arg) so the route
        delegates to the mock instead of the real streamer."""
        def _emit(handler):
            handler._json_response(200, {"sse": "fired"})
        with patch(
            "media_stack.api.routes.webhooks_and_deferred."
            "_handle_logs_sse",
            side_effect=_emit,
        ):
            harness = RouteDispatchHarness.with_default_router()
            outcome, response = harness.try_dispatch(
                "GET", "/api/logs/stream",
            )
        assert outcome == DispatchOutcome.HANDLED
        assert response.status == 200

    def test_get_api_bazarr_subtitle_config_handled_by_router(
        self,
    ) -> None:
        """The default Router auto-discovers a module that pulls in
        ``bazarr_proxy`` lazily, so we install a ``sys.modules`` stub
        for the test rather than ``patch`` (which fails when the
        target module's own imports are broken in this environment).
        """
        import sys
        import types

        stub_module = types.ModuleType(
            "media_stack.api.services.bazarr_proxy",
        )
        stub_module.get_subtitle_config = lambda: {
            "available_languages": [], "profiles": [],
        }
        prior = sys.modules.get("media_stack.api.services.bazarr_proxy")
        sys.modules["media_stack.api.services.bazarr_proxy"] = stub_module
        try:
            harness = RouteDispatchHarness.with_default_router()
            outcome, response = harness.try_dispatch(
                "GET", "/api/bazarr/subtitle-config",
            )
        finally:
            if prior is not None:
                sys.modules["media_stack.api.services.bazarr_proxy"] = prior
            else:
                del sys.modules["media_stack.api.services.bazarr_proxy"]
        assert outcome == DispatchOutcome.HANDLED
        assert response.status == 200
